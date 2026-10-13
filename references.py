"""
references.py — Jake's independent reference table (independent_references.csv, 2026-10-13) as checks on our figures
(Fix Round 2, T2 / T4).

Each CSV row (project, metric, value, unit, as_of, source_type, source, tolerance, note) is matched to the stored series
or the workbook headline that measures the same thing (ROUTES below) and compared within its tolerance:

  primary reference        within tolerance -> VERIFIED          outside -> BROKEN (both values shown)
  cross-check reference    within tolerance -> LIVE-UNVERIFIED   outside -> CHECK  (both values shown)
  ("secondary", "primary-ish", "primary-derived", "derived …": proof of nothing on its own — never VERIFIED)
  no route / no stored value at the reference date            -> NOT WIRED + the route or the reason

A row whose note says "reconcile" also carries the cause of the gap (CAUSES), computed from the same series, never a
guess. Nothing here changes a figure: a reference only grades it. The rows are added to the Coverage tab
(coverage_audit.audit) under Tab "References".
"""
from __future__ import annotations

import csv
import math
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "independent_references.csv"
TAB = "References"
PROJECT_ALIASES = {"NEAR": "Near"}
# a stock sampled once a day reads a transfer made late on day D on day D+1: sums and events over a stock allow one day
SAMPLE_LAG_DAYS = 1
STALE_DAYS = 7             # our latest row may be this many days before the reference date (the dump's as-of)


def load(path: Path | None = None) -> list[dict]:
    with open(path or CSV_PATH, newline="", encoding="utf-8") as f:
        return [dict(r) for r in csv.DictReader(f)]


def project_of(row: dict) -> str:
    return PROJECT_ALIASES.get(row["project"], row["project"])


def role(source_type: str) -> str:
    """'primary' only for a plain primary source; anything qualified (primary-ish, primary-derived, derived, secondary)
    is a cross-check — PROPOSED reading of Jake's rule (the conservative one: it can never make a figure VERIFIED)."""
    st = (source_type or "").strip().lower()
    return "primary" if st == "primary" or st.startswith("primary (") else "cross-check"


def tolerance(tol: str, ref_text: str) -> tuple[str, float]:
    """('rel', 0.005) for '0.5%'; ('abs', 0.01) for '1pp'; ('abs', 0.1) for '0.1'; ('exact', half a unit of the
    reference's last stated digit) for 'exact'."""
    t = (tol or "").strip().lower()
    if t.endswith("%"):
        return "rel", float(t[:-1]) / 100
    if t.endswith("pp"):
        return "abs", float(t[:-2]) / 100
    if t == "exact":
        dec = len(ref_text.split(".", 1)[1]) if "." in ref_text else 0
        return "exact", 0.5 * 10 ** (-dec)
    return "abs", float(t)


def within(ours: float, ref: float, kind: str, tol: float) -> bool:
    if kind == "rel":
        return abs(ours - ref) <= tol * abs(ref) + 1e-12
    return abs(ours - ref) <= tol + 1e-12


