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

@pytest.fixture(autouse=True)
def _r18_merged_basis_on(monkeypatch):
    """R18 (Jake 2026-11-24) switched the merged-basis restatement OFF for the build (config.VELO_MERGED restate).
    These tests check the mechanism's arithmetic, which stays in the code: they run it switched on."""
    monkeypatch.setitem(config.VELO_MERGED["Aerodrome"], "restate", True)


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


# ================================================================= 4: Base archive reads — the pool and the pace
def test_4_publicnode_is_not_asked_for_a_past_block_on_base(monkeypatch):
    import check_offline_items as coi
    monkeypatch.setenv("ALCHEMY_API_KEY", "SECRETKEY123")
    monkeypatch.delenv("BASE_RPC_URL", raising=False)
    host = lambda u: u.split("/")[2]                                           # noqa: E731
    head, past = [host(u) for u in coi._rpcs_for("base")], [host(u) for u in coi._rpcs_archive("base")]
    assert "base-rpc.publicnode.com" in head and "base-rpc.publicnode.com" not in past
    assert past[0] == "base-mainnet.g.alchemy.com" and "mainnet.base.org" in past
    asked = []

    def fake(url, method, params=None):
        asked.append(host(url))
        if "alchemy" in url:
            raise ValueError("refused")
        return {"result": "0x1"}
    monkeypatch.setattr(coi, "rpc", fake)
    monkeypatch.setattr(coi, "_nap", lambda s, what: None)
    assert coi._rpc_retry("base", "eth_getBlockReceipts", ["0x3000000"]) == "0x1"
    assert coi._rpc_retry("base", "eth_call", [{"to": "0x0", "data": "0x"}, "0x3000000"]) == "0x1"
    assert "base-rpc.publicnode.com" not in asked
    asked.clear()
    assert coi._rpc_retry("base", "eth_call", [{"to": "0x0", "data": "0x"}, "latest"]) == "0x1"
    assert asked[:2] == ["base-mainnet.g.alchemy.com", "base-rpc.publicnode.com"]   # at the head it is still asked


def test_4_what_counts_as_a_past_block_read():
    import check_offline_items as coi
    assert coi.is_archive_request("eth_getBlockReceipts", ["0x10"])
    assert coi.is_archive_request("eth_getBlockByNumber", ["0x10", False])
    assert not coi.is_archive_request("eth_getBlockByNumber", ["latest", False])
    assert coi.is_archive_request("eth_call", [{"to": "0x0"}, "0x10"]) and not coi.is_archive_request("eth_call", [{"to": "0x0"}, "latest"])
    assert not coi.is_archive_request("eth_blockNumber", []) and not coi.is_archive_request("eth_getLogs", [{}])


def test_4_alchemy_is_paced_to_its_compute_units_and_other_hosts_are_not(monkeypatch):
    """Three block-receipt reads (500 units each) at 500 units a second leave one second apart; an eth_call (26 units)
    after them waits out the last receipt's second and books 0.052 s; a public host never waits."""
    import check_offline_items as coi
    monkeypatch.setenv("ALCHEMY_CU_PER_SECOND", "500")
    monkeypatch.setattr(coi, "_ALCHEMY_NEXT", {})
    now, naps = [100.0], []

    def nap(sec, what):
        naps.append(round(sec, 6))
        now[0] += sec
    url = "https://base-mainnet.g.alchemy.com/v2/SECRETKEY123"
    waits = [coi._alchemy_pace(url, "eth_getBlockReceipts", clock=lambda: now[0], nap=nap) for _ in range(3)]
    assert waits == pytest.approx([0.0, 1.0, 1.0])
    assert coi._alchemy_pace(url, "eth_call", clock=lambda: now[0], nap=nap) == pytest.approx(1.0)
    assert coi._alchemy_pace(url, "eth_call", clock=lambda: now[0], nap=nap) == pytest.approx(0.052)
    assert coi._alchemy_pace("https://mainnet.base.org", "eth_getBlockReceipts", clock=lambda: now[0], nap=nap) == 0.0
    assert len(naps) == 4 and not any("SECRETKEY" in str(x) for x in naps)
    # an idle endpoint owes nothing: after a long pause the next request leaves at once
    now[0] += 60
    assert coi._alchemy_pace(url, "eth_getBlockReceipts", clock=lambda: now[0], nap=nap) == 0.0


def test_4_the_default_pace_is_at_or_under_the_free_plans_allowance(monkeypatch):
    import check_offline_items as coi
    monkeypatch.delenv("ALCHEMY_CU_PER_SECOND", raising=False)
    assert coi.alchemy_cu_per_second() <= 500
    monkeypatch.setenv("ALCHEMY_CU_PER_SECOND", "not a number")
    assert coi.alchemy_cu_per_second() == 330


# ================================================================= A: Aerodrome frozen
def test_a_aerodrome_is_frozen_with_its_rounds_moves_on_record():
    import json
    import project_freeze as pf
    # R16: Ether.fi frozen after Aerodrome (was: FROZEN_BEFORE_R13 | {"Aerodrome"})
    assert set(config.FROZEN_BEFORE_R16) == set(config.FROZEN_BEFORE_R13) | {"Aerodrome"} and len(config.FROZEN_BEFORE_R13) == 7
    assert set(config.FROZEN_PROJECTS) == set(config.FROZEN_BEFORE_R16) | {"Ether.fi"}
    base = json.loads(pf.baseline_path("Aerodrome").read_text(encoding="utf-8"))
    # R16: later rounds' declarations (R16's renamed headers and moves) are left out — the R13 record is 45 cells
    rec = {k: m for k, m in config.FROZEN_MOVES["Aerodrome"].items() if not str(m.get("why", "")).startswith(("R16", "R17", "R18", "R20"))}   # R20 added
    assert len(base) == 365 and len(rec) == 45
    # the record ends on the baseline: every recorded cell's `to` is the frozen value, so nothing is undeclared today
    for k, m in rec.items():
        assert pf._norm(m["to"]) == base.get(k, "<absent>"), k
        assert m["why"].startswith("R") and m["date"] == "2026-11-19"
    assert pf.undeclared(base, dict(base), rec) == []
    # and a later, undeclared change of a frozen Aerodrome cell is caught
    k = next(iter(base))
    assert pf.undeclared(base, {**base, k: "changed"}, rec)


def test_a_a_frozen_aerodrome_dump_is_never_refreshed(tmp_path, capsys):
    import dump_fixture
    assert dump_fixture.main(["--project", "Aerodrome", "--from-export", str(tmp_path / "x.json.gz")]) == 2
    assert "frozen" in capsys.readouterr().out
