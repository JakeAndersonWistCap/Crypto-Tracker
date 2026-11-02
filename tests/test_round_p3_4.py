"""ROUND P3-4 (Jake, 2026-11-02): THE CATCH-UP. Days missed between daily runs stayed holes until a backfill was run by
hand. The daily run (token_metrics.py) now finds, for every daily on-chain series the archive can read at a past block,
the days missed since its last stored day and fills them by the archive's own read at each day's first block, BEFORE
today's tiers, budgeted and resumable, inside the run's lock; archive_backfill.py takes that lock too.

The fixture: PancakeSwap's burn leg (burn_address_balance -> gross_burn_tokens, read on BSC) with a 3-day hole at the
end, beside its rule-9 partner (gross_issuance_tokens: the other leg of burn / issuance, _guarded_net's same-span pair)
held every day. A fake BSC: one block an hour, the dead address's balance = 1,000,000 + the block number, so a value
names the block it was read at. No network.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

PC, BAL, BURN, ISS, TSG = "PancakeSwap", "burn_address_balance", "gross_burn_tokens", "gross_issuance_tokens", \
    "total_supply_gross"
TODAY = pd.Timestamp("2026-10-30")
Y = TODAY - pd.Timedelta(days=1)                 # yesterday: the catch-up's last day
HOLE = [str((Y - pd.Timedelta(days=i)).date()) for i in (2, 1, 0)]           # 10-27, 10-28, 10-29
T0 = int((TODAY - pd.Timedelta(days=40)).tz_localize("UTC").timestamp())    # block 0


def first_block(day) -> int:
    return int((pd.Timestamp(day).tz_localize("UTC").timestamp() - T0) // 3600)


class Eth:
    block_number = 41 * 24

    def get_block(self, n):
        return {"timestamp": T0 + n * 3600}

    def get_balance(self, a, block_identifier=None):
        return 0

    def get_code(self, a, block_identifier=None):
        return b"\x60"


class Reader:
    """The fake BSC: totalSupply 400m; the dead address holds 1,000,000 + the pinned block number. `refuse` days raise."""

    def __init__(self, refuse=()):
        self.at_block, self.reads, self.refuse = {}, [], set(refuse)

    def web3(self, chain):
        return type("W", (), {"eth": Eth()})()

    def checksum(self, a):
        return a

    def symbol_matches(self, chain, address, expected):
        return True, expected

    def has_code(self, chain, a):
        return True

    def scaled(self, chain, address, call, *args, **kw):
        blk = self.at_block[chain]
        day = str(pd.Timestamp(T0 + blk * 3600, unit="s").normalize().date())
        self.reads.append((day, call))
        if not args:
            return 400e6
        if day in self.refuse:
            raise RuntimeError("execution reverted")
        return 1_000_000.0 + blk


def _rows(metric, days, value, source):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": PC, "metric": metric,
                         "value": [float(value(d)) for d in days], "tier": 2, "source": source})


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A store as the live reads left it: 30 days of both legs; the burn leg's last 3 days (to yesterday) missing."""
    import store as sm
    from fetch import archive as ar
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(ar, "today", lambda: TODAY)
    p = dict(config.PROJECT_BY_NAME[PC])
    p["contracts"] = {k: v for k, v in p["contracts"].items() if k in ("token", "burn_dead")}
    monkeypatch.setitem(config.PROJECT_BY_NAME, PC, p)
    full = [Y - pd.Timedelta(days=i) for i in range(29, -1, -1)]
    held = [d for d in full if str(d.date()) not in HOLE]
    st = sm.Store(tmp_path / "metrics.db")
    st.upsert(pd.concat([
        _rows(BAL, held, lambda d: 1_000_000.0 + first_block(d), "chain:bsc:burn_dead"),
        _rows(BURN, held[1:], lambda d: 24.0, "chain:bsc:burn_dead:delta"),
        _rows(TSG, full, lambda d: 400e6, "chain:bsc:token"),
        _rows(ISS, full[1:], lambda d: 50.0, "defillama:pancakeswap:emissions"),
    ]))
    return st, p, tmp_path


def _days(st, metric):
    return [d for (d,) in st.conn.execute("SELECT substr(date,1,10) FROM metrics WHERE project=? AND metric=? "
                                          "ORDER BY date", (PC, metric))]


