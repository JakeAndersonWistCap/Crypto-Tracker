"""PROJECT ROUND 1, group `revenue` (Jake 2026-11-05).

S13: three revenue lines defined once (FEES, REVENUE, HOLDER REVENUE; HOLDER SHARE = holder revenue ÷ revenue); REVENUE
     is the headline. All three and the share on CP and the reader; Uniswap populated, every other project "not yet
     split"; every headline revenue cell says which line it is (the GATE below).
S8:  net supply change = the change in circulating supply (closing − opening), everywhere; a cell that is not says what
     it is ("burn only" / "issuance − burn" / "release − burn"). GATE: Master's net supply change = closing − opening
     circulating within config.NET_SUPPLY_CIRC's declared tolerance, on every project.
U1:  Highlights C5 fires only when circulating falls; its caveat quotes the latest 30 days against the prior 30.
U7:  Uniswap's three lines, each route stated on the cell.
No network: the build reads the 15 stored dumps (the shared plain build).
"""
from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import highlights as hl  # noqa: E402
import reader  # noqa: E402

LINES = ("fees", "revenue", "holder_revenue")
SUFFIXES = tuple(config.REVENUE_TIER_SUFFIX.values())


# ======================================================================================================== S13, units
def test_s13_the_definitions_are_jakes_words_once():
    d = config.REVENUE_LINE_DEFS
    assert d["fees"][1] == "All fees paid by users of the protocol in the period."
    assert d["revenue"][1].startswith("Fees minus the cost of delivering them (payments to liquidity providers, "
                                      "suppliers or validators). Not operating expenses.")
    assert d["holder_revenue"][1] == ("The part of revenue that reaches token holders in the period (burns, buybacks, "
                                      "distributions to stakers or lockers).")
    assert d["holder_share"][1] == "Holder revenue ÷ revenue."


def test_s13_every_project_declares_which_line_its_existing_revenue_cell_is():
    for p in config.PROJECTS:
        line, why = config.revenue_tier(p["name"])
        assert line in LINES and len(why) > 40, p["name"]
    assert config.revenue_tier("Aerodrome")[0] == "holder_revenue", "all of Aerodrome's revenue is paid to voters"
    assert config.revenue_tier("Uniswap")[0] == "revenue"
    with pytest.raises(KeyError):
        config.revenue_tier("No Such Project")                 # never a silent default


def test_s13_labels_say_the_line_and_never_substitute():
    s, note = config.revenue_tier_label("Uniswap", "Revenue Q0 ($)")
    assert s == " · revenue" and "REVENUE LINE: REVENUE" in note and "populated" in note
    s, note = config.revenue_tier_label("Aerodrome", "Protocol revenue, annualised")
    assert s == " · holder revenue" and "HOLDER REVENUE" in note and "not yet split" in note
    assert config.revenue_tier_label("Uniswap", "Market cap ($)") is None
    # a new line's column: numbers only where the project's round populated it
    s, note = config.revenue_tier_label("Uniswap", bw.CP_FEES_LINE_HEAD)
    assert s == " · fees" and "SECONDARY" in note
    s, note = config.revenue_tier_label("Uniswap", bw.CP_HREV_LINE_HEAD)
    assert s == " · holder revenue" and "PRIMARY" in note
    assert config.revenue_tier_label("Sky", bw.CP_REV_LINE_HEAD) is None
    t = config.not_yet_split_text("Sky")
    assert t.startswith("not yet split") and "REVENUE" in t and reader.code_of(t) == "not yet split"


def test_s13_uniswap_states_every_route_and_the_other_recipients():
    u = config.revenue_split("Uniswap")
    assert (u["fees"]["metric"], u["revenue"]["metric"], u["holder_revenue"]["metric"]) == (
        "fees_usd", "revenue_usd", "holder_value_revenue_usd")
    assert u["fees"]["primary"] is False and u["revenue"]["primary"] is False and u["holder_revenue"]["primary"] is True
    assert "UNISWAP LABS'" in u["other_recipients"] and "NONE of the three lines" in u["other_recipients"]
    assert "measured 0" in u["revenue"]["route"]                # the pre-switch take, read on chain
    ev = config.UNISWAP_REVENUE_EVIDENCE
    assert ev["pre_switch"]["block"] < 24_106_378 < ev["post_switch"]["block"]
    assert ev["pre_switch"]["v2_feeTo"] == "0x0000000000000000000000000000000000000000"
    assert "HOLDER SHARE" in config.PAYOUT_ABOVE_ONE["Uniswap"]


