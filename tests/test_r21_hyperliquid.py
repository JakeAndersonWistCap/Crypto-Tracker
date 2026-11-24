"""ROUND 21 — HYPERLIQUID (Jake 2026-11-25): the Assistance Fund is a HELD BUYBACK (Jake's default), A4 one switch.

"6. Hyperliquid: research first, then decide. ... The default until you report is the held-buyback treatment:
circulating, off the float, A4 off. If the research shows part of it is a true burn, count that part under 2 and give
it A4, scored on what is actually burned." (docs/r21_hyperliquid_burns.md: recommendation B, not yet decided.)

1. The Fund 0xfefe…fefe is IN circulating (all or no row), OFF the free float (held buybacks), inside total supply;
   its purchases are bought and HELD. The free float does not move.
2. Every burn series holds the true burns only (Core: the fall in tokenDetails.totalSupply; the HyperEVM zero address);
   the Fund's purchases are in none of them.
3. config.HYPERLIQUID_TRUE_BURN_A4: off = A4 off (the default); on = A4 on, scored on the true burns only.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import free_float  # noqa: E402
from fetch.base import _measuring_point  # noqa: E402

HL = "Hyperliquid"
FUND = "burn_address_balance"


def _frame(metric, pairs, source="test:read"):
    return pd.DataFrame({"date": [pd.Timestamp(d) for d, _v in pairs], "project": HL, "metric": metric,
                         "value": [float(v) for _d, v in pairs], "source": source, "tier": 1,
                         "fetched_at": "2026-10-13T00:00:00Z"})


def _days(start, values):
    d0 = pd.Timestamp(start)
    return [(d0 + pd.Timedelta(days=i), v) for i, v in enumerate(values) if v is not None]


# ====================================================================================== 1. the Fund: held, circulating
def test_the_default_is_jakes_held_buyback_treatment():
    assert config.HYPERLIQUID_TRUE_BURN_A4 is False
    assert config.PROJECT_BY_NAME[HL]["archetypes"] == [3, 1]                   # A4 off
    assert config.CIRC_HELD_BUYBACK_IN[HL]["held"] == FUND
    assert config.held_buyback_float_metric(HL) == FUND                         # off the float as a held buyback
    sh = config.buyback_destination_shares(HL, "2026-10-13")
    assert (sh["burn"], sh["hold"], sh["distribute"]) == (0.0, 1.0, 0.0)        # bought and HELD, not burned
    assert config.buyback_route(HL)["metric"] == "gross_burn_tokens"            # the Fund's purchases are the buyback
    # total supply keeps the Fund (it is inside tokenDetails.totalSupply); only the zero address is taken out
    assert config.TOTAL_SUPPLY_NET[HL]["less"] == ("hyperevm_zero_address_balance",)
    # the release no longer adds the buyback back (the Fund's purchase does not leave circulating any more)
    assert config.CIRCULATING_ONCHAIN[HL]["release_first_party"]["plus"] == ("hyperevm_priority_burn_tokens",)
    assert "HELD BUYBACK" in config.CIRCULATING_ONCHAIN[HL]["noncirculating_seen"][
        "0xfefefefefefefefefefefefefefefefefefefefe"]["identity"]


def test_circulating_adds_the_fund_on_each_day_both_are_held_and_keeps_its_measuring_point():
    src = "derived:released_staked[the rule: team + foundation + grants in]"
    groups = {(HL, "circulating_supply_first_party"): _frame("circulating_supply_first_party",
                                                             _days("2026-10-11", [500.0, 510.0, 520.0]), src),
              (HL, FUND): _frame(FUND, _days("2026-10-11", [40.0, None, 42.0]), "hypercore_info:spotClearinghouseState")}
    bw._held_buyback_in_circulating_views(groups)
    out = bw._daily(groups[(HL, "circulating_supply_first_party")])
    # 10-12 has no Fund row: no circulating that day (all or no row; never the figure without the Fund)
    assert list(out.index.strftime("%Y-%m-%d")) == ["2026-10-11", "2026-10-13"]
    assert list(out.values) == [540.0, 562.0]
    g = groups[(HL, "circulating_supply_first_party")]
    assert set(g["source"].map(_measuring_point)) == {_measuring_point(src)}    # one basis, the same point
    assert all("Assistance Fund" in s for s in g["source"])                      # the added leg is named on the row
    assert bw.HELD_BUYBACK_IN_CIRC[HL] == {"days": 2, "dropped": 1, "first": "2026-10-11", "last": "2026-10-13"}
    # the restated point is declared on the series' handover
    pts = config.declared_handover(HL, "circulating_supply_first_party")["ordered_points"]
    assert any("Assistance Fund" in p for p in pts)


@pytest.fixture
def one_project(monkeypatch):
    def run(series: dict, asof):
        groups = {(HL, m): _frame(m, pairs) for m, pairs in series.items()}
        monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[HL]])
        monkeypatch.setattr(config, "RULE_IN_PENDING", {})
        free_float.views(groups, pd.Timestamp(asof))
        return groups
    return run


def test_the_fund_in_circulating_comes_off_the_float_as_held_buybacks_so_the_float_does_not_move(one_project):
    sp = free_float.parts(config.PROJECT_BY_NAME[HL])
    less = {m: _days("2026-10-01", [40, 40, 40]) for m in sp["lock"]["less"]}
    common = {"locked_tokens": _days("2026-10-01", [400, 400, 400]),
              "rule_in_off_float_tokens": _days("2026-10-01", [100, 100, 100]), **less}
    fund = [48.0, 48.5, 49.0]
    g = one_project({"circulating_supply_used": _days("2026-10-01", [1000 + f for f in fund]),
                     FUND: _days("2026-10-01", fund), **common}, "2026-10-03")
    lock = 400.0 - 40.0 * len(less)
    assert list(bw._daily(g[(HL, free_float.C_HELD)]).values) == fund
    assert list(bw._daily(g[(HL, free_float.FF)]).values) == [1000.0 - lock - 100.0] * 3     # what it was without the Fund
    # the Fund's purchase is a held buyback bought that day (− held buybacks), not a burn
    mv = free_float.MOVES[HL]
    assert list(mv["held_bought"].round(6)) == [-0.5, -0.5]
    # the Fund not read: no float (unknown is never 0), and the component is named
    g = one_project({"circulating_supply_used": _days("2026-10-01", [1048, 1048.5, 1049]), **common}, "2026-10-03")
    assert (HL, free_float.FF) not in g
    assert "burn_address_balance (held buybacks)" in free_float.MISSING[HL]


def test_the_funds_purchases_are_held_not_burned_in_the_buyback_split(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[HL]])
    groups = {(HL, "actual_buyback_tokens"): _frame("actual_buyback_tokens", _days("2026-10-01", [30_000, 31_000]))}
    bw._buyback_split_views(groups)
    assert list(bw._daily(groups[(HL, "buyback_held_tokens")]).values) == [30_000.0, 31_000.0]
    assert list(bw._daily(groups[(HL, "buyback_burned_tokens")]).values) == [0.0, 0.0]
    assert list(bw._daily(groups[(HL, "retired_buyback_tokens")]).values) == [30_000.0, 31_000.0]   # retired unchanged


def test_the_release_is_unchanged_by_the_fund_moving_in(monkeypatch):
    """d(circulating + Fund) + the HyperEVM burn = d(circulating) + the Fund's buyback + the HyperEVM burn (the old
    formula): the same release, the buyback not counted twice."""
    circ = [500.0, 503.0, 501.0]
    fund = [40.0, 41.5, 42.0]
    prio = [0.2, 0.3, 0.1]
    src = "derived:released_staked[the rule][+ the Assistance Fund]"
    groups = {(HL, "circulating_supply_first_party"): _frame("circulating_supply_first_party",
                                                             _days("2026-10-11", [c + f for c, f in zip(circ, fund)]), src),
              (HL, "hyperevm_priority_burn_tokens"): _frame("hyperevm_priority_burn_tokens", _days("2026-10-11", prio))}
    monkeypatch.setattr(config, "CIRCULATING_ONCHAIN", {HL: config.CIRCULATING_ONCHAIN[HL]})
    bw._onchain_release_views(groups)
    rel = bw._daily(groups[(HL, "pool_release_tokens")])
    old = [max(0.0, (circ[i] - circ[i - 1]) + (fund[i] - fund[i - 1]) + prio[i]) for i in (1, 2)]
    assert list(rel.round(9).values) == [round(x, 9) for x in old]


# ====================================================================================== 2. the burns: true burns only
def test_every_burn_series_holds_the_true_burns_and_never_the_fund(monkeypatch):
    bt = config.burn_total(HL)
    assert bt["components"] == ["core_burn_tokens"] and "history_leg" not in bt
    assert bt["optional_components"] == ["hyperevm_priority_burn_tokens"]
    assert bt["buyback_metric"] == "gross_burn_tokens"
    for m in (config.a1_burn_metric(HL), config.a4_burn_metric(HL), config.a4_net_burn_metric(HL)):
        assert m in ("total_burn_tokens", "burn_ex_chain_fee_tokens"), m
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[HL]])
    groups = {
        # the Fund's purchases: DefiLlama history, then its live balance change — must reach no burn series
        (HL, "gross_burn_tokens"): pd.concat([
            _frame("gross_burn_tokens", _days("2026-09-20", [25_000, 26_000]), "defillama:holders_revenue_usd/price"),
            _frame("gross_burn_tokens", _days("2026-09-22", [27_000, 28_000, 29_000]),
                   "hypercore_info:spotClearinghouseState:delta")], ignore_index=True),
        (HL, "core_burn_tokens"): _frame("core_burn_tokens", _days("2026-09-23", [4_000, 4_300]),
                                         "hypercore_info:tokenDetails.totalSupply:delta"),
        (HL, "hyperevm_priority_burn_tokens"): _frame("hyperevm_priority_burn_tokens",
                                                      _days("2026-09-20", [270, 280, 290, 300, 310])),
    }
    bw._burn_total_views(groups)
    tot = bw._daily(groups[(HL, "total_burn_tokens")])
    ex = bw._daily(groups[(HL, "burn_ex_chain_fee_tokens")])
    # no history leg: nothing before the Core leg's first day (the Fund's history is not a burn; unknown is never 0)
    assert list(tot.index.strftime("%Y-%m-%d")) == ["2026-09-23", "2026-09-24"]
    assert list(tot.values) == [4_300.0, 4_610.0]                                 # Core + the zero address
    assert list(ex.values) == [4_000.0, 4_300.0]                                  # Core only (G7 0c)
    assert set(groups[(HL, "total_burn_tokens")]["source"].map(_measuring_point)) == {
        "derived:burn_total(core_burn_tokens)"}


def test_total_supply_keeps_the_fund(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[HL]])
    groups = {(HL, "total_supply_gross"): _frame("total_supply_gross", _days("2026-10-12", [998_870_000, 998_862_000])),
              (HL, "hyperevm_zero_address_balance"): _frame("hyperevm_zero_address_balance",
                                                            _days("2026-10-12", [308_300, 308_438])),
              (HL, FUND): _frame(FUND, _days("2026-10-12", [47_980_000, 48_009_595]))}
    bw._total_supply_net_views(groups)
    assert list(bw._daily(groups[(HL, "total_supply")]).values) == [998_870_000 - 308_300.0, 998_862_000 - 308_438.0]


# ====================================================================================== 3. the switch
def test_one_switch_turns_a4_on_scored_on_the_true_burns_only_and_back_off():
    p = config.PROJECT_BY_NAME[HL]
    try:
        config.apply_hyperliquid_burn_treatment(True)
        assert p["archetypes"] == [3, 1, 4] and config.CONDITIONAL_ARCHETYPES[HL]["on"] is True
        assert config.a4_burn_metric(HL) == config.a4_net_burn_metric(HL) == config.HYPERLIQUID_TRUE_BURN_A4_METRIC
        assert config.HYPERLIQUID_TRUE_BURN_A4_METRIC in ("burn_ex_chain_fee_tokens", "total_burn_tokens")
        assert config.a4_burn_metric(HL) != "gross_burn_tokens"                 # never the Fund
        assert HL in config.METRICS["gross_burn_day_priced_usd"]["only_projects"]
        assert config.CREDIBILITY[HL]["a4_gross_burn"]["formula"] == "fall_q0"
        # the Fund stays held in both states
        assert config.buyback_destination_shares(HL, "2026-10-13")["hold"] == 1.0
        assert config.held_buyback_float_metric(HL) == FUND
    finally:
        config.apply_hyperliquid_burn_treatment(False)
    assert p["archetypes"] == [3, 1] and config.CONDITIONAL_ARCHETYPES[HL]["on"] is False
    assert config.a4_burn_metric(HL) == "burn_ex_chain_fee_tokens"
    assert config.buyback_destination_shares(HL, "2026-10-13")["hold"] == 1.0


def test_the_a4_reference_is_the_fall_of_total_supply_across_q0():
    import credibility as cr
    long = pd.DataFrame({"project": HL, "metric": "total_supply_gross", "date": pd.to_datetime(
        ["2026-07-14", "2026-08-30", "2026-10-13"]), "value": [999_100_000.0, 999_000_000.0, 998_862_000.0]})
    v, d, how = cr.FORMULAS["fall_q0"](HL, None, long, pd.Timestamp("2026-10-13"), metric="total_supply_gross")
    assert v == 238_000.0 and d == "2026-10-13" and how.startswith("−")


# ====================================================================================== the dump, one-project build
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    import measuring_guard as mg
    import project_freeze as pf
    import store as sm
    from real_dump import to_store
    d = tmp_path_factory.mktemp("r21_hl")
    pl = next(p for p in mg._payloads() if p["meta"]["project"] == HL)
    db = d / "metrics.db"
    to_store(pl, db)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sm, "DB_PATH", str(db))
        mp.setitem(sys.modules, "recalc", None)
        mp.setitem(os.environ, "TOKEN_METRICS_STORE_VALUES", "1")
        config.pin_frozen_probe()
        st = sm.Store(str(db))
        try:
            bw.build_workbook(st, d / "audit.xlsx", asof=pd.Timestamp(pl["meta"]["asof"]), only=[HL],
                              reader_path=d / "reader.xlsx")
        finally:
            st.close()
        return pf.cells(d / "audit.xlsx", d / "reader.xlsx", HL)


CP = "audit|CP Comparability|"


def _cell(cells, prefix):
    hits = [v for k, v in cells.items() if k.startswith(prefix)]
    assert len(hits) == 1, (prefix, len(hits))
    return hits[0]


def test_on_the_dump_circulating_gains_the_fund_and_the_free_float_does_not_move(built):
    # was 539,758,896 (the Fund out as burned); + the Fund's 48,009,594.74 on 2026-10-13
    assert _cell(built, CP + "Circulating supply (tokens)") == pytest.approx(587_768_491, abs=1)
    assert _cell(built, CP + "Held buyback inside circulating") == pytest.approx(48_009_594.7, abs=1)
    assert _cell(built, CP + "FREE FLOAT (tokens)") == pytest.approx(99_462_451.6, abs=1)          # unchanged
    assert _cell(built, CP + "Total supply (tokens)") == pytest.approx(998_553_300, abs=1)          # was 950,543,705
    assert _cell(built, CP + "Market cap ($)") == pytest.approx(47_589_620_000, rel=1e-6)           # was 43,702,446,000


def test_on_the_dump_the_buyback_is_held_and_the_burns_are_the_true_burns(built):
    a3 = "audit|A3 Revenue Buyback|"
    assert _cell(built, a3 + "Actual buyback Q0 HELD") == pytest.approx(2_380_273.5, abs=1)          # was 0
    assert _cell(built, a3 + "Actual buyback Q0 BURNED") == 0.0                                        # was 2,380,273.5
    assert _cell(built, a3 + "Actual buyback Q0 RETIRED") == pytest.approx(2_380_273.5, abs=1)       # unchanged
    # A1's every burn: the Core burns + the zero address on the days the Core leg is read (was 2,474,408.02 with the Fund)
    assert _cell(built, "audit|A1 Infrastructure|Gross burn Q0 (tokens)") == pytest.approx(52_260.63, abs=1)


def test_on_the_dump_the_headlines(built):
    s = "reader|Summary|"
    assert _cell(built, s + "Holder value yield") == pytest.approx(0.014323244, abs=1e-8)            # was 0.0155918832
    assert _cell(built, s + "Dilution, % of market cap") == pytest.approx(0.0433037444, abs=1e-8)    # was 0.047139246
    assert _cell(built, s + "Net holder yield") == pytest.approx(-0.0289805003, abs=1e-8)            # was -0.0315473628
    assert _cell(built, s + "Buying pressure on float") == pytest.approx(0.0970550318, abs=1e-8)     # unchanged
    assert _cell(built, s + "Net float pressure") == pytest.approx(-0.174401367, abs=1e-8)           # unchanged
    assert _cell(built, s + "Archetype key") == pytest.approx(0.330762253, abs=1e-8)                 # A3 key, unchanged
    assert _cell(built, "audit|Master|Archetypes (primary first)") == "3, 1"                          # A4 off
