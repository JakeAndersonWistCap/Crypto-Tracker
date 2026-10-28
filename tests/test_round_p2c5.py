"""ROUND P2c-5 (Jake 2026-10-28): THREE WALLET-SET HISTORIES RESTATED ON THE CURRENT SET. No network.

Jake's live guard (2026-10-27) found two or more wallet-set bases, undeclared, in Ether.fi noncirculating_holding_tokens
(8 wallets to 2026-10-13, 10 from 10-14, 12 from 10-26) and in Maple's (8 -> 9) and Pendle's (3 -> 6) insider_stake_shares
(from 10-26). Every change was an addition and every series is a balanceOf sum at a block (archivable), so each is
restated by the P2b-2 route — a once-only declared clean-up, then the archive re-reads each day at its first block on the
whole current set; a day not re-read is a named hole; while earlier-set rows remain the seam(s) are declared for that
build and the days named NOT YET RESTATED. No series is declared a break instead (a restatement is possible for all).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402

EF, MP, PD = "Ether.fi", "Maple", "Pendle"
NC, IS = "noncirculating_holding_tokens", "insider_stake_shares"
SERIES = {(EF, NC): "P2c-5a", (MP, IS): "P2c-5b", (PD, IS): "P2c-5c"}

# the wallet sets as they were (config at 359cc1f, b2578e1, 4555346 — read 2026-10-28)
_EF8 = ("custodian_origin_1", "custodian_origin_2", "treasury_1", "treasury_2", "treasury_3", "operating_topup_safe",
        "buyback_safe_old", "treasury_reserve_safe")
_EF10 = _EF8 + ("treasury_docs_3", "treasury_docs_4")
_EF12 = _EF10 + ("foundation_q18_1", "foundation_q18_2")
_MP8 = ("treasury_registry", "migrator", "syrup_drip", "recapitalization_module", "governor_timelock",
        "operational_admin", "security_admin", "ssf_safe")
_PD3 = ("ecosystem_fund", "governance_multisig", "team_multisig")


def _pt(keys, prefix):
    return "chain:sum(" + "+".join(f"ethereum:{prefix}{k}" for k in keys) + ")"


P = {"ef8": _pt(_EF8, "noncirc_"), "ef10": _pt(_EF10, "noncirc_"), "ef12": _pt(_EF12, "noncirc_"),
     "mp8": _pt(_MP8, "insider_stake_noncirc_"),
     "mp9": _pt(_MP8, "insider_stake_noncirc_")[:-1] + "+ethereum:insider_stake_dao_multisig)",
     "pd3": _pt(_PD3, "insider_stake_noncirc_"),
     "pd6": _pt(_PD3, "insider_stake_noncirc_")[:-1] + "+ethereum:insider_stake_treasury+ethereum:insider_stake_dev_"
                                                      "multisig+ethereum:insider_stake_timelock)"}


def _g(proj, metric, days, values, sources):
    if isinstance(sources, str):
        sources = [sources] * len(days)
    if not isinstance(values, (list, tuple)):
        values = [values] * len(days)
    return pd.DataFrame({"date": pd.to_datetime(days), "project": proj, "metric": metric,
                         "value": [float(v) for v in values], "tier": 2, "source": sources})


@pytest.fixture(autouse=True)
def _clean_active():
    config.RESTATEMENT_ACTIVE.clear()
    yield
    config.RESTATEMENT_ACTIVE.clear()


# ============================================================================================ the declarations
def test_each_series_is_declared_on_its_current_set_and_the_sets_only_grew():
    from fetch import archive as ar
    cur = {(EF, NC): P["ef12"], (MP, IS): P["mp9"], (PD, IS): P["pd6"]}
    old = {(EF, NC): (P["ef8"], P["ef10"]), (MP, IS): (P["mp8"],), (PD, IS): (P["pd3"],)}
    for (proj, m), rid in SERIES.items():
        r = config.restatement(proj, m)
        assert r and r["id"] == rid and r["before"] == "2026-10-26"
        assert config._sum_set(r["point"]) == config._sum_set(cur[(proj, m)]), (proj, m)
        # ADDITIONS ONLY: every earlier set is inside the current one (the nested_sums growth logic covers it)
        for o in old[(proj, m)]:
            assert config._sum_set(o) < config._sum_set(r["point"])
        assert r["pending_handover"]["nested_sums"] and "none removed" in r["pending_handover"]["composition_change"]
        assert r["refill"] == f"python archive_backfill.py --run --project {proj}"
        assert config.declared_handover(proj, m) is None, "no static handover: a restated store is one basis"
        # RESTATABLE: every component is a state read at a past block (no event-log read, no run-time argument)
        ok, why = ar.archivable(config.PROJECT_BY_NAME[proj], m, {"ethereum"}, set())
        assert ok, (proj, m, why)
        c = [c for c in config.RUN_CLEANUPS if c["id"] == rid]
        assert len(c) == 1 and c[0]["once"] and c[0]["superseded_by"] == "restatement"
        assert c[0]["projects"] == (proj,) and c[0]["metric"] == m
    assert config.restatement_seams(EF, NC) == ("2026-10-14", "2026-10-26")
    assert config.restatement(PD, IS)["series_from"] == "2026-01-16"
    # Ether.fi's insider series keeps its own static declaration; it is not restated here
    assert config.restatement(EF, IS) is None and config.declared_handover(EF, IS)["nested_sums"]


def test_which_rows_are_superseded():
    rev = "chain:sum(" + "+".join(sorted(config._sum_set(P["ef12"]), reverse=True)) + ")"
    assert config.restatement_superseded(EF, NC, "2026-10-13", P["ef8"] + ":archive")
    assert config.restatement_superseded(EF, NC, "2026-10-20", P["ef10"])
    assert config.restatement_superseded(EF, NC, "2026-10-20", P["ef10"] + ":PARTIAL")
    assert not config.restatement_superseded(EF, NC, "2026-10-20", rev + ":archive")
    assert config.restatement_superseded(EF, NC, "2026-10-26", P["ef10"])     # P2d-1: another set, whatever its date
    assert config.restatement_superseded(MP, IS, "2026-10-25", P["mp8"])
    assert not config.restatement_superseded(MP, IS, "2026-10-25", P["mp9"] + ":archive")
    assert config.restatement_superseded(PD, IS, "2026-03-01", P["pd3"] + ":archive")
    assert not config.restatement_superseded(PD, IS, "2026-03-01", P["pd6"] + ":archive")
    assert not config.restatement_superseded(EF, IS, "2026-10-20", P["ef8"])     # not declared


# ============================================================================================ the clean-up
def test_the_cleanups_remove_every_superseded_row_once_and_log_each(tmp_path, monkeypatch):
    import store as sm
    import token_metrics as tm
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(config, "RUN_CLEANUPS", tuple(c for c in config.RUN_CLEANUPS if c["id"] in SERIES.values()))
    st = sm.Store(tmp_path / "m.db")
    st.upsert(pd.concat([
        _g(EF, NC, ["2026-10-12", "2026-10-13"], 187_839_959.47, P["ef8"] + ":archive"),
        _g(EF, NC, ["2026-10-14", "2026-10-15"], 188_587_000.0, P["ef10"]),
        _g(EF, NC, ["2026-10-16"], 188_000_000.0, P["ef10"] + ":PARTIAL"),
        _g(EF, NC, ["2026-10-11"], 188_587_000.0, P["ef12"] + ":archive"),              # already restated: kept
        _g(EF, NC, ["2026-10-26", "2026-10-27"], 188_587_000.0, P["ef12"]),             # the current set: kept
        _g(MP, IS, ["2026-10-24", "2026-10-25"], 0.0, P["mp8"] + ":archive"),
        _g(MP, IS, ["2026-10-26"], 0.0, P["mp9"]),
        _g(PD, IS, ["2026-10-25"], 0.0, P["pd3"]),
        _g(PD, IS, ["2026-10-26"], 0.0, P["pd6"]),
        _g(EF, IS, ["2026-10-20"], 0.0, P["ef10"].replace("noncirc_", "insider_stake_noncirc_")),   # untouched
    ]))
    lines = tm.apply_run_cleanups(st, "run1", logging.getLogger("t"))

    def left(proj, m):
        return st.conn.execute("SELECT substr(date,1,10) FROM metrics WHERE project=? AND metric=? ORDER BY date",
                               (proj, m)).fetchall()
    assert [d for (d,) in left(EF, NC)] == ["2026-10-11", "2026-10-26", "2026-10-27"]
    assert [d for (d,) in left(MP, IS)] == ["2026-10-26"]
    assert [d for (d,) in left(PD, IS)] == ["2026-10-26"]
    assert [d for (d,) in left(EF, IS)] == ["2026-10-20"]
    heads = [x for x in lines if "applied once" in x]
    assert len(heads) == 3
    assert any(x.startswith("CLEAN-UP P2c-5a: applied once — 5 row(s) of Ether.fi / noncirculating_holding_tokens")
               for x in heads)
    assert any(x.startswith("CLEAN-UP P2c-5b: applied once — 2 row(s) of Maple / insider_stake_shares") for x in heads)
    assert any(x.startswith("CLEAN-UP P2c-5c: applied once — 1 row(s) of Pendle / insider_stake_shares") for x in heads)
    removed = [x for x in lines if ": removed " in x]
    assert len(removed) == 8
    for d in ("2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"):
        assert any(f"CLEAN-UP P2c-5a: removed Ether.fi / noncirculating_holding_tokens {d} from " in x for x in removed)
    assert any("from chain:sum(8 components):archive (187,839,959.47)" in x for x in removed)
    assert any("from chain:sum(10 components):PARTIAL" in x for x in removed)
    assert any("CLEAN-UP P2c-5c: removed Pendle / insider_stake_shares 2026-10-25" in x for x in removed)
    for rid, n in (("P2c-5a", 5), ("P2c-5b", 2), ("P2c-5c", 1)):
        assert st.conn.execute("SELECT COUNT(*) FROM run_log WHERE source=?", (f"cleanup:{rid}",)).fetchone()[0] == n
    # ONCE: a later superseded row is not removed again (the build names it instead)
    st.upsert(_g(MP, IS, ["2026-10-20"], 0.0, P["mp8"]))
    assert tm.apply_run_cleanups(st, "run2", logging.getLogger("t")) == []
    assert len(left(MP, IS)) == 2


# ============================================================================================ the archive refill
def _archive(tmp_path, monkeypatch, proj, metric, seed, unreadable=None, days=5, asof="2026-10-27"):
    """The archive route on a fake chain: one block an hour; every wallet holds 1 (token or share), except on the
    `unreadable` (day, contract key) where that wallet's balanceOf fails."""
    import store as sm
    from fetch import archive as ar
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    asof = pd.Timestamp(asof)
    monkeypatch.setattr(ar, "today", lambda: asof)
    t0 = int((asof - pd.Timedelta(days=40)).tz_localize("UTC").timestamp())
    src = config.PROJECT_BY_NAME[proj]
    bad = (pd.Timestamp(unreadable[0]), src["contracts"][unreadable[1]]["address"].lower()) if unreadable else None

    class Eth:
        block_number = 40 * 24 + 10

        def get_block(self, n):
            return {"timestamp": t0 + n * 3600}

        def get_balance(self, a, block_identifier=None):
            return 0

        def get_code(self, a, block_identifier=None):
            return b"\x60"

    class Reader:
        def __init__(self):
            self.at_block = {}

        def web3(self, chain):
            return type("W", (), {"eth": Eth()})()

        def checksum(self, a):
            return a

        def symbol_matches(self, chain, address, expected):
            return True, expected

        def has_code(self, chain, a):
            return True

        def scaled(self, chain, address, call, *args, **kw):
            if not args:
                return 1e9
            day = pd.Timestamp(t0 + self.at_block[chain] * 3600, unit="s").normalize()
            if bad is not None and day == bad[0] and str(args[0]).lower() == bad[1]:
                raise RuntimeError("execution reverted")
            return 1.0

    serving = {k for k, v in src["contracts"].items() if v.get("metric_override") == metric and v.get("chain") == "ethereum"}
    tokens = {"token"} | {str(src["contracts"][k].get("underlying")) for k in serving}
    p = dict(src)
    p["contracts"] = {k: v for k, v in src["contracts"].items() if k in serving or k in tokens}
    monkeypatch.setitem(config.PROJECT_BY_NAME, proj, p)
    st = sm.Store(tmp_path / "a.db")
    st.upsert(seed)
    res = ar.ArchiveBackfill(st, [p], budget_s=120, reader=Reader(), days=days).run({"ethereum"})
    rows = st.conn.execute("SELECT substr(date,1,10), value, source FROM metrics WHERE project=? AND metric=? "
                           "ORDER BY date", (proj, metric)).fetchall()
    return res, rows, len(serving)


