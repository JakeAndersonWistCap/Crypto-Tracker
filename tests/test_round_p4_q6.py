"""ROUND P4-Q6 (Jake, 2026-11-03): THE ALWAYS-BACKFILL CATCH-UP. P3-4 filled only the days after a series' last stored day;
a day missed in the MIDDLE of a series (a run that stored today after a gap, a refused read) stayed a hole. Every
normal run now scans every daily series across the longest window any cell reads (W = 450 days: the trajectory's Q0..Q-4,
five 90-day blocks; Y1 is 365) for missing days ANYWHERE, newest first, and fills them by the series' own route:
on-chain by an archive read at the day's first block, CoinGecko by market_chart over the range, an adapter that keeps its
own day cache (GEODNET's balance flow) by its own read, the API series a tier can read back by today's tier; a page
scrape's days are named not recoverable in the cell comment. Budgeted, resumable, inside the run's lock.

Fakes only: PancakeSwap's burn leg on a fake BSC (one block an hour; the dead address holds 1,000,000 + the block, so a
value names the block it was read at), a fake CoinGecko, a fake balance-flow read. No network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

PC, BAL, BURN, TSG = "PancakeSwap", "burn_address_balance", "gross_burn_tokens", "total_supply_gross"
ETH, PX, MC, VOL = "Ethereum", "price_usd", "market_cap_usd", "volume_usd"
GEO, REL = "GEODNET", "pool_release_tokens"
TODAY = pd.Timestamp("2026-11-03")
Y = TODAY - pd.Timedelta(days=1)
T0 = int((TODAY - pd.Timedelta(days=60)).tz_localize("UTC").timestamp())    # block 0
HOLE = ["2026-10-20", "2026-10-21", "2026-10-22"]          # INTERIOR: rows stored before AND after


def first_block(day) -> int:
    return int((pd.Timestamp(day).tz_localize("UTC").timestamp() - T0) // 3600)


def bal(d) -> float:
    return 1_000_000.0 + first_block(d)


class Eth:
    block_number = 61 * 24

    def get_block(self, n):
        return {"timestamp": T0 + n * 3600}

    def get_balance(self, a, block_identifier=None):
        return 0

    def get_code(self, a, block_identifier=None):
        return b"\x60"


class Reader:
    """The fake BSC: totalSupply 400m; the dead address holds 1,000,000 + the pinned block. `refuse` days raise."""

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


def _rows(project, metric, days, value, source, tier=2):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": project, "metric": metric,
                         "value": [float(value(d)) for d in days], "tier": tier, "source": source})


def _days(n: int, end=Y) -> list:
    return [end - pd.Timedelta(days=i) for i in range(n - 1, -1, -1)]


def _held(st, project, metric) -> dict:
    return dict(st.conn.execute("SELECT substr(date,1,10), value FROM metrics WHERE project=? AND metric=?",
                                (project, metric)).fetchall())


def _val(st, project, metric, day):
    r = st.conn.execute("SELECT value, source FROM metrics WHERE project=? AND metric=? AND substr(date,1,10)=?",
                        (project, metric, day)).fetchone()
    return r if r is None else (r[0], r[1])


@pytest.fixture
def world(tmp_path, monkeypatch):
    """PancakeSwap's burn leg read daily for 40 days to yesterday — an INTERIOR 3-day hole (10-20..10-22) with rows on
    both sides; total supply complete. Its flow is missing on the hole days and on 10-23 (no earlier reading to
    difference)."""
    import store as sm
    from fetch import archive as ar
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(ar, "today", lambda: TODAY)
    p = dict(config.PROJECT_BY_NAME[PC])
    p["contracts"] = {k: v for k, v in p["contracts"].items() if k in ("token", "burn_dead")}
    monkeypatch.setitem(config.PROJECT_BY_NAME, PC, p)
    full = _days(40)
    held = [d for d in full if str(d.date()) not in HOLE]
    st = sm.Store(tmp_path / "metrics.db")
    st.upsert(pd.concat([
        _rows(PC, BAL, held, bal, "chain:bsc:burn_dead"),
        _rows(PC, BURN, [d for d in held[1:] if str(d.date()) != "2026-10-23"], lambda d: 24.0,
              "chain:bsc:burn_dead:delta"),
        _rows(PC, TSG, full, lambda d: 400e6, "chain:bsc:token"),
    ]))
    return st, p, tmp_path


def _catch_up(st, projects, reader, **kw):
    from fetch import archive as ar
    kw.setdefault("budget_s", 120)
    kw.setdefault("max_days", 7)
    kw.setdefault("valuers", [])
    return ar.catch_up(st, projects, reader=reader, archive_chains={"bsc"}, **kw)


# ============================================================================================ the window
def test_the_window_is_the_longest_any_cell_reads_450_days():
    import build_workbook as bw
    from fetch import archive as ar
    # Q0..Q-4: five 90-day blocks (the trajectory's fifth quarter ends 360 days back); Y1 is 365; never under 400
    assert len(bw.QUARTERS) * bw.GLOBALS["period_days"] == 450 == ar.catch_up_window_days()
    assert ar.catch_up_window_days() >= 400 and ar.catch_up_window_days() <= ar.ARCHIVE_DAYS


# ============================================================================================ an interior hole, on chain
def test_an_interior_hole_is_found_and_filled_at_each_days_first_block(world):
    st, p, _ = world
    from fetch import archive as ar
    gaps, _, _ = ar.catch_up_gaps(st, [p])
    assert gaps == {(PC, BAL): HOLE[::-1]}                  # NOT after the last stored day (yesterday is held); newest first
    r = Reader()
    res = _catch_up(st, [p], r)
    got = {d: v for d, v in _held(st, PC, BAL).items() if d in HOLE}
    assert got == {d: bal(d) for d in HOLE}                 # each value names the day's first block
    assert {_val(st, PC, BAL, d)[1] for d in HOLE} == {"chain:bsc:burn_dead:archive"}
    assert sorted({d for d, _ in r.reads}) == HOLE          # only the hole was read
    # the flow, day by day (24 blocks a day), across the hole and the day after it — never a lump, never 0
    flows = {d: v for d, v in _held(st, PC, BURN).items() if "2026-10-20" <= d <= "2026-10-23"}
    assert flows == {"2026-10-20": 24.0, "2026-10-21": 24.0, "2026-10-22": 24.0, "2026-10-23": 24.0}
    lines = [t for _, t in res["lines"]]
    assert lines[0] == (f"CATCH-UP {PC}/{BAL}: 3 day(s) filled (2026-10-20..10-22), 0 left — archive read at each "
                        f"day's first block via bsc; 4 {BURN} row(s) differenced from it"), lines
    summary = lines[-1]
    assert summary.startswith("CATCH-UP: 1 of 2 daily on-chain series had missed days — 3 day(s) filled, 0 left, "
                              "0 hole(s) named; 3 day(s) read in "), summary
    # the summary counts every daily series' holes: the stock's 3 and its flow's 4, all filled
    assert summary.endswith("; HOLES in the 450 days 2025-08-10..2026-11-02 (every daily series): 7 found over 2 "
                            "series — 7 filled, 0 left, 0 not recoverable (no history)"), summary
    # a second run reads nothing
    r2 = Reader()
    res2 = _catch_up(st, [p], r2)
    assert r2.reads == [] and not res2["gaps"]


def test_an_interior_hole_inside_a_stored_multi_day_delta_is_not_counted_twice(world):
    """The STRADDLED guard: the live delta on 10-23 already differenced across the hole ([span=4d]); the stock's days
    are filled, the flow's days inside that row are NOT differenced again (never the same burn twice)."""
    st, p, _ = world
    st.upsert(_rows(PC, BURN, [pd.Timestamp("2026-10-23")], lambda d: 96.0, "chain:bsc:burn_dead:delta[span=4d]"))
    from fetch import archive as ar
    scan = ar.scan_holes(st, [p], {(PC, BAL), (PC, TSG)})
    assert scan == {(PC, BAL): {"route": "archive", "source": "chain:bsc:burn_dead", "days": HOLE[::-1]}}   # flow: held
    res = _catch_up(st, [p], Reader())
    assert {d for d in _held(st, PC, BAL)} >= set(HOLE)
    flows = {d: v for d, v in _held(st, PC, BURN).items() if "2026-10-20" <= d <= "2026-10-23"}
    assert flows == {"2026-10-23": 96.0}
    assert any(t.startswith(f"CATCH-UP: STRADDLED {PC}/{BURN}: 3 backfilled day(s)") and
               f"python rederive.py {PC} {BURN}" in t for _, t in res["lines"]), res["lines"]


