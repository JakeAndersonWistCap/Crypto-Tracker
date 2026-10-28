"""
tests/test_round_p2c.py — Round P2c-1 / P2c-2 (2026-10-28), Aerodrome across the Aero migration.
P2c-1: fdv_usd hands over legacy -> v3 at 2026-10-22 (a declared handover the measuring guard reads), with a reference
check of each leg's supply against new-AERO totalSupply 2,129,347,490 at Base block 53,165,286, within ±2%.
P2c-2: 2026-10-21 reads ~218.8M high — a declared SERIES BREAK at the cutover, named on the cells and the Trajectory.
Fakes only: no network, no store.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import measuring_guard as mg  # noqa: E402
import trajectory  # noqa: E402

REF = 2_129_347_490.610605
LEGACY_AT_CUTOVER = 2_002_379_493.920913          # legacy AERO totalSupply, Base block 52,918,950 (2026-10-22 00:00:47)
V3_AT_CUTOVER = 2_119_379_492.920913              # supply_once total at the same block


def _g(metric, rows, project="Aerodrome"):
    return pd.DataFrame({"date": pd.to_datetime([d for d, _v, _s in rows]), "project": project, "metric": metric,
                         "value": [float(v) for _d, v, _s in rows], "tier": 2, "source": [s for _d, _v, s in rows],
                         "is_manual": False, "entered_on": ""})


def _supply_groups(legacy=LEGACY_AT_CUTOVER, v3=V3_AT_CUTOVER):
    ts = _g("total_supply", [("2026-10-20", legacy - 1e5, "chain:base:token"), ("2026-10-21", legacy, "chain:base:token"),
                             ("2026-10-22", v3, "aero_v3:supply_once[ratio 1]"),
                             ("2026-10-23", v3 + 1e5, "aero_v3:supply_once[ratio 1]")])
    fdv = _g("fdv_usd", [("2026-10-21", legacy * 0.5, "derived:price_x_total_supply[chain:base:token]"),
                         ("2026-10-22", v3 * 0.5, "derived:price_x_total_supply[aero_v3:supply_once]")])
    return {("Aerodrome", "total_supply"): ts, ("Aerodrome", "fdv_usd"): fdv}


# ============================================================================== P2c-1: the guard's BASIS check
def test_guard_basis_check_takes_the_legacy_and_v3_fdv_bases_without_a_violation():
    """live_check's basis check (measuring_guard.live_basis_violations), fed Jake's two bases on the fixture baseline."""
    baseline = json.loads((ROOT / "tests" / "fixtures" / "guard_baseline.json").read_text(encoding="utf-8"))
    assert baseline["bases"]["Aerodrome|fdv_usd"] == ["chain:base:token"]
    bases = {"Aerodrome|fdv_usd": ["aero_v3:supply_once", "chain:base:token"]}
    assert mg.live_basis_violations(bases, baseline) == []
    # the fixture compare reads the same declaration
    assert mg.compare({"bases": baseline["bases"], "headlines": {}}, {"bases": bases, "headlines": {}}) == []
    assert {"chain:base:token", "aero_v3:supply_once"} <= mg._declared("Aerodrome", "fdv_usd")


def test_guard_basis_check_still_flags_fdv_without_the_declaration(monkeypatch):
    """The control: Jake's reported violation, reproduced with the P2c-1 declaration taken out (global fdv_usd only)."""
    sh = dict(config.PROJECT_BY_NAME["Aerodrome"]["series_handover"])
    sh.pop("fdv_usd")
    monkeypatch.setitem(config.PROJECT_BY_NAME["Aerodrome"], "series_handover", sh)
    bad = mg.live_basis_violations({"Aerodrome|fdv_usd": ["aero_v3:supply_once", "chain:base:token"]},
                                   {"bases": {"Aerodrome|fdv_usd": ["chain:base:token"]}})
    assert bad == ["BASIS Aerodrome fdv_usd: ['aero_v3:supply_once'] beside ['chain:base:token'] with no declared "
                   "handover or component"]


def test_fdv_handover_is_declared_in_config_and_the_switch_file_alike():
    decl = config.declared_handover("Aerodrome", "fdv_usd")
    assert decl["declared"] == config.AERODROME_MIGRATION["cutover_utc"] == "2026-10-22"
    assert [trajectory.basis_of("fdv_usd", p) for p in decl["ordered_points"]] == [
        "coingecko", "chain:base:token", "aero_v3:supply_once"]
    # handover_refusal reads the legs as measuring points: the same two the global fdv_usd declaration named
    from fetch.base import _measuring_point
    assert list(dict.fromkeys(_measuring_point(p) for p in decl["ordered_points"])) == list(
        config.GLOBAL_SERIES_HANDOVER["fdv_usd"]["ordered_points"])
    assert not decl["composition_change"].lower().startswith("none")          # a STEP: a series break at 10-22
    rc = decl["reference_check"]
    assert rc["tolerance_pct"] == 2.0 and rc["reference"]["block"] == 53_165_286
    assert rc["reference"]["value"] == pytest.approx(REF)
    st = json.loads((ROOT / "aero_migration.json").read_text(encoding="utf-8"))["series_handover"]["fdv_usd"]
    assert st["declared"] == decl["declared"] and st["composition_change"] == "step"
    assert st["ordered_points"] == [p for _k, p in rc["legs"]]
    assert st["reference"]["block"] == rc["reference"]["block"] and st["reference"]["value"] == rc["reference"]["value"]
    assert st["reference"]["token"].lower() == config.aerodrome_migration_state()["addresses"]["token"].lower()
    assert st["tolerance_pct"] == rc["tolerance_pct"]


