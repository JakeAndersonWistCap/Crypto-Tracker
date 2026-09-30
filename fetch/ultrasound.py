"""
fetch/ultrasound.py — Ethereum's DAILY supply and staked-ETH history, from ultrasound.money's API.
A1/A3 of the second pass (Jake, 2026-09-30).

    GET https://ultrasound.money/api/v2/fees/supply-projection-inputs

ONE CALL gives both series, each a list of {"t": unix seconds, "v": ETH as a float}:

    supplyByDay              total ETH supply, one point per UTC day
    inBeaconValidatorsByDay  ETH held by beacon-chain validators, one point per UTC day

** READ FROM ULTRASOUND.MONEY'S OWN BACKEND, NOT GUESSED. ** ultrasoundmoney/eth-analysis-rs
@1012fcb74037bc45bf545fec240e6a4ad3d36052:
  src/serve/mod.rs:182                               the route
  src/bin/update-supply-projection-inputs/main.rs:53-59  the camelCase fields
  main.rs:90-98; src/beacon_chain/balances/mod.rs:64-90  inBeaconValidatorsByDay = the first
                                                     beacon_validators_balance row of each day,
                                                     Gwei / 1e9
  src/eth_supply/over_time.rs:358-391                supplyByDay = the daily supply: Glassnode's
                                                     daily figure up to the backend's first own
                                                     row, then the backend's own reading
  src/eth_supply/store.rs:61                         the backend's supply = execution balances +
                                                     beacon balances - beacon deposits: a MEASURED
                                                     total, not delta-summed
The shape is still verified on every call and NOTHING is stored if it does not hold.

WHAT IT FEEDS: a CROSS-CHECK BEFORE 2024-06-22 ONLY. Jake's probe (2026-09-30) found both
series frozen at 2024-06-22 (t=1719014400), so they cannot give the last year; the issuance and
staked history route is ETH.Store's per-day consensus rewards (fetch/beaconchain.py). A frozen
series is stored whole and its log line says FROZEN.

ROBOTS ARE CHECKED AGAINST THE API PATH ITSELF (RFC 9309), not the page: the live run of
2026-09-17/18 refused the page root (https://ultrasound.money/), which says nothing about
/api/v2/fees/... by itself. A disallow is respected and said; it is never routed around.

POLITE: one call a day (DailyChecks), an honest User-Agent (Http), today's point never stored.
"""
from __future__ import annotations

import logging

import pandas as pd

from .base import Http, tidy, today

log = logging.getLogger("token_metrics.fetch.ultrasound")

SOURCE = "ultrasound"
TIER = 3


class UltrasoundHistory:
    """Daily ETH supply and staked ETH for any project declaring an `ultrasound_history` block."""

    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, daily=None, **_ignored):
        self.http = http or Http(min_interval=1.0, retries=2)
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = p.get("ultrasound_history")
            if spec:
                self._project(p["name"], spec, out)

    def _project(self, name: str, spec: dict, out) -> None:
        url, day = spec["url"], str(today().date())
        if not self.daily.due(f"ultrasound:{url}", day):
            for metric in spec["series"].values():
                out.mark_current(SOURCE, name, metric, f"{metric}: {url} already read today ({day}); "
                                                       f"the daily series does not change intraday", TIER)
            return
        from .scrape import robots_verdict
        allowed, why = robots_verdict(url)
        if not allowed:
            for metric in spec["series"].values():
                out.fail(SOURCE, name, f"{metric}: robots.txt disallows {url} — {why}", TIER)
                out.gap(name, metric, reason=f"robots.txt disallows the API path {url} — {why}",
                        tiers_attempted="3", suggestion="Not worked around. See the licensing register.")
            return
        try:
            body = self.http.get(url)
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            for metric in spec["series"].values():
                out.fail(SOURCE, name, f"{metric}: {url}: {e}", TIER)
            return
        self.daily.done(f"ultrasound:{url}", day)
        stored = self.store(name, spec, body, out)
        if stored:
            log.info("%s: ultrasound history stored %s", name, stored)

    @staticmethod
    def _points(rows) -> list[tuple] | None:
        """[(date, value)] from [{"t": unix s, "v": number}], or None if any row breaks the shape."""
        if not isinstance(rows, list) or not rows:
            return None
        pts = []
        for r in rows:
            if not isinstance(r, dict) or not isinstance(r.get("t"), (int, float)) \
                    or not isinstance(r.get("v"), (int, float)) or isinstance(r.get("v"), bool):
                return None
            pts.append((pd.Timestamp(int(r["t"]), unit="s"), float(r["v"])))
        return pts

    def store(self, name: str, spec: dict, body, out) -> dict:
        """Store each declared series; refuse any whose shape differs from the source-read one."""
        done = {}
        keys = sorted(body)[:20] if isinstance(body, dict) else type(body).__name__
        kd = spec.get("keep_days", 400)
        horizon = today() - pd.Timedelta(days=int(kd)) if kd else pd.Timestamp.min
        for field, metric in spec["series"].items():
            pts = self._points(body.get(field)) if isinstance(body, dict) else None
            if pts is None:
                out.fail(SOURCE, name, f"{metric}: {field} is not a list of {{t, v}} numbers — the "
                                       f"response carries {keys}. NOTHING STORED.", TIER)
                out.gap(name, metric, reason=f"ultrasound.money's {field} did not have the shape read "
                                             f"from its backend source (list of {{t, v}}); keys {keys}",
                        tiers_attempted="3", suggestion="Correct `ultrasound_history` from the keys above.")
                continue
            pts = [(d, v) for d, v in pts if horizon <= d.normalize() < today()]
            if not pts:
                out.fail(SOURCE, name, f"{metric}: {field} held no complete day in the last "
                                       f"{spec.get('keep_days', 400)} days", TIER)
                continue
            frame = tidy(pts, name, metric, f"{SOURCE}:{field}", TIER)
            last = frame["date"].max()
            # FROZEN SERIES SAY SO (Jake's probe, 2026-09-30: both end 2024-06-22).
            frozen = (f" — FROZEN: the series ends {last.date()}, {(today() - last).days} days ago; "
                      f"{spec.get('role', 'cross-check only')}") if (today() - last).days > 30 else ""
            out.add(frame, SOURCE, name, f"{metric} = ultrasound.money {field}: {len(frame)} day(s) "
                                         f"{frame['date'].min().date()}..{last.date()} "
                                         f"(ETH) — {spec.get('note', '')}{frozen}", TIER)
            done[metric] = len(frame)
        return done
