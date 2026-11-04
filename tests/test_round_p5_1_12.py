"""P5-1.12 (Jake 2026-11-04): Ethereum's staked ETH off beaconcha.in.

beaconcha.in is in a declared cooldown (never called before its 429's wait has passed); staked ETH is read once a day
from a free public beacon node (Σ actual balances at the finalized state, summed while streaming); the days the node
cannot read back are rebuilt from BigQuery (deposits − withdrawals + consensus rewards, rolled back from a live node
read) and adopted only when every one of the 455 validatorqueue days agrees within ±1%. No network: every read is a
stub (conftest blocks the rest).
"""
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from fetch import base  # noqa: E402
from fetch import staked_eth as se  # noqa: E402


# ---------- the cooldown ----------

def test_beaconchain_is_in_a_declared_cooldown_until_the_wait_has_passed(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path))
    c = config.HOST_COOLDOWN["beaconcha.in"]
    end = pd.Timestamp(c["until"])
    # never early: 2,300,523 s counted from the END of 2026-11-04 UTC
    assert end == pd.Timestamp("2026-11-04T23:59:59Z") + pd.Timedelta(seconds=2_300_523)
    assert c["retry_after_s"] == 2_300_523
    on = datetime(2026, 11, 20, tzinfo=timezone.utc)
    assert "NOT CALLED" in base.host_cooldown("beaconcha.in", now=on)
    assert base.host_cooldown("beaconcha.in", now=datetime(2026, 12, 1, 15, 2, 3, tzinfo=timezone.utc)) is None
    assert base.host_cooldown("api.llama.fi", now=on) is None, "only declared hosts"


def test_a_later_429_is_recorded_and_the_longer_wait_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path))
    t0 = datetime(2026, 12, 2, tzinfo=timezone.utc)
    assert base.host_cooldown("beaconcha.in", now=t0) is None
    base.record_cooldown("beaconcha.in", 86_400, now=t0)
    assert "recorded" in base.host_cooldown("beaconcha.in", now=datetime(2026, 12, 2, 12, tzinfo=timezone.utc))
    base.record_cooldown("beaconcha.in", 60, now=t0)                  # a shorter wait never shortens it
    rec = json.loads((tmp_path / base.COOLDOWN_FILE).read_text())
    assert rec["beaconcha.in"]["until"] == "2026-12-03T00:00:00Z"
    base.record_cooldown("api.llama.fi", 86_400, now=t0)              # a host not declared is not recorded
    assert "api.llama.fi" not in json.loads((tmp_path / base.COOLDOWN_FILE).read_text())


def test_no_request_is_sent_to_a_host_in_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path))
    h = base.Http(retries=0)
    sent = []
    monkeypatch.setattr(h.s, "post", lambda *a, **k: sent.append(a) or (_ for _ in ()).throw(AssertionError("sent")))
    with pytest.raises(base.RateLimited, match="NOT CALLED"):
        h.post("https://beaconcha.in/api/v2/ethereum/epoch", json_body={})
    assert sent == []


def test_a_long_429_from_a_declared_host_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path))
    monkeypatch.setitem(config.HOST_COOLDOWN, "example.org", {"until": "2000-01-01T00:00:00Z"})

    class R:
        status_code, headers, text = 429, {"Retry-After": "2300523"}, ""
    h = base.Http(retries=0)
    monkeypatch.setattr(h.s, "get", lambda *a, **k: R())
    with pytest.raises(base.RateLimited):
        h.get("https://example.org/x")
    rec = json.loads((tmp_path / base.COOLDOWN_FILE).read_text())
    assert rec["example.org"]["retry_after_s"] == 2300523
    with pytest.raises(base.RateLimited, match="NOT CALLED"):
        h.get("https://example.org/x")


def test_the_beacon_api_tier_no_longer_runs_the_beaconchain_adapter():
    import fetch
    tier = next(t for t in fetch.ADAPTERS if t[0] == "beacon_api") if hasattr(fetch, "ADAPTERS") else None
    if tier is None:
        import inspect
        src = inspect.getsource(fetch)
        assert '("beacon_api", 1, lambda ctx: BeaconNodeStakedEth())' in src
    else:
        assert isinstance(tier[2]({}), se.BeaconNodeStakedEth)
    assert "retired" in config.PROJECT_BY_NAME["Ethereum"]["staked_eth_primary"]


# ---------- the daily read ----------

