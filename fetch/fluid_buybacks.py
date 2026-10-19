"""
fetch/fluid_buybacks.py — tier 1: FLUID BUYBACKS FROM FLUID'S OWN API (Round G6 item 5, Jake 2026-10-19).

The API behind fluid.io/fluid lists every buyback: api.fluid.io/v2/fluid-token/buybacks/charts?start=&end= (one record
per execution: createdAt, amountUsd, amount in FLUID, transactionHash) and /buybacks/stats (the totals). Read 2026-10-19:
125 records 2025-10-03T18:54:11Z .. 2026-03-20T10:43:23Z, $4,745,304.89 / 1,306,296.83 FLUID — 1.31% of the 100M
supply, the "~1.3% of supply" of Fluid's own post-mortem (config UNAVAILABLE, governance_figure), not Tokenomist's 0.51%.
robots: api.fluid.io serves none (404); fluid.io allows everything.

Stored as actual_buyback_usd / actual_buyback_tokens per UTC day, zero-filled between executions, from the first record
to the HALT (config.DECLARED_ZERO's `since` − 1 day): from then the declared zero stands, so the two never meet. The
records must add up to the stats endpoint's totals (to 0.01) or nothing is stored. Once a day.
"""
from __future__ import annotations

import logging

import pandas as pd

import config
from .base import Http, tidy, today

log = logging.getLogger("token_metrics.fetch.fluid_buybacks")

SOURCE = "fluid_buybacks"
TIER = 1
API = "https://api.fluid.io/v2/fluid-token/buybacks"
FIRST = "2025-10-01"


def _day(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return (t.tz_convert("UTC").tz_localize(None) if t.tzinfo else t).normalize()


def daily(records: list) -> pd.DataFrame:
    """[{createdAt, amountUsd, amount}] -> daily (usd, tokens), UTC days."""
    df = pd.DataFrame([{"date": _day(r["createdAt"]), "usd": float(r["amountUsd"]), "tokens": float(r["amount"])}
                       for r in records])
    return df.groupby("date")[["usd", "tokens"]].sum()


class FluidBuybacks:
    def __init__(self, http: Http | None = None, daily_checks=None):
        self.http = http or Http(min_interval=1.0, timeout=30)
        if daily_checks is None:
            from .logcache import DailyChecks
            daily_checks = DailyChecks()
        self.daily = daily_checks

    def run(self, projects: list[dict], window_days, out):
        if not any(p["name"] == "Fluid" for p in projects):
            return
        day, key = str(today().date()), "fluid_buybacks:Fluid"
        if not self.daily.due(key, day):
            for m in ("actual_buyback_usd", "actual_buyback_tokens"):
                out.mark_current(SOURCE, "Fluid", m, f"{m}: Fluid's buyback list read today already", TIER)
            return
        end = pd.Timestamp(config.DECLARED_ZERO[("Fluid", "actual_buyback_usd")]["since"]) - pd.Timedelta(days=1)
        try:
            recs = self.http.get(f"{API}/charts", params={"start": f"{FIRST}T00:00:00.000Z",
                                                         "end": f"{today().date()}T00:00:00.000Z"})
            stats = self.http.get(f"{API}/stats")
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, "Fluid", f"actual_buyback_usd: Fluid's buyback API did not answer — {e}", TIER)
            return
        recs = recs if isinstance(recs, list) else (recs or {}).get("data") or []
        if not recs:
            out.fail(SOURCE, "Fluid", "actual_buyback_usd: the buyback list came back empty — NOTHING STORED", TIER)
            return
        tot_usd = sum(float(r["amountUsd"]) for r in recs)
        tot_tok = sum(float(r["amount"]) for r in recs)
        s_usd, s_tok = _stats_totals(stats)
        if s_usd is None or abs(tot_usd - s_usd) > 0.01 or abs(tot_tok - s_tok) > 0.01:
            out.fail(SOURCE, "Fluid", f"actual_buyback_usd: the records sum to ${tot_usd:,.2f} / {tot_tok:,.2f} FLUID "
                                      f"but the stats endpoint says {s_usd} / {s_tok} — NOTHING STORED", TIER)
            return
        d = daily(recs)
        last = d.index.max()
        if last > end:
            out.fail(SOURCE, "Fluid", f"actual_buyback_usd: a buyback dated {last.date()}, after the declared halt "
                                      f"({end.date()}) — the programme restarted? NOTHING STORED; review "
                                      f"config.DECLARED_ZERO", TIER)
            return
        days = pd.date_range(d.index.min(), end)
        d = d.reindex(days, fill_value=0.0)
        src = f"{SOURCE}:api.fluid.io/v2/fluid-token/buybacks/charts"
        for col, metric in (("usd", "actual_buyback_usd"), ("tokens", "actual_buyback_tokens")):
            out.add(tidy(list(zip(d.index, d[col])), "Fluid", metric, src, TIER), SOURCE, "Fluid",
                    f"{metric}: {len(recs)} buyback(s) {d.index.min().date()}..{last.date()} = "
                    f"{d[col].sum():,.2f} ({'USD' if col == 'usd' else 'FLUID'}), zero-filled to the halt "
                    f"({end.date()}); the records match the stats totals", TIER)
        self.daily.done(key, day)


def _stats_totals(stats) -> tuple[float | None, float | None]:
    """(total USD, total FLUID) from /buybacks/stats, whatever its nesting: the entry titled like 'Total FLUID Buyback'."""
    def walk(x):
        if isinstance(x, dict):
            title = str(x.get("title") or x.get("name") or x.get("label") or "")
            if "total" in title.lower() and "buyback" in title.lower() and "value" in x:
                yield x
            for v in x.values():
                yield from walk(v)
        elif isinstance(x, list):
            for v in x:
                yield from walk(v)
    for e in walk(stats):
        alt = e.get("alternativeValue")
        try:
            return float(e["value"]), float(alt.get("value") if isinstance(alt, dict) else alt)
        except (TypeError, ValueError, AttributeError):
            continue
    return None, None
