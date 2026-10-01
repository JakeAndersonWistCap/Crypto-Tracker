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

  fields      key -> metric: the tile's CURRENT figure (one undated value) dated today, plus the
              chart's dated points before today as history (current_and_series) — unless the key
              is `current_only` (totalStaked: locked_tokens has a declared wrapper -> dashboard
              handover, and chart history would overlap the wrapper leg)
  arrays      a dated list under its own key (emissionStakeRewardSchedule — the Staking Rewards
              Emission Schedule, cumulative, weekly, DD-MM-YYYY, published past today: only dates
              up to today are stored, as staker_rewards_emitted)
  components  a total and its parts' CURRENT figures: the sum is LOGGED against the total, and a
              part not read is named, never counted as zero
  report      figures logged each run and never stored

LABELLED FIGURES (Jake's PDFs of the rendered pages, 2026-10-01). The payload carries keys, not
labels, so each labelled figure is matched to its key BY VALUE (resolve_scalar / resolve_series):
the one current figure within a declared distance of the value Jake read, or the one chart with the
declared cadence, value range and check against a resolved figure. Ambiguous or absent: nothing is
stored and every candidate is named; the Run Log names each match so the key can be pinned.
DERIVED (config `derived`): same-run sums and ratios — the checker-node and edge reward
cumulatives that supplier emissions are differenced from at read time, and the utilisation
labelled "derived: assumes every container available 24/7".

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
# DD-MM-YYYY (Jake's probes4, 2026-10-01): emissionStakeRewardSchedule dates read "06-08-2026" ..
# "26-11-2026" — day first, settled by the 26. MM-DD-YYYY is deliberately NOT accepted beside it,
# so no string can parse both ways.
_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m", "%Y/%m/%d", "%Y/%m", "%b %Y", "%B %Y", "%b %y", "%b '%y",
                 "%m/%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d-%m-%Y")


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


def current_and_series(html: str, key: str):
    """(current, series, why) for `key` on a page that serves it BOTH ways — a headline tile (one
    undated value: the CURRENT figure) and a chart (dated values: its HISTORY). Jake's probes4
    (2026-10-01): aiStaked is 416,297,029 on the tile and 400.2M at the chart's last month-start;
    reading() saw the mix as ambiguous, and the probe's sum silently dropped idcStaked. Either part
    may be None; `why` says what was not read."""
    objs = [o for o in rsc_objects(html)
            if isinstance(o.get(key), (int, float)) and not isinstance(o.get(key), bool)]
    if not objs:
        return None, None, f"`{key}` is not in the server-rendered payload"
    undated = sorted({float(o[key]) for o in objs if _date_of(o) is None})
    by_day: dict = {}
    why = []
    for o in objs:
        d = _date_of(o)
        if d is None:
            continue
        if by_day.setdefault(d, float(o[key])) != float(o[key]):
            why.append(f"`{key}` carries two values on {d.date()} ({by_day[d]:,.2f} and {float(o[key]):,.2f})")
            by_day = {}
            break
    if len(undated) > 1:
        why.append(f"`{key}` carries {len(undated)} different values ({undated[:5]}) beside no date — "
                   f"which one is the figure is not established")
    current = undated[0] if len(undated) == 1 else None
    series = sorted(by_day.items()) if len(by_day) > 1 else None
    return current, series, "; ".join(why)


def array_objects(html: str, key: str):
    """The list served under `"key":[...]` in the payload (emissionStakeRewardSchedule,
    stakeHistory), or why not. Bracket-matched over the unescaped payload, strings respected."""
    text = rsc_text(html)
    m = re.search(re.escape(f'"{key}"') + r"\s*:\s*\[", text)
    if not m:
        return f"`{key}` is not in the server-rendered payload"
    i, depth, in_str, esc = m.end() - 1, 0, False, False
    for j in range(i, len(text)):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                try:
                    got = json.loads(text[i:j + 1])
                except ValueError as e:
                    return f"`{key}`: the array does not parse ({e})"
                return [o for o in got if isinstance(o, dict)]
    return f"`{key}`: the array is not closed in the payload"