def test_handover_refusal_accepts_the_fdv_legs():
    spans = {"coingecko": (pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-10")),
             "derived:price_x_total_supply": (pd.Timestamp("2026-09-11"), pd.Timestamp("2026-10-28"))}
    assert bw.handover_refusal("Aerodrome", "fdv_usd", set(spans), spans) is None


# ============================================================================== P2c-1: the reference check
def test_reference_check_reports_the_legacy_gap_and_passes_the_v3_leg():
    """Each leg at the cutover vs 2,129,347,490: the legacy leg is outside ±2% — NOT PASSED with its gap, absolute and %;
    the v3 leg is within."""
    rc = config.declared_handover("Aerodrome", "fdv_usd")["reference_check"]
    out = bw.handover_reference_check(_supply_groups(), "Aerodrome", rc)
    leg, v3 = out["legs"]
    assert not out["passed"]
    assert not leg["passed"] and leg["value"] == pytest.approx(LEGACY_AT_CUTOVER)
    assert leg["gap"] == pytest.approx(LEGACY_AT_CUTOVER - REF) and leg["gap_pct"] == pytest.approx(-5.9628, abs=1e-3)
    assert v3["passed"] and v3["gap_pct"] == pytest.approx(-0.4681, abs=1e-3)
    t = out["text"]
    assert t.startswith("REFERENCE CHECK NOT PASSED") and "2,129,347,490.61" in t and "53,165,286" in t
    assert "legacy leg (chain:base:token, 2026-10-21) 2,002,379,494, gap -126,967,997 (-5.96%) OUTSIDE ±2%" in t
    assert "v3 leg (aero_v3:supply_once, 2026-10-22) 2,119,379,493, gap -9,967,998 (-0.47%) within ±2%" in t
    # the declared same-block reads, checked by the same rule
    assert "legacy at block 53,165,286 2,006,874,385, gap -122,473,106 (-5.75%) OUTSIDE" in t
    assert "v3 at block 53,165,286 2,126,221,874, gap -3,125,616 (-0.15%) within" in t
    assert "STEP at 2026-10-22" in t


def test_reference_check_passes_when_every_leg_is_inside_the_tolerance():
    rc = dict(config.declared_handover("Aerodrome", "fdv_usd")["reference_check"], block_reads=())
    out = bw.handover_reference_check(_supply_groups(legacy=REF * 0.981, v3=REF * 1.019), "Aerodrome", rc)
    assert out["passed"] and all(x["passed"] for x in out["legs"])
    assert out["text"].startswith("REFERENCE CHECK PASSED") and "OUTSIDE" not in out["text"]
    # just outside (the tolerance is not loosened): -2.01% fails
    out = bw.handover_reference_check(_supply_groups(legacy=REF * 0.9799, v3=REF), "Aerodrome", rc)
    assert not out["passed"] and "(-2.01%) OUTSIDE ±2%" in out["text"]


def test_reference_check_with_a_leg_not_stored_is_unchecked_never_passed():
    rc = dict(config.declared_handover("Aerodrome", "fdv_usd")["reference_check"], block_reads=())
    g = _supply_groups(v3=REF)
    g[("Aerodrome", "total_supply")] = g[("Aerodrome", "total_supply")].iloc[2:]          # no legacy row
    out = bw.handover_reference_check(g, "Aerodrome", rc)
    assert not out["passed"] and out["legs"][0]["value"] is None and "no stored row — UNCHECKED" in out["text"]


def test_a_failed_reference_check_is_on_the_fdv_audit_cell(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Aerodrome"]])
    bw._handover_overlap_checks(_supply_groups())
    assert not bw.HANDOVER_OVERLAP[("Aerodrome", "fdv_usd")]["passed"]
    row = {"status": "ok", "n_points": 50, "source": "derived:price_x_total_supply[aero_v3:supply_once]"}
    conf, why = bw.confidence_for("Aerodrome", "fdv_usd", row, pd.Timestamp("2026-10-28"))
    assert conf == "AMBER" and "DECLARED HANDOVER (2026-10-22): REFERENCE CHECK NOT PASSED" in why
    assert "gap -126,967,997 (-5.96%) OUTSIDE ±2%" in why
    # a build whose fdv_usd ends before the cutover has no seam: no check, nothing added (the fixture build)
    g = _supply_groups()
    g[("Aerodrome", "fdv_usd")] = g[("Aerodrome", "fdv_usd")].iloc[:1]
    bw._handover_overlap_checks(g)
    assert ("Aerodrome", "fdv_usd") not in bw.HANDOVER_OVERLAP
    bw.HANDOVER_OVERLAP.clear()


# ============================================================================== P2c-2: 2026-10-21, a declared break
def _circ_groups():
    days = pd.date_range("2026-07-01", "2026-10-28")
    rows = [(str(d.date()), 1.54e9 + (218.8e6 if str(d.date()) == "2026-10-21" else 0.0),
             "derived:onchain_total-noncirculating") for d in days if not "2026-10-22" <= str(d.date()) <= "2026-10-27"]
    circ = _g("circulating_supply_onchain", rows)
    used = circ.assign(metric="circulating_supply_used", source="derived:circulating_used[circulating_supply_onchain]")
    filing = _g("filing_managed_lock_tokens",
                [(d, v, "ve_managed:0xeBf418Fe.weights") for d, v in (("2026-10-20", 444.3e6), ("2026-10-21", 2.85e6))]
                + [("2026-10-28", 500.5e6, "aero_v3:filing_locks")])
    return {("Aerodrome", "circulating_supply_onchain"): circ, ("Aerodrome", "circulating_supply_used"): used,
            ("Aerodrome", "filing_managed_lock_tokens"): filing}


def test_the_1021_break_is_declared_at_the_cutover_on_every_series_that_carries_it():
    for m in ("filing_managed_lock_tokens", "circulating_supply_onchain", "circulating_supply_used", "market_cap_usd"):
        b = config.SERIES_BREAK_NOTES[("Aerodrome", m)]
        assert b["break"] == "2026-10-22" and b["day"] == "2026-10-21"
        assert ("2026-10-22", b["text"]) in config.SOURCE_RECLASSIFICATIONS[("Aerodrome", m)]
    t = config.AERO_1021_WHY
    assert t.startswith("2026-10-21: circulating reads ~218.8M HIGH") and "normal veNFTs" in t and "Not smoothed" in t


def test_the_trajectory_reads_the_break_not_a_number_across_it():
    """trajectory.basis_breaks (the Trajectory's basis rule) finds the declared 2026-10-22 break from a row that reads
    circulating_supply_used, beside the filing leg's own change of basis; clean_quarters leaves no quarter spanning it."""
    found = trajectory.basis_breaks("Aerodrome", {"circulating_supply_used"}, _circ_groups(), pd.Timestamp("2025-10-28"))
    declared = [x for x in found if x[3] == config.AERO_1021_WHY]
    assert {(str(x[0].date()), x[1]) for x in declared} == {("2026-10-22", "circulating_supply_used"),
                                                            ("2026-10-22", "circulating_supply_onchain"),
                                                            ("2026-10-22", "filing_managed_lock_tokens")}
    assert (pd.Timestamp("2026-10-28"), "filing_managed_lock_tokens", "ve_managed:0xeBf418Fe.weights",
            "aero_v3:filing_locks") in found
    assert trajectory.clean_quarters(pd.Timestamp("2026-10-28"), pd.Timestamp("2026-10-22"), 91, 5) == 0


def test_the_series_holding_1021_carries_the_note_on_the_row_and_the_audit_cell():
    bw._series_break_notes(_circ_groups())
    note = bw.SERIES_BREAK_NOTED[("Aerodrome", "circulating_supply_onchain")]
    assert note.startswith("DECLARED SERIES BREAK 2026-10-22: 2026-10-21: circulating reads ~218.8M HIGH")
    assert ("Aerodrome", "market_cap_usd") not in bw.SERIES_BREAK_NOTED          # not in these groups: no note
    row = {"status": "ok", "n_points": 100, "source": "derived:onchain_total-noncirculating"}
    conf, why = bw.confidence_for("Aerodrome", "circulating_supply_onchain", row, pd.Timestamp("2026-10-28"))
    assert conf == "AMBER" and "~218.8M HIGH" in why and "2026-10-22" in why
    bw.SERIES_BREAK_NOTED.clear()
    conf, why = bw.confidence_for("Aerodrome", "circulating_supply_onchain", row, pd.Timestamp("2026-10-28"))
    assert "218.8M" not in why


def test_the_trajectory_break_text_names_1021_and_the_statistics_stop_at_the_break():
    """The Trajectory row code: the declared text is appended to the SERIES BREAK text, and the quarters before the break
    are dropped from the statistics for EVERY break (the ef6dc86 rule; 0e7fc8b had nested it under the restatement test)."""
    import inspect
    src = inspect.getsource(bw)
    i = src.index('brk_text += "".join(f" · DECLARED: {t}" for t in named)')
    j = src.index("qs = list(qs[:n_clean]) + [None] * (len(qs) - n_clean)", i)
    k = src.index("# P2b-2: a declared restatement not yet complete says what it lacks", i)
    assert i < j < k
    line = src[src.rindex("\n", 0, j) + 1:j]
    assert line == " " * 20                                                    # inside `if n_clean < len(qs):`
