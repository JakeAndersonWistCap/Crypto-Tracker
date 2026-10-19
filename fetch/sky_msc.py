"""
fetch/sky_msc.py — tier 3: SKY NET REVENUE AS THE MONTHLY SETTLEMENT REPORTS IT (Round G5 decision D1, Jake 2026-10-19).

D1: "Soter Labs' MSC settlement report is the monthly measuring point; Block Analitica stays as the daily series. Store
the Soter monthly figure as its own series, reference-check monthly totals against it, and label the BA-vs-Soter
difference as a known accounting-basis difference with the numbers, not BROKEN."

THE SOURCE: Soter Labs' consolidated monthly report, one Markdown file a month in their public repository — the file the
Treasury Management Function posts on the Sky forum quote (TMF thread 28153: "According to MSC #13, Sky's Net Revenue was
14,635,947 USDS for the month of September"):

    https://raw.githubusercontent.com/soterlabs/settlement-cycle/main/settlements/sky_total/<YYYY-MM>/summary.md
    -> "## Sky Net Revenue" table: | MSC net (<basis>) | … |, | non-MSC net | … |, | **Sky Net Revenue** | **<USDS>** |

Read 2026-10-19: 2026-01..06 on the BUFFER basis, 2026-07.. on the ACCRUAL basis (operator definition 2026-08-07). Each
month is stored as net_revenue_msc_usd dated the month's last day, its basis in the source string, so a reader never
sums or compares across the switch unawares. Once a day; a month with no file (not yet published) is not a gap.
"""
from __future__ import annotations

import logging
import re

import pandas as pd

from .base import LogEntry, tidy, today

log = logging.getLogger("token_metrics.fetch.sky_msc")

SOURCE = "sky_msc"
TIER = 3
URL = "https://raw.githubusercontent.com/soterlabs/settlement-cycle/main/settlements/sky_total/{month}/summary.md"
METRIC = "net_revenue_msc_usd"
FIRST_MONTH = "2026-01"        # the first month the repository publishes (read 2026-10-19)
_SNR = re.compile(r"\|\s*\*\*Sky Net Revenue\*\*\s*\|\s*\*\*(-?[\d,]+(?:\.\d+)?)\*\*\s*\|")
_BASIS = re.compile(r"\|\s*\*{0,2}MSC net \(([^)]+)\)\*{0,2}\s*\|")


def parse_summary(text: str) -> tuple[float, str] | str:
    """(Sky Net Revenue in USDS, the MSC leg's basis) from one month's summary.md, or why it is refused."""
    sec = (text or "").split("## Sky Net Revenue", 1)
    if len(sec) < 2:
        return "no '## Sky Net Revenue' section"
    m = _SNR.search(sec[1])
    if not m:
        return "no '**Sky Net Revenue**' row in the section"
    b = _BASIS.search(sec[1]) or _BASIS.search(text)
    return float(m.group(1).replace(",", "")), (b.group(1).strip() if b else "basis not stated")


class SkyMsc:
    def __init__(self, get_text=None, daily=None, robots=None):
        self.get_text = get_text
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily
        if robots is None:
            from .scrape import robots_verdict
            robots = robots_verdict
        self.robots = robots

    def _get(self, url: str) -> str | None:
        if self.get_text is not None:
            return self.get_text(url)
        import requests
        from .base import USER_AGENT
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.text

    def run(self, projects: list[dict], window_days, out):
        if not any(p["name"] == "Sky" for p in projects):
            return
        day = str(today().date())
        key = f"{SOURCE}:Sky:{METRIC}"
        if not self.daily.due(key, day):
            out.mark_current(SOURCE, "Sky", METRIC, f"{METRIC}: read today already (once a day)", TIER)
            return
        last = (today().normalize().replace(day=1) - pd.Timedelta(days=1))
        months = [str(m)[:7] for m in pd.period_range(FIRST_MONTH, last.strftime("%Y-%m"), freq="M")]
        rows, bases, notes, refused = [], {}, [], []
        for month in months:
            url = URL.format(month=month)
            ok, why = self.robots(url)
            if not ok:
                out.fail(SOURCE, "Sky", f"{METRIC}: robots.txt refuses {url} ({why})", TIER)
                return
            try:
                text = self._get(url)
            except Exception as e:  # noqa: BLE001 — one month failing is said; the others stand
                refused.append(f"{month}: {str(e)[:120]}")
                continue
            if text is None:
                notes.append(f"{month} not published")
                continue
            got = parse_summary(text)
            if isinstance(got, str):
                refused.append(f"{month}: {got}")
                continue
            v, basis = got
            end = pd.Period(month, freq="M").to_timestamp(how="end").normalize()
            rows.append((end, v, basis))
            bases[month] = basis
        for basis in sorted({b for _d, _v, b in rows}):
            part = [(d, v) for d, v, b in rows if b == basis]
            out.add(tidy(part, "Sky", METRIC, f"{SOURCE}:soterlabs/settlement-cycle sky_total[{basis}]", TIER),
                    SOURCE, "Sky",
                    f"{METRIC}: {len(part)} month(s), {basis}: "
                    + ", ".join(f"{d:%Y-%m} {v:,.2f}" for d, v in part), TIER)
        if refused:
            out.fail(SOURCE, "Sky", f"{METRIC}: {len(refused)} month(s) not read — {'; '.join(refused[:3])}", TIER)
        else:
            self.daily.done(key, day)
        if notes:
            out.log.append(LogEntry(SOURCE, "Sky", 0, "ok", f"{METRIC}: {', '.join(notes)}", TIER))
