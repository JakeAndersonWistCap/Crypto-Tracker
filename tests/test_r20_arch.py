"""ROUND 20, ARCH lane (Jake 2026-11-24) — archetype definitions, tags and keys; "history filling"; the 6-on-6 interim
column; the A3 tab's status labels (Fluid's row).

Unit tests on config / reader / build_workbook / done_check with synthetic series and reasons; the two tests that read a
built reader use the shared plain build (tests/shared_build.py — the lead's run).
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import done_check  # noqa: E402
import reader  # noqa: E402

JAKE_TAGS = {   # Jake's list, primary first
    # R21b (Jake 2026-11-25, fee burns in A3): Ethereum [1, 4] -> [1, 3, 4]; Aethir [2, 4] -> [2, 3, 4];
    # Hyperliquid [3, 1] -> [3, 1, 4] (the Assistance Fund's buy-and-burn)
    "Ethereum": [1, 3, 4], "Near": [1, 3, 4], "Chainlink": [2, 3], "Aethir": [2, 3, 4], "GEODNET": [2, 3, 4],
    "Hyperliquid": [3, 1, 4], "Sky": [3, 4], "Aerodrome": [3, 4], "Pendle": [3], "Maple": [3], "Fluid": [3],
    "Ether.fi": [3], "Uniswap": [4, 3], "Morpho": [3],
}


# ------------------------------------------------------------------------------------------------ 1. definitions
def test_the_definitions_are_jakes_everywhere():
    d = config.ARCHETYPE_DEFINITIONS
    assert [d[a]["name"] for a in (1, 2, 3, 4)] == ["A1 Infrastructure", "A2 Coordination", "A3 Revenue to holders",
                                                    "A4 Permanent burn"]
    assert "native asset of a chain" in d[1]["what"]
    assert "pays the suppliers of a real-world resource" in d[2]["what"]
    assert "market buybacks however the tokens end up (burned, held or distributed)" in d[3]["what"]
    assert "revenue paid directly to stakers, including in other tokens" in d[3]["what"]
    assert "any permanent destruction" in d[4]["what"] and "actually burned" in d[4]["what"]
    assert d[3]["key"] == "revenue sent to holders ($) ÷ value of new supply ($), same window"
    assert d[2]["key_reads"] == "network economics, not holder value"
    assert any("nets out" in r and "does not qualify a project for A3" in r for r in config.ARCHETYPE_RULES)
    assert any("only burned tokens are retired from supply" in r and "not A3's key" in r for r in config.ARCHETYPE_RULES)
    # the reader says the same, and the old definitions are gone
    assert reader.ARCHETYPE_NAMES[3] == "A3 Revenue to holders"
    k3 = reader.ARCHETYPE_KEYS[3][1]
    assert "REVENUE SENT TO HOLDERS ($) ÷ VALUE OF NEW SUPPLY ($)" in k3 and "BURNED BUYBACK ÷" not in k3
    assert "NETWORK ECONOMICS, NOT HOLDER VALUE" in reader.ARCHETYPE_KEYS[2][1]
    assert all(len(k[0]) <= reader.MAX_HEADER for k in reader.ARCHETYPE_KEYS.values())
    src = (ROOT / "reader.py").read_text(encoding="utf-8")
    assert "A3 Revenue buyback — revenue buys tokens back" not in src
    spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    assert "## 1c. The archetypes (Jake 2026-11-24, Round 20)" in spec
    assert spec.index("### Round 20 — archetypes (Jake 2026-11-24), old → new") < spec.index("### Round 18")


def test_the_sheet_titles_are_kept():
    """The tools find the tabs by title (project_freeze, leaderboard, highlights, done_check): unchanged."""
    assert (reader.A1_TAB, reader.A2_TAB, reader.A3_TAB, reader.A4_TAB) == (
        "A1 Infrastructure", "A2 Coordination", "A3 Revenue Buyback", "A4 Permanent Burn")


# ------------------------------------------------------------------------------------------------ 2. tags
@pytest.mark.parametrize("name,tags", sorted(JAKE_TAGS.items()))
def test_the_tags_are_jakes_primary_first(name, tags):
    p = config.PROJECT_BY_NAME[name]
    assert p["archetypes"] == tags
    assert reader._primary(p) == tags[0]


def test_projects_not_on_the_list_keep_their_tags():
    for p in config.PROJECTS:
        if p["name"] not in JAKE_TAGS:
            assert p["archetypes"] == config.ARCHETYPES_BEFORE_R20[p["name"]], p["name"]
    assert config.PROJECT_BY_NAME["Plume"]["archetypes"] == [1]                  # on hold, no change
    assert config.PROJECT_BY_NAME["World Mobile"]["archetypes"] == [2, 3]        # parked
    assert reader.parked_reason("World Mobile")
    assert 1 not in config.PROJECT_BY_NAME["Chainlink"]["archetypes"] and 1 in config.ARCHETYPE_OFF["Chainlink"]
    assert 2 not in config.PROJECT_BY_NAME["Morpho"]["archetypes"]


def test_the_two_conditional_tags_are_off_and_one_switch_each_turns_them_on():
    c = config.CONDITIONAL_ARCHETYPES
    assert set(c) == {"Aethir", "Hyperliquid"} and (c["Aethir"]["tag"], c["Hyperliquid"]["tag"]) == (3, 4)
    # R21b (Jake 2026-11-25): both ON — Aethir's A3 (the revenue-funded fee burn), Hyperliquid's A4 (the Assistance
    # Fund's buy-and-burn)
    assert c["Aethir"]["on"] and c["Hyperliquid"]["on"] and c["Aethir"]["needs"] and c["Hyperliquid"]["needs"]
    assert config.archetypes_r20("Aethir") == [2, 3, 4] and config.archetypes_r20("Hyperliquid") == [3, 1, 4]
    assert config.archetypes_r20("Hyperliquid", {"Hyperliquid": False}) == [3, 1]
    try:
        c["Hyperliquid"]["on"] = False
        config.apply_archetypes_r20()
        assert config.PROJECT_BY_NAME["Hyperliquid"]["archetypes"] == [3, 1]
    finally:
        c["Hyperliquid"]["on"] = True
        config.apply_archetypes_r20()
    assert config.PROJECT_BY_NAME["Aethir"]["archetypes"] == [2, 3, 4]
    assert config.PROJECT_BY_NAME["Hyperliquid"]["archetypes"] == [3, 1, 4]
    assert reader._primary(config.PROJECT_BY_NAME["Hyperliquid"]) == 3       # the primary does not move


def test_a_retag_moves_no_series():
    """A re-tag changes where a project is shown and ranked, not which series are asked of it: nothing measured is
    dropped and no gap is opened for a series nobody has wired (metrics_for_project reads the pre-R20 tags)."""
    for name in JAKE_TAGS:
        p = config.PROJECT_BY_NAME[name]
        before = dict(p, archetypes=config.ARCHETYPES_BEFORE_R20[name])
        before.pop("metric_archetypes", None)
        assert set(config.metrics_for_project(before)) == set(config.metrics_for_project(p)), name
    # the keys of the new tabs read series the projects already hold: Uniswap's A3 key the credited holder value
    assert "holder_value_revenue_usd" in config.metrics_for_project(config.PROJECT_BY_NAME["Uniswap"])
    # Aerodrome's A4 burn is what is actually burned — its burned buyback; no other burn series is asked of it
    aero = config.PROJECT_BY_NAME["Aerodrome"]
    assert config.a4_burn_metric("Aerodrome") == "buyback_burned_tokens" in config.metrics_for_project(aero)
    assert config.a1_burn_metric("Aerodrome") == "gross_burn_tokens" not in config.metrics_for_project(aero)
    assert "customer_revenue_usd" in config.metrics_for_project(config.PROJECT_BY_NAME["Morpho"])   # kept
    assert "buyback_fund_balance" not in config.metrics_for_project(config.PROJECT_BY_NAME["Morpho"])   # A3 off


def test_the_status_labels():
    # R21 (Jake 2026-11-25, F4): labelled by evidence (was "paused", "paused", "off")
    assert config.ARCHETYPE_STATUS == {"Fluid": {3: "paused, no restart condition stated"},
                                       "Ether.fi": {3: "not executing since 1 Apr"}, "Morpho": {3: "off"}}
    assert config.archetype_status("Fluid", 3).startswith("paused") and config.archetype_status("Morpho", 3) == "off"
    assert config.archetype_status("Maple", 3) == "" and config.archetype_status("Fluid", 4) == ""


# ------------------------------------------------------------------------------------------------ 3. the A3 key
def _g(project, metric, s):
    return pd.DataFrame({"date": s.index, "project": project, "metric": metric, "value": s.values, "tier": 2,
                         "source": "test", "fetched_at": "2026-11-20T00:00:00"})


def test_the_a3_key_is_declared_and_the_burned_figure_is_an_ordinary_column():
    assert bw.A3_KEY_HEAD.startswith("REVENUE TO HOLDERS ÷ NEW SUPPLY (x) = revenue sent to holders ($) ÷ value of new "
                                     "supply ($)")
    assert bw.A3_BURNED_HEAD.startswith("BURNED ÷ (EMISSIONS + UNLOCKS) (x)") and "rank key until" in bw.A3_BURNED_HEAD
    assert bw.A3_KEY_TRAJECTORY_ID == reader.A3_TRAJECTORY_ID == "a3_revenue_vs_new_supply"
    assert bw.A3_BURNED_TRAJECTORY_ID == "a3_burned_vs_new_supply"
    assert {bw.A3_KEY_TRAJECTORY_ID, bw.A3_BURNED_TRAJECTORY_ID} <= set(config.TRAJECTORY["rows"])
    import headline_check
    import spec
    assert headline_check.key_prefix(3) == "REVENUE TO HOLDERS ÷ NEW SUPPLY (x)"
    assert spec.concept_for("A3 Revenue Buyback", bw.A3_KEY_HEAD) == "a3_key"
    assert spec.concept_for("A3 Revenue Buyback", bw.A3_BURNED_HEAD) == "a3_burned"
    assert "revenue_to_holders_usd" in spec.concepts()["a3_key"]["inputs"]
    a3 = next(t for t in reader.tab_specs(bw) if t.arch == 3)
    assert a3.key == ("audit", bw.A3_KEY_HEAD) and a3.key_header == reader.ARCHETYPE_KEYS[3][0]
    assert a3.key_traj[0] == "a3_revenue_vs_new_supply"
    assert any(str(d[1]).startswith("BURNED ÷ (EMISSIONS + UNLOCKS)") for d in a3.drivers)   # kept, not the key
    a2 = next(t for t in reader.tab_specs(bw) if t.arch == 2)
    assert "network economics, not holder value" in a2.title and a2.key_header.startswith("Network economics")
    a4 = next(t for t in reader.tab_specs(bw) if t.arch == 4)
    assert a4.key == ("audit", "BURN ÷ ISSUANCE (x)")                                       # unchanged


def test_the_numerator_is_the_credited_holder_value_with_the_fee_burn(monkeypatch):
    """Maple: revenue to holders = the credited holder value, day for day. Near: the gas burn credited inside holder
    value (HOLDER_VALUE burn_credited) is revenue sent to holders too (R21b, Jake 2026-11-25; was R20: taken out)."""
    days = pd.date_range("2026-08-01", "2026-08-10")
    hv_m = pd.Series([100.0, 0.0, 250.0, 0.0, 0.0, 40.0, 0.0, 0.0, 0.0, 10.0], index=days)
    burn = pd.Series(2.0, index=days)                                   # NEAR burned a day
    px = pd.Series(5.0, index=days)                                     # $ — the burn is $10 a day
    act = pd.Series(30.0, index=days[:8])                               # the buyback's last stored day: 08-08
    hv_n = pd.Series(40.0, index=days[:8])                              # 30 buyback + 10 burn, credited to 08-08
    bc = config.HOLDER_VALUE["Near"]["burn_credited"]
    groups = {("Maple", "holder_value_revenue_usd"): _g("Maple", "holder_value_revenue_usd", hv_m),
              ("Near", "holder_value_revenue_usd"): _g("Near", "holder_value_revenue_usd", hv_n),
              ("Near", bc["metric"]): _g("Near", bc["metric"], burn),
              ("Near", "price_usd"): _g("Near", "price_usd", px),
              ("Near", "actual_buyback_usd"): _g("Near", "actual_buyback_usd", act)}
    monkeypatch.setattr(bw, "_SCOPE", [config.PROJECT_BY_NAME["Maple"], config.PROJECT_BY_NAME["Near"],
                                       config.PROJECT_BY_NAME["Fluid"]])
    bw._revenue_to_holders_views(groups)
    # Maple credits no fee burn: its key reads the credited holder value itself (no second series)
    assert ("Maple", "revenue_to_holders_usd") not in groups
    assert bw.a3_key_numerator("Maple") == "holder_value_revenue_usd"
    assert bw.a3_key_numerator("Near") == "revenue_to_holders_usd"
    m = hv_m
    n = bw._daily(groups[("Near", "revenue_to_holders_usd")])
    assert list(n.index) == list(days[:8])
    # R21b: 320 = 8 x ($30 buyback + $10 gas burn), the credited holder value itself (was R20: 240, the burn taken out)
    assert n.sum() == pytest.approx(8 * 40.0)
    assert (n == bw._daily(groups[("Near", "holder_value_revenue_usd")])).all()
    assert ("Fluid", "revenue_to_holders_usd") not in groups            # no holder value rows: unknown, never 0
    # the key on these: $ to holders ÷ $ of new supply, per covered day
    assert m.sum() / len(m) / (2_000.0 / 10) == pytest.approx(0.2)      # 400 over 10 days ÷ 2,000 of outflows over 10


def test_the_key_reads_zero_for_a_paused_or_off_programme_and_names_its_legs():
    col = bw._a3_key_col(type("R", (), {"D": lambda self, r, m, c="q0": f"V!{m}_{c}"})())   # R21: builds read refs
    head, build, _fmt, kind, _bold, meta = col
    assert head == bw.A3_KEY_HEAD and kind == "calc" and meta["metric_fn"] is bw.a3_key_numerator
    for name in ("Fluid", "Ether.fi", "Morpho"):
        # R21 (decision 5): no longer forced to 0 — the key reads what the data reads; the reader leaves it unranked
        assert build(5, config.PROJECT_BY_NAME[name]) != "=0", name
        note = bw.a3_key_note(config.PROJECT_BY_NAME[name])
        assert f"A3 KEY — {config.archetype_status(name, 3)}" in note and "UNRANKED" in note
    note = bw.a3_key_note(config.PROJECT_BY_NAME["Maple"])
    assert "revenue sent to holders ($) ÷ value of new supply ($)" in note
    assert "treasury- or emissions-funded buyback is recorded, not counted" in note
    assert "outflow_" in note and "NET HOLDER YIELD subtracts" in note
    assert "+ the fee burn" in bw.a3_key_note(config.PROJECT_BY_NAME["Near"])                  # R21b
    # the denominator is the set NET HOLDER YIELD subtracts
    for name in ("Maple", "Aerodrome", "Sky", "Hyperliquid", "Pendle"):
        p = config.PROJECT_BY_NAME[name]
        assert bw.a3_key_cats(p) == bw._cp_cats(p)
    assert "revenue_to_holders_usd" not in config.metrics_for_project(config.PROJECT_BY_NAME["Maple"])
    assert "revenue_to_holders_usd" in config.metrics_for_project(config.PROJECT_BY_NAME["Near"])
    assert set(config.METRICS["revenue_to_holders_usd"]["only_projects"]) == {"Near", "Ethereum", "Aethir"}   # R21b


# ------------------------------------------------------------------------------------------------ 3b. the key's numbers
def _new_key(name: str, vals: dict, monkeypatch, shift: int = 0):
    """The R20 key's own formula evaluated on given Data values (test_round_p4_q5's harness: a value not given holds
    the build's 'n/a' text, never a blank or a 0)."""
    from test_round_p4_q5 import FakeRefs, evaluate
    monkeypatch.setattr(bw, "LAG_MATCH", {})
    monkeypatch.setattr(bw, "PAID_LATER_NOT_READ", {})
    monkeypatch.setitem(bw._SHIFT, "k", shift)
    R = FakeRefs(vals)
    f = bw._a3_key_col(R, {})[1](5, config.PROJECT_BY_NAME[name])
    return evaluate(f, vals, R), f


