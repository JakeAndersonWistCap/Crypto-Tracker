"""
fetch/price_history.py — tier 1: DAILY PRICES BACK TO THE FIRST SERIES THAT NEEDS ONE (Round G 2.1, Jake 2026-10-18; G7
item 2b, 2026-10-19: from the first token or USD flow or token stock, not only the first token flow).

Ether.fi's actual_buyback_usd left 433 of 834 days unvalued from 2024-07-06: the store held no ETHFI price_usd before
2025-09-12 (CoinGecko's free market_chart serves 365 days, fetch.coingecko.FREE_MAX_DAYS). A past flow is never valued
at today's price, so those days stayed blank. This adapter fills, for every project with a coingecko_id, each day from
the project's FIRST TOKEN FLOW (any stored series of kind "flow", unit "tokens") to yesterday that holds no price_usd —
only those days, never over a stored price.

THE SOURCE: DefiLlama's coins API, keyed coingecko:<id> — CoinGecko's own price relayed, on the same 00:00 UTC stamp as
the CoinGecko rows beside it (one price basis for the whole series), served for the whole history:
    GET https://coins.llama.fi/chart/coingecko:<id>?start=<unix>&span=<days>&period=1d&searchWidth=600
    -> {"coins": {"coingecko:<id>": {"prices": [{"timestamp", "price"}], "symbol", "confidence"}}}
A point more than 10 minutes from 00:00 UTC is dropped (it would be another instant's price). coins.llama.fi answers
robots.txt with 404 (no rules; RFC 9309 s2.3.1.3), checked each run through the pipeline's reader; at most 499 points
a call (the API refuses 500+), ~1 call a second, once a day per project (DailyChecks).
"""
from __future__ import annotations

import logging

import pandas as pd

import config

from .base import Http, tidy, today

log = logging.getLogger("token_metrics.fetch.price_history")

SOURCE = "price_history"
TIER = 1
CHART = "https://coins.llama.fi/chart/{key}"
MAX_POINTS = 499
STAMP_TOLERANCE_S = 600
EMPTY_RECHECK_DAYS = 30     # a span DefiLlama served no price for is not asked again for this long (G5 item 3)
STOP_BEFORE_S = 15.0        # the adapter stops this long before its tier's budget and keeps what it read (G5 item 3)


def _empty_path():
    import os
    from pathlib import Path
    return Path(os.environ.get("TOKEN_METRICS_LOGCACHE", ".cache/logscan")).parent / "price_history_empty.json"


