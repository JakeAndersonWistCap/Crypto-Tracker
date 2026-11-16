"""
fetch/hl_stats.py — tier 1: Hyperliquid's OWN statistics, for its S13 lines (R7B.C2, Jake 2026-11-16: "wire it into
Jake's daily run (his PC can reach it; your machine was refused) for fees by venue and the HIP-3 deployer share;
DefiLlama stays secondary until those rows are stored").

    hl_exchange_volume_usd   the exchange's own daily volume, info {type: userFees}.dailyUserVlm[].exchange (any user's
                             answer carries it; read from this machine 2026-11-16) — first-party, evidence beside DefiLlama
    hl_hip3_dex_count        how many HIP-3 perp DEXs are deployed, info {type: perpDexs} (each names its feeRecipient,
                             where the deployer's fee share is paid) — the deployer-share route's index
    <the stats series>       config HL_STATS["series"]: each a stats-data path with its declared date / value keys. A path
                             is STORED only once `confirmed` (its shape read on Jake's machine); until then every run
                             logs the path's HTTP status and the first record's keys, so the shape can be confirmed —
                             never a guessed field read as fees.
"""
from __future__ import annotations

import logging

import pandas as pd

from .base import Http, point, tidy

log = logging.getLogger("token_metrics.fetch.hl_stats")

SOURCE = "hl_stats"
TIER = 1


def exchange_volume(payload: dict) -> list[tuple]:
    """[(day, $)] from userFees.dailyUserVlm — the exchange-wide figure each record carries."""
    out = []
    for r in (payload or {}).get("dailyUserVlm") or []:
        try:
            out.append((pd.Timestamp(r["date"]), float(r["exchange"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def series_rows(payload, spec: dict) -> list[tuple]:
    """[(day, value)] from a stats-data payload: a list of records with the declared date / value keys."""
    rows = payload if isinstance(payload, list) else (payload or {}).get(spec.get("list_key", "chart_data")) or []
    out = []
    for r in rows:
        try:
            out.append((pd.Timestamp(r[spec["date_key"]]).normalize(), float(r[spec["value_key"]])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def shape_of(payload) -> str:
    rows = payload if isinstance(payload, list) else (payload or {}).get("chart_data") if isinstance(payload, dict) else None
    if isinstance(rows, list) and rows:
        first = rows[0]
        return f"a list of {len(rows)} record(s); first record keys {sorted(first)[:8] if isinstance(first, dict) else type(first).__name__}"
    if isinstance(payload, dict):
        return f"an object with keys {sorted(payload)[:8]}"
    return f"{type(payload).__name__}"


class HlStats:
    def __init__(self, http=None):
        self.http = http or Http()

    def run(self, projects: list[dict], window_days, out):
        import config                                      # noqa: PLC0415
        spec = config.HL_STATS
        for p in projects:
            if p["name"] != spec["project"]:
                continue
            name = p["name"]
            try:
                fees = self.http.post(spec["info_url"], json_body={"type": "userFees", "user": spec["any_user"]})
                rows = exchange_volume(fees)
                if rows:
                    out.add(tidy(rows, name, "hl_exchange_volume_usd", f"{SOURCE}:info.userFees.dailyUserVlm.exchange",
                                 TIER), SOURCE, name, f"hl_exchange_volume_usd: {len(rows)} day(s), latest "
                                                      f"{rows[-1][1]:,.0f}", TIER)
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                out.fail(SOURCE, name, f"hl_exchange_volume_usd: info userFees: {e}", TIER)
            try:
                dexs = [d for d in (self.http.post(spec["info_url"], json_body={"type": "perpDexs"}) or []) if d]
                out.add(point(name, "hl_hip3_dex_count", float(len(dexs)), f"{SOURCE}:info.perpDexs", TIER),
                        SOURCE, name, f"hl_hip3_dex_count={len(dexs)}: " + "; ".join(
                            f"{d.get('name')} feeRecipient {str(d.get('feeRecipient'))[:10]}…" for d in dexs), TIER)
            except Exception as e:  # noqa: BLE001
                out.fail(SOURCE, name, f"hl_hip3_dex_count: info perpDexs: {e}", TIER)
            for metric, s in spec["series"].items():
                url = f"{spec['stats_base']}/{s['path']}"
                try:
                    payload = self.http.get(url)
                except Exception as e:  # noqa: BLE001
                    out.fail(SOURCE, name, f"{metric}: {url}: {e} — NOTHING STORED", TIER)
                    continue
                if not s.get("confirmed"):
                    out.skipped(SOURCE, name, f"{metric}: {url} answered — {shape_of(payload)}. UNCONFIRMED: nothing "
                                              f"stored until its date / value keys are confirmed (config HL_STATS)", TIER)
                    continue
                rows = series_rows(payload, s)
                if not rows:
                    out.fail(SOURCE, name, f"{metric}: {url}: no record with the keys {s['date_key']} / "
                                           f"{s['value_key']} — {shape_of(payload)}", TIER)
                    continue
                out.add(tidy(rows, name, metric, f"{SOURCE}:stats-data/{s['path']}", TIER), SOURCE, name,
                        f"{metric}: {len(rows)} day(s), latest {rows[-1][1]:,.0f}", TIER)
