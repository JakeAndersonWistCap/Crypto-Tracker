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
    # P4-Q6: the whole 450-day window is checked (was "since its last stored day"), and every daily series is counted
    assert [t for _, t in res["lines"]] == [
        "CATCH-UP: 2 daily on-chain series checked — none has a missed day in the 450 days 2025-08-06..2026-10-29; "
        "nothing read; HOLES in the 450 days 2025-08-06..2026-10-29 (every daily series): 0 found over 0 series — "
        "0 filled, 0 left, 0 not recoverable (no history)"]


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
    # P4-Q6: the given-up day STAYS in the ledger (was: the ledger emptied) — the full-window scan would read it again
    import json
    assert json.loads((tmp / "cache" / ar.CATCH_UP_LEDGER).read_text()) == {f"{PC}|{BAL}": {"tries": {HOLE[1]: 3}}}
    r = Reader(refuse={HOLE[1]})
    assert not _catch_up(st, p, r)["gaps"] and r.reads == []


# ============================================================================================ budgets
def test_the_day_budget_fills_a_long_gap_over_several_runs_newest_first(world):
    """P4-Q6: NEWEST first (was oldest first) — the cells' Q0 window fills first; no flow is differenced across a day
    still owed, so the gap's flows land day by day (the old run's 10-28 -> 10-30 [span=2d] lump no longer arises; the
    STRADDLED guard is tested in test_round_p4_q6 on an interior hole the live delta already spans)."""
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
    assert sorted({d for d, _ in r.reads}) == gap[4:]                       # newest first, 3 days a run
    assert any(t.startswith("CATCH-UP: day budget spent at 2026-10-26 (3 day(s) a run); 4 day(s) still to read")
               for _, t in res["lines"]), res["lines"]
    line = res["lines"][0][1]
    assert "3 day(s) filled (2026-10-27..10-29), 4 left (2026-10-23..10-26)" in line
    assert "not reached this run (budget): 2026-10-23..10-26 — the next run continues" in line
    # no lump across the days still owed: 10-27's flow waits for 10-26
    assert _val(st, PC, BURN, "2026-10-27") is None and _val(st, PC, BURN, "2026-10-28")[0] == 24.0
    # today's tier lands (yesterday was filled first: today's delta is one day)
    st.upsert(_rows(BAL, [TODAY], lambda d: 1_000_000.0 + first_block(d), "chain:bsc:burn_dead"))
    r = Reader()
    _catch_up(st, p, r, max_days=3)
    assert sorted({d for d, _ in r.reads}) == gap[1:4]
    r = Reader()
    res = _catch_up(st, p, r, max_days=3)
    assert sorted({d for d, _ in r.reads}) == gap[:1]
    assert set(gap) <= set(_days(st, BAL))                     # the stock: every owed day, at its first block
    r = Reader()
    assert not _catch_up(st, p, r, max_days=3)["gaps"] and r.reads == []
    # THE FLOW: every day its own 24 blocks, today's one day — nothing straddled, nothing twice
    assert not any("STRADDLED" in t for _, t in res["lines"]), res["lines"]
    flows = dict(st.conn.execute("SELECT substr(date,1,10), value FROM metrics WHERE project=? AND metric=? AND "
                                 "date>=?", (PC, BURN, gap[0])).fetchall())
    assert set(flows) == set(gap) | {str(TODAY.date())} and set(flows.values()) == {24.0}
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
    # P4-Q6: newest first (was oldest first)
    assert set(gaps) == checked and all(v[-1] == "2026-10-21" and v[0] == str(Y.date()) for v in gaps.values())
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


# ============================================================================================ A4: the API series first
# Jake's addendum A4 (2026-11-02): his live case — Ethereum's price_usd missing 2026-10-30..11-01, so the P2P leg
# (p2p_transfer_volume_usd, valued at the STORED same-day price) and everything else valued at a price stayed holes, and
# `--catch-up` alone never read a price. The catch-up now fills the daily API series that are not re-read in full each
# run (CoinGecko's market_chart: price, market cap, volume, implied supply) from the adapter's own read, BEFORE the
# on-chain days, then replays the run's derivations for the caught-up days. A fake CoinGecko host answers market_chart
# with price(d) = 2,000 + day-of-month; no network.
ETH, PX, MC, VOL, P2P = "Ethereum", "price_usd", "market_cap_usd", "volume_usd", "p2p_transfer_volume_usd"
P2P_SRC = "eth_settlement:bigquery:p2p[Artemis method, rebuilt]"
ETH_MOVED = 1000                                   # ETH moved peer-to-peer each cached day (the BigQuery answer)


