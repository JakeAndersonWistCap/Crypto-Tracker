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


def _evaluate(tmp_path, monkeypatch, project, rows, asof=ASOF, run_log=()):
    """{row id: {verdict, ours, ref, note}} for `project`, from credibility_report on a store holding `rows` (and
    `run_log` lines: (ts, source, project, message))."""
    import sqlite3
    import credibility_report as cr
    import store as sm
    db = tmp_path / "metrics.db"
    st = sm.Store(db)
    st.upsert(pd.DataFrame(rows, columns=["date", "project", "metric", "value", "source", "tier"]))
    st.close()
    if run_log:
        con = sqlite3.connect(db)
        # (ts, source, project, message[, status[, run_id]]) — a status 'failed' line for a tier timeout
        con.executemany("INSERT INTO run_log (run_id, ts, source, tier, project, rows, status, message) "
                        "VALUES (?, ?, ?, 3, ?, 1, ?, ?)",
                        [(r[5] if len(r) > 5 else f"r{i}", r[0], r[1], r[2], r[4] if len(r) > 4 else "ok", r[3])
                         for i, r in enumerate(run_log)])
        con.commit()
        con.close()
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


def test_ethereum_issuance_rows_that_span_several_days_are_judged_over_the_days_they_cover(tmp_path, monkeypatch):
    """Jake's run 2026-10-09 ~10:15 (real store): "OURS, same 7 day(s): issuance 29,812 (4,259/day, +41.4%)" — 29,812 is
    the full 10-day sum. Etherscan's supply did not move on 3 of the 10 days, so the rows on the day it moved again hold
    the change since it LAST moved (the 10-04 row covers 10-02..10-04). Both sides now cover the same 10 days."""
    days = pd.date_range("2026-09-30", "2026-10-09")
    per_day, staked = 2982.0, (2982.0 * 365 / 166.32) ** 2             # the curve gives 2,982/day at this stake
    flat = {pd.Timestamp("2026-10-02"), pd.Timestamp("2026-10-03"), pd.Timestamp("2026-10-07")}
    rows, sup, pending = [], 120.5e6, 0
    for d in days:
        pending += 1
        if d not in flat:                                  # the supply moved: the row covers every day since it last did
            sup += per_day * pending - 99.0 * pending
            src = "derived:d_total_supply_protocol+burn" + (f"[span={pending}d]" if pending > 1 else "")
            if d >= pd.Timestamp("2026-10-05"):            # older rows (10-04) carry no tag: their span comes from the supply
                rows.append((d, "Ethereum", "gross_issuance_tokens", per_day * pending, src, 1))
            else:
                rows.append((d, "Ethereum", "gross_issuance_tokens", per_day * pending,
                             "derived:d_total_supply_protocol+burn", 1))
            pending = 0
        rows += [(d, "Ethereum", "total_supply_protocol", sup, "etherscan:ethsupply2", 1),
                 (d, "Ethereum", "gross_burn_tokens", 99.0, "etherscan:ethsupply2:burnt_fees:delta", 1),
                 (d, "Ethereum", "price_usd", 4000.0, "coingecko", 1),
                 (d, "Ethereum", "revenue_usd", 99.0 * 4000, "llama:fees", 1),
                 (d, "Ethereum", "beacon_chain_eth", staked, "validatorqueue", 1)]
    o = _evaluate(tmp_path, monkeypatch, "Ethereum", rows, asof="2026-10-09")
    net = o["a4_net_change"]
    assert "OURS, same 10 day(s): issuance 29,820" in net["note"], net["note"][:900]
    assert "10-04 8,946 (3d)" in net["note"] and "10-08 5,964 (2d)" in net["note"], net["note"][-500:]
    assert abs(float(net["ours"]) - 10 * (2982 - 99)) < 1e-6, net["ours"]
    assert net["verdict"].startswith("PASS"), net


# --------------------------------------------------------------------------------------------------------- Chainlink
def _chainlink_rows():
    """Jake's store: the sum read 24 wallets until three more were added to config (2026-09-15 here); those three hold
    400,000 LINK, so the stored sum steps up by 400,000 — a re-basing, not LINK returned. No transfer in or out."""
    rows = []
    w24 = "+".join(f"cl_nc_{i}" for i in range(24))
    w27 = "+".join(f"cl_nc_{i}" for i in range(27))
    for d in _days():
        late = d >= pd.Timestamp("2026-09-15")
        rows += [(d, "Chainlink", "noncirculating_holding_tokens", 251_900_000.10 if late else 251_500_000.10,
                  f"chain:sum({w27 if late else w24})", 2),
                 (d, "Chainlink", "noncirc_outflow_scan_tokens", 0.0, "explorer:noncirc_out", 2),
                 (d, "Chainlink", "noncirc_inflow_scan_tokens", 0.0, "explorer:noncirc_in", 2),
                 (d, "Chainlink", "circulating_supply", 678e6, "coingecko", 1),
                 (d, "Chainlink", "price_usd", 22.0, "coingecko", 1),
                 (d, "Chainlink", "fees_usd", 50_000.0, "llama:fees", 1)]
    return rows


def test_chainlink_release_is_zero_when_the_only_step_is_wallets_added_to_the_set(tmp_path, monkeypatch):
    """2a (Jake's run 2026-10-09 ~10:15): chainlink_noncirc_transfers read the 27 wallets at 251,900,000.10 at both Q0
    ends with no transfer, yet we stored a −400,000 release. The step was the wallet set growing, not LINK coming back:
    the release view now differences only days with the same wallets, so ours = the stock change = 0 -> PASS.
    2b: A1 fees / issuance is N/A by definition (LINK minted, not the release). 2c: a3_buyback_locked is recorded."""
    o = _evaluate(tmp_path, monkeypatch, "Chainlink", _chainlink_rows())
    iss = o["in_issuance"]
    assert abs(float(iss["ours"])) < 1e-6 and iss["verdict"].startswith("PASS"), iss
    assert o["in_issuance_coingecko"]["verdict"] == "N/A (recorded, stepwise)"
    a1 = o["a1_fees_issuance"]
    assert a1["verdict"] == "N/A" and "LINK minted" in a1["note"], a1
    assert o["a3_buyback_locked"]["verdict"] == "N/A (recorded — judged by the exact-date row)"


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


def _pendle_expected_headline(rows):
    """The mean of the per-epoch APRs, each over its own stake, from the fixture rows themselves."""
    df = pd.DataFrame(rows, columns=["date", "project", "metric", "value", "source", "tier"])
    s = lambda m: df[df.metric == m].set_index("date")["value"]                       # noqa: E731
    dist, sh, vi, pub = (s("pendle_distributed_tokens"), s("locked_tokens_shares"), s("locked_tokens_virtual"),
                         s("pendle_epoch_apr_published"))
    aprs = []
    for d, t in dist.items():
        v = vi[(vi.index <= d + pd.Timedelta(days=7))]
        aprs.append(t * 365.25 / 14 / (sh.loc[d] + (v.iloc[0] if v.index[0] > d else v[v.index <= d].iloc[-1]))
                    if len(v) else pub.loc[d])
    return sum(aprs) / len(aprs)


def test_pendle_headline_is_the_mean_of_the_per_epoch_aprs_and_passes_when_every_epoch_is_ours(tmp_path, monkeypatch):
    """Jake's 5c decision (2026-10-09): the headline = the MEAN of the per-epoch APRs, each over its own stake — not mean
    distribution / today's stake. The stake was twice today's before September: the headline follows each epoch's
    own stake, every epoch reproduces Pendle's APR (5%), and with our stake at every epoch it is a plain PASS."""
    rows = _pendle_rows(early_stake_x=2.0)
    o = _evaluate(tmp_path, monkeypatch, "Pendle", rows)
    head = o["a3_protocol_yield"]
    assert abs(float(head["ours"]) - _pendle_expected_headline(rows)) < 1e-9, head
    assert o["in_epoch_reproduction"]["verdict"].startswith("PASS"), o["in_epoch_reproduction"]
    assert o["in_epoch_mean"]["verdict"].startswith("PASS"), o["in_epoch_mean"]
    assert o["in_epochs_first_party"]["verdict"] == "N/A", o["in_epochs_first_party"]
    assert head["verdict"].startswith("PASS"), head


def test_pendle_epochs_before_virtual_history_take_pendles_apr_as_a_documented_limitation(tmp_path, monkeypatch):
    """Jake's store: virtual sPENDLE starts 2026-09-11. The July/August epochs take Pendle's published APR (labelled
    per epoch); the 09-08 and 09-22 epochs are ours and must reproduce Pendle's within 5%. The headline is a
    DOCUMENTED LIMITATION: 'first-party APR only, virtual sPENDLE history starts 2026-09-11'."""
    rows = _pendle_rows(virtual_from="2026-09-11")
    o = _evaluate(tmp_path, monkeypatch, "Pendle", rows)
    fp = o["in_epochs_first_party"]
    assert fp["verdict"] == "DOCUMENTED LIMITATION" and float(fp["ours"]) == 4, fp
    # Jake's 1c decision (2026-10-09): no vePENDLE supply in this fixture, so no calibrated rebuild — the epochs before
    # the API's history take Pendle's published APR, named per epoch, under the same limitation
    assert "calibrated, not first-party" in fp["note"] and "(Pendle published)" in fp["note"], fp["note"]
    assert "2026-09-22" in o["in_epoch_mean"]["note"] and "2026-07-14" not in o["in_epoch_mean"]["note"]
    assert o["in_epoch_reproduction"]["verdict"].startswith("PASS"), o["in_epoch_reproduction"]
    head = o["a3_protocol_yield"]
    assert abs(float(head["ours"]) - _pendle_expected_headline(rows)) < 1e-9, head
    assert head["verdict"] == "DOCUMENTED LIMITATION (inputs)", head


def _pendle_calibrated_rows(late_ratio=0.986):
    """Jake's store shape for the 1c decision: the API's virtual sPENDLE starts 2026-09-29; vePENDLE totalSupply is read
    every day and decays 30,000/day (active locked = 30,000 x 728). The API reads 0.986 x the rebuild; from 10-05 on it
    reads `late_ratio` x the rebuild."""
    # as on Jake's store, Pendle's per-epoch aprs read 0: none stored
    rows = [r for r in _pendle_rows(virtual_from="2026-09-29")
            if r[2] not in ("locked_tokens_virtual", "pendle_epoch_apr_published")]
    for i, d in enumerate(_days()):
        ve = 44e6 - 30_000 * i
        # archive reads, as Jake's history is: each at the first block of its UTC day (a known read time)
        rows.append((d, "Pendle", "vependle_voting_supply_tokens", ve, "chain:ethereum:vependle_voting_supply:archive",
                     2))
        if d >= pd.Timestamp("2026-09-29"):
            ratio = 0.986 if d < pd.Timestamp("2026-10-05") else late_ratio
            rows.append((d, "Pendle", "locked_tokens_virtual", ratio * (30_000 * 728 + 3 * ve),
                         "scrape:api-v2.pendle.finance", 3))
    return rows


