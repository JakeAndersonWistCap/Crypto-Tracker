"""
references.py — Jake's independent reference table (independent_references.csv, 2026-10-13) as checks on our figures
(Fix Round 2, T2 / T4).

Each CSV row (project, metric, value, unit, as_of, source_type, source, tolerance, note) is matched to the stored series
or the workbook headline that measures the same thing (ROUTES below) and compared within its tolerance:

  primary reference        within tolerance -> VERIFIED          outside -> BROKEN (both values shown)
  cross-check reference    within tolerance -> LIVE-UNVERIFIED   outside -> CHECK  (both values shown)
  ("secondary", "primary-ish", "primary-derived", "derived …": proof of nothing on its own — never VERIFIED)
  no route / no stored value at the reference date            -> NOT WIRED + the route or the reason

A row whose note says "reconcile" also carries the cause of the gap (CAUSES), computed from the same series, never a
guess. Nothing here changes a figure: a reference only grades it. The rows are added to the Coverage tab
(coverage_audit.audit) under Tab "References".
"""
from __future__ import annotations

import csv
import math
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "independent_references.csv"
TAB = "References"
PROJECT_ALIASES = {"NEAR": "Near"}
# a stock sampled once a day reads a transfer made late on day D on day D+1: sums and events over a stock allow one day
SAMPLE_LAG_DAYS = 1
STALE_DAYS = 7             # our latest row may be this many days before the reference date (the dump's as-of)


def load(path: Path | None = None) -> list[dict]:
    with open(path or CSV_PATH, newline="", encoding="utf-8") as f:
        return [dict(r) for r in csv.DictReader(f)]


def project_of(row: dict) -> str:
    return PROJECT_ALIASES.get(row["project"], row["project"])


def role(source_type: str) -> str:
    """'primary' only for a plain primary source; anything qualified (primary-ish, primary-derived, derived, secondary)
    is a cross-check — PROPOSED reading of Jake's rule (the conservative one: it can never make a figure VERIFIED)."""
    st = (source_type or "").strip().lower()
    return "primary" if st == "primary" or st.startswith("primary (") else "cross-check"


def tolerance(tol: str, ref_text: str) -> tuple[str, float]:
    """('rel', 0.005) for '0.5%'; ('abs', 0.01) for '1pp'; ('abs', 0.1) for '0.1'; ('exact', half a unit of the
    reference's last stated digit) for 'exact'."""
    t = (tol or "").strip().lower()
    if t.endswith("%"):
        return "rel", float(t[:-1]) / 100
    if t.endswith("pp"):
        return "abs", float(t[:-2]) / 100
    if t == "exact":
        dec = len(ref_text.split(".", 1)[1]) if "." in ref_text else 0
        return "exact", 0.5 * 10 ** (-dec)
    return "abs", float(t)


def within(ours: float, ref: float, kind: str, tol: float) -> bool:
    if kind == "rel":
        return abs(ours - ref) <= tol * abs(ref) + 1e-12
    return abs(ours - ref) <= tol + 1e-12


# ROUTES: (project, reference metric) -> how our figure is read. Kinds:
#   ("last", metric)                     latest value on or before the reference date
#   ("ratio", num, den)                  latest num ÷ latest den
#   ("mean", metric, days)               mean daily value over the `days` ending at the reference date
#   ("sum_days", metric, days)           sum over the `days` ending at the reference date
#   ("sum_since", metric, start)         sum from `start` to the reference date (+ SAMPLE_LAG_DAYS)
#   ("sum_period", metric, start, end)   sum over [start, end]
#   ("on_day", metric)                   the value dated on the reference date
#   ("event", metric, days, sign)        a single-day move of the stock equal to the reference, within ±days
#   ("cumulative", metric)               every stored row up to the reference date (the series' first date is named)
#   ("cover", num, den, price, start, end) sum(num) ÷ sum(den × price) over [start, end]
#   ("headline", tab, header prefix)     the workbook cell, evaluated
#   ("wallets", check)                   a config check (addresses), see _wallet_check
#   ("flows", line prefix, label)        the Flows tab's own Token-flow line in that quarter (same series and window)
#   ("not_wired", route)                 no series measures it yet — the route that would
#   ("block_read", reference metric)     our own archive read AT THE REFERENCE'S BLOCK (config.REFERENCE_BLOCK_READS;
#                                        G2 addendum A3: a stock that moves intraday is compared at one instant only)
_NEAR_INFLOW_ROUTE = ("N5: all-time inflow of {} — fee inflows arrive as receipts, which NearBlocks' account txns do not "
                      "list; route: BigQuery receipt_actions TRANSFER + wNEAR ft_events (sql/near/"
                      "bigquery_wallet_inflows.sql), dry-run by `check_offline_items.py near_settlement_routes` — run "
                      "once Jake approves the bytes")


