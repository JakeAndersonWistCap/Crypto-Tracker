"""PR1B (Jake 2026-11-09), group circ: item 4 (circulating vs CoinGecko), item 5 (subsidy cover / leakage (ii) /
uniformity reasons), item 12 (project_freeze without pytest; declared headline renames read by the guard). No network."""
import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

import config
import measuring_guard as mg

ROOT = Path(__file__).resolve().parents[1]
MS_OLD, MS_NEW = f"CP | {config._MS_OLD}", f"CP | {config._MS_NEW}"
NSC_OLD = "Master | NET SUPPLY CHANGE Q0 (tokens) = issuance − burn"


@pytest.fixture
def round_1_renames(monkeypatch):
    """These tests pin ROUND 1's declared renames (MS_OLD -> MS_NEW, NSC_OLD): round 1B's own entries (PR1B.3, PR1B.8,
    PR1B.10b, dated 2026-11-09; MS_NEW -> the 90-day-mean header) are set aside — tested in tests/test_pr1b_burn.py."""
    monkeypatch.setattr(config, "HEADLINE_RENAMES", [e for e in config.HEADLINE_RENAMES if e.get("date") != "2026-11-09"])


# ============================================================ item 12a: project_freeze --compare runs without pytest

def _top_level_imports(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for node in tree.body:                                 # module level only: a function's own import is lazy
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split(".")[0])
    return out


@pytest.mark.parametrize("rel", ["project_freeze.py", "measuring_guard.py", "tests/real_dump.py"])
def test_the_freeze_path_imports_no_pytest_at_module_level(rel):
    """Jake's machine has no pytest: tests/real_dump.py imported it at the top, so `project_freeze.py --compare`
    (which builds through real_dump.to_store) failed at import. pytest is imported only where a test skips."""
    assert not _top_level_imports(ROOT / rel) & {"pytest", "_pytest"}, rel


BLOCK = r"""
import json, sys, tempfile
from pathlib import Path
class _NoPytest:                                # pytest is NOT INSTALLED: any import of it fails
    def find_spec(self, name, *a):
        if name.split(".")[0] in ("pytest", "_pytest"):
            raise ImportError("No module named " + repr(name) + " (blocked: Jake's machine has no pytest)")
sys.meta_path.insert(0, _NoPytest())
sys.path.insert(0, sys.argv[1])
import project_freeze as pf
import measuring_guard as mg
mg._payloads = lambda: [json.loads((Path(sys.argv[1]) / "fixtures" / "real_uniswap.json").read_text())]
with tempfile.TemporaryDirectory() as d:
    now = pf.cells(*pf._build(d), "Uniswap")
print(json.dumps({"cells": len(now), "pytest": sorted(m for m in sys.modules if m.split(".")[0] in ("pytest", "_pytest"))}))
"""


def test_project_freeze_builds_and_reads_cells_with_pytest_unimportable(tmp_path):
    """The whole build path project_freeze runs (real_dump.to_store, build_workbook, the reader, cells()) in a fresh
    interpreter where `import pytest` fails — on Uniswap's dump alone (one project: about a minute)."""
    script = tmp_path / "no_pytest.py"
    script.write_text(BLOCK, encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), str(ROOT)], capture_output=True, text=True, timeout=900,
                       cwd=tmp_path)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["pytest"] == [] and out["cells"] > 300, out


# ============================================================ item 12b: declared headline renames read by the guard

def test_round_1_entries_name_todays_headers_and_old_ones():
    """Every declared successor is a header the code writes today; no declared old key is one."""
    import build_workbook as bw
    today = {f"Master | {bw.MASTER_NSC_HEAD}", f"Master | {bw.MASTER_MINT_BURN_HEAD}", f"CP | {bw.MARKET_SHARE_HEAD}",
             f"CP | {bw.MARKET_SHARE_2_HEAD}"}
    for e in config.HEADLINE_RENAMES:
        assert e["kind"] in ("rename", "change") and e.get("why") and e.get("ref") and e.get("date"), e
        assert e["from"] not in today, e["from"]
        if e["from"] in (NSC_OLD, MS_OLD, f"CP | {config._MS2_OLD}"):
            # PR1B.10b (burn group, lead 2026-11-09): MS_OLD's round-1 successor was renamed again this round — the
            # chain's END is today's header
            end = config.headline_successor(e.get("project") or "Pendle", e["from"])["to"]
            assert end in today, end
            assert e.get("same_quantity_at") in (None, *today)


