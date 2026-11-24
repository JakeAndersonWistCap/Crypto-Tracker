"""Round 21, the lead's items (Jake 2026-11-25): item 1 (the cp_nfp Trend row declared on the nine frozen projects)
and item 8 (the three headlines blocked until the history is complete; the basis of two Aerodrome views ignores the
figures written into their source text; Chainlink's netted release a declared component)."""
import config
import trajectory


def test_1_the_new_trend_row_is_declared_on_all_nine_frozen_projects():
    for p in config.FROZEN_PROJECTS:
        k = [x for x in config.FROZEN_MOVES.get(p, {}) if "Trend — cp_nfp" in x]
        assert k, p


def test_8_the_three_blocked_headlines_are_allowed_and_only_they():
    a = config.regression_allowance
    assert a("Aerodrome", "CP | SUBSIDY COVER = revenue ÷ $ supply-side incentives (b)")
    assert a("Aerodrome", "CP | NET HOLDER YIELD = holder value yield − dilution")
    assert a("Chainlink", "CP | NET HOLDER YIELD = holder value yield − dilution")
    assert a("Chainlink", "CP | SUBSIDY COVER = revenue ÷ $ supply-side incentives (b)") is None
    assert a("Sky", "CP | NET HOLDER YIELD = holder value yield − dilution") is None


def test_8_a_day_count_in_the_source_text_is_not_a_new_measuring_point():
    b6 = trajectory.basis_of("rewards_tokens_payday_annual", "derived:holders_revenue_usd / same-day price_usd, 6 day(s), annualised")
    b88 = trajectory.basis_of("rewards_tokens_payday_annual", "derived:holders_revenue_usd / same-day price_usd, 88 day(s), annualised")
    assert b6 == b88
    # other metrics keep their numbers: a changed ratio IS a different basis
    assert trajectory.basis_of("gross_issuance_tokens", "derived:x0.044") != trajectory.basis_of("gross_issuance_tokens", "derived:x0.05")


def test_a_decision_5_unranked_is_not_a_rule_3_blocker_on_either_path():
    """Morpho on the 20 Nov export: the rank reads "unranked" with its 'off' label (decision 5). The rule-3 scan of both
    workbooks (judge_suspects) must accept it as classify() does — it did not, so Morpho read NOT READY."""
    import done_check
    reason = ("Unranked — off: the fee switch is off: fee 0 on every listed market. A paused or off programme is shown "
              "with its label and not ranked (Jake 2026-11-25, decision 5); its key shows 0 only where the data says 0.")
    hit = {"project": "Morpho", "tab": "reader:Summary", "header": "Rank in archetype", "code": "unranked",
           "reason": reason}
    assert done_check.judge_suspects([hit], "Morpho", []) == []
    other = dict(hit, reason="Unranked — no fee switch exists")       # a mechanism-absent reason without the decision
    assert done_check.judge_suspects([other], "Morpho", []) != [] or not __import__("rule3").is_suspect(other["reason"])
