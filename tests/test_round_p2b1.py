"""
tests/test_round_p2b1.py — Round P2b-1 (2026-10-27): Aerodrome's circulating after the migration. The filing wallets'
locked AERO moved from managed veAERO to sAERO and Aero Relays (traced at Base block 53,174,900); fetch/aero_v3.py now
reads it at the supply's block as filing_managed_lock_tokens. No network: a fake RPC answers Multicall3.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
from eth_abi import decode, encode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402

HEAD = 53_174_900
VE = "0x8e2d7c4b1a9f36e05d8c27b4a1f9e6d3c0b5a872"           # the test switch file's voting_escrow (sAERO)
REG = "0x6c1e9a4f2b8d37e05a9c1f6b3d8e2a7c4f0b5d19"
FAC = "0x4f8b2e6a1c9d35f07b2e8a4c6d1f9b3e5a7c0d28"
RELAY_A = "0x9a3c7e1f5b2d48a06c9e3f7b1d5a2c8e4f6b0d37"
RELAY_B = "0x1d7f3b9e5a2c46b08d1f7a3e9c5b2d6f4a8e0c51"
PT_A = "0x7b5e1d9c3f2a48e06b7d1c5f9e3a2b8d4c6f0e93"
PT_B = "0x3e9a5c1f7d2b48e06a3f9c5e1b7d2a8c4e6f0b75"
OTHER = "0x5c2e8a4f1d9b37e06c2a8f4d1b9e3c7a5f0d6e82"
WAD = 10 ** 18


def _owners():
    return [o.lower() for o in config.PROJECT_BY_NAME["Aerodrome"]["ve_managed_holdings"]["owners"]]


def _state(**extra):
    from test_round_g2 import _aero_state
    return {**_aero_state(), "relay_factory_registry": REG, **extra}


def _chain():
    """The answers, keyed (to, function name, args). Team (owner 0): two legacy veNFTs, one still in a managed NFT with
    weight 1,000; one sAERO with staked 0 (moved to a Relay). Velodrome Foundation airdrop (owner 5): one sAERO staked
    500. Relay A: PT supply 1,000 against backing 1,500, Team holds 400 PT -> 600 AERO. Relay B: Flight School (owner
    2) has a live pending deposit of 70; a consumed entry and another recipient's entry do not count."""
    from fetch import aero_v3 as a
    o = _owners()
    team, fs, velo = o[0], o[2], o[5]
    legacy = a.LEGACY_ESCROW
    ans = {}
    for x in o:
        ans[(VE, "balanceOf", (x,))] = [1 if x in (team, velo) else 0]
        ans[(legacy, "balanceOf", (x,))] = [2 if x == team else 0]
        for pt in (PT_A, PT_B):
            ans[(pt, "balanceOf", (x,))] = [400 * WAD if (pt, x) == (PT_A, team) else 0]
    ans[(REG, "relayFactories", ())] = ("address[]", [FAC])
    ans[(VE, "tokenOfOwnerByIndex", (team, 0))] = [11]
    ans[(VE, "tokenOfOwnerByIndex", (velo, 0))] = [12]
    ans[(legacy, "ownerToNFTokenIdList", (team, 0))] = [101]
    ans[(legacy, "ownerToNFTokenIdList", (team, 1))] = [102]
    ans[(FAC, "allRelaysLength", ())] = [2]
    ans[(VE, "staked", (11,))] = [0, 0, 0]
    ans[(VE, "staked", (12,))] = [500 * WAD, 2_000_000_000, 0]
    ans[(legacy, "idToManaged", (101,))] = [9001]
    ans[(legacy, "idToManaged", (102,))] = [0]
    ans[(FAC, "allRelays", (0, 2))] = ("address[]", [RELAY_A, RELAY_B])
    ans[(legacy, "weights", (101, 9001))] = [1_000 * WAD]
    ans[(RELAY_A, "principalToken", ())] = [int(PT_A, 16)]
    ans[(RELAY_B, "principalToken", ())] = [int(PT_B, 16)]
    ans[(RELAY_A, "totalBacking", ())] = [1_500 * WAD]
    ans[(RELAY_B, "totalBacking", ())] = [70 * WAD]
    ans[(RELAY_A, "depositList", ())] = [0, 0, 0]
    ans[(RELAY_B, "depositList", ())] = [1, 3, 2]                    # ids 1..3, two live
    ans[(PT_A, "totalSupply", ())] = [1_000 * WAD]
    ans[(PT_B, "totalSupply", ())] = [0]
    ans[(RELAY_B, "pendingDeposits", (1,))] = [int(fs, 16), 1_761_000_000, 77, 70 * WAD]
    ans[(RELAY_B, "pendingDeposits", (2,))] = [int(fs, 16), 0, 78, 999 * WAD]          # consumed: reads zero time
    ans[(RELAY_B, "pendingDeposits", (3,))] = [int(OTHER, 16), 1_761_000_100, 79, 5 * WAD]
    return ans