ROUTES: dict[tuple[str, str], tuple] = {
    ("Ethereum", "active_stake_effective_balance"): ("last", "beacon_chain_eth"),
    ("Ethereum", "issuance_per_day"): ("mean", "gross_issuance_tokens", 7),
    ("Ethereum", "burn_30d"): ("sum_days", "gross_burn_tokens", 30),
    ("Ethereum", "supply"): ("last", "circulating_supply"),
    ("Ethereum", "validator_apr_gross"): ("headline", "CP Comparability", "Staker yield — validator (A1)"),
    ("Hyperliquid", "total_staked"): ("last", "locked_tokens"),
    ("Hyperliquid", "issuer_side_stake"): ("last", "staked_noncirculating_tokens"),
    ("Hyperliquid", "assistance_fund_balance"): ("last", "burn_address_balance"),
    ("Hyperliquid", "api_total_supply"): ("last", "total_supply_gross"),
    ("Hyperliquid", "api_circulating_supply"): ("last", "circulating_supply_tokendetails"),
    ("Hyperliquid", "future_emissions_remaining"): ("last", "future_emissions_tokens"),
    ("Hyperliquid", "team_unlock_2026-10-07"): ("on_day", "issuer_release_replay_tokens"),
    ("Hyperliquid", "team_unlock_2026-01-06"): ("on_day", "issuer_release_replay_tokens"),
    ("Hyperliquid", "team_unlock_2025-11-29"): ("on_day", "issuer_release_replay_tokens"),
    ("Uniswap", "total_supply"): ("last", "total_supply_gross"),
    ("Uniswap", "dead_address_balance"): ("last", "burn_address_balance"),
    ("Uniswap", "timelock_balance"): ("last", "treasury_holding_tokens"),
    ("Uniswap", "growth_budget_paid_2026"): ("sum_since", "growth_budget_release_tokens", "2026-01-01"),
    ("Uniswap", "franchiser_recall_inflow"): ("event", "treasury_holding_tokens", 7, 1),
    # ROUND G PHASE 3 (2026-10-18): dated primary reads of the headline stocks (independent_references.csv)
    ("Aerodrome", "total_supply"): ("last", "total_supply"),
    ("Aerodrome", "ve_locked_supply"): ("last", "ve_locked_supply_tokens"),
    ("Chainlink", "total_supply"): ("last", "total_supply"),
    ("Chainlink", "staked_in_pools"): ("last", "locked_tokens"),
    ("Morpho", "total_supply"): ("last", "total_supply"),
    ("Fluid", "total_supply"): ("last", "total_supply"),
    ("Aethir", "total_supply"): ("last", "total_supply"),
    ("Aethir", "wrapper_locked"): ("last", "locked_tokens_wrapper"),
    ("Sky", "lssky_total_supply"): ("last", "locked_tokens"),
    ("Pendle", "spendle_underlying"): ("last", "locked_tokens"),
    ("GEODNET", "total_supply"): ("last", "total_supply_gross"),
    ("GEODNET", "staking_wallet_balance"): ("last", "locked_tokens"),
    ("Near", "total_supply"): ("last", "total_supply_protocol"),
    ("Near", "total_staked"): ("last", "locked_tokens"),
    ("Near", "revenue_wallets_balance"): ("last", "buyback_fund_balance"),
    ("Plume", "total_supply"): ("last", "total_supply"),
    ("Aerodrome", "tail_emission_rate"): ("source_tag", "gross_issuance_tokens", r"tail@(\d+)bps"),
    # ROUND G 2.8 (2026-10-18): the rate the team-emission line applies on the reference day (config.ISSUER_MINTS
    # rate_by_flip, from archive reads of Minter.teamRate(); check_offline_items aero_team_rate re-reads it live)
    ("Aerodrome", "team_rate"): ("issuer_rate",),
    # Aerodrome's own weekly split (aero.xyz/economics/metrics emissionsWeekly "team", Aerodrome + converted Velodrome)
    # against the derived team line on its flip day
    ("Aerodrome", "team_emission_2026-10-08"): ("on_day", "issuer_mint_tokens"),
    ("Aerodrome", "weekly_lp_emissions"): ("last", "gross_issuance_tokens"),
    ("Aerodrome", "weekly_rebase"): ("last", "emissions_tokens"),
    # like for like: our weekly (b) + (c) on the dashboard's basis (x 52 ÷ total supply); the team emission is not
    # measured yet (S5), so ours omits it — the cause names our headline (÷ circulating) and the team share
    ("Aerodrome", "annualised_dilution_incl_rebase_team"): ("weekly_sum_over", ("gross_issuance_tokens",
                                                                               "emissions_tokens"), "total_supply", 52),
    ("Aerodrome", "veaero_share_of_supply"): ("ratio", "ve_voting_power_tokens", "total_supply"),
    # ROUND G 2.8: aero.xyz/economics/metrics serves the Momentum Fund's purchases BY DAY (buybacks.days[].momentumFund,
    # server-rendered JSON). The lifetime total (since 2023-08-31) is compared only once our purchases series reaches
    # back that far; the windows below compare day for day.
    ("Aerodrome", "momentum_fund_cumulative"): ("sum_since", "foundation_aero_purchased_tokens", "2023-08-31"),
    ("Aerodrome", "momentum_fund_purchases_trace_span"): ("sum_period", "foundation_buyback_trace_tokens", "2026-07-15",
                                                          "2026-08-24"),
    ("Aerodrome", "momentum_fund_purchases_2026-07-15_2026-10-13"): ("sum_period", "foundation_aero_purchased_tokens",
                                                                     "2026-07-15", "2026-10-13"),
    ("Aerodrome", "revenue_per_emissions_usd"): ("cover", "fees_usd", "gross_issuance_tokens", "price_usd",
                                                 "2026-01-01", "2026-03-31"),
    ("Pendle", "total_supply"): ("last", "total_supply_gross"),
    # ROUND G 2.8: each wallet's own row (config component_metric — the balanceOf that feeds the sum)
    ("Pendle", "governance_multisig_balance"): ("last", "noncirc_governance_multisig_tokens"),
    ("Pendle", "team_multisig_balance"): ("last", "noncirc_team_multisig_tokens"),
    ("Pendle", "ecosystem_fund_balance"): ("last", "noncirc_ecosystem_fund_tokens"),
    ("Pendle", "unclaimed_accrued_emissions"): ("latent", "Pendle"),
    ("Sky", "vest18_rate"): ("last", "emissions_rate_current_annual_tokens"),
    ("Sky", "vest17_rate"): ("last", "emissions_rate_current_annual_tokens"),
    # G2 ADDENDUM A3 (Jake's run on e2250e5: still BROKEN +1.29% — the 10-13 row had been overwritten by a later read of
    # the same day, 20,399,911, ~5 h after the reference): both compare at the reference's own block
    ("Sky", "pause_proxy_sky_balance"): ("block_read", "pause_proxy_sky_balance"),
    ("Sky", "mkr_supply_unconverted"): ("block_read", "mkr_supply_unconverted"),
    ("Sky", "sky_total_supply"): ("last", "total_supply_protocol"),
    ("Sky", "spell_burn_2026-09-13"): ("on_day", "gross_burn_tokens"),
    ("Sky", "spell_burn_2026-10-12"): ("on_day", "gross_burn_tokens"),
    # G5 D1 (Jake 2026-10-19): the MSC settlement report is the monthly measuring point — each TMF month against our own
    # read of the same report (net_revenue_msc_usd, fetch/sky_msc.py); Block Analitica's P&L month is compared in
    # basis_rows(), labelled a known accounting-basis difference with the numbers, not graded
    ("Sky", "net_revenue_jul_2026"): ("on_day", "net_revenue_msc_usd"),
    ("Sky", "net_revenue_aug_2026"): ("on_day", "net_revenue_msc_usd"),
    ("Sky", "net_revenue_sep_2026"): ("on_day", "net_revenue_msc_usd"),
    ("Ether.fi", "total_supply"): ("last", "total_supply"),
    ("Ether.fi", "sethfi_vault_assets"): ("last", "locked_tokens_underlying"),
    ("Maple", "total_supply"): ("last", "total_supply"),
    ("Maple", "inflation_claim_2026-04-22"): ("on_day", "issuer_mint_tokens"),
    ("Maple", "inflation_claim_2026-10-09"): ("on_day", "issuer_mint_tokens"),
    ("Maple", "dao_safe_balance"): ("last", "treasury_holding_tokens_chain"),
    # ROUND G 2.8: the top tier of the schedule on file (config Maple fee_tiers, from Maple's insights page) against
    # MIP-021's own Snapshot text
    ("Maple", "buyback_top_tier"): ("fee_tier_top",),
    ("Morpho", "fee_recipient"): ("zero_sum", "revenue_usd", 90),
    # D3 (2026-10-14): like for like — the off-Ethereum campaign SCHEDULE against Merkl's own rate. G2-D1: the schedule is
    # context only now ((b) is claims on every chain); this checks the context series, not (b)
    ("Morpho", "merkl_rewards_rate"): ("mean", "merkl_campaigns_non_ethereum_tokens", 30),
    ("Fluid", "treasury_to_team_rewards"): ("event", "treasury_holding_tokens", 21, -1),
    ("Fluid", "treasury_to_team_agi3_locked"): ("event", "treasury_holding_tokens", 21, -1),
    ("Aethir", "dat_holding_axe_compute"): ("dat_file", "Axe Compute"),
    ("Aethir", "circulating_schedule_oct_2026"): ("last", "circulating_supply_first_party"),
    ("Chainlink", "reserve_holdings_2026-04-15"): ("last", "buyback_fund_balance"),
    ("Chainlink", "reserve_holdings_end_q1_2026"): ("last", "buyback_fund_balance"),
    ("Chainlink", "staking_community_rate"): ("last", "staker_yield_community_pool"),
    ("Chainlink", "staking_blended_rate"): ("ratio", "reward_emission_rate_annual", "locked_tokens_principal"),
    # V5 (Jake's round-2 run): the Flows tab's own release line and Q0 window — read 0 on calendar Q3 while Flows
    # showed the 18.75M tranche in Q0
    ("Chainlink", "quarterly_release"): ("flows", "+ released from issuer wallets", "Q0"),
    # Round G 2.6: fee inflows arrive as RECEIPTS (intents.near withdrawals), which NearBlocks' account txns do not list
    # (fefundsadmin's newest "transaction" there is 2026-03-27) — the route is BigQuery receipt_actions + wNEAR ft_events
    # (sql/near/bigquery_wallet_inflows.sql), dry-run in check_offline_items near_settlement_routes; Jake decides the bytes
    # G8 4c: the BigQuery route is wired — stage 1 (Nov 1-7 window) then stage 2 cut at the references' date; the route
    # reads data/near/wallet_inflows_alltime.json (check_offline_items.near_wallet_inflows_stage2)
    ("Near", "fefundsadmin_alltime_inflow"): ("near_inflow", "fefundsadmin.sputnik-dao.near"),
    ("Near", "buybacks_multisig_alltime_inflow"): ("near_inflow", "buybacks.multisignature.near"),
    ("Near", "1csfundsadmin_alltime_inflow"): ("near_inflow", "1csfundsadmin.sputnik-dao.near"),
    ("Near", "intents_cumulative_volume"): ("cumulative", "near_intents_volume_usd"),
    ("GEODNET", "mining_pool_wallets"): ("wallets", "mining"),
    ("GEODNET", "team_investor_ecosystem_vendor_publicsale_wallets"): ("wallets", "discretionary"),
    ("GEODNET", "burned_to_date"): ("last", "burn_address_balance"),
}



