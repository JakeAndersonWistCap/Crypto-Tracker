#!/usr/bin/env python3
"""Run ONE section of orphan_cleanup.sql against metrics.db, and print what it finds.

WHY THIS EXISTS
---------------
The cleanup SQL is written to be READ before it is run — every section looks first and deletes
second, and the DELETEs ship commented out on purpose. That discipline only works if looking is
easy. It was not: running the SELECTs meant pasting SQL into an inline `python -c` or a sqlite3
shell, and on Windows PowerShell the quoting around string literals broke repeatedly, which turns
"read this before you delete anything" into "fight the shell, then guess".

So: `python run_sql.py E` runs section E's SELECTs and prints them. No SQL is typed, no quoting is
involved, and nothing is deleted.

USAGE
-----
    python run_sql.py                 list the sections, with what each one is about
    python run_sql.py E               run section E's SELECTs and print the results
    python run_sql.py AB              same, for a two-letter section — the file runs A..Z
                                      and then AA, AB, ... like spreadsheet columns
    python run_sql.py E --db other.db run against a different store
    python run_sql.py --delete E      run section E's DELETE, after showing what goes and
                                      asking for typed confirmation

THE TWO MODES ARE SEPARATE ON PURPOSE. Plain mode REFUSES to execute anything that is not a
SELECT, even if a DELETE has been uncommented in the file — so a half-finished edit cannot delete
rows because somebody ran the "just look" command. Deleting takes the explicit --delete flag, a
typed confirmation, and prints the rows first.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sqlite3
import sys
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
SQL_FILE = HERE / "orphan_cleanup.sql"
DEFAULT_DB = HERE / "metrics.db"

# A section starts at its first marker and runs to the next label's first marker. Sections are
# headed inconsistently in the file — some open with "-- A. Title", some go straight to "-- D1." —
# so both spellings are matched rather than relying on one convention the file does not keep.
#
# ===== ** TWO-LETTER LABELS. Fixed 2026-09-23, and the symptom was NOT a missing section. ** =====
# This was `([A-Z])` — exactly one letter — so when the file passed Z and went to AA, the new
# markers stopped matching ENTIRELY. They were not reported as unknown; they were invisible, and
# their SQL was absorbed into whichever section preceded them.
#
# ** THAT MADE IT A DELETE-SAFETY BUG, not a listing bug. ** Section Z (Pendle's re-attribution)
# swallowed AA and AB, so `--delete Z` collected AA's `DELETE FROM metrics WHERE project =
# 'Uniswap'` alongside Pendle's UPDATE and offered them under one prompt reading "DELETE Z".
# Somebody approving the Pendle move would have taken the Uniswap delete with it.
#
# ** WHY {1,2} AND NOT [A-Z]+. ** A bare [A-Z]+ invents sections out of ordinary prose: this file
# contains "-- FIX. Nothing renders wrongly...", "CAKE.balanceOf(...)" and "PENDLE.balanceOf(...)",
# all of which match `^--\s*[A-Z]+\.`, and each would have become a phantom section stealing the
# SQL that followed it. Two letters covers A..ZZ — 702 sections, and the file is at AB — and
# `_check_section_labels` below fails loudly if a longer label is ever added, so the next person
# gets an error rather than the silence this bug lived in.
SECTION_MARKER = re.compile(r"^--\s?([A-Z]{1,2})(\d*)\.\s?(.*)$")
# What a section label may look like on the command line. Kept separate from SECTION_MARKER:
# this validates what a HUMAN typed, where there is no prose to be confused with, so it is the
# permissive form. An over-long label falls through to the ordinary "no section X" message with
# the available list, which is the answer the caller needs.
SECTION_ARG = re.compile(r"^[A-Z]+$")

# ===== WHEN A SECTION WAS WRITTEN, SO ITS PREVIEW CAN SAY WHAT POST-DATES IT. 2026-09-23. =====
# Section headers end with an authoring stamp — "2026-09-22" by convention, or a precise
# "2026-09-23T21:30Z" — on the marker line or one of the two after it. It is read so that a
# preview can separate the rows a section was WRITTEN AGAINST from rows written into the store
# later, which the section's WHERE clause never saw.
#
# ** THE NEAR-MISS THAT PROMPTED IT. ** Section U selected `date >= '2026-09-12'` because on
# the day it was written every row from the break onward was the parent residual. morpho-blue
# recovered from 2026-09-21 and the recovery wrote two real days into that range. U's preview
# listed them silently beside the nine targets, and its DELETE would have removed the only good
# post-break data.
AUTHORED_STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?Z)?)\s*$")


def _decode_safe() -> None:
    """Windows consoles default to cp1252 and the SQL file is full of em-dashes.

    Without this, printing a section title raises UnicodeEncodeError and the tool fails at the
    one job it exists to make easy. Replacing unmappable characters is the right trade here: a
    mangled dash in a comment costs nothing, a crash costs the whole run.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def split_statements(sql: str) -> list[str]:
    """Split on semicolons that are OUTSIDE string literals and outside `--` comments.

    A naive split on ";" is wrong twice over: it breaks on a semicolon inside a quoted source
    string, and it breaks on one inside prose ("Left unfilled deliberately; there is no store").
    Both exist in this file's history and both produced fragments that look like broken SQL.
    """
    out, buf, in_str, in_comment = [], [], False, False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_comment:
            if ch == "\n":
                in_comment = False
            buf.append(ch)
        elif in_str:
            buf.append(ch)
            if ch == "'":
                if i + 1 < len(sql) and sql[i + 1] == "'":   # '' is an escaped quote
                    buf.append(sql[i + 1])
                    i += 1
                else:
                    in_str = False
        elif ch == "-" and sql[i:i + 2] == "--":
            in_comment = True
            buf.append(ch)
        elif ch == "'":
            in_str = True
            buf.append(ch)
        elif ch == ";":
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    if "".join(buf).strip():
        out.append("".join(buf))
    return out


