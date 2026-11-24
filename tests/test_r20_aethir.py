"""Round 20 — Aethir (Jake 2026-11-24; SPEC "Round 20 — Aethir"). Behaviour and numbers, never wording.

1. The vesting stock and the issuer wallets: the on-chain schedule, the vesters' balances, the wallets held (direct
   and INFERRED apart), the issuer stake — one finalized block, all-or-no-row, the past days from the Transfer logs.
2. The daily fee burn: per UTC day from the burn contract's transfers to the dead address; refused when the logs do
   not sum to the dead address's balance.
3. The three revenue lines: fees, fees x the published protocol fee rate, the burn at the day's price.
4. Unlocks and total dilution form from the vesters' stock; the wallets leave the float; the insider share is wired.
No network: every read is a stub."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import reader  # noqa: E402
from fetch import aethir_chain as ac  # noqa: E402
from fetch import base  # noqa: E402
from fetch.base import FetchOutput  # noqa: E402

ATH = config.PROJECT_BY_NAME["Aethir"]
SPEC = ATH["aethir_chain"]
VW = SPEC["vesting_wallet"]
COLS = ["date", "project", "metric", "value", "source", "tier"]
READ_TS = int(pd.Timestamp("2026-11-23 17:07:35").timestamp())          # the finalized block of the recorded read
WEI = 10 ** 18
# R21: the day marks name the project and the series they vouch for (were "aethir_chain:ethereum" / "aethir_chain:burn")
_K_ETH, _K_BURN = "aethir_chain:Aethir:ath_issuer_held_tokens", "aethir_chain:Aethir:gross_burn_tokens"


def _g(metric, rows, source="x", project="Aethir", tier=2):
    return pd.DataFrame([{"date": pd.Timestamp(x[0]), "project": project, "metric": metric, "value": float(x[1]),
                          "source": x[2] if len(x) > 2 else source, "tier": tier} for x in rows], columns=COLS)


# ============================================================================================ 1. the vesting schedule
def test_the_schedule_on_file_is_the_chain_s_85_tranches_and_reproduces_the_recorded_read():
    sched = VW["schedule"]
    assert len(sched) == 85 and sum(a for _d, a in sched) == pytest.approx(25_591_527_408.39, abs=0.01)
    # not yet vested at the recorded block: 11,065,600,000.39 of the 11,804,099,993.76 the contract held
    assert ac.unvested(sched, READ_TS) == pytest.approx(11_065_600_000.39, abs=0.01)
    assert VW["read"]["balance"] - ac.unvested(sched, READ_TS) == pytest.approx(738_499_993.37, abs=0.01)
    # the 13 releases paid exactly the tranches dated up to the last one (2026-09-15 17:52 UTC)
    last_release = int(pd.Timestamp("2026-09-15 17:52").timestamp())
    assert sum(a for d, a in sched if d <= last_release) == pytest.approx(13_787_427_408.0, abs=0.01)
    # the tranche of 2026-11-12 00:00 UTC vests at the start of that day: out of the stock at that day's end
    d = pd.Timestamp("2026-11-11")
    assert (ac.unvested(sched, (d + pd.Timedelta(days=1)).timestamp() - 1)
            - ac.unvested(sched, (d + pd.Timedelta(days=2)).timestamp())) == pytest.approx(369_250_000.0)


def test_the_non_circulating_set_reproduces_aethir_s_own_circulating_within_a_fifth_of_a_percent():
    vesters = sum(v["read"] for v in SPEC["vesters"])
    ours = 42_000_000_000 - VW["read"]["unvested"] - vesters - SPEC["burn"]["read"]["total"]
    assert vesters == pytest.approx(6_092_058_683.0)
    assert ours == pytest.approx(24_830_386_135, abs=2)
    assert ours / 24_798_862_034 - 1 == pytest.approx(0.00127, abs=0.0001)     # dashboard athCirculatingSupply, same hour


def _encode_distribution(total, done, left):
    w = [int(round(total * 100)) * 10 ** 16, int(round(done * 100)) * 10 ** 16, 0x60, len(left)]
    for d, a in left:
        w += [int(d), int(round(a * 100)) * 10 ** 16]
    return "0x" + "".join(f"{x:064x}" for x in w)


def test_a_re_scheduled_vesting_is_refused_not_mixed():
    left = [(d, a) for d, a in VW["schedule"] if d > READ_TS - 90 * 86400]      # two vested tranches still listed
    total, done, got = ac.decode_distribution(_encode_distribution(VW["total"], 13_787_427_408.0, left))
    assert total == pytest.approx(VW["total"]) and done == pytest.approx(13_787_427_408.0) and len(got) == len(left)
    assert ac.schedule_matches(VW, total, got, READ_TS) == []
    moved = [(d + 86400 * 30, a) if i == 5 else (d, a) for i, (d, a) in enumerate(got)]       # one tranche postponed
    assert ac.schedule_matches(VW, total, moved, READ_TS)
    assert ac.schedule_matches(VW, total - 1_000_000.0, got, READ_TS)                         # the total changed


# ================================================================================================ the Transfer logs
def test_past_balances_come_from_the_logs_only_when_they_account_for_the_balance():
    a, b = "0x" + "a" * 40, "0x" + "b" * 40
    t = lambda s: int(pd.Timestamp(s).timestamp())  # noqa: E731
    logs = [{"block": 1, "ts": t("2026-01-01 10:00"), "from": b, "to": a, "value": 1000 * WEI},
            {"block": 2, "ts": t("2026-03-05 23:59:59"), "from": a, "to": b, "value": 300 * WEI},
            {"block": 3, "ts": t("2026-03-07 00:00:00"), "from": a, "to": b, "value": 200 * WEI}]
    days = [pd.Timestamp(x) for x in ("2026-03-04", "2026-03-05", "2026-03-06", "2026-03-07")]
    got = ac.day_end_balances(500.0, 3, logs, a, days)
    assert [got[d] for d in days] == [1000.0, 700.0, 700.0, 500.0]              # each day's END, by block time
    assert ac.day_end_balances(501.0, 3, logs, a, days) is None                 # 1 ATH unexplained: no history at all
    assert ac.day_end_balances(500.0, 3, logs[1:], a, days) is None             # a log missing
    assert ac.day_end_balances(500.0, 3, [dict(logs[0], ts=0)] + logs[1:], a, days) is None   # an undated log


def test_the_burn_is_summed_per_utc_day_and_a_day_without_one_is_a_measured_zero():
    burner, other = SPEC["burn"]["burner"].lower(), "0x" + "c" * 40
    t = lambda s: int(pd.Timestamp(s).timestamp())  # noqa: E731
    ev = [{"ts": t("2026-11-20 09:42:31"), "from": burner, "value": int(632_278.1371 * WEI)},
          {"ts": t("2026-11-22 03:48:48"), "from": burner, "value": int(633_411.1116 * WEI)},
          {"ts": t("2026-11-22 03:49:08"), "from": burner, "value": int(635_228.7033 * WEI)},
          {"ts": t("2026-11-22 12:00:00"), "from": other, "value": 5 * WEI}]     # a stray transfer: not the burn
    got = ac.daily_burns(ev, burner, "2026-11-20", "2026-11-22")
    assert got[pd.Timestamp("2026-11-20")] == pytest.approx(632_278.1371)
    assert got[pd.Timestamp("2026-11-21")] == 0.0
    assert got[pd.Timestamp("2026-11-22")] == pytest.approx(1_268_639.8149)
    assert ac.daily_burns(ev, burner, "2026-11-23", "2026-11-22") == {}          # no complete day yet


def test_the_recorded_burns_sum_to_the_chain_s_total_and_the_dashboard_s():
    rd = SPEC["burn"]["read"]
    assert sum(v for _d, v in config.AETHIR_BURNS_READ) == pytest.approx(rd["total"], abs=0.01) == pytest.approx(
        float(rd["dashboard_cumulative"]), abs=0.01)
    assert rd["total"] == pytest.approx(11_955_182.09375163)
    assert rd["dead_balance"] - rd["total"] == pytest.approx(0.0159, abs=0.0001)   # one stray transfer, not counted
    assert len([v for _d, v in config.AETHIR_BURNS_READ if v == 0.0]) == 1         # 2026-11-21: no burn that day


# ================================================================================================ the daily tier
class _Resp:
    def __init__(self, body, status=200):
        self._b, self.status_code = body, status

    def json(self):
        return self._b


class _World:
    """A stub Ethereum + Arbitrum: balances as recorded, Transfer logs that account for them, the burns of the read."""
    ETH_BLOCK, ARB_BLOCK = 26_479_225, 526_872_395

    def __init__(self, fail_address=None, schedule_shift=0, drop_log_of=None, dead_extra=0.0):
        self.fail, self.shift, self.drop, self.dead_extra = (fail_address or "").lower(), schedule_shift, \
            (drop_log_of or "").lower(), dead_extra
        self.bal = {VW["address"].lower(): VW["read"]["balance"]}
        self.bal.update({v["address"].lower(): v["read"] for v in SPEC["vesters"]})
        self.bal.update({w["address"].lower(): w["read"] for w in SPEC["wallets"]})
        self.eath = {SPEC["wallets"][1]["address"].lower(): 168_450_044.98}
        t = lambda s: int(pd.Timestamp(s).timestamp())  # noqa: E731
        src = "0x" + "9" * 40
        self.logs = []
        for i, (a, v) in enumerate(self.bal.items()):
            # funded 100 ATH above today's balance in 2024, 100 ATH paid out on 2026-10-01 (a claim / a payment)
            self.logs.append({"blockNumber": hex(21_000_000 + i), "blockTimestamp": hex(t("2024-11-01") + i),
                              "transactionHash": f"0xf{i}", "logIndex": "0x1",
                              "topics": [ac.TRANSFER, "0x" + ac._pad(src), "0x" + ac._pad(a)],
                              "data": hex(int(round(v * 100)) * 10 ** 16 + 100 * WEI)})
            if a != self.drop:
                self.logs.append({"blockNumber": hex(26_100_000 + i), "blockTimestamp": hex(t("2026-10-01 12:00") + i),
                                  "transactionHash": f"0xc{i}", "logIndex": "0x2",
                                  "topics": [ac.TRANSFER, "0x" + ac._pad(a), "0x" + ac._pad(src)],
                                  "data": hex(100 * WEI)})
        burner, dead = SPEC["burn"]["burner"], SPEC["burn"]["dead"]
        self.burns = [{"blockNumber": hex(520_000_000 + i), "blockTimestamp": hex(t(d + " 05:00")),
                       "transactionHash": f"0xb{i}", "logIndex": "0x3",
                       "topics": [ac.TRANSFER, "0x" + ac._pad(burner), "0x" + ac._pad(dead)],
                       "data": hex(int(round(v * 10 ** 4)) * 10 ** 14)}
                      for i, (d, v) in enumerate(config.AETHIR_BURNS_READ) if v]
        self.calls = []

    def post(self, url, json=None, timeout=None):
        m, p = json["method"], json["params"]
        arb = "arb" in url
        self.calls.append((m, "arb" if arb else "eth"))
        if m == "eth_getBlockByNumber":
            if arb:
                return _Resp({"result": {"number": hex(self.ARB_BLOCK), "timestamp": hex(READ_TS + 900)}})
            assert p[0] == "finalized"
            return _Resp({"result": {"number": hex(self.ETH_BLOCK), "timestamp": hex(READ_TS)}})
        if m == "eth_getLogs":
            f = p[0]
            if arb:
                return _Resp({"result": [e for e in self.burns
                                         if int(f["fromBlock"], 16) <= int(e["blockNumber"], 16) <= int(f["toBlock"], 16)]})
            want_from, want_to = (f["topics"] + [None, None])[1:3]
            out = []
            for e in self.logs:
                if not int(f["fromBlock"], 16) <= int(e["blockNumber"], 16) <= int(f["toBlock"], 16):
                    continue
                if (want_from and e["topics"][1] in want_from) or (want_to and e["topics"][2] in want_to):
                    out.append(e)
            return _Resp({"result": out})
        to, data = p[0]["to"].lower(), p[0]["data"]
        if arb:
            total = sum(int(e["data"], 16) for e in self.burns) + int(self.dead_extra * WEI)
            return _Resp({"result": hex(total)})
        assert p[1] == hex(self.ETH_BLOCK)                        # every Ethereum figure at the ONE finalized block
        if data.startswith(ac.GET_DISTRIBUTION):
            left = [(d + self.shift, a) for d, a in VW["schedule"] if d > READ_TS - 60 * 86400]
            return _Resp({"result": _encode_distribution(VW["total"], VW["read"]["distributed"], left)})
        who = "0x" + data[-40:]
        if data.startswith(ac.LOCKED):
            return _Resp({"result": "0x" + "0" * 128})
        if who == self.fail:
            return _Resp({"error": {"message": "boom"}})
        if to == SPEC["stake"]["eath"].lower():
            return _Resp({"result": hex(int(round(self.eath.get(who, 0.0) * 100)) * 10 ** 16)})
        return _Resp({"result": hex(int(round(self.bal[who] * 100)) * 10 ** 16)})


class _Daily:
    def __init__(self):
        self.marks = set()

    def due(self, k, d):
        return (k, d) not in self.marks

    def done(self, k, d):
        self.marks.add((k, d))


def _run(world, monkeypatch):
    for env in ("ETHEREUM_RPC_URL", "ARBITRUM_RPC_URL"):
        monkeypatch.delenv(env, raising=False)
    out, daily = FetchOutput(), _Daily()
    ac.AethirChain(session=world, daily=daily).run([ATH], 1, out)
    return {f["metric"].iloc[0]: f.sort_values("date").set_index("date")["value"] for f in out.frames}, out, daily


def test_one_run_stores_today_and_the_past_days_of_every_series(monkeypatch):
    got, out, daily = _run(_World(), monkeypatch)
    assert set(got) == {"ath_vesting_unvested_tokens", "ath_vester_balance_tokens", "ath_issuer_held_tokens",
                        "ath_issuer_inferred_held_tokens", "ath_insider_staked_tokens", "ath_issuer_eath_tokens",
                        "gross_burn_tokens", "burn_address_balance"}
    assert {k for k, _d in daily.marks} == {_K_ETH, _K_BURN}
    day = pd.Timestamp("2026-11-23")
    assert got["ath_vesting_unvested_tokens"][day] == pytest.approx(11_065_600_000.39, abs=0.01)
    assert got["ath_vester_balance_tokens"][day] == pytest.approx(6_092_058_683.0)
    # direct = the two Safes + the VestingWallet's vested-unreleased tranche; inferred apart
    assert got["ath_issuer_held_tokens"][day] == pytest.approx(3_451_450_629.89 + 53_219_115.75 + 738_499_993.37, abs=0.01)
    assert got["ath_issuer_inferred_held_tokens"][day] == pytest.approx(1_018_003_661.04 + 2_462_540_823.71, abs=0.01)
    assert got["ath_insider_staked_tokens"][day] == 0.0 and len(got["ath_insider_staked_tokens"]) == 1
    assert got["ath_issuer_eath_tokens"][day] == pytest.approx(168_450_044.98)
    # the past 455 days in the same run: before each address's 100 ATH payment of 2026-10-01 it held 100 more
    for m, n in (("ath_vester_balance_tokens", 3), ("ath_issuer_inferred_held_tokens", 2)):
        s = got[m]
        assert len(s) == 456
        assert s[pd.Timestamp("2026-09-30")] - s[pd.Timestamp("2026-10-01")] == pytest.approx(100.0 * n)
        assert s[pd.Timestamp("2026-10-01")] == pytest.approx(s[day])
    # the schedule's own past days: the tranche of 2026-11-12 was still unvested on 11-11
    u = got["ath_vesting_unvested_tokens"]
    assert u[pd.Timestamp("2026-11-11")] - u[pd.Timestamp("2026-11-12")] == pytest.approx(369_250_000.0)
    # the burn: every complete day from the launch day, today (incomplete) not among them
    b = got["gross_burn_tokens"]
    assert b.index.min() == pd.Timestamp("2026-11-02") and b.index.max() == pd.Timestamp("2026-11-22")
    assert b[pd.Timestamp("2026-11-02")] == 0.0 and b[pd.Timestamp("2026-11-21")] == 0.0
    assert b.sum() == pytest.approx(11_955_182.0938 - 635_257.2326, abs=0.01)
    assert got["burn_address_balance"][day] == pytest.approx(11_955_182.0938, abs=0.001)
    assert all(e.status == "ok" for e in out.log)


def test_the_past_days_are_not_read_again_the_day_after_a_completed_run(monkeypatch):
    monkeypatch.delenv("ETHEREUM_RPC_URL", raising=False)
    world, out, daily = _World(), FetchOutput(), _Daily()
    yday = str((base.today() - pd.Timedelta(days=1)).date())
    daily.done(_K_ETH, yday)                      # yesterday's run completed
    ac.AethirChain(session=world, daily=daily).run([ATH], 1, out)
    got = {f["metric"].iloc[0]: f for f in out.frames}
    assert len(got["ath_vester_balance_tokens"]) == 1 and len(got["ath_issuer_held_tokens"]) == 1
    assert ("eth_getLogs", "eth") not in world.calls               # no log scan on Ethereum
    assert len(got["gross_burn_tokens"]) == 21                     # the burn's days are always the scan itself
    assert (_K_ETH, str(base.today().date())) in daily.marks


def test_live_and_past_rows_share_one_measuring_point(monkeypatch):
    out, daily = FetchOutput(), _Daily()
    monkeypatch.delenv("ETHEREUM_RPC_URL", raising=False)
    ac.AethirChain(session=_World(), daily=daily).run([ATH], 1, out)
    for f in out.frames:
        assert len({base._measuring_point(s) for s in f["source"]}) == 1, f["metric"].iloc[0]
    src = {f["metric"].iloc[0]: f["source"].iloc[0] for f in out.frames}
    assert "INFERRED" in src["ath_issuer_inferred_held_tokens"] and "INFERRED" not in src["ath_issuer_held_tokens"]


def test_a_wallet_that_cannot_be_read_stores_no_ethereum_row_and_the_day_is_asked_again(monkeypatch):
    got, out, daily = _run(_World(fail_address=SPEC["wallets"][2]["address"]), monkeypatch)
    assert set(got) == {"gross_burn_tokens", "burn_address_balance"}             # the Arbitrum part is its own read
    assert {k for k, _d in daily.marks} == {_K_BURN}
    assert [e.status for e in out.log].count("failed") == 1


def test_a_changed_schedule_stores_nothing(monkeypatch):
    got, out, daily = _run(_World(schedule_shift=86400 * 30), monkeypatch)
    assert "ath_vesting_unvested_tokens" not in got and "ath_issuer_held_tokens" not in got
    assert _K_ETH not in {k for k, _d in daily.marks}


def test_logs_that_do_not_account_for_a_balance_store_today_only_and_ask_again(monkeypatch):
    got, out, daily = _run(_World(drop_log_of=SPEC["vesters"][0]["address"]), monkeypatch)
    assert len(got["ath_vester_balance_tokens"]) == 1 and len(got["ath_issuer_held_tokens"]) == 1
    assert _K_ETH not in {k for k, _d in daily.marks}           # the past days are owed
    assert any(e.status == "failed" and "NO PAST DAY" in e.message for e in out.log)


def test_burn_logs_that_do_not_sum_to_the_dead_balance_store_nothing(monkeypatch):
    got, out, daily = _run(_World(dead_extra=1000.0), monkeypatch)
    assert "gross_burn_tokens" not in got and "burn_address_balance" not in got
    assert _K_BURN not in {k for k, _d in daily.marks}


def test_the_tier_is_registered_with_a_budget():
    import fetch
    assert "aethir_chain" in [t[0] for t in fetch.TIER_ORDER] and fetch.TIER_BUDGET_S["aethir_chain"] >= 120
    assert fetch.adapter_files()["aethir_chain"][1] == "aethir_chain"


# ============================================================================================ 3. the revenue lines
def test_the_three_lines_are_fees_the_protocol_fee_and_the_burn():
    s = config.revenue_split("Aethir")
    assert s["fees"]["metric"] == "customer_revenue_usd"
    assert s["revenue"]["metric"] == "s13_protocol_fee_usd" and s["revenue"]["inferred"] is True
    assert s["holder_revenue"]["metric"] == "holder_value_revenue_usd"
    bc = config.HOLDER_VALUE["Aethir"]["burn_credited"]
    assert bc["metric"] == "gross_burn_tokens" and bc["share_of_revenue"] == 1.0
    assert config.holder_value_absent("Aethir") is None                 # "nothing reaches holders" no longer holds
    mets = config.metrics_for_project(ATH)
    for m in ("customer_revenue_usd", "s13_protocol_fee_usd", "holder_value_revenue_usd", "gross_burn_tokens",
              "burn_address_balance", "cloud_host_revenue_share_pct"):
        assert m in mets, m
    assert config.series_granularity("Aethir", "s13_protocol_fee_usd") == "weekly" == config.series_granularity(
        "Aethir", "customer_revenue_usd")


def test_revenue_is_fees_times_the_fee_rate_of_the_week():
    groups = {
        ("Aethir", "customer_revenue_usd"): _g("customer_revenue_usd", [
            ("2026-10-19", 900_000.0), ("2026-10-26", 1_000_000.0),      # the fixed 20% (to 2026-11-01)
            ("2026-11-02", 800_000.0), ("2026-11-09", 700_000.0),        # the floating fee: 100 − host share
            ("2026-11-16", 600_000.0)], tier=3),                         # no share published for this week: no row
        ("Aethir", "cloud_host_revenue_share_pct"): _g("cloud_host_revenue_share_pct", [
            ("2026-10-26", 80.0), ("2026-11-02", 90.05), ("2026-11-09", 89.13)], tier=3)}
    bw._s13_fee_rate_views(groups)
    rev = bw._daily(groups[("Aethir", "s13_protocol_fee_usd")])
    assert rev[pd.Timestamp("2026-10-19")] == pytest.approx(180_000.0)             # fixed, no share row that week
    assert rev[pd.Timestamp("2026-10-26")] == pytest.approx(200_000.0)             # the stored share: 100 − 80
    assert rev[pd.Timestamp("2026-11-02")] == pytest.approx(800_000.0 * 0.0995)    # $79,600
    assert rev[pd.Timestamp("2026-11-09")] == pytest.approx(700_000.0 * 0.1087)    # $76,090
    assert pd.Timestamp("2026-11-16") not in rev.index                              # unknown, never 0
    # a week-old share never carries forward to the next week
    del groups[("Aethir", "s13_protocol_fee_usd")]
    groups[("Aethir", "customer_revenue_usd")] = _g("customer_revenue_usd", [("2026-11-16", 600_000.0)])
    bw._s13_fee_rate_views(groups)
    assert ("Aethir", "s13_protocol_fee_usd") not in groups
    bw._s13_fee_rate_views({})                                                      # no fees series: no line, no error


def test_the_fixture_s_revenue_line_is_a_fifth_of_its_fees_row_for_row():
    from real_dump import load
    rows = [(m["date"], m["value"], m["source"]) for m in load("Aethir")["metrics"] if m["metric"] == "customer_revenue_usd"]
    groups = {("Aethir", "customer_revenue_usd"): _g("customer_revenue_usd", rows, tier=3)}
    bw._s13_fee_rate_views(groups)
    fees, rev = bw._daily(groups[("Aethir", "customer_revenue_usd")]), bw._daily(groups[("Aethir", "s13_protocol_fee_usd")])
    assert len(rev) == len(fees) == 17 and list(rev.index) == list(fees.index)
    q0 = (fees.index >= "2026-07-20") & (fees.index <= "2026-10-05")                # the 12 weeks inside Q0 of 2026-10-13
    assert fees[q0].sum() == pytest.approx(11_925_590.09, abs=0.01)
    assert rev[q0].sum() == pytest.approx(2_385_118.02, abs=0.01)                   # a fifth of it


def test_the_burn_at_the_day_s_price_is_the_credited_holder_value(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", [ATH])
    groups = {("Aethir", "gross_burn_tokens"): _g("gross_burn_tokens", [("2026-11-20", 632_278.1371), ("2026-11-21", 0.0),
                                                                        ("2026-11-22", 1_268_639.8149)]),
              ("Aethir", "price_usd"): _g("price_usd", [("2026-11-20", 0.0032), ("2026-11-21", 0.0031),
                                                        ("2026-11-22", 0.0030)])}
    bw._holder_value_views(groups)
    hv = bw._daily(groups[("Aethir", "holder_value_revenue_usd")])
    assert hv[pd.Timestamp("2026-11-20")] == pytest.approx(632_278.1371 * 0.0032)            # $2,023.29
    assert hv[pd.Timestamp("2026-11-21")] == 0.0
    assert hv[pd.Timestamp("2026-11-22")] == pytest.approx(1_268_639.8149 * 0.0030)          # $3,805.92


def test_before_the_burn_existed_it_is_a_declared_zero_and_after_it_only_what_was_measured(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", [ATH])
    z = config.DECLARED_ZERO[("Aethir", "gross_burn_tokens")]
    assert z["until"] == "2026-11-01" and ("Aethir", "holder_value_revenue_usd") not in config.DECLARED_ZERO
    # the fixture's day: no burn row at all — a 0 on every day of the window
    groups = {("Aethir", "price_usd"): _g("price_usd", [("2026-10-13", 0.0059)])}
    bw._declared_zero_views(groups, pd.Timestamp("2026-10-13"))
    b = bw._daily(groups[("Aethir", "gross_burn_tokens")])
    assert b.index.max() == pd.Timestamp("2026-10-13") and (b == 0.0).all() and len(b) > 300
    # a store run after go-live: zeros to 2026-11-01, the measured days stand, an unread day stays a hole (never 0)
    groups = {("Aethir", "gross_burn_tokens"): _g("gross_burn_tokens", [("2026-11-02", 0.0), ("2026-11-03", 512_764.4675),
                                                                        ("2026-11-05", 513_979.4906)])}
    bw._declared_zero_views(groups, pd.Timestamp("2026-11-23"))
    b = bw._daily(groups[("Aethir", "gross_burn_tokens")])
    assert b[pd.Timestamp("2026-11-01")] == 0.0 and b[pd.Timestamp("2026-11-03")] == pytest.approx(512_764.4675)
    assert pd.Timestamp("2026-11-04") not in b.index and b.index.max() == pd.Timestamp("2026-11-05")
    assert "Aethir" in config.BUYING_PRESSURE_BY_DESIGN


# ====================================================================================== 4. unlocks, float, insiders
def test_the_unlock_leg_is_the_vesters_stock_and_total_dilution_forms_on_a2():
    assert config.unlock_stock_metrics("Aethir") == ("ath_vester_balance_tokens",) and config.has_unlock_leg("Aethir")
    # the A-tab's total dilution is no longer "not formed" for want of a non-circulating wallet set
    col = reader.Col(header="Total dilution, % of float a year", definition="")
    _code, why = reader.blank_reason(ATH, None, col)
    assert "no non-circulating wallet set is declared" not in why
    _code, why = reader.blank_reason(config.PROJECT_BY_NAME["Bittensor"], None, col)     # the reason itself is unchanged
    assert "no non-circulating wallet set is declared" in why
    # the VestingWallet's stock is stored but is not an unlock leg (its releases leave the Safe as emissions)
    assert "ath_vesting_unvested_tokens" not in config.unlock_stock_metrics("Aethir")
    assert "ath_vesting_unvested_tokens" in config.metrics_for_project(ATH)


def test_the_unlock_is_the_fall_of_the_vesters_stock_and_a_top_up_releases_nothing(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", [ATH])
    src = ac.POINT_VESTERS + "[x]"
    groups = {("Aethir", "ath_vester_balance_tokens"): _g("ath_vester_balance_tokens", [
        ("2026-10-01", 6_400_000_000.0, src), ("2026-10-02", 6_395_000_000.0, src),      # 5,000,000 claimed
        ("2026-10-03", 6_420_000_000.0, src),                                            # a top-up: releases 0
        ("2026-10-04", 6_412_500_000.0, src)])}                                          # 7,500,000 claimed
    bw._unlock_views(groups)
    rel = bw._daily(groups[("Aethir", "unlock_release_tokens")])
    assert rel[pd.Timestamp("2026-10-02")] == pytest.approx(5_000_000.0)
    assert rel[pd.Timestamp("2026-10-03")] == 0.0
    assert rel[pd.Timestamp("2026-10-04")] == pytest.approx(7_500_000.0)


def test_the_issuer_wallets_leave_the_float_from_their_first_stored_row():
    r = config.RULE_IN["Aethir"]
    assert r["legs"] == () and r["off_float"] == ("ath_issuer_held_tokens", "ath_issuer_inferred_held_tokens")
    assert set(r["off_float_from"]) == set(r["off_float"])
    # no stored row yet (the fixture): the float is what it was — pending, never a 0
    groups = {("Aethir", "circulating_supply"): _g("circulating_supply", [("2026-10-13", 24_053_550_151.0)])}
    bw._rule_in_views(groups)
    assert "Aethir" in config.RULE_IN_PENDING and ("Aethir", "rule_in_off_float_tokens") not in groups
    # both series held: their sum leaves the float
    groups.update({("Aethir", "ath_issuer_held_tokens"): _g("ath_issuer_held_tokens", [("2026-11-23", 4_243_169_739.01)]),
                   ("Aethir", "ath_issuer_inferred_held_tokens"): _g("ath_issuer_inferred_held_tokens",
                                                                     [("2026-11-23", 3_480_544_484.75)])})
    bw._rule_in_views(groups)
    off = bw._daily(groups[("Aethir", "rule_in_off_float_tokens")])
    assert off[pd.Timestamp("2026-11-23")] == pytest.approx(7_723_714_223.76, abs=0.01)
    assert "Aethir" not in config.RULE_IN_PENDING
    config.RULE_IN_PENDING.clear()


def test_the_wallet_list_carries_its_evidence_and_keeps_inferred_apart():
    tags = [w["tag"] for w in SPEC["wallets"]]
    assert tags == ["DIRECT", "DIRECT", "INFERRED", "INFERRED"]
    assert all(w["source"] and w["address"].startswith("0x") and len(w["address"]) == 42 for w in SPEC["wallets"])
    assert all(w["source"].startswith("INFERRED") for w in SPEC["wallets"] if w["tag"] == "INFERRED")
    assert len({w["address"].lower() for w in SPEC["wallets"]} | {v["address"].lower() for v in SPEC["vesters"]}) == 7
    # an exchange-like wallet examined and left out stays out
    assert not {a.lower() for a in SPEC["candidates_not_included"]} & {w["address"].lower() for w in SPEC["wallets"]}
    assert sum(w["read"] for w in SPEC["wallets"] if w["tag"] == "INFERRED") == pytest.approx(3_480_544_484.75)


def test_the_insider_share_is_the_issuer_locks_over_total_stake_a_measured_zero():
    num, den, _why = config.INSIDER_LOCK["Aethir"]
    assert (num, den) == ("ath_insider_staked_tokens", "locked_tokens")
    assert "Aethir" not in config.INSIDER_SHARE_ROUTES and "Aethir" in config.INSIDER_SHARE_ROUTE_CLOSED
    assert SPEC["stake"]["read"]["locked"] == 0.0
    # the eATH the issuer holds is beside the share, not in it: 22.0% of the pre-deposit vault
    rd = SPEC["stake"]["read"]
    assert rd["eath"] / rd["vault_total_deposited"] == pytest.approx(0.2197, abs=0.0005)
    assert set(SPEC["stake"]["pools"]) == {"ai_pool", "gaming_pool"}


def test_no_pool_is_fee_funded_today_and_the_uniformity_text_no_longer_says_nothing_is_paid():
    pools = config.AETHIR_STAKING_POOLS["pools"]
    assert set(pools) == {"ai_pool", "gaming_pool", "eigenlayer_ath_vault", "cloud_host_idc", "edge", "checker_nodes"}
    assert pools["ai_pool"]["funded_from"].startswith("EMISSIONS") and pools["gaming_pool"]["funded_from"].startswith(
        "EMISSIONS")
    assert "NOT ACTIVE" in pools["eigenlayer_ath_vault"]["pays"]
    # the four pools of Aethir's totalStaked, as read: 1,799,648,101.23
    assert sum(pools[k]["staked"] for k in ("ai_pool", "gaming_pool", "cloud_host_idc", "edge")) == pytest.approx(
        1_799_648_101.23)
    txt = config.declared_na_text("Aethir", "UNIFORMITY = whole-token yield ÷ staker yield")
    assert txt and "nothing is paid to stakers" not in txt
