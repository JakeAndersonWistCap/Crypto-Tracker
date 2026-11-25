"""
fetch/pyth_chain.py — tier 2: PYTH FROM THE SOLANA CHAIN (Round 22, Jake 2026-11-25). The spec is
config.PYTH_CHAIN (every address, read on chain 2026-11-25 with its mint and owner checked); the evidence is in
docs/r22_pyth.md. Keyless public RPC (https://api.mainnet-beta.solana.com), once a day, User-Agent token-metrics-check.

THREE READS:

 1. THE STOCKS (fetch/pyth_stocks.read — the D7-1 reader, every token account stored only when its mint AND owner are
    the declared ones), mapped onto the series the build reads:
      total_supply                  the mint's supply (no mint authority: 10B minted at launch, less SPL burns)
      pyth_unvested_tokens          genesis lockup (379 PeriodicVestingAfterListing accounts) + the other periodic
                                    vesting locks (PPR / grant locks): OUT of circulating (Jake R22 item 1). ALL-OR-NO-ROW.
      treasury_holding_tokens       DAO Treasury PYTH + Community Council vault PYTH (DAO wallets: in circulating, off float)
      pyth_operator_tokens          Douro Labs' paying wallet + the PPR distribution wallet it funds (issuer side, DOCUMENTED
                                    by Douro's own reports' tx links)
      pyth_issuer_inferred_tokens   the two wallets that fund Douro's paying wallet — INFERRED (no primary document
                                    names their controller)
      pyth_held_buyback_tokens      the Pythian Council Ops vault's PYTH (bought, not yet returned to the Treasury)
    and the raw D7-1 lines (pyth_gov_staked_tokens, pyth_ois_staked_tokens, pyth_ois_reward_rate_y, ...).

 2. THE STAKE INSIDE CIRCULATING (locked_tokens), ONCE PER STAKING EPOCH (weekly). Every PositionData account of the staking program pytS9…, read in
    `partitions` slices (memcmp on the owner's first byte, so no single answer is ~1 GB), each position's state at the
    current epoch decoded (LOCKING / LOCKED / UNLOCKING count; UNLOCKED does not). Governance and OIS positions can hold
    the SAME tokens, so a stake account's stake is max(voting, OIS), never their sum; the account's UNVESTED balance
    (its StakeAccountMetadataV2, the PDA ["stake_metadata", positions]) is already out of circulating, so it comes off
    the stake first: locked_tokens = Σ_accounts max(0, max(voting, OIS) − unvested). PROOF, every read: Σ LOCKED voting
    positions must equal the voting target's `locked` and Σ LOCKED OIS positions the OIS pool's delegated + self, to
    1e-6 PYTH — else nothing is stored. Read 2026-11-25: voting 1,114,802,334.696795 = target; OIS 669,878,631.892217 =
    pool; union 1,263.4M; unvested inside staked accounts 843.4M (84 accounts); locked_tokens 527.1M.

 3. THE BUYBACK (actual_buyback_tokens; pyth_buyback_returned_tokens). Every transaction of the Council vault's PYTH
    account BTy44…: a rise in a transaction that invokes a swap program (Jupiter DCA, aggregator, limit orders —
    config.PYTH_CHAIN["swap_programs"]) is PYTH BOUGHT; a fall into the Treasury's PYTH account is PYTH RETURNED (held);
    anything else is logged, never counted. Resumable (a state file under .cache/pyth_chain): forward from the newest
    signature seen, backward to `scan_from` within the tier's budget. A day is stored only when the scan covers all of
    it; a covered day with no buy is a measured 0.

A line that does not read stores nothing (never 0) and is logged FAILED with the reason.

    python -m fetch.pyth_chain          the live figures, nothing stored
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import struct
import time
from pathlib import Path

import pandas as pd

import config
from . import pyth_stocks
from .base import Http, LogEntry, point, tidy, today

log = logging.getLogger("token_metrics.fetch.pyth_chain")

SOURCE = "pyth_chain"
TIER = 2
PROJECT = "Pyth"
DECIMALS = 6

# ---------------------------------------------------------------------------------------------- base58 / PDAs
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_P = 2 ** 255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P


def b58decode(s: str) -> bytes:
    n = 0
    for c in s:
        n = n * 58 + _B58.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * (len(s) - len(s.lstrip("1"))) + raw


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\x00"))) + out


def on_curve(b: bytes) -> bool:
    """Whether 32 bytes decode to an ed25519 point (a PDA must NOT)."""
    y = int.from_bytes(b, "little") & ((1 << 255) - 1)
    if y >= _P:
        return False
    u, v = (y * y - 1) % _P, (_D * y * y + 1) % _P
    x2 = u * pow(v, _P - 2, _P) % _P
    if x2 == 0:
        return not (b[31] >> 7)
    return pow(x2, (_P - 1) // 2, _P) == 1


def find_pda(seeds: list[bytes], program: str) -> str:
    prog = b58decode(program)
    for bump in range(255, -1, -1):
        h = hashlib.sha256(b"".join(seeds) + bytes([bump]) + prog + b"ProgramDerivedAddress").digest()
        if not on_curve(h):
            return b58encode(h)
    raise ValueError("no viable bump")


def _disc(name: str) -> str:
    return base64.b64encode(hashlib.sha256(f"account:{name}".encode()).digest()[:8]).decode()


def _tok(raw: int) -> float:
    return raw / 10 ** DECIMALS


# ---------------------------------------------------------------------------------------------- positions
def position_state(act: int, unl: int | None, epoch: int) -> str:
    """The staking program's rule (positions.rs get_current_position), unlocking duration one epoch."""
    if epoch < act:
        return "LOCKING"
    if unl is None:
        return "LOCKED"
    has = act <= unl
    if has and epoch < unl:
        return "LOCKED"
    if has and unl <= epoch < unl + 1:
        return "UNLOCKING"
    return "UNLOCKED"


