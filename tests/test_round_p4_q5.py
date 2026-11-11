"""ROUND P4-Q5 (Jake 2026-11-03, SPEC P4-Q5a..d) — A3 RULES.

Q5a  A3's key = BURNED buyback ÷ (emissions + unlocks), for every A3 project (build_workbook._a3_key_col, A3_KEY_HEAD);
     emissions + unlocks a measured 0 with a burn above 0 reads the code "no new supply" and ranks first; ties are broken
     by net holder yield. The emissions-only key (retired ÷ emissions, P1-6) and its P3-2b fallback are retired.
Q5b  A held buyback (Maple's SYRUP Strategic Fund, Sky's held share) is not burned, so not in the key; while held it is out
     of the tradeable float (config.HELD_BUYBACK_FLOAT, build_workbook._cp_float) and returns when the held stock falls.
Q5c  "Buying pressure on float, % a year" = every market buyback (any destination) annualised ÷ tradeable float, on the A3
     and CP audit tabs, the reader's A3 tab and Comparison.
Q5d  Holder value yield is not changed.

The formula checks evaluate the builders' own formulas (xlcalc) on a small workbook whose inputs are plain cells; the
fixture checks build once on the committed real dumps (fixtures/real_*.json), as test_round_p3_2 does. No network.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import reader  # noqa: E402
import trajectory  # noqa: E402
import xlcalc  # noqa: E402
from shared_build import plain_build  # noqa: E402

A3, CP = "A3 Revenue Buyback", "CP Comparability"
P = config.PROJECT_BY_NAME


# ============================================================================================== formula harness
class FakeRefs:
    """Stands in for build_workbook.Refs: every (metric, column) the builder reads is a cell on sheet V holding the
    given value — a missing one holds the build's own 'n/a' text (a Data cell with nothing stored), never a blank."""

    def __init__(self, vals: dict):
        self.vals, self.cells = vals, {}

    def D(self, r, metric, col="q0"):
        k = (metric, col)
        if k not in self.cells:
            self.cells[k] = len(self.cells) + 1
        return f"V!$A${self.cells[k]}"


def evaluate(formula: str, vals: dict, R: FakeRefs):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "F"
    v = wb.create_sheet("V")
    for (m, c), i in R.cells.items():
        v.cell(i, 1, vals.get((m, c), f"n/a — {m} ({c}): no rows stored"))
    cfg = wb.create_sheet(bw.CFG.strip("'"))
    for name, row in bw.CFG_GLOBAL_ROWS.items():
        cfg.cell(row, 2, float(bw.GLOBALS[name]) if isinstance(bw.GLOBALS.get(name), (int, float)) else None)
    ws.cell(1, 1, formula)
    return xlcalc.Workbook(wb).value("F", 1, 1)


def key_value(name: str, vals: dict):
    R = FakeRefs(vals)
    f = bw._a3_key_col(R, {})[1](5, P[name])
    return evaluate(f, vals, R), f


def days(m, n=90.0):
    return {(m, "q0_covered_days"): n}


# ============================================================================================== Q5a: the key
def test_key_is_burned_over_emissions_plus_unlocks_for_every_a3_project():
    """The formula reads the BURNED buyback (never the retired = burned + held series), the absorption emissions and the
    unlock releases; Sky at its emission rate in force (no unlock set declared); Ether.fi with no emissions leg (not
    emitted, by declaration); the old key's header, builder and fallback are gone."""
    for p in config.PROJECTS:
        if 3 not in p["archetypes"] or bw._retirement_zero(p):
            continue
        f = bw._a3_key_col(bw.Refs(100, 10, ["2026-09"]), {})[1](5, p)
        assert '|buyback_burned_tokens"' in f and "|retired_buyback_tokens" not in f, p["name"]
        assert ('|unlock_release_tokens"' in f) == bool(config.unlock_stock_metrics(p["name"])), p["name"]
    hl = bw._a3_key_col(bw.Refs(100, 10, ["2026-09"]), {})[1](5, P["Hyperliquid"])
    assert '|emissions_tokens"' in hl and '|unlock_release_tokens"' in hl
    sky = bw._a3_key_col(bw.Refs(100, 10, ["2026-09"]), {})[1](5, P["Sky"])
    assert '|emissions_rate_current_annual_tokens"' in sky and '|emissions_tokens"' not in sky
    efi = bw._a3_key_col(bw.Refs(100, 10, ["2026-09"]), {})[1](5, P["Ether.fi"])
    assert '|emissions_tokens"' not in efi and '|unlock_release_tokens"' in efi
    for gone in ("RETIRE_VS_EMISSIONS_HEAD", "_retire_vs_emissions_col", "A3_KEY_FALLBACK_TEXT", "a3_key_fallback",
                 "_a3_fallback_applies", "_a3_emissions_zero_in"):
        assert not hasattr(bw, gone), gone
    assert bw.A3_KEY_TRAJECTORY_ID == reader.A3_TRAJECTORY_ID == "a3_burned_vs_new_supply"
    assert "a3_retire_vs_emissions" not in config.TRAJECTORY["rows"] and bw.A3_KEY_TRAJECTORY_ID in config.TRAJECTORY["rows"]


