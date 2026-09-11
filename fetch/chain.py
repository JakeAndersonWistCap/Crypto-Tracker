"""
fetch/chain.py — tier 2: contract reads via web3.py against free public RPC endpoints.

This layer removes most of the Dune dependency and is cheaper and more durable than anything
else here. It covers:
  * cumulative burn      — balance of the burn address (and the flow derived by differencing)
  * lock rate            — totalSupply on the vote-escrow contract (veAERO, vePENDLE, stkAAVE)
  * buyback fund balance — balance of the buyback contract
  * circulating / total supply where the token contract is authoritative

A contract read gives point-in-time state, not history. Month-by-month history for the
trajectory columns comes from tier 4; once the store holds that history, daily tier 2
snapshots keep it current.

ADDRESS SAFETY — two independent guards, because a wrong address that still returns a
plausible number is the dangerous failure:

  1. Verification gate. An address whose config entry has verified=None was not checked
     against the protocol's own documentation. By default it is REFUSED and written to the
     Gap Report. TOKEN_METRICS_ALLOW_UNVERIFIED=1 opts in; every value it produces is then
     flagged address_unverified in the Review Queue.
  2. On-chain symbol check. Before any value is accepted, symbol() is called and compared
     with expected_symbol from config. A mismatch rejects the address outright, whatever the
     verification gate said. This catches a transposed or stale address before a plausible
     wrong number reaches the sheet.
"""
from __future__ import annotations

import logging
import os

import config

from .base import LogEntry, derive_flow_from_cumulative, point, today

log = logging.getLogger("token_metrics.fetch.chain")

SOURCE = "chain"
TIER = 2

# Minimal ERC-20 surface. totalSupply/balanceOf return the figure; symbol/decimals police it.
ERC20_ABI = [
    {"constant": True, "inputs": [], "name": "totalSupply", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
]

# Metrics a contract read can produce, by contract kind.
KIND_METRIC = {
    "erc20_total_supply": "total_supply",
    "burn_address_balance": "burn_address_balance",
    "ve_total_supply": "locked_tokens",
    "buyback_fund_balance": "buyback_fund_balance",
    # Solana kinds. Declared so the gap report can name them precisely; the EVM adapter refuses
    # them at the chain-coverage guard rather than failing obscurely.
    "spl_mint": "total_supply",
    "spl_token_account": "burn_address_balance",
}

# A cumulative stock that also yields a flow once differenced against the prior observation.
CUMULATIVE_FLOW = {
    "burn_address_balance": "gross_burn_tokens",
}

# Burn read methods this adapter can serve. Everything else is refused with an explanation,
# because the two burn mechanisms are NOT interchangeable:
#   transfer       tokens move to an address no one controls -> readable as a balance
#   protocol_level supply destroyed with no transfer -> NOT readable; reading a dead address
#                  here returns other people's discarded tokens, not the protocol burn
READABLE_BURN_METHODS = {"transfer", None}


def allow_unverified() -> bool:
    return os.environ.get("TOKEN_METRICS_ALLOW_UNVERIFIED", "").strip() in ("1", "true", "yes")


def rpc_endpoints(chain: str) -> list[str]:
    """Per-chain endpoint list: RPC_<CHAIN> in .env (comma-separated) overrides the defaults."""
    env = os.environ.get(f"RPC_{chain.upper()}", "").strip()
    if env:
        return [u.strip() for u in env.split(",") if u.strip()]
    return list(config.DEFAULT_RPC.get(chain, []))


class ChainReader:
    """Holds one working web3 connection per chain, trying the fallback list in order."""

    def __init__(self):
        self._w3: dict[str, object] = {}
        self._failed: dict[str, str] = {}

    def web3(self, chain: str):
        if chain in self._w3:
            return self._w3[chain]
        if chain in self._failed:
            raise RuntimeError(self._failed[chain])
        try:
            from web3 import HTTPProvider, Web3
        except ImportError as e:  # noqa: BLE001
            self._failed[chain] = f"web3 not installed: {e}"
            raise RuntimeError(self._failed[chain]) from e
        endpoints = rpc_endpoints(chain)
        if not endpoints:
            self._failed[chain] = f"no RPC endpoint configured for chain {chain!r} (set RPC_{chain.upper()} in .env)"
            raise RuntimeError(self._failed[chain])
        errors = []
        for url in endpoints:
            try:
                w3 = Web3(HTTPProvider(url, request_kwargs={"timeout": 30}))
                if w3.is_connected():
                    log.info("chain %s connected via %s", chain, url)
                    self._w3[chain] = w3
                    return w3
                errors.append(f"{url}: not connected")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{url}: {e}")
        self._failed[chain] = f"all RPC endpoints failed for {chain}: {'; '.join(errors)}"
        raise RuntimeError(self._failed[chain])

    def erc20(self, chain: str, address: str):
        from web3 import Web3
        return self.web3(chain).eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)

    def symbol_matches(self, chain: str, address: str, expected: str) -> tuple[bool, str]:
        """On-chain self-check. Returns (matches, actual_symbol)."""
        actual = self.erc20(chain, address).functions.symbol().call()
        if isinstance(actual, bytes):
            actual = actual.rstrip(b"\x00").decode("utf-8", "replace")
        return str(actual).strip().lower() == str(expected).strip().lower(), str(actual)

    def scaled(self, chain: str, address: str, call: str, *args) -> float:
        c = self.erc20(chain, address)
        raw = getattr(c.functions, call)(*args).call()
        decimals = c.functions.decimals().call()
        return float(raw) / (10 ** int(decimals))


