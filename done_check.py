"""done_check.py — is each project DONE? (P3-5). READ-ONLY.

    python done_check.py                         # token_metrics.xlsx + token_metrics_audit.xlsx in this folder
    python done_check.py --reader R --audit A --asof 2026-11-02 --verbose

Per project that is NOT PARKED (reader.parked_reason empty; a parked project is listed PARKED, not judged): every
Summary and Comparison cell, the primary archetype's key and the rank must be a NUMBER, or a CODE (the cell's text, one
of reader.CODES; its reason is the cell's comment) whose reason is one of:

    by design   structural: the code is 'by design', or the reason says "by design" / "structural"
    history     the reason says the history is too short (needs history, valid from, short of the window ...) AND
                carries a clear-by date
    blocked     the code is 'blocked' (or the reason says BLOCKED) AND the reason carries a clear-by date
    accepted    the cell is on the allowlist (config.regression_allowance: REGRESSION_ALLOWLIST, not past `until`)

A CLEAR-BY DATE is a date found in the reason text that is on or after the as-of day: an ISO date (YYYY-MM-DD), or a
month written out ("about January 2027", "by Jan 2027" — read as that month's last day; the build writes ISO dates,
"about 2027-01-01"). A reason whose dates are all before the as-of day (a hole, a "held from") has NO clear-by date.

Anything else is a BLOCKER:
    rule-3      a rule-3 suspect (rule3.suspects, both workbooks: a code whose reason says a mechanism is absent),
                even when its code is 'by design'; one blocker per reason, the other cells with it listed under it
                (a suspect whose code is 0 is a measured zero, a number: not a blocker)
    BROKEN      a Coverage row (audit file) with status BROKEN
    MISSING     a Coverage row with status MISSING on a HEADLINE (the audit's headline cells, listed on its
                Credibility tab), unless its reason is history with a clear-by date or it is on the allowlist
    STALE       a "STALE — last point / last read <date>" note on a judged cell or a headline more than 3 days before
                the workbook's as-of day
    unranked    an unranked primary whose reason carries no clear-by date (and is not by design or accepted)
    n/a, not wired, a blank, an error, 'parked' on a project that is not parked — without an allowed reason

Prints a first line naming the as-of day, then one line per project, its blockers the worst first (BROKEN, STALE,
unranked, MISSING, rule-3, blocked, history, not wired, n/a):

    <project> | READY |
    <project> | NOT READY | <tab>: <header> — <code>: <short reason>; ... (at most 5, then "and N more")
    <project> | PARKED | <why>

then `DONE CHECK: <r> READY, <n> NOT READY, <p> PARKED`. --verbose lists every blocker under its project.
Exit code 1 when any project is NOT READY, else 0. It writes nothing, fetches nothing and never opens the store.

Cells are found by HEADER TEXT (row 4) and project name (column A), never by position.
"""
from __future__ import annotations

import argparse
import calendar
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import friday_check as fc  # noqa: E402
import reader  # noqa: E402

MAX_SHOWN = 5
STALE_DAYS = 3
SHORT = 60                                    # characters of a reason shown on the one-line table
HEAD_SHORT = 40                               # characters of a header (the audit's headers are long)
HEADER_ROW = 4
# the order blockers are listed in, the worst first (a class not named here comes last)
ORDER = {c: i for i, c in enumerate(("BROKEN", "STALE", "unranked", "MISSING", "rule-3", "blocked", "history",
                                      "not wired", "n/a"))}

# ============================================================================================ dates
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTH = re.compile(r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{4})\b", re.I)


def dates_in(text) -> list:
    """Every date written in `text`: ISO dates, and 'Month YYYY' (that month's last day)."""
    out = []
    for y, m, d in _ISO.findall(str(text or "")):
        try:
            out.append(date(int(y), int(m), int(d)))
        except ValueError:
            pass
    for mon, y in _MONTH.findall(str(text or "")):
        m, y = _MONTHS[mon.lower()], int(y)
        out.append(date(y, m, calendar.monthrange(y, m)[1]))
    return out


def clear_by(text, asof: date):
    """(the clear-by date, '') when the reason carries a date on or after `asof`; (None, why not) otherwise."""
    ds = dates_in(text)
    if not ds:
        return None, "no clear-by date"
    later = [d for d in ds if d >= asof]
    if later:
        return max(later), ""
    return None, f"no clear-by date (latest {max(ds)} < as-of)"


