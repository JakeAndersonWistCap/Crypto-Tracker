"""
fetch/geodnet_feed.py — tier 3: GEODNET'S OWN DATA FEED (Round G6 item 3, Jake 2026-10-19).

geodnet.com/geod loads its charts from dune-dash.rajukhan641.workers.dev/geodnet/{name} ("availableNames": burn-daily,
burn-weekly, burn-monthly, issuance-daily, issuance-weekly, issuance-monthly, miner-stats, miner-earnings). No
robots.txt (the path answers 400 "Invalid path"). Read 2026-10-19:
  burn-monthly      {"month", "tokens_burned" (Polygon), "usd_burned", "sol_tokens_burned", "sol_usd_burned"}
  issuance-monthly  {"month", "tokens_issued" (Polygon), "sol_tokens_issued"}
  miner-stats       {"total_miner", "total_city", "total_country", "total_usage_revenue" (unit not labelled)}

Stored, COMPLETE months only (the month in progress is never stored), each dated the month's first day like the Dune
rows it continues:
  gross_burn_tokens_dune_monthly   Polygon + Solana burn — the monthly CROSS-CHECK of the daily burn series (stale since
                                   2026-08-01 on Dune 8683175, a backfill-only tier); source says which feed and legs
  issuance_feed_polygon_tokens     GEOD issued as rewards on Polygon (reference for the mining-wallet release)
  issuance_feed_solana_tokens      GEOD issued on Solana — NOT in our supply-side (b) line, which reads the two Polygon
                                   mining wallets only (pool_release_tokens): the reference shows the size of that hole
and supply_units = total_miner (GEODNET's own count, today's row). Once a day.
"""
from __future__ import annotations

import logging

import pandas as pd

from .base import Http, point, tidy, today

log = logging.getLogger("token_metrics.fetch.geodnet_feed")

SOURCE = "geodnet_feed"
TIER = 3
BASE = "https://dune-dash.rajukhan641.workers.dev/geodnet"


def complete_months(rows: list, asof: pd.Timestamp) -> list:
    """The rows of months that ended before `asof`'s month, each with its month's first day."""
    cur = pd.Timestamp(asof).to_period("M")
    out = []
    for r in rows:
        try:
            m = pd.Period(str(r["month"]), freq="M")
        except (KeyError, ValueError):
            continue
        if m < cur:
            out.append((m.to_timestamp(how="start"), r))
    return sorted(out, key=lambda x: x[0])


class GeodnetFeed:
    def __init__(self, http: Http | None = None, daily=None):
        self.http = http or Http(min_interval=1.0, timeout=30)
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily

    def _rows(self, name: str) -> list:
        j = self.http.get(f"{BASE}/{name}")
        return ((j or {}).get("result") or {}).get("rows") or []

    def run(self, projects: list[dict], window_days, out):
        if not any(p["name"] == "GEODNET" for p in projects):
            return
        day, key = str(today().date()), "geodnet_feed:GEODNET"
        metrics = ("gross_burn_tokens_dune_monthly", "issuance_feed_polygon_tokens", "issuance_feed_solana_tokens",
                   "supply_units")
        if not self.daily.due(key, day):
            for m in metrics:
                out.mark_current(SOURCE, "GEODNET", m, f"{m}: GEODNET's feed read today already", TIER)
            return
        asof, failed = today(), False
        try:
            burn = complete_months(self._rows("burn-monthly"), asof)
            iss = complete_months(self._rows("issuance-monthly"), asof)
            stats = self._rows("miner-stats")
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, "GEODNET", f"gross_burn_tokens_dune_monthly: GEODNET's feed did not answer — {e}", TIER)
            return
        if burn:
            pts = [(d, float(r.get("tokens_burned") or 0) + float(r.get("sol_tokens_burned") or 0)) for d, r in burn]
            out.add(tidy(pts, "GEODNET", "gross_burn_tokens_dune_monthly",
                         f"{SOURCE}:burn-monthly[polygon+solana]", TIER), SOURCE, "GEODNET",
                    f"gross_burn_tokens_dune_monthly: {len(pts)} complete month(s) {pts[0][0]:%Y-%m}..{pts[-1][0]:%Y-%m} "
                    f"from GEODNET's feed (Polygon + Solana); last {pts[-1][1]:,.0f}", TIER)
        else:
            failed = True
            out.fail(SOURCE, "GEODNET", "gross_burn_tokens_dune_monthly: burn-monthly held no complete month", TIER)
        for col, metric in (("tokens_issued", "issuance_feed_polygon_tokens"),
                            ("sol_tokens_issued", "issuance_feed_solana_tokens")):
            pts = [(d, float(r[col])) for d, r in iss if r.get(col) is not None]
            if pts:
                out.add(tidy(pts, "GEODNET", metric, f"{SOURCE}:issuance-monthly.{col}", TIER), SOURCE, "GEODNET",
                        f"{metric}: {len(pts)} complete month(s), last {pts[-1][0]:%Y-%m} {pts[-1][1]:,.2f}", TIER)
            else:
                failed = True
                out.fail(SOURCE, "GEODNET", f"{metric}: issuance-monthly held no {col}", TIER)
        miners = (stats[0] if stats else {}).get("total_miner")
        if miners is not None:
            out.add(point("GEODNET", "supply_units", float(miners), f"{SOURCE}:miner-stats.total_miner", TIER),
                    SOURCE, "GEODNET", f"supply_units: {int(miners):,} base stations (GEODNET's own count)", TIER)
        else:
            failed = True
            out.fail(SOURCE, "GEODNET", "supply_units: miner-stats held no total_miner", TIER)
        if not failed:
            self.daily.done(key, day)
