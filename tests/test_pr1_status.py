"""PROJECT ROUND 1, group `status` (Jake 2026-11-05).

S6:  Coverage and Credibility give ONE verdict per cell: a headline is VERIFIED only when its band is green AND the
     Credibility tab passes it (PASS / VERIFIED FINDING); a Credibility CHECK is a Coverage CHECK; a figure read only
     from an aggregator (DefiLlama, CoinGecko, Token Terminal, Artemis, growthepie) is never VERIFIED.
S7:  the price cross-checks (Coinbase, DefiLlama) compare at the instant of the price the workbook uses.
S9:  a code that contradicts a number fails the gate (contradictions.py), run on the reader built from the 15 dumps.
S11: Summary's key column in each key's own unit (A1 a change, %, A2-A4 ratios, x); the payout ratio in x.
S12: the Flows reconciliation blocks: a Q0 token-flow residual beyond tolerance, unexplained, takes the project's
     supply cells off green and out of the ranks.
"""
from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402
import reader  # noqa: E402
from shared_build import plain_build  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return plain_build(tmp_path_factory, "pr1_status")


# ============================================================================================================= S7
def _px_long(rows):
    import pandas as pd
    return pd.DataFrame([{"date": pd.Timestamp(d), "project": "Uniswap", "metric": m, "value": v, "source": s,
                          "fetched_at": f} for d, m, v, s, f in rows])


def test_s7_a_row_read_during_its_day_is_a_spot_at_its_fetch_time():
    import pandas as pd

    import credibility as cred
    long = _px_long([("2026-10-12", "price_usd", 7.76, "coingecko", "2026-10-13T08:33:00Z"),
                     ("2026-10-13", "price_usd", 8.45, "coingecko", "2026-10-13T16:22:38Z")])
    inst = cred.price_instants(long, "Uniswap")
    assert inst[pd.Timestamp("2026-10-12")] == pd.Timestamp("2026-10-12")              # CoinGecko's 00:00 point
    assert inst[pd.Timestamp("2026-10-13")] == pd.Timestamp("2026-10-13 16:22:38")     # the run's spot


def test_s7_the_price_used_is_checked_at_its_own_instant():
    import pandas as pd

    import credibility as cred
    asof = pd.Timestamp("2026-10-13")
    base = [("2026-10-12", "price_usd", 7.7646, "coingecko", "2026-10-13T08:33:00Z"),
            ("2026-10-12", "price_usd_coinbase", 7.756, "coinbase_exchange:UNI-USD:open", "2026-10-13T08:33:00Z"),
            ("2026-10-13", "price_usd", 8.4535, "coingecko", "2026-10-13T16:22:38Z"),
            # the daily OPEN of the as-of day: 00:00, not the 16:22 spot — +9.01% apart, a FALSE gap
            ("2026-10-13", "price_usd_coinbase", 7.755, "coinbase_exchange:UNI-USD:open", "2026-10-13T08:33:00Z")]
    long = _px_long(base)
    assert cred.same_instant(long, "Uniswap", pd.Timestamp("2026-10-13"), "price_usd_coinbase") is None
    v, d, how = cred._price_at_use("Uniswap", {}, long, asof, ref="price_usd_coinbase", side="ours")
    r, _d, _h = cred._price_at_use("Uniswap", {}, long, asof, ref="price_usd_coinbase", side="ref")
    assert (v, r, d) == (7.7646, 7.756, "2026-10-12") and "no reference read at its instant" in how
    # with Coinbase's 1-minute candle at the spot's minute stored, the spot itself is checked
    long = _px_long(base + [("2026-10-13", "price_usd_coinbase_at", 8.44, "coinbase_exchange:UNI-USD:open@2026-10-13T16:22:00Z",
                             "2026-10-14T08:00:00Z")])
    v, d, how = cred._price_at_use("Uniswap", {}, long, asof, ref="price_usd_coinbase", side="ours")
    r, _d, _h = cred._price_at_use("Uniswap", {}, long, asof, ref="price_usd_coinbase", side="ref")
    assert (v, r, d) == (8.4535, 8.44, "2026-10-13") and "16:22 UTC" in how
    # a reference stamped at another minute is not the same instant
    far = _px_long(base + [("2026-10-13", "price_usd_coinbase_at", 8.0, "coinbase_exchange:UNI-USD:open@2026-10-13T12:00:00Z",
                            "2026-10-14T08:00:00Z")])
    assert cred.same_instant(far, "Uniswap", pd.Timestamp("2026-10-13"), "price_usd_coinbase") is None
    for ref in ("price_usd_coinbase", "price_usd_llama"):
        rows = cred.price_inputs("Uniswap")
        r_ = rows["in_price" if ref.endswith("coinbase") else "in_price_llama"]
        assert r_["ours"]["py"] == "price_at_use" and r_["ref"]["formula"] == "price_at_use"


