"""Round 18, item 5 (Jake 2026-11-24) — Near: the P2P history before the stored year, in monthly chunks from 2026-12,
within the 900 GB budget, never automatic. Numbers and behaviour only: the GB per chunk and the month's room, the
order, no day twice, no partial chunk, the refusal, and the date the key forms from a synthetic series."""
import copy
import datetime as dt
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import config
import near_p2p_history as nh

ROOT = Path(__file__).resolve().parent.parent
CFG = config.NEAR_P2P_HISTORY
NEAR = config.PROJECT_BY_NAME["Near"]
D = dt.date.fromisoformat


# ================================================================================================= the plan's numbers
def test_one_chunk_a_month_brings_the_key_from_late_2027_to_the_end_of_may_2027():
    p = nh.plan("2025-12-01", CFG)
    assert [r["run_month"] for r in p["rows"]] == ["2026-12", "2027-01", "2027-02", "2027-03", "2027-04", "2027-05"]
    spans = [(str(c[0]), str(c[1])) for r in p["rows"] for c in r["chunks"]]
    assert spans == [("2025-11-01", "2025-11-30"), ("2025-10-01", "2025-10-31"), ("2025-09-01", "2025-09-30"),
                     ("2025-08-01", "2025-08-31"), ("2025-07-01", "2025-07-31"), ("2025-06-01", "2025-06-30")]
    # GB per chunk: the days x 7.54 GB a day (233.7 GB / 31 days, Jake's dry run of 2026-11-17)
    assert [round(r["gb"], 1) for r in p["rows"]] == [226.2, 233.7, 226.2, 233.7, 233.7, 226.2]
    assert p["total_gb"] == pytest.approx(183 * 7.54)
    assert p["formed_on"] == D("2027-05-31") and p["without"] == D("2027-11-30")
    # as the store stood on 2026-11-23 (the run from 2025-12-29) and with no chunk at all: R17's date
    assert nh.plan("2025-12-29", {**CFG, "chunks_per_month": 0})["formed_on"] == D("2027-12-28")


def test_every_chunk_fits_the_months_room_and_the_per_query_cap():
    for per in (1, 2, 3):
        p = nh.plan("2025-12-01", CFG, chunks_per_month=per)
        for r in p["rows"]:
            assert 0 < r["gb"] <= r["room_gb"] and r["left_gb"] >= 0
            assert all(c[3] <= 400.0 for c in r["chunks"])
            # the month in full stays inside 900 GB: chunks + the top-up reserve + what else the ledger carries
            other = CFG["other_gb"]["every_month"] + CFG["other_gb"].get(r["run_month"], 0)
            assert r["gb"] + nh.reserve_gb(r["run_on"], 2.84, 1.2) + other <= 900.0
    # December 2026: 900 - 30 days x 2.84 x 1.2 - Ethereum's 56 - the daily run's own chunk 211.1
    dec = nh.plan("2025-12-01", CFG)["rows"][0]
    assert dec["room_gb"] == pytest.approx(900 - 30 * 2.84 * 1.2 - 56.0 - 211.1)
    assert nh.reserve_gb(D("2026-12-01"), 2.84, 1.2) == pytest.approx(102.24)


def test_more_chunks_a_month_form_the_key_sooner_and_december_cannot_take_three():
    two, three = nh.plan("2025-12-01", CFG, chunks_per_month=2), nh.plan("2025-12-01", CFG, chunks_per_month=3)
    assert two["formed_on"] == D("2027-03-31") and three["formed_on"] == D("2027-02-28")
    assert [len(r["chunks"]) for r in two["rows"]] == [2, 2, 2, 2]
    assert len(three["rows"][0]["chunks"]) == 2            # a third December chunk would pass the month's room
    assert three["rows"][0]["gb"] == pytest.approx(61 * 7.54)
    # a month stops reading once the key has what it needs: March 2027 takes one chunk, not three
    assert [len(r["chunks"]) for r in three["rows"]] == [2, 3, 3, 1]
    assert two["total_gb"] == pytest.approx(244 * 7.54)


def test_the_budget_is_never_passed_by_asking_for_more():
    # a budget too small for one chunk reads nothing and the date does not move
    p = nh.plan("2025-12-01", CFG, budget_gb=300.0)
    assert p["rows"] == [] and p["formed_on"] == p["without"] == D("2027-11-30")
    # a per-query cap under a chunk's size holds it
    assert nh.plan("2025-12-01", CFG, max_query_gb=200.0)["rows"] == []


