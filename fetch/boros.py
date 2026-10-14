"""
fetch/boros.py — tier 1: Pendle Boros traded volume in $, per day (round 3 B6 / W2, alignment round C16; Jake
2026-10-13). Boros's own open API, no key (https://api-boros.pendle.finance/apis, OpenAPI at /apis/docs-json, read
2026-10-13; robots.txt: 404, nothing disallowed):

    GET /apis/v1/markets?limit=200[&resumeToken=…]                     every whitelisted market, matured ones included
    GET /apis/v1/indicators/export?marketId=N&timeFrame=1d&select=ap   CSV, one row per UTC day, up to 10,000 rows:
        timestamp, open, high, low, close, volume, asset_price_usd

WHAT IS SUMMED. `volume` is the day's traded size in YIELD UNITS (Σ|trade.size|; the export's header text says USD but
its figures equal /v1/markets/ohlcv's `v`, documented as YU — checked on market 2, 2025-08-13: 129.449 both). A YU is
one unit of the market's COLLATERAL (tokenId -> /v1/assets: 1 WBTC, 2 WETH, 3 USD₮0, 4 BNB, 5 HYPE) — not of its
underlying: HYPERLIQUID-BTC-31JUL2026 (USD₮0 collateral) traded 117,325,521 units in Q0 against a volume24h of ~7.2M,
which is dollars, not BTC (read 2026-10-13). So the day's $ volume is volume x the collateral's price: Boros's own
asset_price_usd on the same row where the collateral IS the underlying (WBTC/BTC, WETH/ETH, HYPE, BNB), $1 where the
collateral is a USD stablecoin (USD₮0 — its par; Boros quoted 0.9987 on 2026-10-13). A market whose collateral is
neither is NOT priced from anywhere else: it is left out and named in the log.

RESUMABLE ACROSS RUNS (round 3 run X2, Jake 2026-10-14: the tier timed out at 120s with 0 rows). Budget: 200 compute
units a window, an export costs 20 — one call every 6.5 s, and the first read is every market alive in the window (232
on 2026-10-13, ≈ 25 min). So each market's daily $ is kept in .cache/boros/state.json with the day it is read THROUGH;
a run reads the stalest markets first and stops itself at MAX_SECONDS, saving after every market. Nothing is stored
until EVERY market is read through a day, and then only the days up to the stalest market's — a partial sum would
understate the day. A matured market's history no longer moves: its CSV is cached (.cache/boros/<id>.csv) and read
once. About 60 exports a run: the first read takes 4 runs; after it, a day's top-up is the ~40 live markets (~4.5 min).
Once complete for yesterday, DailyChecks skips the source for the rest of the day. Complete UTC days only.
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import logging
import time
from pathlib import Path

from .base import Http, Progress, tidy, today
from .logcache import atomic_write_text

log = logging.getLogger("token_metrics.fetch.boros")

SOURCE = "boros_api"
TIER = 1
BASE = "https://api-boros.pendle.finance/apis"
CACHE = Path(__file__).resolve().parent.parent / ".cache" / "boros"
# the adapter stops itself here and resumes next run; the tier budget (fetch.TIER_BUDGET_S["boros"]) is above it
MAX_SECONDS = 420.0


USD_STABLES = {"USD₮0", "USDT", "USDC", "USDT0"}
WRAPPED = {"WBTC": "BTC", "WETH": "ETH"}


def collateral_pricing(market: dict, assets: dict) -> str | None:
    """How a market's volume is priced: "row" (the collateral IS the underlying — Boros's own daily asset_price_usd),
    "par" (a USD stablecoin collateral: $1 a unit), or None (neither — left out, named)."""
    coll = (assets.get(int(market.get("tokenId") or 0)) or {}).get("symbol") or ""
    under = (market.get("metadata") or {}).get("underlyingSymbol") or ""
    if WRAPPED.get(coll, coll) == under:
        return "row"
    if coll in USD_STABLES:
        return "par"
    return None


def daily_usd(rows: list[dict], first: _dt.date, last: _dt.date, pricing: str = "row") -> tuple[dict, float]:
    """({date: $ volume}, units traded on days with no price) for one market's export rows, days in [first, last].
    pricing "row": x the row's asset_price_usd; "par": x 1 (a USD stablecoin collateral)."""
    out: dict = {}
    unpriced = 0.0
    for r in rows:
        try:
            ts = int(float(r["timestamp"]))
        except (KeyError, TypeError, ValueError):
            continue
        d = _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).date()
        if d < first or d > last:
            continue
        v = float(r.get("volume") or 0.0)
        if v <= 0:
            continue
        if pricing == "par":
            out[d] = out.get(d, 0.0) + v
            continue
        p = r.get("asset_price_usd")
        if p in (None, ""):
            unpriced += v
            continue
        out[d] = out.get(d, 0.0) + v * float(p)
    return out, unpriced


