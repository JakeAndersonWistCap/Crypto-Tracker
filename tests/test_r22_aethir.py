"""Round 22 — Aethir: the staking yield wired (Jake 2026-11-25, item 4; SPEC "Round 22 — Aethir staking yield").

"Aethir uniformity: wire the staking yield. Aethir's docs give 1,000,000 ATH a week for each of the two pools (Gaming
and AI), so 2M a week, about 104M a year, paid from the issuer Safe. Count it as value to stakers and as emissions; it
nets out. That clears UNIFORMITY. A3 still counts only the fee burn."
1. The reward series: the measured faucet payouts from their first stored day, the published schedule's rise before.
2. Value to stakers = the same tokens x the same day's price; in holder value yield, never in A3's numerator.
3. The staker yield, whole-token yield and UNIFORMITY on the fixture dump (as of 2026-10-13)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402


def _g(project, metric, s, source="test"):
    return pd.DataFrame({"date": s.index, "project": project, "metric": metric, "value": s.values, "tier": 2,
                         "source": source, "fetched_at": "2026-11-25T00:00:00"})


# ============================================================================================ 1. the reward series
def test_measured_payouts_replace_the_schedule_from_their_first_day():
    decl = pd.Series(2_000_000.0, index=pd.date_range("2026-08-06", periods=6, freq="7D"))
    meas = pd.Series(0.0, index=pd.date_range("2026-08-20", "2026-09-12"))
    meas[pd.Timestamp("2026-08-21")] = 1_000_000.0
    meas[pd.Timestamp("2026-08-22")] = 1_000_000.0
    s, which = bw.staker_reward_series(meas, decl)
    assert which == "declared then measured"
    # the schedule's rows before 08-20 (08-06, 08-13), then every measured day (zeros measured)
    assert list(s.index[:2]) == [pd.Timestamp("2026-08-06"), pd.Timestamp("2026-08-13")]
    assert s.sum() == pytest.approx(2 * 2_000_000 + 2_000_000)
    assert bw.staker_reward_series(pd.Series(dtype=float), decl)[1] == "declared"
    assert bw.staker_reward_series(meas, pd.Series(dtype=float))[1] == "measured"


def test_the_docs_figure_is_the_cross_check_on_13_measured_weeks():
    days = pd.date_range("2026-06-01", "2026-11-24")
    meas = pd.Series(0.0, index=days)
    meas[meas.index.dayofweek == 3] = 2_000_000.0                       # 1,000,000 per pool, one day a week
    txt = bw.staker_reward_crosscheck(meas, 2_000_000.0)
    assert "= 1.000x the declared 2,000,000 a week" in txt and "26,000,000" in txt
    assert bw.staker_reward_crosscheck(meas[-30:], 2_000_000.0) == ""   # under 13 weeks: no ratio


def test_the_log_scan_reads_the_two_faucets_into_their_distributors():
    sc = next(s for s in config.PROJECT_BY_NAME["Aethir"]["log_scans"] if s["key"] == "staker_faucet_out")
    rf = config.AETHIR_STAKING_POOLS["reward_funding"]
    assert sc["direction"] == "out" and sc["store"] and sc["metric"] == "staker_rewards_faucet_tokens"
    assert set(sc["holders"]) == {rf["ai_pool"]["faucet"], rf["gaming_pool"]["faucet"]}
    assert set(sc["count_to"]) == {rf["ai_pool"]["distributor"], rf["gaming_pool"]["distributor"]}
    assert config.log_scan_declared("Aethir", "staker_rewards_faucet_tokens") == "staker_faucet_out"
    assert config.STAKER_REWARD_VIEWS[("Aethir", "staker_rewards_tokens")]["declared_weekly"] == 2_000_000.0
    assert 2_000_000.0 * 365.25 / 7 == pytest.approx(104_357_142.86, abs=0.01)    # "about 104M a year"


# ============================================================================================ 2. both sides, A3 untouched
def test_value_to_stakers_and_emissions_are_the_same_dollars_and_a3_counts_the_burn_only(monkeypatch):
    days = pd.date_range("2026-11-02", "2026-11-15")
    px = pd.Series([0.006 + 0.0001 * i for i in range(len(days))], index=days)
    burn = pd.Series(100_000.0, index=days)
    sched = pd.Series(2_000_000.0, index=[pd.Timestamp("2026-11-05"), pd.Timestamp("2026-11-12")])
    meas = pd.Series(0.0, index=pd.date_range("2026-11-10", "2026-11-15"))
    meas[pd.Timestamp("2026-11-13")] = 2_000_000.0
    groups = {("Aethir", "price_usd"): _g("Aethir", "price_usd", px),
              ("Aethir", "gross_burn_tokens"): _g("Aethir", "gross_burn_tokens", burn),
              ("Aethir", "staker_rewards_paid_tokens"): _g("Aethir", "staker_rewards_paid_tokens", sched,
                                                           "derived:stock_rise[staker_rewards_emitted]"),
              ("Aethir", "staker_rewards_faucet_tokens"): _g("Aethir", "staker_rewards_faucet_tokens", meas,
                                                             "explorer:staker_faucet_out")}
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME["Aethir"]])
    bw._staker_reward_views(groups)
    bw._outflow_views(groups)
    bw._holder_value_views(groups)
    bw._revenue_to_holders_views(groups)
    tok = bw._daily(groups[("Aethir", "staker_rewards_tokens")])
    # 11-05 from the schedule (before the scan's first day 11-10); 11-12 dropped (the scan covers it); 11-13 measured
    assert tok.sum() == pytest.approx(4_000_000.0) and tok[pd.Timestamp("2026-11-05")] == 2_000_000.0
    assert pd.Timestamp("2026-11-12") in tok.index and tok[pd.Timestamp("2026-11-12")] == 0.0
    src = groups[("Aethir", "staker_rewards_tokens")].set_index("date")["source"]
    assert src[pd.Timestamp("2026-11-05")].startswith("derived:stock_rise")
    assert src[pd.Timestamp("2026-11-13")] == "explorer:staker_faucet_out"
    val = bw._daily(groups[("Aethir", "holder_value_staking_usd")])
    out_a = bw._daily(groups[("Aethir", "outflow_a_usd")])
    want = 2_000_000.0 * px[pd.Timestamp("2026-11-05")] + 2_000_000.0 * px[pd.Timestamp("2026-11-13")]
    assert val.sum() == pytest.approx(want) and out_a.sum() == pytest.approx(want)     # it nets out, to the cent
    # A3's numerator and the credited holder value: the fee burn x price, nothing of the staking emission
    a3 = bw._daily(groups[("Aethir", bw.A3_KEY_METRIC)])
    hv = bw._daily(groups[("Aethir", "holder_value_revenue_usd")])
    assert a3.sum() == pytest.approx((burn * px).sum()) and hv.sum() == pytest.approx((burn * px).sum())
    assert bw.a3_key_numerator("Aethir") == "revenue_to_holders_usd"
    assert "holder_value_staking_usd" not in str(config.REVENUE_SPLIT["Aethir"]["holder_revenue"])


def test_the_switches():
    assert config.OUTFLOW_CATEGORIES["Aethir"]["a"] == ("staker_rewards_tokens",)
    vy = config.VALIDATOR_YIELD["Aethir"]
    assert vy["method"] == "issuance_share" and vy["stake_metric"] == "aethir_reward_pools_staked_tokens"
    assert config.SET_SUM_VIEWS[("Aethir", "aethir_reward_pools_staked_tokens")]["parts"] == (
        "locked_tokens_ai_onchain", "locked_tokens_gaming_onchain")
    assert config.declared_na_text("Aethir", "UNIFORMITY = whole-token yield") is None
    assert "Aethir" not in config.PROTOCOL_YIELD                       # never a revenue share (A3's protocol yield)
    assert set(config.STAKER_EMISSION_VALUE) == {"Aethir"}


# ============================================================================================ 3. the fixture's numbers
def test_the_fixture_numbers():
    """The Aethir fixture build (fixtures/real_aethir.json, as of 2026-10-13; built before/after in R22):
    staker yield 0.136044181 = 9 schedule rises x 2,000,000 over 61 covered days x 365 ÷ the two pools' 791,690,739.17
    ATH on 10-13; whole-token yield 0.00447771399 = that x 791,690,739.17 ÷ circulating 24,053,550,151; UNIFORMITY
    0.0329136753 = the staked share (was: n/a — no staker yield series is wired)."""
    rows = json.loads((ROOT / "fixtures" / "real_aethir.json").read_text())["metrics"]
    last = {}
    for r in rows:
        if r["metric"] in ("locked_tokens_ai_onchain", "locked_tokens_gaming_onchain") and r["date"] == "2026-10-13":
            last[r["metric"]] = r["value"]
    stake = sum(last.values())
    assert stake == pytest.approx(791_690_739.17, abs=0.01)
    sched = sorted((r["date"], r["value"]) for r in rows if r["metric"] == "staker_rewards_emitted")
    rises = [b[1] - a[1] for a, b in zip(sched, sched[1:]) if "2026-07-16" <= b[0] <= "2026-10-13"]
    assert rises == [2_000_000.0] * 9
    sy = sum(rises) / 61 * 365 / stake
    assert sy == pytest.approx(0.136044181, abs=1e-9)
    circ = 24_053_550_151.0
    assert sy * stake / circ == pytest.approx(0.00447771399, abs=1e-11)
    assert stake / circ == pytest.approx(0.0329136753, abs=1e-10)
