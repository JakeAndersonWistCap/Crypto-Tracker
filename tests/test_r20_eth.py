"""Round 20 — the ETH lane (Jake 2026-11-24; SPEC "Round 20 — Ethereum run fixes, NearBlocks pacing, BigQuery budget").
Behaviour and numbers, never wording.

1. Staked ETH, the validator count and the staking yield from beaconcha.in's v2 API (answers shaped as its OpenAPI
   shows them, read 2026-11-23 — NO LIVE KEYED ANSWER WAS READ: keyless the API says 401); the public beacon node as
   the cross-check; validatorqueue retired; staked_eth_rebuild.py refuses to bill; the handover a declared change of
   quantity with its step measured from the store.
2. Ethereum's rule-3 lines: issuance is outflow (b), counted once; the two declared n/a.
3. Settlement: the labelled seam (+6.77%), the 1,000 GB budget, the combined schedule, when the 89 days fill.
4. Fees: Blockscout the figure on every cell, DefiLlama the cross-check.
5. NearBlocks: the tier's budget covers the pacing of its dearest day — every owed read is called."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import fetch  # noqa: E402
import near_p2p_history as nh  # noqa: E402
import settlement_extend  # noqa: E402
import staked_eth_rebuild  # noqa: E402
import test_r17_eth_near as r17  # noqa: E402
from fetch import base, nearblocks as nb, scrape, staked_eth as se  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402

ETH = config.PROJECT_BY_NAME["Ethereum"]
BC = ETH["staked_eth_beaconchain"]
KEY = "bc-secret-key-0123456789"
TODAY = pd.Timestamp("2026-12-02")                   # the first day after beaconcha.in's declared cooldown
COLS = ["date", "project", "metric", "value", "source", "tier"]


@pytest.fixture(autouse=True)
def _beaconchain_reader_as_wired_in_r20(monkeypatch):
    """R21b (Jake 2026-11-25): beaconcha.in is not the standing source (config not_standing; tests/test_r21b_lead.py).
    These tests read the R20 reader's own logic, which stays on record: they run it without that switch."""
    spec = config.PROJECT_BY_NAME["Ethereum"]["staked_eth_beaconchain"]
    if "not_standing" in spec:
        monkeypatch.delitem(spec, "not_standing")