@pytest.mark.parametrize("proj,metric,cur,missing_key", [
    (EF, NC, "ef12", "noncirc_foundation_q18_2"),
    (MP, IS, "mp9", "insider_stake_dao_multisig"),
    (PD, IS, "pd6", "insider_stake_timelock"),
])
def test_the_archive_restates_each_day_on_the_current_set_and_a_missing_wallet_day_is_a_hole(
        tmp_path, monkeypatch, proj, metric, cur, missing_key):
    import build_workbook as bw
    seed = _g(proj, metric, ["2026-10-26"], 12.0, P[cur])
    res, rows, n = _archive(tmp_path, monkeypatch, proj, metric, seed, unreadable=("2026-10-24", missing_key))
    got = {d: (v, s) for d, v, s in rows}
    # 10-22, 10-23, 10-25 re-read on the WHOLE current set at their first block; 10-24 (one added wallet unreadable) is
    # NOT written — no earlier-set sum, no 0 for the wallet
    assert sorted(got) == ["2026-10-22", "2026-10-23", "2026-10-25", "2026-10-26"], sorted(got)
    for d in ("2026-10-22", "2026-10-23", "2026-10-25"):
        v, s = got[d]
        assert v == float(n) and s.endswith(":archive"), (d, v, s)
        assert config._sum_set(s.split(":archive")[0]) == config._sum_set(P[cur])
        assert not config.restatement_superseded(proj, metric, d, s)
    assert res["written"][(proj, metric)] == 3
    assert any(x.startswith(f"NOT WRITTEN {proj}/{metric}: 1 day(s)") for x in res["report"])
    g = _g(proj, metric, list(got), [v for v, _ in got.values()], [s for _, s in got.values()])
    bw._series_restatement_views({(proj, metric): g}, pd.Timestamp("2026-10-27"))
    txt = bw.RESTATEMENT_STATUS[(proj, metric)]
    assert txt.startswith(f"RESTATEMENT {SERIES[(proj, metric)]} INCOMPLETE") and "2026-10-24" in txt
    assert "HOLES" in txt and "never 0" in txt