def test_u7_the_burn_trace_and_the_jar_sample_add_up_and_the_proxy_is_declared():
    """U7: the traced burn is the one-off + the 4,000-UNI mainnet releases + the 2,000-UNI bridged burns (+ dust); the
    revenue line is DefiLlama's RELEASE value, declared as a proxy, with the TokenJar inflow sample beside it."""
    bt = config.UNISWAP_REVENUE_EVIDENCE["burn_trace"]
    assert bt["tokens"] == pytest.approx(bt["one_off"] + 4000 * bt["releases_4000"] + 2000 * bt["bridged_2000"], abs=10)
    assert abs(bt["q0_traced"] - bt["q0_stored"]) / bt["q0_stored"] < 0.01
    jar = config.UNISWAP_REVENUE_EVIDENCE["token_jar_inflows"]
    assert jar["priced_usd"] > 0 and jar["lp_transfers"] > 0 and jar["defillama_ethereum_release_value_usd"] > 0
    route = config.revenue_split("Uniswap")["revenue"]["route"]
    assert "RELEASED" in route and "PROXY" in route and "SECONDARY" in route
    assert "DECLARED PROXY" in config.revenue_tier("Uniswap")[1]


def test_s13_the_definitions_tab_writes_the_three_lines_and_the_net_supply_change():
    wb = openpyxl.Workbook()
    ws = wb.active
    reader._write_revenue_line_defs(ws, 1)
    text = " ".join(str(c.value) for row in ws.iter_rows() for c in row if c.value)
    for key in ("fees", "revenue", "holder_revenue", "holder_share"):
        assert config.REVENUE_LINE_DEFS[key][1] in text
    assert "not yet split" in text and "CIRCULATING supply" in text and "Uniswap" in text


# ========================================================================================================= S8, units
def _circ(days, values, sources):
    return pd.DataFrame({"date": pd.to_datetime(days), "project": "Uniswap", "metric": "circulating_supply_used",
                         "value": values, "tier": 1, "source": sources})


