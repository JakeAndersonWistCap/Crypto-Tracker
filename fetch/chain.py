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
from collections import defaultdict

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

# Holder kinds that are SUPPOSED to be deployed contracts, so an empty eth_getCode means the
# address is wrong. A burn or dead address is deliberately NOT in this set: it is an EOA nobody
# controls, empty bytecode is exactly what correct looks like there, and the read is balanceOf on
# the TOKEN with the burn address only ever used as the holder argument.
HOLDER_MUST_HAVE_CODE = {"buyback_fund_balance", "ve_total_supply"}


def holder_should_have_code(spec: dict) -> bool:
    """Should this holder address have deployed bytecode?

    Explicit config wins. Otherwise a canonical dead or zero address is an EOA by definition, and
    anything else is checked only where the kind implies a contract. The default direction is NOT
    to check: a missing check loses a safety net, while a wrong check REJECTS valid data.
    """
    explicit = spec.get("holder_has_code")
    if explicit is not None:
        return bool(explicit)
    address = str(spec.get("address") or "").lower()
    if address in {a.lower() for a in config.BURN_ADDRESSES.values()}:
        return False
    return spec.get("kind") in HOLDER_MUST_HAVE_CODE


# Kinds whose read method must be EXPLICITLY established before anything is read. A vote escrow
# can be a fungible ERC-20 (totalSupply is the staked amount) or an NFT position (totalSupply is a
# COUNT OF POSITIONS, wrong by orders of magnitude and entirely plausible-looking). Assuming
# either is unsafe, so read_method None means refuse.
METHOD_REQUIRED_KINDS = {"ve_total_supply"}


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

    @staticmethod
    def checksum(address: str) -> str:
        """EIP-55 the address. web3.py rejects non-checksummed input, and it rejects it for CALL
        ARGUMENTS too, not just contract addresses — which is how balanceOf(0x...dead) failed live
        while the contract address beside it was fine. Everything that reaches web3 goes through here."""
        from web3 import Web3
        return Web3.to_checksum_address(address)

    def erc20(self, chain: str, address: str):
        return self.web3(chain).eth.contract(address=self.checksum(address), abi=ERC20_ABI)

    def has_code(self, chain: str, address: str) -> bool:
        """Is anything deployed at this address ON THIS CHAIN?

        The right existence check for a contract that is NOT a token. TokenJar and Firepit are
        custom fee-collection contracts with no ERC-20 surface, so symbol() tells you nothing about
        them; eth_getCode does. It also catches an address that is right for one chain and absent
        on another.
        """
        code = self.web3(chain).eth.get_code(self.checksum(address))
        return bool(code) and code not in (b"", b"0x", "0x")

    def symbol_matches(self, chain: str, address: str, expected: str) -> tuple[bool, str]:
        """On-chain self-check. Returns (matches, actual_symbol)."""
        actual = self.erc20(chain, address).functions.symbol().call()
        if isinstance(actual, bytes):
            actual = actual.rstrip(b"\x00").decode("utf-8", "replace")
        return str(actual).strip().lower() == str(expected).strip().lower(), str(actual)

    def scaled(self, chain: str, address: str, call: str, *args) -> float:
        c = self.erc20(chain, address)
        args = tuple(self.checksum(a) if isinstance(a, str) and a.startswith("0x") else a for a in args)
        raw = getattr(c.functions, call)(*args).call()
        decimals = c.functions.decimals().call()
        return float(raw) / (10 ** int(decimals))


