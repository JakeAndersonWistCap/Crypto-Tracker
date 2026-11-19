"""R13 (Jake 2026-11-19) — Aerodrome's buyback: the purchase-day basis and the burn leg (SPEC "Round 13").

No network: every reader is injected or the chain is a stand-in. What is tested is behaviour and arithmetic — the
handover date loses and double counts nothing; a purchase counts once whether or not it is later locked or staked; a
burn moves AERO from held to burned and leaves retired unchanged; the token-flow statement balances with the burn;
before the migration the burned leg is the declared zero; the buyer Safe's balance leaves the float once.
The figures are the stored window rows of 2026-07-15..11-18 (Jake's export + the R12 live read)."""
from __future__ import annotations

import pandas as pd
import pytest
from eth_utils import keccak

import build_workbook as bw
import config

P = config.PROJECT_BY_NAME["Aerodrome"]
FB = P["foundation_buyback"]
WM = FB["window_metrics"]
XM = FB["extra_metrics"]
HANDOVER = pd.Timestamp("2026-08-25")
CUT = pd.Timestamp("2026-10-22")
PGF = "0x834c0da026d5f933c2c18fa9f8ba7f1f792fda52"
BLF = "0x623cf63a1fa7068ebbdba9f2eb262613eab557a1"
SAFE = "0x10b42e926c2b8ff0200f35ec466d50576c1efa30"
LEGACY = "0x940181a94a35a4569e4529a3cdfb74e38fd98631"
NEW = "0x3f5bf3edac59d650d8fdf9657a71f2f904a3a4ee"
SAERO = "0xbf7977e1521e9a3032bf7243ee4712f311f13915"
VE = "0xebf418fe2512e7e6bd9b87a8f0f294acdc67e6b4"
MIG = "0x22a2f7340a3fb4c7c389d91ee954766b12476721"
VPM = "0xbe8e5149dc0fe28d124de4222b7fb332de99aa79"
ZERO = "0x" + "0" * 40
OUTSIDE = "0x101a124c068b58938762f9e23af5c555b661473d"
# THE STORED SERIES: every purchase and every lock paid from the balance, 2026-07-15..11-18
PURCHASES = {"2026-07-15": 214_508.93, "2026-07-22": 336_195.19, "2026-07-29": 312_820.96, "2026-08-06": 373_687.12,
             "2026-08-12": 343_699.45, "2026-08-19": 325_784.61, "2026-08-26": 155_056.75, "2026-09-02": 281_944.42,
             "2026-09-09": 171_488.66, "2026-09-16": 135_142.67, "2026-09-23": 34_091.07, "2026-10-28": 174_600.64,
             "2026-10-30": 233_215.20, "2026-11-04": 243_612.23, "2026-11-11": 294_003.10, "2026-11-18": 503_340.91}
LOCKS = {"2026-08-24": 2_746_705.44, "2026-10-28": 174_600.64, "2026-10-30": 233_215.20}
BURNS = {"2026-10-28": 174_600.64, "2026-10-30": 233_215.20}
DAYS = pd.date_range("2026-07-15", "2026-11-18")


def _parts(purchases=PURCHASES, locks=LOCKS, same_tx=None, compounded=None, days=DAYS) -> dict:
    ser = lambda d: pd.Series({pd.Timestamp(k): float(v) for k, v in (d or {}).items()},  # noqa: E731
                              dtype=float).reindex(days).fillna(0.0)
    return {"purchased": ser(purchases), "lock_from_balance": ser(locks), "bought_same_tx": ser(same_tx),
            "compounded_owner": ser(compounded), "rebase_owner": ser(None)}


def _fb(date="2026-08-25") -> dict:
    return {**FB, "purchase_day_basis": ({**FB["purchase_day_basis"], "from": date} if date else None)}


# ------------------------------------------------------------------------------------------------ B1: the handover
def test_the_declared_date_loses_nothing_and_counts_nothing_twice():
    """2026-08-25, the day after the last lock that purchases were matched to: the pool after the 08-24 match is
    exactly 0, so every purchase is counted exactly once — up to 08-19 by the matching on the lock day, from 08-26 on
    its own day."""
    assert FB["purchase_day_basis"]["from"] == "2026-08-25"
    vals, src, info = bw.foundation_basis_series(FB, _parts(), DAYS)
    assert info["pool_at_handover"] == 0.0 and info["remainder"] == 0.0 and info["remainder_day"] is None
    # nothing lost, nothing twice: the series is every purchase, once
    assert vals.sum() == pytest.approx(sum(PURCHASES.values()), abs=1e-6)
    before = {d: v for d, v in PURCHASES.items() if pd.Timestamp(d) < HANDOVER}
    assert vals[pd.Timestamp("2026-08-24")] == pytest.approx(sum(before.values())) == pytest.approx(1_906_696.26)
    assert all(vals[pd.Timestamp(d)] == 0 for d in before), "bought before the date: counted by the matching, not here"
    for d, v in PURCHASES.items():
        if pd.Timestamp(d) >= HANDOVER:
            assert vals[pd.Timestamp(d)] == pytest.approx(v), d
    # the two bases never share a day, and each says which it is
    assert {s for d, s in src.items() if d < HANDOVER} == {bw.LOCKED_BASIS_SOURCE}
    assert {s for d, s in src.items() if d >= HANDOVER} == {bw.PURCHASE_BASIS_SOURCE}
    # Q0 for as-of 2026-11-19 (2026-08-21..11-18): was 2,314,512.09 (the 08-24 lock + the two October stakes)
    q0 = vals[vals.index >= pd.Timestamp("2026-08-21")]
    old = bw.foundation_basis_series(_fb(None), _parts(), DAYS)[0]
    assert old[old.index >= pd.Timestamp("2026-08-21")].sum() == pytest.approx(2_314_512.10, abs=0.01)
    assert q0.sum() == pytest.approx(4_133_191.91, abs=0.01)
    assert q0.sum() - old[old.index >= pd.Timestamp("2026-08-21")].sum() == pytest.approx(
        sum(v for d, v in PURCHASES.items() if pd.Timestamp(d) >= HANDOVER and d not in BURNS), abs=0.01), \
        "what the new basis adds is exactly the purchases held and never locked"


