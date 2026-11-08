"""
contradictions.py — a code that contradicts a number (PR1.S9, Jake 2026-11-05).

A reader cell that says a quantity does not exist or does not apply — the code "by design", an "n/a" whose reason
denies the quantity ("no issuance", "not applicable", "no outflow … has a series", "not emitted"), or a 0 shown as the
quantity — while the SAME quantity reads a non-zero number elsewhere in the reader for the same project. Jake's cases
(2026-11-05): Uniswap A4 "Issuance or release" 0 though the key divides by the release, "Revenue needed for crossover" /
"Governance lever" "no issuance", "Total dilution" "by design" beside Summary's dilution; Ether.fi A3 total dilution /
retirement ÷ dilution / net absorption "by design" (emissions not applicable) beside its unlocks; Sky's A3 / A4 total
dilution "no outflow has a series" beside its emissions; Summary's net supply change "by design" (Pendle, Morpho,
Ether.fi) beside their supply flows.

"The same quantity" is a FAMILY of reader columns (FAMILIES, by header) and the families whose non-zero figure proves
it non-zero (EVIDENCE): total dilution is non-zero when emissions, unlocks or the release the A4 key divides by are;
retirement ÷ dilution and net absorption deny their emissions + unlocks leg when they say "by design"; net supply
change says "does not apply" against a supply that moves. A plain "n/a" whose reason says the figure is unknown (not
measured, not stored, blocked) denies nothing and is not a contradiction; "blocked", "not wired", "parked" never are.

    find(wb)  -> [{"project", "sheet", "cell", "header", "shown", "reason", "family", "evidence": [(sheet, cell,
                   header, value), ...]}, ...]       wb = the reader (openpyxl, formulas kept)

The gate test (tests/test_pr1_status.py) runs it on the reader built from the 15 dumps: it must find nothing.
"""
from __future__ import annotations

import re

SHEETS = ("Summary", "A1 Infrastructure", "A2 Coordination", "A3 Revenue Buyback", "A4 Permanent Burn", "Comparison")
HEADER_ROW = 4

# family -> the reader headers (regex, from the start of the header) that show it
FAMILIES = {
    "dilution": r"Total dilution",
    "emissions": r"Emissions(,| in force)",
    "unlocks": r"(Unlocks,|UNLOCK DILUTION)",
    # what A4's key divides by, and A1's issuer releases (pool_release_tokens: the release basis of a pre-minted supply)
    "release": r"(Issuance or release|Issuer releases)",
    # the A4 crossover columns are computed over the release the key divides by: they deny it when they say
    # "no issuance"
    "release_derived": r"(Revenue needed for crossover|Governance lever)",
    # A3's retirement ÷ (emissions + unlocks) and net absorption (retired − emissions [− unlocks]) carry the dilution leg
    "unlock_derived": r"Net absorption incl\. unlocks",
    "dilution_derived": r"(Retired ÷ \(emissions \+ unlocks\)|Net absorption)",
    "net_supply": r"Net supply change",
}
# a family is non-zero when any of these families holds a non-zero number for the project
EVIDENCE = {
    "dilution": ("dilution", "emissions", "unlocks", "release"),
    "emissions": ("emissions",),
    "unlocks": ("unlocks",),
    "release": ("release", "a4_key"),
    "release_derived": ("release", "a4_key"),
    "dilution_derived": ("emissions", "unlocks"),
    "unlock_derived": ("unlocks",),
    "net_supply": ("net_supply", "dilution", "emissions", "unlocks", "release"),
}
# A4's key (burn ÷ the release it divides by): any number there means the release is not 0
A4_KEY = ("A4 Permanent Burn", r"Burn ÷ issuance \(x\)$")
# an "n/a" whose reason says the quantity is absent or inapplicable (not merely unknown)
DENIAL = re.compile(r"no issuance|nothing issued|not applicable|does not apply|not emitted|no outflow .{0,80}has a "
                    r"series|no new supply|returns no|there is no ", re.I)
NEVER = ("blocked", "not wired", "parked", "secondary", "unranked")


def _family(header: str) -> str | None:
    for fam, rx in FAMILIES.items():
        if re.match(rx, header or ""):
            return fam
    return None


def _cells(wb):
    """(sheet, row, col, project, header, value, comment) for every project cell on SHEETS, values evaluated."""
    import xlcalc                                           # noqa: PLC0415
    X = xlcalc.Workbook(wb)
    for sh in SHEETS:
        if sh not in wb.sheetnames:
            continue
        ws = wb[sh]
        heads = {c: str(ws.cell(HEADER_ROW, c).value or "") for c in range(2, ws.max_column + 1)}
        for r in range(HEADER_ROW + 1, ws.max_row + 1):
            proj = ws.cell(r, 1).value
            if not isinstance(proj, str) or not proj.strip() or " — " in proj:
                continue
            for c, h in heads.items():
                cell = ws.cell(r, c)
                raw = cell.value
                if raw is None:
                    continue
                v = raw
                if isinstance(raw, str) and raw.startswith("="):
                    try:
                        v = xlcalc.display(X.value(sh, r, c))
                    except Exception as e:  # noqa: BLE001 — an unevaluable cell is not a number
                        v = f"#ERR {type(e).__name__}"
                yield sh, r, c, proj, h, v, (cell.comment.text if cell.comment else "")


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def find(wb) -> list[dict]:
    from openpyxl.utils import get_column_letter            # noqa: PLC0415
    rows = list(_cells(wb))
    evidence: dict = {}
    for sh, r, c, proj, h, v, _cm in rows:
        fam = "a4_key" if (sh == A4_KEY[0] and re.match(A4_KEY[1], h)) else _family(h)
        if fam and _is_number(v) and v != 0:
            evidence.setdefault((proj, fam), []).append((sh, f"{get_column_letter(c)}{r}", h, v))
    out = []
    for sh, r, c, proj, h, v, cm in rows:
        fam = _family(h)
        if fam is None:
            continue
        reason = " ".join(str(cm or "").split())
        if _is_number(v):
            claim = v == 0 and fam in ("release", "emissions", "unlocks", "dilution")
        elif isinstance(v, str):
            t = v.strip().lower()
            if t in NEVER:
                continue
            claim = t == "by design" or (t == "n/a" and bool(DENIAL.search(reason))) or bool(
                re.search(r"no issuance", t))
        else:
            claim = False
        if not claim:
            continue
        ev = [e for f in EVIDENCE[fam] for e in evidence.get((proj, f), ())
              if not (e[0] == sh and e[1] == f"{get_column_letter(c)}{r}")]
        if ev:
            out.append({"project": proj, "sheet": sh, "cell": f"{get_column_letter(c)}{r}", "header": h,
                        "shown": v, "reason": reason[:300], "family": fam, "evidence": ev})
    return out


def lines(found: list[dict]) -> list[str]:
    """One line per contradiction, for a failing test or a report."""
    return [f"{x['project']} {x['sheet']}!{x['cell']} [{x['header']}] shows {x['shown']!r} ({x['reason'][:140]}) — "
            f"but {'; '.join(f'{s}!{c} [{h}] = {v:,.6g}' for s, c, h, v in x['evidence'][:3])}" for x in found]
