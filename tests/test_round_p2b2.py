"""ROUND P2b-2 (Jake 2026-10-27): CHAINLINK'S NON-CIRCULATING HISTORY RESTATED ON THE 33-WALLET SET. No network.

The route (the CK precedent, 24 -> 27 on 2026-10-12): CLEAN-UP P2b-2 removes, once, every row dated before 2026-10-26
on another wallet set (logged row by row), then `archive_backfill.py --run --project Chainlink` re-reads each day at its
first block on all 33 wallets. A day without all 33 read is a hole, named; never the 27-wallet sum, never 0. The 10-26
nested_sums handover is no longer static: it holds only while the store still has 27-wallet rows (the break stays and
the days are named); on a restated store there is one basis and no break.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402

CL, NC = "Chainlink", "noncirculating_holding_tokens"


def _p(n):
    return config.CHAINLINK_NONCIRC_POINTS[n]


def _g(days, values, sources):
    if isinstance(sources, str):
        sources = [sources] * len(days)
    return pd.DataFrame({"date": pd.to_datetime(days), "project": CL, "metric": NC,
                         "value": [float(v) for v in values], "tier": 2, "source": sources})


@pytest.fixture(autouse=True)
def _clean_active():
    config.RESTATEMENT_ACTIVE.clear()
    yield
    config.RESTATEMENT_ACTIVE.clear()


# ============================================================================================ the declarations
def test_the_handover_is_no_longer_static_and_the_restatement_is_declared():
    assert config.declared_handover(CL, NC) is None, "no static handover: a restated store is one basis"
    r = config.restatement(CL, NC)
    assert r["id"] == "P2b-2" and r["before"] == "2026-10-26" and r["point"] == _p("33")
    assert r["pending_handover"]["nested_sums"] and r["pending_handover"]["overlap"]["b_point"] == _p("33")
    assert "archive_backfill.py --run --project Chainlink" in r["refill"]
    c = [c for c in config.RUN_CLEANUPS if c["id"] == "P2b-2"]
    assert len(c) == 1 and c[0]["once"] and c[0]["superseded_by"] == "restatement" and c[0]["projects"] == (CL,)
    # which rows the restatement replaces: any other set (archive, live, PARTIAL) before 10-26; never the 33-set, never
    # a row from 10-26 on
    reordered = "chain:sum(" + "+".join(sorted(config._sum_set(_p("33")), reverse=True)) + ")"
    assert config.restatement_superseded(CL, NC, "2026-10-25", _p("27") + ":archive")
    assert config.restatement_superseded(CL, NC, "2026-10-20", _p("27"))
    assert config.restatement_superseded(CL, NC, "2026-10-20", _p("27") + ":PARTIAL")
    assert not config.restatement_superseded(CL, NC, "2026-10-20", reordered + ":archive")
    assert config.restatement_superseded(CL, NC, "2026-10-26", _p("27"))      # P2d-1: another set, whatever its date
    assert not config.restatement_superseded("Aerodrome", NC, "2026-10-20", _p("27"))


# ============================================================================================ the clean-up
def _store(tmp_path):
    import store as sm
    st = sm.Store(tmp_path / "m.db")
    rows = pd.concat([
        _g(["2026-10-20", "2026-10-21", "2026-10-22"], [233_150_000.10] * 3, _p("27") + ":archive"),
        _g(["2026-10-23"], [233_150_000.10], _p("27")),                                   # a live read
        _g(["2026-10-24"], [233_000_000.00], _p("27") + ":PARTIAL"),                      # a part-read live day
        _g(["2026-10-19"], [233_150_029.57], _p("33") + ":archive"),                     # already restated: kept
        _g(["2026-10-26", "2026-10-27"], [233_150_029.58] * 2, _p("33")),                 # the current set: kept
    ])
    other = rows.iloc[:1].assign(metric="total_supply", source="chain:ethereum:token", value=1e9)
    st.upsert(pd.concat([rows, other]))
    return st


def test_the_cleanup_removes_every_superseded_row_once_and_logs_each(tmp_path, monkeypatch):
    import logging
    import token_metrics as tm
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(config, "RUN_CLEANUPS", tuple(c for c in config.RUN_CLEANUPS if c["id"] == "P2b-2"))
    st = _store(tmp_path)
    lines = tm.apply_run_cleanups(st, "run1", logging.getLogger("t"))
    left = st.conn.execute("SELECT substr(date,1,10), source FROM metrics WHERE project=? AND metric=? ORDER BY date",
                           (CL, NC)).fetchall()
    assert [d for d, _ in left] == ["2026-10-19", "2026-10-26", "2026-10-27"]
    assert all(config._sum_set(s.split(":archive")[0]) == config._sum_set(_p("33")) for _, s in left)
    assert st.conn.execute("SELECT COUNT(*) FROM metrics WHERE metric='total_supply'").fetchone()[0] == 1
    head = [x for x in lines if "applied once" in x]
    assert len(head) == 1 and "5 row(s) of Chainlink / noncirculating_holding_tokens" in head[0]
    assert "dated before 2026-10-26" in head[0]
    removed = [x for x in lines if ": removed Chainlink" in x]
    assert len(removed) == 5
    for d in ("2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24"):
        assert any(f"noncirculating_holding_tokens {d} from chain:sum(27 components)" in x for x in removed), d
    assert any("chain:sum(27 components):archive (233,150,000.10)" in x for x in removed)
    assert any("chain:sum(27 components):PARTIAL" in x for x in removed)
    # every row is in the Run Log too, and the marker is recorded
    logged = st.conn.execute("SELECT COUNT(*) FROM run_log WHERE source='cleanup:P2b-2'").fetchone()[0]
    assert logged == 5
    assert st.conn.execute("SELECT 1 FROM fetch_status WHERE source='cleanup:P2b-2:noncirculating_holding_tokens' AND "
                           "project='Chainlink'").fetchone()
    # ONCE: a second run removes nothing, even a superseded row written since (the build then names it)
    st.upsert(_g(["2026-10-18"], [233_150_000.10], _p("27")))
    assert tm.apply_run_cleanups(st, "run2", logging.getLogger("t")) == []
    assert st.conn.execute("SELECT COUNT(*) FROM metrics WHERE project=? AND metric=?", (CL, NC)).fetchone()[0] == 4


# ============================================================================================ the archive refill
def _archive(tmp_path, monkeypatch, unreadable_day=None, seed=None):
    """The archive route on a fake chain: one block an hour; each of the 33 wallets holds 1 LINK, except a day on which
    one wallet's balanceOf fails."""
    import store as sm
    from fetch import archive as ar
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    asof = pd.Timestamp("2026-10-27")
    monkeypatch.setattr(ar, "today", lambda: asof)
    t0 = int((asof - pd.Timedelta(days=40)).tz_localize("UTC").timestamp())
    added = config.PROJECT_BY_NAME[CL]["contracts"]["noncirc_cl_page_6"]["address"].lower()

    class Eth:
        block_number = 40 * 24 + 10

        def get_block(self, n):
            return {"timestamp": t0 + n * 3600}

        def get_balance(self, a, block_identifier=None):
            return 0

    class Reader:
        def __init__(self):
            self.at_block = {}

        def web3(self, chain):
            return type("W", (), {"eth": Eth()})()

        def symbol_matches(self, chain, address, expected):
            return True, expected

        def has_code(self, chain, a):
            return True

        def scaled(self, chain, address, call, *args, **kw):
            if not args:
                return 1e9
            day = pd.Timestamp(t0 + self.at_block[chain] * 3600, unit="s").normalize()
            if unreadable_day is not None and day == pd.Timestamp(unreadable_day) and str(args[0]).lower() == added:
                raise RuntimeError("execution reverted")
            return 1.0

    p = dict(config.PROJECT_BY_NAME[CL])
    p["contracts"] = {k: v for k, v in p["contracts"].items()
                      if k == "token" or (v.get("metric_override") == NC and v.get("chain") == "ethereum")}
    monkeypatch.setitem(config.PROJECT_BY_NAME, CL, p)
    st = sm.Store(tmp_path / "a.db")
    st.upsert(seed if seed is not None else _g(["2026-10-26"], [33.0], _p("33")))
    res = ar.ArchiveBackfill(st, [p], budget_s=120, reader=Reader(), days=5).run({"ethereum"})
    rows = st.conn.execute("SELECT substr(date,1,10), value, source FROM metrics WHERE project=? AND metric=? "
                           "ORDER BY date", (CL, NC)).fetchall()
    return res, rows