def _full(name: str, numer: float, outflows: dict, days: float = 90.0) -> dict:
    p = config.PROJECT_BY_NAME[name]
    cats = bw.a3_key_cats(p)
    nm = bw.a3_key_numerator(name)
    vals = {(nm, "q0"): numer, (nm, "q0_covered_days"): days}
    for c in cats:
        vals[(f"outflow_{c}_usd", "q0")] = float(outflows.get(c, 0.0))
        vals[(f"outflow_{c}_usd", "q0_covered_days")] = days
    return vals


def test_the_key_is_dollars_to_holders_over_dollars_of_new_supply(monkeypatch):
    """Aerodrome on Jake's 2026-11-20 export: $32,653,633 to holders ÷ ($42,694,770 (b) + $3,247,910 (c) + $0 (e)) of
    new supply over the same 90 days = 0.7107x by the Q0 totals (the build's cell, per covered day: 0.719)."""
    monkeypatch.setattr(bw, "PAID_LATER_NOT_READ", {})
    assert bw.a3_key_cats(config.PROJECT_BY_NAME["Aerodrome"]) == ["b", "c", "e"]
    vals = _full("Aerodrome", 32_653_632.62, {"b": 42_694_769.84, "c": 3_247_910.37, "e": 0.0})
    v, f = _new_key("Aerodrome", vals, monkeypatch)
    assert v == pytest.approx(32_653_632.62 / (42_694_769.84 + 3_247_910.37), rel=1e-12)
    assert round(v, 4) == 0.7107
    assert '|holder_value_revenue_usd"' in f or "V!$A$" in f            # reads the credited holder value
    # per COVERED day: the same $ over half the days on one side is half / double the rate — blocked, never mixed
    short = dict(vals)
    short[("outflow_b_usd", "q0_covered_days")] = 45.0
    v2, _f = _new_key("Aerodrome", short, monkeypatch)
    assert isinstance(v2, str) and v2.startswith("BLOCKED")
    # held, burned or distributed makes no difference to the key (the supply rule is not A3's key): the numerator is
    # the $ sent to holders — the formula reads no burned / held / retired token series
    R_f = bw._a3_key_col(bw.Refs(100, 10, ["2026-09"]), {})[1](5, config.PROJECT_BY_NAME["Aerodrome"])
    assert "buyback_burned_tokens" not in R_f and "retired_buyback_tokens" not in R_f
    assert '|holder_value_revenue_usd"' in R_f and '|outflow_b_usd"' in R_f and '|outflow_c_usd"' in R_f
    near = bw._a3_key_col(bw.Refs(100, 10, ["2026-09"]), {})[1](5, config.PROJECT_BY_NAME["Near"])
    assert '|revenue_to_holders_usd"' in near and '|holder_value_revenue_usd"' not in near   # (= holder value, R21b)