# ============================================================================================ classes
@dataclass
class Blocker:
    tab: str
    header: str
    code: str
    why: str
    cls: str                                  # the blocker class (rule-3, BROKEN, MISSING, STALE, unranked, no reason …)
    more: list = field(default_factory=list)  # other cells with the same reason ("tab: header"), listed by --verbose

    def line(self) -> str:
        extra = f" (and {len(self.more)} more cell(s) with this reason)" if self.more else ""
        return f"{self.tab}: {short(self.header, HEAD_SHORT)} — {self.code}: {self.why}{extra}"


_HISTORY = re.compile(r"\bhistory\b|valid (?:from|once)|short of the window|needs? (?:the )?\S+ .*? on \d{4}|"
                      r"first epoch|covers \d+ of \d+ days|stored from|starts inside the window|too short|"
                      r"two years of|not yet (?:a|one) (?:full )?(?:year|quarter)", re.I)
_BY_DESIGN = re.compile(r"\bby design\b|\bstructural\b", re.I)
_BLOCKED = re.compile(r"\bblocked\b", re.I)
_ERRORS = ("#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#N/A", "#NUM!", "#NULL!")
_WORKING = re.compile(r"Working: audit file, (?:'([^']+)'|([^!]+))!\$?([A-Z]{1,3})\$?(\d+)")
_STALE = re.compile(r"STALE\b[^\n]*?\blast (?:point|read)\s+(\d{4}-\d{2}-\d{2})")


def short(text: str, n: int = SHORT) -> str:
    """The reason without its 'Working:' / 'Source:' tail, on one line, at most n characters."""
    t = re.split(r"\s*(?:Working: |Behind it: |\n\s*Source: )", str(text or ""))[0]
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def classify(code: str, reason: str, asof: date, accepted: bool = False) -> tuple:
    """(allowed, class, why) for a code cell. class is one of: accepted, by design, history, blocked (allowed), or the
    blocker class: rule-3, history, blocked, n/a, not wired, parked, unranked, code."""
    import rule3                                                          # noqa: PLC0415
    code = str(code or "").strip().lower()
    if accepted:
        return True, "accepted", "on the allowlist"
    if rule3.is_suspect(reason):
        return False, "rule-3", "rule-3 suspect"
    if code == "by design" or _BY_DESIGN.search(reason or ""):
        return True, "by design", "structural"
    if code == "blocked" or _BLOCKED.search(reason or ""):
        d, why = clear_by(reason, asof)
        return (True, "blocked", f"clears by {d}") if d else (False, "blocked", why)
    if _HISTORY.search(reason or ""):
        d, why = clear_by(reason, asof)
        return (True, "history", f"clears by {d}") if d else (False, "history", f"history, {why}")
    if code == "unranked":
        return False, "unranked", "unranked, no date"
    if code in ("n/a", "not wired"):
        return False, code, "no allowed reason"
    if code == "parked":
        return False, "parked", "reads 'parked' but the project is not parked"
    return False, "code", f"'{code}' is not an allowed code"


# ============================================================================================ reading
def _headers(ws) -> dict:
    """{column: header text} of a reader tab's header row."""
    return {c: str(ws.cell(HEADER_ROW, c).value) for c in range(1, ws.max_column + 1)
            if ws.cell(HEADER_ROW, c).value is not None}


def _audit_header(awb, sheet: str, col: str) -> str:
    """The audit tab's header over column `col` (its header row is the row whose column A reads 'Project')."""
    if awb is None or sheet not in awb.sheetnames:
        return ""
    ws = awb[sheet]
    hr = _audit_header_row(awb, sheet)
    if hr is None:
        return ""
    from openpyxl.utils import column_index_from_string                  # noqa: PLC0415
    return str(ws.cell(hr, column_index_from_string(col)).value or "")


_HR_CACHE: dict = {}


def _audit_header_row(awb, sheet: str):
    key = (id(awb), sheet)
    if key not in _HR_CACHE:
        ws = awb[sheet]
        _HR_CACHE[key] = next((r for r in range(1, 15) if ws.cell(r, 1).value == "Project"), None)
    return _HR_CACHE[key]


def allowance(project: str, tab: str, header: str, reason: str, awb, asof: date):
    """The allowlist entry (config.regression_allowance) that covers this cell: matched on the reader tab and header,
    and on the audit cell the reader reads ('Working: audit file, <sheet>!<cell>' in the comment) as
    '<A1..A4 | CP> | <audit header>' — the guard's spelling of a headline."""
    import config                                                         # noqa: PLC0415
    details = [f"{tab} | {header}: {reason}"]
    for m in _WORKING.finditer(reason or ""):
        sheet = m.group(1) or m.group(2)
        ah = _audit_header(awb, sheet.strip(), m.group(3))
        if ah:
            details.append(f"{sheet.strip()[:2]} | {ah}: {reason}")
    for d in details:
        a = config.regression_allowance(project, d, on=asof)
        if a is not None:
            return a
    return None


