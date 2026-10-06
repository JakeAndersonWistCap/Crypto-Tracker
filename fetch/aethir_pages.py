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
PINNED BY KEY NAME (Jake's probes5, 2026-10-01): the value matcher found no ARR — "arr" sits in an
object that also holds arrays, which the flat-object reader cannot see — but the keys are in the
payload. A pinned scalar is read anywhere in the payload (key_scalar) and its anchor becomes a logged
cross-check. weeklyNetworkRevenue (DD/MM, no year: yearless_days infers it from the sequence ending at
the current week), monthlyNetworkRevenue ("August, 2024") and stakeHistory (ISO startTime, one metric
per component) are `arrays`; only finished periods are stored; series_checks log the monthly sum
against Total Network Revenue and weekly against monthly. Weekly compute hours and stake durations
are client-loaded, not in the payload (config UNAVAILABLE).
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

import config

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
                 "%m/%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d-%m-%Y",
                 # monthlyNetworkRevenue (Jake's probes5, 2026-10-01): {"month": "August, 2024", ...}
                 "%B, %Y", "%b, %Y")


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


def resolve_array_key(html: str, key: str):
    """The full key for a pin ending in "*" (a PREFIX: Jake's probes8 saw "weeklyComputeHo..." truncated
    in the paste) — the ONE key with that prefix serving a list — or the key itself; else why not."""
    if not key.endswith("*"):
        return key
    names = sorted(set(re.findall(r'"(' + re.escape(key[:-1]) + r'[A-Za-z0-9_]*)"\s*:\s*\[', rsc_text(html))))
    if len(names) != 1:
        return (f"{len(names)} list key(s) start with `{key[:-1]}`" + (f": {names}" if names else "")
                + " — pin the full key")
    return names[0]


def auto_keys(objs: list, a: dict):
    """(date_key, value_key) — the declared ones, or for "auto" the ONE key that fits (a DD/MM label for
    a yearless date; the one other numeric key for the value) — else why not, naming the keys."""
    first = next((o for o in objs if isinstance(o, dict)), {})
    dk, vk = a.get("date_key"), a.get("value_key")
    if dk == "auto":
        c = [k for k, v in first.items() if isinstance(v, str) and (
            re.match(r"^\d{1,2}/\d{1,2}$", v.strip()) if a.get("yearless") else _as_day(v) is not None)]
        if len(c) != 1:
            return None, None, f"no single date key among {sorted(first)}"
        dk = c[0]
    if vk == "auto":
        c = [k for k, v in first.items() if k != dk and isinstance(v, (int, float)) and not isinstance(v, bool)]
        if len(c) != 1:
            return None, None, f"no single value key among {sorted(first)} — pin value_key"
        vk = c[0]
    return dk, vk, None


def payload_health(html: str) -> tuple[int, int, int]:
    """(bytes, numeric keys, RSC chunks) — enough to tell a page that carries its data from a challenge,
    loading shell or error page served with HTTP 200 (Jake's run 2026-10-05: every field on onchain-metric
    'no current figure', the APRs, revenue, purchases, ecosystem and edge figures all NOTHING STORED)."""
    text = rsc_text(html)
    numeric = set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:\s*-?\d', text))
    return len(html or ""), len(numeric), len(_PUSH_RE.findall(html or ""))


