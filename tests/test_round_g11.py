"""
tests/test_round_g11.py — Round G11, "plumbing only, no new data" (Jake 2026-10-21), SPEC §19 "G11". Every fix uses rows
already in metrics.db or values already in config: workbook mechanics (A), wrong plumbing (B), stored but not shown (C),
labels and statuses (D), and the one Aerodrome guard (E).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402


def _g(project, metric, days, values, source="x"):
    return pd.DataFrame({"date": pd.to_datetime(list(days)), "project": project, "metric": metric,
                         "value": [float(v) for v in values], "tier": 1, "source": source, "is_manual": False,
                         "entered_on": ""})


def _days(a, b):
    return list(pd.date_range(a, b))


# ================================================================================================== A workbook mechanics
def test_a1_every_headline_formula_cell_carries_its_calculated_value(tmp_path, monkeypatch):
    """A1: after the save, every formula on Master / A1–A4 / CP holds a cached value (no blank for a reader that does
    not recalculate — openpyxl data_only, the guard, Credibility CONTINUITY)."""
    import workbook_values
    from test_trajectory import _built
    monkeypatch.setenv("TOKEN_METRICS_STORE_VALUES", "1")
    wb, _rows = _built(tmp_path, monkeypatch, "Chainlink")
    assert bw.WORKBOOK_VALUES.get("formulas", 0) > 100 and not bw.WORKBOOK_VALUES.get("error")
    sheets = [s for s in wb.sheetnames if s == "Master" or s[:2] in ("A1", "A2", "A3", "A4") or s.startswith("CP")]
    assert len(sheets) >= 5
    assert workbook_values.missing_values(tmp_path / "wb.xlsx", sheets) == []


def test_a2_guard_asof_is_never_after_today_and_scope_is_the_portfolio():
    import inspect

    import measuring_guard as mg
    src = inspect.getsource(mg.live_check)
    assert "WHERE date <= ?" in src and "read_portfolio" in src and "only=scope or None" in src


def test_a2_continuity_names_what_it_lost():
    import inspect
    assert '"held"' in inspect.getsource(__import__("credibility").continuity_row)
    src = inspect.getsource(bw.write_credibility)
    assert "LOST NOW" in src and "every held headline still reads a number" in src


def test_a3_q4_without_a_split_reads_the_declared_text():
    src = Path(ROOT / "build_workbook.py").read_text(encoding="utf-8")
    assert "n/a — no split for this window (Config & Sources resolves Q0..Q-3)" in src


def test_a4_a_monthly_series_is_stale_only_after_its_own_cadence():
    month_ends = pd.date_range("2026-01-31", "2026-09-30", freq="ME")
    g = _g("Maple", "retired_buyback_usd", month_ends, [1.0] * len(month_ends))
    assert bw._observed_cadence(g) == "monthly"
    assert bw._stale_limit("Maple", "retired_buyback_usd", 7, g) >= 40
    fortnights = pd.date_range("2026-07-01", "2026-10-06", freq="14D")
    weekly = _g("Pendle", "x", fortnights, [1.0] * len(fortnights))
    assert bw._observed_cadence(weekly) == "weekly"


def test_a5a_a_successful_read_clears_the_login_row():
    from fetch.near_bigquery import login_needed
    assert not login_needed({"needed_on": "2026-10-21", "ok_on": "2026-10-21"})
    assert login_needed({"needed_on": "2026-10-21", "ok_on": "2026-10-20"})


def test_a5b_a_gap_whose_series_is_current_is_answered_by_the_store(monkeypatch):
    monkeypatch.setitem(bw._WINDOWS, "q0", ("2026-07-16", "2026-10-13"))
    monkeypatch.setattr(bw, "_LAST_GROUPS", {("Plume", "fees_usd"): _g("Plume", "fees_usd", _days("2026-10-01", "2026-10-12"),
                                                                       [1.0] * 12)})
    assert bw._gap_answered_by_store("Plume", "fees_usd")
    assert not bw._gap_answered_by_store("Plume", "[open] something")


# ==================================================================================================== B wrong plumbing
def test_b1_the_retro_burn_is_matched_to_the_treasury_fall_not_released():
    spec = config.PROJECT_BY_NAME["Uniswap"]["one_off_flows"][0]
    assert spec["from_stock"] == "treasury_holding_tokens" and spec["tokens"] == 100_000_000
    days = _days("2025-12-24", "2025-12-30")
    stock = [500e6, 500e6, 500e6, 395e6, 395e6, 395e6, 395e6]           # 105M fall on 12-27: 100M burn + 5M release
    groups = {("Uniswap", "treasury_holding_tokens"): _g("Uniswap", "treasury_holding_tokens", days, stock,
                                                         "chain:ethereum:treasury")}
    bw._ONE_OFF_UNMATCHED.clear()
    bw._unlock_release_view(groups, "Uniswap", "unlock_release_tokens", ("treasury_holding_tokens",))
    rel = bw._daily(groups[("Uniswap", "unlock_release_tokens")])
    assert abs(rel.sum() - 5e6) < 1 and not bw._ONE_OFF_UNMATCHED


def test_b2_maple_mints_are_configured_issuance_is_derived_and_the_release_leg_is_named():
    src = (config.ISSUER_MINTS["Maple"]).get("source", "")
    assert not config.orphaned_contract_keys("Maple", src)
    assert config.FLOW_RELEASE_LEGS["Maple"][0][1] == "unlock_release_tokens"
    days = _days("2026-10-07", "2026-10-11")
    groups = {("Maple", "total_supply"): _g("Maple", "total_supply", days,
                                            [1.245e9, 1.245e9, 1.245e9, 1.245e9 + 23_197_507, 1.245e9 + 23_197_507])}
    bw._issuance_from_supply_views(groups)
    iss = bw._daily(groups[("Maple", "gross_issuance_tokens")])
    assert abs(iss.sum() - 23_197_507) < 1 and "derived:d_supply[" in groups[("Maple", "gross_issuance_tokens")]["source"].iloc[0]


def test_b3_geodnet_ecosystem_wallet_is_already_out_of_circulating_once():
    """B3 NOT CHANGED: the Ecosystem wallet is inside treasury_holding_tokens (ecosystem_polygon), which circulating
    subtracts; subtracting ecosystem_holding_tokens too would count it twice. Releases stay disjoint."""
    p = config.PROJECT_BY_NAME["GEODNET"]["contracts"]
    eco = config.GEODNET_ECOSYSTEM_ALREADY_SUBTRACTED["wallet"]
    assert p["ecosystem_polygon"]["address"] == eco == p["ecosystem_stock_polygon"]["address"]
    assert p["ecosystem_polygon"].get("metric_override") is None          # it sums into treasury_holding_tokens
    sub = config.CIRCULATING_ONCHAIN["GEODNET"]["subtract"]
    assert "treasury_holding_tokens" in sub and "ecosystem_holding_tokens" not in sub
    assert eco not in config.mining_wallets("GEODNET") and eco in config.discretionary_wallets("GEODNET")


def test_b4_plume_release_leg_is_the_insider_unlocks():
    assert config.FLOW_RELEASE_LEGS["Plume"][0][1] == "insider_unlock_tokens"


def test_b5_the_rebuild_names_the_days_a_leg_lacks_and_the_365d_sum_says_where_it_stops(monkeypatch):
    spec = config.SETTLEMENT_REBUILD["ethereum_rebuild"]
    days = _days("2026-08-20", "2026-09-25")
    groups = {("Ethereum", spec["legs"]["dex"]): _g("Ethereum", spec["legs"]["dex"], days, [1.0] * len(days)),
              ("Ethereum", spec["legs"]["p2p"]): _g("Ethereum", spec["legs"]["p2p"], days, [1.0] * len(days)),
              ("Ethereum", spec["legs"]["nft"]): _g("Ethereum", spec["legs"]["nft"], [d for d in days if d != days[30]],
                                                    [1.0] * (len(days) - 1)),
              ("Ethereum", "settlement_volume_usd"): _g("Ethereum", "settlement_volume_usd", _days("2025-08-01", "2026-08-25"),
                                                        [1.0] * 390, "artemis")}
    bw._settlement_rebuild_views(groups)
    v = bw.SETTLEMENT_VALIDATION["Ethereum"]
    assert v["dropped_days"] == {"nft": [str(days[30].date())]} and "nft 1" in v["legs_end"]
    bw._nrr_views(groups, "Ethereum", "settlement_volume_usd", "settlement_volume_365d_usd", "network_reserve_ratio",
                  365, ("settlement_volume_365d_usd",), "short {have} {n} {last}")
    src = groups[("Ethereum", "settlement_volume_365d_usd")]["source"].iloc[-1]
    assert f"stops at {(days[30] - pd.Timedelta(days=1)).date()}" in src and str(days[30].date()) in src


def test_b6_the_modelled_consensus_leg_reaches_the_first_measured_read():
    src = Path(ROOT / "build_workbook.py").read_text(encoding="utf-8")
    assert "INSIDE THE HEADLINE WINDOW — fills the days before the first measured read (G11 B6)" in src


def test_b7_hyperliquid_buyback_is_the_fund_only_and_declared():
    assert config.buyback_route("Hyperliquid")["metric"] == "gross_burn_tokens"
    assert config.a1_burn_metric("Hyperliquid") == "total_burn_tokens"
    h = config.declared_handover("Hyperliquid", "actual_buyback_tokens")
    assert h["ordered_points"][1] == "hypercore_info:spotClearinghouseState"


def test_b8_the_circulating_handover_is_declared_and_its_overlap_checked():
    assert config.declared_handover("Hyperliquid", "circulating_supply_used")["overlap"]["b"] == \
        "circulating_supply_first_party"
    days = _days("2026-09-30", "2026-10-13")
    groups = {("Hyperliquid", "circulating_supply"): _g("Hyperliquid", "circulating_supply", days, [222.4e6] * 14),
              ("Hyperliquid", "circulating_supply_first_party"): _g("Hyperliquid", "circulating_supply_first_party",
                                                                    days, [238.6e6] * 14)}
    out = bw.handover_overlap(groups, "Hyperliquid", {"a": "circulating_supply", "b": "circulating_supply_first_party",
                                                      "tolerance_pct": 2.0, "min_days": 3})
    assert not out["passed"] and out["common_days"] == 14 and "NOT PASSED" in out["text"] and "+7.28%" in out["text"]
    assert "NET of the" in config.metric_label("Hyperliquid", "circulating_supply_first_party")
    assert "AS PUBLISHED" in config.metric_label("Hyperliquid", "circulating_supply_tokendetails")


def test_b9_pendle_buyback_is_the_measured_epoch_spread_over_its_days(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Pendle"]])
    ep = _g("Pendle", "pendle_distributed_tokens", ["2026-09-08", "2026-09-22"], [140_000, 0],
            "pendle_api:sPendleHistoricalData.buybackAmounts")
    px = _g("Pendle", "price_usd", _days("2026-09-01", "2026-10-13"), [2.0] * 43, "coingecko")
    der = _g("Pendle", "actual_buyback_tokens", _days("2026-09-01", "2026-10-13"), [9.0] * 43, bw.DERIVED_USD_OVER_PRICE)
    apr = _g("Pendle", "staking_apr_published", ["2026-09-29"], [0.0], "pendle_api:lastEpochApr")
    groups = {("Pendle", "pendle_distributed_tokens"): ep, ("Pendle", "price_usd"): px,
              ("Pendle", "actual_buyback_tokens"): der, ("Pendle", "staking_apr_published"): apr}
    bw._EARLY_BLOCKS.clear()
    bw._measured_buyback_views(groups)
    tok = bw._daily(groups[("Pendle", "actual_buyback_tokens")])
    assert abs(tok.sum() - 140_000) < 1e-6 and len(tok) == 14 and tok.index.max() == pd.Timestamp("2026-09-21")
    assert "publish lag" in groups[("Pendle", "actual_buyback_tokens")]["source"].iloc[0]
    assert abs(bw._daily(groups[("Pendle", "actual_buyback_usd")]).sum() - 280_000) < 1e-6
    assert ("Pendle", "actual_buyback_tokens_derived") in groups
    assert ("Pendle", "staking_apr_published") not in groups and "publish lag" in bw._EARLY_BLOCKS[("Pendle", "staking_apr_published")]
    assert config.stale_after_days("Pendle", "actual_buyback_tokens", 7) == 30


def test_b10_fluid_ends_at_its_last_execution_and_the_handover_is_declared():
    assert config.DECLARED_ZERO[("Fluid", "actual_buyback_tokens")]["since"] == "2026-03-21"
    assert "2026-03-20" in config.DECLARED_ZERO[("Fluid", "actual_buyback_usd")]["why"]
    assert config.declared_handover("Fluid", "actual_buyback_usd")["ordered_points"][1] == "declared:zero"


def test_b11_ether_fi_status_names_the_programmatic_buyback(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Ether.fi"]])
    data = pd.DataFrame([{"project": "Ether.fi", "metric": "programmatic_buyback_tokens", "now": 1_415_000.0,
                          "q0": 3e6, "latest_date": "2026-10-21"}])
    fs = config.PROJECT_BY_NAME["Ether.fi"].get("fee_split") or {}
    monkeypatch.setitem(config.PROJECT_BY_NAME["Ether.fi"], "fee_split", {**fs, "status": "active"})
    st = bw.derived_statuses(data, {"Ether.fi": "2026-09-02"})
    assert st["Ether.fi"]["buyback"].startswith("wallet buyback silent since 2026-09-02; programmatic buyback active (1.4M")
    assert "paid on as cashback, not credited" in st["Ether.fi"]["buyback"]


# ============================================================================================ C stored but not shown
def test_c1_declared_points_are_measuring_points_and_block_tags_are_not_points():
    from fetch.base import _measuring_point
    assert _measuring_point("pendle_latent:claimLiquidityEmissions()@21000000") == \
        _measuring_point("pendle_latent:claimLiquidityEmissions()@21000123")
    spans = {"ultrasound:supply-over-time": (pd.Timestamp("2025-09-08"), pd.Timestamp("2026-09-28")),
             "etherscan:ethsupply2.supply": (pd.Timestamp("2026-09-29"), pd.Timestamp("2026-10-13"))}
    assert bw.handover_refusal("Ethereum", "total_supply_protocol", list(spans), spans) is None


def test_c1_a_grown_wallet_set_is_one_series():
    a, b = "chain:sum(ethereum:w1+ethereum:w2)", "chain:sum(ethereum:w1+ethereum:w2+ethereum:w3)"
    spans = {a: (pd.Timestamp("2026-10-01"), pd.Timestamp("2026-10-13")),
             b: (pd.Timestamp("2026-10-14"), pd.Timestamp("2026-10-20"))}
    assert bw.handover_refusal("Ether.fi", "insider_stake_shares", [a, b], spans) is None
    swapped = {a: spans[a], "chain:sum(ethereum:w9)": spans[b]}
    assert bw.handover_refusal("Ether.fi", "insider_stake_shares", list(swapped), swapped)


def test_c1_maple_treasury_archive_rows_after_the_page_are_cut(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Maple"]])
    g = pd.concat([_g("Maple", "treasury_holding_tokens", _days("2026-09-20", "2026-09-23"), [23e6] * 4,
                      "chain:ethereum:treasury:archive"),
                   _g("Maple", "treasury_holding_tokens", ["2026-09-24", "2026-09-25"], [79e6, 78e6], "maple_page"),
                   _g("Maple", "treasury_holding_tokens", ["2026-09-26"], [23e6], "chain:ethereum:treasury:archive")])
    groups = {("Maple", "treasury_holding_tokens"): g}
    bw._handover_cut_views(groups)
    out = groups[("Maple", "treasury_holding_tokens")]
    assert len(out) == 6 and pd.Timestamp("2026-09-26") not in set(out["date"])
    assert "1 row(s)" in bw.HANDOVER_CUT[("Maple", "treasury_holding_tokens")]


def test_c2_component_rows_are_not_withdrawn_and_maple_has_its_cleanup():
    assert config.withdrawn_contract_keys("Pendle", "noncirc_team_multisig_tokens",
                                          "chain:ethereum:noncirc_team_multisig") == []
    assert config.withdrawn_contract_keys("Maple", "locked_tokens", "chain:ethereum:stsyrup") == ["stsyrup"]
    sql = (ROOT / "orphan_cleanup.sql").read_text(encoding="utf-8")
    assert "-- DB. MAPLE locked_tokens" in sql and "-- DELETE FROM metrics\n--  WHERE project = 'Maple' AND metric = 'locked_tokens'" in sql


def test_c3_c4_c5_c8_columns_read_the_stored_series():
    assert config.CP_STAKE_METRIC["Ethereum"] == "beacon_chain_eth"
    assert bw._master_tvl_metric(config.PROJECT_BY_NAME["Chainlink"]) == "protocol_tvl_usd"
    assert bw._master_tvl_metric(config.PROJECT_BY_NAME["Ethereum"]) == "tvl_usd"
    assert config.unavailable_for("Hyperliquid", "tx_count")["route_only"] and config.TX_COUNT_SCOPE["Hyperliquid"] == "HyperEVM"
    assert config.MASTER_FEES_OVERRIDE["World Mobile"] == ("own_chain_fees_usd", "own-chain fees")


def test_c6_sky_issuance_is_d_supply_plus_the_burn_before_the_stored_series():
    """The archive reading dated D is D's opening (close_shift_days 1): a burn dated 09-14 shows in the 09-15 reading."""
    spec = config.ISSUANCE_FROM_TOTAL_SUPPLY["Sky"]
    assert spec["close_shift_days"] == 1
    days = _days("2026-09-12", "2026-09-17")
    groups = {("Sky", "total_supply_protocol"): _g("Sky", "total_supply_protocol", days,
                                                   [100.0, 105.0, 105.0, 101.0, 101.0, 101.0]),
              ("Sky", "sky_stage2_burn_tokens"): _g("Sky", "sky_stage2_burn_tokens", days[1:4], [0.0, 4.0, 0.0])}
    have = _g("Sky", "gross_issuance_tokens", ["2026-09-16", "2026-09-17"], [0.0, 0.0], "schedule:config:declared")
    groups[("Sky", "gross_issuance_tokens")] = have
    bw._issuance_supply_plus_burn(groups, "Sky", spec, have)
    iss = bw._daily(groups[("Sky", "gross_issuance_tokens")])
    assert iss[pd.Timestamp("2026-09-12")] == 5.0 and iss[pd.Timestamp("2026-09-14")] == 0.0     # the mint; the burn nets
    assert len(iss) == 6 and "d(total_supply_protocol) + sky_stage2_burn_tokens" in \
        groups[("Sky", "gross_issuance_tokens")]["source"].iloc[0]