def test_s8_the_change_is_dated_only_on_consecutive_days_on_one_basis(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Uniswap"]])
    a, b = "derived:circulating_used[circulating_supply_onchain]", "derived:circulating_used[circulating_supply]"
    days = ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-05", "2026-10-06", "2026-10-07"]
    g = {("Uniswap", "circulating_supply_used"): _circ(days, [100.0, 103.0, 101.0, 110.0, 111.0, 150.0],
                                                        [a, a, a, a, a, b])}
    bw._circulating_change_views(g)
    ch = g[("Uniswap", "circulating_change_tokens")]
    assert list(ch["date"].dt.strftime("%m-%d")) == ["10-02", "10-03", "10-06"]
    assert ch["value"].tolist() == [3.0, -2.0, 1.0]
    gaps = bw.CIRC_CHANGE_GAPS["Uniswap"]
    assert [d.strftime("%m-%d") for d, _w in gaps] == ["10-05", "10-07"]
    assert "no circulating reading" in gaps[0][1] and "basis changes" in gaps[1][1]


def test_s8_a_full_window_telescopes_to_closing_minus_opening(monkeypatch):
    monkeypatch.setattr(bw, "scoped_projects", lambda: [config.PROJECT_BY_NAME["Uniswap"]])
    days = pd.date_range("2026-07-14", "2026-10-12")                 # the opening day + 90 days
    vals = [600e6 + 1000.0 * i * i for i in range(len(days))]
    g = {("Uniswap", "circulating_supply_used"): _circ(days, vals, ["derived:circulating_used[x]"] * len(days))}
    bw._circulating_change_views(g, pd.Timestamp("2026-10-12"))
    ch = g[("Uniswap", "circulating_change_tokens")]
    assert len(ch) == 90 and ch["value"].sum() == pytest.approx(vals[-1] - vals[0])
    assert bw.CIRC_CHANGE_GAPS["Uniswap"] == []


def test_s8_cells_that_are_not_the_circulating_change_say_what_they_are():
    P = config.PROJECT_BY_NAME
    assert bw._a4_net_head_flag(P["Uniswap"])[0] == " burn only"
    assert bw._a4_net_head_flag(P["GEODNET"])[0] == " burn only"
    assert bw._a4_net_head_flag(P["Sky"])[0] == " issuance − burn"
    assert bw._a4_net_kind_flag(P["GEODNET"])[0] == " release − burn"
    assert bw._a4_net_kind_flag(P["Sky"])[0] == " issuance − burn"
    assert set(reader.CARRIED_SUFFIXES) == {" burn only", " issuance − burn", " release − burn"}
    assert "closing − opening circulating" in bw.MASTER_NSC_HEAD


# ========================================================================================================= U1, units
def test_u1_c5_holds_only_when_circulating_falls():
    assert hl.c5_holds(0.8, 0.075) is False, "circulating rising (+7.5% a year) is not a shrinking supply"
    assert hl.c5_holds(0.8, -0.01) is True
    assert hl.c5_holds(0.8, "n/a — closing − opening circulating is not measured") is False


def test_u1_c5_caveat_quotes_the_30_days_by_formula():
    r30 = hl.Ref("Business", 9, 12, "Uniswap", reader.REV30_HEADER, -0.40, "0.0%", None)
    b30 = hl.Ref("Business", 9, 13, "Uniswap", reader.BURN30_HEADER, -0.49, "0.0%", None)
    f = hl.c5_caveat_formula("Net holder yield is negative.", r30, b30)
    assert f.startswith('="Caveat: Net holder yield is negative. Over the latest 30 days, revenue "&TEXT(Business!L9,')
    assert "TEXT(Business!M9," in f and "against the 30 days before" in f and not hl.FORBIDDEN.search(f)
    assert hl.c5_caveat_formula("x.", None, b30) is None
    assert hl.c5_caveat_formula("x.", hl.Ref("Business", 9, 12, "Uniswap", "h", "n/a", "0.0%", None), b30) is None


# ================================================================================= the GATES, on the 15 real dumps
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from shared_build import plain_build
    rec = plain_build(tmp_path_factory, "pr1_revenue")
    assert "error" not in rec["stats"], rec["stats"]
    import xlcalc
    rec["wb"] = openpyxl.load_workbook(rec["audit"])
    rec["X"] = xlcalc.Workbook(rec["wb"])
    rec["rd"] = openpyxl.load_workbook(rec["reader"])
    rec["RX"] = xlcalc.Workbook(rec["rd"])
    return rec


def _table(ws):
    hr = next(r for r in range(1, 13) if str(ws.cell(r, 1).value or "").strip() == "Project")
    heads = {str(ws.cell(hr, c).value or ""): c for c in range(1, ws.max_column + 1)}
    rows = {}
    for r in range(hr + 1, ws.max_row + 1):
        v = ws.cell(r, 1).value
        if not v:
            break
        rows.setdefault(str(v), r)
    return heads, rows


def _col(heads, prefix):
    hits = [c for h, c in heads.items() if h.startswith(prefix)]
    assert len(hits) == 1, (prefix, hits)
    return hits[0]


def test_gate_every_headline_revenue_cell_has_a_line_label(built):
    """S13 GATE: every headline revenue cell (config.REVENUE_TIER_HEADS) that holds a number names its line on the
    number and in its comment; a new line's cell holds a number only where the project's round populated it."""
    bad = []
    for book, X in ((built["wb"], built["X"]), (built["rd"], built["RX"])):
        for ws in book.worksheets:
            try:
                heads, rows = _table(ws)
            except StopIteration:
                continue
            for h, c in heads.items():
                if not any(h.startswith(k) for k in config.REVENUE_TIER_HEADS):
                    continue
                for name, r in rows.items():
                    v = X.value(ws.title, r, c)
                    cell = ws.cell(r, c)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        if not any(s.replace('"', "'") in str(cell.number_format) for s in SUFFIXES) or \
                                "REVENUE LINE:" not in (cell.comment.text if cell.comment else ""):
                            bad.append((ws.title, h[:40], name, v, cell.number_format))
                    elif config.REVENUE_TIER_HEADS[next(k for k in config.REVENUE_TIER_HEADS if h.startswith(k))] \
                            != "@existing" and not config.revenue_split(name):
                        assert str(v).startswith("not yet split"), (ws.title, h, name, v)
    assert not bad, bad[:10]


def test_gate_the_new_lines_are_on_cp_and_every_reader_tab(built):
    cp_heads, cp_rows = _table(built["wb"]["CP Comparability"])
    for h in (bw.CP_FEES_LINE_HEAD, bw.CP_REV_LINE_HEAD, bw.CP_HREV_LINE_HEAD, bw.CP_HSHARE_HEAD):
        assert h in cp_heads, h
    comp, _r = _table(built["rd"][reader.COMPARISON])
    for h in ("Fees (S13), 90 days", "Revenue (S13), 90 days", "Holder revenue (S13), 90 days", "Holder share (S13)"):
        assert h in comp, h
    bus, _r = _table(built["rd"][reader.BUSINESS])
    for h in ("Fees, last 90 days", "Protocol revenue, last 90 days", "Holder revenue (S13), 90 days",
              "Holder share (S13)", reader.REV30_HEADER, reader.BURN30_HEADER):
        assert h in bus, h
    # the code is the reader's "not yet split", with the existing cell's line in the comment
    ws = built["rd"][reader.COMPARISON]
    heads, rows = _table(ws)
    c = heads["Revenue (S13), 90 days"]
    sky = ws.cell(rows["Sky"], c)
    assert sky.value == "not yet split" and "REVENUE" in sky.comment.text


def test_gate_uniswap_three_lines_are_the_routed_series(built):
    """U7: CP's three lines for Uniswap are the Data rows of the routed metrics; the share is the payout ratio."""
    wb, X = built["wb"], built["X"]
    heads, rows = _table(wb["CP Comparability"])
    r = rows["Uniswap"]
    data = wb["Data"]
    dh = {str(data.cell(1, c).value): c for c in range(1, data.max_column + 1)}
    q0c = next(c for h, c in dh.items() if h.startswith("Q0 (flow"))
    drow = {data.cell(i, 1).value: i for i in range(2, data.max_row + 1)}
    for head, m in ((bw.CP_FEES_LINE_HEAD, "fees_usd"), (bw.CP_REV_LINE_HEAD, "revenue_usd"),
                    (bw.CP_HREV_LINE_HEAD, "holder_value_revenue_usd")):
        assert X.value("CP Comparability", r, heads[head]) == pytest.approx(
            X.value("Data", drow[f"Uniswap|{m}"], q0c)), head
    share = X.value("CP Comparability", r, heads[bw.CP_HSHARE_HEAD])
    pay = X.value("CP Comparability", r, _col(heads, "Payout ratio — the project's own net basis"))
    assert isinstance(share, float) and share == pytest.approx(pay)
    note = wb["CP Comparability"].cell(r, heads[bw.CP_HSHARE_HEAD]).comment.text
    assert "HOLDER SHARE" in note and "Can read above 1" in note


def _endpoints(groups, name, asof):
    g = groups.get((name, "circulating_supply_used"))
    if g is None or g.empty:
        return None
    s = g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).set_index("date").sort_index()
    n = int(config.NET_SUPPLY_CIRC["window_days"])
    o, c = asof - pd.Timedelta(days=n), asof
    win = s.loc[o:c]
    if o not in s.index or c not in s.index or len(win) != n + 1 or win["source"].nunique() != 1:
        return None                                          # a hole or a basis change: not measured on one basis
    return float(s.loc[o, "value"]), float(s.loc[c, "value"])


