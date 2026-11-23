"""ROUND 20 — FLOAT (Jake 2026-11-24, Part 2 "free float").

1. ONE free float per project per day, formed in one place (free_float.views): circulating − staked / locked /
   earning − issuer, foundation and treasury holdings − held buybacks. Every tab reads that series; the build fails
   here if any tab shows a float different from it, or names another float.
2. The daily float movements: yesterday's float + movements = today's float, a residual beyond 0.5% flagged with its
   cause; weekly and quarterly figures are sums of the days.
3. New supply counts when created, the staking is its own movement.   4. Held buybacks: off when bought, back as a
   named line when the held stock falls; a payout that stays staked stays off.
5. The two nets, each with one base on both sides.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import free_float  # noqa: E402

FF = config.FREE_FLOAT_METRIC


# ====================================================================================== 1. one place, statically
def test_the_float_series_is_written_in_one_function_and_nowhere_else():
    """No source file but free_float.py stores free_float_tokens (or a component) into the build's series."""
    names = (FF, *(m for m, _l in config.FREE_FLOAT_COMPONENTS))
    for path in ROOT.glob("*.py"):
        if path.name in ("free_float.py", "config.py", "metrics_dictionary.py"):
            continue
        src = path.read_text(encoding="utf-8")
        for n in names:
            assert f'"metric": "{n}"' not in src and f"groups[(name, \"{n}\")]" not in src, (path.name, n)
    src = (ROOT / "free_float.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    writers = [f.name for f in tree.body if isinstance(f, ast.FunctionDef)
               and "store(FF" in (ast.get_source_segment(src, f) or "")]
    assert writers == ["views"]


def test_every_float_cell_builder_reads_the_series_and_forms_nothing():
    """The builders the tabs call for a float (_cp_float: CP and buying pressure; _ff_expr: the dilution cells on A2 / A3 /
    A4; _free_float: A1, A2, A3) return the one Data reference — no circulating, lock, held or issuer term."""
    class R:
        @staticmethod
        def D(r, metric, col="q0"):
            return f"<{metric}|{col}>"
    p = config.PROJECT_BY_NAME["Maple"]
    want = f"IF(ISNUMBER(<{FF}|now>),<{FF}|now>,{bw.NA})"
    assert bw._free_float(R, 7, p) == bw._cp_float(R, 7, p) == bw._ff_expr(R, 7, p) == want
    for fn in (bw._free_float, bw._cp_float, bw._ff_expr):
        body = inspect.getsource(fn)
        for block in ("_circ(", "_ff_lock(", "_cp_lock(", "_held_inside(", "_released_held(", "_rule_in_term("):
            assert block not in body, (fn.__name__, block)


# The pieces the old floats were assembled from, and the only functions that may still use them — none is a float:
# the lock / staking RATE, the insider share, the whole-token yield, CoinGecko's fallback for circulating. A new user
# of one of these has to be shown not to be a float before it is added here.
OLD_FLOAT_BLOCKS = ("_ff_lock(", "_cp_lock(", "_stake_inside(", "_held_inside(", "_released_held(", "_lock_inside(")
NOT_A_FLOAT = {"_lock_inside": "the lock rate's numerator", "_circ_formula": "CoinGecko's fallback for circulating",
               "_cp_lock": "the stake inside circulating (staking rate)", "_rule_addback_term": "the insider share",
               "_released_held_note": "a cell comment", "_released_held_view": "the released-held series itself",
               "_cp_cols": "whole-token yield and insider share", "write_a3": "the lock rate",
               "write_a1": "the staking rate", "_ff_lock": "its own definition", "_stake_inside": "its own definition",
               "_held_inside": "its own definition", "_released_held": "its own definition"}


def test_no_other_function_assembles_a_float_from_the_old_pieces():
    src = (ROOT / "build_workbook.py").read_text(encoding="utf-8")
    lines = src.split("\n")
    users = {f.name for f in ast.parse(src).body if isinstance(f, ast.FunctionDef)
             and any(b in "\n".join(lines[f.lineno - 1:f.end_lineno]) for b in OLD_FLOAT_BLOCKS)}
    assert users <= set(NOT_A_FLOAT), sorted(users - set(NOT_A_FLOAT))
    # and the headers of the deleted floats are gone from the builders and the reader
    import re
    for name in ("build_workbook.py", "reader.py"):
        text = (ROOT / name).read_text(encoding="utf-8")
        heads = re.findall(r'\(\s*"([^"\n]{4,160})",\s*(?:lambda|cell\(|calc\()', text)
        bad = [h for h in heads if re.search(free_float.BANNED_FLOAT_NAMES, h, re.I)]
        assert not bad, (name, bad)
    assert "Float after staking" not in (ROOT / "reader.py").read_text(encoding="utf-8")