def price(d) -> float:
    return 2000.0 + pd.Timestamp(d).day


class FakeCoinGeckoHttp:
    """market_chart over the last `days` days at 00:00 UTC, plus a "now" point today. `skip` days are not served."""
    min_interval = 0.0

    def __init__(self, skip=(), fail=False):
        self.calls, self.skip, self.fail = [], set(skip), fail

    def get(self, url, params=None, headers=None):
        self.calls.append((url, dict(params or {})))
        if self.fail:
            raise RuntimeError("HTTP 429 Too Many Requests for https://api.coingecko.com/api/v3/coins/ethereum/"
                               "market_chart")
        days = int(params["days"])
        pts = [TODAY - pd.Timedelta(days=i) for i in range(days, -1, -1)]
        pts = [d for d in pts if str(d.date()) not in self.skip]
        ms = [int(d.tz_localize("UTC").timestamp() * 1000) for d in pts]
        if pts and pts[-1] == TODAY:
            ms[-1] += 9 * 3600 * 1000                       # today's point is "now", not 00:00
        return {"prices": [[t, price(d)] for t, d in zip(ms, pts)],
                "market_caps": [[t, price(d) * 120e6] for t, d in zip(ms, pts)],
                "total_volumes": [[t, 1e9] for t in ms]}


def _coingecko(**kw):
    from fetch.coingecko import CoinGecko
    cg = CoinGecko()
    cg.http = FakeCoinGeckoHttp(**kw)
    return cg


def _eth_valuer(tmp):
    from fetch.eth_settlement import EthP2PBigQuery
    return lambda sl: EthP2PBigQuery(stored_long=sl, cache_file=tmp / "near-bq.json")


@pytest.fixture
def eth_world(world):
    """The PancakeSwap world, plus Ethereum's CoinGecko series as the daily run left them — price and market cap with the
    same 3-day hole to yesterday, volume complete — the P2P leg valued on every day that has a price, and the BigQuery
    answers cached for every day (the weekly turn read them; the valuation waits on the price)."""
    import json
    st, p, tmp = world
    full = [Y - pd.Timedelta(days=i) for i in range(29, -1, -1)]
    held = [d for d in full if str(d.date()) not in HOLE]
    rows = [pd.DataFrame({"date": pd.to_datetime(days), "project": ETH, "metric": m, "value": [f(d) for d in days],
                          "tier": 1, "source": s})
            for m, days, f, s in ((PX, held, price, "coingecko"), (MC, held, lambda d: price(d) * 120e6, "coingecko"),
                                  (VOL, full, lambda d: 1e9, "coingecko"),
                                  (P2P, held, lambda d: ETH_MOVED * price(d), P2P_SRC))]
    st.upsert(pd.concat(rows))
    (tmp / "near-bq.json").write_text(json.dumps({"eth_p2p": {
        "days": {str(d.date()): {"ETH": str(ETH_MOVED * 10 ** 18)} for d in full}, "prices": {}}}))
    return st, [p, config.PROJECT_BY_NAME[ETH]], tmp


def _val(st, project, metric, day):
    r = st.conn.execute("SELECT value, source FROM metrics WHERE project=? AND metric=? AND substr(date,1,10)=?",
                        (project, metric, day)).fetchone()
    return r if r is None else (r[0], r[1])


def ar_catch_up(st, projects, reader, chain="bsc", **kw):
    from fetch import archive as ar
    kw.setdefault("budget_s", 120)
    kw.setdefault("max_days", 7)
    return ar.catch_up(st, projects, reader=reader, archive_chains={chain}, **kw)


class OrderedReader(Reader):
    """The fake BSC, noting which missed prices the store held when the first on-chain day was read."""

    def __init__(self, st, **kw):
        super().__init__(**kw)
        self.st, self.prices_at_first_read = st, None

    def scaled(self, chain, address, call, *args, **kw):
        if self.prices_at_first_read is None:
            self.prices_at_first_read = [d for d in HOLE if _val(self.st, ETH, PX, d)]
        return super().scaled(chain, address, call, *args, **kw)


