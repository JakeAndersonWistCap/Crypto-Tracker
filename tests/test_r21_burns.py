"""ROUND 21 — BURNS (Jake 2026-11-25): burns count in net float pressure; the bought-back-token rules F1-F7; paused /
off programmes labelled by evidence and unranked.

    net float pressure = revenue-funded buying + burns − new supply − bought tokens returned to the float  (÷ free float)

1. Every project lists its burn series or says it has none; NO BURN SERIES IS MISSING FROM THE NET (the test fails on a
   burn series that is neither in the net nor declared out of it with its reason, and on a burn leg of the circulating
   statement the net does not read); free_float.check_burns, the check Jake runs on the store's workbook.
2. A burn is never buying: the buying line is the held + distributed buyback (F2) + purchases paid to customers (F1).
3. Bought tokens returned to the float (F1 / F2 / F3 / F7); F3: Sky's staking rewards covered by purchases are not new
   supply.   4. The burn-source memo.   5. F4 labels; decision 5: unranked.
"""
from __future__ import annotations

import json
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
import reader  # noqa: E402
from test_round_p4_q5 import FakeRefs, evaluate  # noqa: E402

P = config.PROJECT_BY_NAME


def _frame(name, metric, pairs, source="test:read"):
    return pd.DataFrame({"date": [pd.Timestamp(d) for d, _v in pairs], "project": name, "metric": metric,
                         "value": [float(v) for _d, v in pairs], "source": source, "tier": 1,
                         "fetched_at": "2026-10-13T00:00:00Z"})


def _days(start, values):
    d0 = pd.Timestamp(start)
    return [(d0 + pd.Timedelta(days=i), v) for i, v in enumerate(values) if v is not None]


def _is_burn_flow(m: str) -> bool:
    md = config.METRICS.get(m) or {}
    return "burn" in m and md.get("kind") == "flow" and md.get("unit") == "tokens"


# ====================================================================================== 1. every project, every burn
BURNS_R21 = {
    "Ethereum": {"gross_burn_tokens"}, "Uniswap": {"gross_burn_tokens"},
    "Sky": {"sky_stage2_burn_tokens"}, "Near": {"gross_burn_tokens_bigquery"},
    "Aethir": {"gross_burn_tokens"}, "GEODNET": {"gross_burn_tokens"},
    "Aerodrome": {"buyback_burned_tokens", "foundation_aero_burned_other_tokens"},
    "Hyperliquid": {"total_burn_tokens"},
    "Chainlink": set(), "Fluid": set(), "Ether.fi": set(), "Maple": set(), "Pendle": set(), "Morpho": set(),
}


def test_every_project_lists_its_burn_series_or_says_it_has_none():
    assert set(BURNS_R21) == set(config.BURN_PROJECTS_R21)
    for n, want in BURNS_R21.items():
        assert (n in config.BURN_SERIES) != (n in config.BURN_NONE), n
        got = config.burn_series(n)
        assert {b["metric"] for b in got} == want, n
        for b in got:
            assert b["from"] in config.BURN_SOURCES and b["why"], (n, b)
            assert b["metric"] in config.metrics_for_project(P[n]), (n, b["metric"])
        if not got:
            assert config.BURN_NONE[n].startswith("no burn mechanism"), n


def test_no_burn_series_is_missing_from_the_net():
    """Jake: "add a test that fails if a burn series is missing from the net". Every burn-named token flow a project
    reads is in its net float pressure or declared out of it with its reason (config.BURN_NOT_IN_NET), and every burn
    leg of its circulating statement (the burns that move circulating) is in the net."""
    for n in config.BURN_PROJECTS_R21:
        p = P[n]
        declared = {b["metric"] for b in config.burn_series(n)}
        for m in config.metrics_for_project(p):
            if _is_burn_flow(m):
                assert m in declared or (n, m) in config.BURN_NOT_IN_NET, (n, m, "a burn series missing from the net")
        groups = {(n, m): _frame(n, m, [("2026-10-01", 1.0)]) for m in config.metrics_for_project(p)}
        for m in declared:
            groups[(n, m)] = _frame(n, m, [("2026-10-01", 1.0)])
        _have, _d, _pre, _rel, legs = bw._token_flow_legs(p, groups)
        burn_legs = {m for lab, m, s in legs if s < 0 and m and not lab.startswith("− returned")}
        assert burn_legs <= declared, (n, burn_legs - declared)
    # the declared reasons are for real series only (no stale entry)
    for (n, m), why in config.BURN_NOT_IN_NET.items():
        # a build-time view (the buyback's burned / held / distributed split) is a real series of the Data sheet too
        # R22: the since-the-burn-began views (`<m>_sinceburn`) are build-time views of a series the project reads
        base = m[:-len("_sinceburn")] if m.endswith("_sinceburn") else None
        assert why and (m in config.metrics_for_project(P[n]) or m in config.BUYBACK_SPLIT_METRICS
                        or (base and m in config.METRICS and n in config.BURN_SINCE_LEGS)), (n, m)


