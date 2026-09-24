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
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=45)
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
            frame = window(tidy(sorted(rows), name, metric, SOURCE, TIER), window_days)
            out.add(frame, SOURCE, name,
                    f"{metric} = Token Buybacks `{v['field']}`, monthly, "
                    f"{min(rows)[0].date()}..{max(rows)[0].date()} dated to month-end; {limit}", TIER)
