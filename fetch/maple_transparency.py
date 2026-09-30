"""
fetch/maple_transparency.py — tier 3: maple.finance/transparency, Maple's own published figures.

** PRIMARY FOR MAPLE'S TREASURY SINCE 2026-09-24. ** Jake's run of
check_offline_items.maple_transparency: robots.txt answered HTTP 200 with "User-agent: * /
Allow: /", and the page parsed to SYRUP Holdings 79,210,000 and Liquid Assets $4,310,000, with 5
of 13 buyback rows in the server-rendered HTML. The 2026-09-21 "robots.txt disallows" was never
a rule — it was the stdlib parser's reading of a status code. robots.txt is still checked on
every run (fetch.scrape.robots_verdict), and a refusal stops the fetch.

KNOWN, PERMANENT LIMIT: the Token Buybacks table shows 5 rows server-side; the other 8 are paged
client-side and are not reachable without a browser. Accepted (Jake, 2026-09-24) — not a bug.
The series therefore starts at the oldest visible month and grows one month at a time.

THE PAGE (Jake, 2026-09-24): Astro v5.18.1, server-side rendered, so the figures are in the HTML
and no browser is needed. Tags are replaced by spaces before matching, so adjacent cells never
run together ("Aug 2026$147,098.00676,293.73" would be unparseable); the holdings regex takes
optional whitespace either side of the number because the same label reads "SYRUP Holdings78.78M"
in textContent.

  SYRUP Holdings   78.78M          -> 78,780,000 SYRUP, ROUNDED to 10,000 by the page itself
  Liquid Assets    $4.41M          -> 4,410,000 USD
  Token Buybacks   one row a month: "Aug 2026 $147,098.00 676,293.73 $0.2175"
                   = month, USD spent, SYRUP acquired, average price

THE SSF CHART (Jake's probe, 2026-09-30): an Astro island's props carry the chart's data,
`datasets.{LAST_1W, 1M, 1Y, ALL}` = [{ts, syrupHoldings, liquidAssetsUsd}], each value wrapped in
Astro's [type, value] pairs (withastro/astro @f637bd9 packages/astro/src/runtime/server/
serialize.ts:4-17 — 0 Value/object, 1 array, 3 Date, 4 Map, 5 Set, 6 BigInt, 11 Infinity).
islands() unwraps them; ssf_series() takes the dataset reaching furthest back and keeps one
reading a UTC day (the last). Stored daily as ssf_holdings_tokens (unrounded, e.g. 2026-09-24
79,206,476.97), and per complete month:

    pool_release_tokens(month) = H(first day) - H(first day of next month) + SYRUP bought (month)

a fall in the fund net of the buyback inflow the same page lists — only for months where the
chart has both boundary days and the buyback row is visible (never assumed 0). ** STORED AS
ssf_net_outflow_tokens, SIGNED, NOT AS pool_release_tokens (Jake's probes2, 2026-09-30): ** two
months came out NEGATIVE (2025-11 -3,052,593; 2026-07 -971,641) — the fund took in more than that
month's buybacks, so it has another inflow. Until those inflows are traced and classified the net
change is not treated as a release (config Maple.pool_release_tokens_blocked).

MAPLE'S OWN MONTHLY REVENUE (revenueUsd) is on the page too, in another island's datasets (12
months in LAST_1Y, 31 in ALL; Aug 2026 $1,470,979.15). It is PRIMARY revenue_usd for Maple, monthly,
dated to month-end; DefiLlama's is stored as revenue_usd_defillama and compared (defillama_metric_as).

IMPLIED BUYBACK for months whose buyback row is paged out of reach: the month's revenue x MIP-021's
tier (10% under $1.5M, 20% $1.5-2M, 30% over $2M — the band of that month's revenue, applied to
all of it), stored as actual_buyback_usd_implied, labelled IMPLIED and never mixed with the
measured actual_buyback_usd. Only inside the table's own span (N rows = N months ending at the
newest visible one — one row a month is the table's layout).

A PRICE IN THE USD FIELD IS REFUSED. The island props' buyback amountUsd held average PRICES for
Jul/Aug 2026 ("0.1604", "0.2175") and a dollar amount for June ("$375,000.00"). The table text is
what is parsed, and its per-row check below catches a price in the USD cell (usd != syrup x price).

EACH BUYBACK ROW IS CHECKED AGAINST ITSELF: USD must equal SYRUP x average price to within the
price's own displayed precision (4 dp, so half of 0.0001 per token). That is not a tolerance on a
reconciliation — it is the rounding the page applied — and a row outside it is a mis-aligned
parse, which is refused rather than stored.
"""
from __future__ import annotations

