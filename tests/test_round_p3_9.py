"""PHASE P3-9 (SPEC P3-A1, P3-A3, P3-A5, P3-A6; Jake's 2026-11-02 live run).

A1 MONTHLY SERIES WINDOWING: a monthly series is windowed on its LAST COMPLETE PUBLISHED months — Q0 = the latest 3,
"3 months to <month end>"; an unpublished month is publication lag, never a hole; STALE only more than 45 days past
the newest month's end; rule 9 sets a daily leg against a monthly one over the monthly series' own months.
A3 ETHEREUM VALIDATOR SOURCE: staked ETH from the beacon chain's own state, handed over from validatorqueue only after a
7-day overlap check (fake beacon answers; since P4-Q4 the route is beaconcha.in v2's epoch endpoint).
A5 MORPHO API: the liquidation query asks for price{ usd } (blue-api renamed priceUsd).
A6 CONCENTRATION CAVEAT: 2 or fewer days carrying more than 30% of a flow window turn the row AMBER with
"x% of this window from <dates>" — never the value.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402


def _row(project, metric, d, v, src, tier=3):
    return {"date": pd.Timestamp(d), "project": project, "metric": metric, "value": float(v), "source": src,
            "tier": tier, "is_manual": False, "entered_on": ""}


def _agg(rows, asof):
    return {r["key"]: r for r in bw.aggregate(pd.DataFrame(rows), pd.DataFrame(), pd.Timestamp(asof),
                                              gaps=pd.DataFrame(), review=pd.DataFrame()).to_dict("records")}


@pytest.fixture(autouse=True)
def _all_projects(monkeypatch):
    monkeypatch.setattr(bw, "_SCOPE", list(bw.PROJECTS))      # never depend on another test's narrowed build


# ============================================================================================ A1 monthly windows
MONTH_ENDS = ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31", "2026-06-30", "2026-07-31",
              "2026-08-31"]


def _maple_buyback(months=MONTH_ENDS, v=100.0):
    return [_row("Maple", "actual_buyback_tokens", d, v, "maple_page") for d in months]


def test_a1_unpublished_month_is_lag_not_a_hole_q0_is_the_latest_3_published():
    """13 October: September is not on Maple's page yet. Q0 = Jun-Aug (the latest 3 published), 92 days covered —
    before P3-A1 it was Jul-Sep, 62 days, and the A3 net absorption guard BLOCKED it."""
    assert config.series_granularity("Maple", "actual_buyback_tokens") == "monthly"   # the flag reused, not a new one
    r = _agg(_maple_buyback(), "2026-10-13")["Maple|actual_buyback_tokens"]
    assert r["q0"] == 300.0 and r["q0_covered_days"] == 92
    assert r["q0_basis"].startswith("3 months to 2026-08-31 (3 complete months Jun 2026–Aug 2026)"), r["q0_basis"]
    assert "the latest 3 published months" in r["q0_basis"]
    assert r["q1"] == 300.0 and r["q1_covered_days"] == 92            # Mar-May: the 3 months before
    assert r["status"] == "ok" and bw.MONTHLY_ANCHOR[("Maple", "actual_buyback_tokens")] == pd.Period("2026-08", "M")


def test_a1_a_missing_month_inside_the_published_window_is_a_hole():
    """July missing while June and August are published: a HOLE (61 of 92 days), not lag."""
    months = [m for m in MONTH_ENDS if not m.startswith("2026-07")]
    r = _agg(_maple_buyback(months), "2026-10-13")["Maple|actual_buyback_tokens"]
    assert r["q0"] == 200.0 and r["q0_covered_days"] == 30 + 31


def test_a1_stale_only_more_than_45_days_past_the_month_end():
    """August ends 08-31: 45 days later (10-15) the series is current, 46 days later (10-16) STALE."""
    ok = _agg(_maple_buyback(), "2026-10-15")["Maple|actual_buyback_tokens"]
    stale = _agg(_maple_buyback(), "2026-10-16")["Maple|actual_buyback_tokens"]
    assert ok["status"] == "ok", ok["note"]
    assert stale["status"] == "stale" and "monthly series, stale after 45 days" in stale["note"], stale["note"]
    # both still windowed on the published months — staleness, never a shorter window
    assert ok["q0"] == stale["q0"] == 300.0 and stale["q0_covered_days"] == 92


def test_a1_current_month_never_counts_and_a_published_month_moves_the_window():
    """Once September is published Q0 is Jul-Sep; a row dated in the current month never anchors the window."""
    rows = _maple_buyback(MONTH_ENDS + ["2026-09-30", "2026-10-31"])
    r = _agg(rows, "2026-10-13")["Maple|actual_buyback_tokens"]
    assert r["q0_basis"].startswith("3 months to 2026-09-30") and r["q0"] == 300.0


def test_a1_split_views_of_a_monthly_series_window_on_its_months():
    """Maple's retired buyback (derived:actual_buyback_tokens x share) holds the input's month rows: same window."""
    rows = _maple_buyback() + [_row("Maple", "retired_buyback_tokens", d, 100.0,
                                    "derived:actual_buyback_tokens x burn+hold share (from start)") for d in MONTH_ENDS]
    g = pd.DataFrame(rows[-3:])
    assert bw._effective_granularity("Maple", "retired_buyback_tokens", g) == "monthly"
    assert bw._effective_granularity("Maple", "fees_usd", pd.DataFrame([_row("Maple", "fees_usd", "2026-08-01", 1,
                                                                             "defillama")])) == "daily"


def test_a1_rule_9_daily_leg_over_the_monthly_series_own_months():
    """A daily flow set against Maple's buyback is summed over Jun-Aug (the buyback's own window), in the Data row
    `Maple|fees_usd@2026-08` and in the guard's expression and coverage test."""
    asof = "2026-10-13"
    rows = _maple_buyback() + [_row("Maple", "fees_usd", d, 2.0, "explorer:test")
                               for d in pd.date_range("2026-01-01", "2026-10-12")]
    by = _agg(rows, asof)
    assert by["Maple|fees_usd"]["q0"] == 2.0 * 89     # its own trailing 90 days (rows to 10-12), unchanged
    mw = bw.MONTH_WINDOW_ROWS["Maple|fees_usd@2026-08"]
    assert mw["q0"] == 2.0 * 92 and mw["q0_covered_days"] == 92 and mw["q1"] == 2.0 * 92
    assert mw["q0_basis"].startswith("3 months to 2026-08-31")
    R = bw.Refs(100, 0, [])
    with bw._project_row("Maple"):
        expr = f"{R.D(5, 'actual_buyback_tokens')}-{R.D(5, 'fees_usd')}"
        f = bw._coverage_guard(R, 5, [("buyback", "actual_buyback_tokens", "q0"), ("fees", "fees_usd", "q0")], expr)
    assert '"|fees_usd@2026-08"' in f and '"|fees_usd",' not in f, f
    assert '"|actual_buyback_tokens"' in f
    # the trajectory's shifted quarter reads the same row's earlier block
    with bw._project_row("Maple"), bw.shifted(1):
        f1 = bw._coverage_guard(R, 5, [("buyback", "actual_buyback_tokens", "q0"), ("fees", "fees_usd", "q0")],
                                f"{R.D(5, 'actual_buyback_tokens')}-{R.D(5, 'fees_usd')}")
    assert '"|fees_usd@2026-08"' in f1
    # a guard with no monthly leg is untouched
    with bw._project_row("Maple"):
        g = bw._coverage_guard(R, 5, [("fees", "fees_usd", "q0"), ("rev", "revenue_usd_defillama", "q0")], "1")
    assert "@" not in g


