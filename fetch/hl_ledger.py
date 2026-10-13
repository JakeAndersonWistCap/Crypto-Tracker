"""
fetch/hl_ledger.py — Hyperliquid's issuer-side wallets replayed from their own ledgers (fix round 3, B1 / H2; Jake
2026-10-13). Tier 1: Hyperliquid's own info API (POST https://api.hyperliquid.xyz/info), no key.

WHY. The team (0x43e9abea…), Hyper Foundation (0xd57ecca4…) and Community Grants (0xa20fcfa0…) wallets are read live
every run (fetch/hypercore.py: team_wallet_tokens, excluded_wallets_tokens, staked_noncirculating_tokens) — but only
from 2026-10-11/12, so (e) unlocks, Net Holder Yield, total dilution and Total Yield were BLOCKED for the largest
position. Each wallet's whole history is in its own ledgers, which this module replays — exactly, not modelled:

    userNonFundingLedgerUpdates   HYPE spotGenesis, spotTransfer / send in and out (+ the sender's native fee)
    delegatorHistory              cDeposit (spot -> staking) and FINALIZED withdrawals (staking -> spot)
    delegatorRewards              the daily staking reward credited to the staking balance
    userFillsByTime               HYPE bought / sold on the HYPE spot pairs (fee in HYPE taken off)

Each event moves the wallet's spot or staked HYPE; the end-of-UTC-day levels are the daily stock. Checked on the real
responses of 2026-10-13 18:09 UTC (fixtures/api_hl_ledgers.json): every wallet's replayed end level equals its live
spotClearinghouseState + delegatorSummary to 1e-8 (team 237,930,044.48, Foundation 60,559,177.73, Grants 2,974,072.51).

STORED (the complete UTC days of the last keep_days; today never — it is still moving):
    team_wallet_tokens_replay            team spot + staked, end of day
    excluded_wallets_tokens_replay       Foundation + Grants spot + staked (the exclude_wallets set)
    staked_noncirculating_tokens_replay  the three wallets' staked HYPE
    issuer_side_rewards_tokens_replay    staking rewards credited to the three that day (new supply into
                                         non-circulating wallets: config.ISSUER_MINTS, never a "return")
    issuer_release_replay_tokens         GROSS HYPE leaving the three to any address OUTSIDE the three (transfers out +
                                         HYPE sold): the exact release, the cross-check of the stock-derived (e)
    hl_reference_yield_gross             the Grants wallet's gross annual staking rate that day, reward ÷ staked at the
                                         previous close ÷ (1 − 0.03 commission) × 365 — the input of the MODELLED
                                         futureEmissions history (build_workbook._hl_emissions_history_views)
The build prepends the stocks to the live series as their history (config.HISTORY_PREPEND; same basis — the live read
is the same two calls at a moment of the day).
"""
from __future__ import annotations

import datetime as _dt
import logging
from collections import defaultdict
from decimal import Decimal

import pandas as pd

from .base import Http, tidy, today

log = logging.getLogger("token_metrics.fetch.hl_ledger")

SOURCE = "hypercore_info:ledger_replay"
TIER = 1
PAGE = 2000                     # the info API's page size for time-ranged user queries


def hype_pairs(spot_meta: dict) -> set[str]:
    """'@<index>' of every spot pair whose BASE token is HYPE."""
    tok = {t["index"]: t["name"] for t in spot_meta.get("tokens") or ()}
    return {f"@{u['index']}" for u in spot_meta.get("universe") or ()
            if u.get("tokens") and tok.get(u["tokens"][0]) == "HYPE"}


def _day(ms: int) -> _dt.date:
    return _dt.datetime.fromtimestamp(ms / 1000, _dt.timezone.utc).date()