def test_key_value_jakes_hyperliquid_numbers_and_no_new_supply():
    """Jake's store (2026-11-03): Hyperliquid 2.49M burned ÷ 7.08M (emissions + unlocks) ≈ 0.35x — the formula on those
    numbers over a full 90-day window. Emissions + unlocks a MEASURED 0 with a burn reads 'no new supply'; 0 ÷ 0 is n/a."""
    base = {**days("buyback_burned_tokens"), **days("emissions_tokens"), **days("unlock_release_tokens")}
    v, _f = key_value("Hyperliquid", {**base, ("buyback_burned_tokens", "q0"): 2.49e6,
                                      ("emissions_tokens", "q0"): 5.0e6, ("unlock_release_tokens", "q0"): 2.08e6})
    assert v == pytest.approx(2.49 / 7.08, rel=1e-12) and round(v, 2) == 0.35
    v, _f = key_value("Hyperliquid", {**base, ("buyback_burned_tokens", "q0"): 1000.0, ("emissions_tokens", "q0"): 0.0,
                                      ("unlock_release_tokens", "q0"): 0.0})
    assert v == reader.NO_NEW_SUPPLY == bw.A3_NO_NEW_SUPPLY
    v, _f = key_value("Hyperliquid", {**base, ("buyback_burned_tokens", "q0"): 0.0, ("emissions_tokens", "q0"): 0.0,
                                      ("unlock_release_tokens", "q0"): 0.0})
    assert isinstance(v, str) and v.startswith("n/a — 0 burned over 0 new supply")


def test_unknown_is_never_0_a_leg_not_measured_is_a_code():
    """An emissions or unlock leg with nothing stored reads n/a; a leg covering under a week (or a span a tenth of the
    window off the burn's) reads BLOCKED — never a 0 that would rank, never 'no new supply'."""
    base = {**days("buyback_burned_tokens"), **days("unlock_release_tokens"), ("buyback_burned_tokens", "q0"): 1e6,
            ("unlock_release_tokens", "q0"): 1e6}
    v, _f = key_value("Hyperliquid", base)                                     # emissions not stored
    assert isinstance(v, str) and v.startswith("n/a")
    v, _f = key_value("Hyperliquid", {**base, ("emissions_tokens", "q0"): 0.0, **days("emissions_tokens", 5.0)})
    assert isinstance(v, str) and v.startswith("BLOCKED — unequal coverage") and "emissions_tokens" in v
    v, _f = key_value("Hyperliquid", {**base, ("emissions_tokens", "q0"): 0.0, **days("emissions_tokens", 60.0)})
    assert isinstance(v, str) and v.startswith("BLOCKED")
    v, _f = key_value("Hyperliquid", {**days("emissions_tokens"), **days("unlock_release_tokens"),
                                      ("emissions_tokens", "q0"): 0.0, ("unlock_release_tokens", "q0"): 0.0})
    assert isinstance(v, str) and v.startswith("n/a")                          # burn not measured: not 'no new supply'