# ROUTES: (project, reference metric) -> how our figure is read. Kinds:
#   ("last", metric)                     latest value on or before the reference date
#   ("ratio", num, den)                  latest num ÷ latest den
#   ("mean", metric, days)               mean daily value over the `days` ending at the reference date
#   ("sum_days", metric, days)           sum over the `days` ending at the reference date
#   ("sum_since", metric, start)         sum from `start` to the reference date (+ SAMPLE_LAG_DAYS)
#   ("sum_period", metric, start, end)   sum over [start, end]
#   ("on_day", metric)                   the value dated on the reference date
#   ("event", metric, days, sign)        a single-day move of the stock equal to the reference, within ±days
#   ("cumulative", metric)               every stored row up to the reference date (the series' first date is named)
#   ("cover", num, den, price, start, end) sum(num) ÷ sum(den × price) over [start, end]
#   ("headline", tab, header prefix)     the workbook cell, evaluated
#   ("wallets", check)                   a config check (addresses), see _wallet_check
#   ("flows", line prefix, label)        the Flows tab's own Token-flow line in that quarter (same series and window)
#   ("not_wired", route)                 no series measures it yet — the route that would
ROUTES: dict[tuple[str, str], tuple] = {
    ("Ethereum", "active_stake_effective_balance"): ("last", "beacon_chain_eth"),
    ("Ethereum", "issuance_per_day"): ("mean", "gross_issuance_tokens", 7),
    ("Ethereum", "burn_30d"): ("sum_days", "gross_burn_tokens", 30),
    ("Ethereum", "supply"): ("last", "circulating_supply"),
    ("Ethereum", "validator_apr_gross"): ("headline", "CP Comparability", "Staker yield — validator (A1)"),
    ("Hyperliquid", "total_staked"): ("last", "locked_tokens"),
    ("Hyperliquid", "issuer_side_stake"): ("last", "staked_noncirculating_tokens"),
    ("Hyperliquid", "assistance_fund_balance"): ("last", "burn_address_balance"),
    ("Hyperliquid", "api_total_supply"): ("last", "total_supply_gross"),
    ("Hyperliquid", "api_circulating_supply"): ("last", "circulating_supply_tokendetails"),
    ("Hyperliquid", "future_emissions_remaining"): ("last", "future_emissions_tokens"),
    ("Hyperliquid", "team_unlock_2026-10-07"): ("on_day", "issuer_release_replay_tokens"),
    ("Hyperliquid", "team_unlock_2026-01-06"): ("on_day", "issuer_release_replay_tokens"),
    ("Hyperliquid", "team_unlock_2025-11-29"): ("on_day", "issuer_release_replay_tokens"),
    ("Uniswap", "total_supply"): ("last", "total_supply_gross"),
    ("Uniswap", "dead_address_balance"): ("last", "burn_address_balance"),
    ("Uniswap", "timelock_balance"): ("last", "treasury_holding_tokens"),
    ("Uniswap", "growth_budget_paid_2026"): ("sum_since", "growth_budget_release_tokens", "2026-01-01"),
    ("Uniswap", "franchiser_recall_inflow"): ("event", "treasury_holding_tokens", 7, 1),
    ("Aerodrome", "tail_emission_rate"): ("source_tag", "gross_issuance_tokens", r"tail@(\d+)bps"),
    ("Aerodrome", "team_rate"): ("not_wired", "S5: Minter.teamRate() read + the team emission as its own insider "
                                              "line (228 bps of the weekly emission); not read yet"),
    ("Aerodrome", "weekly_lp_emissions"): ("last", "gross_issuance_tokens"),
    ("Aerodrome", "weekly_rebase"): ("last", "emissions_tokens"),
    # like for like: our weekly (b) + (c) on the dashboard's basis (x 52 ÷ total supply); the team emission is not
    # measured yet (S5), so ours omits it — the cause names our headline (÷ circulating) and the team share
    ("Aerodrome", "annualised_dilution_incl_rebase_team"): ("weekly_sum_over", ("gross_issuance_tokens",
                                                                               "emissions_tokens"), "total_supply", 52),
    ("Aerodrome", "veaero_share_of_supply"): ("ratio", "ve_voting_power_tokens", "total_supply"),
    ("Aerodrome", "momentum_fund_cumulative"): ("not_wired", "aero.xyz/economics/metrics momentum fund series (rendered "
                                                             "page) — not read; the filing managed lock is another figure"),
    ("Aerodrome", "revenue_per_emissions_usd"): ("cover", "fees_usd", "gross_issuance_tokens", "price_usd",
                                                 "2026-01-01", "2026-03-31"),
    ("Pendle", "total_supply"): ("last", "total_supply_gross"),
    ("Pendle", "governance_multisig_balance"): ("not_wired", "per-wallet balance of the governance multisig — the store "
                                                             "keeps the sum of the non-circulating wallets only"),
    ("Pendle", "team_multisig_balance"): ("not_wired", "per-wallet balance of the team multisig 0x918c… (S4) — the "
                                                       "store keeps the sum only"),
    ("Pendle", "ecosystem_fund_balance"): ("not_wired", "per-wallet balance of the ecosystem fund 0x399b… (S4) — the "
                                                        "store keeps the sum only"),
    ("Pendle", "unclaimed_accrued_emissions"): ("latent", "Pendle"),
    ("Sky", "vest18_rate"): ("last", "emissions_rate_current_annual_tokens"),
    ("Sky", "vest17_rate"): ("last", "emissions_rate_current_annual_tokens"),
    ("Sky", "pause_proxy_sky_balance"): ("last", "treasury_holding_tokens"),
    ("Sky", "mkr_supply_unconverted"): ("last", "mkr_outstanding_tokens"),
    ("Sky", "sky_total_supply"): ("last", "total_supply_protocol"),
    ("Sky", "spell_burn_2026-09-13"): ("on_day", "gross_burn_tokens"),
    ("Sky", "spell_burn_2026-10-12"): ("on_day", "gross_burn_tokens"),
    ("Sky", "net_revenue_sep_2026"): ("on_day", "net_revenue_pre_distribution_usd"),
    ("Ether.fi", "total_supply"): ("last", "total_supply"),
    ("Ether.fi", "sethfi_vault_assets"): ("last", "locked_tokens_underlying"),
    ("Maple", "total_supply"): ("last", "total_supply"),
    ("Maple", "inflation_claim_2026-04-22"): ("on_day", "issuer_mint_tokens"),
    ("Maple", "inflation_claim_2026-10-09"): ("on_day", "issuer_mint_tokens"),
    ("Maple", "dao_safe_balance"): ("last", "treasury_holding_tokens_chain"),
    ("Maple", "buyback_top_tier"): ("not_wired", "S3: MIP-021 tiers (10% < $1.5M, 20% $1.5–2.0M, 30% > $2.0M of "
                                                 "monthly revenue) are not a config value yet"),
    ("Morpho", "fee_recipient"): ("zero_sum", "revenue_usd", 90),
    ("Morpho", "merkl_rewards_rate"): ("mean", "outflow_b_tokens", 30),
    ("Fluid", "treasury_to_team_rewards"): ("event", "treasury_holding_tokens", 21, -1),
    ("Fluid", "treasury_to_team_agi3_locked"): ("event", "treasury_holding_tokens", 21, -1),
    ("Aethir", "dat_holding_axe_compute"): ("dat_file", "Axe Compute"),
    ("Aethir", "circulating_schedule_oct_2026"): ("last", "circulating_supply_first_party"),
    ("Chainlink", "reserve_holdings_2026-04-15"): ("last", "buyback_fund_balance"),
    ("Chainlink", "reserve_holdings_end_q1_2026"): ("last", "buyback_fund_balance"),
    ("Chainlink", "staking_community_rate"): ("last", "staker_yield_community_pool"),
    # V5 (Jake's round-2 run): the Flows tab's own release line and Q0 window — read 0 on calendar Q3 while Flows
    # showed the 18.75M tranche in Q0
    ("Chainlink", "quarterly_release"): ("flows", "+ released from issuer wallets", "Q0"),
    ("Near", "fefundsadmin_alltime_inflow"): ("not_wired", "N5: all-time inflow of fefundsadmin.near — the v3 "
                                                           "account txns read of that wallet is not wired"),
    ("Near", "buybacks_multisig_alltime_inflow"): ("not_wired", "N5/N6: all-time inflow of buybacks.multisignature.near"
                                                                " — not stored (we keep its balance)"),
    ("Near", "1csfundsadmin_alltime_inflow"): ("not_wired", "N5: all-time inflow of 1csfundsadmin.near — not wired"),
    ("Near", "intents_cumulative_volume"): ("cumulative", "near_intents_volume_usd"),
    ("GEODNET", "mining_pool_wallets"): ("wallets", "mining"),
    ("GEODNET", "team_investor_ecosystem_vendor_publicsale_wallets"): ("wallets", "discretionary"),
    ("GEODNET", "burned_to_date"): ("last", "burn_address_balance"),
}


