#!/usr/bin/env python3
"""
check_offline_items.py — every check this sandbox cannot reach, in one command.

    python check_offline_items.py

The build environment's proxy blocks chain RPCs, Cosmos LCDs and beaconcha.in, so a handful of
questions have been stacking up unanswered. This runs all of them from a machine that has real
network, and prints results in a form that can be pasted straight back.

Reads only. It touches no contract state, writes nothing to the store, and needs no API key.
"""
from __future__ import annotations

import json
import os
import sys
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
    tables = [SELECTORS, SELECTORS_SPLITTER, SELECTORS_SPLITTER_PARAMS,
              {"list()": CHAINLOG_LIST, "getAddress(bytes32)": CHAINLOG_GET,
               "totalSupply()": SEL_TOTAL_SUPPLY, "balanceOf(address)": SEL_BALANCE_OF,
               "decimals()": SEL_DECIMALS, "threshold()": SEL_THRESHOLD}]
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


def eth_block_number():
    """The current block, so a pair of reads can be pinned to ONE state rather than two."""
    for url in ETH_RPCS:
        try:
            j = rpc(url, "eth_blockNumber")
            if "result" in j:
                return j["result"], url
        except Exception:  # noqa: BLE001
            continue
    return None, None


def eth_call(to: str, selector: str, block: str = "latest"):
    """Try each endpoint until one answers. Returns (result_hex, endpoint) or (None, error).

    `block` pins the read. Two calls at "latest" can straddle a block boundary, which for a
    ratio of two figures is the difference between a measurement and a coincidence.
    """
    errors = []
    for url in ETH_RPCS:
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
        rows.append((int(lg["blockNumber"], 16), name, raw, lg["blockNumber"]))
    rows.sort()
    print(f"\n  {'block':>10}  {'date':<12} {'what':<8} {'raw':>22}  reading")
    for blk, name, raw, blk_hex in rows:
        when = block_time(blk_hex)
        reading = (f"{raw / 1e18:.6f} WAD = {raw / 1e16:.2f}% to buybacks" if name == "burn"
                   else f"{raw} s = {raw / 3600:.2f} h" if name == "hop" else "")
        print(f"  {blk:>10,}  {when:<12} {name:<8} {raw:>22}  {reading}")
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
    head("BEACONCHA.IN — reachability of the free tier (Ethereum validator issuance)")
    try:
        r = requests.get("https://beaconcha.in/api/v1/epoch/latest", timeout=TIMEOUT)
        print(f"  HTTP {r.status_code}  {r.text[:200]}")
        if r.ok:
            print("  reachable without a key — usable for validator issuance")
    except Exception as e:  # noqa: BLE001
        print(f"  UNREACHABLE — {e}")


def main():
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--splitter", default=SKY_SPLITTER,
                    help="Sky's Splitter address, to confirm which flapper is live and to read "
                         "its burn/hop parameters and their File history")
    ap.add_argument("--splitter-from-block", type=int, default=SKY_SPLITTER_FROM_BLOCK,
                    help="first block to scan for the Splitter's File events. Set it at or "
                         "BEFORE deployment — a history that starts too late is not a shorter "
                         "history, it is a wrong one")
    args = ap.parse_args()

    print("check_offline_items.py — running every check the build sandbox cannot reach.")
    print("Paste the whole output back.")
    for fn in (sky_chainlog, sky, morpho_blue_api, lambda: sky_splitter(args.splitter),
               lambda: sky_splitter_params(args.splitter),
               lambda: sky_splitter_history(args.splitter, args.splitter_from_block),
               solana, injective, near, etherfi_sethfi,
               maple_dao_multisig, pendle_spendle_virtual, uniswap_firepit_threshold,
               beaconchain):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — one failure must not stop the rest
            print(f"\n  {getattr(fn, '__name__', 'check')} FAILED: {e}")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