def test_held_and_distributed_buybacks_are_not_burned(monkeypatch):
    """The split view: Maple's SSF (hold) and Pendle's sPENDLE payout (distribute) burn nothing; Sky burns 5/27.5 of
    what it buys from 2026-08-17 and nothing before (its purchases were held at the Pause Proxy)."""
    monkeypatch.setattr(bw, "_SCOPE", list(bw.PROJECTS))
    idx = pd.date_range("2026-08-10", "2026-08-24")
    groups = {}
    for n in ("Maple", "Pendle", "Sky"):
        groups[(n, "actual_buyback_tokens")] = pd.DataFrame(
            {"date": idx, "project": n, "metric": "actual_buyback_tokens", "value": 1000.0, "tier": 1, "source": "x"})
    bw._buyback_split_views(groups)
    for n in ("Maple", "Pendle"):
        assert groups[(n, "buyback_burned_tokens")]["value"].sum() == 0.0, n
    sky = groups[("Sky", "buyback_burned_tokens")].set_index("date")["value"]
    assert (sky[sky.index < "2026-08-17"] == 0.0).all()
    assert sky[sky.index >= "2026-08-17"].tolist() == pytest.approx([1000.0 * 5 / 27.5] * 8)
    held = groups[("Sky", "buyback_held_tokens")].set_index("date")["value"]
    assert held[held.index < "2026-08-17"].tolist() == pytest.approx([1000.0] * 7)
    # Maple's whole buyback is held; with the key on the burned series, Maple's key is 0 over its unlocks
    v, _f = key_value("Maple", {**days("buyback_burned_tokens", 92.0), **days("emissions_tokens"),
                                **days("unlock_release_tokens"), ("buyback_burned_tokens", "q0"): 0.0,
                                ("emissions_tokens", "q0"): 0.0, ("unlock_release_tokens", "q0"): 12e6})
    assert v == 0.0


# ============================================================================================== ranking
def test_no_new_supply_ranks_first_and_ties_are_broken_by_net_holder_yield(monkeypatch):
    monkeypatch.setattr(reader, "parked_reason", lambda n: "")
    nns = reader.Src("A3", "X5", value=reader.NO_NEW_SUPPLY, text=reader.NO_NEW_SUPPLY)
    assert reader.key_number(nns) == reader.NO_NEW_SUPPLY_KEY
    assert reader.key_number(reader.Src("A3", "X6", value=0.35, num=0.35)) == 0.35
    assert reader.key_number(reader.Src("A3", "X7", value="n/a — x", text="n/a — x")) is None
    assert reader.code_of(reader.NO_NEW_SUPPLY) == reader.NO_NEW_SUPPLY and reader.NO_NEW_SUPPLY in reader.CODES
    inf = reader.NO_NEW_SUPPLY_KEY
    keys = {"Hyperliquid": (3, 0.35, None, ""), "Sky": (3, 0.13, None, ""), "Maple": (3, 0.0, None, ""),
            "Fluid": (3, 0.0, None, ""), "Pendle": (3, 0.0, None, ""), "Ether.fi": (3, 0.0, None, ""),
            "X": (3, inf, None, ""), "Y": (3, inf, None, ""), "Aave": (3, None, None, "no key")}
    tb = {"Maple": -0.01, "Fluid": 0.02, "Pendle": -0.03, "Ether.fi": None, "X": -0.5, "Y": 0.1}
    got = reader._arch_ranking(keys, 3, tb)
    assert [n for n, _r, _v in got] == ["Y", "X", "Hyperliquid", "Sky", "Fluid", "Maple", "Pendle", "Ether.fi", "Aave"]
    assert [r for _n, r, _v in got] == [1, 2, 3, 4, 5, 6, 7, 8, None]
    note = reader._tie_note("X", got, tb)
    assert "Tied on the key (no new supply) with Y" in note and "net holder yield" in note
    import done_check
    assert done_check.classify("no new supply", "", pd.Timestamp("2026-11-03").date())[0] is True