def test_no_new_supply_ranks_first_and_unknown_is_never_zero(monkeypatch):
    v, _f = _new_key("Aerodrome", _full("Aerodrome", 1000.0, {}), monkeypatch)
    assert v == reader.NO_NEW_SUPPLY == bw.A3_NO_NEW_SUPPLY
    v, _f = _new_key("Aerodrome", _full("Aerodrome", 0.0, {}), monkeypatch)
    assert isinstance(v, str) and v.startswith("n/a — 0 to holders over 0 new supply")
    v, _f = _new_key("Aerodrome", _full("Aerodrome", 0.0, {"b": 500.0}), monkeypatch)
    assert v == 0.0                                                     # a MEASURED 0 to holders is a 0
    vals = _full("Aerodrome", 1000.0, {"b": 500.0})
    del vals[("outflow_c_usd", "q0")]                                   # a leg with nothing stored
    v, _f = _new_key("Aerodrome", vals, monkeypatch)
    assert isinstance(v, str) and not v.startswith("no new supply")     # a code, never a number built on a gap
    vals = _full("Aerodrome", 1000.0, {"b": 500.0})
    del vals[("holder_value_revenue_usd", "q0")]
    v, _f = _new_key("Aerodrome", vals, monkeypatch)
    assert isinstance(v, str)


