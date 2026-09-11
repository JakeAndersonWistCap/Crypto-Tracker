"""
tests/report_contracts.py — the contract verification worklist.

    python tests/report_contracts.py

Prints every contract entry in config.py with its purpose, address, chain and verification
status, so each can be checked against the protocol's own documentation one at a time.
Nothing here touches the network.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402


def main() -> int:
    rows = []
    for p in config.PROJECTS:
        for key, c in (p.get("contracts") or {}).items():
            rows.append((p["name"], key, c))

    print("=" * 132)
    print("CONTRACT VERIFICATION WORKLIST")
    print("=" * 132)
    print(f"{'PROJECT':<13} {'ENTRY':<32} {'CHAIN':<10} {'STATUS':<12} ADDRESS")
    print("-" * 132)
    for name, key, c in rows:
        if c.get("ambiguous"):
            status, addr = "AMBIGUOUS", f"{len(c['candidates'])} candidates — none chosen"
        else:
            status = "verified" if c.get("verified") else "UNVERIFIED"
            addr = c.get("address") or "-"
        print(f"{name:<13} {key:<32} {str(c.get('chain')):<10} {status:<12} {addr}")

    print()
    print("=" * 132)
    print("DETAIL — work through these one at a time")
    print("=" * 132)
    for name, key, c in rows:
        print(f"\n{name} / {key}")
        print(f"  purpose     : {c.get('purpose') or '-'}")
        if c.get("ambiguous"):
            print("  status      : AMBIGUOUS — nothing is read until one address is confirmed")
            for i, cand in enumerate(c.get("candidates") or [], 1):
                print(f"    candidate {i}: {cand}")
        else:
            print(f"  address     : {c.get('address')}")
            print(f"  status      : {'verified ' + str(c['verified']) if c.get('verified') else 'UNVERIFIED — refused by default'}")
        print(f"  chain       : {c.get('chain')}  (EVM adapter covers: {'yes' if c.get('chain') in config.EVM_CHAINS else 'NO — needs its own adapter'})")
        print(f"  expects sym : {c.get('expected_symbol')}   (checked on-chain before any value is accepted)")
        print(f"  source URL  : {c.get('source_url') or '-'}")
        print(f"  provenance  : {c.get('provenance')}")
        if c.get("note"):
            print(f"  note        : {c['note']}")

    print()
    print("=" * 132)
    print("BURN MECHANISM BY PROJECT — transfer burn and protocol burn are NOT interchangeable")
    print("=" * 132)
    print(f"{'PROJECT':<13} {'METHOD':<16} NOTE")
    print("-" * 132)
    for p in config.PROJECTS:
        method = p.get("burn_read_method")
        if method is None:
            continue
        note = (p.get("burn_read_note") or "")[:96]
        print(f"{p['name']:<13} {method:<16} {note}")

    unverified = sum(1 for _, _, c in rows if not c.get("verified") and not c.get("ambiguous"))
    ambiguous = sum(1 for _, _, c in rows if c.get("ambiguous"))
    non_evm = sum(1 for _, _, c in rows if c.get("chain") not in config.EVM_CHAINS)
    print()
    print(f"TOTAL {len(rows)} entries — {unverified} unverified, {ambiguous} ambiguous, "
          f"{non_evm} on chains the EVM adapter cannot read. "
          f"Verified and readable right now: "
          f"{sum(1 for _, _, c in rows if c.get('verified') and not c.get('ambiguous') and c.get('chain') in config.EVM_CHAINS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