def _cadence_ok(pts: list, granularity: str) -> bool:
    gaps = pd.Series([(b[0] - a[0]).days for a, b in zip(pts, pts[1:])])
    lo, hi = {"monthly": (27, 32), "daily": (1, 1), "weekly": (7, 7)}[granularity]
    return not gaps.empty and lo <= float(gaps.median()) <= hi


def resolve_scalar(spec: dict, pages: dict):
    """(page, key, value) for a LABELLED figure, or why not. Jake read the labels and their values
    off the rendered pages (PDFs, 2026-10-01); the payload carries keys, not labels. A pinned
    `key` is read directly. Otherwise the key is found BY VALUE: the one current figure on the
    named pages within `within` of the label's value as read (a percentage may be served as 12.49
    or 0.1249). Exactly one candidate, or nothing is stored and every candidate is named."""
    cands = []
    for page in spec["pages"]:
        html = pages.get(page)
        if html is None:
            continue
        keys = [spec["key"]] if spec.get("key") else sorted(fields(html))
        for k in keys:
            cur, _, _ = current_and_series(html, k)
            if cur is None:
                continue
            for scale in ((1.0, 100.0) if spec.get("pct") else (1.0,)):
                v = cur / scale
                if spec.get("key") or abs(v / spec["anchor"] - 1) <= spec["within"]:
                    cands.append((page, k, v))
                    break
    if len(cands) == 1:
        return cands[0]
    if not cands:
        return (f"no current figure within {spec['within']:.0%} of {spec['anchor']:,} on "
                f"{', '.join(spec['pages'])}")
    return "ambiguous — " + ", ".join(f"{p} `{k}` {v:,.4f}" for p, k, v in cands)


