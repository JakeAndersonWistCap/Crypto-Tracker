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
            assert e["to"] in today, e["to"]
            assert e.get("same_quantity_at") in (None, *today)


def test_successor_rename_change_project_override_and_chain(monkeypatch):
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


def test_compare_reads_a_declared_rename_like_for_like_and_a_change_as_a_change():
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


def test_a_declared_successor_with_no_value_is_still_a_lost_headline():
    before = _snap({"Uniswap": {MS_OLD: 0.2956}})
    bad = mg.compare(before, _snap({"Uniswap": {}}), [], declared=[])
    assert len(bad) == 1 and bad[0].startswith(f"HEADLINE LOST Uniswap {MS_OLD}")
    assert f"declared changed to {MS_NEW} (PR1.S2/U4, 2026-11-05): no value there either" in bad[0]
    # an undeclared rename is lost as before, with no suffix
    bad = mg.compare(_snap({"Uniswap": {"CP | OLD": 1.0}}), _snap({"Uniswap": {"CP | NEW": 1.0}}), [])
    assert bad == ["HEADLINE LOST Uniswap CP | OLD: 1 before, no value after"]


def test_the_live_guard_reads_the_renames_too():
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


def test_round_1_renames_against_the_committed_guard_baseline():
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


def test_sanity_and_continuity_read_a_value_held_under_an_older_declared_name():
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