def test_the_burns_cell_reads_every_declared_series_each_over_its_own_days(monkeypatch):
    """BURNS ON FLOAT = Σ each declared burn series annualised over its own covered days (Aerodrome's mint-funded burn
    over its mint's — the issuance series'), ÷ the one free float. Aerodrome: 900,000 over 90 days and the 10,000,000
    of 11-20 annualised over the issuance series' 40 days, on a float of 1,000,000,000."""
    monkeypatch.setattr(free_float, "MISSING", {})
    aero = P["Aerodrome"]
    vals = {("buyback_burned_tokens", "q0"): 900_000.0, ("buyback_burned_tokens", "q0_covered_days"): 90.0,
            ("foundation_aero_burned_other_tokens", "q0"): 10_000_000.0,
            ("foundation_aero_burned_other_tokens", "q0_covered_days"): 30.0,
            ("gross_issuance_accrued_tokens", "q0_covered_days"): 40.0,
            (config.FREE_FLOAT_METRIC, "now"): 1_000_000_000.0}
    dbk = {f"Aerodrome|{m}": {} for m in ("buyback_burned_tokens", "foundation_aero_burned_other_tokens")}
    R = FakeRefs(vals)
    burns = next(c for c in bw._float_pressure_cols(R, dbk) if c[0] == bw.BURNS_HEAD)[1]
    v = evaluate(burns(5, aero), vals, R)
    assert v == pytest.approx((900_000.0 * 365 / 90 + 10_000_000.0 * 365 / 40) / 1e9, rel=1e-12)
    assert {m for m, _c in R.cells} >= {"buyback_burned_tokens", "foundation_aero_burned_other_tokens"}
    # the mint-funded burn nets its mint exactly: the same tokens over the same days as the issuance leg
    assert 10_000_000.0 * 365 / 40 / 1e9 == pytest.approx(v - 900_000.0 * 365 / 90 / 1e9)
    # an event leg with no row yet (the dumps predate 2026-10-22) is no event; a missing ordinary leg is unknown
    R2 = FakeRefs(vals)
    burns2 = next(c for c in bw._float_pressure_cols(R2, {"Aerodrome|buyback_burned_tokens": {}}) if c[0] == bw.BURNS_HEAD)[1]
    assert evaluate(burns2(5, aero), vals, R2) == pytest.approx(900_000.0 * 365 / 90 / 1e9)
    burns3 = next(c for c in bw._float_pressure_cols(FakeRefs({}), {}) if c[0] == bw.BURNS_HEAD)[1]
    assert burns3(5, P["Uniswap"]).startswith("n/a — burns: the declared burn series gross_burn_tokens holds no row")
    assert burns3(5, P["Maple"]) == "=0"                                  # no burn mechanism: a declared 0


