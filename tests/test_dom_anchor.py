"""
tests/test_dom_anchor.py — the DOM-anchor contract, against real headless Chromium.

    python tests/test_dom_anchor.py

Loads handwritten pages shaped like the dashboards this tool reads (a stat card, a table row,
a label-above-value block, a redesigned layout) and asserts the anchor finds the right number
in each. No network: the pages are data: URLs.

This is the test that matters for brittleness. If a redesign moves the number, the anchor
should still find it, because it anchors on the label a human reads, not on a position.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fetch.scrape import extract_dom  # noqa: E402

PAGES = {
    "stat card (value in a sibling div)": """
      <div class="card"><div class="label">Total CAKE Burned</div><div class="value">1,234,567</div></div>
      <div class="card"><div class="label">Total CAKE Minted</div><div class="value">9,876,543</div></div>
    """,
    "label and value in one element": """
      <div><span>Total CAKE Burned: 1,234,567 CAKE</span></div>
    """,
    "table row": """
      <table><tbody>
        <tr><td>Total CAKE Minted</td><td>9,876,543</td></tr>
        <tr><td>Total CAKE Burned</td><td>1,234,567</td></tr>
      </tbody></table>
    """,
    "REDESIGNED: value first, label after": """
      <section><p class="figure">1,234,567</p><p class="caption">Total CAKE Burned</p></section>
    """,
    "REDESIGNED: deeply nested, different classes": """
      <main><div><div><article>
        <header><h4><span>Total CAKE Burned</span></h4></header>
        <footer><strong data-v="x">1,234,567</strong></footer>
      </article></div></div></main>
    """,
    "value with currency and suffix": """
      <div><div>Protocol Revenue</div><div>$1.2M</div></div>
    """,
    "negative in parentheses": """
      <div><div>Net CAKE</div><div>(1,958,514)</div></div>
    """,
}

EXPECT = {
    "stat card (value in a sibling div)": ("Total CAKE Burned", 1234567.0),
    "label and value in one element": ("Total CAKE Burned", 1234567.0),
    "table row": ("Total CAKE Burned", 1234567.0),
    "REDESIGNED: value first, label after": ("Total CAKE Burned", 1234567.0),
    "REDESIGNED: deeply nested, different classes": ("Total CAKE Burned", 1234567.0),
    "value with currency and suffix": ("Protocol Revenue", 1200000.0),
    "negative in parentheses": ("Net CAKE", -1958514.0),
}


def data_url(body: str) -> str:
    html = f"<!doctype html><html><head><meta charset='utf-8'></head><body>{body}</body></html>"
    return "data:text/html;base64," + base64.b64encode(html.encode()).decode()


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures = []
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception:  # noqa: BLE001
            browser = pw.chromium.launch(headless=True, executable_path="/opt/pw-browsers/chromium")
        page = browser.new_page()
        for name, body in PAGES.items():
            anchor, expected = EXPECT[name]
            page.goto(data_url(body))
            value, detail = extract_dom(page, {"anchor": anchor})
            ok = value == expected
            print(f"{'OK ' if ok else 'BAD'} {name}: {value!r} (expected {expected!r}) — {detail[:90]}")
            if not ok:
                failures.append((name, value, expected, detail))

        # A label that is not on the page must return None, never a number from elsewhere.
        page.goto(data_url(PAGES["stat card (value in a sibling div)"]))
        value, detail = extract_dom(page, {"anchor": "Nonexistent Label"})
        ok = value is None
        print(f"{'OK ' if ok else 'BAD'} missing label returns None: {value!r} — {detail[:70]}")
        if not ok:
            failures.append(("missing label", value, None, detail))

        # The wrong sibling must not be picked when two cards sit next to each other.
        page.goto(data_url(PAGES["stat card (value in a sibling div)"]))
        value, _ = extract_dom(page, {"anchor": "Total CAKE Minted"})
        ok = value == 9876543.0
        print(f"{'OK ' if ok else 'BAD'} adjacent card not confused: {value!r} (expected 9876543.0)")
        if not ok:
            failures.append(("adjacent card", value, 9876543.0, ""))

        browser.close()

    if failures:
        print(f"\n{len(failures)} DOM anchor FAILURES")
        return 1
    print(f"\nALL {len(PAGES) + 2} DOM ANCHOR CASES PASSED (real headless Chromium)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