import html
import logging
import re

import pandas as pd

from .base import USER_AGENT, point, tidy, today, window

log = logging.getLogger("token_metrics.fetch.maple_transparency")

SOURCE = "maple_page"
TIER = 3

URL = "https://maple.finance/transparency"

_SUFFIX = {"K": 1e3, "M": 1e6, "B": 1e9}
_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
_NUM = r"[\d,]+(?:\.\d+)?"

HOLDINGS_RE = re.compile(r"SYRUP Holdings\s*(" + _NUM + r")\s*([KMB])\b")
LIQUID_RE = re.compile(r"Liquid Assets\s*\$\s*(" + _NUM + r")\s*([KMB])\b")
BUYBACK_RE = re.compile(r"\b(" + _MONTHS + r")[a-z]*\.?\s+(20\d\d)\s+\$\s*(" + _NUM + r")\s+("
                        + _NUM + r")\s+\$\s*(" + _NUM + r")")
SHOWING_RE = re.compile(r"Showing\s+(\d+)\s*[-–]\s*(\d+)\s+of\s+(\d+)")


def page_text(markup: str) -> str:
    """HTML to one line of text: scripts and styles dropped, every tag a space, entities decoded."""
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", markup)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def parse(markup_or_text: str) -> dict:
    """Everything the page states that this tool would store, plus what could not be read.

    Returns {holdings_syrup, holdings_rounding, liquid_assets_usd, buybacks, refused, showing}.
    buybacks is a DataFrame (month, usd, syrup, avg_price), one row per month, newest first as
    the page lists them. A figure that is absent is None — never 0.
    """
    text = page_text(markup_or_text) if "<" in markup_or_text else markup_or_text
    out: dict = {"holdings_syrup": None, "holdings_rounding": None, "liquid_assets_usd": None,
                 "buybacks": pd.DataFrame(columns=["month", "usd", "syrup", "avg_price"]),
                 "refused": [], "showing": None}

    found = HOLDINGS_RE.findall(text)
    if len(found) > 1 and len(set(found)) > 1:
        out["refused"].append(f"'SYRUP Holdings' matched {len(found)} different figures: {found}")
    elif found:
        n, suf = found[0]
        out["holdings_syrup"] = _num(n) * _SUFFIX[suf]
        decimals = len(n.split(".")[1]) if "." in n else 0
        out["holdings_rounding"] = 0.5 * 10 ** -decimals * _SUFFIX[suf]

    found = LIQUID_RE.findall(text)
    if found:
        n, suf = found[0]
        out["liquid_assets_usd"] = _num(n) * _SUFFIX[suf]

    rows = []
    for mon, year, usd, syrup, price in BUYBACK_RE.findall(text):
        usd, syrup, price = _num(usd), _num(syrup), _num(price)
        month = pd.Timestamp(f"{mon} {year}").to_period("M").to_timestamp()
        # The price is shown to 4 dp, so it can be off by 0.00005 per token; USD is to the cent.
        slack = syrup * 0.00005 + 0.01
        if abs(usd - price) < 0.0001 and syrup > 1:
            out["refused"].append(f"{mon} {year}: the USD cell (${usd}) equals the average price — a "
                                  f"PRICE in the USD field, not stored")
            continue
        if abs(usd - syrup * price) > slack:
            out["refused"].append(f"{mon} {year}: ${usd:,.2f} != {syrup:,.2f} x ${price} "
                                  f"(= ${syrup * price:,.2f}; the page's own rounding allows "
                                  f"${slack:,.2f}) — a mis-aligned parse, not stored")
            continue
        rows.append((month, usd, syrup, price))
    if rows:
        df = pd.DataFrame(rows, columns=["month", "usd", "syrup", "avg_price"])
        dupes = df[df.duplicated("month", keep=False)]
        if not dupes.empty:
            out["refused"].append(f"months listed twice: {sorted({str(m.date()) for m in dupes['month']})}"
                                  f" — none of those months stored")
            df = df[~df["month"].isin(dupes["month"])]
        out["buybacks"] = df.reset_index(drop=True)

    m = SHOWING_RE.search(text)
    if m:
        out["showing"] = tuple(int(x) for x in m.groups())
    return out


_ISLAND_PROPS_RE = re.compile(r'<astro-island\b[^>]*?\bprops="([^"]*)"', re.S)


