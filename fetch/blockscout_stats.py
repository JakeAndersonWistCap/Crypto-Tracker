"""
fetch/blockscout_stats.py — daily chain activity from a Blockscout stats service. Plume, 2026-09-30.

    GET {base}/api/v1/lines/{chart}?from=YYYY-MM-DD&to=YYYY-MM-DD&resolution=DAY
        -> {"chart": [{"date", "date_to", "value": "<string>", "is_approximate": bool}, ...]}

Jake's probe (2026-09-30) found Plume's explorer serving the Blockscout stats service at
https://explorer.plume.org/stats-service. Shape read from blockscout/blockscout-rs
(stats/stats-server, the /api/v1/lines/{name} route and its LineChart response).

One definition per chart, the explorer's own:
    newTxns          transactions per day
    activeAccounts   distinct addresses that SENT a transaction that day
    txnsFee          fees paid per day, in the NATIVE coin (PLUME) — valued at the same-day price
                     at read time (build_workbook._native_fee_usd_views)
    nativeCoinSupply the sum of native balances on this chain — NOT issuance: native PLUME moves
                     with bridging to and from Ethereum, and PLUME's total supply is flat and
                     vesting-driven (see the Plume entry). Stored as its own series, used by nothing.

A value that is not a number refuses that chart; an `is_approximate` point (the running day) is
left out. The value is a STRING in the response and is always cast.
POLITE: once a day per chart (DailyChecks), the first run reads `days` of history, later runs
the last 30 days; an honest User-Agent (Http).
"""
from __future__ import annotations

import logging

import pandas as pd

from .base import Http, tidy, today

log = logging.getLogger("token_metrics.fetch.blockscout_stats")

SOURCE = "blockscout_stats"
TIER = 1


class BlockscoutStats:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, daily=None, last_dates=None, **_ignored):
        self.http = http or Http(min_interval=1.0, retries=2)
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily
        self.last_dates = last_dates or {}

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = p.get("blockscout_stats")
            if spec:
                self._project(p["name"], spec, out)

    @staticmethod
    def parse(body) -> list[tuple] | str:
        """[(date, value)] from a LineChart response, or why it is refused."""
        rows = body.get("chart") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            keys = sorted(body)[:12] if isinstance(body, dict) else type(body).__name__
            return f"no `chart` list in the response (keys {keys})"
        pts = []
        for r in rows:
            if not isinstance(r, dict) or "date" not in r or "value" not in r:
                return f"a point without date/value: {str(r)[:120]}"
            if r.get("is_approximate"):
                continue
            try:
                pts.append((pd.Timestamp(r["date"]), float(str(r["value"]))))
            except (TypeError, ValueError):
                return f"a value that is not a number: {r.get('value')!r} on {r.get('date')}"
        return pts

    def _project(self, name: str, spec: dict, out) -> None:
        day = str(today().date())
        base = spec["base"].rstrip("/")
        for metric, chart in spec["lines"].items():
            if not self.daily.due(f"blockscout_stats:{base}:{chart}", day):
                out.mark_current(SOURCE, name, metric, f"{metric}: {chart} already read today ({day})", TIER)
                continue
            have = self.last_dates.get((name, metric))
            days = 30 if have is not None else int(spec.get("days", 365))
            start = (today() - pd.Timedelta(days=days)).date()
            url = f"{base}/api/v1/lines/{chart}"
            try:
                body = self.http.get(url, params={"from": str(start), "to": day, "resolution": "DAY"})
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                out.fail(SOURCE, name, f"{metric}: {chart}: {e}", TIER)
                continue
            self.daily.done(f"blockscout_stats:{base}:{chart}", day)
            pts = self.parse(body)
            if isinstance(pts, str):
                out.fail(SOURCE, name, f"{metric}: {chart}: {pts}. NOTHING STORED.", TIER)
                continue
            pts = [(d, v) for d, v in pts if d.normalize() < today()]
            if not pts:
                out.fail(SOURCE, name, f"{metric}: {chart} returned no complete day from {start}", TIER)
                continue
            frame = tidy(pts, name, metric, f"{SOURCE}:{chart}", TIER)
            out.add(frame, SOURCE, name, f"{metric} = {chart} ({spec.get('unit_note', {}).get(metric, '')}"
                                         f"{'' if metric not in spec.get('unit_note', {}) else '; '}"
                                         f"{len(frame)} day(s) {frame['date'].min().date()}.."
                                         f"{frame['date'].max().date()})", TIER)