def test_a4_the_price_hole_is_filled_before_the_onchain_days_and_the_p2p_leg_is_stored_for_them(eth_world):
    st, projects, tmp = eth_world
    cg, r = _coingecko(), OrderedReader(st)
    res = ar_catch_up(st, projects, r, api=cg, valuers=[_eth_valuer(tmp)])
    # THE ORDER: every missed price was stored before the archive read its first on-chain day
    assert r.prices_at_first_read == HOLE
    # ONE CoinGecko call for Ethereum, back to the oldest missed day, through the adapter's own market_chart
    assert [(u.rsplit("/", 2)[-2], q["days"], q["interval"]) for u, q in cg.http.calls] == [("ethereum", "4", "daily")]
    # the series' own source string, each day's own 00:00 price; today's "now" point is not a missed day
    for d in HOLE:
        assert _val(st, ETH, PX, d) == (price(d), "coingecko")
        assert _val(st, ETH, MC, d) == (price(d) * 120e6, "coingecko")
    assert _val(st, ETH, PX, str(TODAY.date())) is None
    # THE DERIVED SERIES for those days: the P2P leg, valued at the stored same-day price (the adapter's own valuation)
    for d in HOLE:
        assert _val(st, ETH, P2P, d) == (ETH_MOVED * price(d), P2P_SRC)
    # the on-chain catch-up ran as before
    assert set(HOLE) <= set(_days(st, BAL))
    lines = [t for _, t in res["lines"]]
    assert lines[0] == (f"CATCH-UP {ETH}/{MC}: 3 day(s) filled (2026-10-27..10-29), 0 left — CoinGecko market_chart "
                        f"for ethereum, the series' own read (coingecko)"), lines
    assert lines[1] == (f"CATCH-UP {ETH}/{PX}: 3 day(s) filled (2026-10-27..10-29), 0 left — CoinGecko market_chart "
                        f"for ethereum, the series' own read (coingecko)"), lines
    assert lines[2].startswith(f"CATCH-UP {PC}/{BAL}: 3 day(s) filled (2026-10-27..10-29)"), lines
    assert not [t for t in lines if f"{ETH}/{VOL}" in t]                    # no missed day: no line
    p2p = next(t for t in lines if t.startswith(f"CATCH-UP {ETH}/{P2P}:"))
    assert p2p.startswith(f"CATCH-UP {ETH}/{P2P}: 3 day(s) derived (2026-10-27..10-29) — eth_settlement:bigquery:p2p")
    summary = lines[-1]
    assert summary.startswith("CATCH-UP: 1 of 2 daily on-chain series had missed days — 3 day(s) filled, 0 left"), summary
    assert ("; API: 2 of 3 daily API series had missed days — 6 day(s) filled, 0 left, 0 hole(s) named "
            "(CoinGecko market_chart)") in summary, summary
    assert "; derived: " in summary and res["lines"][-1][0] == "info"
    # A SECOND RUN reads nothing and writes nothing
    cg2 = _coingecko()
    res2 = ar_catch_up(st, projects, Reader(), api=cg2, valuers=[_eth_valuer(tmp)])
    assert cg2.http.calls == [] and not res2["api"]["gaps"]
    assert "; API: 3 daily API series checked — none has a missed day" in res2["lines"][-1][1]


def test_a4_a_day_coingecko_does_not_serve_stays_a_named_hole_then_fills_when_served(eth_world):
    st, projects, tmp = eth_world
    res = ar_catch_up(st, projects, Reader(), api=_coingecko(skip={HOLE[1]}), valuers=[_eth_valuer(tmp)])
    assert _val(st, ETH, PX, HOLE[1]) is None and _val(st, ETH, P2P, HOLE[1]) is None     # never 0, never carried
    line = next(t for lv, t in res["lines"] if t.startswith(f"CATCH-UP {ETH}/{PX}:"))
    assert line.startswith(f"CATCH-UP {ETH}/{PX}: 2 day(s) filled (2026-10-27, 2026-10-29), 1 left (2026-10-28)")
    assert ("HOLE(S) 2026-10-28 — CoinGecko's market_chart served no point for the day (never 0, never carried; "
            "tried on 1 of 3 runs)") in line, line
    assert res["lines"][-1][0] == "warning" and "2 hole(s) named (CoinGecko market_chart)" in res["lines"][-1][1]   # price and market cap
    # RESUMABLE: the next run asks only from the hole, and fills it (and its P2P day) once CoinGecko serves it
    cg = _coingecko()
    ar_catch_up(st, projects, Reader(), api=cg, valuers=[_eth_valuer(tmp)])
    assert [q["days"] for _, q in cg.http.calls] == ["3"]
    assert _val(st, ETH, PX, HOLE[1]) == (price(HOLE[1]), "coingecko")
    assert _val(st, ETH, P2P, HOLE[1]) == (ETH_MOVED * price(HOLE[1]), P2P_SRC)


