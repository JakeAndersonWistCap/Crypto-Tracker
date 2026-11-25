"""R14 (Jake 2026-11-19) — Ether.fi on the rule: supply, float, insiders, the financial-theatre flag (SPEC "Round 14").

No network: stores are built in memory, explorers are stand-ins. What is tested is behaviour and arithmetic — circulating
is the total supply; the unvested team leg is a function of the day that falls monthly and ends 2027-03-18; the off-float
legs (issuer-side stock, the MODELLED team leg, the five-chain stake) come off the float and not off circulating; the
stake is all chains or no row, with the old point before the handover; the insider share of the stake forms with the
INFERRED wallets tagged; the theatre flag's three states and its two readings; the scan counts only the tagged
destinations. Figures are the reads of 2026-11-19 (docs/r13_etherfi_pack.md and this round's)."""
from __future__ import annotations

import pandas as pd
import pytest

import build_workbook as bw
import config
import trajectory

EF = "Ether.fi"
P = config.PROJECT_BY_NAME[EF]
D = pd.Timestamp("2026-11-19")


def _frame(metric, rows, source="chain:ethereum:x"):
    return pd.DataFrame({"date": [pd.Timestamp(d) for d, _ in rows], "project": EF, "metric": metric,
                         "value": [float(v) for _, v in rows], "tier": 2, "source": source})


