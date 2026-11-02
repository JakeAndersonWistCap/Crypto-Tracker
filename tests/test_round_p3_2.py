"""ROUND P3-2 (Jake 2026-11-02, SPEC P3-2a..P3-2e) — definition changes R3 (A3, A4), R4 (Chainlink) and the A1 key.

P3-2a  Uniswap's outflow (d) grants and (e) releases: checked against the source — SEPARATE wallets and different tokens
       (config.OUTFLOW_LEG_CHECKS), so nothing is counted twice and nothing changes.
P3-2b  A3's key when emissions are 0: retired ÷ (emissions + unlocks), AMBER with the caveat (Maple, Ether.fi); ties on
       the key are broken by net holder yield in the ranking helper Summary and the tabs share; the key's own Trajectory
       row uses the same fallback, so its latest quarter is the key.
P3-2c  Uniswap's A4 key on the release basis (issuance = releases into circulating, config.A4_RELEASE_ISSUANCE).
P3-2d  Chainlink's primary archetype is A2; its A1 and A3 columns stay as "secondary".
P3-2e  trajectory._own_breaks normalises a declared handover's ordered_points (Ethereum's 2026-09-29 handover is declared).

Built once on the committed real dumps (fixtures/real_*.json), as test_round_p1 / test_round_p2e_ranks do.
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
import trajectory  # noqa: E402

A3, A4, CP = "A3 Revenue Buyback", "A4 Permanent Burn", "CP Comparability"
TABS = {1: reader.A1_TAB, 2: reader.A2_TAB, 3: reader.A3_TAB, 4: reader.A4_TAB}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Both workbooks built on the committed real dumps, and the read-time groups the build used."""
    import build_workbook as bw
    import measuring_guard as mg
    import store as sm
    from real_dump import to_store
    d = tmp_path_factory.mktemp("p3_2")
    db = d / "metrics.db"
    asofs = []
    for pl in mg._payloads():
        to_store(pl, db)
        asofs.append(pl["meta"]["asof"])
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sm, "DB_PATH", str(db))
        mp.setitem(sys.modules, "recalc", None)
        mp.setenv("TOKEN_METRICS_STORE_VALUES", "1")
        st = sm.Store(str(db))
        try:
            bw.build_workbook(st, d / "audit.xlsx", asof=pd.Timestamp(max(asofs)), reader_path=d / "reader.xlsx")
        finally:
            st.close()
    assert "error" not in bw.READER_STATS, bw.READER_STATS
    assert bw.READER_STATS.get("blank_unexplained") == 0, bw.READER_STATS.get("blank_cells")
    import xlcalc
    a, r = openpyxl.load_workbook(d / "audit.xlsx"), openpyxl.load_workbook(d / "reader.xlsx")
    return {"audit": a, "reader": r, "XA": xlcalc.Workbook(a), "XR": xlcalc.Workbook(r),
            "groups": dict(bw._LAST_GROUPS), "asof": pd.Timestamp(max(asofs))}


def _audit_cell(wb, sheet: str, head: str, project: str):
    ws = wb[sheet]
    c = next(c for c in range(1, ws.max_column + 1) if str(ws.cell(4, c).value or "").startswith(head))
    r = next(r for r in range(5, ws.max_row + 1) if ws.cell(r, 1).value == project)
    return ws.cell(r, c)


def _reader_rows(ws) -> dict:
    return {ws.cell(r, 1).value: r for r in range(5, ws.max_row + 1)
            if ws.cell(r, 1).value and ws.cell(r, 2).value is not None}


def _reader_col(ws, pred) -> int:
    return next(c for c in range(1, ws.max_column + 1) if pred(str(ws.cell(4, c).value or "")))


def _daily(groups, project, metric) -> pd.Series:
    g = groups.get((project, metric))
    if g is None or g.empty:
        return pd.Series(dtype=float)
    return (g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).groupby("date")["value"].sum().astype(float))


def _data_value(X, wb, key: str, letter: str):
    ws = wb["Data"]
    r = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, 1).value == key)
    return X.value("Data", r, openpyxl.utils.column_index_from_string(letter))


