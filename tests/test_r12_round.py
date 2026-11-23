"""Round 12 (Jake 2026-11-19). No network."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402


# ================================================================= 4: Multicall3 refusals say their cause

@pytest.fixture(autouse=True)
def _r18_merged_basis_on(monkeypatch):
    """R18 (Jake 2026-11-24) switched the merged-basis restatement OFF for the build (config.VELO_MERGED restate).
    These tests check the mechanism's arithmetic, which stays in the code: they run it switched on."""
    monkeypatch.setitem(config.VELO_MERGED["Aerodrome"], "restate", True)


class _Resp:
    def __init__(self, status, text="", headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}


def _http_error(status, text="", headers=None):
    import requests
    return requests.HTTPError(f"{status} Error", response=_Resp(status, text, headers))


def _mc3(monkeypatch, behave, urls=("https://base-mainnet.g.alchemy.com/v2/SECRETKEY123", "https://mainnet.base.org")):
    """_batch_eth_calls with `behave(url, n_calls)` deciding each eth_call: return None to answer, or raise."""
    import check_offline_items as coi
    from eth_abi import decode, encode
    seen, naps = [], []

    def fake(url, method, params=None):
        if method == "eth_getCode":
            return {"result": "0x6080"}
        (calls,) = decode(["(address,bool,bytes)[]"], bytes.fromhex(params[0]["data"][10:]))
        seen.append((url.split("/")[2], len(calls)))
        behave(url, len(calls), seen)
        return {"result": "0x" + encode(["(bool,bytes)[]"], [[(True, (7).to_bytes(32, "big")) for _ in calls]]).hex()}
    monkeypatch.setattr(coi, "rpc", fake)
    monkeypatch.setattr(coi, "_rpcs_for", lambda chain: list(urls))
    monkeypatch.setattr(coi, "_nap", lambda s, what: naps.append(s))
    return coi, seen, naps


def test_4_a_rate_limit_is_waited_out_on_the_same_endpoint(monkeypatch, capsys):
    state = {"n": 0}

    def behave(url, n, seen):
        state["n"] += 1
        if "alchemy" in url and state["n"] <= 2:
            raise _http_error(429, '{"error":{"message":"Your app has exceeded its compute units per second capacity"}}',
                              {"Retry-After": "1"} if state["n"] == 1 else None)
    coi, seen, naps = _mc3(monkeypatch, behave)
    out = coi._batch_eth_calls("base", [("0x" + "11" * 20, "0x12345678")] * 5)
    assert all(int(v, 16) == 7 for v in out)
    assert {h for h, _ in seen} == {"base-mainnet.g.alchemy.com"}            # never fell to the public endpoint
    assert naps == [1.0, 2.0]                                                 # Retry-After, then the backoff
    assert "failed" not in capsys.readouterr().out


def test_4_a_batch_refused_for_its_size_is_read_in_halves_at_the_same_endpoint(monkeypatch):
    def behave(url, n, seen):
        if n > 50:
            raise _http_error(413, "Request Entity Too Large")
    coi, seen, _naps = _mc3(monkeypatch, behave)
    out = coi._batch_eth_calls("base", [("0x" + "11" * 20, "0x12345678")] * 200, chunk=200)
    assert len(out) == 200 and all(int(v, 16) == 7 for v in out)
    assert {h for h, _ in seen} == {"base-mainnet.g.alchemy.com"}
    assert sum(n for _h, n in seen if n <= 50) == 200                          # every call answered once


def test_4_any_other_refusal_names_its_status_and_never_the_key(monkeypatch, capsys):
    def behave(url, n, seen):
        if "alchemy" in url:
            raise _http_error(400, '{"error":{"message":"archive state unavailable for '
                                   'https://base-mainnet.g.alchemy.com/v2/SECRETKEY123"}}')
    coi, seen, _naps = _mc3(monkeypatch, behave)
    out = coi._batch_eth_calls("base", [("0x" + "11" * 20, "0x12345678")] * 3)
    assert all(int(v, 16) == 7 for v in out) and seen[-1][0] == "mainnet.base.org"
    said = capsys.readouterr().out
    assert "HTTP 400" in said and "archive state unavailable" in said and "SECRETKEY123" not in said


