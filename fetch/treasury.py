"""
fetch/treasury.py — tier 1: the 3-MONTH US T-BILL, the risk-free benchmark beside every yield (Jake's overnight review of
ab5f2a9, Phase 3, 2026-10-12).

PRIMARY: the US Treasury's own Daily Treasury Bill Rates, one CSV a year (no key):

    https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/<YEAR>/all
        ?type=daily_treasury_bill_rates&field_tdr_date_value=<YEAR>&page&_format=csv
    -> Date (MM/DD/YYYY), "13 WEEKS BANK DISCOUNT", "13 WEEKS COUPON EQUIVALENT", ... (percent)

tbill_3m_yield = the 13-week COUPON EQUIVALENT (the bond-equivalent investment yield — the figure comparable with a
token yield; the bank-discount rate understates it), stored as a fraction (4.15% -> 0.0415).

CROSS-CHECK: FRED DTB3 (https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3, no key; "." marks a holiday) — the
3-month secondary-market rate on a DISCOUNT basis, stored as tbill_3m_dtb3. It equals Treasury's 13-week bank
discount on the same day (4.05 / 4.05 on 2026-10-08, read 2026-10-12), so a gap between the two is a read problem.

Global series, stored under the pseudo-project "Macro" (config.MACRO_PROJECT; no project row on any tab). The first
read stores config.TRAJECTORY history_days (455) of history; later reads the current year (and the last, in January).
Once a day (DailyChecks).
"""
from __future__ import annotations

import csv
import io
import logging

import pandas as pd

import config
from .base import tidy, today

log = logging.getLogger("token_metrics.fetch.treasury")

SOURCE = "treasury_bills"
TIER = 1
TREASURY_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
                "{year}/all?type=daily_treasury_bill_rates&field_tdr_date_value={year}&page&_format=csv")
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3"
COLUMN = "13 WEEKS COUPON EQUIVALENT"


def parse_treasury(text: str) -> list[tuple] | str:
    """[(day, fraction)] from one year's CSV, or why it is refused."""
    rows = list(csv.DictReader(io.StringIO(text or "")))
    if not rows or COLUMN not in rows[0]:
        return f"no '{COLUMN}' column (header: {list(rows[0])[:4] if rows else 'empty'})"
    out = []
    for r in rows:
        v = (r.get(COLUMN) or "").strip()
        if not v:
            continue
        try:
            out.append((pd.Timestamp(pd.to_datetime(r["Date"], format="%m/%d/%Y")), float(v) / 100.0))
        except (ValueError, KeyError):
            continue
    return sorted(out) or "no dated rows"


def parse_fred(text: str) -> list[tuple] | str:
    """[(day, fraction)] from FRED's DTB3 CSV ("." = no observation), or why it is refused."""
    rows = list(csv.reader(io.StringIO(text or "")))
    if len(rows) < 2 or len(rows[0]) < 2 or "DTB3" not in rows[0][1]:
        return f"not FRED's DTB3 CSV (header: {rows[0] if rows else 'empty'})"
    out = []
    for d, v in rows[1:]:
        if v.strip() in ("", "."):
            continue
        try:
            out.append((pd.Timestamp(d), float(v) / 100.0))
        except ValueError:
            continue
    return out or "no dated rows"


def _get_text(url: str) -> str:
    """One polite GET (honest UA, two retries), the body as text: both sources serve CSV, not JSON."""
    import time
    import requests
    from .base import USER_AGENT
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=(10, 45))
            r.raise_for_status()
            return r.text
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise last


class TreasuryBills:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, get_text=None, daily=None, **_ignored):
        self.get_text = get_text or _get_text            # url -> body text (CSV); injectable for tests
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily

    def _text(self, url: str) -> str:
        return self.get_text(url)

    def run(self, projects: list[dict], window_days, out):
        name = config.MACRO_PROJECT
        day = str(today().date())
        key = "macro:tbill_3m"
        if not self.daily.due(key, day):
            for m in ("tbill_3m_yield", "tbill_3m_dtb3"):
                out.mark_current(SOURCE, name, m, f"{m}: read today already", TIER)
            return
        first = not self.daily.ever(key)
        span = int(config.TRAJECTORY["history_days"]) if first else 14
        since = today().normalize() - pd.Timedelta(days=span)
        years = sorted({since.year, today().year})
        rows, why = [], []
        for y in years:
            try:
                got = parse_treasury(self._text(TREASURY_URL.format(year=y)))
            except Exception as e:  # noqa: BLE001
                got = f"{type(e).__name__}: {str(e)[:120]}"
            if isinstance(got, str):
                why.append(f"{y}: {got}")
            else:
                rows += got
        keep = [(d, v) for d, v in rows if d >= since]
        if keep:
            out.add(tidy(keep, name, "tbill_3m_yield", f"{SOURCE}:home.treasury.gov[13w coupon equivalent]", TIER),
                    SOURCE, name, f"tbill_3m_yield: {len(keep)} day(s) {keep[0][0].date()}..{keep[-1][0].date()}, "
                                  f"latest {keep[-1][1]:.4%} (US Treasury daily bill rates, 13-week coupon equivalent)",
                    TIER)
        else:
            out.fail(SOURCE, name, "tbill_3m_yield: NOTHING STORED — " + ("; ".join(why) or "no row in the window"),
                     TIER)
        try:
            fred = parse_fred(self._text(FRED_URL))
        except Exception as e:  # noqa: BLE001
            fred = f"{type(e).__name__}: {str(e)[:120]}"
        if isinstance(fred, str):
            out.fail(SOURCE, name, f"tbill_3m_dtb3 (FRED cross-check): {fred}", TIER)
        else:
            fk = [(d, v) for d, v in fred if d >= since]
            if fk:
                out.add(tidy(fk, name, "tbill_3m_dtb3", f"{SOURCE}:fred.DTB3[discount basis]", TIER), SOURCE, name,
                        f"tbill_3m_dtb3: {len(fk)} day(s), latest {fk[-1][1]:.4%} (FRED DTB3, discount basis — "
                        f"the cross-check of Treasury's 13-week bank discount)", TIER)
        if keep:
            self.daily.done(key, day)
