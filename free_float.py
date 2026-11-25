"""free_float.py — ROUND 20 (Jake 2026-11-24, Part 2): THE ONE FREE FLOAT, and what moved it each day.

    free float = circulating − staked / locked / earning − issuer, foundation and treasury holdings − held buybacks

ONE series per project per day (config.FREE_FLOAT_METRIC = free_float_tokens), formed HERE and nowhere else
(`views`), as a read-time view from the series the build already holds; its three components are stored beside it
(config.FREE_FLOAT_COMPONENTS). Every tab reads that series (build_workbook._free_float): A1's "float after staking",
A2's own free float, A3's "effective float", CP's "tradeable float" and the dilution cells' own float are gone as
separate computations (tests/test_r20_float.py holds every float-named cell to the series, and the source to this
one place).

A day on which a component cannot be read holds NO value — unknown is never 0 — and the missing component is named
(MISSING). A component is "read on a day" when its latest row on or before the day is no older than the series'
staleness limit (the same rule a stock's quarter-end reading uses, build_workbook._quarter_end_readings).
A token is taken off ONCE: the lock inside circulating is the stake less what circulating already leaves out, the
issuer stock is the rule's own sum (config.RULE_IN, which already nets an added-back wallet), and a held buyback the
rule carries inside the issuer stock is shown under held buybacks and taken out of the issuer component.

THE DAILY MOVEMENTS (`movements`): per project per day
    + emissions (new supply created)            + unlocks (released from issuer wallets / schedules)
    + unstaked                                  − newly staked / locked
    − held buybacks (bought)                    + held buybacks sold or paid out in liquid form
    − burns from float                          ± issuer and treasury moves other than unlocks
                                                  (of which: sent to exchange-tagged addresses — a named line)
and the identity  yesterday's float + movements = today's float.  The lines that are stock changes (staking, held
buybacks, issuer) are exact; the measured flows (emissions, unlocks, burns) are the circulating statement's own legs,
so the day's residual is the part of the day's circulating change no measured flow explains. A residual beyond
config.FLOAT_RESIDUAL_LIMIT (0.5%) of yesterday's float is flagged with its cause. Weekly and quarterly figures are
sums of the days (`statements`, `period_table`), written on the Flows sheet beside the circulating statement.

Rule 3 (new supply counts when created or released, even if it goes straight into staking): the emission is its own
"+" line on its day and the stake's rise its own "−" line — never netted before it is shown.
Rule 4 (held buybacks): the rise of the held stock comes off on the day it is bought; a fall comes back as its own
named line; a payout to stakers that stays staked is a fall of the held stock and an equal rise of the stake on the
same day, so it stays off the float.
"""
from __future__ import annotations

import pandas as pd

import config

MISSING: dict = {}      # {project: why no free float is formed (the missing component named)} — this build
PARTS: dict = {}        # {project: {"staked_locked": "...", "issuer_treasury": "...", "held_buybacks": "..."}} — what was read
FORMED: set = set()     # the projects whose free float holds at least one day — this build
MOVES: dict = {}        # {project: DataFrame, one row per day the float is formed (from the second such day)}

FF = config.FREE_FLOAT_METRIC
C_LOCK, C_ISSUER, C_HELD = (m for m, _l in config.FREE_FLOAT_COMPONENTS)

# (key, the line on the sheet). Values are SIGNED contributions to the float, so the lines sum to the expected change.
LINES = (
    ("emissions", "+ emissions (new supply created — counted when created, even if it goes straight into staking)"),
    ("unlocks", "+ unlocks (released from issuer wallets and schedules)"),
    ("unstaked", "+ unstaked / unlocked (the fall of the staked / locked stock, net per day)"),
    ("staked", "− newly staked / locked (the rise of the staked / locked stock, net per day)"),
    ("held_bought", "− held buybacks (bought and held: the rise of the held stock)"),
    ("held_returned", "+ held buybacks sold or paid out in liquid form (the fall of the held stock)"),
    ("burns", "− burns from float"),
    ("issuer", "± issuer and treasury moves, other than unlocks (+ = out of issuer hands into the float)"),
)
LINE_KEYS = tuple(k for k, _l in LINES)
EXCHANGE_LINE = "of which: sent by issuer-side wallets to exchange-tagged addresses (named line, inside the lines above)"


def parts(p: dict) -> dict | str:
    """What the one free float subtracts for this project — {"lock", "issuer", "held"} as the series to read — or the
    reason (text) the staked / locked component cannot be read at all (no lock metric on the project, or blocked)."""
    import build_workbook as bw                                          # noqa: PLC0415
    name = p["name"]
    lock = {"add": (), "less": (), "const": 0.0}
    stake = config.CP_STAKE_METRIC.get(name)
    if stake:                                                            # the declared stake series (Ethereum)
        lock["add"] = (stake,)
    elif not p.get("free_float_lock_zero"):                              # a declared zero lock is the number 0
        why = bw._free_float_reason(p)
        if why:
            return why
        lock["add"] = tuple(config.free_float_lock_metrics(name))
        if config.circulating_excludes_declared(name):
            # the stake held by wallets circulating already leaves out comes off ONCE
            x = config.locked_excluded_from_circulating(name)
            if x:
                lock["const"] = float(x)
            else:
                lock["less"] = tuple(config.locked_excluded_metrics(name))
    fr = config.float_released_held(name) or {}
    rule = "rule_in_off_float_tokens" if name in config.RULE_IN and name not in config.RULE_IN_PENDING else None
    issuer = tuple(m for m in (fr.get("metric"), rule) if m)
    hm = config.held_buyback_float_metric(name)
    h = config.held_buyback_float(name) or {}
    held = {"add": (hm,) if hm else (),
            "less": (h["less"],) if hm and config.held_buyback_less_applies(name) else ()}
    return {"lock": lock, "issuer": issuer, "held": held}


