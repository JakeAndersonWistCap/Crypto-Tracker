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

INDEPENDENT REFERENCES (fix round 2, T2 / T4 — references.py, independent_references.csv): one row per reference
(Tab "References"); a primary reference within tolerance -> VERIFIED, outside -> BROKEN with both values; a cross-check
(secondary / primary-ish / derived) outside -> CHECK, inside -> LIVE-UNVERIFIED (never VERIFIED on its own). A
reference on a workbook headline grades that headline's own row too. VERIFIED share per project = VERIFIED ÷ the rows
that hold a value (VERIFIED + LIVE-UNVERIFIED + CHECK + BROKEN).
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
STATUSES = ("VERIFIED", "VERIFIED (derived)", "LIVE-UNVERIFIED", "CHECK", "N/A BY DESIGN", "MISSING", "NOT WIRED",
            "BROKEN", "BLANK-UNEXPLAINED")
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


def flow_depth(fr: dict) -> int:
    """Quarters with a value on a Flows line. The Status line is a TEXT VERDICT per quarter (Round G 2.2: it read 0 on
    all 16 projects — by design, not short): its depth is the quarters carrying a verdict other than n/a."""
    labs = ("Q0", "Q-1", "Q-2", "Q-3", "Q-4")
    if fr.get("Line") == "Status":
        return sum(1 for lab in labs if isinstance(fr.get(lab), str) and fr.get(lab) and not fr.get(lab).startswith("n/a"))
    return sum(1 for lab in labs if isinstance(fr.get(lab), (int, float)))


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
          X=None, groups: dict | None = None, sanity_hits: list | None = None,
          windows: dict | None = None, series_green=None, gap_rows: list | None = None) -> list[dict]:
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
                if status == "LIVE-UNVERIFIED" and not why and cell.comment is not None:
                    # WHY IT IS NOT GREEN, from the cell's own band note (Round G5 item 1: 15 cells lost VERIFIED and
                    # Coverage said nothing; the band's reason was in the cell comment all along)
                    why = "band: " + " ".join(str(cell.comment.text).split())[:300]
                con = cs.get(key) or {}
                out_match = output_matches(tab, str(h), proj, val, groups, windows)
                if out_match:
                    match = out_match
                elif key in spec.SUPPORT or not con:
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
                if (status == "LIVE-UNVERIFIED" and series_green is not None and match.startswith("yes")
                        and isinstance(raw, str) and raw.startswith("=")):
                    # D2 (Jake 2026-10-19): VERIFIED (derived) — a calculated cell whose every input series is
                    # VERIFIED (a green band: verified source, mechanism confirmed, the window covered) and whose
                    # formula MATCHES-SPEC. A separate label, counted apart, never added to VERIFIED.
                    reads = _refs(wb, raw)
                    if reads and all(series_green(proj, m) for m in reads):
                        status, why = "VERIFIED (derived)", (f"every input VERIFIED: {', '.join(sorted(reads))[:200]}; "
                                                             f"formula matches SPEC")
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
            # T1 (fix round 2): a token-flow residual beyond ±0.5% with no named cause, in ANY of Q0..Q-4, is BROKEN
            broken = [(q, fr.get(q)) for q in ("Q0", "Q-1", "Q-2", "Q-3", "Q-4")
                      if isinstance(fr.get(q), str) and fr.get(q).startswith("BROKEN")]
            if broken:
                status, why = "BROKEN", "; ".join(f"{q}: {t}" for q, t in broken)
        elif str(fr.get("Note") or "").startswith("0 — no such flow"):
            status, why = "N/A BY DESIGN", str(fr.get("Note"))       # class A (G8 item 2): 0 with its primary source
        else:
            status, why = ("LIVE-UNVERIFIED", "") if v is not None else (
                "N/A BY DESIGN", "no series in Q0 for this line (see the Status line)")
        con = cs.get(key) or {}
        rows.append({"Tab": "Flows", "Column": f"{fr['Statement']}: {fr['Line']}", "Key": key,
                     "Project": fr["Project"], "Value now": v if not isinstance(v, str) else v[:120],
                     "Status": status, "Reason / route / error": (why or "")[:240], "Axis": spec.axis_of(key),
                     "Direction": spec.direction_of(key), "Tier": con.get("tier", ""),
                     "History depth (quarters with a value)": flow_depth(fr),
                     "Formula as built": fr.get("Series", ""), "SPEC formula": con.get("formula", ""),
                     "MATCHES-SPEC": "yes — computed at build from the SPEC section 8 statement"})
    prim = trajectory_primary_checks(trajectory_rows, (windows or {}).get("q0", (None, None))[1]) \
        if (windows or {}).get("q0") else {}
    for t in trajectory_rows or ():
        key = spec.concept_for(t.get("tab", ""), t.get("header", "")) or ""
        con = cs.get(key) or {}
        n = sum(1 for lab in ("Q0", "Q-1", "Q-2", "Q-3", "Q-4") if isinstance(t.get(lab), (int, float)))
        has_dir = t.get("direction") not in (None, "")
        status = "LIVE-UNVERIFIED" if n else "N/A BY DESIGN"
        why = "" if n else (str(t.get("why") or "no quarter with a value")[:240])
        if 0 < n < 5:                          # a short history names its kind (Round G 2.2)
            why = next((part for part in str(t.get("why") or "").split("; ") if part.startswith("HISTORY")), "")[:240]
        if not has_dir:
            status, why = "BLANK-UNEXPLAINED", "no direction declared for this row"
        pc = prim.get((t.get("project"), t.get("tab"), t.get("hid")))
        if pc and n:                                       # G7 item 6: a primary check graded its Q0 / Q-1
            status, why = pc[0], pc[1][:400]
        rows.append({"Tab": "Trajectory", "Column": t.get("header", ""), "Key": key, "Project": t.get("project", ""),
                     "Value now": t.get("Q0") if not isinstance(t.get("Q0"), str) else str(t.get("Q0"))[:120],
                     "Status": status, "Reason / route / error": why, "Axis": spec.axis_of(key),
                     "Direction": t.get("direction"), "Tier": t.get("tier") or con.get("tier", ""),
                     "History depth (quarters with a value)": n, "Formula as built": f"{t.get('tab')} → {t.get('hid')}",
                     "SPEC formula": con.get("formula", spec.SUPPORT.get(key, "")),
                     "MATCHES-SPEC": "yes — re-runs the column's own builder k quarters back"})
    if groups is not None:
        rows.extend(reference_rows(wb, X, groups, rows, flows_rows))
    rows.extend(regression_rows(sanity_hits or ()))
    for item, route in not_wired():
        # A NAMED BLOCKER IS NOT "NOT WIRED" (Round G8 item 4, Jake's standing rule): an item ends a round wired, or
        # relabelled with its blocker — "NOT BUILT BY DESIGN" or "BLOCKED — paid / no public source / no comparable
        # category / Jake's decision" — which is N/A BY DESIGN, off the NOT WIRED count
        st = "N/A BY DESIGN" if route.startswith(("NOT BUILT BY DESIGN", "BLOCKED —")) else "NOT WIRED"
        rows.append({"Tab": "(not on a tab)", "Column": item, "Key": "", "Project": "(all)", "Value now": "",
                     "Status": st, "Reason / route / error": route, "Axis": "", "Direction": "", "Tier": "",
                     "History depth (quarters with a value)": "", "Formula as built": "", "SPEC formula": "",
                     "MATCHES-SPEC": "yes — SPEC.md section 10"})
    mark_missing(rows, gap_rows, groups, (windows or {}).get("q0", (None, None))[1])
    return rows