def test_gate_net_supply_change_is_closing_minus_opening_circulating(built):
    """S8 GATE (tolerance declared in config.NET_SUPPLY_CIRC before measuring): on every project Master's NET SUPPLY
    CHANGE (tokens) = closing − opening circulating_supply_used read from the store; where the window has a hole or a
    basis change the cell is a reasoned n/a, never a number. The % a year is it × 365 ÷ 90 ÷ circulating now; Summary
    shows the same two figures."""
    wb, X, groups = built["wb"], built["X"], built["groups"]
    asof = pd.Timestamp(built["asof"]).normalize()
    tol = config.NET_SUPPLY_CIRC["tolerance"]
    heads, rows = _table(wb["Master"])
    ct, cp, cc = heads[bw.MASTER_NSC_HEAD], heads[bw.MASTER_NSC_PCT_HEAD], heads["Circulating supply (used)"]
    checked = 0
    for name, r in rows.items():
        v, pct, circ = X.value("Master", r, ct), X.value("Master", r, cp), X.value("Master", r, cc)
        ends = _endpoints(groups, name, asof)
        if ends is None:
            assert isinstance(v, str) and v.startswith("n/a —"), (name, v)
            continue
        o, c = ends
        assert isinstance(v, float), (name, v)
        assert abs(v - (c - o)) <= max(tol["abs_tokens"], tol["rel_of_opening"] * abs(o)), (name, v, c - o)
        assert pct == pytest.approx(v * 365 / 90 / circ, rel=1e-9), name
        checked += 1
    assert checked >= 10, checked
    # Summary reads the same cells
    rd, RX = built["rd"], built["RX"]
    sh, srows = _table(rd[reader.SUMMARY])
    for name, r in srows.items():
        if name in rows and isinstance(X.value("Master", rows[name], ct), float):
            assert RX.value(reader.SUMMARY, r, sh["Net supply change, 90 days"]) == pytest.approx(
                X.value("Master", rows[name], ct)), name


