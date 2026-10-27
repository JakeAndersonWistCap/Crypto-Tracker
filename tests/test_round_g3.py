"""
tests/test_round_g3.py — Round G3 (Jake 2026-10-18 evening), SPEC §19 "G3 — Round G3, old → new". One test (or more) per
item, each named for its row.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pandas as pd
import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402

KEY = "abcDEF0123456789_-xyz9876"           # a well-formed stand-in, never a real key


@pytest.fixture
def local_only(monkeypatch):
    """A test talking to 127.0.0.1 must not be sent through the sandbox's proxy."""
    for k in ("HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


def _hanging_server():
    """Accepts connections and never answers — rpc.katana.network on Jake's run of 2026-10-18."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(64)
    conns = []

    def accept():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            conns.append(c)
    threading.Thread(target=accept, daemon=True).start()
    return srv, conns, f"http://127.0.0.1:{srv.getsockname()[1]}"


# ------------------------------------------------------------------------------------------------- item 1: stalls
def test_g3_1a_one_tier_per_chain_with_its_own_budget():
    import fetch
    tiers = [t for t, _, _ in fetch.TIER_ORDER if t.startswith(config.L2_TIER_PREFIX)]
    assert "explorer_gauge_l2" not in [t for t, _, _ in fetch.TIER_ORDER]
    # G6 0a: one tier per chain AND project (explorer_l2:<chain>@<Project>), each with its chain's budget
    assert {"explorer_l2:katana@Morpho", "explorer_l2:arbitrum@Pendle", "explorer_l2:optimism@Pendle"} <= set(tiers)
    assert fetch.TIER_BUDGET_S["explorer_l2:katana@Morpho"] == 300
    assert fetch.TIER_BUDGET_S["explorer_l2:arbitrum@Pendle"] == 240
    # every L2 scan declares its chain's tier (the config remap of the old shared tier)
    for p in config.PROJECTS:
        for sc in p.get("log_scans") or ():
            if str(sc.get("own_tier", "")).startswith(config.L2_TIER_PREFIX):
                assert sc["own_tier"] == config.l2_tier(sc["chain"]) + config.PROJECT_TIER_SEP + p["name"], sc["key"]


def test_g3_1b_a_hanging_host_times_out_once_per_call_and_stalls_after_three(local_only, monkeypatch):
    from fetch import chain as ch
    srv, conns, url = _hanging_server()
    try:
        monkeypatch.setattr(ch, "rpc_endpoints", lambda c: [url])
        ch.BREAKER.reset()
        w3 = ch.ChainReader.make_web3("katana", url, 0.3)
        assert w3.provider.exception_retry_configuration is None, "web3's own retry (5x on Timeout) is off"
        for i in range(3):
            t0 = time.monotonic()
            with pytest.raises(requests.exceptions.Timeout):
                w3.eth.block_number
            assert time.monotonic() - t0 < 2.0
            assert len(conns) == i + 1, "one connection per call: a timeout is never retried"
        assert ch.BREAKER.chain_stalled("katana"), "three consecutive timeouts stall the host (the chain's only one)"
        t0 = time.monotonic()
        with pytest.raises(ch.ChainStalled):
            w3.eth.block_number
        assert time.monotonic() - t0 < 0.1 and len(conns) == 3, "a stalled host is not asked again"
    finally:
        srv.close()
        ch.BREAKER.reset()


def test_g3_1e_with_katana_hanging_every_other_chains_series_lands(local_only, monkeypatch, tmp_path):
    """The done-when of item 1: through the real dispatcher, a Katana tier whose RPC hangs and a Base tier that produces.
    Base's series lands; Katana's are gapped; ONE FAILED line names the stalled chain and the series it gapped."""
    import fetch
    from fetch import chain as ch
    from fetch.logscan import LogScan
    srv, conns, url = _hanging_server()
    try:
        real = ch.rpc_endpoints
        monkeypatch.setattr(ch, "rpc_endpoints", lambda c: [url] if c == "katana" else real(c))
        monkeypatch.setenv("TOKEN_METRICS_RPC_READ_TIMEOUT", "0.3")
        monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "logscan"))
        monkeypatch.setenv("TOKEN_METRICS_DAILY_CHECKS", "always")
        ch.BREAKER.reset()
        morpho = config.PROJECT_BY_NAME["Morpho"]
        katana = next(s for s in morpho["log_scans"] if s["key"] == "merkl_morpho_out_katana")
        base = {**katana, "key": "merkl_morpho_out_base_test", "chain": "base", "metric": "emissions_tokens_claims_base",
                "own_tier": config.l2_tier("base"), "rpc_window": None}
        proj = {**morpho, "log_scans": [katana, base]}

        def base_scan(self, p, spec, w, out):
            out.add(pd.DataFrame({"date": [pd.Timestamp("2026-10-17")], "project": p["name"], "metric": spec["metric"],
                                  "value": [123.0], "source": f"explorer:{spec['key']}", "tier": 2}),
                    "explorer", p["name"], f"{spec['metric']}: stub", 2)

        class BaseScan(LogScan):
            _scan = base_scan

        kt = katana["own_tier"]                       # G6 0a: explorer_l2:katana@Morpho
        todo = [(kt, 2, lambda ctx: LogScan(own_tier=kt)),
                (config.l2_tier("base"), 2, lambda ctx: BaseScan(own_tier=config.l2_tier("base")))]
        t0 = time.monotonic()
        results = fetch._dispatch(todo, [proj], None, {})
        assert time.monotonic() - t0 < 60, "the stall costs seconds, not the tier's whole budget"
        by = {name: (sub, timed_out) for name, _, sub, _, timed_out in results}
        kat, kat_to = by[kt]
        bas, bas_to = by[config.l2_tier("base")]
        assert not kat_to and not bas_to, "neither tier timed out"
        f = bas.frame()
        assert list(f.metric) == ["emissions_tokens_claims_base"] and f.value.iloc[0] == 123.0, "Base's series landed"
        msgs = [e.message for e in kat.log if e.status == "failed"]
        stalled = [m for m in msgs if m.startswith("CHAIN STALLED THIS RUN: katana")]
        assert len(stalled) == 1 and "Morpho/emissions_tokens_claims_katana" in stalled[0], msgs
        assert {(g["project"], g["metric"]) for g in kat.gaps} >= {("Morpho", "emissions_tokens_claims_katana")}
        assert kat.frame().empty
        assert url not in " ".join(msgs) or "127.0.0.1" in " ".join(msgs)
    finally:
        srv.close()
        ch.BREAKER.reset()


def test_g3_1d_katana_seed_saves_every_chunk_reconciles_and_stores(tmp_path, monkeypatch):
    from fetch import katana_seed as ks
    from fetch.explorer import TRANSFER_TOPIC, pad_address
    _, spec = ks._spec()
    holder = spec["holders"][0].lower()
    recip = "0x" + "ab" * 20

    def ev(block, frm, to, amount, i):
        return {"blockNumber": block, "transactionHash": "0x" + f"{block:064x}", "logIndex": i,
                "topics": [TRANSFER_TOPIC, pad_address(frm), pad_address(to)], "data": hex(amount)}
    ins = [ev(100, "0x" + "11" * 20, holder, 10 * 10 ** 18, 0)]
    outs = [ev(1_500_000, holder, recip, 3 * 10 ** 18, 0), ev(2_600_000, holder, recip, 2 * 10 ** 18, 0)]

    class Reader:
        log_endpoint_used = {"katana": "rpc.katana.network"}
        stop_at = None

        def __init__(self, bal):
            self.bal, self.calls = bal, []

        def block_number(self, chain):
            return 3_000_020

        def checksum(self, a):
            return a

        def _get_logs_resilient(self, chain, base, lo, hi, chunk):
            if self.stop_at is not None and lo >= self.stop_at:
                raise RuntimeError("connection reset")
            up = min(lo + chunk - 1, hi)
            self.calls.append((lo, up))
            src = ins if base["topics"][2] is not None else outs
            return [e for e in src if lo <= e["blockNumber"] <= up], up, chunk

        def erc20(self, chain, token):
            outer = self

            class F:
                def balanceOf(self, h):
                    return type("C", (), {"call": lambda s, block_identifier=None: outer.bal})()

                def decimals(self):
                    return type("C", (), {"call": lambda s: 18})()
            return type("E", (), {"functions": F()})()

        def block_timestamp(self, chain, b):
            return int(pd.Timestamp("2026-07-01").timestamp()) + b // 100_000 * 86_400

    path = tmp_path / "seed.json"
    r = Reader(5 * 10 ** 18)
    r.stop_at = 2_000_000
    out = FetchOutput()
    res = ks.run(r, out, path=path, chunk=1_000_000)
    assert res["status"] == "stopped" and json.loads(path.read_text())["scanned_to"] == 1_999_999, \
        "stopped mid-way: state saved to the last chunk read"
    r.stop_at = None
    out = FetchOutput()
    res = ks.run(r, out, path=path, chunk=1_000_000)
    assert res["status"] == "stored" and r.calls[-1][1] == 3_000_000, "resumed at the next block, read to the head"
    f = out.frame()
    assert round(f.value.sum(), 6) == 5.0 and (f.source == f"explorer:{ks.KEY}").all()
    assert "RECONCILED to the wei" in out.log[-1].message
    # NOT reconciled -> nothing stored, the numbers named
    path2 = tmp_path / "seed2.json"
    out = FetchOutput()
    res = ks.run(Reader(6 * 10 ** 18), out, path=path2, chunk=1_000_000)
    assert res["status"] == "unreconciled" and out.frame().empty and "does NOT reconcile" in out.log[-1].message


def test_g3_1d_bracketed_dating_matches_one_call_per_block():
    from fetch.katana_seed import date_blocks
    blocks = sorted({b for b in range(0, 5_000, 7)})
    ts = lambda b: 1_750_000_000 + b * 37  # noqa: E731
    asked = []

    def counting(b):
        asked.append(b)
        return ts(b)
    got = date_blocks(blocks, counting)
    assert got == {b: pd.Timestamp(ts(b), unit="s").normalize() for b in blocks}
    assert len(set(asked)) < len(blocks) / 4, "far fewer timestamp reads than blocks"


# ---------------------------------------------------------------------------------------- item 2: the stake bound
def _hl_stake(prior):
    from fetch.hypercore import HyperCoreInfo
    from test_adapters import _InfoStub, _validator
    hl = config.PROJECT_BY_NAME["Hyperliquid"]
    hc = HyperCoreInfo(prior_values=prior, prior_dates={}, prior_delta={})
    # 442,115,644.7157 staked, as on Jake's run; the Assistance Fund read THIS run (48,007,021.50)
    hc.http = _InfoStub([_validator(30_000_000_000_000_000), _validator(14_211_564_471_570_000 - 1_528)],
                        balances=[{"coin": "HYPE", "total": "48007021.50"}])
    out = FetchOutput()
    hc.run([hl], None, out)
    return out


def test_g3_2_hyperliquid_stake_is_bounded_by_the_net_total_never_by_circulating():
    legs = {("Hyperliquid", "total_supply_gross"): 998_862_059.40,
            ("Hyperliquid", "burn_address_balance"): 48_007_021.50,
            ("Hyperliquid", "hyperevm_zero_address_balance"): 308_538.17,
            ("Hyperliquid", "circulating_supply"): 222_445_714.0}
    out = _hl_stake(legs)
    f = out.frame()
    locked = f[f.metric == "locked_tokens"]
    assert len(locked) == 1 and round(float(locked.value.iloc[0]), 4) == 442_115_644.7157
    line = next(e.message for e in out.log if e.message.startswith("locked_tokens="))
    assert ("net total=950,546,499.73 (total_supply_gross 998,862,059.40 [stored] − burn_address_balance "
            "48,007,021.50 [this run] − hyperevm_zero_address_balance 308,538.17 [stored])") in line, line
    assert "222,445,714" not in line
    # a leg missing -> no bound, nothing stored, named; circulating is never the stand-in
    out = _hl_stake({k: v for k, v in legs.items() if k[1] != "hyperevm_zero_address_balance"})
    assert out.frame().query("metric == 'locked_tokens'").empty
    msg = " ".join(e.message for e in out.log if e.status == "failed")
    assert "the bound cannot be formed: no hyperevm_zero_address_balance read this run or stored" in msg


# ---------------------------------------------------------------------------------- item 3: hyperevm_burn reach
def test_g3_3_hyperevm_burn_reaches_the_earliest_supply_row_and_further():
    from fetch import hyperevm_burn as hb
    long = pd.DataFrame({"project": "Hyperliquid",
                         "metric": ["circulating_supply", "circulating_supply_first_party",
                                    "hyperevm_zero_address_balance"],
                         "date": pd.to_datetime(["2025-10-20", "2026-09-30", "2025-10-21"]), "value": 1.0})
    days = hb.days_needed(long, "Hyperliquid", "hyperevm_zero_address_balance")
    assert days[0] == pd.Timestamp("2025-10-20") and pd.Timestamp("2025-10-21") not in days, \
        "from CoinGecko's earliest circulating row, skipping days already held"
    more = hb.days_needed(long, "Hyperliquid", "hyperevm_zero_address_balance", extra_days=600)
    # G14 0: was `< 2025-03-01`, a fixed date — false from 2026-10-22, when today − 600 days IS 2025-03-01. The claim is
    # the reach: exactly 600 days back from today, further than the earliest supply row
    from fetch.base import today
    assert more[0] == today().normalize() - pd.Timedelta(days=600) and more[0] < days[0]

    # a day before HyperEVM's first block is skipped and named, never read as 0
    def rpc(m, p):
        if m == "eth_getBlockByNumber":
            return {"timestamp": hex(int(pd.Timestamp("2025-02-18").timestamp()) + int(p[0], 16))}
        if m == "eth_blockNumber":
            return hex(10_000_000)
        return hex(5 * 10 ** 18)
    got, failed = hb.history_rows("Hyperliquid", {"address": "0x" + "0" * 40}, [pd.Timestamp("2025-01-01")], rpc)
    assert not got and "before HyperEVM's first block" in failed[0][1]


def test_g3_3_an_endpoint_that_ignores_the_block_is_refused_for_history():
    from fetch import hyperevm_burn as hb

    def latest_only(m, p):
        return hex(100) if m == "eth_blockNumber" else hex(308_538 * 10 ** 18)
    assert "ignores the block" in hb.serves_past_state(latest_only, "0x" + "0" * 40)

    def archive(m, p):
        if m == "eth_blockNumber":
            return hex(100)
        return hex(0 if p[1] == hex(1) else 308_538 * 10 ** 18)
    assert hb.serves_past_state(archive, "0x" + "0" * 40) is None


def test_g3_3_alchemy_is_the_first_hyperevm_archive_endpoint(monkeypatch):
    from fetch import hyperevm_burn as hb
    read = next(r for r in config.PROJECT_BY_NAME["Hyperliquid"]["node_api"]["extra_reads"]
                if r.get("subtract_from_supply"))
    monkeypatch.setenv("ALCHEMY_API_KEY", KEY)
    urls = hb.archive_urls(read)
    assert urls[0] == f"https://hyperliquid-mainnet.g.alchemy.com/v2/{KEY}" and "https://rpc.purroofgroup.com" in urls
    monkeypatch.delenv("ALCHEMY_API_KEY")
    for k in [k for k in list(__import__("os").environ) if k.endswith("_RPC_URL")]:
        monkeypatch.delenv(k, raising=False)
    assert hb.archive_urls(read)[0] == "https://rpc.purroofgroup.com"


def _store(rows):
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE metrics (date TEXT, project TEXT, metric TEXT, value REAL, source TEXT, tier INTEGER, "
                "fetched_at TEXT, PRIMARY KEY (date, project, metric))")
    con.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,?,'2026-10-18T10:00:00Z')", rows)
    return con


def _section(label):
    import run_sql
    secs = run_sql.parse_sections((ROOT / "orphan_cleanup.sql").read_text(encoding="utf-8"))
    s = secs[label]
    assert s["authored"] and pd.Timestamp(s["authored"].rstrip("Z")) <= pd.Timestamp.now("UTC").tz_localize(None)
    assert not [ln for ln in s["text"].splitlines() if ln.lstrip().upper().startswith(("UPDATE", "DELETE"))], \
        "every write ships commented"
    selects = [x for x in run_sql.split_statements(run_sql.strip_comments(s["text"])) if x.strip()]
    writes = [w for w in run_sql.split_statements("\n".join(run_sql.uncommented_write(s["text"])))
              if run_sql.classify(w) == "write"]
    return run_sql, selects, writes


def _write(con, run_sql, writes):
    for w in writes:
        _verb, table, where = run_sql.write_target(w)
        con.execute(f"SELECT * FROM {table} WHERE {where}").fetchall()      # the preview runs
        con.execute(run_sql.strip_comments(w))


def test_g3_3_run_sql_cx_three_dry_runs_and_the_restatement():
    rows = [("2026-09-30", "Hyperliquid", "circulating_supply_first_party", 302e6,
             "hypercore_info:tokenDetails.circulatingSupply", 1),
            ("2026-09-30", "Hyperliquid", "hyperevm_zero_address_balance", 300e3, "x", 1),
            ("2026-10-12", "Hyperliquid", "circulating_supply_first_party", 238.9e6,
             "hypercore_info:tokenDetails.circulatingSupply-exclude_wallets", 1),
            ("2026-10-12", "Hyperliquid", "hyperevm_zero_address_balance", 308e3, "x", 1),
            ("2025-10-01", "Hyperliquid", "circulating_supply", 330e6, "coingecko", 1)]
    con = _store(rows)
    run_sql, selects, writes = _section("CX")
    assert len(selects) == 3 and len(writes) == 2
    cx3 = con.execute(selects[2]).fetchall()
    assert [r[0] for r in cx3] == ["2025-10-01"], "CX3: the CoinGecko day with no burn row"
    _write(con, run_sql, writes)
    got = dict(con.execute("SELECT date, value FROM metrics WHERE metric='circulating_supply_first_party'").fetchall())
    assert got == {"2026-09-30": 301_700_000.0, "2026-10-12": 238_592_000.0}
    assert con.execute("SELECT value FROM metrics WHERE metric='circulating_supply'").fetchone()[0] == 330e6, \
        "CoinGecko's circulating is never restated"


def test_g3_7_run_sql_cy_lists_and_removes_the_multi_day_new_users_rows():
    con = _store([("2026-10-18", "Hyperliquid", "hypercore_new_users", 6565,
                   "browser_capture:asxn.total_users[HyperCore, daily change, span=2d]", 3),
                  ("2026-10-16", "Hyperliquid", "hypercore_new_users", 3100,
                   "browser_capture:asxn.total_users[HyperCore, daily change]", 3)])
    run_sql, selects, writes = _section("CY")
    assert len(con.execute(selects[0]).fetchall()) == 1
    _write(con, run_sql, writes)
    assert con.execute("SELECT COUNT(*) FROM metrics WHERE metric='hypercore_new_users'").fetchone()[0] == 1


# ---------------------------------------------------------------------------- item 4: the full-history daily key
def test_g3_4_a_full_history_series_has_its_own_daily_mark(tmp_path, monkeypatch):
    from fetch.business import Business
    from fetch.logcache import DailyChecks
    monkeypatch.delenv("TOKEN_METRICS_DAILY_CHECKS", raising=False)
    daily = DailyChecks(tmp_path)
    today = str(pd.Timestamp.now(tz="UTC").date())
    daily.done("business:Near:near_intents_volume_usd", today)        # the windowed read's mark, as on 10-18
    b = Business(daily=daily)
    seen = []
    from fetch.business import last_complete
    # the read reaches the last complete day: a shorter read is stored and NOT marked (G5 item 4, test_round_g5)
    b._series = lambda name, spec, key: (seen.append(key) or ([(pd.Timestamp("2025-01-01"), 1.0),
                                                                (pd.Timestamp(last_complete()), 1.0)], "s", "d"))
    near = config.PROJECT_BY_NAME["Near"]
    out = FetchOutput()
    b.run([near], None, out)
    assert "business:Near:near_intents_volume_usd" in seen, "the full history ran despite the windowed mark"
    assert daily.get("business:Near:near_intents_volume_usd:full-history") == today
    seen.clear()
    b.run([near], None, FetchOutput())
    assert "business:Near:near_intents_volume_usd" not in seen, "and once a day after that"


# --------------------------------------------------------------------------------- item 5: deferred daily marks
def test_g3_5_daily_marks_wait_for_the_store_and_an_abandoned_tier_leaves_none(tmp_path, monkeypatch):
    from fetch.logcache import DailyChecks
    monkeypatch.delenv("TOKEN_METRICS_DAILY_CHECKS", raising=False)
    d = DailyChecks(tmp_path)
    DailyChecks.defer()
    try:
        d.done("filing_managed_lock_tokens", "2026-10-18")
        assert not d.due("filing_managed_lock_tokens", "2026-10-18"), "inside the run it counts"
        assert not d.f.exists(), "but nothing is on disk before the store write"

        def abandoned():
            d.done("ve_managed:Aerodrome:x", "2026-10-18")
        t = threading.Thread(target=abandoned, name="ve_managed")
        t.start()
        t.join()
        assert DailyChecks.discard_tier("ve_managed") == 1
        t = threading.Thread(target=abandoned, name="ve_managed")        # still running after its cut-off
        t.start()
        t.join()
        assert DailyChecks.commit() == 1
    finally:
        DailyChecks.commit()
    state = json.loads(d.f.read_text())
    assert state == {"filing_managed_lock_tokens": "2026-10-18"}
    # a killed run (no commit) writes nothing
    d2 = DailyChecks(tmp_path / "b")
    DailyChecks.defer()
    d2.done("k", "2026-10-18")
    DailyChecks._defer = {"on": False, "marks": [], "dropped": set()}  # the process dies here
    assert not d2.f.exists()


def test_g3_5_the_mcap_message_names_both_dates_and_the_implied_price():
    import sanity
    assert "MCAP_MEASURING_POINT" in dir(config) and "Aerodrome" in config.MCAP_MEASURING_POINT
    src = Path(sanity.__file__).read_text()
    assert "implies price" in src and "_data_dates" in src and "TOL_MCAP = 0.005" in src, "the rule is unchanged"


# ------------------------------------------------------------------------------------- item 6: NEAR in November
def test_g3_6_near_stage1_never_runs_in_october(monkeypatch, capsys):
    import check_offline_items as coi
    w = config.NEAR_STAGE1_WINDOW
    assert w["from"] == "2026-11-01" and w["to"] == "2026-11-07"
    rc = coi.near_wallet_inflow_days()
    out = capsys.readouterr().out
    if pd.Timestamp.now(tz="UTC").tz_localize(None) < pd.Timestamp("2026-11-01"):
        assert rc == 0 and "NOT RUN: today" in out
    src = Path(coi.__file__).read_text()
    body = src[src.index("def near_wallet_inflow_days"):src.index("def near_wallet_inflows_stage2")]
    assert "bigquery_wallet_inflows.sql" not in body, "stage 1 never runs the inflow query itself"
    assert "backfill=True" in body, "the existing top-up reserve rule (NearBigQuery._run backfill)"
    # G8 4c: stage 2 (stage 1's days only) is reached only from the DONE branch, under the same rule
    done_branch = body[body.index("if done_file.exists():"):body.index("day = pd.Timestamp.now")]
    assert "near_wallet_inflows_stage2(done_file)" in done_branch
    s2 = src[src.index("def near_wallet_inflows_stage2"):src.index("def worldmobile_probe")]
    assert "backfill=True" in s2 and "IN UNNEST(@days)" in (ROOT / "sql" / "near" / "bigquery_wallet_inflows.sql").read_text()
    assert "near_wallet_inflow_days" in (ROOT / "windows" / "daily_run.ps1").read_text()


# -------------------------------------------------------------------------------------- item 7: new users span
def test_g3_7_only_a_one_day_change_is_stored_as_new_users():
    src = Path(ROOT / "fetch" / "browser_capture.py").read_text()
    assert "if span == 1:" in src and "span={span}d" not in src.split("if span == 1:")[1].split("else:")[0]


# -------------------------------------------------------------------------------------------- item 8: Sky burns
def test_g3_8_the_stage2_line_names_each_burn_and_checks_it_against_the_record():
    from fetch.chain import Chain
    cfg = config.PROJECT_BY_NAME["Sky"]["contracts"]["burn_logs"]["burn_logs"]
    on_file = cfg["pause_proxy_burns_on_file"]
    assert [r.get("tx") for r in on_file[1:]] == [
        "0xcd57535f7a9e60e61d34b31f793501fa6178b4ae37fe0b2900f76efd54e192c3",
        "0x93389082b75a9e278b6b2a2d7dde53618c38214b5d71699fd60461dbe1e10275"]
    pp = cfg["stage2_burner"]["address"]

    class R:
        def block_timestamp(self, chain, b):
            return {25_968_583: int(pd.Timestamp("2026-09-13 12:42:11").timestamp()),
                    26_176_859: int(pd.Timestamp("2026-10-12 14:01:47").timestamp()),
                    26_200_000: int(pd.Timestamp("2026-10-16").timestamp())}[b]
    c = Chain.__new__(Chain)
    c.reader = R()
    events = [{"from": pp, "value": 2_860_943_760_000_000_000_000_000, "block": 25_968_583},
              {"from": pp, "value": 7_372_288 * 10 ** 18, "block": 26_176_859}]
    out = FetchOutput()
    parts = __import__("collections").defaultdict(list)
    split = {**cfg["stage2_split"], "_on_file": on_file}
    c._stage2_split(config.PROJECT_BY_NAME["Sky"], "ethereum", "burn_logs", split, pp, events, 18, 26_200_000,
                    parts, {}, "logs@x", out)
    line = next(e.message for e in out.log if e.message.startswith("sky_stage2_burn_tokens:"))
    assert line.count("= the verified burn on file") == 2 and "0xcd57535f7a9e60e61d34b31f793501fa6178b4ae37fe0b2900f76efd54e192c3" in line
    assert "0x93389082b75a9e278b6b2a2d7dde53618c38214b5d71699fd60461dbe1e10275" in line
    f = out.frame().set_index("date")["value"]
    assert f[pd.Timestamp("2026-09-13")] == 2_860_943.76 and f[pd.Timestamp("2026-10-12")] == 7_372_288.0
    assert f.sum() == 10_233_231.76
    # an amount that differs from the record by one wei is flagged, not passed
    events[0]["value"] -= 1
    out = FetchOutput()
    c._stage2_split(config.PROJECT_BY_NAME["Sky"], "ethereum", "burn_logs", split, pp, events, 18, 26_200_000,
                    __import__("collections").defaultdict(list), {}, "logs@x", out)
    assert "— CHECK" in next(e.message for e in out.log if e.message.startswith("sky_stage2_burn_tokens:"))


def test_g3_8_no_double_count_between_the_buyback_and_the_burn():
    # the buyback column is the flapper scan's; the split relabel never writes beside it
    assert config.log_scan_declared("Sky", "actual_buyback_tokens")
    # a Pause Proxy burn is never a release: Sky has no unlock stock legs
    assert not config.unlock_stock_metrics("Sky")
    # no outflow category holds a burn metric
    cats = config.OUTFLOW_CATEGORIES["Sky"]
    assert not any("burn" in m for c in "abcde" for m in cats.get(c, ()))
    assert config.a1_burn_metric("Sky") == config.a4_burn_metric("Sky") == "sky_stage2_burn_tokens"


# ---------------------------------------------------------------------------------------- item 9: aero_migration
def test_g3_9_aero_migration_check_exits_nonzero_until_complete(monkeypatch, capsys):
    import check_offline_items as coi
    monkeypatch.setattr(config, "aerodrome_migration_state", lambda: {})
    assert coi.aero_migration() == 1 and "ABSENT" in capsys.readouterr().out
    st = {"launched": True, "verified": "2026-10-22", "source_url": "https://github.com/dromos-labs/x",
          "addresses": {"minter": "0x" + "1" * 40, "voting_escrow": "0x" + "2" * 40, "token": "0x" + "3" * 40}}
    monkeypatch.setattr(config, "aerodrome_migration_state", lambda: st)
    rc = coi.aero_migration()
    out = capsys.readouterr().out
    assert rc == 1 and "voting_rewards_factory is missing" in out and "deploy_block is missing" in out
    monkeypatch.setattr(sys, "argv", ["check_offline_items.py", "aero_migration"])
    assert coi.main() == 1, "run alone, the process exits non-zero"
    assert "aero_migration" in (ROOT / "RUNBOOK.md").read_text()


# ----------------------------------------------------------------------------------- items 10 / 12: Alchemy
def test_g3_10_12_hosts_one_key_overrides_and_validation(monkeypatch):
    from fetch import chain as ch
    for net in ("opt-mainnet", "bnb-mainnet", "berachain-mainnet", "hyperliquid-mainnet", "plasma-mainnet",
                "sonic-mainnet", "mantle-mainnet", "solana-mainnet", "tron-mainnet", "worldmobilechain-mainnet"):
        assert net in ch.ALCHEMY_HOSTS.values(), net
    assert "katana" not in ch.ALCHEMY_HOSTS and "plume" not in ch.ALCHEMY_HOSTS
    for k in [k for k in list(__import__("os").environ) if k.endswith("_RPC_URL") or k.startswith("RPC_")]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALCHEMY_API_KEY", KEY)
    assert ch.rpc_endpoints("mantle")[0] == f"https://mantle-mainnet.g.alchemy.com/v2/{KEY}", "Alchemy first"
    assert ch.rpc_endpoints("katana")[0] == "https://rpc.katana.network", "Katana stays on its own endpoint"
    monkeypatch.setenv("MANTLE_RPC_URL", "https://my.mantle.example")
    assert ch.rpc_endpoints("mantle")[0] == "https://my.mantle.example", "<CHAIN>_RPC_URL still wins"
    monkeypatch.setenv("RPC_MANTLE", "https://only.example")
    assert ch.rpc_endpoints("mantle") == ["https://only.example"], "RPC_<CHAIN> still replaces"
    for bad, why in (("https://eth-mainnet.g.alchemy.com/v2/" + KEY, "'/'"), (KEY + " ", "whitespace"),
                     ("<your key>", "placeholder"), ("short", "16-64")):
        monkeypatch.setenv("ALCHEMY_API_KEY", bad)
        msg = ch.alchemy_key_problem()
        assert msg and why in msg and bad.strip() not in msg, (bad, msg)
        assert ch.alchemy_url("ethereum") == ""
    monkeypatch.setenv("ALCHEMY_API_KEY", KEY)
    assert ch.alchemy_key_problem() is None
    assert KEY not in ch.redact_urls(f"error for url: https://eth-mainnet.g.alchemy.com/v2/{KEY} key={KEY}")


def test_g3_12_a_malformed_key_stops_the_run_without_printing_it(monkeypatch, caplog):
    import token_metrics as tm
    monkeypatch.setenv("ALCHEMY_API_KEY", "<paste here>")
    monkeypatch.setattr(tm, "load_dotenv", lambda *a, **k: None)
    import logging
    args = tm.parse_args(["--no-fetch"])
    with caplog.at_level(logging.ERROR):
        rc = tm._main(args, logging.getLogger("token_metrics"))
    assert rc == 2 and "not a valid Alchemy key" in caplog.text and "<paste here>" not in caplog.text


def test_g3_10_usage_is_counted_per_method_and_warns_near_the_allowance(tmp_path, monkeypatch):
    from fetch import alchemy_usage as au
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "logscan"))
    au.reset()
    au.record(f"https://eth-mainnet.g.alchemy.com/v2/{KEY}", "eth_getLogs")
    au.record(f"https://eth-mainnet.g.alchemy.com/v2/{KEY}", "eth_call")
    au.record("https://mainnet.gateway.tenderly.co", "eth_call")           # not Alchemy: not counted
    line, level = au.report("2026-10")
    assert "2 request(s), ~86 CU" in line and level is None and KEY not in line
    (tmp_path / "alchemy_usage.json").write_text(json.dumps({"2026-10": {"cu": 24_500_000, "requests": 1}}))
    au.record(f"https://base-mainnet.g.alchemy.com/v2/{KEY}", "eth_call")
    assert au.report("2026-10")[1] == "WARN"
    (tmp_path / "alchemy_usage.json").write_text(json.dumps({"2026-10": {"cu": 28_600_000, "requests": 1}}))
    assert au.report("2026-10")[1] == "FAIL"


def test_g3_10_web3_and_http_requests_to_alchemy_are_counted(local_only, monkeypatch):
    from fetch import alchemy_usage as au
    from fetch import chain as ch
    au.reset()
    prov = ch._guarded_provider_class(__import__("web3").HTTPProvider)
    p = prov(f"https://eth-mainnet.g.alchemy.com/v2/{KEY}", exception_retry_configuration=None)
    p.chain = "ethereum"
    monkeypatch.setattr(p._request_session_manager, "make_post_request", lambda *a, **k: b'{"jsonrpc":"2.0","id":1,"result":"0x1"}')
    p._make_request("eth_blockNumber", b"{}")
    assert au.run_counts() == {"eth_blockNumber": 1}
    au.reset()


# --------------------------------------------------------------------------------- item 11: getLogs plan limits
class _Plan:
    """Alchemy free tier first (10-block plan limit, with its hint), then a public route that serves wide ranges."""

    def __init__(self):
        self.calls = []

    def provider_for(self, url):
        outer = self

        class Eth:
            block_number = 30_000_000

            def get_logs(self, params):
                span = params["toBlock"] - params["fromBlock"] + 1
                outer.calls.append((url, span))
                if "alchemy" in url and span > 10:
                    e = Exception("400 Client Error: Bad Request for url: " + url)
                    e.response = type("R", (), {"text": '{"error":{"code":-32600,"message":"Under the Free tier plan, '
                                                        'you can make eth_getLogs requests with up to a 10 block range. '
                                                        'Based on your parameters, this block range should work: '
                                                        '[0x1901823, 0x190182c]. Upgrade your plan"}}'})()
                    raise e
                return [{"blockNumber": params["fromBlock"]}]
        w3 = type("W3", (), {})()
        w3.eth = Eth()
        w3.provider = type("P", (), {"endpoint_uri": url})()
        return w3


def test_g3_11_a_plan_limit_costs_no_call_and_the_scan_falls_through(monkeypatch):
    import types
    from fetch import chain as ch
    al = f"https://eth-mainnet.g.alchemy.com/v2/{KEY}"
    urls = [al, "https://ethereum-rpc.publicnode.com"]
    monkeypatch.setattr(ch, "rpc_endpoints", lambda c: list(urls))
    monkeypatch.setattr(config, "LOG_RPC", {"ethereum": ["https://mainnet.gateway.tenderly.co"]})
    stub = _Plan()
    fake = types.ModuleType("web3")
    fake.Web3 = lambda provider, **kw: stub.provider_for(provider)
    fake.HTTPProvider = lambda url, **kw: url
    monkeypatch.setitem(sys.modules, "web3", fake)
    r = ch.ChainReader()
    r._w3["ethereum"] = stub.provider_for(al)
    r._w3_url["ethereum"] = al
    logs, chunks, used = r.scan_logs("ethereum", {"address": "0xabc", "topics": []}, 0, 99_999, 50_000, rpc_only=True)
    al_calls = [c for c in stub.calls if "alchemy" in c[0]]
    # D4-2a: G3 cost ONE refused call; Alchemy's free-plan cap (ALCHEMY_GETLOGS_MAX_RANGE) is now known, so a span it
    # cannot serve is never sent to it
    assert len(al_calls) == 0, f"no call on Alchemy for a span its plan refuses: {stub.calls}"
    assert r.no_getlogs("ethereum", al).startswith("its plan serves 10 blocks per call")
    assert {c[0] for c in stub.calls if "alchemy" not in c[0]} == {"https://mainnet.gateway.tenderly.co"}, \
        "the public log route served it, before the public list"
    assert chunks == 2 and used == 50_000
    assert KEY not in " ".join(r.log_endpoints_refused.get("ethereum", []) + r.log_range_errors.get("ethereum", [])
                               + [r.no_getlogs("ethereum", al)])
    # and the next scan this run never asks Alchemy for logs again
    stub.calls.clear()
    r.scan_logs("ethereum", {"address": "0xabc", "topics": []}, 0, 9_999, 50_000, rpc_only=True)
    assert not [c for c in stub.calls if "alchemy" in c[0]]


def test_g3_11_the_stated_range_is_read_not_halved_towards():
    from fetch.chain import ChainReader as R
    for msg, want in (("Max range: 800", 800), ("query exceeds max block range 100000", 100_000),
                      ("ranges over 10000 blocks are not supported", 10_000), ("block range exceeds 10000 blocks", 10_000),
                      ("this block range should work: [0x1901823, 0x190182c]", 10), ("nothing stated", None)):
        assert R.log_range_hint(msg) == want, msg


# -------------------------------------------------------------------------------------- item 13: key audit
def test_g3_13_env_keys_prints_presence_never_values(monkeypatch, capsys):
    import check_offline_items as coi
    monkeypatch.setenv("ETHERSCAN_API_KEY", "SECRET_ETHERSCAN_VALUE")
    monkeypatch.delenv("BLOCKSCOUT_API_KEY", raising=False)
    coi.env_keys()
    out = capsys.readouterr().out
    assert "SECRET_ETHERSCAN_VALUE" not in out
    assert "ETHERSCAN_API_KEY    PRESENT" in out and "BLOCKSCOUT_API_KEY   ABSENT" in out and "disables:" in out
    names = {k for k, *_ in config.ENV_KEYS}
    assert names >= {"ALCHEMY_API_KEY", "ETHERSCAN_API_KEY", "BLOCKSCOUT_API_KEY", "NEARBLOCKS_API_KEY",
                     "BEACONCHAIN_API_KEY", "ARTEMIS_API_KEY", "DUNE_API_KEY", "COINGECKO_API_KEY"}
    assert config.env_keys_line().startswith("KEYS present: ")


def test_g3_13_an_absent_key_is_a_config_gap_not_a_failure():
    from fetch.base import LogEntry, run_class
    src = (ROOT / "fetch" / "chainlink_fees.py").read_text()
    assert "CONFIG GAP" in src and "(missing - optional) <= nokey[base]" in src
    assert "CONFIG GAP — DUNE_API_KEY not set" in (ROOT / "fetch" / "dune.py").read_text()
    assert run_class(LogEntry("chainlink_fees", "Chainlink", 0, "unconfigured", "x: CONFIG GAP", 2)) == "UNCONFIGURED"
    assert "config.env_keys_line()" in (ROOT / "token_metrics.py").read_text()


# ---------------------------------------------------------------------------------- item 14: the probe's file
def test_g3_14_the_probe_never_clobbers_on_not_tried(tmp_path, monkeypatch, capsys):
    import check_offline_items as coi
    path = tmp_path / "explorer_routes.json"
    prior = {"alchemy": {"probed_on": "2026-10-18", "served": [56, 8453, 80094, 10], "refused": {}}}
    path.write_text(json.dumps(prior))
    monkeypatch.setattr(coi.os.path, "dirname", lambda p: str(tmp_path))
    for k in [k for k in list(coi.os.environ) if k.endswith("_RPC_URL") or k == "ALCHEMY_API_KEY"]:
        monkeypatch.delenv(k, raising=False)
    coi.alchemy_transfer_routes()
    out = capsys.readouterr().out
    assert "NOT WRITTEN" in out and json.loads(path.read_text()) == prior, "every chain NOT TRIED: file untouched"
    # with a key: one chain NOT SERVED (an answer), one unreachable (NOT TRIED), one SERVED
    monkeypatch.setenv("ALCHEMY_API_KEY", KEY)

    class Resp:
        def __init__(self, j):
            self.j = j

        def json(self):
            return self.j

    def post(url, json=None, timeout=None):
        if "bnb-mainnet" in url:
            return Resp({"error": {"code": -32600, "message": "BNB Mainnet is not enabled for this app"}})
        if "berachain" in url:
            raise requests.exceptions.ConnectTimeout("timed out")
        return Resp({"result": {"transfers": [{"blockNum": "0x10", "hash": "0xabc"}]}})
    monkeypatch.setattr(coi.requests, "post", post)
    coi.alchemy_transfer_routes()
    got = json.loads(path.read_text())["alchemy"]
    assert 56 not in got["served"], "a real NOT SERVED removes it"
    assert 80094 in got["served"] and "80094" in got["not_tried"], "NOT TRIED keeps what the last probe found"
    assert KEY not in capsys.readouterr().out
