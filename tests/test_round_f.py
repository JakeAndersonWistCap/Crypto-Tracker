"""
tests/test_round_f.py — Round F (Jake 2026-10-14), F1-F6, each on real data where the fix touches stored figures: the
dumps (fixtures/real_*.json) and the rows of Jake's 14 Oct workbook Review Queue transcribed below.
"""
from __future__ import annotations

import builtins
import contextlib
import copy
import io
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402

FX = ROOT / "fixtures"


# ---------------------------------------------------------------------------------------------------------- F1 aero v3
def test_f1_aero_v3_calls_match_the_transcribed_abi_and_keccak():
    """Every call and event the reader uses is in fixtures/aero_v3_abi.json (declaration text verbatim from the pinned
    Dromos source), with the same signature, return types and file:line; the selectors are keccak of the signatures."""
    from eth_utils import keccak

    from fetch import aero_v3 as a
    abi = json.loads((FX / "aero_v3_abi.json").read_text(encoding="utf-8"))
    assert abi["repo"] == a.REPO
    for name, (sig, rets, where) in a.CALLS.items():
        f = abi["functions"][name]
        assert f["signature"] == sig and tuple(f["returns"]) == rets
        assert where.startswith(f"{f['file']}:{f['line']}")
        assert a.selector(name) == "0x" + keccak(text=sig).hex()[:8]
    for name, (sig, where) in a.EVENTS.items():
        e = abi["events"][name]
        assert e["signature"] == sig and where == f"{e['file']}:{e['line']}"
    # the values checked by hand on 2026-10-14
    assert {n: a.selector(n) for n in ("emissionsMinted", "emissionRate", "teamRate", "supply", "totalPoint",
                                       "incentive")} == {
        "emissionsMinted": "0xbd121d28", "emissionRate": "0x96afc450", "teamRate": "0x78ef7f02",
        "supply": "0x047fc9aa", "totalPoint": "0xdbc4feb6", "incentive": "0xb97e8b9e"}
    assert abi["functions"]["emissionsMinted"]["text"] == "uint256 public emissionsMinted;"


def test_f1_decode_words_signed_and_address():
    from fetch.aero_v3 import decode_words
    neg = (1 << 256) - 5                                   # int128 -5 sign-extended to a word
    word = lambda v: f"{v:064x}"  # noqa: E731
    data = "0x" + word(neg) + word(7) + word(1_760_000_000) + word(10 ** 18) + word(int("ab" * 20, 16))
    assert decode_words(data, ("int128", "int128", "uint48", "uint128", "address")) == (
        -5, 7, 1_760_000_000, 10 ** 18, "0x" + "ab" * 20)


def test_f1_walk_point_is_the_contracts_walk():
    """AllocationLogicLibrary._walkPoint (L1295-1316) by hand: decay to each Unix-week boundary, drop the slope that
    expires there, then decay to `now`; weight = permanent + max(0, bias) (L1447)."""
    from fetch.aero_v3 import WEEK, walk_point
    ts = 10 * WEEK + 100
    now = 12 * WEEK + 50
    changes = {11 * WEEK: 3, 12 * WEEK: 1}
    # hand: to 11W: 1000 - 5*(WEEK-100); slope 5-3=2; to 12W: - 2*WEEK; slope 1; to now: - 1*50
    bias = 10 ** 9
    exp = bias - 5 * (WEEK - 100) - 2 * WEEK - 50
    assert walk_point(bias, 5, ts, 777, now, lambda t: changes.get(t, 0)) == 777 + exp
    assert walk_point(5, 5, ts, 777, now, lambda t: 0) == 777                    # bias floors at zero
    assert walk_point(bias, 5, now, 1, now, lambda t: 0) == 1 + bias             # nothing to walk


