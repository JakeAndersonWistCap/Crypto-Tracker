"""
fetch/sky_accounting.py — tier 3: Sky's accounting, as Block Analitica publishes it (Jake, 2026-10-07).

THE API (sky.data.blockanalitica.com, documented in the "Balance Sheet / Cash Flow / Profit and Loss API" pages Jake
supplied 2026-10-07). Every response is {"data": ..., "status": 200, "success": true}; amounts are decimal STRINGS
at full precision; history endpoints take group_by=day|month|quarter|year and label each period ("2026-05"). P&L and
cash flow start 2025-01-01. Read monthly:

  NET PROTOCOL SURPLUS = revenue - expense - revenue_distribution ("Remitted to Sky Reserves") — CONFIRMED by Jake
  against Sky's own monthly table (2026-10-07 afternoon: Jan-Sep 2026; e.g. Aug 31.85M - 18.74M - 2.48M = 10.63M),
  after the 14:17 run matched May (9,709,132 vs 9,710,000) and June (10,807,203 vs 10,810,000). The formula is
  DECLARED (spec nps_formula) and always stored; the comparison with the months Sky reported is logged each run and
  a month beyond NPS_MATCH_TOL becomes a Review Queue item against that month — it never blocks the series.
  (Before that confirmation the three combinations below were matched, and only a match was stored.)
      GET /v1/accounting/profit-and-loss/history/?group_by=month              -> [{date, type, amount}] per type
          (type: revenue | expense | revenue_distribution; amounts positive, the type carries the sign)
      GET /v1/accounting/profit-and-loss/history/?group_by=month&type=revenue_distribution
          &category=Security%20and%20Maintenance                                -> the S&M part of the distribution
    Three candidates per month: revenue - expense (net revenue); revenue - expense - Security and Maintenance (Sky's
    financials page counts S&M among the expenses: $99.07M - $61.27M with S&M $8.63M, Jake 2026-09-29); revenue -
    expense - all revenue_distribution (after the buyback and flap too). Each is set against the months Sky REPORTED
    (manual_overrides.csv net_protocol_surplus_usd_reported: May-Aug 2026). The candidate that meets EVERY reported
    month within NPS_MATCH_TOL is stored as net_protocol_surplus_usd, its source naming the combination; if none does,
    NOTHING is stored and the failure prints every candidate against every month.

  BUYBACK SPENDING ($) — cash-flow category "Buyback Spending" (outflows, negative; stored positive)
      GET /v1/accounting/cash-flow/items/history/?group_by=month&category=Buyback%20Spending
  STAKING REWARDS ($) — cash-flow category "Staking Rewards" (outflows; stored positive)
      GET /v1/accounting/cash-flow/items/history/?group_by=month&category=Staking%20Rewards
    Both summed over sources per month. References, not headlines: buyback spending is set beside our flapper
    buyback in dollars, staking rewards beside the USDS the Splitter mints to the lsSKY farm.

  CUMULATIVE SKY BOUGHT — info-sky.blockanalitica.com/buyback/historic/?days_ago=N&format=json -> line[] with
      sky_cumulative_buyback per day (1.977bn on 2026-09-07, Jake). Stored daily as sky_cumulative_buyback_ba; its rise
      over a window is set beside our flapper counted inflow over the same days (Credibility in_buyback_cumulative_ba).

  THE TWO lsSKY FARMS — info-sky.blockanalitica.com/api/v1/farms/<farm>/historic/ -> daily apy, total_staked,
      total_farmed (cumulative rewards). Stored per farm: total staked, the APY as published, and daily rewards =
      d(total_farmed) (a fall — a reset — is refused, never stored as negative rewards). SKY farm rewards are emissions
      (beside our on-chain release); USDS farm rewards are the revenue-funded staking yield (beside the USDS mint scan).

Only COMPLETE months are stored for the accounting series (the current month is still accruing), dated to month-end.
robots.txt is read first for each host (fetch.scrape.robots_verdict); a refusal stores nothing from that host.
"""
from __future__ import annotations

