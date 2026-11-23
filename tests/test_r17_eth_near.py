"""Round 17 — Ethereum and Near (Jake 2026-11-23; SPEC "Round 17"). Behaviour and tolerance, never wording.

Ethereum: the staked-ETH node read is checked for staleness and against a second source; the declared handover of
beacon_chain_eth and its overlap are untouched. Near: NearBlocks is billed as it bills (limit / 25, 1..10), a run asks
only what is owed, a 429 that survives its wait ends the run's calls, and the archetype key says when it forms."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from fetch import base, nearblocks as nb, scrape, staked_eth as se  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402

ETH = config.PROJECT_BY_NAME["Ethereum"]
NEAR = config.PROJECT_BY_NAME["Near"]
CC = ETH["staked_eth_node"]["cross_check"]


# ================================================================================================ Ethereum
class _Resp:
    def __init__(self, status=200, body=None, raw=b""):
        self.status_code, self._body, self._raw = status, body, raw

    def json(self):
        return self._body

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._raw), 4096):
            yield self._raw[i:i + 4096]


class _Node:
    """A stub beacon node (finalized slot `slot`, n validators of `each` gwei) and a stub second source."""
    def __init__(self, slot, n=200_000, each=220_000_000_000, second=None, second_status=200):
        self.slot, self.second, self.second_status, self.calls = slot, second, second_status, []
        self.bal = (b'{"finalized":true,"data":[' + b",".join(b'{"index":"%d","balance":"%d"}' % (i, each)
                                                           for i in range(n)) + b"]}")

    def get(self, url, stream=False, timeout=None):
        self.calls.append(url)
        if url.endswith("/headers/finalized"):
            return _Resp(body={"data": {"header": {"message": {"slot": str(self.slot)}}}})
        if url == CC["url"]:
            return _Resp(status=self.second_status, body=self.second)
        return _Resp(raw=self.bal)


class _Daily:
    def __init__(self):
        self.marks = set()

    def due(self, k, d):
        return (k, d) not in self.marks

    def done(self, k, d):
        self.marks.add((k, d))


SLOT = 15_716_896                                   # 2026-11-23 09:39:35 UTC, the slot read live on 2026-11-23
NODE_GWEI = 200_000 * 220_000_000_000               # 44,000,000 ETH


def _run_node(monkeypatch, tmp_path, node, now="2026-11-23 09:55"):
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path))
    monkeypatch.setattr(scrape, "robots_verdict", lambda url: (True, "test"))
    monkeypatch.setattr(se, "_utcnow", lambda: pd.Timestamp(now))
    out = FetchOutput()
    daily = _Daily()
    se.BeaconNodeStakedEth(session=node, daily=daily).run([ETH], 1, out)
    return out, daily


def test_the_slot_read_live_is_dated_its_own_day():
    assert se.slot_time(SLOT) == pd.Timestamp("2026-11-23 09:39:35")


def test_the_second_source_within_tolerance_is_stored_and_named(monkeypatch, tmp_path):
    second = {"beaconBalancesSum": str(int(NODE_GWEI * (1 - 0.000003))), "slot": SLOT + 80}     # the −0.0003% measured
    out, daily = _run_node(monkeypatch, tmp_path, _Node(SLOT, second=second))
    assert len(out.frames) == 1 and daily.marks
    f = out.frames[0]
    assert f["metric"].iloc[0] == "beacon_chain_eth_primary" and f["value"].iloc[0] == pytest.approx(44_000_000.0)
    assert pd.Timestamp(f["date"].iloc[0]) == pd.Timestamp("2026-11-23")
    # the cross-check travels in the row's annotation and does not change the measuring point (no handover is made)
    assert base._measuring_point(f["source"].iloc[0]) == config.BEACON_NODE_POINT
    assert "agrees" in f["source"].iloc[0]


def test_a_second_source_outside_the_tolerance_refuses_the_node_read(monkeypatch, tmp_path):
    tol = CC["tolerance_pct"]
    for factor in (1 + 2 * tol / 100, 1 - 2 * tol / 100, 0.5):
        second = {"beaconBalancesSum": str(int(NODE_GWEI * factor)), "slot": SLOT + 80}
        out, daily = _run_node(monkeypatch, tmp_path, _Node(SLOT, second=second))
        assert out.frames == [] and not daily.marks, factor           # nothing stored, the day is asked again
        assert [e.status for e in out.log] == ["failed"]


def test_the_tolerance_is_judged_at_its_edge():
    tol = CC["tolerance_pct"]
    inside = se.cross_check_sum(NODE_GWEI, SLOT, {"beaconBalancesSum": str(int(NODE_GWEI * (1 + 0.9 * tol / 100))),
                                                  "slot": SLOT}, CC)
    outside = se.cross_check_sum(NODE_GWEI, SLOT, {"beaconBalancesSum": str(int(NODE_GWEI * (1 + 1.1 * tol / 100))),
                                                   "slot": SLOT}, CC)
    assert inside["status"] == "agrees" and outside["status"] == "DISAGREES"
    assert inside["gap_pct"] == pytest.approx(0.9 * tol, rel=1e-6)
    # the tolerance is tight: the sum moves < 0.14% a day at the spec churn, the slot gap allowed is 3 hours
    assert tol <= 0.1 and CC["max_slot_gap"] <= 900


@pytest.mark.parametrize("second", [
    None,                                                                   # no answer
    {"beaconBalancesSum": "0", "slot": SLOT},                               # a zero is not a reading
    {"beaconBalancesSum": 4.4e16, "slot": SLOT},                            # a float is not a gwei amount
    {"beaconBalancesSum": str(NODE_GWEI)},                                  # no slot: cannot be placed in time
    {"beaconBalancesSum": str(NODE_GWEI // 2), "slot": SLOT + 7200},        # a day away: not comparable, never judged
])
def test_a_cross_check_that_cannot_be_made_stores_the_node_sum_and_says_so(monkeypatch, tmp_path, second):
    out, daily = _run_node(monkeypatch, tmp_path, _Node(SLOT, second=second))
    assert len(out.frames) == 1 and daily.marks
    src = out.frames[0]["source"].iloc[0]
    assert "NOT MADE" in src and "agrees" not in src
    assert out.frames[0]["value"].iloc[0] == pytest.approx(44_000_000.0)    # the node's own sum, never the other's


def test_a_second_source_that_errors_does_not_fail_the_read(monkeypatch, tmp_path):
    out, daily = _run_node(monkeypatch, tmp_path, _Node(SLOT, second=None, second_status=503))
    assert len(out.frames) == 1 and "NOT MADE" in out.frames[0]["source"].iloc[0]


def test_a_stale_finalized_state_is_refused(monkeypatch, tmp_path):
    max_h = ETH["staked_eth_node"]["max_age_hours"]
    assert max_h <= 6
    second = {"beaconBalancesSum": str(NODE_GWEI), "slot": SLOT}
    late = se.slot_time(SLOT) + pd.Timedelta(hours=max_h, minutes=1)
    out, daily = _run_node(monkeypatch, tmp_path, _Node(SLOT, second=second), now=str(late))
    assert out.frames == [] and not daily.marks and out.log[0].status == "failed"
    just = se.slot_time(SLOT) + pd.Timedelta(hours=max_h) - pd.Timedelta(minutes=1)
    out, daily = _run_node(monkeypatch, tmp_path, _Node(SLOT, second=second), now=str(just))
    assert len(out.frames) == 1


def test_the_bounds_and_the_declared_handover_are_not_loosened():
    m = config.METRICS["beacon_chain_eth_primary"]
    assert (m["sanity_min"], m["sanity_max"]) == (1e7, 7e7)
    assert ETH["staked_eth_node"]["min_entries"] == 100_000
    hc = config.HANDOVER_CONTINUATION[("Ethereum", "beacon_chain_eth")]
    assert hc["requires_overlap"] is True and hc["from_metric"] == "beacon_chain_eth_primary"
    ov = ETH["series_handover"]["beacon_chain_eth"]["overlap"]
    assert ov["tolerance_pct"] == 1.0 and ov["min_days"] == 455 and ov["days"] == ("2025-07-28", "2026-10-25")
    assert ov["b_point"] == config.STAKED_REBUILD_POINT
    assert ETH["series_handover"]["beacon_chain_eth_primary"]["ordered_points"] == (
        config.STAKED_REBUILD_POINT, config.BEACON_NODE_POINT)
    # no new metric this round
    assert "beacon_chain_eth_node" not in config.METRICS and "beacon_chain_eth_ultrasound" not in config.METRICS


def test_the_handover_is_still_held_without_its_overlap():
    """validatorqueue to 2026-10-25, node rows from 2026-11-04, no rebuild row: beacon_chain_eth keeps its stale leg and
    says why — the node rows are never joined blind."""
    import build_workbook as bw
    cols = ["date", "project", "metric", "value", "source", "tier", "run_id"]
    vq = pd.DataFrame([(d, "Ethereum", "beacon_chain_eth", 43.7e6, "validatorqueue:staked_amount", 1, "r")
                       for d in pd.date_range("2025-07-28", "2026-10-25")], columns=cols)
    node = pd.DataFrame([(d, "Ethereum", "beacon_chain_eth_primary", 44.5e6,
                          config.BEACON_NODE_POINT + f"[finalized slot {i}]", 1, "r")
                         for i, d in enumerate(pd.date_range("2026-11-04", "2026-11-23"))], columns=cols)
    groups = {("Ethereum", "beacon_chain_eth"): vq, ("Ethereum", "beacon_chain_eth_primary"): node}
    bw._handover_continuation_views(groups)
    g = groups[("Ethereum", "beacon_chain_eth")]
    assert pd.to_datetime(g["date"]).max() == pd.Timestamp("2026-10-25")
    assert ("Ethereum", "beacon_chain_eth") in bw.HANDOVER_HELD
    bw.HANDOVER_HELD.clear()


# ================================================================================================ Near
@pytest.mark.parametrize("path,params,credits", [
    ("/v3/txn-stats", {"limit": 100}, 4),                       # NearBlocks' own example: limit=100 uses 4
    ("/v3/txn-stats", {"limit": 50}, 2),                        # "limit 50 will use 2 credits"
    ("/v3/txn-stats", {"limit": 25}, 1),
    ("/v3/txn-stats", {"limit": 26}, 2),
    ("/v3/txn-stats", {"limit": 3}, 1),
    ("/v3/accounts/treasury.near/stats/balance", {"limit": 365}, 10),     # "from 1 up to 10 credits"
    ("/v3/accounts/treasury.near/stats/balance", {"limit": 250}, 10),
    ("/v3/accounts/treasury.near/stats/balance", {}, 1),
    ("/v3/validators/info", {}, 1),                             # "Validator endpoints always use 1 credit"
    ("/v3/validators", {"limit": 100}, 1),
    ("/v3/accounts/x/txns", {"per_page": 50}, 2),
])
def test_a_call_is_billed_as_nearblocks_bills_it(path, params, credits):
    assert nb.call_credits(path, params) == credits


def test_the_free_plan_figures_are_not_raised():
    assert (nb.CREDITS_PER_MINUTE, nb.CREDITS_PER_DAY) == (6, 333)
    assert config.NEARBLOCKS_BILLING["free_plan"] == {"per_minute": 6, "per_day": 333, "per_month": 10_000}


def test_no_minute_window_is_overspent_and_a_ten_credit_call_waits_for_an_empty_one(_nearblocks_fake_clock):
    p = nb._Pacer()
    stamps = []
    for c in (4, 1, 1, 4, 10, 1, 4, 4):
        p.wait(c)
        p.charge(c)
        stamps.append((nb._clock(), c))
    for t0, _c in stamps:
        inside = [c for t, c in stamps if t0 <= t < t0 + 60]
        assert sum(inside) <= nb.CREDITS_PER_MINUTE or len(inside) == 1, (t0, inside)
    assert p.total == 29


def test_a_wait_past_the_deadline_is_not_made(_nearblocks_fake_clock):
    p = nb._Pacer()
    p.wait(6, deadline=nb._clock() + 30)
    p.charge(6)
    with pytest.raises(nb.PacedOut):
        p.wait(1, deadline=nb._clock() + 30)
    assert _nearblocks_fake_clock == []                          # it did not sleep toward a budget it would pass
    assert p.wait(1, deadline=nb._clock() + 120) > 0             # with room, it waits


class _NB:
    """A stub NearBlocks: answers each path's shape, records (path, params); `refuse` -> 429 on every call from the
    n-th."""
    def __init__(self, today, refuse_from=None, rows_per_day=1):
        self.today, self.calls, self.refuse_from, self.rows_per_day = pd.Timestamp(today), [], refuse_from, rows_per_day
        self.last_headers = {}

    def _days(self, n):
        return [(self.today - pd.Timedelta(days=k + 1)).strftime("%Y-%m-%d") for k in range(n)]

    def get(self, url, params=None, headers=None):
        path = url.split("api.nearblocks.io")[-1]
        self.calls.append((path, dict(params or {})))
        if self.refuse_from is not None and len(self.calls) >= self.refuse_from:
            e = RuntimeError("HTTP 429 from api.nearblocks.io: You have exceeded your API request limit.")
            e.status = 429
            raise e
        n = int((params or {}).get("limit") or 25)
        if path.endswith("/stats/balance"):
            if self.rows_per_day == 1:
                return {"data": [{"date": d, "amount": str(10 ** 24 * (5 + i)), "amount_staked": "0"}
                                 for i, d in enumerate(self._days(n))]}
            # an account whose rows do not reach back a day each (two a day): n rows cover only n / 2 days
            days = self._days(-(-n // self.rows_per_day))
            return {"data": [{"date": d, "amount": str(10 ** 24), "amount_staked": "0"}
                             for d in days for _ in range(self.rows_per_day)][:n]}
        if path == "/v3/validators/info":
            return {"data": {"total_stake": str(596 * 10 ** 30)}}
        if path == "/v3/stats":
            return {"data": {"total_supply": str(1312 * 10 ** 30), "circulating_supply": str(1312 * 10 ** 30)}}
        if path.endswith("/receipts"):
            return {"data": []}
        return {"data": [{"date": d, "txns": 1, "txn_fee": "1", "active_accounts": 1, "daily": "1"}
                         for d in self._days(n)]}

    def post(self, url, json_body=None, headers=None):
        import json
        st = json.dumps({"Running": {"participants": {"participants": [["op1.near", 0, {}], ["op2.near", 1, {}]]}}})
        return {"result": {"result": list(st.encode())}}


TODAY = "2026-11-23"


def _near_run(monkeypatch, http, last_dates, stored=None, deadline_s=None):
    monkeypatch.setenv("NEARBLOCKS_API_KEY", "nb-secret-123")
    monkeypatch.setattr(scrape, "robots_verdict", lambda url: (True, "test"))
    monkeypatch.setattr(nb, "today", lambda: pd.Timestamp(TODAY))
    out = FetchOutput()
    a = nb.NearBlocks(http=http, last_dates=last_dates, stored_long=stored, deadline_s=deadline_s)
    a.run([copy.deepcopy(NEAR)], 35, out)
    return a, out


def _held_history():
    """The store holding the two fixed histories through the day before their store_before."""
    rows = []
    for key in ("foundation_history", "treasury_history"):
        bh = NEAR["nearblocks"][key]
        day = pd.Timestamp(bh["store_before"]) - pd.Timedelta(days=1)
        rows.append({"date": day, "project": "Near", "metric": bh["metric"], "value": 1.0,
                     "source": f"nearblocks:stats/balance[{'+'.join(bh['accounts'])}]"})
    return pd.DataFrame(rows)


def _all_last(day):
    spec = NEAR["nearblocks"]
    ms = list(spec["metrics"]) + [spec["balance_history"]["metric"], spec["payer_out"]["metric"]]
    return {("Near", m): day for m in ms}


def test_a_normal_day_asks_only_what_is_owed(monkeypatch, _nearblocks_fake_clock):
    """Everything held through 2026-11-21, read on 2026-11-23: one day owed. Each stats path and each revenue wallet
    asks 3 rows (owed + 2), the histories that end before a fixed day are not asked, the payer's receipts are asked
    with the predecessor filter — 12 credits, where the old run asked 100- and 365-row pages (~70 credits)."""
    http = _NB(TODAY)
    a, out = _near_run(monkeypatch, http, _all_last("2026-11-21"), stored=_held_history())
    by_path = {}
    for path, params in http.calls:
        by_path.setdefault(path, []).append(params)
    spec = NEAR["nearblocks"]
    for path in {m["path"] for m in spec["metrics"].values()}:
        assert by_path[path] == [{"limit": 3}], (path, by_path[path])
    for acct in spec["balance_history"]["accounts"]:
        assert by_path[f"/v3/accounts/{acct}/stats/balance"] == [{"limit": 3}]
    for key in ("foundation_history", "treasury_history"):
        for acct in spec[key]["accounts"]:
            assert f"/v3/accounts/{acct}/stats/balance" not in by_path, acct
    payer = spec["payer_out"]["account"]
    assert by_path[f"/v3/accounts/{payer}/receipts"] == [{"limit": 100, "predecessor": payer}]
    spent = sum(nb.call_credits(p, q) for p, q in http.calls)
    assert spent == a.pacer.total == 12
    assert not [e for e in out.log if e.status == "failed"], [e.message for e in out.log if e.status == "failed"]
    # the revenue wallets' sum is stored through yesterday, from a day at or before the last stored close
    f = out.frame()
    bal = f[f["metric"] == spec["balance_history"]["metric"]]
    assert pd.to_datetime(bal["date"]).max() == pd.Timestamp("2026-11-22")
    assert pd.to_datetime(bal["date"]).min() <= pd.Timestamp("2026-11-21")


def test_a_second_run_the_same_day_asks_almost_nothing(monkeypatch, _nearblocks_fake_clock):
    http = _NB(TODAY)
    last = _all_last("2026-11-22")
    last[("Near", NEAR["nearblocks"]["validators_info"]["metric"])] = TODAY
    last[("Near", NEAR["nearblocks"]["supply_total"]["metric"])] = TODAY
    _near_run(monkeypatch, http, last, stored=_held_history())
    assert http.calls == []


def test_a_history_not_yet_held_is_still_read_in_full(monkeypatch, _nearblocks_fake_clock):
    http = _NB(TODAY)
    _near_run(monkeypatch, http, _all_last("2026-11-22"), stored=None)
    asked = {p: q for p, q in http.calls if p.endswith("/stats/balance")}
    for key in ("foundation_history", "treasury_history"):
        for acct in NEAR["nearblocks"][key]["accounts"]:
            assert asked[f"/v3/accounts/{acct}/stats/balance"] == {"limit": 365}


def test_a_top_up_that_does_not_reach_the_last_close_reads_the_full_history(monkeypatch, _nearblocks_fake_clock):
    """Ten days owed, an account with two rows a day: 12 rows reach back 6 days, short of the last stored close — the
    full 365 are read; a hole is never carried over."""
    http = _NB(TODAY, rows_per_day=2)
    last = _all_last("2026-11-22")
    last[("Near", NEAR["nearblocks"]["balance_history"]["metric"])] = "2026-11-12"
    _near_run(monkeypatch, http, last, stored=_held_history())
    acct = NEAR["nearblocks"]["balance_history"]["accounts"][0]
    limits = [q["limit"] for p, q in http.calls if p == f"/v3/accounts/{acct}/stats/balance"]
    assert limits == [12, 365]


def test_a_429_that_survives_its_wait_ends_the_runs_calls(monkeypatch, _nearblocks_fake_clock):
    http = _NB(TODAY, refuse_from=2)
    a, out = _near_run(monkeypatch, http, _all_last("2026-11-21"), stored=_held_history())
    assert len(http.calls) == 2                       # the refused call was the last one made: never hammered
    assert a.limited
    failed = [e for e in out.log if e.status == "failed"]
    assert len(failed) >= 5                           # every read still owed says so; none is silently dropped
    f = out.frame()
    assert set(f["metric"]) <= set(NEAR["nearblocks"]["metrics"])     # only what the first call read
    assert "nb-secret-123" not in " ".join(str(e.message) for e in out.log)


def test_the_run_stops_asking_before_its_budget(monkeypatch, _nearblocks_fake_clock):
    """A first read (nothing stored) under a 30 s budget: the calls that fit the first minute window are made, the rest
    are not waited for and say so — the tier is never abandoned mid-wait."""
    http = _NB(TODAY)
    a, out = _near_run(monkeypatch, http, {}, stored=None, deadline_s=30.0)
    assert sum(_nearblocks_fake_clock) <= 30.0
    assert a.pacer.total <= nb.CREDITS_PER_MINUTE + nb.CREDITS_MAX_PER_CALL
    assert any("NOT CALLED" in str(e.message) for e in out.log if e.status == "failed")


def test_the_tier_passes_its_budget_and_the_store_to_the_reader():
    import fetch
    tier = next(t for t in fetch.ADAPTERS if t[0] == "nearblocks") if hasattr(fetch, "ADAPTERS") else None
    if tier is None:
        src = (ROOT / "fetch" / "__init__.py").read_text(encoding="utf-8")
        i = src.index('("nearblocks", 1')
        assert "stored_long" in src[i:i + 300] and "deadline_s" in src[i:i + 300]
        return
    a = tier[2]({"last_dates": {}, "stored_long": None})
    assert a.deadline_s == fetch.TIER_BUDGET_S["nearblocks"] - 10.0
    # a normal day's 12 credits are two minute windows: inside the budget with a window to spare
    assert fetch.TIER_BUDGET_S["nearblocks"] >= 60 * 2 + 60


# ================================================================================================ the archetype key
def _g(project, metric, days, value=1.0):
    return pd.DataFrame({"date": days, "project": project, "metric": metric, "value": value,
                         "source": "t", "tier": 1, "run_id": "r"})


def test_the_key_says_when_it_forms_from_the_stored_series():
    import reader
    met = config.ARTEMIS_SETTLEMENT["metric"]
    circ = _g("Near", "circulating_supply_used", pd.date_range("2025-01-01", "2026-11-23"), 1.2e9)
    # (a) no 365-day sum yet: the daily series from 2025-12-29 without a hole -> the sum on 2026-12-28, the change a
    # year later
    daily = _g("Near", met, pd.date_range("2025-12-29", "2026-11-22"))
    v, why = reader.settlement_key({("Near", met): daily, ("Near", "circulating_supply_used"): circ}, "Near")
    assert v is None
    import done_check as dc
    ds = dc.dates_in(why)
    assert max(ds).isoformat() == "2027-12-28" and pd.Timestamp("2026-12-28").date() in ds
    assert dc.classify("n/a", why, pd.Timestamp("2026-11-23").date())[:2] == (True, "history")
    assert dc.classify("unranked", "Unranked: " + why, pd.Timestamp("2026-11-23").date())[:2] == (True, "history")
    # (b) a hole restarts the count: the run that ends at the last day starts after it
    holed = _g("Near", met, [d for d in pd.date_range("2025-12-29", "2026-11-22") if d != pd.Timestamp("2026-06-01")])
    _v, why_h = reader.settlement_key({("Near", met): holed, ("Near", "circulating_supply_used"): circ}, "Near")
    assert max(dc.dates_in(why_h)) == (pd.Timestamp("2026-06-02") + pd.Timedelta(days=729)).date()
    # (c) a series that has stopped gets NO date (Ethereum's Artemis export ends 2026-08-25): still a blocker
    stopped = _g("Near", met, pd.date_range("2025-09-08", "2026-08-25"))
    _v, why_s = reader.settlement_key({("Near", met): stopped, ("Near", "circulating_supply_used"): circ}, "Near")
    assert why_s == "no trailing-365-day settlement volume series"
    assert dc.classify("n/a", why_s, pd.Timestamp("2026-11-23").date())[0] is False
    # (d) nothing stored: no date is guessed
    assert reader.settlement_key({("Near", "circulating_supply_used"): circ}, "Near")[1] == \
        "no trailing-365-day settlement volume series"


def test_the_key_with_a_sum_held_says_the_day_its_change_forms():
    import done_check as dc
    import reader
    circ = _g("Near", "circulating_supply_used", pd.date_range("2025-01-01", "2026-12-05"), 1.2e9)
    s365 = _g("Near", "settlement_volume_365d_usd", pd.date_range("2026-12-01", "2026-12-04"), 5e10)
    groups = {("Near", "settlement_volume_365d_usd"): s365, ("Near", "circulating_supply_used"): circ}
    v, why = reader.settlement_key(groups, "Near")
    assert v is None and max(dc.dates_in(why)).isoformat() == "2027-12-01"
    assert dc.classify("n/a", why, pd.Timestamp("2026-12-05").date())[:2] == (True, "history")
    # the trajectory's earlier quarters keep their reason (no date promised for a past quarter)
    _v, why_q = reader.settlement_key(groups, "Near", back_days=2)
    assert "first forms" not in why_q
    # a full two years: a number, unchanged arithmetic
    s2 = _g("Near", "settlement_volume_365d_usd", pd.date_range("2025-12-01", "2026-12-04"), 5e10)
    s2.loc[s2["date"] == pd.Timestamp("2026-12-04"), "value"] = 6e10
    v, _why = reader.settlement_key({("Near", "settlement_volume_365d_usd"): s2,
                                     ("Near", "circulating_supply_used"): circ}, "Near")
    assert v == pytest.approx(0.2)
