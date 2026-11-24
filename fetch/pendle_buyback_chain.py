"""
fetch/pendle_buyback_chain.py — tier 2: PENDLE'S BUYBACK, READ FROM ITS OWN BUYBACK CONTRACT ON ETHEREUM (Round 21, Jake
2026-11-25 item 5: "Pendle stale buying pressure ... Wire Pendle's own per-epoch buyback amounts as the primary source,
with DefiLlama as the cross-check").

WHAT THE CONTRACT DOES (read 2026-11-24, every PENDLE Transfer in and out of 0x9e08C549…896b since block 24,218,213):
each two-week sPENDLE epoch's buyback is BOUGHT by Pendle's router (0x8888…f946 -> the contract, ~75 fills over four
days, the 13th-17th day after the epoch's start) and then PAID in one transfer to sPENDLE (0x9999…4144) — where it
arrives STAKED. 21 payouts, 2026-02-13..2026-11-21; every payout equals the purchases since the one before it to the wei,
and 19 of the 21 equal Pendle's own per-epoch figure (spendle/data buybackAmounts) within 2 PENDLE (the API rounds); the
epochs of 2026-01-29 and 2026-02-24 read 4,405.94 and 6,992.00 PENDLE MORE in the API than the contract paid (not found
on chain; NOT VERIFIED).

Stored, one row per complete UTC day from the contract's first scanned day to yesterday (a day with no transfer is a
MEASURED 0 — the chain was read and nothing moved):
    pendle_buyback_bought_tokens  PENDLE received from the declared router(s): the buyback, dated the day it was bought
    pendle_buyback_paid_tokens    PENDLE paid from the contract to sPENDLE: the epoch's distribution, dated its payday
Nothing is stored unless Σ in − Σ out equals PENDLE.balanceOf(contract) at the read block TO THE WEI. An inflow from any
other sender is named in the log and not counted as bought; an outflow to any other address likewise.

The whole history is read every run (about 2.3M blocks: a few eth_getLogs calls on the public log route, config.LOG_RPC),
so a missed run never leaves a hole.
"""
from __future__ import annotations

import logging
import time

import pandas as pd

from .base import USER_AGENT, LogEntry, tidy

log = logging.getLogger("token_metrics.fetch.pendle_buyback_chain")

SOURCE = "pendle_buyback_chain"
TIER = 2
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _pad(addr: str) -> str:
    return "0x" + "0" * 24 + addr.lower()[2:]


def _int(v) -> int:
    if isinstance(v, int):
        return int(v)
    if isinstance(v, (bytes, bytearray)):
        return int.from_bytes(bytes(v), "big") if v else 0
    if hasattr(v, "hex") and not isinstance(v, str):
        v = v.hex()
    s = str(v)
    if s in ("", "0x"):
        return 0
    return int(s, 16) if s.startswith("0x") or any(c in "abcdef" for c in s.lower()) else int(s)


def _hex(v) -> str:
    if isinstance(v, (bytes, bytearray)):
        return "0x" + bytes(v).hex()
    s = v.hex() if hasattr(v, "hex") and not isinstance(v, str) else str(v)
    return s if s.startswith("0x") else "0x" + s


def norm(x, ts=None) -> dict:
    """An RPC log (dict or AttributeDict) -> {block, ts, from, to, wei, tx, idx}."""
    g = (lambda k: x.get(k)) if hasattr(x, "get") else (lambda k: getattr(x, k, None))      # noqa: E731
    topics = [_hex(t).lower() for t in (g("topics") or [])]
    stamp = g("blockTimestamp")
    return {"block": _int(g("blockNumber")), "ts": int(ts if ts is not None else (_int(stamp) if stamp else 0)),
            "from": "0x" + topics[1][-40:], "to": "0x" + topics[2][-40:], "wei": _int(g("data")),
            "tx": _hex(g("transactionHash")).lower(), "idx": _int(g("logIndex"))}


def day_of(ts: int) -> pd.Timestamp:
    return pd.Timestamp(int(ts), unit="s").normalize()


def daily_series(ins: list, outs: list, routers, payees, first_day, last_day) -> dict:
    """{"bought": {day: tokens}, "paid": {day: tokens}, "other_in": [...], "other_out": [...]} over EVERY day
    first_day..last_day (0 where nothing moved). Bought = inflows from a declared router; paid = outflows to a declared
    payee; anything else is listed, never counted."""
    routers = {r.lower() for r in routers}
    payees = {p.lower() for p in payees}
    days = pd.date_range(pd.Timestamp(first_day).normalize(), pd.Timestamp(last_day).normalize(), freq="D")
    bought = {d: 0.0 for d in days}
    paid = {d: 0.0 for d in days}
    other_in, other_out = [], []
    for e in ins:
        d = day_of(e["ts"])
        if d not in bought:
            continue
        if e["from"].lower() in routers:
            bought[d] += e["wei"] / 1e18
        else:
            other_in.append(e)
    for e in outs:
        d = day_of(e["ts"])
        if d not in paid:
            continue
        if e["to"].lower() in payees:
            paid[d] += e["wei"] / 1e18
        else:
            other_out.append(e)
    return {"bought": bought, "paid": paid, "other_in": other_in, "other_out": other_out}


