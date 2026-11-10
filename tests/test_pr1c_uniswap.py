"""Project round 1C — Uniswap finish (Jake 2026-11-10). No network."""
import pandas as pd

import build_workbook as bw
import config

# The 2020 airdrop distributor's daily claims, 0x090D4613….balanceOf at each UTC day's first block (eth.drpc.org
# archive eth_call, read 2026-11-10): 12,256,960.38 UNI over 2026-10-11..11-02 (the note's 12,257,360 within 400).
CLAIMS = {"2026-10-09": 400.0, "2026-10-12": 400.0, "2026-10-13": 163312.23, "2026-10-14": 4403.52,
          "2026-10-15": 266196.23, "2026-10-16": 6000.0, "2026-10-17": 4635.91, "2026-10-18": 41265.28,
          "2026-10-19": 400.0, "2026-10-20": 1200.0, "2026-10-21": 800.01, "2026-10-22": 760126.63,
          "2026-10-23": 1260370.63, "2026-10-24": 800336.46, "2026-10-25": 159711.0, "2026-10-26": 773266.48,
          "2026-10-27": 5095200.0, "2026-10-28": 1786800.0, "2026-10-29": 885273.06, "2026-10-30": 53214.64,
          "2026-11-01": 178048.31, "2026-11-02": 16000.0}
IN_WINDOW = sum(v for d, v in CLAIMS.items() if "2026-10-11" <= d <= "2026-11-02")


def _frame(series: dict, source="derived"):
    return pd.DataFrame({"date": pd.to_datetime(list(series)), "value": list(series.values()), "source": source})


def _live_groups():
    """Jake's store shape (2026-11-09): the carriers (the falls of the whole non-circulating set, read live) carry each
    day's claims on that day; the distributor's own series, read at each day's FIRST BLOCK by the catch-up, falls the
    day after (its balance at 00:00 of d+1 is lower by day d's claims). unlock_release_tokens adds the growth budget's
    5,000,000 release of 2026-10-01 (outflow d); the other (e) release in Q0 brings vesting to 12,316,597."""
    days = pd.date_range("2026-08-12", "2026-11-09")
    vest = {d.strftime("%Y-%m-%d"): 0.0 for d in days}
    for d, v in CLAIMS.items():
        vest[d] = v
    vest["2026-11-06"] = 12_316_597.0 - sum(vest.values())          # Q0 total as Jake's store reads it
    bal, comp = 30_000_000.0, {}
    for d in days:
        comp[d.strftime("%Y-%m-%d")] = bal                                # balance at the day's first block
        bal -= CLAIMS.get(d.strftime("%Y-%m-%d"), 0.0)
    rel = dict(vest)
    rel["2026-10-01"] = rel.get("2026-10-01", 0.0) + 5_000_000.0
    return {("Uniswap", "noncirc_merkle_distributor_tokens"): _frame(comp, "chain:ethereum:noncirc:archive"),
            ("Uniswap", "outflow_e_tokens"): _frame(vest),
            ("Uniswap", "unlock_release_vesting_tokens"): _frame(vest),
            ("Uniswap", "unlock_release_tokens"): _frame(rel)}


def _old_same_day_min(groups):
    """The rule before PR1C.1: the holder's fall and the carrier matched day by day."""
    spec = config.ONE_OFF_DILUTION_NOTES["Uniswap"]
    lo, hi = pd.Timestamp(spec["from"]), pd.Timestamp(spec["to"])
    comp = bw._daily(groups[("Uniswap", spec["component"])]).sort_index()
    own = (-comp.diff()).clip(lower=0.0)[comp.index.to_series().diff().dt.days.eq(1).to_numpy()]
    own = own[(own.index >= lo) & (own.index <= hi)]
    s = bw._daily(groups[("Uniswap", "unlock_release_tokens")])
    return sum(min(float(v), float(s[d])) for d, v in own.items() if d in s.index)


def test_1_the_one_off_counts_every_claim_when_holder_and_carrier_date_them_a_day_apart():
    """PR1C.1: Jake's store read 6,756,992 UNI one-off (39%) where the claims are 12,257,360: a same-day min of the
    distributor's falls (dated the day after, first-block reads) and the carrier (dated the day itself) keeps about half
    of claims this lumpy. Matched by a running remainder, every claim is placed and none twice."""
    groups = _live_groups()
    old = _old_same_day_min(groups)
    assert old < 0.6 * IN_WINDOW, old                                   # the bug, reproduced (~5.5M of 12.26M)
    parts = bw._one_off_parts(groups, "Uniswap")
    for c in ("outflow_e_tokens", "unlock_release_vesting_tokens", "unlock_release_tokens"):
        got = sum(v for v, _w in parts[c].values())
        assert abs(got - IN_WINDOW) < 1.0, (c, got, IN_WINDOW)
        s = bw._daily(groups[("Uniswap", c)])
        assert all(v <= float(s[d]) + 1e-6 for d, (v, _w) in parts[c].items())   # never more than a carrier row
    share = sum(v for v, _w in parts["unlock_release_tokens"].values()) / 17_316_597.0
    assert bw._share_pct(share) == "70.8%", share


