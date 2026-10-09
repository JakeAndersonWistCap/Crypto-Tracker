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
    # OLDEST FIRST, WHATEVER ORDER THE API SENDS (Jake, 2026-09-29): the published-APR line said
    # "the latest epoch started 2026-01-29" — sPENDLE's launch — because the array's LAST element
    # was taken as the latest. Every consumer here reads [-1] as the newest, so the pairs are
    # sorted by timestamp once, here, and `raw` is returned in the same order.
    pairs = sorted(((int(float(t)), a) for t, a in zip(ts, raw)), key=lambda x: x[0])
    out = []
    for t, a in pairs:
        when = pd.Timestamp(t, unit="s" if t < 10**11 else "ms").normalize()
        out.append((when, float(a) / scale))
    return out, [a for _, a in pairs]


def completed(rows: list, spec: dict, now) -> list:
    """The epochs that have ENDED (start + epoch_days <= now). The epoch in progress is still
    accruing — Jake's staking page showed it at 82,545 against ~170K-350K for a whole epoch — and
    counted as a full epoch it pulls the per-epoch mean, and so the token yield, down."""
    days = int(spec.get("epoch_days", 14))
    return [(d, v) for d, v in rows if d + pd.Timedelta(days=days) <= now]


def fitting_scale(payload: dict, spec: dict, now) -> tuple:
    """(decimals, rows, median, tried) for the one declared scale whose median epoch lands in the
    band — the declared decimals first, then `decimals_candidates`. (None, [], None, tried) when
    none fits, or when more than one does."""
    lo, hi = spec["units_check"]["median_between"]
    tried, fits = [], []
    for k in dict.fromkeys([int(spec["decimals"]), *map(int, spec.get("decimals_candidates", ()))]):
        rows, _ = epochs(payload, spec, k)
        done = completed(rows, spec, now)
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
    def _apr_line(out, name, spec, text, stored=None, when=None, source=None):
        """THE ONE LINE FOR THE PUBLISHED APR, whatever happened (Jake, 2026-09-29: he could not
        find it). Every outcome writes a Run Log row that starts with the metric's name —
        source pendle_api, project Pendle — and the same text at INFO on the console."""
        msg = f"{spec['apr_metric']}: {text}"
        if stored is not None:
            out.add(point(name, spec["apr_metric"], stored, source, TIER, when), SOURCE, name, msg, TIER)
        else:
            out.log.append(LogEntry(SOURCE, name, 0, "ok", msg, TIER))
        log.info("%s / %s: %s", SOURCE, name, msg)

    def _published_apr(self, name, spec, payload, now, out):
        apr_key = spec.get("apr_field")
        if not apr_key:
            return
        apr = payload.get(apr_key) if isinstance(payload, dict) else None
        if apr is None:
            keys = sorted(payload)[:12] if isinstance(payload, dict) else type(payload).__name__
            self._apr_line(out, name, spec, f"{apr_key} ABSENT from the payload (keys {keys}) — "
                                            f"the cross-check is unavailable this run")
            return
        try:
            v = float(apr)
        except (TypeError, ValueError):
            self._apr_line(out, name, spec, f"{apr_key}={apr!r} is not a number — unavailable this run")
            return
        if v > 0:
            self._apr_line(out, name, spec, f"{v} ({apr_key} raw {apr!r}, Pendle's own APR; a cross-check)",
                           stored=v, when=now, source=f"{SOURCE}:{apr_key}")
        else:
            self._last_complete_apr(name, spec, payload, apr_key, apr, now, out)

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
        for t, a in sorted(((int(float(t)), a) for t, a in zip(ts, aprs)), key=lambda x: x[0]):
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
            PendleEpochs._apr_line(out, name, spec, f"{what}; no complete epoch with an APR above 0 — "
                                                    f"the cross-check is UNAVAILABLE this run, not 0")
            return
        s0, a = last
        PendleEpochs._apr_line(out, name, spec, f"{a} — {what}; stored the last COMPLETE epoch's APR "
                                                f"(started {s0.date()}) instead of 0",
                               stored=a, when=s0.normalize(),
                               source=f"{SOURCE}:{spec['history_key']}.aprs[last complete epoch]")

    @staticmethod
    def _epoch_aprs(name, spec, payload, out):
        """PENDLE'S OWN APR, EPOCH BY EPOCH (Jake's run 2026-10-08 11:27): sPendleHistoricalData.aprs, one row per
        COMPLETE epoch dated the epoch's start — the reference the headline's per-epoch arithmetic must reproduce.
        A value outside (0, 1) is not a fraction and is not stored."""
        metric = spec.get("epoch_apr_metric")
        if not metric:
            return
        hist = payload.get(spec["history_key"]) or {}
        ts, aprs = hist.get(spec["time_field"]) or [], hist.get(spec.get("aprs_field", "aprs")) or []
        days = int(spec.get("epoch_days", 14))
        now = pd.Timestamp.now("UTC").tz_localize(None)
        rows, refused = [], []
        for t, a in zip(ts, aprs):
            t = int(float(t))
            start = pd.Timestamp(t, unit="s" if t < 10**11 else "ms")
            if start + pd.Timedelta(days=days) > now:
                continue                                          # in progress: its APR is not final
            try:
                v = float(a)
            except (TypeError, ValueError):
                refused.append(f"{start.date()} {a!r}")
                continue
            if 0 < v < 1:
                rows.append((start.normalize(), v))
            else:
                refused.append(f"{start.date()} {a!r}")
        if rows:
            frame = pd.DataFrame([point(name, metric, v, f"{SOURCE}:{spec['history_key']}.aprs", TIER, d).iloc[0]
                                  for d, v in sorted(rows)])
            out.add(frame, SOURCE, name, f"{metric}: {len(rows)} complete epoch APR(s) "
                    f"{min(rows)[0].date()}..{max(rows)[0].date()}"
                    + (f"; not stored (not a fraction): {', '.join(refused)}" if refused else ""), TIER)
        else:
            # SAID, NOT SILENT (Jake's run 2026-10-09 11:41, 1a: the fallback was empty and nothing said why). The field
            # is present but no complete epoch holds an APR in (0, 1) — Pendle's per-epoch APR cannot be the fallback.
            msg = (f"{metric}: NOTHING STORED — sPendleHistoricalData.{spec.get('aprs_field', 'aprs')} holds no complete "
                   f"epoch APR in (0, 1)" + (f"; read: {', '.join(refused[-6:])}" if refused else
                                             "; the field is empty") + " — the published-APR fallback is unavailable")
            out.log.append(LogEntry(SOURCE, name, 0, "ok", msg, TIER))
            log.info("%s / %s: %s", SOURCE, name, msg)

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
                self._apr_line(out, name, spec, f"not read — robots.txt: {why}")
                continue
            try:
                payload = self.get(spec["url"])
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                out.fail(SOURCE, name, f"{metric}: {spec['url']}: {e}", TIER)
                out.gap(name, metric, reason=f"Pendle's spendle/data did not answer: {e}",
                        tiers_attempted="3", suggestion="Run check_offline_items.py pendle_spendle_fees.")
                self._apr_line(out, name, spec, f"not read — spendle/data did not answer: {e}")
                continue
            try:
                rows, raw = epochs(payload, spec)
            except Exception as e:  # noqa: BLE001
                out.fail(SOURCE, name, f"{metric}: {spec['url']}: {e}", TIER)
                out.gap(name, metric, reason=f"Pendle's spendle/data did not give the epoch series: {e}",
                        tiers_attempted="3", suggestion="Run check_offline_items.py pendle_spendle_fees.")
                self._published_apr(name, spec, payload, now, out)     # the APR is a separate field
                continue
            _keep(payload)
            lo, hi = spec["units_check"]["median_between"]
            k, done, med, tried = fitting_scale(payload, spec, now)
            shown = ", ".join(f"/10^{d}: {'none' if m is None else f'{m:,.4f}'}" for d, m in tried)
            if k is None or not done:
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
                allk, _ = epochs(payload, spec, k)
                live = [(d, v) for d, v in allk if (d, v) not in set(done)]
                ts = [int(float(t)) for t in (payload[spec["history_key"]][spec["time_field"]])]
                order = ("newest-first" if ts == sorted(ts, reverse=True) and len(ts) > 1 else
                         "oldest-first" if ts == sorted(ts) else "unordered")
                out.add(frame, SOURCE, name,
                        f"{metric}: payload holds {len(allk)} epoch(s) {allk[0][0].date()}..{allk[-1][0].date()} "
                        f"(API order {order}; sorted by date here); stored {len(done)} COMPLETED "
                        f"{done[0][0].date()}..{done[-1][0].date()}, median {med:,.0f} PENDLE — UNITS "
                        f"CONFIRMED against the staking page's {lo:,}..{hi:,} (/10^{k}; last complete epoch "
                        f"{done[-1][1]:,.0f} PENDLE)"
                        # EVERY STORED EPOCH, EVERY RUN (Jake's run 2026-10-09, 5b): the first run that reads an epoch
                        # above 0 dates Pendle's publish lag (credibility.epoch_publish_lag).
                        + "; STORED PER EPOCH: " + " | ".join(f"{d.date()} {v:,.0f}" for d, v in done)
                        + (f"; IN PROGRESS, not stored until it ends: " + ", ".join(
                            f"{d.date()} at {v:,.0f} PENDLE" for d, v in live) if live else "")
                        + note, TIER)
            self._published_apr(name, spec, payload, now, out)
            self._epoch_aprs(name, spec, payload, out)
