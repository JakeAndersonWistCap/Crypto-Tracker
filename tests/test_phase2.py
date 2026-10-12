"""
tests/test_phase2.py — Phase 2, the comparability layer (Jake 2026-10-12: A3/A4 items 1-5, amendments 1-5, LITMUS A-F),
judged on the REAL dumps (fixtures/real_*.json, asof 2026-10-09) — never on a hand-built fixture.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from test_trajectory import _built  # noqa: E402

CP = "CP Comparability"


def _row(wb, X, sheet, project):
    """{header: evaluated value} for one project's row on a tab."""
    ws = wb[sheet]
    hr = next(r for r in range(1, 12) if ws.cell(r, 1).value == "Project")
    r = next(r for r in range(hr + 1, ws.max_row + 1) if ws.cell(r, 1).value == project)
    return {str(ws.cell(hr, c).value): X.value(sheet, r, c) for c in range(1, ws.max_column + 1)
            if ws.cell(hr, c).value}


def _get(row, prefix):
    return next(v for h, v in row.items() if h.startswith(prefix))


def _data(wb, X, key, col):
    import build_workbook as bw
    ws = wb["Data"]
    r = next(i for i in range(2, ws.max_row + 1) if ws.cell(i, 1).value == key)
    return X.value("Data", r, bw.DATA_COLS.index(col) + 1)


def _rate(wb, X, key):
    """A flow's Q0 total per covered day — _flow_rate's definition."""
    cov = _data(wb, X, key, "q0_covered_days")
    return _data(wb, X, key, "q0") / (cov if isinstance(cov, (int, float)) else 90)


def test_cp_on_the_real_aerodrome_dump(tmp_path, monkeypatch):
    """Float = circulating − locked; voter fees are credited holder value and the Foundation's buys are recorded but
    never credited (its voting rewards are already inside the credited fees); subsidy cover = revenue ÷ $ (b) per day."""
    import xlcalc
    wb, _rows = _built(tmp_path, monkeypatch, "Aerodrome")
    X = xlcalc.Workbook(wb)
    o = _row(wb, X, CP, "Aerodrome")
    circ, lock, flt = (_get(o, "Circulating supply (tokens)"), _get(o, "Staked / locked inside"),
                       _get(o, "TRADEABLE FLOAT"))
    assert flt == pytest.approx(circ - lock) and 0 < flt < circ
    assert _get(o, "Tradeable float ÷ circulating") == pytest.approx(flt / circ)
    # every retirement and dilution rate on both bases: same numerator, two denominators
    a_c, a_f = _get(o, "Dilution (a) security emissions (annualised) ÷ circ"), _get(o, "Dilution (a) security emissions (annualised) ÷ trad")
    assert a_c * circ == pytest.approx(a_f * flt)
    r_c, r_f = _get(o, "Retirement rate (RETIRED buyback tokens, burned + held, annualised) ÷ circ"), \
        _get(o, "Retirement rate (RETIRED buyback tokens, burned + held, annualised) ÷ trad")
    assert r_c * circ == pytest.approx(r_f * flt)
    # LITMUS A: credited = the voter fees; the Foundation's buys are recorded beside it, equal to the flow demand
    assert config.holder_funding("Aerodrome", "2026-09-01")[0] == "treasury"
    # fees only (liquid bribes are holder income, not revenue — decision 4), less the illiquid fee leg: ~1
    assert _get(o, "Payout ratio — the project's own net basis") == pytest.approx(1.0, rel=1e-3)
    assert _get(o, "Holder value RECORDED, NOT CREDITED") == pytest.approx(_get(o, "Flow demand")) and \
        _get(o, "Holder value RECORDED, NOT CREDITED") > 0
    assert _get(o, "SUBSIDY COVER") == pytest.approx(_rate(wb, X, "Aerodrome|fees_usd")
                                                     / _rate(wb, X, "Aerodrome|outflow_b_usd"))
    # no demand-side incentives on file: (b + c) is (b)
    assert _get(o, "Subsidy cover incl. demand-side") == pytest.approx(_get(o, "SUBSIDY COVER"))
    # a young unlock series BLOCKS total dilution (and so net holder yield) rather than reading as a smaller dilution
    assert str(_get(o, "TOTAL DILUTION = outflows a+b+c+e (annualised) ÷ circ")).startswith("BLOCKED")
    assert str(_get(o, "NET HOLDER YIELD")).startswith("BLOCKED")
    # attributes, never penalties: text from phase2_attributes.json
    assert _get(o, "CAPTURE STATUS").startswith("Live")
    assert "veAERO" in _get(o, "Lock terms per yield")