# ============================================================================================ an interior hole, CoinGecko
def price(d) -> float:
    return 2000.0 + pd.Timestamp(d).day


class FakeCoinGeckoHttp:
    """market_chart over the last `days` days at 00:00 UTC plus a "now" point today."""
    min_interval = 0.0

    def __init__(self):
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append((url, dict(params or {})))
        days = int(params["days"])
        pts = [TODAY - pd.Timedelta(days=i) for i in range(days, -1, -1)]
        ms = [int(d.tz_localize("UTC").timestamp() * 1000) for d in pts]
        ms[-1] += 9 * 3600 * 1000
        return {"prices": [[t, price(d)] for t, d in zip(ms, pts)],
                "market_caps": [[t, price(d) * 120e6] for t, d in zip(ms, pts)],
                "total_volumes": [[t, 1e9] for t in ms]}


def test_an_api_interior_hole_is_filled_through_market_chart_over_the_range(world):
    st, p, _ = world
    from fetch.coingecko import CoinGecko
    full = _days(30)
    held = [d for d in full if str(d.date()) not in HOLE]
    st.upsert(pd.concat([_rows(ETH, PX, held, lambda d: 1.0, "coingecko", 1),        # 1.0: a value never re-read
                         _rows(ETH, MC, held, lambda d: 1.0, "coingecko", 1),
                         _rows(ETH, VOL, full, lambda d: 1e9, "coingecko", 1)]))
    cg = CoinGecko()
    cg.http = FakeCoinGeckoHttp()
    res = _catch_up(st, [p, config.PROJECT_BY_NAME[ETH]], Reader(), api=cg)
    # ONE call, back to the hole's oldest day (11-03 - 10-20 = 14 days, + today): the range, not "since the last day"
    assert [(u.rsplit("/", 2)[-2], q["days"]) for u, q in cg.http.calls] == [("ethereum", "15")]
    for d in HOLE:
        assert _val(st, ETH, PX, d) == (price(d), "coingecko")
        assert _val(st, ETH, MC, d) == (price(d) * 120e6, "coingecko")
    # NEVER AN OVERWRITE: the days held keep their stored value
    assert {v for d, v in _held(st, ETH, PX).items() if d not in HOLE} == {1.0}
    line = next(t for _, t in res["lines"] if t.startswith(f"CATCH-UP {ETH}/{PX}:"))
    assert line == (f"CATCH-UP {ETH}/{PX}: 3 day(s) filled (2026-10-20..10-22), 0 left — CoinGecko market_chart for "
                    f"ethereum, the series' own read (coingecko)"), line


