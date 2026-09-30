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
  plume/src/facets/StakingFacet.sol:537         totalAmountStaked()
  PlumeRewardLogic.sol:175-187                  reward per token = seconds x rate (1e18 precision),
                                                less the validator's commission

    gross APR = rewardRates[PLUME_NATIVE] x 31,536,000 / 1e18    (before validator commission)

The rate is capped by the contract at 3171e9 (~10.0%/yr); a reading above the cap, or a
non-integer answer, stores nothing. Native PLUME has 18 decimals like any EVM native asset.
Rewards are paid from a pre-funded treasury (PlumeStakingRewardTreasury), not minted by this
contract — so this is what a staker is paid, and the headline says GROSS of commission.
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
            staked = c.functions.totalAmountStaked().call()
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            out.fail(SOURCE, name, f"{spec['address']} on {spec['rpc'].split('/')[2]}: {e}", TIER)
            return
        if not isinstance(rate, int) or not isinstance(staked, int) or rate < 0 or staked < 0:
            out.fail(SOURCE, name, f"getRewardRate/totalAmountStaked answered {rate!r}/{staked!r}, "
                                   f"not non-negative integers. NOTHING STORED.", TIER)
            return
        if rate > int(spec["max_reward_rate"]):
            out.fail(SOURCE, name, f"getRewardRate = {rate} is above the contract's own cap "
                                   f"{spec['max_reward_rate']} — not the quantity read from source. "
                                   f"NOTHING STORED.", TIER)
            return
        apr = rate * SECONDS_PER_YEAR / 1e18
        tokens = staked / 1e18
        out.add(point(name, spec["apr_metric"], apr, f"{SOURCE}:getRewardRate(PLUME_NATIVE)", TIER, when),
                SOURCE, name, f"{spec['apr_metric']} = {rate} x 31,536,000 / 1e18 = {apr:.4%} GROSS of "
                              f"validator commission (Plume staking diamond)", TIER)
        out.add(point(name, spec["stake_metric"], tokens, f"{SOURCE}:totalAmountStaked", TIER, when),
                SOURCE, name, f"{spec['stake_metric']} = totalAmountStaked() = {tokens:,.2f} PLUME", TIER)