def test_the_config_proves_the_pool_is_zero_at_the_handover_from_the_trace():
    tr = FB["trace"]
    assert sum(r["aero"] for r in tr["rows"]) == pytest.approx(tr["lock"]["matched_aero"], abs=0.01)
    assert tr["lock"]["aero"] > tr["lock"]["matched_aero"], "the lock is larger than the pool: the match empties it"
    assert max(r["date"] for r in tr["rows"]) < tr["lock"]["date"] < FB["purchase_day_basis"]["from"]
    assert min(d for d in PURCHASES if pd.Timestamp(d) >= HANDOVER) == "2026-08-26"


@pytest.mark.parametrize("date", ["2026-07-15", "2026-08-01", "2026-08-10", "2026-08-24", "2026-08-25", "2026-09-10",
                                  "2026-10-29", "2026-11-19"])
def test_whatever_the_date_every_purchase_is_counted_exactly_once(date):
    """The rule, not the luck of one date: a pool left unmatched at the handover is counted once, on the first stored
    day from the date, and named — so the total is the purchases for ANY handover date that leaves no purchase
    unmatched after it. (A date AFTER a purchase that no later lock matches leaves that purchase in the old basis'
    pool — uncounted there by the old basis's own rule, as before R13.)"""
    vals, src, info = bw.foundation_basis_series(_fb(date), _parts(), DAYS)
    d0 = pd.Timestamp(date)
    counted_before = sum(v for d, v in PURCHASES.items() if pd.Timestamp(d) < d0)        # purchases dated before
    locked_before = sum(v for d, v in LOCKS.items() if pd.Timestamp(d) < d0)
    if d0 <= DAYS[-1]:
        # everything bought before the date is counted: by a lock's match, or as the named remainder
        assert vals.sum() == pytest.approx(sum(PURCHASES.values()), abs=1e-6), date
        rest = max(counted_before - min(counted_before, locked_before), 0.0)
        if date in ("2026-08-01", "2026-08-10", "2026-08-24"):
            assert info["remainder"] == pytest.approx(rest) and rest > 0
            first = min(d for d in vals.index if d >= d0)
            assert info["remainder_day"] == first and "handover's remainder, counted once" in src[first]
    else:
        # no handover inside the series: the old basis, whole
        assert vals.equals(bw.foundation_basis_series(_fb(None), _parts(), DAYS)[0])


def test_a_purchase_counts_once_whether_or_not_it_is_later_locked_or_staked():
    days = pd.date_range("2026-09-01", "2026-09-10")
    held = _parts({"2026-09-02": 1_000.0}, {}, days=days)
    locked = _parts({"2026-09-02": 1_000.0}, {"2026-09-05": 1_000.0}, days=days)                 # staked three days on
    more = _parts({"2026-09-02": 1_000.0}, {"2026-09-05": 4_000.0}, days=days)                   # + 3,000 not bought
    for parts in (held, locked, more):
        vals, _src, _info = bw.foundation_basis_series(FB, parts, days)
        assert vals[pd.Timestamp("2026-09-02")] == 1_000.0 and vals[pd.Timestamp("2026-09-05")] == 0.0
        assert vals.sum() == 1_000.0
    # bought and locked in ONE transaction is a purchase of that day too — once, and compounding is added on accrual
    one = _parts({}, {}, same_tx={"2026-09-03": 500.0}, compounded={"2026-09-04": 12.5}, days=days)
    vals, _s, _i = bw.foundation_basis_series(FB, one, days)
    assert (vals[pd.Timestamp("2026-09-03")], vals[pd.Timestamp("2026-09-04")], vals.sum()) == (500.0, 12.5, 512.5)


def test_the_handover_of_the_buyback_series_is_declared_with_two_distinct_points():
    from fetch.base import _measuring_point
    for metric in ("actual_buyback_tokens", "actual_buyback_usd"):
        d = config.declared_handover("Aerodrome", metric)
        a, b = d["ordered_points"]
        assert _measuring_point(a) != _measuring_point(b) and d["declared"] == "2026-08-25"
        assert "Jake 2026-11-19" in d["why"] and d["composition_change"]
    assert _measuring_point(bw.LOCKED_BASIS_SOURCE) == _measuring_point(
        config.declared_handover("Aerodrome", "actual_buyback_tokens")["ordered_points"][0])
    assert _measuring_point(bw.PURCHASE_BASIS_SOURCE) == _measuring_point(
        config.declared_handover("Aerodrome", "actual_buyback_tokens")["ordered_points"][1])
    assert _measuring_point(bw.TRACE_SOURCE.format(label="x", buy="0x1", fwd="0x2")) == _measuring_point(
        bw.LOCKED_BASIS_SOURCE), "the trace stands in for the lock on the old basis: no third point"


