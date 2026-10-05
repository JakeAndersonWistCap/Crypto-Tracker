"""
fetch/xref.py — INDEPENDENT REFERENCES for the Credibility tab (Jake, 2026-10-05).

Figures from sources OTHER than the one each headline is read from, stored under their own metrics
so build_workbook.write_credibility can set them beside our figure. None of them feeds a headline.

  price_usd_coinbase   Coinbase Exchange public market data, one daily candle per UTC day:
                         GET https://api.exchange.coinbase.com/products/{SYMBOL}-USD/candles
                             ?granularity=86400&start=<ISO>&end=<ISO>
                         -> [[time, low, high, open, close, volume], ...]   (time = the day's start, unix s)
                       The OPEN of day D is the price at D 00:00 UTC — the instant CoinGecko's daily
                       market_chart point for D is stamped — so the two compare on the same instant.
                       An exchange price: independent of CoinGecko's aggregate. A product that does not
                       exist answers HTTP 404; it is remembered as NOT LISTED and re-tried weekly.
  price_usd_llama      DefiLlama's coins API, one point per UTC day at 00:00, for the token's VERIFIED
                       chain:address (config contracts) — the second price source for every project, and the
                       only one where no Coinbase product is declared:
                         GET https://coins.llama.fi/chart/{keys}?start=<unix>&span=<days>&period=1d&searchWidth=600
                         -> {"coins": {"<key>": {"prices": [{"timestamp", "price"}], "symbol", "confidence"}}}
                       A point more than 10 minutes from 00:00 is dropped (searchWidth 600 s). Where no
                       address is on file (native coins) the key is coingecko:<id> — CoinGecko's own price
                       relayed, so that row is FRESH-only, never a PASS.
  staking_apr_lido     Lido's stETH APR, 7-day simple moving average, as a FRACTION:
                         GET https://eth-api.lido.fi/v1/protocol/steth/apr/sma
                         -> {"data": {"aprs": [{"timeUnix", "apr"}, ...], "smaApr": <percent>}, "meta": {...}}
                       NET of Lido's 10% protocol fee — labelled as Lido's, and grossed up (/0.9) only
                       on the Credibility row that compares it with the network's staking yield.

POLITE: robots.txt per host (the pipeline's RFC 9309 reader), once a day per reference (DailyChecks),
an honest User-Agent, ~1 request a second. The first read takes `days` of candles, later runs a week.
"""
from __future__ import annotations

import logging

import pandas as pd

import config
from .base import Http, tidy, today

log = logging.getLogger("token_metrics.fetch.xref")

SOURCE = "xref"
TIER = 1
COINBASE = "https://api.exchange.coinbase.com/products/{product}/candles"
LLAMA_CHART = "https://coins.llama.fi/chart/{keys}"
LIDO_SMA = "https://eth-api.lido.fi/v1/protocol/steth/apr/sma"