def test_1_the_key_and_dilution_without_the_one_off_read_as_jake_expects():
    """Expected on the live store: key without the one-off ≈1.08x (0.317x with it); unlock dilution ≈3.3% (11.26%)."""
    groups = _live_groups()
    parts = bw._one_off_parts(groups, "Uniswap")
    one_off = sum(v for v, _w in parts["unlock_release_tokens"].values())
    release, burn_ratio = 17_316_597.0, 0.317
    burn = burn_ratio * release                                          # the key = burn ÷ release (Jake's 0.317x)
    assert abs(burn / (release - one_off) - 1.08) < 0.01
    dilution = 0.1126                                                     # 11.26% with the claims
    assert abs(dilution * (release - one_off) / release - 0.033) < 0.001


def test_1_allocation_handles_a_carrier_row_that_spans_a_missing_day():
    falls = pd.Series([100.0, 200.0, 50.0], index=pd.to_datetime(["2026-10-20", "2026-10-21", "2026-10-22"]))
    carrier = pd.Series([100.0, 250.0], index=pd.to_datetime(["2026-10-20", "2026-10-22"]))   # 10-21 not stored
    got = bw._allocate_falls(falls, carrier, pd.Timestamp("2026-10-11"), pd.Timestamp("2026-11-02"))
    assert got == {pd.Timestamp("2026-10-20"): 100.0, pd.Timestamp("2026-10-22"): 250.0}
    # a carrier row smaller than the falls takes only itself; the rest is never placed elsewhere outside the window
    got2 = bw._allocate_falls(falls, carrier * 0.5, pd.Timestamp("2026-10-11"), pd.Timestamp("2026-11-02"))
    assert sum(got2.values()) == 175.0


def test_1_never_more_than_the_declared_one_off():
    falls = pd.Series([10e6, 5e6], index=pd.to_datetime(["2026-10-27", "2026-10-28"]))
    carrier = pd.Series([10e6, 5e6], index=pd.to_datetime(["2026-10-27", "2026-10-28"]))
    got = bw._allocate_falls(falls, carrier, pd.Timestamp("2026-10-11"), pd.Timestamp("2026-11-02"), cap=12_257_360)
    assert sum(got.values()) == 12_257_360


# ============================================================================ PR1C.2: revenue back to DefiLlama
import pytest  # noqa: E402

from fetch.base import _measuring_point  # noqa: E402

ASOF = pd.Timestamp("2026-11-09")
UNI = config.PROJECT_BY_NAME["Uniswap"]


def _r(metric, days, vals, source, tier=1):
    days = pd.to_datetime(list(days))
    vals = vals if isinstance(vals, (list, tuple)) else [vals] * len(days)
    return pd.DataFrame({"date": days, "project": "Uniswap", "metric": metric, "value": [float(x) for x in vals],
                         "source": source, "tier": tier})


def _store_like():
    """Jake's store, 2026-11-09: DefiLlama dailyRevenue to 11-08; the jar inflows 11-05..11-08 only, 66.3% of the
    transfers unpriced, the native coin not read on 4 chains (PARTIAL), the balance read on 1 of 13 chains (PARTIAL)."""
    jd = pd.date_range("2026-11-05", "2026-11-08")
    part = config.mark_source("chain:tokenjars:tokenjar_inflow[native coin NOT READ on base, arbitrum, bnb, polygon]",
                              "PARTIAL")
    bal = config.mark_source("chain:tokenjars:tokenjar_balance[NOT READ: 12 chains]", "PARTIAL")
    return {("Uniswap", "revenue_usd"): _r("revenue_usd", pd.date_range("2026-07-01", "2026-11-08"), 300_000.0,
                                           "defillama"),
            ("Uniswap", "tokenjar_inflow_usd"): _r("tokenjar_inflow_usd", jd, 170_000.0, part, 2),
            ("Uniswap", "tokenjar_inflow_transfers"): _r("tokenjar_inflow_transfers", jd, 10_000.0, "chain:x", 2),
            ("Uniswap", "tokenjar_unpriced_transfers"): _r("tokenjar_unpriced_transfers", jd, 6_630.0, "chain:x", 2),
            ("Uniswap", "tokenjar_balance_usd"): _r("tokenjar_balance_usd", ["2026-11-08"], 5_367.0, bal, 2)}


@pytest.fixture
def uni_only(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [UNI])
    monkeypatch.setattr(config, "TOKENJAR_NOTE", {})
    yield