def trajectory_primary_checks(trajectory_rows, asof, period_days: int = 90, quarters=("Q0", "Q-1")) -> dict:
    """{(project, tab, hid): (status, detail)} from config.TRAJECTORY_PRIMARY_CHECKS (Round G7 item 6): for each quarter
    the events dated in (asof − period·(k+1), asof − period·k] are summed and set against the row's Q-k. A quarter whose
    window starts before covers_from or ends after read_through is not judged; a row is VERIFIED when every judged
    quarter is within tolerance (at least one judged), BROKEN when any is outside."""
    import pandas as pd                                    # noqa: PLC0415
    cfg = _config()
    by = {(t.get("project"), t.get("tab"), t.get("hid")): t for t in trajectory_rows or ()}
    out = {}
    for c in getattr(cfg, "TRAJECTORY_PRIMARY_CHECKS", ()):
        t = by.get((c["project"], c["tab"], c["hid"]))
        if t is None:
            continue
        parts, ok_all, judged = [], True, 0
        for k, lab in enumerate(QUARTER_LABELS):
            if lab not in quarters:
                continue
            hi = pd.Timestamp(asof).normalize() - pd.Timedelta(days=period_days * k)
            lo = hi - pd.Timedelta(days=period_days)
            if lo + pd.Timedelta(days=1) < pd.Timestamp(c["covers_from"]) or hi > pd.Timestamp(c["read_through"]):
                parts.append(f"{lab} {lo.date() + pd.Timedelta(days=1)}..{hi.date()}: not judged (primary events read "
                             f"{c['covers_from']}..{c['read_through']})")
                continue
            ev = [e for e in c["events"] if lo < pd.Timestamp(e[0]) <= hi]
            prim = float(sum(e[1] for e in ev))
            ours = t.get(lab)
            if not isinstance(ours, (int, float)):
                parts.append(f"{lab}: primary {prim:,.2f} ({len(ev)} event(s)); ours blank — not judged")
                continue
            judged += 1
            gap = (ours / prim - 1) * 100 if prim else (0.0 if ours == 0 else float("inf"))
            ok = abs(gap) <= float(c["tolerance_pct"])
            ok_all &= ok
            parts.append(f"{lab}: ours {ours:,.2f} vs primary {prim:,.2f} ({len(ev)} event(s)) {gap:+.4f}% — "
                         + ("within" if ok else "OUTSIDE") + f" {c['tolerance_pct']}%")
        if judged:
            out[(c["project"], c["tab"], c["hid"])] = (
                "VERIFIED" if ok_all else "BROKEN",
                f"PRIMARY CHECK ({c['basis']}; {c['source']}): " + "; ".join(parts))
    return out