def test_c7_a_premint_with_issuance_declared_na_reads_its_release():
    assert bw._a4_iss_metric("Hyperliquid") == "pool_release_tokens"
    assert bw._a4_iss_metric("GEODNET") == "pool_release_tokens" and bw._a4_iss_metric("Near") == "gross_issuance_tokens"


# ============================================================================================== D labels and statuses
def test_d1_a_modelled_or_scheduled_leg_is_amber():
    assert config.is_modelled(config.ISSUANCE_PRIMARY["World Mobile"]["source_label"])
    assert ("Aethir", "outflow_b_tokens") in config.SCHEDULE_LEGS
    row = {"status": "ok", "n_points": 50, "modelled": "SCHEDULE leg 2025-09-08..2026-06-30"}
    conf, why = bw.confidence_for("Aethir", "emissions_tokens", row, pd.Timestamp("2026-10-13"))
    assert conf == "AMBER" and "SCHEDULE leg" in why


def test_d2_aethir_stake_definition_change_breaks_the_trend():
    h = config.declared_handover("Aethir", "locked_tokens")
    assert not h["composition_change"].lower().startswith("none") and h["overlap"]["a"] == "locked_tokens_wrapper"
    assert config.SOURCE_RECLASSIFICATIONS[("Aethir", "locked_tokens")][0][0] == "2026-09-30"


