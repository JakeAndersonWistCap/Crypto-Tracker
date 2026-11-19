"""Round 14 (Jake 2026-11-19): the R13 decisions taken in this round, beside Ether.fi's own two files
(test_r14_etherfi_supply.py, test_r14_etherfi_revenue.py):
  D2  Aerodrome purchases no longer held are SUBTRACTED from held and retired as a named negative row
  D5  Near's stage-2 approval covers the calendar month it is dated in, and no other
  D6  three Near labels reworded to the rule in force (a declared label change)
  E   Ether.fi's modelled team leg is an unlock stock: its monthly falls are releases in (e), as Morpho's relock
"""
import pandas as pd
import pytest

import build_workbook as bw
import config

COLS = ["date", "project", "metric", "value", "tier", "source"]


def _frame(metric, vals, project="Aerodrome", source="derived:test"):
    return pd.DataFrame({"date": pd.to_datetime(list(vals)), "project": project, "metric": metric,
                         "value": [float(v) for v in vals.values()], "tier": 2, "source": source})[COLS]


def _ser(g, key):
    x = g[key]
    return x.assign(date=pd.to_datetime(x["date"])).set_index("date")["value"].astype(float).sort_index()


# ----- D2 ------------------------------------------------------------------------------------------------------------
def test_d2_the_row_is_minus_the_days_change_of_the_shortfall():
    nh = pd.Series([0.0, 0.0, 5.0, 5.0, 3.0], index=pd.date_range("2026-11-01", periods=5))
    ch = bw.foundation_shortfall_change(nh)
    assert list(ch.index.strftime("%m-%d")) == ["11-03", "11-05"]
    assert ch.tolist() == [-5.0, 2.0]                           # found missing on 11-03; 2 came back on 11-05
    assert ch.sum() == pytest.approx(-float(nh.iloc[-1]))       # the rows add up to minus the shortfall now
    first = bw.foundation_shortfall_change(pd.Series([4.0], index=[pd.Timestamp("2026-11-01")]))
    assert first.tolist() == [-4.0]                             # a shortfall on the first day read is its own row
    assert bw.foundation_shortfall_change(pd.Series([0.0, 0.0], index=pd.date_range("2026-11-01", periods=2))).empty


def test_d2_held_and_retired_fall_on_the_day_and_usd_needs_the_days_price():
    days = {"2026-11-01": 10.0, "2026-11-02": 10.0, "2026-11-03": 10.0}
    g = {("Aerodrome", "buyback_held_tokens"): _frame("buyback_held_tokens", days),
         ("Aerodrome", "retired_buyback_tokens"): _frame("retired_buyback_tokens", days),
         ("Aerodrome", "retired_buyback_usd"): _frame("retired_buyback_usd", {k: 2 * v for k, v in days.items()}),
         ("Aerodrome", "price_usd"): _frame("price_usd", {"2026-11-02": 0.5})}
    nh = pd.Series([0.0, 3.0, 4.0], index=pd.to_datetime(list(days)))
    info = {}
    bw._foundation_shortfall_rows(g, "Aerodrome", nh, "foundation_buyback_shortfall_tokens", COLS, info)
    assert info["shortfall_rows"] == 2
    row = _ser(g, ("Aerodrome", "foundation_buyback_shortfall_tokens"))
    assert row.tolist() == [0.0, -3.0, -1.0]
    for m in ("buyback_held_tokens", "retired_buyback_tokens"):
        assert _ser(g, ("Aerodrome", m)).tolist() == [10.0, 7.0, 9.0], m
        assert g[("Aerodrome", m)]["source"].str.contains("foundation_buyback_shortfall_tokens").sum() == 2
    usd = _ser(g, ("Aerodrome", "retired_buyback_usd"))
    assert usd[pd.Timestamp("2026-11-02")] == pytest.approx(20.0 - 3.0 * 0.5)
    assert pd.Timestamp("2026-11-03") not in usd.index          # no price that day: unknown, never the old figure


def test_d2_aerodrome_subtracts_and_the_flag_says_so():
    fb = config.PROJECT_BY_NAME["Aerodrome"]["foundation_buyback"]
    assert fb["held_check"]["subtract_metric"] == "foundation_buyback_shortfall_tokens"
    assert config.METRICS["foundation_buyback_shortfall_tokens"]["sanity_min"] < 0
    saved = dict(bw.FOUNDATION_BURN)
    try:
        bw.FOUNDATION_BURN["Aerodrome"] = {"not_held_now": 12.0, "not_held_day": pd.Timestamp("2026-11-18"),
                                           "shortfall_rows": 1}
        assert any("SUBTRACTED" in x for x in bw.foundation_flags("Aerodrome", "retired_buyback_tokens"))
        bw.FOUNDATION_BURN["Aerodrome"] = {"not_held_now": 12.0, "not_held_day": pd.Timestamp("2026-11-18")}
        assert any("NOT subtracted" in x for x in bw.foundation_flags("Aerodrome", "retired_buyback_tokens"))
    finally:
        bw.FOUNDATION_BURN.clear()
        bw.FOUNDATION_BURN.update(saved)


# ----- D5 ------------------------------------------------------------------------------------------------------------
def test_d5_an_approval_covers_its_own_calendar_month_only():
    r = config.near_stage2_refusal
    ok = {"approved": True, "approved_by": "Jake", "approved_on": "2026-12-02"}
    assert r(True, "2026-12-31", "2026-12-01", ok) is None                     # the same month, to its last day
    late = r(True, "2027-01-01", "2026-12-01", ok)
    assert late is not None and "2026-12" in late and "2027-01" in late       # the next month needs a new approval
    assert r(True, "2027-12-05", "2026-12-01", ok) is not None                 # the same month of a later year too


# ----- D6 ------------------------------------------------------------------------------------------------------------
def test_d6_the_three_near_labels_describe_the_rule_in_force():
    fa = config.METRICS["near_foundation_accounts_tokens"]["label"]
    assert "in circulating" in fa and "off the free float" in fa
    un = config.METRICS["near_lockup_unvested_tokens"]["label"]
    assert "where idle" in un and "off the float" in un
    hdr = [r["header"] for r in config.DETAIL_COLUMNS if r.get("cells", {}).get("Near") == ("near_lockup_unvested_tokens",
                                                                                          "now")]
    assert len(hdr) == 1 and "where idle" in hdr[0] and "off the float where staked" in hdr[0]


# ----- E -------------------------------------------------------------------------------------------------------------
def test_e_the_modelled_team_leg_is_an_unlock_stock_beside_the_wallets():
    legs = config.unlock_stock_metrics("Ether.fi")
    assert legs[-1] == "etherfi_team_unvested_modelled_tokens"
    assert set(config.CIRCULATING_ONCHAIN["Ether.fi"]["subtract"]) <= set(legs)
    # the leg falls by 214.7M / 36 on each 18th: that fall is what the unlock view reads as a release
    a, b = config.etherfi_team_unvested_on("2026-11-17"), config.etherfi_team_unvested_on("2026-11-18")
    assert a - b == pytest.approx(214_700_000 / 36)
