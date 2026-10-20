"""
fetch/ccip_value.py — tier 1: the USD VALUE OF TOKENS MOVED THROUGH CHAINLINK CCIP per UTC day, and the number of
token-moving messages (G8, 2026-10-20). Keyless, first-party API + DefiLlama's coins API. Every route below was called
live on 2026-10-20 (CCIP API spec v2.13.0, https://api.ccip.chain.link/docs/):

    GET api.ccip.chain.link/v2/messages?limit=1000&environment=mainnet[&cursor=…]
        newest first, {data: [...], pagination: {hasNextPage, cursor}}; NO date filter — the cursor is walked. Each
        record carries sendTimestamp, sourceNetworkInfo.chainSelector / destNetworkInfo.chainSelector and
        sourceTokenAmount {tokenAddress, tokenAmount} (raw units; null when no token moves). 1,000 messages are
        ~12 h: 60 pages reached 2026-09-20 05:18 from 2026-10-20 09:20 (0.66 s a page), so ~2 pages a day.
    GET api.ccip.chain.link/v2/messages/{messageId}       version ("1.5.0" / "1.6.0" / "1.6.2" / "2.0.0") and
                                                          tokenAmounts[] — EVERY token the message moved
    GET api.ccip.chain.link/v2/tokens?environment=mainnet&limit=1000&reviewedOnly=false
                                                          3,529 tokens in 4 pages: decimals, symbol, groupId
    GET coins.llama.fi/prices/historical/{12:00 UTC}/{chain}:{addr},…?searchWidth=12h
                                                          the day's price (any point inside the day), batched

robots.txt of ccip.chain.link disallows /tx/* and /address/* (the explorer pages); this module reads only the
documented API host api.ccip.chain.link. Both hosts are keyless — only hosts are ever logged.

VALUE = Σ over the day's token-moving messages of amount × 10^-decimals × that day's price, where amount is the
SOURCE-chain amount at send (locked or burned at the source; a message that later FAILS on the destination still
moved the token out of the source, so it counts on its send day). Decimals are the CCIP token directory's for that
(source chain, address); where it has none, DefiLlama's for the same coin. Price: DefiLlama at 12:00 UTC of the day
(searchWidth 12h — the same convention as fetch/chainlink_fees.py). A token DefiLlama has no price for on its own
chain is priced by a sibling in the SAME CCIP TOKEN GROUP (groupId: the one token deployed across chains and moved
1:1 by its pools) — up to 3 siblings, Ethereum / Arbitrum / Base / BNB / Polygon / Avalanche / Optimism / Solana
first; the source chain's decimals still scale the amount.

UNKNOWN IS NEVER 0. A transfer with no price (chain DefiLlama does not cover — unmapped chains are listed in
LLAMA_CHAINS' comment — no price that day on the chain or any group sibling, or no decimals anywhere) is NOT valued
at 0. Its day is
    stored PARTIAL   config.mark_source(SRC, "PARTIAL") + "[n of m transfers unpriced, x% of messages]" — x is the
                     share of the day's token-moving messages carrying at least one unpriced transfer; the value
                     is then a LOWER BOUND (the build reads ":PARTIAL" as "understates"), or
    REFUSED          when x > spec max_unpriced_share (default 0.10). Why 10%: unpriced transfers are almost all
                     long-tail tokens on chains DefiLlama does not price (Bitlayer, Bittensor, Canton, …), whose
                     values are small next to USDC / LINK / WETH; a tenth of the messages unpriced still leaves the
                     priced majority as a usable lower bound, while a day above that is more likely an outage of
                     the price API or a new major token than a long tail — that day is asked again on a later run
                     once prices (or the token directory) catch up, never stored at a guess.
`ccip_token_transfers` (a count, no price involved) is stored for every complete, verified day.

MULTI-TOKEN MESSAGES. The list's sourceTokenAmount holds ONE token; a message can carry several (tokenAmounts[] of the
detail). The list says nothing about it, so it is checked by LANE VERSION: OnRamp 2.0.0 reverts any message with more
than one token (chainlink-ccip main, chains/evm/contracts/onRamp/OnRamp.sol: `error CanOnlySendOneTokenPerMessage()`,
`if (message.tokenAmounts.length != 1) revert CanOnlySendOneTokenPerMessage()`, read 2026-10-20), so a message sent on
a 2.0 lane is complete in the list. 1.5 / 1.6 lanes allow several. Per (source>dest lane, day): the OLDEST
token-moving message is read in detail; version 2.x → the whole lane-day is single-token (a lane's version only moves
forward — a rollback inside a day is the one case this misses). A 1.x lane-day has its messages read in detail —
all of them for days in the last `recent_days` (spec detail_sample, default 1.0), a deterministic sample by message id
for older days (spec seed_detail_sample, default 0.1 — measured live 2026-10-20 on 10-16..10-18: 138 detail reads and
~22 s a day; reading every 1.x message is 234-776 reads a day there, more further back where fewer lanes run 2.0,
which would make the 270-day seed many hours) — and ONE sampled
message with >1 token makes the whole lane-day read. A message read in detail is counted from tokenAmounts[] (every
token); the rest from the list. What was read is kept per day in the state (check: lanes, 1.x messages, read, multi).
Measured 2026-10-20: 735 token-moving messages read in detail (2026-10-19 14:13 – 10-20 09:17, plus 40 on 09-20), 0
carried more than one token; 76% were 2.0.0, the rest 1.6.0 / 1.6.2 / 1.5.0. The live smoke then read 410 more 1.x
messages (all 234 of 10-19's, a 10% sample of 10-16..10-18): 0 multi-token.

COMPLETE DAYS ONLY, AND ONLY FULLY COVERED ONES. A day is closed when the walk has read a message from an OLDER day
(the list is newest-first, so every message of the day has been seen); today is never stored. A day inside the walk
with no message at all is a GAP (an API hole, not a quiet day: CCIP mainnet carries 900-5,000 messages a day) and is
refused. A closed day is stored once its detail checks are done and every coin it needs has an answer from DefiLlama
(a failed price CALL leaves the day pending for the next run; an answered "no price" is remembered).

INCREMENTAL, RESUMABLE (state file spec state_path, default .cache/ccip/state.json, saved after every page, every
closed day, every 25 detail reads). It holds the closed days (per-token raw sums — re-pricing needs no re-walk), the
WALKS in progress (cursor, the day being read and its messages so far) and what is known of each lane's version.
A daily run walks from the newest message down until it passes below the newest closed day (2-4 pages). The history
seed (spec history_days, at most 270: the API answered HTTP 500 beyond ~271 days on 2026-10-12, config) walks on
from where the last run stopped, inside spec budget_s (or without a limit when run unbounded, as `--seed`),
closing, checking and pricing each day as it passes it. A
cursor the API no longer accepts (4xx) restarts that walk from the newest message with its open day discarded (never
half-counted); a walk that fails three runs running is dropped and its open day becomes the floor.
"""
from __future__ import annotations

