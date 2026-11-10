"""Project round 2 — Fluid (Jake 2026-11-10). No network."""
import build_workbook as bw
import config
import credibility as cred
import done_check


def test_1_every_fluid_na_reason_is_declared_with_its_source_and_read_date():
    """PR2.1: the rule-3 n/a cells (staking yield, staker yield, the lock and reserve gaps, buyback to locked supply,
    uniformity) carry reasons checked against Fluid's own contract index; done_check accepts them as declared."""
    reasons = [
        "n/a — FLUID staking is not deployed — nothing is staked to be paid",
        "n/a — locked_tokens (now): not applicable to this project — FLUID STAKING IS NOT DEPLOYED. The April 2026 …",
        "n/a — Data: not applicable to this project — NO BUYBACK FUND EXISTS SEPARATELY. Fluid's own materials: …",
        config.declared_na_text("Fluid", "UNIFORMITY = whole-token yield ÷ staker yield"),
    ]
    for r in reasons:
        e = config.declared_na_reason("Fluid", r)
        assert e and e["source"].startswith("https://docs.fluid.io") and e["read_on"] == "2026-11-10", r
    hits = [{"project": "Fluid", "tab": "reader:A3 Revenue Buyback", "header": "Staking yield (tokens)", "code": "n/a",
             "reason": reasons[0]},
            {"project": "Fluid", "tab": "audit:CP Comparability", "header": "UNIFORMITY", "code": "n/a",
             "reason": reasons[3]}]
    assert done_check.judge_suspects(hits, "Fluid", []) == []
    # a reason nobody declared stays flagged (leakage on Jake's store: not seen here, so not declared)
    other = [{"project": "Fluid", "tab": "audit:CP Comparability", "header": "LEAKAGE (ii)", "code": "n/a",
              "reason": "n/a — no supply-side incentive (b) series on file"}]
    assert len(done_check.judge_suspects(other, "Fluid", [])) == 1
    assert config.declared_na_text("Fluid", "UNIFORMITY").startswith("n/a — no staker yield exists")
    assert not config.declared_na_reason("Aave", reasons[0])                       # Fluid's only


def test_2_the_three_revenue_lines_are_declared_with_their_routes():
    s = config.revenue_split("Fluid")
    assert s["fees"]["metric"] == "fees_usd" and s["fees"]["primary"] is False
    assert s["revenue"]["metric"] == "revenue_usd" and s["revenue"]["primary"] is False
    assert s["holder_revenue"]["metric"] == "holder_value_revenue_usd" and s["holder_revenue"]["primary"] is True
    assert "2026-03-21" in s["holder_revenue"]["route"] and "2026-05-11" in s["holder_revenue"]["route"]
    assert "0 while the buyback is halted" in s["holder_share"]["route"]
    assert config.revenue_tier("Fluid")[1].startswith("SECONDARY — DefiLlama 'fluid' dailyRevenue")
    lab = config.revenue_tier_label("Fluid", bw.CP_HREV_LINE_HEAD)
    assert lab and "PRIMARY" in lab[1]
    assert "SECONDARY — aggregator only" in config.revenue_tier_label("Fluid", bw.CP_FEES_LINE_HEAD)[1]


def test_3_the_a3_key_of_a_halted_buyback_says_a_dated_measured_zero():
    note = bw.a3_key_note(config.PROJECT_BY_NAME["Fluid"])
    assert note.startswith("0, MEASURED — the buyback programme is HALTED: last execution 2026-03-20, halted by "
                           "governance 2026-05-11")
    assert "still halted on 2026-11-10" in note and "not n/a (Rule 3)" in note
    assert "HALTED" not in bw.a3_key_note(config.PROJECT_BY_NAME["Sky"])            # no other project's note moves
    assert config.PROJECT_BY_NAME["Fluid"]["buyback_programme"]["still_halted_check"]["checked_on"] == "2026-11-10"


def test_3_no_open_checks_is_a_label_only():
    rows = [{"project": "A", "id": "x", "note": "r"}, {"project": "B", "id": "y", "note": "r"}]
    s = cred.signoff(rows, ["PASS", "CHECK"])
    assert s["A"]["status"] == "NO OPEN CHECKS" and s["B"]["status"] == "OPEN"
    src = open(bw.__file__, encoding="utf-8").read()
    assert '=IF(K{r}=0,"NO OPEN CHECKS","OPEN")' in src and '"SIGNED OFF","OPEN"' not in src