# ============================================================================================== P3-2a
def test_p3_2a_uniswap_grants_d_and_releases_e_are_separate_wallets_with_the_source():
    """(d) = the falls of the governance Timelock; (e) = the falls of the docs' vesting contracts and the airdrop merkle
    distributor. Disjoint wallets, and the whole set is exactly the two (the A4 release basis, P3-2c, counts both once)."""
    cats = config.OUTFLOW_CATEGORIES["Uniswap"]
    assert cats["d"] == ("growth_budget_release_tokens",) and cats["e"] == ("unlock_release_vesting_tokens",)
    sub = config.UNLOCK_RELEASE_SUBSETS["Uniswap"]
    d_legs, e_legs = set(sub["growth_budget_release_tokens"]), set(sub["unlock_release_vesting_tokens"])
    assert d_legs == {"treasury_holding_tokens"} and e_legs == {"noncirculating_holding_tokens"}
    assert not d_legs & e_legs
    assert set(config.unlock_stock_metrics("Uniswap")) == d_legs | e_legs
    timelock = config.PROJECT_BY_NAME["Uniswap"]["contracts"]["treasury"]["address"].lower()
    e_wallets = {w[1].lower() for w in config._NONCIRC_WALLETS_FIRST_PARTY["Uniswap"]}
    assert timelock == "0x1a9c8182c09f50c8318d769245bea52c32be35bc" and timelock not in e_wallets
    assert "0x090d4613473dee047c3f2706764f49e0821d256e" in e_wallets          # the 2020 airdrop's merkle distributor
    # the check, with its source, is on record — and says nothing changes
    rec = config.OUTFLOW_LEG_CHECKS["Uniswap"]
    assert rec["verdict"].startswith("SEPARATE") and rec["changed"] == "nothing"
    for s in ("0x1a9C8182", "0x090D4613", "proposals/93", "2024-12-25", "technical-reference.mdx"):
        assert s in rec["evidence"] + " " + rec["source"], s


def test_p3_2a_on_the_fixture_no_token_is_in_both_legs(built):
    """A token counted in both legs would be an internal move (a vesting contract's claim INTO the Timelock): the whole
    set nets it on its day, the legs do not, so (d) + (e) would exceed the whole set's release by it. On the fixture they
    agree: in total, and on every day but the 100M burn's landing days (the one-off is taken out of the whole set's fall,
    which there includes 1,200 UNI of airdrop claims, moved to the next day — same total) and a day a stock ROSE (a
    return netted in the whole set, never a release)."""
    g = built["groups"]
    d = _daily(g, "Uniswap", "growth_budget_release_tokens")
    e = _daily(g, "Uniswap", "unlock_release_vesting_tokens")
    whole = _daily(g, "Uniswap", "unlock_release_tokens")
    assert len(whole) > 300 and d.sum() > 0 and e.sum() > 0
    both = pd.concat([d, e, whole], axis=1, keys=["d", "e", "whole"]).dropna()
    assert len(both) > 300
    assert abs(both["d"].sum() + both["e"].sum() - both["whole"].sum()) < 1.0
    rose = set()
    for m in ("treasury_holding_tokens", "noncirculating_holding_tokens"):
        dd = _daily(g, "Uniswap", m).diff()
        rose |= set(dd[dd > 0].index)
    landed = pd.Timestamp(config.PROJECT_BY_NAME["Uniswap"]["one_off_flows"][0]["landed"])
    one_off = {landed + pd.Timedelta(days=j) for j in range(4)}
    off = both[(both["d"] + both["e"] - both["whole"]).abs() > 1e-6]
    assert set(off.index) <= rose | one_off, off
    # the grants leg is the Timelock's 5M quarterly claims (proposal 93); the releases leg the airdrop claims
    assert d.max() == pytest.approx(5_000_000, rel=1e-9)
    assert e.max() < 100_000


