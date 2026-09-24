#!/usr/bin/env python3
"""
check_offline_items.py — every check this sandbox cannot reach, in one command.

    python check_offline_items.py              run every check
    python check_offline_items.py <name>       run just that one — exact name, or an
                                                unambiguous prefix (e.g. "beaconchain")
    python check_offline_items.py --list       print the check names, run nothing

The build environment's proxy blocks chain RPCs, Cosmos LCDs and beaconcha.in, so a handful of
questions have been stacking up unanswered. This runs all of them from a machine that has real
network, and prints results in a form that can be pasted straight back. When only one question
is live, the positional filter runs just that check instead of scrolling past the other 20-odd.

Reads only. It touches no contract state, writes nothing to the store, and needs no API key.
"""
from __future__ import annotations

import json
import os
import sys
import time
from urllib.parse import urlparse

import requests

TIMEOUT = 25

# Sky's Splitter/Flapper. The 2024 executive vote set this flapper; SBEBeam has allowed
# reconfiguration since, so the point is to check whether it is STILL this one.
SKY_FLAPPER = "0x374D9c3d5134052Bc558F432Afa1df6575f07407"
# ORDER MATTERS. llamarpc returned 525 for want() and spotter() on two separate days while
# publicnode answered every other call in the same script, so that is not a transient and
# llamarpc is no longer tried first.
_PUBLIC_ETH_RPCS = ["https://ethereum-rpc.publicnode.com", "https://eth.llamarpc.com",
                    "https://rpc.ankr.com/eth", "https://cloudflare-eth.com",
                    "https://eth.drpc.org"]


def _rpc_host(url) -> str:
    """Host only. ** A KEYED ENDPOINT CARRIES ITS KEY IN THE PATH, and this script PRINTS. **

    Everything here goes to stdout to be pasted back into chat, so a full URL in an error line
    is a key in a chat log. Degrades to a placeholder rather than to the raw string: falling
    back to the raw string is how a redactor leaks.
    """
    try:
        return urlparse(str(url)).hostname or "<unparseable endpoint>"
    except Exception:  # noqa: BLE001 — a redactor must never raise
        return "<unparseable endpoint>"


def _eth_rpcs() -> list:
    """ETHEREUM_RPC_URL first, the public list behind it. Same contract as fetch.chain.

    PREPENDED, NOT SUBSTITUTED: the keyed endpoint is what serves eth_getLogs, and the public
    ones answer everything else perfectly well. Losing four working endpoints to gain logs would
    be a bad trade, and it is not one that has to be made.
    """
    keyed = os.environ.get("ETHEREUM_RPC_URL", "").strip()
    urls = [u.strip() for u in keyed.split(",") if u.strip()] if keyed else []
    urls += _PUBLIC_ETH_RPCS
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


ETH_RPCS = _eth_rpcs()

# Sky's canonical on-chain registry. It exists precisely so integrators never hardcode an address
# governance might change — which is the failure mode that produced a superseded pip in config.
SKY_CHAINLOG = "0xdA0Ab1e0017DEbCd72Be8599041a2aa3bA7e740F"
# ** A FOURTH WRONG SELECTOR, FOUND 2026-09-23 BY WIDENING THE CHECK. ** list() was
# 0x63b0c6b0; keccak says 0x0f560cd7. That is the call that enumerates every ChainLog key so the
# registry key is READ rather than guessed — so the one check built to avoid guessing an address
# was itself calling a function that does not exist, and would have reported "UNREACHABLE".
#
# Three of these were found on 2026-09-23 by checking the three selector DICTS. This one survived
# because it is a bare module constant and the check did not look at those. It does now: every
# selector in this file is derived and compared, whatever shape it is declared in.
CHAINLOG_LIST = "0x0f560cd7"       # keccak("list()")[:4]
CHAINLOG_GET = "0x21f8a721"        # keccak("getAddress(bytes32)")[:4]
# keccak("receiver()")[:4] — SwapOnly exposes the address it sends bought SKY to.
# ** TWO OF THESE WERE WRONG, AND THE SYMPTOM WAS BLAMED ON A PROVIDER. ** Corrected 2026-09-23
# by _assert_selectors, which checks every one against keccak at import:
#     want()     was 0x1f1c827f, is 0x1f1fcd51
#     spotter()  was 0xf3701da2, is 0x2e77468d
# Those are EXACTLY the two calls the Sky flapper question recorded as failing while every other
# call in the same script answered — and the conclusion drawn was that llamarpc was unreliable
# and should not be tried first. A selector that matches no function on the contract is a
# sufficient explanation for a failure that is consistent, repeatable and confined to those two
# calls, which is what was observed. The endpoint ordering is left as it is (it costs nothing),
# but the diagnosis on the config entry is corrected.
SELECTORS = {"receiver()": "0xf7260d3e", "pair()": "0xa8aa1b31", "want()": "0x1f1fcd51",
             "pip()": "0xd741e2f9", "spotter()": "0x2e77468d"}
# ** THE flapper() SELECTOR WAS WRONG. ** Corrected 2026-09-23: keccak("flapper()")[:4] is
# 0x5ca0d723, not 0x5c94e4d2. The old value matches no function on the Splitter, so the call
# would have reverted or returned empty and the check would have read "UNREACHABLE" — a network
# answer to what was actually a typo. Every selector in this file is now machine-checked against
# keccak at import; see _assert_selectors below.
SELECTORS_SPLITTER = {"flapper()": "0x5ca0d723"}

# Sky's Splitter (ChainLog MCD_SPLIT), from config — defaulted so the two new checks below run
# without anyone having to remember to pass it.
SKY_SPLITTER = "0xBF7111F13386d23cb2Fba5A538107A73f6872bCF"
# A FLOOR FOR THE File SCAN, not an estimate of deployment. dss-flappers went live with the
# Smart Burn Engine in mid-2023; 17,000,000 is comfortably before that and cheap to scan past.
# Lower it rather than raise it if in doubt.
SKY_SPLITTER_FROM_BLOCK = 17_000_000

# ===== THE SPLITTER'S OWN PARAMETERS, AND ITS HISTORY. Added 2026-09-23. =====
# `burn` is the WAD share of each SBE cycle sent to the flapper (SKY buybacks) rather than to the
# reward farm. Sky's executive of 2026-08-13 set it to 55%, which reconciles exactly with the
# three-way Stage 2 allocation of 50% of NPS: 27.5/50 = 0.55 to buybacks, 22.5/50 = 0.45 to the
# LSSKY-USDS farm. A live read of 0.55e18 confirms that reading against the chain rather than
# against a reading of a proposal.
SELECTORS_SPLITTER_PARAMS = {"burn()": "0x44df8e70", "hop()": "0xb0b8579b"}
SPLITTER_BURN_EXPECTED_WAD = 0.55

# Aethir staking probe (aethir_staking_probe, below). Checked against keccak at import.
SELECTORS_AETHIR = {"token()": "0xfc0c546a", "supply()": "0x047fc9aa",
                    "totalATH()": "0x0d97beca", "totalDeposited()": "0xff50abdc",
                    "totalEscrowed()": "0xf9168231", "aethirStrategy()": "0x8d214897",
                    # the wrapper at 0x3f69… (DefiLlama's "Aethir staking"), 2026-09-24
                    "aethir()": "0xb8df02f7", "stAethir()": "0xfa56fc42", "veAethir()": "0x5fe36136",
                    # VeAethir itself (0x1b49f587…), and the ERC-4626-style accessors probed on
                    # it DEFENSIVELY even though its verified ABI (Keystone registry) lists none
                    # of them — a live call is what actually proves absence, not a downloaded ABI.
                    "owner()": "0x8da5cb5b", "asset()": "0x38d52e0f", "underlying()": "0x6f307dc3",
                    "totalAssets()": "0x01e1d114"}

# Splitter.file(bytes32 what, uint256 data) emits File(bytes32 indexed what, uint256 data).
# EVERY change to `burn` and `hop` since deployment is in these logs — which makes the pre-August
# split a matter of reading the chain rather than of finding a document nobody published.
FILE_TOPIC_UINT = "0xe986e40cc8c151830d4f61050f4fb2e4add8567caad2d5f5496f9158e91fe4c7"
WHAT_BURN = "0x6275726e" + "0" * 56      # bytes32("burn")
WHAT_HOP = "0x686f70" + "0" * 58         # bytes32("hop")

# Ether.fi's governance token and its staking contract. The open question is what sETHFI IS:
# a 1:1 receipt, or a share that compounds against ETHFI the way a vault share does. It decides
# what a gap between locked_tokens (shares) and locked_tokens_underlying (assets) MEANS, and the
# two answers point opposite ways — so it is read, not inferred from documentation.
ETHFI = "0xFe0c30065B384F05761f15d0CC899D4F9F9Cc0eB"
SETHFI = "0x86B5780b606940Eb59A062aA85a07959518c0161"
SEL_TOTAL_SUPPLY = "0x18160ddd"    # keccak("totalSupply()")[:4]
SEL_BALANCE_OF = "0x70a08231"      # keccak("balanceOf(address)")[:4]
SEL_DECIMALS = "0x313ce567"        # keccak("decimals()")[:4]
SEL_THRESHOLD = "0x42cde4e8"       # keccak("threshold()")[:4]
# veAERO (Aerodrome VotingEscrow) — the three reads the lock-duration proxy depends on.
# ** permanentLockBalance() WAS WRITTEN FROM MEMORY AS 0xa4d49d90 AND THAT IS WRONG. ** keccak
# says 0x4d01cb66. The fifth hand-written selector in this file to be wrong, and the first to be
# caught before it ran — because these are declared HERE, where _assert_selectors sees them,
# rather than beside the function that uses them.
SEL_VE_SUPPLY = "0x047fc9aa"       # keccak("supply()")[:4]
SEL_VE_PERMANENT = "0x4d01cb66"    # keccak("permanentLockBalance()")[:4]
SEL_VE_EPOCH = "0x900cf0cf"        # keccak("epoch()")[:4]
AERO_TOKEN = "0x940181a94A35A4569E4529A3CDfB74e38FD98631"
VEAERO = "0xeBf418Fe2512e7E6bd9b87a8F0f294aCDC67e6B4"
# Base, for the veAERO reads. Same shape as ETH_RPCS and the same preference rule: a keyed
# endpoint in BASE_RPC_URL goes first, the public ones stay behind it.
_PUBLIC_BASE_RPCS = ["https://base-rpc.publicnode.com", "https://mainnet.base.org",
                     "https://base.llamarpc.com"]

# --- addresses for the three checks added 2026-09-17 -------------------------------------
SYRUP = "0x643C4E15d7d62Ad0aBeC4a9BD4b001aA3Ef52d66"           # Maple SYRUP token
MAPLE_DAO_MULTISIG = "0xd6d4Bcde6c816F17889f1Dd3000aF0261B03a196"
PENDLE = "0x808507121B80c02388fAd14726482e061B8da827"
SPENDLE = "0x999999999991E178D52Cd95AFd4b00d066664144"
UNI_FIRE_PIT = "0x0D5Cd355e2aBEB8fb1552F56c965B867346d6721"


def _assert_selectors() -> None:
    """Every four-byte selector in this file, checked against keccak at import.

    ** ONE OF THEM WAS WRONG AND THE FAILURE LOOKED LIKE A NETWORK PROBLEM. ** flapper() was
    0x5c94e4d2, which matches no function on the Splitter, so the call returned nothing and the
    check printed "UNREACHABLE" — a network answer to a typo. A selector is the one thing in this
    script that can be verified without a network, so it is.

    SKIPPED SILENTLY IF NO KECCAK IS INSTALLED. This script is meant to run on someone else's
    machine with nothing but `requests`; refusing to start because a hashing library is missing
    would cost more than the check is worth.
    """
    try:
        from eth_utils import keccak                       # noqa: PLC0415
    except ImportError:
        try:
            from Crypto.Hash import keccak as _k           # noqa: PLC0415

            def keccak(text=b""):
                h = _k.new(digest_bits=256)
                h.update(text)
                return h.digest()
        except ImportError:
            return
    # ** EVERY SELECTOR IN THE FILE, WHATEVER SHAPE IT IS DECLARED IN. ** The first version of
    # this checked the three DICTS only, and a fourth wrong selector survived it — list(), a bare
    # module constant. Bare constants are named here explicitly rather than discovered, so adding
    # one and forgetting to register it is a visible omission and not a silent gap.
    tables = [SELECTORS, SELECTORS_SPLITTER, SELECTORS_SPLITTER_PARAMS, SELECTORS_AETHIR,
              {"list()": CHAINLOG_LIST, "getAddress(bytes32)": CHAINLOG_GET,
               "totalSupply()": SEL_TOTAL_SUPPLY, "balanceOf(address)": SEL_BALANCE_OF,
               "decimals()": SEL_DECIMALS, "threshold()": SEL_THRESHOLD,
               # ** A FIFTH WRONG SELECTOR, CAUGHT BEFORE IT RAN. ** permanentLockBalance() was
               # written from memory as 0xa4d49d90; keccak says 0x4d01cb66. Registering these
               # here is what turned that into a one-line correction instead of another
               # "UNREACHABLE" that reads as a network fault.
               "supply()": SEL_VE_SUPPLY, "permanentLockBalance()": SEL_VE_PERMANENT,
               "epoch()": SEL_VE_EPOCH}]
    wrong = []
    for table in tables:
        for sig, sel in table.items():
            want = "0x" + keccak(sig.encode()).hex()[:8]
            if want != sel:
                wrong.append(f"{sig}: file has {sel}, keccak says {want}")
    # AND THE EVENT TOPIC, which is a full 32 bytes rather than four and would not be caught by
    # the loop above. Same class of typo, same cost: a wrong topic matches nothing and reads as
    # "no events found", which is indistinguishable from "the parameter never changed".
    want_topic = "0x" + keccak(b"File(bytes32,uint256)").hex()
    if want_topic != FILE_TOPIC_UINT:
        wrong.append(f"File(bytes32,uint256): file has {FILE_TOPIC_UINT}, keccak says {want_topic}")
    if wrong:
        raise SystemExit("SELECTOR MISMATCH — fix these before running:\n  "
                         + "\n  ".join(wrong))


_assert_selectors()


def head(title: str):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def rpc(url: str, method: str, params=None):
    r = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []},
                      timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def eth_block_number(chain: str = "ethereum"):
    """The current block, so a pair of reads can be pinned to ONE state rather than two."""
    for url in _rpcs_for(chain):
        try:
            j = rpc(url, "eth_blockNumber")
            if "result" in j:
                return j["result"], url
        except Exception:  # noqa: BLE001
            continue
    return None, None


def _rpcs_for(chain: str) -> list:
    """The endpoint list for a chain, keyed endpoint first. Ethereum and Base only.

    ** ADDED BECAUSE A BASE CONTRACT WAS ABOUT TO BE CALLED AGAINST ETHEREUM RPCs. ** Every
    helper here was Ethereum-only, and the veAERO reads are on Base — which would have returned
    "no code at address" or silence, and read as the contract being wrong rather than the
    endpoint. The chain is now named at the call site.
    """
    if chain == "base":
        keyed = os.environ.get("BASE_RPC_URL", "").strip()
        urls = [u.strip() for u in keyed.split(",") if u.strip()] if keyed else []
        urls += _PUBLIC_BASE_RPCS
        seen, out = set(), []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out
    if chain != "ethereum":
        # ARBITRUM AND POLYGON (2026-09-24): the pipeline's own list, keyed endpoint first, so the
        # probe and the run read through the same endpoints. Imported lazily — this script is
        # otherwise standalone.
        try:
            from fetch.chain import rpc_endpoints        # noqa: PLC0415
            return rpc_endpoints(chain)
        except Exception:  # noqa: BLE001
            return []
    return ETH_RPCS


def eth_call(to: str, selector: str, block: str = "latest", chain: str = "ethereum"):
    """Try each endpoint until one answers. Returns (result_hex, endpoint) or (None, error).

    `block` pins the read. Two calls at "latest" can straddle a block boundary, which for a
    ratio of two figures is the difference between a measurement and a coincidence.
    """
    errors = []
    for url in _rpcs_for(chain):
        try:
            j = rpc(url, "eth_call", [{"to": to, "data": selector}, block])
            if "result" in j:
                return j["result"], url
            errors.append(f"{_rpc_host(url)}: {j.get('error')}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{_rpc_host(url)}: {e}")
    return None, "; ".join(errors)


