"""ROUND G18 (Jake 2026-10-26, run on 4ebc061) — close the last wiring gaps.

1 NEAR's lockup rule never falls back unflagged; 2 Plume's claims scan by campaign over the RPC; 3 Sky (d) classed;
4 Sky's mints matched to spells; 5 NEAR's indexer total in Credibility; 6 the RWA turn's own tier and class C;
7 the "(all)" insider row; 8 Pendle's Berachain seed resumes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import coverage_audit as ca  # noqa: E402


def _g(project, metric, days, values, source="x"):
    days = list(days)
    vals = values if isinstance(values, (list, tuple)) else [values] * len(days)
    return pd.DataFrame({"date": pd.to_datetime(days), "project": project, "metric": metric,
                         "value": [float(v) for v in vals], "tier": 1, "source": source, "is_manual": False,
                         "entered_on": "", "fetched_at": ""})


# ================================================================================================== 1 NEAR
def _near_groups(read_on="2026-10-11", base_to="2026-10-22", to="2026-10-26"):
    d = pd.date_range("2026-10-01", to)
    return {("Near", "circulating_supply_first_party"): _g("Near", "circulating_supply_first_party",
                                                           pd.date_range("2026-10-01", base_to), 1_250_000_000.0,
                                                           "near_bigquery:circulating_supply"),
            ("Near", "total_supply"): _g("Near", "total_supply", d, 1_306_900_000.0, "coingecko"),
            ("Near", "circulating_supply"): _g("Near", "circulating_supply", d, 1_280_000_000.0, "coingecko"),
            ("Near", "price_usd"): _g("Near", "price_usd", d, 2.0, "coingecko"),
            ("Near", "near_lockup_locked_tokens"): _g("Near", "near_lockup_locked_tokens", [read_on], 2_058_522.0,
                                                      "manual"),
            ("Near", "near_foundation_accounts_tokens"): _g("Near", "near_foundation_accounts_tokens", d, 171_059.0,
                                                            "near_rpc:view_account")}


def test_1_a_stale_lockup_read_blocks_the_headline_and_coingecko_never_stands_in(monkeypatch):
    """Jake's 10-26 run: the only lockup read was 10-11, so from 10-22 the rule could not form; 10-22 took NEAR's own
    figure and 10-26 CoinGecko's (the guard's BASIS violation). Now: not formed, said, and no other basis."""
    monkeypatch.setattr(bw, "_SCOPE", list(config.PROJECTS))
    monkeypatch.setattr(bw, "_VIEW_BLOCKS", {})
    g = _near_groups()
    bw._near_lockup_views(g)
    s = g[("Near", "circulating_supply_lockup_rule")].set_index("date")
    assert s.index.max() == pd.Timestamp("2026-10-21")                # the read in force through 10-21 (10 days)
    assert "lockups read 2026-10-11" in s.loc["2026-10-21", "source"]
    assert s.loc["2026-10-10", "source"].endswith("no lockup read in force]")   # before the first read: NEAR's own
    why = bw._VIEW_BLOCKS[("Near", "circulating_supply_used")]
    assert why.startswith("BLOCKED — NEAR'S LOCKUP RULE CANNOT FORM ON 2026-10-26")
    assert "2026-10-11, 15 days old" in why and "near_lockups_weekly.py" in why
    bw._market_cap_views(g)
    used = g[("Near", "circulating_supply_used")]
    assert not used["source"].str.contains(r"\[circulating_supply[\]+]").any()     # never CoinGecko's figure
    assert pd.to_datetime(used["date"]).max() == pd.Timestamp("2026-10-24")      # the rule carried its 3 days, no more
    assert config.no_coingecko_fallback("Near")


def test_1_a_fresh_read_keeps_the_headline_on_the_rule_and_raises_no_block(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", list(config.PROJECTS))
    monkeypatch.setattr(bw, "_VIEW_BLOCKS", {})
    g = _near_groups(read_on="2026-10-20")
    bw._near_lockup_views(g)
    s = g[("Near", "circulating_supply_lockup_rule")].set_index("date")
    assert s.index.max() == pd.Timestamp("2026-10-26") and "lockups read 2026-10-20" in s.loc["2026-10-26", "source"]
    assert ("Near", "circulating_supply_used") not in bw._VIEW_BLOCKS
    bw._market_cap_views(g)
    used = g[("Near", "circulating_supply_used")].set_index("date")
    assert used.loc["2026-10-26", "value"] == pytest.approx(1_306_900_000 - 2_058_522 - 171_059)


# ================================================================================================== 5 NEAR total
def test_5_near_indexer_total_is_a_judged_credibility_row():
    import credibility
    rows = {"Near|total_supply": {"now": 1_309_976_215.0}, "Near|total_supply_dashboard": {"now": 1_310_001_847.0}}
    spec = credibility.wiring_inputs("Near", rows)["in_total_supply_dashboard"]
    assert spec["ours"]["metric"] == "total_supply" and spec["ref"]["metric"] == "total_supply_dashboard"
    assert spec["ref"]["tol"] == 1.0 and "force_verdict" not in spec["ref"] and "/v3/stats" in spec["ref"]["source"]
    assert "in_total_supply_dashboard" not in credibility.wiring_inputs("Sky", {"Sky|total_supply": {"now": 1.0}})


# ================================================================================================== 6 RWA turn
def test_6_the_rwa_turn_has_its_own_tier_and_says_what_it_did(tmp_path):
    import fetch
    from fetch import llama
    from fetch.base import FetchOutput
    names = [t[0] for t in fetch.TIER_ORDER]
    assert "defillama_rwa_share" in names and fetch._budget("defillama_rwa_share") >= 120
    d = llama.DefiLlama.__new__(llama.DefiLlama)
    d.part, d._today, d.unbounded = "rwa_share", "2026-10-26", True
    d.daily = type("D", (), {"f": tmp_path / "daily-checks.json"})()
    (tmp_path / "rwa-share.json").write_text('{"turn_on": "2026-10-24", "slugs": [], "done": {}, "failed": {}}')
    out = FetchOutput()
    d.run([config.PROJECT_BY_NAME["Plume"]], 30, out)
    assert any("RWA turn is weekly — last turn 2026-10-24" in e.message for e in out.log)    # a line, every run


def test_6_a_turn_in_progress_makes_the_share_cells_c_with_the_completion_date(tmp_path):
    import store as sm
    st = sm.Store(str(tmp_path / "m.db"))
    st.conn.execute("INSERT INTO run_log(run_id, ts, source, project, status, message) VALUES "
                    "('20261026T070000Z-a', '', 'defillama', 'Plume', 'ok', 'market_share_pct: RWA turn IN PROGRESS — "
                    "60 of 178 RWA protocol(s) read (60 this run); resumes next run')")
    st.conn.commit()
    prog = sm.rwa_turn_progress(st.conn, "Plume")
    assert prog == {"run_on": pd.Timestamp("2026-10-26"), "done": 60, "total": 178, "this_run": 60}
    try:
        config.set_rwa_turn_progress(prog)
        cls, ev = config.MISSING_DECLARED[("Plume", "market_share_pct")]
        assert cls == "C" and "60 of 178" in ev and "valid from 2026-10-28" in ev            # 118 left: 2 runs
        row = {"Project": "Plume", "Reason / route / error": "n/a — market_share_pct (now): no rows stored"}
        assert ca.missing_class(row)[0] == "C — NEEDS HISTORY"
    finally:
        config.set_rwa_turn_progress(None)
    assert ("Plume", "market_share_pct") not in config.MISSING_DECLARED
    st.close()


# ================================================================================================== 7 (all)
def test_7_the_all_row_for_the_insider_cells_reads_missing_e():
    rows = dict(ca.not_wired())
    item = next(k for k in rows if k.startswith("Insider share"))
    assert rows[item].startswith("MISSING E — needs issuer wallet list (Jake)")
    assert ca._BUILD_MISSING.match(rows[item])
    assert not any(v.startswith("NOT WIRED") for v in rows.values())


# ================================================================================================== 8 Berachain
def test_8_alchemy_pages_are_dated_as_they_come_and_a_read_stream_is_cached_at_once(tmp_path, monkeypatch):
    """Pendle's Berachain gauge (~700k transfers, no timestamps from Alchemy there): every page is dated before it is
    checkpointed, so a stop at the budget keeps dated transfers; a stream read in full is cached before the scan goes on."""
    from fetch import explorer as ex
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path))
    chain = 80094
    assert chain not in config.EXPLORERS["alchemy"]["metadata_chains"]
    pages = [[{"blockNum": hex(b), "hash": f"0x{b:064x}", "uniqueId": f"0x{b:064x}:log:0", "from": "0x" + "11" * 20,
               "to": "0x" + "22" * 20, "rawContract": {"value": hex(10 ** 18), "address": "0x" + "33" * 20,
                                                       "decimal": "0x12"}}
              for b in range(100 * i + 1, 100 * i + 4)] for i in range(3)]
    calls = []

    class H:
        def post(self, url, json_body=None, deadline=None):
            calls.append(json_body)
            i = len(calls) - 1
            if i == 2:
                raise ex.BudgetExhausted("budget")
            return {"result": {"transfers": pages[i], "pageKey": f"k{i + 1}"}}
    e = ex.ExplorerLogs(http=H(), pace_sleep=lambda _s: None)
    monkeypatch.setattr(e, "key", lambda name, cid=None: "https://x.example/v2/redacted" if name == "alchemy" else None)
    monkeypatch.setattr(e, "_check_budget", lambda: None)
    monkeypatch.setattr(e, "_timed_out", lambda: ex.ExplorerTimeout("out of time"))
    dated = []

    def dater(evs):
        dated.append(len(evs))
        return [{**x, "timeStamp": 1_790_000_000 + int(x["blockNumber"])} for x in evs]
    e.date_events = dater
    topics = [ex.TRANSFER_TOPIC, ex.pad_address("0x" + "11" * 20), None]
    with pytest.raises(ex.ExplorerTimeout) as ei:
        e._alchemy_paged("alchemy", chain, "0x" + "33" * 20, topics, 0, 1000)
    assert dated == [3, 3]                                                    # page by page
    assert ei.value.partial and all(int(x["timeStamp"]) > 0 for x in ei.value.partial)
    ck = ex.AlchemyCheckpoint(chain, {"fromBlock": hex(0), "contractAddresses": ["0x" + "33" * 20],
                                      "category": ["erc20"], "excludeZeroValue": False, "order": "asc",
                                      "maxCount": hex(int(config.EXPLORERS["alchemy"]["max_records"])),
                                      "fromAddress": "0x" + "11" * 20})
    out, _key, blk = ck.load()
    # the checkpoint holds dated transfers (loaded for another range, the last block is re-read from `blk`)
    assert blk == 103 and len(out) == 5 and all(int(x["timeStamp"]) > 0 for x in out)
    src = (ROOT / "fetch" / "logscan.py").read_text(encoding="utf-8")
    assert "A STREAM READ IN FULL IS CACHED AT ONCE" in src


# ================================================================================================== 2 Plume claims
def _word(n: int) -> str:
    return f"{n:064x}"


class _PlumeRpc:
    """A fake rpc.plume.org: eth_getLogs filtered by topic0 / topic1 lists, a 10,000-log cap simulated at `cap`,
    batched block timestamps, campaigns(id) by eth_call."""

    def __init__(self, logs, remaining, head=1_000, cap=10_000):
        self.logs, self.remaining, self.head, self.cap, self.calls = logs, remaining, head, cap, []

    def post(self, url, json_body=None, deadline=None):
        assert "explorer.plume.org" not in url
        if isinstance(json_body, list):
            return [{"id": b["id"], "result": {"timestamp": hex(1_790_000_000 + int(b["params"][0], 16) * 3600)}}
                    for b in json_body]
        m, p = json_body["method"], json_body["params"]
        self.calls.append(m)
        if m == "eth_blockNumber":
            return {"result": hex(self.head)}
        if m == "eth_getLogs":
            f = p[0]
            lo, hi = int(f["fromBlock"], 16), int(f["toBlock"], 16)
            t0, t1 = [x.lower() for x in f["topics"][0]], [x.lower() for x in f["topics"][1]]
            hit = [x for x in self.logs if lo <= int(x["blockNumber"], 16) <= hi and x["topics"][0] in t0
                   and x["topics"][1] in t1]
            if len(hit) > self.cap:
                return {"error": {"code": -32005, "message": "logs count limit exceeded (10000)"}}
            return {"result": hit}
        if m == "eth_call":
            tid = "0x" + p[0]["data"][10:]
            return {"result": "0x" + _word(0) * 2 + _word(self.remaining[tid]) + _word(0) * 5}
        raise AssertionError(m)


def _claims_world():
    from fetch.plume_claims import campaign_topic
    T = config.PLUME_CLAIM["topics"]
    nest, s2 = (config.PLUME_CLAIM["campaigns"][k]["id"] for k in ("Nest Season One", "Plume Points Season 2"))
    other = "9f66e8ef-59a9-4422-9487-a8f06002adc9"
    E = 10 ** 18

    def lg(block, t0, uuid, *words, i=0):
        return {"blockNumber": hex(block), "logIndex": hex(i), "topics": [t0, campaign_topic(uuid), "0x" + "0" * 64],
                "data": "0x" + "".join(_word(w) for w in words)}
    start_words = lambda amt: (0, 0, amt, 0, 0, 0, 0, 0, 0)   # noqa: E731
    logs = [lg(10, T["started"], nest, *start_words(100 * E)), lg(30, T["started"], s2, *start_words(50 * E)),
            lg(12, T["started"], other, *start_words(7 * E)),
            lg(11, T["claimed"], nest, 40 * E, 60 * E), lg(35, T["claimed"], nest, 10 * E, 50 * E, i=1),
            lg(35, T["claimed"], s2, 5 * E, 45 * E, i=2), lg(13, T["claimed"], other, 7 * E, 0)]
    remaining = {campaign_topic(nest): 50 * E, campaign_topic(s2): 45 * E}
    return logs, remaining


def test_2_claims_count_only_the_two_campaigns_over_the_rpc_reconciled(tmp_path, monkeypatch):
    from fetch.base import FetchOutput
    from fetch.plume_claims import PlumeClaims
    monkeypatch.setattr("fetch.plume_claims.today", lambda: pd.Timestamp("2026-10-20"))
    monkeypatch.setitem(config.PLUME_CLAIM, "deploy_block", 1)
    logs, remaining = _claims_world()
    rpc = _PlumeRpc(logs, remaining, cap=3)                            # the cap forces a split, as rpc.plume.org does
    pc = PlumeClaims(http=rpc, state_file=tmp_path / "s.json")
    pc.unbounded = True
    out = FetchOutput()
    pc.run([config.PROJECT_BY_NAME["Plume"]], 30, out)
    fr = [f for f in out.frames if f is not None and not f.empty]
    assert len(fr) == 1 and fr[0]["metric"].iloc[0] == "plume_airdrop_claims_tokens"
    assert fr[0]["value"].sum() == pytest.approx(55.0)                    # 40 + 10 + 5; the other campaign's 7 is out
    assert (fr[0]["value"] == 0).any()                                    # a day without a claim is a measured 0
    assert any("reconciled to the wei" in e.message for e in out.log)
    # the next run reads only new blocks and keeps the totals
    rpc.head = 1_200
    out = FetchOutput()
    pc.run([config.PROJECT_BY_NAME["Plume"]], 30, out)
    assert sum(f["value"].sum() for f in out.frames if f is not None and not f.empty) == pytest.approx(55.0)


def test_2_a_campaign_that_does_not_reconcile_stores_nothing(tmp_path, monkeypatch):
    from fetch.base import FetchOutput
    from fetch.plume_claims import PlumeClaims, campaign_topic
    monkeypatch.setitem(config.PLUME_CLAIM, "deploy_block", 1)
    logs, remaining = _claims_world()
    remaining[campaign_topic(config.PLUME_CLAIM["campaigns"]["Nest Season One"]["id"])] -= 1     # one wei adrift
    pc = PlumeClaims(http=_PlumeRpc(logs, remaining), state_file=tmp_path / "s.json")
    pc.unbounded = True
    out = FetchOutput()
    pc.run([config.PROJECT_BY_NAME["Plume"]], 30, out)
    assert not [f for f in out.frames if f is not None and not f.empty]
    assert any("DOES NOT RECONCILE" in e.message and "Nest Season One" in e.message for e in out.log)
    assert not (tmp_path / "s.json").exists()                             # the state is not advanced


def test_2_explorer_plume_is_left_to_plume_staking():
    """G18 2a: no log scan is routed to explorer.plume.org; its /api window belongs to plume_staking's payouts read."""
    import fetch
    assert 98866 not in config.EXPLORER_LOG_ROUTES and 98866 not in config.EXPLORERS["blockscout"]["hosts"]
    assert not any(s.get("chain") == "plume" for p in config.PROJECTS for s in p.get("log_scans") or [])
    assert "plume_claims" in [t[0] for t in fetch.TIER_ORDER]
    pay = config.PROJECT_BY_NAME["Plume"]["plume_staking"]["payouts"]
    assert pay["logs_api"] == "https://explorer.plume.org/api" and pay["max_calls_per_run"] <= 8
    for k in ("Nest Season One", "Plume Points Season 2"):
        assert len(config.PLUME_CLAIM["campaigns"][k]["id"]) == 36
    assert any(c["id"] == "G18-2" and c.get("every_row") for c in config.RUN_CLEANUPS)