def _text(add=(), less=(), const=0.0) -> str:
    if not add:
        return "0 (none declared)"
    t = " + ".join(add)
    if less or const:
        t = f"max(0, {t}" + "".join(f" − {m}" for m in less) + (f" − {const:,.0f}" if const else "") + ")"
    return t


def views(groups: dict, asof) -> None:
    """THE ONE PLACE THE FREE FLOAT IS FORMED. Writes free_float_tokens and its three components into `groups`, per
    project per day, and fills MISSING / PARTS / MOVES."""
    import build_workbook as bw                                          # noqa: PLC0415
    MISSING.clear()
    FORMED.clear()
    PARTS.clear()
    MOVES.clear()
    asof = pd.Timestamp(asof).normalize()
    stale = bw.GLOBALS["stale_after_days"]
    for p in bw.scoped_projects():
        name = p["name"]
        for m in (FF, C_LOCK, C_ISSUER, C_HELD):
            groups.pop((name, m), None)
        g_c = groups.get((name, "circulating_supply_used"))
        circ_raw = bw._daily(g_c).dropna()
        if circ_raw.empty or circ_raw.index.min() > asof:
            MISSING[name] = "n/a — free float: circulating_supply_used holds no row"
            continue
        days = pd.date_range(circ_raw.index.min(), asof)
        last_read: dict = {}

        def carried(metric: str):
            """The series as read on each day: its latest row on or before the day, within its staleness limit."""
            m = config.metric_alias(name, metric)
            g = groups.get((name, m))
            s = bw._daily(g).dropna()
            if s.empty:
                last_read[metric] = None
                return None
            last_read[metric] = s.index.max()
            lim = int(bw._stale_limit(name, m, stale, g))
            return s.reindex(s.index.union(days)).sort_index().ffill(limit=lim).reindex(days)

        def total(add=(), less=(), const=0.0):
            if not add:
                return pd.Series(0.0, index=days)
            tot = pd.Series(0.0, index=days)
            for m, sign in [(m, 1.0) for m in add] + [(m, -1.0) for m in less]:
                s = carried(m)
                if s is None:
                    return None
                tot = tot + sign * s
            return (tot - const).clip(lower=0.0) if (less or const) else tot

        sp = parts(p)
        circ = carried("circulating_supply_used")
        if isinstance(sp, str):
            lock, why_lock = None, sp
            sp = {"lock": {"add": (), "less": (), "const": 0.0}, "issuer": (), "held": {"add": (), "less": ()}}
            sp.update({k: v for k, v in (parts({**p, "free_float_lock_zero": "x"}) or {}).items() if k != "lock"})
        else:
            lock, why_lock = total(**sp["lock"]), None
        issuer = total(add=sp["issuer"])
        held = total(**sp["held"])
        # a held buyback the rule carries inside the issuer stock is shown under held buybacks — moved, not counted twice
        moved = [m for m in config.FREE_FLOAT_HELD_IN_ISSUER.get(name, ())
                 if "rule_in_off_float_tokens" in sp["issuer"]
                 and m in ((config.RULE_IN.get(name) or {}).get("off_float") or ())]
        if moved and issuer is not None and held is not None:
            for m in moved:
                s = carried(m)
                if s is None:
                    continue
                part = pd.concat([s, issuer], axis=1).min(axis=1).fillna(0.0).clip(lower=0.0)
                issuer, held = issuer - part, held + part
        PARTS[name] = {
            "staked_locked": why_lock or _text(**sp["lock"]),
            "issuer_treasury": _text(add=sp["issuer"]) + (f" − {' − '.join(moved)} (shown under held buybacks)"
                                                          if moved else "")
            # R18: a project whose every issuer leg joins from a declared date and is not read yet keeps its float as
            # it was until they are (config.RULE_IN_PENDING) — said here, never shown as "none"
            + (f" — PENDING: the declared issuer legs ({', '.join((config.RULE_IN.get(name) or {}).get('off_float') or ())}"
               f") are {config.RULE_IN_PENDING[name]}; they come off the float from their first stored row"
               if name in config.RULE_IN_PENDING else ""),
            "held_buybacks": " + ".join(x for x in (_text(**sp["held"]) if sp["held"]["add"] else "", *moved) if x)
            or "0 (none declared)"}
        cols = g_c.columns
        src = (f"derived:free_float[circulating_supply_used − staked/locked({PARTS[name]['staked_locked']}) − "
               f"issuer/treasury({PARTS[name]['issuer_treasury']}) − held buybacks({PARTS[name]['held_buybacks']})]")

        def store(metric: str, s: pd.Series, source: str) -> None:
            s = s.dropna()
            if s.empty:
                return
            groups[(name, metric)] = bw._as_stored(pd.DataFrame(
                {"date": s.index, "project": name, "metric": metric, "value": s.values, "tier": 2,
                 "source": source}), cols)

        comps = {C_LOCK: lock, C_ISSUER: issuer, C_HELD: held}
        for m, s in comps.items():
            if s is not None:
                store(m, s, f"derived:free_float_component[{PARTS[name][m[len('free_float_'):-len('_tokens')]]}]")
        if any(s is None for s in comps.values()):
            ff = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
        else:
            ff = (circ - lock - issuer - held).dropna()
        store(FF, ff, src)
        if not ff.empty:
            FORMED.add(name)
        if ff.empty or ff.index.max() < asof:
            # NAME THE MISSING COMPONENT (on the as-of day): which leg holds no reading within its limit
            legs = [("circulating", ("circulating_supply_used",)),
                    ("staked / locked", (*sp["lock"]["add"], *sp["lock"]["less"])),
                    ("issuer / treasury", sp["issuer"]), ("held buybacks", (*sp["held"]["add"], *sp["held"]["less"]))]
            bad = [why_lock[6:] if why_lock and why_lock.startswith("n/a — ") else why_lock] if why_lock else []
            for lab, ms in legs:
                for m in ms:
                    s = carried(m)
                    if s is None:
                        bad.append(f"{m} ({lab}): no rows stored")
                    elif pd.isna(s.iloc[-1]):
                        bad.append(f"{m} ({lab}): last read {last_read[m].date()}, not within its limit on "
                                   f"{asof.date()}")
            if "rule_in_off_float_tokens" in sp["issuer"] and (name, "rule_in_off_float_tokens") not in groups:
                bad = [b for b in bad if not b.startswith("rule_in_off_float_tokens")]
                bad.append("rule_in_off_float_tokens (issuer / treasury): a declared issuer-side leg holds no row "
                           f"({', '.join((config.RULE_IN.get(name) or {}).get('off_float') or ())})")
            MISSING[name] = ("n/a — free float: a component is not read — " + "; ".join(bad or ["not formed"]))[:480]
        if len(ff) > 1:
            MOVES[name] = movements(p, groups, ff, {k: v.reindex(ff.index) for k, v in comps.items()},
                                    circ.reindex(ff.index))