import datetime as _dt
import functools
import hashlib
import json
import logging
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd

import config
from .base import Http, HttpError, LogEntry, tidy, today
from .logcache import atomic_write_text

log = logging.getLogger("token_metrics.fetch.ccip_value")

SOURCE = "ccip_value"
TIER = 1
API = "https://api.ccip.chain.link/v2"
PRICE_API = "https://coins.llama.fi"
SRC = "ccip_api:messages.sourceTokenAmount x llama price"     # the stored value source — keep it stable
SRC_COUNT = "ccip_api:messages.sourceTokenAmount count"         # the transfer count (no price involved)
VALUE = "ccip_token_value_usd"
FEE_SRC = "ccip_api:messages.fees.fixedFeesDetails x llama price"     # G9 K4


def fee_metric(chain: str) -> str:
    """The stored per-chain CCIP 2.0 fee series read from the message detail (G9 K4)."""
    return f"ccip_v2_fees_api_{chain}_usd"
TRANSFERS = "ccip_token_transfers"
ROOT = Path(__file__).resolve().parent.parent

PAGE = 1000                    # the list's maximum (spec: default 100, max 1000)
MAX_HISTORY_DAYS = 270         # HTTP 500 beyond ~271 days (config BUSINESS_SERIES note, 2026-10-12)
COINS_PER_CALL = 60            # ~3.3 KB of URL; DefiLlama answers a batch in one call
MAX_SIBLINGS = 3
MAX_WALK_FAILS = 3             # runs in a row a walk may fail before it is dropped
SAVE_EVERY_DETAILS = 25
CHECKPOINT_S = 15.0           # the state is written at most this often between closed days (it grows to MBs)
KEEP_DAYS_BEHIND = 60         # closed days kept this far below the seed target, then pruned (the store has them)

DEFAULTS = {
    "environment": "mainnet",
    "state_path": ".cache/ccip/state.json",
    "tokens_path": ".cache/ccip/tokens.json",
    "prices_path": ".cache/ccip/prices.json",
    "history_days": 270,
    "budget_s": 300,
    "max_unpriced_share": 0.10,
    "detail_sample": 1.0,
    "seed_detail_sample": 0.1,
    "recent_days": 7,
    "tokens_max_age_days": 7,
    "price_api": PRICE_API,
}

# CCIP chain selector -> DefiLlama coins-API chain key. Each key priced at least one CCIP token of that chain on
# coins.llama.fi/prices/current on 2026-10-20 (the token directory's first 8 addresses per chain), except those marked
# "(key known, nothing priced 2026-10-20)". Chains NOT here are unmapped -> their transfers are unpriced (never 0)
# unless a group sibling prices them: ab, botanix, bittensor, canton, edge, everclear, gravity, memento, mind, mint,
# mova, nexon-henesys, superseed, ton, tron-evm (DefiLlama keys tron by base58, CCIP by hex). spec llama_chains
# overrides an entry ({selector: key}, or null to unmap).
LLAMA_CHAINS = {
    "5009297550715157269": "ethereum", "4949039107694359620": "arbitrum", "15971525489660198786": "base",
    "11344663589394136015": "bsc", "4051577828743386545": "polygon", "6433500567565415381": "avax",
    "3734403246176062136": "optimism", "124615329519749607": "solana", "4426351306075016396": "0g",
    "3577778157919314504": "abstract", "4059281736450291836": "adi", "14894068710063348487": "apechain",
    "4741433654826277614": "aptos", "6370580034781731079": "arc", "1294465214383781161": "berachain",
    "465944652040885897": "op_bnb", "7937294810946806131": "btr", "3849287863852499584": "bob",
    "5406759801798337480": "bsquared", "241851231317828981": "merlin", "1346049177634351622": "celo",
    "1224752112135636129": "core", "9043146809313071210": "corn", "18240105181246962294": "ctc",
    "1456215246176062136": "cronos", "4411394078118774322": "blast", "7613811247471741961": "hsk",
    "3461204551265785888": "ink", "4627098889531055414": "linea", "1556008542357238666": "mantle",
    "8805746078405598895": "metis", "7264351850409363825": "mode", "13204309965629103672": "scroll",
    "16468599424800719238": "taiko", "1923510103922296319": "unichain", "2049429975587534727": "wc",
    "3016212468291539606": "xlayer", "17198166215261833993": "zircuit", "1562403441176082196": "era",
    "13624601974233774587": "etlk", "1462016016387883143": "fraxtal", "465200170687744372": "xdai",
    "3229138320728879060": "hedera", "1804312132722180201": "hemi", "2442541497099098535": "hyperliquid",
    "1523760397290643893": "jovay", "9813823125703490621": "klaytn", "5608378062013572713": "lens",
    "15293031020466096408": "lisk", "6093540873831549674": "megaeth", "13447077090413146373": "metall2",
    "8481857512324358265": "monad", "18164309074156128038": "morph", "7222032299962346917": "neox",
    "7801139999541420232": "pharos", "9335212494177455608": "plasma", "17912061998839310979": "plume_mainnet",
    "6422105447186081193": "astar", "2459028469735686113": "katana", "6180753054346818345": "robinhood",
    "6916147374840168594": "ronin", "11964252391146578476": "rsk", "9027416829622342829": "sei",
    "3993510008929295315": "shibarium", "12505351618335765396": "soneium", "1673871237479749969": "sonic",
    "16978377838628290997": "stable", "5936861837188149645": "tac", "7281642695469137430": "tempo",
    "5142893604156789321": "wemix", "17673274061779414707": "xdc", "3555797439612589184": "zora",
    # (key known, nothing priced 2026-10-20)
    "4348158687435793198": "polygon_zkevm", "3768048213127883732": "fantom", "1252863800116739621": "moonbeam",
    "1355020143337428062": "moonriver", "8788096068760390840": "cronos_zkevm",
}
SIBLING_ORDER = ("5009297550715157269", "4949039107694359620", "15971525489660198786", "11344663589394136015",
                 "4051577828743386545", "6433500567565415381", "3734403246176062136", "124615329519749607")


