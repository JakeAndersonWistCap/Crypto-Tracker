"""
fetch/hl_candles.py — Hyperliquid's perps volume per UTC day, from its own info API. Jake, 2026-10-01.

DefiLlama's derivatives route is paid (HTTP 402), so Hyperliquid's own free info API is the source:

    POST https://api.hyperliquid.xyz/info
      {"type": "perpDexs"}                     -> [null, {name, ...}, ...]   index 0 = the main dex
      {"type": "meta", "dex": <name>}          -> {universe: [{name, isDelisted?, ...}]}
      {"type": "candleSnapshot", "req": {"coin", "interval": "1d", "startTime", "endTime"}}
                                               -> [{t, T, s, i, o, c, h, l, v, n}]  (v in BASE units)
      {"type": "metaAndAssetCtxs", "dex": ...} -> [meta, [{dayNtlVlm, ...}]]       (rolling 24h)

(hyperliquid-dex docs mirror dzmbs/hyperliquid-docs@91d05aa: info-endpoint/perpetuals.md:7-269,
info-endpoint.md:479-520; rate-limits-and-user-limits.md:5-14.)

EVERY PERP MARKET, HIP-3 INCLUDED: the main dex's universe and every builder-deployed (HIP-3) dex's
universe; HIP-3 names arrive prefixed ("xyz:TSLA") and are passed to candleSnapshot as they are.
Delisted markets are read too (their history is real volume); one that returns no candles adds nothing.

DAILY USD = v x close of each market's 1d candle, summed over markets — an APPROXIMATION of notional
(the day's volume valued at its closing price, not trade by trade), and labelled so.

A DAY IS STORED ONLY WHEN EVERY MARKET HAS BEEN READ THROUGH IT. Per-market progress lives in a
cache file, so a run cut short by its budget resumes where it stopped and never stores a partial
day's sum as if it were a quiet day. The first year is one call per market
(`token_metrics.py --seed hl_candles`); after that each run reads each market's new days.

PACED to the published limit: 1200 weight per minute per IP; info requests weigh 20, and
candleSnapshot adds weight per 60 items returned (taken as +1 per 60 — the docs give the step, not
the increment). The pacer keeps under `weight_per_min` (config, below the published limit).

CROSS-CHECK: after a run that completes yesterday, the sum of metaAndAssetCtxs dayNtlVlm (a rolling
24h at read time, so never equal) is logged beside yesterday's sum.
"""
from __future__ import annotations

import json
import logging
import math
import time
from collections import deque
from pathlib import Path

import pandas as pd

from .base import Http, Progress, tidy, today

log = logging.getLogger("token_metrics.fetch.hl_candles")

SOURCE = "hl_candles"
TIER = 1
DAY_MS = 86_400_000


class Pacer:
    """Keeps the request weight inside a rolling minute under `per_min`."""

    def __init__(self, per_min: int, clock=time.monotonic, sleep=time.sleep):
        self.per_min, self.clock, self.sleep = per_min, clock, sleep
        self.spent: deque = deque()

    def take(self, weight: int) -> None:
        while True:
            now = self.clock()
            while self.spent and now - self.spent[0][0] >= 60:
                self.spent.popleft()
            if sum(w for _, w in self.spent) + weight <= self.per_min:
                self.spent.append((now, weight))
                return
            self.sleep(max(60 - (now - self.spent[0][0]), 0.05))