QUARTER_LABELS = ("Q0", "Q-1", "Q-2", "Q-3", "Q-4")


# AN N/A THAT IS A MISSING ROUTE (Round G5 item 5g, Jake 2026-10-19: na_reasons flagged 230 N/A cells whose reason is
# "no rows stored", an empty registry stub, a series not read — not a column that does not apply). The same signs as
# check_offline_items.na_reasons.
NA_MISSING_ROUTE_SIGNS = ("no rows stored", "empty stub", "stored nothing", "no series in q", "not wired",
                          "produced nothing", "no url on file", "history bug", "returned no data")
_NA_METRIC = re.compile(r"n/a\s*[—-]\s*([a-z][a-z0-9_]+)\s*\(")


# THE CAUSE OF A MISSING CELL (Round G8 item 2, Jake 2026-10-20 — G7's four causes put all 232 in "route exists, not
# filled"). Every MISSING cell is classed WITH EVIDENCE:
#   A  mechanism absent per a primary source — the Flows line shows 0 labelled "no such flow" with its citation
#      (config.NO_SUCH_FLOW; build_workbook.flow_statements), so it is no longer MISSING. Unknown is never 0.
#   B  an accepted limit Jake decided — N/A BY DESIGN, citing the decision (a `<metric>_blocked` record answered ACCEPTED
#      LIMIT, config.ACCEPTED_LIMIT_CASCADE, or a config.UNAVAILABLE closure).
#   C  needs more history — the input is stored but starts inside the window; the date the cell becomes valid is stated.
#   D  a route exists and has not filled the cell — fixed in the round, or the command named.
#   E  no source — listed for Jake with what would fill it.
MISSING_CLASSES = ("A — NO SUCH FLOW", "B — ACCEPTED LIMIT", "C — NEEDS HISTORY", "D — ROUTE NOT FILLED",
                   "E — NO SOURCE")
MISSING_CAUSES = MISSING_CLASSES                  # the G7 name, kept for the scoreboard and missing_triage
_E_SIGNS = ("no public source", "no free source", "no source configured", "empty stub", "no url on file",
            "not published", "never published", "no series on file", "no route", "paid", "no history provider")
_IDENT = re.compile(r"[a-z][a-z0-9_]{3,}")


def _metrics_named(*texts) -> list[str]:
    cfg = _config()
    seen = []
    for t in texts:
        for w in _IDENT.findall(str(t or "")):
            if w in cfg.METRICS and w not in seen:
                seen.append(w)
    return seen


