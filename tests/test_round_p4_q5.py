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
    f = bw._a3_burned_col(R, {})[1](5, P[name])
    return evaluate(f, vals, R), f


def days(m, n=90.0):
    return {(m, "q0_covered_days"): n}


# ============================================================================================== Q5a: the key
def test_key_is_burned_over_emissions_plus_unlocks_for_every_a3_project(monkeypatch):
    """The formula reads the BURNED buyback (never the retired = burned + held series), the absorption emissions and the
    unlock releases; Sky at its emission rate in force (no unlock set declared); Ether.fi with no emissions leg (not
    emitted, by declaration); the old key's header, builder and fallback are gone."""
    # no build state: a build earlier on the same worker leaves its R5.3 lag match (Pendle's follow series read as
    # `<m>_lagm`), which the formula then reads — an order dependence; the matched case is checked below on its own
    monkeypatch.setattr(bw, "LAG_MATCH", {})
    for p in config.PROJECTS:
        if 3 not in p["archetypes"] or bw._retirement_zero(p):
            continue
        f = bw._a3_burned_col(bw.Refs(100, 10, ["2026-09"]), {})[1](5, p)
        assert '|buyback_burned_tokens"' in f and "|retired_buyback_tokens" not in f, p["name"]
        # R9.3: an unlock leg forms from the vesting stocks or as a DECLARED zero (Aerodrome: no schedule in its docs)
        assert ('|unlock_release_tokens"' in f) == config.has_unlock_leg(p["name"]), p["name"]
    hl = bw._a3_burned_col(bw.Refs(100, 10, ["2026-09"]), {})[1](5, P["Hyperliquid"])
    assert '|emissions_tokens"' in hl and '|unlock_release_tokens"' in hl
    sky = bw._a3_burned_col(bw.Refs(100, 10, ["2026-09"]), {})[1](5, P["Sky"])
    assert '|emissions_rate_current_annual_tokens"' in sky and '|emissions_tokens"' not in sky
    efi = bw._a3_burned_col(bw.Refs(100, 10, ["2026-09"]), {})[1](5, P["Ether.fi"])
    assert '|emissions_tokens"' not in efi and '|unlock_release_tokens"' in efi
    # R5.3: where the build matched Pendle's window, the key reads the lag-matched unlock release
    monkeypatch.setitem(bw.LAG_MATCH, "Pendle", {"ok": True, "cut": {"unlock_release_tokens", "emissions_tokens"}})
    pen = bw._a3_burned_col(bw.Refs(100, 10, ["2026-09"]), {})[1](5, P["Pendle"])
    assert '|unlock_release_tokens_lagm"' in pen and '|unlock_release_tokens"' not in pen
    for gone in ("RETIRE_VS_EMISSIONS_HEAD", "_retire_vs_emissions_col", "A3_KEY_FALLBACK_TEXT", "a3_key_fallback",
                 "_a3_fallback_applies", "_a3_emissions_zero_in"):
        assert not hasattr(bw, gone), gone
    assert bw.A3_BURNED_TRAJECTORY_ID == "a3_burned_vs_new_supply"   # R20: the reader's key row is the new key's
    assert "a3_retire_vs_emissions" not in config.TRAJECTORY["rows"] and bw.A3_BURNED_TRAJECTORY_ID in config.TRAJECTORY["rows"]


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
def _float_now(monkeypatch, name: str, now: dict):
    """R20: the one free float (free_float.views — where it is formed since Round 20) on one day from hand-set
    readings {series: value}; None where a component is not read. Was: the CP cell's own formula, evaluated."""
    import free_float
    d = pd.Timestamp("2026-10-13")
    groups = {(name, m): pd.DataFrame({"date": [d], "project": name, "metric": m, "value": [float(v)],
                                       "source": "test:read", "tier": 1, "fetched_at": "2026-10-13T00:00:00Z"})
              for m, v in now.items()}
    monkeypatch.setattr(bw, "scoped_projects", lambda: [P[name]])
    monkeypatch.setattr(config, "RULE_IN_PENDING", {})
    free_float.views(groups, d)
    g = groups.get((name, config.FREE_FLOAT_METRIC))
    return None if g is None or g.empty else float(g["value"].iloc[-1])


