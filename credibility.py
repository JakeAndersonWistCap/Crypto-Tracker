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
    iss = brn = 0.0
    n_llama = n_counter = 0
    missing = []
    for d in days:
        before = st[st.index <= d]
        s0 = float(before.iloc[-1]) if len(before) else float(st.iloc[0])
        iss += 166.32 * math.sqrt(s0) / 365.0
        if d in px.index and px.loc[d] and d in rev.index:
            brn += float(rev.loc[d] / px.loc[d])
            n_llama += 1
        elif d in cum.index and (d - pd.Timedelta(days=1)) in cum.index:
            brn += float(cum.loc[d] - cum.loc[d - pd.Timedelta(days=1)])
            n_counter += 1
        else:
            missing.append(d)
    if missing:
        return None, None, (f"no burn on {len(missing)} of our {len(days)} common day(s) (neither DefiLlama x price "
                            f"nor the BurntFees counter on the day and the day before), e.g. {missing[0].date()}")
    # BOTH SIDES, LEG BY LEG (Jake's sign-off round, 2026-10-07): ours on the same days, so a miss names its leg —
    # issuance (Etherscan d(EthSupply + Eth2Staking) + d(BurntFees) vs the curve) or burn (ours vs DefiLlama / price)
    o_iss = float(si.reindex(days).sum())
    o_brn = float(sb.reindex(days).sum())
    gap = lambda a, b: f"{(a / b - 1):+.1%}" if b else "n/a"             # noqa: E731
    if side == "ours":                    # the judged OURS: issuance − burn summed over exactly these days
        return o_iss - o_brn, str(days[-1].date()), (f"ours on the {len(days)} common day(s) {days[0].date()}.."
                                                    f"{days[-1].date()}: issuance {o_iss:,.0f} − burn {o_brn:,.0f}")
    return iss - brn, str(days[-1].date()), (
        f"issuance curve {iss:,.0f} ETH (166.32 x sqrt(that day's staked ETH) over {len(days)} day(s)) − burn "
        f"{brn:,.0f} ETH (DefiLlama / price on {n_llama} day(s)"
        + (f", BurntFees counter on {n_counter}" if n_counter else "") + f"), on our {len(days)} common day(s) "
        f"{days[0].date()}..{days[-1].date()}. OURS, same {len(days)} day(s): issuance {o_iss:,.0f} "
        f"({o_iss / len(days):,.0f}/day, {gap(o_iss, iss)} vs the curve's {iss / len(days):,.0f}/day) − burn "
        f"{o_brn:,.0f} ({gap(o_brn, brn)} vs the reference burn) = {o_iss - o_brn:,.0f}")


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


def _rate_on_stake(p, rows, long, asof, flow="", stock="", days=28, price=None, **_):
    """A farm's reward rate (Jake, 2026-10-07 — Sky's two lsSKY farms): the rewards it received over the last `days`
    days, annualised, over what is staked in it now (x the token's price when the rewards are in dollars). A simple
    rate: StakingRewards pays rewards out, it does not compound them."""
    lo = asof - pd.Timedelta(days=days)
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


