"""
rule3.py — the RULE 3 SWEEP ("0 IS NOT n/a", SPEC.md section 1 rule 3; P3-1a, Jake 2026-11-02).

A store-driven cell that reads a CODE (n/a, by design, blocked ...) with a reason saying a MECHANISM IS ABSENT ("no
buyback programme", "no declared payout", "no lock metric", "not deployed", "no holder value yield" ...) is a rule-3
SUSPECT: where the mechanism verifiably does not exist the measured value is 0, not n/a. A suspect is not a bug by
itself — some quantities truly do not exist (a staker yield where nothing is staked) and stay a code by design; the
decision per reason is docs/p3_1a_rule3_sweep.md.

API (small on purpose — done_check.py imports it):

    SUSPECT_PATTERNS                    # tuple of compiled, case-insensitive regexes (the absent-mechanism wordings)
    suspects(reader_wb, audit_wb)       # -> list[dict(tab, project, header, code, reason)]
    is_suspect(text) -> bool            # one reason text against SUSPECT_PATTERNS

`reader_wb` / `audit_wb`: an openpyxl Workbook or a path to one (either may be None). A cell is scanned when it holds
a CODE — on the reader a text cell whose value is one of reader.CODES (its reason is the comment); on the audit file a
text cell or a formula whose string literal reads like a code ("n/a — …", "0 — …", "BLOCKED — …"), its reason the text
itself plus the comment. Headers are row 4 and the project is column A, the layout of every reader and audit tab; a cell
outside a project row (a title, a header) is never a suspect.

    python rule3.py token_metrics.xlsx token_metrics_audit.xlsx      # prints every suspect, one line each
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# The wordings that say a MECHANISM IS ABSENT (P3-1a). Every reason the build writes with one of them is listed in
# docs/p3_1a_rule3_sweep.md with its decision.
SUSPECT_PATTERNS = tuple(re.compile(p, re.I) for p in (
    r"\bno buyback programme\b",
    r"\bno declared payout\b",
    r"\bno payout mechanism\b",
    r"\bno lock metric\b",
    r"\bno lock or staking\b",
    r"\bno lock contract\b",
    r"\bnot deployed\b",
    r"\bno holder value( yield)?\b",
    r"\bfee switch (is )?off\b",
    r"\bnothing (is )?staked\b",
    r"\bno [A-Z]* ?staking exists\b",
    r"\bno staker yield\b",
    r"\bnothing is paid to stakers\b",
    r"\bno supply-side incentive\b",
    r"\bno non-circulating wallet set\b",
    r"\bno burn mechanism\b",
    r"\bno buyback\b(?! since)",
))

HEADER_ROW = 4
_AUDIT_CODE = re.compile(r'^\s*(n/a|0|blocked|by design|not wired|missing [a-e])\s*[—-]', re.I)
_LITERAL = re.compile(r'"((?:[^"]|"")*)"')


MEASURED_ZERO_NOTE = "a measured zero (SPEC rule 3)"


def is_suspect(text) -> bool:
    """True when `text` (a reason) says a mechanism is absent. A paragraph that RECORDS a measured zero (the comment
    build_workbook puts on such a 0 — "… = 0 — a measured zero (SPEC rule 3) …: <mechanism, source>") is rule 3 obeyed,
    not a suspect: it is left out, so a code beside it is judged by its own reason (a coverage block, say)."""
    t = "\n\n".join(x for x in str(text or "").split("\n\n") if MEASURED_ZERO_NOTE not in x)
    return any(p.search(t) for p in SUSPECT_PATTERNS)


def _load(wb):
    if wb is None:
        return None
    if isinstance(wb, (str, Path)):
        import openpyxl
        return openpyxl.load_workbook(wb)
    return wb


def _project(ws, row: int) -> str:
    v = ws.cell(row, 1).value
    return v if isinstance(v, str) and v.strip() and v != "Project" else ""


def _reader_codes() -> set:
    try:
        import reader
        return {str(k).lower() for k in reader.CODES}
    except Exception:  # noqa: BLE001 — the scan still runs on the codes the reader writes today
        return {"n/a", "by design", "blocked", "not wired", "parked", "history"}


def _evaluator(wb):
    """value(sheet, row, col) of a formula cell (xlcalc, as reader.Audit reads the audit file), or None when it cannot
    be evaluated — then the formula's own literal decides."""
    try:
        import xlcalc
        X = xlcalc.Workbook(wb)
    except Exception:  # noqa: BLE001
        return lambda *_: None

    def value(sheet, row, col):
        try:
            return X.value(sheet, row, col)
        except Exception:  # noqa: BLE001
            return None
    return value


def _scan(wb, kind: str, codes: set) -> list[dict]:
    out = []
    value = _evaluator(wb) if kind == "audit" else None
    for ws in wb.worksheets:
        if ws.title.startswith("_"):
            continue
        for row in ws.iter_rows(min_row=HEADER_ROW + 1):
            for c in row:
                v = c.value
                if not isinstance(v, str) or not v:
                    continue
                comment = c.comment.text if c.comment else ""
                if kind == "reader":
                    if v.strip().lower() not in codes:
                        continue
                    code, reason = v.strip(), comment
                elif v.startswith("="):
                    lits = [x.replace('""', '"') for x in _LITERAL.findall(v)]
                    hits = [x for x in lits if _AUDIT_CODE.match(x) and is_suspect(x)]
                    if not hits:
                        continue
                    # a formula whose fallback names an absent mechanism but which EVALUATES to a number is no code
                    got = value(ws.title, c.row, c.column)
                    if isinstance(got, (int, float)) and not isinstance(got, bool):
                        continue
                    if isinstance(got, str) and _AUDIT_CODE.match(got):
                        if not is_suspect(got):
                            continue
                        hits = [got]
                    code, reason = _AUDIT_CODE.match(hits[0]).group(1), " | ".join(hits)
                else:
                    m = _AUDIT_CODE.match(v)
                    if not m:
                        continue
                    code, reason = m.group(1), v + (f"\n\n{comment}" if comment else "")
                if not is_suspect(reason):
                    continue
                project = _project(ws, c.row)
                if not project:
                    continue
                out.append({"tab": f"{kind}:{ws.title}", "project": project,
                            "header": str(ws.cell(HEADER_ROW, c.column).value or ""), "code": code,
                            "reason": re.sub(r"\s+", " ", reason).strip()[:400]})
    return out


def suspects(reader_wb, audit_wb) -> list[dict]:
    """Every code cell in the reader and the audit workbook whose reason says a mechanism is absent:
    [{"tab": "reader:<tab>" | "audit:<tab>", "project", "header", "code", "reason"}], reader first."""
    codes = _reader_codes()
    out = []
    r, a = _load(reader_wb), _load(audit_wb)
    if r is not None:
        out += _scan(r, "reader", codes)
    if a is not None:
        out += _scan(a, "audit", codes)
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    hits = suspects(argv[0], argv[1] if len(argv) > 1 else None)
    for h in hits:
        print(f"{h['tab']:<28} {h['project']:<14} {h['header'][:60]:<60} {h['code']:<10} {h['reason'][:160]}")
    print(f"RULE 3 SUSPECTS — {len(hits)}")
    return 0


if __name__ == "__main__":
    import progress                                    # stdout line-buffered when piped (tests/test_progress.py)
    progress.line_buffered()
    sys.exit(main())
