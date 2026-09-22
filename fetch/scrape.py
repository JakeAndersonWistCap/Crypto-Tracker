"""
fetch/scrape.py — tier 3: the protocol's own dashboard, read by rendering the page.

Where a protocol publishes the figure itself, that is the number we want. PancakeSwap
publishes net mint monthly; others publish burn totals, node counts, utilisation and revenue.
These are single-page apps, so plain requests returns an empty shell — this uses Playwright
headless Chromium and lets the JavaScript execute.

Two extraction strategies, in this order:

  1. INTERCEPT THE NETWORK RESPONSE (method: xhr). Nearly every dashboard fetches its numbers
     from a JSON endpoint the page calls on load. Capturing that response and reading the JSON
     is far more stable than DOM selectors, and survives redesigns that leave the data layer
     intact.
  2. DOM EXTRACTION (method: dom), anchored on LABEL TEXT, never on position. Find the element
     whose text matches the expected label, then read the number associated with it.

Anti-brittleness. The dangerous failure is not the site going down, which is obvious. It is a
redesign where the selector still matches but now points at a different number. Three defences:
  * anchor on label text, not position          — here
  * sanity bounds per metric                    — fetch/validate.py
  * change threshold to a review queue          — fetch/validate.py

Politeness: one run a day, aggressive caching (a same-day re-run reads the cache and makes no
request), robots.txt respected, and an honest user agent naming the tool.

The registry is sources.yaml. Adding a source is a YAML edit — no Python. A page that genuinely
needs bespoke handling gets a named override in adapters/<name>.py exposing
extract(page, entry) -> float | None, referenced by the entry's `adapter:` field.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import urllib.parse
import urllib.robotparser

import config
from pathlib import Path

import yaml

from .base import USER_AGENT, derive_flow_from_cumulative, json_path_get, parse_number, point, today

log = logging.getLogger("token_metrics.fetch.scrape")

SOURCE = "scrape"
TIER = 3
REGISTRY = Path(os.environ.get("TOKEN_METRICS_SOURCES", "sources.yaml"))
CACHE_DIR = Path(os.environ.get("TOKEN_METRICS_CACHE", ".cache/scrape"))
PAGE_TIMEOUT_MS = int(os.environ.get("TOKEN_METRICS_PAGE_TIMEOUT_MS", 45000))

REQUIRED_FIELDS = ("project", "metric", "url", "method")

# Find the number associated with a label, anchored on the label's text.
# Searches, in order: the label element's own trailing text, its next sibling, its previous
# sibling, then the other children of its parent. Returns every candidate so an ambiguous
# match can be reported rather than silently resolved.
DOM_ANCHOR_JS = r"""
(anchor) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const target = norm(anchor).toLowerCase();
  const NUM = /-?\(?\s*\$?\s*\d[\d,]*(?:\.\d+)?\s*(?:%|[KkMmBb]n?|[Tt])?\s*\)?/;
  const textOf = el => norm(el ? el.textContent : '');
  const firstNumber = s => { const m = NUM.exec(s || ''); return m ? m[0] : null; };

  const all = Array.from(document.querySelectorAll('body *'))
    .filter(el => !['SCRIPT','STYLE','NOSCRIPT'].includes(el.tagName));
  let labels = all.filter(el => {
    const t = textOf(el).toLowerCase();
    return t.includes(target) && t.length < target.length + 120;
  });
  // Prefer the tightest match: the label element itself, not an enclosing container.
  labels.sort((a, b) => textOf(a).length - textOf(b).length);
  labels = labels.slice(0, 8);

  const out = [];
  for (const el of labels) {
    const own = textOf(el);
    const after = own.toLowerCase().indexOf(target) + target.length;
    const tail = own.slice(after);
    const candidates = [
      ['own_tail', firstNumber(tail)],
      ['next_sibling', firstNumber(textOf(el.nextElementSibling))],
      ['prev_sibling', firstNumber(textOf(el.previousElementSibling))],
    ];
    if (el.parentElement) {
      for (const sib of Array.from(el.parentElement.children)) {
        if (sib === el) continue;
        candidates.push(['parent_child', firstNumber(textOf(sib))]);
      }
    }
    for (const [where, val] of candidates) {
      if (val) out.push({ where, value: val, label: own.slice(0, 120) });
    }
    if (out.length) break;   // the tightest label that yielded anything wins
  }
  return out;
}
"""


# ---------------------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------------------
def load_registry(path: Path | str = REGISTRY) -> list[dict]:
    """Read sources.yaml. Entries missing required fields or disabled are returned too — the
    gap detector needs to see them to report what is still unresolved."""
    path = Path(path)
    if not path.exists():
        return []
    with path.open() as fh:
        data = yaml.safe_load(fh) or []
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a YAML list of source entries")
    return [e for e in data if isinstance(e, dict)]


def entry_ready(entry: dict) -> tuple[bool, str]:
    """Is this entry usable? Returns (ready, reason_if_not)."""
    if entry.get("enabled") is False:
        # TWO VERY DIFFERENT THINGS WERE SAYING THE SAME SENTENCE, and the Gap Report filed both
        # at P6 "uncovered". An entry disabled WITH a url on file is a diagnosed decision —
        # robots.txt disallows the page, or the extraction target is not yet known — and the
        # reason is written in the entry's own note. An entry disabled with url: null is an empty
        # stub nobody has looked at. The first is "by design", the second is real untouched work,
        # and collapsing them hides the second behind the first.
        if entry.get("url"):
            return False, ("entry DELIBERATELY disabled in sources.yaml — the page is on file "
                           f"({entry.get('url')}) and the entry's note records why it is not armed")
        return False, "entry disabled in sources.yaml — EMPTY STUB, no url on file yet"
    missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
    if missing:
        return False, f"sources.yaml entry incomplete — missing {', '.join(missing)}"
    if entry["method"] not in ("xhr", "dom", "adapter"):
        return False, f"unknown method {entry['method']!r} (expected xhr, dom or adapter)"
    if entry["method"] == "xhr" and not entry.get("json_path"):
        return False, "method xhr needs json_path"
    if entry["method"] == "dom" and not entry.get("anchor"):
        return False, "method dom needs anchor (the label text to anchor on)"
    if entry["method"] == "adapter" and not entry.get("adapter"):
        return False, "method adapter needs adapter (module name under adapters/)"
    return True, ""


# ---------------------------------------------------------------------------------------
# Politeness
# ---------------------------------------------------------------------------------------
_ROBOTS: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def robots_allows(url: str) -> bool:
    """Respect robots.txt. A robots.txt we cannot fetch is treated as permissive, which is the
    conventional reading, but a explicit Disallow is always honoured."""
    if os.environ.get("TOKEN_METRICS_IGNORE_ROBOTS", "").strip() in ("1", "true", "yes"):
        return True
    parts = urllib.parse.urlparse(url)
    root = f"{parts.scheme}://{parts.netloc}"
    if root not in _ROBOTS:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{root}/robots.txt")
        try:
            rp.read()
        except Exception:  # noqa: BLE001 — unreachable robots.txt is permissive
            rp = None
        _ROBOTS[root] = rp
    rp = _ROBOTS[root]
    if rp is None:
        return True
    try:
        return rp.can_fetch(USER_AGENT, url)
    except Exception:  # noqa: BLE001
        return True


def cache_key(entry: dict) -> str:
    raw = f"{entry['project']}|{entry['metric']}|{entry.get('url','')}"
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in raw)[:120]


def cache_read(entry: dict):
    f = CACHE_DIR / str(today().date()) / f"{cache_key(entry)}.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:  # noqa: BLE001
            return None
    return None


def cache_write(entry: dict, payload: dict):
    d = CACHE_DIR / str(today().date())
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{cache_key(entry)}.json").write_text(json.dumps(payload, default=str))


# ---------------------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------------------
def extract_xhr(page, entry: dict, captured: list) -> tuple[float | None, str]:
    """Read the value out of a JSON response the page fetched for itself."""
    needle = entry.get("url_contains") or ""
    path = entry["json_path"]
    for url, payload in captured:
        if needle and needle not in url:
            continue
        v = json_path_get(payload, path)
        if v is not None:
            parsed = parse_number(v)
            if parsed is not None:
                return parsed, f"xhr {url[:120]} -> {path}"
    return None, (f"no intercepted response matched url_contains={needle!r} with json_path={path!r}"
                  f" ({len(captured)} JSON responses seen)")


def extract_dom(page, entry: dict) -> tuple[float | None, str]:
    """Read the value anchored on the label text."""
    anchor = entry["anchor"]
    try:
        candidates = page.evaluate(DOM_ANCHOR_JS, anchor)
    except Exception as e:  # noqa: BLE001
        return None, f"DOM evaluation failed: {e}"
    if not candidates:
        return None, f"anchor {anchor!r} not found on the page"
    values = []
    for c in candidates:
        v = parse_number(c.get("value"))
        if v is not None:
            values.append((v, c))
    if not values:
        return None, f"anchor {anchor!r} matched but no number was associated with it"
    distinct = {v for v, _ in values}
    v, c = values[0]
    detail = f"dom anchor={anchor!r} via {c['where']} label={c['label']!r}"
    if len(distinct) > 1:
        detail += f" | AMBIGUOUS: {len(distinct)} different numbers matched, took the tightest"
    return v, detail


def extract_adapter(page, entry: dict) -> tuple[float | None, str]:
    """Hand off to a named override in adapters/<name>.py exposing extract(page, entry)."""
    name = entry["adapter"]
    try:
        mod = importlib.import_module(f"adapters.{name}")
    except ImportError as e:
        return None, f"adapter {name!r} not importable: {e}"
    fn = getattr(mod, "extract", None)
    if fn is None:
        return None, f"adapter {name!r} has no extract(page, entry)"
    try:
        return parse_number(fn(page, entry)), f"adapter:{name}"
    except Exception as e:  # noqa: BLE001
        return None, f"adapter {name!r} raised: {e}"


# ---------------------------------------------------------------------------------------
class Scrape:
    """Tier 3 adapter. One browser for the whole run; one page per entry."""

    def __init__(self, prior_values: dict | None = None, registry_path: Path | str = REGISTRY,
                 prior_dates: dict | None = None, prior_delta: dict | None = None):
        self.prior = prior_values or {}
        self.prior_dates = prior_dates or {}
        # SAME PAIRING RULE AS THE CHAIN AND HYPERCORE ADAPTERS: the value differenced against
        # must come from the same row as the date that guards it. prior_values is the newest
        # figure of ANY date and is poisoned by an earlier run on the same day; prior_delta is
        # values_before(today). Using the first with the second's date makes a same-day re-run
        # destructive — see store.values_before.
        self.prior_delta = prior_delta if prior_delta is not None else (prior_values or {})
        self.registry_path = registry_path
        self.entries = load_registry(registry_path)

    # -- the browser is opened lazily so an offline run that has nothing to scrape costs nothing
    def _browser(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        try:
            browser = self._pw.chromium.launch(headless=True)
        except Exception:  # noqa: BLE001 — fall back to an explicit executable path
            browser = self._pw.chromium.launch(headless=True, executable_path="/opt/pw-browsers/chromium")
        return browser

    def run(self, projects: list[dict], window_days, out):
        names = {p["name"] for p in projects}
        ready, deferred = [], []
        for e in self.entries:
            ok, why = entry_ready(e)
            (ready if ok else deferred).append((e, why))

        for e, why in deferred:
            proj, metric = e.get("project", "?"), e.get("metric", "?")
            out.unconfigured(SOURCE, proj, f"{metric}: {why}", TIER)
            out.gap(proj, metric, reason=why, tiers_attempted="3",
                    suggestion=f"Complete the entry in {self.registry_path} (url, method, and anchor or json_path), "
                               f"then set enabled: true")

        todo = [e for e, _ in ready if e["project"] in names]
        if not todo:
            if ready:
                log.info("tier 3: no enabled sources.yaml entries match the current universe")
            return

        cached, live = [], []
        for e in todo:
            (cached if cache_read(e) is not None else live).append(e)
        for e in cached:
            self._emit(e, cache_read(e)["value"], f"{SOURCE}:cache", out, cached=True)

        if not live:
            return

        browser = None
        try:
            browser = self._browser()
        except Exception as e:  # noqa: BLE001
            for entry in live:
                out.fail(SOURCE, entry["project"], f"{entry['metric']}: Playwright unavailable: {e}", TIER)
                out.gap(entry["project"], entry["metric"], reason=f"Playwright could not start: {e}",
                        tiers_attempted="3", suggestion="pip install playwright (Chromium is already on this image)")
            return

        try:
            context = browser.new_context(user_agent=USER_AGENT)
            for entry in live:
                self._scrape_one(context, entry, out)
            context.close()
        finally:
            try:
                browser.close()
                self._pw.stop()
            except Exception:  # noqa: BLE001
                pass

    def _scrape_one(self, context, entry: dict, out):
        proj, metric, url = entry["project"], entry["metric"], entry["url"]
        if not robots_allows(url):
            out.fail(SOURCE, proj, f"{metric}: robots.txt disallows {url}", TIER)
            out.gap(proj, metric, reason=f"robots.txt disallows fetching {url}", tiers_attempted="3",
                    suggestion="Use a different published source, or enter the figure via manual_overrides.csv")
            return
        captured: list = []
        page = context.new_page()

        def on_response(resp):
            try:
                ctype = (resp.headers or {}).get("content-type", "")
                if "json" in ctype.lower():
                    captured.append((resp.url, resp.json()))
            except Exception:  # noqa: BLE001 — a body we cannot read is simply not a candidate
                pass

        page.on("response", on_response)
        try:
            page.goto(url, timeout=PAGE_TIMEOUT_MS, wait_until=entry.get("wait_until", "networkidle"))
            if entry.get("wait_for"):
                page.wait_for_selector(entry["wait_for"], timeout=PAGE_TIMEOUT_MS)
            method = entry["method"]
            if method == "xhr":
                value, detail = extract_xhr(page, entry, captured)
                if value is None and entry.get("anchor"):   # documented fallback, never silent
                    value, dom_detail = extract_dom(page, entry)
                    detail = f"{detail} | fell back to DOM: {dom_detail}"
            elif method == "dom":
                value, detail = extract_dom(page, entry)
            else:
                value, detail = extract_adapter(page, entry)
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, proj, f"{metric}: {url}: {e}", TIER)
            out.gap(proj, metric, reason=f"page load or extraction failed: {e}", tiers_attempted="3",
                    suggestion=f"Check the url and method in {self.registry_path}")
            page.close()
            return
        page.close()

        if value is None:
            out.fail(SOURCE, proj, f"{metric}: extraction returned nothing — {detail}", TIER)
            out.gap(proj, metric, reason=f"extraction returned nothing: {detail}", tiers_attempted="3",
                    suggestion=f"Re-check anchor/json_path in {self.registry_path}; the page may have been redesigned")
            return
        cache_write(entry, {"value": value, "detail": detail, "url": url})
        self._emit(entry, value, f"{SOURCE}:{urllib.parse.urlparse(url).netloc}", out, detail=detail)

    def _emit(self, entry: dict, value: float, source: str, out, detail: str = "", cached: bool = False):
        proj, metric = entry["project"], entry["metric"]
        scale = float(entry.get("scale", 1) or 1)
        value = value * scale
        when = today()
        tier = int(entry.get("tier", TIER) or TIER)   # provenance: a Dune dashboard page is tier 4
        note = ""
        if entry.get("needs_first_run_check"):
            note = " | FIRST-RUN CHECK: the anchor/json_path was inferred without sight of the page — "
            note += "eyeball this value against the page before trusting it"
            out.review_item(proj, metric, "anchor_unconfirmed", "stored_flagged", value=value,
                            prior_value=self.prior.get((proj, metric)), date=when, source=source, tier=tier)
        out.add(point(proj, metric, value, source, tier, when), SOURCE, proj,
                f"{metric}={value:,.4f}" + (" (cached)" if cached else "") + (f" | {detail}" if detail else "") + note, tier)
        flow_metric = entry.get("derive_flow_metric")
        if entry.get("cumulative") and flow_metric:
            flow = derive_flow_from_cumulative(value, self.prior_delta.get((proj, metric)), proj, flow_metric,
                                               config.mark_source(source, "delta"), tier, when,
                                               prior_date=self.prior_dates.get((proj, metric)),
                                               stock_metric=metric, out=out)
            if not flow.empty:
                out.add(flow, SOURCE, proj, f"{flow_metric} derived from {metric} delta", TIER)