class Chain:
    """Tier 2 adapter. prior_values supplies the last stored figure for cumulative differencing."""

    def __init__(self, prior_values: dict | None = None):
        self.reader = ChainReader()
        self.prior = prior_values or {}

    @staticmethod
    def _underlying_on_chain(contracts: dict, spec: dict, chain: str) -> dict | None:
        """The ERC-20 token to call balanceOf on, which must be deployed on `chain`.

        A token address is chain-specific. Picking the single entry named "token" regardless of
        chain is what sent an Ethereum address to a Unichain RPC. Preference order: the contract
        named by `underlying` if it is on this chain, then any erc20_total_supply contract on this
        chain, then nothing — and nothing means refuse, not guess.
        """
        named = contracts.get(spec.get("underlying") or "")
        if named and named.get("address") and named.get("chain") == chain:
            return named
        for candidate in contracts.values():
            if (candidate.get("kind") == "erc20_total_supply" and candidate.get("chain") == chain
                    and candidate.get("address")):
                return candidate
        return None

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

        # 4. LOCK READ METHOD. For a vote escrow, how the figure is read decides whether the cell
        #    holds tokens locked or a count of NFT positions. Never assumed.
        if spec["kind"] in METHOD_REQUIRED_KINDS and not spec.get("read_method"):
            out.gap(name, metric,
                    reason=f"lock read method for {key!r} is NOT ESTABLISHED — a vote escrow can be a fungible "
                           f"ERC-20 (totalSupply is the staked amount) or an NFT position (totalSupply is a "
                           f"COUNT OF POSITIONS, wrong by orders of magnitude). Neither is assumed.",
                    tiers_attempted="2",
                    suggestion=f"Establish whether {key!r} is ERC-20 or ERC-721, then set read_method to "
                               f"'erc20_total_supply' or 'escrow_balance_of' (with `underlying`) and "
                               f"token_standard in config.py.")
            out.unconfigured(SOURCE, name, f"{key}: lock read method not established, read refused", TIER)
            return False

        # 5. VERIFICATION GATE.
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
            # SEVERAL CONTRACTS CAN SERVE ONE METRIC, and they must be SUMMED rather than letting the
            # last read win. Uniswap burns on both mainnet and Unichain and accumulates fees in three
            # places; GEODNET burns on Polygon and Solana; CAKE is a LayerZero OFT with a deployment
            # per chain. Reporting one component as if it were the whole is the exact failure this tool
            # exists to prevent, so every metric accumulates parts and the composition is named in the
            # source string to stay auditable.
            parts: dict[str, list[tuple[str, float]]] = defaultdict(list)
            partial_metrics: set[str] = set()
            # A component that was REFUSED (unverified, unreachable chain, no same-chain token)
            # makes the resulting sum partial. Dropping one burn path and reporting the rest as if
            # it were the whole is precisely the understatement this tool exists to prevent.
            refused: dict[str, list[str]] = defaultdict(list)
            for key, spec in contracts.items():
                if not self._gate(p, key, spec, out):
                    continue
                chain, kind = spec["chain"], spec["kind"]
                metric = KIND_METRIC.get(kind)
                if metric is None:
                    out.unconfigured(SOURCE, name, f"{key}: unknown contract kind {kind!r}", TIER)
                    continue
                # A balance read is balanceOf ON THE TOKEN, with this contract as the holder. The
                # token MUST be the deployment on the SAME CHAIN as the holder: calling an Ethereum
                # token address over another chain's RPC returns empty data, which is what made the
                # Unichain reads fail while the identical mainnet path worked.
                read_address = spec["address"]
                holder = None
                if kind in ("burn_address_balance", "buyback_fund_balance") or spec.get("read_method") == "escrow_balance_of":
                    under = self._underlying_on_chain(contracts, spec, chain)
                    if under is None:
                        out.gap(name, metric,
                                reason=f"{key} is on {chain}, and no ERC-20 token contract is declared on {chain} "
                                       f"to call balanceOf against. The token entry on file is on a different chain, "
                                       f"and a token address is not valid across chains.",
                                tiers_attempted="2",
                                suggestion=f"Add the {chain} deployment of the token to contracts in config.py, "
                                           f"or set `underlying` on {key!r} to a contract that is on {chain}.")
                        out.unconfigured(SOURCE, name, f"{key}: no same-chain token to read balanceOf against", TIER)
                        refused[metric].append(f"{key} ({chain}): no same-chain token")
                        continue
                    holder, read_address = spec["address"], under["address"]

                try:
                    # The holder is usually NOT a token — TokenJar and Firepit are custom
                    # fee-collection contracts with no ERC-20 surface — so its existence is checked
                    # with eth_getCode, not symbol(). symbol() is reserved for the token being read.
                    if holder is not None and holder_should_have_code(spec):
                        try:
                            if not self.reader.has_code(chain, holder):
                                out.fail(SOURCE, name,
                                         f"{key}: nothing deployed at {holder} on {chain} (eth_getCode is empty), "
                                         f"and a {spec['kind']} holder is supposed to be a contract. "
                                         f"Address rejected.", TIER)
                                out.gap(name, metric,
                                        reason=f"contract {key!r} has no deployed bytecode at {holder} on {chain} — "
                                               f"the address is wrong for this chain",
                                        tiers_attempted="2",
                                        suggestion="Re-check the address, and which chain it belongs to, on the "
                                                   "protocol's own docs.")
                                refused[metric].append(f"{key} ({chain}): no deployed bytecode")
                                continue
                        except Exception as e:  # noqa: BLE001 — a node without eth_getCode must not block the read
                            log.debug("%s: eth_getCode unavailable (%s), continuing", key, e)
                    ok, actual = self.reader.symbol_matches(chain, read_address, spec["expected_symbol"])
                    if not ok:
                        out.fail(SOURCE, name,
                                 f"{key}: symbol check FAILED — {read_address} reports {actual!r}, "
                                 f"config expects {spec['expected_symbol']!r}. Address rejected.", TIER)
                        out.gap(name, metric, reason=f"contract {key!r} symbol mismatch: on-chain {actual!r} vs expected "
                                                     f"{spec['expected_symbol']!r} — the address is wrong or stale",
                                tiers_attempted="2", suggestion="Re-check the address on the protocol's own docs.")
                        refused[metric].append(f"{key} ({chain}): symbol mismatch")
                        continue
                    value = (self.reader.scaled(chain, read_address, "balanceOf", holder) if holder
                             else self.reader.scaled(chain, read_address, spec.get("call", "totalSupply")))
                except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                    out.fail(SOURCE, name, f"{key} ({chain}): {e}", TIER)
                    out.gap(name, metric, reason=f"contract read failed: {e}", tiers_attempted="2",
                            suggestion="Check the RPC endpoints for this chain in .env")
                    refused[metric].append(f"{key} ({chain}): read failed")
                    continue
                parts[metric].append((f"{chain}:{key}", value))
                if spec.get("supply_is_partial"):
                    partial_metrics.add(metric)
                out.log.append(LogEntry(SOURCE, name, 0, "ok",
                                        f"{metric} component {chain}:{key}={value:,.4f}", TIER))

            for metric, missing in refused.items():
                if metric not in parts:
                    out.gap(p["name"], metric,
                            reason=f"every component was refused: {'; '.join(missing)}",
                            tiers_attempted="2",
                            suggestion="Resolve the refused components in config.py; nothing was stored for "
                                       "this metric rather than a partial figure being presented as whole.")
            self._emit_parts(p, parts, partial_metrics, refused, when, out)

    def _emit_parts(self, project: dict, parts: dict, partial_metrics: set, refused: dict, when, out):
        """Emit one figure per metric, summing every contract that served it."""
        name = project["name"]
        for metric, components in parts.items():
            total = sum(v for _, v in components)
            if len(components) == 1:
                label, _ = components[0]
                src = f"{SOURCE}:{label}"
                detail = f"{metric}={total:,.4f}"
            else:
                src = f"{SOURCE}:sum(" + "+".join(label for label, _ in components) + ")"
                composition = " + ".join(f"{label} {v:,.4f}" for label, v in components)
                detail = f"{metric}={total:,.4f} from {len(components)} components: {composition}"

            missing = refused.get(metric) or []
            is_partial = (metric in partial_metrics or bool(missing)
                          or (metric == "total_supply" and project.get("supply_is_partial")))
            if is_partial:
                src += ":PARTIAL"
                detail += " [PARTIAL]"
                reason = (f"{len(missing)} component(s) refused: {'; '.join(missing)}" if missing
                          else project.get("supply_partial_reason") or "not every component is known")
                out.review_item(name, metric, "supply_partial", "stored_flagged", value=total,
                                prior_value=self.prior.get((name, metric)), date=when, source=src, tier=TIER)
                out.gap(name, metric,
                        reason=f"figure is PARTIAL — summed over {len(components)} known component(s) only. {reason}",
                        tiers_attempted="2",
                        suggestion="Add the remaining components to contracts in config.py, or rely on the "
                                   "self-reported figure. The sheet labels this figure partial either way.")

            out.add(point(name, metric, total, src, TIER, when), SOURCE, name, detail, TIER)

            flow_metric = CUMULATIVE_FLOW.get(metric)
            if flow_metric:
                flow = derive_flow_from_cumulative(total, self.prior.get((name, metric)), name,
                                                   flow_metric, f"{src}:delta", TIER, when)
                if not flow.empty:
                    out.add(flow, SOURCE, name, f"{flow_metric} derived from the summed {metric} delta", TIER)

