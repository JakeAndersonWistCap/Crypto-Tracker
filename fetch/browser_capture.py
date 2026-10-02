"""
fetch/browser_capture.py — figures a dashboard draws in the BROWSER, read by rendering it. Jake, 2026-10-02.

Some charts are not in the server-rendered payload (Aethir's weekly compute hours and stake durations) or come
from a site with no API (ASXN's Hyperliquid dashboard). This renders the page headless (Chromium, the scrape
tier's), clicks the timeframe tabs it is told to, and records every JSON response the page fetched for itself
and every websocket frame. Two ways to read a figure, pinned per page in config (`browser_capture`):

  series   a dated list inside a captured JSON response: the response whose URL contains `url_contains`,
           the list at `path` (dot path; "" = the body), each item's `date_key` and `value_key`. History.
  tiles    a figure printed beside its `label` in the rendered text — the fallback when the chart's data
           arrives in a form that cannot be captured. Today only: FORWARD-ONLY.

EVERY RESPONSE, NOT ONLY JSON (probes6, 2026-10-02): Aethir's charts arrived as 0 JSON and 0 websocket
frames, so text bodies are kept too — text/x-component (Next.js React Server Components / server actions),
text/plain, application/octet-stream — and an RSC body's `id:{json}` lines are parsed into the captured
JSON as `<url>#rsc:<id>`, so a pin can read them like any response. Static assets are skipped.

PINS can name `date_key: "auto"` / `value_key: "auto"` / `path: "auto"` — taken only when exactly one
candidate fits, else NOTHING is stored and the candidates are named. `units: "from_check"` takes the page's
`unit_check` verdict (HYPE or USD, decided against stored figures; ambiguous = nothing stored); USD legs
are stored as tokens at the stored same-day price. `crosscheck` logs agreement with a stored series.
A tile's `label` may be a tuple of alternatives.

NOTHING RUNS until the page is `permitted: True` in config — set once robots.txt (checked again at every run)
and the site's terms have been read (probe browser_captures prints both). Nothing is stored for a page whose
`series` / `tiles` are not pinned: the probe prints every captured response's URL, keys and list shapes so
they can be. One render per page per day. Each series says which layer it measures (`layer`, e.g. HyperCore
or HyperEVM) in its source.
"""
from __future__ import annotations

import json
import logging
import re

import pandas as pd

from .base import USER_AGENT, point, tidy, today

log = logging.getLogger("token_metrics.fetch.browser_capture")

SOURCE = "browser_capture"
TIER = 3
_NUM = re.compile(r"[-+]?\$?\s*\d[\d,]*(?:\.\d+)?\s*[KMB%]?", re.I)


_SKIP_TYPES = ("image/", "font/", "text/css", "javascript", "video/", "audio/", "text/html")


def parse_rsc(text: str) -> list:
    """[(id, obj)] from a React Server Components body: lines `id:<json>` (an `I`/`HL`/`T` prefix or
    non-JSON payload is skipped)."""
    out = []
    for line in str(text or "").splitlines():
        k, sep, rest = line.partition(":")
        if not sep or not k or len(k) > 8 or not rest[:1] in "[{":
            continue
        try:
            out.append((k, json.loads(rest)))
        except ValueError:
            continue
    return out


