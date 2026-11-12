"""ONE BUILD OF THE 15 REAL DUMPS PER TEST PROCESS (P5-0 a, Jake 2026-11-03).

Eight test files built the same workbook from the same 15 committed real dumps (fixtures/real_*.json), with the same
call and the same patches: about 3 minutes each, 8 times a gate. They now share one build per process:

    from shared_build import plain_build
    @pytest.fixture(scope="module")
    def built(tmp_path_factory):
        rec = plain_build(tmp_path_factory, "p3_1")
        ...

THE RECIPE is fixed here and is exactly what each of those fixtures did: every real dump into a fresh store, the
store path patched, no LibreOffice recalculation, TOKEN_METRICS_STORE_VALUES=1, both files built at the newest dump
date. A fixture that builds anything else (other patches, other file names, probes, a planted project) is not this
build and keeps building its own.

WHAT A CALLER GETS: its OWN copy of the build directory (the audit file, the reader, the store and every file the
build wrote beside them), so no test can change another file's inputs, and a record of the build-output state the
fixtures used to read from the modules right after the build (READER_STATS, BAD_PRINTS, _LAST_GROUPS,
_HEADLINE_CELLS, TRAJECTORY_ROWS, reader.HEADLINE_LOCATIONS), deep-copied for each caller. Callers read those from the record: on a
cache hit the modules hold whatever the last build in the process left there, not this build.

THE KEY (code version): a hash of every source and data file in the repository (.py .json .md .sql .csv .yaml .yml
.txt, the 15 dumps included), the TOKEN_METRICS_* environment, the Python version and the versions of pandas and
openpyxl. A cached build is reused only under the same key; any change to any of those files is a new key and a
fresh build. The cache lives in memory for one process only (one gate, one own-check run): every gate builds
fresh at least once.

THE CONTAINMENT CHECKS:
    - the master copy's file hashes are recorded at build time and re-checked at the end of the session
      (verify_unchanged, called from conftest's pytest_sessionfinish): a test that wrote into the master copy
      fails the run;
    - TOKEN_METRICS_SHARED_BUILD_STALE=1 runs a different, single-project build right after every plain_build call,
      so the modules' live state is NOT the plain build's: a test that still passes in that mode does not depend
      on live module state (P5-0 a, the one-time check of every converted file; repeatable at any time).
"""
from __future__ import annotations

import copy
import hashlib
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY_SUFFIXES = {".py", ".json", ".md", ".sql", ".csv", ".yaml", ".yml", ".txt"}
# directories that hold no build input: caches, scratch, tool state, and machine-local run output
KEY_SKIP_DIRS = {".git", "__pycache__", ".cache", ".pytest_cache", "_scratch", ".claude", "logs", "snapshots", "dumps"}
# machine-local outputs a run writes into the repository root (gitignored); never read by a build of the dumps
KEY_SKIP_FILES = {"coverage_audit.csv", "reader_column_map.csv", "wiring.json", "explorer_routes.json"}
STALE_ENV = "TOKEN_METRICS_SHARED_BUILD_STALE"
STALE_PROJECT = "Pendle"

_CACHE: dict[str, dict] = {}


def key_files(root: Path = ROOT) -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in KEY_SKIP_DIRS)
        for f in sorted(filenames):
            p = Path(dirpath) / f
            if p.suffix in KEY_SUFFIXES and not (p.parent == root and f in KEY_SKIP_FILES):
                out.append(p)
    return out


def code_key(root: Path = ROOT, env: dict | None = None) -> str:
    """The code version: every key file's path and bytes, the TOKEN_METRICS_* environment and the tool versions."""
    import openpyxl
    import pandas
    h = hashlib.sha256()
    for p in key_files(root):
        h.update(str(p.relative_to(root)).encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    env = os.environ if env is None else env
    for k in sorted(k for k in env if k.startswith("TOKEN_METRICS_") and k != STALE_ENV):
        h.update(f"{k}={env[k]}\0".encode())
    h.update(f"py{sys.version_info[:3]} pandas{pandas.__version__} openpyxl{openpyxl.__version__}".encode())
    return h.hexdigest()


def _hash_dir(d: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir()) if p.is_file()}


