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

    def _project(self, name: str, spec: dict, out) -> None:
        when = today()
        try:
            c = self._contract(spec)
            rate = c.functions.getRewardRate(spec["reward_token"]).call()
            aggregate = c.functions.totalAmountStaked().call()
            validators = c.functions.getValidatorsList().call()
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            out.fail(SOURCE, name, f"{spec['address']} on {spec['rpc'].split('/')[2]}: {e}", TIER)
            return
        if not isinstance(rate, int) or rate < 0:
            out.fail(SOURCE, name, f"getRewardRate answered {rate!r}, not a non-negative integer. "
                                   f"NOTHING STORED.", TIER)
            return
        if rate > int(spec["max_reward_rate"]):
            out.fail(SOURCE, name, f"getRewardRate = {rate} is above the contract's own cap "
                                   f"{spec['max_reward_rate']} — not the quantity read from source. "
                                   f"NOTHING STORED.", TIER)
            return
        apr = rate * SECONDS_PER_YEAR / 1e18
        out.add(point(name, spec["apr_metric"], apr, f"{SOURCE}:getRewardRate(PLUME_NATIVE)[GROSS]", TIER, when),
                SOURCE, name, f"{spec['apr_metric']} = {rate} x 31,536,000 / 1e18 = {apr:.4%} GROSS of "
                              f"validator commission (Plume staking diamond)", TIER)
        rows = []
        for v in validators if isinstance(validators, (list, tuple)) else ():
            try:
                vid, staked, comm = int(v[0]), int(v[1]), int(v[2])
            except (TypeError, ValueError, IndexError):
                rows = None
                break
            if staked < 0 or not 0 <= comm <= 10**18:
                rows = None
                break
            rows.append((vid, staked, comm))
        if not rows:
            out.fail(SOURCE, name, f"getValidatorsList answered {str(validators)[:200]!r} — not a list of "
                                   f"(id, totalStaked, commission) with commission in [0, 1e18]. "
                                   f"{spec['stake_metric']} NOT STORED (totalAmountStaked = "
                                   f"{aggregate!r}, never used: it read 95 PLUME on 2026-09-30).", TIER)
            return
        total = sum(r[1] for r in rows)
        agg = aggregate / 1e18 if isinstance(aggregate, int) else None
        if total <= 0 or (agg is not None and total / 1e18 < agg):
            out.fail(SOURCE, name, f"per-validator sum {total / 1e18:,.2f} PLUME is not above the "
                                   f"aggregate totalAmountStaked {agg!r} — inconsistent. NOTHING STORED.", TIER)
            return
        tokens = total / 1e18
        comm = sum(r[1] * r[2] for r in rows) / total / 1e18
        out.add(point(name, spec["stake_metric"], tokens, f"{SOURCE}:getValidatorsList.sum(totalStaked)", TIER, when),
                SOURCE, name, f"{spec['stake_metric']} = sum of {len(rows)} validators' totalStaked = "
                              f"{tokens:,.2f} PLUME (totalAmountStaked() = {agg:,.2f}, not used)", TIER)
        out.add(point(name, spec["commission_metric"], comm, f"{SOURCE}:getValidatorsList.commission[stake-weighted]",
                      TIER, when), SOURCE, name,
                f"{spec['commission_metric']} = stake-weighted commission {comm:.2%} over {len(rows)} "
                f"validators (min {min(r[2] for r in rows) / 1e18:.2%}, max {max(r[2] for r in rows) / 1e18:.2%})", TIER)
        net = apr * (1 - comm)
        out.add(point(name, spec["net_apr_metric"], net, f"{SOURCE}:gross*(1-commission)", TIER, when),
                SOURCE, name, f"{spec['net_apr_metric']} = {apr:.4%} x (1 - {comm:.2%}) = {net:.4%} NET of "
                              f"stake-weighted commission", TIER)
