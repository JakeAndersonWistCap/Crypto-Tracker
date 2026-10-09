"""
credibility.py — the rows of the Credibility tab (Jake, 2026-10-05).

One row per headline cell on A1-A4 for every project in config.CREDIBILITY_PROJECTS, plus the input
rows each project declares (price, circulating, burn, ...). Each row sets OUR figure (the headline cell
itself, by address, or the Data tab's value for an input) beside an INDEPENDENT reference:

  PASS          an independent reference agrees within the stated tolerance
  CHECK         it disagrees — or a reference exists but was not read (the note says which, and how)
  FRESH-only    the only reference is a re-read of our own source: it shows the store is current,
                never that the figure is right. NEVER counted as PASS.
  UNVERIFIABLE  no independent source exists — the note says why
  N/A           the cell carries no figure BY DESIGN for this project (no buyback, issuance on the other
                basis, not a chain ...) — counted apart, so it never pads either column

PASS / CHECK / FRESH-only are FORMULAS on the tab (our value moves with the workbook); UNVERIFIABLE
and the static CHECKs (a reference that exists but is not wired) are literals with their reason.

Reference kinds (config.CREDIBILITY, per project, keyed by headline id or "in_*" input id):
  {"metric": m, "window": w}          another stored series (another source), same window
  {"metric": m, "align_to": ours}     m's value ON THE DAY of our metric's latest point
  {"formula": name}                   computed here from stored inputs (FORMULAS) — says how
  {"manual": {...}}                   a by-hand reading on record (value, read_on, read_by, source)
  {"verdict": "UNVERIFIABLE"|"CHECK", "why": ..., "resolve": ...}   no reference value
  "same_source": True                 the reference re-reads our own source -> FRESH-only
  "scale": k                          multiply the reference (units, a published net-of-fee rate)
"""
from __future__ import annotations

import math
import re

import pandas as pd

import config

VERDICTS = ("PASS", "CHECK", "FRESH-only", "UNVERIFIABLE", "N/A")

# THE FINISH LINE (Jake, overnight sign-off round 2026-10-07): a project is SIGNED OFF when every row is one of
#   PASS                   independently confirmed within tolerance
#   N/A                    does not apply by design
#   DOCUMENTED LIMITATION  no free second source exists — the reason AND what would upgrade it are written down
#   MATURING               fills by a stated date (a forward-only series)
#   VERIFIED FINDING       our figure is confirmed and the gap is a real-world fact, with the evidence
# Anything else (CHECK, FRESH-only, UNVERIFIABLE) is OPEN, with its reason. A row becomes a LIMITATION or FINDING
# only with its evidence on the spec ("evidence", plus "upgrade" / "until") — config._c_lim / _c_find / _c_mat refuse
# one without.
SIGNED = ("PASS", "N/A", "DOCUMENTED LIMITATION", "MATURING", "VERIFIED FINDING")


def signed(verdict) -> bool:
    """Whether a verdict (as displayed) counts towards sign-off."""
    v = str(verdict or "")
    return any(v.startswith(s) for s in SIGNED)


def signoff(rows: list, verdicts: list) -> dict:
    """{project: {"status": "SIGNED OFF"|"OPEN", "open": [(id, verdict, note)], "counts": {category: n}}} from the
    built rows and their displayed verdicts (same order)."""
    out: dict = {}
    for r, v in zip(rows, verdicts):
        d = out.setdefault(r["project"], {"open": [], "counts": {}})
        v = str(v or "")
        cat = next((s for s in SIGNED if v.startswith(s)), "OPEN")
        d["counts"][cat] = d["counts"].get(cat, 0) + 1
        if cat == "OPEN":
            d["open"].append((r["id"], v, (r.get("note") or "")[:300]))
    for d in out.values():
        d["status"] = "SIGNED OFF" if not d["open"] else "OPEN"
    return out

DERIVED_WHY = ("a ratio we derive — no third party publishes it; its inputs are checked in this "
               "project's input rows below")


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _series(long, project: str, metric: str) -> pd.Series:
    if long is None or long.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    g = long[(long.project == project) & (long.metric == metric)]
    if g.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    return g.assign(d=g["date"].dt.normalize()).groupby("d")["value"].last().sort_index()


# ------------------------------------------------------------------ computed references
def _eth_issuance_formula(p, rows, long, asof, **_):
    """The protocol's issuance curve: ~166.32 x sqrt(ETH staked) per year at full participation
    (base reward factor 64 x 4 base rewards per epoch x 225 epochs/day x 365 / sqrt), over our Q0
    covered days. An upper bound — missed attestations and penalties take it below."""
    staked = _num((rows.get(f"{p}|beacon_chain_eth") or {}).get("now"))
    cov = _num((rows.get(f"{p}|gross_issuance_tokens") or {}).get("q0_covered_days")) or 90.0
    if not staked:
        return None, None, "no staked-ETH figure (beacon_chain_eth) stored"
    v = 166.32 * math.sqrt(staked) * cov / 365.0
    return v, (rows.get(f"{p}|beacon_chain_eth") or {}).get("latest_date"), (
        f"166.32 x sqrt({staked:,.0f} ETH staked) x {cov:.0f}/365 — the issuance curve at full participation")


def _eth_net_formula(p, rows, long, asof, issuance="gross_issuance_tokens", burn="gross_burn_tokens",
                     net_metric="net_supply_change_tokens", side="ref", **_):
    """ETHEREUM'S NET SUPPLY CHANGE FROM INDEPENDENT SOURCES, OVER OUR OWN COMMON DAYS (Jake's run 2026-10-07: the
    reference was CoinGecko's d(circulating) across Q0 and read 1.43M ETH — impossible; its stored history is not
    one consistent series). Ours is issuance - burn on the days BOTH our series hold (net_supply_change_tokens). The
    reference takes the SAME days: the issuance curve at THAT DAY's staked ETH (beacon_chain_eth, validatorqueue's
    daily history, the nearest reading on or before the day; 166.32 x sqrt(staked)/365, full participation: an upper
    bound by ~1-2%) minus the day's burn — DefiLlama's burned fees / same-day price, or, on a day DefiLlama or the
    price is missing, the protocol's own BurntFees counter differenced (burn_cumulative_tokens). Jake's probes15
    (root J): both legs from stored series, so a missing DefiLlama day no longer leaves the row without a reference."""
    lo, hi = _q0(asof)
    # IDENTICAL DAYS (Jake's run 2026-10-08 08:37: ours 9 days, the curve 6): the issuance is the PRIMARY series the
    # headline uses (first-party rows only, config ISSUANCE_PRIMARY source_prefix), and a day counts only where staked
    # ETH, issuance and burn are ALL stored that day — the same rule as the net-change view
    # (build_workbook._net_common_views, net_change_common_days.require)
    # (the row reads the VIEWS — use_views — so the issuance here is the primary view the headline differences)
    si, sb = _series(long, p, issuance), _series(long, p, burn)
    st = _series(long, p, "beacon_chain_eth")
    if st.empty:
        return None, None, "no staked-ETH figure (beacon_chain_eth) stored"
    # THE HEADLINE'S OWN DAYS (Jake's run 2026-10-08 11:27: the working printed ours as the full 9-day issuance
    # 26,838 while the curve covered 6 days). The days are those of the headline's net-change view
    # (net_supply_change_tokens, built from issuance, burn and staked ETH on the same day), so ours, the legs and the
    # curve are summed over exactly the days the headline cell sums — never a wider set from another view.
    nv = _series(long, p, net_metric) if net_metric else pd.Series(dtype=float)
    pool = set(nv.index) if not nv.empty else set(si.index) & set(sb.index) & set(st.index)
    days = sorted(d for d in pool & set(si.index) & set(sb.index) & set(st.index) if lo < d <= hi)
    if not days:
        return None, None, f"no day in Q0 on which staked ETH, {issuance} and {burn} are all stored"
    px, rev = _series(long, p, "price_usd"), _series(long, p, "revenue_usd")
    cum = _series(long, p, "burn_cumulative_tokens")
    # EACH ISSUANCE ROW COVERS A SPAN, NOT ALWAYS ONE DAY (Jake's run 2026-10-09 ~10:15: "OURS, same 7 day(s): issuance
    # 29,812 (4,259/day, +41.4%)" — the full 10-day Q0 sum over 7 days). The issuance is d(total_supply_protocol) +
    # burn; Etherscan's supply figure does not move every day, and on the day it moves the whole change since it LAST
    # moved is stored on that day, with the burn of the days in between (fetch._burns_since_last_move). So a row dated d
    # covers (U, d], U the first day of the flat run before d — or the [span=Nd] its source carries. Both sides are
    # summed over the union of the rows' spans: ours = those rows (issuance) − our burn on every covered day; the
    # reference = the curve and the reference burn on the SAME covered days. A row whose span lacks a burn on any day
    # is left out of both, and named.
    tsp = _series(long, p, "total_supply_protocol")
    src_of, bsrc_of = {}, {}
    if long is not None and not long.empty and "source" in long.columns:
        g = long[(long.project == p) & (long.metric == issuance)]
        src_of = dict(zip(g["date"].dt.normalize(), g["source"].astype(str)))
        gb = long[(long.project == p) & (long.metric == burn)]
        bsrc_of = dict(zip(gb["date"].dt.normalize(), gb["source"].astype(str)))
    # OUR BURN CARRIES SPANS TOO (Jake's run 2026-10-09 11:41, item 4: the 10-05 issuance row spans 10-02..10-05 and so
    # does the 10-05 burn row, [span=4d]; asking for a burn ROW on 10-02 left the whole span out — 17,022 over 6 days
    # where the headline covers 10). A day is covered by our burn when a burn row's own span contains it.
    burn_cover = {}
    for bd in sb.index:
        mb = re.search(r"\[span=(\d+)d\]", bsrc_of.get(bd, ""))
        for x in pd.date_range(bd - pd.Timedelta(days=(int(mb.group(1)) if mb else 1) - 1), bd):
            burn_cover[x] = bd

    def span_of(d):
        m = re.search(r"\[span=(\d+)d\]", src_of.get(d, ""))
        if m:
            return list(pd.date_range(d - pd.Timedelta(days=int(m.group(1)) - 1), d))
        prev = tsp[tsp.index < d]
        if prev.empty:
            return [d]
        v, u = float(prev.iloc[-1]), prev.index[-1]
        for day, val in reversed(list(prev.items())):
            if abs(float(val) - v) > config.FLAT_TOLERANCE_TOKENS:
                break
            u = day
        return list(pd.date_range(u + pd.Timedelta(days=1), d))

    def ref_burn(d):
        if d in px.index and px.loc[d] and d in rev.index:
            return float(rev.loc[d] / px.loc[d]), "llama"
        if d in cum.index and (d - pd.Timedelta(days=1)) in cum.index:
            return float(cum.loc[d] - cum.loc[d - pd.Timedelta(days=1)]), "counter"
        return None, None

    iss = brn = o_iss = o_brn = 0.0
    n_llama = n_counter = 0
    used, covered, dropped = [], [], []
    for d in days:
        span = span_of(d)
        legs = [(x, ref_burn(x), x in burn_cover) for x in span]
        bad_ref = [x for x, (rb, _k), _c in legs if rb is None]
        bad_ours = [x for x, _r, covered in legs if not covered]
        if bad_ref or bad_ours:
            x = (bad_ours or bad_ref)[0]
            dropped.append(f"{d.date()} (span {len(span)}d; no {'burn of ours' if bad_ours else 'reference burn'} "
                           f"covering {x.date()})")
            continue
        for x, (rb, kind), _ in legs:
            before = st[st.index <= x]
            s0 = float(before.iloc[-1]) if len(before) else float(st.iloc[0])
            iss += 166.32 * math.sqrt(s0) / 365.0
            brn += rb
            n_llama += kind == "llama"
            n_counter += kind == "counter"
        # our burn rows dated inside the span (each carries its own span, all inside this one)
        o_brn += float(sum(float(sb.loc[bd]) for bd in sorted({burn_cover[x] for x in span})))
        o_iss += float(si.loc[d])
        used.append((d, len(span), float(si.loc[d])))
        covered += span
    if not used:
        return None, None, (f"no common day whose issuance span has a burn on every day: {'; '.join(dropped)}")
    n = len(covered)
    rows_txt = "; ".join(f"{d:%m-%d} {v:,.0f}" + (f" ({k}d)" if k > 1 else "") for d, k, v in used)
    gap = lambda a, b: f"{(a / b - 1):+.1%}" if b else "n/a"             # noqa: E731
    if side == "ours":                    # the judged OURS: issuance − burn summed over exactly these days
        return o_iss - o_brn, str(used[-1][0].date()), (f"ours: {len(used)} issuance row(s) covering {n} day(s) "
                                                        f"{covered[0].date()}..{covered[-1].date()}: issuance "
                                                        f"{o_iss:,.0f} − burn {o_brn:,.0f}")
    return iss - brn, str(used[-1][0].date()), (
        f"issuance curve {iss:,.0f} ETH (166.32 x sqrt(that day's staked ETH) over {n} day(s)) − burn "
        f"{brn:,.0f} ETH (DefiLlama / price on {n_llama} day(s)"
        + (f", BurntFees counter on {n_counter}" if n_counter else "") + f"), on the {n} day(s) our "
        f"{len(used)} common-day issuance row(s) cover, {covered[0].date()}..{covered[-1].date()}. OURS, same {n} "
        f"day(s): issuance {o_iss:,.0f} ({o_iss / n:,.0f}/day, {gap(o_iss, iss)} vs the curve's {iss / n:,.0f}/day) − "
        f"burn {o_brn:,.0f} ({gap(o_brn, brn)} vs the reference burn) = {o_iss - o_brn:,.0f}. OUR ISSUANCE ROWS (a row "
        f"spans the days since the supply last moved): {rows_txt}"
        + (f". Left out: {'; '.join(dropped)}" if dropped else ""))


def _base_reward_ceiling(p, rows, long, asof, cover_metric="pool_release_tokens", units="supply_units", **_):
    """GEODNET'S BASE-REWARD CEILING, A PLAUSIBILITY BOUND (Jake, 2026-10-07, docs.geodnet.com 'Tokenomics'): the
    base reward per triple-band station per day (config per_miner_reward_schedule: 12 GEOD to 2026-06-30, 6 from
    2026-07-01, halving each 30 June) x active stations (supply_units, the nearest stored reading) summed over the
    days our series holds in Q0. The actual reward applies data-quality, hex, SuperHex and performance rules to the
    base, so it moves either way of this — a bound on scale, not a reconciliation."""
    lo, hi = _q0(asof)
    cov = _series(long, p, cover_metric)
    days = [d for d in cov.index if lo < d <= hi]
    if not days:
        return None, None, f"no {cover_metric} day in Q0"
    st = _series(long, p, units)
    if st.empty:
        return None, None, f"no {units} reading stored"
    steps = [s0 for s0 in (config.PROJECT_BY_NAME[p].get("per_miner_reward_schedule") or {}).get("steps", ())
             if "tokens_per_miner_per_day" in s0]
    tot, used, rates = 0.0, set(), set()
    for d in days:
        step = next((s0 for s0 in steps if pd.Timestamp(s0["from"]) <= d <= pd.Timestamp(s0["until"])), None)
        if step is None:
            return None, None, f"no base-reward step covers {d.date()}"
        before = st[st.index <= d]
        u_day, u = (before.index[-1], before.iloc[-1]) if len(before) else (st.index[0], st.iloc[0])
        used.add(str(u_day.date()))
        rates.add(step["tokens_per_miner_per_day"])
        tot += float(step["tokens_per_miner_per_day"]) * float(u)
    return tot, str(days[-1].date()), (
        f"PLAUSIBILITY BOUND: base reward {'/'.join(f'{r:g}' for r in sorted(rates))} GEOD/station/day x {units} "
        f"(reading(s) of {', '.join(sorted(used))}, the nearest on or before each day) over our {len(days)} "
        f"{cover_metric} day(s) {days[0].date()}..{days[-1].date()} — before data-quality / hex / SuperHex / "
        f"performance rules")


def payday_yield(flow, price, stake, lo, hi, year=365.0, epoch_days=None) -> dict | None:
    """THE PRICE CONVENTION FOR EVERY PROTOCOL YIELD WITH NON-NATIVE REWARDS (Jake, 2026-10-09): rewards are put in the
    staked token at the PAYMENT-DAY price — tokens = sum over the window's days of $paid_d / price_d — and divided by
    the stake IN TOKENS over the same window (the mean of the stake's stored days in it), annualised over the days
    counted. The dollar yield on the same basis is the same number: $paid / (stake x the payment-weighted price
    $paid / tokens), so the price cancels. A day with a payment and no same-day price is left out of both the tokens
    and the day count, and named. Series in, one dict out — the workbook view and the Credibility rows call this one
    function. None when the window has no priced payment day or no stake.

    PER-EPOCH STAKE (`epoch_days`, Aerodrome — Jake's run 2026-10-09 ~14:10, 1c: "mean stake 1,018,617,757 over its 1
    stored day"): each paid day's tokens are divided by ITS epoch's stake — the reading on the epoch's start (Thursday
    00:00 UTC; Voter.totalWeight archive-read at the epoch-start block), else the first reading inside that epoch,
    else the nearest reading at all (named as borrowed) — and summed: value = sum(tokens_d / stake_d) x year / days."""
    if flow is None or price is None or stake is None:
        return None
    if epoch_days:
        return _payday_yield_epochs(flow, price, stake, lo, hi, year, int(epoch_days))
    fl = flow[(flow.index > lo) & (flow.index <= hi)]
    px = price[(price.index > lo) & (price.index <= hi)]
    st = stake[(stake.index > lo) & (stake.index <= hi)]
    if fl.empty or st.empty:
        return None
    priced = [d for d in fl.index if d in px.index and px.loc[d]]
    no_price = [d for d in fl.index if d not in priced]
    if not priced:
        return None
    usd = float(fl.loc[priced].sum())
    tokens = float(sum(float(fl.loc[d]) / float(px.loc[d]) for d in priced))
    mean_stake = float(st.mean())
    if not mean_stake:
        return None
    n = len(priced)
    value = tokens * float(year) / n / mean_stake
    p_eff = usd / tokens if tokens else None
    how = (f"PAYMENT-DAY PRICES: ${usd:,.0f} paid over {n} day(s) {priced[0].date()}..{priced[-1].date()}, each day's "
           f"$ / that day's price = {tokens:,.0f} tokens (payment-weighted price ${p_eff:,.4f}) x {float(year):g}/{n} / "
           f"mean stake {mean_stake:,.0f} over its {len(st)} stored day(s) in the window = {value:.2%}"
           + (f"; left out, no same-day price: {', '.join(str(d.date()) for d in no_price)}" if no_price else ""))
    return {"value": value, "tokens": tokens, "usd": usd, "days": n, "p_eff": p_eff, "stake": mean_stake,
            "stake_days": len(st), "last": max(priced[-1], st.index[-1]), "no_price": no_price, "how": how}


def _payday_epochs(p):
    """The per-epoch stake setting of project p's payday yield (config token_yield.payday.stake_epoch_days), or None."""
    return ((((config.PROTOCOL_YIELD.get(p) or {}).get("token_yield") or {}).get("payday") or {})
            .get("stake_epoch_days"))


def _payday_illiquid(p):
    """The illiquid-token rule's stored metrics for project p's payday yield (config token_yield.payday.illiquid:
    {"fees": metric, "bribes": metric}), or None."""
    return ((((config.PROTOCOL_YIELD.get(p) or {}).get("token_yield") or {}).get("payday") or {}).get("illiquid"))


def illiquid_flow(flow, fees_x=None, bribes_x=None, epoch_days=7, lo=None, hi=None):
    """THE ILLIQUID-TOKEN RULE ON A DAILY FLOW (Jake 2026-10-09 17:05, Aerodrome's 2026-09-03 LAPTOP bribe): a
    non-native reward token counts at its quoted price only up to what a sale could take out of the DEX at payment;
    the excess per epoch (fetch/aero_voter.py: voter_rewards_illiquid_*_usd, dated the epoch's start) is taken out of
    the flow ON THE DAYS IT WAS BOOKED — bribes over the epoch's own days (DefiLlama books them on the NotifyReward
    day, inside the epoch), fees over the week before (fees credited at epoch E were earned in E-1) — an equal share
    a day, as the day of each deposit is not stored. Returns (flow less the excess, {"fees": $, "bribes": $ taken out
    of days in [lo, hi]}, the epochs read). Series in, never mutated."""
    if flow is None or flow.empty:
        return flow, {"fees": 0.0, "bribes": 0.0}, []
    out = flow.astype(float).copy()
    took, read = {"fees": 0.0, "bribes": 0.0}, set()
    n = int(epoch_days)
    for kind, x, shift in (("bribes", bribes_x, 0), ("fees", fees_x, -n)):
        if x is None or x.empty:
            continue
        for e, v in x.items():
            read.add(e)
            if not v:
                continue
            for d in pd.date_range(e + pd.Timedelta(days=shift), periods=n, freq="D"):
                if d in out.index:
                    out.loc[d] -= float(v) / n
                    if (lo is None or d > lo) and (hi is None or d <= hi):
                        took[kind] += float(v) / n
    return out, took, sorted(read)


