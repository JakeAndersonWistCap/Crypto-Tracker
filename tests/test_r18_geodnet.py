"""Round 18 (Jake 2026-11-24) — GEODNET's three revenue lines on the Blockworks filing's reading: HOLDER REVENUE = the
measured burn, REVENUE = the burn ÷ 0.8, FEES = the burn ÷ 0.4, both INFERRED (SPEC "Round 18 — GEODNET"). No network."""
from __future__ import annotations

import sys

import pandas as pd
import pytest

import build_workbook as bw
import config

G = "GEODNET"
COLS = ["date", "project", "metric", "value", "source", "tier", "fetched_at"]


def _g(metric, rows, project=G):
    return pd.DataFrame([{"date": pd.Timestamp(d), "project": project, "metric": metric, "value": v, "source": s,
                          "tier": 1, "fetched_at": "2026-11-24T00:00:00+00:00"} for d, v, s in rows], columns=COLS)


def _burn():
    # a month row (a Dune month, dated its first day), two daily rows and a day with no burn
    return _g("actual_buyback_usd", [("2026-08-01", 754_542.0, "derived:tokens*price[span=31d]"),
                                     ("2026-09-01", 24_000.0, "derived:tokens*price"),
                                     ("2026-09-02", 0.0, "derived:tokens*price"),
                                     ("2026-09-03", 16_000.0, "derived:tokens*price")])


# =============================================================================== the three lines, by hand
def test_revenue_is_the_burn_over_0_8_and_fees_the_burn_over_0_4_row_for_row():
    g = {(G, "actual_buyback_usd"): _burn()}
    bw._s13_inferred_views(g)
    burn, rev, fees = (g[(G, m)] for m in ("actual_buyback_usd", "s13_revenue_inferred_usd", "s13_fees_inferred_usd"))
    assert list(burn["value"]) == [754_542.0, 24_000.0, 0.0, 16_000.0]                  # the measured line is untouched
    assert list(rev["value"]) == pytest.approx([943_177.5, 30_000.0, 0.0, 20_000.0])    # B ÷ 0.8
    assert list(fees["value"]) == pytest.approx([1_886_355.0, 60_000.0, 0.0, 40_000.0])  # B ÷ 0.4
    for v in (rev, fees):                                   # the same rows: dates, and the span marker a month carries
        assert list(v["date"]) == list(burn["date"]) and "[span=31d]" in v["source"].iloc[0]
        assert v["source"].str.startswith("derived:s13_inferred[actual_buyback_usd x ").all()
    b, r, f = burn["value"].sum(), rev["value"].sum(), fees["value"].sum()
    assert b == 794_542.0 and r == pytest.approx(993_177.5) and f == pytest.approx(1_986_355.0)
    assert b / r == pytest.approx(0.80) and b / f == pytest.approx(0.40) and r / f == pytest.approx(0.50)


def test_the_factors_are_the_filings_two_shares():
    v = config.S13_INFERRED[G]
    assert v["s13_revenue_inferred_usd"] == ("actual_buyback_usd", pytest.approx(1.25))    # 1 ÷ 0.8
    assert v["s13_fees_inferred_usd"] == ("actual_buyback_usd", pytest.approx(2.5))        # 1 ÷ (0.8 x 0.5)
    assert config.GEODNET_BURN_SHARE_OF_REVENUE == 0.80 and config.GEODNET_FOUNDATION_SHARE_OF_FEES == 0.50
    assert config.PROJECT_BY_NAME[G]["fee_split"]["share_to_buyback"] == 0.80              # the declared split, unchanged


def test_no_burn_series_no_inferred_line_and_no_other_project_is_touched():
    g = {(G, "fees_usd"): _g("fees_usd", [("2026-09-01", 30_000.0, "defillama")]),
         ("Uniswap", "actual_buyback_usd"): _g("actual_buyback_usd", [("2026-09-01", 5.0, "x")], "Uniswap")}
    before = {k: v.copy() for k, v in g.items()}
    bw._s13_inferred_views(g)
    assert set(g) == set(before)                              # nothing inferred from DefiLlama's fees, nothing for Uniswap
    assert all(g[k].equals(before[k]) for k in before)
    assert set(config.S13_INFERRED) == {G}
    for m in ("s13_revenue_inferred_usd", "s13_fees_inferred_usd"):
        assert config.METRICS[m]["only_projects"] == (G,) and config.METRICS[m]["view_only"] is True
    assert set(config.FROZEN_PROJECTS).isdisjoint(config.S13_INFERRED)


# =============================================================================== what the lines say they are
def test_the_lines_say_inferred_cite_the_filing_and_say_its_wording_is_ambiguous():
    s = config.revenue_split(G)
    assert s["holder_revenue"]["metric"] == "actual_buyback_usd" and s["holder_revenue"]["primary"] is True
    assert (s["revenue"]["metric"], s["fees"]["metric"]) == ("s13_revenue_inferred_usd", "s13_fees_inferred_usd")
    for role in ("revenue", "fees"):
        leg = s[role]
        assert leg["primary"] is False and leg["inferred"] is True
        assert leg["route"].startswith("INFERRED") and "AMBIGUOUS" in leg["route"]
        assert "Blockworks token-transparency filing" in leg["route"] and "19 Aug 2025" in leg["route"]
        _suffix, note = config.revenue_tier_label(G, bw.CP_FEES_LINE_HEAD if role == "fees" else bw.CP_REV_LINE_HEAD)
        assert "INFERRED" in note and "AMBIGUOUS" in note                      # carried onto the cell's comment
    f = s["filing"]
    assert f["filed"] == "2025-08-19" and f["read_on"] == "2026-11-23"
    assert "50% of the subscription revenue is shared with the GEODNET Foundation" in f["quote"]
    assert "80% of this revenue is used for buy-back and burning GEOD tokens" in f["quote"]
    assert "DERIVED FROM THE ON-CHAIN BURN" in config.metric_label(G, "fees_usd")      # the headline fee cell: as it was
    assert "S13 FEES = the burn ÷ 0.4" in config.metric_label(G, "fees_usd")


