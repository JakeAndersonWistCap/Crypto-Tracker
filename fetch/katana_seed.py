"""
fetch/katana_seed.py — MORPHO'S KATANA CLAIMS, THE WHOLE HISTORY (Round G3 item 1(d), Jake 2026-10-18).

The routine read of `merkl_morpho_out_katana` is a 100-day RPC window (G2-D1e: no explorer serves Katana). This one-off
reads the Distributor's MORPHO Transfer history on Katana from block 0 to the head through the chain's own RPC
(fetch.chain.rpc_endpoints("katana"): rpc.katana.network), chunked (1,000,000 blocks a call; a server that states a
smaller range is read — ChainReader._get_logs_resilient), with state saved after EVERY chunk in
.cache/seed_katana_claims.json so a stopped seed resumes at the next block. The stall breaker applies (fetch.chain
.BREAKER): a stalled chain saves state and stops.

Nothing is stored until the history RECONCILES at the head to the wei: balanceOf(holder, head) = Σ in − Σ out over
every Transfer since block 0. Then the counted outflow (to anyone but the holders and the declared excluded
counterparties) is stored per UTC day from the first event's day to yesterday, zeros observed, under the routine scan's
own measuring point (explorer:merkl_morpho_out_katana) — the same series, further back.
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from pathlib import Path

import pandas as pd

import config

from .base import FetchOutput, tidy, today
from .logscan import CONFIRMATIONS, SOURCE, TIER, TRANSFER_TOPIC, _amount, pad_address, topic_address

log = logging.getLogger("token_metrics.fetch.katana_seed")

KEY = "merkl_morpho_out_katana"
CHUNK = 1_000_000


def state_path() -> Path:
    return Path(os.environ.get("TOKEN_METRICS_LOGCACHE", ".cache/logscan")).parent / "seed_katana_claims.json"


def _spec() -> tuple[dict, dict]:
    p = config.PROJECT_BY_NAME["Morpho"]
    return p, next(s for s in p["log_scans"] if s["key"] == KEY)


def _norm(e) -> dict:
    hx = lambda v: v.hex() if hasattr(v, "hex") else str(v)  # noqa: E731
    h = hx(e["transactionHash"])
    return {"blockNumber": int(e["blockNumber"]), "transactionHash": h if h.startswith("0x") else "0x" + h,
            "logIndex": int(e["logIndex"]),
            "topics": [t if t.startswith("0x") else "0x" + t for t in (hx(t) for t in e["topics"])],
            "data": (lambda d: d if d.startswith("0x") else "0x" + d)(hx(e["data"]))}


def _load(path: Path, key: str) -> dict:
    try:
        st = json.loads(path.read_text())
        if st.get("key") == key:
            return st
    except (OSError, ValueError):
        pass
    return {"key": key, "scanned_to": -1, "ins": [], "outs": [], "ts": {}}


def _save(path: Path, st: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(path)


def run(reader, out: FetchOutput, path: Path | None = None, chunk: int = CHUNK) -> dict:
    """Read, save, reconcile, store. Returns {"status": "stored" | "stopped" | "unreconciled", ...}."""
    from .chain import ChainStalled, redact_urls
    p, spec = _spec()
    name, chain, token = p["name"], spec["chain"], spec["token"]
    holders = [h.lower() for h in spec["holders"]]
    path = path or state_path()
    st = _load(path, KEY)
    head = int(reader.block_number(chain)) - CONFIRMATIONS
    blk = int(st["scanned_to"]) + 1
    calls = 0
    while blk <= head:
        try:
            got_in, got_out, upper = [], [], head
            for h in holders:
                logs, up, chunk = reader._get_logs_resilient(
                    chain, {"address": reader.checksum(token), "topics": [TRANSFER_TOPIC, None, pad_address(h)]},
                    blk, upper, chunk)
                got_in += [_norm(e) for e in logs]
                upper = min(upper, up)
                logs, up, chunk = reader._get_logs_resilient(
                    chain, {"address": reader.checksum(token), "topics": [TRANSFER_TOPIC, pad_address(h), None]},
                    blk, upper, chunk)
                got_out += [_norm(e) for e in logs]
                upper = min(upper, up)
                calls += 2
        except ChainStalled as e:
            _save(path, st)
            msg = (f"--seed katana_claims: {chain} STALLED at block {blk:,} of {head:,} — {redact_urls(e)}. State saved "
                   f"to {path.name} (scanned to {st['scanned_to']:,}); re-run to resume.")
            out.fail(SOURCE, name, msg, TIER)
            return {"status": "stopped", "why": msg}
        except Exception as e:  # noqa: BLE001 — saved and stopped, never half-stored
            _save(path, st)
            msg = (f"--seed katana_claims: stopped at block {blk:,} of {head:,} — {type(e).__name__}: "
                   f"{redact_urls(e)}. State saved (scanned to {st['scanned_to']:,}); re-run to resume.")
            out.fail(SOURCE, name, msg, TIER)
            return {"status": "stopped", "why": msg}
        # a chunk is kept only to the narrower of the two reads' ends, so in and out always cover the same blocks
        st["ins"] += [e for e in got_in if e["blockNumber"] <= upper]
        st["outs"] += [e for e in got_out if e["blockNumber"] <= upper]
        st["scanned_to"] = upper
        _save(path, st)
        log.info("--seed katana_claims: blocks %s..%s read (%d in, %d out so far; chunk %s); state saved",
                 f"{blk:,}", f"{upper:,}", len(st["ins"]), len(st["outs"]), f"{chunk:,}")
        blk = upper + 1
    # RECONCILE AT THE HEAD, TO THE WEI, over the whole history from block 0
    uniq = lambda evs: list({(e["transactionHash"], e["logIndex"]): e for e in evs}.values())  # noqa: E731
    ins, outs = uniq(st["ins"]), uniq(st["outs"])
    internal = set(holders)
    net = sum(_amount(e) for e in ins if topic_address(e["topics"][1]) not in internal) \
        - sum(_amount(e) for e in outs if topic_address(e["topics"][2]) not in internal)
    c = reader.erc20(chain, token)
    bal = sum(int(c.functions.balanceOf(reader.checksum(h)).call(block_identifier=int(st["scanned_to"])))
              for h in holders)
    if bal != net:
        msg = (f"--seed katana_claims: the history does NOT reconcile at block {st['scanned_to']:,}: in-out {net} wei vs "
               f"balanceOf {bal} wei (difference {bal - net}). Nothing stored.")
        out.fail(SOURCE, name, msg, TIER)
        return {"status": "unreconciled", "why": msg}
    excluded = {a.lower() for a in spec.get("exclude_counterparties") or []}
    counted = [e for e in outs if topic_address(e["topics"][2]) not in internal | excluded]
    dec = int(c.functions.decimals().call())
    blocks = sorted({e["blockNumber"] for e in counted})
    log.info("--seed katana_claims: reconciled; dating %s payout(s) in %s distinct block(s) (block times read only where "
             "a day boundary can fall; progress every 30 s)", f"{len(counted):,}", f"{len(blocks):,}")
    day_of = date_blocks(blocks, lambda b: _ts(reader, chain, st, b), say=lambda m: log.info("--seed katana_claims: %s", m))
    log.info("--seed katana_claims: dated %s block(s); %d block time(s) cached", f"{len(day_of):,}", len(st["ts"]))
    _save(path, st)
    by_day = defaultdict(float)
    for e in counted:
        by_day[day_of[e["blockNumber"]]] += _amount(e) / 10 ** dec
    first_blk = min([e["blockNumber"] for e in ins + outs], default=None)
    if first_blk is None:
        out.log.append(_entry(name, "--seed katana_claims: reconciled, and the holder has no Transfer at all"))
        return {"status": "stored", "rows": 0}
    k0 = str(first_blk)
    if k0 not in st["ts"]:
        st["ts"][k0] = int(reader.block_timestamp(chain, first_blk))
    d0 = pd.Timestamp(st["ts"][k0], unit="s").normalize()
    days = pd.date_range(d0, today() - pd.Timedelta(days=1), freq="D")
    msg = (f"{spec['metric']}: --seed katana_claims RECONCILED to the wei at block {st['scanned_to']:,} — balanceOf "
           f"{bal / 10 ** dec:,.4f} = in − out over {len(ins)} + {len(outs)} Transfer(s) since block 0; "
           f"{len(counted)} payout(s); stored {sum(by_day.get(d, 0.0) for d in days):,.4f} MORPHO over "
           f"{d0.date()}..{days[-1].date() if len(days) else '-'} ({len(days)} day(s), zeros observed; today's "
           f"{sum(v for d, v in by_day.items() if d > days[-1]) if len(days) else 0:,.4f} waits for the day to close); "
           f"read by {', '.join(sorted(set(reader.log_endpoint_used.values())))}")
    out.add(tidy([(d, by_day.get(d, 0.0)) for d in days], name, spec["metric"], f"{SOURCE}:{KEY}", TIER), SOURCE, name,
            msg, TIER)
    return {"status": "stored", "rows": len(days), "total": sum(by_day.get(d, 0.0) for d in days)}


def _ts(reader, chain: str, st: dict, block: int) -> int:
    k = str(int(block))
    if k not in st["ts"]:
        st["ts"][k] = int(reader.block_timestamp(chain, int(block)))
    return st["ts"][k]


def date_blocks(blocks: list[int], ts, say=None, every: float = 30.0) -> dict[int, pd.Timestamp]:
    """{block: its UTC day} for sorted `blocks`, asking ts(block) only where a day boundary can fall: timestamps never
    decrease with the block number, so when the first and last block of a run share a day every block between does too
    (~ days x log2(events a day) calls instead of one per block). `say(msg)` reports progress every `every` seconds
    (G4 item 4: this phase ran ~16 minutes with no output)."""
    import time as _time
    out: dict[int, pd.Timestamp] = {}
    day = lambda b: pd.Timestamp(ts(b), unit="s").normalize()  # noqa: E731
    stack = [(0, len(blocks) - 1)] if blocks else []
    t0 = last = _time.monotonic()
    while stack:
        if say and _time.monotonic() - last >= every:
            last = _time.monotonic()
            say(f"dating payouts: {len(out):,} of {len(blocks):,} block(s) dated ({len(out) / max(1, len(blocks)):.0%}), "
                f"{len(stack)} range(s) left, {last - t0:.0f}s in")
        i, j = stack.pop()
        di, dj = day(blocks[i]), day(blocks[j])
        if di == dj:
            for b in blocks[i:j + 1]:
                out[b] = di
        elif j - i <= 1:
            out[blocks[i]], out[blocks[j]] = di, dj
        else:
            m = (i + j) // 2
            stack += [(i, m), (m + 1, j)]
    return out


def _entry(name, msg):
    from .base import LogEntry
    return LogEntry(SOURCE, name, 0, "ok", msg, TIER)