def test_the_stream_sum_counts_a_value_split_across_chunks_once():
    body = (b'{"execution_optimistic":false,"finalized":true,"data":[{"index":"0","balance":"32000000000"},'
            b'{"index":"1","balance":"0"},{"index":"2","balance":"40500000001"}]}')
    want = (72_500_000_001, 3, 2)
    for size in (1, 3, 7, 16, 64, len(body)):
        chunks = [body[i:i + size] for i in range(0, len(body), size)]
        assert se.sum_field_stream(chunks) == want, size
    pend = b'{"data":[{"pubkey":"0xa","amount":"178500000000","slot":"1"},{"amount":"580000000000"}]}'
    assert se.sum_field_stream([pend[:30], pend[30:]], b"amount") == (758_500_000_000, 2, 2)


class _Resp:
    def __init__(self, status=200, body=None, raw=b""):
        self.status_code, self._body, self._raw = status, body, raw

    def json(self):
        return self._body

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._raw), 5):
            yield self._raw[i:i + 5]


class _Node:
    """A stub beacon node: the finalized slot answers from `slots` in turn; the balances body is `bal`."""
    def __init__(self, slots, n=200_000, each=220_000_000_000):
        self.slots, self.calls = list(slots), []
        self.bal = (b'{"finalized":true,"data":[' + b",".join(b'{"index":"%d","balance":"%d"}' % (i, each)
                                                           for i in range(n)) + b"]}")

    def get(self, url, stream=False, timeout=None):
        self.calls.append(url)
        if url.endswith("/headers/finalized"):
            s = self.slots.pop(0) if len(self.slots) > 1 else self.slots[0]
            return _Resp(body={"data": {"header": {"message": {"slot": str(s)}}}})
        return _Resp(raw=self.bal)


class _Daily:
    def __init__(self):
        self.done_keys = set()

    def due(self, k, d):
        return (k, d) not in self.done_keys

    def done(self, k, d):
        self.done_keys.add((k, d))


class _Out:
    def __init__(self):
        self.frames, self.log = [], []

    def add(self, frame, source, name, msg, tier):
        self.frames.append(frame)
        self.log.append(("ok", msg))

    def fail(self, source, name, msg, tier):
        self.log.append(("failed", msg))

    def mark_current(self, *a):
        self.log.append(("current", a[-2]))


def test_the_node_read_stores_the_finalized_sum_dated_by_its_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path))
    node = _Node([15_580_224])
    a = se.BeaconNodeStakedEth(session=node, daily=_Daily())
    out = _Out()
    a.run([config.PROJECT_BY_NAME["Ethereum"]], 400, out)
    f = out.frames[0]
    assert f["metric"].iloc[0] == "beacon_chain_eth_primary"
    assert f["value"].iloc[0] == pytest.approx(200_000 * 220.0)              # 44,000,000 ETH
    assert pd.Timestamp(f["date"].iloc[0]) == pd.Timestamp("2026-11-04")
    src = f["source"].iloc[0]
    assert src.startswith(config.BEACON_NODE_POINT + "[finalized slot 15580224")
    assert base._measuring_point(src) == config.BEACON_NODE_POINT
    out2 = _Out()
    a.run([config.PROJECT_BY_NAME["Ethereum"]], 400, out2)                  # once a day
    assert out2.frames == [] and out2.log[0][0] == "current"


def test_a_moving_slot_is_read_again_then_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path))
    a = se.BeaconNodeStakedEth(session=_Node([1, 2, 2, 2]), daily=_Daily())
    assert a.read("https://n")[0] == 2                                      # moved once: read again, same slot
    b = se.BeaconNodeStakedEth(session=_Node([1, 2, 3, 4, 5]), daily=_Daily())
    with pytest.raises(RuntimeError, match="moved"):
        b.read("https://n")


def test_a_truncated_or_out_of_band_answer_stores_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path))
    out = _Out()
    se.BeaconNodeStakedEth(session=_Node([5], n=1_000), daily=_Daily()).run([config.PROJECT_BY_NAME["Ethereum"]], 1,
                                                                            out)
    assert out.frames == [] and out.log[0][0] == "failed" and "refused" in out.log[0][1]


# ---------- the rebuild ----------

def test_consensus_reward_is_the_spec_maximum():
    b = 44_400_000.0
    per_epoch = b * 64 / math.sqrt(b * 1e9)                     # altair: increments x base_reward_per_increment
    assert se.cl_reward_eth(b) == pytest.approx(225 * per_epoch)
    assert 2_900 < se.cl_reward_eth(b) < 3_200                 # ~1.1M ETH a year at 44.4M staked
    assert se.cl_reward_eth(b, 0.5) == pytest.approx(se.cl_reward_eth(b) / 2)
    assert se.cl_reward_eth(0) == 0.0