def test_s7_the_payment_weighted_overlap_leaves_out_a_spot_day():
    import pandas as pd

    import credibility as cred
    rows = []
    for d in pd.date_range("2026-08-01", "2026-10-13"):
        spot = d == pd.Timestamp("2026-10-13")
        f = "2026-10-13T16:22:38Z" if spot else (d + pd.Timedelta(days=1)).strftime("%Y-%m-%dT08:00:00Z")
        rows += [(d, "holders_revenue_usd", 100.0, "defillama", f), (d, "price_usd", 1.5 if spot else 1.0, "coingecko", f),
                 (d, "price_usd_coinbase", 1.0, "coinbase_exchange:AERO-USD:open", f)]
    long = pd.DataFrame([{"date": d, "project": "Aerodrome", "metric": m, "value": v, "source": s, "fetched_at": f}
                         for d, m, v, s, f in rows])
    asof = pd.Timestamp("2026-10-13")
    ours, last, how = cred._price_overlap("Aerodrome", {}, long, asof, side="ours")
    ref, _l, _h = cred._price_overlap("Aerodrome", {}, long, asof, side="ref")
    assert last == "2026-10-12" and ours == pytest.approx(ref) and "largest single-day difference 0.00%" in how


def test_s7_xref_reads_the_references_at_the_spots_instant():
    import pandas as pd

    from fetch import base as fbase
    from fetch import xref
    from fetch.base import FetchOutput
    long = pd.DataFrame([{"date": "2026-11-04", "project": "Uniswap", "metric": "price_usd", "value": 8.0,
                          "source": "coingecko", "fetched_at": "2026-11-04T18:42:13Z"},
                         {"date": "2026-11-03", "project": "Uniswap", "metric": "price_usd", "value": 7.9,
                          "source": "coingecko", "fetched_at": "2026-11-04T18:42:13Z"}])
    calls = []

    class Http:
        def get(self, url, params=None, **_k):
            calls.append((url, dict(params or {})))
            if "coinbase" in url:
                t0 = int(pd.Timestamp("2026-11-04 18:42").timestamp())
                return [[t0, 7.9, 8.1, 8.01, 8.02, 10.0]]
            return {"coins": {url.rsplit("/", 1)[1]: {"price": 8.03, "timestamp": int(pd.Timestamp(
                "2026-11-04 18:43:00").timestamp())}}}
    old = fbase.today
    try:
        xref.today = lambda: pd.Timestamp("2026-11-05 09:00")
        x = xref.CrossRefs(http=Http(), daily=object(), robots=lambda u: (True, ""), stored_long=long)
        assert x.spot_instants("Uniswap") == [(pd.Timestamp("2026-11-04"), pd.Timestamp("2026-11-04 18:42:13"))]
        out = FetchOutput()
        x._at_spot([config.PROJECT_BY_NAME["Uniswap"]], config.CREDIBILITY_XREF, out)
    finally:
        xref.today = old
    cb = [c for c in calls if "coinbase" in c[0]][0]
    assert cb[1]["granularity"] == 60 and cb[1]["start"].startswith("2026-11-04T18:42:00")
    assert any(f"/{int(pd.Timestamp('2026-11-04 18:42:13').timestamp())}/" in c[0] for c in calls)
    frames = pd.concat(out.frames, ignore_index=True) if hasattr(out, "frames") else None
    assert frames is not None
    got = {r.metric: (r.value, r.source) for r in frames.itertuples()}
    assert got["price_usd_coinbase_at"] == (8.01, "coinbase_exchange:UNI-USD:open@2026-11-04T18:42:00Z")
    assert got["price_usd_llama_at"][0] == 8.03 and got["price_usd_llama_at"][1].endswith("@2026-11-04T18:43:00Z")


