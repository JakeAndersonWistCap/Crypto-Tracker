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

DailyChecks: once a day. A matured market's history no longer moves, so its CSV is cached (.cache/boros/<id>.csv).
Complete UTC days only. Budget: 200 compute units a window, an export costs 20 — one call every 6.5 s (the first run
reads every market alive in the window, ~200 calls ≈ 22 min; later runs only the live ones).
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import logging
from collections import defaultdict
from pathlib import Path

from .base import Http, tidy, today

log = logging.getLogger("token_metrics.fetch.boros")

SOURCE = "boros_api"
TIER = 1
BASE = "https://api-boros.pendle.finance/apis"
CACHE = Path(__file__).resolve().parent.parent / ".cache" / "boros"


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
    out, unpriced = defaultdict(float), 0.0
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
            out[d] += v
            continue
        p = r.get("asset_price_usd")
        if p in (None, ""):
            unpriced += v
            continue
        out[d] += v * float(p)
    return dict(out), unpriced


class BorosVolume:
    """Pendle's Boros volume (config `boros_volume`), once a day."""

    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, daily_checks=None, cache_dir: Path | None = None, **_ignored):
        # THE API'S OWN BUDGET: 200 compute units a window (x-ratelimit-limit), the export costs 20 (x-computing-unit,
        # OpenAPI) — 10 exports a minute, so one every 6.5 s; a 429 waits the minute out
        self.http = http or Http(min_interval=6.5, retries=3, rate_limit_wait=61.0)
        if daily_checks is None:
            from .logcache import DailyChecks
            daily_checks = DailyChecks()
        self.daily = daily_checks
        self.cache = cache_dir or CACHE

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
            out.mark_current(SOURCE, name, m, f"{m}: Boros was read today ({day})", TIER)
            return
        last = today().date() - _dt.timedelta(days=1)
        first = last - _dt.timedelta(days=int(spec.get("keep_days", 460)) - 1)
        now_ts = int(_dt.datetime.now(_dt.timezone.utc).timestamp())
        total: dict = defaultdict(float)
        unpriced: dict = defaultdict(float)
        n = 0
        try:
            assets = self.assets()
            for mk in self.markets():
                maturity = int((mk.get("imData") or {}).get("maturity") or 0)
                if maturity and _dt.datetime.fromtimestamp(maturity, _dt.timezone.utc).date() < first:
                    continue                                   # ended before the window: nothing to add
                pricing = collateral_pricing(mk, assets)
                label = f"{(mk.get('imData') or {}).get('symbol') or mk.get('marketId')}"
                if pricing is None:
                    unpriced[label + " (collateral is neither the underlying nor a USD stablecoin)"] += 1
                    continue
                rows = self.export(mk, matured=bool(maturity) and maturity < now_ts - 2 * 86_400)
                per, yu = daily_usd(rows, first, last, pricing)
                for d, v in per.items():
                    total[d] += v
                if yu:
                    unpriced[label + " units with no price on the row"] += yu
                n += 1
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run; a partial sum is not stored
            out.fail(SOURCE, name, f"{m}: {e} — NOTHING STORED (a market missing would understate the day)", TIER)
            return
        self.daily.done(f"boros_volume:{name}", day)
        pts, d = [], first
        while d <= last:
            pts.append((_dt.datetime(d.year, d.month, d.day), total.get(d, 0.0)))
            d += _dt.timedelta(days=1)
        note = (f"{n} market(s) read; $ = traded units (the market's COLLATERAL token) x Boros's own daily "
                f"asset_price_usd where the collateral is the underlying, x $1 where it is a USD stablecoin"
                + ("; LEFT OUT: " + ", ".join(f"{k} {v:,.2f}" for k, v in sorted(unpriced.items())) if unpriced else ""))
        out.add(tidy(pts, name, m, SOURCE, TIER), SOURCE, name, f"{m}: {len(pts)} day(s) {first}..{last} — {note}", TIER)