# ====================================================================================== the series, on hand-built rows
def _frame(name, metric, pairs, source="test:read"):
    return pd.DataFrame({"date": [pd.Timestamp(d) for d, _v in pairs], "project": name, "metric": metric,
                         "value": [float(v) for _d, v in pairs], "source": source, "tier": 1,
                         "fetched_at": "2026-10-13T00:00:00Z"})


def _days(start, values):
    d0 = pd.Timestamp(start)
    return [(d0 + pd.Timedelta(days=i), v) for i, v in enumerate(values) if v is not None]


@pytest.fixture
def one_project(monkeypatch):
    def run(name, series: dict, asof):
        groups = {(name, m): _frame(name, m, pairs) for m, pairs in series.items()}
        monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[name]])
        monkeypatch.setattr(config, "RULE_IN_PENDING", {})
        free_float.views(groups, pd.Timestamp(asof))
        return groups
    return run


def test_sky_free_float_is_circulating_less_each_component_once(one_project):
    """Sky reads all three components: staked SKY (locked_tokens), the issuer stock (the released-held series + the
    rule's sum) and the held buyback at the Pause Proxy (treasury_holding_tokens)."""
    sp = free_float.parts(config.PROJECT_BY_NAME["Sky"])
    assert sp["lock"]["add"] == ("locked_tokens",) and sp["held"]["add"] == ("treasury_holding_tokens",)
    assert sp["issuer"] == ("sky_released_held_tokens", "rule_in_off_float_tokens")
    g = one_project("Sky", {
        "circulating_supply_used": _days("2026-10-01", [1000, 1000, 1000, 1000, 1000]),
        "locked_tokens": _days("2026-10-01", [100, 100, 120, 120, 130]),
        "sky_released_held_tokens": _days("2026-10-01", [50, 50, 50, 50, 50]),
        "rule_in_off_float_tokens": _days("2026-10-01", [30, 30, 30, 20, 20]),
        "treasury_holding_tokens": _days("2026-10-01", [10, 15, 15, 15, 5])}, "2026-10-05")
    ff = bw._daily(g[("Sky", FF)])
    assert list(ff.values) == [810.0, 805.0, 785.0, 795.0, 795.0]
    lock, issuer, held = (bw._daily(g[("Sky", m)]) for m, _l in config.FREE_FLOAT_COMPONENTS)
    assert list(issuer.values) == [80.0, 80.0, 80.0, 70.0, 70.0] and list(held.values) == [10.0, 15.0, 15.0, 15.0, 5.0]
    circ = bw._daily(g[("Sky", "circulating_supply_used")])
    assert ((circ - lock - issuer - held) == ff).all()          # the components ARE what the float subtracts
    assert "Sky" in free_float.FORMED and "Sky" not in free_float.MISSING


def test_a_day_a_component_is_not_read_holds_no_value_and_the_component_is_named(one_project):
    """Unknown is never 0: with the held stock not stored at all there is no float (never circulating − the rest),
    and the reason names the series; a component that stops being read ends the series at its staleness limit."""
    base = {"circulating_supply_used": _days("2026-09-01", [1000] * 30),
            "locked_tokens": _days("2026-09-01", [100] * 30),
            "sky_released_held_tokens": _days("2026-09-01", [50] * 30),
            "rule_in_off_float_tokens": _days("2026-09-01", [30] * 30)}
    g = one_project("Sky", base, "2026-09-30")
    assert ("Sky", FF) not in g and "Sky" not in free_float.FORMED
    assert "treasury_holding_tokens (held buybacks): no rows stored" in free_float.MISSING["Sky"]
    assert bw._free_float_missing(config.PROJECT_BY_NAME["Sky"]).startswith("n/a — free float: a component is not read")
    # the held stock read on the first two days only: carried to its staleness limit, then no value
    g = one_project("Sky", {**base, "treasury_holding_tokens": _days("2026-09-01", [10, 10])}, "2026-09-30")
    lim = bw._stale_limit("Sky", "treasury_holding_tokens", bw.GLOBALS["stale_after_days"],
                          g[("Sky", "treasury_holding_tokens")])
    ff = bw._daily(g[("Sky", FF)])
    assert ff.index.max() == pd.Timestamp("2026-09-02") + pd.Timedelta(days=lim) and (ff == 810.0).all()
    assert "treasury_holding_tokens (held buybacks): last read 2026-09-02" in free_float.MISSING["Sky"]
    # the other components are still stored on the days they are read
    assert bw._daily(g[("Sky", free_float.C_LOCK)]).index.max() == pd.Timestamp("2026-09-30")


