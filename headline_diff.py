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
    p5_1_13_spans  2026-11-04: a rederived delta across missed reads counts every day since its stock's previous
                   read (covered days); before = one day a row
    p5_1c_b1_one_offs  2026-11-04: one-off releases and burns stay in every rate and trend (Uniswap's 100M burn of
                   2025-12-27 stays in gross_burn_tokens); before = moved out of the series at read time

    --cells all    every numeric cell on the A1-A4, CP and Master tabs, not only the A1-A4 headlines
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
    # P5-1.13 (2026-11-04): a rederived delta across missed reads counts every day since its stock's previous read;
    # before = one day a row
    "p5_1_13_spans": lambda: [(config.REDERIVED_SPAN_FROM_STOCK, "on", False)],
    # P5-1C.B1 (2026-11-04): one-off releases and burns stay in every rate and trend (Uniswap's 100M burn of 2025-12-27
    # no longer moved out of gross_burn_tokens); before = the one-off moved out of the series at read time
    "p5_1c_b1_one_offs": lambda: [(config.ONE_OFFS_IN_RATES, "on", False)],
}
ALL_CELL_TABS = ("A1", "A2", "A3", "A4", "CP", "Master")   # --cells all: every numeric cell on these tabs


def _all_cells(X, wb) -> dict:
    """{(project, sheet, header): value} for every cell on ALL_CELL_TABS whose row names a project (column A), under the
    tab's header row - the row whose column A reads "Project" (Jake 2026-11-04: rows 1-6 were read as headers before, so
    a data row's text could replace a header and columns sharing that text overwrote each other). A header used twice
    keeps its column letter. Evaluated (xlcalc)."""
    import xlcalc
    from openpyxl.utils import get_column_letter
    out = {}
    for ws in wb.worksheets:
        if not ws.title.startswith(ALL_CELL_TABS):
            continue
        hr = next((r for r in range(1, min(ws.max_row, 12) + 1)
                   if str(ws.cell(r, 1).value or "").strip().lower() == "project"), None)
        if hr is None:
            continue
        heads, seen = {}, {}
        for c in range(2, ws.max_column + 1):
            v = ws.cell(hr, c).value
            if isinstance(v, str) and v.strip() and not v.startswith("="):
                seen[v.strip()] = seen.get(v.strip(), 0) + 1
                heads[c] = v.strip()
        heads = {c: (h if seen[h] == 1 else f"{h} [{get_column_letter(c)}]") for c, h in heads.items()}
        for r in range(hr + 1, ws.max_row + 1):
            proj = ws.cell(r, 1).value
            if not isinstance(proj, str) or not proj.strip() or proj.startswith("="):
                continue
            for c in heads:
                if ws.cell(r, c).value is None:
                    continue
                try:
                    v = xlcalc.display(X.value(ws.title, r, c))
                except Exception:  # noqa: BLE001 - a cell xlcalc cannot evaluate is left out, never guessed
                    continue
                out[(proj.strip(), ws.title, heads[c])] = v
    return out


def _headlines(store_mod, build_workbook, cells_ref, path: Path, all_cells: bool = False, asof=None) -> dict:
    import openpyxl
    from openpyxl.utils.cell import column_index_from_string, coordinate_from_string

    import xlcalc
    st = store_mod.Store(store_mod.DB_PATH)
    try:
        build_workbook(st, path, **({"asof": asof} if asof is not None else {}))
    finally:
        st.close()
    cells = copy.deepcopy(list(cells_ref))
    wb = openpyxl.load_workbook(path)
    X = xlcalc.Workbook(wb)
    if all_cells:
        return _all_cells(X, wb)
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
    ap.add_argument("--cells", choices=("headlines", "all"), default="headlines",
                    help="all = every numeric cell on A1-A4, CP and Master")
    ap.add_argument("--asof", default=None, help="build as of this day (default: today; the test dumps: 2026-10-13)")
    args = ap.parse_args(argv)
    import store as store_mod
    from build_workbook import _HEADLINE_CELLS, build_workbook
    from credibility_report import a, fmt
    if not Path(store_mod.DB_PATH).exists():
        print(f"no {store_mod.DB_PATH} — run token_metrics.py first")
        return 1
    which = args.only or sorted(BEFORE)
    import pandas as pd                                # noqa: PLC0415
    asof = pd.Timestamp(args.asof) if args.asof else None   # a store that stops earlier (the test dumps: their asof)
    changes = [c for k in which for c in BEFORE[k]()]
    saved = [(d, k, d.get(k, KeyError)) for d, k, _v in changes]
    with tempfile.TemporaryDirectory() as tmp:
        for d, k, v in changes:
            if v is None:
                d.pop(k, None)
            else:
                d[k] = v
        try:
            before = _headlines(store_mod, build_workbook, _HEADLINE_CELLS, Path(tmp) / "before.xlsx",
                                args.cells == "all", asof)
        finally:
            for d, k, v in saved:
                if v is KeyError:
                    d.pop(k, None)
                else:
                    d[k] = v
        after = _headlines(store_mod, build_workbook, _HEADLINE_CELLS, Path(tmp) / "after.xlsx", args.cells == "all",
                           asof)
    moved = []
    for key, v1 in after.items():
        v0 = before.get(key)
        if isinstance(v0, (int, float)) and isinstance(v1, (int, float)):
            if v0 == v1:
                continue
            rel = (v1 - v0) / abs(v0) if v0 else float("inf")
            if abs(rel) * 100 > args.threshold or (args.threshold == 0 and v0 != v1):
                moved.append((key, v0, v1, rel))
        elif v0 != v1 and (isinstance(v0, (int, float)) or isinstance(v1, (int, float))):
            moved.append((key, v0, v1, None))
    print(f"HEADLINES MOVING BEYOND {args.threshold:g}% — changes: {', '.join(which)}"
          + (f" — built as of {asof.date()}" if asof is not None else ""))
    for (proj, sheet, head), v0, v1, rel in sorted(moved, key=lambda m: (m[0][0], m[0][1])):
        pct = f"{rel * 100:+.1f}%" if rel is not None and rel != float("inf") else "(new / gone)"
        print(f"  {a(proj):<11} {a(sheet):<4} {a(head)[:70]:<70} {fmt(v0):>14} -> {fmt(v1):>14}  {pct}")
    if not moved:
        print("  none")
    return 0


if __name__ == "__main__":
    import progress                                    # stdout line-buffered when piped (2026-10-11)
    progress.line_buffered()
    sys.exit(main())