def test_successor_rename_change_project_override_and_chain(monkeypatch, round_1_renames):
    s = config.headline_successor("Pendle", MS_OLD)
    assert (s["to"], s["kind"], s["ref"]) == (MS_NEW, "rename", "PR1.S2")
    u = config.headline_successor("Uniswap", MS_OLD)
    assert (u["to"], u["kind"]) == (MS_NEW, "change"), "Uniswap's own entry (U4: the 90-day mean) wins"
    n = config.headline_successor("Uniswap", NSC_OLD)
    assert n["kind"] == "change" and n["same_quantity_at"].startswith("Master | Issuance − burn Q0")
    assert config.headline_successor("Uniswap", "CP | something never renamed") is None
    # a chain: round 1 A -> B, this round B -> C — an old snapshot's A is read at C; one change makes it a change
    monkeypatch.setattr(config, "HEADLINE_RENAMES", config.HEADLINE_RENAMES + [
        {"from": MS_NEW, "to": "CP | MARKET SHARE, 90-day mean (x)", "kind": "rename", "ref": "PR1B.8",
         "date": "2026-11-09", "why": "w"}])
    c = config.headline_successor("Pendle", MS_OLD)
    assert (c["to"], c["kind"], c["ref"]) == ("CP | MARKET SHARE, 90-day mean (x)", "rename", "PR1.S2+PR1B.8")
    assert config.headline_successor("Uniswap", MS_OLD)["kind"] == "change"
    # a declared loop ends
    monkeypatch.setattr(config, "HEADLINE_RENAMES", [{"from": "A | x", "to": "A | y", "kind": "rename"},
                                                     {"from": "A | y", "to": "A | x", "kind": "rename"}])
    assert config.headline_successor("P", "A | x")["to"] == "A | y"


def _snap(heads):
    return {"bases": {}, "headlines": heads}


def test_compare_reads_a_declared_rename_like_for_like_and_a_change_as_a_change(round_1_renames):
    before = _snap({"Pendle": {MS_OLD: 0.10},
                    "Uniswap": {MS_OLD: 0.2956, NSC_OLD: -5594001.2}})
    after = _snap({"Pendle": {MS_NEW: 0.10},
                   "Uniswap": {MS_NEW: 0.3004, "Master | NET SUPPLY CHANGE Q0 (tokens) = closing − opening "
                               "circulating supply": -649634.0,
                               "Master | Issuance − burn Q0 (tokens) — a component of the supply change, NOT the net "
                               "supply change": -5594001.2}})
    declared = []
    assert mg.compare(before, after, [], declared=declared) == []
    text = "\n".join(declared)
    assert len(declared) == 3
    assert "RENAMED (declared PR1.S2, 2026-11-05): Pendle" in text and "0.1 before, 0.1 after (like for like)" in text
    assert "CHANGE OF QUANTITY (declared PR1.S2/U4, 2026-11-05): Uniswap" in text
    assert ("CHANGE OF QUANTITY (declared PR1.S8, 2026-11-05): Uniswap" in text
            and "the old quantity reads -5,594,001 at Master | Issuance − burn Q0" in text)


def test_a_declared_successor_with_no_value_is_still_a_lost_headline(round_1_renames):
    before = _snap({"Uniswap": {MS_OLD: 0.2956}})
    bad = mg.compare(before, _snap({"Uniswap": {}}), [], declared=[])
    assert len(bad) == 1 and bad[0].startswith(f"HEADLINE LOST Uniswap {MS_OLD}")
    assert f"declared changed to {MS_NEW} (PR1.S2/U4, 2026-11-05): no value there either" in bad[0]
    # an undeclared rename is lost as before, with no suffix
    bad = mg.compare(_snap({"Uniswap": {"CP | OLD": 1.0}}), _snap({"Uniswap": {"CP | NEW": 1.0}}), [])
    assert bad == ["HEADLINE LOST Uniswap CP | OLD: 1 before, no value after"]