def test_the_archive_restates_each_day_on_all_33_and_a_missing_wallet_day_is_a_hole(tmp_path, monkeypatch):
    res, rows = _archive(tmp_path, monkeypatch, unreadable_day="2026-10-24")
    got = {d: (v, s) for d, v, s in rows}
    # 10-22, 10-23, 10-25 re-read on all 33 at their first block; 10-24 (one wallet unreadable) NOT written — no
    # 32-wallet sum, no 0 for the wallet
    assert sorted(got) == ["2026-10-22", "2026-10-23", "2026-10-25", "2026-10-26"], sorted(got)
    for d in ("2026-10-22", "2026-10-23", "2026-10-25"):
        v, s = got[d]
        assert v == 33.0 and s.endswith(":archive")
        assert config._sum_set(s.split(":archive")[0]) == config._sum_set(_p("33"))
        assert not config.restatement_superseded(CL, NC, d, s)
    assert res["written"][(CL, NC)] == 3
    assert any(x.startswith("NOT WRITTEN Chainlink/noncirculating_holding_tokens: 1 day(s)") for x in res["report"])
    # the build names that day a hole
    import build_workbook as bw
    g = _g(list(got), [v for v, _ in got.values()], [s for _, s in got.values()])
    bw._series_restatement_views({(CL, NC): g}, pd.Timestamp("2026-10-27"))
    assert "2026-10-24" in bw.RESTATEMENT_STATUS[(CL, NC)] and "HOLES" in bw.RESTATEMENT_STATUS[(CL, NC)]


