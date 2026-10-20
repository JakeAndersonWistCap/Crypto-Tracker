"""
tests/test_round_g8.py — Round G8 (Jake 2026-10-20), SPEC §19 "G8 — Round G8, old → new". One test (or more) per item,
each named for its row.
"""
from __future__ import annotations

import json
import sys
import time
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


# ======================================================================================== 0 measuring-point guard
def test_0_guard_flags_an_undeclared_basis_and_a_lost_headline():
    import measuring_guard as mg
    before = {"bases": {"Sky|x_tokens": ["chain:a"], "Hyperliquid|total_burn_tokens":
                        ["derived:burn_total(gross_burn_tokens+core_burn_tokens)"]},
              "headlines": {"Sky": {"A1 | head": 5.0}}}
    after = {"bases": {"Sky|x_tokens": ["chain:a", "chain:b"],
                       "Hyperliquid|total_burn_tokens": ["derived:burn_total(gross_burn_tokens+core_burn_tokens)"],
                       "Sky|new_series": ["chain:z"]},
             "headlines": {}}
    bad = mg.compare(before, after)
    assert any(b.startswith("BASIS Sky x_tokens") for b in bad)
    assert any(b.startswith("HEADLINE LOST Sky A1 | head") for b in bad)
    assert not any("new_series" in b for b in bad), "a new series is not a change of basis"
    # the G7 regression: the HyperEVM leg written into the source text is a THIRD basis, caught
    after["bases"]["Hyperliquid|total_burn_tokens"].append(
        "derived:burn_total(gross_burn_tokens+core_burn_tokens+hyperevm_priority_burn_tokens)")
    assert any("BASIS Hyperliquid total_burn_tokens" in b for b in mg.compare(before, after))


def test_0_a_declared_component_is_not_a_new_basis():
    import measuring_guard as mg
    import trajectory
    decl = mg._declared("Hyperliquid", "total_burn_tokens")
    base = "derived:burn_total(gross_burn_tokens+core_burn_tokens)"
    assert trajectory.basis_of("total_burn_tokens", base) in decl
    annotated = base + "[+hyperevm_priority_burn_tokens declared component from 2026-10-14]"
    assert trajectory.basis_of("total_burn_tokens", annotated) == trajectory.basis_of("total_burn_tokens", base)


def test_0_probe_rows_cover_every_stored_series_the_dump_lacks():
    import measuring_guard as mg
    pl = {"meta": {"project": "Hyperliquid", "asof": "2026-10-13"}, "metrics": [{"metric": "price_usd"}]}
    rows = mg.probe_rows(pl)
    probed = {r["metric"] for r in rows}
    assert "hyperevm_priority_burn_tokens" in probed and "price_usd" not in probed
    assert all(r["source"] == f"probe:{r['metric']}" for r in rows)
    assert not any((config.METRICS.get(m) or {}).get("view_only") for m in probed)
    assert len([r for r in rows if r["metric"] == "hyperevm_priority_burn_tokens"]) == mg.PROBE_DAYS


@pytest.mark.skipif(not (ROOT / "tests" / "fixtures" / "guard_baseline.json").exists(), reason="no committed baseline")
def test_0_measuring_point_guard_on_the_fixture_store(tmp_path):
    """THE GUARD (Jake, G8 item 0): build on the fixture store with probe rows and compare with the committed baseline —
    no series gains an undeclared basis and no headline that held a value loses it."""
    import measuring_guard as mg
    base = json.loads((ROOT / "tests" / "fixtures" / "guard_baseline.json").read_text(encoding="utf-8"))
    snap = mg.build(tmp_path)
    bad = mg.compare(base, snap)
    assert not bad, "\n".join(bad)