# ============================================================================================ a page scrape: not recoverable
SCRAPED = ("Fluid", "fees_usd")


def _scraped_long(days, hole):
    n, m = SCRAPED
    keep = [d for d in days if str(d.date()) not in hole]
    return pd.DataFrame({"date": keep, "project": n, "metric": m, "value": 1000.0, "tier": 3,
                         "source": "scrape:dashboard.example.org", "is_manual": False, "entered_on": "",
                         "source_note": ""})


def test_a_page_scrape_hole_is_named_not_recoverable_in_the_log_and_the_cell_comment(world):
    st, p, _ = world
    import build_workbook as bw
    from fetch import archive as ar
    n, m = SCRAPED
    hole = ["2026-10-10", "2026-10-11"]
    long = _scraped_long(_days(120), hole)
    st.upsert(long.drop(columns=["is_manual", "entered_on", "source_note"]))
    res = _catch_up(st, [p, config.PROJECT_BY_NAME[n]], Reader())
    lines = [t for _, t in res["lines"]]
    line = next(t for t in lines if t.startswith(f"CATCH-UP {n}/{m}:"))
    assert line.startswith(f"CATCH-UP {n}/{m}: 2 day(s) not recoverable (2026-10-10..10-11) — not recoverable (scrape "
                           f"keeps no history"), line
    assert res["not_recoverable"] == {SCRAPED: hole[::-1]}
    assert lines[-1].endswith("2 not recoverable (no history)"), lines[-1]
    assert all(_val(st, n, m, d) is None for d in hole)                     # never 0, never carried
    # THE CELL COMMENT: the Q0 holes note (why_amber, the AMBER comment) names them; Q0 is annualised over 88 days
    data = bw.aggregate(long, pd.DataFrame(), TODAY)
    row = data[(data["project"] == n) & (data["metric"] == m)].iloc[0]
    assert ("holes (no row): 2026-10-10..10-11 — not recoverable (scrape keeps no history); the Q0 sum is annualised "
            "over the") in str(row["note"]), row["note"]
    assert "not recoverable (scrape keeps no history)" in str(row["why_amber"]), row["why_amber"]
    # the same series read from an API with history: holes, no "not recoverable"
    long2 = long.assign(source="defillama")
    data2 = bw.aggregate(long2, pd.DataFrame(), TODAY)
    row2 = data2[(data2["project"] == n) & (data2["metric"] == m)].iloc[0]
    assert "holes (no row): 2026-10-10..10-11" in str(row2["note"]) and "not recoverable" not in str(row2["note"])
    assert ar.not_recoverable("scrape:x.org") == "scrape keeps no history" and ar.not_recoverable("defillama") == ""


