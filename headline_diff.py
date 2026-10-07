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
    circulating_policy  2026-10-07: the project's own figure first, CoinGecko where it publishes nothing — Uniswap
                   and Sky to CoinGecko, Aerodrome to CoinGecko + veAERO added back, GEODNET to its own 462M; before =
                   the 2026-10-06 choices (on-chain for Uniswap / Sky / Aerodrome, CoinGecko for GEODNET)
    geodnet_onchain  2026-10-07 evening: GEODNET's on-chain set primary; before = Blockworks' static 462M (labelled)
    onchain_sweep  2026-10-07 full sweep: the on-chain count from the projects' own wallet lists primary for
                   Uniswap (+ its docs' vesting contracts and merkle distributor), Sky and Ether.fi (its Blockworks
                   filing's wallets); before = CoinGecko for all three (the policy round's choices)
    chainlink_onchain_preview  NOT a change made — a PREVIEW: BEFORE = Chainlink's on-chain set (27 wallets of its
                   2022 post + the Reserve) as primary, AFTER = CoinGecko (as configured). Read the move with the
                   sign reversed: it is what switching Chainlink to on-chain would do
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
    "circulating_policy": lambda: [
        (config.CIRCULATING_ONCHAIN["Uniswap"], "ratios_use", None),
        (config.CIRCULATING_ONCHAIN["Sky"], "ratios_use", None),
        (config.CIRCULATING_ONCHAIN["Aerodrome"], "ratios_use", "onchain"),
        (config.CIRCULATING_ONCHAIN["GEODNET"], "ratios_use", None)],
    # 2026-10-07 evening: GEODNET's on-chain set primary (Solana side resolved); before = Blockworks' static 462M
    "geodnet_onchain": lambda: [(config.CIRCULATING_ONCHAIN["GEODNET"], "ratios_use", "third_party_reference"),
                                (config.CIRCULATING_ONCHAIN["GEODNET"], "status", "partial")],
    # 2026-10-07 full sweep: the projects' own wallet lists make the on-chain count primary; before = CoinGecko
    "onchain_sweep": lambda: [
        (config.CIRCULATING_ONCHAIN["Uniswap"], "ratios_use", "coingecko"),
        (config.CIRCULATING_ONCHAIN["Uniswap"], "subtract", ("burn_address_balance", "treasury_holding_tokens")),
        (config.CIRCULATING_ONCHAIN["Sky"], "ratios_use", "coingecko"),
        (config.CIRCULATING_ONCHAIN["Ether.fi"], "ratios_use", None),
        (config.CIRCULATING_ONCHAIN["Ether.fi"], "status", "partial"),
        (config.CIRCULATING_ONCHAIN["Ether.fi"], "subtract", ("treasury_holding_tokens",))],
    # a PREVIEW, not a change: BEFORE = Chainlink on-chain primary, AFTER = CoinGecko as configured
    "chainlink_onchain_preview": lambda: [(config.CIRCULATING_ONCHAIN["Chainlink"], "ratios_use", "onchain")],
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
