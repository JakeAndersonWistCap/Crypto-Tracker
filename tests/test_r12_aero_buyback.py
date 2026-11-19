"""R12 (Jake 2026-11-19) — Aerodrome's Foundation buyback across the Aero migration (SPEC "Round 12").

No network: every reader is injected. What is tested is behaviour — the seed reads a day's buyback window whatever the
lock stock holds; a 1:1 migration is not a purchase; sAERO stakes are locks; nothing is counted twice across the
cutover; the change of measuring point is declared; the series forms over a Q0 that straddles the cutover.
The synthetic transactions follow the real ones traced on Base on 2026-11-19 (hashes in config's R12 block)."""
from __future__ import annotations

import pandas as pd
import pytest
from eth_utils import keccak

import config

PGF = "0x834c0da026d5f933c2c18fa9f8ba7f1f792fda52"
BLF = "0x623cf63a1fa7068ebbdba9f2eb262613eab557a1"
SAFE = "0x10b42e926c2b8ff0200f35ec466d50576c1efa30"
LEGACY = "0x940181a94a35a4569e4529a3cdfb74e38fd98631"
NEW = "0x3f5bf3edac59d650d8fdf9657a71f2f904a3a4ee"
SAERO = "0xbf7977e1521e9a3032bf7243ee4712f311f13915"
VE = "0xebf418fe2512e7e6bd9b87a8f0f294acdc67e6b4"
MIG = "0x22a2f7340a3fb4c7c389d91ee954766b12476721"
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
POOL = "0x00000000000000000000000000000000000000aa"
ZERO = "0x" + "0" * 40
STATE = {"addresses": {"token": NEW, "voting_escrow": SAERO, "migration": MIG},
         "relay_factory_registry": "0x0dfbe7b949eae4b826e33146f1df4c0c60c78f99"}
FB = config.PROJECT_BY_NAME["Aerodrome"]["foundation_buyback"]
WM = FB["window_metrics"]
CUT = pd.Timestamp("2026-10-22")


def _top(sig: str) -> str:
    return "0x" + keccak(text=sig).hex().removeprefix("0x")


def _pad(a) -> str:
    return "0x" + (a[2:] if isinstance(a, str) else f"{a:x}").rjust(64, "0")


def _w(n: float, dec: int = 18) -> str:
    return f"{int(round(n * 10 ** dec)):064x}"


def xfer(token, frm, to, amount, dec=18):
    return {"address": token, "topics": [_top("Transfer(address,address,uint256)"), _pad(frm), _pad(to)],
            "data": "0x" + _w(amount, dec)}


def stake(provider, token_id, kind, value):
    """sAERO Deposit(provider, tokenId, depositType, uint128 value, unstakeTime, ts) — IVotingEscrow.sol."""
    return {"address": SAERO, "topics": [_top("Deposit(address,uint256,uint8,uint128,uint256,uint256)"), _pad(provider),
                                         _pad(token_id), _pad(kind)], "data": "0x" + _w(value) + _w(0, 0) + _w(0, 0)}


def legacy_lock(provider, token_id, kind, value):
    return {"address": VE, "topics": [_top("Deposit(address,uint256,uint8,uint256,uint256,uint256)"), _pad(provider),
                                      _pad(token_id), _pad(kind)], "data": "0x" + _w(value) + _w(0, 0) + _w(0, 0)}


VPM = "0xbe8e5149dc0fe28d124de4222b7fb332de99aa79"


def burn_logs(wallet, token_id, amount, fee=0.0):
    """The burn transaction as traced (R13; tx 0xe00ba4ce…): the wallet's stake routed to the escrow's tokenId-0
    accumulator through the VoterPaymentsModule (DepositedIntoNFT, destinationId 0), then burnFees: Transfer
    escrow -> 0x0."""
    return [{"address": VPM, "topics": [_top("DepositedIntoNFT(address,(uint256,uint128)[],uint256,uint256,uint256)"),
                                        _pad(wallet), _pad(0)],
             "data": "0x" + _w(0x60, 0) + _w(amount) + _w(fee) + _w(1, 0) + _w(token_id, 0) + _w(amount)},
            xfer(NEW, SAERO, ZERO, amount)]


def ctx(lockers=("owners", "buyers")):
    own, buy = {PGF, BLF}, {SAFE}
    return {"aero": {LEGACY, NEW}, "owners": own, "buyers": buy,
            "lockers": own | (buy if "buyers" in lockers else set()), "escrows": {VE, SAERO}, "saero": SAERO,
            "migration": MIG, "vpm": VPM}


# ------------------------------------------------------------------------------------------- the declared handover
def test_the_handover_is_declared_for_every_metric_of_the_read():
    """A change of measuring point is always declared: each affected metric names its legacy point, then its v3 point,
    at the migration cutover, as a cut."""
    from fetch.base import _measuring_point
    mig = FB["migration"]
    assert mig["cutover"] == config.AERODROME_MIGRATION["cutover_utc"] == "2026-10-22"
    assert mig["window_source"] == "ve_managed:buyback_window_v3"
    assert mig["stock_source"] == "ve_managed:foundation_locks_v3"
    want = {m: ("ve_managed:buyback_window", mig["window_source"]) for m in WM.values()}
    want.update({FB["stock_metric"]: ("ve_managed:foundation_locks", mig["stock_source"]),
                 FB["claimable_metric"]: ("ve_managed:foundation_locks", mig["stock_source"])})
    assert len(want) == 7
    for metric, pts in want.items():
        d = config.declared_handover("Aerodrome", metric)
        assert tuple(d["ordered_points"]) == pts and d["cut"] is True and d["declared"] == "2026-10-22", metric
        assert d["composition_change"] and "migration" in d["why"]
        assert _measuring_point(pts[0]) != _measuring_point(pts[1])
        assert _measuring_point(config.mark_source(pts[1], "archive")) == _measuring_point(pts[1])