def _catch_up(st, p, reader, **kw):
    from fetch import archive as ar
    kw.setdefault("budget_s", 120)
    kw.setdefault("max_days", 7)
    return ar.catch_up(st, [p], reader=reader, archive_chains={"bsc"}, **kw)


def _rule9(st, asof, days=20):
    """Each leg's covered days in the window ending `asof`, as the build counts them (build_workbook: the D6-1 covered
    sets, _window_coverage, minus _interior_holes), and whether _coverage_guard's same-span test (rule 9: the two sides
    differ by more than a tenth of the window) would BLOCK burn / issuance."""
    import build_workbook as bw
    long = pd.read_sql("SELECT date, project, metric, value, source FROM metrics WHERE project=?", st.conn, params=(PC,))
    long["date"] = pd.to_datetime(long["date"]).dt.normalize()
    groups = {k: g.sort_values("date") for k, g in long.groupby(["project", "metric"])}
    sets = bw._covered_day_sets(groups)
    start = asof - pd.Timedelta(days=days)
    cov = {}
    for m in (BURN, ISS):
        g = groups[(PC, m)]
        s = g.set_index("date")["value"]
        n, _ = bw._window_coverage(s, start, asof, bw._differenced_first(g))
        cov[m] = n - len(bw._interior_holes(sets[(PC, m)], start, asof, s.index.min()))
    held = {m: {d for d in sets[(PC, m)] if start < d <= asof} for m in (BURN, ISS)}
    return cov, abs(cov[BURN] - cov[ISS]) > days / 10, held


# ============================================================================================ the 3-day hole
def test_the_3_day_hole_is_filled_at_each_days_first_block_and_rule9_coverage_is_equal_again(world):
    st, p, _ = world
    _, _, held = _rule9(st, Y)
    assert sorted(str(d.date()) for d in held[ISS] - held[BURN]) == HOLE        # the partner holds them, the burn not
    r = Reader()
    res = _catch_up(st, p, r)
    # each day read at ITS first block: the dead address's balance names the block
    got = dict(st.conn.execute("SELECT substr(date,1,10), value FROM metrics WHERE project=? AND metric=? AND "
                               "source='chain:bsc:burn_dead:archive'", (PC, BAL)).fetchall())
    assert got == {d: 1_000_000.0 + first_block(d) for d in HOLE}
    # the flow, day by day from the stock (the backfill's derive_flows): 24 blocks a day, never a lump, never 0
    flows = dict(st.conn.execute("SELECT substr(date,1,10), value || ' ' || source FROM metrics WHERE project=? AND "
                                 "metric=? AND date>=?", (PC, BURN, HOLE[0])).fetchall())
    assert flows == {d: "24.0 chain:bsc:burn_dead:archive:delta" for d in HOLE}
    # the other series read on the chain (total_supply_gross) had no gap: not read, no line
    assert {c for _, c in r.reads} == {"balanceOf"} and sorted({d for d, _ in r.reads}) == HOLE
    # RULE 9: the two legs' covered days are equal again, day for day
    cov, blocked, held = _rule9(st, Y)
    assert cov[BURN] == cov[ISS] == 20 and not blocked and held[BURN] == held[ISS]
    # ... and after today's tier lands (today's delta now differences yesterday's reading: one day, no span)
    st.upsert(pd.concat([_rows(BAL, [TODAY], lambda d: 1_000_000.0 + first_block(d), "chain:bsc:burn_dead"),
                         _rows(BURN, [TODAY], lambda d: 24.0, "chain:bsc:burn_dead:delta"),
                         _rows(ISS, [TODAY], lambda d: 50.0, "defillama:pancakeswap:emissions")]))
    cov, blocked, held = _rule9(st, TODAY)
    assert cov[BURN] == cov[ISS] == 20 and not blocked and held[BURN] == held[ISS]
    # THE LOG: one line for the series filled, none for the one without a gap, and the summary
    lines = [t for _, t in res["lines"]]
    assert lines[0] == (f"CATCH-UP {PC}/{BAL}: 3 day(s) filled (2026-10-27..10-29), 0 left — archive read at each "
                        f"day's first block via bsc; 3 {BURN} row(s) differenced from it"), lines
    assert not [t for t in lines if f"/{TSG}" in t]
    assert lines[-1].startswith("CATCH-UP: 1 of 2 daily on-chain series had missed days — 3 day(s) filled, 0 left, "
                                "0 hole(s) named; 3 day(s) read in "), lines[-1]
    assert "(budget 2 min, 7 day(s) a run)" in lines[-1]
    assert all(level == "info" for level, _ in res["lines"])