# ======================================================================================== 1 Hyperliquid A1 total
def test_1_hyperevm_burn_is_a_declared_component_with_one_basis_on_every_row(monkeypatch):
    import build_workbook as bw
    import trajectory
    hl = config.PROJECT_BY_NAME["Hyperliquid"]
    dc = config.DECLARED_COMPONENTS[("Hyperliquid", "total_burn_tokens")]
    assert [c["metric"] for c in dc] == ["hyperevm_priority_burn_tokens"]
    days = ["2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13"]
    groups = {("Hyperliquid", "gross_burn_tokens"): _rows("Hyperliquid", "gross_burn_tokens", days, 100.0, "hypercore"),
              ("Hyperliquid", "core_burn_tokens"): _rows("Hyperliquid", "core_burn_tokens", days, 10.0, "hypercore"),
              ("Hyperliquid", "hyperevm_priority_burn_tokens"): _rows(
                  "Hyperliquid", "hyperevm_priority_burn_tokens", ["2026-10-11", "2026-10-13"], 1.0, "chain:hyperevm")}
    monkeypatch.setattr(bw, "scoped_projects", lambda: [hl])
    bw._burn_total_views(groups)
    g = groups[("Hyperliquid", "total_burn_tokens")].set_index("date")
    assert list(g["value"]) == [110.0, 111.0, 110.0, 111.0]
    assert {trajectory.basis_of("total_burn_tokens", s) for s in g["source"]} == {
        trajectory.basis_of("total_burn_tokens", "derived:burn_total(gross_burn_tokens+core_burn_tokens)")}
    assert "declared component from 2026-10-11" in g["source"].iloc[1]
    # a day after the start that the component did not report is SAID, never a silent 0
    assert "hyperevm_priority_burn_tokens not read this day" in g["source"].iloc[2]
    assert "[" not in g["source"].iloc[0]
    a4 = groups[("Hyperliquid", "burn_ex_chain_fee_tokens")].set_index("date")["value"]
    assert list(a4) == [110.0] * 4


def test_1_the_48_42m_figure_is_gone_and_the_hypercore_reads_are_cited():
    text = (ROOT / "config.py").read_text(encoding="utf-8")
    assert "48.42M HYPE as of 6 Sep" not in text and "48,145,239.17" in text


# ======================================================================================== 2 MISSING triage A–E
def test_2_no_such_flow_reads_zero_with_its_evidence_only_where_every_quarter_is_empty(monkeypatch):
    import build_workbook as bw
    import trajectory
    monkeypatch.setitem(config.NO_SUCH_FLOW, ("Testco", "a"), {"evidence": "no validator set", "source": "docs",
                                                               "read_on": "2026-10-20"})
    line = config.FLOW_LEG_LINES["a"]
    empty = {"Project": "Testco", "Line": line, **{lab: None for lab in trajectory.LABELS}}
    got = bw._no_such_flow(empty)
    assert all(got[lab] == 0.0 for lab in trajectory.LABELS)
    assert got["Note"].startswith("0 — no such flow: no validator set (docs, read 2026-10-20)")
    held = dict(empty, **{trajectory.LABELS[0]: 5.0})
    assert bw._no_such_flow(held) is held, "a line with a value is never overwritten"
    other = {"Project": "Otherco", "Line": line, **{lab: None for lab in trajectory.LABELS}}
    assert bw._no_such_flow(other) is other, "no evidence: unknown is never 0"


def test_2_no_such_flow_entries_cite_a_primary_source_and_skip_unused_mint_capability():
    assert config.NO_SUCH_FLOW, "group A's verdicts are recorded"
    for (p, leg), ev in config.NO_SUCH_FLOW.items():
        assert leg in config.FLOW_LEG_LINES and p in config.PROJECT_BY_NAME, (p, leg)
        assert ev["evidence"] and ev["source"] and ev["read_on"] == "2026-10-20", (p, leg)
    for p in ("Uniswap", "Sky", "Morpho", "Plume", "Fluid"):        # a mint function never used measures 0
        assert (p, "m") not in config.NO_SUCH_FLOW, p
    assert ("Ethereum", "m") not in config.NO_SUCH_FLOW, "Jake's call: the EF solo-stakes"