def replay_wallet(address: str, ledger: list, history: list, rewards: list, fills: list, pairs: set,
                  internal: set | None = None) -> dict:
    """One wallet's ledgers -> {"eod": {date: (spot, staked)}, "out": {date: gross HYPE to outside `internal`},
    "reward": {date: reward}, "end": (spot, staked)}. Decimal arithmetic; unknown ledger types are listed, not
    guessed."""
    addr = address.lower()
    internal = {a.lower() for a in (internal or ())} | {addr}
    ev, unknown = [], []
    for x in ledger:
        d = x.get("delta") or {}
        t = d.get("type")
        if d.get("token") != "HYPE":
            continue
        a = Decimal(str(d.get("amount") or 0))
        if t == "spotGenesis":
            ev.append((x["time"], a, Decimal(0), "genesis"))
        elif t in ("spotTransfer", "send"):
            src, dst = str(d.get("user", "")).lower(), str(d.get("destination", "")).lower()
            sgn = (1 if dst == addr else 0) - (1 if src == addr else 0)
            fee = Decimal(str(d.get("nativeTokenFee") or 0)) if src == addr else Decimal(0)
            kind = "in" if sgn > 0 else ("out_internal" if dst in internal else "out") if sgn < 0 else "self"
            ev.append((x["time"], sgn * a - fee, Decimal(0), kind))
        elif t == "cStakingTransfer":
            continue                                     # the staking account side is read from delegatorHistory
        else:
            unknown.append(t)
    for x in history:
        d = x.get("delta") or {}
        if "cDeposit" in d:
            a = Decimal(str(d["cDeposit"]["amount"]))
            ev.append((x["time"], -a, a, "stake"))
        elif "withdrawal" in d and d["withdrawal"].get("phase") == "finalized":
            a = Decimal(str(d["withdrawal"]["amount"]))
            ev.append((x["time"], a, -a, "unstake"))
    for x in rewards:
        ev.append((x["time"], Decimal(0), Decimal(str(x["totalAmount"])), "reward"))
    for f in fills:
        if f.get("coin") in pairs:
            q = Decimal(str(f["sz"])) * (1 if f.get("side") == "B" else -1)
            if f.get("feeToken") == "HYPE":
                q -= Decimal(str(f.get("fee") or 0))
            ev.append((f["time"], q, Decimal(0), "buy" if q >= 0 else "sell"))
    ev.sort(key=lambda e: e[0])
    spot = staked = Decimal(0)
    eod, out, rew = {}, defaultdict(Decimal), defaultdict(Decimal)
    for t, ds, dk, kind in ev:
        spot += ds
        staked += dk
        day = _day(t)
        eod[day] = (spot, staked)
        if kind in ("out", "sell"):
            out[day] += -ds
        if kind == "reward":
            rew[day] += dk
    return {"eod": eod, "out": dict(out), "reward": dict(rew), "end": (spot, staked), "unknown": sorted(set(unknown))}


def daily(replays: dict, start: _dt.date, end: _dt.date) -> pd.DataFrame:
    """The replays as one row per UTC day in [start, end): each wallet's level carried from its last event."""
    rows, cur = [], {}
    for n, r in replays.items():
        before = [d for d in r["eod"] if d < start]
        cur[n] = r["eod"][max(before)] if before else (Decimal(0), Decimal(0))
    d = start
    while d < end:
        row = {"date": pd.Timestamp(d)}
        for n, r in replays.items():
            if d in r["eod"]:
                cur[n] = r["eod"][d]
            row[f"{n}_spot"], row[f"{n}_staked"] = float(cur[n][0]), float(cur[n][1])
            row[f"{n}_out"] = float(r["out"].get(d, 0))
            row[f"{n}_reward"] = float(r["reward"].get(d, 0))
        rows.append(row)
        d += _dt.timedelta(days=1)
    return pd.DataFrame(rows)


def series(frame: pd.DataFrame, spec: dict) -> dict:
    """{metric: [(date, value)]} from daily(): the stocks, rewards, gross release and the reference yield."""
    team, excl = spec["team"], tuple(spec["excluded"])
    allw = (team, *excl)
    out = {spec["metrics"]["team"]: [], spec["metrics"]["excluded"]: [], spec["metrics"]["staked"]: [],
           spec["metrics"]["rewards"]: [], spec["metrics"]["release"]: [], spec["metrics"]["ref_yield"]: []}
    ref, comm = spec["reference_wallet"], float(spec["reference_commission"])
    prev_staked = None
    for _, r in frame.iterrows():
        d = r["date"]
        out[spec["metrics"]["team"]].append((d, r[f"{team}_spot"] + r[f"{team}_staked"]))
        out[spec["metrics"]["excluded"]].append((d, sum(r[f"{n}_spot"] + r[f"{n}_staked"] for n in excl)))
        out[spec["metrics"]["staked"]].append((d, sum(r[f"{n}_staked"] for n in allw)))
        out[spec["metrics"]["rewards"]].append((d, sum(r[f"{n}_reward"] for n in allw)))
        out[spec["metrics"]["release"]].append((d, sum(r[f"{n}_out"] for n in allw)))
        if prev_staked and r[f"{ref}_reward"] > 0:
            out[spec["metrics"]["ref_yield"]].append((d, r[f"{ref}_reward"] / prev_staked / (1 - comm) * 365))
        prev_staked = r[f"{ref}_staked"]
    return out


