"""ROUND G17 (Jake 2026-10-23, run 20261023T132001Z on de4a779) — finish the wiring outside Aerodrome and World Mobile.

0 a class the build names; 1a-1h MISSING D; 2a/2b NOT WIRED; 3a/3b CHECK; 4 the ultrasound Gap row; 5 Plume's weekly
RWA share; 6 deterministic series breaks; 8 wiring_complete.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import coverage_audit as ca  # noqa: E402


def _g(project, metric, days, values, source="x"):
    return pd.DataFrame({"date": pd.to_datetime(list(days)), "project": project, "metric": metric,
                         "value": [float(v) for v in values], "tier": 1, "source": source, "is_manual": False,
                         "entered_on": "", "fetched_at": ""})


# ================================================================================================== 0 / 2a classes
def test_0_a_cell_naming_its_class_is_missing_of_that_class():
    st, why = ca.classify("x", "MISSING E — needs issuer wallet list (Jake): the 27 wallets stake 0", None)
    assert st == "MISSING" and why.startswith("MISSING [E — NO SOURCE] — needs issuer wallet list (Jake)")
    st, why = ca.classify("x", "MISSING C — valid from 2026-11-08", None)
    assert st == "MISSING" and why.startswith("MISSING [C — NEEDS HISTORY]")


def test_2a_a_route_waiting_on_a_wallet_list_is_missing_e_and_no_route_is_not_wired():
    """NOT WIRED only for a cell with no route code at all: every project with a route (INSIDER_SHARE_ROUTES /
    INSIDER_SHARE_MONITOR) reads MISSING E naming the list; the build's no-route fallback stays NOT WIRED."""
    texts = [v["cell"] for v in config.INSIDER_SHARE_ROUTES.values()] + \
            [v["not_wired"] for v in config.INSIDER_SHARE_MONITOR.values()]
    assert len(texts) >= 11
    for t in texts:
        assert len(t) <= 255
        st, why = ca.classify("x", t, None)
        assert st == "MISSING" and "needs issuer wallet list (Jake)" in why, t
    fallback = ("NOT WIRED — no issuer / insider wallet is identified among the stakers or lockers (route: the staking "
                "contract's depositors crossed with the issuer-wallet list)")
    assert ca.classify("x", fallback, None)[0] == "NOT WIRED"
    import inspect
    assert "NOT WIRED — no issuer / insider wallet is identified" in inspect.getsource(bw)   # the no-route fallback


def test_0_declared_classes_decide_before_inference():
    # (D1-4: Pendle's published APR is closed N/A by design — config.UNAVAILABLE — so it is no longer a declared E)
    assert ("Pendle", "staking_apr_published") in config.UNAVAILABLE_BY_KEY
    cls, ev = ca.missing_class({"Project": "Ethereum", "Reason / route / error": "outflow_d_usd (q0): BLOCKED"})
    assert cls == "C — NEEDS HISTORY" and "valid from 2026-12-03" in ev
    cls, ev = ca.missing_class({"Project": "Plume", "Reason / route / error": "outflow_d_usd (q0): no rows stored"})
    assert cls == "E — NO SOURCE" and "no (d) route" in ev


# ================================================================================================== 1a
def test_1a_sky_dashboard_closed_with_its_reason_and_near_armed_on_nearblocks():
    u = config.unavailable_for("Sky", "locked_tokens_dashboard")
    assert u and u["cell_text"].startswith("n/a — by design") and ca.classify("x", u["cell_text"], None)[0] == "N/A BY DESIGN"
    nb = config.PROJECT_BY_NAME["Near"]["nearblocks"]["supply_total"]
    assert nb["path"] == "/v3/stats" and nb["metric"] == "total_supply_dashboard"