def _col(ws, header, row=4):
    return next(c for c in range(1, ws.max_column + 1) if ws.cell(row, c).value == header)


def _rows(ws):
    return {ws.cell(r, 1).value: r for r in range(5, ws.max_row + 1) if isinstance(ws.cell(r, 1).value, str)}


# ============================================================================================================ S11
def test_s11_each_key_has_its_own_unit():
    assert reader.key_format(1) == reader.FMT_PCT, "A1's key is a 1-year change: -0.135 reads -13.5%, never -0.1x"
    for a in (2, 3, 4):
        assert reader.key_format(a) == reader.FMT_X


def test_s11_the_payout_ratio_is_in_x_everywhere():
    cols = {k: fmt for k, _h, _s, fmt, _d, _f in reader.COMPARISON_COLS}
    assert cols["payout"] == reader.FMT_X
    import inspect
    src = inspect.getsource(bw)
    i = src.index('("Payout ratio — gross = credited holder value ÷ fees"')
    assert "FMT_X" in src[i:i + 400] and "FMT_PCT" not in src[i:i + 400]


def test_s11_summary_formats_on_the_built_reader(built):
    wb = openpyxl.load_workbook(built["reader"])
    ws = wb["Summary"]
    kc, pc = _col(ws, "Archetype key"), _col(ws, "Payout ratio")
    arch_of = {}
    for name, r in _rows(ws).items():
        a = str(ws.cell(r, 3).value or "")
        if a.startswith("A") and a[1:2].isdigit():
            arch_of[name] = int(a[1])
    seen = set()
    for name, r in _rows(ws).items():
        c = ws.cell(r, kc)
        if isinstance(c.value, str) and c.value.startswith("=") and name in arch_of:
            want = reader.key_format(arch_of[name])
            assert c.number_format.split(";")[0] == want.split(";")[0], (name, c.number_format)
            seen.add(arch_of[name])
        p = ws.cell(r, pc)
        if isinstance(p.value, str) and p.value.startswith("="):
            assert '"x"' in p.number_format, (name, p.number_format)
    assert seen, "no key on Summary was a number"


# ============================================================================================================= S9
def _mini_reader(cells: dict, comments: dict | None = None):
    """A reader-shaped workbook: {sheet: {(row, col): value}}, headers on row 4, projects in column A."""
    from openpyxl.comments import Comment
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sh, cs in cells.items():
        ws = wb.create_sheet(sh)
        ws.cell(4, 1, "Project")
        for (r, c), v in cs.items():
            ws.cell(r, c, v)
            if comments and (sh, r, c) in comments:
                ws.cell(r, c).comment = Comment(comments[(sh, r, c)], "t")
    return wb


def test_s9_the_detector_finds_a_code_that_denies_a_non_zero_quantity():
    import contradictions
    wb = _mini_reader({
        "Summary": {(4, 2): "Total dilution, % of float", (4, 3): "Net supply change, % a year", (5, 1): "X",
                    (5, 2): 0.11, (5, 3): "by design", (6, 1): "Y", (6, 2): "blocked", (6, 3): "by design"},
        "A4 Permanent Burn": {(4, 2): "Burn ÷ issuance (x)", (4, 3): "Issuance or release, 90 days",
                              (4, 4): "Revenue needed for crossover", (4, 5): "Total dilution, % of float a year",
                              (5, 1): "X", (5, 2): 1.1, (5, 3): 0, (5, 4): "n/a", (5, 5): "by design"}},
        {("A4 Permanent Burn", 5, 4): "n/a — no issuance: burn needs no revenue to exceed it"})
    found = {(x["project"], x["sheet"], x["cell"]) for x in contradictions.find(wb)}
    assert found == {("X", "Summary", "C5"), ("X", "A4 Permanent Burn", "C5"), ("X", "A4 Permanent Burn", "D5"),
                     ("X", "A4 Permanent Burn", "E5")}, found
    # nothing non-zero elsewhere: "by design" contradicts no number; an honest unknown never counts
    wb2 = _mini_reader({"Summary": {(4, 2): "Total dilution, % of float", (5, 1): "X", (5, 2): "n/a",
                                    (4, 3): "Emissions, last 90 days (tokens)", (5, 3): 5.0}},
                       {("Summary", 5, 2): "Not formed on this tab: the unlock leg is not measured here"})
    assert contradictions.find(wb2) == []