def test_f1_epoch_rewards_prorates_streams_and_sums_fees():
    from fetch.aero_v3 import WAD, epoch_rewards
    tok_a, tok_b = "0x" + "aa" * 20, "0x" + "bb" * 20
    fee = lambda tok, v: {"topics": ["0x0", "0x" + "11" * 32, "0x" + tok[2:].rjust(64, "0")],  # noqa: E731
                          "data": hex(v)}
    start, end = 1000, 1000 + 604800
    progs = [(tok_a.upper().replace("0X", "0x"), 0, 2 * WAD, start - 100, start + 300, "0x0"),   # 300 s inside
             (tok_b, 0, WAD, start + 10, end + 999, "0x0"),                                      # end - start - 10
             (tok_b, 0, WAD, end, end + 5, "0x0")]                                               # outside
    got = epoch_rewards([fee(tok_a, 40), fee(tok_a, 2), fee(tok_b, 1)], progs, start, end)
    assert got == {tok_a: (42, 600), tok_b: (1, 604790)}


def _aero_state():
    # high-entropy test addresses (Round G 2.10 refuses repeated patterns like 0x0101… as placeholders)
    return {"launched": True, "addresses": {"minter": "0x5f1c9a3e7b2d48c06e9f31a7b5d2c8e4f0a6b913",
                                            "voting_escrow": "0x8e2d7c4b1a9f36e05d8c27b4a1f9e6d3c0b5a872",
                                            "token": "0x3a7f9c1e5b8d24f06a3c9e7b1d5f82a4c6e0b391"},
            "source_url": "https://github.com/dromos-labs", "verified": "2026-10-22"}


def test_f1_aero_v3_run_writes_the_replaced_metrics_from_a_stub_chain(monkeypatch):
    """The whole state path on a stub RPC answering each selector: the metrics config says v3 replaces are written,
    the issuance flow is d(emissionsMinted), the team line is that x teamRate / 1e6 (Minter.sol L162), and with no
    voting_rewards_factory in the file the epoch is a NAMED gap, not a silent zero."""
    from fetch import aero_v3 as a
    from fetch.base import FetchOutput
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-10-23"))
    voter = "0x" + "04" * 20
    now = 2934 * a.WEEK + 3600
    w = lambda v: "0x" + f"{v:064x}"  # noqa: E731
    ans = {a.selector("emissionsMinted"): w(1_500 * a.WAD), a.selector("teamRate"): w(50_000),
           a.selector("emissionRate"): w(10 ** 15), a.selector("supply"): w(900 * a.WAD),
           a.selector("totalVotingPower"): w(800 * a.WAD), a.selector("permanentStakeBalance"): w(300 * a.WAD),
           a.selector("balanceOf"): w(905 * a.WAD), a.selector("VOTER"): w(int(voter, 16)),
           a.selector("totalPoint"): "0x" + f"{500 * a.WAD:064x}" + f"{0:064x}" + f"{now:064x}" + f"{300 * a.WAD:064x}"}
    calls = []

    def rpc(method, params):
        if method == "eth_getCode":
            return "0x6080"                                 # code at every address (Round G 2.10 checks it first)
        assert method == "eth_call"
        calls.append(params[0]["data"][:10])
        return ans[params[0]["data"][:10]]
    out = FetchOutput()
    rdr = a.AeroV3(prior_values={("Aerodrome", "aero_v3_emissions_minted_cumulative"): 1_000.0},
                   prior_dates={("Aerodrome", "aero_v3_emissions_minted_cumulative"): pd.Timestamp("2026-10-22")},
                   rpc=rpc, state=_aero_state(), now=now)
    rdr.run([config.PROJECT_BY_NAME["Aerodrome"]], 30, out)
    f = out.frame()
    got = dict(zip(f["metric"], f["value"]))
    assert got["gross_issuance_tokens"] == pytest.approx(500.0)
    assert got["issuer_mint_tokens"] == pytest.approx(25.0)                # 500 x 50,000 / 1,000,000
    assert got["emission_rate_current"] == pytest.approx(10 ** 15 * a.WEEK / a.WAD)
    assert (got["ve_locked_supply_tokens"], got["ve_voting_power_tokens"], got["permanent_locked_tokens"],
            got["locked_tokens"]) == (900.0, 800.0, 300.0, 905.0)
    assert got["voter_total_weight_tokens"] == pytest.approx(800.0)        # 300 permanent + 500 bias, ts == now
    assert set(got) - {"aero_v3_emissions_minted_cumulative"} <= set(config.AERODROME_MIGRATION["v3_replaces"])
    assert all(str(s).startswith("aero_v3") for s in f["source"])
    assert [g["metric"] for g in out.gaps] == ["voter_rewards_onchain_usd"]
    assert "voting_rewards_factory" in out.gaps[0]["reason"]


