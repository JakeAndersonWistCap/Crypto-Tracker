#!/usr/bin/env python3
"""
check_offline_items.py — every check this sandbox cannot reach, in one command.

    python check_offline_items.py              run every check
    python check_offline_items.py <name>       run just that one — exact name, or an
                                                unambiguous prefix (e.g. "maple_transparency")
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
                # "0x" IS NO VALUE (no code there, a revert, or a missing function) — never an int of 0, never a
                # crash (Jake's probes 2026-10-06 18:21). Logged with the call and address.
                if str(j["result"]).strip() in ("", "0x", "0X"):
                    print(f"    eth_call {selector[:10]} on {to} at {block} returned 0x (no value)")
                    return None, f"{to}: {selector[:10]} returned 0x (no code, a revert, or no such function)"
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


def eth_get_logs(address: str, topics: list, from_block: int, to_block: int, chunk: int = 50_000,
                 chain: str = "ethereum", min_chunk: int = 1_000):
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
        for url in _rpcs_for(chain):
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
            if chunk > min_chunk and any(w in joined for w in
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
            rows.append((lg["blockNumber"], name, _hexint(lg.get("data")),
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
        raw = _hexint(lg.get("data"))
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


GEODNET_SOL_BURN_ACCOUNT = "5SBfxBdqsCM1SJZGQkf9Y74EFmUfzs8LGDjBZUjZGnED"
GEODNET_SOL_MINT = "7JA5eZdCzztSfQbJvS8aVVxMFfd81Rs9VvwnocV1mKHu"
SOL_INCINERATOR = "1nc1nerator11111111111111111111111111111111"
SOL_SYSTEM_PROGRAM = "11111111111111111111111111111111"


def geod_solana_burn_account():
    """Is 5SBf...GnED a GEOD BURN SINK, before anything sums it into burn_address_balance?

    Added 2026-09-28. Config lists it (from Dune 8683175) as GEODNET's Solana burn destination,
    and GIP-7 proposes Solana as GEODNET's primary chain — if burns moved there, the Polygon
    zeros would look like quiet days. Three questions, all from the public RPC, no key:
      1. is it a token account for GEOD's Solana mint 7JA5...mKHu?
      2. who controls it? A sink is owned by the incinerator (or an authority nobody can sign
         for); an ordinary wallet owner (System Program) can spend it, which is a treasury or a
         bridge, not a burn.
      3. does its balance ever FALL? Recent transactions' pre/post balances for this account:
         a sink only ever rises.
    """
    head("GEODNET — the Solana burn token account: mint, controller, and whether it ever spends")
    url = "https://api.mainnet-beta.solana.com"
    try:
        j = rpc(url, "getAccountInfo", [GEODNET_SOL_BURN_ACCOUNT, {"encoding": "jsonParsed"}])
    except Exception as e:  # noqa: BLE001
        print(f"  UNREACHABLE — {e}")
        return
    val = (j.get("result") or {}).get("value")
    if not val:
        print(f"  NO ACCOUNT at {GEODNET_SOL_BURN_ACCOUNT} — closed or never existed. Do not wire it.")
        return
    parsed = (val.get("data") or {}).get("parsed") or {}
    info = parsed.get("info") or {}
    amt = info.get("tokenAmount") or {}
    owner = info.get("owner")
    print(f"  program        {val.get('owner')}  (type {parsed.get('type')})")
    print(f"  mint           {info.get('mint')}  "
          f"{'= GEOD Solana mint' if info.get('mint') == GEODNET_SOL_MINT else '** NOT GEOD MINT ' + GEODNET_SOL_MINT + ' **'}")
    print(f"  balance        {amt.get('uiAmountString')} GEOD (decimals {amt.get('decimals')})")
    print(f"  state          {info.get('state')}  closeAuthority {info.get('closeAuthority')}  "
          f"delegate {info.get('delegate')}")
    print(f"  owner          {owner}")
    if owner == SOL_INCINERATOR:
        print("                 = the Solana INCINERATOR: nobody can sign for it — a burn sink.")
    elif owner:
        o, failed = None, False
        try:
            o = (rpc(url, "getAccountInfo", [owner, {"encoding": "jsonParsed"}]).get("result") or {}).get("value")
        except Exception as e:  # noqa: BLE001
            failed = True
            print(f"                 owner lookup failed — {e}")
        if failed:
            pass
        elif o is None:
            print("                 owner has no account data (an unfunded key, or a PDA with no state)")
        else:
            prog = o.get("owner")
            print(f"                 owner account program {prog}  executable {o.get('executable')}")
            if prog == SOL_SYSTEM_PROGRAM:
                print("                 ** an ORDINARY WALLET owns it: whoever holds that key can spend "
                      "these tokens. Not a sink unless GEODNET documents the key as destroyed. **")
    try:
        sigs = rpc(url, "getSignaturesForAddress", [GEODNET_SOL_BURN_ACCOUNT, {"limit": 15}]).get("result") or []
    except Exception as e:  # noqa: BLE001
        print(f"  signatures: UNREACHABLE — {e}")
        return
    print(f"\n  last {len(sigs)} transaction(s) touching the account — balance before -> after:")
    falls = 0
    for sg in sigs:
        when = time.strftime("%Y-%m-%d", time.gmtime(sg["blockTime"])) if sg.get("blockTime") else "?"
        try:
            tx = rpc(url, "getTransaction", [sg["signature"], {"encoding": "jsonParsed",
                                                               "maxSupportedTransactionVersion": 0}]).get("result") or {}
        except Exception as e:  # noqa: BLE001
            print(f"    {when} {sg['signature'][:12]}...  unreadable — {e}")
            continue
        keys = [k.get("pubkey") if isinstance(k, dict) else k
                for k in ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []]
        meta = tx.get("meta") or {}
        def bal(lst):
            for b in lst or []:
                if b.get("accountIndex") is not None and b["accountIndex"] < len(keys) \
                        and keys[b["accountIndex"]] == GEODNET_SOL_BURN_ACCOUNT:
                    return float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
            return None
        pre, post = bal(meta.get("preTokenBalances")), bal(meta.get("postTokenBalances"))
        move = "" if pre is None or post is None else ("  ** FELL **" if post < pre else "")
        falls += bool(move)
        print(f"    {when} {sg['signature'][:12]}...  {pre} -> {post}{move}")
    print(f"\n  VERDICT INPUTS: mint {'OK' if info.get('mint') == GEODNET_SOL_MINT else 'WRONG'}; "
          f"owner {'incinerator' if owner == SOL_INCINERATOR else owner}; "
          f"{falls} fall(s) in the transactions shown.")
    print("  PASTE BACK the whole block. It is summed into burn_address_balance only if the mint")
    print("  matches, nobody can spend from it, and the balance never falls.")


def near_block_supply():
    """A3 (2026-09-28): NEAR's total supply from the block header, now and ~1 day earlier.

    The issuance derivation reads header.total_supply (yoctoNEAR, net of burn). This shows the
    value, the one-day change and what it annualises to — expected ~2.5%/yr gross less the burn.
    The earlier block comes from the archival endpoint (docs.near.org RPC providers: archival-rpc.
    mainnet.near.org, public, heavily rate-limited)."""
    head("NEAR — block header total_supply, now and ~24h ago")
    def blk(url, params):
        return (rpc(url, "block", params).get("result") or {}).get("header") or {}
    try:
        now = blk("https://rpc.mainnet.near.org", {"finality": "final"})
    except Exception as e:  # noqa: BLE001
        print(f"  UNREACHABLE — {e}")
        return
    h, sup = int(now["height"]), int(now["total_supply"]) / 1e24
    print(f"  block {h:,}  total_supply {sup:,.4f} NEAR  ({now.get('timestamp_nanosec')})")
    try:
        then = blk("https://archival-rpc.mainnet.near.org", {"block_id": h - 86_400})
    except Exception as e:  # noqa: BLE001
        print(f"  archival read failed — {e}")
        return
    sup0 = int(then["total_supply"]) / 1e24
    secs = (int(now["timestamp_nanosec"]) - int(then["timestamp_nanosec"])) / 1e9
    d = sup - sup0
    print(f"  block {h - 86_400:,}  total_supply {sup0:,.4f}  ({secs / 3600:.1f}h earlier)")
    print(f"  change {d:+,.4f} NEAR -> {d / secs * 86400 * 365:,.0f}/yr "
          f"({d / secs * 86400 * 365 / sup0:.3%}/yr) NET of burn")
    print("  PASTE BACK: expect ~2.5%/yr less the burnt gas; issuance = this change + burn.")


def near_protocol_v87():
    """NEAR (Jake's run 2026-10-06 17:20): WHEN did protocol version 87 activate on mainnet? nearcore 87.yaml /
    CHANGELOG 2.14.0: "Remove gas rewards … burnt_gas_reward is changed from 30% (3/10) to 0%" (HSP-027, approved
    2026-07-06). Before it 70% of gas was burned; from it, 100% — and DefiLlama's revenue (fees x 0.7) then
    understates the burn by 30%. Reads the protocol version now (rpc.mainnet.near.org `status`), then
    binary-searches the archival node's EXPERIMENTAL_protocol_config by block for the first block at >= 87."""
    head("NEAR — protocol v87 (100% of gas burned) activation on mainnet")
    live, arch = "https://rpc.mainnet.near.org", "https://archival-rpc.mainnet.near.org"
    try:
        st = rpc(live, "status", []).get("result") or {}
        pv, h = int(st.get("protocol_version") or 0), int((st.get("sync_info") or {}).get("latest_block_height") or 0)
    except Exception as e:  # noqa: BLE001
        print(f"  UNREACHABLE — {e}")
        return
    print(f"  mainnet protocol_version now: {pv} at block {h:,}")
    if pv < 87:
        print("  v87 NOT ACTIVE: 70% of gas is still burned; DefiLlama's fees x 0.7 is the burn. Nothing to change.")
        return

    def ver(height):
        r = rpc(arch, "EXPERIMENTAL_protocol_config", {"block_id": int(height)}).get("result") or {}
        return int(r.get("protocol_version") or 0)

    def when(height):
        hd = (rpc(arch, "block", {"block_id": int(height)}).get("result") or {}).get("header") or {}
        return pd.Timestamp(int(hd.get("timestamp_nanosec") or 0), unit="ns")
    import pandas as pd                                    # noqa: PLC0415
    lo = h - int(120 * 86_400 / 0.6)                       # ~120 days back at ~0.6 s/block (a generous floor)
    try:
        if ver(lo) >= 87:
            print(f"  already >= 87 at block {lo:,} ({when(lo).date()}) — widen the floor")
            return
        hi = h
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if ver(mid) >= 87:
                hi = mid
            else:
                lo = mid
        t = when(hi)
    except Exception as e:  # noqa: BLE001
        print(f"  archival read failed — {e}")
        return
    print(f"  FIRST BLOCK AT >= 87: {hi:,} — {t} UTC ({t.date()})")
    print("  PASTE BACK. From that date the burn is 100% of gas fees; config Near.chain_burn_from_revenue.fees_rule "
          "and the revenue-based burn after it get the activation date.")


# World Mobile on Cardano: policy + asset name from the Cardano Foundation token registry,
# mappings/<policy><asset hex>.json (name "World Mobile Token X", ticker WMTX, decimals 6, url
# worldmobiletoken.com), read 2026-09-28. The legacy WMT policy is the pre-migration token.
WMTX_CARDANO = ("e5a42a1a1d3d1da71b0449663c32798725888d2eb0843c4dabeca05a",
                "576f726c644d6f62696c65546f6b656e58")
WMT_LEGACY_CARDANO = ("1d7f33bd23d85e1a25d87d86fac4f199c3197a2f7afeb662a0f34e1e",
                      "776f726c646d6f62696c65746f6b656e")
WMTX_EVM = (("ethereum", "0xDBB5Cf12408a3Ac17d668037Ce289f9eA75439D7"),
            ("bsc", "0xDBB5Cf12408a3Ac17d668037Ce289f9eA75439D7"),
            ("arbitrum", "0xDBB5Cf12408a3Ac17d668037Ce289f9eA75439D7"),
            ("base", "0x3e31966d4f81C72D2a55310A6365A56A4393E98D"))


WMTX_SOLANA_MINT = "WMTXyYKUMTG3VuZA5beXuHVRLpyTwwaoP7h2i8YpuRH"   # Chainlink CCIP directory


def wm_cardano_supply():
    """A8 / 2026-09-29: World Mobile's supply leg by leg, against the whitepaper curve.

    Koios (api.koios.rest, free, keyless) asset_info gives the Cardano policy's total_supply
    (6 decimals). Beside it: every EVM totalSupply() scaled by its OWN decimals() (WMTX is 6 —
    the 2026-09-28 version of this probe wrongly divided by 1e18), the Solana mint's
    getTokenSupply, and the curve's aggregate today (t0 2022-04-16, the curve's implied start).
      SUMMED NOW   Arbitrum + BNB + Base + Solana (Ethereum deprecated 2026-09-25).
      BASE CHECK   a re-issue of Ethereum holders onto the EXISTING Base contract raises its
                   totalSupply by roughly the pre-exploit Ethereum supply (~1.49bn); if Base is
                   still small, holders went to a NEW contract and config must follow it.
      CARDANO      burn-and-mint: Cardano + the sum ~ the curve; lock-and-mint: Cardano alone
                   ~ the curve. The closer one names the model; Cardano is not summed until
                   Jake has seen it."""
    head("WORLD MOBILE — every WMTX leg vs the whitepaper curve")
    card = {}
    for label, (policy, name) in (("WMTX", WMTX_CARDANO), ("WMT legacy", WMT_LEGACY_CARDANO)):
        try:
            r = requests.post("https://api.koios.rest/api/v1/asset_info",
                              json={"_asset_list": [[policy, name]]}, timeout=TIMEOUT)
            r.raise_for_status()
            row = (r.json() or [{}])[0]
            card[label] = int(row["total_supply"]) / 1e6
            print(f"  Cardano {label:<10} total_supply {card[label]:>18,.0f}  "
                  f"(mint_cnt {row.get('mint_cnt')}, burn_cnt {row.get('burn_cnt')})")
        except Exception as e:  # noqa: BLE001
            print(f"  Cardano {label:<10} UNREACHABLE — {e}")
    legs = {}
    for chain, addr in WMTX_EVM:
        v, d = _uint(addr, SEL_TOTAL_SUPPLY, chain), _uint(addr, SEL_DECIMALS, chain)
        legs[chain] = None if v is None or d is None else v / 10 ** d
        shown = "UNREACHABLE" if legs[chain] is None else f"{legs[chain]:,.0f} (decimals {d})"
        print(f"  {chain:<9} totalSupply {shown}"
              + ("   <- DEPRECATED 2026-09-25, not summed" if chain == "ethereum" else ""))
    try:
        from fetch.solana import SolanaRPC                # noqa: PLC0415
        v, raw, dec, host = SolanaRPC().token_supply(WMTX_SOLANA_MINT)
        legs["solana"] = v
        print(f"  solana    supply {v:,.0f} (raw {raw} / 10^{dec}, via {host})")
    except Exception as e:  # noqa: BLE001
        legs["solana"] = None
        print(f"  solana    UNREACHABLE — {e}")
    try:
        import pandas as pd                               # noqa: PLC0415
        import config                                     # noqa: PLC0415
        c = config.issuance_curve("World Mobile")
        t = (pd.Timestamp.now().normalize() - pd.Timestamp("2022-04-16")).days / 365.25
        model = config.issuance_curve_s0(c) * (t + 1) ** float(c["k"])
        print(f"  curve aggregate today (MODEL, t={t:.2f}y) {model:,.0f}")
    except Exception as e:  # noqa: BLE001
        model = None
        print(f"  curve not evaluated — {e}")
    summed = [k for k in ("arbitrum", "bsc", "base", "solana")]
    if all(legs.get(k) is not None for k in summed):
        total = sum(legs[k] for k in summed)
        print(f"  SUM NOW (arbitrum+bsc+base+solana) {total:,.0f}   band 1,420,000,000..2,000,000,000"
              f" -> {'INSIDE' if 1.42e9 <= total <= 2e9 else 'OUTSIDE — see the Base check'}")
        if model:
            print(f"  sum - curve = {total - model:+,.0f}")
        if "WMTX" in card and model:
            both, alone = abs(card["WMTX"] + total - model), abs(card["WMTX"] - model)
            print(f"  -> {'BURN-AND-MINT (sum)' if both < alone else 'LOCK-AND-MINT (Cardano alone)'}"
                  f" fits the curve better ({min(both, alone):,.0f} away).")
    if legs.get("base") is not None:
        print(f"  BASE CHECK: Base holds {legs['base']:,.0f}. Hundreds of millions to ~1.5bn means "
              f"holders were re-issued on 0x3e31...98D (config is right); tens of millions or less "
              f"means a NEW Base contract — find it on @wmchain and update contracts.token_base.")
    print("  PASTE BACK the lines above.")


def etherscan_ethsupply2():
    """A9 (2026-09-28): is stats/ethsupply2 on the free tier for the key in .env?

    Prints the four fields (wei -> ETH) and the supply they imply, EthSupply + Eth2Staking -
    BurntFees. A 'NOTOK' answer is printed verbatim with the key scrubbed: a message naming a
    plan means the endpoint is not on this key's tier and the DefiLlama-derived burn stays
    primary. The key is never printed; only the host is."""
    head("ETHEREUM — Etherscan stats/ethsupply2 (BurntFees, EthSupply, Eth2Staking)")
    try:
        from dotenv import load_dotenv                    # noqa: PLC0415
        load_dotenv()
    except Exception:  # noqa: BLE001
        pass
    key = os.environ.get("ETHERSCAN_API_KEY", "").strip()
    if not key:
        print("  NO ETHERSCAN_API_KEY in .env — nothing to test.")
        return
    try:
        r = requests.get("https://api.etherscan.io/v2/api", timeout=TIMEOUT,
                         params={"chainid": 1, "module": "stats", "action": "ethsupply2",
                                 "apikey": key})
        body = r.json()
    except Exception as e:  # noqa: BLE001
        print(f"  api.etherscan.io UNREACHABLE — {str(e).replace(key, '***')}")
        return
    res = body.get("result")
    if str(body.get("status")) != "1" or not isinstance(res, dict):
        print(f"  REFUSED: status {body.get('status')!r} message {body.get('message')!r} "
              f"result {str(res).replace(key, '***')[:200]!r}")
        return
    f = {k: int(v) / 1e18 for k, v in res.items()}
    for k, v in f.items():
        print(f"  {k:<15} {v:>20,.4f} ETH")
    print(f"  supply = EthSupply + Eth2Staking - BurntFees = "
          f"{f['EthSupply'] + f['Eth2Staking'] - f['BurntFees']:,.4f} ETH")
    print("  PASTE BACK: FREE TIER CONFIRMED if the four fields printed. Expect supply ~121-122M "
          "and BurntFees in the millions.")


AETHIR_PIN_HINTS = {
    "revenue_total": r"(?i)revenue|earning", "purchases_total": r"(?i)purchas|compute.*(ath|token)|spent",
    "apr_ai": r"(?i)apr|apy|yield", "apr_gaming": r"(?i)apr|apy|yield", "locked_ratio": r"(?i)ratio|locked|percent",
    "sophon_stath": r"(?i)sophon|stath", "eco_rewards_total": r"(?i)reward|distribut",
    "edge_earnings": r"(?i)edge|earning", "edge_stipend": r"(?i)stipend", "edge_daily_pool": r"(?i)daily|pool",
    "edge_devices": r"(?i)device|edge|count|node"}


def aethir_pin_keys():
    """Aethir (Jake's run 2026-10-06): the labelled figures without a pinned `key` are matched BY VALUE to
    Jake's 2026-10-01 readings and fail by design once the values move. For each, this prints the page's
    candidate keys with their CURRENT values — the nearest by value (any distance) and every key whose NAME
    fits the label — so the right key can be pinned in config (dashboard_pages.labelled.<id>.key) like `arr`.
    Read-only: one polite GET per page (robots.txt first), nothing stored."""
    import re as _re
    import config
    from fetch import aethir_pages as ap
    from fetch.scrape import robots_verdict
    head("AETHIR — candidate keys for the value-matched labelled figures")
    spec = config.PROJECT_BY_NAME["Aethir"]["dashboard_pages"]
    todo = {fid: f for fid, f in spec["labelled"].items() if not f.get("key") and not f.get("granularity")}
    pages = {}
    for page in sorted({pg for f in todo.values() for pg in f["pages"]}):
        url = spec["base"].rstrip("/") + "/" + page
        ok, why = robots_verdict(url)
        if not ok:
            print(f"  {page}: robots.txt disallows — {why}")
            continue
        try:
            pages[page] = ap.AethirPages(daily=object())._fetch(url)
        except Exception as e:  # noqa: BLE001
            print(f"  {page}: not read — {e}")
            continue
        size, numeric, chunks = ap.payload_health(pages[page])
        print(f"  {page}: {size:,} bytes, {numeric} numeric keys, {chunks} RSC chunks")
    for fid, f in todo.items():
        cands = []
        for pg in f["pages"]:
            html = pages.get(pg)
            if html is None:
                continue
            for k, vals in ap.fields(html).items():
                for v in vals:
                    for scale in ((1.0, 100.0) if f.get("pct") else (1.0,)):
                        if v and f["anchor"]:
                            cands.append((abs(v / scale / f["anchor"] - 1), pg, k, v / scale))
        cands.sort()
        hint = AETHIR_PIN_HINTS.get(fid)
        named = sorted({(pg, k, v) for _d, pg, k, v in cands if hint and _re.search(hint, k)}, key=lambda x: x[1])
        print(f"\n  {fid} — \"{f['label']}\" (read {f['anchor']:,} on {f['read_on']}):")
        print("    nearest by value: " + ("; ".join(f"{pg} `{k}` {v:,.4f} ({d:+.1%})" for d, pg, k, v in cands[:5])
                                          or "no numeric key on its pages"))
        if named:
            print("    keys whose name fits: " + "; ".join(f"{pg} `{k}` {v:,.4f}" for pg, k, v in named[:8]))
    print("\n  PASTE BACK. Each figure gets the key that is BOTH near its reading and named for it; a figure with "
          "no such key stays matched by value (and says so).")


def etherfi_safe_owners():
    """Ether.fi (Jake, 2026-10-05 21:00): WHO OWNS the Safe that sent 600,000 ETHFI into the buyback wallet
    (0x01e42ad3…, last 2026-05-20)? Read-only: getOwners() / getThreshold() / VERSION() on it and on the
    buyback Safe 0x2f5301a3…, and each owner's code size (EOA or contract). An owner shared with the
    buyback Safe, or the ether.fi deployer EOA, makes it an ether.fi-controlled wallet — an internal
    transfer, never a purchase. No address is wired from this; it prints what the chain says."""
    head("ETHER.FI — owners of the 600K-ETHFI Safe vs the buyback Safe")
    from fetch.base import redact
    from fetch.chain import ChainReader
    abi = [{"name": "getOwners", "type": "function", "stateMutability": "view", "inputs": [],
            "outputs": [{"type": "address[]"}]},
           {"name": "getThreshold", "type": "function", "stateMutability": "view", "inputs": [],
            "outputs": [{"type": "uint256"}]},
           {"name": "VERSION", "type": "function", "stateMutability": "view", "inputs": [],
            "outputs": [{"type": "string"}]}]
    known = {"0x9eac7114d1a1eabc4732a886795cfd9e6e35843f": "ether.fi deployer EOA"}
    try:
        r = ChainReader()
        w3 = r.web3("ethereum")
    except Exception as e:  # noqa: BLE001
        print(f"  no Ethereum RPC — {redact(str(e))}")
        return
    got = {}
    for label, addr in (("600K sender", "0x01e42ad3acd58584ffc1d1982ecbbe758996d601"),
                        ("buyback Safe", "0x2f5301a3D59388c509C65f8698f521377D41Fd0F")):
        c = w3.eth.contract(address=r.checksum(addr), abi=abi)
        try:
            owners = [o.lower() for o in c.functions.getOwners().call()]
            thr = c.functions.getThreshold().call()
        except Exception as e:  # noqa: BLE001
            print(f"  {label} {addr}: not a Safe, or unreadable — {redact(str(e))[:160]}")
            continue
        try:
            ver = c.functions.VERSION().call()
        except Exception:  # noqa: BLE001
            ver = "?"
        got[label] = set(owners)
        print(f"  {label} {addr}: Safe v{ver}, {thr}-of-{len(owners)}")
        for o in owners:
            code = len(w3.eth.get_code(r.checksum(o)))
            print(f"      {o}  {'contract' if code else 'EOA'}  {known.get(o, '')}")
    if len(got) == 2:
        shared = got["600K sender"] & got["buyback Safe"]
        print(f"  OWNERS SHARED WITH THE BUYBACK SAFE: {', '.join(sorted(shared)) or 'none'}")
        print("  PASTE BACK. Shared owners or the deployer EOA = ether.fi-controlled (internal transfer); "
              "none = still unidentified (label stays 'owner unidentified').")


def etherfi_sethfi_topups():
    """Ether.fi (Jake's run 2026-10-06 15:33): does the new buyback programme pay INTO sETHFI? sETHFI's share
    price rose 4.7%/yr, then 101.7%/yr over 2026-09-14..23, after the programme passed (2026-09-03).
    Read-only, Etherscan V2 (ETHERSCAN_API_KEY) + the Ethereum RPC:
      a) every ETHFI Transfer INTO sETHFI since 2026-08-01, each split by its transaction: sETHFI minted in the
         same tx = an ordinary DEPOSIT; none = a TOP-UP (assets in, no shares: the share price rises);
      b) the top-up senders, each checked as a Safe (getOwners) against the buyback Safe's owners and the
         known ether.fi addresses;
      c) where each top-up sender's own ETHFI came from since 2026-08-01 (CoW GPv2Settlement = BOUGHT;
         known ether.fi wallets = treasury/internal; other);
      d) top-ups x the day's stored price vs DefiLlama's ether.fi-stake holders revenue over the same days
         (metrics.db, when present). Nothing is wired from this; it prints what the chain says."""
    head("ETHER.FI — ETHFI into sETHFI since 2026-08-01: deposits vs top-ups, and who tops up")
    import pandas as pd                                     # noqa: PLC0415
    from collections import defaultdict                     # noqa: PLC0415
    from fetch.base import redact                           # noqa: PLC0415
    try:
        from fetch.explorer import ExplorerLogs, ExplorerRefused   # noqa: PLC0415
    except ImportError as e:
        print(f"  fetch.explorer not importable: {e}")
        return
    ex = ExplorerLogs()
    if not ex.configured(1):
        print("  no Etherscan key in .env (ETHERSCAN_API_KEY) — nothing read")
        return
    zero = "0x" + "0" * 40
    cow = "0x9008d19f58aabd9ed0d60971565aa8510560ab41"
    known = {"0x2f5301a3d59388c509c65f8698f521377d41fd0f": "OLD-programme buyback Safe",
             "0x9eac7114d1a1eabc4732a886795cfd9e6e35843f": "ether.fi deployer EOA",
             "0x01e42ad3acd58584ffc1d1982ecbbe758996d601": "ether.fi-controlled Safe (600K sender)",
             "0x0c83eae1fe72c390a02e426572854931eeff93ba": "protocol treasury (DefiLlama adapter)",
             cow: "CoW Protocol GPv2Settlement"}
    since = pd.Timestamp("2026-08-01", tz="UTC")
    try:
        b0 = ex.block_at(1, int(since.timestamp()))
        ins, m1 = ex.get_logs(1, ETHFI, [TRANSFER_TOPIC, None, _pad(SETHFI)], b0)
        mints, m2 = ex.get_logs(1, SETHFI, [TRANSFER_TOPIC, _pad(zero)], b0)
    except ExplorerRefused as e:
        print(f"  explorer refused: {redact(str(e))[:200]}")
        return
    from fetch.logscan import drop_poison                   # noqa: PLC0415
    real = {SETHFI.lower()} | set(known) | {"0x" + e["topics"][1][-40:].lower() for e in ins
                                            if _hexint(e.get("data"))}
    ins, poisoned = drop_poison(ins, 1, real)                # address-poisoning spam (2026-10-06 17:20)
    if poisoned:
        print(f"  {poisoned} zero-value lookalike transfer(s) dropped as address poisoning")
    print(f"  from block {b0:,} (2026-08-01): {len(ins):,} ETHFI transfer(s) into sETHFI, {len(mints):,} sETHFI "
          f"mint(s); served by {m1['explorer']} / {m2['explorer']}")
    minted = {str(e["transactionHash"]).lower() for e in mints}
    E = 10 ** 18

    def amt(e):
        return _hexint(e.get("data"))

    def day(e):
        return pd.Timestamp(int(e.get("timeStamp") or 0), unit="s").date()
    dep = [e for e in ins if str(e["transactionHash"]).lower() in minted]
    top = [e for e in ins if str(e["transactionHash"]).lower() not in minted]
    print(f"  DEPOSITS (shares minted in the tx): {len(dep):,} transfer(s), {sum(map(amt, dep)) / E:,.2f} ETHFI")
    print(f"  TOP-UPS (no shares minted):         {len(top):,} transfer(s), {sum(map(amt, top)) / E:,.2f} ETHFI")
    by = defaultdict(lambda: [0, 0, None, None])
    for e in top:
        r = by["0x" + e["topics"][1][-40:].lower()]
        r[0] += amt(e)
        r[1] += 1
        r[2] = min(r[2] or day(e), day(e))
        r[3] = max(r[3] or day(e), day(e))
    print("\n  TOP-UP SENDERS:")
    for a, (v, n, d0, d1) in sorted(by.items(), key=lambda kv: -kv[1][0]):
        print(f"    {a}  {v / E:>16,.2f} ETHFI  {n:>4} tx  {d0}..{d1}  {known.get(a, '')}")
    print("\n  TOP-UPS BY DAY (date, sender, ETHFI, tx):")
    for e in sorted(top, key=lambda e: int(e.get("timeStamp") or 0)):
        print(f"    {day(e)}  0x{e['topics'][1][-40:]}  {amt(e) / E:>14,.2f}  {e['transactionHash']}")

    # b) Safes and owners
    from fetch.chain import ChainReader                     # noqa: PLC0415
    abi = [{"name": "getOwners", "type": "function", "stateMutability": "view", "inputs": [],
            "outputs": [{"type": "address[]"}]},
           {"name": "getThreshold", "type": "function", "stateMutability": "view", "inputs": [],
            "outputs": [{"type": "uint256"}]}]
    try:
        r = ChainReader()
        w3 = r.web3("ethereum")
    except Exception as e:  # noqa: BLE001
        print(f"\n  no Ethereum RPC — owners not read ({redact(str(e))[:160]})")
        w3 = None

    def owners(a):
        try:
            c = w3.eth.contract(address=r.checksum(a), abi=abi)
            return {o.lower() for o in c.functions.getOwners().call()}, int(c.functions.getThreshold().call())
        except Exception:  # noqa: BLE001
            return None, None
    if w3 is not None:
        ref, _ = owners("0x2f5301a3D59388c509C65f8698f521377D41Fd0F")
        print(f"\n  buyback Safe owners: {', '.join(sorted(ref or [])) or 'unreadable'}")
        for a in sorted(by, key=lambda k: -by[k][0])[:6]:
            code = len(w3.eth.get_code(r.checksum(a)))
            own, thr = owners(a)
            if own is None:
                print(f"    {a}: {'contract (not a Safe, or unreadable)' if code else 'EOA'}"
                      + (" — IS a buyback Safe owner" if ref and a in ref else ""))
            else:
                shared = own & (ref or set())
                print(f"    {a}: Safe {thr}-of-{len(own)}; owners shared with the buyback Safe: "
                      f"{len(shared)} of {len(own)}" + (f" ({', '.join(sorted(shared))})" if shared else ""))

    # c) where the top-up senders' ETHFI came from
    print("\n  WHERE EACH TOP-UP SENDER'S ETHFI CAME FROM (since 2026-08-01):")
    for a in sorted(by, key=lambda k: -by[k][0])[:4]:
        try:
            src, _ = ex.get_logs(1, ETHFI, [TRANSFER_TOPIC, None, _pad(a)], b0)
        except ExplorerRefused as e:
            print(f"    {a}: unreadable — {redact(str(e))[:120]}")
            continue
        cls = defaultdict(int)
        for e in src:
            frm = "0x" + e["topics"][1][-40:].lower()
            cls[known.get(frm) or ("MINT" if frm == zero else frm)] += amt(e)
        rows = ", ".join(f"{k} {v / E:,.2f}" for k, v in sorted(cls.items(), key=lambda kv: -kv[1])[:6])
        # CoW AND UNISWAP v4 (Jake, 2026-10-06 16:33: "BOUGHT via CoW 0.00" missed the v4 PoolManager fills); the
        # full any-DEX rule (another token paid out in the same tx) is etherfi_topup_safe's.
        bought = cls.get(known[cow], 0) + sum(v for k, v in cls.items() if k == UNI_V4_POOL_MANAGER)
        print(f"    {a}: in {sum(cls.values()) / E:,.2f} — BOUGHT via CoW + Uniswap v4 {bought / E:,.2f}; "
              f"{rows or 'no inflow'}")

    # d) DefiLlama holders revenue over the same days
    print("\n  vs DEFILLAMA ether.fi-stake HOLDERS REVENUE (metrics.db):")
    try:
        import sqlite3                                       # noqa: PLC0415
        con = sqlite3.connect("metrics.db")
        q = ("SELECT date, metric, value FROM metrics WHERE project = 'Ether.fi' AND metric IN "
             "('holders_revenue_usd', 'price_usd') AND date >= '2026-09-03'")
        rows = con.execute(q).fetchall()
        con.close()
    except Exception as e:  # noqa: BLE001
        print(f"    metrics.db unreadable here ({e}) — compare by hand")
        return
    px = {str(d)[:10]: float(v) for d, m, v in rows if m == "price_usd"}
    hr = {str(d)[:10]: float(v) for d, m, v in rows if m == "holders_revenue_usd"}
    t_usd, unpriced = 0.0, 0
    for e in top:
        d = str(day(e))
        if d < "2026-09-03":
            continue
        if d in px:
            t_usd += amt(e) / E * px[d]
        else:
            unpriced += 1
    print(f"    since 2026-09-03: top-ups x same-day price ${t_usd:,.0f}" + (f" ({unpriced} unpriced)" if unpriced else "")
          + f"; DefiLlama holders revenue ${sum(hr.values()):,.0f} over {len(hr)} day(s)"
          + (f" ({min(hr)}..{max(hr)})" if hr else " — none stored (parent id fixed this round; next run)"))
    print("\n  PASTE BACK. Top-ups from an ether.fi Safe whose ETHFI came via CoW = the bought ETHFI paid to stakers "
          "(wire as the buyback from 2026-09-03 + the yield numerator); from the treasury / unbought = rewards, not a "
          "buyback. For the any-DEX classification by month run etherfi_topup_safe.")


ETHERFI_TOPUP_SAFE = "0x3fb6784e263643656f386a0371644931133d7b78"
ETHERFI_5M_SENDER = "0x83971edb4f24df6cf97b1b17d0e692bf11c63dcd"
UNI_V4_POOL_MANAGER = "0x000000000004444c5dc75cb358380d2e3de08a90"   # Uniswap/sdks sdk-core addresses.ts (mainnet)


def _etherfi_identify(ex, w3, r, addr: str, known: dict, ref_owners) -> None:
    """Who is `addr`: code size, Safe owners (and how many it shares with the buyback Safe), the OpenZeppelin
    VestingWallet getters, and its own ETHFI in/out by counterparty with the first date. Read-only."""
    import pandas as pd                                     # noqa: PLC0415
    from collections import defaultdict                     # noqa: PLC0415
    from fetch.base import redact                           # noqa: PLC0415
    from fetch.explorer import ExplorerRefused              # noqa: PLC0415
    from fetch.logscan import drop_poison                   # noqa: PLC0415
    E = 10 ** 18
    print(f"\n  WHO IS {addr}?  {known.get(addr, '')}")
    if w3 is not None:
        a = r.checksum(addr)
        code = w3.eth.get_code(a)
        print(f"    code: {len(code)} byte(s) — {'contract' if code else 'EOA'}")
        abi = [{"name": n, "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": t}]}
               for n, t in (("getOwners", "address[]"), ("getThreshold", "uint256"), ("VERSION", "string"),
                            ("beneficiary", "address"), ("owner", "address"), ("start", "uint256"),
                            ("duration", "uint256"))]
        c = w3.eth.contract(address=a, abi=abi)
        for fn in (("getOwners", "getThreshold", "VERSION", "beneficiary", "owner", "start", "duration") if code else ()):
            try:
                v = getattr(c.functions, fn)().call()
            except Exception:  # noqa: BLE001
                continue
            if fn == "getOwners":
                own = {o.lower() for o in v}
                print(f"    Safe owners ({len(own)}): {', '.join(sorted(own))}")
                if ref_owners is not None:
                    print(f"    owners shared with the buyback Safe: {len(own & ref_owners)} of {len(own)}")
            elif fn == "start" and isinstance(v, int):
                print(f"    start() = {v} ({pd.Timestamp(v, unit='s').date() if v > 0 else '-'})")
            elif fn == "duration" and isinstance(v, int):
                print(f"    duration() = {v} s ({v / 86_400:,.0f} days)")
            else:
                print(f"    {fn}() = {str(v).lower()}  {known.get(str(v).lower(), '')}")
    try:
        src, _ = ex.get_logs(1, ETHFI, [TRANSFER_TOPIC, None, _pad(addr)])
        out_, _ = ex.get_logs(1, ETHFI, [TRANSFER_TOPIC, _pad(addr), None])
    except ExplorerRefused as e:
        print(f"    its ETHFI flows unreadable — {redact(str(e))[:120]}")
        return
    src, _ = drop_poison(src, 1, [addr] + list(known))
    out_, _ = drop_poison(out_, 2, [addr] + list(known))
    for label, evs, side in (("IN", src, 1), ("OUT", out_, 2)):
        agg, last = defaultdict(int), {}
        for e in evs:
            k = "0x" + e["topics"][side][-40:].lower()
            agg[k] += _hexint(e.get("data"))
            last[k] = max(last.get(k, ""), str(pd.Timestamp(int(e.get("timeStamp") or 0), unit="s").date()))
        print(f"    ETHFI {label}: {sum(agg.values()) / E:,.2f} over {len(evs)} transfer(s) — "
              + ", ".join(f"{k} {v / E:,.2f} (last {last[k]})" + (f" [{known[k]}]" if k in known else "")
                          for k, v in sorted(agg.items(), key=lambda kv: -kv[1])[:6]))
    if src:
        first = min(int(e.get("timeStamp") or 0) for e in src)
        print(f"    first ETHFI in: {pd.Timestamp(first, unit='s').date()}"
              + (" — a mint/allocation-era date" if first < int(pd.Timestamp('2024-04-01').timestamp()) else ""))


