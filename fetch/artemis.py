"""
fetch/artemis.py — daily settlement volume from Jake's Artemis CSV exports. 2026-09-30.

    <Chain> - Settlement Volume.csv      DateTime, "<Chain> - Settlement Volume"  (Artemis's own name)
    <Chain>_-_Settlement_Volume.csv      (the same, underscored)

in the repository root OR data/artemis/ (or $TOKEN_METRICS_ARTEMIS_DIR), so Jake never renames or
moves a download. One file per chain (config.ARTEMIS_SETTLEMENT["chains"]), read AT RUN TIME.
THE WHOLE FILE IS IMPORTED EVERY RUN, whatever the run's window (Jake's run 2026-09-30: the
export ends 2026-08-25, and the file was found nowhere the importer looked — 0 rows, 0 failed);
the upsert makes a re-import idempotent. A UTF-8 byte-order mark on the header is stripped. The
log line names the exact path read, the rows read, the date range and the rows stored. The files are NEVER committed until Artemis's terms are read
(.gitignore). A MISSING FILE GAPS THE METRIC with that reason — the run never fails on it. Definition (Artemis, Powered by Flipside): "Total settlement volume per
day in USD (DEX Volumes + NFT Trading Volume + P2P Transfer Volume)".

ONE DEFINITION, NEVER MIXED: the value column must be exactly "<artemis_name> - Settlement
Volume" for the chain the file is declared for. Any other header — another chain's, another
metric's — stores nothing and says what it found. A date that does not parse, or a negative or
non-numeric value, refuses the whole file: a half-read export would sum to a wrong 365 days.
Blank cells are days Artemis has no figure for; they are left out and counted in the log line.

LOCAL FILES ONLY — no network. The Artemis API (/asset) answered HTTP 410 without a key
(2026-09-30), so it is not wired: with ARTEMIS_API_KEY set, the run says so and asks for the
probe's keyed response before anything is built on it.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
from pathlib import Path

import pandas as pd

import config

from .base import tidy, today

log = logging.getLogger("token_metrics.fetch.artemis")

SOURCE = "artemis"
TIER = 5
ROOT = Path(__file__).resolve().parent.parent


def source_label(chain: dict, path: Path) -> str:
    """artemis:settlement_volume (CSV export, <who>, <date>) — the date Jake exported it, or the
    file's own date when the config does not record one (and the label says which)."""
    if chain.get("exported_on"):
        return (f"artemis:settlement_volume (CSV export, {chain.get('exported_by', 'Jake')}, "
                f"{chain['exported_on']})")
    mtime = _dt.datetime.fromtimestamp(path.stat().st_mtime, tz=_dt.timezone.utc).date()
    return f"artemis:settlement_volume (CSV export, file dated {mtime})"


def parse(path: Path, artemis_name: str) -> tuple[list[tuple], int] | str:
    """([(date, usd)], n_blank) from one export, or the reason it is refused."""
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")        # a BOM would break the header check
    except Exception as e:  # noqa: BLE001
        return f"{path.name} did not read as CSV: {e}"
    cols = [str(c).strip().lstrip("\ufeff") for c in df.columns]
    want = f"{artemis_name}{config.ARTEMIS_SETTLEMENT['column_suffix']}"
    if len(cols) != 2 or cols[0] != "DateTime" or cols[1].lower() != want.lower():
        return (f"{path.name}: columns {cols}, not ['DateTime', {want!r}] — a different chain or "
                f"metric is never mixed into settlement_volume_usd")
    df.columns = ["DateTime", "value"]
    dates = pd.to_datetime(df["DateTime"], errors="coerce", utc=True)
    if dates.isna().any():
        bad = df.loc[dates.isna(), "DateTime"].head(3).tolist()
        return f"{path.name}: {int(dates.isna().sum())} DateTime value(s) do not parse, e.g. {bad}"
    raw = df["value"]
    blank = raw.isna() | (raw.astype(str).str.strip() == "")
    vals = pd.to_numeric(raw.astype(str).str.replace(",", "", regex=False).str.replace("$", "", regex=False),
                         errors="coerce")
    bad = ~blank & vals.isna()
    if bad.any():
        return f"{path.name}: {int(bad.sum())} value(s) are not numbers, e.g. {raw[bad].head(3).tolist()}"
    if (vals[~blank] < 0).any():
        return f"{path.name}: negative settlement volume on {int((vals[~blank] < 0).sum())} day(s)"
    days = dates.dt.tz_convert(None).dt.normalize()
    keep = ~blank
    if days[keep].duplicated().any():
        return f"{path.name}: {int(days[keep].duplicated().sum())} duplicated day(s)"
    pts = sorted(zip(days[keep], vals[keep].astype(float)))
    return pts, int(blank.sum())