def _rpc(ans, blocks, fail_on=None):
    """A fake Base RPC that answers Multicall3.aggregate3 from `ans` (allowFailure false: one miss reverts all)."""
    from fetch import aero_v3 as a
    by_sel = {}
    for (to, fn, args), v in ans.items():
        by_sel[(to.lower(), a.AeroV3.calldata(fn, *args))] = v

    def rpc(method, params):
        assert method == "eth_call", method
        assert params[0]["to"].lower() == a.MULTICALL3
        blocks.append(params[1])
        (calls,) = decode(["(address,bool,bytes)[]"], bytes.fromhex(params[0]["data"][10:]))
        res = []
        for to, allow, data in calls:
            assert allow is False
            key = (to.lower(), "0x" + data.hex())
            if fail_on and fail_on in key[1] or key not in by_sel:
                raise RuntimeError(f"execution reverted ({to}, {data.hex()[:8]})")
            v = by_sel[key]
            ret = encode([v[0]], [v[1]]) if isinstance(v, tuple) else b"".join(int(x).to_bytes(32, "big") for x in v)
            res.append((True, ret))
        return "0x" + encode(["(bool,bytes)[]"], [res]).hex()
    return rpc


def test_filing_calls_match_the_transcribed_declarations_and_keccak():
    """Every call _filing_locks adds is in fixtures/aero_v3_filing_abi.json — the declaration line verbatim from the
    pinned source (@8b506b45 for v3, @1ba30815 for the legacy escrow) — and its selector is keccak of the signature."""
    import json

    from eth_utils import keccak

    from fetch import aero_v3 as a
    fx = json.loads((ROOT / "tests" / "fixtures" / "aero_v3_filing_abi.json").read_text(encoding="utf-8"))
    assert set(fx["functions"]) == set(a.FILING_CALLS) and not set(a.FILING_CALLS) & set(a.CALLS)
    for name, (sig, rets, repo, file, line) in a.FILING_CALLS.items():
        f = fx["functions"][name]
        assert (f["signature"], tuple(f["returns"]), f["repo"], f["file"], f["line"]) == (sig, rets, repo, file, line)
        assert a.selector(name) == "0x" + keccak(text=sig).hex()[:8]
        word = "ERC721Enumerable" if name == "tokenOfOwnerByIndex" else name
        assert word in f["text"], name
    assert a.FILING_REPO.endswith("8b506b450aaa9d4a9f1a77e264f2d9dd508e9ddf")
    assert config.aerodrome_migration_state()["source_url"].split("/blob/")[1].startswith(a.FILING_REPO.split("@")[1])


def test_relay_claim_is_shares_times_backing_over_supply_floored():
    from fetch.aero_v3 import relay_claim
    assert relay_claim(400, 1_500, 1_000) == 600
    assert relay_claim(1, 2, 3) == 0                                 # floor, as QueueLib L439
    assert relay_claim(0, 5, 0) == 0
    with pytest.raises(RuntimeError, match="totalSupply of 0"):
        relay_claim(1, 5, 0)