def test_1a_near_total_supply_read_from_v3_stats(monkeypatch):
    from fetch import nearblocks as nbm
    from fetch.base import FetchOutput
    a = nbm.NearBlocks.__new__(nbm.NearBlocks)
    a.last_dates = {}
    monkeypatch.setattr(a, "_key", lambda spec: "k", raising=False)
    monkeypatch.setattr(a, "_get", lambda url, params, key: {"data": {"total_supply": str(1_309_375_672 * 10 ** 24),
                                                                       "circulating_supply": "1"}}, raising=False)
    monkeypatch.setattr(a, "_scrub", lambda spec, e: str(e), raising=False)
    import fetch.scrape as sc
    monkeypatch.setattr(sc, "robots_verdict", lambda url: (True, "ok"))
    out = FetchOutput()
    spec = config.PROJECT_BY_NAME["Near"]["nearblocks"]
    a._supply_total("Near", spec, spec["supply_total"], out)
    f = [x for x in out.frames if x is not None and not x.empty]
    assert f and f[0]["metric"].iloc[0] == "total_supply_dashboard" and abs(f[0]["value"].iloc[0] - 1_309_375_672) < 1


# ================================================================================================== 1b
def test_1b_a_zero_apr_is_never_stored_and_the_stored_zero_is_cleaned(tmp_path):
    from fetch import pendle_epochs as pe
    from fetch.base import FetchOutput
    spec = config.PROJECT_BY_NAME["Pendle"]["spendle_epochs"]
    old = int(pd.Timestamp("2026-08-01").timestamp())
    payload = {spec["history_key"]: {spec["time_field"]: [old], spec.get("aprs_field", "aprs"): [0]}}
    out = FetchOutput()
    pe.PendleEpochs._epoch_aprs("Pendle", spec, payload, out)
    assert not [x for x in out.frames if x is not None and not x.empty]
    import logging
    import store
    import token_metrics as tm
    st = store.Store(str(tmp_path / "metrics.db"))
    st.conn.executemany("INSERT INTO metrics(date, project, metric, value, source, tier, fetched_at) VALUES "
                        "(?, 'Pendle', 'staking_apr_published', ?, 'pendle_api:lastEpochApr', 3, 'x')",
                        [("2026-09-29", 0.0), ("2026-09-15", 0.071)])
    st.conn.commit()
    lines = tm.apply_run_cleanups(st, "r", logging.getLogger("t"))
    assert any("CLEAN-UP G17-1" in x and "2026-09-29" in x for x in lines)
    left = st.conn.execute("SELECT date, value FROM metrics WHERE metric='staking_apr_published'").fetchall()
    assert left == [("2026-09-15", 0.071)]
    st.close()


# ================================================================================================== 1d / 1e / 1f
def test_1d_1e_sky_scans_are_declared_with_their_primary_sources():
    scans = {s["key"]: s for s in config.PROJECT_BY_NAME["Sky"]["log_scans"]}
    mint, out = scans["sky_issuer_mints"], scans["sky_ecosystem_out"]
    assert mint["count_mints"] and mint["count_from"] == [config.BURN_ADDRESSES["zero"]]
    assert set(config.SKY_ENDOWMENTS.values()) <= set(out["holders"]) and out["direction"] == "out"
    assert config.BURN_ADDRESSES["zero"] in out["exclude_counterparties"]
    assert all(len(a) == 42 for a in out["holders"] + out["exclude_counterparties"])
    assert config.ISSUER_MINTS["Sky"]["metric"] == "sky_issuer_mint_tokens"
    assert config.OUTFLOW_CATEGORIES["Sky"]["d"] == ("sky_ecosystem_out_tokens",)
    assert ("Sky", "Outflows beside: ecosystem / grants (d)") not in config.FLOW_LINE_EVIDENCE