def _epoch_mean_check(p, rows, long, asof, dist="pendle_distributed_tokens", stock="locked_tokens_shares",
                      plus=("locked_tokens_virtual",), epoch_days=14, after_days=7, side="ref", **_):
    """THE HEADLINE MUST EQUAL THE MEAN OF THE PER-EPOCH APRs (Jake's run 2026-10-08 on 3d5dbeb, Pendle 5c: the twin
    compares our 82,545 with Jake's 82,545, so it passes trivially and cannot catch a headline below every epoch's APR).
    Ours = the headline's own arithmetic: the mean Q0 epoch distribution x 365.25/epoch_days / (stake + plus) at the
    latest reading. The reference = the mean over the SAME Q0 epochs of each epoch's APR over ITS OWN stake (on or
    before the epoch's start, else within after_days after). A Q0 epoch with no stake means the mean cannot be formed:
    no reference, the epochs named — never a mean over a subset."""
    lo, hi = _q0(asof)
    d_ = _series(long, p, dist)
    d_ = d_[(d_.index > lo) & (d_.index <= hi)]
    if d_.empty:
        return None, None, f"no {dist} epoch in Q0"
    f = 365.25 / float(epoch_days)
    now = [_stake_near(long, p, m, hi.normalize(), 0) for m in (stock, *plus)]
    if any(x is None for x in now):
        return None, None, f"no {'/'.join(m for m, x in zip((stock, *plus), now) if x is None)} at {hi.date()}"
    stake_now = sum(x[0] for x in now)
    head = float(d_.mean()) * f / stake_now
    how_head = (f"HEADLINE: mean of {len(d_)} Q0 epoch(s) {float(d_.mean()):,.0f} PENDLE x 365.25/{epoch_days} / "
                f"({' + '.join((stock, *plus))} {stake_now:,.0f} at {now[0][1].date()}) = {head:.3%}")
    if side == "ours":
        return head, str(d_.index[-1].date()), how_head
    table, missing, aprs = [], [], []
    for day, tokens in d_.items():
        parts = [_stake_near(long, p, m, day, after_days) for m in (stock, *plus)]
        if any(x is None for x in parts):
            missing.append(f"{day.date()} (no {'/'.join(m for m, x in zip((stock, *plus), parts) if x is None)})")
            continue
        st = sum(x[0] for x in parts)
        aprs.append(float(tokens) * f / st)
        table.append(f"{day.date()}: {float(tokens):,.0f} / {st:,.0f} = {aprs[-1]:.3%}")
    if missing:
        return None, None, (f"{how_head}. The MEAN OF PER-EPOCH APRs needs a stake at every Q0 epoch; missing: "
                            f"{'; '.join(missing)}. Per epoch so far: {'; '.join(table) or 'none'}")
    mean = sum(aprs) / len(aprs)
    return mean, str(d_.index[-1].date()), (f"{how_head}; MEAN OF PER-EPOCH APRs (each over its own stake) = "
                                            f"{mean:.3%}. Per epoch: {'; '.join(table)}")


def _epoch_apr(p, rows, long, asof, tokens=0.0, date="", stock="", plus=(), mult=26.0, read_by="", source="",
               ours_metric="", after_days=0, **_):
    """ONE EPOCH'S APR FROM A READING (Jake's run 2026-10-08, Pendle): `tokens` distributed in the epoch read on
    `date` x `mult` epochs a year / the reward-bearing stake stored on that date (stock + plus). With `ours_metric`
    the epoch's tokens are OUR stored figure on that date instead — the like-for-like twin of the same arithmetic."""
    day = pd.Timestamp(date)
    staked, late = 0.0, []
    for m in (stock, *plus):
        got = _stake_near(long, p, m, day, after_days)
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
         + (f"; {', '.join(late)} — the first reading after the epoch" if late else "") + ")")


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


def _aero_epochs(long, p, asof, onchain, flow, epoch_days=7):
    """The stored on-chain epochs inside Q0 that are COMPLETE and whose every day of `flow` is stored:
    [(epoch start, on-chain value, our flow summed over the same days)], and the epochs left out with why."""
    lo, hi = _q0(asof)
    oc, fl = _series(long, p, onchain), _series(long, p, flow)
    got, skipped = [], []
    for e, v in oc[(oc.index > lo) & (oc.index <= hi)].items():
        days = pd.date_range(e, periods=int(epoch_days), freq="D")
        if days[-1] >= asof.normalize():
            skipped.append(f"{e.date()} (epoch not complete)")
            continue
        miss = [d for d in days if d not in fl.index]
        if miss:
            skipped.append(f"{e.date()} ({len(miss)} day(s) of {flow} missing)")
            continue
        got.append((e, float(v), float(fl.loc[days].sum())))
    return got, skipped


