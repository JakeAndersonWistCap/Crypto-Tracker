#!/usr/bin/env python3
"""
a1_break_check.py — would a change of measuring point inside the A1 key's one-year span block the key? (P2f-5b)

    python a1_break_check.py [--store metrics.db] [--asof YYYY-MM-DD] [--project NAME]

READ-ONLY AND BEHAVIOUR-NEUTRAL. The store is opened with sqlite's URI mode=ro (nothing is written, no journal, no
schema call); no network is touched; no workbook is built or saved; no key is read. It changes no number anywhere. It
exits 0 always (a store it cannot open is said, not raised).

THE QUESTION (SPEC §1 rule 10 — a change of measuring point is a series break or a declared handover; P2f-5b, Jake
2026-10-29): A1's key (reader.settlement_key) is (settlement_volume_365d_usd ÷ circulating_supply_used, now) ÷ (the
same a year earlier) − 1. The proposed rule: the key cell blocks when a change of measuring point of EITHER input
falls inside its one-year span — after the day `then` (= the series' last date − 365 days) up to and including the
last date. This script prints, for every project the A1 tab lists (reader: a project whose archetypes include 1;
the first-listed is "primary", the others "secondary"):

  * the key's two end dates and the span;
  * each input's measuring points over the span (trajectory.basis_of of the stored `source`), inputs of the inputs
    included (trajectory.basis_breaks follows them: circulating_supply_used -> circulating_supply_onchain ->
    total_supply_protocol ...);
  * every change of measuring point in the span — the date, the stored `source` strings just before and on it, the
    declared handover (config series_handover) or SERIES_BREAK_NOTES entry if any, and whether the existing
    machinery (trajectory._own_breaks) recognised it as declared;
  * the verdict for the proposed rule: WOULD BLOCK or OK.

THE SERIES ARE THE READER'S. The reader reads the post-view groups (build_workbook.aggregate), not the raw rows
(settlement_volume_365d_usd and circulating_supply_used are READ-TIME VIEWS), so the groups come from the same
aggregate() call the daily build makes, on the rows read from the store. The breaks are trajectory.basis_breaks —
the very function reader.settlement_key_trajectory already uses; nothing here re-implements break detection.

    python a1_break_check.py --store metrics.db            Jake's live store (run it with the daily run NOT writing)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

SPAN_DAYS = 365                      # the key's span: a year back from the series' last date (reader.settlement_key)
PROJECT_CAP = 110                    # characters of a source string printed


def open_readonly(path: str) -> sqlite3.Connection:
    """The store, opened read-only. Raises sqlite3.OperationalError when it is absent or unreadable."""
    p = Path(path).resolve()
    if not p.is_file():
        raise sqlite3.OperationalError(f"no store at {p}")
    return sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)


def load_long(con: sqlite3.Connection) -> pd.DataFrame:
    """store.Store.load_long, without the Store class (whose constructor creates the schema): every metric row, a manual
    override replacing a fetched row for the same (date, project, metric)."""
    long = pd.read_sql_query("SELECT date, project, metric, value, source, tier, fetched_at FROM metrics", con)
    long["is_manual"] = False
    long["entered_on"] = ""
    long["source_note"] = ""
    try:
        man = pd.read_sql_query("SELECT date, project, metric, value, source_note, entered_on FROM manual_overrides", con)
    except (pd.errors.DatabaseError, sqlite3.DatabaseError):
        man = pd.DataFrame()
    if not man.empty:
        man["source"] = "manual"
        man["tier"] = None
        man["fetched_at"] = man["entered_on"]
        man["is_manual"] = True
        long = pd.concat([long, man[long.columns]], ignore_index=True).drop_duplicates(
            subset=["date", "project", "metric"], keep="last")
    long["date"] = pd.to_datetime(long["date"])
    return long.sort_values(["project", "metric", "date"]).reset_index(drop=True)


def read_groups(con: sqlite3.Connection, asof: pd.Timestamp | None = None) -> tuple[dict, pd.Timestamp]:
    """({(project, metric): frame} as the reader sees them after every read-time view, the as-of day). No file is
    written: aggregate() works on the frames it is given."""
    sys.modules.setdefault("recalc", None)              # the build's own LibreOffice step is never needed here
    import build_workbook as bw                         # noqa: PLC0415
    import config                                       # noqa: PLC0415
    import store as store_mod                           # noqa: PLC0415
    long = bw.drop_store_only(load_long(con))
    asof = pd.Timestamp(asof).normalize() if asof is not None else (long["date"].max() if not long.empty
                                                                   else pd.Timestamp.now().normalize())
    bw._SCOPE = list(bw.PROJECTS)
    try:                                                # the build's own read-only context queries (store.py)
        config.set_provisional_from_store(store_mod.low_read_days(con, config.PROVIDERS_WITH_INCOMPLETE_CURRENT_PERIOD,
                                                                  config.PROVISIONAL_LOW_READ_RATIO))
        bw.PROVIDER_RUNS.clear()
        bw.PROVIDER_RUNS.update(store_mod.provider_runs(con, config.PROVIDERS_WITH_INCOMPLETE_CURRENT_PERIOD))
        config.set_rwa_turn_progress(store_mod.rwa_turn_progress(con, config.RWA_SHARE_TURN["project"]))
    except Exception:                                   # noqa: BLE001 — a store without the run log: the views read none
        pass
    empty = pd.DataFrame()
    bw.aggregate(long, empty, asof, gaps=empty, review=empty)
    return dict(bw._LAST_GROUPS), asof


def a1_projects() -> list[tuple[str, str]]:
    """[(name, 'primary'|'secondary')] — the projects the A1 tab lists (bw: archetypes include 1)."""
    import build_workbook as bw                         # noqa: PLC0415
    return [(p["name"], "primary" if p["archetypes"][0] == 1 else "secondary") for p in bw.PROJECTS
            if 1 in p["archetypes"]]


def key_metric_closure(project: str, metrics, groups: dict) -> list[str]:
    """The key's two inputs and every series trajectory.basis_breaks follows from them, in discovery order."""
    import trajectory                                   # noqa: PLC0415
    seen, todo = [], list(metrics)
    while todo:
        m = todo.pop(0)
        if m in seen:
            continue
        seen.append(m)
        todo += [x for x in trajectory._inputs(project, m, groups) if x not in seen]
    return seen