def _load_empty() -> dict:
    import json
    try:
        return json.loads(_empty_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_empty(d: dict) -> None:
    import json
    from .logcache import atomic_write_text
    _empty_path().parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(_empty_path(), json.dumps(d, indent=1, sort_keys=True))


def token_flow_metrics() -> set:
    return {m for m, d in config.METRICS.items() if d.get("kind") == "flow" and d.get("unit") == "tokens"}


def price_needing_metrics() -> set:
    """EVERY series a price is applied to (Round G7 item 2b, Jake 2026-10-19: price_usd reached back only to the first
    token flow, so Ethereum's and NEAR's history_derive refused 3,689 and 1,794 days for "no price" and FDV history
    stopped short): flows in tokens (valued at the day's price) or in USD (divided by it — burns derived from USD, implied
    buybacks) and stocks in tokens (FDV, market cap)."""
    return {m for m, d in config.METRICS.items()
            if (d.get("kind") == "flow" and d.get("unit") in ("tokens", "usd"))
            or (d.get("kind") == "stock" and d.get("unit") == "tokens")}


# THE DECLARED OLDER LEG of price_usd (config series_handover: defillama_coins:history -> coingecko). G2 addendum A1: the
# tier wrote `price_history:coins.llama.fi[...]`, a third measuring point, and blanked every Q0 cell that reads price.
HISTORY_POINT = "defillama_coins:history"


def missing_price_days(stored: pd.DataFrame, name: str, flows: set | None = None) -> tuple[list, str | None]:
    """(days with no price_usd from the first series that needs a price — price_needing_metrics: a token or USD flow, a
    token stock — to the day BEFORE CoinGecko's first stored price, that first date) for one project. Never a day inside CoinGecko's range (G2 addendum A1: interleaved with it, the series
    had two measuring points in Q0); with no CoinGecko price stored yet, nothing (CoinGecko seeds its 365 days first)."""
    if stored is None or stored.empty:
        return [], None
    s = stored[stored["project"] == name]
    flows = price_needing_metrics() if flows is None else flows
    fl = s[s["metric"].isin(flows)]
    fl = fl[pd.to_numeric(fl["value"], errors="coerce").fillna(0) != 0]
    if fl.empty:
        return [], None
    first = pd.to_datetime(fl["date"]).min().normalize()
    px = s[s["metric"] == "price_usd"]
    cg = px[px["source"].astype(str).str.split(":").str[0] == "coingecko"] if "source" in px else px.iloc[0:0]
    if cg.empty:
        return [], str(first.date())
    have = set(pd.to_datetime(px["date"]).dt.normalize())
    until = pd.to_datetime(cg["date"]).min().normalize() - pd.Timedelta(days=1)
    days = [d for d in pd.date_range(first, until) if d not in have]
    return days, str(first.date())


def runs(days: list, cap: int = MAX_POINTS) -> list:
    """Contiguous runs of days, each at most `cap` long."""
    out, cur = [], []
    for d in sorted(days):
        if cur and ((d - cur[-1]).days != 1 or len(cur) >= cap):
            out.append(cur)
            cur = []
        cur.append(d)
    if cur:
        out.append(cur)
    return out


class PriceHistory:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, stored_long=None, http: Http | None = None, daily=None, robots=None, **_ignored):
        self.stored = stored_long
        self.http = http or Http(min_interval=1.0, retries=2)
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily
        if robots is None:
            from .scrape import robots_verdict
            robots = robots_verdict
        self.robots = robots

    def run(self, projects: list[dict], window_days, out, unbounded: bool = False):
        # ITS OWN CLOCK (Round G5 item 3, Jake's 06:30 run: the tier timed out at 120 s after 88 calls, 61 s of them rate
        # spacing, and kept 0 frames — every day's read re-asked the same spans DefiLlama has no price for). Spans that
        # came back empty are remembered for EMPTY_RECHECK_DAYS; the adapter stops STOP_BEFORE_S before the tier's
        # budget, stores what it read, and the next run resumes (the project is not marked done).
        import time as _time
        from . import _budget                                 # noqa: PLC0415
        deadline = None if unbounded else _time.monotonic() + _budget(SOURCE) - STOP_BEFORE_S
        empty = _load_empty()
        cutoff = str((today() - pd.Timedelta(days=EMPTY_RECHECK_DAYS)).date())
        day = str(today().date())
        try:
            self._run(projects, out, day, deadline, empty, cutoff)
        finally:
            _save_empty(empty)

    def _run(self, projects, out, day, deadline, empty, cutoff):
        import time as _time
        for p in projects:
            cid, name = p.get("coingecko_id"), p["name"]
            if not cid:
                continue
            key = f"{SOURCE}:{name}"
            if not self.daily.due(key, day):
                out.mark_current(SOURCE, name, "price_usd", "price history: checked today already", TIER)
                continue
            days, first = missing_price_days(self.stored, name)
            if not days:
                out.mark_current(SOURCE, name, "price_usd",
                                 "price history: every day from the first series that needs a price"
                                 + (f" ({first})" if first else "") + " to CoinGecko's first price has a price_usd",
                                 TIER)
                self.daily.done(key, day)
                continue
            coin = f"coingecko:{cid}"
            ok, why = self.robots(CHART.format(key=coin))
            if not ok:
                out.fail(SOURCE, name, f"price_usd history: robots.txt refuses coins.llama.fi ({why})", TIER)
                continue
            got, errors, stopped, skipped_empty = {}, [], False, 0
            known_empty = empty.setdefault(name, {})
            for run in runs(days):
                span = f"{run[0].date()}..{run[-1].date()}"
                if known_empty.get(span, "") >= cutoff:
                    skipped_empty += len(run)
                    continue
                if deadline is not None and _time.monotonic() >= deadline:
                    stopped = True
                    break
                start = int(run[0].tz_localize("UTC").timestamp())
                try:
                    body = self.http.get(CHART.format(key=coin), params={"start": start, "span": len(run),
                                                                          "period": "1d", "searchWidth": 600})
                except Exception as e:  # noqa: BLE001 — one span failing leaves the others
                    from .chain import redact_urls
                    errors.append(f"{run[0].date()}..{run[-1].date()}: {redact_urls(e)}"[:200])
                    continue
                n0 = len(got)
                for pt in ((body or {}).get("coins") or {}).get(coin, {}).get("prices") or []:
                    ts = int(pt.get("timestamp") or 0)
                    d = pd.Timestamp(ts, unit="s").normalize()
                    if abs(ts - int(d.tz_localize("UTC").timestamp())) <= STAMP_TOLERANCE_S and pt.get("price"):
                        got[d] = float(pt["price"])
                if len(got) == n0:
                    known_empty[span] = day           # answered, with nothing for these days: not re-asked for a while
            want = set(days)
            rows = sorted((d, v) for d, v in got.items() if d in want)
            if rows:
                out.add(tidy(rows, name, "price_usd", f"{HISTORY_POINT}[coins.llama.fi {coin}]", TIER), SOURCE, name,
                        f"price_usd: {len(rows)} day(s) {rows[0][0].date()}..{rows[-1][0].date()} filled from "
                        f"DefiLlama's {coin} (CoinGecko's price relayed, 00:00 UTC) — the days from the first token "
                        f"flow ({first}) with no stored price; {len(days) - len(rows)} day(s) not served", TIER)
            if errors:
                out.fail(SOURCE, name, f"price_usd history: {len(errors)} span(s) not read — {'; '.join(errors[:3])}",
                         TIER)
            elif not rows:
                out.skipped(SOURCE, name, f"price_usd history: DefiLlama serves no {coin} price for the "
                                          f"{len(days)} missing day(s) from {days[0].date()}", TIER)
            if skipped_empty:
                out.log.append(_entry(name, f"price_usd history: {skipped_empty} day(s) in span(s) DefiLlama served no "
                                            f"price for within {EMPTY_RECHECK_DAYS} days — not re-asked"))
            if stopped:
                out.log.append(_entry(name, f"price_usd history: stopped {STOP_BEFORE_S:.0f}s before the tier's budget; "
                                            f"what was read is stored and the next run resumes"))
                break
            if not errors:
                self.daily.done(key, day)


def _entry(name, msg):
    from .base import LogEntry
    return LogEntry(SOURCE, name, 0, "ok", msg, TIER)
