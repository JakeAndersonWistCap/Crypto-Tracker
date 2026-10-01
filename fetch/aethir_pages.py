"""
fetch/aethir_pages.py — Aethir's own protocol dashboard, read from the page's server-rendered payload.
Jake's probes2 (2026-09-30).

dashboard.aethir.com is a Next.js app: the figures arrive in the HTML itself, inside the React
Server Components payload (`self.__next_f.push([1, "<escaped chunk>"])`), not from a JSON call —
which is why the XHR capture saw 0 JSON responses. On the supply page Jake found

    {"nodes":433704,"locations":94}  and  {"totalComputePower":38509113.66,"totalMonthlyCapacity":638256960}

rsc_objects() unescapes every pushed chunk and returns every flat JSON object with a numeric
field; fields() flattens them to {key: [values]}. A configured key must occur with ONE value on
the page — two different values under the same key store nothing and say so. The other pages
(demand-metric, onchain-metric, overview) are listed by the probe aethir_pages; nothing from them
is stored until a field is named in config (utilisation needs a USED or RENTED capacity field
beside totalMonthlyCapacity, and none is named yet).

DATED SERIES (Jake's probes3, 2026-09-30). The on-chain page carries time series — aiStaked,
gamingStaked, edgeStaked, idcStaked, emitted — as objects that also hold a date. reading() tells
the two shapes apart: a key whose every occurrence sits beside a date is a SERIES and is stored
with its history; a key with one value is a SCALAR stored today; anything else stores nothing.

  fields      key -> metric; scalar or series, as the page serves it
  components  a total and the parts the page shows beside it: the latest parts are summed and the
              difference from the total LOGGED (both dates) — informational, nothing is gated
  flows       a series that is a FLOW (emitted -> emissions_tokens). Its cadence must match the
              declared granularity, and its shape is settled from the page itself: CUMULATIVE when
              it never falls and its last point matches one of the page's own cumulative reward
              totals (baseRewardDistributed, + bonus, + airdrop), PER-PERIOD when its SUM does.
              Neither, or both, stores nothing and logs every figure.
  report      scalars logged each run and never stored (cross-checks, fields not yet understood)

POLITE: robots.txt is checked (fetch.scrape.robots_verdict); one GET per page per day; an honest
User-Agent. The monthly manual supply_units row stays as the fallback (a declared handover).
"""
from __future__ import annotations

import json
import logging
import re

import pandas as pd

from .base import USER_AGENT, point, tidy, today

log = logging.getLogger("token_metrics.fetch.aethir_pages")

SOURCE = "aethir_page"
TIER = 3

_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[\s*1\s*,\s*"((?:[^"\\]|\\.)*)"\s*\]\)', re.S)
_OBJ_RE = re.compile(r"\{[^{}\[\]]{1,4000}\}")


def rsc_text(html: str) -> str:
    """The page's RSC payload, unescaped and concatenated, plus the raw HTML (some payloads are
    inlined as plain JSON)."""
    parts = []
    for chunk in _PUSH_RE.findall(html):
        try:
            parts.append(json.loads(f'"{chunk}"'))
        except ValueError:
            continue
    return "\n".join(parts) + "\n" + html


def rsc_objects(html: str) -> list[dict]:
    """Every flat JSON object in the payload that carries at least one numeric field."""
    out = []
    for m in _OBJ_RE.findall(rsc_text(html)):
        try:
            obj = json.loads(m)
        except ValueError:
            continue
        if isinstance(obj, dict) and any(isinstance(v, (int, float)) and not isinstance(v, bool)
                                         for v in obj.values()):
            out.append(obj)
    return out


def fields(html: str) -> dict:
    """{key: sorted distinct numeric values} across every object on the page."""
    got: dict = {}
    for obj in rsc_objects(html):
        for k, v in obj.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                got.setdefault(k, set()).add(float(v))
    return {k: sorted(v) for k, v in got.items()}


_DATE_WORDS = ("date", "time", "day", "month", "period", "week", "year")
_DATE_KEYS = ("t", "ts", "x", "label", "name", "key", "category")
# STRICT text formats only (Jake's probes3 run: `emitted` read as 17 undated values — its axis is
# in a key or a format the first reader did not know). Each is a whole-string match, so a figure
# never parses as a date by accident.
_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m", "%Y/%m/%d", "%Y/%m", "%b %Y", "%B %Y", "%b %y", "%b '%y",
                 "%m/%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y")