def basis_runs(project: str, metric: str, groups: dict, lo: pd.Timestamp, hi: pd.Timestamp) -> list[dict]:
    """The measuring points a series holds between lo and hi: [{basis, from, to, source (first stored string)}]."""
    import trajectory                                   # noqa: PLC0415
    g = groups.get((project, metric))
    if g is None or getattr(g, "empty", True) or "source" not in g:
        return []
    g = g.assign(date=pd.to_datetime(g["date"])).sort_values("date")
    g = g[(g["date"] >= lo) & (g["date"] <= hi)]
    runs: list[dict] = []
    for d, src in zip(g["date"], g["source"].astype(str)):
        b = trajectory.basis_of(metric, src)
        if runs and runs[-1]["basis"] == b:
            runs[-1]["to"] = d
        else:
            runs.append({"basis": b, "from": d, "to": d, "source": src})
    return runs


def stored_source(project: str, metric: str, groups: dict, day: pd.Timestamp, before: bool) -> str:
    """The stored source string of the last row before `day` (before=True) or of the row on `day`."""
    g = groups.get((project, metric))
    if g is None or getattr(g, "empty", True):
        return "(no row)"
    g = g.assign(date=pd.to_datetime(g["date"])).sort_values("date")
    rows = g[g["date"] < day] if before else g[g["date"] >= day]
    if rows.empty:
        return "(no row)"
    r = rows.iloc[-1] if before else rows.iloc[0]
    return f"{r['source']}  [row dated {pd.Timestamp(r['date']).date()}]"


def declared_note(project: str, metric: str, day: pd.Timestamp, before: str, after: str) -> list[str]:
    """What config says about this series' change on `day`: the declared handover (series_handover) and why
    trajectory._own_breaks does or does not treat the change between the two stored bases as declared, then the
    SERIES_BREAK_NOTES / SOURCE_RECLASSIFICATIONS entries."""
    import config                                       # noqa: PLC0415
    out = []
    decl = config.declared_handover(project, metric)
    if decl:
        cc = str(decl.get("composition_change", "none")).strip()
        pts = tuple(decl.get("ordered_points") or ())
        if not cc.lower().startswith("none"):
            state = "its composition_change declares a STEP (it does not start 'none'): reported by design"
        elif before in pts and after in pts:
            state = "both stored bases are among its ordered_points: recognised, not a break"
        else:
            state = (f"NOT RECOGNISED: the stored bases ('{before[:60]}', '{after[:60]}') are not both among its "
                     "ordered_points as written")
        out.append(f"declared handover on file ({decl.get('declared') or decl.get('declared_on') or 'undated'}): "
                   f"{' -> '.join(str(x) for x in pts)}; composition_change: {cc[:150]} -- {state}")
    note = config.SERIES_BREAK_NOTES.get((project, metric))
    if note:
        out.append(f"SERIES_BREAK_NOTES: break {note.get('break')} (day {note.get('day')}): {str(note.get('text'))[:200]}")
    for rday, why in config.SOURCE_RECLASSIFICATIONS.get((project, metric), ()):
        if pd.Timestamp(rday) == day:
            out.append(f"SOURCE_RECLASSIFICATIONS {rday}: {str(why)[:200]}")
    return out


