"""
fetch/circulating.py — circulating supply measured on-chain. M, Jake 2026-09-30.

    circulating = on-chain total supply − the DOCUMENTED non-circulating set

The set is config.CIRCULATING_ONCHAIN[project]["subtract"]: stored balance metrics, each fed by
contract entries that carry their own address, source_url and verified date. exclusions() turns
that into the per-address list the report prints, so the list shown is always the one used.

A day is computed only where the total AND every subtracted balance were read that day: a
missing balance would make the figure read high by exactly that balance, silently.

CoinGecko's circulating_supply is the cross-check. check() compares the latest figures and puts
a difference beyond config.CIRCULATING_TOLERANCE in the Review Queue.
"""
from __future__ import annotations

import pandas as pd

import config

SOURCE = "derived:onchain_total-noncirculating"


def exclusions(project_name: str) -> list[dict]:
    """Every address the on-chain figure excludes: [{metric, key, chain, address, source_url,
    verified, purpose}], from the contract entries that feed each subtracted metric."""
    spec = config.circulating_onchain(project_name) or {}
    p = config.PROJECT_BY_NAME.get(project_name) or {}
    out = []
    for metric in spec.get("subtract") or ():
        for key, c in (p.get("contracts") or {}).items():
            served = c.get("metric_override") or config.KIND_METRIC.get(c.get("kind"))
            if served == metric:
                out.append({"metric": metric, "key": key, "chain": c.get("chain"),
                            "address": c.get("address"), "source_url": c.get("source_url"),
                            "verified": c.get("verified"), "purpose": c.get("purpose", "")})
    return out


def _daily(h: pd.DataFrame, name: str, metric: str) -> pd.Series:
    g = h[(h["project"] == name) & (h["metric"] == metric)]
    if g.empty:
        return pd.Series(dtype=float)
    g = g.assign(date=pd.to_datetime(g["date"]).dt.normalize())
    return g.drop_duplicates("date", keep="last").set_index("date")["value"].astype(float).sort_index()


def series(h: pd.DataFrame, name: str) -> pd.Series:
    """The on-chain circulating figure per day, where the total and every excluded balance exist."""
    spec = config.circulating_onchain(name) or {}
    if spec.get("status") not in ("established", "partial"):
        return pd.Series(dtype=float)
    total = _daily(h, name, spec["total"])
    if total.empty:
        return total
    out = total.copy()
    for m in spec.get("subtract") or ():
        bal = _daily(h, name, m)
        out = (out - bal).dropna()
    # DECLARED EXCLUSIONS (Aerodrome, Jake 2026-09-30): a fixed, documented amount — permanently
    # locked team tokens — that stays in total supply and in the locked total, but not circulating.
    for ex in spec.get("declared_exclusions") or ():
        out = out - float(ex["tokens"])
    return out[out >= 0]


def check(out, h: pd.DataFrame, projects: list[dict]) -> None:
    """Latest on-chain (or first-party) circulating vs CoinGecko's, same day; beyond the
    tolerance, a Review Queue row with both figures and the exclusion set's status."""
    tol = config.CIRCULATING_TOLERANCE
    for p in projects:
        name = p["name"]
        spec = config.circulating_onchain(name) or {}
        st = spec.get("status")
        _check_first_party_against_set(out, h, name, spec)
        if st in ("established", "partial"):
            ours = series(h, name)
        elif st == "first_party":
            ours = _daily(h, name, spec["metric"])
        else:
            continue
        basis = ""
        if config.coingecko_counts_total(name):
            # CoinGecko counts (almost) every token (Sky, 2026-10-06): set against our on-chain TOTAL; ours is stricter
            # by exactly the subtracted balances, which the basis names.
            ours = _daily(h, name, spec["total"])
            basis = (" (ours as the on-chain TOTAL: CoinGecko counts every token; our circulating is stricter by "
                     + " + ".join(spec.get("subtract") or ()) + ")")
        elif config.coingecko_is_free_float(name):
            # CoinGecko's figure EXCLUDES staked/locked tokens (Pendle, Aerodrome; 2026-10-06): it is set against
            # OUR free float — circulating − the locked tokens inside it — never against our circulating.
            counted = set(config.coingecko_counted_lock_legs(name))    # legs CoinGecko still counts (sPENDLE)
            legs = [m for m in config.free_float_lock_metrics(name) if m not in counted]
            lock = _daily(h, name, legs[0]) if legs else ours * 0.0
            for extra in legs[1:]:                                      # Pendle's legacy vePENDLE leg
                lock = lock.add(_daily(h, name, extra), fill_value=0.0)
            if config.circulating_excludes_declared(name):
                lock = (lock - config.locked_excluded_from_circulating(name)).clip(lower=0)
            ours = (ours - lock).dropna()
            basis = " (ours as FREE FLOAT: CoinGecko's circulating excludes staked/locked tokens)"
        cg = _daily(h, name, "circulating_supply")
        both = pd.concat([ours.rename("ours"), cg.rename("cg")], axis=1).dropna()
        if both.empty:
            continue
        day, row = both.index[-1], both.iloc[-1]
        if row["cg"] <= 0:
            continue
        diff = row["ours"] / row["cg"] - 1
        if abs(diff) > tol:
            partial = f" The exclusion set is PARTIAL — {spec.get('missing')}, so ours reads high." \
                if st == "partial" else ""
            out.review_item(name, "circulating_supply", "onchain_vs_coingecko", "review",
                            value=float(row["ours"]), prior_value=float(row["cg"]), date=day,
                            source=SOURCE if st != "first_party" else spec["metric"], tier=2,
                            basis=f"{st} on-chain circulating {row['ours']:,.0f} vs CoinGecko "
                                  f"{row['cg']:,.0f} on {day.date()}: {diff:+.2%}, beyond the "
                                  f"±{tol:.0%} tolerance.{partial}{basis}")