def strip_comments(stmt: str) -> str:
    return "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--")).strip()


def parse_sections(sql: str) -> dict[str, dict]:
    """{letter: {"title", "start", "end", "text"}} — first marker of a letter to the next letter."""
    lines = sql.splitlines(keepends=True)
    starts: list[tuple[str, int, str]] = []
    for n, line in enumerate(lines):
        m = SECTION_MARKER.match(line.rstrip())
        if not m:
            continue
        letter, _, title = m.groups()
        if starts and starts[-1][0] == letter:
            continue                                  # a later step of the same section
        starts.append((letter, n, title.strip()))

    sections: dict[str, dict] = {}
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line)
    offsets.append(pos)

    for idx, (letter, n, title) in enumerate(starts):
        end_line = starts[idx + 1][1] if idx + 1 < len(starts) else len(lines)
        # A section headed "-- D1. LOOK ONLY — ..." has no title of its own. Fall back to the
        # first substantive line AFTER the nearest ==== rule above it, which is where this file
        # puts its section headings. Anchoring on the rule matters: "the nearest comment line
        # above" picks up whatever prose or commented-out SQL happens to sit there, which is how
        # section C ended up titled "AND source LIKE 'derived:%';".
        if not title or title.split(None, 1)[0] in ("LOOK", "THE", "VERIFY", "AND", "WHERE"):
            rule = None
            for back in range(n - 1, max(n - 60, -1), -1):
                probe = lines[back].strip()
                if probe.startswith("--") and set(probe[2:].strip()) <= {"=", "-"} and len(probe) > 20:
                    rule = back
                    break
            if rule is not None:
                for fwd in range(rule + 1, n):
                    probe = lines[fwd].strip()
                    if not probe.startswith("--"):
                        continue
                    body = probe.lstrip("- ").strip()
                    if body and not set(body) <= {"=", "-"}:
                        title = body
                        break
        authored = None
        for k in range(n, min(n + 3, end_line)):
            probe = lines[k].rstrip()
            m_st = AUTHORED_STAMP.search(probe)
            if probe.lstrip().startswith("--") and m_st:
                authored = m_st.group(1)
                break
        sections[letter] = {
            "title": title or "(no title)",
            "line": n + 1,
            "text": sql[offsets[n]:offsets[end_line]],
            "authored": authored,
        }
    return sections


# Prose in the SQL file that LOOKS like a section marker and is not. Each was checked by hand
# on 2026-09-23. The list exists so that a NEW one has to be looked at rather than absorbed: see
# _check_section_labels.
KNOWN_NON_SECTIONS = {"FIX", "CAKE", "PENDLE"}


