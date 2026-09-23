"""
fetch/near.py — tier 2: NEAR's staked supply, read from a NEAR node.

locked_tokens for NEAR is the total delegated to validators, and the only place that number
exists whole is the chain: the `validators` JSON-RPC method returns every current validator with
its stake, INCLUDING delegations, and summing them is the figure. No aggregator publishes it and
no contract holds it — NEAR's staking is protocol-level, spread across one staking pool contract
per validator, so there is no single balance to read.

** THE SCALING IS SOURCED, NOT ASSUMED. ** `stake` is a decimal string in yoctoNEAR, NEAR's
smallest unit, and the exponent is 24 — not the EVM's 18. Assuming 18 would report a genuine
600m NEAR stake as 600 billion, and assuming 24 where it was 18 would report it as 600. The
exponent comes from NEAR's own SDK (near/near-api-js, NEAR_NOMINATION_EXP = 24) with the URL and
read date on the config entry, exactly as the Hyperliquid stake read is held by spotMeta rather
than by a guess.

Endpoint, method, response path, field and exponent all live in config under the project's
node_api block, so a node API change is a config edit rather than a code change.
"""
from __future__ import annotations

import logging

import config

from .base import Http, json_path_get, parse_number, point, today

log = logging.getLogger("token_metrics.fetch.near")

SOURCE = "near_rpc"
TIER = 2
KIND = "near_validators"


