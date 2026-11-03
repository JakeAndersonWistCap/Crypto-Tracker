"""ROUND P3-1 (Jake 2026-11-02) — the rule-3 sweep and definition changes R1 / R2 (SPEC.md "### P3", P3-1a/b/c).

P3-1a: rule3.suspects lists every code cell whose reason says a mechanism is absent (docs/p3_1a_rule3_sweep.md).
P3-1b (R1): holder value yield = a MEASURED 0 where no payout mechanism exists (Morpho, Aethir, Plume) — HOLDER_VALUE
`none`, held only while the project's fee_split agrees; staked/locked = 0 where nothing can be locked (Uniswap, Fluid) —
free_float_lock_zero, the practice Morpho and Maple follow. Each 0's comment names the absent mechanism and its source.
P3-1c (R2): Ethereum's protocol revenue is its fee burn, credited as holder value (share 1.0), read from the primary
burn series; the stopped route (the burn re-labelled as a buyback, :as-buyback, last 2026-09-27) is never credited.

Built once on the committed real dumps (fixtures/real_*.json), as test_round_p2e_ranks does.
"""
from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402
import reader  # noqa: E402
import rule3  # noqa: E402
from shared_build import plain_build  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The reader and the audit file built on the committed real dumps (the shared plain build, tests/shared_build.py)."""
    import xlcalc
    rec = plain_build(tmp_path_factory, "p3_1")
    assert "error" not in rec["stats"], rec["stats"]
    assert rec["stats"].get("blank_unexplained") == 0, rec["stats"].get("blank_cells")
    rwb, awb = openpyxl.load_workbook(rec["reader"]), openpyxl.load_workbook(rec["audit"])
    return {"reader": rwb, "audit": awb, "RX": xlcalc.Workbook(rwb), "AX": xlcalc.Workbook(awb),
            "reader_path": rec["reader"], "audit_path": rec["audit"]}


def _cell(built, book: str, tab: str, project: str, header: str):
    """(value, comment) of the cell under the header STARTING with `header` in `project`'s row (row 4 = headers)."""
    wb, X = (built["reader"], built["RX"]) if book == "reader" else (built["audit"], built["AX"])
    ws = wb[tab]
    cols = [c for c in range(1, ws.max_column + 1) if str(ws.cell(4, c).value or "").startswith(header)]
    assert cols, f"{tab}: no header {header!r}"
    rows = [r for r in range(5, ws.max_row + 1) if ws.cell(r, 1).value == project]
    assert rows, f"{tab}: no row {project}"
    c = ws.cell(rows[0], cols[0])
    return X.value(tab, c.row, c.column), (c.comment.text if c.comment else "")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


CP = "CP Comparability"
HVY, NHY = "HOLDER VALUE YIELD", "NET HOLDER YIELD"


# ===================================================================================================== R1: holder value
@pytest.mark.parametrize("name,source", [("Morpho", "docs.morpho.org"), ("Aethir", "docs.aethir.com"),
                                         ("Plume", "docs.plume.org")])
def test_r1_holder_value_yield_is_a_measured_zero_naming_the_absent_mechanism(built, name, source):
    v, comment = _cell(built, "audit", CP, name, HVY)
    assert _num(v) and v == 0, f"{name} holder value yield {v!r}"
    assert "measured zero" in comment and source in comment, comment
    rv, rcomment = _cell(built, "reader", reader.COMPARISON, name, "Holder value yield")
    assert _num(rv) and rv == 0
    assert source in rcomment and "no " in rcomment.lower(), rcomment


@pytest.mark.parametrize("name", ["Morpho", "Aethir", "Plume"])
def test_r1_net_holder_yield_no_longer_waits_on_holder_value(built, name):
    """Net holder yield = 0 − dilution: a number wherever the dilution legs are formed. On the fixture dumps some
    outflow legs are short (Aethir's (a) covers 61 of 90 days, Morpho's (b) / Plume's (a) are not formed) — then the
    cell names THAT leg, never the absence of holder value."""
    v, comment = _cell(built, "audit", CP, name, NHY)
    assert _num(v) or ("outflow" in str(v) and "no holder value" not in str(v)), v
    assert not rule3.is_suspect(v)