def test_pendle_epochs_before_the_api_take_the_calibrated_rebuild_as_a_documented_limitation(tmp_path, monkeypatch):
    """Jake's 1c decision (2026-10-09): every Q0 epoch before the API's history takes the on-chain rebuild (active
    locked + 3 x vePENDLE) x the mean API/rebuild ratio, labelled per epoch with the ratio and its range; the headline
    is the mean of the per-epoch APRs and a DOCUMENTED LIMITATION (calibrated, not first-party)."""
    import credibility as cred
    rows = _pendle_calibrated_rows()
    o = _evaluate(tmp_path, monkeypatch, "Pendle", rows)
    fp = o["in_epochs_first_party"]
    # 09-22 is ours: the API's 09-29 reading is within the 7 days after its start
    assert fp["verdict"] == "DOCUMENTED LIMITATION" and float(fp["ours"]) == 5, fp
    assert "calibrated on-chain rebuild (x 0.9860" in fp["note"] and "range 0.9860..0.9860" in fp["note"], fp["note"]
    head = o["a3_protocol_yield"]
    assert head["verdict"] == "DOCUMENTED LIMITATION (inputs)", head
    df = pd.DataFrame(rows, columns=["date", "project", "metric", "value", "source", "tier"])
    s = lambda m: df[df.metric == m].set_index("date")["value"]                       # noqa: E731
    vi = lambda d: (s("locked_tokens_virtual").loc[pd.Timestamp("2026-09-29")] if d == pd.Timestamp("2026-09-22")  # noqa: E731
                    else 0.986 * (30_000 * 728 + 3 * s("vependle_voting_supply_tokens").loc[d]))
    aprs = [t * 365.25 / 14 / (s("locked_tokens_shares").loc[d] + vi(d))
            for d, t in s("pendle_distributed_tokens").items()]
    assert abs(float(head["ours"]) - sum(aprs) / len(aprs)) < 1e-9, (head, sum(aprs) / len(aprs))
    assert sum(r["source"] == "calibrated on-chain rebuild" for r in cred.epoch_apr_table(
        s("pendle_distributed_tokens"), s("pendle_epoch_apr_published"),
        {"locked_tokens_shares": s("locked_tokens_shares"), "locked_tokens_virtual": s("locked_tokens_virtual")},
        *cred._q0(pd.Timestamp("2026-10-08")), fallback={"locked_tokens_virtual": (
            0.986 * (30_000 * 728 + 3 * s("vependle_voting_supply_tokens")), "x")})) == 5


def test_pendle_calibration_drift_beyond_half_a_point_turns_the_rebuilt_epochs_to_check(tmp_path, monkeypatch):
    """Jake's 1c decision: the ratio is checked on every API day; a day more than 0.5 percentage points from the
    calibration -> the rebuilt epochs read CHECK, and so does the headline. 0.986 then 0.975 from 10-05: mean ~0.9811,
    the 0.975 days are 0.61 points off."""
    o = _evaluate(tmp_path, monkeypatch, "Pendle", _pendle_calibrated_rows(late_ratio=0.975))
    fp = o["in_epochs_first_party"]
    assert fp["verdict"].startswith("CHECK"), fp
    assert "DRIFT beyond 0.5%" in fp["note"], fp["note"]
    assert o["a3_protocol_yield"]["verdict"].startswith("CHECK"), o["a3_protocol_yield"]
    (tmp_path / "small").mkdir()
    ok = _evaluate(tmp_path / "small", monkeypatch, "Pendle", _pendle_calibrated_rows(late_ratio=0.982))
    assert ok["in_epochs_first_party"]["verdict"] == "DOCUMENTED LIMITATION", ok["in_epochs_first_party"]


def test_pendle_headline_is_a_check_when_our_epochs_miss_pendles_apr_by_more_than_5_percent(tmp_path, monkeypatch):
    o = _evaluate(tmp_path, monkeypatch, "Pendle", _pendle_rows(off=1.06, virtual_from="2026-09-11"))
    assert o["in_epoch_reproduction"]["verdict"].startswith("CHECK"), o["in_epoch_reproduction"]
    assert o["a3_protocol_yield"]["verdict"].startswith("CHECK"), o["a3_protocol_yield"]


def test_pendle_emissions_is_judged_against_the_gauge_scan_not_maturing(tmp_path, monkeypatch):
    """The MATURING row (until 2026-10-09) closes with a verdict: the GaugeController scan is the reference."""
    rows = _pendle_gauge_rows()
    o = _evaluate(tmp_path, monkeypatch, "Pendle", rows)
    em = o["in_emissions"]
    assert not em["verdict"].startswith("MATURING"), em
    assert em["verdict"].startswith(("PASS", "CHECK")), em


def _pendle_log(ts, last_epoch, value, per_epoch=None):
    """One run's pendle_distributed_tokens line, as fetch/pendle_epochs.py writes it."""
    msg = (f"pendle_distributed_tokens: payload holds 12 epoch(s) 2026-04-21..2026-10-06 (API order oldest-first; "
           f"sorted by date here); stored 11 COMPLETED 2026-04-21..{last_epoch}, median 140,000 PENDLE — UNITS "
           f"CONFIRMED against the staking page's 50,000..600,000 (/10^18; last complete epoch {value:,.0f} PENDLE)")
    if per_epoch:
        msg += "; STORED PER EPOCH: " + " | ".join(f"{d} {v:,.0f}" for d, v in per_epoch)
    return (ts, "pendle_api", "Pendle", msg)


# Pendle's lag as Jake's log shows it here: epoch 09-08 ended 09-22, read 0 that day and 82,545 two days later.
_PENDLE_LAG_LOG = [_pendle_log("2026-09-22T08:00:00", "2026-09-08", 0),
                   _pendle_log("2026-09-24T08:00:00", "2026-09-08", 82_545),
                   _pendle_log("2026-10-06T08:00:00", "2026-09-22", 0),
                   _pendle_log("2026-10-08T08:00:00", "2026-09-22", 0)]


def _pendle_zero_rows():
    rows = [r for r in _pendle_rows(virtual_from="2026-09-11")
            if not (r[2] == "pendle_distributed_tokens" and r[0] == pd.Timestamp("2026-09-22"))]
    rows = [r for r in rows if not (r[2] == "pendle_epoch_apr_published" and r[0] == pd.Timestamp("2026-09-22"))]
    return rows + [(pd.Timestamp("2026-09-22"), "Pendle", "pendle_distributed_tokens", 0.0,
                    "pendle_api:sPendleHistoricalData.buybackAmounts", 3)]


def test_pendle_epoch_read_at_zero_inside_the_observed_lag_is_left_out_of_the_mean(tmp_path, monkeypatch):
    """5b (Jake's run 2026-10-09 ~10:15): epoch 09-22 stored COMPLETED at 0 — not yet published. The run log shows
    Pendle's lag (09-08 read 0 on 09-22, non-zero on 09-24: 2 days). On 10-08 the 09-22 epoch (ended 10-06) is inside
    that lag: it is left out of the headline's mean, named, and the mean is over the other five epochs."""
    import credibility as cred
    rows = _pendle_zero_rows()
    o = _evaluate(tmp_path, monkeypatch, "Pendle", rows, run_log=_PENDLE_LAG_LOG)
    lag, seen = cred.epoch_publish_lag("Pendle", db=tmp_path / "metrics.db")
    assert lag == 2, seen
    full = _pendle_expected_headline(_pendle_rows(virtual_from="2026-09-11"))
    five = [r for r in rows if r[2] == "pendle_distributed_tokens" and r[0] != pd.Timestamp("2026-09-22")]
    assert len(five) == 5
    head = o["a3_protocol_yield"]
    assert float(head["ours"]) > full, (head, full)              # 0 left out, not averaged in
    assert "UNPUBLISHED" in o["in_epochs_first_party"]["note"], o["in_epochs_first_party"]["note"]


def test_pendle_zero_still_standing_after_the_lag_counts_as_zero(tmp_path, monkeypatch):
    """After the observed lag (10-06 + 2 days), a 0 still stored is a distribution of 0 and is in the mean."""
    rows = _pendle_zero_rows()
    log = _PENDLE_LAG_LOG + [_pendle_log("2026-10-10T08:00:00", "2026-09-22", 0)]
    late = _evaluate(tmp_path, monkeypatch, "Pendle", rows, asof="2026-10-10", run_log=log)
    (tmp_path / "inlag").mkdir()
    early = _evaluate(tmp_path / "inlag", monkeypatch, "Pendle", rows, asof="2026-10-08", run_log=_PENDLE_LAG_LOG)
    assert float(late["a3_protocol_yield"]["ours"]) < float(early["a3_protocol_yield"]["ours"])


def test_pendle_headline_cell_equals_the_python_on_the_same_store(tmp_path, monkeypatch):
    """5a: the headline cell (AR17 on Jake's book) must equal the Python computation of the same definition — the mean
    of the per-epoch APRs from credibility.epoch_apr_table/epoch_headline over the store the build read."""
    import credibility as cred
    rows = _pendle_zero_rows()
    o = _evaluate(tmp_path, monkeypatch, "Pendle", rows, run_log=_PENDLE_LAG_LOG)
    df = pd.DataFrame(rows, columns=["date", "project", "metric", "value", "source", "tier"])
    s = lambda m: df[df.metric == m].set_index("date")["value"].astype(float).sort_index()   # noqa: E731
    lo, hi = cred._q0(pd.Timestamp(ASOF))
    lag, _ = cred.epoch_publish_lag("Pendle", db=tmp_path / "metrics.db")
    tab = cred.epoch_apr_table(s("pendle_distributed_tokens"), s("pendle_epoch_apr_published"),
                               {m: s(m) for m in ("locked_tokens_shares", "locked_tokens_virtual")}, lo, hi, 14, 7, lag)
    v, n, _txt = cred.epoch_headline(tab)
    assert n == 5
    assert abs(float(o["a3_protocol_yield"]["ours"]) - v) < 1e-12, (o["a3_protocol_yield"], v)


def _pendle_gauge_rows(arb=70.0, arb_direct=70.0, with_arb=True, with_direct=True):
    rows = _pendle_rows()
    for d in _days():
        rows += [(d, "Pendle", "emissions_tokens_gauge_mainnet", 860.0, "explorer:gauge_pendle_out", 2),
                 (d, "Pendle", "emissions_tokens_gauge_optimism", 0.0, "explorer:gauge_pendle_out_optimism", 2),
                 (d, "Pendle", "total_supply", 281.5e6, "coingecko", 1)]
        if with_arb:
            rows.append((d, "Pendle", "emissions_tokens_gauge_arbitrum", arb, "explorer:gauge_pendle_out_arbitrum", 2))
        if with_direct:
            rows += [(d, "Pendle", "emissions_tokens_gauge_mainnet_direct", 860.0, "explorer:direct:gauge_pendle_out", 2),
                     (d, "Pendle", "emissions_tokens_gauge_arbitrum_direct", arb_direct,
                      "explorer:direct:gauge_pendle_out_arbitrum", 2),
                     (d, "Pendle", "emissions_tokens_gauge_optimism_direct", 0.0,
                      "explorer:direct:gauge_pendle_out_optimism", 2)]
    return rows


