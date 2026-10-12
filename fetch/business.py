"""
fetch/business.py — tier 1: the per-project BUSINESS series (Phase 4) and MARKET SHARE (Phase 3) — Jake's overnight
review of ab5f2a9, 2026-10-12 night. Every route below was called live on 2026-10-12 (research r6 / r7).

PHASE 4 — config.BUSINESS_SERIES: the usage / customer metric each business is judged on, where no adapter already
stored it. Routes:
  overview    api.llama.fi/overview/<type>?excludeTotalDataChartBreakdown=false — one call for every protocol: the
              daily breakdown keyed by DISPLAY NAME. The listed names are summed; each must appear in the response's
              protocols[] (a renamed protocol stores nothing). A name counts 0 before its first day (not launched);
              a later day without it is dropped, never read as 0.
  protocol    api.llama.fi/protocol/<slug>: field tvl (top-level tvl[]), borrowed (chainTvls 'borrowed', else the sum
              of '<chain>-borrowed'), chain:<Chain> (chainTvls[<Chain>].tvl). The response must name the protocol.
  growthepie  api.growthepie.com/v1/export/<export>.json: every chain's daily value, summed over the chains not
              excluded (a chain counts 0 before its first day; a later gap drops the day).
  geodnet     websiteapi.geodnet.com/api/front/getMinerStatistics data.total_miner — today's snapshot only.
  ccip        api.ccip.chain.link/v2/messages (mainnet; newest first, a cursor, no date filter): messages counted per
              UTC day of sendTimestamp; a day is stored only once the walk has passed its start (complete days).
Each stores a dashboard / first-party label in its source (config METRICS[...]['source_kind'], with the on-chain
replacement beside it).

PHASE 3 — config.MARKET_SHARE: share = own ÷ category, per day:
  overview     own (names or a parentProtocol, within the category) ÷ every protocol filed under the category
  peers        own slug(s) ÷ (own + each listed peer) — a peer counts 0 before its first day; a day a launched peer
               did not report is DROPPED (a missing peer is never 0: it would inflate the share)
  chain_tvl    api.llama.fi/v2/historicalChainTvl/<Chain> ÷ api.llama.fi/v2/historicalChainTvl
  stablecoins  stablecoins.llama.fi/stablecoincharts/all?stablecoin=<id> (each own id) ÷ .../all (peggedUSD)
Stored: market_share_pct and the denominator (market_share_category_usd).

The latest day of an aggregator chart is often partial (Maple's fees read 0 for yesterday on 2026-10-12): only days
before YESTERDAY are stored; the next read restates the last ones. First read: config.TRAJECTORY history_days (455)
of days; later reads 30. Once a day per series (DailyChecks).
"""
from __future__ import annotations

import logging
from collections import defaultdict

import pandas as pd

import config
from .base import Http, tidy, today

log = logging.getLogger("token_metrics.fetch.business")

SOURCE = "business"
TIER = 1
API = "https://api.llama.fi"
STABLES = "https://stablecoins.llama.fi"
GROWTHEPIE = "https://api.growthepie.com/v1/export/{export}.json"
GEODNET = "https://websiteapi.geodnet.com/api/front/getMinerStatistics"
CCIP = "https://api.ccip.chain.link/v2/messages"
CCIP_PAGE = 1000
CCIP_MAX_PAGES_FIRST = 700          # ~271 days were reachable on 2026-10-12 (the API answered 500 beyond)
CCIP_MAX_PAGES = 60


def _day(t) -> pd.Timestamp:
    """A UTC day from unix seconds (int or digit string) or an ISO timestamp."""
    if isinstance(t, str) and not t.isdigit():
        ts = pd.Timestamp(t)
        return (ts.tz_convert("UTC").tz_localize(None) if ts.tzinfo is not None else ts).normalize()
    return pd.Timestamp(int(t), unit="s").normalize()


def last_complete() -> pd.Timestamp:
    """The last day stored: the day before yesterday (an aggregator's yesterday can still be partial)."""
    return today().normalize() - pd.Timedelta(days=2)