def capture(url: str, clicks=(), wait_ms: int = 4000, timeout_ms: int = 45000, browser=None, toggles=()) -> dict:
    """{"responses": [(url, json)], "bodies": [(url, content-type, text)], "ws": [(url, text)],
    "text": rendered text, "clicked": [labels], "texts": {toggle: rendered text after clicking it}}."""
    from playwright.sync_api import sync_playwright
    out = {"responses": [], "bodies": [], "ws": [], "text": "", "clicked": [], "texts": {}}
    with sync_playwright() as pw:
        b = browser
        if b is None:
            try:
                b = pw.chromium.launch(headless=True)
            except Exception:  # noqa: BLE001 — the image's explicit Chromium
                b = pw.chromium.launch(headless=True, executable_path="/opt/pw-browsers/chromium")
        page = b.new_page(user_agent=USER_AGENT)

        def on_response(r):
            ct = (r.headers.get("content-type") or "").lower()
            try:
                if "json" in ct:
                    out["responses"].append((r.url, r.json()))
                elif not any(t in ct for t in _SKIP_TYPES) and r.request.resource_type in ("fetch", "xhr", "other"):
                    body = r.text()[:2_000_000]
                    out["bodies"].append((r.url, ct or "(none)", body))
                    for k, obj in parse_rsc(body):
                        out["responses"].append((f"{r.url}#rsc:{k}", obj))
            except Exception:  # noqa: BLE001 — a body that cannot be read (redirect, aborted)
                pass

        def on_ws(ws):
            ws.on("framereceived", lambda payload: out["ws"].append((ws.url, str(payload)[:20000])))
        page.on("response", on_response)
        page.on("websocket", on_ws)
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        page.wait_for_timeout(wait_ms)
        for label in clicks:
            loc = page.get_by_text(label, exact=True)
            try:
                if loc.count():
                    loc.first.click(timeout=5000)
                    page.wait_for_timeout(wait_ms)
                    out["clicked"].append(label)
            except Exception:  # noqa: BLE001 — a tab that is not there on this page
                continue
        out["text"] = page.inner_text("body")
        # TOGGLES (Aethir's "Average Stake Duration (Days)" tile, AI / Gaming — Jake's probes8): each clicked
        # in turn and the page text read after it
        for label in toggles:
            loc = page.get_by_text(label, exact=True)
            try:
                if loc.count():
                    loc.first.click(timeout=5000)
                    page.wait_for_timeout(wait_ms)
                    out["texts"][label] = page.inner_text("body")
            except Exception:  # noqa: BLE001 — a toggle not on the page: its tiles say so
                continue
        b.close()
    return out


def path_get(obj, path: str):
    for k in [p for p in str(path or "").split(".") if p]:
        if isinstance(obj, list) and k.isdigit():
            obj = obj[int(k)] if int(k) < len(obj) else None
        elif isinstance(obj, dict):
            obj = obj.get(k)
        else:
            return None
    return obj


def _matches(u: str, want) -> bool:
    return any(w in u for w in ((want,) if isinstance(want, str) else want))


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dated_lists(body, depth=0, path=""):
    """[(path, list)] of lists of dicts anywhere in the body (3 levels)."""
    found = []
    if isinstance(body, list) and body and isinstance(body[0], dict):
        found.append((path, body))
    elif isinstance(body, dict) and depth < 3:
        for k, v in body.items():
            found += _dated_lists(v, depth + 1, f"{path}.{k}" if path else k)
    return found


def _auto_keys(arr: list, pin: dict):
    """(date_key, value_key) — the pinned ones, or the single candidate when "auto"; else why not."""
    from .aethir_pages import _as_day
    first = next((it for it in arr if isinstance(it, dict)), {})
    dk, vk = pin.get("date_key", "auto"), pin.get("value_key", "auto")
    if dk == "auto" and pin.get("yearless"):           # Aethir's chart axis: "08/06" .. "21/09"
        cands = [k for k, v in first.items() if isinstance(v, str) and re.match(r"^\d{1,2}/\d{1,2}$", v.strip())]
        if len(cands) != 1:
            return None, None, f"no single DD/MM key among {sorted(first)[:12]}"
        dk = cands[0]
    if dk == "auto":
        cands = [k for k, v in first.items() if _as_day(v) is not None and _num(v) is None or
                 k.lower() in ("date", "day", "time", "timestamp", "ts", "t")]
        if len(cands) != 1:
            return None, None, f"date key not unique among {sorted(first)[:10]}"
        dk = cands[0]
    if vk == "auto":
        cands = [k for k, v in first.items() if k != dk and _num(v) is not None]
        if pin.get("value_key_regex"):                   # probes7: a field named like X, excluding Y
            cands = [k for k in cands if re.search(pin["value_key_regex"], k, re.I)
                     and not (pin.get("value_key_exclude") and re.search(pin["value_key_exclude"], k, re.I))]
        if len(cands) != 1:
            return None, None, (f"value key not unique — candidates {sorted(cands)[:12]} of {sorted(first)[:40]}: "
                                f"pin one")
        vk = cands[0]
    return dk, vk, None


