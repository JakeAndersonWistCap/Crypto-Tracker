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
    st = sm.Store(db)
    audit, reader = Path(out_dir) / "audit.xlsx", Path(out_dir) / "reader.xlsx"
    try:
        bw.build_workbook(st, audit, asof=pd.Timestamp(asof), reader_path=reader)
    finally:
        st.close()
    return audit, reader


def main(argv=None) -> int:
    import progress
    progress.line_buffered()
    import argparse
    import config
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--compare", metavar="PROJECT")
    g.add_argument("--write", metavar="PROJECT")
    a = ap.parse_args(argv)
    project = a.compare or a.write
    if project not in config.FROZEN_PROJECTS:
        print(f"{project} is not frozen (config.FROZEN_PROJECTS: {', '.join(config.FROZEN_PROJECTS)})")
        return 2
    with tempfile.TemporaryDirectory(prefix="freeze_") as d:
        print("building the 15 dumps (about 6 minutes) ...")
        now = cells(*_build(d), project)
    path = baseline_path(project)
    if a.write:
        path.write_text(json.dumps(now, indent=0, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {len(now)} cells to {path.relative_to(ROOT)}")
        return 0
    base = json.loads(path.read_text(encoding="utf-8"))
    bad = undeclared(base, now, config.FROZEN_MOVES.get(project, {}))
    print(f"{project}: {len(now)} cells, {len(bad)} undeclared move(s)")
    for line in bad:
        print("  " + line)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