# ------------------------------------------------------------------------------------------------- the build's views
def _groups(extra: dict | None = None, burned: dict | None = BURNS, liquid: dict | None = None,
            days=DAYS) -> dict:
    old, new = "ve_managed:buyback_window:archive", "ve_managed:buyback_window_v3:archive"
    parts = _parts(days=days)
    rows = []
    for k, m in WM.items():
        rows += [(d, m, float(parts[k][d]), new if d >= CUT else old) for d in days]
    for d in days[days >= CUT]:
        rows.append((d, XM["burned"], float((burned or {}).get(str(d.date()), 0.0)), new))
        rows.append((d, XM["burned_other"], 0.0, new))
    for d, v in (liquid or {}).items():
        rows.append((pd.Timestamp(d), XM["liquid"], v, "ve_managed:foundation_liquid"))
    tot = pd.date_range(days[0], days[-1] + pd.Timedelta(days=1))
    rows += [(d, "total_supply", 2.1e9, "aero_v3:supply_once") for d in tot]
    rows += [(d, "price_usd", 0.5, "coingecko") for d in tot]
    rows += [(pd.Timestamp(d), m, v, s) for (m, d, s), v in (extra or {}).items()]
    out: dict = {}
    for d, m, v, s in rows:
        out.setdefault(("Aerodrome", m), []).append({"date": pd.Timestamp(d), "project": "Aerodrome", "metric": m,
                                                     "value": float(v), "source": s, "tier": 2})
    return {k: pd.DataFrame(v).sort_values("date").reset_index(drop=True) for k, v in out.items()}


def _views(monkeypatch, groups: dict) -> dict:
    monkeypatch.setattr(bw, "_SCOPE", [P])
    monkeypatch.setitem(FB, "trace", None)
    bw._foundation_buyback_views(groups)
    bw._buyback_split_views(groups)
    bw._burned_declared_zero_views(groups)
    bw._foundation_burn_views(groups)
    return {m: groups[("Aerodrome", m)].set_index("date")["value"] for m in (
        "actual_buyback_tokens", "buyback_burned_tokens", "buyback_held_tokens", "retired_buyback_tokens",
        "retired_buyback_usd") if ("Aerodrome", m) in groups}


def test_a_burn_moves_aero_from_held_to_burned_and_retired_is_unchanged(monkeypatch):
    g0 = _groups(burned=None)                                              # the same series, nothing burned
    base = _views(monkeypatch, g0)
    g = _groups()
    s = _views(monkeypatch, g)
    burned, held, retired = s["buyback_burned_tokens"], s["buyback_held_tokens"], s["retired_buyback_tokens"]
    assert burned.sum() == pytest.approx(407_815.84) and burned[burned > 0].index.tolist() == [
        pd.Timestamp("2026-10-28"), pd.Timestamp("2026-10-30")]
    # held falls by what was burned, on the burn day; here the burn day is the purchase day: never shown as held
    assert held[pd.Timestamp("2026-10-28")] == 0 and held[pd.Timestamp("2026-10-30")] == 0
    assert base["buyback_held_tokens"].sum() - held.sum() == pytest.approx(burned.sum())
    # retired = burned + held, unchanged by the burn; the buyback itself does not move; nothing is counted twice
    assert retired.equals(base["retired_buyback_tokens"]) and s["retired_buyback_usd"].equals(base["retired_buyback_usd"])
    assert s["actual_buyback_tokens"].equals(base["actual_buyback_tokens"])
    both = pd.concat([burned, held], axis=1).dropna()
    assert (both.sum(axis=1) - retired.reindex(both.index)).abs().max() < 1e-6
    assert retired.sum() == pytest.approx(s["actual_buyback_tokens"].sum()) == pytest.approx(4_133_191.91, abs=0.01)
    assert burned.sum() + held.sum() == pytest.approx(retired.sum())
    # one measuring point for held (the burn is an annotation); the burned leg's three points are declared, in order
    from fetch.base import _measuring_point
    assert len({_measuring_point(x) for x in g[("Aerodrome", "buyback_held_tokens")]["source"]}) == 1
    pts = [_measuring_point(x) for x in config.declared_handover("Aerodrome", "buyback_burned_tokens")["ordered_points"]]
    got = [_measuring_point(x) for x in g[("Aerodrome", "buyback_burned_tokens")].sort_values("date")["source"]]
    assert set(got) <= set(pts) and got == sorted(got, key=pts.index), "declared points, in the declared order"


def test_a_burn_on_a_later_day_than_the_purchase_shows_held_then_moved(monkeypatch):
    s = _views(monkeypatch, _groups(burned={"2026-11-06": 243_612.23}))    # the 11-04 purchase, burned two days on
    assert s["buyback_held_tokens"][pd.Timestamp("2026-11-04")] == pytest.approx(243_612.23)
    assert s["buyback_held_tokens"][pd.Timestamp("2026-11-06")] == pytest.approx(-243_612.23)      # moved out of held
    assert s["buyback_burned_tokens"][pd.Timestamp("2026-11-06")] == pytest.approx(243_612.23)
    assert s["retired_buyback_tokens"][pd.Timestamp("2026-11-06")] == 0
    assert s["retired_buyback_tokens"].sum() == pytest.approx(s["actual_buyback_tokens"].sum())