def key_diagnosis(html: str, key: str) -> str:
    """Why a pinned key is missing: served as a string, or renamed (the payload's closest keys)."""
    import difflib
    text = rsc_text(html)
    if re.search(re.escape(f'"{key}"') + r'\s*:\s*"', text):
        return f"`{key}` is served as a STRING, not a number"
    if re.search(re.escape(f'"{key}"') + r'\s*:\s*(null|\{|\[)', text):
        return f"`{key}` is present but null / an object / a list"
    keys = sorted(set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:', text)))
    # PRESENT, IN A FORM NONE OF THE READERS TAKES (Jake's run 2026-10-06: "`aiStaked` is not in the payload —
    # keys like it: ['aiStaked', ...]"). Say it is present, quote what follows it, and give the page's health,
    # so the next run shows whether it is a partial load or a parse change.
    if key in keys:
        nums = sorted({m.group(1) for m in re.finditer(re.escape(f'"{key}"') + r"\s*:\s*(-?[\d.eE+]+)", text)})
        snips = [text[m.end():m.end() + 40].replace("\n", " ")
                 for m in list(re.finditer(re.escape(f'"{key}"') + r"\s*:", text))[:3]]
        size, numeric, chunks = payload_health(html)
        return (f"`{key}` IS in the payload ({len(snips)} occurrence(s) shown"
                + (f"; numbers {nums[:4]}" if nums else "") + f"; followed by {snips}) but no reader takes it"
                + (f" — {len(nums)} different values, so which is current is not established" if len(nums) > 1 else
                   " — inside a dated chart point only, or a form the readers do not parse")
                + f"; page {size:,} bytes, {numeric} numeric keys, {chunks} RSC chunks")
    close = difflib.get_close_matches(key, keys, n=4, cutoff=0.6)
    return (f"`{key}` is not in the payload" + (f" — keys like it: {close} (renamed?)" if close else
                                                f" ({len(keys)} keys on the page, none like it)"))


def key_scalar(html: str, key: str):
    """(value, why) for `"key": <number>` ANYWHERE in the payload — inside nested objects and beside
    arrays, where rsc_objects() (flat objects only) cannot see it. Jake's probes5 (2026-10-01): the
    value matcher found no ARR because "arr": 62489999.90221721 sits in an object that also holds
    the revenue arrays. One distinct value, or nothing."""
    text = rsc_text(html)
    vals = sorted({float(m.group(1)) for m in re.finditer(
        re.escape(f'"{key}"') + r"\s*:\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\b", text)})
    if not vals:
        return None, f"`{key}` is not in the server-rendered payload as a number"
    if len(vals) > 1:
        return None, f"`{key}` carries {len(vals)} different values ({vals[:5]})"
    return vals[0], ""


def yearless_days(labels: list, granularity: str, asof: pd.Timestamp | None = None):
    """[day] for labels served as DD/MM with NO YEAR (weeklyNetworkRevenue "08/06"), or why not.
    The year is INFERRED FROM THE SEQUENCE, which ends at the current week (Jake's probes5): the last
    label gets the latest year that does not put it after today; walking back, the year drops by one
    whenever a label would not fall before the one after it. Accepted only if every step is exactly
    the declared cadence and the last point is within two weeks of today — otherwise nothing."""
    asof = (asof or today()).normalize()
    try:
        dm = [tuple(int(x) for x in str(t).strip().split("/")) for t in labels]
        if not dm or any(len(x) != 2 or not (1 <= x[0] <= 31 and 1 <= x[1] <= 12) for x in dm):
            raise ValueError
    except ValueError:
        return f"labels are not DD/MM ({list(labels)[:3]})"
    step = {"weekly": 7, "daily": 1}.get(granularity)
    if step is None:
        return f"yearless labels are read only for weekly/daily series, not {granularity}"
    for order in (1, -1):                                   # as served, else newest-first
        seq = dm[::order]
        try:
            d, m = seq[-1]
            year = asof.year if pd.Timestamp(asof.year, m, d) <= asof else asof.year - 1
            days = [pd.Timestamp(year, m, d)]
            for d, m in reversed(seq[:-1]):
                cand = pd.Timestamp(days[0].year, m, d)
                if cand >= days[0]:
                    cand = pd.Timestamp(days[0].year - 1, m, d)
                days.insert(0, cand)
        except ValueError:                                  # 29/02 in a year without one
            continue
        if all((b - a).days == step for a, b in zip(days, days[1:])) and (asof - days[-1]).days < 14:
            return days[::order]
    return (f"no year assignment makes {len(dm)} DD/MM labels {step} days apart and ending within two "
            f"weeks of {asof.date()} (first {labels[0]}, last {labels[-1]})")


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
            if cur is None and spec.get("key"):
                cur, _ = key_scalar(html, k)            # PINNED: anywhere in the payload
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
        read = [pages[p] for p in spec["pages"] if pages.get(p) is not None]
        if spec.get("key"):
            return (f"pinned `{spec['key']}` is not in the payload of {', '.join(spec['pages'])}"
                    + (f" — {key_diagnosis(read[0], spec['key'])}" if read else " (page not read)"))
        # the nearest current figures, so a drifted anchor reads apart from a page without data
        near = []
        for html in read:
            for k, vals in fields(html).items():
                for v in vals:
                    for scale in ((1.0, 100.0) if spec.get("pct") else (1.0,)):
                        if v and spec["anchor"]:
                            near.append((abs(v / scale / spec["anchor"] - 1), k, v / scale))
        near.sort()
        return (f"no current figure within {spec['within']:.0%} of {spec['anchor']:,} on "
                f"{', '.join(spec['pages'])}" + (" (page not read)" if not read else
                f" — nearest: {', '.join(f'`{k}` {v:,.4f} ({d:+.1%})' for d, k, v in near[:3])}" if near else
                " — the page carries no numeric figure"))
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
        self._series: dict = {}
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
        # A PAGE WITHOUT ITS DATA IS NOT A READ PAGE (Jake's run 2026-10-05): a challenge, loading shell or
        # error page served with HTTP 200 made every field on it fail one by one — and was marked read for
        # the day, so a same-day rerun skipped it. Now it fails ONCE, says what came back, and is retried.
        size, numeric, chunks = payload_health(html)
        if numeric < int(spec.get("min_numeric_keys", 3)):
            out.fail(SOURCE, name, f"{url}: served WITHOUT ITS DATA ({size:,} bytes, {numeric} numeric key(s), "
                                   f"{chunks} RSC chunk(s)) — a challenge / loading shell / error page, or a payload "
                                   f"that changed shape. Nothing read from it; NOT marked read, so the next run "
                                   f"(same day included) tries again.", TIER)
            return None
        log.info("aethir_page %s: %s bytes, %d numeric keys, %d RSC chunks", page, f"{size:,}", numeric, chunks)
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
                if cur is None and not pts:
                    cur, why = key_scalar(html, key)
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
        self._series_checks(name, spec, resolved, out)
        self._series_totals(name, spec, out)
        self._apr_series(name, spec, pages, out)

    def _series_totals(self, name: str, spec: dict, out) -> None:
        """A cumulative figure DERIVED as the sum of a pinned series read this run (Jake's pins 2026-10-06:
        Total Network Revenue is not in the payload; the monthly list from Aug 2024 is). What the list
        lacks is named on the cell (PARTIAL)."""
        for t in spec.get("series_totals") or ():
            pts = self._series.get(t["series"])
            if not pts:
                out.fail(SOURCE, name, f"{t['metric']}: `{t['series']}` not read this run. NOTHING STORED.", TIER)
                continue
            total = float(sum(v for _d, v in pts))
            src = (config.mark_source(t["source"], "PARTIAL") + f"[{t['missing']}]") if t.get("missing") else t["source"]
            out.add(point(name, t["metric"], total, src, TIER, today()), SOURCE, name,
                    f"{t['metric']} = sum of `{t['series']}` over {len(pts)} point(s) {pts[0][0].date()}.."
                    f"{pts[-1][0].date()} = {total:,.2f}" + (f" — PARTIAL: {t['missing']}" if t.get("missing") else ""),
                    TIER)

    def _apr_series(self, name: str, spec: dict, pages: dict, out) -> None:
        """WHICH STATISTIC OF A DAILY APR SERIES IS THE PRINTED FIGURE (Jake, 2026-10-06): the onchain-metric
        `ai` / `gaming` series (daily APR) against the labelled average Jake read on a date. Each candidate —
        the point on that date, the mean of the series to that date, and its trailing 7/30/90-day means — is
        computed AS OF THE READING DATE and compared with the reading. Exactly one within `tol`: that statistic
        is pinned and stored today. None or several: nothing is stored, and every candidate is named."""
        for fid, a in (spec.get("apr_series") or {}).items():
            html = pages.get(a["page"])
            if html is None:
                continue
            _cur, pts, why = current_and_series(html, a["key"])
            if not pts:
                out.fail(SOURCE, name, f"{a['metric']}: `{a['key']}` on {a['page']} is not a dated series "
                                       f"({why or 'no points'}). NOTHING STORED.", TIER)
                continue
            s = pd.Series({d: v for d, v in pts}).sort_index()

            def stats(at):
                h = s[s.index <= at]
                if h.empty:
                    return {}
                out_ = {"point": float(h.iloc[-1]), "mean_all": float(h.mean())}
                for n in (7, 30, 90):
                    w = h[h.index > at - pd.Timedelta(days=n)]
                    if len(w) >= max(3, n // 2):
                        out_[f"mean_{n}d"] = float(w.mean())
                return out_
            d0 = pd.Timestamp(a["read_on"])
            then = stats(d0)
            ok = [k for k, v in then.items() if abs(v / a["anchor"] - 1) <= a.get("tol", 0.01)]
            listing = ", ".join(f"{k} {v:.4f} ({v / a['anchor'] - 1:+.1%})" for k, v in then.items())
            if len(ok) != 1:
                out.fail(SOURCE, name, f"{a['metric']}: \"{a['label']}\" read {a['anchor']} on {a['read_on']}; "
                                       f"`{a['key']}` as of that date gives {listing or 'no points by then'} — "
                                       + ("NONE" if not ok else f"{len(ok)} ({', '.join(ok)})")
                                       + f" within {a.get('tol', 0.01):.0%}. Left UNPINNED; NOTHING STORED.", TIER)
                continue
            now = stats(s.index[-1])
            if ok[0] not in now:
                out.fail(SOURCE, name, f"{a['metric']}: pinned statistic {ok[0]} cannot be computed today", TIER)
                continue
            v = now[ok[0]]
            out.add(point(name, a["metric"], v, f"{SOURCE}:{a['page']}.{a['key']}[{ok[0]}]", TIER, today()),
                    SOURCE, name, f"{a['metric']} = {ok[0]} of `{a['key']}` = {v:.4f} as of {s.index[-1].date()} — "
                                  f"PINNED: on {a['read_on']} it gave {then[ok[0]]:.4f} against the printed "
                                  f"{a['anchor']} (candidates: {listing})", TIER)

    def _store(self, name: str, page: str, key: str, metric: str, html: str, out,
               history: bool = True) -> None:
        """The tile's CURRENT figure dated today, and — unless the field is current-only — the chart's
        dated points before today as its history. Same source: one measuring point."""
        src = f"{SOURCE}:{page}.{key}"
        cur, pts, why = current_and_series(html, key)
        if cur is None and not pts:
            # A KEY BESIDE AN ARRAY (Jake's probes5: "arr"; the supply page's totalRewards sits with
            # weeklyData) is invisible to the flat-object reader: read by key name anywhere, ONE value.
            cur, why2 = key_scalar(html, key)
            why = why2 if cur is None else ""
        rows = [(d, v) for d, v in (pts or []) if d < today()] if history else []
        if cur is not None:
            rows.append((today(), cur))
        if not rows:
            why = f"{why or f'`{key}` has no current figure'}; {key_diagnosis(html, key)}"
            out.fail(SOURCE, name, f"{metric}: {why} on {page}. NOTHING STORED"
                                   + ("; the manual row stays the fallback." if metric == "supply_units" else "."), TIER)
            return
        frame = tidy(rows, name, metric, src, TIER)
        out.add(frame, SOURCE, name, f"{metric} = {page} `{key}`: "
                                     + (f"current {cur:,.2f}" if cur is not None else "no current figure")
                                     + (f"; history {len(rows) - (cur is not None)} point(s) "
                                        f"{rows[0][0].date()}..{rows[-2 if cur is not None else -1][0].date()}"
                                        if len(rows) > (cur is not None) else "")
                                     + (f" ({why})" if why else ""), TIER)

    def _array_points(self, objs: list, a: dict, value_key: str):
        """[(day, value, period_end)] from a dated list, or why not. The date is `date_key` when
        declared (ISO, a strict format, or DD/MM with no year — inferred from the sequence), else
        whatever date the object carries (_date_of)."""
        gran = a.get("granularity") or a.get("cadence")
        rows = [o for o in objs if isinstance(o.get(value_key), (int, float)) and not isinstance(o.get(value_key), bool)]
        if not rows:
            return f"no object carries a numeric `{value_key}`"
        dk = a.get("date_key")
        if dk and a.get("yearless"):
            days = yearless_days([o.get(dk) for o in rows], gran)
            if isinstance(days, str):
                return days
        elif dk:
            days = [_as_day(o.get(dk)) for o in rows]
            if any(d is None for d in days):
                return f"`{dk}` does not parse as a date on every object ({[o.get(dk) for o in rows][:3]})"
        else:
            days = [_date_of(o) for o in rows]
        ek = a.get("end_key")
        pts = sorted((d, float(o[value_key]), _as_day(o.get(ek)) if ek else None)
                     for d, o in zip(days, rows) if d is not None)
        if len(pts) < 2 or not _cadence_ok([(d, v) for d, v, _ in pts], gran):
            return f"not a {gran} dated list ({len(pts)} dated point(s) of {len(objs)})"
        if len({d for d, _, _ in pts}) != len(pts):
            return "two objects share a date"
        return pts

    def _period_done(self, a: dict, d: pd.Timestamp, end) -> bool:
        """A FINISHED period only, when `complete_only`: the week/month in progress (or one whose
        served end is after today) is a partial figure, reported, never stored."""
        if not a.get("complete_only"):
            return d <= today()
        if end is not None:
            return end <= today()
        gran = a.get("granularity") or a.get("cadence")
        nxt = d + (pd.Timedelta(days=7) if gran == "weekly" else pd.DateOffset(months=1) if gran == "monthly"
                   else pd.Timedelta(days=1))
        return nxt <= today()

    def _array(self, name: str, page: str, key: str, a: dict, html: str, out) -> None:
        """A dated list under its own key — pinned BY KEY NAME (Jake's probes5): the staker reward
        schedule, weeklyNetworkRevenue (DD/MM, year inferred), monthlyNetworkRevenue ("August, 2024"),
        stakeHistory (ISO startTime; one metric per component key). Kept: dates up to today, or only
        finished periods (`complete_only`); a cumulative that falls is refused."""
        pinned = key
        key = resolve_array_key(html, key)
        if pinned != key and not key.startswith(pinned[:-1]):     # a prefix that did not resolve
            for m in (a.get("values") or {"": a["metric"]}).values():
                out.fail(SOURCE, name, f"{m}: {key} on {page}. NOTHING STORED.", TIER)
            return
        objs = array_objects(html, key)
        if not isinstance(objs, str) and "auto" in (a.get("date_key"), a.get("value_key")):
            dk, vk, why = auto_keys(objs, a)
            if why:
                out.fail(SOURCE, name, f"{a.get('metric')}: `{key}` on {page}: {why}. NOTHING STORED.", TIER)
                return
            a = {**a, "date_key": dk, "value_key": vk}
            out.skipped(SOURCE, name, f"{a.get('metric')}: `{key}` resolved — full key `{key}`, date `{dk}`, "
                                      f"value `{vk}` (pin them in config)", TIER)
        values = a.get("values") or {a["value_key"]: a["metric"]}
        if isinstance(objs, str):
            for m in values.values():
                out.fail(SOURCE, name, f"{m}: {objs} on {page}. NOTHING STORED.", TIER)
            return
        for vk, metric in values.items():
            pts = self._array_points(objs, a, vk)
            if isinstance(pts, str):
                if a.get("values") and pts.startswith("no object carries"):
                    out.skipped(SOURCE, name, f"{metric}: `{key}` on {page} {pts} — that component has no "
                                              f"history there; its current figure is read from the tile", TIER)
                else:
                    out.fail(SOURCE, name, f"{metric}: `{key}` on {page}: {pts}. NOTHING STORED.", TIER)
                continue
            self._series[key + ("." + vk if a.get("values") else "")] = [(d, v) for d, v, _ in pts]
            if a.get("cumulative") and any(b[1] < x[1] for x, b in zip(pts, pts[1:])):
                out.fail(SOURCE, name, f"{metric}: `{key}` falls somewhere — not the cumulative it is "
                                       f"declared to be. NOTHING STORED.", TIER)
                continue
            keep = [(d, v) for d, v, e in pts if self._period_done(a, d, e)]
            later = [(d, v) for d, v, e in pts if not self._period_done(a, d, e)]
            if not keep:
                out.skipped(SOURCE, name, f"{metric}: no finished/past point in `{key}`", TIER)
                continue
            src = (f"{SOURCE}:{page}.{vk}" if a.get("source") == "field"
                   else f"{SOURCE}:{page}.{key}.{vk}")
            out.add(tidy(keep, name, metric, src, TIER), SOURCE, name,
                    f"{metric} = `{key}`.{vk} ({a['label']}): {len(keep)} point(s) "
                    f"{keep[0][0].date()}..{keep[-1][0].date()}, latest {keep[-1][1]:,.2f}"
                    + (f"; {len(later)} point(s) not stored ({'in progress' if a.get('complete_only') else 'after today'}"
                       f": {later[0][0].date()} {later[0][1]:,.2f}"
                       + (f" .. {later[-1][0].date()}" if len(later) > 1 else "") + ")" if later else ""), TIER)

    def _series_checks(self, name: str, spec: dict, resolved: dict, out) -> None:
        """Cross-checks between the pinned revenue series and the cumulative total (logged only):
        the monthly sum against "Total Network Revenue (Since June 2024)", and weekly against monthly
        where they overlap (each week split pro rata by day across months)."""
        for c in spec.get("series_checks") or ():
            if c["kind"] == "sum_vs":
                pts = self._series.get(c["series"])
                ref = resolved.get(c["against"])
                tot, how = ((ref[2], f"{ref[0]} `{ref[1]}` today") if ref else
                            (c["anchor"], f"as read {c['read_on']}"))
                if not pts:
                    continue
                got = sum(v for _, v in pts)
                out.skipped(SOURCE, name, f"{c['what']}: {got:,.0f} over {len(pts)} month(s) "
                                          f"{pts[0][0].strftime('%b %Y')}..{pts[-1][0].strftime('%b %Y')} vs "
                                          f"{tot:,.0f} ({how}): {got / tot - 1:+.2%}"
                                          + (f" — {c['note']}" if c.get("note") else "") + " — LOGGED", TIER)
            elif c["kind"] == "weekly_vs_monthly":
                wk, mo = self._series.get(c["weekly"]), self._series.get(c["monthly"])
                if not wk or not mo:
                    continue
                spread: dict = {}
                for d, v in wk:
                    for k in range(7):
                        day = d + pd.Timedelta(days=k)
                        spread[day.to_period("M")] = spread.get(day.to_period("M"), 0.0) + v / 7
                first, last = wk[0][0], wk[-1][0] + pd.Timedelta(days=6)
                rows = []
                for d, v in mo:
                    per = d.to_period("M")
                    if per.start_time >= first and per.end_time.normalize() <= last and per in spread:
                        rows.append((per, spread[per], v))
                if not rows:
                    out.skipped(SOURCE, name, f"{c['what']}: no month wholly inside the weekly series", TIER)
                    continue
                w, m = sum(r[1] for r in rows), sum(r[2] for r in rows)
                worst = max(rows, key=lambda r: abs(r[1] / r[2] - 1) if r[2] else 0)
                out.skipped(SOURCE, name, f"{c['what']}: {len(rows)} overlapping month(s) "
                                          f"{rows[0][0]}..{rows[-1][0]}: weekly {w:,.0f} vs monthly {m:,.0f} "
                                          f"({w / m - 1:+.2%}); widest {worst[0]} {worst[1]:,.0f} vs "
                                          f"{worst[2]:,.0f} ({worst[1] / worst[2] - 1:+.2%}) — LOGGED", TIER)

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
            how = (f"pinned by key; cross-check vs {f['anchor']:,} as read {f['read_on']}: {v / f['anchor'] - 1:+.1%}"
                   + ("" if abs(v / f["anchor"] - 1) <= f["within"] else f" — OUTSIDE ±{f['within']:.0%}, CHECK")
                   if f.get("key") else f"matched by value to {f['anchor']:,} as read {f['read_on']}")
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
            if page not in pages:
                return None
            cur, pts, _ = current_and_series(pages[page], key)
            if cur is None and not pts:
                cur, _ = key_scalar(pages[page], key)      # beside an array: read by key name anywhere
            return cur
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