def test_s9_an_emissions_leg_is_zero_only_by_declaration():
    assert bw.emissions_absent("Ether.fi", "emissions_tokens"), "ETHFI IS NOT EMITTED (config not_applicable)"
    assert bw.emissions_absent("Uniswap", "emissions_tokens"), "UNI: a fixed supply, emissions not carried"
    assert bw.emissions_absent("Pendle", "emissions_tokens") is None
    assert bw.emissions_absent("Sky", "emissions_tokens") is None
    assert bw.live_emission_metrics("Ether.fi") == [] and bw.live_emission_metrics("Pendle") == ["emissions_tokens"]


def test_s9_net_supply_not_carried_is_unknown_never_by_design():
    p = {"name": "P", "archetypes": [3]}
    src = reader.Src("Master", "X1", text="n/a — gross_issuance_tokens (q0): not applicable to this project — not "
                                           "applicable to this project's archetypes")
    dil = reader.Src("CP Comparability", "A1", num=0.05)
    why = reader.nsc_not_carried(p, src, dil)
    assert "unknown here, not zero" in why and "5.00%" in why and "not applicable" not in why
    assert reader.nsc_not_carried(p, reader.Src("Master", "X1", num=1.0), dil) == ""


def test_s9_gate_no_code_contradicts_a_number_on_the_built_reader(built):
    """THE GATE (Jake 2026-11-05): on the reader built from the 15 dumps, no cell says a quantity is absent or does not
    apply while the same quantity reads non-zero elsewhere. Before PR1 it found 24 (Uniswap A4 U5/Y5/Z5/AT5, Ether.fi
    A3 E/BB/BD/BE/BO, Sky A3 BO / A4 AT, Aethir A2 AD, Summary N/O of Pendle / Morpho / Ether.fi, A3's 'Emissions in
    force' for six projects)."""
    import contradictions
    found = contradictions.find(openpyxl.load_workbook(built["reader"]))
    assert not found, "\n".join(contradictions.lines(found))


def test_s9_uniswap_a4_reads_the_release_the_key_divides_by(built):
    import xlcalc
    wb = openpyxl.load_workbook(built["reader"])
    X = xlcalc.Workbook(wb)
    ws = wb["A4 Permanent Burn"]
    r = _rows(ws)["Uniswap"]
    au = openpyxl.load_workbook(built["audit"])
    XA = xlcalc.Workbook(au)
    wa = au["A4 Permanent Burn"]
    ra = next(i for i in range(5, wa.max_row + 1) if wa.cell(i, 1).value == "Uniswap")
    ca = next(c for c in range(1, wa.max_column + 1) if str(wa.cell(4, c).value or "").startswith("UNLOCK DILUTION Q0"))
    release = XA.value("A4 Permanent Burn", ra, ca)
    assert isinstance(release, float) and release > 0
    assert X.value(ws.title, r, _col(ws, "Issuance or release, 90 days")) == pytest.approx(release)
    for h in ("Revenue needed for crossover", "Governance lever"):
        assert isinstance(X.value(ws.title, r, _col(ws, h)), float), h


# ============================================================================================================ S12
def _flow_rows(name, pct, residual, opening=1e9, status="CHECK — residual"):
    return [{"Project": name, "Statement": "Token flow", "Line": "Opening circulating", "Q0": opening},
            {"Project": name, "Statement": "Token flow", "Line": "Residual (closing − expected)", "Q0": residual},
            {"Project": name, "Statement": "Token flow", "Line": "Residual ÷ opening", "Q0": pct},
            {"Project": name, "Statement": "Token flow", "Line": "Status", "Q0": status}]


