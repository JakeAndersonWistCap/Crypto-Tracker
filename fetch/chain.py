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
import re
from urllib.parse import urlparse
import time
from collections import defaultdict

import pandas as pd

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
    # ===== veAERO's PERMANENT TRANCHE. Added 2026-09-23 after the read failed for want of it.
    # "The function 'permanentLockBalance' was not found in this contract's abi" — the ADDRESS
    # was right (0xeBf418Fe…, VotingEscrow on Base) and the fragment was simply absent. Taken
    # from Aerodrome's own contracts/VotingEscrow.sol line 566, `uint256 public
    # permanentLockBalance;`, which compiles to a no-input uint256 getter. NOT guessed from the
    # name: a public state variable and a view function with the same name would encode
    # identically here, but only the source says which exists.
    #
    # ** THIS ONE IS LOAD-BEARING, unlike the harmless-if-unused fragments above. **
    # BalanceLogicLibrary.supplyAt returns `bias + permanentLockBalance`, so without it
    # avg_lock_duration_days has no way to remove the non-decaying tranche — and the derivation
    # REFUSES rather than falling back to the naive ratio, which would report permanent locks as
    # nearly-four-year ones. A missing fragment therefore gaps the metric; it never degrades it.
    {"constant": True, "inputs": [], "name": "permanentLockBalance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    # ===== veAERO's OWN ACCOUNTING OF WHAT IT HAS LOCKED. Added 2026-09-23. =====
    # `uint256 public supply` (VotingEscrow.sol line 556), incremented at 768 and decremented at
    # 907. NOT the same as AERO.balanceOf(escrow): at block 51,693,612 they differed by
    # 59,653,709.90 AERO — the escrow HOLDS 990,636,288.30 and has LOCKED 1,050,289,998.20.
    # The bias is computed against the locked amount, so this is the denominator the lock
    # duration derives from.
    {"constant": True, "inputs": [], "name": "supply", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    # tailEmissionRate() is BASIS POINTS, not a token amount. Scaling it by the token's decimals
    # would turn 67 bps into a vanishing fraction and the emission into zero. Read through
    # Reader.raw(), never scaled().
    {"constant": True, "inputs": [], "name": "tailEmissionRate", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    # ** THE FUNCTION WAS RIGHT; THE ABI DID NOT CARRY IT. ** The run of 2026-09-22 reported
    # "cooldownDuration was not found in this contract's abi" for Pendle, which reads as "we
    # guessed the name". We did not: sPENDLE declares
    #     function cooldownDuration() external view returns (uint24);
    # in contracts/interfaces/IPStakedPendle.sol, backed by `uint24 public cooldownDuration` in
    # contracts/LiquidityMining/sPendle/StakedPendle.sol — read from Pendle's own repository,
    # pendle-finance/pendle-core-v2-public, on 2026-09-22. The contract at
    # 0x999999999991E178D52Cd95AFd4b00d066664144 is that deployment: deployments/1-core.json
    # names it under "sPendle".
    #
    # uint24, NOT uint256, and the width is taken from the interface rather than assumed. It
    # returns SECONDS, so it goes through Reader.raw_call (unscaled) — dividing a 14-day notice
    # period by 10^18 gives 1.2e-12 days, which reads as zero and is believable.
    {"constant": True, "inputs": [], "name": "cooldownDuration", "outputs": [{"name": "", "type": "uint24"}], "type": "function"},
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
# bridged_representation: a token deployment on a DESTINATION chain whose supply is already
# counted in the home chain's totalSupply. Under a lock-and-mint bridge (LayerZero ProxyOFT and
# friends) bridging out LOCKS the home-chain tokens rather than burning them, so the home read
# already includes everything represented elsewhere and ADDING the destination's totalSupply
# double-counts. Declaring the kind keeps the address on file — it is real, and it matters if the
# bridge model ever changes — without letting it be summed on the intuition that more chains means
# a more complete figure. Exactly the role burn_executor plays for a burn address.
REFERENCE_ONLY_KINDS = {"burn_executor", "bridged_representation"}

# Kinds whose figure is read by calling the CONTRACT ITSELF rather than a token balance, and
# which therefore need a separate token for symbol() and decimals().
# Kinds whose figure is read BY CALLING THE HOLDER, and scaled by the UNDERLYING TOKEN's
# decimals. The holder is not an ERC-20 — a staking pool or a veNFT escrow — so symbol() and
# decimals() on it would revert, and the read would fail for a reason unrelated to the figure.
#
# ve_voting_power / permanent_locked joined 2026-09-23. veAERO is ERC-721 and BOTH figures are
# denominated in AERO: VotingEscrow.sol computes bias as `slope * (end - now)` where
# `slope = amount / MAXTIME`, so the units are locked-token units throughout.
PRINCIPAL_KINDS = {"stake_principal", "emission_rate_current", "rebase_last_week",
                   "ve_voting_power", "permanent_locked", "ve_locked_supply"}

# WEEK, in seconds — the ve(3,3) / Aerodrome-family epoch length. Used only to compute
# call_arg "last_complete_week_unix"; not a general-purpose constant.
_WEEK_SECONDS = 7 * 86400

# A cumulative stock that also yields a flow once differenced against the prior observation.
# ALIASED FROM CONFIG, not redeclared — config.series_granularity needs the same table to know a
# daily balance read is what feeds gross_burn_tokens, and two copies of it would drift.
# The LOOKUP goes through config.cumulative_flow_for, which consults the project first: a
# buyback fund's balance means different things on different projects, and a global entry would
# derive a buyback figure from whatever each one's balance happened to do.
CUMULATIVE_FLOW = config.CUMULATIVE_FLOW

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
#
# ===== stake_underlying JOINED THEM 2026-09-23, AND IT IS THE SAME HAZARD. =====
# ** A stake_underlying ENTRY WITH NO read_method IS NOT A BALANCE READ — IT IS A totalSupply
# ** READ WEARING THE ASSETS NAME. ** Pendle's spendle_underlying was added without one, and the
# generic path would have called sPENDLE.totalSupply() — 30,310,807 SHARES — and stored it as
# locked_tokens, the ASSETS column. That is precisely the shares-for-assets confusion the entry
# was created to fix, arriving through the door left open behind it.
#
# It was caught only by accident: the symbol gate rejected the holder for reporting 'sPENDLE'
# where 'PENDLE' was expected. ** THAT GATE IS A CHECK ON THE ADDRESS, NOT ON THE READ METHOD,
# and it happens to fail here only because the holder and the token are different contracts.**
# Where they are the same address — a vault that is its own underlying — the symbol would match,
# the entry would pass, and the wrong quantity would be stored with nothing to notice.
#
# So the requirement is declared on the KIND, where it holds for every entry, rather than relying
# on a symbol collision to expose the next one.
METHOD_REQUIRED_KINDS = {"ve_total_supply", "stake_underlying"}

# Kinds that claim to measure a burn. A REFUTED MECHANISM REFUSES ALL OF THEM — the question a
# refutation answers is "does this project burn the way we assumed", and the answer does not
# depend on whether we were planning to read a balance or an event log. burn_transfer_logs was
# added on 2026-09-22 and the gate was widened in the same change; scoping the refutation check
# to balance reads alone would have let a refuted mechanism through the new door.
BURN_READ_KINDS = {"burn_address_balance", "burn_transfer_logs"}


def allow_unverified() -> bool:
    return os.environ.get("TOKEN_METRICS_ALLOW_UNVERIFIED", "").strip() in ("1", "true", "yes")


def rpc_host(url: str) -> str:
    """The HOST of an endpoint, and never its path.

    ===== ** AN API KEY LIVES IN THE PATH, AND THIS PROJECT LOGS ENDPOINTS BY NAME. ** =====
    A keyed endpoint is https://<host>/v2/<key>, and every place that reported "which endpoint
    served it" was printing `endpoint_uri` verbatim — the whole URL, key included — into the run
    log, the Run Log tab and the failover messages. Those are read by a human and pasted into
    chat. The host alone answers the only question anyone asks of that field, which is WHICH
    PROVIDER, so nothing is lost by cutting the path off.

    Defensive about shape on purpose: this runs inside logging paths, and a redactor that throws
    on an odd URL would take down the read it was describing. Anything unparseable degrades to a
    fixed placeholder rather than falling back to the raw string — falling back to the raw string
    is how a redactor leaks.
    """
    try:
        host = urlparse(str(url)).hostname
    except Exception:  # noqa: BLE001 — a redactor must never raise
        return "<unparseable endpoint>"
    return host or "<unparseable endpoint>"


_URL_RE = re.compile(r"https?://[^\s'\"<>)\]]+")


def redact_urls(text) -> str:
    """Replace every URL inside a message with its host.

    ===== ** rpc_host IS NOT ENOUGH ON ITS OWN, BECAUSE THE ERROR TEXT CARRIES THE URL TOO. **
    A provider's exception routinely embeds the request URI — "403 Client Error for url:
    https://<host>/v2/<key>" — and that string is what gets appended to log_endpoints_refused,
    put in the RuntimeError, and written to the Run Log. Redacting the endpoint field while
    interpolating the raw exception beside it would have leaked the key anyway, one field over.
    Its own test caught exactly that.

    Deliberately blunt: ANY url in the message is cut to its host, not just the configured
    endpoints. A redactor that only knows the list it was given misses the one that arrives from
    somewhere else, and that is the one that matters.
    """
    try:
        return _URL_RE.sub(lambda m: rpc_host(m.group(0)), str(text))
    except Exception:  # noqa: BLE001 — a redactor must never raise
        return "<unprintable>"


def rpc_endpoints(chain: str) -> list[str]:
    """Per-chain endpoint list, most-preferred first.

    THREE LAYERS, and they do different things:

      <CHAIN>_RPC_URL   PREPENDED. A keyed endpoint (Alchemy/Infura/dRPC) goes here: it is tried
                        first and the public list stays behind it as fallback, so one provider's
                        outage or rate limit does not take the run down.
      RPC_<CHAIN>       REPLACES. The escape hatch for "use exactly these and nothing else".
      DEFAULT_RPC       the public fallbacks.

    ** THE PREPEND IS THE POINT, AND REPLACE WAS THE ONLY OPTION BEFORE. ** The one method that
    actually needs a keyed endpoint is eth_getLogs — publicnode answers eth_call and eth_getCode
    perfectly and returns 403 to logs, which is why four Sky burn rows gapped for days. Replacing
    the list to get logs would have thrown away four working endpoints for every other call.

    DEDUPLICATED, ORDER PRESERVED: a keyed endpoint that also appears in DEFAULT_RPC is tried
    once, in its preferred position, not twice.
    """
    env = os.environ.get(f"RPC_{chain.upper()}", "").strip()
    if env:
        return [u.strip() for u in env.split(",") if u.strip()]
    preferred = os.environ.get(f"{chain.upper()}_RPC_URL", "").strip()
    urls = ([u.strip() for u in preferred.split(",") if u.strip()] if preferred else [])
    urls += list(config.DEFAULT_RPC.get(chain, []))
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _check_component_labels(project: dict, metric: str, components: list) -> None:
    """A component label's last piece is a CONTRACT KEY, and nothing else may sit there.

    ** THE FOURTH INSTANCE OF ONE BUG. ** `[tail@21bps]`, `:rederived`, `:as-buyback` each put
    something in a source string that the key parsers then read as a contract, and each surfaced
    days later as a column reading "written by contract(s) X, which are no longer in config" —
    a message pointing at the wrong file, for a contract that was never meant to be named.

    This is the fourth, and it had not bitten yet only because the read it belongs to has been
    403ing: Sky's burn_transfer_logs decomposes into three metrics and labels each component
    `{chain}:{key}:{metric}`, so `chain:ethereum:burn_logs:governance_burn_balance` names the
    METRIC in the slot the key parsers take the key from. The day that read succeeds, all three
    of Sky's burn series would have rendered ORPHANED.

    ** CHECKED AGAINST THE PROJECT IN HAND, NOT GLOBAL CONFIG. ** The row is being written from
    these contracts; whether some other copy of the config has them is a different question, and
    asking it here would make a fixture's divergence look like a data fault.

    It RAISES. A source that cannot be resolved back to the contract it was read from is not a
    lower-confidence figure — it is a figure nothing downstream can interpret, and every previous
    instance of this class was found by a human reading a blank cell weeks later.
    """
    contracts = project.get("contracts") or {}
    for label, _ in components:
        key = config.strip_source_annotations(label).split(":")[-1]
        if key and key not in contracts:
            raise ValueError(
                f"{project['name']}/{metric}: component label {label!r} ends in {key!r}, which is "
                f"not a contract in this project. The last colon-delimited piece of a source "
                f"string is where every key parser looks for the contract — put anything else "
                f"there (a metric name, a marker, a note) and the column renders ORPHANED against "
                f"a contract that was never meant to be named. Use a bracketed annotation for "
                f"anything that is not a key.")


class ChainReader:
    """Holds one working web3 connection per chain, trying the fallback list in order."""

    def __init__(self):
        self._w3: dict[str, object] = {}
        self._failed: dict[str, str] = {}
        # The eth_getLogs chunk size that actually worked, per chain. Reported rather than
        # assumed: the configured size is a request, and a provider that narrows it silently
        # would make the scan cost a mystery.
        self.log_chunk_used: dict[str, int] = {}
        # WHICH ENDPOINT SERVED THE SCAN, and which ones refused it first. The run of
        # 2026-09-22 reported Sky's burn read as "403 from publicnode" and there was no way to
        # tell from the log whether one endpoint refused and another served, or all four refused
        # and the failover simply had nowhere left to go. Those are different problems with
        # different fixes, and the log has to say which one happened.
        self.log_endpoint_used: dict[str, str] = {}
        self.log_endpoints_refused: dict[str, list[str]] = {}
        # Every range/bad-request body the providers returned, so the working chunk size is READ
        # from what they said rather than guessed at.
        self.log_range_errors: dict[str, list[str]] = {}

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
                    # HOST ONLY — see rpc_host. A keyed endpoint carries its key in the path.
                    log.info("chain %s connected via %s", chain, rpc_host(url))
                    self._w3[chain] = w3
                    return w3
                errors.append(f"{rpc_host(url)}: not connected")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{rpc_host(url)}: {redact_urls(e)}")
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

    def raw(self, chain: str, address: str, call: str, *args) -> int:
        """Call `call` and return the integer AS STORED, with no decimals scaling.

        For a figure that is not a token amount. Aerodrome's tailEmissionRate() is basis points:
        dividing it by the token's decimals would be arithmetically valid and meaningless.
        """
        c = self.erc20(chain, address)
        args = tuple(self.checksum(a) if isinstance(a, str) and a.startswith("0x") else a for a in args)
        return int(getattr(c.functions, call)(*args).call())

    def deployment_block(self, chain: str, address: str) -> int:
        """The block this contract was deployed in, found by binary search on eth_getCode.

        ** DERIVED, NOT LOOKED UP AND NOT HARDCODED. ** A burn-log scan needs a start height, and
        the start height is the one input where a wrong value fails invisibly: begin after the
        events and the scan returns a smaller, confident, entirely plausible total. Etherscan
        would answer it in one call and is not reachable from every environment this runs in, so
        the number is computed from the chain itself — eth_getCode is empty before deployment and
        non-empty after, which is monotonic and therefore searchable.

        Costs ~log2(head) calls, about 25 on mainnet, once. The caller records the answer so the
        next run reads it from config instead of searching again.
        """
        w3 = self.web3(chain)
        addr = self.checksum(address)
        lo, hi = 0, int(w3.eth.block_number)
        if not w3.eth.get_code(addr, block_identifier=hi):
            raise RuntimeError(f"nothing deployed at {address} on {chain} at block {hi}")
        while lo < hi:
            mid = (lo + hi) // 2
            if w3.eth.get_code(addr, block_identifier=mid):
                hi = mid
            else:
                lo = mid + 1
        return lo

    def block_timestamp(self, chain: str, block: int) -> int:
        """Unix timestamp of one block. One call, used to DATE a single discovered event."""
        return int(self.web3(chain).eth.get_block(int(block))["timestamp"])

    # An endpoint that REFUSES eth_getLogs, and what it costs to find out at connect time.
    # publicnode answers eth_blockNumber perfectly and returns 403 to eth_getLogs, so
    # `is_connected()` picks it, every other read on the chain works, and only the log scan dies.
    # These are the substrings that mean "this ENDPOINT will not serve this method" rather than
    # "this REQUEST was wrong" — the first is fixed by moving to the next endpoint, the second is
    # not, and treating them alike would hammer four providers with the same bad request.
    LOGS_ENDPOINT_REFUSED = ("403", "forbidden", "unauthorized", "method not found",
                             "not supported", "unsupported method", "429", "rate limit")
    # ** A 400 IS NOT IN EITHER LIST, AND THAT IS WHY SKY'S SCAN DIED RATHER THAN NARROWING. **
    # Sky's burn read moved 403 -> 400 once a keyed endpoint was configured: the method is now
    # permitted and the REQUEST is being rejected. But requests' HTTPError stringifies to
    # "400 Client Error: Bad Request for url: ..." with the BODY DROPPED, and the body is where
    # the provider names its own limit ("block range exceeds..."). So str(e) matched neither
    # LOGS_RANGE_TOO_WIDE nor LOGS_ENDPOINT_REFUSED, the handler re-raised, and a scan with
    # working narrowing logic never narrowed once.
    LOGS_BAD_REQUEST = ("400", "bad request", "invalid params", "-32602")
    # And the substrings that mean "this RANGE is too wide for me" — the server naming its own
    # limit. Halving answers what it said; it is not a blind retry of the same request.
    LOGS_RANGE_TOO_WIDE = ("query returned more than", "block range", "too many results",
                           "response size", "limit exceeded", "range is too large", "query timeout")
    # ===== ** THE FLOOR IS NOT A TUNING KNOB. Confirmed the hard way 2026-09-23. ** =====
    # Alchemy's free tier caps eth_getLogs at TEN blocks, from its own error body: "Under the
    # Free tier plan, you can make eth_getLogs requests with up to a 10 block range." The
    # narrowing did exactly what it should — 5000 -> 2500 -> 1250 -> 625 -> 500 — and was still
    # refused, because the real ceiling is below this floor. Refusing there is the algorithm
    # working: it declines to guess below a bound it was given.
    #
    # ** DO NOT LOWER THIS TO FORCE A RESULT. ** A 10-block cap over Sky's ~5.4m block history
    # is ~540,000 requests: not viable at any chunk size, so a lower floor would buy a scan that
    # runs for hours and rate-limits, not a figure. The four burn-decomposition rows stay
    # correctly GAPPED until the plan or the provider changes, which is a true empty cell rather
    # than a number assembled from half a scan.
    MIN_LOG_CHUNK = 500

    @staticmethod
    def error_detail(e) -> str:
        """The exception text PLUS the response body, which is where the real message lives.

        ** requests.HTTPError DROPS THE BODY FROM ITS str(). ** It renders as "400 Client Error:
        Bad Request for url: ..." and the provider's actual complaint — the one naming its own
        block-range limit — is in `.response.text`, which nothing was reading. Every match and
        every report below runs against this, not against str(e), because otherwise the code is
        pattern-matching a message that has had its content removed.

        Tolerant of shape on purpose: web3 wraps provider errors several ways and this runs
        inside an exception handler, so it must never raise on its way to explaining a failure.
        """
        parts = [str(e)]
        for attr in ("response", "args"):
            try:
                v = getattr(e, attr, None)
            except Exception:  # noqa: BLE001
                continue
            if attr == "response" and v is not None:
                try:
                    parts.append(str(getattr(v, "text", ""))[:600])
                except Exception:  # noqa: BLE001
                    pass
            elif attr == "args" and v:
                parts.extend(str(a)[:600] for a in v if not isinstance(a, Exception))
        seen, out = set(), []
        for part in parts:
            if part and part not in seen:
                seen.add(part)
                out.append(part)
        return redact_urls(" | ".join(out))

    def _get_logs_resilient(self, chain: str, base: dict, block: int, head: int,
                            chunk: int) -> tuple[list, int, int]:
        """One eth_getLogs chunk, falling over to the next endpoint on refusal, narrowing on range.

        ** THE FALLBACK LIST WAS ONLY CONSULTED AT CONNECT TIME, WHICH IS THE WRONG MOMENT. **
        ChainReader.web3 tries each endpoint until one answers is_connected() and then keeps it
        for every call on that chain. A provider can be perfectly connected and still refuse ONE
        method: ethereum-rpc.publicnode.com serves eth_blockNumber, eth_call and eth_getCode and
        returns 403 to eth_getLogs. So Sky's burn read failed with three working alternatives
        sitting unused in the list.

        TWO FAILURES, TWO DIFFERENT ANSWERS, told apart by what the server SAID:
          REFUSED BY THIS ENDPOINT -> move to the next one and retry the same range. Each
            endpoint is tried at most once per chunk, so a request that is simply wrong fails
            after one pass instead of being hammered round the list.
          RANGE TOO WIDE -> halve the chunk and retry, down to MIN_LOG_CHUNK. The server named
            its own limit and narrowing is a reply to that, not a guess. Below the floor it
            raises: a scan needing thousands of chunks is a configuration answer, not a retry.
        Anything else raises immediately. A decoding error or a bad topic must not be papered
        over by trying another provider, which would turn one clear failure into four vague ones.

        Returns (logs, last_block_covered, chunk_that_worked) so the caller advances by what was
        actually read and reports the real chunk size rather than the one it asked for.
        """
        from web3 import HTTPProvider, Web3

        tried: list[str] = []
        urls = rpc_endpoints(chain)
        w3 = self.web3(chain)
        while True:
            upper = min(block + chunk - 1, head)
            try:
                logs = list(w3.eth.get_logs({**base, "fromBlock": block, "toBlock": upper}))
                self.log_chunk_used[chain] = chunk
                self.log_endpoint_used[chain] = rpc_host(getattr(
                    getattr(w3, "provider", None), "endpoint_uri", "?"))
                return logs, upper, chunk
            except Exception as e:  # noqa: BLE001
                detail = self.error_detail(e)
                msg = detail.lower()
                # ** THE BODY IS RECORDED WHATEVER HAPPENS NEXT. ** Knowing that a provider said
                # "block range exceeds 10000" is what turns a guessed chunk size into a read one,
                # and it is lost the moment the exception is re-raised.
                self.log_range_errors.setdefault(chain, [])
                if detail not in self.log_range_errors[chain]:
                    self.log_range_errors[chain].append(detail[:300])
                named_range = any(s in msg for s in self.LOGS_RANGE_TOO_WIDE)
                # AN UNEXPLAINED 400 OVER A WIDE SPAN IS TREATED AS A RANGE REJECTION, and the
                # inference is logged AS an inference. A 400 means the request was rejected, not
                # the method; over thousands of blocks the overwhelmingly likely cause is the
                # range, and narrowing tests that in one call. It is bounded by MIN_LOG_CHUNK, so
                # it cannot become an unbounded retry loop — at the floor it raises with the body
                # attached, which is the honest end state.
                bare_400 = (not named_range and any(s in msg for s in self.LOGS_BAD_REQUEST)
                            and chunk > self.MIN_LOG_CHUNK)
                if (named_range or bare_400) and chunk > self.MIN_LOG_CHUNK:
                    chunk = max(self.MIN_LOG_CHUNK, chunk // 2)
                    log.info("chain %s: eth_getLogs %s (%s) — narrowing to %d blocks", chain,
                             "range refused" if named_range else
                             "400 with no range named — INFERRING a range limit",
                             detail[:160], chunk)
                    continue
                if not any(s in msg for s in self.LOGS_ENDPOINT_REFUSED):
                    # ** RE-RAISED UNCHANGED, AND THE BODY TRAVELS SEPARATELY. ** Wrapping this
                    # in a RuntimeError would put the provider's words in the message and throw
                    # away the exception TYPE, which callers and tests discriminate on — a
                    # decoding fault and a range fault must stay distinguishable. The body is
                    # already in log_range_errors, which the scan's own report prints, so it
                    # reaches a human either way.
                    log.info("chain %s: eth_getLogs failed over blocks %d-%d at chunk %d — %s",
                             chain, block, upper, chunk, detail[:300])
                    raise
                tried.append(rpc_host(getattr(getattr(w3, "provider", None),
                                              "endpoint_uri", "?")))
                self.log_endpoints_refused.setdefault(chain, [])
                if tried[-1] not in self.log_endpoints_refused[chain]:
                    self.log_endpoints_refused[chain].append(
                        f"{tried[-1]} ({redact_urls(e)[:60]})")
                nxt = next((u for u in urls if rpc_host(u) not in tried), None)
                if nxt is None:
                    raise RuntimeError(
                        f"ALL {len(tried)} configured {chain} RPC endpoint(s) refused "
                        f"eth_getLogs, in order: {'; '.join(self.log_endpoints_refused[chain])}. "
                        f"Last error: {redact_urls(e)}. These endpoints answer other methods, "
                        f"so this is a "
                        f"PER-METHOD refusal: the fix is an endpoint that serves logs "
                        f"(set {chain.upper()}_RPC_URL in .env — it is PREPENDED, so the "
                        f"public endpoints stay as fallback), not a narrower range.") from e
                log.info("chain %s: %s refused eth_getLogs (%s) — falling back to %s",
                         chain, tried[-1], redact_urls(e)[:80], rpc_host(nxt))
                w3 = Web3(HTTPProvider(nxt, request_kwargs={"timeout": 60}))
                self._w3[chain] = w3

    def burn_transfer_events(self, chain: str, token: str, burn_to: str, from_block: int,
                             chunk: int = 10_000, max_blocks: int | None = None
                             ) -> tuple[list[dict], int, int, int]:
        """Every Transfer(*, burn_to, value) on `token` from `from_block` to the head, UNAGGREGATED.

        THE READ A PROTOCOL BURN NEEDS, AND THE ONE A BALANCE READ CANNOT GIVE. OpenZeppelin-style
        _burn — and Sky's own Sky.burn(from, value), read from source — emits
        Transfer(from, address(0), value) and destroys the tokens, so there is no balance at
        address(0) afterwards and the events are the only record.

        ** THE `from` IS RETURNED PER EVENT AND THAT IS THE POINT. ** One token can be burned by
        several unrelated mechanisms, and summing them produces a figure that is not any of them.
        On SKY: the Stage 2 buy-and-burn, governance burning from the Pause Proxy, and the
        MkrSky converter's auth-only burn() are three different economic facts sharing one event
        signature. Only the caller knows which is which, so this returns the raw facts and
        decomposes nothing.

        A MINT IS NOT CAUGHT BY THIS, and the filter is what guarantees it rather than luck:
        Sky.mint emits Transfer(address(0), to, value) — address(0) in topic[1], the FROM slot.
        The filter pins topic[2], the TO slot. Confirmed against src/Sky.sol, 2026-09-22.

        CHUNKED, BECAUSE PUBLIC RPCs CAP THE RANGE. Returns the chunk COUNT alongside the events
        so the caller can report what the scan actually cost.
        """
        from web3 import Web3

        w3 = self.web3(chain)
        head = int(w3.eth.block_number)
        if max_blocks is not None and head - from_block > max_blocks:
            # REFUSE, DO NOT TRUNCATE. A silently shortened window returns a smaller number that
            # looks exactly like a quieter period.
            raise RuntimeError(
                f"the log window is {head - from_block:,} blocks (from {from_block:,} to {head:,}), "
                f"over the configured max_blocks_per_run of {max_blocks:,}. Refusing rather than "
                f"scanning a shortened window, which would return a smaller total that reads as a "
                f"quieter period. Raise max_blocks_per_run, or move from_block forward and carry "
                f"the earlier total as a declared starting point.")

        topic = Web3.keccak(text="Transfer(address,address,uint256)").hex()
        to_topic = "0x" + self.checksum(burn_to)[2:].lower().rjust(64, "0")
        base = {"address": self.checksum(token), "topics": [topic, None, to_topic]}
        raw, chunks, used = self.scan_logs(chain, base, from_block, head, chunk)
        events = []
        if True:
            for entry in raw:
                # THE VALUE IS THE DATA WORD, not a topic: Transfer indexes from and to and leaves
                # value unindexed. Reading a topic here would return an address as an amount.
                data = entry["data"]
                word = int(data.hex() if hasattr(data, "hex") else str(data), 16)
                sender = entry["topics"][1]
                sender = sender.hex() if hasattr(sender, "hex") else str(sender)
                events.append({"from": "0x" + sender[-40:],
                               "value": word,
                               "block": int(entry["blockNumber"])})
        self.log_chunk_used[chain] = used
        return events, from_block, head, chunks

    def scan_logs(self, chain: str, base: dict, from_block: int, to_block: int,
                  chunk: int = 10_000) -> tuple[list, int, int]:
        """THE one chunked eth_getLogs. Every event read in this codebase goes through it.

        Returns (raw log entries, chunks used, the chunk size that worked). Decoding is the
        CALLER's job — this returns entries untouched, so a Transfer scan and a pool-outflow
        scan share the traversal without sharing an ABI.

        ** IT WAS ALREADY WRITTEN AND IT WAS NOT REUSABLE. ** The traversal, the narrowing and
        the endpoint failover all existed, fused inside the Transfer-specific burn reader — so
        the next event-based read would have had to copy them or go without. Extracted rather
        than rewritten: the logic below is the logic that was there, which is why no behaviour
        changes for the burn scan.

        A SPAN NO PROVIDER SERVES IN ONE CALL IS THE NORMAL CASE, not an error. Sky's burn
        history is ~5.4m blocks (20,663,735 to ~26,039,143); nothing serves that unchunked, and
        the 400 it now returns is the server saying so.
        """
        block, chunks, used, out = from_block, 0, chunk, []
        while block <= to_block:
            logs, upper, used = self._get_logs_resilient(chain, base, block, to_block, used)
            chunks += 1
            out.extend(logs)
            # ** upper, NOT block + used. ** _get_logs_resilient may have NARROWED mid-scan, and
            # advancing by the requested size after a narrowed chunk would skip every block
            # between the narrowed end and the assumed one — silently, and only on the runs
            # where narrowing happened, which is the hardest kind of gap to notice.
            block = upper + 1
        self.log_chunk_used[chain] = used
        return out, chunks, used

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

    def raw_call(self, chain: str, address: str, call: str, *args) -> float:
        """Call `call` and return the integer AS RETURNED — no decimals scaling.

        ** NOT EVERY ON-CHAIN NUMBER IS A TOKEN AMOUNT. ** scaled() divides by decimals() because
        every reader it was written for returns wei. A governance parameter does not:
        sPENDLE.cooldownDuration() returns SECONDS, and dividing it by 10^18 would turn a 14-day
        notice period into 1.2e-12 days — a number small enough to read as zero and be believed.
        The two paths are separate so neither can be reached by accident.
        """
        c = self.erc20(chain, address)
        args = tuple(self.checksum(a) if isinstance(a, str) and a.startswith("0x") else a for a in args)
        return float(getattr(c.functions, call)(*args).call())


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
        # ** metric_override FIRST, exactly as the read path resolves it. ** A gap filed under
        # the KIND's default metric names a column this contract does not serve: Pendle's
        # spendle_underlying is overridden to locked_tokens, and a refusal filed against
        # locked_tokens_underlying lands on a metric the project has no route to — so the gap
        # is invisible where the reader is looking and present where nothing was expected.
        # Caught 2026-09-23 by the control test for the read-method gate.
        metric = spec.get("metric_override") or KIND_METRIC.get(spec["kind"], key)

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
        if spec["kind"] in BURN_READ_KINDS and mech.get("status") == "refuted":
            out.gap(name, metric,
                    reason=f"the burn MECHANISM is refuted, not merely the address: this project does not "
                           f"burn by {mech.get('model')!r} in the way a {spec['kind']} read could measure. "
                           f"{mech.get('note', '')}".strip(),
                    tiers_attempted="2",
                    suggestion=f"Do not substitute another address or another event — no chain read models "
                               f"this. Establish the real mechanism first; see "
                               f"{mech.get('source_url') or 'the protocol docs'} "
                               f"and OPEN_QUESTIONS for this project.")
            out.unconfigured(SOURCE, name, f"{key}: burn mechanism refuted, chain read refused", TIER)
            return False

        # 2b. Is the READ METHOD one this adapter can serve? A protocol-level burn has no address.
        #     SCOPED TO BALANCE READS ONLY, unlike 2a. A protocol-level burn is exactly what
        #     burn_transfer_logs exists to read: the tokens are destroyed and the Transfer-to-zero
        #     events are the record. Refusing it here would refuse the one read that works.
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
            # TWO KINDS LAND HERE AND THE HAZARD IS NOT THE SAME ONE, so the reason says which.
            # Sending a reader to "ERC-20 or ERC-721?" for a stake_underlying entry points at a
            # question that is not the problem, and a reason that names the wrong obstacle is
            # worse than none — it looks actionable, so somebody acts on it.
            if spec["kind"] == "stake_underlying":
                why = (f"read method for {key!r} is NOT ESTABLISHED, and for a stake_underlying "
                       f"entry the default is the WRONG QUANTITY rather than a failure. Without "
                       f"'escrow_balance_of' the generic path calls totalSupply() on the HOLDER "
                       f"— the SHARE count — and stores it under an ASSETS metric. The two "
                       f"differ by the accrued rate, so the number looks entirely plausible.")
                fix = (f"Set read_method='escrow_balance_of' and `underlying` on {key!r} so the "
                       f"call is made on the holder and symbol/decimals come from the token. "
                       f"See contracts.sethfi for the same shape.")
            else:
                why = (f"lock read method for {key!r} is NOT ESTABLISHED — a vote escrow can be a "
                       f"fungible ERC-20 (totalSupply is the staked amount) or an NFT position "
                       f"(totalSupply is a COUNT OF POSITIONS, wrong by orders of magnitude). "
                       f"Neither is assumed.")
                fix = (f"Establish whether {key!r} is ERC-20 or ERC-721, then set read_method to "
                       f"'erc20_total_supply' or 'escrow_balance_of' (with `underlying`) and "
                       f"token_standard in config.py.")
            out.gap(name, metric, reason=why, tiers_attempted="2", suggestion=fix)
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
            partial_reasons: dict[str, str] = {}
            # WHICH FORMULA PRODUCED THE FIGURE, where a contract can produce it two ways. Carried
            # into the source string so a series that silently changes basis mid-history is
            # readable in the sheet rather than being an unexplained step change.
            source_suffix: dict[str, str] = {}
            # THE DATE A FIGURE BELONGS TO, where that is not the run date. A weekly read is dated
            # to its EPOCH START, so running six days in a row lands six times on ONE key instead
            # of laying down six rows a trailing-30-day sum then adds up as six separate weeks.
            metric_when: dict[str, object] = {}
            # Contracts whose ADDRESS is right but whose role is in doubt — a burn sink we are no
            # longer confident the protocol actually burns to. Read, but never stored as a metric.
            disputed: dict[str, list[str]] = defaultdict(list)
            # A component that was REFUSED (unverified, unreachable chain, no same-chain token)
            # makes the resulting sum partial. Dropping one burn path and reporting the rest as if
            # it were the whole is precisely the understatement this tool exists to prevent.
            refused: dict[str, list[str]] = defaultdict(list)
            for key, spec in contracts.items():
                chain, kind = spec["chain"], spec["kind"]
                # ** REFERENCE-ONLY IS CHECKED BEFORE THE GATE, not after. ** The gate exists to
                # stop an UNVERIFIED ADDRESS BEING READ; a reference-only contract is never read,
                # so there is nothing for it to gate and its verification status is irrelevant.
                #
                # Gating first raised an "address is NOT verified" gap row every run for
                # PancakeSwap's token_base — and that row invited precisely the wrong fix, because
                # verifying it would not have improved the supply figure, it would have let a
                # bridged representation be summed into a home-chain total that already contains
                # it. A gap row that asks for the harmful action is worse than no row.
                if kind in REFERENCE_ONLY_KINDS:
                    out.log.append(LogEntry(SOURCE, name, 0, "ok",
                                            f"{key}: {kind}, reference only — no metric read from it", TIER))
                    continue
                if not self._gate(p, key, spec, out):
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
                    # ===== A GOVERNANCE PARAMETER IS NOT A TOKEN AMOUNT. Added 2026-09-23. =====
                    # cooldownDuration() returns SECONDS. Through scaled() it would be divided by
                    # the token's decimals, turning a 14-day notice period into 1.2e-12 days —
                    # small enough to read as zero and be believed, on a metric whose whole point
                    # is that governance can change it. The unit conversion is declared on the
                    # kind rather than inferred from the size of the number.
                    #
                    # FIRST IN THE CHAIN, not nested inside the principal branch. Placed there
                    # initially, it was unreachable — the read fell through to the generic
                    # totalSupply path, which called cooldownDuration() and then scaled it, and
                    # the test is what caught it. Kind dispatch belongs where every other kind is
                    # decided.
                    if kind == "cooldown_duration":
                        seconds = self.reader.raw_call(chain, read_address,
                                                       spec.get("call") or "cooldownDuration")
                        value = seconds / 86_400.0
                        log.info("%s/%s: %s() = %.0f seconds = %.4f days", name, key,
                                 spec.get("call") or "cooldownDuration", seconds, value)
                    elif holder:
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
                        # AN EMISSION SCHEDULE THAT STOPS BEING THE EMISSION. Aerodrome's
                        # Minter.updatePeriod() assigns `weekly` back only on the non-tail
                        # branch, so the first epoch where weekly < TAIL_START freezes the
                        # variable at its last scheduled value FOREVER while the real emission
                        # becomes totalSupply * tailEmissionRate / MAX_BPS. Reading weekly()
                        # past that point returns a dead constant wearing the label of a rate,
                        # which is exactly what run 20260921T100546Z stored: 8,969,149, the
                        # frozen value to the token. So the read takes the contract's own
                        # branch, on the contract's own threshold.
                        tail = spec.get("emission_tail")
                        if tail:
                            value, note = self._tail_aware_emission(
                                chain, read_address, contracts, tail, value, key, out, name,
                                metric)
                            if value is None:
                                refused[metric].append(f"{key} ({chain}): {note}")
                                continue
                            source_suffix[metric] = note
                    elif kind == "burn_transfer_logs":
                        value = self._burn_logs(chain, read_address, key, spec, p, metric,
                                                parts, refused, source_suffix, when, out)
                        if value is None:
                            continue
                    else:
                        value = self.reader.scaled(chain, read_address, spec.get("call") or "totalSupply")
                except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                    out.fail(SOURCE, name, f"{key} ({chain}): {e}", TIER)
                    out.gap(name, metric, reason=f"contract read failed: {e}", tiers_attempted="2",
                            suggestion="Check the RPC endpoints for this chain in .env")
                    refused[metric].append(f"{key} ({chain}): read failed")
                    continue
                parts[metric].append((f"{chain}:{key}", value))
                # A WEEKLY READ IS DATED TO ITS EPOCH, NOT TO THE RUN. The contract holds one
                # figure per epoch; the run date is just when we happened to look. Dating to the
                # epoch start makes a re-read inside the same epoch land on the SAME
                # (date, project, metric) key and overwrite, instead of laying down another row
                # that a trailing-30-day sum then counts as another week's emissions.
                if config.series_granularity(name, metric) == "weekly":
                    metric_when[metric] = self._epoch_start(spec)
                if spec.get("supply_is_partial"):
                    partial_metrics.add(metric)
                    # THE CONTRACT'S OWN REASON, not the project's. supply_partial_reason on the
                    # PROJECT describes a partial total_supply and is shared by every metric;
                    # Maple's treasury read is partial for a reason that has nothing to do with
                    # SYRUP's supply (the daoMultisig holds part of what Maple counts as the
                    # treasury). Falling back to the project's text would have labelled it with
                    # someone else's explanation.
                    if spec.get("partial_reason"):
                        partial_reasons[metric] = spec["partial_reason"]
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
            self._emit_parts(p, parts, partial_metrics, refused, when, out, disputed,
                             source_suffix=source_suffix, metric_when=metric_when,
                             partial_reasons=partial_reasons)

    @staticmethod
    def _epoch_start(spec: dict):
        """The start of the epoch this read describes, as a date.

        Minter.weekly() / the tail emission describe the CURRENT epoch, so they take the current
        epoch's start. RewardsDistributor.tokensPerWeek(t) is asked for the last COMPLETE week
        (call_arg), so it takes that week's start — the same boundary that was passed as the
        argument, never recomputed differently, or the figure would be filed under a week it is
        not about.

        time.time(), not a pandas Timestamp's .timestamp(): the latter reads a tz-naive value in
        the SYSTEM's local zone and would put the boundary in the wrong week on any host not on
        UTC. Same reasoning as the call_arg computation this mirrors.
        """
        now_ts = int(time.time())
        weeks_back = 1 if spec.get("call_arg") == "last_complete_week_unix" else 0
        start = ((now_ts // _WEEK_SECONDS) - weeks_back) * _WEEK_SECONDS
        return pd.Timestamp(start, unit="s").normalize()

    def _tail_aware_emission(self, chain, minter_address, contracts, tail, weekly_value, key,
                             out, name, metric):
        """Take the emission branch the CONTRACT would take, on the contract's own threshold.

        Returns (value, note), or (None, reason) to refuse. Refusing is the right answer when the
        tail leg cannot be read: the alternative is storing weekly(), and weekly() in tail mode is
        a frozen constant that LOOKS like a plausible emission. A number that looks right and is
        not is the failure this tool exists to prevent, so nothing is stored instead.
        """
        tail_start = float(tail["tail_start"])
        if weekly_value >= tail_start:
            # Pre-tail: the schedule IS the emission, and weekly() is the whole answer.
            return weekly_value, f"weekly<{tail_start:,.0f}=no"

        supply_key = tail["supply_from"]
        supply_spec = contracts.get(supply_key) or {}
        if not supply_spec.get("address"):
            return None, (f"tail mode is active (weekly {weekly_value:,.2f} < TAIL_START "
                          f"{tail_start:,.0f}) but supply_from {supply_key!r} has no address")
        try:
            rate_bps = self.reader.raw(chain, minter_address, tail["rate_call"])
            supply = self.reader.scaled(chain, supply_spec["address"], "totalSupply")
        except Exception as e:  # noqa: BLE001 — a failed leg refuses; it must not fall back
            return None, (f"tail mode is active but the tail legs could not be read ({e}). "
                          f"weekly() returns {weekly_value:,.2f}, which is FROZEN and is not the "
                          f"emission — refusing rather than storing it")

        lo, hi = tail.get("rate_bounds_bps") or (None, None)
        if lo is not None and not (lo <= rate_bps <= hi):
            # Outside the contract's own bounds means the read is not what we think it is.
            return None, (f"{tail['rate_call']}() returned {rate_bps} bps, outside the contract's "
                          f"own [{lo}, {hi}] bounds — the read is not measuring what config says")

        denom = float(tail["rate_bps_denominator"])
        emission = supply * rate_bps / denom

        # ** AND A BOUND ON THE RESULT, because the rate bound alone is far too wide. **
        # [1, 100] bps spans 0.5% to 52% of supply per year; a wrong read lands inside it easily.
        # Aerodrome documents ~10.9% annualised as of April 2026, so a band around that catches a
        # rate that is stale, mis-scaled, or read off the wrong getter — none of which the [1,100]
        # check would notice. See config's annualised_share_source for the band's derivation and
        # for the ambiguity in the documented figure that makes it deliberately loose.
        lo, hi = (tail.get("annualised_share_bounds") or (None, None))
        if lo is not None and supply:
            annualised = emission * 52.0 / supply
            if not (lo <= annualised <= hi):
                return None, (
                    f"tail emission {emission:,.2f}/week is {annualised:.1%} of supply annualised, "
                    f"outside the documented {lo:.0%}-{hi:.0%} band "
                    f"({tail.get('annualised_share_source', {}).get('claim', 'see config')}). "
                    f"Read back {rate_bps} bps against supply {supply:,.2f}. Refusing rather than "
                    f"storing: a figure of the wrong ORDER presented as an emission rate is worse "
                    f"than a blank, and 67 bps — the rate at tail activation — sits outside this "
                    f"band on purpose, so a value that has never been nudged reads as a finding")
        out.log.append(LogEntry(SOURCE, name, 0, "ok",
                                f"{metric}: TAIL MODE — weekly() {weekly_value:,.2f} is below "
                                f"TAIL_START {tail_start:,.0f} and is frozen. Emission computed as "
                                f"totalSupply {supply:,.2f} x {rate_bps} bps = {emission:,.2f}",
                                TIER))
        return emission, f"tail@{rate_bps}bps"

    def _burn_logs(self, chain, token, key, spec, project, metric, parts, refused,
                   source_suffix, when, out):
        """Scan Transfer-to-burn events, DECOMPOSE THEM BY SENDER, and emit one series per source.

        ** ONE EVENT SIGNATURE, SEVERAL UNRELATED ECONOMIC FACTS. ** Sky.burn(from, value) emits
        Transfer(from, address(0), value) whoever calls it and for whatever reason, and SKY is
        burned by at least three mechanisms that mean different things:

          Stage 2 buy-and-burn   revenue-funded, recurring, the archetype 4 figure.
          Pause Proxy burns      governance destroying treasury SKY. Real supply reduction and
                                 NOT revenue-funded — a one-off decision, not a rate.
          MkrSky converter       burn(uint256) is auth-only and documented in the contract's own
                                 source as "for burning excess SKY due to MKR being burned". A
                                 supply CORRECTION, not a buyback of any kind.

        Summing them gives a number that is none of the three, and it is the recurring one the
        archetype 4 tab is asking for. So the sender is the key, the series are separate, and
        anything unrecognised gets its own row rather than being folded into the answer.

        THE STAGE 2 BURNER IS DISCOVERED, NOT HARDCODED. Its address is not in any source on
        file; what IS known is that it burned ~2,860,000 SKY on 2026-09-14. The scan finds the
        event matching that amount, takes its sender, and confirms the block's DATE before
        accepting it — one extra call. Exactly one candidate or it refuses: two matches or none
        is an unresolved identification, and guessing which is the whole failure this avoids.
        """
        cfg = spec.get("burn_logs") or {}
        burn_to = config.BURN_ADDRESSES[cfg.get("burn_to", "zero")]

        # ===== THE START HEIGHT, DERIVED WHEN IT IS NOT ON FILE. =====
        # A scan that begins after the events returns a smaller, confident, entirely plausible
        # total and nothing in the output shows it, so this is the one input that must not be
        # estimated. Where config has no height, it is computed from the chain by binary search
        # on eth_getCode rather than looked up in a block explorer that not every environment
        # can reach — and the discovered value is printed so it can be written back.
        from_block = cfg.get("from_block")
        discovered_from = None
        if from_block is None:
            if cfg.get("from_block_discover") != "deployment":
                out.gap(project["name"], metric,
                        reason=(f"{key!r} reads burns from Transfer events and its from_block is "
                                f"NOT SET. A scan starting after the burns returns a small, "
                                f"confident, plausible number — the failure is invisible in the "
                                f"output, so the start height is required rather than defaulted."),
                        tiers_attempted="2",
                        suggestion=cfg.get("how_to_set") or "Set burn_logs.from_block, with its source.")
                out.unconfigured(SOURCE, project["name"], f"{key}: burn_logs.from_block not set", TIER)
                refused[metric].append(f"{key} ({chain}): from_block not set")
                return None
            from_block = discovered_from = self.reader.deployment_block(chain, token)
            log.info("%s/%s: from_block not on file — %s deployed at block %d (binary search on "
                     "eth_getCode). Write this into config so the next run skips the search.",
                     project["name"], key, token, from_block)

        events, first_blk, last_blk, chunks = self.reader.burn_transfer_events(
            chain, token, burn_to, int(from_block),
            chunk=int(cfg.get("chunk_blocks", 10_000)),
            max_blocks=cfg.get("max_blocks_per_run"))
        # DECIMALS FROM THE TOKEN ITSELF, read directly rather than through scaled(): these are
        # raw log data words, not a call return, so nothing has scaled them yet.
        dec = int(self.reader.erc20(chain, token).functions.decimals().call())
        # THE CHUNK SIZE REPORTED IS THE ONE THAT WORKED, not the one config asked for. A
        # provider that caps the range makes the adapter narrow, and printing the request would
        # misreport what the scan cost — and hide that the configured size is unusable here.
        asked = int(cfg.get("chunk_blocks", 10_000))
        # getattr, not attribute access: this is a REPORTING line and it must not be able to
        # fail the read it is describing. A reader that does not track the worked size simply
        # reports the asked one.
        worked = getattr(self.reader, "log_chunk_used", {}).get(chain, asked)
        # ** WHICH ENDPOINT SERVED IT. ** "403 from publicnode" in a run log does not say whether
        # the failover then found a working endpoint or ran out of them — and on a read that has
        # been failing for days, that is the whole question. Reported on success too, so the next
        # refusal can be read against the endpoint that used to work.
        served = getattr(self.reader, "log_endpoint_used", {}).get(chain, "?")
        refused_by = getattr(self.reader, "log_endpoints_refused", {}).get(chain) or []
        log.info("%s/%s: %d burn event(s) over blocks %d-%d in %d chunk(s) of %d%s — SERVED BY "
                 "%s%s",
                 project["name"], key, len(events), first_blk, last_blk, chunks, worked,
                 "" if worked == asked else f" (config asked for {asked:,}; the endpoint capped it)",
                 served,
                 "" if not refused_by else f", after {len(refused_by)} refusal(s): {'; '.join(refused_by)}")
        # ** WHAT THE PROVIDERS ACTUALLY SAID ABOUT THE RANGE. ** The working chunk size is only
        # interpretable next to the complaint that produced it, and a 400 whose body was never
        # printed is what left Sky's scan unexplained for days.
        ranges = getattr(self.reader, "log_range_errors", {}).get(chain) or []
        if ranges:
            log.info("%s/%s: %d range/bad-request response(s) while scanning: %s",
                     project["name"], key, len(ranges), " || ".join(ranges[:3]))
        out.log.append(LogEntry(SOURCE, project["name"], 0, "ok",
                                f"{key}: eth_getLogs served by {served}"
                                + (f"; provider range message(s): {' || '.join(ranges[:2])}"
                                   if ranges else "")
                                + (f" after {len(refused_by)} refusal(s): {'; '.join(refused_by)}"
                                   if refused_by else " (no endpoint refused it)"), TIER))

        by_sender: dict[str, float] = {}
        for e in events:
            by_sender[e["from"].lower()] = by_sender.get(e["from"].lower(), 0.0) + e["value"] / (10 ** dec)

        # ===== WHO IS THE STAGE 2 BURNER? =====
        stage2 = (cfg.get("stage2_burner") or {}).get("address")
        disc = (cfg.get("stage2_burner") or {}).get("discover_by") or {}
        if not stage2 and disc:
            want, tol = float(disc["approx_tokens"]), float(disc.get("tolerance_pct", 5)) / 100.0
            hits = [e for e in events
                    if want and abs(e["value"] / (10 ** dec) - want) / want <= tol]
            if len(hits) != 1:
                out.gap(project["name"], metric,
                        reason=(f"THE STAGE 2 BURNER COULD NOT BE IDENTIFIED and nothing was "
                                f"stored. {len(hits)} event(s) match {want:,.0f} tokens within "
                                f"{disc.get('tolerance_pct', 5)}%, and exactly one is required — "
                                f"two matches or none is an unresolved identification, and "
                                f"picking one would put a series under an address nobody checked. "
                                f"Senders seen: "
                                + ", ".join(f"{a} {v:,.0f}" for a, v in sorted(
                                    by_sender.items(), key=lambda kv: -kv[1])[:8])),
                        tiers_attempted="2",
                        suggestion=("Identify the Stage 2 burner from Sky's own material and set "
                                    "burn_logs.stage2_burner.address. Do NOT widen "
                                    "discover_by.tolerance_pct to force a single match."))
                refused[metric].append(f"{key} ({chain}): stage 2 burner unidentified")
                return None
            hit = hits[0]
            # THE AMOUNT ALONE IS NOT THE IDENTIFICATION. Confirm the block's DATE too — one
            # call — because a coincidental match of the same size on another day would
            # otherwise name the wrong address with no trace.
            got = time.strftime("%Y-%m-%d", time.gmtime(
                self.reader.block_timestamp(chain, hit["block"])))
            if disc.get("date") and got != disc["date"]:
                out.gap(project["name"], metric,
                        reason=(f"THE STAGE 2 BURNER WAS NOT CONFIRMED. An event of "
                                f"{hit['value'] / (10 ** dec):,.0f} tokens was found from "
                                f"{hit['from']}, but in block {hit['block']:,} dated {got}, not "
                                f"{disc['date']}. A coincidental match of the same size on "
                                f"another day would name the wrong address, so the date is "
                                f"checked and the match is rejected."),
                        tiers_attempted="2",
                        suggestion="Re-check the reference date and amount against Sky's own thread.")
                refused[metric].append(f"{key} ({chain}): burner date mismatch {got}")
                return None
            stage2 = hit["from"]
            log.info("%s/%s: Stage 2 burner DISCOVERED as %s (%.0f tokens in block %d, %s). "
                     "Write it into config so the next run gates on it instead of rediscovering.",
                     project["name"], key, stage2, hit["value"] / (10 ** dec), hit["block"], got)

        # ===== THE THREE SERIES. =====
        named = {k.lower(): v for k, v in (cfg.get("named_senders") or {}).items()}
        if stage2:
            named[stage2.lower()] = cfg.get("stage2_metric", "burn_address_balance")
        other_metric = cfg.get("other_metric", "other_burn_balance")
        totals: dict[str, float] = {}
        unrecognised: dict[str, float] = {}
        for sender, amount in by_sender.items():
            target = named.get(sender)
            if target is None:
                unrecognised[sender] = amount
                target = other_metric
            totals[target] = totals.get(target, 0.0) + amount

        suffix = (f"logs@{first_blk}-{last_blk}"
                  + (f",deployed@{discovered_from}" if discovered_from is not None else ""))
        for m, total in totals.items():
            if m == metric:
                continue
            # ** THE METRIC GOES IN BRACKETS, NOT IN THE KEY'S SLOT. ** One contract read
            # decomposes into three series, so the source has to say which one this is — but the
            # last colon-delimited piece is where every parser looks for the CONTRACT KEY, and
            # `chain:ethereum:burn_logs:governance_burn_balance` names a metric there. All three
            # of Sky's burn series would have rendered ORPHANED ("written by contract(s)
            # governance_burn_balance, which are no longer in config") the day this read stopped
            # 403ing. A bracketed annotation is stripped by every one of those parsers and by
            # _measuring_point, which is what the slot is for.
            parts[m].append((f"{chain}:{key}[{m}]", total))
            source_suffix[m] = suffix
        # A SERIES WITH NO EVENTS IS STILL A SERIES, and a zero here is measured rather than
        # missing: the scan ran and found nothing from that sender. Emitted so the column reads
        # 0 rather than going stale and looking like a broken read.
        for m in set(named.values()) | {other_metric}:
            if m not in totals and m != metric:
                parts[m].append((f"{chain}:{key}[{m}]", 0.0))
                source_suffix[m] = suffix
        if unrecognised:
            out.review_item(project["name"], other_metric, "unrecognised_burn_sender",
                            "stored_flagged", value=float(sum(unrecognised.values())),
                            date=when, source=f"{SOURCE}:{chain}:{key}", tier=TIER,
                            basis="senders: " + ", ".join(
                                f"{a} {v:,.2f}" for a, v in sorted(unrecognised.items(),
                                                                   key=lambda kv: -kv[1])[:6]))

        source_suffix[metric] = suffix
        value = totals.get(metric, 0.0)

        # ** THE FIRST READ OF THE STAGE 2 LEG IS CHECKED BEFORE IT IS KEPT. **
        # Against the DECOMPOSED figure, not the total — a full-history scan picks up governance
        # and converter burns too, so the total is legitimately far above the 2.86M reference and
        # gating on it would reject a correct read every time.
        # ** AND IT IS A FLOOR, NOT AN EQUALITY. Corrected 2026-09-22. **
        # The gate was written against a scan that started AT Stage 2, where the cumulative and
        # the reference were the same quantity on the same day. Scanning from deployment changes
        # that: the Stage 2 cumulative legitimately grows past 2,860,000 the moment a second burn
        # happens, so an equality check would reject every correct read after the first day —
        # which is the opposite of what a gate is for. The failure it exists to catch is a scan
        # that started too LATE and returns too little, and a floor catches exactly that while
        # letting the series grow.
        ref = cfg.get("first_read_reference")
        if ref and self.prior.get((project["name"], metric)) is None:
            expect, tol = float(ref["value"]), float(ref.get("tolerance_pct", 5)) / 100.0
            floor = ref.get("mode", "at_least") == "at_least"
            missed = (expect == 0 or (value < expect * (1 - tol) if floor
                                      else abs(value - expect) / expect > tol))
            if missed:
                out.review_item(project["name"], metric, "first_read_disagrees_with_reference",
                                "rejected", value=float(value), prior_value=expect, date=when,
                                source=f"{SOURCE}:{chain}:{key}", tier=TIER,
                                basis=f"{ref.get('what')} ({ref.get('as_of')}), {ref.get('source_url')}")
                out.gap(project["name"], metric,
                        reason=(f"THE FIRST STAGE 2 READ IS BELOW ITS FLOOR and was NOT stored. "
                                f"{len(events):,} burn event(s) over blocks {first_blk:,}-"
                                f"{last_blk:,} decompose to {value:,.4f} from the Stage 2 burner, "
                                f"against a floor of {expect:,.4f} ({ref.get('what')}, "
                                f"{ref.get('as_of')}). The cumulative can only GROW from that "
                                f"date, so a figure below it means the scan began after some of "
                                f"the burns — the one failure that leaves no other trace. "
                                f"Measured on the DECOMPOSED figure, not the scan total, which "
                                f"legitimately includes governance and converter burns."),
                        tiers_attempted="2",
                        suggestion=(f"Check the burner identification above, then from_block. Do "
                                    f"NOT widen tolerance_pct to make this pass."))
                refused[metric].append(f"{key} ({chain}): stage 2 first read {value:,.2f} "
                                       f"vs reference {expect:,.2f}")
                return None
        return value

    def _emit_parts(self, project: dict, parts: dict, partial_metrics: set, refused: dict, when, out,
                    disputed: dict | None = None, source_suffix: dict | None = None,
                    metric_when: dict | None = None, partial_reasons: dict | None = None):
        """Emit one figure per metric, summing every contract that served it."""
        name = project["name"]
        source_suffix = source_suffix or {}
        metric_when = metric_when or {}
        run_date = when
        for metric, components in parts.items():
            # A PERIODIC READ IS FILED UNDER ITS PERIOD. Defaults to the run date, which is right
            # for every balance read; a weekly read overrides it with its epoch start so repeated
            # runs inside one epoch collapse onto one row instead of accumulating.
            when = metric_when.get(metric, run_date)
            total = sum(v for _, v in components)
            _check_component_labels(project, metric, components)
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

            # WHICH FORMULA PRODUCED IT, in the source string. Where one contract can yield a
            # figure two ways, a series that changes basis mid-history would otherwise be an
            # unexplained step change with nothing in the row to explain it.
            if source_suffix.get(metric):
                src += f"[{source_suffix[metric]}]"

            missing = refused.get(metric) or []
            is_partial = (metric in partial_metrics or bool(missing)
                          or (metric == "total_supply" and project.get("supply_is_partial")))
            if is_partial:
                src = config.mark_source(src, "PARTIAL")
                detail += " [PARTIAL]"
                reason = (f"{len(missing)} component(s) refused: {'; '.join(missing)}" if missing
                          else (partial_reasons or {}).get(metric)
                          or project.get("supply_partial_reason") or "not every component is known")
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

            flow_metric = config.cumulative_flow_for(name, metric)
            if flow_metric:
                flow = derive_flow_from_cumulative(total, self.prior_delta.get((name, metric)), name,
                                                   flow_metric, config.mark_source(src, "delta"), TIER, when,
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
                        clean["source"] = config.mark_source(
                            config.mark_source(src, "delta"), "recurring-only")
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
        # THE ADDRESS NAMED IS THE ONE THAT WAS READ. Contract kinds are named after the stock
        # metric they serve, so the holders behind `stock_metric` are the contracts of that kind.
        # This used to list burn kinds only, so Chainlink's Reserve (kind buyback_fund_balance,
        # on file and read) was reported as "(no address on file)" — a stale message, not lost
        # wiring. Fixed 2026-09-23.
        holders = {k: v for k, v in (project.get("contracts") or {}).items()
                   if v.get("kind") in (stock_metric, "spl_token_account")}
        token = (project.get("contracts") or {}).get("token", {})
        where = "; ".join(f"{k} {v.get('address')} on {v.get('chain')}" for k, v in holders.items()) or "(no address on file)"

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

