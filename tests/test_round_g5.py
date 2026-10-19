"""
tests/test_round_g5.py — Round G5 (Jake 2026-10-19), SPEC §19 "G5 — Round G5, old → new". Decisions D1 and D2 first, then
one test (or more) per item, each named for its row.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402


# ======================================================================================================== D1 Sky MSC
_MSC = """# Sky — Monthly Settlement Cycle summary

## Sky Net Revenue

| Line | USDS |
|---|---:|
| MSC net (accrual) | 13,927,348.22 |
| non-MSC net | 708,599.21 |
| **Sky Net Revenue** | **14,635,947.43** |
"""


class _Daily:
    def __init__(self, due=True):
        self._due, self.marked = due, []

    def due(self, key, day):
        return self._due

    def done(self, key, day):
        self.marked.append(key)


def test_d1_parse_summary_reads_the_total_and_its_basis():
    from fetch.sky_msc import parse_summary
    assert parse_summary(_MSC) == (14_635_947.43, "accrual")
    assert parse_summary(_MSC.replace("(accrual)", "(buffer basis)"))[1] == "buffer basis"
    assert isinstance(parse_summary("# nothing here"), str)                      # refused, said why
    assert isinstance(parse_summary(_MSC.replace("**Sky Net Revenue**", "Sky Net Revenue")), str)


def test_d1_sky_msc_stores_each_month_at_month_end_with_its_basis_and_skips_unpublished(monkeypatch):
    from fetch import sky_msc
    monkeypatch.setattr(sky_msc, "today", lambda: pd.Timestamp("2026-10-19"))
    pages = {"2026-07": _MSC.replace("14,635,947.43", "10,517,425.81"),
             "2026-08": _MSC.replace("14,635,947.43", "15,745,296.07"),
             "2026-09": _MSC,
             "2026-06": _MSC.replace("(accrual)", "(buffer basis)").replace("14,635,947.43", "14,875,074.63")}
    daily = _Daily()
    a = sky_msc.SkyMsc(get_text=lambda u: pages.get(u.split("/sky_total/")[1][:7]), daily=daily,
                       robots=lambda u: (True, ""))
    out = FetchOutput()
    a.run([{"name": "Sky"}], None, out)
    df = pd.concat(out.frames)
    assert set(df.metric) == {"net_revenue_msc_usd"}
    by = {str(d.date()): (v, s) for d, v, s in zip(df.date, df.value, df.source)}
    assert by["2026-09-30"][0] == 14_635_947.43 and "[accrual]" in by["2026-09-30"][1]
    assert by["2026-06-30"][0] == 14_875_074.63 and "[buffer basis]" in by["2026-06-30"][1]
    assert "2026-10-31" not in by                                       # the month in progress is never asked
    assert any("not published" in e.message for e in out.log)            # Jan–May: no file — said, not a gap
    assert not out.gaps and daily.marked == ["sky_msc:Sky:net_revenue_msc_usd"]


def test_d1_sky_msc_once_a_day_marks_current_and_a_failed_month_is_not_marked(monkeypatch):
    from fetch import sky_msc
    monkeypatch.setattr(sky_msc, "today", lambda: pd.Timestamp("2026-10-19"))
    out = FetchOutput()
    sky_msc.SkyMsc(get_text=lambda u: _MSC, daily=_Daily(due=False), robots=lambda u: (True, "")).run(
        [{"name": "Sky"}], None, out)
    assert ("Sky", "net_revenue_msc_usd") in out.current and not out.frames

    def boom(u):
        raise RuntimeError("503")
    daily, out = _Daily(), FetchOutput()
    sky_msc.SkyMsc(get_text=boom, daily=daily, robots=lambda u: (True, "")).run([{"name": "Sky"}], None, out)
    assert not daily.marked and any(e.status == "failed" for e in out.log)


def test_d1_metric_cadence_tier_and_reference_routes():
    import fetch
    import references as rf
    assert "net_revenue_msc_usd" in config.METRICS
    assert config.series_granularity("Sky", "net_revenue_msc_usd") == "monthly"
    assert any(t[0] == "sky_msc" for t in fetch.TIER_ORDER) and fetch.TIER_BUDGET_S["sky_msc"] == 60
    for m in ("net_revenue_jul_2026", "net_revenue_aug_2026", "net_revenue_sep_2026"):
        assert rf.ROUTES[("Sky", m)] == ("on_day", "net_revenue_msc_usd")
    rows = {r["metric"]: r for r in rf.load() if r["project"] == "Sky"}
    assert float(rows["net_revenue_jul_2026"]["value"]) == 10_517_425.81
    assert float(rows["net_revenue_aug_2026"]["value"]) == 15_745_296.07
    assert all(rows[m]["tolerance"] == "exact" for m in ("net_revenue_jul_2026", "net_revenue_aug_2026",
                                                         "net_revenue_sep_2026"))


def test_d1_a_restated_month_reads_broken_not_basis():
    import references as rf
    g = {("Sky", "net_revenue_msc_usd"): pd.DataFrame(
        {"date": [pd.Timestamp("2026-09-30")], "value": [14_600_000.0],
         "source": ["sky_msc:soterlabs/settlement-cycle sky_total[accrual]"]})}
    r = {x["metric"]: x for x in rf.check(g)}["net_revenue_sep_2026"]
    assert r["status"] == "BROKEN" and "restated" in r["detail"]


def test_d1_basis_rows_label_every_shared_month_with_its_numbers():
    import references as rf
    ba = pd.DataFrame({"date": pd.to_datetime(["2026-08-31", "2026-09-30"]), "value": [16_000_000.0, 15_379_185.0],
                       "source": "sky_accounting:pnl.revenue-expense"})
    msc = pd.DataFrame({"date": pd.to_datetime(["2026-08-31", "2026-09-30"]), "value": [15_745_296.07, 14_635_947.43],
                        "source": "sky_msc:soterlabs/settlement-cycle sky_total[accrual]"})
    rows = rf.basis_rows({("Sky", "net_revenue_pre_distribution_usd"): ba, ("Sky", "net_revenue_msc_usd"): msc})
    assert [r["metric"] for r in rows] == ["net_revenue_ba_vs_msc_2026-08", "net_revenue_ba_vs_msc_2026-09"]
    assert all(r["status"] == "N/A BY DESIGN" and rf.BASIS_LABEL in r["detail"] for r in rows)
    assert "+5.08%" in rows[1]["detail"] and "accrual" in rows[1]["detail"]
    # no Sky in the build, or one series absent: nothing invented
    assert rf.basis_rows({("Sky", "net_revenue_msc_usd"): msc}) == []
    assert rf.basis_rows({}, projects={"Sky"}) == []


# ================================================================================================ D2 VERIFIED (derived)
def test_d2_derived_is_its_own_status_and_never_counts_as_verified():
    import coverage_audit as ca
    assert "VERIFIED (derived)" in ca.STATUSES and "VERIFIED (derived)" in ca.BOARD_COLS
    rows = [{"Project": "X", "Status": "VERIFIED"}, {"Project": "X", "Status": "VERIFIED (derived)"},
            {"Project": "X", "Status": "LIVE-UNVERIFIED"}]
    assert ca.verified_share(rows)["X"][:2] == (1, 3)
    board = ca.scoreboard([dict(r, Tab="t", Column="c", Key="k", **{"MATCHES-SPEC": "yes"}) for r in rows])
    assert board["X"]["VERIFIED"] == 1 and board["X"]["VERIFIED (derived)"] == 1
    assert board["X"]["Verified share"] == "1/3 = 33%"


def test_d2_the_rule_needs_every_input_green_and_the_formula_matching():
    src = (ROOT / "coverage_audit.py").read_text()
    blk = src[src.index("# D2 (Jake 2026-10-19)") - 300:src.index("rows.append({", src.index("# D2 (Jake 2026-10-19)"))]
    assert 'status == "LIVE-UNVERIFIED"' in blk and 'match.startswith("yes")' in blk
    assert "all(series_green(proj, m) for m in reads)" in blk


# ================================================================================================ item 1 lost VERIFIED
def _cov(status, project="Fluid", col="Fees"):
    return {"Tab": "A3", "Column": col, "Key": "fees", "Project": project, "Status": status,
            "Reason / route / error": "band: flagged in the Review Queue"}


def test_1_lost_verified_names_each_cell_and_board_delta_counts_the_change():
    import coverage_audit as ca
    prev = [_cov("VERIFIED"), _cov("VERIFIED", col="Revenue"), _cov("LIVE-UNVERIFIED", col="TVL")]
    now = [_cov("LIVE-UNVERIFIED"), _cov("VERIFIED", col="Revenue"), _cov("VERIFIED", col="TVL")]
    lost = ca.lost_verified(prev, now)
    assert [r["Column"] for r in lost] == ["Fees"] and "Review Queue" in lost[0]["Reason / route / error"]
    d = ca.board_delta({"ALL": {"VERIFIED": 3, "MISSING": 0, "Verified share": "x"}},
                       {"ALL": {"VERIFIED": 5, "MISSING": 2}})
    assert d["ALL"] == {"VERIFIED": -2, "MISSING": -2}
    assert ca.board_delta({"ALL": {"VERIFIED": 3}}, None) == {"ALL": {}}            # first build: no delta


def test_1_an_accepted_review_row_no_longer_flags_its_series():
    src = (ROOT / "build_workbook.py").read_text()
    i = src.index("review_keys = {(r[\"project\"], r[\"metric\"]) for r in review.to_dict(\"records\")")
    assert "ACCEPT" in src[i:i + 600]


def test_1_scoreboard_lines_and_lost_lines_reach_the_run_log():
    src = (ROOT / "build_workbook.py").read_text()
    assert "lost_verified" in src and "board_delta" in src and "SCOREBOARD_LINES" in src


# ================================================================================================ item 2 starved scans
def test_2_headline_scans_hold_a_reserve_no_other_scan_can_eat():
    from fetch import logscan
    assert logscan.RESERVE_SHARE == 0.6
    m = config.PROJECT_BY_NAME["Morpho"]
    spec = next(s for s in m.get("log_scans") or () if s.get("key") == "merkl_morpho_out_base") \
        if any(s.get("key") == "merkl_morpho_out_base" for s in m.get("log_scans") or ()) else None
    if spec is not None:
        assert config.scan_feeds_headline("Morpho", spec)
        assert config.scan_reserve_s("Morpho", spec) == config.HEADLINE_SCAN_RESERVE_S
    assert config.scan_reserve_s("X", {"reserve_s": 5}) == 5.0
    assert config.scan_reserve_s("X", {"metric": "some_context_series"}) == 0.0
    assert "cashback_paid_tokens" in config.HEADLINE_SCAN_METRICS


def test_2_a_scan_stopped_on_its_share_is_retried_in_pass_2():
    src = (ROOT / "fetch" / "logscan.py").read_text()
    assert 'return "budget"' in src and "completed in PASS 2" in src
    assert '== "budget" and tier_deadline is not None' in src


def test_2_alchemy_routes_first_where_etherscan_is_paid_only_or_slow():
    assert {8453, 56, 10, 80094, 999} == config.TRANSFER_FIRST_CHAINS
    src = (ROOT / "fetch" / "explorer.py").read_text()
    assert "(tr + order) if int(chain_id) in config.TRANSFER_FIRST_CHAINS else (order + tr)" in src
    assert "e.resume_from" in src and "e.partial" in src                  # an Alchemy stop keeps what it read


class _Reader:
    def __init__(self):
        self.calls = 0

    def block_timestamp(self, chain, b):
        self.calls += 1
        return 1_760_000_000 + (b - 100) * 2                              # 2 s blocks


def test_2_undated_events_are_dated_from_the_chain_by_bracketing():
    from fetch.logscan import LogScan
    ls = LogScan.__new__(LogScan)
    ls.reader = _Reader()
    evs = [{"blockNumber": str(b), "timeStamp": 0} for b in (100, 150, 200)] + [{"blockNumber": "90",
                                                                                    "timeStamp": 1_759_999_000}]
    out = ls._date_undated("berachain", evs)
    assert all(int(e["timeStamp"]) > 0 for e in out)
    assert out[-1]["timeStamp"] == 1_759_999_000                           # a dated event is left as it was
    assert ls.reader.calls <= 3

    class _NoReader:
        pass
    ls.reader = _NoReader()
    left = ls._date_undated("berachain", [{"blockNumber": "1", "timeStamp": 0}])
    assert left[0]["timeStamp"] == 0                                       # never guessed


def test_2_morpho_base_seed_is_offered_and_resumes():
    src = (ROOT / "token_metrics.py").read_text()
    assert '"morpho_base_claims"' in src and "def seed_morpho_base_claims" in src
    blk = src[src.index("def seed_morpho_base_claims"):src.index("def seed_plume_payouts")]
    assert "unbounded=True" in blk and "merkl_morpho_out_base" in blk and "NOT WIRED" in blk


def test_2_katana_up_to_date_is_not_a_gap():
    src = (ROOT / "fetch" / "logscan.py").read_text()
    assert src.count("out.mark_current(") >= 2


# ================================================================================================ item 3 budgets
def test_3_defillama_is_split_into_fees_and_tvl_tiers_each_stamped_on_its_own():
    import fetch
    from fetch import gaps
    names = [t[0] for t in fetch.TIER_ORDER]
    assert "defillama" in names and "defillama_tvl" in names
    p = {"name": "X", "defillama_fees_slug": "x", "defillama_protocol": "x", "defillama_chain": "X"}
    assert "fees_usd" in gaps.served_by("defillama", p) and "protocol_tvl_usd" not in gaps.served_by("defillama", p)
    assert "protocol_tvl_usd" in gaps.served_by("defillama_tvl", p) and "fees_usd" not in gaps.served_by(
        "defillama_tvl", p)


def test_3_price_history_remembers_empty_spans_and_stops_before_its_budget(tmp_path, monkeypatch):
    from fetch import price_history as ph
    assert ph.EMPTY_RECHECK_DAYS == 30 and ph.STOP_BEFORE_S == 15.0
    monkeypatch.setattr(ph, "_empty_path", lambda: tmp_path / "price_history_empty.json")
    assert ph._load_empty() == {}
    ph._save_empty({"Aethir|2023-01-01": "2026-10-19"})
    assert ph._load_empty() == {"Aethir|2023-01-01": "2026-10-19"}


# ================================================================================================ item 4 stale series
def _con():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE metrics (date TEXT, project TEXT, metric TEXT, value REAL, source TEXT)")
    return con


def test_4_not_stale_names_closed_retired_and_declared_curve_series():
    import check_offline_items as co
    con = _con()
    assert co._not_stale(config, con, "Pendle", "max_supply", "2026-01-01").startswith("CLOSED 2026-10-19")
    assert co._not_stale(config, con, "Plume", "price_usd_coinbase", "2026-10-05").startswith("RETIRED ROUTE")
    assert co._not_stale(config, con, "Plume", "fees_usd", "2026-10-05") == ""          # no table, nothing to excuse


def test_4_business_marks_only_when_the_last_complete_day_is_stored():
    assert co_lag() == 2
    src = (ROOT / "fetch" / "business.py").read_text()
    i = src.index("pd.Timestamp(rows[-1][0]).normalize() >= pd.Timestamp(last_complete()).normalize()")
    assert "self.daily.done(dkey, day)" in src[i:i + 200] and "NOT marked read today" in src[i:i + 800]


def test_4_business_short_read_is_stored_and_not_marked(monkeypatch):
    from fetch import business

    class D:
        def __init__(self):
            self.marked = []

        def due(self, k, d):
            return True

        def done(self, k, d):
            self.marked.append(k)

        def ever(self, k):
            return True
    monkeypatch.setattr(config, "BUSINESS_SERIES", {"X": [{"metric": "fees_usd"}]})
    monkeypatch.setattr(config, "MARKET_SHARE", {})
    monkeypatch.setattr(business, "last_complete", lambda: pd.Timestamp("2026-10-17"))
    b = business.Business.__new__(business.Business)
    b.daily = D()
    for newest, marked in (("2026-10-16", False), ("2026-10-17", True)):
        b.daily.marked = []
        monkeypatch.setattr(b, "_series", lambda name, spec, key, n=newest: (
            [(pd.Timestamp("2026-10-15"), 1.0), (pd.Timestamp(n), 2.0)], "defillama:x", "ok"), raising=False)
        out = FetchOutput()
        b.run([{"name": "X"}], None, out)
        assert bool(b.daily.marked) is marked
        assert any("NOT marked read today" in e.message for e in out.log) is (not marked)


def co_lag():
    import check_offline_items as co
    return co.MARK_EXPECTED_LAG_DAYS["business"]


def test_4_dune_monthly_crosscheck_names_its_parent_query():
    from fetch import gaps
    p = config.PROJECT_BY_NAME.get("GEODNET")
    if p is None:
        pytest.skip("GEODNET not configured")
    for parent, q in (p.get("dune_queries") or {}).items():
        m = config.monthly_crosscheck_metric("GEODNET", parent)
        if q.get("query_id") and m in config.METRICS and 4 in config.METRICS[m].get("tiers", []):
            reason, fix = gaps._tier_note(p, m, {})
            assert str(q["query_id"]) in reason and "TOKEN_METRICS_DUNE_ALWAYS=1" in fix
            return
    pytest.skip("no monthly cross-check leg declared")


def test_4_voter_and_spendle_epochs_are_not_stale_inside_two_epochs():
    assert config.stale_after_days("Aerodrome", "voter_rewards_usd", 7) >= 7


def test_4_cz_cleanup_section_is_dry_run_first_and_not_future_stamped():
    sql = (ROOT / "orphan_cleanup.sql").read_text()
    i = sql.index("CZ1")
    blk = sql[i - 400:i + 3000]
    assert "SELECT" in blk and "-- DELETE" in blk.replace("--DELETE", "-- DELETE")
    assert "price_usd_coinbase" in blk and "2026-10-19T10:00Z" in sql


# ================================================================================================ item 5 status honesty
def test_5g_missing_route_na_cells_become_missing_with_route_and_fix():
    import coverage_audit as ca
    rows = [{"Project": "Plume", "Status": "N/A BY DESIGN",
             "Reason / route / error": "n/a — fees_usd (no rows stored for this window)"},
            {"Project": "Plume", "Status": "N/A BY DESIGN", "Reason / route / error": "n/a — fixed supply"}]
    gaps_ = [{"project": "Plume", "metric": "fees_usd", "reason": "DefiLlama has no Plume fees slug",
              "suggestion": "add defillama_fees_slug"}]
    assert ca.mark_missing(rows, gaps_) == 1
    assert rows[0]["Status"] == "MISSING" and "ROUTE: DefiLlama" in rows[0]["Reason / route / error"]
    assert "FIX: add defillama_fees_slug" in rows[0]["Reason / route / error"]
    assert rows[1]["Status"] == "N/A BY DESIGN"
    assert "MISSING" in ca.BOARD_COLS


def test_5h_spec_coverage_is_a_check():
    import check_offline_items as co
    assert co.spec_coverage in co.CHECKS


def test_4_plume_payouts_seed_reads_by_rpc_and_halves_on_refusal():
    src = (ROOT / "fetch" / "plume_staking.py").read_text()
    blk = src[src.index("def seed_payouts_rpc"):]
    blk = blk[:blk.index("\n    def ", 10)] if "\n    def " in blk[10:] else blk
    assert "-32005" in blk and "date_blocks" in blk and "plume-payouts.json" in blk
    tm = (ROOT / "token_metrics.py").read_text()
    assert '"plume_payouts"' in tm and "seed_payouts_rpc" in tm


# ================================================================================================ item 6 gap routes
def _frame(pairs):
    return pd.DataFrame([{"project": p, "metric": m, "date": "2026-10-18", "value": 1.0} for p, m in pairs])


def test_6_usd_twin_and_net_total_and_buyback_parts_cover_their_metrics():
    from fetch import gaps
    hl = config.PROJECT_BY_NAME["Hyperliquid"]
    have_usd = _frame([("Hyperliquid", "buyback_hypercore_asxn_usd"), ("Hyperliquid", "total_supply_gross")])
    rows = gaps.detect([hl], have_usd, set(), {}, [])
    missing = {r["metric"] for r in rows}
    assert "buyback_hypercore_asxn_tokens" not in missing
    if "Hyperliquid" in config.TOTAL_SUPPLY_NET:
        assert "total_supply" not in missing


def test_6_first_party_circulating_names_each_projects_own_route():
    from fetch import gaps
    for name, word in (("Hyperliquid", "tokenDetails"), ("Aethir", "athCirculatingSupply")):
        p = config.PROJECT_BY_NAME[name]
        reason, _fix = gaps._tier_note(p, "circulating_supply_first_party", {})
        assert word in reason, (name, reason)
    near = config.PROJECT_BY_NAME["Near"]
    reason, _fix = gaps._tier_note(near, "circulating_supply_first_party", {})
    assert "tokenDetails" not in reason


def test_6_max_supply_closed_for_the_uncapped_tokens():
    closed = {(u["project"], u["metric"]): u for u in config.UNAVAILABLE}
    for p in ("Pendle", "Aerodrome", "Maple"):
        assert closed[(p, "max_supply")]["summary"].startswith("NO MAX SUPPLY")


def test_6_new_actionable_signals():
    from fetch import gaps
    sig = " ".join(gaps.ACTIONABLE_SIGNALS)
    assert "scan timed out after" in sig and "is not assembled yet" in sig


# ================================================================================================ item 8 carried over
def test_8a_pool_release_over_more_than_one_day_is_not_stored():
    src = (ROOT / "fetch" / "__init__.py").read_text()
    blk = src[src.index("def _derive_pool_release"):]
    assert "is not one day's release — NOT STORED" in blk[:12000]


def test_8b_base_rpc_falls_back_to_alchemy_with_the_key_never_printed(monkeypatch):
    import check_offline_items as co
    from fetch.base import redact
    monkeypatch.delenv("BASE_RPC_URL", raising=False)
    monkeypatch.setenv("ALCHEMY_API_KEY", "TESTKEY0123456789abcdef")
    urls = co._rpcs_for("base")
    assert urls[0].startswith("https://base-mainnet.g.alchemy.com/v2/") and len(urls) > 1      # public ones behind it
    assert co._rpc_host(urls[0]) == "base-mainnet.g.alchemy.com"
    assert "TESTKEY" not in redact(f"error at {urls[0]}")
    monkeypatch.setenv("BASE_RPC_URL", "https://example.org/rpc")
    assert co._rpcs_for("base")[0] == "https://example.org/rpc"                                # BASE_RPC_URL wins


def test_8c_etherscan_limits_cite_the_docs():
    assert "docs.etherscan.io" in str(config.__dict__).replace("\\n", "") or "rate-limits" in (
        ROOT / "config.py").read_text()


def test_8d_master_prints_the_market_cap_date_only_when_it_differs():
    from build_workbook import _mcap_date_note
    d = {"X|market_cap_usd": {"latest_date": "2026-10-17"}, "X|price_usd": {"latest_date": "2026-10-19"}}
    assert _mcap_date_note(d, "X") == "as of 2026-10-17 (spot price 2026-10-19)"
    d["X|market_cap_usd"]["latest_date"] = "2026-10-19"
    assert _mcap_date_note(d, "X") == ""
    assert _mcap_date_note({}, "X") == ""


# ================================================================================================ standing rule /v2/
def test_never_print_anything_after_v2():
    from fetch.base import mask_v2, redact
    assert mask_v2("/v2/historicalChainTvl/Plume Mainnet") == "/v2/***"
    assert mask_v2("/protocol/morpho") == "/protocol/morpho"
    assert redact("slowest: /v2/historicalChainTvl/Plume 12.7s") == "slowest: /v2/*** 12.7s"
    assert redact("https://api.etherscan.io/v2/api?chainid=1&apikey=K") == "https://api.etherscan.io/v2/***"
    assert redact("https://base-mainnet.g.alchemy.com/v2/abc123") == "https://base-mainnet.g.alchemy.com"