def test_s12_an_unexplained_residual_beyond_tolerance_blocks():
    rows = _flow_rows("A", 0.05, 5e7) + _flow_rows("B", 0.004, 4e6, status="OK") + _flow_rows("C", None, None)
    blocks = bw.flow_blocks(rows, explained={})
    assert set(blocks) == {"A"} and "+5.00%" in blocks["A"] and "not ranked" in blocks["A"]
    # explained only by a QUANTIFIED declaration that leaves the residual within tolerance
    assert bw.flow_blocks(rows, explained={"A": {"amount_tokens": 4.8e7}}) == {}
    assert set(bw.flow_blocks(rows, explained={"A": {"amount_tokens": 1e7}})) == {"A"}
    assert set(bw.flow_blocks(_flow_rows("D", -0.006, -6e6), explained={})) == {"D"}
    assert config.FLOW_RESIDUAL_EXPLAINED == {}, "no explanation is declared in this round"


def test_s12_a_blocked_projects_statement_cells_leave_green():
    from openpyxl.styles import PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Master"
    for c, h in enumerate(("Project", "Circulating", "Price"), start=1):
        ws.cell(4, c, h)
    ws.cell(5, 1, "A")
    for c, m in ((2, "circulating_supply_used"), (3, "price_usd")):
        ws.cell(5, c, f'=INDEX(Data!$J$2:$J$9,MATCH($A5&"|{m}",Data!$A$2:$A$9,0))').fill = \
            PatternFill("solid", fgColor=bw.FILL_GREEN.fgColor.rgb)
    rows = [{"Project": "A", "Statement": "Token flow", "Line": "Opening circulating",
             "Series": "circulating_supply_used (start of the quarter)"}]
    moved = bw.block_flow_greens(wb, {"A": "FLOWS BLOCK (PR1.S12): residual"}, rows, {})
    assert moved == [("A", "Master!B5", "Circulating")]
    assert str(ws["B5"].fill.fgColor.rgb)[-6:] == str(bw.FILL_AMBER.fgColor.rgb)[-6:]
    assert ws["B5"].comment.text.startswith("FLOWS BLOCK")
    assert str(ws["C5"].fill.fgColor.rgb)[-6:] == str(bw.FILL_GREEN.fgColor.rgb)[-6:], "price is not on the statement"
    assert ("A", "circulating_supply_used") in bw.FLOW_BLOCKED_SERIES


def test_s12_on_the_built_files_a_blocked_project_is_neither_green_on_its_statement_nor_ranked(built):
    au = openpyxl.load_workbook(built["audit"])
    blocks = reader.flow_blocks_of(au, bw)
    assert set(blocks) == {"Plume", "Hyperliquid"}, blocks     # Q0 residuals +5.00% and +8.38% on the 15 dumps
    ws = au["Master"]
    green = str(bw.FILL_GREEN.fgColor.rgb)[-6:]
    for r in range(5, ws.max_row + 1):
        if ws.cell(r, 1).value in blocks:
            for c in range(2, ws.max_column + 1):
                v = ws.cell(r, c).value
                if isinstance(v, str) and "|circulating_supply_used" in v:
                    assert str(ws.cell(r, c).fill.fgColor.rgb)[-6:] != green, (ws.cell(r, 1).value, c)
    rd = openpyxl.load_workbook(built["reader"])["Summary"]
    rc = _col(rd, "Rank in archetype")
    for name, r in _rows(rd).items():
        if name in blocks:
            assert rd.cell(r, rc).value in ("unranked", "parked"), name