def buying_views(groups: dict) -> None:
    """float_buying_tokens — the demand side of net float pressure. R21 (Jake 2026-11-25, F1/F2, "any burn"): each
    day's buyback HELD + DISTRIBUTED (buyback_held_tokens + buyback_distributed_tokens: paid to stakers counts as
    buying, F2) where the buyback was revenue-funded that day (config.holder_funding), + the purchases paid on to
    customers (config.BUYING_CUSTOMERS_F1, Ether.fi: "buying counts as buying"). A BURNED buyback is not here: it is on
    the burns line, once, on the day it is burned. A day whose buyback was not revenue-funded is a measured 0.
    Was (R20): retired_buyback_tokens (burned + held) on revenue-funded days."""
    import build_workbook as bw                                          # noqa: PLC0415
    for p in bw.scoped_projects():
        name = p["name"]
        groups.pop((name, "float_buying_tokens"), None)
        if config.buyback_route(name)["route"] == "none":
            continue
        legs = [m for m in ("buyback_held_tokens", "buyback_distributed_tokens")
                if groups.get((name, m)) is not None and not groups[(name, m)].empty]
        cust = config.BUYING_CUSTOMERS_F1.get(name)
        cust = cust if cust and groups.get((name, cust)) is not None and not groups[(name, cust)].empty else None
        if not legs and not cust:
            continue
        frames = [bw._daily(groups[(name, m)]) for m in legs]
        if frames:
            # the split series share one set of days (the purchase days the destination shares cover)
            v = pd.concat(frames, axis=1).sum(axis=1, min_count=1).dropna()
            ok = pd.Series([config.holder_funding(name, d)[0] == "revenue" for d in v.index], index=v.index)
            v = v.where(ok, 0.0)
        else:
            v = pd.Series(dtype=float)
        if cust:
            c = bw._daily(groups[(name, cust)])
            v = v.add(c, fill_value=0.0) if not v.empty else c
        if v.empty:
            continue
        cols = groups[(name, (legs or [cust])[0])].columns
        groups[(name, "float_buying_tokens")] = bw._as_stored(pd.DataFrame(
            {"date": v.index, "project": name, "metric": "float_buying_tokens", "value": v.values, "tier": 2,
             "source": "derived:float_buying[" + "+".join(legs) + " where revenue-funded (config.holder_funding)"
                       + (f" + {cust} (F1)" if cust else "") + "]"}), cols)


def returned_views(groups: dict) -> None:
    """float_returned_tokens (R21 F1/F2/F3/F7): BOUGHT TOKENS RETURNED TO THE FLOAT, a supply line of net float
    pressure. A declared payout leg (config.FLOAT_RETURNS): `paid` − `less` per day — the payout the purchases covered
    (the uncovered part is already new supply), on the days both hold a row. A held buyback stock
    (config.FLOAT_RETURNS_HELD, F7): each fall of the stock between consecutive readings, on the day it is read, less
    the same day's burn out of that holding (a burn is a burn). Rises (purchases) are the buying line's."""
    import build_workbook as bw                                          # noqa: PLC0415
    for p in bw.scoped_projects():
        name = p["name"]
        groups.pop((name, "float_returned_tokens"), None)
        spec, held = config.FLOAT_RETURNS.get(name), config.FLOAT_RETURNS_HELD.get(name)
        if spec:
            paid, less = bw._daily(groups.get((name, spec["paid"]))), bw._daily(groups.get((name, spec["less"])))
            if paid.empty or less.empty:
                continue
            both = pd.concat([paid.rename("p"), less.rename("l")], axis=1).dropna()
            v = (both["p"] - both["l"]).clip(lower=0.0)
            src = f"derived:float_returned[{spec['paid']} − {spec['less']}]"
            base = groups[(name, spec["paid"])]
        elif held:
            # what LEFT the holding = what was put in (the held buyback) − what the stock rose by, never below 0 and
            # never taken back (a running maximum, as the cashback rule): a day-to-day wobble of the stock read is not
            # a departure; a burn out of the holding is a burn, not a return
            s = bw._daily(groups.get((name, held["held"]))).dropna()
            if len(s) < 2:
                continue
            put = bw._daily(groups.get((name, "buyback_held_tokens"))).reindex(s.index.union(
                bw._daily(groups.get((name, "buyback_held_tokens"))).index)).fillna(0.0)
            put = put[put.index > s.index[0]].cumsum().reindex(s.index).ffill().fillna(0.0)
            burned = [b["metric"] for b in config.burn_series(name) if b.get("from") == "treasury"]
            out_b = pd.Series(0.0, index=s.index)
            for m in burned:
                bm = bw._daily(groups.get((name, m)))
                out_b = out_b + bm[bm.index > s.index[0]].cumsum().reindex(s.index).ffill().fillna(0.0)
            gap = (put - (s - s.iloc[0]) - out_b).clip(lower=0.0).cummax()
            v = gap.diff().iloc[1:]
            src = (f"derived:float_returned[the rise of max(0, Σ buyback_held_tokens − Δ{held['held']}"
                   + (f" − Σ{'+'.join(burned)}" if burned else "") + ")]")
            base = groups[(name, held["held"])]
        else:
            continue
        if v.empty:
            continue
        groups[(name, "float_returned_tokens")] = bw._as_stored(pd.DataFrame(
            {"date": v.index, "project": name, "metric": "float_returned_tokens", "value": v.values, "tier": 2,
             "source": src}), base.columns)


