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

POLITE: robots.txt is checked (fetch.scrape.robots_verdict); one GET per page per day; an honest
User-Agent. The monthly manual supply_units row stays as the fallback (a declared handover).
"""
from __future__ import annotations

import json
import logging
import re

from .base import USER_AGENT, point, today

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
        for page, m in spec["pages"].items():
            wanted = m.get("fields") or {}
            if not wanted:
                continue
            url = spec["base"].rstrip("/") + "/" + page.lstrip("/")
            if not self.daily.due(f"aethir_page:{url}", day):
                for metric in wanted.values():
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
            got = fields(html)
            for key, metric in wanted.items():
                vals = got.get(key)
                if not vals:
                    out.fail(SOURCE, name, f"{metric}: `{key}` is not in {page}'s server-rendered payload "
                                           f"(keys seen: {sorted(got)[:25]}). NOTHING STORED; the "
                                           f"manual row stays the fallback.", TIER)
                    continue
                if len(vals) > 1:
                    out.fail(SOURCE, name, f"{metric}: `{key}` carries {len(vals)} different values on "
                                           f"{page} ({vals[:5]}) — which one is the figure is not "
                                           f"established. NOTHING STORED.", TIER)
                    continue
                out.add(point(name, metric, vals[0], f"{SOURCE}:{page}.{key}", TIER, today()), SOURCE, name,
                        f"{metric} = {page} `{key}` = {vals[0]:,.2f} (server-rendered payload)", TIER)
