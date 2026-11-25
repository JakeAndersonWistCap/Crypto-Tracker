"""Round 21b (lead): Jake's dated approval of NEAR's December P2P history (item 4); the keyless beacon node as the
standing staked-ETH source (item 5)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import near_p2p_history as nh  # noqa: E402

NOV = (date(2025, 11, 1), date(2025, 11, 30))


def _r(today, approval=None, gb=226.2, done=0, explicit=True):
    return nh.refusal(explicit, today, approval or config.NEAR_P2P_HISTORY_APPROVAL, NOV, gb, done,
                      config.NEAR_P2P_HISTORY)


def test_jakes_approval_covers_december_2026_only_within_the_months_room():
    a = config.NEAR_P2P_HISTORY_APPROVAL
    assert a["approved"] is True and a["approved_on"] == "2026-11-25" and a["for_month"] == "2026-12"
    # the month's room in the combined schedule (1,000 GB budget, inside the 1,099.5 GB free tier)
    assert a["max_gb"] == 337.0 and config.BIGQUERY_SCHEDULE["budget_gb"] <= config.BIGQUERY_SCHEDULE["free_tier_gb"]
    assert config.BIGQUERY_SCHEDULE["months"]["2026-12"]["near_gb"] <= a["max_gb"]
    assert _r("2026-12-01") is None and _r("2026-12-31") is None
    assert _r("2026-11-30") is not None                                   # never before December
    assert "2026-12 only" in _r("2027-01-02")                             # one month, never the next
    assert "over the 337.0 GB" in _r("2026-12-05", gb=337.1)              # judged at its edge
    assert "already taken 1 chunk(s)" in _r("2026-12-05", done=1)        # the schedule's one chunk for 2026-12
    assert "only from its own command" in _r("2026-12-05", explicit=False)


def test_an_approval_naming_a_month_before_its_date_is_refused():
    bad = dict(config.NEAR_P2P_HISTORY_APPROVAL, approved_on="2026-12-01", for_month="2026-11")
    assert "a month before it" in _r("2026-12-02", approval=bad)


def test_the_keyless_beacon_node_is_the_standing_source_and_beaconchain_is_not_asked_even_with_a_key():
    from fetch.staked_eth import BeaconchainEth
    spec = config.PROJECT_BY_NAME["Ethereum"]["staked_eth_beaconchain"]
    why = BeaconchainEth(key="a-key").blocked(spec)
    assert why and why[0] == "skipped" and "keyless public beacon node is the standing" in why[1]
    assert config.SOURCE_REGISTER["beaconcha.in"]["status"] == "NOT USED (R21b)"
    key = next(w for k, w, _t in config.ENV_KEYS if k == "BEACONCHAIN_API_KEY")
    assert key.startswith("nothing:") and "trial" not in key.lower().replace("a 30-day trial or a paid plan", "")


def test_a_buyback_input_of_a_project_that_buys_nothing_is_na_not_missing_history():
    """R21b: Ethereum's new A3 tab read 'MISSING [C — NEEDS HISTORY] — actual_buyback_tokens is stored from …' off the
    stale ':as-buyback' copies; its buyback route is 'none' (nothing is bought), so the cell is N/A by that decision."""
    import pandas as pd
    import coverage_audit as ca
    g = pd.DataFrame({"date": pd.date_range("2026-08-25", "2026-09-27"), "value": 1.0})
    row = {"Project": "Ethereum", "Tab": "A3 Revenue Buyback", "Column": "Net absorption Q0 (tokens)",
           "Reason / route / error": "actual_buyback_tokens (q0) covers 34 days", "Formula as built": ""}
    cls, why = ca.missing_class(row, None, {("Ethereum", "actual_buyback_tokens"): g}, pd.Timestamp("2026-10-13"))
    assert cls == "N/A" and "NOTHING IS BOUGHT" in why
    # a project that does buy keeps the history class
    row2 = dict(row, Project="Pendle")
    cls2, _ = ca.missing_class(row2, None, {("Pendle", "actual_buyback_tokens"): g}, pd.Timestamp("2026-10-13"))
    assert cls2 != "N/A"