def burn_memo(name: str, groups: dict, asof, window: tuple | None = None) -> dict:
    """THE MEMO LINE (R21): where each burn of the window came from — {"float", "staked", "treasury", "not split"} in
    tokens (the declared source of each series, config.BURN_SERIES; Uniswap's one-offs out of an issuer-side wallet are
    'treasury' by their declared one_off_flows row) — plus the off-float share's effect on net float pressure
    (tokens a year ÷ the free float, each leg annualised as the burns cell does: by events x cadence where declared,
    else over the days its series — or its mint's, `annualise_as` — covers in the window), the one-offs named in the window
    (config.ONE_OFF_NAMED) and the text the CP cell shows. Never guessed: a series with no row in the window is named."""
    import build_workbook as bw                                          # noqa: PLC0415
    asof = pd.Timestamp(asof).normalize()
    start, end = window or (asof - pd.Timedelta(days=int(bw.GLOBALS["period_days"])), asof)
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    out = {k: 0.0 for k in config.BURN_SOURCES}
    yearly = {k: 0.0 for k in config.BURN_SOURCES}
    missing, parts = [], []
    span = max((end - start).days, 1)

    def per_year(metric, w, tot, cov_of=None):
        ev = config.events_per_year(name, metric)
        if ev:
            n = int((w != 0).sum())
            return tot / n * ev if n else tot * 365.0 / span
        c = bw._daily(groups.get((name, cov_of or metric)))
        cov = int(((c.index > start) & (c.index <= end)).sum()) if not c.empty else len(w)
        return tot * 365.0 / min(max(cov, 1), span)
    p = config.PROJECT_BY_NAME.get(name) or {}
    for b in config.burn_series(name):
        s = bw._daily(groups.get((name, b["metric"])))
        w = s[(s.index > start) & (s.index <= end)]
        if w.empty:
            missing.append(b["metric"])
            continue
        tot = float(w.sum())
        if b["metric"] == config.a1_burn_metric(name):
            for f in p.get("one_off_flows") or ():
                day = pd.Timestamp(f.get("landed") or f["window"][0])
                if f.get("metric") == b["metric"] and f.get("from_stock") and start < day <= end \
                        and tot >= float(f["tokens"]):           # the one-off is in the series that window
                    tot -= float(f["tokens"])
                    out["treasury"] += float(f["tokens"])
                    yearly["treasury"] += float(f["tokens"]) * 365.0 / span
                    parts.append(f"{float(f['tokens']):,.0f} {f['what']} (treasury)")
        out[b["from"]] += tot
        yearly[b["from"]] += per_year(b["metric"], w, tot, b.get("annualise_as"))
        parts.append(f"{b['metric']} {tot:,.0f} ({b['from']})")
    ff = bw._daily(groups.get((name, FF))).dropna()
    ff = ff[ff.index <= end]
    off = out["staked"] + out["treasury"]
    pts = ((yearly["staked"] + yearly["treasury"]) / float(ff.iloc[-1])) if (len(ff) and float(ff.iloc[-1]) > 0) \
        else None
    named = [o["what"] for o in config.ONE_OFF_NAMED.get(name, ())
             if pd.Timestamp(o["from"]) <= end and pd.Timestamp(o["to"]) > start]
    tot_all = sum(out.values())
    if not config.burn_series(name):
        text = "no burn — " + (config.BURN_NONE.get(name) or "no burn series declared")
    else:
        text = (f"burns {start.date()}..{end.date()}: " + (", ".join(
            f"from {k} {v:,.0f} ({v / tot_all:.0%})" for k, v in out.items() if v) if tot_all else "0 burned")
            + (f" — {'; '.join(parts)}" if parts else "")
            + (f"; NO ROW in the window: {', '.join(missing)}" if missing else ""))
        if pts is not None and off:
            text += (f". Off-float burns (staked + treasury) = {pts:+.2%} of the free float a year"
                     + (" — MOVES THE HEADLINE BY MORE THAN 1 POINT" if pts > config.BURN_OFF_FLOAT_REPORT else ""))
        if named:
            text += ". One-off in the window (in the yearly rate, a named line): " + "; ".join(named)
    return {**out, "off_float_rate": pts, "missing": missing, "one_offs": named, "text": text[:1500]}


def _leg_kind(name: str, label: str, sign: int) -> str:
    """Which float line a leg of the circulating statement belongs to."""
    if sign < 0:
        return "returned" if label.startswith("− returned") else "burns"
    rule_in = {x[0] for x in (config.RULE_IN.get(name) or {}).get("flow_in") or ()}
    return "emissions" if label.startswith("+ minted") or label in rule_in else "unlocks_out"