def test_the_live_guard_reads_the_renames_too(round_1_renames):
    """Jake's live guard (--newer) compares the previous run's snapshot (old headers) with the live build."""
    prev = {"Uniswap": {NSC_OLD: -5594001.2, MS_OLD: 0.2956}}
    heads = {"Uniswap": {"Master | NET SUPPLY CHANGE Q0 (tokens) = closing − opening circulating supply": -649634.0,
                         MS_NEW: 0.3004}}
    declared = []
    bad, acc = mg.lost_against_previous(prev, heads, None, "2026-11-08", on="2026-11-09", declared=declared)
    assert bad == [] and acc == [] and len(declared) == 2
    assert all(d.startswith("CHANGE OF QUANTITY") for d in declared)
    assert "the old quantity's declared place Master | Issuance − burn Q0" in "\n".join(declared)
    bad, _ = mg.lost_against_previous(prev, {"Uniswap": {}}, None, "2026-11-08", on="2026-11-09")
    assert len(bad) == 2 and all("no value there either" in b for b in bad)


def test_round_1_renames_against_the_committed_guard_baseline(round_1_renames):
    """The committed baseline (today's headers) against the same headlines under round 1's OLD headers: no HEADLINE
    LOST, every market-share header listed as declared (12 projects' first basis, 2 second basis)."""
    base = json.loads((ROOT / "tests" / "fixtures" / "guard_baseline.json").read_text(encoding="utf-8"))
    back = {e["to"]: e["from"] for e in config.HEADLINE_RENAMES if not e.get("project")}
    old = {p: {back.get(k, k): v for k, v in cells.items()} for p, cells in base["headlines"].items()}
    declared = []
    bad = mg.compare({"bases": {}, "headlines": old}, {"bases": {}, "headlines": base["headlines"]}, [],
                     declared=declared)
    assert not [b for b in bad if b.startswith("HEADLINE LOST")], bad
    n = sum(k in back.values() for cells in old.values() for k in cells)
    assert n >= 14 and len(declared) == n


def test_sanity_and_continuity_read_a_value_held_under_an_older_declared_name(round_1_renames):
    import openpyxl
    import credibility
    import sanity
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "CP Comparability"
    ws["B2"] = "n/a — no reading"
    ws["B3"] = 0.3
    hcs = [{"project": "Uniswap", "sheet": "CP Comparability", "cell": "B2", "header": config._MS_NEW},
           {"project": "Pendle", "sheet": "CP Comparability", "cell": "B3", "header": config._MS_NEW}]
    prev = {"Uniswap": {MS_OLD: 0.2956}, "Pendle": {MS_OLD: 0.25}}
    hits = sanity.lost_headlines(wb, prev, hcs, "2026-11-08")
    assert len(hits) == 1 and hits[0][0] == "Uniswap" and hits[0][1] == "headline_lost"
    assert "0.2956 in the previous run (2026-11-08) (as 'MARKET SHARE vs its category" in hits[0][2]
    assert "declared change PR1.S2/U4" in hits[0][2]
    row = credibility.continuity_row("Pendle", hcs, prev, "2026-11-08")
    assert row is not None and row["value"] == 1.0 and "was 0.25" in row["note"]
    assert credibility.continuity_row("Pendle", hcs, {"Pendle": {"CP | undeclared": 0.25}}) is None


# ============================================================ item 5: one true reason (subsidy cover, leakage (ii), uniformity)

@pytest.fixture(scope="module")
def built(tmp_path_factory):
    sys.path.insert(0, str(ROOT / "tests"))
    from shared_build import plain_build
    rec = plain_build(tmp_path_factory, "pr1b_circ")
    import openpyxl
    rec["awb"] = openpyxl.load_workbook(rec["audit"])
    rec["rwb"] = openpyxl.load_workbook(rec["reader"])
    return rec