def _as_day(v, numeric_ok: bool = True) -> pd.Timestamp | None:
    """A date as the page serves it — ISO text, a strict month/day format, or UNIX seconds /
    milliseconds (only where the key says it is a time) — as a UTC day."""
    try:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if not numeric_ok or v < 1e9:
                return None
            ts = pd.to_datetime(v, unit="ms" if v > 1e11 else "s", utc=True)
            return ts.tz_localize(None).normalize()
        if not isinstance(v, str):
            return None
        t = v.strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}[T ]", t):
            return pd.to_datetime(t, utc=True).tz_localize(None).normalize()
        for fmt in _DATE_FORMATS:
            try:
                return pd.Timestamp(pd.to_datetime(t, format=fmt)).normalize()
            except (ValueError, TypeError):
                continue
    except (ValueError, OverflowError):
        return None
    return None


def _date_of(obj: dict) -> pd.Timestamp | None:
    """The object's date: a key named like a time (any accepted form), or a common axis key (t, ts,
    x, label, name...) holding a date in a strict form."""
    for k, v in obj.items():
        kl = k.lower()
        if any(w in kl for w in _DATE_WORDS) or kl in _DATE_KEYS:
            d = _as_day(v, numeric_ok=any(w in kl for w in _DATE_WORDS) or kl in ("t", "ts", "x"))
            if d is not None:
                return d
    return None


def reading(html: str, key: str):
    """("scalar", value) | ("series", [(day, value)] oldest first) | the reason nothing is read."""
    objs = [o for o in rsc_objects(html)
            if isinstance(o.get(key), (int, float)) and not isinstance(o.get(key), bool)]
    if not objs:
        return f"`{key}` is not in the server-rendered payload"
    dated = [(_date_of(o), float(o[key])) for o in objs]
    if all(d is not None for d, _ in dated):
        by_day: dict = {}
        for d, v in dated:
            if by_day.setdefault(d, v) != v:
                return f"`{key}` carries two values on {d.date()} ({by_day[d]:,.2f} and {v:,.2f})"
        if len(by_day) > 1:
            return "series", sorted(by_day.items())
    vals = sorted({v for _, v in dated})
    if len(vals) == 1:
        return "scalar", vals[0]
    n_dated = sum(d is not None for d, _ in dated)
    return (f"`{key}` carries {len(vals)} different values ({vals[:5]}), {n_dated} of {len(dated)} "
            f"beside a date — which one is the figure is not established")


def flow_shape(pts: list, totals: dict, agree_within: float) -> tuple[str | None, str]:
    """("cumulative" | "per_period" | None, the evidence) for a dated flow series, from the page's
    own cumulative totals: cumulative when it never falls and its LAST point matches one of them,
    per-period when its SUM does. Neither or both is not settled."""
    vals = [v for _, v in pts]
    last, total = vals[-1], sum(vals)
    rising = all(b >= a for a, b in zip(vals, vals[1:]))
    cum = [n for n, t in totals.items() if t and abs(last / t - 1) <= agree_within]
    per = [n for n, t in totals.items() if t and abs(total / t - 1) <= agree_within]
    ev = (f"last {last:,.0f}, sum {total:,.0f}, never falls: {rising}; page totals "
          + ", ".join(f"{n} {t:,.0f}" for n, t in totals.items()) + f" (±{agree_within:.0%})")
    if cum and rising and not per:
        return "cumulative", f"last point = {cum[0]} — {ev}"
    if per and not (cum and rising):
        return "per_period", f"sum = {per[0]} — {ev}"
    return None, f"not settled — {ev}"


def context(html: str, key: str, width: int = 240, limit: int = 2) -> list[str]:
    """Up to `limit` stretches of the unescaped payload around `"key"` — where a label, tooltip or
    unit sits beside the figure (probe aethir_pages)."""
    text = rsc_text(html)
    out = []
    for m in re.finditer(re.escape(f'"{key}"'), text):
        a, b = max(m.start() - width, 0), m.end() + width
        out.append(re.sub(r"\s+", " ", text[a:b]))
        if len(out) >= limit:
            break
    return out


