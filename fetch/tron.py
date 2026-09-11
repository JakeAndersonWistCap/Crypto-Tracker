"""
fetch/tron.py — tier 2: TRON's protocol-level burn, read from a node.

TRON burns TRX by protocol rule when a transaction's bandwidth or energy is insufficient. Every
black-hole address that circulates publicly is the WRONG approach: TIP proposal #49 moved burned
TRX out of the black-hole address and into DynamicPropertiesStore under the key BURN_TRX,
specifically so burns could be counted flexibly. A balance read on T9yD14... does not return the
fee burn, and would quietly report a different number entirely.

So the read is a node API call for BURN_TRX (TronGrid, or any TRON node), not an address
balance. That value is cumulative, so the period burn is derived by differencing.

Sources: https://developers.tron.network/docs/glossary
         https://github.com/tronprotocol/tips/issues/234

The endpoint path, response key and scale all live in config under the project's node_api block,
so a node API change is a config edit rather than a code change.
"""
from __future__ import annotations

import logging

from .base import Http, derive_flow_from_cumulative, json_path_get, point, today

log = logging.getLogger("token_metrics.fetch.tron")

SOURCE = "tron_node"
TIER = 2
KIND = "tron_burn_trx"


class TronNode:
    """Reads BURN_TRX from a TRON node for any project declaring a tron_burn_trx node_api block."""

    def __init__(self, prior_values: dict | None = None):
        self.http = Http(min_interval=0.5)
        self.prior = prior_values or {}

    def _read(self, api: dict) -> tuple[float | None, str]:
        """Try each configured endpoint in turn. Returns (value, detail)."""
        path = api.get("path", "/wallet/getburntrx")
        keys = api.get("response_keys") or ["burnTrxAmount"]
        scale = float(api.get("scale", 1) or 1)
        errors = []
        for base in api.get("endpoints") or []:
            url = base.rstrip("/") + path
            for verb in ("post", "get"):
                try:
                    payload = self.http.post(url) if verb == "post" else self.http.get(url)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{verb.upper()} {url}: {e}")
                    continue
                for key in keys:
                    raw = json_path_get(payload, key)
                    if raw is not None:
                        try:
                            return float(raw) * scale, f"{verb.upper()} {url} -> {key}"
                        except (TypeError, ValueError):
                            continue
                errors.append(f"{verb.upper()} {url}: none of {keys} present in the response")
        return None, "; ".join(errors) or "no endpoints configured"

    def run(self, projects: list[dict], window_days, out):
        when = today()
        for p in projects:
            api = p.get("node_api") or {}
            if api.get("kind") != KIND:
                continue
            name = p["name"]
            metric = api.get("metric", "burn_address_balance")
            value, detail = self._read(api)
            if value is None:
                out.fail(SOURCE, name, f"{metric}: BURN_TRX read failed — {detail}", TIER)
                out.gap(name, metric,
                        reason=f"BURN_TRX node read failed: {detail}",
                        tiers_attempted="2",
                        suggestion="Check the node_api endpoints, path and response_keys in config.py against "
                                   "the TRON node API docs. Do NOT substitute a black-hole address balance — "
                                   "TIP #49 moved the burn out of that address, so it would report the wrong figure.")
                continue
            src = f"{SOURCE}:BURN_TRX"
            out.add(point(name, metric, value, src, TIER, when), SOURCE, name,
                    f"{metric}={value:,.4f} via {detail}", TIER)
            flow = derive_flow_from_cumulative(value, self.prior.get((name, metric)), name,
                                               "gross_burn_tokens", f"{src}:delta", TIER, when)
            if not flow.empty:
                out.add(flow, SOURCE, name, "gross_burn_tokens derived from the BURN_TRX delta", TIER)
