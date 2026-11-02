"""ROUND P3 ADDENDUM A2 (Jake): THE BAD-PRINT GUARD FOR STOCK SERIES (config.BAD_PRINT_GUARD).

A stored day of a STOCK series more than 25% off BOTH neighbouring stored days, which agree within 5%, is left out of
every read (build_workbook._bad_print_views, before any view or window) and named 'one-day print excluded: <date>
<value>' on the series' Data row note and on the reader cells whose period reads the series. A selected-period change
whose start day is an excluded print reads the nearest kept day before it. Flows are never touched; a first / last day,
two consecutive outliers, neighbours that disagree, and a declared break day are never excluded.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import reader  # noqa: E402


def _daily(values, start="2026-06-01") -> pd.Series:
    return pd.Series([float(v) for v in values], index=pd.date_range(start, periods=len(values)))


def _group(project: str, metric: str, s: pd.Series, source: str = "defillama:tvl") -> pd.DataFrame:
    return pd.DataFrame({"project": project, "metric": metric, "date": s.index, "value": s.to_numpy(),
                         "source": source})


# ============================================================================================ the rule (bad_prints)
def test_a2_one_bad_print_is_excluded_with_its_neighbours():
    s = _daily([100, 101, 58, 100, 102])
    got = bw.bad_prints(s)
    assert [(str(d.date()), v, a, b) for d, v, a, b in got] == [("2026-06-03", 58.0, 101.0, 100.0)]


def test_a2_two_consecutive_outliers_are_both_kept():
    # each outlier's neighbours are one normal day and the other outlier: they do not agree, so neither is excluded
    assert bw.bad_prints(_daily([100, 101, 58, 57, 100, 102])) == []


def test_a2_neighbours_that_disagree_keep_the_day():
    # 100 -> 60 -> 130: 60 is >25% off both, but 100 and 130 disagree by more than 5%
    assert bw.bad_prints(_daily([100, 100, 60, 130, 131])) == []


def test_a2_the_first_and_last_day_have_one_neighbour_and_are_kept():
    assert bw.bad_prints(_daily([40, 100, 101, 100, 250])) == []


def test_a2_a_declared_break_day_is_never_excluded():
    s = _daily([100, 101, 58, 100, 102])
    assert bw.bad_prints(s, protected={pd.Timestamp("2026-06-03")}) == []


def test_a2_the_text_is_exact():
    assert bw.bad_print_text([("2026-08-04", 58064203.0)]) == "one-day print excluded: 2026-08-04 58,064,203"
    assert bw.bad_print_text([("2026-03-09", 0.013588953727166586, 0.032, 0.031)]) == \
        "one-day print excluded: 2026-03-09 0.013589"
    assert bw.bad_print_text([("2026-06-21", 7.0), ("2026-06-27", 6.0)]) == \
        "one-day print excluded: 2026-06-21 7; one-day print excluded: 2026-06-27 6"


# ============================================================================================ where it sits
def test_a2_the_view_drops_a_stock_print_logs_it_and_never_touches_a_flow(caplog):
    stock = _daily([100e6, 101e6, 58e6, 100e6, 102e6])
    flow = _daily([10, 11, 90, 10, 11])                    # a one-day spike in a flow is activity
    groups = {("X", "tvl_usd"): _group("X", "tvl_usd", stock),
              ("X", "volume_usd"): _group("X", "volume_usd", flow, "defillama:volume")}
    assert config.METRICS["tvl_usd"]["kind"] == "stock" and config.METRICS["volume_usd"]["kind"] == "flow"
    with caplog.at_level(logging.INFO, logger="build_workbook"):
        bw._bad_print_views(groups)
    assert bw.BAD_PRINTS == {("X", "tvl_usd"): [("2026-06-03", 58e6, 101e6, 100e6)]}
    assert pd.Timestamp("2026-06-03") not in set(groups[("X", "tvl_usd")]["date"])
    assert len(groups[("X", "volume_usd")]) == 5
    lines = [r.getMessage() for r in caplog.records if "bad-print guard" in r.getMessage()]
    assert lines == ["bad-print guard (P3-A2): X tvl_usd 2026-06-03 58,000,000 excluded "
                     "(previous day 101,000,000, next day 100,000,000)"]
    long = pd.concat(groups.values())
    assert len(bw._without_bad_prints(pd.concat([long, _group("X", "tvl_usd", stock.iloc[2:3])]))) == len(long)


def test_a2_a_declared_break_or_reclassification_day_is_kept_by_the_view(monkeypatch):
    s = _daily([100, 101, 58, 100, 102])
    monkeypatch.setitem(config.SOURCE_RECLASSIFICATIONS, ("X", "tvl_usd"), (("2026-06-03", "a declared step"),))
    groups = {("X", "tvl_usd"): _group("X", "tvl_usd", s)}
    bw._bad_print_views(groups)
    assert bw.BAD_PRINTS == {} and len(groups[("X", "tvl_usd")]) == 5
    monkeypatch.delitem(config.SOURCE_RECLASSIFICATIONS, ("X", "tvl_usd"))
    monkeypatch.setitem(config.SERIES_BREAK_NOTES, ("X", "tvl_usd"),
                        {"break": "2026-06-04", "day": "2026-06-03", "text": "declared"})
    groups = {("X", "tvl_usd"): _group("X", "tvl_usd", s)}
    bw._bad_print_views(groups)
    assert bw.BAD_PRINTS == {}


def test_a2_a_declared_handover_seam_is_kept(monkeypatch):
    s = _daily([100, 101, 58, 100, 102])
    g = _group("X", "tvl_usd", s)
    g.loc[g["date"] == pd.Timestamp("2026-06-03"), "source"] = "manual:filing"
    monkeypatch.setattr(config, "declared_handover",
                        lambda p, m: {"ordered_points": ["defillama", "manual"]} if (p, m) == ("X", "tvl_usd") else None)
    groups = {("X", "tvl_usd"): g}
    bw._bad_print_views(groups)
    assert bw.BAD_PRINTS == {}


def test_a2_a_day_on_another_basis_is_a_basis_change_not_a_bad_print():
    # a manual reading among a provider's days (the guard baseline's GEODNET circulating_supply_third_party): the
    # measuring-point rules own a change of basis; the guard judges a day only against neighbours on its own basis
    g = _group("X", "tvl_usd", _daily([1, 1, 300e6, 1, 1]), "probe:tvl_usd")
    g.loc[g["date"] == pd.Timestamp("2026-06-03"), "source"] = "manual"
    groups = {("X", "tvl_usd"): g}
    bw._bad_print_views(groups)
    assert bw.BAD_PRINTS == {} and len(groups[("X", "tvl_usd")]) == 5


# ============================================================================================ the reader's end points
def _near_like():
    """A Near-like TVL: ~101M at the 90-day window's start, a bad print of 58M ON the start day, ~189M at the end."""
    end = pd.Timestamp("2026-11-01")
    days = pd.date_range(end - pd.Timedelta(days=120), end)
    s = pd.Series([101e6 + (189e6 - 101e6) * max(0, (d - (end - pd.Timedelta(days=90))).days) / 90 for d in days],
                  index=days)
    start = end - pd.Timedelta(days=90)
    s.loc[start - pd.Timedelta(days=1)] = 101.1e6
    s.loc[start + pd.Timedelta(days=1)] = 100.0e6
    raw = s.copy()
    raw.loc[start] = 58e6
    return raw, start, end