def test_no_day_is_planned_twice_and_the_order_is_backwards_without_a_hole():
    for per in (1, 2, 3):
        chunks = [c for r in nh.plan("2025-12-01", CFG, chunks_per_month=per)["rows"] for c in r["chunks"]]
        days = [d for c in chunks for d in nh._days(c[0], c[1])]
        assert len(days) == len(set(days))
        prev = D("2025-12-01")
        for d0, d1, n, _gb in chunks:                       # each chunk ends the day before the one read before it
            assert d1 == prev - dt.timedelta(days=1) and d0.day == 1 and n == (d1 - d0).days + 1
            prev = d0


def test_the_plan_never_reads_before_the_dex_legs_first_day():
    cfg = {**CFG, "est_gb_per_day": 0.5}                   # cheap enough to take everything at once
    p = nh.plan("2025-12-01", cfg, chunks_per_month=40)
    first = min(c[0] for r in p["rows"] for c in r["chunks"])
    assert first == D(CFG["floor"]) == D("2024-12-10")
    assert p["formed_on"] == D("2026-12-09")               # floor + 729: no budget brings the key before this day
    assert nh.chunk_before(D("2024-12-10"), D("2024-12-10")) is None


# ================================================================================================= the next chunk
def test_the_next_chunk_is_the_calendar_month_before_the_stored_run():
    held = [str(d.date()) for d in pd.date_range("2025-12-01", "2026-11-30")]
    assert nh.run_start(held) == D("2025-12-01")
    assert nh.next_chunk(held, CFG["floor"]) == (D("2025-11-01"), D("2025-11-30"))
    # resumable: with November held the next is October; the state is the only memory
    held += [str(d.date()) for d in pd.date_range("2025-11-01", "2025-11-30")]
    assert nh.next_chunk(held, CFG["floor"]) == (D("2025-10-01"), D("2025-10-31"))


def test_a_stub_at_a_months_end_is_taken_with_the_month_before_and_a_held_day_is_never_in_a_chunk():
    # the daily run started on the 5th: 2025-12-01..04 fell out of its window — 4 days, taken with November
    held = [str(d.date()) for d in pd.date_range("2025-12-05", "2026-12-04")]
    assert nh.next_chunk(held, CFG["floor"]) == (D("2025-11-01"), D("2025-12-04"))
    assert nh.chunk_before(D("2025-12-08"), D("2024-12-10")) == (D("2025-12-01"), D("2025-12-07"))    # 7 days: its own
    # an older held day (a console CSV) bounds the chunk: it is not read again
    held2 = held + ["2025-11-20"]
    c = nh.next_chunk(held2, CFG["floor"])
    assert c == (D("2025-11-21"), D("2025-12-04")) and "2025-11-20" not in {str(d) for d in nh._days(*c)}
    assert nh.next_chunk([], CFG["floor"]) is None


def test_the_daily_runs_own_window_comes_first():
    held = [str(d.date()) for d in pd.date_range("2025-12-29", "2026-11-30")]
    holes = nh.window_holes(held, "2026-12-01", 365)
    assert holes[0] == D("2025-12-01") and holes[-1] == D("2025-12-28") and len(holes) == 28
    full = [str(d.date()) for d in pd.date_range("2025-12-01", "2026-11-30")]
    assert nh.window_holes(full, "2026-12-01", 365) == []
    # yesterday not read yet is not a hole (the top-up reads it), and days older than the window are not owed
    assert nh.window_holes(full[:-1], "2026-12-01", 365) == []
    assert nh.window_holes(full, "2026-12-20", 365) == []


# ================================================================================================= the refusal
OK = {"approved": True, "approved_by": "Jake", "approved_on": "2026-12-01", "max_gb": 240, "chunks": 1}
NOV = (D("2025-11-01"), D("2025-11-30"))


def _r(explicit=True, today="2026-12-01", approval=OK, chunk=NOV, gb=226.2, done=0):
    return nh.refusal(explicit, today, approval, chunk, gb, done, CFG)


def test_the_shipped_approval_is_not_approved_and_the_refusal_says_what_to_approve_and_the_gb():
    # R21b (Jake 2026-11-25): the shipped approval is now Jake's, for 2026-12 (tests/test_r21b_lead.py); the refusal
    # without one is read on an empty approval (was: the shipped one, approved False)
    a = {"approved": False, "approved_by": None, "approved_on": None, "max_gb": None, "chunks": 1}
    why = _r(approval=a)
    assert why is not None and "NOT APPROVED" in why
    assert "2025-11-01..2025-11-30" in why and "30 days" in why and "226.2 GB" in why
    assert "max_gb (at least 227)" in why and "2026-12" in why and CFG["command"] in why


