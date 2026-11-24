"""
fetch/aethir_chain.py — tier 2: AETHIR'S VESTING STOCK, ISSUER WALLETS, ISSUER STAKE AND THE DAILY FEE BURN, FROM THE
CHAIN (Round 20, Jake 2026-11-24). config.PROJECT_BY_NAME["Aethir"]["aethir_chain"] holds every address with the
evidence for it. Keyless public RPC; once a day; every series ALL-OR-NO-ROW (a figure without one of its parts is not
the figure).

ETHEREUM (ATH 0xbe0Ed413…), every balance at ONE finalized block:
  ath_vesting_unvested_tokens     Aethir's VestingWallet 0xf9d9f4ae…: Σ of the tranches of its ON-CHAIN schedule dated
                                  after the block (the schedule is the DistributionAdded event's 85 dated tranches, held
                                  in config). REFUSED when getDistribution(beneficiary) no longer matches it (the owner
                                  can re-schedule): totalAmount, and the tranches still to come.
  ath_vester_balance_tokens       Σ ATH.balanceOf of the three MerkleVester contracts (team / investor / advisor
                                  claims; funded by the genesis allocation Safe). Their schedules are Merkle leaves, not
                                  readable on chain: the balance is unvested + vested-but-unclaimed.
  ath_issuer_held_tokens          Σ ATH of the wallets tied to the issuer BY CONTRACT STATE (tag DIRECT), plus the
                                  VestingWallet's vested-but-unreleased tranche (its balance − the unvested figure).
  ath_issuer_inferred_held_tokens Σ ATH of the wallets tied to it by funding only (tag INFERRED) — never mixed.
  ath_insider_staked_tokens       Σ locked(wallet).amount in the AI and Gaming pools, every listed wallet.
  ath_issuer_eath_tokens          Σ eATH.balanceOf(wallet) — the EigenLayer pre-deposit vault's receipt (reference).
HISTORY, IN THE SAME RUN (so one run fills the windows; read again only when yesterday's run did not complete): the ATH Transfer logs to and from every listed address since the
token's first block give each address's balance at the end of every past day (today's balance − the later net inflow).
PROOF OF COMPLETENESS, per address: the balance today − the net inflow of ALL its logs must be 0 (it held nothing before
the token existed); otherwise NO history row is stored. The schedule series' past days are the schedule itself.

ARBITRUM (ATH 0xc87B37a5…): Transfer(burn contract 0xf8ec8e8e… -> 0x…dEaD) — the daily fee burn (docs.aethir.com
ath-burn-mechanism). gross_burn_tokens = Σ per UTC day (block time), every COMPLETE day from the launch day, a day with
no burn a measured 0; burn_address_balance = ATH.balanceOf(0x…dEaD). PROOF: Σ of every Transfer to 0x…dEaD (any sender)
since `from_block` (a block before the burn contract existed, where the dead address held the declared
`dead_before`) must equal that balance to 1e-6 ATH, else nothing is stored.

    python -m fetch.aethir_chain          the live tables (wallets, burns), nothing stored
"""
from __future__ import annotations

import logging
import os

import pandas as pd

import check_offline_items as _coi   # the keccak-checked selectors (tests/test_adapters.py)

from .base import tidy, today

log = logging.getLogger("token_metrics.fetch.aethir_chain")

SOURCE = "aethir_chain"
TIER = 2
POINT_SCHEDULE = "aethir_chain:VestingWallet.schedule"
POINT_VESTERS = "aethir_chain:vesters.balanceOf"
POINT_HELD = "aethir_chain:issuer.balanceOf"
POINT_INFERRED = "aethir_chain:issuer_inferred.balanceOf"
POINT_STAKE = "aethir_chain:pools.locked"
POINT_EATH = "aethir_chain:eATH.balanceOf"
POINT_BURN = "aethir_chain:burn_logs"
POINT_DEAD = "aethir_chain:dead.balanceOf"
# the function selectors live in check_offline_items.py (each checked against keccak by tests/test_adapters.py)
BALANCE_OF = _coi.SEL_BALANCE_OF
LOCKED = _coi.SEL_LOCKED_ADDRESS                      # locked(address) -> (int128 amount, uint256 end)
GET_DISTRIBUTION = _coi.SEL_GET_DISTRIBUTION     # VestingWallet.getDistribution(address)
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
WEI = 10 ** 18