# ============================================================================================== Q5b: float
def test_tradeable_float_leaves_out_the_held_buyback_and_takes_it_back_when_it_moves_out():
    maple = P["Maple"]
    assert config.held_buyback_float_metric("Maple") == "ssf_holdings_tokens"
    # PR3.3 (Jake 2026-11-11): the Fund's chart counts its Safe 0x58BE…, which circulating already subtracts — the
    # float subtracts the chart LESS the Safe (it took the Safe out twice: 1,176,793,290 - 78,035,331)
    vals = {("circulating_supply_used", "now"): 1_176_793_290.0, ("ssf_holdings_tokens", "now"): 78_035_331.0,
            ("maple_ssf_safe_tokens", "now"): 48_624_987.59}
    R = FakeRefs(vals)
    f = "=" + bw._cp_float(R, 5, maple)
    assert evaluate(f, vals, R) == pytest.approx(1_176_793_290.0 - (78_035_331.0 - 48_624_987.59))
    out = {**vals, ("ssf_holdings_tokens", "now"): 70_035_331.0}            # 8M moved out of the Fund
    assert evaluate(f, out, R) - evaluate(f, vals, R) == pytest.approx(8_000_000.0)
    gone = {k: v for k, v in vals.items() if k[0] != "ssf_holdings_tokens"}
    assert evaluate(f, gone, R) == "n/a"                                      # the held stock unknown: float unknown
    # where the held stock is already out of circulating (Sky's Pause Proxy, Chainlink's Reserve, Fluid's treasury) it
    # is not subtracted twice: the declared leg is one circulating subtracts
    for n, h in config.HELD_BUYBACK_FLOAT.items():
        sh = config.buyback_destination_shares(n) or {}
        rows = config.BUYBACK_DESTINATION_SHARES.get(n) or []
        assert any(r.get("hold") for r in rows) or sh.get("hold"), n
        if h["how"] == "outside":
            assert h["leg"] in (config.CIRCULATING_ONCHAIN[n].get("subtract") or ()), n
            assert config.held_buyback_float_metric(n) is None
            assert "ISNUMBER(V!" in bw._cp_float(FakeRefs({}), 5, P[n])
        else:
            assert h["how"] == "subtract" and h["held"] not in (config.CIRCULATING_ONCHAIN[n].get("subtract") or ()), n
            assert h["held"] in config.metrics_for_project(P[n]), n


# ============================================================================================== Q5c: buying pressure
def test_buying_pressure_is_every_market_buyback_annualised_over_tradeable_float():
    hl = P["Hyperliquid"]
    col = bw._buying_pressure_col(FakeRefs({}))
    assert col[0] == bw.BUYING_PRESSURE_HEAD and col[2] == bw.FMT_PCT
    vals = {("actual_buyback_tokens", "q0"): 2_000_000.0, **days("actual_buyback_tokens"),
            ("circulating_supply_used", "now"): 330_000_000.0, ("locked_tokens", "now"): 400_000_000.0}
    for m in config.locked_excluded_metrics("Hyperliquid"):
        vals[(m, "now")] = 300_000_000.0
    R = FakeRefs(vals)
    f = bw._buying_pressure_col(R)[1](5, hl)
    v = evaluate(f, vals, R)
    flt = evaluate("=" + bw._cp_float(R, 5, hl), vals, R)
    assert isinstance(flt, float) and v == pytest.approx(2_000_000.0 * 365 / 90 / flt, rel=1e-12)
    # Maple: the whole buyback (all of it held) counts, and the float leaves the Fund out; its monthly buyback is
    # annualised by events (12 a year)
    mv = {("actual_buyback_tokens", "q0"): 4_029_134.43, ("actual_buyback_tokens", "q0_events"): 3.0,
          **days("actual_buyback_tokens", 92.0), ("circulating_supply_used", "now"): 1_176_793_290.0,
          ("ssf_holdings_tokens", "now"): 78_035_331.0, ("maple_ssf_safe_tokens", "now"): 48_624_987.59}
    R = FakeRefs(mv)
    v = evaluate(bw._buying_pressure_col(R)[1](5, P["Maple"]), mv, R)
    assert config.events_per_year("Maple", "actual_buyback_tokens") == 12.0
    flt = 1_176_793_290.0 - (78_035_331.0 - 48_624_987.59)                  # PR3.3: the Fund's Safe out once
    assert v == pytest.approx(4_029_134.43 / 3 * 12 / flt, rel=1e-12)
    # no buyback mechanism (Morpho: a declared, checked absence) is a measured 0; no buyback measured is a code
    assert bw._buying_pressure_col(FakeRefs({}))[1](5, P["Morpho"]) == "=0"
    assert bw._buying_pressure_col(FakeRefs({}))[1](5, P["Ethereum"]).startswith("n/a — no market buyback")