class HLLedgerReplay:
    """Replays the declared wallets' ledgers once a day (config `hl_ledger_replay`)."""

    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, daily_checks=None, **_ignored):
        self.http = http or Http(min_interval=0.6, retries=2)
        if daily_checks is None:
            from .logcache import DailyChecks
            daily_checks = DailyChecks()
        self.daily = daily_checks

    def _post(self, url: str, body: dict) -> list | dict:
        return self.http.post(url, json_body=body)

    def _paged(self, url: str, body: dict) -> list:
        """A time-ranged user query, paged by startTime while a page comes back full."""
        rows, start = [], int(body.get("startTime", 0))
        for _ in range(200):
            page = self._post(url, {**body, "startTime": start})
            if not isinstance(page, list):
                raise ValueError(f"{body['type']}: not a list ({type(page).__name__})")
            rows += page
            if len(page) < PAGE:
                break
            start = max(int(x["time"]) for x in page) + 1
        return rows

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = p.get("hl_ledger_replay")
            if spec:
                self._project(p["name"], spec, out)

    def _project(self, name: str, spec: dict, out) -> None:
        url, day = spec["url"], str(today().date())
        metrics = list(spec["metrics"].values())
        if not self.daily.due(f"hl_ledger:{name}", day):
            for m in metrics:
                out.mark_current(SOURCE, name, m, f"{m}: the ledgers were replayed today ({day})", TIER)
            return
        try:
            pairs = hype_pairs(self._post(url, {"type": "spotMeta"}))
            replays = {}
            internal = set(spec["wallets"].values())
            for n, addr in spec["wallets"].items():
                replays[n] = replay_wallet(
                    addr,
                    self._paged(url, {"type": "userNonFundingLedgerUpdates", "user": addr, "startTime": 0}),
                    self._post(url, {"type": "delegatorHistory", "user": addr}),
                    self._post(url, {"type": "delegatorRewards", "user": addr}),
                    self._paged(url, {"type": "userFillsByTime", "user": addr, "startTime": 0}),
                    pairs, internal)
                if replays[n]["unknown"]:
                    out.warn(SOURCE, name, f"{n} {addr[:10]}…: ledger types not replayed {replays[n]['unknown']} "
                                           f"— read them before trusting the level", TIER)
                # THE CHECK: the replayed end level against the live state, read in the same run
                spot = self._post(url, {"type": "spotClearinghouseState", "user": addr})
                ds = self._post(url, {"type": "delegatorSummary", "user": addr})
                live = (sum(float(b.get("total") or 0) for b in spot.get("balances") or () if b.get("coin") == "HYPE")
                        + sum(float(ds.get(k) or 0) for k in ("delegated", "undelegated", "totalPendingWithdrawal")))
                got = float(sum(replays[n]["end"]))
                # a reward or transfer between the ledger reads and the state read moves the live level a little
                if abs(got - live) > max(1.0, 1e-6 * live) + float(spec.get("check_slack_tokens", 50_000)):
                    raise ValueError(f"{n}: replayed end {got:,.4f} vs live {live:,.4f} — the ledgers do not "
                                     f"reconcile; NOTHING STORED")
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            for m in metrics:
                out.fail(SOURCE, name, f"{m}: ledger replay: {e}", TIER)
            return
        self.daily.done(f"hl_ledger:{name}", day)
        end = today().date()
        start = end - _dt.timedelta(days=int(spec.get("keep_days", 460)))
        frame = daily(replays, start, end)
        for m, pts in series(frame, spec).items():
            if pts:
                out.add(tidy(pts, name, m, SOURCE, TIER), SOURCE, name,
                        f"{m}: {len(pts)} day(s) {pts[0][0].date()}..{pts[-1][0].date()} replayed from the wallets' "
                        f"own ledgers", TIER)