def test_a4_a_failed_read_names_the_error_masked_and_keeps_the_days_owed(eth_world, monkeypatch):
    st, projects, tmp = eth_world
    monkeypatch.setenv("COINGECKO_API_KEY", "CG-secret-demo-key-1234")
    res = ar_catch_up(st, projects, Reader(), api=_coingecko(fail=True), valuers=[_eth_valuer(tmp)])
    line = next(t for _, t in res["lines"] if t.startswith(f"CATCH-UP {ETH}/{PX}:"))
    assert "the market_chart read failed: RuntimeError: HTTP 429" in line and "CG-secret" not in line
    assert not [d for d in HOLE if _val(st, ETH, PX, d)]
    ar_catch_up(st, projects, Reader(), api=_coingecko(), valuers=[_eth_valuer(tmp)])
    assert [d for d in HOLE if _val(st, ETH, PX, d)] == HOLE


def test_a4_budgets_the_time_budget_and_the_day_budget_resume_on_the_next_run(eth_world):
    st, projects, tmp = eth_world
    # no time left: nothing read, every day still owed — said, not a hole
    cg = _coingecko()
    res = ar_catch_up(st, projects, Reader(), api=cg, valuers=[_eth_valuer(tmp)], budget_s=0)
    assert cg.http.calls == [] and not [d for d in HOLE if _val(st, ETH, PX, d)]
    line = next(t for _, t in res["lines"] if t.startswith(f"CATCH-UP {ETH}/{PX}:"))
    assert line.endswith("0 day(s) filled, 3 left (2026-10-27..10-29) — CoinGecko market_chart for ethereum, the "
                         "series' own read (coingecko); not reached this run (budget): 2026-10-27..10-29 — the next "
                         "run continues"), line
    # today's tier lands meanwhile: the ledger keeps the days owed though they are no longer after the last stored day
    st.upsert(pd.DataFrame({"date": [TODAY], "project": ETH, "metric": PX, "value": [price(TODAY)], "tier": 1,
                            "source": "coingecko"}))
    # the day budget: 2 days a series a run, NEWEST first (P4-Q6; was oldest first); the third on the next run
    res = ar_catch_up(st, projects, Reader(), api=_coingecko(), valuers=[_eth_valuer(tmp)], max_days=2)
    assert [d for d in HOLE if _val(st, ETH, PX, d)] == HOLE[1:]
    line = next(t for _, t in res["lines"] if t.startswith(f"CATCH-UP {ETH}/{PX}:"))
    assert "2 day(s) filled (2026-10-28..10-29), 1 left (2026-10-27)" in line
    assert "not reached this run (budget): 2026-10-27 — the next run continues" in line
    ar_catch_up(st, projects, Reader(), api=_coingecko(), valuers=[_eth_valuer(tmp)], max_days=2)
    assert [d for d in HOLE if _val(st, ETH, PX, d)] == HOLE
    assert [d for d in HOLE if _val(st, ETH, P2P, d)] == HOLE


def test_a4_never_from_another_measuring_point(eth_world):
    """A series whose newest stored row was read elsewhere is not filled from CoinGecko (rule 10): the line says so."""
    st, projects, tmp = eth_world
    last = str((Y - pd.Timedelta(days=3)).date())
    st.conn.execute("UPDATE metrics SET source='defillama_coins:history[coins.llama.fi coingecko:ethereum]' WHERE "
                    "project=? AND metric=? AND substr(date,1,10)=?", (ETH, PX, last))
    st.conn.commit()
    res = ar_catch_up(st, projects, Reader(), api=_coingecko(), valuers=[_eth_valuer(tmp)])
    assert not [d for d in HOLE if _val(st, ETH, PX, d)]
    line = next(t for lv, t in res["lines"] if t.startswith(f"CATCH-UP {ETH}/{PX}:"))
    assert "NOT FILLED: its newest stored row (2026-10-26) was read from defillama_coins:history" in line, line
    assert "(rule 10)" in line and [d for d in HOLE if _val(st, ETH, MC, d)] == HOLE      # market cap: its own basis


