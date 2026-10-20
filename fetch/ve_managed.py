"""
fetch/ve_managed.py — tier 2: AERO that named wallets have DEPOSITED into managed veNFTs (Jake's probes16, 2026-10-07).

Aerodrome's filing wallets own ~50 veNFTs whose locked() reads 0: their AERO was moved into MANAGED veNFTs with
VotingEscrow.depositManaged, which zeroes locked[tokenId] and records weights[tokenId][mTokenId] — the amount each
normal NFT put in (aerodrome-finance/contracts@1ba30815 VotingEscrow.sol). Per owner:

    n = balanceOf(owner);  tokenId = ownerToNFTokenIdList(owner, i);  mTokenId = idToManaged(tokenId)
    deposited += weights(tokenId, mTokenId)            (for every tokenId with mTokenId != 0)

summed over the owners and stored as `metric` (AERO, the escrow's underlying decimals). Jake's probe read Team
175,788,092 / Public Goods 174,772,260 / Flight School 66,991,134 / Velodrome Foundation airdrop 26,725,510. These are
team / foundation holdings — out of circulating on our convention even though locked. Once a day (DailyChecks).
"""
from __future__ import annotations

import logging
import time

import pandas as pd

from .base import tidy, today

log = logging.getLogger("token_metrics.fetch.ve_managed")

SOURCE = "ve_managed"
TIER = 2
WINDOW_BUDGET_S = 80          # of ve_managed's 120 s: the holdings and lock-stock reads come first and take seconds
_ABI = [{"name": "balanceOf", "type": "function", "stateMutability": "view", "inputs": [{"type": "address"}],
         "outputs": [{"type": "uint256"}]},
        {"name": "ownerToNFTokenIdList", "type": "function", "stateMutability": "view",
         "inputs": [{"type": "address"}, {"type": "uint256"}], "outputs": [{"type": "uint256"}]},
        {"name": "idToManaged", "type": "function", "stateMutability": "view", "inputs": [{"type": "uint256"}],
         "outputs": [{"type": "uint256"}]},
        {"name": "weights", "type": "function", "stateMutability": "view",
         "inputs": [{"type": "uint256"}, {"type": "uint256"}], "outputs": [{"type": "uint256"}]}]


def deposits(ve, owner: str, block) -> tuple[float, int, int]:
    """(raw AERO deposited into managed NFTs, NFTs owned, of which in a managed NFT) for one owner at `block`."""
    n = int(ve.functions.balanceOf(owner).call(block_identifier=block))
    tot = held = 0
    for i in range(n):
        tid = int(ve.functions.ownerToNFTokenIdList(owner, i).call(block_identifier=block))
        mid = int(ve.functions.idToManaged(tid).call(block_identifier=block))
        if mid:
            tot += int(ve.functions.weights(tid, mid).call(block_identifier=block))
            held += 1
    return tot, n, held


class VeManaged:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, reader=None, daily=None, **_ignored):
        if reader is None:
            from .chain import ChainReader
            reader = ChainReader()
        self.reader = reader
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily

    def run(self, projects: list[dict], window_days, out):
        # CHEAP READS FIRST, THE BUYBACK WINDOW LAST (Round G 1.4): the window's archive bisection is the one part that
        # can run long, and it used to run before the holdings read — a timeout lost both
        for p in projects:
            self._holdings(p, out)
        for p in projects:
            if p.get("foundation_buyback"):
                self._foundation(p, p["foundation_buyback"], out)

    def _holdings(self, p, out):
        """THE FILING WALLETS' MANAGED DEPOSITS, BATCHED (Round G8 item 3b, Jake 2026-10-20: ve_managed timed out on both
        runs — "nothing registered", then waiting on POST base-mainnet.g.alchemy.com with no phase). The read walked every
        veNFT one eth_call at a time (balanceOf, then ownerToNFTokenIdList, idToManaged and weights per NFT: ~150 calls in
        sequence on the keyed host). It now makes the four Multicall3 rounds the seed already used
        (check_offline_items.aerodrome_managed_deposits), each named as a phase."""
        from .base import set_phase
        spec = p.get("ve_managed_holdings")
        if not spec:
            return
        name, metric = p["name"], spec["metric"]
        day = str(today().date())
        key = f"ve_managed:{name}:{metric}"
        if not self.daily.due(key, day):
            out.mark_current(SOURCE, name, metric, f"{metric}: read today already", TIER)
            return
        owners = list(spec["owners"])
        set_phase(f"ve_managed {name}: managed-veNFT deposits of {len(owners)} filing wallet(s), Multicall3 (4 rounds)")
        try:
            got = (getattr(self, "managed_reader", None) or _managed_reader)(owners, spec["escrow"])
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            from .chain import redact_urls
            out.fail(SOURCE, name, f"{metric}: {redact_urls(e)}", TIER)      # hosts only, never a key
            return
        if got.get("error"):
            out.fail(SOURCE, name, f"{metric}: {got['error']}", TIER)
            return
        parts = [f"{spec['owners'][o]} {v:,.0f}" for o, v in got["by_owner"].items()]
        out.add(tidy([(today(), got["total"])], name, metric, f"{SOURCE}:{spec['escrow'][:10]}.weights", TIER),
                SOURCE, name, f"{metric} = {got['total']:,.0f} ({got['in_managed']} of {got['nfts']} veNFT(s) in a "
                              f"managed NFT): " + "; ".join(parts), TIER)
        self.daily.done(key, day)


