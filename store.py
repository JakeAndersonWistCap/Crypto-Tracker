"""
store.py — the local historical store.

SQLite (metrics.db) with a single long table keyed on (date, project, metric), upsert on
conflict. The store is the durable artefact; the workbook is disposable output.

Tables
  metrics           date, project, metric, value, source, fetched_at        PK(date, project, metric)
  manual_overrides  date, project, metric, value, source_note, entered_on   PK(date, project, metric)
  fetch_status      source, project, last_attempt_at, last_success_at, last_error, last_rows
  run_log           run_id, ts, source, project, rows, status, message

Overrides live in their own table so a later fetch can never clobber them; they win at read
time (load_long / load_wide) and are flagged is_manual with entered_on.
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
    project  TEXT,
    rows     INTEGER,
    status   TEXT NOT NULL,      -- ok | failed | skipped | unconfigured
    message  TEXT
);
"""

LONG_COLUMNS = ["date", "project", "metric", "value", "source"]


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, path: Path | str = DB_PATH):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------------------------------------------------------------- state
    def is_empty(self) -> bool:
        return self.conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == 0

    def has_series(self, project: str, metric: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM metrics WHERE project=? AND metric=? LIMIT 1", (project, metric)
        ).fetchone()
        return row is not None

    def last_date(self, project: str, metric: str) -> str | None:
        row = self.conn.execute(
            "SELECT MAX(date) FROM metrics WHERE project=? AND metric=?", (project, metric)
        ).fetchone()
        return row[0] if row else None

    # ---------------------------------------------------------------- writes
    def upsert(self, df: pd.DataFrame) -> int:
        """Upsert a tidy long frame (date, project, metric, value, source). Returns rows written."""
        if df is None or df.empty:
            return 0
        df = df[LONG_COLUMNS].copy()
        df = df.dropna(subset=["date", "project", "metric"])
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["value"])
        df = df.drop_duplicates(subset=["date", "project", "metric"], keep="last")
        now = utcnow()
        rows = [(r.date, r.project, r.metric, float(r.value), r.source, now) for r in df.itertuples(index=False)]
        self.conn.executemany(
            """INSERT INTO metrics(date, project, metric, value, source, fetched_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(date, project, metric) DO UPDATE SET
                 value=excluded.value, source=excluded.source, fetched_at=excluded.fetched_at""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def load_overrides_csv(self, csv_path: Path | str) -> int:
        """Replace the manual_overrides table from manual_overrides.csv. Returns rows loaded."""
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
            rows.append((
                pd.to_datetime(r.date).strftime("%Y-%m-%d"), r.project.strip(), r.metric.strip(),
                value, r.source_note.strip(), r.entered_on.strip(),
            ))
        self.conn.executemany(
            "INSERT OR REPLACE INTO manual_overrides(date, project, metric, value, source_note, entered_on) VALUES (?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def record_fetch(self, run_id: str, source: str, project: str | None, rows: int, status: str, message: str = ""):
        ts = utcnow()
        self.conn.execute(
            "INSERT INTO run_log(run_id, ts, source, project, rows, status, message) VALUES (?,?,?,?,?,?,?)",
            (run_id, ts, source, project, rows, status, message[:2000] if message else ""),
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
                     last_attempt_at=excluded.last_attempt_at,
                     last_success_at=excluded.last_success_at,
                     last_error=excluded.last_error,
                     last_rows=excluded.last_rows""",
                (source, project, ts, last_success, "" if status == "ok" else message[:500], rows),
            )
        self.conn.commit()

    # ---------------------------------------------------------------- reads
    def load_long(self) -> pd.DataFrame:
        """All rows, with manual overrides applied last. Columns:
        date, project, metric, value, source, fetched_at, is_manual, entered_on, source_note."""
        fetched = pd.read_sql_query(
            "SELECT date, project, metric, value, source, fetched_at FROM metrics", self.conn
        )
        fetched["is_manual"] = False
        fetched["entered_on"] = ""
        fetched["source_note"] = ""
        manual = pd.read_sql_query(
            "SELECT date, project, metric, value, source_note, entered_on FROM manual_overrides", self.conn
        )
        if not manual.empty:
            manual["source"] = "manual"
            manual["fetched_at"] = manual["entered_on"]
            manual["is_manual"] = True
            manual = manual[fetched.columns]
            fetched = pd.concat([fetched, manual], ignore_index=True)
            # overrides loaded last: keep the last occurrence per key
            fetched = fetched.drop_duplicates(subset=["date", "project", "metric"], keep="last")
        fetched["date"] = pd.to_datetime(fetched["date"])
        return fetched.sort_values(["project", "metric", "date"]).reset_index(drop=True)

    def fetch_status(self) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM fetch_status", self.conn)

    def run_log(self, run_id: str | None = None) -> pd.DataFrame:
        if run_id:
            return pd.read_sql_query("SELECT * FROM run_log WHERE run_id=? ORDER BY ts", self.conn, params=(run_id,))
        return pd.read_sql_query("SELECT * FROM run_log ORDER BY ts", self.conn)

    def close(self):
        self.conn.close()