def _check_first_party_against_set(out, h: pd.DataFrame, name: str, spec: dict) -> None:
    """THE POLICY'S CROSS-CHECK (Jake, 2026-10-07): where the project's OWN figure is primary and an on-chain set is
    computed (GEODNET), the latest own figure is set against the on-chain set on the same day — a hand-entered monthly
    figure holds for 45 days. Beyond the circulating row's tolerance (5% for a partial set) the PROJECT FIGURE is
    flagged for review; it is not replaced."""
    if spec.get("ratios_use") != "first_party" or spec.get("status") not in ("established", "partial"):
        return
    own, onchain = _daily(h, name, spec["metric"]), series(h, name)
    if own.empty or onchain.empty:
        return
    day = onchain.index[-1]
    prior = own[(own.index <= day) & (own.index >= day - pd.Timedelta(days=45))]
    if prior.empty:
        return
    fp = float(prior.iloc[-1])
    if fp <= 0:
        return
    tol = 0.02 if spec["status"] == "established" else 0.05
    diff = float(onchain.iloc[-1]) / fp - 1
    if abs(diff) > tol:
        out.review_item(name, "circulating_supply_first_party", "first_party_vs_onchain_set", "review",
                        value=fp, prior_value=float(onchain.iloc[-1]), date=day, source=spec["metric"], tier=2,
                        basis=f"the project's own circulating {fp:,.0f} (dated {prior.index[-1].date()}) vs our "
                              f"{spec['status']} on-chain set {float(onchain.iloc[-1]):,.0f} on {day.date()}: the set "
                              f"reads {diff:+.2%}, beyond ±{tol:.0%}. The project's figure stays primary and is "
                              f"flagged for review. Missing from the set: {spec.get('missing', 'n/a')}")


def report_lines() -> list[str]:
    """Per project: status, the excluded addresses with each one's source, or why none."""
    conv = config.CIRCULATING_CONVENTION
    lines = [f"CONVENTION ({conv['status']}): circulating includes {conv['counts_as_circulating']}; "
             f"excluded: {conv['excluded']}. {conv['standard_case']}."]
    for name, spec in config.CIRCULATING_ONCHAIN.items():
        st = spec["status"]
        head = f"{name}: {st.upper()}"
        if st in ("established", "partial"):
            ex = exclusions(name)
            dec = spec.get("declared_exclusions") or ()
            head += f" — total {spec['total']} − {len(ex)} address(es)" + (
                f" − {len(dec)} declared amount(s)" if dec else "")
            lines.append(head)
            for e in ex:
                lines.append(f"    - {e['key']} {e['chain']} {e['address']} ({e['metric']}); "
                             f"source {e['source_url']}, verified {e['verified']}")
            for d in dec:
                lines.append(f"    - EXCLUDED: {d['name']} = {d['tokens']:,.0f} (still in total supply, "
                             f"FDV and the locked total); source {d['source']}; decided by {d['decided_by']}")
            for pnd in spec.get("pending_same_principle") or ():
                lines.append(f"    - PENDING (same principle, not excluded): {pnd}")
            if not ex and not dec:
                lines.append(f"    (no exclusions) {spec.get('why', '')}")
            if st == "partial":
                lines.append(f"    MISSING: {spec['missing']}")
        else:
            lines.append(f"{head} — {spec.get('why', '')}")
        lines.append("    convention: staked/locked count as circulating; treasury, team, vesting "
                     "and burned are excluded")
        cand = config.NONCIRCULATING_CANDIDATES.get(name)
        if cand:
            lines.append(f"    documented candidates (NOT subtracted until a balance read is wired): "
                         f"{cand['note']}")
            for a in cand["addresses"]:
                lines.append(f"      - {a['role']}: {a['chain']} {a['address']} — {a['source']}")
            lines.append(f"      not established: {cand['not_established']}")
    return lines


if __name__ == "__main__":
    print("\n".join(report_lines()))
