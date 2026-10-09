"""
fetch/aero_voter.py — tier 2: what veAERO voters were PAID per epoch, from the reward contracts' state, stored once a
week as the on-chain reference for Aerodrome's in_revenue and a3_protocol_yield (Jake's run 2026-10-08 11:27).

THE READ is check_offline_items.aerodrome_voter_epoch — the probe Jake ran (epoch 2026-10-01: $1,918,756 paid, 18 tokens
unpriced, totalWeight 1,021,271,849, APR 12.56%): every voted pool's FeesVotingReward and BribeVotingReward
tokenRewardsPerEpoch[token][epoch] (aerodrome-finance/contracts@1ba30815 Reward.sol L34/L245) through Multicall3,
priced with DefiLlama's coins API AT THE EPOCH'S END. The Voter is read from veAERO.voter(), not wired by address.

Stored for the last COMPLETE epoch (Thursday 00:00 UTC) and — once a day until stored — every Q0 epoch and the one
before it (Jake's run 2026-10-09 ~14:10, 1b), each dated the epoch's start:
    voter_rewards_onchain_usd       fees + bribes notified for the epoch, priced tokens only ($)
    voter_rewards_onchain_fees_usd  the FeesVotingReward part (fees EARNED the week before: Gauge._claimFees runs at
                                    the epoch's distribute(), Reward._notifyRewardAmount books epochStart(now))
    voter_rewards_onchain_bribes_usd  the BribeVotingReward part (deposited during the epoch)
    voter_rewards_unpriced_count    reward tokens DefiLlama could not price (their value is not in the $ figure)
    voter_rewards_onchain_apr       $ x 52 / (Voter.totalWeight x AERO price at the epoch end)
and, every day (one eth_call), Voter.totalWeight() — the votes cast, the reward-bearing stake:
    voter_total_weight_tokens
plus, for each epoch read with an archive endpoint, Voter.totalWeight AT THE EPOCH'S START BLOCK (source suffix
:archive) on the epoch's start date where no reading is stored — the per-epoch stake (1c).

THE POOL SET: pools with votes now, and (archive) every pool with votes at the last block of any epoch read. Without
an archive endpoint a pool voted in an epoch and fully unvoted since is missed, and the $ figure can read low by its
rewards; the log line names the set used.
"""
from __future__ import annotations

import logging
import time

import pandas as pd

import config

from .base import tidy, today

log = logging.getLogger("token_metrics.fetch.aero_voter")

SOURCE = "aero_voter"
TIER = 2
WEEK = 7 * 86400


def _epoch_reader(epoch=None):
    import check_offline_items as coi                      # noqa: PLC0415 — the probe Jake runs, one computation
    return coi.aerodrome_voter_epoch(epoch=epoch, say=lambda m: log.info("aero_voter:%s", m), price_at_end=True)


def _epochs_reader(epochs):
    """{epoch: result} for several epochs in one pass — the structure read once; the pool set and each epoch's
    totalWeight at its start block read at past blocks (archive)."""
    import check_offline_items as coi                      # noqa: PLC0415
    return coi.aerodrome_voter_epochs(list(epochs), say=lambda m: log.info("aero_voter:%s", m), price_at_end=True,
                                      archive=True)


def _weight_reader() -> float | None:
    import check_offline_items as coi                      # noqa: PLC0415
    import config                                          # noqa: PLC0415
    c = config.PROJECT_BY_NAME["Aerodrome"]["contracts"]
    ve = c["ve"]["address"] if isinstance(c["ve"], dict) else c["ve"]
    w, _ = coi.eth_call(ve, coi._selx("voter()"), chain="base")
    if not w or len(w) < 42:
        return None
    t, _ = coi.eth_call("0x" + w[-40:], coi._selx("totalWeight()"), chain="base")
    return int(t, 16) / 1e18 if t else None


