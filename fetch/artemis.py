"""
fetch/artemis.py — daily settlement volume from Jake's Artemis CSV exports. 2026-09-30.

    data/artemis/<Chain>_-_Settlement_Volume.csv     DateTime, "<Chain> - Settlement Volume"

One file per chain (config.ARTEMIS_SETTLEMENT["chains"]), exported by hand from Artemis's
Settlement Volume chart. Definition (Artemis, Powered by Flipside): "Total settlement volume per
day in USD (DEX Volumes + NFT Trading Volume + P2P Transfer Volume)".

ONE DEFINITION, NEVER MIXED: the value column must be exactly "<artemis_name> - Settlement
Volume" for the chain the file is declared for. Any other header — another chain's, another
metric's — stores nothing and says what it found. A date that does not parse, or a negative or
non-numeric value, refuses the whole file: a half-read export would sum to a wrong 365 days.
Blank cells are days Artemis has no figure for; they are left out and counted in the log line.

LOCAL FILES ONLY — no network. The Artemis API (/asset) answered HTTP 410 without a key
(2026-09-30), so it is not wired: with ARTEMIS_API_KEY set, the run says so and asks for the
probe's keyed response before anything is built on it.

Also looks in the repository root, so a file dropped there is used (and the log says to move it).
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
        df = pd.read_csv(path)
    except Exception as e:  # noqa: BLE001
        return f"{path.name} did not read as CSV: {e}"
    cols = [str(c).strip() for c in df.columns]
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

    def _find(self, fname: str) -> Path | None:
        for d in (self.root / config.ARTEMIS_SETTLEMENT["dir"], self.root):
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
            path = self._find(chain["file"])
            if path is None:
                out.skipped(SOURCE, name, f"{metric}: no Artemis export at {spec['dir']}/{chain['file']} "
                                          f"— export it from Artemis (RUNBOOK.md, monthly)", TIER)
                continue
            got = parse(path, chain["artemis_name"])
            if isinstance(got, str):
                out.fail(SOURCE, name, f"{metric}: {got}. NOTHING STORED.", TIER)
                continue
            pts, blank = got
            pts = [(d, v) for d, v in pts if d < today()]
            if not pts:
                out.fail(SOURCE, name, f"{metric}: {path.name} holds no complete day", TIER)
                continue
            label = source_label(chain, path)
            frame = tidy(pts, name, metric, label, TIER)
            last = frame["date"].max()
            age = (today() - last).days
            where = "" if path.parent != self.root else \
                f" (read from the repository root; move it to {spec['dir']}/)"
            out.add(frame, SOURCE, name,
                    f"{metric} = {label}: {len(frame)} day(s) {frame['date'].min().date()}..{last.date()}, "
                    f"{blank} blank day(s) left out; last date {age} day(s) ago"
                    + (f" — STALE beyond {spec['stale_after_days']} days: re-export" if age > spec["stale_after_days"] else "")
                    + where, TIER)