def _next_label(label: str) -> str:
    """A, B, ... Z, AA, AB — the spreadsheet-column sequence the sections are named in."""
    chars = list(label)
    i = len(chars) - 1
    while i >= 0:
        if chars[i] != "Z":
            chars[i] = chr(ord(chars[i]) + 1)
            return "".join(chars)
        chars[i] = "A"
        i -= 1
    return "A" + "".join(chars)


def _check_section_labels(sql: str, sections: dict) -> list[str]:
    """Problems with how the file's section headers parse, or [] if there are none.

    ** THIS EXISTS BECAUSE THE FAILURE IT CATCHES WAS SILENT. ** When the file went past Z, the
    one-letter SECTION_MARKER stopped matching and AA/AB were not reported as unknown — they were
    absorbed into section Z, taking a Uniswap DELETE into Z's confirmation prompt with them. A
    parser that cannot see a section must SAY so.
    """
    problems = []
    # 1. A marker with a longer label than SECTION_MARKER accepts. Either it is a new section and
    #    the pattern needs widening, or it is prose and belongs in KNOWN_NON_SECTIONS. Both are
    #    decisions for a person; neither is something to guess at silently.
    long_marker = re.compile(r"^--\s?([A-Z]{3,})\d*\.")
    for line in sql.splitlines():
        m = long_marker.match(line.rstrip())
        if m and m.group(1) not in KNOWN_NON_SECTIONS:
            problems.append(
                f"{m.group(1)!r} looks like a section header but SECTION_MARKER accepts at most "
                f"two letters, so it would be INVISIBLE and its SQL absorbed into the section "
                f"above it. Widen SECTION_MARKER if it is a section, or add it to "
                f"KNOWN_NON_SECTIONS if it is prose. Line: {line.strip()[:80]!r}")
    # 2. The labels that DID parse must run A, B, ... Z, AA, AB with no holes. A hole means a
    #    header was skipped, which is the same silence in a different shape.
    got = list(sections)
    expected = "A"
    for label in got:
        if label != expected:
            problems.append(f"section sequence jumps: expected {expected!r}, found {label!r} — "
                            f"a header between them was not parsed")
            expected = label
        expected = _next_label(expected)
    return problems


def _utc(stamp) -> datetime | None:
    """An ISO-ish timestamp as an aware UTC datetime, or None. Accepts Z, +00:00, a space for T."""
    if stamp is None:
        return None
    t = str(stamp).strip().replace(" ", "T", 1)
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _num(v) -> str:
    return f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def fetch_provenance(cols: list[str], rows: list, authored: str | None,
                     now: datetime | None = None) -> list[str]:
    """Lines to print under a preview: which rows were last WRITTEN after the section was authored.

    ** WHAT THIS CAN AND CANNOT SEE. ** `fetched_at` is when a row was last WRITTEN, not when it
    first appeared — store.py's upsert sets fetched_at = excluded.fetched_at. So "written after
    this section" includes rows that already existed and were rewritten since, not only rows
    that are new. That is still the right question — a rewritten row may carry a different value
    from the one the section was written against — but it means the flag alone does not separate
    a still-intended target from an interloper when both were rewritten after authoring.
    That is what the batch breakdown is for: rows written by a LATER run than the rest are the
    likeliest sign the situation has moved on. It is the line that would have stopped section U.

    Silent when nothing post-dates the section: a flag on every run is a flag nobody reads.
    """
    if "fetched_at" not in cols or not rows:
        return []
    fi = cols.index("fetched_at")
    di = cols.index("date") if "date" in cols else None
    vi = cols.index("value") if "value" in cols else None
    if not authored:
        return ["note: this section carries no authoring stamp, so rows written after it "
                "cannot be told apart from the ones it was written against."]
    stamp = _utc(authored)
    if stamp is None:
        return [f"note: unreadable authoring stamp {authored!r} — post-dating rows cannot be told apart."]
    precise = "T" in authored
    now = now or datetime.now(timezone.utc)
    if (stamp if precise else stamp.replace(hour=0)) > now:
        # A future stamp silently disables the check: nothing is ever "after" it.
        return [f"!! this section's authoring stamp ({authored}) is in the FUTURE, so no row can "
                f"be recognised as written after it. Correct the stamp before trusting this preview."]

    def later(r) -> bool:
        f = _utc(r[fi])
        if f is None:
            return False
        return f > stamp if precise else f.date() >= stamp.date()

    late = [r for r in rows if later(r)]
    if not late:
        return []
    basis = (authored if precise else
             f"{authored} — a DATE-ONLY stamp, so every row written on or after that day counts; "
             f"the time of day it was written is not recorded")
    out = [f"!! {len(late)} of {len(rows)} row(s) were last WRITTEN after this section was "
           f"authored ({basis}). Its WHERE clause was chosen before they took their current "
           f"values — confirm each one is still a target."]
    batches: dict[str, list] = {}
    for r in late:
        batches.setdefault(str(r[fi]), []).append(r)
    order = sorted(batches, key=lambda k: _utc(k) or datetime.min.replace(tzinfo=timezone.utc))
    if len(order) > 1:
        out.append(f"   They come from {len(order)} different writes. Rows from a LATER run than "
                   f"the rest are the likeliest sign the situation this section describes has "
                   f"moved on since it was written:")
    if len(order) > 8:
        out.append(f"     ... {len(order) - 8} earlier write(s) not shown")
    for k in order[-8:]:
        rs = batches[k]
        line = f"     written {k}   {len(rs)} row(s)"
        if di is not None:
            ds = sorted(str(r[di]) for r in rs)
            line += f"   dates {ds[0]}" + (f"..{ds[-1]}" if ds[-1] != ds[0] else "")
        if vi is not None:
            vs = [r[vi] for r in rs if isinstance(r[vi], (int, float))]
            if vs:
                line += f"   value {_num(min(vs))}" + (f" .. {_num(max(vs))}" if max(vs) != min(vs) else "")
        if len(order) > 1 and k == order[-1]:
            line += "   <- NEWEST"
        out.append(line)
    return out