def accepted_limit(project: str, metric: str) -> str | None:
    """The decision that makes (project, metric) an ACCEPTED LIMIT, cited — or None."""
    cfg = _config()
    p = cfg.PROJECT_BY_NAME.get(project) or {}
    blk = p.get(f"{metric}_blocked") or {}
    txt = f"{blk.get('status', '')} {blk.get('answered', '')}"
    if "ACCEPTED LIMIT" in txt.upper():
        return str(blk.get("answered") or blk.get("status"))
    casc = (getattr(cfg, "ACCEPTED_LIMIT_CASCADE", {}) or {}).get(project) or {}
    if metric in (casc.get("metrics") or ()):
        return f"{casc['decision']} (cascade: {metric} is built from {', '.join(casc['roots'])})"
    u = (getattr(cfg, "UNAVAILABLE_BY_KEY", {}) or {}).get((project, metric))
    if u:
        return f"CLOSED {u.get('closed_on', '')}: {str(u.get('summary', ''))[:200]}"
    return None


def declared_not_applicable(project: str, named: list[str]) -> str | None:
    """Why a cell whose inputs are DECLARED unable to exist for the project is N/A BY DESIGN, not MISSING (Round G8 item
    2, found on the fixture triage: Aerodrome's "Buyback to LOCKED supply" read MISSING [D] although config declares
    buyback_fund_balance not applicable — repurchased AERO passes straight to lockers). Every named input must carry a
    declaration (config.declared_na: not_applicable, or a decided classification). Archetype membership alone is NOT
    enough (it would blank Uniswap's FDV and Pendle's issuance, which exist): those stay D and are listed for Jake."""
    cfg = _config()
    if not named or project not in cfg.PROJECT_BY_NAME:
        return None
    out = []
    for m in named:
        r = cfg.declared_na(project, m)
        if not r:
            return None
        out.append(f"{m}: declared not applicable — {str(r)[:160]}")
    return "; ".join(out)


def missing_class(row: dict, gap: dict | None = None, groups: dict | None = None, asof=None) -> tuple[str, str]:
    """(class, evidence) for one MISSING cell — one of MISSING_CLASSES (A is resolved on the Flows tab before this)."""
    import pandas as pd                                    # noqa: PLC0415
    why = str(row.get("Reason / route / error") or "")
    named = _metrics_named(why, row.get("Formula as built"), (gap or {}).get("reason"))
    proj = str(row.get("Project"))
    for m in named:
        a = accepted_limit(proj, m)
        if a:
            return MISSING_CLASSES[1], f"{m}: {a}"
    na = declared_not_applicable(proj, named)
    if na:
        return "N/A", na
    text = " ".join(str(x or "") for x in (why, (gap or {}).get("reason"), (gap or {}).get("suggestion"))).lower()
    if groups is not None and asof is not None:
        for m in named:
            g = groups.get((proj, m))
            if g is None or getattr(g, "empty", True):
                continue
            first = pd.to_datetime(g["date"]).min().normalize()
            span = 365 if "365" in m or "365" in why else 90
            k = 0
            mt = re.search(r"Q-(\d)", str(row.get("Column", "")) + " " + why)
            if mt and row.get("Tab") == "Trajectory":
                k = int(mt.group(1))
            start = pd.Timestamp(asof).normalize() - pd.Timedelta(days=span * (k + 1) - 1)
            if first > start:
                valid = first + pd.Timedelta(days=span * (k + 1) - 1)
                return MISSING_CLASSES[2], (f"{m} is stored from {first.date()}; the {span}-day window needs it from "
                                            f"{start.date()} — valid from {valid.date()}")
    if "--seed" in text and not any(sg in text for sg in _E_SIGNS):
        # a one-off seed fills it: the route exists (D), the fix is the command
        return MISSING_CLASSES[3], (f"{', '.join(named) or 'the series'} — one-off seed: "
                                    + str((gap or {}).get("suggestion") or why)[:200])
    if any(sg in text for sg in _E_SIGNS) or not named:
        what = (gap or {}).get("suggestion") or "no series is wired for this line"
        return MISSING_CLASSES[4], f"{', '.join(named) or 'no input series'} — {str(what)[:200]}"
    return MISSING_CLASSES[3], (f"{', '.join(named)} — " + str((gap or {}).get("suggestion") or
                                                              "the route is configured and stored nothing for the window")[:200])


def missing_cause(why: str, gap: dict | None = None) -> str:
    """The class of a MISSING cell from its reason alone (no store): B, E or D (G7's helper, G8 classes)."""
    return missing_class({"Reason / route / error": why}, gap)[0]


