"""
fetch/growthepie.py — chain activity (daily active addresses, transaction count).

growthepie publishes daily fundamentals for Ethereum Mainnet and 27+ Ethereum-aligned chains as
one free unauthenticated JSON document. Tier 1, the same class as DefiLlama and CoinGecko.

    GET https://api.growthepie.com/v1/fundamentals.json

ROBOTS: www.growthepie.com/robots.txt disallows only page paths (/embed/, /refactor,
/gtpplayground, /debug/, /_next/*). There is no restriction on api.growthepie.com and no separate
robots.txt for the API subdomain. Checked 2026-09-23. Respecting robots.txt is a design
requirement here, not a formality, so the check and its date are recorded rather than assumed.

** THE FIELD NAMES ARE NOT KNOWN, AND THIS ADAPTER DOES NOT GUESS THEM. **
origin_key and metric_key are confirmed — 'ethereum' and 'plume' are both present, as are 'daa'
and 'txcount'. What is NOT confirmed is what a row calls its DATE and its VALUE: that was
inferred from the query shape, never read from a sample row. A parser that assumes `date` and
`value` would either work silently or fail in a way that looks like a missing series, and this
project has been caught by a plausible-looking wrong number often enough that "probably called
value" is not a basis for storing one.

So the adapter runs, REPORTS THE KEYS IT ACTUALLY FOUND, and stores nothing until
config's growthepie.status is 'confirmed'. One live run settles it, the same contract as
MorphoBlueApi. The names then live in config, not in this file.
"""
from __future__ import annotations

import logging

import pandas as pd

import config

from .base import Http, tidy, window

log = logging.getLogger("token_metrics.fetch.growthepie")

SOURCE = "growthepie"
TIER = 1
# Keys a row might plausibly use for its date and its value, most likely first. Used ONLY to
# report what a sample row looks like — never to pick one and store a figure from it.
DATE_CANDIDATES = ("date", "day", "timestamp", "ts", "unix", "datetime")
VALUE_CANDIDATES = ("value", "val", "metric_value", "amount", "count")


class GrowThePie:
    """Daily chain activity for any project declaring a `growthepie` block."""

    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, **_ignored):
        self.http = Http(min_interval=0.5)
        self._doc = None
        self._failed = None

    def _fundamentals(self, url):
        """Fetched ONCE per run and shared. One document carries every chain and metric, so a
        call per project would fetch the same megabytes five times and be rude about it."""
        if self._doc is None and self._failed is None:
            try:
                self._doc = self.http.get(url)
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                self._failed = str(e)
        return self._doc

    @staticmethod
    def _shape(rows: list) -> dict:
        """What a sample row actually looks like — the whole point of the unconfirmed state."""
        if not rows:
            return {}
        r = rows[0]
        if not isinstance(r, dict):
            return {"row_is_not_a_dict": type(r).__name__, "sample": str(r)[:120]}
        return {
            "keys": sorted(r),
            "date_like": [k for k in r if k.lower() in DATE_CANDIDATES],
            "value_like": [k for k in r if k.lower() in VALUE_CANDIDATES],
            "sample": {k: str(v)[:40] for k, v in list(r.items())[:8]},
        }

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = p.get("growthepie") or {}
            origin = spec.get("origin_key")
            if not origin:
                continue
            name = p["name"]
            doc = self._fundamentals(spec["endpoint"])
            if doc is None:
                out.fail(SOURCE, name, f"{spec['endpoint']}: {self._failed}", TIER)
                continue
            if not isinstance(doc, list):
                out.fail(SOURCE, name,
                         f"{spec['endpoint']}: expected a list of rows, got "
                         f"{type(doc).__name__}. Top-level keys: "
                         f"{', '.join(sorted(doc)[:10]) if isinstance(doc, dict) else 'n/a'}.",
                         TIER)
                continue

            for metric, metric_key in (spec.get("metrics") or {}).items():
                rows = [r for r in doc
                        if isinstance(r, dict)
                        and r.get("origin_key") == origin
                        and r.get("metric_key") == metric_key]
                if not rows:
                    # ** WHICH HALF OF THE FILTER MISSED. ** "no rows" for a chain that is not
                    # covered and for a metric_key that is spelled wrong are different problems
                    # with different fixes, and the document itself can say which.
                    origins = sorted({r.get("origin_key") for r in doc if isinstance(r, dict)})
                    keys = sorted({r.get("metric_key") for r in doc
                                   if isinstance(r, dict) and r.get("origin_key") == origin})
                    out.gap(name, metric,
                            reason=(f"growthepie has no rows for origin_key={origin!r} + "
                                    f"metric_key={metric_key!r}. "
                                    + (f"origin_key {origin!r} IS present and carries: "
                                       f"{', '.join(str(k) for k in keys[:14])}."
                                       if origin in origins else
                                       f"origin_key {origin!r} is NOT in the document at all; "
                                       f"it carries {len(origins)} chains.")),
                            tiers_attempted="1",
                            suggestion=("Check the key against the live document before "
                                        "changing anything — both halves of the filter are "
                                        "reported above so the wrong one is not edited."))
                    continue

                date_key, value_key = spec.get("date_field"), spec.get("value_field")
                if spec.get("status") != "confirmed" or not date_key or not value_key:
                    # ===== ** THE FIELD NAMES WERE INFERRED FROM THE QUERY, NOT READ FROM A
                    # ** ROW — SO NOTHING IS STORED UNTIL A RUN HAS SEEN ONE. ** The filter keys
                    # are confirmed; the date and value keys are not. Guessing them would either
                    # work silently or look exactly like a missing series, and neither outcome
                    # tells anyone which happened.
                    shape = self._shape(rows)
                    out.skipped(SOURCE, name,
                                f"{metric}: NOT stored — growthepie.status is "
                                f"{spec.get('status')!r} and the row's date/value field names "
                                f"have not been read from a live row. THE FILTER WORKS: "
                                f"{len(rows)} row(s) for origin_key={origin!r} "
                                f"metric_key={metric_key!r}. A SAMPLE ROW HAS KEYS "
                                f"{shape.get('keys')}; date-like {shape.get('date_like')}, "
                                f"value-like {shape.get('value_like')}; sample "
                                f"{shape.get('sample')}. Put the two real names in config as "
                                f"date_field/value_field and set status to 'confirmed'.",
                                TIER)
                    continue

                pairs = []
                missing = 0
                for r in rows:
                    d, v = r.get(date_key), r.get(value_key)
                    if d is None or v is None:
                        missing += 1
                        continue
                    try:
                        pairs.append((pd.Timestamp(d), float(v)))
                    except (TypeError, ValueError):
                        missing += 1
                if missing:
                    # A ROW THAT DOES NOT PARSE IS REPORTED, NEVER DROPPED SILENTLY — a partial
                    # series under a full-coverage header is the error this project keeps
                    # finding, and it is invisible unless the count is stated.
                    out.skipped(SOURCE, name,
                                f"{metric}: {missing} of {len(rows)} growthepie row(s) had no "
                                f"usable {date_key!r}/{value_key!r} and were not converted. The "
                                f"remaining {len(pairs)} are stored.", TIER)
                if not pairs:
                    out.fail(SOURCE, name,
                             f"{metric}: {len(rows)} row(s) matched the filter and none carried "
                             f"a usable {date_key!r}/{value_key!r}. The field names in config "
                             f"are wrong for this document.", TIER)
                    continue
                out.add(window(tidy(sorted(pairs), name, metric, SOURCE, TIER), window_days),
                        SOURCE, name,
                        f"{metric} = growthepie {metric_key} for {origin} "
                        f"({len(pairs)} day(s))", TIER)