def test_f1_aero_v3_waits_for_the_cutover_and_a_complete_switch_file(monkeypatch):
    from fetch import aero_v3 as a
    from fetch.base import FetchOutput

    def rpc(*_):
        raise AssertionError("no chain read before the switch is complete")
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-10-21"))
    out = FetchOutput()
    a.AeroV3(rpc=rpc, state=_aero_state()).run([], 30, out)
    assert out.frame().empty and not out.log
    monkeypatch.setattr(a, "today", lambda: pd.Timestamp("2026-10-23"))
    st = _aero_state()
    st["addresses"]["minter"] = "0x…"
    a.AeroV3(rpc=rpc, state=st).run([], 30, out)
    assert out.frame().empty and "addresses.minter" in out.log[-1].message


def test_f1_cutover_drops_legacy_rows_of_replaced_metrics_but_keeps_the_rebase():
    from fetch.base import FetchOutput
    from fetch.validate import check_migration_cutover
    out = FetchOutput()
    rows = [("2026-10-23", "gross_issuance_tokens", 1.0, "chain:base:minter:delta"),
            ("2026-10-23", "gross_issuance_tokens", 2.0, "aero_v3:minter.emissionsMinted:delta"),
            ("2026-10-23", "emissions_tokens", 3.0, "chain:base:legacy_rebase"),
            ("2026-10-21", "locked_tokens", 4.0, "chain:base:ve")]
    out.frames.append(pd.DataFrame([{"date": d, "project": "Aerodrome", "metric": m, "value": v, "source": s,
                                     "tier": 2} for d, m, v, s in rows]))
    check_migration_cutover(out, today=pd.Timestamp("2026-10-23"), state=_aero_state())
    kept = sorted(zip(out.frame()["metric"], out.frame()["source"]))
    assert kept == [("emissions_tokens", "chain:base:legacy_rebase"),
                    ("gross_issuance_tokens", "aero_v3:minter.emissionsMinted:delta"),
                    ("locked_tokens", "chain:base:ve")]
    assert "AERO CUTOVER: 1 legacy row" in out.log[-1].message


# ------------------------------------------------------------------------------------------------- F2 explorer probe
def test_f2_probe_writes_served_chains_and_never_prints_the_key(monkeypatch, tmp_path):
    import check_offline_items as coi
    secret = "SECRETKEY1234567890"
    monkeypatch.setenv("ETHERSCAN_API_KEY", secret)
    monkeypatch.setattr(coi, "__file__", str(tmp_path / "check_offline_items.py"))

    class R:
        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    def get(url, params, timeout):
        assert url == "https://api.etherscan.io/v2/api" and params["offset"] == 1
        cid = params["chainid"]
        if cid == 8453:
            return R({"status": "1", "result": [{"blockNumber": "0x10", "transactionHash": "0x" + "ab" * 32}]})
        if cid == 999:
            raise RuntimeError(f"connection refused for https://api.etherscan.io/v2/api?apikey={secret}")
        return R({"status": "0", "message": "NOTOK", "result": f"Free API access is not supported for this chain {secret}"})
    monkeypatch.setattr(coi.requests, "get", get)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        coi.etherscan_v2_routes()
    st = json.loads((tmp_path / "explorer_routes.json").read_text())
    assert st["served"] == [8453] and set(st["refused"]) == {"56", "5000", "146", "80094", "999", "9745", "10"}  # 10: OP, G2 addendum A7
    assert secret not in buf.getvalue() and secret not in json.dumps(st)
    assert coi.etherscan_v2_routes in coi.CHECKS