def payday_headline(flow, price, stake, lo, hi, year=365.0, epoch_days=None, fees_x=None, bribes_x=None,
                    illiquid=False) -> dict | None:
    """payday_yield with the illiquid-token rule applied (`illiquid`: the config names the rule's metrics): the
    headline on the flow less each epoch's excess (illiquid_flow), with the figure before it, the dollars taken out
    and a labelled line in `how` — the workbook view and the Credibility rows call this one function."""
    gross = payday_yield(flow, price, stake, lo, hi, year, epoch_days)
    if not illiquid or gross is None:
        return gross
    adj, took, read = illiquid_flow(flow, fees_x, bribes_x, int(epoch_days or 7), lo, hi)
    in_q = [e for e in read if lo - pd.Timedelta(days=7) < e <= hi]
    if not in_q:
        return {**gross, "gross": gross["value"], "took": None, "how": gross["how"] + (
            "; ILLIQUID-TOKEN RULE NOT YET READ: no epoch of the window has voter_rewards_illiquid_*_usd stored "
            "(fetch/aero_voter.py / `python token_metrics.py --seed aero_epochs`), so the figure is at quoted prices")}
    r = payday_yield(adj, price, stake, lo, hi, year, epoch_days)
    if r is None:
        return gross
    x = took["fees"] + took["bribes"]
    return {**r, "gross": gross["value"], "took": took, "how": (
        f"{r['how']}; ILLIQUID-TOKEN RULE (Jake 2026-10-09): illiquid-token rewards excluded: ${x:,.0f} (fees "
        f"${took['fees']:,.0f}, bribes ${took['bribes']:,.0f}; {len(in_q)} epoch(s) read) — {gross['value']:.2%} "
        f"before the exclusion")}


def _tier_timed_out(source: str):
    """(ts, message) when the LAST run that logged `source` ended it with a tier timeout (fetch/__init__._abandon:
    "TIER TIMED OUT after ...", logged with no project), else None. Read-only from metrics.db's run_log (the store the
    build reads); None when unreadable."""
    import sqlite3
    try:
        import store as sm                                  # noqa: PLC0415
        con = sqlite3.connect(f"file:{sm.DB_PATH}?mode=ro", uri=True)
        try:
            last = con.execute("SELECT run_id FROM run_log WHERE source = ? ORDER BY ts DESC LIMIT 1",
                               (source,)).fetchone()
            if not last:
                return None
            hit = con.execute("SELECT ts, message FROM run_log WHERE source = ? AND run_id = ? AND status = 'failed' "
                              "AND message LIKE 'TIER TIMED OUT%' ORDER BY ts DESC LIMIT 1",
                              (source, last[0])).fetchone()
            return (hit[0], hit[1]) if hit else None
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return None


def _epoch_start(d, epoch_days=7):
    """The epoch a day falls in: Thursday 00:00 UTC starts (unix weeks) for 7-day epochs."""
    d = pd.Timestamp(d).normalize()
    return d - pd.Timedelta(days=(d.dayofweek - 3) % int(epoch_days))


def _payday_yield_epochs(flow, price, stake, lo, hi, year, epoch_days):
    fl = flow[(flow.index > lo) & (flow.index <= hi)]
    px = price[(price.index > lo) & (price.index <= hi)]
    st = stake.dropna().sort_index()
    if fl.empty or st.empty:
        return None
    priced = [d for d in fl.index if d in px.index and px.loc[d]]
    no_price = [d for d in fl.index if d not in priced]
    if not priced:
        return None
    by_epoch, used = {}, {"start": [], "later": [], "borrowed": []}
    for d in priced:
        e = _epoch_start(d, epoch_days)
        if e in by_epoch:
            continue
        inside = st[(st.index >= e) & (st.index < e + pd.Timedelta(days=epoch_days))]
        if e in st.index:
            by_epoch[e] = float(st.loc[e])
            used["start"].append(e)
        elif len(inside):
            by_epoch[e] = float(inside.iloc[0])
            used["later"].append((e, inside.index[0]))
        else:
            near = st.index[abs((st.index - e).days).argmin()]
            by_epoch[e] = float(st.loc[near])
            used["borrowed"].append((e, near))
    if not all(by_epoch.values()):
        return None
    usd = float(fl.loc[priced].sum())
    tok = {d: float(fl.loc[d]) / float(px.loc[d]) for d in priced}
    tokens = sum(tok.values())
    per_stake = sum(t / by_epoch[_epoch_start(d, epoch_days)] for d, t in tok.items())
    n = len(priced)
    value = per_stake * float(year) / n
    eff = tokens / per_stake if per_stake else None
    p_eff = usd / tokens if tokens else None
    how = (f"PAYMENT-DAY PRICES, PER-EPOCH STAKE: ${usd:,.0f} paid over {n} day(s) {priced[0].date()}..{priced[-1].date()}, "
           f"each day's $ / that day's price = {tokens:,.0f} tokens (payment-weighted price ${p_eff:,.4f}); each day "
           f"over its epoch's stake, x {float(year):g}/{n} = {value:.2%} (effective stake {eff:,.0f} over "
           f"{len(by_epoch)} epoch(s): {len(used['start'])} read at the epoch's start"
           + (f", {len(used['later'])} read later in the epoch ({', '.join(f'{e.date()} on {r.date()}' for e, r in used['later'])})"
              if used["later"] else "")
           + (f", {len(used['borrowed'])} BORROWED from the nearest reading ("
              + ", ".join(f"{e.date()} <- {r.date()}" for e, r in used["borrowed"]) + ")" if used["borrowed"] else "")
           + ")" + (f"; left out, no same-day price: {', '.join(str(d.date()) for d in no_price)}" if no_price else ""))
    return {"value": value, "tokens": tokens, "usd": usd, "days": n, "p_eff": p_eff, "stake": eff,
            "stake_days": len(used["start"]), "borrowed": used["borrowed"], "per_epoch": by_epoch,
            "last": max(priced[-1], st.index[-1]), "no_price": no_price, "how": how}


def _rate_on_stake(p, rows, long, asof, flow="", stock="", days=28, price=None, basis="spot", **_):
    """A farm's reward rate (Jake, 2026-10-07 — Sky's two lsSKY farms): the rewards it received over the last `days`
    days, annualised, over what is staked in it now (x the token's price when the rewards are in dollars). A simple
    rate: StakingRewards pays rewards out, it does not compound them.

    basis="payday" (Jake's price convention, 2026-10-09): dollar rewards put in tokens at each payment day's price and
    divided by the mean stake over the same window — payday_yield, the arithmetic of the headline view."""
    lo = asof - pd.Timedelta(days=days)
    if basis == "payday" and price:
        r = payday_yield(_series(long, p, flow), _series(long, p, price), _series(long, p, stock), lo, asof, 365.0)
        if r is None:
            return None, None, f"no {flow} day with a same-day {price} and no {stock} in the last {days} days"
        return r["value"], str(r["last"].date()), r["how"]
    fl = _series(long, p, flow)
    fl = fl[(fl.index > lo) & (fl.index <= asof)]
    st = _series(long, p, stock)
    st = st[st.index <= asof]
    if fl.empty:
        return None, None, f"no {flow} in the last {days} days"
    if st.empty or not st.iloc[-1]:
        return None, None, f"no {stock} stored"
    base, how_px = float(st.iloc[-1]), ""
    if price:
        px = _series(long, p, price)
        px = px[px.index <= asof]
        if px.empty or not px.iloc[-1]:
            return None, None, f"no {price} stored"
        base *= float(px.iloc[-1])
        how_px = f" x {price} {float(px.iloc[-1]):,.4f}"
    v = float(fl.sum()) * 365.0 / days / base
    return v, str(st.index[-1].date()), (f"{flow} {float(fl.sum()):,.0f} over the last {days} days x 365/{days} / "
                                         f"{stock} {float(st.iloc[-1]):,.0f} ({st.index[-1].date()}){how_px}")


def _reward_rate_apr(p, rows, long, asof, rate="", stock="", stake_price="", reward_price=None, **_):
    """A StakingRewards farm's rate FROM ITS OWN STATE (Jake's run 2026-10-08, Sky's SKY-rewards farm): the stored
    rewardRate() (reward tokens per second) x 31,536,000 / the stake stored on the same day. Reward and stake are
    the same token, so no price enters."""
    r, st = _series(long, p, rate), _series(long, p, stock)
    r, st = r[r.index <= asof], st[st.index <= asof]
    common = r.index.intersection(st.index)
    if common.empty:
        return None, None, f"no day holds both {rate} and {stock}"
    d = common.max()
    if not st.loc[d]:
        return None, None, f"{stock} is 0 on {d.date()}"
    v = float(r.loc[d]) * 31_536_000 / float(st.loc[d])
    how_px = ""
    if stake_price:                         # reward and stake differ (Sky's USDS farm: USDS paid on lsSKY = 1:1 SKY)
        px = _series(long, p, stake_price)
        px = px[px.index <= d]
        if px.empty or not px.iloc[-1]:
            return None, None, f"no {stake_price} on or before {d.date()}"
        v *= float(reward_price if reward_price is not None else 1.0) / float(px.iloc[-1])
        how_px = f" x ${float(reward_price if reward_price is not None else 1.0):g} / {stake_price} {float(px.iloc[-1]):.4f}"
    return v, str(d.date()), (f"{rate} {float(r.loc[d]):,.4f}/s x 31,536,000 / {stock} {float(st.loc[d]):,.0f} "
                              f"({d.date()}){how_px}")


def _trailing_token_yield(p, rows, long, asof, tokens="", lock="", days=365, **_):
    """REALISED TOKEN YIELD OVER A TRAILING WINDOW (Jake, 2026-10-07 — Ether.fi): the reward tokens holders gained over
    the last `days` days / the average staked over the stored days of that window, annualised over the days the
    reward series covers (a series younger than the window is annualised over its own span, and says so)."""
    lo = asof - pd.Timedelta(days=days)
    tk = _series(long, p, tokens)
    lk = _series(long, p, lock)
    tk, lk = tk[(tk.index > lo) & (tk.index <= asof)], lk[(lk.index > lo) & (lk.index <= asof)]
    if tk.empty or lk.empty:
        return None, None, f"no {tokens if tk.empty else lock} in the last {days} days"
    covered = (asof - max(lo, tk.index[0] - pd.Timedelta(days=1))).days
    avg = float(lk.mean())
    if covered <= 0 or not avg:
        return None, None, "nothing to annualise"
    # TIME-WEIGHTED (2026-10-07 17:08, the same rule as build_workbook._trailing_yield_views): each day's reward over
    # that day's stake, summed
    daily = (tk / lk.reindex(tk.index.union(lk.index)).sort_index().ffill().reindex(tk.index)).replace(
        [float("inf"), float("-inf")], float("nan")).dropna()
    if daily.empty:
        return None, None, f"no {lock} on or before the reward days"
    v = float(daily.sum()) * 365.0 / covered
    return v, str(asof.date()), (f"sum of daily {tokens} / that day's {lock} ({float(tk.sum()):,.0f} tokens; average "
                                 f"{lock} {avg:,.0f}) over {covered} day(s) "
                                 f"{(asof - pd.Timedelta(days=covered - 1)).date()}..{asof.date()}, x 365/{covered}"
                                 + ("" if covered >= days else f" (the series covers {covered} of {days} days)"))


def _sum_since(p, rows, long, asof, metrics=(), since="", **_):
    """Every stored value of `metrics` from `since` to asof, summed — for a figure published as an all-time total
    from a known start (NEAR's revenue wallets since the buybacks began, 2026-10-07)."""
    lo, tot, held = pd.Timestamp(since), 0.0, []
    for m in metrics:
        sr = _series(long, p, m)
        sr = sr[(sr.index >= lo) & (sr.index <= asof)]
        if sr.empty:
            return None, None, f"no {m} stored since {since}"
        tot += float(sr.sum())
        held.append(f"{m} {float(sr.sum()):,.0f} ({sr.index[0].date()}..{sr.index[-1].date()})")
    return tot, str(asof.date()), "; ".join(held)


def _schedule_month(p, rows, long, asof, **_):
    """The project's published circulating figure for asof's month (config.CIRCULATING_SCHEDULE — Aethir's docs table,
    2026-10-07)."""
    v = config.circulating_schedule_value(p, pd.Timestamp(asof).to_period("M"))
    if v is None:
        return None, None, f"no published circulating figure for {pd.Timestamp(asof).to_period('M')}"
    return v, str(pd.Timestamp(asof).to_period("M")), (f"{p}'s published circulating for "
                                                       f"{pd.Timestamp(asof).to_period('M')} (config.CIRCULATING_SCHEDULE)")


def _q0(asof):
    return asof - pd.Timedelta(days=90), asof


def _flow_usd_over_price(p, rows, long, asof, metrics=("revenue_usd",), share=1.0, **_):
    """Sum over the Q0 days of (sum of `metrics`, $) x share / that day's price — tokens. Only days
    with a price count, and the line says how many."""
    lo, hi = _q0(asof)
    px = _series(long, p, "price_usd")
    usd = None
    for m in metrics:
        sr = _series(long, p, m)
        usd = sr if usd is None else usd.add(sr, fill_value=0.0)
    if usd is None or usd.empty or px.empty:
        return None, None, f"no {'/'.join(metrics)} or price_usd in the store"
    usd = usd[(usd.index > lo) & (usd.index <= hi)]
    days = [d for d in usd.index if d in px.index and px.loc[d]]
    if not days:
        return None, None, "no priced day in the Q0 window"
    v = float(sum(usd.loc[d] / px.loc[d] for d in days)) * share
    return v, str(days[-1].date()), (f"{' + '.join(metrics)}{f' x {share:g}' if share != 1 else ''} / same-day price, "
                                     f"{len(days)} priced day(s) of {len(usd)} in Q0")


def _delta_q0(p, rows, long, asof, metric="total_supply", plus=None, **_):
    """Change of a STOCK across the Q0 window (last point minus the point at or before the window
    start), plus an optional Q0 flow — e.g. issuance = d(supply) + burn."""
    lo, hi = _q0(asof)
    sr = _series(long, p, metric)
    a, b = sr[sr.index <= lo], sr[sr.index <= hi]
    if a.empty or b.empty:
        return None, None, f"{metric} does not reach back to the Q0 start ({lo.date()})"
    v = float(b.iloc[-1] - a.iloc[-1])
    how = f"d({metric}) {a.index[-1].date()}..{b.index[-1].date()}"
    if plus:
        f = _series(long, p, plus)
        f = f[(f.index > a.index[-1]) & (f.index <= b.index[-1])]
        v += float(f.sum())
        how += f" + {plus} over the same days"
    # Float noise, not a tolerance: a difference of two ~1e10 float64 stocks carries ~1e-6 of rounding
    # (Sky's d(supply) + burn read -2.1e-06 SKY for an exact zero), which the gap formula would turn into a
    # 100% miss against a declared 0. Hundredths of a token are below any figure the tab shows.
    v = round(v, 2) + 0.0
    return v, str(b.index[-1].date()), how


def _delta_diff_q0(p, rows, long, asof, a="circulating_supply_first_party", b="total_supply_gross", **_):
    """d(a) - d(b) across Q0 — pool release from a supply source other than ours."""
    va, da, ha = _delta_q0(p, rows, long, asof, metric=a)
    vb, db, hb = _delta_q0(p, rows, long, asof, metric=b)
    if va is None or vb is None:
        return None, None, ha if va is None else hb
    return va - vb, da, f"{ha} - {hb}"


def _daily_delta_plus_flow(p, rows, long, asof, cover_metric="pool_release_tokens",
                           stock="circulating_supply_first_party", plus="actual_buyback_tokens", **_):
    """A RELEASE FROM A FIRST-PARTY STOCK, DAY BY DAY (Jake's probes15, root B — Hyperliquid): on each of OUR
    cover_metric days in Q0 where the stock is stored on that day AND the day before, d(stock) + that day's `plus`
    flow (the Assistance Fund's buyback takes HYPE out of circulation, so adding it back gives the release gross of
    it). The stock is read forward-only, so where it covers fewer days than ours the per-day mean on the common days
    is carried over our covered days — said in the source text, with the count."""
    lo, hi = _q0(asof)
    cov = _series(long, p, cover_metric)
    days = [d for d in cov.index if lo < d <= hi]
    if not days:
        return None, None, f"no {cover_metric} day in Q0"
    st, fl = _series(long, p, stock), _series(long, p, plus)
    common, tot = [], 0.0
    for d in days:
        prev = d - pd.Timedelta(days=1)
        if d in st.index and prev in st.index:
            tot += float(st.loc[d]) - float(st.loc[prev]) + (float(fl.loc[d]) if d in fl.index else 0.0)
            common.append(d)
    if not common:
        return None, None, (f"{stock} is not stored on any of our {len(days)} {cover_metric} day(s) together with "
                            f"its previous day (it is read forward-only)")
    if len(common) == len(days):
        return tot, str(days[-1].date()), (f"sum of d({stock}) + {plus} on all our {len(days)} {cover_metric} "
                                           f"day(s) {days[0].date()}..{days[-1].date()}")
    v = tot / len(common) * len(days)
    return v, str(common[-1].date()), (
        f"d({stock}) + {plus} on the {len(common)} of our {len(days)} {cover_metric} day(s) where the stock is "
        f"stored with its previous day ({common[0].date()}..{common[-1].date()}): {tot:,.0f}, a per-day mean of "
        f"{tot / len(common):,.0f} carried over our {len(days)} day(s)")


def _hl_reward_formula(p, rows, long, asof, **_):
    """Hyperliquid's published staking rate: 2.37% at 400M HYPE staked, scaling as 1/sqrt(staked)
    (VALIDATOR_YIELD.published_rate, read 2026-09-29), at OUR current stake."""
    staked = _num((rows.get(f"{p}|locked_tokens") or {}).get("now"))
    if not staked:
        return None, None, "no locked_tokens (stake) stored"
    v = 0.0237 * math.sqrt(400e6 / staked)
    return v, (rows.get(f"{p}|locked_tokens") or {}).get("latest_date"), (
        f"2.37% x sqrt(400M / {staked / 1e6:,.1f}M staked) — the documented reward curve")


def _share_price_growth(p, rows, long, asof, metric="lock_assets_per_share", days=None, **_):
    """The staking receipt's own accrual: assets per share, annualised over the longest stretch
    inside Q0 (or the trailing `days`, Ether.fi's trailing year since 2026-10-07) — what a staker actually earned
    on-chain."""
    lo, hi = _q0(asof) if not days else (asof - pd.Timedelta(days=int(days)), asof)
    sr = _series(long, p, metric)
    sr = sr[(sr.index > lo) & (sr.index <= hi)]
    if len(sr) < 2 or not sr.iloc[0]:
        return None, None, f"fewer than two {metric} points in the window"
    span = (sr.index[-1] - sr.index[0]).days
    if span < 7:
        return None, None, f"{metric} covers {span} day(s) — under a week"
    v = (float(sr.iloc[-1]) / float(sr.iloc[0])) ** (365.0 / span) - 1.0
    return v, str(sr.index[-1].date()), f"{metric} {sr.index[0].date()}..{sr.index[-1].date()}, annualised"


def _log_price_growth(p, rows, long, asof, metric="", days=365, **_):
    """ln(last / first) of a price series over the trailing `days`, x 365 / span — a share price's growth expressed as
    the SUM of its daily returns, the same basis as a headline that sums each day's reward over that day's stake
    (Ether.fi, Jake's probes14 2026-10-07: assets-per-share +32.47% compounded = 28.1% summed vs the headline's
    27.74%)."""
    import math
    sr = _series(long, p, metric)
    sr = sr[(sr.index > asof - pd.Timedelta(days=int(days))) & (sr.index <= asof)]
    if len(sr) < 2 or not sr.iloc[0] or not sr.iloc[-1]:
        return None, None, f"fewer than two {metric} points in the last {days} days"
    span = (sr.index[-1] - sr.index[0]).days
    if span < 7:
        return None, None, f"{metric} covers {span} day(s) — under a week"
    g = float(sr.iloc[-1]) / float(sr.iloc[0])
    return math.log(g) * 365.0 / span, str(sr.index[-1].date()), (
        f"{metric} {float(sr.iloc[0]):.6f} ({sr.index[0].date()}) -> {float(sr.iloc[-1]):.6f} "
        f"({sr.index[-1].date()}): {g - 1:+.2%} compounded = {math.log(g):.2%} as a sum of daily returns, x 365/{span}")