def _aero_epoch_revenue(p, rows, long, asof, flow="revenue_usd", onchain="voter_rewards_onchain_usd", side="ref", **_):
    """THE SAME EPOCH ON BOTH SIDES (Jake's run 2026-10-08 11:27, Aerodrome in_revenue): DefiLlama's fees + bribes summed
    over the epoch's seven UTC days (Thursday 00:00 start) against the fees + bribes notified to the voting-reward
    contracts for that epoch (aerodrome_voter_epoch, stored weekly by fetch/aero_voter.py). Judged on the epoch that
    differs most; every epoch is in the working."""
    got, skipped = _aero_epochs(long, p, asof, onchain, flow)
    if not got:
        return None, None, ("no complete Q0 epoch with both the on-chain read and every DefiLlama day stored"
                            + (": " + "; ".join(skipped) if skipped else " — fetch/aero_voter.py stores one a week"))
    unp = _series(long, p, "voter_rewards_unpriced_count")
    table, worst = [], None
    for e, oc, ours in got:
        gap = ours / oc - 1 if oc else float("inf")
        n = f", {int(unp.loc[e])} token(s) unpriced on-chain" if e in unp.index else ""
        table.append(f"epoch {e.date()}: DefiLlama ${ours:,.0f} vs on-chain ${oc:,.0f} ({gap:+.1%}{n})")
        if worst is None or abs(gap) > abs(worst[3]):
            worst = (e, ours, oc, gap)
    how = ("; ".join(table) + (f". Not judged: {'; '.join(skipped)}" if skipped else "")
           + ". Unpriced reward tokens are left out of the on-chain figure, so it can read LOW by their value.")
    return (worst[1] if side == "ours" else worst[2]), str(worst[0].date()), how