# ============================================================================================ A4: derived issuance
UNI, TS = "Uniswap", "total_supply_gross"


class GrowingReader(Reader):
    """The fake Ethereum: totalSupply = 1e9 + 10 x the block (240 a day: one block an hour); the dead address as above."""

    def scaled(self, chain, address, call, *args, **kw):
        v = super().scaled(chain, address, call, *args, **kw)
        return 1e9 + 10.0 * self.at_block[chain] if not args else v


@pytest.fixture
def uni_world(tmp_path, monkeypatch):
    """Uniswap as the daily run leaves it over 60 days: total_supply_gross read on chain, gross_issuance_tokens its daily
    change (history_derive: d(total_supply_gross)), the burn leg beside it; the last 3 days to yesterday missed by
    every series."""
    import store as sm
    from fetch import archive as ar
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(ar, "today", lambda: TODAY)
    p = dict(config.PROJECT_BY_NAME[UNI])
    p["contracts"] = {k: v for k, v in p["contracts"].items() if k in ("token", "burn_dead")}
    monkeypatch.setitem(config.PROJECT_BY_NAME, UNI, p)
    full = [Y - pd.Timedelta(days=i) for i in range(59, -1, -1)]
    held = [d for d in full if str(d.date()) not in HOLE]
    st = sm.Store(tmp_path / "metrics.db")

    def rows(metric, days, value, source):
        return pd.DataFrame({"date": pd.to_datetime(days), "project": UNI, "metric": metric,
                             "value": [float(value(d)) for d in days], "tier": 2, "source": source})
    st.upsert(pd.concat([
        rows(TS, held, lambda d: 1e9 + 10.0 * first_block(d), "chain:ethereum:token"),
        rows(ISS, held[1:], lambda d: 240.0, "derived:d_supply_gross"),
        rows(BAL, held, lambda d: 1_000_000.0 + first_block(d), "chain:ethereum:burn_dead"),
        rows(BURN, held[1:], lambda d: 24.0, "chain:ethereum:burn_dead:delta"),
    ]))
    return st, p


def _issuance_delta_30d(st, asof):
    """Issuance Δ30d as the build forms it for a flow: the per-covered-day rate over the last 30 days against the 30
    before, each window's covered days (build_workbook._window_coverage minus _interior_holes), BLOCKED where a side
    covers fewer than RATE_MIN_DAYS or the two differ by more than a tenth of the window (rule 9). Returns (Δ or the
    BLOCKED text, each window's covered days, the days of the last 30 with no issuance row)."""
    import build_workbook as bw
    long = pd.read_sql("SELECT date, project, metric, value, source FROM metrics WHERE project=?", st.conn,
                       params=(UNI,))
    long["date"] = pd.to_datetime(long["date"]).dt.normalize()
    groups = {k: g.sort_values("date") for k, g in long.groupby(["project", "metric"])}
    g = groups[(UNI, ISS)]
    s = g.set_index("date")["value"]
    cov = bw._covered_day_sets(groups)[(UNI, ISS)]
    side = {}
    for w, (a, b) in {"now": (asof - pd.Timedelta(days=30), asof),
                      "m1": (asof - pd.Timedelta(days=60), asof - pd.Timedelta(days=30))}.items():
        n, _ = bw._window_coverage(s, a, b, bw._differenced_first(g))
        holes = bw._interior_holes(cov, a, b, s.index.min())
        side[w] = (n - len(holes), float(s[(s.index > a) & (s.index <= b)].sum()))
    (cn, sn), (cm, sm_) = side["now"], side["m1"]
    missing = [str(d.date()) for d in pd.date_range(asof - pd.Timedelta(days=29), asof) if d not in s.index]
    if min(cn, cm) < bw.RATE_MIN_DAYS or abs(cn - cm) > 3:
        return f"BLOCKED — unequal coverage: now covers {cn}, m1 covers {cm}", (cn, cm), missing
    return (sn / cn) / (sm_ / cm) - 1, (cn, cm), missing


