"""PROJECT ROUND 1B, group `labels` (Jake 2026-11-09): items 1, 8, 10, 11.

1. The 2020 airdrop claims (12,257,360 UNI, 2026-10-11..11-02) are 70.8% of Uniswap's Q0 release (17,316,997) on Jake's
   store: every cell that reads that release window says "of which one-off" and what it reads without it (A4 key 0.317 →
   1.08 without; total dilution 11.26% → 3.3%; net holder yield), values unchanged. Tested on a LIVE-SHAPED fixture: the
   committed dump (to 2026-10-13) carried forward to 2026-11-09 with the claims inside Q0 and the distributor's own
   balance stored only from 2026-11-05 (P5-1.7 shipped 11-04) — the shape that hid the note on Jake's store.
No test touches the network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402

UNI = config.PROJECT_BY_NAME["Uniswap"]

# ===================================================================================== the live-shaped fixture (item 1)
LIVE_ASOF = pd.Timestamp("2026-11-09")
CLAIMS = 12_257_360.0              # the distributor's claims 2026-10-11..11-02 (P4-Q9 trace; Jake's store)
RELEASE_Q0 = 17_316_997.0          # Jake's Q0 unlock_release_tokens on 2026-11-09
BURN_Q0 = 5_486_000.0              # a Q0 burn giving Jake's key: 0.317 (1.08 without the claims)


def live_payload(dump=ROOT / "fixtures" / "real_uniswap.json") -> dict:
    """The committed Uniswap dump (as of 2026-10-13) carried forward to 2026-11-09 like Jake's store: every daily series
    extended 27 days (a flow repeats the day four weeks earlier, a stock holds), then — the distributor's claims laid
    over 10-11..11-02 so they total 12,257,360 (the dump's falls 10-11..10-13 kept), the rest of (e) after them so the Q0
    release is 17,316,997; the distributor's own balance stored only from 2026-11-05; the burn's added days scaled to a
    Q0 of 5,486,000 (0x…dEaD, the buyback copy and CoinGecko's circulating follow)."""
    d = json.loads(Path(dump).read_text(encoding="utf-8"))
    by: dict = {}
    for r in d["metrics"]:
        by.setdefault(r["metric"], {})[r["date"]] = r
    out = list(d["metrics"])
    dump_asof = pd.Timestamp(d["meta"]["asof"])
    shift = (LIVE_ASOF - dump_asof).days

    def day_(x):
        return pd.Timestamp(x).strftime("%Y-%m-%d")

    def add(metric, day, value, like):
        r = dict(like, date=day_(day), value=float(value), fetched_at=day_(day) + "T16:22:38Z", metric=metric)
        by.setdefault(metric, {})[r["date"]] = r
        out.append(r)

    for m, s in list(by.items()):
        days = sorted(s)
        last = pd.Timestamp(days[-1])
        if (dump_asof - last).days > 10:
            continue
        flow = (config.METRICS.get(m) or {}).get("kind") == "flow"
        for k in range(1, shift + 1):
            day = last + pd.Timedelta(days=k)
            src = s.get(day_(day - pd.Timedelta(days=28)))
            add(m, day, src["value"] if flow and src is not None else s[days[-1]]["value"], s[days[-1]])
    lo = LIVE_ASOF - pd.Timedelta(days=90)
    nc = by["noncirculating_holding_tokens"]
    done = nc["2026-10-10"]["value"] - nc["2026-10-13"]["value"]
    per = (CLAIMS - done) / 20
    for k in range(1, 21):
        nc[day_(pd.Timestamp("2026-10-13") + pd.Timedelta(days=k))]["value"] = nc["2026-10-13"]["value"] - per * k
    after = nc["2026-11-02"]["value"]
    tr = by["treasury_holding_tokens"]
    td = sorted(tr)
    d_leg = sum(max(0.0, tr[a]["value"] - tr[b]["value"]) for a, b in zip(td, td[1:]) if pd.Timestamp(b) > lo)
    nd = sorted(nc)
    e_before = sum(max(0.0, nc[a]["value"] - nc[b]["value"]) for a, b in zip(nd, nd[1:])
                   if lo < pd.Timestamp(b) < pd.Timestamp("2026-10-11"))
    rest = RELEASE_Q0 - CLAIMS - d_leg - e_before
    assert rest >= 0, rest
    for k, day in enumerate(pd.date_range("2026-11-03", LIVE_ASOF)):
        nc[day_(day)]["value"] = after - rest * (k + 1) / 7
    like = dict(nc["2026-11-05"], source="chain:ethereum:noncirc_merkle_distributor")
    for day in pd.date_range("2026-11-05", LIVE_ASOF):
        add("noncirc_merkle_distributor_tokens", day, nc[day_(day)]["value"], like)
    gb = by["gross_burn_tokens"]
    old = sum(r["value"] for k, r in gb.items() if lo < pd.Timestamp(k) <= dump_asof)
    new = [k for k in gb if pd.Timestamp(k) > dump_asof]
    f = (BURN_Q0 - old) / sum(gb[k]["value"] for k in new)
    for k in new:
        gb[k]["value"] *= f
    bab = by["burn_address_balance"]
    for k in sorted(k for k in bab if pd.Timestamp(k) > dump_asof):
        bab[k]["value"] = bab[day_(pd.Timestamp(k) - pd.Timedelta(days=1))]["value"] + gb.get(k, {"value": 0.0})["value"]
    for k in new:
        if k in by.get("actual_buyback_tokens", {}):
            by["actual_buyback_tokens"][k]["value"] = gb[k]["value"]
        if k in by.get("actual_buyback_usd", {}) and k in by["price_usd"]:
            by["actual_buyback_usd"][k]["value"] = gb[k]["value"] * by["price_usd"][k]["value"]
    cs = by["circulating_supply"]
    for k in sorted(k for k in cs if pd.Timestamp(k) > dump_asof):
        cs[k]["value"] = (cs[day_(dump_asof)]["value"] + (nc[day_(dump_asof)]["value"] - nc[k]["value"])
                          - (bab[k]["value"] - bab[day_(dump_asof)]["value"]))
    return {**d, "meta": dict(d["meta"], asof=day_(LIVE_ASOF)), "metrics": out}


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """A Uniswap-only build of the live-shaped fixture (about 10 s): {audit, reader} workbooks (formulas + comments)."""
    import store as sm
    from real_dump import to_store
    d = tmp_path_factory.mktemp("pr1b_live")
    db = d / "metrics.db"
    pl = live_payload()
    to_store(pl, db)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sm, "DB_PATH", str(db))
        mp.setitem(sys.modules, "recalc", None)
        mp.setenv("TOKEN_METRICS_STORE_VALUES", "1")
        st = sm.Store(str(db))
        try:
            bw.build_workbook(st, d / "audit.xlsx", asof=LIVE_ASOF, only=["Uniswap"], reader_path=d / "reader.xlsx")
        finally:
            st.close()
    assert "error" not in bw.READER_STATS, bw.READER_STATS
    return {"audit": openpyxl.load_workbook(d / "audit.xlsx"), "audit_v": openpyxl.load_workbook(d / "audit.xlsx",
                                                                                                    data_only=True),
            "reader": openpyxl.load_workbook(d / "reader.xlsx"),
            "reader_v": openpyxl.load_workbook(d / "reader.xlsx", data_only=True), "groups": dict(bw._LAST_GROUPS)}


def _cell(wb, sheet, header, project="Uniswap", exact=False):
    """The project's cell under the column whose header (rows 1-4) starts with `header`."""
    ws = wb[sheet]
    col = next((c for c in range(1, ws.max_column + 1) for r in range(1, 5)
                if isinstance(ws.cell(r, c).value, str)
                and (ws.cell(r, c).value == header if exact else ws.cell(r, c).value.startswith(header))), None)
    assert col is not None, f"{sheet}: no column {header!r}"
    row = next(r for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value == project)
    return ws.cell(row, col)


def _note(c) -> str:
    return c.comment.text if c.comment is not None else ""


def test_1_the_live_shaped_fixture_holds_jakes_release_and_claims(live):
    g = live["groups"]
    rel = bw._daily(g[("Uniswap", "unlock_release_tokens")])
    q0 = rel[(rel.index > LIVE_ASOF - pd.Timedelta(days=90)) & (rel.index <= LIVE_ASOF)].sum()
    assert q0 == pytest.approx(RELEASE_Q0, abs=1.0)
    part, tot, _w = bw.ONE_OFF_SHARES[("Uniswap", "unlock_release_tokens", "q0")]
    assert part == pytest.approx(CLAIMS, abs=1.0) and f"{part / tot:.1%}" == "70.8%"


AUDIT_CELLS = [  # (sheet, header, shown, without) — Jake's numbers where the fixture reproduces them
    ("A4 Permanent Burn", "BURN ÷ ISSUANCE (x)", "0.317x", "1.08x"),
    ("A4 Permanent Burn", "UNLOCK DILUTION Q0 (tokens)", "17,316,997", "5,059,637"),
    ("A4 Permanent Burn", "Unlock dilution, annualised % of free float", "11.26%", "3.29%"),
    ("A4 Permanent Burn", "TOTAL DILUTION = emissions + unlocks", "11.26%", "3.29%"),
    ("CP Comparability", "Unlocks Q0 (tokens)", "17,316,997", "5,059,637"),
    ("CP Comparability", "TOTAL DILUTION = outflows a+b+c+d+e (annualised) ÷ tradeable float", "11.26%", "3.29%"),
    ("CP Comparability", "TOTAL DILUTION = outflows a+b+c+d+e (annualised) ÷ circulating", "11.26%", "3.29%"),
    ("CP Comparability", "NET HOLDER YIELD", "-8.80%", "-0.82%"),
]


@pytest.mark.parametrize("sheet,header,shown,without", AUDIT_CELLS)
def test_1_every_audit_cell_reading_the_release_says_its_one_off_and_without(live, sheet, header, shown, without):
    c = _cell(live["audit"], sheet, header)
    n = _note(c)
    assert "ONE-OFF IN THIS RATE" in n and ("70.8%" in n or "67.3%" in n), n[:400]
    assert f"Without the one-off this cell reads {without} (shown: {shown}," in n, n[:900]


def test_1_the_key_names_the_release_it_divides_by(live):
    n = _note(_cell(live["audit"], "A4 Permanent Burn", "BURN ÷ ISSUANCE (x)"))
    assert "of which one-off: 70.8% of the unlock_release_tokens Q0 it divides by" in n
    v = _cell(live["audit_v"], "A4 Permanent Burn", "BURN ÷ ISSUANCE (x)").value
    assert v == pytest.approx(BURN_Q0 / RELEASE_Q0), "values unchanged: the key keeps the claims"


READER_CELLS = [  # (sheet, header, text that must be in the note)
    ("Summary", "Archetype key", "Without the one-off this cell reads 1.08x"),
    ("Summary", "Total dilution, % of float", "Without the one-off this cell reads 3.29%"),
    ("Summary", "Total dilution, tokens a year", "Without the one-off this cell reads 3.29%"),
    ("Summary", "Net holder yield", "Without the one-off this cell reads -0.82%"),
    ("Comparison", "Total dilution, % of float", "Without the one-off this cell reads 3.29%"),
    ("Comparison", "Net holder yield", "Without the one-off this cell reads -0.82%"),
    ("A4 Permanent Burn", "Burn ÷ issuance (x)", "70.8% of the unlock_release_tokens"),
    ("A4 Permanent Burn", "Issuance or release, 90 days", "Without the one-off this cell reads 5,059,637"),
    ("A4 Permanent Burn", "Issuance growth, selected period", "of which one-off: 70.8% of the latest 90 days"),
    ("A4 Permanent Burn", "Key: change vs previous quarter", "Q0: of which one-off: 70.8%"),
    ("A4 Permanent Burn", "UNLOCK DILUTION last 90 days", "Without the one-off this cell reads 5,059,637"),
    ("A4 Permanent Burn", "Unlocks, % of free float a year", "Without the one-off this cell reads 3.29%"),
    ("A4 Permanent Burn", "Total dilution, % of float a year", "Without the one-off this cell reads 3.29%"),
]


@pytest.mark.parametrize("sheet,header,text", READER_CELLS)
def test_1_every_reader_cell_reading_the_release_carries_the_note(live, sheet, header, text):
    n = _note(_cell(live["reader"], sheet, header))
    assert text in n, n[:900]


def test_1_issuance_growth_says_what_it_reads_without_the_claims(live):
    n = _note(_cell(live["reader"], "A4 Permanent Burn", "Issuance growth, selected period"))
    assert "Without the one-off the growth reads +0.9% (with it: +245.3%" in n, n


def test_1_a_component_stored_after_the_claims_still_dates_them(monkeypatch):
    """Jake's store: noncirc_merkle_distributor_tokens first stored 2026-11-05, after the claims 10-11..11-02 — its falls
    held none of them, so no window said "of which one-off". A day the holder is not stored (with the day before) reads
    the category's outflow that day."""
    monkeypatch.setattr(bw, "scoped_projects", lambda: [UNI])
    days = pd.date_range("2026-10-09", "2026-11-09")

    def rows(metric, vals):
        return pd.DataFrame({"date": days[:len(vals)] if len(vals) == len(days) else vals.index, "project": "Uniswap",
                             "metric": metric, "value": list(vals), "source": "x", "tier": 2})
    out_e = pd.Series([1000.0 if pd.Timestamp("2026-10-11") <= d <= pd.Timestamp("2026-11-02") else 0.0 for d in days],
                      index=days)
    comp = pd.Series([5_000.0, 5_000.0, 4_990.0, 4_990.0, 4_990.0], index=pd.date_range("2026-11-05", periods=5))
    groups = {("Uniswap", "outflow_e_tokens"): rows("outflow_e_tokens", out_e),
              ("Uniswap", "unlock_release_tokens"): rows("unlock_release_tokens", out_e + 5.0),
              ("Uniswap", "noncirc_merkle_distributor_tokens"): pd.DataFrame(
                  {"date": comp.index, "project": "Uniswap", "metric": "noncirc_merkle_distributor_tokens",
                   "value": comp.values, "source": "x", "tier": 2})}
    bw._ONE_OFF_IN_SERIES.clear()
    bw._ONE_OFF_HV_RECORDED.clear()
    parts = bw._one_off_parts(groups, "Uniswap")
    assert sum(v for v, _w in parts["unlock_release_tokens"].values()) == 23_000.0, "23 claim days, each read"
    assert sum(v for v, _w in parts["outflow_e_tokens"].values()) == 23_000.0


def test_1_share_reads_one_decimal_between_one_and_ninety_nine():
    assert bw._share_pct(12_257_360 / 17_316_997) == "70.8%"
    assert bw._share_pct(0.23) == "23%" and bw._share_pct(13_303 / 5_057_505) == "0.26%"
    assert bw._share_pct(100_000_000 / 100_244_003) == "99.76%"


def test_1_a_one_off_in_the_denominator_is_named_so():
    f = ('=IF(INDEX(Data!$L$2:$L$9,MATCH($A5&"|unlock_release_tokens",Data!$A$2:$A$9,0))=0,"n/a",'
         'INDEX(Data!$L$2:$L$9,MATCH($A5&"|gross_burn_tokens",Data!$A$2:$A$9,0))/'
         'INDEX(Data!$L$2:$L$9,MATCH($A5&"|unlock_release_tokens",Data!$A$2:$A$9,0)))')
    assert bw._divides_by(f, "L", "unlock_release_tokens")
    assert not bw._divides_by(f, "L", "gross_burn_tokens")


# ============================================================================================================ item 8
def test_8_a4_issuance_minus_burn_is_named_so_on_audit_and_credibility(live):
    ws = live["audit"]["A4 Permanent Burn"]
    heads = [ws.cell(4, c).value for c in range(1, ws.max_column + 1)]
    assert not any(str(h).startswith(("NET SUPPLY CHANGE", "Net supply change", "Derived net supply")) for h in heads)
    assert any(str(h).startswith("ISSUANCE − BURN Q0 (tokens)") for h in heads)
    assert any(str(h).startswith("ISSUANCE − BURN, annualised % of circulating") for h in heads)
    net = _cell(live["audit"], "A4 Permanent Burn", "ISSUANCE − BURN Q0 (tokens)")
    assert net.number_format.endswith('" burn only"'), "Uniswap's cell says burn only, as the reader has it"
    cred = live["audit"]["Credibility"]
    quoted = [str(cred.cell(r, 4).value) for r in range(1, cred.max_row + 1) if cred.cell(r, 1).value == "Uniswap"
              and cred.cell(r, 2).value == "A4 Permanent Burn"]
    assert any(q.startswith("ISSUANCE − BURN") for q in quoted) and not any("NET SUPPLY CHANGE" in q for q in quoted)


def test_8_burn_share_is_of_protocol_revenue_and_crossover_needs_fees(live):
    share = _cell(live["audit"], "A4 Permanent Burn", "Documented share burned (config)")
    assert share.number_format.endswith('" of revenue"') and "PROTOCOL REVENUE" in _note(share)
    assert _cell(live["audit"], "A4 Permanent Burn", "Implied burn Q0 (tokens) = burn base × documented share")
    assert _cell(live["audit"], "A4 Permanent Burn", "Fees required for crossover ($ a year) = fees × issuance")
    assert _cell(live["reader"], "A4 Permanent Burn", "Fees needed for crossover")
    assert _cell(live["reader"], "A4 Permanent Burn", "Share burned, of revenue or fees")


def test_8_market_share_header_says_what_a_shifted_quarter_reads():
    assert bw.MARKET_SHARE_HEAD.startswith("MARKET SHARE vs its category (90-day mean; a shifted quarter: that "
                                           "quarter's mean)")
    with bw.shifted(1):
        assert bw._shift_col("market_share_pct", config.market_share_window("Uniswap"), 1) == "q1"


def test_8_no_payout_note_quotes_the_dumps(live):
    for head in ("Payout ratio", "HOLDER SHARE = holder revenue"):
        n = _note(_cell(live["audit"], "CP Comparability", head))
        assert "1.071" not in n and "2026-10-12" not in n and "{live}" not in n, n
        assert "On this build's stored days, by complete month" in n, n
    assert "{live}" in config.PAYOUT_ABOVE_ONE["Uniswap"] and "1.071" not in config.PAYOUT_ABOVE_ONE["Uniswap"]


# ============================================================================================================ item 10
def test_10a_the_threshold_is_declared_and_a_lumpy_window_reads_the_code(live):
    assert config.LUMPY_SHARE["share"] == 0.80 and config.LUMPY_SHARE["days"] == 2 and "before measuring" in \
        config.LUMPY_SHARE["declared"]
    s = pd.Series([1.0] * 88 + [500.0, 500.0], index=pd.date_range("2026-01-01", periods=90))
    share, k, _days, tot, n = bw._lumpy_window(s, pd.Timestamp("2025-12-31"), pd.Timestamp("2026-03-31"))
    assert k == 2 and share == pytest.approx(1000 / 1088) and n == 90
    assert bw._lumpy_window(pd.Series([1.0] * 90, index=s.index), pd.Timestamp("2025-12-31"),
                            pd.Timestamp("2026-03-31")) is None
    c = _cell(live["audit"], "A4 Permanent Burn", "Burn Δ9m")       # Q-3 holds the 100M treasury burn
    assert c.value == "lumpy (98.9% of the window on 1 day)" and "As a rate it would read" in _note(c), _note(c)
    g = _cell(live["reader"], "A4 Permanent Burn", "Issuance growth, selected period")
    assert '_series!$B$1=90,"lumpy"' in g.value and "LUMPY (PR1B.10a" in _note(g)
    assert _cell(live["reader_v"], "A4 Permanent Burn", "Issuance growth, selected period").value == "lumpy"


def test_10b_every_project_with_a_share_reads_the_90_day_mean():
    for name, v in config.MARKET_SHARE.items():
        assert config.market_share_window(name) == ("now" if "na" in v else "q0"), name
        if "na" not in v:
            assert config.MARKET_SHARE_MEAN[name]["means"] == (30, 60)


def test_10c_a_yield_that_can_be_negative_changes_in_percentage_points(live):
    import trajectory
    assert config.qoq_in_pp("NET HOLDER YIELD = holder value yield − dilution", True)
    assert not config.qoq_in_pp("NET HOLDER YIELD", False) and not config.qoq_in_pp("Fees Q0 ($)", True)
    assert trajectory.qoq_text(-0.0798, pp=True) == "QoQ -8.0 pp"
    ws = live["audit_v"]["Trajectory"]
    row = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, 1).value == "Uniswap"
               and str(ws.cell(r, 5).value).startswith("NET HOLDER YIELD"))
    assert "QoQ -8.0 pp" in ws.cell(row, trajectory.COL["Trend"]).value
    assert live["audit"]["Trajectory"].cell(row, trajectory.COL["QoQ"]).number_format == bw.FMT_PP
    pct = _cell(live["reader"], "A4 Permanent Burn", "Issuance − burn, % a year")
    qc = pct.parent.cell(pct.row, pct.column + 1)                          # its "Quarter change"
    assert qc.number_format == '+0.0" pp";-0.0" pp";0.0" pp"' and "percentage points" in _note(qc)


# ============================================================================================================ item 11
def test_11_uniswap_constant_price_tvl_cells_give_the_dated_source_limit(live):
    for head in ("TVL change, start-of-window prices", "TVL change at constant prices"):
        c = _cell(live["reader"], "Business", head)
        assert c.value == "n/a" and "read 2026-11-09" in _note(c) and "uniswap-v3" in _note(c), _note(c)
