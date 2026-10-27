"""ROUND P2a D8/D9 (Jake 2026-10-27).

D8: every new config.REVIEW_ANSWERED entry matches its row — the rows are rebuilt from the PRIMARY figures read on
2026-10-27 through the same checks that raise them (fetch._derive_lock_ratio's rise guard, fetch.validate's change
check), and the fixture build's own queue rows are answered with their stored numbers in the decision. A FIX, not an
answer: the Plume payout routine stored the day its logs cursor stopped inside as if complete (the fixture's 2025-08-03,
384.81 of 757.28 PLUME) — a day is now stored only once a read has passed it.
D9: the Plume settlement state file's two legs, kept apart (check_offline_items.plume_native_status).
No network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

# Daily sums read 2026-10-27 (scratch reads of the same events the adapters read; see SPEC P2a-D8)
PLUME = {"2026-10-13": 1221.417488, "2026-10-14": 5848.015742, "2026-10-15": 12499.198977, "2026-10-16": 506.956977,
         "2026-10-17": 1364.493481, "2026-10-18": 1222.020919, "2026-10-19": 1386.909243, "2026-10-20": 214146.393598,
         "2026-10-21": 678.42432, "2026-10-22": 2136.961375, "2026-10-23": 745.962986, "2026-10-24": 1105.555325,
         "2026-10-25": 1527.05092, "2026-10-26": 2079.128459}
FLUID_ETH = {"2026-10-13": 86.890191, "2026-10-14": 413.555511, "2026-10-15": 0.692621, "2026-10-16": 87.246279,
             "2026-10-17": 200.172289, "2026-10-18": 203.166535, "2026-10-19": 2.777293, "2026-10-20": 3416.99052,
             "2026-10-21": 0.0, "2026-10-22": 289.786195, "2026-10-23": 40.812322, "2026-10-24": 819.62668,
             "2026-10-25": 35.457794, "2026-10-26": 209.353831}
FLUID_ARB = {"2026-10-13": 6.282176, "2026-10-14": 7.328568, "2026-10-15": 13.057955, "2026-10-16": 12.067778,
             "2026-10-17": 7.253338, "2026-10-18": 1.074107, "2026-10-19": 0.244746, "2026-10-20": 5.922311,
             "2026-10-21": 54.371848, "2026-10-22": 0.0, "2026-10-23": 150.616089, "2026-10-24": 0.701339,
             "2026-10-25": 3.682607, "2026-10-26": 559.765164}


def _change_rows(project, metric, series, source):
    from fetch.base import FetchOutput
    from fetch.validate import validate_frame
    df = pd.DataFrame({"date": pd.to_datetime(list(series)), "project": project, "metric": metric,
                       "value": list(series.values()), "source": source, "tier": 1})
    out = FetchOutput()
    validate_frame(df, {}, out)
    return [r for r in out.review if (r["project"], r["metric"]) == (project, metric)]


@pytest.mark.parametrize("project,metric,series,source,cur,prior", [
    ("Plume", "emissions_claimed_tokens", PLUME, "plume_staking:treasury.RewardDistributed[paid, PLUME]",
     "222,419.48", "24,049.01"),
    ("Fluid", "emissions_tokens", FLUID_ETH, "explorer:merkle_rewards_out", "4,812.03", "994.50"),
    ("Fluid", "emissions_tokens_arbitrum", FLUID_ARB, "explorer:merkle_rewards_out_arbitrum", "775.06", "47.31"),
])
def test_d8_each_lumpy_flow_row_is_the_row_its_entry_answers(project, metric, series, source, cur, prior):
    rows = _change_rows(project, metric, series, source)
    assert len(rows) == 1, rows
    r = rows[0]
    assert (r["reason"], str(r["date"])[:10]) == ("change_threshold", "2026-10-26")
    assert f"{r['value']:,.2f}" == cur and f"{r['prior_value']:,.2f}" == prior, (r["value"], r["prior_value"])
    a = config.review_answered({**r, "date": str(r["date"])[:10]}, on="2026-10-27")
    assert a and a["decision"] == "ACCEPT" and cur in a["why"] and prior in a["why"]
    # that row's date only
    assert config.review_answered({**r, "date": "2026-10-25"}, on="2026-10-27") is None


def test_d8_pendle_rise_is_the_row_its_entry_answers():
    from fetch import _derive_lock_ratio
    from fetch.base import LONG_COLUMNS, FetchOutput
    out = FetchOutput()
    out.add(pd.DataFrame([
        {"date": pd.Timestamp("2026-10-27"), "project": "Pendle", "metric": "locked_tokens", "value": 30_323_989.49,
         "source": "chain:ethereum:spendle_underlying", "tier": 2},
        {"date": pd.Timestamp("2026-10-27"), "project": "Pendle", "metric": "locked_tokens_shares",
         "value": 28_551_138.40, "source": "chain:ethereum:spendle", "tier": 2}])[LONG_COLUMNS], "test", "Pendle", "", 2)
    key = ("Pendle", "lock_assets_per_share")
    prior = 30_323_790.19 / 28_858_541.90                    # 1.050773, block 26,284,536
    _derive_lock_ratio(out, [config.PROJECT_BY_NAME["Pendle"]], {key: prior}, {key: prior},
                       {key: pd.Timestamp("2026-10-26")})
    rows = [r for r in out.review if r["metric"] == "lock_assets_per_share"]
    assert len(rows) == 1 and rows[0]["reason"] == "accrued_rate_implausible", out.review
    r = rows[0]
    assert str(r["date"])[:10] == "2026-10-27"
    assert f"{r['value']:.6f}" == "1.062094" and f"{r['prior_value']:.6f}" == "1.050773"
    assert round((r["value"] / r["prior_value"] - 1) * 100, 2) == 1.08
    a = config.review_answered({**r, "date": "2026-10-27"}, on="2026-10-27")
    assert a and a["decision"] == "ACCEPT" and "1.050773" in a["why"] and "1.062094" in a["why"]
    assert "0x565b3217e8190c15e6faf5d66e6402a6bc6db636779c4fe4f77039c0c5138db9" in a["why"]


def test_d8_the_fixture_builds_queue_rows_are_answered_with_their_own_numbers():
    rows = []
    for f in sorted((ROOT / "fixtures").glob("real_*.json")):
        rows += json.loads(f.read_text(encoding="utf-8")).get("review_queue") or []
    last = max(rows, key=lambda r: r["ts"])["run_id"]
    q = {(r["project"], r["metric"], r["reason"]): r for r in rows if r["run_id"] == last}
    want = {("Chainlink", "emissions_claimed_tokens"): ("{:,.2f}", "{:,.2f}"),
            ("Pendle", "fees_usd"): ("{:,.0f}", "{:,.0f}"), ("Pendle", "revenue_usd"): ("{:,.0f}", "{:,.0f}"),
            ("Pendle", "holders_revenue_usd"): ("{:,.0f}", "{:,.0f}"),
            ("Plume", "volume_usd"): ("{:,.0f}", "{:,.0f}"), ("Maple", "volume_usd"): ("{:,.0f}", "{:,.0f}"),
            ("GEODNET", "volume_usd"): ("{:,.0f}", "{:,.0f}")}
    for (p, m), (fv, fp) in want.items():
        r = q[(p, m, "change_threshold")]
        assert str(r["date"])[:10] == "2026-10-12"
        a = config.review_answered(r, on="2026-10-27")
        assert a and a["decision"] == "ACCEPT", (p, m)
        assert fv.format(r["value"]) in a["why"] and fp.format(r["prior_value"]) in a["why"], (p, m, a["why"])


def test_d8_no_new_entry_is_a_fix_and_each_is_dated():
    new = [a for a in config.REVIEW_ANSWERED if a.get("date") in ("2026-10-26", "2026-10-27", "2026-10-12")]
    assert len(new) == 11
    assert all(a["decision"] in ("ACCEPT", "REJECT") and not a.get("until") for a in new)


# ---- the FIX: a payout day is stored only once a read has passed it --------------------------------------------
def _staking(tmp_path, pages, calls_per_run):
    from fetch.plume_staking import PlumeStaking
    spec = config.PROJECT_BY_NAME["Plume"]["plume_staking"]
    spec = {**spec, "payouts": {**spec["payouts"], "page_cap": 3, "max_calls_per_run": calls_per_run,
                                "rate_per_s": 1000}}

    class C:
        def call(self, block_identifier=None):
            return "0xTreasury"

    class F:
        functions = None

        def getTreasury(self):
            return C()
    F.functions = F()

    class Http:
        def get(self, url, params=None):
            return {"result": pages(params["fromBlock"])}
    return PlumeStaking(contract_factory=lambda sp: F.functions, http=Http(), cache_file=tmp_path / "p.json"), spec


def test_d8_fix_the_day_the_payout_cursor_stops_inside_is_held_not_stored(tmp_path):
    from fetch.base import FetchOutput, today
    d1, d2 = today() - pd.Timedelta(days=3), today() - pd.Timedelta(days=2)

    def log_(block, day, amt):
        return {"blockNumber": hex(block), "timeStamp": hex(int(day.timestamp()) + 3600), "data": hex(int(amt * 1e18))}

    def pages(fb):
        if fb == 0:          # FULL (cap 3): block 12 is the edge — d2 is read only up to it
            return [log_(10, d1, 995.31), log_(11, d2, 384.81), log_(12, d2, 100.0)]
        if fb == 12:
            return [log_(12, d2, 100.0), log_(13, d2, 272.47)]
        return []
    ps, spec = _staking(tmp_path, pages, 1)
    out = FetchOutput()
    ps._payouts("Plume", spec, out)
    s = out.frame().set_index("date")["value"]
    assert s[d1] == pytest.approx(995.31) and d2 not in s.index, "the partial day must not be stored"
    assert any("HELD, not stored" in e.message for e in out.log)
    assert json.loads((tmp_path / "p.json").read_text())["covered_to"] == str(d2.date())
    out = FetchOutput()
    ps._payouts("Plume", spec, out)      # the next run passes it
    s = out.frame().set_index("date")["value"]
    assert s[d2] == pytest.approx(384.81 + 100.0 + 272.47)
    assert json.loads((tmp_path / "p.json").read_text())["covered_to"] == str(today().date())


def test_d8_fix_the_rpc_seed_stores_only_days_before_the_heads_day(tmp_path):
    from fetch.base import FetchOutput, today
    from fetch.plume_staking import PlumeStaking
    spec = config.PROJECT_BY_NAME["Plume"]["plume_staking"]
    d1, d2 = today() - pd.Timedelta(days=2), today() - pd.Timedelta(days=1)
    stamp = {100: d1, 110: d2, 120: d2}

    class C:
        def call(self, block_identifier=None):
            return "0x14789d64465f0f5521593e58cb120724bdf7d2cf"

    class F:
        def getTreasury(self):
            return C()

    class Eth:
        block_number = 140                                   # head = 120, on d2

        def get_logs(self, q):
            return [{"blockNumber": b, "data": bytes.fromhex(f"{int(5e18):064x}")} for b in (100, 110)
                    if q["fromBlock"] <= b <= q["toBlock"]]

        def get_block(self, b):
            return {"timestamp": int(stamp.get(b, d2).timestamp()) + 60}

    class W3:
        eth = Eth()
    (tmp_path / "p.json").write_text(json.dumps({"treasury": "0x14789d64465f0f5521593e58cb120724bdf7d2cf",
                                                 "next_block": 90, "days": {}}))
    ps = PlumeStaking(contract_factory=lambda sp: type("X", (), {"functions": F()})(), cache_file=tmp_path / "p.json")
    out = FetchOutput()
    ps.seed_payouts_rpc("Plume", spec, out, say=lambda m: None, w3=W3())
    s = out.frame().set_index("date")["value"]
    assert list(s.index) == [d1], "the head's own day is held"
    assert json.loads((tmp_path / "p.json").read_text())["covered_to"] == str(d2.date())


# ---- D9 ---------------------------------------------------------------------------------------------------------
def test_d9_the_erc20_floor_says_nothing_about_the_native_seed_window():
    import check_offline_items as coi
    window = [str(d.date()) for d in pd.date_range("2025-10-27", "2026-03-31")]
    st = {"erc20": {"done_back": True, "oldest": "2025-10-02"},
          "logs": {"plan": {"floor": "2025-10-02"}, "completed_on": "2026-10-02"},
          "native": {"days": {d: 1.0 for d in window if d >= "2026-02-12"} | {"2026-04-01": 1.0},
                     "capped": {d: 400 for d in window if "2026-02-12" <= d} | {"2025-12-01": 1200}}}
    s = coi.plume_native_status(st, "2026-10-27")
    assert s["erc20_done_back"] and s["erc20_plan_floor"] == "2025-10-02" and s["daily_floor"] == "2025-10-27"
    assert s["seed_window"] == ("2025-10-27", "2026-03-31") and s["days"] == 156
    assert s["held"] == 48 and s["refused_at_cap"] == ["2025-12-01"] and s["restart"]
    assert len(s["unread"]) == 156 - 48 - 1 and not s["retry"]
    # held days win over an old refusal; a refusal below the seed's cap is retried
    st["native"]["days"] = {d: 1.0 for d in window if d != "2026-01-05"}
    st["native"]["capped"] = {"2026-01-05": 400}
    s = coi.plume_native_status(st, "2026-10-28")            # the floor has moved past 2025-10-27
    assert s["seed_window"][0] == "2025-10-28" and s["retry"] == ["2026-01-05"] and s["restart"]
    st["native"]["days"]["2026-01-05"] = 2.0
    assert coi.plume_native_status(st, "2026-10-28")["restart"] is False
