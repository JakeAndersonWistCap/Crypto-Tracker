"""Round 13 (Jake 2026-11-19): Uniswap's HOLDER SHARE read above 1 (holder revenue 34,285,990 over revenue 32,381,304,
1.0588). The two lines are the same UNI valued at two points; the share is read on one basis (UNI burned ÷ UNI
released, the same covered days) and is never capped. Behaviour and arithmetic. No network."""
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

N = "Uniswap"
ASOF = pd.Timestamp("2026-11-18")


def _g(metric, rows, source="chain"):
    return pd.DataFrame({"date": pd.to_datetime([d for d, _ in rows]), "project": N, "metric": metric,
                         "value": [float(v) for _, v in rows], "source": source, "tier": 2})


def _days(first, n):
    return [str(d.date()) for d in pd.date_range(first, periods=n)]


def _store(released, burned, prices, first="2026-11-01", rev_days=None, hold_days=None):
    """The three stored series as the build forms them: price_usd[d] = the 00:00 UTC price of d; revenue_usd[d] =
    released[d] x the price at the END of d (= price_usd[d+1], DefiLlama's basis); holder_value_revenue_usd[d] =
    burned[d] x price_usd[d] (the burn at the 00:00 price of its day)."""
    days = _days(first, len(prices))
    rev = [(days[i], released[i] * prices[i + 1]) for i in range(len(released))][:rev_days]
    hold = [(days[i], burned[i] * prices[i]) for i in range(len(burned))][:hold_days]
    return {(N, "price_usd"): _g("price_usd", list(zip(days, prices)), "coingecko"),
            (N, "revenue_usd"): _g("revenue_usd", rev, "defillama"),
            (N, "holder_value_revenue_usd"): _g("holder_value_revenue_usd", hold, "derived:holder_value[revenue-funded]")}


def _views(g, asof=ASOF):
    config.TOKENJAR_NOTE.pop(N, None)
    bw._s13_views(g)
    bw._s13_share_basis_views(g, asof)
    return bw.S13_STATE[N]


def _sum(g, m):
    return float(g[(N, m)]["value"].sum())


@pytest.fixture(autouse=True)
def _clean():
    yield
    bw.S13_STATE.clear()
    config.TOKENJAR_NOTE.pop(N, None)


# ============================================================================================ the declaration
def test_declared_uniswap_only_and_nothing_is_capped_or_loosened():
    assert config.S13_SAME_DAYS[N] == ("revenue", "holder_revenue")
    assert set(config.S13_SHARE_BASIS) == {N}                       # no other project's share changes basis
    spec = config.S13_SHARE_BASIS[N]
    assert spec["holder_revenue"]["price_day_offset"] == 0          # the burn: the 00:00 UTC price of its own day
    assert spec["revenue"]["price_day_offset"] == 1                 # DefiLlama: the price at the END of the release day
    assert spec["evidence"]["read_on"] and "uniswap-v3.ts" in spec["evidence"]["defillama_adapter"]
    for role in ("holder_revenue", "revenue"):
        m = config.METRICS[spec[role]["view"]]
        assert m["view_only"] and m["only_projects"] == (N,) and m["unit"] == "tokens" and m["kind"] == "flow"
    assert config.METRICS["revenue_usd_s13d"]["only_projects"] == (N,)
    assert N in config.METRICS["holder_value_revenue_usd_s13d"]["only_projects"]
    assert {"Hyperliquid", "Near"} <= set(config.METRICS["holder_value_revenue_usd_s13d"]["only_projects"])
    # the lines themselves are the same series as before: only the share's basis is declared
    split = config.REVENUE_SPLIT[N]
    assert split["revenue"]["metric"] == "revenue_usd" and split["holder_revenue"]["metric"] == "holder_value_revenue_usd"
    assert "UNI burned ÷ UNI released" in split["holder_share"]["route"] and split["holder_share"]["was"]["route"]
    assert "HOLDER SHARE" in config.PAYOUT_ABOVE_ONE[N] and "{live}" in config.PAYOUT_ABOVE_ONE[N]


# ======================================================================================== the same covered days
def test_the_two_lines_are_read_to_the_last_day_both_hold():
    """A case where the lines differ in coverage: the burn is stored a day further than DefiLlama's revenue."""
    px = [5.0, 5.0, 5.0, 5.0, 5.0, 5.0]
    g = _store([100, 100, 100, 100], [100, 100, 100, 100, 100], px, rev_days=4, hold_days=5)
    st = _views(g)
    assert st["same_days"] == ("revenue_usd", "holder_value_revenue_usd") and st["last"] == "2026-11-04"
    h, r = g[(N, "holder_value_revenue_usd_s13d")], g[(N, "revenue_usd_s13d")]
    assert len(h) == len(r) == 4 and pd.to_datetime(h["date"]).max() == pd.Timestamp("2026-11-04")
    assert bw.s13_metric(N, "revenue") == "revenue_usd_s13d"
    assert bw.s13_metric(N, "holder_revenue") == "holder_value_revenue_usd_s13d"
    assert bw.s13_metric(N, "fees") == "fees_usd"                         # a line not compared keeps its own days
    # the stored series are untouched; over their own days the $ quotient was 5 days ÷ 4 days = 1.25 — coverage only
    assert len(g[(N, "holder_value_revenue_usd")]) == 5
    assert _sum(g, "holder_value_revenue_usd") / _sum(g, "revenue_usd") == pytest.approx(1.25)
    assert _sum(g, "holder_value_revenue_usd_s13d") / _sum(g, "revenue_usd_s13d") == pytest.approx(1.0)
    assert st["share_basis"]["q0"]["share"] == pytest.approx(1.0) and st["share_basis"]["q0"]["days"] == (4, 4)
    assert "2026-11-04" in bw.s13_note({"name": N})