def test_it_starts_only_from_its_command_in_or_after_december_2026_under_a_dated_approval_of_that_month():
    assert _r() is None
    assert "only from its own command" in _r(explicit=False)                   # an approval alone starts nothing
    assert "2026-12" in _r(today="2026-11-30", approval={**OK, "approved_on": "2026-11-30"})   # not before December
    assert _r(approval={**OK, "approved": "yes"}) is not None                  # True, not truthy
    assert _r(approval={**OK, "approved_by": " "}) is not None
    assert _r(approval={**OK, "approved_on": "December"}) is not None
    assert "after today" in _r(approval={**OK, "approved_on": "2026-12-02"})
    # an approval covers its own calendar month only (R14's rule for stage 2)
    assert _r(today="2026-12-31") is None
    late = _r(today="2027-01-01")
    assert late is not None and "2026-12 only" in late and "2027-01" in late
    assert _r(today="2027-12-05") is not None


def test_the_approval_carries_the_gb_and_the_chunks_it_covers():
    assert "no max_gb" in _r(approval={**OK, "max_gb": None})
    assert "no max_gb" in _r(approval={**OK, "max_gb": True})
    assert _r(gb=240.0) is None and "over the 240.0 GB" in _r(gb=240.1)        # judged at its edge
    assert "already taken 1 chunk(s)" in _r(done=1)                            # one chunk a month unless Jake says more
    assert _r(done=1, approval={**OK, "chunks": 2}) is None
    assert _r(done=2, approval={**OK, "chunks": 2}) is not None
    assert "nothing to read" in _r(chunk=None)


# ================================================================================================= the adapter's chunk
class _Bq:
    class QueryJobConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            self.dry_run = kw.get("dry_run", False)
    ScalarQueryParameter = staticmethod(lambda k, t, v: (k, t, v))
    ArrayQueryParameter = staticmethod(lambda k, t, v: (k, t, tuple(v)))


class _Client:
    def __init__(self, est, drop=(), extra=()):
        self.est, self.drop, self.extra, self.dry, self.real, self.caps = est, set(drop), list(extra), [], [], []

    def query(self, sql, job_config=None):
        p = {k: v for k, _, v in job_config.query_parameters}
        win = (str(p["d0"]), str(p["d1"]))
        if job_config.dry_run:
            self.dry.append(win)
            return SimpleNamespace(total_bytes_processed=self.est)
        self.real.append(win)
        self.caps.append(job_config.maximum_bytes_billed)
        rows = []
        for d in list(pd.date_range(*win)) + [pd.Timestamp(x) for x in self.extra]:
            if str(d.date()) not in self.drop:
                rows.append({"day": d, "token": "NEAR", "amount": "1000.5", "n": 10})
            rows.append({"day": d, "token": "wrap.near", "amount": "5" + "0" * 24, "n": 2})
        return SimpleNamespace(result=lambda: rows, total_bytes_billed=self.est)


def _chunk(tmp_path, monkeypatch, est=226_200_000_000, cap=240_000_000_000, used=0, tag="c", today="2026-12-01", **kw):
    from fetch import near_bigquery as nbq
    from fetch.base import FetchOutput
    monkeypatch.setattr(nbq, "today", lambda: pd.Timestamp(today))
    st = {"ledger": {today[:7]: used}, "prices": {}, "tokens": {"list": ["wrap.near"], "on": today},
          "topup_bytes": {"bytes": 2.84e9, "on": today, "source": "t"},
          "days": {str(d.date()): {"NEAR": "1"} for d in pd.date_range("2025-12-01", "2026-11-30")}}
    a = nbq.NearBigQuery(client=_Client(est, **kw), bq=_Bq, cache_file=tmp_path / f"{tag}.json", csv_dir=tmp_path / "x")
    out = FetchOutput()
    res = a.history_chunk(a._client_obj, copy.deepcopy(NEAR["near_bigquery"]), st, out, "Near", "2025-11-01",
                          "2025-11-30", cap)
    return a._client_obj, out, st, res