def test_gate_every_net_supply_cell_that_is_not_the_circulating_change_is_labelled(built):
    """S8: every A4 cell titled 'net supply change' holds the circulating change or says what it is on the number; on
    the reader the words are carried and the A4 tab names the figure 'Issuance − burn'."""
    bad = []
    for book, X in ((built["wb"], built["X"]),):
        ws = book["A4 Permanent Burn"]
        heads, rows = _table(ws)
        for h, c in heads.items():
            if "net supply change" not in h.lower():
                continue
            for name, r in rows.items():
                if isinstance(X.value(ws.title, r, c), float):
                    fmt = str(ws.cell(r, c).number_format)
                    if not any(f'"{s}"' in fmt for s in reader.CARRIED_SUFFIXES):
                        bad.append((h[:50], name, fmt))
    assert not bad, bad
    ws = built["rd"][reader.A4_TAB]
    heads, rows = _table(ws)
    assert "Issuance − burn, 90 days" in heads and "Net supply change, 90 days" not in heads
    c = heads["Issuance − burn, 90 days"]
    assert str(ws.cell(rows["Uniswap"], c).number_format).endswith('" burn only"')


def test_gate_c5_fires_only_when_circulating_falls_and_quotes_the_30_days(built):
    """U1: C5 quotes Summary's net supply change, which is the circulating change; it is built only when that is below
    0, and its caveat quotes the latest 30 days against the prior 30 by formula on Business."""
    rd, RX = built["rd"], built["RX"]
    sh, srows = _table(rd[reader.SUMMARY])
    net = RX.value(reader.SUMMARY, srows["Uniswap"], sh["Net supply change, % a year"])
    L = hl.Lookup(rd)
    try:
        card = hl.card_c5(L)
    except hl.Omit:
        card = None
    gro = RX.value(reader.SUMMARY, srows["Uniswap"], sh["Revenue growth, selected period"])
    assert (card is not None) == hl.c5_holds(gro, net)
    if not (isinstance(net, float) and net < 0):
        assert card is None, (net, "C5 must not claim a shrinking supply while circulating does not fall")
    bh, brows = _table(rd[reader.BUSINESS])
    r30 = RX.value(reader.BUSINESS, brows["Uniswap"], bh[reader.REV30_HEADER])
    b30 = RX.value(reader.BUSINESS, brows["Uniswap"], bh[reader.BURN30_HEADER])
    assert isinstance(r30, float) and isinstance(b30, float)
    if card is not None:
        f = card.caveat_formula
        assert f and "latest 30 days" in f and f"Business!{hl.get_column_letter(bh[reader.REV30_HEADER])}" in f