def _per_day_x_covered(p, rows, long, asof, per_day=0.0, cover_metric="emissions_tokens", label="", **_):
    cov = _num((rows.get(f"{p}|{cover_metric}") or {}).get("q0_covered_days")) or 90.0
    return per_day * cov, None, f"{label or f'{per_day:,.2f}/day'} x {cov:.0f} covered day(s)"


def _value_on(p, rows, long, asof, metric="", date="", plus=(), **_):
    """A stored series' value on one date (plus other series on the same date) — for comparing with
    a figure that was read, by hand, on that date."""
    day = pd.Timestamp(date)
    total = 0.0
    for m in (metric, *plus):
        sr = _series(long, p, m)
        if day not in sr.index:
            return None, None, f"no {m} point on {date}"
        total += float(sr.loc[day])
    return total, date, f"{' + '.join((metric, *plus))} on {date}"


def _stake_near(long, p, metric, day, after_days=0):
    """The stock on or before `day`, else the first reading within `after_days` after it: (value, date) or None."""
    sr = _series(long, p, metric)
    before = sr[sr.index <= day]
    if not before.empty:
        return float(before.iloc[-1]), before.index[-1]
    after = sr[(sr.index > day) & (sr.index <= day + pd.Timedelta(days=int(after_days)))]
    return (float(after.iloc[0]), after.index[0]) if after_days and not after.empty else None


def _epoch_reproduction(p, rows, long, asof, dist="pendle_distributed_tokens", published="pendle_epoch_apr_published",
                        stock="locked_tokens_shares", plus=("locked_tokens_virtual",), epoch_days=14, after_days=7,
                        side="ref", **_):
    """DOES THE HEADLINE'S ARITHMETIC REPRODUCE EACH EPOCH? (Jake's run 2026-10-08 11:27, Pendle.) For every Q0 epoch:
    distributed x 365.25/epoch_days / (stake + plus, on or before the epoch's start, else within after_days after) —
    against Pendle's own APR for that epoch. Returns the epoch that differs most (ours or Pendle's figure, by side),
    with the whole table as the working; epochs with no stake or no published APR are listed, not judged."""
    lo, hi = _q0(asof)
    d_ = _series(long, p, dist)
    d_ = d_[(d_.index > lo) & (d_.index <= hi)]
    pub = _series(long, p, published)
    if d_.empty:
        return None, None, f"no {dist} epoch in Q0"
    table, skipped, worst = [], [], None
    for day, tokens in d_.items():
        parts = [_stake_near(long, p, m, day, after_days) for m in (stock, *plus)]
        if any(x is None for x in parts):
            skipped.append(f"{day.date()} (no {'/'.join(m for m, x in zip((stock, *plus), parts) if x is None)})")
            continue
        staked = sum(x[0] for x in parts)
        ours = float(tokens) * 365.25 / epoch_days / staked if staked else None
        theirs = float(pub.loc[day]) if day in pub.index else None
        if ours is None or theirs is None:
            skipped.append(f"{day.date()} (no published APR)" if theirs is None else f"{day.date()} (no stake)")
            continue
        gap = ours / theirs - 1 if theirs else float("inf")
        table.append(f"{day.date()}: {float(tokens):,.0f} PENDLE / {staked:,.0f} staked = {ours:.2%} vs Pendle "
                     f"{theirs:.2%} ({gap:+.1%})")
        if worst is None or abs(gap) > abs(worst[3]):
            worst = (day, ours, theirs, gap)
    if worst is None:
        return None, None, "no Q0 epoch with both a stake and Pendle's APR stored: " + "; ".join(skipped)
    how = (f"worst epoch {worst[0].date()}. Per epoch: " + "; ".join(table)
           + (f". Not judged: {'; '.join(skipped)}" if skipped else ""))
    return (worst[1] if side == "ours" else worst[2]), str(worst[0].date()), how


_EPOCH_LAG_CACHE: dict = {}


def epoch_publish_lag(project: str, metric: str = "pendle_distributed_tokens", epoch_days: int = 14, db=None):
    """HOW LONG PENDLE TAKES TO PUBLISH AN EPOCH'S DISTRIBUTION, OBSERVED (Jake's run 2026-10-09 ~10:15, 5b; rule fixed on
    his run 11:41: "PUBLISH LAG 239 days is wrong: it counts epochs first logged today. Only epochs we read before their
    amount was published count — so far only 2026-09-08: +7 days").

    ONLY AN EPOCH WE WERE WATCHING WHEN IT WAS PUBLISHED COUNTS:
      * an epoch read at 0 after it ended and above 0 later: its lag is that first non-zero read minus its end;
      * an epoch that ended AFTER our first read of the payload: its lag is the first run that read it above 0 once
        complete, minus its end ("... COMPLETED a..b ... last complete epoch V PENDLE", or "STORED PER EPOCH");
      * the epoch that was the LATEST COMPLETE one at our first read (ended before it, the next had not ended) and was
        already above 0 then: published within (first read − its end) — an upper bound, counted;
      * every older epoch was published before we watched and says nothing about the lag.
    The lag is the longest counted. (lag or None when none counts, [rows])."""
    import re as _re
    import sqlite3
    import store as _sm
    path = str(db or _sm.DB_PATH)
    try:
        from pathlib import Path as _P
        key = (path, _P(path).stat().st_mtime, project, metric, int(epoch_days))
    except OSError:
        return None, []
    if key in _EPOCH_LAG_CACHE:
        return _EPOCH_LAG_CACHE[key]
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            got = con.execute("SELECT ts, message FROM run_log WHERE project = ? AND message LIKE ? ORDER BY ts",
                              (project, f"{metric}:%")).fetchall()
            stored = dict(con.execute("SELECT date, value FROM metrics WHERE project = ? AND metric = ?",
                                      (project, metric)).fetchall())
        finally:
            con.close()
    except sqlite3.Error:
        got, stored = [], {}
    if not got:
        out = (None, [])
        _EPOCH_LAG_CACHE[key] = out
        return out
    num = lambda s: float(s.replace(",", ""))                                      # noqa: E731
    span = pd.Timedelta(days=int(epoch_days))
    first_read = pd.Timestamp(str(got[0][0])[:19])
    seen: dict = {}
    for ts, msg in got:
        when = pd.Timestamp(str(ts)[:19])
        reads = []
        m = _re.search(r"COMPLETED \S+?\.\.(\d{4}-\d{2}-\d{2}).*?last complete epoch ([\d,.]+) PENDLE", msg or "")
        if m:
            reads.append((pd.Timestamp(m.group(1)), num(m.group(2))))
        m = _re.search(r"STORED PER EPOCH: ([^;]*)", msg or "")
        if m:
            reads += [(pd.Timestamp(d), num(v)) for d, v in _re.findall(r"(\d{4}-\d{2}-\d{2}) ([\d,.]+)", m.group(1))]
        for e, v in reads:
            r = seen.setdefault(e, {"epoch": e, "end": e + span, "first_read": when, "first_zero": None,
                                    "first_nonzero": None})
            if v == 0 and r["first_zero"] is None:
                r["first_zero"] = when
            if v > 0 and r["first_nonzero"] is None:
                r["first_nonzero"] = when
    for d, v in stored.items():                     # epochs stored but never named in a parsable line
        e = pd.Timestamp(str(d)[:10])
        seen.setdefault(e, {"epoch": e, "end": e + span, "first_read": None, "first_zero": None,
                            "first_nonzero": None, "stored": float(v or 0)})["stored"] = float(v or 0)
    rows = sorted(seen.values(), key=lambda r: r["epoch"])
    lags = []
    for r in rows:
        r["lag_days"], r["counts"] = None, "published before our first read"
        if (r["first_zero"] is not None and r["first_nonzero"] is not None and r["first_zero"] < r["first_nonzero"]
                and r["first_zero"] >= r["end"]):
            # read at 0 after it ended, then above 0: watched across its publication
            r["lag_days"], r["counts"] = (r["first_nonzero"].normalize() - r["end"]).days, "watched (0, then published)"
        elif r["end"] > first_read:
            if r["first_nonzero"] is not None:
                r["lag_days"], r["counts"] = (r["first_nonzero"].normalize() - r["end"]).days, "watched"
            else:
                r["counts"] = "not published yet"
        elif r["end"] + span > first_read and (r.get("stored") or 0) > 0 and r["first_zero"] is None:
            r["lag_days"] = (first_read.normalize() - r["end"]).days
            r["counts"] = "latest complete at our first read (an upper bound)"
        if r["lag_days"] is not None:
            lags.append(max(int(r["lag_days"]), 0))
    out = (max(lags) if lags else None, rows)
    _EPOCH_LAG_CACHE[key] = out
    return out


def virtual_rebuild(ve, ve_src: dict, api, max_lock_days: int = 728, drift: float = 0.005, legacy=None,
                    ve_at: dict | None = None, min_gap_h: float = 20.0) -> dict | None:
    """VIRTUAL sPENDLE REBUILT ON-CHAIN, CALIBRATED TO PENDLE'S API (Jake's 1c decision, 2026-10-09). Pendle's method per
    lock: virtual = locked x (1 + 3 x remaining/2y), vePENDLE balance = locked x remaining/2y; summed over ACTIVE locks,
    virtual = active locked + 3 x vePENDLE supply. Active locked is the supply's own decay: its fall per 24h x
    max_lock_days (104 weeks) — an expired lock no longer decays, so it drops out.

    THE GUARD (Jake's run 2026-10-09 ~14:10: active 94,954,481 > all 63,524,138 PENDLE locked, from two reads less than
    a day apart). A read's time is its UTC day start for an archive read (the first block of the day), else its
    fetched_at when that falls on its own date (otherwise unknown, and the day is skipped). A day's fall is taken from the read the day before only when the two
    reads are at least `min_gap_h` hours apart, scaled to 24h; and the rebuild is kept only when active locked is no
    more than the PENDLE locked in vePENDLE that day (`legacy`). A day that fails is SKIPPED — not rebuilt, not in the
    calibration, never judged for drift — and named. A day with no read the day before carries the last good fall.

    CALIBRATION: the mean of API / rebuild over every day both exist; the rebuild x that ratio is the stake used for
    epochs before the API's history. DRIFT: any API day whose ratio is more than `drift` (0.005 = 0.5 percentage
    points) from the mean — the rebuilt epochs then read CHECK. None when there is no overlap to calibrate on."""
    if ve is None or ve.empty or api is None or api.empty:
        return None
    ve = ve.sort_index()
    ve_at = ve_at or {}
    base = lambda d: str(ve_src.get(d, "")).replace(":archive", "")                        # noqa: E731

    def when(d):
        if ":archive" in str(ve_src.get(d, "")):
            return d                                       # the first block of the UTC day
        t = pd.Timestamp(ve_at[d]) if ve_at.get(d) else None
        if t is not None:
            t = t.tz_convert(None) if t.tzinfo else t
            if t.normalize() == d:
                return t
        return None                                        # a live read at an unknown time of day

    rebuilt, active, last, skipped = {}, {}, None, []
    for prev, d in zip(ve.index[:-1], ve.index[1:]):
        if (d - prev).days == 1 and base(d) == base(prev):
            if when(d) is None or when(prev) is None:
                skipped.append((d, "a live read's time of day is unknown (fetched_at not on its own date)"))
                continue
            gap_h = (when(d) - when(prev)).total_seconds() / 3600.0
            if gap_h < float(min_gap_h):
                skipped.append((d, f"reads {gap_h:.1f}h apart ({when(prev)} -> {when(d)}), under {min_gap_h:.0f}h"))
                continue
            dec = (float(ve.loc[prev]) - float(ve.loc[d])) * 24.0 / gap_h
            if dec > 0:
                last = dec
        if last is None:
            continue
        act = last * int(max_lock_days)
        if legacy is not None and not legacy.empty:
            lg = legacy[legacy.index <= d]
            if lg.empty or (d - lg.index[-1]).days > 1:
                skipped.append((d, "no PENDLE-locked reading that day to bound active locked by"))
                continue
            if act > float(lg.iloc[-1]):
                skipped.append((d, f"active locked {act:,.0f} > all PENDLE locked {float(lg.iloc[-1]):,.0f} "
                                   "(impossible)"))
                continue
        active[d] = act
        rebuilt[d] = act + 3 * float(ve.loc[d])
    rb = pd.Series(rebuilt, dtype=float).sort_index()
    days = [(d, float(rb.loc[d]), float(api.loc[d]), float(api.loc[d]) / float(rb.loc[d]))
            for d in api.index if d in rb.index and rb.loc[d]]
    if not days:
        return None
    ratios = [r for *_x, r in days]
    mean = sum(ratios) / len(ratios)
    out = {"rebuilt": rb, "active": pd.Series(active, dtype=float).sort_index(), "ratio": mean,
           "lo": min(ratios), "hi": max(ratios), "days": days, "skipped": skipped,
           "drift": [(d, r) for d, _b, _a, r in days if abs(r - mean) > drift], "drift_limit": drift,
           "calibrated": rb * mean}
    out["label"] = (f"calibrated on-chain rebuild (x {mean:.4f} = API/rebuild mean over {len(days)} day(s) "
                    f"{days[0][0].date()}..{days[-1][0].date()}, range {out['lo']:.4f}..{out['hi']:.4f})")
    return out


def epoch_apr_table(dist, published, stakes: dict, lo, hi, epoch_days=14, after_days=7, lag_days=None,
                    zero_rule=True, fallback: dict | None = None) -> list[dict]:
    """THE PER-EPOCH APRs BEHIND PENDLE'S HEADLINE (Jake's 5c decision, 2026-10-09). For every epoch in (lo, hi]: OUR APR
    = distributed x 365.25/epoch_days / the summed stakes (each on or before the epoch's start, else within after_days
    after) wherever every stake exists; otherwise PENDLE'S PUBLISHED APR for that epoch (first-party, it embeds the
    epoch's own stake — virtual sPENDLE is API-only and its history starts ~2026-09-11). One function for the headline
    view, the Credibility rows and pendle_epoch_table. Rows: {date, tokens, stake, apr, source, published}; source is
    "ours", "calibrated on-chain rebuild", "Pendle published", "unpublished" or None (neither: the epoch has no APR).
    `fallback` {metric: (series, label)} supplies a stake part missing from `stakes` (on or before the epoch's day,
    within 1 day) — Pendle's virtual sPENDLE before the API's history (Jake's 1c decision); it comes before Pendle's
    published APR.

    A 0 IS NOT YET PUBLISHED (Jake's run 2026-10-09, 5b): an epoch read at 0 PENDLE is "unpublished" — no APR, left
    out of the mean — until Pendle's observed publish lag (`lag_days`, epoch_publish_lag) has passed since the epoch
    ended; with no lag observed it stays unpublished. A 0 still standing after the lag is a distribution of 0."""
    out = []
    d_ = dist[(dist.index > lo) & (dist.index <= hi)] if dist is not None else dist
    for day, tokens in (d_.items() if d_ is not None else ()):
        if zero_rule and float(tokens) == 0 and (
                lag_days is None or day + pd.Timedelta(days=int(epoch_days) + int(lag_days)) >= hi):
            out.append({"date": day, "tokens": 0.0, "stake": None, "published": None, "source": "unpublished",
                        "apr": None, "why": ("0 read; Pendle's publish lag has not been observed yet" if lag_days is None
                                             else f"0 read; within Pendle's observed {int(lag_days)}-day publish lag "
                                                  f"(ended {(day + pd.Timedelta(days=int(epoch_days))).date()})")})
            continue
        parts, used_fb = [], None
        for m, sr in stakes.items():
            got = None
            if sr is not None and not sr.empty:
                b = sr[sr.index <= day]
                a = sr[(sr.index > day) & (sr.index <= day + pd.Timedelta(days=int(after_days)))]
                got = float(b.iloc[-1]) if len(b) else (float(a.iloc[0]) if len(a) else None)
            if got is None and fallback and m in fallback:
                # BEFORE THE API'S HISTORY (Jake's 1c decision): the calibrated on-chain rebuild on the epoch's day
                fs, label = fallback[m]
                fb = fs[fs.index <= day] if fs is not None else None
                if fb is not None and len(fb) and (day - fb.index[-1]).days <= 1:
                    got, used_fb = float(fb.iloc[-1]), label
            parts.append(got)
        pub = float(published.loc[day]) if published is not None and day in published.index else None
        if all(x is not None for x in parts) and sum(parts):
            stake = sum(parts)
            out.append({"date": day, "tokens": float(tokens), "stake": stake, "published": pub,
                        "source": "calibrated on-chain rebuild" if used_fb else "ours", "stake_note": used_fb,
                        "apr": float(tokens) * 365.25 / float(epoch_days) / stake})
        else:
            out.append({"date": day, "tokens": float(tokens), "stake": None, "published": pub,
                        "source": "Pendle published" if pub is not None else None, "apr": pub})
    return out


def epoch_headline(tab: list[dict]):
    """(the mean of the per-epoch APRs, n, the per-epoch text) over the epochs that have one; unpublished epochs (a 0
    inside Pendle's publish lag) are left out and named. None when an epoch has no APR at all (neither our stake nor
    Pendle's): the mean is never formed over a subset of the published epochs."""
    use = [r for r in tab if r["source"] != "unpublished"]
    if not use or any(r["apr"] is None for r in use):
        return None
    v = sum(r["apr"] for r in use) / len(use)
    text = "; ".join(f"{r['date']:%m-%d} {r['apr']:.2%} ({r['source']})" if r["source"] != "unpublished"
                     else f"{r['date']:%m-%d} left out ({r['why']})" for r in tab)
    return v, len(use), text


def pendle_calibration(p, long):
    """The calibrated virtual rebuild for project `p` from config (token_yield.epoch_mean.calibrate), or None."""
    cal = ((((config.PROTOCOL_YIELD.get(p) or {}).get("token_yield") or {}).get("epoch_mean") or {})
           .get("calibrate"))
    if not cal or long is None or long.empty:
        return None
    g = long[(long.project == p) & (long.metric == cal["ve"])]
    dd = pd.to_datetime(g["date"]).dt.normalize()
    src = dict(zip(dd, g["source"].astype(str))) if "source" in g.columns else {}
    at = dict(zip(dd, g["fetched_at"])) if "fetched_at" in g.columns else {}
    return virtual_rebuild(_series(long, p, cal["ve"]), src, _series(long, p, cal["api"]),
                           int(cal.get("max_lock_days", 728)), float(cal.get("drift_pp", 0.5)) / 100.0,
                           legacy=_series(long, p, cal["legacy"]) if cal.get("legacy") else None, ve_at=at,
                           min_gap_h=float(cal.get("min_gap_h", 20)))


def _pendle_epochs(p, long, asof, dist, published, stock, plus, epoch_days, after_days):
    lo, hi = _q0(asof)
    lag, _ = epoch_publish_lag(p, dist, epoch_days)
    cal = pendle_calibration(p, long)
    api = (((config.PROTOCOL_YIELD.get(p) or {}).get("token_yield") or {}).get("epoch_mean") or {}).get("calibrate", {})
    fb = {api["api"]: (cal["calibrated"], cal["label"])} if cal and api else None
    return epoch_apr_table(_series(long, p, dist), _series(long, p, published),
                           {m: _series(long, p, m) for m in (stock, *plus)}, lo, hi, epoch_days, after_days, lag,
                           fallback=fb)


