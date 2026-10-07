"""
manual_refs.py — manual readings that clear Credibility rows (Jake, 2026-10-06: "one form listing every manual
reference that would clear a row ... accept Jake's filled-in form in one pass and wire it").

    python manual_form.py make             writes manual_readings_form.csv: one line per open row whose root fix is
                                           a MANUAL READING — URL, the exact tile/label, our current value, rows cleared
    python manual_form.py load FILE.csv    validates the filled form, previews every reading, asks once, then writes
                                           manual_references.csv (the store of readings; committed, so it travels)

A reading replaces that row's reference with {"manual": ...} (credibility.reference already judges those) and keeps
OUR side as the row has it. A MONTHLY reading (buybacks per month) is compared month for month: ours becomes the sum
of the daily series over exactly the months read (formula sum_months), the reference the sum of the readings.
Nothing here guesses a value: an empty `value` cell is skipped, a non-number refused.
"""
from __future__ import annotations

import csv
from pathlib import Path

STORE = Path(__file__).resolve().parent / "manual_references.csv"
FORM = Path(__file__).resolve().parent / "manual_readings_form.csv"
FIELDS = ["project", "row", "period", "value", "unit", "read_on", "read_by", "url", "tile", "tol_pct", "note"]

# WHERE EACH READING IS, said once. (project, row) -> page, the exact tile/label, unit, default tolerance %, and
# for a monthly reading the stored daily metric it is compared with. A row not listed here still appears on the
# form, with its own RESOLVE text and "URL not on file" — never an invented page.
PAGES: dict = {
    ("Chainlink", "in_locked"): {"url": "https://staking.chain.link", "tile": "Total staked (LINK) — Community + "
                                 "Node Operator pools together", "unit": "LINK", "tol_pct": 2.0},
    ("Aerodrome", "a3_protocol_yield"): {"url": "https://aerodrome.finance/vote", "tile": "average voter APR "
                                         "(the page's vAPR across pools)", "unit": "fraction (0.25 = 25%)",
                                         "tol_pct": 15.0},
    ("Aerodrome", "in_locked"): {"url": "https://aerodrome.finance/vote", "tile": "total locked veAERO",
                                 "unit": "AERO", "tol_pct": 2.0},
    # A3 is the USDS-rewards farm's revenue-funded yield since 2026-10-07, so its page is that farm's.
    ("Sky", "a3_protocol_yield"): {"url": "https://info.skyeco.com/staking/0x38e4254bd82ed5ee97cd1c4278faae748d998865",
                                   "tile": "APY (USDS-rewards farm)", "unit": "fraction (0.25 = 25%)", "tol_pct": 15.0},
    ("Sky", "in_buyback"): {"url": "https://forum.sky.money (monthly settlement posts)",
                            "tile": "SKY bought in the month", "unit": "SKY", "tol_pct": 10.0,
                            "monthly_metric": "actual_buyback_tokens"},
    ("Near", "in_buyback"): {"url": "NEAR Foundation / NEAR Intents buyback announcements",
                             "tile": "NEAR bought in the month", "unit": "NEAR", "tol_pct": 10.0,
                             "monthly_metric": "actual_buyback_tokens"},
    ("Near", "a3_buyback_locked"): {"url": "https://nearblocks.io/address/buybacks.multisignature.near (+ "
                                    "fefundsadmin.sputnik-dao.near, 1csfundsadmin.sputnik-dao.near)",
                                    "tile": "Balance (NEAR), the three wallets summed", "unit": "NEAR",
                                    "tol_pct": 2.0},
    # ONE POOL, NET OF ITS COMMISSION (Jake, 2026-10-07): a near.com staking pool's APR is compared with our GROSS
    # validator yield — ours should read higher by that pool's commission. The account on that page is not stored.
    ("Near", "a1_validator_yield"): {"url": "near.com staking (one pool's page)", "tile": "pool APR, NET of the "
                                     "pool's commission — ours is GROSS", "unit": "fraction", "tol_pct": 15.0},
    # B7 (overnight 2026-10-06): the page is docs.aethir.com/aethir-tokenomics/ath-circulating-supply (a .md
    # variant exists); its robots.txt could not be read from here, so it stays a reading by hand, not a fetch.
    ("Aethir", "in_circ"): {"url": "https://docs.aethir.com/aethir-tokenomics/ath-circulating-supply",
                            "tile": "the October 2026 step of the monthly table", "unit": "ATH", "tol_pct": 2.0},
    ("GEODNET", "a2_customer_revenue"): {"url": "URL not on file — GEODNET's own monthly revenue reports",
                                         "tile": "revenue in the month ($)", "unit": "USD", "tol_pct": 10.0,
                                         "monthly_metric": "customer_revenue_usd"},
    # Sky's Stage 2 burns (overnight 2026-10-06, D): the monthly settlement post's burned SKY, month by month.
    ("Sky", "a4_gross_burn"): {"url": "https://forum.sky.money (monthly settlement posts)",
                               "tile": "SKY burned in the month (Stage 2, Sky.burn)", "unit": "SKY", "tol_pct": 10.0,
                               "monthly_metric": "sky_stage2_burn_tokens"},
    ("Pendle", "in_emissions"): {"url": "URL not on file — Pendle's own weekly AIM incentive totals",
                                 "tile": "PENDLE emitted in the month (weeks summed)", "unit": "PENDLE",
                                 "tol_pct": 10.0, "monthly_metric": "emissions_tokens"},
    ("Morpho", "in_circ"): {"url": "URL not on file — Morpho's own stated circulating supply",
                            "tile": "circulating supply (MORPHO)", "unit": "MORPHO", "tol_pct": 2.0},
    # JAKE'S READINGS OF 2026-10-07 (given in chat): where each was read.
    ("Hyperliquid", "in_locked"): {"url": "https://app.hyperliquid.xyz/staking", "tile": "Total staked (HYPE)",
                                   "unit": "HYPE", "tol_pct": 2.0},
    ("Sky", "in_locked"): {"url": "https://info.skyeco.com/staking", "tile": "staked SKY, SKY-rewards farm + "
                           "USDS-rewards farm", "unit": "SKY", "tol_pct": 2.0},
    ("Sky", "in_locked_sky_farm"): {"url": "https://info.skyeco.com/staking/0xb44c2fb4181d7cb06bdff34a46fdfe4a259b40fc",
                                    "tile": "SKY staked (SKY-rewards farm)", "unit": "SKY", "tol_pct": 2.0},
    ("Sky", "in_locked_usds_farm"): {"url": "https://info.skyeco.com/staking/0x38e4254bd82ed5ee97cd1c4278faae748d998865",
                                     "tile": "SKY staked (USDS-rewards farm)", "unit": "SKY", "tol_pct": 2.0},
    ("Sky", "in_apy_sky_farm"): {"url": "https://info.skyeco.com/staking/0xb44c2fb4181d7cb06bdff34a46fdfe4a259b40fc",
                                 "tile": "APY (SKY-rewards farm)", "unit": "fraction (0.25 = 25%)", "tol_pct": 15.0},
    ("Sky", "in_apy_usds_farm"): {"url": "https://info.skyeco.com/staking/0x38e4254bd82ed5ee97cd1c4278faae748d998865",
                                  "tile": "APY (USDS-rewards farm)", "unit": "fraction (0.25 = 25%)", "tol_pct": 15.0},
    # RECORDED, NOT COMPARED (Jake's probes15, root N): our side is CLOSED (config UNAVAILABLE staking_apr_ai /
    # staking_apr_gaming — the dashboard payload carries the APR series undated) and the two veAethir pools expose no
    # reward-rate read with a verified ABI, so there is nothing of ours to judge. The reading stands as the figure.
    ("Aethir", "in_apr_ai"): {"url": "https://dashboard.aethir.com", "tile": "AI pool average APR",
                              "unit": "fraction (0.25 = 25%)", "tol_pct": 5.0, "same_source": True,
                              "record_only": "our APR is CLOSED (undated dashboard series; no verified pool reward-rate "
                                             "read) — the reading is the figure, recorded"},
    ("Aethir", "in_apr_gaming"): {"url": "https://dashboard.aethir.com", "tile": "Gaming pool average APR",
                                  "unit": "fraction (0.25 = 25%)", "tol_pct": 5.0, "same_source": True,
                                  "record_only": "our APR is CLOSED (undated dashboard series; no verified pool "
                                                 "reward-rate read) — the reading is the figure, recorded"},
    ("GEODNET", "in_burn_report_months"): {"url": "GEODNET's monthly burn reports", "tile": "GEOD burned in the month",
                                           "unit": "GEOD", "tol_pct": 5.0, "monthly_metric": "gross_burn_tokens"},
    ("Ether.fi", "in_locked"): {"url": "https://etherscan.io/token/0x86B5780b606940Eb59A062aA85a07959518c0161",
                                "tile": "sETHFI total supply (shares; the ETHFI it holds is higher by the accrued "
                                        "share price)", "unit": "sETHFI", "tol_pct": 1.0},
    # RECORDED, NOT COMPARED (Jake's probes14, 2026-10-07): the app's APY is a FORWARD rate; set against our trailing
    # realised year it is the wrong window and fails by construction. Its reading is shown beside ours, labelled.
    ("Ether.fi", "in_apy_published"): {"url": "https://app.ether.fi", "tile": "staked-ETHFI APY (the app's published, "
                                       "forward rate)", "unit": "fraction (0.25 = 25%)", "tol_pct": 25.0,
                                       "record_only": "the app's FORWARD rate beside our trailing-365 realised yield — "
                                                      "different quantities, so it is recorded, not judged"},
    ("Near", "in_buyback_wallets"): {"url": "https://revenue.near.org", "tile": "Wallet Breakdown (All-time), total "
                                      "NEAR of the three wallets", "unit": "NEAR", "tol_pct": 5.0},
    ("Aerodrome", "in_voting_power"): {"url": "https://aerodrome.finance", "tile": "total veAERO (voting power — "
                                       "decays with lock time; NOT AERO locked)", "unit": "veAERO", "tol_pct": 2.0},
    # The programme page (etherfi.gitbook.io/gov/ethfi-buyback-program, Jake 2026-10-07): "All buybacks will be
    # announced on" the Foundation's X account.
    ("Ether.fi", "in_buyback"): {"url": "https://x.com/ether_fi_Fdn (the Foundation's buyback announcements)",
                                 "tile": "ETHFI bought in the month (announcements summed)", "unit": "ETHFI",
                                 "tol_pct": 10.0, "monthly_metric": "actual_buyback_tokens"},
}
STAKING_PAGE = {"tile": "total staked", "unit": "tokens", "tol_pct": 2.0}


