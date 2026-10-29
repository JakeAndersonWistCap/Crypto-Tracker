"""charlie_edition.py - THE CHARLIE EDITION OF THE READER (P2g-2, Jake 2026-10-29).

`python token_metrics.py --no-fetch --edition charlie` writes, beside the normal files, token_metrics_charlie.xlsx: the
reader built a SECOND time into a fresh workbook (so its charts and images are made, not round-tripped through a
load and a save), with the Highlights tab added first and, when charlie_look.py is present, its look applied. The
normal reader and the audit file are written exactly as without the flag; nothing here touches them.

Imported, not an entry point (token_metrics.py is). The hooks: token_metrics.py (--edition), build_workbook.write_edition
(the second build), reader.build_reader(edition=...) (calls apply_edition just before the workbook is saved).
"""
from __future__ import annotations

from pathlib import Path

EDITIONS = ("charlie",)
CHARLIE_NAME = "token_metrics_charlie.xlsx"
MIN_CARDS = 5                       # the production floor; tests on the fixture build lower it (it holds other numbers)
LAST: dict = {}                     # the last edition build: {"cards", "omitted", "lines", "look"}


def edition_path(reader_path) -> Path:
    """token_metrics_charlie.xlsx beside the reader file."""
    return Path(reader_path).with_name(CHARLIE_NAME)


def apply_edition(wb, ctx, edition: str) -> dict:
    """Add the edition to the reader workbook `wb` (just before it is saved): the Highlights tab, then the look."""
    if edition not in EDITIONS:
        raise ValueError(f"unknown edition {edition!r} (choices: {', '.join(EDITIONS)})")
    import charlie_highlights                              # noqa: PLC0415
    res = charlie_highlights.add_highlights(wb, ctx, min_cards=MIN_CARDS)
    try:
        import charlie_look                                # noqa: PLC0415
    except ImportError:
        look = "charlie_look.py is not present: the edition is written without its look"
    else:
        charlie_look.apply_look(wb)
        look = "charlie_look.apply_look applied"
    LAST.clear()
    LAST.update({"cards": res["cards"], "omitted": res["omitted"], "lines": res["lines"], "look": look})
    return {"edition": edition, "cards": res["cards"], "omitted": res["omitted"], "look": look}


def report_lines(path=None) -> list:
    """The lines to print at the end of the run: the file, the look, the rendered cards with their displayed values."""
    if not LAST:
        return []
    head = [f"CHARLIE EDITION: {path}" if path else "CHARLIE EDITION", f"  {LAST['look']}"]
    return head + list(LAST["lines"])


def print_report(path=None) -> None:
    for line in report_lines(path):
        print(line)