def _managed_reader(owners, escrow):
    import check_offline_items as coi                      # noqa: PLC0415
    return coi.aerodrome_managed_deposits(owners, escrow=escrow)


def foundation_rows(name: str, spec: dict, day, got: dict, archive: bool = False) -> tuple[list, str]:
    """[(frame)] for one day's Foundation lock read (check_offline_items.aerodrome_foundation_locks), and the log line.
    ONE SOURCE STRING, SEEDED OR DAILY (Jake's run on 20d0eb4: unify them) — an archive read says so in the log line."""
    src = f"{SOURCE}:foundation_locks"
    frames = [tidy([(day, got["locked"])], name, spec["stock_metric"], src, TIER),
              tidy([(day, got["claimable"])], name, spec["claimable_metric"], src, TIER)]
    msg = (f"{spec['stock_metric']} = {got['locked']:,.0f} AERO locked by the Foundation's buyback wallets ("
           + "; ".join(f"{spec['owners'][o]} {v:,.0f}" for o, v in got["by_owner"].items())
           + f"; {got['nfts']} veNFT(s), {got['owned_managed']} owned managed veNFT(s) not counted); rebase claimable "
             f"{got['claimable']:,.0f}" + (" (archive read)" if archive else ""))
    return frames, msg


def _foundation_reader(owners, block="latest"):
    import check_offline_items as coi                      # noqa: PLC0415 — one read, shared with the probe and seed
    return coi.aerodrome_foundation_locks(list(owners), block=block)


def _foundation(self, p, spec, out):
    """THE FOUNDATION'S BUY-AND-LOCK, FROM STATE (external audit 2026-10-09, item 1): once a day, the AERO locked by
    the buyback wallets and their rebase claimable; the buyback is their daily change (build_workbook.
    _foundation_buyback_views)."""
    name, metric = p["name"], spec["stock_metric"]
    day = str(today().date())
    key = f"ve_managed:{name}:{metric}"
    if not self.daily.due(key, day):
        out.mark_current(SOURCE, name, metric, f"{metric}: read today already", TIER)
        return
    from .base import set_phase
    set_phase(f"ve_managed {name}: Foundation lock stock of {len(spec['owners'])} wallet(s), Multicall3")
    try:
        got = (getattr(self, "foundation_reader", None) or _foundation_reader)(spec["owners"])
    except Exception as e:  # noqa: BLE001
        from .chain import redact_urls
        out.fail(SOURCE, name, f"{metric}: {redact_urls(e)}", TIER)
        return
    if got.get("error"):
        out.fail(SOURCE, name, f"{metric}: {got['error']}", TIER)
        return
    frames, msg = foundation_rows(name, spec, today(), got)
    for f in frames:
        out.add(f, SOURCE, name, msg, TIER)
    self.daily.done(key, day)
    if spec.get("basis") == "bought_plus_compounded":
        _buyback_window(self, p, spec, out)


