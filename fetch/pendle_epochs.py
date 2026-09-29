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

THE PAYLOAD DECIDES THE SCALE (Jake's run 2026-09-29 14:38: "median epoch 0.0000 after /10^18" —
not wei). The declared decimals are tried first, then each of `decimals_candidates` — a short
DECLARED list (wei, plain tokens, the 1e-7 scale other Pendle API fields came back on), never a
free search over powers of ten, which could fit a USD figure to the band. The band spans less
than 10x, so at most one candidate can fit; if one does, it is stored with the scale named in
the log line beside the raw values and the latest (partial) epoch, for a reader to hold against
the staking page. If none or several fit, nothing is stored. The last payload is kept at
<log cache>/pendle-spendle-data.json either way.
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


def epochs(payload: dict, spec: dict, decimals=None) -> tuple[list[tuple[pd.Timestamp, float]], list]:
    """[(date, tokens)] from sPendleHistoricalData, and the raw amounts (for the log)."""
    hist = payload.get(spec["history_key"]) or {}
    ts, raw = hist.get(spec["time_field"]) or [], hist.get(spec["amount_field"]) or []
    if not ts or len(ts) != len(raw):
        raise ValueError(f"{spec['history_key']}: {len(ts)} {spec['time_field']} against {len(raw)} "
                         f"{spec['amount_field']}; keys {sorted(hist)[:12]}")
    scale = 10.0 ** int(spec["decimals"] if decimals is None else decimals)
    out = []
    for t, a in zip(ts, raw):
        t = int(float(t))
        when = pd.Timestamp(t, unit="s" if t < 10**11 else "ms").normalize()
        out.append((when, float(a) / scale))
    return out, list(raw)


def fitting_scale(payload: dict, spec: dict, now) -> tuple:
    """(decimals, rows, median, tried) for the one declared scale whose median epoch lands in the
    band — the declared decimals first, then `decimals_candidates`. (None, [], None, tried) when
    none fits, or when more than one does."""
    lo, hi = spec["units_check"]["median_between"]
    tried, fits = [], []
    for k in dict.fromkeys([int(spec["decimals"]), *map(int, spec.get("decimals_candidates", ()))]):
        rows, _ = epochs(payload, spec, k)
        done = [(d, v) for d, v in rows if d <= now]
        med = statistics.median(v for _, v in done) if done else None
        tried.append((k, med))
        if med is not None and lo <= med <= hi:
            fits.append((k, done, med))
    if len(fits) == 1:
        return (*fits[0], tried)
    return None, [], None, tried


def _keep(payload) -> None:
    import json
    from .archive import cache_root
    try:
        f = cache_root() / "pendle-spendle-data.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(payload, indent=1, default=str))
    except Exception:  # noqa: BLE001 — a copy for reading; never fails the fetch
        pass


