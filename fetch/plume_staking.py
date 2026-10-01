"""
fetch/plume_staking.py — Plume's staking APR and total staked, from its own staking contract.
G of the second pass (Jake, 2026-09-30: "Plume VALIDATOR YIELD: Jake understands 5-8%,
emissions-funded. Find Plume's own staking APR and wire it").

READ FROM PLUME'S OWN SOURCE, plumenetwork/contracts @8248e78ce0c15ad3875fb2e693de9685ecf982d7:
  plume/test/ForkTestPlumeStaking.s.sol:47-51   "Mainnet Configuration": rpc.plume.org and the
                                                Diamond proxy 0xCF8B97260F77c11d58542644c5fD1D5F93FdA57d
  plume/src/lib/PlumeStakingStorage.sol:14-15   PLUME_NATIVE = 0xEeee…EEeE, REWARD_PRECISION = 1e18
  plume/src/facets/RewardsFacet.sol:679         getRewardRate(address token) -> rewardRates[token]
  plume/src/facets/RewardsFacet.sol:67          MAX_REWARD_RATE = 3171e9 (the contract's cap)
  plume/src/facets/StakingFacet.sol:537         totalAmountStaked() -> Layout.totalStaked
  plume/src/facets/ValidatorFacet.sol:968       getValidatorsList() -> (uint16 id, uint256
                                                totalStaked, uint256 commission)[] (struct 75-79)
  PlumeRewardLogic.sol:175-187                  reward per token = seconds x rate (1e18 precision),
                                                less the validator's commission
  lib/PlumeStakingStorage.sol:150               commission uses REWARD_PRECISION (1e18 = 100%)

    gross APR = rewardRates[PLUME_NATIVE] x 31,536,000 / 1e18    (before validator commission)
    total staked = sum of getValidatorsList()[i].totalStaked / 1e18
    commission   = stake-weighted mean of getValidatorsList()[i].commission / 1e18
    net APR      = gross x (1 - commission)          (stored as its own metric)

** THE LIVE DIAMOND IS 0x30c791E4654EdAc575FA1700eD8633CB2FEDE871 (Jake's probes3, 2026-09-30). **
Its totalAmountStaked() = 134,043,359.31 PLUME, -0.04% against staking.plume.org's 134.1M, and
Mystic's myPLUME feed plumeStaking() points to it. 0xCF8B (94.87 PLUME) is the deploy script's test
diamond; its 0.50% commission is never stored (its 4.9966% gross rate equals the live diamond's,
fixed since 2025-10-01; with the live 10% commission that is 4.497% net = the app's 4.5% — Jake's
probes4, 2026-10-01). locked_tokens = totalAmountStaked() on the live diamond;
the per-validator sum is logged beside it.

THE ABI IS ALREADY v2. The staking facets at 8248e78 are byte-identical to 3ef710a ("[PDE-2650] v2
fixes", 2025-07-16), the commit that moved the scripts to 0x30c791E4. getValidatorInfo(uint16)
failed to decode for Jake because ValidatorInfo's field ORDER changed in d408f63 (same selector,
different tuple). It is not used: getValidatorStats(uint16) -> (bool active, uint256 commission,
uint256 totalStaked, uint256 stakersCount) (ValidatorFacet.sol@3ef710a:899-913) has kept its shape
in every version, and gives the active flag.
  - the rate is GLOBAL: setRewardRates writes one rate into every validator's checkpoint
    (RewardsFacet.sol@3ef710a:252-284); an inactive validator gets a 0-rate checkpoint
    (ValidatorFacet.sol:279-282) and its stake earns nothing (PlumeRewardLogic.sol:141-157)
  - commission is taken per validator (PlumeRewardLogic.sol:163-190, 339-354)
    net APR (per staked PLUME) = gross x sum_active(staked x (1 - commission)) / sum_all(staked)
    commission (reported)      = stake-weighted over ACTIVE validators
  - getCooldownInterval() is in SECONDS (PlumeStakingStorage.sol:111, StakingFacet.sol:830):
    1,814,400 = 21 days (Jake, 2026-09-30); read and compared with the recorded value every run.

The rate is capped by the contract at 3171e9; a reading above the cap, or a non-integer answer,
stores nothing. (MAX_REWARD_RATE x 31,536,000 / 1e18 = 100x a year — the interface's "~100% APY"
comment is off by 100x; the cap is a ceiling, not a plausibility bound.) Native PLUME has 18
decimals like any EVM native asset. Rewards are paid from a pre-funded treasury
(PlumeStakingRewardTreasury), not minted by this contract.
"""
from __future__ import annotations

import logging