def spec_of(p: dict) -> dict | None:
    return p.get("aethir_chain")


def _pad(a: str) -> str:
    return a.lower().replace("0x", "").rjust(64, "0")


def _addr(topic: str) -> str:
    return "0x" + str(topic)[-40:].lower()


def unvested(schedule, ts: float) -> float:
    """Σ of the schedule's tranches dated AFTER `ts` (a tranche dated at or before it has vested)."""
    return float(sum(a for d, a in schedule if d > ts))


def day_last_second(d) -> float:
    """23:59:59 UTC of day d — a tranche dated 00:00:00 of the next day vests on that next day, as a read made then
    sees it."""
    return (pd.Timestamp(d).normalize() + pd.Timedelta(days=1)).timestamp() - 1


def decode_distribution(result: str) -> tuple[float, float, list[tuple[int, float]]]:
    """(totalAmount, distributedAmount, [(date, amount)] still listed) from getDistribution's return data. A released
    tranche is deleted on chain (date 0, amount 0) and is left out."""
    b = bytes.fromhex(result[2:])
    w = [int.from_bytes(b[i:i + 32], "big") for i in range(0, len(b), 32)]
    n = w[3]
    left = [(w[4 + 2 * i], w[5 + 2 * i] / WEI) for i in range(n)]
    return w[0] / WEI, w[1] / WEI, [(d, a) for d, a in left if d]


def schedule_matches(spec_vw: dict, total: float, left: list, ts: float, tol: float = 1e-3) -> list[str]:
    """[] when the on-chain distribution is still the schedule on file, else what differs (the series is then refused:
    a re-scheduled vesting is a new schedule, to be read and declared, never silently mixed with the old one)."""
    bad = []
    if abs(total - float(spec_vw["total"])) > tol:
        bad.append(f"totalAmount {total:,.2f} is not the {float(spec_vw['total']):,.2f} on file")
    want = sorted((int(d), round(float(a), 2)) for d, a in spec_vw["schedule"] if d > ts)
    got = sorted((int(d), round(float(a), 2)) for d, a in left if d > ts)
    if want != got:
        bad.append(f"the tranches still to come differ from the schedule on file ({len(got)} on chain, Σ "
                   f"{sum(a for _d, a in got):,.2f}; {len(want)} on file, Σ {sum(a for _d, a in want):,.2f})")
    return bad


def day_end_balances(balance_now: float, block_now: int, logs: list[dict], address: str, days) -> dict | None:
    """{day: the address's balance at the END of that UTC day} for the past `days`, from its balance at `block_now` and
    its Transfer logs up to that block (each: block, ts, from, to, value). None when the logs do not account for the
    balance — today's balance less the net inflow of ALL of them is not 0 — or a log carries no time."""
    a = address.lower()
    mine = [e for e in logs if e["block"] <= block_now and a in (e["from"], e["to"]) and e["from"] != e["to"]]
    if any(not e.get("ts") for e in mine):
        return None
    net = sum(e["value"] if e["to"] == a else -e["value"] for e in mine)
    if abs(balance_now * WEI - net) > 10 ** 12:           # 1e-6 ATH: the logs are the whole history, or they are not
        return None
    out = {}
    for d in days:
        end = (pd.Timestamp(d).normalize() + pd.Timedelta(days=1)).timestamp()
        later = sum(e["value"] if e["to"] == a else -e["value"] for e in mine if e["ts"] >= end)
        out[pd.Timestamp(d).normalize()] = (balance_now * WEI - later) / WEI
    return out


def daily_burns(events: list[dict], burner: str, first_day, last_day) -> dict:
    """{day: Σ ATH the burn contract sent to the dead address that UTC day} for every day first..last (0 where none)."""
    out = {d.normalize(): 0.0 for d in pd.date_range(first_day, last_day, freq="D")} if pd.Timestamp(first_day) <= \
        pd.Timestamp(last_day) else {}
    for e in events:
        if e["from"] != burner.lower():
            continue
        d = pd.Timestamp(e["ts"], unit="s").normalize()
        if d in out:
            out[d] += e["value"] / WEI
    return out