# ============================================================================================== the fixture build
# P5-1.13 (Jake 2026-11-04, approved): Hyperliquid 0.03945259923265727 -> 0.038137512591568695 — its holder value's
# rederived 3-day Assistance Fund row (09-21) now counts 3 covered days, Q0 87 -> 90 days (a correction, SPEC P5-1.13)
# PR1.S1 (Jake 2026-11-05): a daily flow is annualised over the days its rows cover — Sky, Chainlink and Near read their
# holder value over 89 days (was x365/90 over 89 values: 0.02383699954852378, 0.005930254280097274, 0.002255183232935747);
# Pendle's holder value (its epoch distributions, read to 2026-09-21) over its 68 covered days, no longer x365/90 for the
# project's programme (was 0.00842358798583249)
# PR3B.3 (Jake 2026-11-11): Maple's holder value reads the buyback's own 3 published months (one flow, one window with
# the A3 actual buyback: 658,866 on the dumps), was the trailing 90 days of month-end rows (283,866): 0.0040527229897458356
BEFORE_HVY = {"Maple": 0.009202065513863445, "Sky": 0.02410483100412517, "Hyperliquid": 0.038137512591568695,
              "Pendle": 0.01114886645183712, "Chainlink": 0.005996886350660165, "Near": 0.0022805223703844632}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The shared plain build of the 15 real dumps (tests/shared_build.py)."""
    rec = plain_build(tmp_path_factory, "p4_q5")
    assert "error" not in rec["stats"], rec["stats"]
    assert rec["stats"].get("blank_unexplained") == 0, rec["stats"].get("blank_cells")
    a, r = openpyxl.load_workbook(rec["audit"]), openpyxl.load_workbook(rec["reader"])
    return {"dir": rec["dir"], "audit": a, "reader": r, "XA": xlcalc.Workbook(a), "XR": xlcalc.Workbook(r)}


def _rows(ws, first=5) -> dict:
    return {ws.cell(r, 1).value: r for r in range(first, ws.max_row + 1) if ws.cell(r, 1).value}


def _col(ws, pred) -> int:
    return next(c for c in range(1, ws.max_column + 1) if pred(str(ws.cell(4, c).value or "")))


def _data(built, key: str, field: str):
    ws = built["audit"]["Data"]
    hdr = {str(ws.cell(1, c).value): c for c in range(1, ws.max_column + 1)}
    c = next(c for h, c in hdr.items() if h.startswith(field))
    r = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, 1).value == key)
    return built["XA"].value("Data", r, c)


def test_fixture_keys_and_ranks(built):
    """Fixture as of 2026-10-13: Sky's key = burned (13.13M over 90 days) ÷ its emission rate in force; Maple, Fluid,
    Pendle and Ether.fi 0 (nothing burned); Hyperliquid BLOCKED (its emissions cover 15 of 90 days on the fixture) —
    unknown, never 0. Summary and the A3 tab rank Sky first, then the zeros by net holder yield, Hyperliquid unranked."""
    a, XA = built["audit"], built["XA"]
    ws = a[A3]
    kc, rows = _col(ws, lambda h: h == bw.A3_KEY_HEAD), _rows(ws)
    val = {n: XA.value(A3, rows[n], kc) for n in rows if n in P}
    q0 = "Q0 ("
    burned = _data(built, "Sky|buyback_burned_tokens", q0)
    rate = _data(built, "Sky|emissions_rate_current_annual_tokens", "Now")
    cov = _data(built, "Sky|buyback_burned_tokens", "Q0: days of the 90")
    assert val["Sky"] == pytest.approx((burned / cov) / (rate / 365.0), rel=1e-9)
    assert round(val["Sky"], 2) == 0.13
    for n in ("Maple", "Fluid", "Ether.fi"):
        assert val[n] == 0.0, (n, val[n])
    # PR1.S1: Pendle's burned buyback (its epoch distributions, read to 2026-09-21) covers 68 days against emissions over
    # 90 — the two sides of the ratio cover different days, so it is BLOCKED (rule 9), never a 0 over unequal spans
    assert isinstance(val["Pendle"], str) and val["Pendle"].startswith("BLOCKED — unequal coverage")
    assert "buyback_burned_tokens, q0) covers 68 days" in val["Pendle"]
    assert isinstance(val["Hyperliquid"], str) and val["Hyperliquid"].startswith("BLOCKED — unequal coverage")
    # PR1.S1 (Jake 2026-11-05): each leg a rate over the days its rows cover (the days after a leg's last row are not
    # covered); the ratio of the 90-day sums was 0.6250996672617004
    def rate(m):
        return _data(built, f"GEODNET|{m}", q0) / _data(built, f"GEODNET|{m}", "Q0: days of the 90")
    assert val["GEODNET"] == pytest.approx(
        rate("buyback_burned_tokens") / (rate("emissions_tokens") + rate("unlock_release_tokens")), rel=1e-9)
    # the key cell says what it divides (a note per project)
    assert "BURNED buyback = buyback_burned_tokens" in ws.cell(rows["Maple"], kc).comment.text
    # ranks: Summary and the A3 tab agree; Sky first; the zeros ordered by net holder yield (numbers first, high to low)
    s, t, XR = built["reader"]["Summary"], built["reader"][reader.A3_TAB], built["XR"]
    srows, sh = _rows(s), {str(s.cell(4, c).value): c for c in range(1, s.max_column + 1)}
    sr, sk, sn = sh["Rank in archetype"], sh["Archetype key"], sh["Net holder yield"]
    a3 = [n for n in srows if n in P and reader._primary(P[n]) == 3 and not reader.parked_reason(n)]
    rank = {n: XR.value("Summary", srows[n], sr) for n in a3}
    assert rank["Sky"] == 1.0
    zeros = sorted((n for n in a3 if XR.value("Summary", srows[n], sk) == 0.0), key=lambda n: rank[n])
    assert set(zeros) >= {"Maple", "Fluid", "Ether.fi"}
    assert [rank[n] for n in zeros] == [2.0 + i for i in range(len(zeros))]
    nhy = [XR.value("Summary", srows[n], sn) for n in zeros]
    nums = [v for v in nhy if isinstance(v, float)]
    assert nums == sorted(nums, reverse=True) and nhy[:len(nums)] == nums
    assert rank["Hyperliquid"] == "unranked" and rank["Pendle"] == "unranked"      # PR1.S1: Pendle's key BLOCKED
    trows, tr = _rows(t), _col(t, lambda h: h == "Rank in A3")
    for n in a3:
        assert XR.value(t.title, trows[n], tr) == rank[n], n


def test_fixture_key_equals_its_trajectory_latest_quarter(built):
    """The key's own Trajectory row (a3_burned_vs_new_supply, the same builder shifted) reads the key cell in Q0 — on the
    audit file and on the reader's A3 tab (its 'latest' column)."""
    a, XA = built["audit"], built["XA"]
    ws, tj = a[A3], a["Trajectory"]
    kc, rows, q0 = _col(ws, lambda h: h == bw.A3_KEY_HEAD), _rows(ws), trajectory.COL["Q0"]
    seen = 0
    for name in ("Sky", "Maple", "Pendle", "Fluid", "Ether.fi", "Hyperliquid", "GEODNET"):
        row = next(r for r in range(5, tj.max_row + 1) if tj.cell(r, 2).value == f"A3|{name}|{bw.A3_KEY_TRAJECTORY_ID}")
        k = XA.value(A3, rows[name], kc)
        tv = XA.value("Trajectory", row, q0)
        if isinstance(k, float):
            assert tv == pytest.approx(k, rel=1e-12), name
            seen += 1
    assert seen >= 5
    t, XR = built["reader"][reader.A3_TAB], built["XR"]
    trows = _rows(t)
    kc2 = _col(t, lambda h: h == reader.ARCHETYPE_KEYS[3][0])
    lc = _col(t, lambda h: h == "Burned ÷ (emis + unlocks), latest")
    for name in ("Sky", "Maple", "Pendle"):
        assert XR.value(t.title, trows[name], lc) == XR.value(t.title, trows[name], kc2), name


