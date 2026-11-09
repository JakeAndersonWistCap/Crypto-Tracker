"""Project round 1B (Jake 2026-11-09), group `burn`: items 2 (burn day-stamping by block time), 3 (one pricing basis for
$ burn), 6 (no holder value credited before the fee switch's execution) and 7 (the fee switch's dates). No network."""
from __future__ import annotations

import pandas as pd

import config
import references


def _frame(days, vals, source="derived:one_off"):
    return pd.DataFrame({"date": pd.to_datetime(days), "value": vals, "source": source})


# ================================================================================================== item 7
def test_7_series_launch_is_the_fee_switch_execution_on_chain():
    la = config.SERIES_LAUNCH["Uniswap"]
    assert la["date"] == "2025-12-27" and la["what"] == "fee switch executed"
    assert la["block"] == 24_106_378 and la["executed_utc"] == "2025-12-27 20:33:11"
    assert la["defillama_first_revenue"] == "2025-12-29"
    assert config.series_launch("Uniswap", "revenue_usd") == {"date": "2025-12-27", "what": "fee switch executed"}
    uni = config.PROJECT_BY_NAME["Uniswap"]
    assert uni["fee_switch_executions"]["#93 UNIfication (mainnet v2/v3, Firepit, 100M burn)"].startswith("2025-12-27")
    assert uni["fee_switch_dates"]["ethereum"] == "2025-12-27"


def test_7_the_launch_label_reads_fee_switch_executed():
    """The Trajectory's label is built from SERIES_LAUNCH: "NEW since Q-k (fee switch executed 2025-12-27)"."""
    import trajectory as tj
    la = config.series_launch("Uniswap", "gross_burn_tokens")
    k, part = tj.launch_quarter(la["date"], "2026-11-09", 90, 5)
    assert (k, part) == (3, True)                       # Q-3 = 2025-11-14 .. 2026-02-11 on Jake's as-of
    assert f"NEW since {tj.LABELS[k]} ({la['what']} {la['date']})" == "NEW since Q-3 (fee switch executed 2025-12-27)"
    k2, _ = tj.launch_quarter(la["date"], "2026-10-13", 90, 5)
    assert k2 == 3                                      # the dumps' as-of: the same quarter


def test_7_the_100m_reference_reads_the_event_whichever_day_it_is_stamped():
    """Jake's store: one_off_burn_tokens ends 2025-12-27 (the tx's block day) and sum_period refused "ends before the
    period's end 2025-12-28". Read-date stamping puts the differenced row on 12-28, block-time stamping (item 2) on
    12-27: both read 100,000,000."""
    route = references.ROUTES[("Uniswap", "g6_retro_burn_100m")]
    assert route[0] == "event_period"
    at = pd.Timestamp("2025-12-27")
    for days in (["2025-12-27"], ["2025-12-28"]):
        g = _frame(days, [100_000_000.0])
        v, where, why = references._our_value(route, {("Uniswap", "one_off_burn_tokens"): g}, "Uniswap", at,
                                              100_000_000.0)
        assert v == 100_000_000.0 and not why, (days, v, where, why)
        assert days[0] in where
    # the old route refused the block-day stamping (the failure Jake saw)
    old = ("sum_period", "one_off_burn_tokens", "2025-12-27", "2025-12-28")
    v, _w, why = references._our_value(old, {("Uniswap", "one_off_burn_tokens"): _frame(["2025-12-27"], [1e8])},
                                       "Uniswap", at, 1e8)
    assert v is None and "before the period's end" in why
    # nothing in the window is never a zero
    v, _w, why = references._our_value(route, {("Uniswap", "one_off_burn_tokens"): _frame(["2026-01-05"], [1e8])},
                                       "Uniswap", at, 1e8)
    assert v is None and "not stored" in why


# ================================================================================================== item 6
def _stored(days, vals, metric, source="test", project="Uniswap"):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": project, "metric": metric, "value": vals,
                         "source": source, "tier": 2})