# ROUND G6 item 5 (Jake's g6_reference_batch.csv, 101 figures read 2026-10-19 + 4 two-chain sums): each mapped to the
# metric and window that measures the same thing. ('recorded', why) = no metric measures it: shown with the reason,
# never graded (status N/A BY DESIGN). Tolerances: stocks and cumulative counters 1%, completed-period flows 2%,
# exact where the source is a contract constant or the very page our route reads.
G6_ROUTES = {
    ('Maple', 'g6_revenue_month_2026_09'): ('sum_period', 'revenue_usd', '2026-09-01', '2026-09-30'),
    ('Maple', 'g6_revenue_month_2026_08'): ('sum_period', 'revenue_usd', '2026-08-01', '2026-08-31'),
    ('Maple', 'g6_revenue_month_2026_07'): ('sum_period', 'revenue_usd', '2026-07-01', '2026-07-31'),
    ('Maple', 'g6_buyback_usd_2026_09'): ('sum_period', 'actual_buyback_usd', '2026-09-01', '2026-09-30'),
    ('Maple', 'g6_buyback_tokens_2026_09'): ('sum_period', 'actual_buyback_tokens', '2026-09-01', '2026-09-30'),
    ('Maple', 'g6_buyback_usd_2026_08'): ('sum_period', 'actual_buyback_usd', '2026-08-01', '2026-08-31'),
    ('Maple', 'g6_buyback_tokens_2026_08'): ('sum_period', 'actual_buyback_tokens', '2026-08-01', '2026-08-31'),
    ('Maple', 'g6_buyback_usd_2026_07'): ('sum_period', 'actual_buyback_usd', '2026-07-01', '2026-07-31'),
    ('Maple', 'g6_buyback_tokens_2026_07'): ('sum_period', 'actual_buyback_tokens', '2026-07-01', '2026-07-31'),
    ('Maple', 'g6_revenue_ltm'): ('sum_days', 'revenue_usd', 365),
    ('Maple', 'g6_treasury_syrup_2026_10_18'): ('last', 'treasury_holding_tokens'),
    # G8 4c: stSYRUP.totalAssets() is the SYRUP held by the LEGACY vault (staking ended, MIP-019, 2026-10-07): it is
    # stsyrup_legacy_tokens, not locked_tokens (N/A for Maple since then) — re-pointed, same contract read
    ('Maple', 'g6_stsyrup_total_assets'): ('last', 'stsyrup_legacy_tokens'),
    ('Maple', 'g6_syrup_total_supply_g6'): ('last', 'total_supply'),
    ('Plume', 'g6_circulating_supply_api'): ('last', 'circulating_supply_first_party'),
    ('Plume', 'g6_total_supply_api'): ('last', 'total_supply'),
    ('Plume', 'g6_max_supply_page'): ('last', 'max_supply'),
    ('Plume', 'g6_circulating_share_website'): ('recorded', "CONFLICT RECORDED (G6-5): plume.org's '57.3% in circulation' (~5.73B) vs supply.plume.org 6,606,142,167 (66.06%) — the supply API stays the measuring point; no metric holds the website share"),
    ('Plume', 'g6_erc20_total_supply_ethereum'): ('last', 'total_supply'),
    ('Plume', 'g6_total_staked_diamond'): ('last', 'locked_tokens'),
    ('Ether.fi', 'g6_ethfi_total_supply'): ('last', 'total_supply'),
    ('Ether.fi', 'g6_sethfi_vault_ethfi'): ('last', 'locked_tokens_underlying'),
    ('Ether.fi', 'g6_sethfi_shares'): ('last', 'locked_tokens'),
    ('Fluid', 'g6_fluid_total_supply'): ('last', 'total_supply'),
    ('Fluid', 'g6_buyback_usd_cumulative'): ('sum_since', 'actual_buyback_usd', '2025-10-03'),
    ('Fluid', 'g6_buyback_tokens_cumulative'): ('sum_since', 'actual_buyback_tokens', '2025-10-03'),
    ('Fluid', 'g6_buyback_avg_price'): ('recorded', 'derived: cumulative $ ÷ cumulative FLUID (rows 24, 25) — no metric of its own; checked through them'),
    ('Fluid', 'g6_buyback_usd_30d'): ('sum_days', 'actual_buyback_usd', 30),
    ('Fluid', 'g6_buyback_records_q3_2026'): ('sum_period', 'actual_buyback_usd', '2026-07-01', '2026-09-30'),
    ('Fluid', 'g6_buyback_spent_eth_panel'): ('recorded', "recorded only: the 'Spent on FLUID Buyback' panel's ETH balance — meaning not stated by Fluid; no metric"),
    ('Fluid', 'g6_revenue_q4_2025_musd'): ('sum_period', 'revenue_usd', '2025-10-01', '2025-12-31'),
    ('Morpho', 'g6_max_supply_docs'): ('last', 'max_supply'),
    ('Morpho', 'g6_total_supply_wrapped'): ('last', 'total_supply'),
    ('Morpho', 'g6_circulating_supply_tokenops'): ('last', 'circulating_supply_tokenops'),
    ('Morpho', 'g6_total_supply_tokenops'): ('last', 'total_supply'),
    ('Morpho', 'g6_merkl_base_campaign_total'): ('sum_period', 'merkl_campaigns_non_ethereum_tokens', '2026-09-21', '2026-10-21'),
    ('Morpho', 'g6_merkl_arc_campaign'): ('recorded', 'recorded only: Arc campaign is paid on Ethereum (distributionChainId 1) — it lands in the Ethereum claims (merkl_morpho_out), not merkl_campaigns_non_ethereum_tokens'),
    ('Uniswap', 'g6_uni_total_supply_gross'): ('last', 'total_supply_gross'),
    ('Uniswap', 'g6_dead_balance'): ('last', 'burn_address_balance'),
    ('Uniswap', 'g6_retro_burn_100m'): ('on_day', 'one_off_burn_tokens'),
    ('Uniswap', 'g6_record_day_burn_june'): ('recorded', "recorded only: 'a day in June 2026' — no date to compare a daily row against"),
    ('GEODNET', 'g6_total_supply_gross_filing'): ('last', 'total_supply_gross'),
    ('GEODNET', 'g6_burn_polygon_2026_09'): ('recorded', "GEODNET feed (Polygon leg): compared as the two-chain monthly sum (row 'gross_burn_2026_09_feed'); per-chain burn has no metric"),
    ('GEODNET', 'g6_burn_polygon_usd_2026_09'): ('recorded', 'USD of the Polygon burn: no per-chain $ metric'),
    ('GEODNET', 'g6_burn_solana_2026_09'): ('recorded', 'GEODNET feed (Solana leg): see the two-chain monthly sum row'),
    ('GEODNET', 'g6_burn_solana_usd_2026_09'): ('recorded', 'USD of the Solana burn: no per-chain $ metric'),
    ('GEODNET', 'g6_burn_polygon_2026_08'): ('recorded', 'see the two-chain monthly sum row (2026-08)'),
    ('GEODNET', 'g6_burn_solana_2026_08'): ('recorded', 'see the two-chain monthly sum row (2026-08)'),
    ('GEODNET', 'g6_burn_polygon_2026_07'): ('recorded', 'see the two-chain monthly sum row (2026-07)'),
    ('GEODNET', 'g6_burn_solana_2026_07'): ('recorded', 'see the two-chain monthly sum row (2026-07)'),
    ('GEODNET', 'g6_issued_polygon_2026_09'): ('recorded', "recorded only: GEODNET's Polygon issuance (2,677,511.95) is not pool_release_tokens — the mining wallets' September outflow is 3,677,511.95 = this + a 1,000,000 transfer that funds Solana rewards (G6-3); the feed's own issuance_feed_polygon_tokens stores the figure"),
    ('GEODNET', 'g6_issued_solana_2026_09'): ('recorded', "Solana issuance (1,143,717 GEOD in Sep): not read as issued, but NOT MISSED — our (b) line, the two Polygon mining wallets' outflow, equals the feed's Polygon issuance plus a whole-million transfer each month (Sep 1,000,000), which funds Solana rewards ahead of issue (INFERRED: destination not traced). See g6_solana_funding_12m (G6-3)"),
    ('GEODNET', 'g6_solana_funding_12m'): ('recorded', "recorded only: 22,000,000 GEOD of whole-million transfers out of the Polygon mining wallets Oct-25..Sep-26 vs Solana issued 20,817,755 — the (b) line counts Solana when it is FUNDED, lumpy and ~1.18M ahead, not when issued (G6-3)"),
    ('Plume', 'g6_reward_rate_diamond'): ('last', 'staking_yield_pct'),
    ('GEODNET', 'g6_usage_revenue_cumulative'): ('recorded', 'unit not labelled by the feed (presumed USD) — not compared'),
    ('GEODNET', 'g6_base_stations'): ('last', 'supply_units'),
    ('GEODNET', 'g6_mining_wallet_outflow_day'): ('recorded', 'a part-day window (2026-10-18 15:35..10-19 10:20 UTC) — not comparable with a daily row'),
    ('Aethir', 'g6_network_revenue_2026_09'): ('on_day', 'customer_revenue_monthly_usd'),
    ('Aethir', 'g6_network_revenue_2026_08'): ('on_day', 'customer_revenue_monthly_usd'),
    ('Aethir', 'g6_network_revenue_2026_07'): ('on_day', 'customer_revenue_monthly_usd'),
    ('Aethir', 'g6_network_revenue_cumulative'): ('recorded', "recorded, not graded: Aethir's counter is every month since June 2024 to date (194,648,687 on 10-19); ours, customer_revenue_cumulative_usd, is the SUM OF THE COMPLETE MONTHS the page lists, PARTIAL by its own source — June-July 2024 not in the list, October not complete (191,935,463; −1.39%). Not like-for-like (G6-5)"),
    ('Aethir', 'g6_arr_dashboard'): ('last', 'arr_tile_usd'),
    ('Aethir', 'g6_compute_purchases_cumulative'): ('last', 'compute_purchases_cumulative_tokens'),
    ('Aethir', 'g6_compute_hours_cumulative'): ('last', 'compute_hours_cumulative'),
    ('Aethir', 'g6_compute_hours_last_week'): ('last', 'compute_hours_last_week'),
    ('Aethir', 'g6_rewards_distributed_cumulative'): ('last', 'ecosystem_rewards_cumulative_tokens'),
    ('Aethir', 'g6_circulating_dashboard_g6'): ('last', 'circulating_supply_first_party'),
    ('Aethir', 'g6_staked_veath'): ('last', 'locked_tokens'),
    ('Aethir', 'g6_locked_cloud_hosts'): ('last', 'locked_tokens_idc'),
    # G7 item 3: the September PERIOD row (stakeHistory, stamped at its startTime 2026-09-01) — the reference is it exactly
    ('Aethir', 'g6_staked_ai_sep'): ('on_period', 'locked_tokens_ai', 'month'),
    ('Aethir', 'g6_staked_gaming_sep'): ('on_period', 'locked_tokens_gaming', 'month'),
    ('Aethir', 'g6_ath_total_supply'): ('last', 'total_supply'),
    ('Pendle', 'g6_total_pendle_staked_api'): ('recorded', "Pendle's totalPendleStaked counts sPENDLE PLUS the deprecated vePENDLE (ours: locked_tokens 31.13M sPENDLE incl. the unstake queue + locked_tokens_legacy_vependle 63.43M, never summed — Jake's rule); compared through rows 71-72"),
    ('Pendle', 'g6_pendle_in_spendle'): ('last', 'locked_tokens_shares'),
    ('Pendle', 'g6_virtual_spendle'): ('last', 'locked_tokens_virtual'),
    ('Pendle', 'g6_epoch_revenue_0908'): ('recorded', "unit not labelled by Pendle's API — not compared"),
    ('Pendle', 'g6_epoch_fees_0908'): ('recorded', 'unit not labelled — not compared'),
    ('Pendle', 'g6_epoch_buyback_0908'): ('recorded', 'unit not labelled (buybackAmounts) — not compared'),
    ('Pendle', 'g6_epoch_revenue_0922'): ('recorded', 'unit not labelled — not compared'),
    ('Pendle', 'g6_spendle_alltime_revenue'): ('recorded', 'unit not labelled — not compared'),
    ('Pendle', 'g6_pendle_total_supply_g6'): ('last', 'total_supply'),
    ('Near', 'g6_total_supply_2026_09_30'): ('on_day', 'total_supply'),
    ('Near', 'g6_total_supply_2026_06_30'): ('on_day', 'total_supply'),
    ('Near', 'g6_total_supply_live'): ('last', 'total_supply'),
    ('Near', 'g6_circulating_nearblocks_stale'): ('recorded', 'NOT USED (G6-3): NearBlocks /v1/stats circulating_supply is flat at 1,249,836,992.16 NEAR since 2025-08-27 while the total rose; the route was retired 2026-10-13 (v3 answers circulating == total)'),
    ('Near', 'g6_max_inflation_rate'): ('recorded', 'protocol parameter, recorded: max_inflation_rate [1,40] since block 170,351,519 (declared step, G6-3)'),
    ('Chainlink', 'g6_reserve_holdings_2026_10_19'): ('last', 'buyback_fund_balance'),
    ('Chainlink', 'g6_link_total_supply_g6'): ('last', 'total_supply'),
    ('Hyperliquid', 'g6_hype_max_supply'): ('last', 'max_supply'),
    ('Hyperliquid', 'g6_hype_total_supply_api'): ('last', 'total_supply_gross'),
    ('Hyperliquid', 'g6_hype_circulating_api'): ('last', 'circulating_supply_tokendetails'),
    ('Hyperliquid', 'g6_assistance_fund_hype'): ('recorded', "recorded: the Assistance Fund balance is inside the API's own total; ours is total_supply_gross net of it (TOTAL_SUPPLY_NET)"),
    ('Sky', 'g6_sky_cumulative_buyback_g6'): ('last', 'sky_cumulative_buyback_ba'),
    ('Sky', 'g6_usds_spent_cumulative'): ('cumulative', 'buyback_spending_usd_ba_daily'),   # G7 5: by day from 2024-11-14
    ('Sky', 'g6_sky_cumulative_buyback_0930'): ('on_day', 'sky_cumulative_buyback_ba'),
    ('Sky', 'g6_sky_cumulative_buyback_0630'): ('on_day', 'sky_cumulative_buyback_ba'),
    ('Sky', 'g6_staked_sky'): ('last', 'locked_tokens'),
    # G7 item 5: compared AT THE REFERENCE'S BLOCK (config.REFERENCE_BLOCK_READS) — Block Analitica read at 09:53 UTC, ours
    # at 13:48, and 7,695,439.49 SKY left the Pause Proxy for the lsSKY rewards at 13:04 in between
    ('Sky', 'g6_sky_treasury'): ('block_read', 'g6_sky_treasury'),
    ('Sky', 'g6_sky_total_supply_g6'): ('last', 'total_supply_protocol'),
    ('Aerodrome', 'g6_aero_total_supply_g6'): ('last', 'total_supply'),
    ('Aerodrome', 'g6_ve_supply_g6'): ('last', 've_locked_supply_tokens'),
    ('Aerodrome', 'g6_ve_balance_g6'): ('last', 'locked_tokens'),
    ('Ethereum', 'g6_eth_supply_2026_09_30'): ('on_day', 'total_supply'),
    ('Ethereum', 'g6_eth_supply_2026_06_30'): ('on_day', 'total_supply'),
    ('Ethereum', 'g6_eth_burn_since_london'): ('last', 'burn_cumulative_tokens'),
    ('Ethereum', 'g6_eth_burn_since_merge'): ('recorded', 'recorded: ours is burn since London (row 102); no since-Merge metric'),
    ('GEODNET', 'g6_gross_burn_2026_09_feed'): ('sum_period', 'gross_burn_tokens', '2026-09-01', '2026-09-30'),
    ('GEODNET', 'g6_gross_burn_2026_08_feed'): ('sum_period', 'gross_burn_tokens', '2026-08-01', '2026-08-31'),
    ('GEODNET', 'g6_gross_burn_2026_07_feed'): ('sum_period', 'gross_burn_tokens', '2026-07-01', '2026-07-31'),
}
ROUTES.update({(PROJECT_ALIASES.get(p, p), m): r for (p, m), r in G6_ROUTES.items()})