def test_4_a_rate_limit_that_never_lifts_moves_on_and_says_429(monkeypatch, capsys):
    def behave(url, n, seen):
        if "alchemy" in url:
            raise _http_error(429, "rate limited")
    coi, seen, naps = _mc3(monkeypatch, behave)
    out = coi._batch_eth_calls("base", [("0x" + "11" * 20, "0x12345678")] * 3)
    assert all(int(v, 16) == 7 for v in out)
    assert len(naps) == coi.MC3_RATE_TRIES - 1 and "HTTP 429" in capsys.readouterr().out


# ================================================================= Hyperliquid: an opening day that is a stored hole
BASIS = "derived:circulating_used[circulating_supply_first_party]"


def _circ(name, days, values):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": name, "metric": "circulating_supply_used",
                         "value": values, "tier": 1, "source": [BASIS] * len(days)})


class _R:
    def D(self, r, metric, col="q0"):
        return f"D_{col}"

    def C(self, r, col):
        return "C1"


def test_h_the_rate_is_annualised_over_the_windows_real_length(monkeypatch):
    """Opening 2027-01-22 − 90 = 2026-10-24 is a hole (10-24, 10-25 not stored): the window opens on 10-23, so it is
    91 days long and the annual rate divides by 91 — the same tokens over 90 days would overstate it by 91/90."""
    hl = config.PROJECT_BY_NAME["Hyperliquid"]
    monkeypatch.setattr(bw, "scoped_projects", lambda: [hl])
    monkeypatch.setitem(bw._SHIFT, "k", 0)
    asof = pd.Timestamp("2027-01-22")
    days = [d for d in pd.date_range("2026-10-13", asof) if str(d.date()) not in {"2026-10-24", "2026-10-25"}]
    g = {("Hyperliquid", "circulating_supply_used"): _circ("Hyperliquid", days, [5e8 + 900.0 * i for i in range(len(days))])}
    bw._circulating_change_views(g, asof)
    assert bw.CIRC_CHANGE_OPENING["Hyperliquid"]["days"] == 91
    f = bw._net_supply_circ(_R(), 5, hl, pct=True)
    assert "*365/91/" in f and "*365/90/" not in f
    assert "D_q0_covered_days=90" in f.replace(" ", "")                        # the 90 days of the window are all held
    suffix, note = bw._net_supply_circ_opening_flag(hl)
    assert "91" in suffix and "2026-10-23" in note
    # an earlier quarter (the trajectory's shifted windows) is not touched by the Q0 opening
    monkeypatch.setitem(bw._SHIFT, "k", 1)
    assert "*365/90/" in bw._net_supply_circ(_R(), 5, hl, pct=True) and bw._net_supply_circ_opening_flag(hl) is None


def test_h_a_stored_opening_day_is_ninety_days_and_carries_no_flag(monkeypatch):
    hl = config.PROJECT_BY_NAME["Hyperliquid"]
    monkeypatch.setattr(bw, "scoped_projects", lambda: [hl])
    monkeypatch.setitem(bw._SHIFT, "k", 0)
    asof = pd.Timestamp("2027-01-11")
    days = list(pd.date_range("2026-10-13", asof))
    g = {("Hyperliquid", "circulating_supply_used"): _circ("Hyperliquid", days, [5e8 + 900.0 * i for i in range(len(days))])}
    bw._circulating_change_views(g, asof)
    assert "Hyperliquid" not in bw.CIRC_CHANGE_OPENING and bw.CIRC_CHANGE_GAPS["Hyperliquid"] == []
    assert "*365/90/" in bw._net_supply_circ(_R(), 5, hl, pct=True)
    assert bw._net_supply_circ_opening_flag(hl) is None


def test_h_no_reading_at_all_before_the_opening_is_still_no_value(monkeypatch):
    """Unknown is never bridged from nothing: with no stored reading on or before the opening the cell stays n/a."""
    hl = config.PROJECT_BY_NAME["Hyperliquid"]
    monkeypatch.setattr(bw, "scoped_projects", lambda: [hl])
    asof = pd.Timestamp("2027-01-05")                        # opening 2026-10-07, before the first stored day
    days = list(pd.date_range("2026-10-13", asof))
    g = {("Hyperliquid", "circulating_supply_used"): _circ("Hyperliquid", days, [5e8 + 900.0 * i for i in range(len(days))])}
    bw._circulating_change_views(g, asof)
    assert "Hyperliquid" not in bw.CIRC_CHANGE_OPENING
    assert any("the opening" in why for _d, why in bw.CIRC_CHANGE_GAPS["Hyperliquid"])


