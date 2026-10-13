"""
coverage_audit.py — every workbook column against SPEC.md (Jake's alignment round, 2026-10-13, D1).

One row per SPEC concept x project x column on Master, A1–A4, CP, Flows and Trajectory: the value now, its status,
axis, direction, source tier, history depth, the formula as built against the SPEC formula, and MATCHES-SPEC. Written
as the "Coverage" tab (counts at the top) and as coverage_audit.csv beside the workbook. The SPEC's NOT WIRED list
(section 10) is appended as rows of its own. Targets: 0 BLANK-UNEXPLAINED, 0 BROKEN, 0 MATCHES-SPEC = no.

STATUS, from the cell itself:
  VERIFIED            a value in a GREEN confidence band (verified source, mechanism confirmed, not PARTIAL)
  LIVE-UNVERIFIED     a value, any other band
  N/A BY DESIGN       "n/a — reason" / "BLOCKED — reason" / "0 — reason", or no cell written because the column does
                      not apply to the project (its builder writes nothing: another archetype, a category not held)
  NOT WIRED           "NOT WIRED — route"
  BROKEN              an Excel error (#REF!, #VALUE!, #DIV/0!, #NAME?, #N/A) — the error is shown
  BLANK-UNEXPLAINED   a formula that evaluates to nothing, or a bare "n/a" with no reason
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import spec

SHEET = "Coverage"
TABS = ("Master", "A1 Infrastructure", "A2 Coordination", "A3 Revenue Buyback", "A4 Permanent Burn", "CP Comparability")
HEAD = ["Tab", "Column", "Key", "Project", "Value now", "Status", "Reason / route / error", "Axis", "Direction", "Tier",
        "History depth (quarters with a value)", "Formula as built", "SPEC formula", "MATCHES-SPEC"]
STATUSES = ("VERIFIED", "LIVE-UNVERIFIED", "N/A BY DESIGN", "NOT WIRED", "BROKEN", "BLANK-UNEXPLAINED")
_SHEET_REF = re.compile(r"'([^']+)'!\$?([A-Z]{1,3})\$?(\d+)")
_ERRORS = ("#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#N/A", "#NUM!", "#NULL!")
_GREEN = "D6E9CE"         # build_workbook.FILL_GREEN


def _fill(cell) -> str:
    try:
        return str(cell.fill.fgColor.rgb or "")[-6:].upper()
    except Exception:  # noqa: BLE001
        return ""


def _header_row(ws) -> int | None:
    return next((r for r in range(1, 15) if ws.cell(r, 1).value == "Project"), None)


_MACRO_REF = re.compile(r'"Macro\|([a-z0-9_]+)"')
_UNIT = re.compile(r"_(usd|tokens|pct)$")


def _stem(metric: str) -> str:
    return _UNIT.sub("", metric)


def _matches(reads: set[str], inputs: set[str]) -> bool:
    """A formula matches its concept where it reads a SPEC input, or a component of one (a series named after the
    input's stem: customer_revenue_vrf_usd is a line of customer_revenue_usd)."""
    return bool(reads & inputs) or any(r.startswith(_stem(i)) for r in reads for i in inputs)


def _refs(wb, formula, depth: int = 3) -> set[str]:
    """The Data metrics a formula reads, following references to other tabs' cells up to `depth` levels."""
    import trajectory                                       # noqa: PLC0415
    out = set(trajectory.referenced_metrics(formula)) | set(_MACRO_REF.findall(str(formula or "")))
    if depth and isinstance(formula, str):
        for sheet, col, row in _SHEET_REF.findall(formula):
            if sheet in wb.sheetnames and sheet != "Data":
                out |= _refs(wb, wb[sheet][f"{col}{row}"].value, depth - 1)
    return out


def classify(raw, value, fill: str) -> tuple[str, str]:
    """(status, reason) of one cell: `raw` as written, `value` as evaluated."""
    if raw is None or raw == "":
        return "N/A BY DESIGN", "no cell written: the column does not apply to this project"
    text = "" if value is None else str(value)
    if any(text.startswith(e) for e in _ERRORS) or type(value).__name__ == "XLError":
        return "BROKEN", text or repr(value)
    if value is None or text.strip() == "":
        return "BLANK-UNEXPLAINED", "the formula evaluates to nothing"
    if isinstance(value, str):
        t = value.strip()
        if t.upper().startswith("NOT WIRED"):
            return "NOT WIRED", t[:240]
        if t.lower() in ("n/a", "n/a —", "–", "-"):
            return "BLANK-UNEXPLAINED", f"a bare {t!r} with no reason"
        if t.lower().startswith(("n/a", "blocked", "withheld", "0 —", "none available", "held —", "parked")):
            return "N/A BY DESIGN", t[:240]
    return ("VERIFIED" if fill == _GREEN else "LIVE-UNVERIFIED"), ""


def audit(wb, trajectory_rows: list[dict] | None = None, flows_rows: list[dict] | None = None,
          X=None) -> list[dict]:
    """The coverage rows for a built workbook (openpyxl, formulas kept). `X` is an xlcalc.Workbook over it."""
    import xlcalc                                           # noqa: PLC0415
    X = X or xlcalc.Workbook(wb)
    cs = spec.concepts()
    depth = {}
    for t in trajectory_rows or ():
        n = sum(1 for lab in ("Q0", "Q-1", "Q-2", "Q-3", "Q-4") if isinstance(t.get(lab), (int, float)))
        depth[(t.get("tab"), t.get("project"), t.get("header"))] = n
    rows = []
    for tab in TABS:
        if tab not in wb.sheetnames:
            continue
        ws = wb[tab]
        hr = _header_row(ws)
        if hr is None:
            continue
        heads = [ws.cell(hr, c).value for c in range(1, ws.max_column + 1)]
        for r in range(hr + 1, ws.max_row + 1):
            proj = ws.cell(r, 1).value
            # the project table ends at its first empty row: what follows (confidence counts, monthly tables, the
            # legend) is another table with other headers
            if not proj or not isinstance(proj, str):
                break
            for c, h in enumerate(heads, start=1):
                if not h:
                    continue
                key = spec.concept_for(tab, str(h))
                cell = ws.cell(r, c)
                raw = cell.value
                val = X.value(tab, r, c) if isinstance(raw, str) and raw.startswith("=") else raw
                val = xlcalc.display(val) if hasattr(xlcalc, "display") else val
                status, why = classify(raw, val, _fill(cell))
                con = cs.get(key) or {}
                if key in spec.SUPPORT or not con:
                    match = "yes — support column" if key in spec.SUPPORT else "no — no SPEC concept"
                elif not (isinstance(raw, str) and raw.startswith("=")):
                    match = "yes — no formula (text / attribute / value)"
                else:
                    reads = _refs(wb, raw)
                    ins = set(con.get("inputs") or ())
                    if not ins or not reads:
                        match = "yes — " + ("no inputs declared" if not ins else "reads no Data series")
                    elif _matches(reads, ins):
                        match = "yes"
                    else:
                        match = f"no — reads {', '.join(sorted(reads))[:120]}; SPEC inputs {', '.join(sorted(ins))[:120]}"
                rows.append({
                    "Tab": tab, "Column": str(h), "Key": key or "", "Project": proj,
                    "Value now": val if not isinstance(val, str) else val[:120], "Status": status,
                    "Reason / route / error": why, "Axis": spec.axis_of(key), "Direction": spec.direction_of(key),
                    "Tier": con.get("tier", ""),
                    "History depth (quarters with a value)": depth.get((tab, proj, str(h)), ""),
                    "Formula as built": (raw[:200] if isinstance(raw, str) and raw.startswith("=") else ""),
                    "SPEC formula": con.get("formula", spec.SUPPORT.get(key, "")),
                    "MATCHES-SPEC": match})
    for fr in flows_rows or ():
        key = "token_flow" if fr["Statement"] == "Token flow" else "value_flow"
        v = fr.get("Q0")
        if fr["Line"] == "Status":
            status = ("N/A BY DESIGN" if isinstance(v, str) and v.startswith("n/a") else "LIVE-UNVERIFIED")
            why = v if isinstance(v, str) else ""
        else:
            status, why = ("LIVE-UNVERIFIED", "") if v is not None else (
                "N/A BY DESIGN", "no series in Q0 for this line (see the Status line)")
        con = cs.get(key) or {}
        rows.append({"Tab": "Flows", "Column": f"{fr['Statement']}: {fr['Line']}", "Key": key,
                     "Project": fr["Project"], "Value now": v if not isinstance(v, str) else v[:120],
                     "Status": status, "Reason / route / error": (why or "")[:240], "Axis": spec.axis_of(key),
                     "Direction": spec.direction_of(key), "Tier": con.get("tier", ""),
                     "History depth (quarters with a value)": sum(
                         1 for lab in ("Q0", "Q-1", "Q-2", "Q-3", "Q-4") if isinstance(fr.get(lab), (int, float))),
                     "Formula as built": fr.get("Series", ""), "SPEC formula": con.get("formula", ""),
                     "MATCHES-SPEC": "yes — computed at build from the SPEC section 8 statement"})
    for t in trajectory_rows or ():
        key = spec.concept_for(t.get("tab", ""), t.get("header", "")) or ""
        con = cs.get(key) or {}
        n = sum(1 for lab in ("Q0", "Q-1", "Q-2", "Q-3", "Q-4") if isinstance(t.get(lab), (int, float)))
        has_dir = t.get("direction") not in (None, "")
        status = "LIVE-UNVERIFIED" if n else "N/A BY DESIGN"
        why = "" if n else (str(t.get("why") or "no quarter with a value")[:240])
        if not has_dir:
            status, why = "BLANK-UNEXPLAINED", "no direction declared for this row"
        rows.append({"Tab": "Trajectory", "Column": t.get("header", ""), "Key": key, "Project": t.get("project", ""),
                     "Value now": t.get("Q0") if not isinstance(t.get("Q0"), str) else str(t.get("Q0"))[:120],
                     "Status": status, "Reason / route / error": why, "Axis": spec.axis_of(key),
                     "Direction": t.get("direction"), "Tier": t.get("tier") or con.get("tier", ""),
                     "History depth (quarters with a value)": n, "Formula as built": f"{t.get('tab')} → {t.get('hid')}",
                     "SPEC formula": con.get("formula", spec.SUPPORT.get(key, "")),
                     "MATCHES-SPEC": "yes — re-runs the column's own builder k quarters back"})
    for item, route in not_wired():
        rows.append({"Tab": "(not on a tab)", "Column": item, "Key": "", "Project": "(all)", "Value now": "",
                     "Status": "NOT WIRED", "Reason / route / error": route, "Axis": "", "Direction": "", "Tier": "",
                     "History depth (quarters with a value)": "", "Formula as built": "", "SPEC formula": "",
                     "MATCHES-SPEC": "yes — SPEC.md section 10"})
    return rows


def not_wired() -> list[tuple[str, str]]:
    """SPEC.md section 10: (item, route)."""
    text = spec.SPEC_PATH.read_text(encoding="utf-8")
    sec = text.split("## 10.", 1)[1].split("\n## ", 1)[0]
    out = []
    for ln in sec.splitlines():
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if ln.startswith("|") and len(cells) == 2 and cells[0] not in ("Item", "---") and not cells[0].startswith("-"):
            out.append((cells[0], cells[1]))
    return out


def counts(rows: list[dict]) -> dict:
    c = {s: 0 for s in STATUSES}
    for r in rows:
        c[r["Status"]] = c.get(r["Status"], 0) + 1
    c["MATCHES-SPEC = no"] = sum(1 for r in rows if str(r["MATCHES-SPEC"]).startswith("no"))
    return c


def write(wb, rows: list[dict], csv_path: Path | None = None) -> None:
    from build_workbook import F_BOLD, F_HEAD, FILL_AMBER, FILL_HEAD, FILL_RED, _title  # noqa: PLC0415
    ws = wb.create_sheet(SHEET)
    _title(ws, "Coverage — every column against SPEC.md",
           "One row per SPEC concept x project x column on Master, A1–A4, CP, Flows and Trajectory, plus SPEC.md's NOT "
           "WIRED list. Status from the cell: VERIFIED (green band) · LIVE-UNVERIFIED · N/A BY DESIGN + reason · NOT "
           "WIRED + route · BROKEN + error · BLANK-UNEXPLAINED. Targets: 0 BLANK-UNEXPLAINED, 0 BROKEN, 0 "
           "MATCHES-SPEC = no. Also written as coverage_audit.csv.")
    c = counts(rows)
    for i, (k, v) in enumerate(c.items()):
        ws.cell(3, 1 + 2 * i, value=k).font = F_BOLD
        ws.cell(3, 2 + 2 * i, value=v)
    hr = 5
    for j, h in enumerate(HEAD, start=1):
        cell = ws.cell(hr, j, value=h)
        cell.font, cell.fill = F_HEAD, FILL_HEAD
    for i, row in enumerate(rows, start=hr + 1):
        for j, h in enumerate(HEAD, start=1):
            v = row[h]
            cell = ws.cell(i, j, value=v if not isinstance(v, str) or not v.startswith("=") else "'" + v)
            if h == "Status" and v in ("BROKEN", "BLANK-UNEXPLAINED"):
                cell.fill = FILL_RED
            elif h == "MATCHES-SPEC" and str(v).startswith("no"):
                cell.fill = FILL_AMBER
    ws.freeze_panes = ws.cell(hr + 1, 5)
    if csv_path is not None:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=HEAD)
            w.writeheader()
            for row in rows:
                w.writerow(row)