def test_a_paused_key_is_zero_today_and_measured_in_an_earlier_quarter(monkeypatch):
    """Fluid (paused): R21 (decision 5) — the key reads what the data reads (was: forced to 0), with the label and
    unranked; a quarter before the pause keeps what was measured then."""
    v, f = _new_key("Fluid", {}, monkeypatch)
    assert f != "=0" and "holder_value_revenue_usd" in f
    p = config.PROJECT_BY_NAME["Fluid"]
    monkeypatch.setitem(bw._SHIFT, "k", 2)
    f2 = bw._a3_key_col(bw.Refs(100, 10, ["2026-09"]), {})[1](5, p)
    assert f2 != "=0" and "holder_value_revenue_usd" in f2 and "outflow_" in f2


# ------------------------------------------------------------------------------------------------ 4. history filling
ASOF = pd.Timestamp("2026-11-20")


def test_history_filling_by_reason_with_its_guards():
    ok = "needs two years of history " + reader.history_marker(730, "2026-11-19", "2027-03-31")
    assert reader.history_filling(ok, ASOF) == date(2027, 3, 31)
    assert reader.history_filling_text(ok, ASOF) == "history filling - forms on 2027-03-31"
    # a window shorter than 12 months keeps today's behaviour
    assert reader.history_filling(reader.history_marker(180, "2026-11-19", "2027-03-31"), ASOF) is None
    assert reader.history_filling(reader.history_marker(364, "2026-11-19", "2027-03-31"), ASOF) is None
    assert reader.history_filling(reader.history_marker(365, "2026-11-19", "2027-03-31"), ASOF) == date(2027, 3, 31)
    # the series must still be read: its last day within 7 days of the as-of
    assert reader.history_filling(reader.history_marker(730, "2026-11-13", "2027-03-31"), ASOF) == date(2027, 3, 31)
    assert reader.history_filling(reader.history_marker(730, "2026-11-12", "2027-03-31"), ASOF) is None
    assert reader.history_filling(reader.history_marker(730, "2026-08-25", "2027-03-31"), ASOF) is None   # Ethereum
    # a forming day that has passed is not "filling"
    assert reader.history_filling(reader.history_marker(730, "2026-11-19", "2026-11-19"), ASOF) is None
    assert reader.history_filling(reader.history_marker(730, "2026-11-19", "2026-11-20"), ASOF) == date(2026, 11, 20)
    # prose alone never qualifies: no marker, no label
    assert reader.history_filling("needs two years of history; forms on 2027-03-31", ASOF) is None
    assert reader.history_filling("", ASOF) is None and reader.history_filling(ok, None) is None


