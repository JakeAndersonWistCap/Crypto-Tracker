"""Round 11 (Jake 2026-11-18/19). No network."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402

BASIS = "derived:circulating_used[circulating_supply_first_party]"


def _circ(name, days, values, sources=None):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": name, "metric": "circulating_supply_used",
                         "value": values, "tier": 1, "source": sources or [BASIS] * len(days)})


# ================================================================= 1: net supply change across a hole
def test_1_the_changes_telescope_to_closing_minus_opening_across_holes(monkeypatch):
    """Hyperliquid on Jake's store: 38 of 49 days held. With every reading on one basis, the changes across the holes
    are multi-day changes and their sum over the window is closing − opening, to the token."""
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Hyperliquid"]])
    asof = pd.Timestamp("2026-12-29")
    days = [d for d in pd.date_range("2026-09-30", asof)
            if str(d.date()) not in {"2026-10-02", "2026-10-03", "2026-10-04", "2026-10-17", "2026-11-07", "2026-11-08"}]
    vals = [539_000_000.0 + 7_000.0 * i + (i % 5) * 311.0 for i in range(len(days))]
    g = {("Hyperliquid", "circulating_supply_used"): _circ("Hyperliquid", days, vals)}
    bw._circulating_change_views(g, asof)
    ch = g[("Hyperliquid", "circulating_change_tokens")]
    assert ch["value"].sum() == pytest.approx(vals[-1] - vals[0], abs=1e-6)        # opening 09-30, closing 12-29
    assert bw.CIRC_CHANGE_GAPS["Hyperliquid"] == []
    from fetch.validate import _span_days
    cov = {d - pd.Timedelta(days=i) for d, s in zip(ch["date"], ch["source"]) for i in range(_span_days(s))}
    assert len([d for d in cov if asof - pd.Timedelta(days=90) < d <= asof]) == 90   # every day of the window is held


def test_1_a_hole_on_the_opening_day_is_not_bridged(monkeypatch):
    """An as-of whose opening day (as-of − 90) falls in a hole: the change that straddles it began before the window —
    it is left out, its days are not covered, and the reason names the two readings either side."""
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Hyperliquid"]])
    asof = pd.Timestamp("2026-12-31")                       # opening 2026-10-02: a hole (10-02..10-04)
    days = [d for d in pd.date_range("2026-09-30", asof) if str(d.date()) not in {"2026-10-02", "2026-10-03", "2026-10-04"}]
    g = {("Hyperliquid", "circulating_supply_used"): _circ("Hyperliquid", days, [5e8 + 1000.0 * i for i in range(len(days))])}
    bw._circulating_change_views(g, asof)
    ch = g[("Hyperliquid", "circulating_change_tokens")]
    assert pd.Timestamp("2026-10-05") not in set(ch["date"])
    gaps = bw.CIRC_CHANGE_GAPS["Hyperliquid"]
    assert len(gaps) == 1 and "2026-10-01" in gaps[0][1] and "2026-10-05" in gaps[0][1]


def test_1_a_change_across_a_hole_on_two_bases_is_still_no_change(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Hyperliquid"]])
    g = {("Hyperliquid", "circulating_supply_used"): _circ(
        "Hyperliquid", ["2026-10-01", "2026-10-02", "2026-10-06", "2026-10-07"], [100.0, 101.0, 300.0, 301.0],
        [BASIS, BASIS, "derived:circulating_used[other]", "derived:circulating_used[other]"])}
    bw._circulating_change_views(g)
    ch = g[("Hyperliquid", "circulating_change_tokens")]
    assert ch["value"].tolist() == [1.0, 1.0] and "basis changes" in bw.CIRC_CHANGE_GAPS["Hyperliquid"][0][1]