def _revive(v):
    """One Astro-serialised [type, value] pair, recursively (serialize.ts PROP_TYPE)."""
    if not (isinstance(v, list) and 1 <= len(v) <= 2 and isinstance(v[0], int)):
        return v
    t, val = v[0], (v[1] if len(v) > 1 else None)
    if t == 0:
        return {k: _revive(x) for k, x in val.items()} if isinstance(val, dict) else val
    if t in (1, 4, 5):
        return [_revive(x) for x in val] if isinstance(val, list) else val
    if t == 6:
        return int(val)
    if t == 11:
        return float("inf") * (val or 1)
    return val                          # 2 RegExp, 3 Date (ISO string), 7 URL, 8-10 typed arrays


def islands(markup: str) -> list[dict]:
    """Every astro-island's props, unwrapped to plain values. A blob that is not JSON is skipped."""
    import json
    out = []
    for blob in _ISLAND_PROPS_RE.findall(markup):
        try:
            raw = json.loads(html.unescape(blob))
        except ValueError:
            continue
        if isinstance(raw, dict):
            out.append({k: _revive(x) for k, x in raw.items()})
    return out


def _ts(v) -> pd.Timestamp | None:
    try:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return pd.Timestamp(int(v), unit="ms" if v > 1e12 else "s")
        return pd.Timestamp(v).tz_localize(None) if pd.Timestamp(v).tzinfo else pd.Timestamp(v)
    except (TypeError, ValueError):
        return None


def ssf_series(markup: str) -> tuple[list[tuple], str] | str:
    """([(day, syrupHoldings)], dataset name) from the island reaching furthest back, one reading
    a UTC day (the last); or why nothing was taken."""
    best = None
    for props in islands(markup):
        ds = props.get("datasets")
        if not isinstance(ds, dict):
            continue
        for key, rows in ds.items():
            if not isinstance(rows, list):
                continue
            pts = {}
            for r in rows:
                if not isinstance(r, dict) or "syrupHoldings" not in r:
                    continue
                d = _ts(r.get("ts"))
                try:
                    val = float(str(r["syrupHoldings"]).replace(",", ""))
                except (TypeError, ValueError):
                    continue
                if d is None or not pd.Timestamp("2020-01-01") <= d <= pd.Timestamp("2035-01-01"):
                    return f"dataset {key}: ts {r.get('ts')!r} is not a date this chart could hold"
                pts[d.normalize()] = val
            if pts and (best is None or min(pts) < min(best[0]) or
                        (min(pts) == min(best[0]) and len(pts) > len(best[0]))):
                best = (pts, key)
    if best is None:
        return "no astro-island carries datasets.*[{ts, syrupHoldings}]"
    return sorted(best[0].items()), best[1]


def revenue_series(markup: str) -> tuple[list[tuple], str] | str:
    """([(month_end, revenueUsd)], dataset) from the island dataset reaching furthest back whose rows
    carry revenueUsd; or why nothing was taken. One row a month; a repeated month is refused."""
    best = None
    for props in islands(markup):
        ds = props.get("datasets")
        if not isinstance(ds, dict):
            continue
        for key, rows in ds.items():
            if not isinstance(rows, list):
                continue
            pts = {}
            for r in rows:
                if not isinstance(r, dict) or "revenueUsd" not in r:
                    continue
                d = _ts(r.get("ts") if "ts" in r else r.get("date") or r.get("month"))
                try:
                    v = float(str(r["revenueUsd"]).replace(",", "").replace("$", ""))
                except (TypeError, ValueError):
                    return f"dataset {key}: revenueUsd {r['revenueUsd']!r} is not a number"
                if d is None:
                    return f"dataset {key}: a revenue row has no readable date ({sorted(r)})"
                m = month_end(d.to_period("M").to_timestamp())
                if m in pts:
                    return f"dataset {key}: month {m:%Y-%m} appears twice — not a monthly series"
                pts[m] = v
            if pts and (best is None or min(pts) < min(best[0])):
                best = (pts, key)
    if best is None:
        return "no astro-island carries datasets.*[{revenueUsd}]"
    return sorted(best[0].items()), best[1]


def implied_buybacks(revenue: list[tuple], buybacks: pd.DataFrame, showing, tiers, before) -> list[tuple]:
    """[(month_end, implied usd, rate, revenue)] for complete months inside the table's span that have
    no visible buyback row. Span: `showing`'s total N rows = the N months ending at the newest
    visible month. Nothing without a visible row to anchor the span."""
    if buybacks.empty or not showing:
        return []
    newest = buybacks["month"].max()
    first = newest - pd.DateOffset(months=int(showing[2]) - 1)
    seen = {month_end(m) for m in buybacks["month"]}
    out = []
    for m, rev in revenue:
        if not (month_end(first) <= m <= month_end(newest)) or m in seen or m >= before:
            continue
        rate = next(r for cap, r in tiers if cap is None or rev < cap)
        out.append((m, rev * rate, rate, rev))
    return out


