"""Charlie edition - the LOOK (docs/charlie_edition_spec.md section 3, SPEC.md P2g-4).

`apply_look(wb)` restyles the openpyxl READER workbook in place. STYLING ONLY: no cell value, formula, comment, rank,
code, number format (bar the token-count format) or tab structure changes. A cell FILL keeps its one meaning -
confidence - so the only fills added are the spec's own: the two alternating blue group bands (row 3) and the zebra
#F7F8FA on data rows, and each only on a cell that has no fill yet. Everything else is font colour, data bars and
borders. Idempotent.
"""
from __future__ import annotations

from openpyxl.formatting.rule import DataBarRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

import reader

NAVY, WHITE, GREY = "14213D", "FFFFFF", "7F7F7F"
BAND_BLUES = ("2F5597", "5B8DB8")
ZEBRA = "F7F8FA"
BORDER_GREY = "D9D9D9"
SIGN_GREEN, SIGN_RED = "1B7F5C", "B23A48"
BAR_TEAL = "8FD3CF"
BODY = "Calibri"
ZOOM = 90
HEADER_ROW, BAND_ROW, FIRST_DATA_ROW = 4, 3, 5
HEADER_HEIGHT, HEADER_TALL = 32, 44

# the same convention as reader.FMT_USD, without the $
TOKEN_FMT = '[>=1000000000]#,##0.0,,,"bn";[>=1000000]#,##0.0,,"m";#,##0'

DATA_TABS = (reader.SUMMARY, reader.A1_TAB, reader.A2_TAB, reader.A3_TAB, reader.A4_TAB, reader.COMPARISON,
             reader.BUSINESS)
HIGHLIGHTS = "Highlights"
TAB_ORDER = (HIGHLIGHTS, *DATA_TABS, reader.CHARTS, reader.ABOUT, reader.DEFS)
TAB_COLOURS = {HIGHLIGHTS: "1F8A8A", reader.SUMMARY: NAVY, reader.A1_TAB: "2F5597", reader.A2_TAB: "2F5597",
               reader.A3_TAB: "2F5597", reader.A4_TAB: "2F5597", reader.COMPARISON: "5B6B7B",
               reader.BUSINESS: "5B6B7B", reader.CHARTS: "BFBFBF", reader.ABOUT: "BFBFBF", reader.DEFS: "BFBFBF"}

SIGN_WORDS = ("growth", "change", "net holder yield", "net supply change", "staking yield minus")
BAR_COLUMNS = ("Holder value yield", "Total dilution, % of float")      # plain single-colour bars
DIVERGING_COLUMN = "Net holder yield"                                   # zero in the middle of the bar range


def _font(f: Font, **kw) -> Font:
    """The same font with Calibri 10 (or the overrides); bold, italic, colour and the rest kept."""
    keep = dict(bold=f.b, italic=f.i, underline=f.u, strike=f.strike, vertAlign=f.vertAlign, color=f.color)
    keep.update(name=BODY, size=10)
    keep.update(kw)
    return Font(**keep)


def _header_cols(ws) -> list:
    """[(column index, header text)] of the header row."""
    return [(c, str(ws.cell(HEADER_ROW, c).value)) for c in range(1, ws.max_column + 1)
            if ws.cell(HEADER_ROW, c).value is not None]


def _project_rows(ws) -> list:
    """The project rows: a name in column A and something in column B (section labels and notes have no column B)."""
    return [r for r in range(FIRST_DATA_ROW, ws.max_row + 1)
            if ws.cell(r, 1).value not in (None, "") and ws.cell(r, 2).value is not None]


def _is_data_tab(ws) -> bool:
    return ws.title in DATA_TABS and ws.cell(HEADER_ROW, 1).value is not None