def _aero_epoch_apr(p, rows, long, asof, flow="holders_revenue_usd", onchain_apr="voter_rewards_onchain_apr",
                    onchain_usd="voter_rewards_onchain_usd", stake="voter_total_weight_tokens", rebase="emissions_tokens",
                    side="ref", **_):
    """DOES THE HEADLINE'S ARITHMETIC REPRODUCE THE EPOCH? (Jake's run 2026-10-08 11:27, Aerodrome a3: ours 17.31% vs
    on-chain 12.56%.) Ours = the headline's own arithmetic on one epoch — DefiLlama's dollars over the epoch's days, put
    in AERO at that week's mean price, x 365.25/7, over the reward-bearing stake (Voter.totalWeight, the headline's
    denominator: on or before the epoch's start, else the first daily read within 14 days — it is read forward from
    2026-10-08 and moves well under 1% a week); the reference = the on-chain epoch APR. The working prints the HEADLINE's own numerator and
    denominator, how far each factor sits from the epoch's, and the veAERO rebase as its own labelled line."""
    got, skipped = _aero_epochs(long, p, asof, onchain_usd, flow)
    apr = _series(long, p, onchain_apr)
    px = _series(long, p, "price_usd")
    table, worst = [], None
    for e, _oc, usd in got:
        days = pd.date_range(e, periods=7, freq="D")
        pw = px[px.index.isin(days)]
        st = _stake_near(long, p, stake, e, 14)
        if e not in apr.index or pw.empty or st is None:
            skipped.append(f"{e.date()} (no " + ("on-chain APR" if e not in apr.index else "price" if pw.empty
                                                 else stake) + ")")
            continue
        ours = usd / float(pw.mean()) * 365.25 / 7 / st[0]
        theirs = float(apr.loc[e])
        gap = ours / theirs - 1 if theirs else float("inf")
        table.append(f"epoch {e.date()}: ${usd:,.0f} / ${float(pw.mean()):,.4f} x 365.25/7 / {st[0]:,.0f} = {ours:.2%} vs "
                     f"on-chain {theirs:.2%} ({gap:+.1%})")
        if worst is None or abs(gap) > abs(worst[3]):
            worst = (e, ours, theirs, gap)
    # THE HEADLINE'S OWN NUMERATOR AND DENOMINATOR, beside the epoch's
    lo, hi = _q0(asof)
    rq = _series(long, p, flow)
    rq = rq[(rq.index > lo) & (rq.index <= hi)]
    pq = px[(px.index > lo) & (px.index <= hi)]
    sn = _stake_near(long, p, stake, hi, 0)
    head = ""
    if len(rq) and len(pq) and sn:
        tok_yr = float(rq.sum()) / float(pq.mean()) * 365.25 / len(rq)
        head = (f"HEADLINE: numerator ${float(rq.sum()):,.0f} over {len(rq)} Q0 day(s) (${float(rq.sum()) / len(rq) * 7:,.0f}"
                f"/week) / Q0 mean price ${float(pq.mean()):,.4f} x 365.25/{len(rq)} = {tok_yr:,.0f} AERO/yr; denominator "
                f"{stake} {sn[0]:,.0f} -> {tok_yr / sn[0]:.2%}.")
        if worst is not None:
            e = worst[0]
            wk = [u for d, _o, u in got if d == e][0]
            pe = float(px[px.index.isin(pd.date_range(e, periods=7, freq="D"))].mean())
            se = _stake_near(long, p, stake, e, 14)[0]
            head += (f" Against epoch {e.date()}: revenue/week x{float(rq.sum()) / len(rq) * 7 / wk:.2f}, price "
                     f"x{pe / float(pq.mean()):.2f} (epoch week mean / Q0 mean), stake x{se / sn[0]:.2f}.")
    rb = _series(long, p, rebase)
    if not rb.empty and sn:
        head += (f" REBASE (a separate stream, in neither figure): {float(rb.iloc[-1]):,.0f} AERO in the week of "
                 f"{rb.index[-1].date()} x 52 / {sn[0]:,.0f} = {float(rb.iloc[-1]) * 52 / sn[0]:.2%} a year to lockers.")
    if worst is None:
        return None, None, (head + " No epoch to judge" + (": " + "; ".join(skipped) if skipped else
                                                           " — fetch/aero_voter.py stores one a week")).strip()
    how = (head + " Per epoch: " + "; ".join(table) + (f". Not judged: {'; '.join(skipped)}" if skipped else ""))
    return (worst[1] if side == "ours" else worst[2]), str(worst[0].date()), how


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
            "value_on": _value_on, "epoch_apr": _epoch_apr, "epoch_reproduction": _epoch_reproduction, "epoch_mean_check": _epoch_mean_check, "aero_epoch_revenue": _aero_epoch_revenue, "aero_rebase_apr": _aero_rebase_apr,
            "aero_epoch_apr": _aero_epoch_apr, "q0_net_flow": _q0_net_flow,
            "negative_release": _negative_release, "sum_month": _sum_month, "last30_annualised": _last30_annualised,
            "common_day_value": _common_day_value, "months_match": _months_match,
            "hl_reward_active": _hl_reward_active, "base_reward_ceiling": _base_reward_ceiling,
            "rate_on_stake": _rate_on_stake, "reward_rate_apr": _reward_rate_apr, "trailing_token_yield": _trailing_token_yield,
            "sum_since": _sum_since, "schedule_month": _schedule_month, "rise_vs_flow": _rise_vs_flow,
            "product_on_common_day": _product_on_common_day, "now_sum": _now_sum,
            "log_price_growth": _log_price_growth, "bridge_reconciled": _bridge_reconciled,
            "daily_delta_plus_flow": _daily_delta_plus_flow, "window_sum": _window_sum, "now_combo": _now_combo}


def reference(project: str, spec: dict, rows: dict, long, asof) -> dict:
    """{value, date, source, mode, verdict, note} for one row's reference spec."""
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
                "verdict": spec["force_verdict"],
                "note": (note + " " if note else "") + spec.get("why", "") + (f" RESOLVE: {res}" if res else "")}
    return {"value": val, "date": None if date is None else str(date)[:10], "source": src, "mode": mode,
            "verdict": None, "note": note,
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