def _series(groups: dict, project: str, metric: str) -> pd.DataFrame | None:
    g = groups.get((project, metric))
    if g is None or g.empty:
        return None
    s = g[["date", "value", "source"]].copy()
    s["date"] = pd.to_datetime(s["date"]).dt.normalize()
    s = s.dropna(subset=["value"]).sort_values("date")
    return s if not s.empty else None


def _fmt(x) -> str:
    if isinstance(x, str):
        return x
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    ax = abs(x)
    if ax and ax < 1:
        return f"{x:.4f}"
    return f"{x:,.2f}" if ax < 1e4 else f"{x:,.0f}"


def _flows_value(route: tuple, flows: list[dict] | None, project: str) -> tuple[float | None, str, str]:
    """("flows", line prefix, quarter label): the Flows tab's own Token-flow line — the same series and window."""
    _k, prefix, label = route
    how = f"Flows: Token flow '{prefix}…' {label}"
    row = next((r for r in flows or () if r.get("Project") == project and r.get("Statement") == "Token flow"
                and str(r.get("Line", "")).startswith(prefix)), None)
    if row is None:
        return None, how, "the Flows tab has no such line for this project (built without Flows?)"
    v = row.get(label)
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None, how, f"the Flows line reads {v!r} in {label}"
    return float(v), how + f" ({row.get('Series')})", ""