def test_1f_plume_claims_scan_and_its_explorer_never_sends_our_key(monkeypatch):
    # G18-2: the Transfer scan on explorer.plume.org is replaced by fetch/plume_claims.py (rpc.plume.org, by campaign
    # id); the series and its Flows (c) line stand. A self-hosted Blockscout routed directly still never gets our key.
    assert not any(s["key"] == "plume_airdrop_claims" for s in config.PROJECT_BY_NAME["Plume"].get("log_scans") or [])
    assert config.OUTFLOW_CATEGORIES["Plume"]["c"] == ("plume_airdrop_claims_tokens",)
    from fetch import explorer as ex
    monkeypatch.setitem(config.EXPLORERS["blockscout"]["hosts"], 98866, "https://explorer.plume.org/api")
    monkeypatch.setitem(config.EXPLORERS["blockscout"], "direct_hosts", (98866,))
    monkeypatch.setitem(config.EXPLORER_LOG_ROUTES, 98866, ["blockscout"])
    monkeypatch.setenv("BLOCKSCOUT_API_KEY", "secret")
    e = ex.ExplorerLogs.__new__(ex.ExplorerLogs)
    url, base = e._endpoint("blockscout", 98866)
    assert url == "https://explorer.plume.org/api" and base == {}
    assert "blockscout" in e.configured(98866)
    seen = {}

    class H:
        def get(self, url, params=None, deadline=None):
            seen.update(url=url, params=dict(params))
            return {"status": "1", "message": "OK", "result": []}
    e.http, e.requests, e._errors, e._deadline = H(), {}, [], None
    monkeypatch.setattr(e, "_check_budget", lambda: None, raising=False)
    monkeypatch.setattr(e, "_pace_sleep", lambda s: None, raising=False)
    e._call("blockscout", 98866, {"module": "logs"})
    assert "apikey" not in seen["params"] and seen["url"].startswith("https://explorer.plume.org")


# ================================================================================================== 1h
def test_1h_a_view_short_of_its_window_is_c_like_its_headline():
    rows = [{"Tab": "A1 Infrastructure", "Project": "Near", "Column": "Settlement volume, trailing 365d ($)",
             "Status": "MISSING", "Reason / route / error": "MISSING [C — NEEDS HISTORY] — p2p_transfer_volume_usd is "
             "stored from 2026-04-01; the 365-day window needs it from 2025-10-14 — valid from 2027-03-31 | n/a"},
            {"Tab": "Trajectory", "Project": "Near", "Column": "Settlement volume, trailing 365d ($)",
             "Status": "MISSING", "Reason / route / error": "MISSING [D — ROUTE NOT FILLED] — settlement_volume_365d_usd "
             "— x | HISTORY BUG (a route exists and has not filled it; cost: as its inputs): settlement_volume_365d_usd "
             "from nan — a read-time view: "
             "it reaches as far back as its inputs"},
            {"Tab": "Trajectory", "Project": "Near", "Column": "Other", "Status": "MISSING",
             "Reason / route / error": "MISSING [D — ROUTE NOT FILLED] — x — a read-time view"}]
    moves = ca.propagate_view_classes(rows)
    assert moves == [("Trajectory", "Near", "Settlement volume, trailing 365d ($)", "D — ROUTE NOT FILLED",
                      "C — NEEDS HISTORY")]
    assert rows[1]["Reason / route / error"].startswith("MISSING [C — NEEDS HISTORY] — p2p_transfer_volume_usd")
    assert "valid from 2027-03-31" in rows[1]["Reason / route / error"]
    assert rows[2]["Reason / route / error"].startswith("MISSING [D")      # no headline to follow: stays D


# ================================================================================================== 2b
def test_2b_near_inflow_before_stage_2_is_c_with_its_date(monkeypatch, tmp_path):
    import references as rf
    monkeypatch.setattr(rf, "ROOT", tmp_path)                  # no stage-2 answer on disk
    res = [x for x in rf.check({("Near", "price_usd"): _g("Near", "price_usd", ["2026-10-12"], [2.0])},
                               projects={"Near"}) if x["metric"].endswith("_alltime_inflow")]
    assert len(res) == 3
    for x in res:
        assert x["status"] == "MISSING" and x["detail"].startswith("MISSING [C — NEEDS HISTORY]")
        assert f"valid from {config.NEAR_STAGE2_BY}" in x["detail"]


# ================================================================================================== 3a / 3b
def test_3a_a_release_day_with_nothing_forwarded_to_staking_is_a_measured_zero():
    sp = config.STAKING_FUNDING_SPLIT["Chainlink"]
    days = pd.date_range("2025-12-18", "2025-12-24")
    groups = {("Chainlink", sp["lumps"]): _g("Chainlink", sp["lumps"], ["2025-12-18"], [100.0]),
              ("Chainlink", sp["forwarded"]): _g("Chainlink", sp["forwarded"], ["2025-12-20"], [100.0]),
              ("Chainlink", sp["funding"]): _g("Chainlink", sp["funding"], ["2025-12-20"], [40.0])}
    total = pd.Series(100.0, index=days)
    cols = groups[("Chainlink", sp["lumps"])].columns
    bw._staking_funding_split(groups, "Chainlink", sp, total, cols)
    out = groups[("Chainlink", sp["out"])].set_index("date")["value"]
    assert float(out.loc[pd.Timestamp("2025-12-18")]) == 40.0
    assert list(out.index) == list(days) and float(out.loc[pd.Timestamp("2025-12-24")]) == 0.0