def mark_missing(rows: list[dict], gap_rows: list | None = None, groups: dict | None = None, asof=None) -> int:
    """N/A BY DESIGN -> MISSING where the reason is a missing route, classed A–E with its evidence (missing_class); a
    class B cell (an accepted limit Jake decided) goes back to N/A BY DESIGN citing the decision. Returns how many are
    MISSING."""
    gaps = {(str(g.get("project")), str(g.get("metric"))): g for g in gap_rows or ()}
    n = 0
    for r in rows:
        why = str(r.get("Reason / route / error") or "")
        if r.get("Status") != "N/A BY DESIGN" or not any(k in why.lower() for k in NA_MISSING_ROUTE_SIGNS):
            continue
        m = _NA_METRIC.search(why)
        metric = m.group(1) if m else ""
        g = gaps.get((str(r["Project"]), metric))
        if g is None:
            for mm in _metrics_named(why, r.get("Formula as built")):
                g = gaps.get((str(r["Project"]), mm))
                if g:
                    break
        cls, ev = missing_class(r, g, groups, asof)
        if cls == "N/A":
            r["Reason / route / error"] = f"N/A BY DESIGN — {ev}"[:400]
            continue
        if cls == MISSING_CLASSES[1]:
            r["Reason / route / error"] = f"N/A BY DESIGN — accepted limit: {ev}"[:400]
            continue
        fix = (f" | ROUTE: {str(g.get('reason'))[:160]} | FIX: {str(g.get('suggestion'))[:200]}" if g else "")
        r["Status"], r["Reason / route / error"] = "MISSING", (f"MISSING [{cls}] — {ev} | {why}" + fix)[:400]
        n += 1
    return n


# T5 (fix round 2): MATCHES-SPEC on OUTPUTS. For these columns the SPEC formula is recomputed in Python from the series
# the build read (its read-time views, build_workbook._LAST_GROUPS) and compared with the evaluated cell: a column whose
# output differs from its SPEC formula is "no — output …", whatever inputs it names. (tab, header prefix) -> fn(groups,
# project, windows) -> (expected, how) or None where the recomputation has no input.
def _series_of(groups, project, metric):
    import pandas as pd                                    # noqa: PLC0415
    g = groups.get((project, metric))
    if g is None or g.empty:
        return None
    s = (g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).drop_duplicates("date", keep="last")
         .set_index("date")["value"])
    s = pd.to_numeric(s, errors="coerce").dropna().sort_index()
    return s if not s.empty else None


def _config():
    import config                                          # noqa: PLC0415
    return config


def _window_sum(metric):
    def fn(groups, project, windows):
        import pandas as pd                                # noqa: PLC0415
        s = _series_of(groups, project, metric)
        if s is None or "q0" not in windows:
            return None
        a, b = (pd.Timestamp(x) for x in windows["q0"])
        x = s[(s.index > a) & (s.index <= b)]          # the Data q0 window: (start, end] (build_workbook._window_sum)
        return (float(x.sum()), f"Σ {metric} ({a.date()}, {b.date()}] ({len(x)} day(s))") if len(x) else None
    return fn


def _product_now(a_metric, b_metric):
    def fn(groups, project, windows):
        a, b = _series_of(groups, project, a_metric), _series_of(groups, project, b_metric)
        if a is None or b is None:
            return None
        d = a.index.intersection(b.index)
        if d.empty:
            return None
        d = d.max()
        return float(a[d] * b[d]), f"{a_metric} × {b_metric} on {d.date()}"
    return fn


def _latest(metric):
    def fn(groups, project, windows):
        s = _series_of(groups, project, metric)
        return (float(s.iloc[-1]), f"{metric} {s.index[-1].date()} (latest)") if s is not None else None
    return fn


OUTPUT_CHECKS = {
    ("Master", "Fees Q0 ($)"): lambda G, p, w: _window_sum("fees_usd")(G, p, w),
    ("Master", "Revenue Q0 ($)"): lambda G, p, w: _window_sum(_config().revenue_headline_metric(p))(G, p, w),
    ("Master", "Market cap ($)"): lambda G, p, w: _latest("market_cap_usd")(G, p, w),
    ("Master", "Price — spot ($)"): lambda G, p, w: _latest("price_usd")(G, p, w),
    ("Master", "Circulating supply (used)"): lambda G, p, w: _latest("circulating_supply_used")(G, p, w),
    ("Master", "FDV ($)"): lambda G, p, w: _latest("fdv_usd")(G, p, w),
}
# the SPEC identity each view must satisfy on its own latest day (market cap = price × circulating used; FDV = price ×
# the total used) — checked beside the cell
IDENTITIES = {
    "Market cap ($)": ("market_cap_usd", _product_now("price_usd", "circulating_supply_used")),
}