def _row_cells(ws, project):
    hr = next(r for r in range(1, 15) if ws.cell(r, 1).value == "Project")
    r = next(r for r in range(hr + 1, ws.max_row + 1) if ws.cell(r, 1).value == project)
    return {str(ws.cell(hr, c).value or ""): ws.cell(r, c) for c in range(2, ws.max_column + 1)}


SUBSIDY_HEADS = ("SUBSIDY COVER", "Subsidy cover incl. demand-side", "LEAKAGE (ii)")


def test_item5_cp_reads_one_true_reason(built):
    cp = next(ws for ws in built["awb"].worksheets if ws.title.startswith("CP"))
    u = _row_cells(cp, "Uniswap")
    decl = config.declared_na_text("Uniswap", "SUBSIDY COVER")
    assert decl.startswith("n/a — any UNI liquidity incentives are paid from the growth budget, held whole in (d); "
                           "no published split (") and "read 2026-11-09" in decl
    for pre in SUBSIDY_HEADS:
        cell = next(c for h, c in u.items() if h.startswith(pre))
        assert cell.value == decl, (pre, cell.value)
    uni = next(c for h, c in u.items() if h.startswith("UNIFORMITY"))
    assert uni.value == "n/a — no staker yield exists (no UNI staking)"
    # every other project keeps its own reason (no declaration)
    c = _row_cells(cp, "Chainlink")
    assert str(next(v for h, v in c.items() if h.startswith("SUBSIDY COVER")).value).startswith(
        "n/a — no supply-side incentive (b) series on file")


def test_item5_flows_and_comparison_say_the_same(built):
    """Flows' (b) line says 'included in (d)' (FLOW_LINE_EVIDENCE); the Comparison codes now say the same."""
    ev = config.FLOW_LINE_EVIDENCE[("Uniswap", "Incentives paid beside: supply side (b)")]
    assert ev["verdict"] == "included_in" and "(d)" in ev["line"]
    cmp_ = built["rwb"]["Comparison"]
    hdr = {str(cmp_.cell(4, c).value): c for c in range(1, cmp_.max_column + 1)}
    r = next(r for r in range(5, cmp_.max_row + 1) if cmp_.cell(r, 1).value == "Uniswap")
    for h in ("Subsidy cover (x)", "Leakage (ii)"):
        cell = cmp_.cell(r, hdr[h])
        assert cell.value == "n/a" and "held whole in (d); no published split" in cell.comment.text, h
        assert "no supply-side incentive (b) series on file" not in cell.comment.text


def test_item5_done_check_accepts_the_declared_reasons(built):
    from datetime import date

    import done_check as dc
    res = {p: bl for p, _st, bl, _w in dc.check(built["rwb"], built["awb"], date(2026, 10, 13))}
    bad = [b.line() for b in res["Uniswap"]
           if any(x in (b.header + b.why) for x in ("ubsidy", "eakage", "NIFORMITY", "staker yield"))]
    assert not bad, bad


def test_item5_declared_reason_needs_its_match_source_and_date(monkeypatch):
    import done_check as dc
    t = config.declared_na_text("Uniswap", "UNIFORMITY")
    assert config.declared_na_reason("Uniswap", f"{t}\n\nWorking: audit file, CP!X5.")
    assert config.declared_na_reason("Pendle", t) is None, "a declaration is the project's own"
    hits = [{"tab": "audit:CP Comparability", "project": "Uniswap", "header": "UNIFORMITY", "code": "n/a",
             "reason": t}]
    assert dc.judge_suspects(hits, "Uniswap", []) == []
    monkeypatch.setitem(config.DECLARED_NA_REASONS, "Uniswap",
                        [dict(e, read_on="") for e in config.DECLARED_NA_REASONS["Uniswap"]])
    assert config.declared_na_reason("Uniswap", t) is None, "no read date: declares nothing"
    assert len(dc.judge_suspects(hits, "Uniswap", [])) == 1, "undeclared: a rule-3 suspect blocker as before"