def series_from(captured: list, pin: dict):
    """[(day, value)] from the first response matching the pin, or why not. Sets pin["_keys"] to the
    (path, date_key, value_key) actually read."""
    from .aethir_pages import _as_day
    seen, why = [], []
    for u, body in captured:
        if not _matches(u, pin["url_contains"]):
            continue
        seen.append(u[:100])
        if pin.get("path") == "auto":
            # probes7: a list qualifies only if its date AND value keys resolve (and its path matches
            # path_regex, where given) — several qualifying lists store nothing and are named
            lists = [(p_, a) for p_, a in _dated_lists(body) if _auto_keys(a, pin)[2] is None
                     and (not pin.get("path_regex") or re.search(pin["path_regex"], p_, re.I))]
            if len(lists) != 1:
                why.append(f"{len(lists)} dated list(s) in {u[:80]}" + (f": {[p_ for p_, _ in lists][:5]}" if lists else ""))
                continue
            path, arr = lists[0]
        else:
            path, arr = pin.get("path", ""), path_get(body, pin.get("path", ""))
        if not isinstance(arr, list):
            continue
        dk, vk, err = _auto_keys(arr, pin)
        if err:
            why.append(err)
            continue
        pts = {}
        items = [it for it in arr if isinstance(it, dict) and _num(it.get(vk)) is not None]
        if pin.get("yearless"):
            from .aethir_pages import yearless_days
            days = yearless_days([it.get(dk) for it in items], pin["yearless"])
            if isinstance(days, str):
                why.append(days)
                continue
        else:
            days = [_as_day(it.get(dk)) for it in items]
        for d, it in zip(days, items):
            if d is not None:
                pts[d] = _num(it.get(vk))
        if pts:
            pin["_keys"] = (path, dk, vk)
            return sorted(pts.items())
    return (f"no captured response for {pin['url_contains']!r} held a dated list at {pin.get('path') or '(body)'}"
            + (f" ({'; '.join(why[:3])})" if why else "") + (f" (matching: {', '.join(seen[:3])})" if seen else ""))


def scalar_from(captured: list, pin: dict):
    """(key, value) — the ONE numeric key whose name contains `key_contains` in the responses matching
    the pin (or the pinned `key`), or why not: several candidates store nothing and are named."""
    cands = {}
    for u, body in captured:
        if not _matches(u, pin["url_contains"]):
            continue

        def walk(o, path, depth):
            if isinstance(o, dict) and depth < 4:
                for k, v in o.items():
                    p_ = f"{path}.{k}" if path else k
                    if _num(v) is not None and ((pin.get("key") and pin["key"] in (p_, k)) or
                                                (not pin.get("key") and pin["key_contains"].lower() in k.lower())):
                        cands[p_] = _num(v)
                    walk(v, p_, depth + 1)
        walk(body, "", 0)
    if len(cands) == 1:
        return next(iter(cands.items()))
    return (f"{len(cands)} candidate key(s) for {pin.get('key') or pin.get('key_contains')!r} in responses "
            f"matching {pin['url_contains']!r}" + (f": {sorted(cands)[:8]} — pin one (`key`)" if cands else ""))


def anchor_ok(v: float, pin: dict):
    """None, or why a value is too far from the pin's anchor (a figure Jake read on `read_on`)."""
    a = pin.get("anchor")
    if not a or v is None:
        return None
    if abs(v / a["value"] - 1) > a["within"]:
        return (f"{v:,.2f} is {v / a['value'] - 1:+.0%} from the anchor {a['value']:,.0f} read {a['read_on']} "
                f"(allowed ±{a['within']:.0%}) — the wrong field, or re-anchor")
    return None


def tile_check(pts: list, text: str, chk: dict):
    """None, or why a series disagrees with the page's own summary tile (probes7: "Avg Daily Txns 334K"):
    the tile must sit within ±`within` of the series' mean over the last 7, 30, 90 days or all of it."""
    t = tile_from(text, chk["label"])
    if t is None:
        return f"the check tile \"{chk['label']}\" is not on the page"
    vals = [v for _, v in pts]
    means = {f"{n}d": sum(vals[-n:]) / len(vals[-n:]) for n in (7, 30, 90) if vals} | \
        ({"all": sum(vals) / len(vals)} if vals else {})
    if any(abs(m / t - 1) <= chk.get("within", 0.25) for m in means.values() if t):
        return None
    return (f"no window mean is within ±{chk.get('within', 0.25):.0%} of the tile \"{chk['label']}\" {t:,.0f} "
            f"({', '.join(f'{k} {m:,.0f}' for k, m in means.items())})")