def test_filing_locks_sums_legacy_weights_sAERO_relay_principal_and_pending_at_one_block(monkeypatch):
    from fetch import aero_v3 as a
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-10-28"))
    blocks = []
    rdr = a.AeroV3(rpc=_rpc(_chain(), blocks), state=_state())
    out = FetchOutput()
    rdr._filing_locks("Aerodrome", HEAD, out, _state())
    f = out.frame().set_index("metric")
    # 1,000 legacy managed weight + 500 sAERO + 400 x 1,500 / 1,000 Relay principal + 70 pending = 2,170
    assert f.loc["filing_managed_lock_tokens", "value"] == pytest.approx(2_170.0)
    assert f.loc["filing_managed_lock_tokens", "source"] == "aero_v3:filing_locks"
    assert set(blocks) == {hex(HEAD)}                                 # every read at the supply's block
    msg = next(e.message for e in out.log if "filing_managed_lock_tokens" in e.message)
    assert f"Base block {HEAD:,}" in msg and "Relay principal 600.0000 (2 Relay(s) of 1 factory(ies))" in msg
    assert "pending Relay deposits 70.0000" in msg and "legacy managed veAERO 1,000.0000 (1 veNFT(s))" in msg


def test_a_failed_read_writes_no_filing_row_and_says_unknown(monkeypatch):
    """allowFailure false: one reverted call (here Relay A's totalBacking) leaves NO filing_managed_lock_tokens row —
    the day's circulating reads n/a — while total and non-circulating, read before it, are still written."""
    from fetch import aero_v3 as a
    from test_round_g2 import _supply_rpc
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-10-28"))
    supply = _supply_rpc(a, basis=1_820e6, converted=1_820e6)
    multi = _rpc(_chain(), [], fail_on=a.selector("totalBacking")[2:])

    def rpc(method, params):
        if method == "eth_call" and params[0]["to"].lower() == a.MULTICALL3:
            return multi(method, params)
        return supply(method, params)
    rdr = a.AeroV3(rpc=rpc, state=_state())
    out = FetchOutput()
    rdr._supply("Aerodrome", {k: v.lower() for k, v in _state()["addresses"].items()}, out, _state())
    got = set(out.frame()["metric"])
    assert {"total_supply", "noncirculating_holding_tokens"} <= got and "filing_managed_lock_tokens" not in got
    fail = [e.message for e in out.log if e.status == "failed"]
    assert any("filing_managed_lock_tokens" in m and "unknown, never 0" in m for m in fail)


def test_without_the_registry_the_filing_read_is_a_named_failure_not_zero(monkeypatch):
    from fetch import aero_v3 as a
    from test_round_g2 import _aero_state, _supply_rpc
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-10-28"))
    rdr = a.AeroV3(rpc=_supply_rpc(a, basis=1_820e6, converted=1_820e6), state=_aero_state())
    out = FetchOutput()
    rdr._supply("Aerodrome", {k: v.lower() for k, v in _aero_state()["addresses"].items()}, out, _aero_state())
    assert "filing_managed_lock_tokens" not in set(out.frame()["metric"])
    assert any("relay_factory_registry" in e.message for e in out.log if e.status == "failed")


def test_the_switch_file_names_the_registry_and_a_placeholder_is_refused():
    from fetch.validate import _migration_switch_missing
    day = pd.Timestamp("2026-10-28")
    st = config.aerodrome_migration_state()
    assert st["relay_factory_registry"].lower() == "0x0dfbe7b949eae4b826e33146f1df4c0c60c78f99"
    assert not _migration_switch_missing(st, day)
    assert any("relay_factory_registry" in m for m in _migration_switch_missing({**st, "relay_factory_registry": "0x…"},
                                                                                 day))
    assert any("relay_factory_registry repeats" in m for m in _migration_switch_missing(
        {**st, "relay_factory_registry": st["addresses"]["token"]}, day))