# ============================================================================================ A6 concentration
def _daily(project, metric, base, spikes, asof="2026-10-13", days=120, src="defillama"):
    end = pd.Timestamp(asof) - pd.Timedelta(days=1)
    rows = [_row(project, metric, d, base, src) for d in pd.date_range(end - pd.Timedelta(days=days - 1), end)]
    for d, v in spikes.items():
        for r in rows:
            if r["date"] == pd.Timestamp(d):
                r["value"] = float(v)
    return rows


def test_a6_two_days_over_30_percent_turn_amber_with_the_exact_comment_value_unchanged():
    spikes = {"2026-08-20": 30.0, "2026-08-21": 30.0}          # inside Q0, outside the 30-day Now window
    rows = _daily("Uniswap", "fees_usd", 1.0, spikes, src="explorer:test")
    r = _agg(rows, "2026-10-13")["Uniswap|fees_usd"]
    total = 87 * 1.0 + 60.0                                   # 89 days held in Q0
    assert r["q0"] == pytest.approx(total)                                        # the value is the plain sum
    want = (f"CONCENTRATED — Q0 (trailing 90 days to 2026-10-13): {60 / total:.1%} of this window from "
            f"2026-08-20 and 2026-08-21")
    assert want == "CONCENTRATED — Q0 (trailing 90 days to 2026-10-13): 40.8% of this window from 2026-08-20 and 2026-08-21"
    assert r["confidence"] == "AMBER" and want in r["why_amber"], r["why_amber"]
    assert bw.CONCENTRATION[("Uniswap", "fees_usd")].startswith("Q0 (trailing 90 days")