def test_pendle_emissions_are_the_gauge_payouts_of_every_chain_read(tmp_path, monkeypatch):
    """Item 2 (Jake's run 2026-10-09 11:41): emissions = PENDLE the gauges paid to markets, every chain read (mainnet +
    Arbitrum + Optimism, summed by day). Since ~14:10 the reference is each chain's DIRECT count (one fresh windowed
    query, no cache), not the scan series ours is built from; the four unrouted chains are a DOCUMENTED LIMITATION."""
    o = _evaluate(tmp_path, monkeypatch, "Pendle", _pendle_gauge_rows())
    em = o["in_emissions"]
    q0 = [d for d in _days() if pd.Timestamp(ASOF) - pd.Timedelta(days=90) < d <= pd.Timestamp(ASOF)]
    assert abs(float(em["ours"]) - 930.0 * len(q0)) < 1e-6, em
    assert em["verdict"].startswith("PASS") and "Arbitrum" in em["note"], em
    assert o["in_emissions_unrouted"]["verdict"] == "DOCUMENTED LIMITATION", o["in_emissions_unrouted"]
    assert "80094" in o["in_emissions_unrouted"]["note"]
    assert config.ISSUANCE_PRIMARY["Pendle"]["kind"] == "first_party"


def test_pendle_emissions_sum_is_refused_while_a_chain_scan_has_stored_nothing(tmp_path, monkeypatch):
    """Jake's run 2026-10-09 ~14:10: in_emissions stored 77,160 (mainnet only) while Arbitrum paid 6,610.56 — its scan
    was still seeding. A listed stream with no rows is not a 0: the column is not formed (named, with the seed
    command) and the row is no PASS."""
    o = _evaluate(tmp_path, monkeypatch, "Pendle", _pendle_gauge_rows(with_arb=False))
    em = o["in_emissions"]
    assert not em["verdict"].startswith("PASS"), em
    assert not isinstance(em["ours"], (int, float)), em


def test_pendle_emissions_reference_is_the_direct_count_not_the_same_series(tmp_path, monkeypatch):
    """The reference is the direct count: when the reconciled Arbitrum series and its direct count disagree, the row
    sees it (CHECK); with a chain's direct count missing there is no reference — never a partial sum against a partial
    sum."""
    o = _evaluate(tmp_path, monkeypatch, "Pendle", _pendle_gauge_rows(arb=70.0, arb_direct=140.0))
    assert o["in_emissions"]["verdict"].startswith("CHECK"), o["in_emissions"]
    (tmp_path / "nodirect").mkdir()
    o2 = _evaluate(tmp_path / "nodirect", monkeypatch, "Pendle", _pendle_gauge_rows(with_direct=False))
    assert not o2["in_emissions"]["verdict"].startswith("PASS"), o2["in_emissions"]


# --------------------------------------------------------------------------------------------------------- Aerodrome
WK, TW, PX = 1_918_756.0, 1_021_271_849.0, 0.778            # Jake's read of epoch 2026-10-01


BRIBE = 400_000.0                                            # bribes per epoch in the fixture


def _aero_rows(off=1.0, weight_from=None, price=None, llama_until=None, fee_week=None, weights=None, bribe=None,
               illiquid=None, no_rule=False):
    """price: a function of the day (default flat PX); CoinGecko and Coinbase both store it. llama_until: the last day
    DefiLlama has published (default every day). fee_week: the swap fees EARNED in the week starting at a Thursday
    (default WK - BRIBE). As on-chain (Gauge._claimFees at distribute()): epoch E's fees are those earned in E-1;
    DefiLlama books each day's fees on the swap day and bribes on the deposit day. weights: {epoch start: totalWeight}
    stored as the epoch-start archive reads instead of a flat daily TW. bribe: the bribes deposited in the epoch
    starting at a Thursday (default BRIBE). illiquid: {epoch start: (fees $, bribes $)} the illiquid-token rule
    excludes (default 0 for every epoch; no_rule: none stored)."""
    price = price or (lambda d: PX)
    fee_week = fee_week or (lambda e: WK - BRIBE)
    bribe = bribe or (lambda e: BRIBE)
    wk = lambda d: d - pd.Timedelta(days=(d.dayofweek - 3) % 7)                                 # noqa: E731
    rows = []
    for d in _days():
        if llama_until is None or d <= pd.Timestamp(llama_until):
            for m in ("revenue_usd", "holders_revenue_usd"):
                rows.append((d, "Aerodrome", m, (fee_week(wk(d)) + bribe(wk(d))) / 7 * off, "defillama", 1))
        rows += [(d, "Aerodrome", "price_usd", price(d), "coingecko", 1),
                 (d, "Aerodrome", "price_usd_coinbase", price(d), "xref:coinbase:AERO-USD", 1),
                 (d, "Aerodrome", "ve_locked_supply_tokens", 1.053e9, "chain:base:ve", 2),
                 (d, "Aerodrome", "ve_voting_power_tokens", 881.1e6, "chain:base:ve", 2)]
        if weight_from is None and weights is None:
            rows.append((d, "Aerodrome", "voter_total_weight_tokens", TW, "aero_voter:Voter.totalWeight", 2))
    if weight_from is not None:                              # read forward only, from Jake's next run
        rows.append((pd.Timestamp(weight_from), "Aerodrome", "voter_total_weight_tokens", TW,
                     "aero_voter:Voter.totalWeight", 2))
    for e, w in (weights or {}).items():
        rows.append((pd.Timestamp(e), "Aerodrome", "voter_total_weight_tokens", w,
                     "aero_voter:Voter.totalWeight:archive", 2))
    for e in pd.date_range("2026-07-02", "2026-10-01", freq="7D"):
        rows.append((e, "Aerodrome", "emissions_tokens", 485_000.0, "chain:base:rewards_distributor", 2))
        fees = fee_week(e - pd.Timedelta(days=7))
        tw = (weights or {}).get(e, TW)
        b = bribe(e)
        rows += [(e, "Aerodrome", "voter_rewards_onchain_usd", fees + b, "aero_voter:tokenRewardsPerEpoch", 2),
                 (e, "Aerodrome", "voter_rewards_onchain_fees_usd", fees, "aero_voter:tokenRewardsPerEpoch", 2),
                 (e, "Aerodrome", "voter_rewards_onchain_bribes_usd", b, "aero_voter:tokenRewardsPerEpoch", 2),
                 (e, "Aerodrome", "voter_rewards_onchain_apr", (fees + b) * 52 / (tw * price(e + pd.Timedelta(days=7))),
                  "aero_voter:tokenRewardsPerEpoch", 2)]
        if not no_rule:
            xf, xb = (illiquid or {}).get(str(e.date()), (0.0, 0.0))
            rows += [(e, "Aerodrome", "voter_rewards_illiquid_fees_usd", xf, "aero_voter:tokenRewardsPerEpoch", 2),
                     (e, "Aerodrome", "voter_rewards_illiquid_bribes_usd", xb, "aero_voter:tokenRewardsPerEpoch", 2)]
    rows.append((pd.Timestamp("2026-10-01"), "Aerodrome", "voter_rewards_unpriced_count", 18.0,
                 "aero_voter:tokenRewardsPerEpoch", 2))
    return rows


def test_aerodrome_price_rise_is_a_finding_and_the_epoch_row_uses_one_price(tmp_path, monkeypatch):
    """4a/b (Jake's run 2026-10-09 ~10:15): the dollars agree; AERO rose over Q0 ($0.54 mean vs $0.81 now), so the
    headline (rewards at the Q0 mean price) sits ~1.5x above the APR at today's price. With CoinGecko and Coinbase
    agreeing on the Q0 mean that is a VERIFIED FINDING beside the headline; the per-epoch row uses the SAME price on
    both sides, so it passes on the dollars."""
    lo, hi = pd.Timestamp("2026-07-01"), pd.Timestamp("2026-10-07")
    price = lambda d: 0.27 + (0.8144 - 0.27) * (min(d, hi) - lo).days / (hi - lo).days          # noqa: E731
    o = _evaluate(tmp_path, monkeypatch, "Aerodrome", _aero_rows(price=price))
    tp = o["in_apr_today_price"]
    assert tp["verdict"] == "N/A (recorded, not judged)", tp                 # Jake's convention: recorded beside
    assert o["in_price_confirmed"]["verdict"].startswith("PASS"), o["in_price_confirmed"]
    assert "AT TODAY'S PRICE" in tp["note"] and "Coinbase: start" in tp["note"], tp["note"][:600]
    ep = o["in_voter_apr_epoch"]
    assert ep["verdict"].startswith("PASS") and "QUARTER: mean of" in ep["note"], ep
    assert o["in_revenue"]["verdict"].startswith("PASS"), o["in_revenue"]


def test_aerodrome_headline_converts_each_days_rewards_at_that_days_price(tmp_path, monkeypatch):
    """Jake's price convention (2026-10-09): tokens = sum over Q0 days of $paid_d / price_d, over the mean stake in the
    same window — not the Q0 simple mean price. With a rising price and flat daily dollars the two differ (the mean of
    1/p is not 1/mean p), so the headline must equal the payment-day sum exactly."""
    lo, hi = pd.Timestamp("2026-07-01"), pd.Timestamp("2026-10-07")
    price = lambda d: 0.27 + (0.8144 - 0.27) * (min(d, hi) - lo).days / (hi - lo).days          # noqa: E731
    rows = _aero_rows(price=price)
    o = _evaluate(tmp_path, monkeypatch, "Aerodrome", rows)
    q0 = [d for d in _days() if pd.Timestamp(ASOF) - pd.Timedelta(days=90) < d <= pd.Timestamp(ASOF)]
    tokens = sum(WK / 7 / price(d) for d in q0)
    want = tokens * 365.25 / len(q0) / TW
    simple_mean = WK / 7 * len(q0) / (sum(price(d) for d in q0) / len(q0)) * 365.25 / len(q0) / TW
    head = o["a3_protocol_yield"]
    assert abs(float(head["ours"]) - want) < 1e-9, (head, want)
    assert abs(want / simple_mean - 1) > 0.05                    # the convention moves the figure on this path
    assert "Payment-weighted Q0 price" in o["in_apr_today_price"]["note"]


def test_sky_farm_yield_is_usds_at_each_days_sky_price_over_the_mean_stake(tmp_path, monkeypatch):
    """Jake's price convention (2026-10-09), Sky's USDS farm: each day's USDS / that day's SKY price over the last 28
    days, x 365/28, / the mean SKY staked in the farm over the same days. The headline and in_apy_usds_farm are one
    number."""
    days = _days("2026-09-01", "2026-10-07")
    price = lambda d: 0.06 + 0.0005 * (d - days[0]).days                                     # noqa: E731
    stake = lambda d: 2.0e9 + 1.0e7 * (d - days[0]).days                                     # noqa: E731
    rows = []
    for d in days:
        rows += [(d, "Sky", "staking_rewards_usds_usd", 300_000.0, "chain:ethereum:splitter", 2),
                 (d, "Sky", "price_usd", price(d), "coingecko", 1),
                 (d, "Sky", "locked_tokens_usds_farm", stake(d), "chain:ethereum:lssky_usds_farm", 2)]
    o = _evaluate(tmp_path, monkeypatch, "Sky", rows)
    win = [d for d in days if pd.Timestamp(ASOF) - pd.Timedelta(days=28) < d <= pd.Timestamp(ASOF)]
    want = sum(300_000.0 / price(d) for d in win) * 365 / len(win) / (sum(stake(d) for d in win) / len(win))
    farm = o["in_apy_usds_farm"]
    assert abs(float(farm["ours"]) - want) < 1e-12, (farm, want)


