"""ROUND P1 STAGE 1 (Jake 2026-10-27): the reader workbook — no data changes.

token_metrics_audit.xlsx is the full build; token_metrics.xlsx (reader.py) shows About, Summary, A3 and Definitions,
every number a formula on a hidden `_data` sheet. Built once here on the committed real dumps (fixtures/real_*.json).
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import sys
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402
import reader  # noqa: E402

CODES = set(reader.CODES)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    import build_workbook as bw
    import measuring_guard as mg
    import store as sm
    from real_dump import to_store
    d = tmp_path_factory.mktemp("p1")
    db = d / "metrics.db"
    asofs = []
    for pl in mg._payloads():
        to_store(pl, db)
        asofs.append(pl["meta"]["asof"])
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sm, "DB_PATH", str(db))
        mp.setitem(sys.modules, "recalc", None)
        mp.setenv("TOKEN_METRICS_STORE_VALUES", "1")
        st = sm.Store(str(db))
        try:
            bw.build_workbook(st, d / "audit.xlsx", asof=pd.Timestamp(max(asofs)), reader_path=d / "reader.xlsx")
        finally:
            st.close()
    assert "error" not in bw.READER_STATS, bw.READER_STATS
    return {"dir": d, "audit": d / "audit.xlsx", "reader": d / "reader.xlsx", "stats": dict(bw.READER_STATS),
            "headlines": list(bw._HEADLINE_CELLS), "locations": dict(reader.HEADLINE_LOCATIONS),
            "asof": pd.Timestamp(max(asofs))}


# ================================================================================================== P1-1 / P1-3
def test_p1_1_two_files_the_reader_tabs_in_order_and_the_audit_file_whole(built):
    r = openpyxl.load_workbook(built["reader"])
    assert r.sheetnames == ["About", "Summary", "A1 Infrastructure", "A2 Coordination", "A3 Revenue Buyback",
                            "A4 Permanent Burn", "Comparison", "Business", "Charts", "Definitions", "_data", "_series"]
    assert r["_data"].sheet_state == "hidden"
    a = openpyxl.load_workbook(built["audit"], read_only=True)
    for tab in ("Master", "A1 Infrastructure", "A2 Coordination", "A3 Revenue Buyback", "A4 Permanent Burn",
                "CP Comparability", "Flows", "Credibility", "Trajectory", "Charts", "Config & Sources", "Gap Report",
                "Review Queue", "Staging", "Run Log", "Data", "Monthly", "Coverage"):
        assert tab in a.sheetnames, tab
    assert built["stats"]["failed"] == 0


def test_p1_1_a_reader_that_cannot_be_built_says_so_and_is_never_left_stale(tmp_path, monkeypatch):
    import build_workbook as bw
    monkeypatch.setattr(reader, "build_reader", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    bw.READER_STATS.clear()
    out = bw.write_reader(openpyxl.Workbook(), tmp_path / "r.xlsx", tmp_path / "audit.xlsx", pd.Timestamp("2026-10-27"))
    assert out["error"] == "RuntimeError: boom"
    ws = openpyxl.load_workbook(tmp_path / "r.xlsx").active
    assert ws.title == "READER NOT BUILT" and "every figure is on audit.xlsx" in ws["A1"].value


def test_p1_1_the_run_writes_the_reader_and_the_audit_file():
    import token_metrics as tm
    assert tm.WORKBOOK.name == "token_metrics.xlsx" and tm.AUDIT_WORKBOOK.name == "token_metrics_audit.xlsx"
    src = (ROOT / "token_metrics.py").read_text(encoding="utf-8")
    assert src.count("build_workbook(st, AUDIT_WORKBOOK") == 2 and src.count("reader_path=WORKBOOK") == 2


# ================================================================================================== P1-2
def _value_cells(ws, first_row=5):
    """(cell, header) for every body cell of a reader tab (group rows skipped)."""
    heads = {c: ws.cell(4, c).value for c in range(1, ws.max_column + 1)}
    for r in range(first_row, ws.max_row + 1):
        if ws.cell(r, 2).value is None:                  # an archetype group row
            continue
        for c in range(3, ws.max_column + 1):
            yield ws.cell(r, c), heads[c]


def test_p1_2_headers_are_short_plain_and_defined(built):
    r = openpyxl.load_workbook(built["reader"])
    defs = {(row[0].value, row[2].value) for row in r["Definitions"].iter_rows(min_row=4) if row[0].value}
    for tab in ("Summary", "A3 Revenue Buyback"):
        ws = r[tab]
        for c in range(1, ws.max_column + 1):
            h = ws.cell(4, c).value
            assert h and len(h) <= 35, (tab, h)
            assert not re.search(r"\bQ-?\d\b|\b[GDP]\d+\b|config|_usd|_tokens", h), (tab, h)
            assert ws.cell(4, c).comment is not None, (tab, h)
            assert (tab, h) in defs, (tab, h)


def test_p1_2_a_value_cell_is_a_number_or_one_short_code(built):
    r = openpyxl.load_workbook(built["reader"])
    text_cols = {"What changed"}
    for tab in ("Summary", "A3 Revenue Buyback"):
        ws = r[tab]
        for c, h in _value_cells(ws):
            v = c.value
            if v is None or h in text_cols or c.number_format == "@":
                continue
            if isinstance(v, str) and v.startswith("="):
                assert "_data!" in v or "_series!" in v, (tab, c.coordinate, v)
                continue
            if isinstance(v, (int, float)):
                continue
            assert v in CODES, (tab, c.coordinate, h, v)
            assert c.comment is not None and c.comment.text, (tab, c.coordinate)
            assert c.font.color.rgb.endswith(reader.CODES[v]), (tab, c.coordinate)


def test_p1_2_codes_and_plain_words():
    assert reader.code_of("BLOCKED — NEAR'S LOCKUP RULE CANNOT FORM") == "blocked"
    assert reader.code_of("MISSING [D — ROUTE NOT FILLED]") == "not wired"
    assert reader.code_of("none available") == "by design"
    assert reader.code_of("n/a") == "n/a" and reader.code_of("") == "n/a"
    s = reader.plain("Revenue Q0 (G13 6, Jake 2026-10-21) beside (config.CURRENT_EMISSION_RATE) the Q-1 figure")
    assert "Q0" not in s and "G13" not in s and "config" not in s
    assert "last 90 days" in s and "the 90 days before" in s


# ================================================================================================== P1-5
def test_p1_5_periods_are_like_for_like_windows_and_a_short_history_is_said():
    d = pd.date_range("2026-01-01", "2026-10-26")
    s = pd.Series(1.0, index=d)
    s.loc["2026-10-20":] = 2.0
    v, why = reader.revenue_growth(s, 7, pd.Timestamp("2026-10-27"))
    assert v == pytest.approx(14 / 7 - 1) and "2026-10-20..2026-10-26 against 2026-10-13..2026-10-19" in why
    v, why = reader.revenue_growth(s, 365, pd.Timestamp("2026-10-27"))
    assert v is None and "730 days" in why and "held from 2026-01-01" in why
    p = pd.Series([1.0, 1.5], index=pd.to_datetime(["2026-09-27", "2026-10-27"]))
    assert reader.price_change(p, 30, pd.Timestamp("2026-10-27"))[0] == pytest.approx(0.5)
    assert reader.price_change(p, 7, pd.Timestamp("2026-10-27"))[0] is None


def test_p1_5_the_selector_drives_the_period_columns_live_custom_included(built):
    """P2a (Jake 2026-10-27): the selector (1D … 3Y, custom) drives every growth and price-change column from the one
    cell — each a formula on the daily series in `_series`, equal to the Python definition for every period."""
    import xlcalc
    import store as sm
    r = openpyxl.load_workbook(built["reader"])
    ws = r["Summary"]
    assert ws["B2"].value == "90D" and ws.data_validations.dataValidation
    assert "custom" in ws.data_validations.dataValidation[0].formula1
    pcol = next(c for c in range(1, ws.max_column + 1) if ws.cell(4, c).value == "Price change, selected period")
    gcol = next(c for c in range(1, ws.max_column + 1) if ws.cell(4, c).value == "Revenue growth, selected period")
    rows = {ws.cell(rr, 1).value: rr for rr in range(5, ws.max_row + 1) if ws.cell(rr, 2).value}
    st = sm.Store(str(built["dir"] / "metrics.db")) if (built["dir"] / "metrics.db").exists() else None
    long = None
    if st is None:
        import measuring_guard as mg
        from real_dump import to_store
        db = built["dir"] / "series.db"
        for pl in mg._payloads():
            to_store(pl, db)
        st = sm.Store(str(db))
    try:
        long = st.load_long()
    finally:
        st.close()
    seen = 0
    for period, days in (("30D", 30), ("1Y", 365), ("custom", 45)):
        ws["B2"] = period
        ws["D2"] = 45
        X = xlcalc.Workbook(r)
        for name, rr in rows.items():
            s = long[(long.project == name) & (long.metric == "price_usd")]
            s = s.assign(date=pd.to_datetime(s.date).dt.normalize()).groupby("date")["value"].last().sort_index()
            want, _why = reader.price_change(s, days, built["asof"])
            got = X.value("Summary", rr, pcol)
            if want is None:
                assert got == "n/a", (period, name, got)
            else:
                assert got == pytest.approx(want, rel=1e-9), (period, name, got, want)
                seen += 1
            g = X.value("Summary", rr, gcol)
            assert isinstance(g, float) or g == "n/a", (period, name, g)
    assert seen > 0


def test_p1_6_the_a1_key_needs_two_years_and_says_so():
    d = pd.date_range("2025-10-27", "2026-10-27")
    g = {("X", "settlement_volume_365d_usd"): pd.DataFrame({"date": d, "value": 100.0}),
         ("X", "circulating_supply_used"): pd.DataFrame({"date": d, "value": 10.0})}
    v, why = reader.settlement_key(g, "X")
    assert v == pytest.approx(0.0) and "2025-10-27 to 2026-10-27" in why
    g[("X", "settlement_volume_365d_usd")] = pd.DataFrame({"date": d[100:], "value": 100.0})
    v, why = reader.settlement_key(g, "X")
    assert v is None and "two years of history" in why


# ================================================================================================== P1-6
def test_p1_6_each_group_is_ranked_by_its_key_and_the_unmeasured_rank_last(built):
    import xlcalc
    r = openpyxl.load_workbook(built["reader"])
    ws = r["Summary"]
    X = xlcalc.Workbook(r)
    heads = {ws.cell(4, c).value: c for c in range(1, ws.max_column + 1)}
    kc, rc = heads["Archetype key"], heads["Rank in archetype"]
    groups, cur = [], None
    for rr in range(5, ws.max_row + 1):
        if ws.cell(rr, 2).value is None and ws.cell(rr, 1).value:
            cur = []
            groups.append(cur)
            continue
        if ws.cell(rr, 2).value:
            cur.append((X.value("Summary", rr, kc), X.value("Summary", rr, rc), ws.cell(rr, rc).comment.text))
    assert len(groups) == 4
    for g in groups:
        g = [x for x in g if x[1] != "parked"]
        keys = [x[0] for x in g]
        nums = [k for k in keys if isinstance(k, float)]
        assert nums == sorted(nums, reverse=True)
        first_text = next((i for i, k in enumerate(keys) if not isinstance(k, float)), len(keys))
        assert [x[1] for x in g[:first_text]] == list(range(1, first_text + 1))
        assert all(not isinstance(k, float) for k in keys[first_text:]), "the unmeasured sort last"
        # P2a A1: no measured key reads "unranked", the reason in the comment
        assert all(x[1] == "unranked" and "Unranked" in x[2] for x in g[first_text:])


def test_p1_6_the_a3_key_is_the_dilution_ratio_without_its_unlock_leg(built):
    import build_workbook as bw
    import xlcalc
    a = openpyxl.load_workbook(built["audit"])
    X = xlcalc.Workbook(a)
    ws = a["A3 Revenue Buyback"]
    hdr = {ws.cell(4, c).value: c for c in range(1, ws.max_column + 1)}
    kc, dc, nc = hdr[bw.RETIRE_VS_EMISSIONS_HEAD], hdr[bw.RETIRE_VS_DILUTION_HEAD], hdr[bw.RETIRE_VS_DILUTION_NOW_HEAD]
    checked = 0
    for rr in range(5, ws.max_row + 1):
        name = ws.cell(rr, 1).value
        if name not in config.PROJECT_BY_NAME:
            continue
        k = X.value("A3 Revenue Buyback", rr, kc)
        ref = X.value("A3 Revenue Buyback", rr, nc if name in config.CURRENT_EMISSION_RATE else dc)
        if not isinstance(k, float) or not isinstance(ref, float):
            continue
        if config.unlock_stock_metrics(name):
            assert k >= ref - 1e-12, (name, k, ref)        # unlocks only add to the denominator the key drops
        else:
            assert k == pytest.approx(ref, rel=1e-9), name
        checked += 1
    assert checked >= 3


# ================================================================================================== P1-9
def test_p1_9_every_headline_shown_on_the_reader_equals_its_audit_value(built):
    import sanity
    import xlcalc
    a, r = openpyxl.load_workbook(built["audit"]), openpyxl.load_workbook(built["reader"])
    XA, XR = xlcalc.Workbook(a), xlcalc.Workbook(r)
    a3 = [h for h in built["headlines"] if h["sheet"] == "A3 Revenue Buyback"]
    assert a3 and all((h["sheet"], h["project"], h["header"]) in built["locations"] for h in a3)
    numbers = 0
    for h in a3:
        sheet, ref = built["locations"][(h["sheet"], h["project"], h["header"])]
        ca, cr = a[h["sheet"]][h["cell"]], r[sheet][ref]
        va = sanity.stated_number(XA.value(h["sheet"], ca.row, ca.column))
        vr = XR.value(sheet, cr.row, cr.column)
        if va is None:
            # P2a A2: an EMPTY audit cell reads a code too (by design / n/a with its reason), never a blank
            assert vr in CODES, (h["project"], h["header"], vr)
        else:
            assert vr == pytest.approx(float(va), rel=1e-12), (h["project"], h["header"])
            numbers += 1
    assert numbers >= 20
    # every other headline stays on the audit file at its own cell
    rest = [h for h in built["headlines"] if h["sheet"] != "A3 Revenue Buyback"]
    assert rest
    for h in rest:
        c = a[h["sheet"]][h["cell"]]
        assert a[h["sheet"]].cell(4, c.column).value == h["header"] and a[h["sheet"]].cell(c.row, 1).value == h["project"]


def test_p1_9_the_column_map_names_every_master_and_a3_column(built):
    import build_workbook as bw
    rows = list(csv.DictReader(open(built["dir"] / "reader_column_map.csv", encoding="utf-8")))
    a = openpyxl.load_workbook(built["audit"], read_only=True)
    for tab in ("Master", "A3 Revenue Buyback"):
        ws = a[tab]
        heads = [c.value for c in next(ws.iter_rows(min_row=4, max_row=4)) if isinstance(c.value, str)]
        mapped = {x["audit_header"] for x in rows if x["audit_tab"] == tab}
        assert set(heads) <= mapped, set(heads) - mapped
    key = [x for x in rows if x["audit_header"] == bw.RETIRE_VS_EMISSIONS_HEAD]
    assert key and key[0]["reader_tab"] == "A3 Revenue Buyback" and key[0]["reader_header"] == "Retired ÷ emissions (x)"
    others = {x["audit_tab"] for x in rows if x["audit_column"] == "(all)"}
    assert {"Data", "Run Log", "Coverage", "Credibility"} <= others


def test_p1_9_the_guard_reads_a_headline_at_its_new_location(tmp_path, monkeypatch):
    import measuring_guard as mg
    import xlcalc
    a, r = openpyxl.Workbook(), openpyxl.Workbook()
    a.active.title = r.active.title = "A3 Revenue Buyback"
    a.active["C5"], r.active["F9"] = 1.0, 2.0
    r.save(tmp_path / "reader.xlsx")
    monkeypatch.setattr(reader, "HEADLINE_LOCATIONS", {("A3 Revenue Buyback", "P", "H"): ("A3 Revenue Buyback", "F9")})
    value = mg.headline_reader(a, xlcalc.Workbook(a), tmp_path / "reader.xlsx")
    assert value({"sheet": "A3 Revenue Buyback", "project": "P", "header": "H", "cell": "C5"}) == 2.0
    assert value({"sheet": "A3 Revenue Buyback", "project": "Q", "header": "H", "cell": "C5"}) == 1.0


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_p1_1_libreoffice_agrees_with_every_stored_reader_value(built, tmp_path):
    import recalc_check
    shutil.copy(built["reader"], tmp_path / "reader.xlsx")
    out = recalc_check.check(tmp_path / "reader.xlsx", 300)
    assert out.get("formulas", 0) > 100 and out["mismatches"] == 0 and out["errors"] == 0, json.dumps(out)[:800]


def test_p1_8_a3_blocks_in_order_and_the_rest_in_four_collapsed_groups(built):
    from openpyxl.utils import get_column_letter
    ws = openpyxl.load_workbook(built["reader"])["A3 Revenue Buyback"]
    labels = [ws.cell(3, c).value for c in range(1, ws.max_column + 1) if ws.cell(3, c).value]
    assert labels[:4] == ["(a) Rank key", "(b) Valuation", "(c) Trajectory", "(d) Drivers"]
    assert labels[4:] == ["(e) More: capture", "(e) More: supply and dilution", "(e) More: business",
                          "(e) More: context"][:len(labels) - 4] and len(labels) == len(set(labels))
    e0 = next(c for c in range(1, ws.max_column + 1) if str(ws.cell(3, c).value or "").startswith("(e)"))
    for c in range(1, ws.max_column + 1):
        hidden = ws.column_dimensions[get_column_letter(c)].hidden
        assert hidden == (c >= e0), (c, ws.cell(4, c).value)
    assert not any(str(ws.cell(4, c).value).startswith("Detail") for c in range(1, ws.max_column + 1))


# ================================================================================================== P2a (the Charlie build)
def _rows_of(ws):
    return {ws.cell(r, 1).value: r for r in range(5, ws.max_row + 1) if ws.cell(r, 2).value is not None}


def _col_of(ws, header):
    return next(c for c in range(1, ws.max_column + 1) if ws.cell(4, c).value == header)


def test_p2a_tabs_in_order_and_nothing_blank(built):
    r = openpyxl.load_workbook(built["reader"])
    assert r.sheetnames == ["About", "Summary", "A1 Infrastructure", "A2 Coordination", "A3 Revenue Buyback",
                            "A4 Permanent Burn", "Comparison", "Business", "Charts", "Definitions", "_data", "_series"]
    assert r["_data"].sheet_state == r["_series"].sheet_state == "hidden"
    assert built["stats"]["blank_unexplained"] == 0, built["stats"]["blank_cells"]
    for tab in ("Summary", "A1 Infrastructure", "A2 Coordination", "A3 Revenue Buyback", "A4 Permanent Burn",
                "Comparison", "Business"):
        ws = r[tab]
        for rr in _rows_of(ws).values():
            for c in range(1, ws.max_column + 1):
                assert ws.cell(rr, c).value not in (None, ""), (tab, ws.cell(rr, c).coordinate, ws.cell(4, c).value)


def test_p2a_a1_no_measured_key_reads_unranked_and_a_parked_project_is_outside_the_ranks(built):
    r = openpyxl.load_workbook(built["reader"])
    ws = r["Summary"]
    rows, rc, kc = _rows_of(ws), _col_of(ws, "Rank in archetype"), _col_of(ws, "Archetype key")
    for name, rr in rows.items():
        rank, key = ws.cell(rr, rc).value, ws.cell(rr, kc).value
        if reader.parked_reason(name):
            assert rank == "parked" and ws.cell(rr, rc).comment.text.startswith("PARKED"), name
            assert ws.cell(rr, 4).fill.fgColor.rgb.endswith("EDEDED"), "a parked row is greyed"
        elif isinstance(key, str) and not key.startswith("="):
            assert rank == "unranked" and "Unranked" in ws.cell(rr, rc).comment.text, (name, key, rank)
    wm = rows.get("World Mobile")
    assert wm and ws.cell(wm, rc).value == "parked"


def test_p2a_a2_a_blank_audit_cell_reads_a_code_with_its_reason(built):
    r = openpyxl.load_workbook(built["reader"])
    ws = r["Summary"]
    rows, pc = _rows_of(ws), _col_of(ws, "Payout ratio")
    for name in ("Ethereum", "Plume", "Morpho", "Aethir"):
        if name in rows:
            c = ws.cell(rows[name], pc)
            assert c.value == "by design" and "no revenue-funded value to holders" in c.comment.text, (name, c.value)


def test_p2a_a3_a_payout_above_one_says_what_funds_the_excess(built):
    import xlcalc
    r = openpyxl.load_workbook(built["reader"])
    X = xlcalc.Workbook(r)
    ws = r["Summary"]
    pc = _col_of(ws, "Payout ratio")
    seen = 0
    for name, rr in _rows_of(ws).items():
        v = X.value("Summary", rr, pc)
        if isinstance(v, float) and v > 1:
            assert "Above 1.0x" in ws.cell(rr, pc).comment.text and "gross fees" in ws.cell(rr, pc).comment.text, name
            seen += 1
    assert seen >= 1


def test_p2a_a4_a_status_driven_zero_keeps_its_zero_and_shows_the_status(built):
    import xlcalc
    r = openpyxl.load_workbook(built["reader"])
    X = xlcalc.Workbook(r)
    ws = r["Summary"]
    rr = _rows_of(ws)["Fluid"]
    for head in ("Holder value yield", "Archetype key"):
        c = ws.cell(rr, _col_of(ws, head))
        assert X.value("Summary", rr, c.column) == 0.0, head
        assert "halted since 2026-05-11" in c.number_format and "halted since 2026-05-11" in c.comment.text, head
    assert reader.status_note(config.PROJECT_BY_NAME["Maple"], "staking").startswith("ended since 2025-11")


def test_p2a_a5_a_pre_minted_zero_net_supply_change_says_releases_are_dilution(built):
    import xlcalc
    r = openpyxl.load_workbook(built["reader"])
    X = xlcalc.Workbook(r)
    ws = r["Summary"]
    nc, seen = _col_of(ws, "Net supply change, % a year"), 0
    for name, rr in _rows_of(ws).items():
        if X.value("Summary", rr, nc) == 0.0 and reader.is_premint(config.PROJECT_BY_NAME[name]):
            assert "appear under dilution" in ws.cell(rr, nc).comment.text, name
            seen += 1
    assert seen >= 3


def test_p2a_a6_a_quarter_change_inherits_the_state_of_what_it_compares():
    asof = pd.Timestamp("2026-10-13")
    t = {"vals": [2.0, 1.0, None, None], "row": 9, "break": "", "modelled": "", "trend": "", "t12m": None,
         "t12m_prev": None}
    assert reader._quarter_change(t, asof)[:2] == ("ok", 1.0)
    assert reader._quarter_change(dict(t, **{"break": "SERIES BREAK 2026-10-12 — x"}), asof)[0] == "blocked"
    assert reader._quarter_change(dict(t, **{"break": "SERIES BREAK 2025-01-12 — x"}), asof)[0] == "ok"
    k, v, why = reader._quarter_change(dict(t, trend="▲ · T12M +17% (rolling T12M)", t12m=12.0, t12m_prev=10.0), asof)
    assert k == "t12m" and v == pytest.approx(0.2) and "TRAILING-12-MONTH" in why
    k, v, why = reader._quarter_change(dict(t, modelled="Q-1"), asof)
    assert k == "modelled" and "MODELLED" in why


def test_p2a_a7_what_changed_follows_the_selector(built):
    import xlcalc
    r = openpyxl.load_workbook(built["reader"])
    ws = r["Summary"]
    wc, rows = _col_of(ws, "What changed"), _rows_of(ws)
    assert all("Summary!$B$2" in str(ws.cell(rr, wc).value) for n, rr in rows.items()
               if str(ws.cell(rr, wc).value).startswith("="))
    texts = {}
    for period in ("7D", "1Y"):
        ws["B2"] = period
        X = xlcalc.Workbook(r)
        texts[period] = {n: X.value("Summary", rr, wc) for n, rr in rows.items()}
    assert any(str(v).startswith(("over 7D", "no figure moved")) for v in texts["7D"].values())
    assert any(texts["7D"][n] != texts["1Y"][n] for n in rows)


def test_p2a_b_archetype_tabs_are_built_like_a3_with_their_keys(built):
    r = openpyxl.load_workbook(built["reader"])
    defs = {(row[0].value, row[2].value) for row in r["Definitions"].iter_rows(min_row=4) if row[0].value}
    keys = {"A1 Infrastructure": "Settlement per token, 1-yr change", "A2 Coordination": "Customer revenue ÷ emissions (x)",
            "A3 Revenue Buyback": "Retired ÷ emissions (x)", "A4 Permanent Burn": "Burn ÷ issuance (x)"}
    for tab, key in keys.items():
        ws = r[tab]
        labels = [ws.cell(3, c).value for c in range(1, ws.max_column + 1) if ws.cell(3, c).value]
        assert labels[:4] == ["(a) Rank key", "(b) Valuation", "(c) Trajectory", "(d) Drivers"], (tab, labels)
        assert ws.cell(4, 3).value == key and "Higher is better" in ws.cell(4, 3).comment.text, tab
        for c in range(1, ws.max_column + 1):
            h = ws.cell(4, c).value
            assert h and len(h) <= 35 and (tab, h) in defs, (tab, h)
        assert not any(re.search(r"Δ(30d|3m|6m|9m)$", str(ws.cell(4, c).value)) for c in range(1, ws.max_column + 1))


def test_p2a_b_comparison_is_sorted_by_net_holder_yield_and_marks_the_archetype(built):
    import xlcalc
    r = openpyxl.load_workbook(built["reader"])
    X = xlcalc.Workbook(r)
    ws = r["Comparison"]
    nc, ac = _col_of(ws, "Net holder yield"), _col_of(ws, "Primary archetype")
    vals = [(n, X.value("Comparison", rr, nc)) for n, rr in _rows_of(ws).items() if not reader.parked_reason(n)]
    nums = [v for _n, v in vals if isinstance(v, float)]
    assert nums == sorted(nums, reverse=True) and nums
    first_text = next((i for i, (_n, v) in enumerate(vals) if not isinstance(v, float)), len(vals))
    assert all(not isinstance(v, float) for _n, v in vals[first_text:])
    assert all(str(ws.cell(rr, ac).value).startswith("A") for rr in _rows_of(ws).values())


def test_p2a_b_business_shows_constant_price_growth_for_protocols(built):
    r = openpyxl.load_workbook(built["reader"])
    ws = r["Business"]
    cc, rows = _col_of(ws, "TVL change at constant prices"), _rows_of(ws)
    for name, rr in rows.items():
        p = config.PROJECT_BY_NAME[name]
        v = ws.cell(rr, cc).value
        if reader._primary(p) == 1:
            assert v == "by design", name
        elif "protocol_tvl_constant_price_usd" in config.metrics_for_project(p):
            ok = str(v).startswith("=") and "_series!" in v
            assert ok or (v == "n/a" and "No daily" in ws.cell(rr, cc).comment.text), (name, v)
    unit = _col_of(ws, "Unit of demand")
    assert not any("_usd" in str(ws.cell(rr, unit).value) for rr in rows.values()), "plain words, not series keys"


def test_p2a_b_charts_read_data_and_leave_out_the_parked(built):
    r = openpyxl.load_workbook(built["reader"])
    ws = r["Charts"]
    assert len(ws._charts) >= 4
    notes = [ws.cell(rr, 1).value for rr in (29, 61, 93, 125)]
    parked = [p["name"] for p in config.PROJECTS if reader.parked_reason(p["name"])]
    assert all(n and n.startswith("Not shown:") for n in notes)
    assert all(all(f"{x} (parked)" in n for x in parked) for n in notes)


def test_p2a_c_about_describes_the_file_as_it_is(built):
    r = openpyxl.load_workbook(built["reader"])
    text = "\n".join(str(c.value) for c in r["About"]["A"] if c.value)
    assert "Coming next" not in text and "Stage" not in text
    for k in ("A1 Infrastructure", "A2 Coordination", "A3 Revenue buyback", "A4 Permanent burn", "Summary!B2",
              "custom", "parked", "token_metrics_audit.xlsx", "Data as of"):
        assert k in text, k
    assert sum(1 for line in text.splitlines() if line.startswith("Parked: ")) == sum(
        1 for p in config.PROJECTS if reader.parked_reason(p["name"]))


def test_p2a_e_the_column_map_places_every_new_tab_column_and_every_number_names_its_source(built):
    rows = list(csv.DictReader(open(built["dir"] / "reader_column_map.csv", encoding="utf-8")))
    a = openpyxl.load_workbook(built["audit"], read_only=True)
    for tab in ("A1 Infrastructure", "A2 Coordination", "A4 Permanent Burn", "CP Comparability"):
        heads = [c.value for c in next(a[tab].iter_rows(min_row=4, max_row=4)) if isinstance(c.value, str)]
        mapped = {x["audit_header"] for x in rows if x["audit_tab"] == tab}
        assert set(heads) <= mapped, (tab, set(heads) - mapped)
    r = openpyxl.load_workbook(built["reader"])
    for tab in ("Summary", "A1 Infrastructure", "A2 Coordination", "A3 Revenue Buyback", "A4 Permanent Burn",
                "Comparison", "Business"):
        ws = r[tab]
        for rr in _rows_of(ws).values():
            for c in range(3, ws.max_column + 1):
                cell = ws.cell(rr, c)
                if isinstance(cell.value, str) and cell.value.startswith("=") and cell.number_format != "General":
                    assert cell.comment and ("Working:" in cell.comment.text or "audit file" in cell.comment.text), \
                        (tab, cell.coordinate, ws.cell(4, c).value)


# ================================================================================================== P2b
def test_p2b_3_hyperliquid_a4_key_is_blocked_with_each_legs_covered_days(built):
    # P2b-3 (Jake 2026-10-27): the common-days ratio is NOT built; the cell says blocked and counts the covered days.
    ws = openpyxl.load_workbook(built["reader"])["A4 Permanent Burn"]
    rows, kc = _rows_of(ws), _col_of(ws, "Burn ÷ issuance (x)")
    cell = ws.cell(rows["Hyperliquid"], kc)
    assert cell.value == "blocked"
    covered = re.findall(r"covers (\d+) days of a 90-day window", cell.comment.text)
    assert len(covered) == 2, cell.comment.text                     # the burn leg and the issuance leg
    assert "COMMON" not in cell.comment.text.upper()


def test_p2b_4_a1_interim_fees_over_issuance_beside_the_unchanged_rank_key(built):
    r = openpyxl.load_workbook(built["reader"])
    ws = r["A1 Infrastructure"]
    hdr = [ws.cell(4, c).value for c in range(1, ws.max_column + 1)]
    i = hdr.index("Fees ÷ issuance, Q0 (interim)")
    assert hdr[i - 2:i] == ["Settlement per token, 1-yr change", "Rank in A1"]   # the key stays the rank key
    rows = _rows_of(ws)
    assert rows
    for name, rr in rows.items():
        c = ws.cell(rr, i + 1)
        assert c.value not in (None, "") and c.comment is not None, name
        if not (isinstance(c.value, str) and not c.value.startswith("=")):
            assert "INTERIM — not the rank key" in c.comment.text and "Working: audit file, A1 Infrastructure!" \
                in c.comment.text, name
    assert ws.cell(4, i + 1).comment is not None and "INTERIM" in ws.cell(4, i + 1).comment.text
    defs = r["Definitions"]
    assert any(defs.cell(rr, 1).value == "A1 Infrastructure" and defs.cell(rr, 3).value ==
               "Fees ÷ issuance, Q0 (interim)" and "INTERIM" in defs.cell(rr, 4).value
               for rr in range(4, defs.max_row + 1))