# ===================================================================================================== pure helpers
@functools.lru_cache(maxsize=65536)
def utc_day(ts: str) -> str:
    """'YYYY-MM-DD' (UTC) of an ISO timestamp ("2026-10-20T09:01:59.000Z")."""
    t = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if t.tzinfo is not None:
        t = t.astimezone(_dt.timezone.utc)
    return t.date().isoformat()


def _shift(day: str, n: int) -> str:
    return (_dt.date.fromisoformat(day) + _dt.timedelta(days=n)).isoformat()


def norm_addr(addr: str) -> str:
    """Hex addresses (EVM, Aptos) compare case-insensitively; base58 (Solana) is case-sensitive and kept."""
    a = str(addr).strip()
    return a.lower() if a.lower().startswith("0x") else a


def tkey(selector, addr) -> str:
    return f"{selector}|{norm_addr(addr)}"


def picked(message_id: str, rate: float) -> bool:
    """A deterministic sample by message id: the same message is in or out on every run."""
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    return int(hashlib.sha1(message_id.encode()).hexdigest()[:8], 16) / 2 ** 32 < rate


def is_v2(version) -> bool:
    try:
        return int(str(version).split(".")[0]) >= 2
    except (TypeError, ValueError):
        return False


def entry(m: dict) -> list | None:
    """[messageId, sendTimestamp, lane, token key, raw amount] for a token-moving list record, else None."""
    sta = m.get("sourceTokenAmount")
    if not sta or not sta.get("tokenAddress") or sta.get("tokenAmount") in (None, ""):
        return None
    src = str((m.get("sourceNetworkInfo") or {}).get("chainSelector"))
    dst = str((m.get("destNetworkInfo") or {}).get("chainSelector"))
    return [m["messageId"], m["sendTimestamp"], f"{src}>{dst}", tkey(src, sta["tokenAddress"]), str(sta["tokenAmount"])]


def compact(rec: dict, details: dict) -> dict:
    """A closed day's messages -> per-token raw sums. A message read in detail counts every token it moved
    (tokenAmounts[]); the others count the list's one token."""
    tokens: dict = defaultdict(lambda: [0, 0])
    dests: dict = defaultdict(set)
    multi = []
    for mid, _ts, lane, key, amt in rec["tok"]:
        d = details.get(mid)
        src, dst = (lane.split(">") + [""])[:2]
        dests[key].add(dst)
        if d is not None and d[1]:
            keys = []
            for addr, a in d[1]:
                k = tkey(src, addr)
                tokens[k][0] += int(a)
                tokens[k][1] += 1
                dests[k].add(dst)
                keys.append(k)
            if len(keys) > 1:
                multi.append(keys)
        else:
            tokens[key][0] += int(amt)
            tokens[key][1] += 1
    return {"stage": "closed", "n_msgs": rec["n_msgs"], "n_token_msgs": len(rec["tok"]),
            "tokens": {k: [str(v[0]), v[1]] for k, v in sorted(tokens.items())}, "multi": multi,
            "check": rec.get("check") or {},
            # G9 K1: the DESTINATION chains each token went to — its destination-chain contract (the same CCIP token
            # group on that chain) is the second pricing step
            "dests": {k: sorted(x for x in v if x) for k, v in sorted(dests.items())}}


def day_value(rec: dict, price_of) -> dict:
    """The day's verdict from a closed record and price_of(key) -> (usd per token, decimals) | None | "pending".
        {"pending": [keys]}                         a coin not yet answered by DefiLlama
        {"value", "transfers", "unpriced_transfers", "unpriced_msgs", "share", "by_token"}
    An unpriced transfer adds nothing and is COUNTED — never valued at 0 silently."""
    value, by_token, unpriced, pending = 0.0, {}, set(), []
    n_tr = 0
    for key, (raw, n) in rec["tokens"].items():
        n_tr += n
        p = price_of(key)
        if p == "pending":
            pending.append(key)
            continue
        if p is None:
            unpriced.add(key)
            continue
        price, dec = p
        v = int(raw) / 10 ** int(dec) * float(price)
        value += v
        by_token[key] = v
    if pending:
        return {"pending": pending}
    in_multi = defaultdict(int)
    for ks in rec.get("multi") or ():
        for k in ks:
            in_multi[k] += 1
    single_unpriced = sum(rec["tokens"][k][1] - in_multi.get(k, 0) for k in unpriced)
    multi_unpriced = sum(1 for ks in rec.get("multi") or () if any(k in unpriced for k in ks))
    n_msgs = rec["n_token_msgs"]
    u_msgs = single_unpriced + multi_unpriced
    return {"value": value, "transfers": n_tr, "unpriced_transfers": sum(rec["tokens"][k][1] for k in unpriced),
            "unpriced_msgs": u_msgs, "share": (u_msgs / n_msgs) if n_msgs else 0.0, "by_token": by_token,
            "unpriced": sorted(unpriced)}


