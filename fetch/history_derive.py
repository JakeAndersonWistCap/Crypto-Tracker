"""
fetch/history_derive.py — derived series over the FULL span of their stored inputs. 2026-09-29 (Jake).

WHY. Every write-time derivation in fetch/__init__.py reads this run's frame only, and a routine
run's frame is 30 days. So a derived series only ever held the days it happened to be computed
on, whatever the store held underneath it:

    Near gross_issuance_tokens  one covered day (~706 NEAR) against a 365-day archival
                                total_supply_protocol — the 10x guard blocked the validator yield
    Near gross_burn_tokens      35 days, though DefiLlama revenue and price now span the year
    Sky actual_buyback_usd      29 of 729 rows valued, though price_usd now spans the year
    Plume gross_issuance_tokens 7 days;  Chainlink pool_release_tokens 7 days

This step runs after every write-time derivation, over (store UNION this run's frame, the frame
winning a shared key), and writes a row only where the stored one is ABSENT or DIFFERS from the
recomputation — so a price backfill re-values every buyback it now covers, and a same-day re-run
writes nothing new. The formulas and refusals are the write-time ones, applied per interval:

    usd        actual_buyback_usd = actual_buyback_tokens x price_usd on the row's own date, for
               every project whose usd series is DERIVED (never where a query sources it or a
               measured/restated usd row exists)
    chain_burn gross_burn_tokens = revenue_usd / price_usd for a chain whose DefiLlama revenue IS
               its burned fees (config.chain_burn_from_revenue), with the adapter's share gate;
               stands down if a measured burn is stored
    issuance   gross_issuance_tokens between CONSECUTIVE stored supply readings: d(supply), plus
               the burns dated inside the interval (add_burn), refused where a burn day is
               missing or the result is negative; a stale (flat) reading on a supply that moves
               daily is skipped so the next interval spans it
    pool_release  d(circulating) - d(total) between consecutive days holding both

Which (project, metric) take the non-usd rules is DECLARED (config.HISTORY_DERIVED): the series
Jake named, not every derivation in the book.
"""
from __future__ import annotations

import logging

import pandas as pd

import config
from .base import _measuring_point, point

log = logging.getLogger("token_metrics.fetch.history_derive")

SOURCE = "derive_history"
USD_SOURCE = "derived:tokens*price"
BURN_SOURCE = "derived:defillama_burned_fee_revenue/price"
REL_EPS = 1e-9


def _history(stored_long, frame) -> pd.DataFrame:
    cols = ["date", "project", "metric", "value", "source"]
    parts = [f[cols] for f in (stored_long, frame) if f is not None and not getattr(f, "empty", True)]
    if not parts:
        return pd.DataFrame(columns=cols)
    h = pd.concat(parts, ignore_index=True)
    h["date"] = pd.to_datetime(h["date"]).dt.normalize()
    return h.drop_duplicates(subset=["date", "project", "metric"], keep="last").sort_values("date")


def _series(h, name, metric) -> pd.DataFrame:
    return h[(h.project == name) & (h.metric == metric)]


def _differs(held: dict, day, v: float) -> bool:
    old = held.get(day)
    return old is None or abs(float(old) - v) > max(1e-9, abs(v) * REL_EPS)


def _emit(out, name, metric, rows, what):
    """rows: [(date, value, source)] — only those absent or different from what is held."""
    if not rows:
        return 0
    out.add(pd.concat([point(name, metric, v, s, 2, d) for d, v, s in rows], ignore_index=True),
            SOURCE, name, f"{metric}: {len(rows)} row(s) {rows[0][0].date()}..{rows[-1][0].date()} "
                          f"re-derived from the stored inputs — {what}", 2)
    return len(rows)


def _usd(out, h, p) -> int:
    name = p["name"]
    if config.dune_query_declared(name, "actual_buyback_usd"):
        return 0
    usd = _series(h, name, "actual_buyback_usd")
    if len(usd) and (usd["source"].astype(str) != USD_SOURCE).any():
        return 0                   # sourced, restated or manual: a measurement is never re-valued
    tok = _series(h, name, "actual_buyback_tokens")
    px = _series(h, name, "price_usd").set_index("date")["value"].astype(float)
    if tok.empty or px.empty:
        return 0
    held = dict(zip(usd["date"], usd["value"]))
    rows = []
    for r in tok.itertuples(index=False):
        if r.date in px.index and px[r.date] > 0:
            v = float(r.value) * float(px[r.date])
            if _differs(held, r.date, v):
                rows.append((r.date, v, USD_SOURCE))
    return _emit(out, name, "actual_buyback_usd", rows,
                 "actual_buyback_tokens x price_usd on each row's own date")


def _chain_burn(out, h, p) -> int:
    name = p["name"]
    decl = config.chain_burn_from_revenue(name)
    if not decl:
        return 0
    burn = _series(h, name, "gross_burn_tokens")
    if len(burn) and (burn["source"].astype(str) != BURN_SOURCE).any():
        return 0                   # a measured burn is stored: the derivation stands down
    rev = _series(h, name, "revenue_usd")
    px = _series(h, name, "price_usd").set_index("date")["value"].astype(float)
    fees = _series(h, name, "fees_usd").set_index("date")["value"].astype(float)
    share, tol = decl.get("share_of_fees"), float(decl.get("share_tolerance") or 0.001)
    held = dict(zip(burn["date"], burn["value"]))
    rows = []
    for r in rev.itertuples(index=False):
        if share is not None:
            fee = fees.get(r.date)
            if fee is None or fee <= 0 or abs(float(r.value) / float(fee) - share) > tol:
                continue           # the methodology tripwire, exactly as at write time
        if r.date in px.index and px[r.date] > 0:
            v = float(r.value) / float(px[r.date])
            if _differs(held, r.date, v):
                rows.append((r.date, v, BURN_SOURCE))
    return _emit(out, name, "gross_burn_tokens", rows,
                 f"revenue_usd / price_usd, DefiLlama burned-fee revenue ({decl.get('components', '')})")