def test_the_archive_never_writes_33_beside_a_stored_27_day(tmp_path, monkeypatch):
    """Without the clean-up the 27-set rows stay and the archive refuses to interleave: the un-restated store stays on
    its declared seam."""
    seed = pd.concat([_g(["2026-10-22", "2026-10-23", "2026-10-25"], [27.0] * 3, _p("27")),
                      _g(["2026-10-26"], [33.0], _p("33"))])
    res, rows = _archive(tmp_path, monkeypatch, seed=seed)
    assert {d for d, _, _ in rows} == {"2026-10-22", "2026-10-23", "2026-10-25", "2026-10-26"}
    assert "was read from" in res["refused"][(CL, NC)]


# ============================================================================================ the build
def _span(asof="2026-10-27"):
    start = pd.Timestamp(asof) - pd.Timedelta(days=int(config.TRAJECTORY["history_days"]))
    return list(pd.date_range(start, "2026-10-25", freq="D"))


def test_the_restated_store_is_one_basis_with_no_break_and_no_note():
    import build_workbook as bw
    import trajectory
    days = _span()
    g = pd.concat([_g(days, [233_150_029.5] * len(days), _p("33") + ":archive"),
                   _g(["2026-10-26", "2026-10-27"], [233_150_029.58] * 2, _p("33"))])
    groups = {(CL, NC): g}
    bw._series_restatement_views(groups, pd.Timestamp("2026-10-27"))
    assert (CL, NC) not in config.RESTATEMENT_ACTIVE and config.declared_handover(CL, NC) is None
    assert (CL, NC) not in bw.RESTATEMENT_STATUS
    since = pd.Timestamp("2026-10-27") - pd.Timedelta(days=int(config.TRAJECTORY["history_days"]))
    assert trajectory.basis_breaks(CL, [NC], groups, since) == [], "no SERIES BREAK at 10-26"
    mps, _ = bw.method_points(g.sort_values("date"))
    assert len(set(mps)) == 1