# ============================================================================================ today's tiers read back
def test_an_api_hole_a_tier_reads_back_is_handed_to_todays_tier_and_reported_after_it(world, monkeypatch):
    st, p, _ = world
    from fetch import archive as ar
    from fetch import base
    n, m = "Fluid", "tvl_usd"
    hole = ["2026-09-01"]                                  # older than the routine 30-day re-read
    full = _days(120)
    st.upsert(_rows(n, m, [d for d in full if str(d.date()) not in hole], lambda d: 5.0, "defillama", 1))
    res = _catch_up(st, [p, config.PROJECT_BY_NAME[n]], Reader())
    assert res["tier_owed"] == {(n, m): hole}
    assert any(t == (f"CATCH-UP {n}/{m}: 1 day(s) owed (2026-09-01) — read back by today's defillama tier from its own "
                     f"history (defillama); only the owed days are kept (never an overwrite)") for _, t in res["lines"])
    assert "1 left (1 to today's tiers)" in res["lines"][-1][1]
    res_alone = _catch_up(st, [p, config.PROJECT_BY_NAME[n]], Reader(), tiers=False)
    assert any("read back by the next plain run's defillama tier" in t for _, t in res_alone["lines"])
    # today's tier: the whole history comes back; window() keeps the routine 30 days and the owed day only
    monkeypatch.setattr(base, "today", lambda: TODAY)
    base.set_catch_up_owed(res["tier_owed"])
    try:
        got = base.window(_rows(n, m, full, lambda d: 6.0, "defillama", 1), 30)
    finally:
        base.set_catch_up_owed({})
    assert set(got["date"].dt.strftime("%Y-%m-%d")) == set(hole) | {str(d.date()) for d in full[-30:]}
    st.upsert(got[got["date"].dt.strftime("%Y-%m-%d").isin(hole)])
    assert ar.tier_fill_line(st, res["tier_owed"]) == (
        "info", "CATCH-UP (today's tiers): 1 of 1 owed day(s) read back over 1 API series from their own history, 0 left")
    # a DECLARED source hole (the source never reported it) is not owed again every run
    st.conn.execute("DELETE FROM metrics WHERE project=? AND metric=? AND substr(date,1,10)='2026-09-01'", (n, m))
    st.conn.commit()
    monkeypatch.setattr(config, "declared_source_hole",
                        lambda a, b: {"from": "2026-08-31", "to": "2026-09-02"} if (a, b) == (n, m) else None)
    assert (n, m) not in ar.scan_holes(st, [config.PROJECT_BY_NAME[n]], set())