def test_before_the_migration_the_burned_leg_is_the_declared_zero_and_after_it_only_what_was_measured(monkeypatch):
    g = _groups()
    for k in [k for k in g if k[1] in (XM["burned"], XM["burned_other"])]:
        g[k] = g[k][g[k]["date"] != pd.Timestamp("2026-11-10")]            # one day of the v3 read holds no burn row
    _views(monkeypatch, g)
    b = g[("Aerodrome", "buyback_burned_tokens")].set_index("date")
    pre = b[b.index < CUT]
    assert len(pre) == 99 and (pre["value"] == 0).all() and set(pre["source"]) == {
        "derived:declared_zero[no burn mechanism exists]"}
    post = b[b.index >= CUT]
    assert set(post["source"]) == {bw.BURNED_MEASURED_SOURCE}
    assert pd.Timestamp("2026-11-10") not in b.index, "a day not measured holds no value — never the declared 0"
    assert pd.Timestamp("2026-11-19") not in b.index, "nor a day the total is held but no burn was read"
    assert config.BURNED_DECLARED_ZERO["Aerodrome"]["until"] == "2026-10-22" == FB["burn_leg"]["from"]
    # with no burn row stored at all, nothing from the migration on is claimed
    g2 = _groups()
    for k in [k for k in g2 if k[1] in (XM["burned"], XM["burned_other"])]:
        del g2[k]
    _views(monkeypatch, g2)
    assert g2[("Aerodrome", "buyback_burned_tokens")]["date"].max() == pd.Timestamp("2026-10-21")


def test_a_burn_beyond_what_was_bought_is_not_a_burned_buyback_and_is_flagged():
    bought = pd.Series({pd.Timestamp("2026-10-28"): 100.0, pd.Timestamp("2026-10-30"): 50.0})
    burned = pd.Series({pd.Timestamp("2026-10-27"): 10.0, pd.Timestamp("2026-10-28"): 80.0,
                        pd.Timestamp("2026-10-29"): 40.0, pd.Timestamp("2026-10-31"): 60.0})
    ok, extra = bw.foundation_burn_split(bought, burned)
    assert ok.tolist() == [0.0, 80.0, 20.0, 50.0] and extra.tolist() == [10.0, 0.0, 20.0, 10.0]
    assert ok.sum() <= bought.sum() and ok.sum() + extra.sum() == burned.sum()
    bw.FOUNDATION_BURN["Aerodrome"] = {"not_bought": 40.0, "other": 5.0}
    try:
        f = bw.foundation_flags("Aerodrome", "buyback_burned_tokens")
        assert len(f) == 1 and "45.00 AERO destroyed" in f[0] and "NOT counted" in f[0]
        assert bw.foundation_flags("Aerodrome", "price_usd") == []
    finally:
        bw.FOUNDATION_BURN.clear()


# ------------------------------------------------------------------------------------------------ "while held"
def test_purchases_that_left_the_wallets_are_flagged_not_subtracted(monkeypatch):
    parts = _parts()
    days = pd.date_range("2026-08-25", "2026-11-18")
    # as read on chain: the wallets always held more liquid AERO than the purchases not yet locked
    plenty = pd.Series(1_700_000.0, index=days)
    plenty[days >= pd.Timestamp("2026-11-04")] = 2_800_000.0
    assert bw.foundation_not_held(FB, parts, plenty).max() == 0.0
    # the stock the check holds: bought since the date and not locked (the October purchases were staked the same day)
    none = pd.Series(0.0, index=days)
    exp = bw.foundation_not_held(FB, parts, none)
    assert exp[pd.Timestamp("2026-08-25")] == 0 and exp[pd.Timestamp("2026-09-23")] == pytest.approx(777_723.57)
    assert exp[pd.Timestamp("2026-10-30")] == pytest.approx(777_723.57), "a stake takes the purchase out of the liquid stock"
    assert exp[pd.Timestamp("2026-11-18")] == pytest.approx(777_723.57 + 243_612.23 + 294_003.10 + 503_340.91)
    # the wallets pay 1,500,000 out on 11-12: 318,679.81 of the purchases can no longer be there
    short = plenty.copy()
    short[days >= pd.Timestamp("2026-11-12")] = 1_000_000.0
    nh = bw.foundation_not_held(FB, parts, short)
    assert nh[pd.Timestamp("2026-11-11")] == 0 and nh[pd.Timestamp("2026-11-12")] == pytest.approx(315_338.90)
    assert nh[pd.Timestamp("2026-11-18")] == pytest.approx(818_679.81)
    # a day whose balance is not stored has no value (unknown, never 0)
    assert pd.Timestamp("2026-08-24") not in nh.index and len(bw.foundation_not_held(FB, parts, short.iloc[:3])) == 3
    # in the build: the flag is a series and a line on the buyback cells; the buyback itself is NOT reduced
    g = _groups(liquid={str(d.date()): v for d, v in short.items()})
    s = _views(monkeypatch, g)
    assert s["actual_buyback_tokens"].sum() == pytest.approx(4_133_191.91, abs=0.01)
    flag = g[("Aerodrome", FB["held_check"]["flag_metric"])].set_index("date")["value"]
    assert flag.iloc[-1] == pytest.approx(818_679.81)
    notes = bw.foundation_flags("Aerodrome", "actual_buyback_tokens")
    # R14 (Jake 2026-11-19, R13 decision 2): the shortfall is SUBTRACTED from held and retired as a named negative
    # row (was: "NOT subtracted"); what was bought (actual_buyback_tokens, above) still does not move
    assert len(notes) == 1 and "818,679.81 AERO" in notes[0] and "SUBTRACTED" in notes[0]
    row = g[("Aerodrome", FB["held_check"]["subtract_metric"])].set_index("date")["value"]
    assert row.sum() == pytest.approx(-818_679.81)
    g = _groups(liquid={str(d.date()): v for d, v in plenty.items()})
    _views(monkeypatch, g)
    assert bw.foundation_flags("Aerodrome", "actual_buyback_tokens") == []
    bw.FOUNDATION_BURN.clear()