def decode_positions(data: bytes, epoch: int) -> dict:
    """PositionData: discriminator 8, owner 32, then 200-byte slots, each Option<Position>: tag u8, amount u64,
    activation_epoch u64, unlocking_start Option<u64>, target u8 (0 voting, 1 integrity pool + publisher).
    {"vote", "ois"}: tokens (raw) in LOCKING / LOCKED / UNLOCKING positions; {"vote_locked", "ois_locked"}: LOCKED only
    (what the voting target and the OIS pool count)."""
    out = {"vote": 0, "ois": 0, "vote_locked": 0, "ois_locked": 0, "bad": 0}
    off = 40
    while off + 200 <= len(data):
        b = data[off:off + 200]
        off += 200
        if b[0] == 0:
            continue
        if b[0] != 1:
            out["bad"] += 1
            continue
        amount, act = struct.unpack_from("<QQ", b, 1)
        p = 17
        unl = struct.unpack_from("<Q", b, p + 1)[0] if b[p] == 1 else None
        p += 9 if b[p] == 1 else 1
        tgt = b[p]
        if tgt not in (0, 1):
            out["bad"] += 1
            continue
        st = position_state(act, unl, epoch)
        if st == "UNLOCKED":
            continue
        k = "vote" if tgt == 0 else "ois"
        out[k] += amount
        if st == "LOCKED":
            out[k + "_locked"] += amount
    return out


def staked_inside_circulating(accounts: list[tuple[str, bytes]], unvested_by_meta: dict, program: str,
                              epoch: int) -> dict:
    """Σ over stake accounts of max(0, max(voting, OIS) − the account's unvested), with the proof legs."""
    tot = {"vote_locked": 0, "ois_locked": 0, "union": 0, "unvested_in_staked": 0, "staked_circ": 0, "accounts": 0,
           "with_vesting": 0, "bad": 0}
    for pubkey, data in accounts:
        d = decode_positions(data, epoch)
        tot["bad"] += d["bad"]
        tot["vote_locked"] += d["vote_locked"]
        tot["ois_locked"] += d["ois_locked"]
        s = max(d["vote"], d["ois"])
        if not s:
            continue
        tot["accounts"] += 1
        unv = unvested_by_meta.get(find_pda([b"stake_metadata", b58decode(pubkey)], program), 0) if unvested_by_meta else 0
        if unv:
            tot["with_vesting"] += 1
            tot["unvested_in_staked"] += min(unv, s)
        tot["union"] += s
        tot["staked_circ"] += max(0, s - unv)
    return tot