def test_tradeable_float_leaves_out_the_held_buyback_and_takes_it_back_when_it_moves_out(monkeypatch):
    import free_float
    assert config.held_buyback_float_metric("Maple") == "ssf_holdings_tokens"
    # PR3.3 (Jake 2026-11-11): the Fund's chart counts its Safe 0x58BE…, which circulating already subtracts — the
    # float subtracts the chart LESS the Safe (it took the Safe out twice: 1,176,793,290 - 78,035,331)
    # R9.2 (the rule): the DAO multisig and the registry wallets are IN circulating (1,267,875,000) and leave the float
    # as the rule's stock (91,081,710 on the dumps) — the float is what it was
    vals = {"circulating_supply_used": 1_267_875_000.0, "ssf_holdings_tokens": 78_035_331.0,
            "maple_ssf_safe_tokens": 48_624_987.59, "rule_in_off_float_tokens": 91_081_710.0}
    less = (78_035_331.0 - 48_624_987.59) if config.held_buyback_less_applies("Maple") else 78_035_331.0
    base = _float_now(monkeypatch, "Maple", vals)
    assert base == pytest.approx(1_176_793_290.0 - less)
    out = {**vals, "ssf_holdings_tokens": 70_035_331.0}                      # 8M moved out of the Fund
    assert _float_now(monkeypatch, "Maple", out) - base == pytest.approx(8_000_000.0)
    gone = {k: v for k, v in vals.items() if k != "ssf_holdings_tokens"}
    assert _float_now(monkeypatch, "Maple", gone) is None                    # the held stock unknown: float unknown
    assert "ssf_holdings_tokens" in free_float.MISSING["Maple"]
    # where the held stock is already out of circulating (Chainlink's Reserve, Fluid's treasury) it is not subtracted
    # twice: the declared leg is one circulating subtracts, and the float's held component does not read it
    for n, h in config.HELD_BUYBACK_FLOAT.items():
        sh = config.buyback_destination_shares(n) or {}
        rows = config.BUYBACK_DESTINATION_SHARES.get(n) or []
        assert any(r.get("hold") for r in rows) or sh.get("hold"), n
        if h["how"] == "outside":
            assert h["leg"] in (config.CIRCULATING_ONCHAIN[n].get("subtract") or ()), n
            assert config.held_buyback_float_metric(n) is None
            assert free_float.parts(P[n])["held"]["add"] == ()
        else:
            assert h["how"] == "subtract" and h["held"] not in (config.CIRCULATING_ONCHAIN[n].get("subtract") or ()), n
            assert h["held"] in config.metrics_for_project(P[n]), n
            assert free_float.parts(P[n])["held"]["add"] == (h["held"],)