# ===================================================================================================== the fetcher
class CcipValue:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, price_http: Http | None = None, clock=None, root=None,
                 **_ignored):
        self.http = http or Http(min_interval=0.1, retries=2)
        self.price_http = price_http or Http(min_interval=0.25, retries=2)
        self.clock = clock or time.monotonic
        self.root = Path(root) if root else ROOT

    # ------------------------------------------------------------------ files
    def _path(self, p: str) -> Path:
        p = Path(p)
        return p if p.is_absolute() else self.root / p

    def _load(self, p: str) -> dict:
        try:
            return json.loads(self._path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, p: str, obj: dict) -> None:
        atomic_write_text(self._path(p), json.dumps(obj, separators=(",", ":")))

    def _checkpoint(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - getattr(self, "_saved_at", 0.0) >= CHECKPOINT_S:
            self._save(self.spec["state_path"], self.st)
            self._saved_at = now

    def _left(self) -> float:
        return self._deadline - self.clock()

    def _get(self, http, url: str, params: dict | None = None):
        # the Http deadline is on the real monotonic clock; the budget itself is kept on self.clock
        return http.get(url, params=params, deadline=time.monotonic() + max(self._left(), 1.0))

    # ------------------------------------------------------------------ run
    def run(self, projects: list[dict], window_days, out, unbounded: bool = False, days=None):
        """unbounded=True is a `--seed` run (the chainlink_fees convention): no time budget — the seed walks, checks
        and prices until it reaches history_days, saving as it goes. A routine run stops at spec budget_s.
        days (G9 K2, `--seed ccip_value --dates d1,d2`): ONLY those UTC days are read again — one walk from the
        newest message down to the oldest of them, closing nothing else; their remembered "no price" answers are
        dropped so DefiLlama is asked again, and the destination-chain and declared-underlying steps apply."""
        for p in projects:
            spec = p.get("ccip_value")
            if spec:
                self._project(p["name"], {**DEFAULTS, **spec}, out, unbounded, days=days)

    def _reread(self, days) -> None:
        """G9 K2: the listed days are walked again (their destination chains are recorded) and re-priced."""
        st = self.st
        only = sorted({str(pd.Timestamp(d).date()) for d in days if str(pd.Timestamp(d).date()) <= self.yesterday})
        if not only:
            return
        for d in only:
            st["days"].pop(d, None)
            for c, v in list(self.prices.get(d, {}).items()):
                if v is None:
                    del self.prices[d][c]
        st["walks"] = [w for w in st["walks"] if not w.get("only")]
        w = self._new_walk(only[-1], only[0])
        w["only"] = only
        st["walks"].append(w)
        self.stats["reread_days"] = len(only)
        self.target = f"{only[0]}..{only[-1]} (re-read of {len(only)} listed day(s))"

    def _project(self, name: str, spec: dict, out, unbounded: bool = False, days=None) -> None:
        self._deadline = self.clock() + (float("inf") if unbounded else float(spec["budget_s"]))
        self.stats = defaultdict(int)
        self.spec = spec
        st = self._load(spec["state_path"])
        if st.get("environment") != spec["environment"] or st.get("version") != 1:
            st = {"version": 1, "environment": spec["environment"], "walks": [], "days": {}, "floor": None,
                  "lanes": {"v2_since": {}, "v1_until": {}}, "detail": {}}
        self.st = st
        self.prices = self._load(spec["prices_path"])
        self._tok, self._priced, self._saved_at = None, set(), 0.0
        self._detail_error = self._price_error = None
        yesterday = str((today() - pd.Timedelta(days=1)).date())
        self.yesterday = yesterday
        notes = []
        if days:
            self.target = "re-read"
            self._reread(days)
            walks = [w for w in st["walks"] if w.get("only")]
            notes.append(f"re-read of {len(walks[0]['only']) if walks else 0} listed day(s) only (G9 K2)")
        else:
            self._plan(yesterday, spec)
            walks = [w for w in st["walks"] if not w.get("only")]
        try:
            for w in sorted(walks, key=lambda w: w["upper"], reverse=True):
                while self._left() > 0:
                    res = self._walk(w)
                    self._settle()
                    if res != "day":
                        if res not in ("budget", "done"):
                            notes.append(f"walk {w['lower']}..{w['upper']}: {res}")
                        break
                if self._left() <= 0:
                    notes.append("time budget reached — the next run resumes")
                    break
            self._settle()
        finally:
            self._checkpoint(force=True)
            if self.prices:
                self._save(spec["prices_path"], self.prices)
        self._emit(name, out, notes)

    # ------------------------------------------------------------------ walks
    def _plan(self, yesterday: str, spec: dict) -> None:
        """Which walks this run needs: one from the newest message down to the newest closed day, and the seed
        down to history_days (≤ 270)."""
        st = self.st
        n = min(int(spec["history_days"]), MAX_HISTORY_DAYS)
        target = _shift(yesterday, -(n - 1))
        if st.get("floor"):
            target = max(target, _shift(st["floor"], 1))
        self.target = target
        for d in [d for d in st["days"] if d < _shift(target, -KEEP_DAYS_BEHIND)]:
            del st["days"][d]
        for w in st["walks"]:
            w["lower"] = max(w["lower"], target)
        st["walks"] = [w for w in st["walks"] if w["lower"] <= w["upper"]]
        own = [w for w in st["walks"] if not w.get("only")]        # a re-read (G9 K2) plans nothing
        his = list(st["days"]) + [w["upper"] for w in own]
        los = list(st["days"]) + [w["lower"] for w in own]
        if not his:
            if target <= yesterday:
                st["walks"].append(self._new_walk(yesterday, target))
            return
        hi, lo = max(his), min(los)
        if yesterday > hi:
            st["walks"].append(self._new_walk(yesterday, _shift(hi, 1)))
        if lo > target and not any(w["lower"] <= _shift(lo, -1) <= w["upper"] for w in own):
            st["walks"].append(self._new_walk(_shift(lo, -1), target))

    def _new_acc(self) -> dict:
        acc = {"stage": "walked", "n_msgs": 0, "tok": []}
        if self.spec.get("fee_chains"):
            acc["fee"] = []                             # G9 K4: a fee chain with no message that day is a measured 0
        return acc

    @staticmethod
    def _new_walk(upper: str, lower: str) -> dict:
        return {"upper": upper, "lower": lower, "cursor": None, "prev": None, "open": None, "acc": None,
                "fails": 0, "pages": 0}

    def _close(self, w: dict, day: str, rec: dict | None) -> None:
        if w.get("only") and day not in w["only"]:
            return                                      # G9 K2: a re-read closes only the listed days
        if w["lower"] <= day <= w["upper"]:
            self.st["days"][day] = rec if rec is not None else {"stage": "gap"}
            self.stats["closed"] += 1

    def _gaps(self, w: dict, hi: str, lo: str) -> None:
        """Days hi..lo (inclusive, hi >= lo) the walk passed with no message: gaps, refused."""
        d = hi
        while d >= lo:
            self._close(w, d, None)
            d = _shift(d, -1)

    def _walk(self, w: dict) -> str:
        """Read pages until a day closes ("day"), the walk passes its lower bound ("done"), the API's history ends
        ("end"), the budget runs out ("budget") or the API fails ("error: …")."""
        st, env = self.st, self.spec["environment"]
        closed0 = self.stats["closed"]
        while self._left() > 0:
            params = {"limit": PAGE, "environment": env}
            if w["cursor"]:
                params["cursor"] = w["cursor"]
            try:
                j = self._get(self.http, f"{API}/messages", params)
            except HttpError as e:
                if w["cursor"] and 400 <= e.status < 500:
                    # THE CURSOR IS NO LONGER ACCEPTED: restart from the newest message. Days above the open one
                    # are closed already, so the walk's top moves down to the open day, which restarts from zero.
                    top = w["open"] or w["upper"]
                    w.update(self._new_walk(min(top, w["upper"]), w["lower"]))
                    self._checkpoint(force=True)
                    return f"cursor refused (HTTP {e.status}) — restarted from the newest message"
                return self._walk_failed(w, e)
            except Exception as e:  # noqa: BLE001 — keep what is closed; the next run resumes
                return self._walk_failed(w, e)
            self.stats["pages"] += 1
            w["pages"] += 1
            w["fails"] = 0
            for m in (j or {}).get("data") or []:
                d = utc_day(m["sendTimestamp"])
                if d > w["upper"]:
                    w["prev"] = d
                    continue
                if d < w["lower"]:
                    if w["open"] is not None:
                        self._close(w, w["open"], w["acc"])
                        if _shift(w["open"], -1) >= w["lower"]:
                            self._gaps(w, _shift(w["open"], -1), w["lower"])
                    elif w["prev"] is not None:
                        self._gaps(w, min(_shift(w["prev"], -1), w["upper"]), w["lower"])
                    st["walks"].remove(w)
                    self._checkpoint(force=True)
                    return "done"
                if w["open"] is None:
                    if w["prev"] is not None and _shift(d, 1) <= min(_shift(w["prev"], -1), w["upper"]):
                        self._gaps(w, min(_shift(w["prev"], -1), w["upper"]), _shift(d, 1))
                    w["open"], w["acc"] = d, self._new_acc()
                elif d < w["open"]:
                    self._close(w, w["open"], w["acc"])
                    if _shift(d, 1) <= _shift(w["open"], -1):
                        self._gaps(w, _shift(w["open"], -1), _shift(d, 1))
                    w["open"], w["acc"] = d, self._new_acc()
                elif d > w["open"]:
                    # out of order across a closed day: cannot be added to a closed day — counted and said
                    self.stats["out_of_order"] += 1
                    continue
                w["acc"]["n_msgs"] += 1
                fsrc = str((m.get("sourceNetworkInfo") or {}).get("chainSelector"))
                if fsrc in (self.spec.get("fee_chains") or {}):
                    # G9 K4: every message SENT FROM a fee chain (token-moving or not) — its fee is in the detail
                    w["acc"].setdefault("fee", []).append([m["messageId"], fsrc])
                e = entry(m)
                if e:
                    w["acc"]["tok"].append(e)
                w["prev"] = d
            pg = (j or {}).get("pagination") or {}
            if not pg.get("hasNextPage") or not pg.get("cursor"):
                # THE API'S HISTORY ENDS HERE: the open day may continue below what is served — not complete.
                st["floor"] = w["open"] or w["upper"]
                st["walks"].remove(w)
                self._checkpoint(force=True)
                return f"the API's history ends at {st['floor']} (that day not complete; floor set)"
            w["cursor"] = pg["cursor"]
            if self.stats["closed"] > closed0:
                self._checkpoint(force=True)
                return "day"
            self._checkpoint()
        return "budget"

    def _walk_failed(self, w: dict, e: Exception) -> str:
        w["fails"] += 1
        why = f"{type(e).__name__}: {str(e)[:120]}"
        if w["fails"] >= MAX_WALK_FAILS:
            self.st["floor"] = w["open"] or w["upper"]
            self.st["walks"].remove(w)
            why += f" — failed {w['fails']} runs running: walk dropped, floor {self.st['floor']}"
        self._checkpoint(force=True)
        return f"error at page {w['pages'] + 1}: {why}"

    # ------------------------------------------------------------------ settle: check + price closed days
    def _settle(self) -> None:
        for day in sorted((d for d, r in self.st["days"].items() if r.get("stage") == "walked"), reverse=True):
            if self._left() <= 0 or not self._check_day(day):
                break
        for day in sorted((d for d, r in self.st["days"].items() if r.get("stage") == "closed"), reverse=True):
            if day in self._priced:
                continue
            if self._left() <= 0:
                break
            self._price_day(day, self.st["days"][day])

    def _detail(self, mid: str):
        """[version, [[sourceTokenAddress, amount], …]] from the detail route (cached in the state until its day
        closes)."""
        cache = self.st["detail"]
        if mid in cache:
            return cache[mid]
        j = self._get(self.http, f"{API}/messages/{mid}")
        self.stats["details"] += 1
        ta = [[t["sourceTokenAddress"], str(t["amount"])] for t in (j or {}).get("tokenAmounts") or []]
        ff = (((j or {}).get("fees") or {}).get("fixedFeesDetails") or {})
        # G9 K4: fees.fixedFeesDetails {tokenAddress, totalAmount} — the fee in the fee token's smallest units (on a 2.0
        # lane the VERIFIER / EXECUTOR / TOKEN_POOL / NETWORK items sum to it exactly; a 1.x lane gives the total only)
        fee = [ff["tokenAddress"], str(ff["totalAmount"])] if ff.get("tokenAddress") and ff.get("totalAmount") else None
        cache[mid] = [str(j.get("version") or ""), ta, fee]
        if self.stats["details"] % SAVE_EVERY_DETAILS == 0:
            self._checkpoint()
        return cache[mid]

    def _learn(self, lane: str, ts: str, version: str) -> None:
        lanes = self.st["lanes"]
        if is_v2(version):
            cur = lanes["v2_since"].get(lane)
            lanes["v2_since"][lane] = ts if cur is None or ts < cur else cur
        else:
            cur = lanes["v1_until"].get(lane)
            lanes["v1_until"][lane] = ts if cur is None or ts > cur else cur

    def _check_day(self, day: str) -> bool:
        """The multi-token check of one closed day (see the module doc), then its compaction. False when the
        budget or the API stopped it (the day stays 'walked'; the reads so far are kept)."""
        rec = self.st["days"][day]
        recent = day >= _shift(self.yesterday, -(int(self.spec["recent_days"]) - 1))
        rate = float(self.spec["detail_sample"] if recent else self.spec["seed_detail_sample"])
        by_lane = defaultdict(list)
        for e in rec["tok"]:
            by_lane[e[2]].append(e)                     # newest first, as walked
        check = {"rate": rate, "lanes": len(by_lane), "lanes_v2": 0, "lanes_v1": 0, "v1_msgs": 0, "v1_read": 0,
                 "multi_lane_days": 0}
        detailed = {}
        try:
            for lane, msgs in sorted(by_lane.items()):
                if rate <= 0:
                    continue                            # detail_check off: the list figure only
                if self._left() <= 0:
                    return False
                lanes = self.st["lanes"]
                v2s, v1u = lanes["v2_since"].get(lane), lanes["v1_until"].get(lane)
                oldest, newest = msgs[-1], msgs[0]
                if v2s is not None and v1u is not None and v1u > v2s:
                    v2s = v1u = None                    # a 1.x message after a 2.0 one: a rollback — probe again
                if v2s is not None and v2s <= oldest[1]:
                    v2 = True
                elif v1u is not None and v1u >= newest[1]:
                    v2 = False
                else:
                    d = self._detail(oldest[0])
                    self._learn(lane, oldest[1], d[0])
                    detailed[oldest[0]] = d
                    v2 = is_v2(d[0])
                if v2:
                    check["lanes_v2"] += 1
                    continue
                check["lanes_v1"] += 1
                check["v1_msgs"] += len(msgs)
                order = [e for e in msgs if picked(e[0], rate)]
                read_all = False
                i = 0
                while i < len(order):
                    if self._left() <= 0:
                        return False
                    e = order[i]
                    d = self._detail(e[0])
                    self._learn(lane, e[1], d[0])
                    detailed[e[0]] = d
                    if len(d[1]) > 1 and not read_all:
                        read_all = True                 # a multi-token message: read the whole lane-day
                        check["multi_lane_days"] += 1
                        order += [x for x in msgs if x not in order]
                    i += 1
                check["v1_read"] += sum(1 for e in msgs if e[0] in detailed)
        except Exception as e:  # noqa: BLE001 — the day stays open for the next run
            self.stats["detail_errors"] += 1
            self._detail_error = f"{type(e).__name__}: {str(e)[:120]}"
            return False
        fees: dict = defaultdict(lambda: [0, 0])
        fee_v1 = defaultdict(int)
        try:
            for mid, fsrc in rec.get("fee") or ():
                if self._left() <= 0:
                    return False
                d = detailed.get(mid) or self._detail(mid)
                if not is_v2(d[0]):
                    fee_v1[fsrc] += 1             # a 1.x fee is already in the aggregator core (chainlink_fees)
                    continue
                if len(d) < 3 or d[2] is None:
                    fee_v1[f"{fsrc}:nofee"] += 1
                    continue
                k = tkey(fsrc, d[2][0])
                fees[k][0] += int(d[2][1])
                fees[k][1] += 1
        except Exception as e:  # noqa: BLE001 — the day stays open for the next run
            self.stats["detail_errors"] += 1
            self._detail_error = f"{type(e).__name__}: {str(e)[:120]}"
            return False
        rec["check"] = check
        new = compact(rec, detailed)
        if rec.get("fee") is not None:
            new["fees"] = {k: [str(v[0]), v[1]] for k, v in sorted(fees.items())}
            new["fee_v1"] = dict(fee_v1)
            new["fee_msgs"] = len(rec["fee"])
        for mid, _src in rec.get("fee") or ():
            self.st["detail"].pop(mid, None)
        self.st["days"][day] = new
        for e in rec["tok"]:
            self.st["detail"].pop(e[0], None)
        self._checkpoint(force=True)
        return True

    # ------------------------------------------------------------------ tokens + prices
    def _tokens(self, need=()) -> dict:
        """{key: [decimals, symbol, groupId]} from the CCIP token directory, cached in tokens_path and read again
        when older than tokens_max_age_days — or older than a day when a needed token is missing from it."""
        if self._tok is not None and not any(k not in self._tok["tokens"] for k in need):
            return self._tok["tokens"]
        cache = self._tok or self._load(self.spec["tokens_path"])
        age = None
        if cache.get("fetched"):
            age = (pd.Timestamp.now("UTC").tz_localize(None) - pd.Timestamp(cache["fetched"])).total_seconds() / 86400
        missing = any(k not in (cache.get("tokens") or {}) for k in need)
        stale = age is None or age > float(self.spec["tokens_max_age_days"]) or (missing and age > 1.0)
        if stale and not self.stats.get("tokens_refreshed"):
            self.stats["tokens_refreshed"] = 1
            try:
                toks, cur = {}, None
                for _ in range(50):
                    params = {"environment": self.spec["environment"], "limit": 1000, "reviewedOnly": "false"}
                    if cur:
                        params["cursor"] = cur
                    j = self._get(self.http, f"{API}/tokens", params)
                    self.stats["token_pages"] += 1
                    for t in (j or {}).get("data") or []:
                        toks[tkey(t["chainSelector"], t["address"])] = [t.get("decimals"), t.get("symbol"),
                                                                        t.get("groupId")]
                    pg = (j or {}).get("pagination") or {}
                    cur = pg.get("cursor")
                    if not pg.get("hasNextPage") or not cur:
                        break
                if toks:
                    cache = {"fetched": pd.Timestamp.now("UTC").tz_localize(None).isoformat(), "tokens": toks}
                    self._save(self.spec["tokens_path"], cache)
            except Exception as e:  # noqa: BLE001 — the old directory stands
                log.info("ccip_value: token directory from api.ccip.chain.link unavailable (%s)", type(e).__name__)
                self.stats["token_errors"] += 1
        cache.setdefault("tokens", {})
        self._tok = cache
        groups = defaultdict(list)
        for k, v in cache["tokens"].items():
            if v[2]:
                groups[v[2]].append(k)
        self._groups = groups
        return cache["tokens"]

    def _chain(self, selector: str):
        over = self.spec.get("llama_chains") or {}
        return over[selector] if selector in over else LLAMA_CHAINS.get(selector)

    def _coin(self, key: str):
        sel, addr = key.split("|", 1)
        c = self._chain(sel)
        return f"{c}:{addr}" if c else None

    def _siblings(self, key: str) -> list[str]:
        tok = self._tok["tokens"].get(key)
        if not tok or not tok[2]:
            return []
        sibs = [k for k in self._groups.get(tok[2], []) if k != key and self._coin(k)]
        rank = {s: i for i, s in enumerate(SIBLING_ORDER)}
        sibs.sort(key=lambda k: (rank.get(k.split("|")[0], 99), k))
        return sibs[:MAX_SIBLINGS]

    def _ask(self, day: str, coins: list[str]) -> bool:
        """Fetch the day's price for every coin not yet answered; True when every call answered."""
        have = self.prices.setdefault(day, {})
        ask = sorted({c for c in coins if c not in have})
        ts = int((pd.Timestamp(day) + pd.Timedelta(hours=12)).timestamp())
        ok = True
        for i in range(0, len(ask), COINS_PER_CALL):
            if self._left() <= 0:
                return False
            chunk = ask[i:i + COINS_PER_CALL]
            try:
                j = self._get(self.price_http, f"{self.spec['price_api'].rstrip('/')}/prices/historical/{ts}/"
                                               f"{','.join(chunk)}", {"searchWidth": "12h"})
            except Exception as e:  # noqa: BLE001 — not remembered: asked again next run
                self.stats["price_errors"] += 1
                self._price_error = f"{type(e).__name__}: {str(e)[:120]}"
                ok = False
                continue
            self.stats["price_calls"] += 1
            got = (j or {}).get("coins") or {}
            low = {k.lower(): v for k, v in got.items()}
            for c in chunk:
                v = got.get(c) or low.get(c.lower())
                # ANSWERED WITH NO PRICE is remembered (null), as in chainlink_fees: a coin DefiLlama cannot
                # price is not asked again every run
                have[c] = ([float(v["price"]), v.get("decimals")]
                           if isinstance(v, dict) and v.get("price") is not None else None)
        return ok

    def _dest_members(self, key: str, rec: dict | None) -> list[str]:
        """G9 K1 step 2: the token's DESTINATION-chain contracts — members of its CCIP token group on the chains the
        day's messages went to (rec["dests"]), beyond the MAX_SIBLINGS already tried."""
        tok = (self._tok or {}).get("tokens", {}).get(key)
        dests = set(((rec or {}).get("dests") or {}).get(key) or ())
        if not tok or not tok[2] or not dests:
            return []
        tried = set(self._siblings(key))
        return [k for k in sorted(self._groups.get(tok[2], [])) if k != key and k not in tried
                and k.split("|")[0] in dests and self._coin(k)]

    def _underlying(self, key: str) -> dict | None:
        """G9 K1 step 3: a DECLARED underlying for a documented receipt / vault / wrapped token (spec
        declared_underlying: {key: {"coin": "<llama coin>", "rate": 1.0, "source": …}}) — never assumed."""
        return (self.spec.get("declared_underlying") or {}).get(key)

    def _price_day(self, day: str, rec: dict) -> None:
        toks = self._tokens(need=list(rec["tokens"]))
        primary = [c for c in (self._coin(k) for k in list(rec["tokens"]) + list(rec.get("fees") or {})) if c]
        if not self._ask(day, primary):
            return
        have = self.prices.get(day, {})
        sib = []
        for k in rec["tokens"]:
            c = self._coin(k)
            if (c is None or have.get(c) is None) and (toks.get(k) or [None])[0] is not None:
                sib += [self._coin(s) for s in self._siblings(k)]
        if sib and not self._ask(day, sib):
            return
        more = []
        for k in rec["tokens"]:
            c = self._coin(k)
            if (c is None or have.get(c) is None) and (toks.get(k) or [None])[0] is not None and \
                    not any(have.get(self._coin(s)) is not None for s in self._siblings(k)):
                more += [self._coin(s) for s in self._dest_members(k, rec)]
                u = self._underlying(k)
                if u:
                    more.append(u["coin"])
        if more and not self._ask(day, more):
            return
        self._priced.add(day)
        n = len(self._priced)
        if n % 10 == 0:
            self._save(self.spec["prices_path"], self.prices)

    def price_of(self, day: str, rec: dict | None = None):
        """price_of(key) -> (usd per whole token, decimals) | None (unpriced) | "pending" (not yet answered).
        Order (G9 K1): the source-chain contract, its group siblings, its destination-chain contracts (rec's
        dests), a declared underlying — never an invented price."""
        have = self.prices.get(day, {})
        toks = self._tok["tokens"] if self._tok else {}

        zero = self.spec.get("zero_value_groups") or {}

        def f(key):
            c = self._coin(key)
            dec = (toks.get(key) or [None])[0]
            if zero and (toks.get(key) or [None, None, None])[2] in zero:
                # G9 K1: a DECLARED zero-value token (its verified contract mints it free to anyone — CCIP's TESTTR):
                # valued 0 with the contract as the evidence, counted on the row, never "unpriced"
                return 0.0, int(dec if dec is not None else 18)
            if c is not None:
                if c not in have:
                    return "pending"
                v = have[c]
                if v is not None:
                    d = dec if dec is not None else v[1]
                    return (v[0], int(d)) if d is not None else None
            if dec is None:
                return None
            sibs = [self._coin(s) for s in self._siblings(key)] if self._tok else []
            for s in sibs:
                if s not in have:
                    return "pending"
                if have[s] is not None:
                    self.stats["sibling_priced"] += 1
                    return have[s][0], int(dec)
            for s in (self._coin(k) for k in self._dest_members(key, rec)) if self._tok else ():
                if s in have and have[s] is not None:
                    self.stats["dest_priced"] += 1
                    return have[s][0], int(dec)
            u = self._underlying(key)
            if u and have.get(u["coin"]) is not None:
                self.stats["underlying_priced"] += 1
                return have[u["coin"]][0] * float(u.get("rate", 1.0)), int(dec)
            return None
        return f

    def _emit_fees(self, name: str, out) -> None:
        """G9 K4: per fee chain, the CCIP 2.0 fees of the messages SENT FROM it per day ($) — fees.fixedFeesDetails of
        the message detail x the fee token's DefiLlama price at 12:00 UTC (fee_metric(chain)). 1.x-lane fees are
        counted and left out (they reach the aggregator core). A day with a fee token unpriced is refused."""
        for sel, chain in (self.spec.get("fee_chains") or {}).items():
            metric, rows, refused = fee_metric(chain), [], []
            for day in sorted(self.st["days"]):
                rec = self.st["days"][day]
                if rec.get("stage") != "closed" or rec.get("fees") is None:
                    continue
                pf = self.price_of(day, rec)
                val, bad, n = 0.0, [], 0
                for k, (raw, cnt) in rec["fees"].items():
                    if not k.startswith(f"{sel}|"):
                        continue
                    pr = pf(k)
                    if pr in (None, "pending"):
                        bad.append(k.split("|", 1)[1])
                        continue
                    val += int(raw) / 10 ** int(pr[1]) * float(pr[0])
                    n += int(cnt)
                if bad:
                    refused.append(f"{day} (fee token {', '.join(b[:10] + '…' for b in bad)} unpriced)")
                    continue
                v1 = int((rec.get("fee_v1") or {}).get(sel, 0))
                rows.append((day, val, f"{FEE_SRC}[{chain}: {n} 2.0 message(s); {v1} 1.x left out]"))
            if rows:
                frame = pd.concat([tidy([(d, v)], name, metric, s_, TIER) for d, v, s_ in rows], ignore_index=True)
                out.add(frame, SOURCE, name, f"{metric}: {len(rows)} day(s) {rows[0][0]}..{rows[-1][0]} — CCIP 2.0 fees "
                                             f"of messages sent from {chain} (message detail fees.fixedFeesDetails)",
                        TIER)
            if refused:
                out.warn(SOURCE, name, f"{metric}: {len(refused)} day(s) refused — " + "; ".join(refused[-4:]), TIER)

    def _zero_valued(self, rec: dict) -> int:
        """Transfers of a declared zero-value group on a closed day (G9 K1)."""
        zero = self.spec.get("zero_value_groups") or {}
        toks = (self._tok or {}).get("tokens", {})
        return sum(int(n) for k, (_raw, n) in rec["tokens"].items() if zero and (toks.get(k) or [None] * 3)[2] in zero)

    # ------------------------------------------------------------------ emit
    @staticmethod
    def _check_line(rec: dict) -> str:
        c = rec.get("check") or {}
        if not c:
            return "not recorded"
        if not c.get("rate"):
            return "off (list figure only)"
        return (f"{c.get('lanes_v2', 0)}/{c.get('lanes', 0)} lanes 2.x, {c.get('v1_read', 0)}/{c.get('v1_msgs', 0)} "
                f"1.x messages read, {len(rec.get('multi') or ())} multi-token")

    def _emit(self, name: str, out, notes: list) -> None:
        st, spec = self.st, self.spec
        if self._tok is None and any(r.get("stage") == "closed" for r in st["days"].values()):
            self._tokens()
        vrows, nrows, refused, pending, gaps, walked = defaultdict(list), [], [], [], [], []
        newest = None
        for day in sorted(st["days"]):
            rec = st["days"][day]
            stage = rec.get("stage")
            if stage == "gap":
                gaps.append(day)
                continue
            if stage == "walked":
                walked.append(day)
                continue
            if rec["n_token_msgs"] == 0:
                refused.append(f"{day} (no token-moving message: an API hole, not a zero)")
                continue
            nrows.append((day, float(rec["n_token_msgs"])))
            v = day_value(rec, self.price_of(day, rec))
            if "pending" in v:
                pending.append(day)
                continue
            if v["share"] > float(spec["max_unpriced_share"]):
                refused.append(f"{day} ({v['share']:.1%} of messages unpriced > {spec['max_unpriced_share']:.0%})")
                continue
            src = SRC
            if v["unpriced_msgs"]:
                src = (config.mark_source(SRC, "PARTIAL")
                       + f"[{v['unpriced_transfers']:,} of {v['transfers']:,} transfers unpriced, "
                         f"{v['share']:.1%} of messages]")
            z = self._zero_valued(rec)
            if z:
                src += f"[{z:,} transfer(s) of a declared zero-value test token valued 0]"
            vrows[src].append((day, v["value"]))
            newest = (day, v, rec)
        frames = [tidy(r, name, VALUE, s, TIER) for s, r in vrows.items()]
        n_stored = sum(len(r) for r in vrows.values())
        n_partial = sum(len(r) for s, r in vrows.items() if s != SRC)
        hosts = (f"{self.stats['pages']} list page(s) + {self.stats['details']} detail read(s) + "
                 f"{self.stats['token_pages']} token page(s) of api.ccip.chain.link, {self.stats['price_calls']} "
                 f"coins.llama.fi call(s)")
        cover = (f"closed days {min(st['days'])}..{max(st['days'])}" if st["days"] else "no closed day yet")
        seed = (f"; seed target {self.target}" + (f" (floor {st['floor']})" if st.get("floor") else "")
                + (f", {len(st['walks'])} walk(s) open" if st["walks"] else ", no walk open"))
        if self._detail_error:
            notes.append(f"detail reads stopped: {self._detail_error}")
        if self._price_error:
            notes.append(f"a coins.llama.fi call failed ({self._price_error}) — those days are priced next run")
        tail = "; ".join(notes)
        if self.stats["out_of_order"]:
            tail += f"; {self.stats['out_of_order']} message(s) out of send order were skipped"
        if frames and n_stored:
            frame = pd.concat(frames, ignore_index=True)
            d, v, rec = newest
            top = sorted(v["by_token"].items(), key=lambda kv: -kv[1])[:3]
            sym = lambda k: ((self._tok or {}).get("tokens", {}).get(k) or [None, k])[1] or k  # noqa: E731
            out.add(frame, SOURCE, name,
                    f"{VALUE}: {n_stored} day(s) {frame['date'].min().date()}..{frame['date'].max().date()} "
                    f"({n_partial} PARTIAL); {d} ${v['value']:,.0f} over {v['transfers']:,} transfer(s), "
                    f"{v['share']:.1%} of messages unpriced, top {', '.join(f'{sym(k)} ${x:,.0f}' for k, x in top)}"
                    f"; multi-token check {self._check_line(rec)}; {cover}{seed}; {hosts}" + (f"; {tail}" if tail else ""), TIER)
        elif not refused:
            why = ("pricing pending for " + ", ".join(pending[-3:])) if pending else (
                "checks pending for " + ", ".join(walked[-3:]) if walked else "no complete day closed yet")
            if st["days"] and max(st["days"]) >= self.yesterday and not pending and not walked:
                out.mark_current(SOURCE, name, VALUE, f"{VALUE}: {self.yesterday} stored already", TIER)
            else:
                out.fail(SOURCE, name, f"{VALUE}: NOTHING STORED — {why}; {cover}{seed}; {hosts}"
                         + (f"; {tail}" if tail else ""), TIER)
        if refused or gaps:
            msg = (f"{VALUE}: {len(refused) + len(gaps)} day(s) REFUSED, never stored as 0 — "
                   + "; ".join((refused + [f'{g} (gap: no message read)' for g in gaps])[-6:]))
            (out.warn if n_stored else out.fail)(SOURCE, name, msg, TIER)
        if pending or walked:
            out.log.append(LogEntry(
                SOURCE, name, 0, "ok", f"{VALUE}: {len(walked)} day(s) awaiting the multi-token check, "
                                       f"{len(pending)} awaiting prices — the next run continues", TIER))
        self._emit_fees(name, out)
        if nrows:
            out.add(tidy(nrows, name, TRANSFERS, SRC_COUNT, TIER), SOURCE, name,
                    f"{TRANSFERS}: {len(nrows)} day(s) {nrows[0][0]}..{nrows[-1][0]} — token-moving messages "
                    f"(mainnet, by send day)", TIER)
