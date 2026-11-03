"""P4-Q8 (Jake 2026-11-03): ONE WORKBOOK. Plain asserts, no network.

The edition layout is the default token_metrics.xlsx: the reader is built ONCE with the Highlights tab first, the look
applied and Project notes in place; there is no `--edition` flag and no second reader file. The audit file is unchanged.
The card floor (the lead's reading): below reader_layout.MIN_CARDS (5) rendered cards the reader is still written and
its Highlights tab holds no card, only the statement that the floor was not met, how many rendered and each card's
unmet condition. The committed real dumps render 4 cards (C5, C7, C8, C9), so the production build here shows that
statement; tests/test_highlights.py lowers the floor where the cards themselves are under test.

Built twice on the committed real dumps (fixtures/real_*.json, a temp store): ONE as shipped, and BARE with
reader_layout.apply_layout made a no-op - the reader as the default run wrote it before this phase - to compare the audit
file cell for cell and the reader's tabs value for value.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import highlights as H  # noqa: E402
import reader  # noqa: E402
import reader_layout  # noqa: E402
import reader_look  # noqa: E402

OLD = "char" + "lie"           # the old edition's name, spelled so that this file does not carry it either
ORDER = ["Highlights", "Summary", "A1 Infrastructure", "A2 Coordination", "A3 Revenue Buyback", "A4 Permanent Burn",
         "Comparison", "Business", "Project notes", "Charts", "About", "Definitions"]


def _build(d: Path, layout: bool) -> dict:
    import build_workbook as bw
    import config
    import measuring_guard as mg
    import store as sm
    from real_dump import to_store
    getattr(config, "RESTATEMENT_ACTIVE", {}).clear()
    db = d / "metrics.db"
    asofs = []
    for pl in mg._payloads():
        to_store(pl, db)
        asofs.append(pl["meta"]["asof"])
    reader_layout.LAST.clear()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(bw, "_SCOPE", list(bw.PROJECTS))
        mp.setattr(sm, "DB_PATH", str(db))
        mp.setitem(sys.modules, "recalc", None)
        mp.setenv("TOKEN_METRICS_STORE_VALUES", "1")
        if not layout:
            mp.setattr(reader_layout, "apply_layout", lambda wb, ctx: {})
        st = sm.Store(str(db))
        try:
            bw.build_workbook(st, d / "audit.xlsx", asof=pd.Timestamp(max(asofs)), reader_path=d / "reader.xlsx")
        finally:
            st.close()
    assert "error" not in bw.READER_STATS, bw.READER_STATS
    getattr(config, "RESTATEMENT_ACTIVE", {}).clear()
    return {"dir": d, "audit": d / "audit.xlsx", "reader": d / "reader.xlsx", "stats": dict(bw.READER_STATS),
            "last": dict(reader_layout.LAST), "asof": pd.Timestamp(max(asofs))}


@pytest.fixture(scope="module")
def one(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("q8_one"), layout=True)


@pytest.fixture(scope="module")
def bare(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("q8_bare"), layout=False)


# ============================================================================================ one file, its layout
def test_one_reader_file_only_and_no_second_reader(one):
    assert sorted(p.name for p in one["dir"].glob("*.xlsx")) == ["audit.xlsx", "reader.xlsx"]
    assert not [p for p in one["dir"].iterdir() if OLD in p.name.lower()]


def test_highlights_first_then_the_tabs_in_the_layout_order_and_project_notes_present(one):
    wb = openpyxl.load_workbook(one["reader"])
    visible = [n for n in wb.sheetnames if wb[n].sheet_state == "visible"]
    assert visible == ORDER, visible
    assert wb.sheetnames[len(ORDER):] == [reader.DATA_SHEET, reader.SERIES_SHEET]
    assert wb.active.title == "Highlights" and sum(ws.sheet_view.tabSelected for ws in wb.worksheets) == 1
    notes = wb[reader.NOTES]
    assert notes.max_row > 10 and any(notes.cell(r, 1).font.b for r in range(1, notes.max_row + 1))
    assert one["stats"]["tabs"][:len(ORDER)] == ORDER                     # the run's reader line names them so


def test_the_look_is_applied(one):
    wb = openpyxl.load_workbook(one["reader"])
    for name, colour in reader_look.TAB_COLOURS.items():
        assert wb[name].sheet_properties.tabColor.rgb[-6:] == colour, name
    for name in ORDER:
        ws = wb[name]
        assert ws.sheet_view.showGridLines is False and ws.sheet_view.zoomScale == reader_look.ZOOM, name
    for name in reader_look.DATA_TABS:
        ws = wb[name]
        assert ws.freeze_panes is not None, name
        head = ws.cell(reader_look.HEADER_ROW, 1)
        assert head.font.name == reader_look.BODY and head.font.b and head.font.color.rgb[-6:] == reader_look.WHITE
    comp = wb[reader.COMPARISON]
    rules = [r.type for rng in comp.conditional_formatting for r in rng.rules]
    assert "dataBar" in rules
    fmts = {c.number_format for ws in wb.worksheets for row in ws.iter_rows() for c in row}
    assert reader_look.TOKEN_FMT in fmts and reader.FMT_TOK not in fmts


def test_the_floor_statement_when_fewer_than_five_cards_render(one):
    """The production floor (5) on the fixture numbers (4 render): the reader is written; its Highlights tab holds no
    card, only the statement - how many rendered and each unmet condition."""
    last = one["last"]
    assert reader_layout.MIN_CARDS == 5
    assert last["floor_met"] is False and last["cards"] == [] and last["rendered"] == ["C5", "C7", "C8", "C9"]
    ws = openpyxl.load_workbook(one["reader"])["Highlights"]
    text = [c.value for row in ws.iter_rows() for c in row if c.value is not None]
    assert text[:2] == [H.TITLE, H.FLOOR_NOTE]
    assert text[2] == "Highlights: only 4 of 9 cards render, 5 are needed. Cards that did not render:"
    assert text[-1] == "Cards that did: C5, C7, C8, C9"
    unmet = text[3:-1]
    assert [u.split(" ")[0].rstrip(":") for u in unmet] == ["C1", "C2", "C3", "C4", "C6"], unmet
    assert unmet == [o.strip() for o in last["omitted"]]                  # the same text the refusal printed
    assert not any(isinstance(v, str) and v.startswith("=") for v in text)   # no chip: no card at all
    assert all(not H.FORBIDDEN.search(v) for v in text)
    # and the run says so
    assert any("FLOOR NOT MET" in ln for ln in last["lines"])


def test_the_run_log_says_the_floor_was_not_met(monkeypatch, caplog, capsys):
    import token_metrics as tm
    monkeypatch.setattr(reader_layout, "LAST", {"cards": [], "rendered": ["C5", "C7"], "omitted": ["C1 x: y"],
                                                "floor_met": False, "look": "reader_look.apply_look applied",
                                                "lines": ["", "HIGHLIGHTS (the reader's first tab): FLOOR NOT MET"]})
    with caplog.at_level(logging.WARNING):
        tm._highlights_report(logging.getLogger("q8"))
    assert any("HIGHLIGHTS FLOOR NOT MET: 2 of 9 card(s) rendered (C5, C7), 5 needed" in r.getMessage()
               and "token_metrics.xlsx is written" in r.getMessage() for r in caplog.records)
    out = capsys.readouterr().out
    assert "READER LAYOUT" in out and "FLOOR NOT MET" in out
    caplog.clear()
    monkeypatch.setattr(reader_layout, "LAST", dict(reader_layout.LAST, floor_met=True, cards=["C5"]))
    with caplog.at_level(logging.WARNING):
        tm._highlights_report(logging.getLogger("q8"))
    assert not caplog.records


# ============================================================================================ the flag is gone
def test_the_edition_flag_is_rejected_with_a_clear_message(capsys):
    import token_metrics as tm
    args = tm.parse_args(["--no-fetch"])
    assert not hasattr(args, "edition")
    for argv in (["--no-fetch", "--edition", OLD], ["--edition"], ["--edition", "x", "--no-fetch"]):
        with pytest.raises(SystemExit) as e:
            tm.parse_args(argv)
        assert e.value.code == 2
        err = capsys.readouterr().err
        assert "--edition was removed" in err and "python token_metrics.py --no-fetch" in err, err


def test_no_caller_passes_the_flag_and_build_workbook_takes_no_edition():
    import inspect

    import build_workbook as bw
    assert "edition" not in inspect.signature(bw.build_workbook).parameters
    assert "edition" not in inspect.signature(reader.build_reader).parameters
    assert not hasattr(bw, "write_edition")
    for ps1 in (ROOT / "windows").glob("*.ps1"):
        assert "--edition" not in ps1.read_text(encoding="utf-8", errors="replace"), ps1


# ============================================================================================ the audit file
def _stamp(v):
    """The Run Log's build time (the clock, not the data) is the one cell two builds may differ in."""
    return re.sub(r"workbook built \d{4}-\d\d-\d\d \d\d:\d\d UTC", "workbook built <time>", v) \
        if isinstance(v, str) else v