def test_a6_at_or_under_30_percent_is_unchanged():
    rows = _daily("Uniswap", "fees_usd", 1.0, {"2026-08-20": 10.0, "2026-08-21": 10.0}, src="explorer:test")
    r = _agg(rows, "2026-10-13")["Uniswap|fees_usd"]
    assert "CONCENTRATED" not in (r["why_amber"] or "") and ("Uniswap", "fees_usd") not in bw.CONCENTRATION
    s = pd.Series([1.0] * 7 + [1.5, 1.5], index=pd.date_range("2026-10-01", periods=9))
    assert bw.concentration(s, pd.Timestamp("2026-09-30"), pd.Timestamp("2026-10-09")) == ""    # 3/10 = 30%: not over


def test_a6_three_days_making_31_percent_is_unchanged_it_needs_two_or_fewer():
    s = pd.Series([1.0] * 87 + [13.5, 13.5, 13.5], index=pd.date_range("2026-07-01", periods=90))
    lo, hi = pd.Timestamp("2026-06-30"), pd.Timestamp("2026-09-28")
    assert 40.5 / 127.5 > 0.31 and 27.0 / 127.5 < 0.30
    assert bw.concentration(s, lo, hi) == ""
    assert bw.concentration(s.drop(s.index[-1]).copy(), lo, hi).startswith("23.7%") is False   # 27/114 = 23.7%: under


def test_a6_negative_flows_use_absolute_values_and_say_so():
    s = pd.Series([1.0] * 18 + [-10.0, 5.0], index=pd.date_range("2026-10-01", periods=20))
    txt = bw.concentration(s, pd.Timestamp("2026-09-30"), pd.Timestamp("2026-10-20"), "Now")
    assert txt == ("Now: 45.5% of this window from 2026-10-19 and 2026-10-20 "
                   "(absolute values: the window holds negative days)"), txt


def test_a6_monthly_series_are_not_judged_by_days():
    r = _agg(_maple_buyback(), "2026-10-13")["Maple|actual_buyback_tokens"]
    assert "CONCENTRATED" not in (r["why_amber"] or "")


# ============================================================================================ A3 validator source
class _BeaconHttp:
    """beaconcha.in v2 epoch answers (POST, total_validator_balance in wei, validators_count) — P4-Q4 replaced P3-A3's
    v1 eligibleether / keyless Lighthouse routes."""
    def __init__(self, staked_eth=44_100_000, fail_beaconchain=False):
        self.staked, self.fail = staked_eth, fail_beaconchain
        self.calls = []

    def post(self, url, json_body=None, headers=None, deadline=None):
        self.calls.append((url, dict(headers or {}), json_body))
        if self.fail:
            raise RuntimeError("HTTP 401")
        from fetch import staked_eth as se
        ep = json_body["epoch"]["number"]
        return {"data": {"epoch": ep, "timestamp": se.GENESIS_UNIX + ep * se.EPOCH_SECONDS, "validators_count": 950_000,
                         "total_validator_balance": str(self.staked * 10**18), "finality": "finalized"}}


class _Daily:
    def __init__(self, ever=False):
        self._ever = ever

    def due(self, key, day):
        return True

    def ever(self, key):
        return self._ever

    def done(self, key, day):
        pass


def test_a3_keyless_stores_nothing_and_says_so(monkeypatch):
    """P4-Q4: the keyless beacon-node route is withdrawn (another quantity) — no key, no call, nothing stored, said."""
    from fetch.base import FetchOutput
    from fetch import staked_eth as se
    monkeypatch.setattr(se, "today", lambda: pd.Timestamp("2026-11-02 12:00"))
    a = se.BeaconStakedEth(http=_BeaconHttp(), daily=_Daily(), key="")
    out = FetchOutput()
    a.run([config.PROJECT_BY_NAME["Ethereum"]], 30, out)
    assert out.frame().empty and not a.http.calls, "no key: nothing is called, nothing stored"
    assert any("no BEACONCHAIN_API_KEY" in e.message for e in out.log)


