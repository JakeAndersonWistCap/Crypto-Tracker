"""
tests/test_round_g7.py — Round G7 (Jake 2026-10-19), SPEC §19 "G7 — Round G7, old → new". One test (or more) per item,
each named for its row.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402


def _rows(project, metric, days, values, source, tier=2):
    days = pd.to_datetime(list(days))
    vals = values if isinstance(values, (list, tuple)) else [values] * len(days)
    return pd.DataFrame({"date": days, "project": project, "metric": metric, "value": [float(v) for v in vals],
                         "source": source, "tier": tier})


# ======================================================================================== 0a World Mobile option C / B
def test_0a_wm_total_is_option_c_and_option_b_is_its_own_row(monkeypatch):
    import build_workbook as bw
    wm = config.PROJECT_BY_NAME["World Mobile"]
    spec = wm["total_supply_option_c"]
    assert spec["eth_pre_exploit"] == 1_013_930_149.415209 and spec["eth_block"] == 26_016_081
    assert spec["first_counterfeit_block"] == spec["eth_block"] + 1
    assert spec["reissue_confirmed"] == 0.0 and spec["snapshot_total"] is None
    assert spec["label"] == "inferred from chain: pre-exploit Ethereum supply awaiting re-issue"
    days = ["2026-10-18", "2026-10-19"]
    groups = {("World Mobile", "total_supply_gross"): _rows("World Mobile", "total_supply_gross", days, 269_379_304.50,
                                                            "chain:sum(...)"),
              ("World Mobile", "wmtx_cardano_supply_tokens"): _rows("World Mobile", "wmtx_cardano_supply_tokens",
                                                                    days[1:], 650_795_981.305422, "wm_supply:koios"),
              ("World Mobile", "total_supply"): _rows("World Mobile", "total_supply", days, 2e9, "coingecko", 3)}
    monkeypatch.setattr(bw, "scoped_projects", lambda: [wm])
    bw._wm_total_supply_views(groups)
    b = groups[("World Mobile", "total_supply_with_cardano_tokens")]
    assert list(b["value"]) == [pytest.approx(269_379_304.50 + 650_795_981.305422)]       # B: one day, both legs read
    t = groups[("World Mobile", "total_supply")].set_index("date")
    c = t.loc[pd.Timestamp("2026-10-19")]
    assert c["value"] == pytest.approx(269_379_304.50 + 650_795_981.305422 + 1_013_930_149.415209)
    assert spec["label"] in c["source"] and "46,227,317.694" in c["source"]
    assert t.loc[pd.Timestamp("2026-10-18"), "source"] == "coingecko"         # the day before C's first keeps its row
    assert list(groups[("World Mobile", "total_supply_coingecko")]["value"]) == [2e9, 2e9]
    # the published snapshot total replaces the inferred leg once one exists
    monkeypatch.setitem(spec, "snapshot_total", 900_000_000.0)
    monkeypatch.setitem(spec, "snapshot_source", "worldmobile.io (test)")
    groups.pop(("World Mobile", "total_supply_coingecko"))
    bw._wm_total_supply_views(groups)
    c = groups[("World Mobile", "total_supply")].set_index("date").loc[pd.Timestamp("2026-10-19")]
    assert c["value"] == pytest.approx(269_379_304.50 + 650_795_981.305422 + 900_000_000.0)
    assert "published snapshot total" in c["source"]


def test_0a_the_base_recovery_mints_are_reconciled_and_not_counted_as_holder_reissue():
    ex = config.PROJECT_BY_NAME["World Mobile"]["wmtx_exploit_2026_09"]["base_reissue"]
    assert ex["holder_reissue_confirmed"] == 0.0
    assert ex["recovery_mints_total"] == pytest.approx(23_276_831.334 + 17_951_875.36 + 10 + 4_998_601)
    # Base totalSupply rebuilt from the logs, exactly (agent read 2026-10-19)
    assert 161_336_057.257586 + 10_066_034.984429 - 2_950_344.123859 + 46_227_317.694 == pytest.approx(214_679_065.812156)
    assert config.METRICS["total_supply_with_cardano_tokens"]["view_only"]


# ======================================================================================== 0b Ether.fi programmatic Safe
def test_0b_etherfi_safe_label_silence_check_and_no_ratio_without_a_stated_share():
    lbl = config.ETHERFI_PROGRAMMATIC_SAFE_LABEL
    assert lbl.startswith("identified on-chain") and "not published by ether.fi" in lbl
    assert "none held or retired" in lbl and "shares no owner" in lbl        # the top-up Safe shares no owner (read)
    cad = config.program_cadence("Ether.fi", "programmatic_buyback_tokens")
    assert cad and cad["silent_after_days"] == 14
    sp = config.PROJECT_BY_NAME["Ether.fi"]["programmatic_buyback_split"]
    assert sp["ratio_to_commitment"] is None and sp["ratio_why_none"]
    assert sp["funding_received"]["usdc"] == pytest.approx(1_255_229.08)


# ======================================================================================== 0c HyperEVM burn is A1 only
def test_0c_hyperevm_gas_burn_leaves_a4_and_is_added_on_a1(monkeypatch):
    import build_workbook as bw
    assert config.a4_net_burn_metric("Hyperliquid") == "burn_ex_chain_fee_tokens"
    assert config.a4_net_burn_metric("Sky") == config.a1_burn_metric("Sky")
    hl = config.PROJECT_BY_NAME["Hyperliquid"]
    days = ["2026-10-10", "2026-10-11", "2026-10-12"]
    groups = {("Hyperliquid", "gross_burn_tokens"): _rows("Hyperliquid", "gross_burn_tokens", days, 100.0, "hypercore"),
              ("Hyperliquid", "core_burn_tokens"): _rows("Hyperliquid", "core_burn_tokens", days, 10.0, "hypercore"),
              ("Hyperliquid", "hyperevm_priority_burn_tokens"): _rows("Hyperliquid", "hyperevm_priority_burn_tokens",
                                                                      days[1:], 1.0, "chain:hyperevm")}
    monkeypatch.setattr(bw, "scoped_projects", lambda: [hl])
    bw._burn_total_views(groups)
    a1 = groups[("Hyperliquid", "total_burn_tokens")].set_index("date")["value"]
    a4 = groups[("Hyperliquid", "burn_ex_chain_fee_tokens")].set_index("date")["value"]
    assert list(a1) == [110.0, 111.0, 111.0] and list(a4) == [110.0, 110.0, 110.0]


# ======================================================================================== 0d reference #102's date
def test_0d_uniswap_execution_102_is_dated_2026_not_2025():
    ex = config.PROJECT_BY_NAME["Uniswap"]["fee_switch_executions"]
    assert ex["#102 (Arc)"].startswith("2026-10-05")
    assert all("2025-12-27" <= str(v)[:10] <= "2026-10-19" for v in ex.values() if str(v)[:2] == "20")


# ======================================================================================== 5 Sky USDS spent by day
def test_5_sky_daily_buyback_spending_from_the_first_sky_buyback():
    import fetch.sky_accounting as sa
    rows = [{"date": "2024-09-30", "amount": "-999"},                     # MKR flap auction: not a SKY buyback
            {"date": "2024-11-14", "amount": "-17500"}, {"date": "2024-11-15", "amount": "-105000"},
            {"date": "2024-11-15", "amount": "-1"}, {"date": "2026-10-19", "amount": "-42900"}]
    got = sa.daily_spending(rows, "2024-11-13", pd.Timestamp("2026-10-19"))
    assert got == [(pd.Timestamp("2024-11-14"), 17500.0), (pd.Timestamp("2024-11-15"), 105001.0)]
    import references as refs
    assert refs.ROUTES[("Sky", "g6_usds_spent_cumulative")] == ("cumulative", "buyback_spending_usd_ba_daily")
    spec = config.PROJECT_BY_NAME["Sky"]["sky_accounting"]
    assert spec["daily_from"] == "2024-11-13" and spec["metrics"]["buyback_daily"] == "buyback_spending_usd_ba_daily"


def test_5_sky_treasury_reference_reads_the_block_it_was_taken_at():
    import references as refs
    assert refs.ROUTES[("Sky", "g6_sky_treasury")] == ("block_read", "g6_sky_treasury")
    r = config.REFERENCE_BLOCK_READS[("Sky", "g6_sky_treasury")]
    assert r["block"] == 26_225_851 and r["value"] == pytest.approx(28_244_406.69674779)


# ======================================================================================== 7 small fixes
def test_7_sanity_workbook_mode_counts_the_builds_own_hits(tmp_path, capsys):
    import sanity
    wb = tmp_path / "token_metrics.xlsx"
    (tmp_path / "sanity_hits.json").write_text(json.dumps({
        "workbook": wb.name, "built": "2026-10-19",
        "hits": [["Near", "headline_starved", "A1 net issuance n/a"], ["Near", "headline_lost", "x"]]}))
    found = sanity.with_build_hits([("Sky", "fdv_lt_mcap", "y")], wb)
    assert len(found) == 3
    (tmp_path / "sanity_hits.json").write_text(json.dumps({"workbook": "other.xlsx", "hits": [["A", "b", "c"]]}))
    assert sanity.with_build_hits([], wb) == []
    assert "not this workbook" in capsys.readouterr().out


def test_7_build_writes_its_sanity_hits_beside_the_workbook():
    src = (ROOT / "build_workbook.py").read_text()
    assert 'with_name("sanity_hits.json")' in src


def _store(tmp_path, rows):
    db = tmp_path / "m.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE metrics (project, metric, date, value, source)")
    con.executemany("INSERT INTO metrics VALUES (?,?,?,?,?)", rows)
    con.commit()
    return con


def test_7_hyperliquid_buyback_usd_is_a_read_time_view(tmp_path):
    import check_offline_items as coi
    con = _store(tmp_path, [("Hyperliquid", "gross_burn_tokens", "2026-10-18", 1.0, "x"),
                            ("Hyperliquid", "core_burn_tokens", "2026-10-17", 1.0, "x")])
    why = coi._not_stale(config, con, "Hyperliquid", "actual_buyback_usd", "2026-09-29")
    assert why.startswith("READ-TIME VIEW") and "price_usd" in why and "inputs through 2026-10-17" in why


def test_7_pendle_latent_supply_is_off_the_not_wired_list():
    import coverage_audit
    assert not any("latent" in item.lower() for item, _r in coverage_audit.not_wired())


def test_7_aero_buyback_seed_reads_no_getlogs_and_an_alchemy_key_counts_as_keyed(monkeypatch):
    import inspect
    import check_offline_items as coi
    import fetch.ve_managed as vm
    src = inspect.getsource(coi.aerodrome_buyback_window) + inspect.getsource(coi._base_move_blocks) \
        + inspect.getsource(coi._safe_nonce_blocks) + inspect.getsource(coi._block_receipts)
    assert "eth_getLogs" not in src and "eth_getBlockReceipts" in src
    monkeypatch.delenv("BASE_RPC_URL", raising=False)
    monkeypatch.setattr(vm, "_alchemy_base", lambda: "https://base-mainnet.g.alchemy.com/v2/k")
    vm.seed_foundation([], days=0)
    assert vm.LAST_SEED["keyed"] is True


# ======================================================================================== 8 Artemis API retired
def test_8_artemis_api_route_is_retired_and_a_key_is_never_sent(monkeypatch, tmp_path):
    import fetch.artemis as fa
    r = config.ARTEMIS_SETTLEMENT["api"]["retired"]
    assert r["on"] == "2026-10-19" and "410" in r["why"]
    monkeypatch.setenv("ARTEMIS_API_KEY", "secret-not-printed")
    out = FetchOutput()
    fa.ArtemisCSV(root=tmp_path).run([], 365, out)
    text = " ".join(str(e.message) for e in out.log)
    assert "RETIRED" in text and "secret-not-printed" not in text
    import check_offline_items as coi
    import inspect
    assert "api.artemisxyz.com" not in inspect.getsource(coi.settlement_sources)


# ======================================================================================== 8 Ethereum settlement rebuild
def _series(days, v):
    return pd.Series(v, index=pd.DatetimeIndex(days), dtype=float)


def test_8_validation_passes_only_with_every_complete_week_within_ten_percent():
    import build_workbook as bw
    spec = config.SETTLEMENT_REBUILD["ethereum_rebuild"]["validation"]
    assert spec == {"to": "2026-08-25", "min_days": 90, "weekly_tol": 0.10,
                    "daily": "median and 90th percentile of |daily gap| reported, not gated"}
    days = pd.date_range("2026-05-01", "2026-08-25")
    csv = _series(days, 100.0)
    ok = bw.settlement_validation(_series(days, 105.0), csv, spec)
    assert ok["status"] == "PASSED" and ok["weekly_max_abs_gap"] == pytest.approx(0.05)
    noisy = _series(days, [150.0 if i % 7 == 0 else 92.0 for i in range(len(days))])     # daily noise, weekly ~+0.6%
    assert bw.settlement_validation(noisy, csv, spec)["status"] == "PASSED"
    bad = _series(days, [100.0] * (len(days) - 7) + [130.0] * 7)
    assert bw.settlement_validation(bad, csv, spec)["status"] == "FAILED"
    short = bw.settlement_validation(_series(days[-30:], 100.0), csv, spec)
    assert short["status"] == "NOT VALIDATED" and "fewer than 90" in short["why"]


def test_8_the_rebuild_extends_the_csv_only_after_it_passes(monkeypatch):
    import build_workbook as bw
    eth = config.PROJECT_BY_NAME["Ethereum"]
    monkeypatch.setattr(bw, "scoped_projects", lambda: [eth])
    days = pd.date_range("2026-05-01", "2026-09-30")
    csv_days = days[days <= "2026-08-25"]

    def groups(p2p_value):
        return {("Ethereum", "dex_volume_usd"): _rows("Ethereum", "dex_volume_usd", days, 10.0, "defillama"),
                ("Ethereum", "nft_volume_usd"): _rows("Ethereum", "nft_volume_usd", days, 1.0, "eth_settlement"),
                ("Ethereum", "p2p_transfer_volume_usd"): _rows("Ethereum", "p2p_transfer_volume_usd", days, p2p_value,
                                                               "eth_settlement:bigquery"),
                ("Ethereum", "settlement_volume_usd"): _rows("Ethereum", "settlement_volume_usd", csv_days, 100.0,
                                                             "artemis:settlement_volume (CSV export)")}
    # the rule without Jake's G10 declared handover (G10 4 hands over from 2026-08-26 whatever the validation)
    monkeypatch.delitem(config.SETTLEMENT_REBUILD["ethereum_rebuild"], "handover")
    g = groups(89.0)
    bw._settlement_rebuild_views(g)
    sv = g[("Ethereum", "settlement_volume_usd")]
    assert sv["date"].max() == pd.Timestamp("2026-09-30") and bw.SETTLEMENT_VALIDATION["Ethereum"]["status"] == "PASSED"
    assert "different measuring point" in sv[sv["date"] > "2026-08-25"]["source"].iloc[0]
    g = groups(50.0)
    bw._settlement_rebuild_views(g)
    assert g[("Ethereum", "settlement_volume_usd")]["date"].max() == pd.Timestamp("2026-08-25")    # CSV only
    assert bw.SETTLEMENT_VALIDATION["Ethereum"]["status"] == "FAILED"
    assert ("Ethereum", "settlement_rebuilt_usd") in g
    g = groups(89.0)
    g.pop(("Ethereum", "p2p_transfer_volume_usd"))
    bw._settlement_rebuild_views(g)
    assert "p2p.approved" in bw.SETTLEMENT_VALIDATION["Ethereum"]["why"]


def test_8_near_and_plume_rebuilds_are_labelled_not_validated_on_their_chain():
    for n in ("Near", "Plume"):
        assert config.PROJECT_BY_NAME[n]["settlement_rebuild"]["label"].endswith("rebuild, not validated on this chain")


def test_8_nft_leg_reads_defillama_and_p2p_is_dry_run_only_until_approved(tmp_path, monkeypatch):
    import fetch.eth_settlement as es
    # Jake approved the leg in G8 (item 5); the rule this tests — dry runs only while NOT approved — still holds
    monkeypatch.setitem(config.SETTLEMENT_REBUILD["ethereum_rebuild"]["p2p"], "approved", False)

    class H:
        def get(self, url, params=None, **_):
            assert url.endswith("/overview/nft-volume/ethereum")
            return {"chain": "Ethereum", "totalDataChart": [[1760140800, 5.0], [1760227200, 6.0]]}

    class D:
        def due(self, *_):
            return True

        def done(self, *_):
            pass
    out = FetchOutput()
    es.EthNftVolume(http=H(), daily=D()).run([config.PROJECT_BY_NAME["Ethereum"]], 365, out)
    assert list(out.frame()["value"]) == [5.0, 6.0]

    class Job:
        total_bytes_processed = 13_000_000_000

    class Client:
        project = config.PROJECT_BY_NAME["Near"]["near_bigquery"]["project"]
        sent = []

        def query(self, sql, job_config=None):
            assert job_config.dry_run, "nothing but dry runs while not approved"
            self.sent.append(sql)
            return Job()

    class BQ:
        class QueryJobConfig:
            def __init__(self, **kw):
                self.dry_run = kw.get("dry_run", False)

        @staticmethod
        def ScalarQueryParameter(*a):
            return a

        @staticmethod
        def ArrayQueryParameter(*a):
            return a
    out = FetchOutput()
    a = es.EthP2PBigQuery(client=Client(), bq=BQ(), cache_file=tmp_path / "bq.json", csv_dir=tmp_path / "none")
    a.run([config.PROJECT_BY_NAME["Ethereum"]], 365, out)
    text = " ".join(e.message for e in out.log)
    assert "DRY RUNS" in text and "one week 13.0 GB" in text and "NOT APPROVED" in text
    assert all("crypto_ethereum.contracts" in q for q in Client.sent)
    st = json.loads((tmp_path / "bq.json").read_text())
    assert st["eth_p2p"]["dry"]["one week"] == 13_000_000_000 and "topup_bytes" not in st


def test_8_p2p_values_eth_at_the_stored_price_and_refuses_a_day_with_wrong_decimals(tmp_path):
    import fetch.eth_settlement as es
    usdc = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    (tmp_path / "csv").mkdir()
    (tmp_path / "csv" / "p2p.csv").write_text("day,token,amount,n\n2026-10-01,ETH,2000000000000000000,5\n"
                                              f"2026-10-01,{usdc},3000000,2\n2026-10-02,{usdc},1000000,1\n")
    px = pd.DataFrame({"project": "Ethereum", "metric": "price_usd", "date": pd.to_datetime(["2026-10-01", "2026-10-02"]),
                       "value": [4000.0, 4100.0]})
    prices = {("2026-10-01", f"ethereum:{usdc}"): (1.0, 6), ("2026-10-02", f"ethereum:{usdc}"): (1.0, 18)}
    a = es.EthP2PBigQuery(stored_long=px, prices=prices, cache_file=tmp_path / "bq.json", csv_dir=tmp_path / "csv")
    a._client = lambda spec: (None, "no ADC in the test")
    out = FetchOutput()
    a.run([config.PROJECT_BY_NAME["Ethereum"]], 365, out)
    f = out.frame()
    assert list(f["value"]) == [pytest.approx(2 * 4000.0 + 3.0)]          # 10-02 refused: decimals 18, declared 6
    assert "1 day(s) refused" in " ".join(e.message for e in out.log)


# ======================================================================================== 10 Plume / World Mobile
def test_10_plume_fees_slug_is_plume_mainnet_stored_as_a_cross_check():
    from fetch.llama import DefiLlama
    pl = config.PROJECT_BY_NAME["Plume"]
    assert pl["defillama_fees_slug"] == "plume-mainnet" and not pl.get("known_absent_calls")
    calls = []

    class D(DefiLlama):
        def _summary_chart(self, slug, data_type):
            calls.append((slug, data_type))
            return [(pd.Timestamp("2026-10-18"), 86.0)]
    out = FetchOutput()
    D(known_absent=set()).fees(pl, 30, out)
    f = out.frame()
    assert set(f["metric"]) == {"fees_usd_defillama"} and calls == [("plume-mainnet", "dailyFees")]
    assert sum("not called" in e.message for e in out.log) == 2


def test_10_world_mobile_chain_fees_are_read_and_priced_and_headlines_recorded(monkeypatch):
    import build_workbook as bw
    wm = config.PROJECT_BY_NAME["World Mobile"]
    assert wm["blockscout_stats"]["lines"]["own_chain_fees_native_tokens"] == "txnsFee"
    assert "own_chain_fees_native_tokens" in config.metrics_for_project(wm)
    days = ["2026-10-17", "2026-10-18"]
    groups = {("World Mobile", "own_chain_fees_native_tokens"): _rows("World Mobile", "own_chain_fees_native_tokens",
                                                                      days, 15_803.65, "blockscout_stats:txnsFee"),
              ("World Mobile", "price_usd"): _rows("World Mobile", "price_usd", days[1:], 0.1, "coingecko")}
    monkeypatch.setattr(bw, "scoped_projects", lambda: [wm])
    bw._native_fee_usd_views(groups)
    usd = groups[("World Mobile", "own_chain_fees_usd")]
    assert list(usd["value"]) == [pytest.approx(1_580.365)] and ("World Mobile", "fees_usd") not in groups
    assert wm["published_headlines"][0] == ("2026-07-23", "AirNodes deployed", "160,000+")


# ======================================================================================== 9 MISSING triage
def test_9_missing_cells_carry_their_cause_and_the_scoreboard_counts_populated():
    import coverage_audit as ca
    # G8 item 2 replaced G7's causes with classes A–E (coverage_audit.MISSING_CLASSES); the G7 cases, re-classed
    D, E = ca.MISSING_CLASSES[3], ca.MISSING_CLASSES[4]
    assert ca.missing_cause("no rows stored", {"suggestion": "python token_metrics.py --seed aero_buyback"}) == D
    assert ca.missing_cause("no rows stored", {"reason": "never published"}) == E
    assert ca.missing_cause("nothing stored", {"suggestion": "paid source — Jake's decision"}) == E
    assert ca.missing_cause("fees_usd: nothing stored", None) == D
    rows = [{"Project": "Sky", "Status": "N/A BY DESIGN", "Reason / route / error": "n/a — x_tokens (no rows stored in Q0)"},
            {"Project": "Sky", "Status": "LIVE-UNVERIFIED", "Reason / route / error": ""},
            {"Project": "Sky", "Status": "N/A BY DESIGN", "Reason / route / error": "n/a — another archetype"}]
    ca.mark_missing(rows, [{"project": "Sky", "metric": "x_tokens", "reason": "r", "suggestion": "--seed x"}])
    assert rows[0]["Status"] == "MISSING" and rows[0]["Reason / route / error"].startswith(f"MISSING [{D}]")
    b = ca.scoreboard(rows)["Sky"]
    assert b["Populated"] == "1/2 = 50%" and b["MISSING by cause"] == f"{D} 1"


def test_9_missing_triage_is_a_registered_check():
    import check_offline_items as coi
    assert coi.missing_triage in coi.CHECKS


# ======================================================================================== 11 changed adapter re-reads
def test_11_a_changed_adapter_has_its_daily_marks_cleared(tmp_path, monkeypatch):
    import fetch
    from fetch.logcache import DailyChecks
    src = tmp_path / "adapter.py"
    src.write_text("v1")
    monkeypatch.setattr(fetch, "adapter_files", lambda: {"wm_supply": (str(src), "wm_supply")})
    d = DailyChecks(tmp_path)
    d.done("wm_supply:World Mobile", "2026-10-19")
    d.done("sky_msc:Sky:net_revenue_msc_usd", "2026-10-19")
    said = []
    assert fetch.refresh_changed_adapters(daily=d, root=tmp_path, say=said.append) == [("wm_supply", 1)]  # no record
    d.done("wm_supply:World Mobile", "2026-10-19")
    assert fetch.refresh_changed_adapters(daily=d, root=tmp_path) == []
    assert d.get("wm_supply:World Mobile") == "2026-10-19"         # unchanged code: the mark stands
    src.write_text("v2")
    assert fetch.refresh_changed_adapters(daily=d, root=tmp_path) == [("wm_supply", 1)]
    assert d.get("wm_supply:World Mobile") is None and d.get("sky_msc:Sky:net_revenue_msc_usd") == "2026-10-19"
    assert "ADAPTER CHANGED wm_supply" in said[0]


def test_11_every_registered_tier_resolves_to_its_adapter_file():
    import fetch
    monkey = fetch.adapter_files()
    assert set(monkey) == {t for t, _, _ in fetch.TIER_ORDER}
    assert monkey["wm_supply"][1] == "wm_supply" and monkey["wm_supply"][0].endswith("wm_supply.py")


# ======================================================================================== 1 NEAR measuring point
def test_1_near_measured_series_takes_over_only_after_the_overlap_check(monkeypatch):
    import build_workbook as bw
    spec = config.issuance_primary("Near")
    assert spec["history_metric"] == "gross_issuance_tokens_bigquery"
    assert spec["overlap_check"] == {"min_days": 3, "tolerance_pct": 1.0}
    asof = pd.Timestamp("2026-10-19")
    der = _rows("Near", "gross_issuance_tokens", pd.date_range("2025-09-01", "2026-10-18"), 90_000.0,
                "derived:d_total_supply_protocol+burn")
    bq = _rows("Near", "gross_issuance_tokens_bigquery", pd.date_range("2025-10-01", "2026-10-12"), 89_000.0,
               "near_bigquery:blocks.total_supply")
    rpc = _rows("Near", "gross_issuance_tokens", pd.date_range("2026-10-08", "2026-10-18"), 89_000.0,
                "near_issuance:near_rpc:epoch_mint[3 epochs]")
    observed = pd.concat([der, rpc], ignore_index=True)
    out = bw._measured_with_history("Near", spec, observed, {("Near", "gross_issuance_tokens_bigquery"): bq}, asof)
    chk = bw.ISSUANCE_OVERLAP["Near"]
    assert chk["passed"] and chk["common_days"] == 5 and "primary from 2026-10-08" in chk["used"]
    o = out.set_index("date")["source"]
    assert o[pd.Timestamp("2025-09-15")].startswith("derived:")                      # before the history leg
    assert o[pd.Timestamp("2026-06-30")].startswith("near_bigquery:")
    assert o[pd.Timestamp("2026-10-18")].startswith("near_issuance:") and out["date"].is_unique
    q = chk["q0_cross_check"]
    assert q["days"] == 89 and q["derived_vs_measured_pct"] == pytest.approx(90 / 89 * 100 - 100)
    # the primary off by 2% on the overlap: the history leg alone stands — never a blank Q0
    off = pd.concat([der, rpc.assign(value=89_000.0 * 1.02)], ignore_index=True)
    out = bw._measured_with_history("Near", spec, off, {("Near", "gross_issuance_tokens_bigquery"): bq}, asof)
    assert not bw.ISSUANCE_OVERLAP["Near"]["passed"] and "history leg alone" in bw.ISSUANCE_OVERLAP["Near"]["used"]
    assert out["date"].max() == pd.Timestamp("2026-10-12") and not out.empty


# ======================================================================================== 2 Trajectory
def test_2a_short_history_never_stored_lists_only_the_projects_own_inputs():
    import check_offline_items as coi
    got = coi.project_inputs("Pendle", ("net_protocol_surplus_usd", "revenue_usd", "fdv_usd"))
    assert "net_protocol_surplus_usd" not in got and "revenue_usd" in got


def test_2b_price_history_covers_every_series_that_needs_a_price():
    from fetch import price_history as ph
    need = ph.price_needing_metrics()
    assert {"total_supply", "gross_burn_tokens", "fees_usd"} <= need and "price_usd" not in need
    stored = pd.DataFrame({"project": "Ethereum", "metric": ["total_supply", "price_usd"],
                           "date": pd.to_datetime(["2016-01-01", "2025-10-19"]), "value": [1.0, 2.0],
                           "source": ["ultrasound", "coingecko"]})
    days, first = ph.missing_price_days(stored, "Ethereum")
    assert days and days[0] == pd.Timestamp("2016-01-01")


def test_2c_quarterly_views_build_every_shifted_quarter_like_q0(monkeypatch):
    import build_workbook as bw
    monkeypatch.setattr(config, "PROTOCOL_YIELD", {"Sky": {}})
    asof = pd.Timestamp("2026-10-19")
    groups = {("Sky", "x"): _rows("Sky", "x", pd.date_range("2025-06-01", "2026-10-18"), 1.0, "s")}

    def one_row(g, a):
        g[("Sky", "y")] = _rows("Sky", "y", [a], float(len(g[("Sky", "x")])), "derived:y")
    bw._quarterly_views(one_row, groups, asof)
    y = groups[("Sky", "y")].set_index("date")["value"]
    assert len(y) == 5 and y[asof] > y[asof - pd.Timedelta(days=90)]


def test_2e_near_and_ethereum_total_supply_take_the_declared_older_leg():
    assert set(config.TOTAL_SUPPLY_HISTORY) == {"Near", "Ethereum"}
    ho = {n: config.PROJECT_BY_NAME[n]["series_handover"]["total_supply"]["ordered_points"] for n in ("Near", "Ethereum")}
    assert ho["Near"][0].startswith("near_rpc:") and ho["Ethereum"][0].startswith("ultrasound:")


# ======================================================================================== 3 period labels
def test_3_period_end_ages_start_stamped_rows_by_their_period_end():
    assert config.period_end("GEODNET", "gross_burn_tokens", "2026-09-01") in (pd.Timestamp("2026-09-30"),
                                                                              pd.Timestamp("2026-09-01"))
    monthly = next((p["name"], m) for p in config.PROJECTS for m in config.metrics_for_project(p)
                   if config.series_granularity(p["name"], m) == "monthly")
    assert config.period_end(*monthly, "2026-09-01") == pd.Timestamp("2026-09-30")
    assert config.period_end(*monthly, "2026-09-30") == pd.Timestamp("2026-09-30")       # already at its end
    assert config.period_end("Aethir", "compute_hours_weekly", "2026-10-06") == pd.Timestamp("2026-10-12")
    assert config.period_end("Sky", "price_usd", "2026-10-06") == pd.Timestamp("2026-10-06")


def test_3_aethir_month_references_read_the_months_own_row():
    import references as refs
    s = _rows("Aethir", "locked_tokens_ai", ["2026-09-01", "2026-09-29", "2026-09-30"], [413_087_226.69, 1.0, 2.0],
              "aethir")
    got = refs._our_value(("on_period", "locked_tokens_ai", "month"), {("Aethir", "locked_tokens_ai"): s}, "Aethir",
                          pd.Timestamp("2026-09-30"), 413_087_226.69, None)
    assert got[0] == pytest.approx(413_087_226.69) and "period row" in got[1]


def test_3_a_series_closed_on_its_block_record_is_not_stale(tmp_path):
    import check_offline_items as coi
    con = _store(tmp_path, [])
    blk = config.PROJECT_BY_NAME["Maple"].get("pool_release_tokens_blocked") or {}
    if str(blk.get("status", "")).upper().startswith("CLOSED"):
        assert coi._not_stale(config, con, "Maple", "pool_release_tokens", "2026-09-01").upper().startswith("CLOSED")


# ======================================================================================== 4 reliability
def test_4b_alchemy_pages_are_checkpointed_and_resume(tmp_path):
    from fetch.explorer import AlchemyCheckpoint
    cp = AlchemyCheckpoint(80094, {"fromBlock": "0x1", "contractAddresses": ["0xabc"]}, root=tmp_path)
    assert cp.load() is None
    cp.append([{"blockNumber": 16, "logIndex": 0}, {"blockNumber": 17, "logIndex": 0}], "k1")
    cp.append([{"blockNumber": 18, "logIndex": 0}], "k2")
    got, key, last = cp.load()
    assert len(got) == 3 and key == "k2" and last == 18
    same = AlchemyCheckpoint(80094, {"fromBlock": "0x1", "contractAddresses": ["0xabc"], "pageKey": "k2"}, root=tmp_path)
    assert same.path == cp.path                                       # the pageKey is not part of the query's identity
    cp.expire_page_key([])
    got, key, last = cp.load()
    assert key is None and last == 18 and len(got) == 2               # restart at the last whole block
    cp.clear()
    assert cp.load() is None
    assert "alchemy" not in str(cp.path).split("alchemy-pages")[1]      # no URL (and so no key) in the file name


def test_4a_rwa_is_one_protocols_call_once_every_chain_has_history(monkeypatch, tmp_path):
    from fetch.llama import DefiLlama
    from fetch.logcache import DailyChecks
    chains = [p for p in config.PROJECTS if p.get("defillama_chain") and 1 in p["archetypes"]]
    stored = pd.DataFrame([{"project": p["name"], "metric": "rwa_defillama_usd", "date": pd.Timestamp("2026-10-01"),
                            "value": 1.0} for p in chains])
    d = DefiLlama(stored_long=stored, part="tvl")
    d.daily = DailyChecks(tmp_path)
    eth = config.PROJECT_BY_NAME["Ethereum"]["defillama_chain"]
    calls = []

    class H:
        def get(self, url, params=None, **_):
            calls.append(url)
            assert url.endswith("/protocols"), "no per-protocol history call once history is stored"
            return [{"category": "RWA", "chains": [eth], "chainTvls": {eth: 10.0}},
                    {"category": "RWA Lending", "chains": [eth], "chainTvls": {eth: 5.0}},
                    {"category": "Dexs", "chains": [eth], "chainTvls": {eth: 99.0}}]
    d.http = H()
    out = FetchOutput()
    d.rwa(chains, 30, out)
    f = out.frame()
    assert len(calls) == 1 and f[f["project"] == "Ethereum"]["value"].tolist() == [15.0]
    assert set(f["source"]) == {"defillama"}


# ======================================================================================== 6 Trajectory primary checks
def test_6_primary_checks_grade_q0_and_q1_from_dated_events():
    import coverage_audit as ca
    rows = [{"project": "Aerodrome", "tab": "Master", "hid": "master_gross_issuance_q0_tokens",
             "Q0": 53_622_724.80211853, "Q-1": 48_020_286.22841778},
            {"project": "Sky", "tab": "A4", "hid": "a4_gross_burn", "Q0": 10_233_231.76, "Q-1": None}]
    got = ca.trajectory_primary_checks(rows, "2026-10-13")
    assert got[("Aerodrome", "Master", "master_gross_issuance_q0_tokens")][0] == "VERIFIED"
    assert "13 event(s)" in got[("Aerodrome", "Master", "master_gross_issuance_q0_tokens")][1]
    assert got[("Sky", "A4", "a4_gross_burn")][0] == "VERIFIED"
    rows[0]["Q-1"] *= 1.01
    assert ca.trajectory_primary_checks(rows, "2026-10-13")[("Aerodrome", "Master",
                                                              "master_gross_issuance_q0_tokens")][0] == "BROKEN"
    # a window past what was read is not judged: Sky's Q0 to 10-19 reaches past its read (10-18)
    assert ("Sky", "A4", "a4_gross_burn") not in ca.trajectory_primary_checks(rows, "2026-10-19")