class ArtemisCSV:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, root: Path | None = None, **_ignored):
        self.root = Path(root) if root else ROOT

    def folders(self) -> list[Path]:
        """Where exports are looked for, in order: $TOKEN_METRICS_ARTEMIS_DIR if set, else the repo
        root and data/artemis/."""
        spec = config.ARTEMIS_SETTLEMENT
        env = os.environ.get(spec["dir_env"], "").strip()
        dirs = [Path(env)] if env else [Path(d) for d in spec["dirs"]]
        return [d if d.is_absolute() else self.root / d for d in dirs]

    def folder(self) -> Path:
        return self.folders()[0]

    @staticmethod
    def names(artemis_name: str) -> tuple[str, str]:
        """Both accepted file names: Artemis's download, and the underscored form."""
        return (f"{artemis_name} - Settlement Volume.csv", f"{artemis_name}_-_Settlement_Volume.csv")

    def _find(self, artemis_name: str) -> Path | None:
        for d in self.folders():
            for fname in self.names(artemis_name):
                f = d / fname
                if f.is_file():
                    return f
        return None

    def run(self, projects: list[dict], window_days, out):
        spec = config.ARTEMIS_SETTLEMENT
        metric = spec["metric"]
        if os.environ.get(spec["api"]["key_env"], "").strip():
            out.skipped(SOURCE, None, f"{spec['api']['key_env']} is set, but the Artemis API is NOT wired: "
                                      f"{spec['api']['without_key']} and no keyed response has been seen. "
                                      f"Run `python check_offline_items.py settlement_sources` and paste "
                                      f"back the keyed response; the CSV exports stay the route.", TIER)
        names = {p["name"] for p in projects}
        for name, chain in spec["chains"].items():
            if name not in names:
                continue
            path = self._find(chain["artemis_name"])
            if path is None:
                where = " or ".join(repr(n) for n in self.names(chain["artemis_name"]))
                why = (f"no Artemis export found: {where} is in none of "
                       f"{', '.join(str(d) for d in self.folders())} (read at run time, never committed; "
                       f"set {spec['dir_env']} to read another folder)")
                out.skipped(SOURCE, name, f"{metric}: {why}", TIER)
                out.gap(name, metric, reason=why, tiers_attempted="5",
                        suggestion=f"Export Artemis's {chain['artemis_name']} Settlement Volume chart as "
                                   f"CSV into the repo root (RUNBOOK.md 11j).")
                continue
            got = parse(path, chain["artemis_name"])
            if isinstance(got, str):
                out.fail(SOURCE, name, f"{metric}: {got}. NOTHING STORED.", TIER)
                continue
            pts, blank = got
            n_read = len(pts) + blank
            pts = [(d, v) for d, v in pts if d < today()]
            if not pts:
                out.fail(SOURCE, name, f"{metric}: {path} holds no complete day ({n_read} row(s) read)", TIER)
                continue
            label = source_label(chain, path)
            # THE WHOLE FILE, never window_days: the export's last day is weeks old by design.
            frame = tidy(pts, name, metric, label, TIER)
            last = frame["date"].max()
            age = (today() - last).days
            msg = (f"{metric} = {label}: read {path} — {n_read} row(s) read, {blank} blank, "
                   f"{len(frame)} day(s) stored {frame['date'].min().date()}..{last.date()} (whole file, "
                   f"not the run's window); last date {age} day(s) ago"
                   + (f" — STALE beyond {spec['stale_after_days']} days: re-export" if age > spec["stale_after_days"] else ""))
            # ON THE CONSOLE TOO (Jake, 2026-09-30 17:21): the path, rows read and date range
            log.info("artemis_csv %s: %s", name, msg)
            out.add(frame, SOURCE, name, msg, TIER)