def stale_blocker(project, tab, header, text, asof: date):
    """A Blocker when `text` carries a STALE note whose date is more than STALE_DAYS before the as-of day."""
    for m in _STALE.finditer(str(text or "")):
        d = date.fromisoformat(m.group(1))
        age = (asof - d).days
        if age > STALE_DAYS:
            return Blocker(tab, header, "STALE", f"last point {d}, {age} days before the as-of {asof}", "STALE")
    return None


def judge_cell(project, tab, header, cell, value, asof, awb, is_rank=False, key_reason=""):
    """[Blocker] for one judged reader cell (value = the evaluated value)."""
    comment = cell.comment.text if cell.comment else ""
    out = []
    st = stale_blocker(project, tab, header, comment, asof)
    if st:
        out.append(st)
    if fc._is_number(value):
        return out
    if value is None or (isinstance(value, str) and not value.strip()):
        return out + [Blocker(tab, header, "blank", "no value and no code", "blank")]
    if not isinstance(value, str) or value.strip().upper() in _ERRORS or value.startswith("#ERROR"):    # an Excel error (xlcalc.XLError) or bool
        return out + [Blocker(tab, header, "error", f"evaluates to {value!r}", "error")]
    code = value.strip().lower()
    if code not in reader.CODES:
        return out                                                        # a descriptor (token, archetype, status)
    reason = comment + (f"\n\n{key_reason}" if is_rank and key_reason else "")
    acc = allowance(project, tab, header, reason, awb, asof)
    ok, cls, why = classify(code, reason, asof, accepted=acc is not None)
    if code == "unranked" and not ok and cls != "rule-3":                  # an unranked primary without a date
        cls, why = "unranked", (why if why.startswith("unranked") else f"unranked, {why}")
    if not ok:
        out.append(Blocker(tab, header, code, f"{why} — {short(comment)}" if short(comment) else why, cls))
    return out


@dataclass
class Book:
    rwb: object
    awb: object
    X: object
    asof: date


def judge_reader(book: Book, project: str) -> list:
    """Blockers among the project's Summary and Comparison cells and its primary tab's key and rank."""
    rwb, X, out = book.rwb, book.X, []
    s = rwb[reader.SUMMARY]
    srows = fc._project_rows(s)
    prim = fc._primary_archetypes(s).get(project)
    hdr = _headers(s)
    key_reason = ""
    if project in srows:
        kc = next((c for c, h in hdr.items() if h == "Archetype key"), None)
        if kc is not None:
            kcell = s.cell(srows[project], kc)
            key_reason = kcell.comment.text if kcell.comment else ""
    on_summary = {"key": False, "rank": False}
    for tab in (reader.SUMMARY, reader.COMPARISON):
        if tab not in rwb.sheetnames:
            out.append(Blocker(tab, "(tab)", "missing", f"the reader has no {tab} tab", "missing tab"))
            continue
        ws = rwb[tab]
        rows = fc._project_rows(ws)
        if project not in rows:
            out.append(Blocker(tab, "(row)", "missing", f"{project} has no row on {tab}", "missing row"))
            continue
        r = rows[project]
        for c, h in _headers(ws).items():
            if c == 1:
                continue
            is_rank = h.startswith("Rank in")
            if tab == reader.SUMMARY:
                on_summary["rank"] |= is_rank
                on_summary["key"] |= h == "Archetype key"
            out += judge_cell(project, tab, h, ws.cell(r, c), _value(X, tab, r, c), book.asof, book.awb,
                              is_rank=is_rank, key_reason=key_reason)
    # the primary tab's key and rank: judged there when Summary does not hold them (by header: P3-6 moves columns)
    if prim and not all(on_summary.values()) and fc.ARCH_TABS[prim] in rwb.sheetnames:
        tab = fc.ARCH_TABS[prim]
        ws = rwb[tab]
        rows = fc._project_rows(ws)
        if project in rows:
            r, key_head = rows[project], reader.ARCHETYPE_KEYS[prim][0]
            cols = _headers(ws)
            kc = next((c for c, h in cols.items() if h == key_head), None)
            if kc is not None and not key_reason:
                key_reason = ws.cell(r, kc).comment.text if ws.cell(r, kc).comment else ""
            for c, h in cols.items():
                want = ("key" if h == key_head else "rank" if h.startswith("Rank in") else None)
                if want and not on_summary[want]:
                    out += judge_cell(project, tab, h, ws.cell(r, c), _value(X, tab, r, c), book.asof, book.awb,
                                      is_rank=want == "rank", key_reason=key_reason)
    return out