def test_a_partly_refilled_store_names_its_holes_and_shows_no_27_sum():
    import build_workbook as bw
    import trajectory
    days = [d for d in _span() if d >= pd.Timestamp("2026-08-01") and d != pd.Timestamp("2026-09-15")]
    g = pd.concat([_g(days, [233_150_029.5] * len(days), _p("33") + ":archive"),
                   _g(["2026-10-26"], [233_150_029.58], _p("33"))])
    groups = {(CL, NC): g}
    bw._series_restatement_views(groups, pd.Timestamp("2026-10-27"))
    txt = bw.RESTATEMENT_STATUS[(CL, NC)]
    assert txt.startswith("RESTATEMENT P2b-2 INCOMPLETE") and "2026-09-15" in txt and "never 0" in txt
    assert "..2026-07-31" in txt                                # the days before the refill reached
    assert config.declared_handover(CL, NC) is None
    assert trajectory.basis_breaks(CL, [NC], groups, pd.Timestamp("2025-01-01")) == []


def test_the_unrestated_store_keeps_its_seam_and_names_the_27_days():
    import build_workbook as bw
    import measuring_guard as mg
    import trajectory
    g = pd.concat([_g(["2026-10-23", "2026-10-24"], [233_150_000.10] * 2, _p("27") + ":archive"),
                   _g(["2026-10-25"], [233_150_000.10], _p("27")),
                   _g(["2026-10-26", "2026-10-27"], [233_150_029.58] * 2, _p("33"))])
    groups = {(CL, NC): g}
    bw._series_restatement_views(groups, pd.Timestamp("2026-10-27"))
    decl = config.declared_handover(CL, NC)
    assert decl and decl["nested_sums"], "the pending seam is declared for this build"
    txt = bw.RESTATEMENT_STATUS[(CL, NC)]
    assert txt.startswith("NOT YET RESTATED (P2b-2): 3 day(s)") and "2026-10-23..2026-10-25" in txt
    assert "SERIES BREAK 2026-10-26" in txt and "archive_backfill.py --run --project Chainlink" in txt
    # the series is NOT blanked (a declared growth) and the Trajectory shows the break at 10-26
    mps, _ = bw.method_points(g.sort_values("date"))
    row = {"source": g["source"].iloc[-1], "measuring_points": tuple(sorted(set(mps))),
           "point_spans": {mp: (gg["date"].min(), gg["date"].max()) for mp, gg in g.groupby(mps)}}
    assert bw.withheld_for(CL, NC, row) is None
    brk = trajectory.basis_breaks(CL, [NC], groups, pd.Timestamp("2026-01-01"))
    assert [b[0] for b in brk] == [pd.Timestamp("2026-10-26")]
    assert mg._nested_growth(CL, NC, set(mps))
    # the overlap check runs on the pending declaration (no common day -> UNCHECKED, naming the live check)
    monkey = bw.HANDOVER_OVERLAP
    bw._handover_overlap_checks(groups)
    assert "UNCHECKED" in monkey[(CL, NC)]["text"]
    # the next build of a restated store drops the declaration again
    bw._series_restatement_views({(CL, NC): g[g["source"].str.startswith(_p("33")[:20]) & (g["date"] >= "2026-10-26")]},
                          pd.Timestamp("2026-10-27"))
    assert config.declared_handover(CL, NC) is None
    assert mg._nested_growth(CL, NC, {_p("27"), _p("33")}), "the guard reads the declared restatement in any process"