def movements(p: dict, groups: dict, ff: pd.Series, comps: dict, circ: pd.Series) -> pd.DataFrame:
    """One row per day the float is formed (from the second): opening, the signed lines, closing, the residual, its
    share of the opening, the flag and its cause; the components and the named exchange line beside them. A day after
    a gap carries the whole gap's movements (flows summed over the days since the float was last formed), so the days
    always add up to the change between any two days."""
    import build_workbook as bw                                          # noqa: PLC0415
    name = p["name"]
    have, d, _pre, _rel_m, legs = bw._token_flow_legs(p, groups)
    idx = ff.index
    span = pd.date_range(idx[0], idx[-1])

    def per_step(s: pd.Series) -> pd.Series:
        """A daily flow summed over each step (previous formed day, this day]."""
        if s is None or s.empty:
            return pd.Series(0.0, index=idx)
        cs = s.groupby(level=0).sum().reindex(span).fillna(0.0).cumsum().reindex(idx)
        return cs.diff().fillna(0.0)

    kinds = {"emissions": [], "unlocks_out": [], "returned": [], "burns": []}
    for lab, m, sign in legs:
        if m is not None:
            kinds[_leg_kind(name, lab, sign)].append(m)
    tot = {k: sum((per_step(d(m)) for m in ms), pd.Series(0.0, index=idx)) for k, ms in kinds.items()}
    has_u = bool(config.has_unlock_leg(name)) and (name, "unlock_release_tokens") in groups \
        and "unlock_release_tokens" not in kinds["unlocks_out"]
    unlocks = per_step(d("unlock_release_tokens")) if has_u else tot["unlocks_out"]
    # unlocks out of issuer wallets the rule counts IN circulating: a fall of the issuer stock, shown as an unlock
    unlock_in = (unlocks - tot["unlocks_out"]) if has_u else pd.Series(0.0, index=idx)
    dS, dI, dH = (comps[k].diff().fillna(0.0) for k in (C_LOCK, C_ISSUER, C_HELD))
    out = pd.DataFrame(index=idx)
    out["open"] = ff.shift(1)
    out["emissions"] = tot["emissions"]
    out["unlocks"] = unlocks
    out["unstaked"] = (-dS).clip(lower=0.0)
    out["staked"] = -(dS.clip(lower=0.0))
    out["held_bought"] = -(dH.clip(lower=0.0))
    out["held_returned"] = (-dH).clip(lower=0.0)
    out["burns"] = -tot["burns"]
    out["issuer"] = -dI - tot["returned"] - unlock_in
    out["close"] = ff
    out["residual"] = out["close"] - out["open"] - out[list(LINE_KEYS)].sum(axis=1)
    out["pct"] = out["residual"] / out["open"].where(out["open"] != 0)
    ex = config.FLOAT_EXCHANGE_SENDS.get(name)
    out["exchange"] = per_step(d(ex)) if ex and (name, ex) in groups else float("nan")
    # R21b (config.FLOAT_NAMED_MOVES): a named movement inside one of the lines above (Morpho's founder Vester claims,
    # inside the issuer line) — shown with its amount, never added to the identity a second time
    nm = config.FLOAT_NAMED_MOVES.get(name)
    out["named"] = per_step(d(nm["series"])) if nm and (name, nm["series"]) in groups else float("nan")
    out["staked_locked"], out["issuer_treasury"], out["held_buybacks"] = comps[C_LOCK], comps[C_ISSUER], comps[C_HELD]
    out["circulating"] = circ
    out["gap_days"] = pd.Series(idx, index=idx).diff().dt.days.fillna(1).astype(int) - 1
    out = out.iloc[1:].copy()
    # ---- the flag and its cause ----
    gc = groups.get((name, "circulating_supply_used"))
    basis = pd.Series(dtype=str)
    if gc is not None and not gc.empty:
        basis = pd.Series(gc["source"].astype(str).map(
            lambda x: bw.trajectory.basis_of("circulating_supply_used", x)).values,
            index=pd.to_datetime(gc["date"]).dt.normalize())
        basis = basis[~basis.index.duplicated(keep="last")].reindex(span).ffill()
    leg_days = {m: set(d(m).index) for ms in kinds.values() for m in ms}
    flags, causes = [], []
    prev = idx[0]
    for day, row in out.iterrows():
        flag = bool(pd.notna(row["pct"]) and abs(row["pct"]) > config.FLOAT_RESIDUAL_LIMIT)
        why = []
        if flag:
            if not basis.empty and basis.get(day) != basis.get(prev):
                why.append(f"the circulating basis changed ({basis.get(prev)} -> {basis.get(day)}): a series break, "
                           "not a flow")
            if row["gap_days"]:
                why.append(f"the float was not formed on the {int(row['gap_days'])} day(s) before (a component "
                           "missing those days)")
            lacking = sorted(m for m, ds in leg_days.items() if ds and not any(
                prev < x <= day for x in ds))
            if lacking:
                why.append("a measured flow has no row that day (not measured, never 0): " + ", ".join(lacking))
            if not why:
                why.append(f"circulating moved by {row['residual']:+,.0f} with no measured emission, unlock or burn "
                           "(issuer move not measured, or a flow with no series)")
        flags.append(flag)
        causes.append("; ".join(why))
        prev = day
    out["flag"], out["cause"] = flags, causes
    return out


def _sum_rows(mv: pd.DataFrame, ff_all: pd.Series, start, end) -> dict | None:
    """The movements of (start, end] summed, with the opening and closing float; None where no day is formed in it."""
    w = mv[(mv.index > start) & (mv.index <= end)]
    if w.empty:
        return None
    before = ff_all[ff_all.index <= start]
    if len(before):
        opening = float(before.iloc[-1])
        w = mv[(mv.index > before.index[-1]) & (mv.index <= end)]
    else:
        opening = float(w["open"].iloc[0])
    row = {k: float(w[k].sum()) for k in (*LINE_KEYS, "residual")}
    row.update({"open": opening, "close": float(w["close"].iloc[-1]), "days": int(len(w)),
                "flagged": int(w["flag"].sum()), "first": w.index[0], "last": w.index[-1],
                "exchange": (float(w["exchange"].sum()) if w["exchange"].notna().any() else None),
                "named": (float(w["named"].sum()) if "named" in w and w["named"].notna().any() else None),
                "causes": [f"{d.date()}: {r['pct']:+.2%} — {r['cause']}" for d, r in w[w["flag"]].iterrows()],
                "staked_locked": float(w["staked_locked"].iloc[-1]), "issuer_treasury": float(w["issuer_treasury"].iloc[-1]),
                "held_buybacks": float(w["held_buybacks"].iloc[-1]), "circulating": float(w["circulating"].iloc[-1])})
    row["expected"] = row["open"] + sum(row[k] for k in LINE_KEYS)
    row["pct"] = row["residual"] / row["open"] if row["open"] else None
    return row