def test_the_queue_releases_at_most_the_churn_a_day():
    deps = {pd.Timestamp("2025-05-07"): 100_000.0, pd.Timestamp("2025-05-08"): 10_000.0}
    q = se.simulate_queue(deps, "2025-05-07", "2025-05-10", 57_600.0)
    assert q[pd.Timestamp("2025-05-07")] == 0.0
    assert q[pd.Timestamp("2025-05-08")] == 42_400.0
    assert q[pd.Timestamp("2025-05-09")] == 0.0                 # 42,400 + 10,000 − 57,600 < 0
    assert q[pd.Timestamp("2025-05-10")] == 0.0


def _flows(days, dep=40_000.0, wd=30_000.0):
    return {d: (dep, wd) for d in days}


def test_roll_back_inverts_a_forward_build_exactly():
    """A lock history built by the recursion the roll back inverts (the reward read at the later day's balance), then
    rolled back from an anchor six hours into the last day: the same L on every day."""
    days = pd.date_range("2026-10-01", "2026-10-10")
    flows = _flows(days[:-1])
    lock = {days[-1]: 45_500_000.0}
    for d in reversed(days[:-1]):
        nxt = lock[d + pd.Timedelta(days=1)]
        lock[d] = nxt - flows[d][0] + flows[d][1] - se.cl_reward_eth(nxt, 1.0)
    anchor = days[-1] + pd.Timedelta(hours=6)
    partial = (5_000.0, 2_000.0)
    lock_a = lock[days[-1]] + partial[0] - partial[1]
    for _ in range(8):           # the anchor's partial-day reward is read at the anchor's own balance: solve for it
        lock_a = lock[days[-1]] + partial[0] - partial[1] + se.cl_reward_eth(lock_a, 0.25)
    got = se.roll_back(lock_a, anchor, flows, partial, days[0])
    for d in days:
        assert got[d] == pytest.approx(lock[d], rel=1e-9), d


def test_a_missing_flow_day_is_a_refusal_never_a_zero():
    days = pd.date_range("2026-10-01", "2026-10-05")
    flows = _flows([d for d in days[:-1] if d != pd.Timestamp("2026-10-02")])
    with pytest.raises(KeyError, match="2026-10-02"):
        se.roll_back(45e6, days[-1], flows, (0.0, 0.0), days[0])


def test_the_overlap_test_needs_every_day_within_one_percent():
    days = pd.date_range("2025-07-28", "2026-10-25")
    assert len(days) == 455
    vq = {d: 44e6 for d in days}
    ok = {d: 44e6 * 1.005 for d in days}
    t = se.overlap_test(ok, vq, "2025-07-28", "2026-10-25", 1.0, 455)
    assert t["passed"] and t["compared"] == 455 and t["worst_gap"] == pytest.approx(0.5)
    one = dict(ok)
    one[days[100]] = 44e6 * 1.0101                               # a single day at +1.01%
    t = se.overlap_test(one, vq, "2025-07-28", "2026-10-25", 1.0, 455)
    assert not t["passed"] and t["outside"] == [str(days[100].date())]
    short = {d: v for d, v in vq.items() if d != days[3]}        # 454 stored days: below the minimum
    assert not se.overlap_test(ok, short, "2025-07-28", "2026-10-25", 1.0, 455)["passed"]
    gap = {d: v for d, v in ok.items() if d != days[9]}         # a rebuild missing a stored day
    t = se.overlap_test(gap, vq, "2025-07-28", "2026-10-25", 1.0, 455)
    assert not t["passed"] and t["missing"] == [str(days[9].date())]


# ---------- the script, end to end on stubs ----------