def as_address(word: str | None) -> str | None:
    if not word or len(word) < 42:
        return None
    body = word[2:] if word.startswith("0x") else word
    return "0x" + body[-40:]


def _decode_bytes32_array(word: str) -> list[str]:
    """ABI-decode a bytes32[] return value into readable key names."""
    raw = word[2:] if word.startswith("0x") else word
    if len(raw) < 128:
        return []
    count = int(raw[64:128], 16)
    keys = []
    for i in range(count):
        chunk = raw[128 + i * 64: 128 + (i + 1) * 64]
        if len(chunk) < 64:
            break
        keys.append(bytes.fromhex(chunk).rstrip(b"\x00").decode("utf-8", "replace"))
    return keys


def sky_chainlog():
    """Resolve Sky's live contracts from the registry rather than from a historical vote.

    The registry is the answer to the whole class of problem that has bitten this project twice:
    an address read from a 2024 executive vote was superseded (pip), and the Splitter's current
    flapper could not be checked at all because nothing on file named the Splitter. list() is
    called FIRST and printed in full, because guessing the registered key blind is the same
    mistake one level up — it may not be "MCD_SPLIT".
    """
    head("SKY CHAINLOG — the canonical registry. Every live Sky address, from Sky itself.")
    word, src = eth_call(SKY_CHAINLOG, CHAINLOG_LIST)
    if word is None:
        print(f"  list() UNREACHABLE  {src[:150]}")
        return
    keys = _decode_bytes32_array(word)
    print(f"  list() returned {len(keys)} registered key(s):\n")
    for i in range(0, len(keys), 4):
        print("    " + "  ".join(k.ljust(26) for k in keys[i:i + 4]))

    # Anything that could plausibly BE the splitter or the flapper. Printed, never auto-chosen.
    interesting = [k for k in keys
                   if any(t in k.upper() for t in ("SPLIT", "FLAP", "BURN", "VOW", "PAUSE", "SKY"))]
    print(f"\n  Candidates worth resolving ({len(interesting)}):")
    for key in interesting:
        padded = key.encode("utf-8").ljust(32, b"\x00").hex()
        got, _ = eth_call(SKY_CHAINLOG, CHAINLOG_GET + padded)
        addr = as_address(got)
        flag = ""
        if addr and addr.lower() == SKY_FLAPPER.lower():
            flag = "   <-- MATCHES the 2024 vote's flapper"
        print(f"    {key.ljust(26)} {addr}{flag}")
    print("\n  PASTE BACK the whole list. The key naming the Splitter tells us which flapper is")
    print("  live; if its flapper is not 0x374D9c3d..., the LP question reopens and every Sky")
    print("  check needs re-running against the new one.")


def sky_splitter(splitter: str | None):
    """Which flapper is the SPLITTER actually pointing at RIGHT NOW?

    Reading the flapper's own parameters says what THAT contract is configured to do. It does not
    say the Splitter still uses it — SBEBeam lets facilitators re-point the splitter, and if it now
    points at FlapperUniV2 rather than FlapperUniV2SwapOnly, the LP-position question reopens.
    """
    head("SKY — which flapper is the SPLITTER pointing at? (the question the flapper read cannot answer)")
    if not splitter:
        print("  SKIPPED — the Splitter's address is not on file and is NOT guessed here.")
        print("  Supply it and re-run:  python check_offline_items.py --splitter 0x...")
        print("  It is the contract the Smart Burn Engine's surplus flows through; Sky's own")
        print("  governance material or the dss-flappers deployment list names it.")
        return
    word, src = eth_call(splitter, SELECTORS_SPLITTER["flapper()"])
    if word is None:
        print(f"  flapper()   UNREACHABLE  {src[:110]}")
        return
    live = as_address(word)
    print(f"  splitter    {splitter}")
    print(f"  flapper()   {live}")
    expected = SKY_FLAPPER.lower()
    if live and live.lower() == expected:
        print("  MATCHES the 2024 executive vote — FlapperUniV2SwapOnly is still active, and the")
        print("  retired LP-position concern stays retired.")
    else:
        print("  *** DOES NOT MATCH the 2024 vote's 0x374D9c3d... ***")
        print("  The splitter has been re-pointed. Report this back: if the live flapper is a")
        print("  FlapperUniV2 rather than SwapOnly, the LP question reopens and Sky's archetype")
        print("  needs looking at again.")


def explorer_logs(chain_id: int, address: str, topics: list, from_block: int = 0,
                 to_block="latest"):
    """(logs, detail) via fetch/explorer.py — Etherscan V2 / Blockscout, paged by record count.

    ** THE RPC RANGE CAP DOES NOT APPLY HERE, which is why the two log checks below try this
    first. ** Needs ETHERSCAN_API_KEY or BLOCKSCOUT_API_KEY in .env; returns (None, why) without
    one, and the caller falls back to eth_getLogs. Logs come back with INT blockNumber/timeStamp.
    """
    try:
        from dotenv import load_dotenv                    # noqa: PLC0415
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    except ImportError:
        pass
    try:
        from fetch.explorer import ExplorerLogs, ExplorerRefused   # noqa: PLC0415
    except ImportError as e:
        return None, f"fetch.explorer not importable: {e}"
    ex = ExplorerLogs()
    if not ex.configured(chain_id):
        return None, "no explorer key in .env for this chain"
    try:
        logs, meta = ex.get_logs(chain_id, address, topics, from_block, to_block)
    except ExplorerRefused as e:
        return None, f"explorers refused: {e}"
    return logs, (f"{len(logs)} log(s), served by {meta['explorer']} in {meta['requests']} "
                  f"request(s)" + (f" after: {'; '.join(meta['refused'])}" if meta["refused"] else ""))


def eth_get_logs(address: str, topics: list, from_block: int, to_block: int, chunk: int = 50_000):
    """Every matching log between two blocks, chunked, trying each endpoint.

    CHUNKED BECAUSE PUBLIC ENDPOINTS CAP THE RANGE, and NARROWING on the server's own complaint
    rather than blind-retrying: a range refusal is answered by halving, anything else moves to
    the next endpoint. Same discipline as fetch/chain.py, kept simple because this script is a
    one-shot and not part of a run.
    """
    out, block = [], from_block
    while block <= to_block:
        upper = min(block + chunk - 1, to_block)
        got, errors = None, []
        for url in ETH_RPCS:
            try:
                j = rpc(url, "eth_getLogs", [{"address": address, "topics": topics,
                                              "fromBlock": hex(block), "toBlock": hex(upper)}])
                if "result" in j:
                    got = j["result"]
                    break
                errors.append(f"{_rpc_host(url)}: {j.get('error')}")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{_rpc_host(url)}: {e}")
        if got is None:
            joined = "; ".join(errors).lower()
            if chunk > 1_000 and any(w in joined for w in
                                     ("range", "too many", "limit", "response size", "timeout")):
                chunk //= 2
                continue
            return None, "; ".join(errors)[:400]
        out.extend(got)
        block = upper + 1
    return out, f"{len(out)} log(s) over blocks {from_block:,}-{to_block:,}"


def block_time(block_hex: str) -> str:
    """UTC date of a block, so a parameter change is dated rather than merely ordered."""
    import datetime as dt
    for url in ETH_RPCS:
        try:
            j = rpc(url, "eth_getBlockByNumber", [block_hex, False])
            ts = (j.get("result") or {}).get("timestamp")
            if ts:
                return dt.datetime.fromtimestamp(int(ts, 16), dt.timezone.utc).strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            continue
    return "?"


def sky_splitter_params(splitter: str | None):
    """Does the Splitter's own `burn` parameter read 0.55e18, as the 2026-08-13 executive says?

    ** THE 55/45 WAS READ AS A BURN/STAKER SPLIT AND IT IS NOT ONE. ** It is THIS parameter: the
    share of each SBE cycle sent to the flapper for SKY buybacks rather than to the reward farm.
    The corrected reading reconciles it with the three-way Stage 2 allocation of 50% of NPS —
    27.5/50 = 0.55 to buybacks (22.5 staking + 5.0 burn), 22.5/50 = 0.45 to the LSSKY-USDS farm.

    So this read is the one thing that can tell the corrected reading from the rejected one
    against the chain rather than against somebody's reading of a proposal. 0.55e18 confirms it;
    anything else means the executive was not applied as understood and the whole Stage 2
    arithmetic goes back to the start.
    """
    head("SKY — does the Splitter's burn parameter read 0.55e18? (the 55/45, on-chain)")
    if not splitter:
        print("  SKIPPED — the Splitter's address is not on file and is NOT guessed here.")
        return
    for name, sel in SELECTORS_SPLITTER_PARAMS.items():
        word, src = eth_call(splitter, sel)
        if word is None:
            print(f"  {name:<8} UNREACHABLE  {src[:110]}")
            continue
        raw = int(word, 16)
        if name == "burn()":
            wad = raw / 1e18
            verdict = ("MATCHES the 2026-08-13 executive — 55% of each SBE cycle to SKY buybacks"
                       if abs(wad - SPLITTER_BURN_EXPECTED_WAD) < 1e-9 else
                       "*** DOES NOT MATCH the expected 0.55 ***")
            print(f"  burn()   {raw} = {wad:.6f} WAD   {verdict}")
            if abs(wad - SPLITTER_BURN_EXPECTED_WAD) >= 1e-9:
                print("  PASTE BACK the figure. Do NOT adjust config to whatever it reads without")
                print("  finding the executive that changed it — an unexplained parameter is a")
                print("  question, not a new constant.")
        else:
            print(f"  hop()    {raw} seconds ({raw / 3600:.2f} hours between kicks)")


def sky_splitter_history(splitter: str | None, from_block: int):
    """The dated history of `burn` and `hop`, rebuilt from the Splitter's own File events.

    ** THE PRE-AUGUST SPLIT WAS BEING TREATED AS AN UNDOCUMENTED FACT. ** It is not undocumented;
    it is just not in prose. Every change to the parameter emitted File(what, data), so the whole
    history is on chain, dated by block timestamp — a PRIMARY source, and a better one than a
    governance post, because it is what the contract actually did rather than what a proposal
    said it would do.

    Layer 1 (the share of surplus reaching the Splitter at all) is separately known: 75% ->
    7.5% interim in April 2026 -> Stage 2 from 2026-08-13. This rebuilds LAYER 2, the Splitter's
    own division of what reaches it.
    """
    head("SKY — the Splitter's parameter history, from its own File events")
    if not splitter:
        print("  SKIPPED — the Splitter's address is not on file and is NOT guessed here.")
        return
    # THE EXPLORER FIRST (2026-09-24): the RPC route is capped at 10 blocks per request on
    # Alchemy's free tier, which is what left this history unread. Rows are normalised to
    # (block, name, raw, date) either way.
    ex_logs, ex_detail = explorer_logs(1, splitter, [FILE_TOPIC_UINT], from_block)
    if ex_logs is not None:
        print(f"  {ex_detail}")
        if not ex_logs:
            print("  NO File EVENTS from the explorer. Same caution as below: a scan that began")
            print("  after every change is not a history.")
            return
        rows = []
        for lg in ex_logs:
            topics = lg.get("topics") or []
            if len(topics) < 2:
                continue
            what = topics[1].lower()
            name = ("burn" if what == WHAT_BURN else "hop" if what == WHAT_HOP else what)
            rows.append((lg["blockNumber"], name, int(lg.get("data") or "0x0", 16),
                         time.strftime("%Y-%m-%d", time.gmtime(lg["timeStamp"]))))
        _print_splitter_rows(sorted(rows))
        return
    print(f"  explorer route unavailable ({ex_detail}) — trying RPC eth_getLogs")
    head_hex, _ = eth_block_number()
    if not head_hex:
        print("  UNREACHABLE — no endpoint answered eth_blockNumber.")
        return
    logs, detail = eth_get_logs(splitter, [FILE_TOPIC_UINT], from_block, int(head_hex, 16))
    if logs is None:
        print(f"  eth_getLogs UNREACHABLE  {detail}")
        print("  If every endpoint refused the METHOD (403/unsupported), the remedy is an")
        print("  endpoint that serves logs, not a narrower range.")
        return
    print(f"  {detail}")
    if not logs:
        print("  NO File EVENTS in range. Either the scan began after every change, or the")
        print("  parameter has never been filed. Those are different facts: re-run with a")
        print("  --splitter-from-block at or before the Splitter's deployment before concluding")
        print("  anything. A history that starts too late is not a shorter history, it is a")
        print("  wrong one.")
        return
    rows = []
    for lg in logs:
        topics = lg.get("topics") or []
        if len(topics) < 2:
            continue
        what = topics[1].lower()
        name = ("burn" if what == WHAT_BURN else "hop" if what == WHAT_HOP else what)
        raw = int(lg.get("data") or "0x0", 16)
        rows.append((int(lg["blockNumber"], 16), name, raw, block_time(lg["blockNumber"])))
    _print_splitter_rows(sorted(rows))


def _print_splitter_rows(rows: list) -> None:
    """The File-event table, one row per parameter change: (block, name, raw, date)."""
    print(f"\n  {'block':>10}  {'date':<12} {'what':<8} {'raw':>22}  reading")
    for blk, name, raw, when in rows:
        reading = (f"{raw / 1e18:.6f} WAD = {raw / 1e16:.2f}% to buybacks" if name == "burn"
                   else f"{raw} s = {raw / 3600:.2f} h" if name == "hop" else "")
        print(f"  {blk:>10,}  {when:<12} {name:<8} {raw:>22}  {reading}")
    print("\n  LAYER 2 ONLY. This is the Splitter's own division of what reaches it; the April")
    print("  2026 LAYER 1 change (the share of surplus reaching the Splitter at all) is not a")
    print("  Splitter parameter and is not in these events.")
    print("\n  PASTE BACK the whole table. Each row is one dated period boundary for Sky's")
    print("  fee_split.history LAYER 2 — and the pre-2026-08-13 periods stop being 'unconfirmed'")
    print("  the moment this table exists. Do NOT fill a period from the period after it.")


