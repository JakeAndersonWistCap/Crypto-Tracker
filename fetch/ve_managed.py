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


# ===== R12 (Jake 2026-11-19): THE DECLARED HANDOVER AT THE AERO MIGRATION. A row dated on or after the cutover
# (config foundation_buyback.migration.cutover, 2026-10-22) is read by the migration-aware readers and carries their
# measuring point (…_v3); a row before it keeps the legacy point. The day decides, never the moment of the run: a seed
# run today for 2026-09-01 writes the legacy point, for 2026-11-01 the v3 point.
def on_v3(spec: dict, day) -> bool:
    """Whether `day`'s rows are on the post-migration basis: the config declares the migration and `day` is on or
    after its cutover."""
    mig = spec.get("migration") or {}
    if not mig.get("cutover"):
        return False
    d = pd.Timestamp(day)
    d = d.tz_convert(None) if d.tzinfo is not None else d
    return d.normalize() >= pd.Timestamp(mig["cutover"])


def stock_source(spec: dict, day) -> str:
    return (spec["migration"]["stock_source"] if on_v3(spec, day) else f"{SOURCE}:foundation_locks")


def window_source(spec: dict, day) -> str:
    return (spec["migration"]["window_source"] if on_v3(spec, day) else f"{SOURCE}:buyback_window")


def foundation_rows(name: str, spec: dict, day, got: dict, archive: bool = False) -> tuple[list, str]:
    """[(frame)] for one day's Foundation lock read (check_offline_items.aerodrome_foundation_locks, or from the
    migration cutover aerodrome_foundation_locks_v3), and the log line. ONE SOURCE STRING PER BASIS, SEEDED OR DAILY
    (Jake's run on 20d0eb4: unify them) — an archive read says so in the log line."""
    src = stock_source(spec, day)
    frames = [tidy([(day, got["locked"])], name, spec["stock_metric"], src, TIER),
              tidy([(day, got["claimable"])], name, spec["claimable_metric"], src, TIER)]
    msg = (f"{spec['stock_metric']} = {got['locked']:,.0f} AERO locked by the Foundation's buyback wallets ("
           + "; ".join(f"{spec['owners'].get(o, o)} {v:,.0f}" for o, v in got["by_owner"].items())
           + f"; {got['nfts']} veNFT(s), {got['owned_managed']} owned managed veNFT(s) not counted); rebase claimable "
             f"{got['claimable']:,.0f}"
           + (f"; v3 basis: legacy locks still held {got['legacy']:,.0f} + sAERO stakes {got['direct']:,.0f} + Relay "
              f"principal {got['relay']:,.0f} + pending Relay deposits {got['pending']:,.0f}" if "legacy" in got else "")
           + (" (archive read)" if archive else ""))
    return frames, msg


def _foundation_reader(owners, block="latest"):
    import check_offline_items as coi                      # noqa: PLC0415 — one read, shared with the probe and seed
    return coi.aerodrome_foundation_locks(list(owners), block=block)


def _foundation_reader_v3(owners, block="latest"):
    import check_offline_items as coi                      # noqa: PLC0415
    return coi.aerodrome_foundation_locks_v3(list(owners), block=block)


