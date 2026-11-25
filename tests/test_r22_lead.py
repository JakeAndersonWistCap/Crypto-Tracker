"""Round 22 (lead): Hyperliquid's A4 on every burn (item 2); the accepted named gaps (item 6)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402


def test_hyperliquid_a4_reads_every_burn_as_a1_does():
    hl = config.PROJECT_BY_NAME["Hyperliquid"]
    assert hl["a4_burn_metric"] == hl["a4_net_burn_metric"] == "total_burn_tokens"
    assert config.a4_net_burn_metric("Hyperliquid") == "total_burn_tokens"
    c = config.CREDIBILITY["Hyperliquid"]
    assert c["a4_gross_burn"]["formula"] == c["a1_gross_burn"]["formula"]
    assert c["a4_gross_burn"]["args"] == c["a1_gross_burn"]["args"]
    # the HyperEVM base fee stays out (inferred, unsubtracted)
    assert "base" not in " ".join(b["metric"] for b in config.burn_series("Hyperliquid"))


def test_the_three_accepted_named_gaps_never_block_ready():
    from datetime import date
    import done_check as dc
    for project, reason in (
            ("Uniswap", "n/a — World Chain not read since 2026-07-25: ~0.2% of inflow — a named gap"),
            ("Pendle", "BLOCKED — the sum of … is not formed: emissions_tokens_gauge_berachain stored nothing yet"),
            ("Hyperliquid", "the HyperEVM base fee burn is not read (INFERRED ~96k lifetime)")):
        assert config.accepted_named_gap(project, reason), project
        ok, cls, why = dc.classify("n/a", reason, date(2026, 11, 25))
        assert ok and cls == "accepted" and "named gap accepted" in why, (project, why)
    # a gap is accepted for its own project only; any other reason still judged
    assert config.accepted_named_gap("Sky", "World Chain not read since 2026-07-25") is None
    assert dc.classify("n/a", "no rows stored", date(2026, 11, 25))[1] != "accepted"


def test_an_accepted_gap_on_the_coverage_tab_is_not_a_blocker():
    import done_check as dc

    class _B:
        asof = None
        awb = {}
    cov = [{"Project": "Uniswap", "Status": "BROKEN", "Tab": "A3 Revenue Buyback", "Column": "Revenue",
            "Reason / route / error": "World Chain not read since 2026-07-25: ~0.2% of inflow"}]
    assert dc.judge_audit(_B(), "Uniswap", cov, {}) == []
