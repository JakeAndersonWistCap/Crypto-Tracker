"""
fetch/solana.py — one Solana JSON-RPC read: an SPL token account's balance. Added 2026-09-28.

WHY IT EXISTS. GEODNET's burns moved to Solana: its burn token account
5SBfxBdqsCM1SJZGQkf9Y74EFmUfzs8LGDjBZUjZGnED (mint 7JA5...mKHu, owned by the incinerator)
grew ~50-55K GEOD/day while the Polygon dead address sat still, so a Polygon-only burn read
reported burns moving chain as a quiet period. fetch/chain.py sums this balance with the
Polygon one into burn_address_balance.

ONE CALL PER ACCOUNT PER RUN: getTokenAccountBalance, no key. The raw integer amount is divided
by 10^decimals exactly (Decimal), so a 9-decimal balance is not rounded before it is stored.

429 IS RETRIED BRIEFLY, THEN THE NEXT ENDPOINT. The public mainnet-beta endpoint rate-limits
hard (Jake's probe: 5 older transactions unreadable on 429). A short exponential retry (2s, 4s),
a Retry-After longer than SOLANA_MAX_RETRY_WAIT_S is not waited, and then the next endpoint in
config.solana_rpc_endpoints() is tried. Every endpoint failing raises, with each one's error.
"""
from __future__ import annotations

import urllib.parse
from decimal import Decimal

import config
from .base import Http

SOLANA_MAX_RETRY_WAIT_S = 10.0


class SolanaRPC:
    def __init__(self, endpoints: list[str] | None = None, http: Http | None = None):
        self.endpoints = endpoints or config.solana_rpc_endpoints()
        self.http = http or Http(retries=2, max_retry_wait=SOLANA_MAX_RETRY_WAIT_S)

    def token_account_balance(self, address: str) -> tuple[float, int, int, str]:
        """(value, raw amount, decimals, host that answered) for an SPL token account."""
        errors = []
        for url in self.endpoints:
            host = urllib.parse.urlsplit(url).netloc
            try:
                j = self.http.post(url, {"jsonrpc": "2.0", "id": 1, "method": "getTokenAccountBalance",
                                         "params": [address, {"commitment": "finalized"}]})
            except Exception as e:  # noqa: BLE001 — try the next endpoint
                errors.append(f"{host}: {e}")
                continue
            if not isinstance(j, dict) or "error" in j or "result" not in j:
                errors.append(f"{host}: {(j or {}).get('error') if isinstance(j, dict) else j}")
                continue
            v = (j["result"] or {}).get("value") or {}
            try:
                raw, dec = int(v["amount"]), int(v["decimals"])
            except (KeyError, TypeError, ValueError):
                errors.append(f"{host}: unexpected shape {str(v)[:120]}")
                continue
            return float(Decimal(raw) / (Decimal(10) ** dec)), raw, dec, host
        raise RuntimeError("no Solana RPC answered getTokenAccountBalance — " + "; ".join(errors))