def test_2_missing_cells_are_classed_with_evidence():
    import coverage_audit as ca
    assert ca.MISSING_CLASSES[0].startswith("A") and ca.MISSING_CLASSES[4].startswith("E")
    # B — the cascade Jake accepted for World Mobile goes back to N/A BY DESIGN, citing the decision
    rows = [{"Project": "World Mobile", "Tab": "A3", "Column": "x", "Status": "N/A BY DESIGN",
             "Reason / route / error": "n/a — actual_buyback_usd (no rows stored in Q0)"}]
    ca.mark_missing(rows, [])
    assert rows[0]["Status"] == "N/A BY DESIGN" and "accepted limit" in rows[0]["Reason / route / error"]
    # E — no public source
    assert ca.missing_cause("no rows stored", {"suggestion": "no public source — console needs a login"}) \
        == ca.MISSING_CLASSES[4]
    # D — a route that stored nothing
    assert ca.missing_cause("gross_burn_tokens: no rows stored", None) == ca.MISSING_CLASSES[3]


def test_2_needs_history_states_the_date_the_cell_becomes_valid():
    import coverage_audit as ca
    g = _rows("Pendle", "fees_usd", pd.date_range("2026-09-25", "2026-10-13"), 1.0, "defillama")
    row = {"Project": "Pendle", "Tab": "A3", "Column": "fees Q0", "Reason / route / error": "fees_usd short"}
    cls, ev = ca.missing_class(row, None, {("Pendle", "fees_usd"): g}, pd.Timestamp("2026-10-13"))
    assert cls == ca.MISSING_CLASSES[2] and "valid from 2026-12-23" in ev


def test_2_missing_triage_prints_counts_by_class():
    import inspect
    import check_offline_items as coi
    src = inspect.getsource(coi.missing_triage)
    assert "COUNTS BY CLASS PER PROJECT" in src and coi.missing_triage in coi.CHECKS


# ======================================================================================== 3a Aerodrome on DefiLlama
def test_3a_aerodrome_tvl_is_the_sum_of_the_three_children_read_from_the_listing():
    from fetch.llama import DefiLlama
    t = lambda d: int(pd.Timestamp(d).timestamp())  # noqa: E731
    listing = [{"id": "3450", "name": "Aerodrome V1", "slug": "aerodrome-v1", "parentProtocol": "parent#aero-protocol"},
               {"id": "4524", "name": "Aerodrome Slipstream", "slug": "aerodrome-slipstream",
                "parentProtocol": "parent#aero-protocol"},
               {"id": "8691", "name": "Aero Lite", "slug": "aero-lite", "parentProtocol": "parent#aero-protocol"},
               {"id": "3302", "name": "Velodrome V2", "slug": "velodrome-v2", "parentProtocol": "parent#aero-protocol"}]
    tvl = {"aerodrome-v1": [("2026-09-16", 10), ("2026-09-17", 11), ("2026-09-18", 12), ("2026-09-19", 13)],
           "aerodrome-slipstream": [("2026-09-16", 20), ("2026-09-17", 21), ("2026-09-18", 22), ("2026-09-19", 23)],
           "aero-lite": [("2026-09-18", 1), ("2026-09-19", 2)],
           "velodrome-v2": [("2026-09-19", 999)]}
    calls = []

    class H:
        def get(self, url, params=None):
            calls.append(url)
            if url.endswith("/protocols"):
                return listing
            slug = url.rsplit("/", 1)[1]
            return {"tvl": [{"date": t(d), "totalLiquidityUSD": v} for d, v in tvl[slug]]}
    a = DefiLlama()
    a.http = H()
    p = dict(config.PROJECT_BY_NAME["Aerodrome"])
    out = FetchOutput()
    a.protocol_tvl(p, None, out)
    got = out.frame().set_index("date")["value"]
    assert list(got) == [30.0, 32.0, 35.0, 38.0], "Aero Lite counts from its first day; Velodrome never"
    assert not any(c.endswith("/protocol/aerodrome") or "velodrome" in c for c in calls)
    assert list(out.frame()["source"].unique()) == ["defillama"], "the measuring point is unchanged"
    # a child missing a day inside its span refuses that day
    tvl["aerodrome-slipstream"] = [x for x in tvl["aerodrome-slipstream"] if x[0] != "2026-09-17"]
    a2 = DefiLlama()
    a2.http = H()
    out2 = FetchOutput()
    a2.protocol_tvl(p, None, out2)
    assert pd.Timestamp("2026-09-17") not in set(out2.frame()["date"])
    # G9 A2: a RENAMED child is still the same id — summed, its live name quoted (DefiLlama: Aero Lite -> Aero)
    listing[2]["name"] = "Aero"
    a3 = DefiLlama()
    a3.http = H()
    out3 = FetchOutput()
    a3.protocol_tvl(p, None, out3)
    assert not out3.frame().empty and "Aero (was Aero Lite)" in " ".join(e.message for e in out3.log)