def test_a_second_catch_up_reads_nothing(world):
    st, p, _ = world
    _catch_up(st, p, Reader())
    r2 = Reader()
    res = _catch_up(st, p, r2)
    assert r2.reads == [] and res["gaps"] == {}
    assert [t for _, t in res["lines"]] == [
        "CATCH-UP: 2 daily on-chain series checked — none has a missed day since its last stored day; nothing read"]


# ============================================================================================ a day the chain refuses
def test_a_day_the_chain_refuses_stays_a_named_hole_retried_then_left_to_the_backfill(world):
    st, p, tmp = world
    from fetch import archive as ar
    r = Reader(refuse={HOLE[1]})
    res = _catch_up(st, p, r)
    assert HOLE[1] not in _days(st, BAL) and HOLE[1] not in _days(st, BURN)       # never 0, never carried
    assert {HOLE[0], HOLE[2]} <= set(_days(st, BAL))
    line = next(t for lv, t in res["lines"] if t.startswith(f"CATCH-UP {PC}/{BAL}:"))
    assert line.startswith(f"CATCH-UP {PC}/{BAL}: 2 day(s) filled (2026-10-27, 2026-10-29), 1 left (2026-10-28)"), line
    assert ("HOLE(S) 2026-10-28 — the archive could not read them: burn_dead" in line
            and "execution reverted" in line and "never 0, never carried; tried on 1 of 3 runs" in line), line
    assert res["lines"][-1][0] == "warning" and "1 hole(s) named" in res["lines"][-1][1]
    # the next run reads ONLY the hole (the filled days are skipped: stored), and after 3 runs leaves it to the backfill
    for k in (2, 3):
        r = Reader(refuse={HOLE[1]})
        res = _catch_up(st, p, r)
        assert sorted({d for d, _ in r.reads}) == [HOLE[1]], r.reads
        line = next(t for _, t in res["lines"] if t.startswith(f"CATCH-UP {PC}/{BAL}:"))
        assert f"tried on {k} of 3 runs" in line
    assert "given up after 3 runs: 2026-10-28 — left to archive_backfill.py" in line
    r = Reader(refuse={HOLE[1]})
    res = _catch_up(st, p, r)
    assert r.reads == [] and not res["gaps"]
    assert ar.CATCH_UP_LEDGER and not (tmp / "cache" / ar.CATCH_UP_LEDGER).read_text().strip("{} \n")