def test_r1_the_holder_value_zero_is_the_declared_zero_path_and_only_where_the_record_agrees():
    zeros = sorted(n for (n, m) in config.DECLARED_ZERO if m == "holder_value_revenue_usd")
    assert zeros == ["Aethir", "Morpho", "Plume"]
    for n in zeros:
        fs = config.PROJECT_BY_NAME[n]["fee_split"]
        assert fs["share_to_buyback"] == 0.0 and fs["source_url"]
        assert config.buyback_route(n)["route"] == "none"


def test_r1_unknown_stays_a_code_a_mechanism_without_data_never_becomes_0(built, monkeypatch):
    # a project WITH a payout mechanism (PancakeSwap: a fee-funded product burn, not wired) keeps its code
    v, _ = _cell(built, "audit", CP, "PancakeSwap", HVY)
    assert not _num(v), v
    rv, _ = _cell(built, "reader", reader.COMPARISON, "PancakeSwap", "Holder value yield")
    assert rv in reader.CODES, rv
    # a `none` declaration the project's own record does not back (no 0.0 share with a source) is ignored
    monkeypatch.setitem(config.HOLDER_VALUE, "PancakeSwap", {"none": "planted"})
    assert config.holder_value_absent("PancakeSwap") is None
    fs = dict(config.PROJECT_BY_NAME["Morpho"]["fee_split"], share_to_buyback=None)
    monkeypatch.setitem(config.PROJECT_BY_NAME["Morpho"], "fee_split", fs)
    assert config.holder_value_absent("Morpho") is None


# ===================================================================================================== R1: staked / locked
@pytest.mark.parametrize("name,words", [("Uniswap", "Uni.sol"), ("Fluid", "fluid-contracts-public")])
def test_r1_staked_is_zero_float_is_circulating_dilution_and_lock_rate_are_numbers(built, name, words):
    staked, comment = _cell(built, "audit", CP, name, "Staked / locked inside circulating")
    assert _num(staked) and staked == 0, staked
    assert "measured zero" in comment and words in comment, comment
    circ, _ = _cell(built, "audit", CP, name, "Circulating supply (tokens)")
    flt, _ = _cell(built, "audit", CP, name, "TRADEABLE FLOAT")
    assert _num(circ) and _num(flt) and flt == pytest.approx(circ)
    dil, _ = _cell(built, "reader", reader.COMPARISON, name, "Total dilution, % of float")
    assert _num(dil), dil
    lock, lcomment = _cell(built, "reader", reader.COMPARISON, name, "Lock rate, % of circulating")
    assert _num(lock) and lock == 0, lock
    assert words in lcomment, lcomment


def test_r1_lock_zero_reuses_the_morpho_maple_declaration():
    for n in ("Morpho", "Maple", "Uniswap", "Fluid"):
        assert config.PROJECT_BY_NAME[n].get("free_float_lock_zero"), n


# ===================================================================================================== R2: Ethereum
def test_r2_ethereum_holder_value_is_the_fee_burn_at_share_one():
    spec = config.HOLDER_VALUE["Ethereum"]["burn_credited"]
    assert spec["share_of_revenue"] == 1.0 and spec["metric"] == "gross_burn_tokens"
    assert config.buyback_route("Ethereum")["route"] == "none"