def test_6_no_holder_value_is_credited_before_the_fee_switch_executed():
    """A 0.0014 UNI burn on 2025-10-22 / 11-03 (stray transfers to 0x…dEaD, two months before the fee switch) was
    credited as holder value with no revenue (Flows Q-4 CHECK). Before 2025-12-27 a burn is no holder value: 0 credited,
    and not recorded as treasury / emissions-funded either; from the execution day it is credited as before."""
    import build_workbook as bw
    assert config.holder_funding("Uniswap", "2025-12-26")[0] == "none"
    assert config.holder_funding("Uniswap", "2025-12-27")[0] == "revenue"
    assert config.holder_funding("Uniswap", "2026-08-01")[0] == "revenue"
    assert config.holder_funding("GEODNET", "2020-01-01")[0] == "revenue"            # no declaration: unchanged
    days = ["2025-10-22", "2025-11-03", "2026-01-05"]
    groups = {("Uniswap", "actual_buyback_usd"): _stored(days, [0.0089, 0.0007, 52_000.0], "actual_buyback_usd"),
              ("Uniswap", "actual_buyback_tokens"): _stored(days, [0.0013, 0.0001, 8_000.0], "actual_buyback_tokens")}
    keep = bw._SCOPE
    bw._SCOPE = [config.PROJECT_BY_NAME["Uniswap"]]
    bw._ONE_OFF_IN_SERIES.clear()
    try:
        bw._holder_value_views(groups)
    finally:
        bw._SCOPE = keep
    cr = groups[("Uniswap", "holder_value_revenue_usd")].set_index("date")["value"]
    assert cr[pd.Timestamp("2025-10-22")] == 0.0 and cr[pd.Timestamp("2025-11-03")] == 0.0
    assert cr[pd.Timestamp("2026-01-05")] == 52_000.0
    assert ("Uniswap", "holder_value_other_usd") not in groups                    # not recorded as another funding


def test_6_flows_q4_reads_ok_on_a_live_shaped_store():
    """Jake's as-of 2026-11-09: Q-4 (to 2025-11-14) holds the two stray pre-switch burns and no revenue — its value-flow
    status read "CHECK — holder value credited with no revenue in the quarter"; now OK (0 credited)."""
    import build_workbook as bw
    asof = pd.Timestamp("2026-11-09")
    days = pd.date_range("2025-08-01", "2026-11-08")
    b = ["2025-10-22", "2025-11-03", "2026-01-05"]
    groups = {("Uniswap", "revenue_usd"): _stored(days, [0.0 if d < pd.Timestamp("2025-12-29") else 1000.0
                                                         for d in days], "revenue_usd"),
              ("Uniswap", "fees_usd"): _stored(days, [5000.0] * len(days), "fees_usd"),
              ("Uniswap", "actual_buyback_usd"): _stored(b, [0.0089, 0.0007, 900.0], "actual_buyback_usd"),
              ("Uniswap", "actual_buyback_tokens"): _stored(b, [0.0013, 0.0001, 100.0], "actual_buyback_tokens")}
    keep = bw._SCOPE
    bw._SCOPE = [config.PROJECT_BY_NAME["Uniswap"]]
    bw._ONE_OFF_IN_SERIES.clear()
    try:
        bw._holder_value_views(groups)
        rows = bw.flow_statements(groups, asof)
    finally:
        bw._SCOPE = keep
    val = {r["Line"]: r for r in rows if r["Statement"] != "Token flow"}
    assert val["Revenue (every stream)"]["Q-4"] == 0.0 and val["Holder value CREDITED (revenue-funded)"]["Q-4"] == 0.0
    assert val["Status"]["Q-4"].startswith("OK"), val["Status"]["Q-4"]


def test_6_the_metrics_doc_names_the_credit_start():
    txt = open(config.__file__.replace("config.py", "METRICS.md"), encoding="utf-8").read()
    assert "Funding: none before 2025-12-27 (0 credited:" in txt
