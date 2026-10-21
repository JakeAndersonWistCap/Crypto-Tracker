"""
wiring.py — THE WIRING AUDIT (Round G12 A1, Jake 2026-10-21): does every stored or built series with a number reach the
workbook?

Every Data row with a number in any window is classified from the code's OWN dependency graph, recorded while the
build runs (never from names or text):
  (1) SHOWN       — read by a cell on Master / A1–A4 / CP / Flows / Trajectory: a formula that reads its Data row,
                    or a display writer that read the series while building a cell;
  (2) CROSS-CHECK — read by a Credibility row (as ours or as the reference): its spec names the series, a Credibility
                    formula reads its Data row, or the Credibility writer read it;
  (3) INPUT       — read by a read-time view that wrote a series that is (1) or (2), transitively; the parents named;
  (4) DECLARED NOT SHOWN — config.NOT_SHOWN, with its one-line reason.
Anything else is UNWIRED.

HOW THE GRAPH IS RECORDED. aggregate() wraps its groups in TrackingDict: every read (get / [] / in) and write ([]=) of a
(project, metric) key is attributed to every named function on the stack down to aggregate() (the view and the
helpers it called). A view that read r and
wrote w for the same project gives the edge r -> w. _LAST_GROUPS and data_by_key are TrackingDicts too: reads made while
a display writer runs (phase "shown") or while the Credibility writer runs (phase "cross") are recorded as such.
"""
from __future__ import annotations

import contextlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

_STATE = {"phase": None}
READS: list = []        # (project, metric, (func, ...) innermost first, phase)
WRITES: list = []       # (project, metric, (func, ...) innermost first)
_SKIP_FUNCS = {"aggregate", "build_workbook", "<module>"}


def reset() -> None:
    READS.clear()
    WRITES.clear()
    _STATE["phase"] = None


@contextlib.contextmanager
def phase(name: str | None):
    """Reads made inside are recorded as `name` ("shown", "cross")."""
    was = _STATE["phase"]
    _STATE["phase"] = name
    try:
        yield
    finally:
        _STATE["phase"] = was


def _caller() -> tuple:
    """Every named function on the stack between the access and aggregate() (innermost first): a view that delegates
    the read to a helper and writes in its own body is still one view."""
    f = sys._getframe(2)
    out = []
    while f is not None:
        n = f.f_code.co_name
        if n in _SKIP_FUNCS:
            break
        if not n.startswith("<") and f.f_code.co_filename != __file__:
            out.append(n)
        f = f.f_back
    return tuple(out)


def _key(k):
    """(project, metric) from a groups key or a data_by_key key ("project|metric"), else None."""
    if isinstance(k, tuple) and len(k) == 2 and all(isinstance(x, str) for x in k):
        return k
    if isinstance(k, str) and "|" in k:
        p, m = k.split("|", 1)
        return p, m
    return None


def note(project: str, metric: str) -> None:
    """A read made outside a TrackingDict (credibility formulas read the long frame through credibility._series)."""
    READS.append((project, metric, _caller(), _STATE["phase"]))


class TrackingDict(dict):
    """A dict that records which function read and wrote which (project, metric)."""

    def _read(self, k):
        kk = _key(k)
        if kk is not None:
            READS.append((kk[0], kk[1], _caller(), _STATE["phase"]))

    def __getitem__(self, k):
        self._read(k)
        return super().__getitem__(k)

    def get(self, k, default=None):
        self._read(k)
        return super().get(k, default)

    def __contains__(self, k):
        self._read(k)
        return super().__contains__(k)

    def __setitem__(self, k, v):
        kk = _key(k)
        if kk is not None:
            WRITES.append((kk[0], kk[1], _caller()))
        super().__setitem__(k, v)


# --------------------------------------------------------------------------------------------------------- the graph
def view_edges() -> dict:
    """{(project, written metric): {(read metric, view function), ...}} — a view that read r and wrote w for the same
    project, both recorded outside any display phase."""
    reads, writes = {}, {}
    for p, m, fs, ph in READS:
        if ph is None:
            for f in fs:
                reads.setdefault((p, f), set()).add(m)
    for p, m, fs in WRITES:
        for f in fs:
            writes.setdefault((p, f), set()).add(m)
    out: dict = {}
    for (p, f), ws in writes.items():
        for w in ws:
            for r in reads.get((p, f), ()):
                if r != w:
                    out.setdefault((p, w), set()).add((r, f))
    return out