def test_2_revenue_is_defillama_with_the_jars_beside_and_no_break(uni_only, monkeypatch):
    """PR1C.2 (Jake 2026-11-10): revenue_usd = DefiLlama dailyRevenue, secondary; the jars beside it, not spliced; the
    2026-11-05 series break gone. The takeover test fails on all four counts and says so with the numbers."""
    import trajectory
    groups = _store_like()
    before = groups[("Uniswap", "revenue_usd")].copy()
    bw._tokenjar_revenue_views(groups, ASOF)
    pd.testing.assert_frame_equal(groups[("Uniswap", "revenue_usd")], before)          # not spliced
    assert set(groups[("Uniswap", "revenue_usd")]["source"].map(_measuring_point)) == {"defillama"}
    assert trajectory.basis_breaks("Uniswap", ["revenue_usd"], groups, pd.Timestamp("2026-01-01")) == []
    assert len(groups[("Uniswap", "tokenjar_inflow_usd")]) == 4                       # its own series, beside
    st = config.TOKENJAR_NOTE["Uniswap"]
    assert st["handed_over"] is False
    t = "; ".join(st["takeover"])
    assert "full Q0 window: 4 of 89 days held" in t
    assert "priced: 33.7% of 40,000 transfers in (needs ≥95%)" in t
    assert "native coin read: NOT on 4 day(s)" in t
    assert "start (2026-08-11): not read within 3 days" in t and "end (2026-11-08): PARTIAL" in t
    monkeypatch.setitem(bw._WINDOWS, "q0", ("2026-08-11", "2026-11-09"))
    bw._tokenjar_window_notes(groups)
    tier = config.revenue_tier("Uniswap")
    assert tier[0] == "revenue" and tier[1].startswith("SECONDARY — DefiLlama 'uniswap' dailyRevenue")
    assert "THIS BUILD: THE TOKENJAR INFLOWS SIT BESIDE THIS LINE" in tier[1]
    assert config.revenue_split("Uniswap")["revenue"]["primary"] is False


def test_2_the_takeover_test_is_declared_in_config():
    t = config.TOKENJAR_TAKEOVER["Uniswap"]
    assert t["full_window"] and t["min_priced_share"] == 0.95 and t["native_every_chain"] and t["balance_every_chain"]
    assert t["declared"] == "2026-11-10"


# ============================================================================ PR1C.3: the sign-off rule
def test_3_unverifiable_or_na_with_a_reason_does_not_hold_sign_off_open():
    import credibility as cred
    rows = [{"project": "U", "id": "a", "note": "no independent source exists: Token Terminal is paid"},
            {"project": "U", "id": "b", "note": "by design"},
            {"project": "U", "id": "c", "note": ""}]
    s = cred.signoff(rows[:2], ["UNVERIFIABLE (inputs)", "N/A"])
    assert s["U"]["status"] == "NO OPEN CHECKS" and s["U"]["counts"] == {"UNVERIFIABLE": 1, "N/A": 1}
    s2 = cred.signoff(rows, ["PASS", "N/A", "UNVERIFIABLE"])                    # a row with no reason: OPEN
    assert s2["U"]["status"] == "OPEN" and [o[0] for o in s2["U"]["open"]] == ["c"]
    for v in ("CHECK (inputs)", "FRESH-only", "something uncoded", ""):         # still open, whatever the note
        assert cred.signoff([rows[0]], [v])["U"]["status"] == "OPEN", v
    assert not cred.signed("UNVERIFIABLE", "  ") and cred.signed("UNVERIFIABLE", "why")


def test_3_the_credibility_tab_counts_the_same_rule_and_moves_no_value():
    """The summary's OPEN column = CHECK + FRESH-only + uncoded + UNVERIFIABLE / N/A with an empty note — evaluated."""
    import openpyxl
    import xlcalc
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Credibility"
    rows = [("U", "PASS", "x"), ("U", "UNVERIFIABLE", "why"), ("U", "N/A", "by design"), ("V", "UNVERIFIABLE", None),
            ("V", "FRESH-only", "re-reads ours"), ("W", "CHECK", "gap"), ("W", "??", "x")]
    for i, (p, v, n) in enumerate(rows, start=11):
        ws.cell(i, 1, p), ws.cell(i, 11, v), ws.cell(i, 12, n)
    K, A, L = "$K$11:$K$17", "$A$11:$A$17", "$L$11:$L$17"
    for r, p in ((2, "U"), (3, "V"), (4, "W")):
        ws.cell(r, 1, p)
        for j, v in enumerate(("PASS*", "CHECK*", "FRESH-only*", "UNVERIFIABLE*", "N/A*"), start=2):
            ws.cell(r, j, f'=COUNTIFS({A},$A{r},{K},"{v}")')
        ws.cell(r, 7, f"=COUNTIF({A},$A{r})")
        for j in (8, 9, 10):
            ws.cell(r, j, 0)
        ws.cell(r, 11, f"=C{r}+D{r}+(G{r}-B{r}-C{r}-D{r}-E{r}-F{r}-H{r}-I{r}-J{r})"
                       f'+COUNTIFS({A},$A{r},{K},"UNVERIFIABLE*",{L},"")+COUNTIFS({A},$A{r},{K},"N/A*",{L},"")')
    X = xlcalc.Workbook(wb)
    assert [X.value("Credibility", r, 11) for r in (2, 3, 4)] == [0.0, 2.0, 2.0]
    src = open(bw.__file__, encoding="utf-8").read()
    assert 'COUNTIFS({crit}{K},"UNVERIFIABLE*",{L},"")' in src and 'COUNTIFS({crit}{K},"N/A*",{L},"")' in src
