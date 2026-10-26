"""ROUND D2 (Jake 2026-10-26, head 6537f98) — the data phase, round 2.

3 Merkl = what Merkl ALLOCATED TO USERS (the creator's returns out; allocated-but-unclaimed in; a running campaign to
the engine's computedUntil; a failed read stores nothing).
7 a clean-up that removes rows clears that series' daily read mark.
8 the NEAR lockup read is resumable at one pinned block, paced over two endpoints; 8a the daily run streams each step's
output to its log and closes out a killed run's log on its next start; the task keeps Jake's settings (no cap, battery).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

T = lambda d: int(pd.Timestamp(d).timestamp())  # noqa: E731
E18 = 10 ** 18


def _camp(cid, amount, start, end, **kw):
    return {"campaignId": cid, "amount": str(int(amount * E18)), "startTimestamp": T(start), "endTimestamp": T(end),
            "distributionChainId": 8453, "rewardToken": {"address": "0xTOK", "symbol": "MORPHO", "decimals": 18},
            "creatorAddress": "0xCREATOR", **kw}


# ================================================================================================== 3
def test_3_allocated_basis_counts_allocated_less_returns_spread_to_until():
    from fetch.merkl_campaigns import daily_amounts
    camps = [_camp("0xended", 300, "2026-01-01", "2026-01-04"),
             _camp("0xlive", 100, "2026-01-05", "2026-01-15"),
             _camp("0xunread", 50, "2026-01-01", "2026-01-02"),
             _camp("0xchild", 30, "2026-01-01", "2026-01-04", parentCampaignId="1")]
    alloc = {"0xended": (210 * E18, T("2026-01-04")),            # 300 allocated, 90 credited back to the creator
             "0xlive": (40 * E18, T("2026-01-07"))}              # computed to 01-07 00:00: 40 over two days
    per_day, skipped = daily_amounts(camps, "0xtok", pd.Timestamp("2026-02-01").date(), 8453, set(), allocated=alloc)
    got = {str(k): round(v, 6) for k, v in per_day.items()}
    assert got == {"2026-01-01": 70, "2026-01-02": 70, "2026-01-03": 70, "2026-01-05": 20, "2026-01-06": 20}
    assert any(c == "0xunread" and "allocation to users was not read" in r for c, r in skipped)
    assert any(c == "0xchild" and "sub-campaign" in r for c, r in skipped)


def test_3_only_returns_are_subtracted_not_the_creators_own_earnings():
    from fetch.merkl_campaigns import RETURN_REASONS, returned_to_creator
    assert set(RETURN_REASONS) == {"end_campaign", "reallocation", "Invalid", "no_recipient"}
    got = returned_to_creator({"amount": str(int(70.5 * E18)), "breakdowns": [
        {"reason": "end_campaign", "amount": str(60 * E18)}, {"reason": "reallocation", "amount": str(10 * E18)},
        {"reason": "ERC20_0xc0Df5784f28046D11813356919B869dDA5815B16", "amount": str(E18 // 2)}]})
    assert got == 70 * E18                                       # Plume 0x194409c2: 8 WPLUME earned as a participant
    assert returned_to_creator({"amount": "0"}) == 0
    assert returned_to_creator({"amount": "5"}) is None          # held, no breakdown: a return cannot be told apart


class _Dc:
    def due(self, *a):
        return True

    def done(self, *a):
        pass


def _reader(monkeypatch, users, totals, camps):
    from fetch import merkl_campaigns as mc
    calls = []

    class H:
        def get(self, url, params=None):
            calls.append(url)
            if "/users/" in url:
                return users(url.rsplit("/", 1)[1])
            if url.endswith("/rewards/total"):
                return totals(params["campaignId"])
            return camps if params.get("page") == 0 else []
    monkeypatch.setattr(mc, "_now", lambda: T("2026-01-10 12:00"))
    monkeypatch.setattr(mc, "today", lambda: pd.Timestamp("2026-01-10"))
    return mc.MerklCampaigns(http=H(), daily_checks=_Dc()), calls


SPEC = {"url": "https://api.merkl.xyz/v4/campaigns", "creator": "0xCREATOR", "metric": "x",
        "chains": {8453: {"name": "Base", "token": "0xTOK"}}}


def test_3_the_reader_stores_allocated_to_users_and_stops_at_computed_until(monkeypatch):
    from fetch.base import FetchOutput
    camps = [_camp("0xended", 300, "2026-01-01", "2026-01-04"),
             _camp("0xlive", 100, "2026-01-05", "2026-01-15",
                   campaignStatus={"computedUntil": T("2026-01-08 06:00")}),
             _camp("0xfuture", 999, "2026-02-01", "2026-02-10")]
    users = {"0xended": {"amount": str(90 * E18), "breakdowns": [{"reason": "end_campaign", "amount": str(90 * E18)}]},
             "0xlive": {"amount": "0", "breakdowns": []}}
    totals = {"0xended": {"amount": str(300 * E18)}, "0xlive": {"amount": str(31 * E18)}}
    m, calls = _reader(monkeypatch, lambda c: users[c], lambda c: totals[c], camps)
    out = FetchOutput()
    m._project("Morpho", SPEC, out)
    f = [x for x in out.frames if x is not None and not x.empty][0]
    s = f.assign(date=pd.to_datetime(f["date"]).dt.strftime("%Y-%m-%d")).set_index("date")["value"].round(6)
    # 0xended: 300 − 90 over 3 days; 0xlive: 31 over 01-05 00:00..01-08 06:00 (3.25 days); the series ends 01-07
    assert s.to_dict() == {"2026-01-01": 70, "2026-01-02": 70, "2026-01-03": 70, "2026-01-04": 0,
                           "2026-01-05": round(31 / 3.25, 6), "2026-01-06": round(31 / 3.25, 6),
                           "2026-01-07": round(31 / 3.25, 6)}
    assert not any("0xfuture" in c for c in calls)               # not started: nothing to read
    assert any("ends 2026-01-07: a running campaign is computed by Merkl only to 2026-01-08 06:00" in e.message
               for e in out.log)


def test_3_a_failed_total_read_stores_nothing(monkeypatch):
    from fetch.base import FetchOutput
    camps = [_camp("0xended", 300, "2026-01-01", "2026-01-04")]
    m, _ = _reader(monkeypatch, lambda c: {"amount": "0", "breakdowns": []}, lambda c: {"error": "down"}, camps)
    out = FetchOutput()
    m._project("Morpho", SPEC, out)
    assert not [f for f in out.frames if f is not None and not f.empty]
    assert any("NOTHING STORED" in e.message and "no allocated total read" in e.message for e in out.log)


def test_3_the_schedule_references_are_recorded_and_the_allocation_is_compared():
    import references as rf
    for k in ("merkl_rewards_rate", "g6_merkl_base_campaign_total"):
        r = rf.ROUTES[("Morpho", k)]
        assert r[0] == "recorded" and "schedule basis" in r[1] and "ALLOCATED TO USERS" in r[1]
    route = rf.ROUTES[("Morpho", "d2_merkl_base_campaign_allocated")]
    row = [r for r in rf.load() if (r["project"], r["metric"]) == ("Morpho", "d2_merkl_base_campaign_allocated")][0]
    assert float(row["value"]) == pytest.approx(199_378.3625 - 61_137.478, abs=0.01)
    assert str(row["source_type"]).startswith("same-source")
    # the allocated series of the live read (2026-10-26): 4,608.03 a day inside the campaign
    d = pd.date_range("2026-09-15", "2026-10-25")
    v = [4608.0295 if x <= pd.Timestamp("2026-10-20") else 4822.0 if x == pd.Timestamp("2026-10-21") else 5464.1
         for x in d]
    g = pd.DataFrame({"date": d, "project": "Morpho", "metric": route[1], "value": v, "source": "merkl_campaigns",
                      "tier": 1})
    got, how, err = rf._our_value(route, {("Morpho", route[1]): g}, "Morpho", pd.Timestamp("2026-10-19"), 138_240.88)
    assert not err and got == pytest.approx(138_240.88, abs=0.1)


def test_3_labels_say_allocated_to_users():
    for m in ("merkl_campaigns_non_ethereum_tokens", "merkl_campaigns_plume_tokens"):
        assert "ALLOCATED TO USERS" in config.METRICS[m]["label"]
    assert "ALLOCATED TO USERS" in config.CREDIBILITY_WIRING["Morpho"]["in_merkl_off_ethereum"]["ref"]["why"]
    assert "INCLUDES the off-Ethereum claims" in config.CREDIBILITY_WIRING["Morpho"]["in_merkl_off_ethereum"]["ref"]["why"]


# ================================================================================================== 4
def test_4a_chainlinks_six_page_wallets_join_the_issuer_set_with_their_source():
    cs = config.PROJECT_BY_NAME["Chainlink"]["contracts"]
    page = {k: c for k, c in cs.items() if k.startswith("noncirc_cl_page_")}
    assert len(page) == 6 and sum(1 for k in cs if k.startswith("noncirc_")) == 33
    for c in page.values():
        assert c["metric_override"] == "noncirculating_holding_tokens"
        assert c["source_url"] == "https://chain.link/circulating-supply" and c["verified"] == "2026-10-26"
    # the insider check reads every non-circulating wallet (check_offline_items.insider_stake_check)
    listed = {a.lower() for _k, a, *_ in config._NONCIRC_WALLETS_FIRST_PARTY["Chainlink"]}
    assert {c["address"].lower() for c in page.values()} <= listed
    assert config.INSIDER_SHARE_ROUTES["Chainlink"]["cell"].startswith("MISSING E — ")
    assert "33" in config.INSIDER_SHARE_ROUTES["Chainlink"]["cell"]


def test_4bc_share_token_checks_hold_the_approved_wallets_each_with_source_and_date():
    want = {"Ether.fi": {"0xe67a6ccc7ded59ef055194ed6402465e738339a8", "0xbe2cfe1a304b6497e6f64525d0017abab7a5e8cb",
                         "0x0c83eae1fe72c390a02e426572854931eeff93ba"},
            "Maple": {"0xd6d4bcde6c816f17889f1dd3000af0261b03a196"},
            "Pendle": {"0x8270400d528c34e1596ef367eedec99080a1b592", "0xe6f0489ed91dc27f40f9dbe8f81fccbfc16b9cb1",
                       "0x68d8d192df476bc01895e16b0ed3945673d777ac"}}
    counts = {"Ether.fi": 13, "Maple": 9, "Pendle": 6}
    for n, addrs in want.items():
        stake = {k: c for k, c in config.PROJECT_BY_NAME[n]["contracts"].items() if k.startswith("insider_stake_")}
        assert len(stake) == counts[n], n
        new = [c for c in stake.values() if c["address"].lower() in addrs]
        assert len(new) == len(addrs), n
        assert all(c["verified"] == "2026-10-26" and c["source_url"].startswith("https://") for c in new), n
        mon = config.INSIDER_SHARE_MONITOR[n]
        assert f"the {counts[n]} issuer-side wallets checked hold 0" in mon["not_wired"]
        assert mon["not_wired"].startswith("MISSING E — ") and len(mon["not_wired"]) <= 255
    # Ether.fi's Q18 wallets are non-circulating like the other five Q18 addresses; the admin Safes / timelocks of
    # Deployed.s.sol are NOT on file and NOT added (Jake: "already on file")
    ef = config.PROJECT_BY_NAME["Ether.fi"]["contracts"]
    assert {"noncirc_foundation_q18_1", "noncirc_foundation_q18_2"} <= set(ef)
    every = {c["address"].lower() for c in ef.values()}
    for a in ("0x2aca71020de61bb532008049e1bd41e451ae8adc", "0xcdd57d11476c22d265722f68390b036f3da48c21",
              "0x9f26d4c958fd811a1f59b01b86be7dffc9d20761", "0xcd425f44758a08baab3c4908f3e3de5776e45d7a"):
        assert a not in every


def test_4d_sky_numerator_is_the_endowments_locked_legs_only():
    assert config.INSIDER_LOCK["Sky"][:2] == ("sky_endowment_locked_tokens", "locked_tokens")
    cs = config.PROJECT_BY_NAME["Sky"]["contracts"]
    legs = {k: c for k, c in cs.items() if c.get("metric_override") == "sky_endowment_locked_tokens"}
    assert set(legs) == {"insider_lock_endowment_sff", "insider_lock_endowment_ff"}
    assert all(c["kind"] == "lockstake_owner_ink" and c["engine"] for c in legs.values())
    # the liquid legs stay out of the numerator
    assert not any(c["kind"] == "treasury_holding" for c in legs.values())


GEOD_PAGE = ('{"filedDate":"2025-08-19","filingStatus":"current","isLatestFiling":true,"x":1,'
             '{"answerMarkdown":"There is no current GEOD or other GEOD related rewards being earned by any locked '
             'wallet team or investor. We commit to 3 should it change.","id":"q11","ordinal":11}')


def test_4e_geodnet_filing_check_confirms_only_the_same_current_filing_and_answer():
    from fetch.filing_statements import check_filing
    spec = config.PROJECT_BY_NAME["GEODNET"]["filing_statements"][0]
    assert check_filing(GEOD_PAGE, spec) == ""
    assert "superseded" in check_filing(GEOD_PAGE.replace("2025-08-19", "2026-11-02"), spec)
    assert "no longer marked current" in check_filing(GEOD_PAGE.replace('"isLatestFiling":true',
                                                                         '"isLatestFiling":false'), spec)
    assert "no longer says it" in check_filing(GEOD_PAGE.replace("no current GEOD", "some GEOD"), spec)
    assert config.stale_after_days("GEODNET", "insider_stake_filing_tokens", 7) == 90


def test_4e_the_reader_stores_the_zero_dated_the_read_day_or_nothing(monkeypatch):
    from fetch import filing_statements as fs
    from fetch.base import FetchOutput
    monkeypatch.setattr(fs, "today", lambda: pd.Timestamp("2026-10-26"))
    import fetch.scrape as sc
    monkeypatch.setattr(sc, "robots_verdict", lambda u: (True, "ok"))
    spec = config.PROJECT_BY_NAME["GEODNET"]["filing_statements"][0]
    for page, stored in ((GEOD_PAGE, True), (GEOD_PAGE.replace("2025-08-19", "2026-11-02"), False)):
        r = fs.FilingStatements(http=type("H", (), {"get_text": lambda self, u: page})())
        out = FetchOutput()
        r._one("GEODNET", spec, out)
        fr = [f for f in out.frames if f is not None and not f.empty]
        if stored:
            assert fr[0]["value"].tolist() == [0.0] and str(fr[0]["date"].iloc[0])[:10] == "2026-10-26"
        else:
            assert not fr and any("NOTHING STORED" in e.message and "superseded" in e.message for e in out.log)


def _build_with(tmp_path, monkeypatch, project, extra):
    """A real-dump build of one project with `extra` rows [(metric, date, value)] added to its store."""
    import sqlite3
    import openpyxl
    import store as sm
    import build_workbook as bw
    import xlcalc
    from real_dump import load, to_store
    payload = load(project)
    monkeypatch.setitem(sys.modules, "recalc", None)
    db = tmp_path / "metrics.db"
    to_store(payload, db)
    con = sqlite3.connect(db)
    con.executemany("INSERT OR REPLACE INTO metrics (date, project, metric, value, source, tier, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", [(d, project, m, v, "test", 1, d) for m, d, v in extra])
    con.commit()
    con.close()
    monkeypatch.setattr(sm, "DB_PATH", str(db))
    st = sm.Store(str(db))
    try:
        bw.build_workbook(st, tmp_path / "wb.xlsx", asof=pd.Timestamp(payload["meta"]["asof"]), only=[project])
    finally:
        st.close()
    wb = openpyxl.load_workbook(tmp_path / "wb.xlsx")
    return wb, xlcalc.Workbook(wb), payload


def test_4e_geodnet_insider_share_is_the_dated_zero_and_lapses_after_90_days(tmp_path, monkeypatch):
    from test_overnight_ab5f2a9 import _get, _row
    from real_dump import load
    asof = pd.Timestamp(load("GEODNET")["meta"]["asof"])
    for sub, day, want in (("held", asof - pd.Timedelta(days=3), 0), ("lapsed", asof - pd.Timedelta(days=120), None)):
        (tmp_path / sub).mkdir()
        wb, X, _ = _build_with(tmp_path / sub, monkeypatch, "GEODNET",
                               [("insider_stake_filing_tokens", str(day.date()), 0.0)])
        cell = _get(_row(wb, X, "CP Comparability", "GEODNET"), "Insider share of staked / locked supply")
        if want is None:
            assert str(cell).startswith("MISSING E — the dated zero lapsed") and "2025-08-19" in str(cell), cell
        else:
            assert cell == 0, cell


def test_4d_sky_insider_share_reads_the_endowments_locked_share(tmp_path, monkeypatch):
    from test_overnight_ab5f2a9 import _get, _row
    from real_dump import load
    p = load("Sky")
    asof = str(pd.Timestamp(p["meta"]["asof"]).date())
    locked = [r for r in p["metrics"] if r["metric"] == "locked_tokens" and r["date"][:10] <= asof]
    last = max(locked, key=lambda r: r["date"])
    wb, X, _ = _build_with(tmp_path, monkeypatch, "Sky", [("sky_endowment_locked_tokens", asof, 2_930_760_603.60)])
    cell = _get(_row(wb, X, "CP Comparability", "Sky"), "Insider share of staked / locked supply")
    assert cell == pytest.approx(2_930_760_603.60 / float(last["value"]), rel=1e-9)


def test_4f_near_payer_scan_counts_operators_and_names_every_other_receiver(monkeypatch):
    import base64
    import json
    from fetch import nearblocks as nb
    from fetch.base import FetchOutput
    state = {"Running": {"parameters": {"participants": {"participants": [["op-a.near", 1, {}], ["op-b.near", 2, {}]]}}}}
    ns = lambda d: str(int(pd.Timestamp(d).value))  # noqa: E731
    rows = [{"predecessor_account_id": "treasury.near", "receiver_account_id": "op-a.near",
             "actions_agg": {"deposit": str(4600 * 10 ** 24)}, "block": {"block_timestamp": ns("2026-02-03 10:00")},
             "outcome": {"status": True}},
            {"predecessor_account_id": "treasury.near", "receiver_account_id": "ecotreasury4.multisignature.near",
             "actions_agg": {"deposit": str(4_900_000 * 10 ** 24)},
             "block": {"block_timestamp": ns("2026-04-15 13:00")}, "outcome": {"status": True}},
            {"predecessor_account_id": "someone.near", "receiver_account_id": "treasury.near",
             "actions_agg": {"deposit": str(9 * 10 ** 24)}, "block": {"block_timestamp": ns("2026-03-01")},
             "outcome": {"status": True}},
            {"predecessor_account_id": "treasury.near", "receiver_account_id": "op-b.near",
             "actions_agg": {"deposit": str(1 * 10 ** 24)}, "block": {"block_timestamp": ns("2025-12-01")},
             "outcome": {"status": True}}]                       # before HSP-007: outside the series

    class H:
        def post(self, url, json_body=None):
            return {"result": {"result": list(json.dumps(state).encode())}}
    monkeypatch.setattr(nb, "today", lambda: pd.Timestamp("2026-04-20"))
    monkeypatch.setenv("NEARBLOCKS_API_KEY", "k")
    r = nb.NearBlocks(http=H())
    monkeypatch.setattr(r, "_get", lambda url, params, key: {"data": rows, "meta": {}})
    spec = config.PROJECT_BY_NAME["Near"]["nearblocks"]
    out = FetchOutput()
    r._payer_out("Near", spec, spec["payer_out"], out)
    f = [x for x in out.frames if x is not None and not x.empty][0]
    s = f.assign(date=pd.to_datetime(f["date"])).set_index("date")["value"]
    assert s.index.min() == pd.Timestamp("2026-01-29") and s.index.max() == pd.Timestamp("2026-04-19")
    assert s.sum() == pytest.approx(4600) and s[pd.Timestamp("2026-02-03")] == pytest.approx(4600)
    assert any("ecotreasury4.multisignat 4,900,000" in e.message for e in out.log)
    assert base64 and nb.signer_participants(H(), spec["payer_out"]["recipients"]) == {"op-a.near", "op-b.near"}


def test_4f_near_b_stays_e_with_the_trace_and_the_route_is_declared_not_shown():
    ev = config.FLOW_LINE_EVIDENCE[("Near", "Incentives paid beside: supply side (b)")]
    assert ev["verdict"] == "class_e" and "treasury.near" in ev["evidence"] and "House of Stake" in ev["evidence"]
    assert "b" not in config.OUTFLOW_CATEGORIES["Near"]
    assert ("Near", "near_mpc_operator_payments_tokens") in config.NOT_SHOWN


# ================================================================================================== 5
def _spendle(rows):
    """A spendle/data payload: rows of (start date, revenues, airdropInUSDs, buybackAmounts)."""
    return {"sPendleHistoricalData": {"timestamps": [T(d) for d, *_ in rows], "revenues": [r for _, r, _a, _b in rows],
                                      "airdropInUSDs": [a for _, _r, a, _b in rows],
                                      "buybackAmounts": [b for *_x, b in rows], "aprs": [0] * len(rows)},
            "lastEpochApr": 0}


def test_5_the_reader_stores_complete_epochs_revenue_less_airdrops_dated_at_the_start():
    from fetch import pendle_epochs as pe
    from fetch.base import FetchOutput
    spec = config.PROJECT_BY_NAME["Pendle"]["spendle_epochs"]
    payload = _spendle([("2026-03-24", 1_507_514.07, 973_833.38, 196_115), ("2026-10-06", 264_054.96, 0, 0),
                        ("2026-10-20", 142_207.83, 0, 0)])                       # 10-20: in progress on 10-27
    out = FetchOutput()
    pe.PendleEpochs._epoch_revenues("Pendle", spec, payload, pd.Timestamp("2026-10-27"), out)
    f = [x for x in out.frames if x is not None and not x.empty][0]
    got = {str(pd.Timestamp(d).date()): round(v, 2) for d, v in zip(f["date"], f["value"])}
    assert got == {"2026-03-24": 533_680.69, "2026-10-06": 264_054.96}
    assert config.stale_after_days("Pendle", "pendle_epoch_revenue_usd", 7) == 30


def test_5_the_cross_check_sums_the_same_utc_days_of_q0s_complete_epochs():
    import credibility as cred
    days = pd.date_range("2026-07-28", "2026-10-26")
    rev = pd.DataFrame({"date": days, "project": "Pendle", "metric": "revenue_usd", "value": 10_000.0})
    ref = pd.DataFrame({"date": pd.to_datetime(["2026-07-28", "2026-08-11", "2026-08-25", "2026-10-06"]),
                        "project": "Pendle", "metric": "pendle_epoch_revenue_usd", "value": [1.0, 150_000.0, 150_000.0,
                                                                                               150_000.0]})
    long = pd.concat([rev, ref])
    asof = pd.Timestamp("2026-10-27")                  # Q0 from 07-29: the 07-28 epoch starts before it — out
    ours, last, how = cred._pendle_epoch_revenue("Pendle", {}, long, asof, side="ours")
    theirs, _l, _h = cred._pendle_epoch_revenue("Pendle", {}, long, asof, side="ref")
    assert ours == 3 * 14 * 10_000.0 and theirs == 450_000.0 and last == "2026-10-19"
    assert "2026-08-11, 2026-08-25, 2026-10-06" in how
    # a missing day of ours takes the whole epoch out
    gap = long[~((long.metric == "revenue_usd") & (long.date == pd.Timestamp("2026-08-30")))]
    assert cred._pendle_epoch_revenue("Pendle", {}, gap, asof, side="ref")[0] == 300_000.0
    row = config.CREDIBILITY["Pendle"]["in_revenue"]
    assert row["ref"]["tol"] == 10.0 and "BASIS" in row["ref"]["note"] and "airdrop" in row["ref"]["note"]
    assert "revenues_closed" not in config.PROJECT_BY_NAME["Pendle"]["spendle_epochs"]


# ================================================================================================== 7
def test_7_a_clean_up_that_removes_rows_clears_the_series_daily_read_mark(tmp_path, monkeypatch):
    """Jake's 17:37 run of 2026-10-26: D1-1 removed Near's DefiLlama rows, then the DefiLlama tier skipped Near as
    "read complete through 2026-10-25 earlier today" — the days stayed empty. The marks now go with the rows."""
    import json
    import logging
    import store as sm
    import token_metrics as tm
    from fetch.logcache import DailyChecks
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(config, "RUN_CLEANUPS", tuple(c for c in config.RUN_CLEANUPS if c["id"] == "D1-1"))
    dc = DailyChecks()
    dc.f.parent.mkdir(parents=True, exist_ok=True)
    marks = {"defillama:project:Near": "2026-10-26",
             "defillama:project:Near:metrics": "dex_volume_usd,fees_usd,intents_fees_usd,revenue_usd",
             "defillama:project:Pendle": "2026-10-26", "defillama:project:Pendle:metrics": "fees_usd",
             "merkl_campaigns:Morpho": "2026-10-26"}
    dc.f.write_text(json.dumps(marks))
    st = sm.Store(str(tmp_path / "metrics.db"))
    st.upsert(pd.DataFrame([
        {"date": "2026-10-23", "project": "Near", "metric": "dex_volume_usd", "value": 8.27e6,
         "source": "defillama:dexs:near", "tier": 1},
        {"date": "2026-10-22", "project": "Near", "metric": "intents_fees_usd", "value": 11_925.0,
         "source": "defillama:near-intents", "tier": 1}]))
    lines = tm.apply_run_cleanups(st, "r", logging.getLogger("t"))
    left = json.loads(dc.f.read_text())
    assert "defillama:project:Near" not in left and "defillama:project:Near:metrics" not in left
    assert left["defillama:project:Pendle"] == "2026-10-26" and left["merkl_campaigns:Morpho"] == "2026-10-26"
    assert dc.get("defillama:project:Near") is None                   # the DefiLlama tier reads Near again this run
    assert any("daily read mark defillama:project:Near cleared" in x for x in lines)
    # nothing left to remove: no mark is touched
    dc.done("defillama:project:Near", "2026-10-26")
    assert tm.apply_run_cleanups(st, "r2", logging.getLogger("t")) == []
    assert dc.get("defillama:project:Near") == "2026-10-26"
    st.close()


def test_7_forget_series_matches_the_three_key_shapes(tmp_path):
    import json
    from fetch.logcache import DailyChecks
    dc = DailyChecks(tmp_path)
    dc.f.parent.mkdir(parents=True, exist_ok=True)
    dc.f.write_text(json.dumps({"defillama_tvl:project:Near": "d", "defillama_tvl:project:Near:metrics": "tvl_usd",
                                "chain:Near:dex_volume_usd:x": "d", "nearblocks:Near": "d", "nearblocks:Plume": "d"}))
    gone = dc.forget_series("Near", "dex_volume_usd", "nearblocks:x")
    assert gone == ["chain:Near:dex_volume_usd:x", "nearblocks:Near"]       # tvl's list does not name the metric


# ================================================================================================== 8
def test_8_the_pacer_honours_retry_after_else_cools_5s_doubling_to_60s():
    import check_offline_items as coi
    p = coi._NearPacer({"https://a": 0.25}, default=0.0)
    assert p.throttled("https://a", "7") == 7.0                          # the server's Retry-After, as given
    p.ok("https://a")
    assert [p.throttled("https://b") for _ in range(6)] == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0]
    assert p.stats["throttled"] == 7
    slept = []
    p2 = coi._NearPacer({"https://a": 0.25}, default=0.0)
    p2.wait_turn("https://a", sleep=slept.append)
    p2.wait_turn("https://a", sleep=lambda s: (slept.append(s), p2.next_ok.update({"https://a": 0.0})))
    assert slept and 0 < slept[0] <= 0.25                                 # the second call waits its turn
    assert p2.pick(["https://a", "https://b"]) == "https://b"            # the endpoint free soonest goes next


class _R:
    def __init__(self, code, body=None, headers=None):
        self.status_code, self._b, self.headers = code, body, headers or {}

    def json(self):
        if self._b is None:
            raise ValueError("no json")
        return self._b


def test_8_near_post_moves_off_a_throttled_endpoint_and_stops_at_the_first_answer(monkeypatch):
    import check_offline_items as coi
    monkeypatch.setattr(coi, "NEAR_PACER", coi._NearPacer({}, default=0.0))
    calls = []
    answers = {"https://f": [_R(429, headers={"Retry-After": "30"})], "https://n": [_R(200, {"result": {"ok": 1}})]}
    monkeypatch.setattr(coi.requests, "post", lambda url, **kw: (calls.append(url), answers[url].pop(0))[1])
    got = coi._near_post({"x": 1}, urls=("https://f", "https://n"))
    first = next(got)
    assert first == ("https://f", {"_transient": "f: HTTP 429"})
    assert next(got) == ("https://n", {"result": {"ok": 1}}) and calls == ["https://f", "https://n"]
    assert coi.NEAR_PACER.stats["cooled_s"] == 30.0                     # Retry-After honoured on the 429'd host
    # lazy: a good first answer costs ONE call (the other endpoint is not asked)
    calls.clear()
    answers["https://n"] = [_R(200, {"result": {"result": list(b'"5"')}})]
    monkeypatch.setattr(coi, "NEAR_PACER", coi._NearPacer({}, default=0.0))
    coi.NEAR_PACER.next_ok["https://f"] = 1e18                           # f still cooling: n goes first
    assert coi._near_view("a.lockup.near", "get_balance", block_id=9, urls=("https://f", "https://n")) == ("5", "")
    assert calls == ["https://n"]


def _lockup_world(monkeypatch, tmp_path, fail: set, height=500):
    """Three lockups; the ones in `fail` cannot be read (HTTP 429) this time."""
    import check_offline_items as coi
    monkeypatch.setenv("TOKEN_METRICS_LOGCACHE", str(tmp_path))
    f = tmp_path / "l.txt"
    f.write_text("a.lockup.near\nb.lockup.near\nc.lockup.near\n")
    monkeypatch.setenv("NEAR_LOCKUPS_FILE", str(f))
    y = 10 ** 24
    seen = []
    heights = iter([height, height + 999])

    def post(params, method="query", urls=None, attempts=None):
        if method == "block" and "finality" in params:
            return iter([("u", {"result": {"header": {"height": next(heights), "timestamp": T("2026-10-26") * 10 ** 9}}})])
        if method == "block":                                         # the pin still served
            return iter([("u", {"result": {"header": {"height": params["block_id"]}}})])
        raise AssertionError(params)

    def view(acc, m, args=None, block_id=None, pace=0.12, urls=None):
        seen.append((acc, block_id))
        if acc in fail:
            return None, "rpc.mainnet.fastnear.com: HTTP 429"
        return {"get_balance": str(10 * y), "get_locked_amount": str(4 * y), "get_owners_balance": str(6 * y),
                "get_vesting_information": "None"}[m], ""
    monkeypatch.setattr(coi, "_near_post", post)
    monkeypatch.setattr(coi, "_near_view", view)
    monkeypatch.setattr(coi, "_near_code_hash", lambda acc, b, urls=None: ("plain", ""))
    return coi, seen


def test_8_a_rerun_reads_only_the_missing_lockups_at_the_same_block_and_never_totals_part(monkeypatch, tmp_path):
    coi, seen = _lockup_world(monkeypatch, tmp_path, {"c.lockup.near"})
    got = coi.near_lockups(workers=2)
    assert got["height"] == 500 and got["read"] == 2 and got["unreadable"] == 1
    import near_lockups_weekly as nl
    assert nl.read_failures(got) == 1                                   # the weekly script stores nothing
    coi2, seen2 = _lockup_world(monkeypatch, tmp_path, set())
    got2 = coi2.near_lockups(workers=2)
    assert got2["height"] == 500, "resumed at the SAME pinned block"
    assert {a for a, _b in seen2} == {"c.lockup.near"} and {b for _a, b in seen2} == {500}
    assert got2["read"] == 3 and got2["unreadable"] == 0 and got2["locked"] == 12.0
    assert nl.read_day(got2, pd.Timestamp("2027-01-01")) == pd.Timestamp("2026-10-26")   # dated at the block
    # resume=False (or a stale pin) starts over at a new block
    coi3, seen3 = _lockup_world(monkeypatch, tmp_path, set())
    got3 = coi3.near_lockups(resume=False)
    assert got3["height"] == 500 and len({a for a, _b in seen3}) == 3


def test_8_a_pin_older_than_48h_or_no_longer_served_is_not_resumed(monkeypatch):
    import check_offline_items as coi
    served = []
    monkeypatch.setattr(coi, "_near_post", lambda params, method="query", urls=None, attempts=None:
                        (served.append(params), iter(served[-1:] and [("u", {"error": {"name": "UNKNOWN_BLOCK"}})]))[1])
    c = {"height": 9, "pinned_at": "2026-10-26 06:30:00+00:00", "results": {}}
    assert coi._lockup_pin_live(c, now="2026-10-28 07:00:00") is False and served == []   # 48.5 h: not even asked
    assert coi._lockup_pin_live(c, now="2026-10-27 06:00:00") is False                   # in time, block not served
    monkeypatch.setattr(coi, "_near_post", lambda params, method="query", urls=None, attempts=None:
                        iter([("u", {"result": {"header": {"height": 9}}})]))
    assert coi._lockup_pin_live(c, now="2026-10-27 06:00:00") is True
    assert coi.NEAR_LOCKUP_PIN_MAX_H == 48


def test_8a_the_task_matches_what_jake_set_no_limit_and_runs_on_battery():
    """D2-8a: Jake removed the 4 h cap and the stop-on-battery; re-registering must not put either back."""
    reg = (ROOT / "windows" / "register_task.ps1").read_text()
    assert "[int]$LimitHours = 0" in reg and "New-TimeSpan -Hours $LimitHours" in reg and "Hours 4)" not in reg
    assert "if (-not $StopOnBattery) {" in reg and "$settings.StopIfGoingOnBatteries = $false" in reg
    assert "daily_run.ps1" in reg and "-Daily -At $At" in reg and "-Scheduled" in reg
    assert not any(ord(ch) > 127 for ch in reg), "Windows PowerShell 5.1 reads the BOM-less file as ANSI"
    run = (ROOT / "windows" / "daily_run.ps1").read_text()
    assert run.index('"token_metrics.py"') < run.index('"near_lockups_weekly.py"')


def _invoke_step_body(ps: str) -> str:
    i = ps.index("function Invoke-Step(")
    return ps[i:ps.index("\n}\n", i)]


def test_8a_every_step_streams_its_output_to_the_log_as_it_arrives():
    """10-25's log ended 1 minute into the lockup step: the step's output reached the log only after it exited."""
    ps = (ROOT / "windows" / "daily_run.ps1").read_text(encoding="utf-8")
    body = _invoke_step_body(ps)
    assert "[System.IO.FileMode]::Append" in body and "[System.IO.FileShare]::ReadWrite" in body
    assert "$w.AutoFlush = $true" in body and "ForEach-Object {" in body and "$w.WriteLine($s)" in body
    assert "Add-Content -Path $Log -Value $out" not in body, "no write-after-exit of the whole output"
    assert '$env:PYTHONUNBUFFERED = "1"' in ps
    assert body.index('Save-State $label') < body.index("& $Python") < body.index('Save-State ""')
    assert "$script:Steps += ,@($label, $code)" in body
    added = [ln for ln in ps.splitlines() if "D2-8a" in ln or "Save-State" in ln or "Resolve-PreviousRun" in ln]
    assert added and not any(ord(ch) > 127 for ln in added for ch in ln)


def test_8a_a_killed_run_gets_its_final_lines_on_the_next_start():
    ps = (ROOT / "windows" / "daily_run.ps1").read_text(encoding="utf-8")
    fn = ps[ps.index("function Resolve-PreviousRun"):ps.index("function Invoke-Step(")]
    assert "if ($prev.final) { return \"\" }" in fn
    assert 'ProcessName -like "powershell*"' in fn and "still running - left alone" in fn     # a live run untouched
    assert "=== $($prev.open) : KILLED - no exit code" in fn and "daily run FINAL: KILLED $where" in fn
    assert "steps that ran: $ran; not run: $nr" in fn and "Add-Content -Path $prev.log" in fn
    assert "the log does not say which" in fn, "it never guesses the cause"
    # it runs before this run's first line; -Reconcile does only that
    assert ps.index("$PrevKilled = Resolve-PreviousRun") < ps.index('Write-Log "daily run start')
    assert "[switch]$Reconcile" in ps and "if ($Reconcile) {" in ps
    # every way the run ends marks it final; the plan names every unconditional step
    assert ps.count("$FinalWritten = $true") == 2 and ps.count('Save-State ""') >= 3
    import re
    plan = re.search(r"\$Plan = @\((.*?)\)\n", ps, re.S).group(1)
    plan = re.findall(r'"([^"]+)"', plan)
    steps = re.findall(r'Invoke-Step "([^"]+)"', ps)
    assert set(plan) == {s for s in steps if s not in ("BigQuery check (after login)", "token_metrics (rerun)")}
    assert 'if ($PrevKilled -like "*KILLED*")' in ps


def test_8a_the_lockup_read_prints_progress_every_100_with_time_left(monkeypatch, tmp_path, capsys):
    coi, _seen = _lockup_world(monkeypatch, tmp_path, set())
    f = tmp_path / "l.txt"
    f.write_text("".join(f"x{i:03d}.lockup.near\n" for i in range(205)))
    coi.near_lockups(workers=3)
    out = capsys.readouterr().out
    prog = [ln for ln in out.splitlines() if "lockups at block 500" in ln]
    assert [ln.split("/")[0].strip() for ln in prog] == ["… 100", "… 200", "… 205"]
    assert all("min left" in ln for ln in prog) and ".lockup.near" not in "".join(prog)