def classify(stmt: str) -> str:
    body = strip_comments(stmt)
    if not body:
        return "empty"
    head = body.split(None, 1)[0].upper()
    return {"SELECT": "select", "WITH": "select", "DELETE": "write", "UPDATE": "write",
            "BEGIN": "txn", "COMMIT": "txn"}.get(head, "other")


def uncommented_write(section_text: str) -> list[str]:
    """The DELETE *or UPDATE* a section ships commented out, with the leading '-- ' stripped.

    UPDATE is included because section A's fix is a RE-ATTRIBUTION, not a removal — the sethfi
    reading was correct and the column was wrong. Handling only DELETE would leave the one
    section whose remedy is a move without a way to run it, which is how it came to still be
    unrun weeks later.

    Only lines that are commented-out SQL are taken. Prose is left behind by requiring the
    uncommented line to start with a SQL keyword or to be a continuation (leading whitespace
    beyond the comment marker), which is how the file already formats them.
    """
    keep, collecting = [], False
    for line in section_text.splitlines():
        s = line.strip()
        if not s.startswith("--"):
            collecting = False
            continue
        body = s[2:]
        if body.startswith(" "):
            body = body[1:]
        head = body.strip().split(None, 1)[0].upper() if body.strip() else ""
        if head in ("DELETE", "UPDATE", "BEGIN;", "BEGIN", "COMMIT;", "COMMIT"):
            collecting = True
            keep.append(body)
        elif collecting and body.startswith((" ", "\t")) and body.strip():
            keep.append(body)
        elif collecting and body.strip().endswith(";"):
            keep.append(body)
            collecting = False
        else:
            collecting = False
    return keep


def render(cursor, rows) -> str:
    if not rows:
        return "    (no rows)"
    cols = [d[0] for d in cursor.description]
    def fmt(v):
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:,.6f}".rstrip("0").rstrip(".")
        return str(v)
    table = [cols] + [[fmt(v) for v in r] for r in rows]
    widths = [max(len(r[i]) for r in table) for i in range(len(cols))]
    out = ["    " + "  ".join(c.ljust(widths[i]) for i, c in enumerate(table[0])),
           "    " + "  ".join("-" * widths[i] for i in range(len(cols)))]
    out += ["    " + "  ".join(c.ljust(widths[i]) for i, c in enumerate(r)) for r in table[1:]]
    return "\n".join(out)


