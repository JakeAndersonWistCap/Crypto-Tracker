"""leaderboard.py - THE READER'S FIRST SHEET (R17.D): the frozen projects, ranked inside their archetype.

Added to every reader build (token_metrics.xlsx) by reader_layout.apply_layout, after Highlights and before the look.
It holds NO figure of its own: every number is a formula on the project's Summary cell, so the sheet can never
disagree with the Summary, and it is no reader tab project_freeze reads (a frozen baseline is not touched by it).

  RANKED       frozen projects only (config.FROZEN_PROJECTS), grouped by primary archetype, in the order of the
               Summary's own "Rank in archetype" and renumbered 1..n among the frozen ones. A frozen project the
               Summary leaves unranked (no measured key, or a Flows block) is listed last in its group as "unranked".
  PROVISIONAL  every other project, below, with no rank.
  THE LINE     one plain-English sentence per project, generated from the row's own figures (line_for): nothing in it
               is typed by hand, and a figure that is not a number is said to be not measured - never read as 0.

Imported, not an entry point (token_metrics.py is).
"""
from __future__ import annotations

from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter

SHEET = "Leaderboard"
HEADER_ROW = 4
HEADERS = ("Rank", "Project", "Key metric", "Holder value yield", "Total dilution, % of float", "Net holder yield",
           "Confidence (share green)", "Freeze date", "As-of date", "In one line")
# leaderboard column -> the Summary header it quotes
QUOTES = {"Key metric": "Archetype key", "Holder value yield": "Holder value yield",
          "Total dilution, % of float": "Total dilution, % of float", "Net holder yield": "Net holder yield",
          "Confidence (share green)": "Confidence (share green)"}
WIDTHS = (11, 16, 16, 14, 14, 14, 14, 12, 12, 118)
PROVISIONAL = "provisional"
UNRANKED = "unranked"
NOT_FROZEN = "not frozen"


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _pct(v) -> str:
    return f"{v * 100:+.1f}%" if v < 0 else f"{v * 100:.1f}%"


def line_for(project: str, vals: dict, *, frozen: bool, rank, key_label: str, parked: str = "") -> str:
    """The row's sentence, from its own figures. `vals` = {leaderboard header: value as the Summary shows it}."""
    hvy, dil, nhy, conf = (vals.get(h) for h in HEADERS[3:7])
    parts = []
    if _num(hvy) and _num(dil):
        parts.append(f"returns {_pct(hvy)} a year to holders and dilutes the float by {_pct(dil)} a year")
    elif _num(hvy):
        parts.append(f"returns {_pct(hvy)} a year to holders; its dilution is not measured ({dil})")
    elif _num(dil):
        parts.append(f"dilutes the float by {_pct(dil)} a year; its holder value yield is not measured ({hvy})")
    else:
        parts.append(f"has neither holder value yield ({hvy}) nor dilution ({dil}) measured")
    if _num(nhy):
        word = "pays holders more than it dilutes them" if nhy > 0 else (
            "dilutes holders more than it pays them" if nhy < 0 else "pays holders exactly what it dilutes them")
        parts.append(f"net holder yield {_pct(nhy) if nhy <= 0 else '+' + _pct(nhy)}, so it {word}")
    else:
        parts.append(f"net holder yield is not measured ({nhy})")
    key = vals.get("Key metric")
    if frozen and _num(rank):
        parts.append(f"rank {int(rank)} among the frozen projects of its archetype by {key_label.lower()}")
    elif frozen:
        parts.append(f"unranked: {key_label.lower()} is {'blocked from the rank' if _num(key) else 'not measured'}")
    else:
        parts.append("not frozen, so provisional and unranked")
    parts.append(f"{conf * 100:.0f}% of its figures are green" if _num(conf)
                 else f"the share of green figures is not measured ({conf})")
    out = f"{project} " + "; ".join(parts) + "."
    if parked:
        out += f" Parked: {parked}"
    return out


def rows_for(L, projects: list, frozen: dict, primary, parked) -> dict:
    """{"ranked": {archetype: [(rank or 'unranked', name)]}, "provisional": [name]} from the Summary's own ranks."""
    summary = "Summary"
    have = L.rows(summary)
    rank_col = L.headers(summary)["Rank in archetype"][0]
    ranked: dict = {}
    for p in projects:
        n = p["name"]
        if n in frozen and n in have:
            ranked.setdefault(primary(p), []).append((L.X.value(summary, have[n], rank_col), n))
    out: dict = {}
    for arch, rows in sorted(ranked.items()):
        num = sorted((r for r in rows if _num(r[0])), key=lambda r: (r[0], r[1]))
        rest = sorted((r for r in rows if not _num(r[0])), key=lambda r: r[1])
        out[arch] = [(i + 1, n) for i, (_, n) in enumerate(num)] + [(UNRANKED, n) for _, n in rest]
    prov = sorted((p for p in projects if p["name"] not in frozen and p["name"] in have),
                  key=lambda p: (primary(p), p["name"]))
    return {"ranked": out, "provisional": [p["name"] for p in prov]}