BINANCE_HOT_WALLET = "0x28c6c06298d514db089934071355e5743bf21d60"     # Jake's probe 2026-10-06 (Etherscan: Binance 14)
ETHERFI_JULY_SENDERS = ("0x5ec5e6b4eb6827914ca8bc3ae02c39417242adde",
                        "0x66fcfc15a40f22fad40fd6b6b9741eef4de85721",
                        "0xe4439b1d150ab2febd72d699954c7b4dde2b66e2")


def etherfi_topup_safe():
    """Ether.fi (Jake, 2026-10-06 16:33 / 17:20): the top-up Safe 0x3fb6784e… buys ETHFI and pays it into sETHFI.
      a) every ETHFI inflow to it, classified on the pipeline's rule (log_scans.buyback_wallet_inflow,
         attribution "swap"): BOUGHT when the sender is a swap venue (CoW GPv2Settlement, Uniswap v4 PoolManager)
         or the Safe paid ANOTHER token out in the same transaction; otherwise TRANSFERRED — by month and sender.
         Zero-value lookalike transfers (address poisoning) are dropped first;
      b) who sent it ETHFI: 0x83971edb… (5M, Binance-withdrawn) and the July 2026 senders 0x5ec5e6b4…, 0x66fcfc15…,
         0xe4439b1d… — code size, Safe owners vs the buyback Safe's, VestingWallet getters, their own ETHFI flows.
    Read-only, Etherscan V2 + the Ethereum RPC. Nothing is wired from it."""
    head("ETHER.FI — the top-up Safe's ETHFI: bought vs transferred; who sends it")
    import pandas as pd                                     # noqa: PLC0415
    from collections import defaultdict                     # noqa: PLC0415
    from fetch.base import redact                           # noqa: PLC0415
    from fetch.logscan import drop_poison                   # noqa: PLC0415
    try:
        from fetch.explorer import ExplorerLogs, ExplorerRefused   # noqa: PLC0415
    except ImportError as e:
        print(f"  fetch.explorer not importable: {e}")
        return
    ex = ExplorerLogs()
    if not ex.configured(1):
        print("  no Etherscan key in .env (ETHERSCAN_API_KEY) — nothing read")
        return
    safe, cow = ETHERFI_TOPUP_SAFE, "0x9008d19f58aabd9ed0d60971565aa8510560ab41"
    venues = {cow: "CoW GPv2Settlement", UNI_V4_POOL_MANAGER: "Uniswap v4 PoolManager"}
    known = {"0x2f5301a3d59388c509c65f8698f521377d41fd0f": "OLD buyback Safe",
             "0x9eac7114d1a1eabc4732a886795cfd9e6e35843f": "ether.fi deployer EOA",
             "0x01e42ad3acd58584ffc1d1982ecbbe758996d601": "ether.fi-controlled Safe (600K sender)",
             "0x0c83eae1fe72c390a02e426572854931eeff93ba": "protocol treasury (Deployed.s.sol TREASURY)",
             ETHERFI_5M_SENDER: "EOA: 5M from Binance 2026-08-12, forwarded 2026-08-13",
             BINANCE_HOT_WALLET: "Binance hot wallet (Etherscan: Binance 14)",
             safe: "SELF", **venues}
    E = 10 ** 18
    try:
        ins, _ = ex.get_logs(1, ETHFI, [TRANSFER_TOPIC, None, _pad(safe)])
        sent, _ = ex.get_logs(1, None, [TRANSFER_TOPIC, _pad(safe)])
    except ExplorerRefused as e:
        print(f"  explorer refused: {redact(str(e))[:200]}")
        return
    real = {safe} | {"0x" + e["topics"][1][-40:].lower() for e in ins if _hexint(e.get("data"))}
    ins, poisoned = drop_poison(ins, 1, real | set(known))
    paid = {str(e["transactionHash"]).lower() for e in sent
            if str(e.get("address") or "").lower() != ETHFI.lower() and _hexint(e.get("data")) > 0}

    def amt(e):
        return _hexint(e.get("data"))
    month = defaultdict(lambda: [0, 0])                    # bought, transferred
    who = defaultdict(lambda: [0, 0, "", ""])              # amount, n, class, last
    for e in ins:
        frm = "0x" + e["topics"][1][-40:].lower()
        if frm == safe:
            cls = "self"
        elif frm in venues or str(e["transactionHash"]).lower() in paid:
            cls = "BOUGHT"
        else:
            cls = "transferred"
        m = pd.Timestamp(int(e.get("timeStamp") or 0), unit="s").strftime("%Y-%m")
        if cls != "self":
            month[m][0 if cls == "BOUGHT" else 1] += amt(e)
        r_ = who[(frm, cls)]
        r_[0] += amt(e)
        r_[1] += 1
        r_[3] = max(r_[3], str(pd.Timestamp(int(e.get("timeStamp") or 0), unit="s").date()))
    print(f"  {len(ins):,} ETHFI inflow(s) ({poisoned} zero-value lookalike transfer(s) dropped as address poisoning); "
          f"the Safe sent another token out in {len(paid):,} transaction(s)")
    print("\n  BY MONTH (ETHFI):            BOUGHT      TRANSFERRED")
    for m in sorted(month):
        b, t = month[m]
        print(f"    {m}   {b / E:>16,.2f}   {t / E:>16,.2f}")
    tb, tt = sum(v[0] for v in month.values()), sum(v[1] for v in month.values())
    print(f"    TOTAL     {tb / E:>16,.2f}   {tt / E:>16,.2f}"
          + (f"   (bought {tb / (tb + tt):.1%})" if tb + tt else ""))
    print("\n  BY SENDER:")
    for (frm, cls), (v, n, _, last) in sorted(who.items(), key=lambda kv: -kv[1][0]):
        print(f"    {frm}  {cls:<11} {v / E:>16,.2f}  {n:>4} tx  last {last}  {known.get(frm, '')}")

    from fetch.chain import ChainReader                     # noqa: PLC0415
    r = w3 = ref = None
    try:
        r = ChainReader()
        w3 = r.web3("ethereum")
        abi = [{"name": "getOwners", "type": "function", "stateMutability": "view", "inputs": [],
                "outputs": [{"type": "address[]"}]}]
        bs = w3.eth.contract(address=r.checksum("0x2f5301a3D59388c509C65f8698f521377D41Fd0F"), abi=abi)
        ref = {o.lower() for o in bs.functions.getOwners().call()}
    except Exception as e:  # noqa: BLE001
        print(f"\n  no Ethereum RPC — on-chain identity reads skipped ({redact(str(e))[:160]})")
    for a in (ETHERFI_5M_SENDER, *ETHERFI_JULY_SENDERS):
        _etherfi_identify(ex, w3, r, a, known, ref)
    print("\n  PASTE BACK. A Safe sharing owners with the buyback Safe, a vesting contract or a treasury makes a sender's "
          "ETHFI a TRANSFER; an exchange withdrawal is 'bought on a CEX (inferred)' only if etherfi_cex_test finds the "
          "payment going in — never on-chain 'bought'.")

# The two dollar stablecoins on Ethereum mainnet (6 decimals each) — read-only probe constants, not wired.
USDC_ETH = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
USDT_ETH = "0xdAC17F958D2ee523a2206206994597C13D831ec7"


def etherfi_cex_test():
    """Ether.fi (Jake, 2026-10-06 17:20): was the 5,000,000 ETHFI that reached the top-up Safe from Binance (via the EOA
    0x83971edb…, withdrawn 2026-08-12) BOUGHT on Binance? Test: did any ether.fi wallet — the top-up Safe, the old
    buyback Safe, the 600K Safe, the deployer — send USDC / USDT / ETH (~$3.3M at ~$0.66) toward Binance in the four
    weeks before (2026-07-15..2026-08-13)? A recipient counts as Binance when it IS the Binance hot wallet or
    FORWARDS what it got to it within 7 days (a deposit address). If yes, the 5M is "bought on a CEX (inferred)" —
    its own category, never mixed into on-chain "bought". Read-only, Etherscan V2."""
    head("ETHER.FI — did an ether.fi wallet pay Binance before the 5M ETHFI withdrawal? (CEX-buy test)")
    import pandas as pd                                     # noqa: PLC0415
    from collections import defaultdict                     # noqa: PLC0415
    from fetch.base import redact                           # noqa: PLC0415
    from fetch.logscan import drop_poison                   # noqa: PLC0415
    try:
        from fetch.explorer import ExplorerLogs, ExplorerRefused   # noqa: PLC0415
    except ImportError as e:
        print(f"  fetch.explorer not importable: {e}")
        return
    ex = ExplorerLogs()
    if not ex.configured(1):
        print("  no Etherscan key in .env (ETHERSCAN_API_KEY) — nothing read")
        return
    wallets = {ETHERFI_TOPUP_SAFE: "top-up Safe", "0x2f5301a3d59388c509c65f8698f521377d41fd0f": "old buyback Safe",
               "0x01e42ad3acd58584ffc1d1982ecbbe758996d601": "600K Safe",
               "0x9eac7114d1a1eabc4732a886795cfd9e6e35843f": "deployer EOA"}
    t0, t1 = pd.Timestamp("2026-07-15", tz="UTC"), pd.Timestamp("2026-08-13", tz="UTC")
    try:
        b0, b1 = ex.block_at(1, int(t0.timestamp())), ex.block_at(1, int(t1.timestamp()))
        b2 = ex.block_at(1, int((t1 + pd.Timedelta(days=7)).timestamp()))
    except ExplorerRefused as e:
        print(f"  explorer refused: {redact(str(e))[:200]}")
        return
    print(f"  window blocks {b0:,}..{b1:,} (2026-07-15..2026-08-13); forwarding checked to block {b2:,}")

    def eth_moves(addr, lo, hi):
        """[(to, wei, ts)] of ETH leaving `addr`: its own transactions and internal (Safe) transfers."""
        got = []
        for action in ("txlist", "txlistinternal"):
            try:
                res = ex._call("etherscan", 1, {"module": "account", "action": action, "address": addr,
                                                "startblock": lo, "endblock": hi, "sort": "asc"}) or []
            except ExplorerRefused as e:
                print(f"    {action} for {addr}: refused — {redact(str(e))[:100]}")
                continue
            for t in res if isinstance(res, list) else []:
                if str(t.get("from", "")).lower() == addr and int(t.get("value") or 0) > 0 and str(t.get("isError", "0")) == "0":
                    got.append((str(t.get("to", "")).lower(), int(t["value"]), int(t.get("timeStamp") or 0)))
        return got
    sent = defaultdict(lambda: [0.0, 0, ""])               # (wallet, asset, recipient) -> [amount, n, last]
    for w in wallets:
        for sym, tok in (("USDC", USDC_ETH), ("USDT", USDT_ETH)):
            try:
                logs, _ = ex.get_logs(1, tok, [TRANSFER_TOPIC, _pad(w)], b0, b1)
            except ExplorerRefused as e:
                print(f"    {sym} out of {w}: refused — {redact(str(e))[:100]}")
                continue
            logs, _ = drop_poison(logs, 2, [w])
            for e in logs:
                k = (w, sym, "0x" + e["topics"][2][-40:].lower())
                sent[k][0] += _hexint(e.get("data")) / 1e6
                sent[k][1] += 1
                sent[k][2] = max(sent[k][2], str(pd.Timestamp(int(e.get("timeStamp") or 0), unit="s").date()))
        for to, wei, ts in eth_moves(w, b0, b1):
            k = (w, "ETH", to)
            sent[k][0] += wei / 1e18
            sent[k][1] += 1
            sent[k][2] = max(sent[k][2], str(pd.Timestamp(ts, unit="s").date()))
    if not sent:
        print("\n  NO USDC / USDT / ETH LEFT ANY ether.fi WALLET in the window — no payment toward an exchange is visible "
              "on-chain. VERDICT: not shown to be a CEX purchase; classify the 5M as TRANSFERRED (exchange holdings / "
              "treasury), not bought.")
        return
    print("\n  OUTFLOWS IN THE WINDOW (wallet, asset, recipient, amount, transfers, last) and where each recipient sent it:")
    to_binance = defaultdict(float)
    for (w, sym, to), (amt_, n, last) in sorted(sent.items(), key=lambda kv: -kv[1][0])[:20]:
        verdict = ""
        if to == BINANCE_HOT_WALLET:
            verdict = "BINANCE hot wallet (direct)"
        else:
            try:
                if sym == "ETH":
                    fwd = [x for x in eth_moves(to, b0, b2) if x[0] == BINANCE_HOT_WALLET]
                else:
                    tok = USDC_ETH if sym == "USDC" else USDT_ETH
                    fl, _ = ex.get_logs(1, tok, [TRANSFER_TOPIC, _pad(to), _pad(BINANCE_HOT_WALLET)], b0, b2)
                    fwd = [e for e in fl if _hexint(e.get("data")) > 0]
                verdict = (f"forwards to the Binance hot wallet ({len(fwd)} transfer(s)) — a Binance DEPOSIT address"
                           if fwd else "not seen forwarding to the Binance hot wallet")
            except ExplorerRefused as e:
                verdict = f"onward flow unreadable — {redact(str(e))[:80]}"
        if verdict.startswith(("BINANCE", "forwards")):
            to_binance[sym] += amt_
        print(f"    {wallets[w]:<16} {sym:<4} -> {to}  {amt_:>16,.2f}  {n:>3} tx  last {last}  {verdict}")
    usd = to_binance.get("USDC", 0.0) + to_binance.get("USDT", 0.0)
    print(f"\n  TO BINANCE: USDC+USDT ${usd:,.0f}; ETH {to_binance.get('ETH', 0.0):,.4f}  (the 5M ETHFI at ~$0.66 is ~$3.3M)")
    print("  VERDICT: " + ("PAYMENT TO BINANCE FOUND — classify the 5M as 'bought on a CEX (inferred)', its own category, "
                           "never on-chain 'bought'." if to_binance else
                           "no payment reached Binance — the 5M is not shown to be a CEX purchase (TRANSFERRED)."))
    print("  PASTE BACK.")


GEOD_POLYGON = "0xAC0F66379A6d7801D7726d5a943356A172549Adb"
GEOD_MINING_WALLETS = ("0xfa5fEd5cc2b6DD8F370651D17242C52Ed711B14F",
                       "0x8FB9dd00B9a3D893dA96d444817d0b77330d5478")


def geod_archive_probe():
    """B1 (2026-09-28): which Polygon endpoints serve HISTORICAL balanceOf, a year back?

    The GEODNET release is inflows minus the change in the mining wallets' daily balances, so it
    needs archive state. For each endpoint (POLYGON_RPC_URL first, then the public list) this
    reads both wallets' GEOD balance at head, ~1 day, ~30 days and ~365 days back (Polygon ~2s
    blocks, so 43,200 blocks a day — the probe prints the actual block timestamps). An endpoint
    that answers the 365-day read serves the backfill; one that fails it serves routine runs
    only. Then run: python token_metrics.py --seed geodnet"""
    head("GEODNET — Polygon archive: mining-wallet balances a day, a month and a year back")
    try:
        from dotenv import load_dotenv                    # noqa: PLC0415
        load_dotenv()
    except Exception:  # noqa: BLE001
        pass
    for url in _rpcs_for("polygon"):
        host = _rpc_host(url)
        try:
            h = int(rpc(url, "eth_blockNumber")["result"], 16)
        except Exception as e:  # noqa: BLE001
            print(f"  {host}: UNREACHABLE — {str(e)[:120]}")
            continue
        print(f"  {host}: head {h:,}")
        for label, back in (("head", 0), ("1d", 43_200), ("30d", 1_296_000), ("365d", 15_768_000)):
            blk = hex(max(h - back, 1))
            try:
                ts = int(rpc(url, "eth_getBlockByNumber", [blk, False])["result"]["timestamp"], 16)
                vals = []
                for w in GEOD_MINING_WALLETS:
                    j = rpc(url, "eth_call", [{"to": GEOD_POLYGON, "data": SEL_BALANCE_OF
                                               + w.lower()[2:].rjust(64, "0")}, blk])
                    vals.append("ERR " + str(j.get("error"))[:60] if "error" in j
                                else f"{int(j['result'], 16) / 1e18:,.0f}")
                when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts))
                print(f"    {label:<5} block {int(blk, 16):,} ({when} UTC): {' | '.join(vals)}")
            except Exception as e:  # noqa: BLE001
                print(f"    {label:<5} FAILED — {str(e)[:120]}")
    print("  PASTE BACK: the first endpoint whose 365d line shows two numbers serves the backfill.")


def hl_af_fills_depth():
    """2026-09-29 (Jake): is there a HISTORICAL source for Hyperliquid before the first HyperCore
    read? userFillsByTime for the Assistance Fund returns its buy fills over a time range; Hyperliquid's
    API docs cap fills at the most recent 10,000, so this measures how far back that reaches.
    Pages newest-first by moving endTime back to the oldest fill seen."""
    head("HYPERLIQUID — Assistance Fund fills: how far back userFillsByTime reaches")
    import requests
    af = "0xfefefefefefefefefefefefefefefefefefefefe"
    url = "https://api.hyperliquid.xyz/info"
    end = int(time.time() * 1000)
    start = end - 365 * 86_400_000
    total, oldest, hype = 0, None, 0.0
    for _ in range(20):
        try:
            r = requests.post(url, json={"type": "userFillsByTime", "user": af, "startTime": start,
                                         "endTime": end, "aggregateByTime": True}, timeout=TIMEOUT)
            fills = r.json()
        except Exception as e:  # noqa: BLE001
            print(f"  userFillsByTime UNREACHABLE — {e}")
            return
        if not isinstance(fills, list) or not fills:
            break
        total += len(fills)
        hype += sum(float(f.get("sz") or 0) for f in fills
                    if f.get("coin") in ("HYPE", "@107") and f.get("side") == "B")
        t = min(int(f["time"]) for f in fills)
        if oldest is not None and t >= oldest:
            break
        oldest, end = t, t - 1
    if oldest is None:
        print("  no fills returned")
        return
    import datetime as _dt
    print(f"  {total} fill(s) back to {_dt.datetime.utcfromtimestamp(oldest / 1000):%Y-%m-%d}; "
          f"HYPE bought in them {hype:,.2f}. If that date is well inside the year, the 10,000-fill "
          f"cap binds and fills cannot give a year of buybacks — DefiLlama holders revenue stays "
          f"the history leg.")


def coinmetrics_community():
    """1a (2026-09-29): is TxTfrValAdjUSD on Coin Metrics' FREE community API? The GitHub mirror of
    the community catalog (coinmetrics/data @f1a36afb) says no, for every asset. This asks the
    live API: robots.txt, then the timeseries for eth (a metric outside the tier answers 403
    'forbidden'), then which of the portfolio's assets carry it. Licence: CC BY-NC 4.0."""
    head("COIN METRICS community — adjusted transfer value (TxTfrValAdjUSD)")
    base = "https://community-api.coinmetrics.io"
    try:
        r = requests.get(f"{base}/robots.txt", timeout=TIMEOUT)
        print(f"  robots.txt: HTTP {r.status_code} {r.text[:200]!r}")
    except Exception as e:  # noqa: BLE001
        print(f"  robots.txt: UNREACHABLE — {e}")
    assets = "eth,near,link,plume,hype,uni,aero,sky,pendle,inst,ethfi,morpho,syrup,geod,wmtx,ath"
    try:
        r = requests.get(f"{base}/v4/timeseries/asset-metrics",
                         params={"assets": "eth", "metrics": "TxTfrValAdjUSD", "frequency": "1d",
                                 "page_size": 3, "paging_from": "end"}, timeout=TIMEOUT)
        print(f"  eth TxTfrValAdjUSD: HTTP {r.status_code} {r.text[:300]}")
    except Exception as e:  # noqa: BLE001
        print(f"  eth TxTfrValAdjUSD: UNREACHABLE — {e}")
    try:
        r = requests.get(f"{base}/v4/catalog-v2/asset-metrics",
                         params={"assets": assets, "metrics": "TxTfrValAdjUSD"}, timeout=TIMEOUT)
        data = (r.json() or {}).get("data") if r.ok else None
        print(f"  catalog: HTTP {r.status_code}; assets carrying it: "
              f"{sorted({d.get('asset') for d in data or []}) or 'none'}")
    except Exception as e:  # noqa: BLE001
        print(f"  catalog: UNREACHABLE — {e}")
    print("  PASTE BACK. A 403/forbidden or an empty catalog confirms: not on the free tier.")


def archive_probe():
    """2026-09-29 (Jake): which endpoints serve STATE a year back, for archive_backfill.py.

    For each EVM chain the state-based series use (ethereum, polygon, arbitrum, base, bsc): the
    configured <CHAIN>_RPC_URL first, then the public list — eth_getBalance of the zero address at
    head and at a block ~365 days back (estimated from the head's block time, timestamp printed).
    An endpoint that answers the 365d line serves the backfill. Then NEAR's archival RPC (block by
    height ~365 days back, and validators at it) and Solana's getSignaturesForAddress on GEODNET's
    burn account (the oldest signature on the first page). Keys are never printed; hosts only."""
    head("ARCHIVE — which endpoints serve state a year back")
    try:
        from dotenv import load_dotenv                    # noqa: PLC0415
        load_dotenv()
    except Exception:  # noqa: BLE001
        pass
    # THE SAME RESOLVER THE BACKFILL USES (fetch.archive.resolve_archive): <CHAIN>_RPC_URL from
    # .env first (ETHEREUM_RPC_URL included), then every configured endpoint, each tested at a
    # block ~365 days back — so this tests exactly what archive_backfill.py will use.
    from fetch import archive as _ar                    # noqa: PLC0415
    for chain in ("ethereum", "polygon", "arbitrum", "base", "bsc"):
        print(f"\n  {chain}:")
        tried: list = []
        _w3, host = _ar.resolve_archive(chain, attempts=tried, every=True)
        for h, verdict in tried:
            print(f"    {h:<40} {verdict}")
        print(f"    -> the backfill will use: {host or 'NOTHING — no endpoint serves year-old state'}")
    from fetch.archive import near_archival_endpoints   # noqa: PLC0415
    print("\n  near (archival):")
    for url in near_archival_endpoints():
        try:
            hdr = rpc(url, "block", {"finality": "final"})["result"]["header"]
            h = int(hdr["height"]) - 365 * 78_000
            old = rpc(url, "block", {"block_id": h})
            if "error" in old:
                print(f"    {_rpc_host(url)}: head {hdr['height']:,}; height {h:,} REFUSED — {str(old['error'])[:100]}")
                continue
            oh = old["result"]["header"]
            v = rpc(url, "validators", {"block_id": h})
            print(f"    {_rpc_host(url)}: height {h:,} "
                  f"({time.strftime('%Y-%m-%d', time.gmtime(int(oh['timestamp']) // 10**9))}) total_supply "
                  f"{int(oh['total_supply']) / 1e24:,.0f} NEAR; validators "
                  + ("REFUSED — " + str(v["error"])[:100] if "error" in v else
                     f"{len(v['result']['current_validators'])} with "
                     f"{sum(int(x['stake']) for x in v['result']['current_validators']) / 1e24:,.0f} NEAR"))
        except Exception as e:  # noqa: BLE001
            print(f"    {_rpc_host(url)}: FAILED — {str(e)[:120]}")
    print("\n  solana (GEODNET burn account signatures):")
    try:
        import config as _c                                 # noqa: PLC0415
        url = _c.solana_rpc_endpoints()[0]
        sigs = rpc(url, "getSignaturesForAddress", [GEODNET_SOL_BURN_ACCOUNT, {"limit": 1000}]).get("result") or []
        if sigs:
            print(f"    {_rpc_host(url)}: {len(sigs)} signature(s) on the first page, oldest "
                  f"{time.strftime('%Y-%m-%d', time.gmtime(sigs[-1].get('blockTime') or 0))} — "
                  f"{'a full page: more pages behind it' if len(sigs) == 1000 else 'the whole history'}")
        else:
            print(f"    {_rpc_host(url)}: no signatures")
    except Exception as e:  # noqa: BLE001
        print(f"    FAILED — {str(e)[:120]}")
    print("\n  PASTE BACK the block. Then: python archive_backfill.py (plan), and --run.")


def plume_growthepie():
    """C3 (2026-09-28): Plume's last 7 days of daa and txcount as the API serves them, and the
    transactions per active address. Compare the latest day with www.growthepie.com/chains/plume
    (not reachable from the sandbox). growthepie's backend excludes the current day at source
    (json_creation.download_data), so every row printed is a complete day."""
    head("PLUME — growthepie fundamentals: daily active addresses vs transactions")
    try:
        rows = requests.get("https://api.growthepie.com/v1/fundamentals.json", timeout=TIMEOUT).json()
    except Exception as e:  # noqa: BLE001
        print(f"  UNREACHABLE — {e}")
        return
    by = {}
    for r in rows:
        if r.get("origin_key") == "plume" and r.get("metric_key") in ("daa", "txcount"):
            by.setdefault(r["date"], {})[r["metric_key"]] = float(r["value"])
    for d in sorted(by)[-7:]:
        daa, tx = by[d].get("daa"), by[d].get("txcount")
        per = f"{tx / daa:,.0f} tx/address" if daa and tx else ""
        print(f"  {d}  daa {daa if daa is None else f'{daa:,.0f}':>10}  txcount "
              f"{tx if tx is None else f'{tx:,.0f}':>12}  {per}")
    print("  PASTE BACK the latest line and the site's figure for the same date.")


def chainlink_reward_rates():
    """2026-09-29: Chainlink staking v0.2 yield from the RewardVault's own emission rates.

    getRewardBuckets() (RewardVault.sol 1.0.0 layout: operatorBase, communityBase,
    operatorDelegated; each emissionRate Juels/s, rewardDurationEndsAt, vestedRewardPerToken) and
    each pool's getTotalPrincipal(). Community yield = communityBase / community principal;
    operator yield = (operatorBase + operatorDelegated) / operator principal. Compare with
    Chainlink's published 4.32% community effective and 4.5% operator base (+ delegation)."""
    head("CHAINLINK — staking v0.2 reward rates from the RewardVault")
    from fetch.reward_vault import SEL_GET_REWARD_BUCKETS, SEL_TYPE_AND_VERSION, decode_buckets, decode_string
    from eth_utils import keccak                          # noqa: PLC0415
    sel_principal = "0x" + keccak(text="getTotalPrincipal()").hex()[:8]
    vault = "0x996913c8c08472f584ab8834e925b06D0eb1D813"
    pools = {"community": "0xBc10f2E862ED4502144c7d632a3459F49DFCDB5e",
             "operator": "0xA1d76A7cA72128541E9FCAcafBdA3a92EF94fDc5"}
    word, _ = eth_call(vault, SEL_TYPE_AND_VERSION)
    if not word:
        print("  UNREACHABLE")
        return
    print(f"  typeAndVersion: {decode_string(word)}")
    raw, _ = eth_call(vault, SEL_GET_REWARD_BUCKETS)
    b = decode_buckets(raw)
    now = int(time.time())
    yr = 31_557_600 / 1e18
    for k, (rate, ends, vested) in b.items():
        print(f"  {k:<18} {rate * yr:>14,.0f} LINK/yr  ends "
              f"{time.strftime('%Y-%m-%d', time.gmtime(ends))}{'' if ends > now else '  (ENDED)'}")
    pr = {}
    for k, a in pools.items():
        w, _ = eth_call(a, sel_principal)
        pr[k] = int(w, 16) / 1e18 if w else None
        print(f"  {k} principal {pr[k]:,.0f} LINK" if pr[k] else f"  {k} principal UNREACHABLE")
    act = lambda k: b[k][0] * yr if b[k][1] > now else 0.0  # noqa: E731
    if pr.get("community"):
        print(f"  community yield {act('communityBase') / pr['community']:.3%}  (published 4.32% effective)")
    if pr.get("operator"):
        print(f"  operator yield {(act('operatorBase') + act('operatorDelegated')) / pr['operator']:.3%}"
              f"  (published 4.5% base + delegation)")
    if all(pr.values()):
        tot = sum(act(k) for k in b)
        print(f"  blended {tot / sum(pr.values()):.3%} on {sum(pr.values()):,.0f} LINK; {tot:,.0f} LINK/yr")


def pendle_spendle_fees():
    """2026-09-29: where sPENDLE's 5% instant-unstake fee goes, and what spendle/data carries.

    StakedPendle.instantUnstake sends the fee to feeReceiver (owner-settable). This reads
    feeReceiver() and instantUnstakeFeeRate(), says whether the receiver has code, and prints
    the keys of api-v2.pendle.finance/core/v1/spendle/data so a per-epoch distribution field,
    if there is one, can be wired."""
    head("PENDLE — sPENDLE instant-unstake fee receiver, and the spendle/data fields")
    from eth_utils import keccak                          # noqa: PLC0415
    sp = "0x999999999991E178D52Cd95AFd4b00d066664144"
    sel = lambda sig: "0x" + keccak(text=sig).hex()[:8]  # noqa: E731
    w, _ = eth_call(sp, sel("feeReceiver()"))
    recv = as_address(w) if w else None
    r, _ = eth_call(sp, sel("instantUnstakeFeeRate()"))
    print(f"  feeReceiver {recv or 'UNREACHABLE'}  ({_code(recv, 'ethereum') if recv else '-'})")
    print(f"  instantUnstakeFeeRate {int(r, 16) / 1e18:.2%}" if r else "  instantUnstakeFeeRate UNREACHABLE")
    known = {"0x3dae3d1734ca3c7b3089d4dd03c9876e0a0102b4": "merkleDepositor",
             "0x33305665f69b4642d1275f4ce81c23651674d21c": "externalRewardsDistributor",
             "0x3942f7b55094250644cffda7160226caa349a38e": "vePendleAirdropDistributor"}
    if recv:
        print(f"  receiver is {known.get(recv.lower(), 'NOT a Pendle distributor in deployments/1-core.json')}")
    try:
        d = requests.get("https://api-v2.pendle.finance/core/v1/spendle/data", timeout=TIMEOUT).json()
        print(f"  spendle/data keys: {sorted(d)[:40] if isinstance(d, dict) else type(d).__name__}")
    except Exception as e:  # noqa: BLE001
        print(f"  spendle/data UNREACHABLE — {e}")
    print("  PASTE BACK: the receiver line (to a distributor = staker yield) and the keys.")


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