def test_f2_wire_probed_scans_opens_only_the_served_chains(monkeypatch):
    saved = {k: copy.deepcopy(config.PROJECT_BY_NAME[k]) for k in ("Pendle", "Fluid")}
    routes = copy.deepcopy(config.EXPLORER_LOG_ROUTES)
    # wire_probed_scans also adds the Base scan's metric to Fluid's outflow (b): restored with everything else (left in
    # place, every later build in the process read a probed one-week leg there — D7-2's same-span test refused it)
    monkeypatch.setitem(config.OUTFLOW_CATEGORIES["Fluid"], "b", config.OUTFLOW_CATEGORIES["Fluid"]["b"])
    try:
        assert config.wire_probed_scans({"served": []}) == []
        wired = config.wire_probed_scans({"served": [8453, 999], "probed_on": "2026-10-15"})
        chains = {sp["key"]: sp for p in ("Pendle", "Fluid") for sp in config.PROJECT_BY_NAME[p].get("log_scans") or ()
                  if sp["key"] in wired}
        assert {sp["chain"] for sp in chains.values()} == {"base", "hyperevm"}
        assert {sp["metric"] for sp in chains.values()} == {"emissions_tokens_gauge_base",
                                                           "emissions_tokens_gauge_hyperevm", "emissions_tokens_base"}
        assert all(sp["store"] and sp["direction"] == "out" for sp in chains.values())
        assert "etherscan" in config.EXPLORER_LOG_ROUTES[8453] and "etherscan" not in config.EXPLORER_LOG_ROUTES.get(56, [])
        em = config.PROJECT_BY_NAME["Pendle"]["emissions_from_metric"]
        assert "emissions_tokens_gauge_base" in em and "emissions_tokens_gauge_bsc" not in em
        assert config.wire_probed_scans({"served": [8453, 999]}) == []                     # idempotent
    finally:
        config.PROJECT_BY_NAME["Pendle"].clear()
        config.PROJECT_BY_NAME["Pendle"].update(saved["Pendle"])
        config.PROJECT_BY_NAME["Fluid"].clear()
        config.PROJECT_BY_NAME["Fluid"].update(saved["Fluid"])
        config.EXPLORER_LOG_ROUTES.clear()
        config.EXPLORER_LOG_ROUTES.update(routes)


# ------------------------------------------------------------------------------------------------ F3 Aethir supply
def test_f3_section_cs_moves_aethirs_coingecko_totals_on_the_real_dump(monkeypatch, tmp_path):
    import real_dump
    import run_sql
    db = tmp_path / "metrics.db"
    real_dump.to_store(real_dump.load("Aethir"), db)
    conn = sqlite3.connect(db)
    q = lambda sql: conn.execute(sql).fetchall()  # noqa: E731
    before = q("SELECT date, value FROM metrics WHERE project = 'Aethir' AND metric = 'total_supply' ORDER BY date")
    assert len(before) == 25
    sec = run_sql.parse_sections(run_sql.SQL_FILE.read_text(encoding="utf-8"))["CS"]
    monkeypatch.setattr(builtins, "input", lambda *a: "DELETE CS")
    with contextlib.redirect_stdout(io.StringIO()):
        assert run_sql.run_delete(conn, sec["text"], "CS", db, sec["authored"]) == 0
        assert run_sql.run_delete(conn, sec["text"], "CS", db, sec["authored"]) == 1
    assert q("SELECT COUNT(*) FROM metrics WHERE project = 'Aethir' AND metric = 'total_supply'") == [(0,)]
    assert q("SELECT date, value FROM metrics WHERE project = 'Aethir' AND metric = 'total_supply_coingecko' "
             "ORDER BY date") == before                                                   # values untouched
    assert "Aethir" in config.TOTAL_SUPPLY_ONCHAIN_FIRST


