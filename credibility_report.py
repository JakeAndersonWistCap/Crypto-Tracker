#!/usr/bin/env python3
"""
credibility_report.py — the Credibility tab, printed (Jake, 2026-10-05).

    python credibility_report.py                 every row, the counts per project, then every CHECK and
                                                 UNVERIFIABLE with what would resolve it
    python credibility_report.py --project Sky   one project
    python credibility_report.py --open-only     only the CHECK / UNVERIFIABLE list
    python credibility_report.py --roots         only the ROOT-CAUSE MAP: every open row traced to the root
                                                 input(s) holding it open, ranked by headline rows blocked

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
    summary = [[get(r, c) for c in range(1, 13)] for r in range(5, head - 1) if ws.cell(r, 1).value]
    rows = [[get(r, c) for c in range(1, 13)] for r in range(head + 1, ws.max_row + 1) if ws.cell(r, 1).value]
    return summary, rows


def evaluate(project: str | None = None, libreoffice: bool = False):
    """(credibility rows, evaluated tab rows) from metrics.db — or (None, None) when there is no store."""
    import store as store_mod
    from build_workbook import CREDIBILITY_ROWS, build_workbook
    if not Path(store_mod.DB_PATH).exists():
        print(f"no {store_mod.DB_PATH} — run token_metrics.py first")
        return None, None
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "credibility.xlsx"
        st = store_mod.Store(store_mod.DB_PATH)
        try:
            build_workbook(st, path)
        finally:
            st.close()
        _summary, tab = read_tab(path, evaluate=not libreoffice)
    rows = list(CREDIBILITY_ROWS)
    if len(rows) != len(tab):
        print(f"the build kept {len(rows)} rows, the tab has {len(tab)} — cannot pair them")
        return None, None
    if project:
        keep = [i for i, r in enumerate(rows) if r["project"].lower() == project.lower()]
        tab = [t if i in keep else t[:10] + [""] + t[11:] for i, t in enumerate(tab)]
    return rows, tab


def print_roots(roots: list[dict]) -> None:
    """The root-cause map: each root, the headline rows it holds open, why, and the fix class; then the
    same roots grouped by input type across projects (one code fix often clears a type everywhere)."""
    print("\nROOT-CAUSE MAP - every CHECK / UNVERIFIABLE traced to the root input(s) holding it open")
    print("  (headline = A1-A4 rows blocked; rows = every open row incl. inputs; ranked by headline)")
    print(f"  {'#':>3} {'hdl':>4} {'rows':>4}  root | verdict | why | fix")
    for k, d in enumerate(roots, 1):
        print(f"  {k:>3} {d['headline_rows']:>4} {d['rows']:>4}  {a(d['root'])[:90]} | {a(d['verdict'])}\n"
              f"             why: {a(d['why'])[:200]}\n             fix: {a(d['fix'])[:200]}\n"
              f"             blocks: {a(', '.join(d['blocks']))[:400]}")
    by: dict = {}
    for d in roots:
        parts = d["root"].split(" | ")
        kind = parts[1] if len(parts) > 2 else parts[-1]
        e = by.setdefault((kind, d["fix"].split(":")[0]), [0, 0, set()])
        e[0] += d["headline_rows"]
        e[1] += 1
        e[2].add(parts[0])
    print("\nROOT-CAUSE MAP - grouped by input type across projects (headline rows blocked | roots | fix class)")
    for (kind, fix), (h, n, projs) in sorted(by.items(), key=lambda x: -x[1][0]):
        print(f"  {h:>4} {n:>3}  {a(kind)[:44]:<44} {a(fix)[:28]:<28} {a(', '.join(sorted(projs)))[:120]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project")
    ap.add_argument("--open-only", action="store_true")
    ap.add_argument("--roots", action="store_true", help="only the counts and the root-cause map")
    ap.add_argument("--libreoffice", action="store_true",
                    help="recalculate with LibreOffice instead of evaluating the formulas in Python")
    args = ap.parse_args(argv)
    import store as store_mod
    from build_workbook import CREDIBILITY_ROWS, build_workbook
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
    import credibility
    if len(CREDIBILITY_ROWS) != len(rows):
        print(f"root-cause map unavailable: the build kept {len(CREDIBILITY_ROWS)} rows, the tab has {len(rows)}")
        roots = None
    else:
        keep = [i for i, r in enumerate(rows) if not args.project or str(r[0]).lower() == args.project.lower()]
        roots = credibility.root_causes(CREDIBILITY_ROWS, [r[10] if i in keep else "" for i, r in enumerate(rows)])
    if args.project:
        rows = [r for r in rows if str(r[0]).lower() == args.project.lower()]
        summary = [s for s in summary if str(s[0]).lower() in (args.project.lower(), "all")]
    print("CREDIBILITY - counts by verdict, and SIGN-OFF (PASS / N/A / LIMITATION / MATURING / FINDING = signed)")
    print(f"  {'Project':<13}{'PASS':>6}{'CHECK':>7}{'FRESH':>7}{'UNVER':>7}{'N/A':>6}{'Rows':>6}"
          f"{'LIMIT':>7}{'MATUR':>7}{'FIND':>6}{'OPEN':>6}  SIGN-OFF")
    for s in summary:
        s = list(s) + [None] * (12 - len(s))
        print(f"  {a(s[0]):<13}" + "".join(f"{int(x or 0):>{w}}" for x, w in
                                            zip(s[1:11], (6, 7, 7, 7, 6, 6, 7, 7, 6, 6))) + f"  {a(s[11])}")
    if roots is not None:
        print_roots(roots)
    if args.roots:
        return 0
    if not args.open_only:
        print("\nCREDIBILITY - every row (project | cell | what | ours | reference | gap | tol | verdict)")
        for r in rows:
            gap = f"{r[8] * 100:+.1f}%" if isinstance(r[8], (int, float)) else ""
            tol = f"+/-{r[9] * 100:g}%" if isinstance(r[9], (int, float)) else ""
            print(f"  {a(r[0]):<12}| {a(r[2])[:28]:<28}| {a(r[3])[:58]:<58}| {fmt(r[4]):>14} | "
                  f"{fmt(r[6]):>14} {a(r[7] or '')[:10]:<10}| {gap:>8} | {tol:>7} | {a(r[10])}")
    print("\nCREDIBILITY - every OPEN row (not PASS / N/A / LIMITATION / MATURING / FINDING), with what would resolve it")
    for r in rows:
        v = a(r[10])
        if not credibility.signed(v):
            print(f"  [{v}] {a(r[0])} - {a(r[3])[:90]}\n      source: {a(r[5])[:160]}\n      {a(r[11])[:600]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
