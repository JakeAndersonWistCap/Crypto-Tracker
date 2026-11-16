"""Rounds 6B + 7 (Jake 2026-11-16): housekeeping, Sky 6B, Hyperliquid 7. Behaviour and values, not cell wording.
No network."""
import subprocess
from pathlib import Path

import pandas as pd
import pytest

import config

ROOT = Path(__file__).resolve().parents[1]


# ============================================================================================ A0: the gate's two
def test_a0_the_s13_concepts_list_skys_series():
    import spec
    c = spec.concepts()
    assert "revenue_usd_ba" in c["fees"]["inputs"]
    assert "sky_revenue_after_savings_usd_ba" in c["revenue"]["inputs"]


# ======================================================================================= A2c: .ps1 files are ASCII
def test_a2c_every_tracked_ps1_is_ascii():
    files = subprocess.check_output(["git", "ls-files", "*.ps1"], cwd=ROOT, text=True).split()
    assert files
    for f in files:
        assert (ROOT / f).read_bytes().isascii(), f


# ============================================================================ A2a: Pendle's revenue row like for like
def test_a2a_defillama_breakdown_sums_the_named_chains():
    from fetch.base import FetchOutput
    from fetch.llama import DefiLlama
    dl = DefiLlama.__new__(DefiLlama)
    day = 1_794_787_200
    dl._memo = {("pendle-v2", "dailyRevenue"): {"totalDataChartBreakdown": [
        [day, {"Ethereum": {"Pendle V2": 2500}, "Robinhood Chain": {"Pendle V2": 3}, "Hyperliquid L1": {"Pendle V2": 1.5}}],
        [day + 86400, {"Ethereum": {"Pendle V2": 10}}]]}}
    p = config.PROJECT_BY_NAME["Pendle"]
    out = FetchOutput()
    dl._chain_breakdown(p, "pendle-v2", p["defillama_chain_breakdown"][0], 10_000, out)
    v = out.frame().sort_values("date")["value"].tolist()
    assert v == pytest.approx([4.5, 0.0])             # a chain absent on a day: DefiLlama booked nothing there


def test_a2a_pendles_unpriced_leg_sums_its_markets_on_the_two_chains():
    from fetch.base import FetchOutput
    from fetch.pendle_epochs import PendleEpochs
    mk = lambda c, a, b: {"chainId": c, "protocolFeeUsd": a, "realizedYtFeeUsd": b}  # noqa: E731
    payload = {"epochs": [{"epochStart": "2026-09-08T00:00:00.000Z", "isOngoing": False,
                           "totals": {"protocolFeeUsd": 100.0, "realizedYtFeeUsd": 20.0, "poolFeeUsd": 150.0},
                           "markets": [mk(1, 70, 10), mk(4663, 20, 5), mk(999, 10, 5)]}]}
    out = FetchOutput()
    PendleEpochs(get=lambda url: payload)._market_revenue(
        "Pendle", config.PROJECT_BY_NAME["Pendle"]["spendle_epochs"], pd.Timestamp("2026-11-16"), out)
    f = out.frame()
    assert f[f.metric == "pendle_epoch_market_revenue_unpriced_usd"]["value"].tolist() == [40.0]


def _pendle_long(unpriced=True):
    days = pd.date_range("2026-09-08", periods=28)
    rows = [(d, "revenue_usd", 100.0) for d in days] + [(d, "revenue_usd_defillama", 92.0) for d in days]
    rows += [(pd.Timestamp("2026-09-08"), "pendle_epoch_market_revenue_usd", 1300.0),
             (pd.Timestamp("2026-09-22"), "pendle_epoch_market_revenue_usd", 1300.0)]
    if unpriced:
        rows += [(pd.Timestamp("2026-09-08"), "pendle_epoch_market_revenue_unpriced_usd", 140.0),
                 (pd.Timestamp("2026-09-22"), "pendle_epoch_market_revenue_unpriced_usd", 140.0)]
        rows += [(d, "revenue_usd_defillama_unpriced", 2.0) for d in days]
    return pd.DataFrame([{"date": d, "project": "Pendle", "metric": m, "value": v} for d, m, v in rows])


def test_a2a_the_row_is_judged_like_for_like_and_waits_on_its_legs():
    import credibility as cred
    args = config.CREDIBILITY["Pendle"]["in_revenue"]["ref"]["args"]
    kw = {k: v for k, v in args.items() if k not in ("side", "use_views")}
    asof = pd.Timestamp("2026-10-07")
    a, _d, _h = cred._pendle_basis_vs_defillama("Pendle", {}, _pendle_long(), asof, side="ours", **kw)
    b, _d, how = cred._pendle_basis_vs_defillama("Pendle", {}, _pendle_long(), asof, side="ref", **kw)
    assert a == pytest.approx(2800 - 280) and b == pytest.approx(2576 - 56)
    assert "LIKE FOR LIKE" in how and "Robinhood Chain" in how and "HyperEVM" in how
    assert config.CREDIBILITY["Pendle"]["in_revenue"]["ref"]["tol"] == 10.0          # not widened
    v, _d, why = cred._pendle_basis_vs_defillama("Pendle", {}, _pendle_long(False), asof, side="ref", **kw)
    assert v is None and "unpriced" in why                                         # unknown, never the old basis


# ============================================================ A2b: Fluid's allowance renewed on a new read; Pendle not
def test_a2b_fluids_allowance_is_renewed_with_its_read_and_pendles_lapses():
    hit = "CP SUBSIDY COVER = revenue ÷ $ supply-side incentives (b): 56.5 in the previous run, now BLOCKED"
    a = config.regression_allowance("Fluid", hit, on="2026-12-01")
    assert a and a["until"] == "2026-12-16" and "888.29" in a["reason"] and "rpc.plasma.to" in a["source"]
    assert config.regression_allowance("Fluid", hit, on="2026-12-17") is None          # lapses on its own date
    assert config.regression_allowance("Pendle", hit, on="2026-12-01") is None         # not renewed (chains wired)


# ================================================================= A1: gate speed — slow items marked and run first
def test_a1_slow_tests_are_marked_and_collected_first_and_none_is_dropped():
    import conftest

    class _Item:
        def __init__(self, f, n):
            self.fspath, self.name, self.marks = f, n, []

        def add_marker(self, m):
            self.marks.append(m)
    items = [_Item("/x/tests/test_adapters.py", "test_quick"), _Item("/x/tests/test_round_p3_6.py", "test_a"),
             _Item("/x/tests/test_adapters.py", "test_headline_diff_runs_on_a_store_circulating"),
             _Item("/x/tests/test_r7_round.py", "test_b")]
    before = list(items)
    conftest.pytest_collection_modifyitems(None, None, items)
    assert sorted(map(id, items)) == sorted(map(id, before))                     # nothing dropped
    assert [i.name for i in items[:2]] == ["test_a", "test_headline_diff_runs_on_a_store_circulating"]
    assert all(any(m.name == "slow" for m in i.marks) for i in items[:2])
    assert not any(m.name == "slow" for i in items[2:] for m in i.marks)