def stray_transfers(events: list[dict], burner: str) -> list[dict]:
    """R21: every Transfer to the dead address NOT sent by the burn contract - {day, from, tokens, tx}, oldest first.
    Named on the burn's rows and excluded from the burn. It stays inside the balance proof (the dead address's balance
    is the sum of ALL its inflows), so a stray never makes the read refuse (2026-10-21: 0.0159 ATH from 0xd4627ecb…)."""
    out = [{"day": str(pd.Timestamp(e["ts"], unit="s").date()), "from": e["from"], "tokens": e["value"] / WEI,
            "tx": e.get("tx", "")} for e in events if e["from"] != burner.lower()]
    return sorted(out, key=lambda x: x["day"])


class AethirChain:
    """Aethir's vesting stock, issuer wallets, issuer stake and fee burn, once a day (R20)."""
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, session=None, daily=None, **_ignored):
        self.session = session              # tests inject a stub
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily

    # ------------------------------------------------------------------------------------------------ transport
    def _sess(self):
        if self.session is None:
            import requests
            from .base import USER_AGENT
            self.session = requests.Session()
            self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"})
        return self.session

    def rpc(self, url: str, method: str, params: list):
        from .base import CONNECT_TIMEOUT_S, READ_TIMEOUT_S, mask_url
        r = self._sess().post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                              timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S))
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} from {mask_url(url)}")
        out = r.json()
        if not isinstance(out, dict) or "result" not in out or out["result"] is None:
            raise RuntimeError(f"{method}: {str((out or {}).get('error') if isinstance(out, dict) else out)[:120]}")
        return out["result"]

    @staticmethod
    def endpoints(urls, env: str | None, keyed_first: bool = True) -> list[str]:
        keyed = os.environ.get(env or "", "")
        return (([keyed] if keyed and keyed_first else []) + list(urls) + ([keyed] if keyed and not keyed_first else []))

    def first(self, urls: list[str], fn):
        """fn(url) from the first endpoint that answers; (result, host) or raises with every endpoint's error."""
        from .base import mask_url
        errs = []
        for u in urls:
            try:
                return fn(u), mask_url(u).split("/")[2]
            except Exception as e:  # noqa: BLE001 — one endpoint's failure is said, the next is tried
                errs.append(f"{mask_url(u).split('/')[2]}: {type(e).__name__}: {str(e)[:120]}")
        raise RuntimeError("; ".join(errs) or "no endpoint declared")

    def call(self, url: str, to: str, data: str, tag: str) -> int:
        return int(self.rpc(url, "eth_call", [{"to": to, "data": data}, tag]), 16)

    def logs(self, url: str, flt: dict, lo: int, hi: int, step: int) -> list[dict]:
        """Transfer logs lo..hi in `step`-block pieces, each {block, ts, from, to, value}. A log the endpoint does not
        date (no blockTimestamp, or 0x0) is dated from its block header."""
        out, stamps = [], {}
        while lo <= hi:
            top = min(lo + step - 1, hi)
            for e in self.rpc(url, "eth_getLogs", [dict(flt, fromBlock=hex(lo), toBlock=hex(top))]):
                bn = int(e["blockNumber"], 16)
                ts = int(str(e.get("blockTimestamp") or "0x0"), 16)
                if not ts:
                    if bn not in stamps:
                        stamps[bn] = int(self.rpc(url, "eth_getBlockByNumber", [hex(bn), False])["timestamp"], 16)
                    ts = stamps[bn]
                out.append({"block": bn, "ts": ts, "from": _addr(e["topics"][1]), "to": _addr(e["topics"][2]),
                            "value": int(e["data"], 16) if e["data"] not in ("0x", "") else 0,
                            "tx": e.get("transactionHash", ""), "li": e.get("logIndex", "")})
            lo = top + 1
        return out

    # ------------------------------------------------------------------------------------------------ Ethereum
    def read_ethereum(self, spec: dict, url: str) -> dict:
        """Every Ethereum figure at ONE finalized block of one endpoint."""
        blk = self.rpc(url, "eth_getBlockByNumber", ["finalized", False])
        tag, ts = blk["number"], int(blk["timestamp"], 16)
        token, vw = spec["token"], spec["vesting_wallet"]
        bal = lambda a, t=token: self.call(url, t, BALANCE_OF + _pad(a), tag) / WEI  # noqa: E731
        total, done, left = decode_distribution(
            self.rpc(url, "eth_call", [{"to": vw["address"], "data": GET_DISTRIBUTION + _pad(vw["beneficiary"])}, tag]))
        got = {"block": int(tag, 16), "ts": ts, "when": pd.Timestamp(ts, unit="s"),
               "schedule_bad": schedule_matches(vw, total, left, ts), "distributed": done,
               "unvested": unvested(vw["schedule"], ts), "vw_balance": bal(vw["address"]),
               "vesters": {v["address"].lower(): bal(v["address"]) for v in spec["vesters"]},
               "wallets": {w["address"].lower(): bal(w["address"]) for w in spec["wallets"]},
               "locked": {}, "eath": {}}
        st = spec["stake"]
        for w in spec["wallets"]:
            a = w["address"].lower()
            got["locked"][a] = sum(
                int(self.rpc(url, "eth_call", [{"to": pool, "data": LOCKED + _pad(a)}, tag])[2:66], 16) / WEI
                for pool in st["pools"].values())
            got["eath"][a] = bal(a, st["eath"])
        return got

    def read_history(self, spec: dict, url: str, got: dict, days) -> dict | None:
        """{address: {day: end-of-day balance}} for every vester and listed wallet (and the VestingWallet), or None
        with the reason logged when any address's logs do not account for its balance."""
        addrs = ([spec["vesting_wallet"]["address"]] + [v["address"] for v in spec["vesters"]]
                 + [w["address"] for w in spec["wallets"]])
        tops = ["0x" + _pad(a) for a in addrs]
        lo, hi, step = int(spec["token_first_block"]), got["block"], int(spec["log_step"])
        logs = self.logs(url, {"address": spec["token"], "topics": [TRANSFER, tops]}, lo, hi, step)
        logs += self.logs(url, {"address": spec["token"], "topics": [TRANSFER, None, tops]}, lo, hi, step)
        seen, uniq = set(), []
        for e in logs:                                   # a transfer between two listed addresses comes back twice
            k = (e["block"], e["tx"], e.get("li"))        # one log, by its place in the block
            if k not in seen or not e["tx"] or e.get("li") in ("", None):
                seen.add(k)
                uniq.append(e)
        now = dict(got["vesters"], **got["wallets"])
        now[spec["vesting_wallet"]["address"].lower()] = got["vw_balance"]
        out = {}
        for a in addrs:
            bals = day_end_balances(now[a.lower()], hi, uniq, a, days)
            if bals is None:
                self.history_refused = f"the Transfer logs of {a[:10]}… do not account for its balance at block {hi}"
                return None
            out[a.lower()] = bals
        return out

    def _eth_frames(self, name: str, spec: dict, got: dict, host: str, hist: dict | None, days) -> list[tuple]:
        """[(metric, frame, message)] — the six Ethereum series, today's row and (with `hist`) the past days'."""
        import config
        vw, when, block = spec["vesting_wallet"], got["when"], got["block"]
        at = f"finalized block {block} {when:%Y-%m-%d %H:%M} UTC via {host}"
        day = when.normalize()
        past = [d for d in days if d < day] if hist is not None else []
        out = []

        def add(metric, point, today_v, detail, hist_fn, hist_note):
            rows = [(day, today_v)]
            frames = [tidy(rows, name, metric, f"{point}[{detail}; {at}]", TIER)]
            if past and hist_fn is not None:
                h = [(d, hist_fn(d)) for d in past]
                frames.append(tidy(h, name, metric, f"{point}[{hist_note}; scanned to block {block} via {host}]", TIER))
            band = config.METRICS[metric]
            vals = [v for f in frames for v in f["value"]]
            if any(not (band["sanity_min"] <= v <= band["sanity_max"]) for v in vals):
                out.append((metric, None, f"{metric}: refused — a value is outside {band['sanity_min']:,.0f}.."
                                          f"{band['sanity_max']:,.0f}"))
                return
            f = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["date"], keep="first")
            out.append((metric, f, f"{metric}: {today_v:,.2f} ATH at block {block}"
                                   + (f"; {len(f) - 1} past day(s) from the logs" if len(f) > 1 else "")))

        first_sched = pd.Timestamp(vw["added_ts"], unit="s").normalize()
        add(spec["unvested_metric"], POINT_SCHEDULE, got["unvested"],
            f"VestingWallet {vw['address'][:10]}…: Σ tranches of the on-chain schedule dated after the block; "
            f"getDistribution matches the schedule on file (distributed {got['distributed']:,.0f})",
            lambda d: unvested(vw["schedule"], day_last_second(d)),
            "the schedule on file (DistributionAdded, block " + f"{vw['added_block']}): tranches dated after the day's end")
        if out and out[-1][0] == spec["unvested_metric"] and out[-1][1] is not None:   # no day before the schedule
            f = out[-1][1]
            out[-1] = (out[-1][0], f[pd.to_datetime(f["date"]) >= first_sched], out[-1][2])
        parts = "; ".join(f"{v['key']} {v['address'][:10]}… {got['vesters'][v['address'].lower()]:,.2f}"
                          for v in spec["vesters"])
        add(spec["vester_metric"], POINT_VESTERS, sum(got["vesters"].values()),
            f"{len(spec['vesters'])} MerkleVester contract(s), balanceOf: {parts}",
            (lambda d: sum(hist[v["address"].lower()][d] for v in spec["vesters"])) if hist else None,
            "each vester's balance at the day's end, from its Transfer logs (they account for today's balance to 1e-6)")
        vwa = vw["address"].lower()
        for tag, metric, point in (("DIRECT", spec["held_metric"], POINT_HELD),
                                   ("INFERRED", spec["inferred_metric"], POINT_INFERRED)):
            ws = [w for w in spec["wallets"] if w["tag"] == tag]
            tot = sum(got["wallets"][w["address"].lower()] for w in ws)
            parts = "; ".join(f"{w['key']} {w['address'][:10]}… {got['wallets'][w['address'].lower()]:,.2f}" for w in ws)
            extra = 0.0
            if tag == "DIRECT":                          # vested, not yet released: only the beneficiary Safe can get it
                extra = max(0.0, got["vw_balance"] - got["unvested"])
                parts += f"; VestingWallet vested-unreleased {extra:,.2f}"

            def hist_fn(d, ws=ws, tag=tag):
                v = sum(hist[w["address"].lower()][d] for w in ws)
                if tag == "DIRECT":
                    v += max(0.0, hist[vwa][d] - unvested(vw["schedule"], day_last_second(d)))
                return v
            add(metric, point, tot + extra, f"{len(ws)} {tag} issuer wallet(s), ATH balanceOf: {parts}",
                hist_fn if hist else None,
                f"each {tag} wallet's balance at the day's end, from its Transfer logs")
        add(spec["stake"]["metric"], POINT_STAKE, sum(got["locked"].values()),
            f"Σ locked(wallet).amount in {', '.join(spec['stake']['pools'])} for {len(spec['wallets'])} listed wallet(s)",
            None, "")
        add(spec["stake"]["eath_metric"], POINT_EATH, sum(got["eath"].values()),
            "Σ eATH.balanceOf(wallet): " + "; ".join(
                f"{w['key']} {got['eath'][w['address'].lower()]:,.2f}" for w in spec["wallets"]
                if got["eath"][w["address"].lower()]), None, "")
        return out

    # ------------------------------------------------------------------------------------------------ Arbitrum
    def read_burn(self, burn: dict, url: str) -> dict:
        """Every Transfer to the dead address since `from_block`, the head they were read to, and the dead balance at
        that head. Raises when the logs do not sum to the balance."""
        blk = self.rpc(url, "eth_getBlockByNumber", ["latest", False])
        head, ts = int(blk["number"], 16), int(blk["timestamp"], 16)
        ev = self.logs(url, {"address": burn["token"], "topics": [TRANSFER, None, "0x" + _pad(burn["dead"])]},
                       int(burn["from_block"]), head, int(burn["log_step"]))
        dead = self.call(url, burn["token"], BALANCE_OF + _pad(burn["dead"]), hex(head))
        before = int(round(float(burn.get("dead_before") or 0.0) * WEI))
        if abs(dead - before - sum(e["value"] for e in ev)) > 10 ** 12:
            raise RuntimeError(f"Σ Transfer to the dead address {sum(e['value'] for e in ev) / WEI:,.6f} since block "
                               f"{burn['from_block']} (+ {before / WEI:,.6f} held before it) is not its balance "
                               f"{dead / WEI:,.6f} at block {head}: the scan is not the whole history")
        return {"head": head, "ts": ts, "events": ev, "dead": dead / WEI}

    def _burn_frames(self, name: str, burn: dict, got: dict, host: str) -> list[tuple]:
        when = pd.Timestamp(got["ts"], unit="s")
        last = when.normalize() - pd.Timedelta(days=1)                   # the last COMPLETE UTC day
        days = daily_burns(got["events"], burn["burner"], burn["launch"], last)
        mine = [e for e in got["events"] if e["from"] == burn["burner"].lower()]
        strays = stray_transfers(got["events"], burn["burner"])
        other = sum(x["tokens"] for x in strays)
        named = "; ".join(f"{x['day']} {x['from'][:10]}… {x['tokens']:,.6f} ATH tx {str(x['tx'])[:12]}…"
                          for x in strays[:5])
        note = (f"Σ Transfer({burn['burner'][:10]}… -> 0x…dEaD) per UTC day, {len(mine)} burn(s) "
                f"{sum(e['value'] for e in mine) / WEI:,.4f} ATH to block {got['head']} via {host}; every Transfer to "
                f"the dead address sums to its balance; {len(strays)} transfer(s) from other senders, {other:,.6f} ATH, "
                f"NOT the burn (inside the balance proof, out of the burn)" + (f": {named}" if strays else ""))
        out = []
        if days:
            out.append((burn["metric"], tidy(sorted(days.items()), name, burn["metric"], f"{POINT_BURN}[{note}]", TIER),
                        f"{burn['metric']}: {len(days)} complete day(s) {min(days).date()}..{max(days).date()}, Σ "
                        f"{sum(days.values()):,.2f} ATH burned"))
        out.append((burn["stock_metric"],
                    tidy([(when.normalize(), got["dead"])], name, burn["stock_metric"],
                         f"{POINT_DEAD}[ATH.balanceOf(0x…dEaD) on Arbitrum at block {got['head']} "
                         f"{when:%Y-%m-%d %H:%M} UTC via {host}]", TIER),
                    f"{burn['stock_metric']}: {got['dead']:,.4f} ATH at the dead address (block {got['head']})"))
        return out

    # ------------------------------------------------------------------------------------------------ the run
    @staticmethod
    def mark_key(name: str, part: str, spec: dict) -> str:
        """R21 (Jake 2026-11-25): the day's mark names the PROJECT and the SERIES it vouches for -
        aethir_chain:<project>:<metric> - so the run keeps it only when that series reached the store
        (token_metrics._mark_stored). The R20 keys ("aethir_chain:burn", "aethir_chain:ethereum") named no series: a
        mark whose rows never reached the store was kept, and every later run that day logged "read today already",
        0 rows."""
        return f"{SOURCE}:{name}:" + (spec["burn"]["metric"] if part == "burn" else spec["held_metric"])

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = spec_of(p)
            if not spec:
                continue
            name, day = p["name"], str(today().date())
            # R21: THE BURN FIRST - one cheap Arbitrum read (~11 s) that no longer waits behind the Ethereum history (14
            # eth_getLogs of 1,000,000 blocks, 58 s measured alone on 2026-11-24): a tier cut off at its budget keeps
            # the burn's frame (fetch._abandon keeps what a timed-out tier produced; gross_burn_tokens is a flow)
            for part, fn in (("burn", self._run_burn), ("ethereum", self._run_ethereum)):
                dkey = self.mark_key(name, part, spec)
                if not self.daily.due(dkey, day):
                    out.mark_current(self.SOURCE, name, spec["held_metric"] if part == "ethereum"
                                     else spec["burn"]["metric"], f"Aethir chain read ({part}): read today already", TIER)
                    continue
                # the past days are read again only when yesterday's run did not complete (the mark holds the last
                # completed day): a first run, a missed day or a refused history — never the same 455 days every day
                yday = str((today() - pd.Timedelta(days=1)).date())
                try:
                    ok = fn(name, spec, out, self.daily.due(dkey, yday))
                except Exception as e:  # noqa: BLE001 — said on the Run Log; the other part is still read
                    out.fail(self.SOURCE, name, f"Aethir chain read ({part}): NOTHING STORED — {str(e)[:300]}", TIER)
                    ok = False
                if ok:
                    self.daily.done(dkey, day)

    def _run_ethereum(self, name: str, spec: dict, out, need_history: bool = True) -> bool:
        got, host = self.first(self.endpoints(spec["rpc"], spec.get("rpc_env")), lambda u: self.read_ethereum(spec, u))
        if got["schedule_bad"]:
            out.fail(self.SOURCE, name, "Aethir VestingWallet: NOTHING STORED — " + "; ".join(got["schedule_bad"])
                     + " (the owner can re-schedule: read the new DistributionUpdated event and declare it)", TIER)
            return False
        day = got["when"].normalize()
        days = [d.normalize() for d in pd.date_range(day - pd.Timedelta(days=int(spec["history_days"])),
                                                     day - pd.Timedelta(days=1), freq="D")]
        hist, hist_host, self.history_refused = None, host, ""
        if not need_history:
            for metric, frame, msg in self._eth_frames(name, spec, got, host, None, days):
                if frame is None:
                    out.fail(self.SOURCE, name, msg, TIER)
                    return False
                out.add(frame, self.SOURCE, name, msg + " (the past days are held: yesterday's run completed)", TIER)
            return True
        try:
            # logs: the declared public log endpoints first (a keyed free plan caps eth_getLogs at a few blocks)
            hist, hist_host = self.first(self.endpoints(spec["log_rpc"], spec.get("rpc_env"), keyed_first=False),
                                         lambda u: self.read_history(spec, u, got, days))
        except Exception as e:  # noqa: BLE001
            self.history_refused = str(e)[:240]
        ok = True
        for metric, frame, msg in self._eth_frames(name, spec, got, host, hist, days):
            if frame is None:
                ok = False
                out.fail(self.SOURCE, name, msg, TIER)
            else:
                out.add(frame, self.SOURCE, name, msg, TIER)
        if hist is None:
            # today's rows stand; the past days are asked again tomorrow (the day's mark is not set)
            out.fail(self.SOURCE, name, "Aethir chain read: NO PAST DAY STORED — "
                     + (self.history_refused or "the log scan gave nothing"), TIER)
            return False
        return ok

    def _run_burn(self, name: str, spec: dict, out, _need_history: bool = True) -> bool:
        burn = spec["burn"]
        got, host = self.first(self.endpoints(burn["rpc"], burn.get("rpc_env"), keyed_first=False),
                               lambda u: self.read_burn(burn, u))
        for _metric, frame, msg in self._burn_frames(name, burn, got, host):
            out.add(frame, self.SOURCE, name, msg, TIER)
        return True