# ============================================================ item 4: circulating vs CoinGecko — the wallet set found

BASIS = json.loads((ROOT / "tests" / "fixtures" / "pr1b_uniswap_coingecko_basis.json").read_text(encoding="utf-8"))
SET = ("uniswap_foundation", "aera_vault", "labs_growth_recipient")


def _repro(d):
    """CoinGecko's basis reproduced from the day's balances: net total − Timelock − the three wallets."""
    return 1e9 - d["dead"] - d["timelock"] - sum(d[k] for k in SET)


def test_item4_coingecko_excess_is_three_wallets_day_by_day():
    """CoinGecko's implied non-circulating − the Timelock, every day 09-11..11-09, is the UF + the Aera vault + Labs'
    growth recipient. CoinGecko's restated history (before 11-03) stamps a day with its END (the next day's first
    block); its live points (11-03 on) with its START — every day matches one of the two within 30,000 UNI."""
    days = BASIS["days"]
    assert len(days) == 60 and days[0]["date"] == "2026-09-11" and days[-1]["date"] == "2026-11-09"
    for i, d in enumerate(days):
        start = _repro(d) - d["coingecko_circulating"]
        end = _repro(days[i + 1]) - d["coingecko_circulating"] if i + 1 < len(days) else start
        assert min(abs(start), abs(end)) <= 30_001, (d["date"], start, end)
        if d["date"] >= "2026-11-03":
            assert abs(start) <= 8_001, (d["date"], start)
        # without the three, the gap is millions on every day — the Timelock alone is not CoinGecko's basis
        assert abs(_repro(d) + sum(d[k] for k in SET) - d["coingecko_circulating"]) > 14_000_000


def test_item4_jakes_two_readings_to_the_token_and_the_175m_step():
    by = {d["date"]: d for d in BASIS["days"]}
    # Jake's 11-05 reading (11:47 UTC) is the state after the UF's 00:10 transfer, before its 11-06 inflow: 11-06's
    # first block; his 11-09 reading: 11-09's
    for day, at in (("2026-11-05", "2026-11-06"), ("2026-11-09", "2026-11-09")):
        got = sum(by[at][k] for k in SET)
        assert round(got) == BASIS["sources"]["jake"][day], (day, got)
    step = sum(by["2026-11-07"][k] for k in SET) - sum(by["2026-11-06"][k] for k in SET)
    assert round(step, 2) == 1_750_000.00, "the +1.75M: the UF's inflow of 2026-11-06 19:40"


def test_item4_the_three_wallets_are_stored_beside_ours_not_out_of_it():
    p = config.PROJECT_BY_NAME["Uniswap"]
    keys = [k for k, c in p["contracts"].items() if c.get("metric_override") == "coingecko_excluded_holdings_tokens"]
    addrs = {p["contracts"][k]["address"].lower() for k in keys}
    assert addrs == {BASIS["wallets"][k].lower() for k in SET}
    assert "coingecko_excluded_holdings_tokens" in config.metrics_for_project(p)
    spec = config.circulating_onchain("Uniswap")
    assert "coingecko_excluded_holdings_tokens" not in spec["subtract"], "ours is unchanged (Jake's decision)"
    assert config.coingecko_excludes_holdings("Uniswap", "2026-10-13") == ()
    assert config.coingecko_excludes_holdings("Uniswap", "2026-11-09") == ("coingecko_excluded_holdings_tokens",)