def test_a_stake_circulating_already_leaves_out_comes_off_once(one_project):
    """Hyperliquid: the issuer-side stake (team_wallet_tokens) is inside locked_tokens and already out of the lock the
    float subtracts — max(0, locked − it) — while the rule's issuer stock carries the issuer side."""
    sp = free_float.parts(config.PROJECT_BY_NAME["Hyperliquid"])
    assert sp["lock"]["add"] == ("locked_tokens",) and sp["lock"]["less"] == tuple(
        config.locked_excluded_metrics("Hyperliquid"))
    less = {m: _days("2026-10-01", [40, 40, 40]) for m in sp["lock"]["less"]}
    g = one_project("Hyperliquid", {
        "circulating_supply_used": _days("2026-10-01", [1000, 1000, 1000]),
        "locked_tokens": _days("2026-10-01", [400, 400, 400]),
        "rule_in_off_float_tokens": _days("2026-10-01", [100, 100, 100]), **less}, "2026-10-03")
    assert list(bw._daily(g[("Hyperliquid", free_float.C_LOCK)]).values) == [400.0 - 40.0 * len(less)] * 3
    assert list(bw._daily(g[("Hyperliquid", FF)]).values) == [1000.0 - (400.0 - 40.0 * len(less)) - 100.0] * 3


def test_chainlinks_reserve_is_shown_under_held_buybacks_and_taken_off_once(one_project):
    """The Reserve is one of the rule's issuer legs: the float takes the rule's sum off once; the Reserve's part is
    shown as the held-buybacks component and leaves the issuer component."""
    assert "buyback_fund_balance" in config.RULE_IN["Chainlink"]["off_float"]
    g = one_project("Chainlink", {
        "circulating_supply_used": _days("2026-10-01", [1000, 1000]), "locked_tokens": _days("2026-10-01", [40, 40]),
        "rule_in_off_float_tokens": _days("2026-10-01", [240, 240]),
        "buyback_fund_balance": _days("2026-10-01", [6, 7])}, "2026-10-02")
    assert list(bw._daily(g[("Chainlink", FF)]).values) == [720.0, 720.0]
    assert list(bw._daily(g[("Chainlink", free_float.C_HELD)]).values) == [6.0, 7.0]
    assert list(bw._daily(g[("Chainlink", free_float.C_ISSUER)]).values) == [234.0, 233.0]


# ====================================================================================== 2-4. the daily movements
def test_the_daily_identity_holds_and_a_payout_that_stays_staked_stays_off(one_project):
    """Rule 4: the held stock's rise comes off on the day (−5); its fall comes back as its own line (+10); the same
    day's equal rise of the stake takes it off again (−10), so a buyback paid to stakers that stays staked stays off."""
    one_project("Sky", {
        "circulating_supply_used": _days("2026-10-01", [1000, 1000, 1000, 1000, 1000]),
        "locked_tokens": _days("2026-10-01", [100, 100, 120, 120, 130]),
        "sky_released_held_tokens": _days("2026-10-01", [50, 50, 50, 50, 50]),
        "rule_in_off_float_tokens": _days("2026-10-01", [30, 30, 30, 20, 20]),
        "treasury_holding_tokens": _days("2026-10-01", [10, 15, 15, 15, 5])}, "2026-10-05")
    mv = free_float.MOVES["Sky"]
    d = lambda day: mv.loc[pd.Timestamp(day)]  # noqa: E731
    assert d("2026-10-02")["held_bought"] == -5.0 and d("2026-10-02")["close"] == 805.0
    assert d("2026-10-03")["staked"] == -20.0
    assert d("2026-10-04")["issuer"] == 10.0                       # the issuer stock fell by 10: into the float
    last = d("2026-10-05")
    assert last["held_returned"] == 10.0 and last["staked"] == -10.0 and last["close"] == last["open"] == 795.0
    # every day: yesterday's float + the lines = today's float (nothing unexplained here)
    assert ((mv["open"] + mv[list(free_float.LINE_KEYS)].sum(axis=1) - mv["close"]).abs() < 1e-9).all()
    assert (mv["residual"].abs() < 1e-9).all() and not mv["flag"].any()


