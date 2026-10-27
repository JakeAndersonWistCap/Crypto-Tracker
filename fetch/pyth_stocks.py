"""
fetch/pyth_stocks.py — Pyth's STOCK reads from the public Solana RPC, STORE ONLY (SPEC §19 D7-1, Jake 2026-10-27).

WHAT. One reading a day of every Pyth balance the research names as a stock (research/pyth/sources.csv rows
kind=STOCK, dossier §1.5–1.8 and §4): the PYTH mint's supply, the DAO Treasury and Pythian Council vaults (PYTH and
USDC), the USDC still in the Council's open Jupiter DCA orders, governance stake, OIS stake / reward rate / unclaimed
custody, the unvested genesis and other vesting locks, the Community Council vault, Douro's paying wallet and the two
wallets that fund it. Rows go to metrics.db under project "Pyth" (config.STORE_ONLY_PROJECTS): no tab shows them.

ADDRESSES ARE FULL STRINGS, AND A TOKEN ACCOUNT IS STORED ONLY IF ITS MINT AND OWNER ARE THE DECLARED ONES. The dossier
found address-poisoning lookalikes on every one of these wallets; getAccountInfo (jsonParsed) returns the account's
own mint and owner, so a wrong account can never be stored as the right one.

PACED, AND A 429 IS WAITED OUT. The public endpoint (config PYTH_STOCKS["rpc"]) is called once every
rpc_min_interval_s; an HTTP 429 waits its Retry-After (fetch.base.Http, at most max_retry_wait_s) and retries.

NOTHING IS INFERRED. A line that does not read — an RPC error, a refused getProgramAccounts, a mint or owner that
does not match — stores nothing for that line (never 0) and is logged FAILED with the reason.
"""
from __future__ import annotations

import base64
import hashlib
import struct
import time
from dataclasses import dataclass, field
from decimal import Decimal

import pandas as pd

import config
from .base import Http, LogEntry

SOURCE = "pyth_stocks"
PYTH_DECIMALS = 6
_U64 = struct.Struct("<Q")


def anchor_discriminator(account: str) -> bytes:
    """An Anchor account's 8-byte discriminator: sha256("account:<Name>")[:8]."""
    return hashlib.sha256(f"account:{account}".encode()).digest()[:8]


def unvested(initial: int, start: int, period: int, num_periods: int, now: int) -> int:
    """The staking program's periodic vesting rule (pytS9…, VestingSchedule): the whole balance before the start, none
    after the last period, else initial x periods remaining / num_periods (integer arithmetic, as on-chain)."""
    if now < start:
        return initial
    passed = (now - start) // period
    if passed >= num_periods:
        return 0
    return (num_periods - passed) * initial // num_periods


def decode_voting_target(data: bytes) -> dict:
    """TargetMetadata after the 8-byte discriminator: bump u8, last_update_at u64 (epoch), prev_epoch_locked u64,
    locked u64, delta_locked i64 (raw units, 6 decimals)."""
    epoch, prev, locked, delta = struct.unpack_from("<QQQq", data, 9)
    return {"epoch": epoch, "prev_epoch_locked": prev, "locked": locked, "delta_locked": delta}


OIS_MAX_PUBLISHERS = 1024
OIS_PUBLISHERS_AT, OIS_DEL_AT, OIS_SELF_AT = 24, 32_792, 49_176
OIS_SLICE = OIS_SELF_AT + 16 * OIS_MAX_PUBLISHERS          # 65,560 bytes: header, publishers and both stake arrays


def decode_ois_pool(data: bytes) -> dict:
    """PoolData (OIS program pyti8…) after the discriminator: last_updated_epoch u64, claimable_rewards u64,
    publishers [Pubkey; 1024], del_state [(total_delegation u64, delta_delegation i64); 1024], self_del_state (same)."""
    epoch, claimable = struct.unpack_from("<QQ", data, 8)
    pubs = sum(1 for i in range(OIS_MAX_PUBLISHERS)
               if any(data[OIS_PUBLISHERS_AT + 32 * i:OIS_PUBLISHERS_AT + 32 * (i + 1)]))
    dl = [struct.unpack_from("<Qq", data, OIS_DEL_AT + 16 * i) for i in range(OIS_MAX_PUBLISHERS)]
    sf = [struct.unpack_from("<Qq", data, OIS_SELF_AT + 16 * i) for i in range(OIS_MAX_PUBLISHERS)]
    return {"epoch": epoch, "claimable": claimable, "publishers": pubs,
            "delegated": sum(x[0] for x in dl), "self": sum(x[0] for x in sf),
            "delta_delegated": sum(x[1] for x in dl), "delta_self": sum(x[1] for x in sf)}


def decode_list_time(data: bytes) -> int | None:
    """GlobalConfig (staking program): pyth_token_list_time is an Option<i64> at byte 179 (tag) / 180 (value)."""
    return struct.unpack_from("<q", data, 180)[0] if data[179] == 1 else None