# ============================================================================================================= S6
def test_s6_one_verdict_per_cell():
    import pandas as pd

    import coverage_audit as ca
    raw = '=INDEX(Data!$J$2:$J$9,MATCH($A5&"|fees_usd",Data!$A$2:$A$9,0))'
    rows = [{"Tab": "A1 Infrastructure", "Project": "P", "Status": s, "Reason / route / error": "",
             "_addr": f"A1 Infrastructure!{c}5", "_raw": raw}
            for s, c in (("VERIFIED", "B"), ("VERIFIED", "C"), ("LIVE-UNVERIFIED", "D"), ("VERIFIED (derived)", "E"),
                         ("VERIFIED", "F"))]
    verdicts = {"A1 Infrastructure!B5": ("UNVERIFIABLE", "no source"), "A1 Infrastructure!C5": ("PASS", ""),
                "A1 Infrastructure!D5": ("CHECK", "off 3%"),
                "A1 Infrastructure!E5": ("DOCUMENTED LIMITATION (inputs)", "")}
    asof = pd.Timestamp("2026-10-13")
    groups = {("P", "fees_usd"): pd.DataFrame({"date": pd.to_datetime(["2026-10-12"]), "value": [1.0],
                                               "source": ["defillama"]})}
    ca.one_verdict(rows, verdicts, groups, asof)
    # B: Credibility UNVERIFIABLE; C: PASS but read only from DefiLlama; D: CHECK; E: a limitation; F: no Credibility
    # row, aggregator only
    assert [r["Status"] for r in rows] == ["LIVE-UNVERIFIED", "LIVE-UNVERIFIED", "CHECK", "LIVE-UNVERIFIED",
                                           "LIVE-UNVERIFIED"]
    assert rows[1]["Reason / route / error"].startswith("AGGREGATOR ONLY") and "defillama" in \
        rows[1]["Reason / route / error"]
    assert rows[0]["Reason / route / error"].startswith("Credibility: UNVERIFIABLE")
    groups[("P", "fees_usd")]["source"] = ["chain:ethereum:fees"]
    row = {"Tab": "A1 Infrastructure", "Project": "P", "Status": "VERIFIED", "Reason / route / error": "",
           "_addr": "A1 Infrastructure!C5", "_raw": raw}
    ca.one_verdict([row], verdicts, groups, asof)
    assert row["Status"] == "VERIFIED", "on chain and passed: one verdict, VERIFIED"


def test_s6_aggregator_only_follows_a_views_inputs():
    import pandas as pd

    import coverage_audit as ca
    d, asof = pd.to_datetime(["2026-10-12"]), pd.Timestamp("2026-10-13")
    groups = {("P", "circulating_supply_used"): pd.DataFrame({"date": d, "value": [1.0],
                                                              "source": ["view:used[circulating_supply]"]}),
              ("P", "circulating_supply"): pd.DataFrame({"date": d, "value": [1.0], "source": ["coingecko"]})}
    assert ca.aggregator_only(groups, "P", "circulating_supply_used", asof) == ["coingecko"]
    groups[("P", "circulating_supply")]["source"] = ["chain:x:total"]
    assert ca.aggregator_only(groups, "P", "circulating_supply_used", asof) == []
    assert config.is_aggregator_source("defillama_coins:base:0x94") and not config.is_aggregator_source("chain:x")


def test_s6_on_the_built_file_coverage_and_credibility_agree(built):
    import csv

    import coverage_audit as ca
    import xlcalc
    au = openpyxl.load_workbook(built["audit"])
    verdicts = ca.credibility_verdicts(au, xlcalc.Workbook(au))
    heads = {(h["project"], h["sheet"], h["header"]): h["cell"] for h in built["headlines"]}
    with open(built["dir"] / "coverage_audit.csv", encoding="utf-8") as f:
        cov = list(csv.DictReader(f))
    n = 0
    for r in cov:
        cell = heads.get((r["Project"], r["Tab"], r["Column"]))
        v = verdicts.get(f"{r['Tab']}!{cell}", ("", ""))[0] if cell else ""
        if not v:
            continue
        n += 1
        if r["Status"] in ("VERIFIED", "VERIFIED (derived)"):
            assert v.startswith(ca.CRED_PASSES), (r["Project"], r["Tab"], r["Column"], r["Status"], v)
        assert not (v.startswith("CHECK") and r["Status"] in ("VERIFIED", "VERIFIED (derived)", "LIVE-UNVERIFIED")), \
            (r["Project"], r["Column"], r["Status"], v)
    assert n > 300