def _hexint(value) -> int:
    """A log's data word as an int; "0x" / "" (empty: a zero-value spam Transfer, a reverted call) is 0, never a crash
    (Jake's probes 2026-10-06 18:21)."""
    from fetch.logscan import hexint                       # noqa: PLC0415
    return hexint(value)


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
            tally[to] = tally.get(to, 0) + _hexint(lg["data"])
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


def _raw_code(addr: str, chain: str) -> str | None:
    for url in _rpcs_for(chain):
        try:
            j = rpc(url, "eth_getCode", [addr, "latest"])
            if "result" in j:
                return j["result"]
        except Exception:  # noqa: BLE001
            continue
    return None


def _slot(addr: str, slot: str, chain: str) -> str | None:
    for url in _rpcs_for(chain):
        try:
            j = rpc(url, "eth_getStorageAt", [addr, slot, "latest"])
            if "result" in j:
                return j["result"]
        except Exception:  # noqa: BLE001
            continue
    return None


# mint(address,uint256), mint(uint256), mintTo(address,uint256), issue(uint256) — the selectors a
# mint path is usually reached by; PUSH4 <selector> (0x63…) in the dispatcher is how it shows.
MINT_SELECTORS = {"40c10f19": "mint(address,uint256)", "a0712d68": "mint(uint256)",
                  "449a52f8": "mintTo(address,uint256)", "cc872b66": "issue(uint256)"}
EIP1967_IMPL = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"


# Axelar's Interchain Token Service, the same address on every EVM chain, and its InterchainToken
# implementation (axelarnetwork/axelar-contract-deployments@c30f3359 axelar-chains-config/info/
# mainnet.json, chains.ethereum/arbitrum.contracts.InterchainTokenService.address / .interchainToken).
AXELAR_ITS = "0xb5fb4be02232b1bba4dc8f81dc24c26980de9e3c"
AXELAR_INTERCHAIN_TOKEN_IMPL = "0x7f9f70da4af54671a6abac58e705b5634cac8819"


def _sel(sig: str) -> str:
    from eth_utils import keccak                           # noqa: PLC0415
    return keccak(text=sig).hex().removeprefix("0x")[:8]


def _dispatches(code: str, sig: str) -> bool:
    return ("63" + _sel(sig)) in (code or "").lower()


def _call_word(to: str, data: str, chain: str) -> str | None:
    word, _ = eth_call(to, data, chain=chain)
    return None if not word or word == "0x" else word


def _eip1167_impl(code: str) -> str | None:
    """The implementation an EIP-1167 minimal proxy delegates to (363d3d373d3d3d363d73<20 bytes>5af4…)."""
    c = (code or "").lower().removeprefix("0x")
    if c.startswith("363d3d373d3d3d363d73") and c[60:64] == "5af4":
        return "0x" + c[20:60]
    return None


def aethir_mint_path():
    """Jake 2026-10-01: Aethir's gross_issuance_tokens is a DECLARED ZERO (ATH pre-minted) unless a
    token contract shows a mint path. His first run: Ethereum ATH dispatches mint(uint256) (8,806 bytes,
    not EIP-1967, totalSupply 42bn) — and the old summary line wrongly said "no mint selector". Now the
    verdict is computed from what is read:
      ETHEREUM  which mint functions are dispatched; a cap (cap() / maxSupply() / MAX_SUPPLY()) and
                whether totalSupply already equals it; who may mint (owner(), MINTER_ROLE holders).
      ARBITRUM  45 bytes = an EIP-1167 minimal proxy: followed to its implementation, which is checked
                for mint/burn and against Axelar's InterchainToken; interchainTokenService() read through
                the proxy. Axelar ITS mint/burn moves supply between chains — it creates none."""
    head("AETHIR — does either ATH contract carry a mint path? (verdict from the reads)")
    verdicts = []
    # ---- Ethereum
    eth = "0xbe0Ed4138121EcFC5c0E56B40517da27E6c5226B"
    code = _raw_code(eth, "ethereum") or ""
    ts = _uint(eth, "0x18160ddd", "ethereum")
    print(f"  ETHEREUM {eth}: {len(code) // 2 - 1:,} bytes; totalSupply "
          f"{'n/a' if ts is None else f'{ts / 1e18:,.0f}'}")
    mints = [sig for sig in ("mint(uint256)", "mint(address,uint256)", "mintTo(address,uint256)") if _dispatches(code, sig)]
    print(f"    mint functions dispatched: {mints or 'NONE'}")
    cap = None
    for sig in ("cap()", "maxSupply()", "MAX_SUPPLY()", "totalSupplyCap()"):
        if _dispatches(code, sig):
            v = _uint(eth, "0x" + _sel(sig), "ethereum")
            print(f"    {sig} = {'n/a' if v is None else f'{v / 1e18:,.0f}'}")
            cap = v if v is not None else cap
    if cap is None:
        print("    no cap function dispatched (cap()/maxSupply()/MAX_SUPPLY()/totalSupplyCap())")
    owner = _call_word(eth, "0x" + _sel("owner()"), "ethereum") if _dispatches(code, "owner()") else None
    if owner:
        print(f"    owner() = 0x{owner[-40:]}  (code: {_code('0x' + owner[-40:], 'ethereum')})")
    if _dispatches(code, "MINTER_ROLE()") and _dispatches(code, "getRoleMemberCount(bytes32)"):
        role = (_call_word(eth, "0x" + _sel("MINTER_ROLE()"), "ethereum") or "").removeprefix("0x")
        n = _uint(eth, "0x" + _sel("getRoleMemberCount(bytes32)") + role, "ethereum") or 0
        holders = [("0x" + (_call_word(eth, "0x" + _sel("getRoleMember(bytes32,uint256)") + role
                                       + hex(i)[2:].rjust(64, "0"), "ethereum") or "0" * 64)[-40:]) for i in range(n)]
        print(f"    MINTER_ROLE holders ({n}): {holders}")
    elif _dispatches(code, "hasRole(bytes32,address)"):
        print("    AccessControl present (hasRole) but not enumerable — role holders need the RoleGranted logs")
    if not mints:
        verdicts.append("ETHEREUM: no mint function dispatched — no mint path")
    elif cap is not None and ts is not None and ts >= cap:
        verdicts.append(f"ETHEREUM: mint dispatched but totalSupply {ts / 1e18:,.0f} = the hard cap "
                        f"{cap / 1e18:,.0f} — MINTING IMPOSSIBLE; the declared zero stands")
    else:
        verdicts.append("ETHEREUM: MINT PATH EXISTS and no reached cap was read — record who can mint (above) "
                        "and WATCH totalSupply; the declared zero holds only while it stays at 42bn")
    # ---- Arbitrum
    arb = "0xc87B37a581ec3257B734886d9d3a581F5A9d056c"
    acode = _raw_code(arb, "arbitrum") or ""
    impl = _eip1167_impl(acode)
    ats = _uint(arb, "0x18160ddd", "arbitrum")
    print(f"\n  ARBITRUM {arb}: {len(acode) // 2 - 1:,} bytes; totalSupply "
          f"{'n/a' if ats is None else f'{ats / 1e18:,.0f}'}; "
          + (f"EIP-1167 minimal proxy -> {impl}" if impl else "not an EIP-1167 proxy"))
    target = _raw_code(impl, "arbitrum") if impl else acode
    am = [sig for sig in ("mint(address,uint256)", "burn(address,uint256)") if _dispatches(target, sig)]
    print(f"    implementation dispatches: {am or 'no mint/burn'}")
    its = _call_word(arb, "0x" + _sel("interchainTokenService()"), "arbitrum")
    its = ("0x" + its[-40:]).lower() if its else None
    tid = _call_word(arb, "0x" + _sel("interchainTokenId()"), "arbitrum")
    print(f"    interchainTokenService() = {its}; interchainTokenId() = {tid}")
    axelar = (impl or "").lower() == AXELAR_INTERCHAIN_TOKEN_IMPL or its == AXELAR_ITS
    if not am:
        verdicts.append("ARBITRUM: no mint/burn in the implementation — no mint path")
    elif axelar:
        verdicts.append("ARBITRUM: Axelar ITS InterchainToken (implementation and/or service match Axelar's "
                        "registry) — mint/burn by its token manager when ATH bridges; it MOVES supply between "
                        "chains and creates none; the declared zero stands for this chain")
    else:
        verdicts.append("ARBITRUM: mint/burn present and NOT matched to Axelar's InterchainToken — classify "
                        "before relying on the declared zero")
    print("\n  VERDICT:")
    for v in verdicts:
        print(f"    {v}")
    print("  PASTE BACK all lines.")



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
        v = _hexint(lg["data"])
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


# ===== GEODNET: THE ~74M COINGECKO EXCLUDES THAT WE DO NOT (Jake's probe 2026-10-07). =====
# With the three candidates out, ours ~536.4M vs CoinGecko ~462.4M. Our exclusions are POLYGON wallets; GEODNET
# migrated toward Solana (GIP-7), so allocation / mining / treasury SKY-side balances may sit in Solana accounts while
# the Polygon side holds the bridged lock (counted as circulating by us). This lists, read-only:
#   1. the Solana mint's total supply and its 20 largest accounts (getTokenLargestAccounts — free public RPC), each
#      with its OWNER (getAccountInfo) — a treasury / mining / bridge wallet shows up here by size;
#   2. the largest Polygon GEOD destinations of the last `days` days (the same read as geodnet_staking_candidates).
# Nothing is wired: a wallet is excluded only when GEODNET's own page names it (NONCIRCULATING_CANDIDATES).
# ===== ALCHEMY ON BASE — HOW WIDE AN eth_getLogs RANGE DOES THE FREE TIER SERVE? (Jake, 2026-10-07) =====
# Base has no free explorer log route (Etherscan free excludes it; Blockscout's PRO API is paid on Base), so
# Chainlink's Base PLUS lines (VRF, Automation, CCIP) and Fluid's Base MerkleDistributors are not scanned and
# Chainlink's lines read "PARTIAL, missing: base". Alchemy's support page says the FREE tier caps eth_getLogs at
# 10 blocks on Base (PAYG: 10,000 blocks, or any range returning <= 10,000 logs) — this measures it on Jake's key,
# and what a year and a day of those lines would cost at the range that works. Reads only; nothing stored.
_BASE_BLOCKS_PER_DAY = 43_200                                # 2-second blocks