def check_project(groups: dict, project: str, role: str = "primary") -> dict:
    """The facts and the verdict of the proposed rule for one project (no printing)."""
    import reader                                       # noqa: PLC0415
    import trajectory                                   # noqa: PLC0415
    s = reader._series(groups, project, reader.A1_KEY_METRICS[0])
    c = reader._series(groups, project, reader.A1_KEY_METRICS[1])
    out: dict = {"project": project, "role": role, "settle": None if s.empty else (s.index.min(), s.index.max()),
                 "circ": None if c.empty else (c.index.min(), c.index.max()), "breaks": [], "declared_ok": []}
    out["key"], out["key_why"] = reader.settlement_key(groups, project)
    anchor = s.index.max() if not s.empty else (c.index.max() if not c.empty else None)
    out["anchored_on"] = ("settlement" if not s.empty else "circulating (no settlement series: the span is where the "
                          "key would read)") if anchor is not None else None
    if anchor is None:
        out["verdict"] = "OK"
        out["verdict_why"] = "no key series stored, nothing to block"
        return out
    end, then = anchor, anchor - pd.Timedelta(days=SPAN_DAYS)
    out["end"], out["then"] = end, then
    metrics = key_metric_closure(project, reader.A1_KEY_METRICS, groups)
    out["metrics"] = metrics
    out["runs"] = {m: basis_runs(project, m, groups, then, end) for m in metrics}
    # the proposed rule: a change strictly after `then` (the day the earlier value is read) up to the last date
    found = [b for b in trajectory.basis_breaks(project, reader.A1_KEY_METRICS, groups,
                                                then + pd.Timedelta(days=1)) if b[0] <= end]
    for d, m, a, b in found:
        d = pd.Timestamp(d)
        out["breaks"].append({"date": d, "metric": m, "before": a, "after": b,
                              "stored_before": stored_source(project, m, groups, d, True),
                              "stored_on": stored_source(project, m, groups, d, False),
                              "config": declared_note(project, m, d, str(a), str(b))})
    reported = {(pd.Timestamp(b["date"]), b["metric"]) for b in out["breaks"]}
    # changes the machinery recognised as a declared handover (benign): in the span, not reported by basis_breaks
    for m in metrics:
        runs = out["runs"][m]
        for prev, nxt in zip(runs, runs[1:]):
            if (nxt["from"], m) in reported or nxt["from"] <= then:
                continue
            out["declared_ok"].append({"date": nxt["from"], "metric": m, "before": prev["basis"], "after": nxt["basis"],
                                       "config": declared_note(project, m, nxt["from"], prev["basis"], nxt["basis"])})
    out["verdict"] = "WOULD BLOCK" if out["breaks"] else "OK"
    # NOT PART OF THE PROPOSED RULE, SAID FOR JAKE: the 365-day sum reads the DAILY series' days from `then` - 365 to
    # the last date, but build_workbook._nrr_rows labels every day of the sum with the LAST day's source, so a change of
    # measuring point inside the daily series (Ethereum: Artemis CSV to 2026-08-25, the rebuild from 08-26) is
    # invisible to basis_breaks on settlement_volume_365d_usd. The daily series' own changes are listed here.
    import config                                       # noqa: PLC0415
    daily = config.ARTEMIS_SETTLEMENT["metric"]
    out["daily_metric"] = daily
    out["daily_breaks"] = [b for b in trajectory.basis_breaks(project, (daily,), groups, then - pd.Timedelta(days=SPAN_DAYS) +
                                                              pd.Timedelta(days=1)) if b[0] <= end]
    out["daily_breaks"] = [dict(date=pd.Timestamp(d), metric=m, before=a, after=b,
                                config=declared_note(project, m, pd.Timestamp(d), str(a), str(b)))
                           for d, m, a, b in out["daily_breaks"]]
    return out


