"""
fetch/maple_transparency.py — the PARSER for maple.finance/transparency. Not wired into a run.

** NOT WIRED, AND WHY. ** robots.txt is the gate for this page, and it has never been read. The
only evidence is run 20260921T100546Z's line "robots.txt disallows https://maple.finance/
transparency", which cannot tell a Disallow rule from a robots.txt that answered 401/403 (the
stdlib robot parser treats both as "disallowed"). fetch/scrape.robots_verdict now says which,
and check_offline_items.maple_transparency prints the robots.txt body, the verdict and — only if
allowed — this parser's output. Promotion to primary waits on that output.

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
import re

import pandas as pd

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