def alchemy_base_logs(chain: str = "base"):
    from fetch.base import redact                           # noqa: PLC0415
    import config                                           # noqa: PLC0415
    head(f"ALCHEMY on {chain.upper()} — widest eth_getLogs range the keyed endpoint serves, and a year at it")
    try:
        from dotenv import load_dotenv                      # noqa: PLC0415
        load_dotenv()
    except Exception:  # noqa: BLE001
        pass
    url = os.environ.get(f"{chain.upper()}_RPC_URL", "").split(",")[0].strip()
    if not url:
        print(f"  {chain.upper()}_RPC_URL is not set — nothing to test.")
        return
    print(f"  endpoint: {_rpc_host(url)} (key not shown)")
    lines = config.PROJECT_BY_NAME["Chainlink"]["chainlink_fee_lines"]["chains"].get(chain) or {}
    addrs = [lines[k] for k in ("vrf_v2_5", "automation_registry", "ccip_router") if lines.get(k)]
    # Fluid's Base MerkleDistributors paying FLUID 0x61E030A5… (Instadapp/fluid-contracts-public deployments.md
    # @9496626, '## MerkleDistributors', read 2026-10-06): Transfer logs OUT of each, on the token.
    fluid_token = "0x61E030A56D33e8260FdD81f03B162A79Fe3449Cd"
    fluid_holders = ["0x94312a608246Cecfce6811Db84B3Ef4B2619054E", "0xF36029358A684CdDD5103A4b84dC8a832c6e5b40"]
    try:
        tip = int(rpc(url, "eth_blockNumber")["result"], 16)
    except Exception as e:  # noqa: BLE001
        print(f"  eth_blockNumber FAILED — {redact(str(e))[:200]}")
        return
    print(f"  head block {tip:,}")
    widest, lat = 0, []
    for span in (10, 100, 1_000, 10_000, 100_000):
        flt = {"fromBlock": hex(tip - span + 1), "toBlock": hex(tip), "address": addrs}
        t0 = time.time()
        try:
            j = rpc(url, "eth_getLogs", [flt])
        except Exception as e:  # noqa: BLE001
            print(f"    {span:>7,} blocks: FAILED — {redact(str(e))[:200]}")
            break
        dt = time.time() - t0
        if "error" in j:
            print(f"    {span:>7,} blocks: REFUSED — {redact(str(j['error']))[:220]}")
            break
        widest = span
        lat.append(dt)
        print(f"    {span:>7,} blocks: OK, {len(j.get('result') or [])} log(s) from the Chainlink lines, {dt:.2f}s")
    if not widest:
        print("  -> no range served: there is no Base log route on this key.")
        return
    flt = {"fromBlock": hex(tip - widest + 1), "toBlock": hex(tip), "address": fluid_token,
           "topics": [TRANSFER_TOPIC, [_pad(h) for h in fluid_holders]]}
    try:
        j = rpc(url, "eth_getLogs", [flt])
        print(f"  Fluid Base distributors, last {widest:,} blocks: "
              + (f"REFUSED — {redact(str(j['error']))[:160]}" if "error" in j else f"{len(j.get('result') or [])} claim(s)"))
    except Exception as e:  # noqa: BLE001
        print(f"  Fluid Base distributors: FAILED — {redact(str(e))[:160]}")
    per = sum(lat) / len(lat)
    year_calls = -(-365 * _BASE_BLOCKS_PER_DAY // widest)
    day_calls = -(-_BASE_BLOCKS_PER_DAY // widest)
    print(f"\n  WIDEST RANGE SERVED: {widest:,} blocks ({widest * 2 / 60:.1f} minutes of Base). One filter carries every")
    print("  address of a line set, so these are calls per FILTER (Chainlink's lines in one, Fluid's in another):")
    print(f"    a year:  {year_calls:,} calls per filter ~ {year_calls * per / 3600:,.1f} h at the measured {per:.2f}s/call, "
          f"one at a time")
    print(f"    a day:   {day_calls:,} calls per filter (the daily increment)")
    print("  READING IT: under ~20,000 calls a year (the explorers' scale) is practical — wire it. At the free tier's")
    print("  10 blocks a year is ~1.58M calls per filter (days of calls, and well past a free monthly compute quota);")
    print("  then Base stays PARTIAL and the choice is Alchemy PAYG (10,000-block ranges: ~1,600 calls a year).")
    print("  Nothing was stored. PASTE BACK the section.")


# ===== NEAR's THREE REVENUE / BUYBACK WALLETS, MONTH BY MONTH (Jake, 2026-10-07). =====
# revenue.near.org's "Wallet Breakdown (All-time)": fefundsadmin.sputnik-dao.near 1,846,188.3 NEAR (48.8%),
# buybacks.multisignature.near 1,384,693 (36.6%), 1csfundsadmin.sputnik-dao.near 555,348.3 (14.7%) — 3,786,229.6
# in all. The page's backend counts NEAR transfers AND wNEAR ft_transfers into the three, excluding transfers
# between them (DefiLlama fees/near-intents/index.ts mirrors it: Dune 6740088 / 6732239). NearBlocks v3
# (Nearblocks/nearblocks @4024051b apps/api/src/routes/v3): /accounts/{a}/balance, /stats/balance (daily end
# balance, yocto, newest first), /txns and /ft-txns (cursor `next`, limit <= 100). ~1 credit a call.
NEAR_REVENUE_WALLETS = ("fefundsadmin.sputnik-dao.near", "buybacks.multisignature.near",
                        "1csfundsadmin.sputnik-dao.near")


def near_buyback_wallets(max_pages: int = 40):
    import pandas as pd                                    # noqa: PLC0415
    from fetch.base import redact                          # noqa: PLC0415
    head("NEAR — the three revenue/buyback wallets: NEAR + wNEAR in and out by month (NearBlocks v3)")
    try:
        from dotenv import load_dotenv                    # noqa: PLC0415
        load_dotenv()
    except Exception:  # noqa: BLE001
        pass
    key = os.environ.get("NEARBLOCKS_API_KEY", "").strip()
    if not key:
        print("  NEARBLOCKS_API_KEY is not set — nothing read.")
        return
    base, hdr = "https://api.nearblocks.io/v3", {**_ua(), "Authorization": f"Bearer {key}"}
    calls = 0

    def get(path, params=None):
        nonlocal calls
        calls += 1
        r = requests.get(base + path, params=params or {}, headers=hdr, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def pages(path, params):
        out, nxt = [], None
        for _ in range(max_pages):
            j = get(path, {**params, "limit": 100, **({"next": nxt} if nxt else {})})
            out += j.get("data") or []
            nxt = (j.get("meta") or {}).get("next_page")
            if not nxt:
                return out, True
        return out, False

    wallets = set(NEAR_REVENUE_WALLETS)
    grand_in = 0.0
    for w in NEAR_REVENUE_WALLETS:
        print(f"\n  {w}")
        try:
            bal = get(f"/accounts/{w}/balance").get("data") or {}
            print(f"    balance now: {int(bal.get('amount') or 0) / 1e24:,.1f} NEAR liquid, "
                  f"{int(bal.get('amount_staked') or 0) / 1e24:,.1f} staked")
            hist = get(f"/accounts/{w}/stats/balance", {"limit": 365}).get("data") or []
            if hist:
                hb = pd.Series({pd.Timestamp(h["date"]): int(h["amount"]) / 1e24 for h in hist}).sort_index()
                me = hb.groupby(hb.index.to_period("M")).last()
                print("    month-end liquid balance: " + ", ".join(f"{m} {v:,.0f}" for m, v in me.tail(9).items()))
            ins, outs = {}, {}
            txs, done_t = pages(f"/accounts/{w}/txns", {})
            for t in txs:
                dep = int((t.get("actions_agg") or {}).get("deposit") or 0) / 1e24
                if not dep or (t.get("outcomes") or {}).get("status") is False:
                    continue
                m = pd.Timestamp(int(t["block_timestamp"]), unit="ns").to_period("M")
                sig, rcv = t.get("signer_account_id"), t.get("receiver_account_id")
                if rcv == w and sig not in wallets:
                    ins[m] = ins.get(m, 0.0) + dep
                elif sig == w and rcv not in wallets:
                    outs.setdefault(m, {}).setdefault(rcv, 0.0)
                    outs[m][rcv] += dep
            fts, done_f = pages(f"/accounts/{w}/ft-txns", {"contract": "wrap.near"})
            for f in fts:
                amt = int(f.get("delta_amount") or 0) / 1e24
                m = pd.Timestamp(int(f["block_timestamp"]), unit="ns").to_period("M")
                peer = f.get("involved_account_id")
                if peer in wallets:
                    continue
                if amt > 0:
                    ins[m] = ins.get(m, 0.0) + amt
                elif amt < 0:
                    outs.setdefault(m, {}).setdefault(f"{peer} (wNEAR)", 0.0)
                    outs[m][f"{peer} (wNEAR)"] += -amt
            tot = sum(ins.values())
            grand_in += tot
            print(f"    IN (NEAR deposits + wNEAR, excluding the other two wallets): {tot:,.1f} NEAR"
                  + ("" if done_t and done_f else f"  [PARTIAL: stopped at {max_pages} pages]"))
            for m in sorted(set(ins) | set(outs)):
                o = outs.get(m) or {}
                top = sorted(o.items(), key=lambda kv: -kv[1])[:3]
                print(f"      {m}: in {ins.get(m, 0.0):,.1f}; out {sum(o.values()):,.1f}"
                      + (" -> " + ", ".join(f"{k} {v:,.0f}" for k, v in top) if top else ""))
        except Exception as e:  # noqa: BLE001
            print(f"    FAILED — {redact(str(e))[:200]}")
    print(f"\n  ALL THREE, IN: {grand_in:,.1f} NEAR vs the page's 3,786,229.6 "
          f"({grand_in / 3_786_229.6 - 1:+.1%}); {calls} call(s) (~1 credit each)")
    print("  READING IT: a wallet whose OUT lines are empty (or only to the other two) is buyback-and-HOLD — its NEAR")
    print("  leaves free float but is not burned. NEAR has no burn address; an outflow is a burn only if it goes to")
    print("  a key-less account the protocol names as such. Monthly IN x that month's price is what to set beside the")
    print("  page's monthly net revenue. Nothing was stored. PASTE BACK the section.")


# ===== SKY — BLOCK ANALITICA'S ENDPOINTS: WHAT THEY RETURN (Jake, 2026-10-07). =====
# The accounting API (sky.data.blockanalitica.com/v1/accounting/...) has no public documentation we could find — no
# schema, no category names — so nothing is wired from it until its shape is read. The info-sky endpoints below are
# used in public code (jetstreamgg/tarmac @5b650e1b apps/webapp/src/hooks; DefiLlama fees/makerdao.ts @7bffe3f3).
# robots.txt is checked first for every host; one GET per path.
SKY_BA_PATHS = (
    "https://sky.data.blockanalitica.com/v1/accounting/profit-and-loss/",
    "https://sky.data.blockanalitica.com/v1/accounting/profit-and-loss/history/",
    "https://sky.data.blockanalitica.com/v1/accounting/cash-flow/",
    "https://sky.data.blockanalitica.com/v1/accounting/cash-flow/history/",
    "https://sky.data.blockanalitica.com/v1/accounting/cash-flow/items/",
    "https://sky.data.blockanalitica.com/v1/accounting/balance-sheet/",
    "https://info-sky.blockanalitica.com/api/v1/farms/0xb44c2fb4181d7cb06bdff34a46fdfe4a259b40fc/historic/?p_size=5",
    "https://info-sky.blockanalitica.com/api/v1/farms/0x38e4254bd82ed5ee97cd1c4278faae748d998865/historic/?p_size=5",
    "https://info-sky.blockanalitica.com/api/v1/overall/",
    "https://info-sky.blockanalitica.com/buyback/historic/?days_ago=30&format=json",
)


def sky_ba_endpoints():
    from fetch.scrape import robots_verdict                # noqa: PLC0415
    head("SKY — Block Analitica endpoints: robots.txt, then each path's shape (keys, first rows)")
    for url in SKY_BA_PATHS:
        ok, why = robots_verdict(url)
        print(f"\n  {url}\n    robots: {'ALLOWED' if ok else 'DISALLOWED'} — {why}")
        if not ok:
            continue
        try:
            r = requests.get(url, headers=_ua(), timeout=TIMEOUT)
            print(f"    HTTP {r.status_code}, {len(r.content):,} bytes, {r.headers.get('content-type', '?')}")
            j = r.json()
            body = j.get("data", j) if isinstance(j, dict) else j
            rows = body.get("results", body) if isinstance(body, dict) else body
            if isinstance(rows, list):
                print(f"    {len(rows)} row(s); keys of the first: {sorted(rows[0]) if rows and isinstance(rows[0], dict) else '-'}")
                for row in rows[:3]:
                    print(f"      {json.dumps(row)[:300]}")
            else:
                print(f"    keys: {sorted(rows)[:40] if isinstance(rows, dict) else type(rows).__name__}")
                print(f"      {json.dumps(rows)[:600]}")
        except Exception as e:  # noqa: BLE001
            print(f"    FAILED — {type(e).__name__}: {str(e)[:200]}")
    print("\n  WANTED: monthly Net Protocol Surplus; the cash-flow category for buyback spending (its exact name);")
    print("  staking-reward lines; per-farm apr / total_staked. Nothing was stored. PASTE BACK the section.")


def geod_residual(days: int = 180):
    head("GEODNET — where is the ~74M CoinGecko excludes and we count? Solana largest accounts + Polygon sinks")
    url = "https://api.mainnet-beta.solana.com"
    try:
        sup = (rpc(url, "getTokenSupply", [GEODNET_SOL_MINT]).get("result") or {}).get("value") or {}
        print(f"  Solana mint {GEODNET_SOL_MINT}: supply {sup.get('uiAmountString')} GEOD")
        big = (rpc(url, "getTokenLargestAccounts", [GEODNET_SOL_MINT]).get("result") or {}).get("value") or []
    except Exception as e:  # noqa: BLE001
        print(f"  Solana UNREACHABLE — {e}")
        big = []
    if big:
        print(f"\n  {'token account':<46} {'GEOD':>16}  owner (wallet / program)")
        for a in big:
            owner = "?"
            try:
                v = (rpc(url, "getAccountInfo", [a["address"], {"encoding": "jsonParsed"}]).get("result") or {}).get("value")
                owner = ((((v or {}).get("data") or {}).get("parsed") or {}).get("info") or {}).get("owner") or "?"
            except Exception:  # noqa: BLE001
                pass
            print(f"  {a['address']:<46} {float(a.get('uiAmount') or 0):>16,.0f}  {owner}")
        print("  READING IT: GEODNET's own reward / treasury / bridge accounts are the large ones; any of them not")
        print("  yet in config (contracts burn_solana_token_account; NONCIRCULATING_CANDIDATES) is a candidate. Its")
        print("  role must come from GEODNET's docs or a GIP before it is excluded.")
    print()
    geodnet_staking_candidates(days)


# ===== SKY lsSKY REWARDS: EVERY RELEASE, AGAINST THE STREAM ACTIVE THAT DAY (Jake's run 2026-10-07). =====
# The measured release (189.1M over Q0) was 2.6x the declared streams as they stood (73.2M). This lists every SKY
# transfer REWARDS_DIST_LSSKY_SKY -> REWARDS_LSSKY_SKY: date, size, tx, and the vest stream config says was paying
# (issuance_schedule, rebuilt from every reset spell on 2026-10-07), plus each release's size in DAYS of that
# stream — a weekly distribute() should read ~7 days; a lump far above it would be a top-up or a migration.
def sky_lssky_releases(days: int = 120):
    import config                                          # noqa: PLC0415
    import pandas as pd                                    # noqa: PLC0415
    head(f"SKY lsSKY rewards — every distributor -> farm transfer, last {days} days, vs the declared stream")
    sky, dist, farm = ("0x56072C95FAA701256059aa122697B133aDEd9279", "0x675671A8756dDb69F7254AFB030865388Ef699Ee",
                       "0xB44C2Fb4181D7Cb06bdFf34A46FdFe4a259B40Fc")
    head_hex, _url = eth_block_number()
    if not head_hex:
        print("  UNREACHABLE — no Ethereum RPC answered eth_blockNumber.")
        return
    from_block = int(head_hex, 16) - days * 7_200
    logs, detail = explorer_logs(1, sky, [TRANSFER_TOPIC, _pad(dist), _pad(farm)], from_block)
    if logs is None:
        print(f"  UNAVAILABLE — {detail}")
        return
    print(f"  {detail}")
    steps = sorted(config.PROJECT_BY_NAME["Sky"]["issuance_schedule"]["steps"], key=lambda s: s["from"])

    def rate_on(d):
        r = None
        for s in steps:
            if d >= pd.Timestamp(s["from"]) and (not s.get("until") or d <= pd.Timestamp(s["until"])):
                r = s
        return r
    total, rows = 0.0, []
    for lg in sorted(logs, key=lambda e: int(e["blockNumber"])):
        d = pd.Timestamp(int(lg["timeStamp"]), unit="s")
        v = _hexint(lg["data"]) / 1e18
        st = rate_on(d.normalize())
        per = st["tokens_per_day"] if st else None
        rows.append((d, v, per, st["from"] if st else "-", lg.get("transactionHash", "")))
        total += v
    print(f"\n  {'when (UTC)':<20} {'SKY released':>16} {'stream from':>12} {'= days of it':>13}  tx")
    for d, v, per, frm, tx in rows:
        print(f"  {str(d)[:19]:<20} {v:>16,.0f} {frm:>12} {('%.1f' % (v / per)) if per else '-':>13}  {tx[:18]}")
    lo = pd.Timestamp.now("UTC").tz_localize(None).normalize() - pd.Timedelta(days=90)
    q0 = sum(v for d, v, *_ in rows if d >= lo)
    print(f"\n  {len(rows)} release(s), {total:,.0f} SKY over {days} days; last 90 days: {q0:,.0f} SKY")
    print("  READING IT: ~7 days of the paying stream per release = weekly distribute(); a release far larger is a")
    print("  lump (a yanked stream's unpaid balance is paid out at a reset, by design). Nothing was stored.")


# ===== GEODNET'S CANDIDATE NON-CIRCULATING WALLETS (overnight 2026-10-06, C). =====
# The three allocation wallets in config.NONCIRCULATING_CANDIDATES["GEODNET"] — seen only in search summaries of
# GEODNET's tokenomics page, so NOT subtracted. This prints, per wallet: its GEOD balance on Polygon now, its
# Transfer history over `days` (in/out, counterparties), and what our on-chain circulating would become if all
# three were excluded. Reads only. The switch is config `confirm_candidates` — Jake flips it after reading the page.
def geod_candidate_wallets(days: int = 180):
    import config                                          # noqa: PLC0415
    head(f"GEODNET — candidate non-circulating wallets (Polygon): balances, last {days} days of transfers")
    cand = config.NONCIRCULATING_CANDIDATES["GEODNET"]
    print(f"  confirm_candidates = {cand.get('confirm_candidates')} (config.NONCIRCULATING_CANDIDATES['GEODNET'])")
    head_hex = None
    for url in _rpcs_for("polygon"):
        try:
            head_hex = rpc(url, "eth_blockNumber").get("result")
            if head_hex:
                break
        except Exception:  # noqa: BLE001
            continue
    if not head_hex:
        print("  UNREACHABLE — no Polygon RPC answered eth_blockNumber.")
        return
    from_block = int(head_hex, 16) - days * 43_200          # ~2s Polygon blocks
    total = 0.0
    for a in cand["addresses"]:
        addr = a["address"]
        bal = _bal(GEOD_POLYGON, addr, "polygon")
        b = None if bal is None else bal / 1e18
        total += b or 0.0
        print(f"\n  {a['role']}\n    {addr}  ({_code(addr, 'polygon')})  balance: "
              f"{'UNREADABLE' if b is None else f'{b:,.0f} GEOD'}")
        for label, topics in (("OUT", [TRANSFER_TOPIC, _pad(addr)]), ("IN ", [TRANSFER_TOPIC, None, _pad(addr)])):
            logs, detail = explorer_logs(137, GEOD_POLYGON, topics, from_block)
            if logs is None:
                print(f"    {label}: UNAVAILABLE — {detail}")
                continue
            amt = sum(_hexint(lg["data"]) for lg in logs) / 1e18
            peers = {}
            for lg in logs:
                peer = "0x" + lg["topics"][2 if label == "OUT" else 1][-40:].lower()
                peers[peer] = peers.get(peer, 0.0) + _hexint(lg["data"]) / 1e18
            top = sorted(peers.items(), key=lambda kv: -kv[1])[:4]
            print(f"    {label}: {len(logs)} transfer(s), {amt:,.0f} GEOD"
                  + ("; top counterparties " + ", ".join(f"{p[:10]}… {v:,.0f}" for p, v in top) if top else ""))
    supply = _uint(GEOD_POLYGON, "0x18160ddd", "polygon")
    print(f"\n  the {len(cand['addresses'])} together: {total:,.0f} GEOD"
          + (f" = {total / (supply / 1e18):.1%} of Polygon totalSupply {supply / 1e18:,.0f}" if supply else ""))
    print("  Subtracted from our on-chain circulating while confirm_candidates is True (each wallet's balance on the "
          "day of the read).")
    # 1b (Jake, 2026-10-07): the wallets added since his probe, against CoinGecko at the circulating row's 5%
    pr = cand.get("probe_2026_10_07") or {}
    probed = {"0xca3e874bc4e830796d822f529c29df30302324b2", "0x486559899e96981dfe55c4e6ebf5101a76bfadfa",
              "0x82146cf0f350c241757660fd803c73313b06d75c"}
    extra = sum((_bal(GEOD_POLYGON, a["address"], "polygon") or 0) / 1e18
                for a in cand["addresses"] if a["address"].lower() not in probed)
    if pr:
        ours = pr["ours_excluding_all"] - extra
        gap = ours / pr["coingecko"] - 1
        print(f"  RESULT (approximate — the 2026-10-07 probe's {pr['ours_excluding_all']:,.0f} less today's balance of the "
              f"wallets added since, {extra:,.0f}): ours ~{ours:,.0f} vs CoinGecko {pr['coingecko']:,.0f} = {gap:+.1%} "
              f"-> {'WITHIN' if abs(gap) <= 0.05 else 'OUTSIDE'} the circulating row's 5%. The run's Credibility tab "
              f"has the exact figure; within 5% -> set CIRCULATING_ONCHAIN['GEODNET']['ratios_use'] = 'onchain'.")
    print("  READING IT: a vesting / allocation wallet sends out in steps to a few recipients (exchanges, OTC);")
    print("  one that never moves is locked in practice. Tokens moved to Solana after the migration are NOT here.")
    print("  Nothing was stored. PASTE BACK the section.")


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
    dup_wei = sum(_hexint(x["data"]) for v in dups.values() for x in v[1:])
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
        by[f] = (n + 1, w + _hexint(e["data"]), txs)
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
    # EVERY LATE EVENT, NOT JUST THE COUNT. A residual sender is only "immaterial" once its
    # amount is on the page; the count alone leaves that as an assumption. Added 2026-09-25 for
    # 0xe751bf33164b8786c71d59c48f668d22408e142d (3 burns, not in Sky's spell address registry).
    other_total = total - by.get(proxy, (0, 0, set()))[1]
    for f, _ in late:
        c, _ = _code_at(f)
        kind = "?" if c is None else ("CONTRACT" if len(c) > 2 else "EOA")
        mine = sorted((e for e in clean if "0x" + e["topics"][1][-40:].lower() == f),
                      key=lambda e: e["blockNumber"])
        amt = sum(_hexint(e["data"]) for e in mine)
        print(f"\n  LATE SENDER {f} — {kind}, {len(mine)} burn(s), total {amt / 1e18:,.6f} SKY "
              f"({(amt / other_total if other_total else 0):.8%} of everything-else)")
        for e in mine:
            when = (time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(e["timeStamp"]))
                    if e.get("timeStamp") else block_time(hex(e["blockNumber"])))
            print(f"    block {e['blockNumber']:>11,}  {when}  {int(e['data'], 16) / 1e18:>20,.6f} SKY  "
                  f"tx {e['transactionHash']}")

    print("\n  LARGEST SINGLE EVENTS")
    for e in sorted(clean, key=lambda e: -_hexint(e["data"]))[:10]:
        print(f"    block {e['blockNumber']:>11,}  {int(e['data'], 16) / 1e18:>20,.2f} SKY  "
              f"from 0x{e['topics'][1][-40:]}  tx {e['transactionHash']}")

    # ===== 3. THE SUPPLY IDENTITY =====
    mints = scan([TRANSFER_TOPIC, zero, None], "mints Transfer(0x0, *)")
    word, _ = eth_call(token, SEL_TOTAL_SUPPLY, hex(head_blk))
    if mints is None or word is None:
        print("\n3. SUPPLY IDENTITY: not computed — the mint scan or totalSupply() did not answer.")
    else:
        minted = sum(_hexint(e["data"]) for e in
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


# =====================================================================================
# SECOND PASS PROBES (Jake, 2026-09-30). Each reads only; each prints what to paste back.
# =====================================================================================
def _ua():
    from fetch.base import USER_AGENT                     # noqa: PLC0415
    return {"User-Agent": USER_AGENT}


def robots_and_terms():
    """The licensing register (config.SOURCE_REGISTER): every declared path through the
    pipeline's own RFC 9309 reader, and each terms page's HTTP status. Paste the lines back and
    the register's 'NOT CHECKED FROM HERE' entries are filled from them."""
    import config                                          # noqa: PLC0415
    from fetch.scrape import robots_verdict                # noqa: PLC0415
    head("LICENSING REGISTER — robots.txt per path, terms page status")
    for host, e in config.SOURCE_REGISTER.items():
        for path in e["paths"]:
            url = f"https://{host}{path}"
            try:
                ok, why = robots_verdict(url)
                print(f"  {host}{path:<45} robots: {'ALLOWED' if ok else 'DISALLOWED'} — {why}")
            except Exception as ex:  # noqa: BLE001
                print(f"  {host}{path:<45} robots: UNREADABLE — {ex}")
        t = (e.get("terms") or {}).get("url")
        if t and e.get("browser_only"):
            print(f"      terms: not fetched — browser-only source ({e.get('access', '')[:80]})")
        elif t:
            try:
                r = requests.get(t, headers=_ua(), timeout=TIMEOUT)
                print(f"      terms {t}: HTTP {r.status_code}, {len(r.text):,} chars")
            except Exception as ex:  # noqa: BLE001
                print(f"      terms {t}: UNREACHABLE — {ex}")
    print("  PASTE BACK every line. Read each terms page yourself for redistribution limits.")


def ultrasound_history():
    """A1/A3: ultrasound.money's supply-projection-inputs — the keys, each series' length and
    ends, and d(supply) over the last week (issuance - burn, ETH/day)."""
    from fetch.scrape import robots_verdict                # noqa: PLC0415
    url = "https://ultrasound.money/api/v2/fees/supply-projection-inputs"
    head("ETHEREUM — ultrasound.money supplyByDay / inBeaconValidatorsByDay")
    ok, why = robots_verdict(url)
    print(f"  robots for the API path: {'ALLOWED' if ok else 'DISALLOWED'} — {why}")
    if not ok:
        return
    try:
        j = requests.get(url, headers=_ua(), timeout=TIMEOUT).json()
    except Exception as e:  # noqa: BLE001
        print(f"  UNREACHABLE — {e}")
        return
    print(f"  keys: {sorted(j) if isinstance(j, dict) else type(j).__name__}")
    for k in ("supplyByDay", "inBeaconValidatorsByDay"):
        rows = j.get(k) if isinstance(j, dict) else None
        if not isinstance(rows, list) or not rows:
            print(f"  {k}: MISSING or empty")
            continue
        f, l = rows[0], rows[-1]
        print(f"  {k}: {len(rows)} rows, first {f}, last {l}")
        if k == "supplyByDay":
            last = rows[-8:]
            for a, b in zip(last, last[1:]):
                print(f"    {time.strftime('%Y-%m-%d', time.gmtime(b['t']))}  d(supply) "
                      f"{b['v'] - a['v']:+,.1f} ETH")
    print("  PASTE BACK: the keys line and both series lines (d(supply) + ~burn should be ~2,700/day).")


def hyperliquid_history_routes():
    """C: tokenDetails (circulating + the non-circulating list), validatorSummaries total stake,
    the stats cloudfront feed's freshness, and HyperEVM's Blockscout (hyperscan.com) stats."""
    head("HYPERLIQUID — first-party circulating, stake, stats feed, HyperEVM explorer")
    info = "https://api.hyperliquid.xyz/info"
    try:
        td = requests.post(info, json={"type": "tokenDetails", "tokenId": "0x0d01dc56dcaaca66ad901c959b4011ec"},
                           headers=_ua(), timeout=TIMEOUT).json()
        print(f"  tokenDetails keys: {sorted(td)[:20]}")
        print(f"  circulatingSupply {td.get('circulatingSupply')}  totalSupply {td.get('totalSupply')}")
        for a, b in (td.get("nonCirculatingUserBalances") or [])[:20]:
            print(f"    non-circulating {a} {b}")
    except Exception as e:  # noqa: BLE001
        print(f"  tokenDetails UNREACHABLE — {e}")
    try:
        vs = requests.post(info, json={"type": "validatorSummaries"}, headers=_ua(), timeout=TIMEOUT).json()
        print(f"  validatorSummaries: {len(vs)} validators, total stake {sum(int(v.get('stake', 0)) for v in vs) / 1e8:,.0f} HYPE")
    except Exception as e:  # noqa: BLE001
        print(f"  validatorSummaries UNREACHABLE — {e}")
    for name in ("daily_unique_users", "daily_trades"):
        url = f"https://d2v1fiwobg9w6.cloudfront.net/{name}"
        try:
            j = requests.get(url, headers=_ua(), timeout=TIMEOUT).json()
            rows = j.get("chart_data") or j.get("table_data") or j
            print(f"  stats {name}: {len(rows)} rows, last {rows[-1] if rows else None}")
        except Exception as e:  # noqa: BLE001
            print(f"  stats {name} UNREACHABLE — {e}")
    for path in ("/api/v2/stats", "/api/v2/stats/charts/transactions"):
        try:
            j = requests.get(f"https://www.hyperscan.com{path}", headers=_ua(), timeout=TIMEOUT).json()
            print(f"  hyperscan{path}: keys {sorted(j)[:12] if isinstance(j, dict) else type(j).__name__}")
        except Exception as e:  # noqa: BLE001
            print(f"  hyperscan{path} UNREACHABLE — {e}")
    # (b) DefiLlama's /protocol staking series — the field name decides the wiring
    # (Jake's probes2, 2026-09-30: Arbitrum and Hyperliquid L1 only — no staking series).
    try:
        j = requests.get("https://api.llama.fi/protocol/hyperliquid", headers=_ua(), timeout=TIMEOUT).json()
        ct = j.get("chainTvls") or {}
        print(f"  llama /protocol/hyperliquid: top keys {sorted(j)[:25]}")
        print(f"    chainTvls keys: {sorted(ct)}")
        for k in sorted(ct):
            if "staking" in k.lower():
                toks = ct[k].get("tokens") or []
                tvl = ct[k].get("tvl") or []
                print(f"    {k}: tvl {len(tvl)} pts {tvl[:1]}..{tvl[-1:]}; tokens {len(toks)} pts, "
                      f"last {toks[-1:] if toks else None}")
        for k in ("currentChainTvls", "otherProtocols", "parentProtocol"):
            if k in j:
                print(f"    {k}: {str(j[k])[:200]}")
    except Exception as e:  # noqa: BLE001
        print(f"  llama /protocol/hyperliquid UNREACHABLE — {e}")
    print("  PASTE BACK all lines: the stats feed's last date says whether it is still updated;\n"
          "  the chainTvls keys say whether DefiLlama carries a HYPE staking series.")


def hyperevm_etherscan():
    """3c (2026-09-30): does the existing ETHERSCAN_API_KEY serve HyperEVM (chainid 999) through
    Etherscan V2 — a plain call, then the daily-stats calls a tx/address series needs? Whatever
    answers measures HyperEVM ONLY (not HyperCore, where the trading is). Key never printed."""
    head("HYPEREVM — Etherscan V2 chainid 999 with the existing key")
    key = os.environ.get("ETHERSCAN_API_KEY", "").strip()
    if not key:
        print("  ETHERSCAN_API_KEY is not set — nothing to test")
        return
    base = "https://api.etherscan.io/v2/api"
    day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 3 * 86400))
    calls = (("eth_blockNumber", {"module": "proxy", "action": "eth_blockNumber"}),
             ("dailytx", {"module": "stats", "action": "dailytx", "startdate": day, "enddate": day, "sort": "asc"}),
             ("dailynewaddress", {"module": "stats", "action": "dailynewaddress", "startdate": day,
                                  "enddate": day, "sort": "asc"}),
             ("dailyavgblocktime", {"module": "stats", "action": "dailyavgblocktime", "startdate": day,
                                    "enddate": day, "sort": "asc"}))
    for label, params in calls:
        try:
            r = requests.get(base, params={"chainid": 999, **params, "apikey": key}, headers=_ua(), timeout=TIMEOUT)
            j = r.json()
            res = j.get("result")
            print(f"  {label}: HTTP {r.status_code} status={j.get('status')!r} message={j.get('message')!r} "
                  f"result={str(res)[:160]!r}")
        except Exception as e:  # noqa: BLE001
            print(f"  {label} UNREACHABLE — {e}")
    print("  PASTE BACK all lines. 'NOTOK'/'API Pro endpoint' on dailytx = the free key does not serve it.")


def plume_sources():
    """G: the staking diamond (reward rate -> APR, total staked), Plume's Blockscout stats host
    (from the explorer's own envs.js), and growthepie's fees_paid_usd for Plume."""
    head("PLUME — staking APR, explorer stats service, growthepie fees")
    from eth_utils import keccak                          # noqa: PLC0415
    import config                                          # noqa: PLC0415
    # probes3 (2026-09-30): the LIVE diamond, confirmed — every read below is against it. The APR
    # line used to read 0xCF8B, the deploy script's test diamond.
    diamond = config.PROJECT_BY_NAME["Plume"]["plume_staking"]["address"]
    test_diamond = "0xCF8B97260F77c11d58542644c5fD1D5F93FdA57d"
    print(f"  live diamond {diamond}")
    native = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"
    sel_rate = "0x" + keccak(text="getRewardRate(address)").hex()[:8] + native[2:].lower().rjust(64, "0")
    sel_staked = "0x" + keccak(text="totalAmountStaked()").hex()[:8]
    for label, data in (("getRewardRate(PLUME)", sel_rate), ("totalAmountStaked()", sel_staked)):
        try:
            j = rpc("https://rpc.plume.org", "eth_call", [{"to": diamond, "data": data}, "latest"])
            v = int(j.get("result") or "0x0", 16)
            extra = (f" -> gross APR {v * 31_536_000 / 1e18:.4%}" if label.startswith("getRewardRate")
                     else f" -> {v / 1e18:,.0f} PLUME")
            print(f"  {label}: {v}{extra}")
        except Exception as e:  # noqa: BLE001
            print(f"  {label} UNREACHABLE — {e}")
    # probes2 (2026-09-30): WHICH DIAMOND IS LIVE? The myPLUME feed stores the diamond live staking
    # uses; each candidate's total; and validator 1's L1 address on 0xCF8B — "l1val_1_placeholder"
    # means the deploy script's placeholder set.
    try:
        from web3 import Web3                              # noqa: PLC0415
        w3 = Web3(Web3.HTTPProvider("https://rpc.plume.org", request_kwargs={"timeout": 25}))
        cs = Web3.to_checksum_address
        feed = w3.eth.contract(address=cs("0xFbb53aa72c10680e822e255aC70D10f8bb957D64"), abi=[
            {"inputs": [], "name": n, "outputs": [{"name": "", "type": "address"}], "stateMutability": "view",
             "type": "function"} for n in ("plumeStaking", "stPlumeMinter")])
        for n in ("plumeStaking", "stPlumeMinter"):
            try:
                print(f"  myPLUME feed {n}() = {getattr(feed.functions, n)().call()}")
            except Exception as e:  # noqa: BLE001
                print(f"  myPLUME feed {n}() failed — {e}")
        tot_abi = [{"inputs": [], "name": "totalAmountStaked", "outputs": [{"name": "", "type": "uint256"}],
                    "stateMutability": "view", "type": "function"},
                   {"inputs": [], "name": "getCooldownInterval", "outputs": [{"name": "", "type": "uint256"}],
                    "stateMutability": "view", "type": "function"}]
        for cand in (diamond, test_diamond, "0xA20bfe49969D4a0E9abfdb6a46FeD777304ba07f"):
            c = w3.eth.contract(address=cs(cand), abi=tot_abi)
            try:
                tot = c.functions.totalAmountStaked().call()
                cd = c.functions.getCooldownInterval().call()
                app = 134_100_000                     # staking.plume.org, Jake 2026-09-30
                print(f"  {cand}: totalAmountStaked {tot / 1e18:,.2f} PLUME ({tot / 1e18 / app - 1:+.2%} vs the "
                      f"app's 134.1M; live if within ±0.5%), cooldown {cd:,}s")
            except Exception as e:  # noqa: BLE001
                print(f"  {cand}: {e}")
        # getValidatorInfo(uint16) did not decode on the live diamond (probes3): ValidatorInfo's field
        # ORDER changed in d408f63 under the same selector. Word 1 of the raw answer settles which
        # order is deployed: 0/1 = the new order (bool active), ~1e16-5e17 = the old (commission).
        raw = rpc("https://rpc.plume.org", "eth_call", [{"to": diamond, "data": "0x" + keccak(
            text="getValidatorInfo(uint16)").hex()[:8] + "1".rjust(64, "0")}, "latest"])
        words = [(raw.get("result") or "0x")[2:][i:i + 64] for i in range(0, 64 * 4, 64)]
        if words and all(words):
            w = int(words[1 + (int(words[0], 16) == 32)], 16)       # skip a leading tuple offset
            print(f"  {diamond} getValidatorInfo(1) raw word 1 after the offset = {w} -> "
                  f"{'NEW field order (d408f63+)' if w in (0, 1) else 'OLD field order (pre-d408f63)'}")
        else:
            print(f"  {diamond} getValidatorInfo(1) raw: {str(raw)[:200]}")
    except Exception as e:  # noqa: BLE001
        print(f"  live-diamond checks UNREACHABLE — {e}")
    # probes3: what the adapter stores, read exactly as it reads it (fetch/plume_staking._read) —
    # per-validator active flags from getValidatorStats, commission over ACTIVE validators.
    try:
        from fetch.plume_staking import PlumeStaking       # noqa: PLC0415
        spec = config.PROJECT_BY_NAME["Plume"]["plume_staking"]
        g = PlumeStaking()._read(spec, diamond)
        if isinstance(g, str):
            print(f"  adapter read: {g}")
        else:
            comm = "n/a" if g["commission"] is None else f"{g['commission']:.2%}"
            net = "n/a" if g["net_factor"] is None else f"{g['apr'] * g['net_factor']:.4%}"
            print(f"  adapter read of {diamond}: totalAmountStaked {g['aggregate']:,.2f} PLUME; "
                  f"{len(g['rows'])} validators summing {g['total']:,.2f}; {g['n_active']} active "
                  f"({g['active_share']:.2%} of stake); gross APR {g['apr']:.4%}; commission {comm}; "
                  f"net APR {net}; cooldown {g['cooldown']} s")
            for v in sorted(g["rows"], key=lambda x: -x[1])[:10]:
                print(f"    id {v[0]}: {v[1] / 1e18:,.2f} PLUME, commission {v[2] / 1e18:.2%}, active {v[3]}")
    except Exception as e:  # noqa: BLE001
        print(f"  adapter read UNREACHABLE — {e}")
    try:
        env = requests.get("https://explorer.plume.org/assets/envs.js", headers=_ua(), timeout=TIMEOUT).text
        for k in ("NEXT_PUBLIC_STATS_API_HOST", "NEXT_PUBLIC_STATS_API_BASE_PATH", "NEXT_PUBLIC_API_HOST"):
            i = env.find(k)
            print(f"  {k}: {env[i:i + 120].split(',')[0] if i >= 0 else 'absent'}")
    except Exception as e:  # noqa: BLE001
        print(f"  envs.js UNREACHABLE — {e}")
    try:
        rows = requests.get("https://api.growthepie.com/v1/fundamentals.json", headers=_ua(), timeout=TIMEOUT).json()
        fees = sorted((r["date"], r["value"]) for r in rows
                      if r.get("origin_key") == "plume" and r.get("metric_key") == "fees_paid_usd")
        print(f"  growthepie plume fees_paid_usd: {len(fees)} days, last {fees[-3:]}")
    except Exception as e:  # noqa: BLE001
        print(f"  growthepie UNREACHABLE — {e}")
    print("  PASTE BACK all lines, and the TOTAL STAKED staking.plume.org shows: nothing is stored until one\n"
          "  diamond is confirmed live and its stake matches the app's figure.")


def settlement_rebuild_coverage():
    """Jake 2026-09-30 (settlement volume for NEAR / Hyperliquid / Plume): BEFORE any transfer scan is
    wired, the coverage per chain and the call volume a year of history would cost, from MEASURED
    daily transfer counts — and how far the free sources get on Ethereum against Artemis's own
    August 2026 figure (the validation that decides whether a rebuild can be trusted). Reads only;
    keys never printed. config.SETTLEMENT_REBUILD holds the method and routes."""
    import pandas as pd                                    # noqa: PLC0415
    import config                                          # noqa: PLC0415
    head("SETTLEMENT REBUILD — Ethereum validation, then measured call volume for Plume and NEAR")
    # 1. ETHEREUM: DefiLlama DEX (+ NFT if it answers) for August 2026 beside Artemis's August total
    m0, m1 = pd.Timestamp("2026-08-01"), pd.Timestamp("2026-08-31")

    def month_sum(path):
        try:
            j = requests.get(f"https://api.llama.fi{path}", params={"excludeTotalDataChart": "false",
                             "excludeTotalDataChartBreakdown": "true"}, headers=_ua(), timeout=TIMEOUT)
            if not j.ok:
                return None, f"HTTP {j.status_code}"
            pts = [(pd.Timestamp(int(t), unit="s"), float(v)) for t, v in j.json().get("totalDataChart") or []]
            got = [v for d, v in pts if m0 <= d <= m1]
            return (sum(got), f"{len(got)} day(s)") if got else (None, "no August days")
        except Exception as e:  # noqa: BLE001
            return None, str(e)[:120]
    dex, dex_n = month_sum("/overview/dexs/ethereum")
    nft, nft_n = month_sum("/overview/nft-volume/ethereum")       # path UNVERIFIED
    art = None
    try:
        from fetch.artemis import ArtemisCSV, parse         # noqa: PLC0415
        f = ArtemisCSV()._find("Ethereum")
        got = parse(f, "Ethereum") if f else "no export found"
        if not isinstance(got, str):
            vals = [v for d, v in got[0] if m0 <= d <= m1]
            art = sum(vals) if len(vals) == 31 else None
    except Exception as e:  # noqa: BLE001
        print(f"  Artemis CSV: {e}")
    fmt = lambda v: "n/a" if v is None else f"${v / 1e9:,.1f}bn"          # noqa: E731
    print(f"  ETHEREUM, August 2026: Artemis settlement {fmt(art)}; DefiLlama DEX {fmt(dex)} ({dex_n}); "
          f"DefiLlama NFT {fmt(nft)} ({nft_n}, path unverified)")
    if art and dex:
        rest = art - dex - (nft or 0)
        print(f"  DEX{' + NFT' if nft else ''} = {(dex + (nft or 0)) / art:.1%} of Artemis; the P2P leg would have "
              f"to supply {fmt(rest)} ({rest / art:.1%}). NO FREE P2P VALUE SERIES EXISTS (Coin Metrics' free "
              f"data has TxTfrCnt only) — the rebuild cannot be validated on Ethereum from free sources.")
    # 2. PLUME: measured daily ERC-20 transfer rate (Blockscout v2, newest pages) and native transfers
    base = "https://explorer.plume.org"
    try:
        rows, params = [], {"type": "ERC-20"}
        for _ in range(5):
            j = requests.get(f"{base}/api/v2/token-transfers", params=params, headers=_ua(), timeout=TIMEOUT).json()
            rows += j.get("items") or []
            params = {"type": "ERC-20", **(j.get("next_page_params") or {})}
            if not j.get("next_page_params"):
                break
        ts = sorted(pd.Timestamp(r["timestamp"]) for r in rows if r.get("timestamp"))
        span = (ts[-1] - ts[0]).total_seconds() if len(ts) > 1 else 0
        n_day = len(ts) / span * 86_400 if span else None
        eoa = sum(1 for r in rows if not (r.get("from") or {}).get("is_contract")
                  and not (r.get("to") or {}).get("is_contract"))
        print(f"\n  PLUME: {len(ts)} newest ERC-20 transfers span {span / 3600:.1f} h -> ~{n_day or 0:,.0f} a day; "
              f"{eoa} of {len(rows)} are EOA-to-EOA (is_contract false on both sides)")
        if n_day:
            print(f"    a year on /api/v2/token-transfers (50 a page): ~{365 * n_day / 50:,.0f} calls "
                  f"(~{365 * n_day / 50 / 300 / 60:,.0f} h at 300/min); on the CSV export (10,000 rows a call): "
                  f"~{365 * n_day / 10_000:,.0f} calls + an is_contract lookup per new address")
    except Exception as e:  # noqa: BLE001
        print(f"  PLUME Blockscout: {e}")
    try:
        lines = requests.get(f"{base}/stats-service/api/v1/lines", headers=_ua(), timeout=TIMEOUT).json()
        names = [c.get("id") for sec in lines.get("sections") or [] for c in sec.get("charts") or []]
        print(f"    stats-service charts: {names}")
    except Exception as e:  # noqa: BLE001
        print(f"    stats-service lines: {e}")
    # 3. NEAR: measured FT transfer rate (NearBlocks, keyed) and the plan budget it would use
    key = os.environ.get("NEARBLOCKS_API_KEY", "").strip()
    hdr = {**_ua(), **({"Authorization": f"Bearer {key}"} if key else {})}
    try:
        j = requests.get("https://api.nearblocks.io/v1/fts/txns", params={"per_page": 25}, headers=hdr,
                         timeout=TIMEOUT).json()
        txs = j.get("txns") or []
        ts = sorted(int(t.get("block_timestamp") or 0) / 1e9 for t in txs if t.get("block_timestamp"))
        span = ts[-1] - ts[0] if len(ts) > 1 else 0
        n_day = len(ts) / span * 86_400 if span else None
        c = requests.get("https://api.nearblocks.io/v1/fts/txns/count", headers=hdr, timeout=TIMEOUT).json()
        print(f"\n  NEAR: {len(ts)} newest FT transfers span {span / 60:.1f} min -> ~{n_day or 0:,.0f} a day; "
              f"lifetime count {c}")
        if n_day:
            print(f"    a year on /v3/fts/txns (100 a page): ~{365 * n_day / 100:,.0f} calls, ~{365 * n_day / 25:,.0f} "
                  f"credits — the keyed default plan allows 3,666 calls a day / 110,000 a month "
                  f"(~{365 * n_day / 100 / 3_666:,.0f} days of quota)")
    except Exception as e:  # noqa: BLE001
        print(f"  NEAR NearBlocks: {str(e).split('?')[0]}")
    print("\n  HYPERLIQUID: not rebuildable (config.UNAVAILABLE) — HyperCore transfers are per-user only.")
    print("  PASTE BACK. Nothing is wired until you approve a route and its call volume.")


def near_activity_break():
    """Jake 2026-10-01: NEAR's BigQuery rows fell ~80% from 2026-03 to 2026-04 (receipt_actions 516.1M ->
    104.3M; ft_events 191.0M -> 47.3M). Real activity, or the pipeline? Daily row counts per table from
    INFORMATION_SCHEMA.PARTITIONS (metadata: tens of MB, capped at 200 MB) for 2026-03-01..2026-04-30,
    beside NearBlocks' own daily transaction count from metrics.db (an independent indexer). READ: if
    blocks/day hold steady while transactions fall in BOTH sources, activity really fell (a campaign or
    programme ended — the day it fell names the candidate); if BigQuery falls and NearBlocks does not,
    the dataset records less since then (NEAR Lake, its documented source, was deprecated 2026-03-24)."""
    import sqlite3                                         # noqa: PLC0415
    import pandas as pd                                    # noqa: PLC0415
    import config                                          # noqa: PLC0415
    from fetch.near_bigquery import NearBigQuery           # noqa: PLC0415
    head("NEAR — the 2026-03 -> 2026-04 drop: BigQuery rows per day vs NearBlocks transactions")
    spec = config.PROJECT_BY_NAME["Near"]["near_bigquery"]
    nb = NearBigQuery()
    client, why = nb._client(spec)
    sql = ("SELECT table_name, PARSE_DATE('%Y%m%d', partition_id) AS day, total_rows "
           "FROM `bigquery-public-data.crypto_near_mainnet_us`.INFORMATION_SCHEMA.PARTITIONS "
           "WHERE table_name IN ('blocks', 'chunks', 'transactions', 'receipt_actions', 'ft_events') "
           "AND partition_id BETWEEN '20260301' AND '20260430' ORDER BY day, table_name")
    if client is None:
        print(f"  not run here: {why}. In the console (near-data-510309) run:\n  {sql}")
        bq = None
    else:
        from google.cloud import bigquery                  # noqa: PLC0415
        nb._guard_sql(sql, spec)
        rows = [dict(r.items()) for r in client.query(sql, job_config=bigquery.QueryJobConfig(
            maximum_bytes_billed=200 * 1024 ** 2)).result()]
        bq = pd.DataFrame(rows).pivot_table(index="day", columns="table_name", values="total_rows", aggfunc="sum")
    try:
        nbk = pd.read_sql_query("SELECT date, value FROM metrics WHERE project='Near' AND metric='tx_count' "
                                "AND date BETWEEN '2026-03-01' AND '2026-04-30'", sqlite3.connect("metrics.db"))
        nbk = nbk.assign(date=pd.to_datetime(nbk["date"]).dt.date).groupby("date")["value"].last()
    except Exception as e:  # noqa: BLE001
        print(f"  NearBlocks tx_count from metrics.db: {e}")
        nbk = pd.Series(dtype=float)
    if len(nbk) < 40:
        # metrics.db holds only what the daily run kept (100 days a call), so March-April may be
        # missing. ONE txn-stats call reaching back to 2026-03-01 (free API; NearBlocks bills
        # ceil(limit / 25) credits at most, ~9 of the 333 a day; limit <= 365 per nb-schemas
        # packages/nb-schemas/src/stats/request.ts, read 2026-10-01). Key in the header, never printed.
        from fetch.nearblocks import NearBlocks                # noqa: PLC0415
        nspec = config.PROJECT_BY_NAME["Near"]["nearblocks"]
        nbl = NearBlocks()
        key = nbl._key(nspec)
        limit = int((pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() - pd.Timestamp("2026-03-01")).days) + 2
        if not key:
            print(f"  NearBlocks: no {nspec['key_env']} in .env — its side of the comparison is missing")
        else:
            try:
                body = nbl._get(nspec["base_url"].rstrip("/") + "/v3/txn-stats", {"limit": limit}, key)
                got = {pd.Timestamp(r["date"]).date(): float(r["txns"]) for r in (body or {}).get("data") or []
                       if r.get("date") and r.get("txns") is not None}
                got = {d: v for d, v in got.items() if pd.Timestamp("2026-03-01").date() <= d
                       <= pd.Timestamp("2026-04-30").date()}
                print(f"  NearBlocks txn-stats (limit {limit}, one call): {len(got)} day(s) in 2026-03..04")
                nbk = pd.Series(got, dtype=float).sort_index() if got else nbk
            except Exception as e:  # noqa: BLE001
                print(f"  NearBlocks txn-stats: {nbl._scrub(nspec, e)}")
    if bq is None and nbk.empty:
        return
    days = sorted(set(bq.index if bq is not None else []) | set(nbk.index))
    cols = ["blocks", "chunks", "transactions", "receipt_actions", "ft_events"]
    print("  day          " + "  ".join(f"{c[:12]:>12}" for c in cols) + "  nearblocks_tx  bq_tx/nb_tx")
    for d in days:
        vals = [bq.loc[d, c] if bq is not None and d in bq.index and c in bq.columns else None for c in cols]
        nt = nbk.get(d)
        ratio = f"{vals[2] / nt:>11.2f}" if (vals[2] and nt) else f"{'-':>11}"
        cells = "  ".join(f"{v:>12,.0f}" if v is not None else f"{'-':>12}" for v in vals)
        ntxt = f"{nt:>13,.0f}" if nt is not None else f"{'-':>13}"
        print(f"  {d}  {cells}  {ntxt}  {ratio}")
    # THE VERDICT (Jake, 2026-10-01): mean daily transactions 2026-03-01..23 against 2026-04-02..30, in
    # BOTH sources. BigQuery fell (< 0.5x) and NearBlocks held (> 0.8x) -> PIPELINE: every BigQuery NEAR
    # figure is marked SUSPECT on its cell. Both fell -> REAL: windows may span it. Otherwise UNCLEAR.
    def ratio(series):
        if series is None or len(series) == 0:
            return None
        s_ = pd.Series(series).dropna()
        s_.index = pd.to_datetime(s_.index)
        before = s_[(s_.index >= "2026-03-01") & (s_.index <= "2026-03-23")]
        after = s_[(s_.index >= "2026-04-02") & (s_.index <= "2026-04-30")]
        return float(after.mean() / before.mean()) if len(before) and len(after) and before.mean() else None
    bq_r = ratio(bq["transactions"]) if bq is not None and "transactions" in bq.columns else None
    nb_r = ratio(nbk) if not nbk.empty else None
    if bq_r is None or nb_r is None:
        verdict = "UNDETERMINED"
        print(f"\n  VERDICT: UNDETERMINED — needs both series (BigQuery x{bq_r}, NearBlocks x{nb_r}); nothing saved.")
    else:
        verdict = ("PIPELINE" if bq_r < 0.5 and nb_r > 0.8 else "REAL" if bq_r < 0.5 and nb_r < 0.5 else "UNCLEAR")
        print(f"\n  mean daily transactions, Apr 2-30 / Mar 1-23: BigQuery x{bq_r:.2f}, NearBlocks x{nb_r:.2f}")
        print("  VERDICT: " + {
            "PIPELINE": "PIPELINE — BigQuery fell and NearBlocks did not: the dataset records less since NEAR Lake's "
                        "deprecation. Every BigQuery-derived NEAR figure is now marked SUSPECT on its cell.",
            "REAL": "REAL — both indexers fell: NEAR activity really dropped. Windows may span the break; "
                    "pre-April backfill can be reconsidered (config Near.near_bigquery.backfill_floor).",
            "UNCLEAR": "UNCLEAR — neither pattern is clean; nothing changes. Paste back for a closer look.",
        }[verdict])
    if verdict != "UNDETERMINED":
        # an undetermined run (a source missing) never overwrites a verdict an earlier run computed
        from fetch.logcache import LogCache                # noqa: PLC0415
        b = config.SERIES_BREAKS[("Near", "settlement_volume_usd")]
        LogCache().root.mkdir(parents=True, exist_ok=True)
        (LogCache().root / b["verdict_file"]).write_text(json.dumps({
            "verdict": verdict, "bq_ratio": bq_r, "nb_ratio": nb_r,
            "computed_on": str(pd.Timestamp.now(tz="UTC").date())}))
        print(f"  saved to {LogCache().root / b['verdict_file']} — the workbook reads it on its next build.")
    print("  PASTE BACK all lines.")


def near_settlement_routes():
    """Jake 2026-10-01: NEAR from Google's public BigQuery dataset — LIVE (MAX(block_date) 2026-10-01).
    Dune (paid plan to save a query) and Flipside (API shut 2025-07-31) are CLOSED.
    With Jake's Application Default Credentials (RUNBOOK 11n): runs sql/near/bigquery_metadata.sql
    (partitioning, clustering, a year's rows per month, the two schemas — ~30 MB billed, the 10 MB
    INFORMATION_SCHEMA minimum x3) and DRY-RUNS every heavy query (free): the P2P year backfill and
    one day's top-up, the token census, the buyback-wallet balances. Without credentials: writes each
    query with literal dates to data/near/console/ — paste into console.cloud.google.com/bigquery
    (project near-data-510309) and the editor shows "This query will process N GB" before running
    anything. Credentials are never printed; every query is pinned to near-data-510309."""
    import re as _re                                       # noqa: PLC0415
    from pathlib import Path                               # noqa: PLC0415
    import pandas as pd                                    # noqa: PLC0415
    import config                                          # noqa: PLC0415
    from fetch.near_bigquery import NearBigQuery           # noqa: PLC0415
    head("NEAR / BigQuery — layout, schemas and dry-run bytes (nothing heavy is run)")
    spec = config.PROJECT_BY_NAME["Near"]["near_bigquery"]
    here = Path(__file__).resolve().parent / "sql" / "near"
    yday = (pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() - pd.Timedelta(days=1))
    year0 = yday - pd.Timedelta(days=int(spec["days"]) - 1)
    toks = list(spec["seed_tokens"])
    runs = {
        "p2p_year": ("bigquery_p2p_daily.sql", {"d0": year0.date(), "d1": yday.date(), "tokens": toks}),
        "p2p_one_day": ("bigquery_p2p_daily.sql", {"d0": yday.date(), "d1": yday.date(), "tokens": toks}),
        "token_census_30d": ("bigquery_token_census.sql", {"d0": (yday - pd.Timedelta(days=29)).date(), "d1": yday.date()}),
        "buyback_wallet_balances_year": ("bigquery_buyback_wallet_balances.sql", {"d0": year0.date(), "d1": yday.date()}),
        "circulating_full_history": ("bigquery_circulating_supply.sql", {"since": "2000-01-01"}),
    }
    nb = NearBigQuery()
    client, why = nb._client(spec)
    if client is None:
        out_dir = Path(__file__).resolve().parent / "data" / "near" / "console"
        out_dir.mkdir(parents=True, exist_ok=True)

        def lit(v):
            if isinstance(v, (list, tuple)):
                return "[" + ", ".join(f"'{x}'" for x in v) + "]"
            return f"DATE '{v}'"
        for label, (fn, params) in runs.items():
            sql = (here / fn).read_text()
            for k, v in params.items():
                sql = _re.sub(rf"@{k}\b", lit(v), sql)
            (out_dir / f"{label}.sql").write_text(sql)
        print(f"  not run here: {why}.")
        print(f"  Console route: open each file in {out_dir} in console.cloud.google.com/bigquery (project\n"
              f"  near-data-510309); the editor shows 'This query will process N' BEFORE you run — that figure\n"
              f"  is the dry run, and costs nothing. Also paste sql/near/bigquery_metadata.sql and run it (~30 MB).\n"
              f"  PASTE BACK: each file's bytes, and the metadata results.")
        return
    from google.cloud import bigquery                      # noqa: PLC0415
    # COMMENTS OUT BEFORE SPLITTING (Jake's run 2026-10-01: "Syntax error: Unexpected identifier
    # circulating_supply at [1:2]") — a ';' inside a comment split the file mid-sentence.
    code = "\n".join(line.split("--", 1)[0] for line in (here / "bigquery_metadata.sql").read_text().splitlines())
    for stmt in [x for x in code.split(";") if "SELECT" in x]:
        try:
            nb._guard_sql(stmt, spec)
            for row in client.query(stmt, job_config=bigquery.QueryJobConfig(
                    maximum_bytes_billed=200 * 1024 ** 2)).result():
                print(f"  {dict(row.items())}")
        except Exception as e:  # noqa: BLE001
            print(f"  metadata query refused: {type(e).__name__}: {str(e)[:200]}")
        print("  --")
    measured = {}
    for label, (fn, params) in runs.items():
        try:
            b = nb._dry(client, (here / fn).read_text(), params, spec)
            measured[label] = b
            print(f"  DRY RUN {label}: {b / 1e9:,.2f} GB ({b / 1e12:,.3f} TB) — free; nothing billed")
        except Exception as e:  # noqa: BLE001
            print(f"  DRY RUN {label}: {type(e).__name__}: {str(e)[:200]}")
    # THE REAL SIZES FEED THE TOP-UP RESERVE (Jake, 2026-10-01): saved where the adapter reads them,
    # so the first seed reserves the month's top-ups from measured figures, not estimates.
    st = nb._load()
    day = str(pd.Timestamp.now(tz="UTC").date())
    if "p2p_one_day" in measured:
        st["topup_bytes"] = {"bytes": measured["p2p_one_day"], "on": day, "source": "probe near_settlement_routes"}
    if "token_census_30d" in measured:
        st["census_bytes"] = {"bytes": measured["token_census_30d"], "on": day}
    nb._save(st)
    nb._run_bytes = nb._backfill_bytes = 0
    print("  " + nb._quota_line(spec, st))
    print(f"  Budget in config: {int(spec['monthly_budget_bytes']) / 1e9:,.0f} GB/month of the free 1 TB; per query "
          f"<= {int(spec['max_bytes_per_query']) / 1e9:,.0f} GB. The FT leg's dry run ignores clustering (it bills less).")
    print("  PASTE BACK all lines. P2P is approved: routine runs top up first, then backfill under the budget.")

def plume_archive():
    """Jake's run 2026-09-30 17:21: does rpc.plume.org serve HISTORICAL state for the live staking
    diamond? totalAmountStaked() and getRewardRate() at the first block of the day 1, 7, 30, 90,
    180 and 365 days back. Every answer = `token_metrics.py --seed plume_staking` backfills a year;
    the first refusal is where the series becomes forward-only on this RPC."""
    import pandas as pd                                    # noqa: PLC0415
    import config                                          # noqa: PLC0415
    from fetch.plume_staking import PlumeStaking           # noqa: PLC0415
    head("PLUME — historical state on rpc.plume.org for the live staking diamond")
    spec = config.PROJECT_BY_NAME["Plume"]["plume_staking"]
    try:
        block_at, has_code = PlumeStaking()._chain(spec)
    except Exception as e:  # noqa: BLE001
        print(f"  rpc.plume.org UNREACHABLE — {e}")
        return
    ps = PlumeStaking()
    for back in (1, 7, 30, 90, 180, 365):
        day = (pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() - pd.Timedelta(days=back))
        try:
            b = block_at(day)
            code = has_code(b)
        except Exception as e:  # noqa: BLE001
            print(f"  {day.date()} ({back}d back): block lookup failed — {e}")
            continue
        if not code:
            print(f"  {day.date()} ({back}d back): block {b} — the diamond has NO CODE yet (not deployed)")
            continue
        g = ps._read(spec, spec["address"], block=b)
        print(f"  {day.date()} ({back}d back): block {b} — " + (g if isinstance(g, str) else
              f"totalAmountStaked {g['aggregate']:,.2f} PLUME, gross APR {g['apr']:.4%}"))
    print("  PASTE BACK. All served = run `python token_metrics.py --seed plume_staking`.")


def _xhr_capture(url: str, wanted: tuple = (), deep: bool = False, dump_keys: tuple = (), save_to=None):
    """Load a page in Chromium (robots-checked first) and list every JSON response: URL, top-level
    keys, and any number in it matching `wanted`. Finds a dashboard's API; stores nothing."""
    from fetch.scrape import robots_verdict                # noqa: PLC0415
    ok, why = robots_verdict(url)
    print(f"  robots for {url}: {'ALLOWED' if ok else 'DISALLOWED'} — {why}")
    if not ok:
        return
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        print(f"  playwright unavailable — {e}")
        return
    import re                                              # noqa: PLC0415
    seen, requests_seen, frames, dumped = [], [], [], []
    content = ""

    def _dump(body):
        # deep=True callers name keys whose VALUES matter (a query, a contract): print them, and
        # every 0x address anywhere in the body.
        txt = json.dumps(body)
        for k in dump_keys:
            for m in re.finditer(r'"' + re.escape(k) + r'"\s*:\s*("(?:[^"\\]|\\.){0,600}"|\{[^{}]{0,600}\}|-?\d+)', txt):
                dumped.append(f"{k} = {m.group(1)[:600]}")
        for a in sorted(set(re.findall(r"0x[0-9a-fA-F]{40}", txt)))[:30]:
            dumped.append(f"address {a}")

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(user_agent=_ua()["User-Agent"])

        def grab(resp):
            if "json" in (resp.headers.get("content-type") or ""):
                try:
                    body = resp.json()
                except Exception:  # noqa: BLE001
                    return
                txt = json.dumps(body)[:200000]
                hits = [w for w in wanted if w in txt]
                keys = sorted(body)[:15] if isinstance(body, dict) else f"list[{len(body)}]"
                seen.append((resp.url.split("?")[0], keys, hits))
                if deep and (hits or dump_keys):
                    _dump(body)
                if save_to is not None and hits:
                    # kept for the next probe (geod_stake_recipient) — local, never committed
                    save_to.mkdir(parents=True, exist_ok=True)
                    (save_to / f"capture-{len(list(save_to.glob('capture-*.json')))}.json").write_text(
                        json.dumps({"url": resp.url.split("?")[0], "body": body}))

        def req(r):
            # EVERY request, not only JSON answers: a GraphQL POST, an RPC call, a websocket upgrade.
            if deep and r.resource_type in ("xhr", "fetch", "websocket", "eventsource", "other"):
                requests_seen.append((r.method, r.url.split("?")[0], (r.post_data or "")[:300]))

        def ws(sock):
            sock.on("framereceived", lambda f: frames.append((sock.url, str(f)[:300]))
                    if any(w in str(f) for w in wanted) or len(frames) < 5 else None)
        pg.on("response", grab)
        if deep:
            pg.on("request", req)
            pg.on("websocket", ws)
        pg.goto(url, wait_until="networkidle", timeout=60000)
        if deep:
            pg.wait_for_timeout(5000)
            content = pg.content()
        b.close()
    for u, k, h in seen:
        print(f"  JSON {u}\n       keys {k}{'  MATCHES ' + ', '.join(h) if h else ''}")
    print(f"  {len(seen)} JSON response(s). PASTE BACK the ones that MATCH.")
    if deep:
        for m_, u, body in requests_seen:
            print(f"  REQ {m_} {u}" + (f"  body {body!r}" if body else ""))
        for u, f in frames[:20]:
            print(f"  WS {u}: {f!r}")
        for w in wanted:
            for m in list(re.finditer(re.escape(w), content))[:3]:
                print(f"  HTML {w!r}: ...{content[max(0, m.start() - 150):m.end() + 150]!r}...")
        for d in dumped[:60]:
            print(f"  VALUE {d}")
        print(f"  {len(requests_seen)} request(s), {len(frames)} websocket frame(s), HTML {len(content):,} chars.")


def aethir_dashboard_xhr():
    """E: the supply-metric page's own API — GPUs/containers (433704), countries (94), TFLOPs
    (38509114), and any utilisation / rented / revenue / ARR field."""
    head("AETHIR — dashboard.aethir.com/protocol/supply-metric: find the underlying endpoint")
    # 2026-09-30: the first capture saw 0 JSON responses — so every request (GraphQL POSTs
    # included), websocket frames and the rendered HTML are searched too.
    _xhr_capture("https://dashboard.aethir.com/protocol/supply-metric",
                 ("433704", "433,704", "38509114", "38,509,114", "utiliz", "utilis", "rented",
                  "revenue", "arr", "ARR"), deep=True)
    print("  If nothing matches: supply_units stays a MONTHLY MANUAL row from Jake (manual_overrides.csv).")


def aethir_pages():
    """Jake's probes2 4 / probes3 2 (2026-09-30): every numeric field in the server-rendered (Next.js
    RSC) payload of Aethir's supply, demand, on-chain and overview pages; for the fields that
    matter, the SHAPE the adapter sees (scalar, or a dated series: dates, spacing, whether it ever
    falls) and the human text printed beside it in the payload (label, tooltip, unit) — what
    settles whether totalRunningHours is cumulative, what capacity it is measured against, and
    whether `amount` / `earning` are USD revenue. Then the Ethereum wrapper's ATH, veAethir's
    supply and the two ve pools' supply() read NOW, beside the page's ai/gaming/edge parts — how
    the wrapper relates to the dashboard's totalStaked."""
    from fetch import aethir_pages as ap                   # noqa: PLC0415
    from fetch.scrape import robots_verdict                # noqa: PLC0415
    import pandas as pd                                    # noqa: PLC0415
    head("AETHIR — numeric fields in each dashboard page's server-rendered payload")
    hints = ("used", "rent", "util", "occup", "revenue", "fee", "earn", "income", "arr", "demand", "hours")
    focus = ("totalStaked", "aiStaked", "gamingStaked", "edgeStaked", "idcStaked", "athCirculatingSupply",
             "emitted", "baseRewardDistributed", "bonusRewardDistributed", "airdropRewardDistributed",
             "numberDelegatedCheckers", "totalRunningHours", "totalOnlineHours", "totalMonthlyCapacity",
             "amount", "earning", "reward", "service")
    parts: dict = {}
    pages: dict = {}
    for page in ("protocol/supply-metric", "protocol/demand-metric", "protocol/onchain-metric",
                 "protocol/overview", "overview", "protocol/ecosystem"):
        url = f"https://dashboard.aethir.com/{page}"
        ok, why = robots_verdict(url)
        if not ok:
            print(f"  {page}: robots DISALLOWS — {why}")
            continue
        try:
            r = requests.get(url, headers=_ua(), timeout=TIMEOUT)
        except Exception as e:  # noqa: BLE001
            print(f"  {page}: UNREACHABLE — {e}")
            continue
        html = r.text
        f = ap.fields(html)
        print(f"\n  {page}: HTTP {r.status_code}, {len(f)} numeric field(s)")
        if r.status_code == 200:
            pages[page] = html
        for arr in ("stakeHistory", "emissionStakeRewardSchedule"):
            got_a = ap.array_objects(html, arr)
            if not isinstance(got_a, str):
                print(f"    [{arr}] {len(got_a)} element(s); keys {sorted({k for o in got_a for k in o})}; "
                      f"first {got_a[:2]}; last {got_a[-1:]}")
        for k, vals in sorted(f.items()):
            mark = "  <-- CANDIDATE" if any(h in k.lower() for h in hints) else ""
            print(f"    {k} = {vals[:4]}{' (+' + str(len(vals) - 4) + ' more)' if len(vals) > 4 else ''}{mark}")
        for k in focus:
            if k not in f:
                continue
            got = ap.reading(html, k)
            if isinstance(got, str):
                shape = got
            elif got[0] == "scalar":
                shape = f"SCALAR {got[1]:,.2f}"
            else:
                pts = got[1]
                gaps = pd.Series([(b[0] - a[0]).days for a, b in zip(pts, pts[1:])])
                rising = all(b[1] >= a[1] for a, b in zip(pts, pts[1:]))
                shape = (f"SERIES of {len(pts)}: {pts[0][0].date()} {pts[0][1]:,.2f} .. {pts[-1][0].date()} "
                         f"{pts[-1][1]:,.2f}; median gap {gaps.median():g} d; never falls: {rising}; "
                         f"sum {sum(v for _, v in pts):,.2f}")
                if len(pts) <= 40:
                    shape += "\n        all: " + ", ".join(f"{d.date()} {v:,.0f}" for d, v in pts)
            cur_, pts_, why_ = ap.current_and_series(html, k)
            print(f"       current (tile) {cur_ if cur_ is None else f'{cur_:,.2f}'}; chart "
                  f"{len(pts_) if pts_ else 0} point(s){'; ' + why_ if why_ else ''}")
            if page.endswith("onchain-metric") and cur_ is not None:
                parts[k] = cur_
            print(f"    >> {k}: {shape}")
            print(f"       labels near it: {ap.labels_near(html, k) or '(none found)'}")
            if k == "emitted" or (isinstance(got, str) and "beside a date" in got):
                objs = [o for o in ap.rsc_objects(html) if k in o][:3]
                print(f"       raw objects carrying it (keys reveal the date axis): {objs}")
            for c in ap.context(html, k, width=160, limit=1):
                print(f"       context: …{c}…")
    # THE WRAPPER BESIDE THE PAGE'S PARTS, read now (Ethereum).
    ath = "0xbe0Ed4138121EcFC5c0E56B40517da27E6c5226B"
    wrapper = "0x3f69Bb14860f7F3348Ac8A5f0D445322143F7feE"
    ve = "0x1B49F587feca530a7Bf7Cf2bD3fBda780e1B7490"
    w = _bal(ath, wrapper, "ethereum")
    vs = _uint(ve, "0x18160ddd", "ethereum")
    sup = {k: _uint(AETHIR_POOLS[k], "0x047fc9aa", "ethereum") for k in ("Gaming Pool", "AI Pool")}
    fm = lambda v: "n/a" if v is None else f"{v / 1e18:,.0f}"       # noqa: E731
    print(f"\n  ON-CHAIN NOW: wrapper ATH {fm(w)}; veAethir totalSupply {fm(vs)}; Gaming pool supply() "
          f"{fm(sup['Gaming Pool'])}; AI pool supply() {fm(sup['AI Pool'])}")
    if parts:
        g_, a_, e_ = (parts.get(k) for k in ("gamingStaked", "aiStaked", "edgeStaked"))
        if None not in (g_, a_, e_) and w:
            wt = w / 1e18
            print(f"  page ai+gaming {a_ + g_:,.0f} ({(a_ + g_) / wt - 1:+.2%} vs wrapper); ai+gaming+edge "
                  f"{a_ + g_ + e_:,.0f} ({(a_ + g_ + e_) / wt - 1:+.2%} vs wrapper)")
            if sup["Gaming Pool"] and sup["AI Pool"]:
                print(f"  page gamingStaked {g_:,.0f} vs Gaming pool supply() {sup['Gaming Pool'] / 1e18:,.0f}; "
                      f"page aiStaked {a_:,.0f} vs AI pool supply() {sup['AI Pool'] / 1e18:,.0f}")
        if "totalStaked" in parts:
            four = ("idcStaked", "aiStaked", "gamingStaked", "edgeStaked")
            missing = [k for k in four if parts.get(k) is None]
            if missing:
                # Jake's probes4: a part that was not read is NAMED, never summed as zero.
                print(f"  page totalStaked {parts['totalStaked']:,.0f}: no sum — no current figure for "
                      f"{', '.join(missing)}")
            else:
                s_ = sum(parts[k] for k in four)
                print(f"  page totalStaked {parts['totalStaked']:,.0f} vs sum of the four parts {s_:,.0f} "
                      f"({s_ / parts['totalStaked'] - 1:+.2%}): " + ", ".join(f"{k} {parts[k]:,.0f}" for k in four))
    # LABELLED FIGURES (Jake's PDFs, 2026-10-01): which payload key each label resolves to, by value.
    import config                                          # noqa: PLC0415
    lab = config.PROJECT_BY_NAME["Aethir"]["dashboard_pages"]["labelled"]
    resolved = {}
    print("\n  LABELLED FIGURES -> payload keys (pin these in config `labelled`):")
    for fid, spec_ in lab.items():
        if spec_.get("granularity"):
            continue
        got_ = ap.resolve_scalar(spec_, pages)
        if not isinstance(got_, str):
            resolved[fid] = got_
        print(f"    {spec_['label']}: " + (got_ if isinstance(got_, str) else f"{got_[0]} `{got_[1]}` {got_[2]:,.4f}"))
    for fid, spec_ in lab.items():
        if not spec_.get("granularity"):
            continue
        got_ = ap.resolve_series(spec_, pages, resolved)
        print(f"    {spec_['label']}: " + (got_ if isinstance(got_, str) else
              f"{got_[0]} `{got_[1]}` {len(got_[2])} point(s) {got_[2][0][0].date()}..{got_[2][-1][0].date()}"))
    print("\n  PASTE BACK all lines. Utilisation and revenue are wired only from a label that says what the"
          "\n  figure is, its unit and whether it is cumulative.")


def maple_ssf_history():
    """H: the transparency page's SSF chart — the embedded series (Astro island props) holding the
    SSF's SYRUP balance by date, and the monthly buyback list on the same page."""
    head("MAPLE — transparency page: the SSF SYRUP balance series")
    import re                                              # noqa: PLC0415
    import pandas as pd                                    # noqa: PLC0415
    import config                                          # noqa: PLC0415
    from fetch.scrape import robots_verdict                # noqa: PLC0415
    url = (config.PROJECT_BY_NAME["Maple"].get("maple_transparency") or {}).get("url") \
        or "https://maple.finance/transparency"
    ok, why = robots_verdict(url)
    print(f"  robots for {url}: {'ALLOWED' if ok else 'DISALLOWED'} — {why}")
    if not ok:
        return
    html = requests.get(url, headers=_ua(), timeout=TIMEOUT).text
    for m in re.finditer(r'props="([^"]{0,200000})"', html):
        blob = m.group(1).replace("&quot;", '"')
        if any(w in blob for w in ("SSF", "Strategic", "ssf", "syrup", "SYRUP")):
            dates = re.findall(r"20\d\d-\d\d-\d\d", blob)
            print(f"  island props {len(blob):,} chars, {len(dates)} dates ({dates[:1]}..{dates[-1:]}); "
                  f"head: {blob[:300]}")
    # 2026-09-30: the unwrapped series the adapter stores (fetch/maple_transparency.ssf_series).
    from fetch import maple_transparency as mt              # noqa: PLC0415
    for props in mt.islands(html):
        ds = props.get("datasets")
        if isinstance(ds, dict):
            for k, rows in ds.items():
                n = len(rows) if isinstance(rows, list) else 0
                print(f"  dataset {k}: {n} points; first {rows[:1] if n else None}; last {rows[-1:] if n else None}")
    got = mt.ssf_series(html)
    if isinstance(got, str):
        print(f"  ssf_series: {got}")
    else:
        pts, key = got
        print(f"  ssf_series -> dataset {key}: {len(pts)} day(s) {pts[0][0].date()}..{pts[-1][0].date()}")
        rel, skipped = mt.ssf_release(pts, mt.parse(html)["buybacks"], pd.Timestamp.now().normalize())
        for d, v in rel:
            print(f"    release {d.date()}: {v:,.2f} SYRUP")
        for w in skipped:
            print(f"    skipped {w}")
    print("  PASTE BACK each island line that names the SSF.")


def maple_ssf_lp_test():
    """Jake's probes3 3a-c (2026-09-30): IS THE SSF A LIQUIDITY POSITION? Last week SYRUP rose ~20%,
    SSF syrupHoldings fell 5.4% and liquidAssetsUsd rose 10.5% — the shape of a constant-product
    pool (sqrt(1.2) = +9.5%). Regress the daily SYRUP change and the daily liquidAssetsUsd change on
    the daily log price change over every day the page serves (fetch/maple_transparency.lp_fit),
    with the page's OWN price series where it has one, else this store's CoinGecko price_usd.
    Verdict by the declared rule (config Maple.ssf_lp_test). Reads only."""
    import sqlite3                                         # noqa: PLC0415
    import pandas as pd                                    # noqa: PLC0415
    import config                                          # noqa: PLC0415
    from fetch import maple_transparency as mt             # noqa: PLC0415
    from fetch.scrape import robots_verdict                # noqa: PLC0415
    head("MAPLE — is the SSF a constant-product liquidity position? (daily regression on price)")
    rule = config.PROJECT_BY_NAME["Maple"]["ssf_lp_test"]
    url = "https://maple.finance/transparency"
    ok, why = robots_verdict(url)
    print(f"  robots for {url}: {'ALLOWED' if ok else 'DISALLOWED'} — {why}")
    if not ok:
        return
    html = requests.get(url, headers=_ua(), timeout=TIMEOUT).text
    df = mt.ssf_frame(html)
    if isinstance(df, str):
        print(f"  {df}")
        return
    got = mt.price_series(html)
    if isinstance(got, str):
        print(f"  the page's own price: {got} — using this store's CoinGecko price_usd instead")
        try:
            px = pd.read_sql_query("SELECT date, value FROM metrics WHERE project='Maple' AND metric='price_usd'",
                                   sqlite3.connect("metrics.db"))
            price = px.assign(date=pd.to_datetime(px["date"]).dt.normalize()).groupby("date")["value"].last()
            where = "metrics.db price_usd (CoinGecko)"
        except Exception as e:  # noqa: BLE001
            print(f"  no price series at all — {e}")
            return
    else:
        price, where = got
    df = df.assign(price=df["day"].map(price))
    print(f"  SSF: {len(df)} day(s) {df['day'].min().date()}..{df['day'].max().date()}; price from {where}, "
          f"{df['price'].notna().sum()} day(s) matched")
    verdicts = {}
    for label, part in (("ALL DAYS", df), ("LAST 90 DAYS", df[df["day"] > df["day"].max() - pd.Timedelta(days=90)])):
        r = mt.lp_fit(part, rule)
        verdicts[label] = r.get("verdict")
        if r.get("verdict") is None:
            print(f"  {label}: {r['why']}")
            continue
        print(f"\n  {label} ({r['n']} daily changes, {r['first'].date()}..{r['last'].date()}):")
        print(f"    d(syrup) on dlnP: slope {r['slope_syrup']:,.0f}, R2 {r['r2_syrup']:.3f}  -> implied LP "
              f"{r['x_lp']:,.0f} SYRUP")
        print(f"    d(usd)   on dlnP: slope {r['slope_usd']:,.0f}, R2 {r['r2_usd']:.3f}  -> implied LP "
              f"${r['y_lp']:,.0f}")
        print(f"    the two sides at the mean price ${r['mean_price']:.4f}: {r['size_agreement']:.2f}x (1.00 = "
              f"one x*y=k position)")
        print(f"    d(usd) on -price x d(syrup): slope {r['swap_slope']:.3f}, R2 {r['r2_swap']:.3f} (1 = every "
              f"SYRUP change swapped at market)")
        print(f"    VERDICT by the declared rule: {'LP CONFIRMED' if r['verdict'] else 'NOT CONFIRMED'} — {r['why']}")
    # THE VERDICT ACTUALLY REACHED (Jake's probes4, 2026-10-01: the old closing lines printed "LP
    # CONFIRMED on ALL DAYS" whatever the fit said — R2 0.11/0.05 all days, 0.39/0.43 last 90).
    word = {True: "LP CONFIRMED", False: "NOT CONFIRMED", None: "NOT DECIDED (too few days)"}
    print("\n  OVERALL: " + "; ".join(f"{k}: {word[v]}" for k, v in verdicts.items()))
    if verdicts.get("ALL DAYS") is True:
        print("  The SSF's holding changes fit price-driven rebalancing on all days.")
    else:
        print("  The constant-product LP hypothesis is NOT supported on all days. pool_release stays N/A on its\n"
              "  own ground: no emission programme runs (MIP-019 sunset; Drips ended — config\n"
              "  Maple.pool_release_tokens_blocked). See Maple.ssf_selling_question for the open question.")
    print("  PASTE BACK all lines.")


def maple_drips():
    """Jake's probes3 3c (2026-09-30): Maple's ACTUAL SYRUP reward emissions, from syrupDrip
    0x509712F368255E92410893Ba2E488f40f7E986EA (maple-labs/address-registry@3df2052
    MapleAddressRegistryETH.md:273), a merkle distributor (maple-labs/syrup-utils@87debb1
    contracts/interfaces/ISyrupDrip.sol:16-40): Claimed(uint256 indexed id, address indexed account,
    uint256 amount) and Staked(uint256 indexed id, address indexed account, uint256 assets, uint256
    shares) pay SYRUP out; Reclaimed(address indexed account, uint256 amount) returns what was not
    claimed. Drips ended with Season 12 (Q4 2025; claims 18 Jan-18 Feb 2026, maple-docs
    drips-rewards.md:7-20). Monthly sums, so the emission stream and its end are measured."""
    import pandas as pd                                    # noqa: PLC0415
    from eth_utils import keccak                          # noqa: PLC0415
    head("MAPLE — SYRUP paid out by syrupDrip (Claimed + Staked, less Reclaimed), by month")
    drip = "0x509712F368255E92410893Ba2E488f40f7E986EA"
    rows = []
    for ev, word in (("Claimed(uint256,address,uint256)", 0), ("Staked(uint256,address,uint256,uint256)", 0),
                     ("Reclaimed(address,uint256)", 0)):
        t0 = "0x" + keccak(text=ev).hex().removeprefix("0x")
        logs, detail = explorer_logs(1, drip, [t0])
        print(f"  {ev.split('(')[0]}: {detail}")
        for lg in logs or ():
            data = lg["data"][2:]
            amt = int(data[word * 64:(word + 1) * 64], 16) / 1e18
            ts = pd.Timestamp(int(lg["timeStamp"]), unit="s")
            rows.append((ts.to_period("M"), ev.split("(")[0], amt))
    if not rows:
        print("  no events read — set ETHERSCAN_API_KEY in .env")
        return
    t = pd.DataFrame(rows, columns=["month", "event", "syrup"]).pivot_table(
        index="month", columns="event", values="syrup", aggfunc="sum", fill_value=0.0)
    for c in ("Claimed", "Staked", "Reclaimed"):
        if c not in t:
            t[c] = 0.0
    t["paid_out"] = t["Claimed"] + t["Staked"] - t["Reclaimed"]
    print("  " + t.round(0).to_string().replace("\n", "\n  "))
    print(f"  last month with a payout: {t[t['paid_out'] > 0].index.max()}. PASTE BACK: this is the candidate "
          f"emissions_tokens route for Maple (monthly, pre-minted SYRUP distributed).")


def maple_ssf_inflows():
    """Jake's probes2 3c (2026-09-30): the SSF's net change came out NEGATIVE for 2025-11 and 2026-07
    — it took in more SYRUP than that month's buybacks. For each such month: the days the SSF
    series jumped up, and the SYRUP transfers INTO Maple's treasury (daoMultisig) on those days
    with each sender, so every inflow can be classified before the release is used. Also Maple's
    own monthly revenue beside DefiLlama's (3b). Reads only; the key is never printed."""
    import pandas as pd                                    # noqa: PLC0415
    import config                                          # noqa: PLC0415
    from fetch import maple_transparency as mt             # noqa: PLC0415
    from fetch.scrape import robots_verdict                # noqa: PLC0415
    head("MAPLE — SSF inflows beyond the buybacks; revenue vs DefiLlama")
    url = "https://maple.finance/transparency"
    ok, why = robots_verdict(url)
    print(f"  robots for {url}: {'ALLOWED' if ok else 'DISALLOWED'} — {why}")
    if not ok:
        return
    html = requests.get(url, headers=_ua(), timeout=TIMEOUT).text
    got = mt.ssf_series(html)
    if isinstance(got, str):
        print(f"  ssf_series: {got}")
        return
    pts, key = got
    h = pd.Series(dict(pts)).sort_index()
    bb = mt.parse(html)["buybacks"]
    now = pd.Timestamp.now().normalize()
    zeros, zwhy = mt.buyback_zero_months(bb, list(bb["month"]), now)
    rel, skipped = mt.ssf_release(pts, bb, now, zeros)
    neg = [(d, v) for d, v in rel if v < 0]
    print(f"  SSF dataset {key}: {len(h)} day(s) {h.index[0].date()}..{h.index[-1].date()}; last {h.iloc[-1]:,.2f}")
    print(f"  buyback zeros: {', '.join(f'{m:%Y-%m}' for m in zeros) or 'none'} — {zwhy}")
    # THE COMPLETE MONTHLY PICTURE (Jake, 2026-09-30): every month since the first visible buyback
    bought = {r.month: float(r.syrup) for r in bb.itertuples()}
    bought.update({m: 0.0 for m in zeros})
    hd = dict(pts)
    print("  month     holdings at start    holdings at end     change     bought      net outflow")
    for d, v in rel:
        m0 = d.to_period("M").to_timestamp()
        m1 = m0 + pd.offsets.MonthBegin(1)
        b = bought.get(m0)
        print(f"  {m0:%Y-%m} {hd[m0]:>19,.0f} {hd[m1]:>18,.0f} {hd[m1] - hd[m0]:>+12,.0f} "
              f"{b:>11,.0f}{' (0)' if m0 in zeros else '    '} {v:>+14,.0f}{'  <-- NEGATIVE' if v < 0 else ''}")
    for w in skipped:
        print(f"  skipped {w}")
    print(f"  months with a NEGATIVE net outflow (an inflow beyond the buybacks): "
          f"{', '.join(f'{d:%Y-%m} {v:,.0f}' for d, v in neg) or 'none'}")
    # every island key that is not a chart dataset: a transactions list would show senders
    for props in mt.islands(html):
        other = [k for k in props if k != "datasets"]
        if other:
            print(f"  island keys besides datasets: {other[:15]}")
    p = config.PROJECT_BY_NAME["Maple"]
    token = p["contracts"]["token"]["address"]
    treasury = p["contracts"]["treasury"]["address"]
    known = {c["address"].lower(): k for k, c in p["contracts"].items()}
    key_ = os.environ.get("ETHERSCAN_API_KEY", "").strip()
    api = "https://api.etherscan.io/v2/api"

    def es(params):
        return requests.get(api, params={"chainid": 1, **params, "apikey": key_}, headers=_ua(),
                            timeout=TIMEOUT).json()
    for m, v in neg:
        start = m.to_period("M").to_timestamp()
        month = h[(h.index >= start - pd.Timedelta(days=1)) & (h.index <= m)]
        jumps = month.diff().dropna()
        jumps = jumps[jumps > 0].sort_values(ascending=False).head(8)
        print(f"\n  {m:%Y-%m}: net {v:,.0f}; the largest daily RISES in SSF holdings:")
        for d, x in jumps.items():
            print(f"    {d.date()}: +{x:,.2f} SYRUP")
        if not key_:
            print("    (no ETHERSCAN_API_KEY — the treasury's SYRUP inflows are not listed)")
            continue
        try:
            b0 = es({"module": "block", "action": "getblocknobytime", "timestamp": int(start.timestamp()),
                     "closest": "after"})["result"]
            b1 = es({"module": "block", "action": "getblocknobytime",
                     "timestamp": int((m + pd.Timedelta(days=1)).timestamp()), "closest": "before"})["result"]
            txs = es({"module": "account", "action": "tokentx", "contractaddress": token, "address": treasury,
                      "startblock": b0, "endblock": b1, "sort": "asc"}).get("result") or []
        except Exception as e:  # noqa: BLE001
            print(f"    Etherscan UNREACHABLE — {e}")
            continue
        ins = [t for t in txs if isinstance(t, dict) and t.get("to", "").lower() == treasury.lower()]
        print(f"    SYRUP transfers INTO the treasury {treasury} that month: {len(ins)}")
        for t in sorted(ins, key=lambda t: -int(t["value"]))[:15]:
            day = pd.Timestamp(int(t["timeStamp"]), unit="s").date()
            frm = t["from"]
            print(f"      {day} {int(t['value']) / 1e18:>16,.2f} SYRUP from {frm} "
                  f"{('(' + known[frm.lower()] + ')') if frm.lower() in known else ''} tx {t['hash'][:12]}…")
    # 3b: Maple's own monthly revenue beside DefiLlama's
    rs = mt.revenue_series(html)
    if isinstance(rs, str):
        print(f"\n  revenue_series: {rs}")
        return
    rev, rkey = rs
    try:
        ll = requests.get("https://api.llama.fi/summary/fees/maple", params={"dataType": "dailyRevenue"},
                          headers=_ua(), timeout=TIMEOUT).json().get("totalDataChart") or []
        dl = pd.Series({pd.Timestamp(int(t), unit="s"): float(v) for t, v in ll})
        dl = dl.groupby(dl.index.to_period("M")).sum()
    except Exception as e:  # noqa: BLE001
        dl = pd.Series(dtype=float)
        print(f"  DefiLlama UNREACHABLE — {e}")
    print(f"\n  Maple's own monthly revenue (dataset {rkey}) vs DefiLlama's, same month:")
    for d, v in rev[-14:]:
        other = dl.get(d.to_period("M"))
        ratio = f"{v / other:.2f}x" if other else "n/a"
        print(f"    {d:%Y-%m}: Maple ${v:>14,.2f}   DefiLlama ${other if other is not None else float('nan'):>14,.2f}   {ratio}")
    print("  PASTE BACK all lines: each inflow's sender decides how the negative months are classified.")


def blockworks_geodnet():
    """D1 (cross-check only): Blockworks' GEODNET staking-flow chart endpoint, robots-checked. Not
    wired: its terms are unread (licensing register)."""
    head("GEODNET — Blockworks staking-flow chart (cross-check only)")
    # 2026-09-30 (Jake: dashboard 326, visualizations 2669, 2573, 2575 matched stake/unstake):
    # print the visualizations' metadata — a query text, a contract address — so a contract can
    # be verified ON-CHAIN (~3.0M now, ~12M Nov 2025) and read directly; Blockworks is only the
    # pointer.
    _xhr_capture("https://app.blockworks.com/projects/geodnet/analytics/geodnet",
                 ("stake", "Stake", "unstake", "2669", "2573", "2575", "geod_stake"), deep=True,
                 dump_keys=("query", "sql", "queryText", "query_sql", "contract", "contractAddress",
                            "address", "source", "dataSource", "table", "description", "title"),
                 save_to=_blockworks_dir())
    print(f"  Matching JSON bodies saved under {_blockworks_dir()} for geod_stake_recipient.")
    print("  PASTE BACK the VALUE lines: a contract address there is checked on-chain before any wiring.")


def _blockworks_dir():
    from fetch.logcache import LogCache                    # noqa: PLC0415
    return LogCache().root / "blockworks-geodnet"


def _blockworks_rows() -> list[dict]:
    """Rows carrying geod_stake from the saved Blockworks captures (query 1243, 'geodnet-issuance-
    burn-stake'), or from $GEODNET_BLOCKWORKS_CSV if Jake exported the query instead."""
    import csv                                             # noqa: PLC0415
    path = os.environ.get("GEODNET_BLOCKWORKS_CSV", "").strip()
    if path:
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    rows = []

    def walk(x):
        if isinstance(x, dict):
            if "geod_stake" in x:
                rows.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    for f in sorted(_blockworks_dir().glob("capture-*.json")):
        try:
            walk(json.loads(f.read_text()))
        except ValueError:
            continue
    return rows


def _bw_days(values) -> "pd.Series":
    """Blockworks' date column as UTC days. ** Jake's probes3 run (2026-09-30): every date parsed as
    1970-01-01 ** — the column is a UNIX timestamp, and pd.to_datetime reads a bare number as
    NANOSECONDS. FIXED AGAIN 2026-10-05 (Jake: "fix its timestamp parsing"): the unit is decided PER
    VALUE by magnitude — seconds (< 1e11), milliseconds (< 1e14), microseconds (< 1e17), else
    nanoseconds — numbers that arrive as strings or floats ("1761955200.0") included; text dates of
    any common shape (ISO with or without a zone, "2025-11-01 00:00:00.000 UTC") are parsed per
    value. Values that parse before 2015 or after tomorrow are refused rather than used."""
    import pandas as pd                                    # noqa: PLC0415
    raw = pd.Series(list(values), dtype=object)
    num = pd.to_numeric(raw.map(lambda v: str(v).strip() if v is not None else v), errors="coerce")
    out = []
    for v, n in zip(raw, num):
        try:
            if pd.notna(n):
                a = abs(float(n))
                unit = "s" if a < 1e11 else "ms" if a < 1e14 else "us" if a < 1e17 else "ns"
                d = pd.to_datetime(float(n), unit=unit, utc=True)
            else:
                txt = str(v).strip().replace(" UTC", "+00:00")
                d = pd.to_datetime(txt, utc=True, format="mixed")
            out.append(d.tz_localize(None).normalize())
        except (ValueError, TypeError, OverflowError):
            out.append(pd.NaT)
    days = pd.Series(out, dtype="datetime64[ns]")
    good = days.dropna()
    if len(good):
        lo, hi = good.min(), good.max()
        if lo < pd.Timestamp("2015-01-01") or hi > pd.Timestamp.now().normalize() + pd.Timedelta(days=1):
            raise ValueError(f"dates parse to {lo.date()}..{hi.date()} — unit not recognised "
                             f"(sample {raw.head(3).tolist()})")
    return days


def _bw_frame(rows: list[dict]):
    """(DataFrame with a `day` column and numeric columns, date column used, why-not). The date
    column is the one that PARSES to plausible days for most rows — name-like candidates first —
    never simply the first key containing "time" (an "updated_time" or a duration would win)."""
    import pandas as pd                                    # noqa: PLC0415
    df = pd.DataFrame(rows)
    named = [k for k in df.columns if any(w in k.lower() for w in ("date", "day", "time", "block_time", "ts"))]
    best, best_n, errs = None, 0, []
    for k in named + [k for k in df.columns if k not in named]:
        try:
            d = _bw_days(df[k])
        except ValueError as e:
            errs.append(f"{k}: {e}")
            continue
        if d.notna().sum() > best_n and d.dropna().nunique() > 1:
            best, best_n, days = k, int(d.notna().sum()), d
    if best is None:
        return None, None, "no column parses to dates: " + "; ".join(errs[:4])
    df["day"] = days.values
    for c in df.columns:
        if c not in (best, "day"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["day"]).sort_values("day"), best, ""


_POLY_BLOCKS: dict = {}


def _polygon_block_at(ts: int) -> int | None:
    """First Polygon block at or after UNIX time `ts`, by binary search on block timestamps over
    the configured endpoints (keyed POLYGON_RPC_URL first). Etherscan's getblocknobytime timed
    out in Jake's run, so this needs no Etherscan. Cached per timestamp for the run."""
    if ts in _POLY_BLOCKS:
        return _POLY_BLOCKS[ts]
    for url in _rpcs_for("polygon"):
        try:
            hi = int(rpc(url, "eth_blockNumber")["result"], 16)

            def t(n):
                return int(rpc(url, "eth_getBlockByNumber", [hex(n), False])["result"]["timestamp"], 16)
            lo = max(hi - 40_000_000, 1)
            if t(lo) > ts:
                return None
            while lo < hi:
                mid = (lo + hi) // 2
                if t(mid) < ts:
                    lo = mid + 1
                else:
                    hi = mid
            _POLY_BLOCKS[ts] = lo
            return lo
        except Exception:  # noqa: BLE001
            continue
    return None


def _geod_day_logs(day, topics: list, es=None) -> tuple[list | None, str]:
    """GEOD Transfer logs on Polygon for one UTC day. Etherscan V2 first with retries (60s, then
    2s/4s/8s backoff) when a key is set; then the RPC route (POLYGON_RPC_URL first — Alchemy
    serves logs by block range), chunked and narrowed on the server's own range complaint."""
    import time                                            # noqa: PLC0415
    import pandas as pd                                    # noqa: PLC0415
    geod = "0xAC0F66379A6d7801D7726d5a943356A172549Adb"
    t0 = int(pd.Timestamp(day).tz_localize("UTC").timestamp())
    b0, b1 = _polygon_block_at(t0), _polygon_block_at(t0 + 86_400)
    if b0 is None or b1 is None:
        return None, "no Polygon endpoint answered the block lookup"
    b1 -= 1
    if es is not None:
        out, why = [], ""
        for page in range(1, 11):
            res = None
            for wait in (0, 2, 4, 8):
                time.sleep(wait)
                try:
                    j = es({"module": "logs", "action": "getLogs", "address": geod, "fromBlock": b0,
                            "toBlock": b1, "page": page, "offset": 1000,
                            **{f"topic{i}": t for i, t in enumerate(topics) if t},
                            **({"topic0_1_opr": "and"} if len(topics) > 1 and topics[1] else {}),
                            **({"topic0_2_opr": "and"} if len(topics) > 2 and topics[2] else {})})
                    res = j.get("result") if isinstance(j.get("result"), list) else []
                    break
                except Exception as e:  # noqa: BLE001
                    why = str(e).split("?")[0]
            if res is None:
                out = None
                break
            out.extend(res)
            if len(res) < 1000:
                break
        if out is not None:
            return out, f"Etherscan, blocks {b0:,}-{b1:,}"
        print(f"    Etherscan failed after retries ({why}); trying the Polygon RPC route")
    logs, detail = eth_get_logs(geod, topics, b0, b1, chunk=2_000, chain="polygon", min_chunk=10)
    return logs, f"RPC, {detail}"


def geod_stake_recipient():
    """Jake's probes2 5 / probes3 4 (2026-09-30): Blockworks' query 1243 carries geod_stake /
    geod_unstake / geod_total_stake but names no staking address (only the GEOD token), so staking
    likely goes to an EOA. Take the days with the most distinctive geod_stake totals, sum each
    day's GEOD Transfer INFLOWS per recipient on Polygon, and print the recipients whose inflow
    matches; then compare the best candidate's balance with the latest geod_total_stake.

    probes3: (a) Blockworks' dates are UNIX timestamps — read as seconds/ms (_bw_days), never as
    nanoseconds (1970-01-01). (b) Etherscan timed out at 25s: Etherscan is retried with backoff,
    then the Polygon RPC route (POLYGON_RPC_URL/Alchemy) by block range. (c) On the 512,000-GEOD
    stake day the nearest recipient was 0x8FB9dd00… — GEODNET's own mining DISTRIBUTION wallet.
    So, for EVERY stake day, the two mining wallets' inflows (and senders) are compared with
    geod_stake and their outflows with geod_unstake: staking that flows through the mining
    wallets would explain why no staking contract exists — and would mean the mining wallets'
    balance fall (our pool_release) nets stake and unstake flows.

    Also compares the series' issuance and burn columns with this store's GEODNET release and burn
    (cross-check only — Blockworks' terms are unread). Reads only; keys never printed. Run
    blockworks_geodnet first (it saves the query's rows), or set GEODNET_BLOCKWORKS_CSV."""
    import pandas as pd                                    # noqa: PLC0415
    head("GEODNET — who receives the staked GEOD? (Blockworks geod_stake vs Polygon Transfer logs)")
    rows = _blockworks_rows()
    if not rows:
        print(f"  no Blockworks rows: run `check_offline_items.py blockworks_geodnet` first (saves to "
              f"{_blockworks_dir()}), or set GEODNET_BLOCKWORKS_CSV to an export of query 1243")
        return
    df, dkey, why = _bw_frame(rows)
    if df is None:
        print(f"  {why} (columns {sorted(rows[0])})")
        return
    print(f"  {len(df)} row(s) {df['day'].min().date()}..{df['day'].max().date()} (date column {dkey!r}, "
          f"sample {rows[0][dkey]!r}); columns {sorted(df.columns)[:20]}")
    stake = df.dropna(subset=["geod_stake"])
    stake = stake[stake["geod_stake"] > 0]
    # distinctive: large, and not round
    pick = stake.assign(frac=(stake["geod_stake"] % 1).abs()).sort_values(["geod_stake"], ascending=False)
    pick = pick[pick["frac"] > 0].head(5) if (pick["frac"] > 0).any() else pick.head(5)
    key = os.environ.get("ETHERSCAN_API_KEY", "").strip()
    geod = "0xAC0F66379A6d7801D7726d5a943356A172549Adb"
    topic = "0x" + __import__("eth_utils").keccak(text="Transfer(address,address,uint256)").hex().removeprefix("0x")
    api = "https://api.etherscan.io/v2/api"

    def es(params):
        return requests.get(api, params={"chainid": 137, **params, "apikey": key}, headers=_ua(),
                            timeout=60).json()
    es_ = es if key else None
    if not key:
        print("  no ETHERSCAN_API_KEY — Polygon RPC route only (set POLYGON_RPC_URL to a keyed endpoint)")
    votes: dict = {}
    for _, r in pick.iterrows():
        d = r["day"]
        logs, how = _geod_day_logs(d, [topic], es_)
        if logs is None:
            print(f"  {d.date()}: logs unavailable — {how}")
            continue
        inflow: dict = {}
        for lg in logs:
            to = "0x" + lg["topics"][2][-40:]
            inflow[to] = inflow.get(to, 0.0) + _hexint(lg["data"]) / 1e18
        target = float(r["geod_stake"])
        close = sorted(inflow.items(), key=lambda kv: abs(kv[1] - target))[:3]
        print(f"  {d.date()}: geod_stake {target:,.4f} ({len(logs)} transfers, {how}); nearest recipients "
              f"by inflow: " + "; ".join(f"{a} {v:,.4f} ({v / target:.4f}x)" for a, v in close))
        for a, v in close[:1]:
            votes[a] = votes.get(a, 0) + (abs(v - target) <= 1.0)
    if votes:
        best = max(votes, key=votes.get)
        print(f"\n  recipient matching geod_stake to within 1 GEOD on {votes[best]} of {len(pick)} day(s): {best}")
        w, _ = eth_call(geod, "0x70a08231" + best[2:].lower().rjust(64, "0"), chain="polygon")
        last = df.dropna(subset=["geod_total_stake"]).iloc[-1] if "geod_total_stake" in df else None
        print(f"  its GEOD balance now {'UNREACHABLE' if not w else f'{int(w, 16) / 1e18:,.2f}'}; Blockworks "
              f"geod_total_stake {'n/a' if last is None else f'{last.geod_total_stake:,.2f} on {last.day.date()}'}"
              f" (~3.0M now, ~12M Nov 2025 expected)")

    # (c) DOES STAKING FLOW THROUGH THE MINING WALLETS? Every stake/unstake day, both wallets.
    mining = {"mining 0xfa5f": "0xfa5fEd5cc2b6DD8F370651D17242C52Ed711B14F",
              "distribution 0x8FB9": "0x8FB9dd00B9a3D893dA96d444817d0b77330d5478"}
    print("\n  (c) the mining wallets on stake/unstake days — inflow vs geod_stake, outflow vs "
          "geod_unstake (distribution outflows include the daily miner rewards)")
    days = df[(df.get("geod_stake", 0).fillna(0) > 0) | (df.get("geod_unstake", 0).fillna(0) > 0)]
    days = days.sort_values("day").tail(int(os.environ.get("GEOD_STAKE_DAYS", "20")))
    matched = {"stake": 0, "unstake": 0}
    for _, r in days.iterrows():
        cells = []
        for label, addr in mining.items():
            pad = "0x" + addr[2:].lower().rjust(64, "0")
            ins, _h = _geod_day_logs(r["day"], [topic, None, pad], es_)
            outs, _h2 = _geod_day_logs(r["day"], [topic, pad, None], es_)
            if ins is None or outs is None:
                cells.append(f"{label}: logs unavailable")
                continue
            vin = sum(_hexint(x["data"]) for x in ins) / 1e18
            senders = sorted({"0x" + x["topics"][1][-40:] for x in ins})
            big = sorted((_hexint(x["data"]) / 1e18 for x in outs), reverse=True)[:3]
            st, us = float(r.get("geod_stake") or 0), float(r.get("geod_unstake") or 0)
            hit_in = st > 0 and abs(vin - st) <= 1.0
            hit_out = us > 0 and any(abs(v - us) <= 1.0 for v in big)
            matched["stake"] += hit_in
            matched["unstake"] += hit_out
            cells.append(f"{label}: in {vin:,.2f} from {len(senders)} sender(s) {senders[:3]}"
                         f"{' = geod_stake' if hit_in else ''}; largest out {[round(v, 2) for v in big]}"
                         f"{' ∋ geod_unstake' if hit_out else ''}")
        print(f"  {r['day'].date()}: stake {float(r.get('geod_stake') or 0):,.2f}, unstake "
              f"{float(r.get('geod_unstake') or 0):,.2f}\n      " + "\n      ".join(cells))
    print(f"  mining-wallet matches over {len(days)} day(s): stake {matched['stake']}, unstake "
          f"{matched['unstake']}. MOST DAYS MATCHING = staking flows through the mining wallets (no "
          f"separate contract; our pool_release then nets stake flows). FEW = a different recipient.")

    # cross-check: the series' issuance and burn columns vs this store's release and burn
    try:
        import sqlite3                                     # noqa: PLC0415
        con = sqlite3.connect("metrics.db")
        st = pd.read_sql_query("SELECT date, metric, value FROM metrics WHERE project='GEODNET' AND metric IN "
                               "('emissions_tokens','pool_release_tokens','gross_burn_tokens')", con)
        st["m"] = pd.to_datetime(st["date"]).dt.to_period("M")
        ours = st.groupby(["metric", "m"])["value"].sum().unstack(0)
        cols = [c for c in df.columns if any(w in c.lower() for w in ("issu", "burn"))]
        theirs = df.assign(m=df["day"].dt.to_period("M")).groupby("m")[cols].sum()
        both = ours.join(theirs, how="inner").tail(12)
        print("\n  monthly: this store's GEODNET release/burn vs Blockworks' issuance/burn columns (cross-check only)")
        print("  " + both.round(0).to_string().replace("\n", "\n  "))
    except Exception as e:  # noqa: BLE001
        print(f"  cross-check skipped — {e}")
    print("  PASTE BACK all lines. A recipient is wired as locked_tokens only after its balance is "
          "verified to track geod_total_stake.")


# Candidates from Jake's GEOD holder export (2026-10-05), with the balance it showed. NOT WIRED: the
# one whose balance tracks Blockworks' geod_total_stake (~12M Nov 2025 -> ~3.0M now) is wired as
# locked_tokens only after geod_stake_wallets shows it, month by month.
GEOD_STAKE_CANDIDATES = {
    "0x82146cf0f350c241757660fd803c73313b06d75c": 3_911_186,
    "0x0d0707963952f2fba59dd06f2b425ace40b492fe": 3_514_051,
    "0xe3b49ad54ca4ee65070f94324cf880ce9a045ccd": 3_122_500,
    "0x4da4f52a0f4212a881f3c03e4b1998f560ec17df": 3_061_102,
    "0x682ba846eed9934cc89ed89a350ea98781256b6f": 2_995_000,
    "0x237ae888ccb6c43628fd6a24ba48dd1bf65cbff0": 2_938_025,
    "0xe92e65049b3c2ca12806e9567b08895118c5a03f": 2_806_841,
}
# A wallet TRACKS the series when, on the months both exist, its balance is within TRACK_TOL of
# geod_total_stake on at least TRACK_SHARE of them, over at least TRACK_MIN_MONTHS months, and the
# series' peak month is within one month of the wallet's.
TRACK_TOL, TRACK_SHARE, TRACK_MIN_MONTHS = 0.10, 0.80, 10


def stake_wallet_verdict(balances: dict, series: dict) -> list[dict]:
    """balances {label: {month: GEOD}}, series {month: geod_total_stake} -> one row per label, best
    first: months compared, share within TRACK_TOL, median |ratio - 1|, both peak months, tracks?"""
    out = []
    for label, b in balances.items():
        common = sorted(m for m in b if m in series and b[m] is not None and series[m])
        if not common:
            out.append({"label": label, "months": 0, "within": 0.0, "median_err": None, "tracks": False,
                        "peak_wallet": None, "peak_series": None})
            continue
        errs = [abs(b[m] / series[m] - 1) for m in common]
        within = sum(e <= TRACK_TOL for e in errs) / len(errs)
        pw, ps = max(common, key=lambda m: b[m]), max(common, key=lambda m: series[m])
        months_apart = abs((pw.year - ps.year) * 12 + pw.month - ps.month)
        out.append({"label": label, "months": len(common), "within": within,
                    "median_err": sorted(errs)[len(errs) // 2], "peak_wallet": pw, "peak_series": ps,
                    "tracks": (len(common) >= TRACK_MIN_MONTHS and within >= TRACK_SHARE and months_apart <= 1)})
    return sorted(out, key=lambda r: (not r["tracks"], -r["within"], r["median_err"] if r["median_err"] is not None else 9))


def geod_stake_wallets():
    """Jake, 2026-10-05: seven candidates from his GEOD holder export (2.8-3.9M each). Each one's GEOD
    balance on Polygon at the first block of the 1st of every month, 2025-06 -> 2026-10 (ARCHIVE reads
    — POLYGON_RPC_URL, e.g. Alchemy; public RPCs serve recent state only), against Blockworks query
    1243's geod_total_stake on that day (the last value on or before it). Also the sum of all seven,
    in case staking is spread over several wallets. Reads only; keys never printed. Needs the saved
    Blockworks rows (blockworks_geodnet) or GEODNET_BLOCKWORKS_CSV."""
    import pandas as pd                                    # noqa: PLC0415
    head("GEODNET — staking-wallet candidates: monthly GEOD balance vs Blockworks geod_total_stake")
    rows = _blockworks_rows()
    if not rows:
        print(f"  no Blockworks rows: run `check_offline_items.py blockworks_geodnet` first (saves to "
              f"{_blockworks_dir()}), or set GEODNET_BLOCKWORKS_CSV to an export of query 1243")
        return
    df, dkey, why = _bw_frame(rows)
    if df is None:
        print(f"  {why}")
        return
    if "geod_total_stake" not in df:
        print(f"  no geod_total_stake column among {sorted(df.columns)[:20]}")
        return
    st = df.dropna(subset=["geod_total_stake"]).drop_duplicates("day", keep="last").set_index("day")["geod_total_stake"]
    print(f"  Blockworks: {len(st)} day(s) {st.index.min().date()}..{st.index.max().date()} (date column "
          f"{dkey!r}, sample {rows[0][dkey]!r}); peak {st.max():,.0f} on {st.idxmax().date()}, latest "
          f"{st.iloc[-1]:,.0f} on {st.index.max().date()}")
    months = list(pd.date_range("2025-06-01", "2026-10-01", freq="MS"))
    series = {}
    for m in months:
        before = st[st.index <= m]
        if len(before) and (m - before.index.max()).days <= 7:
            series[m] = float(before.iloc[-1])
    geod = "0xAC0F66379A6d7801D7726d5a943356A172549Adb"
    bal = {a: {} for a in GEOD_STAKE_CANDIDATES}
    for m in months:
        blk = _polygon_block_at(int(m.tz_localize("UTC").timestamp()))
        if blk is None:
            print(f"  {m.date()}: no Polygon endpoint answered the block lookup")
            continue
        for a in GEOD_STAKE_CANDIDATES:
            w, where = eth_call(geod, "0x70a08231" + a[2:].lower().rjust(64, "0"), block=hex(blk), chain="polygon")
            bal[a][m] = int(w, 16) / 1e18 if w and w != "0x" else None
            if w is None:
                print(f"  {m.date()} {a[:10]}: UNREADABLE at block {blk:,} — {str(where)[:160]} "
                      f"(an archive endpoint is needed: POLYGON_RPC_URL)")
    bal["SUM of all seven"] = {m: sum(bal[a][m] for a in GEOD_STAKE_CANDIDATES)
                               for m in months if all(bal[a].get(m) is not None for a in GEOD_STAKE_CANDIDATES)}
    short = {a: a[:10] for a in GEOD_STAKE_CANDIDATES}
    print(f"\n  {'month':<10} {'Blockworks':>13} " + " ".join(f"{short.get(k, 'SUM'):>12}" for k in bal))
    for m in months:
        print(f"  {str(m.date()):<10} {series.get(m, float('nan')):>13,.0f} "
              + " ".join(f"{(bal[k].get(m) if bal[k].get(m) is not None else float('nan')):>12,.0f}" for k in bal))
    print(f"\n  verdict (tracks = within {TRACK_TOL:.0%} on >= {TRACK_SHARE:.0%} of >= {TRACK_MIN_MONTHS} months "
          f"and the same peak month +-1):")
    ver = stake_wallet_verdict(bal, series)
    for r in ver:
        err = "n/a" if r["median_err"] is None else f"{r['median_err']:.1%}"
        pw = r["peak_wallet"].date() if r["peak_wallet"] is not None else "n/a"
        ps = r["peak_series"].date() if r["peak_series"] is not None else "n/a"
        print(f"  {'TRACKS ' if r['tracks'] else '       '} {r['label']}: {r['months']} month(s), "
              f"{r['within']:.0%} within {TRACK_TOL:.0%}, median error {err}, peak {pw} vs series {ps}")
    hits = [r["label"] for r in ver if r["tracks"]]
    print("\n  RESULT: " + (f"{', '.join(hits)} tracks geod_total_stake — paste these lines back; it is wired "
                            f"as locked_tokens with a 365-day archive backfill, replacing the manual 3,000,000."
                            if hits else "NO candidate (nor their sum) tracks geod_total_stake — the manual "
                                         "3,000,000 stays. Paste these lines back."))


def morpho_incentives():
    """J: is Morpho paying MORPHO now? Live Merkl campaigns rewarding MORPHO on Ethereum, and the
    Merkl distributor's MORPHO balance."""
    head("MORPHO — live MORPHO reward campaigns (Merkl) and the distributor's balance")
    morpho = "0x58D97B57BB95320F9a05dC918Aef65434969c2B2"
    try:
        j = requests.get("https://api.merkl.xyz/v4/campaigns",
                         params={"tokenAddress": morpho, "chainId": 1, "status": "LIVE"},
                         headers=_ua(), timeout=TIMEOUT).json()
        rows = j if isinstance(j, list) else j.get("campaigns") or []
        print(f"  live campaigns paying MORPHO: {len(rows)}")
        for c in rows[:10]:
            print(f"    {str(c)[:200]}")
    except Exception as e:  # noqa: BLE001
        print(f"  Merkl API UNREACHABLE — {e}")
    from eth_utils import keccak                          # noqa: PLC0415
    sel = "0x" + keccak(text="balanceOf(address)").hex()[:8] + "3Ef3D8bA38EBe18DB133cEc108f4D14CE00Dd9Ae".lower().rjust(64, "0")
    w, _ = eth_call(morpho, sel)
    print(f"  MORPHO held by Merkl's distributor: {int(w, 16) / 1e18:,.0f}" if w else "  balance UNREACHABLE")
    print("  PASTE BACK: zero live campaigns + a flat balance means Jake's reading holds.")


def settlement_sources():
    """B: which settlement-volume sources answer, and on what terms (robots + a keyed call)."""
    head("SETTLEMENT VOLUME — Artemis and Visa Onchain Analytics reachability")
    key = os.environ.get("ARTEMIS_API_KEY", "").strip()
    try:
        r = requests.get("https://api.artemisxyz.com/asset", params={"APIKey": key} if key else {},
                         headers=_ua(), timeout=TIMEOUT)
        print(f"  Artemis /asset: HTTP {r.status_code} ({'keyed' if key else 'no ARTEMIS_API_KEY'})")
        if key and r.ok:
            try:
                j = r.json()
                print(f"    keyed body: {type(j).__name__}, keys {sorted(j)[:20] if isinstance(j, dict) else len(j)}")
            except ValueError:
                print(f"    keyed body is not JSON: {r.text[:200]!r}")
    except Exception as e:  # noqa: BLE001
        print(f"  Artemis UNREACHABLE — {e}")
    _xhr_capture("https://visaonchainanalytics.com/", ("adjusted", "Adjusted", "volume"))


def plume_settlement_routes():
    """Jake, 2026-10-01: `--seed plume_settlement` (chain-wide v2, ~21,755 pages) ran 1h+ unfinished.
    MEASURES, on this machine, what each route would cost for the year, before the seed switches:
      A  v2 chain-wide token-transfers (today's route): 50 rows a call, one cursor (sequential)
      B  token-scoped tokentx (Etherscan-compatible): 10,000 rows a call, in-scope tokens only,
         + eth_getCode once per address (JSON-RPC batches)
      C  eth_getLogs on rpc.plume.org for the in-scope tokens' Transfer events: the node's max block
         range is found by trying 1,000,000 / 100,000 / 10,000 / 1,000 blocks; + the same getCode
      N  native PLUME (no Transfer event): advanced-filters per day — pages for yesterday
    and the explorer's rate limit from its x-ratelimit-* headers, with and without BLOCKSCOUT_API_KEY
    (the key is SENT to explorer.plume.org once, in the query, to see if it applies; never printed).
    About 40-60 calls in all, each paced >= 0.3 s apart."""
    import statistics
    import config                                          # noqa: PLC0415
    import pandas as pd                                    # noqa: PLC0415
    from fetch.base import USER_AGENT                      # noqa: PLC0415
    head("PLUME settlement seed — what each route costs for a year (measured here)")
    spec = config.PROJECT_BY_NAME["Plume"]["settlement_rebuild"]
    base, cfg = spec["base"].rstrip("/"), spec["tokentx"]
    S = requests.Session()
    S.headers["User-Agent"] = USER_AGENT
    lat, seen_limits = {}, {}
    from fetch.base import query_params                    # noqa: PLC0415 — true/false/null, as Blockscout expects

    def call(kind, url, params=None, post=None, timeout=90):
        time.sleep(0.3)
        t = time.monotonic()
        r = (S.post(url, json=post, timeout=timeout) if post is not None
             else S.get(url, params=params, timeout=timeout))
        dt = time.monotonic() - t
        lat.setdefault(kind, []).append(dt)
        hs = {k.lower(): v for k, v in r.headers.items() if k.lower().startswith("x-ratelimit")}
        if hs:
            seen_limits[kind] = hs                       # the LAST answer's counters per endpoint
        return r, dt

    def limits(r):
        return {k: r.headers.get(k) for k in ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset")}

    # --- rate limit, with and without the key -------------------------------------------------
    r, dt = call("v2", f"{base}/api/v2/token-transfers", {"type": "ERC-20"})
    print(f"  v2 token-transfers: HTTP {r.status_code}, {dt:.2f}s, {len(r.content):,} bytes; limits {limits(r)}")
    sample = (r.json().get("items") or []) if r.ok else []
    key = os.environ.get("BLOCKSCOUT_API_KEY")
    if key:
        r2, dt2 = call("v2", f"{base}/api/v2/token-transfers", {"type": "ERC-20", "apikey": key})
        print(f"  ... with BLOCKSCOUT_API_KEY: HTTP {r2.status_code}, {dt2:.2f}s; limits {limits(r2)}")
        same = limits(r2).get("x-ratelimit-limit") == limits(r).get("x-ratelimit-limit")
        print("  KEY " + ("DOES NOT CHANGE the limit here — it is not an account key on this instance"
                          if same else "RAISES the limit here — it applies"))
    else:
        print("  BLOCKSCOUT_API_KEY not set — the keyed limit is not tested")
    nxt = r.json().get("next_page_params") if r.ok else None
    for _ in range(4):                                      # 250 transfers: the in-scope share
        if not nxt:
            break
        rr, _ = call("v2", f"{base}/api/v2/token-transfers", {"type": "ERC-20", **query_params(nxt)})
        if not rr.ok:
            break
        sample += rr.json().get("items") or []
        nxt = rr.json().get("next_page_params")
    # --- scope: tokens DefiLlama has ever priced ----------------------------------------------
    toks, params = [], {"type": "ERC-20"}
    for _ in range(int(cfg.get("scope_max_pages", 1000))):
        rr, _ = call("v2", f"{base}/api/v2/tokens", params)
        if not rr.ok:
            print(f"  token list: HTTP {rr.status_code}")
            break
        toks += [str(i.get("address_hash") or "").lower() for i in rr.json().get("items") or []]
        params = {"type": "ERC-20", **query_params(rr.json().get("next_page_params"))}
        if not rr.json().get("next_page_params"):
            break
    toks = sorted(set(t for t in toks if t) | {str((i.get("token") or {}).get("address_hash") or "").lower()
                                                 for i in sample} - {""})
    priced = set()
    for i in range(0, len(toks), 50):
        rr, _ = call("llama", f"{spec['price_api']}/prices/first/" + ",".join(f"{spec['chain_key']}:{a}" for a in toks[i:i + 50]))
        if rr.ok:
            priced |= {c.split(":", 1)[1].lower() for c in (rr.json().get("coins") or {})}
    share = (sum(str((i.get("token") or {}).get("address_hash") or "").lower() in priced for i in sample) / len(sample)
             if sample else None)
    per_day = 2_980                                          # Jake's measure, ERC-20 transfers a day
    print(f"  SCOPE: {len(priced)} of {len(toks)} candidate tokens DefiLlama has ever priced; "
          f"{share:.0%} of the last {len(sample)} transfers are in them" if share is not None else "  SCOPE: no sample")
    for t in sorted(priced)[:30]:
        rr, _ = call("v2", f"{base}/api/v2/tokens/{t}/counters")
        c = rr.json() if rr.ok else {}
        print(f"    {t}  all-time transfers {c.get('transfers_count')}, holders {c.get('token_holders_count')}")
    # --- B: one tokentx call over the last day for the busiest in-scope token -----------------
    rr, _ = call("api", f"{base}/api", {"module": "block", "action": "getblocknobytime",
                                         "timestamp": int(time.time()) - 86_400, "closest": "after"})
    b_day = int(((rr.json() or {}).get("result") or {}).get("blockNumber") or 0) if rr.ok else 0
    rr, _ = call("api", f"{base}/api", {"module": "block", "action": "getblocknobytime",
                                         "timestamp": int(time.time()) - 365 * 86_400, "closest": "after"})
    b_year = int(((rr.json() or {}).get("result") or {}).get("blockNumber") or 0) if rr.ok else 0
    busiest = max(priced, key=lambda t: sum(str((i.get("token") or {}).get("address_hash") or "").lower() == t
                                            for i in sample), default=None)
    tt_rows, addrs = [], set()
    if busiest and b_day:
        rr, dt = call("tokentx", f"{base}/api", {"module": "account", "action": "tokentx", "contractaddress": busiest,
                                                  "startblock": b_day, "endblock": 99_999_999_999, "sort": "asc",
                                                  "page": 1, "offset": int(cfg["offset"])}, timeout=180)
        tt_rows = (rr.json().get("result") or []) if rr.ok and isinstance(rr.json().get("result"), list) else []
        addrs = {str(x.get(k)).lower() for x in tt_rows for k in ("from", "to")}
        print(f"  B tokentx {busiest} last day: HTTP {rr.status_code}, {len(tt_rows):,} rows in {dt:.1f}s, "
              f"{len(rr.content) / 1e6:.1f} MB; {len(addrs):,} distinct addresses; limits {limits(rr)}")
    # --- getCode batch ------------------------------------------------------------------------
    rpc_url = spec["rpc"]
    batch = sorted(addrs)[:int(spec["rpc_batch"])] or ["0x" + "0" * 40]
    rr, dt = call("rpc", rpc_url, post=[{"jsonrpc": "2.0", "id": k, "method": "eth_getCode", "params": [a, "latest"]}
                                            for k, a in enumerate(batch)])
    ok_batch = rr.ok and isinstance(rr.json(), list) and all("result" in x for x in rr.json())
    print(f"  eth_getCode batch of {len(batch)} on {rpc_url.split('//')[1]}: HTTP {rr.status_code}, {dt:.2f}s, "
          f"{'ANSWERED' if ok_batch else 'REFUSED: ' + rr.text[:160]}")
    # --- C: eth_getLogs max range -------------------------------------------------------------
    rr, _ = call("rpc", rpc_url, post={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []})
    headb = int(rr.json()["result"], 16) if rr.ok and "result" in rr.json() else 0
    max_range, logs_per_block = None, None
    TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    for span in (1_000_000, 100_000, 10_000, 1_000):
        rr, dt = call("rpc", rpc_url, post={"jsonrpc": "2.0", "id": 1, "method": "eth_getLogs", "params": [{
            "fromBlock": hex(max(headb - span, 0)), "toBlock": hex(headb), "address": sorted(priced)[:50],
            "topics": [TRANSFER]}]}, timeout=120)
        j = rr.json() if rr.ok else {}
        if "result" in j:
            max_range, logs_per_block = span, len(j["result"]) / span
            print(f"  C eth_getLogs over {span:,} blocks: {len(j['result']):,} logs in {dt:.1f}s — ACCEPTED")
            break
        print(f"  C eth_getLogs over {span:,} blocks: REFUSED {str(j.get('error') or rr.text)[:160]}")
    # --- N: native, yesterday -----------------------------------------------------------------
    yday = (pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() - pd.Timedelta(days=1)).date()
    params, npages = {"transaction_types": "COIN_TRANSFER", "age_from": f"{yday}T00:00:00Z",
                      "age_to": f"{yday}T23:59:59Z"}, 0
    while npages < 60:
        rr, _ = call("v2", f"{base}/api/v2/advanced-filters", params)
        if not rr.ok:
            break
        npages += 1
        if not rr.json().get("next_page_params"):
            break
        params = {**params, **query_params(rr.json()["next_page_params"])}
    print(f"  N native PLUME {yday}: {npages}{'+' if npages >= 60 else ''} advanced-filters page(s)")
    # --- THE ESTIMATES -----------------------------------------------------------------------
    # PER-ENDPOINT LIMITS (Jake's run 2026-10-01: the "~0.36 h" for B ignored /api's 10 calls a window).
    # Blockscout's x-ratelimit-reset is MILLISECONDS to the end of a FIXED window (plug/rate_limit.ex),
    # so a reset is a LOWER BOUND on the window; its likely length is the next of 1 s / 1 min / 1 h / 1 d.
    def window_s(kind):
        hs = seen_limits.get(kind) or {}
        try:
            lim_, reset_ = int(hs["x-ratelimit-limit"]), float(hs["x-ratelimit-reset"]) / 1000
        except (KeyError, ValueError):
            return None, None, None
        if lim_ <= 0:
            return None, None, None                            # -1: unlimited
        likely = next(w for w in (1, 60, 3600, 86400, 10 ** 9) if w >= reset_)
        return lim_, reset_, likely

    def floor_h(kind, calls):
        """(hours at least, hours likely) that `calls` take under the endpoint's own limit."""
        lim_, low, likely = window_s(kind)
        if lim_ is None:
            return None
        return calls / lim_ * low / 3600, calls / lim_ * likely / 3600

    for kind in sorted(seen_limits):
        lim_, low, likely = window_s(kind)
        print(f"  limit {kind}: {seen_limits[kind]}" + (f" -> {lim_} per window of >= {low:,.0f}s (likely "
                                                        f"{likely:,}s)" if lim_ else ""))
    m = {k: statistics.median(v) for k, v in lat.items()}
    rate = float(spec["v2_rate_per_s"])
    lim = limits(r).get("x-ratelimit-limit")
    print(f"\n  median latency: " + ", ".join(f"{k} {v:.2f}s" for k, v in m.items()))
    print(f"  explorer limit header: {lim} (Blockscout's default is 300 per minute per IP = 5/s); pacing {rate}/s shared")
    year_v2 = per_day * 365 / 50
    a_h = year_v2 * m.get("v2", 1.0) / 3600
    fa = floor_h("v2", year_v2)
    a_h = max(a_h, fa[1]) if fa else a_h
    print(f"  A v2 chain-wide: {year_v2:,.0f} pages x {m.get('v2', 1):.2f}s, ONE cursor (sequential)"
          + (f", and the v2 limit allows them in >= {fa[0]:.1f} h" if fa else "") + f" = ~{a_h:.1f} h"
          + " (late March ran ~50x denser than this per-day rate)")
    blocks_year = headb - b_year if headb and b_year else None
    in_year = per_day * 365 * (share or 0)
    uniq = (len(addrs) / max(len(tt_rows), 1)) * in_year if tt_rows else None
    codes = (uniq or 0) / int(spec["rpc_batch"])
    if share is not None and "tokentx" in m:
        calls = in_year / (int(cfg["offset"]) - 1) + len(priced) * int(cfg["segments"])
        b_h = (calls * m["tokentx"] / int(cfg["workers"]) + codes * m.get("rpc", 0.5)) / 3600
        fb_ = floor_h("tokentx", calls + 2)            # + the two getblocknobytime calls, same /api
        print(f"  B tokentx: ~{in_year:,.0f} in-scope transfers/yr -> ~{calls:,.0f} calls x {m['tokentx']:.1f}s over "
              f"{cfg['workers']} workers + ~{codes:,.0f} getCode batches = ~{b_h:.2f} h of requests"
              + (f"; THE /api LIMIT makes it {fb_[0]:.1f}-{fb_[1]:.1f} h — RATE-LIMITED, not the default"
                 if fb_ else "; /api sent no limit headers"))
    if max_range and blocks_year:
        c_calls = blocks_year / max_range * max(1.0, (logs_per_block or 0) * max_range / 10_000)
        bis = 25 * -(-366 // int(spec["rpc_batch"]))     # day boundaries bisected in batches on the RPC
        c_h = (c_calls * m.get("rpc", 0.5) + codes * m.get("rpc", 0.5) + bis * m.get("rpc", 0.5)) / 3600
        fc = floor_h("rpc", c_calls + codes + bis)
        c_h = max(c_h, fc[1]) if fc else c_h
        print(f"  C eth_getLogs (THE ROUTE): {blocks_year:,} blocks a year / {max_range:,} per call -> ~{c_calls:,.0f} "
              f"calls (+ halvings in dense weeks) + ~{codes:,.0f} getCode batches + ~{bis} boundary batches = "
              f"~{c_h:.2f} h" + (f" (RPC limit headers {seen_limits['rpc']})" if "rpc" in seen_limits
                                  else " (the RPC sent no limit headers)"))
    n_h = npages * 365 * m.get("v2", 1.0) / 3600
    print(f"  N native: ~{npages * 365:,} pages a year: sequential ~{n_h:.1f} h; {spec['native_workers']} days side "
          f"by side at {rate}/s ~{max(npages * 365 / rate, npages * 365 * m.get('v2', 1) / spec['native_workers']) / 3600:.1f} h")
    print(f"  PASTE BACK all lines. The seed's ERC-20 route is {spec['erc20_route']!r} (config erc20_route).")


def near_activity_cause():
    """Jake, 2026-10-02: NEAR's activity fell ~74% from March to April 2026 in BigQuery AND NearBlocks.
    Which apps? Top RECEIVER accounts by transaction count, 2026-03-09..15 against 2026-04-06..12, from the
    transactions table — the receiver column is read from INFORMATION_SCHEMA first (metadata, free), the
    query is dry-run (free), and run through the adapter's own path: capped at its dry run, counted in the
    monthly ledger, and held if it would eat the month's top-up reserve (~16 GB expected). Prints each
    receiver whose activity disappeared and its share of the drop."""
    head("NEAR — what disappeared between 2026-03-09..15 and 2026-04-06..12 (top receivers by transactions)")
    try:
        _near_cause_receivers()
    except Exception as e:  # noqa: BLE001
        _near_reauth_or_raise(e)


def _near_reauth_or_raise(e: BaseException) -> None:
    """probes6 4: an expired Google login fails gracefully with the command to run."""
    from fetch.near_bigquery import REAUTH_ACTION, is_reauth   # noqa: PLC0415
    if not is_reauth(e):
        raise e
    print(f"  NOT RUN — Application Default Credentials need re-authentication (\"Reauthentication is needed\"): "
          f"{REAUTH_ACTION}, then `gcloud auth application-default set-quota-project near-data-510309` if ADC has "
          f"no quota project. Nothing was billed.")


def _near_cause_setup():
    """(nb, spec, client, dataset, columns) for the NEAR cause probes, or None (printed why)."""
    import config                                          # noqa: PLC0415
    from fetch.near_bigquery import NearBigQuery           # noqa: PLC0415
    spec = config.PROJECT_BY_NAME["Near"]["near_bigquery"]
    nb = NearBigQuery()
    client, why = nb._client(spec)
    if getattr(nb, "quota_reminder", None):
        print(f"  REMINDER: {nb.quota_reminder}")
    ds = spec.get("allowed_dataset", "bigquery-public-data.crypto_near_mainnet_us")
    meta = (f"SELECT column_name FROM `{ds}`.INFORMATION_SCHEMA.COLUMNS WHERE table_name = 'transactions' "
            f"ORDER BY ordinal_position")
    if client is None:
        print(f"  not run here: {why}. In the console (near-data-510309) run first:\n  {meta}")
        return None
    from google.cloud import bigquery                      # noqa: PLC0415
    nb._guard_sql(meta, spec)
    cols = [r["column_name"] for r in client.query(meta, job_config=bigquery.QueryJobConfig(
        maximum_bytes_billed=200 * 1024 ** 2)).result()]
    print(f"  transactions columns: {', '.join(cols)}")
    return nb, spec, client, ds, cols


_NEAR_CAUSE_WEEKS = {"a0": "2026-03-09", "a1": "2026-03-15", "b0": "2026-04-06", "b1": "2026-04-12"}


def _near_cause_receivers():
    import pandas as pd                                    # noqa: PLC0415
    from fetch.base import FetchOutput                     # noqa: PLC0415
    got = _near_cause_setup()
    if got is None:
        return
    nb, spec, client, ds, cols = got
    recv = next((c for c in cols if c in ("receiver_account_id", "transaction_receiver_account_id")), None) or \
        next((c for c in cols if "receiver" in c), None)
    if recv is None:
        print("  NO receiver column — paste back the column list")
        return
    sql = (f"WITH tx AS (SELECT block_date, {recv} AS receiver FROM `{ds}.transactions` "
           f"WHERE block_date BETWEEN @a0 AND @a1 OR block_date BETWEEN @b0 AND @b1), "
           f"agg AS (SELECT receiver, COUNTIF(block_date BETWEEN @a0 AND @a1) AS before_n, "
           f"COUNTIF(block_date BETWEEN @b0 AND @b1) AS after_n FROM tx GROUP BY receiver) "
           f"SELECT * FROM (SELECT receiver, before_n, after_n FROM agg ORDER BY before_n - after_n DESC LIMIT 40) "
           f"UNION ALL SELECT '__TOTAL__', SUM(before_n), SUM(after_n) FROM agg")
    params = {k: pd.Timestamp(v).date() for k, v in _NEAR_CAUSE_WEEKS.items()}
    st = nb._load()
    nb._run_bytes = nb._backfill_bytes = 0                 # the adapter's per-run counters (set in run())
    out = FetchOutput()
    est = nb._dry(client, sql, params, spec)
    print(f"  dry run (free): {est / 1e9:,.2f} GB")
    rows = nb._run(client, "near_activity_cause", sql, params, spec, st, out, "Near", backfill=True)
    for e in out.log:
        print(f"  {e.status}: {e.message}")
    if not rows:
        return
    tot = next(r for r in rows if r["receiver"] == "__TOTAL__")
    drop = int(tot["before_n"]) - int(tot["after_n"])
    print(f"\n  all receivers: {int(tot['before_n']):,} -> {int(tot['after_n']):,} transactions a week "
          f"(x{int(tot['after_n']) / max(int(tot['before_n']), 1):.2f}); the drop {drop:,}")
    print(f"  {'receiver':<48} {'Mar 9-15':>12} {'Apr 6-12':>12} {'lost':>12} {'share of drop':>14} {'cumulative':>11}")
    cum = 0
    for r in [r for r in rows if r["receiver"] != "__TOTAL__"][:25]:
        lost = int(r["before_n"]) - int(r["after_n"])
        cum += lost
        print(f"  {str(r['receiver'])[:48]:<48} {int(r['before_n']):>12,} {int(r['after_n']):>12,} {lost:>12,} "
              f"{lost / drop if drop else 0:>13.1%} {cum / drop if drop else 0:>10.1%}")
    print("  PASTE BACK all lines: the receivers explaining most of the drop name the programme that ended.")


def near_activity_signers():
    """Jake, 2026-10-02 — NEAR's drop, SECOND PASS (the first: top 25 receivers explain only 9.7%; Kai-Ching
    wallet.kaiching 1,110,074 -> 0 = 6.9%, game.hot.tg 1.5%). Same weeks, same budget path (dry run, capped,
    ledgered, held behind the top-up reserve; ~2 GB expected — three columns over two weeks): (1) top SIGNERS
    by transactions lost, with share of the drop; (2) the drop split by account TYPE for signers and receivers
    — implicit (64-hex), eth-implicit (0x + 40 hex), *.tg, *.near, Aurora (aurora / *.aurora), other; (3) distinct
    signers and receivers per week and the share of transactions where receiver = signer. Says which it is: a
    few senders, a class (e.g. implicit-account farming) or a broad decline."""
    head("NEAR — the drop by SIGNER, by account type, distinct accounts and self-transactions (second pass)")
    try:
        _near_cause_signers()
    except Exception as e:  # noqa: BLE001
        _near_reauth_or_raise(e)


def near_account_class_sql(col: str) -> str:
    return (f"CASE WHEN REGEXP_CONTAINS({col}, r'^[0-9a-f]{{64}}$') THEN 'implicit (64-hex)' "
            f"WHEN REGEXP_CONTAINS({col}, r'^0x[0-9a-f]{{40}}$') THEN 'eth-implicit (0x+40-hex)' "
            f"WHEN ENDS_WITH({col}, '.tg') THEN '*.tg' "
            f"WHEN {col} = 'aurora' OR ENDS_WITH({col}, '.aurora') THEN 'Aurora (aurora / *.aurora)' "
            f"WHEN ENDS_WITH({col}, '.near') THEN '*.near' ELSE 'other' END")


def near_cause_verdict(rows: list[dict]) -> str:
    """Which of the three it is, from the second pass's rows: A FEW SENDERS (top 10 signers >= 50% of the
    drop), A CLASS (one signer type >= 50% of the drop and it fell >= 1.5x as hard, in %, as everything else),
    else BROAD. The thresholds are stated in the verdict so Jake can judge the call."""
    tot = next(r for r in rows if r["section"] == "total")
    drop = int(tot["before_n"]) - int(tot["after_n"])
    if drop <= 0:
        return "NO DROP between the two weeks"
    top = sorted((r for r in rows if r["section"] == "signer"), key=lambda r: -(int(r["before_n"]) - int(r["after_n"])))
    top10 = sum(int(r["before_n"]) - int(r["after_n"]) for r in top[:10]) / drop
    types = [r for r in rows if r["section"] == "signer_type"]
    best = max(types, key=lambda r: int(r["before_n"]) - int(r["after_n"])) if types else None
    if top10 >= 0.5:
        return f"A FEW SENDERS: the top 10 signers explain {top10:.0%} of the drop"
    if best:
        lost = int(best["before_n"]) - int(best["after_n"])
        fall = 1 - int(best["after_n"]) / max(int(best["before_n"]), 1)
        rest_b = int(tot["before_n"]) - int(best["before_n"])
        rest_fall = 1 - (int(tot["after_n"]) - int(best["after_n"])) / max(rest_b, 1)
        if lost / drop >= 0.5 and fall >= 1.5 * max(rest_fall, 0.0001):
            return (f"A CLASS: {best['key']} signers carry {lost / drop:.0%} of the drop (they fell {fall:.0%}; "
                    f"everything else fell {rest_fall:.0%}); the top 10 signers explain only {top10:.0%} "
                    f"[rule: >= 50% of the drop and >= 1.5x the rest's fall]")
    return f"BROAD DECLINE: no few senders (top 10 signers = {top10:.0%}) and no single account type dominates"


def _near_cause_signers():
    import pandas as pd                                    # noqa: PLC0415
    from fetch.base import FetchOutput                     # noqa: PLC0415
    got = _near_cause_setup()
    if got is None:
        return
    nb, spec, client, ds, cols = got
    recv = next((c for c in cols if c in ("receiver_account_id", "transaction_receiver_account_id")), None) or \
        next((c for c in cols if "receiver" in c), None)
    sign = next((c for c in cols if c in ("signer_account_id", "transaction_signer_account_id")), None) or \
        next((c for c in cols if "signer" in c), None)
    if recv is None or sign is None:
        print(f"  NO {'receiver' if recv is None else 'signer'} column — paste back the column list")
        return
    sc, rc = near_account_class_sql("signer"), near_account_class_sql("receiver")
    sql = (f"WITH tx AS (SELECT block_date <= @a1 AS wk_a, {sign} AS signer, {recv} AS receiver "
           f"FROM `{ds}.transactions` WHERE block_date BETWEEN @a0 AND @a1 OR block_date BETWEEN @b0 AND @b1), "
           f"s AS (SELECT signer AS key, COUNTIF(wk_a) AS before_n, COUNTIF(NOT wk_a) AS after_n FROM tx GROUP BY signer) "
           f"SELECT * FROM (SELECT 'signer' AS section, key, before_n, after_n FROM s "
           f"ORDER BY before_n - after_n DESC LIMIT 40) "
           f"UNION ALL SELECT 'signer_type', {sc}, COUNTIF(wk_a), COUNTIF(NOT wk_a) FROM tx GROUP BY 2 "
           f"UNION ALL SELECT 'receiver_type', {rc}, COUNTIF(wk_a), COUNTIF(NOT wk_a) FROM tx GROUP BY 2 "
           f"UNION ALL SELECT 'distinct_signers', '', COUNT(DISTINCT IF(wk_a, signer, NULL)), "
           f"COUNT(DISTINCT IF(NOT wk_a, signer, NULL)) FROM tx "
           f"UNION ALL SELECT 'distinct_receivers', '', COUNT(DISTINCT IF(wk_a, receiver, NULL)), "
           f"COUNT(DISTINCT IF(NOT wk_a, receiver, NULL)) FROM tx "
           f"UNION ALL SELECT 'self', '', COUNTIF(wk_a AND signer = receiver), COUNTIF(NOT wk_a AND signer = receiver) "
           f"FROM tx "
           f"UNION ALL SELECT 'total', '', COUNTIF(wk_a), COUNTIF(NOT wk_a) FROM tx")
    params = {k: pd.Timestamp(v).date() for k, v in _NEAR_CAUSE_WEEKS.items()}
    st = nb._load()
    nb._run_bytes = nb._backfill_bytes = 0
    out = FetchOutput()
    est = nb._dry(client, sql, params, spec)
    print(f"  dry run (free): {est / 1e9:,.2f} GB — the run below spends that from the month's 900 GB budget "
          f"(held if it would eat the top-up reserve)")
    rows = nb._run(client, "near_activity_signers", sql, params, spec, st, out, "Near", backfill=True)
    for e in out.log:
        print(f"  {e.status}: {e.message}")
    if not rows:
        return
    sec = lambda name: [r for r in rows if r["section"] == name]   # noqa: E731
    tot = sec("total")[0]
    b, a = int(tot["before_n"]), int(tot["after_n"])
    drop = b - a
    print(f"\n  all transactions: {b:,} -> {a:,} a week (x{a / max(b, 1):.2f}); the drop {drop:,}")
    print(f"\n  (1) TOP SIGNERS by transactions lost\n  {'signer':<66} {'Mar 9-15':>11} {'Apr 6-12':>11} "
          f"{'lost':>11} {'share':>7} {'cum':>7}")
    cum = 0
    for r in sorted(sec("signer"), key=lambda r: -(int(r["before_n"]) - int(r["after_n"])))[:25]:
        lost = int(r["before_n"]) - int(r["after_n"])
        cum += lost
        print(f"  {str(r['key'])[:66]:<66} {int(r['before_n']):>11,} {int(r['after_n']):>11,} {lost:>11,} "
              f"{lost / drop if drop else 0:>6.1%} {cum / drop if drop else 0:>6.1%}")
    for name, title in (("signer_type", "(2a) SIGNERS by account type"), ("receiver_type", "(2b) RECEIVERS by account type")):
        print(f"\n  {title}\n  {'type':<30} {'Mar 9-15':>11} {'Apr 6-12':>11} {'fell':>7} {'lost':>11} {'share of drop':>14}")
        for r in sorted(sec(name), key=lambda r: -(int(r["before_n"]) - int(r["after_n"]))):
            lost = int(r["before_n"]) - int(r["after_n"])
            print(f"  {str(r['key']):<30} {int(r['before_n']):>11,} {int(r['after_n']):>11,} "
                  f"{1 - int(r['after_n']) / max(int(r['before_n']), 1):>6.0%} {lost:>11,} "
                  f"{lost / drop if drop else 0:>13.1%}")
    print("\n  (3) DISTINCT ACCOUNTS and SELF-TRANSACTIONS")
    for name, label in (("distinct_signers", "distinct signers"), ("distinct_receivers", "distinct receivers")):
        r = sec(name)[0]
        print(f"  {label:<22} {int(r['before_n']):>11,} -> {int(r['after_n']):>11,} "
              f"(x{int(r['after_n']) / max(int(r['before_n']), 1):.2f})")
    r = sec("self")[0]
    print(f"  receiver = signer      {int(r['before_n']) / max(b, 1):>10.1%} -> {int(r['after_n']) / max(a, 1):>10.1%} "
          f"of transactions ({int(r['before_n']):,} -> {int(r['after_n']):,})")
    print(f"\n  VERDICT: {near_cause_verdict(rows)}")
    print("  PASTE BACK all lines; the verdict goes to METHODOLOGY_FLAGS near_activity_cause beside the Kai-Ching "
          "finding.")


def _key_paths(obj, rx, path="", depth=0, out=None):
    """[(path, value or list shape)] for every key matching rx (6 levels), for pinning by key."""
    out = [] if out is None else out
    if depth > 6 or len(out) > 40:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p_ = f"{path}.{k}" if path else str(k)
            if rx.search(str(k)):
                if isinstance(v, list):
                    first = v[0] if v else None
                    out.append((p_, f"list[{len(v)}] first {json.dumps(first)[:200]} last {json.dumps(v[-1])[:120]}"
                                if v else "list[0]"))
                else:
                    out.append((p_, json.dumps(v)[:200]))
            _key_paths(v, rx, p_, depth + 1, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:50]):
            if isinstance(v, (dict, list)):
                _key_paths(v, rx, f"{path}.{i}", depth + 1, out)
            elif isinstance(v, str) and rx.search(v) and len(v) < 120:
                out.append((f"{path}.{i}", json.dumps(v) + f"  (string; siblings {json.dumps(obj[:6])[:200]})"))
    return out


def browser_captures():
    """Jake, 2026-10-02: the charts a dashboard draws in the browser — ASXN's Hyperliquid dashboard and
    Aethir's compute-hours and stake-duration charts. For each configured page: robots.txt (the verdict
    the adapter applies), the site's terms (an excerpt of any sentence about scraping, automated access or
    commercial use, from the usual terms paths), then ONE render with the timeframe tabs clicked: every JSON
    response (URL, keys, lists with their item keys), websocket frames, and the rendered text around the
    wanted labels. Paste back: the series are pinned from these lines; nothing is stored before."""
    import config                                          # noqa: PLC0415
    from fetch import browser_capture as bc                # noqa: PLC0415
    from fetch.base import USER_AGENT                      # noqa: PLC0415
    from fetch.scrape import robots_verdict                # noqa: PLC0415
    import re                                              # noqa: PLC0415
    import urllib.parse                                    # noqa: PLC0415
    labels = {"Hyperliquid": ("Transactions", "Active Addresses", "Daily Active", "HyperEVM", "HyperCore", "Users"),
              "Aethir": ("Weekly Compute Hours", "Total Compute Hours Delivered Last Week", "Average Stake Duration",
                         "AI Pool", "Gaming Pool")}
    # probes6: what each project's figures look like inside ANY body (JSON, RSC, text)
    wanted_re = {"Hyperliquid": r"transaction|txn|tx_count|active|users|addresses",
                 "Aethir": r"hour|duration"}
    stored = None
    try:                                                   # the unit check needs the stored figures
        import store as store_mod                          # noqa: PLC0415
        if os.path.exists(store_mod.DB_PATH):
            stored = store_mod.Store(store_mod.DB_PATH).load_long()
    except Exception as e:  # noqa: BLE001
        print(f"  (stored figures not loaded: {type(e).__name__})")
    pages = []
    for p in config.PROJECTS:
        for pg in (p.get("browser_capture") or {}).get("pages") or ():
            pages.append((p, pg))
            # probes6 2d: HyperEVM / HyperCore activity pages — rendered page loads, like the dashboard
            for u in pg.get("probe_urls") or ():
                pages.append((p, {**pg, "url": u, "series": (), "scalars": (), "tiles": (), "unit_check": None,
                                  "probe_only": True}))
    for p, pg in pages:
        if pg:
            head(f"{p['name']} — {pg['url']}" + ("  (probe only: is there activity data?)" if pg.get("probe_only") else ""))
            ok, why = robots_verdict(pg["url"])
            print(f"  robots.txt: {'ALLOWED' if ok else 'DISALLOWED'} — {why}")
            base = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(pg["url"]))
            host = urllib.parse.urlsplit(pg["url"]).netloc
            browser_only = (config.SOURCE_REGISTER.get(host) or {}).get("browser_only")
            if browser_only:
                print("  terms: not fetched — browser-only source, terms read by Jake (SOURCE_REGISTER)")
            for path in (() if browser_only or pg.get("probe_only") else
                         ("/terms", "/terms-of-service", "/terms-of-use", "/tos", "/legal")):
                try:
                    r = requests.get(base + path, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
                except Exception as e:  # noqa: BLE001
                    print(f"  terms {path}: {type(e).__name__}")
                    continue
                if r.status_code != 200:
                    continue
                txt = re.sub(r"<[^>]+>", " ", r.text)
                hits = [s.strip() for s in re.split(r"(?<=[.!?])\s+", txt)
                        if re.search(r"scrap|crawl|automated|robot|bot\b|commercial|reproduc", s, re.I)][:6]
                print(f"  terms {base + path}: HTTP 200; " + (" | ".join(h[:220] for h in hits) or
                                                             "no sentence on scraping or automated access"))
            if not ok:
                continue
            toggles = tuple(dict.fromkeys(t["toggle"] for t in pg.get("tiles") or () if t.get("toggle")))
            try:
                got = bc.capture(pg["url"], clicks=pg.get("clicks") or (), toggles=toggles)
            except Exception as e:  # noqa: BLE001
                print(f"  render failed: {type(e).__name__}: {str(e)[:200]}")
                continue
            for tg, txt in (got.get("texts") or {}).items():
                i = txt.find("Average Stake Duration")
                print(f"  after toggle \"{tg}\": " + (' '.join(txt[i:i + 160].split()) if i >= 0 else
                                                    "no \"Average Stake Duration\" text"))
            bodies = got.get("bodies") or []
            kinds = {}
            for _, ct, _b in bodies:
                kinds[ct.split(";")[0]] = kinds.get(ct.split(";")[0], 0) + 1
            print(f"  tabs clicked: {got['clicked'] or 'none found'}; {len(got['responses'])} JSON response(s) "
                  f"(RSC lines included); {len(bodies)} other body(ies) {kinds or ''}; {len(got['ws'])} websocket frame(s)")
            for line in bc.shapes(got["responses"])[:40]:
                print(f"    {line}")
            for u, frame in got["ws"][:5]:
                print(f"    WS {u[:100]}: {frame[:200]}")
            rx = re.compile(wanted_re.get(p["name"], r"$^"), re.I)
            for u, ct, b in bodies[:60]:
                m = rx.search(b)
                print(f"    BODY {ct.split(';')[0]:<22} {len(b):>9,} chars {u[:110]}"
                      + (f"  <- '{m.group(0)}': {' '.join(b[max(m.start() - 80, 0):m.end() + 160].split())[:260]}"
                         if m else ""))
            hits = [u for u, body in got["responses"] if rx.search(json.dumps(body)[:200_000])]
            print(f"  responses mentioning /{rx.pattern}/: {len(hits)}" + (f" — {'; '.join(h[:100] for h in hits[:8])}"
                                                                           if hits else ""))
            # probes7 4: the RSC lines / parsed objects around each hit, with their key paths and list shapes
            for u, ct, b in bodies:
                if "x-component" not in ct:
                    continue
                for m in list(rx.finditer(b))[:8]:
                    print(f"    RSC {u[:80]} @{m.start():,}: {' '.join(b[max(m.start() - 200, 0):m.end() + 400].split())[:600]}")
            for u, body in got["responses"]:
                if "#rsc:" not in u:
                    continue
                for path_, val in _key_paths(body, rx):
                    print(f"    RSC-KEY {u.split('#')[-1]} {path_}: {val}")
            # the pins, DRY — the adapter's own page logic on this render (nothing is stored by the probe)
            if pg.get("series") or pg.get("scalars") or pg.get("tiles"):
                import logging as _lg                          # noqa: PLC0415
                from fetch.base import FetchOutput             # noqa: PLC0415

                class _Due:
                    def due(self, *a):
                        return True

                    def done(self, *a):
                        pass

                class _Grab(_lg.Handler):
                    def emit(self, rec):
                        if "CROSS-CHECK" in rec.getMessage() or "unit check" in rec.getMessage():
                            print(f"  {rec.getMessage()}")
                h = _Grab()
                _lg.getLogger("token_metrics.fetch.browser_capture").addHandler(h)
                _lg.getLogger("token_metrics.fetch.browser_capture").setLevel(_lg.INFO)
                o = FetchOutput()
                try:
                    bc.BrowserCapture(capture_fn=lambda *a, **k: got, daily=_Due(), stored_long=stored)._page(
                        p["name"], {**pg, "alt_urls": ()}, o)
                finally:
                    _lg.getLogger("token_metrics.fetch.browser_capture").removeHandler(h)
                for e in o.log:
                    print(f"  DRY {e.status:<7} {e.message[:300]}")
                if stored is None:
                    print("  (no metrics.db here: unit checks against stored figures cannot decide)")
            for lab in labels.get(p["name"], ()):
                i = got["text"].find(lab)
                if i >= 0:
                    print(f"  text near \"{lab}\": {' '.join(got['text'][i:i + 200].split())}")
            print(f"  configured: permitted={pg.get('permitted')}, wanted {pg.get('wanted') or '(activity series)'}")
    print("\n  PASTE BACK all lines. Each series is pinned with its response URL, list path, date and value keys and "
          "its layer (HyperCore / HyperEVM for ASXN).")


def mev_relays():
    """Jake, 2026-10-02: the relays behind Ethereum's MEV leg — for each configured relay ONE data-API call:
    HTTP status, any rate-limit headers, rows, the newest slot; then the share of the last 200 slots that
    the covered relays delivered together (de-duplicated by slot). Terms are not published at the API; each
    relay's site is listed for Jake to read."""
    import config                                          # noqa: PLC0415
    from fetch.base import USER_AGENT                      # noqa: PLC0415
    from fetch.mev_relays import PATH, seed_cost           # noqa: PLC0415
    import socket                                          # noqa: PLC0415
    from urllib.parse import urlparse                      # noqa: PLC0415
    spec = config.PROJECT_BY_NAME["Ethereum"]["mev_relays"]
    head("Ethereum — MEV-Boost relays: data API, limits, coverage")
    slots, newest = {}, 0
    for r in spec["relays"]:
        try:
            resp = requests.get(r["url"] + PATH, params={"limit": int(r.get("page_limit", 200))},
                                headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        except Exception as e:  # noqa: BLE001
            # probes6: say WHICH failure — the name not resolving, or the host not answering
            host = urlparse(r["url"]).hostname
            try:
                dns = f"DNS {host} -> {socket.gethostbyname(host)}"
            except OSError as de:
                dns = f"DNS {host} does NOT resolve ({de})"
            print(f"  {r['name']:<22} {type(e).__name__}: {str(e)[:160]}; {dns}")
            continue
        lim = {k: v for k, v in resp.headers.items() if "ratelimit" in k.lower() or k.lower() == "retry-after"}
        rows = resp.json() if resp.ok else []
        s = [int(x["slot"]) for x in rows] if isinstance(rows, list) else []
        newest = max([newest] + s)
        for x in s:
            slots.setdefault(x, []).append(r["name"])
        print(f"  {r['name']:<22} HTTP {resp.status_code}; {len(s)} row(s), newest slot {max(s) if s else '-'}; "
              f"limit headers {lim or 'none'}; site {r['url']}")
        time.sleep(1)
    if newest:
        window = range(newest - 199, newest + 1)
        hit = sum(1 for x in window if x in slots)
        multi = sum(1 for x in window if len(slots.get(x, [])) > 1)
        print(f"\n  last 200 slots ({window.start}..{newest}): {hit} delivered by a covered relay ({hit / 200:.0%}, "
              f"missed slots included in the 200); {multi} slot(s) on more than one relay (counted once)")
        # probes6 1b: what a seed costs, from each relay's share of those 200 slots
        share = {r["name"]: sum(1 for x in window if r["name"] in slots.get(x, [])) / 200 for r in spec["relays"]}
        for days in (90, 365):
            c = seed_cost(spec, share, days, hit / 200)
            print(f"  SEED {days}d: ~{c['relay_calls']:,} relay calls ({c['relay_calls_per_day']}/day), ~{c['wall_h']:.1f} h "
                  f"wall (busiest {c['busiest']}, {c['wall_s_per_day']:.0f} s/day at "
                  f"{spec.get('relay_rate_per_s', 1)}/s per relay, side by side). Non-relay leg: ESTIMATE 0 extra "
                  f"calls; PER-BLOCK ~{c['nonrelay_blocks_per_day']:,} blocks/day = ~{c['per_block_rpc_calls']:,} RPC "
                  f"calls ({c['per_block_http_requests']:,} batched requests)")
        print("  offer: `python token_metrics.py --seed mev_relays --seed-days 90` first; a later full seed resumes")
    print(f"  not covered: {', '.join(spec['not_covered'])}")


# ======================================================================================
# SPOT CHECKS (Jake, 2026-10-05): OUR figure beside an INDEPENDENT reference fetched live.
# Read-only — nothing is stored. Each comparison states its tolerance; a reference that
# cannot be fetched automatically prints where to look by hand and is MANUAL, never PASS.
# A reference read from the SAME source as our figure checks only that the store is current:
# it is reported FRESH (or CHECK), and never counts as a PASS.
# ======================================================================================
SPOT_TOLERANCE_PCT = {
    # our latest stored day vs a live read: the HYPE price moves a few % within a day
    "hl_mcap_first_party": 5.0,
    "hl_mcap_asxn": 5.0,
    "hl_circulating": 0.5,          # first-party circulating changes slowly (emissions, unlocks)
    "aethir_arr_fresh": 1.0,
    "aethir_staked_fresh": 1.0,
    # Aethir's ARR is a run-rate whose definition is not published; our figure is the Q0
    # weekly revenue annualised — a different window by construction
    "aethir_revenue_vs_arr": 25.0,
    # DefiLlama's holders revenue is fee VALUE accrued; UNI is burned in fixed lots when the
    # Firepit threshold is met, so burns lag and track fees net of the burner's margin
    "uni_burn_vs_holders_revenue": 30.0,
    "pendle_shares": 1.0,
    "pendle_total_staked": 1.0,
    "pendle_last_epoch": 1.0,
    # token-terms yield over real + virtual sPENDLE (Q0 epochs) vs Pendle's last-epoch APR
    "pendle_yield_vs_published": 25.0,
    # ROUGH: allocation x 27.5/50 over a window that straddles Stage 2's start (2026-08-17)
    "sky_buyback_vs_allocation": 35.0,
}


def _num(v, unit=""):
    if v is None:
        return "n/a"
    a = abs(float(v))
    if unit == "%":
        return f"{float(v) * 100:.2f}%"
    for div, suf in ((1e12, "tn"), (1e9, "bn"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            s = f"{float(v) / div:,.2f}{suf}"
            break
    else:
        s = f"{float(v):,.2f}"
    return f"${s}" if unit == "$" else (f"{s} {unit}" if unit else s)


# ASCII-SAFE OUTPUT (Jake's run 2026-10-05: spot_checks died on "UnicodeEncodeError: 'charmap' codec
# can't encode character '\u2192'" — his console and Tee write cp1252). Every line this script prints
# passes through _AsciiStream: the symbols used here get ASCII spellings, anything else becomes '?'.
_ASCII = str.maketrans({"\u2014": "-", "\u2013": "-", "\u2026": "...", "\u2192": "->", "\u2190": "<-",
                        "\u00b1": "+/-", "\u00f7": "/", "\u00d7": "x", "\u201c": '"', "\u201d": '"',
                        "\u2018": "'", "\u2019": "'", "\u220b": " contains ", "\u2264": "<=", "\u2265": ">=",
                        "\u2248": "~", "\u00a0": " "})


def ascii_safe(text) -> str:
    return str(text).translate(_ASCII).encode("ascii", "replace").decode("ascii")


class _AsciiStream:
    def __init__(self, stream):
        self._s = stream

    def write(self, text):
        return self._s.write(ascii_safe(text))

    def __getattr__(self, name):
        return getattr(self._s, name)


class _Spot:
    """Collects one line per comparison and one verdict per item."""

    def __init__(self):
        self.items: dict = {}

    def _add(self, item, verdict, text):
        self.items.setdefault(item, []).append((verdict, text))

    def compare(self, item, label, ours, ref, tol_key, ref_from, unit="", same_source=False, ours_from=""):
        tol = SPOT_TOLERANCE_PCT[tol_key]
        if ours is None or ref is None or not ref:
            missing = "our figure" if ours is None else "the reference"
            print(f"  [MANUAL] {label}: {missing} unavailable — ours {_num(ours, unit)} vs {_num(ref, unit)} ({ref_from})")
            self._add(item, "MANUAL", f"{label}: {missing} unavailable")
            return None
        gap = (float(ours) - float(ref)) / abs(float(ref)) * 100
        ok = abs(gap) <= tol
        verdict = ("FRESH" if ok else "CHECK") if same_source else ("PASS" if ok else "CHECK")
        print(f"  [{verdict}] {label}: ours {_num(ours, unit)}{f' ({ours_from})' if ours_from else ''} vs "
              f"{_num(ref, unit)} — {ref_from}; gap {gap:+.1f}% (tolerance ±{tol:g}%)"
              + ("  [same source: checks the store is current, not the figure]" if same_source else ""))
        self._add(item, verdict, f"{label} {gap:+.1f}% (±{tol:g}%)")
        return gap

    def recorded(self, item, label, ours, ref, tol_key, ref_from, unit=""):
        """A BY-HAND reading on record (config), compared with the store: "PASS (manual record)" — the
        reference was read by a person, not fetched by this run, and the line says so."""
        tol = SPOT_TOLERANCE_PCT[tol_key]
        if ours is None:
            print(f"  [MANUAL] {label}: no stored value for it here — on record: {_num(ref, unit)} ({ref_from})")
            self._add(item, "MANUAL", f"{label}: not in the store here")
            return
        gap = (float(ours) - float(ref)) / abs(float(ref)) * 100
        verdict = "PASS" if abs(gap) <= tol else "CHECK"
        print(f"  [{verdict} (manual record)] {label}: ours {_num(ours, unit)} vs {_num(ref, unit)} — {ref_from}; "
              f"gap {gap:+.2f}% (tolerance ±{tol:g}%)")
        self._add(item, verdict, f"{label} {gap:+.2f}% (manual record)")

    def manual(self, item, label, ours, where, unit=""):
        print(f"  [MANUAL] {label}: ours {_num(ours, unit)} — check by hand: {where}")
        self._add(item, "MANUAL", f"{label}: by hand — {where}")

    def info(self, text):
        print(f"           {text}")

    def summary(self):
        head("SPOT CHECKS — SUMMARY (PASS needs an independent reference within tolerance)")
        for item, rows in self.items.items():
            vs = [v for v, _ in rows]
            verdict = ("CHECK" if "CHECK" in vs else "PASS" if "PASS" in vs else "MANUAL")
            print(f"  {verdict:<6} {item}: " + "; ".join(f"{v} {t}" for v, t in rows))


def _spot_ours():
    """(long frame, {"Project|metric": aggregate row}, asof) — the figures the workbook shows, built from
    the store exactly as build_workbook does. (None, {}, asof) without a metrics.db."""
    import pandas as pd                                    # noqa: PLC0415
    asof = pd.Timestamp.now("UTC").tz_localize(None).normalize()
    try:
        import store as store_mod                          # noqa: PLC0415
        if not os.path.exists(store_mod.DB_PATH):
            print(f"  no {store_mod.DB_PATH} here — our figures are unavailable; references still print")
            return None, {}, asof
        import build_workbook as bw                        # noqa: PLC0415
        st = store_mod.Store(store_mod.DB_PATH)
        try:
            long = st.load_long()
            data = bw.aggregate(long, st.fetch_status(), asof, gaps=st.gap_report(), review=st.review_queue())
        finally:
            st.close()
        return long, {r["key"]: r for r in data.to_dict("records")}, asof
    except Exception as e:  # noqa: BLE001
        print(f"  our figures not loaded: {type(e).__name__}: {e}")
        return None, {}, asof


def chainlink_revenue_coverage():
    """A7 (overnight 2026-10-06): Chainlink's customer revenue over the full year — per component, how many of the
    last 365 complete days are stored (a day missing from ANY component is refused from the sum); then Q0, Q0
    annualised over its covered days, and free float / ARR. From metrics.db only; stores nothing."""
    import pandas as pd                                    # noqa: PLC0415
    import config                                          # noqa: PLC0415
    head("CHAINLINK — customer revenue: full-year coverage, Q0, annualised, free float / ARR")
    long, rows, asof = _spot_ours()
    if long is None:
        return
    spec = config.PROJECT_BY_NAME["Chainlink"]["customer_revenue_components"]
    comps = (spec["core"], *spec["plus"])
    end = asof - pd.Timedelta(days=1)
    days = pd.date_range(end - pd.Timedelta(days=364), end)
    have = {}
    g = long[long.project == "Chainlink"]
    for m in comps:
        s = g[g.metric == m]
        have[m] = set(s["date"].dt.normalize())
        miss = [d for d in days if d not in have[m]]
        print(f"  {m:<44} {len(days) - len(miss):>4} of 365 days"
              + (f"; missing {len(miss)}: first {miss[0].date()}, last {miss[-1].date()}" if miss else " — complete"))
    full = [d for d in days if all(d in have[m] for m in comps)]
    print(f"  ALL components present on {len(full)} of 365 days" + ("" if len(full) == 365 else
          " — the summed series refuses the other days (a partial day is never stored)"))
    r = rows.get("Chainlink|customer_revenue_usd") or {}
    q0, cov = _row(rows, "Chainlink|customer_revenue_usd", "q0"), _row(rows, "Chainlink|customer_revenue_usd",
                                                                       "q0_covered_days")
    if q0 is None:
        print("  Q0: not computed (customer_revenue_usd has no Q0 value)")
        return
    cov = cov or 90.0
    arr = q0 * 365.0 / cov
    print(f"  Q0 customer revenue: ${q0:,.0f} over {cov:.0f} covered day(s) (latest {r.get('latest_date')})")
    print(f"  annualised (ARR): ${arr:,.0f}  = Q0 x 365 / {cov:.0f}")
    circ, lock, px = (_row(rows, "Chainlink|circulating_supply"), _row(rows, "Chainlink|locked_tokens"),
                      _row(rows, "Chainlink|price_usd"))
    if None in (circ, lock, px) or not arr:
        print("  free float / ARR: not computed (circulating, locked or price missing)")
        return
    ff = (circ - lock) * px
    print(f"  free float: ({circ:,.0f} circulating - {lock:,.0f} staked) x ${px:,.4f} = ${ff:,.0f}")
    print(f"  FREE FLOAT / ARR: {ff / arr:,.1f}x")
    print("  Nothing was stored.")


def _row(rows, key, field="now"):
    r = rows.get(key) or {}
    v = r.get(field)
    try:
        return None if v is None or v != v else float(v)
    except (TypeError, ValueError):
        return None


def _polite(url, method="GET", **kw):
    """(parsed JSON or text, why) after the pipeline's own robots.txt verdict for the URL."""
    from fetch.scrape import robots_verdict                # noqa: PLC0415
    ok, why = robots_verdict(url)
    if not ok:
        return None, f"robots.txt disallows {url} ({why})"
    try:
        r = (requests.post(url, headers=_ua(), timeout=TIMEOUT, **kw) if method == "POST"
             else requests.get(url, headers=_ua(), timeout=TIMEOUT, **kw))
    except Exception as e:  # noqa: BLE001
        return None, f"{urlparse(url).netloc}: {type(e).__name__}"
    if r.status_code != 200:
        return None, f"{urlparse(url).netloc}: HTTP {r.status_code}"
    try:
        return r.json(), ""
    except ValueError:
        return r.text, ""


def _render_once(url):
    """(rendered text, why) — ONE normal page load in the browser (fetch/browser_capture.capture),
    after robots.txt; for pages whose figures exist only once rendered."""
    from fetch.scrape import robots_verdict                # noqa: PLC0415
    ok, why = robots_verdict(url)
    if not ok:
        return None, f"robots.txt disallows {url} ({why})"
    try:
        from fetch import browser_capture as bc            # noqa: PLC0415
        cap = bc.capture(url)
    except Exception as e:  # noqa: BLE001
        return None, f"browser route unavailable ({type(e).__name__}: {str(e)[:120]})"
    return cap.get("text") or "", ""


_MULT = {"k": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9, "t": 1e12, "tn": 1e12}


def _figure_after(text, label, window=80):
    """($ figure, the snippet) for the first amount printed within `window` characters after `label`."""
    import re                                              # noqa: PLC0415
    m = re.search(re.escape(label), text or "", re.I)
    if not m:
        return None, ""
    snip = text[m.start(): m.end() + window].replace("\n", " ")
    n = re.search(r"\$?\s*([\d][\d,]*(?:\.\d+)?)\s*(bn|billion|million|mn|tn|[kmbt])?\b", snip[len(label):], re.I)
    if not n:
        return None, snip
    v = float(n.group(1).replace(",", ""))
    return v * _MULT.get((n.group(2) or "").lower(), 1.0), snip


def _spot_hyperliquid(spot, rows):
    import config                                          # noqa: PLC0415
    item = "a) Hyperliquid market cap"
    head(item)
    ours = _row(rows, "Hyperliquid|market_cap_usd")
    when = (rows.get("Hyperliquid|market_cap_usd") or {}).get("latest_date")
    price = _row(rows, "Hyperliquid|price_usd")
    circ = _row(rows, "Hyperliquid|circulating_supply_first_party")
    spot.info(f"ours = price x first-party circulating (build_workbook _market_cap_views): "
              f"{_num(price, '$')} x {_num(circ)} HYPE = {_num(ours, '$')} as of {when}")
    info = "https://api.hyperliquid.xyz/info"
    meta, why = _polite(info, "POST", json={"type": "spotMeta"})
    tid = None
    if isinstance(meta, dict):
        tid = next((t.get("tokenId") for t in meta.get("tokens") or () if t.get("name") == "HYPE"), None)
    td, why2 = (_polite(info, "POST", json={"type": "tokenDetails", "tokenId": tid}) if tid else (None, why or "HYPE not in spotMeta"))
    if isinstance(td, dict):
        c_live = float(td.get("circulatingSupply") or 0) or None
        px_key = next((k for k in ("markPx", "midPx", "prevDayPx") if td.get(k) not in (None, "")), None)
        px = float(td[px_key]) if px_key else None
        spot.compare(item, "first-party circulating", circ, c_live, "hl_circulating",
                     "Hyperliquid tokenDetails.circulatingSupply, live", unit="HYPE", same_source=True)
        ref = c_live * px if (c_live and px) else None
        spot.compare(item, "market cap", ours, ref, "hl_mcap_first_party",
                     f"Hyperliquid tokenDetails circulatingSupply x {px_key or 'markPx'} ({_num(px, '$')}), live "
                     f"— our price is CoinGecko's, the circulating is the same first-party figure", unit="$",
                     ours_from=str(when))
    else:
        spot.manual(item, "market cap vs tokenDetails", ours,
                    f"tokenDetails unreadable ({why2}); app.hyperliquid.xyz → HYPE token page: circulating x mark price",
                    unit="$")
    # ASXN — rendered page loads only; its pages carry no market-cap pin in config
    pg = next((pg for p in config.PROJECTS if p["name"] == "Hyperliquid"
               for pg in (p.get("browser_capture") or {}).get("pages") or ()), None)
    url = (pg or {}).get("url", "https://hyperscreener.asxn.xyz")
    text, why = _render_once(url)
    v, snip = _figure_after(text, "Market Cap") if text else (None, "")
    if v:
        spot.info(f"ASXN rendered text: “{snip[:120]}”")
        spot.compare(item, "market cap vs ASXN", ours, v, "hl_mcap_asxn", f"{url}, rendered once", unit="$",
                     ours_from=str(when))
    else:
        spot.manual(item, "market cap vs ASXN", ours,
                    (f"{why}; " if why else "no 'Market Cap' figure in the rendered page; ")
                    + f"open {url} and look for a Market Cap tile (ASXN: internal check only, no public use "
                      f"without ASXN's permission)", unit="$")
    # CoinGecko: shown with the reason it differs, never as the reference
    cg, why = _polite("https://api.coingecko.com/api/v3/coins/markets",
                      params={"vs_currency": "usd", "ids": "hyperliquid"})
    if isinstance(cg, list) and cg:
        c = cg[0]
        spot.info(f"CoinGecko (context, not a reference): market cap {_num(c.get('market_cap'), '$')} = price "
                  f"{_num(c.get('current_price'), '$')} x ITS circulating {_num(c.get('circulating_supply'))} HYPE")
        if circ and c.get("circulating_supply") and ours and c.get("market_cap"):
            spot.info(f"  why it differs: its circulating is {c['circulating_supply'] / circ:.1%} of the first-party "
                      f"{_num(circ)}; its market cap is {c['market_cap'] / ours:.1%} of ours — the gap is the "
                      f"circulating basis, not the price")
    else:
        spot.info(f"CoinGecko market cap not read ({why})")


def _spot_aethir(spot, rows):
    from fetch.aethir_pages import key_scalar              # noqa: PLC0415
    item = "b) Aethir ARR and total staked"
    head(item)
    base = "https://dashboard.aethir.com/protocol/"
    demand, why_d = _polite(base + "demand-metric")
    chain, why_c = _polite(base + "onchain-metric")
    arr = key_scalar(demand, "arr")[0] if isinstance(demand, str) else None
    staked = key_scalar(chain, "totalStaked")[0] if isinstance(chain, str) else None
    if arr is None:
        spot.info(f"dashboard arr not read: {why_d or key_scalar(demand or '', 'arr')[1]}")
    if staked is None:
        spot.info(f"dashboard totalStaked not read: {why_c or key_scalar(chain or '', 'totalStaked')[1]}")
    spot.compare(item, "ARR (stored) vs dashboard arr", _row(rows, "Aethir|arr_usd"), arr, "aethir_arr_fresh",
                 base + "demand-metric `arr`, read now", unit="$", same_source=True)
    spot.compare(item, "locked_tokens vs dashboard totalStaked", _row(rows, "Aethir|locked_tokens"), staked,
                 "aethir_staked_fresh", base + "onchain-metric `totalStaked`, read now", unit="ATH", same_source=True)
    q0 = _row(rows, "Aethir|customer_revenue_usd", "q0")
    cov = _row(rows, "Aethir|customer_revenue_usd", "q0_covered_days") or 90.0
    ann = q0 * 365.0 / cov if q0 is not None and cov else None
    spot.info(f"ours: weekly customer revenue, Q0 sum {_num(q0, '$')} over {cov:.0f} covered day(s), x365/{cov:.0f} "
              f"= {_num(ann, '$')}/yr")
    spot.compare(item, "customer revenue annualised vs dashboard arr", ann, arr, "aethir_revenue_vs_arr",
                 "the dashboard's own run-rate (definition not published) — a different derivation from the "
                 "weekly series, both Aethir's own figures", unit="$")
    if arr is None:
        spot.manual(item, "ARR", _row(rows, "Aethir|arr_usd"),
                    "dashboard.aethir.com → Protocol → Demand Metric, the ARR tile", unit="$")


def _spot_uniswap(spot, rows, asof, long=None):
    import pandas as pd                                    # noqa: PLC0415
    import config                                          # noqa: PLC0415
    item = "c) Uniswap burn (last 90 days, 100M one-off excluded)"
    head(item)
    q0 = _row(rows, "Uniswap|gross_burn_tokens", "q0")
    cov = _row(rows, "Uniswap|gross_burn_tokens", "q0_covered_days") or 90.0
    start = asof - pd.Timedelta(days=90)
    one = next((f for f in (config.PROJECT_BY_NAME["Uniswap"].get("one_off_flows") or ())
                if f.get("metric") == "gross_burn_tokens"), {})
    w = one.get("window") or ("", "")
    inside = bool(w[0]) and pd.Timestamp(w[1]) > start
    spot.info(f"ours: UNI to 0x…dEaD (on-chain, archive reads), window {start.date()}..{asof.date()} — Q0 "
              f"{_num(q0, 'UNI')} over {cov:.0f} covered day(s) = {_num(q0 * 365 / cov if q0 else None, 'UNI')}/yr; "
              f"one-off window {w[0]}..{w[1]} is {'INSIDE (removed by the one-off view)' if inside else 'outside'} it")
    fees, why = _polite("https://api.llama.fi/summary/fees/uniswap", params={"dataType": "dailyHoldersRevenue"})
    t0 = int(start.timestamp())
    px, why_p = _polite(f"https://coins.llama.fi/chart/coingecko:uniswap", params={"start": t0, "span": 92, "period": "1d"})
    if not (isinstance(fees, dict) and isinstance(px, dict)):
        spot.manual(item, "UNI burned vs holders revenue", q0,
                    f"DefiLlama not read ({why or why_p}); defillama.com/protocol/uniswap → Holders Revenue, last 90d, "
                    f"÷ the UNI price", unit="UNI")
        return
    prices = {pd.Timestamp(int(p["timestamp"]), unit="s").normalize(): float(p["price"])
              for p in ((px.get("coins") or {}).get("coingecko:uniswap") or {}).get("prices") or ()}
    usd = uni = 0.0
    days, priced = 0, []
    for ts, v in fees.get("totalDataChart") or ():
        d = pd.Timestamp(int(ts), unit="s").normalize()
        if start < d <= asof:
            days += 1
            usd += float(v or 0)
            p = prices.get(d)
            if p:
                uni += float(v or 0) / p
                priced.append(d)
    unpriced = days - len(priced)
    spot.info(f"reference: DefiLlama Uniswap holders revenue {_num(usd, '$')} over {days} day(s), each day ÷ that "
              f"day's UNI price (coins.llama.fi) = {_num(uni, 'UNI')} over {len(priced)} priced day(s)"
              + (f"; {unpriced} day(s) unpriced, left out" if unpriced else ""))
    if not priced:
        spot.manual(item, "UNI burned (90d)", q0, "no priced day in the window; defillama.com/protocol/uniswap -> "
                                                  "Holders Revenue, last 90d, / the UNI price", unit="UNI")
        return
    # LIKE WITH LIKE (Jake, 2026-10-05): our burn over the SAME priced days, from the stored daily rows
    # (one-off removed as the workbook removes it); without them, the reference is scaled up instead.
    ours_days = None
    if long is not None:
        g = long[(long.project == "Uniswap") & (long.metric == "gross_burn_tokens")]
        daily = g.groupby(g["date"].dt.normalize())["value"].sum()
        if len(daily):
            if inside and one.get("tokens"):
                win = daily[(daily.index >= pd.Timestamp(w[0])) & (daily.index <= pd.Timestamp(w[1]))]
                big = win[win >= float(one["tokens"])]
                if len(big) == 1:
                    daily.loc[big.index[0]] -= float(one["tokens"])
            ours_days = float(daily.reindex(priced).fillna(0.0).sum())
    if ours_days is not None:
        spot.info(f"ours over the same {len(priced)} priced day(s): {_num(ours_days, 'UNI')}")
        spot.compare(item, f"UNI burned ({len(priced)} priced days of 90)", ours_days, uni,
                     "uni_burn_vs_holders_revenue",
                     "DefiLlama dailyHoldersRevenue / same-day UNI price, the same days (an independent source: our "
                     "burn is an on-chain read)", unit="UNI")
    else:
        scaled = uni * cov / len(priced)
        spot.info(f"our daily rows are not in the store here: the reference is SCALED x{cov:.0f}/{len(priced)} to our "
                  f"{cov:.0f} covered days = {_num(scaled, 'UNI')}")
        spot.compare(item, "UNI burned (90d)", q0, scaled, "uni_burn_vs_holders_revenue",
                     f"DefiLlama dailyHoldersRevenue / same-day UNI price over {len(priced)} priced day(s), scaled "
                     f"x{cov:.0f}/{len(priced)}", unit="UNI")


def _spot_pendle(spot, rows, long):
    import pandas as pd                                    # noqa: PLC0415
    import config                                          # noqa: PLC0415
    from fetch.pendle_epochs import fitting_scale          # noqa: PLC0415
    item = "d) Pendle staking"
    head(item)
    spec = config.PROJECT_BY_NAME["Pendle"]["spendle_epochs"]
    api, why = _polite(spec["url"])
    shares = _row(rows, "Pendle|locked_tokens_shares")
    assets = _row(rows, "Pendle|locked_tokens")
    legacy = _row(rows, "Pendle|locked_tokens_legacy_vependle")
    virtual = _row(rows, "Pendle|locked_tokens_virtual")
    spot.info(f"ours (chain reads): sPENDLE assets {_num(assets, 'PENDLE')}, shares {_num(shares)}, legacy vePENDLE "
              f"{_num(legacy, 'PENDLE')}, virtual {_num(virtual)}")
    if not isinstance(api, dict):
        spot.manual(item, "staking totals", shares, f"{spec['url']} not read ({why}); app.pendle.finance → sPENDLE: "
                                                    f"'Total PENDLE Staked'")
        return
    scal = {k: v for k, v in api.items() if not isinstance(v, (list, dict))}
    spot.info("API scalars: " + ", ".join(f"{k}={str(v)[:24]}" for k, v in scal.items()))
    t = api.get("totalStakedInSpendle")
    spot.compare(item, "sPENDLE shares vs totalStakedInSpendle", shares, float(t) / 1e18 if t is not None else None,
                 "pendle_shares", "Pendle spendle/data API (18 decimals, as sources.yaml reads it) — independent of "
                 "our totalSupply() read", unit="sPENDLE")
    hub = api.get("totalPendleStaked")
    ours_hub = (shares + legacy) if shares is not None and legacy is not None else None
    if hub is not None:
        spot.compare(item, "shares + legacy vePENDLE vs totalPendleStaked", ours_hub, float(hub) / 1e18,
                     "pendle_total_staked", "spendle/data totalPendleStaked, READ AS 18 DECIMALS like "
                     "totalStakedInSpendle (assumed; a gap near a power of ten means it is not)", unit="PENDLE")
    else:
        spot.manual(item, "shares + legacy vePENDLE (the hub's ~93.9M)", ours_hub,
                    "no totalPendleStaked in the API answer; app.pendle.finance → sPENDLE: 'Total PENDLE Staked'",
                    unit="PENDLE")
    # last epoch: our stored epoch vs the API's own lastEpochBuybackAmount, at the scale the adapter fits
    try:
        k, done, _med, tried = fitting_scale(api, spec, pd.Timestamp.now("UTC").tz_localize(None))
    except (ValueError, KeyError, TypeError) as e:          # an unreadable epoch history: say so, go on
        k, tried = None, [f"epoch history unreadable: {e}"]
    ours_last = None
    if long is not None:
        s = long[(long.project == "Pendle") & (long.metric == spec["metric"])].sort_values("date")
        ours_last = float(s["value"].iloc[-1]) if len(s) else None
        if len(s):
            spot.info(f"our last stored epoch: {s['date'].iloc[-1].date()} {_num(ours_last, 'PENDLE')}")
    lb = api.get("lastEpochBuybackAmount")
    try:
        lb = float(lb) if lb is not None else None
    except (TypeError, ValueError):
        lb = None
    if lb == 0:
        lb = None
        spot.info("lastEpochBuybackAmount reads 0 in the API answer (as on Jake's run of 2026-10-05)")
    if lb is not None and k is not None:
        spot.compare(item, "last epoch distributed vs lastEpochBuybackAmount", ours_last, float(lb) / 10 ** k,
                     "pendle_last_epoch", f"spendle/data lastEpochBuybackAmount at the scale the epoch series fits "
                     f"(10^{k}; tried {tried})", unit="PENDLE", same_source=True)
    else:
        spot.manual(item, "last epoch", ours_last,
                    ("no usable lastEpochBuybackAmount in the API answer" if lb is None else f"no scale fits the epoch series ({tried})")
                    + "; app.pendle.finance -> sPENDLE: 'Last Epoch Distribution'", unit="PENDLE")
    # BY-HAND CHECKS ON RECORD (config spendle_epochs.manual_checks): the stored epoch vs Jake's reading
    for mc in spec.get("manual_checks") or ():
        mine = None
        if long is not None:
            s_ = long[(long.project == "Pendle") & (long.metric == spec["metric"])]
            hit = s_[s_["date"].dt.normalize() == pd.Timestamp(mc["epoch"])]
            mine = float(hit["value"].iloc[-1]) if len(hit) else None
        spot.recorded(item, f"epoch {mc['epoch']} distributed vs '{mc['field']}'", mine, float(mc["value"]),
                      "pendle_last_epoch", f"{mc['source']}, read {mc['read_on']} by {mc['read_by']}",
                      unit="PENDLE")
    # token-terms yield, exactly as the workbook's _token_yield builds it
    q0 = _row(rows, f"Pendle|{spec['metric']}", "q0")
    ev = _row(rows, f"Pendle|{spec['metric']}", "q0_events")
    stake = (shares or 0) + (virtual or 0)
    y = (q0 / ev) * 365.25 / spec["epoch_days"] / stake if q0 and ev and stake else None
    apr = api.get(spec["apr_field"])
    try:
        apr = float(apr) if apr is not None else None
    except (TypeError, ValueError):
        apr = None
    if not apr:
        hist = (api.get(spec["history_key"]) or {}).get(spec["aprs_field"]) or []
        apr = next((float(a) for a in reversed(hist) if a), None)
        spot.info("lastEpochApr is 0 or absent — the last non-zero entry of the aprs history is used")
    spot.info(f"ours: token yield = (Q0 {_num(q0, 'PENDLE')} / {ev or 0:.0f} epochs) x 365.25/{spec['epoch_days']} ÷ "
              f"(shares + virtual {_num(stake)}) = {_num(y, '%')}")
    spot.compare(item, "token-terms yield vs Pendle's published APR", y, apr, "pendle_yield_vs_published",
                 "spendle/data lastEpochApr (a fraction) — Pendle's own APR, one epoch", unit="%")


def _spot_sky(spot, rows, long, asof):
    import pandas as pd                                    # noqa: PLC0415
    import config                                          # noqa: PLC0415
    item = "e) Sky buybacks (flapper Exec)"
    head(item)
    usd = _row(rows, "Sky|actual_buyback_usd", "q0")
    tok = _row(rows, "Sky|actual_buyback_tokens", "q0")
    spot.info(f"ours (SKY into the Pause Proxy in flapper Exec transactions), {(asof - pd.Timedelta(days=90)).date()}.."
              f"{asof.date()}: {_num(tok, 'SKY')}, {_num(usd, '$')} at same-day prices")
    page = ((config.PROJECT_BY_NAME["Sky"].get("net_protocol_surplus_reference") or {})
            .get("financials_page_2026_09_29") or {})
    xc = page.get("a3_buyback_cross_check") or {}
    share = float(xc.get("sky_buyback_share") or 27.5 / 50)
    url = page.get("url", "https://financial.skyeco.com/financials/revenue")
    text, why = _render_once(url)
    alloc, snip = _figure_after(text, "Revenue Allocation") if text else (None, "")
    if alloc:
        spot.info(f"financial.skyeco.com rendered once: “{snip[:120]}” — CONFIRM the page's window reads 90 days")
        spot.compare(item, "SKY bought (USD, 90d) vs allocation x 27.5/50", usd, alloc * share,
                     "sky_buyback_vs_allocation", f"{url} Revenue Allocation {_num(alloc, '$')} x {share:.3f} "
                     f"(ROUGH: the window straddles Stage 2's start)", unit="$")
    else:
        last = xc.get("allocation_usd")
        spot.info(f"financial.skyeco.com not read live ({why or 'no Revenue Allocation figure in the rendered text'}); "
                  f"Jake's reading of {page.get('read_on', '?')}: allocation {_num(last, '$')} x {share:.3f} = "
                  f"{_num(last * share if last else None, '$')} — not live, so not a PASS")
        spot.manual(item, "SKY bought (USD, 90d) vs Revenue Allocation", usd,
                    f"{url} → 'Revenue Allocation', last 90 days; implied SKY buyback = it x 27.5/50", unit="$")
    # Sky's announced monthly figures: no automatic route — our months, to compare by hand
    if long is not None:
        s = long[(long.project == "Sky") & (long.metric.isin(["actual_buyback_tokens", "actual_buyback_usd"]))]
        if len(s):
            m = (s.assign(month=s["date"].dt.to_period("M")).pivot_table(index="month", columns="metric",
                                                                           values="value", aggfunc="sum").tail(4))
            for mon, r in m.iterrows():
                spot.info(f"  ours {mon}: {_num(r.get('actual_buyback_tokens'), 'SKY')}, "
                          f"{_num(r.get('actual_buyback_usd'), '$')}")
    spot.manual(item, "monthly SKY bought vs Sky's announced figures", tok,
                "Sky's own monthly buyback posts (forum.sky.money, Stage 2 / Smart Burn Engine updates) — "
                "info.skyeco.com disallows robots; compare the months above", unit="SKY")


def spot_checks():
    """Jake, 2026-10-05: OUR figure (the store, through build_workbook's own views) beside an INDEPENDENT
    reference fetched live, the % gap, and PASS / CHECK against a stated tolerance (SPOT_TOLERANCE_PCT).
    robots.txt first everywhere; the browser route only for a page that needs rendering, once. A
    reference that cannot be fetched prints where to look by hand: MANUAL, never PASS. Stores nothing."""
    spot = _Spot()
    long, rows, asof = _spot_ours()
    for fn, args in ((_spot_hyperliquid, (spot, rows)), (_spot_aethir, (spot, rows)),
                     (_spot_uniswap, (spot, rows, asof, long)), (_spot_pendle, (spot, rows, long)),
                     (_spot_sky, (spot, rows, long, asof))):
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001 — one item failing must not stop the rest
            print(f"  {fn.__name__} FAILED: {type(e).__name__}: {e}")
            spot._add(fn.__name__, "MANUAL", f"probe failed: {type(e).__name__}")
    spot.summary()
    print("  Nothing was stored. PASTE BACK the whole section.")


CHECKS = (
    sky_chainlog, sky, morpho_blue_api,
    sky_splitter, sky_splitter_params, sky_splitter_history,
    solana, injective, near, etherfi_sethfi,
    maple_dao_multisig, pendle_spendle_virtual, pendle_compounding_ledger, aerodrome_lock_inputs,
    uniswap_firepit_threshold, near_buyback_inflow_probe,
    fluid_buyback_destination, aethir_staking_probe, aethir_wrapper_relationship,
    aethir_veaethir_probe, geodnet_staking_candidates, geod_candidate_wallets, sky_lssky_releases, geod_residual, alchemy_base_logs, near_buyback_wallets, sky_ba_endpoints, chainlink_revenue_coverage,
    maple_transparency, sky_burn_breakdown, geod_solana_burn_account, near_block_supply,
    wm_cardano_supply, etherscan_ethsupply2, geod_archive_probe, plume_growthepie,
    chainlink_reward_rates, pendle_spendle_fees, archive_probe, coinmetrics_community,
    hl_af_fills_depth, etherfi_safe_owners, etherfi_sethfi_topups, etherfi_topup_safe, etherfi_cex_test, near_protocol_v87, aethir_pin_keys,
    robots_and_terms, ultrasound_history, hyperliquid_history_routes,
    plume_sources, aethir_dashboard_xhr, maple_ssf_history, blockworks_geodnet,
    morpho_incentives, settlement_sources, hyperevm_etherscan, maple_ssf_inflows, aethir_pages,
    geod_stake_recipient, geod_stake_wallets, maple_ssf_lp_test, maple_drips, plume_archive, settlement_rebuild_coverage,
    near_settlement_routes,
    aethir_mint_path,
    near_activity_break,
    plume_settlement_routes,
    near_activity_cause, near_activity_signers,
    browser_captures,
    mev_relays,
    spot_checks,
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
    # NO KEY IN ANY OUTPUT (2026-09-29: this probe printed an Alchemy key inside an HTTPError's
    # "for url: ..."). .env first so its values are known secrets, then every print and log
    # line passes through fetch.base.redact.
    try:
        from dotenv import load_dotenv                    # noqa: PLC0415
        load_dotenv()
    except Exception:  # noqa: BLE001
        pass
    for name in ("stdout", "stderr"):                     # cp1252 consoles: ASCII only (2026-10-05)
        if not isinstance(getattr(sys, name), _AsciiStream):
            setattr(sys, name, _AsciiStream(getattr(sys, name)))
    from fetch.base import install_redaction              # noqa: PLC0415
    install_redaction()

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("check", nargs="?", default=None,
                    help="run only this check — its exact name, or an unambiguous prefix "
                         "(e.g. 'maple_transparency'). Omit to run everything.")
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