def render(r: dict) -> list[str]:
    L = []
    L.append(f"=== {r['project']} ({r['role']} A1) ===")
    cap = lambda x: str(x)[:PROJECT_CAP]                # noqa: E731
    k = "n/a" if r["key"] is None else f"{r['key']:+.4f}"
    L.append(f"  key today: {k}" + ("" if r["key"] is not None else f" — {cap(r['key_why'])}"))
    for lab, rng in (("settlement_volume_365d_usd", r["settle"]), ("circulating_supply_used", r["circ"])):
        L.append(f"  {lab}: " + ("not held" if rng is None else f"held {rng[0].date()}..{rng[1].date()}"))
    if r.get("anchored_on") is None:
        L.append(f"  verdict: {r['verdict']} ({r['verdict_why']})")
        return L
    L.append(f"  the key's two end dates: {r['then'].date()} and {r['end'].date()}  "
             f"(span {SPAN_DAYS} days back from the {r['anchored_on']} series' last date; a change after "
             f"{r['then'].date()} up to {r['end'].date()} counts)")
    for m in r["metrics"]:
        runs = r["runs"][m]
        L.append(f"  measuring points of {m} over the span:" + ("  (no row in the span)" if not runs else ""))
        for u in runs:
            L.append(f"      {u['from'].date()}..{u['to'].date()}  {cap(u['basis'])}")
    if r["breaks"]:
        L.append("  CHANGE OF MEASURING POINT INSIDE THE SPAN (not recognised as declared):")
        for b in r["breaks"]:
            L.append(f"      {b['date'].date()}  {b['metric']}:  {cap(b['before'])}  ->  {cap(b['after'])}")
            L.append(f"          stored before: {cap(b['stored_before'])}")
            L.append(f"          stored on/after: {cap(b['stored_on'])}")
            for n in b["config"] or ["no declared handover, SERIES_BREAK_NOTES or reclassification entry for this series"]:
                L.append(f"          config: {n[:420]}")
    if r["declared_ok"]:
        L.append("  changes inside the span that the machinery treats as a DECLARED handover (do not block):")
        for b in r["declared_ok"]:
            L.append(f"      {b['date'].date()}  {b['metric']}:  {cap(b['before'])}  ->  {cap(b['after'])}")
            for n in b["config"]:
                L.append(f"          config: {n[:420]}")
    if not r["breaks"] and not r["declared_ok"]:
        L.append("  no change of measuring point inside the span")
    if r.get("daily_breaks"):
        L.append(f"  NOTE (outside the proposed rule): the 365-day sum reads {r['daily_metric']}'s days "
                 f"{(r['then'] - pd.Timedelta(days=SPAN_DAYS) + pd.Timedelta(days=1)).date()}..{r['end'].date()}, and "
                 "the 365d view labels every day with the last day's source, so these changes of the DAILY series are "
                 "invisible to the rule:")
        for b in r["daily_breaks"]:
            L.append(f"      {b['date'].date()}  {b['metric']}:  {cap(b['before'])}  ->  {cap(b['after'])}")
            for n in b["config"]:
                L.append(f"          config: {n[:420]}")
    eff = ("the key is n/a today for another reason, so no cell changes" if r["key"] is None else
           "the key's number would become 'blocked'")
    L.append(f"  verdict (proposed rule): {r['verdict']}" + (f" — {eff}" if r["verdict"] == "WOULD BLOCK" else ""))
    return L


def run(store: str, asof: str | None = None, only: str | None = None, out=print) -> list[dict]:
    """Print the report; return the per-project results. Never raises on a missing/unreadable store."""
    try:
        con = open_readonly(store)
    except sqlite3.Error as e:
        out(f"a1_break_check: cannot open {store} read-only: {e}")
        return []
    try:
        groups, day = read_groups(con, pd.Timestamp(asof) if asof else None)
    except Exception as e:                              # noqa: BLE001 — exit 0 always, said
        out(f"a1_break_check: could not read the series from {store}: {type(e).__name__}: {e}")
        return []
    finally:
        con.close()
    out(f"a1_break_check — store {store}, as of {day.date()} (read-only; no network; nothing written)")
    out("rule proposed: the A1 key blocks when a change of measuring point of settlement_volume_365d_usd or "
        "circulating_supply_used (or a series they read) falls inside its one-year span")
    out("")
    results = []
    for name, role in a1_projects():
        if only and name != only:
            continue
        try:
            r = check_project(groups, name, role)
        except Exception as e:                          # noqa: BLE001
            out(f"=== {name} ({role} A1) ===\n  could not be checked: {type(e).__name__}: {e}")
            continue
        results.append(r)
        for line in render(r):
            out(line)
        out("")
    out("SUMMARY (project | role | key today | verdict)")
    for r in results:
        k = "n/a" if r["key"] is None else f"{r['key']:+.4f}"
        out(f"  {r['project']:<12} {r['role']:<9} {k:>10}  {r['verdict']}")
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Would a change of measuring point inside the A1 key's one-year span "
                                                 "block the key? Read-only.")
    ap.add_argument("--store", default="metrics.db", help="the store (opened read-only); default metrics.db")
    ap.add_argument("--asof", default=None, help="as-of day for the read-time views; default the newest stored date")
    ap.add_argument("--project", default=None, help="one project only")
    a = ap.parse_args(argv)
    try:                                                # a Windows console must not lose the report to a character
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    try:
        run(a.store, a.asof, a.project)
    except Exception as e:                              # noqa: BLE001 — exit 0 always
        print(f"a1_break_check: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    import progress                                    # stdout line-buffered when piped (2026-10-11)
    progress.line_buffered()
    sys.exit(main())