def test_fixture_float_buying_pressure_and_holder_value_unchanged(built):
    a, XA = built["audit"], built["XA"]
    cp, ws = a[CP], a[A3]
    crow, fc = _rows(cp), _col(cp, lambda h: h == bw.CP_FLOAT_HEAD)
    cc = _col(cp, lambda h: h.startswith("Circulating supply (tokens)"))
    lc = _col(cp, lambda h: h.startswith("Staked / locked inside circulating"))
    for n, held in (("Near", "buyback_fund_balance"),):
        circ, lock = XA.value(CP, crow[n], cc), XA.value(CP, crow[n], lc)
        h = _data(built, f"{n}|{held}", "Now")
        assert XA.value(CP, crow[n], fc) == pytest.approx(circ - lock - h, rel=1e-12), n
        assert "out of the tradeable float while held" in cp.cell(crow[n], fc).comment.text.lower()
    # PR3.3 (Jake 2026-11-11): Maple's float needs the Fund's Safe on the day (maple_ssf_safe_tokens, new — the dumps
    # predate it): n/a naming it, never the old figure that took the Safe out twice
    assert "maple_ssf_safe_tokens" in str(XA.value(CP, crow["Maple"], fc))
    assert "less maple_ssf_safe_tokens" in cp.cell(crow["Maple"], fc).comment.text
    assert "already out of circulating" in cp.cell(crow["Sky"], fc).comment.text
    # buying pressure, same figure on A3 and CP (one builder): every buyback annualised ÷ the float
    pa, pc = _col(ws, lambda h: h == bw.BUYING_PRESSURE_HEAD), _col(cp, lambda h: h == bw.BUYING_PRESSURE_HEAD)
    arows = _rows(ws)
    for n in ("Sky", "Hyperliquid", "Pendle", "GEODNET"):        # Maple: its float waits on the Safe's series (PR3.3)
        v = XA.value(A3, arows[n], pa)
        assert isinstance(v, float) and v == pytest.approx(XA.value(CP, crow[n], pc), rel=1e-12), n
    tot, cov = _data(built, "Sky|actual_buyback_tokens", "Q0 ("), _data(built, "Sky|actual_buyback_tokens", "Q0: days")
    sky = XA.value(A3, arows["Sky"], pa)
    assert sky == pytest.approx(tot * 365 / 90 / XA.value(CP, crow["Sky"], fc), rel=1e-9) and cov == 90
    assert XA.value(A3, arows["Fluid"], pa) == 0.0                         # halted: no buyback in the window
    # holder value yield: unchanged (the formula reads holder_value_revenue_usd; the fixture values as before Q5)
    hc = _col(cp, lambda h: h == bw.CP_HVY_HEAD)
    assert "holder_value_revenue_usd" in str(cp.cell(crow["Maple"], hc).value)
    for n, v in BEFORE_HVY.items():
        assert XA.value(CP, crow[n], hc) == pytest.approx(v, rel=1e-9), n


