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
    # COMPARED BY MEASURING POINT (Jake's run 2026-10-05 21:00: Chainlink valued 2 of 431 rows though
    # 389 prices were stored): a marker on our own derived rows is not a measurement, and a stand-down
    # is SAID, with the source that caused it — it used to be silent.
    other = usd[usd["source"].astype(str).map(_measuring_point) != USD_SOURCE] if len(usd) else usd
    if len(other):
        out.skipped(SOURCE, name, f"actual_buyback_usd: NOT re-valued from stored prices — {len(other)} stored "
                                  f"row(s) come from another source ({str(other['source'].iloc[0])[:80]}, "
                                  f"{other['date'].min().date()}..{other['date'].max().date()}); a measurement "
                                  f"is never re-valued", 2)
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
    measured = burn[burn["source"].astype(str).map(_measuring_point) != BURN_SOURCE]
    rev = _series(h, name, "revenue_usd")
    if len(measured):
        # A MEASURED BURN TAKES OVER, BUT ONLY FROM ITS FIRST DAY (Ethereum, 2026-09-29). Where the
        # project DECLARES this derivation as the history leg of a handover to the measured burn,
        # the days before the measured leg are still filled — standing down entirely left
        # Ethereum with no burn before 2026-09-29, and so no issuance history. Undeclared: stand
        # down, as before.
        decl = config.declared_handover(name, "gross_burn_tokens") or {}
        if (decl.get("ordered_points") or (None,))[0] != BURN_SOURCE:
            return 0
        rev = rev[rev["date"] < measured["date"].min()]
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


def _say(out, name, n, message) -> None:
    """One line on the Run Log AND the console (Jake, 2026-09-30: NEAR's issuance-history step
    wrote nothing on a run where Ethereum's did, and no line said why)."""
    from .base import LogEntry
    out.log.append(LogEntry(SOURCE, name, n, "ok", message, 2))
    log.info("%s: %s", name, message)


def _issuance(out, h, p) -> int:
    name = p["name"]
    smetric = p.get("issuance_supply_metric") or "total_supply"
    # A DECLARED HISTORY SUPPLY (Ethereum, A1 2026-09-30): the live issuance is differenced from
    # one stock, its history from another, and the history leg stops at the live leg's first row
    # (a declared series_handover; the two never overlap).
    hist = p.get("issuance_history_supply")
    if hist:
        smetric = hist["metric"]
    mech = config.burn_mechanism(p)
    rule = config.issuance_supply_rule(p, mech.get("model"))
    # EVERY EXIT SAYS WHY. Before 2026-09-30 these three returned silently, so a run that wrote
    # nothing for NEAR could not be told apart from a run that never reached it.
    if rule is None or mech.get("status") in ("refuted", "assumed"):
        _say(out, name, 0, f"gross_issuance_tokens history NOT RUN — no supply rule for burn "
                           f"mechanism {mech.get('model')!r} (status {mech.get('status')!r}); the "
                           f"write-time derivation says why")
        return 0
    sup = _series(h, name, smetric)
    if len(sup) < 2:
        _say(out, name, 0, f"gross_issuance_tokens history NOT RUN — {smetric} holds {len(sup)} "
                           f"reading(s) in the store and this run; 2 are needed")
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
    skip_dates = set()
    if len(measured) and hist:
        # A DECLARED HISTORY LEG FILLS AROUND A MEASURED ROW, never over it (Ethereum, 2026-09-30):
        # the dates a measured row holds are skipped, and the log names them.
        skip_dates = set(measured["date"])
        srcs = measured["source"].astype(str).value_counts()
        _say(out, name, 0, f"gross_issuance_tokens history: {len(measured)} measured row(s) kept, "
                           f"their dates skipped — " + ", ".join(f"{s} x{c}" for s, c in srcs.items()))
    elif len(measured):            # a measured issuance is never overwritten
        srcs = measured["source"].astype(str).value_counts()
        _say(out, name, 0, f"gross_issuance_tokens history NOT RUN — {len(measured)} stored "
                           f"row(s) are not derived, and a measured issuance is never "
                           f"overwritten: " + ", ".join(f"{s} x{c}" for s, c in srcs.items())
                           + f" ({measured['date'].min().date()}..{measured['date'].max().date()})")
        return 0
    held = dict(zip(iss["date"], iss["value"]))
    stop = None
    if hist:
        live = iss[iss["source"].astype(str).str.startswith(hist["before_first_prefix"])]
        stop = live["date"].min() if len(live) else None
        # ONLY THIS LEG'S rows are "held" for the unchanged-value comparison
        own = iss[iss["source"].astype(str).str.startswith(hist["tag"])]
        held = dict(zip(own["date"], own["value"]))
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
        if stop is not None and d1 >= stop:
            continue           # the live leg covers it: the handover never overlaps
        if d1 in skip_dates:
            continue
        if _differs(held, d1, issued):
            rows.append((d1, issued, src))
    n = _emit(out, name, "gross_issuance_tokens", rows,
              f"{'d(' + smetric + ') + the burns inside each interval' if rule == 'add_burn' else 'd(' + smetric + ')'}"
              f"; {refused} interval(s) refused (point change, missing burn day, or negative)")
    # SAY WHAT THE INPUTS COVER, WHETHER OR NOT ANYTHING WAS WRITTEN (Jake, 2026-09-29: NEAR's
    # issuance history was asked for three times and a refusal wrote nothing to the log).
    burn_days = set(burn.index)
    no_burn = sorted({d for (d0, _, _), (d1, _, _) in zip(kept, kept[1:])
                      for d in pd.date_range(d0, d1 - pd.Timedelta(days=1)) if d not in burn_days}) \
        if rule == "add_burn" else []
    fees = _series(h, name, "fees_usd")
    _say(out, name, n,
        f"gross_issuance_tokens history INPUTS — {smetric} {len(kept)} reading(s) "
        f"{kept[0][0].date()}..{kept[-1][0].date()}; gross_burn_tokens {len(burn)} day(s)"
        + (f" {min(burn_days).date()}..{max(burn_days).date()}" if burn_days else "")
        + f"; fees_usd {len(fees)} day(s) (the burn's 70% tripwire needs it on each day); "
        f"{n} row(s) written, {refused} interval(s) refused"
        + (f", {len(no_burn)} day(s) with no burn ({no_burn[0].date()}..{no_burn[-1].date()})"
           if no_burn else ""))
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