def ar_catch_up_eth(st, projects, reader, **kw):
    kw.setdefault("valuers", [])
    return ar_catch_up(st, projects, reader, chain="ethereum", api=_coingecko(), **kw)


def test_a4_issuance_is_derived_for_the_caught_up_days_and_the_30d_comparison_is_a_number(uni_world):
    st, p = uni_world
    _, cov, missing = _issuance_delta_30d(st, Y)
    assert missing == HOLE                                         # the issuance leg's missed days
    res = ar_catch_up_eth(st, [p], GrowingReader())
    # the stock, at each day's first block (P3-4), then its DERIVED change for those days (A4: the run's own derivation)
    for d in HOLE:
        assert _val(st, UNI, TS, d) == (1e9 + 10.0 * first_block(d), "chain:ethereum:token:archive")
        v, src = _val(st, UNI, ISS, d)
        assert v == 240.0 and src.startswith("derived:d_supply_gross"), (d, v, src)
    after, cov, missing = _issuance_delta_30d(st, Y)
    assert missing == [] and isinstance(after, float) and abs(after) < 1e-12, (after, cov)
    line = next(t for _, t in res["lines"] if t.startswith(f"CATCH-UP {UNI}/{ISS}:"))
    assert line.startswith(f"CATCH-UP {UNI}/{ISS}: 3 day(s) derived (2026-10-27..10-29) — derived:d_supply_gross"), line
    assert "; derived: " in res["lines"][-1][1]
    # replayed on the next runs (an input can land later), writing nothing twice; after 3 runs the ledger is empty
    from fetch import archive as ar
    for _ in range(3):
        res = ar_catch_up_eth(st, [p], GrowingReader())
        assert not [t for _, t in res["lines"] if " day(s) derived " in t]
    assert not ar._read_json(ar.DERIVE_LEDGER)


def test_a4_jakes_store_the_stock_was_caught_up_without_its_issuance_and_the_price_was_missed(uni_world):
    """Jake's store on 2026-11-02: P3-4's `--catch-up` had filled total_supply_gross for the missed days but derived
    nothing from it, and no plain run has run since, so the price is missing too. A4's price catch-up brings Uniswap's
    days back, and the issuance is derived for them from the stored stock."""
    st, p = uni_world
    st.upsert(pd.DataFrame({"date": pd.to_datetime(HOLE), "project": UNI, "metric": TS,
                            "value": [1e9 + 10.0 * first_block(d) for d in HOLE], "tier": 2,
                            "source": "chain:ethereum:token:archive"}))
    full = [Y - pd.Timedelta(days=i) for i in range(29, -1, -1)]
    st.upsert(pd.DataFrame({"date": [d for d in full if str(d.date()) not in HOLE], "project": UNI, "metric": PX,
                            "value": 7.0, "tier": 1, "source": "coingecko"}))
    assert _issuance_delta_30d(st, Y)[2] == HOLE
    r = GrowingReader()
    res = ar_catch_up_eth(st, [p], r)
    assert r.reads and {c for _, c in r.reads} == {"balanceOf"}            # the stock is not read again
    assert [d for d in HOLE if _val(st, UNI, PX, d)] == HOLE
    assert [_val(st, UNI, ISS, d)[0] for d in HOLE] == [240.0] * 3
    delta, cov, missing = _issuance_delta_30d(st, Y)
    assert missing == [] and isinstance(delta, float) and abs(delta) < 1e-12, (delta, cov)
    assert any(t.startswith(f"CATCH-UP {UNI}/{ISS}: 3 day(s) derived") for _, t in res["lines"]), res["lines"]


