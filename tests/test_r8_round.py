"""Round 8 (Jake 2026-11-17): Aerodrome, plus one circulating rule for every project. Behaviour and values. No network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402
import project_freeze as pf  # noqa: E402


# =============================================== 6: one freeze compare for several projects, the dumps built once
def test_6_compare_takes_several_projects_and_builds_once(monkeypatch, capsys):
    builds = []
    base = {p: json.loads(pf.baseline_path(p).read_text(encoding="utf-8")) for p in config.FROZEN_PROJECTS}

    def fake_build(d):
        builds.append(d)
        return "audit", "reader"
    monkeypatch.setattr(pf, "_build", fake_build)
    monkeypatch.setattr(pf, "cells", lambda audit, reader, project: dict(base[project]))
    assert pf.main(["--compare", "Uniswap", "Sky", "Hyperliquid"]) == 0
    out = capsys.readouterr().out
    assert len(builds) == 1
    for p in ("Uniswap", "Sky", "Hyperliquid"):
        assert f"{p}: {len(base[p])} cells, 0 undeclared move(s)" in out
    assert pf.main(["--compare", "all"]) == 0 and len(builds) == 2
    assert capsys.readouterr().out.count("0 undeclared move(s)") == len(config.FROZEN_PROJECTS)


def test_6_one_undeclared_move_fails_the_whole_call_and_is_named(monkeypatch, capsys):
    base = {p: json.loads(pf.baseline_path(p).read_text(encoding="utf-8")) for p in ("Uniswap", "Fluid")}
    key = next(iter(base["Fluid"]))
    monkeypatch.setattr(pf, "_build", lambda d: ("a", "r"))
    monkeypatch.setattr(pf, "cells", lambda a, r, project: {**base[project], **({key: "moved"} if project == "Fluid" else {})})
    monkeypatch.setitem(config.FROZEN_MOVES, "Fluid", {})
    assert pf.main(["--compare", "Uniswap", "Fluid"]) == 1
    out = capsys.readouterr().out
    assert "Uniswap: " in out and "Fluid: " in out and "1 undeclared move(s)" in out and key in out
    assert pf.main(["--compare", "Uniswap", "Nobody"]) == 2


# ================================================================================ the rule, written once
def test_the_rule_out_only_when_all_three_hold():
    c = config.classify_position
    assert c(unreleased=True, immovable=True, idle=True)["circulating"] is False
    # any one of the three failing puts it in circulating — and off the float when it earns or the issuer holds it
    staked_unvested = c(unreleased=True, immovable=True, idle=False, staked_locked_or_earning=True)
    assert staked_unvested["circulating"] and staked_unvested["off_float"]
    treasury = c(unreleased=True, immovable=False, idle=True, issuer_held=True)
    assert treasury["circulating"] and treasury["off_float"]
    holder_lock = c(staked_locked_or_earning=True)
    assert holder_lock["circulating"] and holder_lock["off_float"]
    plain = c()
    assert plain["circulating"] and not plain["off_float"]
    burned = c(burned=True)
    assert not burned["circulating"] and not burned["in_total"]
    assert len(config.CIRCULATING_RULE["out_only_if_all"]) == 3 and "2026-11-17" in config.CIRCULATING_RULE["decided"]


# ============================================================================== 5: Hyperliquid follow-ups
def test_5b_the_four_guessed_stats_paths_are_retired_and_make_no_read():
    from fetch.base import FetchOutput
    from fetch.hl_stats import HlStats
    gets = []

    class H:
        def post(self, url, json_body=None):
            return {"dailyUserVlm": []} if json_body["type"] == "userFees" else []

        def get(self, url, params=None):
            gets.append(url)
            raise RuntimeError("403")
    out = FetchOutput()
    HlStats(http=H()).run([config.PROJECT_BY_NAME["Hyperliquid"]], 30, out)
    assert gets == [] and not any("stats-data" in e.message for e in out.log)
    assert len(config.HL_STATS_RETIRED["series"]) == 4
    assert not set(config.HL_STATS_RETIRED["series"]) & set(config.METRICS)
    assert "hl_exchange_volume_usd" in config.METRICS and "hl_hip3_dex_count" in config.METRICS


def test_5c_circ_legs_names_the_series_hyperliquids_circulating_reads():
    import circ_legs
    legs = circ_legs.legs("Hyperliquid")
    assert legs[0] == ("circulating_supply_first_party", "first-party figure")
    assert "total_supply" not in [m for m, _ in legs]
    assert circ_legs.legs("Sky")[0] == ("total_supply", "total")                 # an on-chain set keeps its total leg


# ============================================================= 4: the rule against the frozen projects — the audit
def test_4_every_frozen_project_is_audited_and_the_sums_tie():
    import pytest
    a = config.RULE_AUDIT_R8
    assert set(a["projects"]) == set(config.FROZEN_PROJECTS) and (ROOT / a["evidence"]).exists()
    for name, p in a["projects"].items():
        now, rule = p["circulating"]
        moved = sum(v for _m, v, _w in p["moves_in"])
        assert rule >= now and all(w for _m, _v, w in p["moves_in"]), name
        # today's figure + what the rule moves in = the rule's figure (within 0.5%: reads at different minutes)
        assert now + moved == pytest.approx(rule, rel=0.005), (name, now + moved, rule)


def test_4_the_audit_is_applied_and_every_leg_it_moves_in_is_declared():
    """R9.2 (Jake 2026-11-17: apply the rule to all seven): the audit is applied — every leg it moves in is one the
    build counts in circulating by declaration (config.RULE_IN), and nothing it leaves out is counted in."""
    a = config.RULE_AUDIT_R8
    assert a["applied"] is True and set(a["projects"]) <= set(config.RULE_IN)
    for name, p in a["projects"].items():
        spec = config.RULE_IN[name]
        declared = set(spec["legs"]) | set(spec["off_float"]) | {spec.get("swc_leg")}
        named = {m for m, _v, _w in p["moves_in"]}
        # Morpho's relock is audited "moves in" on the literal rule and STAYS OUT by Jake's decision
        named -= {"morpho_relocked_tokens"} if name == "Morpho" else set()
        assert named <= declared, (name, named - declared)
    assert "morpho_relocked_tokens" not in config.rule_in_legs("Morpho")


def test_4a_held_buybacks_are_listed_and_none_is_out_of_circulating():
    # R9.2: Fluid's Treasury DSA (the one held buyback that was out) is in circulating, off the float
    assert not [r for r in config.HELD_BUYBACKS_R8 if r[3].startswith("OUT")]
    assert "treasury_holding_tokens" in config.RULE_IN["Fluid"]["off_float"]
    near = [r for r in config.HELD_BUYBACKS_R8 if r[0] == "Near"]
    assert len(near) == 3 and 4.3e6 < sum(r[2] for r in near) < 4.4e6
    assert any(r[0] == "Hyperliquid" and r[3].startswith("burned") for r in config.HELD_BUYBACKS_R8)


# =========================================================================== 1: Aerodrome under the rule
def test_1_aerodrome_circulating_subtracts_nothing_issuer_side():
    import pandas as pd
    from fetch import circulating as C
    spec = config.circulating_onchain("Aerodrome")
    # R9.2: the issuer-side set is listed, and every leg of it is counted IN by the rule — nothing is subtracted
    assert set(spec["subtract"]) == set(config.rule_in_legs("Aerodrome")) and "Jake 2026-11-17" in spec["decided_by"]
    d = pd.Timestamp("2026-11-10")
    h = pd.DataFrame([{"date": d, "project": "Aerodrome", "metric": "total_supply", "value": 2_141.5e6},
                      {"date": d, "project": "Aerodrome", "metric": "noncirculating_holding_tokens", "value": 58.3e6},
                      {"date": d, "project": "Aerodrome", "metric": "filing_managed_lock_tokens", "value": 503.1e6}])
    assert list(C.series(h, "Aerodrome")) == [2_141.5e6]
    assert list(C.series(h[h.metric == "total_supply"], "Aerodrome")) == [2_141.5e6]     # the legs are not needed for it
    assert config.RULE_IN["Aerodrome"]["off_float"] == ("aero_issuer_held_tokens", "filing_managed_lock_tokens")
    assert config.unlock_stock_metrics("Aerodrome") == () and "Aerodrome" in config.UNLOCK_DECLARED_ZERO


def test_1_the_read_ties_circulating_float_and_both_references():
    import pytest
    r = config.AERO_RULE_READ
    circ = (r["total_supply_new"] - r["aero_swap_reserve"] - r["velo_swap_reserve"] + r["unswapped_legacy_nonissuer"]
            + min(r["unswapped_velo_x0044"], r["velo_swap_reserve"]))
    assert circ == pytest.approx(r["circulating"], abs=1)
    assert r["circulating"] - sum(r["off_float"].values()) == pytest.approx(r["free_float"], abs=1)
    for ref, _how, legs in r["references"].values():           # every gap is named leg by leg, to within 0.1%
        assert sum(legs.values()) == pytest.approx(r["circulating"] - ref, rel=0.001)
    assert r["unswapped_velo_x0044"] >= r["velo_swap_reserve"]                  # the cap binds: the reserve stands in


def test_1_issuer_liquid_is_the_holding_less_the_velodrome_reserve_and_zero_reserve_before_the_cutover():
    import pandas as pd
    import build_workbook as bw
    d = pd.to_datetime(["2026-10-20", "2026-10-21", "2026-10-23", "2026-10-24"])
    mk = lambda m, days, v: pd.DataFrame({"date": days, "project": "Aerodrome", "metric": m, "value": v,  # noqa: E731
                                          "tier": 2, "source": "chain"})
    g = {("Aerodrome", "noncirculating_holding_tokens"): mk("noncirculating_holding_tokens", d, [36e6, 36e6, 58e6, 58e6]),
         ("Aerodrome", "aero_velo_reserve_tokens"): mk("aero_velo_reserve_tokens", d[2:3], [21e6])}
    bw._difference_views(g)
    s = g[("Aerodrome", "aero_issuer_held_tokens")].set_index("date")["value"]
    assert s.loc["2026-10-20"] == 36e6 and s.loc["2026-10-21"] == 36e6          # before the cutover: no reserve existed
    assert s.loc["2026-10-23"] == 37e6
    assert pd.Timestamp("2026-10-24") not in s.index                            # unread after the cutover: unknown


def test_1_coingecko_points_at_the_merged_token_with_a_declared_handover():
    p = config.PROJECT_BY_NAME["Aerodrome"]
    assert p["coingecko_id"] == "aero" and p["coingecko_id_handover"]["at"] == "2026-10-22"
    assert p["coingecko_id_handover"]["from"] == "aerodrome-finance"


# ================================================================================ 3: Aerodrome's three lines
def test_3_aerodrome_revenue_lines_are_declared_and_ordered_on_the_export_figures():
    # R9.4 (Jake 2026-11-17): the on-chain epoch read is the source of record; DefiLlama is the cross-check on the same
    # epochs — the three lines read the per-epoch views, each the one below + a measured leg
    rs, on = config.REVENUE_SPLIT["Aerodrome"], config.S13_ONCHAIN["Aerodrome"]
    assert [rs[k]["metric"] for k in ("fees", "revenue", "holder_revenue")] == [
        on["views"]["fees"], on["views"]["revenue"], on["views"]["holder_revenue"]]
    assert all(rs[k]["primary"] for k in ("fees", "revenue", "holder_revenue"))
    assert on["defillama"] == {"fees": "fees_usd", "revenue": "revenue_usd", "holder_revenue": "holders_revenue_usd"}
    assert on["insider_stake_share"]["share"] == 0.4667 and on["unstaked_lp_share"]["was_expected"] == 0.29