def test_aerodrome_epoch_rows_mature_until_defillama_publishes_the_last_day(tmp_path, monkeypatch):
    """4d: DefiLlama has not published the epoch's last day yet -> MATURING (until 2026-10-10) naming the day; CHECK if
    it is still missing after that."""
    o = _evaluate(tmp_path, monkeypatch, "Aerodrome", _aero_rows(llama_until="2026-10-06"), asof="2026-10-09")
    for k in ("in_revenue", "in_voter_apr_epoch"):
        assert o[k]["verdict"] == "MATURING (until 2026-10-10)", o[k]
        assert "2026-10-07" in o[k]["note"] and "DefiLlama's" in o[k]["note"], o[k]["note"]
    (tmp_path / "later").mkdir()
    later = _evaluate(tmp_path / "later", monkeypatch, "Aerodrome", _aero_rows(llama_until="2026-10-06"),
                      asof="2026-10-11")
    assert later["in_revenue"]["verdict"].startswith("CHECK"), later["in_revenue"]


def test_aerodrome_maturing_does_not_lapse_to_check_when_the_tier_timed_out(tmp_path, monkeypatch):
    """Jake's run 2026-10-09 15:59: "tier aero_voter TIMED OUT after 180s ... Kept the 0 frame(s)". After 2026-10-10 a
    missing on-chain split is a CHECK — unless the last aero_voter run was abandoned at its budget: then it stays
    MATURING and says "not stored (tier timeout)"."""
    rows = [r for r in _aero_rows() if not (r[2] == "voter_rewards_onchain_bribes_usd"
                                            and r[0] == pd.Timestamp("2026-10-01"))]
    timeout = [("2026-10-11T08:00:00Z", "aero_voter", None, "TIER TIMED OUT after 180s — abandoned and the run moved "
                "on. Kept the 0 frame(s) it had produced", "failed", "run-b")]
    o = _evaluate(tmp_path, monkeypatch, "Aerodrome", rows, asof="2026-10-11", run_log=timeout)
    for k in ("in_revenue", "in_voter_apr_epoch"):
        assert o[k]["verdict"] == "MATURING (until 2026-10-10)", o[k]
        assert "not stored (tier timeout" in o[k]["note"], o[k]["note"][:400]
    (tmp_path / "ok").mkdir()
    ran = [("2026-10-11T08:00:00Z", "aero_voter", "Aerodrome", "epoch 2026-10-01 stored already", "skipped", "run-c")]
    o = _evaluate(tmp_path / "ok", monkeypatch, "Aerodrome", rows, asof="2026-10-11", run_log=ran)
    assert o["in_revenue"]["verdict"].startswith("CHECK"), o["in_revenue"]


SPIKE = pd.Timestamp("2026-09-03")


def test_aerodrome_illiquid_bribe_is_capped_and_shown_as_a_labelled_line(tmp_path, monkeypatch):
    """Jake 2026-10-09 17:05 (LAPTOP, epoch 2026-09-03): a non-native reward counts only up to its DEX depth at
    payment; the excess is excluded from the headline as a labelled line, never silently. $7.6M of bribes in one epoch
    against a depth of $0.4M: $7.2M excluded, the headline falls, the before figure is in the working, and with the
    excess gone the epoch is no outlier (N/A, recorded)."""
    bribe = lambda e: 7_600_000.0 if e == SPIKE else BRIBE                                  # noqa: E731
    gross = _evaluate(tmp_path, monkeypatch, "Aerodrome", _aero_rows(bribe=bribe))
    (tmp_path / "capped").mkdir()
    capped = _evaluate(tmp_path / "capped", monkeypatch, "Aerodrome",
                       _aero_rows(bribe=bribe, illiquid={"2026-09-03": (0.0, 7_200_000.0)}))
    g, c = float(gross["a3_protocol_yield"]["ours"]), float(capped["a3_protocol_yield"]["ours"])
    assert c < g * 0.85, (g, c)
    note = capped["in_voter_apr_epoch"]["note"]
    assert "illiquid-token rewards excluded: $7,200,000" in note and f"{g:.2%} before the exclusion" in note, note[:900]
    assert capped["in_bribe_outliers"]["verdict"] == "N/A (recorded, not judged)", capped["in_bribe_outliers"]
    # the revenue check stays at the quoted price on both sides: the same NotifyReward events
    assert capped["in_revenue"]["verdict"].startswith("PASS"), capped["in_revenue"]


def test_aerodrome_bribe_outlier_that_survives_the_rule_is_a_finding_with_a_second_figure(tmp_path, monkeypatch):
    """Jake's run 2026-10-09 15:59, 2: if the bribe is genuine (liquid at payment), a VERIFIED FINDING and a labelled
    second figure — the headline excluding the single-epoch outlier (bribes > 5x the quarter's median week)."""
    bribe = lambda e: 7_600_000.0 if e == SPIKE else BRIBE                                  # noqa: E731
    o = _evaluate(tmp_path, monkeypatch, "Aerodrome", _aero_rows(bribe=bribe))
    ob = o["in_bribe_outliers"]
    assert ob["verdict"] == "VERIFIED FINDING", ob
    assert "2026-09-03 $7,600,000 OUTLIER" in ob["note"] and "EXCLUDING the outlier epoch(s) 2026-09-03" in ob["note"]
    head = float(o["a3_protocol_yield"]["ours"])
    assert float(ob["ours"]) < head * 0.85, (ob["ours"], head)
    assert o["a3_protocol_yield"]["verdict"] == "VERIFIED FINDING (inputs)" or "FINDING" in o["a3_protocol_yield"]["verdict"]


def test_aerodrome_outlier_row_matures_until_the_rule_is_stored(tmp_path, monkeypatch):
    o = _evaluate(tmp_path, monkeypatch, "Aerodrome", _aero_rows(no_rule=True))
    assert o["in_bribe_outliers"]["verdict"] == "MATURING (until 2026-10-10)", o["in_bribe_outliers"]
    assert "NOT stored" in o["in_bribe_outliers"]["note"]
    assert "ILLIQUID-TOKEN RULE NOT YET READ" in o["in_voter_apr_epoch"]["note"]


def test_epoch_rewards_probe_accepts_thursday_epoch_starts(monkeypatch, capsys):
    """Jake's run 2026-10-09 18:13: "not an epoch start (Thursday 00:00 UTC): 2026-09-03, 2026-09-24" — both ARE
    Thursdays; epochs are unix weeks (1970-01-01 was a Thursday). A Wednesday is still refused."""
    import check_offline_items as coi
    asked = []
    monkeypatch.setattr(coi, "aerodrome_voter_epochs", lambda eps, **k: asked.append(eps) or {"error": "stop here"})
    coi.aerodrome_epoch_rewards("2026-09-03,2026-09-24")
    assert asked and "not an epoch start" not in capsys.readouterr().out
    coi.aerodrome_epoch_rewards("2026-09-02")
    assert "not an epoch start (Thursday 00:00 UTC): 2026-09-02" in capsys.readouterr().out and len(asked) == 1


def test_aerodrome_fee_leg_under_defillama_is_a_finding_on_revenue(tmp_path, monkeypatch):
    """Jake's run 2026-10-09 18:13, 3: DefiLlama's week-before fees run ~5% under the on-chain credit every week; the
    quarter passes its +/-10%, so in_revenue is a VERIFIED FINDING naming on-chain as the reference, with the
    tolerance on the row."""
    rows = []
    for r in _aero_rows():
        if r[2] in ("revenue_usd", "holders_revenue_usd"):
            wk = r[0] - pd.Timedelta(days=(r[0].dayofweek - 3) % 7)
            r = (r[0], r[1], r[2], ((WK - BRIBE) * 0.95 + BRIBE) / 7, r[4], r[5])
        rows.append(r)
    o = _evaluate(tmp_path, monkeypatch, "Aerodrome", rows)
    rv = o["in_revenue"]
    assert rv["verdict"] == "VERIFIED FINDING", rv
    assert "THE ON-CHAIN FIGURE IS THE REFERENCE" in rv["note"] and "typical (median) week -5.0%" in rv["note"]
    assert o["in_voter_apr_epoch"]["verdict"].startswith("PASS"), o["in_voter_apr_epoch"]


def test_token_depth_reads_the_counter_side_and_stops_once_covered(monkeypatch):
    """The rule's depth: the OTHER token's balance in each voted pool holding the reward token, at the given block, x
    its price; the pools it was paid on first; no further reads once the depth covers the amount."""
    import check_offline_items as coi
    LAP, USDC, AAPL, WETH = "0x" + "aa" * 20, "0x" + "bb" * 20, "0x" + "cc" * 20, "0x" + "dd" * 20
    P1, P2, P3 = "0x" + "01" * 20, "0x" + "02" * 20, "0x" + "03" * 20
    pool_toks = {P1: (USDC, LAP), P2: (LAP, AAPL), P3: (USDC, WETH)}
    bal = {(USDC, P1): 400_000 * 10 ** 6, (AAPL, P2): 50 * 10 ** 18, (WETH, P3): 10 ** 18}
    seen = []

    def batch(chain, calls, chunk=200, block="latest"):
        seen.append((block, list(calls)))
        return [hex(bal.get((to, "0x" + d[-40:]), 0)) for to, d in calls]
    monkeypatch.setattr(coi, "_batch_eth_calls", batch)
    prices = {USDC: {"price": 1.0, "decimals": 6}, AAPL: {"price": 200.0, "decimals": 18}}
    r = coi._token_depth({LAP: 7_600_000.0, USDC: 1_000.0}, {LAP: {P1}, USDC: {P3}}, pool_toks, prices, "0x10")
    assert abs(r[LAP]["depth"] - 410_000.0) < 1e-6 and r[LAP]["pools_read"] == 2 and r[LAP]["pools_held"] == 2
    assert seen[0][0] == "0x10"
    # USDC: its paid-on pool P3 holds 1 WETH, unpriced here — named, not counted; then P1's LAP side, unpriced too
    assert r[USDC]["unpriced_counter"] and r[USDC]["depth"] == 0.0