def test_new_supply_counts_when_created_and_the_staking_is_its_own_movement(one_project):
    """Rule 3 (Hyperliquid: every staking reward enters circulating): a reward that goes straight into the stake is
    + emissions AND − newly staked on its day — two lines, not a float that never moved for no stated reason."""
    name = "Hyperliquid"
    less = {m: _days("2026-10-01", [0, 0, 0]) for m in config.locked_excluded_metrics(name)}
    one_project(name, {
        "circulating_supply_used": _days("2026-10-01", [1000, 1005, 1005]),
        "locked_tokens": _days("2026-10-01", [400, 405, 405]),
        "rule_in_off_float_tokens": _days("2026-10-01", [100, 100, 100]),
        "emissions_tokens": _days("2026-10-01", [0, 5, 0]), **less}, "2026-10-03")
    day = free_float.MOVES[name].loc[pd.Timestamp("2026-10-02")]
    assert day["emissions"] == 5.0 and day["staked"] == -5.0 and day["close"] == day["open"] == 500.0
    assert abs(day["residual"]) < 1e-9 and not day["flag"]


def test_a_residual_over_half_a_percent_is_flagged_with_its_cause(one_project):
    """Circulating jumps by 10 (2% of the float) with no measured emission, unlock or burn: the day is flagged and the
    cause says so; a 0.4% move is inside the limit."""
    name = "Hyperliquid"
    less = {m: _days("2026-10-01", [0] * 4) for m in config.locked_excluded_metrics(name)}
    one_project(name, {
        "circulating_supply_used": _days("2026-10-01", [1000, 1010, 1012, 1012]),
        "locked_tokens": _days("2026-10-01", [400] * 4), "rule_in_off_float_tokens": _days("2026-10-01", [100] * 4),
        **less}, "2026-10-04")
    mv = free_float.MOVES[name]
    a, b = mv.loc[pd.Timestamp("2026-10-02")], mv.loc[pd.Timestamp("2026-10-03")]
    assert a["residual"] == 10.0 and a["pct"] == pytest.approx(10 / 500) and bool(a["flag"])
    assert "with no measured emission, unlock or burn" in a["cause"]
    assert b["residual"] == 2.0 and b["pct"] == pytest.approx(2 / 510) and not b["flag"] and b["cause"] == ""
    assert config.FLOAT_RESIDUAL_LIMIT == 0.005                    # the bound is ±0.5%, as the circulating statement's


def test_a_day_after_a_gap_carries_the_gap_and_names_it(one_project):
    """The float not formed for some days (a component beyond its limit), then formed again: that day's row spans the
    gap, so the days still add up; if it is flagged the gap is in the cause."""
    name = "Hyperliquid"
    n = 400                       # far beyond any staleness limit (a sparse series' observed cadence raises its limit)
    rule = [100] * 2 + [None] * (n - 3) + [100]
    less = {m: _days("2026-09-01", [0] * n) for m in config.locked_excluded_metrics(name)}
    one_project(name, {
        "circulating_supply_used": _days("2026-09-01", [1000] * (n - 1) + [1100]),
        "locked_tokens": _days("2026-09-01", [400] * n), "rule_in_off_float_tokens": _days("2026-09-01", rule),
        **less}, pd.Timestamp("2026-09-01") + pd.Timedelta(days=n - 1))
    mv = free_float.MOVES[name]
    last = mv.iloc[-1]
    assert last["gap_days"] >= 1 and bool(last["flag"]) and "the float was not formed on the" in last["cause"]
    assert last["open"] + last[list(free_float.LINE_KEYS)].sum() + last["residual"] == last["close"] == 600.0


