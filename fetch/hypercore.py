"""
fetch/hypercore.py — Hyperliquid's own public info API.

The Assistance Fund balance is the cumulative HYPE burn: since the December 2025 validator vote,
HYPE held there is recognised as permanently burned, and the address has never had a private key
so nothing can leave.

HYPE on HyperCore is not an ERC-20 on any chain the EVM adapter covers, so that route was never
going to work. Hyperliquid publishes the balance through its own documented info endpoint instead:
a plain HTTPS POST, no RPC, no key, no chain. This REPLACES the contract-read approach rather than
supplementing it, which is why Hyperliquid declares no contracts at all.

    POST https://api.hyperliquid.xyz/info
    {"type": "spotClearinghouseState", "user": "0xfefe...fefe"}

Documented at hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint. Tier 1:
a free unauthenticated HTTP API, the same class as DefiLlama and CoinGecko.

The response shape is read through config rather than hardcoded, so a change to it is a config
edit and not a code change.
"""
from __future__ import annotations

import logging

import config

from .base import Http, derive_flow_from_cumulative, json_path_get, parse_number, point, today

log = logging.getLogger("token_metrics.fetch.hypercore")

SOURCE = "hypercore_info"
TIER = 1
KIND = "hypercore_info"


class HyperCoreInfo:
    """Reads a balance from Hyperliquid's info endpoint for any project declaring this kind."""

    def __init__(self, prior_values: dict | None = None, prior_dates: dict | None = None,
                 prior_delta: dict | None = None):
        self.http = Http(min_interval=0.5)
        self.prior = prior_values or {}
        self.prior_dates = prior_dates or {}
        # THE VALUE TO SUBTRACT AND THE DATE IT CARRIES MUST COME FROM THE SAME ROW.
        # prior_values is latest_values() — the newest figure of ANY date, which on a same-day
        # re-run is THIS MORNING'S. prior_dates is values_before(today) — the last EARLIER-dated
        # row. Differencing one against the other's date passes the same-date guard (the date is
        # yesterday's) while subtracting today's number, so run 2 of a day reports only the
        # increment since run 1 and overwrites the full day's flow on the (date, project, metric)
        # key. That is the PancakeSwap fault recorded in store.values_before, and it was fixed for
        # the chain adapter and left in place here. prior_delta is values_before's value, so the
        # two now agree and a same-day re-run is idempotent.
        self.prior_delta = prior_delta if prior_delta is not None else (prior_values or {})
        # spotMeta answers the same for every read in a run, so it is fetched once.
        self._decimals_cache: tuple | None = None

    @staticmethod
    def _pick_balance(payload, api: dict) -> tuple[float | None, str]:
        """Find the balance for the configured coin in the response.

        spotClearinghouseState returns a list of balances, one per asset. The entry is matched on
        its coin symbol, and the amount is read from the first configured field that carries a
        number — so a renamed field is a config edit, not a code change.
        """
        coin = str(api.get("coin", "HYPE"))
        list_path = api.get("balances_path", "balances")
        coin_key = api.get("coin_key", "coin")
        amount_keys = api.get("amount_keys") or ["total", "balance", "amount"]

        balances = json_path_get(payload, list_path)
        if not isinstance(balances, list):
            return None, f"{list_path!r} is not a list in the response (got {type(balances).__name__})"
        available = []
        for entry in balances:
            if not isinstance(entry, dict):
                continue
            available.append(str(entry.get(coin_key)))
            if str(entry.get(coin_key)).upper() != coin.upper():
                continue
            for key in amount_keys:
                value = parse_number(entry.get(key))
                if value is not None:
                    return value, f"{coin} via {list_path}[{coin_key}={coin}].{key}"
            return None, f"{coin} found but none of {amount_keys} held a number: {entry}"
        return None, f"{coin} not present. Coins returned: {', '.join(available[:12]) or 'none'}"

    # ===== SCALING COMES FROM THE PROTOCOL'S OWN METADATA, NOT FROM A GUESS. Added 2026-09-23. =====
    # The two info endpoints this adapter reads do NOT agree on units. spotClearinghouseState
    # returns `total` as a decimal string in human units ("47387407.0"); validatorSummaries
    # returns `stake` as an integer in the token's smallest unit. And HyperCore's decimals are
    # NOT the EVM convention, so assuming 18 would report a real 400m HYPE stake as 0.0004 — a
    # number that reads as a rounding error rather than as a fault.
    #
    # ** AN EARLIER VERSION INFERRED THE DIVISOR BY TESTING CANDIDATES AGAINST A SUPPLY BAND, and
    # its own test killed it. ** With a bound of (0, 955m], three divisors pass for a genuine
    # 400m stake — 10^8 gives 400m, 10^9 gives 40m, 10^18 gives 0.04, and all are "under supply
    # and above zero". A rule requiring exactly one match would have REFUSED a perfectly good
    # read, and loosening it to "pick the largest" would have been choosing a number to make the
    # answer look right. Neither is a measurement.
    #
    # SO THE DECIMALS ARE READ FROM spotMeta, which is where Hyperliquid publishes them, and the
    # supply band is kept as a GATE rather than a discriminator: it cannot tell 10^8 from 10^9,
    # but it will catch a scaled figure that exceeds everything in existence. Sourced answer
    # first, bound second — the same order as everywhere else in this book.
    def _token_decimals(self, api: dict, read: dict) -> tuple[int | None, str]:
        """HYPE's weiDecimals, from Hyperliquid's own spot metadata. Cached for the run."""
        declared = read.get("wei_decimals")
        if declared is not None:
            return int(declared), f"wei_decimals={declared} declared in config"
        spec = read.get("decimals_from") or {}
        if not spec:
            return None, ("no wei_decimals in config and no decimals_from block to read them "
                          "with, so the scaling has no source")
        if self._decimals_cache is not None:
            return self._decimals_cache
        endpoint = spec.get("endpoint") or api.get("endpoint")
        try:
            meta = self.http.post(endpoint, json_body=spec.get("request") or {"type": "spotMeta"})
        except Exception as e:  # noqa: BLE001
            self._decimals_cache = (None, f"{spec.get('request', {}).get('type')} call failed: {e}")
            return self._decimals_cache
        rows = json_path_get(meta, spec.get("list_path", "tokens"))
        if not isinstance(rows, list):
            self._decimals_cache = (None, f"{spec.get('list_path', 'tokens')!r} is not a list in "
                                          f"the metadata response")
            return self._decimals_cache
        want = str(spec.get("name", "HYPE")).upper()
        seen = []
        for entry in rows:
            if not isinstance(entry, dict):
                continue
            seen.append(str(entry.get(spec.get("name_key", "name"))))
            if str(entry.get(spec.get("name_key", "name"))).upper() != want:
                continue
            d = entry.get(spec.get("decimals_key", "weiDecimals"))
            if d is None:
                self._decimals_cache = (None, f"{want} found in the metadata but carries no "
                                              f"{spec.get('decimals_key', 'weiDecimals')!r}")
                return self._decimals_cache
            self._decimals_cache = (int(d), f"weiDecimals={int(d)} read from "
                                            f"{spec.get('request', {}).get('type')} for {want}")
            return self._decimals_cache
        self._decimals_cache = (None, f"{want} not in the metadata. Tokens: "
                                      f"{', '.join(seen[:12]) or 'none'}")
        return self._decimals_cache

    @staticmethod
    def _apply_scale(raw: float, decimals: int, supply: float | None) -> tuple[float | None, str]:
        """Scale, then GATE against the token's own supply. Returns (value, detail).

        The gate is the right shape and it is ASYMMETRIC, which is worth stating plainly.
        Nothing staked can exceed everything in existence, so the UPPER bound is structural and
        catches a divisor that is too small by orders of magnitude. The lower bound is only
        "greater than zero", because there is no structural floor: "a real network stakes at
        least X% of supply" is a judgement, not a property. So a divisor that is too LARGE —
        10^18 turning a 400m stake into 0.04 — passes this gate, and is held by the metadata
        being right rather than by the bound. Recorded so nobody later reads the gate as
        protection it does not give.
        """
        value = raw / (10 ** decimals)
        if supply is None or supply <= 0:
            return None, (f"no total_supply in the store to gate the figure against. Raw sum "
                          f"{raw:,.0f}, which at 10^{decimals} would be {value:,.4f}")
        if not (0 < value <= supply):
            return None, (f"THE SCALED FIGURE FAILS ITS BOUND and nothing was stored: "
                          f"{raw:,.0f} / 10^{decimals} = {value:,.4f}, which is outside "
                          f"(0, total_supply={supply:,.0f}]. Nothing staked can exceed everything "
                          f"in existence, so either the decimals changed meaning or the field is "
                          f"not what it was")
        return value, (f"{raw:,.0f} / 10^{decimals} = {value:,.4f}, inside "
                       f"(0, total_supply={supply:,.0f}]")

    def _sum_field(self, payload, api: dict, read: dict, supply: float | None) -> tuple[float | None, dict, str]:
        """Sum one numeric field across a list response — validatorSummaries' total delegated HYPE.

        ** JAILED AND INACTIVE VALIDATORS COUNT. ** Delegated HYPE is locked whatever the
        validator's status: a jailed validator's delegators cannot withdraw any faster than
        anyone else's, so excluding them would understate locked supply by whatever is delegated
        to the ones currently in trouble — which is exactly when that number moves. The
        active-only figure is captured beside it as a staged row, because it answers a different
        question (how much stake is securing the chain right now) and is not this metric.
        """
        field = read.get("sum_field", "stake")
        rows = json_path_get(payload, read.get("list_path", "")) if read.get("list_path") else payload
        if not isinstance(rows, list):
            return None, {}, f"expected a list of validators, got {type(rows).__name__}"
        raw, active_raw, n, n_active, missing = 0.0, 0.0, 0, 0, 0
        for entry in rows:
            if not isinstance(entry, dict):
                continue
            v = parse_number(entry.get(field))
            if v is None:
                missing += 1
                continue
            raw += v
            n += 1
            jailed = bool(entry.get(read.get("jailed_key", "isJailed")))
            if not jailed:
                active_raw += v
                n_active += 1
        if not n:
            return None, {}, (f"no validator carried a numeric {field!r} "
                              f"({len(rows)} entries, {missing} without it)")
        decimals, dec_detail = self._token_decimals(api, read)
        if decimals is None:
            return None, {}, (f"{field} summed across {n} validator(s) to {raw:,.0f}, but the "
                              f"scaling has no source: {dec_detail}")
        value, detail = self._apply_scale(raw, decimals, supply)
        extra = {"validators": n, "active": n_active, "missing_field": missing,
                 "raw": raw, "active_raw": active_raw, "decimals": decimals}
        return value, extra, (f"{field} summed across {n} validator(s), {n_active} not jailed; "
                              f"{dec_detail}; {detail}")

    def run(self, projects: list[dict], window_days, out):
        when = today()
        for p in projects:
            api = p.get("node_api") or {}
            if api.get("kind") != KIND:
                continue
            name = p["name"]
            metric = api.get("metric", "burn_address_balance")
            endpoint = api.get("endpoint")
            request = api.get("request") or {}
            if not endpoint or not request:
                out.unconfigured(SOURCE, name, f"{metric}: endpoint or request body missing in config", TIER)
                continue
            try:
                payload = self.http.post(endpoint, json_body=request)
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                out.fail(SOURCE, name, f"{metric}: {endpoint}: {e}", TIER)
                out.gap(name, metric, reason=f"Hyperliquid info API call failed: {e}", tiers_attempted="1",
                        suggestion=f"Check {endpoint} is reachable. It needs no key and no RPC; if it is down, "
                                   f"the figure is unavailable rather than wrong.")
                continue

            value, detail = self._pick_balance(payload, api)
            if value is None:
                out.fail(SOURCE, name, f"{metric}: {detail}", TIER)
                out.gap(name, metric, reason=f"Hyperliquid info API returned no usable balance: {detail}",
                        tiers_attempted="1",
                        suggestion="Check balances_path, coin_key and amount_keys in config against the "
                                   "documented response shape at hyperliquid.gitbook.io.")
                continue

            src = f"{SOURCE}:{api.get('request', {}).get('type', 'info')}"
            out.add(point(name, metric, value, src, TIER, when), SOURCE, name,
                    f"{metric}={value:,.4f} via {detail}", TIER)
            flow_metric = api.get("derive_flow_metric")
            if flow_metric:
                flow = derive_flow_from_cumulative(value, self.prior_delta.get((name, metric)), name,
                                                   flow_metric, config.mark_source(src, "delta"), TIER, when,
                                                   prior_date=self.prior_dates.get((name, metric)),
                                                   stock_metric=metric, out=out)
                if not flow.empty:
                    out.add(flow, SOURCE, name, f"{flow_metric} derived from the {metric} delta", TIER)

            # ===== EXTRA READS FROM THE SAME ENDPOINT. Added 2026-09-23. =====
            # One project, one endpoint, several questions. validatorSummaries is the aggregate
            # staking figure the official SDKs do not expose — every staking type in both of them
            # takes a `user` address, which is why this was refused on 2026-09-23 and is wired
            # now that the aggregate endpoint is on file.
            for read in (api.get("extra_reads") or []):
                self._extra_read(p, api, read, out, when)

    def _extra_read(self, project: dict, api: dict, read: dict, out, when) -> None:
        """One additional request against the same info endpoint, with its own shape."""
        name, metric = project["name"], read.get("metric")
        endpoint = read.get("endpoint") or api.get("endpoint")
        request = read.get("request") or {}
        if not metric or not endpoint or not request:
            out.unconfigured(SOURCE, name, f"extra read: metric, endpoint or request missing", TIER)
            return
        try:
            payload = self.http.post(endpoint, json_body=request)
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            out.fail(SOURCE, name, f"{metric}: {endpoint} {request.get('type')}: {e}", TIER)
            out.gap(name, metric,
                    reason=f"Hyperliquid info API call for {request.get('type')!r} failed: {e}",
                    tiers_attempted="1",
                    suggestion=f"Check {endpoint} is reachable. It needs no key and no RPC.")
            return

        shape = read.get("shape", "sum_field")
        if shape != "sum_field":
            out.unconfigured(SOURCE, name, f"{metric}: unknown extra-read shape {shape!r}", TIER)
            return

        supply = self.prior.get((name, "total_supply")) or self.prior.get((name, "circulating_supply"))
        value, extra, detail = self._sum_field(payload, api, read, supply)
        if value is None:
            # REFUSED, WITH THE RAW FIGURE AND THE WHOLE CANDIDATE TABLE. A scaling that cannot
            # be established is not a missing source: the data arrived and the units are the open
            # question, so the row says the number and what each divisor would make of it.
            out.fail(SOURCE, name, f"{metric}: {detail}", TIER)
            out.gap(name, metric, reason=f"{request.get('type')} returned data but {detail}",
                    tiers_attempted="1",
                    suggestion="Confirm the token's smallest-unit decimals from the response "
                               "itself and set wei_decimals on this read in config.py. Do NOT "
                               "assume 18: HyperCore decimals are not the EVM convention.")
            return

        src = f"{SOURCE}:{request.get('type', 'info')}"
        out.add(point(name, metric, value, src, TIER, when), SOURCE, name,
                f"{metric}={value:,.4f} — {detail}", TIER)

        # THE ACTIVE-ONLY FIGURE, BESIDE IT AND NOT INSTEAD OF IT. Staged rather than stored as a
        # metric: it answers a different question (how much stake is securing the chain now) and
        # nothing in the book has chosen it for anything. Staging is exactly the third option
        # between discarding it and letting it silently drive a column.
        if extra.get("decimals") is not None and extra.get("active_raw") is not None:
            active = extra["active_raw"] / (10 ** extra["decimals"])
            out.stage(name, f"{metric}_active_validators_only", active, date=when, source=src,
                      tier=TIER,
                      note=(f"{extra['active']} of {extra['validators']} validators not jailed. "
                            f"{metric} DELIBERATELY includes jailed and inactive validators: "
                            f"delegated HYPE is locked whatever the validator's status, and a "
                            f"jailed validator's delegators cannot withdraw any faster than "
                            f"anyone else's. Excluding them would understate locked supply by "
                            f"whatever is delegated to the validators currently in trouble — "
                            f"which is precisely when that number moves."))