class NearNode:
    """Sums validator stake from a NEAR node for any project declaring a near_validators block."""

    def __init__(self, prior_values: dict | None = None):
        self.http = Http(min_interval=0.5)
        self.prior = prior_values or {}

    def _call(self, api: dict, body: dict | None = None) -> tuple[object, str]:
        """Try each configured endpoint in turn. Returns (payload, detail); payload None on failure."""
        body = body or {"jsonrpc": "2.0", "id": "token-metrics",
                        "method": api.get("method", "validators"),
                        "params": api.get("params", [None])}
        errors = []
        for url in api.get("endpoints") or []:
            try:
                payload = self.http.post(url, json_body=body)
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                errors.append(f"{url}: {e}")
                continue
            # A JSON-RPC ERROR IS A 200. The transport succeeded and the call did not, and
            # treating that as a payload would walk the path into None and report "no validators
            # carried a stake" for what is actually a rejected request.
            if isinstance(payload, dict) and payload.get("error"):
                errors.append(f"{url}: JSON-RPC error {payload['error']}")
                continue
            return payload, url
        return None, "; ".join(errors) or "no endpoints configured"

    @staticmethod
    def _sum_stake(payload, api: dict) -> tuple[float | None, int, str]:
        """Sum the stake field across the validator list. Returns (raw_yocto, count, detail)."""
        path = api.get("list_path", "result.current_validators")
        field = api.get("sum_field", "stake")
        rows = json_path_get(payload, path)
        if not isinstance(rows, list):
            return None, 0, f"{path!r} is not a list in the response (got {type(rows).__name__})"
        raw, n, missing = 0.0, 0, 0
        for entry in rows:
            if not isinstance(entry, dict):
                continue
            # ** parse_number, NOT float(): ** stake is a 26-digit decimal STRING. float() would
            # work here and json.loads would not have made it an int, but the same field on
            # another node build could arrive with separators, and a silent ValueError inside a
            # sum is how a validator quietly stops counting.
            v = parse_number(entry.get(field))
            if v is None:
                missing += 1
                continue
            raw += v
            n += 1
        if not n:
            return None, 0, (f"no validator carried a numeric {field!r} "
                             f"({len(rows)} entries, {missing} without it)")
        return raw, n, (f"{field} summed across {n} validator(s) via {path}"
                        + (f", {missing} without the field" if missing else ""))

    def run(self, projects: list[dict], window_days, out):
        when = today()
        for p in projects:
            api = p.get("node_api") or {}
            if api.get("kind") != KIND:
                continue
            name = p["name"]
            metric = api.get("metric", "locked_tokens")
            supply = self.prior.get((name, "total_supply")) or self.prior.get((name, "circulating_supply"))
            # ===== ACCOUNT BALANCES FROM THE SAME NODE. Added 2026-09-23. =====
            # Run before the validators read, so a failed stake call cannot silently skip them.
            for read in (api.get("extra_reads") or []):
                self._view_accounts(p, api, read, supply, out, when)
            payload, detail = self._call(api)
            if payload is None:
                out.fail(SOURCE, name, f"{metric}: validators call failed — {detail}", TIER)
                out.gap(name, metric,
                        reason=f"the NEAR `validators` RPC call failed: {detail}",
                        tiers_attempted="2",
                        suggestion=("Check the endpoints in this project's node_api block. The "
                                    "call needs no key. Do NOT substitute a staking-pool balance: "
                                    "NEAR runs one pool contract per validator, so any single "
                                    "address is one validator's stake, not the network's."))
                continue

            raw, n, sum_detail = self._sum_stake(payload, api)
            if raw is None:
                out.fail(SOURCE, name, f"{metric}: {sum_detail}", TIER)
                out.gap(name, metric,
                        reason=f"the validators call answered but {sum_detail}",
                        tiers_attempted="2",
                        suggestion=("Check list_path and sum_field in this project's node_api "
                                    "block against the response shape the node actually returns."))
                continue

            exp = api.get("yocto_exponent")
            if exp is None:
                out.unconfigured(SOURCE, name,
                                 f"{metric}: no yocto_exponent on the node_api block, so the "
                                 f"scaling has no source. Raw sum {raw:,.0f}. Do NOT assume 18 — "
                                 f"NEAR's smallest unit is not the EVM convention.", TIER)
                continue
            value = raw / (10 ** int(exp))

            # ===== THE BOUND IS STRUCTURAL AND IT IS ONE-SIDED. Same shape as the HYPE read. =====
            # Nothing staked can exceed everything in existence, so an exponent too SMALL by
            # orders of magnitude is caught here. An exponent too LARGE is not — 10^30 would turn
            # 600m NEAR into 0.0006 and pass "greater than zero" — and that direction is held by
            # the exponent being SOURCED (near-api-js, on the config entry), not by this gate.
            if supply is None or supply <= 0:
                out.fail(SOURCE, name,
                         f"{metric}: no total_supply in the store to bound the figure against. "
                         f"Raw sum {raw:,.0f}, which at 10^{exp} would be {value:,.4f}. Nothing "
                         f"was stored: an unbounded scaled figure is the one thing this read "
                         f"cannot check about itself.", TIER)
                continue
            if not (0 < value <= supply):
                out.fail(SOURCE, name,
                         f"{metric}: THE SCALED FIGURE FAILS ITS BOUND and nothing was stored: "
                         f"{raw:,.0f} / 10^{exp} = {value:,.4f}, outside (0, total_supply="
                         f"{supply:,.0f}]. Either the exponent changed meaning or {api.get('sum_field', 'stake')} "
                         f"is no longer what it was.", TIER)
                out.gap(name, metric,
                        reason=(f"the summed validator stake scales to {value:,.4f}, which is not "
                                f"inside (0, total_supply={supply:,.0f}]."),
                        tiers_attempted="2",
                        suggestion=(f"Re-read the exponent from NEAR's own SDK "
                                    f"({api.get('exponent_source_url')}, last read "
                                    f"{api.get('exponent_source_date')}) before changing it. Do "
                                    f"NOT pick the exponent that makes the number look right — "
                                    f"that is choosing an answer, not reading one."))
                continue

            src = f"{SOURCE}:validators"
            out.add(point(name, metric, value, src, TIER, when), SOURCE, name,
                    f"{metric}={value:,.4f} — {sum_detail}; {raw:,.0f} / 10^{exp}, inside "
                    f"(0, total_supply={supply:,.0f}]", TIER)

            # ** A PLAUSIBILITY FLOOR IS A JUDGEMENT, SO IT FLAGS RATHER THAN REFUSES. ** There is
            # no structural reason a network cannot have a small staked fraction, so this cannot
            # be a gate — but an exponent too large by orders of magnitude lands here and nowhere
            # else, and silence would be the whole failure.
            floor = api.get("expect_min_share_of_supply")
            if floor and value < supply * float(floor):
                out.review_item(
                    name, metric, "below_expected_share", "stored_flagged",
                    value=value, prior_value=supply * float(floor), date=when,
                    source=src, tier=TIER,
                    basis=(f"{n} validator(s) sum to {value:,.4f}, which is "
                           f"{value / supply:.2%} of total_supply ({supply:,.0f}) against an "
                           f"expected floor of {float(floor):.0%}. STORED ANYWAY: a low staked "
                           f"share is possible and this is a judgement, not a property. It is "
                           f"the one place an exponent too LARGE would show — the structural "
                           f"bound only catches one too small — so check "
                           f"{api.get('exponent_source_url')} before accepting it."))

    def _view_accounts(self, p: dict, api: dict, read: dict, supply, out, when) -> None:
        """Sum native NEAR held by a list of accounts, via `query` / request_type view_account.

        NEAR's docs (api/rpc/contracts, URL on the config read) give the response as
        result.amount — a yoctoNEAR decimal string — beside result.locked, the staked part. The
        same sourced 10^24 exponent as the validators read applies.

        ** A STOCK, NEVER A FLOW. ** The one caller today is NEAR's buyback_fund_balance: the
        three Intents revenue wallets. Their balance is what they hold; it is deliberately NOT
        differenced into actual_buyback_tokens, because a buyback wallet exists to spend and its
        delta is inflow minus spending. That row keeps its own blocked reason.

        ** ALL OR NOTHING. ** A sum with one wallet missing is not a smaller figure, it is a
        wrong one, so a single failed account stores nothing and says which.
        """
        name, metric = p["name"], read.get("metric")
        accounts = read.get("accounts") or []
        if read.get("kind") != "near_view_account" or not metric or not accounts:
            out.unconfigured(SOURCE, name,
                             f"extra read: kind, metric or accounts missing ({read.get('kind')!r})", TIER)
            return
        exp = api.get("yocto_exponent")
        if exp is None:
            out.unconfigured(SOURCE, name,
                             f"{metric}: no yocto_exponent on the node_api block, so the scaling "
                             f"has no source. Nothing was called.", TIER)
            return
        field = read.get("field", "amount")
        raw, parts = 0.0, []
        for acct in accounts:
            body = {"jsonrpc": "2.0", "id": "token-metrics", "method": "query",
                    "params": {"request_type": "view_account", "finality": "final",
                               "account_id": acct}}
            payload, detail = self._call(api, body)
            if payload is None:
                out.fail(SOURCE, name,
                         f"{metric}: view_account {acct} failed — {detail}. NOTHING STORED: a sum "
                         f"missing one account is a wrong figure, not a smaller one.", TIER)
                out.gap(name, metric,
                        reason=f"the NEAR view_account call for {acct} failed: {detail}",
                        tiers_attempted="2",
                        suggestion=("Check the endpoints in this project's node_api block. Every "
                                    "listed account must answer for the sum to be stored."))
                return
            v = parse_number(json_path_get(payload, f"result.{field}"))
            if v is None:
                keys = (", ".join(sorted((payload.get("result") or {}) if isinstance(payload, dict) else []))
                        or type(payload).__name__)
                out.fail(SOURCE, name,
                         f"{metric}: view_account {acct} answered without a numeric result.{field} "
                         f"(keys: {keys}). NOTHING STORED.", TIER)
                out.gap(name, metric,
                        reason=f"view_account {acct} answered but carried no numeric result.{field}",
                        tiers_attempted="2",
                        suggestion=f"Check `field` on the extra read against {read.get('spec_url')}.")
                return
            raw += v
            parts.append(f"{acct}={v / (10 ** int(exp)):,.4f}")
        value = raw / (10 ** int(exp))
        if supply is None or supply <= 0:
            out.fail(SOURCE, name,
                     f"{metric}: no total_supply in the store to bound the figure against. Raw sum "
                     f"{raw:,.0f}, which at 10^{exp} would be {value:,.4f}. Nothing was stored.", TIER)
            return
        if not (0 <= value <= supply):
            out.fail(SOURCE, name,
                     f"{metric}: THE SCALED FIGURE FAILS ITS BOUND and nothing was stored: "
                     f"{raw:,.0f} / 10^{exp} = {value:,.4f}, outside [0, total_supply={supply:,.0f}].", TIER)
            out.gap(name, metric,
                    reason=f"the summed account balance scales to {value:,.4f}, which is not inside "
                           f"[0, total_supply={supply:,.0f}].",
                    tiers_attempted="2",
                    suggestion=(f"Re-read the exponent from NEAR's own SDK "
                                f"({api.get('exponent_source_url')}) before changing it."))
            return
        src = f"{SOURCE}:view_account"
        partial = read.get("partial")
        if partial:
            src = config.mark_source(src, "PARTIAL")
        out.add(point(name, metric, value, src, TIER, when), SOURCE, name,
                f"{metric}={value:,.4f} — native NEAR summed over {len(accounts)} account(s) via "
                f"view_account: {'; '.join(parts)}; inside [0, total_supply={supply:,.0f}]. A STOCK, "
                f"not differenced." + (f" PARTIAL: {partial}" if partial else ""), TIER)