def _value(X, tab, r, c):
    """The cell's value, a formula evaluated (xlcalc, as friday_check reads it); an evaluation failure is an error."""
    try:
        return X.value(tab, r, c)
    except Exception as e:                                                # noqa: BLE001
        return f"#ERROR {type(e).__name__}"


# ============================================================================================ the audit file
def coverage_rows(awb) -> list:
    """[dict] of the Coverage tab's detail rows (the table under the scoreboard: Tab, Column, Project, Status …)."""
    if awb is None or "Coverage" not in awb.sheetnames:
        return []
    head, out = None, []
    for row in awb["Coverage"].iter_rows(values_only=True):
        if head is None:
            if row and row[0] == "Tab" and "Status" in row and "Project" in row:
                head = [str(h) if h is not None else "" for h in row]
            continue
        if not row or not row[0]:
            continue
        out.append(dict(zip(head, row)))
    return out


def headline_cells(awb) -> dict:
    """{(audit tab, project, header): coordinate} of the audit file's headline cells — the Credibility tab lists every
    one by address (build_workbook._HEADLINE_CELLS); the header is read off the audit tab."""
    if awb is None or "Credibility" not in awb.sheetnames:
        return {}
    out, on = {}, False
    for row in awb["Credibility"].iter_rows(values_only=True):
        if not on:
            on = bool(row) and row[0] == "Project" and len(row) > 2 and row[1] == "Tab" and row[2] == "Cell"
            continue
        if not row or not row[0] or not row[1] or not row[2]:
            continue
        m = re.match(r"\$?([A-Z]{1,3})\$?\d+$", str(row[2]))
        if m and str(row[1]) in awb.sheetnames:
            h = _audit_header(awb, str(row[1]), m.group(1))
            if h:
                out[(str(row[1]), str(row[0]), h)] = str(row[2]).replace("$", "")
    return out


def judge_audit(book: Book, project: str, cov: list, heads: dict) -> list:
    """BROKEN rows, MISSING headline rows (Coverage tab) and STALE headline notes (the headline cell's comment) on the
    audit file for one project."""
    out = []
    for r in cov:
        if str(r.get("Project") or "") != project:
            continue
        st, tab, col = str(r.get("Status") or ""), str(r.get("Tab") or ""), str(r.get("Column") or "")
        why = str(r.get("Reason / route / error") or "")
        if st == "BROKEN":
            out.append(Blocker(f"audit {tab}", col, "BROKEN", short(why) or "BROKEN", "BROKEN"))
        elif st == "MISSING" and (tab, project, col) in heads:
            acc = _allowed(project, f"{tab[:2]} | {col}: {why}", book.asof)
            ok, cls, _v = classify("n/a", why, book.asof, accepted=acc is not None)
            if not (ok and cls in ("history", "accepted")):
                out.append(Blocker(f"audit {tab}", col, "MISSING", f"headline MISSING — {short(why)}", "MISSING"))
    for (tab, p, head), coord in heads.items():
        if p != project:
            continue
        c = book.awb[tab][coord]
        st_b = stale_blocker(project, f"audit {tab}", head, c.comment.text if c.comment else "", book.asof)
        if st_b:
            out.append(st_b)
    return out


def _allowed(project, detail, asof):
    import config                                                         # noqa: PLC0415
    return config.regression_allowance(project, detail, on=asof)


def judge_suspects(hits: list, project: str, already: list) -> list:
    """rule3.suspects hits of this project as blockers, ONE per reason: the other cells with the same reason are listed
    under it (--verbose). A cell already judged a rule-3 blocker (Summary, Comparison) takes the cells with its reason.
    A suspect whose code is 0 is a measured zero — a number, rule 3 obeyed — and is not a blocker."""
    seen = {(b.tab, b.header) for b in already}
    by_reason = {}
    for b in already:
        if b.cls == "rule-3":
            by_reason.setdefault(b.why.split(" — ", 1)[-1], b)
    out = []
    for h in hits:
        if h["project"] != project or str(h["code"]).strip() == "0":
            continue
        kind, tab = h["tab"].split(":", 1)
        key = (tab if kind == "reader" else f"audit {tab}", h["header"] or "(no header)")
        if key in seen:
            continue
        seen.add(key)
        r = short(h["reason"])
        if r in by_reason:
            by_reason[r].more.append(f"{key[0]}: {key[1]}")
            continue
        b = Blocker(key[0], key[1], h["code"], f"rule-3 suspect — {r}", "rule-3")
        by_reason.setdefault(r, b)
        out.append(b)
    return out