def test_r2_holder_value_view_credits_burn_x_price_and_never_the_stale_as_buyback_rows(monkeypatch):
    import build_workbook as bw
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME["Ethereum"]])
    days = pd.date_range("2026-10-01", periods=3)

    def frame(metric, vals, src):
        return pd.DataFrame({"date": days, "project": "Ethereum", "metric": metric, "value": vals, "tier": 1,
                             "source": src})
    groups = {("Ethereum", "gross_burn_tokens"): frame("gross_burn_tokens", [10.0, 20.0, 30.0],
                                                       "etherscan:ethsupply2.BurntFees:delta"),
              ("Ethereum", "price_usd"): frame("price_usd", [2000.0, 2500.0, 3000.0], "coingecko"),
              # the stopped route: the burn re-labelled as a buyback — must never be credited
              ("Ethereum", "actual_buyback_usd"): frame("actual_buyback_usd", [9e9, 9e9, 9e9], "derived:tokens*price")}
    bw._holder_value_views(groups)
    hv = groups[("Ethereum", "holder_value_revenue_usd")].set_index("date")["value"]
    assert list(hv.values) == [20_000.0, 50_000.0, 90_000.0]
    src = set(groups[("Ethereum", "holder_value_revenue_usd")]["source"])
    assert all("fee burn" in s and "ethsupply2" in s for s in src), src


def test_r2_ethereum_cells_are_numbers_with_no_stale_note(built):
    v, comment = _cell(built, "audit", CP, "Ethereum", HVY)
    assert _num(v) and v > 0, v
    assert "STALE" not in comment and "fee burn" in comment, comment
    n, _ = _cell(built, "audit", CP, "Ethereum", NHY)
    assert _num(n), n
    # Summary J / M (reader._write_summary: hvy, nhy) — the cells that carried the STALE note on Jake's build
    for head in ("Holder value yield", "Net holder yield"):
        sv, sc = _cell(built, "reader", reader.SUMMARY, "Ethereum", head)
        assert _num(sv) and "STALE" not in sc, (head, sv, sc)
    pr, pc = _cell(built, "reader", reader.COMPARISON, "Ethereum", "Payout ratio")
    # 1.0 by definition; the cell MEASURES it (the burn as stored ÷ DefiLlama's revenue) and says so
    assert _num(pr) and abs(pr - 1.0) < 0.10, pr
    assert "by definition" in pc and "Above 1.0x" not in pc, pc


# ===================================================================================================== P3-1a: the sweep
def test_rule3_suspects_finds_a_planted_no_buyback_programme_reason():
    from openpyxl.comments import Comment
    rwb, awb = openpyxl.Workbook(), openpyxl.Workbook()
    ws = rwb.active
    ws.title = "Comparison"
    ws.cell(4, 1, "Project")
    ws.cell(4, 2, "Holder value yield")
    ws.cell(5, 1, "Planted")
    ws.cell(5, 2, "n/a").comment = Comment("n/a — no buyback programme and no declared payout to holders", "t")
    ws.cell(6, 1, "Measured")
    ws.cell(6, 2, 0.01)
    wa = awb.active
    wa.title = "CP Comparability"
    wa.cell(4, 1, "Project")
    wa.cell(4, 2, "HOLDER VALUE YIELD")
    wa.cell(5, 1, "Planted")
    wa.cell(5, 2, "n/a — no buyback programme and no declared payout to holders")
    wa.cell(6, 1, "Other")
    wa.cell(6, 2, "n/a — Data: no rows stored")
    hits = rule3.suspects(rwb, awb)
    assert [(h["tab"], h["project"], h["header"], h["code"]) for h in hits] == [
        ("reader:Comparison", "Planted", "Holder value yield", "n/a"),
        ("audit:CP Comparability", "Planted", "HOLDER VALUE YIELD", "n/a")]
    assert "no buyback programme" in hits[0]["reason"]


def test_rule3_r1_cells_are_no_longer_suspects_on_the_fixture_build(built):
    hits = rule3.suspects(built["reader"], None)
    bad = [h for h in hits if h["project"] in ("Morpho", "Aethir", "Plume", "Ethereum")
           and h["header"] in ("Holder value yield", "Net holder yield")]
    bad += [h for h in hits if h["project"] in ("Uniswap", "Fluid")
            and h["header"] in ("Total dilution, % of float", "Lock rate, % of circulating",
                                "Total dilution, % of float a year", "Unlocks, % of free float a year")]
    assert not bad, bad
