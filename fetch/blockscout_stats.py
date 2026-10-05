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
    (nativeCoinSupply was configured too, and answered HTTP 404 on Plume's instance, 2026-10-01 —
    removed; Plume's supply is read from the Ethereum ERC-20 instead.)

TRANSACTIONS EXCLUDE THE ONE-PER-BLOCK SYSTEM TRANSACTION (Jake, 2026-10-05). Plume is an Arbitrum
Orbit chain: every block carries one ArbitrumInternalTx, and newTxns counts it — Q0 read 35,511,343
(~395K/day) where growthepie reads ~200K/day. Blockscout's own definition (blockscout/blockscout-rs
@bfc3771, stats/stats/src/charts/lines/blockscout_instance/transactions/arbitrum_new_operational_txns.rs:
"transactions excluding a one-per-block system transaction ... the difference between number of
transactions and number of blocks") is the chart newOperationalTxns. A line spec may name it with a
`minus` fallback: where the instance does not serve that chart, newTxns - newBlocks per day, the same
arithmetic. A chart read for the first time reads its full `days` of history, so a changed
definition never leaves older days on the old one.

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
        for metric, line in spec["lines"].items():
            chart, minus = (line, None) if isinstance(line, str) else (line["chart"], line.get("minus"))
            key = f"blockscout_stats:{base}:{chart}"
            if not self.daily.due(key, day):
                out.mark_current(SOURCE, name, metric, f"{metric}: {chart} already read today ({day})", TIER)
                continue
            have = self.last_dates.get((name, metric))
            ever = getattr(self.daily, "ever", lambda k: True)(key)
            days = 30 if (have is not None and ever) else int(spec.get("days", 365))
            start = (today() - pd.Timedelta(days=days)).date()
            params = {"from": str(start), "to": day, "resolution": "DAY"}
            label = chart
            try:
                pts = self.parse(self.http.get(f"{base}/api/v1/lines/{chart}", params=params))
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                pts = f"{e}"
                if minus and "404" in str(e):
                    pts = self._difference(base, minus, params)
                    label = f"{minus[0]}-{minus[1]}"
            if isinstance(pts, str):
                out.fail(SOURCE, name, f"{metric}: {chart}: {pts}. NOTHING STORED.", TIER)
                continue
            self.daily.done(key, day)
            pts = [(d, v) for d, v in pts if d.normalize() < today()]
            if not pts:
                out.fail(SOURCE, name, f"{metric}: {label} returned no complete day from {start}", TIER)
                continue
            frame = tidy(pts, name, metric, f"{SOURCE}:{label}", TIER)
            out.add(frame, SOURCE, name, f"{metric} = {label} ({spec.get('unit_note', {}).get(metric, '')}"
                                         f"{'' if metric not in spec.get('unit_note', {}) else '; '}"
                                         f"{len(frame)} day(s) {frame['date'].min().date()}.."
                                         f"{frame['date'].max().date()})", TIER)

    def _difference(self, base: str, charts: tuple, params: dict) -> list[tuple] | str:
        """a - b per day (newTxns - newBlocks), only on days both charts serve; else why not."""
        series = []
        for c in charts:
            try:
                pts = self.parse(self.http.get(f"{base}/api/v1/lines/{c}", params=params))
            except Exception as e:  # noqa: BLE001
                return f"{c} (for the {charts[0]}-{charts[1]} fallback): {e}"
            if isinstance(pts, str):
                return f"{c}: {pts}"
            series.append(dict(pts))
        a, b = series
        out = [(d, a[d] - b[d]) for d in sorted(set(a) & set(b))]
        bad = [d for d, v in out if v < 0]
        if bad:
            return f"{charts[0]} < {charts[1]} on {len(bad)} day(s) (first {bad[0].date()}) — not one system tx per block"
        return out