def test_3b_the_recorded_reference_reads_n_a_by_its_own_words():
    """3b: the reference's reason said "stale", which the status-honesty rule reads as CHECK (it was a word in the
    reason, not a stale series). The reason now says what it records — and the honesty rule is unchanged."""
    import references as rf
    route = rf.ROUTES[("Near", "g6_circulating_nearblocks_stale")]
    assert route[0] == "recorded" and "retired 2026-10-13" in route[1]
    rows = [{"Tab": "References", "Project": "Near", "Column": "g6_circulating_nearblocks_stale (NEAR)",
             "Status": "N/A BY DESIGN", "Reason / route / error": f"N/A BY DESIGN — recorded, not compared: {route[1]}"}]
    ca.honest_statuses(rows)
    assert rows[0]["Status"] == "N/A BY DESIGN"
    stale = [dict(rows[0], **{"Reason / route / error": "n/a — the series is stale"})]
    ca.honest_statuses(stale)
    assert stale[0]["Status"] == "CHECK"                     # the rule itself still holds


# ================================================================================================== 4
def test_4_a_disabled_ultrasound_block_reads_retired_and_answered():
    from fetch import gaps
    p = config.PROJECT_BY_NAME["Ethereum"]
    reason, _sugg = gaps._tier_note(p, "beacon_validators_eth", {})
    assert reason.startswith("RETIRED ROUTE 2026-10-05") and "answered, not open" in reason
    assert gaps._priority("Ethereum", "beacon_validators_eth", reason) == gaps.P_SUPPRESSED


# ================================================================================================== 5
def test_5_plume_share_is_built_over_weekly_turns(tmp_path, monkeypatch):
    from fetch import llama
    from fetch.base import FetchOutput
    monkeypatch.setitem(config.RWA_SHARE_TURN, "calls_per_run", 2)
    t1, t2 = int(pd.Timestamp("2026-10-20").timestamp()), int(pd.Timestamp("2026-10-21").timestamp())
    protos = [{"slug": s, "category": "RWA"} for s in ("a", "b", "c")] + [{"slug": "dex", "category": "Dexs"}]
    body = {"a": {"Plume Mainnet": 10.0, "Ethereum": 90.0}, "b": {"Ethereum": 100.0, "Ethereum-borrowed": 999.0},
            "c": {"Plume Mainnet": 30.0, "borrowed": 5.0}}
    calls = []

    class H:
        def get(self, url, params=None):
            calls.append(url)
            if url.endswith("/protocols"):
                return protos
            slug = url.rsplit("/", 1)[-1]
            return {"chainTvls": {ch: {"tvl": [{"date": t1, "totalLiquidityUSD": v}, {"date": t2, "totalLiquidityUSD": v}]}
                                  for ch, v in body[slug].items()}}
    d = llama.DefiLlama.__new__(llama.DefiLlama)
    d.http, d._today = H(), "2026-10-22"
    d.daily = type("D", (), {"f": tmp_path / "daily-checks.json"})()
    proj = [config.PROJECT_BY_NAME["Plume"]]
    out = FetchOutput()
    d.rwa_share(proj, out)                                       # turn starts: /protocols + 2 protocols
    assert not [x for x in out.frames if x is not None and not x.empty]
    assert any("IN PROGRESS — 2 of 3" in e.message for e in out.log)
    out = FetchOutput()
    d.rwa_share(proj, out)                                       # the third protocol, then the share
    fr = {x["metric"].iloc[0]: x for x in out.frames if x is not None and not x.empty}
    assert abs(float(fr["market_share_category_usd"]["value"].iloc[-1]) - 230.0) < 1e-9   # 100 + 100 + 30
    assert abs(float(fr["market_share_pct"]["value"].iloc[-1]) - 40.0 / 230.0) < 1e-12
    assert sum(1 for c in calls if "/protocol/" in c) == 3
    out = FetchOutput()
    d.rwa_share(proj, out)                                       # the same week: not again
    assert not [x for x in out.frames if x is not None and not x.empty] and len(calls) == 4


