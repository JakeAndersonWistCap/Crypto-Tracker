"""ROUND P5-1C.A3 (Jake, 2026-11-04): COINGECKO'S FDV, MAX AND TOTAL SUPPLY HOLES, REBUILT WHERE A CONTRACT EXISTS.

"CoinGecko FDV, max and total supply are marked 'not recoverable' (495 holes). Total supply can be read on-chain at
past blocks, and FDV = price x max supply: rebuild them that way where a contract exists."

  - total: the declared same-quantity formula over the STORED on-chain legs of the day (each a contract / NEAR header
    read at a past block — the archive route fills a missed leg day first);
  - max: a constant the contract or the project's own source fixes (never invented, never CoinGecko's own figure);
  - FDV: the price closing the day x CoinGecko's basis (max where declared, else the total formula);
  - each a DIFFERENT measuring point (supply_rebuild:<what>), written only on an owed day, only while the declared check
    against CoinGecko's own stored days passes (config.SUPPLY_REBUILD_TOL, stated before any overlap was measured);
  - the build re-runs the check and drops the rebuilt days when it fails; the headline FDV never takes a rebuilt
    CoinGecko FDV (its basis is CoinGecko's, not the workbook's price x total).
No network: every read here is the store.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402

TODAY = pd.Timestamp("2026-11-04")
Y = TODAY - pd.Timedelta(days=1)


def _days(n, end=Y):
    return [end - pd.Timedelta(days=i) for i in range(n - 1, -1, -1)]


def _rows(project, metric, days, value, source, tier=1):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": project, "metric": metric,
                         "value": [float(value(d)) if callable(value) else float(value) for d in days], "tier": tier,
                         "source": source})


@pytest.fixture
def st(tmp_path, monkeypatch):
    import store as sm
    from fetch import archive as ar
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(ar, "today", lambda: TODAY)
    s = sm.Store(tmp_path / "metrics.db")
    yield s
    s.close()


def _val(st, p, m, d):
    r = st.conn.execute("SELECT value, source FROM metrics WHERE project=? AND metric=? AND substr(date,1,10)=?",
                        (p, m, str(pd.Timestamp(d).date()))).fetchone()
    return None if r is None else (r[0], r[1])


HOLES = [Y - pd.Timedelta(days=i) for i in (3, 4)]


def _chainlink(st, cg_total=1e9, px=lambda d: 10.0 + d.day / 10):
    """30 days: the chain total (every day), CoinGecko's total / max / FDV (two days missing), the price (every day and
    today, the closing price of yesterday)."""
    n, full = "Chainlink", _days(30)
    held = [d for d in full if d not in HOLES]
    st.upsert(pd.concat([
        _rows(n, "total_supply", full, 1e9, "chain:ethereum:token", 2),
        _rows(n, "price_usd", full + [TODAY], px, "coingecko"),
        _rows(n, "total_supply_coingecko", held, cg_total, "coingecko"),
        _rows(n, "max_supply", held, 1e9, "coingecko"),
        _rows(n, "fdv_usd", held, lambda d: px(d + pd.Timedelta(days=1)) * 1e9 * 1.001, "coingecko")]))
    return n


# ============================================================================================ declarations
def test_the_declared_rebuilds_per_project_and_the_named_not_recoverable_ones():
    m = config.supply_rebuild_metrics
    assert m("Chainlink") == {"total_supply_coingecko": "total", "max_supply": "max", "fdv_usd": "fdv"}
    # PR1.U5: Uniswap's total_supply is ours (net view); CoinGecko's total, rebuilt, is kept as total_supply_coingecko
    assert m("Uniswap") == {"total_supply_coingecko": "total"}, "UNI is mintable (2% a year): no max, so no max-basis FDV"
    assert m("Hyperliquid") == {"max_supply": "max"}, "native HYPE: tokenDetails' maxSupply only"
    assert m("Ethereum") == {}, "native ETH: no past-block read"
    assert m("Fluid") == {"total_supply_coingecko": "total"} and m("Sky") == {"total_supply_coingecko": "total"}
    assert m("Near") == {"total_supply": "total", "fdv_usd": "fdv"}
    assert config.supply_rebuild_kind("Chainlink", "fdv_usd_coingecko") == "fdv", "the build's name for CoinGecko's FDV"
    for k, why in config.SUPPLY_REBUILD_NOT_RECOVERABLE.items():
        assert why and k[1] not in m(k[0]), k
    # every declared max cites where it comes from; the FDV basis is declared for every project rebuilt
    for name, s in config.SUPPLY_REBUILD.items():
        assert s.get("max") is None or "2026-11-04" in s["max_cite"], name
        assert s.get("fdv_basis") in ("max", "total"), name
    tol = config.SUPPLY_REBUILD_TOL
    assert tol["total"]["median"] == config.FDV_HISTORY_TOL and tol["total"]["worst"] == 2 * config.FDV_HISTORY_TOL
    assert tol["fdv"]["median"] == 0.02 and tol["max"]["worst"] == 1e-9


def test_polygon_has_keyless_endpoints_that_serve_past_block_calls():
    eps = config.DEFAULT_RPC["polygon"]
    assert eps[:2] == ["https://polygon-bor-rpc.publicnode.com", "https://polygon-rpc.com"], "the existing order kept"
    assert "https://polygon.drpc.org" in eps and "https://polygon.gateway.tenderly.co" in eps


def test_routes_supply_rebuild_where_declared_else_not_recoverable_with_the_project_s_reason():
    from fetch import archive as ar
    assert ar.hole_route("Chainlink", "max_supply", "coingecko", set()) == "supply_rebuild"
    assert ar.hole_route("Chainlink", "total_supply_coingecko", "coingecko", set()) == "supply_rebuild"
    assert ar.hole_route("Uniswap", "max_supply", "coingecko", set()) == "not_recoverable"
    assert ar.hole_route("Ethereum", "total_supply", "coingecko", set()) == "not_recoverable"
    assert "mintCap() 2% a year" in ar._no_history_why("coingecko", "max_supply", "Uniswap")
    assert "Enterprise-only" in ar._no_history_why("coingecko", "max_supply", "Uniswap")
    # a chain-read total is not CoinGecko's: never this route
    assert ar.hole_route("Chainlink", "total_supply", "chain:ethereum:token", set()) != "supply_rebuild"


# ============================================================================================ the catch-up
def test_a_missed_coingecko_day_is_rebuilt_from_the_chain_the_declared_max_and_the_closing_price(st):
    from fetch import archive as ar
    from fetch.base import _measuring_point
    n = _chainlink(st)
    p = config.PROJECT_BY_NAME[n]
    scan = ar.scan_holes(st, [p], set())
    for m in ("total_supply_coingecko", "max_supply", "fdv_usd"):
        assert scan[(n, m)]["route"] == "supply_rebuild" and sorted(scan[(n, m)]["days"]) == sorted(str(d.date()) for d in
                                                                                                    HOLES)
    res = ar.supply_rebuild_catch_up(st, [p], scan)
    assert not res["unrebuildable"]
    for d in HOLES:
        v, src = _val(st, n, "total_supply_coingecko", d)
        assert v == 1e9 and _measuring_point(src) == "supply_rebuild:total_supply"
        assert "LINK.totalSupply()" in src and "P5-1C.A3" in src
        v, src = _val(st, n, "max_supply", d)
        assert v == 1e9 and _measuring_point(src) == "supply_rebuild:declared_max" and "no mint function" in src
        v, src = _val(st, n, "fdv_usd", d)
        closing = 10.0 + (d + pd.Timedelta(days=1)).day / 10        # the NEXT day's 00:00 point
        assert v == pytest.approx(closing * 1e9) and _measuring_point(src) == "supply_rebuild:price_usd_x_max_supply"
    assert _val(st, n, "fdv_usd", Y)[1] == "coingecko", "never an overwrite"
    assert res["filled"][(n, "max_supply")] == sorted((str(d.date()) for d in HOLES), reverse=True)
    assert any("CATCH-UP Chainlink/max_supply: 2 day(s) rebuilt" in t for _, t in res["lines"])


def test_a_rebuild_that_does_not_measure_coingecko_s_figure_writes_nothing_and_says_why(st):
    from fetch import archive as ar
    n = _chainlink(st, cg_total=965_350_000)                # CoinGecko's total 3.4% under the chain's (Ether.fi's case)
    p = config.PROJECT_BY_NAME[n]
    scan = ar.scan_holes(st, [p], set())
    res = ar.supply_rebuild_catch_up(st, [p], scan)
    why = res["unrebuildable"][(n, "total_supply_coingecko")]
    assert "FAILED (median > 0.50%, worst > 1.0000%)" in why and "3.5894%" in why
    assert all(_val(st, n, "total_supply_coingecko", d) is None for d in HOLES), "never written"
    assert _val(st, n, "max_supply", HOLES[0])[0] == 1e9, "the other series are judged on their own"


def test_too_few_common_days_is_not_a_pass(st):
    from fetch import archive as ar
    ok, how = ar.supply_rebuild_check("Chainlink", "total_supply_coingecko", pd.Series([1e9] * 3, index=pd.to_datetime(
        _days(3))), pd.Series([1e9] * 3, index=pd.to_datetime(_days(3))))
    assert not ok and "fewer than the 14" in how


def test_a_day_whose_input_is_not_stored_is_left_named_never_carried(st):
    from fetch import archive as ar
    n = _chainlink(st)
    st.conn.execute("DELETE FROM metrics WHERE project=? AND metric='price_usd' AND substr(date,1,10)=?",
                    (n, str((HOLES[0] + pd.Timedelta(days=1)).date())))     # the closing price of HOLES[0]
    st.conn.commit()
    p = config.PROJECT_BY_NAME[n]
    res = ar.supply_rebuild_catch_up(st, [p], ar.scan_holes(st, [p], set()))
    assert res["left"][(n, "fdv_usd")] == [str(HOLES[0].date())]
    assert _val(st, n, "fdv_usd", HOLES[0]) is None and _val(st, n, "fdv_usd", HOLES[1]) is not None
    line = next(t for _, t in res["lines"] if "Chainlink/fdv_usd" in t)
    assert "NOT REBUILT" in line and "price_usd" in line and "never 0, never carried" in line


def test_the_catch_up_counts_a_failed_check_as_not_recoverable_and_the_rest_as_filled(st):
    from fetch import archive as ar
    n = _chainlink(st, cg_total=965_350_000)

    class NoCG:
        def market_chart(self, *a):
            raise AssertionError("no CoinGecko read: nothing of market_chart's is owed")
    res = ar.catch_up(st, [config.PROJECT_BY_NAME[n]], budget_s=5, max_days=3, valuers=[], api=NoCG(), derive=False,
                      contracts={}, tiers=True)
    assert set(res["not_recoverable"]) == {(n, "total_supply_coingecko")}
    assert res["holes_filled"][(n, "max_supply")] and res["holes_filled"][(n, "fdv_usd")]
    summary = res["lines"][-1][1]
    assert "CoinGecko supply / FDV rebuilt (P5-1C.A3): 4 day(s), 0 left, 1 series failed the declared check" in summary
    assert "4 filled" in summary and "2 not recoverable" in summary
    assert any("Chainlink/total_supply_coingecko: 2 day(s) not recoverable" in t and "FAILED" in t
               for _, t in res["lines"])


# ============================================================================================ the build
def _group(st, n, m):
    return pd.read_sql_query("SELECT date, project, metric, value, source, tier FROM metrics WHERE project=? AND "
                             "metric=?", st.conn, params=(n, m)).assign(date=lambda g: pd.to_datetime(g["date"]))


def test_the_build_uses_rebuilt_days_while_the_check_passes_and_drops_them_when_off(st, monkeypatch):
    import build_workbook as bw
    from fetch import archive as ar
    n = _chainlink(st)
    p = config.PROJECT_BY_NAME[n]
    ar.supply_rebuild_catch_up(st, [p], ar.scan_holes(st, [p], set()))
    ms = ("total_supply", "price_usd", "total_supply_coingecko", "max_supply", "fdv_usd")
    groups = {(n, m): _group(st, n, m) for m in ms}
    bw._supply_rebuild_views(groups)
    assert len(groups[(n, "max_supply")]) == 30 and "rebuilt days used" in bw.SUPPLY_REBUILD_NOTE[(n, "max_supply")]
    monkeypatch.setitem(config.SUPPLY_REBUILD_ON, "on", False)
    groups = {(n, m): _group(st, n, m) for m in ms}
    bw._supply_rebuild_views(groups)
    assert len(groups[(n, "max_supply")]) == 28 and "switched off" in bw.SUPPLY_REBUILD_NOTE[(n, "max_supply")]


def test_the_build_drops_rebuilt_days_when_coingecko_s_figure_moves_away(st):
    import build_workbook as bw
    from fetch import archive as ar
    n = _chainlink(st)
    p = config.PROJECT_BY_NAME[n]
    ar.supply_rebuild_catch_up(st, [p], ar.scan_holes(st, [p], set()))
    groups = {(n, m): _group(st, n, m) for m in ("total_supply", "total_supply_coingecko")}
    g = groups[(n, "total_supply_coingecko")]
    groups[(n, "total_supply_coingecko")] = g.assign(value=g["value"].where(g["source"] != "coingecko", 9.0e8))
    bw._supply_rebuild_views(groups)
    assert len(groups[(n, "total_supply_coingecko")]) == 28
    assert "rebuilt days NOT used" in bw.SUPPLY_REBUILD_NOTE[(n, "total_supply_coingecko")]


def test_a_rebuilt_point_stands_in_for_coingecko_only_where_declared():
    import build_workbook as bw
    import trajectory
    assert bw.hole_fill_point("Chainlink", "max_supply", "supply_rebuild:declared_max") == "coingecko"
    assert bw.hole_fill_point("Uniswap", "max_supply", "supply_rebuild:declared_max") == "supply_rebuild:declared_max"
    assert bw.hole_fill_point("Chainlink", "max_supply", "chain:ethereum:token") == "chain:ethereum:token"
    assert trajectory.basis_of("total_supply", "supply_rebuild:total_supply_protocol[rebuilt]") == "coingecko"
    assert trajectory.basis_of("total_supply", "coingecko") == "coingecko"


def test_the_headline_fdv_never_falls_back_to_a_rebuilt_coingecko_fdv(st, monkeypatch):
    import build_workbook as bw
    from fetch import archive as ar
    n = _chainlink(st)
    p = config.PROJECT_BY_NAME[n]
    ar.supply_rebuild_catch_up(st, [p], ar.scan_holes(st, [p], set()))
    groups = {(n, m): _group(st, n, m) for m in ("price_usd", "total_supply", "fdv_usd")}
    # no total of ours on the hole days: CoinGecko's own FDV may stand in, its rebuilt one may not
    t = groups[(n, "total_supply")]
    groups[(n, "total_supply")] = t[t["date"] < HOLES[1] - pd.Timedelta(days=10)]
    monkeypatch.setattr(bw, "scoped_projects", lambda: [p])
    monkeypatch.setattr("credibility.now_max_age", lambda *a: 1)
    bw._fdv_views(groups)
    fdv = groups[(n, "fdv_usd")]
    days = set(pd.to_datetime(fdv["date"]).dt.normalize())
    assert not (set(HOLES) & days), "a rebuilt CoinGecko FDV (price x its max) is never the headline FDV"
    assert not fdv["source"].astype(str).str.startswith("supply_rebuild").any()
    cgv = groups[(n, "fdv_usd_coingecko")]
    assert cgv["source"].astype(str).str.startswith("supply_rebuild").sum() == 2, "the cross-check keeps them"


# ============================================================================================ the real dumps
def test_on_jake_s_10_13_dumps_chainlink_s_holes_are_rebuilt_and_ether_fi_s_total_is_refused(st, monkeypatch):
    import sqlite3

    import real_dump
    from fetch import archive as ar
    monkeypatch.setattr(ar, "today", lambda: pd.Timestamp("2026-10-13"))
    for name in ("Chainlink", "Ether.fi"):
        payload = real_dump.load(name)
        con = sqlite3.connect(st.path)
        con.executemany("INSERT OR REPLACE INTO metrics (date, project, metric, value, source, tier, fetched_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)", [(r["date"], name, r["metric"], r["value"], r["source"],
                                                          r["tier"], r["fetched_at"]) for r in payload["metrics"]])
        con.commit()
        con.close()
    ps = [config.PROJECT_BY_NAME[x] for x in ("Chainlink", "Ether.fi")]
    res = ar.supply_rebuild_catch_up(st, ps, ar.scan_holes(st, ps, set()))
    holes = ["2026-09-13", "2026-09-19", "2026-09-20", "2026-09-26", "2026-09-27", "2026-10-02", "2026-10-03",
             "2026-10-04"]
    for m in ("total_supply_coingecko", "max_supply", "fdv_usd"):
        assert sorted(res["filled"][("Chainlink", m)]) == holes, m
    assert _val(st, "Chainlink", "total_supply_coingecko", "2026-09-13")[0] == 1e9
    assert sorted(res["filled"][("Ether.fi", "max_supply")]) == holes
    assert "median |diff| 3.4377%" in res["unrebuildable"][("Ether.fi", "total_supply_coingecko")]
    assert ("Ether.fi", "fdv_usd") in res["unrebuildable"], "its FDV is on CoinGecko's total, which is not the chain's"