# ============================================================================================ budgets
def test_the_day_budget_fills_a_long_gap_over_several_runs_oldest_first(world):
    st, p, _ = world
    from fetch import archive as ar
    # a 7-day gap: drop the burn leg's 4 days before the hole
    more = [str((Y - pd.Timedelta(days=i)).date()) for i in (6, 5, 4, 3)]
    for d in more:
        st.conn.execute("DELETE FROM metrics WHERE project=? AND metric IN (?, ?) AND substr(date,1,10)=?",
                        (PC, BAL, BURN, d))
    st.conn.commit()
    gap = more + HOLE
    r = Reader()
    res = _catch_up(st, p, r, max_days=3)
    assert sorted({d for d, _ in r.reads}) == gap[:3]                       # oldest first, 3 days a run
    assert any(t.startswith("CATCH-UP: day budget spent at 2026-10-26 (3 day(s) a run); 4 day(s) still to read")
               for _, t in res["lines"]), res["lines"]
    line = res["lines"][0][1]
    assert "3 day(s) filled (2026-10-23..10-25), 4 left (2026-10-26..10-29)" in line
    assert "not reached this run (budget): 2026-10-26..10-29 — the next run continues" in line
    # today's tier lands: the owed days are no longer "after the last stored day" — the ledger keeps them owed
    st.upsert(_rows(BAL, [TODAY], lambda d: 1_000_000.0 + first_block(d), "chain:bsc:burn_dead"))
    r = Reader()
    _catch_up(st, p, r, max_days=3)
    assert sorted({d for d, _ in r.reads}) == gap[3:6]
    r = Reader()
    res = _catch_up(st, p, r, max_days=3)
    assert sorted({d for d, _ in r.reads}) == gap[6:]
    assert set(gap) <= set(_days(st, BAL))                     # the stock: every owed day, at its first block
    r = Reader()
    assert not _catch_up(st, p, r, max_days=3)["gaps"] and r.reads == []
    # THE FLOW: per day where the stock's consecutive readings landed together; where today's delta had already
    # differenced across the owed days (here 10-28 -> 10-30, [span=2d]) the day inside it is NOT differenced again —
    # named STRADDLED with the rederive command (the backfill's own guard): never the same burn twice
    assert any(t.startswith("CATCH-UP: STRADDLED PancakeSwap/gross_burn_tokens: 1 backfilled day(s)") and
               "python rederive.py PancakeSwap gross_burn_tokens" in t for _, t in res["lines"]), res["lines"]
    flows = dict(st.conn.execute("SELECT substr(date,1,10), value FROM metrics WHERE project=? AND metric=? AND "
                                 "date>=?", (PC, BURN, gap[0])).fetchall())
    assert set(gap) - set(flows) == {HOLE[2]} and flows[str(TODAY.date())] == 48.0
    assert sum(flows.values()) == first_block(TODAY) - first_block(pd.Timestamp(gap[0]) - pd.Timedelta(days=1))
    assert ar.catch_up_defaults() == (10.0, 7)


def test_budget_defaults_from_the_environment(monkeypatch):
    from fetch import archive as ar
    monkeypatch.setenv("TOKEN_METRICS_CATCH_UP_MIN", "25")
    monkeypatch.setenv("TOKEN_METRICS_CATCH_UP_DAYS", "3")
    assert ar.catch_up_defaults() == (25.0, 3)
    monkeypatch.setenv("TOKEN_METRICS_CATCH_UP_MIN", "lots")
    assert ar.catch_up_defaults() == (10.0, 3)


# ============================================================================================ scope
def test_scope_every_daily_archivable_series_parked_and_weekly_left_out(tmp_path, monkeypatch):
    import store as sm
    from fetch import archive as ar
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(ar, "today", lambda: TODAY)
    st = sm.Store(tmp_path / "metrics.db")
    projects = list(config.PROJECTS)
    rows = [{"date": pd.Timestamp("2026-10-20"), "project": p["name"], "metric": m, "value": 1.0, "tier": 2,
             "source": "x"} for p in projects for m in ar.state_metrics(p)]
    st.upsert(pd.DataFrame(rows))
    gaps, excluded, checked = ar.catch_up_gaps(st, projects)
    assert set(gaps) == checked and all(v[0] == "2026-10-21" and v[-1] == str(Y.date()) for v in gaps.values())
    assert not {n for n, _ in checked} & {"Aerodrome", "World Mobile"}                 # parked: never touched
    assert all(not ar.weekly(config.PROJECT_BY_NAME[n], m) for n, m in checked)
    why = {(n, m): w for n, m, w in excluded}
    assert why[("Aerodrome", "emissions_tokens")].startswith("parked")
    assert why[("Sky", "burn_address_balance")].startswith("burn_logs is an event-log read")
    assert ("Uniswap", BAL) in checked and ("GEODNET", BAL) in checked and ("Chainlink", "noncirculating_holding_tokens") in checked


# ============================================================================================ the lock
def _hold(db):
    from runlock import RunLock
    return RunLock(db, "token_metrics run", wait_s=0)


def test_archive_backfill_run_takes_the_lock_and_a_second_writer_exits_75(tmp_path, monkeypatch, caplog):
    import archive_backfill as ab
    import runlock
    db = tmp_path / "metrics.db"
    monkeypatch.setenv("TOKEN_METRICS_LOCK_WAIT", "0")
    # a run holds metrics.db: the backfill does NOT run, exit 75, the same plain line as token_metrics.py
    with _hold(db):
        assert ab.main(["--run", "--db", str(db)]) == runlock.EXIT_LOCKED == 75
    out = caplog.text
    assert "RUN LOCKED — " in out and "this archive_backfill --run did NOT run (exit 75)" in out, out
    # the backfill HOLDS it while it works: a second writer is refused
    seen = []

    def work(a):
        with pytest.raises(runlock.RunLocked):
            with _hold(db):
                pass
        seen.append(runlock.holder(Path(f"{db}.lock")))
        return 0
    monkeypatch.setattr(ab, "_work", work)
    assert ab.main(["--run", "--db", str(db)]) == 0
    assert seen and "archive_backfill --run" in seen[0]
    # the plan reads and writes nothing: no lock
    with _hold(db):
        assert ab.main(["--db", str(db)]) == 0


