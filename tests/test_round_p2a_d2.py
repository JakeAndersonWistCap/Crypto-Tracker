"""
tests/test_round_p2a_d2.py — Round P2a, Part D2 (2026-10-27): Aerodrome after the Aero migration. No network: every
figure below was read on-chain on 2026-10-27 (public Base / Ethereum / OP / Arbitrum RPCs) or from
Dromos-Labs/metadex-public@8b506b45, and is written here as data.
"""
from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402

NC_SRC = ("chain:sum(base:noncirc_foundation_multisig+base:noncirc_public_goods_fund+base:noncirc_airdrop_distributor+"
          "base:noncirc_flight_school+base:noncirc_buyback_locked_funds+base:noncirc_tge_incentives+"
          "base:noncirc_velodrome_foundation_airdrop)")
FILING_SRC = "ve_managed:0xeBf418Fe.weights"


def _g(metric, rows):
    return pd.DataFrame({"date": pd.to_datetime([d for d, _v, _s in rows]), "project": "Aerodrome", "metric": metric,
                         "value": [float(v) for _d, v, _s in rows], "tier": 2, "source": [s for _d, _v, s in rows],
                         "is_manual": False, "entered_on": ""})


def _migration_groups():
    """The two unlock legs as Jake's store holds them across the migration: the legacy reads at the values read on-chain
    at noon UTC (filing_managed_lock_tokens via check_offline_items.aerodrome_managed_deposits, the seven wallets' legacy
    AERO via balanceOf), plus a genuine 1,000,000 release on 10-15 so a pre-migration unlock is seen to still count, and
    the v3 non-circulating row of 10-27 (aero_v3.supply_once)."""
    days = [d.strftime("%Y-%m-%d") for d in pd.date_range("2026-10-10", "2026-10-27")]
    filing = {"2026-10-21": 245_053_379.52895224, "2026-10-22": 5_541_059.884908306}
    nc, fl = [], []
    for d in days:
        f = filing.get(d, 444_276_994.96777046 if d <= "2026-10-20" else 2_848_932.0365878115)
        n = (37_868_406.6657 if d < "2026-10-15" else 36_868_406.6657 if d < "2026-10-22"
             else 7_622_093.6657 if d == "2026-10-22" else 741.2118)
        fl.append((d, f, FILING_SRC))
        if d == "2026-10-27":
            nc.append((d, 87_113_881.3688, "aero_v3:supply_once[ratio 1]"))
        else:
            nc.append((d, n, NC_SRC))
    return {("Aerodrome", "noncirculating_holding_tokens"): _g("noncirculating_holding_tokens", nc),
            ("Aerodrome", "filing_managed_lock_tokens"): _g("filing_managed_lock_tokens", fl)}


LEGS = ("noncirculating_holding_tokens", "filing_managed_lock_tokens")


# ============================================================================================ item 6: dilution
def test_the_migration_is_not_an_unlock():
    """Jake's 16:47 Summary: total dilution 1.326 (132% a year), 1.258bn tokens a year. The legacy legs saw the filing
    wallets' AERO and managed locks LEAVE for the new token (~478.3M from 10-20) and counted every fall as a release.
    From config.UNLOCK_LEGACY_CUT's last_day a legacy row is not read; the 10-15 release before it still counts."""
    assert config.unlock_stock_metrics("Aerodrome") == LEGS
    groups = _migration_groups()
    bw.UNLOCK_LEGACY_DROPPED.clear()
    bw._unlock_release_view(groups, "Aerodrome", "unlock_release_tokens", LEGS)
    rel = bw._daily(groups[("Aerodrome", "unlock_release_tokens")])
    assert rel.sum() == pytest.approx(1_000_000.0)                    # the genuine 10-15 release, nothing else
    assert rel.index.max() <= pd.Timestamp("2026-10-19")              # no day after the cut: Unknown, not 0
    src = groups[("Aerodrome", "unlock_release_tokens")]["source"].iloc[0]
    assert "[legacy legs read to 2026-10-19, config.UNLOCK_LEGACY_CUT]" in src
    assert bw.UNLOCK_LEGACY_DROPPED == {("Aerodrome", "unlock_release_tokens", "noncirculating_holding_tokens"): 7,
                                        ("Aerodrome", "unlock_release_tokens", "filing_managed_lock_tokens"): 8}
    ret = bw._daily(groups[("Aerodrome", "issuer_return_tokens")])
    assert ret.sum() == pytest.approx(0.0)
    # the measuring point the trajectory / guard read is unchanged by the note
    import trajectory
    assert trajectory.basis_of("unlock_release_tokens", src) == "derived:unlock_release"