def morpho_blue_api():
    """Does Morpho's own GraphQL API answer, and DOES ITS SUM MEAN WHAT WE THINK IT MEANS?

    ** THE FIRST RUN CAME BACK WITH A NUMBER THAT CANNOT BE SUPPLY. ** 7,868 markets summing to
    $39.47bn supplied against $38.73bn borrowed is 98.1% aggregate utilisation. No lending
    protocol runs at 98%: at that level borrowers cannot be liquidated and lenders cannot
    withdraw, which is the definition of the thing every risk parameter exists to prevent. The
    first page alone read 96.3%, so it is not one outlier market — it is the population.

    ** SO THE ROUTE STAYS UNCONFIRMED UNTIL THE SUM IS EXPLAINED, NOT UNTIL IT IS FETCHED. **
    Confirming it on the grounds that both fields are present would swap a KNOWN bias
    (DefiLlama's collateral-inflated denominator, which reads too LOW) for an unknown one that
    reads too high, and the second is worse precisely because nothing on the label warns of it.

    THE LEADING HYPOTHESIS, and what this probe now measures:

      MORPHO BLUE IS PERMISSIONLESS. Anyone can create a market with any oracle, and the api
      lists every one of them. A market whose loan asset is a worthless token with a fabricated
      oracle price contributes an arbitrary supplyAssetsUsd, and because the creator lends to
      themselves it contributes almost exactly the same borrowAssetsUsd — which is what drags an
      aggregate to 98%. Real markets hold idle liquidity; a self-dealt one does not.

      ** DEFILLAMA FILTERS EXACTLY THIS, AND THE FILTER IS THE EVIDENCE. ** From their own
      utils/scripts/findInsolventMarkets.js, read 2026-09-23:

        const API_MIN_USD = 1000
        const contradictsMarket = (m) => m.listed === true && !redTypes(m).length ...
        const queuesMarket = (m) => usdOf(m.badDebt) > API_MIN_USD
                                    && !!m.state && m.state.supplyAssetsUsd > API_MIN_USD

      `listed` is a real field on Market and they gate on it being TRUE, with a $1,000 floor
      beside it. They do not trust the unfiltered population either.

    The other two candidates are measured here too rather than argued about:
      (b) DOUBLE COUNTING VAULT AND MARKET — a MetaMorpho vault's deposits sit IN the markets it
          allocates to, so summing vaults and markets together counts them twice. This probe sums
          MARKETS ONLY, so if the total is still 8x TVL that candidate is dead.
      (c) THE SAME MARKET ON SEVERAL CHAINS — measured by grouping on marketId across chain ids.

    THE CROSS-CHECK IS THE DECIDER. DefiLlama's morpho-blue TVL is the same quantity by a
    different route. Order-of-magnitude agreement means supplyAssetsUsd is total supplied;
    an 8x gap means it is not, whatever it is called.
    """
    head("MORPHO — what IS the blue-api sum? (98.1% utilisation is not a lending protocol)")
    url = "https://blue-api.morpho.org/graphql"
    q = ("query($c:[Int!],$skip:Int!,$first:Int!){ markets(first:$first, skip:$skip, "
         "where:{chainId_in:$c}){ pageInfo{countTotal} items{ marketId chain{id} listed "
         "loanAsset{symbol} collateralAsset{symbol} "
         "state{ supplyAssetsUsd borrowAssetsUsd } } } }")
    try:
        chains = requests.post(url, json={"query": "{ chains { id } }"}, timeout=TIMEOUT).json()
    except Exception as e:  # noqa: BLE001
        print(f"  UNREACHABLE  {e}")
        print("  Leave Morpho lending_api.status as 'unconfirmed'. The DefiLlama route stays,")
        print("  with its bias on the label — that is the correct state, not a fallback.")
        return
    ids = [c["id"] for c in ((chains.get("data") or {}).get("chains") or [])]
    print(f"  chains       {len(ids)}: {ids[:14]}")
    if not ids:
        print(f"  *** no chains returned. Response keys: {sorted(chains)} ***")
        return

    # ===== EVERY MARKET, NOT ONE PAGE. ** A DISTRIBUTION CANNOT BE READ OFF 50 ROWS. ** The
    # question is what share of $39.47bn sits in how few markets, and a first page ordered by
    # whatever the api defaults to answers that with a number that is not the population's.
    items, total = [], None
    for skip in range(0, 40_000, 1_000):
        try:
            page = requests.post(url, json={"query": q,
                                            "variables": {"c": ids, "skip": skip, "first": 1000}},
                                 timeout=TIMEOUT).json()
        except Exception as e:  # noqa: BLE001
            print(f"  markets query FAILED at skip={skip}  {e}")
            return
        if page.get("errors"):
            # ** A GraphQL ERROR IS A 200. ** The transport succeeded and the query did not, and
            # a field name that does not exist is reported here rather than as a missing value.
            print(f"  *** GraphQL errors — most likely a FIELD NAME: {page['errors']} ***")
            print("  If it names borrowAssetsUsd, find the right spelling and put it in Morpho's")
            print("  lending_api.borrow_field. If it names `listed`, say so — the whole")
            print("  filtered-population reading below depends on that field existing.")
            return
        node = ((page.get("data") or {}).get("markets") or {})
        got = node.get("items") or []
        total = (node.get("pageInfo") or {}).get("countTotal") or total
        items.extend(got)
        if not got or (total and len(items) >= total):
            break
    print(f"  markets      {len(items)} of {total} returned")
    if not items:
        print("  *** no market items. Nothing to confirm. ***")
        return

    def st(i, k):
        v = (i.get("state") or {}).get(k)
        return float(v) if isinstance(v, (int, float)) else None

    # ---- (0) field presence, over the WHOLE population rather than one page.
    have_supply = sum(1 for i in items if st(i, "supplyAssetsUsd") is not None)
    have_borrow = sum(1 for i in items if st(i, "borrowAssetsUsd") is not None)
    print(f"\n  FIELD PRESENCE (all {len(items)} markets)")
    print(f"    supplyAssetsUsd  {have_supply}/{len(items)}")
    print(f"    borrowAssetsUsd  {have_borrow}/{len(items)}"
          + ("" if have_borrow == len(items) else "   <-- THE PARTIAL-FIELD PROBLEM"))
    # ** A MISSING BORROW FIELD IS NOT A ZERO. ** 8 of the first 50 had none. Summing them as 0
    # understates borrowing; dropping the market understates supply. Which of the two is right
    # depends on why it is absent, and that is not knowable from the absence itself.
    missing_supply_of_missing_borrow = sum(
        1 for i in items if st(i, "borrowAssetsUsd") is None and st(i, "supplyAssetsUsd"))
    if have_borrow < len(items):
        print(f"    of the {len(items) - have_borrow} with no borrow field, "
              f"{missing_supply_of_missing_borrow} DO carry a supply figure — so they are not "
              f"simply empty markets, and neither treating them as 0 nor dropping them is safe.")

    def report(label, rows):
        sup = sum(st(i, "supplyAssetsUsd") or 0 for i in rows)
        bor = sum(st(i, "borrowAssetsUsd") or 0 for i in rows)
        u = f"{bor / sup:7.4f}" if sup else "    n/a"
        print(f"    {label:<34} {len(rows):>6} mkts  supply ${sup:>18,.0f}  "
              f"borrow ${bor:>18,.0f}  util {u}")
        return sup, bor

    # ---- (1) THE FILTERS DEFILLAMA ITSELF APPLIES. If the unfiltered total is 39bn and the
    # listed total is a few bn at a believable utilisation, the question is answered.
    print(f"\n  POPULATION vs THE FILTERS DEFILLAMA APPLIES (listed === true, $1,000 floor)")
    all_sup, _ = report("ALL markets (what we summed)", items)
    listed = [i for i in items if i.get("listed") is True]
    unlisted = [i for i in items if i.get("listed") is not True]
    report("listed === true", listed)
    report("NOT listed", unlisted)
    big = [i for i in items if (st(i, "supplyAssetsUsd") or 0) > 1000]
    report("supply > $1,000 (API_MIN_USD)", big)
    report("listed AND supply > $1,000", [i for i in listed if (st(i, "supplyAssetsUsd") or 0) > 1000])

    # ---- (2) THE CONCENTRATION. A handful of markets carrying most of $39bn with borrow==supply
    # to four decimals is a self-dealt position, not a lending market.
    ranked = sorted(items, key=lambda i: st(i, "supplyAssetsUsd") or 0, reverse=True)
    print(f"\n  TOP 12 BY SUPPLY — look for borrow == supply and for assets nobody has heard of")
    for i in ranked[:12]:
        sup, bor = st(i, "supplyAssetsUsd") or 0, st(i, "borrowAssetsUsd")
        pair = (f"{((i.get('collateralAsset') or {}).get('symbol') or '?')}"
                f"/{((i.get('loanAsset') or {}).get('symbol') or '?')}")
        shown = f"${bor:,.0f}" if bor is not None else "ABSENT"
        print(f"    {pair:<24} chain {str((i.get('chain') or {}).get('id')):<7} "
              f"listed={str(i.get('listed')):<5} supply ${sup:>16,.0f}  "
              f"borrow {shown:>17}  "
              f"{(bor / sup) if (bor and sup) else 0:6.4f}  "
              f"{(sup / all_sup * 100) if all_sup else 0:5.2f}% of total")
    for n in (10, 50, 200):
        share = sum(st(i, "supplyAssetsUsd") or 0 for i in ranked[:n])
        print(f"    top {n:<4} carry ${share:>18,.0f}  "
              f"({(share / all_sup * 100) if all_sup else 0:5.1f}% of the total)")

    # ---- (3) CANDIDATE (c): THE SAME MARKET COUNTED ON SEVERAL CHAINS.
    per_chain, by_id = {}, {}
    for i in items:
        cid = (i.get("chain") or {}).get("id")
        per_chain.setdefault(cid, []).append(i)
        by_id.setdefault(i.get("marketId"), set()).add(cid)
    dupes = {k: v for k, v in by_id.items() if len(v) > 1}
    print(f"\n  PER CHAIN")
    for cid, rows in sorted(per_chain.items(), key=lambda kv: -sum(
            st(i, 'supplyAssetsUsd') or 0 for i in kv[1]))[:10]:
        report(f"chain {cid}", rows)
    print(f"    marketIds appearing on more than one chain: {len(dupes)}")
    if not dupes:
        print("    -> CANDIDATE (c) IS DEAD. Nothing is counted twice across chains.")

    # ---- (4) THE CROSS-CHECK THAT DECIDES IT. Same quantity, different route.
    print(f"\n  CROSS-CHECK against DefiLlama morpho-blue TVL (the same quantity, other route)")
    try:
        tv = requests.get("https://api.llama.fi/protocol/morpho-blue", timeout=TIMEOUT).json()
        cur = tv.get("currentChainTvls") or {}
        llama = sum(float(v) for k, v in cur.items()
                    if isinstance(v, (int, float)) and "-" not in k)
        print(f"    DefiLlama morpho-blue TVL  ${llama:,.0f}")
        print(f"    blue-api supplyAssetsUsd   ${all_sup:,.0f}")
        if llama:
            r = all_sup / llama
            print(f"    ratio                       {r:.2f}x")
            # ** NOTE WHICH DIRECTION EACH BIAS RUNS. ** DefiLlama's morpho-blue tvl COUNTS
            # COLLATERAL as well as the loan token, so it is if anything the LARGER of the two
            # honest measures. A blue-api sum several times LARGER than that cannot be explained
            # by a difference of definition in the direction that would excuse it.
            if r > 3:
                print("    -> supplyAssetsUsd IS NOT TOTAL SUPPLIED as we read it. And note the")
                print("       direction: llama's tvl already includes COLLATERAL, so it is the")
                print("       more generous measure. Being several times larger than the")
                print("       generous one leaves no definitional excuse. Compare against the")
                print("       listed-only line above — if THAT is the same order as llama, the")
                print("       answer is the unlisted spam markets and the route needs the filter.")
            elif r > 1.5:
                print("    -> same order, but not the same number. Find the difference before")
                print("       confirming; do not average them.")
            else:
                print("    -> SAME ORDER. Candidate 'supplyAssetsUsd excludes idle liquidity' is")
                print("       then unlikely, and the 98% needs another explanation.")
    except Exception as e:  # noqa: BLE001
        print(f"    UNREACHABLE — {e}")
        print("    Without this the sum is unexplained, so the route stays unconfirmed.")

    print("\n  ANSWERED 2026-09-23 — THIS PROBE SETTLED IT. Re-run it to re-check, not to decide.")
    print("  The answer was the PERMISSIONLESS TAIL: 651 listed markets at $5.87bn and 0.8802,")
    print("  against 7,217 unlisted at $33.61bn and 0.9989, with 78% of the whole figure in four")
    print("  markets whose supply equals their borrow TO THE DOLLAR. config now filters with")
    print("  where:{whitelisted:true} IN THE QUERY and lending_api.status is 'confirmed'.")
    print("  * The DefiLlama TVL cross-check below is INAPPLICABLE, not unmet: their tvl counts")
    print("    loanToken AND collateralToken, so it is a different quantity from supplyAssetsUsd")
    print("    and will not reconcile at any filter level. Do not reinstate it as a gate.")
    print("  * WHAT WOULD REOPEN THIS: the listed subset drifting outside 0.40-0.92, or the")
    print("    listed/unlisted split moving sharply. Both are visible in the POPULATION block.")


def sky():
    head("SKY — is the 2024 flapper still the live one, and where does the bought SKY go?")
    code, where = eth_call(SKY_FLAPPER, "0x" + "0" * 8)
    print(f"flapper under test : {SKY_FLAPPER}")
    for name, sel in SELECTORS.items():
        word, src = eth_call(SKY_FLAPPER, sel)
        if word is None:
            print(f"  {name:<12} UNREACHABLE  {src[:110]}")
            continue
        addr = as_address(word)
        if name in ("want()",):
            try:
                print(f"  {name:<12} {int(word, 16) / 1e18:.4f}  (WAD; the 2024 vote set 0.98)")
            except ValueError:
                print(f"  {name:<12} {word}")
        elif addr and int(word, 16) != 0:
            print(f"  {name:<12} {addr}")
        else:
            print(f"  {name:<12} {word}  (no address — this variant may not expose it)")
    print("\n  PASTE BACK: the receiver() address is the answer to Sky's archetype 4 question —")
    print("  a receiver that destroys is a burn, one that holds is a treasury position.")
    print("  Also confirm pair() == 0x2621CC0B3F3c079c1Db0E80794AA24976F0b9e3c and")
    print("  pip() == 0x61A12E5b1d5E9CC1302a32f0df1B5451DE6AE437 from the 2024 vote.")


def solana():
    head("SOLANA — current inflation, for the archetype 1 issuance decomposition")
    url = "https://api.mainnet-beta.solana.com"
    for method in ("getInflationRate", "getInflationGovernor"):
        try:
            j = rpc(url, method)
            print(f"  {method}: {json.dumps(j.get('result'), indent=2)}")
        except Exception as e:  # noqa: BLE001
            print(f"  {method}: UNREACHABLE — {e}")
    print("\n  NOTE for the record: SIMD-0550 passed late Aug 2026 and DOUBLED annual disinflation")
    print("  15% -> 30%. SIMD-0553's resource fees were REJECTED (53.9%, two-thirds needed), so")
    print("  Solana's BURN is unchanged — getInflationRate answers issuance only.")


def injective():
    head("INJECTIVE — mint module: inflation and annual provisions")
    for host in ("https://sentry.lcd.injective.network", "https://lcd.injective.network"):
        ok = False
        for path in ("/cosmos/mint/v1beta1/inflation", "/cosmos/mint/v1beta1/annual_provisions"):
            try:
                r = requests.get(host + path, timeout=TIMEOUT)
                print(f"  {path:<44} HTTP {r.status_code}  {r.text[:120]}")
                ok = ok or r.ok
            except Exception as e:  # noqa: BLE001
                print(f"  {path:<44} UNREACHABLE — {e}")
        if ok:
            print(f"  (answered by {host})")
            return
    print("  no Injective LCD answered — try another public endpoint")


def near():
    head("NEAR — protocol config (inflation rate)")
    try:
        j = rpc("https://rpc.mainnet.near.org", "EXPERIMENTAL_protocol_config",
                {"finality": "final"})
        res = j.get("result") or {}
        keys = ("max_inflation_rate", "num_blocks_per_year", "protocol_reward_rate",
                "protocol_treasury_account", "epoch_length")
        for k in keys:
            if k in res:
                print(f"  {k:<26} {res[k]}")
        if not any(k in res for k in keys):
            print(f"  unexpected shape — top-level keys: {sorted(res)[:18]}")
    except Exception as e:  # noqa: BLE001
        print(f"  UNREACHABLE — {e}")
    print("\n  Expect ~2.5% (cut from 5% on 2025-10-30), ~32.2m NEAR/yr, 90% validators / 10% treasury.")


