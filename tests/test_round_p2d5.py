"""ROUND P2d-5 (Jake's backfill of 2026-10-28): ETHER.FI insider_stake_shares RESTATED ON ITS CURRENT 13-WALLET SET.
No network.

`archive_backfill.py --run --project Ether.fi …` left the series at 453 days: "REFUSED … the stored row before
2026-10-24 (2026-10-23) was read from" the 10-wallet sum — the archive reads the current 13-wallet set and never writes
beside a row on another one. The wallet set only grew (8 from W6 2026-10-13; 10 from 2026-10-14, E1; 13 from
2026-10-26, D2-4b), every component an sETHFI balanceOf at a block — so the series is restated by the P2b-2 / P2c-5
route: CLEAN-UP P2d-5 (once) and P2d-5b (every run, any date) remove each earlier-set row, logged; the archive re-reads
each day on all 13; a day not re-read is a named hole. G11 C1's static nested_sums declaration is withdrawn: the seam
is declared for a build only while earlier-set rows remain.
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
from test_round_p2c5 import _EF8, _EF10, _archive, _g, _pt  # noqa: E402

EF, IS = "Ether.fi", "insider_stake_shares"
_EF13 = _EF10 + ("foundation_q18_1", "foundation_q18_2")
P = {"8": _pt(_EF8, "insider_stake_noncirc_"), "10": _pt(_EF10, "insider_stake_noncirc_"),
     "13": _pt(_EF13, "insider_stake_noncirc_")[:-1] + "+ethereum:insider_stake_treasury)"}
ONCE, REPEAT = "P2d-5", "P2d-5b"


@pytest.fixture(autouse=True)
def _clean_active():
    config.RESTATEMENT_ACTIVE.clear()
    yield
    config.RESTATEMENT_ACTIVE.clear()


def _only_p2d5(monkeypatch):
    monkeypatch.setattr(config, "RUN_CLEANUPS", tuple(c for c in config.RUN_CLEANUPS if c["id"] in (ONCE, REPEAT)))


def _span(asof=None, end="2026-10-25"):
    asof = pd.Timestamp(asof) if asof is not None else pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    start = asof - pd.Timedelta(days=int(config.TRAJECTORY["history_days"]))
    return list(pd.date_range(start, end, freq="D"))


# ============================================================================================ the declaration
def test_declared_on_the_current_13_wallet_set_and_the_set_only_grew():
    from fetch import archive as ar
    r = config.restatement(EF, IS)
    assert r["id"] == ONCE and r["before"] == "2026-10-26" and config.restatement_repeat_id(r) == REPEAT
    assert config._sum_set(r["point"]) == config._sum_set(P["13"]) and len(config._sum_set(r["point"])) == 13
    # ADDITIONS ONLY: 8 ⊂ 10 ⊂ 13 (config at 359cc1f, b2578e1, 4555346)
    assert config._sum_set(P["8"]) < config._sum_set(P["10"]) < config._sum_set(r["point"])
    assert config.restatement_seams(EF, IS) == ("2026-10-14", "2026-10-26")
    assert r["pending_handover"]["nested_sums"] and "none removed" in r["pending_handover"]["composition_change"]
    assert r["refill"] == "python archive_backfill.py --run --project Ether.fi"
    # G11 C1's static declaration is withdrawn: a restated store is one basis, no break
    assert config.declared_handover(EF, IS) is None
    assert not (config.PROJECT_BY_NAME[EF].get("series_handover") or {}).get(IS)
    assert not config.archive_not_by_design(EF, IS)
    ok, why = ar.archivable(config.PROJECT_BY_NAME[EF], IS, {"ethereum"}, set())
    assert ok, why
    once = [c for c in config.RUN_CLEANUPS if c["id"] == ONCE]
    rep = [c for c in config.RUN_CLEANUPS if c["id"] == REPEAT]
    assert len(once) == 1 and once[0]["once"] and once[0]["superseded_by"] == "restatement"
    assert once[0]["projects"] == (EF,) and once[0]["metric"] == IS
    assert len(rep) == 1 and rep[0]["repeat"] and rep[0]["projects"] == (EF,) and rep[0]["metric"] == IS
    # the other restatements' every-run ids are unchanged
    assert config.restatement_repeat_id(config.restatement("Chainlink", "noncirculating_holding_tokens")) == "P2b-2b"
    assert config.restatement_repeat_id(config.restatement("Maple", IS)) == "P2c-5b2"


def test_the_definition_is_unchanged():
    """A sum of sETHFI SHARES held (the numerator of the insider share of the stake), ÷ locked_tokens (sETHFI
    totalSupply) at read time — shares over shares."""
    m = config.METRICS[IS]
    assert m["kind"] == "stock" and m["unit"] == "tokens" and "numerator of the insider share" in m["label"]
    mon = config.INSIDER_SHARE_MONITOR[EF]
    assert (mon["numerator"], mon["denominator"], mon["symbol"]) == (IS, "locked_tokens", "sETHFI")
    cs = config.PROJECT_BY_NAME[EF]["contracts"]
    assert {cs[k.split(":", 1)[1]]["underlying"] for k in config._sum_set(P["13"])} == {"sethfi_shares"}
    assert cs["sethfi_shares"]["metric_override"] == "locked_tokens"


def test_which_rows_are_superseded():
    rev = "chain:sum(" + "+".join(sorted(config._sum_set(P["13"]), reverse=True)) + ")"
    assert config.restatement_superseded(EF, IS, "2025-09-08", P["8"] + ":archive")
    assert config.restatement_superseded(EF, IS, "2026-10-23", P["10"])
    assert config.restatement_superseded(EF, IS, "2026-10-20", P["10"] + ":PARTIAL")
    assert config.restatement_superseded(EF, IS, "2026-10-26", P["10"])          # P2d-1: any date
    assert not config.restatement_superseded(EF, IS, "2026-10-20", rev + ":archive")
    assert not config.restatement_superseded(EF, IS, "2026-10-27", P["13"])
    # a PARTIAL read of the 13 on or after 10-26 is a partial read of the current set, kept
    part = P["13"].replace("+ethereum:insider_stake_treasury", "") + ":PARTIAL"
    assert not config.restatement_superseded(EF, IS, "2026-10-27", part)


# ============================================================================================ an old-set store
def test_an_old_set_store_says_not_yet_restated_with_both_seams(tmp_path, capsys):
    import build_workbook as bw
    import check_offline_items as coi
    import measuring_guard as mg
    import store as sm
    import trajectory
    g = pd.concat([_g(EF, IS, ["2026-10-12", "2026-10-13"], 0.0, P["8"] + ":archive"),
                   _g(EF, IS, ["2026-10-14", "2026-10-23"], 0.0, P["10"]),
                   _g(EF, IS, ["2026-10-26", "2026-10-27"], 0.0, P["13"])])
    groups = {(EF, IS): g}
    bw._series_restatement_views(groups, pd.Timestamp("2026-10-27"))
    assert config.declared_handover(EF, IS)["nested_sums"], "the pending seam is declared for this build"
    txt = bw.RESTATEMENT_STATUS[(EF, IS)]
    assert txt.startswith("NOT YET RESTATED (P2d-5): 4 day(s)") and "2026-10-12..2026-10-14, 2026-10-23" in txt
    assert "SERIES BREAK 2026-10-14, 2026-10-26" in txt and "archive_backfill.py --run --project Ether.fi" in txt
    assert [b[0] for b in trajectory.basis_breaks(EF, [IS], groups, pd.Timestamp("2026-01-01"))] == \
        [pd.Timestamp("2026-10-14"), pd.Timestamp("2026-10-26")]
    mps, _ = bw.method_points(g.sort_values("date"))
    row = {"source": g["source"].iloc[-1], "measuring_points": tuple(sorted(set(mps))),
           "point_spans": {mp: (gg["date"].min(), gg["date"].max()) for mp, gg in g.groupby(mps)}}
    assert bw.withheld_for(EF, IS, row) is None, "not blanked: a declared growth"
    assert mg._nested_growth(EF, IS, set(mps))
    # the status check on a store holding the old sets over the span
    span = _span()
    st = sm.Store(tmp_path / "s.db")
    st.upsert(pd.concat([_g(EF, IS, span, 0.0, P["10"] + ":archive"), _g(EF, IS, ["2026-10-26"], 0.0, P["13"])]))
    coi.series_restatements(str(tmp_path / "s.db"))
    out = capsys.readouterr().out
    blk = out[out.index("ETHER.FI — insider_stake_shares"):]
    assert "CLEAN-UP P2d-5 NOT RUN" in blk and "set grew 2026-10-14, 2026-10-26" in blk
    assert f"-> NOT YET RESTATED: {len(span)} day(s)" in blk


# ============================================================================================ Jake's store
def _jakes_store(tmp_path):
    """Jake's store at the refused backfill: the 8-wallet archive history to 2026-10-13, the 10 from 10-14 to 10-23
    (a PARTIAL live day among them), 10-24 a hole, 10-25 and 10-26 still on the 10 (read before D2-4 was pulled), the 13
    from 10-27. The P2c-5 clean-ups ran on it, but they never named this series: P2d-5's once-flag is NOT recorded."""
    import store as sm
    st = sm.Store(tmp_path / "s.db")
    st.upsert(pd.concat([
        _g(EF, IS, ["2026-10-11", "2026-10-12", "2026-10-13"], 0.0, P["8"] + ":archive"),
        _g(EF, IS, ["2026-10-14", "2026-10-15", "2026-10-22", "2026-10-23"], 0.0, P["10"]),
        _g(EF, IS, ["2026-10-16"], 0.0, P["10"] + ":PARTIAL"),
        _g(EF, IS, ["2026-10-25", "2026-10-26"], 0.0, P["10"]),
        _g(EF, IS, ["2026-10-27", "2026-10-28"], 0.0, P["13"]),
    ]))
    st.record_fetch("r0", "cleanup:P2c-5a:noncirculating_holding_tokens", EF, 459, "ok",
                    "CLEAN-UP P2c-5a: applied once — 459 row(s)")
    return st