def test_jakes_10_13_dump_is_an_unrestated_store_and_says_so():
    d = json.loads((ROOT / "fixtures" / "real_chainlink.json").read_text())
    rows = [r for r in d["metrics"] if r["metric"] == NC]
    import build_workbook as bw
    g = _g([r["date"] for r in rows], [r["value"] for r in rows], [r["source"] for r in rows])
    bw._series_restatement_views({(CL, NC): g}, pd.Timestamp(d["meta"]["asof"]))
    txt = bw.RESTATEMENT_STATUS[(CL, NC)]
    assert txt.startswith(f"NOT YET RESTATED (P2b-2): {len(rows)} day(s)") and "2025-09-08..2026-10-13" in txt


# ============================================================================================ the status check
def test_the_status_check_says_not_restated_incomplete_or_restated(tmp_path, monkeypatch, capsys):
    import check_offline_items as coi
    import store as sm
    import token_metrics as tm
    import logging
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    today0 = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    start = today0 - pd.Timedelta(days=int(config.TRAJECTORY["history_days"]))
    span = list(pd.date_range(start, "2026-10-25", freq="D"))
    st = sm.Store(tmp_path / "s.db")
    st.upsert(pd.concat([_g(span, [27.0] * len(span), _p("27") + ":archive"), _g(["2026-10-26"], [33.0], _p("33"))]))
    assert coi.chainlink_noncirc_restatement(str(tmp_path / "s.db")) == 0
    out = capsys.readouterr().out
    assert f"-> NOT YET RESTATED: {len(span)} day(s)" in out and "CLEAN-UP P2b-2 NOT RUN" in out
    monkeypatch.setattr(config, "RUN_CLEANUPS", tuple(c for c in config.RUN_CLEANUPS if c["id"] == "P2b-2"))
    tm.apply_run_cleanups(st, "r", logging.getLogger("t"))
    st.upsert(_g(span[-10:], [33.0] * 10, _p("33") + ":archive"))
    coi.chainlink_noncirc_restatement(str(tmp_path / "s.db"))
    out = capsys.readouterr().out
    assert f"-> RESTATEMENT INCOMPLETE: {len(span) - 10} day(s)" in out and "CLEAN-UP P2b-2 RUN" in out
    st.upsert(_g(span[:-10], [33.0] * (len(span) - 10), _p("33") + ":archive"))
    coi.chainlink_noncirc_restatement(str(tmp_path / "s.db"))
    assert "-> RESTATED: every day" in capsys.readouterr().out


# ============================================================================================ the archive's start rule
def test_an_eoa_wallets_empty_code_never_ends_the_restatement(tmp_path, monkeypatch):
    """Read live 2026-10-27: one rate-limited day made the archive check each wallet's code; 0xbe6977E0… (an EOA on
    chain.link's list, holder_has_code False) has none at any block, and the series was recorded as starting the next
    day — the rest of the restatement never retried. An EOA's empty code dates nothing; a start recorded that way is
    ignored."""
    from types import SimpleNamespace
    from fetch import archive as ar
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path))

    class Rdr:
        at_block = {"ethereum": 26_258_774}

        def web3(self, chain):
            return SimpleNamespace(eth=SimpleNamespace(get_code=lambda a, block_identifier=None: b""))

        def checksum(self, a):
            return a
    bf = ar.ArchiveBackfill.__new__(ar.ArchiveBackfill)
    bf.reader = Rdr()
    assert bf._undeployed(config.PROJECT_BY_NAME[CL], NC) is None
    ar.record_series_start(CL, NC, "2026-10-25", "noncirc_cl_page_4 0xbe6977E0... has no code at block 26,258,774 — "
                                                "not deployed before 2026-10-25")
    assert (CL, NC) not in ar.series_starts()
    # a token that should carry code still dates a start (the Pendle sPENDLE case)
    ar.record_series_start("Pendle", "insider_stake_shares", "2026-01-17",
                           "insider_stake_dev_multisig 0x… has no code at block 1 (deployed at block 2)")
    assert ("Pendle", "insider_stake_shares") in ar.series_starts()