def etherfi_sethfi():
    """Does sETHFI COMPOUND against ETHFI, or is it a 1:1 receipt? One block settles it.

    This is the open question behind Ether.fi's locked_tokens / locked_tokens_underlying split.
    It is NOT answerable from documentation with any confidence — a previous note in config
    asserted the compounding shape by ANALOGY with Maple's stSYRUP, which was never checked and
    has since been withdrawn. Two reads in one block answer it outright.

    DECIMALS ARE READ TOO, and that is not gold-plating: comparing a share supply against an
    asset balance is only meaningful if both are scaled the same way. If they differ, the ratio
    is meaningless and the script says so rather than printing a number.
    """
    head("ETHER.FI — is sETHFI a 1:1 receipt, or does it COMPOUND against ETHFI?")
    block, src = eth_block_number()
    if block is None:
        print("  UNREACHABLE — no Ethereum RPC answered eth_blockNumber; nothing else attempted.")
        return
    print(f"  pinned to block {block} ({int(block, 16):,}) via {src}")
    print("  both reads use that block, so the two figures describe ONE state.\n")

    holder = SETHFI[2:].lower().rjust(64, "0")
    reads = {
        "sETHFI.totalSupply()":      (SETHFI, SEL_TOTAL_SUPPLY),
        "ETHFI.balanceOf(sETHFI)":   (ETHFI, SEL_BALANCE_OF + holder),
        "sETHFI.decimals()":         (SETHFI, SEL_DECIMALS),
        "ETHFI.decimals()":          (ETHFI, SEL_DECIMALS),
    }
    got = {}
    for name, (to, data) in reads.items():
        word, where = eth_call(to, data, block)
        if word is None or word in ("0x", None):
            print(f"  {name:<26} UNREACHABLE / empty — {str(where)[:110]}")
            continue
        got[name] = int(word, 16)
        print(f"  {name:<26} {word}")
        print(f"  {'':26} = {got[name]:,} raw")

    shares = got.get("sETHFI.totalSupply()")
    assets = got.get("ETHFI.balanceOf(sETHFI)")
    d_s, d_a = got.get("sETHFI.decimals()"), got.get("ETHFI.decimals()")
    if shares is None or assets is None:
        print("\n  VERDICT: NOT ESTABLISHED — one of the two reads did not return. Do not guess "
              "from the other.")
        return
    print(f"\n  decimals: sETHFI {d_s}   ETHFI {d_a}")
    if d_s is not None and d_a is not None and d_s != d_a:
        print("  VERDICT: NOT COMPARABLE — the two contracts use DIFFERENT decimals, so the ratio "
              "of the raw figures means nothing. Rescale before concluding anything.")
        return
    scale = 10 ** (d_a if d_a is not None else 18)
    print(f"  scaled:   sETHFI shares {shares / scale:,.6f}")
    print(f"            ETHFI assets  {assets / scale:,.6f}")

    if shares == 0:
        print("\n  VERDICT: NOT ESTABLISHED — sETHFI totalSupply is zero, so there is no ratio.")
        return
    ratio = assets / shares
    diff = assets - shares
    print(f"  assets / shares = {ratio:.12f}   (difference {diff:,} raw)")

    # A ratio this close to 1 is a receipt; anything above it is accrual. The tolerance is in RAW
    # UNITS rather than a percentage, because a true 1:1 receipt should agree to the wei and a
    # percentage band would quietly absorb a small real accrual.
    if abs(diff) <= 1:
        print("\n  VERDICT: sETHFI IS A 1:1 RECEIPT — shares and assets agree to within 1 wei.")
        print("  MEANING for the split: locked_tokens and locked_tokens_underlying should track")
        print("  each other almost exactly, and a PERSISTENT GAP between them is itself a finding")
        print("  — stray ETHFI at the contract, a sync problem, or a wrong assumption.")
    elif diff > 1:
        print(f"\n  VERDICT: sETHFI COMPOUNDS — assets exceed shares by {diff:,} raw "
              f"({(ratio - 1) * 100:.4f}%).")
        print(f"  That excess IS the accrued rate: 1 sETHFI currently claims {ratio:.8f} ETHFI.")
        print("  MEANING for the split: a GROWING assets-over-shares ratio is expected and")
        print("  informative. The thing worth watching is the ratio SHRINKING, which would mean")
        print("  rewards stopped or holders are exiting at a discount.")
    else:
        # THE THIRD CASE, WHICH THE QUESTION DID NOT ANTICIPATE. Forcing it into one of the two
        # expected buckets would be the whole error this project keeps correcting.
        print(f"\n  VERDICT: NEITHER — assets are BELOW shares by {abs(diff):,} raw "
              f"({(1 - ratio) * 100:.4f}%).")
        print("  This is not one of the two expected outcomes and must not be filed as either.")
        print("  Shares outstanding exceeding the ETHFI actually held means the contract cannot")
        print("  honour every share at par: a shortfall, a slashing event, a pending withdrawal")
        print("  queue, or a wrong assumption about which contract custodies the stake.")
        print("  DO NOT wire a cross-check on this reading — establish the cause first.")

    print("\n  PASTE BACK both raw values, the decimals and the verdict. This settles the open")
    print("  question on Ether.fi's locked_tokens / locked_tokens_underlying split. NO CROSS-CHECK")
    print("  IS WIRED EITHER WAY until the answer is reviewed — the adapter test fails on purpose")
    print("  if one is added before then.")


def maple_dao_multisig():
    """Is the DAO multisig the "Maple Treasury" Maple's own materials name as the buyback destination?

    THE OPEN P1 QUESTION. The address currently on file as Maple's treasury
    (0xa9466EaBd096449d650D5AEB0dD3dA6F52FD0B19) returned 0.51 SYRUP against the ~75.78m Maple
    reports, and was traced to Maple's v2 protocol FEE treasury — right contract, wrong role. Its
    destination_status is DISPUTED and it stores nothing.

    A BALANCE IN THE TENS OF MILLIONS OF DOLLARS CONFIRMS THE ROLE; a dust balance refutes it just
    as cleanly. That asymmetry is why this is worth one read: both outcomes are informative, and
    neither requires interpretation.

    ** IT CANNOT PRICE ITSELF. ** The threshold is expressed in DOLLARS and the read returns
    TOKENS, so the verdict below is stated in SYRUP against the ~75.78m reference and the dollar
    conversion is left to the reader with a price. Printing a dollar figure from a made-up price
    would be the one way to get this wrong.
    """
    head("MAPLE — is the DAO multisig the treasury that holds repurchased SYRUP?")
    block, src = eth_block_number()
    if block is None:
        print("  UNREACHABLE — no Ethereum RPC answered eth_blockNumber; nothing else attempted.")
        return
    print(f"  pinned to block {block} ({int(block, 16):,}) via {src}")
    print(f"  multisig {MAPLE_DAO_MULTISIG}")
    print(f"  SYRUP    {SYRUP}\n")

    holder = MAPLE_DAO_MULTISIG[2:].lower().rjust(64, "0")
    word, where = eth_call(SYRUP, SEL_BALANCE_OF + holder, block)
    if word is None or word == "0x":
        print(f"  SYRUP.balanceOf(multisig)  UNREACHABLE / empty — {str(where)[:110]}")
        print("\n  VERDICT: NOT ESTABLISHED. The read did not return; do not infer from silence.")
        return
    raw = int(word, 16)
    dword, _ = eth_call(SYRUP, SEL_DECIMALS, block)
    if dword is None or dword == "0x":
        print(f"  SYRUP.balanceOf(multisig)  {raw:,} raw — decimals() did not return, so it "
              f"CANNOT be scaled. No verdict.")
        return
    dec = int(dword, 16)
    bal = raw / (10 ** dec)
    print(f"  SYRUP.decimals()           {dec}")
    print(f"  SYRUP.balanceOf(multisig)  {bal:,.6f} SYRUP")

    ref = 75_780_000
    print(f"\n  reference: Maple's transparency page reports ~{ref:,} SYRUP for the Syrup "
          f"Strategic Fund (77.66M at a later reading).")
    if bal >= ref * 0.5:
        print(f"  VERDICT: CONSISTENT — {bal:,.0f} SYRUP is the same ORDER as the reported fund "
              f"({bal / ref:.2f}x the ~75.78m reference). This supports the multisig being the "
              f"treasury Maple's materials name. Confirm against the transparency page before "
              f"changing the disputed status on the other address.")
    elif bal < 1_000:
        print(f"  VERDICT: REFUTED — {bal:,.6f} SYRUP is dust, the same shape as the 0.51 that "
              f"started this. Not the buyback destination either. The question stays open.")
    else:
        print(f"  VERDICT: INCONCLUSIVE — {bal:,.2f} SYRUP is neither dust nor the reported order "
              f"({bal / ref:.4f}x). Report the figure; do not force it either way.")


# ===== THREE OUTCOMES, NOT TWO — AND A VERDICT YOU CAN CALL IS A VERDICT YOU CAN TEST. =====
#
# ** THE FIRST VERSION WAS A TWO-WAY BRANCH INSIDE A print(), AND IT ASSERTED THE OPPOSITE OF ITS
# OWN NUMBER. ** Anything not above 1.01 fell into an else reading "shares are 0.8524x assets,
# i.e. one share is one locked PENDLE within a percent". 0.8524 is fifteen percent from parity.
# The test that was supposed to cover this checked that the string "VERDICT (direct)" APPEARED —
# presence, not correctness — so the contradiction sailed through.
#
# The missing case is the one that turned out to be true: a share worth MORE than one asset, a
# COMPOUNDING receipt, which is what sETHFI and stSYRUP already do in this book. Below parity the
# share count UNDERSTATES what is locked — the opposite direction from the boost the branch was
# written to catch — and the old text waved it through as confirmation that nothing was wrong.
#
# PULLED OUT OF THE PRINTER so it can be called with numbers. A branch that only ever runs inside
# a script nobody can import is a branch nobody can check.
def share_verdict(shares: float, assets: float) -> tuple[str, str]:
    """(code, sentence) for a share token measured against the assets its contract holds."""
    if assets <= 0:
        return ("not_established",
                "NOT ESTABLISHED — the staking contract holds no PENDLE, which means the lock is "
                "not custodied at this address and this comparison does not apply. Do not read "
                "it as 'shares exceed assets'.")
    ratio = shares / assets
    per_share = assets / shares if shares else float("inf")
    if ratio > 1.01:
        return ("boosted",
                f"BOOSTED OR VIRTUAL BALANCES ARE INCLUDED. The share count is {ratio:.2f}x the "
                f"PENDLE the contract actually holds, and every really-locked PENDLE is in that "
                f"balance — so the excess is not locked tokens. locked_tokens OVERSTATES by this "
                f"factor and the non_comparable flag is correct. THE ASSETS FIGURE IS THE ONE TO "
                f"USE.")
    if ratio < 0.99:
        return ("compounds",
                f"sPENDLE COMPOUNDS. One share redeems for {per_share:.4f} PENDLE, so the share "
                f"count UNDERSTATES what is locked by {(per_share - 1) * 100:.1f}%. Same shape as "
                f"sETHFI and stSYRUP. locked_tokens must read PENDLE.balanceOf(sPENDLE) — the "
                f"ASSETS — and the share count belongs in its own column beside the ratio. This "
                f"is NOT the boosted-balance case: a boost would put this ratio ABOVE one.")
    return ("one_to_one",
            f"A 1:1 RECEIPT — shares are {ratio:.4f}x assets, within a percent of parity, so one "
            f"share really is one locked PENDLE. The boosted balance is a VOTING-WEIGHT construct "
            f"that totalSupply() does not carry, and locked_tokens is measuring what its name "
            f"says. This SETTLES the open P1 where the ceiling test below could not.")


def pendle_spendle_virtual():
    """Does sPENDLE.totalSupply() include the vePENDLE-migration BOOSTED and virtual balances?

    THE OPEN P1. vePENDLE holders converting to sPENDLE received a boosted balance of up to 4x,
    decaying over ~2 years, and Pendle's docs describe a separate "virtual sPENDLE balance" used
    for voting power. If either is inside totalSupply(), locked_tokens OVERSTATES real PENDLE
    locked — by up to 4x, which is the kind of error that still looks plausible on a sheet.

    TWO TESTS, AND THE SECOND ONE IS THE ANSWER. Added 2026-09-22.

    THE CEILING (sPENDLE.totalSupply() vs PENDLE.totalSupply()) was the original and it is weak
    by construction: real locked PENDLE cannot exceed PENDLE's total supply, so a figure ABOVE it
    refutes, and a figure below proves nothing — a 4x boost on a small locked fraction still fits
    under the cap. It is kept because a refutation is worth having whichever test produces it.

    THE DIRECT TEST is sPENDLE.totalSupply() against PENDLE.balanceOf(sPENDLE): shares against
    the ASSETS ACTUALLY HELD by the staking contract. Every real locked PENDLE is in that
    balance. So shares materially above assets means the share count contains something that is
    not locked PENDLE — which is exactly what a boosted or virtual balance is — and shares at or
    below assets means it does not. This settles the question the ceiling could only fail to
    refute, and it is the same shares-versus-assets comparison already running on Ether.fi.

    ** BOTH READS PINNED TO ONE BLOCK. ** Two calls at "latest" can straddle a block boundary,
    and for a ratio of two figures that is the difference between a measurement and a
    coincidence. Same discipline as the Ether.fi read, which was pinned to block 25,982,077.
    """
    head("PENDLE — does sPENDLE.totalSupply() include boosted / virtual balances?")
    block, src = eth_block_number()
    if block is None:
        print("  UNREACHABLE — no Ethereum RPC answered eth_blockNumber; nothing else attempted.")
        return
    print(f"  pinned to block {block} ({int(block, 16):,}) via {src}\n")

    # balanceOf(sPENDLE) — the selector plus the address left-padded to 32 bytes.
    bal_of_spendle = SEL_BALANCE_OF + SPENDLE[2:].lower().rjust(64, "0")

    got = {}
    for name, to, data in (("sPENDLE.totalSupply()", SPENDLE, SEL_TOTAL_SUPPLY),
                           ("PENDLE.balanceOf(sPENDLE)", PENDLE, bal_of_spendle),
                           ("PENDLE.totalSupply()", PENDLE, SEL_TOTAL_SUPPLY),
                           ("sPENDLE.decimals()", SPENDLE, SEL_DECIMALS),
                           ("PENDLE.decimals()", PENDLE, SEL_DECIMALS)):
        word, where = eth_call(to, data, block)
        if word is None or word == "0x":
            print(f"  {name:<24} UNREACHABLE / empty — {str(where)[:100]}")
            continue
        got[name] = int(word, 16)
        print(f"  {name:<24} {got[name]:,} raw")

    sp, pe = got.get("sPENDLE.totalSupply()"), got.get("PENDLE.totalSupply()")
    held = got.get("PENDLE.balanceOf(sPENDLE)")
    ds, dp = got.get("sPENDLE.decimals()"), got.get("PENDLE.decimals()")
    if sp is None or pe is None or ds is None or dp is None:
        print("\n  VERDICT: NOT ESTABLISHED — a read did not return. Do not infer from the others.")
        return
    if ds != dp:
        print(f"\n  VERDICT: NOT COMPARABLE — decimals differ ({ds} vs {dp}). Scaling them the "
              f"same way would be wrong; no ratio is printed.")
        return
    locked, total = sp / (10 ** ds), pe / (10 ** dp)
    print(f"\n  sPENDLE totalSupply  {locked:,.6f}   (shares)")
    if held is not None:
        assets = held / (10 ** dp)
        print(f"  PENDLE held by it    {assets:,.6f}   (assets actually locked)")
    print(f"  PENDLE  totalSupply  {total:,.6f}")
    print(f"  ratio to supply      {locked / total:.6f}")

    # ===== THE DIRECT TEST FIRST, because it can SETTLE the question where the ceiling below
    # can only fail to refute it. Reported before the ceiling so a reader meets the answer
    # before the weaker test that does not give one.
    if held is not None:
        assets = held / (10 ** dp)
        if assets <= 0:
            print("\n  VERDICT (direct): NOT ESTABLISHED — the staking contract holds no PENDLE, "
                  "which means the lock is not custodied at this address and this comparison "
                  "does not apply. Do not read it as 'shares exceed assets'.")
        else:
            code, text = share_verdict(locked, assets)
            print(f"  shares / assets      {locked / assets:.6f}")
            print(f"  assets / share       {assets / locked:.6f}")
            print(f"\n  VERDICT (direct): {text}")
            del code

    if locked > total:
        print(f"\n  VERDICT (ceiling): INCLUDES BOOSTED/VIRTUAL — sPENDLE totalSupply EXCEEDS the entire "
              f"PENDLE supply by {locked / total:.2f}x, which is impossible for real locked "
              f"tokens. locked_tokens is overstated and the non_comparable flag is correct.")
    else:
        print(f"\n  VERDICT (ceiling): NOT REFUTED, AND NOT CONFIRMED — {locked / total:.2%} of PENDLE "
              f"supply. This is a CEILING test and it passed, which does not settle the question: "
              f"a 4x boost on a small locked fraction still fits under the cap. The "
              f"non_comparable flag stays until Pendle's own docs or a virtual-balance read "
              f"settles it.")