def test_a4_skys_declared_zero_issuance_is_written_for_the_caught_up_days_never_a_supply_delta(tmp_path, monkeypatch):
    """Sky's issuance is the declared schedule's zero from 2026-10-06 (config handover: d(supply) before it). The schedule
    tier writes it for the run's own day only, so a missed day held no issuance row. The catch-up writes the declaration
    for the caught-up days (Schedule, for that day) — and never a d(supply) row inside the declared leg."""
    import store as sm
    from fetch import archive as ar
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(ar, "today", lambda: TODAY)
    sky = config.PROJECT_BY_NAME["Sky"]
    st = sm.Store(tmp_path / "metrics.db")
    full = [Y - pd.Timedelta(days=i) for i in range(19, -1, -1)]
    held = [d for d in full if str(d.date()) not in HOLE]
    st.upsert(pd.concat([
        pd.DataFrame({"date": held, "project": "Sky", "metric": PX, "value": 0.07, "tier": 1, "source": "coingecko"}),
        pd.DataFrame({"date": full, "project": "Sky", "metric": "total_supply", "value": 23.4e9, "tier": 2,
                      "source": "chain:ethereum:token"}),
        pd.DataFrame({"date": held, "project": "Sky", "metric": ISS, "value": 0.0, "tier": 1,
                      "source": "schedule:config:declared"}),
    ]))
    res = ar.catch_up(st, [sky], budget_s=120, max_days=7, reader=Reader(), archive_chains={"ethereum"},
                      api=_coingecko(), valuers=[])
    assert [_val(st, "Sky", ISS, d) for d in HOLE] == [(0.0, "schedule:config:declared")] * 3
    assert any(t.startswith(f"CATCH-UP Sky/{ISS}: 3 day(s) derived (2026-10-27..10-29) — schedule:config")
               for _, t in res["lines"]), res["lines"]


def test_a4_a_derived_day_inside_a_stored_multi_day_row_is_not_derived_again(uni_world):
    """Today's run already wrote issuance across the missed days as one [span=4d] row: the caught-up days inside it are
    not derived again (never counted twice), and the line says so."""
    st, p = uni_world
    st.upsert(pd.DataFrame({"date": [TODAY], "project": UNI, "metric": ISS, "value": [960.0], "tier": 2,
                            "source": "derived:d_supply_gross[span=4d]"}))
    res = ar_catch_up_eth(st, [p], GrowingReader())
    assert not [d for d in HOLE if _val(st, UNI, ISS, d)]
    assert any(t.startswith(f"CATCH-UP: STRADDLED {UNI}/{ISS}: 2026-10-27..10-29 lie inside the stored multi-day row "
                            f"dated 2026-10-30") for _, t in res["lines"]), res["lines"]


# ============================================================================================ A4: inside the run's lock
def test_a4_token_metrics_catch_up_reads_the_prices_inside_its_one_lock(tmp_path, monkeypatch):
    import store as sm
    import token_metrics as tm
    from fetch import archive as ar
    from fetch.coingecko import CoinGecko
    from runlock import RunLocked
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("TOKEN_METRICS_LOCK_WAIT", "0")
    monkeypatch.delenv("ALCHEMY_API_KEY", raising=False)
    monkeypatch.setattr(tm, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(ar, "load_env", lambda: None)
    monkeypatch.setattr(ar, "today", lambda: TODAY)
    monkeypatch.setattr(ar, "default_valuers", lambda: [])
    db = tmp_path / "metrics.db"
    monkeypatch.setattr(sm, "DB_PATH", db)
    full = [Y - pd.Timedelta(days=i) for i in range(9, -1, -1)]
    st = sm.Store(db)
    st.upsert(pd.DataFrame({"date": [d for d in full if str(d.date()) not in HOLE], "project": ETH, "metric": PX,
                            "value": 2000.0, "tier": 1, "source": "coingecko"}))
    st.close()
    seen = []
    real = CoinGecko.market_chart

    def market_chart(self, cid, name, days):
        with pytest.raises(RunLocked):                     # another writer is refused while the catch-up reads
            with _hold(db):
                pass
        seen.append((cid, days))
        self.http = FakeCoinGeckoHttp()
        return real(self, cid, name, days)
    monkeypatch.setattr(CoinGecko, "market_chart", market_chart)
    monkeypatch.setattr(tm, "build_workbook", lambda *a, **k: pytest.fail("--catch-up must not build the workbook"))
    monkeypatch.setattr(tm.fetch, "fetch_all", lambda *a, **k: pytest.fail("--catch-up must not run the tiers"))
    assert tm.main(["--catch-up", "--project", ETH]) == 0
    assert seen == [("ethereum", 4)]
    st = sm.Store(db)
    assert [d for d in HOLE if _val(st, ETH, PX, d)] == HOLE
    st.close()