def test_without_the_cut_the_migration_reads_as_478m_of_unlocks(monkeypatch):
    """The bug, reproduced: with no declared cut the same rows release 199.2M (10-21), 268.8M (10-22), 10.3M (10-23)."""
    monkeypatch.setattr(config, "UNLOCK_LEGACY_CUT", {})
    groups = _migration_groups()
    bw._unlock_release_view(groups, "Aerodrome", "unlock_release_tokens", LEGS)
    rel = bw._daily(groups[("Aerodrome", "unlock_release_tokens")])
    assert rel.loc["2026-10-21"] == pytest.approx(199_223_615.4388, abs=1)
    assert rel.loc["2026-10-22"] == pytest.approx(239_512_319.6440 + 29_246_313.0, abs=1)
    assert rel.loc["2026-10-23"] == pytest.approx(2_692_127.8483 + 7_621_352.4539, abs=1)
    assert rel.sum() - 1_000_000.0 == pytest.approx(478_295_728.4, abs=10)


def test_the_cut_is_aerodromes_alone():
    assert config.unlock_leg_legacy_cut("GEODNET", "noncirculating_holding_tokens") is None
    assert config.unlock_leg_legacy_cut("Aerodrome", "total_supply") is None
    cut = config.unlock_leg_legacy_cut("Aerodrome", "filing_managed_lock_tokens")
    assert cut["last_day"] == "2026-10-19" and cut["v3_prefix"] == config.AERODROME_MIGRATION["v3_source"]


def test_a_v3_row_after_the_cut_is_read():
    """A v3-measured leg after the cut is kept (both legs from aero_v3: their falls are the v3 set's)."""
    days = ["2026-10-25", "2026-10-26", "2026-10-27"]
    groups = {("Aerodrome", m): _g(m, [(d, v, "aero_v3:x") for d, v in zip(days, vals)])
              for m, vals in (("noncirculating_holding_tokens", (90.0, 80.0, 80.0)),
                              ("filing_managed_lock_tokens", (10.0, 10.0, 5.0)))}
    bw._unlock_release_view(groups, "Aerodrome", "unlock_release_tokens", LEGS)
    assert bw._daily(groups[("Aerodrome", "unlock_release_tokens")]).sum() == pytest.approx(15.0)


# ===================================================================================== item 2: emission rate label
def test_emission_rate_current_is_labelled_the_ceiling():
    lab = config.metric_label("Aerodrome", "emission_rate_current")
    assert lab != "emission_rate_current" and "CEILING" in lab and "not AERO emitted" in lab
    from fetch import aero_v3 as a
    assert "ceiling" in a.EMISSION_RATE_LABEL and "gross_issuance_tokens" in a.EMISSION_RATE_LABEL
    assert "emission_rate_current" not in config.METRICS          # no archetype gap row for it (label is per project)


def test_activation_timestamp_is_the_next_epoch_after_migration_open():
    """Minter.sol @8b506b45 L130: ACTIVATION_TIMESTAMP = ProtocolTimeLibrary.epochNext(migrationOpen) (start of the next
    Unix week). Constructor arg migrationOpen 0x6ad935e0 = 2026-10-21 22:00 UTC -> 2026-10-22 00:00 UTC, which the
    deployed Minter returns (ACTIVATION_TIMESTAMP() = 1792627200, read 2026-10-27). Not migrationOpen + 1 week."""
    mo = 0x6AD935E0
    assert pd.Timestamp(mo, unit="s") == pd.Timestamp("2026-10-21 22:00")
    epoch_next = mo - mo % (7 * 86400) + 7 * 86400
    assert epoch_next == 1792627200 and pd.Timestamp(epoch_next, unit="s") == pd.Timestamp("2026-10-22 00:00")
    assert pd.Timestamp(mo + 7 * 86400, unit="s") != pd.Timestamp(epoch_next, unit="s")


def test_v3_rate_label_reaches_the_run_log(monkeypatch):
    from fetch import aero_v3 as a
    from fetch.base import FetchOutput
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-10-27"))
    w = lambda v: "0x" + f"{v:064x}"  # noqa: E731
    now = 2964 * a.WEEK + 3600
    ans = {a.selector("emissionsMinted"): w(2_248_284 * a.WAD), a.selector("teamRate"): w(50_000),
           a.selector("emissionRate"): w(13_489_345_000_000_000_000), a.selector("supply"): w(900 * a.WAD),
           a.selector("totalVotingPower"): w(800 * a.WAD), a.selector("permanentStakeBalance"): w(300 * a.WAD),
           a.selector("balanceOf"): w(905 * a.WAD), a.selector("VOTER"): w(int("0x" + "4a" * 20, 16)),
           a.selector("totalPoint"): w(0) + f"{0:064x}" + f"{now:064x}" + f"{800 * a.WAD:064x}"}
    st = {"launched": True, "source_url": "https://github.com/Dromos-Labs", "verified": "2026-10-27",
          "addresses": {"minter": "0x5f1c9a3e7b2d48c06e9f31a7b5d2c8e4f0a6b913",
                        "voting_escrow": "0x8e2d7c4b1a9f36e05d8c27b4a1f9e6d3c0b5a872",
                        "token": "0x3a7f9c1e5b8d24f06a3c9e7b1d5f82a4c6e0b391"}}

    def rpc(method, params):
        if method == "eth_getCode":
            return "0x6080"
        if method == "eth_blockNumber":
            raise RuntimeError("supply path not exercised here")
        return ans[params[0]["data"][:10]]
    out = FetchOutput()
    a.AeroV3(rpc=rpc, state=st, now=now).run([config.PROJECT_BY_NAME["Aerodrome"]], 30, out)
    line = next(e.message for e in out.log if e.message.startswith("emission_rate_current"))
    assert "8,158,355.8560 AERO a week" in line and a.EMISSION_RATE_LABEL in line