import pandas as pd

import config

from .base import point, today

log = logging.getLogger("token_metrics.fetch.plume_staking")

SOURCE = "plume_staking"
TIER = 2
SECONDS_PER_YEAR = 31_536_000
ABI = [
    {"inputs": [{"name": "token", "type": "address"}], "name": "getRewardRate",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "totalAmountStaked",
     "outputs": [{"name": "amount", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "validatorId", "type": "uint16"}], "name": "getValidatorStats",
     "outputs": [{"name": "active", "type": "bool"}, {"name": "commission", "type": "uint256"},
                 {"name": "totalStaked", "type": "uint256"}, {"name": "stakersCount", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getTreasury",
     "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getCooldownInterval",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getValidatorsList",
     "outputs": [{"name": "list", "type": "tuple[]", "components": [
         {"name": "id", "type": "uint16"}, {"name": "totalStaked", "type": "uint256"},
         {"name": "commission", "type": "uint256"}]}],
     "stateMutability": "view", "type": "function"},
]


class PlumeStaking:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, contract_factory=None, http=None, cache_file=None, **_ignored):
        self._factory = contract_factory       # tests inject; production builds a web3 contract
        self._http = http
        self._cache_file = cache_file

    def _contract(self, spec: dict):
        if self._factory:
            return self._factory(spec)
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(spec["rpc"], request_kwargs={"timeout": 25}))
        return w3.eth.contract(address=Web3.to_checksum_address(spec["address"]), abi=ABI)

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            spec = p.get("plume_staking")
            if spec:
                self._project(p["name"], spec, out)

    @staticmethod
    def _call(fn, block):
        return fn.call() if block is None else fn.call(block_identifier=block)

    def _read(self, spec: dict, address: str, block=None) -> dict | str:
        """One diamond's readings, or why they are unusable: {address, rate, apr, aggregate
        (totalAmountStaked, PLUME), rows [(id, staked wei, commission 1e18, active)], total (the
        per-validator sum, PLUME), commission (stake-weighted over active), net_factor, cooldown}."""
        try:
            c = self._contract(dict(spec, address=address))
            rate = self._call(c.functions.getRewardRate(spec["reward_token"]), block)
            aggregate = self._call(c.functions.totalAmountStaked(), block)
            validators = self._call(c.functions.getValidatorsList(), block)
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            return f"{address} on {spec['rpc'].split('/')[2]}: {e}"
        if not isinstance(rate, int) or rate < 0:
            return f"{address}: getRewardRate answered {rate!r}, not a non-negative integer"
        if rate > int(spec["max_reward_rate"]):
            return (f"{address}: getRewardRate = {rate} is above the contract's own cap "
                    f"{spec['max_reward_rate']} — not the quantity read from source")
        if not isinstance(aggregate, int) or aggregate <= 0:
            return f"{address}: totalAmountStaked answered {aggregate!r}"
        parsed = []
        for v in validators if isinstance(validators, (list, tuple)) else ():
            try:
                vid, staked, comm = int(v[0]), int(v[1]), int(v[2])
            except (TypeError, ValueError, IndexError):
                return f"{address}: getValidatorsList answered {str(validators)[:160]!r}"
            if staked < 0 or not 0 <= comm <= 10**18:
                return f"{address}: validator {vid} carries staked {staked} / commission {comm}"
            parsed.append((vid, staked, comm))

        def active_of(vid):
            try:
                return bool(self._call(c.functions.getValidatorStats(vid), block)[0])
            except Exception as e:  # noqa: BLE001
                log.info("plume_staking: getValidatorStats(%s) on %s: %s", vid, address, e)
                return None
        # BATCHED (Jake's run 2026-10-01: plume_staking timed out at 60s on one-by-one reads)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:
            flags = list(pool.map(active_of, [r[0] for r in parsed]))
        rows = [(vid, staked, comm, act) for (vid, staked, comm), act in zip(parsed, flags)]
        total = sum(r[1] for r in rows)
        if not rows or total <= 0:
            return f"{address}: getValidatorsList holds no stake"
        try:
            cooldown = int(self._call(c.functions.getCooldownInterval(), block))
        except Exception:  # noqa: BLE001
            cooldown = None
        known = all(r[3] is not None for r in rows)
        act = [r for r in rows if r[3]] if known else []
        act_stake = sum(r[1] for r in act)
        return {"address": address, "rate": rate, "apr": rate * SECONDS_PER_YEAR / 1e18,
                "aggregate": aggregate / 1e18, "rows": rows, "total": total / 1e18,
                "active_known": known, "n_active": len(act), "active_share": act_stake / total,
                "commission": (sum(r[1] * r[2] for r in act) / act_stake / 1e18) if act_stake else None,
                "net_factor": (sum(r[1] * (10**18 - r[2]) for r in act) / total / 1e18) if known else None,
                "cooldown": cooldown}

    def _project(self, name: str, spec: dict, out) -> None:
        """NOTHING IS STORED UNTIL ONE DIAMOND RECONCILES WITH THE APP (Jake, 2026-09-30).

        UNCONFIRMED: every candidate is read and its totalAmountStaked() compared with
        staking.plume.org's figure (live_contract.app_total_tokens); exactly ONE within
        ±app_tolerance is the live contract. CONFIRMED (0x30c791E4, Jake's probes3): only that
        address is read and the app comparison is LOGGED — it no longer gates, because the stake
        moves away from a one-day app reading as weeks pass. Stored: locked_tokens =
        totalAmountStaked(); the GROSS APR; the stake-weighted commission over ACTIVE validators;
        the NET APR = gross x sum_active(staked x (1 - commission)) / sum_all(staked)."""
        when = today()
        live = spec.get("live_contract") or {}
        confirmed = bool(live.get("confirmed"))
        cands = [spec["address"]] if confirmed else list(live.get("candidates") or (spec["address"],))
        target, tol = live.get("app_total_tokens"), float(live.get("app_tolerance") or 0)
        reads, lines = [], []
        for addr in cands:
            got = self._read(spec, addr)
            if isinstance(got, str):
                lines.append(got)
                continue
            reads.append(got)
            vs = (f" vs the app's {target:,.0f} ({got['aggregate'] / target - 1:+.2%})" if target else "")
            lines.append(f"{addr}: totalAmountStaked {got['aggregate']:,.2f} PLUME{vs}; {len(got['rows'])} "
                         f"validators summing {got['total']:,.2f}; gross APR {got['apr']:.4%}")
        if confirmed:
            match = reads
        else:
            match = [g for g in reads if target and abs(g["aggregate"] / target - 1) <= tol]
        if len(match) != 1:
            why = (f"the confirmed diamond {spec['address']} could not be read" if confirmed else
                   "no app total to reconcile against (live_contract.app_total_tokens)" if not target else
                   f"{len(match)} candidate(s) within ±{tol:.1%} of staking.plume.org's {target:,.0f} PLUME "
                   f"({live.get('app_read', '')})")
            out.skipped(SOURCE, name, f"NOTHING STORED — {why}. Candidates: " + " | ".join(lines), TIER)
            return
        g = match[0]
        tag = f"{SOURCE}:{g['address'][:10]}"
        vs = (f"{g['aggregate'] / target - 1:+.2%} against the app's {target:,.0f} ({live.get('app_read', '')})"
              if target else "no app figure")
        out.add(point(name, spec["apr_metric"], g["apr"], f"{tag}.getRewardRate(PLUME_NATIVE)[GROSS]", TIER, when),
                SOURCE, name, f"{spec['apr_metric']} = {g['rate']} x 31,536,000 / 1e18 = {g['apr']:.4%} GROSS "
                              f"of validator commission — live diamond {g['address']}", TIER)
        out.add(point(name, spec["stake_metric"], g["aggregate"], f"{tag}.totalAmountStaked", TIER, when),
                SOURCE, name, f"{spec['stake_metric']} = totalAmountStaked() = {g['aggregate']:,.2f} PLUME "
                              f"({vs}); {len(g['rows'])} validators sum to {g['total']:,.2f} "
                              f"({g['total'] / g['aggregate'] - 1:+.4%})", TIER)
        if g["commission"] is None or g["net_factor"] is None:
            out.skipped(SOURCE, name, f"{spec['commission_metric']} / {spec['net_apr_metric']}: NOT STORED — "
                                      f"getValidatorStats did not give every validator's active flag, and "
                                      f"inactive stake earns nothing", TIER)
        else:
            out.add(point(name, spec["commission_metric"], g["commission"],
                          f"{tag}.getValidatorsList.commission[stake-weighted, active]", TIER, when), SOURCE, name,
                    f"{spec['commission_metric']} = {g['commission']:.2%}, stake-weighted over {g['n_active']} "
                    f"active of {len(g['rows'])} validators ({g['active_share']:.2%} of stake)", TIER)
            net = g["apr"] * g["net_factor"]
            app = spec.get("app_net_apy") or {}
            apy = (1 + net / 365) ** 365 - 1
            recon = (f"; RECONCILED to staking.plume.org: net APR {net:.4%} -> net APY {apy:.4%} with daily "
                     f"compounding, vs the app's {app['value']:.2%} NET APY ({app['read']}): "
                     f"{apy - app['value']:+.2%} points" if app else "")
            out.add(point(name, spec["net_apr_metric"], net, f"{tag}.gross*active(1-commission)", TIER, when),
                    SOURCE, name, f"{spec['net_apr_metric']} = {g['apr']:.4%} x {g['net_factor']:.4f} (active "
                                  f"stake net of commission, over all stake) = {net:.4%} APR{recon}", TIER)
        want = spec.get("cooldown_seconds")
        cd = g["cooldown"]
        out.skipped(SOURCE, name, f"getCooldownInterval() = "
                                  + (f"{cd:,} s = {cd / 86_400:g} days" if cd is not None else "UNREAD")
                                  + (f"; recorded {want:,} s" + ("" if cd == want else " — DIFFERENT: update "
                                     "plume_staking.cooldown_seconds with the date") if want else ""), TIER)
        if not confirmed:
            out.skipped(SOURCE, name, f"identified {g['address']} as the live diamond by reconciliation; set "
                                      f"plume_staking.address to it and live_contract.confirmed = True", TIER)
        else:
            self._payouts(name, spec, out)

    # ===== REWARDS PAID, FROM THE TREASURY'S OWN EVENTS (Jake, 2026-10-01). =====
    def _payouts(self, name: str, spec: dict, out) -> None:
        """Every reward payout — claim and restake alike — goes through the treasury's
        distributeReward(token, amount, recipient), which emits RewardDistributed(address indexed
        token, uint256 amount, address indexed recipient) (PlumeEvents.sol@3ef710a:260;
        PlumeStakingRewardTreasury.sol:160-198). The treasury is read from the diamond
        (getTreasury(), RewardsFacet.sol:809), never assumed. Native PLUME only (topic1 =
        PLUME_NATIVE), summed per UTC day from the explorer's logs API (1000 a call, paged by block;
        progress cached). Stored as emissions_claimed_tokens: PAID, not accrued — the pre-funded
        treasury releasing PLUME to stakers, nothing minted."""
        pay = spec.get("payouts") or {}
        if not pay:
            return
        import json
        from pathlib import Path
        import pandas as pd
        from .base import Http, tidy
        try:
            treasury = self._call(self._contract(spec).functions.getTreasury(), None)
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"{pay['metric']}: getTreasury() on {spec['address']}: {e}", TIER)
            return
        if self._cache_file:
            path = Path(self._cache_file)
        else:
            from .logcache import LogCache
            path = LogCache().root / "plume-payouts.json"
        try:
            st = json.loads(path.read_text())
        except (OSError, ValueError):
            st = {}
        if st.get("treasury", "").lower() != str(treasury).lower():
            st = {"treasury": str(treasury), "next_block": 0, "days": {}}
        http = self._http or Http(min_interval=0.25, retries=2)
        native_topic = "0x" + spec["reward_token"][2:].lower().rjust(64, "0")
        calls = 0
        while calls < int(pay["max_calls_per_run"]):
            j = http.get(pay["logs_api"], params={
                "module": "logs", "action": "getLogs", "address": str(treasury), "fromBlock": st["next_block"],
                "toBlock": "latest", "topic0": pay["topic0"], "topic1": native_topic, "topic0_1_opr": "and"})
            calls += 1
            res = j.get("result") if isinstance(j, dict) else None
            if not isinstance(res, list):
                break
            blocks = [int(str(r["blockNumber"]), 16) if str(r["blockNumber"]).startswith("0x") else int(r["blockNumber"])
                      for r in res]
            full = len(res) >= int(pay["page_cap"])
            edge = max(blocks) if (full and blocks) else None
            for r, b in zip(res, blocks):
                if edge is not None and b == edge:
                    continue                      # re-read with the next page, never counted twice
                ts = int(str(r["timeStamp"]), 16) if str(r["timeStamp"]).startswith("0x") else int(r["timeStamp"])
                day = str(pd.Timestamp(ts, unit="s").normalize().date())
                st["days"][day] = st["days"].get(day, 0.0) + int(str(r["data"]), 16) / 1e18
            if full:
                st["next_block"] = edge
                continue
            st["next_block"] = (max(blocks) + 1) if blocks else st["next_block"]
            break
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(st))
        today_ = str(pd.Timestamp.now(tz="UTC").tz_localize(None).normalize().date())
        pts = sorted((pd.Timestamp(d), v) for d, v in st["days"].items() if d < today_)
        if not pts:
            out.skipped(SOURCE, name, f"{pay['metric']}: no RewardDistributed(PLUME) events yet from treasury "
                                      f"{treasury} ({calls} logs call(s))", TIER)
            return
        frame = tidy(pts, name, pay["metric"], f"{SOURCE}:treasury.RewardDistributed[paid, PLUME]", TIER)
        out.add(frame, SOURCE, name, f"{pay['metric']} = PLUME paid to stakers by treasury {treasury} "
                                     f"(RewardDistributed, claims + restakes; PAID, not accrued): {len(pts)} day(s) "
                                     f"{pts[0][0].date()}..{pts[-1][0].date()}; unlocks are NOT in it (no vesting "
                                     f"address is documented)", TIER)

    # ===== HISTORY: THE LIVE DIAMOND READ ON PAST DAYS (Jake's run 2026-09-30 17:21). =====
    def _chain(self, spec: dict):
        """(block_at(day) -> first block at/after 00:00 UTC, has_code(block) -> bool). Binary
        search on block timestamps over rpc.plume.org; tests inject their own."""
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(spec["rpc"], request_kwargs={"timeout": 25}))
        head = int(w3.eth.block_number)
        ts = {}

        def t(n):
            if n not in ts:
                ts[n] = int(w3.eth.get_block(n)["timestamp"])
            return ts[n]

        def block_at(day):
            want = int(pd.Timestamp(day).tz_localize("UTC").timestamp())
            lo, hi = 1, head
            if t(hi) < want:
                return None
            while lo < hi:
                mid = (lo + hi) // 2
                lo, hi = (mid + 1, hi) if t(mid) < want else (lo, mid)
            return lo

        def has_code(block):
            return len(w3.eth.get_code(Web3.to_checksum_address(spec["address"]), block_identifier=block)) > 0
        return block_at, has_code

    def seed(self, projects: list[dict], days: int, out, have: set | None = None, chain=None) -> dict:
        """Read the CONFIRMED live diamond at the first block of each of the last `days` days,
        newest first, skipping days already held (`have`), and store locked_tokens, the gross and
        net APR and the commission for each, dated to that day and marked `archive` (the same
        measuring point as the live read). STOPS at the first day the RPC will not serve state
        at — that is the answer to "does rpc.plume.org serve history" — or the first block where
        the diamond has no code yet (its deployment: the series' mechanism start)."""
        res = {"stored_days": 0, "stopped": None, "mechanism_start": None}
        for p in projects:
            spec = p.get("plume_staking") or {}
            if not spec or not (spec.get("live_contract") or {}).get("confirmed"):
                continue
            name = p["name"]
            block_at, has_code = chain or self._chain(spec)
            tag = f"{SOURCE}:{spec['address'][:10]}"
            for k in range(1, int(days) + 1):
                day = (today() - pd.Timedelta(days=k)).normalize()
                if have and day in have:
                    continue
                try:
                    b = block_at(day)
                    if b is None:
                        continue
                    if not has_code(b):
                        res["mechanism_start"] = str((day + pd.Timedelta(days=1)).date())
                        out.skipped(SOURCE, name, f"history: the live diamond has no code at block {b} "
                                                  f"({day.date()}) — deployed after it; the series starts "
                                                  f"{res['mechanism_start']}", TIER)
                        break
                except Exception as e:  # noqa: BLE001
                    res["stopped"] = f"{day.date()}: block lookup failed — {e}"
                    break
                g = self._read(spec, spec["address"], block=b)
                if isinstance(g, str):
                    res["stopped"] = f"{day.date()} (block {b}): {g}"
                    out.skipped(SOURCE, name, f"history STOPPED — rpc.plume.org did not serve state at "
                                              f"{day.date()} (block {b}): {g}. Days before it are "
                                              f"FORWARD-ONLY unless an archive endpoint serves them.", TIER)
                    break
                rows = [(spec["stake_metric"], g["aggregate"], f"{tag}.totalAmountStaked"),
                        (spec["apr_metric"], g["apr"], f"{tag}.getRewardRate(PLUME_NATIVE)[GROSS]")]
                if g["commission"] is not None and g["net_factor"] is not None:
                    rows += [(spec["commission_metric"], g["commission"],
                              f"{tag}.getValidatorsList.commission[stake-weighted, active]"),
                             (spec["net_apr_metric"], g["apr"] * g["net_factor"],
                              f"{tag}.gross*active(1-commission)")]
                for metric, v, src in rows:
                    src = config.mark_source(src, "archive")      # the live read's measuring point
                    out.add(point(name, metric, v, src, TIER, day), SOURCE, name,
                            f"history {day.date()} (block {b}): {metric} = {v:,.6g}", TIER)
                res["stored_days"] += 1
        return res
