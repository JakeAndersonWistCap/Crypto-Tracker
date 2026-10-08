"""
tests/test_signoff_fixtures.py — every item of Jake's run 2026-10-08 11:27 (5e43fba: OPEN Ethereum, Chainlink, Near, Sky,
Pendle; MATURING rows lapsing for Maple, Near, Aerodrome, Pendle) judged THE WAY THE REPORT JUDGES IT.

"Two fixes last round were reported done but did not change the output" (Jake). So each test writes a store shaped like
the real one (the same metrics, sources and cadence), runs credibility_report.evaluate on it — the workbook build, the
xlcalc evaluation of the verdict formulas, the same path `python credibility_report.py` takes — and asserts the VERDICT
on the tab, not a helper's return value. `narrow=True` builds only the project under test (the full book is ~25s).

    python -m pytest tests/test_signoff_fixtures.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402

ASOF = "2026-10-08"


def _evaluate(tmp_path, monkeypatch, project, rows, asof=ASOF):
    """{row id: {verdict, ours, ref, note}} for `project`, from credibility_report on a store holding `rows`."""
    import credibility_report as cr
    import store as sm
    db = tmp_path / "metrics.db"
    st = sm.Store(db)
    st.upsert(pd.DataFrame(rows, columns=["date", "project", "metric", "value", "source", "tier"]))
    st.close()
    monkeypatch.setattr(sm, "DB_PATH", str(db))
    monkeypatch.setitem(sys.modules, "recalc", None)          # the Python evaluation, as on Jake's machine
    rws, tab = cr.evaluate(project, asof=pd.Timestamp(asof), narrow=True)
    assert rws is not None, "credibility_report built nothing"
    return {r["id"]: {"verdict": t[10], "ours": t[4], "ref": t[6], "note": t[11]}
            for r, t in zip(rws, tab) if r["project"] == project}


def _days(a="2026-07-01", b="2026-10-07"):
    return pd.date_range(a, b)


# ---------------------------------------------------------------------------------------------------------- Ethereum
def test_ethereum_net_change_sums_our_issuance_and_burn_over_the_references_own_days(tmp_path, monkeypatch):
    """1. a4_net_change: OUR issuance − burn summed over exactly the days the reference (the protocol supply's change)
    uses — the days of the headline's own net view — and a4_net_change_pct follows. Six days stored here, as in Jake's
    store; Jake's exact failing shape was not reproduced (this shape passed before the change too), so the test pins
    the arithmetic: ours = 6 x (2,982 − 99)."""
    days = pd.date_range("2026-09-30", "2026-10-08")
    rows = []
    for i, d in enumerate(days):
        rows += [(d, "Ethereum", "gross_issuance_tokens", 2982.0, "derived:d_total_supply_protocol+d_burn_cumulative", 1),
                 (d, "Ethereum", "gross_burn_tokens", 99.0, "etherscan:ethsupply2:burnt_fees:delta", 1),
                 (d, "Ethereum", "price_usd", 4000.0, "coingecko", 1),
                 (d, "Ethereum", "revenue_usd", 99.0 * 4000, "llama:fees", 1),
                 (d, "Ethereum", "total_supply_protocol", 120.5e6 + i * 2883, "etherscan:ethsupply2", 1)]
    for d in days[3:]:
        rows.append((d, "Ethereum", "beacon_chain_eth", 36.0e6, "validatorqueue", 1))
    o = _evaluate(tmp_path, monkeypatch, "Ethereum", rows)
    net = o["a4_net_change"]
    assert net["verdict"].startswith("PASS"), net
    # Jake's run 2026-10-08 11:27 printed "OURS, same days: issuance 26,838 ... burn 891" — our FULL 9-day Q0 figure
    # (9 x 2,982). Ours must be the 6 days staked ETH, issuance and burn all exist: 6 x 2,982 and 6 x 99.
    assert "OURS, same 6 day(s): issuance 17,892" in net["note"] and "burn 594" in net["note"], net["note"][:600]
    assert "26,838" not in net["note"] and "891" not in net["note"]
    assert abs(float(net["ours"]) - 6 * (2982 - 99)) < 1e-6, net["ours"]
    # a4_net_change_pct follows: it is judged by a4_net_change and in_circ (the circulating row, not stored here), so it
    # is PASS exactly when in_circ is
    pct = o["a4_net_change_pct"]
    assert "a4_net_change" in pct["note"]
    assert pct["verdict"].startswith("PASS") == o["in_circ"]["verdict"].startswith("PASS"), (pct, o["in_circ"])


# --------------------------------------------------------------------------------------------------------- Chainlink
def _chainlink_rows(returned=400_000.0):
    rows, bal = [], 300e6
    for d in _days():
        in_ = returned if d == pd.Timestamp("2026-09-15") else 0.0
        bal += in_
        rows += [(d, "Chainlink", "noncirculating_holding_tokens", bal, "chain:ethereum:noncirc_wallets", 2),
                 (d, "Chainlink", "noncirc_outflow_scan_tokens", 0.0, "explorer:noncirc_out", 2),
                 (d, "Chainlink", "noncirc_inflow_scan_tokens", in_, "explorer:noncirc_in", 2),
                 (d, "Chainlink", "circulating_supply", 678e6, "coingecko", 1),
                 (d, "Chainlink", "price_usd", 22.0, "coingecko", 1),
                 (d, "Chainlink", "fees_usd", 50_000.0, "llama:fees", 1)]
    return rows


def test_chainlink_issuance_is_judged_stock_vs_flow_and_a_negative_release_is_a_finding(tmp_path, monkeypatch):
    """2. in_issuance: the balance-derived release (−400,000: LINK RETURNED into the 27 wallets) against the scanned
    net outflow over Q0 — PASS; CoinGecko's stepwise d(circulating) is recorded N/A; a1_fees_issuance is the VERIFIED
    FINDING 'n/a — net release negative in Q0'."""
    o = _evaluate(tmp_path, monkeypatch, "Chainlink", _chainlink_rows())
    assert o["in_issuance"]["verdict"].startswith("PASS"), o["in_issuance"]
    assert o["in_issuance_coingecko"]["verdict"] == "N/A (recorded, stepwise)"
    a1 = o["a1_fees_issuance"]
    assert a1["verdict"] == "VERIFIED FINDING", a1
    assert "net release negative in Q0" in a1["note"]


# -------------------------------------------------------------------------------------------------------------- NEAR
def test_near_buyback_emissions_and_locked_close_on_the_real_store_shape(tmp_path, monkeypatch):
    """3a. in_buyback: Σ d(buyback_fund_balance_eod) x that day's price over 09-07..10-07 against Jake's $2.08M (the
    recompute read raw rows, so it said 'no figure'); 3b. a3_net_absorption's emissions input is the gross issuance
    row; 3c. in_locked against NearBlocks' total_stake, 2%."""
    rows, bal = [], 3_000_000.0
    for d in _days():
        bal += 30_000.0
        rows += [(d, "Near", "buyback_fund_balance_eod", bal, "nearblocks:stats/balance[3]", 1),
                 (d, "Near", "price_usd", 2.3, "coingecko", 1),
                 (d, "Near", "gross_issuance_tokens", 87_000.0, "bigquery:header_supply", 1),
                 (d, "Near", "circulating_supply_first_party", 1.28e9, "bigquery:circulating", 1),
                 (d, "Near", "circulating_supply", 1.28e9, "coingecko", 1)]
    t = pd.Timestamp(ASOF)
    rows += [(t, "Near", "locked_tokens", 555_944_000.0, "near:validators", 1),
             (t, "Near", "locked_tokens_nearblocks", 556_100_000.0, "nearblocks:validators/info", 1)]
    o = _evaluate(tmp_path, monkeypatch, "Near", rows)
    bb = o["in_buyback"]
    assert bb["verdict"].startswith("PASS"), bb
    # "30 days to 2026-10-07" (the page's "last 30 days"): 30 daily changes x 30,000 NEAR x $2.30 = $2.07M
    assert abs(float(bb["ours"]) - 30 * 30_000 * 2.3) < 1.0
    assert "a4_gross_issuance" in o["a3_net_absorption"]["note"] and "in_buyback" in o["a3_net_absorption"]["note"]
    assert o["in_locked"]["verdict"].startswith("PASS"), o["in_locked"]
    assert not o["in_locked"]["verdict"].startswith("MATURING")


def test_near_locked_is_a_check_when_nearblocks_differs_by_more_than_two_percent(tmp_path, monkeypatch):
    t = pd.Timestamp(ASOF)
    rows = [(t, "Near", "locked_tokens", 555_944_000.0, "near:validators", 1),
            (t, "Near", "locked_tokens_nearblocks", 600_000_000.0, "nearblocks:validators/info", 1),
            (t, "Near", "price_usd", 2.3, "coingecko", 1)]
    o = _evaluate(tmp_path, monkeypatch, "Near", rows)
    assert o["in_locked"]["verdict"].startswith("CHECK"), o["in_locked"]


# --------------------------------------------------------------------------------------------------------------- Sky
def test_sky_headline_is_the_usds_farm_yield(tmp_path, monkeypatch):
    """4. a3_protocol_yield's VALUE is the USDS-farm row's (Jake: 4.489% PASS vs 4.61%), never the Q0 economy-wide
    2.72%, which stays a second figure."""
    rows = []
    for d in _days():
        paid = 60_000.0 if d < pd.Timestamp("2026-09-10") else 83_100.0
        rows += [(d, "Sky", "staking_rewards_usds_usd", paid, "explorer:usds_farm_mint", 2),
                 (d, "Sky", "locked_tokens_usds_farm", 8_597_539_804.0, "chain:ethereum:usds_farm_stake", 2),
                 (d, "Sky", "price_usd", 0.0785, "coingecko", 1),
                 (d, "Sky", "holders_revenue_usd", 210_000.0, "llama:fees", 1),
                 (d, "Sky", "locked_tokens", 17.42e9, "chain:ethereum:lssky", 2)]
    o = _evaluate(tmp_path, monkeypatch, "Sky", rows)
    head, farm = o["a3_protocol_yield"], o["in_apy_usds_farm"]
    assert abs(float(head["ours"]) - float(farm["ours"])) < 1e-9, (head, farm)
    assert 0.040 < float(head["ours"]) < 0.050
    assert head["verdict"].startswith("PASS"), head


# ------------------------------------------------------------------------------------------------------------ Pendle
def _pendle_rows(off=1.0, virtual_from="2026-07-01", early_stake_x=1.0):
    """Six Q0 epochs (Pendle's sizes). virtual_from: the first virtual sPENDLE reading (Jake's store: ~2026-09-11).
    early_stake_x: the stake before 2026-09-01 as a multiple of today's — Pendle's published APR always uses each
    epoch's own stake, so only the headline-vs-mean check sees a stake that moved."""
    epochs = {pd.Timestamp("2026-07-14"): 144_000, pd.Timestamp("2026-07-28"): 199_000,
              pd.Timestamp("2026-08-11"): 94_000, pd.Timestamp("2026-08-25"): 140_000,
              pd.Timestamp("2026-09-08"): 82_545, pd.Timestamp("2026-09-22"): 83_000}
    sh, vi = 30.3e6, 177.8e6
    x = lambda d: early_stake_x if d < pd.Timestamp("2026-09-01") else 1.0          # noqa: E731
    rows = []
    for d in _days():
        rows += [(d, "Pendle", "locked_tokens_shares", sh * x(d), "chain:ethereum:spendle", 2),
                 (d, "Pendle", "locked_tokens_legacy_vependle", 63.58e6, "chain:ethereum:vependle_legacy", 2),
                 (d, "Pendle", "price_usd", 3.1, "coingecko", 1)]
        if d >= pd.Timestamp(virtual_from):
            rows.append((d, "Pendle", "locked_tokens_virtual", vi * x(d), "scrape:api-v2.pendle.finance", 3))
    for d, t in epochs.items():
        rows += [(d, "Pendle", "pendle_distributed_tokens", t, "pendle_api:sPendleHistoricalData.buybackAmounts", 3),
                 (d, "Pendle", "pendle_epoch_apr_published", t * 365.25 / 14 / ((sh + vi) * x(d)) * off,
                  "pendle_api:sPendleHistoricalData.aprs", 3)]
    return rows


def test_pendle_headline_passes_when_it_equals_the_mean_of_the_per_epoch_aprs(tmp_path, monkeypatch):
    """5c (Jake's run on 3d5dbeb): the headline inherits PASS ONLY if it equals the mean of the per-epoch APRs
    (each epoch over its own stake) — and each epoch reproduces Pendle's own APR. Flat stake: both hold."""
    o = _evaluate(tmp_path, monkeypatch, "Pendle", _pendle_rows())
    assert o["in_epoch_mean"]["verdict"].startswith("PASS"), o["in_epoch_mean"]
    assert "MEAN OF PER-EPOCH APRs" in o["in_epoch_mean"]["note"]
    assert o["in_epoch_reproduction"]["verdict"].startswith("PASS"), o["in_epoch_reproduction"]
    assert o["a3_protocol_yield"]["verdict"].startswith("PASS"), o["a3_protocol_yield"]


def test_pendle_headline_is_a_check_when_the_epochs_do_not_reproduce(tmp_path, monkeypatch):
    o = _evaluate(tmp_path, monkeypatch, "Pendle", _pendle_rows(off=1.4))
    assert o["in_epoch_reproduction"]["verdict"].startswith("CHECK"), o["in_epoch_reproduction"]
    assert o["a3_protocol_yield"]["verdict"].startswith("CHECK"), o["a3_protocol_yield"]


def test_pendle_headline_is_a_check_when_the_stake_moved_and_the_twin_cannot_see_it(tmp_path, monkeypatch):
    """5a/5c: the stake was twice today's before September. Every epoch still reproduces Pendle's APR and the
    82,545 twin passes trivially — only the headline-vs-mean row catches the headline (mean distribution over TODAY's
    stake) sitting above the mean of what each epoch actually paid."""
    o = _evaluate(tmp_path, monkeypatch, "Pendle", _pendle_rows(early_stake_x=2.0))
    assert o["in_epoch_reproduction"]["verdict"].startswith("PASS"), o["in_epoch_reproduction"]
    assert o["in_epoch_apr"]["verdict"].startswith("PASS"), o["in_epoch_apr"]
    assert o["in_epoch_mean"]["verdict"].startswith("CHECK"), o["in_epoch_mean"]
    assert o["a3_protocol_yield"]["verdict"].startswith("CHECK"), o["a3_protocol_yield"]


def test_pendle_headline_stays_check_while_early_epochs_have_no_stake(tmp_path, monkeypatch):
    """Jake's store today: virtual sPENDLE (API-only) starts ~2026-09-11, so the July/August epochs have no stake and
    the mean of per-epoch APRs cannot be formed. No reference, the epochs named — never a mean over a subset."""
    o = _evaluate(tmp_path, monkeypatch, "Pendle", _pendle_rows(virtual_from="2026-09-11"))
    em = o["in_epoch_mean"]
    assert em["verdict"].startswith("CHECK"), em
    assert "2026-07-14" in em["note"] and "missing" in em["note"]
    assert o["a3_protocol_yield"]["verdict"].startswith("CHECK"), o["a3_protocol_yield"]


def test_pendle_emissions_is_judged_against_the_gauge_scan_not_maturing(tmp_path, monkeypatch):
    """The MATURING row (until 2026-10-09) closes with a verdict: the GaugeController scan is the reference."""
    rows = _pendle_rows(1.0)
    for d in _days():
        rows += [(d, "Pendle", "emissions_tokens", 50_000.0, "llama:emissions", 1),
                 (d, "Pendle", "emissions_tokens_gauge_mainnet", 50_000.0, "explorer:gauge_pendle_out", 2)]
    o = _evaluate(tmp_path, monkeypatch, "Pendle", rows)
    em = o["in_emissions"]
    assert not em["verdict"].startswith("MATURING"), em
    assert em["verdict"].startswith(("PASS", "CHECK")), em


# --------------------------------------------------------------------------------------------------------- Aerodrome
WK, TW, PX = 1_918_756.0, 1_021_271_849.0, 0.778            # Jake's read of epoch 2026-10-01


def _aero_rows(off=1.0, weight_from=None):
    rows = []
    for d in _days():
        for m in ("revenue_usd", "holders_revenue_usd"):
            rows.append((d, "Aerodrome", m, WK / 7 * off, "defillama", 1))
        rows += [(d, "Aerodrome", "price_usd", PX, "coingecko", 1),
                 (d, "Aerodrome", "ve_locked_supply_tokens", 1.053e9, "chain:base:ve", 2),
                 (d, "Aerodrome", "ve_voting_power_tokens", 881.1e6, "chain:base:ve", 2)]
        if weight_from is None:
            rows.append((d, "Aerodrome", "voter_total_weight_tokens", TW, "aero_voter:Voter.totalWeight", 2))
    if weight_from is not None:                              # read forward only, from Jake's next run
        rows.append((pd.Timestamp(weight_from), "Aerodrome", "voter_total_weight_tokens", TW,
                     "aero_voter:Voter.totalWeight", 2))
    for e in pd.date_range("2026-07-02", "2026-10-01", freq="7D"):
        rows.append((e, "Aerodrome", "emissions_tokens", 485_000.0, "chain:base:rewards_distributor", 2))
    e = pd.Timestamp("2026-10-01")
    rows += [(e, "Aerodrome", "voter_rewards_onchain_usd", WK, "aero_voter:tokenRewardsPerEpoch", 2),
             (e, "Aerodrome", "voter_rewards_unpriced_count", 18.0, "aero_voter:tokenRewardsPerEpoch", 2),
             (e, "Aerodrome", "voter_rewards_onchain_apr", WK * 52 / (TW * PX), "aero_voter:tokenRewardsPerEpoch", 2)]
    return rows


def test_aerodrome_revenue_and_yield_are_judged_epoch_for_epoch_against_state(tmp_path, monkeypatch):
    """6a. in_revenue = DefiLlama's fees + bribes over the SAME epoch's 7 UTC days vs the on-chain epoch ($1,918,756),
    10%; 6b. the headline's arithmetic on that epoch over Voter.totalWeight reproduces the on-chain 12.56%, the working
    prints the headline's numerator / denominator, and the rebase (485K AERO/week, ~2.5%) is its own labelled row."""
    o = _evaluate(tmp_path, monkeypatch, "Aerodrome", _aero_rows())
    assert o["in_revenue"]["verdict"].startswith("PASS"), o["in_revenue"]
    assert "18 token(s) unpriced" in o["in_revenue"]["note"]
    ep = o["in_voter_apr_epoch"]
    assert ep["verdict"].startswith("PASS"), ep
    assert "HEADLINE: numerator" in ep["note"] and "denominator voter_total_weight_tokens" in ep["note"]
    assert "REBASE" in ep["note"]
    assert abs(float(ep["ref"]) - 0.1256) < 0.0005
    rb = o["in_rebase_apr"]
    assert rb["verdict"] == "N/A (recorded, separate stream)" and abs(float(rb["ours"]) - 0.0247) < 0.0005
    assert o["a3_protocol_yield"]["verdict"].startswith("PASS"), o["a3_protocol_yield"]
    assert not any(v["verdict"].startswith("MATURING") for v in o.values())


def test_aerodrome_is_a_check_when_defillama_reads_1_4x_the_epoch(tmp_path, monkeypatch):
    o = _evaluate(tmp_path, monkeypatch, "Aerodrome", _aero_rows(off=1.4))
    assert o["in_revenue"]["verdict"].startswith("CHECK"), o["in_revenue"]
    assert o["in_voter_apr_epoch"]["verdict"].startswith("CHECK"), o["in_voter_apr_epoch"]
    assert o["a3_protocol_yield"]["verdict"].startswith("CHECK"), o["a3_protocol_yield"]


def test_aerodrome_epoch_row_uses_the_first_daily_weight_read_after_the_epoch(tmp_path, monkeypatch):
    """totalWeight is read forward from Jake's next run (2026-10-09): the epoch of 10-01 still finds a stake."""
    o = _evaluate(tmp_path, monkeypatch, "Aerodrome", _aero_rows(weight_from="2026-10-09"), asof="2026-10-09")
    assert o["in_voter_apr_epoch"]["verdict"].startswith("PASS"), o["in_voter_apr_epoch"]


# ------------------------------------------------------------------------------------------------------------- Maple
def test_maple_buyback_is_a_documented_limitation_and_emissions_name_the_treasury_mints(tmp_path, monkeypatch):
    """7. in_buyback: the page is first-party and the only source — DOCUMENTED LIMITATION (was MATURING until 10-08).
    in_emissions stays N/A for holders but says SYRUP IS minted into the treasury (gross issuance into non-circulating)."""
    rows = []
    for m in ("2026-07-31", "2026-08-31"):
        rows.append((pd.Timestamp(m), "Maple", "actual_buyback_tokens", 1_200_000.0, "scrape:maple_transparency", 3))
    for d in _days():
        rows += [(d, "Maple", "price_usd", 0.45, "coingecko", 1),
                 (d, "Maple", "total_supply", 1.2e9, "chain:ethereum:token", 2)]
    o = _evaluate(tmp_path, monkeypatch, "Maple", rows)
    assert o["in_buyback"]["verdict"] == "DOCUMENTED LIMITATION", o["in_buyback"]
    assert "maple_buyback_inflows" in o["in_buyback"]["note"]
    em = o["in_emissions"]
    assert em["verdict"] == "N/A" and "MINTED" in em["note"] and "RecapitalizationModule" in em["note"]
    p = config.PROJECT_BY_NAME["Maple"]
    assert config.issuance_supply_rule(p, config.burn_mechanism(p)["model"]) == "delta_only"


# ======================================================================================================== unit pieces
def test_aero_voter_stores_the_last_complete_epoch_once_and_the_weight_daily(tmp_path):
    from fetch.aero_voter import AeroVoter
    from fetch.base import FetchOutput
    from fetch.logcache import DailyChecks
    calls = []

    def epoch_reader(epoch):
        calls.append(epoch)
        return {"epoch": epoch, "voter": "0xv", "pools": 300, "total_weight": TW, "usd": WK, "unpriced": ["0x1"] * 18,
                "unpriced_raw": {}, "priced": 120, "aero_price": PX, "priced_at": "epoch end"}
    now = int(pd.Timestamp("2026-10-09 12:00").timestamp())
    daily = DailyChecks(tmp_path)
    p = config.PROJECT_BY_NAME["Aerodrome"]
    out = FetchOutput()
    AeroVoter(epoch_reader=epoch_reader, weight_reader=lambda: TW, daily=daily, now=now).run([p], None, out)
    f = out.frame()
    usd = f[f.metric == "voter_rewards_onchain_usd"]
    assert len(usd) == 1 and str(usd["date"].iloc[0])[:10] == "2026-10-01" and usd["value"].iloc[0] == WK
    apr = f[f.metric == "voter_rewards_onchain_apr"]["value"].iloc[0]
    assert abs(apr - 0.1256) < 0.0005
    assert f[f.metric == "voter_rewards_unpriced_count"]["value"].iloc[0] == 18
    assert f[f.metric == "voter_total_weight_tokens"]["value"].iloc[0] == TW
    out2 = FetchOutput()
    AeroVoter(epoch_reader=epoch_reader, weight_reader=lambda: TW, daily=daily, now=now).run([p], None, out2)
    assert len(calls) == 1, "the same epoch was read twice"


def test_aero_voter_stores_nothing_on_an_error(tmp_path):
    from fetch.aero_voter import AeroVoter
    from fetch.base import FetchOutput
    from fetch.logcache import DailyChecks
    out = FetchOutput()
    AeroVoter(epoch_reader=lambda e: {"error": "DefiLlama returned no AERO price"}, weight_reader=lambda: None,
              daily=DailyChecks(tmp_path), now=int(pd.Timestamp("2026-10-09").timestamp())).run(
        [config.PROJECT_BY_NAME["Aerodrome"]], None, out)
    assert out.frame().empty
    assert any("no AERO price" in str(e.message) for e in out.log)


def test_nearblocks_validators_info_stores_total_stake_in_near(monkeypatch):
    from fetch import nearblocks, scrape
    from fetch.base import FetchOutput
    monkeypatch.setenv("NEARBLOCKS_API_KEY", "nb-secret-123")
    monkeypatch.setattr(scrape, "robots_verdict", lambda url: (True, "test"))
    nb = nearblocks.NearBlocks(http=None)
    seen = []
    monkeypatch.setattr(nb, "_get", lambda url, params, key: seen.append(url) or
                        {"data": {"total_stake": "555944000" + "0" * 24}})
    spec = config.PROJECT_BY_NAME["Near"]["nearblocks"]
    out = FetchOutput()
    nb._validators_info("Near", spec, spec["validators_info"], out)
    f = out.frame()
    assert f["metric"].iloc[0] == "locked_tokens_nearblocks" and abs(f["value"].iloc[0] - 555_944_000) < 1e-3
    assert seen[0].endswith("/v3/validators/info")
    assert all("nb-secret-123" not in str(e.message) for e in out.log)


def test_pendle_epoch_aprs_are_stored_per_complete_epoch():
    from fetch.base import FetchOutput
    from fetch.pendle_epochs import PendleEpochs
    spec = config.PROJECT_BY_NAME["Pendle"]["spendle_epochs"]
    now = pd.Timestamp.now("UTC").tz_localize(None)
    ts = [int((now - pd.Timedelta(days=14 * k + 1)).timestamp()) for k in (3, 2, 1, 0)]
    payload = {spec["history_key"]: {spec["time_field"]: ts, "aprs": [0.02, 0.03, 1.7, 0.025]}}
    out = FetchOutput()
    PendleEpochs._epoch_aprs("Pendle", spec, payload, out)
    f = out.frame()
    # the newest started 1 day ago (in progress), 1.7 is a percent not a fraction: two stored
    assert sorted(f["value"].tolist()) == [0.02, 0.03]


def test_logscan_runs_only_its_own_tier_and_names_what_it_planned():
    from fetch.base import FetchOutput
    from fetch.logscan import LogScan
    p = config.PROJECT_BY_NAME["Pendle"]
    gauge = LogScan(own_tier="explorer_gauge")
    ran = []
    gauge._scan = lambda proj, spec, w, out: ran.append(spec["key"])
    gauge.run([p], None, FetchOutput())
    assert ran == ["gauge_pendle_out"] and gauge.planned == [("Pendle", "emissions_tokens_gauge_mainnet")]
    plain = LogScan()
    ran.clear()
    plain._scan = lambda proj, spec, w, out: ran.append(spec["key"])
    plain.run([p], None, FetchOutput())
    assert "gauge_pendle_out" not in ran


def test_a_timed_out_tier_names_the_series_it_gapped():
    import fetch
    from fetch.base import FetchOutput

    class A:
        planned = [("Pendle", "emissions_tokens_gauge_mainnet")]
    st = {"sub": FetchOutput(), "adapter": A(), "tier": 2, "t0": 0.0}
    _, _, kept, _, _ = fetch._abandon("explorer_gauge", st, 300)
    assert any("Pendle/emissions_tokens_gauge_mainnet" in str(e.message) for e in kept.log)
    assert "explorer_gauge" in fetch.TIER_BUDGET_S and "aero_voter" in fetch.TIER_BUDGET_S
    assert any(n == "aero_voter" for n, _, _ in fetch.TIER_ORDER)


def test_empty_registry_variable_falls_back_to_sources_yaml(monkeypatch):
    """8. Plume probe: `TOKEN_METRICS_SOURCES=` (as .env.example ships it) made the registry Path('') = '.', and opening
    a directory on Windows is '[Errno 13] Permission denied: '.''."""
    import importlib
    from fetch import scrape
    monkeypatch.setenv("TOKEN_METRICS_SOURCES", "")
    monkeypatch.setenv("TOKEN_METRICS_CACHE", "")
    try:
        importlib.reload(scrape)
        assert str(scrape.REGISTRY) == "sources.yaml" and str(scrape.CACHE_DIR) == str(Path(".cache/scrape"))
        assert any(e.get("project") == "Plume" for e in scrape.load_registry())
    finally:
        monkeypatch.delenv("TOKEN_METRICS_SOURCES")
        monkeypatch.delenv("TOKEN_METRICS_CACHE")
        importlib.reload(scrape)


def test_this_rounds_probes_are_registered():
    import check_offline_items as coi
    for fn in ("chainlink_noncirc_transfers", "pendle_epoch_table", "maple_syrup_mints", "aerodrome_voter_rewards",
               "sky_farm_rates", "plume_supply_read", "maple_buyback_inflows"):
        assert getattr(coi, fn) in coi.CHECKS, fn


def test_maple_inflow_table_joins_the_pages_months_and_splits_out_mints(tmp_path, monkeypatch, capsys):
    """7. 'page buybacks' read 0 every month: the table held only months WITH an inflow, so the page's months never
    appeared. Now the page's months join, a month it does not cover is n/a, and 0x0 transfers are mints."""
    import check_offline_items as coi
    import store as sm
    monkeypatch.chdir(tmp_path)
    st = sm.Store(tmp_path / "metrics.db")
    st.upsert(pd.DataFrame([(pd.Timestamp("2026-06-30"), "Maple", "actual_buyback_tokens", 900_000.0, "scrape", 3),
                            (pd.Timestamp("2026-07-31"), "Maple", "actual_buyback_tokens", 1_200_000.0, "scrape", 3)],
                           columns=["date", "project", "metric", "value", "source", "tier"]))
    st.close()
    router = "0x" + "ab" * 20
    ts_oct, ts_jul = int(pd.Timestamp("2025-10-03").timestamp()), int(pd.Timestamp("2026-07-10").timestamp())

    def logs(chain, token, topics, start=0, to="latest"):
        if topics[2] and topics[2].endswith(coi.MAPLE_DAO[2:].lower()):
            return [{"data": hex(30_000_000 * 10**18), "topics": [topics[0], "0x" + "0" * 64, topics[2]],
                     "timeStamp": ts_oct, "transactionHash": "0xa", "blockNumber": 1},
                    {"data": hex(500_000 * 10**18), "topics": [topics[0], "0x" + "0" * 24 + router[2:], topics[2]],
                     "timeStamp": ts_jul, "transactionHash": "0xb", "blockNumber": 2}], ""
        return [], ""
    monkeypatch.setattr(coi, "explorer_logs", logs)
    monkeypatch.setattr(coi, "_safe_owners", lambda w: ([], 0))
    monkeypatch.setattr(coi, "_code", lambda w, c: "EOA")
    monkeypatch.setattr(coi, "_block_at", lambda t: 1)
    monkeypatch.setattr(coi, "_source", lambda a: {"name": "UniswapV3Pool" if a == router else ""})
    coi.maple_buyback_inflows()
    out = capsys.readouterr().out
    assert "minted (0x0)" in out and "30,000,000" in out
    line_jun = next(ln for ln in out.splitlines() if ln.startswith("2026-06"))
    line_jul = next(ln for ln in out.splitlines() if ln.startswith("2026-07"))
    assert "900,000" in line_jun and "1,200,000" in line_jul and "500,000" in line_jul
    line_oct = next(ln for ln in out.splitlines() if ln.startswith("2025-10"))
    assert line_oct.rstrip().endswith("n/a")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