def _cp_book(path, rows, data_keys, burns_formula=None):
    """A CP-shaped audit file: Data keys in column A; CP's table with the burns and net float pressure columns."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    d = wb.create_sheet("Data")
    d.append(["Key (project|metric)", "Points"])
    for k in data_keys:
        d.append([k, 5])
    d.append(["Aerodrome|gross_burn_tokens", 0])          # a row with no points: not stored, not judged
    cp = wb.create_sheet("CP Comparability")
    cp.append(["x"])
    cp.append(["Project", bw.BURNS_HEAD, bw.NET_FLOAT_PRESSURE_HEAD])
    for name, f in rows:
        cp.append([name, f, "=1+B3-0"])
    wb.save(path)
    return path


def test_check_burns_passes_and_fails_where_a_burn_series_is_missing(tmp_path):
    keys = ["Aerodrome|buyback_burned_tokens", "Aerodrome|foundation_aero_burned_other_tokens"]
    good = '=IF(1,(INDEX(Data!$E$1:$E$9,MATCH($A3&"|buyback_burned_tokens",x))+INDEX(x,MATCH($A3&"|foundation_aero_burned_other_tokens",x))),0)'
    assert free_float.check_burns(_cp_book(tmp_path / "a.xlsx", [("Aerodrome", good)], keys)) == []
    bad = '=IF(1,INDEX(Data!$E$1:$E$9,MATCH($A3&"|buyback_burned_tokens",x)),0)'
    out = free_float.check_burns(_cp_book(tmp_path / "b.xlsx", [("Aerodrome", bad)], keys))
    assert out == ["Aerodrome: the burn series foundation_aero_burned_other_tokens is stored but not in net float "
                   "pressure's burns cell"]
    out = free_float.check_burns(_cp_book(tmp_path / "c.xlsx", [("Aerodrome", "=0")], keys))
    assert any("its burns cell is a declared 0" in x for x in out)
    # a burn series stored for the project that is neither in the net nor declared out of it
    config.METRICS.setdefault("zz_test_burn_tokens", {"kind": "flow", "unit": "tokens"})
    try:
        out = free_float.check_burns(_cp_book(tmp_path / "d.xlsx", [("Aerodrome", good)],
                                              keys + ["Aerodrome|zz_test_burn_tokens"]))
        assert out == ["Aerodrome: the burn series zz_test_burn_tokens is neither in net float pressure nor declared "
                       "out of it (config.BURN_NOT_IN_NET)"]
    finally:
        config.METRICS.pop("zz_test_burn_tokens", None)
    # the net does not read the burns cell
    wb = openpyxl.load_workbook(_cp_book(tmp_path / "e.xlsx", [("Aerodrome", good)], keys))
    wb["CP Comparability"]["C3"] = "=1-0"
    wb.save(tmp_path / "e.xlsx")
    assert free_float.check_burns(tmp_path / "e.xlsx") == ["Aerodrome: net float pressure does not read its burns cell B3"]


def test_check_nets_holds_net_float_pressure_to_its_four_sides(tmp_path):
    heads = ["Project", "HOLDER VALUE YIELD x", "DILUTION, % OF MARKET CAP x", "NET HOLDER YIELD x",
             bw.BUYING_PRESSURE_HEAD, bw.BURNS_HEAD, bw.RETURNED_HEAD,
             "TOTAL DILUTION = outflows a+b ÷ free float", bw.NET_FLOAT_PRESSURE_HEAD]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "CP Comparability"
    ws.append(heads)
    ws.append(["Sky", 0.02, 0.01, 0.01, 0.056, 0.0104, 0.0408, 0.0858, 0.056 + 0.0104 - 0.0858 - 0.0408])
    ws.append(["Uniswap", 0.03, 0.03, 0.0, 0.0, 0.037, 0.0, 0.0335, 0.0])          # the burn left out of the net
    wb.save(tmp_path / "n.xlsx")
    out = free_float.check_nets(tmp_path / "n.xlsx")
    assert len(out) == 1 and out[0].startswith("Uniswap: net float pressure 0.000000 is not buying pressure")


# ====================================================================================== 2. the buying line
def test_buying_is_the_held_and_distributed_buyback_and_never_the_burned_one(monkeypatch):
    """F2: a buyback paid to stakers counts as buying; a burned buyback is on the burns line (counted once, when burned);
    F1: Ether.fi's purchases paid on to card users count as buying; a non-revenue day is a measured 0."""
    groups = {("Sky", "buyback_held_tokens"): _frame("Sky", "buyback_held_tokens", _days("2026-10-01", [1, 1, 1])),
              ("Sky", "buyback_distributed_tokens"): _frame("Sky", "buyback_distributed_tokens", _days("2026-10-01", [8, 8, 8])),
              ("Sky", "buyback_burned_tokens"): _frame("Sky", "buyback_burned_tokens", _days("2026-10-01", [2, 2, 2])),
              ("Ether.fi", "buyback_distributed_tokens"): _frame("Ether.fi", "buyback_distributed_tokens",
                                                                 _days("2026-10-01", [5, 5, 5])),
              ("Ether.fi", "buyback_to_customers_tokens"): _frame("Ether.fi", "buyback_to_customers_tokens",
                                                                  _days("2026-10-01", [3, 4, 6])),
              ("Hyperliquid", "buyback_burned_tokens"): _frame("Hyperliquid", "buyback_burned_tokens",
                                                               _days("2026-10-01", [9, 9, 9]))}
    monkeypatch.setattr(bw, "scoped_projects", lambda: [P[n] for n in ("Sky", "Ether.fi", "Hyperliquid")])
    free_float.buying_views(groups)
    assert list(bw._daily(groups[("Sky", "float_buying_tokens")]).values) == [9.0, 9.0, 9.0]
    # Ether.fi's staker buyback is treasury-funded on these days (0); the cashback purchases count (F1)
    assert config.holder_funding("Ether.fi", "2026-10-01")[0] == "treasury"
    assert list(bw._daily(groups[("Ether.fi", "float_buying_tokens")]).values) == [3.0, 4.0, 6.0]
    assert ("Hyperliquid", "float_buying_tokens") not in groups          # bought and burned: the burns line only
    for n in ("Ethereum", "Uniswap", "Aethir"):
        assert bw._buying_pressure_col(FakeRefs({}))[1](5, P[n]) == "=0", n     # was n/a / "by design"
    assert not config.BUYING_PRESSURE_BY_DESIGN


