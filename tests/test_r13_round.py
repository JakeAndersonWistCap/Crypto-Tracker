"""Round 13 (Jake 2026-11-19). No network."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402


def _rows(metric, days, values, source):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": "Aerodrome", "metric": metric,
                         "value": [float(v) for v in values], "tier": 2, "source": source})


@pytest.fixture
def _aero(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Aerodrome"]])


DAYS = list(pd.date_range("2026-06-01", "2026-06-04"))


def _groups(v2, v1_days=DAYS, liquid=16_000_000.0, locked=31_600_000.0):
    g = {("Aerodrome", "total_supply"): _rows("total_supply", DAYS, [1_990_000_000.0] * 4, "chain:base:token:archive"),
         ("Aerodrome", "locked_tokens"): _rows("locked_tokens", DAYS, [1_000_000_000.0] * 4, "chain:base:ve"),
         ("Aerodrome", "velo_total_supply_tokens"): _rows("velo_total_supply_tokens", DAYS, v2, "velo_history:VELO.totalSupply:archive"),
         ("Aerodrome", "velo_locked_tokens"): _rows("velo_locked_tokens", DAYS, [1_300_000_000.0] * 4, "velo_history:x:archive")}
    if v1_days:
        g[("Aerodrome", "velo_v1_liquid_tokens")] = _rows("velo_v1_liquid_tokens", v1_days, [liquid] * len(v1_days), "velo_history:v1liq:archive")
        g[("Aerodrome", "velo_v1_locked_tokens")] = _rows("velo_v1_locked_tokens", v1_days, [locked] * len(v1_days), "velo_history:v1lock:archive")
        g[("Aerodrome", "velo_v1_distributor_tokens")] = _rows("velo_v1_distributor_tokens", v1_days, [239_562_809.33] * len(v1_days), "velo_history:v1dist:archive")
    return g


# ================================================================= B3: VELO v1's certain part in the pre-merger series
def test_b3_the_certain_part_joins_at_the_contract_ratio_and_the_distributor_stays_out(_aero):
    g = _groups([2_480_000_000.0] * 4)
    bw._velo_merged_views(g)
    t = g[("Aerodrome", "total_supply")].set_index("date")
    want = 1_990_000_000.0 + (2_480_000_000.0 + 16_000_000.0 + 31_600_000.0) * 0.044
    assert t.loc[DAYS[0], "value"] == pytest.approx(want, abs=1e-3)              # the 239.56M of the distributor is not in it
    assert t.loc[DAYS[0], "source"] == "velo_merged_v1:chain:base:token"
    lk = g[("Aerodrome", "locked_tokens")].set_index("date")
    assert lk.loc[DAYS[0], "value"] == pytest.approx(1e9 + (1.3e9 + 31_600_000.0) * 0.044, abs=1e-3)   # v1 veNFTs only
    from fetch.base import _measuring_point
    for m in ("total_supply", "locked_tokens"):
        declared = {_measuring_point(x) for x in config.PROJECT_BY_NAME["Aerodrome"]["series_handover"][m]["ordered_points"]}
        assert {_measuring_point(x) for x in g[("Aerodrome", m)]["source"]} <= declared


def test_b3_a_day_without_the_v1_reads_stays_on_velo_v2_under_its_own_point(_aero):
    g = _groups([2_480_000_000.0] * 4, v1_days=DAYS[2:])
    bw._velo_merged_views(g)
    t = g[("Aerodrome", "total_supply")].set_index("date")
    assert t.loc[DAYS[0], "source"] == "velo_merged:chain:base:token"
    assert t.loc[DAYS[0], "value"] == pytest.approx(1_990_000_000.0 + 2_480_000_000.0 * 0.044, abs=1e-3)
    assert t.loc[DAYS[3], "source"] == "velo_merged_v1:chain:base:token"
    assert t.loc[DAYS[3], "value"] - t.loc[DAYS[0], "value"] == pytest.approx(47_600_000.0 * 0.044, abs=1e-3)


def test_b3_the_allocation_still_caps_the_velodrome_leg(_aero):
    g = _groups([2_640_000_000.0] * 4)                       # (2,640.0M + 47.6M) x 0.044 = 118.25M > 117.0M
    bw._velo_merged_views(g)
    t = g[("Aerodrome", "total_supply")].set_index("date")
    assert t.loc[DAYS[0], "value"] == pytest.approx(1_990_000_000.0 + 117_000_000.0)


def test_b3_a_v1_to_v2_conversion_is_no_mint_of_the_merged_supply(_aero):
    """5,000 v1 converted on day 2: v2 rises by 5,000 and v1 liquid falls by 5,000 — the statement's other-mints line
    holds no row for it (it read the v2 rise alone before)."""
    g = _groups([2_480_000_000.0, 2_480_005_000.0, 2_480_005_000.0, 2_480_005_000.0])
    g[("Aerodrome", "velo_v1_liquid_tokens")] = _rows("velo_v1_liquid_tokens", DAYS, [16_000_000.0, 15_995_000.0, 15_995_000.0, 15_995_000.0], "velo_history:v1liq:archive")
    bw._velo_merged_views(g)
    assert ("Aerodrome", "velo_merged_issuance_tokens") not in g
    t = g[("Aerodrome", "total_supply")].set_index("date")["value"]
    assert t[DAYS[1]] == pytest.approx(t[DAYS[0]], abs=1e-3)


def test_b3_the_recorded_merger_read_adds_up():
    m = config.VELO_MERGED["Aerodrome"]["v1"]["at_merger"]
    assert m["escrow"] - m["sink_lock"] == pytest.approx(m["locked"], abs=1e-6)
    assert m["total"] - m["escrow"] - m["distributor"] - m["sink_gauge"] == pytest.approx(m["liquid"], abs=1e-6)
    assert (m["locked"] + m["liquid"]) * 0.044 == pytest.approx(config.VELO_V1["x_ratio"]["certain"], abs=0.01)
    assert config.VELO_V1["in_merged_series"] is True


def test_b3_the_reader_makes_the_parts_from_five_reads_and_refuses_a_read_that_does_not_add_up():
    from fetch import velo_history as vh
    v1 = config.VELO_MERGED["Aerodrome"]["v1"]
    m = v1["at_merger"]

    def call(to, sig, arg, blk, gauge=m["sink_gauge"]):
        if sig == "totalSupply()":
            return int(m["total"] * 1e18)
        if sig == "locked(uint256)":
            assert int(arg, 16) == v1["sink_lock_id"]
            return int(m["sink_lock"] * 1e18)
        return int({v1["escrow"]: m["escrow"], v1["distributor"]: m["distributor"], v1["sink_gauge"]: gauge}[arg] * 1e18)
    got = vh.v1_parts(call, v1, 158_514_211)
    assert got["velo_v1_locked_tokens"] == pytest.approx(m["locked"], abs=1e-3)
    assert got["velo_v1_liquid_tokens"] == pytest.approx(m["liquid"], abs=1e-3)
    assert got["velo_v1_distributor_tokens"] == pytest.approx(m["distributor"], abs=1e-3)
    with pytest.raises(RuntimeError):
        vh.v1_parts(lambda to, sig, arg, blk: call(to, sig, arg, blk, gauge=5e9), v1, 158_514_211)
