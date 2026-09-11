"""
adapters — named overrides for pages that genuinely need bespoke handling.

Almost nothing belongs here. The generic sources.yaml path (intercept the page's own JSON
endpoint, or anchor on label text) handles nearly every dashboard. Reach for an override only
when a page defeats both: a figure assembled from several elements, a value behind a control
that must be clicked, a chart with no underlying endpoint.

Contract — a module here exposes:

    def extract(page, entry) -> float | str | None

`page` is a live Playwright page, already navigated to entry["url"] with the JavaScript
executed. `entry` is the sources.yaml entry. Return the figure (a string is parsed by
fetch.base.parse_number, so "1,234,567" and "$1.2M" are fine), or None if the figure was not
found — never a zero standing in for a failure.

Reference it from sources.yaml:

    - project: Example
      metric: supply_units
      tier: 5
      enabled: true
      url: https://...
      method: adapter
      adapter: example_dashboard      # -> adapters/example_dashboard.py

Keep the same anti-brittleness discipline as the generic path: anchor on text the page shows
a human, not on positions in the DOM tree. Sanity bounds and the change threshold still apply
to whatever you return.
"""
