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
import sys

import requests

TIMEOUT = 25

# Sky's Splitter/Flapper. The 2024 executive vote set this flapper; SBEBeam has allowed
# reconfiguration since, so the point is to check whether it is STILL this one.
SKY_FLAPPER = "0x374D9c3d5134052Bc558F432Afa1df6575f07407"
ETH_RPCS = ["https://eth.llamarpc.com", "https://ethereum-rpc.publicnode.com", "https://rpc.ankr.com/eth"]
# keccak("receiver()")[:4] — SwapOnly exposes the address it sends bought SKY to.
SELECTORS = {"receiver()": "0xf7260d3e", "pair()": "0xa8aa1b31", "want()": "0x1f1c827f",
             "pip()": "0xd741e2f9", "spotter()": "0xf3701da2"}


def head(title: str):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def rpc(url: str, method: str, params=None):
    r = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []},
                      timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def eth_call(to: str, selector: str):
    """Try each endpoint until one answers. Returns (result_hex, endpoint) or (None, error)."""
    errors = []
    for url in ETH_RPCS:
        try:
            j = rpc(url, "eth_call", [{"to": to, "data": selector}, "latest"])
            if "result" in j:
                return j["result"], url
            errors.append(f"{url}: {j.get('error')}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url}: {e}")
    return None, "; ".join(errors)


def as_address(word: str | None) -> str | None:
    if not word or len(word) < 42:
        return None
    body = word[2:] if word.startswith("0x") else word
    return "0x" + body[-40:]


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
    print("check_offline_items.py — running every check the build sandbox cannot reach.")
    print("Paste the whole output back.")
    for fn in (sky, solana, injective, near, beaconchain):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — one failure must not stop the rest
            print(f"\n  {fn.__name__} FAILED: {e}")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
