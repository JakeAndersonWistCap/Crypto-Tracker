"""Project round 3 — Maple, plus freezing Fluid (Jake 2026-11-11). No network."""
import json

import build_workbook as bw
import config
import done_check
import project_freeze as pf


class _R:
    def D(self, r, metric, win):                     # noqa: N802 — the Refs interface
        return f"[{metric}]"


def test_0_fluid_is_frozen_with_round_2s_moves_declared():
    assert config.FROZEN_PROJECTS["Fluid"]["baseline"] == "tests/fixtures/frozen_fluid.json"
    base = json.loads(pf.baseline_path("Fluid").read_text(encoding="utf-8"))
    assert len(base) > 300
    moves = config.FROZEN_MOVES["Fluid"]
    # round 2's 11 moves; round 3B's 18 (PR3B.1: Base / Plasma (b) legs pinned, the dumps read BLOCKED) join them
    pr2 = {k: d for k, d in moves.items() if d["why"].startswith("PR2.")}
    assert len(pr2) == 11 and all(d["date"] == "2026-11-10" for d in pr2.values())
    rest = {k: d for k, d in moves.items() if k not in pr2}
    assert len(rest) == 18 and all(d["date"] == "2026-11-11" and d["why"].startswith("PR3B.1") for d in rest.values())
    assert all(base[k] == pf._norm(d["to"]) for k, d in moves.items())          # the baseline holds every declared value


def test_1_maples_three_revenue_lines_follow_the_existing_buyback_credit():
    s = config.revenue_split("Maple")
    assert (s["fees"]["metric"], s["fees"]["primary"]) == ("fees_usd", False)
    assert (s["revenue"]["metric"], s["revenue"]["primary"]) == ("revenue_usd", True)
    assert (s["holder_revenue"]["metric"], s["holder_revenue"]["primary"]) == ("holder_value_revenue_usd", True)
    # the config's own credit, followed: revenue-funded, held (not distributed: staking ended)
    assert config.holder_funding("Maple", "2026-09-01")[0] == "revenue"
    assert config.buyback_destination_shares("Maple")["hold"] == 1.0
    assert config.PROJECT_BY_NAME["Maple"]["staking_sunset"]["ended"] == "2025-11"
    assert "ssf_selling_question" in s["holder_revenue"]["route"]                # the open question is said
    assert config.revenue_tier("Maple")[1].startswith("PRIMARY (first-party, not on chain)")


def test_2_insider_share_and_leakage_i_are_a_documented_limitation():
    t = config.INSIDER_SHARE_MONITOR["Maple"]["not_wired"]
    assert t.startswith("n/a — DOCUMENTED LIMITATION: no first-party list ties stSYRUP stakers to Maple")
    assert len(t) <= 250 and "MISSING" not in t                                 # a string inside a cell formula
    assert "block 26,391,252" in t and "block 26,391,252" in config.INSIDER_SHARE_MONITOR["Maple"]["what"]
    e = config.declared_na_reason("Maple", t)
    assert e and e["read_on"] == "2026-11-11" and "address-registry" in e["source"]
    hits = [{"project": "Maple", "tab": "audit:CP Comparability", "header": "Insider share of staked / locked supply",
             "code": "n/a", "reason": t}]
    assert done_check.judge_suspects(hits, "Maple", []) == []


def test_3_the_float_subtracts_the_funds_holdings_once():
    """Maple's SSF chart counts the Fund's Safe 0x58BE…, which circulating already subtracts (decision 1, 2026-10-12):
    the float subtracts the chart LESS the Safe. Jake's live figures: circulating 1,176,793,291, the chart 80,972,899,
    the Safe 48,624,987.59 (block 26,391,252) -> held inside circulating 32,347,911, float 1,144,445,380 (it read
    1,095,820,392: the Safe taken out twice)."""
    h = config.held_buyback_float("Maple")
    assert (h["held"], h["less"]) == ("ssf_holdings_tokens", "maple_ssf_safe_tokens")
    assert config.PROJECT_BY_NAME["Maple"]["contracts"]["noncirc_ssf_safe"]["component_metric"] == "maple_ssf_safe_tokens"
    assert config.METRICS["maple_ssf_safe_tokens"]["only_projects"] == ("Maple",)
    f = bw._held_inside(_R(), 1, config.PROJECT_BY_NAME["Maple"])
    assert f == ("IF(AND(ISNUMBER([ssf_holdings_tokens]),ISNUMBER([maple_ssf_safe_tokens])),"
                 "MAX(0,[ssf_holdings_tokens]-[maple_ssf_safe_tokens]),NA())")
    circ, chart, safe = 1_176_793_291, 80_972_899, 48_624_987.59
    assert round(circ - max(0, chart - safe)) == 1_144_445_380
    # a project without `less` keeps its held stock as it was (NEAR, Sky, Chainlink, Fluid)
    for n in ("Near", "Sky", "Chainlink", "Fluid"):
        hm = config.held_buyback_float_metric(n)
        assert bw._held_inside(_R(), 1, config.PROJECT_BY_NAME[n]) == (f"[{hm}]" if hm else None)
    assert "less maple_ssf_safe_tokens" in bw._held_buyback_note(config.PROJECT_BY_NAME["Maple"])