# ===== PENDLE — COMPOUNDING OR 1:1 PLUS A QUEUE? SETTLED BY sPENDLE'S OWN EVENT LEDGER. =====
# Added 2026-09-24. Two readings of the same gap (PENDLE held above sPENDLE supply) disagree:
#   COMPOUNDING  stake() mints fewer shares than PENDLE deposited; the gap is accrued yield.
#   1:1 + QUEUE  stake() mints 1:1 (StakedPendle.sol: _transferIn then _mint(amount)); cooldown()
#                BURNS the shares at once and the PENDLE leaves only at finalizeCooldown(), so
#                the gap is PENDLE queued to leave.
# Under 1:1 + QUEUE two identities hold TO THE WEI at one block, from events alone:
#   (1) totalSupply = sum Staked - sum CooldownInitiated + sum CooldownCanceled
#                     - sum instantUnstake gross (Unstaked with fee > 0: amountAfterFee + fee)
#   (2) held - totalSupply = sum CooldownInitiated - sum CooldownCanceled
#                            - sum finalized (Unstaked with fee == 0)
# Under compounding (1) fails: minted shares would be below the Staked amounts. No tolerance.
PENDLE_EVENTS = {
    "Staked": "0x9e71bc8eea02a63969f509818f2dafb9254532904319f9dbda79b67bd34a5f3d",
    "Unstaked": "0x7fc4727e062e336010f2c282598ef5f14facb3de68cf8195c2f23e1454b2b74e",
    "CooldownCanceled": "0x526c2f609254f61e4f8ad7e187f5cd1a08553c70348f25168012b30ae57abf44",
    "CooldownInitiated": "0x810500030f51f04e0a6a7c0323c84654a386b2572d248a7ae15432d4496cc9d1",
}
EIP1967_IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"


def _words(data: str) -> list[int]:
    body = (data or "0x")[2:]
    return [int(body[i:i + 64], 16) for i in range(0, len(body), 64)]


def pendle_compounding_ledger():
    head("PENDLE — does sPENDLE compound, or is the gap an unstake queue? (event ledger, to the wei)")
    block_hex, _ = eth_block_number()
    if block_hex is None:
        print("  UNREACHABLE — no Ethereum RPC answered eth_blockNumber.")
        return
    pin = int(block_hex, 16) - 20
    pin_hex = hex(pin)
    impl = None
    for url in _rpcs_for("ethereum"):
        try:
            j = rpc(url, "eth_getStorageAt", [SPENDLE, EIP1967_IMPL_SLOT, pin_hex])
            if "result" in j:
                impl = as_address(j["result"])
                break
        except Exception:  # noqa: BLE001
            continue
    print(f"  pinned to block {pin:,}; sPENDLE implementation (EIP-1967 slot): {impl or 'not read'}")
    sup_w, _ = eth_call(SPENDLE, SEL_TOTAL_SUPPLY, pin_hex)
    bal_w, _ = eth_call(PENDLE, SEL_BALANCE_OF + SPENDLE[2:].lower().rjust(64, "0"), pin_hex)
    if not sup_w or not bal_w or sup_w == "0x" or bal_w == "0x":
        print("  UNREACHABLE — totalSupply or balanceOf did not return.")
        return
    supply, held = int(sup_w, 16), int(bal_w, 16)
    sums = {"Staked": 0, "CooldownInitiated": 0, "CooldownCanceled": 0,
            "finalized": 0, "instant_gross": 0}
    counts = {}
    for name, topic in PENDLE_EVENTS.items():
        logs, detail = explorer_logs(1, SPENDLE, [topic], 0)
        if logs is None:
            print(f"  {name}: UNAVAILABLE — {detail}. No verdict without every event type.")
            return
        logs = [lg for lg in logs if lg["blockNumber"] <= pin]
        counts[name] = len(logs)
        for lg in logs:
            w = _words(lg["data"])
            if name == "Unstaked":
                after, fee = w[0], w[1]
                if fee == 0:
                    sums["finalized"] += after
                else:
                    sums["instant_gross"] += after + fee
            else:
                sums[name] += w[0]
    print(f"  events to block {pin:,}: {counts}")
    pred_supply = (sums["Staked"] - sums["CooldownInitiated"] + sums["CooldownCanceled"]
                   - sums["instant_gross"])
    pred_queue = sums["CooldownInitiated"] - sums["CooldownCanceled"] - sums["finalized"]
    f = lambda v: f"{v / 1e18:,.6f}"
    print(f"  totalSupply            {f(supply)}")
    print(f"  PENDLE held            {f(held)}   held/supply {held / supply:.6f}")
    print(f"  (1) supply from events {f(pred_supply)}   residual {supply - pred_supply} wei")
    print(f"  (2) queue from events  {f(pred_queue)}   held - supply {f(held - supply)}   "
          f"residual {(held - supply) - pred_queue} wei")
    if supply == pred_supply and held - supply == pred_queue:
        print("  VERDICT: 1:1 + QUEUE, PROVEN — every share was minted 1:1 and the whole gap is "
              "PENDLE in cooldown. Nothing accrues to the share.")
    elif supply == pred_supply:
        print("  VERDICT: shares ARE minted 1:1 (identity 1 exact); the gap is the queue plus "
              f"{f((held - supply) - pred_queue)} PENDLE that arrived without a stake() — "
              "a direct transfer or a path the events above do not cover. Not compounding.")
    else:
        print("  VERDICT: identity (1) FAILS — shares were NOT minted 1:1 against Staked amounts. "
              "Consistent with compounding (or a mint path not in these events). Paste back.")


def uniswap_firepit_threshold():
    """The live threshold() on Uniswap's mainnet Fire Pit — governance STORAGE, not a constant.

    config records that the UNI-burn threshold gating release() is a governance-settable
    parameter: `uint256 public threshold;` with setThreshold() behind onlyThresholdSetter, and the
    Governance Timelock holds thresholdSetter and can appoint a different setter. So the value
    cannot be read from source — only from mainnet storage, at a block.
    """
    head("UNISWAP — live threshold() on the mainnet Fire Pit")
    block, src = eth_block_number()
    if block is None:
        print("  UNREACHABLE — no Ethereum RPC answered eth_blockNumber; nothing else attempted.")
        return
    print(f"  pinned to block {block} ({int(block, 16):,}) via {src}")
    print(f"  fire pit {UNI_FIRE_PIT}\n")

    word, where = eth_call(UNI_FIRE_PIT, SEL_THRESHOLD, block)
    if word is None or word == "0x":
        print(f"  threshold()  UNREACHABLE / empty — {str(where)[:110]}")
        print("\n  VERDICT: NOT ESTABLISHED. An empty return may also mean this contract has no "
              "threshold() at all — check the address before assuming the RPC failed.")
        return
    raw = int(word, 16)
    print(f"  threshold()  {word}")
    print(f"  {'':13}{raw:,} raw")
    # UNI is 18 decimals, but that is an ASSUMPTION about this contract's units rather than a
    # read: threshold() returns a bare uint256 and nothing declares its scale. Both are printed.
    print(f"  {'':13}{raw / 1e18:,.6f} if denominated in UNI at 18 decimals")
    print("\n  VERDICT: READ. Record the raw value and the block in config — it is governance "
          "STORAGE and can change, so a figure without a block is not a fact. The 18-decimal "
          "reading is an ASSUMPTION about units, not something this read establishes.")


def beaconchain():
    """BEACONCHA.IN — the wired route (ETH.Store, Ethereum consensus-layer issuance). 2026-09-24.

    fetch/beaconchain.py stores consensus_rewards_sum_wei from /api/v1/ethstore/latest, auth as
    an `apikey` header (from beaconcha.in's own OpenAPI spec — its docs site is unreachable from
    here, so this is a source reading, never a live confirmation, until this runs). This check IS
    that live confirmation: it makes the exact call the adapter makes, with
    BEACONCHAIN_API_KEY from .env, and prints the actual shape returned.

    ** NO UNAUTHENTICATED BASELINE CALL. ** There used to be one, against /api/v1/epoch/latest,
    run immediately before this. It served no purpose once the key was confirmed working, and on
    the free tier's fair-use limit — 10 requests/minute per IP, beaconcha.in's own OpenAPI spec,
    read 2026-09-24 — it was spending quota the very call this check exists to test then needed.
    That is why Jake's run got a 429 here and not a 401: auth was fine, the budget wasn't. Cut
    2026-09-24 rather than left to burn quota it has no use for.
    """
    head("BEACONCHA.IN — ETH.Store, the wired route for Ethereum's consensus-layer issuance")
    try:
        from dotenv import load_dotenv                    # noqa: PLC0415
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    except ImportError:
        pass
    key = os.environ.get("BEACONCHAIN_API_KEY", "").strip()
    print(f"  BEACONCHAIN_API_KEY: {'set' if key else 'NOT SET'}")

    if not key:
        print("\n  NO KEY SET — cannot exercise the authenticated route. Set BEACONCHAIN_API_KEY "
              "in .env and re-run.")
        return

    # 429 on this endpoint means quota, not a broken key — the free tier's fair-use limit is 10
    # requests/minute per IP (beaconcha.in's OpenAPI spec, read 2026-09-24), tight enough that one
    # earlier call this run, or a neighbour on the same IP, can exhaust it. A SHORT, NARROW retry:
    # only on 429, only here, at most twice — never a blind retry against an API in general.
    print("\n  THE WIRED CALL — GET /api/v1/ethstore/latest, header apikey: ***:")
    r = None
    for attempt in range(3):
        try:
            r = requests.get("https://beaconcha.in/api/v1/ethstore/latest",
                             headers={"apikey": key}, timeout=TIMEOUT)
        except Exception as e:  # noqa: BLE001
            print(f"    UNREACHABLE — {e}")
            return
        if r.status_code != 429 or attempt == 2:
            break
        wait = 5 * (attempt + 1)
        print(f"    HTTP 429 — quota, not a bad key. Retrying in {wait}s "
              f"(attempt {attempt + 1}/2)…")
        time.sleep(wait)
    print(f"    HTTP {r.status_code}")
    try:
        body = r.json()
    except ValueError:
        print(f"    NON-JSON BODY: {r.text[:300]}")
        return
    if not isinstance(body, dict):
        print(f"    top level is {type(body).__name__}, not an object: {str(body)[:300]}")
        return
    print(f"    top-level keys: {sorted(body)}")
    print(f"    status: {body.get('status')!r}")
    rows = body.get("data")
    if not isinstance(rows, list) or not rows:
        print(f"    data: {body.get('data')!r} — no row to inspect")
        return
    rec = rows[0]
    if not isinstance(rec, dict):
        print(f"    data[0] is {type(rec).__name__}, not an object: {str(rec)[:300]}")
        return
    print(f"    data[0] keys: {sorted(rec)}")
    for k in ("day", "day_start", "day_end", "consensus_rewards_sum_wei", "tx_fees_sum_wei",
             "apr", "cl_apr", "el_apr"):
        print(f"      {k:<28} {rec.get(k)!r}")
    wei = rec.get("consensus_rewards_sum_wei")
    try:
        eth = float(wei) / 1e18
        print(f"\n    consensus_rewards_sum_wei / 1e18 = {eth:,.4f} ETH for this beaconchain-day")
    except (TypeError, ValueError):
        print(f"\n    consensus_rewards_sum_wei did not parse as a number: {wei!r}")
    print("\n  PASTE BACK. If the shape above matches fetch/beaconchain.py's expectations "
          "(status 'OK', data[0] carrying day_start/day_end/consensus_rewards_sum_wei), the "
          "adapter will store it on the next run — nothing here writes to the store.")