class Chain:
    """Tier 2 adapter. prior_values supplies the last stored figure for cumulative differencing."""

    def __init__(self, prior_values: dict | None = None):
        self.reader = ChainReader()
        self.prior = prior_values or {}

    def _gate(self, project: dict, key: str, spec: dict, out) -> bool:
        """Every guard that must pass before an address is read. Returns True only if all do."""
        name = project["name"]
        metric = KIND_METRIC.get(spec["kind"], key)

        # 1. AMBIGUOUS ADDRESS. Two or more candidates circulate and we have not established which
        #    is correct. Picking one on a guess would silently poison every figure downstream.
        if spec.get("ambiguous"):
            cands = spec.get("candidates") or []
            mech = project.get("burn_read_note", "") if spec["kind"] == "burn_address_balance" else ""
            out.gap(name, metric,
                    reason=(f"contract {key!r} is AMBIGUOUS — {len(cands)} addresses circulate publicly and "
                            f"none is established as correct, so none is read. {mech}").strip(),
                    tiers_attempted="2",
                    suggestion=f"Resolve from {spec.get('source_url') or 'the protocol docs'} and replace the "
                               f"candidates list with the single confirmed address. Candidates: "
                               f"{', '.join(cands)}")
            out.unconfigured(SOURCE, name, f"{key}: ambiguous address, read refused", TIER)
            return False

        # 2. BURN MECHANISM. A protocol-level burn has no address to read at all.
        method = project.get("burn_read_method")
        if spec["kind"] == "burn_address_balance" and method not in READABLE_BURN_METHODS:
            out.gap(name, metric,
                    reason=f"burn is {method!r}, not a transfer — there is no address balance to read. "
                           f"{project.get('burn_read_note', '')}".strip(),
                    tiers_attempted="2",
                    suggestion="Use a chain-data or dashboard source: add a sources.yaml entry for this metric.")
            out.unconfigured(SOURCE, name, f"{key}: burn_read_method={method}, address read refused", TIER)
            return False

        # 3. CHAIN COVERAGE. The EVM adapter cannot read Solana, Tron or HyperCore.
        chain = spec.get("chain")
        if chain not in config.EVM_CHAINS:
            out.gap(name, metric,
                    reason=f"contract {key!r} is on {chain!r}, which the EVM adapter does not cover",
                    tiers_attempted="2",
                    suggestion=f"Needs a {chain} adapter, or use the protocol's own dashboard via sources.yaml.")
            out.unconfigured(SOURCE, name, f"{key}: chain {chain!r} not covered by the EVM adapter", TIER)
            return False

        # 4. VERIFICATION GATE.
        if spec.get("verified"):
            return True
        if not allow_unverified():
            out.gap(name, metric,
                    reason=f"contract address for {key!r} is NOT verified against the protocol's own docs",
                    tiers_attempted="2",
                    suggestion=f"Confirm {spec['address']} on {spec.get('source_url') or 'the protocol docs'}, "
                               f"then set verified to the date checked in config.py. "
                               f"Or set TOKEN_METRICS_ALLOW_UNVERIFIED=1 to read it anyway (values get flagged).")
            out.unconfigured(SOURCE, name, f"{key}: address unverified, read skipped", TIER)
            return False
        out.review_item(name, metric, "address_unverified", "stored_flagged",
                        value=None, source=f"{SOURCE}:{spec['address'][:10]}", tier=TIER)
        return True

    def run(self, projects: list[dict], window_days, out):
        when = today()
        for p in projects:
            name = p["name"]
            contracts = p.get("contracts") or {}
            if not contracts:
                continue
            token = contracts.get("token")
            # A project can have SEVERAL burn paths — Uniswap burns on mainnet and Unichain, GEODNET
            # on Polygon and Solana. Each is read separately and they are SUMMED into one burn figure:
            # reporting only one path understates the total, which is the exact failure this tool exists
            # to prevent. Components are named in the source string so the composition stays auditable.
            burn_parts: list[tuple[str, float]] = []
            for key, spec in contracts.items():
                if not self._gate(p, key, spec, out):
                    continue
                chain, kind = spec["chain"], spec["kind"]
                metric = KIND_METRIC.get(kind)
                if metric is None:
                    out.unconfigured(SOURCE, name, f"{key}: unknown contract kind {kind!r}", TIER)
                    continue
                # A burn-address balance is a balanceOf ON THE TOKEN, holding the burn address.
                read_address = spec["address"]
                holder = None
                if kind in ("burn_address_balance", "buyback_fund_balance"):
                    if not token:
                        out.gap(name, metric, reason=f"{key} needs the token contract to call balanceOf, none declared",
                                tiers_attempted="2", suggestion="Add a 'token' entry to contracts in config.py")
                        continue
                    holder, read_address = spec["address"], token["address"]
                try:
                    ok, actual = self.reader.symbol_matches(chain, read_address, spec["expected_symbol"])
                    if not ok:
                        out.fail(SOURCE, name,
                                 f"{key}: symbol check FAILED — {read_address} reports {actual!r}, "
                                 f"config expects {spec['expected_symbol']!r}. Address rejected.", TIER)
                        out.gap(name, metric, reason=f"contract {key!r} symbol mismatch: on-chain {actual!r} vs expected "
                                                     f"{spec['expected_symbol']!r} — the address is wrong or stale",
                                tiers_attempted="2", suggestion="Re-check the address on the protocol's own docs.")
                        continue
                    value = (self.reader.scaled(chain, read_address, "balanceOf", holder) if holder
                             else self.reader.scaled(chain, read_address, spec.get("call", "totalSupply")))
                except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                    out.fail(SOURCE, name, f"{key} ({chain}): {e}", TIER)
                    out.gap(name, metric, reason=f"contract read failed: {e}", tiers_attempted="2",
                            suggestion="Check the RPC endpoints for this chain in .env")
                    continue
                src = f"{SOURCE}:{chain}:{key}"
                if metric == "burn_address_balance":
                    burn_parts.append((f"{chain}:{key}", value))
                    out.log.append(LogEntry(SOURCE, name, 0, "ok", f"burn path {chain}:{key}={value:,.4f}", TIER))
                    continue
                out.add(point(name, metric, value, src, TIER, when), SOURCE, name, f"{key}={value:,.4f}", TIER)

            self._emit_burn(name, burn_parts, when, out)

    def _emit_burn(self, name: str, parts: list[tuple[str, float]], when, out):
        """Sum every burn path into one cumulative figure, then derive the period flow from it."""
        if not parts:
            return
        total = sum(v for _, v in parts)
        composition = " + ".join(f"{label} {v:,.4f}" for label, v in parts)
        src = f"{SOURCE}:sum(" + "+".join(label for label, _ in parts) + ")"
        out.add(point(name, "burn_address_balance", total, src, TIER, when), SOURCE, name,
                f"burn_address_balance={total:,.4f} from {len(parts)} path(s): {composition}", TIER)
        flow = derive_flow_from_cumulative(total, self.prior.get((name, "burn_address_balance")), name,
                                           "gross_burn_tokens", f"{src}:delta", TIER, when)
        if not flow.empty:
            out.add(flow, SOURCE, name, f"gross_burn_tokens derived from the summed burn delta", TIER)