def _period_value(s: pd.Series, excluded, asof) -> object:
    import xlcalc
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = reader.SERIES_SHEET
    ser = reader.SeriesSheet(ws, asof)
    ws.cell(1, 2, 90)                                     # the selected period: 90D
    L = ser.add("Near|tvl_usd", s, excluded=excluded)
    out = wb.create_sheet("Out")
    out.cell(1, 1, "=" + ser.stock_change(L))
    return xlcalc.Workbook(wb).value("Out", 1, 1), ser


def test_a2_near_like_a_bad_print_at_the_window_start_no_longer_triples_the_change():
    raw, start, end = _near_like()
    found = bw.bad_prints(raw)
    assert [d for d, *_ in found] == [start]
    before, _ = _period_value(raw, (), end)                # no guard: the start day is the bad print
    kept = raw.drop(index=start)
    after, ser = _period_value(kept, [(str(start.date()), 58e6)], end)
    assert before == pytest.approx(189e6 / 58e6 - 1)       # +226%
    assert after == pytest.approx(189e6 / 101.1e6 - 1)     # +87%: the nearest kept day before the start
    assert 2.5 < before / after < 3
    # the Python definition agrees, and a day merely missing (not excluded) still reads n/a
    assert reader.price_change(kept, 90, end, excluded=[(start, 58e6)])[0] == pytest.approx(after)
    assert reader.price_change(kept, 90, end)[0] is None
    assert ser.describe("Near|tvl_usd").endswith("1 one-day print(s) excluded)")
    ctx = SimpleNamespace(ser=ser, bw=bw)
    note = reader._excluded_note(ctx, "Near", "tvl_usd", "stock")
    assert note.startswith("\n\none-day print excluded: 2026-08-03 58,000,000. ")
    assert reader._excluded_note(ctx, "Near", "tvl_usd", "flow") == ""