def main() -> int:  # pragma: no cover — a live read for a person
    import config
    p = config.PROJECT_BY_NAME["Aethir"]
    spec, ad = spec_of(p), AethirChain(daily=object())
    got, host = ad.first(ad.endpoints(spec["rpc"], spec.get("rpc_env")), lambda u: ad.read_ethereum(spec, u))
    print(f"ETHEREUM finalized block {got['block']} {got['when']:%Y-%m-%d %H:%M:%S} UTC via {host}")
    print(f"  VestingWallet {spec['vesting_wallet']['address']}  balance {got['vw_balance']:,.4f}  unvested "
          f"{got['unvested']:,.4f}  vested-unreleased {got['vw_balance'] - got['unvested']:,.4f}  "
          f"schedule {'matches' if not got['schedule_bad'] else got['schedule_bad']}")
    for v in spec["vesters"]:
        print(f"  VESTER   {v['key']:<22} {v['address']}  {got['vesters'][v['address'].lower()]:>20,.4f}")
    for w in spec["wallets"]:
        a = w["address"].lower()
        print(f"  {w['tag']:<8} {w['key']:<22} {w['address']}  {got['wallets'][a]:>20,.4f}  locked {got['locked'][a]:,.2f}"
              f"  eATH {got['eath'][a]:,.4f}")
    b, bhost = ad.first(ad.endpoints(spec["burn"]["rpc"], spec["burn"].get("rpc_env"), keyed_first=False),
                        lambda u: ad.read_burn(spec["burn"], u))
    print(f"ARBITRUM block {b['head']} via {bhost}: dead balance {b['dead']:,.6f}")
    tot = 0.0
    for e in b["events"]:
        if e["from"] == spec["burn"]["burner"].lower():
            tot += e["value"] / WEI
            print(f"  {pd.Timestamp(e['ts'], unit='s'):%Y-%m-%d %H:%M:%S}  block {e['block']}  {e['value'] / WEI:>16,.4f}  "
                  f"cumulative {tot:>18,.4f}  {e['tx']}")
    return 0


if __name__ == "__main__":
    import progress                                    # stdout line-buffered when piped (2026-10-11)
    progress.line_buffered()
    raise SystemExit(main())