def _sheet():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.cell(5, 1, "Near")
    cols = [reader.Col("Project", ""), reader.Col("Token", ""), reader.Col("Archetype key", "", reader.FMT_X),
            reader.Col("Rank in archetype", "", reader.FMT_RANK)]
    for i, c in enumerate(cols, start=1):
        c.letter = openpyxl.utils.get_column_letter(i)
    return reader.Sheet(ws, cols), wb


def test_a_qualifying_cell_reads_history_filling_grey_and_done_check_does_not_count_it(monkeypatch):
    sh, wb = _sheet()
    monkeypatch.setitem(reader._BUILD_ASOF, "day", ASOF)
    ok = "No measured key: needs two years of history " + reader.history_marker(730, "2026-11-19", "2027-03-31")
    reader.put_code(sh, 5, sh.cols[2], "n/a", ok)
    c = sh.ws.cell(5, 3)
    assert c.value == "history filling - forms on 2027-03-31"
    assert str(c.fill.fgColor.rgb)[-6:] == "EDEDED" and str(c.font.color.rgb)[-6:] == reader.CODES["history filling"]
    done_check.HISTORY_FILLING_SEEN.clear()
    assert done_check.judge_cell("Near", "Summary", "Archetype key", c, c.value, ASOF.date(), None) == []
    assert done_check.HISTORY_FILLING_SEEN["Near"] == ["Summary: Archetype key — forms on 2027-03-31"]
    # a short window, or a stopped series, keeps its code — and done_check still judges it
    for bad in (reader.history_marker(180, "2026-11-19", "2027-03-31"),
                reader.history_marker(730, "2026-08-25", "2027-03-31")):
        reader.put_code(sh, 5, sh.cols[2], "n/a", "no figure " + bad)
        assert sh.ws.cell(5, 3).value == "n/a"
    # a label whose reason has stopped qualifying (the series went quiet) is a blocker, not a free pass
    reader.put_code(sh, 5, sh.cols[2], "n/a", ok)
    late = date(2026, 12, 15)                                           # the series was last read 26 days before
    bl = done_check.judge_cell("Near", "Summary", "Archetype key", sh.ws.cell(5, 3), sh.ws.cell(5, 3).value, late, None)
    assert len(bl) == 1 and bl[0].cls == "history" and "no longer qualifies" in bl[0].why
    # a code that is not n/a / blocked is never relabelled
    reader.put_code(sh, 5, sh.cols[2], "by design", ok)
    assert sh.ws.cell(5, 3).value == "by design"


