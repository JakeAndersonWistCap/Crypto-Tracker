"""Round 11 (Jake 2026-11-18/19). No network."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402

BASIS = "derived:circulating_used[circulating_supply_first_party]"


def _circ(name, days, values, sources=None):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": name, "metric": "circulating_supply_used",
                         "value": values, "tier": 1, "source": sources or [BASIS] * len(days)})


# ================================================================= 1: net supply change across a hole
def test_1_the_changes_telescope_to_closing_minus_opening_across_holes(monkeypatch):
    """Hyperliquid on Jake's store: 38 of 49 days held. With every reading on one basis, the changes across the holes
    are multi-day changes and their sum over the window is closing − opening, to the token."""
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Hyperliquid"]])
    asof = pd.Timestamp("2026-12-29")
    days = [d for d in pd.date_range("2026-09-30", asof)
            if str(d.date()) not in {"2026-10-02", "2026-10-03", "2026-10-04", "2026-10-17", "2026-11-07", "2026-11-08"}]
    vals = [539_000_000.0 + 7_000.0 * i + (i % 5) * 311.0 for i in range(len(days))]
    g = {("Hyperliquid", "circulating_supply_used"): _circ("Hyperliquid", days, vals)}
    bw._circulating_change_views(g, asof)
    ch = g[("Hyperliquid", "circulating_change_tokens")]
    assert ch["value"].sum() == pytest.approx(vals[-1] - vals[0], abs=1e-6)        # opening 09-30, closing 12-29
    assert bw.CIRC_CHANGE_GAPS["Hyperliquid"] == []
    from fetch.validate import _span_days
    cov = {d - pd.Timedelta(days=i) for d, s in zip(ch["date"], ch["source"]) for i in range(_span_days(s))}
    assert len([d for d in cov if asof - pd.Timedelta(days=90) < d <= asof]) == 90   # every day of the window is held


def test_1_a_hole_on_the_opening_day_opens_the_window_on_the_reading_before_it(monkeypatch):
    """R12 (Jake 2026-11-19; was R11.1: the straddling change was left out and the as-of read n/a, its gap naming the
    two readings either side). An as-of whose opening day (as-of − 90) falls in a hole: the window opens on the last
    stored reading before it — the change that straddles the opening is kept whole, the sum is closing − that reading,
    and the window's real length is recorded for the annual rate."""
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Hyperliquid"]])
    asof = pd.Timestamp("2026-12-31")                       # opening 2026-10-02: a hole (10-02..10-04)
    days = [d for d in pd.date_range("2026-09-30", asof) if str(d.date()) not in {"2026-10-02", "2026-10-03", "2026-10-04"}]
    vals = [5e8 + 1000.0 * i + (i % 3) * 7.0 for i in range(len(days))]
    g = {("Hyperliquid", "circulating_supply_used"): _circ("Hyperliquid", days, vals)}
    bw._circulating_change_views(g, asof)
    ch = g[("Hyperliquid", "circulating_change_tokens")]
    assert pd.Timestamp("2026-10-05") in set(ch["date"])
    assert bw.CIRC_CHANGE_GAPS["Hyperliquid"] == []
    o = bw.CIRC_CHANGE_OPENING["Hyperliquid"]
    assert o["used"] == pd.Timestamp("2026-10-01") and o["next"] == pd.Timestamp("2026-10-05") and o["days"] == 91
    q0 = ch[(ch["date"] > asof - pd.Timedelta(days=90)) & (ch["date"] <= asof)]["value"].sum()
    by_day = dict(zip(days, vals))
    assert q0 == pytest.approx(by_day[asof] - by_day[pd.Timestamp("2026-10-01")], abs=1e-6)


def test_1_a_change_across_a_hole_on_two_bases_is_still_no_change(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Hyperliquid"]])
    g = {("Hyperliquid", "circulating_supply_used"): _circ(
        "Hyperliquid", ["2026-10-01", "2026-10-02", "2026-10-06", "2026-10-07"], [100.0, 101.0, 300.0, 301.0],
        [BASIS, BASIS, "derived:circulating_used[other]", "derived:circulating_used[other]"])}
    bw._circulating_change_views(g)
    ch = g[("Hyperliquid", "circulating_change_tokens")]
    assert ch["value"].tolist() == [1.0, 1.0] and "basis changes" in bw.CIRC_CHANGE_GAPS["Hyperliquid"][0][1]


# ================================================================= 2c: Aerodrome before the merger, merged basis
def _rows(metric, days, values, source):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": "Aerodrome", "metric": metric,
                         "value": [float(v) for v in values], "tier": 2, "source": source})


