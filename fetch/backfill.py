"""
fetch/backfill.py — which (project, metric) series to re-read over a full year. Added 2026-09-28.

WHY. Only the store's very first run asked each source for full history; every later run asks
for the trailing 30 days. A series configured after day one therefore started short and only
ever grew forward, so its Q0 covered a fraction of the 90-day window:
    GEODNET fees_usd / customer_revenue_usd  ~38 days stored
    Aethir  fees_usd / customer_revenue_usd  ~52 days stored
    Ethereum gross_burn_tokens (derived)     ~a few weeks, though the inputs go back a year
plan() names every in-scope pair whose oldest stored row is younger than BACKFILL_DAYS (or that
has no row at all), plus the INPUTS of any short derived series, since a derivation can only
reach as far back as what it is computed from in the run. fetch.base.window() then keeps
BACKFILL_DAYS for those pairs and CoinGecko asks for 365 days.

A SOURCE THAT GENUINELY STARTS LATER is remembered, not re-asked every run: after the run,
record() notes each backfilled pair's oldest stored date in history-depth.json (beside the log
cache, disposable). If the next plan finds the same oldest date — the source had nothing older —
the pair is skipped for RECHECK_DAYS.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

import config
from .base import BACKFILL_DAYS, today

RECHECK_DAYS = 30
PRICE_SERIES = ("price_usd", "market_cap_usd", "volume_usd")
SLACK_DAYS = 5          # a first date within this of the full year counts as full


def _depth_file() -> Path:
    return Path(os.environ.get("TOKEN_METRICS_LOGCACHE", ".cache/logscan")) / "history-depth.json"


def _read() -> dict:
    try:
        return json.loads(_depth_file().read_text())
    except (OSError, ValueError):
        return {}


def derived_inputs(project: dict, metric: str) -> set[str]:
    """The stored series a derived `metric` is computed from IN THE RUN, for this project."""
    name, out = project["name"], set()
    spec = config.metric_restatements(name).get(metric)
    if spec:
        out.add(spec["equals"])
    if metric == "gross_burn_tokens" and config.chain_burn_from_revenue(name):
        out |= {"revenue_usd", "price_usd"}
    if metric == "actual_buyback_usd":
        out |= {"actual_buyback_tokens", "price_usd"}
    if metric == "actual_buyback_tokens" and config.buyback_tokens_from_usd(name):
        out |= {"actual_buyback_usd", "price_usd"}
    return out


def plan(projects: list[dict], first_dates: dict) -> tuple[set, list[str]]:
    """(pairs to backfill, one log line per pair saying why)."""
    cutoff = today() - pd.Timedelta(days=BACKFILL_DAYS - SLACK_DAYS)
    memo, now = _read(), today()

    def limited(name: str, metric: str) -> bool:
        """A backfill found nothing older than what is stored, within RECHECK_DAYS."""
        first, seen = first_dates.get((name, metric)), memo.get(f"{name}|{metric}") or {}
        return bool(first is not None and seen.get("first") == first and seen.get("checked_on")
                    and (now - pd.Timestamp(seen["checked_on"])).days < RECHECK_DAYS)

    pairs, why = set(), []
    for p in projects:
        name = p["name"]
        for metric in config.metrics_for_project(p):
            # FLOWS AND PRICES ONLY: Q0 sums a flow and averages a price. A stock is read "now";
            # most stock sources (CoinGecko's circulating, a contract balance) have no history.
            # ...AND A SUPPLY HISTORY AN ISSUANCE HISTORY IS BUILT FROM (Ethereum, 2026-09-29).
            hist_input = (metric == (p.get("issuance_history") or {}).get("supply_metric"))
            if ((config.METRICS.get(metric) or {}).get("kind") != "flow" and metric not in PRICE_SERIES
                    and not hist_input):
                continue
            first = first_dates.get((name, metric))
            if first is not None and pd.Timestamp(first) <= cutoff:
                continue
            if limited(name, metric):
                continue          # the source had nothing older last time; re-asked monthly
            # INPUTS ARE RE-READ EVEN WHEN THEIR OWN STORED HISTORY IS LONG: a derivation runs on
            # the in-run frame, which is otherwise trimmed to the routine window.
            want = {metric} | {m for m in derived_inputs(p, metric) if not limited(name, m)}
            for m in want:
                if (name, m) not in pairs:
                    pairs.add((name, m))
            if first is not None:     # an empty series is widened too, but not listed
                why.append(f"{name}/{metric}: oldest stored {first}"
                           + (f" (+ inputs {sorted(want - {metric})})" if want - {metric} else ""))
    return pairs, why


def record(pairs: set, first_dates_after: dict, answered: set | None = None) -> None:
    """After the run: remember how far back each backfilled series now reaches.

    ** ONLY A PAIR THE SOURCE ACTUALLY ANSWERED THIS RUN. Fixed 2026-09-29. ** A run in which the
    source failed, was rate-limited or was skipped stored nothing older — and was recorded as
    "the source has nothing older", which stopped the year being re-asked for RECHECK_DAYS. Sky's
    price_usd sat at 29 days (2026-08-31..09-28) though CoinGecko serves SKY's whole history, and
    actual_buyback_usd valued 29 of 729 buyback rows. `answered`: the (project, metric) pairs
    with rows in this run's output; None keeps the old behaviour for callers that cannot say.
    """
    memo = _read()
    for name, metric in pairs:
        if answered is not None and (name, metric) not in answered:
            continue
        first = first_dates_after.get((name, metric))
        if first:
            memo[f"{name}|{metric}"] = {"first": first, "checked_on": str(today().date())}
    f = _depth_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(memo, sort_keys=True, indent=1))
    tmp.replace(f)