import logging

import pandas as pd

from .base import Http, tidy, today

log = logging.getLogger("token_metrics.fetch.sky_accounting")

SOURCE = "sky_accounting"
TIER = 3
NPS_MATCH_TOL = 0.02          # a candidate must meet every month Sky reported within 2%
NPS_CANDIDATES = ("revenue-expense", "revenue-expense-security_and_maintenance",
                  "revenue-expense-revenue_distribution")


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


def nps_candidates(by_type: list[dict], security: list[dict]) -> dict:
    """{candidate: {month: value}} from the per-type P&L history and the Security and Maintenance rows. A month is a
    candidate's only where revenue and expense are both present."""
    side: dict = {}
    for r in by_type:
        try:
            side.setdefault(str(r["type"]), {}).setdefault(str(r["date"]), 0.0)
            side[str(r["type"])][str(r["date"])] += float(r["amount"])
        except (KeyError, TypeError, ValueError):
            continue
    rev, exp = side.get("revenue", {}), side.get("expense", {})
    dist, sec = side.get("revenue_distribution", {}), monthly_sum(security)
    months = sorted(set(rev) & set(exp))
    return {"revenue-expense": {m: rev[m] - exp[m] for m in months},
            "revenue-expense-security_and_maintenance": {m: rev[m] - exp[m] - sec.get(m, 0.0) for m in months},
            "revenue-expense-revenue_distribution": {m: rev[m] - exp[m] - dist.get(m, 0.0) for m in months}}


