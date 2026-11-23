"""
fetch/ef_wallets.py — tier 1: THE ETHEREUM FOUNDATION'S ETH, WALLET BY WALLET (R18, Jake 2026-11-24: "Ethereum
Foundation wallets off the float, from published addresses; inferred ones tagged INFERRED").
config.PROJECT_BY_NAME["Ethereum"]["ef_wallets"] holds the list, each wallet with where its address was published.

Once a day, keyless:
  HELD     eth_getBalance of every wallet at ONE finalized execution block (+ WETH.balanceOf where declared: WETH is
           ETH held 1:1 by the WETH contract for the wallet). Two series: the PUBLISHED wallets' sum (ef_held_tokens)
           and the INFERRED wallets' sum (ef_inferred_held_tokens) — never mixed, each row names every wallet's figure.
           A wallet that cannot be read is NO ROW for its series that day (never the sum without it).
  STAKED   Σ balance of the validators whose withdrawal credentials are the published treasury Safe (the validator the
           Foundation's own post names, and the others the Safe deposited for), read by public key from a public
           beacon node at the finalized state (ef_staked_tokens). Refused when a listed validator no longer carries
           the Safe's withdrawal credentials. The list is a READ of 2026-11-23 (the whole registry scanned: 34
           validators): the row says so when the Safe has executed a transaction since (its nonce moved) — a new
           deposit would need the scan again (`python -m fetch.ef_wallets --scan`).
The ETH lent out through lending vaults (vault shares, aTokens) is NOT counted: the wallet holds a claim, the ETH is
with the borrower. Reads only; nothing here writes the store (the run does).

    python -m fetch.ef_wallets            the live table: wallet, tag, source of the address, ETH, WETH, block, day
    python -m fetch.ef_wallets --scan     the whole validator registry (~1.1 GB of JSON, streamed, ~1 minute): every
                                          validator whose withdrawal credentials are one of the listed wallets
    python -m fetch.ef_wallets --backfill the HELD series' past days the store lacks (455 by default, newest first, a
                                          20-minute budget, resumable): each day's balances at its last block from an
                                          archive endpoint; writes only keys that hold nothing, under the run lock.
                                          The stake has no history route (a public beacon node serves no past state)
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import pandas as pd

from .base import tidy, today

log = logging.getLogger("token_metrics.fetch.ef_wallets")

ROOT = Path(__file__).resolve().parent.parent
SOURCE = "ef_wallets"
TIER = 1
POINT_HELD = "ef_wallets:eth_getBalance"
POINT_STAKED = "ef_wallets:validators.balance"
BALANCE_OF = "0x70a08231"
SAFE_NONCE = "0xaffed0e0"


def spec_of(p: dict) -> dict | None:
    return p.get("ef_wallets")


def credentials_address(wc: str) -> str:
    """The execution address inside 0x01 / 0x02 withdrawal credentials ('' for a BLS 0x00 credential)."""
    s = str(wc).lower()
    return "0x" + s[-40:] if re.fullmatch(r"0x0[12]0{22}[0-9a-f]{40}", s) else ""


def sum_wallets(reads: dict, wallets, tag: str) -> tuple[float | None, list[str], list[str]]:
    """(Σ ETH + WETH of the wallets carrying `tag`, [per-wallet text], [wallets not read]). The sum is None when any
    wallet of the tag was not read — a figure without one of its wallets is not the figure."""
    total, parts, missing = 0.0, [], []
    for w in wallets:
        if w["tag"] != tag:
            continue
        got = reads.get(w["address"].lower())
        if got is None or got.get("eth") is None or (w.get("weth") and got.get("weth") is None):
            missing.append(w["key"])
            continue
        v = float(got["eth"]) + float(got.get("weth") or 0.0)
        total += v
        parts.append(f"{w['key']} {w['address'][:10]}… {v:,.4f}"
                     + (f" (ETH {got['eth']:,.4f} + WETH {got['weth']:,.4f})" if w.get("weth") else ""))
    return (None if missing else total), parts, missing


def staked_sum(rows, validators, treasury: str) -> tuple[float | None, int, list[str]]:
    """(Σ balance ETH, validators counted, [problems]) from a beacon node's validators answer (`data` rows) for the
    declared public keys: every declared key must be answered and must still carry the treasury's withdrawal
    credentials — else None (a stake read on another set is not this stake)."""
    want = {str(k).lower() for k in validators}
    seen, total, bad = set(), 0.0, []
    for v in rows or []:
        val = v.get("validator") or {}
        pk = str(val.get("pubkey", "")).lower()
        if pk not in want:
            continue
        if credentials_address(val.get("withdrawal_credentials", "")) != treasury.lower():
            bad.append(f"validator {v.get('index')} carries withdrawal credentials "
                       f"{str(val.get('withdrawal_credentials'))[:26]}…, not the treasury Safe's")
            continue
        bal = str(v.get("balance", ""))
        if not bal.isdigit():
            bad.append(f"validator {v.get('index')} has no balance in the answer")
            continue
        seen.add(pk)
        total += int(bal) / 1e9
    lost = want - seen
    if lost and not bad:
        bad.append(f"{len(lost)} of {len(want)} declared validator(s) not in the node's answer")
    return (None if bad else total), len(seen), bad


class EFWallets:
    """The Ethereum Foundation's held and staked ETH, once a day (R18)."""
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, session=None, daily=None, **_ignored):
        self.session = session              # tests inject a stub
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily

    def _sess(self):
        if self.session is None:
            import requests
            from .base import USER_AGENT
            self.session = requests.Session()
            self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"})
        return self.session

    def _post(self, url: str, body):
        from .base import CONNECT_TIMEOUT_S, READ_TIMEOUT_S, mask_url
        r = self._sess().post(url, json=body, timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S))
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} from {mask_url(url)}")
        return r.json()

    def rpc(self, url: str, method: str, params: list):
        out = self._post(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if not isinstance(out, dict) or "result" not in out or out["result"] is None:
            raise RuntimeError(f"{method}: {str((out or {}).get('error') if isinstance(out, dict) else out)[:120]}")
        return out["result"]

    def endpoints(self, spec: dict) -> list[str]:
        keyed = os.environ.get(spec.get("rpc_env") or "", "")
        return ([keyed] if keyed else []) + list(spec["rpc"])

    def read_wallets(self, spec: dict, url: str, block: int | None = None) -> tuple[int, pd.Timestamp, dict, int | None]:
        """(block, its time, {address: {"eth", "weth"}}, the treasury Safe's nonce) — every figure at ONE block of one
        endpoint: the finalized block, or a past `block` (the history read: an archive endpoint; no nonce)."""
        blk = self.rpc(url, "eth_getBlockByNumber", ["finalized" if block is None else hex(int(block)), False])
        tag, when = blk["number"], pd.Timestamp(int(blk["timestamp"], 16), unit="s")
        reads = {}
        for w in spec["wallets"]:
            a = w["address"]
            got = {"eth": int(self.rpc(url, "eth_getBalance", [a, tag]), 16) / 1e18, "weth": None}
            if w.get("weth"):
                data = BALANCE_OF + a[2:].lower().rjust(64, "0")
                got["weth"] = int(self.rpc(url, "eth_call", [{"to": spec["weth"], "data": data}, tag]), 16) / 1e18
            reads[a.lower()] = got
        nonce = None
        if block is None:
            nonce = int(self.rpc(url, "eth_call", [{"to": spec["stake"]["treasury"], "data": SAFE_NONCE}, tag]), 16)
        return int(tag, 16), when, reads, nonce

    def read_stake(self, spec: dict) -> tuple[float | None, int, int | None, list[str]]:
        """(Σ ETH, validators, finalized slot, [problems]) from the first beacon node that answers."""
        st, errs = spec["stake"], []
        for node in st["beacon"]:
            from .base import mask_url
            try:
                hdr = self._sess().get(f"{node}/eth/v1/beacon/headers/finalized", timeout=60).json()
                slot = int(hdr["data"]["header"]["message"]["slot"])
                body = self._post(f"{node}/eth/v1/beacon/states/finalized/validators", {"ids": list(st["validators"])})
            except Exception as e:  # noqa: BLE001 — one node's failure is said, the next is tried
                errs.append(f"{mask_url(node)}: {type(e).__name__}: {str(e)[:120]}")
                continue
            if not (isinstance(body, dict) and body.get("finalized") is True):
                errs.append(f"{mask_url(node)}: the answer does not say finalized")
                continue
            total, n, bad = staked_sum(body.get("data"), st["validators"], st["treasury"])
            if total is None:
                return None, n, slot, bad
            return total, n, slot, []
        return None, 0, None, errs

    def run(self, projects: list[dict], window_days, out):
        import config
        for p in projects:
            spec = spec_of(p)
            if not spec:
                continue
            name, day, dkey = p["name"], str(today().date()), "ef_wallets:daily"
            if not self.daily.due(dkey, day):
                out.mark_current(self.SOURCE, name, spec["held_metric"], "EF wallets: read today already", TIER)
                continue
            got, errs = None, []
            for url in self.endpoints(spec):
                from .base import mask_url
                try:
                    got = self.read_wallets(spec, url)
                    host = mask_url(url).split("/")[2]
                    break
                except Exception as e:  # noqa: BLE001
                    errs.append(f"{mask_url(url).split('/')[2]}: {type(e).__name__}: {str(e)[:120]}")
            if got is None:
                out.fail(self.SOURCE, name, "EF wallets: NOTHING STORED — " + "; ".join(errs), TIER)
                continue
            block, when, reads, nonce = got
            ok = True
            for tag, metric in (("PUBLISHED", spec["held_metric"]), ("INFERRED", spec["inferred_metric"])):
                total, parts, missing = sum_wallets(reads, spec["wallets"], tag)
                if total is None:
                    ok = False
                    out.fail(self.SOURCE, name, f"{metric}: NOTHING STORED — not read: {', '.join(missing)}", TIER)
                    continue
                band = config.METRICS[metric]
                if not (band["sanity_min"] <= total <= band["sanity_max"]):
                    ok = False
                    out.fail(self.SOURCE, name, f"{metric}: refused — Σ {total:,.2f} ETH is outside "
                                                f"{band['sanity_min']:,.0f}..{band['sanity_max']:,.0f}", TIER)
                    continue
                n = sum(1 for w in spec["wallets"] if w["tag"] == tag)
                src = (f"{POINT_HELD}[{n} {tag} wallet(s), ETH + WETH, finalized block {block} "
                       f"{when:%Y-%m-%d %H:%M} UTC via {host}: {'; '.join(parts)}]")
                out.add(tidy([(when.normalize(), total)], name, metric, src, TIER), self.SOURCE, name,
                        f"{metric}: {total:,.2f} ETH in {n} {tag} Ethereum Foundation wallet(s) at block {block}", TIER)
            total, n, slot, bad = self.read_stake(spec)
            st = spec["stake"]
            if total is None:
                ok = False
                out.fail(self.SOURCE, name, f"{st['metric']}: NOTHING STORED — " + "; ".join(bad), TIER)
            else:
                moved = (f"; THE SAFE'S NONCE IS {nonce}, {nonce - int(st['safe_nonce'])} transaction(s) since the list "
                         f"was read ({st['read_on']}, nonce {st['safe_nonce']}): a later deposit is not in it until the "
                         f"registry is scanned again" if nonce != int(st["safe_nonce"]) else
                         f"; the Safe's nonce is {nonce}, unchanged since the list was read ({st['read_on']})")
                src = (f"{POINT_STAKED}[{n} validator(s) with the treasury Safe {st['treasury'][:10]}… as withdrawal "
                       f"address, finalized slot {slot}{moved}]")
                out.add(tidy([(when.normalize(), total)], name, st["metric"], src, TIER), self.SOURCE, name,
                        f"{st['metric']}: {total:,.2f} ETH staked in {n} validator(s) of the Ethereum Foundation "
                        f"(slot {slot}){moved}", TIER)
            if ok:
                self.daily.done(dkey, day)


def history_rows(spec: dict, day, block: int, when, reads: dict) -> list[pd.DataFrame]:
    """The two held series' rows for a PAST day, from balances read at `block` (the day's last block): the live read's
    measuring point with the `archive` marker. A tag whose wallets were not all read gives no row."""
    import config
    out = []
    for tag, metric in (("PUBLISHED", spec["held_metric"]), ("INFERRED", spec["inferred_metric"])):
        total, parts, missing = sum_wallets(reads, spec["wallets"], tag)
        if total is None:
            continue
        n = sum(1 for w in spec["wallets"] if w["tag"] == tag)
        src = config.mark_source(f"{POINT_HELD}[{n} {tag} wallet(s), ETH + WETH, the day's last block {block} "
                                 f"{pd.Timestamp(when):%Y-%m-%d %H:%M} UTC: {'; '.join(parts)}]", "archive")
        out.append(tidy([(pd.Timestamp(day).normalize(), total)], "Ethereum", metric, src, TIER))
    return out


def owed_history_days(held: set, first, last) -> list[pd.Timestamp]:
    """The days first..last with no stored row, NEWEST FIRST (a stopped backfill resumes; a held day is never re-read)."""
    return [d for d in reversed(list(pd.date_range(first, last, freq="D"))) if d not in held]


def backfill(db, days: int, budget_s: float, adapter=None, block_at=None, clock=None) -> int:
    """`python -m fetch.ef_wallets --backfill`: the held series for the past `days` days the store lacks — each day's
    balances at its LAST block, read from an archive endpoint (a keyed ETHEREUM_RPC_URL, else the declared public
    ones). Writes only (date, metric) keys that hold nothing, under the run lock. Returns the rows written."""
    import sqlite3
    import time
    import config
    import store as sm
    from runlock import RunLock
    clock = clock or time.monotonic
    spec = config.PROJECT_BY_NAME["Ethereum"]["ef_wallets"]
    ad = adapter or EFWallets(daily=object())
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        held = {m: {pd.Timestamp(d) for (d,) in con.execute(
            "SELECT substr(date,1,10) FROM metrics WHERE project='Ethereum' AND metric=?", (m,))}
            for m in (spec["held_metric"], spec["inferred_metric"])}
    finally:
        con.close()
    last = today() - pd.Timedelta(days=1)
    owed = owed_history_days(held[spec["held_metric"]] & held[spec["inferred_metric"]],
                             last - pd.Timedelta(days=int(days) - 1), last)
    if not owed:
        print("EF WALLETS HISTORY: nothing owed")
        return 0
    keyed = os.environ.get(spec.get("rpc_env") or "", "")
    urls = ([keyed] if keyed else []) + list(spec["archive_rpc"])
    if block_at is None:
        def block_at(ts: int) -> int:
            return int(ad._sess().get(f"https://coins.llama.fi/block/ethereum/{int(ts)}", timeout=60).json()["height"])
    t0, frames, url = clock(), [], None
    for d in owed:
        if clock() - t0 > budget_s:
            break
        try:
            blk = block_at(int((d + pd.Timedelta(days=1)).timestamp()) - 1)
        except Exception as e:  # noqa: BLE001
            print(f"  {d.date()}: no block for the day's end — {type(e).__name__}: {str(e)[:80]}")
            continue
        got = None
        for u in ([url] if url else urls):
            try:
                got = ad.read_wallets(spec, u, block=blk)
                url = u                                   # the endpoint that serves past state is kept
                break
            except Exception as e:  # noqa: BLE001
                print(f"  {d.date()}: {str(u).split('/')[2]} — {type(e).__name__}: {str(e)[:80]}")
        if got is None:
            continue
        block, when, reads, _nonce = got
        if pd.Timestamp(when).normalize() != d:
            print(f"  {d.date()}: block {block} is dated {when} — not that day's last block, skipped")
            continue
        frames += history_rows(spec, d, block, when, reads)
    if not frames:
        print(f"EF WALLETS HISTORY: {len(owed)} day(s) owed, none read")
        return 0
    df = pd.concat(frames, ignore_index=True)
    df = df[[pd.Timestamp(x) not in held[m] for x, m in zip(df["date"], df["metric"])]]
    with RunLock(db, "ef_wallets --backfill"):
        st = sm.Store(db)
        try:
            n = st.upsert(df)
        finally:
            st.close()
    print(f"EF WALLETS HISTORY: {n} row(s) stored for {df['date'].nunique()} of {len(owed)} owed day(s) "
          f"({pd.Timestamp(df['date'].min()).date()}..{pd.Timestamp(df['date'].max()).date()}); run again to continue")
    return n


def scan_registry(node: str, addresses, session=None) -> dict:
    """Every validator at the finalized state whose withdrawal credentials are one of `addresses`: {"slot", "entries",
    "found": [(index, ETH, status, pubkey, address)]} — the whole registry streamed, never held."""
    import requests
    from .base import USER_AGENT
    s = session or requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"})
    want = "|".join(a.lower()[2:] for a in addresses)
    pat = re.compile((r'\{"index":"(\d+)","balance":"(\d+)","status":"([a-z_]+)","validator":\{"pubkey":"(0x[0-9a-f]{96})",'
                      r'"withdrawal_credentials":"0x0[12]0{22}(' + want + r')"').encode())
    slot = int(s.get(f"{node}/eth/v1/beacon/headers/finalized", timeout=60).json()["data"]["header"]["message"]["slot"])
    r = s.get(f"{node}/eth/v1/beacon/states/finalized/validators", stream=True, timeout=(20, 300))
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return {"slot": slot, **scan_stream(r.iter_content(chunk_size=1 << 20), pat)}


def scan_stream(chunks, pat) -> dict:
    carry, n, found = b"", 0, []
    for chunk in chunks:
        buf = carry + chunk
        cut = buf.rfind(b'{"index"')
        body, carry = (buf[:cut], buf[cut:]) if cut > 0 else (b"", buf)
        n += body.count(b'{"index"')
        found += [(int(m.group(1)), int(m.group(2)) / 1e9, m.group(3).decode(), m.group(4).decode(),
                   "0x" + m.group(5).decode()) for m in pat.finditer(body)]
    n += carry.count(b'{"index"')
    found += [(int(m.group(1)), int(m.group(2)) / 1e9, m.group(3).decode(), m.group(4).decode(),
               "0x" + m.group(5).decode()) for m in pat.finditer(carry)]
    return {"entries": n, "found": found}


def main(argv=None) -> int:
    import argparse
    import config
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scan", action="store_true", help="scan the whole validator registry for the listed wallets")
    ap.add_argument("--backfill", action="store_true", help="store the held series' past days the store lacks")
    ap.add_argument("--db", default=str(ROOT / "metrics.db"))
    ap.add_argument("--days", type=int, default=455)
    ap.add_argument("--budget-min", type=float, default=20.0)
    a = ap.parse_args(argv)
    spec = config.PROJECT_BY_NAME["Ethereum"]["ef_wallets"]
    ad = EFWallets(daily=object())
    if a.backfill:
        if not os.path.exists(a.db):
            print(f"no store at {a.db}")
            return 2
        backfill(a.db, a.days, a.budget_min * 60.0, ad)
        return 0
    if a.scan:
        res = scan_registry(spec["stake"]["beacon"][0], [w["address"] for w in spec["wallets"]])
        listed = {str(k).lower() for k in spec["stake"]["validators"]}
        new = [f for f in res["found"] if f[3] not in listed]
        print(f"REGISTRY: {res['entries']:,} validators at finalized slot {res['slot']}; {len(res['found'])} carry a "
              f"listed wallet as withdrawal address, Σ {sum(f[1] for f in res['found']):,.4f} ETH; "
              f"{len(new)} not in config's list")
        for f in new:
            print(f"  NOT LISTED: index {f[0]} {f[3]} {f[1]:,.4f} ETH {f[2]} -> {f[4]}")
        return 1 if new else 0
    block, when, reads, nonce = None, None, None, None
    for url in ad.endpoints(spec):
        try:
            block, when, reads, nonce = ad.read_wallets(spec, url)
            break
        except Exception as e:  # noqa: BLE001
            print(f"endpoint failed: {type(e).__name__}: {str(e)[:100]}")
    if reads is None:
        print("no endpoint answered")
        return 1
    print(f"finalized block {block}, {when:%Y-%m-%d %H:%M} UTC; treasury Safe nonce {nonce}")
    for w in spec["wallets"]:
        g = reads[w["address"].lower()]
        print(f"{w['tag']:9s} {w['address']} ETH {g['eth']:>14,.4f} WETH {(g['weth'] or 0.0):>10,.4f}  {w['key']}: "
              f"{w['source'][:150]}")
    for tag in ("PUBLISHED", "INFERRED"):
        print(f"{tag}: {sum_wallets(reads, spec['wallets'], tag)[0]:,.4f} ETH")
    total, n, slot, bad = ad.read_stake(spec)
    print(f"STAKED: {total:,.4f} ETH in {n} validator(s), slot {slot}" if total is not None else f"STAKED: not read — {bad}")
    print(json.dumps({"block": block, "day": str(when.date())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