def _build(d: Path) -> dict:
    """THE RECIPE: what the eight fixtures each did (test_round_p1's build)."""
    import pandas as pd
    import pytest

    import build_workbook as bw
    import measuring_guard as mg
    import reader
    import store as sm
    from real_dump import to_store
    db = d / "metrics.db"
    asofs = []
    for pl in mg._payloads():
        to_store(pl, db)
        asofs.append(pl["meta"]["asof"])
    asof = pd.Timestamp(max(asofs))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sm, "DB_PATH", str(db))
        mp.setitem(sys.modules, "recalc", None)
        mp.setenv("TOKEN_METRICS_STORE_VALUES", "1")
        # PR3B.1 / PR4.0b: every scan explorer_routes.json can wire (Pendle's, Fluid's and Morpho's L2 scans) set to
        # exactly the pinned probe (project_freeze does the same), so the build is the same on every machine; restored
        # after the build
        import config
        mp.setitem(config.OUTFLOW_CATEGORIES["Fluid"], "b", config.OUTFLOW_CATEGORIES["Fluid"]["b"])
        for _proj in sorted({row[1] for row in config.EXPLORER_PROBED_SCANS}):
            _p = config.PROJECT_BY_NAME[_proj]
            mp.setitem(_p, "log_scans", list(_p.get("log_scans") or []))
            if "emissions_from_metric" in _p:
                mp.setitem(_p, "emissions_from_metric", _p["emissions_from_metric"])
        config.pin_frozen_probe()
        st = sm.Store(str(db))
        try:
            bw.build_workbook(st, d / "audit.xlsx", asof=asof, reader_path=d / "reader.xlsx")
        finally:
            st.close()
    return {"asof": asof,
            "stats": copy.deepcopy(dict(bw.READER_STATS)),
            "bad_prints": copy.deepcopy(dict(bw.BAD_PRINTS)),
            "groups": copy.deepcopy(dict(bw._LAST_GROUPS)),
            "headlines": copy.deepcopy(list(bw._HEADLINE_CELLS)),
            "trajectory": copy.deepcopy(list(bw.TRAJECTORY_ROWS)),
            "locations": copy.deepcopy(dict(reader.HEADLINE_LOCATIONS))}


def _stale_build(d: Path) -> None:
    """A different build (one project, its own store) so the modules' live state is not the plain build's."""
    import pandas as pd
    import pytest

    import build_workbook as bw
    import measuring_guard as mg
    import store as sm
    from real_dump import to_store
    db = d / "stale.db"
    pl = next(p for p in mg._payloads() if p["meta"]["project"] == STALE_PROJECT)
    to_store(pl, db)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sm, "DB_PATH", str(db))
        mp.setitem(sys.modules, "recalc", None)
        st = sm.Store(str(db))
        try:
            bw.build_workbook(st, d / "stale_audit.xlsx", asof=pd.Timestamp(pl["meta"]["asof"]),
                              only=[STALE_PROJECT], reader_path=d / "stale_reader.xlsx")
        finally:
            st.close()


def plain_build(tmp_path_factory, name: str) -> dict:
    """The plain build of the 15 dumps: a fresh copy of its files in a directory of the caller's own, and the record.
    Keys: dir, audit, reader, db, asof, stats, bad_prints, groups, headlines, trajectory, locations, built_fresh."""
    key = code_key()
    hit = _CACHE.get(key)
    fresh = hit is None
    if fresh:
        master = tmp_path_factory.mktemp("shared_plain_build")
        rec = _build(master)
        hit = _CACHE[key] = {"master": master, "rec": rec, "hashes": _hash_dir(master)}
    d = tmp_path_factory.mktemp(name)
    for p in hit["master"].iterdir():
        if p.is_file():
            shutil.copy2(p, d / p.name)
    out = copy.deepcopy(hit["rec"])
    out.update({"dir": d, "audit": d / "audit.xlsx", "reader": d / "reader.xlsx", "db": d / "metrics.db",
                "built_fresh": fresh})
    if os.environ.get(STALE_ENV) == "1":
        _stale_build(tmp_path_factory.mktemp(f"{name}_stale"))
    return out


def verify_unchanged() -> list[str]:
    """Master copies whose files changed after their build (a test wrote into the shared copy): [] when none."""
    bad = []
    for key, c in _CACHE.items():
        now = _hash_dir(c["master"]) if c["master"].exists() else {}
        if now != c["hashes"]:
            changed = sorted(n for n in set(now) | set(c["hashes"]) if now.get(n) != c["hashes"].get(n))
            bad.append(f"shared build {key[:12]}: master copy changed after the build: {', '.join(changed)}")
    return bad
