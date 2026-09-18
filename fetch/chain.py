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
import time
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
    # NOT an ERC-20 function. Chainlink's v0.2 staking pools expose the protocol's OWN accounting
    # of staked principal, which is the figure balanceOf can only bound from above. Adding the
    # fragment here is harmless for every other contract: it is only encoded when it is called.
    {"constant": True, "inputs": [], "name": "getTotalPrincipal", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    # Aerodrome-family (ve(3,3)) Minter/RewardsDistributor. Also harmless elsewhere: only encoded
    # when actually called. weekly() is a public state var (Minter's current planned epoch
    # emission); tokensPerWeek(week) is a public mapping (RewardsDistributor's per-week rebase).
    {"constant": True, "inputs": [], "name": "weekly", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "", "type": "uint256"}], "name": "tokensPerWeek", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
]

# Metrics a contract read can produce, by contract kind.
# ALIASED FROM CONFIG, not redeclared. config.destination_disputed() needs the same table to
# answer "which metric does this disputed contract serve", and two copies of it would drift.
KIND_METRIC = config.KIND_METRIC

# Contracts kept for reference, that serve NO metric. A burn executor is the contract release()
# is called on — real, needed for the mechanism and for eth_getCode, and NOT where the tokens end
# up. Reading its balance as "cumulative burned" is what returned 0 for Uniswap while 100k+ UNI a
# day was being burned to the dead address. Declaring the kind keeps the entry in config without
# letting it be mistaken for a destination again.
REFERENCE_ONLY_KINDS = {"burn_executor"}

# Kinds whose figure is read by calling the CONTRACT ITSELF rather than a token balance, and
# which therefore need a separate token for symbol() and decimals().
PRINCIPAL_KINDS = {"stake_principal", "emission_rate_current", "rebase_last_week"}

# WEEK, in seconds — the ve(3,3) / Aerodrome-family epoch length. Used only to compute
# call_arg "last_complete_week_unix"; not a general-purpose constant.
_WEEK_SECONDS = 7 * 86400

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
HOLDER_MUST_HAVE_CODE = {"buyback_fund_balance", "ve_total_supply", "treasury_holding"}


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

    def scaled(self, chain: str, address: str, call: str, *args, decimals_from: str | None = None) -> float:
        """Call `call` on `address`, scaled by decimals().

        decimals_from names a DIFFERENT contract to take decimals() from, for the case where the
        contract holding the figure is not a token. Chainlink's staking pools report
        getTotalPrincipal() in LINK wei and have no decimals() of their own; taking 18 on faith
        would be the same class of assumption this file exists to refuse, so the LINK contract is
        asked instead.
        """
        c = self.erc20(chain, address)
        args = tuple(self.checksum(a) if isinstance(a, str) and a.startswith("0x") else a for a in args)
        raw = getattr(c.functions, call)(*args).call()
        dec_source = self.erc20(chain, decimals_from) if decimals_from else c
        decimals = dec_source.functions.decimals().call()
        return float(raw) / (10 ** int(decimals))