def test_the_archive_never_writes_the_current_set_beside_a_stored_earlier_set_day(tmp_path, monkeypatch):
    seed = pd.concat([_g(MP, IS, ["2026-10-22", "2026-10-23", "2026-10-25"], 0.0, P["mp8"]),
                      _g(MP, IS, ["2026-10-26"], 0.0, P["mp9"])])
    res, rows, _n = _archive(tmp_path, monkeypatch, MP, IS, seed)
    assert {d for d, _, _ in rows} == {"2026-10-22", "2026-10-23", "2026-10-25", "2026-10-26"}
    assert "was read from" in res["refused"][(MP, IS)]


# ============================================================================================ the build
def _span(asof="2026-10-27", start=None):
    s = pd.Timestamp(asof) - pd.Timedelta(days=int(config.TRAJECTORY["history_days"]))
    return list(pd.date_range(max(s, pd.Timestamp(start)) if start else s, "2026-10-25", freq="D"))


@pytest.mark.parametrize("proj,metric,cur", [(EF, NC, "ef12"), (MP, IS, "mp9"), (PD, IS, "pd6")])
def test_a_restated_store_is_one_basis_with_no_break_and_no_note(proj, metric, cur):
    import build_workbook as bw
    import trajectory
    days = _span(start=config.restatement(proj, metric).get("series_from"))
    g = pd.concat([_g(proj, metric, days, 1.0, P[cur] + ":archive"),
                   _g(proj, metric, ["2026-10-26", "2026-10-27"], 1.0, P[cur])])
    groups = {(proj, metric): g}
    bw._series_restatement_views(groups, pd.Timestamp("2026-10-27"))
    assert (proj, metric) not in config.RESTATEMENT_ACTIVE and config.declared_handover(proj, metric) is None
    assert (proj, metric) not in bw.RESTATEMENT_STATUS
    assert trajectory.basis_breaks(proj, [metric], groups, pd.Timestamp("2025-01-01")) == []
    mps, _ = bw.method_points(g.sort_values("date"))
    assert len(set(mps)) == 1