# ====================================================================================== 3. returned to the float
def test_a_payout_the_purchases_covered_returns_to_the_float_and_a_held_wobble_does_not(monkeypatch):
    groups = {("Ether.fi", "cashback_paid_tokens"): _frame("Ether.fi", "cashback_paid_tokens", _days("2026-10-01", [10, 10, 10])),
              ("Ether.fi", "cashback_unfunded_tokens"): _frame("Ether.fi", "cashback_unfunded_tokens",
                                                               _days("2026-10-01", [0, 4, None])),
              # Maple's SSF: a wobble (+5, −2) is no departure; 7 bought on day 4 and the stock back at 100 on day 5
              ("Maple", "ssf_holdings_tokens"): _frame("Maple", "ssf_holdings_tokens",
                                                       _days("2026-10-01", [100, 105, 103, 110, 100])),
              ("Maple", "buyback_held_tokens"): _frame("Maple", "buyback_held_tokens", [("2026-10-04", 7.0)])}
    monkeypatch.setattr(bw, "scoped_projects", lambda: [P[n] for n in ("Ether.fi", "Maple", "Pendle")])
    free_float.returned_views(groups)
    e = bw._daily(groups[("Ether.fi", "float_returned_tokens")])
    assert list(e.values) == [10.0, 6.0] and pd.Timestamp("2026-10-03") not in e.index     # a gap, never 0
    m = bw._daily(groups[("Maple", "float_returned_tokens")])
    assert m.sum() == pytest.approx(7.0) and m[pd.Timestamp("2026-10-05")] == pytest.approx(7.0)
    assert ("Pendle", "float_returned_tokens") not in groups           # paid as sPENDLE: stays off the float (F2)
    assert "Pendle" in config.FLOAT_RETURNS_NONE