def _has_rule(ws, sqref: str, kind: str, key: str) -> bool:
    for cf in ws.conditional_formatting:
        if str(cf.sqref) != sqref:
            continue
        for r in cf.rules:
            if r.type != kind:
                continue
            if kind == "dataBar" and r.dataBar is not None and r.dataBar.color is not None \
                    and r.dataBar.color.rgb[-6:] == key:
                return True
            if kind == "expression" and r.dxf is not None and r.dxf.font is not None \
                    and r.dxf.font.color is not None and r.dxf.font.color.rgb[-6:] == key:
                return True
    return False


def _style_cells(ws, out: dict) -> None:
    """Calibri 10 body, title, header row, band row, borders, zebra, codes - on one data tab."""
    ncols = max(c for c, _ in _header_cols(ws))
    rows = _project_rows(ws)
    rowset = {r: i for i, r in enumerate(rows)}
    side = Side(style="thin", color=BORDER_GREY)
    box = Border(left=side, right=side, top=side, bottom=side)
    zebra = PatternFill("solid", fgColor=ZEBRA)
    # group bands: each block label runs to the next label; two alternating blues; a cell with a fill keeps it
    labels = [c for c in range(1, ncols + 1) if ws.cell(BAND_ROW, c).value not in (None, "")
              and not str(ws.cell(BAND_ROW, c).value).startswith("As of")]
    for i, c0 in enumerate(labels):
        c1 = (labels[i + 1] if i + 1 < len(labels) else ncols + 1) - 1
        blue = PatternFill("solid", fgColor=BAND_BLUES[i % 2])
        for c in range(c0, c1 + 1):
            cell = ws.cell(BAND_ROW, c)
            if cell.fill.fill_type is None:
                cell.fill = blue
            if c == c0:
                cell.font = _font(cell.font, bold=True, color=WHITE)
    out["bands"] += len(labels)
    codes = 0
    for cell in list(ws._cells.values()):
        r, c = cell.row, cell.column
        if r == 1 and c == 1:
            cell.font = _font(cell.font, size=18, bold=True, color=NAVY)
            continue
        if r == HEADER_ROW and c <= ncols and cell.value is not None:
            cell.font = _font(cell.font, bold=True, color=WHITE)
            cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal=cell.alignment.horizontal)
            if cell.fill.fill_type is None:
                cell.fill = reader.FILL_HEAD
            continue
        cell.font = _font(cell.font)
        if r in rowset and c <= ncols:
            cell.border = box
            if cell.fill.fill_type is None and rowset[r] % 2 == 1:
                cell.fill = zebra
            if isinstance(cell.value, str) and cell.value in reader.CODES:
                cell.font = _font(cell.font, italic=True, color=GREY)
                cell.alignment = Alignment(horizontal="center", vertical=cell.alignment.vertical,
                                           wrap_text=cell.alignment.wrap_text)
                codes += 1
    # 32 as specified; 44 only when a visible header needs three lines at its column width (it would clip at 32)
    from openpyxl.utils import get_column_letter as L                   # noqa: PLC0415
    tall = any(len(str(ws.cell(HEADER_ROW, c).value)) > 2.2 * (ws.column_dimensions[L(c)].width or 8.43)
               and not ws.column_dimensions[L(c)].hidden for c in range(1, ncols + 1)
               if ws.cell(HEADER_ROW, c).value is not None)
    ws.row_dimensions[HEADER_ROW].height = HEADER_TALL if tall else HEADER_HEIGHT
    out["codes_styled"] += codes
    # freeze under the header row, after the Token column
    heads = {h.strip().lower(): c for c, h in _header_cols(ws)}
    tok = heads.get("token") or heads.get("project") or 1          # Business has no Token column: after Project
    ws.freeze_panes = ws.cell(FIRST_DATA_ROW, tok + 1)


