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
