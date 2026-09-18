"""
fetch/dune.py — tier 4: Dune, for HISTORICAL BACKFILL ONLY.

A contract read (tier 2) gives point-in-time state. It does not give the month-by-month
history the 3/6/9-month trajectory columns need — that is event-log aggregation, which is what
Dune is for.

So Dune is a backfill dependency, not an ongoing one. Once the store holds history for a
series, this adapter skips it and daily tier 2/3 snapshots keep it current. Set
TOKEN_METRICS_DUNE_ALWAYS=1 to re-pull regardless — which is what you want after correcting a
column mapping, since the stored rows were written by the OLD mapping and only a re-pull
overwrites them. A forced re-pull ignores the trailing-window limit and takes the full history:
rebuilding only the last 30 days of a 794-row series would leave the rest at their old values.

No query is assumed to exist. A metric with no query_id in config is written to the Gap
Report naming the project, the metric and why it failed, rather than silently returning
nothing.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pandas as pd

from .base import Http, HttpError, tidy, window

log = logging.getLogger("token_metrics.fetch.dune")

SOURCE = "dune"
TIER = 4
API = "https://api.dune.com/api/v1"

# Column names that mean "this row carries its own date". Used ONLY to catch a query declared as
# a snapshot that is really a time series — never to pick a date column automatically.
DATEISH = ("day", "date", "month", "week", "time", "period", "block_time", "dt", "ts", "hour")

# Where the shape of each query is written on first contact. Column names alone are not the shape:
# without types and a sample row you cannot tell which column holds the figure you want. This is
# plain JSON on disk so it is greppable without opening the workbook.
SHAPE_DIR = Path(os.environ.get("TOKEN_METRICS_DUNE_SHAPES", ".cache/dune"))


def describe_rows(rows: list[dict], sample: int = 3) -> dict:
    """Columns, inferred types, and a few real rows — enough to map the query in one pass."""
    columns = sorted({k for r in rows[:50] for k in r}) if rows else []
    types = {}
    for col in columns:
        for r in rows[:50]:
            if r.get(col) is not None:
                types[col] = type(r[col]).__name__
                break
        types.setdefault(col, "null")
    return {"row_count": len(rows), "columns": columns, "types": types,
            "sample_rows": rows[:sample]}


def write_shape(query_id, project: str, metric: str, rows: list[dict]) -> Path | None:
    """Persist the shape so it survives the run and can be read without the workbook."""
    try:
        SHAPE_DIR.mkdir(parents=True, exist_ok=True)
        path = SHAPE_DIR / f"query-{query_id}.json"
        payload = {"query_id": query_id, "project": project, "metric": metric,
                   "captured_at": pd.Timestamp.now("UTC").isoformat(), **describe_rows(rows)}
        path.write_text(json.dumps(payload, indent=2, default=str))
        return path
    except Exception as e:  # noqa: BLE001 — never let bookkeeping break a run
        log.warning("could not write the query shape for %s: %s", query_id, e)
        return None


def always_refetch() -> bool:
    return os.environ.get("TOKEN_METRICS_DUNE_ALWAYS", "").strip() in ("1", "true", "yes")


class Dune:
    def __init__(self, has_history: set[tuple[str, str]] | None = None):
        self.key = os.environ.get("DUNE_API_KEY", "").strip()
        self.http = Http(min_interval=0.5)
        self.has_history = has_history or set()
        self._cache: dict[int, list[dict]] = {}   # two metrics can share one query; fetch it once

    def _results(self, query_id: int) -> list[dict]:
        if query_id in self._cache:
            return self._cache[query_id]
        rows, offset, limit = [], 0, 5000
        while True:
            j = self.http.get(f"{API}/query/{query_id}/results",
                              params={"limit": limit, "offset": offset},
                              headers={"X-Dune-API-Key": self.key})
            batch = (j.get("result") or {}).get("rows") or []
            rows.extend(batch)
            if len(batch) < limit:
                self._cache[query_id] = rows
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
        # A forced re-pull means the WHOLE history, never a trailing slice.
        #
        # Later runs pass a 30-day window so the daily tiers re-fetch recent revisions cheaply.
        # Applied to a forced tier 4 re-pull that is exactly wrong: TOKEN_METRICS_DUNE_ALWAYS is
        # set precisely when a mapping has changed and the stored series needs rebuilding, and
        # trimming the result to 30 days would rewrite the last month, leave the other 760 rows
        # standing at their old values, and report success. The cost of ignoring the window here
        # is one extra paid query; the cost of honouring it is a silently half-corrected series.
        if always_refetch() and window_days is not None:
            log.info("TOKEN_METRICS_DUNE_ALWAYS is set — pulling full history, ignoring the %d-day window",
                     window_days)
            window_days = None
        for p in projects:
            name = p["name"]
            for metric, q in (p.get("dune_queries") or {}).items():
                qid = q.get("query_id")
                if not qid:
                    out.unconfigured(SOURCE, name, f"{metric}: no query_id in config", TIER)
                    continue
                # A snapshot query returns CURRENT state, so it is an ongoing source, not a
                # backfill: skipping it once the store has history would freeze the series at
                # its first reading. Only a dated historical query is skip-eligible.
                # NOTE: no query in config is a snapshot today — 8683038 was read that way from a
                # partial column list and turned out to be a daily history. The shape is real
                # though, and the guard below is what catches that mistake, so both stay.
                ongoing = bool(q.get("snapshot") or q.get("ongoing"))
                first_time = (name, metric) not in self.has_history
                if not first_time and not always_refetch() and not ongoing:
                    out.skipped(SOURCE, name,
                                f"{metric}: store already holds a row — backfill NOT run. Set "
                                f"TOKEN_METRICS_DUNE_ALWAYS=1 to force it.", TIER)
                    continue
                # THIS METRIC'S OWN FIRST RUN NEEDS ITS OWN FULL HISTORY, regardless of the
                # window_days the caller passed for the run as a whole. window_days is a global,
                # per-RUN decision — trimmed once the SYSTEM has been running a while, so daily
                # tiers refresh recent revisions cheaply — and it has no idea that a metric added
                # to config after that history built up (GEODNET's archetype-3 actual_buyback_
                # tokens/usd, reusing an already-backfilled query) has never been fetched before.
                # A monthly series with drop_current_period always has its newest surviving row
                # at least one full period old, which a 30-day trailing window excludes entirely —
                # so a brand-new metric on a mature run silently got ZERO rows, not a partial
                # backfill, and looked identical to a broken query. Same treatment as
                # always_refetch: a first-time pull always takes the whole history.
                metric_window_days = None if first_time else window_days
                if first_time and window_days is not None:
                    log.info("%s/%s: first backfill for this metric — pulling full history, "
                             "ignoring the %d-day window", name, metric, window_days)
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
                    snapshot = bool(q.get("snapshot"))
                    has_value = q.get("value_col") or q.get("value_cols")
                    if (not dcol and not snapshot) or not has_value:
                        shape = write_shape(qid, name, metric, rows)
                        types = describe_rows(rows)["types"]
                        rendered = ", ".join(f"{c} ({types.get(c, '?')})" for c in columns) or "none"
                        out.fail(SOURCE, name,
                                 f"{metric}: query {qid} columns not mapped. Columns: {rendered}", TIER)
                        out.gap(name, metric,
                                reason=f"Dune query {qid} ran and returned {len(rows)} rows, but date_col and/or "
                                       f"value_col are not set in config, so no figure was read. "
                                       f"Columns the query ACTUALLY returns: {rendered}."
                                       + (f" Full shape with sample rows written to {shape}." if shape else ""),
                                tiers_attempted="4",
                                suggestion=f"Pick the date column and the value column(s) and set date_col and "
                                           f"value_col (or value_cols for a figure split across several columns) "
                                           f"under {name} dune_queries.{metric} in config.py. "
                                           f"`python dune_probe.py {qid}` re-reads this one query without a full run.")
                        continue

                    write_shape(qid, name, metric, rows)

                    # A query can be correctly mapped and still have a half of it that cannot be
                    # trusted. Declaring that in config puts it on the to-do list every run,
                    # rather than in a comment nobody reads before promoting a column.
                    qw = q.get("quality_warning")
                    if qw:
                        out.gap(name, f"[data] {qw['label']}",
                                reason=f"Dune query {qid}: {qw['reason']}",
                                tiers_attempted="4", suggestion=qw["suggestion"])

                    # A query declared as a snapshot is checked, not trusted. Both of these would
                    # otherwise turn history into a single wrongly dated point.
                    if snapshot:
                        dateish = [c for c in columns if any(d in c.lower() for d in DATEISH)]
                        problem = None
                        if len(rows) > 1:
                            problem = (f"declared snapshot: True but returned {len(rows)} rows, so it is a time "
                                       f"series, not one current-state row")
                        elif dateish:
                            problem = (f"declared snapshot: True but returns date-like column(s) "
                                       f"{', '.join(dateish)}")
                        if problem:
                            out.fail(SOURCE, name, f"{metric}: query {qid} {problem}", TIER)
                            out.gap(name, metric,
                                    reason=f"Dune query {qid} {problem}. Nothing was stored: dating a series by "
                                           f"the run date would collapse its history to today.",
                                    tiers_attempted="4",
                                    suggestion=f"Set date_col (columns: {', '.join(columns)}) and remove "
                                               f"snapshot under {name} dune_queries.{metric} in config.py. "
                                               f"`python dune_probe.py {qid}` shows the shape without a full run.")
                            continue

                    # Columns worth keeping but deliberately not metrics. Captured to the staging
                    # table so evaluating them later costs nothing, and read by nothing until
                    # somebody decides they should be.
                    for col in (q.get("staging_cols") or []):
                        if col not in columns:
                            continue
                        for r in rows[-1:] if snapshot else rows:
                            try:
                                v = float(r[col])
                            except (TypeError, ValueError, KeyError):
                                continue
                            out.stage(name, col, v,
                                      date=(r.get(dcol) if dcol else pd.Timestamp.now("UTC").date()),
                                      source=f"dune:{qid}", tier=TIER,
                                      note=q.get("staging_note", "captured from Dune, not used in any figure"))

                    missing = [c for c in ([dcol] if dcol else []) + (q.get("value_cols") or [q.get("value_col")]) if c not in columns]
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
                        when = r.get(dcol) if dcol else pd.Timestamp.now("UTC").tz_localize(None).normalize()
                        value = self._values(r, q)
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
                            f"{'current-state snapshot' if snapshot else q.get('granularity', 'unspecified') + ' granularity'}"
                            + (f", dropped {dropped} incomplete current-period row(s)" if dropped else "") + ")")
                    out.add(window(df, metric_window_days), SOURCE, name, note, TIER)
                except HttpError as e:
                    # A 4xx is the server's definite answer, and its message is what distinguishes
                    # "no such query" from "exists, but not yours to read".
                    out.fail(SOURCE, name, f"{metric}: query {qid}: {e}", TIER)
                    hint = ("The query id does not resolve for this key. Either it does not exist, or it exists "
                            "and this account cannot read it — Dune's own message above is what tells them apart. "
                            "A query you do not own often cannot be read through the results endpoint at all; "
                            "forking it into your own account gives you an id that can."
                            if e.status in (403, 404) else
                            "Check the query id and the API key's permissions.")
                    out.gap(name, metric,
                            reason=f"Dune query {qid} returned HTTP {e.status}: {e.detail or 'no message'}",
                            tiers_attempted="4", suggestion=hint)
                except Exception as e:  # noqa: BLE001
                    out.fail(SOURCE, name, f"{metric}: query {qid}: {e}", TIER)
                    out.gap(name, metric, reason=f"Dune query {qid} failed: {e}", tiers_attempted="4",
                            suggestion="Check the query id and its date_col/value_col in config.py")