class HLCandles:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, pacer: Pacer | None = None, cache_file: Path | None = None,
                 max_seconds: float | None = 540, clock=time.monotonic, **_ignored):
        self.http = http or Http(min_interval=0.0, retries=2)
        self._pacer = pacer
        self.cache_file = cache_file
        self.max_seconds, self.clock = max_seconds, clock

    # --- plumbing -------------------------------------------------------------------------
    def _post(self, spec: dict, body: dict, items_weight: int = 0):
        if self._pacer is None:
            self._pacer = Pacer(int(spec["weight_per_min"]))
        self._pacer.take(int(spec["weight"]) + items_weight)
        return self.http.post(spec["url"], json_body=body)

    def _cache_path(self) -> Path:
        if self.cache_file:
            return Path(self.cache_file)
        from .logcache import LogCache
        return LogCache().root / "hl-candles.json"

    def _load(self) -> dict:
        try:
            return json.loads(self._cache_path().read_text())
        except (OSError, ValueError):
            return {"markets": {}}

    def _save(self, state: dict) -> None:
        p = self._cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state))

    def markets(self, spec: dict) -> list[dict]:
        """[{coin, dex, delisted}] — the main dex and every HIP-3 dex."""
        dexes = self._post(spec, {"type": "perpDexs"})
        names = [""] + [d["name"] for d in (dexes or []) if isinstance(d, dict) and d.get("name")]
        out = []
        for dex in names:
            meta = self._post(spec, {"type": "meta", "dex": dex})
            for u in (meta or {}).get("universe") or []:
                if u.get("name"):
                    out.append({"coin": u["name"], "dex": dex or "main", "delisted": bool(u.get("isDelisted"))})
        return out

    # --- the read -------------------------------------------------------------------------
    def run(self, projects: list[dict], window_days, out, unbounded: bool = False):
        for p in projects:
            spec = p.get("hl_candles")
            if spec:
                self._project(p["name"], spec, out, unbounded)

    def _project(self, name: str, spec: dict, out, unbounded: bool) -> None:
        t0 = self.clock()
        state = self._load()
        mk = state.setdefault("markets", {})
        try:
            universe = self.markets(spec)
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            out.fail(SOURCE, name, f"perpDexs/meta: {e}", TIER)
            return
        n_hip3 = sum(1 for m in universe if m["dex"] != "main")
        yday = (today() - pd.Timedelta(days=1)).normalize()
        floor = (today() - pd.Timedelta(days=int(spec["days"]))).normalize()
        read = cut = 0
        prog = Progress(f"hl_candles {name}", total=len(universe), unit="markets", every_units=50,
                        checkpoint=lambda: self._save(state),
                        status=lambda: f"{read} read this run, {cut} left for the next (budget)")
        for m in universe:
            prog.tick()
            if not unbounded and self.max_seconds is not None and self.clock() - t0 > self.max_seconds:
                cut += 1
                continue
            st = mk.setdefault(m["coin"], {"through": None, "days": {}})
            through = pd.Timestamp(st["through"]) if st["through"] else floor - pd.Timedelta(days=1)
            if through >= yday:
                continue
            if m["delisted"] and st["through"]:
                st["through"] = str(yday.date())      # read once already: a delisted market adds nothing new
                continue
            start = max(through + pd.Timedelta(days=1), floor)
            s_ms, e_ms = int(start.timestamp() * 1000), int((yday + pd.Timedelta(days=1)).timestamp() * 1000) - 1
            expect = (yday - start).days + 1
            try:
                rows = self._post(spec, {"type": "candleSnapshot", "req": {"coin": m["coin"], "interval": "1d",
                                                                           "startTime": s_ms, "endTime": e_ms}},
                                  items_weight=math.ceil(expect / 60))
            except Exception as e:  # noqa: BLE001
                out.fail(SOURCE, name, f"candleSnapshot {m['coin']}: {e} — resumes next run", TIER)
                continue
            for c in rows or []:
                d = pd.Timestamp(int(c["t"]), unit="ms").normalize()
                if floor <= d <= yday:
                    st["days"][str(d.date())] = float(c["v"]) * float(c["c"])
            st["through"] = str(yday.date())
            read += 1
        prog.flush(final=not cut)
        # prune what no window can reach
        keep = str((floor - pd.Timedelta(days=30)).date())
        for st in mk.values():
            st["days"] = {d: v for d, v in st["days"].items() if d >= keep}
        self._save(state)
        coins = [m["coin"] for m in universe]
        throughs = [mk[c]["through"] for c in coins if c in mk]
        if len(throughs) < len(coins) or any(t is None for t in throughs):
            out.skipped(SOURCE, name, f"perps_volume_usd: {read} market(s) read this run, {cut} left for the next "
                                      f"(budget) — no day stored until every one of {len(coins)} markets is read "
                                      f"through it", TIER)
            return
        complete = pd.Timestamp(min(throughs))
        sums: dict = {}
        for c in coins:
            for d, v in mk[c]["days"].items():
                if pd.Timestamp(d) <= complete:
                    sums[d] = sums.get(d, 0.0) + v
        if not sums:
            out.fail(SOURCE, name, "perps_volume_usd: no candles in the window", TIER)
            return
        pts = sorted((pd.Timestamp(d), v) for d, v in sums.items())
        frame = tidy(pts, name, spec["metric"], f"{SOURCE}:candleSnapshot[1d, sum v x close ~ notional]", TIER)
        msg = (f"{spec['metric']} = Σ over {len(coins)} perp markets ({len(coins) - n_hip3} main, {n_hip3} HIP-3; "
               f"{sum(m['delisted'] for m in universe)} delisted) of daily candle v x close — an APPROXIMATION of "
               f"notional; {len(frame)} day(s) {pts[0][0].date()}..{pts[-1][0].date()}; {read} market(s) read "
               f"this run, {cut} cut")
        if complete >= yday:
            msg += self._cross_check(spec, sums.get(str(yday.date())))
        out.add(frame, SOURCE, name, msg, TIER)

    def _cross_check(self, spec: dict, yday_sum) -> str:
        """Σ metaAndAssetCtxs dayNtlVlm over every dex (rolling 24h at read time) beside yesterday."""
        try:
            dexes = self._post(spec, {"type": "perpDexs"})
            total = 0.0
            for dex in [""] + [d["name"] for d in (dexes or []) if isinstance(d, dict) and d.get("name")]:
                meta_ctx = self._post(spec, {"type": "metaAndAssetCtxs", "dex": dex})
                total += sum(float(c.get("dayNtlVlm") or 0) for c in (meta_ctx or [None, []])[1])
        except Exception as e:  # noqa: BLE001
            return f"; dayNtlVlm cross-check unavailable ({e})"
        if not yday_sum:
            return f"; dayNtlVlm (rolling 24h) {total:,.0f}"
        return (f"; cross-check: Σ dayNtlVlm (rolling 24h at read time) {total:,.0f} vs yesterday's "
                f"Σ v x close {yday_sum:,.0f} ({total / yday_sum - 1:+.1%})")
