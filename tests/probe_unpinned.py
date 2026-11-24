"""probe_unpinned.py — a test starts from the UNPINNED probe wiring (R21 gate, 2026-11-25).

A shared dump build (project_freeze / shared_build) calls config.pin_frozen_probe, which wires the probed explorer
scans (Base, HyperEVM, BSC ...) into Pendle's and Fluid's config for the rest of the process. A test that reads the
wiring as it is with no explorer_routes.json on file then fails or passes by which file ran before it on the same
worker. `unpinned(monkeypatch)` gives the test copies of those projects with every probed scan taken out (and its leg
of Fluid's (b) and Pendle's emissions), restored by monkeypatch when the test ends."""
from __future__ import annotations

import copy


def unpinned(monkeypatch) -> None:
    import config
    keys = {k[3] for k in config.EXPLORER_PROBED_SCANS}
    metrics = {k[4] for k in config.EXPLORER_PROBED_SCANS}
    for name in {k[1] for k in config.EXPLORER_PROBED_SCANS}:
        p = copy.deepcopy(config.PROJECT_BY_NAME[name])
        if p.get("log_scans") is not None:
            p["log_scans"] = [sp for sp in p["log_scans"] if sp.get("key") not in keys]
        if p.get("emissions_from_metric") is not None:
            p["emissions_from_metric"] = tuple(m for m in p["emissions_from_metric"] if m not in metrics)
        monkeypatch.setitem(config.PROJECT_BY_NAME, name, p)
    b = config.OUTFLOW_CATEGORIES["Fluid"]["b"]
    monkeypatch.setitem(config.OUTFLOW_CATEGORIES["Fluid"], "b", tuple(m for m in b if m not in metrics))
