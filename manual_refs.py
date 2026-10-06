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
    ("Sky", "a3_protocol_yield"): {"url": "https://sky.money (page not pinned)", "tile": "SKY staking rewards rate",
                                   "unit": "fraction", "tol_pct": 15.0},
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
    ("Near", "a1_validator_yield"): {"url": "URL not on file — NEAR's published staking APR (near.org staking page "
                                     "or a validator explorer)", "tile": "staking APR", "unit": "fraction",
                                     "tol_pct": 15.0},
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
    ("Ether.fi", "in_buyback"): {"url": "URL not on file — Ether.fi's weekly buyback posts",
                                 "tile": "ETHFI bought in the month (weeks summed)", "unit": "ETHFI",
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
    if first.get("note"):
        ref["note"] = first["note"]
    return ref, None