# ---------------------------------------------------------------------------------------------- the buyback scan
def classify(tx: dict, account: str, treasury_account: str, swap_programs: set) -> tuple[str, float] | None:
    """(kind, PYTH) for one transaction of the Council vault's PYTH account: 'bought' (a rise in a transaction that
    invokes a swap program), 'returned' (a fall that lands in the Treasury's PYTH account), 'other_in' / 'other_out'.
    None where the account's balance did not change."""
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return None
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in tx["transaction"]["message"]["accountKeys"]]
    keys += list((meta.get("loadedAddresses") or {}).get("writable") or []) + \
        list((meta.get("loadedAddresses") or {}).get("readonly") or [])

    def bal(which: str, addr: str) -> int:
        for t in meta.get(which) or ():
            if t["accountIndex"] < len(keys) and keys[t["accountIndex"]] == addr:
                return int(t["uiTokenAmount"]["amount"])
        return 0
    delta = bal("postTokenBalances", account) - bal("preTokenBalances", account)
    if delta == 0:
        return None
    progs = {ix.get("programId") for ix in tx["transaction"]["message"].get("instructions") or ()}
    for inner in meta.get("innerInstructions") or ():
        progs |= {ix.get("programId") for ix in inner.get("instructions") or ()}
    if delta > 0:
        return ("bought" if progs & swap_programs else "other_in"), _tok(delta)
    t_delta = bal("postTokenBalances", treasury_account) - bal("preTokenBalances", treasury_account)
    return ("returned" if t_delta >= -delta else "other_out"), _tok(-delta)


def reconcile_returns(days: dict, reported: dict, rel: float = 1e-6, window_days: int = 75) -> dict:
    """{month: the day the scan saw that month's reported return land in the Treasury, or None} — each figure of the
    Council's monthly 'PYTH Purchases Report' (config.PYTH_CHAIN['reported_returns']) matched to a scanned day whose
    'returned' PYTH equals it (to `rel`), dated from the month's start to `window_days` after it (a month's return lands
    early the next month: June's on 2026-07-06, September's on 2026-10-01)."""
    out = {}
    for m, rep in sorted(reported.items()):
        lo = pd.Timestamp(m + "-01")
        hit = None
        for day in sorted(days):
            t = pd.Timestamp(day)
            v = float((days[day] or {}).get("returned", 0.0))
            if lo <= t < lo + pd.Timedelta(days=window_days) and v and abs(v - rep) <= rel * rep:
                hit = day
                break
        out[m] = hit
    return out


class _State:
    """The scan's resumable state: {newest, oldest, complete, days: {day: {kind: PYTH}}, txs}."""

    def __init__(self, path: Path):
        self.path = path
        try:
            self.d = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — no state (or unreadable): start again
            self.d = {}
        self.d.setdefault("days", {})
        self.d.setdefault("seen", [])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d), encoding="utf-8")
        tmp.replace(self.path)


