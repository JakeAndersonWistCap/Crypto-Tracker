#!/usr/bin/env python3
"""
credibility_report.py — the Credibility tab, printed (Jake, 2026-10-05).

    python credibility_report.py                 every row, the counts per project, then every CHECK and
                                                 UNVERIFIABLE with what would resolve it
    python credibility_report.py --project Sky   one project
    python credibility_report.py --open-only     only the CHECK / UNVERIFIABLE list

Builds the workbook from metrics.db into a temporary file and works out the Credibility tab's formulas
(the verdicts are formulas over the headline cells) IN PYTHON, with xlcalc.py — no LibreOffice or
Excel needed, so it runs as-is on Windows. xlcalc covers exactly the functions build_workbook.py
writes and names any it does not know instead of guessing; it agrees with LibreOffice on every formula
of a full workbook (tests/test_adapters.py). `--libreoffice` recalculates with LibreOffice instead.
Writes nothing to the store and leaves token_metrics.xlsx alone. Output is ASCII, for any console.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ASCII = str.maketrans({"—": "-", "–": "-", "…": "...", "→": "->", "±": "+/-",
                       "÷": "/", "×": "x", "“": '"', "”": '"', "‘": "'", "’": "'"})


def a(text) -> str:
    return str("" if text is None else text).translate(ASCII).encode("ascii", "replace").decode("ascii")


def fmt(v) -> str:
    if isinstance(v, (int, float)):
        x = abs(v)
        if x >= 1e9:
            return f"{v / 1e9:,.3f}bn"
        if x >= 1e6:
            return f"{v / 1e6:,.3f}M"
        if x >= 1e3:
            return f"{v:,.0f}"
        return f"{v:.4g}"
    return a(v or "")


def read_tab(path: Path, evaluate: bool = True) -> tuple[list, list]:
    """(summary rows, data rows) of the Credibility tab. evaluate=True works every formula out in
    Python; False reads the values a recalculation (LibreOffice / Excel) saved in the file."""
    import openpyxl
    if evaluate:
        import xlcalc
        wb = openpyxl.load_workbook(path)
        missing = xlcalc.unsupported(wb)
        if missing:
            raise SystemExit("the workbook uses formula functions xlcalc.py does not evaluate: "
                             + "; ".join(f"{k} (e.g. {', '.join(v)})" for k, v in missing.items())
                             + " - run with --libreoffice, or extend xlcalc.py")
        X = xlcalc.Workbook(wb)

        def get(r, c):
            return xlcalc.display(X.value("Credibility", r, c))
        ws = wb["Credibility"]
    else:
        ws = openpyxl.load_workbook(path, data_only=True)["Credibility"]

        def get(r, c):
            return ws.cell(r, c).value
    head = next(r for r in range(1, 60) if ws.cell(r, 1).value == "Project" and ws.cell(r, 2).value == "Tab")
    summary = [[get(r, c) for c in range(1, 8)] for r in range(5, head - 1) if ws.cell(r, 1).value]
    rows = [[get(r, c) for c in range(1, 13)] for r in range(head + 1, ws.max_row + 1) if ws.cell(r, 1).value]
    return summary, rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project")
    ap.add_argument("--open-only", action="store_true")
    ap.add_argument("--libreoffice", action="store_true",
                    help="recalculate with LibreOffice instead of evaluating the formulas in Python")
    args = ap.parse_args(argv)
    import store as store_mod
    from build_workbook import build_workbook
    if not Path(store_mod.DB_PATH).exists():
        print(f"no {store_mod.DB_PATH} — run token_metrics.py first")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "credibility.xlsx"
        st = store_mod.Store(store_mod.DB_PATH)
        try:
            build_workbook(st, path)
        finally:
            st.close()
        if args.libreoffice:
            from recalc import recalc
            res = recalc(str(path), timeout=180)
            if isinstance(res, dict) and res.get("error"):
                print(f"recalculation failed: {a(res['error'])} - run without --libreoffice to evaluate in Python")
                return 1
        summary, rows = read_tab(path, evaluate=not args.libreoffice)
    if args.project:
        rows = [r for r in rows if str(r[0]).lower() == args.project.lower()]
        summary = [s for s in summary if str(s[0]).lower() in (args.project.lower(), "all")]
    print("CREDIBILITY - counts by verdict")
    print(f"  {'Project':<13}{'PASS':>6}{'CHECK':>7}{'FRESH':>7}{'UNVER':>7}{'N/A':>6}{'Rows':>6}")
    for s in summary:
        print(f"  {a(s[0]):<13}" + "".join(f"{int(x or 0):>{w}}" for x, w in zip(s[1:], (6, 7, 7, 7, 6, 6))))
    if not args.open_only:
        print("\nCREDIBILITY - every row (project | cell | what | ours | reference | gap | tol | verdict)")
        for r in rows:
            gap = f"{r[8] * 100:+.1f}%" if isinstance(r[8], (int, float)) else ""
            tol = f"+/-{r[9] * 100:g}%" if isinstance(r[9], (int, float)) else ""
            print(f"  {a(r[0]):<12}| {a(r[2])[:28]:<28}| {a(r[3])[:58]:<58}| {fmt(r[4]):>14} | "
                  f"{fmt(r[6]):>14} {a(r[7] or '')[:10]:<10}| {gap:>8} | {tol:>7} | {a(r[10])}")
    print("\nCREDIBILITY - every CHECK and UNVERIFIABLE, with what would resolve it")
    for r in rows:
        v = a(r[10])
        if v.startswith("CHECK") or v.startswith("UNVERIFIABLE"):
            print(f"  [{v}] {a(r[0])} - {a(r[3])[:90]}\n      source: {a(r[5])[:160]}\n      {a(r[11])[:600]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