def test_aerodrome_revenue_and_yield_are_judged_epoch_for_epoch_against_state(tmp_path, monkeypatch):
    """6a. in_revenue = DefiLlama's fees + bribes over the SAME epoch's 7 UTC days vs the on-chain epoch ($1,918,756),
    10%; 6b. the headline's arithmetic on that epoch over Voter.totalWeight reproduces the on-chain 12.56%, the working
    prints the headline's numerator / denominator, and the rebase (485K AERO/week, ~2.5%) is its own labelled row."""
    o = _evaluate(tmp_path, monkeypatch, "Aerodrome", _aero_rows())
    assert o["in_revenue"]["verdict"].startswith("PASS"), o["in_revenue"]
    assert "18 token(s) unpriced" in o["in_revenue"]["note"]
    ep = o["in_voter_apr_epoch"]
    assert ep["verdict"].startswith("PASS"), ep
    assert "HEADLINE: PAYMENT-DAY PRICES, PER-EPOCH STAKE" in ep["note"] and "effective stake" in ep["note"]
    assert "REBASE" in ep["note"]
    assert abs(float(ep["ref"]) - 0.1256) < 0.0005
    rb = o["in_rebase_apr"]
    assert rb["verdict"] == "N/A (recorded, separate stream)" and abs(float(rb["ours"]) - 0.0247) < 0.0005
    assert o["in_apr_today_price"]["verdict"] == "N/A (recorded, not judged)", o["in_apr_today_price"]
    assert o["a3_protocol_yield"]["verdict"].startswith("PASS"), o["a3_protocol_yield"]
    assert not any(v["verdict"].startswith("MATURING") for v in o.values())


def test_aerodrome_revenue_compares_defillamas_week_before_fees_with_the_epochs_credit(tmp_path, monkeypatch):
    """1a (Jake's run 2026-10-09 ~14:10; contracts @1ba3081 Gauge._claimFees at distribute(), Reward books
    epochStart(now)): epoch E's fees are those EARNED in E-1. Weekly fees alternating 1.0M / 1.5M put the same-week
    comparison 20-30% off every week; DefiLlama's week before + the epoch's own bribes matches to the dollar, so the
    quarter sum PASSES and the working shows the spread and the fee leg."""
    fee = lambda e: 1.0e6 if (e - pd.Timestamp("2026-07-02")).days // 7 % 2 == 0 else 1.5e6          # noqa: E731
    o = _evaluate(tmp_path, monkeypatch, "Aerodrome", _aero_rows(fee_week=fee))
    rv = o["in_revenue"]
    assert rv["verdict"].startswith("PASS"), rv
    assert "QUARTER: 12 epoch(s)" in rv["note"] and "mean |gap| 0.0%" in rv["note"], rv["note"][:400]
    assert "fee leg" in rv["note"] and "bribes then" in rv["note"]
    assert o["in_voter_apr_epoch"]["verdict"].startswith("PASS"), o["in_voter_apr_epoch"]


def test_aerodrome_epoch_without_the_split_is_left_out_and_named(tmp_path, monkeypatch):
    """1a: the like-for-like figure needs the on-chain bribes of the epoch and of the one before; an epoch without them
    is not judged, and named."""
    rows = [r for r in _aero_rows() if not (r[2] == "voter_rewards_onchain_bribes_usd"
                                            and r[0] == pd.Timestamp("2026-08-20"))]
    o = _evaluate(tmp_path, monkeypatch, "Aerodrome", rows)
    rv = o["in_revenue"]
    assert rv["verdict"].startswith("PASS"), rv
    assert "QUARTER: 10 epoch(s)" in rv["note"], rv["note"][:300]
    assert "2026-08-20 (on-chain fees/bribes split not stored for 2026-08-20)" in rv["note"]
    assert "2026-08-27 (on-chain fees/bribes split not stored for 2026-08-20)" in rv["note"]


def test_aerodrome_headline_divides_each_epochs_rewards_by_that_epochs_stake(tmp_path, monkeypatch):
    """1c (Jake's run 2026-10-09 ~14:10: "mean stake 1,018,617,757 over its 1 stored day"): Voter.totalWeight read at
    each epoch's start block; each paid day's AERO over ITS epoch's stake. Stakes rising 1% an epoch: the headline is
    sum(tokens_d / stake_epoch(d)) x 365.25 / days — not tokens / the mean of the readings."""
    eps = list(pd.date_range("2026-07-02", "2026-10-01", freq="7D"))
    weights = {e: TW * (1 + 0.01 * i) for i, e in enumerate(eps)}
    o = _evaluate(tmp_path, monkeypatch, "Aerodrome", _aero_rows(weights=weights))
    q0 = [d for d in _days() if pd.Timestamp(ASOF) - pd.Timedelta(days=90) < d <= pd.Timestamp(ASOF)]
    wk = lambda d: d - pd.Timedelta(days=(d.dayofweek - 3) % 7)                                 # noqa: E731
    want = sum(WK / 7 / PX / weights[wk(d)] for d in q0) * 365.25 / len(q0)
    head = o["a3_protocol_yield"]
    assert abs(float(head["ours"]) - want) < 1e-9, (head, want)
    note = o["in_voter_apr_epoch"]["note"]
    assert "PER-EPOCH STAKE" in note and "13 read at the epoch's start" in note and "BORROWED" not in note, note[:600]


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
    # only the latest is missing from the store (the Q0 backfill is its own test)
    latest_only = lambda proj, m, d: d != "2026-10-01"                                         # noqa: E731
    AeroVoter(epoch_reader=epoch_reader, weight_reader=lambda: TW, daily=daily, now=now,
              stored=latest_only).run([p], None, out)
    f = out.frame()
    usd = f[f.metric == "voter_rewards_onchain_usd"]
    assert len(usd) == 1 and str(usd["date"].iloc[0])[:10] == "2026-10-01" and usd["value"].iloc[0] == WK
    apr = f[f.metric == "voter_rewards_onchain_apr"]["value"].iloc[0]
    assert abs(apr - 0.1256) < 0.0005
    assert f[f.metric == "voter_rewards_unpriced_count"]["value"].iloc[0] == 18
    assert f[f.metric == "voter_total_weight_tokens"]["value"].iloc[0] == TW
    out2 = FetchOutput()
    AeroVoter(epoch_reader=epoch_reader, weight_reader=lambda: TW, daily=daily, now=now,
              stored=lambda *a: True).run([p], None, out2)
    assert len(calls) == 1, "the same epoch was read twice"
    # 3c (Jake's run 2026-10-09 11:41): the marker said "stored already" while metrics.db held no row — read it again.
    out3 = FetchOutput()
    AeroVoter(epoch_reader=epoch_reader, weight_reader=lambda: TW, daily=daily, now=now,
              stored=latest_only).run([p], None, out3)
    assert len(calls) == 2 and not out3.frame()[out3.frame().metric == "voter_rewards_onchain_usd"].empty


def test_aero_voter_backfills_every_q0_epoch_with_fees_bribes_and_the_start_weight(tmp_path, monkeypatch):
    """1b/1c (Jake's run 2026-10-09 ~14:10): every epoch from the one before Q0 to the last complete one is read in one
    pass — fees and bribes apart, and Voter.totalWeight at each epoch's start block stored on the epoch's start date
    (archive), never over a live reading."""
    from fetch.aero_voter import AeroVoter
    from fetch.base import FetchOutput
    from fetch.logcache import DailyChecks
    monkeypatch.delenv("TOKEN_METRICS_DAILY_CHECKS", raising=False)
    asked, archive_seen = [], []

    def epochs_reader(eps, archive=True):
        asked.append(list(eps))
        archive_seen.append(archive)
        return {e: {"epoch": e, "voter": "0xv", "pools": 300, "pool_set": "pools with votes now (300)",
                    "total_weight": TW, "total_weight_start": TW - 1e6, "start_block": 123, "usd": WK,
                    "usd_fees": WK - BRIBE, "usd_bribes": BRIBE, "unpriced": [], "unpriced_raw": {}, "priced": 120,
                    "aero_price": PX, "priced_at": "epoch end"} for e in eps}
    now = int(pd.Timestamp("2026-10-09 12:00").timestamp())
    daily = DailyChecks(tmp_path)
    p = config.PROJECT_BY_NAME["Aerodrome"]
    live = {"2026-10-08"}                                     # a live daily weight already stored that day
    stored = lambda proj, m, d: m == "voter_total_weight_tokens" and d in live                # noqa: E731
    out = FetchOutput()
    AeroVoter(epochs_reader=epochs_reader, weight_reader=lambda: TW, daily=daily, now=now, stored=stored,
              max_backfill=0).run([p], None, out)
    f = out.frame()
    starts = sorted(str(d)[:10] for d in f[f.metric == "voter_rewards_onchain_bribes_usd"]["date"])
    assert len(asked) == 1 and len(asked[0]) == 14, asked
    assert starts[0] == "2026-07-02" and starts[-1] == "2026-10-01" and len(starts) == 14, starts
    fe = f[f.metric == "voter_rewards_onchain_fees_usd"]
    assert (fe["value"] == WK - BRIBE).all()
    w = f[(f.metric == "voter_total_weight_tokens") & f["source"].str.endswith(":archive")]
    assert len(w) == 14 and (w["value"] == TW - 1e6).all()
    assert archive_seen[0] is True                            # the seed reads the pool set at past blocks
    # A ROUTINE RUN READS THE NEWEST EPOCH PLUS AT MOST ONE MISSING (backfill_per_run 1 — Jake's run 2026-10-09 15:59:
    # three earlier epochs still outran the 180s tier and stored none), newest first, and names how many are left
    out2 = FetchOutput()
    AeroVoter(epochs_reader=epochs_reader, weight_reader=lambda: TW, daily=daily, now=now,
              stored=lambda proj, m, d: d == "2026-10-01").run([p], None, out2)
    assert len(asked) == 2 and len(asked[1]) == 1 and max(asked[1]) == int(pd.Timestamp("2026-09-24").timestamp())
    assert archive_seen[1] is True                            # a backfill epoch: the pool set at its last block
    assert any("12 earlier epoch(s) still to backfill" in str(e.message) for e in out2.log)
    # the newest alone (everything earlier stored): the pools with votes now, no archive pool set — the daily read
    # that fits the tier
    out3 = FetchOutput()
    AeroVoter(epochs_reader=epochs_reader, weight_reader=lambda: TW, daily=DailyChecks(tmp_path / "fresh"), now=now,
              stored=lambda proj, m, d: d != "2026-10-01").run([p], None, out3)
    assert asked[2] == [int(pd.Timestamp("2026-10-01").timestamp())] and archive_seen[2] is False