# ============================================================================================ budgets, resumable
def test_the_budgets_stop_and_the_next_run_resumes_newest_first(world):
    st, p, tmp = world
    more = ["2026-10-01", "2026-10-02"]                     # a second interior hole, older
    for d in more:
        st.conn.execute("DELETE FROM metrics WHERE project=? AND metric=? AND substr(date,1,10)=?", (PC, BAL, d))
    st.conn.commit()
    # no time: nothing read, every day still owed — said, not a hole
    r = Reader()
    res = _catch_up(st, [p], r, budget_s=0)
    assert r.reads == []
    line = res["lines"][0][1]
    assert line.endswith("0 day(s) filled, 5 left (2026-10-01..10-02, 2026-10-20..10-22) — archive read at each day's "
                         "first block via bsc; not reached this run (budget): 2026-10-01..10-02, 2026-10-20..10-22 — "
                         "the next run continues"), line
    # 2 days a run, NEWEST first: the Q0 window's newest days fill first
    r = Reader()
    _catch_up(st, [p], r, max_days=2)
    assert sorted({d for d, _ in r.reads}) == ["2026-10-21", "2026-10-22"]
    r = Reader()
    _catch_up(st, [p], r, max_days=2)
    assert sorted({d for d, _ in r.reads}) == ["2026-10-02", "2026-10-20"]
    r = Reader()
    res = _catch_up(st, [p], r, max_days=2)
    assert sorted({d for d, _ in r.reads}) == ["2026-10-01"]
    assert set(HOLE + more) <= set(_held(st, PC, BAL))
    # no lump was ever stored across a day still owed: every flow is one day's 24 blocks
    assert set(_held(st, PC, BURN).values()) == {24.0}
    r = Reader()
    assert not _catch_up(st, [p], r)["gaps"] and r.reads == []


def test_a_refused_interior_day_stays_given_up_across_runs(world):
    st, p, tmp = world
    from fetch import archive as ar
    for _ in range(3):
        _catch_up(st, [p], Reader(refuse={HOLE[1]}))
    assert HOLE[1] not in _held(st, PC, BAL)
    led = json.loads((tmp / "cache" / ar.CATCH_UP_LEDGER).read_text())
    assert led == {f"{PC}|{BAL}": {"tries": {HOLE[1]: 3}}}
    for _ in range(2):                                      # the full-window scan does not read it again
        r = Reader(refuse={HOLE[1]})
        res = _catch_up(st, [p], r)
        assert r.reads == [] and not res["gaps"]
    # the 2-day delta across the given-up day is honest (48 = two days' blocks), dated the day after
    assert _val(st, PC, BURN, "2026-10-22") == (48.0, "chain:bsc:burn_dead:archive:delta[span=2d]")


# ============================================================================================ starts, never an overwrite
def test_nothing_before_a_series_start_counts(world):
    st, p, _ = world
    from fetch import archive as ar
    # the window reaches 450 days back; the series starts 40 days back — the days before are not holes
    gaps, _, _ = ar.catch_up_gaps(st, [p])
    assert min(gaps[(PC, BAL)]) == HOLE[0]
    # a recorded start (a contract not deployed before) after the hole: the hole is before the series exists
    ar.record_series_start(PC, BAL, "2026-10-23", "burn_dead 0x... has no code at block 1 — not deployed before")
    gaps, _, _ = ar.catch_up_gaps(st, [p])
    assert (PC, BAL) not in gaps
    r = Reader()
    res = _catch_up(st, [p], r)
    assert r.reads == [] and res["holes_found"].get((PC, BAL)) is None
    assert ar.owed_days(PC, BAL, [("2026-10-10", "x"), ("2026-10-13", "x")], TODAY - pd.Timedelta(days=450), Y) == \
        [str(d.date()) for d in pd.date_range("2026-10-14", Y)][::-1] + ["2026-10-12", "2026-10-11"]