class CrossRefs:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, daily=None, robots=None, **_ignored):
        self.http = http or Http(min_interval=1.0, retries=2)
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily
        if robots is None:
            from .scrape import robots_verdict
            robots = robots_verdict
        self.robots = robots

    def run(self, projects: list[dict], window_days, out):
        spec = config.CREDIBILITY_XREF
        day = str(today().date())
        names = set(config.CREDIBILITY_PROJECTS)
        mine = [p for p in projects if p["name"] in names]
        for p in mine:
            # ONLY A DECLARED PRODUCT (Jake's credibility run, 2026-10-05): "<SYMBOL>-USD" can be another
            # asset with the same ticker — Fluid read 22% off CoinGecko on the guessed FLUID-USD.
            if (spec.get("coinbase_product") or {}).get(p["name"]):
                self._coinbase(p, spec, day, out)
            if p["name"] in spec.get("lido_apr_projects", ()):
                self._lido(p, day, out)
        if mine:
            self._llama(mine, spec, day, out)

    # ------------------------------------------------------------------ Coinbase candles
    def _coinbase(self, p: dict, spec: dict, day: str, out) -> None:
        name = p["name"]
        product = spec["coinbase_product"][name]
        key = f"xref:coinbase:{product}"
        absent = self.daily.get(f"{key}:absent")
        if absent and (pd.Timestamp(day) - pd.Timestamp(absent)).days < 7:
            out.skipped(SOURCE, name, f"price_usd_coinbase: {product} not listed on Coinbase Exchange "
                                      f"(HTTP 404 on {absent}; re-tried weekly)", TIER)
            return
        if not self.daily.due(key, day):
            out.skipped(SOURCE, name, f"price_usd_coinbase: {product} read today already", TIER)
            return
        url = COINBASE.format(product=product)
        ok, why = self.robots(url)
        if not ok:
            out.fail(SOURCE, name, f"price_usd_coinbase: robots.txt disallows {url} ({why})", TIER)
            return
        days = int(spec.get("days", 30)) if not self.daily.ever(key) else 7
        end = today().normalize() + pd.Timedelta(days=1)
        start = end - pd.Timedelta(days=days)
        try:
            body = self.http.get(url, params={"granularity": 86400, "start": start.isoformat(),
                                              "end": end.isoformat()})
        except Exception as e:  # noqa: BLE001 — a 404 is an answer: not listed
            if "404" in str(e) or "NotFound" in str(e):
                self.daily.set(f"{key}:absent", day)
                out.skipped(SOURCE, name, f"price_usd_coinbase: {product} not listed on Coinbase Exchange "
                                          f"(HTTP 404) — the price row uses its fallback reference", TIER)
                return
            out.fail(SOURCE, name, f"price_usd_coinbase: {product}: {e}", TIER)
            return
        rows = self.parse_candles(body)
        if isinstance(rows, str):
            out.fail(SOURCE, name, f"price_usd_coinbase: {product}: {rows}", TIER)
            return
        frame = tidy(rows, name, "price_usd_coinbase", f"coinbase_exchange:{product}:open", TIER)
        out.add(frame, SOURCE, name, f"price_usd_coinbase: {len(rows)} day(s) of {product} daily OPEN", TIER)
        self.daily.done(key, day)

    @staticmethod
    def parse_candles(body) -> list[tuple] | str:
        """[(day, open)] from Coinbase Exchange daily candles, or why they are refused."""
        if not isinstance(body, list):
            return f"not a list of candles ({type(body).__name__}: {str(body)[:120]})"
        rows = []
        for c in body:
            if not isinstance(c, (list, tuple)) or len(c) < 6:
                return f"a candle that is not [time, low, high, open, close, volume]: {str(c)[:80]}"
            t, opn = int(c[0]), float(c[3])
            if t % 86400:
                return f"a candle that does not start at 00:00 UTC (time {t})"
            rows.append((pd.Timestamp(t, unit="s"), opn))
        return sorted(rows)

    # ------------------------------------------------------------------ DefiLlama coins
    @staticmethod
    def llama_key(p: dict) -> tuple[str, bool]:
        """(coins key, independent?) — the token's verified chain:address where config has one, else
        coingecko:<id> (CoinGecko's own price relayed: not independent)."""
        cs = p.get("contracts") or {}
        for c in (cs.values() if isinstance(cs, dict) else cs):
            if isinstance(c, dict) and c.get("kind") == "erc20_total_supply" and c.get("verified") \
                    and c.get("expected_symbol", p.get("symbol")) == p.get("symbol") and c.get("address"):
                return f"{c.get('chain', 'ethereum')}:{c['address'].lower()}", True
        return f"coingecko:{p['coingecko_id']}", False

    def _llama(self, projects: list[dict], spec: dict, day: str, out) -> None:
        key = "xref:llama_prices"
        if not self.daily.due(key, day):
            for p in projects:
                out.skipped(SOURCE, p["name"], "price_usd_llama: read today already", TIER)
            return
        ok, why = self.robots(LLAMA_CHART.format(keys="x"))
        if not ok:
            for p in projects:
                out.fail(SOURCE, p["name"], f"price_usd_llama: robots.txt disallows coins.llama.fi ({why})", TIER)
            return
        keys = {self.llama_key(p)[0]: p for p in projects}
        days = int(spec.get("days", 30)) if not self.daily.ever(key) else 7
        start = int((today().normalize() - pd.Timedelta(days=days)).timestamp())
        try:
            body = self.http.get(LLAMA_CHART.format(keys=",".join(keys)),
                                 params={"start": start, "span": days + 1, "period": "1d", "searchWidth": 600})
        except Exception as e:  # noqa: BLE001
            for p in projects:
                out.fail(SOURCE, p["name"], f"price_usd_llama: {e}", TIER)
            return
        coins = (body or {}).get("coins") if isinstance(body, dict) else None
        if not isinstance(coins, dict):
            for p in projects:
                out.fail(SOURCE, p["name"], f"price_usd_llama: no `coins` in the answer", TIER)
            return
        for k, p in keys.items():
            pts = (coins.get(k) or {}).get("prices") or []
            rows = []
            for pt in pts:
                t = int(pt.get("timestamp") or 0)
                d = pd.Timestamp(t, unit="s")
                mid = d.normalize()
                off = min(abs((d - mid).total_seconds()), abs((mid + pd.Timedelta(days=1) - d).total_seconds()))
                if off > 600 or pt.get("price") is None:
                    continue                    # not a 00:00 point: never compared with CoinGecko's 00:00
                rows.append((mid if (d - mid).total_seconds() <= 600 else mid + pd.Timedelta(days=1), float(pt["price"])))
            if not rows:
                out.fail(SOURCE, p["name"], f"price_usd_llama: no 00:00 point for {k}", TIER)
                continue
            indep = self.llama_key(p)[1]
            frame = tidy(rows, p["name"], "price_usd_llama", f"defillama_coins:{k}", TIER)
            out.add(frame, SOURCE, p["name"], f"price_usd_llama: {len(rows)} day(s) for {k}"
                                              + ("" if indep else " (coingecko key: CoinGecko's own price, relayed)"), TIER)
        self.daily.done(key, day)

    # ------------------------------------------------------------------ Lido APR
    def _lido(self, p: dict, day: str, out) -> None:
        name, key = p["name"], "xref:lido_apr"
        if not self.daily.due(key, day):
            out.skipped(SOURCE, name, "staking_apr_lido: read today already", TIER)
            return
        ok, why = self.robots(LIDO_SMA)
        if not ok:
            out.fail(SOURCE, name, f"staking_apr_lido: robots.txt disallows {LIDO_SMA} ({why})", TIER)
            return
        try:
            body = self.http.get(LIDO_SMA)
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"staking_apr_lido: {e}", TIER)
            return
        sma = ((body or {}).get("data") or {}).get("smaApr") if isinstance(body, dict) else None
        try:
            frac = float(sma) / 100.0
        except (TypeError, ValueError):
            out.fail(SOURCE, name, f"staking_apr_lido: no data.smaApr in the answer "
                                   f"(keys {sorted((body or {}).get('data') or {})[:8] if isinstance(body, dict) else '?'})",
                     TIER)
            return
        if not 0 < frac < 0.2:
            out.fail(SOURCE, name, f"staking_apr_lido: {sma}% is outside 0-20% — refused (a unit change?)", TIER)
            return
        frame = tidy([(today(), frac)], name, "staking_apr_lido", "lido_api:steth/apr/sma", TIER)
        out.add(frame, SOURCE, name, f"staking_apr_lido: {frac:.4%} (Lido stETH 7-day SMA, net of Lido's fee)", TIER)
        self.daily.done(key, day)
