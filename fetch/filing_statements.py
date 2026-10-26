"""
fetch/filing_statements.py — a project's own published statement, stored as a DATED VALUE only while it still stands
(Round D2-4e, Jake 2026-10-26: "store it as a dated zero with the filing date. Re-check it, don't carry it forward
forever, and say when it lapses").

GEODNET's Blockworks Token Transparency filing (issuer-filed, B2 v1.0, filed 2025-08-19), Q11 "Locked Staking Reward to
Insiders": "There is no current GEOD or other GEOD related rewards being earned by any locked wallet team or investor."
The page is read on every run; the value (0) is stored, dated the read day, ONLY when all of these hold on the page:
  - the filing it serves is the one declared: filedDate == `filed`, filingStatus "current", isLatestFiling true;
  - the declared question's answer still contains the declared quote.
Anything else stores nothing and says what changed. A value not re-confirmed turns stale after
config.INSIDER_DATED_ZERO_LAPSE_DAYS (config.stale_after_days) and the cell reads MISSING E naming the lapse — the
statement is never carried past its last confirmation.
"""
from __future__ import annotations

import json
import logging
import re

import pandas as pd

from .base import Http, tidy, today

log = logging.getLogger("token_metrics.fetch.filing_statements")

SOURCE = "filing_statement"
TIER = 1


def check_filing(page: str, spec: dict) -> str:
    """'' when the page still serves the declared filing with the declared answer, else what differs."""
    filed = re.search(r'"filedDate"\s*:\s*"([0-9-]+)"', page)
    if not filed:
        return "no filedDate on the page (its shape changed)"
    if filed.group(1) != spec["filed"]:
        return f"the page serves a filing dated {filed.group(1)}, not {spec['filed']} — superseded"
    if not re.search(r'"filingStatus"\s*:\s*"current"', page) or not re.search(r'"isLatestFiling"\s*:\s*true', page):
        return f"the {spec['filed']} filing is no longer marked current and latest"
    m = re.search(r'"answerMarkdown"\s*:\s*("(?:[^"\\]|\\.)*")\s*,\s*"id"\s*:\s*"' + re.escape(spec["question"]) + '"',
                  page)
    if not m:
        return f"no answer to {spec['question']} on the page"
    answer = " ".join(json.loads(m.group(1)).split())
    if " ".join(spec["quote"].split()) not in answer:
        return f"{spec['question']}'s answer no longer says it (now: {answer[:160]!r})"
    return ""


class FilingStatements:
    """Every project declaring `filing_statements`, read once per run."""

    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, **_ignored):
        self.http = http or Http(min_interval=1.0, retries=2)

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            for spec in p.get("filing_statements") or ():
                self._one(p["name"], spec, out)

    def _one(self, name: str, spec: dict, out) -> None:
        from .scrape import robots_verdict

        m = spec["metric"]
        allowed, robots = robots_verdict(spec["url"])
        if not allowed:
            out.fail(SOURCE, name, f"{m}: robots.txt disallows {spec['url']} — {robots}; NOTHING STORED", TIER)
            return
        try:
            page = self.http.get_text(spec["url"])
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            out.fail(SOURCE, name, f"{m}: {spec['url']}: {e} — NOTHING STORED (the dated value is not re-confirmed "
                                   f"today)", TIER)
            return
        why = check_filing(str(page or ""), spec)
        if why:
            out.fail(SOURCE, name, f"{m}: {why} — NOTHING STORED: the statement is not re-confirmed and lapses "
                                   f"once its last confirmation is stale", TIER)
            return
        day = pd.Timestamp(today().date())
        out.add(tidy([(day, float(spec["value"]))], name, m, SOURCE, TIER), SOURCE, name,
                f"{m}: {spec['value']:g} — {spec['url']} (filed {spec['filed']}, {spec['question'].upper()}) still current "
                f"and unchanged, confirmed {day.date()}", TIER)