def test_pendle_days_before_spendle_existed_are_not_holes():
    import build_workbook as bw
    days = _span(start="2026-01-16")
    g = pd.concat([_g(PD, IS, [d for d in days if d != pd.Timestamp("2026-03-03")], 0.0, P["pd6"] + ":archive"),
                   _g(PD, IS, ["2026-10-26"], 0.0, P["pd6"])])
    bw._series_restatement_views({(PD, IS): g}, pd.Timestamp("2026-10-27"))
    txt = bw.RESTATEMENT_STATUS[(PD, IS)]
    assert "1 day(s) of 2026-01-16..2026-10-25" in txt and "2026-03-03" in txt


def test_ether_fi_unrestated_store_keeps_both_seams_and_names_the_earlier_set_days():
    import build_workbook as bw
    import measuring_guard as mg
    import trajectory
    g = pd.concat([_g(EF, NC, ["2026-10-12", "2026-10-13"], 187_839_959.47, P["ef8"] + ":archive"),
                   _g(EF, NC, ["2026-10-14", "2026-10-25"], 188_587_000.0, P["ef10"]),
                   _g(EF, NC, ["2026-10-26", "2026-10-27"], 188_587_000.0, P["ef12"])])
    groups = {(EF, NC): g}
    bw._series_restatement_views(groups, pd.Timestamp("2026-10-27"))
    decl = config.declared_handover(EF, NC)
    assert decl and decl["nested_sums"], "the pending seam is declared for this build"
    txt = bw.RESTATEMENT_STATUS[(EF, NC)]
    assert txt.startswith("NOT YET RESTATED (P2c-5a): 4 day(s)") and "2026-10-12..2026-10-14, 2026-10-25" in txt
    assert "SERIES BREAK 2026-10-14, 2026-10-26" in txt and "archive_backfill.py --run --project Ether.fi" in txt
    mps, _ = bw.method_points(g.sort_values("date"))
    row = {"source": g["source"].iloc[-1], "measuring_points": tuple(sorted(set(mps))),
           "point_spans": {mp: (gg["date"].min(), gg["date"].max()) for mp, gg in g.groupby(mps)}}
    assert bw.withheld_for(EF, NC, row) is None, "not blanked: a declared growth"
    brk = trajectory.basis_breaks(EF, [NC], groups, pd.Timestamp("2026-01-01"))
    assert [b[0] for b in brk] == [pd.Timestamp("2026-10-14"), pd.Timestamp("2026-10-26")]
    assert mg._nested_growth(EF, NC, set(mps))
    # the next build of a restated store drops the declaration; the guard still reads it in any process
    bw._series_restatement_views({(EF, NC): g[g["date"] >= "2026-10-26"]}, pd.Timestamp("2026-10-27"))
    assert config.declared_handover(EF, NC) is None
    assert mg._nested_growth(EF, NC, {P["ef8"], P["ef10"], P["ef12"]})