def _cells(path: Path, data_only: bool = False) -> dict:
    wb = openpyxl.load_workbook(path, data_only=data_only)
    out = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.value is not None or c.comment is not None:
                    out[(ws.title, c.coordinate)] = (_stamp(c.value), c.number_format,
                                                     c.comment.text if c.comment else None)
        out[(ws.title, "#dims")] = (ws.max_row, ws.max_column, ws.sheet_state)
    return out, wb.sheetnames


def test_the_audit_file_is_identical_to_the_build_without_the_layout(one, bare):
    for data_only in (False, True):
        a, names_a = _cells(bare["audit"], data_only)
        b, names_b = _cells(one["audit"], data_only)
        assert names_a == names_b
        assert a.keys() == b.keys()
        diff = [k for k in a if a[k] != b[k]]
        assert not diff, diff[:10]
    assert len(a) > 20000


def test_the_reader_tabs_hold_the_same_values_as_without_the_layout(one, bare):
    """Every tab but Highlights: value / formula and comment identical; the number format identical except the look's
    one declared change (token counts as 1.2m / 3.4bn)."""
    a, names_a = _cells(bare["reader"])
    b, names_b = _cells(one["reader"])
    assert sorted(names_b) == sorted(names_a + ["Highlights"])
    b = {k: v for k, v in b.items() if k[0] != "Highlights"}
    assert {k for k in a if k[1] != "#dims"} == {k for k in b if k[1] != "#dims"}
    for k, v in a.items():
        if k[1] == "#dims":
            continue
        w = b[k]
        assert (w[0], w[2]) == (v[0], v[2]), k
        assert w[1] == v[1] or (v[1] == reader.FMT_TOK and w[1] == reader_look.TOKEN_FMT), (k, v[1], w[1])
    av, _ = _cells(bare["reader"], data_only=True)
    bv, _ = _cells(one["reader"], data_only=True)
    assert [k for k in av if k[1] != "#dims" and av[k][0] != bv[k][0]] == []
    assert (bare["dir"] / "reader_column_map.csv").read_bytes() == (one["dir"] / "reader_column_map.csv").read_bytes()


