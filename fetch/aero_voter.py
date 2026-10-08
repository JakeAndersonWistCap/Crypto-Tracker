"""
fetch/aero_voter.py — tier 2: what veAERO voters were PAID per epoch, from the reward contracts' state, stored once a
week as the on-chain reference for Aerodrome's in_revenue and a3_protocol_yield (Jake's run 2026-10-08 11:27).

THE READ is check_offline_items.aerodrome_voter_epoch — the probe Jake ran (epoch 2026-10-01: $1,918,756 paid, 18 tokens
unpriced, totalWeight 1,021,271,849, APR 12.56%): every voted pool's FeesVotingReward and BribeVotingReward
tokenRewardsPerEpoch[token][epoch] (aerodrome-finance/contracts@1ba30815 Reward.sol L34/L245) through Multicall3,
priced with DefiLlama's coins API AT THE EPOCH'S END. The Voter is read from veAERO.voter(), not wired by address.

Stored for the last COMPLETE epoch (Thursday 00:00 UTC), dated the epoch's start, once — the epoch's date is kept in
daily-checks.json so a later run in the same week does not read it again:
    voter_rewards_onchain_usd       fees + bribes notified for the epoch, priced tokens only ($)
    voter_rewards_unpriced_count    reward tokens DefiLlama could not price (their value is not in the $ figure)
    voter_rewards_onchain_apr       $ x 52 / (Voter.totalWeight x AERO price at the epoch end)
and, every day (one eth_call), Voter.totalWeight() — the votes cast, the reward-bearing stake:
    voter_total_weight_tokens

LIMIT, SAID ONCE: pools are those with votes when the epoch is read (the first days of the next epoch); a pool voted
in the epoch and fully unvoted since is missed, so the $ figure can read low by its rewards.
"""
from __future__ import annotations

import logging
import time

import pandas as pd

from .base import tidy, today

log = logging.getLogger("token_metrics.fetch.aero_voter")

SOURCE = "aero_voter"
TIER = 2
WEEK = 7 * 86400


def _epoch_reader(epoch=None):
    import check_offline_items as coi                      # noqa: PLC0415 — the probe Jake runs, one computation
    return coi.aerodrome_voter_epoch(epoch=epoch, say=lambda m: log.info("aero_voter:%s", m), price_at_end=True)


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


class AeroVoter:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, epoch_reader=None, weight_reader=None, daily=None, now=None, **_ignored):
        self.epoch_reader = epoch_reader or _epoch_reader
        self.weight_reader = weight_reader or _weight_reader
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
        now = int(self.now if self.now is not None else time.time())
        epoch = now // WEEK * WEEK - WEEK                   # the last COMPLETE epoch's start
        when = pd.Timestamp(epoch, unit="s").normalize()
        key = f"{SOURCE}:{name}:epoch"
        if self.daily.get(key) == str(when.date()):
            out.mark_current(SOURCE, name, spec["usd_metric"], f"epoch {when.date()} stored already", TIER)
            return
        try:
            r = self.epoch_reader(epoch)
        except Exception as e:  # noqa: BLE001
            from .chain import redact_urls
            out.fail(SOURCE, name, f"{spec['usd_metric']}: {redact_urls(e)}", TIER)
            return
        if r.get("error"):
            out.fail(SOURCE, name, f"{spec['usd_metric']}: epoch {when.date()}: {r['error']}", TIER)
            return
        usd, tw, ap, unp = float(r["usd"]), float(r["total_weight"]), float(r["aero_price"]), len(r["unpriced"])
        apr = usd * 52 / (tw * ap) if tw and ap else None
        src = f"{SOURCE}:tokenRewardsPerEpoch"
        rows = [(spec["usd_metric"], usd), (spec["unpriced_metric"], float(unp))]
        if apr is not None:
            rows.append((spec["apr_metric"], apr))
        msg = (f"epoch {when.date()}: ${usd:,.0f} paid to voters over {r['pools']} voted pools ({r['priced']} tokens "
               f"priced at the epoch end, {unp} unpriced and left out); totalWeight {tw:,.0f}; AERO ${ap:,.4f}"
               + (f"; APR = ${usd:,.0f} x 52 / ({tw:,.0f} x ${ap:,.4f}) = {apr:.2%}" if apr is not None else ""))
        for m, v in rows:
            out.add(tidy([(when, v)], name, m, src, TIER), SOURCE, name, f"{m}: {msg}", TIER)
        self.daily.set(key, str(when.date()))