def test_the_settlement_reasons_carry_the_marker_only_while_the_series_is_read():
    met = config.ARTEMIS_SETTLEMENT["metric"]
    idx = pd.date_range("2026-04-01", "2026-11-20")
    daily, circ = pd.Series(100.0, index=idx), pd.Series(1000.0, index=idx)
    g = {("Near", met): _g("Near", met, daily), ("Near", "circulating_supply_used"): _g("Near", "x", circ)}
    v, why = reader.settlement_key(g, "Near")
    assert v is None and reader.history_filling(why, ASOF) == date(2028, 3, 30)        # r0 + 729
    stopped = {("Near", met): _g("Near", met, daily[:"2026-08-25"]), ("Near", "circulating_supply_used"): g[
        ("Near", "circulating_supply_used")]}
    _v, why_s = reader.settlement_key(stopped, "Near")
    assert "history filling" not in why_s and reader.history_filling(why_s, ASOF) is None
    # the 365-day sum (NRR): r0 + 364, from the build's own reason
    gg = {("Near", met): g[("Near", met)], ("Near", "price_usd"): _g("Near", "price_usd", circ)}
    mk = bw._settlement_history_marker(gg, {"name": "Near"})
    assert reader.history_filling(mk, ASOF) == date(2027, 3, 31) and "window 365d" in mk   # 2026-04-01 + 364
    gg[("Near", met)] = _g("Near", met, daily[:"2026-08-25"])
    assert bw._settlement_history_marker(gg, {"name": "Near"}) == ""


# ------------------------------------------------------------------------------------------------ 5. 6 months on 6
def _settle(first, last, step_on=None, level=100.0, later=150.0, circ=1000.0, project="Near"):
    met = config.ARTEMIS_SETTLEMENT["metric"]
    idx = pd.date_range(first, last)
    d = pd.Series(level, index=idx)
    if step_on:
        d[idx >= step_on] = later
    return {(project, met): _g(project, met, d),
            (project, "circulating_supply_used"): _g(project, "c", pd.Series(circ, index=idx))}