def _epoch_mean_check(p, rows, long, asof, dist="pendle_distributed_tokens", published="pendle_epoch_apr_published",
                      stock="locked_tokens_shares", plus=("locked_tokens_virtual",), epoch_days=14, after_days=7,
                      side="ref", **_):
    """OVER THE EPOCHS WE COMPUTE OURSELVES (Jake's 5c decision, 2026-10-09): ours = the mean of our per-epoch APRs
    (distributed / each epoch's own sPENDLE + virtual) — the part of the headline that is ours; the reference = the
    mean of Pendle's published APRs for the SAME epochs. Epochs on Pendle's APR (no stake of ours) are not in either
    side; they are the headline's documented limitation (in_epochs_first_party)."""
    tab = [r for r in _pendle_epochs(p, long, asof, dist, published, stock, plus, epoch_days, after_days)
           if r["source"] == "ours" and (side == "ours" or r["published"] is not None)]
    if not tab:
        return None, None, "no Q0 epoch with our own stake (virtual sPENDLE history starts ~2026-09-11)"
    v = sum((r["apr"] if side == "ours" else r["published"]) for r in tab) / len(tab)
    lines = "; ".join(f"{r['date'].date()}: ours {r['apr']:.3%}"
                      + (f" vs Pendle {r['published']:.3%}" if r["published"] is not None else "") for r in tab)
    return v, str(tab[-1]["date"].date()), f"mean over the {len(tab)} epoch(s) we compute: {lines}"


def _epochs_first_party(p, rows, long, asof, dist="pendle_distributed_tokens", published="pendle_epoch_apr_published",
                        stock="locked_tokens_shares", plus=("locked_tokens_virtual",), epoch_days=14, after_days=7,
                        **_):
    """THE Q0 EPOCHS WHOSE STAKE IS THE CALIBRATED ON-CHAIN REBUILD (Jake's 1c decision, 2026-10-09), with the table.
    > 0: that many epochs, the calibration holding — a DOCUMENTED LIMITATION (calibrated, not first-party).
    < 0: the rebuilt epochs cannot be used — the ratio drifted more than the limit on an API day, or an epoch has no
         APR at all — a CHECK. 0: every Q0 epoch is on Pendle's own virtual sPENDLE (the API)."""
    tab = _pendle_epochs(p, long, asof, dist, published, stock, plus, epoch_days, after_days)
    if not tab:
        return None, None, "no Q0 epoch stored"
    cal = pendle_calibration(p, long)
    rebuilt = [r for r in tab if r["source"] == "calibrated on-chain rebuild"]
    first = [r for r in tab if r["source"] == "Pendle published"]
    none_ = [r for r in tab if r["source"] is None]
    how = ("per epoch: " + "; ".join(
        f"{r['date'].date()} {r['tokens']:,.0f} PENDLE / {r['stake']:,.0f} = {r['apr']:.3%} ({r['source']})"
        if r["apr"] is not None and r["stake"] else f"{r['date'].date()} {r['apr']:.3%} ({r['source']})"
        if r["apr"] is not None else f"{r['date'].date()} UNPUBLISHED ({r['why']}), left out of the mean"
        if r["source"] == "unpublished" else f"{r['date'].date()} NO APR" for r in tab))
    if cal:
        how += (f". CALIBRATION: {cal['label']}; every API day: "
                + ", ".join(f"{d:%m-%d} {r:.4f}" for d, _b, _a, r in cal["days"]))
        if cal["drift"]:
            how += (f". DRIFT beyond {cal['drift_limit']:.1%}: "
                    + ", ".join(f"{d:%m-%d} {r:.4f}" for d, r in cal["drift"]))
    if none_ or (rebuilt and cal and cal["drift"]):
        if none_:
            how += f". {len(none_)} epoch(s) have no APR — the headline is not formed"
        return -float(len(none_) or len(rebuilt)), str(tab[-1]["date"].date()), how
    return float(len(rebuilt) + len(first)), str(tab[-1]["date"].date()), how


def _published_epoch_aprs(p, rows, long, asof, metric="pendle_epoch_apr_published", **_):
    """How many Q0 epochs carry Pendle's own per-epoch APR (stored above 0); 0 when Pendle publishes none."""
    lo, hi = _q0(asof)
    s = _series(long, p, metric)
    s = s[(s.index > lo) & (s.index <= hi) & (s > 0)]
    return float(len(s)), (str(s.index[-1].date()) if len(s) else None), (
        f"{len(s)} Q0 epoch(s) with Pendle's published APR" if len(s) else
        "Pendle's per-epoch aprs: none above 0 in Q0 (sPendleHistoricalData.aprs reads 0)")


def _epoch_apr(p, rows, long, asof, tokens=0.0, date="", stock="", plus=(), mult=26.0, read_by="", source="",
               ours_metric="", after_days=0, calibrate=False, **_):
    """ONE EPOCH'S APR FROM A READING (Jake's run 2026-10-08, Pendle): `tokens` distributed in the epoch read on
    `date` x `mult` epochs a year / the reward-bearing stake stored on that date (stock + plus). With `ours_metric`
    the epoch's tokens are OUR stored figure on that date instead — the like-for-like twin of the same arithmetic."""
    day = pd.Timestamp(date)
    staked, late, cal_note = 0.0, [], ""
    cal = pendle_calibration(p, long) if calibrate else None
    for m in (stock, *plus):
        got = _stake_near(long, p, m, day, after_days)
        if got is None and cal is not None and m in plus:
            # BEFORE THE API'S HISTORY (Jake's 1c decision): the calibrated on-chain rebuild on that day, both sides
            fb = cal["calibrated"][cal["calibrated"].index <= day]
            if len(fb) and (day - fb.index[-1]).days <= 1:
                got = (float(fb.iloc[-1]), fb.index[-1])
                cal_note = f"; {m} = {cal['label']}"
        if got is None:
            return None, None, f"no {m} on or before {date}" + (f" or within {after_days} days after" if after_days else "")
        staked += got[0]
        if got[1] > day:
            late.append(f"{m} read {got[1].date()}")
    if not staked:
        return None, None, f"no stake on {date}"
    if ours_metric:
        sr = _series(long, p, ours_metric)
        if day not in sr.index:
            return None, None, f"no {ours_metric} point on {date}"
        tokens, who = float(sr.loc[day]), f"{ours_metric} {float(sr.loc[day]):,.0f}"
    else:
        who = f"{float(tokens):,.0f} ({read_by}: {source})"
    return float(tokens) * float(mult) / staked, date, \
        (f"{who} x {mult:g} / ({' + '.join((stock, *plus))} {staked:,.0f} on {date}"
         + (f"; {', '.join(late)} — the first reading after the epoch" if late else "") + cal_note + ")")


def _q0_net_flow(p, rows, long, asof, out="", inn="", **_):
    """SCANNED NET OUTFLOW OVER Q0 (Jake's run 2026-10-08 11:27, Chainlink): Transfer events out of the named wallets
    minus those into them, each scan reconciled to balanceOf to the wei before it is stored (fetch/logscan.py), summed
    over the Q0 days — the flow the balance-derived release (the stock's change) is judged by."""
    lo, hi = _q0(asof)
    so, si = _series(long, p, out), _series(long, p, inn)
    so, si = so[(so.index > lo) & (so.index <= hi)], si[(si.index > lo) & (si.index <= hi)]
    if so.empty or si.empty:
        return None, None, f"no scanned flow in Q0 ({out if so.empty else inn} not stored)"
    v = float(so.sum()) - float(si.sum())
    return v, str(max(so.index.max(), si.index.max()).date()), (
        f"scanned out {float(so.sum()):,.2f} − in {float(si.sum()):,.2f} over {len(so)} day(s) "
        f"{so.index.min().date()}..{so.index.max().date()} (each scan reconciled to balanceOf to the wei)")


def _negative_release(p, rows, long, asof, release="", out="", inn="", agree_pct=1.0, **_):
    """A NEGATIVE NET RELEASE, CONFIRMED BY BOTH SIDES (Jake's run 2026-10-08 11:27, Chainlink): the balance-derived
    release over Q0 and the scanned net outflow are both below zero and agree within agree_pct — tokens came BACK into
    the non-circulating wallets. Returns that release, or None when the two do not both say so."""
    lo, hi = _q0(asof)
    rel = _series(long, p, release)
    rel = rel[(rel.index > lo) & (rel.index <= hi)]
    flow, _d, how = _q0_net_flow(p, rows, long, asof, out=out, inn=inn)
    if rel.empty or flow is None:
        return None, None, "no release or no scanned flow in Q0"
    r = float(rel.sum())
    if not (r < 0 and flow < 0 and abs(r / flow - 1) * 100 <= agree_pct):
        return None, None, f"release {r:,.2f} vs scanned {flow:,.2f}: not a confirmed negative release"
    return r, str(rel.index.max().date()), f"balance-derived release {r:,.2f} LINK; {how} = {flow:,.2f}"


def _sum_month(p, rows, long, asof, metric="", month="", **_):
    sr = _series(long, p, metric)
    per = pd.Period(month, "M")
    m = sr[(sr.index >= per.start_time) & (sr.index <= per.end_time.normalize())]
    if m.empty:
        return None, None, f"no {metric} in {month}"
    return float(m.sum()), month, f"{metric} summed over {month} ({len(m)} day(s))"


def _sum_months(p, rows, long, asof, metric="", months=(), **_):
    """The daily series summed over exactly the listed months (manual monthly readings, manual_refs.py)."""
    sr = _series(long, p, metric)
    tot, seen = 0.0, []
    for month in months:
        per = pd.Period(month, "M")
        m = sr[(sr.index >= per.start_time) & (sr.index <= per.end_time.normalize())]
        if m.empty:
            return None, None, f"no {metric} in {month}"
        tot += float(m.sum())
        seen.append(month)
    return tot, ", ".join(seen), f"{metric} summed over {', '.join(seen)}"


def _now_sum(p, rows, long, asof, metrics=(), **_):
    """The latest value of each of `metrics`, summed — FROM THE BUILT ROWS first (a read-time view such as
    circulating_supply_onchain is absent from the raw store; Jake's run 2026-10-07 18:11 showed the like-for-like row
    'CHECK (no reference)' because the store-only sum found nothing), else the store's latest."""
    tot, parts, day = 0.0, [], None
    for m in metrics:
        r = (rows or {}).get(f"{p}|{m}") or {}
        v, d = _num(r.get("now")), r.get("latest_date")
        if v is None and long is not None:
            sr = _series(long, p, m)
            v, d = (float(sr.iloc[-1]), str(sr.index[-1].date())) if len(sr) else (None, None)
        if v is None:
            return None, None, f"no {m} figure"
        tot += v
        parts.append(f"{m} {v:,.0f}")
        day = max(day or str(d or ""), str(d or "")) or None
    return tot, day, " + ".join(parts)


def _bridge_reconciled(p, rows, long, asof, key="", **_):
    """THE RECORDED ON-CHAIN CIRCULATING, GATED ON ITS TWO CONDITIONS (Jake's probes15, root A — GEODNET): the bridge
    custody holds at least the bridged chain's whole supply (every bridged token backed: a zero-tolerance bound, no
    slack) and the project's filing lists exactly our wallets (filing_confirms). Both hold -> the recorded figure is
    the reference; either fails -> no reference, and the note says which."""
    spec = config.circulating_onchain(p) or {}
    rec = spec.get(key) or {}
    cust, sol, circ = _num(rec.get("custody")), _num(rec.get("solana_supply")), _num(rec.get("circulating"))
    if None in (cust, sol, circ):
        return None, None, f"no recorded reconciliation '{key}' (custody / bridged supply / circulating)"
    if cust < sol:
        return None, None, (f"bridge does NOT reconcile: custody {cust:,.0f} < bridged supply {sol:,.0f} "
                            f"({sol - cust:,.0f} unbacked)")
    if not spec.get("filing_confirms"):
        return None, None, "the project's filing has not confirmed our wallet list"
    day = key.rsplit("_", 3)[-3:] if key.startswith("onchain_") else None
    date = "-".join(day) if day else None
    return circ, date, (f"recorded on-chain circulating {circ:,.0f} ({rec.get('read_by', '')}); bridge reconciles "
                        f"(custody {cust:,.0f} >= bridged supply {sol:,.0f}, surplus {cust - sol:,.0f}) and the "
                        f"filing lists our wallets (confirmed {spec['filing_confirms']})")


def _now_combo(p, rows, long, asof, metrics=(), minus=(), **_):
    """The latest values of `metrics` summed, less those of `minus` (built rows first, as _now_sum) — CoinGecko put on
    our basis (Aerodrome, Jake's probes16: CoinGecko + permanent locks − the filing wallets' managed locks)."""
    a, da, ha = _now_sum(p, rows, long, asof, metrics=metrics)
    if a is None:
        return None, None, ha
    if not minus:
        return a, da, ha
    b, db, hb = _now_sum(p, rows, long, asof, metrics=minus)
    if b is None:
        return None, None, hb
    return a - b, max(str(da or ""), str(db or "")) or None, f"{ha} − ({hb})"


def _free_float_now(p, rows, long, asof, add_back=(), **_):
    """OUR free float now: the circulating the ratios use − the locked tokens inside it (as A2 computes it)."""
    # FROM THE BUILT ROWS (2026-10-06 overnight, A1/A2): the on-chain circulating is a READ-TIME view, absent from
    # the raw store — reading the store fell back to CoinGecko's circulating, which for Pendle / Aerodrome already
    # excludes staked tokens, and subtracted the lock a second time (Pendle 173.7M − 31.4M = 142.3M; Aerodrome
    # ~102M). CoinGecko is never the fallback here: it is the reference this row is judged against.
    from build_workbook import chosen_circulating_metric
    proj = config.PROJECT_BY_NAME[p]
    m = chosen_circulating_metric(proj)

    def now(metric):
        r = (rows or {}).get(f"{p}|{metric}") or {}
        v = _num(r.get("now"))
        if v is None and long is not None:
            s = _series(long, p, metric)
            v = float(s.iloc[-1]) if len(s) else None
        return v, r.get("latest_date")
    circ, day = now(m)
    if circ is None or m == "circulating_supply":
        return None, None, f"no {m} figure — the on-chain set was not computed on the latest day"
    lock, parts = 0.0, []
    if not proj.get("free_float_lock_zero"):
        for lm in config.free_float_lock_metrics(p):
            v, _d = now(lm)
            if v is None and lm == config.lock_display_metric(p):
                return None, None, f"no {lm} figure"
            lock += v or 0.0
            parts.append(f"{lm} {v or 0:,.0f}")
        if config.circulating_excludes_declared(p):
            x = config.locked_excluded_from_circulating(p)
            lock = max(0.0, lock - x)
            if x:
                parts.append(f"less {x:,.0f} declared locked exclusion")
            for xm in config.locked_excluded_metrics(p):          # measured (Aerodrome's managed locks, probes16)
                v, _d = now(xm)
                if v is None:
                    return None, None, f"no {xm} figure"
                lock = max(0.0, lock - v)
                parts.append(f"less {xm} {v:,.0f} (locked, already out of circulating)")
    extra, extra_parts = 0.0, []
    for lm in add_back:                       # lock legs the REFERENCE still counts (Pendle: CoinGecko's sPENDLE)
        v, _d = now(lm)
        if v is None:
            return None, None, f"no {lm} figure to add back"
        extra += v
        extra_parts.append(f"{lm} {v:,.0f}")
    how = f"{m} {circ:,.0f} − locked ({'; '.join(parts) or '0'}) = A2 free float"
    if extra_parts:
        how += f", + {'; '.join(extra_parts)} (counted by the reference) for like-for-like"
    return circ - lock + extra, None if day is None else str(day)[:10], how


def _common_day(p, long, a, b, asof):
    """(day, a's value, b's value) on the latest COMPLETED day both series hold. Today is excluded: our
    newest CoinGecko point is taken at fetch time, not at 00:00, so it is never compared."""
    sa, sb = _series(long, p, a), _series(long, p, b)
    days = sorted(set(sa.index) & set(sb.index))
    days = [d for d in days if d < asof.normalize()]
    if not days:
        return None, None, None
    d = days[-1]
    return d, float(sa.loc[d]), float(sb.loc[d])


def _common_day_value(p, rows, long, asof, metric="price_usd", ref="price_usd_coinbase", side="ours", **_):
    d, va, vb = _common_day(p, long, metric, ref, asof)
    if d is None:
        return None, None, f"no completed day on which both {metric} and {ref} have a 00:00 point"
    return (va if side == "ours" else vb), str(d.date()), f"{metric if side == 'ours' else ref} on {d.date()} (00:00 UTC)"


def _product_on_common_day(p, rows, long, asof, a="", b="", ref="", side="ours", **_):
    """a x b against ref, all three on the latest day each holds (the sweep, 2026-10-07: Morpho's blue-api supply x
    utilisation = its listed-market borrow, against DefiLlama's borrowed on the same day)."""
    sa, sb, sr = (_series(long, p, m) for m in (a, b, ref))
    if sa.empty or sb.empty or sr.empty:
        return None, None, "not stored: " + ", ".join(m for m, x in ((a, sa), (b, sb), (ref, sr)) if x.empty)
    common = sa.index.intersection(sb.index).intersection(sr.index)
    common = common[common <= asof]
    if common.empty:
        return None, None, f"no day on which {a}, {b} and {ref} are all stored"
    d = common.max()
    if side == "ours":
        return float(sa.loc[d] * sb.loc[d]), str(d.date()), f"{a} x {b} on {d.date()}"
    return float(sr.loc[d]), str(d.date()), f"{ref} on {d.date()}"


def _sums_on_common_day(p, rows, long, asof, a=(), b=(), side="ours", **_):
    """Two SUMS OF STOCKS on the latest day every one of their series holds (overnight 2026-10-06, B6: the dashboard's
    aiStaked + gamingStaked vs the two pools' on-chain balances, read on the same day)."""
    series = {m: _series(long, p, m) for m in (*a, *b)}
    if any(s.empty for s in series.values()):
        return None, None, "not stored: " + ", ".join(m for m, s in series.items() if s.empty)
    common = None
    for s in series.values():
        idx = s.index[s.index <= asof]
        common = idx if common is None else common.intersection(idx)
    if common is None or common.empty:
        return None, None, f"no day on which all of {', '.join(series)} are stored"
    d = common.max()
    legs = a if side == "ours" else b
    return float(sum(series[m].loc[d] for m in legs)), str(d.date()), f"{' + '.join(legs)} on {d.date()}"


def _months_match(p, rows, long, asof, daily="gross_burn_tokens", monthly="gross_burn_tokens_dune_monthly",
                  months=3, side="ours", **_):
    """The same COMPLETE calendar months on both sides: our daily rows summed per month vs the monthly
    series' row for that month (as SQL BX2 does) — never a 90-day window against one monthly row. `daily` may be a
    tuple: the series are added on the days every one holds (Sky's Revenue Allocation = SKY buyback $ + USDS paid to
    the lsSKY farm, 2026-10-07)."""
    if isinstance(daily, (tuple, list)):
        parts = [_series(long, p, x) for x in daily]
        d = sum(x.reindex(sorted(set.intersection(*(set(y.index) for y in parts)))) for x in parts) \
            if all(len(x) for x in parts) else pd.Series(dtype=float)
        daily = " + ".join(daily)
    else:
        d = _series(long, p, daily)
    m = _series(long, p, monthly)
    if d.empty or m.empty:
        return None, None, f"no {daily} or {monthly} in the store"
    last_full = (asof.normalize().to_period("M") - 1)
    picked = []
    for day in sorted(m.index, reverse=True):
        per = day.to_period("M")
        if per > last_full:
            continue
        span = d[(d.index >= per.start_time) & (d.index <= per.end_time.normalize())]
        if len(span) >= per.days_in_month - 1:          # one day of slack: the 1st can collide
            picked.append((per, float(span.sum()), float(m.loc[day])))
        if len(picked) >= months:
            break
    if not picked:
        return None, None, f"no complete month held by both {daily} and {monthly}"
    v = sum(x[1] if side == "ours" else x[2] for x in picked)
    names = ", ".join(str(x[0]) for x in sorted(picked))
    return v, str(picked[0][0]), f"{daily if side == 'ours' else monthly} summed over {names}"


