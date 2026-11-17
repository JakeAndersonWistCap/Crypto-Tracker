"""PROJECT FREEZE (Jake 2026-11-05, project round 1: "When done, freeze Uniswap's cells as a baseline snapshot: from then
on the gate fails if any Uniswap cell moves without a declared reason").

A finished project's every cell on the 15-dump build (as of the newest dump) is committed as a baseline:
    - the audit file's A1–A4 / CP / Master cells on the project's row (headline_diff._all_cells), and
    - the reader's Summary, archetype, Comparison and Business cells on the project's row (READER_TABS),
each value as the workbook shows it (numbers to 9 significant digits, text as written). The gate test
(tests/test_pr1_freeze.py) rebuilds the same workbook and fails on any cell that moved, appeared or disappeared unless
config.FROZEN_MOVES[project] declares it — the cell's key, the value it now reads, why and when. A declared move is
then written into the baseline with --write, the declaration staying in config as the record.

    python project_freeze.py --compare Uniswap      # read-only: list every cell that moved against the baseline
    python project_freeze.py --write Uniswap        # rewrite the baseline (only after declaring every move)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
READER_TABS = ("Summary", "A1 ", "A2 ", "A3 ", "A4 ", "Comparison", "Business")


def baseline_path(project: str) -> Path:
    import config
    return ROOT / config.FROZEN_PROJECTS[project]["baseline"]


def _norm(v):
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return float(f"{float(v):.9g}")
    return str(v)


def _reader_cells(rd, project: str) -> dict:
    import xlcalc
    X = xlcalc.Workbook(rd)
    out = {}
    for ws in rd.worksheets:
        if not ws.title.startswith(READER_TABS):
            continue
        hr = next((r for r in range(1, min(ws.max_row, 12) + 1)
                   if str(ws.cell(r, 1).value or "").strip() == "Project"), None)
        if hr is None:
            continue
        heads, seen = {}, {}
        for c in range(2, ws.max_column + 1):
            h = str(ws.cell(hr, c).value or "").strip()
            if h:
                seen[h] = seen.get(h, 0) + 1
                heads[c] = h
        for r in range(hr + 1, ws.max_row + 1):
            if str(ws.cell(r, 1).value or "").strip() != project:
                continue
            for c, h in heads.items():
                if ws.cell(r, c).value is None:
                    continue
                key = f"reader|{ws.title}|{h}" + (f" [{c}]" if seen[h] > 1 else "")
                try:
                    out[key] = _norm(xlcalc.display(X.value(ws.title, r, c)))
                except Exception as e:  # noqa: BLE001 — an unevaluable cell is frozen as what it says
                    out[key] = f"#ERROR {type(e).__name__}"
    return out


def cells(audit_path, reader_path, project: str) -> dict:
    """{key: value} — every cell of `project` on the audit's A-tabs / CP / Master and the reader's tabs."""
    import openpyxl
    import xlcalc
    import headline_diff as hd
    wb = openpyxl.load_workbook(audit_path)
    out = {f"audit|{s}|{h}": _norm(v) for (p, s, h), v in hd._all_cells(xlcalc.Workbook(wb), wb).items()
           if p == project}
    out.update(_reader_cells(openpyxl.load_workbook(reader_path), project))
    return out


def undeclared(base: dict, now: dict, declared: dict) -> list[str]:
    """Every cell that moved, appeared or disappeared and is not declared with the value it now reads."""
    bad = []
    for k in sorted(set(base) | set(now)):
        b, n = base.get(k, "<absent>"), now.get(k, "<absent>")
        if b == n:
            continue
        d = declared.get(k)
        if d is not None and d.get("then"):               # R7D.A1: a later declared move of the same cell — the
            d = d["then"][-1]                             # earlier declaration stays on record; the latest is judged
        if d is not None and _norm(d.get("to")) == n and d.get("why") and d.get("date"):
            continue
        bad.append(f"{k}: {b!r} -> {n!r}")
    return bad


def _build(out_dir: str) -> tuple[Path, Path]:
    sys.path.insert(0, str(ROOT / "tests"))
    sys.modules["recalc"] = None
    os.environ["TOKEN_METRICS_STORE_VALUES"] = "1"
    import pandas as pd
    import store as sm
    import measuring_guard as mg
    from real_dump import to_store
    db = os.path.join(out_dir, "metrics.db")
    asof = None
    for pl in mg._payloads():
        to_store(pl, db)
        asof = max(asof or pl["meta"]["asof"], pl["meta"]["asof"])
    sm.DB_PATH = db
    import build_workbook as bw
    import config
    config.pin_frozen_probe()        # PR3B.1: Fluid's L2 scans wired as Jake's probe wires them, on every machine
    st = sm.Store(db)
    audit, reader = Path(out_dir) / "audit.xlsx", Path(out_dir) / "reader.xlsx"
    try:
        bw.build_workbook(st, audit, asof=pd.Timestamp(asof), reader_path=reader)
    finally:
        st.close()
    return audit, reader


def main(argv=None) -> int:
    import argparse
    import config
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    # R8.6 (Jake 2026-11-17): several projects in one call — the 15 dumps are built ONCE for all of them (each compare
    # rebuilt them, about 6 minutes each: 42 minutes for seven). `all` = every frozen project.
    g.add_argument("--compare", metavar="PROJECT", nargs="+", help="one or more frozen projects, or: all")
    g.add_argument("--write", metavar="PROJECT")
    a = ap.parse_args(argv)
    projects = [a.write] if a.write else list(a.compare)
    if projects == ["all"]:
        projects = sorted(config.FROZEN_PROJECTS)
    for project in projects:
        if project not in config.FROZEN_PROJECTS:
            print(f"{project} is not frozen (config.FROZEN_PROJECTS: {', '.join(config.FROZEN_PROJECTS)})")
            return 2
    with tempfile.TemporaryDirectory(prefix="freeze_") as d:
        print("building the 15 dumps (about 6 minutes) ...")
        audit, reader = _build(d)
        now = {project: cells(audit, reader, project) for project in projects}
    if a.write:
        path = baseline_path(a.write)
        path.write_text(json.dumps(now[a.write], indent=0, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {len(now[a.write])} cells to {path.relative_to(ROOT)}")
        return 0
    return report(now)


def report(now: dict) -> int:
    """Print one line per project (and its undeclared moves); 1 when any project has one."""
    import config
    code = 0
    for project, got in now.items():
        base = json.loads(baseline_path(project).read_text(encoding="utf-8"))
        bad = undeclared(base, got, config.FROZEN_MOVES.get(project, {}))
        print(f"{project}: {len(got)} cells, {len(bad)} undeclared move(s)")
        for line in bad:
            print("  " + line)
        code = code or (1 if bad else 0)
    return code


if __name__ == "__main__":
    import progress
    progress.line_buffered()
    sys.exit(main())
