"""Project round 4 — Morpho, plus freezing Maple, the machine-local wiring and a circulating audit (Jake 2026-11-12).
No network."""
import json

import pandas as pd
import pytest

import build_workbook as bw
import config
import done_check
import project_freeze as pf

CP = "audit|CP Comparability|"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from shared_build import plain_build
    rec = plain_build(tmp_path_factory, "pr4")
    assert "error" not in rec["stats"], rec["stats"]
    return rec


@pytest.fixture(scope="module")
def morpho(built):
    return pf.cells(built["audit"], built["reader"], "Morpho")


def _cell(cells, head):
    return next(v for k, v in cells.items() if k.startswith(CP + head))


# ===================================================================================================== 0a: Maple frozen
def test_0a_maple_is_frozen_with_round_3_and_3bs_moves_declared(built):
    assert config.FROZEN_PROJECTS["Maple"]["baseline"] == "tests/fixtures/frozen_maple.json"
    base = json.loads(pf.baseline_path("Maple").read_text(encoding="utf-8"))
    moves = config.FROZEN_MOVES["Maple"]
    assert len(base) == 360 and len(moves) == 46
    assert all(d["date"] == "2026-11-11" and d["why"].startswith(("PR3.", "PR3B.")) for d in moves.values())
    assert all(base[k] == pf._norm(d["to"]) for k, d in moves.items())     # the baseline holds every declared value
    now = pf.cells(built["audit"], built["reader"], "Maple")
    assert pf.undeclared(base, now, moves) == []


# =========================================================================================== 0b: the probe wiring, pinned
def test_0b_the_frozen_build_carries_exactly_the_pinned_probe_whatever_the_local_file_wires(monkeypatch):
    """explorer_routes.json is machine-local: a scan it wires beyond the pin (here Pendle's Mantle gauge) is taken out of
    a frozen / test build, and the pin's six are wired — Pendle BNB / Base / Berachain, Fluid Base / Plasma, Morpho Base.
    Idempotent; no fetch route opened or closed."""
    for proj in ("Pendle", "Fluid", "Morpho"):
        p = config.PROJECT_BY_NAME[proj]
        monkeypatch.setitem(p, "log_scans", [s for s in p.get("log_scans") or ()
                                             if s["key"] not in {r[3] for r in config.EXPLORER_PROBED_SCANS}])
    pendle_em = tuple(m for m in config.PROJECT_BY_NAME["Pendle"]["emissions_from_metric"]
                      if m not in {r[4] for r in config.EXPLORER_PROBED_SCANS})
    monkeypatch.setitem(config.PROJECT_BY_NAME["Pendle"], "emissions_from_metric", pendle_em)
    monkeypatch.setitem(config.OUTFLOW_CATEGORIES["Fluid"], "b", ("emissions_tokens", "emissions_tokens_arbitrum"))
    routes = {k: list(v) for k, v in config.EXPLORER_LOG_ROUTES.items()}
    troutes = {k: list(v) for k, v in config.EXPLORER_TRANSFER_ROUTES.items()}
    # PR4D.5a: every probed chain is in the pin now (Jake's file serves Mantle, Sonic, HyperEVM too) — so the test pin
    # leaves Sonic out, to prove a chain the local file wires beyond the pin is taken out
    monkeypatch.setitem(config.FROZEN_PROBE_PIN, "served", [c for c in config.FROZEN_PROBE_PIN["served"] if c != 146])
    assert config.wire_probed_scans({"served": [146]}, open_routes=False) == ["gauge_pendle_out_sonic"]
    wired = config.pin_frozen_probe()
    assert sorted(wired) == sorted(k for _p, _c, k, _m in config.frozen_probe_wiring())
    assert sorted((p, c) for p, c, _k, _m in config.frozen_probe_wiring()) == [
        ("Fluid", "base"), ("Fluid", "plasma"), ("Morpho", "base"), ("Pendle", "base"), ("Pendle", "berachain"),
        ("Pendle", "bsc"), ("Pendle", "hyperevm"), ("Pendle", "mantle")]
    keys = {s["key"] for s in config.PROJECT_BY_NAME["Pendle"]["log_scans"]}
    assert "gauge_pendle_out_sonic" not in keys and {"gauge_pendle_out_bsc", "gauge_pendle_out_base",
                                                      "gauge_pendle_out_berachain", "gauge_pendle_out_mantle"} <= keys
    em = config.PROJECT_BY_NAME["Pendle"]["emissions_from_metric"]
    assert "emissions_tokens_gauge_sonic" not in em and "emissions_tokens_gauge_mantle" in em
    assert config.OUTFLOW_CATEGORIES["Fluid"]["b"][-2:] == ("emissions_tokens_base", "emissions_tokens_plasma")
    assert "merkl_morpho_out_base" in {s["key"] for s in config.PROJECT_BY_NAME["Morpho"]["log_scans"]}
    assert config.pin_frozen_probe() == []                                        # idempotent
    assert {k: list(v) for k, v in config.EXPLORER_LOG_ROUTES.items()} == routes
    assert {k: list(v) for k, v in config.EXPLORER_TRANSFER_ROUTES.items()} == troutes