def list_sections(sections: dict[str, dict]) -> int:
    print(f"\n{SQL_FILE.name} — sections\n")
    for letter, sec in sections.items():
        stmts = [s for s in split_statements(sec["text"]) if classify(s) == "select"]
        has_write = bool(uncommented_write(sec["text"]))
        print(f"  {letter:<3} line {sec['line']:>4}   {len(stmts)} SELECT(s)"
              f"   {'write available' if has_write else 'look only'}")
        print(f"      {sec['title'][:96]}")
    print("\n  python run_sql.py <section>            run that section's SELECTs")
    print("  python run_sql.py --delete <section>   run its DELETE, after showing what goes")
    print("  Section labels are one or more letters: A..Z, then AA, AB, ...\n")
    return 0


def run_selects(conn, section_text: str, letter: str, authored: str | None = None) -> int:
    stmts = split_statements(section_text)
    ran, refused = 0, 0
    for stmt in stmts:
        kind = classify(stmt)
        if kind in ("empty", "txn"):
            continue
        body = strip_comments(stmt)
        if kind != "select":
            # A DELETE that has been uncommented in the file is NOT run here. This command is the
            # "just look" one, and it stays that way even when the file has been half-edited.
            print(f"\n  [skipped: not a SELECT] {body.splitlines()[0][:80]}")
            print("  Use --delete to run a DELETE. This mode never modifies the store.")
            refused += 1
            continue
        label = next((l.strip() for l in stmt.splitlines() if l.strip().startswith("--")), "")
        print(f"\n  {label[:110]}" if label else "")
        try:
            cur = conn.execute(body)
            rows = cur.fetchall()
            print(render(cur, rows))
            for line in fetch_provenance([d[0] for d in cur.description], rows, authored):
                print("    " + line)
            ran += 1
        except sqlite3.Error as e:
            print(f"    SQL ERROR: {e}")
            print(f"    statement: {body[:200]}")
            return 1
    print(f"\n  section {letter}: {ran} SELECT(s) run"
          + (f", {refused} non-SELECT statement(s) skipped" if refused else "")
          + " — nothing was modified.\n")
    return 0