def test_friday_checks_pass_on_the_one_reader(one):
    import friday_check as fc
    wb = openpyxl.load_workbook(one["reader"])
    assert fc.check_ranks(wb) == []
    assert fc.check_about_counts(wb) == []
    assert fc.check_headers(wb) == []
    assert fc.check_no_blank(wb) == []
    assert fc.check_parked(wb, one["asof"]) == []
    assert fc.check_comments(wb) == []
    assert fc._asof_of_reader(wb) == one["asof"].date()


# ============================================================================================ the name is gone
def _tracked() -> list:
    """The repo's tracked files (git ls-files), or every file under the repo when it is not a git checkout (the gate
    runs on an archive of HEAD)."""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, timeout=60)
        if out.returncode == 0 and out.stdout.strip():
            return [ROOT / p for p in out.stdout.splitlines()]
    except (OSError, subprocess.SubprocessError):
        pass
    skip = {".git", "__pycache__", ".pytest_cache", ".ruff_cache"}
    files = []
    for base, dirs, names in os.walk(ROOT):
        dirs[:] = [x for x in dirs if x not in skip]
        files += [Path(base) / n for n in names]
    return files


def test_the_old_name_is_in_no_file_name_and_no_file_but_spec():
    files = [p for p in _tracked() if p.name != "SPEC.md" or p.parent != ROOT]
    assert len(files) > 100
    assert [str(p.relative_to(ROOT)) for p in files if OLD in str(p.relative_to(ROOT)).lower()] == []
    hits = []
    for p in files:
        if p.suffix.lower() not in (".py", ".md", ".ps1", ".txt", ".csv", ".json", ".yaml", ".yml", ".sql", ".toml",
                                    ".cfg", ".ini", ".bat", ".sh") or not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace").lower()
        if OLD in text:
            hits.append(str(p.relative_to(ROOT)))
    assert hits == [], hits