def units_verdict(leg: dict, refs: dict, band=(0.8, 1.25)):
    """('tokens'|'usd', report) — which stored reference the leg agrees with (median of leg/ref on shared
    days inside `band`), or (None, report) when neither or both do."""
    import statistics
    rep, hits = [], []
    for unit, ref in refs.items():
        both = [d for d in leg if d in ref and ref[d]]
        if len(both) < 5:
            rep.append(f"{unit}: {len(both)} shared day(s) — too few")
            continue
        med = statistics.median(leg[d] / ref[d] for d in both)
        rep.append(f"{unit}: median leg/ref {med:.3f} over {len(both)} day(s)")
        if band[0] <= med <= band[1]:
            hits.append(unit)
    return (hits[0] if len(hits) == 1 else None), "; ".join(rep)


def agreement(a: dict, b: dict, tol: float = 0.10) -> str:
    """Shared days, median a/b, share of days within ±tol."""
    import statistics
    both = [d for d in a if d in b and b[d]]
    if not both:
        return "no shared day"
    r = [a[d] / b[d] for d in both]
    within = sum(1 for x in r if abs(x - 1) <= tol) / len(r)
    return (f"{len(both)} shared day(s) {min(both).date()}..{max(both).date()}: median ratio "
            f"{statistics.median(r):.3f}, {within:.0%} within ±{tol:.0%}")