def epoch_check(payouts: list, api: list, epoch_days: int = 14, lag_days=(14, 21), tol: float = 2.0) -> list:
    """Each payout (day, tokens) against Pendle's per-epoch figure: the API epoch (start, tokens) whose start lies
    lag_days before the payday (observed 15..20 days). Returns [(epoch_start, payday, paid, api_tokens, diff, ok)];
    a payout with no epoch in the window has epoch_start None."""
    rows = []
    for d, v in payouts:
        d = pd.Timestamp(d).normalize()
        cand = [(s, a) for s, a in api if d - pd.Timedelta(days=lag_days[1]) <= pd.Timestamp(s) <= d - pd.Timedelta(
            days=lag_days[0])]
        if not cand:
            rows.append((None, d, v, None, None, False))
            continue
        s, a = max(cand, key=lambda t: pd.Timestamp(t[0]))
        rows.append((pd.Timestamp(s).normalize(), d, v, float(a), v - float(a), abs(v - float(a)) <= tol))
    return rows


class PendleBuybackChain:
    """One project (Pendle), one contract, the whole history every run."""

    def __init__(self, reader=None, get_api=None, deadline_s: float | None = None):
        self.reader = reader
        self.get_api = get_api
        self.deadline_s = deadline_s

    # ----- eth_getLogs over the log route, narrowing on a range an endpoint calls too wide -----
    def _logs(self, chain: str, topics: list, address: str, lo: int, hi: int, chunk: int, deadline: float,
              said: list) -> list:
        r = self.reader
        urls = list(r.log_endpoints(chain)) if hasattr(r, "log_endpoints") else []
        too_wide = tuple(getattr(r, "LOGS_RANGE_TOO_WIDE", ()))
        out, cur, span = [], int(lo), max(1, int(chunk))
        while cur <= hi:
            if time.monotonic() > deadline:
                raise TimeoutError(f"budget spent at block {cur:,} of {hi:,}")
            upper = min(cur + span - 1, hi)
            flt = {"address": r.checksum(address), "topics": topics, "fromBlock": int(cur), "toBlock": int(upper)}
            got, narrowed, errs = None, False, []
            for u in urls:
                cap = r.getlogs_cap(u) if hasattr(r, "getlogs_cap") else None
                if cap is not None and upper - cur + 1 > cap:
                    continue
                try:
                    got = list(r.make_web3(chain, u, 60).eth.get_logs(flt))
                    said.append(f"{_host(u)} {cur:,}..{upper:,}: {len(got)}")
                    break
                except Exception as e:  # noqa: BLE001
                    msg = _detail(r, e)
                    errs.append(f"{_host(u)}: {msg[:120]}")
                    if any(x in msg.lower() for x in too_wide) and span > 1:
                        narrowed = True
                        break
            if narrowed:
                span = max(1, span // 2)
                continue
            if got is None:
                raise RuntimeError(f"blocks {cur:,}..{upper:,}: no endpoint served eth_getLogs — " + "; ".join(errs))
            out += got
            cur = upper + 1
        return out

    def _ts(self, chain: str, logs: list) -> list:
        """Normalised events; a log without blockTimestamp gets its block's time from the chain (one read a block)."""
        evs = []
        for x in logs:
            e = norm(x)
            if not e["ts"]:
                e["ts"] = int(self.reader.block_timestamp(chain, e["block"]))
            evs.append(e)
        return evs

    def run(self, projects: list[dict], window_days, out):
        import config                                                   # noqa: PLC0415
        spec = getattr(config, "PENDLE_BUYBACK_CHAIN", None)
        if not spec or not any(p["name"] == spec["project"] for p in projects):
            return
        name, chain = spec["project"], spec["chain"]
        deadline = time.monotonic() + float(self.deadline_s or spec.get("budget_s") or 100)
        if self.reader is None:
            from .chain import ChainReader                              # noqa: PLC0415
            self.reader = ChainReader()
        said: list = []
        try:
            w3 = self.reader.web3(chain)
            head = int(w3.eth.block_number) - int(spec.get("confirmations", 20))
            head_ts = int(w3.eth.get_block(head)["timestamp"])
            c = spec["contract"]
            ins = self._ts(chain, self._logs(chain, [TRANSFER, None, _pad(c)], spec["token"], spec["from_block"], head,
                                             spec["chunk_blocks"], deadline, said))
            outs = self._ts(chain, self._logs(chain, [TRANSFER, _pad(c), None], spec["token"], spec["from_block"],
                                              head, spec["chunk_blocks"], deadline, said))
            tok = w3.eth.contract(address=self.reader.checksum(spec["token"]),
                                  abi=[{"name": "balanceOf", "type": "function", "stateMutability": "view",
                                        "inputs": [{"name": "a", "type": "address"}],
                                        "outputs": [{"name": "", "type": "uint256"}]}])
            bal = int(tok.functions.balanceOf(self.reader.checksum(c)).call(block_identifier=head))
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            from .chain import redact_urls                              # noqa: PLC0415
            msg = f"{spec['metric']}: Pendle's buyback contract not read — {redact_urls(e)}"[:600]
            out.fail(SOURCE, name, msg, TIER)
            out.gap(name, spec["metric"], reason=msg, tiers_attempted="2",
                    suggestion="Re-run; the Ethereum log route is config.LOG_RPC (mainnet.gateway.tenderly.co).")
            return
        net = sum(e["wei"] for e in ins) - sum(e["wei"] for e in outs)
        if net != bal:
            msg = (f"{spec['metric']}: DOES NOT RECONCILE at block {head:,} — Σ in − Σ out = {net} wei, "
                   f"balanceOf = {bal} wei ({len(ins)} in / {len(outs)} out). NOTHING STORED.")
            out.fail(SOURCE, name, msg, TIER)
            out.gap(name, spec["metric"], reason=msg, tiers_attempted="2",
                    suggestion="Re-run once; a second difference is real and is not absorbed by a tolerance.")
            return
        first = pd.Timestamp(spec["first_day"]) if spec.get("first_day") else (
            day_of(min(e["ts"] for e in ins)) if ins else None)
        # complete UTC days only: the read block must be past the day's end
        last = day_of(head_ts) - pd.Timedelta(days=1)
        if first is None or last < first:
            out.log.append(LogEntry(SOURCE, name, 0, "ok", f"{spec['metric']}: no complete day to store yet", TIER))
            return
        ser = daily_series(ins, outs, spec["routers"], spec["payees"], first, last)
        src = f"{SOURCE}:{chain}"
        for metric, key in ((spec["metric"], "bought"), (spec["paid_metric"], "paid")):
            frame = tidy(sorted(ser[key].items()), name, metric, src, TIER)
            tot = sum(ser[key].values())
            nz = [d for d, v in ser[key].items() if v]
            out.add(frame, SOURCE, name,
                    f"{metric}: {len(frame)} day(s) {first.date()}..{last.date()} ({len(nz)} with a transfer), "
                    f"{tot:,.2f} PENDLE; reconciled to balanceOf at block {head:,} to the wei "
                    f"({bal / 1e18:,.6f} PENDLE held); {len(said)} eth_getLogs call(s)", TIER)
        if ser["other_in"] or ser["other_out"]:
            out.log.append(LogEntry(SOURCE, name, 0, "ok",
                                    f"{spec['metric']}: NOT COUNTED — {len(ser['other_in'])} inflow(s) from a sender "
                                    f"other than the router(s), {len(ser['other_out'])} outflow(s) to an address other "
                                    f"than sPENDLE: " + "; ".join(
                                        f"{day_of(e['ts']).date()} {e['wei'] / 1e18:,.2f} {e['from']}->{e['to']}"
                                        for e in (ser["other_in"] + ser["other_out"])[:6]), TIER))
        self._against_api(name, spec, ser, out)

    def _against_api(self, name, spec, ser, out):
        """Each payout against Pendle's own per-epoch figure (spendle/data buybackAmounts) — said, never blocking."""
        try:
            payload = (self.get_api or _get)(spec["api_url"])
            h = payload["sPendleHistoricalData"]
            api = [(pd.Timestamp(int(float(t)), unit="s").normalize(), float(a or 0))
                   for t, a in zip(h["timestamps"], h["buybackAmounts"])]
        except Exception as e:  # noqa: BLE001
            out.log.append(LogEntry(SOURCE, name, 0, "ok", f"{spec['metric']}: per-epoch check against spendle/data "
                                                           f"not made — {str(e)[:200]}", TIER))
            return
        pays = [(d, v) for d, v in sorted(ser["paid"].items()) if v]
        rows = epoch_check(pays, api, tol=float(spec.get("epoch_tol_tokens", 2.0)))
        ok = sum(1 for r in rows if r[5])
        bad = [r for r in rows if not r[5]]
        out.log.append(LogEntry(SOURCE, name, 0, "ok",
                                f"{spec['metric']}: per epoch, {ok} of {len(rows)} payout(s) equal Pendle's "
                                f"buybackAmounts within {spec.get('epoch_tol_tokens', 2.0)} PENDLE"
                                + ("; DIFFER: " + "; ".join(
                                    f"epoch {r[0].date() if r[0] is not None else '?'} paid {r[1].date()} "
                                    f"{r[2]:,.2f} vs API {r[3] if r[3] is None else f'{r[3]:,.2f}'}" for r in bad)
                                   if bad else ""), TIER))


def _host(u: str) -> str:
    from .chain import rpc_host                                         # noqa: PLC0415
    return rpc_host(u)


def _detail(r, e) -> str:
    from .chain import redact_urls                                      # noqa: PLC0415
    try:
        return redact_urls(r.error_detail(e))
    except Exception:  # noqa: BLE001
        return redact_urls(str(e))


def _get(url: str):
    import requests                                                     # noqa: PLC0415
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=(10, 30))
    resp.raise_for_status()
    return resp.json()