@pytest.mark.parametrize("proj,old,cur", [(MP, "mp8", "mp9"), (PD, "pd3", "pd6")])
def test_insider_unrestated_store_keeps_its_seam(proj, old, cur):
    import build_workbook as bw
    import measuring_guard as mg
    import trajectory
    g = pd.concat([_g(proj, IS, ["2026-10-23", "2026-10-24", "2026-10-25"], 0.0, P[old]),
                   _g(proj, IS, ["2026-10-26", "2026-10-27"], 0.0, P[cur])])
    groups = {(proj, IS): g}
    bw._series_restatement_views(groups, pd.Timestamp("2026-10-27"))
    txt = bw.RESTATEMENT_STATUS[(proj, IS)]
    assert txt.startswith(f"NOT YET RESTATED ({SERIES[(proj, IS)]}): 3 day(s)") and "SERIES BREAK 2026-10-26" in txt
    assert [b[0] for b in trajectory.basis_breaks(proj, [IS], groups, pd.Timestamp("2026-01-01"))] == \
        [pd.Timestamp("2026-10-26")]
    mps, _ = bw.method_points(g.sort_values("date"))
    assert mg._nested_growth(proj, IS, set(mps))
    # the guard's live check: the two sets beside each other are a declared growth, not a BASIS violation
    assert not (set(mps) - mg._declared(proj, IS)) or mg._nested_growth(proj, IS, set(mps))


