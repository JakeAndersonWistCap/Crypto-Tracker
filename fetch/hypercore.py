"""
fetch/hypercore.py — Hyperliquid's own public info API.

The Assistance Fund balance is the cumulative HYPE burn: since the December 2025 validator vote,
HYPE held there is recognised as permanently burned, and the address has never had a private key
so nothing can leave.

HYPE on HyperCore is not an ERC-20 on any chain the EVM adapter covers, so that route was never
going to work. Hyperliquid publishes the balance through its own documented info endpoint instead:
a plain HTTPS POST, no RPC, no key, no chain. This REPLACES the contract-read approach rather than
supplementing it, which is why Hyperliquid declares no contracts at all.

    POST https://api.hyperliquid.xyz/info
    {"type": "spotClearinghouseState", "user": "0xfefe...fefe"}

Documented at hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint. Tier 1:
a free unauthenticated HTTP API, the same class as DefiLlama and CoinGecko.

The response shape is read through config rather than hardcoded, so a change to it is a config
edit and not a code change.
"""
from __future__ import annotations

import logging

from .base import Http, derive_flow_from_cumulative, json_path_get, parse_number, point, today

log = logging.getLogger("token_metrics.fetch.hypercore")

SOURCE = "hypercore_info"
TIER = 1
KIND = "hypercore_info"


class HyperCoreInfo:
    """Reads a balance from Hyperliquid's info endpoint for any project declaring this kind."""

    def __init__(self, prior_values: dict | None = None):
        self.http = Http(min_interval=0.5)
        self.prior = prior_values or {}

    @staticmethod
    def _pick_balance(payload, api: dict) -> tuple[float | None, str]:
        """Find the balance for the configured coin in the response.

        spotClearinghouseState returns a list of balances, one per asset. The entry is matched on
        its coin symbol, and the amount is read from the first configured field that carries a
        number — so a renamed field is a config edit, not a code change.
        """
        coin = str(api.get("coin", "HYPE"))
        list_path = api.get("balances_path", "balances")
        coin_key = api.get("coin_key", "coin")
        amount_keys = api.get("amount_keys") or ["total", "balance", "amount"]

        balances = json_path_get(payload, list_path)
        if not isinstance(balances, list):
            return None, f"{list_path!r} is not a list in the response (got {type(balances).__name__})"
        available = []
        for entry in balances:
            if not isinstance(entry, dict):
                continue
            available.append(str(entry.get(coin_key)))
            if str(entry.get(coin_key)).upper() != coin.upper():
                continue
            for key in amount_keys:
                value = parse_number(entry.get(key))
                if value is not None:
                    return value, f"{coin} via {list_path}[{coin_key}={coin}].{key}"
            return None, f"{coin} found but none of {amount_keys} held a number: {entry}"
        return None, f"{coin} not present. Coins returned: {', '.join(available[:12]) or 'none'}"

    def run(self, projects: list[dict], window_days, out):
        when = today()
        for p in projects:
            api = p.get("node_api") or {}
            if api.get("kind") != KIND:
                continue
            name = p["name"]
            metric = api.get("metric", "burn_address_balance")
            endpoint = api.get("endpoint")
            request = api.get("request") or {}
            if not endpoint or not request:
                out.unconfigured(SOURCE, name, f"{metric}: endpoint or request body missing in config", TIER)
                continue
            try:
                payload = self.http.post(endpoint, json_body=request)
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                out.fail(SOURCE, name, f"{metric}: {endpoint}: {e}", TIER)
                out.gap(name, metric, reason=f"Hyperliquid info API call failed: {e}", tiers_attempted="1",
                        suggestion=f"Check {endpoint} is reachable. It needs no key and no RPC; if it is down, "
                                   f"the figure is unavailable rather than wrong.")
                continue

            value, detail = self._pick_balance(payload, api)
            if value is None:
                out.fail(SOURCE, name, f"{metric}: {detail}", TIER)
                out.gap(name, metric, reason=f"Hyperliquid info API returned no usable balance: {detail}",
                        tiers_attempted="1",
                        suggestion="Check balances_path, coin_key and amount_keys in config against the "
                                   "documented response shape at hyperliquid.gitbook.io.")
                continue

            src = f"{SOURCE}:{api.get('request', {}).get('type', 'info')}"
            out.add(point(name, metric, value, src, TIER, when), SOURCE, name,
                    f"{metric}={value:,.4f} via {detail}", TIER)
            flow_metric = api.get("derive_flow_metric")
            if flow_metric:
                flow = derive_flow_from_cumulative(value, self.prior.get((name, metric)), name,
                                                   flow_metric, f"{src}:delta", TIER, when)
                if not flow.empty:
                    out.add(flow, SOURCE, name, f"{flow_metric} derived from the {metric} delta", TIER)
