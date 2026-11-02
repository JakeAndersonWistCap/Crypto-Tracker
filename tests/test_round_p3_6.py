"""ROUND P3-6 (Jake 2026-11-02, "scale before adding"): the Project notes tab.

The archetype-tab columns that measure ONE project's own mechanism (reader.PROJECT_NOTE_COLUMNS, listed with the reason
in docs/p3_6_project_notes_columns.md) move off A1-A4 to a new reader tab "Project notes" — after Business, before
Charts — one block per project: a project row, then label | value | short definition. The value is the archetype tab's
own cell, unchanged. Keys, ranks, the trajectory block and the generic columns stay; the audit file is unchanged.

Built twice on the committed real dumps by the measuring guard's own build (measuring_guard.build — the fixture store
plus its probe rows, the reader beside the audit file): BEFORE with the list emptied (the build as it was, bar an empty
Project notes tab) and AFTER as shipped. Then cell for cell: the audit file, the archetype tabs' remaining columns (by
header — the letters shift), every other data tab, the moved cells, the column map, the guard's headlines.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import reader  # noqa: E402

DOC = ROOT / "docs" / "p3_6_project_notes_columns.md"
ARCH = (reader.A1_TAB, reader.A2_TAB, reader.A3_TAB, reader.A4_TAB)
BASELINE = ROOT / "tests" / "fixtures" / "guard_baseline.json"


def _guard_build(d: Path, notes: bool) -> dict:
    """measuring_guard.build on `d` — the audit file (wb.xlsx) and the reader beside it — with the Project notes list
    as shipped (notes=True) or emptied (the build before P3-6). Returns the snapshot, the files and the state."""
    import config
    import measuring_guard as mg
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    getattr(config, "RESTATEMENT_ACTIVE", {}).clear()
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "recalc", None)
        mp.setenv("TOKEN_METRICS_STORE_VALUES", "1")
        if not notes:
            mp.setattr(reader, "PROJECT_NOTE_COLUMNS", ())
            mp.setattr(reader, "NOTE_OWNER", {})
        snap = mg.build(d, previous=base.get("headlines"))
        out = {"snap": snap, "audit": d / "wb.xlsx", "reader": d / "reader.xlsx",
               "map": d / "reader_column_map.csv", "locations": dict(reader.HEADLINE_LOCATIONS),
               "stats": dict(reader.STATS), "base": base}
    getattr(config, "RESTATEMENT_ACTIVE", {}).clear()
    return out


@pytest.fixture(scope="module")
def before(tmp_path_factory):
    return _guard_build(tmp_path_factory.mktemp("p36_before"), notes=False)


@pytest.fixture(scope="module")
def after(tmp_path_factory):
    return _guard_build(tmp_path_factory.mktemp("p36_after"), notes=True)


# ================================================================================================ helpers
def _cell(c) -> tuple:
    """Everything a reader cell shows: value or formula, number format, comment, fill colour, font colour / italic /
    bold, horizontal alignment."""
    fill = c.fill.fgColor.rgb if c.fill is not None and c.fill.fill_type else None
    f = c.font
    return (c.value, c.number_format, c.comment.text if c.comment else None, fill,
            (f.color.rgb if f is not None and f.color is not None else None), bool(f.i), bool(f.b),
            c.alignment.horizontal)


def _headers(ws) -> list:
    return [ws.cell(4, c).value for c in range(1, ws.max_column + 1)]


def _keyed_columns(ws) -> dict:
    """{(header, nth occurrence): column index} — 'Quarter change' appears more than once on a tab."""
    seen: dict = {}
    out = {}
    for c, h in enumerate(_headers(ws), start=1):
        if h is None:
            continue
        seen[h] = seen.get(h, 0) + 1
        out[(h, seen[h])] = c
    return out


def _rows(ws) -> dict:
    return {ws.cell(r, 1).value: r for r in range(5, ws.max_row + 1)
            if ws.cell(r, 1).value and ws.cell(r, 2).value is not None}


def _dims(ws) -> dict:
    """{column index: (width, hidden, outline level)} — openpyxl reads a run of like columns as one range."""
    out = {}
    for d in ws.column_dimensions.values():
        if d.min is None:
            continue
        for c in range(d.min, (d.max or d.min) + 1):
            out[c] = (d.width, bool(d.hidden), d.outlineLevel)
    return out


def _block_label(ws, c: int) -> str:
    for k in range(c, 0, -1):
        if ws.cell(3, k).value:
            return str(ws.cell(3, k).value)
    return ""


def notes_blocks(ws) -> dict:
    """{project: {label: row}} of the Project notes tab: a project row is bold in column A; the rows under it are its
    columns."""
    out: dict = {}
    cur = None
    for r in range(5, ws.max_row + 1):
        a = ws.cell(r, 1).value
        if a is None:
            continue
        if ws.cell(r, 1).font.b:
            cur = out.setdefault(a, {})
            continue
        if cur is not None:
            cur[a] = r
    return out


def doc_rows() -> list:
    """The 'Moved to Project notes' table of the doc: [(tab, header, project, reason)]."""
    text = DOC.read_text(encoding="utf-8")
    sec = text.split("## Moved to Project notes", 1)[1].split("\n## ", 1)[0]
    out = []
    for line in sec.splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0] in ARCH:
            out.append(tuple(cells))
    return out


# ================================================================================================ the list
def test_the_moved_list_is_the_doc():
    assert doc_rows() == [tuple(x) for x in reader.PROJECT_NOTE_COLUMNS]
    assert len({(t, h) for t, h, _o, _w in reader.PROJECT_NOTE_COLUMNS}) == len(reader.PROJECT_NOTE_COLUMNS)
    for _t, h, _o, _w in reader.PROJECT_NOTE_COLUMNS:
        assert len(h) <= reader.MAX_HEADER, h
    # Jake's named examples are on it; no key, rank or trajectory column is
    heads = {h for _t, h, _o, _w in reader.PROJECT_NOTE_COLUMNS}
    for named in ("Still in old vePENDLE (tokens)", "Umbrella staked (Aave, $)", "Sky Savings Rate",
                  "Of which CCIP fees", "Of which edge rewards"):
        assert named in heads, named
    assert not any(h.startswith(("Rank in", "Key:")) or ", latest" in h or "qtr" in h for h in heads)


def test_each_moved_column_left_its_tab_and_sits_on_project_notes_unchanged(before, after):
    rb, ra = openpyxl.load_workbook(before["reader"]), openpyxl.load_workbook(after["reader"])
    vb = openpyxl.load_workbook(before["reader"], data_only=True)
    va = openpyxl.load_workbook(after["reader"], data_only=True)
    notes = ra[reader.NOTES]
    blocks = notes_blocks(notes)
    for tab, head, owner, _why in reader.PROJECT_NOTE_COLUMNS:
        assert head not in _headers(ra[tab]), (tab, head)                     # gone from its archetype tab
        assert _headers(rb[tab]).count(head) == 1, (tab, head)                # it was there, once
        assert head in blocks.get(owner, {}), (owner, head, blocks.get(owner))
        r = blocks[owner][head]
        got, got_v = notes.cell(r, 2), va[reader.NOTES].cell(r, 2)
        # the owner's own cell on the tab it was on (Sky has no A1 row: its Sky Savings Rate is the A3 cell, the same
        # macro series)
        src_tab = tab if owner in _rows(rb[tab]) else next(t for t, h, o, _w in reader.PROJECT_NOTE_COLUMNS
                                                            if h == head and o == owner and owner in _rows(rb[t]))
        c = _headers(rb[src_tab]).index(head) + 1
        want = rb[src_tab].cell(_rows(rb[src_tab])[owner], c)
        assert _cell(got) == _cell(want), (tab, head, owner)
        assert got_v.value == vb[src_tab].cell(want.row, c).value, (tab, head, owner)
        # the label carries the column's definition; the third cell a short one naming the tab it came from
        assert notes.cell(r, 1).comment.text == rb[src_tab].cell(4, c).comment.text
        assert tab in str(notes.cell(r, 3).value)


def test_project_notes_tab_shape_and_no_blank_cell_without_a_code(after):
    ra = openpyxl.load_workbook(after["reader"])
    names = ra.sheetnames
    assert names.index(reader.NOTES) == names.index(reader.BUSINESS) + 1
    assert names.index(reader.CHARTS) == names.index(reader.NOTES) + 1
    ws = ra[reader.NOTES]
    assert [ws.cell(4, c).value for c in (1, 2, 3)] == ["Project and column", "Value", "Definition"]
    assert all(len(str(ws.cell(4, c).value)) <= reader.MAX_HEADER for c in (1, 2, 3))
    blocks = notes_blocks(ws)
    owners = {o for _t, _h, o, _w in reader.PROJECT_NOTE_COLUMNS}
    assert set(blocks) == owners
    assert sum(len(v) for v in blocks.values()) == len({(o, h) for _t, h, o, _w in reader.PROJECT_NOTE_COLUMNS})
    for r in range(5, ws.max_row + 1):
        if ws.cell(r, 1).value is None:
            continue
        for c in (1, 2, 3):
            v = ws.cell(r, c).value
            assert v is not None and str(v).strip(), f"{reader.NOTES}!{ws.cell(r, c).coordinate} is blank"
            if isinstance(v, str) and v in reader.CODES:
                assert ws.cell(r, c).comment and ws.cell(r, c).comment.text.strip(), ws.cell(r, c).coordinate
    assert after["stats"]["blank_unexplained"] == 0, after["stats"]["blank_cells"]
    # cell FILL keeps its one meaning (confidence): the tab adds none of its own — only the header row's navy, as on
    # every tab, and a moved cell's own fill
    moved_cells = {(r, 2) for b in blocks.values() for r in b.values()}
    for row in ws.iter_rows(min_row=5):
        for c in row:
            if c.fill is not None and c.fill.fill_type and (c.row, c.column) not in moved_cells:
                raise AssertionError(f"{c.coordinate} has a fill")


# ================================================================================================ nothing else moves
def test_the_archetype_tabs_remaining_columns_are_unchanged_cell_for_cell(before, after):
    rb, ra = openpyxl.load_workbook(before["reader"]), openpyxl.load_workbook(after["reader"])
    moved = {(t, h) for t, h, _o, _w in reader.PROJECT_NOTE_COLUMNS}
    for tab in ARCH:
        wb_, wa = rb[tab], ra[tab]
        kb, ka = _keyed_columns(wb_), _keyed_columns(wa)
        assert [h for h, _n in ka] == [h for h, _n in kb if (tab, h) not in moved], tab     # same columns, same order
        assert _rows(wb_) == _rows(wa), tab
        for key, cb in kb.items():
            if (tab, key[0]) in moved:
                continue
            ca = ka[key]
            assert _block_label(wb_, cb) == _block_label(wa, ca), (tab, key)
            assert _cell(wb_.cell(4, cb)) == _cell(wa.cell(4, ca)), (tab, key)
            for r in range(5, wb_.max_row + 1):
                assert _cell(wb_.cell(r, cb)) == _cell(wa.cell(r, ca)), (tab, key, r)
            assert _dims(wb_).get(cb) == _dims(wa).get(ca), (tab, key)
        for r in (1, 2):
            assert wb_.cell(r, 1).value == wa.cell(r, 1).value
        assert wb_.freeze_panes == wa.freeze_panes
        # conditional formats (arrows, codes) stay on the same columns, by header
        def cf(ws):
            out = set()
            for rng in ws.conditional_formatting:
                for rule in rng.rules:
                    col = re.match(r"([A-Z]+)", str(rng.sqref)).group(1)
                    out.add((ws[f"{col}4"].value, rule.type, str(rng.sqref).count(":")))
            return out
        assert cf(wb_) == cf(wa), tab


def test_every_other_reader_tab_is_unchanged(before, after):
    """Summary, Comparison, Business, Charts and the hidden data sheets: identical — no number, formula or `_data`
    position moved (the moved columns are written in the same order as before)."""
    rb, ra = openpyxl.load_workbook(before["reader"]), openpyxl.load_workbook(after["reader"])
    for tab in (reader.SUMMARY, reader.COMPARISON, reader.BUSINESS, reader.CHARTS, reader.DATA_SHEET,
                reader.SERIES_SHEET):
        wb_, wa = rb[tab], ra[tab]
        assert (wb_.max_row, wb_.max_column) == (wa.max_row, wa.max_column), tab
        for row in wb_.iter_rows():
            for c in row:
                assert _cell(c) == _cell(wa[c.coordinate]), (tab, c.coordinate)


def _stamp(v):
    """The Run Log's build time (the clock, not the data) is the one cell two builds may differ in."""
    return re.sub(r"workbook built \d{4}-\d\d-\d\d \d\d:\d\d UTC", "workbook built <time>", v) \
        if isinstance(v, str) else v