def _hl_reward_active(p, rows, long, asof, **_):
    """The documented curve paid on ACTIVE stake only: 2.37% x sqrt(400M / S_total) x S_active, over our
    emissions' Q0 covered days — if this meets the observed emissions, inactive stake explains the gap.
    TIME-WEIGHTED where the split is stored (Jake's run 2026-10-05 21:00: ours 190,482 sat between the
    snapshot active curve 162,861 and the full 245,143 — 77.7% of full against a 66.4% active share TODAY):
    the curve is taken per stored day of the split and averaged, so a share that moved during Q0 is not
    read as today's. Commission is no part of it: emissions are the fall in futureEmissions, which pays
    validators' commission and delegators alike."""
    cov = _num((rows.get(f"{p}|emissions_tokens") or {}).get("q0_covered_days")) or 90.0
    lo, hi = _q0(asof)
    tot, off = _series(long, p, "locked_tokens"), _series(long, p, "locked_tokens_inactive")
    days = sorted(d for d in set(tot.index) & set(off.index) if lo < d <= hi) if len(tot) and len(off) else []
    if days:
        per_day = [0.0237 * math.sqrt(400e6 / float(tot.loc[d])) * (float(tot.loc[d]) - float(off.loc[d])) / 365.0
                   for d in days if float(tot.loc[d]) > 0]
        shares = [float(off.loc[d]) / float(tot.loc[d]) for d in days if float(tot.loc[d]) > 0]
        if per_day:
            v = sum(per_day) / len(per_day) * cov
            return v, str(days[-1].date()), (
                f"2.37% x sqrt(400M / S_total) x S_active per day, averaged over the {len(per_day)} day(s) "
                f"{days[0].date()}..{days[-1].date()} the split is stored, x {cov:.0f} covered day(s); inactive "
                f"share {min(shares):.1%}..{max(shares):.1%} (mean {sum(shares) / len(shares):.1%}, latest "
                f"{shares[-1]:.1%})" + ("" if len(per_day) >= cov * 0.8 else
                                       f" — the split covers {len(per_day)} of {cov:.0f} days, so the earlier "
                                       f"days are assumed to look like these"))
    s_tot = _num((rows.get(f"{p}|locked_tokens") or {}).get("now"))
    s_off = _num((rows.get(f"{p}|locked_tokens_inactive") or {}).get("now"))
    if not s_tot or s_off is None:
        return None, None, "no locked_tokens / locked_tokens_inactive stored"
    v = 0.0237 * math.sqrt(400e6 / s_tot) * (s_tot - s_off) / 365.0 * cov
    return v, (rows.get(f"{p}|locked_tokens") or {}).get("latest_date"), (
        f"2.37% x sqrt(400M / {s_tot / 1e6:,.1f}M) x ACTIVE {(s_tot - s_off) / 1e6:,.1f}M "
        f"({s_off / s_tot:.1%} of stake inactive, TODAY's snapshot only) / 365 x {cov:.0f} day(s)")


def _scans_q0(p, rows, long, asof, metrics=(), labels=(), require_all=False, **_):
    """THE GAUGE PAYOUTS CHAIN BY CHAIN OVER Q0 (Pendle in_emissions, Jake's run 2026-10-09 11:41): each stored scan's Q0
    sum, listed per chain; a chain with no stored row is named, never counted as 0. `require_all` (the reference side,
    Jake's run 2026-10-09 ~14:10): with any chain missing there is no figure — a partial sum never meets a partial sum."""
    lo, hi = _q0(asof)
    parts, missing = [], []
    for m, lab in zip(metrics, labels or metrics):
        sr = _series(long, p, m)
        sr = sr[(sr.index > lo) & (sr.index <= hi)]
        if sr.empty:
            missing.append(lab)
            continue
        parts.append((lab, float(sr.sum()), sr.index.min(), sr.index.max()))
    if not parts:
        return None, None, "no gauge scan stored in Q0: " + ", ".join(missing)
    if require_all and missing:
        return None, None, (f"not every chain is counted yet — NONE STORED for {', '.join(missing)}; stored: "
                            + "; ".join(f"{lab} {v:,.2f}" for lab, v, _a, _b in parts))
    tot = sum(v for _l, v, _a, _b in parts)
    how = ("; ".join(f"{lab} {v:,.2f} ({a.date()}..{b.date()})" for lab, v, a, b in parts)
           + f" = {tot:,.2f} PENDLE" + (f". NOT YET SCANNED: {', '.join(missing)}" if missing else ""))
    return tot, str(max(b for _l, _v, _a, b in parts).date()), how


def _window_sum(p, rows, long, asof, metric="", end="", days=30, times_price=False, **_):
    """A daily flow summed over the `days` days ending on `end` (a reading's own window — a figure read by hand on one
    date covers the days before it, not the build's), valued at the same-day price_usd when `times_price`. A day
    missing its price is a missing day, said in the source text."""
    hi = pd.Timestamp(end) if end else asof.normalize() - pd.Timedelta(days=1)
    lo = hi - pd.Timedelta(days=int(days) - 1)
    sr = _series(long, p, metric)
    sr = sr[(sr.index >= lo) & (sr.index <= hi)]
    if sr.empty:
        return None, None, f"no {metric} in {lo.date()}..{hi.date()}"
    if times_price:
        px = _series(long, p, "price_usd").reindex(sr.index)
        miss = int(px.isna().sum())
        sr = (sr * px).dropna()
        if sr.empty:
            return None, None, f"no price_usd on the {metric} days in {lo.date()}..{hi.date()}"
    else:
        miss = 0
    return float(sr.sum()), str(sr.index[-1].date()), (
        f"{metric}{' x same-day price' if times_price else ''} summed over {len(sr)} stored day(s) in "
        f"{lo.date()}..{hi.date()} ({days}-day window)" + (f"; {miss} day(s) without a price left out" if miss else ""))


def _last30_annualised(p, rows, long, asof, metric="revenue_usd", **_):
    sr = _series(long, p, metric)
    sr = sr[(sr.index > asof - pd.Timedelta(days=30)) & (sr.index <= asof)]
    if sr.empty:
        return None, None, f"no {metric} in the last 30 days"
    return float(sr.sum()) * 365.0 / 30.0, str(sr.index[-1].date()), f"{metric}, last 30 days x 365/30"


def _window_vs_rate(p, rows, long, asof, flow="fees_usd", rate="", days=7, side="ours", **_):
    """A FLOW SUMMED OVER THE LAST `days` COMPLETE DAYS vs A PER-DAY RATE x `days` (overnight 2026-10-06, A4): Morpho's
    one-day comparison read nothing — a rate read at one instant against one day's fees. ours = the flow summed over
    the window (every day present, or nothing); ref = the mean of the rate's daily readings inside the window (the
    latest within 14 days if none falls inside) x days, so read-time effects average out."""
    end = asof.normalize() - pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=int(days) - 1)
    f = _series(long, p, flow)
    f = f[(f.index >= start) & (f.index <= end)]
    label = f"{start.date()}..{end.date()}"
    if side == "ours":
        if len(f) < int(days):
            return None, None, f"{flow}: {len(f)} of {days} complete days in {label}"
        return float(f.sum()), str(end.date()), f"{flow} summed over {label}"
    r = _series(long, p, rate)
    inside = r[(r.index >= start) & (r.index <= asof.normalize())]
    if inside.empty:
        recent = r[r.index >= asof.normalize() - pd.Timedelta(days=14)]
        if recent.empty:
            return None, None, f"no {rate} reading in the last 14 days"
        return float(recent.iloc[-1]) * int(days), str(recent.index[-1].date()), \
            f"{rate} latest reading ({recent.index[-1].date()}) x {days} days"
    return float(inside.mean()) * int(days), str(inside.index[-1].date()), \
        f"{rate}: mean of {len(inside)} reading(s) in {label} x {days} days"


def _rise_vs_flow(p, rows, long, asof, flow="", stock="", days=30, side="ours", **_):
    """A CUMULATIVE STOCK'S RISE against a FLOW over the same days (Sky, 2026-10-07: Block Analitica's cumulative SKY
    bought vs our flapper counted inflow). The window is the stock's own: its first and last day within the last `days`
    complete days; the flow is summed over the days after the first and up to the last, so both cover one span."""
    end = asof.normalize() - pd.Timedelta(days=1)
    st = _series(long, p, stock)
    st = st[(st.index >= end - pd.Timedelta(days=int(days))) & (st.index <= end)]
    if len(st) < 2:
        return None, None, f"fewer than two {stock} days in the last {days}"
    a, b = st.index[0], st.index[-1]
    if side == "ref":
        return float(st.iloc[-1] - st.iloc[0]), str(b.date()), f"d({stock}) {a.date()}..{b.date()}"
    fl = _series(long, p, flow)
    fl = fl[(fl.index > a) & (fl.index <= b)]
    if fl.empty:
        return None, None, f"no {flow} between {a.date()} and {b.date()}"
    return float(fl.sum()), str(b.date()), f"{flow} summed over ({a.date()}..{b.date()}], {len(fl)} day(s)"


def _common_days_sum(p, rows, long, asof, a="", b="", days=30, side="ours", min_days=20, b_times_price=False, **_):
    """TWO DAILY FLOWS OVER THE SAME DAYS (overnight 2026-10-06, B4/B11): each side summed over exactly the days in the
    last `days` complete days on which BOTH are stored — so a series still filling (a scan seeded forward) is never
    set against a full window of the other. Fewer than `min_days` shared days: nothing. `b_times_price`: b is in the
    native coin and is valued at the same-day price_usd (a day without a price is not shared)."""
    end = asof.normalize() - pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=int(days) - 1)
    sa, sb = _series(long, p, a), _series(long, p, b)
    if b_times_price:
        px = _series(long, p, "price_usd")
        sb = (sb * px.reindex(sb.index)).dropna()
    sa, sb = sa[(sa.index >= start) & (sa.index <= end)], sb[(sb.index >= start) & (sb.index <= end)]
    common = sa.index.intersection(sb.index)
    if len(common) < int(min_days):
        return None, None, f"{a} / {b}: {len(common)} shared day(s) in {start.date()}..{end.date()}, {min_days} needed"
    s = (sa if side == "ours" else sb).loc[common]
    return float(s.sum()), str(common.max().date()), \
        f"{a if side == 'ours' else b} summed over the {len(common)} day(s) both hold in {start.date()}..{end.date()}"


def _aero_rebase_formula(p, rows, long, asof, epochs=4, side="ref", **_):
    """THE REBASE FROM THE MINTER'S OWN FORMULA (overnight 2026-10-06, B3): Minter.calculateGrowth(E) =
    E x ((T - V) / T)^2 / 2 (aerodrome-finance/contracts@1ba30815 Minter.sol L135-139), with E the epoch's emission
    (our Minter read), V the veAERO voting supply and T the AERO total supply at the flip (our daily reads, latest on
    or before the epoch date). Summed over the last `epochs` epochs and set against the RewardsDistributor's own
    tokensPerWeek over the same epochs — summed because the distributor may book a flip's rebase to the week
    before it (a one-epoch offset cancels in a sum, all but one edge week)."""
    e = _series(long, p, "gross_issuance_tokens")
    r = _series(long, p, "emissions_tokens")
    v, t = _series(long, p, "ve_voting_power_tokens"), _series(long, p, "total_supply")
    if side == "ours":
        r = r[r.index < asof.normalize()]
        if len(r) < int(epochs):
            return None, None, f"emissions_tokens (rebase): {len(r)} epoch(s) stored, {epochs} needed"
        last = r.iloc[-int(epochs):]
        return float(last.sum()), str(last.index[-1].date()), f"RewardsDistributor tokensPerWeek, last {epochs} epochs"
    e = e[e.index < asof.normalize()]
    if len(e) < int(epochs) or v.empty or t.empty:
        return None, None, "Minter emission / veAERO voting supply / total supply not all stored"
    tot, used, after = 0.0, [], []
    for d, em in e.iloc[-int(epochs):].items():
        # THE NEAREST READING ON OR BEFORE THE FLIP; where the daily V / T reads begin after it (they are read forward
        # — Jake's probes15, root K: the row sat at "no reference" with four archive epochs stored), the first reading
        # after it, within one epoch, and said. V and T move by well under 1% a week, far inside the 10% band.
        def at(sr):
            b = sr[sr.index <= d]
            if len(b):
                return float(b.iloc[-1]), None
            a = sr[(sr.index > d) & (sr.index <= d + pd.Timedelta(days=7))]
            return (float(a.iloc[0]), a.index[0]) if len(a) else (None, None)
        (V, va), (T, ta) = at(v), at(t)
        if V is None or T is None:
            return None, None, f"no veAERO voting supply / total supply within a week of {d.date()}"
        if va is not None or ta is not None:
            after.append(str(d.date()))
        if T <= 0 or V > T:
            return None, None, f"inconsistent supplies on {d.date()} (V {V:,.0f} > T {T:,.0f})"
        tot += float(em) * ((T - V) / T) ** 2 / 2
        used.append(str(d.date()))
    return tot, used[-1], (f"Minter.calculateGrowth over epochs {', '.join(used)}"
                           + (f" (V / T from the first daily read after the flip for {', '.join(after)})"
                              if after else ""))


def _aero_epochs(long, p, asof, onchain, flow, epoch_days=7, fees="voter_rewards_onchain_fees_usd",
                 bribes="voter_rewards_onchain_bribes_usd"):
    """THE STORED ON-CHAIN EPOCHS INSIDE Q0, EACH AGAINST ITS LIKE-FOR-LIKE DEFILLAMA FIGURE (Jake's run 2026-10-09
    ~14:10, 1a). Verified from the contracts (aerodrome-finance/contracts @1ba3081): fees reach FeesVotingReward only
    through Gauge._claimFees() inside Gauge.notifyRewardAmount (gauges/Gauge.sol L78-102, L197-203), which only the
    Voter calls, from _distribute()/distribute() (Voter.sol L486-513); Reward._notifyRewardAmount books them to
    epochStart(block.timestamp) (rewards/Reward.sol L240-247) — the epoch distribute() runs in. So epoch E's fees are
    those EARNED in E-1; Slipstream's CLGauge does the same (slipstream @f8717fa, contracts/gauge/CLGauge.sol
    L295-313, L356-382). Bribes are booked to the epoch they are deposited in. DefiLlama books staked-LP fees on the SWAP day and bribes on
    the NotifyReward day (dimension-adapters @0219a7b dexs/aerodrome/index.ts L59-79, L214-240). LIKE FOR LIKE: ours_E
    = DefiLlama over E-1's seven days - E-1's bribes + E's bribes, the bribes taken from the on-chain split
    (BribeVotingReward, the same NotifyReward events DefiLlama reads; its label split is not stored here) — so the
    comparison tests the fee leg, with the bribe leg the same events on both sides. An epoch whose split is not stored
    for it or the epoch before, or whose E-1 days are not all in DefiLlama, is left out and named.
    Returns ([(epoch start, on-chain total, ours like-for-like, detail)], [skipped])."""
    lo, hi = _q0(asof)
    oc, fl = _series(long, p, onchain), _series(long, p, flow)
    fe, br = _series(long, p, fees), _series(long, p, bribes)
    got, skipped = [], []
    for e, v in oc[(oc.index > lo) & (oc.index <= hi)].items():
        days = pd.date_range(e, periods=int(epoch_days), freq="D")
        prev = e - pd.Timedelta(days=int(epoch_days))
        pdays = pd.date_range(prev, periods=int(epoch_days), freq="D")
        if days[-1] >= asof.normalize():
            skipped.append(f"{e.date()} (epoch not complete)")
            continue
        if e not in br.index or prev not in br.index:
            skipped.append(f"{e.date()} (on-chain fees/bribes split not stored for "
                           + " and ".join(str(x.date()) for x in (prev, e) if x not in br.index) + ")")
            continue
        miss = [d for d in pdays if d not in fl.index]
        if miss:
            skipped.append(f"{e.date()} ({len(miss)} day(s) of {flow} missing in the week before)")
            continue
        dl_prev = float(fl.loc[pdays].sum())
        ours = dl_prev - float(br.loc[prev]) + float(br.loc[e])
        det = {"dl_prev": dl_prev, "b_prev": float(br.loc[prev]), "b": float(br.loc[e]),
               "f": float(fe.loc[e]) if e in fe.index else None, "days": days, "pdays": pdays}
        got.append((e, float(v), ours, det))
    return got, skipped


def _aero_epoch_revenue(p, rows, long, asof, flow="revenue_usd", onchain="voter_rewards_onchain_usd", side="ref", **_):
    """JUDGED OVER THE QUARTER (Jake's run 2026-10-09 ~14:10, 1b): the sum over every judged Q0 epoch of DefiLlama's
    like-for-like figure (_aero_epochs: the week before's fees + the epoch's own bribes) against the sum of the fees +
    bribes notified on-chain for the same epochs; the working lists every epoch's gap and the per-epoch spread."""
    got, skipped = _aero_epochs(long, p, asof, onchain, flow)
    if not got:
        return None, None, ("no complete Q0 epoch with the on-chain split and DefiLlama's days stored"
                            + (": " + "; ".join(skipped) if skipped else " — fetch/aero_voter.py stores them"))
    unp = _series(long, p, "voter_rewards_unpriced_count")
    table, gaps = [], []
    for e, oc, ours, d in got:
        gap = ours / oc - 1 if oc else float("inf")
        gaps.append(gap)
        n = f", {int(unp.loc[e])} token(s) unpriced on-chain" if e in unp.index else ""
        fee = (f"; fee leg: DefiLlama {d['dl_prev'] - d['b_prev']:,.0f} vs on-chain {d['f']:,.0f} "
               f"({(d['dl_prev'] - d['b_prev']) / d['f'] - 1:+.1%})" if d["f"] else "")
        table.append(f"epoch {e.date()}: DefiLlama {d['pdays'][0].date()}..{d['pdays'][-1].date()} ${d['dl_prev']:,.0f} "
                     f"- bribes then ${d['b_prev']:,.0f} + bribes now ${d['b']:,.0f} = ${ours:,.0f} vs on-chain "
                     f"${oc:,.0f} ({gap:+.1%}{n}{fee})")
    s_ours, s_oc = sum(g[2] for g in got), sum(g[1] for g in got)
    # THE FEE LEG ON ITS OWN (Jake's run 2026-10-09 15:59, 3): DefiLlama's week before less that week's on-chain bribes
    # against the epoch's on-chain fees — the part of the comparison not built from our own bribes
    legs = [(d["dl_prev"] - d["b_prev"], d["f"]) for _e, _oc, _o, d in got if d["f"]]
    fee = ""
    if legs:
        lg = [a / b - 1 for a, b in legs]
        fee = (f" FEE LEG ({len(legs)} epoch(s)): DefiLlama ${sum(a for a, _ in legs):,.0f} vs on-chain fees "
               f"${sum(b for _, b in legs):,.0f} ({sum(a for a, _ in legs) / sum(b for _, b in legs) - 1:+.1%}); "
               f"per-epoch spread {min(lg):+.1%}..{max(lg):+.1%}, mean |gap| {sum(abs(x) for x in lg) / len(lg):.1%}.")
    how = (f"QUARTER: {len(got)} epoch(s) {got[0][0].date()}..{got[-1][0].date()}: DefiLlama like-for-like "
           f"${s_ours:,.0f} vs on-chain ${s_oc:,.0f} ({s_ours / s_oc - 1:+.1%}); per-epoch spread {min(gaps):+.1%}.."
           f"{max(gaps):+.1%}, mean |gap| {sum(abs(x) for x in gaps) / len(gaps):.1%}.{fee} Per epoch: " + "; ".join(table)
           + (f". Not judged: {'; '.join(skipped)}" if skipped else "")
           + ". Unpriced reward tokens are left out of the on-chain figure, so it can read LOW by their value.")
    return (s_ours if side == "ours" else s_oc), str(got[-1][0].date()), how