def test_d3_a_daily_twin_of_the_monthly_series_is_dropped():
    from fetch.sky_accounting import looks_daily
    assert not looks_daily([(pd.Timestamp(f"2026-0{m}-01"), 1.0) for m in range(1, 5)])
    assert looks_daily([(pd.Timestamp("2026-10-01"), 1.0), (pd.Timestamp("2026-10-02"), 1.0), (pd.Timestamp("2026-10-03"), 1.0)])
    ends = pd.date_range("2025-09-30", "2026-09-30", freq="ME")
    groups = {("Sky", "buyback_spending_usd_ba"): _g("Sky", "buyback_spending_usd_ba", ends, [10.0] * len(ends)),
              ("Sky", "buyback_spending_usd_ba_daily"): _g("Sky", "buyback_spending_usd_ba_daily", ends, [10.0] * len(ends))}
    bw._daily_twin_views(groups, pd.Timestamp("2026-10-13"))
    assert ("Sky", "buyback_spending_usd_ba_daily") not in groups
    assert "NOT DAILY" in bw._VIEW_BLOCKS[("Sky", "buyback_spending_usd_ba_daily")]


def test_d4_an_intraday_spot_is_never_compared_with_an_open():
    import credibility as cred
    d1, d2 = pd.Timestamp("2026-10-19"), pd.Timestamp("2026-10-20")
    long = pd.DataFrame([
        {"date": d1, "project": "Aerodrome", "metric": "price_usd", "value": 1.00, "fetched_at": "2026-10-21T10:46:00Z"},
        {"date": d2, "project": "Aerodrome", "metric": "price_usd", "value": 1.05, "fetched_at": "2026-10-20T10:46:00Z"},
        {"date": d1, "project": "Aerodrome", "metric": "price_usd_coinbase", "value": 1.0, "fetched_at": "2026-10-21"},
        {"date": d2, "project": "Aerodrome", "metric": "price_usd_coinbase", "value": 1.0, "fetched_at": "2026-10-21"}])
    d, va, vb = cred._common_day("Aerodrome", long, "price_usd", "price_usd_coinbase", pd.Timestamp("2026-10-21"))
    assert d == d1 and va == 1.00


