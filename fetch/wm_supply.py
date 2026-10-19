"""
fetch/wm_supply.py — tier 2: WORLD MOBILE'S SUPPLY COMPONENTS AND LOCKED STAKING OFF EVM (Round G6 item 1, Jake
2026-10-19).

  wmtx_cardano_supply_tokens   Cardano-native WMTx, policy e5a42a1a…ca05a / WorldMobileTokenX — Koios asset_info
                               total_supply (6 decimals). Read 2026-10-19: 650,795,981.305422 (13,542 mints, 1,254 burns).
  wmt_legacy_supply_tokens     legacy Cardano WMT, policy 1d7f33bd…f0a0f34e1e / worldmobiletoken — 2,000,000,000, minted
                               once, never burned. A REFERENCE ONLY: where it sits is not known, so it is never in a total.
  locked_tokens_base           WMTX held by World Mobile Locked Staking on Base, 0xBD4f5D76…02Ae (balanceOf; 6 decimals).
  locked_tokens_cardano        WMTx held at the Cardano Locked Staking script addr1w9l52766…76yh4v (Koios address_assets).
  locked_tokens                their sum, stored only when both legs read.
Both staking addresses are World Mobile's own (worldmobile.io/blog/post/wmtx-locked-staking-is-live-pick-a-term-lock-the-
rate, 2026-08-10: "Locked Staking contract: 0xBD4f…" on Base and "Cardano Locked Staking contract: addr1w9l52766…"). Read
2026-10-19: 7,321,544.14 + 29,556,739.39. Koios serves no robots.txt (404). Once a day.
"""
from __future__ import annotations

import logging

from .base import Http, point, today

log = logging.getLogger("token_metrics.fetch.wm_supply")

SOURCE = "wm_supply"
TIER = 2
KOIOS = "https://api.koios.rest/api/v1"
WMTX_POLICY, WMTX_NAME = "e5a42a1a1d3d1da71b0449663c32798725888d2eb0843c4dabeca05a", "WorldMobileTokenX"
WMT_POLICY, WMT_NAME = "1d7f33bd23d85e1a25d87d86fac4f199c3197a2f7afeb662a0f34e1e", "worldmobiletoken"
BASE_TOKEN = "0x3e31966d4f81C72D2a55310A6365A56A4393E98D"
BASE_STAKING = "0xBD4f5D76ecb370e06095FFaF1CD485D9FD9f02Ae"
CARDANO_STAKING = "addr1w9l52766gs8wypceuw64uuvrqsw5glvmafa5nweskw8jqfc76yh4v"
DECIMALS = 6


class WmSupply:
    def __init__(self, http: Http | None = None, daily=None, base_call=None):
        self.http = http or Http(min_interval=1.0, timeout=30)
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily
        self._base_call = base_call

    def _asset_supply(self, policy: str, name: str) -> float:
        rows = self.http.post(f"{KOIOS}/asset_info", json_body={"_asset_list": [[policy, name.encode().hex()]]})
        if not rows:
            raise RuntimeError(f"Koios asset_info answered nothing for {policy[:8]}…/{name}")
        return int(rows[0]["total_supply"]) / 10 ** DECIMALS

    def _script_holding(self) -> float:
        rows = self.http.post(f"{KOIOS}/address_assets", json_body={"_addresses": [CARDANO_STAKING]}) or []
        want = WMTX_NAME.encode().hex()
        return sum(int(r["quantity"]) for r in rows
                   if r.get("policy_id") == WMTX_POLICY and r.get("asset_name") == want) / 10 ** DECIMALS

    def _base_staked(self) -> float:
        from eth_utils import keccak                                    # noqa: PLC0415
        data = "0x" + keccak(text="balanceOf(address)").hex()[:8] + BASE_STAKING.lower()[2:].rjust(64, "0")
        if self._base_call is not None:
            return self._base_call(BASE_TOKEN, data) / 10 ** DECIMALS
        from .chain import redact_urls, rpc_endpoints  # noqa: PLC0415
        errs = []
        for url in rpc_endpoints("base"):
            try:
                r = self.http.post(url, json_body={"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                                                   "params": [{"to": BASE_TOKEN, "data": data}, "latest"]})
                if "result" in r:
                    return int(r["result"], 16) / 10 ** DECIMALS
                errs.append(str(r.get("error"))[:80])
            except Exception as e:  # noqa: BLE001
                errs.append(redact_urls(e)[:80])
        raise RuntimeError("; ".join(errs))

    def run(self, projects: list[dict], window_days, out):
        name = "World Mobile"
        if not any(p["name"] == name for p in projects):
            return
        day, key = str(today().date()), f"{SOURCE}:{name}"
        mets = ("wmtx_cardano_supply_tokens", "wmt_legacy_supply_tokens", "locked_tokens_base", "locked_tokens_cardano",
                "locked_tokens")
        if not self.daily.due(key, day):
            for m in mets:
                out.mark_current(SOURCE, name, m, f"{m}: read today already", TIER)
            return
        legs, failed = {}, False
        for metric, fn, src in (
                ("wmtx_cardano_supply_tokens", lambda: self._asset_supply(WMTX_POLICY, WMTX_NAME),
                 f"koios:asset_info[{WMTX_POLICY[:8]}…{WMTX_NAME}]"),
                ("wmt_legacy_supply_tokens", lambda: self._asset_supply(WMT_POLICY, WMT_NAME),
                 f"koios:asset_info[{WMT_POLICY[:8]}…{WMT_NAME}]"),
                ("locked_tokens_base", self._base_staked, f"chain:base:balanceOf({BASE_STAKING})"),
                ("locked_tokens_cardano", self._script_holding, f"koios:address_assets[{CARDANO_STAKING[:16]}…]")):
            try:
                v = float(fn())
            except Exception as e:  # noqa: BLE001
                from .chain import redact_urls  # noqa: PLC0415
                out.fail(SOURCE, name, f"{metric}: {redact_urls(e)[:200]}", TIER)
                failed = True
                continue
            legs[metric] = v
            out.add(point(name, metric, v, f"{SOURCE}:{src}", TIER), SOURCE, name, f"{metric}: {v:,.6f}", TIER)
        if "locked_tokens_base" in legs and "locked_tokens_cardano" in legs:
            tot = legs["locked_tokens_base"] + legs["locked_tokens_cardano"]
            out.add(point(name, "locked_tokens", tot, f"{SOURCE}:locked_staking[base+cardano]", TIER), SOURCE, name,
                    f"locked_tokens = Base {legs['locked_tokens_base']:,.2f} + Cardano {legs['locked_tokens_cardano']:,.2f}"
                    f" = {tot:,.2f} (World Mobile Locked Staking, both contracts named in its 2026-08-10 post)", TIER)
        if not failed:
            self.daily.done(key, day)