class BorosVolume:
    """Pendle's Boros volume (config `boros_volume`), once a day."""

    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, daily_checks=None, cache_dir: Path | None = None,
                 max_seconds: float | None = MAX_SECONDS, clock=time.monotonic, **_ignored):
        # THE API'S OWN BUDGET: 200 compute units a window (x-ratelimit-limit), the export costs 20 (x-computing-unit,
        # OpenAPI) — 10 exports a minute, so one every 6.5 s; a 429 waits the minute out
        self.http = http or Http(min_interval=6.5, retries=3, rate_limit_wait=61.0)
        if daily_checks is None:
            from .logcache import DailyChecks
            daily_checks = DailyChecks()
        self.daily = daily_checks
        self.cache = cache_dir or CACHE
        self.max_seconds, self.clock = max_seconds, clock

    def _state_path(self) -> Path:
        return self.cache / "state.json"

    def _load(self) -> dict:
        try:
            st = json.loads(self._state_path().read_text(encoding="utf-8"))
            return st if st.get("version") == 1 else {"version": 1, "markets": {}}
        except (OSError, ValueError):
            return {"version": 1, "markets": {}}

    def _save(self, state: dict) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self._state_path(), json.dumps(state))

    def markets(self) -> list[dict]:
        rows, token = [], None
        for _ in range(50):
            got = self.http.get(f"{BASE}/v1/markets", params={"limit": 200, **({"resumeToken": token} if token else {})})
            rows += got.get("results") or []
            token = got.get("resumeToken")
            if not token:
                return rows
        raise ValueError("more than 50 pages of markets — not read in full")

    def assets(self) -> dict:
        """{tokenId: asset} — every whitelisted asset, collateral included (one call)."""
        got = self.http.get(f"{BASE}/v1/assets")
        rows = (got.get("results") or got.get("assets")) if isinstance(got, dict) else got
        return {int(a["tokenId"]): a for a in rows or () if a.get("tokenId") is not None}

    def export(self, market: dict, matured: bool) -> list[dict]:
        mid = int(market["marketId"])
        path = self.cache / f"{mid}.csv"
        if matured and path.exists():
            text = path.read_text(encoding="utf-8")
        else:
            text = self.http.get_text(f"{BASE}/v1/indicators/export",
                                      params={"marketId": mid, "timeFrame": "1d", "select": "ap"})
            if matured:
                self.cache.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
        return list(csv.DictReader(io.StringIO(text)))

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = p.get("boros_volume")
            if spec:
                self._project(p["name"], spec, out)

    def _project(self, name: str, spec: dict, out) -> None:
        m, day = spec["metric"], str(today().date())
        if not self.daily.due(f"boros_volume:{name}", day):
            out.mark_current(SOURCE, name, m, f"{m}: Boros was read through yesterday today ({day})", TIER)
            return
        t0 = self.clock()
        last = today().date() - _dt.timedelta(days=1)
        first = last - _dt.timedelta(days=int(spec.get("keep_days", 460)) - 1)
        now_ts = int(_dt.datetime.now(_dt.timezone.utc).timestamp())
        try:
            assets = self.assets()
            universe = self.markets()
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run; nothing partial is stored
            out.fail(SOURCE, name, f"{m}: the market list could not be read — {e}; resumes next run", TIER)
            return
        state = self._load()
        mk = state.setdefault("markets", {})
        todo, left_out = [], {}
        for mkt in universe:
            maturity = int((mkt.get("imData") or {}).get("maturity") or 0)
            if maturity and _dt.datetime.fromtimestamp(maturity, _dt.timezone.utc).date() < first:
                continue                                   # ended before the window: nothing to add
            label = f"{(mkt.get('imData') or {}).get('symbol') or mkt.get('marketId')}"
            pricing = collateral_pricing(mkt, assets)
            if pricing is None:
                left_out[label + " (collateral is neither the underlying nor a USD stablecoin)"] = 1.0
                continue
            todo.append((mkt, maturity, label, pricing))
        # THE STALEST FIRST, so a cut run never starves the same markets twice
        todo.sort(key=lambda t: (mk.get(str(t[0]["marketId"])) or {}).get("through") or "")
        read = cut = failed = 0
        prog = Progress(f"boros {name}", total=len(todo), unit="markets", every_units=20,
                        checkpoint=lambda: self._save(state),
                        status=lambda: f"{read} read this run, {cut} left for the next (budget)")
        for mkt, maturity, label, pricing in todo:
            prog.tick()
            mid = str(mkt["marketId"])
            st = mk.get(mid) or {}
            if st.get("through") and st["through"] >= str(last):
                continue
            if self.max_seconds is not None and self.clock() - t0 > self.max_seconds:
                cut += 1
                continue
            try:
                rows = self.export(mkt, matured=bool(maturity) and maturity < now_ts - 2 * 86_400)
            except Exception as e:  # noqa: BLE001 — this market resumes next run
                failed += 1
                log.warning("boros %s: market %s (%s) not read — %s; resumes next run", name, mid, label, e)
                continue
            per, yu = daily_usd(rows, first, last, pricing)
            mk[mid] = {"label": label, "through": str(last), "unpriced_units": yu,
                       "days": {str(d): v for d, v in sorted(per.items())}}
            read += 1
        prog.flush(final=not cut and not failed)
        keep = str(first)
        for v in mk.values():
            v["days"] = {d: x for d, x in v.get("days", {}).items() if d >= keep}
        self._save(state)
        ids = [str(t[0]["marketId"]) for t in todo]
        throughs = [(mk.get(i) or {}).get("through") for i in ids]
        if not ids:
            out.fail(SOURCE, name, f"{m}: no Boros market in the window — NOTHING STORED", TIER)
            return
        if any(t is None for t in throughs):
            done = sum(t is not None for t in throughs)
            out.skipped(SOURCE, name, f"{m}: FIRST READ IN PROGRESS — {done} of {len(ids)} market(s) read ({read} "
                                      f"this run, {cut} left for the next run by the {self.max_seconds:.0f}s budget, "
                                      f"{failed} failed); nothing stored until every market is read through a day",
                        TIER)
            return
        complete = min(throughs)
        total: dict = {}
        unpriced = dict(left_out)
        for i in ids:
            for d, v in mk[i]["days"].items():
                if d <= complete:
                    total[d] = total.get(d, 0.0) + v
            if mk[i].get("unpriced_units"):
                key = mk[i].get("label", i) + " units with no price on the row"
                unpriced[key] = unpriced.get(key, 0.0) + mk[i]["unpriced_units"]
        if complete >= str(last):
            self.daily.done(f"boros_volume:{name}", day)
        pts, d = [], first
        while str(d) <= complete:
            pts.append((_dt.datetime(d.year, d.month, d.day), total.get(str(d), 0.0)))
            d += _dt.timedelta(days=1)
        note = (f"{len(ids)} market(s), every one read through {complete} ({read} this run, {cut} left for the next, "
                f"{failed} failed); $ = traded units (the market's COLLATERAL token) x Boros's own daily "
                f"asset_price_usd where the collateral is the underlying, x $1 where it is a USD stablecoin"
                + ("; LEFT OUT: " + ", ".join(f"{k} {v:,.2f}" for k, v in sorted(unpriced.items())) if unpriced else ""))
        out.add(tidy(pts, name, m, SOURCE, TIER), SOURCE, name, f"{m}: {len(pts)} day(s) {first}..{complete} — {note}",
                TIER)