# ============================================================================================== P3-2b
def test_p3_2b_zero_emission_fallback_key_is_retired_over_emissions_plus_unlocks_and_amber(built):
    import build_workbook as bw
    a, XA = built["audit"], built["XA"]
    for name in ("Maple", "Ether.fi"):
        key = _audit_cell(a, A3, bw.RETIRE_VS_EMISSIONS_HEAD, name)
        v = XA.value(A3, key.row, key.column)
        assert isinstance(v, float), (name, v)
        assert "|unlock_release_tokens" in str(key.value), name
        assert str(key.fill.fgColor.rgb)[-6:] == str(bw.FILL_AMBER.fgColor.rgb)[-6:], name
        text = key.comment.text
        assert text.startswith("AMBER — a real number, qualified: FALLBACK KEY") and "emissions + unlocks" in text
        assert ("by declaration" if name == "Ether.fi" else "emissions_tokens Q0 = 0, measured") in text, text
    # Maple: the fallback IS the RETIREMENT ÷ DILUTION basis (same legs, same window)
    k = _audit_cell(a, A3, bw.RETIRE_VS_EMISSIONS_HEAD, "Maple")
    dil = _audit_cell(a, A3, bw.RETIRE_VS_DILUTION_HEAD, "Maple")
    assert XA.value(A3, k.row, k.column) == pytest.approx(XA.value(A3, dil.row, dil.column), rel=1e-12)
    # projects with emissions keep the plain key: no unlock leg, no AMBER caveat
    for name in ("Sky", "Pendle", "Fluid"):
        c = _audit_cell(a, A3, bw.RETIRE_VS_EMISSIONS_HEAD, name)
        assert "|unlock_release_tokens" not in str(c.value), name
        assert not (c.comment and "FALLBACK KEY" in c.comment.text), name
    # the reader: a ranked key, AMBER, the caveat on the cell
    r, XR = built["reader"], built["XR"]
    ws = r[reader.A3_TAB]
    rows, kc = _reader_rows(ws), _reader_col(ws, lambda h: h == "Retired ÷ emissions (x)")
    rc = _reader_col(ws, lambda h: h.startswith("Rank in"))
    for name in ("Maple", "Ether.fi"):
        cell = ws.cell(rows[name], kc)
        assert isinstance(XR.value(ws.title, rows[name], kc), float)
        assert str(cell.fill.fgColor.rgb)[-6:] == str(bw.FILL_AMBER.fgColor.rgb)[-6:]
        assert "FALLBACK KEY" in cell.comment.text
        assert isinstance(XR.value(ws.title, rows[name], rc), float), name


def test_p3_2b_the_fallback_is_decided_per_quarter_and_unknown_is_never_0():
    """The key's builder (the one its Trajectory row re-runs k quarters back) takes the fallback only in a window whose
    emissions are a MEASURED 0; a quarter with emissions writes the plain key; a missing value is unknown, never 0."""
    import build_workbook as bw
    R = bw.Refs(100, 10, ["2026-09"])
    maple = config.PROJECT_BY_NAME["Maple"]
    dbk = {"Maple|emissions_tokens": {"q0": 0.0, "q1": 2_500_000.0, "q2": None}}
    _head, build, *_rest, meta = bw._retire_vs_emissions_col(R, dbk)
    assert "|unlock_release_tokens" in build(5, maple)
    with bw.shifted(1):
        assert "|unlock_release_tokens" not in build(5, maple)        # emissions in Q-1: retired ÷ emissions
    with bw.shifted(2):
        assert "|unlock_release_tokens" not in build(5, maple)        # unknown in Q-2: never read as 0
    assert "FALLBACK KEY" in meta["amber_fn"](maple)
    assert bw._retire_vs_emissions_col(R, {"Maple|emissions_tokens": {"q0": None}})[-1]["amber_fn"](maple) is None
    # 0 by declaration (Ether.fi: ETHFI is not emitted) — retired ÷ unlocks, every quarter
    efi = config.PROJECT_BY_NAME["Ether.fi"]
    assert "NOT EMITTED" in bw.a3_emissions_declared_zero("Ether.fi")
    f = bw._retire_vs_emissions_col(R, {})[1](5, efi)
    assert "|unlock_release_tokens" in f and "|emissions_tokens" not in f
    # no unlock set (Sky) or a declared rate in force: the plain key, as before
    assert bw._a3_fallback_applies(config.PROJECT_BY_NAME["Sky"], {"Sky|emissions_tokens": {"q0": 0.0}}, "q0") is None


def test_p3_2b_key_equals_its_trajectory_latest_quarter(built):
    """The A3 key cell and the key's own Trajectory row (a3_retire_vs_emissions, the same builder) agree in Q0, on the
    audit file and on the reader, for the fallback projects too."""
    import build_workbook as bw
    a, XA = built["audit"], built["XA"]
    t = a["Trajectory"]
    q0 = trajectory.COL["Q0"]
    for name in ("Maple", "Ether.fi", "Sky", "Pendle"):
        key = _audit_cell(a, A3, bw.RETIRE_VS_EMISSIONS_HEAD, name)
        row = next(r for r in range(5, t.max_row + 1)
                   if t.cell(r, 2).value == f"A3|{name}|{bw.A3_KEY_TRAJECTORY_ID}")
        assert XA.value("Trajectory", row, q0) == XA.value(A3, key.row, key.column), name
    ws, XR = built["reader"][reader.A3_TAB], built["XR"]
    rows = _reader_rows(ws)
    kc = _reader_col(ws, lambda h: h == "Retired ÷ emissions (x)")
    lc = _reader_col(ws, lambda h: h == "Retired ÷ emissions (x), latest")
    for name in ("Maple", "Ether.fi"):
        assert XR.value(ws.title, rows[name], lc) == XR.value(ws.title, rows[name], kc), name


