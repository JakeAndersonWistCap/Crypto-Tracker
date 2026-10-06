#!/usr/bin/env python3
"""
headline_diff.py — which A1-A4 headlines move, and by how much, because of a decision (Jake, 2026-10-06: "Report
every headline that moves by more than 10%").

    python headline_diff.py                    every change below, against metrics.db, moves beyond 10%
    python headline_diff.py --only circulating --threshold 5

Builds the workbook twice from the SAME store into temporary files — once with the previous decision restored
in memory (BEFORE), once as configured (AFTER) — and evaluates every headline cell in Python (xlcalc.py).
Writes nothing to the store and leaves token_metrics.xlsx alone. Output is ASCII.

    circulating    2026-10-06: Uniswap / Sky / Pendle on-chain circulating (established), Aerodrome on-chain by
                   decision; before = CoinGecko's circulating for all four
    etherfi_yield  2026-10-06: Ether.fi's token-yield numerator = the reconciled share-price total; before = the
                   top-ups alone (sethfi_topup_tokens)
"""
from __future__ import annotations

import argparse
import copy
import sys
import tempfile
from pathlib import Path

import config

BEFORE = {
    "circulating": lambda: [
        *[(config.CIRCULATING_ONCHAIN[n], "status", "partial") for n in ("Uniswap", "Sky", "Pendle")],
        (config.CIRCULATING_ONCHAIN["Aerodrome"], "ratios_use", None)],
    "etherfi_yield": lambda: [(config.PROTOCOL_YIELD["Ether.fi"]["token_yield"], "tokens", "sethfi_topup_tokens")],
}


def _headlines(store_mod, build_workbook, cells_ref, path: Path) -> dict:
    import openpyxl
    from openpyxl.utils.cell import column_index_from_string, coordinate_from_string

    import xlcalc
    st = store_mod.Store(store_mod.DB_PATH)
    try:
        build_workbook(st, path)
    finally:
        st.close()
    cells = copy.deepcopy(list(cells_ref))
    X = xlcalc.Workbook(openpyxl.load_workbook(path))
    out = {}
    for hc in cells:
        col, row = coordinate_from_string(hc["cell"])
        v = xlcalc.display(X.value(hc["sheet"], row, column_index_from_string(col)))
        out[(hc["project"], hc["sheet"], hc["header"])] = v
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=sorted(BEFORE), action="append")
    ap.add_argument("--threshold", type=float, default=10.0, help="percent (default 10)")
    args = ap.parse_args(argv)
    import store as store_mod
    from build_workbook import _HEADLINE_CELLS, build_workbook
    from credibility_report import a, fmt
    if not Path(store_mod.DB_PATH).exists():
        print(f"no {store_mod.DB_PATH} — run token_metrics.py first")
        return 1
    which = args.only or sorted(BEFORE)
    changes = [c for k in which for c in BEFORE[k]()]
    saved = [(d, k, d.get(k, KeyError)) for d, k, _v in changes]
    with tempfile.TemporaryDirectory() as tmp:
        for d, k, v in changes:
            if v is None:
                d.pop(k, None)
            else:
                d[k] = v
        try:
            before = _headlines(store_mod, build_workbook, _HEADLINE_CELLS, Path(tmp) / "before.xlsx")
        finally:
            for d, k, v in saved:
                if v is KeyError:
                    d.pop(k, None)
                else:
                    d[k] = v
        after = _headlines(store_mod, build_workbook, _HEADLINE_CELLS, Path(tmp) / "after.xlsx")
    moved = []
    for key, v1 in after.items():
        v0 = before.get(key)
        if isinstance(v0, (int, float)) and isinstance(v1, (int, float)):
            if v0 == v1:
                continue
            rel = (v1 - v0) / abs(v0) if v0 else float("inf")
            if abs(rel) * 100 > args.threshold:
                moved.append((key, v0, v1, rel))
        elif v0 != v1 and (isinstance(v0, (int, float)) or isinstance(v1, (int, float))):
            moved.append((key, v0, v1, None))
    print(f"HEADLINES MOVING BEYOND {args.threshold:g}% — changes: {', '.join(which)}")
    for (proj, sheet, head), v0, v1, rel in sorted(moved, key=lambda m: (m[0][0], m[0][1])):
        pct = f"{rel * 100:+.1f}%" if rel is not None and rel != float("inf") else "(new / gone)"
        print(f"  {a(proj):<11} {a(sheet):<4} {a(head)[:70]:<70} {fmt(v0):>14} -> {fmt(v1):>14}  {pct}")
    if not moved:
        print("  none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
