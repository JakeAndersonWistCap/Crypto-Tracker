"""
fetch/price_history.py — tier 1: DAILY PRICES BACK TO THE FIRST TOKEN FLOW (Round G 2.1, Jake 2026-10-18).

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


def token_flow_metrics() -> set:
    return {m for m, d in config.METRICS.items() if d.get("kind") == "flow" and d.get("unit") == "tokens"}


def missing_price_days(stored: pd.DataFrame, name: str, flows: set | None = None) -> tuple[list, str | None]:
    """(days with no price_usd from the first token flow to yesterday, the first flow's date) for one project."""
    if stored is None or stored.empty:
        return [], None
    s = stored[stored["project"] == name]
    flows = token_flow_metrics() if flows is None else flows
    fl = s[s["metric"].isin(flows)]
    fl = fl[pd.to_numeric(fl["value"], errors="coerce").fillna(0) != 0]
    if fl.empty:
        return [], None
    first = pd.to_datetime(fl["date"]).min().normalize()
    have = set(pd.to_datetime(s.loc[s["metric"] == "price_usd", "date"]).dt.normalize())
    days = [d for d in pd.date_range(first, today() - pd.Timedelta(days=1)) if d not in have]
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

    def run(self, projects: list[dict], window_days, out):
        day = str(today().date())
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
                                 "price history: every day from the first token flow"
                                 + (f" ({first})" if first else "") + " has a price_usd", TIER)
                self.daily.done(key, day)
                continue
            coin = f"coingecko:{cid}"
            ok, why = self.robots(CHART.format(key=coin))
            if not ok:
                out.fail(SOURCE, name, f"price_usd history: robots.txt refuses coins.llama.fi ({why})", TIER)
                continue
            got, errors = {}, []
            for run in runs(days):
                start = int(run[0].tz_localize("UTC").timestamp())
                try:
                    body = self.http.get(CHART.format(key=coin), params={"start": start, "span": len(run),
                                                                          "period": "1d", "searchWidth": 600})
                except Exception as e:  # noqa: BLE001 — one span failing leaves the others
                    from .chain import redact_urls
                    errors.append(f"{run[0].date()}..{run[-1].date()}: {redact_urls(e)}"[:200])
                    continue
                for pt in ((body or {}).get("coins") or {}).get(coin, {}).get("prices") or []:
                    ts = int(pt.get("timestamp") or 0)
                    d = pd.Timestamp(ts, unit="s").normalize()
                    if abs(ts - int(d.tz_localize("UTC").timestamp())) <= STAMP_TOLERANCE_S and pt.get("price"):
                        got[d] = float(pt["price"])
            want = set(days)
            rows = sorted((d, v) for d, v in got.items() if d in want)
            if rows:
                out.add(tidy(rows, name, "price_usd", f"{SOURCE}:coins.llama.fi[{coin}]", TIER), SOURCE, name,
                        f"price_usd: {len(rows)} day(s) {rows[0][0].date()}..{rows[-1][0].date()} filled from "
                        f"DefiLlama's {coin} (CoinGecko's price relayed, 00:00 UTC) — the days from the first token "
                        f"flow ({first}) with no stored price; {len(days) - len(rows)} day(s) not served", TIER)
            if errors:
                out.fail(SOURCE, name, f"price_usd history: {len(errors)} span(s) not read — {'; '.join(errors[:3])}",
                         TIER)
            elif not rows:
                out.skipped(SOURCE, name, f"price_usd history: DefiLlama serves no {coin} price for the "
                                          f"{len(days)} missing day(s) from {days[0].date()}", TIER)
            if not errors:
                self.daily.done(key, day)