# ================================================================================== 1. beaconcha.in: the stub API
def _epoch_body(epoch: int, eth: float, count: int = 1_050_000, finality: str = "finalized") -> dict:
    """POST /api/v2/ethereum/epoch, the fields the OpenAPI marks required (docs.beaconcha.in, read 2026-11-23)."""
    wei = int(round(eth * 1e9)) * 10 ** 9
    return {"data": {"epoch": epoch, "timestamp": se.GENESIS_UNIX + epoch * se.EPOCH_SECONDS,
                     "slots": {"start": epoch * 32, "end": epoch * 32 + 31}, "sync_committee_period": 1700,
                     "validators_count": count, "total_validator_balance": str(wei),
                     "average_validator_balance": str(wei // count),
                     "total_active_balance": str(int(wei * 0.994)), "attesting_balance": str(int(wei * 0.992)),
                     "participation_rate": 0.998, "finality": finality}}


def _store_day(n: int, apr: float = 0.0301, cl: float = 0.0262) -> dict:
    """One ETH.STORE datapoint (EthStore.Data): beaconchain-day n, apr as fractions."""
    t0 = se.GENESIS_UNIX + n * 86400
    return {"day": n, "day_start": t0, "day_end": t0 + 86400, "effective_balance": "44000000000000000000000000",
            "start_balance": "44700000000000000000000000", "end_balance": "44703000000000000000000000",
            "deposits": "0", "rewards": {"total": "3", "consensus_layer": "2", "execution_layer": "1"},
            "apr": {"combined": apr, "consensus_layer": cl, "execution_layer": round(apr - cl, 6)},
            "moving_averages": {"window_7d": {"apr": {"combined": 0.0305, "consensus_layer": 0.0262,
                                                      "execution_layer": 0.0043},
                                              "rewards": {"consensus_layer": "2", "execution_layer": "1"}},
                                "window_31d": {"apr": {"combined": 0.0309, "consensus_layer": 0.0263,
                                                       "execution_layer": 0.0046},
                                               "rewards": {"consensus_layer": "2", "execution_layer": "1"}}}}


def _day_index(day) -> int:
    """The beaconchain-day that STARTS on the UTC day `day` (12:00:23 UTC)."""
    return int((pd.Timestamp(day).timestamp() + 12 * 3600 + 23 - se.GENESIS_UNIX) // 86400)


class _Api:
    """A stub of beaconcha.in v2 behind fetch.base.Http's post(): staked ETH grows 1,000 ETH a day."""
    def __init__(self, today=TODAY, base_eth=44_000_000.0, today_factor=1.0, unfinalized=(), status=None):
        self.today, self.base, self.today_factor = pd.Timestamp(today), base_eth, today_factor
        self.unfinalized, self.status, self.calls = set(unfinalized), status, []
        self.stats = {"calls": 0, "http_s": 0.0, "retries": 0, "waited_s": 0.0, "slowest_s": 0.0, "slowest": "",
                      "times": []}
        self.s = None

    def eth_on(self, day) -> float:
        v = self.base + 1000.0 * (pd.Timestamp(day) - pd.Timestamp("2026-10-19")).days
        return v * (self.today_factor if pd.Timestamp(day) == self.today else 1.0)

    def post(self, url, json_body=None, headers=None, deadline=None):
        self.calls.append((url, json_body, dict(headers or {})))
        if self.status:
            e = base.HttpError(self.status, url, "endpoint not allowed for your subscription tier")
            raise e
        if url.endswith("/epoch"):
            ep = json_body["epoch"]["number"]
            day = se.day_of_epoch(ep)
            return _epoch_body(ep, self.eth_on(day), finality="not_finalized" if day in self.unfinalized else "finalized")
        assert url.endswith("/eth-store") and json_body["page_size"] == 10
        newest = _day_index(self.today - pd.Timedelta(days=2))        # a morning run: the day that ended yesterday noon
        reach = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}[json_body["range"]["evaluation_window"]]
        days = list(range(newest, newest - reach, -1))
        start = int(json_body.get("cursor") or 0)
        page = days[start:start + 10]
        nxt = str(start + 10) if start + 10 < len(days) else ""
        return {"data": [_store_day(n) for n in page], "paging": {"next_cursor": nxt},
                "range": {"slot": {"start": 0, "end": 1}, "epoch": {"start": 0, "end": 1},
                          "timestamp": {"start": 0, "end": 1}}}


class _NodeStub:
    """The public beacon node's reading, as BeaconNodeStakedEth.measure returns it."""
    def __init__(self, eth=None, hours=7.0, fail=False):
        self.eth, self.hours, self.fail, self.stored = eth, hours, fail, []

    def measure(self, spec):
        if self.fail:
            return None, ["https://node: RuntimeError: HTTP 503"]
        when = TODAY + pd.Timedelta(hours=self.hours)
        return {"node": "https://node", "slot": 15_800_000, "when": when, "eth": self.eth, "n": 2_400_000,
                "nz": 790_000, "chk": {"status": "agrees", "text": "cross-check agrees: ultrasound"}}, []

    def store(self, name, spec, got, out):
        self.stored.append(got)
        out.add(base.tidy([(got["when"].normalize(), got["eth"])], name, spec["metric"],
                          f"{spec['point']}[finalized slot {got['slot']}]", 1), "beacon_api", name, "node row", 1)


def _stored(rows) -> pd.DataFrame:
    return pd.DataFrame([{"date": pd.Timestamp(d), "project": "Ethereum", "metric": m, "value": v, "source": s}
                         for d, m, v, s in rows], columns=["date", "project", "metric", "value", "source"])


def _run(monkeypatch, api, node, key=KEY, stored=None, now="2026-12-02 07:00", deadline_s=100.0, spec=None):
    monkeypatch.setattr(se, "today", lambda: pd.Timestamp(now).normalize())
    monkeypatch.setattr(se, "_utcnow", lambda: pd.Timestamp(now))
    monkeypatch.setattr(base, "host_cooldown", lambda host, now=None: None if pd.Timestamp(now_) >= COOL_END else COOL)
    out, daily = FetchOutput(), r17._Daily()
    a = se.BeaconchainEth(http=api, daily=daily, key=key, stored_long=stored, deadline_s=deadline_s, node=node)
    p = dict(ETH, staked_eth_beaconchain=spec) if spec else ETH
    a.run([p], 1, out)
    return a, out, daily


COOL_END = pd.Timestamp("2026-12-01 15:02:02")
COOL = "beaconcha.in is in a cooldown until 2026-12-01T15:02:02Z (declared) — NOT CALLED"
now_ = "2026-12-02 07:00"


def _frames(out) -> dict:
    got = {}
    for f in out.frames:
        got.setdefault(f["metric"].iloc[0], []).append(f)
    return {m: pd.concat(fs, ignore_index=True) for m, fs in got.items()}


# ---------------------------------------------------------------------------------------- the keyed read
def test_the_first_keyed_run_stores_the_missing_days_the_count_and_the_yield(monkeypatch):
    api = _Api()
    node = _NodeStub(eth=api.eth_on(TODAY) * 1.0004)                       # a few hours later, +0.04%
    a, out, daily = _run(monkeypatch, api, node)
    got = _frames(out)
    days = pd.date_range("2026-10-19", "2026-12-02")                        # validatorqueue's last week + every day since
    st = got["beacon_chain_eth_primary"].set_index("date")["value"]
    assert list(st.index) == list(days) and len(days) == 45
    # wei -> ETH, each day at its own first epoch (00:00 UTC): the stub's 1,000 ETH a day
    assert st[pd.Timestamp("2026-10-19")] == pytest.approx(44_000_000.0)
    assert st[pd.Timestamp("2026-10-26")] == pytest.approx(44_007_000.0)    # the first day validatorqueue lacks
    assert st[TODAY] == pytest.approx(44_044_000.0)
    assert set(got["beacon_chain_eth_primary"]["source"].map(base._measuring_point)) == {config.BEACONCHAIN_POINT}
    assert (got["beacon_validators_count"]["value"] == 1_050_000).all() and len(got["beacon_validators_count"]) == 45
    # ETH.STORE: a fraction, dated the UTC day its beaconchain-day starts; the newest is two days back on a morning run
    y = got["staking_yield_pct"].set_index("date")["value"]
    assert y.index.max() == pd.Timestamp("2026-11-30") and y.index.min() == pd.Timestamp("2026-10-19")
    assert len(y) == 43 and (y == 0.0301).all()
    assert set(got["staking_yield_pct"]["source"].map(base._measuring_point)) == {se.ETHSTORE_POINT}
    # the requests: one per day of history + the ETH.STORE pages of the 90-day window down to the oldest owed day
    epochs = [c for c in api.calls if c[0].endswith("/epoch")]
    stores = [c for c in api.calls if c[0].endswith("/eth-store")]
    assert len(epochs) == 45 and len(stores) == 5 and a.requests == 50 <= BC["max_requests_per_run"]
    assert {c[1]["range"]["evaluation_window"] for c in stores} == {"90d"}
    assert epochs[0][1] == {"chain": "mainnet", "epoch": {"number": se.epoch_at(TODAY)}}      # newest first
    # the key: in the Authorization header of every call, in no log line and no stored source
    assert all(c[2] == {"Authorization": f"Bearer {KEY}"} for c in api.calls)
    assert KEY not in " ".join(str(e.message) for e in out.log) + " ".join(
        s for f in out.frames for s in f["source"].astype(str))
    # today's row was set against the node and the day is done; the node's own row is not stored
    assert any("cross-check agrees" in str(e.message) for e in out.log) and node.stored == []
    assert (se.stake_mark("Ethereum", "beacon_chain_eth_primary"), "2026-12-02") in daily.marks
    assert (se.stake_mark("Ethereum", "staking_yield_pct"), "2026-12-02") in daily.marks


def test_a_later_day_asks_one_epoch_and_one_page(monkeypatch):
    api = _Api()
    held = [(d, "beacon_chain_eth_primary", 44e6, config.BEACONCHAIN_POINT)
            for d in pd.date_range("2026-10-19", "2026-12-01")]
    held += [(d, "beacon_validators_count", 1e6, se.COUNT_POINT) for d in pd.date_range("2026-10-19", "2026-12-01")]
    held += [(d, "staking_yield_pct", 0.03, se.ETHSTORE_POINT + "[x]")
             for d in pd.date_range("2026-10-19", "2026-11-29")]
    a, out, _ = _run(monkeypatch, api, _NodeStub(eth=api.eth_on(TODAY)), stored=_stored(held))
    assert [c[0].rsplit("/", 1)[-1] for c in api.calls] == ["epoch", "eth-store"] and a.requests == 2
    assert api.calls[1][1]["range"]["evaluation_window"] == "7d"
    got = _frames(out)
    assert list(got["beacon_chain_eth_primary"]["date"]) == [TODAY]
    assert list(got["staking_yield_pct"]["date"]) == [pd.Timestamp("2026-11-30")]


def test_the_run_never_asks_more_than_its_cap_and_resumes_from_the_store(monkeypatch):
    api = _Api()
    spec = dict(BC, max_requests_per_run=10)
    a, out, daily = _run(monkeypatch, api, _NodeStub(eth=api.eth_on(TODAY)), spec=spec)
    assert a.requests == 10 == len(api.calls)
    st = _frames(out)["beacon_chain_eth_primary"]
    assert list(st["date"]) == list(pd.date_range("2026-11-23", "2026-12-02"))          # the newest ten days
    assert any("STOPPED" in str(e.message) and "35 day(s) left" in str(e.message) for e in out.log)
    assert not daily.marks                    # not done: the next run of the day carries on
    held = [(d, m, 1.0, pt) for d in st["date"] for m, pt in (("beacon_chain_eth_primary", config.BEACONCHAIN_POINT),
                                                              ("beacon_validators_count", se.COUNT_POINT))]
    api2 = _Api()
    _run(monkeypatch, api2, _NodeStub(eth=api2.eth_on(TODAY)), stored=_stored(held), spec=spec)
    asked = [se.day_of_epoch(c[1]["epoch"]["number"]) for c in api2.calls if c[0].endswith("/epoch")]
    assert asked == list(pd.date_range("2026-11-13", "2026-11-22")[::-1])               # the next ten, never a held day


# ---------------------------------------------------------------------------------------- the cross-check
@pytest.mark.parametrize("factor,stored", [(1 + 0.9 * 0.0025, True), (1 - 0.9 * 0.0025, True),
                                           (1 + 1.1 * 0.0025, False), (1 - 1.1 * 0.0025, False), (0.5, False)])
def test_todays_row_is_refused_when_the_node_disagrees(monkeypatch, factor, stored):
    assert BC["cross_check"]["tolerance_pct"] == 0.25
    api = _Api()
    a, out, daily = _run(monkeypatch, api, _NodeStub(eth=api.eth_on(TODAY) * factor))
    st = _frames(out)["beacon_chain_eth_primary"]
    assert (TODAY in set(st["date"])) is stored
    assert len(st) == (45 if stored else 44)             # the past days are stored either way (no past cross-check)
    assert ((se.stake_mark("Ethereum", "beacon_chain_eth_primary"), "2026-12-02") in daily.marks) is stored
    if not stored:
        assert any("REFUSED" in str(e.message) and "DISAGREES" in str(e.message) for e in out.log)


def test_the_cross_check_needs_the_same_day():
    cc = BC["cross_check"]
    node = {"when": TODAY + pd.Timedelta(hours=27), "slot": 1, "eth": 44e6}
    assert se.node_cross_check(44e6, TODAY, node, cc)["status"] == "not made"          # more than 26 h apart
    assert se.node_cross_check(44e6, TODAY, None, cc)["status"] == "not made"
    ok = se.node_cross_check(44e6, TODAY, dict(node, when=TODAY + pd.Timedelta(hours=7), eth=44.05e6), cc)
    assert ok["status"] == "agrees" and ok["gap_pct"] == pytest.approx(0.1136, abs=1e-3)


def test_an_epoch_not_yet_finalized_is_refused_and_asked_again(monkeypatch):
    api = _Api(unfinalized=[TODAY])
    a, out, daily = _run(monkeypatch, api, _NodeStub(eth=api.eth_on(TODAY)))
    st = _frames(out)["beacon_chain_eth_primary"]
    assert TODAY not in set(st["date"]) and len(st) == 44
    assert (se.stake_mark("Ethereum", "beacon_chain_eth_primary"), "2026-12-02") not in daily.marks


# ---------------------------------------------------------------------------------------- no key, the cooldown
def test_unconfigured_it_says_so_and_stores_nothing_of_its_own(monkeypatch):
    api, node = _Api(), _NodeStub(eth=44_700_000.0)
    a, out, daily = _run(monkeypatch, api, node, key="")
    assert api.calls == [] and a.requests == 0
    assert [e.status for e in out.log if "BEACONCHAIN_API_KEY" in str(e.message)] == ["unconfigured"]
    got = _frames(out)
    # the node's reading is the day's row while the series holds no beaconcha.in row (the earlier leg)
    assert set(got) == {"beacon_chain_eth_primary"} and got["beacon_chain_eth_primary"]["value"].iloc[0] == 44_700_000.0
    assert base._measuring_point(got["beacon_chain_eth_primary"]["source"].iloc[0]) == config.BEACON_NODE_POINT
    assert (se.stake_mark("Ethereum", "beacon_chain_eth_primary"), "2026-12-02") in daily.marks


def test_inside_the_declared_cooldown_beaconchain_is_not_called(monkeypatch):
    global now_
    api, node = _Api(today="2026-11-24"), _NodeStub(eth=44_700_000.0)
    assert config.HOST_COOLDOWN["beaconcha.in"]["until"] == "2026-12-01T15:02:02Z"       # the declared wait stands
    now_ = "2026-11-24 07:00"
    try:
        a, out, _ = _run(monkeypatch, api, node, now="2026-11-24 07:00")
    finally:
        now_ = "2026-12-02 07:00"
    assert api.calls == []
    assert any(e.status == "skipped" and "cooldown" in str(e.message) for e in out.log)
    assert len(node.stored) == 1                                                         # the node's row, meanwhile


def test_the_node_is_never_written_after_a_beaconchain_row(monkeypatch):
    """Once the series is on beaconcha.in's rows, a day beaconcha.in does not answer stores NOTHING (said) — the node's
    reading is the cross-check, never a fallback written into the current leg."""
    held = _stored([("2026-12-01", "beacon_chain_eth_primary", 44e6, config.BEACONCHAIN_POINT)])
    for api, key in ((_Api(), ""), (_Api(status=403), KEY)):
        node = _NodeStub(eth=44_700_000.0)
        a, out, daily = _run(monkeypatch, api, node, key=key, stored=held)
        assert node.stored == [] and "beacon_chain_eth_primary" not in _frames(out)
        assert any(e.status == "failed" and "NOTHING STORED" in str(e.message) for e in out.log)
        assert (se.stake_mark("Ethereum", "beacon_chain_eth_primary"), "2026-12-02") not in daily.marks
        assert len(api.calls) <= 2                       # a refusal stops the read: one epoch call, one ETH.STORE call


def test_a_refused_key_before_any_row_falls_to_the_node_for_the_day(monkeypatch):
    api, node = _Api(status=401), _NodeStub(eth=44_700_000.0)
    a, out, _ = _run(monkeypatch, api, node)
    assert len([c for c in api.calls if c[0].endswith("/epoch")]) == 1 and len(node.stored) == 1
    assert KEY not in " ".join(str(e.message) for e in out.log)


# ---------------------------------------------------------------------------------------- ETH.STORE's answer
def test_an_ethstore_datapoint_is_refused_when_its_day_or_its_units_are_wrong():
    n = _day_index("2026-11-30")
    good, _bad, _cur = se.parse_ethstore({"data": [_store_day(n, 0.0342, 0.0271)], "paging": {"next_cursor": "abc"}})
    assert good[0][0] == pd.Timestamp("2026-11-30") and good[0][1] == 0.0342 and _cur == "abc"
    pct = _store_day(n, 3.42, 2.71)                                    # per cent, not a fraction
    off = dict(_store_day(n), day_start=se.GENESIS_UNIX + n * 86400 + 1)             # not a whole day from genesis
    off["day_end"] = off["day_start"] + 86400
    parts = _store_day(n)
    parts["apr"] = {"combined": 0.0342, "consensus_layer": 0.0271, "execution_layer": 0.0171}      # not the two layers
    for bad in (pct, off, parts, dict(_store_day(n), apr=None), dict(_store_day(n), day="7")):
        rows, refused, _ = se.parse_ethstore({"data": [bad]})
        assert rows == [] and len(refused) == 1
    assert isinstance(se.parse_ethstore({"error": "Unauthorized"}), str)
    assert isinstance(se.parse_ethstore([]), str)


def test_the_newest_ethstore_day_follows_the_noon_boundary(monkeypatch):
    """A beaconchain-day runs 12:00:23 UTC to 12:00:23 UTC: before ~12:30 UTC the newest complete day is the one
    dated two days back, after it yesterday's — a run asks only for a day that can exist."""
    held = [(d, "staking_yield_pct", 0.03, se.ETHSTORE_POINT + "[x]") for d in pd.date_range("2026-10-19", "2026-11-30")]
    held += [(d, m, 1.0, pt) for d in pd.date_range("2026-10-19", "2026-12-02")
             for m, pt in (("beacon_chain_eth_primary", config.BEACONCHAIN_POINT),
                           ("beacon_validators_count", se.COUNT_POINT))]
    api = _Api()
    _run(monkeypatch, api, _NodeStub(eth=44e6), stored=_stored(held), now="2026-12-02 07:00")
    assert [c for c in api.calls if c[0].endswith("/eth-store")] == []           # 11-30 is held, 12-01 not complete
    api = _Api()
    _run(monkeypatch, api, _NodeStub(eth=44e6), stored=_stored(held), now="2026-12-02 13:00")
    assert len([c for c in api.calls if c[0].endswith("/eth-store")]) == 1       # 12-01 ended at noon: asked


# ---------------------------------------------------------------------------------------- the tier, the marks
def test_validatorqueue_is_retired_and_the_tier_is_beaconchain():
    names = [t[0] for t in fetch.TIER_ORDER]
    assert "validatorqueue" not in names and names.count("beacon_api") == 1
    tier = next(t for t in fetch.TIER_ORDER if t[0] == "beacon_api")
    a = tier[2]({"stored_long": None})
    assert isinstance(a, se.BeaconchainEth) and isinstance(a.node, se.BeaconNodeStakedEth)
    assert ETH["staked_eth"]["retired"] and ETH["staked_eth"]["metric"] == "beacon_chain_eth"
    # its stored rows stand: the handover still follows validatorqueue's point
    assert config.HANDOVER_CONTINUATION[("Ethereum", "beacon_chain_eth")]["after_point"] == "validatorqueue:staked_amount"


def test_the_tier_has_an_http_client_to_report(monkeypatch, tmp_path):
    """Jake's run: "tier beacon_api done in 0s — no Http client". The node read now owns an Http client and counts
    its calls in it, so the tier's timing line says what was asked."""
    node = r17._Node(r17.SLOT, second={"beaconBalancesSum": str(r17.NODE_GWEI), "slot": r17.SLOT + 80})
    monkeypatch.setattr(scrape, "robots_verdict", lambda url: (True, "test"))
    monkeypatch.setattr(se, "_utcnow", lambda: pd.Timestamp("2026-11-23 09:55"))
    a = se.BeaconNodeStakedEth(session=node, daily=r17._Daily())
    out = FetchOutput()
    a.run([ETH], 1, out)
    assert len(out.frames) == 1 and a.http.stats["calls"] == len(node.calls) == 4
    assert fetch._http_summary({"adapter": a}) != "no Http client"
    assert fetch._http_summary({"adapter": se.BeaconchainEth(http=_Api(), key="", node=_NodeStub())}) != "no Http client" \
        or hasattr(_Api(), "summary") is False


def test_a_days_mark_is_kept_only_when_its_series_reached_the_store():
    import token_metrics
    key = se.stake_mark("Ethereum", "beacon_chain_eth_primary")
    assert key == "beacon_api:Ethereum:beacon_chain_eth_primary"
    empty = pd.DataFrame(columns=["project", "metric"])
    stored = pd.DataFrame([{"project": "Ethereum", "metric": "beacon_chain_eth_primary"}])
    assert token_metrics._mark_stored(stored)(key) is True
    assert token_metrics._mark_stored(empty)(key) is False                 # the row was refused downstream: read again
    assert token_metrics._mark_stored(empty)("staked_eth:beacon_node") is True          # the old key was kept blind


def test_the_key_is_registered_and_never_in_the_repo():
    row = next(r for r in config.ENV_KEYS if r[0] == "BEACONCHAIN_API_KEY")
    assert "beacon_api" in row[2] and BC["key_env"] == "BEACONCHAIN_API_KEY"
    assert BC["history_from"] == "2026-10-19" and BC["max_requests_per_run"] <= 1000
    assert config.SOURCE_REGISTER["beaconcha.in"]["paths"] == ["/api/v2/ethereum/epoch", "/api/v2/ethereum/eth-store"]
    assert "staking_yield_pct" in config.metrics_for_project(ETH)
    assert config.VALIDATOR_YIELD["Ethereum"]["stored_first"]["metric"] == "staking_yield_pct"
    assert config.VALIDATOR_YIELD["Ethereum"]["method"] == "consensus_plus_execution"   # our own figure, the fallback


def test_the_headline_yield_reads_ethstore_where_stored_and_our_own_figure_where_not(monkeypatch):
    R = bw.Refs.__new__(bw.Refs)
    R.D = lambda r, metric, col="now": f"D_{metric}_{col}"
    monkeypatch.setattr(bw, "_eth_yield_parts", lambda R_, r, p, s: ("CONS", "EXE", [("consensus", "x", "q0")]))
    monkeypatch.setattr(bw, "_coverage_guard", lambda R_, r, legs, expr, **kw: f'=IF(SHORT,"BLOCKED",{expr})')
    cell = bw._a1_headline(R)[0][1](7, ETH)
    assert cell == '=IF(ISNUMBER(D_staking_yield_pct_now),D_staking_yield_pct_now,IF(SHORT,"BLOCKED",CONS+EXE))'
    # (built on the 2026-10-13 dump: 2.557% from our own figure with no ETH.STORE row, the stored day's value with one)
    # a project without the declaration is untouched (the declaration is Ethereum's alone)
    monkeypatch.setitem(config.VALIDATOR_YIELD, "Ethereum",
                        {k: v for k, v in config.VALIDATOR_YIELD["Ethereum"].items() if k != "stored_first"})
    assert bw._a1_headline(R)[0][1](7, ETH) == '=IF(SHORT,"BLOCKED",CONS+EXE)'


# ---------------------------------------------------------------------------------------- staked_eth_rebuild.py
def test_the_rebuild_refuses_to_bill_unless_forced(tmp_path, capsys, monkeypatch):
    called = []
    monkeypatch.setattr(staked_eth_rebuild, "anchor", lambda reader=None: called.append("anchor"))
    monkeypatch.setattr(staked_eth_rebuild, "bigquery_rows", lambda anc: called.append("bigquery"))
    assert staked_eth_rebuild.main(["--db", str(tmp_path / "none.db"), "--apply"]) == 2
    text = capsys.readouterr().out
    assert text.startswith("REFUSED") and "1,170 GB" in text and called == []      # nothing read, nothing billed
    assert staked_eth_rebuild.refusal(False) and staked_eth_rebuild.refusal(True) is None
    assert staked_eth_rebuild.refusal(False, injected=True) is None                 # the tests' stub rows bill nothing
    # forced, it goes on to its own checks (here: no store)
    assert staked_eth_rebuild.main(["--db", str(tmp_path / "none.db"), "--force-bigquery"]) == 2
    assert "no store" in capsys.readouterr().out and called == []


def test_nothing_the_daily_run_calls_names_the_rebuild():
    for f in ("token_metrics.py", "windows/daily_run.ps1", "fetch/__init__.py"):
        src = (ROOT / f).read_text(encoding="utf-8")
        assert "staked_eth_rebuild.main" not in src and "import staked_eth_rebuild" not in src, f
    assert "staked_eth_rebuild" not in (ROOT / "windows" / "daily_run.ps1").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------- the handover, the seam
def _g(metric, rows, source="x"):
    return pd.DataFrame([{"date": pd.Timestamp(x[0]), "project": "Ethereum", "metric": metric, "value": float(x[1]),
                          "source": x[2] if len(x) > 2 else source, "tier": 1} for x in rows], columns=COLS)


VQ = [(d, 44_000_000.0, "validatorqueue:staked_amount") for d in pd.date_range("2025-07-28", "2026-10-25")]
HKEY = ("Ethereum", "beacon_chain_eth")


def test_the_step_is_measured_on_the_days_both_legs_hold(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", [ETH])
    bcn = [(d, 44_000_000.0 * 1.0080, config.BEACONCHAIN_POINT) for d in pd.date_range("2026-10-19", "2026-10-25")] + \
          [(d, 44_400_000.0, config.BEACONCHAIN_POINT) for d in pd.date_range("2026-10-26", "2026-12-02")]
    groups = {HKEY: _g("beacon_chain_eth", VQ), ("Ethereum", "beacon_chain_eth_primary"): _g("beacon_chain_eth_primary", bcn)}
    bw._handover_continuation_views(groups)
    s = bw._daily(groups[HKEY])
    assert HKEY in bw.HANDOVER_CONTINUED and HKEY not in bw.HANDOVER_HELD
    assert s.index.max() == pd.Timestamp("2026-12-02") and len(s) == 455 + 38
    assert s[pd.Timestamp("2026-10-25")] == 44_000_000.0 and s[pd.Timestamp("2026-10-26")] == 44_400_000.0   # never scaled
    said = bw.HANDOVER_CONTINUED[HKEY]
    assert "+0.80%" in said and "7 day(s) both legs hold" in said and "no day between the two legs is missing" in said
    decl = ETH["series_handover"]["beacon_chain_eth"]
    assert "CHANGE OF QUANTITY" in decl["composition_change"] and "+0.80%" in decl["composition_change"]
    # the overlap test itself is P5-1.12's, untouched — and it has not passed
    ov = decl["overlap"]
    assert (ov["tolerance_pct"], ov["min_days"], ov["b_point"]) == (1.0, 455, config.STAKED_REBUILD_POINT)
    assert bw.handover_overlap(groups, "Ethereum", ov)["passed"] is False


def test_the_days_no_source_serves_are_a_named_hole(monkeypatch):
    """Before the key works only the node's rows exist: the days between validatorqueue's last row and the first node
    row hold nothing, stay unformed, and the cell names them."""
    monkeypatch.setattr(bw, "_SCOPE", [ETH])
    node = [(d, 44_352_000.0, config.BEACON_NODE_POINT + "[finalized slot 1]")
            for d in pd.date_range("2026-11-24", "2026-11-26")]
    groups = {HKEY: _g("beacon_chain_eth", VQ), ("Ethereum", "beacon_chain_eth_primary"): _g("beacon_chain_eth_primary", node)}
    bw._handover_continuation_views(groups)
    s = bw._daily(groups[HKEY])
    assert len(s) == 455 + 3 and pd.Timestamp("2026-11-01") not in s.index
    said = bw.HANDOVER_CONTINUED[HKEY]
    assert "HOLE: 29 day(s)" in said and "2026-10-26..2026-11-23" in said
    assert "+0.80%" in said and "30 day(s) apart" in said            # 44,352,000 / 44,000,000 − 1, said to span the gap


def test_one_quantity_three_legs_and_the_node_is_cut_from_beaconchains_first_day(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", [ETH])
    decl = ETH["series_handover"]["beacon_chain_eth_primary"]
    assert decl["ordered_points"] == (config.STAKED_REBUILD_POINT, config.BEACON_NODE_POINT, config.BEACONCHAIN_POINT)
    assert decl["composition_change"].startswith("none")
    rows = [(d, 44_700_000.0, config.BEACON_NODE_POINT + "[finalized slot 1]")
            for d in pd.date_range("2026-11-24", "2026-12-01")]
    rows += [(d, 44_400_000.0, config.BEACONCHAIN_POINT) for d in pd.date_range("2026-11-28", "2026-12-02")]
    groups = {("Ethereum", "beacon_chain_eth_primary"): _g("beacon_chain_eth_primary", rows)}
    bw._handover_cut_views(groups)
    g = groups[("Ethereum", "beacon_chain_eth_primary")]
    by = g.assign(pt=g["source"].map(base._measuring_point)).groupby("pt")["date"].agg(["min", "max", "count"])
    assert by.loc[config.BEACON_NODE_POINT, "max"] == pd.Timestamp("2026-11-27")         # 11-28..12-01 dropped
    assert by.loc[config.BEACONCHAIN_POINT, "count"] == 5 and len(g) == 4 + 5


# ================================================================================== 2. the rule-3 lines
def test_issuance_is_the_supply_side_incentive_and_is_counted_once(monkeypatch):
    assert config.outflow_metrics("Ethereum", "b") == ("gross_issuance_tokens",)
    assert config.outflow_metrics("Ethereum", "a") == ()                 # was ("gross_issuance_tokens",): it MOVED
    cats = [c for c in config.OUTFLOW_CATEGORY_NAMES if config.outflow_metrics("Ethereum", c)]
    assert cats == ["b"] and bw._cp_cats(ETH) == ["b"]                   # total dilution sums one category
    assert "Ethereum" in config.METRICS["outflow_b_usd"]["only_projects"]
    assert "Ethereum" not in config.METRICS["outflow_a_usd"]["only_projects"]
    monkeypatch.setattr(bw, "_SCOPE", [ETH])
    days = pd.date_range("2026-09-30", "2026-10-13")
    groups = {("Ethereum", "gross_issuance_tokens"): _g("gross_issuance_tokens", [(d, 2_600.0) for d in days]),
              ("Ethereum", "price_usd"): _g("price_usd", [(d, 4_000.0) for d in days])}
    bw._outflow_views(groups)
    b = bw._daily(groups[("Ethereum", "outflow_b_tokens")])
    assert len(b) == 14 and b.sum() == pytest.approx(14 * 2_600.0)
    assert bw._daily(groups[("Ethereum", "outflow_b_usd")]).sum() == pytest.approx(14 * 2_600.0 * 4_000.0)
    assert not [k for k in groups if k[1].startswith("outflow_") and k[1][8] != "b"]     # no (a), no other category
    # the Flows statement's (a) line says where the tokens are — never a second count, never a bare blank
    ev = config.flow_line_evidence("Ethereum", config.FLOW_LEG_LINES["a"])
    assert ev["verdict"] == "included_in" and ev["line"] == config.FLOW_LEG_LINES["b"]
    assert ("Ethereum", "b") not in config.NO_SUCH_FLOW


@pytest.mark.parametrize("reason", [
    "n/a — no non-circulating wallet set on this project (config.unlock_stock_metrics)",
    "n/a — no buyback programme declared (config fee_split)",
    "n/a — no market buyback is measured for this project: NOTHING IS BOUGHT. EIP-1559 destroys the base fee users pay",
])
def test_the_declared_na_carry_a_source_and_a_read_date(reason):
    e = config.declared_na_reason("Ethereum", reason)
    assert e and e["source"].startswith("https://") and e["read_on"] == "2026-11-23" and "R20" in e["decided"]
    assert config.declared_na_reason("Ethereum", "n/a — no supply-side incentive (b) series on file") is None


def test_the_buyback_reason_the_cell_writes_is_the_one_declared():
    route = config.buyback_route("Ethereum")
    assert route["route"] == "none"
    text = ("n/a — no market buyback is measured for this project" + f": {route.get('reason')}")[:240]
    assert config.declared_na_reason("Ethereum", text)
    assert config.declared_na_text("Ethereum", "Unlocks Q0 (tokens) — releases").startswith("n/a — no unlock")


# ================================================================================== 3. settlement, the budget
def test_the_seam_is_labelled_with_jakes_overlap_figure():
    ho = config.SETTLEMENT_REBUILD["ethereum_rebuild"]["handover"]
    assert ho["from"] == "2026-08-26" and ho["scaled"] is False
    assert ho["overlap_gap_pct"] == 6.77 and ho["overlap"] == ("2026-05-28", "2026-08-25")      # was "+6.8%" (G10-4)
    assert (pd.Timestamp(ho["overlap"][1]) - pd.Timestamp(ho["overlap"][0])).days + 1 == 90
    decl = ETH["series_handover"]["settlement_volume_usd"]
    assert "+6.77%" in decl["composition_change"] and "+6.77%" in ho["validation_note"]
    assert decl["ordered_points"][-1] == "derived:settlement_rebuild"


def test_the_budget_is_1000_gb_on_the_one_ledger():
    near = config.PROJECT_BY_NAME["Near"]["near_bigquery"]
    assert near["monthly_budget_bytes"] == 1000 * 10 ** 9                       # was 900 * 10 ** 9
    from fetch.eth_settlement import EthP2PBigQuery
    assert EthP2PBigQuery()._bq_spec()["monthly_budget_bytes"] == 1000 * 10 ** 9      # Ethereum's reader, NEAR's budget
    s = config.BIGQUERY_SCHEDULE
    assert s["budget_gb"] == 1000.0 < s["free_tier_gb"] == 1099.5


def test_the_89_days_fill_by_the_ledgers_own_rule():
    o = config.BIGQUERY_SCHEDULE["eth_owed"]
    first, last = dt.date.fromisoformat(o["first"]), dt.date.fromisoformat(o["last"])
    assert (last - first).days + 1 == 89 == o["days"]
    weeks = {(first + dt.timedelta(days=i)).isocalendar()[:2] for i in range(89)}
    assert len(weeks) == 13 == o["pieces"] and o["pieces"] * o["gb_per_piece"] == pytest.approx(123.24)
    # 24 Nov, ~74 GB left: NEAR's reserve 6 days x 2.84 x 1.2 = 20.4 GB -> 53.6 GB of room -> 5 pieces (47.4 GB)
    p = config.eth_owed_fill_plan("2026-11-24")
    assert p["reserve_gb"] == pytest.approx(20.448) and p["this_month_pieces"] == 5
    assert p["this_month_through"] == "2026-09-27" and p["this_month_days"] == 33
    assert p["rest_pieces"] == 8 and p["rest_gb"] == pytest.approx(75.84) and p["all_filled_on"] == "2026-12-01"
    # if the ~74 GB was counted against the old 900 (174 GB left of 1,000): all 13 in November
    assert config.eth_owed_fill_plan("2026-11-24", 174.0)["all_filled_on"] == "2026-11-24"
    # December's first run has the room for all of them: 1,000 − 30 x 2.84 x 1.2 = 897.8 GB
    dec = config.eth_owed_fill_plan("2026-12-01", 1000.0)
    assert dec["this_month_pieces"] == 13 and dec["reserve_gb"] == pytest.approx(102.24)


def test_the_readers_own_pieces_are_those_thirteen_weeks():
    from fetch.eth_settlement import EthP2PBigQuery
    p2p = config.SETTLEMENT_REBUILD["ethereum_rebuild"]["p2p"]
    held = {str(d.date()) for d in pd.date_range(p2p["validation_extend_from"], "2026-08-25")}
    pieces = EthP2PBigQuery.pieces(held, p2p, pd.Timestamp("2026-11-24"))
    assert len(pieces) == 13
    assert (pieces[0][0], pieces[0][1]) == (pd.Timestamp("2026-08-24"), pd.Timestamp("2026-08-30"))   # oldest first
    assert pieces[-1][1] == pd.Timestamp("2026-11-22")


def test_the_combined_schedule_is_the_plans():
    m = config.BIGQUERY_SCHEDULE["months"]
    assert [m[k]["near_chunks"] for k in sorted(m)] == [1, 2, 2, 3, 3, 1]
    assert [(m[k]["eth_weeks"], m[k]["eth_back_to"]) for k in ("2026-12", "2027-01", "2027-02")] == [
        (31, "2025-10-27"), (29, "2025-04-07"), (22, "2024-11-04")]
    assert sum(m[k]["eth_weeks"] for k in m) == 82                              # the 82 pieces, 777 GB
    assert sum(m[k]["eth_gb"] for k in m) == pytest.approx(777.0)
    p = nh.combined_plan("2025-12-01", config.NEAR_P2P_HISTORY)
    assert [len(r["chunks"]) for r in p["rows"]] == [1, 2, 2, 3]
    # days x 7.54 GB: the plan's 226.2 / 459.9 / 467.4 / 686.1 (it rounds each chunk before adding)
    assert [r["gb"] for r in p["rows"]] == pytest.approx([30 * 7.54, 61 * 7.54, 62 * 7.54, 91 * 7.54])
    assert [m[r["run_month"]]["near_gb"] for r in p["rows"]] == pytest.approx([r["gb"] for r in p["rows"]], abs=0.1)
    assert p["formed_on"] == dt.date(2027, 3, 31)                               # NEAR's key, as the plan says
    for r in p["rows"]:                                                         # every month inside 1,000 GB
        other = 56.0 + (211.1 if r["run_month"] == "2026-12" else 0.0) + m[r["run_month"]]["eth_gb"]
        assert r["gb"] + nh.reserve_gb(r["run_on"], 2.84, 1.2) + other <= 1000.0
    dec = p["rows"][0]
    assert dec["room_gb"] == pytest.approx(1000 - 30 * 2.84 * 1.2 - 56.0 - 211.1 - 293.7)
    assert dec["left_gb"] == pytest.approx(110.76, abs=0.01)                    # was 10.8 GB under 900


def test_a_months_approval_covers_the_schedules_chunks():
    cfg = config.NEAR_P2P_HISTORY
    # R21b (Jake 2026-11-25): given, for 2026-12 only, the schedule's count (was: not yet given)
    assert config.NEAR_P2P_HISTORY_APPROVAL == {"approved": True, "approved_by": "Jake (Round 21b, item 4)",
                                               "approved_on": "2026-11-25", "for_month": "2026-12", "max_gb": 337.0,
                                               "chunks": None}
    ok = {"approved": True, "approved_by": "Jake", "approved_on": "2027-01-02", "max_gb": 240, "chunks": None}
    chunk = (dt.date(2025, 10, 1), dt.date(2025, 10, 31))
    assert nh.scheduled_chunks("2027-01", cfg) == 2 and nh.scheduled_chunks("2027-03", cfg) == 3
    assert nh.scheduled_chunks("2028-01", cfg) == 1
    assert nh.refusal(True, "2027-01-02", ok, chunk, 233.7, 1, cfg) is None          # the month's second chunk
    assert nh.refusal(True, "2027-01-02", ok, chunk, 233.7, 2, cfg) is not None      # not a third
    assert nh.refusal(True, "2027-01-02", dict(ok, chunks=1), chunk, 233.7, 1, cfg) is not None   # Jake's own count wins
    assert nh.refusal(True, "2027-01-02", dict(ok, approved=False), chunk, 233.7, 0, cfg) is not None


def test_ethereums_history_reaches_only_as_far_as_its_month():
    assert config.SETTLEMENT_REBUILD["ethereum_rebuild"]["p2p"]["extend_approved"]["by"].startswith("Jake")
    assert settlement_extend.scheduled_from("2024-11-04", "2026-11-24")[0] == "2026-05-28"     # nothing before December
    assert settlement_extend.scheduled_from("2024-11-04", "2026-12-03")[0] == "2025-10-27"
    assert settlement_extend.scheduled_from("2024-11-04", "2027-01-05")[0] == "2025-04-07"
    assert settlement_extend.scheduled_from("2024-11-04", "2027-02-01")[0] == "2024-11-04"
    assert settlement_extend.scheduled_from("2024-11-04", "2027-09-01")[0] == "2024-11-04"
    assert settlement_extend.scheduled_from("2026-03-02", "2026-12-03")[0] == "2026-03-02"     # a later day asked stands


# ================================================================================== 4. fees, the Foundation's wallets
def test_blockscout_is_the_fee_figure_on_every_cell_and_defillama_the_cross_check():
    assert config.metric_alias("Ethereum", "fees_usd") == "fees_usd_blockscout"
    assert config.MASTER_FEES_OVERRIDE["Ethereum"][0] == "fees_usd_blockscout"
    assert config.revenue_split("Ethereum")["fees"]["metric"] == "fees_usd_blockscout"
    assert config.metric_alias("Near", "fees_usd") == "fees_usd"                         # Ethereum's alone
    assert "in_fees_blockscout" not in config.CREDIBILITY_WIRING["Ethereum"]
    spec = config.CREDIBILITY["Ethereum"]["in_fees"]
    assert spec["ref"]["tol"] == 10.0                                                    # B11's tolerance, untouched
    # the judged cross-check on the days both hold: 30 days of DefiLlama $1,000 a day, Blockscout 0.26 ETH x $4,000
    from credibility import _common_days_sum
    days = pd.date_range("2026-10-01", "2026-10-30")
    long = pd.concat([_g("fees_usd", [(d, 1_000.0) for d in days]), _g("fees_native_tokens", [(d, 0.26) for d in days]),
                      _g("price_usd", [(d, 4_000.0) for d in days])], ignore_index=True)
    asof = pd.Timestamp("2026-10-31")
    ours, _d, how = _common_days_sum("Ethereum", {}, long, asof, **spec["ours"]["args"])
    ref, _d2, how2 = _common_days_sum("Ethereum", {}, long, asof, **spec["ref"]["args"])
    assert ours == pytest.approx(30 * 0.26 * 4_000.0) and "fees_native_tokens" in how      # ours: Blockscout x price
    assert ref == pytest.approx(30 * 1_000.0) and "fees_usd" in how2                       # the reference: DefiLlama


def test_the_fee_cell_reads_blockscouts_series():
    R = bw.Refs.__new__(bw.Refs)
    seen = []
    R.D = lambda r, metric, col="now": seen.append(metric) or f"D[{metric}|{col}]"
    name, spec = bw._fees_q0_col(R)[0], bw._fees_q0_col(R)
    assert name == "Fees Q0 ($)" and spec[1](5, ETH) == "=D[fees_usd_blockscout|q0]"
    assert spec[1](5, config.PROJECT_BY_NAME["Near"]) == "=D[fees_usd|q0]"


def test_the_two_inferred_foundation_wallets_stay_off_the_float():
    rs = config.RULE_IN["Ethereum"]
    assert rs["legs"] == () and "ef_inferred_held_tokens" in rs["off_float"] and "ef_held_tokens" in rs["off_float"]
    inferred = [w for w in ETH["ef_wallets"]["wallets"] if w["tag"] == "INFERRED"]
    assert len(inferred) == 2 and sum(sum(w["read"]) for w in inferred) == pytest.approx(2_473.8858)     # ~2,474 ETH


# ================================================================================== 5. NearBlocks
def test_the_budget_covers_the_pacing_of_the_dearest_day():
    credits = nb.worst_case_credits(r17.NEAR["nearblocks"])
    assert credits == [4, 4, 4, 10, 10, 10, 10, 10, 10, 1, 1, 4, 4] and sum(credits) == 82 <= nb.CREDITS_PER_DAY
    assert nb.pacing_seconds(credits) == pytest.approx(605.0)            # ten waits of 60.5 s
    assert nb.pacing_seconds([1] * 12) == pytest.approx(60.5)            # a normal day: two minute windows
    assert nb.pacing_seconds([1] * 6) == 0.0
    budget = fetch.TIER_BUDGET_S["nearblocks"]
    assert budget == 750.0 == nb.budget_seconds(config.PROJECTS)         # was 180
    assert budget - nb.STOP_BEFORE_S >= nb.pacing_seconds(credits) + nb.CALL_SECONDS * len(credits)
    # the fetch stage's longest wait is unchanged: the tier stays under the longest budget already there
    assert budget <= max(v for k, v in fetch.TIER_BUDGET_S.items() if k != "nearblocks") == 900
    tier = next(t for t in fetch.TIER_ORDER if t[0] == "nearblocks")
    assert tier[2]({"last_dates": {}, "stored_long": None}).deadline_s == budget - 10.0


def test_every_owed_read_is_called_on_the_dearest_day(monkeypatch, _nearblocks_fake_clock):
    """Nothing stored (every history at its 365 rows, every stat at its 100): under the tier's budget every read is
    made — none says NOT CALLED — where the 180 s it had left the reads after the third window uncalled."""
    http = r17._NB(r17.TODAY)
    a, out = r17._near_run(monkeypatch, http, {}, stored=None, deadline_s=fetch.TIER_BUDGET_S["nearblocks"] - 10.0)
    said = " | ".join(str(e.message) for e in out.log)
    assert "NOT CALLED" not in said and "not called" not in said
    paths = [p for p, _ in http.calls]
    assert len([p for p in paths if p.endswith("/stats/balance")]) == 6           # the six balance histories
    assert {"/v3/txn-stats", "/v3/address-stats", "/v3/intents/volume-stats", "/v3/validators/info",
            "/v3/stats"} <= set(paths) and any(p.endswith("/receipts") for p in paths)
    assert a.pacer.total == 78 and sum(_nearblocks_fake_clock) <= fetch.TIER_BUDGET_S["nearblocks"] - 10.0
    assert len(_nearblocks_fake_clock) >= 9                                       # it waited window after window
    # the same day under the old budget: reads left uncalled (the run of 2026-11-23)
    _nearblocks_fake_clock.clear()
    http2 = r17._NB(r17.TODAY)
    _a2, out2 = r17._near_run(monkeypatch, http2, {}, stored=None, deadline_s=170.0)
    lost = [e for e in out2.log if "NOT CALLED" in str(e.message)]
    assert len(lost) == 4 and len(http2.calls) < len(http.calls)      # four series stored nothing: three histories, a scan


def test_a_normal_day_still_ends_in_about_a_minute(monkeypatch, _nearblocks_fake_clock):
    http = r17._NB(r17.TODAY)
    last = r17._all_last(pd.Timestamp(r17.TODAY) - pd.Timedelta(days=2))
    a, out = r17._near_run(monkeypatch, http, last, stored=r17._held_history(),
                           deadline_s=fetch.TIER_BUDGET_S["nearblocks"] - 10.0)
    assert a.pacer.total <= 12 and sum(_nearblocks_fake_clock) <= 61.0
    assert "NOT CALLED" not in " | ".join(str(e.message) for e in out.log)


def test_the_daily_runs_step_limits_hold_the_tier():
    """windows/daily_run.ps1 is unchanged and ASCII: token_metrics has 240 minutes, the fetch stage's longest tier is
    15 (business) — the NearBlocks tier at 12.5 minutes changes neither."""
    raw = (ROOT / "windows" / "daily_run.ps1").read_bytes()
    assert all(b < 128 for b in raw)
    assert b'"token_metrics" = 240' in raw
    assert fetch.TIER_BUDGET_S["nearblocks"] / 60.0 <= 15.0 < 240
