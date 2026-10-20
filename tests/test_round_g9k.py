"""
tests/test_round_g9k.py — Round G9 add-on K–N (Jake 2026-10-20), SPEC §19 "G9 K–N". K: CCIP refused days (pricing
steps, re-read of the listed days only, the 90-day coverage that names them); M: MODELLED is its own status; N: the
evidence ladder (SPEC §1 rule 13) and its N3 guard.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402
import test_g8_ccip_value as t8  # noqa: E402
from fetch import ccip_value as cv  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402

ZG = "4426351306075016396"          # 0G: mapped, ranked after every SIBLING_ORDER chain
HTOK = "0x00000000000000000000000000000000000000e1"
GROUP_H = [(t8.HYPER, HTOK, 18, "HX", "g-h"), (t8.ETH, "0x00000000000000000000000000000000000000e2", 18, "HX", "g-h"),
           (t8.ARB, "0x00000000000000000000000000000000000000e3", 18, "HX", "g-h"),
           (t8.BASE, "0x00000000000000000000000000000000000000e4", 18, "HX", "g-h"),
           (ZG, "0x00000000000000000000000000000000000000e5", 18, "HX", "g-h")]


@pytest.fixture
def at(monkeypatch):
    """Pin 'today' (UTC) for fetch.ccip_value."""
    def pin(day):
        monkeypatch.setattr(cv, "today", lambda: pd.Timestamp(day))
    return pin


@pytest.fixture
def directory(monkeypatch):
    monkeypatch.setattr(t8, "DIRECTORY", t8.DIRECTORY + GROUP_H)


def _day(token=HTOK, src=t8.HYPER, dst=ZG, n=10, priced=0):
    """2026-10-19: n messages moving `token` from src to dst, the first `priced` moving USDC instead."""
    ms = [t8.msg(f"0x{i:064x}", f"2026-10-19T12:{i:02d}:00.000Z", src=t8.ETH if i < priced else src,
                 dst=dst, token=t8.USDC_E if i < priced else token, amount=10 ** 18 if i >= priced else 1_000_000)
          for i in range(n)]
    ms.sort(key=lambda m: m["sendTimestamp"], reverse=True)
    return [t8.msg("0xtoday", "2026-10-20T01:00:00.000Z")] + ms + [t8.msg("0xolder", "2026-10-18T23:00:00.000Z")]


# ============================================================================================ K1 pricing steps
def test_k1_a_token_is_priced_by_its_destination_chain_contract(tmp_path, at, directory):
    at("2026-10-20")
    llama = t8.FakeLlama({**t8.PRICES, f"0g:{GROUP_H[4][1]}": (3.0, 18)})
    f, projects = t8.make(tmp_path, t8.FakeCcip(_day()), llama, history_days=1)
    out = FetchOutput()
    f.run(projects, None, out)
    value, src = t8.rows(out, cv.VALUE)["2026-10-19"]
    assert value == pytest.approx(30.0) and src == cv.SRC, "10 HX at the 0G (destination) contract's $3"
    import json
    st = json.loads((tmp_path / ".cache/ccip/state.json").read_text())
    assert st["days"]["2026-10-19"]["dests"] == {f"{t8.HYPER}|{HTOK}": [ZG]}
    asked = [c for _, cs in llama.calls for c in cs]
    assert asked.index(f"0g:{GROUP_H[4][1]}") > asked.index(f"ethereum:{GROUP_H[1][1]}"), \
        "the destination contract is asked only after the source contract and its three siblings"


def test_k1_a_declared_underlying_prices_a_receipt_token_never_an_assumed_one(tmp_path, at, directory):
    at("2026-10-20")
    key = f"{t8.HYPER}|{HTOK}"
    f, projects = t8.make(tmp_path, t8.FakeCcip(_day(dst=t8.ETH)), t8.FakeLlama(), history_days=1)
    out = FetchOutput()
    f.run(projects, None, out)
    assert "2026-10-19" not in t8.rows(out, cv.VALUE), "no price anywhere and nothing declared: refused, never 0"
    f2, projects2 = t8.make(tmp_path / "b", t8.FakeCcip(_day(dst=t8.ETH)), t8.FakeLlama(), history_days=1,
                            declared_underlying={key: {"coin": f"ethereum:{t8.WETH}", "rate": 1.0,
                                                       "source": "the token's docs (test)"}})
    out2 = FetchOutput()
    f2.run(projects2, None, out2)
    assert t8.rows(out2, cv.VALUE)["2026-10-19"][0] == pytest.approx(10 * 2000.0)


# ============================================================================================ K2 re-read listed days
def test_k2_only_the_listed_days_are_read_again_and_their_no_price_answers_asked_again(tmp_path, at, directory):
    at("2026-10-20")
    ms = _day(dst=t8.ETH)
    ccip = t8.FakeCcip(ms)
    f, projects = t8.make(tmp_path, ccip, t8.FakeLlama(), history_days=1)
    out = FetchOutput()
    f.run(projects, None, out)
    assert "2026-10-19" not in t8.rows(out, cv.VALUE)
    # DefiLlama now prices the Ethereum sibling: a plain run would never ask again (the null is remembered) ...
    llama2 = t8.FakeLlama({**t8.PRICES, f"ethereum:{GROUP_H[1][1]}": (2.0, 18)})
    f2, _ = t8.make(tmp_path, ccip, llama2, history_days=1)
    out2 = FetchOutput()
    f2.run(projects, None, out2)
    assert "2026-10-19" not in t8.rows(out2, cv.VALUE), "remembered 'no price' is not re-asked on a routine run"
    # ... the re-read of the listed day asks again, walks once, closes only that day
    n_list = len(ccip.list_calls())
    f3, _ = t8.make(tmp_path, ccip, llama2, history_days=1)
    out3 = FetchOutput()
    f3.run(projects, None, out3, unbounded=True, days=["2026-10-19"])
    assert t8.rows(out3, cv.VALUE)["2026-10-19"][0] == pytest.approx(20.0)
    assert len(ccip.list_calls()) > n_list, "walked again: the destinations are recorded"
    import json
    st = json.loads((tmp_path / ".cache/ccip/state.json").read_text())
    assert st["walks"] == [] and set(st["days"]) == {"2026-10-19"}
    assert any("re-read of 1 listed day" in e.message for e in out3.log)


# ============================================================================================ K3 coverage names days
def test_k3_a_refused_day_is_a_day_not_held_named_on_the_row():
    import build_workbook as bw
    assert ("Chainlink", "ccip_token_value_usd") in config.DENSE_DAILY_FLOWS
    assert "10%" in config.DENSE_DAILY_FLOWS[("Chainlink", "ccip_token_value_usd")]
    days = pd.date_range("2026-07-23", "2026-10-19").difference(
        pd.DatetimeIndex(["2026-09-11", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-30"]))
    s = pd.Series(1.0, index=days)
    asof = pd.Timestamp("2026-10-20")
    miss = bw._dense_missing(s, asof - pd.Timedelta(days=90), asof)
    assert [str(d.date()) for d in miss] == ["2026-09-11", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25",
                                             "2026-09-30"], "today (not closed) is not a missing day"
    assert bw._day_ranges(miss) == "2026-09-11, 2026-09-22..09-25, 2026-09-30"
    assert bw._dense_days(s, asof - pd.Timedelta(days=90), asof) == 83


# ============================================================================================ K4 fees from the detail
def test_k4_the_fee_is_read_from_the_message_detail_for_messages_sent_from_a_fee_chain(tmp_path, at, directory):
    at("2026-10-20")
    WETH_B = "0x4200000000000000000000000000000000000006"
    ms = [t8.msg("0xtoday", "2026-10-20T01:00:00.000Z"),
          t8.msg("0xb2", "2026-10-19T12:00:00.000Z", src=t8.BASE, dst=t8.ETH),                 # no token: still a fee
          t8.msg("0xb1", "2026-10-19T11:00:00.000Z", src=t8.BASE, dst=t8.ETH, token=t8.USDC_E, amount=1_000_000),
          t8.msg("0xe1", "2026-10-19T10:00:00.000Z", src=t8.ETH, dst=t8.BASE, token=t8.USDC_E, amount=1_000_000),
          t8.msg("0xolder", "2026-10-18T23:00:00.000Z")]
    fee = lambda v, amt: {"version": v, "tokenAmounts": [], "fees": {"fixedFeesDetails": {  # noqa: E731
        "tokenAddress": WETH_B, "totalAmount": str(amt)}}}
    details = {"0xb2": fee("2.0.0", 10 ** 15), "0xb1": {**fee("1.6.0", 5 * 10 ** 15),
                                                       "tokenAmounts": [{"sourceTokenAddress": t8.USDC_E,
                                                                         "amount": "1000000"}]},
               "0xe1": fee("2.0.0", 10 ** 18)}
    llama = t8.FakeLlama({**t8.PRICES, f"base:{WETH_B}": (2000.0, 18)})
    f, projects = t8.make(tmp_path, t8.FakeCcip(ms, details), llama, history_days=1,
                          fee_chains={t8.BASE: "base"})
    out = FetchOutput()
    f.run(projects, None, out)
    got = t8.rows(out, cv.fee_metric("base"))
    value, src = got["2026-10-19"]
    assert value == pytest.approx(2.0), "only the 2.0-lane fee: 0.001 WETH x $2,000; the 1.6 fee is in the core"
    assert "1 2.0 message(s); 1 1.x left out" in src
    assert all(e.status != "failed" for e in out.log if cv.fee_metric("base") in e.message)


def test_k4_the_base_leg_joins_the_log_total_only_while_the_arbitrum_overlap_agrees():
    import build_workbook as bw
    o = config.CCIP_FEE_OVERLAP
    days = pd.date_range("2026-10-01", "2026-10-10")

    def g(metric, v):
        return pd.DataFrame({"date": days, "project": "Chainlink", "metric": metric, "value": v, "tier": 1,
                             "source": "x"})
    for api_v, ok in ((101.0, True), (110.0, False)):
        groups = {("Chainlink", o["api"]): g(o["api"], api_v), ("Chainlink", o["log"]): g(o["log"], 100.0),
                  ("Chainlink", o["leg"]): g(o["leg"], 5.0), ("Chainlink", o["into"]): g(o["into"], 50.0)}
        bw.EVIDENCE_RESULTS.clear()
        bw._VIEW_BLOCKS.pop(("Chainlink", o["out"]), None)
        bw._ccip_fee_overlap_view(groups)
        res = bw.EVIDENCE_RESULTS[("Chainlink", o["out"])]
        if ok:
            assert res["verdict"] == "VERIFIED" and float(groups[("Chainlink", o["out"])]["value"].iloc[0]) == 55.0
        else:
            assert res["verdict"] == "CHECK" and ("Chainlink", o["out"]) not in groups
            assert "DISAGREES" in bw._VIEW_BLOCKS[("Chainlink", o["out"])]


# ============================================================================================ L1 EF spread evenly
def _build_groups(name, metric_rows):
    return {(name, m): pd.DataFrame({"date": pd.to_datetime([d for d, _v in rows]), "project": name, "metric": m,
                                     "value": [v for _d, v in rows], "tier": 1, "source": "x"})
            for m, rows in metric_rows.items()}


def test_l1_ef_quarterly_totals_spread_evenly_and_a_block_is_the_pro_rata_blend(monkeypatch):
    import build_workbook as bw
    groups = _build_groups("Ethereum", {"price_usd": [("2026-01-01", 3000.0)]})
    monkeypatch.setitem(bw._WINDOWS, "q0", ("2026-07-22", "2026-10-20"))
    bw.EVIDENCE_RESULTS.clear()
    bw._evidence_views(groups, usd=True)
    s = bw._daily(groups[("Ethereum", "outflow_d_usd")])
    q2 = s[(s.index >= "2026-04-01") & (s.index <= "2026-06-30")]
    assert len(q2) == 91 and q2.sum() == pytest.approx(5_502_930.20) and q2.nunique() == 1
    blend = s[(s.index > "2026-04-23") & (s.index <= "2026-07-21")].sum()
    assert blend == pytest.approx(5_502_930.20 * 68 / 91), "Q-1 holds 68 of Q2-2026's 91 days and nothing after"
    res = bw.EVIDENCE_RESULTS[("Ethereum", "outflow_d_usd")]
    assert res["label"].startswith("PUBLISHED: EF quarterly report") and "spread evenly" in res["note"]
    assert res["verdict"] == "LIVE-UNVERIFIED"


# ============================================================================================ L3 NEAR treasury → m
def test_l3_near_treasury_share_is_line_m_and_out_of_line_a():
    import build_workbook as bw
    assert config.OUTFLOW_CATEGORIES["Near"]["a"] == ("near_validator_rewards_tokens",)
    assert config.ISSUER_MINTS["Near"]["metric"] == "near_treasury_mint_view_tokens"
    groups = _build_groups("Near", {"gross_issuance_tokens": [("2026-10-01", 1000.0), ("2026-10-02", 2000.0)],
                                    "near_treasury_mint_tokens": [("2026-10-02", 200.2)]})
    bw.EVIDENCE_RESULTS.clear()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(bw, "scoped_projects", lambda: [{"name": "Near"}])
    try:
        bw._treasury_split_views(groups)
    finally:
        monkeypatch.undo()
    a = groups[("Near", "near_validator_rewards_tokens")]
    assert list(a["value"]) == [pytest.approx(900.0), pytest.approx(1799.8)]
    assert "MECHANISM" in a["source"].iloc[0] and "(measured)" in a["source"].iloc[1]
    assert bw.EVIDENCE_RESULTS[("Near", "near_validator_rewards_tokens")]["verdict"] == "VERIFIED"
    m = groups[("Near", "near_treasury_mint_view_tokens")]
    assert list(m["value"]) == [pytest.approx(100.0), pytest.approx(200.2)], "counted once: what (a) leaves out is m"
    assert list(a["value"] + m["value"]) == [pytest.approx(1000.0), pytest.approx(2000.0)]


def test_l3_the_epoch_walk_reads_the_treasury_inflow_across_the_mint_block():
    from fetch import near_issuance as ni
    bal = {100: 10 ** 24 * 5, 99: 10 ** 24 * 3}
    blocks = {100: {"header": {"height": 100, "total_supply": str(50 * 10 ** 24), "timestamp_nanosec":
                               str(int(pd.Timestamp("2026-10-18 06:00").value)), "prev_hash": "p100", "epoch_id": "e"},
                    "chunks": []},
              "p100": {"header": {"height": 99, "total_supply": str(30 * 10 ** 24), "timestamp_nanosec": "0",
                                  "prev_hash": "x", "epoch_id": "pe"}},
              50: {"header": {"height": 50, "total_supply": "0", "timestamp_nanosec":
                              str(int(pd.Timestamp("2026-10-10").value)), "prev_hash": "p50", "epoch_id": "e2"}}}

    def rpc(method, params):
        if method == "validators":
            return {"epoch_start_height": 100 if params == [None] else 50}
        if method == "query":
            assert params["account_id"] == "treasury.near"
            return {"amount": str(bal[params["block_id"]]), "locked": "0"}
        return blocks[params["block_id"]]
    eps, _cont, reached = ni.walk(rpc, pd.Timestamp("2026-10-15"), treasury=ni.TREASURY)
    assert reached and eps[0][1] == pytest.approx(20.0) and eps[0][3] == pytest.approx(2.0)


# ============================================================================================ M MODELLED status
def test_m_a_row_built_on_a_modelled_leg_reads_modelled_never_verified():
    import coverage_audit as ca
    assert config.is_modelled("model:inverse_sqrt(locked_tokens)[observed/model=1.02 over 12d]")
    assert config.is_modelled("modelled:hl_reference_yield[k=1.000 on 9 day(s)]")
    rows = [{"Tab": "Trajectory", "Column": "H", "Project": "Hyperliquid", "Status": "VERIFIED",
             "Reason / route / error": "primary check", "Formula as built": "A1 → x", "Value now": 1.0},
            {"Tab": "Trajectory", "Column": "H2", "Project": "Hyperliquid", "Status": "LIVE-UNVERIFIED",
             "Reason / route / error": "", "Formula as built": "A1 → y", "Value now": 1.0}]
    traj = [{"tab": "A1", "project": "Hyperliquid", "header": "H", "modelled_quarters": ["Q-1", "Q-2"]},
            {"tab": "A1", "project": "Hyperliquid", "header": "H2", "modelled_quarters": []}]
    assert ca.mark_modelled(rows, traj, [], {}, pd.Timestamp("2026-10-20")) == 1
    assert rows[0]["Status"] == "MODELLED" and "never VERIFIED" in rows[0]["Reason / route / error"]
    assert "checked:" in rows[0]["Reason / route / error"], "Hyperliquid names the published figures it checked"
    assert rows[1]["Status"] == "LIVE-UNVERIFIED"
    assert "MODELLED" in ca.STATUSES and "MODELLED" in ca.BOARD_COLS


# ============================================================================================ N the evidence ladder
def test_n_the_check_is_a_bound_or_a_reconciliation_and_disagreement_is_check():
    import build_workbook as bw
    days = pd.date_range("2026-07-23", "2026-10-20")
    v = pd.Series(10.0, index=days)
    for rel, chk_v, want in (("upper_bound", 12.0, "VERIFIED"), ("upper_bound", 9.0, "CHECK"),
                             ("lower_bound", 9.0, "VERIFIED"), ("equal", 10.4, "VERIFIED"), ("equal", 12.0, "CHECK")):
        groups = _build_groups("X", {"c": [(d, chk_v) for d in days]})
        e = {"check": {"metric": "c", "relation": rel, "tolerance": 0.05, "onchain": True}}
        verdict, text = bw._evidence_check(e, v, groups, "X", pd.Timestamp("2026-10-20"))
        assert verdict == want, (rel, chk_v, text)
        # a check that is another PUBLISHED figure (not an independent on-chain measurement) never grades VERIFIED
        e["check"]["onchain"] = False
        assert bw._evidence_check(e, v, groups, "X", pd.Timestamp("2026-10-20"))[0] == \
            ("LIVE-UNVERIFIED" if want == "VERIFIED" else want)
    # a check series that does not span the quarter does not judge it
    late = _build_groups("X", {"c": [(d, 99.0) for d in days[60:]]})
    e = {"check": {"metric": "c", "relation": "equal", "tolerance": 0.05, "onchain": True}}
    assert "no quarter holds both" in bw._evidence_check(e, v, late, "X", pd.Timestamp("2026-10-20"))[1]
    assert bw._evidence_check({}, v, {}, "X", pd.Timestamp("2026-10-20"))[0] == "LIVE-UNVERIFIED"


def test_n_every_ladder_entry_carries_its_step_a_source_and_dated_periods():
    for (name, metric), e in config.EVIDENCE_LADDER.items():
        assert e["step"] in config.EVIDENCE_STEPS[1:], (name, metric)
        assert e["source"] and e["read_on"] and e["periods"]
        lab = config.evidence_label(e)
        assert lab.startswith(("PUBLISHED: ", "AGGREGATOR: ", "MECHANISM × PUBLISHED BASE: "))
        for p in e["periods"]:
            assert pd.Timestamp(p["from"]) <= pd.Timestamp(p["to"]) and p["value"] >= 0 and p["quote"]


def test_n_world_mobile_buyback_is_missing_e_with_the_sources_checked_never_an_accepted_na():
    import coverage_audit as ca
    rows = [{"Tab": "A3 Revenue Buyback", "Column": "Actual buyback", "Project": "World Mobile",
             "Status": "N/A BY DESIGN", "Reason / route / error": "N/A BY DESIGN — accepted limit: x: ACCEPTED LIMIT "
             "(Jake, 2026-09-28): World Mobile has never published its treasury address, so …"}]
    assert ca.apply_checked(rows) == 1
    assert rows[0]["Status"] == "MISSING" and rows[0]["Reason / route / error"].startswith("MISSING [E — NO SOURCE]")
    assert "checked: worldmobile.io/blog (2026-10-20)" in rows[0]["Reason / route / error"]


def test_n_ccip_testtr_is_a_declared_zero_value_token_with_its_contract_as_evidence(tmp_path, at, monkeypatch):
    at("2026-10-20")
    grp = "g-testtr"
    monkeypatch.setattr(t8, "DIRECTORY", t8.DIRECTORY + [(t8.ETH, "0x00000000000000000000000000000000000000f1", 18,
                                                           "TESTTR", grp)])
    ms = _day(token="0x00000000000000000000000000000000000000f1", src=t8.ETH, dst=t8.ARB, n=10, priced=5)
    f, projects = t8.make(tmp_path, t8.FakeCcip(ms), t8.FakeLlama(), history_days=1,
                          zero_value_groups={grp: {"evidence": "drip() mints free"}})
    out = FetchOutput()
    f.run(projects, None, out)
    value, src = t8.rows(out, cv.VALUE)["2026-10-19"]
    assert value == pytest.approx(5.0) and "[5 transfer(s) of a declared zero-value test token valued 0]" in src
    assert ":PARTIAL" not in src
    assert "dbfad97a-19ea-59e6-a1cb-0694887755b0" in config.PROJECT_BY_NAME["Chainlink"]["ccip_value"]["zero_value_groups"]
