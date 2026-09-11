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

import pandas as pd

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

    @staticmethod
    def _columns(rows: list[dict]) -> list[str]:
        return sorted({k for r in rows[:20] for k in r}) if rows else []

    @staticmethod
    def _values(row: dict, q: dict) -> float | None:
        """One column, or several summed.

        A query can split a figure across columns — GEODNET reports Polygon and Solana burns
        separately, and the monthly total is their sum. Summing here beats picking one and
        silently reporting a fraction of the burn.
        """
        cols = q.get("value_cols") or ([q["value_col"]] if q.get("value_col") else [])
        total, seen = 0.0, False
        for col in cols:
            raw = row.get(col)
            if raw is None:
                continue
            try:
                total += float(raw)
                seen = True
            except (TypeError, ValueError):
                continue
        return total if seen else None

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
                    columns = self._columns(rows)

                    # Columns not mapped? Report what the query actually returns rather than
                    # guessing which one holds the figure. One round trip turns an unknown into
                    # a definitive list, and a wrong guess here would be a plausible wrong number.
                    dcol = q.get("date_col")
                    has_value = q.get("value_col") or q.get("value_cols")
                    if not dcol or not has_value:
                        out.fail(SOURCE, name, f"{metric}: query {qid} columns not mapped in config", TIER)
                        out.gap(name, metric,
                                reason=f"Dune query {qid} ran and returned {len(rows)} rows, but date_col and/or "
                                       f"value_col are not set in config, so no figure was read. "
                                       f"Columns the query ACTUALLY returns: {', '.join(columns) or 'none'}",
                                tiers_attempted="4",
                                suggestion=f"Pick the date column and the value column(s) from that list and set "
                                           f"date_col and value_col (or value_cols for a figure split across "
                                           f"several columns) under {name} dune_queries.{metric} in config.py.")
                        continue

                    missing = [c for c in ([dcol] + (q.get("value_cols") or [q.get("value_col")])) if c not in columns]
                    if missing and columns:
                        out.fail(SOURCE, name, f"{metric}: query {qid} has no column(s) {missing}", TIER)
                        out.gap(name, metric,
                                reason=f"Dune query {qid} does not return {missing}. Columns available: "
                                       f"{', '.join(columns)}",
                                tiers_attempted="4",
                                suggestion=f"Correct date_col/value_col(s) under {name} dune_queries.{metric}.")
                        continue

                    pairs = []
                    for r in rows:
                        when, value = r.get(dcol), self._values(r, q)
                        if when is not None and value is not None:
                            pairs.append((when, value))
                    df = tidy(pairs, name, metric, f"dune:{qid}", TIER)

                    # A monthly series' CURRENT month is incomplete, and the live tier 2 read already
                    # covers the present. Keeping both would double count inside the trailing window,
                    # so a period-granularity backfill supplies only complete past periods.
                    dropped = 0
                    if q.get("drop_current_period") and not df.empty:
                        granularity = q.get("granularity", "monthly")
                        freq = {"monthly": "M", "weekly": "W", "daily": "D"}.get(granularity, "M")
                        current = pd.Timestamp.now("UTC").tz_localize(None).to_period(freq)
                        keep = df["date"].dt.to_period(freq) < current
                        dropped = int((~keep).sum())
                        df = df[keep]

                    note = (f"{metric}: query {qid} ({len(pairs)} rows, "
                            f"{q.get('granularity', 'unspecified')} granularity"
                            + (f", dropped {dropped} incomplete current-period row(s)" if dropped else "") + ")")
                    out.add(window(df, window_days), SOURCE, name, note, TIER)
                except Exception as e:  # noqa: BLE001
                    out.fail(SOURCE, name, f"{metric}: query {qid}: {e}", TIER)
                    out.gap(name, metric, reason=f"Dune query {qid} failed: {e}", tiers_attempted="4",
                            suggestion="Check the query id and its date_col/value_col in config.py")