def test_p3_2b_a_tie_on_the_key_is_broken_by_net_holder_yield(monkeypatch):
    """Synthetic: the shared helper (Summary and every tab rank through it) breaks a tie by net holder yield, higher
    first; a project with none after those with one; untied keys rank as before."""
    monkeypatch.setattr(reader, "parked_reason", lambda n: "")
    keys = {"A": (3, 0.0, None, ""), "B": (3, 0.0, None, ""), "C": (3, 0.0, None, ""), "D": (3, 1.5, None, ""),
            "E": (3, None, None, "x"), "F": (1, 0.0, None, "")}
    tb = {"A": -0.02, "B": 0.01, "C": None, "D": -0.5, "F": 9.0}
    assert reader._arch_ranking(keys, 3, tb) == [("D", 1, 1.5), ("B", 2, 0.0), ("A", 3, 0.0), ("C", 4, 0.0),
                                                 ("E", None, None)]
    # the default tie-break is this build's net holder yields (_TIEBREAK), the one table Summary and the tabs read
    monkeypatch.setattr(reader, "_TIEBREAK", {"A": 0.3, "B": 0.1})
    assert [n for n, _r, _v in reader._arch_ranking(keys, 3)] == ["D", "A", "B", "C", "E"]
    note = reader._tie_note("B", reader._arch_ranking(keys, 3))
    assert "Tied on the key" in note and "net holder yield" in note and "A +30.00%" in note
    assert reader._tie_note("D", reader._arch_ranking(keys, 3)) == ""


def test_p3_2b_fixture_ties_follow_net_holder_yield_on_summary_and_the_tab(built):
    r, XR = built["reader"], built["XR"]
    s, ws = r["Summary"], r[reader.A3_TAB]
    srows, trows = _reader_rows(s), _reader_rows(ws)
    sr, sn = _reader_col(s, lambda h: h == "Rank in archetype"), _reader_col(s, lambda h: h == "Net holder yield")
    sk = sr - 1
    tr = _reader_col(ws, lambda h: h.startswith("Rank in"))
    tied = [n for n in srows if n in trows and XR.value("Summary", srows[n], sk) == 0.0
            and isinstance(XR.value("Summary", srows[n], sr), float)]
    assert len(tied) >= 2, tied
    by_rank = sorted(tied, key=lambda n: XR.value("Summary", srows[n], sr))
    nhy = [XR.value("Summary", srows[n], sn) for n in by_rank]
    nums = [v for v in nhy if isinstance(v, float)]
    assert nums == sorted(nums, reverse=True) and nhy[:len(nums)] == nums       # numbers first, high to low
    for n in tied:
        assert XR.value("Summary", srows[n], sr) == XR.value(ws.title, trows[n], tr), n
        assert "the tie is broken by net holder yield" in s.cell(srows[n], sr).comment.text


# ============================================================================================== P3-2c
def test_p3_2c_uniswap_a4_key_is_burn_over_releases_into_circulating(built):
    a, XA = built["audit"], built["XA"]
    assert config.crossover_issuance_metric("Uniswap") == "unlock_release_tokens"
    assert config.crossover_issuance_metric("GEODNET") == config.issuance_basis("GEODNET") == "pool_release_tokens"
    c = _audit_cell(a, A4, "BURN ÷ ISSUANCE (x)", "Uniswap")
    f = str(c.value)
    assert "|unlock_release_tokens" in f and "BLOCKED — issuance declared zero" in f and "no issuance" not in f
    v = XA.value(A4, c.row, c.column)
    bm = config.a4_burn_metric("Uniswap")
    burn = _data_value(XA, a, f"Uniswap|{bm}", "L") / _data_value(XA, a, f"Uniswap|{bm}", "X")
    rel = (_data_value(XA, a, "Uniswap|unlock_release_tokens", "L")
           / _data_value(XA, a, "Uniswap|unlock_release_tokens", "X"))
    assert isinstance(v, float) and v == pytest.approx(burn / rel, rel=1e-12)
    basis = _audit_cell(a, A4, "Issuance basis for the crossover", "Uniswap").value
    assert basis.startswith("unlock_release_tokens — supply pre-minted: releases into circulating")
    # Sky (also declared zero, not in A4_RELEASE_ISSUANCE) still reads burn-only
    assert "no issuance — burn only" in str(_audit_cell(a, A4, "BURN ÷ ISSUANCE (x)", "Sky").value)
    # the reader ranks it, Summary and the A4 tab alike
    r, XR = built["reader"], built["XR"]
    ws, s = r[reader.A4_TAB], r["Summary"]
    tr, srows = _reader_rows(ws), _reader_rows(s)
    kc = _reader_col(ws, lambda h: h == "Burn ÷ issuance (x)")
    assert XR.value(ws.title, tr["Uniswap"], kc) == pytest.approx(v, rel=1e-12)
    rk = XR.value(ws.title, tr["Uniswap"], _reader_col(ws, lambda h: h.startswith("Rank in")))
    assert isinstance(rk, float) and rk == XR.value("Summary", srows["Uniswap"],
                                                    _reader_col(s, lambda h: h == "Rank in archetype"))


