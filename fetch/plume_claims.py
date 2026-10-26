"""
fetch/plume_claims.py — Plume's points-airdrop claims, by campaign, over rpc.plume.org (Round G18 item 2).

Jake 2026-10-26 (G18 2c): count ONLY the Nest Season One and Plume Season 2 campaigns, by the campaign ids the official
claim pages carry (config.PLUME_CLAIM["campaigns"]), filtered on the claim event's own campaign id — Hedgey's
ClaimCampaigns contract (0x8A2725a6…) is shared, so a Transfer scan of its WPLUME out counted every campaign on it.
The G17 scan also read explorer.plume.org's /api, whose 10 calls per >= 40-minute window it spent, and plume_staking's
payouts read (the host's existing reader) failed for the first time (G18 2a/2b). This reads the chain's own RPC, which
answers eth_getLogs over any block span up to 10,000 logs a call (measured 2026-10-26) — explorer.plume.org is not
called at all.

THE EVENTS (Hedgey ClaimCampaigns, verified on explorer.plume.org, solc 0.8.28; github.com/hedgey-finance):
  UnlockedTokensClaimed(bytes16 indexed id, address indexed claimer, uint256 amountClaimed, uint256 amountRemaining)
  CampaignStarted(bytes16 indexed id, Campaign campaign, uint256 totalClaimers)   data word 2 = the initial amount
  CampaignCancelled(bytes16 indexed id)
  campaigns(bytes16) -> (manager, token, amount, start, end, tokenLockup, root, delegating); amount = what remains
The id is topic1: the UUID's 16 bytes, left-aligned in 32. Every campaign here has tokenLockup 0: a claim is a plain
WPLUME transfer to the claimer (nothing locked or vested).

RECONCILED TO THE WEI, PER CAMPAIGN, AT ONE PINNED BLOCK: initial amount − Σ claimed == campaigns(id).amount (and ==
the last claim's amountRemaining). A campaign that does not reconcile, or whose start event is not found, stores
NOTHING for the series and says which. Read incrementally: the state (per-campaign totals, per-day sums, the block read
to) is kept beside the log cache and advanced only after a reconciled read. Stored: plume_airdrop_claims_tokens per
UTC day from the first campaign's start to yesterday — a day without a claim is a measured 0.
"""
from __future__ import annotations

import json
import logging
import os
import time as _time
from pathlib import Path

import pandas as pd

import config

from .base import Http, today, tidy

log = logging.getLogger("token_metrics.fetch.plume_claims")

SOURCE = "plume_claims"
TIER = 2
METRIC = "plume_airdrop_claims_tokens"
CONFIRMATIONS = 20
LOG_CAP_CODE = -32005                       # "logs count limit exceeded (10000)"


def campaign_topic(uuid: str) -> str:
    h = uuid.replace("-", "").lower()
    if len(h) != 32 or any(c not in "0123456789abcdef" for c in h):
        raise ValueError(f"not a campaign uuid: {uuid!r}")
    return "0x" + h + "0" * 32


def _words(data: str) -> list[int]:
    d = data[2:] if data.startswith("0x") else data
    return [int(d[i:i + 64], 16) for i in range(0, len(d), 64)]


class LogCapExceeded(RuntimeError):
    pass