def test_3a_aerodrome_market_share_names_its_children_not_the_retired_parent():
    ms = config.MARKET_SHARE["Aerodrome"]
    assert ms["own_names"] == ("Aerodrome V1", "Aerodrome Slipstream", "Aero Lite") and "own_parent" not in ms


# ======================================================================================== 3b ve_managed
def test_3b_ve_managed_holdings_are_one_batched_read_under_a_named_phase(tmp_path):
    from fetch import base
    from fetch import ve_managed as vm
    from fetch.logcache import DailyChecks
    owner = "0x" + "a" * 40
    p = {"name": "Aerodrome", "ve_managed_holdings": {"metric": "filing_managed_lock_tokens", "escrow": "0x" + "e" * 40,
                                                      "owners": {owner: "Team"}}}
    a = vm.VeManaged(reader=object(), daily=DailyChecks(tmp_path))
    seen = []

    def reader(owners, escrow):
        seen.append((list(owners), escrow, base.last_phase(base._owner_thread().name)[0]))
        return {"total": 7.0, "by_owner": {owner: 7.0}, "nfts": 2, "in_managed": 1}
    a.managed_reader = reader
    out = FetchOutput()
    a._holdings(p, out)
    assert len(seen) == 1 and "Multicall3" in seen[0][2]
    assert out.frame()["value"].tolist() == [7.0]


# ======================================================================================== 3c timeouts
def test_3c_dated_logs_stops_at_its_deadline_without_a_call(monkeypatch):
    from fetch.chain import ChainReader
    r = ChainReader.__new__(ChainReader)
    r._w3_url = {"arbitrum": "https://example.invalid"}
    monkeypatch.setattr("fetch.chain.rpc_endpoints", lambda chain: [])
    r.make_web3 = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call past the deadline"))
    r.checksum = lambda a: a
    with pytest.raises(TimeoutError):
        r.dated_logs("arbitrum", "0x" + "1" * 40, [], 1, 10, 10, [], deadline=time.monotonic() + 1)


def test_3c_balance_flow_keeps_what_it_read_when_the_tier_budget_runs_out(tmp_path, monkeypatch):
    from fetch import balance_flow as bf
    from fetch.explorer import ExplorerTimeout
    from fetch.logcache import LogCache, stream_id
    spec = config.PROJECT_BY_NAME["GEODNET"]["balance_flows"][0]
    part = [{"blockNumber": 5, "logIndex": 0, "transactionHash": "0x5", "timeStamp": 0, "data": "0x1", "topics": []}]

    class Ex:
        def get_logs(self, chain_id, token, topics, start, to):
            e = ExplorerTimeout("budget")
            e.partial, e.resume_from = part, 6
            raise e

    cache = LogCache(tmp_path)
    a = bf.BalanceFlow(reader=object(), explorer=Ex(), cache=cache, unbounded=False)
    monkeypatch.setattr(a, "_balances", lambda *x, **k: {}, raising=False)
    out = FetchOutput()
    # the stream loop alone: what a timed-out page leaves in the cache
    sid = stream_id(config.CHAIN_IDS[spec["chain"]], spec["token"], [None, None, None])
    try:
        Ex().get_logs(0, "", [], 0, 10)
    except ExplorerTimeout as e:
        cache.save(sid, list(e.partial), int(e.resume_from) - 1, None)
    c = cache.load(sid)
    assert c["scanned_to"] == 5 and len(c["events"]) == 1
    src = Path(bf.__file__).read_text(encoding="utf-8")
    assert "except ExplorerTimeout as e:" in src and "resumes at block" in src and 'tier_deadline' in src
    assert out.frame().empty