# ============================================================================================== Q5c: buying pressure
def test_buying_pressure_is_every_market_buyback_annualised_over_tradeable_float():
    """R20 (Jake 2026-11-24): buying pressure is the REVENUE-FUNDED buying that is burned or held (float_buying_tokens)
    ÷ the one free float — net float pressure's demand side. Was (P4-Q5c): every market buyback, any destination
    (actual_buyback_tokens), ÷ the tradeable float formed in the cell."""
    hl = P["Hyperliquid"]
    col = bw._buying_pressure_col(FakeRefs({}))
    assert col[0] == bw.BUYING_PRESSURE_HEAD and col[2] == bw.FMT_PCT
    flt = 330_000_000.0 - (400_000_000.0 - 300_000_000.0) - 100_000.0        # the float the series holds that day
    vals = {("float_buying_tokens", "q0"): 2_000_000.0, **days("float_buying_tokens"),
            (config.FREE_FLOAT_METRIC, "now"): flt}
    R = FakeRefs(vals)
    f = bw._buying_pressure_col(R)[1](5, hl)
    v = evaluate(f, vals, R)
    assert {m for m, _c in R.cells} == {"float_buying_tokens", config.FREE_FLOAT_METRIC}    # nothing else is read
    assert v == pytest.approx(2_000_000.0 * 365 / 90 / flt, rel=1e-12)
    # the float not formed that day: no buying pressure, never the buying over circulating
    gone = {k: x for k, x in vals.items() if k[0] != config.FREE_FLOAT_METRIC}
    assert not isinstance(evaluate(f, gone, R), float)
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
# R6.3 (Jake 2026-11-16): Sky's market cap counts the Pause Proxy's held buyback (circulating + 20,142,225.8 on the
# dumps), so its holder value yield is over a larger cap (was 0.02410483100412517)
# R7.C1 (Jake 2026-11-16): Hyperliquid's market cap counts the Foundation + Grants stake (released) — its holder value
# yield is over a larger cap; on the dumps that stake is held on the last day only (was 0.038137512591568695)
# R7C.A1 (Jake 2026-11-16, decision 1b): Sky's and Hyperliquid's dumps were refreshed from his store exports (more
# stored history up to the same 10-13 as-of), so their figures are re-pinned on the refreshed dumps (were 0.024084003858726048
# and 0.030133419313739723); the four other projects' dumps did not change and their pins hold to 1e-9.
# R9.2 (the rule, Jake 2026-11-17): the four frozen projects' market cap is on the larger circulating (issuer / treasury
# wallets in, off the float), so their yield is the same $ over a larger market cap — declared moves; was Maple
# 0.009202065513863445, Sky 0.02447713721663361, Hyperliquid 0.030258293116699385, Pendle 0.01114886645183712.
# Chainlink is untouched.
# R10.B2b / B3a (Jake 2026-11-18): Near's credited holder value now carries the gas burn (the chain's own burn x price)
# beside the Intents buyback, over a market cap on a circulating with lockup.near + contributors.near in — was
# 0.0022805223703844632 (the buyback alone). The same step on Jake's 11-17 export: 0.50712% -> 0.53733% = (1 + burn $ /
# buyback $) x (old ÷ new market cap) = 1.05971 x 0.99987.
# R17.A3 (Jake 2026-11-23, a declared change of quantity, config.HEADLINE_RENAMES "R17.A3"; docs/moves_r17.md): holder
# value yield divides by the window's AVERAGE market cap (each day's $ over each day's value), was today's market cap —
# was Maple 0.008541006768956963, Sky 0.024434984263792533, Hyperliquid 0.01691147794146245, Pendle
# 0.009420640711118868, Chainlink 0.005996886350660165, Near 0.002418363439991883. Re-pinned in the Round 20 gate fix on
# the 15 dumps; the same six figures read on the pre-Round-20 build (1894183), so Round 20 moved none of them.
BEFORE_HVY = {"Maple": 0.010720597913026847, "Sky": 0.023648788113572274, "Hyperliquid": 0.015591883176373859,
              "Pendle": 0.01071647386102105, "Chainlink": 0.005392940384874894, "Near": 0.004504518825379258}


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
    kc, rows = _col(ws, lambda h: h == bw.A3_BURNED_HEAD), _rows(ws)
    val = {n: XA.value(A3, rows[n], kc) for n in rows if n in P}
    q0 = "Q0 ("
    burned = _data(built, "Sky|buyback_burned_tokens", q0)
    rate = _data(built, "Sky|emissions_rate_current_annual_tokens", "Now")
    cov = _data(built, "Sky|buyback_burned_tokens", "Q0: days of the 90")
    assert val["Sky"] == pytest.approx((burned / cov) / (rate / 365.0), rel=1e-9)
    assert round(val["Sky"], 2) == 0.13
    for n in ("Maple", "Fluid"):
        assert val[n] == 0.0, (n, val[n])
    # R14 (Jake 2026-11-19, config.BUYBACK_LEGS): from 2026-09-03 Ether.fi's buyback = the wallet scan + the
    # programme's purchases, which the dump never stored — the buyback covers 49 of 90 days, so the key is BLOCKED
    # (unknown is never 0). Was 0.0 before R14.
    assert isinstance(val["Ether.fi"], str) and "buyback_burned_tokens, q0) covers 49 days" in val["Ether.fi"]
    # PR1.S1: Pendle's burned buyback (its epoch distributions, read to 2026-09-21) covers 68 days against emissions over
    # 90 — the two sides of the ratio cover different days, so it is BLOCKED (rule 9), never a 0 over unequal spans
    assert isinstance(val["Pendle"], str) and val["Pendle"].startswith("BLOCKED — unequal coverage")
    assert "buyback_burned_tokens, q0) covers 68 days" in val["Pendle"]
    # Hyperliquid: BLOCKED while its legs cover unequal days; a number once they cover the same days (the dump
    # refreshed from Jake's export, R7C.A1: the burned buyback and option C's issuance both over the window)
    hl = val["Hyperliquid"]
    if isinstance(hl, str):
        assert hl.startswith("BLOCKED — unequal coverage")
    else:
        cb = _data(built, "Hyperliquid|buyback_burned_tokens", "Q0: days of the 90")
        ci = _data(built, "Hyperliquid|issuance_option_c_tokens", "Q0: days of the 90")
        assert abs(cb - ci) <= 9 and 0 < hl < 5, (cb, ci, hl)
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
    # the projects with a measured key above 0 rank first, by the key, high to low (Sky; and Hyperliquid once its key
    # is a number — the dump refreshed from Jake's export, R7C.A1); the zeros follow on consecutive ranks
    keyv = {n: XR.value("Summary", srows[n], sk) for n in a3}
    keyed = sorted((n for n in a3 if isinstance(keyv[n], float) and keyv[n] > 0), key=lambda n: -keyv[n])
    assert "Sky" in keyed and [rank[n] for n in keyed] == [1.0 + i for i in range(len(keyed))]
    zeros = sorted((n for n in a3 if keyv[n] == 0.0), key=lambda n: rank[n])
    # R20 (the A3 key is revenue to holders ÷ new supply; config.ARCHETYPE_STATUS): the zeros are the paused / off
    # programmes — Fluid, Ether.fi (paused) and Morpho (off, new on A3); Maple's key is BLOCKED on the dumps (its (b) leg
    # in $ is not formed: docs/moves_r20.md), so it is unranked (was: zeros >= {Maple, Fluid}, Ether.fi unranked)
    assert set(zeros) >= {"Fluid", "Ether.fi", "Morpho"}
    assert rank["Maple"] == "unranked" and not isinstance(keyv["Maple"], float)
    assert [rank[n] for n in zeros] == [len(keyed) + 1.0 + i for i in range(len(zeros))]
    nhy = [XR.value("Summary", srows[n], sn) for n in zeros]
    nums = [v for v in nhy if isinstance(v, float)]
    assert nums == sorted(nums, reverse=True) and nhy[:len(nums)] == nums
    assert rank["Pendle"] == "unranked"                                             # PR1.S1: Pendle's key BLOCKED
    assert (rank["Hyperliquid"] == "unranked") == (not isinstance(keyv["Hyperliquid"], float))   # ranked iff measured
    trows, tr = _rows(t), _col(t, lambda h: h == "Rank in A3")
    for n in a3:
        assert XR.value(t.title, trows[n], tr) == rank[n], n