def test_a_chunk_is_kept_whole_with_its_bytes_on_the_ledger_and_the_run_moves_back_a_month(tmp_path, monkeypatch):
    cl, out, st, res = _chunk(tmp_path, monkeypatch)
    assert cl.dry == cl.real == [("2025-11-01", "2025-11-30")]
    assert res == {"days": 30, "billed": 226_200_000_000, "d0": "2025-11-01", "d1": "2025-11-30"}
    assert len(st["days"]) == 365 + 30 and st["days"]["2025-11-15"] == {"NEAR": "1000.5", "wrap.near": "5" + "0" * 24}
    assert st["ledger"]["2026-12"] == 226_200_000_000
    assert st["history"]["runs"] == [{"month": "2026-12", "on": "2026-12-01", "d0": "2025-11-01", "d1": "2025-11-30",
                                      "days": 30, "billed_bytes": 226_200_000_000}]
    assert nh.done_in_month(st, "2026-12-15") == 1 and nh.done_in_month(st, "2027-01-01") == 0
    assert nh.run_start(st["days"]) == D("2025-11-01") and nh.key_forms_on(st["days"]) == D("2027-10-31")
    # BigQuery itself is told the most it may bill: never above the approval's GB
    assert 226_200_000_000 <= cl.caps[0] <= 240_000_000_000
    # the state on disk is the same: a later run resumes from it
    assert len(json.loads((tmp_path / "c.json").read_text())["days"]) == 395


def test_a_chunk_with_a_day_missing_is_not_kept_at_all(tmp_path, monkeypatch):
    cl, out, st, res = _chunk(tmp_path, monkeypatch, drop=("2025-11-17",), tag="short")
    assert res is None and len(cl.real) == 1
    assert len(st["days"]) == 365 and not any(d.startswith("2025-11") for d in st["days"]) and "history" not in st
    assert "NOTHING KEPT" in out.log[-1].message and "2025-11-17" in out.log[-1].message
    assert st["ledger"]["2026-12"] == 226_200_000_000        # billed is billed: the ledger says so
    # a row dated outside the chunk also keeps nothing
    cl, out, st, res = _chunk(tmp_path, monkeypatch, extra=("2025-10-31",), tag="outside")
    assert res is None and len(st["days"]) == 365


def test_a_held_day_is_never_asked_again_and_no_query_is_made(tmp_path, monkeypatch):
    from fetch import near_bigquery as nbq
    from fetch.base import FetchOutput
    monkeypatch.setattr(nbq, "today", lambda: pd.Timestamp("2026-12-01"))
    st = {"ledger": {}, "tokens": {"list": ["wrap.near"]}, "topup_bytes": {"bytes": 2.84e9},
          "days": {"2025-11-30": {"NEAR": "7"}}}
    a = nbq.NearBigQuery(client=_Client(1), bq=_Bq, cache_file=tmp_path / "t.json", csv_dir=tmp_path / "x")
    out = FetchOutput()
    assert a.history_chunk(a._client_obj, copy.deepcopy(NEAR["near_bigquery"]), st, out, "Near", "2025-11-01",
                           "2025-11-30", 10 ** 12) is None
    assert a._client_obj.dry == a._client_obj.real == [] and st["days"] == {"2025-11-30": {"NEAR": "7"}}
    assert "never read twice" in out.log[-1].message


def test_the_standing_rules_still_hold_the_chunk(tmp_path, monkeypatch):
    # over the approval's GB: dry-run only
    cl, out, st, res = _chunk(tmp_path, monkeypatch, est=240_000_000_001, tag="cap")
    assert res is None and cl.real == [] and len(st["days"]) == 365 and "NOT RUN" in out.log[-1].message
    # over the 400 GB per-query cap, whatever the approval says
    cl, out, st, res = _chunk(tmp_path, monkeypatch, est=400_000_000_001, cap=10 ** 12, tag="query")
    assert res is None and cl.real == [] and "per-query cap" in out.log[-1].message
    # the month's budget after the top-up reserve: 1,000 - 700 used - 30 x 2.84 x 1.2 = 197.8 GB left < 226.2
    # (R20, Jake 2026-11-24: the budget is 1,000 GB — was 900 - 600 used = the same 197.8 GB left)
    cl, out, st, res = _chunk(tmp_path, monkeypatch, used=700_000_000_000, tag="budget")
    assert res is None and cl.real == [] and "BACKFILL HELD" in out.log[-1].message
    assert st["ledger"]["2026-12"] == 700_000_000_000                       # was 600_000_000_000 under 900 GB
    # and it fits when the month has the room: 1,000 - 500 - 102.2 = 397.8 GB (was 900 - 500 - 102.2 = 297.8)
    cl, out, st, res = _chunk(tmp_path, monkeypatch, used=500_000_000_000, tag="fits")
    assert res is not None and st["ledger"]["2026-12"] == 726_200_000_000 <= 1000 * 10 ** 9     # was <= 900 GB