def test_quarterly_and_weekly_figures_are_sums_of_the_days(one_project, monkeypatch):
    name = "Sky"
    n = 200
    lock = [100 + (i % 7) for i in range(n)]
    held = [10 + i // 10 for i in range(n)]
    g = one_project(name, {
        "circulating_supply_used": _days("2026-03-01", [5000 + i for i in range(n)]),
        "locked_tokens": _days("2026-03-01", lock), "sky_released_held_tokens": _days("2026-03-01", [50] * n),
        "rule_in_off_float_tokens": _days("2026-03-01", [30] * n),
        "treasury_holding_tokens": _days("2026-03-01", held)}, pd.Timestamp("2026-03-01") + pd.Timedelta(days=n - 1))
    asof = pd.Timestamp("2026-03-01") + pd.Timedelta(days=n - 1)
    mv = free_float.MOVES[name]
    rows = {r["Line"]: r for r in free_float.statements(g, asof) if r["Project"] == name}
    start, end = bw._flow_window(asof, 0)
    w = mv[(mv.index > start) & (mv.index <= end)]
    for key, label in free_float.LINES:
        assert rows[label]["Q0"] == pytest.approx(float(w[key].sum()))
    assert rows["Opening free float"]["Q0"] + sum(rows[lab]["Q0"] for _k, lab in free_float.LINES) \
        == pytest.approx(rows["Expected closing (opening + the movements)"]["Q0"])
    assert rows["Closing free float"]["Q0"] == float(mv["close"].iloc[-1])
    assert rows["Residual (closing − expected) = the sum of the days' residuals"]["Q0"] == pytest.approx(
        float(w["residual"].sum()))
    # the weekly view: 13 rows, each the sum of its 7 days; the daily view: one row a day
    weeks = free_float.period_table(name, g, asof, 7, config.FLOAT_WEEKS)
    assert len(weeks) == 13
    i_res, i_open, i_close = (free_float.BLOCK_HEAD.index(h) for h in
                              ("Residual", "Opening free float", "Closing free float"))
    wk = mv[(mv.index > asof - pd.Timedelta(days=7)) & (mv.index <= asof)]
    assert weeks[0][i_res] == pytest.approx(float(wk["residual"].sum()))
    assert weeks[0][i_open] == float(wk["open"].iloc[0]) and weeks[0][i_close] == float(wk["close"].iloc[-1])
    assert weeks[1][i_close] == weeks[0][i_open]                   # consecutive weeks chain
    days = free_float.period_table(name, g, asof, 1, config.FLOAT_DAILY_DAYS)
    assert len(days) == 35 and days[0][2] == str(asof.date()) and days[0][i_close] == float(mv["close"].iloc[-1])


def test_the_exchange_sends_line_is_named_where_a_series_exists():
    assert config.FLOAT_EXCHANGE_SENDS == {"Ether.fi": "theatre_exchange_outflow_tokens",
                                           "Chainlink": "issuer_exchange_outflow_tokens"}
    assert "exchange-tagged" in free_float.EXCHANGE_LINE


# ====================================================================================== 5. the two nets
def test_float_buying_is_the_retired_buyback_on_revenue_funded_days_only(monkeypatch):
    """Net float pressure's demand side: revenue-funded buying that is burned or held. Aerodrome's Foundation buys with
    its own voting rewards (config.HOLDER_VALUE funding: treasury) — a measured 0; Hyperliquid's default is revenue."""
    assert config.holder_funding("Aerodrome", "2026-10-01")[0] != "revenue"
    assert config.holder_funding("Hyperliquid", "2026-10-01")[0] == "revenue"
    groups = {(n, "retired_buyback_tokens"): _frame(n, "retired_buyback_tokens", _days("2026-10-01", [7, 8, 9]))
              for n in ("Aerodrome", "Hyperliquid")}
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[n] for n in ("Aerodrome", "Hyperliquid")])
    free_float.buying_views(groups)
    assert list(bw._daily(groups[("Aerodrome", "float_buying_tokens")]).values) == [0.0, 0.0, 0.0]
    assert list(bw._daily(groups[("Hyperliquid", "float_buying_tokens")]).values) == [7.0, 8.0, 9.0]
    assert "float_buying_tokens" in config.BUYBACK_SPLIT_METRICS    # no row where the buyback route is 'none'


