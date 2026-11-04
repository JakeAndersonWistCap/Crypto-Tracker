"""PHASE P4-Q4 (SPEC "P4-Q4", Jake 2026-11-03): ETHEREUM'S STAKED ETH AND LOCK RATE.

Staked ETH = beaconcha.in POST /api/v2/ethereum/epoch (Authorization: Bearer BEACONCHAIN_API_KEY; the key in the header
only, never logged), the first epoch of each UTC day: total_validator_balance (wei → ETH) and validators_count; the
window backfilled by epoch number (newest first, resumable — the store is the state). Declared handover from
validatorqueue with the overlap check on 2026-10-19..10-25 (±2%, all 7 days; the per-day and mean gap said). The lock
rate counts staked ETH as locked (the exit queue makes it a lock): staked ÷ circulating, the one stake figure CP's
staked column, the float and the A1 staking rate read. Fakes only — no network (there is no key in this container).
"""
from __future__ import annotations

import logging
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
from fetch import staked_eth as se  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402

KEY = "SECRETKEY-q4-0123456789"
ETH = config.PROJECT_BY_NAME["Ethereum"]
TODAY = pd.Timestamp("2026-11-03 12:00")


@pytest.fixture(autouse=True)
def _all_projects(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", list(bw.PROJECTS))      # never depend on another test's narrowed build
    monkeypatch.setattr(se, "today", lambda: TODAY)


class _Daily:
    def __init__(self):
        self.marked = []

    def due(self, key, day):
        return True

    def ever(self, key):
        return False

    def done(self, key, day):
        self.marked.append((key, day))


def _answer(ep, staked_eth=44_395_404, count=818_978, **over):
    d = {"epoch": ep, "timestamp": se.GENESIS_UNIX + ep * se.EPOCH_SECONDS, "slots": {"start": ep * 32, "end": ep * 32 + 31},
         "validators_count": count, "total_validator_balance": str(int(staked_eth) * 10**18 + 123_456_789),
         "total_active_balance": str(44_237_728 * 10**18), "finality": "finalized"}
    d.update(over)
    return {"data": {k: v for k, v in d.items() if v is not _DROP}}


_DROP = object()


class _V2Http:
    """beaconcha.in's v2 epoch endpoint as documented (docs.beaconcha.in, OpenAPI 1.0.3, read 2026-11-03)."""
    def __init__(self, fail_after=None, error=None, answer=None):
        self.calls, self.fail_after, self.error, self.answer = [], fail_after, error, answer or _answer

    def post(self, url, json_body=None, headers=None, deadline=None):
        self.calls.append({"url": url, "body": json_body, "headers": dict(headers or {})})
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise self.error
        return self.answer(json_body["epoch"]["number"])

    def get(self, *a, **k):
        raise AssertionError("the v2 route is POST only")


def _run(http, stored=None, key=KEY, deadline_s=None):
    a = se.BeaconStakedEth(http=http, daily=_Daily(), key=key, stored_long=stored, deadline_s=deadline_s)
    out = FetchOutput()
    a.run([ETH], 30, out)
    return a, out


# ================================================================================================ the request
def test_request_is_a_post_with_the_key_in_the_bearer_header_only():
    a, out = _run(_V2Http())
    c = a.http.calls[0]
    assert c["url"] == "https://beaconcha.in/api/v2/ethereum/epoch"
    assert c["headers"] == {"Authorization": f"Bearer {KEY}"}
    assert c["body"] == {"chain": "mainnet", "epoch": {"number": se.epoch_at(TODAY)}}
    assert all(KEY not in x["url"] and KEY not in str(x["body"]) for x in a.http.calls)
    assert all(KEY not in e.message for e in out.log)
    assert not any(KEY in str(v) for v in out.frame()["source"])


@pytest.mark.parametrize("error", [
    RuntimeError(f"HTTP 401 — Unauthorized: token {KEY} is not valid"),       # a server that echoes the token
    __import__("fetch.base", fromlist=["HttpError"]).HttpError(403, "https://beaconcha.in/api/v2/ethereum/epoch",
                                                              f"endpoint not allowed for your tier ({KEY})"),
])
def test_no_key_in_the_log_on_an_error_either(error, caplog):
    caplog.set_level(logging.DEBUG)
    a, out = _run(_V2Http(fail_after=2, error=error))
    assert len(a.http.calls) == 3, "stops at the first refusal — never hammers a refused key"
    text = " ".join(e.message for e in out.log) + caplog.text
    assert KEY not in text and "***" in text
    assert "v2/ethereum" not in text                                 # nothing after /v2/ is printed
    assert "STOPPED" in text and "the next run resumes" in text


def test_keyless_nothing_is_called_nothing_stored_and_it_is_said():
    a, out = _run(_V2Http(), key="")
    assert not a.http.calls and out.frame().empty
    assert any("no BEACONCHAIN_API_KEY" in e.message and e.status == "failed" for e in out.log)


def test_the_key_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("BEACONCHAIN_API_KEY", "  envkey-abcdefgh  ")
    assert se.BeaconStakedEth(http=_V2Http(), daily=_Daily()).key == "envkey-abcdefgh"


# ================================================================================================ wei → ETH, strict
def test_wei_to_eth():
    assert se.wei_to_eth("44395404150864980000000000") == pytest.approx(44_395_404.15086498)
    assert se.wei_to_eth(32 * 10**18) == 32.0
    for bad in (None, "", "0", "-5", "1.5e25", "0x10", 1.5e25, True, "44 395 404"):
        assert se.wei_to_eth(bad) is None, bad                      # unknown is never 0


def test_parse_takes_the_day_and_refuses_a_missing_or_wrong_field():
    ep = se.epoch_at(pd.Timestamp("2026-10-19"))
    eth, n = se.parse_epoch(_answer(ep), ep)
    assert eth == pytest.approx(44_395_404.000000000123456789) and n == 818_978
    for over, why in [({"total_validator_balance": _DROP}, "no total_validator_balance"),
                      ({"total_validator_balance": None}, "is not a wei amount"),
                      ({"total_validator_balance": 4.4e25}, "is not a wei amount"),
                      ({"validators_count": _DROP}, "validators_count"),
                      ({"validators_count": 0}, "validators_count"),
                      ({"finality": "not_finalized"}, "not finalized"),
                      ({"finality": _DROP}, "not finalized"),
                      ({"epoch": ep + 1}, "answered epoch"),
                      ({"timestamp": _DROP}, "timestamp")]:
        got = se.parse_epoch(_answer(ep, **over), ep)
        assert isinstance(got, str) and why in got, (over, got)
    assert "no data object" in se.parse_epoch({"error": "x"}, ep)


def test_a_refused_day_stores_nothing_for_that_day_and_says_so():
    bad = se.epoch_at(pd.Timestamp("2026-11-01"))
    a, out = _run(_V2Http(answer=lambda e: _answer(e, total_validator_balance=_DROP) if e == bad else _answer(e)))
    f = out.frame()
    st = f[f["metric"] == "beacon_chain_eth_primary"]
    assert pd.Timestamp("2026-11-01") not in set(pd.to_datetime(st["date"])) and len(st) == 454
    assert (st["value"] > 0).all()
    assert any("REFUSED" in e.message and "2026-11-01" in e.message for e in out.log)


# ================================================================================================ first epoch of a day
@pytest.mark.parametrize("day", ["2020-12-01", "2020-12-02", "2026-10-19", "2026-10-25", "2026-11-03", "2027-02-28"])
def test_first_epoch_of_each_utc_day(day):
    d = pd.Timestamp(day)
    start = int(d.timestamp())
    ep = se.epoch_at(d)
    assert ep == max(0, math.ceil((start - 1606824023) / 384))
    assert se.GENESIS_UNIX + ep * 384 >= start > se.GENESIS_UNIX + (ep - 1) * 384 or ep == 0
    assert se.day_of_epoch(ep) == d or day == "2020-12-01"           # genesis came at 12:00:23 on 2020-12-01
    assert se.epoch_at(pd.Timestamp("2026-10-19")) == 483_188


# ================================================================================================ backfill by epoch
def test_backfill_reads_the_whole_window_newest_first_and_stores_both_series():
    a, out = _run(_V2Http())
    eps = [c["body"]["epoch"]["number"] for c in a.http.calls]
    assert eps == sorted(eps, reverse=True) and len(eps) == ETH["staked_eth_primary"]["history_days"] == 455
    assert eps[0] == se.epoch_at(TODAY) and eps[-1] == se.epoch_at(TODAY.normalize() - pd.Timedelta(days=454))
    f = out.frame()
    st, n = (f[f["metric"] == m] for m in ("beacon_chain_eth_primary", "beacon_validators_count"))
    assert len(st) == len(n) == 455
    assert set(st["source"]) == {se.POINT} and set(n["source"]) == {se.COUNT_POINT}
    assert st["value"].between(config.METRICS["beacon_chain_eth_primary"]["sanity_min"],
                               config.METRICS["beacon_chain_eth_primary"]["sanity_max"]).all()
    assert n["value"].between(config.METRICS["beacon_validators_count"]["sanity_min"],
                              config.METRICS["beacon_validators_count"]["sanity_max"]).all()
    assert a.daily.marked, "the whole window read: done for the day"


def test_backfill_is_resumable_the_store_is_the_state():
    from fetch.base import RateLimited
    first, out1 = _run(_V2Http(fail_after=100, error=RateLimited("HTTP 429", retry_after=3600, headers={})))
    f1 = out1.frame()
    assert len(f1[f1["metric"] == "beacon_chain_eth_primary"]) == 100 and not first.daily.marked
    assert any("355 day(s) left" in e.message for e in out1.log)
    second, out2 = _run(_V2Http(), stored=f1)
    asked = [c["body"]["epoch"]["number"] for c in second.http.calls]
    done = {se.epoch_at(d) for d in pd.to_datetime(f1["date"])}
    assert len(asked) == 355 and not done & set(asked), "a day already holding both series is never asked again"
    assert second.daily.marked
    third, _ = _run(_V2Http(), stored=pd.concat([f1, out2.frame()]))
    assert not third.http.calls and third.daily.marked


def test_backfill_stops_inside_its_time_budget_and_stores_what_it_read():
    a, out = _run(_V2Http(), deadline_s=0.0)
    assert not a.http.calls and not a.daily.marked
    assert any("STOPPED at the tier's time budget" in e.message for e in out.log)


# ================================================================================================ the handover
def _row(project, metric, d, v, src, tier=1):
    return {"date": pd.Timestamp(d), "project": project, "metric": metric, "value": float(v), "source": src,
            "tier": tier, "is_manual": False, "entered_on": ""}


def _agg(rows, asof):
    return {r["key"]: r for r in bw.aggregate(pd.DataFrame(rows), pd.DataFrame(), pd.Timestamp(asof),
                                              gaps=pd.DataFrame(), review=pd.DataFrame()).to_dict("records")}


# P5-1.12 (Jake 2026-11-04) re-declared the overlap: the BigQuery rebuild's 455 days 2025-07-28..2026-10-25 at ±1%
# (beaconcha.in's last week at ±2% is withdrawn with the route); the next leg's rows carry the rebuild's point
VQ = pd.date_range("2025-07-20", "2026-10-25")


def _eth(prim_val, prim_days=pd.date_range("2025-07-20", "2026-11-03"), vq_val=43_700_000.0):
    vq = [_row("Ethereum", "beacon_chain_eth", d, vq_val, "validatorqueue:staked_amount") for d in VQ]
    pv = prim_val if callable(prim_val) else (lambda d: prim_val)
    prim = [_row("Ethereum", "beacon_chain_eth_primary", d, pv(d), config.STAKED_REBUILD_POINT + "[test]")
            for d in prim_days]
    return vq + prim


def test_overlap_week_is_declared_and_the_tolerance_is_not_widened():
    # P5-1.12: narrower than P4-Q4's ±2% on 7 days — ±1% on all 455 days — never wider
    ov = ETH["series_handover"]["beacon_chain_eth"]["overlap"]
    assert ov["days"] == ("2025-07-28", "2026-10-25") and ov["tolerance_pct"] == 1.0 and ov["min_days"] == 455
    assert config.HANDOVER_CONTINUATION[("Ethereum", "beacon_chain_eth")]["requires_overlap"] is True


def test_handover_passes_on_the_455_day_overlap_within_tolerance_and_stale_clears():
    # only the declared 455 days are compared — a day before them at +5% does not count
    val = lambda d: 44_000_000.0 * (1.05 if d < pd.Timestamp("2025-07-28") else 1.0) + (d.day * 1_000)  # noqa: E731
    by = _agg(_eth(val), "2026-11-03")
    r = by["Ethereum|beacon_chain_eth"]
    ho = bw.HANDOVER_OVERLAP[("Ethereum", "beacon_chain_eth")]
    days = pd.date_range("2025-07-28", "2026-10-25")
    assert ho["passed"] and ho["common_days"] == 455 and len(ho["per_day"]) == 455
    exp = [((44_000_000.0 + d.day * 1_000) / 43_700_000.0 - 1) * 100 for d in days]
    assert ho["mean_gap"] == pytest.approx(sum(exp) / 455)
    assert "on 2025-07-28..2026-10-25" in ho["text"] and f"mean gap {sum(exp) / 455:+.2f}%" in ho["text"]
    assert "worst days 2025-07-31 +" in ho["text"]
    assert r["source"] == "derived:=beacon_chain_eth_primary" and r["latest_date"] == "2026-11-03"
    assert r["now"] == pytest.approx(44_000_000.0 + 3_000) and r["status"] != "stale"      # STALE clears
    assert ("Ethereum", "beacon_chain_eth") in bw.HANDOVER_CONTINUED
    assert ("Ethereum", "beacon_chain_eth") not in bw.HANDOVER_HELD


@pytest.mark.parametrize("prim_days,prim_val,why", [
    (pd.date_range("2025-07-29", "2026-11-03"), 44_100_000.0, "454 common day(s)"),     # one overlap day missing
    (pd.date_range("2025-07-20", "2026-11-03"), 44_700_000.0, "+2.29%"),                # all 455 days, outside ±1%
    (pd.date_range("2026-10-26", "2026-11-03"), 44_100_000.0, "share no stored day on 2025-07-28..2026-10-25"),
])
def test_handover_is_held_outside_the_overlap_and_the_cell_says_so(prim_days, prim_val, why):
    r = _agg(_eth(prim_val, prim_days), "2026-11-03")["Ethereum|beacon_chain_eth"]
    assert r["latest_date"] == "2026-10-25" and r["source"] == "validatorqueue:staked_amount"   # never switched blind
    held = bw.HANDOVER_HELD[("Ethereum", "beacon_chain_eth")]
    assert held.startswith("NOT HANDED OVER to beacon_chain_eth_primary") and why in held, held
    assert held in r["why_amber"] and r["status"] == "stale"                                    # stays STALE, said


def test_a_one_day_spike_inside_the_week_holds_it():
    val = lambda d: 46_000_000.0 if d == pd.Timestamp("2026-10-22") else 44_000_000.0  # noqa: E731
    _agg(_eth(val), "2026-11-03")
    ho = bw.HANDOVER_OVERLAP[("Ethereum", "beacon_chain_eth")]
    assert not ho["passed"] and ho["worst_day"] == "2026-10-22" and "2026-10-22 +5.26%" in ho["text"]
    assert "1 day(s) outside ±1%" in ho["text"]


# ================================================================================================ withdrawn routes
def test_p3_a3_routes_are_withdrawn_and_their_rows_removed(tmp_path, monkeypatch):
    import store as sm
    import token_metrics as tm
    sp = ETH["staked_eth_primary"]
    assert "beacon_api" not in sp and sp["beaconchain"]["url"].endswith("/api/v2/ethereum/epoch")
    assert ("Ethereum", "beacon_chain_eth_primary") not in config.SAME_BASIS     # one definition, one point
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(config, "RUN_CLEANUPS", tuple(c for c in config.RUN_CLEANUPS if c["id"].startswith("P4-Q4")))
    st = sm.Store(str(tmp_path / "metrics.db"))
    rows = [("2026-11-02", 44_233_406.0, "beacon_api:validator_inclusion.current_epoch_active_gwei"),
            ("2026-10-19", 44_100_000.0, "beaconchain:epoch.eligibleether"),
            ("2026-10-20", 44_310_000.0, se.POINT)]
    st.upsert(pd.DataFrame([{"date": d, "project": "Ethereum", "metric": "beacon_chain_eth_primary", "value": v,
                             "source": s, "tier": 1} for d, v, s in rows]))
    lines = tm.apply_run_cleanups(st, "r", logging.getLogger("t"))
    left = st.conn.execute("SELECT source FROM metrics WHERE metric='beacon_chain_eth_primary'").fetchall()
    assert left == [(se.POINT,)] and len([x for x in lines if x.startswith("CLEAN-UP P4-Q4")]) >= 2
    st.close()


# ================================================================================================ the lock rate
def test_lock_rate_for_ethereum_is_staked_over_circulating(tmp_path, monkeypatch):
    """CP's lock rate = staked ETH ÷ circulating — the same stake CP's staked column, the float and A1's staking rate
    read (one computation), with the cell's comment saying why staked ETH counts as locked."""
    import xlcalc
    from test_phase2 import CP, _data, _get, _row as _tab_row
    from test_trajectory import _built
    wb, _ = _built(tmp_path, monkeypatch, "Ethereum")
    X = xlcalc.Workbook(wb)
    o = _tab_row(wb, X, CP, "Ethereum")
    circ, stake = _get(o, "Circulating supply (tokens)"), _data(wb, X, "Ethereum|beacon_chain_eth", "now")
    lock = _get(o, "Holding demand — lock rate")
    assert isinstance(lock, float) and lock == pytest.approx(stake / circ) and 0.30 < lock < 0.40, lock
    assert _get(o, "Staked / locked inside circulating") == pytest.approx(stake)
    assert _get(o, "TRADEABLE FLOAT") == pytest.approx(circ - stake)
    a1 = _tab_row(wb, X, "A1 Infrastructure", "Ethereum") if "A1 Infrastructure" in wb.sheetnames else None
    if a1 is not None:
        assert _get(a1, "Staking rate") == pytest.approx(lock)                      # rule 1: one figure
    ws = wb[CP]
    hr = next(r for r in range(1, 12) if ws.cell(r, 1).value == "Project")
    r = next(r for r in range(hr + 1, ws.max_row + 1) if ws.cell(r, 1).value == "Ethereum")
    c = next(c for c in range(1, ws.max_column + 1) if str(ws.cell(hr, c).value or "").startswith("Holding demand — lock"))
    com = ws.cell(r, c).comment.text if ws.cell(r, c).comment else ""
    assert "exit queue" in com and "Jake 2026-11-03" in com, com


def test_lock_rate_reads_the_stake_only_where_declared():
    assert set(config.STAKE_COUNTS_AS_LOCKED) == {"Ethereum"} <= set(config.CP_STAKE_METRIC)
    assert config.not_applicable_reason("Ethereum", "locked_tokens"), "locked_tokens itself stays not applicable"