def test_jakes_10_13_ether_fi_dump_is_an_unrestated_store_and_says_so():
    import build_workbook as bw
    d = json.loads((ROOT / "fixtures" / "real_etherfi.json").read_text())
    rows = [r for r in d["metrics"] if r["metric"] == NC]
    g = _g(EF, NC, [r["date"] for r in rows], [r["value"] for r in rows], [r["source"] for r in rows])
    bw._series_restatement_views({(EF, NC): g}, pd.Timestamp(d["meta"]["asof"]))
    txt = bw.RESTATEMENT_STATUS[(EF, NC)]
    assert txt.startswith(f"NOT YET RESTATED (P2c-5a): {len(rows)} day(s)") and "2025-09-08..2026-10-13" in txt


def test_no_p2c5_series_is_declared_a_break_instead():
    """A restatement is possible for all three (additions only, state reads): none is declared a SERIES BREAK in its
    place — no static series_handover, no source reclassification, no 'not archivable' declaration."""
    for (proj, m) in SERIES:
        assert not ((config.PROJECT_BY_NAME[proj].get("series_handover") or {}).get(m))
        assert not config.archive_not_by_design(proj, m)


# ============================================================================================ the status check
def test_the_status_check_reports_each_series(tmp_path, monkeypatch, capsys):
    import check_offline_items as coi
    import store as sm
    import token_metrics as tm
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    today0 = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    start = today0 - pd.Timedelta(days=int(config.TRAJECTORY["history_days"]))
    span = list(pd.date_range(start, "2026-10-25", freq="D"))
    pspan = [d for d in span if d >= pd.Timestamp("2026-01-16")]
    st = sm.Store(tmp_path / "s.db")
    st.upsert(pd.concat([_g(EF, NC, span, 1.0, P["ef8"] + ":archive"), _g(EF, NC, ["2026-10-26"], 1.0, P["ef12"]),
                         _g(MP, IS, span, 0.0, P["mp8"] + ":archive"), _g(MP, IS, ["2026-10-26"], 0.0, P["mp9"]),
                         _g(PD, IS, pspan, 0.0, P["pd3"] + ":archive"), _g(PD, IS, ["2026-10-26"], 0.0, P["pd6"])]))
    assert coi.series_restatements(str(tmp_path / "s.db")) == 0
    out = capsys.readouterr().out
    for rid in ("P2c-5a", "P2c-5b", "P2c-5c"):
        assert f"CLEAN-UP {rid} NOT RUN" in out
    assert out.count(f"-> NOT YET RESTATED: {len(span)} day(s)") == 2
    assert f"-> NOT YET RESTATED: {len(pspan)} day(s)" in out
    assert "set grew 2026-10-14, 2026-10-26" in out
    monkeypatch.setattr(config, "RUN_CLEANUPS", tuple(c for c in config.RUN_CLEANUPS if c["id"] in SERIES.values()))
    tm.apply_run_cleanups(st, "r", logging.getLogger("t"))
    st.upsert(pd.concat([_g(EF, NC, span[-10:], 1.0, P["ef12"] + ":archive"),
                         _g(MP, IS, span, 0.0, P["mp9"] + ":archive"),
                         _g(PD, IS, pspan, 0.0, P["pd6"] + ":archive")]))
    coi.series_restatements(str(tmp_path / "s.db"))
    out = capsys.readouterr().out
    assert f"-> RESTATEMENT INCOMPLETE: {len(span) - 10} day(s) before 2026-10-26 hold no 12-wallet row" in out
    assert out.count("-> RESTATED: every day") == 2 and "CLEAN-UP P2c-5b RUN" in out
    # a store whose earlier set ran past the declared first day says so
    st.upsert(_g(MP, IS, ["2026-10-27"], 0.0, P["mp8"]))
    coi.series_restatements(str(tmp_path / "s.db"))
    out = capsys.readouterr().out                       # P2d-2: it blocks the archive — the check says so and why
    assert "!! 2026-10-27 is stored on the 8-wallet set" in out
    assert "-> BLOCKED: 1 row(s) on another set on or after 2026-10-26 (2026-10-27)" in out and "CLEAN-UP P2c-5b2" in out