def test_the_day_decides_the_basis_of_a_row_never_the_moment_of_the_run():
    from fetch import ve_managed as vm
    assert not vm.on_v3(FB, "2026-10-21") and vm.on_v3(FB, "2026-10-22") and vm.on_v3(FB, pd.Timestamp("2026-11-18"))
    assert vm.window_reader_for(FB, "2026-10-21") is vm._window_reader
    assert vm.window_reader_for(FB, "2026-10-22") is vm._window_reader_v3
    assert vm.stock_reader_for(FB, "2026-09-01") is vm._foundation_reader
    assert vm.stock_reader_for(FB, "2026-11-01") is vm._foundation_reader_v3
    got = {"bought_same_tx": 0.0, "lock_from_balance": 1.0, "purchased": 2.0, "compounded_owner": 3.0,
           "rebase_owner": 4.0, "locks": [1], "purchases": [1], "migrated_liquid": 5.0, "burned": 6.0}
    old, _ = vm.window_rows("Aerodrome", FB, pd.Timestamp("2026-10-21"), got)
    new, msg = vm.window_rows("Aerodrome", FB, pd.Timestamp("2026-10-22"), got)
    assert {f["source"].iloc[0] for f in old} == {"ve_managed:buyback_window"}
    assert {f["source"].iloc[0] for f in new} == {"ve_managed:buyback_window_v3"}
    assert "migrated 1:1, not bought 5.00" in msg and "burned out of the escrow 6.00" in msg
    stock = {"locked": 10.0, "claimable": 0.0, "nfts": 0, "owned_managed": 0, "by_owner": {}}
    assert vm.foundation_rows("Aerodrome", FB, pd.Timestamp("2026-10-21"), stock)[0][0]["source"].iloc[0] == \
        "ve_managed:foundation_locks"
    assert vm.foundation_rows("Aerodrome", FB, pd.Timestamp("2026-10-22"), stock)[0][0]["source"].iloc[0] == \
        "ve_managed:foundation_locks_v3"


# ----------------------------------------------------------------------------------- what a transaction is, v3 rules
def test_a_one_to_one_migration_is_not_a_purchase_and_not_a_payment():
    """The PGF's 1,728,692.45 legacy AERO -> new AERO (tx 0xd8d435af…): legacy out to the migration, new in from it. A
    reader keyed on the new token alone would see AERO received where 'a different token' was paid."""
    import check_offline_items as coi
    amt = 1_728_692.4483
    logs = [xfer(LEGACY, PGF, MIG, amt), xfer(LEGACY, MIG, VE, amt), xfer(NEW, MIG, PGF, amt),
            {"address": MIG, "topics": [_top("LiquidMigrated(address,address,uint256,uint256)"), _pad(PGF), _pad(PGF)],
             "data": "0x" + _w(amt) + _w(amt)}]
    got = coi.v3_classify_tx(logs, ctx(), tx_to=PGF)
    assert got["purchased"] == 0 and got["purchases"] == [] and got["inflows"] == {}
    assert got["bought_same_tx"] == 0 and got["lock_from_balance"] == 0 and got["locks"] == []
    assert got["migrated_liquid"] == pytest.approx(amt)
    # even with a real payment in the same transaction, AERO from the migration contract is not bought
    assert coi.v3_classify_tx(logs + [xfer(USDC, PGF, POOL, 100.0, 6)], ctx())["purchased"] == 0


def test_aero_of_either_token_paid_for_in_another_token_is_bought():
    """The Safe's purchases: NEW AERO for USDC (2026-10-28) and LEGACY AERO for USDC (2026-11-04, after the migration).
    Its forward to the PGF is internal; AERO arriving with nothing paid is an inflow, not a purchase."""
    import check_offline_items as coi
    new_buy = coi.v3_classify_tx([xfer(USDC, SAFE, POOL, 122_988.0, 6), xfer(NEW, POOL, SAFE, 174_600.6362)], ctx())
    assert new_buy["purchased"] == pytest.approx(174_600.6362)
    assert new_buy["purchases"][0]["paid_out"] == {USDC: 122_988_000_000}
    old_buy = coi.v3_classify_tx([xfer(USDC, SAFE, POOL, 39_760.0, 6), xfer(LEGACY, POOL, SAFE, 61_780.32)], ctx())
    assert old_buy["purchased"] == pytest.approx(61_780.32)
    fwd = coi.v3_classify_tx([xfer(LEGACY, SAFE, PGF, 243_612.2344)], ctx())
    assert fwd["purchased"] == 0 and fwd["inflows"] == {}
    gift = coi.v3_classify_tx([xfer(NEW, POOL, PGF, 1_164_053.44)], ctx())
    assert gift["purchased"] == 0 and gift["inflows"] == {POOL: pytest.approx(1_164_053.44)}
    # paying AERO for AERO (legacy out, new in, outside the migration contract) is not paying a different token
    swap = coi.v3_classify_tx([xfer(LEGACY, PGF, POOL, 10.0), xfer(NEW, POOL, PGF, 10.0)], ctx())
    assert swap["purchased"] == 0
    # AERO returned by an escrow (a withdrawal of the wallet's own stake) is not bought
    back = coi.v3_classify_tx([xfer(USDC, PGF, POOL, 1.0, 6), xfer(NEW, SAERO, PGF, 500.0)], ctx())
    assert back["purchased"] == 0