def strict_sum(series: dict[str, dict]) -> dict:
    """{day: sum} over named series: a series counts 0 before its own first day; a day on which a series that had
    started is missing is dropped."""
    firsts = {k: min(v) for k, v in series.items() if v}
    if len(firsts) < len(series):
        return {}
    days = sorted(set().union(*(set(v) for v in series.values())))
    out = {}
    for d in days:
        tot, ok = 0.0, True
        for k, v in series.items():
            if d in v:
                tot += v[d]
            elif d >= firsts[k]:
                ok = False
                break
        if ok:
            out[d] = tot
    return out


def overview_frame(j: dict) -> tuple[dict, dict]:
    """({name: {day: value}}, {name: protocol meta}) from an overview response."""
    by = defaultdict(dict)
    for t, row in j.get("totalDataChartBreakdown") or []:
        d = _day(t)
        for name, v in (row or {}).items():
            if isinstance(v, dict):                     # a per-chain nesting: sum it
                v = sum(float(x or 0) for x in v.values())
            if v is not None:
                by[name][d] = float(v)
    meta = {p.get("name"): p for p in j.get("protocols") or []}
    return dict(by), meta


def protocol_series(j: dict, field: str) -> dict:
    """{day: value} from an api.llama.fi/protocol response (the last point of a day wins)."""
    ct = j.get("chainTvls") or {}
    if field == "tvl":
        pts = [(p["date"], p.get("totalLiquidityUSD")) for p in j.get("tvl") or []]
    elif field == "borrowed":
        if "borrowed" in ct:
            pts = [(p["date"], p.get("totalLiquidityUSD")) for p in ct["borrowed"].get("tvl") or []]
        else:
            acc = defaultdict(dict)
            for k, v in ct.items():
                if k.endswith("-borrowed"):
                    for p in v.get("tvl") or []:
                        acc[k][_day(p["date"])] = float(p.get("totalLiquidityUSD") or 0)
            return strict_sum(acc) if acc else {}
    elif field.startswith("chain:"):
        pts = [(p["date"], p.get("totalLiquidityUSD")) for p in (ct.get(field[6:]) or {}).get("tvl") or []]
    else:
        raise ValueError(field)
    out = {}
    for t, v in sorted(pts):
        if v is not None:
            out[_day(t)] = float(v)
    return out