def test_aero_voter_stores_the_illiquid_excess_per_epoch_and_names_the_token(tmp_path):
    """Jake 2026-10-09 17:05: the excess above a reward token's DEX depth is stored per epoch (fees and bribes apart),
    the run log names the token, its quoted value and its depth; tokens with no readable voted pool are named, not
    capped. The $ figures stay at the quoted price."""
    from fetch.aero_voter import AeroVoter
    from fetch.base import FetchOutput
    from fetch.logcache import DailyChecks
    e0 = int(pd.Timestamp("2026-10-01").timestamp())
    lap = {"token": "0xb095274743941e953c746f9c228da9c18bb6ec29", "symbol": "LAPTOP", "usd": 7_600_000.0,
           "depth": 400_000.0, "excess": 7_200_000.0}

    def epochs_reader(eps, archive=True):
        return {e: {"epoch": e, "voter": "0xv", "pools": 300, "total_weight": TW, "usd": WK + 7_600_000.0,
                    "usd_fees": WK - BRIBE, "usd_bribes": BRIBE + 7_600_000.0, "usd_illiquid_fees": 0.0,
                    "usd_illiquid_bribes": 7_200_000.0, "liquidity_unread": ["0x" + "ee" * 20], "tokens": [lap],
                    "unpriced": [], "unpriced_raw": {}, "priced": 120, "aero_price": PX, "priced_at": "epoch end"}
                for e in eps}
    out = FetchOutput()
    AeroVoter(epochs_reader=epochs_reader, weight_reader=lambda: TW, daily=DailyChecks(tmp_path),
              now=int(pd.Timestamp("2026-10-09 12:00").timestamp()),
              stored=lambda proj, m, d: d != "2026-10-01").run([config.PROJECT_BY_NAME["Aerodrome"]], None, out)
    f = out.frame()
    ib = f[f.metric == "voter_rewards_illiquid_bribes_usd"]
    assert len(ib) == 1 and ib["value"].iloc[0] == 7_200_000.0 and str(ib["date"].iloc[0])[:10] == "2026-10-01"
    assert f[f.metric == "voter_rewards_illiquid_fees_usd"]["value"].iloc[0] == 0.0
    assert f[f.metric == "voter_rewards_onchain_bribes_usd"]["value"].iloc[0] == BRIBE + 7_600_000.0
    msg = " ".join(str(x.message) for x in out.log)
    assert "illiquid-token rewards excluded $7,200,000" in msg and "LAPTOP (0xb095274743941e953c746f9c228da9c18bb6ec29) $7,600,000 quoted vs $400,000 depth" in msg
    assert "not capped, no readable voted pool: 0x" + "ee" * 20 in msg
    assert e0


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
    l2 = LogScan(own_tier="explorer_gauge_l2")
    ran.clear()
    l2._scan = lambda proj, spec, w, out: ran.append(spec["key"])
    l2.run([p], None, FetchOutput())
    assert ran == ["gauge_pendle_out_arbitrum", "gauge_pendle_out_optimism"]


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


# ================================================================================ real dumps (Jake's run 2026-10-09)
def test_dump_fixture_round_trips_a_store_and_the_verdicts_are_unchanged(tmp_path, monkeypatch):
    """0. dump_fixture.py exports a project's rows (and review-queue flags) from metrics.db; tests/real_dump.py loads
    them back verbatim. The verdicts from the reloaded store must equal those from the original — otherwise a test on
    a real dump would not be testing what Jake's run read. Run-log messages are redacted (no key reaches the file)."""
    import json
    import sqlite3
    import dump_fixture
    import store as sm
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import real_dump
    src = tmp_path / "src"
    src.mkdir()
    direct = _evaluate(src, monkeypatch, "Pendle", _pendle_rows(virtual_from="2026-09-11"))
    db = src / "metrics.db"
    con = sqlite3.connect(db)
    con.execute("INSERT INTO run_log (run_id, ts, source, tier, project, rows, status, message) VALUES "
                "('r', '2099-01-01T00:00:00Z', 'x', 1, 'Pendle', 1, 'ok', "
                "'GET https://api.example.com/v1?apikey=SECRET123 ok')")
    con.execute("INSERT INTO review_queue (run_id, ts, project, metric, date, value, reason, action) VALUES "
                "('r', '2026-10-08T00:00:00Z', 'Pendle', 'locked_tokens_virtual', '2026-10-07', 1.0, "
                "'anchor_unconfirmed', 'stored_flagged')")
    con.commit()
    con.close()
    out = tmp_path / "real_pendle.json"
    dump_fixture.dump("Pendle", db=str(db), out=str(out))
    payload = json.loads(out.read_text())
    assert payload["meta"]["counts"]["metrics"] > 0 and payload["review_queue"][0]["reason"] == "anchor_unconfirmed"
    assert "SECRET123" not in out.read_text()
    payload["meta"]["asof"] = ASOF
    db2 = tmp_path / "reloaded.db"
    real_dump.to_store(payload, db2)
    monkeypatch.setattr(sm, "DB_PATH", str(db2))
    import credibility_report as cr
    rws, tab = cr.evaluate("Pendle", asof=pd.Timestamp(ASOF), narrow=True)
    again = {r["id"]: t[10] for r, t in zip(rws, tab) if r["project"] == "Pendle"}
    assert again == {k: v["verdict"] for k, v in direct.items()}


def test_credibility_and_completeness_print_the_same_signoff_across_midnight(tmp_path, monkeypatch, capsys):
    """External audit 2026-10-09, item 8: credibility_report showed Morpho SIGNED OFF (in_interest_day PASS) while
    completeness_report, run straight after, showed it "CHECK (no figure)". Each evaluated on its own at its own `now`;
    a second evaluation after 00:00 UTC takes in a DefiLlama day not yet published (6 of 7 days). Now ONE evaluation
    per store state is shared: the two sign-off blocks are identical even when the clock crosses midnight between
    them, and a changed store is evaluated afresh."""
    import credibility_report as cr
    import completeness_report as comp
    import store as sm
    db = tmp_path / "metrics.db"
    day = pd.Timestamp("2026-10-09")
    rows = []
    for d in pd.date_range(day - pd.Timedelta(days=20), day - pd.Timedelta(days=1)):      # fees to 10-08 only
        rows += [(d, "Morpho", "fees_usd", 700_000.0, "defillama", 1),
                 (d, "Morpho", "borrow_interest_usd_day_morpho_api", 690_000.0, "morpho_api", 1)]
    st = sm.Store(db)
    st.upsert(pd.DataFrame(rows, columns=["date", "project", "metric", "value", "source", "tier"]))
    st.close()
    monkeypatch.setattr(sm, "DB_PATH", str(db))
    monkeypatch.setitem(sys.modules, "recalc", None)
    monkeypatch.delenv("TOKEN_METRICS_FRESH_EVAL", raising=False)
    clock = {"today": day}
    monkeypatch.setattr(cr, "_today", lambda: clock["today"])
    first = cr.signoff_block("Morpho")
    clock["today"] = day + pd.Timedelta(days=1)          # past midnight UTC: 10-09 not published by DefiLlama
    second = comp.signoff_lines("Morpho")
    assert first == second, (first, second)
    assert "evaluated as of 2026-10-09" in first[0] and not any("in_interest_day" in x for x in first)
    # the bug, shown: a fresh evaluation at the new date has no figure for the 7-day window
    monkeypatch.setenv("TOKEN_METRICS_FRESH_EVAL", "1")
    fresh = cr.signoff_block("Morpho")
    assert any("OPEN in_interest_day: CHECK (no figure)" in x for x in fresh), fresh
    # credibility_report's own printout carries the same block
    monkeypatch.delenv("TOKEN_METRICS_FRESH_EVAL")
    cr.main(["--project", "Morpho", "--roots"])
    out = capsys.readouterr().out
    assert all(cr.a(x) in out for x in fresh if x.strip()), out[:2000]


def test_base_logs_are_found_by_bisecting_the_balance_not_by_scanning(monkeypatch):
    """External audit 2026-10-09, item 9: Blockscout answers 402 on Base and Alchemy's free tier serves eth_getLogs 10
    blocks at a time. The NotifyReward deposits are located by bisecting the reward contract's archive balance of the
    token, and eth_getLogs is asked only for the <=10-block windows where it moved — tens of calls, not 30,000."""
    import check_offline_items as coi
    deposits = {1_000_123: 5, 1_150_000: 7}                      # block -> amount
    calls = {"bal": 0, "logs": []}

    def eth_call(to, data, block="latest", chain="ethereum"):
        calls["bal"] += 1
        b = int(block, 16)
        return hex(sum(v for k, v in deposits.items() if k <= b)), None

    def eth_get_logs(address, topics, a, b, chunk=50_000, chain="ethereum", min_chunk=1_000):
        assert chain == "base" and b - a < 10, (a, b)
        calls["logs"].append((a, b))
        return [{"blockNumber": hex(k), "transactionHash": f"0xt{k}", "topics": topics, "data": hex(v)}
                for k, v in deposits.items() if a <= k <= b], "ok"
    monkeypatch.setattr(coi, "eth_call", eth_call)
    monkeypatch.setattr(coi, "eth_get_logs", eth_get_logs)
    monkeypatch.setattr(coi, "_rpcs_for", lambda chain: ["https://base.example"])
    monkeypatch.setattr(coi, "rpc", lambda url, m, params=None: {"result": {"timestamp": hex(1_757_000_000)}})
    logs, how = coi.base_logs_where_balance_moves("0x" + "aa" * 20, "0x" + "bb" * 20, ["0xtopic"], 1_000_000, 1_302_400)
    assert sorted(int(e["blockNumber"]) for e in logs) == [1_000_123, 1_150_000], logs
    assert all(e["timeStamp"] == 1_757_000_000 for e in logs)
    assert len(calls["logs"]) == 2 and calls["bal"] < 80, calls
    assert "2 balance move(s)" in how
    import config
    assert config.explorer_order(8453) == []                   # no paid Base route is tried


# ===== EXTERNAL AUDIT 2026-10-09, ITEM 1: AERODROME'S FOUNDATION BUY-AND-LOCK =====

def test_foundation_buy_and_lock_is_the_lock_stock_change_net_of_rebase_claims():
    """actual_buyback_tokens = d(locked) + min(d(claimable), 0) on consecutive days: a rebase claim (claimable -> lock)
    nets to 0, accrual is not a buy, a gap day is not differenced across, and a lock leaving the wallets is kept
    negative, under the same source. USD = tokens x the same-day price."""
    import build_workbook as bw
    import config
    fb = config.PROJECT_BY_NAME["Aerodrome"]["foundation_buyback"]
    d = pd.to_datetime(["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-06", "2026-09-07"])
    mk = lambda metric, vals: pd.DataFrame({"date": d, "project": "Aerodrome", "metric": metric,  # noqa: E731
                                            "value": vals, "source": "ve_managed:foundation_locks", "tier": 2})
    groups = {
        ("Aerodrome", fb["stock_metric"]): mk(fb["stock_metric"], [100.0, 412.0, 462.0, 462.0, 500.0, 450.0]),
        #                    09-02: +312 bought;  09-03: +50 claimed in;  09-04: flat, +7 accrued;  09-07: -50 left
        ("Aerodrome", fb["claimable_metric"]): mk(fb["claimable_metric"], [50.0, 50.0, 0.0, 7.0, 7.0, 7.0]),
        ("Aerodrome", "price_usd"): mk("price_usd", [1.0, 0.5, 0.5, 0.5, 0.5, 0.5]),
    }
    bw._foundation_buyback_views(groups)
    tok = groups[("Aerodrome", "actual_buyback_tokens")].set_index("date")["value"]
    assert tok.to_dict() == {pd.Timestamp("2026-09-02"): 312.0, pd.Timestamp("2026-09-03"): 0.0,
                             pd.Timestamp("2026-09-04"): 0.0, pd.Timestamp("2026-09-07"): -50.0}
    # the negative day is kept as measured, under the SAME source as every other row (one measuring point)
    assert groups[("Aerodrome", "actual_buyback_tokens")]["source"].nunique() == 1
    usd = groups[("Aerodrome", "actual_buyback_usd")].set_index("date")["value"]
    assert usd[pd.Timestamp("2026-09-02")] == 156.0
    # the buyback is HELD; after the merged-token launch it stays held until a first-party source says burned
    # (Aerodrome's docs name buybacks, grants and partnerships for the Momentum Fund — no burn), re-checked then
    assert config.buyback_destination_shares("Aerodrome", "2026-09-02")["hold"] == 1.0
    after = config.buyback_destination_shares("Aerodrome", "2026-10-23")
    assert after["hold"] == 1.0 and after["burn"] == 0.0 and not after["verified"]