def last_day(ctx, name: str):
    """The last day the project holds a price on, not after the build's as-of day; the as-of day where none is held."""
    import pandas as pd                                    # noqa: PLC0415
    g = (getattr(ctx, "groups", None) or {}).get((name, "price_usd"))
    asof = pd.Timestamp(ctx.asof).normalize()
    if g is None or len(g) == 0:
        return asof.date()
    d = pd.to_datetime(g["date"])
    d = d[d <= asof]
    return (d.max().date() if len(d) else asof.date())


def add_leaderboard(wb, ctx) -> dict:
    """Add the Leaderboard sheet, first, to the reader workbook `wb`. Returns {"ranked", "provisional", "lines"}."""
    import config                                          # noqa: PLC0415
    import highlights                                      # noqa: PLC0415
    import reader                                          # noqa: PLC0415
    if SHEET in wb.sheetnames:
        raise ValueError("the workbook already has a Leaderboard sheet")
    L = highlights.Lookup(wb)
    summary = reader.SUMMARY
    frozen = config.FROZEN_PROJECTS
    projects = getattr(ctx, "projects", None) or config.PROJECTS
    asof = getattr(ctx, "asof", None)
    plan = rows_for(L, projects, frozen, reader._primary, reader.parked_reason)
    cols = {h: L.headers(summary)[s][0] for h, s in QUOTES.items()}
    srow = L.rows(summary)
    ws = wb.create_sheet(SHEET, 0)
    ws.sheet_view.showGridLines = False
    ws.cell(1, 1, "Leaderboard — the frozen projects, ranked inside their archetype by its key metric").font = \
        reader.F_TITLE
    ws.cell(2, 1, ((f"As of {asof.date()} · " if asof is not None else "")
                   + "refreshed on every build · every figure is the project's Summary cell · only frozen projects "
                     "are ranked; the others are listed below as provisional, with no rank")).font = reader.F_SUB
    for i, h in enumerate(HEADERS, 1):
        c = ws.cell(HEADER_ROW, i, h)
        c.font, c.fill = reader.F_HEAD, reader.FILL_HEAD
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = WIDTHS[i - 1]
    ws.freeze_panes = ws.cell(HEADER_ROW + 1, 3)
    row = HEADER_ROW + 1
    lines: dict = {}

    def group(text: str) -> None:
        nonlocal row
        for i in range(1, len(HEADERS) + 1):
            ws.cell(row, i).fill = reader.FILL_GROUP
        ws.cell(row, 1, text).font = reader.F_BOLD
        row += 1

    def project_row(name: str, rank, arch: int, is_frozen: bool) -> None:
        nonlocal row
        r = srow[name]
        ws.cell(row, 1, rank).font = reader.F_BODY if is_frozen else reader.F_PARKED
        ws.cell(row, 2, name).font = reader.F_BOLD
        vals = {}
        for i, h in enumerate(HEADERS[2:7], 3):
            src = wb[summary].cell(r, cols[h])
            c = ws.cell(row, i, f"='{summary}'!{src.coordinate}")
            c.number_format, c.font = src.number_format, reader.F_BODY
            c.alignment = Alignment(horizontal="right")
            vals[h] = L.X.value(summary, r, cols[h])
        ws.cell(row, 8, frozen[name]["since"] if is_frozen else NOT_FROZEN).font = \
            reader.F_BODY if is_frozen else reader.F_PARKED
        ws.cell(row, 9, str(last_day(ctx, name)) if asof is not None else "").font = reader.F_BODY
        key_label = reader.ARCHETYPE_KEYS[arch][0] if arch in reader.ARCHETYPE_KEYS else "its key"
        text = line_for(name, vals, frozen=is_frozen, rank=rank, key_label=key_label,
                        parked=reader.parked_reason(name) or "")
        c = ws.cell(row, 10, text)
        c.font, c.alignment = reader.F_BODY, Alignment(wrap_text=True, vertical="top")
        lines[name] = text
        row += 1

    by_name = {p["name"]: p for p in projects}
    for arch, rows in plan["ranked"].items():
        label = reader.ARCHETYPE_KEYS[arch][0] if arch in reader.ARCHETYPE_KEYS else "its key"
        group(f"{reader.ARCHETYPE_NAMES.get(arch, f'Archetype {arch}')} — frozen, ranked by {label.lower()} "
              "(higher is better)")
        for rank, name in rows:
            project_row(name, rank, arch, True)
    if not plan["ranked"]:
        group("No frozen project is in this build")
    if plan["provisional"]:
        group("Provisional — not frozen: figures can still move without a declaration; no rank")
        for name in plan["provisional"]:
            project_row(name, PROVISIONAL, reader._primary(by_name[name]), False)
    return {"ranked": plan["ranked"], "provisional": plan["provisional"], "lines": lines, "sheet": ws}