def reported_nps(project: str = "Sky", metric: str = "net_protocol_surplus_usd_reported") -> dict:
    """{month: USD} — the months Sky reported, as hand-entered in manual_overrides.csv."""
    import csv
    from pathlib import Path
    f = Path(__file__).resolve().parent.parent / "manual_overrides.csv"
    out = {}
    try:
        lines = [ln for ln in f.read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")]
    except OSError:
        return out
    for r in csv.DictReader(lines):
        if r.get("project") == project and r.get("metric") == metric:
            try:
                out[r["date"][:7]] = float(r["value"])
            except (KeyError, TypeError, ValueError):
                continue
    return out


def choose_nps(cands: dict, reported: dict, tol: float = NPS_MATCH_TOL) -> tuple[str | None, str]:
    """(the candidate meeting every reported month within tol — the closest if several — or None, the table)."""
    lines, best = [], None
    for name in NPS_CANDIDATES:
        vals = cands.get(name) or {}
        common = [m for m in sorted(reported) if m in vals]
        errs = {m: (vals[m] / reported[m] - 1) if reported[m] else float("inf") for m in common}
        worst = max((abs(e) for e in errs.values()), default=float("inf"))
        lines.append(f"{name}: " + (", ".join(f"{m} {vals[m]:,.0f} vs {reported[m]:,.0f} ({e:+.1%})"
                                             for m, e in errs.items()) or "no reported month in the answer"))
        if common and worst <= tol and (best is None or worst < best[1]):
            best = (name, worst)
    return (best[0] if best else None), "; ".join(lines)


def daily_rows(body) -> list[dict] | str:
    """The row list of an info-sky answer: a bare list, or under results / data / line."""
    rows = body
    for k in ("results", "data", "line"):
        if isinstance(rows, dict) and k in rows:
            rows = rows[k]
    if isinstance(rows, dict) and isinstance(rows.get("results"), list):
        rows = rows["results"]
    if not isinstance(rows, list):
        return f"no row list (top-level keys: {sorted(body)[:12] if isinstance(body, dict) else type(body).__name__})"
    return rows


def day_of(r: dict):
    for k in ("date", "datetime", "day", "timestamp", "dt"):
        if r.get(k) is not None:
            try:
                return pd.Timestamp(r[k]).tz_localize(None).normalize() if not isinstance(r[k], (int, float)) \
                    else pd.Timestamp(int(r[k]), unit="s").normalize()
            except (TypeError, ValueError):
                return None
    return None


def daily_series(rows: list[dict], field: str) -> pd.Series:
    """{day: field} — the last row of each day."""
    out = {}
    for r in rows:
        d = day_of(r) if isinstance(r, dict) else None
        try:
            v = float(r[field])
        except (KeyError, TypeError, ValueError):
            continue
        if d is not None:
            out[d] = v
    return pd.Series(out, dtype=float).sort_index()


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

            # 1. NET PROTOCOL SURPLUS — the combination that meets the months Sky reported
            m = spec["metrics"]["nps"]
            try:
                bt = _rows(get("/v1/accounting/profit-and-loss/history/"))
                sm = _rows(get("/v1/accounting/profit-and-loss/history/", type="revenue_distribution",
                               category="Security and Maintenance"))
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                out.fail(SOURCE, name, f"{m}: {e}", TIER)
                bt = sm = None
            if isinstance(bt, str) or isinstance(sm, str):
                out.fail(SOURCE, name, f"{m}: {bt if isinstance(bt, str) else sm} — NOTHING STORED", TIER)
            elif bt is not None:
                cands = nps_candidates(bt, sm)
                formula = spec.get("nps_formula", "revenue-expense-revenue_distribution")
                reported = reported_nps(name)
                _chosen, table = choose_nps(cands, reported)
                self._store(out, name, m, cands[formula], last_complete, f"{SOURCE}:pnl.{formula}",
                            f"NPS = {formula} (declared; confirmed against Sky's own table). Against the months Sky "
                            f"reported: {table}")
                for mo in sorted(reported):                  # a reported month beyond tolerance: review that month
                    v = cands[formula].get(mo)
                    if v is not None and reported[mo] and abs(v / reported[mo] - 1) > NPS_MATCH_TOL \
                            and pd.Period(mo, "M") <= last_complete:
                        out.review_item(name, m, "nps_month_differs_from_reported", "review", value=v,
                                        prior_value=reported[mo], date=month_end(mo), source=f"{SOURCE}:pnl.{formula}",
                                        tier=TIER, basis=f"{mo}: Block Analitica {formula} {v:,.0f} vs the reported "
                                                         f"{reported[mo]:,.0f} ({v / reported[mo] - 1:+.1%}) — re-check "
                                                         f"the reported month at source")
                # REVENUE ALLOCATION (SKY buyback + USDS distribution) = revenue_distribution less Security and
                # Maintenance — set beside our flapper buyback $ + the USDS minted to the lsSKY farm, month by month.
                alloc_m = spec["metrics"].get("revenue_allocation")
                if alloc_m:
                    dist, sec = {}, monthly_sum(sm)
                    for r in bt:
                        if str(r.get("type")) == "revenue_distribution":
                            try:
                                dist[str(r["date"])] = dist.get(str(r["date"]), 0.0) + float(r["amount"])
                            except (KeyError, TypeError, ValueError):
                                continue
                    self._store(out, name, alloc_m, {k: v - sec.get(k, 0.0) for k, v in dist.items()}, last_complete,
                                f"{SOURCE}:pnl.revenue_distribution-security_and_maintenance",
                                "Revenue Allocation = revenue_distribution less Security and Maintenance (the SKY "
                                "buyback + the USDS distribution)")
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
            self._info_sky(out, name, spec.get("info_sky") or {})

    def _info_sky(self, out, name: str, spec: dict) -> None:
        """Cumulative SKY bought and the two farms' daily staked / APY / rewards (info-sky.blockanalitica.com)."""
        from .scrape import robots_verdict
        if not spec:
            return
        base = spec["base_url"].rstrip("/")
        allowed, why = robots_verdict(base + "/api/v1/farms/")
        if not allowed:
            out.fail(SOURCE, name, f"info-sky: robots.txt disallows {base} — {why}; nothing read", TIER)
            return
        last_day = today().normalize() - pd.Timedelta(days=1)       # a complete day only

        def keep(sr: pd.Series) -> list:
            return [(d, float(v)) for d, v in sr.items() if d <= last_day]
        bb = spec.get("buyback")
        if bb:
            try:
                rows = daily_rows(self.http.get(base + "/buyback/historic/",
                                                params={"days_ago": bb.get("days_ago", 365), "format": "json"}))
            except Exception as e:  # noqa: BLE001
                rows = f"{e}"
            sr = daily_series(rows, "sky_cumulative_buyback") if isinstance(rows, list) else None
            if sr is None or sr.empty:
                out.fail(SOURCE, name, f"{bb['metric']}: {rows if isinstance(rows, str) else 'no sky_cumulative_buyback rows'}"
                                       f" — NOTHING STORED", TIER)
            elif keep(sr):
                k = keep(sr)
                out.add(tidy(k, name, bb["metric"], f"{SOURCE}:info-sky.buyback.sky_cumulative_buyback", TIER),
                        SOURCE, name, f"{bb['metric']}: {len(k)} day(s) {k[0][0]:%Y-%m-%d}..{k[-1][0]:%Y-%m-%d}, "
                                      f"latest {k[-1][1]:,.0f} SKY", TIER)
        for farm, m in (spec.get("farms") or {}).items():
            try:
                rows = daily_rows(self.http.get(base + f"/api/v1/farms/{farm}/historic/",
                                                params={"p_size": m.get("p_size", 400)}))
            except Exception as e:  # noqa: BLE001
                rows = f"{e}"
            if isinstance(rows, str):
                out.fail(SOURCE, name, f"farm {farm[:10]}…: {rows} — NOTHING STORED", TIER)
                continue
            for field, metric in (("total_staked", m.get("staked")), ("apy", m.get("apy"))):
                if not metric:
                    continue
                k = keep(daily_series(rows, field))
                if not k:
                    out.fail(SOURCE, name, f"{metric}: no `{field}` rows in the farm's history", TIER)
                    continue
                out.add(tidy(k, name, metric, f"{SOURCE}:info-sky.farm.{field}", TIER), SOURCE, name,
                        f"{metric}: {len(k)} day(s), latest {k[-1][1]:,.6g} ({k[-1][0]:%Y-%m-%d}) — `{field}` as "
                        f"published", TIER)
            if m.get("rewards"):
                cum = daily_series(rows, "total_farmed")
                d = cum.diff().dropna()
                bad = d[d < 0]
                d = d[d >= 0]
                k = keep(d)
                if k:
                    out.add(tidy(k, name, m["rewards"], f"{SOURCE}:info-sky.farm.d(total_farmed)", TIER), SOURCE,
                            name, f"{m['rewards']}: {len(k)} day(s) = d(total_farmed)"
                                  + (f"; {len(bad)} fall(s) refused (a reset is not negative rewards)" if len(bad)
                                     else ""), TIER)
                else:
                    out.fail(SOURCE, name, f"{m['rewards']}: fewer than two `total_farmed` days", TIER)

    @staticmethod
    def _store(out, name, metric, vals: dict, last_complete, source, how):
        keep = sorted((month_end(k), v) for k, v in vals.items() if pd.Period(k, "M") <= last_complete)
        if not keep:
            out.fail(SOURCE, name, f"{metric}: no complete month in the answer ({sorted(vals)[-3:]})", TIER)
            return
        out.add(tidy(keep, name, metric, source, TIER), SOURCE, name,
                f"{metric}: {len(keep)} complete month(s) {keep[0][0]:%Y-%m}..{keep[-1][0]:%Y-%m}, latest "
                f"${keep[-1][1]:,.0f} — {how}", TIER)