def _series(groups: dict, project: str, metric: str) -> pd.DataFrame | None:
    g = groups.get((project, metric))
    if g is None or g.empty:
        return None
    s = g[["date", "value", "source"]].copy()
    s["date"] = pd.to_datetime(s["date"]).dt.normalize()
    # A PERIODIC ROW IS MATCHED BY ITS PERIOD'S END (Round G7 item 3, config.period_end): Aethir's monthly revenue stamped
    # 2026-09-01 is September's row, so a reference dated 2026-09-30 finds it
    import config                                          # noqa: PLC0415
    if config.series_granularity(project, metric) in ("monthly", "weekly"):
        s["date"] = [config.period_end(project, metric, d) for d in s["date"]]
    s = s.dropna(subset=["value"]).sort_values("date")
    return s if not s.empty else None


def _fmt(x) -> str:
    if isinstance(x, str):
        return x
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    ax = abs(x)
    if ax and ax < 1:
        return f"{x:.4f}"
    return f"{x:,.2f}" if ax < 1e4 else f"{x:,.0f}"


def _flows_value(route: tuple, flows: list[dict] | None, project: str) -> tuple[float | None, str, str]:
    """("flows", line prefix, quarter label): the Flows tab's own Token-flow line — the same series and window."""
    _k, prefix, label = route
    how = f"Flows: Token flow '{prefix}…' {label}"
    row = next((r for r in flows or () if r.get("Project") == project and r.get("Statement") == "Token flow"
                and str(r.get("Line", "")).startswith(prefix)), None)
    if row is None:
        return None, how, "the Flows tab has no such line for this project (built without Flows?)"
    v = row.get(label)
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None, how, f"the Flows line reads {v!r} in {label}"
    return float(v), how + f" ({row.get('Series')})", ""


