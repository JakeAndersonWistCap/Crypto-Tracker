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


def _bb_groups(last_source):
    import pandas as pd
    days = pd.date_range("2026-08-20", "2026-11-23")
    lead = pd.DataFrame({"date": days, "value": 1.0, "project": "Pendle", "metric": "actual_buyback_tokens",
                         "source": ["pendle_api:sPendleHistoricalData.buybackAmounts"] * (len(days) - 1) + [last_source],
                         "tier": 2})
    fees = lead.assign(metric="fees_usd", source="defillama")
    return {("Pendle", "actual_buyback_tokens"): lead, ("Pendle", "fees_usd"): fees}


def test_pendle_lag_match_only_for_a_publish_lag():
    """R22 (Jake's store run: Pendle's payout ratio and holder share BLOCKED, fees_usd_lagm 8 of 90 days): the R5.3 match
    cuts the fee side only while the buyback's last day is read from the lagged API point. Read from the buyback contract
    (R21 item 5, dated the day bought) there is no publish lag, and each side keeps its own coverage."""
    import build_workbook as bw
    g = _bb_groups("pendle_buyback_chain:ethereum")
    bw._lag_matched_views(g, "2026-11-24")
    st = bw.LAG_MATCH["Pendle"]
    assert st["ok"] is False and st["not_lagged"] and ("Pendle", "fees_usd_lagm") not in g
    assert "not a publish lag" in bw.lag_matched_note({"name": "Pendle"})
    g = _bb_groups("pendle_api:sPendleHistoricalData.buybackAmounts")
    bw._lag_matched_views(g, "2026-11-24")
    assert bw.LAG_MATCH["Pendle"]["ok"] is True and ("Pendle", "fees_usd_lagm") in g


def test_a_burn_begun_inside_the_window_is_read_since_it_began():
    """R22 (Jake 2026-11-25): Aethir's burn ÷ issuance read MISSING (the burn live 2026-11-02, the release stored from
    2026-10-07). Both A4 legs are read from the burn's start, the cell labelled with that date; a burn that began
    before the window is not re-based."""
    import pandas as pd
    import build_workbook as bw
    import config
    assert config.burn_start("Aethir") == "2026-11-02"
    def rows(m, d0):
        days = pd.date_range(d0, "2026-11-24")
        return pd.DataFrame({"date": days, "value": 1.0, "project": "Aethir", "metric": m, "source": "x", "tier": 2})
    g = {("Aethir", "gross_burn_tokens"): rows("gross_burn_tokens", "2026-08-27"),
         ("Aethir", "pool_release_tokens"): rows("pool_release_tokens", "2026-10-07")}
    bw._burn_since_views(g, "2026-11-24")
    st = bw.BURN_SINCE["Aethir"]
    assert st["start"] == "2026-11-02" and st["days"] == 23
    for m in ("gross_burn_tokens", "pool_release_tokens"):
        v = g[("Aethir", f"{m}_sinceburn")]
        assert pd.to_datetime(v["date"]).min() == pd.Timestamp("2026-11-02") and len(v) == 23
        assert bw.burn_since({"name": "Aethir"}, m) == f"{m}_sinceburn"
    assert "since 2026-11-02" in bw.burn_since_fmt("Aethir")
    assert "2026-11-02" in bw.burn_since_note({"name": "Aethir"})
    # the window that opens after the start: nothing re-based
    bw._burn_since_views(g, "2027-03-01")
    assert "Aethir" not in bw.BURN_SINCE and bw.burn_since({"name": "Aethir"}, "gross_burn_tokens") == "gross_burn_tokens"
