"""
tests/test_round_g.py — Round G (Jake 2026-10-18). One test per bug fixed, each named for the item in SPEC §19.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402
from fetch import base  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402


def _ok_response(request):
    r = requests.Response()
    r.status_code = 200
    r._content = b'{"ok": 1}'
    r.url = request.url
    r.request = request
    return r


# ------------------------------------------------------------------------------------------------ G1.1 connections
def test_g1_1_the_send_patch_belongs_to_the_live_module_after_config_loads():
    """At bf53e87 `import config` reached fetch during its own import-time validation; fetch.base patched requests and
    then failed (config.TRAJECTORY not yet defined), and that orphan's patch stayed installed — every HTTP call
    registered in a registry the heartbeat never read ("waiting on: nothing registered"). A fresh interpreter, config
    first, then the run's own imports: the installed send is the live module's and wraps requests' true original."""
    import subprocess
    code = ("import config, token_metrics, requests, fetch.base as b, fetch;"
            "f = requests.adapters.HTTPAdapter.send;"
            "print(f.__globals__ is vars(b), f._tm_original.__module__, f._tm_original.__name__)")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.stdout.split() == ["True", "requests.adapters", "send"], r.stdout + r.stderr


def test_g1_1_every_requests_call_is_capped_per_host_and_run_wide(monkeypatch):
    """~45 tiers started together opened connections without limit (Jake's full runs: 27–60 SSL/connection failures,
    4 when the same projects ran in four groups). At most MAX_CONN_PER_HOST calls to one host are ever in flight, and
    the ones waiting are registered with the heartbeat as queued."""
    monkeypatch.setattr(base, "_HOST_SLOTS", {})
    monkeypatch.setattr(base, "MAX_CONN_PER_HOST", 2)
    monkeypatch.setattr(base, "_GLOBAL_SLOTS", threading.BoundedSemaphore(3))
    live, peak, queued_seen, lock = [0], [0], [], threading.Lock()

    def fake_send(self, request, **kw):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        queued_seen.extend(w for _, w, _ in base.inflight() if w.startswith("queued for"))
        time.sleep(0.05)
        with lock:
            live[0] -= 1
        return _ok_response(request)
    monkeypatch.setattr(base, "_ORIGINAL_SEND", fake_send)
    ts = [threading.Thread(target=lambda: requests.Session().get("https://one.example/x", timeout=5)) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert peak[0] == 2, "two calls to one host at most"
    assert any("queued for one.example" in q for q in queued_seen)


def test_g1_1_one_shared_pool_and_connect_errors_retried_once_per_layer(monkeypatch):
    """Every Session (and web3 provider) sends through the run's one connection pool, so TLS connections are reused;
    a connect-phase SSLError is retried with backoff — by the send layer for web3/raw calls, by Http itself for Http
    calls (never both)."""
    monkeypatch.setenv("TOKEN_METRICS_CONNECT_RETRIES", "2")
    adapters, fails, naps = [], [2], []

    def fake_send(self, request, **kw):
        adapters.append(self)
        if fails[0]:
            fails[0] -= 1
            raise requests.exceptions.SSLError("EOF occurred in violation of protocol")
        return _ok_response(request)
    monkeypatch.setattr(base, "_ORIGINAL_SEND", fake_send)
    monkeypatch.setattr(base, "sleep", lambda s, what: naps.append(s))
    r = requests.Session().get("https://two.example/a", timeout=5)
    assert r.status_code == 200 and len(adapters) == 3 and len(naps) == 2
    assert all(a is base._SHARED_ADAPTER for a in adapters)
    # a ReadTimeout is not a connect failure: raised at once
    adapters.clear()

    def timeout_send(self, request, **kw):
        adapters.append(self)
        raise requests.exceptions.ReadTimeout("read timed out")
    monkeypatch.setattr(base, "_ORIGINAL_SEND", timeout_send)
    with pytest.raises(requests.exceptions.ReadTimeout):
        requests.Session().get("https://two.example/b", timeout=5)
    assert len(adapters) == 1
    # inside Http, the send layer makes ONE attempt per Http attempt (Http's own loop retries)
    adapters.clear()
    fails[0] = 1
    monkeypatch.setattr(base, "_ORIGINAL_SEND", fake_send)
    h = base.Http(retries=2)
    monkeypatch.setattr(base, "sleep", lambda s, what: None)
    assert h.get("https://two.example/c") == {"ok": 1}
    assert len(adapters) == 2 and h.stats["retries"] == 1


def test_g1_1_tiers_start_staggered_and_an_abandoned_tier_stops(monkeypatch):
    """Tiers start TIER_STAGGER apart (not all in one instant); a tier abandoned at its budget STOPS — its next HTTP call
    raises TierAbandoned and its log lines are dropped (Jake's Ether.fi run: a probe logged after the run summary)."""
    import fetch
    monkeypatch.setenv("TOKEN_METRICS_TIER_STAGGER", "0.15")
    starts = {}
    after_abandon = []

    class Quick:
        def __init__(self, name):
            self.name = name

        def run(self, projects, window_days, out):
            starts[self.name] = time.monotonic()

    class Slow:
        def run(self, projects, window_days, out):
            starts["slow"] = time.monotonic()
            time.sleep(0.6)                            # the budget (0.3 s) passes; the dispatcher abandons this thread
            try:
                base.check_abandoned()
            except base.TierAbandoned as e:
                after_abandon.append(str(e))
                logging.getLogger("token_metrics.fetch").warning("this line must not print")
                raise
    monkeypatch.setattr(fetch, "_budget", lambda name: 0.3 if name == "slow" else 30)
    todo = [("a", 2, lambda ctx: Quick("a")), ("slow", 2, lambda ctx: Slow()), ("b", 2, lambda ctx: Quick("b"))]
    res = fetch._dispatch(todo, [], None, {})
    assert [r[0] for r in res] == ["a", "slow", "b"]
    assert starts["slow"] - starts["a"] >= 0.12 and starts["b"] - starts["slow"] >= 0.12
    assert res[1][4] is True                            # timed out
    time.sleep(0.6)
    assert after_abandon and "abandoned" in after_abandon[0]
    rec = logging.LogRecord("x", logging.WARNING, "", 0, "m", None, None)
    assert base.DROP_ABANDONED.filter(rec) is True     # this (main) thread is not abandoned


def test_g1_1_pool_workers_belong_to_their_tier(monkeypatch):
    """Worker threads a tier starts (plume_staking, plume_settlement, mev_relays) stop with it."""
    from concurrent.futures import ThreadPoolExecutor
    got = []

    def tier():
        with ThreadPoolExecutor(max_workers=2, **base.pool_owner()) as ex:
            base.abandon_thread(threading.current_thread())
            got.extend(ex.map(lambda _: base.is_abandoned(), range(3)))
    t = threading.Thread(target=tier, name="g11_tier")
    t.start()
    t.join()
    assert got == [True, True, True]
    for name in ("plume_staking", "plume_settlement", "mev_relays"):
        assert "pool_owner()" in (ROOT / "fetch" / f"{name}.py").read_text()


def test_g1_1_robots_unreachable_uses_the_last_good_copy(monkeypatch, tmp_path):
    """robots.txt for Coinbase / NearBlocks / Ultrasound / Maple failed on SSL errors in the run's first minute and each
    source was refused. The last definite answer is cached; unreachable now -> that copy (a Disallow still blocks, a
    404 still allows); no copy -> complete disallow as before."""
    from fetch import scrape
    monkeypatch.setattr(scrape, "_ROBOTS", {})
    monkeypatch.setattr(scrape, "_robots_sleep", lambda s: None)
    answers = {"https://a.example/robots.txt": (200, "User-agent: *\nDisallow: /private\n"),
               "https://b.example/robots.txt": (404, "")}
    down = {"on": False}

    def fake_get(url, headers=None, timeout=None):
        if down["on"]:
            raise requests.exceptions.SSLError("EOF occurred in violation of protocol")
        st, text = answers[url]
        return SimpleNamespace(status_code=st, text=text)
    monkeypatch.setattr(requests, "get", fake_get)
    assert scrape.robots_verdict("https://a.example/private/x")[0] is False
    assert scrape.robots_verdict("https://b.example/any")[0] is True
    down["on"] = True
    monkeypatch.setattr(scrape, "_ROBOTS", {})
    ok, why = scrape.robots_verdict("https://a.example/public")
    assert ok and "using the copy read on" in why and scrape.UNREACHABLE_MARK not in why
    assert scrape.robots_verdict("https://a.example/private/y")[0] is False      # the real Disallow still blocks
    assert scrape.robots_verdict("https://b.example/z")[0] is True               # a 404 still allows
    ok, why = scrape.robots_verdict("https://c.example/z")                        # never read: disallow, as before
    assert not ok and scrape.UNREACHABLE_MARK in why


# --------------------------------------------------------------------------------------- G1.2 / G1.5 / G1.6 past state
class _Eth:
    def __init__(self, url, behaviour, calls):
        self.url, self.b, self.calls = url, behaviour, calls

    def contract(self, address=None, abi=None):
        eth = self

        class Fn:
            def __init__(self, name):
                self.name = name

            def __call__(self, *a):
                return self

            def call(self, block_identifier=None):
                eth.calls.append((eth.url, self.name, block_identifier))
                how = eth.b(eth.url, block_identifier)
                if isinstance(how, Exception):
                    raise how
                return how
        return SimpleNamespace(functions=SimpleNamespace(balanceOf=Fn("balanceOf"), totalSupply=Fn("totalSupply")))

    def get_code(self, address, block_identifier=None):
        self.calls.append((self.url, "get_code", block_identifier))
        return b"" if block_identifier < 1000 else b"\x60\x80"


def _reader(monkeypatch, urls, behaviour):
    from fetch import chain as ch
    calls = []
    monkeypatch.setattr(ch, "rpc_endpoints", lambda chain: list(urls))
    monkeypatch.setattr(ch.ChainReader, "make_web3",
                        staticmethod(lambda chain, url, t=30: SimpleNamespace(eth=_Eth(url, behaviour, calls))))
    return ch.ChainReader(), calls


def test_g1_6_a_403_marks_the_endpoint_down_for_the_run_and_keyed_goes_first(monkeypatch):
    def behaviour(url, block):
        if "public" in url:
            return requests.exceptions.HTTPError("403 Client Error: Forbidden for url: https://public.example")
        return 7
    r, calls = _reader(monkeypatch, ["https://keyed.example", "https://public.example"], behaviour)
    T, H = "0x" + "1" * 40, "0x" + "2" * 40
    assert r.erc20_balance_at("optimism", T, H, 5000) == 7
    assert calls[0][0] == "https://keyed.example"
    # the keyed endpoint later refuses past state: public answers 403 once, then is skipped for the run
    r2, calls2 = _reader(monkeypatch, ["https://public.example", "https://archive.example"],
                         lambda url, b: behaviour(url, b))
    assert r2.erc20_balance_at("optimism", T, H, 5000) == 7
    assert r2.is_down("optimism", "https://public.example")
    calls2.clear()
    r2._archive_ok.clear()
    assert r2.erc20_balance_at("optimism", T, H, 6000) == 7
    assert [c[0] for c in calls2] == ["https://archive.example"], "the 403 endpoint is not asked again"


def test_g1_2_empty_return_before_deployment_reads_zero_and_no_history_stops_at_once(monkeypatch):
    """mainnet.optimism.io / drpc answered b'' for ETHFI on OP at block 79,180,853: no code there yet — the balance is 0,
    a fact. When every endpoint refuses past state for a non-block reason, the chain is marked NO HISTORICAL STATE and
    the next past-block read fails without asking anyone."""
    empty = ValueError("Could not decode contract function call to balanceOf(address) with return data: b'', "
                       "output_types: ['uint256']")
    r, calls = _reader(monkeypatch, ["https://op.example"], lambda url, b: empty if b < 1000 else 5)
    T, H = "0x" + "1" * 40, "0x" + "2" * 40
    assert r.erc20_balance_at("optimism", T, H, 500) == 0
    assert ("https://op.example", "get_code", 500) in calls
    assert r.erc20_balance_at("optimism", T, H, 5000) == 5
    # empty although the contract HAS code: a refusal, not a zero
    with pytest.raises(RuntimeError, match="empty return although the contract has code"):
        _reader(monkeypatch, ["https://op.example"], lambda url, b: empty)[0].erc20_balance_at("optimism", T, H, 5000)

    def refuse(url, b):
        return (requests.exceptions.HTTPError("403 Client Error: Forbidden") if "a." in url
                else ValueError("missing trie node abc (path ) state is not available"))
    r3, calls3 = _reader(monkeypatch, ["https://a.example", "https://b.example"], refuse)
    with pytest.raises(RuntimeError):
        r3.erc20_balance_at("optimism", T, H, 5000)
    n = len(calls3)
    with pytest.raises(RuntimeError, match="NO HISTORICAL STATE"):
        r3.erc20_balance_at("optimism", T, H, 4000)
    assert len(calls3) == n, "no endpoint asked once the chain has no historical state"
    with pytest.raises(RuntimeError, match="NO HISTORICAL STATE"):
        r3.erc20_supply_at("optimism", T, 4000)


def test_g1_5_plasma_past_state_read_when_the_kept_endpoint_answers_first(monkeypatch):
    """Jake's Fluid run: "rpc.plasma.to: 'ChainReader' object has no attribute '_w3_by_url'" — the kept endpoint
    answered first and the per-URL cache had never been made."""
    r, calls = _reader(monkeypatch, ["https://rpc.plasma.to"], lambda url, b: 42)
    del r.__dict__["_w3_by_url"]                                   # the state the bug needed
    r._w3["plasma"] = r.make_web3("plasma", "https://rpc.plasma.to")
    r._w3_url["plasma"] = "https://rpc.plasma.to"
    assert r.erc20_balance_at("plasma", "0x" + "1" * 40, "0x" + "2" * 40, "latest") == 42


def test_g1_2_alchemy_key_serves_optimism_when_no_optimism_url_is_set(monkeypatch):
    from fetch import chain as ch
    for c in ch.ALCHEMY_HOSTS:
        monkeypatch.delenv(f"{c.upper()}_RPC_URL", raising=False)
        monkeypatch.delenv(f"RPC_{c.upper()}", raising=False)
    monkeypatch.setenv("ARBITRUM_RPC_URL", "https://arb-mainnet.g.alchemy.com/v2/SECRETKEY123")
    eps = ch.rpc_endpoints("optimism")
    assert eps[0] == "https://opt-mainnet.g.alchemy.com/v2/SECRETKEY123"
    assert eps[1:] == list(config.DEFAULT_RPC["optimism"])
    assert "SECRETKEY123" not in ch.rpc_host(eps[0])
    monkeypatch.setenv("OPTIMISM_RPC_URL", "https://my.op.example")
    assert ch.rpc_endpoints("optimism")[0] == "https://my.op.example"            # an explicit URL wins
    monkeypatch.delenv("OPTIMISM_RPC_URL")
    monkeypatch.setenv("TOKEN_METRICS_NO_ALCHEMY_DERIVE", "1")
    assert ch.rpc_endpoints("optimism") == list(config.DEFAULT_RPC["optimism"])
    # chains with no known Alchemy host are untouched
    monkeypatch.delenv("TOKEN_METRICS_NO_ALCHEMY_DERIVE")
    # (G3 item 10 gave Plasma an Alchemy host; Katana still has none)
    assert ch.rpc_endpoints("katana") == list(config.DEFAULT_RPC["katana"])


def test_g1_2_hole_bisection_never_uses_the_explorer_tail_as_a_balance(monkeypatch):
    """The tip-minus-logged-tail stand-in is built from the explorer's own logs, so inside the hole bisection it differs
    by the same amount at every block before the hole and the search halves to block ~0 (Jake: 79,180,853 -> 19,331 at
    13–25 s a step). Without archive state the search stops at once."""
    from fetch.logscan import LogScan
    ls = LogScan.__new__(LogScan)
    tail_used = []

    class Reader:
        def erc20_balance_at(self, *a):
            raise RuntimeError("balanceOf at block refused by every optimism endpoint: 403")

        def erc20(self, *a):
            tail_used.append(1)
            raise AssertionError("the tail route must not run")
    ls.reader, ls.explorer = Reader(), None
    with pytest.raises(RuntimeError, match="not used here"):
        ls._balance_at("optimism", 10, "0xt", "0xh", 100, allow_tail=False)
    assert not tail_used
    ins = [{"blockNumber": 50, "topics": ["0x", "0x" + "0" * 64, "0x" + "0" * 24 + "a" * 40], "data": hex(5),
            "transactionHash": "0x1", "logIndex": 0}]
    ins2, outs2, holes, trace = ls._patch_explorer_holes("optimism", 10, "0xt", ["0x" + "a" * 40], 1, 200, ins, [], 0)
    assert holes == [] and any("stopped" in t and "not used here" in t for t in trace) and not tail_used


# ----------------------------------------------------------------------------------------------------- G1.3 aero_voter
def test_g1_3_aero_voter_marker_records_what_a_complete_read_produced(tmp_path):
    """2026-10-15: the abandoned thread set the epoch marker after its frames were dropped, so every later run re-read
    the epoch (~100 s) and timed out. The marker now carries the metrics the read produced: rows produced and not stored
    are read again; a metric the read could not produce (no fees/bribes split for the epoch) is genuine partiality —
    named, and not re-read every run."""
    from fetch.aero_voter import AeroVoter
    from fetch.logcache import DailyChecks
    calls = []

    def epoch_reader(epoch):                     # no split, no illiquid figure: a genuinely partial epoch
        calls.append(epoch)
        return {"epoch": epoch, "voter": "0xv", "pools": 300, "total_weight": 1e9, "usd": 1e6, "unpriced": [],
                "unpriced_raw": {}, "priced": 120, "aero_price": 0.8, "priced_at": "epoch end"}
    now = int(pd.Timestamp("2026-10-09 12:00").timestamp())
    daily, p = DailyChecks(tmp_path), config.PROJECT_BY_NAME["Aerodrome"]
    stored_rows = set()

    def stored(proj, m, d):
        return d != "2026-10-01" or (m, d) in stored_rows
    out = FetchOutput()
    AeroVoter(epoch_reader=epoch_reader, weight_reader=lambda: 1e9, daily=daily, now=now, stored=stored).run([p], None, out)
    assert len(calls) == 1
    mark = daily.get("aero_voter:Aerodrome:epoch")
    assert mark.startswith("2026-10-01|") and "voter_rewards_onchain_usd" in mark
    assert "voter_rewards_onchain_fees_usd" not in mark
    # the run's rows reached the store: the next run does NOT re-read for the split the epoch does not have
    for f in out.frames:
        for r in f.itertuples():
            stored_rows.add((r.metric, str(r.date)[:10]))
    out2 = FetchOutput()
    AeroVoter(epoch_reader=epoch_reader, weight_reader=lambda: 1e9, daily=daily, now=now, stored=stored).run([p], None, out2)
    assert len(calls) == 1
    assert any("did not produce voter_rewards_onchain_fees_usd" in e.message for e in out2.log)
    # rows produced but NOT stored (dropped by an abandoned tier): read again
    stored_rows.clear()
    AeroVoter(epoch_reader=epoch_reader, weight_reader=lambda: 1e9, daily=daily, now=now, stored=stored).run(
        [p], None, FetchOutput())
    assert len(calls) == 2


def test_g1_3_an_abandoned_aero_voter_never_marks_its_epoch(tmp_path):
    from fetch.aero_voter import AeroVoter
    from fetch.logcache import DailyChecks
    daily = DailyChecks(tmp_path)

    def epoch_reader(epoch):
        base.abandon_thread(threading.current_thread())       # the dispatcher abandons the tier mid-read
        return {"epoch": epoch, "voter": "0xv", "pools": 1, "total_weight": 1e9, "usd": 1.0, "unpriced": [],
                "unpriced_raw": {}, "priced": 1, "aero_price": 0.8, "priced_at": "epoch end"}
    now = int(pd.Timestamp("2026-10-09 12:00").timestamp())
    err = []

    def go():
        try:
            AeroVoter(epoch_reader=epoch_reader, weight_reader=lambda: 1e9, daily=daily, now=now,
                      stored=lambda *a: False).run([config.PROJECT_BY_NAME["Aerodrome"]], None, FetchOutput())
        except base.TierAbandoned as e:
            err.append(e)
    t = threading.Thread(target=go, name="aero_voter_g13")
    t.start()
    t.join()
    assert err and daily.get("aero_voter:Aerodrome:epoch") is None


# ----------------------------------------------------------------------------------------------------- G1.4 ve_managed
def test_g1_4_ve_managed_reads_holdings_first_and_the_window_under_its_own_deadline(tmp_path, monkeypatch):
    import check_offline_items as coi
    from fetch import ve_managed as vm
    from fetch.logcache import DailyChecks
    order = []
    p = {"name": "Aerodrome", "ve_managed_holdings": {"metric": "m", "escrow": "0x" + "e" * 40, "owners": {}},
         "foundation_buyback": {"stock_metric": "s", "claimable_metric": "c", "owners": {"0x" + "f" * 40: "F"},
                                "basis": "bought_plus_compounded", "window_budget_s": 0.5,
                                "window_metrics": {"compounded_owner": "w"}}}
    a = vm.VeManaged(reader=SimpleNamespace(), daily=DailyChecks(tmp_path))
    monkeypatch.setattr(a, "_holdings", lambda proj, out: order.append("holdings"), raising=False)
    a.foundation_reader = lambda owners: (order.append("locks") or {"error": None, "locked": 1.0, "claimable": 0.0,
                                                                    "by_owner": {"0x" + "f" * 40: 1.0}, "nfts": 1,
                                                                    "owned_managed": 0})
    a.day_blocks = lambda d: (1, 2)

    def slow_window(b0, b1):
        order.append("window")
        for _ in range(10):
            coi._retry(lambda: (_ for _ in ()).throw(ValueError("archive read refused")), tries=6)
        return {}
    a.window_reader = slow_window
    out = FetchOutput()
    t0 = time.monotonic()
    a.run([p], None, out)
    assert order == ["holdings", "locks", "window"]
    assert time.monotonic() - t0 < 3, "the window stopped at its deadline"
    msg = " ".join(e.message for e in out.log)
    assert "Foundation buyback window" in msg and "time budget spent" in msg and "read again on the next run" in msg
    assert DailyChecks(tmp_path).get("ve_managed:Aerodrome:buyback_window") is None


def test_g1_4_retry_naps_are_registered_and_name_the_error(monkeypatch):
    import check_offline_items as coi
    seen = []
    monkeypatch.setattr(base, "sleep", lambda s, what: seen.append(what))
    n = [0]

    def flaky():
        n[0] += 1
        if n[0] < 3:
            raise ValueError("nonce() unreadable at block 123")
        return 9
    assert coi._retry(flaky) == 9
    assert len(seen) == 2 and "nonce() unreadable at block 123" in seen[0]


# ------------------------------------------------------------------------------------------------------- G1.7 ADC / BQ
def test_g1_7_adc_check_never_waits_for_a_login(tmp_path):
    """run_2026-10-18.log stopped at "=== BigQuery check": the check runs in a child with no stdin and a timeout."""
    import adc_check
    hang = tmp_path / "hang.py"
    hang.write_text("import sys, time\nsys.stdin.read()\ntime.sleep(30)\n")
    t0 = time.monotonic()
    assert adc_check.guarded(timeout_s=2, argv0=str(hang)) == 9
    assert time.monotonic() - t0 < 15
    quick = tmp_path / "quick.py"
    quick.write_text("import sys\nprint('BIGQUERY CHECK: OK - stub')\nsys.exit(0 if sys.stdin.read() == '' else 5)\n")
    assert adc_check.guarded(timeout_s=20, argv0=str(quick)) == 0      # stdin is empty: no prompt can wait on it


def test_g1_7_login_needed_goes_to_the_top_of_the_run_log_and_the_gap_report_at_p2(tmp_path, monkeypatch):
    import build_workbook as bw
    from fetch import gaps
    from fetch.near_bigquery import login_needed
    rl = pd.DataFrame([{"ts": "t", "source": "near_bigquery", "project": "Near", "status": "failed", "tier": 1,
                        "rows": 0, "message": "NOT READ — Application Default Credentials need re-authentication"}])
    lines = bw.run_log_actions(rl)
    assert lines[0].startswith("ACTION NEEDED — NEAR BigQuery")
    assert "gcloud auth application-default login" in lines[1]
    assert "set-quota-project near-data-510309" in lines[2]
    assert bw.run_log_actions(rl.assign(source="other")) == []
    root = Path(os.environ["TOKEN_METRICS_LOGCACHE"])
    root.mkdir(parents=True, exist_ok=True)
    (root / "near-bq.json").write_text(json.dumps({"login_needed_on": "2026-10-18", "login_ok_on": "2026-10-16",
                                                   "reauth": {"dates": ["2026-10-16", "2026-10-18"]}}))
    assert login_needed()
    rows = gaps.detect([config.PROJECT_BY_NAME["Near"]], pd.DataFrame(columns=base.LONG_COLUMNS), set(), {}, [])
    row = [r for r in rows if r["metric"] == "[open] NEAR BigQuery login"]
    assert row and row[0]["priority_label"] == "P2 decision" and "set-quota-project" in row[0]["suggestion"]
    (root / "near-bq.json").write_text(json.dumps({"login_needed_on": "2026-10-16", "login_ok_on": "2026-10-18"}))
    rows = gaps.detect([config.PROJECT_BY_NAME["Near"]], pd.DataFrame(columns=base.LONG_COLUMNS), set(), {}, [])
    assert not [r for r in rows if r["metric"] == "[open] NEAR BigQuery login"]


def test_g1_7_after_a_login_lapse_the_first_good_run_tops_up_every_missed_day(tmp_path, monkeypatch):
    """Six days lapsed: the first good run's top-up covers all six (it is not the backfill held for quota)."""
    from fetch import near_bigquery as nbq
    monkeypatch.setattr(nbq, "today", lambda: pd.Timestamp("2026-10-18"))
    spec = json.loads(json.dumps(config.PROJECT_BY_NAME["Near"]["near_bigquery"], default=list))
    a = nbq.NearBigQuery(cache_file=tmp_path / "s.json", csv_dir=tmp_path / "none")
    ran = []
    monkeypatch.setattr(a, "_run", lambda client, label, *rest, **kw: ran.append(label) or [])
    held = {str(d.date()): {} for d in pd.date_range("2025-10-19", "2026-10-11")}
    st = {"days": held, "tokens": {"on": "2026-10-17", "list": ["wrap.near"]}, "ledger": {}}
    a._p2p(object(), spec, st, FetchOutput(), "Near", False)
    assert ran[0] == "p2p top-up 2026-10-12..2026-10-17"


# ----------------------------------------------------------------------------------------------------- G1.8 run lock
def test_g1_8_a_second_run_on_the_same_store_waits_then_exits_75(tmp_path):
    from runlock import RunLock, RunLocked
    db = tmp_path / "metrics.db"
    said = []
    with RunLock(db, "token_metrics run"):
        assert "token_metrics run" in (tmp_path / "metrics.db.lock.who").read_text()
        with pytest.raises(RunLocked) as e:
            with RunLock(db, "token_metrics run", wait_s=0.3, say=said.append, sleep=lambda s: time.sleep(0.05)):
                pass
        assert "RUN LOCKED" in str(e.value.code) and "pid" in str(e.value.code) and said
    with RunLock(db, "token_metrics run", wait_s=0):              # released: the next one runs
        pass
    assert "$run[0] -eq 75" in (ROOT / "windows" / "daily_run.ps1").read_text()
    assert "daily run FINAL: stopped early" in (ROOT / "windows" / "daily_run.ps1").read_text()


# ------------------------------------------------------------------------------------------------- G1.9 / G1.11 CLI
def test_g1_9_project_run_renders_the_whole_portfolio(monkeypatch, tmp_path):
    import token_metrics as tm
    monkeypatch.setattr(tm, "PORTFOLIO_TXT", tmp_path / "portfolio.txt")
    (tmp_path / "portfolio.txt").write_text("Maple\nFluid\n")
    log = logging.getLogger("t")
    args = SimpleNamespace(project=["Ether.fi"], all=False, portfolio=False)
    assert [p["name"] for p in tm.resolve_scope(args, log)] == ["Ether.fi"]
    assert [p["name"] for p in tm.render_scope(args, log)] == ["Maple", "Fluid", "Ether.fi"]
    args = SimpleNamespace(project=["Maple"], all=False, portfolio=False)
    assert [p["name"] for p in tm.render_scope(args, log)] == ["Maple", "Fluid"]


def test_g1_11_info_logging_goes_to_stdout():
    import inspect
    import archive_backfill
    import token_metrics as tm
    for mod in (tm, archive_backfill):
        assert "logging.basicConfig(level=logging.INFO, stream=sys.stdout" in inspect.getsource(mod)


# ------------------------------------------------------------------------------------------------- G2.1 price history
def test_g2_1_prices_are_filled_back_to_the_first_token_flow_never_over_a_stored_price(tmp_path, monkeypatch):
    """Ether.fi: 433 of 834 buyback days unvalued — no ETHFI price before 2025-09-12. Every day from the first token flow
    with no price_usd is filled from DefiLlama's coingecko:<id> chart (00:00 UTC points only); stored prices are never
    replaced; a past flow is still never valued at today's price."""
    from fetch import price_history as ph
    from fetch.logcache import DailyChecks
    monkeypatch.setattr(ph, "today", lambda: pd.Timestamp("2026-10-18"))
    monkeypatch.delenv("TOKEN_METRICS_DAILY_CHECKS", raising=False)
    rows = [("2024-07-06", "actual_buyback_tokens", 1000.0), ("2024-07-07", "actual_buyback_tokens", 0.0)]
    rows += [(str(d.date()), "price_usd", 1.0) for d in pd.date_range("2025-09-12", "2026-10-17")]
    stored = pd.DataFrame([{"date": d, "project": "Ether.fi", "metric": m, "value": v,
                            "source": "coingecko" if m == "price_usd" else "x", "tier": 1} for d, m, v in rows])
    days, first = ph.missing_price_days(stored, "Ether.fi")
    assert first == "2024-07-06" and days[0] == pd.Timestamp("2024-07-06") and days[-1] == pd.Timestamp("2025-09-11")
    assert len(days) == 433
    asked = []

    class H:
        def get(self, url, params=None, **kw):
            asked.append((url, dict(params)))
            t0 = params["start"]
            pts = [{"timestamp": t0 + i * 86400 + 60, "price": 2.0 + i} for i in range(params["span"])]
            if len(pts) > 1:
                pts[1]["timestamp"] += 3600               # one point an hour off 00:00: dropped
            return {"coins": {url.rsplit("/", 1)[1]: {"prices": pts}}}
    a = ph.PriceHistory(stored_long=stored, http=H(), daily=DailyChecks(tmp_path), robots=lambda u: (True, "404"))
    out = FetchOutput()
    a.run([config.PROJECT_BY_NAME["Ether.fi"]], None, out)
    f = out.frame()
    # G9 G1 (Jake 2026-10-20): DefiLlama coins by CONTRACT first (config.PRICE_HISTORY_KEYS), coingecko:<id> after
    assert all(p["span"] <= 499 for _, p in asked) and "ethereum:0xFe0c30065B384F05761f15d0CC899D4F9F9Cc0eB" in asked[0][0]
    first_key = [a for a in asked if "ethereum:0x" in a[0]]
    contract = f[f["source"].str.contains("ETHFI")]
    assert len(contract) == 433 - len(first_key)                # one dropped point per call on the contract key
    assert len(f) <= 433 and len(f) > len(contract)            # coingecko:<id> fills only what the contract key left
    assert f["date"].min() == pd.Timestamp("2024-07-06") and f["date"].max() <= pd.Timestamp("2025-09-11")
    # G2 addendum A1: the declared older leg of price_usd's handover, not a third measuring point
    assert set(f["source"]) == {"defillama_coins:history[coins.llama.fi ethereum:ETHFI 0xFe0c3006…]",
                                "defillama_coins:history[coins.llama.fi coingecko:ether-fi]"}
    # the next run the same day reads nothing
    out2 = FetchOutput()
    n_asked = len(asked)
    ph.PriceHistory(stored_long=stored, http=H(), daily=DailyChecks(tmp_path), robots=lambda u: (True, "")).run(
        [config.PROJECT_BY_NAME["Ether.fi"]], None, out2)
    assert out2.frame().empty and len(asked) == n_asked, "the next run the same day asks nothing"
    import fetch
    assert "price_history" in [n for n, _, _ in fetch.TIER_ORDER] and fetch._budget("price_history") == 120


# ------------------------------------------------------------------------------------------------ G2.2 FDV history
def _fdv_groups(project):
    import real_dump as rd
    df = rd.long_frame(rd.load(project))
    return {k: g.reset_index(drop=True) for k, g in df.groupby(["project", "metric"])}


@pytest.mark.parametrize("project", ["Uniswap", "Plume", "Near", "Ethereum"])
def test_g2_2_fdv_reaches_five_quarters_on_the_same_quantity(project, monkeypatch):
    """Jake's 10-13 dumps: CoinGecko's total (a snapshot) starts 2026-09-11, so FDV had one quarter. The same quantity
    measured on chain / first-party fills the days before it — FDV now spans five quarters, the basis bracket stays one
    (no series break), and the headline days are unchanged."""
    import build_workbook as bw
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[project]])
    g_off, g_on = _fdv_groups(project), _fdv_groups(project)
    monkeypatch.setattr(config, "FDV_TOTAL_HISTORY", {})
    bw._fdv_views(g_off)
    monkeypatch.undo()
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME[project]])
    bw._fdv_views(g_on)
    off, on = g_off[(project, "fdv_usd")], g_on[(project, "fdv_usd")]
    asof = pd.Timestamp("2026-10-13")
    quarters = lambda v: ((asof - pd.to_datetime(v["date"])).dt.days // 90).loc[lambda q: q < 5].nunique()  # noqa: E731
    assert quarters(off) == 1 and quarters(on) == 5
    import trajectory
    assert len({trajectory.basis_of("fdv_usd", s) for s in on["source"]}) == 1
    m = off.merge(on, on="date", suffixes=("_off", "_on"))
    assert (m["value_off"] == m["value_on"]).all(), "the days the primary covers are unchanged"
    assert "the same quantity" in str(on.sort_values("date")["source"].iloc[0])


def test_g2_2_fdv_history_refused_when_the_quantity_differs(monkeypatch):
    """GEODNET: CoinGecko's total became net of the burn on 2026-10-12 (−3.0% before) — fewer than 14 recent days agree,
    so nothing is extended and the note says why. G2-L3 retired GEODNET's entry (its total is the on-chain net itself);
    the refusal is kept, and tested here on the entry as it was."""
    import build_workbook as bw
    monkeypatch.setitem(config.FDV_TOTAL_HISTORY, "GEODNET", {
        "total": "total_supply_gross", "less": "burn_address_balance",
        "label": "GEOD.totalSupply() on Polygon − the burn balances (Polygon + Solana token account)"})
    g = _fdv_groups("GEODNET")
    s = lambda x: (x.assign(date=pd.to_datetime(x["date"]).dt.normalize()).drop_duplicates("date", keep="last")  # noqa: E731
                   .set_index("date")["value"].astype(float))
    note, alt = bw._fdv_history("GEODNET", g, s(g[("GEODNET", "total_supply")]), s)
    assert alt is None and "history NOT extended" in note and "median" in note


def test_g2_2_a_short_history_says_which_kind(monkeypatch):
    import build_workbook as bw
    p = config.PROJECT_BY_NAME["Hyperliquid"]
    data = {"Hyperliquid|total_supply_gross": {"first_date": "2026-09-24", "source": "hypercore_info:tokenDetails"},
            "Hyperliquid|price_usd": {"first_date": "2025-09-12", "source": "coingecko"}}
    t = bw._history_class(p, {"total_supply_gross", "price_usd"}, data)
    assert t.startswith("HISTORY FORWARD-ONLY: total_supply_gross from 2026-09-24")
    t = bw._history_class(config.PROJECT_BY_NAME["World Mobile"], {"fdv_usd"},
                          {"World Mobile|fdv_usd": {"first_date": "2026-09-11", "source": "coingecko"}})
    assert t.startswith("HISTORY SOURCE-LIMITED: fdv_usd from 2026-09-11")
    t = bw._history_class(config.PROJECT_BY_NAME["Uniswap"], {"fees_usd"},
                          {"Uniswap|fees_usd": {"first_date": "2026-07-20", "source": "defillama"}})
    assert t.startswith("HISTORY BUG (a route exists") and "455 days" in t


def test_g2_2_flows_status_depth_counts_verdict_quarters():
    import coverage_audit as ca
    fr = {"Statement": "Token flow", "Line": "Status", "Project": "X", "Q0": "OK — closes", "Q-1": "CHECK — x",
          "Q-2": "n/a — no series", "Q-3": None, "Q-4": "OK"}
    assert ca.flow_depth(fr) == 3
    assert ca.flow_depth({"Line": "Opening circulating", "Q0": 1.0, "Q-1": 2, "Q-2": None, "Q-3": "x"}) == 2


# ------------------------------------------------------------------------------------------- G2.6a nearblocks seed
def test_g2_6a_the_nearblocks_seed_line_names_what_it_reads():
    import inspect
    import token_metrics as tm
    src = inspect.getsource(tm.seed_nearblocks)
    assert 'or "no NEAR account flows configured"' not in src and '"no NearBlocks series configured"' in src
    assert "NearBlocks series: " in src and "account-txns inflow reads RETIRED" in src


# ---------------------------------------------------------------------------------------------------- G2.3 Sky references
def test_g2_3_a_dated_reference_reads_its_own_day_before_the_next(monkeypatch):
    """Jake's 18 Oct workbook: the Pause Proxy reference of 2026-10-13 (20.14M) was compared with the 10-14 row (21.53M:
    the SKY received in the 25 hours between) although the 10-13 row (20,142,230) was stored — likewise MKR (83,465.89 on
    10-13 = the reference; 83,170 on 10-14). The reference's own day is read first; the one-day lag only without it."""
    import references as rf
    # the generic "last" rule, on the two Sky references it was written for (both now compare at their block, A3)
    monkeypatch.setitem(rf.ROUTES, ("Sky", "pause_proxy_sky_balance"), ("last", "treasury_holding_tokens"))
    monkeypatch.setitem(rf.ROUTES, ("Sky", "mkr_supply_unconverted"), ("last", "mkr_outstanding_tokens"))
    rows = [("2026-10-12", "treasury_holding_tokens", 19_071_870.0), ("2026-10-13", "treasury_holding_tokens", 20_142_230.0),
            ("2026-10-14", "treasury_holding_tokens", 21_528_971.0),
            ("2026-10-13", "mkr_outstanding_tokens", 83_465.892107), ("2026-10-14", "mkr_outstanding_tokens", 83_170.0)]
    df = pd.DataFrame([{"date": pd.Timestamp(d), "project": "Sky", "metric": m, "value": v,
                        "source": "chain:ethereum:pause_proxy", "tier": 2} for d, m, v in rows])
    groups = {k: g for k, g in df.groupby(["project", "metric"])}
    refs = [r for r in rf.load() if r["project"] == "Sky" and r["metric"] in ("pause_proxy_sky_balance",
                                                                               "mkr_supply_unconverted")]
    got = {x["metric"]: x for x in rf.check(groups, rows=refs, projects={"Sky"})}
    assert got["pause_proxy_sky_balance"]["status"] == "VERIFIED", got["pause_proxy_sky_balance"]["detail"]
    assert got["mkr_supply_unconverted"]["status"] == "VERIFIED", got["mkr_supply_unconverted"]["detail"]
    # without a row of its own day, the next day's still stands in (the sample lag)
    groups2 = {k: g[g.date != pd.Timestamp("2026-10-13")] for k, g in groups.items()}
    got2 = {x["metric"]: x for x in rf.check(groups2, rows=refs, projects={"Sky"})}
    assert "2026-10-14" in got2["pause_proxy_sky_balance"]["route"]


def test_g2_3_vest_rate_history_from_the_award_records(tmp_path, monkeypatch):
    """emissions_rate_current_annual_tokens started 2026-10-12, so the vest-17 reference (2026-09-13, 580.8M/yr, executive
    2026-09-10) had nothing to check. The DssVest award records (read 2026-10-18) give each past day's rate in force at
    the day's close; only days before the series' first stored row are written."""
    from fetch import sky_mkr as sm
    from fetch.logcache import DailyChecks
    monkeypatch.delenv("TOKEN_METRICS_DAILY_CHECKS", raising=False)
    monkeypatch.setattr(sm, "today", lambda: pd.Timestamp("2026-10-18"))
    T = lambda s: int(pd.Timestamp(s, tz="UTC").timestamp())                       # noqa: E731
    awards = [{"id": 16, "bgn": T("2026-08-17 14:02:23"), "fin": T("2026-09-13 12:42:11"), "tot": 29_011_145.0},
              {"id": 17, "bgn": T("2026-09-13 12:42:11"), "fin": T("2026-10-12 14:01:47"), "tot": 46_232_885.0},
              {"id": 18, "bgn": T("2026-10-12 14:01:47"), "fin": T("2027-01-10 14:01:47"), "tot": 99_525_882.0}]
    for a in awards:
        a["annual"] = a["tot"] * sm.YEAR_S / (a["fin"] - a["bgn"])
    r = dict((str(d.date()), (round(v, -3), a["id"])) for d, v, a in
             sm.vest_rate_history(awards, pd.date_range("2026-09-12", "2026-09-14")))
    assert r == {"2026-09-12": (392_998_000, 16), "2026-09-13": (580_790_000, 17), "2026-09-14": (580_790_000, 17)}
    stored = pd.DataFrame([{"date": pd.Timestamp("2026-10-12"), "project": "Sky",
                            "metric": "emissions_rate_current_annual_tokens", "value": 4.0e8, "source": "x", "tier": 2}])
    a = sm.SkyMkr(stored_long=stored, daily=DailyChecks(tmp_path), awards_reader=lambda spec, upto: awards)
    out = FetchOutput()
    a._vest_history("Sky", config.PROJECT_BY_NAME["Sky"]["mkr_backing"], {"id": 18}, out)
    f = out.frame()
    assert f["date"].max() == pd.Timestamp("2026-10-11"), "never on or after the first stored row"
    assert f[f.date == pd.Timestamp("2026-09-13")]["value"].iloc[0] == pytest.approx(580_789_594, rel=1e-6)
    assert f["source"].str.endswith(":record").all()
    from fetch.base import _measuring_point
    assert {_measuring_point(x) for x in f["source"]} == {_measuring_point("sky_mkr:dss_vest.awards[vest 18: x]")}


def test_g2_3_burn_history_groups_held_events_by_utc_day_before_the_first_row(monkeypatch):
    """Sky's gross_burn_tokens started 2026-09-25; the 2026-09-13 spell burn (2,860,943.76) had nothing to check. The held
    burn events, grouped by each UTC day's first block, are the same quantity; only days before the first stored row
    minus one are written, on the live rows' measuring point."""
    from fetch import burn_history as bh
    from fetch.base import _measuring_point
    # one block per hour from 2026-09-01 00:00 UTC (block 1000)
    t0 = int(pd.Timestamp("2026-09-01", tz="UTC").timestamp())
    block_at = lambda ts: 1000 + (ts - t0) // 3600                                 # noqa: E731
    ev = [{"block": block_at(int(pd.Timestamp("2026-09-13 15:00", tz="UTC").timestamp())), "value": 2_860_943.76e18},
          {"block": block_at(int(pd.Timestamp("2026-09-12 23:59", tz="UTC").timestamp())), "value": 5e18},
          {"block": block_at(int(pd.Timestamp("2026-09-14 00:00", tz="UTC").timestamp())), "value": 7e18}]
    cache = {}
    frame, line = bh.history_rows(config.PROJECT_BY_NAME["Sky"], ev, pd.Timestamp("2026-09-25"), block_at, 1e18, cache,
                                  history_days=30)
    v = frame.set_index("date")["value"]
    assert v[pd.Timestamp("2026-09-13")] == pytest.approx(2_860_943.76)
    assert v[pd.Timestamp("2026-09-12")] == 5 and v[pd.Timestamp("2026-09-14")] == 7
    assert v[pd.Timestamp("2026-09-20")] == 0, "a day with no event is a measured zero"
    assert v.index.max() == pd.Timestamp("2026-09-23"), "the day before the first stored row is left out"
    assert v.index.min() == pd.Timestamp("2026-09-12"), "nothing before the first event's day"
    assert _measuring_point(frame["source"].iloc[0]) == _measuring_point("chain:ethereum:burn_logs[logs@1-2]:delta")
    assert len(cache) >= 12


# ------------------------------------------------------------------------------------------------- G2.4 NEAR intents
def test_g2_4_a_since_launch_series_keeps_its_whole_history(tmp_path):
    """The intents reference is since launch; the store kept 455 days (Σ from 2025-07-15). The series keeps every day its
    source serves, and once it reaches the first day the cause reads BASIS MISMATCH (window aligned), not a window."""
    from fetch.business import Business
    from fetch.base import window
    from fetch.logcache import DailyChecks
    import references as rf
    b = Business.__new__(Business)
    b.daily = DailyChecks(tmp_path)
    assert b._span_start("business:Near:near_intents_volume_usd") == pd.Timestamp("2000-01-01")
    assert b._span_start("business:Near:tx_count") > pd.Timestamp("2025-01-01")
    df = pd.DataFrame({"date": pd.date_range("2024-12-10", periods=700), "project": "Near",
                       "metric": "near_intents_volume_usd", "value": 1.0})
    assert len(window(df, 30)) == 700
    s = pd.DataFrame({"date": pd.date_range("2024-12-10", "2026-09-22"), "project": "Near",
                      "metric": "near_intents_volume_usd", "value": 1.0, "source": "x", "tier": 1})
    txt = rf._near_intents_cause({("Near", "near_intents_volume_usd"): s}, "Near", pd.Timestamp("2026-09-22"),
                                 30.69e9, 31.4e9)
    assert txt.startswith("BASIS MISMATCH, window aligned") and "29,867,500,840" in txt


# --------------------------------------------------------------------------------------------- G2.5 Hyperliquid ASXN
def test_g2_5_the_asxn_series_are_not_listed_as_not_wired():
    """browser_capture stores them (7,822 rows) and they feed the burn-history legs and cross-checks."""
    import coverage_audit as ca
    assert not [i for i, _ in ca.not_wired() if "ASXN" in i]
    p = config.PROJECT_BY_NAME["Hyperliquid"]
    caps = {c["metric"] for c in (p.get("browser_capture") or {}).get("captures", [])} if isinstance(
        p.get("browser_capture"), dict) else set()
    assert "buyback_hypercore_asxn_usd" in config.METRICS and (not caps or "buyback_hypercore_asxn_usd" in caps)
    # G2-D4 (Jake 2026-10-18): booked once the sender was matched to the docs' address — the note is closed
    q = next(q for q in config.OPEN_QUESTIONS if str(q.get("topic", "")).startswith("ANSWERED 2026-10-18 (G2-D4)"))
    assert q["status"] == "answered"
    aq = next(s for s in p["revenue_sources"] if s["name"].startswith("AQAv2"))
    assert aq["booked"] is True and "0x5000000000000000000000000000000000000000" in aq["documented_payer"]


# ------------------------------------------------------------------------------ G2.7 Base / BNB / Berachain transfers
_ALC_KEY = "AlcKey_0123456789abcdef"


class _AlchemyHttp:
    """Answers alchemy_getAssetTransfers from a list of pages; records each request body."""

    def __init__(self, pages, fail=None):
        self.pages, self.fail, self.bodies = list(pages), fail, []

    def post(self, url, json_body=None, headers=None, deadline=None):
        assert _ALC_KEY in url
        self.bodies.append(json_body)
        if self.fail:
            raise RuntimeError(f"403 Client Error for url: {url}")
        return {"jsonrpc": "2.0", "id": json_body["id"], "result": self.pages.pop(0)}


def _transfer(block, tx, idx, frm, to, raw, ts="2026-08-01T00:00:00.000Z"):
    return {"blockNum": hex(block), "uniqueId": f"{tx}:log:{idx}", "hash": tx, "from": frm, "to": to,
            "value": 1.0, "asset": "FLUID", "category": "erc20",
            "rawContract": {"value": raw, "address": "0x61e030a56d33e8260fdd81f03b162a79fe3449cd", "decimal": "0x12"},
            "metadata": {"blockTimestamp": ts}}


def test_g2_7_alchemy_transfers_are_returned_as_the_transfer_logs(monkeypatch):
    """Base had no free log route; the Transfers API answers one token from / to one address, paged by pageKey."""
    from fetch import explorer as ex
    monkeypatch.setenv("BASE_RPC_URL", f"https://base-mainnet.g.alchemy.com/v2/{_ALC_KEY}")
    monkeypatch.setitem(config.EXPLORER_TRANSFER_ROUTES, 8453, ["alchemy"])
    holder, tok = "0x94312a608246cecfce6811db84b3ef4b2619054e", "0x61E030A56D33e8260FdD81f03B162A79Fe3449Cd"
    http = _AlchemyHttp([
        {"transfers": [_transfer(100, "0xaa", 3, holder, "0x1111111111111111111111111111111111111111", "0x0de0b6b3a7640000")],
         "pageKey": "pk-2"},
        {"transfers": [_transfer(105, "0xbb", "0x0a", holder, "0x2222222222222222222222222222222222222222", "0x01")],
         "pageKey": ""}])
    logs, meta = ex.ExplorerLogs(http=http).get_logs(8453, tok, [ex.TRANSFER_TOPIC, ex.pad_address(holder), None],
                                                     0, 200)
    assert meta["explorer"] == "alchemy" and meta["requests"] == 2
    p0 = http.bodies[0]["params"][0]
    assert p0["fromAddress"] == holder and "toAddress" not in p0 and p0["contractAddresses"] == [tok]
    assert p0["category"] == ["erc20"] and p0["toBlock"] == hex(200) and p0["withMetadata"] is True
    assert http.bodies[1]["params"][0]["pageKey"] == "pk-2"
    a, b = logs
    assert a["topics"] == [ex.TRANSFER_TOPIC, ex.pad_address(holder),
                           ex.pad_address("0x1111111111111111111111111111111111111111")]
    assert a["data"] == "0x0de0b6b3a7640000" and a["blockNumber"] == 100 and a["logIndex"] == 3
    assert a["timeStamp"] == int(pd.Timestamp("2026-08-01", tz="UTC").timestamp())
    assert b["logIndex"] == 10, "a hex log index in uniqueId is read as hex"


def test_g2_7_alchemy_serves_only_transfer_queries_and_never_the_zero_address(monkeypatch):
    from fetch import explorer as ex
    monkeypatch.setenv("BASE_RPC_URL", f"https://base-mainnet.g.alchemy.com/v2/{_ALC_KEY}")
    monkeypatch.setitem(config.EXPLORER_TRANSFER_ROUTES, 8453, ["alchemy"])
    e = ex.ExplorerLogs(http=_AlchemyHttp([]))
    tok = "0x61E030A56D33e8260FdD81f03B162A79Fe3449Cd"
    # a burn filter (to = 0x0) is Alchemy's own default for "unset": not routed there at all
    with pytest.raises(ex.ExplorerRefused, match="no explorer is routed"):
        e.get_logs(8453, tok, [ex.TRANSFER_TOPIC, None, ex.pad_address("0x0")])
    with pytest.raises(ex.ExplorerRefused, match="no explorer is routed"):
        e.get_logs(8453, tok, ["0x" + "ab" * 32])
    with pytest.raises(ex.ExplorerRefused, match="ERC-20 Transfer queries"):
        e._alchemy_paged("alchemy", 8453, None, [ex.TRANSFER_TOPIC, ex.pad_address("0x01"), None], 0, "latest")


def test_g2_7_alchemy_errors_never_carry_the_key(monkeypatch):
    from fetch import explorer as ex
    monkeypatch.setenv("BASE_RPC_URL", f"https://base-mainnet.g.alchemy.com/v2/{_ALC_KEY}")
    monkeypatch.setitem(config.EXPLORER_TRANSFER_ROUTES, 8453, ["alchemy"])
    e = ex.ExplorerLogs(http=_AlchemyHttp([], fail=True))
    with pytest.raises(ex.ExplorerRefused) as got:
        e.get_logs(8453, "0x61E030A56D33e8260FdD81f03B162A79Fe3449Cd",
                   [ex.TRANSFER_TOPIC, ex.pad_address("0x94312a608246cecfce6811db84b3ef4b2619054e"), None])
    assert _ALC_KEY not in str(got.value) and "base-mainnet.g.alchemy.com" in str(got.value)
    # the derived URL for another chain uses the same key, and is scrubbed the same way
    from fetch.chain import alchemy_endpoint
    assert alchemy_endpoint("berachain") == f"https://berachain-mainnet.g.alchemy.com/v2/{_ALC_KEY}"
    assert _ALC_KEY not in e._scrub(f"boom {alchemy_endpoint('bsc')}")


def test_g2_7_the_alchemy_probe_opens_the_transfer_route_and_wires_the_base_scans(monkeypatch):
    import copy
    saved = {n: copy.deepcopy(config.PROJECT_BY_NAME[n].get("log_scans")) for n in ("Fluid", "Pendle")}
    em, fb = config.PROJECT_BY_NAME["Pendle"].get("emissions_from_metric"), config.OUTFLOW_CATEGORIES["Fluid"]["b"]
    routes, troutes = copy.deepcopy(config.EXPLORER_LOG_ROUTES), copy.deepcopy(config.EXPLORER_TRANSFER_ROUTES)
    try:
        wired = config.wire_probed_scans({"served": [], "alchemy": {"probed_on": "2026-10-18", "served": [8453]}})
        assert set(wired) == {"gauge_pendle_out_base", "merkle_rewards_out_base"}
        assert config.EXPLORER_TRANSFER_ROUTES[8453] == ["alchemy"] and config.EXPLORER_LOG_ROUTES.get(8453) == []
        sp = next(s for s in config.PROJECT_BY_NAME["Fluid"]["log_scans"] if s["key"] == "merkle_rewards_out_base")
        assert "Alchemy Transfers API chain 8453" in sp["route"] and sp["wired_on"] == "2026-10-18"
        assert sp["window_days"] == 100, "the window is stored only when balanceOf's change = in − out to the wei"
        assert "emissions_tokens_base" in config.OUTFLOW_CATEGORIES["Fluid"]["b"]
        assert "emissions_tokens_gauge_base" in config.PROJECT_BY_NAME["Pendle"]["emissions_from_metric"]
    finally:
        for n, v in saved.items():
            config.PROJECT_BY_NAME[n]["log_scans"] = v
        config.PROJECT_BY_NAME["Pendle"]["emissions_from_metric"] = em
        config.OUTFLOW_CATEGORIES["Fluid"]["b"] = fb
        config.EXPLORER_LOG_ROUTES.clear()
        config.EXPLORER_LOG_ROUTES.update(routes)
        config.EXPLORER_TRANSFER_ROUTES.clear()
        config.EXPLORER_TRANSFER_ROUTES.update(troutes)


def test_g2_7_a_declared_outflow_gap_lifts_only_when_its_scans_are_stored_over_q0():
    full = lambda m: 90.0  # noqa: E731
    assert config.outflow_incomplete("Fluid", ["b"], q0_days=full) == []
    part = config.outflow_incomplete("Fluid", ["b"], q0_days=lambda m: 90.0 if m.endswith("_base") else None)
    assert len(part) == 1 and "STILL NOT STORED OVER THE WHOLE Q0" in part[0] and "emissions_tokens_plasma: not stored" \
        in part[0] and "(stored: emissions_tokens_base)" in part[0]
    short = config.outflow_incomplete("Pendle", ["b"], q0_days=lambda m: 40.0)
    assert "40 of 90 Q0 days" in short[0]
    assert config.outflow_incomplete("Fluid", ["b"]), "without data to hand the declared gap holds"
    assert config.outflow_incomplete("Aethir", ["b"], q0_days=full), "a time-based gap has nothing that lifts it"


def test_g2_7_fluids_incentive_denominator_includes_the_arbitrum_claims():
    """(b) held the mainnet claims only while the declared gap called it 'Ethereum + Arbitrum'."""
    assert config.outflow_metrics("Fluid", "b")[:2] == ("emissions_tokens", "emissions_tokens_arbitrum")


# --------------------------------------------------------------------------------------- G2.8 references not wired
def test_g2_8_the_four_unwired_references_now_have_routes():
    import references as rf
    at = pd.Timestamp("2026-10-13")
    v, how, why = rf._our_value(rf.ROUTES[("Aerodrome", "team_rate")], {}, "Aerodrome", at, 228)
    assert v == 228 and "rate_by_flip" in how and not why
    v, how, _ = rf._our_value(rf.ROUTES[("Maple", "buyback_top_tier")], {}, "Maple", at, 0.30)
    assert v == 0.30 and "fee_tiers" in how
    trace = pd.DataFrame({"date": pd.to_datetime(["2026-07-15", "2026-08-19", "2026-09-02"]), "project": "Aerodrome",
                          "metric": "foundation_buyback_trace_tokens", "value": [1.0, 2.0, 40.0], "source": "t",
                          "tier": 3})
    v, _, _ = rf._our_value(rf.ROUTES[("Aerodrome", "momentum_fund_purchases_trace_span")],
                            {("Aerodrome", "foundation_buyback_trace_tokens"): trace}, "Aerodrome", at, 3.0)
    assert v == 3.0, "the trace span sums 2026-07-15..2026-08-24 only"
    late = trace.assign(metric="foundation_aero_purchased_tokens", date=pd.to_datetime(
        ["2026-08-30", "2026-09-02", "2026-09-23"]))
    v, _, why = rf._our_value(rf.ROUTES[("Aerodrome", "momentum_fund_cumulative")],
                              {("Aerodrome", "foundation_aero_purchased_tokens"): late}, "Aerodrome", at, 1.1e8)
    assert v is None and "after 2023-08-31" in why, "the lifetime total is compared only over a lifetime series"
    for w in ("governance_multisig", "team_multisig", "ecosystem_fund"):
        assert rf.ROUTES[("Pendle", f"{w}_balance")] == ("last", f"noncirc_{w}_tokens")
    assert not [k for k, r in rf.ROUTES.items() if r[0] == "not_wired"
                and k in {("Aerodrome", "team_rate"), ("Aerodrome", "momentum_fund_cumulative"),
                          ("Pendle", "team_multisig_balance"), ("Maple", "buyback_top_tier")}]


def test_g2_8_a_component_metric_is_stored_beside_the_sum():
    """Pendle's three wallets fed one sum; each balanceOf now also lands on its own metric (the same read)."""
    from fetch.chain import Chain
    oc = Chain.__new__(Chain)
    oc.backfill, oc.prior, oc.prior_delta = None, {}, {}
    out = FetchOutput()
    p = config.PROJECT_BY_NAME["Pendle"]
    parts = {"noncirculating_holding_tokens": [("ethereum:noncirc_team_multisig", 7_770_000.0),
                                               ("ethereum:noncirc_ecosystem_fund", 16_120_000.0)]}
    oc._emit_parts(p, parts, set(), {}, pd.Timestamp("2026-10-13"), out)
    f = out.frame().set_index("metric")["value"]
    assert f["noncirculating_holding_tokens"] == 23_890_000.0
    assert f["noncirc_team_multisig_tokens"] == 7_770_000.0 and f["noncirc_ecosystem_fund_tokens"] == 16_120_000.0


def test_g2_3_the_vest_history_needs_the_store(tmp_path):
    """Without stored_long there is no 'before the first row' — no award read, nothing written (the 6f8c86c test ran
    the history over the network and its frame held 351 rate rows)."""
    from fetch import sky_mkr as sm
    from fetch.logcache import DailyChecks
    called = []
    a = sm.SkyMkr(daily=DailyChecks(tmp_path), awards_reader=lambda spec, upto: called.append(1) or [])
    out = FetchOutput()
    a._vest_history("Sky", config.PROJECT_BY_NAME["Sky"]["mkr_backing"], {"id": 18}, out)
    assert not called and out.frame().empty


# ------------------------------------------------------------------------------------- G2.9 Aethir veAethir reconcile
def _reconcile_run(monkeypatch, capsys, holders, balances, total, wrapped):
    import check_offline_items as coi
    pages = [{"items": [{"address": {"hash": h}} for h in holders[:2]], "next_page_params": {"p": 2}},
             {"items": [{"address": {"hash": h}} for h in holders[2:]], "next_page_params": None}]

    class R:
        def __init__(self, j):
            self.j = j

        def raise_for_status(self):
            pass

        def json(self):
            return self.j
    monkeypatch.setattr(coi.requests, "get", lambda *a, **k: R(pages.pop(0)))
    monkeypatch.setattr(coi, "_rpcs_for", lambda chain: ["https://rpc.example"])
    monkeypatch.setattr(coi, "rpc", lambda url, method, params=None: {"result": hex(1000)})
    monkeypatch.setattr(coi, "_code", lambda a, c: "no code")
    pools = [v for _, v in coi.AETHIR_VE_POOLS.items()]
    fixed = [total, wrapped, balances[pools[0]], balances[pools[1]], balances[pools[0]], balances[pools[1]]]

    def batch(chain, calls, chunk=200, block="latest"):
        assert block == hex(995), "every read at ONE pinned block"
        return [hex(v) for v in fixed] + [hex(balances[h]) for h in holders]
    monkeypatch.setattr(coi, "_batch_eth_calls", batch)
    monkeypatch.setattr("time.sleep", lambda s: None)
    coi.aethir_veaethir_reconcile()
    return capsys.readouterr().out


def test_g2_9_veaethir_is_reconciled_holder_by_holder_at_one_block(monkeypatch, capsys):
    import check_offline_items as coi
    gp, ap = coi.AETHIR_VE_POOLS["Gaming Pool"], next(v for k, v in coi.AETHIR_VE_POOLS.items() if k != "Gaming Pool")
    e1, e2 = "0x" + "c0" * 20, "0x" + "16" * 20
    bal = {gp: 373 * 10**18, ap: 419 * 10**18, e1: 14 * 10**18, e2: 2 * 10**18}
    out = _reconcile_run(monkeypatch, capsys, [ap, gp, e1, e2], bal, 808 * 10**18, 808 * 10**18)
    assert "RECONCILED TO THE WEI" in out and "LOCKED in the two pools       792.000000" in out
    assert "OUTSIDE the pools             16.000000" in out
    out = _reconcile_run(monkeypatch, capsys, [ap, gp, e1, e2], bal, 809 * 10**18, 809 * 10**18)
    assert "NOT reconciled" in out, "a listing that does not add up to totalSupply is said so, never rounded away"


def test_g2_9_the_peg_is_judged_every_run_to_the_token():
    row = next(v for k, v in vars(config).items() if isinstance(v, dict) and "Aethir" in v
               and isinstance(v["Aethir"], dict) and "in_locked_wrapper_peg" in v["Aethir"])["Aethir"][
        "in_locked_wrapper_peg"]
    assert row["ref"]["tol"] == 0.0 and row["ref"]["args"]["b"] == ("veaethir_total_supply_tokens",)
    c = config.PROJECT_BY_NAME["Aethir"]["contracts"]["veaethir_supply"]
    assert c["metric_override"] == "veaethir_total_supply_tokens" and c["address"].lower().startswith("0x1b49f587")


# ------------------------------------------------------------------------------------ G2.10 Aero launch switch file
_AERO_REAL = {"minter": "0x5f1c9a3e7b2d48c06e9f31a7b5d2c8e4f0a6b913",
              "voting_escrow": "0x8e2d7c4b1a9f36e05d8c27b4a1f9e6d3c0b5a872",
              "token": "0x3a7f9c1e5b8d24f06a3c9e7b1d5f82a4c6e0b391"}


def _aero_file(**kw):
    st = {"launched": True, "verified": "2026-10-22", "source_url": "https://github.com/Dromos-Labs/deployments",
          "addresses": dict(_AERO_REAL)}
    st.update(kw)
    return st


def test_g2_10_the_switch_file_refuses_every_placeholder():
    from fetch.validate import _migration_switch_missing as miss
    day = pd.Timestamp("2026-10-23")
    assert miss(_aero_file(), day) == []
    assert miss(_aero_file(voting_rewards_factory="0x7c3e9a1f5d2b48e06c9a3f7e1b5d82c4a6f0e193", deploy_block=37_000_000),
                day) == []
    cases = {
        "zero address": dict(addresses=dict(_AERO_REAL, token="0x" + "0" * 40)),
        "repeated pattern": dict(addresses=dict(_AERO_REAL, minter="0x" + "01" * 20)),
        "bad checksum": dict(addresses=dict(_AERO_REAL, minter="0xeB018363F0a9Af8f91F06FEe6613a751b2A33FE6")),
        "duplicate": dict(addresses=dict(_AERO_REAL, token=_AERO_REAL["minter"])),
        "template factory": dict(voting_rewards_factory="0x…", deploy_block=0),
        "factory without block": dict(voting_rewards_factory="0x7c3e9a1f5d2b48e06c9a3f7e1b5d82c4a6f0e193"),
        "block 0": dict(voting_rewards_factory="0x7c3e9a1f5d2b48e06c9a3f7e1b5d82c4a6f0e193", deploy_block=0),
        "template url": dict(source_url="<the page or repo file the addresses came from>"),
        "verified before launch": dict(verified="2026-10-14"),
        "verified in the future": dict(verified="2026-10-30"),
    }
    for what, kw in cases.items():
        assert miss(_aero_file(**kw), day), what


def test_g2_10_no_read_from_an_address_without_code(monkeypatch):
    from fetch import aero_v3 as a
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-10-23"))
    seen = []

    def rpc(method, params):
        seen.append(method)
        if method == "eth_getCode":
            return "0x" if params[0] == _AERO_REAL["token"] else "0x6080"
        raise AssertionError("no eth_call before every address holds code")
    out = FetchOutput()
    a.AeroV3(rpc=rpc, state=_aero_file()).run([], 30, out)
    assert out.frame().empty and set(seen) == {"eth_getCode"}
    assert "no contract code on Base at token" in out.log[-1].message


def test_g2_10_the_v3_reader_asks_the_keyed_base_endpoint_first(monkeypatch):
    from fetch import aero_v3 as a
    monkeypatch.setenv("BASE_RPC_URL", "https://base-mainnet.g.alchemy.com/v2/KEYG210abcdef")
    asked = []

    class H:
        def post(self, url, json_body=None, **_):
            asked.append(url)
            return {"result": "0x1"}
    r = a.AeroV3(state=_aero_file())
    r.http = H()
    assert r.rpc("eth_blockNumber", []) == "0x1" and asked[0].startswith("https://base-mainnet.g.alchemy.com/")


def test_g2_10_the_migration_watch_ends_when_v3_rows_arrive_without_a_config_edit():
    import build_workbook as bw
    assert bw.migration_watch_on(pd.Timestamp("2026-10-23"), groups={})
    g = pd.DataFrame({"date": pd.to_datetime(["2026-10-23"]), "project": "Aerodrome", "metric": "locked_tokens",
                      "value": [1.0], "source": ["aero_v3:token.balanceOf(sAERO)"], "tier": [2]})
    assert bw.v3_rows_since({("Aerodrome", "locked_tokens"): g}) == "2026-10-23"
    assert not bw.migration_watch_on(pd.Timestamp("2026-10-23"), groups={("Aerodrome", "locked_tokens"): g})


# ----------------------------------------------------------------------------------------- G2.11 insider share
def test_g2_11_each_not_wired_insider_cell_names_what_was_checked_and_what_is_needed():
    for n, r in config.INSIDER_SHARE_ROUTES.items():
        assert set(r) >= {"stake", "checked", "docs", "needed", "cell"} and len(r["cell"]) <= 240, n
        assert r["cell"].startswith(config.INSIDER_LIST_NEEDED), n                    # G17-2a: MISSING E, not NOT WIRED
    assert "block 26,219,782" in config.INSIDER_SHARE_ROUTES["Chainlink"]["checked"]


def test_g2_11_leakage_i_is_zero_when_nothing_was_distributed():
    """(i) = insider share x distributed / credited: an all-retired quarter is 0 whatever the share; a distributing one
    still needs the share and names the route."""
    import build_workbook as bw
    cols = bw._cp_cols(bw.Refs(10, 10, ["2026-09"]), {})
    build = next(f for h, f, *_ in cols if h.startswith("Leakage (i)"))
    f = build(5, config.PROJECT_BY_NAME["Sky"])
    assert f.startswith("=IF(NOT(ISNUMBER(") and "<=0,0," in f and "Pause Proxy" in f


# ------------------------------------------------------------------------------- G2.6 NEAR all-time wallet inflows
def test_g2_6_the_wallet_inflow_sql_is_dry_run_by_near_settlement_routes():
    sql = (ROOT / "sql" / "near" / "bigquery_wallet_inflows.sql").read_text(encoding="utf-8")
    for w in ("fefundsadmin.sputnik-dao.near", "buybacks.multisignature.near", "1csfundsadmin.sputnik-dao.near"):
        assert sql.count(w) == 2, w                       # native receipts and wNEAR ft_events
    assert "@d0" in sql and "@d1" in sql and "block_date BETWEEN @d0 AND @d1" in sql
    src = (ROOT / "check_offline_items.py").read_text(encoding="utf-8")
    assert '"wallet_inflows_alltime": ("bigquery_wallet_inflows.sql"' in src
    import references as rf
    for k in ("fefundsadmin_alltime_inflow", "buybacks_multisig_alltime_inflow"):
        r = rf.ROUTES.get(("Near", k))
        # G8 4c: wired to BigQuery stage 2's answer (route near_inflow), the account named
        assert r and r[0] == "near_inflow" and r[1] == {"fefundsadmin_alltime_inflow": "fefundsadmin.sputnik-dao.near",
                                                        "buybacks_multisig_alltime_inflow":
                                                            "buybacks.multisignature.near"}[k], k


# ------------------------------------------------------------------------------- G3 verification coverage references
def test_g3_a_reference_dated_after_the_data_is_not_judged_against_older_rows():
    """Jake's 10-13 dumps against a 2026-10-18 read: a five-day-old stock is not today's (AERO supply grows ~0.04% a
    day). N/A BY DESIGN until a build holds the day; a reference within the sample lag is judged as before."""
    import references as rf
    g = {("X", "m"): pd.DataFrame({"date": pd.to_datetime(["2026-10-12", "2026-10-13"]), "project": "X",
                                   "metric": "m", "value": [99.0, 100.0], "source": "s", "tier": 2})}
    base = {"project": "X", "unit": "t", "source_type": "primary (x)", "source": "", "tolerance": "0.1%",
            "note": ""}
    rf.ROUTES[("X", "late")] = ("last", "m")
    rf.ROUTES[("X", "lag")] = ("last", "m")
    try:
        late, lag = rf.check(g, rows=[base | {"metric": "late", "value": "105", "as_of": "2026-10-18"},
                                      base | {"metric": "lag", "value": "100", "as_of": "2026-10-14"}])
    finally:
        rf.ROUTES.pop(("X", "late"))
        rf.ROUTES.pop(("X", "lag"))
    assert late["status"] == "N/A BY DESIGN" and "after this build's data (latest row 2026-10-13)" in late["detail"]
    assert lag["status"] == "VERIFIED"


def test_g3_the_phase_3_references_are_dated_primary_reads_with_routes():
    import references as rf
    new = [r for r in rf.load() if r["as_of"] == "2026-10-18" and "phase 3" in r["note"]]
    assert len(new) >= 14
    for r in new:
        assert rf.role(r["source_type"]) == "primary", r["metric"]
        assert "block" in r["source"] or "read 2026-10-18" in r["source"], r["metric"]
        assert (rf.project_of(r), r["metric"]) in rf.ROUTES, r["metric"]


def test_g2_8_the_archive_fills_a_component_without_writing_a_partial_sum():
    """The per-wallet references are dated 2026-10-13; the component series start with the next run, so the archive
    backfill fills their history. A day whose sum is stored reads only the wanted component and never writes a sum."""
    from fetch import archive as ar
    from fetch.chain import Chain
    p = config.PROJECT_BY_NAME["Pendle"]
    assert "noncirc_team_multisig_tokens" in ar.state_metrics(p)
    assert [k for k, _ in ar.serving_contracts(p, "noncirc_team_multisig_tokens")] == ["noncirc_team_multisig"]
    oc = Chain.__new__(Chain)
    oc.backfill = {"day": pd.Timestamp("2026-10-13"), "metrics": {"Pendle": {"noncirc_team_multisig_tokens"}}}
    oc.prior, oc.prior_delta = {}, {}
    out = FetchOutput()
    oc._emit_parts(p, {"noncirculating_holding_tokens": [("ethereum:noncirc_team_multisig", 7_770_000.0)]}, set(), {},
                   pd.Timestamp("2026-10-13"), out)
    f = out.frame()
    assert list(f["metric"]) == ["noncirc_team_multisig_tokens"] and "archive" in f["source"].iloc[0]
