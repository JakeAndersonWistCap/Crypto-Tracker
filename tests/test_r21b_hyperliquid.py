"""ROUND 21b — HYPERLIQUID (Jake 2026-11-25): the Assistance Fund is BURNED; A3 and A4; the three true burns.

"Hyperliquid: the Assistance Fund at 0xfefe…fe is burned, not held. In December 2025 the validators voted (85% of stake
in favour) to recognise it as burned and remove it from both circulating and total supply. Treat it like any burn: out of
circulating and total supply. Assistance Fund buybacks count as buy-and-burn, so Hyperliquid is A3 and A4. Add the three
true burns from docs/r21_hyperliquid_burns.md; the HyperEVM base fee stays inferred and unsubtracted until it's read on
chain. Hyperliquid's API still includes the Fund in totalSupply, so name that gap in the reconciliation. Cite the vote in
the declared reason. Check the CoinGecko/CMC cross-check after the change and name any gap over 2%."

Was (R21, the interim default): the Fund a HELD buyback — in circulating and total supply, off the float, A4 off. The
R21 tests that pinned it (tests/test_r21_hyperliquid.py) are replaced by these; every number they checked is here with
its R21 value in a comment.
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

HL = "Hyperliquid"
FUND = "burn_address_balance"


def _frame(metric, pairs, source="test:read"):
    return pd.DataFrame({"date": [pd.Timestamp(d) for d, _v in pairs], "project": HL, "metric": metric,
                         "value": [float(v) for _d, v in pairs], "source": source, "tier": 1,
                         "fetched_at": "2026-10-13T00:00:00Z"})


def _days(start, values):
    d0 = pd.Timestamp(start)
    return [(d0 + pd.Timedelta(days=i), v) for i, v in enumerate(values) if v is not None]


# ====================================================================================== 1. the Fund is burned
def test_the_fund_is_burned_out_of_circulating_and_total_and_the_reason_cites_the_vote():
    bds = config.BUYBACK_DESTINATION_SHARES[HL]
    assert len(bds) == 1 and bds[0]["burn"] == 1.0 and not bds[0].get("hold")
    assert "85% of stake" in bds[0]["source"] and "2003684257949188396" in bds[0]["source"]
    assert config.buyback_destination_shares(HL, "2026-10-13")["burn"] == 1.0
    assert config.TOTAL_SUPPLY_NET[HL]["less"] == (FUND, "hyperevm_zero_address_balance")
    assert HL not in config.HELD_BUYBACK_FLOAT and HL not in config.FLOAT_RETURNS_HELD
    assert config.CIRC_HELD_BUYBACK_IN == {}
    # the Fund's purchase leaves circulating (tokenDetails lists it non-circulating): the release adds it back
    assert config.CIRCULATING_ONCHAIN[HL]["release_first_party"]["plus"] == ("actual_buyback_tokens",
                                                                             "hyperevm_priority_burn_tokens")
    assert not any("held buyback (R21)" in pt for pt in
                   config.PROJECT_BY_NAME[HL]["series_handover"]["circulating_supply_first_party"]["ordered_points"])
    assert [r[3] for r in config.HELD_BUYBACKS_R8 if r[0] == HL] == ["burned (out of circulating and total)"]
    # R21's guard declarations of the held bases are gone with them
    for m in ("fdv_usd", "pool_release_tokens", "buyback_held_tokens", "retired_buyback_usd"):
        assert not any("HELD" in str(c.get("why")) for c in config.DECLARED_COMPONENTS.get((HL, m), ())), m


def test_hyperliquid_is_a3_and_a4_and_a4_scores_the_fund_and_the_core_burns():
    assert config.PROJECT_BY_NAME[HL]["archetypes"] == [3, 1, 4]            # R21: [3, 1] (A4 off)
    c = config.CONDITIONAL_ARCHETYPES[HL]
    assert c["on"] is True and "85% of stake" in c["decided"]
    assert config.a1_burn_metric(HL) == "total_burn_tokens"
    assert config.a4_burn_metric(HL) == config.a4_net_burn_metric(HL) == "burn_ex_chain_fee_tokens"
    assert HL in config.METRICS["gross_burn_day_priced_usd"]["only_projects"]
    # net float pressure: the buy-and-burn on the burns line once, every burn in it
    assert [s["metric"] for s in config.burn_series(HL)] == ["total_burn_tokens"]
    assert "85% of stake" in config.burn_series(HL)[0]["why"]


def test_the_buyback_is_burned_in_the_split_and_never_on_the_buying_line(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[HL]])
    groups = {(HL, "actual_buyback_tokens"): _frame("actual_buyback_tokens", _days("2026-10-01", [30_000, 31_000]))}
    bw._buyback_split_views(groups)
    assert list(bw._daily(groups[(HL, "buyback_burned_tokens")]).values) == [30_000.0, 31_000.0]  # R21: 0, 0
    assert list(bw._daily(groups[(HL, "buyback_held_tokens")]).values) == [0.0, 0.0]               # R21: the buys
    assert list(bw._daily(groups[(HL, "retired_buyback_tokens")]).values) == [30_000.0, 31_000.0]  # unchanged


# ====================================================================================== 2. the burns
def test_every_burn_is_the_fund_plus_the_core_burns_plus_the_zero_address_and_a4_leaves_the_evm_gas(monkeypatch):
    bt = config.burn_total(HL)
    assert bt["components"] == ["gross_burn_tokens", "core_burn_tokens"]
    assert bt["optional_components"] == ["hyperevm_priority_burn_tokens"]
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[HL]])
    live = "hypercore_info:spotClearinghouseState:delta"
    groups = {
        (HL, "gross_burn_tokens"): _frame("gross_burn_tokens", _days("2026-09-23", [27_000, 28_000]), live),
        (HL, "core_burn_tokens"): _frame("core_burn_tokens", _days("2026-09-23", [4_000, 4_300]),
                                         "hypercore_info:tokenDetails.totalSupply:delta"),
        (HL, "hyperevm_priority_burn_tokens"): _frame("hyperevm_priority_burn_tokens", _days("2026-09-23", [290, 300])),
    }
    bw._burn_total_views(groups)
    tot = bw._daily(groups[(HL, "total_burn_tokens")])
    ex = bw._daily(groups[(HL, "burn_ex_chain_fee_tokens")])
    assert list(tot.values) == [31_290.0, 32_600.0]           # R21 (held): 4,300 / 4,610 — Core + the zero address
    assert list(ex.values) == [31_000.0, 32_300.0]            # R21 (held): 4,000 / 4,300 — Core only


def test_total_supply_is_the_api_total_less_the_fund_and_the_zero_address(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[HL]])
    groups = {(HL, "total_supply_gross"): _frame("total_supply_gross", _days("2026-10-12", [998_870_000, 998_862_000])),
              (HL, "hyperevm_zero_address_balance"): _frame("hyperevm_zero_address_balance",
                                                            _days("2026-10-12", [308_300, 308_438])),
              (HL, FUND): _frame(FUND, _days("2026-10-12", [47_980_000, 48_009_595]))}
    bw._total_supply_net_views(groups)
    # R21 (held): 998,561,700 / 998,553,562 (the Fund inside)
    assert list(bw._daily(groups[(HL, "total_supply")]).values) == [998_870_000 - 47_980_000 - 308_300.0,
                                                                    998_862_000 - 48_009_595 - 308_438.0]


def test_the_api_total_supply_gap_is_named_in_the_reconciliation():
    r = config.CREDIBILITY[HL]["in_total_supply_api"]
    assert r["ours"] == {"metric": "total_supply", "window": "now"}
    assert r["ref"]["metric"] == "total_supply_gross" and r["ref"]["force_verdict"] == "N/A (recorded)"
    assert "the API still counts the Assistance Fund" in r["ref"]["why"] and "85% of stake" in r["ref"]["why"]
    assert "r21b_total_supply_gap" in config.HYPERLIQUID_RULE_REFERENCES


def test_the_burn_checks_read_hyperliquids_own_stocks():
    import credibility as cr
    long = pd.concat([
        pd.DataFrame({"project": HL, "metric": m, "date": pd.to_datetime(["2026-07-14", "2026-10-13"]), "value": v})
        for m, v in (("af_noncirc_listed_tokens", [45_000_000.0, 48_000_000.0]),
                     ("hyperevm_zero_address_balance", [290_000.0, 308_000.0]),
                     ("total_supply_gross", [999_100_000.0, 998_862_000.0]))], ignore_index=True)
    asof = pd.Timestamp("2026-10-13")
    a1 = config.CREDIBILITY[HL]["a1_gross_burn"]
    v, d, how = cr.FORMULAS[a1["formula"]](HL, None, long, asof, **a1["args"])
    assert v == 3_000_000.0 + 18_000.0 + 238_000.0 and a1["tol"] == 15.0
    a4 = config.CREDIBILITY[HL]["a4_gross_burn"]
    v, d, how = cr.FORMULAS[a4["formula"]](HL, None, long, asof, **a4["args"])
    assert v == 3_000_000.0 + 238_000.0 and a4["tol"] == 15.0
    # a leg not held: nothing (unknown is never 0)
    v, _d, _h = cr.FORMULAS[a1["formula"]](HL, None, long[long.metric != "total_supply_gross"], asof, **a1["args"])
    assert v is None


# ====================================================================================== the dump, one-project build
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    import measuring_guard as mg
    import project_freeze as pf
    import store as sm
    from real_dump import to_store
    d = tmp_path_factory.mktemp("r21b_hl")
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
        return {**pf.cells(d / "audit.xlsx", d / "reader.xlsx", HL), "__audit__": str(d / "audit.xlsx")}


CP = "audit|CP Comparability|"


def _cell(cells, prefix):
    hits = [v for k, v in cells.items() if k.startswith(prefix)]
    assert len(hits) == 1, (prefix, len(hits))
    return hits[0]


def test_on_the_dump_the_fund_is_out_of_circulating_and_total_supply(built):
    # R22 (Jake 2026-11-25, item 1a: the team wallet's unvested HYPE out): was 539,758,896 (R21b); R21: 587,768,491
    assert _cell(built, CP + "Circulating supply (tokens)") == pytest.approx(301_828_851, abs=1)
    assert _cell(built, CP + "FREE FLOAT (tokens)") == pytest.approx(99_462_451.6, abs=1)             # unchanged
    assert _cell(built, CP + "Total supply (tokens)") == pytest.approx(950_543_705, abs=1)            # R21: 998,553,300
    assert _cell(built, CP + "Market cap ($)") == pytest.approx(24_438_057_800, rel=1e-6)  # R22; R21b 43,702,446,000


def test_on_the_dump_the_buyback_is_burned_and_every_burn_counts(built):
    a3 = "audit|A3 Revenue Buyback|"
    assert _cell(built, a3 + "Actual buyback Q0 HELD") == 0.0                                          # R21: 2,380,273.5
    assert _cell(built, a3 + "Actual buyback Q0 BURNED") == pytest.approx(2_380_273.5, abs=1)        # R21: 0
    assert _cell(built, a3 + "Actual buyback Q0 RETIRED") == pytest.approx(2_380_273.5, abs=1)       # unchanged
    # A1's every burn: the Fund + the Core burns + the zero address (R21: 52,260.63, the true burns without the Fund)
    assert _cell(built, "audit|A1 Infrastructure|Gross burn Q0 (tokens)") == pytest.approx(2_474_408, rel=0.005)


def test_on_the_dump_the_burns_and_the_nets_check_clean(built):
    assert free_float.check_burns(built["__audit__"]) == []
    assert free_float.check_nets(built["__audit__"]) == []


def test_on_the_dump_the_headlines(built):
    s = "reader|Summary|"
    # R22 (market cap on the circulating without the team's unvested HYPE): R21b 0.0155918832 / 0.047139246 /
    # -0.0315473628; R21: 0.014323244 / 0.0433037444 / -0.0289805003
    assert _cell(built, s + "Holder value yield") == pytest.approx(0.0280504452, abs=1e-8)
    assert _cell(built, s + "Dilution, % of market cap") == pytest.approx(0.0848054608, abs=1e-8)
    assert _cell(built, s + "Net holder yield") == pytest.approx(-0.0567550156, abs=1e-8)
    # net float pressure: the Fund's buy-and-burn on the burns line only (R21: buying 9.71%, burns 1.13%)
    assert _cell(built, s + "Buying pressure on float") == 0.0                                        # R21: 0.0970550318
    assert _cell(built, s + "Net float pressure") == pytest.approx(-0.170563056, abs=1e-8)           # R21: -0.16312006
    assert _cell(built, CP + "BURNS ON FLOAT") == pytest.approx(0.1008933425, abs=1e-8)              # R21: 0.0112813068
    assert _cell(built, s + "Archetype key") == pytest.approx(0.330762253, abs=1e-8)                 # A3 key, unchanged
    assert _cell(built, "audit|Master|Archetypes (primary first)") == "3, 1, 4"                       # R21: "3, 1"


def test_the_a4_dollar_burn_basis_is_declared_to_the_guard():
    import measuring_guard as mg
    assert "derived:burn_ex_chain_fee_tokens*price_usd" in mg._declared(HL, "gross_burn_day_priced_usd")


def test_the_cumulative_burn_cell_adds_every_true_burn_stock():
    ex = config.BURN_CUMULATIVE_EXTRA[HL]
    assert ex["add"] == ("hyperevm_zero_address_balance",) and ex["fall_of"] == "total_supply_gross"
