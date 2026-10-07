"""
fetch/sky_accounting.py — tier 3: Sky's accounting, as Block Analitica publishes it (Jake, 2026-10-07).

THE API (sky.data.blockanalitica.com, documented in the "Balance Sheet / Cash Flow / Profit and Loss API" pages Jake
supplied 2026-10-07). Every response is {"data": ..., "status": 200, "success": true}; amounts are decimal STRINGS
at full precision; history endpoints take group_by=day|month|quarter|year and label each period ("2026-05"). P&L and
cash flow start 2025-01-01. Read monthly:

  NET PROTOCOL SURPLUS = P&L net - "Security and Maintenance"
      GET /v1/accounting/profit-and-loss/statement/history/?group_by=month   -> [{date, revenue, expense, net}]
      GET /v1/accounting/profit-and-loss/history/?group_by=month&type=revenue_distribution
          &category=Security%20and%20Maintenance                                -> [{date, type, amount}]
    WHY THE SUBTRACTION: Sky's own financials page (financial.skyeco.com, Jake's reading 2026-09-29) states NPS as
    revenue less expenses INCLUDING Security and Maintenance ($99.07M - $61.27M, S&M $8.63M among the expenses).
    Block Analitica's P&L books Security and Maintenance BELOW net revenue, in revenue_distribution with the SKY
    buyback and flap — so its `net` is NPS + S&M. Checked against the four months Sky reported (May-Aug 2026,
    net_protocol_surplus_usd_reported) on Credibility; a month that disagrees says the definition, not the API, moved.

  BUYBACK SPENDING ($) — cash-flow category "Buyback Spending" (outflows, negative; stored positive)
      GET /v1/accounting/cash-flow/items/history/?group_by=month&category=Buyback%20Spending
  STAKING REWARDS ($) — cash-flow category "Staking Rewards" (outflows; stored positive)
      GET /v1/accounting/cash-flow/items/history/?group_by=month&category=Staking%20Rewards
    Both summed over sources per month. References, not headlines: buyback spending is set beside our flapper
    buyback in dollars, staking rewards beside the USDS the Splitter mints to the lsSKY farm.

Only COMPLETE months are stored (the current month is still accruing), dated to month-end. robots.txt is read first
for the host (fetch.scrape.robots_verdict); a refusal stores nothing.
"""
from __future__ import annotations

import logging

import pandas as pd

from .base import Http, tidy, today

log = logging.getLogger("token_metrics.fetch.sky_accounting")

SOURCE = "sky_accounting"
TIER = 3


def month_end(label: str) -> pd.Timestamp:
    return pd.Period(label, "M").end_time.normalize()


def _rows(body) -> list[dict] | str:
    """The `data` list of a history endpoint, or why it is refused."""
    if not isinstance(body, dict) or body.get("success") is False:
        return f"not a success envelope ({str(body)[:120]})"
    data = body.get("data")
    if not isinstance(data, list):
        return f"`data` is {type(data).__name__}, not the list a history endpoint returns"
    return data


def monthly_sum(rows: list[dict], field: str = "amount", sign: float = 1.0) -> dict:
    """{month label: signed sum of `field`} — several sources per month are added."""
    out: dict = {}
    for r in rows:
        try:
            label, v = str(r["date"]), float(r[field])
        except (KeyError, TypeError, ValueError):
            continue
        out[label] = out.get(label, 0.0) + sign * v
    return out


def nps(statement: list[dict], security: list[dict]) -> tuple[dict, list[str]]:
    """{month: P&L net - Security and Maintenance}, and the months refused (no `net`)."""
    sec = monthly_sum(security)
    out, refused = {}, []
    for r in statement:
        try:
            out[str(r["date"])] = float(r["net"]) - sec.get(str(r["date"]), 0.0)
        except (KeyError, TypeError, ValueError):
            refused.append(str(r.get("date")))
    return out, refused


class SkyAccounting:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, **_ignored):
        self.http = http or Http(min_interval=1.0, retries=2)

    def run(self, projects: list[dict], window_days, out):
        from .scrape import robots_verdict
        for p in projects:
            spec = p.get("sky_accounting")
            if not spec:
                continue
            name, base = p["name"], spec["base_url"].rstrip("/")
            allowed, why = robots_verdict(base + "/v1/accounting/")
            if not allowed:
                for m in spec["metrics"].values():
                    out.fail(SOURCE, name, f"{m}: robots.txt disallows {base}/v1/accounting/ — {why}", TIER)
                    out.gap(name, m, reason=f"robots.txt disallows {base}/v1/accounting/ — {why}",
                            tiers_attempted="3", suggestion="Not worked around.")
                continue
            last_complete = (today().to_period("M") - 1)
            first = str(spec.get("from", "2025-01-01"))

            def get(path, **params):
                return self.http.get(base + path, params={"group_by": "month", "date_from": first, **params})

            # 1. NET PROTOCOL SURPLUS
            m = spec["metrics"]["nps"]
            try:
                st = _rows(get("/v1/accounting/profit-and-loss/statement/history/"))
                sm = _rows(get("/v1/accounting/profit-and-loss/history/", type="revenue_distribution",
                               category="Security and Maintenance"))
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                out.fail(SOURCE, name, f"{m}: {e}", TIER)
                st = sm = None
            if isinstance(st, str) or isinstance(sm, str):
                out.fail(SOURCE, name, f"{m}: {st if isinstance(st, str) else sm} — NOTHING STORED", TIER)
            elif st is not None:
                vals, refused = nps(st, sm)
                self._store(out, name, m, vals, last_complete,
                            f"{SOURCE}:pnl.net-security_and_maintenance",
                            "P&L net (revenue - expense) less Security and Maintenance (revenue_distribution)"
                            + (f"; refused month(s) without `net`: {refused}" if refused else ""))
            # 2-3. CASH-FLOW CATEGORIES (outflows are negative; stored as positive spending)
            for key, category in (("buyback", "Buyback Spending"), ("staking", "Staking Rewards")):
                m = spec["metrics"].get(key)
                if not m:
                    continue
                try:
                    rows = _rows(get("/v1/accounting/cash-flow/items/history/", category=category))
                except Exception as e:  # noqa: BLE001
                    out.fail(SOURCE, name, f"{m}: {e}", TIER)
                    continue
                if isinstance(rows, str):
                    out.fail(SOURCE, name, f"{m}: {rows} — NOTHING STORED", TIER)
                    continue
                self._store(out, name, m, monthly_sum(rows, sign=-1.0), last_complete,
                            f"{SOURCE}:cash-flow.{category.replace(' ', '_')}",
                            f"cash-flow category {category!r}, summed over sources, sign flipped (outflows)")

    @staticmethod
    def _store(out, name, metric, vals: dict, last_complete, source, how):
        keep = sorted((month_end(k), v) for k, v in vals.items() if pd.Period(k, "M") <= last_complete)
        if not keep:
            out.fail(SOURCE, name, f"{metric}: no complete month in the answer ({sorted(vals)[-3:]})", TIER)
            return
        out.add(tidy(keep, name, metric, source, TIER), SOURCE, name,
                f"{metric}: {len(keep)} complete month(s) {keep[0][0]:%Y-%m}..{keep[-1][0]:%Y-%m}, latest "
                f"${keep[-1][1]:,.0f} — {how}", TIER)
