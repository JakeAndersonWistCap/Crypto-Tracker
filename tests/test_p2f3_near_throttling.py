"""P2f-3 — NEAR lockup read under HTTP 429 (no network, no sleeping: fakes and a fake clock).

The pacer changes only WHEN a call is made. Every test that reads lockups asserts the SAME lockup figures with and without
a 429 storm; the others pin the pacer's behaviour (Retry-After in both RFC forms, capped; jitter only upward; a global,
debounced slow-down and recovery; the cool-down ladder surviving a lone success) and the in-run retry sweep.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import check_offline_items as coi  # noqa: E402

Y = 10 ** 24
A, B = "https://a", "https://b"


class Clock:
    """A fake monotonic clock; sleeping advances it."""

    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def pacer(clock=None, pace=None, **kw):
    clock = clock or Clock()
    return coi._NearPacer(pace if pace is not None else {A: 0.25, B: 1.0}, default=0.25, clock=clock.now,
                          sleep=clock.sleep, **kw), clock


# ------------------------------------------------------------------------------------------------ Retry-After
def test_retry_after_reads_seconds_and_http_dates_and_refuses_the_rest():
    f = coi.retry_after_seconds
    assert f("7") == 7.0 and f(" 2.5 ") == 2.5 and f(0) == 0.0
    from email.utils import format_datetime
    from datetime import datetime, timezone
    d = datetime(2026, 10, 21, 7, 28, 0, tzinfo=timezone.utc)
    assert f(format_datetime(d, usegmt=True), now=d.timestamp() - 42) == 42.0
    assert f(format_datetime(d, usegmt=True), now=d.timestamp() + 5) is None          # a date in the past: no wait given
    for bad in (None, "", "abc", "-3", "nan", "inf", "-inf"):
        assert f(bad) is None, bad
    assert f("99999") == coi.NEAR_RETRY_AFTER_MAX_S                                     # capped, never parked for good


def test_the_pacer_honours_retry_after_in_either_form_and_falls_back_to_the_ladder():
    p, _c = pacer()
    assert p.throttled(A, "7") == 7.0
    assert p.throttled(B, "inf") == 5.0                                                   # unusable header: the ladder
    assert p.throttled(B, "garbage") == 10.0
    assert p.throttled(A, "99999") == coi.NEAR_RETRY_AFTER_MAX_S


def test_jitter_is_only_ever_later_than_the_server_asked_and_bounded():
    for r in (0.0, 0.5, 0.999):
        p, c = pacer(jitter=0.25, rng=lambda r=r: r)
        got = p.throttled(A, "10")
        assert 10.0 <= got <= 12.5 and abs(got - 10.0 * (1 + 0.25 * r)) < 1e-9
        assert abs(p.next_ok[A] - (c.t + got)) < 1e-9
    p, _c = pacer(jitter=0.25, rng=lambda: 1.0)
    assert [p.throttled(B) for _ in range(6)] == [6.25, 12.5, 25.0, 50.0, 75.0, 75.0]    # ladder 5..60 plus <= 25%


# ------------------------------------------------------------------------------------------------ the global slow-down
def test_a_429_burst_slows_the_endpoint_once_for_every_thread_and_clean_calls_speed_it_back():
    p, c = pacer()
    assert p._gap(A) == 0.25
    for _ in range(4):                                  # four workers' calls in flight all come back 429
        p.throttled(A, "1")
    assert p.mult[A] == 1.5 and p.stats["slowdowns"] == 1, "one slow-down per burst, not one per call"
    assert p._gap(A) == 0.375 and p._gap(B) == 1.0       # the other endpoint is untouched
    c.t += 100                                          # the window passes; the next 429 is a new burst
    p.throttled(A, "1")
    assert p.mult[A] == 2.25 and p.stats["slowdowns"] == 2
    for _ in range(coi.NEAR_GAP_FAST_AFTER):
        p.ok(A)
    assert abs(p.mult[A] - 2.25 * 0.9) < 1e-9
    for _ in range(coi.NEAR_GAP_FAST_AFTER * 20):
        p.ok(A)
    assert p.mult[A] == 1.0 and p._gap(A) == 0.25         # back to the base, never below it


def test_the_gap_never_passes_its_ceiling_and_a_zero_base_never_grows():
    p, c = pacer(pace={A: 1.0})
    for _ in range(40):
        c.t += 1000
        p.throttled(A, "1")
    assert p._gap(A) == coi.NEAR_GAP_MAX_S
    z, c2 = pacer(pace={A: 0.0})
    for _ in range(5):
        c2.t += 1000
        z.throttled(A, "1")
    assert z._gap(A) == 0.0


def test_a_lone_success_does_not_wipe_the_cooldown_ladder():
    p, _c = pacer()
    assert p.throttled(B) == 5.0
    p.ok(B)                                              # one answer in a storm
    assert p.throttled(B) == 10.0, "the ladder keeps climbing"
    for _ in range(coi.NEAR_STRIKE_RESET_OKS):
        p.ok(B)
    assert p.throttled(B) == 5.0, "reset after a run of clean calls"


def test_wait_turn_spaces_calls_by_the_current_gap_on_the_pacers_own_clock():
    p, c = pacer()
    p.wait_turn(A)
    p.wait_turn(A)
    assert abs(c.t - 1000.25) < 1e-9 and p.stats["calls"] == 2
    p.throttled(A, "3")
    p.wait_turn(A)
    assert c.t >= 1003.25 and c.slept[-1] <= 5.0


# ------------------------------------------------------------------------------------------------ same figures under 429s
LOCKUPS = {                       # account -> (balance, locked, owners, unvested via a schedule or None)
    "a.lockup.near": (10, 4, 6, None),
    "b.lockup.near": (20, 9, 11, 3),
    "c.lockup.near": (0, 0, 0, None),                    # zero balance
    "d.lockup.near": (7, 7, 0, 7),
    "e.lockup.near": (5, 1, 4, None),
}


class FakeNode:
    """requests.post stand-in: one deterministic answer per (method, account), with a scripted failure plan in front of it."""

    def __init__(self, plan=None):
        self.plan = plan or (lambda n, acct, kind: None)       # (call number, account, kind) -> None | ("429", hdr) | ...
        self.n = 0
        self.calls = []
        self.hosts = set()

    def post(self, url, json=None, **kw):
        self.n += 1
        params = json["params"]
        acct = params.get("account_id", "")
        kind = params.get("method_name") or params.get("request_type") or json["method"]
        self.calls.append((acct, kind))
        self.hosts.add(url)
        fail = self.plan(self.n, acct, kind)
        if fail == "exc":
            raise ConnectionError("reset")
        if fail:
            code, hdr = fail
            return Resp(code, None, hdr)
        return Resp(200, self.answer(json["method"], params))

    @staticmethod
    def answer(method, params):
        if method == "block":
            return {"result": {"header": {"height": 777, "timestamp": 1_790_000_000 * 10 ** 9}}}
        rt = params.get("request_type")
        acct = params.get("account_id")
        bal, locked, owners, unv = LOCKUPS[acct]
        if rt == "view_account":
            return {"result": {"code_hash": "plain", "amount": str(bal * Y)}}
        m = params["method_name"]
        val = {"get_balance": str(bal * Y), "get_locked_amount": str(locked * Y), "get_owners_balance": str(owners * Y),
               "get_vesting_information": ({"VestingSchedule": {"x": 1}} if unv is not None else "None"),
               "get_unvested_amount": str((unv or 0) * Y)}[m]
        raw = list(__import__("json").dumps(val).encode())
        assert base64.b64decode(params["args_base64"]) is not None
        return {"result": {"result": raw}}


class Resp:
    def __init__(self, code, body, headers=None):
        self.status_code, self._b, self.headers = code, body, headers or {}

    def json(self):
        if self._b is None:
            raise ValueError("no json")
        return self._b


def run_read(monkeypatch, tmp_path, plan=None, **kw):
    clock = Clock()
    monkeypatch.setattr(coi, "NEAR_PACER", coi._NearPacer({}, default=0.0, jitter=0.25, rng=lambda: 0.5, clock=clock.now,
                                                          sleep=clock.sleep))
    node = FakeNode(plan)
    monkeypatch.setattr(coi.requests, "post", node.post)
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path))
    tmp_path.mkdir(parents=True, exist_ok=True)
    f = tmp_path / "l.txt"
    f.write_text("".join(f"{a}\n" for a in LOCKUPS))
    monkeypatch.setenv("NEAR_LOCKUPS_FILE", str(f))
    got = coi.near_lockups(workers=1, route="getters", **kw)
    return got, node, clock


FIGURES = ("height", "accounts", "read", "zero", "unreadable", "balance", "locked", "unvested", "owners", "buggy",
           "block_ts")


def test_the_same_fake_responses_give_the_same_lockup_figures_with_and_without_429s(monkeypatch, tmp_path, capsys):
    base, node0, _c = run_read(monkeypatch, tmp_path / "clean")
    assert base["read"] == 4 and base["zero"] == 1 and base["unreadable"] == 0
    assert (base["balance"], base["locked"], base["unvested"], base["owners"]) == (42.0, 21.0, 10.0, 21.0)
    sequences = {
        "every second call 429": lambda n, a, k: (429, {}) if n % 2 == 0 else None,
        "Retry-After seconds": lambda n, a, k: (429, {"Retry-After": "3"}) if n % 3 == 0 else None,
        "5xx and resets": lambda n, a, k: ("exc" if n % 5 == 0 else ((503, {}) if n % 4 == 0 else None)),
        "a hostile header": lambda n, a, k: (429, {"Retry-After": "inf"}) if n % 2 else None,
        "a storm on the first calls": lambda n, a, k: (429, {}) if n <= 12 else None,
    }
    for name, plan in sequences.items():
        got, node, clock = run_read(monkeypatch, tmp_path / name.replace(" ", "_"), plan)
        assert {k: got[k] for k in FIGURES} == {k: base[k] for k in FIGURES}, name
        assert got["errors"] == {} and got["unreadable"] == 0, name
        assert node.n > node0.n and clock.t > 1000.0, f"{name}: the 429s were really served and waited out"
        assert sorted(set(node.calls)) == sorted(set(node0.calls)), f"{name}: the same questions were asked"
    capsys.readouterr()


def test_a_lockup_that_runs_out_of_attempts_is_tried_again_last_in_the_same_run_and_read_only_once(monkeypatch, tmp_path,
                                                                                                  capsys):
    base, _n, _c = run_read(monkeypatch, tmp_path / "clean")
    # b's first NEAR_RPC_ATTEMPTS + 3 questions fail (sweep 1 gives up on it, sweep 2 gets through); the others answer
    left = {"n": coi.NEAR_RPC_ATTEMPTS + 3}

    def plan(n, acct, kind):
        if acct == "b.lockup.near" and left["n"] > 0:
            left["n"] -= 1
            return (429, {})
        return None
    got, node, _c = run_read(monkeypatch, tmp_path / "storm", plan)
    out = capsys.readouterr().out
    assert "sweep 2" in out, "the unread lockup was tried again in this run"
    assert {k: got[k] for k in FIGURES} == {k: base[k] for k in FIGURES}
    assert got["errors"] == {} and got["unreadable"] == 0
    # nothing already read was read again: a, d, e are asked exactly as often as on a clean run
    for acct in ("a.lockup.near", "d.lockup.near", "e.lockup.near", "c.lockup.near"):
        assert [c for c in node.calls if c[0] == acct] == [c for c in _n.calls if c[0] == acct], acct


def test_sweeps_are_bounded_and_the_errors_are_counted_once_per_lockup(monkeypatch, tmp_path, capsys):
    got, node, _c = run_read(monkeypatch, tmp_path / "dead", lambda n, a, k: (429, {}) if a == "e.lockup.near" else None)
    out = capsys.readouterr().out
    assert got["read"] == 3 and got["unreadable"] == 1                                    # c is zero, e never read
    assert sum(got["errors"].values()) == 1, "one lockup, one error — not one per sweep"
    import near_lockups_weekly as nl
    assert nl.read_failures(got) == 1, "the weekly script still stores nothing"
    asks = [c for c in node.calls if c[0] == "e.lockup.near"]
    assert len(asks) <= coi.NEAR_LOCKUP_SWEEPS * coi.NEAR_RPC_ATTEMPTS
    assert out.count("sweep ") <= coi.NEAR_LOCKUP_SWEEPS - 1
    # a sweep that reads nothing new stops the sweeping
    assert "sweep 3" not in out
    # and the cache keeps what was read, at the pinned block, for the next run
    cache = json.loads((tmp_path / "dead" / "near-lockups-read.json").read_text())
    assert cache["height"] == 777 and set(cache["results"]) == {"a.lockup.near", "b.lockup.near", "c.lockup.near",
                                                                 "d.lockup.near"}
    # the rerun reads only e, at the same block, and completes with the clean figures
    base, _n, _c2 = run_read(monkeypatch, tmp_path / "clean")
    got2, node2, _c3 = run_read(monkeypatch, tmp_path / "dead")
    assert {a for a, _k in node2.calls} - {""} == {"e.lockup.near"}      # ("" is the block / pin check)
    assert {k: got2[k] for k in FIGURES} == {k: base[k] for k in FIGURES}


def test_an_interrupt_leaves_no_queue_of_reads_and_keeps_what_was_read(monkeypatch, tmp_path, capsys):
    _b, clean, _c = run_read(monkeypatch, tmp_path / "clean")
    seen = []

    def plan(n, acct, kind):
        seen.append(acct)
        if len(seen) > 6:
            raise KeyboardInterrupt
        return None
    try:
        run_read(monkeypatch, tmp_path / "int", plan)
    except KeyboardInterrupt:
        pass
    capsys.readouterr()
    assert len(seen) < clean.n, "the remaining lockups were cancelled, not drained"
    cache = json.loads((tmp_path / "int" / "near-lockups-read.json").read_text())
    assert cache["height"] == 777 and len(cache["results"]) >= 1


def test_a_429_storm_on_the_pin_checks_neither_ends_the_run_nor_throws_the_progress_away(monkeypatch, tmp_path, capsys):
    # (1) the first block fetch is throttled on every attempt of its first try: the next try pins the block
    base, _n, _c = run_read(monkeypatch, tmp_path / "clean")
    got, _node, _c = run_read(monkeypatch, tmp_path / "pin", lambda n, a, k: (429, {}) if n <= coi.NEAR_RPC_ATTEMPTS else None)
    assert got is not None and {k: got[k] for k in FIGURES} == {k: base[k] for k in FIGURES}
    # (2) a resume whose pin check is throttled keeps the pin (the lockups already read stay read) ...
    cache = {"height": 777, "pinned_at": coi._now_utc(), "results": {}}
    always = FakeNode(lambda n, a, k: (429, {}))
    clk = Clock()
    monkeypatch.setattr(coi, "NEAR_PACER", coi._NearPacer({}, default=0.0, clock=clk.now, sleep=clk.sleep))
    monkeypatch.setattr(coi.requests, "post", always.post)
    assert coi._lockup_pin_live(cache) is True
    assert always.n == coi.NEAR_LOCKUP_PIN_TRIES * coi.NEAR_RPC_ATTEMPTS
    # ... but an endpoint that ANSWERS "no such block" still ends it, whatever the other endpoint does
    mixed = iter([("u1", {"_transient": "429"}), ("u2", {"error": {"name": "UNKNOWN_BLOCK"}})])
    monkeypatch.setattr(coi, "_near_post", lambda params, method="query", urls=None, attempts=None: mixed)
    assert coi._lockup_pin_live(cache) is False
    capsys.readouterr()