def test_fixture_key_equals_its_trajectory_latest_quarter(built):
    """The key's own Trajectory row (a3_burned_vs_new_supply, the same builder shifted) reads the key cell in Q0 — on the
    audit file and on the reader's A3 tab (its 'latest' column)."""
    a, XA = built["audit"], built["XA"]
    ws, tj = a[A3], a["Trajectory"]
    kc, rows, q0 = _col(ws, lambda h: h == bw.A3_BURNED_HEAD), _rows(ws), trajectory.COL["Q0"]
    seen = 0
    for name in ("Sky", "Maple", "Pendle", "Fluid", "Ether.fi", "Hyperliquid", "GEODNET"):
        row = next(r for r in range(5, tj.max_row + 1) if tj.cell(r, 2).value == f"A3|{name}|{bw.A3_BURNED_TRAJECTORY_ID}")
        k = XA.value(A3, rows[name], kc)
        tv = XA.value("Trajectory", row, q0)
        if isinstance(k, float):
            assert tv == pytest.approx(k, rel=1e-12), name
            seen += 1
    assert seen >= 5
    t, XR = built["reader"][reader.A3_TAB], built["XR"]
    trows = _rows(t)
    kc2 = _col(t, lambda h: h == reader.ARCHETYPE_KEYS[3][0])
    # R20: the reader's key trajectory columns are the new key's (was "Burned ÷ (emis + unlocks), latest")
    lc = _col(t, lambda h: h == f"To holders ÷ new supply, {reader.TRAJ_LABELS[0]}")
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
        assert all(isinstance(x, float) and x > 0 for x in (circ, lock, h)), n
        # R10.B3a (Jake 2026-11-18): Near's float also loses lockup.near + contributors.near + treasury.near
        # (config.RULE_IN["Near"]: rule_in_off_float_tokens). treasury.near's balance is a new read
        # (near_treasury_tokens — the dumps predate it), so on the dumps the float is n/a naming the missing stock,
        # never the float without it (was: == circ - lock - h, 708,547,659.5 on the dump)
        assert "rule_in_off_float_tokens" in str(XA.value(CP, crow[n], fc)), n      # R20: free_float.MISSING names it
        assert "near_treasury_tokens" in config.RULE_IN[n]["off_float"]
        assert "out of the free float while held" in cp.cell(crow[n], fc).comment.text.lower()
    # PR3.3 (Jake 2026-11-11): Maple's float needs the Fund's Safe on the day (maple_ssf_safe_tokens, new — the dumps
    # predate it): n/a naming it, never the old figure that took the Safe out twice
    assert "maple_ssf_safe_tokens" in str(XA.value(CP, crow["Maple"], fc))
    assert "less maple_ssf_safe_tokens" in cp.cell(crow["Maple"], fc).comment.text
    # R6.3 (Jake 2026-11-16): Sky's Pause Proxy is a held buyback IN circulating, out of the float while held
    sky_note = cp.cell(crow["Sky"], fc).comment.text
    assert "out of the free float while held" in sky_note.lower() and "treasury_holding_tokens" in sky_note
    # buying pressure, same figure on A3 and CP (one builder): every buyback annualised ÷ the float
    pa, pc = _col(ws, lambda h: h == bw.BUYING_PRESSURE_HEAD), _col(cp, lambda h: h == bw.BUYING_PRESSURE_HEAD)
    arows = _rows(ws)
    for n in ("Sky", "Hyperliquid", "Pendle", "GEODNET"):        # Maple: its float waits on the Safe's series (PR3.3)
        # R20: the numerator is the revenue-funded buying that is burned or held (was every market buyback)
        v = XA.value(A3, arows[n], pa)
        assert isinstance(v, float) and v == pytest.approx(XA.value(CP, crow[n], pc), rel=1e-12), n
    # R20: Sky 2.368% (float_buying_tokens: the burned + held share; was 6.518% on every buyback, actual_buyback_tokens)
    tot, cov = _data(built, "Sky|float_buying_tokens", "Q0 ("), _data(built, "Sky|float_buying_tokens", "Q0: days")
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
    key = [x for x in rows if x["audit_header"] == bw.A3_KEY_HEAD]     # R20: the key column is revenue to holders ÷ new supply (was A3_BURNED_HEAD)
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
    assert spec.concept_for("A3 Revenue Buyback", bw.A3_BURNED_HEAD) == "a3_burned"
    assert spec.concept_for("A3", bw.A3_BURNED_HEAD) == "a3_burned"
    key = cs["a3_burned"]   # R20: the burned figure is an ordinary column with its own concept
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
    # R20: + the one series and its three components (the cells read those; the old inputs are what they are formed from)
    assert ins == {"circulating_supply_used", "locked_tokens", "staked_noncirculating_tokens",
                   "ssf_holdings_tokens", "buyback_fund_balance", "buyback_held_tokens", "treasury_holding_tokens",
                   "free_float_tokens", "free_float_staked_locked_tokens", "free_float_issuer_treasury_tokens",
                   "free_float_held_buybacks_tokens"}
    # R20: the float's formula names its stored held-buybacks component (was "held_buyback_inside")
    assert "held" in flt["definition"] and "free_float_held_buybacks_tokens" in flt["formula"]
    for n, held in (("Maple", "ssf_holdings_tokens"), ("Near", "buyback_fund_balance"),
                    ("Sky", "treasury_holding_tokens")):                # R6.3: Sky's held buyback is inside now
        assert config.held_buyback_float(n)["how"] == "subtract"
        assert config.held_buyback_float_metric(n) == held
        reads = {held}                                   # the held stock the float's held component is formed from
        assert reads <= ins and ca._matches(reads, ins), n
        import free_float                                # R20: the float is formed in free_float.views, not in the cell
        assert free_float.parts(P[n])["held"]["add"] == (held,), n      # the float subtracts the held stock once
    for n in ("Chainlink", "Fluid"):
        h = config.held_buyback_float(n)
        assert h["how"] == "outside" and config.held_buyback_float_metric(n) is None, n
        assert h["leg"] in (config.CIRCULATING_ONCHAIN[n].get("subtract") or ()), n
        import free_float
        assert h["leg"] not in free_float.parts(P[n])["held"]["add"], n   # not read as a held component a second time