def labels_near(html: str, key: str, width: int = 600) -> list[str]:
    """Human text near `"key"`: quoted strings with a space and a letter, not code."""
    got = []
    for chunk in context(html, key, width=width, limit=3):
        for s in re.findall(r'"([^"\\]{4,120})"', chunk):
            if " " in s and re.search(r"[A-Za-z]", s) and not re.search(r"[{}<>=;]|^\$|__|\bclass", s):
                if s not in got:
                    got.append(s)
    return got[:12]


class AethirPages:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, get=None, daily=None, **_ignored):
        self._get = get
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily

    def _fetch(self, url: str) -> str:
        if self._get is not None:
            return self._get(url)
        import requests
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=(10, 45))
        r.raise_for_status()
        return r.text

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = p.get("dashboard_pages")
            if spec:
                self._project(p["name"], spec, out)

    def _project(self, name: str, spec: dict, out) -> None:
        from .scrape import robots_verdict
        day = str(today().date())
        read: dict = {}
        for page, m in spec["pages"].items():
            wanted = m.get("fields") or {}
            flows = m.get("flows") or {}
            report = m.get("report") or ()
            if not (wanted or flows or report):
                continue
            url = spec["base"].rstrip("/") + "/" + page.lstrip("/")
            if not self.daily.due(f"aethir_page:{url}", day):
                for metric in list(wanted.values()) + [f["metric"] for f in flows.values()]:
                    out.mark_current(SOURCE, name, metric, f"{metric}: {url} already read today", TIER)
                continue
            ok, why = robots_verdict(url)
            if not ok:
                out.fail(SOURCE, name, f"robots.txt disallows {url} — {why}", TIER)
                continue
            try:
                html = self._fetch(url)
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                out.fail(SOURCE, name, f"{url}: {e}", TIER)
                continue
            self.daily.done(f"aethir_page:{url}", day)
            got = {k: reading(html, k) for k in set(wanted) | set(report) | set(flows)
                   | {t for f in flows.values() for t in f.get("totals", ())}}
            read[page] = got
            for key, metric in wanted.items():
                self._store(name, page, key, metric, got[key], out)
            for key, f in flows.items():
                self._flow(name, page, key, f, got, out)
            for key in report:
                r = got[key]
                out.skipped(SOURCE, name, f"{page} `{key}` = " + (
                    f"{r[1]:,.2f} (scalar)" if isinstance(r, tuple) and r[0] == "scalar" else
                    f"series of {len(r[1])}: {r[1][0][0].date()} {r[1][0][1]:,.2f} .. {r[1][-1][0].date()} "
                    f"{r[1][-1][1]:,.2f}" if isinstance(r, tuple) else r) + " — REPORTED, not stored", TIER)
            comp = m.get("components")
            if comp:
                self._components(name, page, comp, got, out)
        self._cross_checks(name, spec, read, out)

    def _store(self, name: str, page: str, key: str, metric: str, r, out) -> None:
        src = f"{SOURCE}:{page}.{key}"
        if isinstance(r, str):
            out.fail(SOURCE, name, f"{metric}: {r} on {page}. NOTHING STORED"
                                   + ("; the manual row stays the fallback." if metric == "supply_units" else "."), TIER)
            return
        if r[0] == "scalar":
            out.add(point(name, metric, r[1], src, TIER, today()), SOURCE, name,
                    f"{metric} = {page} `{key}` = {r[1]:,.2f} (server-rendered payload)", TIER)
            return
        pts = [(d, v) for d, v in r[1] if d <= today()]
        frame = tidy(pts, name, metric, src, TIER)
        out.add(frame, SOURCE, name, f"{metric} = {page} `{key}` series: {len(frame)} point(s) "
                                     f"{pts[0][0].date()}..{pts[-1][0].date()}, latest {pts[-1][1]:,.2f}", TIER)

    def _flow(self, name: str, page: str, key: str, f: dict, got: dict, out) -> None:
        metric, r = f["metric"], got[key]
        if not (isinstance(r, tuple) and r[0] == "series"):
            out.fail(SOURCE, name, f"{metric}: `{key}` on {page} is not a dated series "
                                   f"({r if isinstance(r, str) else 'one value'}). NOTHING STORED.", TIER)
            return
        pts = [(d, v) for d, v in r[1] if d <= today()]
        gaps = pd.Series([(b[0] - a[0]).days for a, b in zip(pts, pts[1:])])
        cadence = {"monthly": (27, 32), "daily": (1, 1), "weekly": (7, 7)}[f["granularity"]]
        if gaps.empty or not cadence[0] <= float(gaps.median()) <= cadence[1]:
            out.fail(SOURCE, name, f"{metric}: `{key}` declared {f['granularity']}, but its points are a "
                                   f"median {gaps.median() if not gaps.empty else 'n/a'} day(s) apart. "
                                   f"NOTHING STORED.", TIER)
            return
        totals, running = {}, 0.0
        for t in f.get("totals", ()):
            v = got.get(t)
            if not (isinstance(v, tuple) and v[0] == "scalar"):
                break
            running += v[1]
            totals["+".join(f["totals"][:len(totals) + 1])] = running
        shape, ev = flow_shape(pts, totals, float(f["agree_within"]))
        if shape is None:
            out.fail(SOURCE, name, f"{metric}: whether `{key}` is cumulative or per-period is {ev}. "
                                   f"NOTHING STORED; the schedule stays.", TIER)
            return
        if shape == "cumulative":
            rows = [(b[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])]
            tag = f"{SOURCE}:{page}.{key}[cumulative, differenced]"
        else:
            rows, tag = pts, f"{SOURCE}:{page}.{key}[per {f['granularity'][:-2]}]"
        frame = tidy(rows, name, metric, tag, TIER)
        out.add(frame, SOURCE, name, f"{metric} = `{key}` is {shape.upper()} ({ev}); {len(frame)} "
                                     f"{f['granularity']} row(s) {rows[0][0].date()}..{rows[-1][0].date()}, "
                                     f"latest {rows[-1][1]:,.0f} — replaces the schedule's emissions rows", TIER)

    def _components(self, name: str, page: str, comp: dict, got: dict, out) -> None:
        """The page's total beside the latest of its parts, both dated. LOGGED, never gating."""
        def latest(r):
            if isinstance(r, tuple) and r[0] == "scalar":
                return None, r[1]
            if isinstance(r, tuple):
                return r[1][-1]
            return None, None
        td, tv = latest(got.get(comp["total"]))
        parts = {k: latest(got.get(k)) for k in comp["parts"]}
        if tv is None or any(v is None for _, v in parts.values()):
            out.skipped(SOURCE, name, f"{comp['total']} vs its parts on {page}: not every figure was read", TIER)
            return
        s = sum(v for _, v in parts.values())
        out.skipped(SOURCE, name, f"{comp['total']} {tv:,.2f}{f' ({td.date()})' if td is not None else ''} vs "
                                  f"the sum of its parts {s:,.2f} ({s / tv - 1:+.2%}): " + ", ".join(
                                      f"{k} {v:,.2f}{f' ({d.date()})' if d is not None else ''}"
                                      for k, (d, v) in parts.items()), TIER)

    def _cross_checks(self, name: str, spec: dict, read: dict, out) -> None:
        """Declared same-quantity comparisons across pages (idcStaked on two pages), and the page's
        cumulative base reward against the declared Checker Node schedule. Logged only."""
        for chk in spec.get("cross_checks") or ():
            vals = []
            for page, key in chk["pair"]:
                r = (read.get(page) or {}).get(key)
                vals.append(r[1] if isinstance(r, tuple) and r[0] == "scalar" else
                            r[1][-1][1] if isinstance(r, tuple) else None)
            if None not in vals and vals[1]:
                out.skipped(SOURCE, name, f"{chk['what']}: " + " vs ".join(
                    f"{p} `{k}` {v:,.2f}" for (p, k), v in zip(chk["pair"], vals))
                    + f" ({vals[0] / vals[1] - 1:+.2%})", TIER)
        sc = spec.get("schedule_check")
        r = (read.get(sc["page"]) or {}).get(sc["key"]) if sc else None
        if isinstance(r, tuple) and r[0] == "scalar":
            days = (today() - pd.Timestamp(sc["from"])).days + 1
            want = days * float(sc["tokens_per_day"])
            out.skipped(SOURCE, name, f"{sc['key']} {r[1]:,.0f} vs the declared schedule's cumulative "
                                      f"{want:,.0f} ({days} days from {sc['from']}): {r[1] / want - 1:+.2%}", TIER)