def _rows(st):
    return st.conn.execute("SELECT substr(date,1,10), value, source FROM metrics WHERE project=? AND metric=? "
                           "ORDER BY date", (EF, IS)).fetchall()


def test_jakes_store_cleanup_refill_restated(tmp_path, monkeypatch, capsys):
    """The reproduction: old-set rows on both sides of the 13's first day -> the archive refuses (as Jake's run) ->
    the clean-ups (once + every run) remove every 8- and 10-wallet row, each logged -> the archive re-reads each day on
    all 13 -> RESTATED, one basis, no break; the guard raises no BASIS."""
    import build_workbook as bw
    import check_offline_items as coi
    import measuring_guard as mg
    import token_metrics as tm
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    _only_p2d5(monkeypatch)
    st = _jakes_store(tmp_path)
    # 1. before the clean-up: the archive refuses beside an earlier-set row (what Jake's run logged)
    (tmp_path / "before").mkdir()
    res0, _rows0, _n = _archive(tmp_path / "before", monkeypatch, EF, IS, pd.DataFrame(
        [{"date": pd.Timestamp(d), "project": EF, "metric": IS, "value": v, "tier": 2, "source": s}
         for d, v, s in _rows(st)]), asof="2026-10-28", days=6)
    assert "the stored row before 2026-10-24 (2026-10-23) was read from chain:sum(" in res0["refused"][(EF, IS)]
    assert not res0["written"].get((EF, IS))
    # the check says BLOCKED (a 10-wallet row on/after 10-26) and names the fix
    coi.series_restatements(str(tmp_path / "s.db"))
    out = capsys.readouterr().out
    blk = out[out.index("ETHER.FI — insider_stake_shares"):]
    assert "CLEAN-UP P2d-5 NOT RUN" in blk and "!! 2026-10-26 is stored on the 10-wallet set" in blk
    assert "-> BLOCKED: 1 row(s) on another set on or after 2026-10-26 (2026-10-26)" in blk and "CLEAN-UP P2d-5b" in blk
    # 2. the daily run's clean-ups: the once pass (rows before 10-26) and the every-run pass (10-26), each row logged
    lines = tm.apply_run_cleanups(st, "run1", logging.getLogger("t"))
    assert [d for d, _v, _s in _rows(st)] == ["2026-10-27", "2026-10-28"]
    assert any(x.startswith("CLEAN-UP P2d-5: applied once — 9 row(s) of Ether.fi / insider_stake_shares") for x in lines)
    assert any(x.startswith("CLEAN-UP P2d-5b: 1 row(s) of Ether.fi / insider_stake_shares on another set") for x in lines)
    removed = [x for x in lines if ": removed " in x]
    assert len(removed) == 10
    for d in ("2026-10-11", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-22",
              "2026-10-23", "2026-10-25"):
        assert any(f"CLEAN-UP P2d-5: removed Ether.fi / insider_stake_shares {d} from " in x for x in removed), d
    assert any("CLEAN-UP P2d-5b: removed Ether.fi / insider_stake_shares 2026-10-26 from chain:sum(10 components)" in x
               for x in removed)
    assert any("from chain:sum(8 components):archive" in x for x in removed)
    assert any("from chain:sum(10 components):PARTIAL" in x for x in removed)
    assert st.conn.execute("SELECT COUNT(*) FROM run_log WHERE source='cleanup:P2d-5'").fetchone()[0] == 9
    assert st.conn.execute("SELECT COUNT(*) FROM run_log WHERE source='cleanup:P2d-5b'").fetchone()[0] == 1
    assert tm.apply_run_cleanups(st, "run2", logging.getLogger("t")) == []          # idempotent
    # 3. the archive on the cleaned store (fake chain, 6 days back from 10-28): each day re-read on all 13
    kept = _rows(st)
    (tmp_path / "after").mkdir()
    res, rows, n = _archive(tmp_path / "after", monkeypatch, EF, IS,
                            _g(EF, IS, [d for d, _v, _s in kept], [v for _d, v, _s in kept], [s for _d, _v, s in kept]),
                            asof="2026-10-28", days=6)
    assert n == 13 and not res.get("refused", {}).get((EF, IS))
    got = {d: (v, s) for d, v, s in rows}
    for d in ("2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26"):
        v, s = got[d]
        assert v == 13.0 and s.endswith(":archive") and config._sum_set(s.split(":archive")[0]) == config._sum_set(P["13"])
    # 4. the whole span on the 13 (what the full backfill writes) -> RESTATED; the build: one basis, no break
    span = _span(end="2026-10-26")
    st.upsert(_g(EF, IS, span, 0.0, P["13"] + ":archive"))
    capsys.readouterr()
    coi.series_restatements(str(tmp_path / "s.db"))
    blk = capsys.readouterr().out
    blk = blk[blk.index("ETHER.FI — insider_stake_shares"):]
    blk = blk[:blk.index("PASTE BACK")]
    assert "-> RESTATED: every day" in blk and "BLOCKED" not in blk and "!!" not in blk
    assert "CLEAN-UP P2d-5 RUN" in blk
    allrows = _rows(st)
    g = _g(EF, IS, [d for d, _v, _s in allrows], [v for _d, v, _s in allrows], [s for _d, _v, s in allrows])
    bw._series_restatement_views({(EF, IS): g}, pd.Timestamp.now(tz="UTC").tz_localize(None).normalize())
    assert (EF, IS) not in config.RESTATEMENT_ACTIVE and config.declared_handover(EF, IS) is None
    assert (EF, IS) not in bw.RESTATEMENT_STATUS
    mps, _ = bw.method_points(g.sort_values("date"))
    assert len(set(mps)) == 1
    # the guard's basis check: one basis — nothing to flag
    import trajectory
    baseline = json.loads((ROOT / "tests" / "fixtures" / "guard_baseline.json").read_text("utf-8"))
    bases = {f"{EF}|{IS}": sorted({trajectory.basis_of(IS, s) for s in g["source"].astype(str).unique()})}
    assert len(bases[f"{EF}|{IS}"]) == 1
    assert mg.live_basis_violations(bases, baseline) == []