def _tokens(raw: int, decimals: int = PYTH_DECIMALS) -> float:
    return float(Decimal(raw) / (Decimal(10) ** decimals))


@dataclass
class Result:
    rows: list = field(default_factory=list)          # (metric, value, source)
    failed: list = field(default_factory=list)        # (metric, reason)
    calls: int = 0
    throttled: int = 0
    seconds: float = 0.0


class _Rpc:
    def __init__(self, spec: dict, http: Http | None = None):
        self.url = spec["rpc"]
        self.http = http or Http(min_interval=float(spec["rpc_min_interval_s"]), retries=4,
                                 max_retry_wait=float(spec["max_retry_wait_s"]))

    def __call__(self, method: str, params: list):
        j = self.http.post(self.url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if not isinstance(j, dict) or "error" in j or "result" not in j:
            err = (j or {}).get("error") if isinstance(j, dict) else j
            raise RuntimeError(f"{method}: {err}")
        return j["result"]


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def read(spec: dict | None = None, http: Http | None = None, now: float | None = None) -> Result:
    """Every STOCK line once. Each row's source carries the read and the slot of the context that answered."""
    spec = spec or config.PYTH_STOCKS
    rpc = _Rpc(spec, http)
    out = Result()
    t0 = time.monotonic()
    fin = {"commitment": "finalized"}

    def attempt(metric: str, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — one line's failure never stops the others
            out.failed.append((metric, str(e)[:300]))

    def supply():
        r = rpc("getTokenSupply", [spec["mint"], fin])
        v = r["value"]
        out.rows.append(("pyth_mint_supply_tokens", _tokens(int(v["amount"]), int(v["decimals"])),
                         f"chain:solana:getTokenSupply {spec['mint']}[slot {r['context']['slot']}]"))
    attempt("pyth_mint_supply_tokens", supply)

    for metric, addr, mint, owner, _what in spec["token_accounts"]:
        def token_account(metric=metric, addr=addr, mint=mint, owner=owner):
            r = rpc("getAccountInfo", [addr, {"encoding": "jsonParsed", **fin}])
            v = r["value"]
            if v is None:
                raise RuntimeError(f"{addr}: no such account")
            info = ((v.get("data") or {}).get("parsed") or {}).get("info") or {}
            if info.get("mint") != mint or info.get("owner") != owner:
                raise RuntimeError(f"{addr}: mint/owner do not match — expected mint {mint} owner {owner}, read mint "
                                   f"{info.get('mint')} owner {info.get('owner')} (NOT STORED)")
            ta = info["tokenAmount"]
            out.rows.append((metric, _tokens(int(ta["amount"]), int(ta["decimals"])),
                             f"chain:solana:token_account {addr}[owner {owner}; slot {r['context']['slot']}]"))
        attempt(metric, token_account)

    def dca():
        disc = anchor_discriminator("Dca")
        r = rpc("getProgramAccounts", [spec["dca_program"], {
            "encoding": "base64", "withContext": True, **fin, "dataSlice": {"offset": 0, "length": 0},
            "filters": [{"memcmp": {"offset": 0, "bytes": _b64(disc), "encoding": "base64"}},
                        {"memcmp": {"offset": 8, "bytes": spec["council_vault"]}}]}])
        accts = sorted(x["pubkey"] for x in r["value"])
        total, parts = 0, []
        for a in accts:
            k = rpc("getTokenAccountsByOwner", [a, {"mint": spec["usdc_mint"]}, {"encoding": "jsonParsed", **fin}])
            amt = sum(int(t["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"]) for t in k["value"])
            total += amt
            parts.append(f"{a} {_tokens(amt):,.6f}")
        out.rows.append(("pyth_council_dca_open_usdc", _tokens(total),
                         f"chain:solana:jupiter_dca_open {spec['council_vault']}[{len(accts)} open DCA account(s): "
                         f"{'; '.join(parts) or 'none'}; slot {r['context']['slot']}]"))
    attempt("pyth_council_dca_open_usdc", dca)

    def gov():
        r = rpc("getAccountInfo", [spec["voting_target"], {"encoding": "base64", **fin,
                                                            "dataSlice": {"offset": 0, "length": 41}}])
        t = decode_voting_target(base64.b64decode(r["value"]["data"][0]))
        out.rows.append(("pyth_gov_staked_tokens", _tokens(t["locked"]),
                         f"chain:solana:voting_target {spec['voting_target']}[locked; epoch {t['epoch']}; prev epoch "
                         f"{_tokens(t['prev_epoch_locked']):,.6f}; delta {_tokens(t['delta_locked']):,.6f}; slot "
                         f"{r['context']['slot']}]"))
    attempt("pyth_gov_staked_tokens", gov)

    def ois():
        r = rpc("getAccountInfo", [spec["ois_pool_data"], {"encoding": "base64", **fin,
                                                            "dataSlice": {"offset": 0, "length": OIS_SLICE}}])
        p = decode_ois_pool(base64.b64decode(r["value"]["data"][0]))
        out.rows.append(("pyth_ois_staked_tokens", _tokens(p["delegated"] + p["self"]),
                         f"chain:solana:ois_pool {spec['ois_pool_data']}[delegated {_tokens(p['delegated']):,.6f} + self "
                         f"{_tokens(p['self']):,.6f}; pending deltas {_tokens(p['delta_delegated']):,.6f} / "
                         f"{_tokens(p['delta_self']):,.6f}; epoch {p['epoch']}; {p['publishers']} publisher pools; "
                         f"claimable {_tokens(p['claimable']):,.6f}; slot {r['context']['slot']}]"))
    attempt("pyth_ois_staked_tokens", ois)

    def ois_y():
        r = rpc("getAccountInfo", [spec["ois_pool_config"], {"encoding": "base64", **fin,
                                                              "dataSlice": {"offset": 104, "length": 8}}])
        y = _U64.unpack(base64.b64decode(r["value"]["data"][0]))[0]
        out.rows.append(("pyth_ois_reward_rate_y", float(y),
                         f"chain:solana:ois_pool_config {spec['ois_pool_config']}[y, u64 as stored; slot "
                         f"{r['context']['slot']}]"))
    attempt("pyth_ois_reward_rate_y", ois_y)

    def vesting():
        try:
            r = rpc("getAccountInfo", [spec["staking_config"], {"encoding": "base64", **fin,
                                                                 "dataSlice": {"offset": 0, "length": 188}}])
            list_time = decode_list_time(base64.b64decode(r["value"]["data"][0]))
        except Exception as e:  # noqa: BLE001 — both vesting lines need the config's clock: neither is stored
            out.failed.append(("pyth_unvested_genesis_tokens", f"staking config not read: {str(e)[:250]}"))
            out.failed.append(("pyth_unvested_other_tokens", f"staking config not read: {str(e)[:250]}"))
            return
        t_now = int(now if now is not None else time.time())
        disc = anchor_discriminator("StakeAccountMetadataV2")
        for tag, metric in ((2, "pyth_unvested_genesis_tokens"), (1, "pyth_unvested_other_tokens")):
            def one(tag=tag, metric=metric):
                g = rpc("getProgramAccounts", [spec["staking_program"], {
                    "encoding": "base64", "withContext": True, **fin, "dataSlice": {"offset": 12, "length": 72},
                    "filters": [{"dataSize": 200},
                                {"memcmp": {"offset": 0, "bytes": _b64(disc), "encoding": "base64"}},
                                {"memcmp": {"offset": 44, "bytes": _b64(bytes([tag])), "encoding": "base64"}}]}])
                total, held, bad = 0, 0, 0
                for x in g["value"]:
                    b = base64.b64decode(x["account"]["data"][0])
                    if tag == 2:
                        if list_time is None:
                            raise RuntimeError("the staking config holds no pyth_token_list_time")
                        ib, per, n = struct.unpack_from("<QQQ", b, 33)
                        start = list_time
                    else:
                        ib, start, per, n = struct.unpack_from("<QqQQ", b, 33)
                    if n == 0 or per == 0:
                        bad += 1
                        continue
                    u = unvested(ib, start, per, n, t_now)
                    total += u
                    held += u > 0
                when = pd.Timestamp(t_now, unit="s").strftime("%Y-%m-%d %H:%M UTC")
                out.rows.append((metric, _tokens(total),
                                 f"chain:solana:vesting {spec['staking_program']}[lock tag {tag}; {len(g['value'])} "
                                 f"account(s), {held} with an unvested balance, {bad} malformed excluded; at {when}"
                                 + (f"; list time {list_time}" if tag == 2 else "")
                                 + f"; slot {g['context']['slot']}]"))
            attempt(metric, one)
    vesting()

    out.calls = int(rpc.http.stats.get("calls", 0))
    out.throttled = int(rpc.http.stats.get("throttled", 0))
    out.seconds = time.monotonic() - t0
    return out


def frame(res: Result, day: str, project: str | None = None) -> pd.DataFrame:
    """The rows as the store's long frame, dated `day` (the UTC day of the read), tier 2 (on-chain)."""
    project = project or config.PYTH_STOCKS["project"]
    return pd.DataFrame([{"date": day, "project": project, "metric": m, "value": v, "source": s, "tier": 2}
                         for m, v, s in res.rows], columns=["date", "project", "metric", "value", "source", "tier"])


def log_entries(res: Result) -> list[LogEntry]:
    project = config.PYTH_STOCKS["project"]
    head = (f"{len(res.rows)} STOCK line(s) stored, {len(res.failed)} not read — {res.calls} RPC call(s), "
            f"{res.throttled} HTTP 429(s), {res.seconds:.0f}s (store only, SPEC D7-1)")
    out = [LogEntry(SOURCE, project, len(res.rows), "ok" if not res.failed else "warn", head, 2)]
    out += [LogEntry(SOURCE, project, 0, "failed", f"{m}: NOT STORED — {why}", 2) for m, why in res.failed]
    return out