class PythChain:
    """Pyth's stocks, stake inside circulating and Council buybacks, once a day (R22)."""
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, daily=None, state_dir: str | None = None, clock=time.time,
                 budget_s: float | None = None, **_ignored):
        spec = config.PYTH_CHAIN
        self.spec = spec
        self.http = http or Http(min_interval=float(spec["rpc_min_interval_s"]), retries=4,
                                 max_retry_wait=float(spec["max_retry_wait_s"]), timeout=180)
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily
        self.state_dir = Path(state_dir or os.environ.get("TOKEN_METRICS_PYTH_STATE", ".cache/pyth_chain"))
        self.clock = clock
        self.budget_s = float(budget_s if budget_s is not None else spec["scan_budget_s"])

    def rpc(self, method: str, params: list):
        j = self.http.post(self.spec["rpc"], {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if not isinstance(j, dict) or "error" in j or "result" not in j:
            err = (j or {}).get("error") if isinstance(j, dict) else j
            raise RuntimeError(f"{method}: {str(err)[:200]}")
        return j["result"]

    # ------------------------------------------------------------------------------------------ 1. stocks
    def stocks(self) -> tuple[dict, list, str]:
        """({metric: (value, source)}, [(metric, why)] failed, slot text) — the D7-1 lines and the mapped series."""
        res = pyth_stocks.read(config.PYTH_STOCKS, self.http, now=self.clock())
        raw = {m: (v, s) for m, v, s in res.rows}
        failed = list(res.failed)
        out = dict(raw)
        if "pyth_mint_supply_tokens" in raw:
            out["total_supply"] = raw["pyth_mint_supply_tokens"]
        for metric, legs, what in self.spec["sums"]:
            if all(leg in raw for leg in legs):
                v = sum(raw[leg][0] for leg in legs)
                out[metric] = (v, f"pyth_chain:sum[{' + '.join(legs)}] ({what})")
            else:
                failed.append((metric, "a leg did not read: " + ", ".join(leg for leg in legs if leg not in raw)
                               + " (ALL-OR-NO-ROW)"))
        return out, failed, f"{res.calls} call(s), {res.throttled} HTTP 429(s)"

    # ------------------------------------------------------------------------------------------ 2. stake
    def unvested_by_metadata(self, now: int) -> dict:
        """{metadata pubkey: unvested raw} over both lock kinds (the staking program's own vesting rule)."""
        st = config.PYTH_STOCKS
        r = self.rpc("getAccountInfo", [st["staking_config"], {"encoding": "base64", "commitment": "finalized",
                                                               "dataSlice": {"offset": 0, "length": 188}}])
        list_time = pyth_stocks.decode_list_time(base64.b64decode(r["value"]["data"][0]))
        out = {}
        for tag in (1, 2):
            g = self.rpc("getProgramAccounts", [st["staking_program"], {
                "encoding": "base64", "commitment": "finalized", "dataSlice": {"offset": 12, "length": 72},
                "filters": [{"dataSize": 200}, {"memcmp": {"offset": 0, "bytes": _disc("StakeAccountMetadataV2"),
                                                           "encoding": "base64"}},
                            {"memcmp": {"offset": 44, "bytes": base64.b64encode(bytes([tag])).decode(),
                                        "encoding": "base64"}}]}])
            for x in g:
                b = base64.b64decode(x["account"]["data"][0])
                if tag == 2:
                    ib, per, n = struct.unpack_from("<QQQ", b, 33)
                    start = list_time
                else:
                    ib, start, per, n = struct.unpack_from("<QqQQ", b, 33)
                if not n or not per or start is None:
                    continue
                u = pyth_stocks.unvested(ib, start, per, n, now)
                if u:
                    out[x["pubkey"]] = u
        return out

    def positions(self) -> tuple[list, int]:
        """Every PositionData account (pubkey, data), read in partitions by the owner's first byte; the slot."""
        st = config.PYTH_STOCKS
        parts = int(self.spec["position_partitions"])
        accts, slot = [], 0
        firsts = [None] if parts <= 1 else range(256)
        for fb in firsts:
            flt = [{"memcmp": {"offset": 0, "bytes": _disc("PositionData"), "encoding": "base64"}}]
            if fb is not None:
                flt.append({"memcmp": {"offset": 8, "bytes": base64.b64encode(bytes([fb])).decode(),
                                       "encoding": "base64"}})
            r = self.rpc("getProgramAccounts", [st["staking_program"], {"encoding": "base64", "withContext": True,
                                                                        "commitment": "finalized", "filters": flt}])
            slot = max(slot, int(r["context"]["slot"]))
            accts += [(a["pubkey"], base64.b64decode(a["account"]["data"][0])) for a in r["value"]]
        return accts, slot

    def stake(self, targets: dict) -> tuple[float, str]:
        """locked_tokens with its proof against the voting target and the OIS pool (raw totals in `targets`)."""
        now = int(self.clock())
        epoch = now // int(self.spec["epoch_seconds"])
        unv = self.unvested_by_metadata(now)
        accts, slot = self.positions()
        t = staked_inside_circulating(accts, unv, config.PYTH_STOCKS["staking_program"], epoch)
        if t["bad"]:
            raise RuntimeError(f"{t['bad']} position slot(s) did not decode — NOT STORED")
        for k, want in (("vote_locked", targets.get("vote")), ("ois_locked", targets.get("ois"))):
            if want is None or abs(_tok(t[k]) - want) > 1e-6:
                raise RuntimeError(f"proof failed: Σ {k} positions {_tok(t[k]):,.6f} vs the program's own total "
                                   f"{want} — NOT STORED")
        return _tok(t["staked_circ"]), (
            f"pyth_chain:positions[{len(accts)} PositionData accounts, epoch {epoch}; staked (max of voting, OIS per "
            f"account) {_tok(t['union']):,.6f} − unvested inside staked accounts {_tok(t['unvested_in_staked']):,.6f} "
            f"({t['with_vesting']} accounts); proof voting {_tok(t['vote_locked']):,.6f} = target, OIS "
            f"{_tok(t['ois_locked']):,.6f} = pool; slot {slot}]")

    # ------------------------------------------------------------------------------------------ 3. buyback
    def scan(self, deadline: float) -> dict:
        sp = self.spec
        stt = _State(self.state_dir / "council_scan.json")
        d = stt.d
        acct, treas = sp["council_pyth_account"], sp["treasury_pyth_account"]
        swaps = set(sp["swap_programs"])
        seen = set(d["seen"])
        start_ts = int(pd.Timestamp(sp["scan_from"]).timestamp())
        now_ts = int(self.clock())

        def take(sigs: list) -> None:
            for s in sigs:
                if s["signature"] in seen or s.get("err") is not None:
                    continue
                tx = self.rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "commitment": "finalized",
                                                                  "maxSupportedTransactionVersion": 0}])
                seen.add(s["signature"])
                if not tx:
                    continue
                c = classify(tx, acct, treas, swaps)
                if c is None:
                    continue
                day = str(pd.Timestamp(int(tx.get("blockTime") or s.get("blockTime")), unit="s").date())
                b = d["days"].setdefault(day, {})
                b[c[0]] = b.get(c[0], 0.0) + c[1]
                b["n"] = b.get("n", 0) + 1

        # forward: everything newer than the newest seen
        if d.get("newest"):
            before = None
            fresh, listed = [], False
            while self.clock() < deadline:
                page = self.rpc("getSignaturesForAddress", [acct, {"limit": 1000, "until": d["newest"],
                                                                   "commitment": "finalized",
                                                                   **({"before": before} if before else {})}])
                fresh += page or []
                if not page or len(page) < 1000:
                    listed = True                      # every signature newer than `newest` is listed
                    break
                before = page[-1]["signature"]
            if listed:
                if fresh:
                    take(list(reversed(fresh)))
                    d["newest"] = fresh[0]["signature"]
                    d["newest_ts"] = fresh[0].get("blockTime")
                d["scanned_to_ts"] = now_ts
        # backward: to scan_from
        while not d.get("complete") and self.clock() < deadline:
            page = self.rpc("getSignaturesForAddress", [acct, {"limit": 200, "commitment": "finalized",
                                                               **({"before": d["oldest"]} if d.get("oldest") else {})}])
            if not d.get("newest") and page:
                d["newest"], d["newest_ts"] = page[0]["signature"], page[0].get("blockTime")
                d["scanned_to_ts"] = now_ts
            if not page:
                d["complete"] = True
                break
            for s in page:
                if self.clock() >= deadline:
                    break
                if int(s.get("blockTime") or 0) < start_ts:
                    d["complete"] = True
                    break
                take([s])
                d["oldest"], d["oldest_ts"] = s["signature"], s.get("blockTime")
            if len(page) < 200 and not d.get("complete") and d.get("oldest") == page[-1]["signature"]:
                d["complete"] = True
        d["seen"] = sorted(seen)
        stt.save()
        return d

    @staticmethod
    def covered_days(d: dict, scan_from: str, today_day: str) -> list[str]:
        """The days the scan covers COMPLETELY: from scan_from (or the day after the oldest transaction read, when the
        backward walk has not reached scan_from) to the day before the newest read's day, and before today."""
        if not d.get("scanned_to_ts"):
            return []
        lo = pd.Timestamp(scan_from) if d.get("complete") else (
            pd.Timestamp(int(d["oldest_ts"]), unit="s").normalize() + pd.Timedelta(days=1) if d.get("oldest_ts") else None)
        if lo is None:
            return []
        hi = min(pd.Timestamp(today_day), pd.Timestamp(int(d["scanned_to_ts"]), unit="s").normalize()) \
            - pd.Timedelta(days=1)
        return [str(x.date()) for x in pd.date_range(lo, hi)] if hi >= lo else []

    # ------------------------------------------------------------------------------------------ the tier
    def run(self, projects: list[dict], window_days, out):
        if not any(p["name"] == PROJECT for p in projects):
            return
        t0 = self.clock()
        day = str(today().date())
        key = f"{SOURCE}:{PROJECT}"
        if not self.daily.due(key, day):
            out.mark_current(SOURCE, PROJECT, "total_supply", "the Solana reads ran today already", TIER)
            return
        ok = True
        vals, failed, calls = {}, [], ""
        try:
            vals, failed, calls = self.stocks()
        except Exception as e:  # noqa: BLE001
            failed.append(("stocks", str(e)[:300]))
        for m, (v, s) in sorted(vals.items()):
            out.add(point(PROJECT, m, v, s if s.startswith("pyth_chain") else f"pyth_chain:{s}", TIER), SOURCE, PROJECT,
                    f"{m}: {v:,.6f}", TIER)
        targets = {"vote": (vals.get("pyth_gov_staked_tokens") or (None,))[0],
                   "ois": (vals.get("pyth_ois_staked_tokens") or (None,))[0]}
        # ONCE PER STAKING EPOCH (the heavy read: every PositionData account, ~1 GB from the public RPC, which cut this
        # session off with HTTP 413 "data allowance" after two such reads in a day); stale only after an epoch and two
        # days (config.stale_after_days, R22)
        ekey, epoch = f"{SOURCE}:{PROJECT}:stake", f"epoch-{int(self.clock()) // int(self.spec['epoch_seconds'])}"
        if self.daily.due(ekey, epoch):
            try:
                v, s = self.stake(targets)
                out.add(point(PROJECT, "locked_tokens", v, s, TIER), SOURCE, PROJECT, f"locked_tokens: {v:,.6f} — {s}",
                        TIER)
                self.daily.done(ekey, epoch)
            except Exception as e:  # noqa: BLE001
                failed.append(("locked_tokens", str(e)[:300]))
        else:
            out.mark_current(SOURCE, PROJECT, "locked_tokens", f"locked_tokens: read this staking epoch ({epoch})", TIER)
        try:
            d = self.scan(self.clock() + self.budget_s)
            days = self.covered_days(d, self.spec["scan_from"], day)
            for metric, kind in (("actual_buyback_tokens", "bought"), ("pyth_buyback_returned_tokens", "returned")):
                rows = [(x, float((d["days"].get(x) or {}).get(kind, 0.0))) for x in days]
                if rows:
                    out.add(tidy(rows, PROJECT, metric, f"pyth_chain:council_vault[{self.spec['council_pyth_account']}"
                                                         f" {kind}; {len(d['seen'])} tx read]", TIER),
                            SOURCE, PROJECT, f"{metric}: {len(rows)} covered day(s), {sum(v for _x, v in rows):,.2f} PYTH",
                            TIER)
            odd = {x: b for x, b in d["days"].items() if b.get("other_in") or b.get("other_out")}
            if odd:
                out.log.append(LogEntry(
                    SOURCE, PROJECT, 0, "warn", "Council vault PYTH moves that are neither a swap nor a return to the "
                    "Treasury (NOT counted): " + "; ".join(f"{x} {b}" for x, b in sorted(odd.items())[-5:]), TIER))
            if not d.get("complete"):
                ok = False
                out.log.append(LogEntry(
                    SOURCE, PROJECT, 0, "warn", f"buyback scan resumes next run (reached {d.get('oldest_ts')})", TIER))
        except Exception as e:  # noqa: BLE001
            failed.append(("actual_buyback_tokens", str(e)[:300]))
        for m, why in failed:
            out.fail(SOURCE, PROJECT, f"{m}: NOT STORED — {why}", TIER)
        if ok and not failed:
            self.daily.done(key, day)
        log.info("pyth_chain: %d series, %d failed; stocks %s; %.0fs", len(vals), len(failed), calls,
                 self.clock() - t0)


if __name__ == "__main__":                                   # pragma: no cover — a live look, nothing stored
    pc = PythChain(budget_s=60)
    v, f, c = pc.stocks()
    for m, (x, s) in sorted(v.items()):
        print(f"{m:40s} {x:>24,.6f}  {s[:120]}")
    print("failed", f, c)
    print(pc.stake({"vote": v.get("pyth_gov_staked_tokens", (None,))[0], "ois": v.get("pyth_ois_staked_tokens", (None,))[0]}))