def test_cp_net_holder_yield_and_whole_token_yield_on_the_real_pendle_dump(tmp_path, monkeypatch):
    import xlcalc
    wb, rows = _built(tmp_path, monkeypatch, "Pendle")
    X = xlcalc.Workbook(wb)
    o = _row(wb, X, CP, "Pendle")
    mc, hvy = _get(o, "Market cap ($)"), _get(o, "HOLDER VALUE YIELD")
    dil = sum(_get(o, f"Outflow ({c})") for c in ("b", "e"))
    assert _get(o, "NET HOLDER YIELD") == pytest.approx(hvy - dil / mc)
    assert _get(o, "Whole-token yield — protocol") < _get(o, "Staker yield — protocol")
    assert _get(o, "SUBSIDY COVER") > 0
    # the four CP headlines are Credibility rows and Trajectory rows with declared directions
    hids = {r["hid"] for r in rows if r["key"].startswith("CP")}
    assert {"cp_float", "cp_hvy", "cp_nhy", "cp_subsidy_cover"} <= hids
    assert all(h in config.TRAJECTORY["rows"] for h in hids)
    cw = wb["Credibility"]
    whats = [str(cw.cell(i, 4).value or "") for i in range(1, cw.max_row + 1) if cw.cell(i, 1).value == "Pendle"]
    assert any(w.startswith("HOLDER VALUE YIELD") for w in whats) and any(w.startswith("TRADEABLE FLOAT") for w in whats)


def test_cp_ethereum_float_takes_the_beacon_stake_and_a4_additions_evaluate(tmp_path, monkeypatch):
    """Ethereum has no lock contract, but its stake is measured (beacon_chain_eth, config.CP_STAKE_METRIC). The A4
    additions are checked on the same real dump with Ethereum put back on A4 for the build (no A4 project is dumped)."""
    import xlcalc
    monkeypatch.setitem(config.PROJECT_BY_NAME["Ethereum"], "archetypes", [1, 4])
    wb, _rows = _built(tmp_path, monkeypatch, "Ethereum")
    X = xlcalc.Workbook(wb)
    o = _row(wb, X, CP, "Ethereum")
    circ, stake = _get(o, "Circulating supply (tokens)"), _data(wb, X, "Ethereum|beacon_chain_eth", "now")
    assert _get(o, "TRADEABLE FLOAT") == pytest.approx(circ - stake)
    assert _get(o, "Whole-token yield — validator") == pytest.approx(_get(o, "Staker yield — validator") * stake / circ)
    assert str(_get(o, "HOLDER VALUE YIELD")).startswith("n/a")      # a fee-market burn is not holder value
    a4 = _row(wb, X, "A4 Permanent Burn", "Ethereum")
    fees, iss = _rate(wb, X, "Ethereum|fees_usd"), _rate(wb, X, "Ethereum|gross_issuance_tokens")
    burn = _rate(wb, X, f"Ethereum|{config.a4_burn_metric('Ethereum')}")
    assert _get(a4, "Revenue required for crossover") == pytest.approx(fees * iss / burn * 365)
    assert _get(a4, "Governance lever") == pytest.approx(iss * _data(wb, X, "Ethereum|price_usd", "q0") / fees)
    assert _get(a4, "Burn split Q0") in (None, "")                    # one burn series: nothing to split