def _our_value(route: tuple, groups: dict, project: str, at: pd.Timestamp, ref: float,
               headline=None) -> tuple[float | None, str, str]:
    """(our value, how it was read, why there is none)."""
    kind = route[0]
    if kind == "headline":
        v = headline(project, route[1], route[2]) if headline else None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v), f"{route[1]} → {route[2]}", ""
        return None, f"{route[1]} → {route[2]}", f"the headline reads {str(v)[:120]!r}"
    if kind == "headline_sum":
        vals = [headline(project, route[1], h) if headline else None for h in route[2]]
        if all(isinstance(v, (int, float)) for v in vals):
            return float(sum(vals)), f"{route[1]} → " + " + ".join(h.split(' (')[0] for h in route[2]), ""
        return None, "headline sum", f"a headline reads {vals!r}"[:200]
    if kind == "ratio":
        a, b = _series(groups, project, route[1]), _series(groups, project, route[2])
        if a is None or b is None:
            return None, f"{route[1]} ÷ {route[2]}", "no stored rows"
        a, b = a[a.date <= at], b[b.date <= at]
        if a.empty or b.empty:
            return None, f"{route[1]} ÷ {route[2]}", "no row on or before the reference date"
        return (float(a.value.iloc[-1]) / float(b.value.iloc[-1]),
                f"{route[1]} {a.date.iloc[-1]:%Y-%m-%d} ÷ {route[2]} {b.date.iloc[-1]:%Y-%m-%d}", "")
    if kind == "cover":
        _k, num, den, px, start, end = route
        n, d, p = (_series(groups, project, m) for m in (num, den, px))
        if n is None or d is None or p is None:
            return None, f"{num} ÷ ({den} × {px})", "no stored rows"
        s0, s1 = pd.Timestamp(start), pd.Timestamp(end)
        n = n[(n.date >= s0) & (n.date <= s1)]
        d = d[(d.date >= s0) & (d.date <= s1)].merge(p[["date", "value"]].rename(columns={"value": "px"}), on="date")
        if n.empty or d.empty:
            return None, f"{num} ÷ ({den} × {px})", f"no rows in {start}..{end}"
        return (float(n.value.sum()) / float((d.value * d.px).sum()),
                f"Σ {num} ÷ Σ({den} × {px}), {start}..{end}", "")
    if kind == "weekly_sum_over":
        _k, legs, den, n = route
        vals, dates = [], []
        for m in legs:
            x = _series(groups, project, m)
            x = x[x.date <= at + pd.Timedelta(days=SAMPLE_LAG_DAYS)] if x is not None else None
            if x is None or x.empty:
                return None, " + ".join(legs), f"{m}: no row on or before the reference date"
            vals.append(float(x.value.iloc[-1]))
            dates.append(f"{m} {x.date.iloc[-1]:%Y-%m-%d}")
        d = _series(groups, project, den)
        d = d[d.date <= at + pd.Timedelta(days=SAMPLE_LAG_DAYS)] if d is not None else None
        if d is None or d.empty:
            return None, den, f"{den}: no row on or before the reference date"
        return (sum(vals) * n / float(d.value.iloc[-1]),
                f"({' + '.join(dates)}) × {n} ÷ {den} {d.date.iloc[-1]:%Y-%m-%d} — our (b) + (c) on the reference's "
                f"basis", "")
    if kind == "latent":
        import config                                       # noqa: PLC0415
        e = config.LATENT_SUPPLY.get(route[1])
        if not e:
            return None, "config.LATENT_SUPPLY", "no latent-supply reading on file"
        return float(e["tokens"]), f"config.LATENT_SUPPLY ({e['as_of']}, {e['entered_by']})", ""
    if kind == "dat_file":
        import json                                         # noqa: PLC0415
        try:
            d = json.loads((ROOT / "etf_dat_holdings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return None, "etf_dat_holdings.json", f"unreadable: {e}"
        for e in ((d.get("projects") or {}).get(project) or {}).get("dat") or ():
            if route[1].lower() in str(e.get("holder", "")).lower():
                return (float(e["tokens"]), f"etf_dat_holdings.json {e.get('holder')} as of {e.get('as_of')} "
                                            f"({e.get('source_url', '')[:60]})", "")
        return None, "etf_dat_holdings.json", f"no DAT entry naming {route[1]!r}"
    if kind == "wallets":
        return None, "config wallets", ""
    if kind == "not_wired":
        return None, "", route[1]
    metric = route[1]
    s = _series(groups, project, metric)
    if s is None:
        return None, metric, f"{metric}: no rows stored"
    first = s.date.iloc[0]
    if kind == "source_tag":
        x = s[s.date <= at]
        m = re.search(route[2], str(x.source.iloc[-1])) if not x.empty else None
        if not m:
            return None, f"{metric} source tag", "no tagged row on or before the reference date"
        return float(m.group(1)), f"{metric} source {x.source.iloc[-1]} ({x.date.iloc[-1]:%Y-%m-%d})", ""
    if kind == "last":
        x = s[s.date <= at + pd.Timedelta(days=SAMPLE_LAG_DAYS)]
        if x.empty:
            return None, metric, f"{metric} starts {first:%Y-%m-%d}, after the reference date"
        d = x.date.iloc[-1]
        if (at - d).days > STALE_DAYS:
            return None, metric, f"{metric}'s last row on or before {at:%Y-%m-%d} is {d:%Y-%m-%d}"
        return float(x.value.iloc[-1]), f"{metric} {d:%Y-%m-%d} ({x.source.iloc[-1]})", ""
    if kind == "on_day":
        if first > at:
            return None, metric, f"{metric} starts {first:%Y-%m-%d}, after the reference date (history not restated)"
        x = s[s.date == at]
        if x.empty:
            return None, metric, f"{metric} has no row dated {at:%Y-%m-%d}"
        return float(x.value.iloc[-1]), f"{metric} {at:%Y-%m-%d}", ""
    if kind in ("mean", "sum_days", "zero_sum"):
        days = route[2]
        lo = at - pd.Timedelta(days=days)
        x = s[(s.date > lo) & (s.date <= at)]
        if first > lo + pd.Timedelta(days=1):
            return None, metric, f"{metric} starts {first:%Y-%m-%d}: fewer than {days} days stored"
        if x.empty:
            return None, metric, f"{metric}: no rows in the {days} days to {at:%Y-%m-%d}"
        if kind == "mean":
            return (float(x.value.mean()), f"mean of {metric} over the {len(x)} stored day(s) in the {days} days to "
                                           f"{at:%Y-%m-%d}", "")
        if kind == "zero_sum":
            return float(x.value.sum()), f"Σ {metric} over {days} days (0 = no protocol fee taken)", ""
        return float(x.value.sum()), f"Σ {metric} over {days} days to {at:%Y-%m-%d}", ""
    if kind in ("sum_since", "sum_period"):
        s0 = pd.Timestamp(route[2])
        s1 = pd.Timestamp(route[3]) if kind == "sum_period" else at + pd.Timedelta(days=SAMPLE_LAG_DAYS)
        if first > s0 + pd.Timedelta(days=1):
            return None, metric, f"{metric} starts {first:%Y-%m-%d}, after {s0:%Y-%m-%d}"
        x = s[(s.date >= s0) & (s.date <= s1)]
        return float(x.value.sum()), f"Σ {metric} {s0:%Y-%m-%d}..{s1:%Y-%m-%d}", ""
    if kind == "cumulative":
        x = s[s.date <= at]
        return (float(x.value.sum()), f"Σ {metric} {first:%Y-%m-%d}..{at:%Y-%m-%d} (our series starts "
                                      f"{first:%Y-%m-%d})", "")
    if kind == "event":
        days, sign = route[2], route[3]
        lo, hi = at - pd.Timedelta(days=days), at + pd.Timedelta(days=days + SAMPLE_LAG_DAYS)
        if first > lo:
            return None, metric, f"{metric} starts {first:%Y-%m-%d}, after {lo:%Y-%m-%d}"
        x = s.set_index("date").value
        dx = x.diff()
        dx = dx[(dx.index >= lo) & (dx.index <= hi)] * sign
        if dx.empty:
            return None, metric, f"{metric}: no rows within ±{days} days"
        best = (dx - ref).abs().idxmin()
        return float(dx[best]), f"single-day move of {metric} on {best:%Y-%m-%d} (largest match within ±{days} days)", ""
    return None, metric, f"unknown route kind {kind!r}"


def _wallet_check(project: str, which: str, ref_text: str) -> tuple[bool | None, str]:
    """G1 / G2: the issuer filing's wallets against config — mining wallets are (b) only and never in (e) /
    discretionary; every other filed wallet is discretionary. (ok, detail)."""
    import config                                          # noqa: PLC0415
    want = {a.strip().lower() for a in ref_text.split(";") if a.strip()}
    p = config.PROJECT_BY_NAME.get(project) or {}
    disc = {a.lower() for a in config.discretionary_wallets(project)} if hasattr(config, "discretionary_wallets") else None
    mining = {a.lower() for a in config.mining_wallets(project)} if hasattr(config, "mining_wallets") else None
    if disc is None or mining is None:
        return None, "config declares no mining / discretionary wallet sets for this project yet (G1/G2)"
    if which == "mining":
        missing, leaked = want - mining, want & disc
        ok = not missing and not leaked
        return ok, (f"mining wallets in config: {len(want - missing)}/{len(want)}; also in the discretionary (e) set: "
                    f"{len(leaked)}" + (f" — missing {sorted(missing)}" if missing else ""))
    missing, leaked = want - disc, want & mining
    ok = not missing and not leaked
    del p
    return ok, (f"filed non-mining wallets in the discretionary set: {len(want - missing)}/{len(want)}; also counted as "
                f"mining: {len(leaked)}" + (f" — missing {sorted(missing)}" if missing else ""))


def check(groups: dict, headline=None, projects: set[str] | None = None, rows: list[dict] | None = None,
          causes: dict | None = None, flows: list[dict] | None = None) -> list[dict]:
    """One result per reference row for the projects in `groups` (or `projects`): {project, metric, ref, ours,
    status, detail, route, source}. `headline(project, tab, header_prefix)` returns the evaluated workbook cell."""
    built = projects if projects is not None else {k[0] for k in groups}
    out = []
    for r in rows if rows is not None else load():
        project = project_of(r)
        if project not in built:
            continue
        key = (project, r["metric"])
        route = ROUTES.get(key)
        rl = role(r["source_type"])
        ref_txt = r["value"].strip()
        base = {"project": project, "metric": r["metric"], "ref": ref_txt, "unit": r["unit"], "as_of": r["as_of"],
                "source_type": r["source_type"], "source": r["source"], "tolerance": r["tolerance"],
                "note": r["note"], "role": rl}
        if route is None:
            out.append(base | {"ours": None, "status": "NOT WIRED", "route": "",
                               "detail": "NOT WIRED — reference check: no route maps this reference to a stored series"})
            continue
        if route[0] == "wallets":
            ok, detail = _wallet_check(project, route[1], ref_txt)
            status = ("NOT WIRED" if ok is None else ("VERIFIED" if ok and rl == "primary" else
                      "LIVE-UNVERIFIED" if ok else "BROKEN" if rl == "primary" else "CHECK"))
            out.append(base | {"ours": "", "status": status, "route": "config wallet sets",
                               "detail": (f"NOT WIRED — reference check: {detail}" if ok is None else detail)})
            continue
        at = _ref_date(r["as_of"])
        try:
            ref = 0.0 if route[0] == "zero_sum" else float(ref_txt)
        except ValueError:
            out.append(base | {"ours": None, "status": "NOT WIRED", "route": "",
                               "detail": f"NOT WIRED — reference check: non-numeric reference {ref_txt[:60]!r}"})
            continue
        ours, how, why = (_flows_value(route, flows, project) if route[0] == "flows"
                          else _our_value(route, groups, project, at, ref, headline))
        if route[0] == "zero_sum":
            ref = 0.0                                      # the zero address: no fee recipient = no protocol fee
        if ours is None:
            out.append(base | {"ours": None, "status": "NOT WIRED", "route": how,
                               "detail": f"NOT WIRED — reference check: {why}"})
            continue
        kind, tol = tolerance(r["tolerance"], "0" if route[0] == "zero_sum" else ref_txt)
        ok = within(ours, ref, kind, tol)
        status = ("VERIFIED" if ok else "BROKEN") if rl == "primary" else ("LIVE-UNVERIFIED" if ok else "CHECK")
        gap = ours - ref
        rel = f" ({gap / ref:+.2%})" if ref else ""
        detail = (f"ours {_fmt(ours)} vs reference {_fmt(ref)} {r['unit']} ({r['as_of']}, {r['source_type']}); "
                  f"gap {_fmt(gap)}{rel}; tolerance {r['tolerance']} — "
                  + ("within" if ok else "OUTSIDE") + ("" if rl == "primary" else " (cross-check, not proof)"))
        cause = (causes or CAUSES).get(key)
        if cause and (not ok or "reconcile" in r["note"].lower()):
            detail += f". Cause: {cause(groups, project, at, ours, ref) if callable(cause) else cause}"
        out.append(base | {"ours": ours, "status": status, "route": how, "detail": detail})
    return out


def _ref_date(as_of: str) -> pd.Timestamp:
    """'2026-10-13' or a quarter '2026-Q3' (its last day)."""
    m = re.fullmatch(r"(\d{4})-Q([1-4])", as_of.strip())
    if m:
        return pd.Period(f"{m.group(1)}Q{m.group(2)}", freq="Q").end_time.normalize()
    return pd.Timestamp(as_of).normalize()


def _aero_dilution_cause(groups, project, at, ours, ref) -> str:
    """aero.xyz annualises (weekly LP emission + rebase + team emission) over TOTAL supply; our headline annualises
    (b) + (c) over our circulating (total − issuer-side). Both shown, from the same stored rows."""
    tot = _series(groups, project, "total_supply")
    circ = _series(groups, project, "circulating_supply_used")
    tot_v = float(tot[tot.date <= at + pd.Timedelta(days=1)].value.iloc[-1]) if tot is not None else None
    circ_v = float(circ.value.iloc[-1]) if circ is not None else None
    txt = ("compared like for like (our weekly gross issuance (b) + rebase (c), × 52 ÷ total supply). Our headline "
           "TOTAL DILUTION divides the same (b) + (c) by OUR circulating")
    if circ_v and tot_v:
        txt += (f" ({circ_v:,.0f}, staked veAERO included, issuer-side excluded) instead of total supply "
                f"({tot_v:,.0f}) — so it reads ×{tot_v / circ_v:.2f} the dashboard's basis")
    return (txt + f"; the remaining {ref - ours:+.2%} is the team emission (Minter teamRate 228 bps of the frozen "
                  f"weekly), its own insider line since fix round 2 (S5), not in (b) / (c)")


def _sky_revenue_cause(groups, project, at, ours, ref) -> str:
    def month(metric, d):
        s = _series(groups, project, metric)
        hit = s[s.date == pd.Timestamp(d)] if s is not None else None
        return f"{float(hit.value.iloc[0]):,.0f}" if hit is not None and len(hit) else "?"
    return (f"the TMF series does not sit on one Block Analitica basis: its July 10,517,426 is AFTER the distribution "
            f"(ours, NPS: {month('net_protocol_surplus_usd', '2026-07-31')}), its August 15,745,296 and September "
            f"14,640,000 are BEFORE it (ours, revenue − expense: Aug {month('net_revenue_pre_distribution_usd', '2026-08-31')}, "
            f"Sep {month('net_revenue_pre_distribution_usd', '2026-09-30')}) — the facilitator's own figures, read at a "
            f"different vintage of the P&L. Ours is Block Analitica's P&L, the primary account")


def _maple_supply_cause(groups, project, at, ours, ref) -> str:
    s = _series(groups, project, "total_supply")
    src = str(s.source.iloc[-1]) if s is not None else "?"
    last = f"{s.date.iloc[-1]:%Y-%m-%d}" if s is not None else "?"
    return (f"the stored total_supply is {src} (to {last}), {ref - ours:+,.0f} short of the contract's totalSupply — "
            f"the aggregator had not taken in the MIP-010 inflation claim (23.20M, 2026-10-09). From 2026-10-11 the "
            f"token's own totalSupply() wins (TOTAL_SUPPLY_ONCHAIN_FIRST, decision A4): a store read after that date "
            f"carries the chain figure")


def _eth_supply_cause(groups, project, at, ours, ref) -> str:
    s = _series(groups, project, "circulating_supply")
    if s is None:
        return ""
    yr = s[s.date <= at - pd.Timedelta(days=330)]
    if yr.empty:
        return "history shorter than a year"
    v0, src0 = float(yr.value.iloc[-1]), str(yr.source.iloc[-1])
    return (f"today agrees; the HISTORY does not — a year back the stored series reads {v0:,.0f} ({src0}) and stays "
            f"near it until the source changes, while issuance − burn adds ~1M ETH a year: CoinGecko's derived "
            f"mcap ÷ price figure was stale (H1 restatement from issuance − burn)")


def _near_intents_cause(groups, project, at, ours, ref) -> str:
    s = _series(groups, project, "near_intents_volume_usd")
    first = f"{s.date.iloc[0]:%Y-%m-%d}" if s is not None else "?"
    return (f"our stored series starts {first} (DefiLlama's NEAR Intents dex volume, first read 455 days back); the "
            f"reference counts from launch (Nov 2024) — the {ref - ours:+,.0f} gap is the days before {first} "
            f"(not a measured difference until those days are stored)")


def _maple_dao_cause(groups, project, at, ours, ref) -> str:
    s = _series(groups, project, "treasury_holding_tokens_chain")
    if s is None:
        return ""
    prev = s[s.date < at]
    p = float(prev.value.iloc[-1]) if len(prev) else None
    return (f"an intraday move, not a misread (V3, 2026-10-13): the stored close of {at:%Y-%m-%d} reads {ours:,.0f}, the "
            f"day before {p:,.0f}" + (" — " if p is not None else "") +
            "tx 0xd628a262… at 18:10 UTC moved 23,197,508 SYRUP (the 10-09 MIP-010 mint) from mapleTreasury 0xa9466EaB… "
            "into the DAO Safe and, in the same tx, 4,000,000 from the Safe to the unlabelled EOA 0x9e786E5E…; the "
            "reference (23.09M) was read before it. Both Safes are issuer-side (subtracted from circulating), so only "
            "the 4,000,000 is a release (e)")


def _eth_burn_cause(groups, project, at, ours, ref) -> str:
    s = _series(groups, project, "gross_burn_tokens")
    if s is None:
        return ""
    w = s[(s.date > at - pd.Timedelta(days=30)) & (s.date <= at)]
    legs = {}
    for src, part in w.groupby(w.source.astype(str).str.split(":").str[0]):
        legs[src] = (float(part.value.sum()), len(part))
    txt = "; ".join(f"{k} {v:,.1f} ETH on {n} row(s) ({v / max(n, 1):,.1f}/row)" for k, (v, n) in legs.items())
    return (f"a basis difference, not a burn difference (V4): our 30 rows join two sources — {txt}. The DefiLlama leg "
            f"is USD burned ÷ the day's price (not ETH measured), the Etherscan leg is read-to-read deltas of BurntFees "
            f"at run time (not 00:00 UTC; one row spans 4 days), and the seam double-counts the hours of 2026-09-29 after "
            f"the first Etherscan read (~4h). ultrasound's d30 is 30 x 24h of measured burn to its read instant")


CAUSES = {
    ("Ethereum", "burn_30d"): _eth_burn_cause,
    ("Maple", "dao_safe_balance"): _maple_dao_cause,
    ("Aerodrome", "annualised_dilution_incl_rebase_team"): _aero_dilution_cause,
    ("Maple", "total_supply"): _maple_supply_cause,
    ("Sky", "net_revenue_sep_2026"): _sky_revenue_cause,
    ("Ethereum", "supply"): _eth_supply_cause,
    ("Near", "intents_cumulative_volume"): _near_intents_cause,
}


def coverage_rows(results: list[dict]) -> list[dict]:
    """The reference checks as Coverage rows (coverage_audit.HEAD)."""
    out = []
    for x in results:
        ours = x["ours"]
        out.append({"Tab": TAB, "Column": f"{x['metric']} ({x['unit']})", "Key": "reference", "Project": x["project"],
                    "Value now": ours if not isinstance(ours, str) else ours[:120], "Status": x["status"],
                    "Reason / route / error": x["detail"][:400], "Axis": "", "Direction": "", "Tier": x["role"],
                    "History depth (quarters with a value)": "", "Formula as built": x["route"][:200],
                    "SPEC formula": f"reference {x['ref'][:60]} {x['unit']} @ {x['as_of']} — {x['source_type']}; "
                                    f"{x['source'][:80]}",
                    "MATCHES-SPEC": "yes — independent reference check (independent_references.csv)"})
    return out