def test_a_second_process_gets_exit_75_from_archive_backfill(tmp_path):
    db = tmp_path / "metrics.db"
    env = dict(os.environ, TOKEN_METRICS_LOCK_WAIT="0", PYTHONIOENCODING="utf-8")
    with _hold(db):
        p = subprocess.run([sys.executable, str(ROOT / "archive_backfill.py"), "--run", "--db", str(db)],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert p.returncode == 75, (p.returncode, p.stdout[-800:], p.stderr[-800:])
    assert "RUN LOCKED" in p.stdout and "did NOT run (exit 75)" in p.stdout


def test_token_metrics_catch_up_runs_alone_inside_its_one_lock(tmp_path, monkeypatch):
    import store as sm
    import token_metrics as tm
    from fetch import archive as ar
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("TOKEN_METRICS_LOCK_WAIT", "0")
    monkeypatch.delenv("ALCHEMY_API_KEY", raising=False)
    monkeypatch.setattr(tm, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(ar, "load_env", lambda: None)
    db = tmp_path / "metrics.db"
    monkeypatch.setattr(sm, "DB_PATH", db)
    calls = []

    def fake_catch_up(st, projects, budget_s=None, max_days=None, **kw):
        # inside the run's lock: a second writer would be refused now, and this process took it once only
        from runlock import RunLocked
        with pytest.raises(RunLocked):
            with _hold(db):
                pass
        calls.append(([p["name"] for p in projects], budget_s, max_days))
        return {"lines": [("info", "CATCH-UP: fake")], "gaps": {}, "filled": {}}
    monkeypatch.setattr(ar, "catch_up", fake_catch_up)
    monkeypatch.setattr(tm, "build_workbook", lambda *a, **k: pytest.fail("--catch-up must not build the workbook"))
    monkeypatch.setattr(tm.fetch, "fetch_all", lambda *a, **k: pytest.fail("--catch-up must not run the tiers"))
    assert tm.main(["--catch-up", "--project", PC, "--budget-min", "3", "--catch-up-days", "2"]) == 0
    assert calls == [([PC], 180.0, 2)]
    # another writer holds metrics.db: --catch-up does not run, exit 75
    with _hold(db):
        assert tm.main(["--catch-up", "--project", PC]) == 75
    assert len(calls) == 1
    # budget 0: off
    assert tm.main(["--catch-up", "--project", PC, "--budget-min", "0"]) == 0 and len(calls) == 1
    with pytest.raises(SystemExit):
        tm.parse_args(["--catch-up", "--no-fetch"])


def test_the_daily_run_catches_up_before_todays_tiers(tmp_path, monkeypatch):
    """The order inside _main: the catch-up, then the prior values today's deltas difference, then fetch_all."""
    import inspect
    import token_metrics as tm
    src = inspect.getsource(tm._main)
    i_cu, i_prior, i_fetch = (src.index("run_catch_up(st, projects, args, log, run_id)"),
                              src.index("prior_values = st.latest_values()"), src.index("out = fetch.fetch_all("))
    assert src.index("apply_run_cleanups(st, run_id, log)") < i_cu < i_prior < i_fetch
    assert src.index("alchemy_usage.reset()") < i_cu                     # its Alchemy calls count in this run


def test_daily_run_ps1_needs_no_change():
    """windows/daily_run.ps1 runs plain `token_metrics.py`: the catch-up is inside it; exit 75 is already handled."""
    ps1 = (ROOT / "windows" / "daily_run.ps1").read_text(encoding="utf-8")
    assert 'Invoke-Step "token_metrics" @("token_metrics.py")' in ps1
    assert "RUN LOCKED" in ps1 and "archive_backfill" not in ps1