class PlumeClaims:
    def __init__(self, http: Http | None = None, state_file=None, **_ignored):
        self.http = http or Http(min_interval=0.1, retries=2, timeout=30)
        root = Path(os.environ.get("TOKEN_METRICS_LOGCACHE", ".cache/logscan"))
        self.state_file = Path(state_file) if state_file else root / "plume-claims.json"
        self.unbounded = False

    # ------------------------------------------------------------------ JSON-RPC
    def _rpc(self, method: str, params: list):
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        res = self.http.post(config.PLUME_CLAIM["rpc"], json_body=body)
        if isinstance(res, dict) and res.get("error"):
            err = res["error"]
            if isinstance(err, dict) and int(err.get("code") or 0) == LOG_CAP_CODE:
                raise LogCapExceeded(str(err.get("message")))
            raise RuntimeError(f"{method}: {err}")
        return res["result"]

    def _batch(self, calls: list[tuple[str, list]]) -> list:
        body = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)]
        res = self.http.post(config.PLUME_CLAIM["rpc"], json_body=body)
        if not isinstance(res, list):
            raise RuntimeError(f"batch answered {type(res).__name__}")
        by_id = {r.get("id"): r for r in res}
        out = []
        for i in range(len(calls)):
            r = by_id.get(i) or {}
            if r.get("error") or "result" not in r:
                raise RuntimeError(f"batch item {i}: {r.get('error')}")
            out.append(r["result"])
        return out

    def _logs(self, lo: int, hi: int, topics: list, deadline) -> list[dict]:
        """eth_getLogs over [lo, hi], halving a range the 10,000-log cap refuses."""
        out, stack = [], [(lo, hi)]
        while stack:
            if deadline is not None and _time.monotonic() > deadline:
                raise TimeoutError("plume_claims: out of budget while reading logs")
            a, b = stack.pop()
            try:
                out += self._rpc("eth_getLogs", [{"address": config.PLUME_CLAIM["contract"], "fromBlock": hex(a),
                                                  "toBlock": hex(b), "topics": topics}])
            except LogCapExceeded:
                if a == b:
                    raise
                m = (a + b) // 2
                stack += [(m + 1, b), (a, m)]
        return out

    def _days(self, blocks: list[int], deadline) -> dict[int, str]:
        """{block: UTC day} — every distinct block's timestamp, 100 a batch."""
        out = {}
        for i in range(0, len(blocks), 100):
            if deadline is not None and _time.monotonic() > deadline:
                raise TimeoutError("plume_claims: out of budget while dating blocks")
            chunk = blocks[i:i + 100]
            for b, blk in zip(chunk, self._batch([("eth_getBlockByNumber", [hex(b), False]) for b in chunk])):
                out[b] = str(pd.Timestamp(int(blk["timestamp"], 16), unit="s").date())
        return out

    def _remaining(self, uuid: str, block: int) -> int:
        """campaigns(id).amount at the pinned block (the selector computed from the signature, never hand-written)."""
        from eth_utils import keccak                       # noqa: PLC0415
        data = "0x" + keccak(text="campaigns(bytes16)")[:4].hex() + campaign_topic(uuid)[2:]
        res = self._rpc("eth_call", [{"to": config.PLUME_CLAIM["contract"], "data": data}, hex(block)])
        return _words(res)[2]

    # ------------------------------------------------------------------ the run
    def _load(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def run(self, projects: list[dict], window_days, out):
        if "Plume" not in {p["name"] for p in projects}:
            return
        spec = config.PLUME_CLAIM
        deadline = None
        if not self.unbounded:
            from . import _budget                                       # noqa: PLC0415
            deadline = _time.monotonic() + _budget(SOURCE) - 10.0
        camps = {name: c["id"] for name, c in spec["campaigns"].items()}
        try:
            self._read(camps, spec, deadline, out)
        except TimeoutError as e:
            out.fail(SOURCE, "Plume", f"{METRIC}: {e} — NOTHING STORED; the state is unchanged, the next run reads "
                                      f"again", TIER)
            out.gap("Plume", METRIC, reason=str(e), tiers_attempted="2",
                    suggestion="Re-run; the claims read is a few calls once the history is held.")
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            out.fail(SOURCE, "Plume", f"{METRIC}: {type(e).__name__}: {str(e)[:240]} — NOTHING STORED", TIER)
            out.gap("Plume", METRIC, reason=f"rpc.plume.org claims read failed: {str(e)[:200]}", tiers_attempted="2",
                    suggestion="Check rpc.plume.org; the read resumes from its saved state.")

    def _read(self, camps: dict, spec: dict, deadline, out) -> None:
        st = self._load()
        ids = {u: n for n, u in camps.items()}
        held = st.get("campaigns") or {}
        if set(held) != set(ids):                               # the declared campaigns changed: read from the start
            st, held = {}, {}
        head = int(self._rpc("eth_blockNumber", []), 16) - CONFIRMATIONS
        lo = int(st.get("read_to") or int(spec["deploy_block"]) - 1) + 1
        topics = [[spec["topics"]["claimed"], spec["topics"]["started"], spec["topics"]["cancelled"]],
                  [campaign_topic(u) for u in ids]]
        logs = self._logs(lo, head, topics, deadline) if lo <= head else []
        logs.sort(key=lambda x: (int(x["blockNumber"], 16), int(x["logIndex"], 16)))
        days = self._days(sorted({int(x["blockNumber"], 16) for x in logs
                                  if x["topics"][0].lower() in (spec["topics"]["claimed"], spec["topics"]["started"])}),
                          deadline)
        for u in ids:
            held.setdefault(u, {"initial": None, "start_day": None, "claimed": "0", "last_remaining": None, "cancelled": False,
                                "claims": 0, "days": {}})
        by_topic = {campaign_topic(u): u for u in ids}
        for x in logs:
            u = by_topic.get(x["topics"][1].lower())
            if u is None:
                continue
            c, t0 = held[u], x["topics"][0].lower()
            w = _words(x["data"])
            if t0 == spec["topics"]["started"]:
                c["initial"], c["start_day"] = str(w[2]), days[int(x["blockNumber"], 16)]
            elif t0 == spec["topics"]["cancelled"]:
                c["cancelled"] = True
            elif t0 == spec["topics"]["claimed"]:
                d = days[int(x["blockNumber"], 16)]
                c["claimed"] = str(int(c["claimed"]) + w[0])
                c["last_remaining"] = str(w[1])
                c["claims"] += 1
                c["days"][d] = str(int(c["days"].get(d, "0")) + w[0])
        # RECONCILE every campaign at the pinned block, before anything is kept
        bad = []
        for u, c in held.items():
            name = ids[u]
            if c["initial"] is None:
                bad.append(f"{name}: no CampaignStarted event for {u} on {spec['contract']}")
                continue
            onchain = self._remaining(u, head)
            want = int(c["initial"]) - int(c["claimed"])
            if c["cancelled"]:
                bad.append(f"{name}: cancelled — its refund is not a claim; the series stops until Jake decides")
            elif onchain != want or (c["last_remaining"] is not None and int(c["last_remaining"]) != want):
                bad.append(f"{name}: initial {int(c['initial'])} − claimed {int(c['claimed'])} = {want} wei but "
                           f"campaigns(id).amount = {onchain} wei at block {head:,}")
        if bad:
            out.fail(SOURCE, "Plume", f"{METRIC}: DOES NOT RECONCILE — {'; '.join(bad)}. NOTHING STORED", TIER)
            out.gap("Plume", METRIC, reason="; ".join(bad)[:400], tiers_attempted="2",
                    suggestion="A difference is real (a missed log or a campaign change) and is not absorbed.")
            return
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({"read_to": head, "campaigns": held}), encoding="utf-8")
        # per UTC day, both campaigns, from the first start to yesterday (a day without a claim is a measured 0)
        per_day: dict[str, int] = {}
        for c in held.values():
            for d, v in c["days"].items():
                per_day[d] = per_day.get(d, 0) + int(v)
        start = min(pd.Timestamp(c["start_day"]) for c in held.values())
        yday = today().normalize() - pd.Timedelta(days=1)
        rows = [(d, per_day.get(str(d.date()), 0) / 10 ** 18) for d in pd.date_range(start, yday)]
        msg = "; ".join(f"{ids[u]} {c['claims']:,} claim(s) {int(c['claimed']) / 1e18:,.2f} WPLUME of "
                        f"{int(c['initial']) / 1e18:,.2f}, {int(c['initial']) / 1e18 - int(c['claimed']) / 1e18:,.2f} "
                        f"unclaimed" for u, c in held.items())
        out.add(tidy(rows, "Plume", METRIC, f"{SOURCE}:hedgey[{','.join(sorted(ids.values()))}]", TIER), SOURCE,
                "Plume", f"{METRIC}: reconciled to the wei at block {head:,} — {msg} ({len(logs)} new log(s) over "
                         f"rpc.plume.org)", TIER)