def _aero_fee_leg(p, rows, long, asof, flow="revenue_usd", onchain="voter_rewards_onchain_usd", tol=10.0, **_):
    """THE FEE LEG AS A FINDING (Jake's run 2026-10-09 18:13, 3): DefiLlama's week before less that week's on-chain
    bribes, against the fees credited on-chain at the epoch (the part of the like-for-like not built from our own
    bribes). 1 when the quarter's like-for-like is within `tol` AND DefiLlama's fee leg sums BELOW the on-chain fees —
    DefiLlama under-counts Aerodrome's voter fees, Dune's per-epoch fees agree with on-chain, and on-chain is the
    reference; 0 otherwise (the comparison is judged on its tolerance). The working: the quarter, the typical (median)
    week, and every week more than 10% low."""
    got, _sk = _aero_epochs(long, p, asof, onchain, flow)
    legs = [(e, d["dl_prev"] - d["b_prev"], d["f"]) for e, _oc, _o, d in got if d["f"]]
    if not legs:
        return 0.0, None, "no epoch with the on-chain fees split stored"
    a, b = sum(x for _e, x, _f in legs), sum(f for _e, _x, f in legs)
    gaps = sorted((x / f - 1, e) for e, x, f in legs)
    med = gaps[len(gaps) // 2][0] if len(gaps) % 2 else (gaps[len(gaps) // 2 - 1][0] + gaps[len(gaps) // 2][0]) / 2
    low = [f"{e.date()} {g:+.1%}" for g, e in gaps if g < -0.10]
    lfl = sum(g[2] for g in got) / sum(g[1] for g in got) - 1
    how = (f"FEE LEG over {len(legs)} epoch(s): DefiLlama ${a:,.0f} vs on-chain fees ${b:,.0f} ({a / b - 1:+.1%}); "
           f"typical (median) week {med:+.1%}; weeks more than 10% low: {', '.join(low) or 'none'}. Like-for-like "
           f"quarter {lfl:+.1%}.")
    return (1.0 if a < b and abs(lfl) * 100 <= float(tol) else 0.0), str(legs[-1][0].date()), how


def _aero_epoch_apr(p, rows, long, asof, flow="holders_revenue_usd", onchain_apr="voter_rewards_onchain_apr",
                    onchain_usd="voter_rewards_onchain_usd", stake="voter_total_weight_tokens", rebase="emissions_tokens",
                    side="ref", **_):
    """DOES THE HEADLINE'S ARITHMETIC REPRODUCE THE EPOCHS? Over the quarter (Jake's run 2026-10-09 ~14:10, 1b): per
    judged epoch, ours = DefiLlama's like-for-like dollars (_aero_epochs) and the reference = the on-chain fees +
    bribes, both put in AERO at the epoch's payment-weighted price (one price on both sides, Jake's price convention),
    x 365.25/7, over THAT EPOCH's stake (Voter.totalWeight at the epoch's start — archive-read, 1c; else the nearest
    read within 14 days); the row is the MEAN of the per-epoch APRs on each side. The working prints the HEADLINE's own
    arithmetic, every epoch, and the veAERO rebase as its own labelled line."""
    got, skipped = _aero_epochs(long, p, asof, onchain_usd, flow)
    apr = _series(long, p, onchain_apr)
    px = _series(long, p, "price_usd")
    fl = _series(long, p, flow)
    table, mine, theirs_l = [], [], []
    for e, oc, usd, d in got:
        paid = [(x, float(fl.loc[x]), float(px.loc[x])) for x in d["days"] if x in fl.index and x in px.index
                and px.loc[x]]
        st = _stake_near(long, p, stake, e, 14)
        if not paid or st is None:
            skipped.append(f"{e.date()} (no " + (stake if st is None else "same-day price") + ")")
            continue
        p_eff = sum(u for _x, u, _q in paid) / sum(u / q for _x, u, q in paid)
        ours = usd / p_eff * 365.25 / 7 / st[0]
        theirs = oc / p_eff * 365.25 / 7 / st[0]
        mine.append(ours)
        theirs_l.append(theirs)
        table.append(f"epoch {e.date()}: ${usd:,.0f} vs ${oc:,.0f} at ${p_eff:,.4f} x 365.25/7 / {st[0]:,.0f} "
                     f"(totalWeight {st[1]}) = {ours:.2%} vs {theirs:.2%} ({ours / theirs - 1:+.1%})"
                     + (f"; stored on-chain APR {float(apr.loc[e]):.2%}" if e in apr.index else ""))
    lo, hi = _q0(asof)
    il = _payday_illiquid(p) or {}
    hd = payday_headline(fl, px, _series(long, p, stake), lo, hi, 365.25, _payday_epochs(p),
                         _series(long, p, il["fees"]) if il else None, _series(long, p, il["bribes"]) if il else None,
                         bool(il))
    sn = _stake_near(long, p, stake, hi, 0)
    head = f"HEADLINE: {hd['how']}." if hd else ""
    rb = _series(long, p, rebase)
    if not rb.empty and sn:
        head += (f" REBASE (a separate stream, in neither figure): {float(rb.iloc[-1]):,.0f} AERO in the week of "
                 f"{rb.index[-1].date()} x 52 / {sn[0]:,.0f} = {float(rb.iloc[-1]) * 52 / sn[0]:.2%} a year to lockers.")
    if not mine:
        return None, None, (head + " No epoch to judge" + (": " + "; ".join(skipped) if skipped else
                                                           " — fetch/aero_voter.py stores them")).strip()
    m_o, m_t = sum(mine) / len(mine), sum(theirs_l) / len(theirs_l)
    how = (head + f" QUARTER: mean of {len(mine)} epoch APR(s) {m_o:.2%} vs on-chain {m_t:.2%} "
           f"({m_o / m_t - 1:+.1%}). Per epoch: " + "; ".join(table)
           + (f". Not judged: {'; '.join(skipped)}" if skipped else ""))
    return (m_o if side == "ours" else m_t), str(got[-1][0].date()), how


def _aero_bribe_outliers(p, rows, long, asof, value="flag", factor=5.0, flow="holders_revenue_usd",
                         bribes="voter_rewards_onchain_bribes_usd", illiquid="voter_rewards_illiquid_bribes_usd",
                         stake="voter_total_weight_tokens", **_):
    """SINGLE-EPOCH BRIBE OUTLIERS, AFTER THE ILLIQUID-TOKEN RULE (Jake's run 2026-10-09 15:59, 2: epoch 2026-09-03's
    bribes $7,595,469 against a normal $55K-$260K, 30% of the quarter's on-chain voter revenue). Per Q0 epoch (the epoch
    before Q0 to the last stored): bribes at the quoted price less the rule's excess = the bribes that count; an
    outlier is an epoch whose counted bribes exceed `factor` x the quarter's MEDIAN week. A priced bribe a sale could
    not realise is a pricing matter, settled by the rule; one that survives it is genuine, and a VERIFIED FINDING.
    value="flag": 1 = an outlier survives the rule, 0 = none, -1 = the epochs' bribes or the rule are not stored.
    value="yield": the headline EXCLUDING the outlier epochs' counted bribes too (a labelled second figure)."""
    lo, hi = _q0(asof)
    br, ib = _series(long, p, bribes), _series(long, p, illiquid)
    br = br[(br.index > lo - pd.Timedelta(days=7)) & (br.index <= hi)]
    if len(br) < 4:
        return (-1.0 if value == "flag" else None), None, (
            f"{len(br)} Q0 epoch(s) of {bribes} stored — the quarter's median needs them all (`python token_metrics.py "
            f"--seed aero_epochs`)")
    no_rule = [e for e in br.index if e not in ib.index]
    counted = pd.Series({e: float(v) - float(ib.get(e, 0.0)) for e, v in br.items()}).sort_index()
    med = float(counted.median())
    out = [e for e, v in counted.items() if med > 0 and v > factor * med]
    table = "; ".join(f"{e.date()} ${float(br[e]):,.0f}" + (f" - ${float(ib[e]):,.0f} illiquid" if e in ib.index and ib[e]
                                                             else "") + (" OUTLIER" if e in out else "")
                      for e in counted.index)
    how = (f"{len(counted)} epoch(s): median week ${med:,.0f}, outlier above {factor:g}x = ${factor * med:,.0f}. "
           f"Per epoch (bribes at the quoted price - the rule's excess): {table}"
           + (f". The illiquid-token rule is NOT stored for {', '.join(str(e.date()) for e in no_rule)}" if no_rule
              else ""))
    if value == "flag" and (no_rule or not out):
        return (-1.0 if no_rule else 0.0), str(counted.index[-1].date()), how
    il = _payday_illiquid(p) or {}
    fe = _series(long, p, il["fees"]) if il else None
    px, st, fl = _series(long, p, "price_usd"), _series(long, p, stake), _series(long, p, flow)
    hd = payday_headline(fl, px, st, lo, hi, 365.25, _payday_epochs(p), fe, ib, bool(il))
    if hd is None:
        return None, None, how + ". No headline (flow, price or stake missing)"
    if not out:
        return hd["value"], str(counted.index[-1].date()), how + f". No outlier: the headline stands, {hd['value']:.2%}"
    extra = pd.Series({e: float(counted[e]) for e in out})
    base, _took, _r = illiquid_flow(fl, fe, ib, 7)
    adj, took, _r = illiquid_flow(base, None, extra, 7, lo, hi)
    r = payday_yield(adj, px, st, lo, hi, 365.25, _payday_epochs(p))
    if r is None:
        return (1.0 if value == "flag" else None), str(counted.index[-1].date()), how
    how += (f". EXCLUDING the outlier epoch(s) {', '.join(str(e.date()) for e in out)} (a further "
            f"${took['bribes']:,.0f} of bribes): {r['value']:.2%}, against the headline {hd['value']:.2%}")
    return (1.0 if value == "flag" else r["value"]), str(counted.index[-1].date()), how


def _aero_epoch_pending(p, rows, long, asof, flow="revenue_usd", onchain="voter_rewards_onchain_usd",
                        until="2026-10-10", split="voter_rewards_onchain_bribes_usd", **_):
    """1 while the latest stored on-chain epoch is complete but DefiLlama's days for it are not all stored yet, and
    `until` has not passed (Jake's run 2026-10-09 ~10:15, 4d: MATURING until 2026-10-10 with the exact reason; CHECK if
    still missing then). The working names the epoch and the missing day(s)."""
    oc = _series(long, p, onchain)
    fl = _series(long, p, flow)
    # THE LAST COMPLETE EPOCH (Thursday 00:00 UTC starts), whether or not it is stored (Jake's run 2026-10-09 11:41, 3c:
    # no on-chain epoch was stored at all, so the rows read "CHECK (no reference)" instead of naming what was missing).
    a = asof.normalize()
    last_start = a - pd.Timedelta(days=(a.dayofweek - 3) % 7) - pd.Timedelta(days=7)
    e = oc.index[-1] if not oc.empty and oc.index[-1] >= last_start else last_start
    days = pd.date_range(e, periods=7, freq="D")
    if days[-1] >= a:
        return 0.0, None, ""
    miss = [d for d in days if d not in fl.index]
    no_onchain = e not in oc.index
    # THE SPLIT TOO (Jake's run 2026-10-09 ~14:10, 1a): the like-for-like reference needs the epoch's bribes and the
    # epoch before's
    sp = _series(long, p, split) if split else None
    no_split = [x for x in (e - pd.Timedelta(days=7), e) if sp is not None and x not in sp.index]
    if not miss and not no_onchain and not no_split:
        return 0.0, None, ""
    # STILL MISSING AFTER `until` IS A CHECK (Jake's run 2026-10-09 ~10:15, 4d), not a quarter judged without it —
    # EXCEPT WHEN THE TIER TIMED OUT (Jake's run 2026-10-09 15:59: "the 10-10 MATURING must not lapse to CHECK just
    # because the tier timed out"): the on-chain part not stored because fetch/aero_voter.py was abandoned at its
    # budget stays MATURING, and says so
    timed = _tier_timed_out("aero_voter") if (no_onchain or no_split) else None
    pending = a <= pd.Timestamp(until) or (timed is not None and not miss)
    tag = f" — not stored (tier timeout: the aero_voter tier was abandoned at its budget, {timed[0]})" if timed else ""
    why = []
    if no_onchain:
        why.append(f"the on-chain figure (fetch/aero_voter.py, {onchain}) for epoch {e.date()} is not stored{tag}")
    if no_split and not no_onchain:
        why.append(f"the on-chain fees/bribes split ({split}) for {', '.join(str(x.date()) for x in no_split)} is not "
                   f"stored (fetch/aero_voter.py backfills every Q0 epoch; `python token_metrics.py --seed "
                   f"aero_epochs` stores them in one sitting){tag}")
    if miss:
        why.append(f"DefiLlama's {flow} for {', '.join(str(d.date()) for d in miss)} is not stored")
    return (1.0 if pending else -1.0), str(e.date()), (
        f"epoch {e.date()} (7 UTC days {days[0].date()}..{days[-1].date()}): " + "; ".join(why) + f", as of {a.date()}")


def _price_basis_gap(p, rows, long, asof, flow="holders_revenue_usd", stake="voter_total_weight_tokens",
                     price="price_usd", second="price_usd_coinbase", moved=0.05, agree=0.02, value="flag", **_):
    """THE HEADLINE'S PRICE BASIS, CONFIRMED FROM TWO SOURCES (Jake's run 2026-10-09 ~10:15, 4a/b, Aerodrome: the dollars
    agree, the whole headline gap is price — Q0 mean $0.5435 vs $0.8144 now). Returns 1 when AERO's spot differs from
    the payment-weighted Q0 price (the headline's basis, Jake's convention 2026-10-09) by more than `moved` AND
    CoinGecko's and Coinbase's payment-weighted prices agree within `agree`; -1 when they do not (or Coinbase is
    missing); 0 otherwise. The working gives the APR at today's price beside the headline at payment-day prices."""
    lo, hi = _q0(asof)
    a_all, b_all = _series(long, p, price), _series(long, p, second)
    a, b = a_all[(a_all.index > lo) & (a_all.index <= hi)], b_all[(b_all.index > lo) & (b_all.index <= hi)]
    if a.empty:
        return 0.0, None, f"no {price} in Q0"
    fl, stk = _series(long, p, flow), _series(long, p, stake)
    # THE ILLIQUID-TOKEN RULE HERE TOO (Jake's run 2026-10-09 18:13: today's-price figure 12.2% beside a 12.1% headline
    # was built on the uncapped flow — and called the headline 17.55%): the same flow the headline uses
    il = _payday_illiquid(p)
    if il:
        fl, _took, _r = illiquid_flow(fl, _series(long, p, il["fees"]), _series(long, p, il["bribes"]),
                                      int(_payday_epochs(p) or 7))
    rq = fl[(fl.index > lo) & (fl.index <= hi)]
    sn = _stake_near(long, p, stake, hi.normalize(), 0)
    spot = float(a.iloc[-1])
    desc = lambda s, n: (f"{n}: start ${float(s.iloc[0]):,.4f}, min ${float(s.min()):,.4f}, max ${float(s.max()):,.4f}, "  # noqa: E731
                         f"mean ${float(s.mean()):,.4f}, last ${float(s.iloc[-1]):,.4f} ({len(s)} days)")
    how = desc(a, "CoinGecko") + ("; " + desc(b, "Coinbase") if len(b) else "; Coinbase: no Q0 days stored")
    # THE HEADLINE'S BASIS IS THE PAYMENT-DAY PRICE (Jake's price convention, 2026-10-09): what is compared with today's
    # price is the payment-weighted Q0 price ($ paid / tokens at each day's price), from each source.
    ep = _payday_epochs(p)
    pa, pb = payday_yield(fl, a_all, stk, lo, hi, 365.25, ep), payday_yield(fl, b_all, stk, lo, hi, 365.25, ep)
    if pa is None:
        return 0.0, None, how + f". No priced {flow} day or no {stake} in Q0"
    confirmed = pb is not None and abs(pb["p_eff"] / pa["p_eff"] - 1) <= agree
    how += (f". Payment-weighted Q0 price: CoinGecko ${pa['p_eff']:,.4f}"
            + (f", Coinbase ${pb['p_eff']:,.4f}" if pb else ", Coinbase n/a"))
    if value == "apr":                                     # the row's own figure: the APR at today's price
        if not len(rq) or not sn:
            return None, None, f"no {flow} in Q0 or no {stake}"
        return float(rq.sum()) * 365.25 / len(rq) / (sn[0] * spot), str(a.index[-1].date()), how
    if len(rq) and sn:
        annual = float(rq.sum()) * 365.25 / len(rq)
        how += (f". AT TODAY'S PRICE: ${annual:,.0f}/yr / ({sn[0]:,.0f} x ${spot:,.4f}) = {annual / (sn[0] * spot):.2%}; "
                f"the headline at payment-day prices: {pa['value']:.2%}; AERO {spot / float(a.iloc[0]) - 1:+.1%} over Q0 "
                f"(spot / payment-weighted price x{spot / pa['p_eff']:.2f})")
    if not confirmed:                                     # -1: the basis is not confirmed by a second source
        return -1.0, str(a.index[-1].date()), how + ". The payment-weighted price is NOT confirmed by Coinbase."
    return (1.0 if abs(spot / pa["p_eff"] - 1) > moved else 0.0), str(a.index[-1].date()), how


def _price_overlap(p, rows, long, asof, flow="holders_revenue_usd", price="price_usd", second="price_usd_coinbase",
                   min_days=30, side="ours", **_):
    """IS THE HEADLINE'S PRICE CONFIRMED BY A SECOND SOURCE? (Jake's run 2026-10-09 11:41, 3a: Coinbase has AERO only from
    2026-09-06, and on all 33 overlapping days it matched CoinGecko to 0.00%.) Over the Q0 days that hold a payment and
    BOTH prices: the payment-weighted price ($ paid / tokens at each day's price) from each source — ours CoinGecko's,
    the reference Coinbase's. Fewer than `min_days` overlapping days is no confirmation (None). The working gives the
    overlap, its first day and the largest single-day difference."""
    lo, hi = _q0(asof)
    fl, a, b = _series(long, p, flow), _series(long, p, price), _series(long, p, second)
    days = [d for d in fl.index if lo < d <= hi and d in a.index and d in b.index and a.loc[d] and b.loc[d]]
    q0_paid = [d for d in fl.index if lo < d <= hi]
    if len(days) < int(min_days):
        return None, None, (f"{second} and {price} overlap on {len(days)} of the {len(q0_paid)} Q0 payment days — "
                            f"fewer than {min_days}, so the price is not confirmed by a second source")
    usd = sum(float(fl.loc[d]) for d in days)
    pa = usd / sum(float(fl.loc[d]) / float(a.loc[d]) for d in days)
    pb = usd / sum(float(fl.loc[d]) / float(b.loc[d]) for d in days)
    worst = max(abs(float(b.loc[d]) / float(a.loc[d]) - 1) for d in days)
    how = (f"{len(days)} of the {len(q0_paid)} Q0 payment days hold both prices ({days[0].date()}..{days[-1].date()}"
           + (f"; {second} starts {b.index.min().date()}" if b.index.min() > lo else "")
           + f"); payment-weighted: {price} ${pa:,.4f} vs {second} ${pb:,.4f}; largest single-day difference "
           f"{worst:.2%}. At least {min_days} overlapping days in full agreement is accepted as confirmation")
    return (pa if side == "ours" else pb), str(days[-1].date()), how


def _aero_rebase_apr(p, rows, long, asof, rebase="emissions_tokens", stake="voter_total_weight_tokens", **_):
    """THE veAERO REBASE AS A RATE, ITS OWN LABELLED ROW (Jake's run 2026-10-08 11:27: ~2.5% at 485K AERO/week): the last
    complete week's RewardsDistributor tokensPerWeek x 52 / the votes cast. Paid in AERO to lockers — a separate stream
    from the fees + bribes in a3_protocol_yield, and in neither that figure nor its on-chain reference."""
    rb = _series(long, p, rebase)
    rb = rb[rb.index < asof.normalize()]
    st = _stake_near(long, p, stake, asof.normalize(), 0)
    if rb.empty or st is None:
        return None, None, f"no {rebase if rb.empty else stake} stored"
    return (float(rb.iloc[-1]) * 52 / st[0], str(rb.index[-1].date()),
            f"{float(rb.iloc[-1]):,.0f} AERO in the week of {rb.index[-1].date()} x 52 / {stake} {st[0]:,.0f}")


FORMULAS = {"sum_months": _sum_months, "free_float_now": _free_float_now, "window_vs_rate": _window_vs_rate,
            "aero_rebase_formula": _aero_rebase_formula, "common_days_sum": _common_days_sum, "eth_net_formula": _eth_net_formula,
            "sums_on_common_day": _sums_on_common_day,
            "eth_issuance_curve": _eth_issuance_formula, "flow_usd_over_price": _flow_usd_over_price,
            "delta_q0": _delta_q0, "delta_diff_q0": _delta_diff_q0, "hl_reward_formula": _hl_reward_formula,
            "share_price_growth": _share_price_growth, "per_day_x_covered": _per_day_x_covered,
            "value_on": _value_on, "epoch_apr": _epoch_apr, "published_epoch_aprs": _published_epoch_aprs, "epoch_reproduction": _epoch_reproduction, "epoch_mean_check": _epoch_mean_check, "epochs_first_party": _epochs_first_party, "aero_epoch_revenue": _aero_epoch_revenue, "aero_rebase_apr": _aero_rebase_apr, "aero_epoch_pending": _aero_epoch_pending, "aero_bribe_outliers": _aero_bribe_outliers, "aero_fee_leg": _aero_fee_leg, "price_basis_gap": _price_basis_gap, "price_overlap": _price_overlap,
            "aero_epoch_apr": _aero_epoch_apr, "q0_net_flow": _q0_net_flow,
            "negative_release": _negative_release, "sum_month": _sum_month, "last30_annualised": _last30_annualised,
            "common_day_value": _common_day_value, "months_match": _months_match,
            "hl_reward_active": _hl_reward_active, "base_reward_ceiling": _base_reward_ceiling,
            "rate_on_stake": _rate_on_stake, "reward_rate_apr": _reward_rate_apr, "trailing_token_yield": _trailing_token_yield,
            "sum_since": _sum_since, "schedule_month": _schedule_month, "rise_vs_flow": _rise_vs_flow,
            "product_on_common_day": _product_on_common_day, "now_sum": _now_sum,
            "log_price_growth": _log_price_growth, "bridge_reconciled": _bridge_reconciled,
            "daily_delta_plus_flow": _daily_delta_plus_flow, "window_sum": _window_sum, "scans_q0": _scans_q0, "now_combo": _now_combo}


def reference(project: str, spec: dict, rows: dict, long, asof) -> dict:
    """{value, date, source, mode, verdict, note} for one row's reference spec."""
    # A VERDICT THAT HOLDS ONLY WHILE ITS CONDITION DOES (Pendle 5c, 2026-10-09): `verdict_when` names a formula; a
    # value above 0 takes `positive`, otherwise `otherwise` — so a limitation lapses by itself once it no longer
    # applies, never by a new date. Either branch may be a static verdict or a live reference (Aerodrome's epoch rows:
    # MATURING while DefiLlama's days are pending, then the comparison itself).
    if spec.get("verdict_when"):
        vw = spec["verdict_when"]
        v, _d, how = FORMULAS[vw["py"]](project, rows, _long_for(vw, long), asof, **(vw.get("args") or {}))
        pick = (vw["positive"] if (v or 0) > 0 else
                vw["negative"] if (v or 0) < 0 and vw.get("negative") else vw["otherwise"])
        spec = ({**pick, "why": f"{pick.get('why', '')} {how}".strip()} if pick.get("verdict") or
                pick.get("force_verdict") else {**pick, "source": pick.get("source") or spec.get("source", "")})
        if spec.get("verdict_when"):                      # a branch may itself hold a condition (Aerodrome in_revenue)
            return reference(project, spec, rows, long, asof)
    src = spec.get("source", "")
    note = spec.get("note", "")
    if spec.get("verdict"):
        why = spec.get("why", "")
        res = spec.get("resolve")
        return {"value": None, "date": None, "source": src or "—", "mode": "static",
                "verdict": spec["verdict"], "note": why + (f" RESOLVE: {res}" if res else "")}
    mode = "same_source" if spec.get("same_source") else "independent"
    long = _long_for(spec, long)
    scale = float(spec.get("scale", 1.0))
    val = date = None
    if "manual" in spec:
        m = spec["manual"]
        val, date = _num(m["value"]), m.get("read_on")
        src = src or f"{m.get('source')} (read by {m.get('read_by')})"
        note = (note + " " if note else "") + "Manual record — the reference was read by a person, not fetched."
    elif "formula" in spec:
        val, date, how = FORMULAS[spec["formula"]](project, rows, long, asof, **(spec.get("args") or {}))
        src = src or how
        if val is None or (spec.get("show_how") and how != src):    # show_how: the working beside a named source
            note = (note + " " if note else "") + how
    elif spec.get("align_to"):
        ours = _series(long, project, spec["align_to"])
        ref = _series(long, project, spec["metric"])
        if len(ours):
            day = ours.index[-1]
            if day in ref.index:
                val, date = _num(ref.loc[day]), str(day.date())
            else:
                note = (note + " " if note else "") + (
                    f"no {spec['metric']} point on {day.date()} (our latest {spec['align_to']} day)")
    elif spec.get("metric"):
        r = rows.get(f"{project}|{spec['metric']}") or {}
        val, date = _num(r.get(spec.get("window", "now"))), r.get("latest_date")
        if val is None and spec.get("window", "now") == "now":
            sr = _series(long, project, spec["metric"])      # a reference-only metric: not on Data
            if len(sr):
                val, date = _num(sr.iloc[-1]), str(sr.index[-1].date())
        if val is None:
            note = (note + " " if note else "") + f"{spec['metric']} has no value in the store"
    if val is not None and val == 0 and spec.get("zero_is_missing"):
        val = None
        note = (note + " " if note else "") + "The reference reads 0, which this source uses for 'not published' " \
                                              "— treated as no reference."
    if val is not None:
        val *= scale
    if spec.get("force_verdict"):
        res = spec.get("resolve")
        return {"value": val, "date": None if date is None else str(date)[:10], "source": src, "mode": "static",
                "verdict": spec["force_verdict"], "tol": spec.get("tol"),
                "note": (note + " " if note else "") + spec.get("why", "") + (f" RESOLVE: {res}" if res else "")}
    # THE TOLERANCE OF THE BRANCH TAKEN (Jake's run 2026-10-09 18:13: Aerodrome's epoch rows read CHECK at -5.9% with a
    # blank tolerance — a `verdict_when` row's tol lives in its `otherwise` branch, and the row took the outer spec's)
    return {"value": val, "date": None if date is None else str(date)[:10], "source": src, "mode": mode,
            "verdict": None, "note": note, "tol": spec.get("tol"),
            **({"fresh_label": spec["fresh_label"]} if spec.get("fresh_label") else {})}


# THE READ-TIME VIEWS AS A LONG FRAME (Jake's run 2026-10-08: NEAR's buyback is the daily change of the three wallets'
# combined close — a VIEW — so a reference reading the raw store found nothing). Set by build_workbook.write_credibility
# from the groups aggregate() built; a spec opts in with "use_views": True. Raw store otherwise, as before.
VIEWS_LONG = None


def _long_for(spec: dict, long):
    # the flag may sit on the spec or inside its args (Jake's run 2026-10-08 11:27: NEAR's in_buyback carried it in
    # args, so the row read the raw store and printed "no figure" after the fix that was meant to cure exactly that)
    on = spec.get("use_views") or (spec.get("args") or {}).get("use_views")
    return VIEWS_LONG if on and VIEWS_LONG is not None else long


def ours_value(project: str, ours: dict, rows: dict, long, asof):
    """A Python-computed OUR value (an input compared on one date, or a month), or None when ours is
    a cell / Data reference written as a formula."""
    if "py" not in ours:
        return None
    v, _d, _how = FORMULAS[ours["py"]](project, rows, _long_for(ours, long), asof, **(ours.get("args") or {}))
    return v


def by_design_na(name: str, hid: str) -> str | None:
    """Why this headline cell carries no figure for this project BY DESIGN, or None."""
    p = config.PROJECT_BY_NAME[name]
    dest = (p.get("buyback_destination") or (p.get("actual_buyback") or {}).get("destination"))
    basis = config.issuance_basis(name)
    if hid == "a3_buyback_locked" and dest != "hold":
        return f"the buyback destination is '{dest}', not a hold — this column is for held buybacks only"
    if hid == "a3_protocol_yield" and name not in config.PROTOCOL_YIELD:
        why = config.PROTOCOL_YIELD_NOT_APPLICABLE.get(name)
        return f"no protocol staking yield: {why}" if why else "no protocol staking yield is declared for this project"
    if hid == "a4_gross_issuance" and basis == "pool_release_tokens":
        return "issuance is measured as pool release (pre-minted supply) — see the Pool release row"
    if hid == "a4_pool_release" and basis != "pool_release_tokens":
        return "supply is minted, not pre-minted — issuance is on the Gross issuance row"
    if hid in ("a4_crossover", "a4_gross_issuance") and p.get("issuance_declared_zero"):
        return "no issuance — burn only (issuance_declared_zero)"
    if hid in ("a3_circ_retirement", "a3_fdv_retirement", "a3_actual_buyback_pct", "a3_implied_buyback_pct"):
        fs = p.get("fee_split") or {}
        if fs.get("destination_model") == "distribute_to_voters" and \
                "actual_buyback_tokens" not in config.metrics_for_project(p):
            return "0 by design — no token is bought; fees go to voters in the pairs' own tokens"
    return None


# A DERIVED HEADLINE IS AS CREDIBLE AS ITS INPUTS: its verdict is a formula over its input rows — PASS
# only when every input passes on an independent reference; CHECK when any input checks; UNVERIFIABLE
# when any input has no check. "@x" names an input whose row differs by project (resolve_alias).
DERIVED_INPUTS = {
    "a4_burn_yield": ("a4_gross_burn", "in_circ"),
    "a4_crossover": ("a4_gross_burn", "@issuance"),
    "a4_net_change": ("a4_gross_burn", "@issuance"),
    "a4_net_change_pct": ("a4_net_change", "in_circ"),
    "a3_circ_retirement": ("@buyback", "in_circ"),
    "a3_fdv_retirement": ("@buyback", "@price"),
    "a3_implied_buyback_pct": ("@revenue", "@price", "in_circ"),
    "a3_actual_buyback_pct": ("@buyback", "@price", "in_circ"),
    "a3_net_absorption": ("@buyback", "@emissions"),
    "a3_protocol_yield": ("@revenue", "@locked", "@price"),
    "a1_fees_issuance": ("@fees", "@issuance"),
    "a1_validator_yield": ("@issuance", "@locked"),
    "a2_free_float": ("in_circ", "@locked"),
    "a2_ff_arr": ("a2_free_float", "@price", "@arr"),
    "a2_trajectory": ("a2_emissions", "a2_free_float"),
    "a2_rev_per_token": ("a2_customer_revenue", "a2_emissions"),
    "a2_rev_vs_emissions_value": ("a2_customer_revenue", "a2_emissions", "@price"),
}
ALIASES = {
    "@issuance": ("a4_gross_issuance", "a4_pool_release", "in_issuance"),
    "@price": ("in_price", "in_price_llama"),
    "@buyback": ("in_reserve_month", "in_buyback_july", "@burn_route", "in_buyback"),
    "@revenue": ("in_revenue",),
    "@locked": ("in_locked", "in_shares"),
    "@emissions": ("a2_emissions", "in_emissions"),
    "@fees": ("in_fees",),
    "@arr": ("in_arr", "a2_customer_revenue"),
}


def resolve_alias(name: str, alias: str, have: dict) -> str | None:
    """The row id this project uses for an "@" input, or None when it has none (an unchecked input)."""
    if not alias.startswith("@"):
        return alias if alias in have else None
    for cand in ALIASES[alias]:
        if cand == "@burn_route":
            route = ((config.PROJECT_BY_NAME[name].get("actual_buyback") or {}).get("destination")
                     or config.PROJECT_BY_NAME[name].get("buyback_destination"))
            if route == "burn" and "a4_gross_burn" in have:
                return "a4_gross_burn"
            continue
        if cand in have and have[cand].get("verdict") != "N/A":
            return cand
    p = config.PROJECT_BY_NAME[name]
    # A DECLARED ZERO IS A CHECKED INPUT: no issuance by design (Uniswap) / nothing locked by design
    # (Morpho) — the N/A row stands for it, and counts as passing.
    if alias == "@issuance" and p.get("issuance_declared_zero"):
        return next((c for c in ALIASES[alias] if c in have), None)
    if alias == "@locked" and p.get("free_float_lock_zero"):
        return next((c for c in ALIASES[alias] if c in have), None)
    # NOTHING IS EMITTED BY DESIGN (config.EMISSIONS_DECLARED_ZERO: Maple, Ether.fi — Jake's run 2026-10-08): the
    # sourced N/A emissions row stands for the input and counts as checked
    if alias == "@emissions" and name in config.EMISSIONS_DECLARED_ZERO:
        return "in_emissions" if "in_emissions" in have else None
    # THE EMISSION IS THE GROSS ISSUANCE (Jake's run 2026-10-08 11:27, NEAR): new NEAR is minted to validators, so the
    # gross-issuance row (BigQuery header supply) is the checked emissions input of buyback − emissions
    if alias == "@emissions" and name in config.EMISSIONS_ARE_ISSUANCE:
        row = config.EMISSIONS_ARE_ISSUANCE[name]
        return row if row in have else None
    # NOTHING IS BOUGHT BY DESIGN (config.BUYBACK_ROUTE_OVERRIDE "none": Aerodrome, Ethereum) — overnight 2026-10-06
    if alias == "@buyback" and (config.BUYBACK_ROUTE_OVERRIDE.get(name) or ("",))[0] == "none":
        return "in_buyback" if "in_buyback" in have else None
    return None


def generic_inputs(name: str) -> dict:
    """The input rows a project needs for its derived headlines, where config declares none: each says
    plainly where the figure comes from and what a second source would be. Only for metrics that apply."""
    p = config.PROJECT_BY_NAME[name]
    arch = set(p.get("archetypes") or ())
    ms = set(config.metrics_for_project(p))
    spec_p = config.CREDIBILITY.get(name) or {}
    has = lambda *ids: any(i in spec_p for i in ids)  # noqa: E731
    route = ((p.get("actual_buyback") or {}).get("destination") or p.get("buyback_destination"))
    out = {}
    override = config.BUYBACK_ROUTE_OVERRIDE.get(name)
    if override and override[0] == "none" and not has("in_buyback"):
        # A DECLARED ZERO IS A CHECKED INPUT (overnight 2026-10-06: Aerodrome's a3_net_absorption read "input
        # 'buyback' has no row" although no token is bought by design) — the N/A row stands for it.
        out["in_buyback"] = {"what": "Buyback (tokens)", "ours": {"metric": "actual_buyback_tokens", "window": "q0"},
                             "fmt": '#,##0;(#,##0);-',
                             "ref": {"verdict": "N/A", "why": f"nothing is bought by design: {override[1]}"}}
    if 3 in arch and "revenue_usd" in ms and not has("in_revenue"):
        out["in_revenue"] = {"what": "Revenue Q0 (DefiLlama)", "ours": {"metric": "revenue_usd", "window": "q0"},
                             "fmt": '$#,##0;($#,##0);-', "ref": {
                                 "verdict": "UNVERIFIABLE",
                                 "why": "DefiLlama is the only free aggregate of this protocol's revenue (Token Terminal "
                                        "is paid).",
                                 "resolve": "the protocol's own reported revenue (dashboard / reports) by hand"}}
    if 3 in arch and "actual_buyback_tokens" in ms and route != "burn" and "in_buyback" not in out and \
            not has("in_buyback", "in_reserve_month", "in_buyback_july"):
        out["in_buyback"] = {"what": "Actual buyback Q0 (tokens)",
                             "ours": {"metric": "actual_buyback_tokens", "window": "q0"}, "fmt": '#,##0;(#,##0);-',
                             "ref": {"verdict": "CHECK", "why": "no independent buyback figure is wired for this project.",
                                     "resolve": "the protocol's own buyback reporting, or an explorer sum of the "
                                                "buyback executor's purchases"}}
    if "locked_tokens" in ms and not has("in_locked", "in_shares") and not p.get("free_float_lock_zero"):
        out["in_locked"] = {"what": "Locked / staked tokens", "ours": {"metric": "locked_tokens", "window": "now"},
                            "fmt": '#,##0;(#,##0);-',
                            "ref": {"verdict": "CHECK",
                                    "why": "our figure is a contract read; the protocol's own staking page is the "
                                           "independent figure and is not read.",
                                    "resolve": "read 'total staked' on the protocol's staking page by hand and record it"}}
    if 1 in arch and "fees_usd" in ms and not has("in_fees"):
        out["in_fees"] = {"what": "Fees Q0 (DefiLlama)", "ours": {"metric": "fees_usd", "window": "q0"},
                          "fmt": '$#,##0;($#,##0);-',
                          "ref": {"verdict": "UNVERIFIABLE",
                                  "why": "DefiLlama is the only free chain-fee aggregate we read for this chain.",
                                  "resolve": "a block explorer's daily fee chart (e.g. Etherscan / Blockscout stats)"}}
    if "emissions_tokens" in ms and not has("in_emissions") and 2 not in arch:
        out["in_emissions"] = {"what": "Emissions Q0 (tokens)", "ours": {"metric": "emissions_tokens", "window": "q0"},
                               "fmt": '#,##0;(#,##0);-',
                               "ref": {"verdict": "CHECK",
                                       "why": "our emissions series is the only measure wired for this project.",
                                       "resolve": "the project's published emission schedule for the same window, by hand"}}
    if p.get("free_float_lock_zero") and not has("in_locked"):
        out["in_locked"] = {"what": "Locked tokens", "ours": {"metric": "locked_tokens", "window": "now"},
                            "fmt": '#,##0;(#,##0);-',
                            "ref": {"verdict": "N/A", "why": f"nothing is locked by design: {p['free_float_lock_zero']}"}}
    basis = config.issuance_basis(name)
    if 1 in arch and 4 not in arch and basis in ms and not has("in_issuance"):
        how = ("CoinGecko's own supply deltas (d circulating - d total)" if basis == "pool_release_tokens"
               else "the token contract's totalSupply changes")
        out["in_issuance"] = {"what": f"Issuance Q0 ({basis})", "ours": {"metric": basis, "window": "q0"},
                              "fmt": '#,##0;(#,##0);-',
                              "ref": {"verdict": "UNVERIFIABLE",
                                      "why": f"measured from {how}; no second publisher of this project's issuance.",
                                      "resolve": "the project's published emission / unlock schedule, by hand"}}
    return out


def price_inputs(name: str) -> dict:
    """Price beside a second source on the SAME instant (00:00 UTC of the latest completed day both hold):
    Coinbase Exchange where a product is declared, and DefiLlama's price for the token's verified address
    for every project (the second source where Coinbase has none, a third where it does)."""
    from fetch.xref import CrossRefs
    p = config.PROJECT_BY_NAME[name]
    fmt = '$#,##0.0000;($#,##0.0000);-'
    key, indep = CrossRefs.llama_key(p)
    out = {}
    product = (config.CREDIBILITY_XREF.get("coinbase_product") or {}).get(name)
    if product:
        out["in_price"] = {"what": f"Price vs Coinbase {product} (00:00 UTC, latest completed day both hold)",
                           "ours": {"py": "common_day_value", "args": {"metric": "price_usd", "ref": "price_usd_coinbase",
                                                                      "side": "ours"}}, "fmt": fmt,
                           "ref": {"formula": "common_day_value", "tol": 2.0,
                                   "args": {"metric": "price_usd", "ref": "price_usd_coinbase", "side": "ref"},
                                   "source": f"Coinbase Exchange {product}: the daily candle's OPEN = the price at 00:00 "
                                             f"UTC, the instant CoinGecko's daily point is stamped"}}
    out["in_price_llama"] = {
        "what": f"Price vs DefiLlama {key} (00:00 UTC, latest completed day both hold)",
        "ours": {"py": "common_day_value", "args": {"metric": "price_usd", "ref": "price_usd_llama", "side": "ours"}},
        "fmt": fmt,
        "ref": {"formula": "common_day_value", "tol": 2.0, "same_source": not indep,
                # a native coin's DefiLlama price is CoinGecko's relayed (sign-off round 2026-10-07): where Coinbase
                # lists it the exchange price is the independent check (in_price) and this row is a third reading;
                # where it does not (HYPE), the relay agreeing is recorded as a documented limitation
                **({} if indep else {"fresh_label": "N/A (relay of CoinGecko; Coinbase is the independent check)"
                                     if product else "DOCUMENTED LIMITATION (DefiLlama relays CoinGecko; no "
                                                     "exchange candle wired)"}),
                "args": {"metric": "price_usd", "ref": "price_usd_llama", "side": "ref"},
                "source": f"DefiLlama coins API, {key}" + ("" if indep else " — CoinGecko's own price relayed"),
                "note": ("DefiLlama prices a listed token partly from CoinGecko, so a match here is weaker evidence "
                         "than an exchange price." if indep else
                         "No token address on file (a native coin): DefiLlama relays CoinGecko — FRESH-only.")}}
    return out


def circulating_input(name: str) -> dict:
    """The circulating row every project gets. THE POLICY (Jake, 2026-10-07): the figure the ratios use is the
    project's own where one exists, else CoinGecko's; OUR ON-CHAIN SET is the credibility reference — a gap beyond
    tolerance flags the figure in use for review and never replaces it. Where definitions differ in a known way the
    comparison is like-for-like (CoinGecko counting every SKY vs our TOTAL; Pendle's CoinGecko figure vs our free
    float + sPENDLE). With no on-chain set, CoinGecko is the reference of a first-party figure."""
    from build_workbook import chosen_circulating_metric
    p = config.PROJECT_BY_NAME[name]
    ours_m = chosen_circulating_metric(p)
    spec = config.circulating_onchain(name) or {}
    st = spec.get("status")
    pol = (config.CIRCULATING_POLICY.get(name) or {})
    what = f"Circulating supply as the ratios use it ({ours_m}; on-chain status '{st}')"
    base = {"what": what, "ours": {"metric": ours_m, "window": "now"}, "fmt": '#,##0;(#,##0);-'}
    has_set = config.circulating_has_onchain_set(name)
    tol_set = 2.0 if st == "established" else 5.0
    partial_note = ("Partial set — it reads HIGH wherever non-circulating wallets are not yet identified, so a CHECK "
                    "questions either the figure in use or our exclusion list; the Config & Sources circulating notes "
                    "say which wallets are named." if st == "partial" else "")
    if config.coingecko_counts_total(name) and ours_m == "circulating_supply_onchain":
        # our set is primary (Sky since the full sweep, 2026-10-07) and CoinGecko counts every token: the like-for-like
        # comparison is our on-chain TOTAL against CoinGecko; ours is stricter by exactly the subtracted balances
        return {"what": f"Circulating as CoinGecko counts it = OUR TOTAL ({spec['total']}); ours ({ours_m}) is stricter "
                        f"by {' + '.join(spec.get('subtract') or ())}",
                "ours": {"metric": spec["total"], "window": "now"}, "fmt": '#,##0;(#,##0);-',
                "ref": {"metric": "circulating_supply", "window": "now", "tol": 2.0,
                        "source": "CoinGecko circulating_supply — it counts (almost) every token, so it is compared "
                                  "with our on-chain total, not with our stricter circulating",
                        "note": spec.get("project_definition") or spec.get("decision", "")}}
    if config.coingecko_counts_total(name) and ours_m == "circulating_supply":
        # CoinGecko counts every token (Sky): its like-for-like on-chain figure is our TOTAL
        return {"what": f"Circulating as the ratios use it = CoinGecko's, which counts every token: set against OUR "
                        f"on-chain TOTAL ({spec['total']}); our on-chain set is stricter by "
                        f"{' + '.join(spec.get('subtract') or ())}",
                "ours": {"metric": "circulating_supply", "window": "now"}, "fmt": '#,##0;(#,##0);-',
                "ref": {"metric": spec["total"], "window": "now", "tol": 2.0,
                        "source": f"on-chain {spec['total']} — CoinGecko counts (almost) every token, so the like-for-"
                                  f"like on-chain figure is the total, not our stricter set",
                        "note": pol.get("definition", "")}}
    if config.coingecko_is_free_float(name) and ours_m == "circulating_supply_onchain":
        legs = config.coingecko_counted_lock_legs(name)
        if legs:                                  # Pendle: CoinGecko excludes vePENDLE but still counts sPENDLE
            return {"what": f"Circulating as CoinGecko counts it = OUR FREE FLOAT + {' + '.join(legs)} (CoinGecko "
                            f"still counts them); ours is stricter by exactly that",
                    "ours": {"py": "free_float_now", "args": {"add_back": legs}}, "fmt": '#,##0;(#,##0);-',
                    "ref": {"metric": "circulating_supply", "window": "now", "tol": 2.0,
                            "source": "CoinGecko circulating_supply — it excludes the legacy lock but still counts "
                                      f"{', '.join(legs)}, so it is compared with our free float plus those",
                            "note": spec.get("decision", "")}}
        return {"what": f"Circulating as CoinGecko counts it = OUR FREE FLOAT ({ours_m} − locked)",
                "ours": {"py": "free_float_now", "args": {}}, "fmt": '#,##0;(#,##0);-',
                "ref": {"metric": "circulating_supply", "window": "now", "tol": 5.0,
                        "source": "CoinGecko circulating_supply — it excludes staked/locked tokens, so it is "
                                  "compared with our circulating − locked",
                        "note": spec.get("decision", "")}}
    if has_set and ours_m != "circulating_supply_onchain":
        # the project's figure (or CoinGecko's, or CoinGecko + the staked add-back) against our on-chain set
        return {**base, "ref": {"metric": "circulating_supply_onchain", "window": "now", "tol": tol_set,
                                "source": "OUR ON-CHAIN SET — total minus the named non-circulating holders "
                                          f"({st}); the independent cross-check under the circulating policy",
                                "note": " ".join(x for x in (f"Figure in use: {pol.get('source', ours_m)}.",
                                                              pol.get("add_back") and f"Staked add-back: "
                                                              f"{pol['add_back']}.", partial_note) if x)}}
    counts = config.coingecko_counts_holdings(name)
    ll = config.coingecko_like_for_like(name)
    if ll and ours_m == "circulating_supply_onchain":
        # CoinGecko ON OUR BASIS (Aerodrome, Jake's probes16): it excludes the PERMANENT locks (993.0M vs 990.2M), so
        # CoinGecko + permanent locks − the filing wallets' managed (permanent) locks; ours adds back the balances
        # CoinGecko still counts (the filing wallets' liquid AERO)
        a = ("circulating_supply_onchain", *counts)
        plus, minus = tuple(ll.get("plus") or ()), tuple(ll.get("minus") or ())
        return {"what": f"Circulating like-for-like: ours ({' + '.join(a)}) vs CoinGecko + {' + '.join(plus)} − "
                        f"{' − '.join(minus)}",
                "ours": {"py": "now_sum", "args": {"metrics": a}}, "fmt": '#,##0;(#,##0);-',
                "ref": {"formula": "now_combo", "args": {"metrics": ("circulating_supply", *plus), "minus": minus},
                        "tol": 2.0,
                        "source": "CoinGecko circulating_supply put on our basis (its exclusion is the permanent "
                                  "veAERO locks)",
                        "note": spec.get("decision", "")}}
    if counts and ours_m == "circulating_supply_onchain":
        # LIKE-FOR-LIKE (Jake's run 2026-10-07 17:08): our stricter on-chain figure PLUS the documented wallets
        # CoinGecko still counts, against CoinGecko on the same day; the wallets' sum is the in_circ_gap row
        a = ("circulating_supply_onchain", *counts)
        return {"what": f"Circulating as CoinGecko counts it = OURS ({ours_m}) + {' + '.join(counts)} (documented "
                        f"wallets our convention excludes and CoinGecko counts)",
                "ours": {"py": "now_sum", "args": {"metrics": a}},
                "fmt": '#,##0;(#,##0);-',
                "ref": {"metric": "circulating_supply", "window": "now",
                        "tol": 2.0, "source": "CoinGecko circulating_supply, the same day — compared with ours plus the "
                                              "wallets it still counts; a gap left beyond tolerance is a real "
                                              "disagreement",
                        "note": "The definitional gap (those wallets) is reported on its own row, in_circ_gap."}}
    if spec.get("reference") == "bridge_reconciliation" and ours_m == "circulating_supply_onchain":
        # GEODNET (Jake's probes15, root A): the reconciliation is the judge; the static aggregator figure is its own
        # "N/A (recorded, stale)" row (in_circ_static)
        return {**base, "ref": {"formula": "bridge_reconciled", "args": {"key": spec.get("reconciliation_key", "")},
                                "tol": 2.0,
                                "note": "PASS needs both: the bridge custody backs the whole bridged supply and the "
                                        "project's filing lists our wallets. The aggregator's static figure is on "
                                        "in_circ_static."}}
    if ours_m != "circulating_supply":           # first-party / on-chain chosen, no other set: CoinGecko
        static = spec.get("static_cross_check")
        return {**base, "ref": {"metric": "circulating_supply", "window": "now", "tol": 2.0,
                                "source": "CoinGecko circulating_supply (an aggregator's own count)"
                                          + (f" — a STATIC figure ({static['who']}: {static['figure']:,}); "
                                             f"{static['why_static']}" if static else "")}}
    return {**base, "ref": {"verdict": "CHECK",
                            "why": "CoinGecko is the only circulating figure we read and no on-chain set is established.",
                            "resolve": "read the project's own circulating figure (tokenomics / transparency page) "
                                       "by hand and record it in config.CREDIBILITY as a manual reference."}}


def build_rows(headline_cells: list[dict], rows: dict, long, asof, projects=None) -> list[dict]:
    """Every Credibility row, in project order: the headline cells (tab order), then the inputs."""
    import manual_refs
    names = [n for n in config.CREDIBILITY_PROJECTS if projects is None or n in projects]
    manual = manual_refs.by_row()
    out = []
    for name in names:
        spec_p = config.CREDIBILITY.get(name) or {}
        beside: dict = {}                            # manual readings recorded beside a headline (manual_refs)
        for hc in [h for h in headline_cells if h["project"] == name]:
            spec = spec_p.get(hc["id"])
            na = by_design_na(name, hc["id"]) if spec is None else None
            if na:
                spec = {"verdict": "N/A", "why": na}
            if spec is None and hc.get("closed"):
                cl = hc["closed"]
                spec = {"verdict": "UNVERIFIABLE",
                        "why": f"the figure itself is CLOSED (config UNAVAILABLE): {cl.get('summary', '')}"}
            if spec is not None and spec.get("finding_when"):
                # A FINDING THE DATA DECIDES (Jake's run 2026-10-08 11:27, Chainlink a1_fees_issuance): when the
                # named formula returns a value below the threshold, the row is that VERIFIED FINDING with the working;
                # otherwise the row's own spec (inputs or reference) stands
                fw = spec["finding_when"]
                fv, _fd, fhow = FORMULAS[fw["py"]](name, rows, _long_for(fw, long), asof, **(fw.get("args") or {}))
                if fv is not None and fv < fw.get("below", 0):
                    spec = {"verdict": "VERIFIED FINDING", "why": f"{fw['why']} EVIDENCE: {fhow}"}
            if spec is not None and spec.get("inputs"):
                out.append({"project": name, "tab": hc["sheet"], "cell": hc["cell"], "id": hc["id"],
                            "what": hc["header"], "ours": {"cell": f"'{hc['sheet']}'!{hc['cell']}"},
                            "fmt": hc.get("fmt"), "tol": None, "value": None, "date": None,
                            "source": spec.get("source", "its input rows (below)"), "mode": "derived",
                            "verdict": None, "inputs": tuple(spec["inputs"]), "note": spec.get("why", "")})
                continue
            if spec is None and hc["id"] in DERIVED_INPUTS:
                out.append({"project": name, "tab": hc["sheet"], "cell": hc["cell"], "id": hc["id"],
                            "what": hc["header"], "ours": {"cell": f"'{hc['sheet']}'!{hc['cell']}"},
                            "fmt": hc.get("fmt"), "tol": None, "value": None, "date": None,
                            "source": "its input rows (below)", "mode": "derived", "verdict": None,
                            "inputs": DERIVED_INPUTS[hc["id"]], "note": ""})
                continue
            if spec is None:
                spec = ({"verdict": "UNVERIFIABLE", "why": DERIVED_WHY} if hc["kind"] == "calc" else
                        {"verdict": "CHECK", "why": "no independent reference is wired for this figure yet."})
            if (name, hc["id"]) in manual:           # Jake's reading (manual_form.py) replaces the reference
                pg = manual_refs.page_for(name, hc["id"])
                if pg.get("beside"):                 # ... unless it is a different quantity: recorded beside it
                    rd = manual[(name, hc["id"])][0]
                    beside[pg["beside"]] = {
                        "what": f"{hc['header']} — {pg.get('tile', 'reading')} (recorded beside the headline)",
                        "ours": {"cell": f"'{hc['sheet']}'!{hc['cell']}"}, "fmt": hc.get("fmt"),
                        "ref": {"verdict": "N/A (recorded)",
                                "why": f"{rd['value']} read {rd.get('read_on')} by {rd.get('read_by') or 'Jake'} "
                                       f"({rd.get('url') or pg['url']}): {pg.get('beside_why', '')}"}}
                else:
                    spec = manual_refs.reference_for(manual[(name, hc["id"])], None)[0]
            ref = reference(name, spec, rows, long, asof)
            ours = {"cell": f"'{hc['sheet']}'!{hc['cell']}"}
            if spec.get("ours_py"):
                # OURS ON THE REFERENCE'S OWN DAYS (Jake's run 2026-10-08 11:27, Ethereum): the judged figure is
                # computed over exactly the days the reference covers; the headline cell is named beside it
                op = spec["ours_py"]
                ours = {"py": op["py"], "args": op.get("args") or {},
                        "value": ours_value(name, {**op, "use_views": spec.get("use_views")}, rows, long, asof),
                        "headline_cell": ours["cell"]}
            out.append({"project": name, "tab": hc["sheet"], "cell": hc["cell"], "id": hc["id"],
                        "what": hc["header"], "ours": ours,
                        "fmt": hc.get("fmt"), "tol": spec.get("tol"), **ref})
        inputs = dict(config.CREDIBILITY_COMMON_INPUTS)
        inputs.update(price_inputs(name))
        inputs["in_circ"] = circulating_input(name)
        gap_legs = config.coingecko_counts_holdings(name)
        if gap_legs and config.circulating_onchain_primary(name):
            inputs["in_circ_gap"] = {
                "what": f"Definitional gap: documented wallets our circulating excludes and CoinGecko counts "
                        f"({' + '.join(gap_legs)})",
                "ours": {"py": "now_sum", "args": {"metrics": gap_legs}},
                "fmt": '#,##0;(#,##0);-',
                # "N/A (recorded)", not "N/A": a bare N/A declares the cell EMPTY by design and a figure in it reads as
                # a contradiction (Jake's run 18:11: "CHECK (figure where none is expected)")
                "ref": {"verdict": "N/A (recorded)", "why": "recorded, not judged: the difference between our convention "
                                                 "(treasury, team/investor, foundation and operating wallets out) "
                                                 "and CoinGecko's. in_circ compares like-for-like."}}
        circ_spec = config.circulating_onchain(name) or {}
        if circ_spec.get("reference") == "bridge_reconciliation" and circ_spec.get("static_cross_check"):
            st = circ_spec["static_cross_check"]
            inputs["in_circ_static"] = {
                "what": f"Aggregator circulating ({st['who']}) — a STATIC figure, recorded beside ours",
                "ours": {"metric": "circulating_supply", "window": "now"}, "fmt": '#,##0;(#,##0);-',
                "ref": {"verdict": "N/A (recorded, stale)",
                        "why": f"recorded, never the judge: {st['figure']:,} ({st['who']}); {st['why_static']}. "
                               f"in_circ is judged by the bridge reconciliation."}}
        inputs.update(generic_inputs(name))
        inputs.update({k: v for k, v in spec_p.items() if k.startswith("in_")})
        inputs.update(beside)
        for iid, spec in inputs.items():
            if spec is None:
                continue
            spec = spec(name) if callable(spec) else spec
            if spec is None:
                continue
            if (name, iid) in manual:                # Jake's reading (manual_form.py) replaces the reference
                mref, mours = manual_refs.reference_for(manual[(name, iid)], spec.get("ours"))
                spec = {**spec, "ref": mref, **({"ours": mours} if mours else {})}
            ref = reference(name, spec["ref"], rows, long, asof)
            ours = dict(spec["ours"])
            if "py" in ours:
                ours["value"] = ours_value(name, ours, rows, long, asof)
                cell = f"computed: {ours['py']} {ours.get('args', {})}"
            elif "cell" in ours:
                cell = ours["cell"]
            else:
                cell = f"Data {ours['metric']} ({ours.get('window', 'now')})"
            out.append({"project": name, "tab": "input", "cell": cell, "id": iid, "what": spec["what"],
                        "ours": ours, "fmt": spec.get("fmt"), "tol": spec["ref"].get("tol"), **ref})
        # resolve each derived row's inputs to row positions; an input with no row is UNCHECKED
        mine = [r for r in out if r["project"] == name]
        have = {r["id"]: r for r in mine}
        pos = {r["id"]: i for i, r in enumerate(out) if r["project"] == name}
        for r in mine:
            if r["mode"] != "derived":
                continue
            got, missing = [], []
            for a in r["inputs"]:
                rid = resolve_alias(name, a, have)
                (got.append(pos[rid]) if rid else missing.append(a.lstrip("@")))
            r["input_rows"], r["missing_inputs"] = got, missing
            r["note"] = ((r["note"] + " ") if r.get("note") else "") + (
                         "Judged by: " + ", ".join(out[i]["id"] for i in got)
                         + (f". UNCHECKED input(s): {', '.join(missing)} — no row checks them" if missing else "")
                         + ". PASS only when every input passes on an independent reference.")
    return out


# THE ROOT-CAUSE MAP (Jake, 2026-10-06: "burn down the credibility table by impact"). A derived row fails
# because an input fails, so the rows to fix are the ROOTS: the non-derived rows (and the unchecked inputs)
# that every open headline traces back to. Ranked by how many headline rows each root holds open.
OPEN = ("CHECK", "UNVER")


def _fix_class(row: dict, verdict: str) -> tuple[str, str]:
    """(why it fails, fix class) for one ROOT row, from its verdict and its own note."""
    note = str(row.get("note") or "")
    resolve = note.split("RESOLVE:", 1)[1].strip() if "RESOLVE:" in note else ""
    by_hand = any(w in resolve.lower() for w in ("by hand", "read '", "read the", "record it", "manual"))
    if verdict.startswith("CHECK (no reference)"):
        return (f"the reference has no value in the store ({note[:140] or row.get('source', '')})",
                "code / data source: fetch the reference")
    if verdict.startswith("CHECK (no figure)"):
        return "our own figure is empty", "code: our figure is not computed"
    if verdict.startswith("CHECK (figure where none"):
        return "a figure where the design says there is none", "code: contradiction"
    if verdict.startswith("UNVER"):
        if resolve:
            return (note.split("RESOLVE:", 1)[0].strip()[:160] or "no independent source wired",
                    f"manual reading: {resolve[:140]}" if by_hand else f"data source: {resolve[:140]}")
        return note[:160] or "no independent source exists", "genuinely unverifiable"
    if row.get("mode") == "static":          # a literal CHECK: a reference exists but is not read
        return (note.split("RESOLVE:", 1)[0].strip()[:160] or "no independent reference wired",
                (f"manual reading: {resolve[:140]}" if by_hand else
                 f"data source: {resolve[:140]}" if resolve else "data source: wire an independent reference"))
    return "it disagrees with an independent reference beyond tolerance", "investigate: a real disagreement"


def root_causes(rows: list[dict], verdicts: list) -> list[dict]:
    """Every root blocking an open (CHECK / UNVERIFIABLE) row, ranked by headline rows blocked.
    `rows` are build_rows' rows in tab order; `verdicts` the tab's evaluated verdict per row."""
    v = [str(x or "") for x in verdicts]
    memo: dict[int, set] = {}

    def roots(i: int) -> set:
        if i in memo:
            return memo[i]
        memo[i] = set()                       # no cycles in practice; never recurse forever
        r, vi = rows[i], v[i]
        if r.get("mode") != "derived":
            got = {("row", i)} if vi.startswith(OPEN) else set()
        else:
            ins = r.get("input_rows") or []
            # A CHECK (inputs) is held open by its CHECK inputs; an UNVERIFIABLE (inputs) by its
            # unverifiable / unchecked ones (no input checks, or the verdict would be CHECK).
            want = ("CHECK",) if vi.startswith("CHECK") else ("UNVER", "FRESH")
            got = set().union(*[roots(j) if v[j].startswith(OPEN) else {("row", j)}
                                for j in ins if v[j].startswith(want)] or [set()])
            if not vi.startswith("CHECK"):
                got |= {("unchecked", r["project"], m) for m in r.get("missing_inputs") or ()}
                if not ins:
                    got |= {("unchecked", r["project"], "every input")}
        memo[i] = got
        return got

    table: dict = {}
    for i, r in enumerate(rows):
        if not v[i].startswith(OPEN):
            continue
        for key in roots(i):
            e = table.setdefault(key, {"headline": [], "rows": []})
            e["rows"].append(i)
            if r.get("tab") != "input":
                e["headline"].append(i)
    out = []
    for key, e in table.items():
        if key[0] == "row":
            rr, vv = rows[key[1]], v[key[1]]
            why, fix = _fix_class(rr, vv)
            if vv.startswith("FRESH"):
                why, fix = ("the only reference re-reads our own source (FRESH-only)",
                            "data source: an independent second source")
            label = f"{rr['project']} | {rr['id']} | {rr['what']}"
            verdict = vv
        else:
            label = f"{key[1]} | (input '{key[2]}' has no row)"
            verdict, why, fix = "UNCHECKED", "no row checks this input", "code: wire an input row"
        out.append({"root": label, "verdict": verdict, "headline_rows": len(e["headline"]),
                    "rows": len(e["rows"]), "why": why, "fix": fix,
                    "blocks": sorted({f"{rows[i]['project']}:{rows[i]['id']}" for i in e["headline"]})})
    out.sort(key=lambda d: (-d["headline_rows"], -d["rows"], d["root"]))
    return out