def _status(row: dict | None, why_none: str) -> str:
    if row is None:
        return why_none
    if row["flagged"]:
        return (f"CHECK — {row['flagged']} day(s) with a residual beyond ±{config.FLOAT_RESIDUAL_LIMIT:.1%} of the day "
                f"before's float: " + " | ".join(row["causes"][:3])
                + (f" | and {len(row['causes']) - 3} more (the daily block lists the last "
                   f"{config.FLOAT_DAILY_DAYS} days)" if len(row["causes"]) > 3 else ""))[:1500]
    return f"OK — every day balances within ±{config.FLOAT_RESIDUAL_LIMIT:.1%} ({row['days']} day(s) formed)"


STATEMENT = "Free float"


def statements(groups: dict, asof) -> list[dict]:
    """The Flows sheet's "Free float" statement per project: Q0..Q-4, each the SUM OF ITS DAYS (rows shaped as
    build_workbook.FLOWS_HEAD)."""
    import build_workbook as bw                                          # noqa: PLC0415
    labels = bw.trajectory.LABELS
    out = []
    for p in bw.scoped_projects():
        name = p["name"]
        mv = MOVES.get(name)
        ff_all = bw._daily(groups.get((name, FF)))
        why = MISSING.get(name) or "n/a — the free float is formed on fewer than two days"
        qs = [(None if mv is None else _sum_rows(mv, ff_all, *bw._flow_window(asof, k))) for k in range(5)]
        if mv is None and name not in PARTS:
            continue                                     # nothing stored for the project at all: no statement
        g = lambda key: [None if q is None else q.get(key) for q in qs]  # noqa: E731
        pt = PARTS.get(name) or {}
        ex = config.FLOAT_EXCHANGE_SENDS.get(name)
        rows = [("Opening free float", g("open"), "tokens", FF + " (start of the quarter)", ""),
                *[(lab, g(k), "tokens", {
                    "emissions": "the circulating statement's minted / emitted legs",
                    "unlocks": "unlock_release_tokens (every release from issuer wallets; else the statement's release legs)",
                    "unstaked": C_LOCK + " (falls)", "staked": C_LOCK + " (rises)",
                    "held_bought": C_HELD + " (rises)", "held_returned": C_HELD + " (falls)",
                    "burns": "the circulating statement's burn legs",
                    "issuer": "−Δ " + C_ISSUER + " − returned to issuer wallets − unlocks out of issuer wallets inside "
                              "circulating"}[k], "sum of the days") for k, lab in LINES],
                ("Expected closing (opening + the movements)", g("expected"), "tokens", "", ""),
                ("Closing free float", g("close"), "tokens", FF + " (end of the quarter)", ""),
                ("Residual (closing − expected) = the sum of the days' residuals", g("residual"), "tokens", "",
                 "the part of the circulating change no measured emission, unlock or burn explains"),
                ("Residual ÷ opening", g("pct"), "pct", "", ""),
                ("Days flagged (residual beyond ±0.5% of the day before's float)", g("flagged"), "days", "", ""),
                ("Status", [_status(q, why) for q in qs], "text", "",
                 "daily identity: yesterday's float + the day's movements = today's float"),
                (EXCHANGE_LINE, g("exchange"), "tokens", ex or "— (no series on this project)",
                 "" if ex else "no exchange-tagged outflow series is stored for this project"),
                *([(nm["label"], g("named"), "tokens", nm["series"],
                    " | ".join(n for k in range(5) for n in nm["names"](*bw._flow_window(asof, k)))[:1500])]
                  if (nm := config.FLOAT_NAMED_MOVES.get(name)) else []),
                ("component at close: staked / locked / earning", g("staked_locked"), "tokens", C_LOCK,
                 pt.get("staked_locked", "")),
                ("component at close: issuer, foundation and treasury holdings", g("issuer_treasury"), "tokens",
                 C_ISSUER, pt.get("issuer_treasury", "")),
                ("component at close: held buybacks", g("held_buybacks"), "tokens", C_HELD, pt.get("held_buybacks", "")),
                ("memo: circulating at close", g("circulating"), "tokens", "circulating_supply_used", "")]
        for line, vals, unit, series, note in rows:
            out.append({"Project": name, "Statement": STATEMENT, "Line": line,
                        **{lab: v for lab, v in zip(labels, vals)}, "Unit": unit, "Series": series, "Note": note})
    return out


BLOCK_HEAD = ["Project", "Statement", "Period (to)", "Opening free float", *[lab for _k, lab in LINES],
              "Closing free float", "Residual", "Residual ÷ opening", "Flag / cause", "Staked / locked",
              "Issuer / treasury", "Held buybacks", "Sent to exchange-tagged addresses (named line)",
              "Named insider float movement (tokens, inside the lines above)", "Named insider float movement — what"]


def period_table(name: str, groups: dict, asof, days: int, n: int) -> list[list]:
    """`n` trailing blocks of `days` days ending on asof (days=1: the daily view; 7: the weekly view) — each the sum
    of its days — as rows shaped like BLOCK_HEAD (newest first)."""
    import build_workbook as bw                                          # noqa: PLC0415
    mv = MOVES.get(name)
    if mv is None:
        return []
    asof = pd.Timestamp(asof).normalize()
    ff_all = bw._daily(groups.get((name, FF)))
    what = f"{STATEMENT} — daily" if days == 1 else f"{STATEMENT} — weekly"
    out = []
    for i in range(n):
        end = asof - pd.Timedelta(days=days * i)
        row = _sum_rows(mv, ff_all, end - pd.Timedelta(days=days), end)
        if row is None:
            out.append([name, what, str(end.date()), "n/a — the float is not formed on this day" if days == 1
                        else "n/a — the float is not formed in this week"])
            continue
        out.append([name, what, str(end.date()), row["open"], *[row[k] for k in LINE_KEYS], row["close"],
                    row["residual"], row["pct"],
                    ("FLAG — " + " | ".join(row["causes"][:2])) if row["flagged"] else "ok",
                    row["staked_locked"], row["issuer_treasury"], row["held_buybacks"], row["exchange"],
                    row.get("named"),
                    "; ".join(nm["names"](end - pd.Timedelta(days=days), end)) if (nm := config.FLOAT_NAMED_MOVES.get(
                        name)) else None])
    return out