def window_rows(name: str, spec: dict, day, got: dict, archive: bool = False) -> tuple[list, str]:
    """[(frame)] for one UTC day's buyback parts (check_offline_items.aerodrome_buyback_window) and the log line. One
    source string, seeded or daily."""
    src = f"{SOURCE}:buyback_window"
    wm = spec["window_metrics"]
    frames = [tidy([(day, float(got[k]))], name, m, src, TIER) for k, m in wm.items()]
    who = "; ".join(f"{a[:10]}… {v:,.0f}" for a, v in sorted(got.get("compounders", {}).items(), key=lambda kv: -kv[1]))
    ins = "; ".join(f"{a[:10]}… {v:,.0f}" for a, v in sorted(got.get("inflows", {}).items(), key=lambda kv: -kv[1])[:5])
    msg = (f"Foundation buyback {pd.Timestamp(day).date()}: bought+locked in one tx {got['bought_same_tx']:,.2f}; "
           f"locked from balance {got['lock_from_balance']:,.2f} ({len(got['locks'])} lock(s)); purchased "
           f"{got['purchased']:,.2f} ({len(got['purchases'])} purchase(s)); compounded (owners' share) "
           f"{got['compounded_owner']:,.2f}" + (f" by {who}" if who else "") + f"; rebase (excluded) "
           f"{got['rebase_owner']:,.2f}" + (f"; other AERO inflows (not purchases): {ins}" if ins else "")
           + (" (archive read)" if archive else ""))
    return frames, msg


def _day_blocks(day) -> tuple:
    """(first block of `day`, first block of the next day), UTC, on Base."""
    import check_offline_items as coi                      # noqa: PLC0415
    d = pd.Timestamp(day).normalize()
    d = d.tz_localize("UTC") if d.tzinfo is None else d
    a = coi.rpc_block_at("base", int(d.timestamp()), say=lambda *_: None)
    b = coi.rpc_block_at("base", int((d + pd.Timedelta(days=1)).timestamp()), say=lambda *_: None)
    return a, b


def _window_reader(b0, b1):
    import check_offline_items as coi                      # noqa: PLC0415
    return coi.aerodrome_buyback_window(b0, b1)


def _buyback_window(self, p, spec, out):
    """THE AGREED BASIS (Jake 2026-10-11, B): once a day, the previous complete UTC day's parts — bought and locked,
    locked from balance, purchased, compounded (owners' share), rebase (apart). build_workbook._foundation_buyback_views
    sums them into actual_buyback_tokens."""
    name = p["name"]
    yday = (today() - pd.Timedelta(days=1)).normalize()
    key = f"ve_managed:{name}:buyback_window"
    if not self.daily.due(key, str(yday.date())):
        out.mark_current(SOURCE, name, spec["window_metrics"]["compounded_owner"], "buyback window: read today already",
                         TIER)
        return
    # ITS OWN DEADLINE (Round G 1.4): WINDOW_BUDGET_S of the tier's budget; past it the window stops with the reason and
    # is read again on the next run (its daily marker is set only once it is stored)
    import check_offline_items as coi                      # noqa: PLC0415
    from .base import set_phase
    t0 = time.monotonic()
    try:
        with coi.call_budget(float(spec.get("window_budget_s") or WINDOW_BUDGET_S)):
            set_phase(f"ve_managed {name}: buyback window {yday.date()} — the day's first Base blocks")
            b0, b1 = (getattr(self, "day_blocks", None) or _day_blocks)(yday)
            if not (b0 and b1):
                raise ValueError("the day's Base blocks could not be resolved")
            set_phase(f"ve_managed {name}: buyback window {yday.date()} — state bisection + receipts, blocks {b0:,}..{b1:,}")
            got = (getattr(self, "window_reader", None) or _window_reader)(b0, b1)
    except TimeoutError as e:
        out.fail(SOURCE, name, f"Foundation buyback window {yday.date()}: stopped after {time.monotonic() - t0:.0f}s — "
                 f"{e}; nothing stored, read again on the next run", TIER)
        return
    except Exception as e:  # noqa: BLE001
        from .chain import redact_urls
        out.fail(SOURCE, name, f"Foundation buyback window {yday.date()}: {redact_urls(e)}", TIER)
        return
    if got.get("error"):
        from .chain import redact_urls
        out.fail(SOURCE, name, f"Foundation buyback window {yday.date()}: {redact_urls(got['error'])} — nothing stored",
                 TIER)
        return
    frames, msg = window_rows(name, spec, yday, got)
    for f in frames:
        out.add(f, SOURCE, name, msg, TIER)
    self.daily.done(key, str(yday.date()))