def resolve_series(spec: dict, pages: dict, resolved: dict):
    """(page, key, points) for a LABELLED chart, or why not: the one dated series on the named
    pages with the declared cadence, every value inside `value_range`, its key matching `name_re`
    (when given), and — where declared — its sum since a date or its latest point agreeing with a
    labelled figure already resolved. Exactly one, or nothing is stored."""
    cands, notes = [], []
    for page in spec["pages"]:
        html = pages.get(page)
        if html is None:
            continue
        for k in sorted(fields(html)):
            if spec.get("name_re") and not re.search(spec["name_re"], k):
                continue
            if spec.get("not_name_re") and re.search(spec["not_name_re"], k):
                continue
            _, pts, _ = current_and_series(html, k)
            if not pts or not _cadence_ok(pts, spec["granularity"]):
                continue
            lo, hi = spec["value_range"]
            if not all(lo <= v <= hi for _, v in pts):
                continue
            if spec.get("first_on_or_after") and pts[0][0] < pd.Timestamp(spec["first_on_or_after"]):
                continue
            ok = True
            for chk, how in (("sum_check", "sum"), ("latest_check", "latest")):
                c = spec.get(chk)
                if not c:
                    continue
                ref = resolved.get(c["against"])
                if ref is None:
                    notes.append(f"`{k}` not checked: {c['against']} was not resolved")
                    ok = False
                    break
                got = (sum(v for d, v in pts if d >= pd.Timestamp(c["since"])) if how == "sum"
                       else pts[-1][1])
                notes.append(f"`{k}` {how} {got:,.0f} vs {c['against']} {ref[2]:,.0f} ({got / ref[2] - 1:+.1%})")
                if abs(got / ref[2] - 1) > c["within"]:
                    ok = False
            if ok:
                cands.append((page, k, pts))
    if len(cands) == 1:
        return cands[0]
    detail = f" ({'; '.join(notes)})" if notes else ""
    if not cands:
        return f"no {spec['granularity']} series on {', '.join(spec['pages'])} fits{detail}"
    return "ambiguous — " + ", ".join(f"{p} `{k}`" for p, k, _ in cands) + detail


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

    def _read_page(self, name: str, spec: dict, page: str, day: str, out):
        """The page's HTML, "DONE" when it was already read today, or None."""
        from .scrape import robots_verdict
        url = spec["base"].rstrip("/") + "/" + page.lstrip("/")
        if not self.daily.due(f"aethir_page:{url}", day):
            return "DONE"
        ok, why = robots_verdict(url)
        if not ok:
            out.fail(SOURCE, name, f"robots.txt disallows {url} — {why}", TIER)
            return None
        try:
            html = self._fetch(url)
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            out.fail(SOURCE, name, f"{url}: {e}", TIER)
            return None
        self.daily.done(f"aethir_page:{url}", day)
        return html

    def _project(self, name: str, spec: dict, out) -> None:
        day = str(today().date())
        pages: dict = {}
        for page, m in spec["pages"].items():
            html = self._read_page(name, spec, page, day, out)
            if html == "DONE":
                for metric in (m.get("fields") or {}).values():
                    out.mark_current(SOURCE, name, metric, f"{metric}: {page} already read today", TIER)
                continue
            if html is None:
                continue
            pages[page] = html
            for key, metric in (m.get("fields") or {}).items():
                self._store(name, page, key, metric, html, out,
                            history=key not in (m.get("current_only") or ()))
            for key, a in (m.get("arrays") or {}).items():
                self._array(name, page, key, a, html, out)
            for key in m.get("report") or ():
                cur, pts, why = current_and_series(html, key)
                out.skipped(SOURCE, name, f"{page} `{key}` = " + (
                    f"{cur:,.2f}" if cur is not None else "no current figure")
                    + (f"; chart of {len(pts)}: {pts[0][0].date()} {pts[0][1]:,.2f} .. {pts[-1][0].date()} "
                       f"{pts[-1][1]:,.2f}" if pts else "") + (f"; {why}" if why else "")
                    + " — REPORTED, not stored", TIER)
            comp = m.get("components")
            if comp:
                self._components(name, page, comp, html, out)
        resolved = self._labelled(name, spec, pages, out)
        self._derived(name, spec, pages, resolved, out)
        self._cross_checks(name, spec, pages, out)

    def _store(self, name: str, page: str, key: str, metric: str, html: str, out,
               history: bool = True) -> None:
        """The tile's CURRENT figure dated today, and — unless the field is current-only — the chart's
        dated points before today as its history. Same source: one measuring point."""
        src = f"{SOURCE}:{page}.{key}"
        cur, pts, why = current_and_series(html, key)
        rows = [(d, v) for d, v in (pts or []) if d < today()] if history else []
        if cur is not None:
            rows.append((today(), cur))
        if not rows:
            out.fail(SOURCE, name, f"{metric}: {why or f'`{key}` has no current figure'} on {page}. NOTHING STORED"
                                   + ("; the manual row stays the fallback." if metric == "supply_units" else "."), TIER)
            return
        frame = tidy(rows, name, metric, src, TIER)
        out.add(frame, SOURCE, name, f"{metric} = {page} `{key}`: "
                                     + (f"current {cur:,.2f}" if cur is not None else "no current figure")
                                     + (f"; history {len(rows) - (cur is not None)} point(s) "
                                        f"{rows[0][0].date()}..{rows[-2 if cur is not None else -1][0].date()}"
                                        if len(rows) > (cur is not None) else "")
                                     + (f" ({why})" if why else ""), TIER)

    def _array(self, name: str, page: str, key: str, a: dict, html: str, out) -> None:
        """A dated list under its own key (emissionStakeRewardSchedule). Kept: dates up to today
        (the published schedule runs past it); a cumulative that falls is refused."""
        objs = array_objects(html, key)
        if isinstance(objs, str):
            out.fail(SOURCE, name, f"{a['metric']}: {objs} on {page}. NOTHING STORED.", TIER)
            return
        pts = sorted((d, float(o[a["value_key"]])) for o in objs
                     if (d := _date_of(o)) is not None and isinstance(o.get(a["value_key"]), (int, float)))
        if len(pts) < 2 or not _cadence_ok(pts, a["granularity"]):
            out.fail(SOURCE, name, f"{a['metric']}: `{key}` on {page} is not a {a['granularity']} dated list "
                                   f"({len(pts)} dated point(s) of {len(objs)}). NOTHING STORED.", TIER)
            return
        if a.get("cumulative") and any(b[1] < x[1] for x, b in zip(pts, pts[1:])):
            out.fail(SOURCE, name, f"{a['metric']}: `{key}` falls somewhere — not the cumulative it is "
                                   f"declared to be. NOTHING STORED.", TIER)
            return
        past = [(d, v) for d, v in pts if d <= today()]
        if not past:
            out.skipped(SOURCE, name, f"{a['metric']}: every point of `{key}` is after today", TIER)
            return
        frame = tidy(past, name, a["metric"], f"{SOURCE}:{page}.{key}.{a['value_key']}", TIER)
        out.add(frame, SOURCE, name, f"{a['metric']} = `{key}` ({a['label']}): {len(past)} point(s) "
                                     f"{past[0][0].date()}..{past[-1][0].date()}, latest {past[-1][1]:,.0f}; "
                                     f"{len(pts) - len(past)} published point(s) after today not stored "
                                     f"(last {pts[-1][0].date()} {pts[-1][1]:,.0f})", TIER)

    def _components(self, name: str, page: str, comp: dict, html: str, out) -> None:
        """The tile total beside the sum of its parts' CURRENT figures. LOGGED, never gating; a part
        that was not read is named — never counted as zero (Jake's probes4: the probe's sum
        dropped idcStaked and reported -47.69%)."""
        tv, _, _ = current_and_series(html, comp["total"])
        parts = {k: current_and_series(html, k)[0] for k in comp["parts"]}
        missing = [k for k, v in parts.items() if v is None] + ([comp["total"]] if tv is None else [])
        if missing:
            out.skipped(SOURCE, name, f"{comp['total']} vs its parts on {page}: no current figure for "
                                      f"{', '.join(missing)} — no sum reported", TIER)
            return
        s = sum(parts.values())
        out.skipped(SOURCE, name, f"{comp['total']} {tv:,.2f} vs the sum of its parts {s:,.2f} "
                                  f"({s / tv - 1:+.2%}): " + ", ".join(f"{k} {v:,.2f}" for k, v in parts.items()), TIER)

    def _labelled(self, name: str, spec: dict, pages: dict, out) -> dict:
        """Figures Jake read off the rendered pages by LABEL (2026-10-01), matched to payload keys —
        scalars first, then charts (a chart's check may cite a scalar). {id: (page, key, value)}."""
        resolved: dict = {}
        for fid, f in (spec.get("labelled") or {}).items():
            if not any(pg in pages for pg in f["pages"]):
                continue
            if f.get("granularity"):
                continue
            r = resolve_scalar(f, pages)
            if isinstance(r, str):
                out.fail(SOURCE, name, f"\"{f['label']}\": {r}. NOTHING STORED.", TIER)
                continue
            resolved[fid] = r
            page, key, v = r
            how = "pinned" if f.get("key") else f"matched by value to {f['anchor']:,} as read {f['read_on']}"
            if f.get("metric"):
                out.add(point(name, f["metric"], v, f"{SOURCE}:{page}.{key}", TIER, today()), SOURCE, name,
                        f"{f['metric']} = \"{f['label']}\" = {page} `{key}` {v:,.4f} ({how})", TIER)
            else:
                out.skipped(SOURCE, name, f"\"{f['label']}\" = {page} `{key}` {v:,.4f} ({how}) — REPORTED", TIER)
        for fid, f in (spec.get("labelled") or {}).items():
            if not f.get("granularity") or not any(pg in pages for pg in f["pages"]):
                continue
            r = resolve_series(f, pages, resolved)
            if isinstance(r, str):
                out.fail(SOURCE, name, f"\"{f['label']}\" ({f['granularity']} chart): {r}. NOTHING STORED.", TIER)
                continue
            page, key, pts = r
            rows = [(d, v) for d, v in pts if d <= today()]
            frame = tidy(rows, name, f["metric"], f"{SOURCE}:{page}.{key}", TIER)
            out.add(frame, SOURCE, name, f"{f['metric']} = \"{f['label']}\" = {page} `{key}`: {len(rows)} "
                                         f"{f['granularity']} point(s) {rows[0][0].date()}..{rows[-1][0].date()}, "
                                         f"latest {rows[-1][1]:,.2f}", TIER)
            resolved[fid] = (page, key, rows[-1][1])
        return resolved

    def _derived(self, name: str, spec: dict, pages: dict, resolved: dict, out) -> None:
        """Sums and ratios of figures read this run, each from same-run readings only (config
        `derived`). A missing input stores nothing and names it."""
        def val(ref):
            if ref in resolved:
                return resolved[ref][2]
            if "#" not in ref:
                return None
            page, key = ref.split("#")
            return current_and_series(pages[page], key)[0] if page in pages else None
        for d in spec.get("derived") or ():
            vals = {ref: val(ref) for ref in d["inputs"]}
            missing = [ref for ref, v in vals.items() if v is None]
            if missing:
                if any(r in resolved or r.split("#")[0] in pages for r in d["inputs"]):
                    out.fail(SOURCE, name, f"{d.get('metric') or d['what']}: {', '.join(missing)} not read this "
                                           f"run. NOTHING STORED.", TIER)
                continue
            v = list(vals.values())
            got = (sum(v) if d["op"] == "sum" else v[0] - sum(v[1:]) if d["op"] == "minus"
                   else v[0] / (v[1] * d.get("per", 1)))
            text = f"{d['what']} = {got:,.4f} (" + ", ".join(f"{r} {x:,.2f}" for r, x in vals.items()) + ")"
            if d.get("compare"):
                ref = val(d["compare"]["against"])
                if ref is not None:
                    text += f"; vs {d['compare']['label']} {ref:,.4f} ({got / ref - 1:+.2%})"
            if d.get("metric"):
                out.add(point(name, d["metric"], got, d["source"], TIER, today()), SOURCE, name,
                        f"{d['metric']}: {text}" + (f" — {d['label']}" if d.get("label") else ""), TIER)
            else:
                out.skipped(SOURCE, name, text + " — LOGGED", TIER)

    def _cross_checks(self, name: str, spec: dict, pages: dict, out) -> None:
        """Declared same-quantity comparisons across pages (idcStaked on two pages), and the page's
        cumulative base reward against the declared Checker Node schedule. Logged only."""
        for chk in spec.get("cross_checks") or ():
            vals = [current_and_series(pages[pg], key)[0] if pg in pages else None for pg, key in chk["pair"]]
            if None not in vals and vals[1]:
                out.skipped(SOURCE, name, f"{chk['what']}: " + " vs ".join(
                    f"{p} `{k}` {v:,.2f}" for (p, k), v in zip(chk["pair"], vals))
                    + f" ({vals[0] / vals[1] - 1:+.2%})", TIER)
        sc = spec.get("schedule_check")
        v = current_and_series(pages[sc["page"]], sc["key"])[0] if sc and sc["page"] in pages else None
        if v is not None:
            days = (today() - pd.Timestamp(sc["from"])).days + 1
            want = days * float(sc["tokens_per_day"])
            out.skipped(SOURCE, name, f"{sc['key']} {v:,.0f} vs the declared schedule's cumulative "
                                      f"{want:,.0f} ({days} days from {sc['from']}): {v / want - 1:+.2%}", TIER)