def test_never_an_overwrite(world):
    st, p, _ = world
    from fetch import archive as ar
    from fetch import base
    # a stored day whose value the chain would read differently keeps its stored value
    st.conn.execute("UPDATE metrics SET value=7.0 WHERE project=? AND metric=? AND substr(date,1,10)='2026-10-23'",
                    (PC, BAL))
    st.conn.commit()
    _catch_up(st, [p], Reader())
    assert _val(st, PC, BAL, "2026-10-23")[0] == 7.0 and _val(st, PC, BAL, HOLE[0])[0] == bal(HOLE[0])
    # today's tiers: window() keeps an owed day beside the routine window; the guard drops a read-back day's row whose
    # key is already held (a derivation from it), keeps the empty key and the routine window
    old = pd.DataFrame({"date": pd.to_datetime(["2026-09-01", "2026-09-02", "2026-10-30"]), "project": "Fluid",
                        "metric": "tvl_usd", "value": [1.0, 2.0, 3.0], "tier": 1, "source": "defillama"})
    base.set_catch_up_owed({("Fluid", "tvl_usd"): ["2026-09-02"]})
    try:
        monkey_today = base.today
        base.today = lambda: TODAY
        assert list(base.window(old, 30)["value"]) == [2.0, 3.0]
        assert list(base.window(old.assign(metric="protocol_tvl_usd"), 30)["value"]) == [3.0]
    finally:
        base.today = monkey_today
        base.set_catch_up_owed({})
    st.upsert(_rows("Fluid", "fees_usd", [pd.Timestamp("2026-09-02")], lambda d: 5.0, "defillama:x"))
    frame = pd.concat([old.assign(metric="tvl_usd"),
                       _rows("Fluid", "mcap_tvl", [pd.Timestamp("2026-09-02")], lambda d: 9.0, "derived:x"),
                       _rows("Fluid", "fees_usd", [pd.Timestamp("2026-09-02")], lambda d: 6.0, "derived:x")])
    kept = ar.tier_fill_guard(st, frame, {("Fluid", "tvl_usd"): ["2026-09-02"]}, 30)
    assert sorted(zip(kept["metric"], kept["value"])) == [("mcap_tvl", 9.0), ("tvl_usd", 1.0), ("tvl_usd", 2.0),
                                                          ("tvl_usd", 3.0)]


# ============================================================================================ GEODNET: 90 of 90
class FakeBalanceFlow:
    """GEODNET's balance-flow read: the release of every day it holds a boundary for (2,000 a day)."""
    runs: list = []

    def __init__(self, budget_s):
        self.budget_s = budget_s

    def run(self, projects, window_days, out):
        from fetch.base import tidy
        FakeBalanceFlow.runs.append([p["name"] for p in projects])
        rows = [(d, 2000.0) for d in _days(120)]
        out.add(tidy(rows, GEO, REL, "balance_flow:mining_wallets_release", 2), "balance_flow", GEO, "fake", 2)