def near_buyback_inflow_probe():
    """NEAR actual_buyback_tokens — WHAT DID THE NEARBLOCKS CALL RETURN? 2026-09-24.

    The live run stored buyback_fund_balance but nothing for actual_buyback_tokens. This makes
    fetch/nearblocks.py's exact call (same account, action, dates, page size, order), follows the
    cursor the same way, and breaks the answer down instead of summing it: direction (in / out /
    self), sender (one of the two excluded near-intents wallets or not), action kind, deposit.
    A second request without the action filter shows whether inflow arrives as native TRANSFER at
    all, or as FUNCTION_CALLs (wrap.near ft_transfer) the TRANSFER filter never sees.

    NearBlocks' own source (services/account/txn.ts, read 2026-09-24): with no from/to the query
    returns receipts where the account is the SENDER OR the RECEIVER; after_date is exclusive
    (>= start of the NEXT day) and before_date is exclusive (< start of that day). Nothing is
    stored. The key is sent as a Bearer header and never printed.
    """
    head("NEAR — buyback inflow: what NearBlocks' v1 account-txns call actually returns")
    try:
        from dotenv import load_dotenv                    # noqa: PLC0415
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    except ImportError:
        pass
    import config as _config                              # noqa: PLC0415
    import pandas as _pd                                  # noqa: PLC0415

    flow = next(f for f in _config.PROJECT_BY_NAME["Near"]["near_account_flows"]
                if f["metric"] == "actual_buyback_tokens")
    key = os.environ.get(flow["key_env"], "").strip()
    print(f"  {flow['key_env']}: {'set' if key else 'NOT SET'}")
    if not key:
        print("  NO KEY SET — the adapter would have gapped with 'needs an API key'. Set it and re-run.")
        return
    account, action = flow["account"], flow.get("action", "TRANSFER")
    exclude = {s.lower() for s in flow.get("exclude_senders") or []}
    scale = 10 ** int(flow.get("yocto_exponent", 24))
    today = _pd.Timestamp.now("UTC").tz_localize(None).normalize()
    after = (today - _pd.Timedelta(days=35)).strftime("%Y-%m-%d")
    before = (today - _pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    url = flow["base_url"].rstrip("/") + f"/v1/account/{account}/txns"
    hdr = {"Authorization": f"Bearer {key}"}
    print(f"  GET {url}")
    print(f"  the adapter's window: after_date={after} before_date={before} (both exclusive — "
          f"effectively {(_pd.Timestamp(after) + _pd.Timedelta(days=1)).date()}.."
          f"{(_pd.Timestamp(before) - _pd.Timedelta(days=1)).date()})")

    def fetch(params, pages):
        rows, cursor = [], None
        for i in range(pages):
            p = dict(params, **({"cursor": cursor} if cursor else {}))
            try:
                r = requests.get(url, params=p, headers=hdr, timeout=TIMEOUT)
            except Exception as e:  # noqa: BLE001
                print(f"    page {i + 1}: UNREACHABLE — {str(e).replace(key, '***')}")
                return rows, "unreachable"
            print(f"    page {i + 1}: HTTP {r.status_code}", end="")
            if r.status_code != 200:
                print(f" — {r.text[:300]}")
                return rows, f"http {r.status_code}"
            try:
                body = r.json()
            except ValueError:
                print(f" — NON-JSON: {r.text[:200]}")
                return rows, "non-json"
            if not isinstance(body, dict) or "txns" not in body:
                print(f" — no `txns` key; top level {sorted(body) if isinstance(body, dict) else type(body).__name__}")
                return rows, "shape"
            page = body.get("txns") or []
            cursor = body.get("cursor")
            print(f" — {len(page)} row(s), next cursor {cursor!r}")
            rows += page
            if not cursor or not page:
                return rows, "complete"
        return rows, f"stopped at {pages} pages with the cursor still set"

    print(f"\n  A. THE ADAPTER'S CALL — action={action}, per_page=50, order=asc:")
    rows, how = fetch({"action": action, "after_date": after, "before_date": before,
                       "per_page": 50, "order": "asc"}, 20)
    print(f"    -> {len(rows)} row(s), {how}")
    if rows:
        print(f"    first row keys: {sorted(rows[0]) if isinstance(rows[0], dict) else type(rows[0]).__name__}")
    buckets = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        snd = str(r.get("predecessor_account_id") or "").lower()
        rcv = str(r.get("receiver_account_id") or "").lower()
        direction = ("self" if snd == rcv == account else "IN" if rcv == account
                     else "OUT" if snd == account else "neither")
        who = "excluded wallet" if snd in exclude else "other sender"
        dep = sum(float(a.get("deposit") or 0) for a in (r.get("actions") or [])
                  if isinstance(a, dict) and a.get("action") == action) / scale
        k = (direction, who)
        n, s = buckets.get(k, (0, 0.0))
        buckets[k] = (n + 1, s + dep)
    if buckets:
        print("    breakdown (direction, sender) -> rows, NEAR deposited:")
        for (d, w), (n, s) in sorted(buckets.items()):
            print(f"      {d:<8} {w:<16} {n:>6}  {s:>22,.4f}")
    counted_in = buckets.get(("IN", "other sender"), (0, 0.0))
    out_rows = sum(n for (d, _), (n, _s) in buckets.items() if d == "OUT")
    adapter_sum = sum(s for (_d, w), (_n, s) in buckets.items() if w == "other sender")

    print(f"\n  B. SAME WINDOW, NO action FILTER — first page only, action kinds seen:")
    raw, how_b = fetch({"after_date": after, "before_date": before, "per_page": 50,
                        "order": "desc"}, 1)
    kinds = {}
    for r in raw:
        for a in (r.get("actions") or []) if isinstance(r, dict) else []:
            if isinstance(a, dict):
                kk = (a.get("action"), a.get("method"))
                kinds[kk] = kinds.get(kk, 0) + 1
    for (kind, method), n in sorted(kinds.items(), key=lambda x: -x[1])[:12]:
        print(f"      {str(kind):<16} {str(method):<28} {n:>4}")

    print("\n  VERDICT (mechanical):")
    if how in ("unreachable", "non-json", "shape") or how.startswith("http"):
        print(f"    THE CALL FAILED ({how}). The adapter logged a FAILED line and gapped.")
    elif not rows:
        print("    THE CALL RAN AND RETURNED NO TRANSFER ROWS in the window. The adapter logged "
              "'0 TRANSFER txn(s) seen … 0 stored'. See B for what the wallet DOES receive.")
    elif counted_in[0] == 0:
        print("    ROWS CAME BACK BUT NONE IS AN INBOUND TRANSFER FROM OUTSIDE THE THREE WALLETS "
              "— everything was excluded or outbound. The adapter logged '… none left after "
              "exclusion'.")
    else:
        print(f"    {counted_in[0]} inbound TRANSFER(s) from outside the three wallets, "
              f"{counted_in[1]:,.4f} NEAR — the adapter SHOULD have stored rows.")
    if out_rows:
        print(f"    ** {out_rows} OUTBOUND row(s) present. The adapter does not filter on "
              f"receiver, so it would count them as inflow: its sum for this window would be "
              f"{adapter_sum:,.4f} NEAR against {counted_in[1]:,.4f} truly inbound. NOT FIXED — "
              f"held until this output is read.")
    print("\n  PASTE BACK. Nothing in fetch/nearblocks.py changes until this has been read.")


def aerodrome_lock_inputs():
    """WHICH OF THE THREE READS IS WRONG — four calls, ONE block, on Base.

    ** THE ARITHMETIC IS IMPOSSIBLE AND THE DERIVATION REFUSED, WHICH IS THE SYSTEM WORKING. **
    The run gave voting_power 1,026,941,566 against locked 990,636,223 and permanent
    988,513,072. Decaying voting power CANNOT exceed the amount it derives from — bias is
    `amount * remaining / MAXTIME` with remaining <= MAXTIME — so:

        bias      = vp - permanent   = 38,428,494
        decaying  = locked - permanent = 2,123,151
        ratio                          = 18.1x, where the ceiling is 1.0

    Three candidates, and this probe separates them rather than arguing about them:

    (1) locked_tokens IS THE WRONG DENOMINATOR. It reads AERO.balanceOf(escrow) — tokens the
        escrow HOLDS. VotingEscrow keeps its own accounting in `supply` (line 556, incremented
        at 768 and decremented at 907), which is what the bias is actually computed against. If
        supply() != balanceOf, the escrow holds a different amount from what it has locked, and
        `supply` is the figure this derivation wants.

    (2) ** THE TWO PERMANENT FIGURES ARE NOT THE SAME QUANTITY, AND THIS IS THE SUBTLE ONE. **
        BalanceLogicLibrary.supplyAt returns `bias + _point.permanentLockBalance` — the value
        CHECKPOINTED in _pointHistory, not current storage. permanentLockBalance() returns
        CURRENT storage. VotingEscrow refreshes the point from storage only inside _checkpoint
        (line 699). So totalSupply() and permanentLockBalance() can legitimately disagree, and
        subtracting one from the other is not exactly the decaying bias even when every read is
        correct. Reading epoch() alongside them says how stale the point is.

    (3) A UNITS OR TARGET MISMATCH — the least likely, since all three scale by AERO's 18
        decimals and two of them are calls on the escrow itself, but it is what the first two
        being clean would leave.

    ** PINNED TO ONE BLOCK, because the run read these at three different moments. ** A moving
    target cannot produce a 36m excess on its own, but an unpinned comparison cannot prove that.
    """
    head("AERODROME — which of the three lock reads is wrong? (four calls, one block)")
    # ** eth_block_number RETURNS (block, endpoint), NOT A BARE VALUE. ** The first version
    # tested `blk is None` against the tuple, which is never None — so an unreachable chain fell
    # through and printed "block (None, None)" before making four calls that could not work.
    # Caught by running the script rather than by reading it.
    blk, via = eth_block_number(chain="base")
    if blk is None:
        print("  UNREACHABLE — no Base RPC answered eth_blockNumber; nothing else attempted.")
        return
    print(f"  block {int(blk, 16):,} (via {_rpc_host(via)})\n")
    reads = [
        ("AERO.balanceOf(veAERO)", AERO_TOKEN, SEL_BALANCE_OF + VEAERO[2:].lower().rjust(64, "0"),
         "what locked_tokens reads"),
        ("veAERO.supply()", VEAERO, SEL_VE_SUPPLY, "the escrow's OWN accounting of AERO locked"),
        ("veAERO.totalSupply()", VEAERO, SEL_TOTAL_SUPPLY,
         "what ve_voting_power_tokens reads = bias + CHECKPOINTED permanent"),
        ("veAERO.permanentLockBalance()", VEAERO, SEL_VE_PERMANENT,
         "what permanent_locked_tokens reads = CURRENT storage"),
    ]
    vals = {}
    for label, addr, data, why in reads:
        word, src = eth_call(addr, data, block=blk, chain="base")
        if word is None:
            print(f"  {label:<32} UNREACHABLE  {src[:80]}")
            continue
        raw = int(word, 16)
        vals[label] = raw / 1e18
        print(f"  {label:<32} {raw / 1e18:>18,.2f}   {why}")
    word, _ = eth_call(VEAERO, SEL_VE_EPOCH, block=blk, chain="base")
    if word is not None:
        print(f"  {'veAERO.epoch()':<32} {int(word, 16):>18,}   global checkpoints so far")

    bal = vals.get("AERO.balanceOf(veAERO)")
    sup = vals.get("veAERO.supply()")
    ts = vals.get("veAERO.totalSupply()")
    perm = vals.get("veAERO.permanentLockBalance()")
    print("\n  VERDICT")
    if bal is not None and sup is not None:
        d = bal - sup
        print(f"    balanceOf - supply() = {d:,.2f}"
              + ("  -> THE SAME. locked_tokens is NOT the problem; candidate (1) is dead."
                 if abs(d) < max(1.0, abs(sup) * 1e-9) else
                 "  -> THEY DIFFER. The escrow holds a different amount from what it has "
                 "locked, and supply() is the denominator this derivation wants."))
    if ts is not None and sup is not None:
        print(f"    totalSupply() - supply() = {ts - sup:,.2f}"
              + ("  -> voting power EXCEEDS the locked amount, which is impossible for a "
                 "decaying weight. Candidate (2) or (3)." if ts > sup else
                 "  -> within the locked amount, as it must be."))
    if ts is not None and perm is not None and sup is not None:
        bias, decaying = ts - perm, sup - perm
        print(f"    implied bias = {bias:,.2f}   decaying locked = {decaying:,.2f}")
        if decaying > 0:
            r = bias / decaying
            print(f"    bias / decaying = {r:.3f}"
                  + ("  -> AT OR UNDER 1.0. The inputs reconcile against supply(), so "
                     "locked_tokens was the wrong denominator." if r <= 1.0001 else
                     "  -> STILL ABOVE 1.0. Not the denominator. Look at candidate (2): "
                     "totalSupply() carries the CHECKPOINTED permanent balance and "
                     "permanentLockBalance() is current, so they are not the same quantity."))
    print("\n  PASTE BACK all four values, the block and the epoch. Do NOT adjust the 0-1460 "
          "bound or the formula on the strength of this — it is a diagnosis, not a fix.")


# ===== THE RUN PATH, AS DATA. Added 2026-09-23 after a check was built and never called. =====
#
# ** aerodrome_lock_inputs WAS WRITTEN, TESTED AND PUSHED, AND NEVER RAN. ** The edit that was
# supposed to add it to main()'s list did not match, did nothing, and said nothing — and the
# verification was `'aerodrome_lock_inputs' in dir(module)`, which asks whether the function
# EXISTS, not whether anything calls it. A check that is defined and unreferenced produces no
# output, no error and no failing test: it is invisible in exactly the way a missing check is.
#
# THE LIST IS NOW A MODULE-LEVEL REGISTRY, so that:
#   * it can be inspected by a test rather than read by eye, and
#   * test_every_check_is_reachable_from_main compares it against every function in this file
#     that prints a section header, so forgetting to register the NEXT one fails the suite.
# The ordering is the reporting order and is deliberate: Sky first, because it is the one with
# an open question, and beaconchain last, because it is the slowest.
# ===== FLUID — WHICH OF THE THREE CANDIDATES IS THE FUND? Added 2026-09-24. =====
# The buyback proxy swaps into FLUID (LogBuyback) and a rebalancer later calls
# collectFluidTokensToTreasury, which moves FLUID to the HARDCODED TREASURY_ADDRESS. So the first
# hop is settled by the code; the question the scan answers is the SECOND: does TREASURY_ADDRESS
# keep the FLUID (it is the fund), or pass it on (and to which of the other candidates)?
FLUID_TOKEN = "0x6f40d4A6237C257fff2dB00FA0510DeEECd303eb"
FLUID_BUYBACK_PROXY = "0x9Afb8C1798B93a8E04a18553eE65bAFa41a012F1"
FLUID_CANDIDATES = {
    "0x9afb8c1798b93a8e04a18553ee65bafa41a012f1": "FluidBuybackProxy",
    "0x28849d2b63fa8d361e5fc15cb8abb13019884d09": "TREASURY_ADDRESS",
    "0xfb3102759f2d57f547b9c519db49ce1ffde15db2": "FluidReserveContract",
}
# keccak of the event signatures, from contracts/periphery/buyback/events.sol (checked in tests)
LOGBUYBACK_TOPIC = "0x8f05f94eed0b7316abb05990df81da789c0a1f51415a0ff7e8b03f58826018cb"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _pad(addr: str) -> str:
    return "0x" + addr.lower()[2:].rjust(64, "0")


def fluid_buyback_destination():
    """Follow bought FLUID two hops, from Transfer events: proxy -> ?, and that -> ?.

    Also sums LogBuyback.buyAmount where tokenOut == FLUID — the buyback flow itself — so the
    destination totals can be compared against what was bought. Evidence only: nothing here
    writes config, and the candidate is chosen by Jake from the printed table.
    """
    head("FLUID — where does the bought FLUID go? (Transfer events, two hops)")
    buys, d = explorer_logs(1, FLUID_BUYBACK_PROXY, [LOGBUYBACK_TOPIC, None, _pad(FLUID_TOKEN)])
    if buys is None:
        print(f"  UNAVAILABLE — {d}. This check needs an explorer key; the RPC route is capped.")
        return
    bought = sum(int(str(b["data"])[2 + 64:2 + 128] or "0", 16) for b in buys) / 1e18
    print(f"  LogBuyback (tokenOut = FLUID): {len(buys)} event(s), {bought:,.2f} FLUID bought — {d}")

    def hop(src: str, label: str):
        logs, det = explorer_logs(1, FLUID_TOKEN, [TRANSFER_TOPIC, _pad(src), None])
        if logs is None:
            print(f"  {label}: UNAVAILABLE — {det}")
            return {}
        tally = {}
        for lg in logs:
            to = "0x" + lg["topics"][2][-40:].lower()
            tally[to] = tally.get(to, 0) + int(lg["data"], 16)
        total = sum(tally.values()) / 1e18
        print(f"\n  FLUID OUT of {label} ({src}): {total:,.2f} over {len(logs)} transfer(s) — {det}")
        for to, v in sorted(tally.items(), key=lambda kv: -kv[1])[:10]:
            print(f"    {to}  {v / 1e18:>18,.2f}  {v / 1e18 / total:6.1%}  {FLUID_CANDIDATES.get(to, '')}")
        return tally

    first = hop(FLUID_BUYBACK_PROXY, "the buyback proxy")
    treasury = "0x28849d2b63fa8d361e5fc15cb8abb13019884d09"
    if treasury in first:
        hop(treasury, "TREASURY_ADDRESS")
    print("\n  READING IT: if TREASURY_ADDRESS receives the proxy's FLUID and sends little or")
    print("  none onward, it is the fund. If it forwards most of it to the Reserve contract, the")
    print("  Reserve is. PASTE BACK the tables — the choice is recorded in config from them.")


# ===== AETHIR — WHICH CHAIN, WHAT HOLDS ATH, AND WHAT THE EIGENLAYER VAULT COUNTS. 2026-09-24. =====
# Three pools from Aethir's staking page (supplied by Jake). Two public contract registries place
# all three on Ethereum only (Keystone metadata registry ethereum/, 0xtorch datasource chains/1),
# and the Gaming and AI pools are wired on that basis as ATH.balanceOf(pool). This probe confirms
# the chain from bytecode on BOTH chains, and answers what the wiring could not settle offline:
#   - ve pools: token() must be ATH; supply() is the locked total in the ve's own accounting and
#     should equal ATH.balanceOf(pool) (totalSupply() is decaying VOTING POWER — never the lock).
#   - EigenLayer vault: its ATH moves into an EigenLayer strategy (DepositToStrategy), so
#     ATH.balanceOf(vault) can undercount. totalATH / totalDeposited / totalEscrowed and the
#     strategy's own ATH balance are printed side by side so the right measure is CHOSEN from
#     them, not guessed.
#   - eATH is the 1:1 receipt for vault deposits: printed for reconciliation, never summed.
ATH_ETH = "0xbe0Ed4138121EcFC5c0E56B40517da27E6c5226B"
ATH_ARB = "0xc87B37a581ec3257B734886d9d3a581F5A9d056c"
AETHIR_POOLS = {
    "EigenLayer ATH Vault": "0x3cFc70a2999a6C35A6A908D634E9B1fb85B98Ab0",
    "Gaming Pool": "0x6F5c81fe067AE25AFD52218F140a73D51f0C6B31",
    "AI Pool": "0x784BC33B9f8fC8e8dE76Dbd3c7b393D747D60bc4",
    # not one of Aethir's three: the only address DefiLlama counts as Aethir staking. Printed
    # so it is seen whether it holds ATH of its own (a fourth pool) or feeds one of the three.
    "DefiLlama staking owner (wrapper)": "0x3f69Bb14860f7F3348Ac8A5f0D445322143F7feE",
}
EATH = {"ethereum": "0x68ff002b30360d3c613c2d6bc7e8c3e1f94883b9",
        "arbitrum": "0x1903aa5b603819b9debd2f4b202b686e9e393aff"}
# (SELECTORS_AETHIR is declared with the other selector tables, near the top.)


def _code(addr: str, chain: str) -> str:
    for url in _rpcs_for(chain):
        try:
            j = rpc(url, "eth_getCode", [addr, "latest"])
            if "result" in j:
                return "CODE" if j["result"] not in ("0x", "0x0", "") else "no code"
        except Exception:  # noqa: BLE001
            continue
    return "UNREACHABLE"


def _uint(to: str, data: str, chain: str):
    word, _ = eth_call(to, data, chain=chain)
    return None if not word or word == "0x" else int(word, 16)


def _bal(token: str, holder: str, chain: str):
    return _uint(token, SEL_BALANCE_OF + holder.lower()[2:].rjust(64, "0"), chain)


def aethir_staking_probe():
    head("AETHIR — the three staking pools: chain, ATH held, and what the vault counts")
    fmt = lambda v: "n/a" if v is None else f"{v / 1e18:,.2f}"
    for label, addr in AETHIR_POOLS.items():
        print(f"\n  {label}  {addr}")
        for chain, ath in (("ethereum", ATH_ETH), ("arbitrum", ATH_ARB)):
            code = _code(addr, chain)
            line = f"    {chain:<9} {code:<11}"
            if code == "CODE":
                line += f" ATH.balanceOf = {fmt(_bal(ath, addr, chain))}"
            print(line)
        if label in ("Gaming Pool", "AI Pool"):
            tok = _uint(addr, SELECTORS_AETHIR["token()"], "ethereum")
            print(f"    ve token() = {None if tok is None else '0x' + hex(tok)[2:].rjust(40, '0')}"
                  f"   (must be ATH {ATH_ETH.lower()})")
            print(f"    ve supply() = {fmt(_uint(addr, SELECTORS_AETHIR['supply()'], 'ethereum'))}"
                  f"   (should equal ATH.balanceOf above)")
        if label == "EigenLayer ATH Vault":
            for sig in ("totalATH()", "totalDeposited()", "totalEscrowed()"):
                print(f"    {sig:<17} = {fmt(_uint(addr, SELECTORS_AETHIR[sig], 'ethereum'))}")
            strat = _uint(addr, SELECTORS_AETHIR["aethirStrategy()"], "ethereum")
            if strat:
                s_addr = "0x" + hex(strat)[2:].rjust(40, "0")
                print(f"    aethirStrategy() = {s_addr}; ATH.balanceOf(strategy) = "
                      f"{fmt(_bal(ATH_ETH, s_addr, 'ethereum'))}")
    for chain, a in EATH.items():
        print(f"\n  eATH totalSupply on {chain:<9} = {fmt(_uint(a, SEL_TOTAL_SUPPLY, chain))}  "
              f"(receipt, 1:1 — reconcile against the vault, NEVER add)")
    print("\n  PASTE BACK. The vault's locked_tokens leg is chosen from these numbers; the two")
    print("  ve pools are already wired as ATH.balanceOf(pool) on Ethereum.")


# ===== AETHIR — IS THE WRAPPER AT 0x3f69… INDEPENDENT, OR WHAT THE POOLS LOCK? 2026-09-24. =====
# Jake's probe: the Gaming and AI pools report supply() 369.45M and 415.94M but hold 264.95 and
# 0.00 ATH; the wrapper holds 808.69M ATH. On a Curve-style Voting Escrow supply() is TOKENS
# LOCKED (totalSupply() is the voting power), so the pools are holding ~785M of SOMETHING — and
# if that something is the wrapper's receipt, the wrapper's ATH is what backs the pools and
# adding the two would count the same ATH twice. The wrapper's ABI (Keystone registry):
# aethir(), stAethir(), veAethir(), wrap(), wrapFor(), unwrap(); its source is verified but not
# reachable from the build environment. So this reads the addresses and balances that decide it.
AETHIR_WRAPPER = "0x3f69Bb14860f7F3348Ac8A5f0D445322143F7feE"
AETHIR_VE_POOLS = {"Gaming Pool": "0x6F5c81fe067AE25AFD52218F140a73D51f0C6B31",
                   "AI Pool": "0x784BC33B9f8fC8e8dE76Dbd3c7b393D747D60bc4"}


def aethir_wrapper_relationship():
    head("AETHIR — wrapper 0x3f69… vs the Gaming/AI ve pools: independent, or the same ATH?")
    fmt = lambda v: "n/a" if v is None else f"{v / 1e18:,.2f}"
    addr = lambda v: None if v is None else "0x" + hex(v)[2:].rjust(40, "0")
    wr = {k: addr(_uint(AETHIR_WRAPPER, SELECTORS_AETHIR[f"{k}()"], "ethereum"))
          for k in ("aethir", "stAethir", "veAethir")}
    print(f"  wrapper.aethir()   = {wr['aethir']}   (ATH is {ATH_ETH.lower()})")
    print(f"  wrapper.stAethir() = {wr['stAethir']}")
    print(f"  wrapper.veAethir() = {wr['veAethir']}")
    ath_in_wrapper = _bal(ATH_ETH, AETHIR_WRAPPER, "ethereum")
    st_supply = _uint(wr["stAethir"], SEL_TOTAL_SUPPLY, "ethereum") if wr["stAethir"] else None
    print(f"  ATH held by wrapper     {fmt(ath_in_wrapper)}")
    print(f"  stAethir totalSupply    {fmt(st_supply)}")
    locked_total, pool_tokens = 0, {}
    for label, pool in AETHIR_VE_POOLS.items():
        tok = addr(_uint(pool, SELECTORS_AETHIR["token()"], "ethereum"))
        sup = _uint(pool, SELECTORS_AETHIR["supply()"], "ethereum")
        pool_tokens[label] = tok
        held_st = _bal(wr["stAethir"], pool, "ethereum") if wr["stAethir"] else None
        print(f"\n  {label} {pool}\n    token() = {tok}\n    supply() = {fmt(sup)}   "
              f"stAethir held = {fmt(held_st)}   ATH held = {fmt(_bal(ATH_ETH, pool, 'ethereum'))}")
        print(f"    is wrapper.veAethir(): {bool(wr['veAethir']) and pool.lower() == wr['veAethir']}")
        locked_total += sup or 0
    st = (wr["stAethir"] or "").lower()
    locks_receipt = [l for l, t in pool_tokens.items() if t and t == st]
    locks_ath = [l for l, t in pool_tokens.items() if t and t == ATH_ETH.lower()]
    print(f"\n  pools' supply() summed  {fmt(locked_total)}")
    if locks_receipt and len(locks_receipt) == len(AETHIR_VE_POOLS):
        print("  VERDICT: THE SAME ATH. Both pools lock stAethir, the wrapper's receipt, and the "
              "wrapper holds the ATH behind it. Read ATH.balanceOf(wrapper) as the aggregate; "
              "the pools are a breakdown of it and must NOT be added to it.")
    elif locks_ath and len(locks_ath) == len(AETHIR_VE_POOLS):
        print("  VERDICT: INDEPENDENT BY TOKEN — the pools lock ATH directly, yet hold almost none; "
              "their supply() is ATH held elsewhere. Paste back; do not wire either.")
    else:
        print("  VERDICT: NOT SETTLED BY TOKEN ADDRESS — the pools lock "
              f"{sorted(set(t for t in pool_tokens.values() if t))}, neither ATH nor the wrapper's "
              "stAethir for both. Paste back.")
    print("  The EigenLayer vault (strategy 777.29M ATH) is a separate question: this check does "
          "not touch it.")


def _decode_string(word_hex: str) -> str | None:
    """A dynamic `string` return: 32-byte offset, then 32-byte length, then the UTF-8 bytes."""
    body = (word_hex or "")[2:]
    if len(body) < 128:
        return None
    try:
        length = int(body[64:128], 16)
        data = bytes.fromhex(body[128:128 + length * 2])
        return data.decode("utf-8", errors="replace")
    except (ValueError, IndexError):
        return None


# ===== AETHIR — WHAT IS 0x1b49f587…, THE TOKEN BOTH VE POOLS ACTUALLY LOCK? 2026-09-24. =====
# Jake's read: pool.token() on BOTH the Gaming and AI pools returns 0x1b49f587…, confirmed
# NEITHER ATH nor the wrapper's stAethir (0xc96aa65f…). The Keystone registry's verified ABI for
# it names the contract "VeAethir" and shows a PLAIN mintable/burnable OpenZeppelin ERC-20 plus
# Ownable — balanceOf/transfer/approve, mint(address,uint256), burn(address,uint256), owner() —
# and NO asset(), underlying(), totalAssets(), convertToAssets() or any other redeemable-
# underlying accessor. So from the ABI alone this is NOT an ERC-4626 vault: there is no on-chain
# claim it redeems for. The four accessors are called anyway, LIVE, because a downloaded ABI is
# not proof of absence — only an on-chain revert/empty answer is.
#
# WHAT REMAINS OPEN IS WHO MINTS IT AND WHY. mint()/burn() are owner-gated, so VeAethir's supply
# is exactly as trustworthy as its owner()'s behaviour — not self-evidently backed by anything,
# unlike ATH.balanceOf() or a vault's totalAssets(). This reads owner(), checks whether it is the
# wrapper (0x3f69…) or one of the pools, and checks the wrapper's OWN veAethir() getter against
# this address — the direct on-chain link (or refutation) the wrapper's ABI offers.
VE_AETHIR = "0x1B49F587feca530a7Bf7Cf2bD3fBda780e1B7490"
SEL_NAME = "0x06fdde03"      # keccak("name()")[:4]
SEL_SYMBOL = "0x95d89b41"    # keccak("symbol()")[:4]


def aethir_veaethir_probe():
    head("AETHIR — 0x1b49f587… ('VeAethir'), what both ve pools actually lock")
    fmt = lambda v: "n/a" if v is None else f"{v / 1e18:,.2f}"
    S = SELECTORS_AETHIR

    name = _decode_string(eth_call(VE_AETHIR, SEL_NAME, chain="ethereum")[0] or "")
    symbol = _decode_string(eth_call(VE_AETHIR, SEL_SYMBOL, chain="ethereum")[0] or "")
    decimals = _uint(VE_AETHIR, SEL_DECIMALS, "ethereum")
    supply = _uint(VE_AETHIR, SEL_TOTAL_SUPPLY, "ethereum")
    owner_word, _ = eth_call(VE_AETHIR, S["owner()"], chain="ethereum")
    owner = as_address(owner_word)
    print(f"  name()     {name!r}")
    print(f"  symbol()   {symbol!r}")
    print(f"  decimals() {decimals}")
    print(f"  totalSupply() {fmt(supply)}")
    print(f"  owner()    {owner}")

    print("\n  ERC-4626-style accessors, called LIVE (not inferred from the ABI):")
    no_accessor = True
    for sig in ("asset()", "underlying()", "totalAssets()"):
        word, where = eth_call(VE_AETHIR, S[sig], chain="ethereum")
        if word and word != "0x":
            print(f"    {sig:<16} RETURNED {word} — a redeemable-underlying accessor EXISTS. "
                  f"The 'plain accounting token' reading below is WRONG.")
            no_accessor = False
        else:
            print(f"    {sig:<16} no data / reverted — {str(where)[:80]}")
    if no_accessor:
        print("  CONFIRMED LIVE: no redeemable-underlying accessor answers. VeAethir is not a "
              "vault over any single on-chain balance; treat it as an accounting token.")

    print(f"\n  owner() code check: {_code(owner, 'ethereum') if owner else 'n/a'}")
    is_wrapper = bool(owner) and owner.lower() == AETHIR_WRAPPER.lower()
    print(f"  owner() == the 0x3f69… wrapper: {is_wrapper}")
    for label, pool in AETHIR_VE_POOLS.items():
        is_pool = bool(owner) and owner.lower() == pool.lower()
        print(f"  owner() == {label}: {is_pool}")

    # THE WRAPPER'S OWN GETTER, the direct link (or refutation) its ABI offers.
    wrapper_ve_word, _ = eth_call(AETHIR_WRAPPER, S["veAethir()"], chain="ethereum")
    wrapper_ve = as_address(wrapper_ve_word)
    print(f"\n  wrapper.veAethir() = {wrapper_ve}")
    matches_wrapper_getter = bool(wrapper_ve) and wrapper_ve.lower() == VE_AETHIR.lower()
    print(f"  wrapper.veAethir() == 0x1b49f587…: {matches_wrapper_getter}")

    if supply is not None:
        near = abs(supply / 1e18 - 785_390_000) < 1_000_000
        print(f"\n  VeAethir.totalSupply() = {fmt(supply)} vs the two pools' summed supply() "
              f"(785.39M, Jake's 2026-09-24 read) — "
              f"{'MATCHES within rounding' if near else 'DOES NOT MATCH'}.")

    print("\n  READING IT:")
    if matches_wrapper_getter and is_wrapper:
        print("  The wrapper NAMES this contract as its veAethir() and IS its owner() — the "
              "wrapper is the sole minter. Whether minting is 1:1 against ATH wrapped is NOT "
              "established by this probe (that needs the wrapper's mint call sites / a Mint "
              "event trace on VeAethir correlated with the wrapper's Wrap events) — paste back "
              "and that trace is the next step, not a figure to wire.")
    elif matches_wrapper_getter and not is_wrapper:
        print("  The wrapper NAMES this contract as its veAethir() but is NOT its owner() — the "
              "getter is stale or descriptive only; minting authority sits elsewhere. Paste back.")
    elif is_wrapper:
        print("  The wrapper IS the owner (sole minter) even though its own veAethir() getter "
              "points elsewhere — an inconsistency worth flagging to Aethir, not resolving here.")
    else:
        print("  Owner is NEITHER the wrapper NOR either ve pool. VeAethir is minted at the "
              "discretion of a THIRD party this probe has not identified. Its supply is an "
              "accounting record with no on-chain proof of what backs it. DO NOT WIRE "
              "locked_tokens from pool.supply() until owner()'s identity and mint history are "
              "understood — a plausible-sounding guess here is exactly the error class this "
              "project exists to catch.")


# ===== GEODNET — IS THERE A STAKING CONTRACT AT ALL? 2026-09-24. =====
# GEODNET's own GIPs describe TWO mechanisms: GEOD "staked in a SuperHex" (a per-hex bounty whose
# success test is a station hitting 90% RRR — GIP5) and "locked GEOD" that sets a veNFT's voting
# power (the governance-platform GIP). No address for either is published anywhere this project
# can read. This scan looks for them by BEHAVIOUR: GEOD Transfer destinations on Polygon over a
# recent window, ranked by volume, marked contract vs EOA by bytecode, with the count of
# transfers that are whole multiples of 1,000 GEOD (SuperHex increments). Candidates only —
# nothing is wired from this without GEODNET's own material naming the address.
GEOD_POLYGON = "0xAC0F66379A6d7801D7726d5a943356A172549Adb"


def geodnet_staking_candidates(days: int = 30):
    head(f"GEODNET — GEOD destinations on Polygon, last {days} days: contracts by inflow")
    head_hex = None
    for url in _rpcs_for("polygon"):
        try:
            j = rpc(url, "eth_blockNumber")
            head_hex = j.get("result")
            if head_hex:
                break
        except Exception:  # noqa: BLE001
            continue
    if not head_hex:
        print("  UNREACHABLE — no Polygon RPC answered eth_blockNumber.")
        return
    from_block = int(head_hex, 16) - days * 43_200          # ~2s Polygon blocks
    logs, detail = explorer_logs(137, GEOD_POLYGON, [TRANSFER_TOPIC], from_block)
    if logs is None:
        print(f"  UNAVAILABLE — {detail}")
        return
    print(f"  {detail}")
    agg = {}
    for lg in logs:
        if len(lg["topics"]) < 3:
            continue
        to = "0x" + lg["topics"][2][-40:].lower()
        v = int(lg["data"], 16)
        a = agg.setdefault(to, [0, 0, 0, set()])
        a[0] += v
        a[1] += 1
        a[2] += 1 if v and v % (1_000 * 10 ** 18) == 0 else 0
        a[3].add("0x" + lg["topics"][1][-40:].lower())
    ranked = sorted(agg.items(), key=lambda kv: -kv[1][0])[:25]
    print(f"\n  {'destination':<44} {'GEOD in':>16} {'txs':>6} {'x1000':>6} {'senders':>8}  code")
    for addr, (v, n, whole, senders) in ranked:
        print(f"  {addr:<44} {v / 1e18:>16,.0f} {n:>6} {whole:>6} {len(senders):>8}  "
              f"{_code(addr, 'polygon')}")
    print("\n  READING IT: a staking or lock contract is a CONTRACT with many distinct senders and")
    print("  whole-number deposits. If no such row appears, SuperHex stakes are not held in one")
    print("  contract (per-hex, or custodial in GEODNET's console) — say so and stop hunting.")


# ===== MAPLE — robots.txt FIRST, then the transparency page. 2026-09-24. =====
# The only record of Maple's robots.txt is run 20260921T100546Z's "robots.txt disallows
# https://maple.finance/transparency", and that line could not tell a Disallow rule from a
# robots.txt that answered 401/403. This prints the file itself and the verdict by the same rule
# the run applies (fetch.scrape.robots_from_response). The PAGE is fetched only if that verdict
# allows it — a disallowed page is closed, however cleanly it would parse.
def maple_transparency():
    head("MAPLE — robots.txt, then maple.finance/transparency (holdings, liquid assets, buybacks)")
    from fetch.base import USER_AGENT
    from fetch.maple_transparency import URL, parse
    from fetch.scrape import robots_from_response

    robots_url = "https://maple.finance/robots.txt"
    try:
        r = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    except Exception as e:  # noqa: BLE001
        print(f"  robots.txt UNREACHABLE — {type(e).__name__}. The page is NOT fetched: the "
              f"question is what robots.txt says, and it has not answered.")
        return
    print(f"  robots.txt: HTTP {r.status_code}, {len(r.text)} bytes. The file:")
    for line in r.text.splitlines()[:60]:
        print(f"    | {line}")
    rp, how = robots_from_response(robots_url, r.status_code, r.text)
    allowed = rp.can_fetch(USER_AGENT, URL)
    print(f"\n  VERDICT for {URL} as {USER_AGENT!r}: {'ALLOWED' if allowed else 'DISALLOWED'} — {how}")
    if not allowed:
        print("  CLOSED. The page is not fetched. (If the file above shows no rule covering "
              "/transparency, the refusal is the status code, not a rule — say so and decide.)")
        return

    try:
        page = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        page.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"  page UNREACHABLE — {e}")
        return
    got = parse(page.text)
    fmt = lambda v: "NOT FOUND" if v is None else f"{v:,.0f}"
    print(f"\n  SYRUP Holdings   {fmt(got['holdings_syrup'])} SYRUP"
          + (f"  (page rounds to ±{got['holdings_rounding']:,.0f})" if got["holdings_rounding"] else ""))
    print(f"  Liquid Assets    ${fmt(got['liquid_assets_usd'])}")
    bb = got["buybacks"]
    print(f"  Token Buybacks   {len(bb)} row(s) in the server-rendered HTML"
          + (f"; the page says 'Showing {got['showing'][0]}-{got['showing'][1]} of "
             f"{got['showing'][2]}'" if got["showing"] else ""))
    for row in bb.itertuples(index=False):
        print(f"    {row.month:%Y-%m}  ${row.usd:>14,.2f}  {row.syrup:>16,.2f} SYRUP  @ ${row.avg_price}")
    for why in got["refused"]:
        print(f"  REFUSED: {why}")
    if got["showing"] and len(bb) < got["showing"][2]:
        import re as _re
        hints = sorted(set(_re.findall(r'[?&](page|offset|cursor|p)=[^"&\s]*', page.text)))[:10]
        print(f"  PAGINATION: {got['showing'][2] - len(bb)} row(s) are not in the HTML. Query-"
              f"parameter hints in the markup: {hints or 'none'} — none means paging is "
              f"client-side and those rows are unreachable without a browser.")
    print("\n  PASTE BACK. Promotion to primary waits on this output.")