# ================================================================= Velodrome's pre-merger emissions in dilution
def _rows(metric, days, values, source):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": "Aerodrome", "metric": metric,
                         "value": [float(v) for v in values], "tier": 2, "source": source})


def _velo_groups(skip=()):
    """Daily VELO reads 2026-10-06..10-21 with flips on Thursday 10-08 and 10-15 (seen at the next day's first block),
    and Aerodrome's legacy weekly Minter rows dated those flips."""
    days = [d for d in pd.date_range("2026-10-06", "2026-10-21") if str(d.date()) not in skip]
    p0 = int(pd.Timestamp("2026-10-01").timestamp())
    tot, per, t = [], [], 2_631_000_000.0
    for d in days:
        flips = sum(1 for f in ("2026-10-08", "2026-10-15") if d > pd.Timestamp(f))
        tot.append(t + 18_000.0 * (d - days[0]).days + 8_000_000.0 * flips)
        per.append(p0 + 7 * 86_400 * flips)
    g = {("Aerodrome", "velo_total_supply_tokens"): _rows("velo_total_supply_tokens", days, tot, "velo_history:VELO.totalSupply:archive"),
         ("Aerodrome", "velo_tail_emission_rate_bps"): _rows("velo_tail_emission_rate_bps", days, [30] * len(days), "velo_history:Minter.tailEmissionRate:archive"),
         ("Aerodrome", "velo_minter_active_period"): _rows("velo_minter_active_period", days, per, "velo_history:Minter.activePeriod:archive"),
         ("Aerodrome", "velo_minter_weekly_tokens"): _rows("velo_minter_weekly_tokens", days, [5_950_167.0] * len(days), "velo_history:Minter.weekly:archive"),
         ("Aerodrome", "gross_issuance_tokens"): pd.concat([
             _rows("gross_issuance_tokens", ["2026-10-01", "2026-10-08", "2026-10-15"], [4_184_974, 4_195_056, 4_204_997],
                   "chain:base:minter[tail@21bps]"),
             _rows("gross_issuance_tokens", ["2026-10-28"], [332_205], "aero_v3:minter.emissionsMinted:delta")],
             ignore_index=True)}
    return g, dict(zip(days, tot))