def test_foundation_lock_read_stores_both_series_once_a_day_and_the_seed_marks_archive(monkeypatch):
    """VeManaged reads the lock stock and the rebase claimable once a day; the seed reads each missing day start at
    its first block with the ':archive' marker (config.mark_source), skipping stored days."""
    import config
    import check_offline_items as coi
    from fetch import ve_managed as vm
    from fetch.base import FetchOutput
    fb = config.PROJECT_BY_NAME["Aerodrome"]["foundation_buyback"]
    owners = list(fb["owners"])
    got = {"locked": 1000.0, "claimable": 5.0, "nfts": 3, "owned_managed": 1,
           "by_owner": {owners[0]: 600.0, owners[1]: 400.0}}

    class Daily:
        def __init__(self):
            self.seen = set()
        def due(self, k, d):  # noqa: E301
            return (k, d) not in self.seen
        def done(self, k, d):  # noqa: E301
            self.seen.add((k, d))

    v = vm.VeManaged(reader=object(), daily=Daily())
    calls = []
    v.foundation_reader = lambda o, block="latest": (calls.append(block), got)[1]
    p = {"name": "Aerodrome", "foundation_buyback": fb}
    out = FetchOutput()
    v.run([p], 30, out)
    v.run([p], 30, out)
    assert len(calls) == 1, "read once a day"
    rows = pd.concat(out.frames)
    assert set(rows["metric"]) == {fb["stock_metric"], fb["claimable_metric"]}
    assert rows.set_index("metric")["value"].to_dict() == {fb["stock_metric"]: 1000.0, fb["claimable_metric"]: 5.0}
    assert "Public Goods Fund 600" in out.log[0].message and "1 owned managed" in out.log[0].message

    monkeypatch.setattr(coi, "rpc_block_at", lambda chain, ts, say=print: 1_000 + ts // 86400)
    stored = lambda name, metric, day: day.endswith("-01")  # noqa: E731
    frames = vm.seed_foundation([p], days=5, stored=stored, say=lambda *_: None,
                                reader=lambda o, block="latest": got)
    seeded = pd.concat(frames)
    # ONE source string, seeded or daily (Jake's run on 20d0eb4)
    assert set(seeded["source"]) == {"ve_managed:foundation_locks"} == set(rows["source"])
    assert not any(str(x.date()).endswith("-01") for x in seeded["date"])


# ===== FOLLOW-UPS TO 0eda6ce (Jake, 2026-10-10): the probes Jake runs locally =====

def test_aethir_arr_formula_names_the_candidate_that_matches_the_tile(monkeypatch, capsys):
    """The probe tests run-rates built from the page's own revenue lists against `arr` and marks one within 1%."""
    import json as _json
    import check_offline_items as coi
    weeks = [{"startDate": f"{d:02d}/09", "amount": a} for d, a in ((8, 1_150_000.0), (15, 1_190_000.0),
                                                                     (22, 1_201_730.77))]
    obj = {"arr": 1_201_730.77 * 52, "weeklyNetworkRevenue": weeks,
           "monthlyNetworkRevenue": [{"month": "August, 2026", "earning": 4_900_000.0}]}
    chunk = _json.dumps(_json.dumps(obj))[1:-1]
    html = f'<html><script>self.__next_f.push([1,"{chunk}"])</script></html>'
    monkeypatch.setattr(coi, "_polite", lambda url, **kw: (html, ""))
    coi.aethir_arr_formula()
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if "last listed week x 52" in ln)
    assert "MATCH" in line and "matches the candidate" in out
    assert "MATCH" not in next(ln for ln in out.splitlines() if "last listed month x 12" in ln)


def test_near_lockups_splits_unvested_from_vested_and_names_no_account(monkeypatch, capsys, tmp_path):
    """Per lockup: balance, NEAR's own locked, unvested (via the contract's vesting schedule), owners' balance — summed;
    a zero-balance lockup is skipped; the buggy code hash is totalled apart; no account id is printed."""
    import check_offline_items as coi
    y = 10 ** 24
    f = tmp_path / "lockups.txt"
    f.write_text("aaaa.lockup.near\nbbbb.lockup.near\ncccc.lockup.near\nnot-a-lockup.near\n")
    monkeypatch.setenv("NEAR_LOCKUPS_FILE", str(f))
    sched = {"start_timestamp": "1", "cliff_timestamp": "2", "end_timestamp": "3"}
    state = {"aaaa.lockup.near": {"get_balance": str(100 * y), "get_locked_amount": str(60 * y),
                                  "get_owners_balance": str(40 * y), "get_vesting_information": {"VestingSchedule": sched},
                                  "get_unvested_amount": str(25 * y)},
             "bbbb.lockup.near": {"get_balance": str(10 * y), "get_locked_amount": str(10 * y),
                                  "get_owners_balance": "0", "get_vesting_information": "None"},
             "cccc.lockup.near": {"get_balance": "0"}}
    seen = []

    def view(acc, method, args=None, block_id=None, pace=0.12):
        seen.append((acc, method, args, block_id))
        return state[acc].get(method), ""
    monkeypatch.setattr(coi, "_near_view", view)

    def fake_rpc(url, method, params=None):
        if method == "block":
            return {"result": {"header": {"height": 123}}}
        return {"result": {"code_hash": "3kVY9qcVRoW3B5498SMX6R3rtSLiCdmBzKs7zcnzDJ7Q"
                           if params["account_id"] == "bbbb.lockup.near" else "x"}}
    monkeypatch.setattr(coi, "rpc", fake_rpc)
    coi.near_lockups()
    out = capsys.readouterr().out
    assert all(b == 123 for *_x, b in seen), "every read at one block"
    assert ("aaaa.lockup.near", "get_unvested_amount", {"vesting_schedule": sched}, 123) in seen
    assert "3 lockup account(s)" in out and "read 2; zero balance 1" in out
    num = lambda label: next(ln for ln in out.splitlines() if label in ln)  # noqa: E731
    assert "110 NEAR" in num("balance in lockups") and "70 NEAR" in num("LOCKED (NEAR's own")
    assert "25 NEAR" in num("STILL UNVESTED") and "45 NEAR" in num("VESTED, still lockup-locked")
    assert "40 NEAR" in num("RELEASED, not withdrawn") and "buggy-code lockups: 1" in out
    assert ".lockup.near" not in out.replace("*.lockup.near", ""), "aggregates only — no account is named"


def test_near_lockups_does_not_spend_bigquery_without_the_switch(monkeypatch, capsys):
    """Without a file, the list query is DRY-RUN and the bytes printed; it runs only with NEAR_LOCKUPS_RUN_BQ=1."""
    import sys
    import types
    import check_offline_items as coi
    monkeypatch.delenv("NEAR_LOCKUPS_FILE", raising=False)
    monkeypatch.delenv("NEAR_LOCKUPS_RUN_BQ", raising=False)
    ran = []

    class Job:
        total_bytes_processed = 300 * 1024 ** 3

    class Client:
        def query(self, sql, job_config=None):
            ran.append(bool(getattr(job_config, "dry_run", False)))
            return Job()

    class NB:
        def _guard_sql(self, sql, spec):
            assert "crypto_near_mainnet_us.receipt_actions" in sql
    monkeypatch.setattr(coi, "_near_cause_setup", lambda: (NB(), {}, Client(), "ds", []))
    bq = types.SimpleNamespace(QueryJobConfig=lambda **kw: types.SimpleNamespace(**kw))
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bq)
    monkeypatch.setitem(sys.modules, "google.cloud", types.SimpleNamespace(bigquery=bq))
    coi.near_lockups()
    out = capsys.readouterr().out
    assert ran == [True] and "300.0 GB" in out and "NOT RUN" in out


def test_fluid_igp137_custody_prints_label_balance_and_transfers_since(monkeypatch, capsys):
    import check_offline_items as coi
    a = coi.FLUID_IGP137_CUSTODY
    monkeypatch.setattr(coi, "_polite", lambda url, **kw: ({"name": None, "is_contract": True, "is_verified": False,
                                                             "public_tags": [], "implementations": []}, ""))
    monkeypatch.setattr(coi, "_source", lambda addr, chain_id=1: {"why": "not verified"})
    monkeypatch.setattr(coi, "_code", lambda addr, chain: "contract (Safe proxy)")
    monkeypatch.setattr(coi, "_bal", lambda token, holder, chain: 5_000_000 * 10 ** 18)
    monkeypatch.setattr(coi, "_block_at", lambda ts: 23_100_000)
    pad = lambda x: "0x" + x.lower()[2:].rjust(64, "0")  # noqa: E731
    inflow = {"data": hex(5_000_000 * 10 ** 18), "timeStamp": "1786694400", "transactionHash": "0xabc",
              "topics": [coi.TRANSFER_TOPIC, pad(coi.FLUID_TEAM_MULTISIG), pad(a)]}
    calls = []

    def logs(chain, token, topics, from_block=0, to_block="latest"):
        calls.append((topics, from_block))
        return ([inflow], "1 log(s)") if topics[2] else ([], "0 log(s)")
    monkeypatch.setattr(coi, "explorer_logs", logs)
    coi.fluid_igp137_custody()
    out = capsys.readouterr().out
    assert all(fb == 23_100_000 for _t, fb in calls) and len(calls) == 2
    assert "FLUID balance now: 5,000,000.00" in out and "total IN: 5,000,000.00" in out and "total OUT: 0.00" in out
    assert "Blockscout: name None" in out and "not verified" in out


# ===== JAKE'S RUN ON 20d0eb4 (2026-10-10) =====

def _aero_long(values, claim=None, start="2026-09-01"):
    days = pd.date_range(start, periods=len(values), freq="D")
    rows = [{"date": d, "project": "Aerodrome", "metric": "foundation_locked_aero_tokens", "value": v,
             "source": "ve_managed:foundation_locks", "tier": 2, "fetched_at": "2026-10-10T00:00:00", "is_manual": 0,
             "entered_on": None, "source_note": None} for d, v in zip(days, values)]
    for d, v in zip(days, claim or [0.0] * len(values)):
        rows.append({**rows[0], "date": d, "metric": "foundation_rebase_claimable_tokens", "value": v})
    for d in days:
        rows.append({**rows[0], "date": d, "metric": "price_usd", "value": 0.5, "source": "coingecko:price"})
    return pd.DataFrame(rows)


def test_aerodrome_buyback_is_one_measuring_point_with_a_negative_day_and_the_split_matches_it():
    """3: a negative day no longer adds a '[NEGATIVE …]' source (which stripped to a trailing space and read as a second
    measuring point, blanking the series as BUG); the buyback row reports, and the retired split equals it (held)."""
    import build_workbook as bw
    long = _aero_long([100.0, 412.0, 462.0, 400.0, 450.0])            # 09-04: -62, a lock left
    data = bw.aggregate(long, pd.DataFrame(), pd.Timestamp("2026-09-06"))
    a = data[(data.project == "Aerodrome")].set_index("metric")
    row = a.loc["actual_buyback_tokens"]
    assert row["status"] != "measuring_point_changed", row["note"]
    assert float(row["q0"]) == 312.0 + 50.0 - 62.0 + 50.0
    assert float(a.loc["retired_buyback_tokens", "q0"]) == float(row["q0"])