def _sign_and_bars(ws, out: dict) -> None:
    rows = _project_rows(ws)
    if not rows:
        return
    lo, hi = rows[0], rows[-1]
    from openpyxl.utils import get_column_letter as L                   # noqa: PLC0415
    for c, h in _header_cols(ws):
        if not any(w in h.lower() for w in SIGN_WORDS):
            continue
        col = L(c)
        sq = f"{col}{lo}:{col}{hi}"
        out["sign_columns"].append(f"{ws.title}!{h}")
        for colour, op in ((SIGN_GREEN, ">"), (SIGN_RED, "<")):
            if not _has_rule(ws, sq, "expression", colour):
                # ISNUMBER guard: Excel ranks text above every number, so a bare "> 0" would colour the codes
                ws.conditional_formatting.add(sq, FormulaRule(
                    formula=[f"AND(ISNUMBER({col}{lo}),{col}{lo}{op}0)"], font=Font(color=colour)))
    if ws.title != reader.COMPARISON:
        return
    for c, h in _header_cols(ws):
        want = next((b for b in (*BAR_COLUMNS, DIVERGING_COLUMN) if h.startswith(b)), None)
        if want is None or want in out["bar_columns"]:
            continue
        col = L(c)
        sq = f"{col}{lo}:{col}{hi}"
        rng = f"${col}${lo}:${col}${hi}"
        out["bar_columns"].append(want)
        if _has_rule(ws, sq, "dataBar", BAR_TEAL):
            continue
        if want == DIVERGING_COLUMN:
            # symmetric range about zero: zero sits mid-bar, a negative reads short, a positive long (the sign
            # font colour carries red/green). openpyxl cannot write Excel's negative-bar colour (x14 extLst).
            span = f"MAX(MAX({rng}),-MIN({rng}))"
            rule = DataBarRule(start_type="formula", start_value=f"-{span}", end_type="formula",
                               end_value=span, color=BAR_TEAL)
        else:
            rule = DataBarRule(start_type="num", start_value=0, end_type="max", color=BAR_TEAL)
        ws.conditional_formatting.add(sq, rule)
    out["bar_columns_missing"] = [b for b in (*BAR_COLUMNS, DIVERGING_COLUMN) if b not in out["bar_columns"]]


def _token_format(ws) -> int:
    n = 0
    for cell in ws._cells.values():
        if cell.number_format == reader.FMT_TOK and cell.value is not None \
                and (not isinstance(cell.value, str) or cell.value.startswith("=")):
            cell.number_format = TOKEN_FMT
            n += 1
    return n


def apply_look(wb) -> dict:
    """Apply the Charlie look to the reader workbook in place; returns what it styled."""
    out = {"data_tabs": [], "codes_styled": 0, "bands": 0, "sign_columns": [], "bar_columns": [],
           "bar_columns_missing": [], "token_format_cells": 0, "tab_order": [], "highlights": False}
    visible = [ws for ws in wb._sheets if ws.sheet_state == "visible"]
    for ws in visible:
        if _is_data_tab(ws):
            _style_cells(ws, out)
            _sign_and_bars(ws, out)
            out["data_tabs"].append(ws.title)
        out["token_format_cells"] += _token_format(ws)
        ws.sheet_view.showGridLines = False
        ws.sheet_view.zoomScale = ZOOM
        if ws.title in TAB_COLOURS:
            ws.sheet_properties.tabColor = TAB_COLOURS[ws.title]
    out["highlights"] = HIGHLIGHTS in wb.sheetnames
    # tab order: the listed tabs, any other visible tab, then the hidden ones (as they were)
    by = {ws.title: ws for ws in wb._sheets}
    listed = [by[n] for n in TAB_ORDER if n in by]
    rest = [ws for ws in wb._sheets if ws not in listed and ws.sheet_state == "visible"]
    hidden = [ws for ws in wb._sheets if ws not in listed and ws.sheet_state != "visible"]
    wb._sheets = listed + rest + hidden
    first = wb._sheets[0]
    for ws in wb._sheets:
        ws.sheet_view.tabSelected = ws is first
    wb.active = 0
    out["tab_order"] = [ws.title for ws in wb._sheets]
    return out