def test_geodnet_emissions_with_a_6_day_interior_hole_cover_90_of_90_days_after_the_fill(world):
    st, p, _ = world
    import build_workbook as bw
    hole = [str(d.date()) for d in pd.date_range("2026-10-26", "2026-10-31")]   # inside Q0, rows on both sides
    days = [d for d in _days(120) if str(d.date()) not in hole]
    st.upsert(_rows(GEO, REL, days, lambda d: 1500.0, "balance_flow:mining_wallets_release"))

    def q0(st_):
        long = pd.read_sql("SELECT date, project, metric, value, source, tier FROM metrics WHERE project=? AND "
                           "metric=?", st_.conn, params=(GEO, REL))
        long = long.assign(date=pd.to_datetime(long["date"]), is_manual=False, entered_on="", source_note="")
        data = bw.aggregate(long, pd.DataFrame(), Y)
        return data[(data["project"] == GEO) & (data["metric"] == REL)].iloc[0]
    before = q0(st)
    assert before["q0_covered_days"] == 84 and "holes (no row): 2026-10-26..10-31" in str(before["note"])
    FakeBalanceFlow.runs = []
    res = _catch_up(st, [p, config.PROJECT_BY_NAME[GEO]], Reader(), replays={"balance_flow": FakeBalanceFlow})
    assert FakeBalanceFlow.runs == [[GEO]]
    held = _held(st, GEO, REL)
    assert {d: held[d] for d in hole} == {d: 2000.0 for d in hole}          # the read's own values, the owed days only
    assert {v for d, v in held.items() if d not in hole} == {1500.0}       # never an overwrite
    line = next(t for _, t in res["lines"] if t.startswith(f"CATCH-UP {GEO}/{REL}:"))
    assert line == (f"CATCH-UP {GEO}/{REL}: 6 day(s) filled (2026-10-26..10-31), 0 left — balance_flow's own read, the "
                    f"series' own source (balance_flow:mining_wallets_release)"), line
    after = q0(st)
    assert after["q0_covered_days"] == 90 and "holes" not in str(after["note"]), after["note"]


# ============================================================================================ the run: no flag, the lock
def _hold(db):
    from runlock import RunLock
    return RunLock(db, "token_metrics run", wait_s=0)


class Stop(Exception):
    pass


def _tm_world(tmp_path, monkeypatch):
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
    return tm, ar, db


def test_the_normal_run_catches_up_with_no_flag_inside_its_lock_and_hands_the_tiers_their_days(tmp_path, monkeypatch):
    tm, ar, db = _tm_world(tmp_path, monkeypatch)
    from fetch import base
    calls = []

    def fake_catch_up(st, projects, budget_s=None, max_days=None, **kw):
        from runlock import RunLocked
        with pytest.raises(RunLocked):                       # inside the run's lock
            with _hold(db):
                pass
        calls.append(([p["name"] for p in projects], kw.get("tiers")))
        return {"lines": [("info", "CATCH-UP: fake")], "gaps": {}, "filled": {},
                "tier_owed": {("Fluid", "tvl_usd"): ["2026-09-02"]}}

    def fake_fetch_all(*a, **k):
        calls.append(dict(base.CATCH_UP_OWED))               # today's tiers see the owed days
        raise Stop()
    monkeypatch.setattr(ar, "catch_up", fake_catch_up)
    monkeypatch.setattr(tm.fetch, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(tm.fetch, "refresh_changed_adapters", lambda **k: None)
    with pytest.raises(Stop):
        tm.main(["--project", "Fluid"])
    assert calls == [(["Fluid"], True), {("Fluid", "tvl_usd"): frozenset({"2026-09-02"})}]
    base.set_catch_up_owed({})


def test_catch_up_alone_still_runs_alone(tmp_path, monkeypatch):
    tm, ar, db = _tm_world(tmp_path, monkeypatch)
    calls = []

    def fake_catch_up(st, projects, budget_s=None, max_days=None, **kw):
        calls.append(([p["name"] for p in projects], budget_s, max_days, kw.get("tiers")))
        return {"lines": [("info", "CATCH-UP: fake")], "gaps": {}, "filled": {}}
    monkeypatch.setattr(ar, "catch_up", fake_catch_up)
    monkeypatch.setattr(tm, "build_workbook", lambda *a, **k: pytest.fail("--catch-up must not build the workbook"))
    monkeypatch.setattr(tm.fetch, "fetch_all", lambda *a, **k: pytest.fail("--catch-up must not run the tiers"))
    assert tm.main(["--catch-up", "--project", PC, "--budget-min", "3", "--catch-up-days", "2"]) == 0
    assert calls == [([PC], 180.0, 2, False)]                # tiers=False: a tier's read-back waits for a plain run
    with _hold(db):
        assert tm.main(["--catch-up", "--project", PC]) == 75
