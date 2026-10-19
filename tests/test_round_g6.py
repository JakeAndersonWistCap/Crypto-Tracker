"""
tests/test_round_g6.py — Round G6 (Jake 2026-10-19), SPEC §19 "G6 — Round G6, old → new". One test (or more) per item,
each named for its row.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402


class _Daily:
    def __init__(self, due=True):
        self._due, self.marked, self.kv = due, [], {}

    def due(self, key, day):
        return self._due

    def done(self, key, day):
        self.marked.append(key)

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value):
        self.kv[key] = value

    def ever(self, key):
        return True


# ======================================================================================== item 0a per-project tiers
def test_0a_every_scan_runs_in_its_projects_own_tier():
    import fetch
    for p in config.PROJECTS:
        for sp in p.get("log_scans") or ():
            t = sp.get("own_tier")
            assert t == "explorer_gauge" or t.endswith("@" + p["name"]), (p["name"], sp.get("key"), t)
            assert t in fetch.TIER_BUDGET_S, t
    assert config.explorer_tier_budget("explorer@Chainlink") == config.EXPLORER_PROJECT_TIER_BUDGET_S
    assert config.explorer_tier_budget("explorer_l2:katana@Morpho") == config.L2_TIER_BUDGET_S["katana"]
    assert config.scan_tier("Morpho", {"own_tier": "explorer_l2:base"}) == "explorer_l2:base@Morpho"


def test_0a_a_tier_timeout_is_stamped_only_on_that_tiers_scans():
    from fetch import gaps
    cl = config.PROJECT_BY_NAME["Chainlink"]
    assert "noncirc_outflow_scan_tokens" in gaps.served_by("explorer@Chainlink", cl)
    assert gaps.served_by("explorer@Sky", cl) == set()


# ======================================================================================== item 0b incremental reconcile
def test_0b_logcache_appends_a_delta_and_saves_marks_without_rewriting(tmp_path):
    from fetch.logcache import LogCache
    ev = lambda i: {"blockNumber": hex(100 + i), "timeStamp": hex(1_700_000_000 + i), "data": hex(i + 1)}  # noqa: E731
    c = LogCache(tmp_path)
    c.save("s", [ev(i) for i in range(5)], 104, 104)
    base_mtime = c.path("s").stat().st_mtime_ns
    c2 = LogCache(tmp_path)
    st = c2.load("s")
    c2.save("s", st["events"] + [ev(5), ev(6)], 106, 106)            # two new: a delta, the base untouched
    assert c.path("s").stat().st_mtime_ns == base_mtime and len(c2._deltas("s")) == 1
    c2.save("s", st["events"] + [ev(5), ev(6)], 110, 110)            # none new: marks only
    assert (tmp_path / "s.meta.json").exists()
    st3 = LogCache(tmp_path).load("s")
    assert len(st3["events"]) == 7 and st3["scanned_to"] == 110 and st3["proven_to"] == 110
    c3 = LogCache(tmp_path)
    c3.load("s")
    c3.save("s", [ev(0)], 110, 110)                                   # not an append: compacted into one file
    assert not c3._deltas("s") and not (tmp_path / "s.meta.json").exists()
    assert len(LogCache(tmp_path).load("s")["events"]) == 1


def test_0b_poison_filter_is_indexed_and_keeps_the_old_rule():
    from fetch.logscan import _lookalike, drop_poison, topic_address
    known = {"0x3fb6aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa7b78", "0x1111222233334444555566667777888899990000"}
    pad = lambda a: "0x" + "0" * 24 + a[2:]                           # noqa: E731
    evs = [{"data": "0x0", "topics": ["t", "x", pad("0x3fb6bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbcccc")]},   # prefix lookalike
           {"data": "0x0", "topics": ["t", "x", pad("0x9999bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb0000")]},   # suffix lookalike
           {"data": "0x0", "topics": ["t", "x", pad("0x1234bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbabcd")]},   # unrelated
           {"data": "0x5", "topics": ["t", "x", pad("0x3fb6bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbcccc")]}]   # value moves
    kept, n = drop_poison(evs, 2, known)
    old = sum(1 for e in evs if int(e["data"], 16) == 0
              and any(_lookalike(topic_address(e["topics"][2]), k) for k in known))
    assert n == old == 2 and len(kept) == 2


def test_0b_sum_after_reads_only_the_new_tail():
    from fetch.logscan import _sum_after
    evs = [{"blockNumber": hex(b), "data": hex(v)} for b, v in ((10, 1), (11, 2), (12, 4), (13, 8))]
    assert _sum_after(evs, 11) == (12, 2)
    assert _sum_after(evs, 13) == (0, 0)


def test_0b_the_reconcile_line_says_how_the_net_was_formed_and_how_long():
    src = (ROOT / "fetch" / "logscan.py").read_text()
    assert "Reconcile {_t_rec.monotonic() - t_rec0:.1f}s, net {how_net}." in src
    assert "full history (the incremental net disagreed)" in src         # a mismatch re-sums before refusing


# ======================================================================================== item 0c Alchemy latency
def test_0c_free_plan_throughput_is_the_documented_300_cu_per_second():
    from fetch import alchemy_usage as au
    assert au.FREE_TIER_CU_PER_S == 300
    waits = []
    au._next_free = 0.0
    for _ in range(3):
        au.pace("https://eth-mainnet.g.alchemy.com/v2/X", "eth_call", sleep=waits.append)
    # eth_call is 26 CU: with the sleep stubbed the queue builds — the 2nd waits one slot, the 3rd two
    assert len(waits) == 2 and abs(waits[0] - 26 / 300) < 0.02 and abs(waits[1] - 2 * 26 / 300) < 0.02
    assert au.pace("https://ethereum-rpc.publicnode.com", "eth_call") == 0.0       # only Alchemy is paced


def test_0c_the_chain_tier_puts_a_faster_fallback_first_only_on_measured_timings():
    from fetch import chain
    eps = ["https://eth-mainnet.g.alchemy.com/v2/KEY", "https://ethereum-rpc.publicnode.com"]
    slow = {"eth-mainnet.g.alchemy.com": {"n": 30, "p50": 2.0, "timeouts": 1, "throttled": 0},
            "ethereum-rpc.publicnode.com": {"n": 3, "p50": 0.3, "timeouts": 0, "throttled": 0}}
    order, why = chain.connect_order("ethereum", eps, prior=slow)
    assert order[0] == eps[1] and "measured slower" in why and "KEY" not in why
    fast = {**slow, "eth-mainnet.g.alchemy.com": {"n": 30, "p50": 0.2, "timeouts": 0, "throttled": 0}}
    assert chain.connect_order("ethereum", eps, prior=fast) == (eps, "")
    few = {**slow, "eth-mainnet.g.alchemy.com": {"n": 5, "p50": 9.0, "timeouts": 3, "throttled": 0}}
    assert chain.connect_order("ethereum", eps, prior=few) == (eps, "")             # too few calls: no decision


def test_0c_latency_summary_and_report_name_hosts_only():
    from fetch import chain
    chain.RPC_TIMES.clear()
    chain._time_rpc("https://eth-mainnet.g.alchemy.com/v2/SECRETKEY123", "eth_call", 1.5, "ok")
    chain._time_rpc("https://eth-mainnet.g.alchemy.com/v2/SECRETKEY123", "eth_call", 30.0, "timeout")
    lines = chain.rpc_latency_report(save=False)
    assert lines and "eth-mainnet.g.alchemy.com" in lines[0] and "SECRETKEY" not in " ".join(lines)
    assert "1 timeout(s)" in lines[0]
    chain.RPC_TIMES.clear()


# ======================================================================================== item 0d defillama_tvl
def test_0d_tvl_tier_stops_before_its_budget_and_starts_next_at_the_cursor(monkeypatch):
    import fetch
    from fetch import llama
    monkeypatch.setitem(fetch.TIER_BUDGET_S, "defillama_tvl", 0.5)          # less than the floor: nothing fits
    d = _Daily()
    dl = llama.DefiLlama.__new__(llama.DefiLlama)
    dl.part, dl.daily, dl._today = "tvl", d, "2026-10-19"
    dl._project = lambda p, w, out: None
    dl.rwa = lambda projects, w, out: None
    out = FetchOutput()
    dl.run([{"name": "A"}, {"name": "B"}], 30, out)
    assert d.kv.get(llama.TVL_CURSOR_KEY) == "A"
    assert any("STOPPED before its budget" in e.message for e in out.log)
    d.kv[llama.TVL_CURSOR_KEY] = "B"
    monkeypatch.setitem(fetch.TIER_BUDGET_S, "defillama_tvl", 500)
    seen = []
    dl._project = lambda p, w, out: seen.append(p["name"])
    dl.run([{"name": "A"}, {"name": "B"}], 30, FetchOutput())
    assert seen == ["B", "A"] and d.kv[llama.TVL_CURSOR_KEY] == ""


# ======================================================================================== item 0 Aethir gap rows
def test_0_a_page_read_earlier_today_marks_every_series_it_carries():
    from fetch.aethir_pages import _declared_metrics
    dp = config.PROJECT_BY_NAME["Aethir"]["dashboard_pages"]
    pages = set()
    for m in dp["pages"].values():
        pages |= _declared_metrics(m)
    for m in ("compute_hours_weekly", "compute_rewards_tokens", "customer_revenue_monthly_usd", "staker_rewards_emitted"):
        assert m in pages
    assert {"arr_tile_usd", "compute_hours_cumulative"} <= _declared_metrics(
        {k: dp.get(k) for k in ("labelled", "derived", "series_totals")})


def test_0_a_dashboard_series_names_its_route_not_sources_yaml():
    from fetch import gaps
    p = config.PROJECT_BY_NAME["Aethir"]
    reason, _fix = gaps._tier_note(p, "compute_hours_weekly", {})
    assert "own dashboard" in reason and "sources.yaml" not in reason
    assert gaps._priority("Aethir", "compute_hours_weekly", reason) == 4


# ======================================================================================== item 6 Aethir weekly parser
def test_6_a_weekly_label_ages_from_the_weeks_end():
    from fetch.aethir_pages import yearless_days
    labels = [f"{d.day:02d}/{d.month:02d}" for d in pd.date_range("2026-06-22", "2026-10-05", freq="7D")]
    got = yearless_days(labels, "weekly", pd.Timestamp("2026-10-19"))
    assert not isinstance(got, str) and got[-1] == pd.Timestamp("2026-10-05")       # week to 11/10: 8 days old
    assert isinstance(yearless_days(labels, "weekly", pd.Timestamp("2026-10-26")), str)  # 15 days: refused


# ======================================================================================== item 8 SPEC coverage
def test_8_the_missing_columns_are_built():
    import build_workbook as bw
    heads = lambda specs: [s[0] for s in specs]                                       # noqa: E731
    vh = heads(bw._valuation_head(None, circulating=True))
    assert any(h.startswith("Circulating supply (used") for h in vh)
    assert any(h == "FDV ($)" for h in heads(bw._valuation_head(None, fdv=True)))
    src = (ROOT / "build_workbook.py").read_text()
    for tab in ("write_a1", "write_a2", "write_a4"):
        i = src.index(f"def {tab}(")
        assert "*_valuation_head(R, circulating=True)," in src[i:i + 2500], tab
    assert "*_valuation_head(R, fdv=True)," in src[src.index("def write_a3("):]
    cp = src[src.index("def _cp_cols("):]
    for h in ('("Price — spot ($)"', '("Emissions Q0 (tokens)', '("Unlocks Q0 (tokens)'):
        assert h in cp[:cp.index("# ---- SUPPLY LAYERS")], h
    for line in ("security emissions (a)", "ecosystem / grants (d)", "Outflows beside: unlocks (e)"):
        assert line in src


def test_8_tvl_native_is_labelled_not_built_by_design():
    import check_offline_items as co
    assert ("tvl_native", "CP Comparability") in co.NOT_BUILT_BY_DESIGN
    import spec
    assert spec.concept_for("CP Comparability", "Unlocks Q0 (tokens) — releases") == "unlocks" \
        if hasattr(spec, "concept_for") else True


# ======================================================================================== item 9 trajectory history
@pytest.mark.parametrize("row, stored, never, cls", [
    ({"Status": "N/A BY DESIGN", "Reason / route / error": "growth: 0 quarter(s)"}, {"a": 900}, [], "N/A BY DESIGN"),
    ({"Status": "MISSING", "Reason / route / error": "x"}, {"a": 900}, ["gross_issuance_tokens"], "INPUT NEVER STORED"),
    ({"Status": "LIVE-UNVERIFIED", "Reason / route / error": "BLOCKED — unequal coverage"}, {"a": 900}, [],
     "COVERAGE BLOCKED"),
    ({"Status": "LIVE-UNVERIFIED", "Reason / route / error": "fdv_usd from 2025 — a read-time view: it reaches"},
     {"a": 900}, [], "READ-TIME VIEW ONLY"),
    ({"Status": "LIVE-UNVERIFIED", "Reason / route / error": "HISTORY FORWARD-ONLY: fdv_usd"}, {"a": 900, "b": 38}, [],
     "GENUINELY LACKS HISTORY"),
    ({"Status": "LIVE-UNVERIFIED", "Reason / route / error": ""}, {"a": 900}, [], "BUILDER NOT WIRED"),
])
def test_9_each_short_trajectory_row_gets_one_reason(row, stored, never, cls):
    import check_offline_items as co
    assert co.classify_short_history(row, stored, never)[0] == cls


def test_9_fees_over_issuance_is_na_where_nothing_is_issued():
    import build_workbook as bw
    for n in ("Chainlink", "Ether.fi", "Plume", "Uniswap"):
        assert bw._no_issuance_text(config.PROJECT_BY_NAME[n]).startswith("n/a — no issuance to divide by"), n
    for n in ("Ethereum", "Near", "Sky", "Pendle"):          # issuing, or minted lazily (Pendle)
        assert bw._no_issuance_text(config.PROJECT_BY_NAME[n]) == "", n


# ======================================================================================== item 1 World Mobile
class _Koios:
    """Koios asset_info / address_assets and nothing else."""
    def __init__(self):
        self.calls = []

    def post(self, url, json_body=None, **_):
        self.calls.append(url)
        if url.endswith("/asset_info"):
            policy = json_body["_asset_list"][0][0]
            return [{"total_supply": "650795981305422" if policy.startswith("e5a42a1a") else "2000000000000000"}]
        if url.endswith("/address_assets"):
            from fetch.wm_supply import WMTX_NAME, WMTX_POLICY
            return [{"policy_id": "7f457b5a" + "0" * 48, "asset_name": "aa", "quantity": "1"},
                    {"policy_id": WMTX_POLICY, "asset_name": WMTX_NAME.encode().hex(), "quantity": "29556739390000"}]
        raise AssertionError(url)


def test_1b_locked_tokens_sums_both_locked_staking_contracts_and_skips_nfts():
    from fetch.wm_supply import WmSupply
    out = FetchOutput()
    WmSupply(http=_Koios(), daily=_Daily(), base_call=lambda token, data: 7_321_544_140_000).run(
        [{"name": "World Mobile"}], 365, out)
    got = {f["metric"].iloc[0]: float(f["value"].iloc[0]) for f in out.frames}
    assert got["wmtx_cardano_supply_tokens"] == pytest.approx(650_795_981.305422)
    assert got["locked_tokens_cardano"] == pytest.approx(29_556_739.39)       # the NFT on the script is not counted
    assert got["locked_tokens"] == pytest.approx(7_321_544.14 + 29_556_739.39)
    assert got["wmt_legacy_supply_tokens"] == 2_000_000_000                    # stored, never in a total
    assert "locked_tokens_blocked" not in config.PROJECT_BY_NAME["World Mobile"]


def test_1a_the_exploit_day_breaks_the_series_and_ethereum_leaves_the_sum():
    wm = config.PROJECT_BY_NAME["World Mobile"]
    assert wm["contracts"]["token"]["deprecated_from"] == "2026-09-20"
    (day, why), = config.SOURCE_RECLASSIFICATIONS[("World Mobile", "total_supply_gross")]
    assert day == "2026-09-20" and "500,479,231.217272" in why and "restatement" in why
    ex = wm["wmtx_exploit_2026_09"]
    assert ex["unauthorised_mint_tokens"] == 500_479_231.217272 and ex["conflicting_figure"] is None
    assert ex["base_reissue"]["total"] == pytest.approx(23_276_831.334 + 17_951_875.36)
    import run_sql
    sql = (ROOT / "orphan_cleanup.sql").read_text()
    sec = run_sql.parse_sections(sql)["DA"]
    assert "2026-09-20" in str(sec) and not run_sql._check_section_labels(sql, run_sql.parse_sections(sql))


def test_1c_world_mobile_chain_counts_exclude_the_system_transaction():
    bs = config.PROJECT_BY_NAME["World Mobile"]["blockscout_stats"]
    assert bs["base"] == "https://explorer.worldmobile.io/stats-service"
    assert bs["lines"]["own_chain_tx_count"]["minus"] == ("newTxns", "newBlocks")
    assert set(bs["lines"]) <= set(config.metrics_for_project(config.PROJECT_BY_NAME["World Mobile"]))


def test_1d_the_unlabelled_safe_is_never_treasury():
    h = config.PROJECT_BY_NAME["World Mobile"]["unlabelled_holders"]["0x76607a4d04Fc2Cd23224181F1C35CfEfCB0164d0"]
    assert h["label"] is None and "NOT treasury" in h["rule"]


def test_1e_headline_counters_are_manual_references_only():
    import manual_refs
    rows = [r for r in manual_refs.load() if r["project"] == "World Mobile"]
    assert {r["row"] for r in rows} >= {"airnodes_headline", "subscribers_headline", "rewards_paid_headline"}
    assert all("never a stored series" in r["note"] for r in rows)


# ======================================================================================== item 3 NEAR
def _near_chain(epochs):
    """A fake NEAR RPC: `epochs` = [(start height, day, supply before, supply at start, burnt)], newest first."""
    by_h, prev_of = {}, {}
    for i, (h, day, before, at, burnt) in enumerate(epochs):
        ts = int(pd.Timestamp(day).value) + 3600 * 10**9
        by_h[h] = {"header": {"height": h, "total_supply": str(int(at * 10**24)), "timestamp_nanosec": str(ts),
                              "prev_hash": f"p{h}", "epoch_id": f"e{h}"},
                   "chunks": [{"height_included": h, "balance_burnt": str(int(burnt * 10**24))},
                              {"height_included": h - 1, "balance_burnt": str(10**30)}]}
        nxt = epochs[i + 1][0] if i + 1 < len(epochs) else h - 1000
        by_h[f"p{h}"] = {"header": {"height": h - 1, "total_supply": str(int(before * 10**24)),
                                    "timestamp_nanosec": str(ts - 10**9), "prev_hash": "x", "epoch_id": f"prev{h}"}}
        prev_of[f"prev{h}"] = nxt

    def rpc(method, params):
        if method == "validators":
            return {"epoch_start_height": epochs[0][0] if params == [None] else prev_of[params["epoch_id"]]}
        return by_h[params["block_id"]]
    return rpc


def test_3_near_epoch_mint_is_supply_rise_plus_burnt_gas_in_that_block_only():
    from fetch.near_issuance import walk
    rpc = _near_chain([(300, "2026-10-18", 1000.0, 1100.0, 5.0), (200, "2026-10-18", 900.0, 990.0, 1.0),
                       (100, "2026-10-17", 800.0, 850.0, 0.0), (50, "2026-10-15", 1.0, 2.0, 0.0)])
    eps, cont, reached = walk(rpc, pd.Timestamp("2026-10-16"))
    assert reached and cont == 50
    assert [(str(d.date()), round(v, 6)) for d, v, _h in eps] == [
        ("2026-10-18", 105.0), ("2026-10-18", 91.0), ("2026-10-17", 50.0)]   # a chunk included elsewhere is not burnt here


def test_3_a_walk_stopped_by_the_deadline_drops_its_oldest_partial_day():
    from fetch.near_issuance import walk
    rpc = _near_chain([(300, "2026-10-18", 0, 1, 0), (200, "2026-10-17", 0, 1, 0), (100, "2026-10-17", 0, 1, 0),
                       (50, "2026-10-16", 0, 1, 0)])
    ticks = iter([0, 0, 0, 99, 99])
    eps, cont, reached = walk(rpc, pd.Timestamp("2026-10-01"), deadline=1, clock=lambda: next(ticks))
    assert not reached and [str(d.date()) for d, _v, _h in eps] == ["2026-10-18"] and cont == 200


def test_3_near_primary_is_the_measured_mint_and_the_rate_change_a_declared_step():
    spec = config.issuance_primary("Near")
    assert spec["kind"] == "first_party" and spec["source_prefix"].startswith("near_issuance:")
    assert spec["history_prefix"] == "derived:d_total_supply_protocol" and spec["prefer_primary_per_day"]
    step = config.PROJECT_BY_NAME["Near"]["issuance_rate_declared"]["step"]
    assert step["block"] == 170_351_519 and step["max_inflation_rate"] == ((1, 20), (1, 40))


def test_3_near_primary_wins_per_day_over_the_history_leg(monkeypatch):
    import build_workbook as bw
    asof = pd.Timestamp("2026-10-19")
    days = pd.date_range("2026-07-01", "2026-10-18")
    hist = pd.DataFrame({"date": days, "project": "Near", "metric": "gross_issuance_tokens", "value": 1.0,
                         "source": "derived:d_total_supply_protocol+burn", "tier": 2})
    prim = pd.DataFrame({"date": days[-3:], "project": "Near", "metric": "gross_issuance_tokens", "value": 2.0,
                         "source": "near_issuance:near_rpc:epoch_mint[3 epochs]", "tier": 1})
    sup = pd.DataFrame({"date": [asof - pd.Timedelta(days=1)], "project": "Near", "metric": "total_supply",
                        "value": 1.0 * 365 / 0.025, "source": "coingecko", "tier": 3})
    groups = {("Near", "gross_issuance_tokens"): pd.concat([hist, prim], ignore_index=True),
              ("Near", "total_supply"): sup}
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Near"]])
    bw._VIEW_BLOCKS.clear()
    bw._issuance_views(groups, asof)
    g = groups[("Near", "gross_issuance_tokens")]
    assert g["date"].is_unique and len(g) == len(days)
    assert g.set_index("date").loc[days[-1], "value"] == 2.0 and g.set_index("date").loc[days[0], "value"] == 1.0
    assert ("Near", "gross_issuance_tokens") not in bw._VIEW_BLOCKS
    # the declared rate is the guard: measured ~1/day against a supply that would mint ~100x that is BLOCKED
    groups = {("Near", "gross_issuance_tokens"): pd.concat([hist, prim], ignore_index=True),
              ("Near", "total_supply"): sup.assign(value=100 * 365 / 0.025)}
    bw._issuance_views(groups, asof)
    assert "BLOCKED — measured" in bw._VIEW_BLOCKS[("Near", "gross_issuance_tokens")]
    bw._VIEW_BLOCKS.clear()


# ======================================================================================== items 3 GEODNET / Ether.fi
def test_3_geodnet_solana_is_counted_when_funded_and_etherfi_short_is_the_burns():
    a = config.PROJECT_BY_NAME["GEODNET"]["solana_issuance_answer"]
    for _m, (ours, poly, _sol) in a["evidence"].items():
        assert ours - poly == pytest.approx(1_000_000, abs=0.01)
    assert "INFERRED" in a["status"]
    e = config.PROJECT_BY_NAME["Ether.fi"]
    assert e["supply_short_of_cap"]["cap"] - e["supply_short_of_cap"]["total_supply"] == 1_464_001
    assert e["programmatic_buyback_split"]["declared"] == {"labs": 0.40, "buyback": 0.30, "user_cashback": 0.30}


# ======================================================================================== item 4 splits
def test_4_uniswap_burn_share_is_of_protocol_fees_and_chainlink_keeps_the_implied_suppressed():
    import build_workbook as bw
    uni = config.PROJECT_BY_NAME["Uniswap"]
    assert uni["burn_split"]["status"] == "active" and uni["burn_split"]["share_of_fees_burned"] == 1.0
    assert bw._burn_fee_base(uni) == "revenue_usd" and bw._burn_fee_base(config.PROJECT_BY_NAME["Sky"]) == "fees_usd"
    assert uni["fee_switch_executions"]["#102 (Arc)"].startswith("2026-10-05")
    fs = config.PROJECT_BY_NAME["Chainlink"]["fee_split"]
    assert fs["status"] == "unconfirmed" and fs["share_to_buyback"] is None
    assert fs["documented_part"]["share"] == 0.5 and "SVR" in fs["documented_part"]["of"]


# ======================================================================================== item 2 Aero VE gap
def test_2_the_ve_gap_is_the_managed_nfts_lmr_balances():
    g = config.AERODROME_MIGRATION["ve_gap_2026_10_19"]
    assert g["supply"] - g["held"] == pytest.approx(g["gap"], abs=1e-3)
    assert g["lmr_held"] - g["gap"] == pytest.approx(g["residual"], abs=0.01)


def test_2_the_v3_supply_adds_the_other_launch_chains_net_of_escrows(monkeypatch):
    sys.path.insert(0, str(ROOT / "tests"))
    from test_round_g2 import _aero_state, _supply_rpc
    from fetch import aero_v3 as a
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-10-23"))
    st = _aero_state()
    st["chain_tokens"] = {"base": st["addresses"]["token"], "ethereum": "0x" + "1" * 40, "optimism": "0x" + "2" * 40}
    st["bridge_escrows"] = [{"chain": "ethereum", "address": "0x" + "3" * 40}]
    rdr = a.AeroV3(rpc=_supply_rpc(a, basis=1_820e6, converted=1_820e6), state=st)
    monkeypatch.setattr(rdr, "_chain_supply", lambda chain, tok: {"ethereum": 30e6}[chain])   # optimism: no RPC
    monkeypatch.setattr(rdr, "_chain_balance", lambda chain, tok, holder: 5e6)
    base_only, out = FetchOutput(), FetchOutput()
    rdr._supply("Aerodrome", {k: v.lower() for k, v in st["addresses"].items()}, base_only)
    rdr._supply("Aerodrome", {k: v.lower() for k, v in st["addresses"].items()}, out, st)
    b, f = base_only.frame().set_index("metric"), out.frame().set_index("metric")
    assert f.loc["total_supply", "value"] - b.loc["total_supply", "value"] == pytest.approx(25e6)
    assert f.loc["total_supply", "source"].endswith("+chains[ethereum]:PARTIAL")       # optimism named, not assumed 0


def test_5_every_compared_reference_has_a_tolerance_the_checker_can_read():
    import csv
    import references
    for row in csv.DictReader(open(ROOT / "independent_references.csv", encoding="utf-8")):
        route = references.ROUTES.get((row["project"], row["metric"]))
        if route and route[0] != "recorded":
            references.tolerance(row["tolerance"], row["value"])       # raises on a blank or unreadable tolerance


def test_5_a_period_our_series_has_not_reached_is_not_judged_as_zero():
    import references
    s = pd.DataFrame({"date": pd.to_datetime(["2026-07-31", "2026-08-31"]), "value": [136_768.0, 147_098.0]})
    groups = {("Maple", "actual_buyback_usd"): s.assign(project="Maple", metric="actual_buyback_usd", source="maple_page")}
    route = ("sum_period", "actual_buyback_usd", "2026-09-01", "2026-09-30")
    ours, how, why = references._our_value(route, groups, "Maple", pd.Timestamp("2026-09-30"), 314_116.0, None)
    assert ours is None and "before the period's end 2026-09-30" in why
    s2 = pd.concat([s, pd.DataFrame({"date": [pd.Timestamp("2026-09-30")], "value": [314_116.0]})])
    groups[("Maple", "actual_buyback_usd")] = s2.assign(project="Maple", metric="actual_buyback_usd", source="maple_page")
    ours, _how, _why = references._our_value(route, groups, "Maple", pd.Timestamp("2026-09-30"), 314_116.0, None)
    assert ours == 314_116.0


def test_0a_a_probed_scan_is_wired_into_its_projects_own_tier(monkeypatch):
    import fetch
    p = config.PROJECT_BY_NAME["Morpho"]
    monkeypatch.setitem(p, "log_scans", [s for s in p["log_scans"] if s["key"] != "merkl_morpho_out_base"])
    for name in ("Fluid", "Pendle"):
        monkeypatch.setitem(config.PROJECT_BY_NAME[name], "log_scans", list(config.PROJECT_BY_NAME[name]["log_scans"]))
    monkeypatch.setitem(config.PROJECT_BY_NAME["Pendle"], "emissions_from_metric",
                        tuple(config.PROJECT_BY_NAME["Pendle"]["emissions_from_metric"]))
    monkeypatch.setitem(config.OUTFLOW_CATEGORIES, "Fluid", dict(config.OUTFLOW_CATEGORIES["Fluid"]))
    config.wire_probed_scans({"alchemy": {"served": [8453], "probed_on": "2026-10-19"}})
    s = next(x for x in p["log_scans"] if x["key"] == "merkl_morpho_out_base")
    assert s["own_tier"] == "explorer_l2:base@Morpho" and s["own_tier"] in {t for t, _, _ in fetch.TIER_ORDER}