def page_for(project: str, row: str) -> dict:
    hit = PAGES.get((project, row))
    if hit:
        return hit
    if row == "in_locked":
        return {**STAKING_PAGE, "url": "URL not on file — the protocol's own staking page"}
    if row == "in_circ":
        return {"url": "URL not on file — the project's own tokenomics / transparency page",
                "tile": "circulating supply", "unit": "tokens", "tol_pct": 2.0}
    return {"url": "URL not on file", "tile": "", "unit": "", "tol_pct": 10.0}


def load(path: Path | None = None) -> list[dict]:
    """The stored readings (manual_references.csv), or [] when there is none."""
    path = STORE if path is None else path
    if not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def save(rows: list[dict], path: Path | None = None) -> None:
    path = STORE if path is None else path
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def by_row(rows: list[dict] | None = None) -> dict:
    """{(project, row): [readings]} — several for a monthly row (one per period)."""
    out: dict = {}
    for r in (load() if rows is None else rows):
        out.setdefault((r["project"], r["row"]), []).append(r)
    return out


def reference_for(readings: list[dict], ours: dict | None) -> tuple[dict, dict | None]:
    """(reference spec, our side or None to keep the row's own) for one row's readings."""
    first = readings[0]
    pg = page_for(first["project"], first["row"])
    tol = float(first.get("tol_pct") or pg.get("tol_pct") or 10.0)
    src = f"{first.get('url') or pg['url']} — {first.get('tile') or pg.get('tile', '')}".strip(" —")
    by = first.get("read_by") or "Jake"
    if pg.get("monthly_metric") or any(r.get("period") for r in readings):
        months = sorted({r["period"] for r in readings if r.get("period")})
        total = sum(float(r["value"]) for r in readings if r.get("period"))
        last = max(r.get("read_on") or "" for r in readings)
        ref = {"manual": {"value": total, "read_on": last, "read_by": by,
                          "source": f"{src}, months {', '.join(months)}"}, "tol": tol,
               "note": f"Manual monthly readings ({', '.join(months)}), summed; ours is the daily series over "
                       f"exactly those months."}
        metric = pg.get("monthly_metric") or (ours or {}).get("metric")
        return ref, {"py": "sum_months", "args": {"metric": metric, "months": months}}
    ref = {"manual": {"value": float(first["value"]), "read_on": first.get("read_on"), "read_by": by,
                      "source": src}, "tol": tol}
    if pg.get("record_only"):                  # shown beside ours, never judged (a different quantity)
        return {"verdict": "N/A (recorded)", "why": f"{first['value']} read {first.get('read_on')} by {by} ({src}): "
                                                    f"{pg['record_only']}"}, None
    if pg.get("same_source"):                  # the page ours reads too: FRESH-only at best
        ref["same_source"] = True
    if first.get("note"):
        ref["note"] = first["note"]
    return ref, None
