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

** totalAmountStaked() IS NOT THE TOTAL ON MAINNET (Jake's probe, 2026-09-30). ** It answered
94,868,945,541,646,499,332 — "95 PLUME" — which is impossible for a live network. Source does not
explain it (between dd5df2e and d408f63, May 2025, unstake stopped decrementing it — that would
push it UP); the deployed storage evidently differs. The total is the per-validator sum; the
aggregate is logged beside it and never stored.

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
        """One diamond's readings, or why they are unusable: {address, rate, apr, aggregate,
        rows [(id, staked wei, commission 1e18)], total (PLUME), commission (fraction)}."""
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
        rows = []
        for v in validators if isinstance(validators, (list, tuple)) else ():
            try:
                vid, staked, comm = int(v[0]), int(v[1]), int(v[2])
            except (TypeError, ValueError, IndexError):
                return f"{address}: getValidatorsList answered {str(validators)[:160]!r}"
            if staked < 0 or not 0 <= comm <= 10**18:
                return f"{address}: validator {vid} carries staked {staked} / commission {comm}"
            rows.append((vid, staked, comm))
        total = sum(r[1] for r in rows)
        if not rows or total <= 0:
            return f"{address}: getValidatorsList holds no stake"
        return {"address": address, "rate": rate, "apr": rate * SECONDS_PER_YEAR / 1e18,
                "aggregate": aggregate / 1e18 if isinstance(aggregate, int) else None, "rows": rows,
                "total": total / 1e18, "commission": sum(r[1] * r[2] for r in rows) / total / 1e18}

    def _project(self, name: str, spec: dict, out) -> None:
        """NOTHING IS STORED UNTIL ONE DIAMOND RECONCILES WITH THE APP (Jake, 2026-09-30).

        staking.plume.org showed 134.1M PLUME staked (live_contract.app_total_tokens). Every
        candidate diamond is read and its per-validator total compared with that figure; exactly
        ONE within ±app_tolerance is the live contract, and only then are its stake, gross APR,
        commission and net APR stored. 0xCF8B (the deploy script's test diamond, 94.87 PLUME)
        cannot match, so its 4.9966% rate is never stored. None, or more than one, matching stores
        nothing and logs every candidate against the target."""
        when = today()
        live = spec.get("live_contract") or {}
        cands = [spec["address"]] if live.get("confirmed") else list(live.get("candidates") or (spec["address"],))
        target, tol = live.get("app_total_tokens"), float(live.get("app_tolerance") or 0)
        reads, lines = [], []
        for addr in cands:
            got = self._read(spec, addr)
            if isinstance(got, str):
                lines.append(got)
                continue
            reads.append(got)
            vs = (f" vs the app's {target:,.0f} ({got['total'] / target - 1:+.2%})" if target else "")
            lines.append(f"{addr}: {len(got['rows'])} validators, {got['total']:,.2f} PLUME{vs}, gross APR "
                         f"{got['apr']:.4%}, commission {got['commission']:.2%}")
        match = [g for g in reads if target and abs(g["total"] / target - 1) <= tol]
        if len(match) != 1:
            why = ("no app total to reconcile against (live_contract.app_total_tokens)" if not target else
                   f"{len(match)} candidate(s) within ±{tol:.1%} of staking.plume.org's {target:,.0f} PLUME "
                   f"({live.get('app_read', '')})")
            out.skipped(SOURCE, name, f"NOTHING STORED — {why}. Candidates: " + " | ".join(lines), TIER)
            return
        g = match[0]
        tag = f"{SOURCE}:{g['address'][:10]}"
        out.add(point(name, spec["apr_metric"], g["apr"], f"{tag}.getRewardRate(PLUME_NATIVE)[GROSS]", TIER, when),
                SOURCE, name, f"{spec['apr_metric']} = {g['rate']} x 31,536,000 / 1e18 = {g['apr']:.4%} GROSS "
                              f"of validator commission — live diamond {g['address']}", TIER)
        out.add(point(name, spec["stake_metric"], g["total"], f"{tag}.getValidatorsList.sum(totalStaked)", TIER, when),
                SOURCE, name, f"{spec['stake_metric']} = sum of {len(g['rows'])} validators' totalStaked = "
                              f"{g['total']:,.2f} PLUME, reconciled with the app's {target:,.0f} "
                              f"({g['total'] / target - 1:+.2%}, within ±{tol:.1%}) — {g['address']} is the live "
                              f"diamond (totalAmountStaked() = {g['aggregate']!r}, not used)", TIER)
        out.add(point(name, spec["commission_metric"], g["commission"],
                      f"{tag}.getValidatorsList.commission[stake-weighted]", TIER, when), SOURCE, name,
                f"{spec['commission_metric']} = stake-weighted commission {g['commission']:.2%}", TIER)
        net = g["apr"] * (1 - g["commission"])
        out.add(point(name, spec["net_apr_metric"], net, f"{tag}.gross*(1-commission)", TIER, when),
                SOURCE, name, f"{spec['net_apr_metric']} = {g['apr']:.4%} x (1 - {g['commission']:.2%}) = "
                              f"{net:.4%} NET of stake-weighted commission", TIER)
        if not live.get("confirmed"):
            out.skipped(SOURCE, name, f"identified {g['address']} as the live diamond by reconciliation; set "
                                      f"plume_staking.address to it and live_contract.confirmed = True", TIER)