# =============================================================================================== 1: Morpho circulating
def test_1_morphos_circulating_takes_out_only_what_is_not_released():
    spec = config.CIRCULATING_ONCHAIN["Morpho"]
    # PR4C.1: the relocked MORPHO (modelled from the schedule) is out too
    assert spec["subtract"] == ("noncirculating_holding_tokens", "morpho_relocked_tokens")
    assert spec["subtract_when_complete"]["metric"] == "morpho_undistributed_tokens"
    assert spec["handover"]["on"] == "2026-11-12" and "issuer rule" not in spec["handover"]["to"]
    c = config.PROJECT_BY_NAME["Morpho"]["contracts"]
    undist = {k: v["address"] for k, v in c.items() if v.get("metric_override") == "morpho_undistributed_tokens"}
    # PR4B.3: the Contributor Grants Safes hold the contributors' unvested / unallocated reserve (Morpho's filing) — not
    # released, so they joined this leg
    assert set(undist) == {"undist_rewards_multisig", "undist_rewards_multisig_legacy", "undist_merkl_distributor",
                           "undist_merkl_distributor_base", "undist_urd", "undist_urd_legacy",
                           "undist_contributor_grants_safe", "undist_contributor_grants_safe_2",
                           "undist_contributor_grants_2_legacy"}
    # the entities' stock is the float's leg while pending; in force, the released part's own series (PR4B.3)
    assert config.float_released_held("Morpho")["from"] == "noncirculating_entities_tokens"
    assert "decision 1 on 6f8c86c" not in config.DISCRETIONARY_SUPPLY["Morpho"][1]
    # no other project's circulating moved
    assert config.CIRCULATING_ONCHAIN["Fluid"]["subtract"] == ("treasury_holding_tokens", "noncirculating_holding_tokens",
                                                               "noncirculating_igp137_tokens")


def _swc():
    return {"metric": "leg", "whole_series": True, "symbol": "MORPHO"}


def test_1_the_undistributed_leg_switches_on_only_when_held_on_every_day():
    days = pd.date_range("2026-01-01", periods=10)
    s = pd.Series(100.0, index=days)
    leg = pd.DataFrame({"date": days[5:], "project": "X", "metric": "leg", "value": 2.0, "source": "chain", "tier": 2})
    swc = _swc()
    out, tag = bw._subtract_when_complete({("X", "leg"): leg}, "X", s, "t", swc)
    assert swc["in_force"] is False and out.equals(s) and tag == "t"
    assert swc["state"].startswith("PENDING — leg held on 5 of 10 day(s)") and "--project X" in swc["state"]
    full = leg.assign(date=days) if len(leg) == len(days) else pd.concat([leg, leg.assign(date=days[:5])])
    swc = _swc()
    out, tag = bw._subtract_when_complete({("X", "leg"): full}, "X", s, "t", swc)
    assert swc["in_force"] is True and (out == 98.0).all() and len(out) == 10 and tag == "t[− leg]"
    assert swc["state"].endswith("2 MORPHO taken out on 2026-01-10")


def test_1_unlocks_follow_the_set_circulating_subtracts_and_never_count_claims_twice(monkeypatch):
    swc = config.CIRCULATING_ONCHAIN["Morpho"]["subtract_when_complete"]
    monkeypatch.setitem(swc, "in_force", False)
    # PR4C.1: the relocked stock is a leg of the release too (each day's vesting is an unlock); kept out of this check
    monkeypatch.setitem(config.CIRCULATING_ONCHAIN["Morpho"], "subtract", ("noncirculating_holding_tokens",))
    assert config.unlock_stock_metrics("Morpho") == ("noncirculating_holding_tokens",)
    assert config.release_less("Morpho") == ((), False)
    monkeypatch.setitem(swc, "in_force", True)
    assert config.unlock_stock_metrics("Morpho") == ("noncirculating_holding_tokens", "morpho_undistributed_tokens")
    assert config.release_less("Morpho") == (("emissions_tokens_claims_ethereum", "emissions_tokens_claims_base"), True)
    # a required leg with no rows leaves (e) unbuilt — never a release that counts the claims twice
    days = pd.date_range("2026-01-01", periods=4)
    rows = lambda m, v: pd.DataFrame({"date": days, "project": "Morpho", "metric": m, "value": v,  # noqa: E731
                                      "source": "chain:sum(x)", "tier": 2})
    groups = {("Morpho", "noncirculating_holding_tokens"): rows("noncirculating_holding_tokens", [10.0, 9.0, 8.0, 8.0]),
              ("Morpho", "morpho_undistributed_tokens"): rows("morpho_undistributed_tokens", [5.0, 5.0, 4.0, 4.0])}
    bw.UNLOCK_LESS_MISSING.clear()
    bw._unlock_release_view(groups, "Morpho", "unlock_release_tokens", config.unlock_stock_metrics("Morpho"))
    assert ("Morpho", "unlock_release_tokens") not in groups
    assert bw.UNLOCK_LESS_MISSING[("Morpho", "unlock_release_tokens")] == "emissions_tokens_claims_ethereum"
    # with the claims stored, they come out of the falls: day 3 falls 2 (1 DAO + 1 distributor), 1 of it claimed
    groups[("Morpho", "emissions_tokens_claims_ethereum")] = rows("emissions_tokens_claims_ethereum", [0.0, 0.0, 1.0, 0.0])
    groups[("Morpho", "emissions_tokens_claims_base")] = rows("emissions_tokens_claims_base", [0.0, 0.0, 0.0, 0.0])
    bw._unlock_release_view(groups, "Morpho", "unlock_release_tokens", config.unlock_stock_metrics("Morpho"))
    rel = groups[("Morpho", "unlock_release_tokens")].set_index("date")["value"]
    assert list(rel.round(6)) == [1.0, 1.0, 0.0]