# ---------------------------------------------------------------------------------------------------------------------
# THE CHECK JAKE ASKED FOR (R20 item 1): no tab shows a float different from the series.
BANNED_FLOAT_NAMES = r"tradeable float|effective float|float after staking|resulting float"
FLOAT_HEADER = r"^free float \(tokens\)"


def _table(ws) -> tuple[list, list]:
    """(headers, rows) of a tab's first project table: the first row within 12 whose column A is 'Project', and the
    rows under it down to the next 'Project' header row (a second table) or the end."""
    rows = list(ws.iter_rows(values_only=True))
    hr = next((i for i, r in enumerate(rows[:12]) if r and str(r[0] or "").strip() == "Project"), None)
    if hr is None:
        return [], []
    body = []
    for r in rows[hr + 1:]:
        if r and str(r[0] or "").strip() == "Project":
            break
        if r and r[0] is not None:
            body.append(r)
    return [str(h or "") for h in rows[hr]], body


def check_workbook(audit_path, reader_path=None, rel: float = 1e-9) -> list[str]:
    """Every float a tab shows against the ONE series (Data: <project>|free_float_tokens, "now"). Returns the
    violations (empty = clean):
      - a header that names another float ("tradeable float", "effective float", "float after staking", "resulting
        float") on any tab of either file;
      - a "Free float (tokens)" cell — audit A1 / A2 / A3 / CP, reader A-tabs — whose number is not the series', or that
        shows a number where the series holds none, or no number where the series holds one;
      - CP's "Free float ÷ circulating" x circulating, and Summary's "Total dilution, tokens a year" ÷ "Total
        dilution, % of float", where they are numbers, against the series."""
    import re                                                            # noqa: PLC0415

    import openpyxl                                                      # noqa: PLC0415
    out = []
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
    wb = openpyxl.load_workbook(audit_path, data_only=True, read_only=True)
    data = list(wb["Data"].iter_rows(values_only=True))
    hk = [str(h or "") for h in data[0]]
    k_i = next(i for i, h in enumerate(hk) if h.startswith("Key"))
    n_i = next(i for i, h in enumerate(hk) if h.startswith("Now"))
    series = {str(r[k_i]).split("|")[0]: r[n_i] for r in data[1:]
              if r and str(r[k_i] or "").endswith("|" + FF) and num(r[n_i])}

    def close(a, b, tol=rel):
        return abs(a - b) <= tol * max(abs(a), abs(b), 1.0)

    def tab(book, wsname, file):
        heads, body = _table(book[wsname])
        for h in heads:
            if re.search(BANNED_FLOAT_NAMES, h, re.I):
                out.append(f"{file} {wsname}: the header '{h[:80]}' names a float other than the one free float")
        col = lambda pat: next((j for j, h in enumerate(heads) if re.search(pat, h, re.I)), None)  # noqa: E731
        for j, h in enumerate(heads):
            if not re.search(FLOAT_HEADER, h, re.I):
                continue
            for r in body:
                name, v = str(r[0]).strip(), r[j] if j < len(r) else None
                s = series.get(name)
                if num(v) and s is None:
                    out.append(f"{file} {wsname} {name}: shows a free float {v:,.2f} but the series holds none")
                elif num(v) and not close(v, s):
                    out.append(f"{file} {wsname} {name}: free float {v:,.2f} is not the series' {s:,.2f}")
                elif not num(v) and s is not None and v not in (None, ""):
                    out.append(f"{file} {wsname} {name}: the series holds {s:,.2f} but the tab shows '{str(v)[:60]}'")
        jr, jc = col(r"^free float ÷ circulating"), col(r"^circulating supply \(tokens\)")
        jt, jp = col(r"^total dilution, tokens a year"), col(r"^total dilution, % of float")
        for r in body:
            name = str(r[0]).strip()
            s = series.get(name)
            if jr is not None and jc is not None and num(r[jr]) and num(r[jc]):
                if s is None or not close(r[jr] * r[jc], s, 1e-6):
                    out.append(f"{file} {wsname} {name}: free float ÷ circulating x circulating = "
                               f"{r[jr] * r[jc]:,.2f}, not the series' {s if s is None else format(s, ',.2f')}")
            if jt is not None and jp is not None and num(r[jt]) and num(r[jp]) and r[jp]:
                if s is None or not close(r[jt] / r[jp], s, 1e-6):
                    out.append(f"{file} {wsname} {name}: total dilution tokens ÷ % of float = {r[jt] / r[jp]:,.2f}, "
                               f"not the series' {s if s is None else format(s, ',.2f')}")

    for wsname in wb.sheetnames:
        if wsname.startswith(("Master", "A1", "A2", "A3", "A4", "CP")):
            tab(wb, wsname, "audit")
    if reader_path is not None:
        rb = openpyxl.load_workbook(reader_path, data_only=True, read_only=True)
        for wsname in rb.sheetnames:
            if wsname.startswith(("Summary", "A1", "A2", "A3", "A4", "Comparison", "Leaderboard")):
                tab(rb, wsname, "reader")
    return out


