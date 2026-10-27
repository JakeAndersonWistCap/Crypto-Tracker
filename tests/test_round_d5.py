"""ROUND D5 (Jake 2026-10-27, head 9b0bf84): data collection for the 14 projects.

0 a DefiLlama restatement older than the 30-day re-read reaches the store (and is logged) — a wrong stored number is
fixed first. 1a Aethir's monthly revenue, spread over each month's days, is the weekly series' history. 1d the Plume
settlement seed lifts the native-leg window only for that invocation. 2a Ethereum's consensus rewards before Etherscan's
first read are MEASURED (ultrasound supply + burn), the formula MODELLED only where nothing is measured. 3b Sky's 500M is a
gift, not a grant.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402


def _frame(project, metric, days, values, source="defillama"):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": project, "metric": metric,
                         "value": [float(v) for v in values], "source": source, "tier": 1})


# ================================================================================================== 0
def test_0_the_defillama_fee_family_keeps_every_day_the_call_returned(monkeypatch):
    import fetch.base as fb
    monkeypatch.setattr(fb, "today", lambda: pd.Timestamp("2026-10-27"))
    days = pd.date_range("2025-10-01", "2026-10-26")
    for m in ("revenue_usd", "fees_usd", "holders_revenue_usd", "revenue_usd_defillama"):
        out = fb.window(_frame("Fluid", m, days, range(len(days))), 30)
        assert len(out) == len(days), m
    # anything else keeps the routine trim: another metric, or the same metric from another source
    assert len(fb.window(_frame("Fluid", "tvl_usd", days, range(len(days))), 30)) == 30
    assert len(fb.window(_frame("Fluid", "revenue_usd", days, range(len(days)), source="defillama:x"), 30)) == 30


def test_0_a_restated_quarter_is_logged_before_the_store_takes_the_new_figure(tmp_path):
    import store as sm
    import token_metrics as tm
    st = sm.Store(str(tmp_path / "m.db"))
    q4 = pd.date_range("2025-10-01", "2025-12-31")
    q1 = pd.date_range("2026-01-01", "2026-03-31")
    st.upsert(_frame("Fluid", "revenue_usd", q4.append(q1), [100.0] * len(q4) + [50.0] * len(q1)))
    new = _frame("Fluid", "revenue_usd", q4.append(q1), [90.0] * len(q4) + [50.1] * len(q1))
    lines = tm.restatement_lines(st, new)
    assert len(lines) == 1                                  # Q1 moved 0.2%: under the 0.5% line
    assert lines[0][0] == "Fluid"
    assert lines[0][1].startswith("RESTATED BY THE SOURCE: Fluid revenue_usd 2025Q4: stored 9,200 → now 8,280 (-10.0%)")
    st.upsert(new)
    assert st.conn.execute("SELECT value FROM metrics WHERE project='Fluid' AND date='2025-11-15'").fetchone()[0] == 90.0
    # another source's rows are never compared or logged
    assert tm.restatement_lines(st, _frame("Fluid", "revenue_usd", q4, [1.0] * len(q4), source="other")) == []
    st.close()


# ================================================================================================== 1a
MONTHLY = "aethir_page:protocol/demand-metric.monthlyNetworkRevenue.earning"
WEEKLY = "aethir_page:protocol/demand-metric.weeklyNetworkRevenue.amount"


def _aethir_groups():
    months = [("2026-04-01", 3_000_000.0), ("2026-05-01", 3_100_000.0), ("2026-06-01", 3_000_000.0)]
    mon = pd.DataFrame({"date": pd.to_datetime([d for d, _ in months]), "project": "Aethir",
                        "metric": "customer_revenue_monthly_usd", "value": [v for _, v in months], "tier": 3,
                        "source": MONTHLY})
    weeks = pd.date_range("2026-06-15", periods=4, freq="7D")
    wk = pd.DataFrame({"date": weeks, "project": "Aethir", "metric": "customer_revenue_usd", "value": 700_000.0,
                       "tier": 3, "source": WEEKLY})
    return {("Aethir", "customer_revenue_monthly_usd"): mon, ("Aethir", "customer_revenue_usd"): wk}


def test_1a_the_months_before_the_weekly_series_are_spread_over_their_days():
    import build_workbook as bw
    groups = _aethir_groups()
    bw._history_prepend_views(groups)
    g = groups[("Aethir", "customer_revenue_usd")]
    g = g.assign(date=pd.to_datetime(g["date"]))
    spread = g[g["source"].str.contains(bw.MONTH_SPREAD_TAG, regex=False)]
    assert spread["date"].min() == pd.Timestamp("2026-04-01") and spread["date"].max() == pd.Timestamp("2026-06-14")
    assert len(spread) == 30 + 31 + 14                      # every day, one row each; none on/after the first week
    assert abs(spread[spread["date"].dt.month == 4]["value"].sum() - 3_000_000) < 1e-6
    assert abs(spread[spread["date"].dt.month == 5]["value"].sum() - 3_100_000) < 1e-6
    assert abs(spread[spread["date"].dt.month == 6]["value"].sum() - 3_000_000 * 14 / 30) < 1e-6
    assert (g[g["source"] == WEEKLY]["value"] == 700_000.0).all() and (g["source"] == WEEKLY).sum() == 4
    # one declared handover, monthly then weekly: the guard reads both points as the declared pair
    decl = config.declared_handover("Aethir", "customer_revenue_usd")
    assert decl["ordered_points"] == (MONTHLY, WEEKLY)
    from fetch.base import _measuring_point
    assert set(g["source"].map(_measuring_point)) == {MONTHLY, WEEKLY}
    # per day: a spread row is one day; a weekly row is still spread over its seven days
    pdays = bw._per_day(g)
    assert abs(pdays[pd.Timestamp("2026-05-10")] - 3_100_000 / 31) < 1e-6
    assert abs(pdays[pd.Timestamp("2026-06-16")] - 100_000) < 1e-6


def test_1a_no_monthly_rows_means_no_history_and_the_weekly_series_is_untouched():
    import build_workbook as bw
    groups = _aethir_groups()
    groups.pop(("Aethir", "customer_revenue_monthly_usd"))
    before = groups[("Aethir", "customer_revenue_usd")].copy()
    bw._history_prepend_views(groups)
    pd.testing.assert_frame_equal(groups[("Aethir", "customer_revenue_usd")], before)


# ================================================================================================== 2a
def _eth_groups(ultra_from="2026-09-01"):
    def g(metric, days, vals, src):
        return pd.DataFrame({"date": pd.to_datetime(days), "project": "Ethereum", "metric": metric,
                             "value": [float(v) for v in vals], "tier": 1, "source": src})
    stake_days = pd.date_range("2026-08-01", "2026-10-10")
    ud = pd.date_range(ultra_from, "2026-10-10")
    supply = [120_000_000 + 2_600 * i for i in range(len(ud))]           # +2,600 a day net of the burn
    bd = pd.date_range("2026-08-01", "2026-10-10")
    meas_days = pd.date_range("2026-09-30", "2026-10-10")
    return {
        ("Ethereum", "beacon_chain_eth"): g("beacon_chain_eth", stake_days, [34_000_000] * len(stake_days),
                                           "validatorqueue:staked_amount"),
        ("Ethereum", "total_supply_ultrasound"): g("total_supply_ultrasound", ud, supply, "ultrasound:supply-over-time"),
        ("Ethereum", "gross_burn_tokens"): g("gross_burn_tokens", bd, [40.0] * len(bd),
                                            "derived:defillama_burned_fee_revenue/price"),
        ("Ethereum", "consensus_rewards_tokens"): g("consensus_rewards_tokens", meas_days, [2_640.0] * len(meas_days),
                                                   "etherscan:ethsupply2.Eth2Staking:delta"),
    }


def test_2a_the_days_ultrasound_covers_are_measured_and_the_formula_only_fills_before_them():
    import build_workbook as bw
    groups = _eth_groups()
    bw._consensus_history_views(groups, pd.Timestamp("2026-10-10"))
    g = groups[("Ethereum", "consensus_rewards_tokens")].assign(date=lambda x: pd.to_datetime(x["date"]))
    measured = g[g["source"].str.startswith(bw.CONSENSUS_MEASURED_POINT)]
    modelled = g[g["source"].str.startswith(bw.CONSENSUS_HISTORY_POINT)]
    # ultrasound's point for day D is stamped D+1 (its first point, 09-01, is the end of 08-31); a day's issuance needs
    # the end of the day before too, so the first measured day is 09-01 and the last before Etherscan 09-29
    assert measured["date"].min() == pd.Timestamp("2026-09-01") and measured["date"].max() == pd.Timestamp("2026-09-29")
    assert (abs(measured["value"] - 2_640.0) < 1e-6).all()            # d(supply) 2,600 + burn 40
    assert modelled["date"].max() < measured["date"].min()            # never interleaved
    assert modelled["source"].str.contains("MODELLED").all() and not measured["source"].str.contains("MODELLED").any()
    from fetch.base import _measuring_point
    order = config.declared_handover("Ethereum", "consensus_rewards_tokens")["ordered_points"]
    assert [_measuring_point(x) for x in order] == ["derived:consensus_formula", "ultrasound:issuance",
                                                     "etherscan:ethsupply2.Eth2Staking"]
    assert set(g["source"].map(_measuring_point)) <= set(order)


def test_2a_without_ultrasound_history_the_modelled_leg_is_unchanged():
    import build_workbook as bw
    groups = _eth_groups()
    groups.pop(("Ethereum", "total_supply_ultrasound"))
    bw._consensus_history_views(groups, pd.Timestamp("2026-10-10"))
    g = groups[("Ethereum", "consensus_rewards_tokens")]
    assert not g["source"].str.startswith("ultrasound").any()
    assert pd.to_datetime(g[g["source"].str.startswith("derived:consensus_formula")]["date"]).max() == \
        pd.Timestamp("2026-09-29")


def test_2a_ethstore_days_and_fields():
    import check_offline_items as coi
    # ETH.STORE day 1 runs 2020-12-02 12:00 -> 12-03 12:00; UTC day 2020-12-02 is half of day 0 and half of day 1
    assert coi.ethstore_day_pair("2020-12-02") == (0, 1)
    assert coi.ethstore_consensus_eth({"consensus_rewards_sum_wei": "2650000000000000000000"}) == 2650.0
    assert coi.ethstore_consensus_eth({"consensus_rewards_sum_gwei": 2_650_000_000_000}) == 2650.0
    assert coi.ethstore_consensus_eth({"apr": 0.03}) is None                       # no field: never a guess
    assert coi.ethstore_consensus_eth({"consensus_rewards_sum": 5}) is None        # no unit: never a guess


def test_2a_ethstore_check_runs_only_by_name_and_never_shows_the_key(monkeypatch, tmp_path, capsys):
    import check_offline_items as coi
    import store as sm
    db = tmp_path / "m.db"
    st = sm.Store(str(db))
    for (_, m), g in _eth_groups().items():
        st.upsert(g)
    st.close()
    monkeypatch.setattr(sm, "DB_PATH", str(db))
    monkeypatch.setenv("BEACONCHAIN_API_KEY", "SECRETKEY123")
    monkeypatch.setattr(coi, "ETHSTORE_PAUSE_S", 0)
    monkeypatch.setattr(coi, "_RUN_BY_NAME", False)
    coi.ethstore_check()
    assert "skipped in the run-everything mode" in capsys.readouterr().out
    calls = []

    class R:
        status_code, content, headers = 200, b"x", {}

        def __init__(self, idx):
            self.idx = idx

        def json(self):
            return {"data": {"day": self.idx, "consensus_rewards_sum_wei": str(2_640 * 10 ** 18)}}

    def get(self, url, headers=None, timeout=None):
        calls.append((url, headers))
        return R(int(url.rsplit("/", 1)[1]))
    monkeypatch.setattr(coi.requests.Session, "get", get)
    monkeypatch.setattr(coi, "_RUN_BY_NAME", True)
    coi.ethstore_check()
    out = capsys.readouterr().out
    assert "2026-09-15" in out and "+0.00%" in out
    assert len(calls) == 2 and all(h == {"apikey": "SECRETKEY123"} for _, h in calls)
    assert not any("SECRETKEY123" in u for u, _ in calls) and "SECRETKEY123" not in out