# Reads made only to STYLE a cell (its confidence fill, the Data-flags column, the confidence tally, an n/a's reason):
# a display writer looked the series up, but no cell shows it. _write_table as the innermost named function is its own
# styling code (builders are lambdas, skipped by _caller, so a builder's direct read is attributed there too — the one
# such builder, Master's "Latest data date", reads price / fees / revenue / TVL, all shown by formulas).
STYLE_FUNCS = {"_write_table", "_flags", "_confidence_tally", "_reasoned_na"}

_FORMULA_REF = re.compile(r'\$A\$?(\d+)&"\|([a-z0-9_]+)",Data!')
SHOWN_SHEETS = ("Master", "A1", "A2", "A3", "A4", "CP", "Trajectory")


def formula_refs(wb) -> tuple[dict, dict]:
    """({(project, metric): {sheet, ...}} read by a formula on a display sheet, {...} read by a Credibility formula)."""
    shown, cross = {}, {}
    for ws in wb.worksheets:
        target = shown if ws.title.startswith(SHOWN_SHEETS) else cross if ws.title == "Credibility" else None
        if target is None:
            continue
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if isinstance(v, str) and v.startswith("=") and "Data!" in v:
                    for r, m in _FORMULA_REF.findall(v):
                        proj = ws.cell(int(r), 1).value
                        if isinstance(proj, str) and proj:
                            target.setdefault((proj, m), set()).add(ws.title)
    return shown, cross


_TEXT_KEYS = ("note", "what", "source", "how", "why", "resolve", "summary", "label")