def run_delete(conn, section_text: str, letter: str, db: pathlib.Path,
               authored: str | None = None) -> int:
    lines = uncommented_write(section_text)
    if not lines:
        print(f"\n  Section {letter} ships no DELETE.")
        print("  Some sections are deliberately SELECT-only — section H, for one, because which")
        print("  rows to remove depends on what its queries show, and under one reading the")
        print("  answer is none. Read the section before assuming a DELETE is missing.\n")
        return 1

    sql = "\n".join(lines)
    deletes = [strip_comments(s) for s in split_statements(sql) if classify(s) == "write"]
    if not deletes:
        print(f"\n  Section {letter} has a commented block but no DELETE statement in it.\n")
        return 1

    print(f"\n  Section {letter} DELETE, exactly as the file has it:\n")
    for d in deletes:
        print("    " + "\n    ".join(d.splitlines()))

    # SHOW THE ROWS FIRST. A count is not enough — "47 rows" tells you nothing about whether they
    # are the right 47. The DELETE's own WHERE clause is reused verbatim so the preview cannot
    # drift from what will actually go.
    #
    # ** AND THE TABLE COMES FROM THE DELETE TOO. Fixed 2026-09-22. ** This read
    # "SELECT date, project, metric, value, source FROM metrics WHERE {where}" with the table and
    # the columns written out, which held for every section up to O because all of them delete
    # from `metrics`. Section P deletes from gap_report and review_queue, whose WHERE clause
    # names run_id — a column `metrics` does not have — so the preview failed with
    # "no such column: run_id" and the delete was refused.
    #
    # THE REFUSAL WAS THE RIGHT BEHAVIOUR AND IS UNCHANGED: a preview that cannot run means the
    # rows cannot be shown, and nothing is deleted unshown. What was wrong was preview-ing the
    # wrong table. Same class as the J3 bug — a lookup keyed on something that does not match the
    # shape of the data — and the same fix: resolve it from the statement instead of assuming it.
    total = 0
    post_dating = False
    for d in deletes:
        where = d[d.upper().index(" WHERE ") + 7:] if " WHERE " in d.upper() else "1=1"
        m = re.search(r"DELETE\s+FROM\s+([A-Za-z_][A-Za-z0-9_]*)", d, re.I)
        if not m:
            print(f"\n  Could not tell which table this DELETE targets:\n    {d}")
            print("  Refusing to delete something I cannot show you first.\n")
            return 1
        table = m.group(1)
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if not cols:
            print(f"\n  Table {table!r} does not exist in this store.")
            print("  Refusing to delete something I cannot show you first.\n")
            return 1
        # ORDER BY only on columns the table actually has, for the same reason.
        order = [c for c in ("project", "metric", "date", "ts") if c in cols] or [cols[0]]
        try:
            cur = conn.execute(f"SELECT * FROM {table} WHERE {where} "
                               f"ORDER BY {', '.join(order)}")
            rows = cur.fetchall()
        except sqlite3.Error as e:
            print(f"\n  Could not preview the rows: {e}")
            print("  Refusing to delete something I cannot show you first.\n")
            return 1
        print(f"\n  Rows this would remove from {table} ({len(rows)}):")
        print(render(cur, rows[:200]))
        if len(rows) > 200:
            print(f"    ... and {len(rows) - 200} more")
        notes = fetch_provenance([c[0] for c in cur.description], rows, authored)
        for line in notes:
            print("    " + line)
        post_dating = post_dating or any(n.startswith("!!") for n in notes)
        total += len(rows)

    if total == 0:
        print("\n  Nothing matches. Either it has already been run, or the WHERE clause needs")
        print("  filling in — section D's DELETE ships with placeholder source strings that must")
        print("  be replaced with the real ones from D2 first.\n")
        return 1

    if post_dating:
        # Said again at the point of decision, not only above a table that may have scrolled off.
        print(f"\n  !! SOME ROWS ABOVE WERE WRITTEN AFTER SECTION {letter} WAS AUTHORED — read the "
              f"notes under the table before confirming.")
    print(f"\n  BACK UP FIRST. This cannot be undone:")
    print(f"      copy {db.name} {db.name}.bak")
    want = f"DELETE {letter}"
    print(f"\n  Type exactly  {want}  to proceed, or anything else to abort.")
    try:
        typed = input("  > ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Aborted. Nothing was deleted.\n")
        return 1
    if typed != want:
        print("\n  Aborted. Nothing was deleted.\n")
        return 1

    try:
        with conn:
            removed = sum(conn.execute(d).rowcount for d in deletes)
    except sqlite3.Error as e:
        print(f"\n  SQL ERROR, rolled back: {e}\n")
        return 1
    print(f"\n  Deleted {removed} row(s). Re-run `python run_sql.py {letter}` to verify.\n")
    return 0


def main(argv=None) -> int:
    _decode_safe()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("section", nargs="?",
                    help="section label — one or more letters, e.g. E or AB")
    ap.add_argument("--delete", metavar="SECTION",
                    help="run that section's DELETE, after showing the rows and confirming")
    ap.add_argument("--db", default=str(DEFAULT_DB), help=f"store to read (default {DEFAULT_DB.name})")
    args = ap.parse_args(argv)

    if not SQL_FILE.exists():
        print(f"  {SQL_FILE} not found.")
        return 1
    sections = parse_sections(SQL_FILE.read_text(encoding="utf-8"))
    if not sections:
        print(f"  No sections found in {SQL_FILE.name}.")
        return 1

    letter = (args.delete or args.section or "").strip().upper()
    if not letter:
        return list_sections(sections)
    if not SECTION_ARG.match(letter):
        print(f"\n  {letter!r} is not a section label. Labels are one or more letters "
              f"(A..Z, then AA, AB, ...).")
        print(f"  Available: {', '.join(sections)}\n")
        return 1
    if letter not in sections:
        print(f"\n  No section {letter!r}. Available: {', '.join(sections)}\n")
        return 1

    db = pathlib.Path(args.db)
    if not db.exists():
        print(f"\n  {db} not found. Run the tool once to create the store, or pass --db.\n")
        return 1

    conn = sqlite3.connect(str(db))
    try:
        sec = sections[letter]
        print(f"\n  {SQL_FILE.name} section {letter} (line {sec['line']}) against {db.name}")
        print(f"  {sec['title'][:100]}")
        if args.delete:
            return run_delete(conn, sec["text"], letter, db, sec.get("authored"))
        return run_selects(conn, sec["text"], letter, sec.get("authored"))
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