def output_matches(tab: str, header: str, project: str, value, groups: dict | None, windows: dict | None):
    """("yes — …" | "no — …") for a column in OUTPUT_CHECKS, else None."""
    if groups is None:
        return None
    fn = next((f for (t, h), f in OUTPUT_CHECKS.items() if t == tab and header.startswith(h)), None)
    if fn is None or not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    got = fn(groups, project, windows or {})
    if got is None:
        return None
    exp, how = got
    tol = 1e-6 * max(1.0, abs(exp))
    if abs(float(value) - exp) > tol:
        return f"no — output {float(value):,.6g} ≠ SPEC {exp:,.6g} ({how})"
    ident = next((v for h, v in IDENTITIES.items() if header.startswith(h)), None)
    if ident:
        m, f2 = ident
        g2 = f2(groups, project, windows or {})
        if g2 is not None and abs(g2[0] - exp) > 1e-6 * max(1.0, abs(exp)):
            # a market cap row for a day the price or the circulating used moved after: the identity is checked on the
            # latest common day, so only flag when that day is the latest market cap day
            mc = _series_of(groups, project, m)
            if mc is not None and g2[1].endswith(f"{mc.index[-1].date()}"):
                return f"no — {m} {exp:,.6g} ≠ SPEC identity {g2[0]:,.6g} ({g2[1]})"
    return f"yes — output = SPEC ({how})"


def _headline_reader(wb, X):
    """headline(project, tab, header_prefix) -> the evaluated cell (None when the tab / row / column is absent)."""
    import xlcalc                                           # noqa: PLC0415
    cache = {}

    def headline(project, tab, prefix):
        if tab not in wb.sheetnames:
            return None
        if tab not in cache:
            ws = wb[tab]
            hr = _header_row(ws)
            cache[tab] = (ws, hr, [ws.cell(hr, c).value for c in range(1, ws.max_column + 1)] if hr else [])
        ws, hr, heads = cache[tab]
        if not hr:
            return None
        c = next((i for i, h in enumerate(heads, start=1) if h and str(h).startswith(prefix)), None)
        r = next((r for r in range(hr + 1, ws.max_row + 1) if ws.cell(r, 1).value == project), None)
        if c is None or r is None:
            return None
        raw = ws.cell(r, c).value
        v = X.value(tab, r, c) if isinstance(raw, str) and raw.startswith("=") else raw
        return xlcalc.display(v) if not isinstance(v, (int, float, str, type(None))) else v
    return headline


def reference_rows(wb, X, groups: dict, audited: list[dict], flows_rows: list[dict] | None = None) -> list[dict]:
    """The independent reference checks (references.py) as Coverage rows; a reference on a headline also grades the
    headline's own row in `audited` (VERIFIED / BROKEN / CHECK, both values in the reason)."""
    import references                                      # noqa: PLC0415
    results = references.check(groups, headline=_headline_reader(wb, X), flows=flows_rows)
    results += references.basis_rows(groups)
    for x in results:
        route = references.ROUTES.get((x["project"], x["metric"])) or ()
        if not route or route[0] != "headline" or x["status"] not in ("VERIFIED", "BROKEN", "CHECK"):
            continue
        for r in audited:
            if r["Tab"] == route[1] and r["Project"] == x["project"] and r["Column"].startswith(route[2]):
                if x["status"] == "VERIFIED" and r["Status"] != "LIVE-UNVERIFIED":
                    continue
                r["Status"] = x["status"] if x["status"] != "CHECK" or r["Status"] != "VERIFIED" else r["Status"]
                r["Reason / route / error"] = (f"independent reference ({x['metric']}): {x['detail']}")[:400]
    return references.coverage_rows(results)