# ------------------------------------------------------------------------------------------------- the reader
def _top(sig: str) -> str:
    return "0x" + keccak(text=sig).hex().removeprefix("0x")


def _pad(a) -> str:
    return "0x" + (a[2:] if isinstance(a, str) else f"{a:x}").rjust(64, "0")


def _w(n: float, dec: int = 18) -> str:
    return f"{int(round(n * 10 ** dec)):064x}"


def xfer(token, frm, to, amount, dec=18):
    return {"address": token, "topics": [_top("Transfer(address,address,uint256)"), _pad(frm), _pad(to)],
            "data": "0x" + _w(amount, dec)}


def routed(wallet, token_id, amount, fee=0.0, dest=0, vpm=VPM):
    return {"address": vpm, "topics": [_top("DepositedIntoNFT(address,(uint256,uint128)[],uint256,uint256,uint256)"),
                                       _pad(wallet), _pad(dest)],
            "data": "0x" + _w(0x60, 0) + _w(amount) + _w(fee) + _w(1, 0) + _w(token_id, 0) + _w(amount)}


def ctx():
    return {"aero": {LEGACY, NEW}, "owners": {PGF, BLF}, "buyers": {SAFE}, "lockers": {PGF, BLF, SAFE},
            "escrows": {VE, SAERO}, "saero": SAERO, "migration": MIG, "vpm": VPM}


def test_the_readers_burn_is_the_wallets_own_stake_and_nothing_else():
    import check_offline_items as coi
    # the event's topic as it is on chain (tx 0xe00ba4ce…, log of 0xbe8e5149…: topic0 0x08f8081c…)
    assert _top("DepositedIntoNFT(address,(uint256,uint128)[],uint256,uint256,uint256)").startswith("0x08f8081c")
    assert FB["burn_leg"]["voter_payments_module"] == VPM
    amt = 174_600.6362
    got = coi.v3_classify_tx([routed(SAFE, 19526, amt), xfer(NEW, SAERO, ZERO, amt)], ctx(), tx_to=SAFE)
    assert got["burned"] == pytest.approx(amt) and got["burned_other"] == 0 and got["purchased"] == 0
    assert got["routed_to_accumulator"] == pytest.approx(amt) and got["locks"] == [] and got["sent_out"] == 0
    # the wallet burns MORE than the stake it routed (fees at the accumulator): the rest is not its stake
    got = coi.v3_classify_tx([routed(SAFE, 1, 100.0), xfer(NEW, SAERO, ZERO, 130.0)], ctx(), tx_to=SAFE)
    assert got["burned"] == pytest.approx(100.0) and got["burned_other"] == pytest.approx(30.0)
    # routed to another sAERO (destinationId not 0), by a stranger, or through a contract that is not the module:
    # nothing of the burn is the wallet's own stake
    for logs in ([routed(SAFE, 1, 100.0, dest=7), xfer(NEW, SAERO, ZERO, 100.0)],
                 [routed(OUTSIDE, 1, 100.0), xfer(NEW, SAERO, ZERO, 100.0)],
                 [routed(SAFE, 1, 100.0, vpm=OUTSIDE), xfer(NEW, SAERO, ZERO, 100.0)]):
        got = coi.v3_classify_tx(logs, ctx(), tx_to=SAFE)
        assert got["burned"] == 0 and got["burned_other"] == pytest.approx(100.0)
    # a burn in a transaction the family did not execute is not theirs at all
    got = coi.v3_classify_tx([routed(SAFE, 1, 100.0), xfer(NEW, SAERO, ZERO, 100.0)], ctx(), tx_to=OUTSIDE)
    assert got["burned"] == 0 and got["burned_other"] == 0
    # AERO paid out to a third party is SENT OUT; forwarded inside the family, staked or migrated is not
    assert coi.v3_classify_tx([xfer(NEW, PGF, OUTSIDE, 27_277.0)], ctx(), tx_to=PGF)["sent_out"] == pytest.approx(27_277.0)
    for logs in ([xfer(LEGACY, SAFE, PGF, 5.0)], [xfer(NEW, SAFE, SAERO, 5.0)], [xfer(LEGACY, PGF, MIG, 5.0)]):
        assert coi.v3_classify_tx(logs, ctx(), tx_to=SAFE)["sent_out"] == 0


def test_the_liquid_balance_is_both_tokens_where_both_exist_and_an_unread_balance_is_an_error(monkeypatch):
    import check_offline_items as coi
    e18 = 10 ** 18
    state = {"addresses": {"token": NEW}, "deploy_block": 1_000}
    asked = []

    def calls(chain, cs, chunk=200, block="latest"):
        asked.append((block, [to for to, _d in cs]))
        return [hex((3 if to == LEGACY else 10) * e18) for to, _d in cs]
    monkeypatch.setattr(coi, "_batch_eth_calls", calls)
    monkeypatch.setattr(coi, "_retry", lambda f, *a, **k: f())
    got = coi.aerodrome_foundation_liquid(2_000, state=state)
    assert got["liquid"] == pytest.approx(3 * 13.0) and got["buyer_liquid"] == pytest.approx(13.0)
    assert set(got["by"]) == {PGF, BLF, SAFE} and asked[-1][1].count(NEW) == 3
    # before the new token's deploy block only the legacy token exists: it alone is asked
    got = coi.aerodrome_foundation_liquid(999, state=state)
    assert got["liquid"] == pytest.approx(9.0) and NEW not in asked[-1][1] and asked[-1][0] == hex(999)
    monkeypatch.setattr(coi, "_batch_eth_calls", lambda chain, cs, chunk=200, block="latest": [None] * len(cs))
    assert "unread" in coi.aerodrome_foundation_liquid(2_000, state=state)["error"]