# ======================================================================================== 3d/3e/3f
def test_3d_berachain_and_p2p_and_uniswap_seeds_are_registered():
    src = (ROOT / "token_metrics.py").read_text(encoding="utf-8")
    for s in ("pendle_berachain", "eth_p2p_validation", "uni_retro_burn"):
        assert f'"{s}"' in src, s
    import token_metrics as tm
    assert tm.SEED_BERACHAIN_BUDGET_S >= 3600


def test_3e_3f_view_only_and_retired_series_are_labelled_not_stale():
    import sqlite3
    import check_offline_items as coi
    con = sqlite3.connect(":memory:")
    why = coi._not_stale(config, con, "Aethir", "arr_usd", "2026-10-10")
    assert why.startswith("READ-TIME VIEW")
    why = coi._not_stale(config, con, "Near", "noncirculating_nearblocks_tokens", "2026-10-13")
    assert why.startswith("RETIRED ROUTE")


# ======================================================================================== 4c references
def test_4c_near_inflow_reference_reads_stage_two_or_says_when_it_runs(tmp_path, monkeypatch):
    import references as refs
    monkeypatch.setattr(refs, "ROOT", tmp_path)
    v, _src, why = refs._our_value(("near_inflow", "fefundsadmin.sputnik-dao.near"), {}, "Near",
                                   pd.Timestamp("2026-10-13"), 1.0)
    assert v is None and "stage 2 has not run yet" in why
    f = tmp_path / "data" / "near" / "wallet_inflows_alltime.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"fefundsadmin.sputnik-dao.near": {"through": "2026-10-13", "total_near": 1234.5}}))
    v, src, why = refs._our_value(("near_inflow", "fefundsadmin.sputnik-dao.near"), {}, "Near",
                                  pd.Timestamp("2026-10-13"), 1.0)
    assert v == 1234.5 and "receipt_actions" in src and not why


def test_4c_maple_stsyrup_reference_reads_the_legacy_series():
    import references as refs
    assert refs.ROUTES[("Maple", "g6_stsyrup_total_assets")] == ("last", "stsyrup_legacy_tokens")


def test_4c_uniswap_burn_is_stored_from_the_transaction_on_its_block_day():
    import token_metrics as tm
    rec = json.loads((ROOT / "tests" / "fixtures" / "uni_retro_burn_tx.json").read_text(encoding="utf-8"))
    spec = next(s for s in config.PROJECT_BY_NAME["Uniswap"]["one_off_flows"] if s.get("tx"))
    row, why = tm.uni_retro_burn_row(rec["tx"], rec["transfers"], spec)
    assert row["date"] == pd.Timestamp("2025-12-27") and row["value"] == 100_000_000.0 and not why
    assert row["source"].startswith("tx:0x091f0083")
    bad = [dict(t, total={"decimals": "18", "value": str(99 * 10 ** 24)}) for t in rec["transfers"]]
    assert tm.uni_retro_burn_row(rec["tx"], bad, spec)[0] is None
    assert tm.uni_retro_burn_row(dict(rec["tx"], status="error"), rec["transfers"], spec)[0] is None