# -------------------------------------------------------------------------------------------- F4 HyperEVM burn
def test_f4_zero_address_balance_is_stored_with_its_flow(monkeypatch):
    from fetch.base import FetchOutput
    from fetch.hypercore import HyperCoreInfo
    read = next(r for r in config.PROJECT_BY_NAME["Hyperliquid"]["node_api"]["extra_reads"]
                if r.get("shape") == "evm_native_balance")
    assert read["address"] == "0x" + "0" * 40 and read["metric"] == "hyperevm_zero_address_balance"
    assert "hyperevm_zero_address_balance" not in str(config.burn_total("Hyperliquid") or {})     # PROPOSED only
    rdr = HyperCoreInfo(prior_delta={("Hyperliquid", "hyperevm_zero_address_balance"): 308_626.43},
                        prior_dates={("Hyperliquid", "hyperevm_zero_address_balance"): pd.Timestamp("2026-10-13")})
    seen = []

    def post(url, json_body):
        seen.append(url)
        if "hyperliquid.xyz" in url:
            raise RuntimeError("503")
        assert json_body["method"] == "eth_getBalance" and json_body["params"] == [read["address"], "latest"]
        return {"result": hex(308_686_908_516 * 10 ** 12)}
    monkeypatch.setattr(rdr.http, "post", post)
    out = FetchOutput()
    rdr._extra_read(config.PROJECT_BY_NAME["Hyperliquid"], {}, read, out, pd.Timestamp("2026-10-14"))
    got = dict(zip(out.frame()["metric"], out.frame()["value"]))
    assert got["hyperevm_zero_address_balance"] == pytest.approx(308_686.908516)
    assert got["hyperevm_priority_burn_tokens"] == pytest.approx(60.478516)
    assert len(seen) == 2


# -------------------------------------------------------------------------------------------- F6 triage
# Jake's 14 Oct workbook, Review Queue rows 8-30 (the 23 change_threshold items): project, metric, date, value, prior.
QUEUE_CHANGE = [
    ("Chainlink", "customer_revenue_ccip_legacy_usd", "2026-10-13", 0, 0.5044864175102397),
    ("Chainlink", "customer_revenue_vrf_usd", "2026-10-13", 535.0680074141428, 2020.340911176731),
    ("Chainlink", "emissions_claimed_tokens", "2026-10-13", 47086.76903963664, 1138.183058267232),
    ("Ether.fi", "programmatic_buyback_tokens", "2026-10-13", 68716.29571659118, 28089.78138480193),
    ("Ether.fi", "sethfi_reward_tokens_reconciled", "2026-10-13", 927.9899655963458, 394.4816623273945),
    ("Ethereum", "settlement_volume_usd", "2025-08-25", 6272972356.570877, 15442232930.16703),
    ("Ethereum", "volume_usd", "2026-10-13", 13499532438.02736, 8566468363.628672),
    ("Fluid", "emissions_tokens", "2026-10-13", 86.89019053236355, 1661.307269183268),
    ("Fluid", "emissions_tokens_arbitrum", "2026-10-13", 6.282175975388972, 45.51494368872451),
    ("Hyperliquid", "core_burn_tokens", "2026-10-14", 1206.193181633949, 611.7788618803024),
    ("Hyperliquid", "perps_volume_usd", "2026-10-13", 9422912186.416128, 6267549323.735209),
    ("Hyperliquid", "volume_usd", "2026-10-13", 787934671.6938454, 446619328.0547642),
    ("Morpho", "emissions_tokens", "2026-10-13", 226502.923232563, 624.0866728065399),
    ("Morpho", "liquidation_bonus_usd_day_morpho_api", "2026-10-08", 388090.4172550117, 50969.42961204352),
    ("Morpho", "liquidation_bonus_usd_day_morpho_api", "2026-10-09", 40.68566281478895, 50969.42961204352),
    ("Morpho", "liquidation_bonus_usd_day_morpho_api", "2026-10-10", 0.01775057597890415, 50969.42961204352),
    ("Morpho", "liquidation_bonus_usd_day_morpho_api", "2026-10-11", 0.003770186192688891, 50969.42961204352),
    ("Morpho", "liquidation_bonus_usd_day_morpho_api", "2026-10-12", 884.922044907471, 50969.42961204352),
    ("Morpho", "volume_usd", "2026-10-13", 25336970.7848251, 15304920.62853628),
    ("Near", "p2p_transfer_volume_usd", "2026-10-13", 684730080.2779605, 128091019.4457032),
    ("Near", "tx_count_kaiching", "2026-10-13", 0, 68052),
    ("Plume", "emissions_claimed_tokens", "2025-08-08", 41.19187859223577, 1057.891005864306),
    ("Plume", "p2p_transfer_volume_usd", "2026-10-13", 6419967.046660485, 1277355.443850498),
]
F6_LUMPY = {("Chainlink", "emissions_claimed_tokens"), ("Chainlink", "customer_revenue_vrf_usd"),
            ("Ether.fi", "programmatic_buyback_tokens"), ("Ether.fi", "sethfi_reward_tokens_reconciled"),
            ("Fluid", "emissions_tokens"), ("Fluid", "emissions_tokens_arbitrum"), ("Hyperliquid", "core_burn_tokens"),
            ("Morpho", "emissions_tokens"), ("Morpho", "liquidation_bonus_usd_day_morpho_api"),
            ("Plume", "emissions_claimed_tokens")}