class PendleEpochs:
    """One GET per project declaring `spendle_epochs` (Pendle only)."""

    def __init__(self, get=None):
        self.get = get or _get

    @staticmethod
    def _last_complete_apr(name, spec, payload, apr_key, raw, now, out):
        """lastEpochApr read 0 (Jake's run, 2026-09-29): a 0 APR is not shown as Pendle's own figure.

        The raw value is logged with whether the latest epoch in sPendleHistoricalData is still
        in progress (its start + epoch_days is after now). The last COMPLETE epoch's `aprs` entry
        is stored instead, dated that epoch; where there is none above 0, nothing is stored and
        the cross-check reads unavailable."""
        hist = payload.get(spec["history_key"]) or {}
        ts, aprs = hist.get(spec["time_field"]) or [], hist.get(spec.get("aprs_field", "aprs")) or []
        days = int(spec.get("epoch_days", 14))
        epochs = []
        for t, a in zip(ts, aprs):
            t = int(float(t))
            start = pd.Timestamp(t, unit="s" if t < 10**11 else "ms")
            epochs.append((start, start + pd.Timedelta(days=days), a))
        partial = bool(epochs) and epochs[-1][1] > pd.Timestamp.now("UTC").tz_localize(None)
        done = [(s0, e, a) for s0, e, a in epochs if e <= pd.Timestamp.now("UTC").tz_localize(None)]
        what = (f"{apr_key} reads {raw!r} (raw); the latest epoch "
                + (f"started {epochs[-1][0].date()} and is IN PROGRESS until {epochs[-1][1].date()}"
                   if partial else (f"started {epochs[-1][0].date()} and is complete" if epochs
                                    else "is not in the payload")))
        try:
            last = next(((s0, float(a)) for s0, e, a in reversed(done) if float(a) > 0), None)
        except (TypeError, ValueError):
            last = None
        if last is None:
            out.log.append(LogEntry(SOURCE, name, 0, "ok", f"{spec['apr_metric']}: {what}; no complete "
                                                           f"epoch with an APR above 0 — the cross-check is "
                                                           f"UNAVAILABLE this run, not 0", TIER))
            return
        s0, a = last
        out.add(point(name, spec["apr_metric"], a,
                      f"{SOURCE}:{spec['history_key']}.aprs[last complete epoch]", TIER, s0.normalize()),
                SOURCE, name, f"{spec['apr_metric']}={a} — {what}; stored the last COMPLETE epoch's APR "
                              f"(started {s0.date()}) instead of 0", TIER)

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
            _keep(payload)
            lo, hi = spec["units_check"]["median_between"]
            k, done, med, tried = fitting_scale(payload, spec, now)
            shown = ", ".join(f"/10^{d}: {'none' if m is None else f'{m:,.4f}'}" for d, m in tried)
            if k is None:
                out.fail(SOURCE, name, f"{metric}: UNITS NOT CONFIRMED — median epoch {shown}; none "
                                       f"(or more than one) of the declared scales lands in {lo:,}..{hi:,} "
                                       f"({spec['units_check']['source']}); raw {raw[:4]} ... latest "
                                       f"{raw[-1:]}. NOTHING STORED.", TIER)
                out.gap(name, metric, reason=f"the epoch amounts fit none of the declared scales "
                                             f"({shown}); raw {raw[:4]}",
                        tiers_attempted="3", suggestion="Read the raw values in the Run Log (or "
                                                        "pendle-spendle-data.json in the log cache) "
                                                        "against the staking page's epochs.")
            else:
                note = ("" if k == int(spec["decimals"]) else
                        f" — DECLARED /10^{spec['decimals']} DID NOT FIT; the payload's scale is /10^{k}")
                frame = pd.DataFrame([point(name, metric, v, f"{SOURCE}:{spec['history_key']}."
                                            f"{spec['amount_field']}", TIER, d).iloc[0] for d, v in done])
                out.add(frame, SOURCE, name,
                        f"{metric}: {len(done)} epoch(s) {done[0][0].date()}..{done[-1][0].date()}, "
                        f"median {med:,.0f} PENDLE — UNITS CONFIRMED against the staking page's "
                        f"{lo:,}..{hi:,} (raw {raw[0]} / 10^{k}; latest epoch {done[-1][1]:,.0f} PENDLE "
                        f"from raw {raw[-1]}){note}", TIER)
            apr_key = spec.get("apr_field")
            apr = payload.get(apr_key) if apr_key else None
            if apr is not None:
                try:
                    v = float(apr)
                except (TypeError, ValueError):
                    out.fail(SOURCE, name, f"{spec['apr_metric']}: {apr_key}={apr!r} is not a number", TIER)
                    continue
                if v > 0:
                    out.add(point(name, spec["apr_metric"], v, f"{SOURCE}:{apr_key}", TIER, now), SOURCE, name,
                            f"{spec['apr_metric']}={v} ({apr_key}, Pendle's own APR; a cross-check)", TIER)
                else:
                    self._last_complete_apr(name, spec, payload, apr_key, apr, now, out)
            elif apr_key:
                out.log.append(LogEntry(SOURCE, name, 0, "ok", f"{apr_key} absent; payload keys "
                                                             f"{sorted(payload)[:12]}", TIER))