VeManaged._foundation = _foundation


LAST_SEED: dict = {}             # the last seed_foundation's outcome counts (why_nothing_stored)


def why_nothing_stored(tally: dict, frames_rows: int) -> str:
    """One line: why a seed stored nothing (no key / nothing read / already stored / no events in range)."""
    if not tally.get("days"):
        return "no Foundation buyback project in scope"
    parts = []
    if tally["already_stored"] == tally["days"]:
        parts.append(f"all {tally['days']} days already stored")
    elif tally["already_stored"]:
        parts.append(f"{tally['already_stored']} of {tally['days']} days already stored")
    if tally["no_block"]:
        parts.append(f"{tally['no_block']} day(s) found no block for the day start (Base RPC unreachable?)")
    if tally["read_error"]:
        parts.append(f"{tally['read_error']} lock-stock read(s) failed (first: {tally['first_error']})")
    if tally["window_error"]:
        parts.append(f"{tally['window_error']} buyback-window read(s) failed (first: {tally['first_window_error']})")
    if not tally.get("keyed"):
        parts.append("NO KEYED BASE_RPC_URL (nor ALCHEMY_API_KEY): the public Base RPCs serve archive eth_call but not archive "
                     "eth_getLogs / receipts at volume, so the buyback window (purchases, locks, compounding) cannot "
                     "be read — set BASE_RPC_URL to a keyed archive endpoint")
    if tally["window_read"] and not frames_rows:
        parts.append(f"{tally['window_read']} window(s) read with no events in range")
    elif frames_rows:
        parts.append(f"{frames_rows} row(s) read but none written (already stored, or refused by validation)")
    return "; ".join(parts) or "nothing to read"


def _alchemy_base() -> str:
    from .chain import alchemy_url                         # noqa: PLC0415
    return alchemy_url("base") or ""


def seed_foundation(projects: list[dict], days: int = 100, stored=None, say=print, reader=None,
                    window_reader=None) -> list:
    """`token_metrics.py --seed aero_buyback`: the Foundation lock stock at each UTC day start over the last `days`
    days, at that day's first block (archive eth_call through Multicall3), only for days with no row. Frames out."""
    import check_offline_items as coi                      # noqa: PLC0415
    import os                                              # noqa: PLC0415
    import pandas as pd                                    # noqa: PLC0415
    reader = reader or _foundation_reader
    frames = []
    # WHY NOTHING WAS STORED, said (Jake's run 2026-10-12: "stored 0 rows in 2 s" with no reason): every day's outcome
    # is counted here and token_metrics.seed_aero_buyback prints it
    LAST_SEED.clear()
    LAST_SEED.update({"days": 0, "already_stored": 0, "no_block": 0, "read_error": 0, "window_error": 0,
                      "window_stored": 0, "window_read": 0, "first_error": "", "first_window_error": "",
                      # Round G7 item 7: an ALCHEMY_API_KEY alone keys Base too (fetch.chain.rpc_endpoints prepends
                      # alchemy_url("base")); the seed reads eth_call / eth_getBlockByNumber / eth_getBlockReceipts
                      # only — no eth_getLogs, so the free plan's 10-block getLogs limit does not apply
                      "keyed": bool(os.environ.get("BASE_RPC_URL", "").strip() or _alchemy_base())})
    for p in projects:
        spec = p.get("foundation_buyback")
        if not spec:
            continue
        name = p["name"]
        end = today()
        import progress                                   # noqa: PLC0415
        for k in range(days, 0, -1):
            progress.tick(f"{name} Foundation locks, days", days - k + 1, days)
            d = (end - pd.Timedelta(days=k)).normalize()
            LAST_SEED["days"] += 1
            if stored and stored(name, spec["stock_metric"], str(d.date())):
                LAST_SEED["already_stored"] += 1
                continue
            b = coi.rpc_block_at("base", int(d.tz_localize("UTC").timestamp()) if d.tzinfo is None
                                 else int(d.timestamp()), say=say)
            if b is None:
                LAST_SEED["no_block"] += 1
                say(f"  {d.date()}: no block for the day start — skipped")
                continue
            got = reader(spec["owners"], block=hex(b))
            if got.get("error"):
                LAST_SEED["read_error"] += 1
                LAST_SEED["first_error"] = LAST_SEED["first_error"] or str(got["error"])[:200]
                say(f"  {d.date()}: {got['error']} — skipped")
                continue
            fs, msg = foundation_rows(name, spec, d, got, archive=True)
            frames += fs
            say(f"  {d.date()} (block {b:,}): {msg}")
            # THE AGREED BASIS (Jake 2026-10-11, B): the day BEFORE d, between its first block and d's (b)
            if spec.get("basis") == "bought_plus_compounded":
                prev = d - pd.Timedelta(days=1)
                if stored and stored(name, spec["window_metrics"]["compounded_owner"], str(prev.date())):
                    LAST_SEED["window_stored"] += 1
                    continue
                b_prev = coi.rpc_block_at("base", int((prev.tz_localize("UTC") if prev.tzinfo is None else prev)
                                                      .timestamp()), say=lambda *_: None)
                if not b_prev:
                    say(f"  {prev.date()}: no block for the day start — buyback window skipped")
                    continue
                w = (window_reader or _window_reader)(b_prev, b)
                if w.get("error"):
                    LAST_SEED["window_error"] += 1
                    LAST_SEED["first_window_error"] = LAST_SEED["first_window_error"] or str(w["error"])[:200]
                    say(f"  {prev.date()}: buyback window {w['error']} — skipped")
                    continue
                LAST_SEED["window_read"] += 1
                fs, msg = window_rows(name, spec, prev, w, archive=True)
                frames += fs
                say(f"  {msg}")
    return frames