def regression_rows(sanity_hits) -> list[dict]:
    """T3 (fix round 2): a headline that read a value in the previous run and reads n/a / BLOCKED / text now
    (sanity.lost_headlines, "headline_lost") is BROKEN — unless config.REGRESSION_ALLOWLIST names it (its project and
    a `match` substring of the hit: the header or the reason it is now n/a), with its reason and clear-by date; then
    N/A BY DESIGN with that reason."""
    import config                                          # noqa: PLC0415
    out = []
    for proj, rule, detail in sanity_hits:
        if rule != "headline_lost":
            continue
        # D2: an allowance tied to a declared gap lapses the moment the gap is closed (config.regression_allowance)
        allow = config.regression_allowance(proj, detail)
        status = "N/A BY DESIGN" if allow else "BROKEN"
        why = (f"allow-listed ({allow['reason']}): {detail}" if allow else
               f"REGRESSION — {detail}; not on config.REGRESSION_ALLOWLIST")
        out.append({"Tab": "Regression guard", "Column": detail.split(":")[0][:120], "Key": "regression",
                    "Project": proj, "Value now": "", "Status": status, "Reason / route / error": why[:400],
                    "Axis": "", "Direction": "", "Tier": "", "History depth (quarters with a value)": "",
                    "Formula as built": "", "SPEC formula": "a value last run is a value this run (T3)",
                    "MATCHES-SPEC": "yes — sanity.lost_headlines against the previous snapshot"})
    return out


def verified_share(rows: list[dict]) -> dict:
    """T4: per project, VERIFIED ÷ rows holding a value (VERIFIED + LIVE-UNVERIFIED + CHECK + BROKEN)."""
    out = {}
    for r in rows:
        p = r["Project"]
        # VERIFIED (derived) holds a value but is never counted as VERIFIED (D2)
        if p in ("(all)", "") or r["Status"] not in ("VERIFIED", "VERIFIED (derived)", "LIVE-UNVERIFIED", "CHECK",
                                                     "BROKEN"):
            continue
        v, n = out.get(p, (0, 0))
        out[p] = (v + (r["Status"] == "VERIFIED"), n + 1)
    return {p: (v, n, v / n if n else 0.0) for p, (v, n) in sorted(out.items())}


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


BOARD_COLS = ("VERIFIED", "VERIFIED (derived)", "LIVE-UNVERIFIED", "N/A", "MISSING", "NOT WIRED", "BROKEN", "CHECK",
              "BLANK-UNEXPLAINED", "Gap P4", "Gap P5", "Gap P6", "Review Queue", "Verified share", "Populated",
              "MISSING by cause")
_BOARD_TEXT = ("Verified share", "Populated", "MISSING by cause")


def scoreboard(rows: list[dict], gaps: dict | None = None, review_open: dict | None = None) -> dict:
    """THE SCOREBOARD (G4 item 7f, Jake 2026-10-18): per project, ALL first — the Coverage statuses (N/A = N/A BY
    DESIGN), the Gap Report's P4 / P5 / P6 rows ({(project, priority): n}), the Review Queue's OPEN rows ({project: n},
    answered rows excluded) and the verified share (T4: VERIFIED ÷ rows holding a value)."""
    gaps, review_open = gaps or {}, review_open or {}
    projects = sorted({r["Project"] for r in rows if r["Project"] not in ("(all)", "")}
                      | {p for p, _ in gaps} | set(review_open))
    blank = lambda: {k: 0 for k in BOARD_COLS if k not in _BOARD_TEXT}  # noqa: E731
    board = {"ALL": blank(), **{p: blank() for p in projects}}
    for r in rows:
        k = "N/A" if r["Status"] == "N/A BY DESIGN" else r["Status"]
        if k not in board["ALL"]:
            continue
        board["ALL"][k] += 1
        if r["Project"] in board and r["Project"] != "ALL":
            board[r["Project"]][k] += 1
    for (p, pr), n in gaps.items():
        if pr in (4, 5, 6):
            board["ALL"][f"Gap P{pr}"] += n
            if p in board:
                board[p][f"Gap P{pr}"] += n
    for p, n in review_open.items():
        board["ALL"]["Review Queue"] += n
        if p in board:
            board[p]["Review Queue"] += n
    # POPULATED / APPLICABLE (Round G7 item 9): the cells holding a value over every cell that applies (all but N/A)
    causes: dict = {}
    for r in rows:
        if r["Status"] == "MISSING":
            m = re.match(r"MISSING \[([^\]]+)\]", str(r.get("Reason / route / error") or ""))
            c = m.group(1) if m else MISSING_CAUSES[0]
            for k in ("ALL", r["Project"]):
                causes.setdefault(k, {}).setdefault(c, 0)
                causes[k][c] += 1
    for p, b in board.items():
        held = b["VERIFIED"] + b["VERIFIED (derived)"] + b["LIVE-UNVERIFIED"] + b["CHECK"] + b["BROKEN"]
        b["Verified share"] = f"{b['VERIFIED']}/{held} = {b['VERIFIED'] / held:.0%}" if held else "-"
        applicable = sum(b[k] for k in ("VERIFIED", "VERIFIED (derived)", "LIVE-UNVERIFIED", "MISSING", "NOT WIRED",
                                        "BROKEN", "CHECK", "BLANK-UNEXPLAINED"))
        b["Populated"] = f"{held}/{applicable} = {held / applicable:.0%}" if applicable else "-"
        b["MISSING by cause"] = " · ".join(f"{c} {n}" for c, n in sorted((causes.get(p) or {}).items())) or "-"
    return board