def test_the_buyback_split_inherits_a_withheld_parent(monkeypatch):
    """3: E14 0.97% and AH14 -2.076M were computed while the Q0 buyback read n/a — the split rows now inherit the
    parent's withholding (status, blank windows, the reason)."""
    import build_workbook as bw
    real = bw.withheld_for
    monkeypatch.setattr(bw, "withheld_for", lambda p, m, r: (("measuring_point_changed", "forced for the test")
                                                            if m == "actual_buyback_tokens" else real(p, m, r)))
    data = bw.aggregate(_aero_long([100.0, 412.0, 462.0]), pd.DataFrame(), pd.Timestamp("2026-09-04"))
    a = data[data.project == "Aerodrome"].set_index("metric")
    for m in ("retired_buyback_tokens", "buyback_held_tokens"):
        assert a.loc[m, "status"] == "measuring_point_changed" and pd.isna(a.loc[m, "q0"]), m
        assert "inherits actual_buyback_tokens's: forced for the test" in a.loc[m, "note"]


def test_aethir_arr_for_ratios_is_three_months_x4_and_the_tile_is_kept_apart():
    """7: arr_usd = the last 3 complete months of monthlyNetworkRevenue x 4, dated the day after the third month; rows
    stored as arr_usd before the decision (the tile) are read as arr_tile_usd."""
    import build_workbook as bw
    base = {"project": "Aethir", "tier": 3, "fetched_at": "x", "is_manual": 0, "entered_on": None, "source_note": None}
    monthly = pd.DataFrame([{**base, "date": pd.Timestamp(d), "metric": "customer_revenue_monthly_usd", "value": v,
                             "source": "aethir_page:protocol/demand-metric.monthlyNetworkRevenue"}
                            for d, v in (("2026-06-01", 3.0e6), ("2026-07-01", 4.0e6), ("2026-08-01", 4.1e6),
                                         ("2026-09-01", 4.32e6))])
    tile = pd.DataFrame([{**base, "date": pd.Timestamp("2026-10-01"), "metric": "arr_usd", "value": 62_489_999.9,
                          "source": "aethir_page:protocol/demand-metric.arr"}])
    groups = {("Aethir", "customer_revenue_monthly_usd"): monthly, ("Aethir", "arr_usd"): tile}
    bw._aethir_arr_view(groups)
    arr = groups[("Aethir", "arr_usd")].sort_values("date")
    assert arr["date"].iloc[-1] == pd.Timestamp("2026-10-01")
    assert abs(float(arr["value"].iloc[-1]) - (4.0e6 + 4.1e6 + 4.32e6) * 4) < 1e-6          # $49.68M
    assert list(groups[("Aethir", "arr_tile_usd")]["value"]) == [62_489_999.9]
    import config
    assert config.METRICS["arr_usd"]["view_only"] and "arr_tile_usd" in config.METRICS


def test_aethir_customer_revenue_reference_is_the_monthly_list_scaled_to_our_days():
    """6: the reference was the ARR tile x 90/365 (15.408M — one month scaled to a quarter); now the monthly list over
    the last three complete months, scaled to our covered days."""
    import credibility as cred
    import config
    long = pd.DataFrame([{"date": pd.Timestamp(d), "project": "Aethir", "metric": "customer_revenue_monthly_usd",
                          "value": v} for d, v in (("2026-07-01", 4.0e6), ("2026-08-01", 4.1e6), ("2026-09-01", 4.32e6))])
    rows = {"Aethir|customer_revenue_usd": {"q0_covered_days": 91.0}}
    v, d, how = cred._months_scaled("Aethir", rows, long, pd.Timestamp("2026-10-10"),
                                    monthly="customer_revenue_monthly_usd", months=3, cover_metric="customer_revenue_usd")
    assert abs(v - 12.42e6 * 91 / 92) < 1 and d == "2026-09" and "2026-07, 2026-08, 2026-09" in how
    spec = config.CREDIBILITY["Aethir"]["a2_customer_revenue"]
    assert spec["formula"] == "months_scaled" and spec["tol"] == 25.0 and "scale" not in spec


def test_aethir_node_reward_share_is_50pct_with_its_source():
    import config
    ref = config.PROJECT_BY_NAME["Aethir"]
    ar = next(v["allocation_reference"] for v in ref.values() if isinstance(v, dict) and "allocation_reference" in v)
    assert ar["checker_nodes_and_compute_providers_pct"] == 0.50 and ar["checker_nodes_and_compute_providers_tokens"] == 21e9
    assert ar["split"] == {"checker_nodes_pct": 0.15, "compute_providers_pct": 0.35}
    assert "token-vesting" in ar["source_url"] and ar["superseded"]["pct"] == 0.55


def test_fluid_avocado_wallet_alert_fires_only_when_fluid_leaves():
    """4: relabelled a team-controlled Avocado wallet; a balance read below 5,000,000 is a Review Queue item."""
    import config
    from fetch.base import FetchOutput
    from fetch.validate import check_watched_wallet, REASON_WATCHED_OUTFLOW
    assert "team-controlled Avocado wallet" in config.METRICS["noncirculating_igp137_tokens"]["label"]
    row = lambda v: pd.DataFrame([{"date": pd.Timestamp("2026-10-10"), "project": "Fluid",  # noqa: E731
                                   "metric": "noncirculating_igp137_tokens", "value": v, "source": "chain:x"}])
    out = FetchOutput()
    check_watched_wallet(row(5_000_000.0), out)
    assert not out.review
    check_watched_wallet(row(4_250_000.0), out)
    assert len(out.review) == 1 and REASON_WATCHED_OUTFLOW in str(out.review[0])


def test_aerodrome_bribe_scan_flags_unpriced_at_deposit_and_reports_what_the_cap_missed(monkeypatch, capsys):
    """1: per deposit — the depositor's rewards listed; 'unpriced at deposit' is a FLAG (not an exclusion), and a
    flagged token the cap did not catch is reported."""
    import check_offline_items as coi
    import time as _t
    week = 7 * 86400
    last = int(_t.time()) // week * week - week
    lap, xdp, rc1, rc2 = "0x" + "aa" * 20, "0x" + "bb" * 20, "0x" + "11" * 20, "0x" + "22" * 20
    tok = lambda t, sym, usd, excess, rc: {"token": t, "symbol": sym, "kinds": ["bribes"], "price": 1.0, "usd": usd,  # noqa: E731
                                          "depth": 1000.0, "excess": excess, "rewards": {rc: ("bribes", "0xpool", 1)}}

    def epochs(eps, archive=True, **kw):
        return {e: ({"usd_bribes": 9e6, "end_block": 200, "tokens": [tok(lap, "LAPTOP", 7.5e6, 6.9e6, rc1),
                                                                    tok(xdp, "XDP", 6e5, 0.0, rc2)]}
                    if e == last else {"usd_bribes": 0.0, "end_block": 200, "tokens": []}) for e in eps}
    monkeypatch.setattr(coi, "aerodrome_voter_epochs", epochs)
    monkeypatch.setattr(coi, "rpc_block_at", lambda chain, ts, say=print: 100)
    pad = lambda a: "0x" + a[2:].rjust(64, "0")  # noqa: E731
    dep = coi.AERO_BRIBE_DEPOSITOR

    def logs(token, holder, topics, b0, b1, say=print, max_calls=600, **kw):
        return [{"topics": [topics[0], pad(dep), topics[2], topics[3]], "data": hex(4 * 10 ** 24),
                 "timeStamp": 1_786_000_000, "transactionHash": "0xt" + token[2:6]}], "1 log(s)"
    monkeypatch.setattr(coi, "base_logs_where_balance_moves", logs)
    monkeypatch.setattr(coi, "_llama_prices", lambda keys, at, say=print: {})       # nothing priced at deposit
    coi.aerodrome_bribe_scan()
    out = capsys.readouterr().out
    assert "(a) REWARDS POSTED BY" in out and out.count("0xt") >= 2
    assert out.count("UNPRICED AT DEPOSIT") >= 2
    missed = out.split("NOT CAUGHT BY THE CAP:")[1]
    assert "XDP" in missed and "LAPTOP" not in missed.split("PASTE BACK")[0]


def test_near_bq_backfill_status_names_the_reauth_stop(monkeypatch, capsys):
    import check_offline_items as coi
    from fetch import near_bigquery as nbq
    today = str(pd.Timestamp.now().date())
    st = {"days": {f"2026-0{m}-0{d}": {} for m in (4, 5) for d in (1, 2)}, "reauth": {"count": 2, "dates": [today]},
          "ledger": {today[:7]: 100 * 10 ** 9}, "topup_bytes": {"bytes": 2e9, "on": today, "source": "t"}}
    monkeypatch.setattr(nbq.NearBigQuery, "_load", lambda self: st)
    coi.near_bq_backfill_status()
    out = capsys.readouterr().out
    assert "days held 4 of 365" in out and "re-authentication needed on 2 day(s)" in out
    assert "resumes from this state" in out


def test_morpho_listed_vs_all_splits_listed_self_lent_and_other(monkeypatch, capsys):
    """2: the API summed over ALL markets, split listed / unlisted other / unlisted self-lent (supply == borrow on a
    market over $1M), interest per day from borrowApy; a historicalState schema error is printed, not guessed."""
    import check_offline_items as coi
    import types
    import store as store_mod
    monkeypatch.setattr(store_mod, "DB_PATH", "/nonexistent/metrics.db")
    mk = lambda k, listed, s, b, apy: {"uniqueKey": k * 8, "chain": {"id": 1}, "listed": listed,  # noqa: E731
                                       "loanAsset": {"symbol": "USDC"}, "collateralAsset": {"symbol": "X"},
                                       "state": {"supplyAssetsUsd": s, "borrowAssetsUsd": b, "borrowApy": apy}}
    markets = [mk("a", True, 100e6, 90e6, 0.05), mk("b", False, 20e6, 10e6, 0.08), mk("c", False, 10e9, 10e9, 0.10)]

    def post(url, json=None, **kw):
        q = json["query"]
        if "chains" in q:
            body = {"data": {"chains": [{"id": 1}]}}
        elif "historicalState" in q:
            body = {"errors": [{"message": 'Cannot query field "historicalState"'}]}
        else:
            body = {"data": {"markets": {"pageInfo": {"countTotal": 3}, "items": markets}}}
        return types.SimpleNamespace(json=lambda: body)
    monkeypatch.setattr(coi.requests, "post", post)
    coi.morpho_interest_listed_vs_all()
    out = capsys.readouterr().out
    d = lambda b, a: b * ((1 + a) ** (1 / 365) - 1)  # noqa: E731
    assert f"listed                    1 markets  borrow $        90,000,000  interest ${d(90e6, 0.05):>14,.0f}" in out
    assert "unlisted self-lent        1 markets" in out and "unlisted other            1 markets" in out
    assert "historicalState not read" in out and "tolerance stays 10%" in out
