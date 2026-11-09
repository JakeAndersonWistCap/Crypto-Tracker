"""PR1 round 1, group `trends` (Jake 2026-11-05): S1 covered-day annualisation, S2 like-for-like stock quarters, S3 the
key's quarter change on the quarters shown, S4 the Monthly tab on the headline series without the as-of day, S5 / U6
launch and partial quarters. No network: every test builds its own frames."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

ASOF = pd.Timestamp("2026-10-13")


def _long(project, metric, days, values, source="defillama"):
    return pd.DataFrame([{"date": pd.Timestamp(d), "project": project, "metric": metric, "value": float(v),
                          "source": source, "tier": 1, "fetched_at": "2026-10-13T00:00:00", "is_manual": 0,
                          "entered_on": None, "source_note": None} for d, v in zip(days, values)])


# ================================================================================================== S1
def test_s1_a_daily_flow_covers_its_rows_days_not_the_unread_as_of_day():
    import build_workbook as bw
    days = pd.date_range("2025-09-01", ASOF - pd.Timedelta(days=1))          # read to the day before asof
    s = pd.Series(1.0, index=days)
    q0 = ASOF - pd.Timedelta(days=90)
    assert bw._window_coverage(s, q0, ASOF, last=days[-1]) == (89, 90)       # 89 values, 89 days
    assert bw._lag_days(days[-1], q0, ASOF) == 1
    s2 = pd.Series(1.0, index=pd.date_range("2025-09-01", ASOF))             # the as-of day read too
    assert bw._window_coverage(s2, q0, ASOF, last=ASOF) == (90, 90)
    assert bw._lag_days(ASOF, q0, ASOF) == 0
    young = pd.Series(1.0, index=pd.date_range(ASOF - pd.Timedelta(days=9), ASOF))   # 10 rows, through asof
    assert bw._window_coverage(young, q0, ASOF, last=ASOF)[0] == 10          # k rows cover k days
    assert bw._window_coverage(s, q0, ASOF) == (90, 90), "without `last`, the span rule is unchanged"


def test_s1_aggregate_annualises_over_89_days_and_the_lag_is_no_short_history():
    import build_workbook as bw
    bw._SCOPE = list(bw.PROJECTS)
    days = list(pd.date_range("2025-09-01", ASOF - pd.Timedelta(days=1)))
    data = bw.aggregate(_long("Uniswap", "fees_usd", days, [1.0] * len(days)), pd.DataFrame(), ASOF)
    row = data[(data.project == "Uniswap") & (data.metric == "fees_usd")].iloc[0]
    assert row["q0"] == pytest.approx(89.0)
    assert row["q0_covered_days"] == 89 and row["q0_lag_days"] == 1
    assert "COVERS" not in str(row["note"])                                    # the lag is not a short history
    assert "covers only" not in str(row["why_amber"])


def test_s1_only_the_programme_series_is_discrete():
    import build_workbook as bw
    assert bw._lumpy("Sky") and bw._lumpy("Pendle")                           # the project still declares one
    assert not bw._lumpy("Sky", "fees_usd") and not bw._lumpy("Pendle", "revenue_usd")
    assert bw._lumpy("Pendle", "actual_buyback_tokens") and bw._lumpy("Pendle", "actual_buyback_usd")
    R = bw.Refs(10, 1, [])
    f = bw._annualise(R, 5, {"name": "Pendle"}, "fees_usd", "X")
    assert bw.DC["q0_covered_days"] in f                                      # over its covered days
    assert bw._annualise(R, 5, {"name": "Pendle"}, "actual_buyback_tokens", "X") == f"(X)*{bw.ANN}"


# ================================================================================================== S2
def test_s2_a_stock_quarter_reads_the_reading_at_its_end():
    import build_workbook as bw
    s = pd.Series([float(i) for i in range(400)], index=pd.date_range(ASOF - pd.Timedelta(days=399), ASOF))
    got = bw._quarter_end_readings(s, ASOF, 90, 7)
    assert got["q1_end"] == float(399 - 90) and got["q4_end"] == float(399 - 360)
    stale = s[s.index <= ASOF - pd.Timedelta(days=100)]                       # nothing within 7 days of Q1's end
    assert bw._quarter_end_readings(pd.concat([stale, s[s.index > ASOF - pd.Timedelta(days=5)]]),
                                    ASOF, 90, 7)["q1_end"] is None            # a hole, never carried
    assert bw._shift_col("tvl_usd", "now", 1) == "q1_end"
    assert bw._shift_col("tvl_usd", "q0", 1) == "q1"                          # a 90-day mean against a mean
    R = bw.Refs(10, 1, [])
    with bw.shifted(2):
        assert f"Data!${bw.DC['q2_end']}$" in R.D(5, "price_usd", "now")
    assert set(bw.QUARTER_ENDS) <= set(bw.DATA_COLS) and len(bw.DATA_COLS) == len(bw.DATA_HEAD)


def test_s2_aggregate_writes_the_quarter_end_readings_for_a_stock():
    import build_workbook as bw
    bw._SCOPE = list(bw.PROJECTS)
    days = list(pd.date_range(ASOF - pd.Timedelta(days=380), ASOF))
    vals = [100.0 + i for i in range(len(days))]
    data = bw.aggregate(_long("Uniswap", "protocol_tvl_usd", days, vals), pd.DataFrame(), ASOF)
    row = data[(data.project == "Uniswap") & (data.metric == "protocol_tvl_usd")].iloc[0]
    assert row["now"] == vals[-1] and row["q1_end"] == vals[-91] and row["q4_end"] == vals[-361]
    assert row["q1"] == pytest.approx(sum(vals[-180:-90]) / 90)               # the mean stays where it was


# ================================================================================================== S3
def test_s3_the_key_change_reads_the_quarters_shown_for_a_lumpy_series():
    import reader
    t = {"vals": [70.0, 100.0, 90.0, 80.0], "row": 9, "break": "", "modelled": "", "why": "",
         "trend": "▲ · T12M +5.5% vs a quarter earlier · +24%/yr (rolling T12M)", "t12m": 105.5, "t12m_prev": 100.0}
    kind, v, why = reader._quarter_change(t, ASOF)
    assert kind == "ok" and v == pytest.approx(-0.30)                        # latest / −1 qtr, not the T12M +5.5%
    assert "trailing-12-month" in why


# ================================================================================================== S4
def test_s4_the_monthly_tab_reads_the_headline_series_and_never_the_as_of_day():
    import build_workbook as bw
    bw._SCOPE = [config.PROJECT_BY_NAME["Uniswap"]]
    try:
        raw = _long("Uniswap", "revenue_usd", ["2026-10-01", "2026-10-12"], [1.0, 2.0])
        view = _long("Uniswap", "revenue_usd", ["2026-10-01", "2026-10-12", "2026-10-13"], [10.0, 20.0, 999.0])
        months, t = bw.monthly_table(raw, ASOF, groups={("Uniswap", "revenue_usd"): view})
        row = t[t.key == "Uniswap|revenue_usd"].iloc[0]
        assert row["2026-10"] == 30.0                                         # the view's rows; 10-13 left out
        _m, t2 = bw.monthly_table(raw, ASOF)
        assert t2[t2.key == "Uniswap|revenue_usd"].iloc[0]["2026-10"] == 3.0  # no groups: the raw rows
    finally:
        bw._SCOPE = list(bw.PROJECTS)


# ================================================================================================== S5 / U6
def test_u6_uniswap_fee_switch_is_a_declared_launch_and_fees_are_not():
    assert config.series_launch("Uniswap", "revenue_usd")["date"] == "2025-12-27"   # PR1B.7: the execution on chain
    for m in ("holders_revenue_usd", "gross_burn_tokens", "holder_value_revenue_usd"):
        assert config.series_launch("Uniswap", m)
    assert config.series_launch("Uniswap", "fees_usd") is None
    assert config.series_launch("Sky", "sky_stage2_burn_tokens")["date"] == "2026-09-13"   # MECHANISM_START too


def test_s5_launch_quarter_and_growth_from_it_reads_new_not_a_percentage():
    import trajectory as tj
    assert tj.launch_quarter("2025-12-30", ASOF, 90, 5) == (3, True)         # Q-3 = 2025-10-18 .. 2026-01-16
    assert tj.launch_quarter("2025-12-30", "2026-11-05", 90, 5) == (3, True)  # Jake's run: Q-3 from 2025-11-10
    assert tj.launch_quarter("2025-10-19", ASOF, 90, 5) == (3, False)        # the quarter's first day: not partial
    assert tj.launch_quarter("2024-01-01", ASOF, 90, 5) == (None, False)
    qs = [29.8, 13.2, 11.4, 1.5, None]
    g, n, label, why, cut = tj.growth_base(qs, None, 3, "NEW since Q-3 (fee switch 2025-12-30)")
    assert g is None and label == "NEW since Q-3 (fee switch 2025-12-30)" and cut == 3
    assert tj.improved(qs, 1, cut) == (2, 2)                                  # Q-2 vs Q-3 is not counted
    assert tj.improved(qs, 1) == (3, 3)
    txt = tj.trend_text(None, None, 2, 2, 1, label, "NEW since Q-1")
    assert "%/yr" not in txt and "QoQ n/a — NEW since Q-1" in txt


def test_s5_a_partial_quarter_too_young_for_a_label_still_bars_its_comparison():
    import trajectory as tj
    g, n, label, why, cut = tj.growth_base([5.0, 1.0, None, None], None, 1, "partial quarter (Q-1: 20 of 90 days covered)")
    assert g is None and cut == 1
    assert tj.improved([5.0, 1.0, None, None], 1, cut) == (None, 0)


def test_s5_the_key_change_from_a_launch_quarter_is_a_code():
    import reader
    t = {"vals": [70.0, 7.0, None, None], "row": 9, "break": "", "modelled": "",
         "why": "growth: NEW since Q-1 (fee switch 2025-12-30) — x; QoQ: n/a — NEW since Q-1 (fee switch 2025-12-30)",
         "trend": "– · NEW since Q-1 (fee switch 2025-12-30) · QoQ n/a — NEW since Q-1 (fee switch 2025-12-30)",
         "t12m": None, "t12m_prev": None}
    kind, v, why = reader._quarter_change(t, ASOF)
    assert kind == "n/a" and v is None and "fee switch 2025-12-30" in why
