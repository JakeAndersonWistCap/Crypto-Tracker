"""reader_layout.py - THE READER'S LAYOUT: the Highlights first tab and the look (P2g-3/4; one workbook, P4-Q8).

Every reader build (token_metrics.xlsx) gets, just before it is saved, the Highlights tab added first
(highlights.add_highlights) and the look applied (reader_look.apply_look): one reader file, no second build, no
edition copy. The audit file is written before the reader and is not touched here.

THE CARD FLOOR (P4-Q8): when fewer than MIN_CARDS cards render, the reader is still written; its Highlights tab holds no
cards, only the statement that the floor was not met, how many rendered and each card's unmet condition
(highlights.floor_lines); the run's report says so.

Imported, not an entry point (token_metrics.py is). The hook: reader.build_reader (calls apply_layout just before the
workbook is saved); token_metrics.py prints report_lines at the end of the run.
"""
from __future__ import annotations

MIN_CARDS = 5                       # the production floor; tests on the fixture build lower it (it holds other numbers)
LAST: dict = {}                     # the last reader build: {"cards", "rendered", "omitted", "floor_met", "lines", "look"}


def apply_layout(wb, ctx) -> dict:
    """Add the Highlights tab to the reader workbook `wb` (just before it is saved), then apply the look."""
    import highlights                                      # noqa: PLC0415
    import reader_look                                     # noqa: PLC0415
    res = highlights.add_highlights(wb, ctx, min_cards=MIN_CARDS)
    reader_look.apply_look(wb)
    look = "reader_look.apply_look applied"
    LAST.clear()
    LAST.update({"cards": res["cards"], "rendered": res["rendered"], "omitted": res["omitted"],
                 "floor_met": res["floor_met"], "lines": res["lines"], "look": look})
    return {"cards": res["cards"], "rendered": res["rendered"], "omitted": res["omitted"],
            "floor_met": res["floor_met"], "look": look}


def report_lines(path=None) -> list:
    """The lines to print at the end of the run: the file, the look, the rendered cards with their displayed values -
    or, when the floor was not met, the floor statement."""
    if not LAST:
        return []
    head = [f"READER LAYOUT: {path}" if path else "READER LAYOUT", f"  {LAST['look']}"]
    return head + list(LAST["lines"])


def print_report(path=None) -> None:
    for line in report_lines(path):
        print(line)