def _our_value(route: tuple, groups: dict, project: str, at: pd.Timestamp, ref: float,
               headline=None) -> tuple[float | None, str, str]:
    """(our value, how it was read, why there is none)."""
    kind = route[0]
    if kind == "headline":
        v = headline(project, route[1], route[2]) if headline else None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v), f"{route[1]} → {route[2]}", ""
        return None, f"{route[1]} → {route[2]}", f"the headline reads {str(v)[:120]!r}"
    if kind == "headline_sum":
        vals = [headline(project, route[1], h) if headline else None for h in route[2]]
        if all(isinstance(v, (int, float)) for v in vals):
            return float(sum(vals)), f"{route[1]} → " + " + ".join(h.split(' (')[0] for h in route[2]), ""
        return None, "headline sum", f"a headline reads {vals!r}"[:200]
    if kind == "ratio":
        a, b = _series(groups, project, route[1]), _series(groups, project, route[2])
        if a is None or b is None:
            return None, f"{route[1]} ÷ {route[2]}", "no stored rows"
        a, b = a[a.date <= at], b[b.date <= at]
        if a.empty or b.empty:
            return None, f"{route[1]} ÷ {route[2]}", "no row on or before the reference date"
        return (float(a.value.iloc[-1]) / float(b.value.iloc[-1]),
                f"{route[1]} {a.date.iloc[-1]:%Y-%m-%d} ÷ {route[2]} {b.date.iloc[-1]:%Y-%m-%d}", "")
    if kind == "cover":
        _k, num, den, px, start, end = route
        n, d, p = (_series(groups, project, m) for m in (num, den, px))
        if n is None or d is None or p is None:
            return None, f"{num} ÷ ({den} × {px})", "no stored rows"
        s0, s1 = pd.Timestamp(start), pd.Timestamp(end)
        n = n[(n.date >= s0) & (n.date <= s1)]
        d = d[(d.date >= s0) & (d.date <= s1)].merge(p[["date", "value"]].rename(columns={"value": "px"}), on="date")
        if n.empty or d.empty:
            return None, f"{num} ÷ ({den} × {px})", f"no rows in {start}..{end}"
        return (float(n.value.sum()) / float((d.value * d.px).sum()),
                f"Σ {num} ÷ Σ({den} × {px}), {start}..{end}", "")
    if kind == "weekly_sum_over":
        _k, legs, den, n = route
        vals, dates = [], []
        for m in legs:
            x = _series(groups, project, m)
            x = x[x.date <= at + pd.Timedelta(days=SAMPLE_LAG_DAYS)] if x is not None else None
            if x is None or x.empty:
                return None, " + ".join(legs), f"{m}: no row on or before the reference date"
            vals.append(float(x.value.iloc[-1]))
            dates.append(f"{m} {x.date.iloc[-1]:%Y-%m-%d}")
        d = _series(groups, project, den)
        d = d[d.date <= at + pd.Timedelta(days=SAMPLE_LAG_DAYS)] if d is not None else None
        if d is None or d.empty:
            return None, den, f"{den}: no row on or before the reference date"
        return (sum(vals) * n / float(d.value.iloc[-1]),
                f"({' + '.join(dates)}) × {n} ÷ {den} {d.date.iloc[-1]:%Y-%m-%d} — our (b) + (c) on the reference's "
                f"basis", "")
    if kind == "near_inflow":
        import json                                         # noqa: PLC0415
        f = ROOT / "data" / "near" / "wallet_inflows_alltime.json"
        try:
            got = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, "BigQuery stage 2", (f"{route[1]}: stage 2 has not run yet — stage 1 runs in "
                                              f"{config_window()} (NEAR_STAGE1_WINDOW), stage 2 after it "
                                              f"(check_offline_items near_wallet_inflow_days)")
        a = got.get(route[1])
        if not a:
            return None, f"{f.name}", f"{route[1]}: no inflow row in stage 2's answer"
        return (float(a["total_near"]), f"BigQuery receipt_actions TRANSFER + wNEAR ft_events to {a['through']} "
                                        f"({f.name})", "")
    if kind == "latent":
        import config                                       # noqa: PLC0415
        live = _series(groups, route[1], "latent_supply_tokens")          # G6 item 3: the live read wins
        if live is not None and not live.empty:
            lv = live[live.date <= at + pd.Timedelta(days=SAMPLE_LAG_DAYS)]
            if not lv.empty:
                return (float(lv.value.iloc[-1]), f"latent_supply_tokens {lv.date.iloc[-1]:%Y-%m-%d} "
                                                  f"({lv.source.iloc[-1]})", "")
        e = config.LATENT_SUPPLY.get(route[1])
        if not e:
            return None, "config.LATENT_SUPPLY", "no latent-supply reading on file"
        return float(e["tokens"]), f"config.LATENT_SUPPLY ({e['as_of']}, {e['entered_by']})", ""
    if kind == "dat_file":
        import json                                         # noqa: PLC0415
        try:
            d = json.loads((ROOT / "etf_dat_holdings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return None, "etf_dat_holdings.json", f"unreadable: {e}"
        for e in ((d.get("projects") or {}).get(project) or {}).get("dat") or ():
            if route[1].lower() in str(e.get("holder", "")).lower():
                return (float(e["tokens"]), f"etf_dat_holdings.json {e.get('holder')} as of {e.get('as_of')} "
                                            f"({e.get('source_url', '')[:60]})", "")
        return None, "etf_dat_holdings.json", f"no DAT entry naming {route[1]!r}"
    if kind == "wallets":
        return None, "config wallets", ""
    if kind == "issuer_rate":
        import config                                       # noqa: PLC0415
        v = config.issuer_mint_rate(project, at)
        if v is None:
            return None, "config.ISSUER_MINTS rate_by_flip", "no rate in force on the reference date"
        return (float(v), f"config.ISSUER_MINTS rate_by_flip in force {at:%Y-%m-%d} (the rate the team-emission line "
                          f"applies; check_offline_items aero_team_rate re-reads Minter.teamRate() live)", "")
    if kind == "fee_tier_top":
        import config                                       # noqa: PLC0415
        ft = (config.PROJECT_BY_NAME.get(project) or {}).get("fee_tiers") or {}
        bands = list(ft["tiers"]) if "tiers" in ft else []
        top = [b for b in bands if b.get("to_usd") is None]
        if not top or top[0].get("share_to_buyback") is None:
            return None, "config fee_tiers", "no open-ended top tier with a share on file"
        t = top[0]
        return (float(t["share_to_buyback"]), f"config {project} fee_tiers top tier (> ${t['from_usd']:,.0f} "
                                              f"{ft.get('basis', '')}; {ft.get('source_url', '')[:60]}, "
                                              f"{ft.get('source_date', '')})", "")
    if kind == "not_wired":
        return None, "", route[1]
    if kind == "block_read":
        import config                                       # noqa: PLC0415
        r = config.REFERENCE_BLOCK_READS.get((project, route[1]))
        if not r:
            return None, "config.REFERENCE_BLOCK_READS", "no read at the reference's block on file"
        return (float(r["value"]), f"{r['call']} at {r['chain']} block {r['block']:,} ({r['block_time']}) = "
                                   f"{r['value']:,.6f}, read {r['read_on']} via {r['rpc_host']} (archive eth_call); "
                                   f"{r['why_this_block']}", "")
    metric = route[1]
    s = _series(groups, project, metric)
    if s is None:
        return None, metric, f"{metric}: no rows stored"
    first = s.date.iloc[0]
    if kind == "source_tag":
        x = s[s.date <= at]
        m = re.search(route[2], str(x.source.iloc[-1])) if not x.empty else None
        if not m:
            return None, f"{metric} source tag", "no tagged row on or before the reference date"
        return float(m.group(1)), f"{metric} source {x.source.iloc[-1]} ({x.date.iloc[-1]:%Y-%m-%d})", ""
    if kind == "last":
        # THE ROW OF THE REFERENCE'S OWN DAY FIRST (Round G 2.3): the one-day sample lag is for a reference with no row of
        # its own day. Jake's 18 Oct workbook compared the Pause Proxy's 2026-10-13 reference (20.14M, read ~16:08 UTC)
        # with the 10-14 row (21.53M — the SKY it received in the 25 hours between) although the 10-13 row (20,142,230,
        # read 16:22) was stored; MKR the same (83,465.89 on 10-13 = the reference, 83,170 on 10-14)
        same_day = s[s.date == at]
        x = same_day if not same_day.empty else s[s.date <= at + pd.Timedelta(days=SAMPLE_LAG_DAYS)]
        if x.empty:
            return None, metric, f"{metric} starts {first:%Y-%m-%d}, after the reference date"
        d = x.date.iloc[-1]
        if (at - d).days > STALE_DAYS:
            return None, metric, f"{metric}'s last row on or before {at:%Y-%m-%d} is {d:%Y-%m-%d}"
        return float(x.value.iloc[-1]), f"{metric} {d:%Y-%m-%d} ({x.source.iloc[-1]})", ""
    if kind == "on_period":
        # A PERIOD ROW STAMPED AT ITS START IN A SERIES THAT IS OTHERWISE DAILY (Round G7 item 3: Aethir's stakeHistory
        # months sit at startTime among the daily tile reads): the period's own row is the one stamped at its first day
        p0 = pd.Timestamp(at).to_period("M").to_timestamp() if route[2] == "month" else \
            pd.Timestamp(at) - pd.Timedelta(days=6)
        x = s[s.date == p0]
        if x.empty:
            return None, metric, f"{metric} has no row stamped at the period's start {p0:%Y-%m-%d}"
        return float(x.value.iloc[-1]), f"{metric} {p0:%Y-%m-%d} (the {route[2]}'s period row)", ""
    if kind == "on_day":
        if first > at:
            return None, metric, f"{metric} starts {first:%Y-%m-%d}, after the reference date (history not restated)"
        x = s[s.date == at]
        if x.empty:
            return None, metric, f"{metric} has no row dated {at:%Y-%m-%d}"
        return float(x.value.iloc[-1]), f"{metric} {at:%Y-%m-%d}", ""
    if kind in ("mean", "sum_days", "zero_sum"):
        days = route[2]
        lo = at - pd.Timedelta(days=days)
        x = s[(s.date > lo) & (s.date <= at)]
        if first > lo + pd.Timedelta(days=1):
            return None, metric, f"{metric} starts {first:%Y-%m-%d}: fewer than {days} days stored"
        if x.empty:
            return None, metric, f"{metric}: no rows in the {days} days to {at:%Y-%m-%d}"
        if kind == "mean":
            return (float(x.value.mean()), f"mean of {metric} over the {len(x)} stored day(s) in the {days} days to "
                                           f"{at:%Y-%m-%d}", "")
        if kind == "zero_sum":
            return float(x.value.sum()), f"Σ {metric} over {days} days (0 = no protocol fee taken)", ""
        return float(x.value.sum()), f"Σ {metric} over {days} days to {at:%Y-%m-%d}", ""
    if kind in ("sum_since", "sum_period"):
        s0 = pd.Timestamp(route[2])
        s1 = pd.Timestamp(route[3]) if kind == "sum_period" else at + pd.Timedelta(days=SAMPLE_LAG_DAYS)
        if first > s0 + pd.Timedelta(days=1):
            return None, metric, f"{metric} starts {first:%Y-%m-%d}, after {s0:%Y-%m-%d}"
        # A PERIOD OUR SERIES HAS NOT REACHED IS NOT A ZERO (G6, 2026-10-19: Maple's September buyback read "ours 0.00"
        # BROKEN on a store whose monthly rows end in August — the month was published later)
        if kind == "sum_period" and s.date.max() < s1:
            return None, metric, f"{metric} ends {s.date.max():%Y-%m-%d}, before the period's end {s1:%Y-%m-%d}"
        x = s[(s.date >= s0) & (s.date <= s1)]
        return float(x.value.sum()), f"Σ {metric} {s0:%Y-%m-%d}..{s1:%Y-%m-%d}", ""
    if kind == "cumulative":
        x = s[s.date <= at]
        return (float(x.value.sum()), f"Σ {metric} {first:%Y-%m-%d}..{at:%Y-%m-%d} (our series starts "
                                      f"{first:%Y-%m-%d})", "")
    if kind == "event":
        days, sign = route[2], route[3]
        lo, hi = at - pd.Timedelta(days=days), at + pd.Timedelta(days=days + SAMPLE_LAG_DAYS)
        if first > lo:
            return None, metric, f"{metric} starts {first:%Y-%m-%d}, after {lo:%Y-%m-%d}"
        x = s.set_index("date").value
        dx = x.diff()
        dx = dx[(dx.index >= lo) & (dx.index <= hi)] * sign
        if dx.empty:
            return None, metric, f"{metric}: no rows within ±{days} days"
        best = (dx - ref).abs().idxmin()
        return float(dx[best]), f"single-day move of {metric} on {best:%Y-%m-%d} (largest match within ±{days} days)", ""
    return None, metric, f"unknown route kind {kind!r}"


def _wallet_check(project: str, which: str, ref_text: str) -> tuple[bool | None, str]:
    """G1 / G2: the issuer filing's wallets against config — mining wallets are (b) only and never in (e) /
    discretionary; every other filed wallet is discretionary. (ok, detail)."""
    import config                                          # noqa: PLC0415
    want = {a.strip().lower() for a in ref_text.split(";") if a.strip()}
    p = config.PROJECT_BY_NAME.get(project) or {}
    disc = {a.lower() for a in config.discretionary_wallets(project)} if hasattr(config, "discretionary_wallets") else None
    mining = {a.lower() for a in config.mining_wallets(project)} if hasattr(config, "mining_wallets") else None
    if disc is None or mining is None:
        return None, "config declares no mining / discretionary wallet sets for this project yet (G1/G2)"
    if which == "mining":
        missing, leaked = want - mining, want & disc
        ok = not missing and not leaked
        return ok, (f"mining wallets in config: {len(want - missing)}/{len(want)}; also in the discretionary (e) set: "
                    f"{len(leaked)}" + (f" — missing {sorted(missing)}" if missing else ""))
    missing, leaked = want - disc, want & mining
    ok = not missing and not leaked
    del p
    return ok, (f"filed non-mining wallets in the discretionary set: {len(want - missing)}/{len(want)}; also counted as "
                f"mining: {len(leaked)}" + (f" — missing {sorted(missing)}" if missing else ""))


def check(groups: dict, headline=None, projects: set[str] | None = None, rows: list[dict] | None = None,
          causes: dict | None = None, flows: list[dict] | None = None) -> list[dict]:
    """One result per reference row for the projects in `groups` (or `projects`): {project, metric, ref, ours,
    status, detail, route, source}. `headline(project, tab, header_prefix)` returns the evaluated workbook cell."""
    built = projects if projects is not None else {k[0] for k in groups}
    # THE DATA'S OWN LAST DAY per project (Round G phase 3): a reference dated after it cannot be judged by this build —
    # Jake's 10-13 dumps against a 2026-10-18 read compared five-day-old stocks with today's (AERO supply grows ~0.04% a
    # day). Such a row is N/A BY DESIGN until a build holds its day; config-only routes have no data date.
    data_end: dict = {}
    for (proj, _m), g in groups.items():
        if g is not None and not getattr(g, "empty", True) and "date" in g:
            d = pd.to_datetime(g["date"]).max()
            data_end[proj] = d if proj not in data_end or d > data_end[proj] else data_end[proj]
    out = []
    for r in rows if rows is not None else load():
        project = project_of(r)
        if project not in built:
            continue
        key = (project, r["metric"])
        route = ROUTES.get(key)
        rl = role(r["source_type"])
        ref_txt = r["value"].strip()
        base = {"project": project, "metric": r["metric"], "ref": ref_txt, "unit": r["unit"], "as_of": r["as_of"],
                "source_type": r["source_type"], "source": r["source"], "tolerance": r["tolerance"],
                "note": r["note"], "role": rl}
        if route is None:
            out.append(base | {"ours": None, "status": "NOT WIRED", "route": "",
                               "detail": "NOT WIRED — reference check: no route maps this reference to a stored series"})
            continue
        if route[0] == "recorded":
            # G6 item 5: a published figure no metric measures — on the sheet with its reason, never graded
            out.append(base | {"ours": None, "status": "N/A BY DESIGN", "route": "recorded",
                               "detail": f"N/A BY DESIGN — recorded, not compared: {route[1]}"})
            continue
        if route[0] == "wallets":
            ok, detail = _wallet_check(project, route[1], ref_txt)
            status = ("NOT WIRED" if ok is None else ("VERIFIED" if ok and rl == "primary" else
                      "LIVE-UNVERIFIED" if ok else "BROKEN" if rl == "primary" else "CHECK"))
            out.append(base | {"ours": "", "status": status, "route": "config wallet sets",
                               "detail": (f"NOT WIRED — reference check: {detail}" if ok is None else detail)})
            continue
        at = _ref_date(r["as_of"])
        end = data_end.get(project)
        if end is not None and route[0] not in CONFIG_ROUTES and at > end.normalize() + pd.Timedelta(days=SAMPLE_LAG_DAYS):
            out.append(base | {"ours": None, "status": "N/A BY DESIGN", "route": "",
                               "detail": f"N/A BY DESIGN — the reference is dated {at:%Y-%m-%d}, after this build's data "
                                         f"(latest row {end:%Y-%m-%d}): judged by a build that holds its day"})
            continue
        try:
            ref = 0.0 if route[0] == "zero_sum" else float(ref_txt)
        except ValueError:
            out.append(base | {"ours": None, "status": "NOT WIRED", "route": "",
                               "detail": f"NOT WIRED — reference check: non-numeric reference {ref_txt[:60]!r}"})
            continue
        ours, how, why = (_flows_value(route, flows, project) if route[0] == "flows"
                          else _our_value(route, groups, project, at, ref, headline))
        if route[0] == "zero_sum":
            ref = 0.0                                      # the zero address: no fee recipient = no protocol fee
        if ours is None and route[0] == "near_inflow" and "stage 2 has not run yet" in str(why):
            # G17-2b: the route is built; it waits on the scheduled BigQuery run — a date the calendar brings
            import config                                   # noqa: PLC0415
            out.append(base | {"ours": None, "status": "MISSING", "route": how,
                               "detail": f"MISSING [C — NEEDS HISTORY] — pending the scheduled BigQuery run: {why} — "
                                         f"valid from {config.NEAR_STAGE2_BY}"})
            continue
        if ours is None:
            out.append(base | {"ours": None, "status": "NOT WIRED", "route": how,
                               "detail": f"NOT WIRED — reference check: {why}"})
            continue
        kind, tol = tolerance(r["tolerance"], "0" if route[0] == "zero_sum" else ref_txt)
        ok = within(ours, ref, kind, tol)
        status = ("VERIFIED" if ok else "BROKEN") if rl == "primary" else ("LIVE-UNVERIFIED" if ok else "CHECK")
        gap = ours - ref
        rel = f" ({gap / ref:+.2%})" if ref else ""
        detail = (f"ours {_fmt(ours)} vs reference {_fmt(ref)} {r['unit']} ({r['as_of']}, {r['source_type']}); "
                  f"gap {_fmt(gap)}{rel}; tolerance {r['tolerance']} — "
                  + ("within" if ok else "OUTSIDE") + ("" if rl == "primary" else " (cross-check, not proof)"))
        cause = (causes or CAUSES).get(key)
        if cause and (not ok or "reconcile" in r["note"].lower()):
            detail += f". Cause: {cause(groups, project, at, ours, ref) if callable(cause) else cause}"
        out.append(base | {"ours": ours, "status": status, "route": how, "detail": detail})
    return out


# route kinds that read config, not stored rows: no data date applies
CONFIG_ROUTES = ("latent", "dat_file", "issuer_rate", "fee_tier_top", "not_wired", "block_read", "recorded",
                 "near_inflow")


def config_window() -> str:
    import config                                           # noqa: PLC0415
    w = config.NEAR_STAGE1_WINDOW
    return f"{w['from']}..{w['to']}"


def _ref_date(as_of: str) -> pd.Timestamp:
    """'2026-10-13' or a quarter '2026-Q3' (its last day)."""
    m = re.fullmatch(r"(\d{4})-Q([1-4])", as_of.strip())
    if m:
        return pd.Period(f"{m.group(1)}Q{m.group(2)}", freq="Q").end_time.normalize()
    return pd.Timestamp(as_of).normalize()


def _aero_dilution_cause(groups, project, at, ours, ref) -> str:
    """aero.xyz annualises (weekly LP emission + rebase + team emission) over TOTAL supply; our headline annualises
    (b) + (c) over our circulating (total − issuer-side). Both shown, from the same stored rows."""
    tot = _series(groups, project, "total_supply")
    circ = _series(groups, project, "circulating_supply_used")
    tot_v = float(tot[tot.date <= at + pd.Timedelta(days=1)].value.iloc[-1]) if tot is not None else None
    circ_v = float(circ.value.iloc[-1]) if circ is not None else None
    txt = ("compared like for like (our weekly gross issuance (b) + rebase (c), × 52 ÷ total supply). Our headline "
           "TOTAL DILUTION divides the same (b) + (c) by OUR circulating")
    if circ_v and tot_v:
        txt += (f" ({circ_v:,.0f}, staked veAERO included, issuer-side excluded) instead of total supply "
                f"({tot_v:,.0f}) — so it reads ×{tot_v / circ_v:.2f} the dashboard's basis")
    return (txt + f"; the remaining {ref - ours:+.2%} is the team emission (Minter teamRate 228 bps of the frozen "
                  f"weekly), its own insider line since fix round 2 (S5), not in (b) / (c)")


def _sky_revenue_cause(groups, project, at, ours, ref) -> str:
    s = _series(groups, project, "net_revenue_msc_usd")
    src = str(s.source.iloc[-1]) if s is not None and len(s) else "no net_revenue_msc_usd rows"
    return (f"ours is our own read of the same Soter Labs summary.md ({src}); a gap means the report was restated after "
            f"the reference was taken (the CSV cites the file at a fixed commit) or the parser misread it — read both "
            f"files before accepting either")


# G5 D1: Block Analitica's monthly P&L (revenue − expense, before distributions) against the MSC report's Sky Net Revenue
BASIS_PAIRS = {"Sky": ("net_revenue_pre_distribution_usd", "net_revenue_msc_usd")}
BASIS_LABEL = "KNOWN ACCOUNTING-BASIS DIFFERENCE"
BASIS_WHY = ("two accounts, not one P&L at two vintages (G4 7a): the MSC report nets prime mint/debt against sends to "
             "primes and carries reconciliation corrections for earlier months (Sep: Jan–Aug corrections net "
             "+2,050,226 with Skybase's −177,114); Block Analitica books P&L revenue − expense; gross differs too (Sep: "
             "MSC income 35.09M vs P&L revenue 30.80M) and the report publishes subtotals only, so the gap cannot be "
             "split by line from a primary source. The MSC figure is the monthly measuring point (G5 D1); BA stays the "
             "daily series")


def basis_rows(groups: dict, projects: set[str] | None = None) -> list[dict]:
    """G5 D1: one row per month both series hold — the difference stated with its numbers and labelled, never graded
    (status N/A BY DESIGN: the two figures measure different accounts, so neither can be BROKEN against the other)."""
    built = projects if projects is not None else {k[0] for k in groups}
    out = []
    for project, (ours_m, ref_m) in BASIS_PAIRS.items():
        if project not in built:
            continue
        a, b = _series(groups, project, ours_m), _series(groups, project, ref_m)
        if a is None or b is None:
            continue
        m = a[["date", "value"]].merge(b[["date", "value", "source"]], on="date", suffixes=("_ba", "_msc"))
        for _i, r in m.sort_values("date").iterrows():
            ba, msc = float(r.value_ba), float(r.value_msc)
            basis = str(r.source).rsplit("[", 1)[-1].rstrip("]") if "[" in str(r.source) else "basis not stated"
            gap = ba - msc
            rel = f" ({gap / msc:+.2%})" if msc else ""
            out.append({"project": project, "metric": f"net_revenue_ba_vs_msc_{r.date:%Y-%m}", "ref": f"{msc:.2f}",
                        "unit": "USD", "as_of": f"{r.date:%Y-%m-%d}", "source_type": "primary (Soter Labs MSC)",
                        "source": str(r.source), "tolerance": "", "note": "", "role": "context", "ours": ba,
                        "status": "N/A BY DESIGN", "route": f"{ours_m} vs {ref_m} {r.date:%Y-%m}",
                        "detail": f"{BASIS_LABEL} (G5 D1) — Block Analitica {_fmt(ba)} vs MSC {_fmt(msc)} USDS "
                                  f"({basis}); difference {_fmt(gap)}{rel}; not BROKEN: {BASIS_WHY}"})
    return out


def _maple_supply_cause(groups, project, at, ours, ref) -> str:
    s = _series(groups, project, "total_supply")
    src = str(s.source.iloc[-1]) if s is not None else "?"
    last = f"{s.date.iloc[-1]:%Y-%m-%d}" if s is not None else "?"
    return (f"the stored total_supply is {src} (to {last}), {ref - ours:+,.0f} short of the contract's totalSupply — "
            f"the aggregator had not taken in the MIP-010 inflation claim (23.20M, 2026-10-09). From 2026-10-11 the "
            f"token's own totalSupply() wins (TOTAL_SUPPLY_ONCHAIN_FIRST, decision A4): a store read after that date "
            f"carries the chain figure")


def _eth_supply_cause(groups, project, at, ours, ref) -> str:
    s = _series(groups, project, "circulating_supply")
    if s is None:
        return ""
    yr = s[s.date <= at - pd.Timedelta(days=330)]
    if yr.empty:
        return "history shorter than a year"
    v0, src0 = float(yr.value.iloc[-1]), str(yr.source.iloc[-1])
    return (f"today agrees; the HISTORY does not — a year back the stored series reads {v0:,.0f} ({src0}) and stays "
            f"near it until the source changes, while issuance − burn adds ~1M ETH a year: CoinGecko's derived "
            f"mcap ÷ price figure was stale (H1 restatement from issuance − burn)")


def _near_intents_cause(groups, project, at, ours, ref) -> str:
    s = _series(groups, project, "near_intents_volume_usd")
    first = f"{s.date.iloc[0]:%Y-%m-%d}" if s is not None else "?"
    # E5 (run review 2026-10-14): DefiLlama serves NEAR Intents from 2024-12-10 (nothing in Nov 2024, though the
    # adapter declares 2024-11-05); the days before 2025-07-15 add only 442,243,767, so the full history to 2026-09-22
    # is 30,685,539,216 — -2.28% on the reference (read 2026-10-14). Ours is labelled what it is: Σ since its first day.
    if first > "2024-12-10":
        return (f"OURS IS Σ SINCE {first} (our stored series' first day), not since launch: DefiLlama's NEAR Intents "
                f"history begins 2024-12-10 and the days before {first} add $442,243,767 (read 2026-10-14) — the full "
                f"history to 2026-09-22 is $30,685,539,216, {30_685_539_216 / ref - 1:+.2%} on the reference. The next "
                f"run keeps the whole history (config.FULL_HISTORY_SERIES, Round G 2.4)")
    # WINDOW ALIGNED (both since launch) — what remains is the providers' basis (Round G 2.4, read 2026-10-18):
    # NearBlocks' own intents volume-stats cumulative at 2026-09-22 is 29,867,500,840 (−4.88%), DefiLlama's (ours) as
    # stated, the reference quotes Dune — three providers, three coverages of the same protocol; no primary aggregate
    # (NEAR Intents' own explorer publishes transactions, not a total) was found
    return (f"BASIS MISMATCH, window aligned (ours is Σ since {first}, the protocol's first day on DefiLlama): DefiLlama "
            f"(ours) {ours:,.0f}, NearBlocks' own cumulative 29,867,500,840 (read 2026-10-18), the reference quotes Dune "
            f"31.4B — the providers count different sets of intents; no primary aggregate exists to settle it")


def _maple_dao_cause(groups, project, at, ours, ref) -> str:
    s = _series(groups, project, "treasury_holding_tokens_chain")
    if s is None:
        return ""
    prev = s[s.date < at]
    p = float(prev.value.iloc[-1]) if len(prev) else None
    return (f"an intraday move, not a misread (V3, 2026-10-13): the stored close of {at:%Y-%m-%d} reads {ours:,.0f}, the "
            f"day before {p:,.0f}" + (" — " if p is not None else "") +
            "tx 0xd628a262… at 18:10 UTC moved 23,197,508 SYRUP (the 10-09 MIP-010 mint) from mapleTreasury 0xa9466EaB… "
            "into the DAO Safe and, in the same tx, 4,000,000 from the Safe to the unlabelled EOA 0x9e786E5E…; the "
            "reference (23.09M) was read before it. Both Safes are issuer-side (subtracted from circulating), so only "
            "the 4,000,000 is a release (e)")


def _sky_pause_proxy_cause(groups, project, at, ours, ref) -> str:
    """D4 (run review 2026-10-14): the Pause Proxy's SKY rises through the day — read on-chain, not inferred."""
    return ("a stock read at a different instant, not a misread (Round G 2.3: the check now reads the reference's own day; "
            "run review D4, balanceOf read 2026-10-14 at each block): the Pause Proxy "
            "0xBE8E3e36… receives ~42–44K SKY from 0x2621cc0b… every ~234 blocks (~47 min) and paid nothing out — 46 "
            "inflows from 2026-10-13 00:00 to 10-14 12:00 UTC; balance 19,242,669.50 at block 26,179,842 (10-13 "
            "00:00), 20,228,168.12 at 26,185,223 (10-13 18:00), 21,169,614.90 at 26,190,301 (10-14 11:00 — the stored "
            "read), 21,212,990.66 at 26,190,598 (10-14 12:00). The 20.14M reference is an afternoon read of 10-13; a "
            "point read compares only at the same block")


def _eth_burn_cause(groups, project, at, ours, ref) -> str:
    s = _series(groups, project, "gross_burn_tokens")
    if s is None:
        return ""
    w = s[(s.date > at - pd.Timedelta(days=30)) & (s.date <= at)]
    legs = {}
    for src, part in w.groupby(w.source.astype(str).str.split(":").str[0]):
        legs[src] = (float(part.value.sum()), len(part))
    txt = "; ".join(f"{k} {v:,.1f} ETH on {n} row(s) ({v / max(n, 1):,.1f}/row)" for k, (v, n) in legs.items())
    # E5 (run review 2026-10-14): the reference is the execution-layer burn measured block by block (Σ baseFeePerGas x
    # gasUsed over the window's UTC days); Etherscan's BurntFees is the previous day's closing snapshot, so its deltas
    # are now dated to the day they measure (orphan_cleanup.sql CQ moves the stored ones) and match it to ~1e-9 ETH.
    g = groups.get((project, "gross_burn_tokens"))
    late = 0
    if g is not None and "fetched_at" in g:
        es = g[g.source.astype(str).str.startswith("etherscan:ethsupply2.BurntFees")]
        late = int((es.fetched_at.astype(str).str[:10] == pd.to_datetime(es.date).dt.strftime("%Y-%m-%d")).sum())
    if late:
        return (f"THE STORE PREDATES orphan_cleanup.sql CQ: {late} Etherscan BurntFees row(s) still sit on the day AFTER "
                f"the one they measure, so the day the two legs meet (2026-09-29) is counted twice — run "
                f"`python run_sql.py --delete CQ`. Our rows: {txt}")
    return (f"one measured-ETH basis (E5): the reference is the execution-layer burn per block (Σ baseFee x gasUsed, "
            f"blobs excluded) over the same 30 UTC days; our rows — {txt}. The Etherscan leg is d(BurntFees) dated to "
            f"the day it closes (equal to the per-block burn to ~1e-9 ETH); the DefiLlama leg is USD burned ÷ the "
            f"day's price, an estimate, and carries what difference remains")


def _fluid_q4_revenue_cause(groups, project, at, ours, ref) -> str:
    return (f"BASIS DIFFERS, NOT EXPLAINED: the reference is Fluid's OWN revenue (Q4 2025 update PDF, 3.19M; "
            f"revenue.instadapp.io), ours is DefiLlama's revenue_usd summed over 2025-10-01..12-31 ({ours:,.0f}). The PDF "
            f"says DefiLlama is MISSING Jupiter Lend, which would make DefiLlama LOWER — ours is {ours / ref - 1:+.1%}. "
            f"Settles it: revenue.instadapp.io's Q4 2025 by deployment against DefiLlama's fluid revenue by chain (G6-5)")


def _geodnet_feed_month_cause(groups, project, at, ours, ref) -> str:
    """GEODNET's feed dates a burn by its transaction; our daily burn is the change between once-a-day balance reads, so
    a burn read the day after it happened moves across a month boundary — the neighbouring months offset."""
    g = groups.get((project, "gross_burn_tokens"))
    txt = ""
    if g is not None and not g.empty:
        s = g.assign(date=pd.to_datetime(g["date"])).set_index("date")["value"].astype(float)
        m = s.groupby(s.index.to_period("M")).sum()
        p = pd.Timestamp(at).to_period("M")
        txt = "; ours by month " + ", ".join(f"{q}: {m[q]:,.0f}" for q in (p - 1, p, p + 1) if q in m.index)
    return ("MONTH BOUNDARY, NOT A MISSING BURN: the feed dates each burn by its transaction; our gross_burn_tokens is the "
            "daily change between once-a-day balance reads (chain:sum(polygon:burn_polygon+solana:…):delta), so a burn "
            "read the next day lands in the next month. Over Dec-25..Sep-26 the monthly differences (+85,000 / −120,000 / "
            "+95,000 / −50,000 / +85,000 / 0 / −25,000 …) offset to −63,000 of 42M (−0.15%)" + txt)


CAUSES = {
    ("Ethereum", "burn_30d"): _eth_burn_cause,
    ("Maple", "dao_safe_balance"): _maple_dao_cause,
    ("Sky", "pause_proxy_sky_balance"): _sky_pause_proxy_cause,
    ("Aerodrome", "annualised_dilution_incl_rebase_team"): _aero_dilution_cause,
    ("Maple", "total_supply"): _maple_supply_cause,
    ("Sky", "net_revenue_sep_2026"): _sky_revenue_cause,
    ("Ethereum", "supply"): _eth_supply_cause,
    ("Near", "intents_cumulative_volume"): _near_intents_cause,
    ("Fluid", "g6_revenue_q4_2025_musd"): _fluid_q4_revenue_cause,
    ("GEODNET", "g6_gross_burn_2026_07_feed"): _geodnet_feed_month_cause,
    ("GEODNET", "g6_gross_burn_2026_08_feed"): _geodnet_feed_month_cause,
    ("GEODNET", "g6_gross_burn_2026_09_feed"): _geodnet_feed_month_cause,
}


def coverage_rows(results: list[dict]) -> list[dict]:
    """The reference checks as Coverage rows (coverage_audit.HEAD)."""
    out = []
    for x in results:
        ours = x["ours"]
        out.append({"Tab": TAB, "Column": f"{x['metric']} ({x['unit']})", "Key": "reference", "Project": x["project"],
                    "Value now": ours if not isinstance(ours, str) else ours[:120], "Status": x["status"],
                    "Reason / route / error": x["detail"][:400], "Axis": "", "Direction": "", "Tier": x["role"],
                    "History depth (quarters with a value)": "", "Formula as built": x["route"][:200],
                    "SPEC formula": f"reference {x['ref'][:60]} {x['unit']} @ {x['as_of']} — {x['source_type']}; "
                                    f"{x['source'][:80]}",
                    "MATCHES-SPEC": "yes — independent reference check (independent_references.csv)"})
    return out