# ================================================================================================ the price basis
def test_each_line_is_turned_back_into_the_uni_it_valued():
    """The burn is valued at the 00:00 price of its day, a release at the price at its day's end: with the same UNI
    released and burned every day and a rising price the $ lines disagree and the share in UNI is exactly 1."""
    px = [4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    released = [2000, 4000, 6000, 2000, 4000]
    g = _store(released, released, px)
    st = _views(g)
    ht, rt = g[(N, "s13_share_holder_tokens")], g[(N, "s13_share_revenue_tokens")]
    assert list(ht["value"]) == pytest.approx(released) and list(rt["value"]) == pytest.approx(released)
    assert "price_usd of the same day" in ht["source"].iloc[0] and "price_usd of day+1" in rt["source"].iloc[0]
    q = st["share_basis"]["q0"]
    assert q["holder_tokens"] == q["revenue_tokens"] == pytest.approx(18000) and q["share"] == pytest.approx(1.0)
    usd = _sum(g, "holder_value_revenue_usd") / _sum(g, "revenue_usd")
    assert usd == pytest.approx(110000 / 128000) and q["holder_usd"] / q["revenue_usd"] == pytest.approx(usd)
    assert bw.s13_share_metrics(N) == ("s13_share_holder_tokens", "s13_share_revenue_tokens")


def test_a_landing_after_a_price_rise_moves_the_dollar_lines_not_the_share():
    """Jake's case in small: the UNI released lands two days later, at a higher price. In $ holder revenue is above
    revenue; in UNI the window burned exactly what was released two days earlier."""
    px = [4.0, 4.0, 4.0, 8.0, 8.0, 8.0, 8.0, 8.0]
    released = [2000, 2000, 2000, 2000, 2000, 0, 0]
    burned = [0, 0, 2000, 2000, 2000, 2000, 2000]
    g = _store(released, burned, px)
    q = _views(g)["share_basis"]["q0"]
    assert q["holder_usd"] > q["revenue_usd"]                      # 72,000 against 64,000: 1.125 in $
    assert q["holder_usd"] / q["revenue_usd"] == pytest.approx(72000 / 64000)
    assert q["share"] == pytest.approx(1.0)                        # 10,000 UNI ÷ 10,000 UNI
    assert q["in_flight_start"] == 0 and q["in_flight_end"] == pytest.approx(0.0)
    assert "Not above 1" in bw.s13_share_note(N) and "ABOVE 1 — REAL" not in bw.s13_share_note(N)


# ================================================================================================= never capped
def test_a_share_above_one_is_kept_and_said_on_the_number():
    """UNI released before the window lands inside it: the share in UNI is above 1 — real, kept, explained."""
    px = [5.0] * 8
    released = [6000, 6000, 2000, 2000, 2000, 2000, 2000]
    burned = [0, 0, 6000, 6000, 2000, 2000, 2000]
    g = _store(released, burned, px, first="2026-08-19")          # Q0 of the as-of opens after 2026-08-20
    st = _views(g, asof=pd.Timestamp("2026-11-18"))
    q = st["share_basis"]["q0"]
    assert q["first"] == "2026-08-21" and q["holder_tokens"] == pytest.approx(18000)
    assert q["revenue_tokens"] == pytest.approx(10000) and q["share"] == pytest.approx(1.8)
    assert q["in_flight_start"] == pytest.approx(12000) and q["in_flight_end"] == pytest.approx(4000)
    assert q["in_flight_start"] - q["in_flight_end"] == pytest.approx(q["holder_tokens"] - q["revenue_tokens"])
    note = bw.s13_share_note(N)
    assert "ABOVE 1 — REAL, NOT CAPPED" in note and "8,000 more UNI" in note and "1.8000" in note
    assert "12,000 UNI at the window's start, 4,000 at its end" in note and note.startswith("Q0, THIS BUILD")
    # the whole series is the view: nothing was clipped to the other line
    assert _sum(g, "s13_share_holder_tokens") == pytest.approx(sum(burned))
    assert _sum(g, "s13_share_revenue_tokens") == pytest.approx(sum(released))


def test_the_share_formula_reads_the_two_token_views_with_no_cap():
    class R:                                                       # the Data references the cell's formula is made of
        def D(self, r, metric, key):
            return f"D!{metric}!{key}"
    px = [5.0] * 6
    g = _store([100] * 5, [100] * 5, px)
    _views(g)
    cols = {c[0]: c for c in bw._revenue_line_cols(R())}
    p = config.PROJECT_BY_NAME[N]
    f = cols[bw.CP_HSHARE_HEAD][1](5, p)
    assert "D!s13_share_holder_tokens!q0" in f and "D!s13_share_revenue_tokens!q0" in f
    assert "holder_value_revenue_usd" not in f and "MIN(" not in f.upper() and "MAX(" not in f.upper()
    # the three lines' own cells keep their $ series (their same-covered-days views)
    assert cols[bw.CP_REV_LINE_HEAD][1](5, p) == bw.pull("D!revenue_usd_s13d!q0")
    assert cols[bw.CP_HREV_LINE_HEAD][1](5, p) == bw.pull("D!holder_value_revenue_usd_s13d!q0")
    assert cols[bw.CP_FEES_LINE_HEAD][1](5, p) == bw.pull("D!fees_usd!q0")
    note = cols[bw.CP_HSHARE_HEAD][5]["note_fn"](p)
    assert "ONE BASIS (R13" in note and "SAME COVERED DAYS" in note and "Can read above 1" in note
    # Hyperliquid's share is what it was: $ ÷ $ on its same-days views
    assert bw.s13_share_metrics("Hyperliquid") is None and bw.s13_share_note("Hyperliquid") == ""


# ============================================================================ unknown is never 0; a stored 0 is 0
def test_a_day_without_its_price_is_left_out_of_both_lines_never_valued_at_another_days():
    px = [5.0, 5.0, 5.0, 5.0, 5.0]
    g = _store([100, 100, 100, 100], [100, 100, 100, 100], px)
    g[(N, "price_usd")] = g[(N, "price_usd")].iloc[:4]            # the price at the END of the last day is not stored
    st = _views(g)
    assert st["share_basis"]["left_out"] == ["2026-11-04"]
    for m in ("s13_share_holder_tokens", "s13_share_revenue_tokens"):
        assert len(g[(N, m)]) == 3 and _sum(g, m) == pytest.approx(300)      # out of BOTH: the same days on each side
    assert st["share_basis"]["q0"]["share"] == pytest.approx(1.0)
    assert "LEFT OUT OF BOTH LINES" in bw.s13_share_note(N) and "2026-11-04" in bw.s13_share_note(N)
    assert len(g[(N, "revenue_usd_s13d")]) == 4                   # the $ line's own cell keeps the day


def test_a_stored_zero_is_zero_uni_whatever_the_price():
    px = [5.0, 5.0, 5.0, 5.0]
    g = _store([0, 100, 100], [0, 100, 100], px)
    g[(N, "price_usd")] = g[(N, "price_usd")].iloc[1:]            # no price on the first day: its $ are a stored 0
    st = _views(g)
    assert st["share_basis"]["left_out"] == []
    assert list(g[(N, "s13_share_holder_tokens")]["value"]) == pytest.approx([0, 100, 100])


def test_no_view_without_both_lines_or_a_price():
    g = _store([100, 100], [100, 100], [5.0, 5.0, 5.0])
    del g[(N, "price_usd")]
    st = _views(g)
    assert "share_basis" not in st and bw.s13_share_metrics(N) is None
    assert (N, "s13_share_holder_tokens") not in g
    g = _store([100, 100], [100, 100], [5.0, 5.0, 5.0])
    del g[(N, "revenue_usd")]
    _views(g)
    assert bw.s13_share_metrics(N) is None and (N, "s13_share_revenue_tokens") not in g


# ===================================================================================== after a TokenJar takeover
def test_not_formed_where_the_jar_inflows_hold_the_revenue_line(monkeypatch):
    """Accrued assets are not UNI released: on a build that handed revenue over, the share is $ ÷ $ on the same days."""
    g = _store([100] * 4, [100] * 4, [5.0] * 5)
    bw._s13_views(g)
    monkeypatch.setitem(config.TOKENJAR_NOTE, N, {"handed_over": True, "text": ""})
    bw._s13_share_basis_views(g, ASOF)
    assert bw.s13_share_metrics(N) is None and (N, "s13_share_holder_tokens") not in g
    assert "NOT FORMED" in bw.s13_share_note(N) and "TokenJar" in bw.s13_share_note(N)


# ======================================================================= the figures of Jake's export, re-derived
def test_the_decomposition_of_the_export_gap_adds_up():
    """config's record of Jake's 2026-11-18 export: the $ gap = tokens + landing price + start-of-day basis."""
    e = config.S13_SHARE_BASIS[N]["evidence"]["q0_export"]
    assert e["holder_usd"] / e["revenue_usd"] == pytest.approx(e["usd_quotient"], abs=5e-5)
    assert e["holder_uni"] / e["revenue_uni"] == pytest.approx(e["share"], abs=5e-5)
    assert e["holder_uni"] - e["revenue_uni"] == pytest.approx(e["in_flight_start_uni"] - e["in_flight_end_uni"], abs=2)
    gap = e["holder_usd"] - e["revenue_usd"]
    assert 689_702 + 1_302_218 - 305_055 == pytest.approx(gap, abs=2)
    assert e["share"] > 1                                           # still above 1 on one basis: said, not capped
