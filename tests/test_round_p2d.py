"""ROUND P2d (Jake's run of 2026-10-28): THE CHAINLINK RESTATEMENT THAT DID NOT COMPLETE. No network.

Jake's store after CLEAN-UP P2b-2: every 27-wallet row before 2026-10-26 removed, but the 2026-10-26 row itself on the
27-wallet set; the archive refused every earlier day beside it ("the stored row after 2026-10-25 (2026-10-26) was read
from chain:sum(…27…)"), and the check said "re-run the backfill", which could not succeed. Now: a row on another set is
superseded whatever its date; the every-run pass (CLEAN-UP P2b-2b) removes it, logged; the archive re-reads that day on
all 33; the check says BLOCKED (naming the row and the fix) until then, and RESTATED after.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402
from test_round_p2b2 import CL, NC, _archive, _g, _p  # noqa: E402

MARK = "cleanup:P2b-2:noncirculating_holding_tokens"


@pytest.fixture(autouse=True)
def _clean_active():
    config.RESTATEMENT_ACTIVE.clear()
    yield
    config.RESTATEMENT_ACTIVE.clear()


def _jakes_store(tmp_path):
    """Jake's store after the once-only pass: the marker recorded, the 10-26 row on the 27-wallet set, the 33 from
    10-27, nothing on any earlier day."""
    import store as sm
    st = sm.Store(tmp_path / "s.db")
    st.upsert(pd.concat([_g(["2026-10-26"], [233_150_000.10], _p("27")),
                         _g(["2026-10-27", "2026-10-28"], [233_150_029.58] * 2, _p("33"))]))
    st.record_fetch("r0", MARK, CL, 466, "ok", "CLEAN-UP P2b-2: applied once — 466 row(s)")
    return st


def test_a_row_on_another_set_is_superseded_whatever_its_date():
    assert config.restatement_superseded(CL, NC, "2026-10-26", _p("27"))
    assert config.restatement_superseded(CL, NC, "2026-11-30", _p("27") + ":archive")
    assert not config.restatement_superseded(CL, NC, "2026-10-26", _p("33"))
    # a PARTIAL read of the current set (a component refused that day) is not another set
    part = _p("33").replace("+ethereum:noncirc_cl_page_6", "") + ":PARTIAL"
    assert not config.restatement_superseded(CL, NC, "2026-10-28", part)
    assert config.restatement_superseded(CL, NC, "2026-10-20", _p("27") + ":PARTIAL")


def test_the_check_names_the_blocking_row_and_the_fix(tmp_path, capsys):
    import check_offline_items as coi
    _jakes_store(tmp_path)
    coi.chainlink_noncirc_restatement(str(tmp_path / "s.db"))
    out = capsys.readouterr().out
    assert "!! 2026-10-26 is stored on the 27-wallet set" in out and "REFUSES" in out
    assert "-> BLOCKED: 1 row(s) on another set on or after 2026-10-26 (2026-10-26)" in out
    assert "re-running it cannot succeed" in out and "CLEAN-UP P2b-2b" in out and "python token_metrics.py" in out
    assert "-> RESTATEMENT INCOMPLETE" not in out


def test_the_every_run_pass_removes_the_boundary_row_logged_and_is_idempotent(tmp_path, monkeypatch):
    import token_metrics as tm
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(config, "RUN_CLEANUPS", tuple(c for c in config.RUN_CLEANUPS
                                                      if c["id"] in ("P2b-2", "P2b-2b")))
    st = _jakes_store(tmp_path)
    lines = tm.apply_run_cleanups(st, "run1", logging.getLogger("t"))
    left = st.conn.execute("SELECT substr(date,1,10), source FROM metrics WHERE project=? AND metric=? ORDER BY date",
                           (CL, NC)).fetchall()
    assert [d for d, _ in left] == ["2026-10-27", "2026-10-28"]               # the 33-wallet rows kept
    assert any(x.startswith("CLEAN-UP P2b-2b: 1 row(s) of Chainlink / noncirculating_holding_tokens on another set")
               for x in lines)
    assert any("CLEAN-UP P2b-2b: removed Chainlink / noncirculating_holding_tokens 2026-10-26 from "
               "chain:sum(27 components)" in x for x in lines)
    assert not any("P2b-2:" in x and "applied once" in x for x in lines)      # the once pass stays done
    assert st.conn.execute("SELECT COUNT(*) FROM run_log WHERE source='cleanup:P2b-2b'").fetchone()[0] == 1
    assert tm.apply_run_cleanups(st, "run2", logging.getLogger("t")) == []    # idempotent: nothing left to remove
    # a 27-wallet row written again later (any date) is removed again, by the same pass
    st.upsert(_g(["2026-11-02"], [233_150_000.10], _p("27")))
    assert any("2026-11-02" in x for x in tm.apply_run_cleanups(st, "run3", logging.getLogger("t")))


def test_jakes_store_ends_restated_after_the_cleanup_and_the_backfill(tmp_path, monkeypatch, capsys):
    """The reproduction: 27-wallet row on the boundary day, no 33-wallet row before it -> clean-up -> backfill ->
    the boundary day re-read at its first block on all 33 -> RESTATED."""
    import check_offline_items as coi
    import token_metrics as tm
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(config, "RUN_CLEANUPS", tuple(c for c in config.RUN_CLEANUPS
                                                      if c["id"] in ("P2b-2", "P2b-2b")))
    st = _jakes_store(tmp_path)
    # before the fix's clean-up, the archive refuses beside the 27-wallet boundary row (what Jake's run logged)
    (tmp_path / "before").mkdir()
    (tmp_path / "after").mkdir()
    res0, _rows0 = _archive(tmp_path / "before", monkeypatch,
                            seed=pd.concat([_g(["2026-10-26"], [27.0], _p("27")), _g(["2026-10-27"], [33.0], _p("33"))]))
    assert "2026-10-26" in res0["refused"][(CL, NC)] and "was read from" in res0["refused"][(CL, NC)]
    tm.apply_run_cleanups(st, "run1", logging.getLogger("t"))
    # the archive (fake chain, 5 days back from 10-27) on the cleaned store: 10-26 is re-read on all 33
    kept = st.conn.execute("SELECT date, value, source FROM metrics WHERE project=? AND metric=?", (CL, NC)).fetchall()
    seed = _g([d for d, _v, _s in kept], [v for _d, v, _s in kept], [s for _d, _v, s in kept])
    res, rows = _archive(tmp_path / "after", monkeypatch, seed=seed)
    got = {d: s for d, _v, s in rows}
    assert "2026-10-26" in got and got["2026-10-26"].endswith(":archive")
    assert config._sum_set(got["2026-10-26"].split(":archive")[0]) == config._sum_set(_p("33"))
    assert not res.get("refused", {}).get((CL, NC))
    # the whole span on the 33 (what the full backfill writes) -> the check says RESTATED
    today0 = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    start = today0 - pd.Timedelta(days=int(config.TRAJECTORY["history_days"]))
    span = list(pd.date_range(start, "2026-10-26", freq="D"))
    st.upsert(_g(span, [233_150_029.58] * len(span), _p("33") + ":archive"))
    capsys.readouterr()
    coi.chainlink_noncirc_restatement(str(tmp_path / "s.db"))
    out = capsys.readouterr().out
    assert "-> RESTATED: every day" in out and "BLOCKED" not in out and "!!" not in out


@pytest.mark.parametrize("proj", ["Chainlink", "Ether.fi", "Maple", "Uniswap"])
def test_an_on_chain_route_takes_no_coingecko_once_it_has_an_on_chain_day(proj, monkeypatch):
    """P2d-3b (Jake 2026-10-28): on-chain only, no handover; CoinGecko only where the project has no on-chain figure."""
    import build_workbook as bw
    import credibility
    import measuring_guard as mg
    monkeypatch.setattr(bw, "_SCOPE", list(bw.PROJECTS))   # never depend on another test's build(only=...) scope
    p = config.PROJECT_BY_NAME[proj]
    assert bw.chosen_circulating_metric(p) == "circulating_supply_onchain"
    assert config.declared_handover(proj, "circulating_supply_used") is None
    lim = credibility.now_max_age(proj, "circulating_supply_onchain")
    days = pd.date_range("2026-10-01", "2026-10-28", freq="D")
    gap_end = pd.Timestamp("2026-10-12") + pd.Timedelta(days=lim + 2)
    on = [d for d in days if d == pd.Timestamp("2026-10-12") or d > gap_end]

    def g(metric, ds, v, src):
        return pd.DataFrame({"date": ds, "project": proj, "metric": metric, "value": float(v), "tier": 1,
                             "source": src})
    base = {(proj, "price_usd"): g("price_usd", days, 15.0, "coingecko"),
            (proj, "circulating_supply"): g("circulating_supply", days, 680e6, "coingecko")}
    groups = {**base, (proj, "circulating_supply_onchain"): g("circulating_supply_onchain", on, 700e6, "x")}
    bw._market_cap_views(groups)
    u = groups[(proj, "circulating_supply_used")].set_index("date")
    assert set(u["source"]) == {"derived:circulating_used[circulating_supply_onchain]"}   # never CoinGecko
    assert u.index.min() == pd.Timestamp("2026-10-12")          # days before the on-chain span are holes
    assert gap_end not in u.index                               # a gap past the carry-forward is a hole
    bases = sorted({s.split("[")[1].rstrip("]") for s in u["source"]})
    baseline = __import__("json").loads((ROOT / "tests" / "fixtures" / "guard_baseline.json").read_text("utf-8"))
    assert mg.live_basis_violations({f"{proj}|circulating_supply_used": bases}, baseline) == []
    # a project with no on-chain figure at all keeps CoinGecko
    groups = dict(base)
    bw._market_cap_views(groups)
    u = groups[(proj, "circulating_supply_used")]
    assert set(u["source"]) == {"derived:circulating_used[circulating_supply]"} and len(u) == len(days)