def test_the_headers_say_the_base_of_each_net():
    assert "÷ average market cap over the window" in bw.DILUTION_MC_HEAD and "÷ average market cap" in bw.CP_NHY_HEAD
    assert "÷ free float" in bw.BUYING_PRESSURE_HEAD and "revenue-funded" in bw.BUYING_PRESSURE_HEAD
    assert "both ÷ free float" in bw.NET_FLOAT_PRESSURE_HEAD
    assert bw.FREE_FLOAT_HEAD.startswith("FREE FLOAT (tokens) = circulating − staked/locked/earning − issuer")


# ====================================================================================== the check itself can fail
def _book(path, sheets: dict):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    wb.save(path)
    return path


def test_the_workbook_check_fails_on_a_float_that_is_not_the_series(tmp_path):
    data = [["Key (project|metric)", "Project", "Now (flow: trailing 30d sum · stock: latest)"],
            ["Maple|free_float_tokens", "Maple", 1000.0], ["Sky|free_float_tokens", "Sky", "n/a"]]
    good = {"Data": data,
            "A2 Coordination": [["x"], ["Project", bw.FREE_FLOAT_HEAD], ["Maple", 1000.0]],
            "CP Comparability": [["Project", "Circulating supply (tokens) — ours", "Free float ÷ circulating"],
                                 ["Maple", 2000.0, 0.5]]}
    reader = {"Summary": [["Project", "Total dilution, % of float", "Total dilution, tokens a year"],
                          ["Maple", 0.1, 100.0]],
              "A2 Coordination": [["Project", "Free float (tokens)"], ["Maple", 1000.0]]}
    a, r = _book(tmp_path / "a.xlsx", good), _book(tmp_path / "r.xlsx", reader)
    assert free_float.check_workbook(a, r) == []
    # a tab's float differs from the series
    bad = dict(good, **{"A2 Coordination": [["Project", bw.FREE_FLOAT_HEAD], ["Maple", 1000.5]]})
    out = free_float.check_workbook(_book(tmp_path / "b.xlsx", bad), r)
    assert len(out) == 1 and "free float 1,000.50 is not the series' 1,000.00" in out[0]
    # a float shown where the series holds none, and a deleted float's name on a tab
    bad = dict(good, **{"A3 Revenue Buyback": [["Project", bw.FREE_FLOAT_HEAD, "Effective float = circulating − ve locked"],
                                               ["Sky", 5.0, 5.0]]})
    out = free_float.check_workbook(_book(tmp_path / "c.xlsx", bad), r)
    assert any("names a float other than the one free float" in x for x in out)
    assert any("Sky: shows a free float 5.00 but the series holds none" in x for x in out)
    # a ratio built on another float, in the reader
    bad_r = dict(reader, Summary=[["Project", "Total dilution, % of float", "Total dilution, tokens a year"],
                                  ["Maple", 0.1, 120.0]])
    out = free_float.check_workbook(a, _book(tmp_path / "d.xlsx", bad_r))
    assert len(out) == 1 and "total dilution tokens ÷ % of float" in out[0]


# ====================================================================================== the built workbook (15 dumps)
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from shared_build import plain_build
    return plain_build(tmp_path_factory, "r20_float")


def _cp(built):
    wb = openpyxl.load_workbook(built["audit"], data_only=True, read_only=True)
    heads, body = free_float._table(wb["CP Comparability"])
    return heads, {str(r[0]).strip(): r for r in body}


def test_no_tab_of_the_built_workbook_shows_a_float_different_from_the_series(built):
    """Jake's test: every "Free float (tokens)" cell on A1 / A2 / A3 / CP and the reader's tabs, CP's float ÷ circulating
    and Summary's dilution in tokens, against the one series; no tab names another float."""
    assert free_float.check_workbook(built["audit"], built["reader"]) == []


def test_the_two_nets_are_each_the_difference_of_their_two_sides(built):
    assert free_float.check_nets(built["audit"]) == []