class Business:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, daily=None, **_ignored):
        self.http = http or Http(min_interval=0.5, retries=2)
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily
        self._cache: dict = {}

    # ------------------------------------------------------------------ cached reads
    def _json(self, url: str, params: dict | None = None):
        key = (url, tuple(sorted((params or {}).items())))
        if key not in self._cache:
            try:
                self._cache[key] = self.http.get(url, params=params)
            except Exception as e:  # noqa: BLE001 — one failed route must not kill the run
                self._cache[key] = RuntimeError(f"{type(e).__name__}: {str(e)[:140]}")
        v = self._cache[key]
        if isinstance(v, Exception):
            raise v
        return v

    def _overview(self, kind: str):
        return overview_frame(self._json(f"{API}/overview/{kind}", {"excludeTotalDataChart": "false",
                                                                   "excludeTotalDataChartBreakdown": "false"}))

    def _protocol(self, slug: str, expect: str, field: str) -> dict:
        j = self._json(f"{API}/protocol/{slug}")
        got = str((j or {}).get("name") or "")
        if got.lower() != expect.lower():
            raise RuntimeError(f"/protocol/{slug} answered as {got!r}, not {expect!r}")
        s = protocol_series(j, field)
        if not s:
            raise RuntimeError(f"/protocol/{slug}: no {field} series")
        return s

    def _span_start(self, key: str) -> pd.Timestamp:
        n = int(config.TRAJECTORY["history_days"]) if not self.daily.ever(key) else 30
        return today().normalize() - pd.Timedelta(days=n)

    def _keep(self, s: dict, key: str) -> list[tuple]:
        lo, hi = self._span_start(key), last_complete()
        return [(d, v) for d, v in sorted(s.items()) if lo <= d <= hi]

    # ------------------------------------------------------------------ run
    def run(self, projects: list[dict], window_days, out):
        names = {p["name"] for p in projects}
        day = str(today().date())
        for name, specs in config.BUSINESS_SERIES.items():
            if name in names:
                for spec in specs:
                    key = f"business:{name}:{spec['metric']}"
                    if not self.daily.due(key, day):
                        out.skipped(SOURCE, name, f"{spec['metric']}: read today already", TIER)
                        continue
                    try:
                        rows, src, detail = self._series(name, spec, key)
                    except Exception as e:  # noqa: BLE001
                        out.fail(SOURCE, name, f"{spec['metric']}: NOTHING STORED — {e}", TIER)
                        continue
                    if not rows:
                        out.fail(SOURCE, name, f"{spec['metric']}: no complete day in the window ({detail})", TIER)
                        continue
                    out.add(tidy(rows, name, spec["metric"], src, TIER), SOURCE, name,
                            f"{spec['metric']}: {len(rows)} day(s) {rows[0][0].date()}..{rows[-1][0].date()} — "
                            f"{detail}", TIER)
                    self.daily.done(key, day)
        for name, spec in config.MARKET_SHARE.items():
            if name in names and "na" not in spec:
                self._share(name, spec, day, out)

    def _series(self, name: str, spec: dict, key: str) -> tuple[list, str, str]:
        route = spec["route"]
        if route == "overview":
            by, meta = self._overview(spec["type"])
            missing = [n for n in spec["names"] if n not in meta]
            if missing:
                raise RuntimeError(f"overview/{spec['type']} lists no protocol named {missing}")
            s = strict_sum({n: by.get(n, {}) for n in spec["names"]})
            return (self._keep(s, key), f"defillama:overview/{spec['type']}:{'+'.join(spec['names'])}",
                    f"DefiLlama overview/{spec['type']} {', '.join(spec['names'])} (dashboard)")
        if route == "protocol":
            s = strict_sum({slug: self._protocol(slug, exp, spec["field"]) for slug, exp in spec["slugs"]})
            tag = "+".join(x for x, _ in spec["slugs"])
            return (self._keep(s, key), f"defillama:protocol/{tag}[{spec['field']}]",
                    f"DefiLlama /protocol/{tag} {spec['field']} (dashboard)")
        if route == "growthepie":
            rows = self._json(GROWTHEPIE.format(export=spec["export"]))
            per = defaultdict(dict)
            for r in rows or []:
                if r.get("origin_key") not in spec["exclude"] and r.get("value") is not None:
                    per[r["origin_key"]][pd.Timestamp(r["date"]).normalize()] = float(r["value"])
            if not per:
                raise RuntimeError(f"growthepie {spec['export']}: no rows")
            s = strict_sum(per)
            return (self._keep(s, key), f"growthepie:export/{spec['export']}[{len(per)} L2s]",
                    f"growthepie export {spec['export']}, {len(per)} chains summed (dashboard)")
        if route == "geodnet":
            j = self._json(GEODNET)
            v = ((j or {}).get("data") or {}).get("total_miner")
            if not isinstance(v, (int, float)) or v <= 0:
                raise RuntimeError(f"getMinerStatistics: no data.total_miner ({str(j)[:120]})")
            return ([(today().normalize(), float(v))], "geodnet:websiteapi.getMinerStatistics[total_miner]",
                    f"{int(v):,} stations on GEODNET's dashboard today (no history: a snapshot a day)")
        if route == "ccip":
            return self._ccip(key)
        raise ValueError(route)

    def _ccip(self, key: str) -> tuple[list, str, str]:
        lo = self._span_start(key)
        pages = CCIP_MAX_PAGES_FIRST if not self.daily.ever(key) else CCIP_MAX_PAGES
        counts, cursor, oldest, n = defaultdict(int), None, None, 0
        why = "walk reached the window start"
        while n < pages:
            params = {"limit": CCIP_PAGE, "environment": "mainnet"}
            if cursor:
                params["cursor"] = cursor
            try:
                j = self.http.get(CCIP, params=params)
            except Exception as e:  # noqa: BLE001 — keep the complete days already walked
                why = f"walk stopped at page {n + 1}: {type(e).__name__}: {str(e)[:100]}"
                break
            n += 1
            for m in (j or {}).get("data") or []:
                d = _day(m["sendTimestamp"])
                counts[d] += 1
                oldest = d if oldest is None or d < oldest else oldest
            pg = (j or {}).get("pagination") or {}
            cursor = pg.get("cursor")
            if not pg.get("hasNextPage") or not cursor or (oldest is not None and oldest < lo):
                break
        else:
            why = f"page cap {pages} reached"
        if oldest is None:
            raise RuntimeError(f"no message read ({why})")
        # a day is complete once the walk holds a message from before it
        rows = [(d, float(c)) for d, c in sorted(counts.items()) if oldest < d <= today().normalize() - pd.Timedelta(
            days=1) and d >= lo]
        return rows, "ccip_api:v2/messages[mainnet, count by sendTimestamp day]", (
            f"CCIP API, {n} page(s), oldest message {oldest.date()} ({why}); first-party API")

    # ------------------------------------------------------------------ market share
    def _share(self, name: str, spec: dict, day: str, out) -> None:
        key = f"business:{name}:market_share"
        if not self.daily.due(key, day):
            out.skipped(SOURCE, name, "market_share_pct: read today already", TIER)
            return
        try:
            own, total, n_peers = self._share_parts(spec)
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"market_share_pct: NOTHING STORED — {e}", TIER)
            return
        lo, hi = self._span_start(key), last_complete()
        rows = [(d, own[d] / total[d]) for d in sorted(set(own) & set(total)) if lo <= d <= hi and total[d] > 0]
        if not rows:
            out.fail(SOURCE, name, "market_share_pct: no day where the project and every peer reported", TIER)
            return
        tag = f"defillama:{spec['how']}:{spec['category']}[{n_peers}]"
        out.add(tidy(rows, name, "market_share_pct", tag, TIER), SOURCE, name,
                f"market_share_pct: latest {rows[-1][1]:.2%} ({rows[-1][0].date()}) of {spec['category']} — "
                f"{n_peers}; {len(rows)} day(s)", TIER)
        out.add(tidy([(d, total[d]) for d, _ in rows], name, "market_share_category_usd", tag, TIER), SOURCE, name,
                "market_share_category_usd: the denominator", TIER)
        self.daily.done(key, day)

    def _share_parts(self, spec: dict) -> tuple[dict, dict, str]:
        how = spec["how"]
        if how == "overview":
            by, meta = self._overview(spec["type"])
            cat = [n for n, p in meta.items() if p.get("category") == spec["category"]]
            if spec.get("own_names"):
                own_names = list(spec["own_names"])
            else:
                own_names = [n for n in cat if meta[n].get("parentProtocol") == spec["own_parent"]]
            if not own_names or any(n not in cat for n in own_names):
                raise RuntimeError(f"overview/{spec['type']}: own protocols {own_names} not all in {spec['category']}")
            total, own = defaultdict(float), defaultdict(float)
            for n in cat:
                for d, v in (by.get(n) or {}).items():
                    total[d] += v
                    if n in own_names:
                        own[d] += v
            own_s = strict_sum({n: by.get(n, {}) for n in own_names})
            return own_s, dict(total), f"{len(cat)} protocols filed under {spec['category']}"
        if how == "peers":
            own = {s: self._protocol(s, e, spec["field"]) for s, e in spec["own"]}
            peers = {s: self._protocol(s, e, spec["field"]) for s, e in spec["peers"]}
            return strict_sum(own), strict_sum({**own, **peers}), f"{len(own) + len(peers)} protocols (own + peers)"
        if how == "chain_tvl":
            mine = {_day(p["date"]): float(p["tvl"]) for p in self._json(f"{API}/v2/historicalChainTvl/{spec['chain']}")}
            allc = {_day(p["date"]): float(p["tvl"]) for p in self._json(f"{API}/v2/historicalChainTvl")}
            return mine, allc, "all chains DefiLlama tracks"
        if how == "stablecoins":
            def chart(params=None):
                return {_day(p["date"]): float(((p.get("totalCirculatingUSD") or {}).get("peggedUSD")) or 0)
                        for p in self._json(f"{STABLES}/stablecoincharts/all", params)}
            own = strict_sum({str(i): chart({"stablecoin": i}) for i in spec["own_ids"]})
            return own, chart(), "every USD stablecoin DefiLlama tracks"
        raise ValueError(how)
