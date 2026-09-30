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
diamond; its 4.9966% rate is never stored. locked_tokens = totalAmountStaked() on the live diamond;
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

    def __init__(self, contract_factory=None, **_ignored):
        self._factory = contract_factory       # tests inject; production builds a web3 contract

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

    def _read(self, spec: dict, address: str) -> dict | str:
        """One diamond's readings, or why they are unusable: {address, rate, apr, aggregate
        (totalAmountStaked, PLUME), rows [(id, staked wei, commission 1e18, active)], total (the
        per-validator sum, PLUME), commission (stake-weighted over active), net_factor, cooldown}."""
        try:
            c = self._contract(dict(spec, address=address))
            rate = c.functions.getRewardRate(spec["reward_token"]).call()
            aggregate = c.functions.totalAmountStaked().call()
            validators = c.functions.getValidatorsList().call()
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            return f"{address} on {spec['rpc'].split('/')[2]}: {e}"
        if not isinstance(rate, int) or rate < 0:
            return f"{address}: getRewardRate answered {rate!r}, not a non-negative integer"
        if rate > int(spec["max_reward_rate"]):
            return (f"{address}: getRewardRate = {rate} is above the contract's own cap "
                    f"{spec['max_reward_rate']} — not the quantity read from source")
        if not isinstance(aggregate, int) or aggregate <= 0:
            return f"{address}: totalAmountStaked answered {aggregate!r}"
        rows = []
        for v in validators if isinstance(validators, (list, tuple)) else ():
            try:
                vid, staked, comm = int(v[0]), int(v[1]), int(v[2])
            except (TypeError, ValueError, IndexError):
                return f"{address}: getValidatorsList answered {str(validators)[:160]!r}"
            if staked < 0 or not 0 <= comm <= 10**18:
                return f"{address}: validator {vid} carries staked {staked} / commission {comm}"
            try:
                active = bool(c.functions.getValidatorStats(vid).call()[0])
            except Exception as e:  # noqa: BLE001
                active = None
                log.info("plume_staking: getValidatorStats(%s) on %s: %s", vid, address, e)
            rows.append((vid, staked, comm, active))
        total = sum(r[1] for r in rows)
        if not rows or total <= 0:
            return f"{address}: getValidatorsList holds no stake"
        try:
            cooldown = int(c.functions.getCooldownInterval().call())
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
            out.add(point(name, spec["net_apr_metric"], net, f"{tag}.gross*active(1-commission)", TIER, when),
                    SOURCE, name, f"{spec['net_apr_metric']} = {g['apr']:.4%} x {g['net_factor']:.4f} (active "
                                  f"stake net of commission, over all stake) = {net:.4%}", TIER)
        want = spec.get("cooldown_seconds")
        cd = g["cooldown"]
        out.skipped(SOURCE, name, f"getCooldownInterval() = "
                                  + (f"{cd:,} s = {cd / 86_400:g} days" if cd is not None else "UNREAD")
                                  + (f"; recorded {want:,} s" + ("" if cd == want else " — DIFFERENT: update "
                                     "plume_staking.cooldown_seconds with the date") if want else ""), TIER)
        if not confirmed:
            out.skipped(SOURCE, name, f"identified {g['address']} as the live diamond by reconciliation; set "
                                      f"plume_staking.address to it and live_contract.confirmed = True", TIER)