def test_the_guard_reads_an_unrestated_store_as_a_declared_growth():
    """Before the clean-up the store holds the 8, the 10 and the 13: the restatement's pending seam is a declared growth
    in any process (no BASIS); a swapped set would still be flagged."""
    import measuring_guard as mg
    import trajectory
    baseline = json.loads((ROOT / "tests" / "fixtures" / "guard_baseline.json").read_text("utf-8"))
    bases = sorted({trajectory.basis_of(IS, s) for s in (P["8"] + ":archive", P["10"], P["13"])})
    assert len(bases) == 3
    assert mg._nested_growth(EF, IS, set(bases))
    assert mg.live_basis_violations({f"{EF}|{IS}": bases}, baseline) == []
    swapped = [trajectory.basis_of(IS, P["13"]), "chain:sum(ethereum:insider_stake_elsewhere)"]
    assert mg.live_basis_violations({f"{EF}|{IS}": swapped}, baseline)


def test_a_wallet_unreadable_on_a_day_leaves_a_named_hole(tmp_path, monkeypatch):
    import build_workbook as bw
    seed = _g(EF, IS, ["2026-10-26"], 13.0, P["13"])
    res, rows, n = _archive(tmp_path, monkeypatch, EF, IS, seed, unreadable=("2026-10-24", "insider_stake_treasury"))
    got = {d: (v, s) for d, v, s in rows}
    # 10-24 (the TREASURY's balanceOf failed) is NOT written — never the 12 others' sum, never 0 for the wallet
    assert sorted(got) == ["2026-10-22", "2026-10-23", "2026-10-25", "2026-10-26"]
    assert any(x.startswith(f"NOT WRITTEN {EF}/{IS}: 1 day(s)") for x in res["report"])
    g = _g(EF, IS, list(got), [v for v, _ in got.values()], [s for _, s in got.values()])
    bw._series_restatement_views({(EF, IS): g}, pd.Timestamp("2026-10-27"))
    txt = bw.RESTATEMENT_STATUS[(EF, IS)]
    assert txt.startswith("RESTATEMENT P2d-5 INCOMPLETE") and "2026-10-24" in txt and "never 0" in txt
