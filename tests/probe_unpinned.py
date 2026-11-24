"""probe_unpinned.py — a test starts from the scan wiring config had when it was IMPORTED (R21 gate, 2026-11-25).

A shared dump build (project_freeze / shared_build) calls config.pin_frozen_probe, and some tests wire probed or
Alchemy-served scans; each leaves Pendle's, Fluid's and Morpho's log scans and emission legs changed for the rest of the
worker process. A test that reads that wiring (or builds on it, as the guard does) then passes or fails by which file ran
before it on the same worker. conftest.pytest_collection_finish takes `snapshot()` before any test runs;
`unpinned(monkeypatch)` puts those keys back on the ORIGINAL project dicts (a build iterates them through
build_workbook._SCOPE / config.PROJECTS, never a copy put in PROJECT_BY_NAME), restored by monkeypatch at the end."""
from __future__ import annotations

import copy

KEYS = ("log_scans", "emissions_from_metric")
_SNAP: dict | None = None


def snapshot() -> None:
    global _SNAP
    import config
    _SNAP = {"projects": {n: {k: copy.deepcopy(p[k]) for k in KEYS if k in p} for n, p in config.PROJECT_BY_NAME.items()},
             "fluid_b": tuple(config.OUTFLOW_CATEGORIES["Fluid"]["b"])}


def unpinned(monkeypatch) -> None:
    import config
    if _SNAP is None:                       # a run without conftest's snapshot: take the probed scans out instead
        keys = {k[3] for k in config.EXPLORER_PROBED_SCANS}
        metrics = {k[4] for k in config.EXPLORER_PROBED_SCANS}
        for name in {k[1] for k in config.EXPLORER_PROBED_SCANS}:
            p = config.PROJECT_BY_NAME[name]
            if p.get("log_scans") is not None:
                monkeypatch.setitem(p, "log_scans", [sp for sp in p["log_scans"] if sp.get("key") not in keys])
            if isinstance(p.get("emissions_from_metric"), (tuple, list)):
                monkeypatch.setitem(p, "emissions_from_metric",
                                    tuple(m for m in p["emissions_from_metric"] if m not in metrics))
        b = config.OUTFLOW_CATEGORIES["Fluid"]["b"]
        monkeypatch.setitem(config.OUTFLOW_CATEGORIES["Fluid"], "b", tuple(m for m in b if m not in metrics))
        return
    for name, p in config.PROJECT_BY_NAME.items():
        was = _SNAP["projects"].get(name, {})
        for k in KEYS:
            if k in was:
                if p.get(k) != was[k]:
                    monkeypatch.setitem(p, k, copy.deepcopy(was[k]))
            elif k in p:
                monkeypatch.delitem(p, k)
    monkeypatch.setitem(config.OUTFLOW_CATEGORIES["Fluid"], "b", _SNAP["fluid_b"])