def _issuance(out, h, p) -> int:
    name = p["name"]
    smetric = p.get("issuance_supply_metric") or "total_supply"
    mech = config.burn_mechanism(p)
    rule = config.issuance_supply_rule(p, mech.get("model"))
    if rule is None or mech.get("status") in ("refuted", "assumed"):
        return 0                   # the write-time derivation says why; no formula is guessed here
    sup = _series(h, name, smetric)
    if len(sup) < 2:
        return 0
    stale = config.moves_daily(name, smetric)
    kept = []
    for r in sup.itertuples(index=False):
        if stale and kept and abs(float(r.value) - kept[-1][1]) <= config.FLAT_TOLERANCE_TOKENS:
            continue               # a stale reading: the next interval spans it
        kept.append((r.date, float(r.value), str(r.source)))
    burn = _series(h, name, "gross_burn_tokens").set_index("date")["value"].astype(float)
    iss = _series(h, name, "gross_issuance_tokens")
    measured = iss[~iss["source"].astype(str).str.startswith("derived:")]
    if len(measured):
        return 0                   # a measured issuance is never overwritten
    held = dict(zip(iss["date"], iss["value"]))
    tag = f"derived:d_{smetric}" if smetric != "total_supply" else "derived:d_supply"
    tag += "+burn" if rule == "add_burn" else ""
    rows, refused = [], 0
    for (d0, v0, s0), (d1, v1, s1) in zip(kept, kept[1:]):
        if _measuring_point(s0) != _measuring_point(s1):
            refused += 1
            continue
        issued = v1 - v0
        if rule == "add_burn":
            days = pd.date_range(d0, d1 - pd.Timedelta(days=1))
            if not all(d in burn.index for d in days):
                refused += 1       # a burn day missing: issuance net of burn would be labelled gross
                continue
            issued += float(burn[days].sum())
        if issued < 0:
            refused += 1
            continue
        span = (d1 - d0).days
        src = tag + (f"[span={span}d]" if span > 1 else "")
        if _differs(held, d1, issued):
            rows.append((d1, issued, src))
    n = _emit(out, name, "gross_issuance_tokens", rows,
              f"{'d(' + smetric + ') + the burns inside each interval' if rule == 'add_burn' else 'd(' + smetric + ')'}"
              f"; {refused} interval(s) refused (point change, missing burn day, or negative)")
    return n


def _pool_release(out, h, p) -> int:
    name = p["name"]
    rel = _series(h, name, "pool_release_tokens")
    if len(rel) and (rel["source"].astype(str) != config.POOL_RELEASE_DERIVED_SOURCE).any():
        return 0                   # a measured release wins
    circ = _series(h, name, "circulating_supply").set_index("date")
    tot = _series(h, name, "total_supply").set_index("date")
    both = sorted(set(circ.index) & set(tot.index))
    held = dict(zip(rel["date"], rel["value"]))
    rows = []
    for d0, d1 in zip(both, both[1:]):
        if (_measuring_point(str(circ.loc[d0, "source"])) != _measuring_point(str(circ.loc[d1, "source"]))
                or _measuring_point(str(tot.loc[d0, "source"])) != _measuring_point(str(tot.loc[d1, "source"]))):
            continue
        v = (float(circ.loc[d1, "value"]) - float(circ.loc[d0, "value"])) \
            - (float(tot.loc[d1, "value"]) - float(tot.loc[d0, "value"]))
        span = (d1 - d0).days
        if _differs(held, d1, v):
            rows.append((d1, v, config.POOL_RELEASE_DERIVED_SOURCE + (f"[span={span}d]" if span > 1 else "")))
    return _emit(out, name, "pool_release_tokens", rows,
                 "d(circulating) - d(total) between consecutive days holding both")


RULES = {"chain_burn": _chain_burn, "issuance": _issuance, "pool_release": _pool_release}


def derive_from_history(out, projects: list[dict], stored_long) -> dict:
    """Run every declared history derivation; {(project, metric): rows written}."""
    done = {}
    if stored_long is None or getattr(stored_long, "empty", True):
        return done
    names = {p["name"] for p in projects}
    # ORDER MATTERS: the burn first, so issuance adds the burns it just re-derived.
    for (name, metric), rule in sorted(config.HISTORY_DERIVED.items(),
                                       key=lambda kv: list(RULES).index(kv[1])):
        if name not in names:
            continue
        h = _history(stored_long, out.frame())
        n = RULES[rule](out, h, config.PROJECT_BY_NAME[name])
        if n:
            done[(name, metric)] = n
    h = _history(stored_long, out.frame())
    for p in projects:
        n = _usd(out, h, p)
        if n:
            done[(p["name"], "actual_buyback_usd")] = n
    return done