# ============================================================================================ runner
def asof_of(rwb, awb, override: str | None) -> date | None:
    if override:
        return date.fromisoformat(override)
    return (fc._asof_of_reader(rwb) if rwb is not None else None) or (fc._asof_of_audit(awb) if awb is not None
                                                                         else None)


def check(rwb, awb, asof: date) -> list:
    """[(project, status, [Blocker], park reason)] in Summary order. status: READY / NOT READY / PARKED."""
    import rule3                                                          # noqa: PLC0415
    import xlcalc                                                         # noqa: PLC0415
    book = Book(rwb, awb, xlcalc.Workbook(rwb), asof)
    s = rwb[reader.SUMMARY]
    names = list(fc._project_rows(s))
    if reader.COMPARISON in rwb.sheetnames:
        names += [n for n in fc._project_rows(rwb[reader.COMPARISON]) if n not in names]
    parked = set(fc.parked_projects(names, asof))
    hits = rule3.suspects(rwb, awb)
    cov, heads = coverage_rows(awb), headline_cells(awb)
    results = []
    for n in names:
        if n in parked:
            results.append((n, "PARKED", [], reader.parked_reason(n)))
            continue
        bl = judge_reader(book, n)
        bl += judge_audit(book, n, cov, heads)
        bl += judge_suspects(hits, n, bl)
        bl.sort(key=lambda x: ORDER.get(x.cls, len(ORDER)))                 # the worst first; stable within a class
        results.append((n, "NOT READY" if bl else "READY", bl, ""))
    return results


def format_line(project: str, status: str, blockers: list, why: str = "") -> str:
    if status == "PARKED":
        return f"{project} | PARKED | {short(why, 80)}"
    shown = "; ".join(b.line() for b in blockers[:MAX_SHOWN])
    if len(blockers) > MAX_SHOWN:
        shown += f"; and {len(blockers) - MAX_SHOWN} more"
    return f"{project} | {status} | {shown}".rstrip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reader", default=reader.READER_NAME, help=f"the reader workbook (default {reader.READER_NAME})")
    ap.add_argument("--audit", default=reader.AUDIT_NAME, help=f"the audit workbook (default {reader.AUDIT_NAME})")
    ap.add_argument("--asof", help="YYYY-MM-DD: judge dates against this day instead of the workbook's as-of")
    ap.add_argument("--verbose", action="store_true", help="list every blocker under its project")
    a = ap.parse_args(argv)
    if a.asof:
        try:
            date.fromisoformat(a.asof)
        except ValueError:
            ap.error(f"--asof must be YYYY-MM-DD, got {a.asof!r}")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")                          # a console that cannot show a character
    import openpyxl                                                       # noqa: PLC0415
    try:
        rwb = openpyxl.load_workbook(a.reader)
    except Exception as e:                                                # noqa: BLE001
        print(f"DONE CHECK: cannot open {a.reader}: {type(e).__name__}: {e}")
        return 1
    try:
        awb = openpyxl.load_workbook(a.audit)
    except Exception as e:                                                # noqa: BLE001
        print(f"DONE CHECK: cannot open {a.audit}: {type(e).__name__}: {e}")
        return 1
    asof = asof_of(rwb, awb, a.asof)
    if asof is None:
        print("DONE CHECK: no as-of date on the reader (About) or the audit file (Run Log); pass --asof")
        return 1
    results = check(rwb, awb, asof)
    print(f"done check as of {asof} — {a.reader} + {a.audit}")
    counts = {"READY": 0, "NOT READY": 0, "PARKED": 0}
    for name, status, blockers, why in results:
        counts[status] += 1
        print(format_line(name, status, blockers, why))
        if a.verbose:
            for b in blockers:
                print(f"    [{b.cls}] {b.line()}")
    print(f"DONE CHECK: {counts['READY']} READY, {counts['NOT READY']} NOT READY, {counts['PARKED']} PARKED")
    return 1 if counts["NOT READY"] else 0


if __name__ == "__main__":
    import progress                                    # stdout line-buffered when piped (2026-10-11)
    progress.line_buffered()
    sys.exit(main())
