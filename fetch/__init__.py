"""
fetch — source adapters, one module per tier.

    free API  ->  contract read  ->  render the public page  ->  paid API

Tier 1  llama.py, coingecko.py, schedule.py   free APIs and documented rules
Tier 2  chain.py                              web3 contract reads (build this out first)
Tier 3  scrape.py                             the protocol's own dashboard, via sources.yaml
Tier 4  dune.py                               historical backfill only
Tier 5  scrape.py                             off-chain operational metrics, same registry

Every adapter returns tidy long frames: date, project, metric, value, source, tier.
Every frame passes through validate.validate_frame before it reaches the store, so a value
outside its sanity bounds is rejected and a value that moved more than its change threshold is
flagged. A failed source is logged and reported as a gap; it never kills the run.
"""
from __future__ import annotations

import logging

from .base import FetchOutput, LogEntry, new_run_id, today  # noqa: F401
from .chain import Chain
from .coingecko import CoinGecko
from .dune import Dune
from .gaps import detect as detect_gaps
from .llama import DefiLlama
from .schedule import Schedule
from .tron import TronNode
from .scrape import Scrape, entry_ready, load_registry
from .validate import check_cross_checks, check_reference_values, validate_frame

log = logging.getLogger("token_metrics.fetch")

TIER_ORDER = [
    ("schedule:config", 1, lambda ctx: Schedule()),
    ("defillama", 1, lambda ctx: DefiLlama()),
    ("coingecko", 1, lambda ctx: CoinGecko()),
    ("chain", 2, lambda ctx: Chain(prior_values=ctx["prior_values"])),
    ("tron_node", 2, lambda ctx: TronNode(prior_values=ctx["prior_values"])),
    ("scrape", 3, lambda ctx: Scrape(prior_values=ctx["prior_values"])),
    ("dune", 4, lambda ctx: Dune(has_history=ctx["has_history"])),
]


def fetch_all(projects: list[dict], window_days: int | None, *,
              prior_values: dict | None = None,
              has_history: set | None = None,
              manual_keys: set | None = None,
              sources: list[str] | None = None) -> FetchOutput:
    """Run every tier in order and return one FetchOutput carrying frames, log, review and gaps.

    prior_values  (project, metric) -> last stored value. Feeds the change-threshold check and
                  the cumulative-to-flow differencing in tiers 2 and 3.
    has_history   (project, metric) pairs the store already has history for. Tier 4 skips these,
                  because Dune is a backfill dependency, not an ongoing one.
    manual_keys   (project, metric) pairs covered by manual_overrides.csv, so the Gap Report
                  does not list something Jake has already entered by hand.
    """
    ctx = {"prior_values": prior_values or {}, "has_history": has_history or set()}
    out = FetchOutput()

    for name, tier, build in TIER_ORDER:
        if sources and name not in sources:
            continue
        log.info("tier %d — %s (window=%s)", tier, name, window_days or "full history")
        before = len(out.frames)
        try:
            build(ctx).run(projects, window_days, out)
        except Exception as e:  # noqa: BLE001 — never let one source kill the run
            out.fail(name, None, f"adapter crashed: {e}", tier)
        # validate each tier's own output, so a rejection names the tier that produced it
        for i in range(before, len(out.frames)):
            out.frames[i] = validate_frame(out.frames[i], ctx["prior_values"], out)
        out.frames = [f for f in out.frames if f is not None and not f.empty]

    check_reference_values(out.frame(), out)
    check_cross_checks(out.frame(), out)

    registry_reasons = {}
    for e in load_registry():
        ok, why = entry_ready(e)
        if not ok and e.get("project") and e.get("metric"):
            registry_reasons[(e["project"], e["metric"])] = why

    out.gaps = detect_gaps(projects, out.frame(), manual_keys or set(), registry_reasons, out.gaps)
    return out