def test_d5_near_settlement_reason_is_in_the_rebuilds_own_terms():
    groups = {("Near", "p2p_transfer_volume_usd"): _g("Near", "p2p_transfer_volume_usd", _days("2026-04-01", "2026-04-03"), [1, 1, 1])}
    why = bw._settlement_short_why(groups, config.PROJECT_BY_NAME["Near"])
    assert "Artemis export" not in why and "BigQuery from 2026-04-01" in why and "{have}" in why


def test_d6_sky_farm_rate_is_judged_on_the_readings_date():
    import credibility as cred
    ours = config.CREDIBILITY["Sky"]["in_apy_sky_farm"]["ours"]
    assert ours["as_of"] == "2026-10-07"
    long = pd.concat([_g("Sky", "sky_farm_reward_rate_tokens_per_s", ["2026-10-07", "2026-10-20"], [18.4, 12.8]),
                      _g("Sky", "locked_tokens_sky_farm", ["2026-10-07", "2026-10-20"], [8.8e9, 8.8e9])])
    v = cred.ours_value("Sky", ours, {}, long, pd.Timestamp("2026-10-21"))
    assert abs(v - 18.4 * 31_536_000 / 8.8e9) < 1e-9


def test_d7_near_treasury_mint_is_a_memo_and_not_d():
    lab, metric, why = config.VALUE_FLOW_MEMO["Near"][0]
    assert metric == "near_treasury_mint_view_tokens" and "NOT an outflow (d)" in lab and "N/A BY DESIGN" in why
    assert "N/A BY DESIGN" in config.FLOW_LINE_NOTES[("Near", "Outflows beside: ecosystem / grants (d)")]


# ======================================================================================================= E Aerodrome
def test_e_a_stock_from_a_timed_out_tier_is_not_stored():
    import fetch
    f = pd.DataFrame({"date": ["2026-10-21"] * 2, "project": ["Aerodrome"] * 2,
                      "metric": ["filing_managed_lock_tokens", "gross_burn_tokens"], "value": [245.1e6, 1.0]})
    frames, dropped = fetch._drop_stocks([f])
    assert dropped == ["Aerodrome/filing_managed_lock_tokens"] and list(frames[0]["metric"]) == ["gross_burn_tokens"]


def test_b9_the_replaced_buyback_columns_are_declared_to_the_guard():
    import measuring_guard as mg
    assert mg._declared("Pendle", "actual_buyback_tokens") == {"pendle_api:sPendleHistoricalData.buybackAmounts"}
    assert "derived:tokens*price" in mg._declared("Pendle", "actual_buyback_usd")
