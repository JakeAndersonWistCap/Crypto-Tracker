"""
fetch/dune.py — tier 4: Dune, for HISTORICAL BACKFILL ONLY.

A contract read (tier 2) gives point-in-time state. It does not give the month-by-month
history the 3/6/9-month trajectory columns need — that is event-log aggregation, which is what
Dune is for.

So Dune is a backfill dependency, not an ongoing one. Once the store holds history for a
series, this adapter skips it and daily tier 2/3 snapshots keep it current. Set
TOKEN_METRICS_DUNE_ALWAYS=1 to re-pull regardless (useful after fixing a query).

No query is assumed to exist. A metric with no query_id in config is written to the Gap
Report naming the project, the metric and why it failed, rather than silently returning
nothing.
"""
from __future__ import annotations

import os

from .base import Http, tidy, window

SOURCE = "dune"
TIER = 4
API = "https://api.dune.com/api/v1"


def always_refetch() -> bool:
    return os.environ.get("TOKEN_METRICS_DUNE_ALWAYS", "").strip() in ("1", "true", "yes")


class Dune:
    def __init__(self, has_history: set[tuple[str, str]] | None = None):
        self.key = os.environ.get("DUNE_API_KEY", "").strip()
        self.http = Http(min_interval=0.5)
        self.has_history = has_history or set()

    def _results(self, query_id: int) -> list[dict]:
        rows, offset, limit = [], 0, 5000
        while True:
            j = self.http.get(f"{API}/query/{query_id}/results",
                              params={"limit": limit, "offset": offset},
                              headers={"X-Dune-API-Key": self.key})
            batch = (j.get("result") or {}).get("rows") or []
            rows.extend(batch)
            if len(batch) < limit:
                return rows
            offset += limit

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            name = p["name"]
            for metric, q in (p.get("dune_queries") or {}).items():
                qid = q.get("query_id")
                if not qid:
                    out.unconfigured(SOURCE, name, f"{metric}: no query_id in config", TIER)
                    continue
                if (name, metric) in self.has_history and not always_refetch():
                    out.unconfigured(SOURCE, name, f"{metric}: store already holds history — backfill skipped", TIER)
                    continue
                if not self.key:
                    out.fail(SOURCE, name, f"{metric}: DUNE_API_KEY not set in .env", TIER)
                    out.gap(name, metric, reason="Dune query configured but DUNE_API_KEY is not set",
                            tiers_attempted="4", suggestion="Add DUNE_API_KEY to .env")
                    continue
                try:
                    rows = self._results(int(qid))
                    dcol, vcol = q.get("date_col", "day"), q.get("value_col", "value")
                    pairs = [(r[dcol], r[vcol]) for r in rows if r.get(dcol) is not None and r.get(vcol) is not None]
                    df = tidy(pairs, name, metric, f"dune:{qid}", TIER)
                    out.add(window(df, window_days), SOURCE, name, f"{metric}: query {qid} ({len(pairs)} rows)", TIER)
                except Exception as e:  # noqa: BLE001
                    out.fail(SOURCE, name, f"{metric}: query {qid}: {e}", TIER)
                    out.gap(name, metric, reason=f"Dune query {qid} failed: {e}", tiers_attempted="4",
                            suggestion="Check the query id and its date_col/value_col in config.py")