def test_f3_skys_staking_rewards_covered_by_purchases_are_not_new_supply():
    """Only the vest the SKY bought for stakers did not cover is emissions (c); purchases before Stage 2 (2026-08-17)
    fund nothing (bought_from_since)."""
    assert config.OUTFLOW_CATEGORIES["Sky"]["c"] == ("sky_staking_rewards_unfunded_tokens",)
    groups = {("Sky", "emissions_tokens"): _frame("Sky", "emissions_tokens",
                                                  [("2026-08-15", 10), ("2026-08-17", 10), ("2026-08-18", 10),
                                                   ("2026-08-19", 10)]),
              ("Sky", "buyback_distributed_tokens"): _frame("Sky", "buyback_distributed_tokens",
                                                            [("2026-08-15", 50), ("2026-08-17", 4), ("2026-08-18", 30),
                                                             ("2026-08-19", 1)])}
    bw._cashback_funding_views(groups)
    out = bw._daily(groups[("Sky", "sky_staking_rewards_unfunded_tokens")])
    # 08-15: 10 paid, the 50 bought before Stage 2 does not count -> 10; 08-17: +10 paid, +4 bought -> 16 (+6);
    # 08-18: +10, +30 -> cumulative gap 0, the running maximum keeps 16 (+0); 08-19: 40 paid, 35 bought -> 5 (+0)
    assert list(out.values) == [10.0, 6.0, 0.0, 0.0]
    assert set(config.STAKING_REWARDS_F3) == set(config.BURN_PROJECTS_R21)


# ====================================================================================== 4. the memo
def test_the_memo_says_where_each_burn_came_from_and_flags_off_float_burns(monkeypatch):
    groups = {("Aerodrome", "buyback_burned_tokens"): _frame("Aerodrome", "buyback_burned_tokens",
                                                             _days("2026-11-01", [100_000.0] * 4)),
              ("Aerodrome", "foundation_aero_burned_other_tokens"): _frame(
                  "Aerodrome", "foundation_aero_burned_other_tokens", [("2026-11-20", 10_000_000.0)]),
              ("Aerodrome", config.FREE_FLOAT_METRIC): _frame("Aerodrome", config.FREE_FLOAT_METRIC,
                                                              [("2026-11-20", 1_000_000_000.0)])}
    m = free_float.burn_memo("Aerodrome", groups, "2026-11-20")
    assert m["staked"] == pytest.approx(10_400_000.0) and m["float"] == 0.0
    # each leg over its own days in the window: 400,000 over 4 days, the 10M over the 1 day its series covers (the memo
    # annualises it over its mint's days where that series is stored — not in this test)
    assert m["off_float_rate"] == pytest.approx((400_000.0 * 365 / 4 + 10_000_000.0 * 365 / 1) / 1e9)
    assert "MOVES THE HEADLINE BY MORE THAN 1 POINT" in m["text"] and "10,000,000 AERO minted 11-19" in m["text"]
    # Uniswap's 100M one-off left the Timelock: treasury; the burn-to-claim is from float
    g = {("Uniswap", "gross_burn_tokens"): _frame("Uniswap", "gross_burn_tokens",
                                                  [("2025-12-27", 100_000_000.0), ("2025-12-28", 50_000.0)]),
         ("Uniswap", config.FREE_FLOAT_METRIC): _frame("Uniswap", config.FREE_FLOAT_METRIC, [("2025-12-28", 6e8)])}
    u = free_float.burn_memo("Uniswap", g, "2025-12-28")
    assert u["treasury"] == pytest.approx(1e8) and u["float"] == pytest.approx(50_000.0)
    c = free_float.burn_memo("Chainlink", {}, "2026-10-13")
    assert c["text"].startswith("no burn — no burn mechanism") and c["off_float_rate"] is None
    # a declared series with no row in the window is named, never read as 0
    s = free_float.burn_memo("Sky", {}, "2026-10-13")
    assert s["missing"] == ["sky_stage2_burn_tokens"] and "NO ROW in the window" in s["text"]


# ====================================================================================== 5. labels; unranked
def test_paused_and_off_programmes_are_labelled_by_evidence_and_unranked():
    assert config.archetype_status("Fluid", 3) == "paused, no restart condition stated"
    assert config.archetype_status("Ether.fi", 3) == "not executing since 1 Apr"
    assert config.archetype_status("Morpho", 3) == "off"
    keys = {"Sky": (3, 0.9, None, ""), "Fluid": (3, 0.0, None, ""), "Ether.fi": (3, 0.0, None, ""),
            "Maple": (3, 0.2, None, ""), "Morpho": (3, 0.0, None, "")}
    ranks = {n: rk for n, rk, _v in reader._arch_ranking(keys, 3, tiebreak={})}
    assert ranks["Sky"] == 1 and ranks["Maple"] == 2
    assert ranks["Fluid"] is None and ranks["Ether.fi"] is None and ranks["Morpho"] is None     # "unranked"
    # the A3 key is no longer forced to 0: it reads the data (0 only where the data says 0)
    build = bw._a3_key_col(FakeRefs({}))[1]
    for n in ("Fluid", "Ether.fi", "Morpho"):
        assert build(5, P[n]) != "=0", n