@pytest.fixture
def scoped(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", [P])
    return P


# =========================================================================== R14.1 circulating = total
def test_1_circulating_is_the_total_supply(scoped):
    from fetch import circulating as circ
    assert config.rule_in_legs(EF) == ("treasury_holding_tokens", "noncirculating_holding_tokens")
    days = ["2026-10-12", "2026-10-13"]
    h = pd.concat([_frame("total_supply", [(d, 998_535_999) for d in days]),
                   _frame("treasury_holding_tokens", [(d, 0.0) for d in days]),
                   _frame("noncirculating_holding_tokens", [(d, 187_838_959.0) for d in days])])
    s = circ.series(h, EF)
    # R22 (Jake 2026-11-25, item 1a): the unvested team leg (MODELLED, 6/36 of 214.7M on the dump) is OUT — the run-time
    # series forms it from the schedule (was R14.1: [998_535_999.0, 998_535_999.0], nothing subtracted)
    assert list(s.values) == pytest.approx([998_535_999.0 - 35_783_333.33] * 2, abs=0.01)
    # the like-for-like figure (rule=False) still leaves the legs out — what a reference that leaves them out is set against
    assert circ.series(h, EF, rule=False).iloc[-1] == pytest.approx(998_535_999 - 187_838_959.0 - 35_783_333.33,
                                                                     abs=0.01)
    spec = config.RULE_IN[EF]
    assert spec["circulating"] == (998_535_999, 962_752_666)           # R22 (was R14.1: (810_696_040, 998_535_999))
    assert spec["reference_leaves_out"] is False                          # CoinGecko counts the total (2026-11-19)
    ref, legs = spec["references"]["coinmarketcap"]
    assert ref + sum(legs.values()) == spec["circulating"][1]             # the CMC gap named to the token
    ref, legs = spec["references"]["coingecko"]                          # R22 (was (998_535_999, {}))
    assert ref == 998_535_999 and ref + sum(legs.values()) == spec["circulating"][1]
    assert "declared handover" in config.circulating_rule_note(EF).lower()


def test_1_the_burn_is_out_of_total_and_circulating():
    # 1,000,000,000 minted once − 1,464,001 burned = the on-chain totalSupply; R22: less the unvested team leg on the
    # dump (35,783,333) = circulating (was R14.1: the totalSupply itself)
    assert 1_000_000_000 - 1_464_001 - 35_783_333 == config.RULE_IN[EF]["circulating"][1]
    assert config.classify_position(burned=True) == {"circulating": False, "in_total": False, "off_float": True,
                                                     "why": "burned"}


# =========================================================================== R14.1b the MODELLED team leg
@pytest.mark.parametrize("day,months_left", [
    ("2025-03-17", 36), ("2025-03-18", 24), ("2025-04-17", 24), ("2025-04-18", 23), ("2026-10-13", 6),
    ("2026-10-18", 5), ("2026-11-17", 5), ("2026-11-18", 4), ("2026-11-19", 4), ("2027-02-18", 1),
    ("2027-03-17", 1), ("2027-03-18", 0), ("2027-12-31", 0)])
def test_1b_the_team_leg_falls_monthly_and_ends_2027_03_18(day, months_left):
    assert config.etherfi_team_unvested_on(day) == pytest.approx(214_700_000 * months_left / 36)


def test_1b_the_team_leg_is_derived_per_day_and_labelled_modelled(scoped):
    assert config.etherfi_team_unvested_on("2026-11-19") == pytest.approx(23_855_555.6, abs=1)   # decision 2: 4/36
    days = pd.date_range("2026-10-01", "2026-12-31")
    groups = {(EF, "total_supply"): _frame("total_supply", [(d, 998_535_999) for d in days])}
    bw._schedule_lock_leg_views(groups)
    g = groups[(EF, "etherfi_team_unvested_modelled_tokens")]
    v = bw._daily(g)
    assert len(v) == len(days) and v.is_monotonic_decreasing
    assert v["2026-11-17"] - v["2026-11-18"] == pytest.approx(214_700_000 / 36)   # one monthly step, on the 18th
    assert v["2026-11-18"] == v["2026-12-17"]                                       # flat between steps
    assert all(config.is_modelled(s) for s in g["source"])                          # MODELLED wherever it shows
    # the float that subtracts it follows the leg (the MODELLED label propagates through the off-float sum)
    assert trajectory._inputs(EF, "rule_in_off_float_tokens", groups) == ("etherfi_team_unvested_modelled_tokens",)
    # a project with no modelled leg is unchanged
    assert trajectory._inputs("Uniswap", "rule_in_off_float_tokens", {}) == ()


# =========================================================================== R14.1 off the float, not off circulating
def test_1_the_off_float_legs_leave_the_float_only(scoped):
    spec = config.RULE_IN[EF]
    # R22: the modelled team leg is OUT of circulating (subtracted), so it no longer leaves the float on its own (was
    # R14.1: off_float = the two legs + the modelled leg)
    assert spec["off_float"] == ("treasury_holding_tokens", "noncirculating_holding_tokens")
    assert "etherfi_team_unvested_modelled_tokens" in config.CIRCULATING_ONCHAIN[EF]["subtract"]
    days = ["2026-11-18", "2026-11-19"]
    groups = {(EF, "total_supply"): _frame("total_supply", [(d, 998_535_999) for d in days]),
              (EF, "treasury_holding_tokens"): _frame("treasury_holding_tokens", [(d, 0.0) for d in days]),
              (EF, "noncirculating_holding_tokens"): _frame("noncirculating_holding_tokens",
                                                            [(d, 182_710_017.0) for d in days])}
    bw._schedule_lock_leg_views(groups)
    bw._rule_in_views(groups)
    off = bw._daily(groups[(EF, "rule_in_off_float_tokens")])
    assert off["2026-11-19"] == pytest.approx(182_710_017, abs=1)      # R22 (was 182_710_017 + 23_855_555.6)
    # the pack's float, unchanged: (total − the modelled team leg) − stake − issuer = 653,939,368 (65.5% of total)
    assert 998_535_999 - 23_855_555.6 - 138_031_058 - off["2026-11-19"] == pytest.approx(653_939_368, abs=1)
    # decision 3: the vested custody holdings are in the float — the insider share beside it, never subtracted
    assert "etherfi_insider_custody_tokens" not in spec["off_float"]
    assert "etherfi_insider_custody_tokens" in config.INSIDER_INSIDE_CIRCULATING[EF]["held"]


def test_1a_the_allocation_table_is_the_docs():
    t = P["allocation_table"]
    shares = {b["name"]: b["share"] for b in t["buckets"]}
    assert shares == {"Investors": 0.3374, "Core contributors": 0.2147, "Treasury": 0.2162, "User airdrops": 0.1927,
                      "Partnerships & liquidity (incl. 1% Protocol Guild)": 0.039}
    assert sum(shares.values()) == pytest.approx(1.0)
    assert "ethfi-allocations" in t["source"] and "DropsTab" in t["was"]["source"]


# =========================================================================== R14.4 the stake: five chains x rate
def _stake_groups(days_all, days_old):
    g = {(EF, "locked_tokens"): _frame("locked_tokens", [(d, 86_035_054.29) for d in days_old + days_all],
                                       "chain:ethereum:sethfi_shares:PARTIAL")}
    for ch, v in config._SETHFI_L2:
        g[(EF, f"sethfi_shares_{ch}")] = _frame(f"sethfi_shares_{ch}", [(d, v) for d in days_all])
    g[(EF, "sethfi_accountant_rate")] = _frame("sethfi_accountant_rate", [(d, 1.2067761500772427) for d in days_all])
    return g


def test_4_the_stake_is_five_chains_times_the_rate_after_the_handover(scoped):
    g = _stake_groups(["2026-11-19", "2026-11-20"], ["2026-11-17", "2026-11-18"])
    bw._multichain_stake_views(g)
    s = g[(EF, "sethfi_staked_all_chains_tokens")].sort_values("date")
    v = dict(zip(s["date"].dt.strftime("%Y-%m-%d"), s["value"]))
    assert v["2026-11-18"] == pytest.approx(86_035_054.29)                       # the old point before the seam
    assert v["2026-11-19"] == pytest.approx(114_884_867.84 * 1.2067761500772427, rel=1e-9)
    assert v["2026-11-19"] == pytest.approx(config.MULTICHAIN_STAKE[EF]["read"]["staked"], abs=1)
    assert list(s["source"]) == [config.MULTICHAIN_STAKE[EF]["points"][0]] * 2 + [config.MULTICHAIN_STAKE[EF]["points"][1]] * 2
    h = P["series_handover"]["sethfi_staked_all_chains_tokens"]
    assert h["ordered_points"] == config.MULTICHAIN_STAKE[EF]["points"] and h["composition_change"].startswith("WIDENS")
    assert config.lock_display_metric(EF) == "sethfi_staked_all_chains_tokens"   # the float and lock rate read it


def test_4_a_day_missing_a_chain_is_no_row_never_zero(scoped):
    g = _stake_groups(["2026-11-19", "2026-11-20", "2026-11-21"], [])
    sc = g[(EF, "sethfi_shares_scroll")]
    g[(EF, "sethfi_shares_scroll")] = sc[sc["date"] != pd.Timestamp("2026-11-20")]
    bw._multichain_stake_views(g)
    s = g[(EF, "sethfi_staked_all_chains_tokens")]
    assert set(s["date"].dt.strftime("%Y-%m-%d")) == {"2026-11-19", "2026-11-21"}    # 11-20: no row, no fallback
    # no L2 read stored at all: the old point only (the fixture's case)
    g2 = _stake_groups([], ["2026-10-12", "2026-10-13"])
    for ch, _v in config._SETHFI_L2:
        g2.pop((EF, f"sethfi_shares_{ch}"))
    bw._multichain_stake_views(g2)
    s2 = g2[(EF, "sethfi_staked_all_chains_tokens")]
    assert set(s2["source"]) == {config.MULTICHAIN_STAKE[EF]["points"][0]} and len(s2) == 2


def test_4_the_accountant_is_read_as_the_vaults_rate():
    c = P["contracts"]["sethfi_accountant_rate"]
    assert c["address"].lower() == "0x05a1552c5e18f5a0bb9571b5f2d6a4765ebda32b" and c["call"] == "getRate"
    assert c["underlying"] == "token" and c["metric_override"] == "sethfi_accountant_rate"
    from fetch.chain import ERC20_ABI
    assert any(f["name"] == "getRate" for f in ERC20_ABI)
    for ch in ("arbitrum", "base", "optimism", "scroll"):
        k = P["contracts"][f"sethfi_shares_{ch}"]
        assert k["chain"] == ch and k["kind"] == "erc20_total_supply" and k["expected_symbol"] == "sETHFI"
        assert ch in config.EVM_CHAINS


# =========================================================================== R14.4b insider stake, PUBLISHED + INFERRED
def test_4b_the_insider_share_of_the_stake_forms_with_inferred_tagged(scoped):
    inf = [k for k in P["contracts"] if k.startswith("insider_inferred_staker_")]
    assert len(inf) == 9 and all("INFERRED" in P["contracts"][k]["provenance"] for k in inf)
    assert sum(s for _a, _g, s in config.ETHERFI_INSIDER_STAKERS) == pytest.approx(34_321_467.85, abs=0.05)
    days = ["2026-11-19"]
    g = {(EF, "insider_stake_shares"): _frame("insider_stake_shares", [(d, 0.0) for d in days]),
         (EF, "etherfi_insider_inferred_stake_shares"): _frame("etherfi_insider_inferred_stake_shares",
                                                               [(d, 34_321_467.85) for d in days])}
    bw._set_sum_views(g)
    n = bw._daily(g[(EF, "etherfi_insider_stake_all_shares")])["2026-11-19"]
    assert n / 86_035_054.29 == pytest.approx(0.3989, abs=5e-5)                       # 39.9% of mainnet shares
    num, den, what = config.INSIDER_LOCK[EF]
    assert (num, den) == ("etherfi_insider_stake_all_shares", "locked_tokens")      # shares over shares
    assert "PUBLISHED" in what and "INFERRED" in what
    # a PARTIAL inferred sum (a wallet refused) is no row: all nine or none
    g2 = {(EF, "insider_stake_shares"): _frame("insider_stake_shares", [(d, 0.0) for d in days]),
          (EF, "etherfi_insider_inferred_stake_shares"): _frame(
              "etherfi_insider_inferred_stake_shares", [(d, 30_000_000.0) for d in days], "chain:sum(a+b):PARTIAL")}
    bw._set_sum_views(g2)
    assert (EF, "etherfi_insider_stake_all_shares") not in g2


# =========================================================================== R14.B the theatre flag
def test_B_the_flag_has_three_states_and_two_readings():
    s = bw.theatre_flag_state(9_300_020.0, 0.0, 2_876_642.0)        # Ether.fi as researched: nothing retired
    assert s["as_written"]["state"] == "NOT RAISED" and s["as_written"]["size"] is None
    assert "0 retired" in s["as_written"]["why"]
    assert s["any_buyback"]["state"] == "RAISED"
    assert s["any_buyback"]["size"] == pytest.approx(9_300_020 / 2_876_642)
    r = bw.theatre_flag_state(1_000.0, 4_000.0, 5_000.0)
    assert r["as_written"]["state"] == "RAISED" and r["as_written"]["size"] == pytest.approx(0.25)
    n = bw.theatre_flag_state(0.0, 4_000.0, 5_000.0)
    assert n["as_written"]["state"] == "NOT RAISED" and n["any_buyback"]["state"] == "NOT RAISED"
    u = bw.theatre_flag_state(None, 4_000.0, 5_000.0)                  # the outflow series not stored: never "not raised"
    assert u["as_written"]["state"] == u["any_buyback"]["state"] == "NOT TESTABLE"
    assert bw.theatre_flag_state(1.0, None, 5.0)["as_written"]["state"] == "NOT TESTABLE"


def test_B_the_flag_reads_the_window_and_shows_its_evidence(scoped, monkeypatch):
    spec = config.THEATRE_FLAG[EF]
    asof = pd.Timestamp("2026-11-19")
    days = pd.date_range("2026-06-01", asof)
    sent = {pd.Timestamp("2026-06-15"): 5.0, pd.Timestamp("2026-07-20"): 3_100_000.0,
            pd.Timestamp("2026-10-12"): 200_000.0}
    g = {(EF, spec["outflow_metric"]): _frame(spec["outflow_metric"], [(d, sent.get(d, 0.0)) for d in days]),
         (EF, spec["retired_metric"]): _frame(spec["retired_metric"], [(d, 0.0) for d in days]),
         (EF, "actual_buyback_tokens"): _frame("actual_buyback_tokens", [(d, 0.0) for d in days]),
         (EF, "programmatic_buyback_tokens"): _frame("programmatic_buyback_tokens",
                                                     [(d, 100_000.0 if d.dayofweek == 1 and d >= pd.Timestamp("2026-09-08")
                                                       else 0.0) for d in days])}
    bw._theatre_flag_views(g, asof)
    r = bw.THEATRE_RESULTS[EF]
    # R15 (Jake 2026-11-20, decision 2 (b)): the NAMED outflow keeps the window from 07-01 (06-15 is before it); the
    # flag's state and size read from 2026-09-08 (was: r["sent"] == 3,300,000 over 07-01..)
    assert r["named_sent"] == 3_300_000.0
    assert r["sent"] == 200_000.0
    assert r["as_written"]["state"] == "NOT RAISED" and r["any_buyback"]["state"] == "RAISED"
    txt = bw.theatre_flag_text(EF)
    for must in ("AS WRITTEN", "IF ANY BUYBACK COUNTS", "FalconX", "Binance", "Blockscout", "evidence, not proof",
                 "2026-07-01", "2026-09-08"):
        assert must in txt, must
    assert txt.startswith("SCORED — IF ANY BUYBACK COUNTS: RAISED")   # decision 1 (b) (was: a DECISION FOR JAKE line)
    bought = r["bought"]
    assert bw.theatre_flag_size(EF) == f"={200_000.0 / bought!r}"    # the scored size: sent ÷ bought since 09-08
    # (was: "n/a — NOT RAISED", the as-written size)
    # no outflow rows (the fixture's case): NOT TESTABLE, with the research read shown as research, not the store
    g.pop((EF, spec["outflow_metric"]))
    bw._theatre_flag_views(g, asof)
    assert bw.THEATRE_RESULTS[EF]["as_written"]["state"] == "NOT TESTABLE"
    assert "RESEARCH READ (not the store" in bw.theatre_flag_text(EF)
    assert bw.theatre_flag_text("Uniswap") == "" and bw.theatre_flag_size("Uniswap") == ""   # blank where none is declared


def test_B_a_scanned_buyback_that_starts_inside_the_window_is_read_from_its_first_transfer(scoped):
    spec = config.THEATRE_FLAG[EF]
    asof = pd.Timestamp("2026-11-19")
    days = pd.date_range("2026-06-01", asof)
    # R15: the flag's window opens at the programme's start (2026-09-08), so a holder whose first transfer is LATER
    # still starts inside it (was: 2026-09-08, then inside the 07-01 window)
    prog = pd.date_range("2026-09-20", asof)
    g = {(EF, spec["outflow_metric"]): _frame(spec["outflow_metric"], [(d, 0.0) for d in days]),
         (EF, spec["retired_metric"]): _frame(spec["retired_metric"], [(d, 0.0) for d in days]),
         (EF, "actual_buyback_tokens"): _frame("actual_buyback_tokens", [(d, 0.0) for d in days],
                                               "explorer:buyback_wallet_inflow"),
         (EF, "programmatic_buyback_tokens"): _frame("programmatic_buyback_tokens", [(d, 10.0) for d in prog],
                                                     "explorer:programmatic_buyback_inflow")}
    g[(EF, spec["outflow_metric"])].loc[g[(EF, spec["outflow_metric"])]["date"] == pd.Timestamp("2026-11-18"),
                                        "value"] = 1_000_010.0
    bw._theatre_flag_views(g, asof)
    r = bw.THEATRE_RESULTS[EF]
    assert r["bought"] == 10.0 * len(prog) and r["any_buyback"]["state"] == "RAISED"
    assert "programmatic_buyback_tokens from its holders' first transfer 2026-09-20" in bw.theatre_flag_text(EF)
    # the same late start on a series that is NOT a log scan is unknown, never a 0 before it
    g[(EF, "programmatic_buyback_tokens")]["source"] = "derived:x"
    bw._theatre_flag_views(g, asof)
    assert bw.THEATRE_RESULTS[EF]["any_buyback"]["state"] == "NOT TESTABLE"


def test_B_the_research_read_adds_up():
    rr = config.THEATRE_FLAG[EF]["research_read"]
    assert sum(rr["by_sender"].values()) == rr["sent"] == sum(rr["by_destination"].values()) == 9_300_020.0
    assert rr["retired_in_window"] == 0.0
    for a, d in config.THEATRE_FLAG[EF]["destinations"].items():
        assert d["label_source"] and "2026-11-19" in d["label_source"], a
    scan = next(s for s in P["log_scans"] if s["key"] == "theatre_exchange_out")
    assert scan["direction"] == "out" and scan["store"] and set(scan["count_to"]) == set(config.THEATRE_FLAG[EF]["destinations"])


def _elog(block, idx, topics, value, ts):
    return {"address": "0xtoken", "topics": topics, "data": hex(int(value)), "blockNumber": hex(block),
            "timeStamp": hex(ts), "logIndex": hex(idx), "transactionHash": f"0x{block:x}{idx:x}"}


class _Http:
    def __init__(self, logs):
        self.logs = logs

    def get(self, url, params=None, headers=None, deadline=None):
        key = (params.get("address", "").lower(), params.get("topic1"), params.get("topic2"))
        top = params.get("toBlock")
        top = float("inf") if top in (None, "latest") else int(top)
        rows = [r for r in self.logs.get(key, []) if int(params["fromBlock"]) <= int(r["blockNumber"], 16) <= top]
        off, page = int(params["offset"]), int(params["page"])
        chunk = rows[(page - 1) * off: page * off]
        return ({"status": "1", "message": "OK", "result": chunk} if chunk
                else {"status": "0", "message": "No records found", "result": []})


class _Call:
    def __init__(self, v):
        self.v = v

    def call(self, block_identifier=None):
        return self.v


class _Reader:
    def __init__(self, balances):
        self.balances = {k.lower(): v for k, v in balances.items()}

    def web3(self, chain):
        import types
        return types.SimpleNamespace(eth=types.SimpleNamespace(block_number=1_000))

    def erc20(self, chain, token):
        import types
        b = self.balances
        return types.SimpleNamespace(functions=types.SimpleNamespace(balanceOf=lambda h: _Call(b[h.lower()]),
                                                                     decimals=lambda: _Call(18)))

    @staticmethod
    def checksum(a):
        return a


def test_B_the_scan_counts_only_the_tagged_destinations(monkeypatch):
    from fetch.base import FetchOutput
    from fetch.explorer import ExplorerLogs, TRANSFER_TOPIC, pad_address
    from fetch.logscan import LogScan
    E, t0 = 10 ** 18, int(pd.Timestamp("2026-08-04").timestamp())
    safe, eoa = "0x3fb6784e263643656f386a0371644931133d7b78", "0x9eac7114d1a1eabc4732a886795cfd9e6e35843f"
    fwd, other, src = "0x80381dc31b249f3655b7b7f1ec36e3e7ddbdd07e", "0x" + "ab" * 20, "0x" + "cd" * 20
    hs, he = pad_address(safe), pad_address(eoa)
    logs = {("0xtoken", None, hs): [_elog(10, 0, [TRANSFER_TOPIC, pad_address(src), hs], 10 * E, t0)],
            ("0xtoken", hs, None): [_elog(20, 1, [TRANSFER_TOPIC, hs, pad_address(fwd)], 3 * E, t0),
                                    _elog(21, 1, [TRANSFER_TOPIC, hs, pad_address(other)], 2 * E, t0),
                                    _elog(22, 1, [TRANSFER_TOPIC, hs, he], 1 * E, t0)],
            ("0xtoken", None, he): [_elog(22, 0, [TRANSFER_TOPIC, hs, he], 1 * E, t0)],
            ("0xtoken", he, None): []}
    monkeypatch.setenv("ETHERSCAN_API_KEY", "K")
    monkeypatch.setenv("BLOCKSCOUT_API_KEY", "K")
    monkeypatch.setitem(config.EXPLORERS["etherscan"], "max_records", 1000)
    monkeypatch.setitem(config.EXPLORERS["blockscout"], "max_records", 1000)
    spec = {"key": "theatre_exchange_out", "metric": "theatre_exchange_outflow_tokens", "chain": "ethereum",
            "token": "0xtoken", "holders": [safe, eoa], "direction": "out", "store": True, "count_to": [fwd]}
    out = FetchOutput()
    LogScan(explorer=ExplorerLogs(http=_Http(logs)), reader=_Reader({safe: 4 * E, eoa: 1 * E}))._scan(
        {"name": EF}, spec, None, out)
    f = out.frame()
    assert f["value"].sum() == 3.0, f            # 3 to the forwarder; 2 elsewhere and the internal 1 are not counted


def test_B_the_named_float_outflow_is_a_statement_line_beside_the_identity(scoped):
    lab, m, _note = config.FLOW_TOKEN_MEMO[EF][0]
    assert m == config.THEATRE_FLAG[EF]["outflow_metric"] and lab.startswith("memo: named float outflow")
    asof = pd.Timestamp("2026-10-13")
    days = pd.date_range("2025-06-01", asof)
    tot = _frame("total_supply", [(d, 998_535_999) for d in days])
    g = {(EF, "total_supply"): tot,
         (EF, "circulating_supply_used"): _frame("circulating_supply_used", [(d, 998_535_999) for d in days],
                                                 "derived:circulating_supply_used[circulating_supply_onchain]"),
         (EF, m): _frame(m, [(d, 3_100_000.0 if d == pd.Timestamp("2026-07-20") else 0.0) for d in days])}
    rows = {r["Line"]: r for r in bw.flow_statements(g, asof) if r["Project"] == EF and r["Statement"] == "Token flow"}
    assert rows[lab]["Q0"] == 3_100_000.0
    exp = rows["Expected closing (opening + minted + released − returned − burned)"]["Q0"]
    assert exp == rows["Opening circulating"]["Q0"]                     # not in the expected closing