def test_the_audit_file_is_unchanged_cell_for_cell(before, after):
    ab, aa = openpyxl.load_workbook(before["audit"]), openpyxl.load_workbook(after["audit"])
    assert ab.sheetnames == aa.sheetnames
    diffs = []
    for ws in ab.worksheets:
        wa = aa[ws.title]
        if (ws.max_row, ws.max_column) != (wa.max_row, wa.max_column):
            diffs.append(f"{ws.title}: dims")
            continue
        for row in ws.iter_rows():
            for c in row:
                o = wa[c.coordinate]
                if (_stamp(c.value), c.number_format) != (_stamp(o.value), o.number_format) or \
                        (c.comment.text if c.comment else None) != (o.comment.text if o.comment else None):
                    diffs.append(f"{ws.title}!{c.coordinate}")
    assert not diffs, diffs[:20]


# ================================================================================================ map, guard, Charlie
def test_the_column_map_sends_moved_columns_to_project_notes(after):
    ra = openpyxl.load_workbook(after["reader"])
    blocks = notes_blocks(ra[reader.NOTES])
    with open(after["map"], encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for tab, head, owner, _why in reader.PROJECT_NOTE_COLUMNS:
        hits = [r for r in rows if r["audit_tab"] == tab and r["reader_header"] == head]
        assert hits, (tab, head)
        assert all(r["reader_tab"] == reader.NOTES for r in hits), (tab, head, hits)
        assert {r["reader_column"] for r in hits} == {f"B{blocks[owner][head]}"}, (tab, head)
        assert all("Project notes" in r["note"] for r in hits)
    # nothing maps a moved header to an archetype tab any more
    moved = {(t, h) for t, h, _o, _w in reader.PROJECT_NOTE_COLUMNS}
    assert not [r for r in rows if (r["reader_tab"], r["reader_header"]) in moved]


def test_the_guard_reports_no_violation_and_the_same_headlines(before, after):
    import measuring_guard as mg
    accepted: list = []
    assert mg.compare(after["base"], after["snap"], accepted) == []
    assert after["snap"]["headlines"] == before["snap"]["headlines"]
    assert after["snap"]["mpc_headlines"] == before["snap"]["mpc_headlines"]
    # every headline the reader shows is still read on the reader, at a cell that exists; one in a moved column would
    # be read at its Project notes cell
    assert set(after["locations"]) == set(before["locations"])
    ra = openpyxl.load_workbook(after["reader"])
    for key, (sheet, cell) in after["locations"].items():
        assert ra[sheet][cell].value is not None, (key, sheet, cell)


def test_the_charlie_look_places_project_notes_after_business(after):
    import charlie_look
    wb = openpyxl.load_workbook(after["reader"])
    charlie_look.apply_look(wb)
    vis = [n for n in wb.sheetnames if wb[n].sheet_state == "visible"]
    assert vis == ["Summary", *ARCH, "Comparison", "Business", "Project notes", "Charts", "About", "Definitions"]
    assert wb[reader.NOTES].sheet_properties.tabColor.rgb[-6:] == charlie_look.TAB_COLOURS[reader.BUSINESS]
