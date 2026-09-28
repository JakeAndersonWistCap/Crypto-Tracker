"""
fetch/etherscan_supply.py — tier 1: Ethereum's cumulative burn and supply from Etherscan's
stats module (action=ethsupply2). Added 2026-09-28 (A9).

WHY. Ethereum's burn was DefiLlama burned-fee revenue divided by each day's price, and its
issuance waited on beaconcha.in, whose free quota is spent until 2026-10-01. ethsupply2 answers
both from one keyed call on Etherscan's free tier (ETHERSCAN_API_KEY, the key the explorer log
scans already use):

    EthSupply      Ether before staking rewards are added and EIP-1559 burns subtracted
    Eth2Staking    ETH minted as consensus-layer staking rewards
    BurntFees      cumulative ETH burned by EIP-1559
    WithdrawnTotal ETH withdrawn from the beacon chain (not used)
All four in wei. Stored:

    burn_cumulative_tokens = BurntFees / 1e18                               (stock)
    total_supply_protocol  = (EthSupply + Eth2Staking - BurntFees) / 1e18   (stock)
    gross_burn_tokens      = d(burn_cumulative_tokens)                      (flow)
and _derive_issuance takes gross_issuance_tokens = d(total_supply_protocol) + burn, which is
d(EthSupply + Eth2Staking): what the protocol minted, independent of beaconcha.in.

Field meanings: Etherscan's API reference, "Get Total Supply of Ether 2"
(docs.etherscan.io/api-reference/endpoint/ethsupply2), read 2026-09-28 via search — the docs
host is not reachable from the sandbox that wrote this; check_offline_items etherscan_ethsupply2
prints the live response.

THE KEY NEVER REACHES A LOG. It goes as a query parameter; every message names the host only.
"""
from __future__ import annotations

import logging
import os

import config

from .base import Http, derive_flow_from_cumulative, point, today

log = logging.getLogger("token_metrics.fetch.etherscan_supply")

SOURCE = "etherscan"
TIER = 1
HOST = "api.etherscan.io"
URL = f"https://{HOST}/v2/api"
WEI = 1e18


def _scrub(text, key: str) -> str:
    return str(text).replace(key, "***") if key else str(text)


class EtherscanSupply:
    """One ethsupply2 call per project declaring an `etherscan_supply` block (Ethereum only)."""

    def __init__(self, prior_dates: dict | None = None, prior_delta: dict | None = None):
        self.http = Http(min_interval=0.25)
        self.prior_dates = prior_dates or {}
        self.prior_delta = prior_delta or {}

    def run(self, projects: list[dict], window_days, out):
        when = today()
        for p in projects:
            spec = p.get("etherscan_supply")
            if not spec:
                continue
            name = p["name"]
            key = os.environ.get(spec.get("key_env", "ETHERSCAN_API_KEY"), "").strip()
            metrics = (spec["burn_metric"], spec["supply_metric"])
            if not key:
                for m in metrics:
                    out.unconfigured(SOURCE, name, f"{m}: no {spec['key_env']} in .env", TIER)
                continue
            try:
                body = self.http.get(URL, params={"chainid": spec.get("chainid", 1),
                                                  "module": "stats", "action": "ethsupply2",
                                                  "apikey": key})
            except Exception as e:  # noqa: BLE001
                msg = _scrub(e, key)
                for m in metrics:
                    out.fail(SOURCE, name, f"{m}: {HOST} ethsupply2 failed — {msg}", TIER)
                    out.gap(name, m, reason=f"{HOST} ethsupply2 did not answer: {msg}",
                            tiers_attempted="1",
                            suggestion="Run check_offline_items.py etherscan_ethsupply2.")
                continue
            res = body.get("result") if isinstance(body, dict) else None
            if str((body or {}).get("status")) != "1" or not isinstance(res, dict):
                # "NOTOK" with a text result is how Etherscan refuses: a rate limit, an invalid
                # key, or an endpoint outside the key's plan. The text says which.
                why = _scrub(f"status {body.get('status')!r}, message {body.get('message')!r}, "
                             f"result {str(res)[:160]!r}" if isinstance(body, dict) else body, key)
                for m in metrics:
                    out.fail(SOURCE, name, f"{m}: {HOST} ethsupply2 refused — {why}", TIER)
                    out.gap(name, m, reason=f"{HOST} ethsupply2 refused: {why}",
                            tiers_attempted="1",
                            suggestion=("If the message names a plan (API Pro), ethsupply2 is not "
                                        "on the free tier for this key and the DefiLlama-derived "
                                        "burn stays primary; otherwise it is a rate limit and the "
                                        "next run retries."))
                continue
            try:
                eth, stake, burnt = (int(res[f]) for f in ("EthSupply", "Eth2Staking", "BurntFees"))
            except (KeyError, TypeError, ValueError) as e:
                for m in metrics:
                    out.fail(SOURCE, name, f"{m}: ethsupply2 fields did not parse ({e}); keys "
                                           f"{sorted(res)}. NOTHING STORED.", TIER)
                continue
            burn, supply = burnt / WEI, (eth + stake - burnt) / WEI
            src = f"{SOURCE}:ethsupply2"
            out.add(point(name, spec["burn_metric"], burn, f"{src}.BurntFees", TIER, when), SOURCE,
                    name, f"{spec['burn_metric']}={burn:,.4f} ETH (BurntFees, cumulative)", TIER)
            out.add(point(name, spec["supply_metric"], supply, f"{src}.supply", TIER, when), SOURCE,
                    name, f"{spec['supply_metric']}={supply:,.4f} ETH = EthSupply {eth / WEI:,.0f} "
                          f"+ Eth2Staking {stake / WEI:,.0f} - BurntFees {burn:,.0f}", TIER)
            flow = derive_flow_from_cumulative(
                burn, self.prior_delta.get((name, spec["burn_metric"])), name, "gross_burn_tokens",
                config.mark_source(f"{src}.BurntFees", "delta"), TIER, when,
                prior_date=self.prior_dates.get((name, spec["burn_metric"])),
                stock_metric=spec["burn_metric"], out=out)
            if not flow.empty:
                out.add(flow, SOURCE, name,
                        "gross_burn_tokens = d(BurntFees) — the primary burn; the DefiLlama "
                        "revenue/price derivation is the cross-check", TIER)
