"""ROUND D6 (Jake 2026-10-27, head 08de221).

1 a daily flow's covered days are the days its rows cover — an interior day with no row is a hole, never a zero (span
tags, derived rows' inherited spans and untagged d(stock) rows counted; the lag after the last row is not a hole).
3 Maple's transparency content read from the pages it moved to. 4 Sky's endowment purchase named on the Flows tab, never
a claimed 0. 5 a Near DEX day whose THORSwap leg drops to 0 is held. 6 the Fluid reference's basis explained.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402


def _g(project, metric, days, values, sources):
    if isinstance(sources, str):
        sources = [sources] * len(days)
    return pd.DataFrame({"date": pd.to_datetime(days), "project": project, "metric": metric,
                         "value": [float(v) for v in values], "tier": 1, "source": sources})


# ================================================================================================== 1
def test_1_an_interior_day_with_no_row_is_a_hole_and_the_lag_after_the_last_row_is_not():
    import build_workbook as bw
    days = [d for d in pd.date_range("2026-09-01", "2026-09-28") if d not in
            (pd.Timestamp("2026-09-10"), pd.Timestamp("2026-09-11"))]          # two-day hole; last row 09-28
    cov = bw._covered_day_sets({("Ethereum", "fees_usd"): _g("Ethereum", "fees_usd", days, [1.0] * len(days),
                                                            "defillama")})[("Ethereum", "fees_usd")]
    holes = bw._interior_holes(cov, pd.Timestamp("2026-08-31"), pd.Timestamp("2026-09-30"))
    assert holes == [pd.Timestamp("2026-09-10"), pd.Timestamp("2026-09-11")]   # 09-29..30: lag, not holes


def test_1_a_span_tagged_row_covers_its_days_and_a_derived_row_inherits_them():
    import build_workbook as bw
    import wiring
    tok = _g("Hyperliquid", "actual_buyback_tokens", ["2026-10-01", "2026-10-05"], [10, 40],
             ["hypercore_info:x:delta", "hypercore_info:x:delta[span=4d]"])
    usd = _g("Hyperliquid", "actual_buyback_usd", ["2026-10-01", "2026-10-05"], [100, 400], "derived:tokens*price")
    wiring.reset()
    groups = wiring.TrackingDict({("Hyperliquid", "actual_buyback_tokens"): tok})

    def view(gr):                                 # a view that reads tokens and writes the $ twin
        gr.get(("Hyperliquid", "actual_buyback_tokens"))
        gr[("Hyperliquid", "actual_buyback_usd")] = usd
    view(groups)
    cov = bw._covered_day_sets(dict(groups))
    for m in ("actual_buyback_tokens", "actual_buyback_usd"):
        assert bw._interior_holes(cov[("Hyperliquid", m)], pd.Timestamp("2026-09-30"), pd.Timestamp("2026-10-05")) == []
    wiring.reset()
    # without the graph the $ twin has no span of its own: 10-02..04 read as holes — the inheritance is what closes them
    cov2 = bw._covered_day_sets({("Hyperliquid", "actual_buyback_usd"): usd})
    assert len(bw._interior_holes(cov2[("Hyperliquid", "actual_buyback_usd")], pd.Timestamp("2026-09-30"),
                                  pd.Timestamp("2026-10-05"))) == 3


def test_1_an_untagged_d_of_a_stock_row_covers_the_days_since_the_stocks_previous_reading():
    import build_workbook as bw
    stock = _g("Ethereum", "total_supply_protocol", ["2026-10-01", "2026-10-05"], [1, 2], "etherscan:ethsupply2.supply")
    flow = _g("Ethereum", "gross_issuance_tokens", ["2026-10-01", "2026-10-05"], [3000, 12000],
              "derived:d_total_supply_protocol+burn")            # stored before the fetch tagged spans (2026-10-09)
    cov = bw._covered_day_sets({("Ethereum", "total_supply_protocol"): stock,
                                ("Ethereum", "gross_issuance_tokens"): flow})[("Ethereum", "gross_issuance_tokens")]
    assert bw._interior_holes(cov, pd.Timestamp("2026-09-30"), pd.Timestamp("2026-10-05")) == []


def test_1_a_q0_with_a_hole_is_annualised_over_the_days_covered_and_names_them(monkeypatch):
    import build_workbook as bw
    asof = pd.Timestamp("2026-10-13")
    days = [d for d in pd.date_range("2026-01-01", "2026-10-12") if not
            (pd.Timestamp("2026-09-01") <= d <= pd.Timestamp("2026-09-04"))]
    g = _g("Fluid", "fees_usd", days, [100.0] * len(days), "defillama")
    bw.COVERED_SETS.clear()
    bw.COVERED_SETS.update(bw._covered_day_sets({("Fluid", "fees_usd"): g}))
    s = bw._daily(g)
    q_start = asof - pd.Timedelta(days=90)
    span, _ = bw._window_coverage(s, q_start, asof)
    holes = bw._interior_holes(bw.COVERED_SETS[("Fluid", "fees_usd")], q_start, asof, s.index.min())
    assert span == 90 and len(holes) == 4                      # 86 days covered: the Q0 sum is a 86-day sum
    assert bw._day_ranges(holes) == "2026-09-01..09-04"
    bw.COVERED_SETS.clear()


def test_1_a_declared_source_hole_is_never_subtracted_twice():
    src = (ROOT / "build_workbook.py").read_text(encoding="utf-8")
    assert "lost = (sum(1 for d_ in pd.date_range(h0, h1) if d_ in cov_set) if cov_set" in src


# ================================================================================================== 2
def test_2_morphos_10_22_volume_flag_is_answered_with_the_exchange_evidence():
    a = config.review_answered({"project": "Morpho", "metric": "volume_usd", "reason": "change_threshold",
                                "date": "2026-10-22"})
    assert a and a["decision"] == "ACCEPT" and "Coinbase" in a["why"] and "SPEC D6-2" in a["why"]
    assert config.review_answered({"project": "Morpho", "metric": "volume_usd", "reason": "change_threshold",
                                   "date": "2026-10-23"}) is None          # that day only


# ================================================================================================== 3
def test_3_maple_fields_are_read_from_the_pages_they_moved_to(monkeypatch, tmp_path):
    import html as _html
    import json
    from fetch import maple_transparency as mt
    from fetch import scrape
    from fetch.base import FetchOutput
    monkeypatch.setattr(scrape, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(scrape, "robots_verdict", lambda url: (True, "test"))

    def enc(v):
        if isinstance(v, dict):
            return [0, {k: enc(x) for k, x in v.items()}]
        if isinstance(v, list):
            return [1, [enc(x) for x in v]]
        return [0, v]
    ms = lambda d: int(pd.Timestamp(d).timestamp() * 1000)  # noqa: E731
    days = pd.date_range("2026-09-01", "2026-09-29")
    ssf = {"datasets": enc({"ALL": [{"ts": ms(d), "syrupHoldings": 80e6 - i, "liquidAssetsUsd": 1} for i, d in
                                     enumerate(days)]})}
    rev = {"datasets": enc({"ALL": [{"ts": ms("2026-08-31"), "revenueUsd": 1_470_979.15}]})}
    fin = "".join(f'<astro-island uid="{i}" props="{_html.escape(json.dumps(p))}"></astro-island>'
                  for i, p in enumerate((ssf, rev)))
    syrup = ("<p>Showing 1-1 of 1</p><table><tr><td>Aug 2026</td><td>$147,098.00</td><td>676,293.73</td>"
             "<td>$0.2175</td></tr></table>")
    pages = {"https://maple.finance/transparency": "<a href='/transparency/syrup'>SYRUP</a>",
             "https://maple.finance/transparency/financials": fin, "https://maple.finance/transparency/syrup": syrup}
    asked = []

    def get(url):
        asked.append(url)
        return pages[url]
    out = FetchOutput()
    mt.MapleTransparency(get=get).run([config.PROJECT_BY_NAME["Maple"]], 30, out)
    f = out.frame()
    assert len(f[f.metric == "ssf_holdings_tokens"]) == len(days)
    assert f[f.metric == "revenue_usd"]["value"].tolist() == [1_470_979.15]
    assert f[(f.metric == "actual_buyback_tokens") & (f.source == "maple_page")]["value"].tolist() == [676_293.73]
    assert not (f.metric == "treasury_holding_tokens").any()
    msgs = " | ".join(e.message for e in out.log)
    assert "different quantity, not used" in msgs                         # the tile: gone, said why
    assert "ssf_net_outflow_tokens: not stored (SPEC D5-3c)" in msgs and "ssf_net_outflow_tokens: no astro" not in msgs
    assert sorted(set(asked)) == sorted(pages)                             # each page fetched once