def _gross_issuance(out, h, p) -> int:
    """d(total_supply_gross) between consecutive stored readings (Uniswap, 2026-09-29): a transfer
    burn leaves the contract's totalSupply alone, so its change IS gross issuance — 0 on every
    one of the 366 archived days for UNI, whose supply is exactly 1,000,000,000."""
    name = p["name"]
    sup = _series(h, name, "total_supply_gross")
    iss = _series(h, name, "gross_issuance_tokens")
    if len(sup) < 2 or len(iss[~iss["source"].astype(str).str.startswith("derived:")]):
        return 0
    held = dict(zip(iss["date"], iss["value"]))
    rows, refused = [], 0
    pts = list(sup.itertuples(index=False))
    for a, b in zip(pts, pts[1:]):
        if _measuring_point(str(a.source)) != _measuring_point(str(b.source)) or b.value < a.value:
            refused += 1
            continue
        span = (b.date - a.date).days
        v = float(b.value) - float(a.value)
        if _differs(held, b.date, v):
            rows.append((b.date, v, "derived:d_supply_gross" + (f"[span={span}d]" if span > 1 else "")))
    return _emit(out, name, "gross_issuance_tokens", rows,
                 f"d(total_supply_gross); {refused} interval(s) refused (point change or a fall)")


FLAT_RUN_DAYS = 7
IMPLIED = "coingecko:mcap/price"


def _circ_history(out, h, p) -> int:
    """circulating_supply before the first live CoinGecko reading = market cap / price for each
    past day (Jake, 2026-09-29) — the 365-day circulating_supply_implied series, stored under
    circulating_supply as the declared history leg (config.GLOBAL_SERIES_HANDOVER), strictly before
    the live leg so the two never overlap.

    FLAT STRETCHES ARE FLAGGED, NOT BELIEVED: CoinGecko updates some coins' circulating figure
    rarely (seen on GEODNET and ETH), so market cap / price holds still while the real supply
    moves. A run of FLAT_RUN_DAYS or more unchanged days carries [flat=Nd] on each of its rows."""
    name = p["name"]
    circ = _series(h, name, "circulating_supply")
    pts = set(circ["source"].astype(str).map(_measuring_point))
    live = circ[circ["source"].astype(str).map(_measuring_point) == "coingecko"]
    if live.empty or pts - {"coingecko", IMPLIED}:
        return 0                   # a live leg from another source: not the declared pair
    first_live = live["date"].min()
    imp = _series(h, name, "circulating_supply_implied")
    imp = imp[imp["date"] < first_live].sort_values("date")
    if imp.empty:
        return 0
    vals, dates = imp["value"].astype(float).tolist(), imp["date"].tolist()
    run = [1] * len(vals)
    for i in range(1, len(vals)):
        if abs(vals[i] - vals[i - 1]) <= max(config.FLAT_TOLERANCE_TOKENS, abs(vals[i]) * 1e-7):
            run[i] = run[i - 1] + 1
    length = run[:]                               # each row gets its whole run's length
    for i in range(len(vals) - 2, -1, -1):
        if run[i + 1] > 1:
            length[i] = length[i + 1]
    held = dict(zip(circ["date"], circ["value"]))
    rows, flat_days = [], 0
    for d, v, n in zip(dates, vals, length):
        src = IMPLIED + (f"[flat={n}d]" if n >= FLAT_RUN_DAYS else "")
        flat_days += n >= FLAT_RUN_DAYS
        if _differs(held, d, v):
            rows.append((d, v, src))
    return _emit(out, name, "circulating_supply", rows,
                 f"market cap / price before the live read ({first_live.date()}); {flat_days} day(s) "
                 f"in flat stretches of {FLAT_RUN_DAYS}+ days, flagged [flat=Nd] — CoinGecko's "
                 f"circulating figure not updating, not a real flat supply")


RULES = {"chain_burn": _chain_burn, "issuance": _issuance, "pool_release": _pool_release,
         "gross_issuance": _gross_issuance}


def derive_from_history(out, projects: list[dict], stored_long) -> dict:
    """Run every declared history derivation; {(project, metric): rows written}."""
    done = {}
    if stored_long is None or getattr(stored_long, "empty", True):
        log.info("history derivations NOT RUN — no stored rows were passed in")
        return done
    names = {p["name"] for p in projects}
    # ORDER MATTERS: the burn first, so issuance adds the burns it just re-derived.
    for (name, metric), rule in sorted(config.HISTORY_DERIVED.items(),
                                       key=lambda kv: list(RULES).index(kv[1])):
        if name not in names:
            log.info("%s %s history NOT RUN — %s is not in this run's projects", name, metric, name)
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
        n = _circ_history(out, h, p) if p.get("coingecko_id") else 0
        if n:
            done[(p["name"], "circulating_supply")] = n
    return done