def test_a3_with_the_key_reads_each_days_first_epoch_and_never_puts_the_key_in_a_url(monkeypatch):
    from fetch.base import FetchOutput
    from fetch import staked_eth as se
    monkeypatch.setattr(se, "today", lambda: pd.Timestamp("2026-11-02 12:00"))
    a = se.BeaconStakedEth(http=_BeaconHttp(), daily=_Daily(ever=False), key="SECRETKEY123")
    out = FetchOutput()
    a.run([config.PROJECT_BY_NAME["Ethereum"]], 30, out)
    f = out.frame()
    st = f[f["metric"] == "beacon_chain_eth_primary"].sort_values("date")
    assert len(st) == 455 and pd.Timestamp(st["date"].max()) == pd.Timestamp("2026-11-02")
    assert set(st["source"]) == {"beaconchain:v2_epoch.total_validator_balance"}
    assert all("SECRETKEY123" not in u and "SECRETKEY123" not in str(b) for u, _, b in a.http.calls)
    assert all(h.get("Authorization") == "Bearer SECRETKEY123" for _, h, _ in a.http.calls)
    assert all("SECRETKEY123" not in e.message for e in out.log)
    assert se.day_of_epoch(se.epoch_at(pd.Timestamp("2026-10-19"))) == pd.Timestamp("2026-10-19")
    # a refused key stores nothing (no keyless fallback any more), and says why without the key
    b = se.BeaconStakedEth(http=_BeaconHttp(fail_beaconchain=True), daily=_Daily(), key="SECRETKEY123")
    out2 = FetchOutput()
    b.run([config.PROJECT_BY_NAME["Ethereum"]], 30, out2)
    assert out2.frame().empty and len(b.http.calls) == 1
    assert any("HTTP 401" in e.message for e in out2.log) and all("SECRETKEY123" not in e.message for e in out2.log)


def _eth_rows(vq_days, vq_val, prim_days, prim_val, prim_src=None):
    # P5-1.12: the next leg's rows are the BigQuery rebuild's; the overlap is its 455 days at ±1%
    prim_src = prim_src or config.STAKED_REBUILD_POINT + "[test]"
    rows = [_row("Ethereum", "beacon_chain_eth", d, vq_val, "validatorqueue:staked_amount", 1) for d in vq_days]
    rows += [_row("Ethereum", "beacon_chain_eth_primary", d, prim_val, prim_src, 1) for d in prim_days]
    return rows


def test_a3_handover_made_after_the_455_day_overlap_within_1_percent():
    vq = pd.date_range("2025-07-01", "2026-10-25")
    prim = pd.date_range("2025-07-28", "2026-11-01")                    # 455 common days: 2025-07-28..2026-10-25
    by = _agg(_eth_rows(vq, 43_700_000.0, prim, 44_100_000.0), "2026-11-02")
    r = by["Ethereum|beacon_chain_eth"]
    assert r["latest_date"] == "2026-11-01" and r["now"] == pytest.approx(44_100_000.0)
    assert r["source"] == "derived:=beacon_chain_eth_primary"
    assert ("Ethereum", "beacon_chain_eth") in bw.HANDOVER_CONTINUED
    ho = bw.HANDOVER_OVERLAP[("Ethereum", "beacon_chain_eth")]
    assert ho["passed"] and ho["common_days"] == 455 and "OVERLAP CHECK PASSED" in ho["text"]
    assert config.declared_handover("Ethereum", "beacon_chain_eth")["ordered_points"][-1] == \
        "derived:=beacon_chain_eth_primary"


@pytest.mark.parametrize("prim_days,prim_val,why", [
    (pd.date_range("2025-07-29", "2026-11-01"), 44_100_000.0, "454 common day(s)"),        # one day short of 455
    (pd.date_range("2025-07-28", "2026-11-01"), 46_000_000.0, "+5.26%"),                     # 455 days, outside ±1%
    (pd.date_range("2026-11-02", "2026-11-02"), 44_233_406.0, "share no stored day"),        # no overlap day read
])
def test_a3_handover_not_made_when_the_overlap_fails(prim_days, prim_val, why):
    vq = pd.date_range("2025-07-01", "2026-10-25")
    by = _agg(_eth_rows(vq, 43_700_000.0, prim_days, prim_val), "2026-11-02")
    r = by["Ethereum|beacon_chain_eth"]
    assert r["latest_date"] == "2026-10-25" and r["source"] == "validatorqueue:staked_amount"   # never switched blind
    held = bw.HANDOVER_HELD[("Ethereum", "beacon_chain_eth")]
    assert held.startswith("NOT HANDED OVER to beacon_chain_eth_primary") and why in held, held
    assert held in r["why_amber"]


def test_a3_no_primary_series_stored_says_nothing():
    by = _agg(_eth_rows(pd.date_range("2026-10-01", "2026-10-12"), 43_700_000.0, [], 0.0), "2026-10-13")
    assert ("Ethereum", "beacon_chain_eth") not in bw.HANDOVER_OVERLAP
    assert "OVERLAP" not in (by["Ethereum|beacon_chain_eth"]["why_amber"] or "")