def test_an_saero_stake_by_a_locker_is_a_lock_and_a_migrated_lock_is_not():
    import check_offline_items as coi
    # createStake from the wallet's balance (the Safe, 2026-10-28, tx 0xaaa24d4f…)
    st_ = coi.v3_classify_tx([xfer(NEW, SAFE, SAERO, 174_600.6362), stake(SAFE, 19526, 0, 174_600.6362)], ctx())
    assert st_["lock_from_balance"] == pytest.approx(174_600.6362) and st_["bought_same_tx"] == 0
    assert st_["locks"][0]["nft"] == 19526 and st_["locks"][0]["escrow"] == "sAERO"
    # the same stake does not count while only the owners lock (the decision of SPEC R12.5)
    assert coi.v3_classify_tx([stake(SAFE, 19526, 0, 174_600.6362)], ctx(lockers=("owners",)))["locks"] == []
    # increaseStakeAmount (1) and reviveStake (3) add AERO; increaseStakingPeriod (2) adds none
    for kind, n in ((1, 1), (3, 1), (2, 0)):
        assert len(coi.v3_classify_tx([stake(PGF, 7, kind, 100.0)], ctx())["locks"]) == n, kind
    # a legacy veNFT migrated to sAERO: the Deposit's provider is the MIGRATION contract (2026-11-10, #23940)
    mig = [xfer(NEW, MIG, SAERO, 423_184.5013), stake(MIG, 23940, 0, 423_184.5013),
           {"address": MIG, "topics": [_top("VeNFTMigrated(address,address,uint256,uint256,uint256,bool,bool)"),
                                       _pad(PGF), _pad(PGF), _pad(20598)],
            "data": "0x" + _w(423_184.5013) + _w(423_184.5013) + _w(0, 0) + _w(0, 0)}]
    got = coi.v3_classify_tx(mig, ctx(), tx_to=PGF)
    assert got["locks"] == [] and got["lock_from_balance"] == 0 and got["purchased"] == 0
    assert got["migrated_locks"] == pytest.approx(423_184.5013)
    # bought and staked in ONE transaction: bought_same_tx, and the purchase is not counted a second time
    one = coi.v3_classify_tx([xfer(USDC, PGF, POOL, 70.0, 6), xfer(NEW, POOL, PGF, 100.0), xfer(NEW, PGF, SAERO, 100.0),
                              stake(PGF, 9, 0, 100.0)], ctx())
    assert one["bought_same_tx"] == pytest.approx(100.0) and one["lock_from_balance"] == 0 and one["purchased"] == 0
    # the accumulator's burn in a transaction the wallet executed is reported, and is neither bought nor locked.
    # R13 (Jake 2026-11-19): "burned" is the wallet's OWN stake — what it routed to the accumulator in the same
    # transaction; a burn with no such routing is destroyed AERO not shown to be a purchase: "burned_other".
    # Was: burn["burned"] == 174_600.6362 for the bare Transfer escrow -> 0x0
    burn = coi.v3_classify_tx([xfer(NEW, SAERO, ZERO, 174_600.6362)], ctx(), tx_to=SAFE)
    assert burn["burned"] == 0 and burn["burned_other"] == pytest.approx(174_600.6362)
    assert burn["locks"] == [] and burn["purchased"] == 0
    burn = coi.v3_classify_tx(burn_logs(SAFE, 19526, 174_600.6362), ctx(), tx_to=SAFE)
    assert burn["burned"] == pytest.approx(174_600.6362) and burn["burned_other"] == 0
    assert burn["locks"] == [] and burn["purchased"] == 0
    assert coi.v3_classify_tx([xfer(NEW, SAERO, ZERO, 5.0)], ctx(), tx_to=POOL)["burned"] == 0