def test_the_legacy_read_hands_over_with_a_cut_and_is_dropped_from_the_cutover():
    """filing_managed_lock_tokens is a v3-replaced metric: from the cutover a LEGACY ve_managed row is dropped at fetch
    (validate.check_migration_cutover), the v3 row kept; the handover is declared (measuring guard) with `cut`."""
    from fetch import validate
    from fetch.base import _measuring_point
    import measuring_guard
    import trajectory
    m = "filing_managed_lock_tokens"
    assert m in config.AERODROME_MIGRATION["v3_replaces"]
    decl = config.declared_handover("Aerodrome", m)
    assert decl["cut"] is True and decl["declared"] == config.AERODROME_MIGRATION["cutover_utc"]
    assert [_measuring_point(x) for x in decl["ordered_points"]] == ["ve_managed:0xeBf418Fe.weights",
                                                                     "aero_v3:filing_locks"]
    assert trajectory.basis_of(m, "aero_v3:filing_locks") in measuring_guard._declared("Aerodrome", m)
    out = FetchOutput()
    out.frames.append(pd.DataFrame({"date": pd.to_datetime(["2026-10-21", "2026-10-28", "2026-10-28"]),
                                    "project": "Aerodrome", "metric": m, "value": [245e6, 2.85e6, 500.5e6],
                                    "source": ["ve_managed:0xeBf418Fe.weights"] * 2 + ["aero_v3:filing_locks"],
                                    "tier": 2}))
    validate.check_migration_cutover(out, today=pd.Timestamp("2026-10-28"), state=config.aerodrome_migration_state())
    kept = out.frame()
    assert list(zip(kept["date"].dt.strftime("%Y-%m-%d"), kept["value"])) == [("2026-10-21", 245e6),
                                                                              ("2026-10-28", 500.5e6)]


def test_circulating_subtracts_the_v3_filing_row_and_the_cut_drops_the_stored_legacy_rows(monkeypatch):
    """At read time: legacy rows already stored for days on/after the first v3 row are dropped by the declared cut, and
    circulating_supply_onchain = total − non-circulating − the v3 filing read (figures of block 53,174,900)."""
    import build_workbook as bw
    from test_round_p2a_d2 import _g
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Aerodrome"]])
    v3 = "aero_v3:supply_once[ratio 1]"
    g = {("Aerodrome", "total_supply"): _g("total_supply", [("2026-10-28", 2_126_320_619.4199, v3)]),
         ("Aerodrome", "noncirculating_holding_tokens"): _g("noncirculating_holding_tokens",
                                                            [("2026-10-28", 86_892_883.4453, v3)]),
         ("Aerodrome", "filing_managed_lock_tokens"): _g("filing_managed_lock_tokens", [
             ("2026-10-27", 2_848_932.0366, "ve_managed:0xeBf418Fe.weights"),
             ("2026-10-28", 2_848_932.0366, "ve_managed:0xeBf418Fe.weights"),
             ("2026-10-28", 500_541_902.6989, "aero_v3:filing_locks")])}
    bw._handover_cut_views(g)
    f = g[("Aerodrome", "filing_managed_lock_tokens")]
    assert list(f["source"]) == ["ve_managed:0xeBf418Fe.weights", "aero_v3:filing_locks"]
    bw._circulating_views(g)
    c = g[("Aerodrome", "circulating_supply_onchain")].set_index("date")["value"]
    assert c.loc[pd.Timestamp("2026-10-28")] == pytest.approx(2_126_320_619.4199 - 86_892_883.4453 - 500_541_902.6989)


def test_aerodrome_stays_parked():
    """P2b-1 builds the reader only: no primary source publishes Aerodrome's circulating on this basis, so the park,
    the MIGRATION WATCH and the named blocker stay as they are (Jake decides)."""
    assert config.AERODROME_MIGRATION_WATCH["built"] is None
    assert [k for k, _w in config.AERODROME_MIGRATION["unpark_blockers"]] == ["filing_v3"]
    assert config.aerodrome_park_reason(config.aerodrome_migration_state(), "2026-10-28").startswith("PARKED")