def seed_managed_locks(projects: list[dict], days: int | None = None, stored=None, say=print,
                       reader=None) -> tuple[list, str]:
    """`token_metrics.py --seed aero_managed_locks` (Jake 2026-10-12, decision 7 and Phase 1 review item 2 — Aerodrome's
    circulating restated on the on-chain basis): the filing wallets' managed-veNFT deposits at the first block of each
    past UTC day — the daily read's arithmetic, batched through Multicall3 (check_offline_items.
    aerodrome_managed_deposits: four eth_calls a day) — so the on-chain circulating, which subtracts them, has a history
    on its own basis. Only days with no row; newest first; source marked archive (one measuring point with the daily
    read). Public Base RPCs serve archive eth_call; a keyed BASE_RPC_URL is faster. Frames out, and one line saying what
    was read and what was not."""
    import config                                          # noqa: PLC0415
    import check_offline_items as coi                      # noqa: PLC0415
    from .archive import ARCHIVE_DAYS                      # noqa: PLC0415
    days = days or ARCHIVE_DAYS
    reader = reader or (lambda owners, block, escrow: coi.aerodrome_managed_deposits(owners, block=block, escrow=escrow))
    frames, n_read, n_stored, errs = [], 0, 0, []
    for p in projects:
        spec = p.get("ve_managed_holdings")
        if not spec:
            continue
        name, metric, chain = p["name"], spec["metric"], spec.get("chain", "base")
        src = config.mark_source(f"{SOURCE}:{spec['escrow'][:10]}.weights", "archive")
        end = today()
        import progress                                   # noqa: PLC0415
        for k in range(1, days + 1):
            progress.tick(f"{name} managed veNFT deposits, days", k, days)
            d = (end - pd.Timedelta(days=k)).normalize()
            if stored and stored(name, metric, str(d.date())):
                n_stored += 1
                continue
            b = coi.rpc_block_at(chain, int(d.tz_localize("UTC").timestamp()), say=lambda *_: None)
            if b is None:
                errs.append(f"{d.date()}: no block for the day start")
                continue
            got = reader(list(spec["owners"]), hex(b), spec["escrow"])
            if got.get("error"):
                from .chain import redact_urls
                errs.append(f"{d.date()}: {redact_urls(got['error'])}"[:200])
                say(f"  {d.date()}: {errs[-1]} — skipped")
                if len(errs) >= 5 and not n_read:
                    break                                  # the endpoint serves no state this far back: stop, said
                continue
            n_read += 1
            frames.append(tidy([(d, float(got["total"]))], name, metric, src, TIER))
            say(f"  {d.date()} (block {b:,}): {metric} = {got['total']:,.0f} ({got['in_managed']} of {got['nfts']} "
                f"veNFT(s) in a managed NFT)")
    line = (f"{n_read} day(s) read, {n_stored} already stored, {len(errs)} not read"
            + (f" (first: {errs[0]})" if errs else ""))
    return frames, line