def ssf_release(holdings: list[tuple], buybacks: pd.DataFrame, before: pd.Timestamp) -> tuple[list, list]:
    """([(month_end, release)], [why a month was skipped]) — complete months only."""
    h = dict(holdings)
    bought = {r.month: float(r.syrup) for r in buybacks.itertuples()} if not buybacks.empty else {}
    rows, skipped = [], []
    if not h:
        return rows, skipped
    m = min(h).to_period("M").to_timestamp()
    while month_end(m) < before:
        nxt = m + pd.offsets.MonthBegin(1)
        if m in h and nxt in h:
            if m in bought:
                rows.append((month_end(m), h[m] - h[nxt] + bought[m]))
            else:
                skipped.append(f"{m:%Y-%m}: no visible buyback row (paged client-side) — not assumed 0")
        elif m >= min(h):
            skipped.append(f"{m:%Y-%m}: the chart has no reading on {m.date() if m not in h else nxt.date()}")
        m = nxt
    return rows, skipped


def month_end(month_start: pd.Timestamp) -> pd.Timestamp:
    """A monthly figure is dated to its LAST day — the convention Sky's monthly NPS uses."""
    return (month_start + pd.offsets.MonthEnd(0)).normalize()


class MapleTransparency:
    """Treasury holding (stock) and monthly buybacks (flows) for any project with a
    `transparency_page` block. One GET per day; the page is cached for the rest of it."""

    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, get=None, **_ignored):
        self._get = get

    def _fetch(self, url: str) -> str:
        from .scrape import CACHE_DIR
        f = CACHE_DIR / str(today().date()) / "maple_transparency.html"
        if f.exists():
            return f.read_text(encoding="utf-8")
        if self._get is not None:
            text = self._get(url)
        else:
            import requests
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=(10, 45))
            r.raise_for_status()
            text = r.text
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        return text

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = p.get("transparency_page")
            if spec:
                self._project(p["name"], spec, window_days, out)

    def _gap_all(self, name, spec, out, reason, suggestion):
        for metric in spec["metrics"]:
            out.gap(name, metric, reason=reason, tiers_attempted="3", suggestion=suggestion)

    def _project(self, name: str, spec: dict, window_days, out):
        from .scrape import robots_verdict
        url = spec["url"]
        allowed, why = robots_verdict(url)
        if not allowed:
            out.fail(SOURCE, name, f"robots.txt disallows {url} — {why}", TIER)
            self._gap_all(name, spec, out, f"robots.txt disallows {url} — {why}",
                          "Not worked around. The daoMultisig read is the labelled reference.")
            return
        try:
            text = self._fetch(url)
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            out.fail(SOURCE, name, f"{url}: {e}", TIER)
            self._gap_all(name, spec, out, f"{url} did not answer: {e}", "Check the page.")
            return
        got = parse(text)
        for why_refused in got["refused"]:
            out.skipped(SOURCE, name, f"{url}: {why_refused}", TIER)
        metrics = spec["metrics"]

        m = next((k for k, v in metrics.items() if v["field"] == "holdings_syrup"), None)
        if m:
            if got["holdings_syrup"] is None:
                out.fail(SOURCE, name, f"{m}: 'SYRUP Holdings' not found on {url}", TIER)
                out.gap(name, m, reason=f"'SYRUP Holdings' was not found on {url} — a redesign, "
                                        f"or the label moved", tiers_attempted="3",
                        suggestion="Re-check HOLDINGS_RE in fetch/maple_transparency.py.")
            else:
                liquid = got["liquid_assets_usd"]
                liquid_txt = "n/a" if liquid is None else f"${liquid:,.0f}"
                out.add(point(name, m, got["holdings_syrup"], SOURCE, TIER, today()), SOURCE, name,
                        f"{m}={got['holdings_syrup']:,.0f} SYRUP (the page rounds to "
                        f"±{got['holdings_rounding']:,.0f}); Liquid Assets {liquid_txt}", TIER)

        # THE SSF CHART: daily holdings and the monthly release (Jake's probe 5, 2026-09-30).
        hm = next((k for k, v in metrics.items() if v["field"] == "ssf_series"), None)
        rm = next((k for k, v in metrics.items() if v["field"] == "ssf_release"), None)
        if hm or rm:
            ser = ssf_series(text)
            if isinstance(ser, str):
                for m_ in (hm, rm):
                    if m_:
                        out.fail(SOURCE, name, f"{m_}: {ser} on {url}. NOTHING STORED.", TIER)
            else:
                pts, key = ser
                pts = [(d, v) for d, v in pts if d < today()]
                if hm and pts:
                    out.add(tidy(pts, name, hm, f"{SOURCE}:island.{key}", TIER), SOURCE, name,
                            f"{hm} = the SSF chart's syrupHoldings, dataset {key}: {len(pts)} day(s) "
                            f"{pts[0][0].date()}..{pts[-1][0].date()}", TIER)
                if rm:
                    rel, skipped = ssf_release(pts, got["buybacks"], today())
                    neg = [f"{d:%Y-%m} {v:,.0f}" for d, v in rel if v < 0]
                    if rel:
                        out.add(tidy(rel, name, rm, f"{SOURCE}:ssf_release", TIER), SOURCE, name,
                                f"{rm} = SSF H(month start) - H(next month start) + SYRUP bought, "
                                f"SIGNED, {len(rel)} month(s) {rel[0][0].date()}..{rel[-1][0].date()}"
                                + (f"; NEGATIVE (an inflow other than buybacks): {', '.join(neg)}" if neg else "")
                                + (f"; skipped {'; '.join(skipped[:6])}" if skipped else ""), TIER)
                    else:
                        out.fail(SOURCE, name, f"{rm}: no complete month computable — "
                                               f"{'; '.join(skipped[:6]) or 'no chart data'}", TIER)

        # MAPLE'S OWN MONTHLY REVENUE, and the IMPLIED buyback for months paged out of the table.
        vm = next((k for k, v in metrics.items() if v["field"] == "revenue_monthly"), None)
        im = next((k for k, v in metrics.items() if v["field"] == "implied_buyback"), None)
        if vm or im:
            rs = revenue_series(text)
            if isinstance(rs, str):
                for m_ in (vm, im):
                    if m_:
                        out.fail(SOURCE, name, f"{m_}: {rs} on {url}. NOTHING STORED.", TIER)
            else:
                rev, key = rs
                rev = [(d, v) for d, v in rev if d < today()]           # complete months only
                if vm and rev:
                    out.add(tidy(rev, name, vm, f"{SOURCE}:island.{key}.revenueUsd", TIER), SOURCE, name,
                            f"{vm} = Maple's own monthly revenueUsd (dataset {key}): {len(rev)} month(s) "
                            f"{rev[0][0]:%Y-%m}..{rev[-1][0]:%Y-%m}, dated to month-end", TIER)
                if im:
                    imp = implied_buybacks(rev, got["buybacks"], got["showing"], spec["mip021_tiers"], today())
                    if imp:
                        out.add(tidy([(d, v) for d, v, _, _ in imp], name, im,
                                     f"{SOURCE}:IMPLIED(revenueUsd x MIP-021 tier)", TIER), SOURCE, name,
                                f"{im} = IMPLIED, never measured: " + "; ".join(
                                    f"{d:%Y-%m} ${r:,.0f} x {rate:.0%} = ${v:,.0f}" for d, v, rate, r in imp), TIER)
                    else:
                        out.skipped(SOURCE, name, f"{im}: no month inside the table's span lacks a "
                                                  f"visible row (or the span is unreadable)", TIER)

        bb = got["buybacks"]
        cutoff = today()
        done = bb[[month_end(mo) < cutoff for mo in bb["month"]]] if not bb.empty else bb
        shown = got["showing"]
        limit = (f"{len(bb)} of {shown[2]} rows are server-rendered; the rest are paged "
                 f"client-side and unreachable (accepted limit)") if shown else f"{len(bb)} row(s)"
        for metric, v in metrics.items():
            if v["field"] not in ("syrup", "usd"):
                continue
            if done.empty:
                out.fail(SOURCE, name, f"{metric}: no complete month in the Token Buybacks table "
                                       f"({limit})", TIER)
                out.gap(name, metric, reason=f"no complete month parsed from {url} ({limit})",
                        tiers_attempted="3", suggestion="Re-check BUYBACK_RE.")
                continue
            rows = [(month_end(r.month), float(getattr(r, v["field"]))) for r in done.itertuples()]
            # EVERY VISIBLE MONTH, NOT THE RUN'S WINDOW (2026-09-28). A 30-day window over rows
            # dated to month-end kept only August: the store held one month ($147,098) of the five
            # the page renders, and Q0 missed June and July. Each row is Maple's own published
            # figure for a closed month, so re-writing it is an idempotent upsert.
            frame = tidy(sorted(rows), name, metric, SOURCE, TIER)
            out.add(frame, SOURCE, name,
                    f"{metric} = Token Buybacks `{v['field']}`, monthly, "
                    f"{min(rows)[0].date()}..{max(rows)[0].date()} dated to month-end; {limit}", TIER)
