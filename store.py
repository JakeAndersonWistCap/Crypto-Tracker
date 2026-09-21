"""
store.py — the local historical store.

SQLite (metrics.db). The store is the durable artefact; the workbook is disposable output.

Tables
  metrics           date, project, metric, value, source, tier, fetched_at   PK(date, project, metric)
  manual_overrides  date, project, metric, value, source_note, entered_on    PK(date, project, metric)
  fetch_status      source, project, last_attempt_at, last_success_at, last_error, last_rows
  run_log           run_id, ts, source, tier, project, rows, status, message
  review_queue      run_id, ts, project, metric, date, value, prior_value, reason, action, source, tier
  gap_report        run_id, ts, project, metric, tiers_attempted, reason, suggestion

Overrides live in their own table so a later fetch can never clobber them; they win at read
time and are flagged is_manual with entered_on.

review_queue and gap_report are rebuilt per run (rows carry run_id); the workbook shows the
current run's rows.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DB_PATH = Path(os.environ.get("TOKEN_METRICS_DB", "metrics.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    date        TEXT NOT NULL,
    project     TEXT NOT NULL,
    metric      TEXT NOT NULL,
    value       REAL,
    source      TEXT NOT NULL,
    tier        INTEGER,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (date, project, metric)
);
CREATE INDEX IF NOT EXISTS ix_metrics_pm ON metrics(project, metric, date);

CREATE TABLE IF NOT EXISTS manual_overrides (
    date        TEXT NOT NULL,
    project     TEXT NOT NULL,
    metric      TEXT NOT NULL,
    value       REAL,
    source_note TEXT,
    entered_on  TEXT,
    PRIMARY KEY (date, project, metric)
);

CREATE TABLE IF NOT EXISTS fetch_status (
    source          TEXT NOT NULL,
    project         TEXT NOT NULL,
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_error      TEXT,
    last_rows       INTEGER,
    PRIMARY KEY (source, project)
);

CREATE TABLE IF NOT EXISTS run_log (
    run_id   TEXT NOT NULL,
    ts       TEXT NOT NULL,
    source   TEXT NOT NULL,
    tier     INTEGER,
    project  TEXT,
    rows     INTEGER,
    status   TEXT NOT NULL,      -- ok | failed | skipped | unconfigured
    message  TEXT
);

CREATE TABLE IF NOT EXISTS review_queue (
    run_id      TEXT NOT NULL,
    ts          TEXT NOT NULL,
    project     TEXT NOT NULL,
    metric      TEXT NOT NULL,
    date        TEXT,
    value       REAL,
    prior_value REAL,
    reason      TEXT NOT NULL,   -- out_of_bounds | change_threshold | address_unverified
    action      TEXT NOT NULL,   -- rejected | stored_flagged
    source      TEXT,
    tier        INTEGER
);

-- Figures a source returned that are deliberately NOT metrics: captured so they are not lost
-- and can be evaluated, but read by nothing. Never joined to `metrics`, never in a calculation.
CREATE TABLE IF NOT EXISTS staging (
    run_id   TEXT NOT NULL,
    ts       TEXT NOT NULL,
    date     TEXT,
    project  TEXT NOT NULL,
    name     TEXT NOT NULL,      -- the raw column/field name, not a metric key
    value    REAL,
    source   TEXT,
    tier     INTEGER,
    note     TEXT
);

CREATE TABLE IF NOT EXISTS gap_report (
    run_id          TEXT NOT NULL,
    ts              TEXT NOT NULL,
    project         TEXT NOT NULL,
    metric          TEXT NOT NULL,
    tiers_attempted TEXT,
    reason          TEXT NOT NULL,
    suggestion      TEXT,
    priority        INTEGER,
    priority_label  TEXT
);
"""

LONG_COLUMNS = ["date", "project", "metric", "value", "source", "tier"]


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _migrate(conn: sqlite3.Connection):
    """Add columns introduced after the first release, so an existing metrics.db keeps its history."""
    for table, col, decl in (("metrics", "tier", "INTEGER"), ("run_log", "tier", "INTEGER"),
                             ("gap_report", "priority", "INTEGER"), ("gap_report", "priority_label", "TEXT")):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if cols and col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    conn.commit()


