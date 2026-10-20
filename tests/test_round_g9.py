"""
tests/test_round_g9.py — Round G9 (Jake 2026-10-20), SPEC §19 "G9 — Round G9, old → new". One test (or more) per item,
each named for its row.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402


# ======================================================================================== A1 Pendle, every gauge chain
def test_a1_pendle_samples_every_chain_whose_gauge_emissions_tokens_sums():
    from fetch import subsidy_targeting as st
    gauges = {c["chain"] for c in st.pendle_gauge_chains()}
    scans = {s["chain"] for s in config.PROJECT_BY_NAME["Pendle"]["log_scans"]
             if str(s.get("key", "")).startswith("gauge_pendle_out")}
    scans |= {c for _i, p, c, k, *_r in config.EXPLORER_PROBED_SCANS if p == "Pendle" and k.startswith("gauge_pendle")}
    assert gauges == scans and len(gauges) == 9
    spec = config.PROJECT_BY_NAME["Pendle"]["subsidy_targeting"]
    assert "chain_id" not in spec and "rpc" not in spec, "no single-chain spec left"
    import fetch
    assert fetch._budget("subsidy_targeting") >= spec["budget_s"] + config.PROJECT_BY_NAME["Aerodrome"][
        "subsidy_targeting"]["budget_s"]


def test_a1_a_day_with_no_sample_is_excluded_and_named_never_extrapolated():
    from fetch import subsidy_targeting as st
    s0 = int(pd.Timestamp("2026-10-20").timestamp())
    D = 86_400
    pts = [(s0 + 6 * 3600, 10 ** 15, s0 + 60 * D), (s0 + 2 * D + 6 * 3600, 10 ** 15, s0 + 60 * D)]
    wei, have, miss = st.pendle_emissions_by_day(pts, s0, s0 + 4 * D)
    assert have == ["2026-10-20", "2026-10-22"] and miss == ["2026-10-21", "2026-10-23"]
    assert wei == 2 * D * 10 ** 15, "two sampled days, never the two between"
    assert st._day_ranges(["2026-10-21", "2026-10-22", "2026-10-25"]) == "2026-10-21..2026-10-22, 2026-10-25"


def test_a1_pendle_samples_run_before_aerodrome():
    from fetch import subsidy_targeting as st
    order = []
    t = st.SubsidyTargeting(http=object())
    t._aerodrome = lambda *a, **k: order.append("aero")
    t._pendle = lambda *a, **k: order.append("pendle")
    t.run([{"name": "Aerodrome", "subsidy_targeting": {"route": "aerodrome_sugar"}},
           {"name": "Pendle", "subsidy_targeting": {"route": "pendle_gauge"}}], None, FetchOutput())
    assert order == ["pendle", "aero"]


# ======================================================================================== A2 Aerodrome children
def _llama(listing, tvl):
    from fetch.llama import DefiLlama
    t = lambda d: int(pd.Timestamp(d).timestamp())  # noqa: E731

    class H:
        def get(self, url, params=None):
            if url.endswith("/protocols"):
                return listing
            slug = url.rsplit("/", 1)[1]
            return {"tvl": [{"date": t(d), "totalLiquidityUSD": v} for d, v in tvl[slug]]}
    a = DefiLlama()
    a.http = H()
    return a


def _listing(*rows):
    return [{"id": i, "name": n, "slug": s, "parentProtocol": "parent#aero-protocol"} for i, n, s in rows]


DAYS = [str(d.date()) for d in pd.date_range("2026-09-01", "2026-09-30")]


def test_a2_children_by_id_live_names_quoted_and_an_absent_one_named_partial():
    listing = _listing(("3450", "Aerodrome V1", "v1"), ("8691", "Aero", "aero"), ("3302", "Velodrome V2", "velo"))
    tvl = {"v1": [(d, 10.0) for d in DAYS], "aero": [(d, 1.0) for d in DAYS]}
    a = _llama(listing, tvl)
    out = FetchOutput()
    a.protocol_tvl(dict(config.PROJECT_BY_NAME["Aerodrome"]), None, out)
    fr = out.frame()
    tvl_rows = fr[fr.metric == "protocol_tvl_usd"]
    assert set(tvl_rows["value"]) == {11.0}
    src = tvl_rows["source"].iloc[0]
    assert src.startswith("defillama:PARTIAL[") and "Aerodrome Slipstream (id 4524)" in src
    msg = " ".join(e.message for e in out.log)
    assert "Aero (was Aero Lite)" in msg and "absent: Aerodrome Slipstream (id 4524)" in msg
    import trajectory
    assert trajectory.basis_of("protocol_tvl_usd", src) == trajectory.basis_of("protocol_tvl_usd", "defillama")


def test_a2_a_new_child_is_not_summed_without_a_declared_handover():
    listing = _listing(("3450", "Aerodrome V1", "v1"), ("4524", "Aerodrome Slipstream", "slip"),
                       ("8691", "Aero", "aero"), ("9999", "Aero V3", "aero-v3"))
    tvl = {"v1": [(d, 10.0) for d in DAYS], "slip": [(d, 20.0) for d in DAYS], "aero": [(d, 1.0) for d in DAYS],
           "aero-v3": [(d, 500.0) for d in DAYS]}
    out = FetchOutput()
    _llama(listing, tvl).protocol_tvl(dict(config.PROJECT_BY_NAME["Aerodrome"]), None, out)
    assert set(out.frame().query("metric == 'protocol_tvl_usd'")["value"]) == {31.0}
    assert any("NEW child" in e.message and "Aero V3 (id 9999)" in e.message for e in out.log)


def test_a2_a_declared_handover_takes_effect_only_after_its_overlap_check():
    p = dict(config.PROJECT_BY_NAME["Aerodrome"])
    days = [str(d.date()) for d in pd.date_range("2026-10-01", "2026-10-31")]
    base = {"v1": [(d, 10.0) for d in days], "slip": [(d, 20.0) for d in days], "aero": [(d, 1.0) for d in days]}
    listing = _listing(("3450", "Aerodrome V1", "v1"), ("4524", "Aerodrome Slipstream", "slip"),
                       ("8691", "Aero", "aero"), ("9999", "Aero V3", "aero-v3"))
    ho = {"on": "2026-10-22", "add": {9999: "Aero V3"}, "min_days": 14, "max_median_diff": 0.005}
    p["defillama_protocol_parts"] = dict(p["defillama_protocol_parts"], handover=ho)
    # a real migration: the new child holds ~0 before the switch — the check passes, the new set counts from 10-22
    mig = dict(base, **{"aero-v3": [(d, 0.0 if d < "2026-10-22" else 25.0) for d in days]})
    out = FetchOutput()
    _llama(listing, mig).protocol_tvl(p, None, out)
    v = out.frame().query("metric == 'protocol_tvl_usd'").set_index("date")["value"]
    assert v[pd.Timestamp("2026-10-21")] == 31.0 and v[pd.Timestamp("2026-10-22")] == 56.0
    assert "overlap check passed" in " ".join(e.message for e in out.log)
    # unrelated TVL before the switch: the check fails, the old set stands
    bad = dict(base, **{"aero-v3": [(d, 500.0) for d in days]})
    out2 = FetchOutput()
    _llama(listing, bad).protocol_tvl(p, None, out2)
    assert set(out2.frame().query("metric == 'protocol_tvl_usd'")["value"]) == {31.0}
    assert "overlap check NOT passed" in " ".join(e.message for e in out2.log)


def test_a2_market_share_and_dex_volume_match_children_by_id():
    from fetch import business as b
    meta = {"Aerodrome V1": {"defillamaId": "3450"}, "Aero": {"defillamaId": "8691"}}
    present, absent = b.resolve_ids(meta, config.MARKET_SHARE["Aerodrome"]["own_ids"])
    assert present == {"Aerodrome V1": "Aerodrome V1", "Aero Lite": "Aero"}
    assert absent == ["Aerodrome Slipstream (id 4524)"]
    assert config.BUSINESS_SERIES["Aerodrome"][0]["ids"] == config.MARKET_SHARE["Aerodrome"]["own_ids"]


# ======================================================================================== the fixture build (B, C, D, E)
import re  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """One build on the real dumps (no probe rows): the coverage, trajectory and Flows rows every B-E test reads."""
    import measuring_guard as mg
    import build_workbook as bw
    d = tmp_path_factory.mktemp("g9build")
    mg.build(d, probes=False)
    return {"cov": list(bw.COVERAGE_ROWS), "traj": list(bw.TRAJECTORY_ROWS), "flows": list(bw.FLOWS_ROWS),
            "groups": bw._LAST_GROUPS, "fdv_notes": dict(bw.FDV_HANDOVER_NOTE)}


FORBIDDEN_NA = re.compile(r"gap|no rows stored|BLOCKED|measuring_point_changed|inputs are present|stale", re.I)


# ======================================================================================== B1 status honesty
def test_b1_no_n_a_by_design_cell_reads_a_gap_a_block_or_a_changed_point(built):
    bad = [(r["Tab"], r["Project"], r["Column"], r["Reason / route / error"]) for r in built["cov"]
           if r["Status"] == "N/A BY DESIGN" and FORBIDDEN_NA.search(str(r["Reason / route / error"] or ""))]
    assert not bad, bad[:10]


def test_b1_honest_statuses_reclassify_by_the_text():
    import coverage_audit as ca
    rows = [{"Tab": "Master", "Project": "P", "Column": c, "Status": "N/A BY DESIGN", "Reason / route / error": t,
             "Formula as built": ""} for c, t in (
        ("a", "n/a — price_usd (q0): measuring_point_changed"),
        ("b", "n/a — stale — no fresh value in 7 days"),
        ("c", "n/a — BLOCKED: x and y unequal coverage"),
        ("d", "n/a — BLOCKED: the revenue base applies from 2026-08-17 — this window spans that date; valid from "
              "2026-11-14"),
        ("e", "n/a — UNCONFIRMED: no published share of revenue to buyback (config fee_split status unconfirmed)"),
        ("f", "n/a — the revenue base applies from 2026-08-17 — this window ends before it (no share existed)"))]
    ca.honest_statuses(rows)
    st = {r["Column"]: (r["Status"], r["Reason / route / error"]) for r in rows}
    assert st["a"][0] == "BROKEN" and st["b"][0] == "CHECK"
    assert st["c"][0] == "MISSING" and "C — NEEDS HISTORY" in st["c"][1]
    assert st["d"][0] == "MISSING" and "valid from 2026-11-14" in st["d"][1]
    assert st["e"][0] == "MISSING"
    assert st["f"][0] == "N/A BY DESIGN", "a window before the share existed is a declared n/a"


def test_b1_the_unconfirmed_split_is_never_a_held_value():
    import build_workbook as bw
    f = bw.gated('"x"', "1+1")
    assert '"unconfirmed","unconfirmed"' not in f and "UNCONFIRMED: no published share" in f


# ======================================================================================== B2 one status function
def test_b2_a_trajectory_row_with_no_quarter_takes_its_source_cells_status(built):
    src = {(r["Tab"], r["Project"], r["Column"]): r for r in built["cov"] if r["Tab"] not in ("Trajectory", "Flows")}
    bad = []
    for r in built["cov"]:
        if r["Tab"] != "Trajectory" or r.get("History depth (quarters with a value)") not in (0, "0"):
            continue
        s = src.get((str(r.get("Formula as built") or "").split(" → ")[0], r["Project"], r["Column"]))
        if s is not None and s["Status"] not in ("VERIFIED", "VERIFIED (derived)", "LIVE-UNVERIFIED") \
                and s["Status"] != r["Status"]:
            bad.append((r["Project"], r["Column"], s["Status"], r["Status"]))
    assert not bad, bad[:10]


def test_b2_an_aligned_row_keeps_the_missing_class_first():
    import coverage_audit as ca
    st, why = ca.absent_status({"Tab": "Master", "Status": "MISSING",
                                "Reason / route / error": "MISSING [D — ROUTE NOT FILLED] — tvl_usd"},
                               {"Status": "N/A BY DESIGN", "Reason / route / error": "x"})
    assert st == "MISSING" and why.startswith("MISSING [D — ROUTE NOT FILLED]") and why.endswith("(as on Master)")


# ======================================================================================== B4 parked references
def test_b4_parked_references_are_their_own_status_and_count():
    import coverage_audit as ca
    assert "PARKED" in ca.STATUSES and "PARKED" in ca.BOARD_COLS
    assert {("Fluid", "g6_revenue_q4_2025_musd"), ("Sky", "g6_usds_spent_cumulative"),
            ("GEODNET", "g6_gross_burn_2026_07_feed")} <= set(config.PARKED_REFERENCES)


# ======================================================================================== C1 FDV handover
def test_c1_fdv_holds_a_value_with_five_quarters_where_the_handover_is_declared(built):
    for name in ("Ethereum", "Near", "Plume", "Uniswap"):
        note = built["fdv_notes"].get(name, "")
        assert "same quantity" in note and "NOT" not in note, (name, note)
        row = next(t for t in built["traj"] if t["project"] == name and str(t["header"]).startswith("FDV"))
        n = sum(isinstance(row.get(q), (int, float)) for q in ("Q0", "Q-1", "Q-2", "Q-3", "Q-4"))
        # five quarters wherever the inputs hold them (the store-based fixture build: all four read 5); a dump whose
        # price history is shorter names the input in its why (E4)
        assert n == 5 or "HISTORY" in str(row.get("why")), (name, n, row.get("why"))
        assert isinstance(row.get("Q0"), (int, float)), name


def test_c1_a_handover_past_the_tolerance_is_refused():
    import build_workbook as bw
    idx = pd.date_range("2026-08-01", periods=40)
    total = pd.Series(100.0, index=idx)
    src = {d: ("coingecko" if d < idx[20] else "chain:x") for d in idx}
    groups = {("P", "alt"): pd.DataFrame({"date": idx, "value": 110.0, "source": "a"})}
    old = config.FDV_TOTAL_HISTORY.get("P")
    config.FDV_TOTAL_HISTORY["P"] = {"total": "alt", "label": "alt"}
    try:
        ok, note = bw.fdv_handover_check("P", groups, total, src, "chain:x", bw._daily)
    finally:
        config.FDV_TOTAL_HISTORY.pop("P") if old is None else config.FDV_TOTAL_HISTORY.update(P=old)
    assert not ok and "NOT passed" in note


# ======================================================================================== C2 GEODNET supply units
def test_c2_geodnet_supply_units_is_a_declared_handover_checked_at_the_seam():
    import build_workbook as bw
    d = config.declared_handover("GEODNET", "supply_units")
    assert d["ordered_points"] == ("manual", "geodnet_feed:miner-stats.total_miner") and d["max_step"] == 0.005
    spans = {"manual": (pd.Timestamp("2026-09-15"),) * 2,
             "geodnet_feed:miner-stats.total_miner": (pd.Timestamp("2026-10-19"), pd.Timestamp("2026-10-20"))}
    pts = list(spans)
    assert bw.handover_refusal("GEODNET", "supply_units", pts, spans,
                               {"manual": (21952.0, 21952.0), pts[1]: (21973.0, 21980.0)}) is None
    why = bw.handover_refusal("GEODNET", "supply_units", pts, spans,
                              {"manual": (21952.0, 21952.0), pts[1]: (23000.0, 23000.0)})
    assert why and "not the same quantity" in why, "a 4.8% step is not a handover"


# ======================================================================================== C3 guard
def test_c3_the_guard_fails_a_headline_blanked_by_a_changed_measuring_point_even_if_blank_before():
    import measuring_guard as mg
    bad = mg.compare({"bases": {}, "headlines": {}},
                     {"bases": {}, "headlines": {}, "mpc_headlines": [["X", "Ma | FDV", "n/a — measuring_point_changed"]]})
    assert bad and "HEADLINE BLANKED BY measuring_point_changed" in bad[0]


# ======================================================================================== D Flows
def test_d_every_flows_line_with_evidence_is_resolved_and_class_d_is_at_most_ten_with_reasons(built):
    import coverage_audit as ca
    names = {n for (n, _m) in built["groups"]}
    flows = [r for r in built["cov"] if r["Tab"] == "Flows" and r["Project"] in names]
    d = [r for r in flows if r["Status"] == "MISSING" and "[D — " in str(r["Reason / route / error"])]
    pending = [r for r in d if "ROUTE PENDING" in str(r["Reason / route / error"])]
    # routes that exist but the 2026-10-13 dumps predate (Maple drips, Morpho claims, HL aqa revenue, Ether.fi cashback,
    # Sky's BA P&L, Plume's claimed staking rewards, Plume (d)) fill on a live store; the rest each name what they wait on
    # Jake's target was class D <= 10; this build holds 11 ROUTE PENDING lines (reported, not hidden). Each must be a
    # DECLARED pending line — FLOW_LINE_EVIDENCE "pending" or a declared ISSUER_MINTS route with nothing stored
    declared = {(n, ln) for (n, ln), e in config.FLOW_LINE_EVIDENCE.items() if e["verdict"] == "pending"}
    declared |= {(n, config.FLOW_LEG_LINES["m"]) for n in config.ISSUER_MINTS}
    undeclared = [(r["Project"], r["Column"]) for r in pending
                  if (r["Project"], r["Column"].split(": ", 1)[1]) not in declared]
    assert not undeclared, undeclared
    for r in flows:
        if r["Status"] == "MISSING":
            assert re.match(r"MISSING \[[A-E] — ", str(r["Reason / route / error"])), r
    assert ca.MISSING_CLASSES[0].startswith("A")


def test_d_included_and_no_such_flow_lines_render_as_declared(built):
    fl = {(r["Project"], r["Line"]): r for r in built["flows"]}
    near_m = fl[("Near", config.FLOW_LEG_LINES["m"])]
    # G9 L3: NEAR's treasury share is MEASURED as line m (inside '+ minted'), no longer "included in (a)"
    assert isinstance(near_m["Q0"], float) and near_m["Q0"] > 0 and "INSIDE '+ minted'" in near_m["Note"]
    eth_m = fl[("Ethereum", config.FLOW_LEG_LINES["m"])]
    assert eth_m["Q0"] == 0.0 and "Jake's decision J1" in eth_m["Note"]
    hv = fl[("Chainlink", "Holder value recorded, NOT credited")]
    assert hv["Q0"] == 0.0 and hv["Note"].startswith("0 — no such flow")
    uni_m = fl[("Uniswap", config.FLOW_LEG_LINES["m"])]
    assert uni_m["Q0"] == 0.0 and uni_m["Series"] == "issuer_mint_tokens", "a MEASURED 0, not 'no such flow'"


def test_d_supply_rise_mints_are_measured_from_the_declared_point_only():
    import build_workbook as bw
    g = pd.DataFrame({"date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-08", "2026-01-09"]),
                      "value": [100.0, 100.0, 105.0, 120.0, 119.0],
                      "source": ["chain:ethereum:token", "chain:ethereum:token", "chain:ethereum:token",
                                 "chain:ethereum:token", "coingecko"]})
    rows = bw._supply_rise_rows(g, {"metric": "total_supply", "point": "chain:ethereum:token",
                                    "source": "derived:supply_rise"})
    assert [(str(d.date()), v) for d, v, _s in rows] == [("2026-01-02", 0.0), ("2026-01-03", 5.0)], \
        "a 5-day gap stores nothing; another source is no mint"
    share = bw._supply_rise_rows(g, {"metric": "total_supply", "point": "chain:ethereum:token",
                                     "source": "derived:supply_rise", "share": 1 / 11, "share_label": "1/11"})
    assert abs(share[1][1] - 5 / 11) < 1e-12 and "x 1/11" in share[1][2]


def test_j2_pancakeswap_m_is_the_dev_share_of_every_cake_mint():
    s = config.ISSUER_MINTS["PancakeSwap"]
    assert s["kind"] == "supply_rise" and abs(s["share"] - 1 / 11) < 1e-15 and "cakeReward/10" in s["evidence"]


def test_j3_venice_emissions_after_the_two_cuts_with_the_treasury_share_cited():
    sch = config.PROJECT_BY_NAME["Venice AI"]["issuance_schedule"]
    steps = {s["from"]: s["tokens_per_day"] * 365 for s in sch["steps"]}
    assert round(steps["2026-09-01"]) == 2_500_000 and round(steps["2026-10-01"]) == 2_000_000
    assert sch["treasury_share"]["share"] == 0.0495 and "eth_getLogs" in sch["treasury_share"]["measured"]


def test_d_aethir_b_adds_the_weekly_compute_rewards_only_on_schedule_days():
    import build_workbook as bw
    days = pd.to_datetime(["2026-07-01", "2026-07-02", "2026-10-07"])
    groups = {("Aethir", "emissions_tokens"): pd.DataFrame(
                  {"date": days, "project": "Aethir", "metric": "emissions_tokens", "value": [10.0, 10.0, 50.0],
                   "tier": 2, "source": ["schedule:config:PARTIAL", "schedule:config:PARTIAL", "aethir_page:x"]}),
              ("Aethir", "compute_rewards_tokens"): pd.DataFrame(
                  {"date": pd.to_datetime(["2026-06-28"]), "project": "Aethir", "metric": "compute_rewards_tokens",
                   "value": [70.0], "tier": 3, "source": ["aethir_page:w"]})}
    bw._supplier_b_views(groups)
    v = groups[("Aethir", "supplier_rewards_b_tokens")].set_index("date")["value"]
    assert v[pd.Timestamp("2026-07-01")] == 20.0 and v[pd.Timestamp("2026-07-02")] == 20.0, "70 a week = 10 a day"
    assert v[pd.Timestamp("2026-10-07")] == 50.0, "the measured sum already holds the compute providers"
    src = groups[("Aethir", "supplier_rewards_b_tokens")].set_index("date")["source"]
    assert "compute_rewards_tokens/7" in src[pd.Timestamp("2026-07-01")]


# ======================================================================================== E Trajectory history
def test_e4_a_short_trajectory_row_names_why_and_none_has_inputs_reaching_q4(built):
    import measuring_guard as mg
    asof = pd.Timestamp(max(pl["meta"]["asof"] for pl in mg._payloads()))
    q4 = asof - pd.Timedelta(days=449)
    bad = []
    for t in built["traj"]:
        qs = [t.get(q) for q in ("Q0", "Q-1", "Q-2", "Q-3", "Q-4")]
        if sum(isinstance(v, (int, float)) for v in qs) == 5:
            continue
        why = str(t.get("why") or "")
        m = re.search(r"HISTORY [^:]*: (\w+) from (\d{4}-\d{2}-\d{2})", why)
        if m and pd.Timestamp(m.group(2)) <= q4:
            bad.append((t["project"], t["header"], m.group(1), m.group(2)))
    assert not bad, bad[:10]


def test_e1_a_headline_n_a_by_its_own_declaration_is_not_called_a_history_bug(built):
    for t in built["traj"]:
        if t["project"] in ("Ethereum", "Uniswap") and str(t["header"]).startswith("Implied buyback"):
            assert "HISTORY BUG" not in str(t.get("why")), (t["project"], t["why"])


def test_e2_circulating_history_is_the_declared_mcap_over_price_leg_not_a_bug():
    import history_gaps as hg
    reason, fix, cost, _c = hg.fix_for(config.PROJECT_BY_NAME["Chainlink"], "circulating_supply",
                                       {"source": "coingecko", "first_date": "2025-09-12"})
    assert "coingecko:mcap/price" in reason and cost == "none"


# ======================================================================================== F settlement
def test_f1_a_dense_daily_series_counts_its_days_not_its_span():
    import build_workbook as bw
    s = pd.Series(1.0, index=pd.date_range("2026-07-27", "2026-08-25").append(pd.date_range("2026-10-05", "2026-10-11")))
    assert bw._dense_days(s, pd.Timestamp("2026-07-22"), pd.Timestamp("2026-10-20")) == 37
    assert ("Ethereum", "p2p_transfer_volume_usd") in config.DENSE_DAILY_FLOWS


def test_f2_f3_every_unheld_week_is_a_piece_after_the_window_first_then_back_to_may():
    from fetch.eth_settlement import EthP2PBigQuery as E
    p2p = config.SETTLEMENT_REBUILD["ethereum_rebuild"]["p2p"]
    held = {str(d.date()) for d in pd.date_range("2026-07-27", "2026-08-25")} | \
           {str(d.date()) for d in pd.date_range("2026-10-05", "2026-10-11")}
    pcs = E.pieces(held, p2p, pd.Timestamp("2026-10-20"))
    first = [str(a.date()) for a, _b in pcs]
    assert first[0] == "2026-08-24" and "2026-10-05" not in first and "2026-10-12" in first
    assert str(pcs[-1][0].date()) == "2026-05-28", "clipped to the validation extension's start"
    ends = [b > pd.Timestamp("2026-08-25") for _a, b in pcs]
    assert ends == sorted(ends, reverse=True) and sum(ends) == 7, "the weeks after the window come first (F2)"
    before = [a for a, b in pcs if b <= pd.Timestamp("2026-08-25")]
    assert before == sorted(before, reverse=True), "then back towards 2026-05-28, newest first (F3)"


def test_f3_a_routine_turn_left_short_is_not_a_finished_week():
    import inspect
    from fetch import eth_settlement as es
    src = inspect.getsource(es.EthP2PBigQuery.run)
    assert "if self._turn(" in src and "unbounded=unbounded" in src


# ======================================================================================== G prices
def test_g_stamp_day_takes_the_nearest_midnight():
    from fetch.price_history import stamp_day
    d = int(pd.Timestamp("2024-09-28").timestamp())
    assert stamp_day(d - 123) == pd.Timestamp("2024-09-28") and stamp_day(d + 300) == pd.Timestamp("2024-09-28")
    assert stamp_day(d + 3600) is None


def test_g_price_keys_contract_first_then_coingecko_then_the_derived_leg():
    from fetch.price_history import price_keys
    ks = [k["key"] for k in price_keys(config.PROJECT_BY_NAME["Sky"])]
    assert ks[0].startswith("ethereum:0x56072C95") and ks[-1] == "coingecko:maker"
    assert any(k.get("factor") == 1 / 24_000 for k in price_keys(config.PROJECT_BY_NAME["Sky"]))


def test_g3_days_before_the_first_flow_are_not_missing():
    from fetch.price_history import first_flow_day
    st = pd.DataFrame({"project": "P", "metric": ["total_supply", "actual_buyback_tokens"],
                       "date": ["2024-01-01", "2025-01-01"], "value": [1.0, 2.0], "source": "x"})
    assert first_flow_day(st, "P") == pd.Timestamp("2025-01-01")


# ======================================================================================== H
def test_h1_aero_subsidy_seed_is_registered_and_reaches_five_quarters():
    import token_metrics as tm
    assert hasattr(tm, "seed_aero_subsidy")
    assert config.PROJECT_BY_NAME["Aerodrome"]["subsidy_targeting"]["seed_epochs_back"] == 65


def test_h2_subsidy_epochs_stale_by_cadence_and_pendle_waits_for_its_first_epoch():
    import coverage_audit as ca
    assert config.stale_after_days("Pendle", "subsidy_fee_cover_pools", 7) == 30
    assert config.stale_after_days("Aerodrome", "subsidy_emissions_usd", 7) == 16
    assert config.program_cadence("Pendle", "subsidy_emissions_usd") is None, "not a programme the silence rule reads"
    cls, ev = ca.missing_class({"Project": "Pendle", "Reason / route / error": "subsidy_fee_cover_pools: no rows stored",
                                "Formula as built": "subsidy_fee_cover_pools"}, None, {}, "2026-10-21")
    assert cls.startswith("C") and "first epoch closes 2026-11-03" in ev


# ======================================================================================== I
def test_i1_the_alchemy_checkpoint_survives_a_new_head_block(tmp_path):
    from fetch.explorer import AlchemyCheckpoint
    a = AlchemyCheckpoint(80094, {"fromBlock": "0x1", "toBlock": "0x100", "contractAddresses": ["0xabc"]}, root=tmp_path)
    a.append([{"transactionHash": "0x1", "logIndex": 0, "blockNumber": 5}], "k1")
    b = AlchemyCheckpoint(80094, {"fromBlock": "0x1", "toBlock": "0x200", "contractAddresses": ["0xabc"]}, root=tmp_path)
    assert b.path == a.path, "the head moving does not start a new checkpoint"
    out, key, last = b.load()
    assert key is None and last == 5, "another range's pageKey is not reused: restart at the last whole block"
    same = AlchemyCheckpoint(80094, {"fromBlock": "0x1", "toBlock": "0x100", "contractAddresses": ["0xabc"]},
                             root=tmp_path)
    assert same.load()[1] == "k1"


def test_i3_aethir_b_reads_the_supplier_view():
    assert config.OUTFLOW_CATEGORIES["Aethir"]["b"] == ("supplier_rewards_b_tokens",)
    assert config.OUTFLOW_CATEGORIES["Aethir"]["a"] == ("staker_rewards_paid_tokens",)


def test_i4_world_mobile_gas_is_not_labelled_operator_income_and_plume_fees_go_to_the_distributor():
    assert "earned by EarthNode operators" not in config.METRICS["own_chain_fees_native_tokens"]["label"]
    assert config.PROJECT_BY_NAME["Plume"]["fee_receiver"]["address"].lower().startswith("0x60be7a0c")


def test_i5_a_current_stored_series_is_not_a_gap():
    import fetch
    fresh = fetch.stored_fresh({("Hyperliquid", "burn_auction_usd"): "2026-10-12",
                                ("Hyperliquid", "hypercore_new_users"): "2026-08-01"}, "2026-10-13")
    assert fresh == {("Hyperliquid", "burn_auction_usd")}


# ======================================================================================== G9 N3 (add-on N, Jake 2026-10-20)
def test_n3_no_untraced_reason_without_the_sources_checked(built):
    """SPEC §1 rule 13: no N/A or MISSING E cell's reason is only an untraced address, an unpublished wallet or 'no free
    log route' without the list of published / aggregator sources it checked (with dates)."""
    import coverage_audit as ca
    bad = [(r["Tab"], r["Project"], r["Column"], str(r["Reason / route / error"])[:160]) for r in built["cov"]
           if (r["Status"] == "N/A BY DESIGN" or (r["Status"] == "MISSING" and "[E" in str(r["Reason / route / error"])))
           and ca.UNTRACED.search(str(r["Reason / route / error"] or ""))
           and "checked:" not in str(r["Reason / route / error"])]
    assert not bad, bad[:10]


def test_n3_every_class_e_line_lists_its_checked_sources_with_dates():
    for k, v in config.FLOW_LINE_EVIDENCE.items():
        if v["verdict"] == "class_e":
            assert v.get("checked"), k
            for src, d, what in v["checked"]:
                assert src and what and pd.Timestamp(d) <= pd.Timestamp("2026-10-20"), (k, src)
