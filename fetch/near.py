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

import base64
import json
import logging
import re
import time

import pandas as pd

import config

from .base import Http, json_path_get, parse_number, point, tidy, today
from .logcache import LogCache, atomic_write_text

log = logging.getLogger("token_metrics.fetch.near")

SOURCE = "near_rpc"
TIER = 2
KIND = "near_validators"


class NearNode:
    """Sums validator stake from a NEAR node for any project declaring a near_validators block."""

    def __init__(self, prior_values: dict | None = None):
        # ONE RETRY PER ENDPOINT (2026-09-28): _call already fails over across every configured
        # endpoint, so 4 retries each (2+4+8+16s of backoff) spent the source's whole budget on
        # the first dead node before trying the next.
        self.http = Http(min_interval=0.5, retries=1)
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
                if read.get("kind") == "near_block_supply":
                    self._block_supply(p, api, read, out, when)
                elif read.get("kind") == "near_protocol_version":
                    self._protocol_version(p, api, read, out, when)
                elif read.get("kind") == "near_protocol_config":
                    self._protocol_config(p, api, read, out, when)
                else:
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

        # R11.4 / R11.5: AFTER every validators read (a slow first ledger read must not starve the stake read)
        for p in projects:
            api = p.get("node_api") or {}
            if api.get("kind") != KIND:
                continue
            supply = self.prior.get((p["name"], "total_supply")) or self.prior.get((p["name"], "circulating_supply"))
            if api.get("pool_stake"):          # the Foundation set's stake, pair by pair
                self._pool_stake(p, api, api["pool_stake"], supply, out, when)
            if api.get("liquid_set"):          # R12.2: the Foundation set's liquid NEAR, account by account
                self._liquid_set(p, api, api["liquid_set"], supply, out, when)
            if api.get("payout_ledger"):       # the HSP-007 payouts, from House of Stake's published ledger
                self._payout_ledger(p, api["payout_ledger"], out)

    def _block_supply(self, p: dict, api: dict, read: dict, out, when) -> None:
        """NEAR's total supply as the protocol keeps it: `block` header.total_supply. 2026-09-28.

        One call. yoctoNEAR (the same sourced 10^24 exponent as the validators read). The header
        is NET of burn — rewards are minted into it and burnt gas destroyed out of it — so the
        issuance derivation adds the burn back (config issuance_supply_metric)."""
        name, metric = p["name"], read["metric"]
        body = {"jsonrpc": "2.0", "id": "token-metrics", "method": "block",
                "params": {"finality": "final"}}
        payload, detail = self._call(api, body)
        raw = json_path_get(payload, read["path"]) if payload is not None else None
        value = parse_number(raw)
        if value is None:
            out.fail(SOURCE, name, f"{metric}: block {read['path']} not readable — {detail}", TIER)
            out.gap(name, metric, reason=f"NEAR `block` did not return {read['path']}: {detail}",
                    tiers_attempted="2", suggestion=f"Check the endpoints; spec {read.get('spec_url')}")
            return
        value = value / 10 ** int(api.get("yocto_exponent", 24))
        height = json_path_get(payload, "result.header.height")
        out.add(point(name, metric, value, f"{SOURCE}:block.header.total_supply", TIER, when),
                SOURCE, name, f"{metric}={value:,.4f} (block {height}, {detail})", TIER)

    def _protocol_version(self, p: dict, api: dict, read: dict, out, when) -> None:
        """Mainnet's protocol version from `status`, once per run (Jake, 2026-10-06): the burn's share switches from
        0.70 to 1.00 on protocol v87 (nearcore 2.14.0, HSP-027), and this reading is how the switch is dated."""
        name, metric = p["name"], read["metric"]
        body = {"jsonrpc": "2.0", "id": "token-metrics", "method": "status", "params": []}
        payload, detail = self._call(api, body)
        v = parse_number(json_path_get(payload, "result.protocol_version")) if payload is not None else None
        if v is None:
            out.fail(SOURCE, name, f"{metric}: `status` gave no protocol_version — {detail}", TIER)
            return
        height = json_path_get(payload, "result.sync_info.latest_block_height")
        out.add(point(name, metric, float(v), f"{SOURCE}:status.protocol_version", TIER, when), SOURCE, name,
                f"{metric}={int(v)} at block {height} — the burn is 100% of gas from v87, 70% before", TIER)

    def _protocol_config(self, p: dict, api: dict, read: dict, out, when) -> None:
        """One protocol parameter that is a [numerator, denominator] pair, once per run (R10.B5a): mainnet's
        max_inflation_rate from EXPERIMENTAL_protocol_config — 1/40 = 2.5% a year. It DATES a change of the issuance
        ceiling (HSP-029 is pending); no figure reads it. Anything but two numbers with a positive denominator stores
        nothing."""
        name, metric = p["name"], read["metric"]
        body = {"jsonrpc": "2.0", "id": "token-metrics", "method": "EXPERIMENTAL_protocol_config",
                "params": {"finality": "final"}}
        payload, detail = self._call(api, body)
        raw = json_path_get(payload, read["path"]) if payload is not None else None
        try:
            num, den = (float(x) for x in raw)
            value = num / den if den > 0 else None
        except (TypeError, ValueError):
            value = None
        if value is None:
            out.fail(SOURCE, name, f"{metric}: EXPERIMENTAL_protocol_config gave no [numerator, denominator] at "
                                   f"{read['path']} — {detail}; nothing stored", TIER)
            return
        ver = json_path_get(payload, "result.protocol_version")
        out.add(point(name, metric, value, f"{SOURCE}:protocol_config.{read['path'].split('.')[-1]}", TIER, when),
                SOURCE, name, f"{metric}={value:.6f} ({int(num)}/{int(den)}) at protocol {ver}", TIER)

    def _pool_stake(self, p: dict, api: dict, read: dict, supply, out, when) -> None:
        """R11.4: NEAR staked by a declared set of delegators — for each (owner, delegator, pool) pair the staking
        pool's own `get_account(delegator)` (a view call, `query` / call_function), its `staked_balance` summed. The
        unstaked balance left in a pool is not staked and is not counted. ALL PAIRS OR NOTHING: a pair that does not
        answer with a number stores nothing (a sum missing one pair is a wrong figure, not a smaller one)."""
        name, metric, pairs = p["name"], read.get("metric"), read.get("pairs") or ()
        exp = api.get("yocto_exponent")
        if not metric or not pairs or exp is None:
            out.unconfigured(SOURCE, name, f"pool stake: metric, pairs or yocto_exponent missing ({metric!r})", TIER)
            return
        field, raw, idle, top = read.get("field", "staked_balance"), 0, 0, []
        for _owner, who, pool in pairs:
            args = base64.b64encode(json.dumps({"account_id": who}).encode()).decode()
            body = {"jsonrpc": "2.0", "id": "token-metrics", "method": "query",
                    "params": {"request_type": "call_function", "finality": "final", "account_id": pool,
                               "method_name": read.get("method", "get_account"), "args_base64": args}}
            payload, detail = self._call(api, body)
            try:
                acct = json.loads(bytes(json_path_get(payload, "result.result")).decode())
                v = int(acct[field])
                idle += int(acct.get("unstaked_balance") or 0)
            except (TypeError, ValueError, KeyError):
                out.fail(SOURCE, name, f"{metric}: {pool} get_account({who}) gave no numeric {field} — {detail}. "
                                       f"NOTHING STORED: a sum missing one pair is a wrong figure.", TIER)
                out.gap(name, metric, reason=f"the staking pool {pool} did not answer get_account for {who}: {detail}",
                        tiers_attempted="2",
                        suggestion="Check the pair in config.NEAR_FOUNDATION_STAKE_PAIRS and the node_api endpoints.")
                return
            raw += v
            top.append((v, who, pool))
        scale = 10 ** int(exp)
        value = raw / scale
        if supply is None or supply <= 0 or not (0 <= value <= supply):
            out.fail(SOURCE, name, f"{metric}: {value:,.4f} is not inside [0, total_supply={supply}] — nothing stored",
                     TIER)
            return
        top.sort(reverse=True)
        out.add(point(name, metric, value, f"{SOURCE}:pool_stake", TIER, when), SOURCE, name,
                f"{metric}={value:,.4f} — staked_balance summed over {len(pairs)} (delegator, pool) pair(s); largest: "
                + "; ".join(f"{w[:28]} @ {pl} {v / scale:,.2f}" for v, w, pl in top[:3])
                + f"; {idle / scale:,.2f} NEAR unstaked in the pools, not counted. The set holds INFERRED wallets "
                  f"({read.get('set', 'config')}).", TIER)

    def _liquid_set(self, p: dict, api: dict, read: dict, supply, out, when) -> None:
        """R12.2 (Jake 2026-11-19): the LIQUID NEAR of a declared account set — `view_account` on every account, its
        `amount` (yoctoNEAR, a decimal string; `locked` is validator stake and is not liquid) summed in whole yocto.
        ALL ACCOUNTS OR NOTHING: an account that does not answer with a number, or a read that runs past its own
        budget, stores nothing and says how far it got — unknown is never 0, and a sum missing one account is a wrong
        figure. The set is ~190 accounts: the client's pacing is shortened for this read only (min_interval_s), and
        the read's own endpoint list is taken in turn, one call each (a public node answers a burst with HTTP 429)."""
        name, metric, accounts = p["name"], read.get("metric"), list(read.get("accounts") or ())
        exp = api.get("yocto_exponent")
        if not metric or not accounts or exp is None:
            out.unconfigured(SOURCE, name, f"liquid set: metric, accounts or yocto_exponent missing ({metric!r})", TIER)
            return
        if len(set(accounts)) != len(accounts):
            out.fail(SOURCE, name, f"{metric}: the account list names an account twice — nothing read", TIER)
            return
        pace = getattr(self.http, "min_interval", None)
        if pace is not None:
            self.http.min_interval = min(float(pace), float(read.get("min_interval_s", 0.15)))
        try:
            self._liquid_set_read(p, api, read, supply, out, when, accounts, int(exp))
        finally:
            if pace is not None:
                self.http.min_interval = pace

    def _liquid_set_read(self, p, api, read, supply, out, when, accounts, exp) -> None:
        name, metric = p["name"], read["metric"]
        stop = time.monotonic() + float(read.get("budget_s", 75))
        raw, top = 0, []
        for i, acct in enumerate(accounts):
            if time.monotonic() > stop:
                out.fail(SOURCE, name, f"{metric}: {i} of {len(accounts)} accounts read inside "
                                       f"{read.get('budget_s', 75)} s — NOTHING STORED (all accounts or nothing; the "
                                       f"next run reads again)", TIER)
                out.gap(name, metric, reason=f"the set's balances were not all read inside the read's budget "
                                             f"({i} of {len(accounts)})", tiers_attempted="2",
                        suggestion="Run `python token_metrics.py --project Near` again; the node rate-limits bursts.")
                return
            body = {"jsonrpc": "2.0", "id": "token-metrics", "method": "query",
                    "params": {"request_type": "view_account", "finality": "final", "account_id": acct}}
            eps = list(read.get("endpoints") or api.get("endpoints") or ())
            k = i % len(eps) if eps else 0
            payload, detail = self._call(dict(api, endpoints=eps[k:] + eps[:k]), body)
            try:
                v = int(str(json_path_get(payload, "result.amount")))
            except (TypeError, ValueError):
                out.fail(SOURCE, name, f"{metric}: view_account {acct} gave no numeric result.amount — {detail}. "
                                       f"NOTHING STORED: a sum missing one account is a wrong figure.", TIER)
                out.gap(name, metric, reason=f"the NEAR view_account call for {acct} failed: {detail}",
                        tiers_attempted="2",
                        suggestion="Check the account in config.NEAR_FOUNDATION_LIQUID_ACCOUNTS and the endpoints.")
                return
            if v < 0:
                out.fail(SOURCE, name, f"{metric}: view_account {acct} gave a negative amount — nothing stored", TIER)
                return
            raw += v
            top.append((v, acct))
        scale = 10 ** int(exp)
        value = raw / scale
        if supply is None or supply <= 0 or not (0 <= value <= supply):
            out.fail(SOURCE, name, f"{metric}: {value:,.4f} is not inside [0, total_supply={supply}] — nothing stored",
                     TIER)
            return
        top.sort(reverse=True)
        out.add(point(name, metric, value, f"{SOURCE}:liquid_set", TIER, when), SOURCE, name,
                f"{metric}={value:,.4f} — liquid NEAR (view_account amount) summed over {len(accounts)} account(s); "
                f"largest: " + "; ".join(f"{a} {v / scale:,.2f}" for v, a in top[:3])
                + f". {read.get('set', '')}", TIER)

    _TX = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{43,44}(?![1-9A-HJ-NP-Za-km-z])")

    @classmethod
    def ledger_hashes(cls, text: str) -> list:
        """The MAINNET transaction hashes a payout ledger publishes: an explorer link (…/txns/<hash>) or a bare hash in
        a table cell. A hash linked on a testnet explorer is not a mainnet payment and is left out."""
        testnet = set(re.findall(r"testnet\.[a-z.]*/txns/([1-9A-HJ-NP-Za-km-z]{43,44})", text))
        out = []
        for m in re.finditer(r"/txns/([1-9A-HJ-NP-Za-km-z]{43,44})|\|\s*`?([1-9A-HJ-NP-Za-km-z]{43,44})`?\s*(?=\|)", text):
            h = m.group(1) or m.group(2)
            if h and cls._TX.fullmatch(h) and h not in testnet and h not in out:
                out.append(h)
        return out

    def _ledger_tx(self, spec: dict, h: str) -> dict | None:
        """One published hash, read on chain: {day, payer, to, near} — or {"refused": why} for a transaction that is
        not a successful payment by a declared payer. None when the node did not answer (retried next run)."""
        rpc = spec["rpc"]
        body = {"jsonrpc": "2.0", "id": "token-metrics", "method": "EXPERIMENTAL_tx_status",
                "params": {"tx_hash": h, "sender_account_id": spec["payers"][0], "wait_until": "NONE"}}
        try:
            res = (self.http.post(rpc, json_body=body) or {}).get("result")
        except Exception:  # noqa: BLE001 — a failed read is retried on the next run
            return None
        if not isinstance(res, dict) or "transaction" not in res:
            return None
        tx = res["transaction"]
        if not isinstance(res.get("status"), dict) or "SuccessValue" not in res["status"]:
            return {"refused": "the transaction did not succeed"}
        if tx.get("signer_id") not in spec["payers"]:
            return {"refused": f"signed by {tx.get('signer_id')}, not a declared payer"}
        skip, best = set(spec.get("forwarders") or ()), (0, None)
        for rc in res.get("receipts") or []:
            for a in ((rc.get("receipt") or {}).get("Action") or {}).get("actions") or []:
                dep = int((a.get("Transfer") or {}).get("deposit") or 0) if isinstance(a, dict) else 0
                if dep > best[0] and rc.get("receiver_id") not in skip and rc.get("receiver_id") != tx["signer_id"]:
                    best = (dep, rc.get("receiver_id"))
        if not best[0]:
            return {"refused": "no NEAR transfer lands on a payee"}
        try:
            blk = self.http.post(rpc, json_body={"jsonrpc": "2.0", "id": "token-metrics", "method": "block", "params": {
                "block_id": res["transaction_outcome"]["block_hash"]}})
            ts = int(blk["result"]["header"]["timestamp"])
        except Exception:  # noqa: BLE001
            return None
        return {"day": str(pd.Timestamp(ts, unit="ns").date()), "payer": tx["signer_id"], "to": best[1],
                "near": best[0] / 10 ** int(spec.get("yocto_exponent", 24))}

    def _payout_ledger(self, p: dict, spec: dict, out) -> None:
        """R11.5: the HSP-007 payouts to the MPC node operators = every transaction House of Stake's monthly Payout
        Execution Reports publish (the forum thread's raw text), each READ ON CHAIN: it succeeded, a declared payer
        signed it, and the NEAR that lands on the payee is the amount, on its block's UTC day. A resolved hash is
        cached (a final transaction does not change). A day inside the ledger's span without a payment is 0 — the
        ledger lists every payment made; a day after the last report's date holds no row (unknown until the next
        report). Nothing is stored while a published hash is still unread (a partial ledger reads low)."""
        name, metric = p["name"], spec["metric"]
        pages = []
        try:
            for page in range(1, int(spec.get("max_pages", 30)) + 1):
                t = self.http.get_text(spec["thread_raw"], params={"page": page})
                if not (t or "").strip():
                    break
                pages.append(t)
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            out.fail(SOURCE, name, f"{metric}: the payout ledger ({spec['thread']}) did not read: {e} — nothing stored",
                     TIER)
            return
        text = "\n".join(pages)
        hashes = self.ledger_hashes(text)
        marks = re.findall(rf"{re.escape(spec['report_marker'])}\W+([A-Z][a-z]+ \d{{1,2}}, \d{{4}})", text)
        dated = pd.to_datetime(pd.Series(marks, dtype=str), errors="coerce").dropna()
        if not hashes or dated.empty:
            out.fail(SOURCE, name, f"{metric}: {spec['thread']} answered {len(text):,} characters with {len(hashes)} "
                                   f"transaction hash(es) and {len(dated)} report date(s) — nothing stored", TIER)
            return
        cache_f = LogCache().root / f"near-payout-ledger-{metric}.json"
        try:
            cache = json.loads(cache_f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        deadline, todo = time.monotonic() + float(spec.get("budget_s", 60)), [h for h in hashes if h not in cache]
        for i, h in enumerate(todo, 1):
            if time.monotonic() > deadline:
                break
            got = self._ledger_tx(spec, h)
            if got is not None:
                cache[h] = got
            if i % 10 == 0 or i == len(todo):          # kept as it goes: a run cut short resumes where it stopped
                atomic_write_text(cache_f, json.dumps(cache, indent=0, sort_keys=True))
        if todo:
            atomic_write_text(cache_f, json.dumps(cache, indent=0, sort_keys=True))
        left = [h for h in hashes if h not in cache]
        if left:
            out.fail(SOURCE, name, f"{metric}: {len(left)} of the ledger's {len(hashes)} transaction(s) are not read on "
                                   f"chain yet (resumes next run) — NOTHING STORED: a partial ledger reads low", TIER)
            return
        paid, refused = {}, []
        for h in hashes:
            r = cache[h]
            if "refused" in r:
                refused.append(f"{h[:10]}… {r['refused']}")
                continue
            d = pd.Timestamp(r["day"])
            paid[d] = paid.get(d, 0.0) + float(r["near"])
        since = pd.Timestamp(spec["from"])
        last = min(dated.max().normalize(), today() - pd.Timedelta(days=1))
        if not paid or last < since:
            out.fail(SOURCE, name, f"{metric}: no payment of the ledger was read as a payment — nothing stored; "
                                   f"refused: {'; '.join(refused) or 'none'}", TIER)
            return
        pts = [(d, paid.get(d, 0.0)) for d in pd.date_range(min(since, min(paid)), max(last, max(paid)))]
        out.add(tidy(pts, name, metric, f"{SOURCE}:payout_ledger", TIER), SOURCE, name,
                f"{metric}: {sum(paid.values()):,.2f} NEAR in {len(hashes) - len(refused)} payout(s) "
                f"{min(paid).date()}..{max(paid).date()}, each read on chain; the ledger's last payout report is dated "
                f"{dated.max().date()} — no row after {last.date()}"
                + (f"; NOT COUNTED (named): {'; '.join(refused)}" if refused else ""), TIER)

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
        # amount + locked where the read names both (lockup.near / contributors.near, as NEAR's lakehouse sums them)
        fields = tuple(read.get("fields") or (read.get("field", "amount"),))
        field = "+".join(fields)
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
            got = [parse_number(json_path_get(payload, f"result.{f}")) for f in fields]
            v = None if any(x is None for x in got) else sum(got)
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