# ================================================================================================= never automatic
def test_nothing_scheduled_or_daily_names_the_history_command():
    for f in ("windows/daily_run.ps1", "windows/register_task.ps1", "token_metrics.py", "check_offline_items.py"):
        text = (ROOT / f).read_text(encoding="utf-8", errors="replace")
        assert "near_p2p_history" not in text and "history_chunk" not in text, f
    import inspect
    from fetch.near_bigquery import NearBigQuery
    for fn in (NearBigQuery.run, NearBigQuery._project, NearBigQuery._p2p):
        assert "history_chunk" not in inspect.getsource(fn)
    assert config.NEAR_STAGE2_APPROVAL["approved"] is False       # the wallets' stage 2 is as it was


def test_the_daily_read_still_stops_at_its_365_days(tmp_path, monkeypatch):
    from fetch import near_bigquery as nbq
    from fetch.base import FetchOutput
    monkeypatch.setattr(nbq, "today", lambda: pd.Timestamp("2026-12-01"))
    st = {"ledger": {}, "prices": {}, "tokens": {"list": ["wrap.near"], "on": "2026-12-01"},
          "topup_bytes": {"bytes": 2.84e9, "on": "2026-12-01", "source": "t"},
          "days": {str(d.date()): {"NEAR": "1"} for d in pd.date_range("2025-12-29", "2026-11-29")}}
    cl = _Client(1_000_000_000)
    a = nbq.NearBigQuery(client=cl, bq=_Bq, cache_file=tmp_path / "d.json", csv_dir=tmp_path / "x")
    a._p2p(cl, copy.deepcopy(NEAR["near_bigquery"]), st, FetchOutput(), "Near", unbounded=True)
    # the top-up (yesterday) and its own last chunk — nothing before 2025-12-01, with or without a chunk limit
    assert cl.real == [("2026-11-30", "2026-11-30"), ("2025-12-01", "2025-12-28")]
    assert min(st["days"]) == "2025-12-01"


def _state(tmp_path, monkeypatch, days):
    lc = tmp_path / "lc"
    lc.mkdir()
    (lc / "near-bq.json").write_text(json.dumps({"ledger": {}, "days": {d: {"NEAR": "1"} for d in days},
                                                 "tokens": {"list": ["wrap.near"]}}))
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(lc))
    from fetch.near_bigquery import NearBigQuery

    def boom(self, spec):
        raise AssertionError("a refused run must not open a BigQuery client")
    monkeypatch.setattr(NearBigQuery, "_client", boom)
    return lc


def test_the_command_without_an_approval_bills_nothing_and_calls_nothing(tmp_path, monkeypatch, capsys):
    held = [str(d.date()) for d in pd.date_range("2025-12-01", "2026-11-30")]
    lc = _state(tmp_path, monkeypatch, held)
    monkeypatch.setattr(nh, "_today", lambda: D("2026-12-01"))
    # R21b: Jake's approval for 2026-12 now ships — this reads the command WITHOUT one
    monkeypatch.setattr(config, "NEAR_P2P_HISTORY_APPROVAL", {"approved": False, "approved_by": None,
                                                              "approved_on": None, "max_gb": None, "chunks": 1})
    before = (lc / "near-bq.json").read_text()
    assert nh.main(["run", "--db", str(tmp_path / "none.db")]) == 0
    said = capsys.readouterr().out
    assert "NOT RUN" in said and "NOT APPROVED" in said and "226.2 GB" in said and "no BigQuery call" in said
    assert (lc / "near-bq.json").read_text() == before


def _db(path, price_days, dex_days):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE metrics (date TEXT, project TEXT, metric TEXT, value REAL, source TEXT)")
    con.executemany("INSERT INTO metrics VALUES (?,?,?,?,?)",
                    [(str(d.date()), "Near", "price_usd", 2.5, "t") for d in price_days]
                    + [(str(d.date()), "Near", "dex_volume_usd", 1e8, "t") for d in dex_days])
    con.commit()
    con.close()