def sky_burn_breakdown():
    """SKY other_burn_balance = 10,565,078,749 — WHO BURNED IT, AND IS THE SCAN SOUND? 2026-09-24.

    There is no stored per-event table: the run decomposes the scan in memory and keeps only the
    per-bucket totals plus the top six unrecognised senders in the Review Queue basis. So this
    re-runs the SAME scan fetch/chain.py runs (SKY token, Transfer(*, address(0)), from the
    from_block on file, explorer first, then chunked eth_getLogs) pinned to one head block, and
    prints the raw GROUP BY from_address alongside the checks that would expose a scan bug:
      1. DUPLICATES — events sharing (transactionHash, logIndex). Any at all means a chunk or page
         was counted twice; a clean scan has zero.
      2. TOPIC MISREAD — events whose topic0 is not Transfer, whose topic2 is not address(0), or
         whose data is not exactly one word. fetch/chain.py does not re-check these after the
         provider filters, so this is where a provider ignoring a topic would show.
      3. THE SUPPLY IDENTITY — sum(mints) - sum(burns) == SKY.totalSupply() at the same block.
         It holds only if the burn events are complete AND real; a double count or a non-burn
         counted as a burn breaks it by exactly the overcount.
    Nothing is stored. Keys are never printed.
    """
    head("SKY — burn events behind other_burn_balance: by sender, duplicates, topics, supply")
    import config as _config                              # noqa: PLC0415
    try:
        from fetch.explorer import normalise              # noqa: PLC0415
    except ImportError as e:
        print(f"  UNREACHABLE  fetch.explorer not importable: {e}")
        return
    sky = next(p for p in _config.PROJECTS if p["name"] == "Sky")
    spec = sky["contracts"]["burn_logs"]
    cfg = spec["burn_logs"]
    token, from_block = spec["address"], int(cfg["from_block"])
    proxy = cfg["stage2_burner"]["address"].lower()
    zero = "0x" + "0" * 64
    blk_hex, _ = eth_block_number()
    if blk_hex is None:
        print("  UNREACHABLE  no Ethereum RPC answered eth_blockNumber — cannot pin the reads.")
        return
    head_blk = int(blk_hex, 16)
    print(f"token {token}  from_block {from_block:,}  pinned to block {head_blk:,}")

    def scan(topics, label):
        logs, detail = explorer_logs(1, token, topics, from_block, head_blk)
        if logs is None:
            print(f"  {label}: explorer unavailable ({detail}) — falling back to eth_getLogs")
            logs, detail = eth_get_logs(token, topics, from_block, head_blk)
        print(f"  {label}: {detail}")
        return None if logs is None else [normalise(e) for e in logs]

    burns = scan([TRANSFER_TOPIC, None, zero], "burns Transfer(*, 0x0)")
    if burns is None:
        print("  UNREACHABLE  the burn scan did not complete; nothing below would be a figure.")
        return

    # ===== 1. DUPLICATES =====
    keys = {}
    for e in burns:
        keys.setdefault((e["transactionHash"], e["logIndex"]), []).append(e)
    dups = {k: v for k, v in keys.items() if len(v) > 1}
    dup_wei = sum(int(x["data"], 16) for v in dups.values() for x in v[1:])
    print(f"\n1. DUPLICATES: {len(burns):,} events, {len(keys):,} distinct (tx, logIndex); "
          f"{len(dups):,} duplicated key(s) carrying {dup_wei / 1e18:,.2f} SKY of double count")
    blank = sum(1 for e in burns if not e["transactionHash"])
    if blank:
        print(f"   ** {blank:,} event(s) have NO transactionHash — the dedup key is unreliable.")

    # ===== 2. TOPIC MISREAD =====
    bad0 = [e for e in burns if not e["topics"] or e["topics"][0].lower() != TRANSFER_TOPIC]
    bad2 = [e for e in burns if len(e["topics"]) < 3 or e["topics"][2].lower() != zero]
    badn = [e for e in burns if len(e["topics"]) != 3]
    badd = [e for e in burns if len(str(e["data"])) != 66]
    print(f"\n2. TOPICS: topic0 != Transfer: {len(bad0):,}   topic2 != address(0): {len(bad2):,}"
          f"   topic count != 3: {len(badn):,}   data not one word: {len(badd):,}")
    for e in (bad0 + bad2 + badd)[:5]:
        print(f"   e.g. block {e['blockNumber']:,} tx {e['transactionHash']} topics {e['topics']}")

    # ===== THE RAW GROUP BY — clean events only, one per key =====
    clean = [v[0] for v in keys.values()
             if v[0] not in bad0 and v[0] not in bad2 and v[0] not in badd]
    by, span = {}, {}
    for e in clean:
        f = "0x" + e["topics"][1][-40:].lower()
        n, w, txs = by.get(f, (0, 0, set()))
        txs.add(e["transactionHash"])
        by[f] = (n + 1, w + int(e["data"], 16), txs)
        lo, hi = span.get(f, (e["blockNumber"], e["blockNumber"]))
        span[f] = (min(lo, e["blockNumber"]), max(hi, e["blockNumber"]))
    total = sum(w for _, w, _ in by.values())
    rows = sorted(by.items(), key=lambda kv: -kv[1][1])
    print(f"\nFROM-ADDRESS BREAKDOWN  (SELECT from_address, COUNT(*), SUM(value) ... GROUP BY "
          f"from_address ORDER BY SUM(value) DESC) — {len(rows):,} distinct sender(s)")
    print(f"  {'#':>4} {'from_address':<42} {'events':>8} {'txs':>8} {'SUM(value) SKY':>22} "
          f"{'share':>7} {'first_block':>11} {'last_block':>11}  code")
    for i, (f, (n, w, txs)) in enumerate(rows[:40], 1):
        code = ""
        if i <= 15:
            c, _ = _code_at(f)
            code = "?" if c is None else ("contract" if len(c) > 2 else "EOA")
        tag = "  <- Pause Proxy (stage2 -> burn_address_balance)" if f == proxy else ""
        print(f"  {i:>4} {f:<42} {n:>8,} {len(txs):>8,} {w / 1e18:>22,.2f} "
              f"{(w / total if total else 0):>7.2%} {span[f][0]:>11,} {span[f][1]:>11,}  "
              f"{code}{tag}")
    if len(rows) > 40:
        rest = rows[40:]
        print(f"  {'rest':>4} {f'{len(rest):,} more sender(s)':<42} "
              f"{sum(n for _, (n, _, _) in rest):>8,} {'':>8} "
              f"{sum(w for _, (_, w, _) in rest) / 1e18:>22,.2f}")
    pp = by.get(proxy, (0, 0, set()))[1]
    print(f"  TOTAL {total / 1e18:,.2f}   Pause Proxy {pp / 1e18:,.2f}   "
          f"everything else (= other_burn_balance) {(total - pp) / 1e18:,.2f}  "
          f"vs stored 10,565,078,749")

    # THE LEGACY CONVERTER AND THE OLD ENGINE STOPPED BURNING IN 2025. If nothing but the
    # Pause Proxy burned after the 2025-06-26 spell's cast (block 22,817,692, the MKR_SKY
    # burnExtraSky event), every non-Pause-Proxy bucket is a CLOSED historical total.
    late = [(f, n) for f, (n, _, _) in rows if f != proxy and span[f][1] > 22_817_692]
    print(f"\n  senders other than the Pause Proxy burning AFTER block 22,817,692: "
          f"{len(late)}" + (" — " + ", ".join(f"{f} ({n})" for f, n in late[:10]) if late else ""))

    print("\n  LARGEST SINGLE EVENTS")
    for e in sorted(clean, key=lambda e: -int(e["data"], 16))[:10]:
        print(f"    block {e['blockNumber']:>11,}  {int(e['data'], 16) / 1e18:>20,.2f} SKY  "
              f"from 0x{e['topics'][1][-40:]}  tx {e['transactionHash']}")

    # ===== 3. THE SUPPLY IDENTITY =====
    mints = scan([TRANSFER_TOPIC, zero, None], "mints Transfer(0x0, *)")
    word, _ = eth_call(token, SEL_TOTAL_SUPPLY, hex(head_blk))
    if mints is None or word is None:
        print("\n3. SUPPLY IDENTITY: not computed — the mint scan or totalSupply() did not answer.")
    else:
        minted = sum(int(e["data"], 16) for e in
                     {(e["transactionHash"], e["logIndex"]): e for e in mints}.values())
        supply = int(word, 16)
        gap = (minted - total) - supply
        print(f"\n3. SUPPLY IDENTITY at block {head_blk:,}:  minted {minted / 1e18:,.2f}  - burned "
              f"{total / 1e18:,.2f}  = {(minted - total) / 1e18:,.2f}   totalSupply() "
              f"{supply / 1e18:,.2f}   difference {gap / 1e18:,.2f}")
        print("   ZERO: the burn events are complete and every one is a real burn — the figure is "
              "a CLASSIFICATION question, not a scan bug. NEGATIVE: burns overcounted by that much. "
              "POSITIVE: burns missed.")
    print("\n  PASTE BACK all of the above. Nothing is stored or reclassified until it is read.")


