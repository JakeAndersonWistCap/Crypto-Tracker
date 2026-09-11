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

CREATE TABLE IF NOT EXISTS gap_report (
    run_id          TEXT NOT NULL,
    ts              TEXT NOT NULL,
    project         TEXT NOT NULL,
    metric          TEXT NOT NULL,
    tiers_attempted TEXT,
    reason          TEXT NOT NULL,
    suggestion      TEXT
);
"""

LONG_COLUMNS = ["date", "project", "metric", "value", "source", "tier"]


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _migrate(conn: sqlite3.Connection):
    """Add columns introduced after the first release, so an existing metrics.db keeps its history."""
    for table, col, decl in (("metrics", "tier", "INTEGER"), ("run_log", "tier", "INTEGER")):
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
            """INSERT INTO gap_report(run_id, ts, project, metric, tiers_attempted, reason, suggestion)
               VALUES (?,?,?,?,?,?,?)""",
            [(run_id, ts, i["project"], i["metric"], i.get("tiers_attempted", ""), i["reason"], i.get("suggestion", ""))
             for i in items],
        )
        self.conn.commit()
        return len(items)

    # ---------------------------------------------------------------- reads
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
        if run_id:
            return pd.read_sql_query("SELECT * FROM review_queue WHERE run_id=? ORDER BY project, metric", self.conn, params=(run_id,))
        return pd.read_sql_query("SELECT * FROM review_queue ORDER BY ts DESC", self.conn)

    def gap_report(self, run_id: str | None = None) -> pd.DataFrame:
        if run_id:
            return pd.read_sql_query("SELECT * FROM gap_report WHERE run_id=? ORDER BY project, metric", self.conn, params=(run_id,))
        return pd.read_sql_query("SELECT * FROM gap_report ORDER BY ts DESC", self.conn)

    def close(self):
        self.conn.close()
