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


def capture(url: str, clicks=(), wait_ms: int = 4000, timeout_ms: int = 45000, browser=None) -> dict:
    """{"responses": [(url, json)], "ws": [(url, text)], "text": rendered text, "clicked": [labels]}."""
    from playwright.sync_api import sync_playwright
    out = {"responses": [], "ws": [], "text": "", "clicked": []}
    with sync_playwright() as pw:
        b = browser
        if b is None:
            try:
                b = pw.chromium.launch(headless=True)
            except Exception:  # noqa: BLE001 — the image's explicit Chromium
                b = pw.chromium.launch(headless=True, executable_path="/opt/pw-browsers/chromium")
        page = b.new_page(user_agent=USER_AGENT)

        def on_response(r):
            try:
                if "json" in (r.headers.get("content-type") or ""):
                    out["responses"].append((r.url, r.json()))
            except Exception:  # noqa: BLE001 — a body that is not JSON after all
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


def series_from(captured: list, pin: dict):
    """[(day, value)] from the first response matching the pin, or why not."""
    from .aethir_pages import _as_day
    seen = []
    for u, body in captured:
        if pin["url_contains"] not in u:
            continue
        arr = path_get(body, pin.get("path", ""))
        seen.append(u[:100])
        if not isinstance(arr, list):
            continue
        pts = {}
        for it in arr:
            if not isinstance(it, dict):
                continue
            d = _as_day(it.get(pin["date_key"]))
            v = it.get(pin["value_key"])
            if d is not None and isinstance(v, (int, float, str)):
                try:
                    pts[d] = float(v)
                except ValueError:
                    continue
        if pts:
            return sorted(pts.items())
    return (f"no captured response for {pin['url_contains']!r} held a dated list at {pin.get('path') or '(body)'}"
            + (f" (matching: {', '.join(seen[:3])})" if seen else ""))


def tile_from(text: str, label: str):
    """The first number printed after `label` in the rendered text, or None."""
    from .base import parse_number
    i = text.find(label)
    if i < 0:
        return None
    m = _NUM.search(text, i + len(label), i + len(label) + 120)
    return parse_number(m.group(0)) if m else None


def shapes(captured: list) -> list[str]:
    """One line per captured response: URL, top keys, and any list of dicts with its item keys."""
    lines = []
    for u, body in captured:
        found = []

        def walk(o, path, depth):
            if depth > 3:
                return
            if isinstance(o, list) and o and isinstance(o[0], dict):
                found.append(f"{path or '(body)'}[{len(o)}] {sorted(o[0])[:8]}")
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

    def __init__(self, capture_fn=None, daily=None, **_ignored):
        self._capture = capture_fn or capture
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
        try:
            got = self._capture(url, clicks=pg.get("clicks") or ())
        except Exception as e:  # noqa: BLE001 — a failed render must not kill the run
            out.fail(SOURCE, name, f"{url}: render failed — {type(e).__name__}: {str(e)[:160]}", TIER)
            return
        self.daily.done(f"{SOURCE}:{url}", day)
        for pin in pg.get("series") or ():
            pts = series_from(got["responses"], pin)
            if isinstance(pts, str):
                out.fail(SOURCE, name, f"{pin['metric']}: {pts}. NOTHING STORED.", TIER)
                continue
            rows = [(d, v) for d, v in pts if d < today()]
            src = f"{SOURCE}:{pin['site']}.{pin['value_key']}[{pin['layer']}]"
            out.add(tidy(rows, name, pin["metric"], src, TIER), SOURCE, name,
                    f"{pin['metric']} = {pin['site']} `{pin['value_key']}` ({pin['layer']}): {len(rows)} point(s) "
                    f"{rows[0][0].date()}..{rows[-1][0].date()}" if rows else f"{pin['metric']}: no past point", TIER)
        for t in pg.get("tiles") or ():
            v = tile_from(got["text"], t["label"])
            if v is None:
                out.fail(SOURCE, name, f"{t['metric']}: \"{t['label']}\" not found in the rendered page. NOTHING "
                                       f"STORED.", TIER)
                continue
            out.add(point(name, t["metric"], v, f"{SOURCE}:{t['site']}.tile[{t['layer']}]", TIER, today()), SOURCE,
                    name, f"{t['metric']} = \"{t['label']}\" {v:,.4f} (rendered tile, FORWARD-ONLY, {t['layer']})", TIER)