def test_a2_a_missing_start_day_still_reads_na_in_the_workbook():
    raw, start, end = _near_like()
    got, _ = _period_value(raw.drop(index=start), (), end)
    assert got == "n/a"


# ============================================================================================ the fixture build
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The build on the committed real dumps (as test_round_p2e_ranks)."""
    import measuring_guard as mg
    import store as sm
    from real_dump import to_store
    d = tmp_path_factory.mktemp("p3a2")
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
    return {"audit": d / "audit.xlsx", "reader": d / "reader.xlsx", "prints": dict(bw.BAD_PRINTS),
            "groups": dict(bw._LAST_GROUPS)}


def test_a2_fixture_the_known_prints_are_excluded_and_read_by_nothing(built):
    prints = built["prints"]
    near = [d for d, *_ in prints.get(("Near", "tvl_usd"), ())]
    for day in ("2026-06-21", "2026-06-27", "2026-08-04", "2026-08-17", "2026-08-30"):
        assert day in near, day
    assert "2026-06-19" in [d for d, *_ in prints.get(("Plume", "stablecoin_supply_usd"), ())]
    g = built["groups"][("Near", "tvl_usd")]
    held = set(pd.to_datetime(g["date"]).dt.strftime("%Y-%m-%d"))
    assert not held & set(near)
    for (p, m) in prints:
        assert config.METRICS[m]["kind"] == "stock", (p, m)


def test_a2_fixture_the_data_row_and_the_reader_period_cell_name_the_print(built):
    a = openpyxl.load_workbook(built["audit"], read_only=True)
    ws = a["Data"]
    kcol, ncol = bw.DATA_COLS.index("key"), bw.DATA_COLS.index("note")
    note = next(r[ncol] for r in ws.iter_rows(min_row=2, values_only=True) if r[kcol] == "Near|tvl_usd")
    assert "one-day print excluded: 2026-08-04 58,064,203" in note
    r = openpyxl.load_workbook(built["reader"])
    b = r[reader.BUSINESS]
    col = next(c for c in range(1, b.max_column + 1) if str(b.cell(4, c).value or "").startswith("TVL change"))
    row = next(rr for rr in range(5, b.max_row + 1) if b.cell(rr, 1).value == "Near")
    cell = b.cell(row, col)
    assert "one-day print excluded: 2026-08-04 58,064,203" in cell.comment.text
    assert str(cell.value).startswith("=") and "IF(" in cell.value