@pytest.fixture
def _aero(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Aerodrome"]])


def test_v_each_flip_adds_velodromes_gauge_emission_at_the_contract_ratio(_aero):
    """A flip's gauge emission is the supply read before it x the tail rate (the Minter's own formula); x 0.044 it
    joins Aerodrome's weekly row of the same flip, to the token, under the declared merged point."""
    g, tot = _velo_groups()
    bw._velo_merged_views(g)
    gi = g[("Aerodrome", "gross_issuance_tokens")].set_index("date")
    for flip, aero in (("2026-10-08", 4_195_056), ("2026-10-15", 4_204_997)):
        em = tot[pd.Timestamp(flip)] * 30 / 10_000                     # the reading at the flip day's first block
        assert gi.loc[pd.Timestamp(flip), "value"] == pytest.approx(aero + em * 0.044, abs=1e-3)
        assert gi.loc[pd.Timestamp(flip), "source"].startswith("velo_merged:chain:base:minter[tail@21bps]")
    assert gi.loc[pd.Timestamp("2026-10-01"), "value"] == 4_184_974     # before the first VELO flip: untouched
    assert gi.loc[pd.Timestamp("2026-10-28"), "value"] == 332_205       # the new Minter's rows are never touched
    from fetch.base import _measuring_point
    declared = {_measuring_point(x) for x in
                config.PROJECT_BY_NAME["Aerodrome"]["series_handover"]["gross_issuance_tokens"]["ordered_points"]}
    assert {_measuring_point(x) for x in gi["source"]} <= declared
    # the merged rows are still re-dated to the week they paid, with the legacy rows
    assert any(str(gi.loc[pd.Timestamp("2026-10-15"), "source"]).startswith(x) for x in config.EPOCH_PAID_AT["Aerodrome"]["sources"])


def test_v_the_checked_flip_reproduces_the_minters_own_event():
    c = config.VELO_MERGED["Aerodrome"]["gauge"]["checked"]
    assert c["supply_before"] * c["rate_bps"] / 10_000 == pytest.approx(c["mint_event_weekly"], abs=1e-3)


def test_v_two_flips_inside_one_hole_are_not_attributed(_aero):
    """Readings missing from 10-08 to 10-16: two flips happened between 10-07 and 10-17 — neither week gets a
    Velodrome figure, so neither Aerodrome row is restated as merged: both hold NO value (unknown, never Aerodrome's
    alone inside the merged span)."""
    skip = {str(d.date()) for d in pd.date_range("2026-10-08", "2026-10-16")}
    g, _tot = _velo_groups(skip=skip)
    bw._velo_merged_views(g)
    assert ("Aerodrome", "velo_gauge_emissions_tokens") not in g
    gi = g[("Aerodrome", "gross_issuance_tokens")].set_index("date")
    assert pd.Timestamp("2026-10-08") in gi.index and gi.loc[pd.Timestamp("2026-10-08"), "value"] == 4_195_056
    assert gi["source"].astype(str).str.startswith("velo_merged").sum() == 0


def test_v_outside_the_tail_phase_no_emission_is_modelled(_aero):
    g, _tot = _velo_groups()
    g[("Aerodrome", "velo_minter_weekly_tokens")] = g[("Aerodrome", "velo_minter_weekly_tokens")].assign(value=7_000_000.0)
    bw._velo_merged_views(g)
    assert ("Aerodrome", "velo_gauge_emissions_tokens") not in g


def test_v_the_statement_balances_gauge_plus_other_mints_is_the_whole_rise(_aero):
    g, tot = _velo_groups()
    bw._velo_merged_views(g)
    gauge = g[("Aerodrome", "velo_gauge_emissions_tokens")]["value"].sum() * 0.044
    other = g[("Aerodrome", "velo_merged_issuance_tokens")]["value"].sum()
    rise = (tot[pd.Timestamp("2026-10-21")] - tot[pd.Timestamp("2026-10-06")]) * 0.044
    assert gauge + other == pytest.approx(rise, abs=1e-3)
    assert 0 < other < 0.05 * gauge


def test_v_reference_checks_of_the_emission_stay_on_aerodrome_alone(_aero):
    import references
    g, _tot = _velo_groups()
    bw._velo_merged_views(g)
    s = sorted(references._series(g, "Aerodrome", "gross_issuance_tokens")["value"])
    assert s == [332_205, 4_184_974, 4_195_056, 4_204_997]                    # no row carries Velodrome's emission


# ================================================================= Velodrome's issuer-linked wallets
def test_w_the_issuer_set_is_tagged_and_adds_up_to_the_merger_read():
    w = config.VELO_ISSUER_WALLETS
    assert len(w) == 2 and all(v["tag"] == "INFERRED" and v["evidence"] for v in w.values())
    m = config.VELO_ISSUER_AT_MERGER
    assert sum(v["liquid_at_merger"] for v in w.values()) == pytest.approx(m["liquid"], abs=0.01)
    assert sum(v["locked_at_merger"] for v in w.values()) == pytest.approx(m["locked"], abs=0.02)
    r = config.VELO_MERGED["Aerodrome"]["ratio"]
    assert m["liquid"] * r == pytest.approx(m["liquid_x_ratio"], abs=0.01)
    assert m["locked"] * r == pytest.approx(m["locked_x_ratio"], abs=0.01)
    e = config.VELO_MERGED["Aerodrome"]["entrypoint"]
    assert e["paid"] + e["remaining"] == pytest.approx(config.VELO_MERGED["Aerodrome"]["allocation"], abs=0.01)


def test_w_the_liquid_read_is_every_wallet_at_one_block_or_no_row():
    from fetch import velo_history as vh
    spec = dict(config.VELO_MERGED["Aerodrome"])
    wallets = [x.lower() for x in spec["issuer_wallets"]]
    stamp = lambda b: vh.BEDROCK_TS + (b - vh.BEDROCK_BLOCK) * 2               # noqa: E731
    blocks = set()

    def call(to, sig, arg, block, fail=None):
        blocks.add(block)
        v1 = config.VELO_MERGED["Aerodrome"]["v1"]            # R13: the five v1 reads, answered so they add up
        if sig == "locked(uint256)":
            return int(1.72e27)
        if to == v1["token"]:
            return int(2.0e27) if sig == "totalSupply()" else int({v1["escrow"]: 1.75e27}.get(arg, 1e26))
        if sig == "balanceOf(address)" and arg and arg.lower() in wallets:
            if fail and arg.lower() == fail:
                raise RuntimeError("no archive state")
            return int((4_000_000 if arg.lower() == wallets[0] else 5_000_000) * 1e18)
        return int({"tailEmissionRate()": 30, "activePeriod()": 1_792_022_400}.get(sig, 2.6e27))
    rows, _why = vh.history("Aerodrome", spec, {}, first_day="2026-10-21", call=call, block_ts=stamp)
    got = {r["metric"]: r["value"] for r in rows}
    assert got["velo_issuer_liquid_tokens"] == pytest.approx(9_000_000.0) and len(blocks) == 1
    assert got["velo_tail_emission_rate_bps"] == 30 and got["velo_minter_active_period"] == 1_792_022_400
    rows, why = vh.history("Aerodrome", spec, {}, first_day="2026-10-21", block_ts=stamp,
                           call=lambda to, sig, arg, block: call(to, sig, arg, block, fail=wallets[1]))
    assert rows == [] and "not readable" in why                               # one wallet unread: the day keeps no row


def test_w_the_issuer_liquid_leg_joins_the_holding_before_the_merger_only(_aero):
    days = list(pd.date_range("2026-10-19", "2026-10-21"))
    g = {("Aerodrome", "noncirculating_holding_tokens"): pd.concat([
            _rows("noncirculating_holding_tokens", days, [40_000_000.0] * 3,
                  "chain:sum(base:noncirc_foundation_multisig+base:noncirc_public_goods_fund)"),
            _rows("noncirculating_holding_tokens", ["2026-10-22"], [37_000_000.0], "aero_v3:supply_once")],
            ignore_index=True),
         ("Aerodrome", "velo_issuer_liquid_tokens"): _rows("velo_issuer_liquid_tokens", days, [9_100_720.40] * 3,
                                                           "velo_history:VELO.balanceOf(2 issuer-linked wallets):archive")}
    bw._velo_merged_views(g)
    h = g[("Aerodrome", "noncirculating_holding_tokens")].set_index("date")
    assert h.loc[pd.Timestamp("2026-10-21"), "value"] == pytest.approx(40_000_000.0 + 9_100_720.40 * 0.044, abs=1e-3)
    assert h.loc[pd.Timestamp("2026-10-22"), "value"] == 37_000_000.0


def test_v_the_primary_check_of_aerodromes_emission_stays_like_for_like(monkeypatch):
    """The Mint events are Aerodrome's Minter's: Velodrome's part of a merged row is taken off ours first. Without
    that, a merged quarter reads BROKEN against events that never covered Velodrome."""
    import coverage_audit as ca
    c = next(x for x in config.TRAJECTORY_PRIMARY_CHECKS if x["project"] == "Aerodrome")
    asof = pd.Timestamp("2026-10-13")
    hi, lo = asof, asof - pd.Timedelta(days=90)
    prim = sum(e[1] for e in c["events"] if lo < pd.Timestamp(e[0]) <= hi)
    key = ("Aerodrome", c["tab"], c["hid"])
    rows = [{"project": "Aerodrome", "tab": c["tab"], "hid": c["hid"], "Q0": prim + 4_300_000.0}]
    monkeypatch.setattr(ca, "PRIMARY_LESS", {})
    assert ca.trajectory_primary_checks(rows, asof, quarters=("Q0",))[key][0] == "BROKEN"
    monkeypatch.setattr(ca, "PRIMARY_LESS", {key: [(asof - pd.Timedelta(days=7), 4_300_000.0),
                                                    (asof - pd.Timedelta(days=200), 9e9)]})   # outside the window
    status, detail = ca.trajectory_primary_checks(rows, asof, quarters=("Q0",))[key]
    assert status == "VERIFIED" and "4,300,000.00 outside the primary events' scope" in detail


def test_v_the_velodrome_part_handed_to_the_check_is_merged_less_aerodrome_alone(_aero):
    import coverage_audit as ca
    g, tot = _velo_groups()
    bw._velo_merged_views(g)
    bw._epoch_paid_views(g, pd.Timestamp("2026-11-19"))
    bw._velo_primary_less(g)
    less = dict(ca.PRIMARY_LESS[("Aerodrome", "Master", "master_gross_issuance_q0_tokens")])
    em = tot[pd.Timestamp("2026-10-15")] * 30 / 10_000 * 0.044
    assert less[pd.Timestamp("2026-10-22")] == pytest.approx(em, abs=1e-3)      # the 10-15 flip, re-dated to its week
    assert len(less) == 2