def test_geodnets_own_arr_equals_its_burn_over_0_8_and_is_half_of_the_fees_line():
    fp = config.REVENUE_SPLIT[G]["first_party"]
    b = fp["burn_usd_month"]
    assert b == 265_235 and fp["burn_month"] == "2025-05" and fp["figure_usd_annualised"] == 4_000_000
    assert b * 12 / 0.8 == pytest.approx(3_978_525) == fp["implied_revenue_annualised"]
    assert b * 12 / 0.4 == pytest.approx(7_957_050) == fp["implied_fees_annualised"]
    assert fp["implied_revenue_annualised"] / fp["figure_usd_annualised"] - 1 == pytest.approx(-0.0054, abs=1e-4)
    assert fp["implied_fees_annualised"] / fp["figure_usd_annualised"] - 1 == pytest.approx(0.9893, abs=1e-4)


# =============================================================================== uniformity: the cell says what is true
def test_the_uniformity_cell_no_longer_says_nothing_is_paid_to_stakers_and_declares_nothing():
    t = config.declared_na_text(G, "UNIFORMITY = whole-token yield ÷ staker yield")
    assert t.startswith("n/a") and "+20% GEOD" in t and "nothing is paid to stakers" not in t
    assert config.declared_na_reason(G, t) is None            # not an accepted reason: done_check judges it as before
    sb = config.GEODNET_STAKER_BONUS
    assert sb["rate"] == 0.20 and sb["phase_1_rate"] == 0.10
    assert sb["staked_read"]["tokens"] * sb["rate"] == pytest.approx(328_400)      # the most +20% can cost in a year


# =============================================================================== the fixture dump, built (GEODNET only)
@pytest.fixture(scope="module")
def cells(tmp_path_factory):
    import openpyxl
    import store as sm
    import xlcalc
    from real_dump import load, to_store
    mp = pytest.MonkeyPatch()
    try:
        payload = load(G)
        d = tmp_path_factory.mktemp("r18_geodnet")
        mp.setitem(sys.modules, "recalc", None)
        mp.setattr(bw, "_SCOPE", bw._SCOPE)
        db = d / "metrics.db"
        to_store(payload, db)
        mp.setattr(sm, "DB_PATH", str(db))
        out = d / "wb.xlsx"
        bw.build_workbook(sm.Store(str(db)), out, asof=pd.Timestamp(payload["meta"]["asof"]), only=[G])
        wb = openpyxl.load_workbook(out)
        X = xlcalc.Workbook(wb)

        def cell(tab, head):
            ws = next(w for w in wb.worksheets if w.title.startswith(tab))
            hr = next(r for r in range(1, 12) if ws.cell(r, 1).value == "Project")
            col = next(c for c in range(1, ws.max_column + 1) if str(ws.cell(hr, c).value or "").startswith(head))
            row = next(r for r in range(hr + 1, ws.max_row + 1) if ws.cell(r, 1).value == G)
            return X.value(ws.title, row, col)
        yield cell
    finally:
        mp.undo()


def test_the_dump_reads_burn_2_210_124_revenue_2_762_655_fees_5_525_310(cells):
    """90 days to 2026-10-13 (the dump): the measured burn $2,210,123.83; ÷ 0.8 = $2,762,654.79; ÷ 0.4 = $5,525,309.57."""
    burn = cells("CP", bw.CP_HREV_LINE_HEAD)
    assert burn == pytest.approx(2_210_123.83, abs=0.01)
    assert cells("CP", bw.CP_REV_LINE_HEAD) == pytest.approx(burn / 0.8, abs=0.01) == pytest.approx(2_762_654.79, abs=0.01)
    assert cells("CP", bw.CP_FEES_LINE_HEAD) == pytest.approx(burn / 0.4, abs=0.01) == pytest.approx(5_525_309.57, abs=0.01)
    assert cells("CP", bw.CP_HSHARE_HEAD) == pytest.approx(0.80, abs=1e-9)
    assert burn / cells("CP", bw.CP_FEES_LINE_HEAD) == pytest.approx(0.40, abs=1e-9)


def test_the_headline_cells_are_where_they_were(cells):
    """DefiLlama's fees = revenue (its burn ÷ 0.8) stays the headline and the cross-check of REVENUE: $2,693,724 on the
    89 days it holds, 2.5% under our 90-day $2,762,655 (per day: 30,267 against 30,696, −1.4%)."""
    assert cells("Master", "Fees Q0 ($)") == 2_693_724.0 and cells("Master", "Revenue Q0 ($)") == 2_693_724.0
    assert 2_693_724.0 / 2_762_654.79 - 1 == pytest.approx(-0.02495, abs=1e-4)
    assert (2_693_724.0 / 89) / (2_762_654.79 / 90) - 1 == pytest.approx(-0.0140, abs=1e-3)
    t = cells("CP", "UNIFORMITY")
    assert "+20% GEOD" in str(t) and "nothing is paid to stakers" not in str(t)