def _db(tmp_path, vq_value=None, node_day=None):
    import store as sm
    db = tmp_path / "metrics.db"
    sm.Store(db).close()
    con = sqlite3.connect(db)
    days = pd.date_range("2025-07-28", "2026-10-25")
    rows = [(str(d.date()), "Ethereum", "beacon_chain_eth", vq_value(d), "validatorqueue:staked_amount", 1, "x")
            for d in days]
    if node_day:
        rows.append((node_day, "Ethereum", "beacon_chain_eth_primary", 44e6,
                     config.BEACON_NODE_POINT + "[finalized slot 1]", 1, "x"))
    con.executemany("INSERT INTO metrics(date,project,metric,value,source,tier,fetched_at) VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return db


class _Anchor:
    """reader for staked_eth_rebuild.anchor: the balances and pending deposits at one slot."""
    def __init__(self, slot, bal_eth, pend_eth):
        self.slot, self.bal, self.pend = slot, bal_eth, pend_eth

    def read(self, node, state="finalized", field="balance", path="validator_balances"):
        return (self.slot, int(self.bal * 1e9), 2_000_000, 800_000) if field == "balance" else \
            (self.slot, int(self.pend * 1e9), 10, 10)


def _scenario(anchor_time, bal_eth, churn_day=57_600.0, dep=50_000.0, wd=45_000.0):
    """Query rows from Electra to the anchor with constant daily flows, the stake each day the rebuild itself implies
    (so a validatorqueue built from it agrees exactly), and the pending queue the spec simulation gives at the anchor."""
    sp = config.PROJECT_BY_NAME["Ethereum"]["staked_eth_rebuild"]
    day_a = pd.Timestamp(anchor_time).normalize()
    days = pd.date_range(sp["electra_day"], day_a)
    rows = [{"day": d.date(), "deposits_gwei": str(int(dep * 1e9)), "withdrawals_gwei": str(int(wd * 1e9)),
             "n_deposits": 1, "bad_layout": 0, "n_withdrawals": 1, "n_blocks": 7150,
             "last_block_ts": (d + pd.Timedelta(hours=23, minutes=59)) if d < day_a else pd.Timestamp(anchor_time)}
            for d in days]
    q = se.simulate_queue({d: dep for d in days[:-1]}, sp["electra_day"], day_a, churn_day)
    frac = (pd.Timestamp(anchor_time) - day_a).total_seconds() / 86400
    pend = max(0.0, q[day_a] + dep - churn_day * frac)
    return rows, pend


def test_the_script_stores_nothing_without_apply_and_only_on_a_pass(tmp_path, monkeypatch, capsys):
    import staked_eth_rebuild as r
    t_a = pd.Timestamp("2026-11-04 10:05:11")
    rows, pend = _scenario(t_a, 44.43e6)
    anc = _Anchor(15_580_224, 44.43e6, pend)
    # the "truth": the rebuild's own balance (so validatorqueue agrees within 0.2%)
    a = r.anchor(anc)
    flows, partial, why = r.flows_from_rows(rows, a["time"])
    assert not why
    truth = r.rebuild(a, flows, partial)["balance"]
    db = _db(tmp_path, vq_value=lambda d: truth[d] * 0.998)
    assert r.main(["--db", str(db)], reader=anc, bq_rows=lambda _a: rows) == 0
    out = capsys.readouterr().out
    assert "OVERLAP TEST PASSED: 455 day(s)" in out and "TEST ONLY — nothing stored" in out
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM metrics WHERE metric='beacon_chain_eth_primary'").fetchone()[0] == 0
    con.close()
    assert r.main(["--db", str(db), "--apply"], reader=anc, bq_rows=lambda _a: rows) == 0
    con = sqlite3.connect(db)
    got = con.execute("SELECT MIN(date), MAX(date), COUNT(*), MIN(source) FROM metrics "
                      "WHERE metric='beacon_chain_eth_primary'").fetchone()
    con.close()
    assert got[0] == "2025-07-28" and got[1] == "2026-11-03" and got[2] == 455 + 9
    assert got[3].startswith(config.STAKED_REBUILD_POINT + "[anchor slot 15580224")


def test_the_script_refuses_when_one_day_is_outside_and_stores_nothing(tmp_path, capsys):
    import staked_eth_rebuild as r
    t_a = pd.Timestamp("2026-11-04 10:05:11")
    rows, pend = _scenario(t_a, 44.43e6)
    anc = _Anchor(15_580_224, 44.43e6, pend)
    a = r.anchor(anc)
    flows, partial, _ = r.flows_from_rows(rows, a["time"])
    truth = r.rebuild(a, flows, partial)["balance"]
    bad = pd.Timestamp("2026-01-15")
    db = _db(tmp_path, vq_value=lambda d: truth[d] * (0.985 if d == bad else 1.0))
    assert r.main(["--db", str(db), "--apply"], reader=anc, bq_rows=lambda _a: rows) == 1
    out = capsys.readouterr().out
    assert "OVERLAP TEST NOT PASSED" in out and "2026-01-15" in out and "NOT ADOPTED — nothing stored" in out
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM metrics WHERE metric='beacon_chain_eth_primary'").fetchone()[0] == 0
    con.close()


def test_rows_stop_before_the_first_node_day(tmp_path):
    import staked_eth_rebuild as r
    t_a = pd.Timestamp("2026-11-04 10:05:11")
    rows, pend = _scenario(t_a, 44.43e6)
    anc = _Anchor(15_580_224, 44.43e6, pend)
    a = r.anchor(anc)
    flows, partial, _ = r.flows_from_rows(rows, a["time"])
    bal = r.rebuild(a, flows, partial)["balance"]
    test = {"tolerance_pct": 1.0, "compared": 455, "worst_gap": 0.1, "worst_day": "2026-01-01"}
    out = r.rows_to_store(bal, pd.Timestamp("2026-10-30"), a, test)
    assert pd.Timestamp(out["date"].max()) == pd.Timestamp("2026-10-29")
    assert pd.Timestamp(out["date"].min()) == pd.Timestamp("2025-07-28")


def test_a_dataset_that_ends_before_the_anchor_or_a_bad_layout_is_refused():
    import staked_eth_rebuild as r
    t_a = pd.Timestamp("2026-11-04 10:05:11")
    rows, _ = _scenario(t_a, 44.43e6)
    short = rows[:-1]
    _f, _p, why = r.flows_from_rows(short, t_a)
    assert any("before the anchor" in w for w in why)
    bad = [dict(x) for x in rows]
    bad[5]["bad_layout"] = 2
    assert any("8-byte amount layout" in w for w in r.flows_from_rows(bad, t_a)[2])
    hole = rows[:10] + rows[11:]
    assert any("no block in the dataset" in w for w in r.flows_from_rows(hole, t_a)[2])


def test_the_query_names_only_the_public_dataset_and_decodes_the_little_endian_amount():
    sql = (ROOT / "sql" / "ethereum" / "bigquery_staked_flows_daily.sql").read_text(encoding="utf-8")
    from fetch.near_bigquery import NearBigQuery
    NearBigQuery._guard_sql(sql, {"allowed_dataset": "bigquery-public-data.crypto_ethereum"})
    assert "SUBSTR(l.data, 707, 16)" in sql and "REVERSE(FROM_HEX" in sql
    # byte 352 of the ABI data (5 head words, pubkey 32+64, withdrawal_credentials 32+32, the length word) is hex
    # character 3 + 2 x 352 = 707 of the '0x'-prefixed string
    assert 3 + 2 * (5 * 32 + 32 + 64 + 32 + 32 + 32) == 707
    assert 3 + 2 * 320 == 643
    sp = config.PROJECT_BY_NAME["Ethereum"]["staked_eth_rebuild"]
    assert sp["deposit_contract"] in sql and sp["deposit_topic"] in sql


# ---------- the handovers ----------

def test_the_handovers_are_declared_and_the_overlap_is_narrower_never_wider():
    eth = config.PROJECT_BY_NAME["Ethereum"]
    ov = eth["series_handover"]["beacon_chain_eth"]["overlap"]
    assert ov["tolerance_pct"] == 1.0 and ov["min_days"] == 455
    assert ov["days"] == ("2025-07-28", "2026-10-25") and ov["b_point"] == config.STAKED_REBUILD_POINT
    h = eth["series_handover"]["beacon_chain_eth_primary"]
    assert h["ordered_points"] == (config.STAKED_REBUILD_POINT, config.BEACON_NODE_POINT) and h["cut"]
    assert config.HANDOVER_CONTINUATION[("Ethereum", "beacon_chain_eth")]["requires_overlap"] is True


def test_a_long_overlap_names_its_worst_days_not_all_455():
    import build_workbook as bw
    days = pd.date_range("2025-07-28", "2026-10-25")

    def g(metric, src, vals):
        return pd.DataFrame({"date": days, "project": "Ethereum", "metric": metric, "value": vals, "source": src,
                             "tier": 1})
    groups = {("Ethereum", "beacon_chain_eth"): g("beacon_chain_eth", "validatorqueue:staked_amount", [44e6] * 455),
              ("Ethereum", "beacon_chain_eth_primary"): g("beacon_chain_eth_primary",
                                                          config.STAKED_REBUILD_POINT + "[x]",
                                                          [44e6 * (1.002 if i != 7 else 1.012) for i in range(455)])}
    ov = config.PROJECT_BY_NAME["Ethereum"]["series_handover"]["beacon_chain_eth"]["overlap"]
    r = bw.handover_overlap(groups, "Ethereum", ov)
    assert not r["passed"] and r["common_days"] == 455
    assert "worst days 2025-08-04 +1.20%" in r["text"] and "1 day(s) outside ±1%" in r["text"]
    assert len(r["text"]) < 1200


def test_the_runbook_section_names_the_commands_and_the_cooldown():
    text = (ROOT / "RUNBOOK.md").read_text(encoding="utf-8")
    sec = text[text.index("## 11ec."):text.index("## 11eb.")]
    for m in ("python staked_eth_rebuild.py", "--apply", "2026-12-01 15:02:02 UTC", "mode=ro", "OVERLAP TEST PASSED",
              "ethereum-beacon-api.publicnode.com", "freely revocable"):
        assert m in sec, m
