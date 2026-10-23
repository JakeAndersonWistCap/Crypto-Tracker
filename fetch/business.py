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

import json
import logging
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd

import config
from .base import Http, LogEntry, tidy, today

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
# G10 5a (Jake's G9 run: the business tier timed out at 900 s, the CCIP walk re-paging 618-643 calls): EACH SOURCE HAS
# ITS OWN BUDGET (seconds, cumulative over the run) — a source past it fails its remaining series, named, and the others
# run; the CCIP walk goes LAST so it can never starve the rest. Sum 780 s, inside the tier's 900 s backstop.
SOURCE_BUDGET_S = {"defillama": 300, "share": 180, "growthepie": 60, "geodnet": 30, "ccip": 210}
_ROUTE_SOURCE = {"overview": "defillama", "protocol": "defillama", "growthepie": "growthepie", "geodnet": "geodnet",
                 "ccip": "ccip"}


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


def resolve_ids(meta: dict, ids: dict) -> tuple[dict, list]:
    """({declared name: live name} present, ["declared name (id N)" absent]) — DefiLlama children matched by their
    numeric id (G9 A2: "Aero Lite" was renamed "Aero" on DefiLlama; an id survives a rename, a name does not)."""
    by_id = {str(p.get("defillamaId") or p.get("id")): n for n, p in meta.items()}
    present, absent = {}, []
    for i, declared in ids.items():
        live = by_id.get(str(i))
        if live is None:
            absent.append(f"{declared} (id {i})")
        else:
            present[declared] = live
    return present, absent


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
    _deadline = None                 # defaults for an instance built without __init__ (tests)
    ccip_root = None

    def __init__(self, http: Http | None = None, daily=None, **_ignored):
        self.http = http or Http(min_interval=0.5, retries=2)
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily
        self._cache: dict = {}
        self._spent: dict = defaultdict(float)       # G10 5a: seconds used per source this run
        self.ccip_root = _ignored.get("ccip_root")    # tests: where ccip_value's state lives
        self._deadline: float | None = None

    def _dl(self) -> dict:
        """{"deadline": …} for an Http that takes one (fetch.base.Http); {} for a client without it."""
        import inspect                                    # noqa: PLC0415
        if self._deadline is None:
            return {}
        try:
            ps = inspect.signature(self.http.get).parameters
        except (TypeError, ValueError):
            return {}
        ok = "deadline" in ps or any(p.kind is p.VAR_KEYWORD for p in ps.values())
        return {"deadline": self._deadline} if ok else {}

    def _budget(self, src: str):
        """A context for one call of `src`: the Http deadline is what is left of the source's budget; the time taken is
        added to it. Raises BudgetExhausted-like RuntimeError before starting when nothing is left."""
        outer = self

        if "_spent" not in outer.__dict__:
            outer._spent = defaultdict(float)

        class _B:
            def __enter__(self_):
                left = SOURCE_BUDGET_S.get(src, 120) - outer._spent[src]
                if left <= 0:
                    raise RuntimeError(f"time budget exhausted — source {src} used its {SOURCE_BUDGET_S.get(src, 120)} s "
                                       f"this run (fetch.business.SOURCE_BUDGET_S); the next run reads it")
                self_.t0, outer._deadline = time.monotonic(), time.monotonic() + left
                return self_

            def __exit__(self_, *exc):
                outer._spent[src] += time.monotonic() - self_.t0
                outer._deadline = None
                return False
        return _B()

    # ------------------------------------------------------------------ cached reads
    def _json(self, url: str, params: dict | None = None):
        key = (url, tuple(sorted((params or {}).items())))
        if key not in self._cache:
            try:
                self._cache[key] = self.http.get(url, params=params, **self._dl())
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
        # a series checked against a since-launch reference keeps everything served (Round G 2.4)
        _src, name, metric = key.split(":", 2)
        if (name, metric) in config.FULL_HISTORY_SERIES:
            return pd.Timestamp("2000-01-01")
        n = int(config.TRAJECTORY["history_days"]) if not self.daily.ever(key) else 30
        return today().normalize() - pd.Timedelta(days=n)

    def _keep(self, s: dict, key: str) -> list[tuple]:
        lo, hi = self._span_start(key), last_complete()
        return [(d, v) for d, v in sorted(s.items()) if lo <= d <= hi]

    # ------------------------------------------------------------------ run
    def run(self, projects: list[dict], window_days, out):
        names = {p["name"] for p in projects}
        day = str(today().date())
        # the CCIP walk LAST (G10 5a): it is the one source that can run long
        work = [(name, spec) for name, specs in config.BUSINESS_SERIES.items() if name in names for spec in specs]
        for name, spec in [w for w in work if w[1].get("route") != "ccip"]:
            self._one(name, spec, day, out)
        for name, spec in config.MARKET_SHARE.items():
            # G17-5: "rwa_turn" is stored by the weekly RWA turn (fetch/llama.DefiLlama.rwa_share), not here
            if name in names and "na" not in spec and spec.get("how") != "rwa_turn":
                self._share(name, spec, day, out)
        # a SECOND basis beside the first (Jake's decision 5 on 3500042: chain fees vs 12 peer chains for Ethereum
        # and NEAR, beside the chain-TVL share)
        for name, spec in config.MARKET_SHARE_2.items():
            if name in names:
                self._share(name, spec, day, out, metric="market_share_2_pct",
                            denominator="market_share_2_category_usd")
        for name, spec in [w for w in work if w[1].get("route") == "ccip"]:
            self._one(name, spec, day, out)

    def _one(self, name: str, spec: dict, day: str, out) -> None:
        """One business series: its daily mark, its source's budget (G10 5a), store, mark."""
        key = f"business:{name}:{spec['metric']}"
        # A FULL-HISTORY SERIES HAS ITS OWN DAILY MARK (Round G3 item 4): NEAR Intents' since-launch read
        # shared the windowed read's mark, so after one windowed read that day the full history never ran
        # (Σ from 2025-07-15 only: CHECK −3.68%, against −2.28% over the whole history)
        dkey = key + (":full-history" if (name, spec["metric"]) in config.FULL_HISTORY_SERIES else "")
        if not self.daily.due(dkey, day):
            out.mark_current(SOURCE, name, spec["metric"], f"{spec['metric']}: read today already", TIER)
            return
        try:
            with self._budget(_ROUTE_SOURCE.get(spec.get("route"), str(spec.get("route")))):
                rows, src, detail = self._series(name, spec, key)
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"{spec['metric']}: NOTHING STORED — {e}", TIER)
            return
        if not rows:
            out.fail(SOURCE, name, f"{spec['metric']}: no complete day in the window ({detail})", TIER)
            return
        out.add(tidy(rows, name, spec["metric"], src, TIER), SOURCE, name,
                f"{spec['metric']}: {len(rows)} day(s) {rows[0][0].date()}..{rows[-1][0].date()} — "
                f"{detail}", TIER)
        # MARKED ONLY WHEN THE NEWEST COMPLETE DAY IS IN (Round G5 item 4: the 01:48 run read DefiLlama
        # before it had published 10-18, stored rows to 10-17 and marked all 40 series "read today"; the
        # 06:30 run then skipped every one — 13 series "marked read today with no row today"). A read that
        # stops short is stored and NOT marked, so the next run reads again.
        if pd.Timestamp(rows[-1][0]).normalize() >= pd.Timestamp(last_complete()).normalize():
            self.daily.done(dkey, day)
        else:
            out.log.append(LogEntry(SOURCE, name, 0, "ok",
                                    f"{spec['metric']}: newest day served {rows[-1][0].date()}, before "
                                    f"{pd.Timestamp(last_complete()).date()} — NOT marked read today; the "
                                    f"next run reads it again", TIER))

    def _series(self, name: str, spec: dict, key: str) -> tuple[list, str, str]:
        route = spec["route"]
        if route == "overview":
            by, meta = self._overview(spec["type"])
            src = f"defillama:overview/{spec['type']}:{'+'.join(spec['names'])}"     # the declared names: the basis
            if spec.get("ids"):
                # G9 A2: by DefiLlama id; the live names quoted; an absent child named and the sum PARTIAL
                present, absent = resolve_ids(meta, spec["ids"])
                if not present:
                    raise RuntimeError(f"overview/{spec['type']} lists none of {absent}")
                s = strict_sum({d: by.get(live, {}) for d, live in present.items()})
                live = ", ".join(v if v == d else f"{v} (was {d})" for d, v in present.items())
                if absent:
                    src = config.mark_source(src, "PARTIAL") + f"[absent from overview/{spec['type']}: {', '.join(absent)}]"
                return (self._keep(s, key), src, f"DefiLlama overview/{spec['type']} {live} (dashboard)"
                        + (f"; PARTIAL — absent: {', '.join(absent)}" if absent else ""))
            missing = [n for n in spec["names"] if n not in meta]
            if missing:
                raise RuntimeError(f"overview/{spec['type']} lists no protocol named {missing}")
            s = strict_sum({n: by.get(n, {}) for n in spec["names"]})
            return (self._keep(s, key), src, f"DefiLlama overview/{spec['type']} {', '.join(spec['names'])} (dashboard)")
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

    def _ccip_value_days(self) -> dict:
        """{day: messages} from ccip_value's walk state (G10 5a): every CLOSED day it walked holds n_msgs — every
        message sent that UTC day, the same count by sendTimestamp day this series stores. Gap days are left out."""
        from .ccip_value import DEFAULTS as _CV, ROOT as _ROOT        # noqa: PLC0415
        spec = next((p.get("ccip_value") for p in config.PROJECTS if p.get("ccip_value")), None) or {}
        path = Path(spec.get("state_path") or _CV["state_path"])
        path = path if path.is_absolute() else Path(self.ccip_root or _ROOT) / path
        try:
            days = json.loads(path.read_text(encoding="utf-8")).get("days") or {}
        except (OSError, ValueError):
            return {}
        return {pd.Timestamp(d).normalize(): float(r["n_msgs"]) for d, r in days.items()
                if isinstance(r, dict) and r.get("stage") not in (None, "gap") and r.get("n_msgs")}

    def _ccip(self, key: str) -> tuple[list, str, str]:
        """Messages per UTC day. THE DAYS ccip_value HAS ALREADY WALKED ARE READ FROM ITS STATE (G10 5a: the business tier
        timed out at 900 s re-paging 618-643 calls ccip_value had already made); the API is walked only for the days
        newer than its newest closed day, inside the ccip source's budget (Http deadline)."""
        lo, top = self._span_start(key), today().normalize() - pd.Timedelta(days=1)
        held = {d: n for d, n in self._ccip_value_days().items() if lo <= d <= top}
        need = (max(held) + pd.Timedelta(days=1)) if held else lo
        need = max(need, lo)
        counts, cursor, oldest, n = defaultdict(int), None, None, 0
        why = f"{len(held)} day(s) from ccip_value's walk state"
        if need <= top:
            pages = CCIP_MAX_PAGES_FIRST if not self.daily.ever(key) and not held else CCIP_MAX_PAGES
            why = "walk reached the first day not held"
            while n < pages:
                params = {"limit": CCIP_PAGE, "environment": "mainnet"}
                if cursor:
                    params["cursor"] = cursor
                try:
                    j = self.http.get(CCIP, params=params, **self._dl())
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
                if not pg.get("hasNextPage") or not cursor or (oldest is not None and oldest < need):
                    break
            else:
                why = f"page cap {pages} reached"
            why = f"{why}; {len(held)} day(s) from ccip_value's walk state"
        if oldest is None and not held:
            raise RuntimeError(f"no message read ({why})")
        # a walked day is complete once the walk holds a message from before it
        walked = {d: float(c) for d, c in counts.items() if oldest is not None and oldest < d <= top and d >= need}
        rows = sorted({**held, **walked}.items())
        return rows, "ccip_api:v2/messages[mainnet, count by sendTimestamp day]", (
            f"CCIP API, {n} page(s) walked" + (f", oldest message {oldest.date()}" if oldest is not None else "")
            + f" ({why}); first-party API")

    # ------------------------------------------------------------------ market share
    def _share(self, name: str, spec: dict, day: str, out, metric: str = "market_share_pct",
               denominator: str = "market_share_category_usd") -> None:
        key = f"business:{name}:{metric.replace('_pct', '')}"
        if not self.daily.due(key, day):
            out.mark_current(SOURCE, name, metric, f"{metric}: read today already", TIER)
            out.mark_current(SOURCE, name, denominator, f"{denominator}: read today already", TIER)
            return
        self._share_absent = []
        try:
            with self._budget("share"):
                own, total, n_peers = self._share_parts(spec)
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"{metric}: NOTHING STORED — {e}", TIER)
            return
        lo, hi = self._span_start(key), last_complete()
        rows = [(d, own[d] / total[d]) for d in sorted(set(own) & set(total)) if lo <= d <= hi and total[d] > 0]
        if not rows:
            out.fail(SOURCE, name, f"{metric}: no day where the project and every peer reported", TIER)
            return
        tag = f"defillama:{spec['how']}:{spec['category']}[{n_peers}]"
        if self._share_absent:              # G9 A2: a declared own child DefiLlama no longer lists — named, PARTIAL
            tag = config.mark_source(tag, "PARTIAL") + f"[own child absent: {', '.join(self._share_absent)}]"
        out.add(tidy(rows, name, metric, tag, TIER), SOURCE, name,
                f"{metric}: latest {rows[-1][1]:.2%} ({rows[-1][0].date()}) of {spec['category']} — "
                f"{n_peers}; {len(rows)} day(s)", TIER)
        out.add(tidy([(d, total[d]) for d, _ in rows], name, denominator, tag, TIER), SOURCE, name,
                f"{denominator}: the denominator", TIER)
        self.daily.done(key, day)

    def _chain_fees(self, slug: str, expect: str) -> dict:
        """{day: fees} for one chain from api.llama.fi/summary/fees/<chain>?dataType=dailyFees."""
        j = self._json(f"{API}/summary/fees/{slug}", {"dataType": "dailyFees", "excludeTotalDataChartBreakdown": "true"})
        got = str((j or {}).get("name") or "")
        if got.lower() != expect.lower():
            raise RuntimeError(f"/summary/fees/{slug} answered as {got!r}, not {expect!r}")
        s = {_day(t): float(v) for t, v in (j.get("totalDataChart") or []) if v is not None}
        if not s:
            raise RuntimeError(f"/summary/fees/{slug}: no daily fees")
        return s

    def _share_parts(self, spec: dict) -> tuple[dict, dict, str]:
        how = spec["how"]
        if how == "overview":
            by, meta = self._overview(spec["type"])
            cat = [n for n, p in meta.items() if p.get("category") == spec["category"]]
            if spec.get("own_ids"):
                present, self._share_absent = resolve_ids(meta, spec["own_ids"])
                own_names = list(present.values())
            elif spec.get("own_names"):
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
            read = (self._chain_fees if spec.get("route") == "chain_fees"
                    else (lambda s, e: self._protocol(s, e, spec["field"])))
            own = {s: read(s, e) for s, e in spec["own"]}
            peers = {s: read(s, e) for s, e in spec["peers"]}
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