# ====================================================================================== the fixture (Sky's dump)
def _aggregate(tmp_path, name):
    """aggregate() alone on one real dump (the views every cell reads), as of the dump's date."""
    import store as sm
    from real_dump import load, to_store
    pl = load(name)
    db = tmp_path / "metrics.db"
    to_store(pl, db)
    asof = pd.Timestamp(pl["meta"]["asof"])
    st = sm.Store(db)
    old = bw._SCOPE
    try:
        bw._SCOPE = [P[name]]
        long = bw.drop_store_only(st.load_long())
        bw._WINDOWS.clear()
        for label, start, end in bw._period_windows(asof):
            bw._WINDOWS[label.lower()] = (start.date().isoformat(), end.date().isoformat())
        bw.aggregate(long, st.fetch_status(), asof, gaps=st.gap_report(), review=st.review_queue())
    finally:
        st.close()
        bw._SCOPE = old
    return asof, {k: v for k, v in bw._LAST_GROUPS.items() if k[0] == name}


def _q0(groups, name, m, asof):
    s = bw._daily(groups.get((name, m)))
    return float(s[(s.index > asof - pd.Timedelta(days=90)) & (s.index <= asof)].sum())


def test_sky_on_the_dump_buying_burns_returned_and_f3(tmp_path):
    asof, g = _aggregate(tmp_path, "Sky")
    q = lambda m: _q0(g, "Sky", m, asof)  # noqa: E731
    # buying: held + distributed on revenue days = 81,135,933.2 (R20: burned + held = 34,336,927.2)
    assert q("float_buying_tokens") == pytest.approx(81_135_933.2, abs=1.0)
    assert q("float_buying_tokens") == pytest.approx(q("buyback_held_tokens") + q("buyback_distributed_tokens"), rel=1e-12)
    # F3: (c) = the vest the purchases did not cover, 124,413,557.1 (R20: the whole vest, 183,496,514.5)
    assert q("sky_staking_rewards_unfunded_tokens") == pytest.approx(124_413_557.1, abs=1.0)
    assert q("emissions_tokens") == pytest.approx(183_496_514.5, abs=1.0)
    # the covered part returns to the float (liquid rewards): 59,082,957.4
    assert q("float_returned_tokens") == pytest.approx(q("emissions_tokens") - q("sky_staking_rewards_unfunded_tokens"),
                                                       rel=1e-9)
    # the burns: the two Stage 2 spells (2,860,943.76 on 09-13 + 7,372,288 on 10-12), out of the Pause Proxy
    m = free_float.burn_memo("Sky", g, asof)
    assert m["treasury"] == pytest.approx(10_233_231.8, abs=1.0) and m["float"] == 0.0
    # the monthly spells annualised by events x 12, as the burns cell: 10,233,231.8 / 2 x 12 = 61.4M a year, 1.04%
    assert m["off_float_rate"] == pytest.approx(10_233_231.8 / 2 * 12 / 5_880_380_760.3, rel=1e-6)
    assert "MOVES THE HEADLINE BY MORE THAN 1 POINT" in m["text"]


def test_the_spec_records_the_round():
    spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    assert spec.index("### Round 21 — burns") < spec.index("### Round 20")
    assert json.dumps(config.ONE_OFF_NAMED["Sky"][0]["from"]) == '"2026-11-19"'


def test_done_check_reads_a_decision_5_unranked_as_decided():
    import datetime

    import done_check
    why = ("Unranked — off: the fee switch is off. A paused or off programme is shown with its label and not ranked "
           "(Jake 2026-11-25, decision 5); its key shows 0 only where the data says 0.")
    assert done_check.classify("unranked", why, datetime.date(2026, 11, 25))[0] is True
    assert done_check.classify("unranked", "no measured key", datetime.date(2026, 11, 25))[0] is False