_MONTH = re.compile(r"^\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I)


def tile_read(text: str, label):
    """(value, why): the first number printed after `label` (or the first of its alternatives found), or
    (None, why). A CHART IS NOT A TILE (Jake's probes9: Aethir's "Average Stake Duration (Days)" 'read'
    2,024 — the year on the chart's axis, "2024 Sep 01 2025 Jan 18 ... 0 500 1000 1500"): a number that is
    a bare year (1990-2100 with no separator, decimal or unit), one followed by a month name, or one inside
    a run of evenly spaced numbers (axis ticks) is refused."""
    from .base import parse_number
    for lab in ((label,) if isinstance(label, str) else label):
        i = text.find(lab)
        if i < 0:
            continue
        m = _NUM.search(text, i + len(lab), i + len(lab) + 120)
        if not m:
            return None, f"no number after \"{lab}\""
        tok = m.group(0).strip()
        v = parse_number(tok)
        if v is None:
            return None, f"no number after \"{lab}\""
        after = text[m.end():m.end() + 60]
        if re.fullmatch(r"(19|20)\d\d", tok) or _MONTH.match(after):
            return None, f"\"{lab}\" is followed by {tok!r}{' ' + after[:12].strip() if after.strip() else ''} — a YEAR / date, not a figure (a chart, not a tile)"
        nums = [parse_number(x.group(0)) for x in _NUM.finditer(text, i + len(lab), i + len(lab) + 240)]
        nums = [x for x in nums if x is not None]
        for j in range(len(nums) - 3):
            a, b, c, d = nums[j:j + 4]
            if b - a == c - b == d - c and b != a and v in (a, b, c, d):
                return None, f"\"{lab}\" sits among evenly spaced axis labels ({a:g} {b:g} {c:g} {d:g}) — a chart, not a tile"
        return v, ""
    return None, "label not found"


def tile_from(text: str, label):
    """The first number printed after `label`, or None (refused: see tile_read)."""
    return tile_read(text, label)[0]


def shapes(captured: list) -> list[str]:
    """One line per captured response: URL, top keys, and any list of dicts with its item keys."""
    lines = []
    for u, body in captured:
        found = []

        def walk(o, path, depth):
            if depth > 3:
                return
            if isinstance(o, list) and o and isinstance(o[0], dict):
                found.append(f"{path or '(body)'}[{len(o)}] {sorted(o[0])[:40]}")    # probes7: all keys
            elif isinstance(o, dict):
                for k, v in list(o.items())[:30]:
                    walk(v, f"{path}.{k}" if path else k, depth + 1)
        walk(body, "", 0)
        top = sorted(body)[:10] if isinstance(body, dict) else type(body).__name__
        lines.append(f"{u[:140]}  keys {top}" + (f"  lists: {'; '.join(found[:4])}" if found else ""))
    return lines


class BrowserCapture:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, capture_fn=None, daily=None, stored_long=None, **_ignored):
        self._capture = capture_fn or capture
        self.stored = stored_long
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            for pg in (p.get("browser_capture") or {}).get("pages") or ():
                self._page(p["name"], pg, out)

    def _page(self, name: str, pg: dict, out) -> None:
        from .scrape import robots_verdict
        url = pg["url"]
        pinned = list(pg.get("series") or ()) + list(pg.get("tiles") or ())
        if pg.get("permitted") is not True:
            out.skipped(SOURCE, name, f"{url}: not rendered — robots.txt and terms not yet confirmed "
                                      f"(config browser_capture permitted; probe browser_captures)", TIER)
            return
        if not pinned:
            out.skipped(SOURCE, name, f"{url}: permitted, but no series or tile is pinned yet — pin keys from "
                                      f"probe browser_captures", TIER)
            return
        day = str(today().date())
        if not self.daily.due(f"{SOURCE}:{url}", day):
            return
        ok, why = robots_verdict(url)
        if not ok:
            out.fail(SOURCE, name, f"robots.txt disallows {url} — {why}", TIER)
            return
        toggles = tuple(dict.fromkeys(t["toggle"] for t in pg.get("tiles") or () if t.get("toggle")))
        try:
            got = (self._capture(url, clicks=pg.get("clicks") or (), toggles=toggles) if toggles
                   else self._capture(url, clicks=pg.get("clicks") or ()))
        except Exception as e:  # noqa: BLE001 — a failed render must not kill the run
            out.fail(SOURCE, name, f"{url}: render failed — {type(e).__name__}: {str(e)[:160]}", TIER)
            return
        # ALTERNATE PAGES (probes7): the page that fires a pinned response is not yet certain — the next
        # candidate is rendered only while a pinned response is still missing (each at most once a day).
        wanted = {w for pin in list(pg.get("series") or ()) + list(pg.get("scalars") or ())
                  for w in ((pin["url_contains"],) if isinstance(pin["url_contains"], str) else pin["url_contains"][:1])}
        for alt in pg.get("alt_urls") or ():
            if all(any(w in u for u, _ in got["responses"]) for w in wanted):
                break
            try:
                more = self._capture(alt, clicks=pg.get("clicks") or ())
            except Exception as e:  # noqa: BLE001
                log.warning("%s: render failed — %s", alt, type(e).__name__)
                continue
            got = {**got, "responses": got["responses"] + more["responses"],
                   "text": got["text"] + "\n" + more["text"]}
        self.daily.done(f"{SOURCE}:{url}", day)
        held, units = {}, {}
        series = list(pg.get("series") or ())
        # 1. pins that need no unit verdict (some are the references a unit check reads)
        for pin in [x for x in series if not x.get("unit_leg")]:
            self._series_pin(name, pin, got, out, held, pin.get("units"))
        # 2. each leg's units, decided against stored or captured references (probes7: per leg)
        for uc in pg.get("unit_checks") or ():
            units[uc["leg"]] = self._leg_units(name, uc, got, held, units, out)
        # 3. the legs, stored under the name their verdict gives (as read: never converted)
        for pin in [x for x in series if x.get("unit_leg")]:
            u = units.get(pin["unit_leg"])
            if u is None:
                out.fail(SOURCE, name, f"{pin['unit_leg']}: units (HYPE or USD) not established — see the unit "
                                       f"check. NOTHING STORED.", TIER)
                continue
            self._series_pin(name, {**pin, "metric": pin["metric_by_unit"][u],
                                    "crosscheck": (pin.get("crosscheck_by_unit") or {}).get(u, ())}, got, out, held, u)
        for cc in pg.get("crosschecks") or ():           # a sum of captured legs against a stored series
            legs = [held.get(m) or {} for m in cc["sum"]]
            days = set.intersection(*(set(x) for x in legs)) if legs else set()
            tot = {d: sum(x[d] for x in legs) for d in days}
            log.info("CROSS-CHECK %s %s vs %s: %s", name, "+".join(cc["sum"]), cc["ref"],
                     agreement(tot, held.get(cc["ref"]) or self._ref(name, cc["ref"])))
        for sp in pg.get("scalars") or ():
            got_ = scalar_from(got["responses"], sp)
            if isinstance(got_, str) and sp.get("tile_label"):
                v = tile_from(got["text"], sp["tile_label"])
                got_ = (f"tile \"{sp['tile_label']}\"", v) if v is not None else got_ + "; no tile either"
            if isinstance(got_, str):
                out.fail(SOURCE, name, f"{sp['metric']}: {got_}. NOTHING STORED.", TIER)
                continue
            k, v = got_
            bad = anchor_ok(v, sp)
            if bad:
                out.fail(SOURCE, name, f"{sp['metric']}: `{k}` {bad}. NOTHING STORED.", TIER)
                continue
            out.add(point(name, sp["metric"], v, f"{SOURCE}:{sp['site']}.{k}[{sp['layer']}]", TIER, today()), SOURCE,
                    name, f"{sp['metric']} = {sp['site']} `{k}` {v:,.2f} (FORWARD-ONLY, {sp['layer']})", TIER)
            for cc in sp.get("crosscheck_annualised") or ():
                ser = self._series(name, cc["metric"])
                if not ser:
                    log.info("CROSS-CHECK %s %s %s vs %s: not stored", name, sp["metric"], f"{v:,.0f}", cc["metric"])
                    continue
                last = max(ser)
                win = {d: x for d, x in ser.items() if last - pd.Timedelta(days=int(cc["days"]) - 1) <= d <= last}
                ours = sum(win.values()) * 365 / len(win)
                log.info("CROSS-CHECK %s %s %s vs our %s annualised (%d covered day(s) to %s) %s: ratio %.3f",
                         name, sp["metric"], f"{v:,.0f}", cc["metric"], len(win), last.date(), f"{ours:,.0f}",
                         v / ours if ours else float("nan"))
            if sp.get("flow_metric"):                    # a cumulative figure's daily change (new users)
                prev = {d: x for d, x in self._series(name, sp["metric"]).items() if d < today().normalize()}
                if prev:
                    d0 = max(prev)
                    span = (today().normalize() - d0).days
                    out.add(point(name, sp["flow_metric"], v - prev[d0],
                                  f"{SOURCE}:{sp['site']}.{k}[{sp['layer']}, daily change"
                                  + (f", span={span}d" if span > 1 else "") + "]", TIER, today()),
                            SOURCE, name, f"{sp['flow_metric']} = {v - prev[d0]:,.0f} since {d0.date()}", TIER)
        for t in pg.get("tiles") or ():
            if t["metric"] in held:                      # the chart's series resolved: the tile is its fallback
                continue
            text = got.get("texts", {}).get(t["toggle"]) if t.get("toggle") else got["text"]
            if text is None:
                out.fail(SOURCE, name, f"{t['metric']}: the \"{t['toggle']}\" toggle is not on the page. NOTHING "
                                       f"STORED.", TIER)
                continue
            v, why = tile_read(text, t["label"])
            if v is None:
                out.fail(SOURCE, name, f"{t['metric']}: {why}"
                                       + (f" (after the \"{t['toggle']}\" toggle)" if t.get("toggle") else "")
                                       + ". NOTHING STORED.", TIER)
                continue
            out.add(point(name, t["metric"], v, f"{SOURCE}:{t['site']}.tile[{t['layer']}"
                          + (f", {t['toggle']} toggle" if t.get("toggle") else "") + "]", TIER, today()), SOURCE,
                    name, f"{t['metric']} = \"{t['label']}\" {v:,.4f} (rendered tile, FORWARD-ONLY, {t['layer']})", TIER)

    def _series_pin(self, name, pin, got, out, held, unit) -> None:
        pts = series_from(got["responses"], pin)
        if isinstance(pts, str):
            out.fail(SOURCE, name, f"{pin['metric']}: {pts}. NOTHING STORED.", TIER)
            return
        bad = anchor_ok(pts[-1][1], pin)                 # the latest point against Jake's reading
        if bad:
            out.fail(SOURCE, name, f"{pin['metric']}: latest {bad}. NOTHING STORED.", TIER)
            return
        if pin.get("tile_check"):
            bad = tile_check(pts, got["text"], pin["tile_check"])
            if bad:
                out.fail(SOURCE, name, f"{pin['metric']}: {bad}. NOTHING STORED.", TIER)
                return
        vk = (pin.get("_keys") or (None, None, pin.get("value_key")))[2]
        src = f"{SOURCE}:{pin['site']}.{vk}[{pin['layer']}" + (f", {unit.upper()} as read" if unit else "") + "]"
        rows = [(d, v) for d, v in pts if d < today()]
        if pin.get("expected_lag_days") and rows:        # a late publisher: say so if it gets later
            lag = (today().normalize() - rows[-1][0]).days
            if lag > int(pin["expected_lag_days"]) + int(pin.get("lag_slack_days", 5)):
                out.fail(SOURCE, name, f"{pin['metric']}: LAG GREW — the latest point is {rows[-1][0].date()}, {lag} "
                                       f"days behind (expected ~{pin['expected_lag_days']}); stored, but check the "
                                       f"source", TIER)
        held[pin["metric"]] = dict(rows)
        if pin.get("store", True):
            out.add(tidy(rows, name, pin["metric"], src, TIER), SOURCE, name,
                    f"{pin['metric']} = {pin['site']} `{vk}` ({pin['layer']}): {len(rows)} point(s) "
                    f"{rows[0][0].date()}..{rows[-1][0].date()}" if rows else f"{pin['metric']}: no past point", TIER)
        for ref in (pin.get("crosscheck") or ()):
            log.info("CROSS-CHECK %s %s vs %s: %s", name, pin["metric"], ref,
                     agreement(dict(rows), held.get(ref) or self._ref(name, ref)))

    def _leg_units(self, name, uc, got, held, units, out):
        """'tokens' | 'usd' | None for one leg (probes7: every leg is checked, not only the buyback):
        `refs` — stored series ("stored:x", or holders_revenue_tokens = holders revenue / price) or series
        captured this render ("captured:metric") — or `same_unit_as_total`: the leg sums with legs already
        decided into the response's `total` on >= 90% of days, so it shares their (single) unit."""
        pts = series_from(got["responses"], {"url_contains": uc["url_contains"], "path": uc["path"],
                                             "date_key": uc["date_key"], "value_key": uc["leg"]})
        if isinstance(pts, str):
            out.fail(SOURCE, name, f"unit check `{uc['leg']}`: {pts}", TIER)
            return None
        leg = dict(pts)
        if uc.get("same_unit_as_total"):
            st_ = uc["same_unit_as_total"]
            others = [units.get(x) for x in st_["legs"] if x != uc["leg"]]
            arr = path_get(next((b for u, b in got["responses"] if _matches(u, uc["url_contains"])), {}), uc["path"])
            ok = n = 0
            for it in arr or ():
                if not isinstance(it, dict):
                    continue
                vals = [_num(it.get(x)) for x in st_["legs"]] + [_num(it.get(st_["total"]))]
                if None in vals or not vals[-1]:
                    continue
                n += 1
                ok += abs(sum(vals[:-1]) / vals[-1] - 1) <= st_.get("within", 0.01)
            agree = len(set(others)) == 1 and None not in others
            verdict = others[0] if agree and n and ok / n >= 0.9 else None
            msg = (f"unit check `{uc['leg']}`: `{st_['total']}` = sum of the legs on {ok}/{n} day(s); the other "
                   f"legs are {others} -> " + (verdict.upper() if verdict else "NOT ESTABLISHED"))
        else:
            refs = {}
            for unit, names in uc["refs"].items():
                ref = {}
                for r in names:
                    kind, _, m = r.partition(":")
                    ref.update((held.get(m) or {}) if kind == "captured" else self._ref(name, m))
                refs[unit] = ref
            verdict, rep_ = units_verdict(leg, refs, tuple(uc.get("band", (0.8, 1.25))))
            msg = f"unit check `{uc['leg']}`: {rep_} -> " + (verdict.upper() if verdict else "NOT ESTABLISHED")
        self.unit_reports = getattr(self, "unit_reports", []) + [msg]
        (log.info if verdict else log.warning)("%s %s", name, msg)
        if not verdict:
            out.fail(SOURCE, name, msg + ". The leg is NOT stored.", TIER)
        return verdict

    # --- stored references ------------------------------------------------------------------------
    def _series(self, name: str, metric: str) -> dict:
        s = self.stored
        if s is None or getattr(s, "empty", True):
            return {}
        g = s[(s["project"] == name) & (s["metric"] == metric)]
        if g.empty:
            return {}
        g = g.sort_values("date").drop_duplicates("date", keep="last")
        return dict(zip(pd.to_datetime(g["date"]).dt.normalize(), g["value"].astype(float)))

    def _ref(self, name: str, ref: str) -> dict:
        """A stored series, or `holders_revenue_tokens` = DefiLlama holders revenue / same-day price."""
        if ref == "holders_revenue_tokens":
            usd, px = self._series(name, "holders_revenue_usd"), self._series(name, "price_usd")
            return {d: v / px[d] for d, v in usd.items() if px.get(d)}
        return self._series(name, ref)