def _stored(project: str, metric: str, date: str) -> bool:
    """Whether metrics.db holds `metric` for `project` on `date` (read-only; False when the store is unreadable)."""
    import sqlite3
    import store as sm                                      # noqa: PLC0415
    try:
        con = sqlite3.connect(f"file:{sm.DB_PATH}?mode=ro", uri=True)
        try:
            return con.execute("SELECT 1 FROM metrics WHERE project = ? AND metric = ? AND date LIKE ? LIMIT 1",
                               (project, metric, f"{date}%")).fetchone() is not None
        finally:
            con.close()
    except sqlite3.Error:
        return False


class AeroVoter:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, epoch_reader=None, weight_reader=None, daily=None, now=None, stored=None, epochs_reader=None,
                 max_backfill=None, **_ignored):
        # AT MOST max_backfill EARLIER EPOCHS A RUN, newest first (Jake's run 2026-10-09 ~14:37 stored none: fourteen
        # epochs in one read outran the tier's 180s budget, and a timed-out tier stores nothing). None = the config's
        # backfill_per_run; `token_metrics.py --seed aero_epochs` passes 0 = no limit.
        self.max_backfill = max_backfill
        # ONE READER FOR MANY EPOCHS (Jake's run 2026-10-09 ~14:10, 1b): a single-epoch reader (the tests') is wrapped.
        if epochs_reader is None and epoch_reader is not None:
            epochs_reader = lambda eps: {e: epoch_reader(e) for e in eps}     # noqa: E731
        self.epochs_reader = epochs_reader or _epochs_reader
        self.weight_reader = weight_reader or _weight_reader
        self.stored = stored or _stored
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily
        self.now = now

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = p.get("voter_epochs")
            if not spec:
                continue
            name = p["name"]
            self._weight(name, spec, out)
            self._epoch(name, spec, out)

    def _weight(self, name, spec, out):
        day, metric = str(today().date()), spec["weight_metric"]
        key = f"{SOURCE}:{name}:{metric}"
        if not self.daily.due(key, day):
            out.mark_current(SOURCE, name, metric, f"{metric}: read today already", TIER)
            return
        try:
            tw = self.weight_reader()
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            from .chain import redact_urls
            out.fail(SOURCE, name, f"{metric}: {redact_urls(e)}", TIER)      # hosts only, never a key
            return
        if not tw:
            out.fail(SOURCE, name, f"{metric}: Voter.totalWeight() unreadable", TIER)
            return
        out.add(tidy([(today(), tw)], name, metric, f"{SOURCE}:Voter.totalWeight", TIER), SOURCE, name,
                f"{metric} = {tw:,.0f} veAERO (votes cast)", TIER)
        self.daily.done(key, day)

    def _epoch(self, name, spec, out):
        """THE LAST COMPLETE EPOCH, AND EVERY Q0 EPOCH BEFORE IT (Jake's run 2026-10-09 ~14:10, 1b: tokenRewardsPerEpoch is
        per-epoch state, readable now for past epochs — store all of Q0 and judge revenue over the quarter). Targets: the
        epoch starts from the one before Q0 (its bribes are the previous week's, which the like-for-like reference
        needs) to the last complete one. The latest is read as before (the store decides, not the marker); of the
        others, at most `backfill_per_run` a run, newest first, until their fees/bribes split is stored."""
        now = int(self.now if self.now is not None else time.time())
        latest = now // WEEK * WEEK - WEEK                   # the last COMPLETE epoch's start
        day_of = lambda e: pd.Timestamp(e, unit="s").normalize()                 # noqa: E731
        q0_start = int((pd.Timestamp(now, unit="s").normalize() - pd.Timedelta(days=int(spec.get("q0_days", 90))))
                       .timestamp())
        first = q0_start // WEEK * WEEK - WEEK
        targets = list(range(first, latest + 1, WEEK)) if spec.get("backfill_q0") else [latest]
        split = spec.get("fees_metric")
        key = f"{SOURCE}:{name}:epoch"
        want = []
        for e in targets:
            when = day_of(e)
            done = self.stored(name, spec["usd_metric"], str(when.date())) and (
                not split or self.stored(name, split, str(when.date())))
            if e == latest:
                if self.daily.get(key) == str(when.date()) and done:
                    out.mark_current(SOURCE, name, spec["usd_metric"], f"epoch {when.date()} stored already", TIER)
                    continue
                if self.daily.get(key) == str(when.date()):
                    # THE STORE DECIDES, NOT THE MARKER (Jake's run 2026-10-09 11:41, 3c)
                    log.info("aero_voter: epoch %s is marked read but its rows are not all stored — reading it again",
                             when.date())
                want.append(e)
            elif not done:
                want.append(e)
        back = [e for e in want if e != latest]
        cap = self.max_backfill if self.max_backfill is not None else int(spec.get("backfill_per_run", 3))
        if cap and len(back) > cap:
            left = len(back) - cap
            back = sorted(back)[-cap:]                     # newest first: the quarter's end fills first
            out.mark_current(SOURCE, name, spec["usd_metric"],
                             f"{left} earlier epoch(s) still to backfill after this run (at most {cap} a run; "
                             f"`python token_metrics.py --seed aero_epochs` reads them all)", TIER)
            want = [e for e in want if e == latest] + back
        if not want:
            return
        try:
            res = self.epochs_reader(want)
        except Exception as e:  # noqa: BLE001
            from .chain import redact_urls
            out.fail(SOURCE, name, f"{spec['usd_metric']}: {redact_urls(e)}", TIER)
            return
        if isinstance(res, dict) and res.get("error"):
            out.fail(SOURCE, name, f"{spec['usd_metric']}: {res['error']}", TIER)
            return
        src = f"{SOURCE}:tokenRewardsPerEpoch"
        for e in want:
            r, when = res.get(e) or {"error": "not read"}, day_of(e)
            if r.get("error"):
                out.fail(SOURCE, name, f"{spec['usd_metric']}: epoch {when.date()}: {r['error']}", TIER)
                continue
            usd, ap, unp = float(r["usd"]), float(r["aero_price"]), len(r["unpriced"])
            tws = r.get("total_weight_start")
            tw = float(tws) if tws else float(r["total_weight"])
            apr = usd * 52 / (tw * ap) if tw and ap else None
            rows = [(spec["usd_metric"], usd), (spec["unpriced_metric"], float(unp))]
            if split and r.get("usd_fees") is not None:
                rows += [(split, float(r["usd_fees"])), (spec["bribes_metric"], float(r["usd_bribes"]))]
            if apr is not None:
                rows.append((spec["apr_metric"], apr))
            msg = (f"epoch {when.date()}: ${usd:,.0f} paid to voters"
                   + (f" (fees ${float(r['usd_fees']):,.0f} + bribes ${float(r['usd_bribes']):,.0f})"
                      if r.get("usd_fees") is not None else "")
                   + f" over {r['pools']} pools ({r.get('pool_set', 'pools with votes now')}; {r['priced']} tokens "
                   f"priced at the epoch end, {unp} unpriced and left out); totalWeight {tw:,.0f}"
                   + (" at the epoch's start block" if tws else " now") + f"; AERO ${ap:,.4f}"
                   + (f"; APR = ${usd:,.0f} x 52 / ({tw:,.0f} x ${ap:,.4f}) = {apr:.2%}" if apr is not None else ""))
            for m, v in rows:
                out.add(tidy([(when, v)], name, m, src, TIER), SOURCE, name, f"{m}: {msg}", TIER)
            # THE PER-EPOCH STAKE (1c): Voter.totalWeight at the epoch's start block, dated the epoch's start — only
            # where that day holds no reading (a live daily read is never overwritten)
            if tws and not self.stored(name, spec["weight_metric"], str(when.date())):
                out.add(tidy([(when, float(tws))], name, spec["weight_metric"],
                             config.mark_source(f"{SOURCE}:Voter.totalWeight", "archive"), TIER), SOURCE, name,
                        f"{spec['weight_metric']} = {float(tws):,.0f} veAERO at block {r.get('start_block')} (the "
                        f"epoch {when.date()} start, archive)", TIER)
            if e == latest:
                self.daily.set(key, str(when.date()))