def check_nets(audit_path, rel: float = 1e-9) -> list[str]:
    """The two nets on CP, each against its own sides on the same row (R20 item 5; R21): NET HOLDER YIELD = HOLDER VALUE
    YIELD − DILUTION, % OF MARKET CAP; NET FLOAT PRESSURE = BUYING PRESSURE ON FLOAT + BURNS ON FLOAT − TOTAL DILUTION ÷
    free float − BOUGHT TOKENS RETURNED TO THE FLOAT (a buying pressure that is a by-design code counts 0). Rows where a
    side is not a number are skipped."""
    import openpyxl                                                      # noqa: PLC0415
    wb = openpyxl.load_workbook(audit_path, data_only=True, read_only=True)
    ws = next(wb[n] for n in wb.sheetnames if n.startswith("CP"))
    heads, body = _table(ws)
    col = lambda pre: next((j for j, h in enumerate(heads) if h.startswith(pre)), None)  # noqa: E731
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
    hvy, dmc, nhy = col("HOLDER VALUE YIELD"), col("DILUTION, % OF MARKET CAP"), col("NET HOLDER YIELD")
    buy, nfp = col("BUYING PRESSURE ON FLOAT"), col("NET FLOAT PRESSURE")
    brn, ret = col(BURNS_PREFIX), col(RETURNED_PREFIX)
    dil = next((j for j, h in enumerate(heads) if h.startswith("TOTAL DILUTION = outflows") and h.endswith("÷ free float")),
               None)
    out = []
    if None in (hvy, dmc, nhy, buy, nfp, dil, brn, ret):
        return ["CP: a column of the two nets is missing"]
    for r in body:
        name = str(r[0]).strip()
        if num(r[hvy]) and num(r[dmc]) and num(r[nhy]) and abs(r[hvy] - r[dmc] - r[nhy]) > rel:
            out.append(f"{name}: net holder yield {r[nhy]:.6f} is not holder value yield {r[hvy]:.6f} − dilution % of "
                       f"market cap {r[dmc]:.6f}")
        b = 0.0 if name in config.BUYING_PRESSURE_BY_DESIGN else r[buy]
        sides = (b, r[brn], r[dil], r[ret])
        if all(num(x) for x in sides) and num(r[nfp]) and abs(b + r[brn] - r[dil] - r[ret] - r[nfp]) > rel:
            out.append(f"{name}: net float pressure {r[nfp]:.6f} is not buying pressure {b:.6f} + burns {r[brn]:.6f} − "
                       f"total dilution ÷ free float {r[dil]:.6f} − returned {r[ret]:.6f}")
        if num(r[nfp]) and not all(num(x) for x in sides):
            out.append(f"{name}: net float pressure is a number while a side is not")
    return out


# ---------------------------------------------------------------------------------------------------------------------
# R21 (Jake 2026-11-25, item 0): THE CHECK THAT EVERY PROJECT'S BURN SERIES IS IN ITS NET FLOAT PRESSURE.
BURNS_PREFIX = "BURNS ON FLOAT"
RETURNED_PREFIX = "BOUGHT TOKENS RETURNED TO THE FLOAT"


def check_burns(audit_path) -> list[str]:
    """Every project's declared burn series (config.burn_series) is in its net float pressure on CP. Returns the
    violations (empty = clean):
      - CP has no BURNS ON FLOAT column, or NET FLOAT PRESSURE does not read it on the row;
      - a declared burn series the Data sheet holds for the project is not read by the row's burns cell;
      - a project that declares a burn series shows a declared 0 (no series read) in its burns cell;
      - a burn-named token flow on the Data sheet of one of the R21 projects is neither a declared burn series nor
        declared not-in-the-net with its reason (config.BURN_NOT_IN_NET).
    Reads the formulas (the audit file as written), so it needs no recalculation."""
    import openpyxl                                                      # noqa: PLC0415
    wb = openpyxl.load_workbook(audit_path, read_only=True)
    # a series is STORED for a project where its Data row holds points (every metric has a row, most of them empty)
    dh = [str(h or "") for h in next(wb["Data"].iter_rows(min_row=1, max_row=1, values_only=True))]
    j_pts = next((j for j, h in enumerate(dh) if h == "Points"), None)
    keys = set()
    for r in wb["Data"].iter_rows(min_row=2, values_only=True):
        if r and r[0] and (j_pts is None or (isinstance(r[j_pts], (int, float)) and r[j_pts] > 0)):
            keys.add(str(r[0]))
    ws = next(wb[n] for n in wb.sheetnames if n.startswith("CP"))
    rows = list(ws.iter_rows())
    hr = next((i for i, r in enumerate(rows[:12]) if r and str(r[0].value or "").strip() == "Project"), None)
    if hr is None:
        return ["CP: no project table"]
    heads = [str(c.value or "") for c in rows[hr]]
    jb = next((j for j, h in enumerate(heads) if h.startswith(BURNS_PREFIX)), None)
    jn = next((j for j, h in enumerate(heads) if h.startswith("NET FLOAT PRESSURE")), None)
    if jb is None or jn is None:
        return ["CP: the BURNS ON FLOAT or NET FLOAT PRESSURE column is missing"]
    out = []
    for r in rows[hr + 1:]:
        if not r or str(r[0].value or "").strip() in ("", "Project"):
            continue
        name = str(r[0].value).strip()
        if name not in config.PROJECT_BY_NAME:
            continue
        bcell, ncell = r[jb], r[jn]
        f_b, f_n = str(bcell.value or ""), str(ncell.value or "")
        declared = [b["metric"] for b in config.burn_series(name)]
        stored = [m for m in declared if f"{name}|{m}" in keys]
        if f_n.startswith("=") and bcell.coordinate not in f_n.replace("$", ""):
            out.append(f"{name}: net float pressure does not read its burns cell {bcell.coordinate}")
        if not f_b.startswith("="):
            continue                     # a text reason (the free float not formed, a series with no row): the net is n/a
        for m in stored:
            if f'|{m}"' not in f_b:
                out.append(f"{name}: the burn series {m} is stored but not in net float pressure's burns cell")
        if declared and f_b.replace(" ", "") in ("=0", "0"):
            out.append(f"{name}: declares burn series ({', '.join(declared)}) but its burns cell is a declared 0")
        if name in config.BURN_PROJECTS_R21:
            for k in keys:
                pn, _, m = k.partition("|")
                md = config.METRICS.get(m) or {}
                if pn == name and "burn" in m and md.get("kind") == "flow" and md.get("unit") == "tokens" \
                        and m not in declared and (name, m) not in config.BURN_NOT_IN_NET:
                    out.append(f"{name}: the burn series {m} is neither in net float pressure nor declared out of it "
                               "(config.BURN_NOT_IN_NET)")
    return out