def test_1_on_the_dumps_circulating_is_total_less_the_dao_pending_and_the_float_takes_the_entities(morpho):
    """The dumps hold no morpho_undistributed_tokens rows: the switch is PENDING (said on the cell) and circulating is
    total − the DAO − the relocked MORPHO modelled for 2026-10-13 (PR4C.1: 121.2M) = 574.8M; the released entities
    (148.7M) leave the float: 426.05M."""
    circ = _cell(morpho, "Circulating supply (tokens)")
    flt = _cell(morpho, "TRADEABLE FLOAT")
    assert circ == pytest.approx(696_001_146.74 - 121_200_000, abs=1)
    assert flt == pytest.approx(547_252_376.94 - 121_200_000, abs=1)
    assert _cell(morpho, "Tradeable float ÷ circulating") == pytest.approx(flt / circ, rel=1e-9)
    assert morpho["reader|A2 Coordination|Free float (tokens)"] == pytest.approx(426_052_377, abs=1)
    assert morpho["reader|A2 Coordination|Circulating supply"] == pytest.approx(574_801_147, abs=1)
    note = config.circulating_rule_note("Morpho")
    assert "DECLARED HANDOVER" in note and "PENDING" in note and "Arbitrum" in note


# ============================================================================================ 2: the three revenue lines
def test_2_morphos_three_revenue_lines_and_holder_share_are_declared(morpho):
    s = config.revenue_split("Morpho")
    assert (s["fees"]["metric"], s["fees"]["primary"]) == ("fees_usd", False)
    assert (s["revenue"]["metric"], s["revenue"]["primary"]) == ("revenue_usd", True)
    assert s["holder_revenue"]["metric"] == "holder_value_revenue_usd"
    assert "feeRecipient() = 0x0" in s["revenue"]["route"] and "26,397,959" in s["revenue"]["route"]
    assert _cell(morpho, "FEES Q0 ($)") == pytest.approx(55_126_707.58)
    assert _cell(morpho, "REVENUE Q0 ($)") == 0 and _cell(morpho, "HOLDER REVENUE Q0 ($)") == 0
    share = _cell(morpho, "HOLDER SHARE")
    assert share == s["holder_share"]["zero_over_zero_text"] and share.startswith("n/a — 0 ÷ 0 while the fee switch is off")
    reasons = [share, "n/a — 0 ÷ 0: revenue_usd is 0 or not stored in this window, so there is no ratio",
               config.ZERO_FLOW_GROWTH_TEXT[("Morpho", "revenue_usd")], "n/a — No buyback; fee switch off"]
    hits = [{"project": "Morpho", "tab": "audit:CP Comparability", "header": f"h{i}", "code": "n/a", "reason": r}
            for i, r in enumerate(reasons)]
    assert done_check.judge_suspects(hits, "Morpho", []) == []
    for r in reasons:
        assert config.declared_na_reason("Morpho", r)["read_on"] == "2026-11-12", r
    assert not config.declared_na_reason("Fluid", reasons[0])                       # Morpho's only


# =================================================================================================== 3: one emissions
def test_4b_morphos_uniformity_cell_reads_the_declared_reason(morpho):
    """PR4B.4: 'no MORPHO staking exists' (Morpho's Blockworks filing Q3(c), read 2026-11-12) on the CP cell."""
    assert _cell(morpho, "UNIFORMITY") == config.declared_na_text("Morpho", "UNIFORMITY")


def test_3_cp_emissions_reads_the_same_figure_as_a2_and_outflow_b(morpho):
    """MORPHO paid to suppliers from the pre-mint is incentive spend (b): CP 'Emissions Q0' reads emissions_tokens, as A2
    and (b) do (on the dumps all three are BLOCKED: the Base / Arbitrum / Katana claims have no rows)."""
    cp_e = _cell(morpho, "Emissions Q0 (tokens)")
    a2 = morpho["audit|A2 Coordination|Emissions to suppliers Q0 (tokens)"]
    assert cp_e == a2 and "fixed supply" not in str(cp_e)
    assert config.outflow_metrics("Morpho", "b") == ("emissions_tokens",)
    # gross issuance stays the fixed-supply convention (nothing is minted); Pendle's CP emissions cell is unchanged
    assert config.fixed_supply_reason("Morpho")
    assert "Morpho" in config.EMISSIONS_ARE_INCENTIVE_SPEND and "Pendle" not in config.EMISSIONS_ARE_INCENTIVE_SPEND