def stock_reader_for(spec: dict, day):
    return _foundation_reader_v3 if on_v3(spec, day) else _foundation_reader


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
        got = (getattr(self, "foundation_reader", None) or stock_reader_for(spec, today()))(spec["owners"])
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
    """[(frame)] for one UTC day's buyback parts (check_offline_items.aerodrome_buyback_window, or from the migration
    cutover aerodrome_buyback_window_v3) and the log line. One source string per basis, seeded or daily."""
    src = window_source(spec, day)
    wm = spec["window_metrics"]
    frames = [tidy([(day, float(got[k]))], name, m, src, TIER) for k, m in wm.items()]
    who = "; ".join(f"{a[:10]}… {v:,.0f}" for a, v in sorted(got.get("compounders", {}).items(), key=lambda kv: -kv[1]))
    ins = "; ".join(f"{a[:10]}… {v:,.0f}" for a, v in sorted(got.get("inflows", {}).items(), key=lambda kv: -kv[1])[:5])
    msg = (f"Foundation buyback {pd.Timestamp(day).date()}: bought+locked in one tx {got['bought_same_tx']:,.2f}; "
           f"locked from balance {got['lock_from_balance']:,.2f} ({len(got['locks'])} lock(s)); purchased "
           f"{got['purchased']:,.2f} ({len(got['purchases'])} purchase(s)); compounded (owners' share) "
           f"{got['compounded_owner']:,.2f}" + (f" by {who}" if who else "") + f"; rebase (excluded) "
           f"{got['rebase_owner']:,.2f}" + (f"; other AERO inflows (not purchases): {ins}" if ins else "")
           + (f"; v3 basis (both AERO tokens, sAERO stakes, Relays): migrated 1:1, not bought "
              f"{got.get('migrated_liquid', 0.0):,.2f}; locks migrated to sAERO, not new locks "
              f"{got.get('migrated_locks', 0.0):,.2f}; burned out of the escrow {got.get('burned', 0.0):,.2f}"
              if on_v3(spec, day) else "")
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


def _window_reader_v3(b0, b1):
    import check_offline_items as coi                      # noqa: PLC0415
    return coi.aerodrome_buyback_window_v3(b0, b1)


def window_reader_for(spec: dict, day):
    return _window_reader_v3 if on_v3(spec, day) else _window_reader


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
            got = (getattr(self, "window_reader", None) or window_reader_for(spec, yday))(b0, b1)
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
        parts.append(f"all {tally['days']} days already stored (lock stock and buyback window)")
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


def stored_source(project: str, metric: str, date: str) -> str | None:
    """The source of the stored row of `metric` for `project` on `date`, or None (read-only; None too when the store
    is unreadable — the day is then read, and the upsert decides)."""
    import sqlite3                                         # noqa: PLC0415
    import store as sm                                     # noqa: PLC0415
    try:
        con = sqlite3.connect(f"file:{sm.DB_PATH}?mode=ro", uri=True)
        try:
            r = con.execute("SELECT source FROM metrics WHERE project = ? AND metric = ? AND date LIKE ? LIMIT 1",
                            (project, metric, f"{date}%")).fetchone()
            return None if r is None else str(r[0])
        finally:
            con.close()
    except sqlite3.Error:
        return None


def _held(stored, stored_src, name: str, metric: str, key: str, want: str, post: bool) -> tuple[bool, bool]:
    """(held, a legacy-basis row is in the way). A stored day is HELD — never read or written again — unless it is
    dated on or after the migration cutover and was written by the LEGACY reader: that row is not on the day's declared
    basis (it read only the legacy token and escrow), so the day is read again on the v3 basis and the row replaced —
    the declared cut of the handover, made in the store (as fetch/aero_v3.held_on_the_new_read does for the supply)."""
    from .base import _measuring_point                     # noqa: PLC0415
    if stored_src is not None:
        src = stored_src(name, metric, key)
        if src is None:
            return False, False
        if post and _measuring_point(src) != _measuring_point(want):
            return False, True
        return True, False
    return bool(stored and stored(name, metric, key)), False


SEED_DAYS = 100                  # the default span of `--seed aero_buyback`


def seed_span(spec: dict, days: int | None, end) -> list:
    """The UTC days a seed covers, oldest first. `days` given: exactly the last `days` days. Not given (the command's
    default): the last SEED_DAYS days, reaching back to `seed_from` where the config names one (the first purchase of
    the pool the first lock in Q0 is matched to — foundation_buyback.seed_from)."""
    end = pd.Timestamp(end)
    end = (end.tz_convert(None) if end.tzinfo is not None else end).normalize()
    first = end - pd.Timedelta(days=SEED_DAYS if days is None else days)
    if days is None and spec.get("seed_from"):
        first = min(first, pd.Timestamp(spec["seed_from"]))
    return [end - pd.Timedelta(days=k) for k in range(int((end - first).days), 0, -1)]


def seed_foundation(projects: list[dict], days: int | None = None, stored=None, say=print, reader=None,
                    window_reader=None, stored_src=None, write=None) -> list:
    """`token_metrics.py --seed aero_buyback`: for every UTC day of the span (seed_span), TWO INDEPENDENT READS —
      the buyback WINDOW of the day (its first block to the next day's first block): bought and locked, locked from
        balance, purchased, compounded, rebase — wherever the day holds no window row;
      the lock STOCK at the day's first block (archive eth_call through Multicall3) — wherever it holds no stock row.
    R12 (Jake's run 2026-11-19, 325061a): the window used to be read only inside the iteration of a day whose STOCK
    was missing, so on a store holding every stock day the windows of 2026-08-21..10-15 were never read and Q0 never
    filled. A stored day is never read or written again (_held; the one exception is a legacy-basis row dated after the
    migration cutover). RESUMABLE: `write(frames)` is called per day as it lands, so a run that is stopped keeps what
    it read and the next run reads only what is missing. One line per day says what was done. Frames out."""
    import check_offline_items as coi                      # noqa: PLC0415
    import os                                              # noqa: PLC0415
    import pandas as pd                                    # noqa: PLC0415
    from .chain import redact_urls                         # noqa: PLC0415
    frames = []
    # WHY NOTHING WAS STORED, said (Jake's run 2026-10-12: "stored 0 rows in 2 s" with no reason): every day's outcome
    # is counted here and token_metrics.seed_aero_buyback prints it
    LAST_SEED.clear()
    LAST_SEED.update({"days": 0, "already_stored": 0, "no_block": 0, "read_error": 0, "window_error": 0,
                      "window_stored": 0, "window_read": 0, "stock_read": 0, "first_error": "",
                      "first_window_error": "",
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
        span = seed_span(spec, days, end)
        blocks: dict = {}

        def first_block(d):
            k = str(d.date())
            if k not in blocks:
                blocks[k] = coi.rpc_block_at("base", int(d.tz_localize("UTC").timestamp()), say=lambda *_: None)
            return blocks[k]
        import progress                                   # noqa: PLC0415
        basis = spec.get("basis") == "bought_plus_compounded"
        for i, d in enumerate(span):
            progress.tick(f"{name} Foundation buyback, days", i + 1, len(span))
            key, post = str(d.date()), on_v3(spec, d)
            LAST_SEED["days"] += 1
            did, day_frames, all_held = [], [], True
            tag = " on the v3 basis" if post else ""
            # ---- THE WINDOW OF DAY d — its own read, whatever the stock holds
            if basis:
                held, legacy_row = _held(stored, stored_src, name, spec["window_metrics"]["compounded_owner"], key,
                                         window_source(spec, d), post)
                if held:
                    LAST_SEED["window_stored"] += 1
                    did.append("buyback window already stored")
                else:
                    all_held = False
                    b0, b1 = first_block(d), first_block(d + pd.Timedelta(days=1))
                    if not (b0 and b1):
                        LAST_SEED["no_block"] += 1
                        did.append("buyback window NOT read — no block for the day's start or end")
                    else:
                        w = (window_reader or window_reader_for(spec, d))(b0, b1)
                        if w.get("error"):
                            LAST_SEED["window_error"] += 1
                            LAST_SEED["first_window_error"] = (LAST_SEED["first_window_error"]
                                                               or redact_urls(str(w["error"]))[:200])
                            did.append(f"buyback window NOT read — {redact_urls(str(w['error']))[:200]}")
                        else:
                            LAST_SEED["window_read"] += 1
                            fs, msg = window_rows(name, spec, d, w, archive=True)
                            day_frames += fs
                            did.append(f"buyback window READ{tag}, blocks {b0:,}..{b1:,}"
                                       + (" (replaces the legacy-basis row of a day after the migration)"
                                          if legacy_row else "") + f" — {msg}")
            # ---- THE LOCK STOCK AT d's FIRST BLOCK
            held, legacy_row = _held(stored, stored_src, name, spec["stock_metric"], key, stock_source(spec, d), post)
            if held:
                did.append("lock stock already stored")
            else:
                all_held = False
                b = first_block(d)
                if b is None:
                    LAST_SEED["no_block"] += 1
                    did.append("lock stock NOT read — no block for the day start")
                else:
                    got = (reader or stock_reader_for(spec, d))(spec["owners"], block=hex(b))
                    if got.get("error"):
                        LAST_SEED["read_error"] += 1
                        LAST_SEED["first_error"] = LAST_SEED["first_error"] or redact_urls(str(got["error"]))[:200]
                        did.append(f"lock stock NOT read — {redact_urls(str(got['error']))[:200]}")
                    else:
                        LAST_SEED["stock_read"] += 1
                        fs, msg = foundation_rows(name, spec, d, got, archive=True)
                        day_frames += fs
                        did.append(f"lock stock READ{tag} at block {b:,}"
                                   + (" (replaces the legacy-basis row of a day after the migration)"
                                      if legacy_row else "") + f" — {msg}")
            if all_held:
                LAST_SEED["already_stored"] += 1
            frames += day_frames
            if write is not None and day_frames:
                write(day_frames)
            say(f"  {key}: " + "; ".join(did))
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