class Store:
    def __init__(self, path: Path | str = DB_PATH):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        _migrate(self.conn)

    # ---------------------------------------------------------------- state
    def is_empty(self) -> bool:
        return self.conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == 0

    def has_series(self, project: str, metric: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM metrics WHERE project=? AND metric=? LIMIT 1", (project, metric)
        ).fetchone() is not None

    def last_date(self, project: str, metric: str) -> str | None:
        row = self.conn.execute(
            "SELECT MAX(date) FROM metrics WHERE project=? AND metric=?", (project, metric)
        ).fetchone()
        return row[0] if row else None

    def latest_value(self, project: str, metric: str) -> float | None:
        """Most recent stored value — the prior value a change-threshold check compares against."""
        row = self.conn.execute(
            "SELECT value FROM metrics WHERE project=? AND metric=? ORDER BY date DESC LIMIT 1",
            (project, metric),
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else None

    def latest_values(self) -> dict[tuple[str, str], float]:
        """All (project, metric) -> most recent value, in one query."""
        rows = self.conn.execute(
            """SELECT m.project, m.metric, m.value FROM metrics m
               JOIN (SELECT project, metric, MAX(date) AS d FROM metrics GROUP BY project, metric) t
                 ON m.project=t.project AND m.metric=t.metric AND m.date=t.d"""
        ).fetchall()
        return {(p, k): float(v) for p, k, v in rows if v is not None}

    def last_dates(self) -> dict[tuple[str, str], str]:
        """All (project, metric) -> most recent stored DATE.

        The companion to latest_values. A differenced flow needs both: the prior value to
        subtract, and the date it was observed on — because a delta between two readings that
        land on the same date spans no interval the store can represent.
        """
        rows = self.conn.execute(
            "SELECT project, metric, MAX(date) FROM metrics GROUP BY project, metric").fetchall()
        return {(p, k): d for p, k, d in rows if d}

    def values_before(self, date: str) -> dict[tuple[str, str], tuple[float, str, str]]:
        """(project, metric) -> (value, date, source) of the last row STRICTLY BEFORE `date`.

        The differencing inputs must come from an EARLIER DAY, never from today. Differencing
        against a same-day reading is what turned PancakeSwap's real 59,857,159.01 burn into
        0.0123: an earlier run that day had already written a 09-14 row, the next run differenced
        against THAT instead of against 09-12, and — because the store keys on
        (date, project, metric) — the dust delta overwrote the correct flow on the same key.

        Anchoring on the last earlier-dated row makes a same-day re-run IDEMPOTENT: it recomputes
        the identical delta from the identical starting point, so re-running is free rather than
        destructive.
        """
        rows = self.conn.execute(
            """SELECT m.project, m.metric, m.value, m.date, m.source FROM metrics m
               JOIN (SELECT project, metric, MAX(date) AS d FROM metrics
                     WHERE date < ? GROUP BY project, metric) t
                 ON m.project=t.project AND m.metric=t.metric AND m.date=t.d""", (date,)).fetchall()
        return {(p, k): (float(v), d, src) for p, k, v, d, src in rows if v is not None}

    def last_sources(self) -> dict[tuple[str, str], str]:
        """All (project, metric) -> the source string of the most recent stored row.

        A delta is only a flow if BOTH readings measured the same thing. When a contract is
        re-pointed — Uniswap's burn moving from the Firepit executor to the dead address — the
        change of measuring point shows up as an enormous one-day move that is not a burn.
        """
        rows = self.conn.execute(
            """SELECT m.project, m.metric, m.source FROM metrics m
               JOIN (SELECT project, metric, MAX(date) AS d FROM metrics GROUP BY project, metric) t
                 ON m.project=t.project AND m.metric=t.metric AND m.date=t.d""").fetchall()
        return {(p, k): src for p, k, src in rows if src}

    def observation_counts(self) -> dict[tuple[str, str], int]:
        """All (project, metric) -> number of stored observations."""
        rows = self.conn.execute(
            "SELECT project, metric, COUNT(*) FROM metrics GROUP BY project, metric").fetchall()
        return {(p, k): int(n) for p, k, n in rows}

    # ---------------------------------------------------------------- writes
    def upsert(self, df: pd.DataFrame) -> int:
        """Upsert a tidy long frame (date, project, metric, value, source, tier)."""
        if df is None or df.empty:
            return 0
        df = df.copy()
        if "tier" not in df.columns:
            df["tier"] = None
        df = df[LONG_COLUMNS].dropna(subset=["date", "project", "metric"])
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["value"]).drop_duplicates(subset=["date", "project", "metric"], keep="last")
        now = utcnow()
        rows = [
            (r.date, r.project, r.metric, float(r.value), r.source,
             int(r.tier) if pd.notna(r.tier) else None, now)
            for r in df.itertuples(index=False)
        ]
        self.conn.executemany(
            """INSERT INTO metrics(date, project, metric, value, source, tier, fetched_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(date, project, metric) DO UPDATE SET
                 value=excluded.value, source=excluded.source, tier=excluded.tier,
                 fetched_at=excluded.fetched_at""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def load_overrides_csv(self, csv_path: Path | str) -> int:
        """Replace manual_overrides from manual_overrides.csv."""
        csv_path = Path(csv_path)
        self.conn.execute("DELETE FROM manual_overrides")
        if not csv_path.exists():
            self.conn.commit()
            return 0
        df = pd.read_csv(csv_path, dtype=str, comment="#").fillna("")
        df.columns = [c.strip() for c in df.columns]
        required = ["date", "project", "metric", "value", "source_note", "entered_on"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"manual_overrides.csv missing columns: {missing}")
        df = df[df["project"].str.strip() != ""]
        rows = []
        for r in df.itertuples(index=False):
            try:
                value = float(str(r.value).replace(",", ""))
            except ValueError:
                continue
            rows.append((pd.to_datetime(r.date).strftime("%Y-%m-%d"), r.project.strip(),
                         r.metric.strip(), value, r.source_note.strip(), r.entered_on.strip()))
        self.conn.executemany(
            "INSERT OR REPLACE INTO manual_overrides(date, project, metric, value, source_note, entered_on) VALUES (?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def record_fetch(self, run_id: str, source: str, project: str | None, rows: int, status: str,
                     message: str = "", tier: int | None = None):
        ts = utcnow()
        self.conn.execute(
            "INSERT INTO run_log(run_id, ts, source, tier, project, rows, status, message) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, ts, source, tier, project, rows, status, (message or "")[:2000]),
        )
        if project is not None and status in ("ok", "failed"):
            existing = self.conn.execute(
                "SELECT last_success_at FROM fetch_status WHERE source=? AND project=?", (source, project)
            ).fetchone()
            last_success = ts if status == "ok" else (existing[0] if existing else None)
            self.conn.execute(
                """INSERT INTO fetch_status(source, project, last_attempt_at, last_success_at, last_error, last_rows)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(source, project) DO UPDATE SET
                     last_attempt_at=excluded.last_attempt_at, last_success_at=excluded.last_success_at,
                     last_error=excluded.last_error, last_rows=excluded.last_rows""",
                (source, project, ts, last_success, "" if status == "ok" else (message or "")[:500], rows),
            )
        self.conn.commit()

    def record_review(self, run_id: str, items: list[dict]):
        if not items:
            return 0
        ts = utcnow()
        self.conn.executemany(
            """INSERT INTO review_queue(run_id, ts, project, metric, date, value, prior_value, reason, action, source, tier)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [(run_id, ts, i["project"], i["metric"], i.get("date"), i.get("value"), i.get("prior_value"),
              i["reason"], i["action"], i.get("source"), i.get("tier")) for i in items],
        )
        self.conn.commit()
        return len(items)

    def record_gaps(self, run_id: str, items: list[dict]):
        if not items:
            return 0
        ts = utcnow()
        self.conn.executemany(
            """INSERT INTO gap_report(run_id, ts, project, metric, tiers_attempted, reason, suggestion,
                                     priority, priority_label)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [(run_id, ts, i["project"], i["metric"], i.get("tiers_attempted", ""), i["reason"],
              i.get("suggestion", ""), i.get("priority", 5), i.get("priority_label", "P5 uncovered"))
             for i in items],
        )
        self.conn.commit()
        return len(items)

    # ---------------------------------------------------------------- reads
    def record_staging(self, run_id: str, items: list[dict]) -> int:
        """Overwrite this run's staged figures. Nothing else in the codebase reads this table."""
        if items is None:
            return 0
        ts = utcnow()
        self.conn.execute("DELETE FROM staging WHERE run_id=?", (run_id,))
        self.conn.executemany(
            "INSERT INTO staging (run_id, ts, date, project, name, value, source, tier, note) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [(run_id, ts, i.get("date"), i["project"], i["name"], i.get("value"),
              i.get("source"), i.get("tier"), i.get("note", "")) for i in items],
        )
        self.conn.commit()
        return len(items)

    def load_long(self) -> pd.DataFrame:
        """All rows with manual overrides applied last.
        Columns: date, project, metric, value, source, tier, fetched_at, is_manual, entered_on, source_note."""
        fetched = pd.read_sql_query(
            "SELECT date, project, metric, value, source, tier, fetched_at FROM metrics", self.conn
        )
        fetched["is_manual"] = False
        fetched["entered_on"] = ""
        fetched["source_note"] = ""
        manual = pd.read_sql_query(
            "SELECT date, project, metric, value, source_note, entered_on FROM manual_overrides", self.conn
        )
        if not manual.empty:
            manual["source"] = "manual"
            manual["tier"] = None
            manual["fetched_at"] = manual["entered_on"]
            manual["is_manual"] = True
            manual = manual[fetched.columns]
            fetched = pd.concat([fetched, manual], ignore_index=True)
            fetched = fetched.drop_duplicates(subset=["date", "project", "metric"], keep="last")
        fetched["date"] = pd.to_datetime(fetched["date"])
        return fetched.sort_values(["project", "metric", "date"]).reset_index(drop=True)

    def fetch_status(self) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM fetch_status", self.conn)

    def run_log(self, run_id: str | None = None) -> pd.DataFrame:
        if run_id:
            return pd.read_sql_query("SELECT * FROM run_log WHERE run_id=? ORDER BY ts", self.conn, params=(run_id,))
        return pd.read_sql_query("SELECT * FROM run_log ORDER BY ts", self.conn)

    def review_queue(self, run_id: str | None = None) -> pd.DataFrame:
        """The review queue for ONE run — the given one, or the most recent.

        Same contract and same fix as gap_report above: rebuilt per run, so the union across runs
        is every flag ever raised, including every one already dealt with.
        """
        run_id = run_id or self.latest_run_id("review_queue")
        if run_id is None:
            return pd.read_sql_query("SELECT * FROM review_queue WHERE 0", self.conn)
        return pd.read_sql_query(
            "SELECT * FROM review_queue WHERE run_id=? ORDER BY project, metric",
            self.conn, params=(run_id,))

    def staging(self, run_id: str | None = None) -> pd.DataFrame:
        if run_id:
            return pd.read_sql_query(
                "SELECT * FROM staging WHERE run_id=? ORDER BY project, name, date", self.conn, params=(run_id,))
        return pd.read_sql_query("SELECT * FROM staging ORDER BY ts DESC, project, name", self.conn)

    def latest_run_id(self, table: str) -> str | None:
        """The run_id of the most recent run that wrote to `table`, or None if it is empty."""
        if table not in ("gap_report", "review_queue", "staging", "run_log"):
            raise ValueError(f"not a per-run table: {table!r}")
        # rowid BREAKS THE TIE, and it is not a test-only concern: ts has one-second resolution,
        # so two runs inside the same second leave SQLite free to return either, and "either" here
        # means the report can silently come from the older run. Insert order is the real order.
        row = self.conn.execute(
            f"SELECT run_id FROM {table} ORDER BY ts DESC, rowid DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def gap_report(self, run_id: str | None = None) -> pd.DataFrame:
        """The gap report for ONE run — the given one, or the most recent.

        ** WITHOUT run_id THIS USED TO RETURN EVERY GAP ROW EVER WRITTEN. ** gap_report is
        rebuilt per run and its rows carry run_id, so the union across runs is not a longer
        report, it is the same report repeated once per run WITH EVERY CLOSED ROW STILL IN IT.
        A question settled a week ago kept appearing because the run that first recorded it is
        still in the table, and there is no way for a later run to retract a row it never wrote.
        That is the second distinct mechanism by which closures failed to reach the report; the
        first was closure recorded in prose while the structural `status` field stayed open.

        It bit on the recalc path specifically. build_workbook takes run_id when it follows a
        fetch and None when it is re-run over an existing store — so the standalone rebuild, the
        one used precisely to check that a fix landed, was the one showing the stalest report.
        GEODNET's buyback_fund_balance was the visible case: declared not_applicable, no longer
        generated by fetch.gaps.detect at all, and still on the sheet.

        Defaulting to the latest run keeps the "rebuilt per run" contract the schema comment
        already claims. History is not lost — it is still in the table, keyed by run_id, for
        anyone who asks for a specific run.
        """
        run_id = run_id or self.latest_run_id("gap_report")
        if run_id is None:
            return pd.read_sql_query("SELECT * FROM gap_report WHERE 0", self.conn)
        return pd.read_sql_query(
            "SELECT * FROM gap_report WHERE run_id=? ORDER BY COALESCE(priority,5), project, metric",
            self.conn, params=(run_id,))

    def close(self):
        self.conn.close()