def test_six_on_six_is_the_change_of_settlement_per_token_between_two_full_halves():
    g = _settle("2025-11-01", "2026-11-20", step_on="2026-05-23")       # the later 182 days at 150, the earlier at 100
    v, how = reader.settlement_six_on_six(g, "Near")
    assert v == pytest.approx(0.5)
    assert "2026-05-23..2026-11-20 against 2025-11-22..2026-05-22" in how
    assert "$54.75 against $36.50 per token, annualised" in how         # 150 × 182 ÷ 1000 × 365 ÷ 182 = 54.75
    # per TOKEN: the same volume over a supply that doubled at the half reads −25% (150/2000 against 100/1000)
    met = config.ARTEMIS_SETTLEMENT["metric"]
    idx = pd.date_range("2025-11-01", "2026-11-20")
    c = pd.Series(1000.0, index=idx)
    c[idx > "2026-05-22"] = 2000.0
    g2 = dict(g)
    g2[("Near", "circulating_supply_used")] = _g("Near", "c", c)
    assert reader.settlement_six_on_six(g2, "Near")[0] == pytest.approx(-0.25)
    assert met


def test_six_on_six_needs_both_halves_hole_free_and_is_never_filled():
    g = _settle("2026-04-01", "2026-11-20")                              # 234 days held: 364 needed
    v, why = reader.settlement_six_on_six(g, "Near")
    assert v is None and "both halves must hold every day" in why and "hold 52" in why
    met = config.ARTEMIS_SETTLEMENT["metric"]
    full = _settle("2025-11-01", "2026-11-20")
    s = bw._daily(full[("Near", met)]).drop(pd.Timestamp("2026-02-10"))  # one hole in the earlier half
    full[("Near", met)] = _g("Near", met, s)
    v, why = reader.settlement_six_on_six(full, "Near")
    assert v is None and "the earlier 182" in why and "hold 181" in why


def test_the_interim_figure_is_for_near_and_ethereum_only_and_is_dropped_when_the_one_year_rule_forms():
    assert config.A1_INTERIM_KEY["projects"] == ("Near", "Ethereum") and config.A1_INTERIM_KEY["days"] == 182
    g = _settle("2025-11-01", "2026-11-20", step_on="2026-05-23")
    assert reader.interim_key(g, "Near")[0] == pytest.approx(0.5)
    ge = _settle("2025-11-01", "2026-11-20", step_on="2026-05-23", project="Ethereum")
    assert reader.interim_key(ge, "Ethereum")[0] == pytest.approx(0.5)
    gs = _settle("2025-11-01", "2026-11-20", step_on="2026-05-23", project="Solana")
    v, why = reader.interim_key(gs, "Solana")
    assert v is None and "not shown for this chain" in why
    # the 1-year rule formed: the interim figure is dropped for that chain
    idx = pd.date_range("2025-11-01", "2026-11-20")
    s365 = pd.Series(36_500.0, index=idx)
    s365[idx >= "2026-11-01"] = 40_000.0
    g[("Near", "settlement_volume_365d_usd")] = _g("Near", "s", s365)
    assert reader.settlement_key(g, "Near")[0] is not None
    v, why = reader.interim_key(g, "Near")
    assert v is None and why.startswith("dropped")
    # it is not a key: the ranking never sees it (a project with no 1-year key stays unranked)
    assert reader._rank_rows({"Near": None, "Solana": 0.1}) and \
        dict((n, rk) for n, rk, _v in reader._rank_rows({"Near": None, "Solana": 0.1}))["Near"] is None


# ------------------------------------------------------------------------------------------------ 6. the A3 rows
_DIGIT = re.compile(r"[0#]")


def _sections(fmt: str) -> list:
    """A number format's sections with the quoted literals removed."""
    return [re.sub(r'"[^"]*"', "", sec) for sec in str(fmt).split(";")]


def test_a_status_zero_keeps_its_digits_in_every_section():
    """Item 7 (Fluid's A3 row): the zero used to be drawn by a text-only section ('"0.0x — halted since …"'), which a
    spreadsheet shows as #### or not at all — the key column read empty and the row read shifted."""
    for fmt in (reader.FMT_X, reader.FMT_PCT, reader.FMT_USD, reader.FMT_TOK):
        z = reader._zero_format(fmt, "halted since 2026-05-11")
        assert all(_DIGIT.search(sec) for sec in _sections(z)), z
        assert "halted since 2026-05-11" in z.split(";")[-1]
    assert reader._zero_format(reader.FMT_X, "paused").split(";")[-1] == '0.0"x — paused"'