def test_fixture_reader_columns_definitions_and_map(built):
    r, XR = built["reader"], built["XR"]
    for tab in (reader.A3_TAB, reader.COMPARISON):
        ws = r[tab]
        c = _col(ws, lambda h: h == reader.BUYING_PRESSURE_SHORT)
        rows = _rows(ws)
        assert isinstance(XR.value(tab, rows["Sky"], c), float), tab
    defs = {(row[0].value, row[2].value) for row in r["Definitions"].iter_rows(min_row=4) if row[0].value}
    assert (reader.A3_TAB, reader.BUYING_PRESSURE_SHORT) in defs and (reader.COMPARISON, reader.BUYING_PRESSURE_SHORT) in defs
    assert (reader.A3_TAB, reader.ARCHETYPE_KEYS[3][0]) in defs
    rows = list(csv.DictReader(open(built["dir"] / "reader_column_map.csv", encoding="utf-8")))
    key = [x for x in rows if x["audit_header"] == bw.A3_KEY_HEAD]
    assert key and key[0]["reader_header"] == reader.ARCHETYPE_KEYS[3][0]
    bp = {(x["reader_tab"], x["audit_tab"]) for x in rows if x["reader_header"] == reader.BUYING_PRESSURE_SHORT}
    assert {(reader.A3_TAB, A3), (reader.COMPARISON, CP)} <= bp


