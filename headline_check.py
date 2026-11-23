"""headline_check.py - THE HEADLINE CHECK (R17.C): each Leaderboard figure and every input behind it, with its status.

READ-ONLY. It reads a finished build (the audit workbook, its coverage_audit.csv and the store beside them) and prints
one table per project:

    python headline_check.py --audit token_metrics_audit.xlsx --reader token_metrics.xlsx --db metrics.db
    python headline_check.py --audit A --reader R --db D --projects Maple,Morpho --out docs/headline_check.md

  THE FIGURES  the archetype key, holder value yield, total dilution % of float, net holder yield (the Summary's
               cells), each with its Coverage status (the audit's own: VERIFIED, LIVE-UNVERIFIED, MODELLED, ...) and
               its Credibility verdict (PASS, CHECK, UNVERIFIABLE, ... - "(inputs)" = judged by its input rows).
  THE INPUTS   every Credibility input row the figure's verdict reads (followed down: an input judged by inputs
               lists them too). For each: our value, and
                 CROSS-CHECKED   a second source exists and was read: its name, its value, the gap, the verdict;
                 SINGLE-SOURCE   no second source exists (or the only one re-reads ours): the source that fed OUR
                                 figure is named from the store's own `source` column - never a guess;
                 RECORDED / N/A  shown beside ours, not judged, with the row's own reason.
It checks nothing itself and changes nothing: the verdicts are the Credibility tab's, the statuses the Coverage audit's.
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

FIGURES = (("Holder value yield", "HOLDER VALUE YIELD"), ("Total dilution, % of float", "TOTAL DILUTION = outflows"),
           ("Net holder yield", "NET HOLDER YIELD"))
SHEET, HEAD_ROW = "Credibility", "What is checked"
C_PROJECT, C_TAB, C_CELL, C_WHAT, C_OURS, C_SRC, C_REF, C_DATE, C_GAP, C_TOL, C_VERDICT, C_NOTE = range(1, 13)


def classify(verdict, ref_value, ref_source: str) -> str:
    """CROSS-CHECKED / SINGLE-SOURCE / RECORDED / N/A / DERIVED from an input row's verdict and reference."""
    v = str(verdict or "")
    if "(inputs)" in v:
        return "DERIVED"
    if v.startswith("N/A (recorded"):
        return "RECORDED"
    if v.startswith("N/A"):
        return "N/A"
    if v.startswith("FRESH-only"):
        return "SINGLE-SOURCE"                         # the only reference re-reads our own source
    has_ref = isinstance(ref_value, (int, float)) and not isinstance(ref_value, bool)
    if has_ref and v.startswith(("PASS", "CHECK", "VERIFIED FINDING", "MATURING", "DOCUMENTED")):
        return "CROSS-CHECKED"
    return "SINGLE-SOURCE"


def our_metrics(cell_text: str) -> list:
    """The stored metrics an input row's 'Cell' text names: 'Data m (now)', or the metric names inside a computed row."""
    t = str(cell_text or "")
    m = re.match(r"Data (\S+) \(", t)
    if m:
        return [m.group(1)]
    return sorted(set(re.findall(r"'([a-z][a-z0-9_]{3,})'", t)) - {"metrics", "metric", "minus", "daily", "monthly"})


def stored_source(db, project: str, metric: str):
    """(source, date) of the newest stored row, or None: named from the store, never a guess."""
    if db is None:
        return None
    r = db.execute("select source, date from metrics where project = ? and metric = ? order by date desc limit 1",
                   (project, metric)).fetchone()
    return (str(r[0]), str(r[1])) if r else None


def _fmt(v) -> str:
    if isinstance(v, bool) or v is None:
        return "—" if v is None else str(v)
    if isinstance(v, (int, float)):
        a = abs(v)
        return f"{v:,.0f}" if a >= 1000 else (f"{v:.4f}" if a < 10 else f"{v:,.2f}")
    return str(v).replace("|", "/").replace("\n", " ")[:90]


def _pct(v) -> str:
    return f"{v * 100:+.2f}%" if isinstance(v, (int, float)) and not isinstance(v, bool) else "—"


def key_prefix(arch: int):
    """The audit header the archetype's key reads (reader._archetype_keys). A1's key is computed by the reader from
    the settlement series (no audit cell): its status is the settlement volume's and the circulating supply's."""
    import build_workbook as bw                            # noqa: PLC0415
    return {2: "Customer revenue ÷ emissions value (x)", 3: bw.A3_KEY_HEAD.split(" = ")[0],
            4: "BURN ÷ ISSUANCE (x)"}.get(arch)