class Chain:
    """Tier 2 adapter. prior_values supplies the last stored figure for cumulative differencing."""

    def __init__(self, prior_values: dict | None = None, prior_dates: dict | None = None,
                 prior_sources: dict | None = None, prior_delta: dict | None = None):
        self.reader = ChainReader()
        self.prior = prior_values or {}
        # When each prior figure was observed. A delta needs an interval, not just a number to
        # subtract — see derive_flow_from_cumulative.
        self.prior_dates = prior_dates or {}
        # What the prior figure MEASURED. A delta across two different addresses is not a flow.
        self.prior_sources = prior_sources or {}
        # The figure to DIFFERENCE against: the last one from an EARLIER DAY, never today's.
        self.prior_delta = prior_delta if prior_delta is not None else (prior_values or {})

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

        # 2. BURN MECHANISM. Two separate questions, and conflating them is what produced a wrong
        # model of Sky that survived every address check this adapter makes.
        #
        # 2a. Is the MODEL itself refuted? An address can be perfectly correct and still be the
        # wrong thing to read, if the protocol does not burn the way we assumed. Sky's zero
        # address was a real address, verified against real docs, and entirely beside the point:
        # Sky burns through a Splitter and an AMM Flapper, never through a dead address.
        mech = config.burn_mechanism(project)
        if spec["kind"] == "burn_address_balance" and mech.get("status") == "refuted":
            out.gap(name, metric,
                    reason=f"the burn MECHANISM is refuted, not merely the address: this project does not "
                           f"burn by {mech.get('model')!r} in the way an address balance could measure. "
                           f"{mech.get('note', '')}".strip(),
                    tiers_attempted="2",
                    suggestion=f"Do not substitute another address — no balance read models this. Establish "
                               f"the real mechanism first; see {mech.get('source_url') or 'the protocol docs'} "
                               f"and OPEN_QUESTIONS for this project.")
            out.unconfigured(SOURCE, name, f"{key}: burn mechanism refuted, address read refused", TIER)
            return False

        # 2b. Is the READ METHOD one this adapter can serve? A protocol-level burn has no address.
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
            # Contracts whose ADDRESS is right but whose role is in doubt — a burn sink we are no
            # longer confident the protocol actually burns to. Read, but never stored as a metric.
            disputed: dict[str, list[str]] = defaultdict(list)
            # A component that was REFUSED (unverified, unreachable chain, no same-chain token)
            # makes the resulting sum partial. Dropping one burn path and reporting the rest as if
            # it were the whole is precisely the understatement this tool exists to prevent.
            refused: dict[str, list[str]] = defaultdict(list)
            for key, spec in contracts.items():
                if not self._gate(p, key, spec, out):
                    continue
                chain, kind = spec["chain"], spec["kind"]
                if kind in REFERENCE_ONLY_KINDS:
                    out.log.append(LogEntry(SOURCE, name, 0, "ok",
                                            f"{key}: {kind}, reference only — no metric read from it", TIER))
                    continue
                # metric_override lets ONE contract's read land under a different metric than
                # its kind's normal mapping — kind->metric is otherwise shared across every
                # project using that kind (treasury_holding alone serves Sky, Maple, NEAR and
                # three GEODNET wallets), so this is how a single project's read is demoted to a
                # cross-check secondary without moving everyone else's.
                metric = spec.get("metric_override") or KIND_METRIC.get(kind)
                if metric is None:
                    out.unconfigured(SOURCE, name, f"{key}: unknown contract kind {kind!r}", TIER)
                    continue
                # A balance read is balanceOf ON THE TOKEN, with this contract as the holder. The
                # token MUST be the deployment on the SAME CHAIN as the holder: calling an Ethereum
                # token address over another chain's RPC returns empty data, which is what made the
                # Unichain reads fail while the identical mainnet path worked.
                read_address = spec["address"]
                holder = None
                # Symbol and decimals normally come from the address being called. For a
                # PRINCIPAL read they cannot: the call is made on the STAKING POOL, which is not
                # an ERC-20 and has neither symbol() nor decimals(). Calling either on it would
                # revert and the read would fail for a reason that has nothing to do with the
                # figure. So the pool is called and the TOKEN is what polices the result.
                # Deliberately NOT initialised to read_address here: the holder branch below
                # REASSIGNS read_address to the token, and capturing it beforehand would pin the
                # symbol check to the holder. It is resolved at the call site instead.
                symbol_address = None
                decimals_address = None
                if kind in PRINCIPAL_KINDS:
                    under = self._underlying_on_chain(contracts, spec, chain)
                    if under is None:
                        out.gap(name, metric,
                                reason=f"{key} is on {chain}, and no ERC-20 token contract is declared on "
                                       f"{chain} to take decimals from. A principal read is scaled by the "
                                       f"TOKEN's decimals because the pool has none.",
                                tiers_attempted="2",
                                suggestion=f"Set `underlying` on {key!r} to a token contract on {chain}.")
                        refused[metric].append(f"{key} ({chain}): no same-chain token for decimals")
                        continue
                    symbol_address = decimals_address = under["address"]
                elif kind in ("burn_address_balance", "buyback_fund_balance", "treasury_holding") \
                        or spec.get("read_method") == "escrow_balance_of":
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
                    ok, actual = self.reader.symbol_matches(chain, symbol_address or read_address,
                                                            spec["expected_symbol"])
                    if not ok:
                        out.fail(SOURCE, name,
                                 f"{key}: symbol check FAILED — {read_address} reports {actual!r}, "
                                 f"config expects {spec['expected_symbol']!r}. Address rejected.", TIER)
                        out.gap(name, metric, reason=f"contract {key!r} symbol mismatch: on-chain {actual!r} vs expected "
                                                     f"{spec['expected_symbol']!r} — the address is wrong or stale",
                                tiers_attempted="2", suggestion="Re-check the address on the protocol's own docs.")
                        refused[metric].append(f"{key} ({chain}): symbol mismatch")
                        continue
                    if holder:
                        value = self.reader.scaled(chain, read_address, "balanceOf", holder)
                    elif decimals_address:
                        # Only the principal path needs a separate decimals source, and the kwarg
                        # is passed ONLY there. Every ordinary read keeps the original signature,
                        # so a reader that has never heard of decimals_from still works.
                        #
                        # call_arg: a call needing an argument computed AT RUN TIME rather than
                        # stored in config, because it is a function of when the run happens.
                        # "last_complete_week_unix" is currently the only one — RewardsDistributor.
                        # tokensPerWeek(week) for the most recently FINISHED week, never the
                        # current one: that week's checkpoint may not have run yet (Minter.
                        # updatePeriod() is permissionless and not on a guaranteed schedule), and
                        # reading it early would return a partial or zero figure that looks like a
                        # measured one.
                        call_args = ()
                        if spec.get("call_arg") == "last_complete_week_unix":
                            # time.time() — genuinely UTC, unlike calling .timestamp() on a
                            # tz-naive pandas Timestamp, which is interpreted in the SYSTEM's
                            # local timezone and would be wrong on any host not set to UTC.
                            now_ts = int(time.time())
                            call_args = (((now_ts // _WEEK_SECONDS) - 1) * _WEEK_SECONDS,)
                        value = self.reader.scaled(chain, read_address,
                                                   spec.get("call") or "totalSupply", *call_args,
                                                   decimals_from=decimals_address)
                    else:
                        value = self.reader.scaled(chain, read_address, spec.get("call") or "totalSupply")
                except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                    out.fail(SOURCE, name, f"{key} ({chain}): {e}", TIER)
                    out.gap(name, metric, reason=f"contract read failed: {e}", tiers_attempted="2",
                            suggestion="Check the RPC endpoints for this chain in .env")
                    refused[metric].append(f"{key} ({chain}): read failed")
                    continue
                parts[metric].append((f"{chain}:{key}", value))
                if spec.get("supply_is_partial"):
                    partial_metrics.add(metric)
                if spec.get("destination_status") == "disputed":
                    disputed[metric].append(key)
                out.log.append(LogEntry(SOURCE, name, 0, "ok",
                                        f"{metric} component {chain}:{key}={value:,.4f}", TIER))

            for metric, missing in refused.items():
                if metric not in parts:
                    out.gap(p["name"], metric,
                            reason=f"every component was refused: {'; '.join(missing)}",
                            tiers_attempted="2",
                            suggestion="Resolve the refused components in config.py; nothing was stored for "
                                       "this metric rather than a partial figure being presented as whole.")
            self._emit_parts(p, parts, partial_metrics, refused, when, out, disputed)

    def _emit_parts(self, project: dict, parts: dict, partial_metrics: set, refused: dict, when, out,
                    disputed: dict | None = None):
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

            # A DISPUTED destination is not a value problem, it is a meaning problem. The read is
            # correct — that address really does hold that much — but the LABEL on it may not be.
            # Storing it as "cumulative burned" would assert something we no longer believe, and a
            # wrong number in the sheet is worse than a gap. So the observation is captured to
            # staging, where it stays available as evidence, and the metric renders n/a with a
            # reason instead of asserting a burn figure we cannot stand behind.
            if disputed and metric in disputed:
                keys = ", ".join(disputed[metric])
                out.stage(name, f"{metric} (disputed destination: {keys})", total, date=when,
                          source=src, tier=TIER,
                          note="Read correctly, but NOT stored as a metric: the contract's role as this "
                               "project's burn destination is disputed. Evidence, not a figure.")
                out.gap(name, metric,
                        reason=f"NOT REPORTED, and deliberately not reported as {total:,.0f}: the "
                               f"destination is disputed. {keys} was read successfully and holds "
                               f"{total:,.4f}, but whether this project's burn actually routes there is "
                               f"an OPEN VERIFICATION QUESTION, not a data gap. Labelling this balance "
                               f"'{metric}' would assert something no longer supported. The observation "
                               f"is on the Staging sheet as evidence.",
                        tiers_attempted="2",
                        suggestion="Settle the destination — see OPEN_QUESTIONS for this project — then "
                                   "either clear destination_status on the contract in config.py, or "
                                   "point the entry at the address the burn really uses.")
                continue

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
            if metric == "burn_address_balance" and (project.get("burn_composition") or {}).get("status") == "contaminated":
                self._flag_contaminated_cumulative(project, total, out, when)
            if metric == "burn_address_balance":
                self._flag_assumed_burn_mechanism(project, out, when)
            if metric == "burn_address_balance" and total == 0.0:
                self._flag_burn_address_never_received(project, components, out, when)

            flow_metric = CUMULATIVE_FLOW.get(metric)
            if flow_metric:
                flow = derive_flow_from_cumulative(total, self.prior_delta.get((name, metric)), name,
                                                   flow_metric, f"{src}:delta", TIER, when,
                                                   prior_date=self.prior_dates.get((name, metric)),
                                                   stock_metric=metric, out=out,
                                                   prior_source=self.prior_sources.get((name, metric)),
                                                   source_base=src)
                if not flow.empty:
                    out.add(flow, SOURCE, name, f"{flow_metric} derived from the summed {metric} delta", TIER)
                    # Where the cumulative is contaminated by a one-off but the FLOW is not, the
                    # flow is also the recurring-programme series. Emitting it under its own name
                    # keeps the demand signal out of a cumulative that is 99.5% a supply event.
                    comp = project.get("burn_composition") or {}
                    if comp.get("flow_is_recurring_only") and flow_metric == "gross_burn_tokens":
                        clean = flow.copy()
                        clean["metric"] = "burn_revenue_funded"
                        clean["source"] = f"{src}:delta:recurring-only"
                        out.add(clean, SOURCE, name,
                                "burn_revenue_funded = the flow, which excludes the one-off by "
                                "construction: it predates every observation held", TIER)
                    if float(flow["value"].iloc[0]) == 0.0:
                        self._flag_unattributable_zero(project, flow_metric, metric, when, out)

    def _flag_contaminated_cumulative(self, project: dict, total: float, out, when):
        """A cumulative burn that is mostly a one-off is not evidence of a recurring programme.

        The number is correct. What is wrong is the use it invites: sitting in a column headed
        "cumulative burned" next to a revenue figure, it reads as the scale of a buyback. For
        Venice that overstates the revenue-funded programme by roughly 200x, because ~99.5% of it
        is a single airdrop burn that will never happen again. Flagged every run rather than
        silently corrected, because the correction is a judgement about what belongs in a ratio.
        """
        name = project["name"]
        comp = project["burn_composition"]
        rec = comp.get("recurring") or {}
        one = comp.get("one_off") or {}
        out.review_item(name, "burn_address_balance", "cumulative_is_mostly_one_off", "stored_flagged",
                        value=total, prior_value=rec.get("tokens_to_date_approx"), date=when,
                        source=f"{SOURCE}: cumulative includes a non-recurring {one.get('what')} "
                               f"({one.get('when')})", tier=TIER)
        out.gap(name, "[data] the cumulative burn is mostly a ONE-OFF, not a recurring programme",
                reason=(f"burn_address_balance reads {total:,.0f}, and that figure is correct — but it is "
                        f"dominated by a NON-RECURRING event: {one.get('what')} burned once in "
                        f"{one.get('when')}. The recurring, revenue-funded programme is about "
                        f"{rec.get('tokens_to_date_approx', 0):,.0f} tokens since {rec.get('since')}. "
                        f"Reading the cumulative as the scale of the buyback overstates it by roughly "
                        f"{total / max(rec.get('tokens_to_date_approx') or 1, 1):,.0f}x. The one-off is a "
                        f"SUPPLY event; only the recurring programme is a demand signal, and it is "
                        f"published separately as burn_revenue_funded. {comp.get('reconciliation', '')}"),
                tiers_attempted="2",
                suggestion=("Use burn_revenue_funded, not the cumulative, in any buyback comparison or "
                            "annualised burn-as-%-of-supply figure. The cumulative cannot be decomposed "
                            "from the address balance alone — a balance carries no history of what "
                            "funded it — so the split comes from the flow, which is clean because the "
                            "one-off predates every observation held. Watch whether the balance actually "
                            "rises: that is what settles the reconciliation question above."))

    def _flag_assumed_burn_mechanism(self, project: dict, out, when):
        """The figure is reported, but the model behind it is not sourced — so say so on the figure.

        Deliberately a flag and not a refusal. Sky's model was refuted by a document, which is a
        fact; the others are merely undocumented, which is a question. Withdrawing four working
        burn figures because one turned out wrong would be its own kind of error, and the sheet
        would lose real numbers to a suspicion. Flagged, and the gap names the specific document
        that would settle it.
        """
        mech = config.burn_mechanism(project)
        if mech.get("status") != "assumed":
            return
        name = project["name"]
        out.review_item(name, "burn_address_balance", "burn_mechanism_assumed", "stored_flagged",
                        value=None, prior_value=None, date=when,
                        source=f"model {mech.get('model')!r} is assumed, not sourced", tier=TIER)
        out.gap(name, "[data] the burn MECHANISM is assumed, not documented",
                reason=(
                    f"This project's burn is read as {mech.get('model')!r}, and that model is ASSUMED "
                    f"rather than sourced to the protocol's own documentation. The figure is still "
                    f"reported — it may well be right — but it is flagged, because an address can be "
                    f"perfectly correct while the claim attached to it is false. That is exactly what "
                    f"happened to Sky: verified address, verified docs, wrong model, and a zero that "
                    f"looked like a finding. {mech.get('note', '')}").strip(),
                tiers_attempted="2",
                suggestion=(
                    f"Read the protocol's own documentation on what happens to repurchased or burned "
                    f"tokens and record it in burn_mechanism, then set status to 'confirmed' with the "
                    f"source URL. Source currently on file: {mech.get('source_url') or 'NONE'}. Two "
                    f"questions settle it: does the protocol TRANSFER tokens to an address nobody "
                    f"controls, and is that address a dead address rather than a contract that merely "
                    f"HOLDS them? A contract balance read as 'cumulative burned' assumes destruction."))

    def _flag_burn_address_never_received(self, project: dict, components, out, when):
        """A burn address holding EXACTLY zero is evidence about the address, not about the burn.

        This is a stock, not a difference: it needs no history to be meaningful. If a single token
        had ever been burned to this address, at any point since the token was deployed, the
        balance would be non-zero today — burn addresses are one-way, nobody withdraws from them.

        So an exact zero says the address has NEVER received anything. For a protocol whose burn
        is a transfer and which is understood to have burned, the likelier reading is that the
        burn does not route here — a wrong destination in config — rather than that the protocol
        has never burned. It is only evidence, not proof: a burn programme that has genuinely
        never fired, or a holder-elected burn nobody has elected, produces the same zero. Which is
        why this is flagged for a human rather than resolved here.
        """
        name = project["name"]
        where = ", ".join(label for label, _ in components)
        out.review_item(name, "burn_address_balance", "burn_address_never_received", "stored_flagged",
                        value=0.0, prior_value=self.prior.get((name, "burn_address_balance")),
                        date=when, source=f"{SOURCE}:{where} holds exactly zero", tier=TIER)
        out.gap(name, "[data] the burn address has NEVER received a single token",
                reason=(
                    f"{where} holds EXACTLY 0. This is a stock, not a differenced flow, so it needs no "
                    f"observation history to mean something: if this project had ever burned to this "
                    f"address, the balance would be non-zero today, because burn addresses are one-way "
                    f"and nobody withdraws from them. Two readings, and the first is the likelier one "
                    f"for a protocol understood to have burned: (b) THE BURN DOES NOT ROUTE HERE — the "
                    f"destination in config is wrong, which is a verification error, not a data gap; "
                    f"(a) the protocol has genuinely never burned to it — which is entirely possible "
                    f"where a burn is holder-elected and nobody has elected, or a programme has not "
                    f"fired. Compare against peers on the same read: a non-zero balance on a single "
                    f"observation is what a working burn destination looks like."),
                tiers_attempted="2",
                suggestion=(
                    "Do not resolve this by assumption in either direction. Check the protocol's own "
                    "documentation for where repurchased or burned tokens are sent, and check the "
                    "token's transfer history for any inbound transfer to this address. If the address "
                    "is wrong, correct it in config.py and re-open its verification — this is the same "
                    "class of error as a transposed or stale address, not a missing feed."))

    def _flag_unattributable_zero(self, project: dict, flow_metric: str, stock_metric: str, when, out):
        """A zero differenced out of a balance read is not a measured zero.

        A balance answers "how much is sitting there now". It cannot answer "did anything move,
        and where did it come from" — so an unchanged balance is consistent with at least three
        different worlds, and the number 0 in the burn column looks identical in all of them.
        Rendering that as a plain zero is the same failure as reporting a skipped backfill as a
        success: no error, no gap, and a figure that reads as measured when it is not.

        The cure is not a better balance read, it is a different kind of read: transfer events
        INTO the burn address, which say what moved, when, and from where.
        """
        name = project["name"]
        burn = {k: v for k, v in (project.get("contracts") or {}).items()
                if v.get("kind") in ("burn_address_balance", "spl_token_account")}
        token = (project.get("contracts") or {}).get("token", {})
        where = "; ".join(f"{k} {v.get('address')} on {v.get('chain')}" for k, v in burn.items()) or "(no address on file)"

        out.review_item(name, flow_metric, "unattributable_zero", "stored_flagged", value=0.0,
                        prior_value=self.prior.get((name, flow_metric)), date=when,
                        source=f"{SOURCE}:balance delta — a zero here is ambiguous, not measured", tier=TIER)
        out.gap(name, f"[data] {flow_metric} is ZERO and a balance read cannot say why",
                reason=(
                    f"{flow_metric} came out at 0 by differencing {stock_metric}, a BALANCE read of "
                    f"{where}. A balance cannot distinguish three different situations, and all three "
                    f"present as 0: (a) no burn occurred; (b) a burn occurred but did not route to the "
                    f"address being watched — where a protocol splits buybacks between burning and "
                    f"distribution, a cycle can run entirely to distribution and leave this balance "
                    f"untouched; (c) the store holds too few observations for the window, because a "
                    f"differenced series only measures the period it has actually been observing — check "
                    f"n_points for {stock_metric} on the Data tab before reading the window at face value. "
                    f"Note also that the balance can only ever OVERSTATE a protocol burn: anyone may send "
                    f"tokens to a dead address, and the read cannot attribute them."),
                tiers_attempted="2",
                suggestion=(
                    f"Only a TRANSFER HISTORY settles this: Transfer events with `to` = the burn address "
                    f"for token {token.get('address') or '(token address not on file)'} on "
                    f"{token.get('chain') or '?'} over the window. Two routes, and they answer different "
                    f"questions. (1) Diagnostic, cheap: eth_getLogs over the last ~30 days says whether "
                    f"ANYTHING moved, which separates (a) from (b) immediately. (2) History: a Dune query "
                    f"on the decoded transfer table, following the GEODNET pattern in "
                    f"dune_queries.gross_burn_tokens — that is what fills the trajectory columns. Until "
                    f"one exists, the zero stays flagged rather than being read as a measured zero."))