# ===================================================================================== P4-Q5 fix: the key's own concept
def test_the_a3_key_column_maps_to_its_own_concept_with_every_input_it_reads():
    """P4-Q5 fix (Jake 2026-11-03, after the full gate): the A3 key column maps to the SPEC concept a3_key — not to
    retirement (retired ÷ supply) — and a3_key declares every series the key reads for EVERY A3 project
    (build_workbook.a3_key_legs), so the Coverage audit's MATCHES-SPEC holds for each, not only Aerodrome. The check
    itself is unchanged, and retirement keeps its own inputs."""
    import coverage_audit as ca
    import spec
    cs = spec.concepts()
    assert spec.concept_for("A3 Revenue Buyback", bw.A3_KEY_HEAD) == "a3_key"
    assert spec.concept_for("A3", bw.A3_KEY_HEAD) == "a3_key"
    key = cs["a3_key"]
    assert key["axis"] == "CAPTURE" and key["dir"] == 1 and key["unit"] == "x" and key["tabs"] == ("A3",)
    ins = set(key["inputs"])
    a3 = [p for p in config.PROJECTS if 3 in (p.get("archetypes") or [])]
    assert len(a3) >= 10
    for p in a3:
        lg = bw.a3_key_legs(p["name"])
        reads = {lg["burn"], *lg["emissions"], *lg["unlocks"]} | ({lg["rate"]} if lg["rate"] else set())
        assert reads <= ins, (p["name"], sorted(reads - ins))
        assert ca._matches(reads, ins), p["name"]
    # the old concept is not loosened: it still declares only the retired-buyback inputs
    assert "buyback_burned_tokens" not in cs["retirement"]["inputs"]
    assert "retired_buyback_tokens" in cs["retirement"]["inputs"]


def test_the_held_buyback_column_maps_to_float_and_reads_only_listed_inputs():
    """P4-Q5 fix 2 (Jake 2026-11-03, after the own checks): the column "Held buyback inside circulating" maps to the SPEC
    concept float (no separate held-buyback concept), and float declares the held stock it reads — Maple's
    ssf_holdings_tokens and Near's buyback_fund_balance — so the Coverage audit's MATCHES-SPEC holds. Where the held stock
    is already outside circulating (Sky, Chainlink, Fluid) the float does not read it, so it is not subtracted twice."""
    import coverage_audit as ca
    import spec
    assert spec.concept_for(CP, bw.HELD_BUYBACK_HEAD) == "float"
    assert spec.concept_for("*", bw.HELD_BUYBACK_HEAD) == "float"
    flt = spec.concepts()["float"]
    ins = set(flt["inputs"])
    # the original inputs stay; the held stocks are added, nothing else
    assert ins == {"circulating_supply_used", "locked_tokens", "staked_noncirculating_tokens",
                   "ssf_holdings_tokens", "buyback_fund_balance", "buyback_held_tokens"}
    assert "held" in flt["definition"] and "held_buyback_inside" in flt["formula"]
    for n, held in (("Maple", "ssf_holdings_tokens"), ("Near", "buyback_fund_balance")):
        assert config.held_buyback_float(n)["how"] == "subtract"
        assert config.held_buyback_float_metric(n) == held
        reads = {held}                                   # the column's metric_fn: held_buyback_float_metric(n)
        assert reads <= ins and ca._matches(reads, ins), n
        R = FakeRefs({})
        bw._cp_float(R, 5, P[n])
        assert held in {m for m, _c in R.cells}, n      # the float subtracts the held stock once
    for n in ("Sky", "Chainlink", "Fluid"):
        h = config.held_buyback_float(n)
        assert h["how"] == "outside" and config.held_buyback_float_metric(n) is None, n
        assert h["leg"] in (config.CIRCULATING_ONCHAIN[n].get("subtract") or ()), n
        R = FakeRefs({})
        bw._cp_float(R, 5, P[n])
        assert h["leg"] not in {m for m, _c in R.cells}, n   # already out of circulating: not subtracted twice