# ================================================================================================== 6
def test_6_two_breaks_on_one_date_read_the_same_whatever_the_hash_seed():
    code = ("import sys; sys.path.insert(0, %r); import trajectory, pandas as pd; "
            "out=[(pd.Timestamp('2026-09-12'), m, 'a', 'b') for m in {'gross_burn_tokens', 'actual_buyback_tokens', "
            "'x_tokens'}]; "
            "import trajectory as t; t._own_breaks=lambda p,m,g,s: [o for o in out if o[1]==m]; "
            "t._inputs=lambda p,m,g: (); "
            "print([b[1] for b in t.basis_breaks('Hyperliquid', {'gross_burn_tokens','actual_buyback_tokens','x_tokens'},"
            " {}, pd.Timestamp('2026-01-01'))])") % str(ROOT)
    got = set()
    for seed in ("1", "2", "3", "4"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        got.add(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                               check=True).stdout.strip())
    assert got == {"['actual_buyback_tokens', 'gross_burn_tokens', 'x_tokens']"}


# ================================================================================================== 8
def test_8_wiring_complete_counts_outside_aerodrome_and_world_mobile(tmp_path, monkeypatch, capsys):
    import csv
    import json
    import check_offline_items as coi
    p = tmp_path / "coverage_audit.csv"
    rows = [{"Tab": "CP", "Column": "c1", "Project": "Near", "Status": "LIVE-UNVERIFIED", "Reason / route / error": ""},
            {"Tab": "CP", "Column": "c2", "Project": "Aerodrome", "Status": "UNWIRED", "Reason / route / error": "x"},
            {"Tab": "CP", "Column": "c3", "Project": "Sky", "Status": "MISSING",
             "Reason / route / error": "MISSING [E — NO SOURCE] — y"}]
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (tmp_path / "wiring.json").write_text(json.dumps({"rows": []}), encoding="utf-8")
    monkeypatch.setenv("TOKEN_METRICS_COVERAGE_CSV", str(p))
    coi.wiring_complete()
    o = capsys.readouterr().out
    assert "  WIRING COMPLETE\n" in o and "Aerodrome (apart): UNWIRED 1" in o
    rows.append({"Tab": "CP", "Column": "c4", "Project": "Chainlink", "Status": "MISSING",
                 "Reason / route / error": "MISSING [D — ROUTE NOT FILLED] — z"})
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    coi.wiring_complete()
    o = capsys.readouterr().out
    assert "  WIRING COMPLETE\n" not in o and "Chainlink 1" in o and "MISSING D" in o and "1 open cell(s)" in o


def test_8_a_not_shown_series_without_any_reason_is_open(tmp_path, monkeypatch, capsys):
    import csv
    import json
    import check_offline_items as coi
    p = tmp_path / "coverage_audit.csv"
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Tab", "Column", "Project", "Status", "Reason / route / error"])
        w.writeheader()
        w.writerow({"Tab": "CP", "Column": "c", "Project": "Near", "Status": "VERIFIED", "Reason / route / error": ""})
    (tmp_path / "wiring.json").write_text(json.dumps({"rows": [
        {"project": "Ether.fi", "metric": "staker_count", "class": "NOT SHOWN", "why": "CLOSED 2026-09-24 (config "
                                                                                      "UNAVAILABLE)"},
        {"project": "Near", "metric": "zz_no_reason_tokens", "class": "NOT SHOWN", "why": ""}]}), encoding="utf-8")
    monkeypatch.setenv("TOKEN_METRICS_COVERAGE_CSV", str(p))
    coi.wiring_complete()
    o = capsys.readouterr().out
    assert "zz_no_reason_tokens" in o and "staker_count" not in o and "Near 1" in o