def _aero_groups(velo_days, velo_total):
    days = list(pd.date_range("2026-10-15", "2026-10-21"))
    g = {("Aerodrome", "total_supply"): pd.concat([
            _rows("total_supply", days, [2_002_000_000 + 50_000 * i for i in range(7)], "chain:base:token:archive"),
            _rows("total_supply", ["2026-10-22"], [2_119_380_000.0], "aero_v3:supply_once")], ignore_index=True),
         ("Aerodrome", "locked_tokens"): _rows("locked_tokens", days, [1_000_000_000.0] * 7, "chain:base:ve")}
    if velo_days:
        g[("Aerodrome", "velo_total_supply_tokens")] = _rows(
            "velo_total_supply_tokens", velo_days, velo_total, "velo_history:VELO.totalSupply:archive")
        g[("Aerodrome", "velo_locked_tokens")] = _rows(
            "velo_locked_tokens", velo_days, [1_300_000_000.0] * len(velo_days),
            "velo_history:VELO.balanceOf(VotingEscrow):archive")
    return g


@pytest.fixture
def _aero(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Aerodrome"]])


def test_2c_no_velo_history_moves_nothing(_aero):
    g = _aero_groups([], [])
    before = {k: v.copy() for k, v in g.items()}
    bw._velo_merged_views(g)
    assert set(g) == set(before)
    for k in before:
        pd.testing.assert_frame_equal(g[k], before[k])


def test_2c_each_day_is_aerodrome_plus_velo_at_the_contract_ratio(_aero):
    """Value: AERO + VELO x 0.044 on each day VELO is stored, to the token; the point is the declared merged one; a day
    inside the span with no VELO reading holds no value; a day before the first VELO reading is untouched."""
    vdays = ["2026-10-17", "2026-10-18", "2026-10-20", "2026-10-21"]          # 10-19 not read; 10-15, 10-16 before
    vtot = [2_640_000_000.0, 2_641_000_000.0, 2_645_000_000.0, 2_648_000_000.0]
    g = _aero_groups(vdays, vtot)
    aero = g[("Aerodrome", "total_supply")].set_index("date")["value"].copy()
    bw._velo_merged_views(g)
    t = g[("Aerodrome", "total_supply")].set_index("date")
    for d, v in zip(vdays, vtot):
        assert t.loc[pd.Timestamp(d), "value"] == pytest.approx(aero[pd.Timestamp(d)] + v * 0.044, abs=1e-3)
        assert t.loc[pd.Timestamp(d), "source"] == "velo_merged:chain:base:token"
    assert pd.Timestamp("2026-10-19") not in t.index                           # unknown, never the unmerged figure
    for d in ("2026-10-15", "2026-10-16"):
        assert t.loc[pd.Timestamp(d), "value"] == aero[pd.Timestamp(d)]
        assert t.loc[pd.Timestamp(d), "source"] == "chain:base:token:archive"
    assert t.loc[pd.Timestamp("2026-10-22"), "value"] == 2_119_380_000.0       # the new read is never touched
    lk = g[("Aerodrome", "locked_tokens")].set_index("date")
    assert lk.loc[pd.Timestamp("2026-10-21"), "value"] == pytest.approx(1e9 + 1.3e9 * 0.044)
    # every point the view can write is a declared one
    from fetch.base import _measuring_point
    for m in ("total_supply", "locked_tokens"):
        declared = {_measuring_point(x) for x in config.PROJECT_BY_NAME["Aerodrome"]["series_handover"][m]["ordered_points"]}
        assert {_measuring_point(s) for s in g[("Aerodrome", m)]["source"]} <= declared


def test_2c_the_velodrome_leg_never_exceeds_the_allocation(_aero):
    g = _aero_groups(["2026-10-21"], [2_700_000_000.0])                         # x 0.044 = 118.8M > the 117.0M minted
    aero = float(g[("Aerodrome", "total_supply")].set_index("date")["value"][pd.Timestamp("2026-10-21")])
    bw._velo_merged_views(g)
    t = g[("Aerodrome", "total_supply")].set_index("date")
    assert t.loc[pd.Timestamp("2026-10-21"), "value"] == pytest.approx(aero + 117_000_000.0)


def test_2c_the_merger_step_is_only_what_was_not_already_counted(_aero):
    """Legacy basis: the step is the whole allocation (117.0M). Merged basis: only the headroom above VELO x 0.044."""
    g = _aero_groups([], [])
    bw._handover_step_views(g)
    legacy = float(g[("Aerodrome", "aero_merger_issuance_tokens")]["value"].iloc[0])
    assert legacy == pytest.approx(2_119_380_000.0 - 2_002_300_000.0)
    g = _aero_groups(["2026-10-20", "2026-10-21"], [2_647_000_000.0, 2_648_026_538.38])
    bw._velo_merged_views(g)
    bw._handover_step_views(g)
    merged = float(g[("Aerodrome", "aero_merger_issuance_tokens")]["value"].iloc[0])
    assert merged == pytest.approx(legacy - 2_648_026_538.38 * 0.044, abs=1e-3)
    assert merged < 0.01 * legacy


def test_2c_velodromes_emissions_telescope_across_a_hole(_aero):
    vdays = ["2026-10-17", "2026-10-18", "2026-10-20", "2026-10-21"]
    vtot = [2_640_000_000.0, 2_641_000_000.0, 2_645_000_000.0, 2_648_000_000.0]
    g = _aero_groups(vdays, vtot)
    bw._velo_merged_views(g)
    e = g[("Aerodrome", "velo_merged_issuance_tokens")]
    assert e["value"].sum() == pytest.approx((vtot[-1] - vtot[0]) * 0.044, abs=1e-3)
    from fetch.validate import _span_days
    assert _span_days(e.set_index("date").loc[pd.Timestamp("2026-10-20"), "source"]) == 2


def test_2c_the_allocation_is_velodromes_supply_converted_within_half_a_percent():
    """The evidence behind 'existing supply converted': VELO's supply at the merger block x the contract ratio is the
    allocation to within 0.5% (the headroom), and the headroom is what the config says it is."""
    m = config.VELO_MERGED["Aerodrome"]
    conv = m["at_merger"]["velo_total"] * m["ratio"]
    assert conv == pytest.approx(m["at_merger"]["x_ratio"], abs=0.01)
    assert 0 <= m["allocation"] - conv <= 0.005 * m["allocation"]
    assert m["allocation"] - conv == pytest.approx(m["at_merger"]["headroom"], abs=0.01)


def test_2c_the_reader_takes_each_day_at_its_first_block_and_stops_on_a_failed_read():
    from fetch import velo_history as vh
    spec = dict(config.VELO_MERGED["Aerodrome"])
    ts = int(pd.Timestamp("2026-10-21", tz="UTC").timestamp())
    blk = vh.block_at(ts)
    assert vh.BEDROCK_TS + (blk - vh.BEDROCK_BLOCK) * 2 in (ts, ts + 1)        # the first block at or after midnight
    assert vh.BEDROCK_TS + (blk - 1 - vh.BEDROCK_BLOCK) * 2 < ts
    calls = []

    def call(to, sig, arg, block):
        if block < vh.block_at(int(pd.Timestamp("2026-10-19", tz="UTC").timestamp())):
            raise RuntimeError("no archive state")
        calls.append((sig, block))
        return int({"totalSupply()": 2.6e27, "balanceOf(address)": 1.3e27, "supply()": 1.33e27,
                    "permanentLockBalance()": 1.1e27}.get(sig, 9e26))
    stamp = lambda b: vh.BEDROCK_TS + (b - vh.BEDROCK_BLOCK) * 2               # noqa: E731
    rows, why = vh.history("Aerodrome", spec, {m: {"2026-10-20"} for m in vh.METRICS}, first_day="2026-10-10",
                           call=call, block_ts=stamp)
    got = {(str(r["date"])[:10], r["metric"]): r["value"] for r in rows}
    assert {d for d, _ in got} == {"2026-10-21", "2026-10-19"}                 # 10-20 is stored: no call, no row
    assert got[("2026-10-21", "velo_total_supply_tokens")] == pytest.approx(2.6e9)
    assert "2026-10-18" in why and "not readable" in why                        # unknown, never 0: no row is written
    # a block whose timestamp is not the day's first is refused before any value is used
    rows, why = vh.history("Aerodrome", spec, {}, first_day="2026-10-21", call=call, block_ts=lambda b: stamp(b) - 2)
    assert rows == [] and "block rule" in why


def test_2c_a_reference_published_before_the_merger_is_checked_against_aerodrome_alone(_aero):
    """Like for like: the reference checks read the series as stored before the merger and the new read after it."""
    import references
    g = _aero_groups(["2026-10-20", "2026-10-21"], [2_647_000_000.0, 2_648_026_538.38])
    aero = g[("Aerodrome", "total_supply")].set_index("date")["value"].copy()
    bw._velo_merged_views(g)
    s = references._series(g, "Aerodrome", "total_supply").set_index("date")["value"]
    assert s[pd.Timestamp("2026-10-21")] == aero[pd.Timestamp("2026-10-21")]          # not + VELO x 0.044
    assert s[pd.Timestamp("2026-10-22")] == 2_119_380_000.0
    assert len(s) == len(aero)
    merged = g[("Aerodrome", "total_supply")].set_index("date")["value"]
    assert merged[pd.Timestamp("2026-10-21")] > aero[pd.Timestamp("2026-10-21")] + 116e6