# ==================================================================================== item 4: the 7 handovers
V3_POINTS = {  # the source each v3 row is written with (fetch/aero_v3.py), as the store holds it
    "gross_issuance_tokens": config.mark_source("aero_v3:minter.emissionsMinted", "delta"),
    "ve_locked_supply_tokens": "aero_v3:sAERO.supply",
    "ve_voting_power_tokens": "aero_v3:sAERO.totalVotingPower",
    "permanent_locked_tokens": "aero_v3:sAERO.permanentStakeBalance",
    "locked_tokens": "aero_v3:token.balanceOf(sAERO)",
    "voter_total_weight_tokens": "aero_v3:voter.totalPoint[resolved]",
    "voter_rewards_onchain_usd": "aero_v3:epoch[288 managers]",
    "total_supply": "aero_v3:supply_once[ratio 1]",
    "noncirculating_holding_tokens": "aero_v3:supply_once[ratio 1]",
}


@pytest.mark.parametrize("metric", sorted(V3_POINTS))
def test_every_replaced_metric_hands_over_to_the_point_v3_writes(metric):
    """Rule 10 (SPEC §1): the v3 measuring point is a DECLARED handover point, read the way handover_refusal, the
    measuring guard (trajectory.basis_of) and the fetch-time legs check read it — the bracket annotation stripped."""
    from fetch.base import _measuring_point
    import measuring_guard
    import trajectory
    decl = config.declared_handover("Aerodrome", metric)
    assert decl and decl.get("ordered_points")
    pts = [_measuring_point(x) for x in decl["ordered_points"]]
    assert _measuring_point(V3_POINTS[metric]) == pts[-1]
    assert trajectory.basis_of(metric, V3_POINTS[metric]) in measuring_guard._declared("Aerodrome", metric)
    if metric not in ("total_supply",):
        assert decl["declared"] == config.AERODROME_MIGRATION["cutover_utc"]


def test_seven_metric_handover_and_watch_stays_on():
    seven = [m for m, d in config.PROJECT_BY_NAME["Aerodrome"]["series_handover"].items()
             if d.get("why") == "the Aero migration (2026-10-22 00:00 UTC; round F, F1)"]
    assert sorted(seven) == sorted(m for m in V3_POINTS if m not in ("total_supply", "noncirculating_holding_tokens"))
    assert config.AERODROME_MIGRATION_WATCH["built"] is None      # MIGRATION WATCH on until v3 rows are held


# ============================================================================ item 3: chain tokens (named gaps)
def test_switch_file_names_every_launch_chain_gap_and_stays_complete():
    from fetch.validate import _migration_switch_missing
    st = config.aerodrome_migration_state()
    assert st["chain_tokens"] == {} and st["bridge_escrows"] == []
    gaps = st["chain_tokens_gaps"]
    for ch in config.AERODROME_MIGRATION["launch_chains"]:
        if ch != "base":
            assert gaps[ch].startswith("GAP - "), ch
    for ch in ("ethereum", "optimism", "arbitrum"):
        assert "symbol 'xAERO'" in gaps[ch] and "holds no code" in gaps[ch]
    for ch in ("arc", "ink", "robinhood"):
        assert "no " in gaps[ch] and "RPC in config.DEFAULT_RPC" in gaps[ch]
    assert _migration_switch_missing(st, pd.Timestamp("2026-10-27")) == []


def test_offline_check_prints_each_chain_and_the_park_condition():
    import check_offline_items as coi
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = coi.aero_migration()
    text = buf.getvalue()
    assert rc == 0 and "COMPLETE" in text
    for ch in ("ethereum", "optimism", "arc", "ink", "robinhood", "arbitrum"):
        assert f"chain_tokens.{ch}" in text and "none — GAP - " in text
    assert "PARKED — migration:" in text


# ============================================================================ item 7: the park condition
def test_park_reason_names_every_open_condition(monkeypatch):
    st = config.aerodrome_migration_state()
    day = pd.Timestamp("2026-10-27")
    why = config.aerodrome_park_reason(st, day)
    # P2c-3: the one open condition is the primary source; the filing-wallet gap is traced (P2b-1)
    assert why.startswith("PARKED — migration: ") and "no primary source publishes" in why and "traced" in why
    assert "no v3 rows held yet" not in why
    assert "Aerodrome" in config.DETAIL_PARKED                     # stays parked
    monkeypatch.setitem(config.AERODROME_MIGRATION_WATCH, "built", "2026-10-28")
    monkeypatch.setitem(config.AERODROME_MIGRATION, "unpark_blockers", ())
    assert config.aerodrome_park_reason(st, day) == ""
    assert "is not launched" in config.aerodrome_park_reason({"launched": False}, day)
    bad = dict(st, addresses=dict(st["addresses"], minter="0x…"))
    assert "addresses.minter" in config.aerodrome_park_reason(bad, day)
