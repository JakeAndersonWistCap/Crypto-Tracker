"""Round 21 — Pendle (Jake 2026-11-25): the buyback read from Pendle's own buyback contract, dated the day bought, with
DefiLlama's own holders revenue as the cross-check; Berachain a named gap with a time cap; a rate limit waited out."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
from fetch import pendle_buyback_chain as pbc  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402

SPEC = config.PENDLE_BUYBACK_CHAIN
ROUTER, SPENDLE, BB = SPEC["routers"][0].lower(), SPEC["payees"][0].lower(), SPEC["contract"].lower()


def _ts(day: str, hour: int = 12) -> int:
    return int((pd.Timestamp(day) + pd.Timedelta(hours=hour)).timestamp())


def _ev(day, frm, to, tokens, block=1):
    return {"block": block, "ts": _ts(day), "from": frm, "to": to, "wei": int(round(tokens * 1e6)) * 10**12,
            "tx": "0x" + "ab" * 32, "idx": 0}


def _g(project, metric, days, values, source="x"):
    return pd.DataFrame({"date": pd.to_datetime(list(days)), "project": project, "metric": metric,
                         "value": [float(v) for v in values], "tier": 1, "source": source, "is_manual": False,
                         "entered_on": ""})


# THE EPOCH OF 2026-11-03, AS READ ON CHAIN 2026-11-24: bought 11-18..11-21, paid 11-21 to sPENDLE
FILLS = {"2026-11-18": 20_735.77, "2026-11-19": 41_409.22, "2026-11-20": 43_333.38, "2026-11-21": 5_487.99}


# ================================================================================================ the reader's arithmetic
def test_daily_series_is_zero_filled_and_counts_only_the_router_and_spendle():
    ins = [_ev(d, ROUTER, BB, v) for d, v in FILLS.items()] + [_ev("2026-11-19", "0x" + "11" * 20, BB, 7.0)]
    outs = [_ev("2026-11-21", BB, SPENDLE, sum(FILLS.values())), _ev("2026-11-20", BB, "0x" + "22" * 20, 1.0)]
    s = pbc.daily_series(ins, outs, SPEC["routers"], SPEC["payees"], "2026-11-15", "2026-11-23")
    assert len(s["bought"]) == 9 and len(s["paid"]) == 9                      # every day, 11-15..11-23
    assert s["bought"][pd.Timestamp("2026-11-15")] == 0.0                    # a measured 0, not a hole
    assert abs(sum(s["bought"].values()) - 110_966.36) < 1e-6               # the router's fills only
    assert abs(s["paid"][pd.Timestamp("2026-11-21")] - 110_966.36) < 1e-6
    assert len(s["other_in"]) == 1 and len(s["other_out"]) == 1             # named, not counted


def test_epoch_check_matches_each_payout_to_its_epoch_within_two_pendle():
    api = [(pd.Timestamp("2026-10-20"), 121_223.52), (pd.Timestamp("2026-11-03"), 110_966.35),
           (pd.Timestamp("2026-01-29"), 260_701.22)]
    rows = pbc.epoch_check([("2026-11-07", 121_223.52), ("2026-11-21", 110_966.35), ("2026-02-13", 256_295.28)], api)
    assert [r[0].date().isoformat() for r in rows] == ["2026-10-20", "2026-11-03", "2026-01-29"]
    assert [r[5] for r in rows] == [True, True, False]
    assert round(rows[2][4], 2) == -4_405.94                                 # the API's first epoch reads more


class _Contract:
    def __init__(self, bal):
        self.bal = bal

        class F:
            def balanceOf(_s, a):
                class C:
                    def call(_c, block_identifier=None):
                        return bal
                return C()
        self.functions = F()


class _Eth:
    def __init__(self, bal, logs):
        self.block_number = 26_486_268
        self._bal, self._logs = bal, logs

    def get_block(self, b):
        return {"timestamp": _ts("2026-11-24", 9)}

    def contract(self, address=None, abi=None):
        return _Contract(self._bal)

    def get_logs(self, flt):
        d = "in" if flt["topics"][2] is not None else "out"
        return [x for x in self._logs if x["_dir"] == d and flt["fromBlock"] <= x["blockNumber"] <= flt["toBlock"]]


class _W3:
    def __init__(self, eth):
        self.eth = eth


def _rpc_log(day, frm, to, tokens, d):
    return {"_dir": d, "blockNumber": 26_400_000, "blockTimestamp": hex(_ts(day)), "logIndex": 1,
            "transactionHash": "0x" + "cd" * 32, "data": hex(int(round(tokens * 1e6)) * 10**12),
            "topics": [pbc.TRANSFER, pbc._pad(frm), pbc._pad(to)]}


class _Reader:
    LOGS_RANGE_TOO_WIDE = ("block range",)

    def __init__(self, bal):
        logs = [_rpc_log(d, ROUTER, BB, v, "in") for d, v in FILLS.items()] + [
            _rpc_log("2026-11-21", BB, SPENDLE, sum(FILLS.values()), "out")]
        self.w3 = _W3(_Eth(bal, logs))

    def web3(self, chain):
        return self.w3

    def log_endpoints(self, chain):
        return ["https://mainnet.gateway.tenderly.co"]

    def getlogs_cap(self, u):
        return None

    def make_web3(self, chain, u, t):
        return self.w3

    @staticmethod
    def checksum(a):
        return a

    def block_timestamp(self, chain, b):
        raise AssertionError("every log here is dated")


def _api(url):
    return {"sPendleHistoricalData": {"timestamps": [int(pd.Timestamp("2026-11-03").timestamp())],
                                      "buybackAmounts": [110_966.35]}}


def test_the_reader_stores_every_day_once_the_contract_reconciles_to_the_wei(monkeypatch):
    monkeypatch.setitem(SPEC, "first_day", "2026-11-15")
    out = FetchOutput()
    pbc.PendleBuybackChain(reader=_Reader(bal=0), get_api=_api).run([config.PROJECT_BY_NAME["Pendle"]], None, out)
    assert not out.gaps
    fr = pd.concat(out.frames)
    b = fr[fr.metric == "pendle_buyback_bought_tokens"]
    assert b["date"].min() == pd.Timestamp("2026-11-15") and b["date"].max() == pd.Timestamp("2026-11-23")
    assert abs(b["value"].sum() - 110_966.36) < 1e-6 and (b["value"] == 0).sum() == 5
    assert set(fr["source"]) == {"pendle_buyback_chain:ethereum"}
    assert any("1 of 1 payout(s) equal" in e.message for e in out.log)


def test_the_reader_stores_nothing_when_the_balance_does_not_reconcile(monkeypatch):
    monkeypatch.setitem(SPEC, "first_day", "2026-11-15")
    out = FetchOutput()
    pbc.PendleBuybackChain(reader=_Reader(bal=10**18), get_api=_api).run([config.PROJECT_BY_NAME["Pendle"]], None, out)
    assert not out.frames
    assert out.gaps and "DOES NOT RECONCILE" in out.gaps[0]["reason"]


# ==================================================================================================== the build's view
def test_the_buyback_is_the_contracts_purchases_dated_the_day_bought(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Pendle"]])
    days = list(pd.date_range("2026-11-15", "2026-11-23"))
    bought = [FILLS.get(str(d.date()), 0.0) for d in days]
    groups = {
        ("Pendle", "pendle_buyback_bought_tokens"): _g("Pendle", "pendle_buyback_bought_tokens", days, bought,
                                                       "pendle_buyback_chain:ethereum"),
        ("Pendle", "price_usd"): _g("Pendle", "price_usd", days, [1.4] * len(days), "coingecko"),
        # the epoch spread G11 B9 built: replaced wherever the contract's series is stored
        ("Pendle", "actual_buyback_tokens"): _g("Pendle", "actual_buyback_tokens", days[:2], [9.0, 9.0], "pendle_api:x"),
        ("Pendle", "holders_revenue_usd_defillama"): _g("Pendle", "holders_revenue_usd_defillama", days,
                                                        [7_000.0] * len(days), "defillama"),
    }
    bw._chain_buyback_views(groups)
    tok = groups[("Pendle", "actual_buyback_tokens")]
    assert len(tok) == 9 and pd.to_datetime(tok["date"]).max() == pd.Timestamp("2026-11-23")   # current to yesterday
    assert abs(tok["value"].sum() - 110_966.36) < 1e-6
    assert tok["source"].iloc[0].startswith("pendle_buyback_chain:ethereum[")
    usd = groups[("Pendle", "actual_buyback_usd")]
    assert abs(usd["value"].sum() - 110_966.36 * 1.4) < 1e-6 and set(usd["source"]) == {"derived:tokens*price"}
    # the cross-check is DefiLlama's own holders revenue / same-day price
    der = groups[("Pendle", "actual_buyback_tokens_derived")]
    assert abs(der["value"].sum() - 9 * 7_000 / 1.4) < 1e-6
    assert set(groups[("Pendle", "actual_buyback_usd_derived")]["source"]) == {"derived:=holders_revenue_usd_defillama"}


def test_without_the_contracts_series_the_epoch_spread_stands(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Pendle"]])
    spread = _g("Pendle", "actual_buyback_tokens", ["2026-09-08"], [10_000.0], "pendle_api:x[spread]")
    groups = {("Pendle", "actual_buyback_tokens"): spread}
    bw._chain_buyback_views(groups)
    assert groups[("Pendle", "actual_buyback_tokens")] is spread


# ============================================================================================================ the config
def test_the_handover_is_declared_and_the_retired_derivation_withdrawn():
    h = config.declared_handover("Pendle", "actual_buyback_tokens")
    assert h["ordered_points"] == ("pendle_api:sPendleHistoricalData.buybackAmounts", "pendle_buyback_chain:ethereum")
    assert config.metric_restatements("Pendle") == {} and config.buyback_tokens_from_usd("Pendle") is None
    assert config.MEASURED_BUYBACK["Pendle"]["chain_metric"] == "pendle_buyback_bought_tokens"
    assert config.MEASURED_BUYBACK["Pendle"]["cross_check_metric"] == "holders_revenue_usd_defillama"
    # the per-epoch check's numbers on record (read 2026-11-24): 21 payouts, the API's two higher epochs
    r = SPEC["read_2026_11_24"]
    assert r["payouts"] == 21 and r["within_2"] == 19
    assert round(r["api_tokens"] - r["paid_tokens"], 2) == 11_391.59
    # the cross-check's tolerance is unchanged
    assert config.CREDIBILITY["Pendle"]["in_buyback"]


def test_berachain_is_a_named_gap_with_a_time_cap():
    g = config.PENDLE_BERACHAIN_GAP
    assert g["seed_cap_min"] == 10
    assert "590.18" in g["worth"] and "18,126.19" in g["worth"]
    assert "gauge_pendle_out_berachain" in config.NAMED_SCAN_GAPS
    assert "Time-capped" in config.NAMED_SCAN_GAPS["gauge_pendle_out_berachain"]


def test_the_seed_runs_under_the_cap_and_names_the_gap(monkeypatch):
    import token_metrics as tm
    from fetch import logscan
    seen = {}

    class Stub:
        def __init__(self, **kw):
            seen.update(kw)

        def run(self, projects, window_days, out):
            out.fail("explorer", "Pendle", "gauge_pendle_out_berachain: SCAN DOES NOT RECONCILE ... HISTORY REPAIR IN "
                                           "PROGRESS: span re-read IN PROGRESS: blocks 1..2 of ..3", 2)

    class St:
        rows = []

        def latest_values(self):
            return {}

        def upsert(self, f):
            return 0

        def record_fetch(self, *a):
            St.rows.append(a)

    monkeypatch.setattr(logscan, "LogScan", Stub)
    p = config.PROJECT_BY_NAME["Pendle"]
    monkeypatch.setitem(p, "log_scans", [{"key": "gauge_pendle_out_berachain", "own_tier": "explorer_l2:berachain"}])
    import logging
    assert tm.seed_pendle_berachain(St(), logging.getLogger("t")) == 1
    assert seen["unbounded"] is True and seen["cap_s"] == 600
    assert any("NAMED GAP" in str(r[5]) and "18,126.19" in str(r[5]) for r in St.rows)


def test_logscan_cap_sets_a_deadline():
    from fetch.logscan import LogScan
    ls = LogScan(reader=object(), cache=type("C", (), {"root": Path(".")})(), unbounded=True, cap_s=5)
    ls.run([], None, FetchOutput())
    assert ls._cap_deadline is not None


# ====================================================================================================== the rate limit
def test_a_rate_limit_is_waited_out_then_the_same_endpoint_asked_again(monkeypatch):
    from fetch import chain as ch
    calls, slept = [], []

    class E:
        def get_logs(self, flt):
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("429 Client Error: Too Many Requests")
            return [{"x": 1}]

    r = ch.ChainReader.__new__(ch.ChainReader)
    monkeypatch.setattr(ch.ChainReader, "make_web3", staticmethod(lambda c, u, t=30: type("W", (), {"eth": E()})()))
    monkeypatch.setattr(ch.time, "sleep", lambda s: slept.append(s))
    assert r._logs_with_backoff("berachain", "https://rpc.berachain.com", {}, None, None) == [{"x": 1}]
    assert slept == [2, 4] and len(calls) == 3


def test_any_other_error_is_not_retried(monkeypatch):
    from fetch import chain as ch

    class E:
        def get_logs(self, flt):
            raise RuntimeError("413 Request Entity Too Large")

    r = ch.ChainReader.__new__(ch.ChainReader)
    monkeypatch.setattr(ch.ChainReader, "make_web3", staticmethod(lambda c, u, t=30: type("W", (), {"eth": E()})()))
    with pytest.raises(RuntimeError):
        r._logs_with_backoff("berachain", "https://rpc.berachain.com", {}, None, None)