def _strings(obj, out: set) -> None:
    """Every string a spec carries as a VALUE it computes with — prose fields (a note, a why, a source) are skipped, so
    a series merely mentioned in a sentence is not counted as read."""
    if isinstance(obj, str):
        out.add(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in _TEXT_KEYS:
                _strings(v, out)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            _strings(v, out)


def credibility_refs(rows: list, known: set) -> set:
    """{(project, metric)} a Credibility row's spec names (ours or reference) — only names that ARE the project's series."""
    out = set()
    for r in rows or ():
        p = r.get("project")
        if not p:
            continue
        s: set = set()
        _strings(r, s)
        out |= {(p, m) for m in s if (p, m) in known}
    return out


def flows_refs(rows: list, known: set) -> set:
    """{(project, metric)} named by a Flows line's Series."""
    out = set()
    for r in rows or ():
        p, s = r.get("Project"), str(r.get("Series") or "")
        for tok in re.findall(r"[a-z][a-z0-9_]+", s):
            if (p, tok) in known:
                out.add((p, tok))
    return out


# ------------------------------------------------------------------------------------------------------- the audit
WINDOWS = ("now", "m1", "q0", "q1", "q2", "q3", "q4", "y1")


def _has_number(row: dict) -> bool:
    for w in WINDOWS:
        v = row.get(w)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v:
            return True
    return False


def audit(data_rows: list, wb=None, cred_rows=None, flows_rows=None, projects=None) -> dict:
    """{(project, metric): {"class", "why", "label", "latest", "latest_date"}} for every Data row with a number."""
    import config
    universe = {(r["project"], r["metric"]): r for r in data_rows if _has_number(r)
                and (projects is None or r["project"] in projects)}
    known = set(universe) | {(r["project"], r["metric"]) for r in data_rows}
    shown_f, cross_f = formula_refs(wb) if wb is not None else ({}, {})
    # WHERE each is read (G13 10: wiring.json carries a parent or a reason for EVERY series)
    where: dict = {k: set(v) for k, v in shown_f.items()}
    for k in flows_refs(flows_rows, known):
        where.setdefault(k, set()).add("Flows")
    cwhere: dict = {k: set(v) for k, v in cross_f.items()}
    for k in credibility_refs(cred_rows, known):
        cwhere.setdefault(k, set()).add("Credibility (row spec)")
    for p, m, fs, ph in READS:
        if ph == "shown" and fs and fs[0] in STYLE_FUNCS:
            continue                      # a confidence fill, a flag or an n/a reason — no cell shows the figure
        if ph == "shown":
            where.setdefault((p, m), set()).add(f"display builder {fs[0] if fs else '?'}")
        elif ph == "cross":
            cwhere.setdefault((p, m), set()).add(f"Credibility ({fs[0] if fs else '?'})")
    shown, cross = set(where), set(cwhere)
    edges = view_edges()
    # FETCH-TIME DERIVATIONS (config.FETCH_INPUTS): a stored series a fetcher turns into another stored series
    for p in {k[0] for k in universe}:
        for child, (parents, module) in config.fetch_inputs(p).items():
            for par in parents:
                edges.setdefault((p, child), set()).add((par, f"fetch: {module}"))
    # INPUT: walk down from every shown / cross-checked series through the views that built it
    parents: dict = {}
    todo = list(shown | cross)
    seen = set(todo)
    while todo:
        w = todo.pop()
        for r, f in edges.get(w, ()):
            k = (w[0], r)
            parents.setdefault(k, set()).add((w[1], f))
            if k not in seen:
                seen.add(k)
                todo.append(k)
    out = {}
    for k, row in universe.items():
        ns = config.not_shown_reason(*k)
        if k in shown:
            cls, why = "SHOWN", "read on " + ", ".join(sorted(where[k]))[:200]
        elif k in cross:
            cls, why = "CROSS-CHECK", "read by " + ", ".join(sorted(cwhere[k]))[:200]
        elif k in parents:
            ps = sorted(parents[k])
            cls, why = "INPUT", "; ".join(f"{m} ({f})" for m, f in ps[:4]) + ("…" if len(ps) > 4 else "")
        elif ns:
            cls, why = "NOT SHOWN", ns
        elif _closed_before(k, row):
            cls, why = "NOT SHOWN", _closed_before(k, row)
        else:
            cls, why = "UNWIRED", ""
        latest = next((row.get(w) for w in ("now", "q0", "m1", "y1", "q1", "q2", "q3", "q4")
                       if isinstance(row.get(w), (int, float)) and not isinstance(row.get(w), bool)
                       and row.get(w) == row.get(w)), None)
        out[k] = {"class": cls, "why": why, "label": str(row.get("label") or "")[:120], "latest": latest,
                  "latest_date": str(row.get("latest_date") or "")}
    return out


def _closed_before(key, row) -> str:
    """A figure CLOSED in config.UNAVAILABLE (chased and closed: its cell reads "none available") whose stored rows all
    predate the closure: declared not shown, the closure its reason. A row NEWER than the closure contradicts it and
    stays UNWIRED."""
    import config
    u = config.unavailable_for(*key)
    if not u or u.get("route_only") or u.get("renders_as_zero_since"):
        return ""
    closed, last = str(u.get("closed_on") or ""), str(row.get("latest_date") or "")[:10]
    if not closed or not last or last > closed:
        return ""
    return f"CLOSED {closed} (config UNAVAILABLE; rows stored to {last}): {str(u.get('summary') or '')[:160]}"


def summary(result: dict) -> dict:
    """{"counts": {class: n}, "by_project": {project: {class: n}}, "unwired": n}."""
    counts, byp = {}, {}
    for (p, _m), v in result.items():
        counts[v["class"]] = counts.get(v["class"], 0) + 1
        byp.setdefault(p, {}).setdefault(v["class"], 0)
        byp[p][v["class"]] += 1
    return {"counts": counts, "by_project": byp, "unwired": counts.get("UNWIRED", 0)}


def write(result: dict, path: Path) -> None:
    rows = [{"project": p, "metric": m, **v} for (p, m), v in sorted(result.items())]
    Path(path).write_text(json.dumps({"summary": summary(result), "rows": rows}, indent=1, default=str),
                          encoding="utf-8")


def report(path: Path, unwired_only: bool = False) -> str:
    """The text `check_offline_items.py wiring` prints from the last build's wiring.json."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    s = d["summary"]
    lines = [f"WIRING — {sum(s['counts'].values())} series with a number: "
             + ", ".join(f"{k} {v}" for k, v in sorted(s["counts"].items())) + f" — UNWIRED {s['unwired']}"]
    for p, c in sorted(s["by_project"].items()):
        lines.append(f"  {p}: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))
    rows = [r for r in d["rows"] if r["class"] == "UNWIRED" or not unwired_only]
    cur = None
    for r in sorted(rows, key=lambda r: (r["project"], r["class"], r["metric"])):
        if r["class"] != "UNWIRED" and unwired_only:
            continue
        if r["project"] != cur:
            cur = r["project"]
            lines.append(f"\n{cur}")
        v = r["latest"]
        vs = f"{v:,.6g}" if isinstance(v, (int, float)) else "—"
        lines.append(f"  {r['class']:<12} {r['metric']:<48} {vs:>14} {r['latest_date']:<10} "
                     f"{(r['why'] or r['label'])[:110]}")
    return "\n".join(lines)