def _code_at(addr: str):
    """(code_hex, endpoint) — '0x' is an EOA. Reported beside a sender, never used to classify."""
    for url in _rpcs_for("ethereum"):
        try:
            j = rpc(url, "eth_getCode", [addr, "latest"])
            if "result" in j:
                return j["result"], url
        except Exception:  # noqa: BLE001
            continue
    return None, None


CHECKS = (
    sky_chainlog, sky, morpho_blue_api,
    sky_splitter, sky_splitter_params, sky_splitter_history,
    solana, injective, near, etherfi_sethfi,
    maple_dao_multisig, pendle_spendle_virtual, pendle_compounding_ledger, aerodrome_lock_inputs,
    uniswap_firepit_threshold, beaconchain, near_buyback_inflow_probe,
    fluid_buyback_destination, aethir_staking_probe, aethir_wrapper_relationship,
    aethir_veaethir_probe, geodnet_staking_candidates,
    maple_transparency, sky_burn_breakdown,
)

# The three that need a value off the command line. Kept beside the registry rather than folded
# into it, so CHECKS stays a plain list of the functions that run — which is what the
# completeness test compares against.
def _bind(fn, args):
    if fn is sky_splitter:
        return lambda: sky_splitter(args.splitter)
    if fn is sky_splitter_params:
        return lambda: sky_splitter_params(args.splitter)
    if fn is sky_splitter_history:
        return lambda: sky_splitter_history(args.splitter, args.splitter_from_block)
    return fn


def _resolve_check(name: str):
    """(fn, None) on a match, (None, message) on a refusal — never a guess.

    EXACT NAME WINS OUTRIGHT, before prefix matching is even considered. Several checks would
    otherwise be ambiguous against their own siblings' prefix — 'sky' is itself a registered
    check (sky_chainlog, sky_splitter... all start with it too), and 'sky_splitter' is itself
    one (sky_splitter_params, sky_splitter_history start with it) — so typing a check's own full
    name must always run exactly that check, never the group it happens to prefix.

    UNAMBIGUOUS PREFIX IS THE FALLBACK, for the common case of typing enough to be unique
    ('beaconchain', 'maple_transparency') without the whole name. A prefix matching MORE than
    one check REFUSES rather than picking one or running the lot — same rule run_sql.py already
    applies to an unmatched section label, applied here to a name instead of a letter: this
    tool guesses nothing about field names, chain state or which contract holds what, and it
    is not about to start guessing which check the caller meant.
    """
    lname = name.strip().lower()
    exact = next((fn for fn in CHECKS if fn.__name__ == lname), None)
    if exact:
        return exact, None
    matches = [fn for fn in CHECKS if fn.__name__.startswith(lname)]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        names = ", ".join(fn.__name__ for fn in matches)
        return None, (f"{name!r} matches {len(matches)} checks and could mean any of them: "
                      f"{names}. Type the full name of the one you want.")
    return None, f"no check named or starting with {name!r}. Run --list to see the names."


def main():
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("check", nargs="?", default=None,
                    help="run only this check — its exact name, or an unambiguous prefix "
                         "(e.g. 'beaconchain'). Omit to run everything.")
    ap.add_argument("--list", action="store_true",
                    help="print the check names and exit; nothing is run")
    ap.add_argument("--splitter", default=SKY_SPLITTER,
                    help="Sky's Splitter address, to confirm which flapper is live and to read "
                         "its burn/hop parameters and their File history")
    ap.add_argument("--splitter-from-block", type=int, default=SKY_SPLITTER_FROM_BLOCK,
                    help="first block to scan for the Splitter's File events. Set it at or "
                         "BEFORE deployment — a history that starts too late is not a shorter "
                         "history, it is a wrong one")
    args = ap.parse_args()

    if args.list:
        for fn in CHECKS:
            print(fn.__name__)
        return 0

    selected = CHECKS
    if args.check:
        fn, err = _resolve_check(args.check)
        if err:
            print(f"\n  {err}\n")
            return 1
        selected = (fn,)

    if selected is CHECKS:
        print("check_offline_items.py — running every check the build sandbox cannot reach.")
    else:
        print(f"check_offline_items.py — running only {selected[0].__name__}.")
    print("Paste the whole output back.")
    ran = []
    for fn in selected:
        ran.append(fn.__name__)
        try:
            _bind(fn, args)()
        except Exception as e:  # noqa: BLE001 — one failure must not stop the rest
            print(f"\n  {fn.__name__} FAILED: {e}")
    # ** THE ROLL CALL IS THE POINT. ** A check that silently never ran is invisible; naming
    # every one that did, and the count, makes an absent section obvious in the pasted output
    # instead of something a reader has to notice is missing. Still true for a filtered run of
    # one — it says "1 check ran: beaconchain" rather than leaving the reader to infer that
    # nothing else was supposed to.
    print(f"\nDone. {len(ran)} check{'s' if len(ran) != 1 else ''} ran: {', '.join(ran)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