def _live_rows(upto="2026-11-09", with_set=True):
    """Live-shaped rows (Jake's store as of 11-09): the day's balances and CoinGecko's figure, 10-14..upto. CoinGecko's
    LIVE figure was total − Timelock on 10-13 (the dumps, to the token) and on the new basis by 11-02 (Jake's +2.89%):
    modelled as total − Timelock before coingecko_excludes_from, its published point from it (VERIFIED ONLY ON THIS
    FIXTURE: the switch day itself is in Jake's store)."""
    frm = config.coingecko_excludes_from("Uniswap")
    out = []
    for d in BASIS["days"]:
        if not ("2026-10-14" <= d["date"] <= upto):
            continue
        cg = d["coingecko_circulating"] if d["date"] >= frm else 1e9 - d["dead"] - d["timelock"]
        vals = {"total_supply_gross": 1e9, "burn_address_balance": d["dead"], "treasury_holding_tokens": d["timelock"],
                "noncirculating_holding_tokens": d["merkle_distributor"],
                "circulating_supply": cg, "total_supply": 1e9 - d["dead"],
                "total_supply_coingecko": 1e9 - d["dead"]}
        if with_set:
            vals["coingecko_excluded_holdings_tokens"] = sum(d[k] for k in SET)
        src = {"circulating_supply": "coingecko", "total_supply_coingecko": "coingecko"}
        out += [{"date": d["date"], "metric": m, "value": v, "source": src.get(m, f"chain:ethereum:{m}"), "tier": 2,
                 "fetched_at": f"{d['date']}T12:00:00Z"} for m, v in vals.items()]
    return out


class _Out:
    def __init__(self):
        self.items, self.log = [], []

    def review_item(self, *a, **k):
        self.items.append((a, k))


def _review(rows):
    import pandas as pd

    from fetch import circulating as fc
    h = pd.DataFrame(rows).assign(project="Uniswap")
    o = _Out()
    fc.check(o, h, [config.PROJECT_BY_NAME["Uniswap"]])
    return [x for x in o.items if x[0][2] == "onchain_vs_coingecko"]


def test_item4_review_queue_judges_like_for_like_on_the_live_shape():
    assert _review(_live_rows()) == [], "11-09: ours + distributor − the three = CoinGecko (within the 2%)"
    bad = _review(_live_rows(with_set=False))
    assert bad == [], "a day after the switch without the three stored is no comparison, never ours un-adjusted"
    # the switch is dated: on 10-13-shaped data (CoinGecko still total − Timelock) nothing is taken out
    assert _review(_live_rows(upto="2026-10-20")) == []


def test_item4_credibility_in_circ_passes_like_for_like_on_the_live_shape(tmp_path, monkeypatch):
    from real_dump import evaluate_real, load
    pl = load("Uniswap")
    pl = dict(pl, metrics=pl["metrics"] + _live_rows(), meta=dict(pl["meta"], asof="2026-11-09"))
    o = evaluate_real(tmp_path, monkeypatch, "Uniswap", payload=pl, asof="2026-11-09")
    v = o["in_circ"]
    assert str(v["verdict"]).startswith("PASS"), v
    assert abs(v["ours"] / v["ref"] - 1) < 0.001, v
    # THE FIVE CELLS THAT READ in_circ (Jake: "drives Uniswap's 5 CHECK cells") no longer read CHECK from it
    for k in DEPENDANTS:
        assert not str(o[k]["verdict"]).startswith("CHECK"), (k, o[k])
    # the three not stored on the as-of day: no like-for-like figure — never ours un-adjusted (unknown is not 0)
    pl2 = dict(pl, metrics=load("Uniswap")["metrics"] + _live_rows(with_set=False))
    (tmp_path / "b").mkdir()
    o2 = evaluate_real(tmp_path / "b", monkeypatch, "Uniswap", payload=pl2, asof="2026-11-09")
    v2 = o2["in_circ"]
    assert not str(v2["verdict"]).startswith("PASS") and v2["ours"] in (None, ""), v2
    assert all(str(o2[k]["verdict"]).startswith("CHECK") for k in DEPENDANTS)


DEPENDANTS = ("a4_burn_yield", "a4_net_change_pct", "cp_float", "cp_hvy", "cp_nhy")


def test_item4_the_dumps_as_of_1013_are_judged_as_before(tmp_path, monkeypatch):
    """Before coingecko_excludes_from CoinGecko counted the three: nothing is taken out (the dumps: PASS, 0.000%)."""
    from real_dump import evaluate_real
    v = evaluate_real(tmp_path, monkeypatch, "Uniswap")["in_circ"]
    assert str(v["verdict"]).startswith("PASS") and abs(v["ours"] / v["ref"] - 1) < 1e-4, v