def test_the_seed_adds_the_burn_to_a_stored_window_without_reading_or_rewriting_it(monkeypatch):
    import check_offline_items as coi
    from fetch import ve_managed as vm
    monkeypatch.setattr(vm, "today", lambda: pd.Timestamp("2026-10-30"))
    monkeypatch.setattr(coi, "rpc_block_at", lambda chain, ts, say=print: 1_000 + ts // 86400)
    monkeypatch.setitem(FB, "seed_from", None)
    days = [str(d.date()) for d in pd.date_range("2026-10-27", "2026-10-29")]
    store = {(m, d): "ve_managed:buyback_window_v3" for m in WM.values() for d in days}
    store.update({(m, d): "ve_managed:foundation_locks_v3" for m in (FB["stock_metric"], FB["claimable_metric"])
                  for d in days})
    asked, lines = {"burn": [], "liquid": []}, []

    def burn_reader(b0, b1):
        asked["burn"].append((b0, b1))
        hit = b0 == 1_000 + pd.Timestamp("2026-10-28").value // 86_400_000_000_000
        return {"burned": 174_600.64 if hit else 0.0, "burned_other": 0.0, "sent_out": 0.0, "liquid": 1_702_156.66}

    def write(frames):
        for f in frames:
            key = (f["metric"].iloc[0], str(pd.Timestamp(f["date"].iloc[0]).date()))
            assert key not in store, "a stored row is never written again"
            store[key] = f["source"].iloc[0]
    kw = dict(days=3, stored_src=lambda n, m, d: store.get((m, d)), write=write, say=lines.append,
              window_reader=lambda b0, b1: pytest.fail("a stored window is not read again"),
              reader=lambda o, block="latest": pytest.fail("a stored stock is not read again"),
              burn_reader=burn_reader, liquid_reader=lambda b: (asked["liquid"].append(b), {"liquid": 1.0})[1])
    frames = vm.seed_foundation([{"name": "Aerodrome", "foundation_buyback": FB}], **kw)
    got = pd.concat(frames)
    assert set(got["metric"]) == {XM[k] for k in ("burned", "burned_other", "sent_out", "liquid")} and len(got) == 12
    assert len(asked["burn"]) == 3 and asked["liquid"] == [], "the burn read carries the day's liquid balance"
    b = got[got["metric"] == XM["burned"]].set_index("date")["value"]
    assert b[pd.Timestamp("2026-10-28")] == pytest.approx(174_600.64) and b.sum() == pytest.approx(174_600.64)
    assert set(got[got["metric"] == XM["burned"]]["source"]) == {"ve_managed:buyback_window_v3"}
    assert "burn READ" in lines[1] and "burned out of the escrow 174,600.64" in lines[1]
    assert vm.LAST_SEED["burn_read"] == 3 and vm.LAST_SEED["window_read"] == 0
    # resumable: the next run reads nothing
    asked["burn"].clear()
    assert vm.seed_foundation([{"name": "Aerodrome", "foundation_buyback": FB}], **kw) == [] and asked["burn"] == []
    # a burn read that fails stores nothing for the day and says so; unknown, never 0
    store2 = {k: v for k, v in store.items() if k[0] in WM.values() or k[0] in (FB["stock_metric"], FB["claimable_metric"])}
    kw2 = dict(kw, stored_src=lambda n, m, d: store2.get((m, d)), write=lambda fr: pytest.fail("nothing to write"),
               burn_reader=lambda b0, b1: {"error": "HTTP 429"}, liquid_reader=lambda b: {"error": "HTTP 429"})
    assert vm.seed_foundation([{"name": "Aerodrome", "foundation_buyback": FB}], **kw2) == []
    assert vm.LAST_SEED["extra_error"] == 6 and "burn NOT read — HTTP 429" in lines[-1]
    # before the migration there is no burn to read: a legacy day gets its liquid balance alone (from 2026-08-25)
    assert not vm.wants_liquid(FB, "2026-08-24") and vm.wants_liquid(FB, "2026-08-25")
    rows = vm.extra_rows("Aerodrome", FB, pd.Timestamp("2026-09-01"), {"burned": 5.0, "burned_other": 1.0, "liquid": 2.0})
    assert [f["metric"].iloc[0] for f in rows] == [XM["liquid"]]
    rows = vm.extra_rows("Aerodrome", FB, pd.Timestamp("2026-10-28"), {"burned": 5.0, "liquid": None})
    assert [f["metric"].iloc[0] for f in rows] == [XM["burned"]], "a key the read did not return writes nothing"


# ------------------------------------------------------------------------------------------------- the statement
def test_the_statement_balances_with_the_burn_and_shows_it(monkeypatch):
    monkeypatch.setitem(bw.GLOBALS, "period_days", 90)
    monkeypatch.setattr(bw, "_SCOPE", [P])
    bw._ONE_OFF_IN_SERIES.clear()
    asof = pd.Timestamp("2026-11-18")
    days = pd.date_range("2026-05-01", "2026-11-18")
    mint = 600_000.0
    burn = {pd.Timestamp(d): v for d, v in BURNS.items()}
    circ, c = [], 1_900e6
    for d in days:
        c += mint - burn.get(d, 0.0)                    # the supply read already holds the fall
        circ.append(c)
    mk = lambda m, v, src="x:delta": pd.DataFrame({"date": days, "project": "Aerodrome", "metric": m, "value": v,  # noqa: E731
                                                  "tier": 2, "source": src})
    g = {("Aerodrome", "circulating_supply_used"): mk("circulating_supply_used", circ, "derived:circulating_onchain"),
         ("Aerodrome", "gross_issuance_tokens"): mk("gross_issuance_tokens", mint),
         ("Aerodrome", "emissions_tokens"): mk("emissions_tokens", 0.0),
         ("Aerodrome", "issuer_mint_tokens"): mk("issuer_mint_tokens", 0.0),
         ("Aerodrome", "buyback_burned_tokens"): mk("buyback_burned_tokens", [burn.get(d, 0.0) for d in days],
                                                    bw.BURNED_MEASURED_SOURCE)}
    tf = {r["Line"]: r for r in bw.flow_statements(g, asof) if r["Statement"] == "Token flow"}
    assert tf["− burned"]["Q0"] == pytest.approx(407_815.84) and tf["− burned"]["Q-1"] == 0.0
    for q in ("Q0", "Q-1"):
        assert abs(tf["Residual ÷ opening"][q]) < 1e-9 and tf["Status"][q].startswith("OK"), (q, tf["Status"][q])
    # without the burned leg the quarter's residual is the burn (what Jake's build showed: -383,908 of -407,816)
    del g[("Aerodrome", "buyback_burned_tokens")]
    tf = {r["Line"]: r for r in bw.flow_statements(g, asof) if r["Statement"] == "Token flow"}
    assert tf["Residual (closing − expected)"]["Q0"] == pytest.approx(-407_815.84)
    # nothing else subtracts it: no burn series of the supply, no net total, and circulating does not read the leg
    assert config.RULE_IN["Aerodrome"]["flow_burn"] == "buyback_burned_tokens"
    assert config.a1_burn_metric("Aerodrome") == "gross_burn_tokens" not in config.metrics_for_project(P)
    assert "Aerodrome" not in config.TOTAL_SUPPLY_NET
    assert "buyback_burned_tokens" not in config.CIRCULATING_ONCHAIN["Aerodrome"]["subtract"]


# ------------------------------------------------------------------------------------------------- the float
def _rule_groups(buyer: dict, days=pd.date_range("2026-10-18", "2026-10-26")) -> dict:
    mk = lambda m, v: pd.DataFrame({"date": list(v), "project": "Aerodrome", "metric": m,  # noqa: E731
                                    "value": list(v.values()), "tier": 2, "source": "x"})
    return {("Aerodrome", "aero_issuer_held_tokens"): mk("aero_issuer_held_tokens", {d: 1_000.0 for d in days}),
            ("Aerodrome", "filing_managed_lock_tokens"): mk("filing_managed_lock_tokens", {d: 500.0 for d in days}),
            ("Aerodrome", "aero_buyer_liquid_tokens"): mk("aero_buyer_liquid_tokens",
                                                          {pd.Timestamp(d): v for d, v in buyer.items()})}


def test_the_buyer_safes_balance_leaves_the_float_from_the_cutover_once(monkeypatch):
    rule = config.RULE_IN["Aerodrome"]
    assert rule["off_float"] == ("aero_issuer_held_tokens", "filing_managed_lock_tokens", "aero_buyer_liquid_tokens")
    assert rule["off_float_from"] == {"aero_buyer_liquid_tokens": "2026-10-22"}
    # the buyer Safe is not one of the issuer wallets whose liquid AERO aero_issuer_held_tokens sums: added once
    issuer = {str(w["address"]).lower() for k, w in P["contracts"].items() if k.startswith("noncirc_")}
    assert SAFE not in issuer and PGF in issuer and len(issuer) == 7
    monkeypatch.setattr(config, "RULE_IN", {"Aerodrome": rule})
    g = _rule_groups({"2026-10-22": 0.0, "2026-10-23": 174_600.0, "2026-10-24": 0.0, "2026-10-26": 0.0})
    bw._rule_in_views(g)
    s = g[("Aerodrome", "rule_in_off_float_tokens")].set_index("date")["value"]
    # before the cutover the leg is no part of the series: the days keep their value
    assert s[pd.Timestamp("2026-10-18")] == s[pd.Timestamp("2026-10-21")] == 1_500.0
    # a purchase the Safe still holds is off the float that day; forwarded, it is in the issuer wallets' balance —
    # the same block, so never in both
    assert s[pd.Timestamp("2026-10-23")] == 1_500.0 + 174_600.0 and s[pd.Timestamp("2026-10-24")] == 1_500.0
    # from the cutover a day missing the leg is no value (never the float without it)
    assert pd.Timestamp("2026-10-25") not in s.index and len(s) == 8
    # THE HANDOVER IS AT THE LEG'S FIRST STORED ROW from the declared date: a store its history has not reached
    # (the backfill not run yet) keeps the series it had — "now" is never the last day before the cutover
    g = _rule_groups({"2026-10-25": 7.0, "2026-10-26": 0.0})
    bw._rule_in_views(g)
    s = g[("Aerodrome", "rule_in_off_float_tokens")].set_index("date")["value"]
    assert len(s) == 9 and s[pd.Timestamp("2026-10-24")] == 1_500.0 and s[pd.Timestamp("2026-10-25")] == 1_507.0
    assert "aero_buyer_liquid_tokens from 2026-10-25" in g[("Aerodrome", "rule_in_off_float_tokens")]["source"].iloc[0]
    g = _rule_groups({"2026-10-20": 99.0})                                   # only a row before the declared date
    bw._rule_in_views(g)
    s = g[("Aerodrome", "rule_in_off_float_tokens")].set_index("date")["value"]
    assert len(s) == 9 and (s == 1_500.0).all(), "a row dated before the declared date is no part of the sum"
    g = _rule_groups({})
    del g[("Aerodrome", "aero_buyer_liquid_tokens")]                          # the leg not read at all yet
    bw._rule_in_views(g)
    assert (g[("Aerodrome", "rule_in_off_float_tokens")]["value"] == 1_500.0).all()
    assert "not read yet" in g[("Aerodrome", "rule_in_off_float_tokens")]["source"].iloc[0]


def test_the_supply_read_stores_the_buyer_leg_at_its_own_block_and_supply_once_does_not_read_it():
    from fetch import aero_v3 as a
    assert "aero_buyer_liquid_tokens" in a.SUPPLY_LEGS and config.METRICS["aero_buyer_liquid_tokens"]["view_only"]
    wad = a.WAD
    velo = "0x" + "7e" * 20
    seen = []

    class Rdr(a.AeroV3):
        def rpc(self, method, params):
            return "0x60" if method == "eth_getCode" else None

        def call(self, to, name, *args, block="latest"):
            seen.append((to, name, args, block))
            who = args[0].lower() if args and isinstance(args[0], str) else None
            if name == "balanceOf":
                if who == SAFE:
                    return (7 * wad if to == NEW else 3 * wad,)
                return (80 * wad if who == MIG else 20 * wad if who == velo else 1 * wad,)
            return {"MIGRATION": (MIG,), "VELODROME_MIGRATION": (velo,), "V2_TOKEN": (a.LEGACY_TOKEN,),
                    "V2_ESCROW": (a.LEGACY_ESCROW,), "V3_TOKEN": (NEW,), "MIGRATION_TOKEN_ID": (77,),
                    "previewConversion": (100 * wad, 100 * wad, 0, 1), "remaining": (80 * wad,),
                    "totalSupply": (1_000 * wad,)}[name]
    t = Rdr(state={"addresses": {"token": NEW}}, rpc=lambda *x: None)._supply_terms("Aerodrome", {"token": NEW}, 4_096)
    assert t["buyer_liq"] == pytest.approx(10.0) and t["buyers"] == 1            # new 7 + legacy 3, the Safe alone
    assert {blk for *_x, blk in seen} == {4_096}, "every term at ONE block"
    assert t["new_nc"] == pytest.approx(7.0) and t["old_nc"] == pytest.approx(7.0), "the seven issuer wallets, without it"
    frames = {f["metric"].iloc[0]: f for f, _m in a.AeroV3(state={}, rpc=lambda *x: None)._supply_frames(
        "Aerodrome", t, pd.Timestamp("2026-11-19")) if f is not None}
    assert frames["aero_buyer_liquid_tokens"]["value"].iloc[0] == pytest.approx(10.0)
    assert frames["aero_buyer_liquid_tokens"]["source"].iloc[0] == "aero_v3:balanceOf(foundation buyer wallets)"
    # total and circulating are what supply_once made of the other terms: the buyer balance is no input of it
    assert frames["total_supply"]["value"].iloc[0] == pytest.approx(t["r"]["total"])
    import inspect
    assert "buyer" not in inspect.signature(a.supply_once).parameters and not any(
        "buyer" in p for p in inspect.signature(a.supply_once).parameters)


# ------------------------------------------------------------------------------------------------- the declarations
def test_the_burn_mechanism_and_the_two_burns_are_recorded_with_their_transactions():
    bl = config.AERODROME_BURN_LEG
    assert FB["burn_leg"] is bl and bl["from"] == config.AERODROME_MIGRATION["cutover_utc"][:10]
    assert [b["date"] for b in bl["burns"]] == ["2026-10-28", "2026-10-30"]
    assert sum(b["aero"] for b in bl["burns"]) == pytest.approx(407_815.83, abs=0.01) == pytest.approx(
        config.AERO_RULE_READ["burned"], abs=0.01)
    for b in bl["burns"]:
        for k in ("funded_tx", "buy_tx", "stake_tx", "burn_tx"):
            assert b[k].startswith("0x") and len(b[k]) == 66, (b["date"], k)
        assert b["usdc"] / b["aero"] == pytest.approx(0.67, abs=0.05)             # the price paid, USDC per AERO
    assert {"established", "documented", "not_established"} <= set(bl["funding"])
    mech = P["burn_mechanism"]
    assert mech["model"] == "protocol_level_destruction" and mech["status"] == "confirmed" and mech["from"] == "2026-10-22"
    assert P["burn_mechanism_was"]["model"] == "no_burn" and P["burn_mechanism_was"]["until"] == "2026-10-22"
    assert config.burn_mechanism(P)["model"] in config.BURN_MECHANISM_MODELS
    assert P["archetypes"] == [3], "the burn is a destination of the buyback, not a fee burn: archetype 4 is not added"
    # nothing derives a figure from the mechanism for Aerodrome: its issuance is read, not derived from the supply
    assert "gross_issuance_tokens" not in config.metrics_for_project(P) and P.get("burn_read_method") is None
    last = config.buyback_destination_shares("Aerodrome", "2026-11-01")
    assert last["hold"] == 1.0 and last["burn"] == 0.0 and last["verified"] is True, "the burn is measured, not a share"