def test_the_status_label_sits_beside_the_key_and_the_rank_and_both_stay_numbers():
    sh, wb = _sheet()
    sh.ws.cell(5, 1, "Fluid")
    data = reader.Data(wb.create_sheet(reader.DATA_SHEET))
    reader.put_number(sh, data, 5, sh.cols[2], "Fluid", "key", 0.0, None, "", zero_label="halted since 2026-05-11")
    reader.put_number(sh, data, 5, sh.cols[3], "Fluid", "rank", 4, None, "")
    lab = config.archetype_status("Fluid", 3)                  # R21: "paused, no restart condition stated"
    assert reader.status_label_cells(sh, 5, sh.cols[2], sh.cols[3], "Fluid", 3) == lab
    key, rank = sh.ws.cell(5, 3), sh.ws.cell(5, 4)
    assert key.number_format == f'0.0"x — {lab}"' and rank.number_format == f'0" — {lab}"'
    assert data.value("Fluid", "key") == 0.0 and data.value("Fluid", "rank") == 4.0      # the key is 0, the rank 4
    assert key.value == f"={data.ref('Fluid', 'key')}" and rank.value == f"={data.ref('Fluid', 'rank')}"
    # no label, no change
    reader.put_number(sh, data, 5, sh.cols[2], "Maple", "key", 0.3, None, "")
    assert reader.status_label_cells(sh, 5, sh.cols[2], sh.cols[3], "Maple", 3) == ""
    assert sh.ws.cell(5, 3).number_format == reader.FMT_X


def a3_rows(reader_path) -> list:
    """[(project, key value or code, key format, rank value or code, rank format)] of the reader's A3 tab — the key
    column by its header, the rank column by its header; a formula is resolved on _data."""
    wb = openpyxl.load_workbook(reader_path)
    ws, dd = wb[reader.A3_TAB], wb[reader.DATA_SHEET]
    heads = {str(ws.cell(4, c).value): c for c in range(1, ws.max_column + 1) if ws.cell(4, c).value}
    kc, rc = heads[reader.ARCHETYPE_KEYS[3][0]], heads["Rank in A3"]

    def val(c):
        v = c.value
        m = re.fullmatch(rf"={reader.DATA_SHEET}!\$([A-Z]+)\$(\d+)", str(v or ""))
        return dd[f"{m.group(1)}{m.group(2)}"].value if m else v
    return [(ws.cell(r, 1).value, val(ws.cell(r, kc)), ws.cell(r, kc).number_format, val(ws.cell(r, rc)),
             ws.cell(r, rc).number_format) for r in range(5, ws.max_row + 1)
            if ws.cell(r, 1).value and ws.cell(r, 2).value is not None]


def check_a3_rows(rows: list) -> None:
    """Every A3 row: the key column holds the key (a ratio, or a code — never a rank), the rank column the rank (a
    whole number 1..n, or a code); the ranks of the ranked rows are 1..n by the key, high to low; every number format
    draws digits in every section."""
    ranked = [(n, k, rk) for n, k, _kf, rk, _rf in rows if isinstance(rk, (int, float))]
    assert sorted(rk for _n, _k, rk in ranked) == list(range(1, len(ranked) + 1)), ranked
    for n, k, kf, rk, rf in rows:
        assert isinstance(k, (int, float)) or str(k).split(" - ")[0] in reader.CODES, (n, k)
        assert isinstance(rk, (int, float)) or rk in reader.CODES, (n, rk)
        if isinstance(rk, (int, float)):
            assert float(rk).is_integer() and (isinstance(k, (int, float)) or k == reader.NO_NEW_SUPPLY), (n, k, rk)
            assert all(_DIGIT.search(s) for s in _sections(rf)), (n, rf)
        if isinstance(k, (int, float)):
            assert all(_DIGIT.search(s) for s in _sections(kf)), (n, kf)
            assert 'x' in kf, (n, kf)                                    # a ratio's format, not a rank's
        lab = config.archetype_status(n, 3)
        if lab:
            # R21 (decision 5): the label + "unranked"; the key is what the data reads (was: 0, ranked)
            assert rk == "unranked", (n, rk)
            if isinstance(k, (int, float)):
                assert kf.endswith(f'— {lab}"'), (n, k, kf)
    order = sorted(ranked, key=lambda x: x[2])
    nums = [k for _n, k, _rk in order if isinstance(k, (int, float))]
    assert nums == sorted(nums, reverse=True), order                     # ranked by the key column's own values


def test_every_a3_row_holds_the_key_in_the_key_column_and_the_rank_in_the_rank_column(tmp_path_factory):
    from shared_build import plain_build
    rec = plain_build(tmp_path_factory, "r20_arch")
    rows = a3_rows(rec["reader"])
    assert {"Fluid", "Maple", "Sky", "Hyperliquid", "Uniswap", "Morpho"} <= {r[0] for r in rows}
    check_a3_rows(rows)
    # R21 (decision 5): a paused / off programme reads "unranked"; its key is what the data reads (0 only where the
    # data says 0, else a code) — never ranked on a key of 0
    for name in ("Fluid", "Morpho"):
        row = next(r for r in rows if r[0] == name)
        assert row[3] == "unranked", row
        assert row[1] == 0 or str(row[1]).split(" - ")[0] in reader.CODES, row