def test_4c_the_stored_tx_row_dates_the_one_off_and_the_rate_still_excludes_it(monkeypatch):
    import build_workbook as bw
    uni = config.PROJECT_BY_NAME["Uniswap"]
    d = ["2025-12-26", "2025-12-27", "2025-12-28"]
    gross = _rows("Uniswap", "gross_burn_tokens", d, [5.0, 6.0, 100_000_007.0], "chain:burn:delta")
    held = _rows("Uniswap", "one_off_burn_tokens", ["2025-12-27"], 100_000_000.0, "tx:0x091f (block 24106378)", 1)
    groups = {("Uniswap", "gross_burn_tokens"): gross, ("Uniswap", "one_off_burn_tokens"): held}
    monkeypatch.setattr(bw, "scoped_projects", lambda: [uni])
    bw._one_off_views(groups)
    one = groups[("Uniswap", "one_off_burn_tokens")]
    assert list(one["date"]) == [pd.Timestamp("2025-12-27")] and one["source"].iloc[0].startswith("tx:")
    assert groups[("Uniswap", "gross_burn_tokens")]["value"].tolist() == [5.0, 6.0, 7.0]


def test_4c_aethir_compute_purchases_is_read_from_the_demand_metric_payload():
    pages = config.PROJECT_BY_NAME["Aethir"]
    spec = next(v for k, v in pages.items() if isinstance(v, dict) and "pages" in v)["pages"]["protocol/demand-metric"]
    assert spec["fields"] == {"onChainComputePurchases": "compute_purchases_cumulative_tokens"}
    assert "onChainComputePurchases" in spec["current_only"]
    assert ("Aethir", "compute_purchases_cumulative_tokens") not in (config.UNAVAILABLE_BY_KEY or {})
    from fetch import aethir_pages as ap
    html = '<script>self.__next_f.push([1,"{\\"totalNetworkRevenue\\":194765193.9,\\"onChainComputePurchases\\":11213170271.22}"])</script>'
    v, _why = ap.key_scalar(html, "onChainComputePurchases")
    assert v == pytest.approx(11213170271.22)


# ======================================================================================== 4e NOT WIRED relabelled
def test_4e_a_named_blocker_leaves_the_not_wired_count(monkeypatch):
    import coverage_audit as ca
    monkeypatch.setattr(ca, "not_wired", lambda: [("x", "NOT BUILT BY DESIGN — restates price"),
                                                  ("y", "BLOCKED — paid source; Jake's decision"),
                                                  ("z", "LATER — not built")])
    rows = []
    for item, route in ca.not_wired():
        st = "N/A BY DESIGN" if route.startswith(("NOT BUILT BY DESIGN", "BLOCKED —")) else "NOT WIRED"
        rows.append(st)
    assert rows == ["N/A BY DESIGN", "N/A BY DESIGN", "NOT WIRED"]
    src = Path(ca.__file__).read_text(encoding="utf-8")
    assert 'route.startswith(("NOT BUILT BY DESIGN", "BLOCKED —"))' in src


def test_4e_spec_section_10_has_no_unlabelled_blocked_item():
    import coverage_audit as ca
    items = dict(ca.not_wired())
    for k, route in items.items():
        if k.startswith(("Exchange balances", "GEODNET customers", "Chains' TVL", "Market share")):
            assert route.startswith(("BLOCKED —", "NOT BUILT BY DESIGN")), k


# ======================================================================================== 5 approvals
def test_5_p2p_leg_is_approved_weekly():
    p2p = config.SETTLEMENT_REBUILD["ethereum_rebuild"]["p2p"]
    assert p2p["approved"] is True and p2p["cadence_days"] == 7 and "Jake" in p2p["approved_by"]


def test_3a_children_are_summed_from_their_first_day_and_a_hole_refuses_the_day():
    from fetch.llama import _sum_children
    d = pd.Timestamp
    rows, refused = _sum_children({"a": {d("2026-01-01"): 1.0, d("2026-01-02"): 2.0, d("2026-01-03"): 3.0},
                                   "b": {d("2026-01-02"): 10.0}})
    assert rows == [(d("2026-01-01"), 1.0), (d("2026-01-02"), 12.0)] and refused == ["2026-01-03 (b)"]