# ============================================================================================ A5 Morpho price field
class _MorphoPriceHttp:
    def __init__(self):
        self.bodies = []

    def post(self, endpoint, json_body=None):
        self.bodies.append(json_body)
        return {"data": {"marketTransactions": {"pageInfo": {"countTotal": 2}, "items": [
            {"market": {"marketId": "0xa", "listed": True, "lltv": "860000000000000000",
                        "collateralAsset": {"decimals": 18, "price": {"usd": 2268.837208217646,
                                                                      "timestamp": 1793639340}}},
             "data": {"seizedAssets": "39659064731168765"}},
            {"market": {"marketId": "0xb", "listed": True, "lltv": "915000000000000000",
                        "collateralAsset": {"decimals": 18, "price": None}},
             "data": {"seizedAssets": "1365264024968098360"}}]}}}


def test_a5_liquidation_query_asks_for_price_usd_and_parses_it():
    from fetch.base import FetchOutput
    from fetch.llama import MorphoBlueApi, _collateral_usd
    api = dict(config.PROJECT_BY_NAME["Morpho"]["lending_api"])
    q = api["liquidations"]["query"]
    assert "priceUsd" not in q and "collateralAsset{ decimals price{ usd } }" in q
    assert _collateral_usd({"price": {"usd": 1.5, "timestamp": 1}}) == 1.5
    assert _collateral_usd({"price": None}) is None and _collateral_usd({}) is None     # unpriced: None, never 0
    a = MorphoBlueApi()
    a.http = _MorphoPriceHttp()
    out = FetchOutput()
    a._liquidations(dict(api, liquidations=dict(api["liquidations"], days=1)), [1, 8453], "Morpho",
                    pd.Timestamp("2026-11-02"), out)
    lif = min(1.15, 1 / (0.3 * 0.86 + 0.7))
    assert out.frame()["value"].iloc[0] == pytest.approx(0.039659064731168765 * (1 - 1 / lif) * 2268.837208217646)
    assert "1 with no collateral price left out" in out.log[-1].message
    assert "price{ usd }" in a.http.bodies[0]["query"]


# ============================================================================================ A1 on the real Maple dump
def test_a1_maple_net_absorption_comes_back_on_the_real_dump(tmp_path, monkeypatch):
    """fixtures/real_maple.json (asof 2026-10-13): September is not on the page yet. Before P3-A1 the Q0 block was
    Jul-Sep (62 days) against 90 days of emissions — both net absorption cells BLOCKED. Now Q0 is Jun-Aug (the latest 3
    published months): 2,500,000 + 852,840.70 + 676,293.73 = 4,029,134.43 retired, emissions over the same months."""
    import openpyxl
    import xlcalc
    import store as sm
    from real_dump import load, to_store
    payload = load("Maple")
    monkeypatch.setitem(sys.modules, "recalc", None)
    db = tmp_path / "metrics.db"
    to_store(payload, db)
    monkeypatch.setattr(sm, "DB_PATH", str(db))
    out = tmp_path / "wb.xlsx"
    bw.build_workbook(sm.Store(str(db)), out, asof=pd.Timestamp("2026-10-13"), only=["Maple"])
    wb = openpyxl.load_workbook(out)
    X = xlcalc.Workbook(wb)
    ws = wb["A3 Revenue Buyback"]
    hr = next(r for r in range(1, 12) if ws.cell(r, 1).value == "Project")
    row = next(r for r in range(hr + 1, ws.max_row + 1) if ws.cell(r, 1).value == "Maple")

    def cell(prefix):
        col = next(c for c in range(1, ws.max_column + 1) if str(ws.cell(hr, c).value or "").startswith(prefix))
        return X.value(ws.title, row, col)
    assert cell("Net absorption Q0 (tokens)") == pytest.approx(4_029_134.43, abs=0.01)
    assert isinstance(cell("Net absorption incl. unlocks Q0"), float)
    data = {r[0].value: r for r in wb["Data"].iter_rows(min_row=2)}
    basis = data["Maple|actual_buyback_tokens"][bw.DATA_COLS.index("q0_basis")].value
    assert basis.startswith("3 months to 2026-08-31 (3 complete months Jun 2026–Aug 2026)"), basis
    assert any(str(k).startswith("Maple|emissions_tokens@2026-08") for k in data), "rule 9: emissions over Jun-Aug"
