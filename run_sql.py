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

HERE = pathlib.Path(__file__).resolve().parent
SQL_FILE = HERE / "orphan_cleanup.sql"
DEFAULT_DB = HERE / "metrics.db"

# A section starts at its first marker and runs to the next letter's first marker. Sections are
# headed inconsistently in the file — some open with "-- A. Title", some go straight to "-- D1." —
# so both spellings are matched rather than relying on one convention the file does not keep.
SECTION_MARKER = re.compile(r"^--\s*([A-Z])(\d*)\.\s?(.*)$")


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
        sections[letter] = {
            "title": title or "(no title)",
            "line": n + 1,
            "text": sql[offsets[n]:offsets[end_line]],
        }
    return sections


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
        print(f"  {letter}   line {sec['line']:>4}   {len(stmts)} SELECT(s)"
              f"   {'write available' if has_write else 'look only'}")
        print(f"      {sec['title'][:96]}")
    print("\n  python run_sql.py <letter>            run that section's SELECTs")
    print("  python run_sql.py --delete <letter>   run its DELETE, after showing what goes\n")
    return 0


def run_selects(conn, section_text: str, letter: str) -> int:
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
            print(render(cur, cur.fetchall()))
            ran += 1
        except sqlite3.Error as e:
            print(f"    SQL ERROR: {e}")
            print(f"    statement: {body[:200]}")
            return 1
    print(f"\n  section {letter}: {ran} SELECT(s) run"
          + (f", {refused} non-SELECT statement(s) skipped" if refused else "")
          + " — nothing was modified.\n")
    return 0


def run_delete(conn, section_text: str, letter: str, db: pathlib.Path) -> int:
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
    total = 0
    for d in deletes:
        where = d[d.upper().index(" WHERE ") + 7:] if " WHERE " in d.upper() else "1=1"
        try:
            cur = conn.execute(f"SELECT date, project, metric, value, source FROM metrics WHERE {where}"
                               f" ORDER BY project, metric, date")
            rows = cur.fetchall()
        except sqlite3.Error as e:
            print(f"\n  Could not preview the rows: {e}")
            print("  Refusing to delete something I cannot show you first.\n")
            return 1
        print(f"\n  Rows this would remove ({len(rows)}):")
        print(render(cur, rows[:200]))
        if len(rows) > 200:
            print(f"    ... and {len(rows) - 200} more")
        total += len(rows)

    if total == 0:
        print("\n  Nothing matches. Either it has already been run, or the WHERE clause needs")
        print("  filling in — section D's DELETE ships with placeholder source strings that must")
        print("  be replaced with the real ones from D2 first.\n")
        return 1

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
    ap.add_argument("section", nargs="?", help="section letter, e.g. E")
    ap.add_argument("--delete", metavar="LETTER",
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
            return run_delete(conn, sec["text"], letter, db)
        return run_selects(conn, sec["text"], letter)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