def test_an_approved_command_still_refuses_while_the_daily_window_or_the_store_is_short(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "NEAR_P2P_HISTORY_APPROVAL", dict(OK))
    monkeypatch.setattr(nh, "_today", lambda: D("2026-12-01"))
    # (a) the daily run still owes 2025-12-01..28: it reads them first
    _state(tmp_path, monkeypatch, [str(d.date()) for d in pd.date_range("2025-12-29", "2026-11-30")])
    assert nh.main(["run", "--db", str(tmp_path / "none.db")]) == 0
    assert "still owes 28 day(s)" in capsys.readouterr().out
    # (b) the store holds no DEX leg on part of the chunk: it would bill and form nothing there
    held = [str(d.date()) for d in pd.date_range("2025-12-01", "2026-11-30")]
    (tmp_path / "lc" / "near-bq.json").write_text(json.dumps({"ledger": {}, "days": {d: {} for d in held}}))
    _db(tmp_path / "m.db", pd.date_range("2025-11-01", "2025-11-30"), pd.date_range("2025-11-11", "2025-11-30"))
    assert nh.store_gaps(tmp_path / "m.db", *NOV) == {"dex_volume_usd": nh._days(D("2025-11-01"), D("2025-11-10"))}
    assert nh.main(["run", "--db", str(tmp_path / "m.db")]) == 0
    said = capsys.readouterr().out
    assert "no dex_volume_usd on 10 of the chunk's days" in said and "Nothing billed" in said
    # a store with both legs on every day has no gap
    _db(tmp_path / "ok.db", pd.date_range("2025-11-01", "2025-11-30"), pd.date_range("2025-11-01", "2025-11-30"))
    assert nh.store_gaps(tmp_path / "ok.db", *NOV) == {}


# ================================================================================================= the date the key forms
def _g(metric, days, value=1.0):
    return pd.DataFrame({"date": days, "project": "Near", "metric": metric, "value": value, "source": "t", "tier": 1,
                         "run_id": "r"})


def test_the_planners_date_is_the_readers_date_on_a_synthetic_series():
    import done_check as dc
    import reader
    met = config.ARTEMIS_SETTLEMENT["metric"]
    circ = _g("circulating_supply_used", pd.date_range("2024-01-01", "2026-12-05"), 1.2e9)
    for first in ("2025-12-29", "2025-12-01", "2025-11-01", "2025-06-01"):
        days = pd.date_range(first, "2026-12-04")
        _v, why = reader.settlement_key({("Near", met): _g(met, days), ("Near", "circulating_supply_used"): circ}, "Near")
        assert max(dc.dates_in(why)) == nh.key_forms_on([str(d.date()) for d in days]) \
            == D(first) + dt.timedelta(days=729)
    # a hole restarts the count for both
    holed = [str(d.date()) for d in pd.date_range("2025-06-01", "2026-12-04") if str(d.date()) != "2025-09-15"]
    assert nh.key_forms_on(holed) == D("2025-09-16") + dt.timedelta(days=729)
    assert nh.key_forms_on([]) is None


def test_the_key_is_a_number_on_day_729_of_a_hole_free_series_and_not_the_day_before():
    import build_workbook as bw
    import reader
    met = config.ARTEMIS_SETTLEMENT["metric"]
    first = pd.Timestamp("2025-06-01")                     # where the six monthly chunks leave the stored run

    def key(last):
        days = pd.date_range(first, last)
        # the later year settles twice the earlier year's volume a day, on an unchanged supply
        vals = [1e8 if d < first + pd.Timedelta(days=365) else 2e8 for d in days]
        groups = {("Near", met): _g(met, days, vals),
                  ("Near", "circulating_supply_used"): _g("circulating_supply_used", pd.date_range("2025-01-01", last),
                                                         1.2e9)}
        bw._nrr_views(groups, "Near", met, "settlement_volume_365d_usd", "network_reserve_ratio", 365,
                      config.ARTEMIS_DERIVED, "{have} of {n} to {last}")
        return reader.settlement_key(groups, "Near")
    forms = first + pd.Timedelta(days=729)
    assert forms == pd.Timestamp("2027-05-31") == pd.Timestamp(nh.key_forms_on([str(first.date()), "2025-06-02"])
                                                              ) - pd.Timedelta(days=0)
    v, why = key(forms)
    assert v == pytest.approx(1.0) and why == "2026-05-31 to 2027-05-31"      # 365 x 2e8 over 365 x 1e8, less one
    v0, why0 = key(forms - pd.Timedelta(days=1))
    assert v0 is None and "2027-05-31" in why0