class Check:
    def __init__(self, audit_wb, reader_wb, coverage_rows: list, db=None):
        import highlights                                  # noqa: PLC0415
        import xlcalc                                      # noqa: PLC0415
        self.ws = audit_wb[SHEET]
        self.X = xlcalc.Workbook(audit_wb)
        self.L = highlights.Lookup(reader_wb)
        self.cov = coverage_rows
        self.db = db
        self.first = next(r for r in range(1, self.ws.max_row + 1)
                          if self.ws.cell(r, C_WHAT).value == HEAD_ROW) + 1

    def val(self, r, c):
        return self.X.value(SHEET, r, c)

    def rows(self, project: str) -> list:
        return [r for r in range(self.first, self.ws.max_row + 1) if self.ws.cell(r, C_PROJECT).value == project]

    def input_rows(self, r: int, seen=None) -> list:
        """The input rows a derived row's verdict formula reads, followed down, in the order first met."""
        seen = seen if seen is not None else []
        f = self.ws.cell(r, C_VERDICT).value
        if not (isinstance(f, str) and f.startswith("=")):
            return seen
        for n in dict.fromkeys(int(x) for x in re.findall(r"\bK(\d+)\b", f)):
            if n != r and n not in seen:
                seen.append(n)
                if self.ws.cell(n, C_TAB).value == "input":      # another headline is listed once, under its own name
                    self.input_rows(n, seen)
        return seen

    def coverage(self, project: str, prefix: str) -> str:
        got = [r["Status"] for r in self.cov if r["Project"] == project and r["Column"].startswith(prefix)
               and r["Tab"] not in ("Trajectory",)]
        return " / ".join(dict.fromkeys(got)) if got else "no coverage row"

    def input_line(self, project: str, r: int) -> dict:
        ws = self.ws
        verdict, ref, src = self.val(r, C_VERDICT), self.val(r, C_REF), str(ws.cell(r, C_SRC).value or "")
        kind = classify(verdict, ref, src)
        out = {"what": str(ws.cell(r, C_WHAT).value or ""), "ours": self.val(r, C_OURS), "verdict": str(verdict),
               "kind": kind, "detail": ""}
        if kind == "CROSS-CHECKED":
            out["detail"] = (f"second source: {src[:110]} = {_fmt(ref)} ({ws.cell(r, C_DATE).value}); gap "
                             f"{_pct(self.val(r, C_GAP))}, tolerance ±{_pct(self.val(r, C_TOL)).lstrip('+')}")
        elif kind == "SINGLE-SOURCE":
            named = []
            import config                                  # noqa: PLC0415
            for m in our_metrics(ws.cell(r, C_CELL).value)[:4]:
                s = stored_source(self.db, project, config.metric_alias(project, m)) or stored_source(
                    self.db, project, m)
                named.append(f"{m} <- {s[0][:80]} ({s[1][:10]})" if s
                             else f"{m} <- built at build time from stored rows (no row of its own)")
            out["detail"] = ("; ".join(named) if named else
                             f"our own cell {ws.cell(r, C_TAB).value}!{ws.cell(r, C_CELL).value}: no second source "
                             f"exists for it ({str(ws.cell(r, C_NOTE).value or '')[:110]})")
        elif kind == "DERIVED":
            out["detail"] = ("the figure above, judged by its own inputs" if ws.cell(r, C_TAB).value != "input"
                             else "judged by its own input rows (listed after it)")
        else:
            out["detail"] = str(ws.cell(r, C_NOTE).value or "")[:160]
        return out

    def project(self, project: str, key_header: str | None) -> list:
        """Markdown lines: the project's table."""
        rows = self.rows(project)
        lines = [f"### {project}", ""]
        if project not in self.L.rows("Summary"):
            return lines + ["Not in this build.", ""]
        srow = self.L.rows("Summary")[project]

        def summary(h):
            return self.L.X.value("Summary", srow, self.L.headers("Summary")[h][0])
        lines += [f"Confidence (share green): {_fmt(summary('Confidence (share green)'))} · rank in archetype: "
                  f"{_fmt(summary('Rank in archetype'))}", "",
                  "| Figure / input | Value | Coverage status | Credibility verdict | Second source, or single-source |",
                  "|---|---|---|---|---|"]
        figs = [("Archetype key", key_header)] + list(FIGURES)
        for label, prefix in figs:
            if prefix is None:                         # A1: the reader's own computation, no audit cell
                lines.append(f"| **{label}** | {_fmt(summary(label))} | computed by the reader from the settlement "
                             "series and circulating supply (no audit cell) | no Credibility row | — |")
                continue
            r = next((x for x in rows if self.ws.cell(x, C_TAB).value != "input"
                      and str(self.ws.cell(x, C_WHAT).value or "").startswith(prefix)), None)
            verdict = str(self.val(r, C_VERDICT)) if r else (
                "no Credibility row of its own: its inputs are net holder yield's dilution inputs, below"
                if label.startswith("Total dilution") else "no Credibility row of its own")
            v = summary(label)
            shown = _pct(v).lstrip("+") if (label != "Archetype key" and isinstance(v, (int, float))) else _fmt(v)
            lines.append(f"| **{label}** | {shown} | {self.coverage(project, prefix)} | {verdict} | "
                         f"{'its inputs, below' if r else '—'} |")
            for n in (self.input_rows(r) if r else []):
                i = self.input_line(project, n)
                lines.append(f"| ↳ {_fmt(i['what'])} | {_fmt(i['ours'])} |  | {i['verdict']} | "
                             f"{i['kind']} — {i['detail'].replace('|', '/')} |")
        return lines + [""]


def run(audit: str, reader_path: str, db_path: str | None, projects: list) -> list:
    import openpyxl                                        # noqa: PLC0415

    import config                                          # noqa: PLC0415
    import reader                                          # noqa: PLC0415
    a, r = openpyxl.load_workbook(audit), openpyxl.load_workbook(reader_path)
    cov_path = Path(audit).with_name("coverage_audit.csv")
    cov = list(csv.DictReader(open(cov_path, encoding="utf-8"))) if cov_path.exists() else []
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) if db_path else None
    chk = Check(a, r, cov, db)
    out = []
    for name in projects:
        out += chk.project(name, key_prefix(reader._primary(config.PROJECT_BY_NAME[name])))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit", default="token_metrics_audit.xlsx")
    ap.add_argument("--reader", default="token_metrics.xlsx")
    ap.add_argument("--db", default=None)
    ap.add_argument("--projects", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    import config                                          # noqa: PLC0415
    names = [n for n in a.projects.split(",") if n] or [p["name"] for p in config.PROJECTS]
    lines = run(a.audit, a.reader, a.db, names)
    text = "\n".join(lines) + "\n"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"headline check: {len(names)} project(s) -> {a.out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