def test_cp_plume_has_no_measured_outflow_and_says_why(tmp_path, monkeypatch):
    """Plume's staking rewards come out of pre-minted supply (gross issuance is 0 on every stored day): no series
    measures them, so no dilution is shown — never a 0 that reads as 'no dilution'."""
    import xlcalc
    payload = json.loads(Path(__file__).resolve().parents[1].joinpath("fixtures/real_plume.json").read_text())
    iss = [m["value"] for m in payload["metrics"] if m["metric"] == "gross_issuance_tokens"]
    assert iss and not any(iss)
    assert not any(config.outflow_metrics("Plume", c) for c in config.OUTFLOW_CATEGORY_NAMES)
    wb, _rows = _built(tmp_path, monkeypatch, "Plume")
    X = xlcalc.Workbook(wb)
    o = _row(wb, X, CP, "Plume")
    assert _get(o, "TOTAL DILUTION = outflows a+b+c+e (annualised) ÷ circ") in (None, "")
    assert "pre-minted" in _get(o, "Outflow categories").lower()


def test_holder_funding_periods_are_dated_and_sourced():
    """LITMUS A (research 2026-10-12): Ether.fi's April 2025 withdrawal-fee buyback is revenue, the October 2025
    treasury programme is not, and the 2026-09-02 inflow is an LP withdrawal; Uniswap's 100M burn was the treasury's."""
    f = config.holder_funding
    assert f("Ether.fi", "2025-06-01")[0] == "revenue"
    assert f("Ether.fi", "2025-12-01")[0] == "treasury"
    assert f("Ether.fi", "2026-09-02")[0] == "not a purchase"
    # Uniswap's 100M treasury burn is moved out of the burn series (one_off_flows) before any holder value reads it
    # (decision 5 of 2026-10-12 evening), so no funding period is needed for it
    assert f("Uniswap", "2025-12-28")[0] == "revenue"
    assert set(config.METRICS["holder_value_other_usd"]["only_projects"]) == {"Aerodrome", "Ether.fi"}
    # no outflow metric for a category nobody holds, and no gap row on a project without the category
    assert "outflow_d_tokens" not in config.METRICS
    assert "Uniswap" not in config.METRICS["outflow_a_tokens"]["only_projects"]


def test_attributes_are_sourced_and_store_no_near_account():
    raw = json.loads(Path(__file__).resolve().parents[1].joinpath("phase2_attributes.json").read_text())
    assert raw["_meta"]["entered_by"] == "Claude Code, pending Jake's review" and raw["_meta"]["read_on"] == "2026-10-12"
    port, _ = config.read_portfolio(config.PORTFOLIO_FILE)
    want = set(port or config.PROJECT_BY_NAME) - {"GEODNET"}
    missing = [n for n in want if n in config.PROJECT_BY_NAME and n not in config.PHASE2_ATTRIBUTES]
    assert not missing or not port, missing
    for n, a in config.PHASE2_ATTRIBUTES.items():
        for k in ("destination", "certainty", "capture_status"):
            assert a[k].get("source"), (n, k)
        for e in a.get("mechanism_log") or []:
            # a future date only where the log says it is scheduled (Aerodrome's Aero launch, 2026-10-21)
            assert (e["date"][:10] <= "2026-10-12" or "scheduled" in e["date"]) and e.get("source"), (n, e)
    assert "sputnik-dao.near" not in json.dumps(raw) and "multisignature.near" not in json.dumps(raw)
    assert config.phase2_attribute_text("GEODNET", "capture") == "not sourced yet"
    assert config.phase2_attribute_text("Fluid", "capture").startswith("Halted")


def test_metrics_doc_has_the_flow_statements_and_flags_flows_with_no_series():
    text = Path(__file__).resolve().parents[1].joinpath("METRICS.md").read_text(encoding="utf-8")
    assert "## Token flow and value flow statements" in text and "## CP — Comparability" in text
    uni = text.split("### Uniswap")[-1].split("### ")[0]
    assert "(d) ecosystem / grant spend | **NO SERIES**" in uni
    assert "TRADEABLE FLOAT" in text and "SUBSIDY COVER" in text