def _window(monkeypatch, receipts: dict, legacy: dict | None = None, relay: dict | None = None, lockers=None):
    """aerodrome_buyback_window_v3 over injected receipts {block: [receipt]} — no chain."""
    import check_offline_items as coi
    monkeypatch.setattr(coi, "_is_safe", lambda a: True)
    monkeypatch.setattr(coi, "_sig_move_blocks", lambda sig, b0, b1, span, **k: sorted(receipts))
    monkeypatch.setattr(coi, "_block_receipts", lambda chain, b: receipts[b])
    if lockers is not None:
        monkeypatch.setitem(FB["migration"], "lockers", lockers)
    base = {"bought_same_tx": 0.0, "lock_from_balance": 0.0, "purchased": 0.0, "compounded_owner": 0.0,
            "rebase_owner": 0.0, "locks": [], "purchases": [], "inflows": {}, "compounders": {}, "error": None}
    base.update(legacy or {})
    seen = {}

    def legacy_reader(b0, b1, owners=None, say=print, parts=None, also_aero=()):
        seen.update({"parts": parts, "also_aero": also_aero})
        return {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    got = coi.aerodrome_buyback_window_v3(
        100, 200, state=STATE, legacy_reader=legacy_reader,
        relay_reader=lambda b0, b1, owners, state=None: relay or {"compounded_owner": 0.0, "compounders": {}, "relays": 4},
        # R13: the wallets' liquid AERO at the window's end is part of the read (no chain here)
        liquid_reader=lambda block, owners=None, state=None: {"liquid": 1_000.0, "buyer_liquid": 0.0, "by": {}})
    return got, seen


def test_the_v3_window_reads_the_safes_week_as_it_happened(monkeypatch):
    """2026-10-28 as traced: the Safe buys 174,600.64 new AERO for 122,988 USDC, stakes it, and the stake is burned."""
    rc = {110: [{"transactionHash": "0xbuy", "to": POOL,
                 "logs": [xfer(USDC, SAFE, POOL, 122_988.0, 6), xfer(NEW, POOL, SAFE, 174_600.6362)]}],
          120: [{"transactionHash": "0xstake", "to": SAFE,
                 "logs": [xfer(NEW, SAFE, SAERO, 174_600.6362), stake(SAFE, 19526, 0, 174_600.6362)]}],
          # R13: the burn as traced — the stake routed to the accumulator and destroyed in one transaction
          # (was: the bare Transfer escrow -> 0x0, which is now "burned_other": not shown to be the wallet's stake)
          130: [{"transactionHash": "0xburn", "to": SAFE, "logs": burn_logs(SAFE, 19526, 174_600.6362)}]}
    got, seen = _window(monkeypatch, rc)
    assert got["error"] is None and got["basis"] == "v3"
    assert got["purchased"] == pytest.approx(174_600.6362) and got["lock_from_balance"] == pytest.approx(174_600.6362)
    assert got["bought_same_tx"] == 0 and got["burned"] == pytest.approx(174_600.6362)
    assert [x["tx"] for x in got["purchases"]] == ["0xbuy"] and [x["tx"] for x in got["locks"]] == ["0xstake"]
    # the legacy part reads the legacy escrow's locks and compounding only — its purchases read is off, and the new
    # token is not "a different token" for it either
    assert seen == {"parts": ("locks", "compounded"), "also_aero": (NEW,)}
    # with the owners alone as lockers the Safe's stake is not a lock; the purchase is still a purchase
    got2, _ = _window(monkeypatch, rc, lockers="owners")
    assert got2["lock_from_balance"] == 0 and got2["purchased"] == pytest.approx(174_600.6362)


def test_a_legacy_lock_found_by_both_parts_is_counted_once_and_relay_compounding_is_added(monkeypatch):
    lock = {"block": 150, "tx": "0xlegacylock", "nft": 1, "owner": PGF, "amount": 50.0, "aero_in_same_tx": 0.0,
            "paid_out": {}, "bought_same_tx": 0.0}
    rc = {150: [{"transactionHash": "0xlegacylock", "to": PGF,
                 "logs": [xfer(LEGACY, PGF, VE, 50.0), legacy_lock(PGF, 1, 1, 50.0)]}],
          160: [{"transactionHash": "0xstake", "to": PGF, "logs": [stake(PGF, 2, 0, 30.0)]}]}
    got, _ = _window(monkeypatch, rc, legacy={"lock_from_balance": 50.0, "locks": [lock], "compounded_owner": 7.0,
                                              "rebase_owner": 3.0, "compounders": {"0xauto": 7.0}},
                     relay={"compounded_owner": 11.0, "compounders": {"0xrelay": 11.0}, "relays": 4})
    assert got["lock_from_balance"] == pytest.approx(80.0), "the legacy lock once + the sAERO stake"
    assert len(got["locks"]) == 2
    assert got["compounded_owner"] == pytest.approx(18.0) and got["compounders"] == {"0xauto": 7.0, "0xrelay": 11.0}
    assert got["rebase_owner"] == 3.0


def test_a_failed_part_is_an_error_never_a_zero(monkeypatch):
    import check_offline_items as coi
    got, _ = _window(monkeypatch, {}, legacy={"error": "ValueError: VotingEscrow state unread at block 0x1"})
    assert "legacy escrow part" in got["error"]
    monkeypatch.setattr(coi, "_is_safe", lambda a: True)
    monkeypatch.setattr(coi, "_sig_move_blocks", lambda *a, **k: (_ for _ in ()).throw(ValueError("state unread")))
    got = coi.aerodrome_buyback_window_v3(1, 2, state=STATE, legacy_reader=lambda *a, **k: {
        "bought_same_tx": 0.0, "lock_from_balance": 0.0, "purchased": 0.0, "compounded_owner": 0.0, "rebase_owner": 0.0,
        "locks": [], "purchases": [], "inflows": {}, "compounders": {}, "error": None})
    assert got["error"].startswith("ValueError: state unread")
    assert "names no new token" in coi.aerodrome_buyback_window_v3(1, 2, state={"addresses": {}})["error"]


def test_state_bisection_finds_every_block_that_moved():
    import check_offline_items as coi
    moves = {1_007: 1, 1_500: 2, 1_501: 3, 3_999: 4}
    sig = lambda b: (max([v for k, v in moves.items() if k <= b] or [0]),)  # noqa: E731
    assert coi._sig_move_blocks(sig, 1_000, 4_000, 1_000) == sorted(moves)
    with pytest.raises(ValueError, match="stopped after 5 archive reads"):
        coi._sig_move_blocks(sig, 1_000, 4_000, 1_000, max_calls=5)


# ------------------------------------------------------------------------------------- compounding and the stock, v3
class _Rdr:
    """A stand-in for fetch.aero_v3.AeroV3: locked_read by block, multi / multi_try by call name."""

    def __init__(self, by_block, backing=None, shares=None):
        self.by_block, self.backing, self.shares, self.asked = by_block, backing or {}, shares or {}, []

    def rpc(self, method, params):
        return hex(999)

    def locked_read(self, labels, legacy_ve, blk, st, allow_no_factory=False):
        self.asked.append((blk, allow_no_factory))
        g = self.by_block[blk]
        if isinstance(g, Exception):
            raise g
        return g

    def multi_try(self, calls, blk):
        return [hex(self.backing[blk][0 if name == "totalBacking" else 1]) for _to, name, _a in calls]

    def multi(self, calls, blk):
        return [hex(self.shares[blk][0] if name == "totalSupply" else self.shares[blk][1] if a[0] == PGF else 0)
                for _to, name, a in calls]


def _relay(backing, supply, mine):
    return {"0xrelay": {"pt": "0xpt", "backing": backing, "supply": supply, "shares": {PGF: mine, BLF: 0}}}


class _Relay(_Rdr):
    """One Relay whose (owners' principal, totalBacking, principal supply) is a function of the block."""

    def __init__(self, state_at):
        self.state_at, self.reads = state_at, 0

    def locked_read(self, labels, legacy_ve, blk, st, allow_no_factory=False):
        assert allow_no_factory, "an empty factory list is 'no Relay yet', not an error"
        mine, backing, supply = self.state_at(blk)
        return {"relay_state": {"0xrelay": {"pt": "0xpt", "backing": backing, "supply": supply,
                                            "shares": {PGF: mine, BLF: 0}}}}

    def multi_try(self, calls, blk):
        self.reads += 1
        mine, backing, supply = self.state_at(blk)
        return [hex(backing if name == "totalBacking" else supply if name == "totalSupply"
                    else mine if a[0] == PGF else 0) for _to, name, a in calls]


def test_relay_compounding_is_the_owners_share_of_what_was_staked_back_and_zero_when_nothing_was():
    import check_offline_items as coi
    e18 = 10 ** 18
    run = lambda f: coi.aerodrome_relay_compounding(100, 200, [PGF, BLF], rdr=_Relay(f), state=STATE)  # noqa: E731
    # nothing moved: no read beyond the two ends
    still = _Relay(lambda b: (250 * e18, 1000 * e18, 1000 * e18))
    assert coi.aerodrome_relay_compounding(100, 200, [PGF, BLF], rdr=still, state=STATE) == {
        "compounded_owner": 0.0, "compounders": {}, "relays": 1}
    assert still.reads == 0
    # the Relay compounds 40 AERO in block 150: backing 1000 -> 1040, supply unchanged; the PGF holds 250 of 1000
    got = run(lambda b: (250 * e18, (1000 if b < 150 else 1040) * e18, 1000 * e18))
    assert got["compounded_owner"] == pytest.approx(10.0) and got["compounders"] == {"0xrelay": pytest.approx(10.0)}
    # others deposit 100 at the current price (backing and supply move together): nothing compounded, however often
    assert run(lambda b: (250 * e18, (1000 + (b - 100)) * e18, (1000 + (b - 100)) * e18))["compounded_owner"] == 0
    # a deposit (block 130) AND a compound of 55 on the 1,100 then outstanding (block 170): the PGF's 250 earn 12.5
    got = run(lambda b: (250 * e18, (1000 if b < 130 else 1100 if b < 170 else 1155) * e18,
                         (1000 if b < 130 else 1100) * e18))
    assert got["compounded_owner"] == pytest.approx(12.5)
    # the owners' OWN deposit is processed in block 160 (principal 250 -> 500 at price 1.04, after the compound of
    # block 150): their gain is 250 x 0.04, and the principal they added is not compounding
    def own(b):
        if b < 150:
            return 250 * e18, 1000 * e18, 1000 * e18
        if b < 160:
            return 250 * e18, 1040 * e18, 1000 * e18
        return 500 * e18, 1300 * e18, 1250 * e18
    assert run(own)["compounded_owner"] == pytest.approx(10.0)
    # a Relay the owners hold nothing in is not counted, whatever it does
    assert run(lambda b: (0, (1 if b < 150 else 9) * e18, e18))["relays"] == 0
    # a price that FALLS has no cause in the source: an error, never a negative buyback
    with pytest.raises(ValueError, match="price FELL"):
        run(lambda b: (250 * e18, (1000 if b < 150 else 900) * e18, 1000 * e18))


def test_the_v3_lock_stock_is_legacy_locks_plus_stakes_plus_relay_principal_at_one_block():
    """2026-11-12 as read on chain: no legacy veNFT left, 957,657.48 staked directly, 193,780,936.11 in Relays."""
    import check_offline_items as coi
    e18 = 10 ** 18
    owners = list(FB["owners"])
    g = {"by": {owners[0].lower(): [5 * e18, int(957_657.4762 * e18), int(193_780_936.1142 * e18), 3 * e18],
                owners[1].lower(): [0, 0, 0, 0]}, "new_ids": list(range(28)), "relays": list(range(18))}
    rdr = _Rdr({4096: g})
    legacy = lambda o, block="latest": {"locked": 100.0, "claimable": 1.5, "nfts": 1, "owned_managed": 0,  # noqa: E731
                                        "by_owner": {owners[0]: 100.0, owners[1]: 0.0}}
    got = coi.aerodrome_foundation_locks_v3(owners, block=hex(4096), state=STATE, rdr=rdr, legacy_reader=legacy)
    # locked_read's own legacy leg (managed weights, 5 here) is NOT added: the legacy read counts those and normal locks
    assert got["locked"] == pytest.approx(100.0 + 957_657.4762 + 193_780_936.1142 + 3.0)
    assert (got["legacy"], got["claimable"], got["block"]) == (100.0, 1.5, 4096)
    assert got["by_owner"][owners[0]] == pytest.approx(got["locked"]) and got["by_owner"][owners[1]] == 0
    assert rdr.asked == [(4096, True)]
    # unknown is never 0: a leg that cannot be read is an error, on either side
    bad = coi.aerodrome_foundation_locks_v3(owners, block=hex(4096), state=STATE, legacy_reader=legacy,
                                            rdr=_Rdr({4096: RuntimeError("Multicall3 sub-call failed")}))
    assert "locked" not in bad and "Multicall3 sub-call failed" in bad["error"]
    bad = coi.aerodrome_foundation_locks_v3(owners, block=hex(4096), state=STATE, rdr=rdr,
                                            legacy_reader=lambda o, block="latest": {"error": "balanceOf unreadable"})
    assert bad == {"error": "legacy escrow: balanceOf unreadable"}


def test_locked_read_takes_an_empty_factory_list_as_no_relay_only_when_asked():
    """relayFactories() answered [] at the cutover block (read 2026-11-19): the filing read keeps that an error, the
    Foundation's reads take it as 'no Relay exists yet' — direct stakes only."""
    from eth_abi import encode
    from fetch.aero_v3 import AeroV3, selector
    empty = "0x" + encode(["address[]"], [[]]).hex()

    class R(AeroV3):
        def multi(self, calls, block, chunk=150):
            out = []
            for to, name, args in calls:
                out.append(empty if name == "relayFactories" else "0x" + _w(0, 0))
            return out
    r = R(state=STATE, rpc=lambda m, p: None)
    assert selector("relayFactories")
    with pytest.raises(RuntimeError, match="lists no factory"):
        r.locked_read({PGF: "PGF"}, VE, 1, STATE)
    got = r.locked_read({PGF: "PGF"}, VE, 1, STATE, allow_no_factory=True)
    assert got["by"] == {PGF: [0, 0, 0, 0]} and got["relays"] == [] and got["relay_state"] == {}


# ---------------------------------------------------------------------------------------------------------- the seed
def _seed(monkeypatch, store: dict, today="2026-11-19", days=5, seed_from=None, window_error=None):
    """Run seed_foundation over an in-memory store {(metric, 'YYYY-MM-DD'): source}; rows written go back into it."""
    import check_offline_items as coi
    from fetch import ve_managed as vm
    monkeypatch.setattr(vm, "today", lambda: pd.Timestamp(today))
    monkeypatch.setattr(coi, "rpc_block_at", lambda chain, ts, say=print: 1_000 + ts // 86400)
    monkeypatch.setitem(FB, "seed_from", seed_from)
    calls = {"stock": [], "window": [], "lines": [], "writes": [], "burn": [], "liquid": []}
    stock = {"locked": 5.0, "claimable": 0.0, "nfts": 1, "owned_managed": 0, "by_owner": {}}
    win = {"bought_same_tx": 0.0, "lock_from_balance": 0.0, "purchased": 7.0, "compounded_owner": 0.0,
           "rebase_owner": 0.0, "locks": [], "purchases": [1], "inflows": {}, "compounders": {}, "error": None}

    def window_reader(b0, b1):
        calls["window"].append((b0, b1))
        return {"error": window_error} if window_error else win

    def write(frames):
        calls["writes"].append(len(frames))
        for f in frames:
            store[(f["metric"].iloc[0], str(pd.Timestamp(f["date"].iloc[0]).date()))] = f["source"].iloc[0]
    p = {"name": "Aerodrome", "foundation_buyback": FB}
    frames = vm.seed_foundation([p], days=days, stored_src=lambda n, m, d: store.get((m, d)), write=write,
                                say=calls["lines"].append, window_reader=window_reader,
                                # R13: the burn of a stored v3 window and the liquid balance at the day's end
                                burn_reader=lambda b0, b1: (calls["burn"].append((b0, b1)), {
                                    "burned": 0.0, "burned_other": 0.0, "sent_out": 0.0, "liquid": 9.0})[1],
                                liquid_reader=lambda b: (calls["liquid"].append(b), {"liquid": 9.0})[1],
                                reader=lambda o, block="latest": (calls["stock"].append(block), stock)[1])
    return frames, calls, vm


def test_the_seed_reads_the_window_of_a_day_whose_stock_is_already_stored(monkeypatch):
    """BUG A (Jake's run 2026-11-19): his store held the lock stock for every day, so no window was ever read for the
    days before the daily read began. The window is its own read."""
    days = [str(d.date()) for d in pd.date_range("2026-09-01", "2026-09-05")]
    store = {(FB["stock_metric"], d): "ve_managed:foundation_locks:archive" for d in days}
    frames, calls, vm = _seed(monkeypatch, store, today="2026-09-06")
    assert calls["stock"] == [], "every stock day is stored: none is read again"
    assert calls["window"] == [(1_000 + pd.Timestamp(d).value // 86_400_000_000_000,
                                1_001 + pd.Timestamp(d).value // 86_400_000_000_000) for d in days]
    got = pd.concat(frames)
    # R13: from 2026-08-25 a day also holds the wallets' liquid AERO at its end (one more row a day, its own point).
    # Was: set(got["metric"]) == set(WM.values()) and len(got) == 5 * len(WM), one source
    LIQ = FB["extra_metrics"]["liquid"]
    assert set(got["metric"]) == set(WM.values()) | {LIQ} and len(got) == 5 * (len(WM) + 1)
    assert set(got[got["metric"] != LIQ]["source"]) == {"ve_managed:buyback_window"}   # before the cutover: legacy point
    assert set(got[got["metric"] == LIQ]["source"]) == {"ve_managed:foundation_liquid"} and len(calls["liquid"]) == 5
    assert sorted({str(pd.Timestamp(d).date()) for d in got["date"]}) == days
    # one line a day says what was done
    assert len(calls["lines"]) == 5
    assert all("buyback window READ, blocks" in ln and "lock stock already stored" in ln for ln in calls["lines"])
    assert calls["lines"][0].lstrip().startswith("2026-09-01:")
    assert vm.LAST_SEED["window_read"] == 5 and vm.LAST_SEED["already_stored"] == 0 and vm.LAST_SEED["stock_read"] == 0


def test_the_seed_is_resumable_and_never_rewrites_a_stored_window_day(monkeypatch):
    days = [str(d.date()) for d in pd.date_range("2026-09-01", "2026-09-05")]
    store = {(FB["stock_metric"], d): "ve_managed:foundation_locks" for d in days}
    store.update({(m, "2026-09-03"): "ve_managed:buyback_window" for m in WM.values()})     # one window day is held
    frames, calls, vm = _seed(monkeypatch, store, today="2026-09-06")
    # R13: each day also lands its liquid-balance row — the day whose window is held gets that row alone.
    # Was: calls["writes"] == [len(WM)] * 4; 2026-09-03 in no frame; "…already stored; lock stock already stored"
    assert len(calls["window"]) == 4 and calls["writes"] == [len(WM) + 1] * 2 + [1] + [len(WM) + 1] * 2, \
        "stored day by day, as each lands"
    got = pd.concat(frames)
    assert set(got[pd.to_datetime(got["date"]) == pd.Timestamp("2026-09-03")]["metric"]) == {FB["extra_metrics"]["liquid"]}
    assert "2026-09-03: buyback window already stored; liquid AERO at the day's end READ — 9.00; lock stock already " \
           "stored" in calls["lines"][2]
    # the next run finds everything it wrote and reads nothing
    frames2, calls2, vm = _seed(monkeypatch, store, today="2026-09-06")
    assert frames2 == [] and calls2["window"] == [] and calls2["stock"] == [] and calls2["writes"] == []
    assert vm.LAST_SEED["already_stored"] == vm.LAST_SEED["days"] == 5
    assert vm.why_nothing_stored(vm.LAST_SEED, 0).startswith("all 5 days already stored")
    # a window that cannot be read stores nothing for the day, says why, and is asked again on the next run
    store3 = {(FB["stock_metric"], d): "ve_managed:foundation_locks" for d in days}
    frames3, calls3, vm = _seed(monkeypatch, store3, today="2026-09-06", window_error="HTTP 429 rate limited")
    assert frames3 == [] and vm.LAST_SEED["window_error"] == 5
    assert all("buyback window NOT read — HTTP 429 rate limited" in ln for ln in calls3["lines"])
    assert not any(k[0] in WM.values() for k in store3)


def test_after_the_cutover_the_seed_reads_on_the_v3_basis_and_replaces_only_legacy_rows_dated_after_it(monkeypatch):
    days = [str(d.date()) for d in pd.date_range("2026-10-20", "2026-10-24")]
    store = {(m, d): "ve_managed:buyback_window" for m in WM.values() for d in days}          # the legacy reader's rows
    store.update({(m, d): "ve_managed:foundation_locks" for m in (FB["stock_metric"], FB["claimable_metric"])
                  for d in days})
    frames, calls, vm = _seed(monkeypatch, store, today="2026-10-25")
    got = pd.concat(frames)
    # R13: every day from 2026-08-25 also gets the wallets' liquid AERO at its end — a NEW row beside the stored
    # ones (10-20 and 10-21 included); the five window parts and the stock of those two days are still never rewritten.
    # Was: `read` taken over every frame
    liq = got[got["metric"] == FB["extra_metrics"]["liquid"]]
    assert sorted({str(pd.Timestamp(d).date()) for d in liq["date"]}) == days
    got = got[got["metric"] != FB["extra_metrics"]["liquid"]]
    read = sorted({str(pd.Timestamp(d).date()) for d in got["date"]})
    assert read == ["2026-10-22", "2026-10-23", "2026-10-24"], "10-20 and 10-21 are before the cutover: never rewritten"
    assert set(got[got["metric"].isin(WM.values())]["source"]) == {"ve_managed:buyback_window_v3"}
    assert set(got[got["metric"] == FB["stock_metric"]]["source"]) == {"ve_managed:foundation_locks_v3"}
    assert "replaces the legacy-basis row of a day after the migration" in calls["lines"][2]
    assert "on the v3 basis" in calls["lines"][2] and "already stored" in calls["lines"][0]
    # once on the v3 basis a day is held like any other
    # (R13: the fake window of the first run returned no burn, so the second run asks the burn of the three v3 days —
    # the receipts read alone — and nothing else; a third run reads nothing. Was: frames2 == [])
    frames2, calls2, _ = _seed(monkeypatch, store, today="2026-10-25")
    assert calls2["window"] == [] and calls2["stock"] == [] and len(calls2["burn"]) == 3
    assert {f["metric"].iloc[0] for f in frames2} == {FB["extra_metrics"][k] for k in ("burned", "burned_other", "sent_out")}
    frames3, calls3, _ = _seed(monkeypatch, store, today="2026-10-25")
    assert frames3 == [] and calls3["window"] == [] and calls3["burn"] == [] and calls3["liquid"] == []


def test_the_seed_reaches_back_to_the_pool_of_the_first_lock_in_q0(monkeypatch):
    from fetch import ve_managed as vm
    assert FB["seed_from"] == FB["trace"]["rows"][0]["date"] == "2026-07-15"
    span = vm.seed_span({"seed_from": "2026-07-15"}, None, pd.Timestamp("2026-11-19 09:44", tz="UTC"))
    assert (str(span[0].date()), str(span[-1].date()), len(span)) == ("2026-07-15", "2026-11-18", 127)
    assert len(vm.seed_span({}, None, pd.Timestamp("2026-11-19"))) == vm.SEED_DAYS == 100
    assert len(vm.seed_span({"seed_from": "2026-11-01"}, None, pd.Timestamp("2026-11-19"))) == 100
    # an explicit span is exactly that span
    assert len(vm.seed_span({"seed_from": "2026-07-15"}, 5, pd.Timestamp("2026-11-19"))) == 5
    assert vm.seed_span({"seed_from": "2026-07-15"}, 0, pd.Timestamp("2026-11-19")) == []


# ------------------------------------------------------------------------------------------------- the series forms
def _groups(rows: dict) -> dict:
    """{(metric, 'YYYY-MM-DD', source): value} -> the build's groups for Aerodrome."""
    out: dict = {}
    for (metric, day, src), v in rows.items():
        out.setdefault(("Aerodrome", metric), []).append(
            {"date": pd.Timestamp(day), "project": "Aerodrome", "metric": metric, "value": float(v), "source": src,
             "tier": 2})
    return {k: pd.DataFrame(v).sort_values("date").reset_index(drop=True) for k, v in out.items()}


def _window_rows(day, src, **parts):
    vals = {"bought_same_tx": 0.0, "lock_from_balance": 0.0, "purchased": 0.0, "compounded_owner": 0.0,
            "rebase_owner": 0.0, **parts}
    return {(WM[k], day, src): v for k, v in vals.items()}


def test_the_series_forms_over_a_q0_that_straddles_the_cutover_and_nothing_is_counted_twice(monkeypatch):
    """Q0 for as-of 2026-11-19 is 2026-08-21..11-18 (90 days): legacy rows to 10-21, v3 rows from 10-22. The pool of
    purchases runs across the cutover (both tokens are AERO); a migration day adds nothing."""
    import build_workbook as bw
    old, new = "ve_managed:buyback_window:archive", "ve_managed:buyback_window_v3:archive"
    rows = {}
    for d in pd.date_range("2026-08-21", "2026-11-18"):
        rows.update(_window_rows(str(d.date()), new if d >= CUT else old))
    rows.update(_window_rows("2026-09-02", old, purchased=1_000.0))                      # bought before the cutover
    rows.update(_window_rows("2026-10-22", new))                                         # the migration: nothing
    rows.update(_window_rows("2026-10-25", new, lock_from_balance=5_000.0))              # staked after it
    rows.update(_window_rows("2026-10-28", new, purchased=174_600.0, lock_from_balance=174_600.0))
    rows.update(_window_rows("2026-11-04", new, purchased=243_612.0))                    # bought, held, not locked
    rows.update(_window_rows("2026-11-06", new, compounded_owner=12.5, rebase_owner=99.0))
    groups = _groups(rows)
    bw._foundation_buyback_views(groups)
    s = groups[("Aerodrome", "actual_buyback_tokens")].set_index("date")["value"]
    assert len(s) == 90 and s.index.min() == pd.Timestamp("2026-08-21") and s.index.max() == pd.Timestamp("2026-11-18")
    assert s[pd.Timestamp("2026-10-22")] == 0
    # R13 B1 (Jake 2026-11-19): from 2026-08-25 a purchase counts on its PURCHASE day and a later lock or stake of it
    # is not counted again. Was (bought and locked, every day): 09-02 0, 10-25 1,000 (the stake matched to the 09-02
    # purchase), 10-28 174,600 (as a lock), 11-04 0 ("in the pool, not yet in the series"), sum 175,612.5, one source
    assert s[pd.Timestamp("2026-09-02")] == pytest.approx(1_000.0), "counted the day it was bought"
    assert s[pd.Timestamp("2026-10-25")] == 0, "the stake of AERO already counted on its purchase day: not again"
    assert s[pd.Timestamp("2026-10-28")] == pytest.approx(174_600.0), "once: the purchase, not the purchase and its stake"
    assert s[pd.Timestamp("2026-11-04")] == pytest.approx(243_612.0), "bought and held, not locked: counted while held"
    assert s[pd.Timestamp("2026-11-06")] == pytest.approx(12.5), "the rebase is never in it"
    assert s.sum() == pytest.approx(1_000.0 + 174_600.0 + 243_612.0 + 12.5)
    src = groups[("Aerodrome", "actual_buyback_tokens")].set_index("date")["source"]
    assert set(src[src.index < pd.Timestamp("2026-08-25")]) == {"derived:foundation_buyback[bought+matched+compounded]"}
    assert set(src[src.index >= pd.Timestamp("2026-08-25")]) == {
        "derived:foundation_purchases[purchased on the day+compounded]"}


def test_a_legacy_row_dated_after_the_cutover_holds_no_value_until_the_v3_read_stores_the_day(monkeypatch):
    """Jake's store on 2026-11-19: the legacy reader's rows for 10-22..11-17 (it saw the legacy token only) and a lock
    stock of 816,485 then 0. They are not on the declared basis: dropped, the day unknown — never the blind figure."""
    import build_workbook as bw
    monkeypatch.setitem(FB, "trace", None)
    old = "ve_managed:buyback_window"
    rows = {}
    for d in pd.date_range("2026-10-18", "2026-10-26"):
        rows.update(_window_rows(str(d.date()), old))
    for k in [k for k in rows if k[1] == "2026-10-24"]:
        del rows[k]
    rows.update(_window_rows("2026-10-24", "ve_managed:buyback_window_v3", compounded_owner=3.0))   # one v3 day stored
    for d, v in (("2026-10-20", 174_772_259.98), ("2026-10-23", 816_485.12), ("2026-11-11", 0.0)):
        rows[(FB["stock_metric"], d, "ve_managed:foundation_locks")] = v
    rows[(FB["stock_metric"], "2026-11-12", "ve_managed:foundation_locks_v3")] = 194_738_593.59
    groups = _groups(rows)
    bw._foundation_buyback_views(groups)
    s = groups[("Aerodrome", "actual_buyback_tokens")].set_index("date")["value"]
    assert [str(d.date()) for d in s.index] == ["2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-24"]
    assert s[pd.Timestamp("2026-10-24")] == 3.0
    stock = groups[("Aerodrome", FB["stock_metric"])].set_index("date")["value"]
    assert list(stock.round(2)) == [174_772_259.98, 194_738_593.59], "816,485 and 0 were the legacy escrow alone"
    note = bw.FOUNDATION_MIGRATION_CUT[("Aerodrome", FB["stock_metric"])]
    assert note.startswith("2 row(s) of ve_managed:foundation_locks dated on/after the migration cutover (2026-10-22)")
    # a project without the migration block is untouched
    monkeypatch.setitem(FB, "migration", None)
    groups = _groups(rows)
    bw._foundation_buyback_views(groups)
    assert len(groups[("Aerodrome", FB["stock_metric"])]) == 4


def test_the_seed_command_goes_through_the_store_and_says_what_it_did(monkeypatch, tmp_path, caplog):
    """`python token_metrics.py --seed aero_buyback` on a store shaped like Jake's: the stock held for every day, one
    window day held before the cutover, the legacy reader's rows on the days after it."""
    import logging
    import check_offline_items as coi
    import store as sm
    import token_metrics
    from fetch import ve_managed as vm
    db = tmp_path / "metrics.db"
    monkeypatch.setattr(sm, "DB_PATH", str(db))
    st = sm.Store(str(db))
    days = [str(d.date()) for d in pd.date_range("2026-10-19", "2026-10-23")]
    rows = [(d, FB["stock_metric"], 174_772_259.98 if d < "2026-10-22" else 816_485.12, "ve_managed:foundation_locks")
            for d in days]
    rows += [(d, m, 0.0, "ve_managed:buyback_window") for d in ("2026-10-19", "2026-10-22", "2026-10-23")
             for m in WM.values()]
    st.upsert(pd.DataFrame([{"date": d, "project": "Aerodrome", "metric": m, "value": v, "source": s, "tier": 2}
                            for d, m, v, s in rows]))
    monkeypatch.setattr(vm, "today", lambda: pd.Timestamp("2026-10-24"))
    monkeypatch.setattr(vm, "SEED_DAYS", 5)
    monkeypatch.setitem(FB, "seed_from", None)
    monkeypatch.setattr(coi, "rpc_block_at", lambda chain, ts, say=print: 1_000 + ts // 86400)
    win = {"bought_same_tx": 0.0, "lock_from_balance": 0.0, "purchased": 100.0, "compounded_owner": 0.0,
           "rebase_owner": 0.0, "locks": [], "purchases": [1], "inflows": {}, "compounders": {}, "error": None}
    stock = {"locked": 194_597_421.24, "claimable": 0.0, "nfts": 2, "owned_managed": 0, "by_owner": {},
             "legacy": 816_485.12, "direct": 0.0, "relay": 193_780_936.11, "pending": 0.0}
    asked = []
    monkeypatch.setattr(vm, "_window_reader", lambda b0, b1: (asked.append("legacy window"), win)[1])
    # R13: the v3 window returns the burn, what was sent out and the liquid balance with its five parts
    win_v3 = {**win, "burned": 0.0, "burned_other": 0.0, "sent_out": 0.0, "liquid": 1.0}
    monkeypatch.setattr(vm, "_window_reader_v3", lambda b0, b1: (asked.append("v3 window"), win_v3)[1])
    monkeypatch.setattr(vm, "_foundation_reader", lambda o, block="latest": (asked.append("legacy stock"), stock)[1])
    monkeypatch.setattr(vm, "_foundation_reader_v3", lambda o, block="latest": (asked.append("v3 stock"), stock)[1])
    # R13: the two reads beside the window — the burn of a v3 day whose window holds none, the liquid AERO at a day's end
    monkeypatch.setattr(vm, "_burn_reader", lambda b0, b1: (asked.append("burn"), {
        "burned": 0.0, "burned_other": 0.0, "sent_out": 0.0, "liquid": 1.0})[1])
    monkeypatch.setattr(vm, "_liquid_reader", lambda b: (asked.append("liquid"), {"liquid": 1.0})[1])
    with caplog.at_level(logging.INFO):
        token_metrics.seed_aero_buyback(st, logging.getLogger("r12"))
    # R13: + the liquid balance at the end of the three legacy days (the v3 windows carry theirs).
    # Was: ["legacy window"] * 2 + ["v3 stock"] * 2 + ["v3 window"] * 2
    assert sorted(asked) == ["legacy window"] * 2 + ["liquid"] * 3 + ["v3 stock"] * 2 + ["v3 window"] * 2
    lg = st.load_long()
    lg = lg[lg.project == "Aerodrome"]
    src = {(str(r.date)[:10], r.metric): (r.source, float(r.value)) for r in lg.itertuples(index=False)}
    pm = WM["purchased"]
    assert src[("2026-10-19", pm)] == ("ve_managed:buyback_window", 0.0), "a stored day before the cutover is held"
    assert src[("2026-10-20", pm)] == src[("2026-10-21", pm)] == ("ve_managed:buyback_window", 100.0)
    assert src[("2026-10-22", pm)] == src[("2026-10-23", pm)] == ("ve_managed:buyback_window_v3", 100.0)
    assert src[("2026-10-21", FB["stock_metric"])] == ("ve_managed:foundation_locks", 174_772_259.98)
    assert src[("2026-10-23", FB["stock_metric"])] == ("ve_managed:foundation_locks_v3", 194_597_421.24)
    text = caplog.text
    assert text.count("--seed aero_buyback:  2026-") == 5
    # R13: 35 rows = the 24 of before + 3 liquid rows (10-19..10-21) + 2 v3 days x (burned, burned other, sent out,
    # liquid). Was: "24 row(s) stored; of 5 day(s): 1 already held, …"; 10-19 is no longer "already held" (its liquid
    # balance was read)
    assert ("35 row(s) stored; of 5 day(s): 0 already held, 4 buyback window(s) read (1 already stored, 0 not read), "
            "2 lock stock(s) read (0 not read), 0 day(s) with no block; beside the windows 0 burn read(s) and 3 liquid "
            "balance(s) (0 not read)") in text
    # a second run reads nothing and says why
    asked.clear()
    with caplog.at_level(logging.INFO):
        token_metrics.seed_aero_buyback(st, logging.getLogger("r12"))
    st.close()
    assert asked == [] and "WHY 0 STORED — all 5 days already stored (lock stock and buyback window)" in caplog.text