_LUMPY = dict(config.LUMPY_FLOWS)


def _flags(rows, monkeypatch, lumpy):
    from fetch.base import FetchOutput
    from fetch.validate import validate_frame
    table = dict(_LUMPY)
    if not lumpy:
        for k in F6_LUMPY:
            table.pop(k)
    monkeypatch.setattr(config, "LUMPY_FLOWS", table)
    n = 0
    for p, m, d, v, prior in rows:
        out = FetchOutput()
        validate_frame(pd.DataFrame([{"date": d, "project": p, "metric": m, "value": float(v), "source": "replay",
                                      "tier": 2}]), {(p, m): float(prior)}, out)
        n += sum(1 for r in out.review if r.get("reason") == "change_threshold")
    return n


def test_f6_the_queue_change_items_by_cause(monkeypatch):
    """Replaying the 23 rows: the 14 rows of the ten F6 LUMPY series (claims, per-request billing, discrete fills,
    read spacing, event-driven liquidations) are no longer compared day against day; the 6 market / UNVALIDATED rows
    still flag (they are reasons, not faults: CCIP legacy on hold, market volume, the Artemis CSV row, NEAR/Plume p2p
    UNVALIDATED, kaiching)."""
    lumpy_rows = [r for r in QUEUE_CHANGE if (r[0], r[1]) in F6_LUMPY]
    other = [r for r in QUEUE_CHANGE if (r[0], r[1]) not in F6_LUMPY]
    assert len(QUEUE_CHANGE) == 23 and len(lumpy_rows) == 14 and len(other) == 9
    assert _flags(lumpy_rows, monkeypatch, lumpy=False) == 14                 # before: every one flagged
    assert _flags(lumpy_rows, monkeypatch, lumpy=True) == 0                   # after: window-checked, not day-checked
    assert all(config.lumpy_flow(p, m)["recorded_on"] == "2026-10-14" for p, m in F6_LUMPY)
    assert _flags(other, monkeypatch, lumpy=True) == 9                        # untouched: each has its reason


