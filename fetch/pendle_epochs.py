"""
fetch/pendle_epochs.py — tier 3: Pendle's per-epoch sPENDLE distributions and its own APR, from
the documented /core/v1/spendle/data endpoint. Added 2026-09-29 (Jake).

WHY. Pendle's token yield was PENDLE BOUGHT (actual_buyback_tokens = holders revenue / same-day
price) over reward-bearing sPENDLE. Pendle publishes what was DISTRIBUTED, per epoch:

    sPendleHistoricalData: timestamps, revenues, aprs, fees, airdrops, buybackAmounts,
                           airdropInUSDs, airdropBreakdowns, allTimeRevenues — the last 12 epochs
    (pendle-finance/documentation @ 9b9509e, docs/pendle-dev-docs/Backend/ApiOverview.mdx
     L283-300, read 2026-09-29; the docs give NO types or units)

Stored:
    pendle_distributed_tokens (flow, one row per epoch, dated the epoch's timestamp)
                              = buybackAmounts[i] / 10^decimals
    staking_apr_published     (stock, fraction) = lastEpochApr — Pendle's own APR, a CROSS-CHECK
                              beside our token yield. Not in the official docs; a third-party
                              client treats it as a decimal fraction (iYieldCrypto/iyield-api
                              @3c1e3b1). A value over 1 (a percent) is rejected by its bound,
                              never stored 100x.

UNITS ARE CHECKED, NOT ASSUMED. The docs do not state them. Jake read Pendle's staking page on
2026-09-29: past epochs ~170K-350K PENDLE. The MEDIAN epoch, scaled by the declared decimals,
must fall inside the configured band, or nothing is stored and the log prints the raw values —
a wrong scale is off by 10^18, never by a factor a band could miss. Airdrops (in kind) are a
separate array and are not in this figure.
"""
from __future__ import annotations

import logging
import statistics

import pandas as pd

from .base import USER_AGENT, LogEntry, point, today

log = logging.getLogger("token_metrics.fetch.pendle_epochs")

SOURCE = "pendle_api"
TIER = 3


def _get(url: str):
    import requests
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=(10, 30))
    r.raise_for_status()
    return r.json()


def epochs(payload: dict, spec: dict) -> tuple[list[tuple[pd.Timestamp, float]], list]:
    """[(date, tokens)] from sPendleHistoricalData, and the raw amounts (for the log)."""
    hist = payload.get(spec["history_key"]) or {}
    ts, raw = hist.get(spec["time_field"]) or [], hist.get(spec["amount_field"]) or []
    if not ts or len(ts) != len(raw):
        raise ValueError(f"{spec['history_key']}: {len(ts)} {spec['time_field']} against {len(raw)} "
                         f"{spec['amount_field']}; keys {sorted(hist)[:12]}")
    scale = 10 ** int(spec["decimals"])
    out = []
    for t, a in zip(ts, raw):
        t = int(float(t))
        when = pd.Timestamp(t, unit="s" if t < 10**11 else "ms").normalize()
        out.append((when, float(a) / scale))
    return out, list(raw)


class PendleEpochs:
    """One GET per project declaring `spendle_epochs` (Pendle only)."""

    def __init__(self, get=None):
        self.get = get or _get

    def run(self, projects: list[dict], window_days, out):
        from .scrape import robots_verdict
        now = today()
        for p in projects:
            spec = p.get("spendle_epochs")
            if not spec:
                continue
            name, metric = p["name"], spec["metric"]
            ok, why = robots_verdict(spec["url"])
            if not ok:
                out.skipped(SOURCE, name, f"{metric}: robots.txt — {why}", TIER)
                continue
            try:
                payload = self.get(spec["url"])
                rows, raw = epochs(payload, spec)
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                out.fail(SOURCE, name, f"{metric}: {spec['url']}: {e}", TIER)
                out.gap(name, metric, reason=f"Pendle's spendle/data did not give the epoch series: {e}",
                        tiers_attempted="3", suggestion="Run check_offline_items.py pendle_spendle_fees.")
                continue
            done = [(d, v) for d, v in rows if d <= now]
            lo, hi = spec["units_check"]["median_between"]
            med = statistics.median(v for _, v in done) if done else None
            if med is None or not lo <= med <= hi:
                out.fail(SOURCE, name, f"{metric}: UNITS NOT CONFIRMED — median epoch "
                                       f"{med if med is None else f'{med:,.4f}'} after /10^{spec['decimals']} "
                                       f"is outside {lo:,}..{hi:,} ({spec['units_check']['source']}); raw "
                                       f"{raw[:4]}. NOTHING STORED.", TIER)
                out.gap(name, metric, reason=f"the epoch amounts do not scale to the staking page's "
                                             f"epoch sizes with decimals {spec['decimals']}: median "
                                             f"{med}; raw {raw[:4]}",
                        tiers_attempted="3", suggestion="Read the raw values in the Run Log and set "
                                                        "spendle_epochs.decimals to what they show.")
            else:
                frame = pd.DataFrame([point(name, metric, v, f"{SOURCE}:{spec['history_key']}."
                                            f"{spec['amount_field']}", TIER, d).iloc[0] for d, v in done])
                out.add(frame, SOURCE, name,
                        f"{metric}: {len(done)} epoch(s) {done[0][0].date()}..{done[-1][0].date()}, "
                        f"median {med:,.0f} PENDLE — UNITS CONFIRMED against the staking page's "
                        f"{lo:,}..{hi:,} (raw {raw[0]} / 10^{spec['decimals']})", TIER)
            apr_key = spec.get("apr_field")
            apr = payload.get(apr_key) if apr_key else None
            if apr is not None:
                try:
                    v = float(apr)
                except (TypeError, ValueError):
                    out.fail(SOURCE, name, f"{spec['apr_metric']}: {apr_key}={apr!r} is not a number", TIER)
                    continue
                out.add(point(name, spec["apr_metric"], v, f"{SOURCE}:{apr_key}", TIER, now), SOURCE, name,
                        f"{spec['apr_metric']}={v} ({apr_key}, Pendle's own APR; a cross-check)", TIER)
            elif apr_key:
                out.log.append(LogEntry(SOURCE, name, 0, "ok", f"{apr_key} absent; payload keys "
                                                             f"{sorted(payload)[:12]}", TIER))