def test_the_series_holds_what_the_tabs_show_and_its_components_add_up(built):
    g = built["groups"]
    heads, rows = _cp(built)
    col = lambda pre: next(j for j, h in enumerate(heads) if h.startswith(pre))  # noqa: E731
    j_ff, j_c = col("FREE FLOAT (tokens)"), col("Circulating supply (tokens)")
    j_s, j_h, j_i = col("Staked / locked inside circulating"), col("Held buyback inside circulating"), col("Issuer, foundation")
    seen = 0
    for name, r in rows.items():
        ff = bw._daily(g.get((name, FF)))
        if ff.empty or not isinstance(r[j_ff], (int, float)):
            continue
        assert r[j_ff] == pytest.approx(float(ff.iloc[-1]), rel=1e-9), name
        held = r[j_h] if isinstance(r[j_h], (int, float)) else 0.0
        assert r[j_c] - r[j_s] - r[j_i] - held == pytest.approx(r[j_ff], rel=1e-9, abs=1e-3), name
        seen += 1
    assert seen >= 10


FLOATS_20261013 = {
    # project: the one free float on the 15 dumps (as of 2026-10-13) — was, per tab, in the comment
    "Chainlink": 718120127,        # A1 "Float after staking" read 957,474,809 (issuer stock still in it); CP / A2 / A3 718,120,127
    "Ethereum": 78452007,          # A1 and CP 78,452,007 (the Foundation's legs not read on the dumps: R18 pending)
    "Hyperliquid": 99462452,       # A1 read 337,547,997 (issuer stock still in it); CP / A3 99,462,452
    "Aerodrome": 903043091,        # A3 "Effective float" read 963,637,222; CP 903,043,091
    "Sky": 5880380760,             # A3 "Effective float" read 5,918,161,883; CP 5,880,380,760
    "Morpho": 426052377,           # A2 and CP 426,052,377
    "Uniswap": 612409760,          # CP 612,409,760
    "Pendle": 143108807,           # A3 "Effective float" read 206,548,528; CP 143,108,807
    "Fluid": 77971195,             # A3 and CP 77,971,195
    "Ether.fi": 686857950,         # A3 and CP 686,857,950
    "GEODNET": 513407600,          # A2, A3 and CP 513,407,600
}


def test_the_one_float_on_the_dumps_is_the_issuer_free_figure(built):
    """Part 3: issuer stock is off the float everywhere — the figure CP showed; the tabs that showed more now show it."""
    heads, rows = _cp(built)
    j = next(i for i, h in enumerate(heads) if h.startswith("FREE FLOAT (tokens)"))
    for name, want in FLOATS_20261013.items():
        assert rows[name][j] == pytest.approx(want, abs=1.0), name
    wb = openpyxl.load_workbook(built["audit"], data_only=True, read_only=True)
    for tab, names in (("A1 Infrastructure", ("Chainlink", "Hyperliquid", "Ethereum")),
                       ("A3 Revenue Buyback", ("Aerodrome", "Sky", "Pendle", "Chainlink"))):
        h, body = free_float._table(wb[tab])
        jj = next(i for i, x in enumerate(h) if x.startswith("FREE FLOAT (tokens)"))
        got = {str(r[0]).strip(): r[jj] for r in body}
        for name in names:
            assert got[name] == pytest.approx(FLOATS_20261013[name], abs=1.0), (tab, name)


def test_the_flows_sheet_carries_the_float_statement_beside_the_circulating_one(built):
    wb = openpyxl.load_workbook(built["audit"], data_only=True, read_only=True)
    rows = list(wb["Flows"].iter_rows(values_only=True))
    hy = [r for r in rows if r and r[0] == "Hyperliquid"]
    kinds = [r[1] for r in hy]
    assert "Token flow" in kinds and free_float.STATEMENT in kinds
    assert kinds.index(free_float.STATEMENT) > kinds.index("Token flow")
    assert f"{free_float.STATEMENT} — weekly" in kinds and f"{free_float.STATEMENT} — daily" in kinds
    st = {r[2]: r for r in hy if r[1] == free_float.STATEMENT}
    q0 = lambda line: st[line][3]  # noqa: E731
    lines = [lab for _k, lab in free_float.LINES]
    assert q0("Opening free float") + sum(q0(x) for x in lines) == pytest.approx(
        q0("Expected closing (opening + the movements)"))
    assert q0("Closing free float") == pytest.approx(FLOATS_20261013["Hyperliquid"], abs=1.0)
    assert str(q0("Status")).startswith(("OK", "CHECK"))
    # the circulating statement's rows are unchanged in FLOWS_ROWS' own shape: still one Status line per project
    assert sum(1 for r in hy if r[1] == "Token flow" and r[2] == "Status") == 1