def test_f6_lumpy_series_in_the_real_dumps_are_window_checked(monkeypatch):
    """On the dumps' own 30 days, a lumpy F6 series is compared trailing week vs prior week (the basis says so) — still
    checked, never exempted."""
    import real_dump
    from fetch.base import FetchOutput
    from fetch.validate import validate_frame
    monkeypatch.setattr(pd.Timestamp, "now", classmethod(lambda cls, tz=None: pd.Timestamp("2026-10-14", tz=tz)))
    checked = 0
    for proj in ("Chainlink", "Fluid", "Morpho"):
        rows = pd.DataFrame(real_dump.load(proj)["metrics"]).assign(project=proj)
        for p, m in sorted(k for k in F6_LUMPY if k[0] == proj):
            g = rows[(rows["project"] == p) & (rows["metric"] == m)].copy()
            g = g[(g["date"] >= "2026-09-29") & (g["date"] <= "2026-10-12")]
            if len(g) < 14:
                continue
            out = FetchOutput()
            validate_frame(g[["date", "project", "metric", "value", "source", "tier"]].assign(source="replay"),
                           {}, out)
            for r in out.review:
                if r.get("reason") == "change_threshold":
                    assert "TRAILING 7-DAY SUM" in r.get("basis", "")
            checked += 1
    assert checked >= 3


def test_f6_near_gross_burn_is_current_not_a_node_api_gap_on_the_real_dump(tmp_path):
    """Jake's 14 Oct Gap Report P3: "node API read (near_validators) is configured but returned nothing this run" for
    NEAR gross_burn_tokens — a read that never sources it. The history step derives it (fees x burned share / price)
    and wrote 0 rows because every derivable day was already held; that is CURRENT, not a gap."""
    import real_dump
    import store as sm
    from fetch import gaps
    from fetch.base import FetchOutput
    from fetch.history_derive import derive_from_history
    db = tmp_path / "metrics.db"
    real_dump.to_store(real_dump.load("Near"), db)
    near = config.PROJECT_BY_NAME["Near"]
    out = FetchOutput()
    assert derive_from_history(out, [near], sm.Store(str(db)).load_long()) == {}
    assert ("Near", "gross_burn_tokens") in out.current
    line = next(e.message for e in out.log if e.message.startswith("gross_burn_tokens = protocol rule"))
    assert "0 row(s) written" in line and "396 held before" in line
    before = [g for g in gaps.detect([near], out.frame(), set(), {}, []) if g["metric"] == "gross_burn_tokens"]
    assert before and "node API read (near_validators)" in before[0]["reason"]
    after = [g for g in gaps.detect([near], out.frame(), set(out.current), {}, []) if g["metric"] == "gross_burn_tokens"]
    assert after == []


def test_f6_anchor_confirmations_on_file():
    import yaml
    src = yaml.safe_load((ROOT / "sources.yaml").read_text(encoding="utf-8"))
    text = json.dumps(src, default=str)
    assert "29,388,031.05" in text or "29388031.05" in text
    assert "700,580,364.72" in text or "700580364.72" in text
    assert "NOT CONFIRMED" in text and "168,657,502.01" in text


def test_f2_f4_unopened_scans_and_the_evm_burn_gap_with_their_own_reason(monkeypatch):
    """A probe-gated scan not yet opened names the probe and what it said; the HyperEVM burn names its read — never
    "no source configured"."""
    from fetch import gaps
    monkeypatch.setattr(config, "explorer_probe_state",
                        lambda: {"probed_on": "2026-10-15", "served": [], "refused": {"56": "status '0' message 'NOTOK'"}})
    rows = {(g["project"], g["metric"]): g for g in gaps.detect(
        [config.PROJECT_BY_NAME["Pendle"], config.PROJECT_BY_NAME["Hyperliquid"]], None, set(), {}, [])}
    bsc = rows[("Pendle", "emissions_tokens_gauge_bsc")]
    assert "chain 56" in bsc["reason"] and "NOTOK" in bsc["reason"] and "etherscan_v2_routes" in bsc["suggestion"]
    evm = rows[("Hyperliquid", "hyperevm_priority_burn_tokens")]
    assert "eth_getBalance" in evm["reason"] and "earlier-dated reading" in evm["reason"]
    assert config.AERODROME_MIGRATION_WATCH["built"] is None          # the watch stays on until v3 rows arrive