# ============================================================================================== P3-2d
def test_p3_2d_chainlink_primary_a2_ranked_there_secondary_on_a1_and_a3(built):
    assert config.PROJECT_BY_NAME["Chainlink"]["archetypes"] == [2, 1, 3]
    r, XR = built["reader"], built["XR"]
    s = r["Summary"]
    prim = {}
    for label, lo, hi in reader._summary_sections(s):
        for rr in range(lo, hi + 1):
            prim[s.cell(rr, 1).value] = int(label[1])
    assert prim["Chainlink"] == 2
    srows = _reader_rows(s)
    srank = XR.value("Summary", srows["Chainlink"], _reader_col(s, lambda h: h == "Rank in archetype"))
    a2 = r[reader.A2_TAB]
    a2rows = _reader_rows(a2)
    a2rank = XR.value(a2.title, a2rows["Chainlink"], _reader_col(a2, lambda h: h.startswith("Rank in")))
    assert isinstance(srank, float) and srank == a2rank
    # ranked on A2's key: its key cell is the A2 key and its rank follows it among A2's primaries
    kc = _reader_col(a2, lambda h: h == "Customer revenue ÷ emissions (x)")
    rc = _reader_col(a2, lambda h: h.startswith("Rank in"))
    ranked = sorted(((XR.value(a2.title, rr, rc), XR.value(a2.title, rr, kc), n) for n, rr in a2rows.items()
                     if isinstance(XR.value(a2.title, rr, rc), float)))
    assert [k for _r, k, _n in ranked] == sorted([k for _r, k, _n in ranked], reverse=True)
    assert "Chainlink" in [n for *_x, n in ranked]
    for tab in (reader.A1_TAB, reader.A3_TAB):
        ws = r[tab]
        c = ws.cell(_reader_rows(ws)["Chainlink"], _reader_col(ws, lambda h: h.startswith("Rank in")))
        assert c.value == "secondary", tab
        assert c.comment.text.startswith("Primary archetype: A2 Coordination — ranked there"), tab


# ============================================================================================== P3-2e
def test_p3_2e_own_breaks_treats_a_bracketed_declared_handover_as_declared(built, monkeypatch):
    g = built["groups"]
    since = pd.Timestamp("2025-01-01")
    eth = trajectory._own_breaks("Ethereum", "total_supply_protocol", g, since)
    assert not [b for b in eth if b[0] == pd.Timestamp("2026-09-29")], eth
    assert not [b for b in trajectory.basis_breaks("Ethereum", reader.A1_KEY_METRICS, g, since)
                if b[0] == pd.Timestamp("2026-09-29")]
    # the stored bases on either side are the declared points, normalised (the bracket stripped)
    decl = config.declared_handover("Ethereum", "total_supply_protocol")
    assert any("[" in x for x in decl["ordered_points"])
    # synthetic: a bracketed declared point is declared; an undeclared change still breaks
    df = pd.DataFrame({"date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
                       "source": ["a:x[next-day 00:00Z]", "b:y", "c:z"], "value": [1.0, 1.0, 1.0]})
    monkeypatch.setattr(config, "declared_handover", lambda p, m: {"ordered_points": ("a:x[next-day 00:00Z]", "b:y"),
                                                                    "composition_change": "none"})
    out = trajectory._own_breaks("X", "some_series", {("X", "some_series"): df}, since)
    assert [(b[0], b[2], b[3]) for b in out] == [(pd.Timestamp("2026-01-03"), "b:y", "c:z")]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