def test_2_a_declared_not_applicable_input_keeps_the_cell_n_a_not_missing():
    import coverage_audit as ca
    assert "declared not applicable" in ca.declared_not_applicable("Aerodrome", ["buyback_fund_balance"])
    assert ca.declared_not_applicable("Aerodrome", ["buyback_fund_balance", "fees_usd"]) is None
    assert ca.declared_not_applicable("Uniswap", ["fdv_usd"]) is None, "archetype membership alone is not enough"
    rows = [{"Project": "Aerodrome", "Tab": "Trajectory", "Column": "Buyback to LOCKED", "Status": "N/A BY DESIGN",
             "Reason / route / error": "HISTORY BUG (a route exists and has not filled it): buyback_fund_balance from "
                                       "nan — nothing stored"}]
    ca.mark_missing(rows, [])
    assert rows[0]["Status"] == "N/A BY DESIGN" and "declared not applicable" in rows[0]["Reason / route / error"]


def test_0_newer_series_lists_what_the_store_holds_past_the_fixtures(tmp_path):
    import sqlite3
    import measuring_guard as mg
    db = tmp_path / "m.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE metrics (project TEXT, metric TEXT, date TEXT, value REAL)")
    con.executemany("INSERT INTO metrics VALUES (?, ?, ?, ?)",
                    [("Hyperliquid", "hyperevm_priority_burn_tokens", "2026-10-19", 1.0),
                     ("Hyperliquid", "price_usd", "2026-10-01", 1.0),
                     ("Nobody", "price_usd", "2026-10-19", 1.0)])
    con.commit()
    con.close()
    got = mg.newer_series(str(db))
    assert [(p, m) for p, m, _d, _f in got] == [("Hyperliquid", "hyperevm_priority_burn_tokens")]
    assert got[0][3] == "none"


# ======================================================================================== 4b wiring
def test_4b_pendle_maturity_and_ccip_are_registered_tiers_with_business_slots_and_reasons():
    import fetch
    from fetch import gaps
    files = fetch.adapter_files()
    assert files["pendle_maturity"][0].endswith("pendle_maturity.py") and files["ccip_value"][0].endswith("ccip_value.py")
    assert fetch._budget("pendle_maturity") > config.PROJECT_BY_NAME["Pendle"]["pendle_maturity"]["budget_s"]
    assert fetch._budget("ccip_value") > config.PROJECT_BY_NAME["Chainlink"]["ccip_value"]["budget_s"]
    assert "ccip_token_value_usd" in config.BUSINESS_SLOTS["Chainlink"]
    assert "tvl_maturity_le_30d_usd" in config.BUSINESS_SLOTS["Pendle"]
    assert "Pendle" not in config.BUSINESS_NOT_WIRED and "Chainlink" not in config.BUSINESS_NOT_WIRED
    for p, spec in (("Pendle", "pendle_maturity"), ("Chainlink", "ccip_value")):
        assert config.PROJECT_BY_NAME[p][spec]["state_path"].startswith(".cache/"), "machine-local, gitignored"
    src = (ROOT / "fetch" / "gaps.py").read_text(encoding="utf-8")
    assert "fetch/pendle_maturity.py" in src and "fetch/ccip_value.py" in src
    assert '"ccip_value"' in (ROOT / "token_metrics.py").read_text(encoding="utf-8")
    assert gaps is not None
    # 4a: subsidy targeting
    assert files["subsidy_targeting"][0].endswith("subsidy_targeting.py")
    for p in ("Aerodrome", "Pendle"):
        spec = config.PROJECT_BY_NAME[p]["subsidy_targeting"]
        assert fetch._budget("subsidy_targeting") > spec["budget_s"] and spec["dir"] == "data/subsidy"
    assert "data/subsidy/" in (ROOT / ".gitignore").read_text(encoding="utf-8")
    import spec as _spec
    assert "subsidy_targeting" in _spec.concepts()
    import coverage_audit as ca
    assert not any(i.startswith("Subsidy targeting") for i, _r in ca.not_wired())