def read_csv(path) -> list[dict]:
    """The coverage rows a previous build wrote beside its workbook, or [] (G5 item 1: the run compares with them)."""
    try:
        with open(path, encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def lost_verified(prev: list[dict], rows: list[dict]) -> list[dict]:
    """Cells VERIFIED in the previous build and not now (Round G5 item 1, Jake: "a verified cell must never go
    unverified without the Run Log naming the cell and why") — each with its status and reason now."""
    key = lambda r: (r["Tab"], r["Column"], r["Key"], r["Project"])  # noqa: E731
    was = {key(r) for r in prev if r.get("Status") == "VERIFIED"}
    return [r for r in rows if key(r) in was and r["Status"] != "VERIFIED"]


def board_delta(board: dict, prev: dict | None) -> dict:
    """{project: {column: change since the previous build}} for the integer scoreboard columns (G5 item 1)."""
    out = {}
    for p, b in board.items():
        pb = (prev or {}).get(p) or {}
        out[p] = {k: (v - int(pb.get(k, 0))) for k, v in b.items() if isinstance(v, int) and pb}
    return out


def write(wb, rows: list[dict], csv_path: Path | None = None, board: dict | None = None) -> None:
    from build_workbook import F_BOLD, F_HEAD, FILL_AMBER, FILL_HEAD, FILL_RED, _title  # noqa: PLC0415
    ws = wb.create_sheet(SHEET)
    _title(ws, "Coverage — every column against SPEC.md",
           "One row per SPEC concept x project x column on Master, A1–A4, CP, Flows and Trajectory, plus SPEC.md's NOT "
           "WIRED list. Status from the cell: VERIFIED (green band) · LIVE-UNVERIFIED · N/A BY DESIGN + reason · NOT "
           "WIRED + route · BROKEN + error · BLANK-UNEXPLAINED. Targets: 0 BLANK-UNEXPLAINED, 0 BROKEN, 0 "
           "MATCHES-SPEC = no. Also written as coverage_audit.csv. The SCOREBOARD below opens every build (G4 7f); "
           "Review Queue counts OPEN rows only.")
    board = board if board is not None else scoreboard(rows)
    ws.cell(3, 1, value="SCOREBOARD").font = F_BOLD
    ws.cell(3, 3, value=f"MATCHES-SPEC = no: {counts(rows)['MATCHES-SPEC = no']}")
    for j, h in enumerate(("Project",) + BOARD_COLS, start=1):
        cell = ws.cell(4, j, value=h)
        cell.font, cell.fill = F_HEAD, FILL_HEAD
    for i, (p, b) in enumerate(board.items(), start=5):
        c1 = ws.cell(i, 1, value=p)
        if p == "ALL":
            c1.font = F_BOLD
        for j, h in enumerate(BOARD_COLS, start=2):
            cell = ws.cell(i, j, value=b[h])
            if h in ("BROKEN", "BLANK-UNEXPLAINED") and b[h]:
                cell.fill = FILL_RED
            elif h == "CHECK" and b[h]:
                cell.fill = FILL_AMBER
    hr = 5 + len(board) + 1
    for j, h in enumerate(HEAD, start=1):
        cell = ws.cell(hr, j, value=h)
        cell.font, cell.fill = F_HEAD, FILL_HEAD
    for i, row in enumerate(rows, start=hr + 1):
        for j, h in enumerate(HEAD, start=1):
            v = row[h]
            cell = ws.cell(i, j, value=v if not isinstance(v, str) or not v.startswith("=") else "'" + v)
            if h == "Status" and v in ("BROKEN", "BLANK-UNEXPLAINED"):
                cell.fill = FILL_RED
            elif h == "MATCHES-SPEC" and str(v).startswith("no") or h == "Status" and v == "CHECK":
                cell.fill = FILL_AMBER
    ws.freeze_panes = ws.cell(hr + 1, 5)
    if csv_path is not None:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=HEAD)
            w.writeheader()
            for row in rows:
                w.writerow(row)
