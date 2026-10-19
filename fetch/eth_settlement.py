"""
fetch/eth_settlement.py — tier 1: ETHEREUM'S SETTLEMENT VOLUME REBUILT FROM FREE SOURCES, LEG BY LEG (Round G7 item 8,
Jake 2026-10-19: "settlement without an Artemis key"). config.SETTLEMENT_REBUILD["ethereum_rebuild"].

  NFT  nft_volume_usd — DefiLlama /overview/nft-volume/ethereum, the whole daily chart once a day, complete days only.
  P2P  p2p_transfer_volume_usd — BigQuery bigquery-public-data.crypto_ethereum (sql/ethereum/bigquery_p2p_daily.sql):
       native ETH + the declared ERC-20s between non-contract accounts. Every query is DRY-RUN first; the one-week and
       validation-month bytes are logged at each weekly turn even when nothing is approved — the figures Jake decides
       on. It runs in NEAR's quota project on NEAR's ledger (fetch.near_bigquery: one free 1 TB a month, one budget), only
       when p2p.approved is set: the validation window first, then each complete week (Monday..Sunday) not yet held.
       ETH and WETH at the store's same-day Ethereum price_usd; stablecoins at DefiLlama's same-day price, their
       decimals checked against DefiLlama's answer. A hand-run CSV of the query's output (day, token, amount, n) in
       data/ethereum/ is read the same way, for days not already held (never committed).
  DEX  dex_volume_usd is fetch/llama.trading_volume's.

The legs are summed and validated against Artemis's CSV at read time (build_workbook._settlement_rebuild_views).
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from .base import Http, tidy, today
from .near_bigquery import NearBigQuery, ReauthNeeded, is_reauth

log = logging.getLogger("token_metrics.fetch.eth_settlement")

SOURCE = "eth_settlement"
TIER = 1
API = "https://api.llama.fi"
ROOT = Path(__file__).resolve().parent.parent
SQL = ROOT / "sql" / "ethereum"


def _spec() -> dict:
    import config
    return config.SETTLEMENT_REBUILD["ethereum_rebuild"]


class EthNftVolume:
    """The NFT leg: DefiLlama's chain NFT volume, once a day."""
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, http: Http | None = None, daily=None, **_ignored):
        self.http = http or Http(min_interval=0.5)
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily

    def run(self, projects: list[dict], window_days, out):
        spec = _spec()
        name, metric = spec["project"], spec["legs"]["nft"]
        if not any(p["name"] == name for p in projects):
            return
        day, key = str(today().date()), f"{SOURCE}:{name}:{metric}"
        if not self.daily.due(key, day):
            out.mark_current(SOURCE, name, metric, f"{metric}: read today already", TIER)
            return
        path = spec["nft_endpoint"]
        try:
            j = self.http.get(API + path, params={"excludeTotalDataChart": "false",
                                                  "excludeTotalDataChartBreakdown": "true"})
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"{metric}: {path}: {str(e)[:200]}", TIER)
            return
        got = str((j or {}).get("chain") or "") if isinstance(j, dict) else ""
        if got and got.lower() != name.lower():
            out.fail(SOURCE, name, f"{metric}: {path} answered for chain {got!r}, not {name!r} — NOTHING STORED", TIER)
            return
        rows = [(pd.Timestamp(int(t), unit="s").normalize(), float(v))
                for t, v in ((j or {}).get("totalDataChart") or []) if v is not None]
        rows = [(d, v) for d, v in rows if d < today()]
        if not rows:
            out.fail(SOURCE, name, f"{metric}: {path} carried no daily chart", TIER)
            return
        out.add(tidy(rows, name, metric, f"{SOURCE}:defillama{path}", TIER), SOURCE, name,
                f"{metric} = DefiLlama {path}: {len(rows)} day(s) {rows[0][0].date()}..{rows[-1][0].date()} — the NFT "
                f"leg of the settlement rebuild", TIER)
        self.daily.done(key, day)


class EthP2PBigQuery(NearBigQuery):
    """The P2P leg. NearBigQuery's client, guard, dry run, budget and ledger, on the Ethereum public dataset."""
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, csv_dir: Path | None = None, **kw):
        super().__init__(**kw)
        self.eth_csv_dir = csv_dir

    def _bq_spec(self) -> dict:
        import config
        near = config.PROJECT_BY_NAME["Near"]["near_bigquery"]
        p2p = _spec()["p2p"]
        return {**{k: near[k] for k in ("project", "project_env", "monthly_budget_bytes", "topup_reserve_margin",
                                         "census_every_days")},
                "allowed_dataset": p2p["allowed_dataset"], "max_bytes_per_query": p2p["max_bytes_per_query"],
                "price_api": p2p["price_api"]}

    @staticmethod
    def weeks(through: pd.Timestamp | None, now: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
        """Complete Monday..Sunday weeks after `through` (or only the last one), oldest first."""
        last_sun = (now.normalize() - pd.Timedelta(days=now.weekday() + 1))
        start = (pd.Timestamp(through) + pd.Timedelta(days=1)) if through is not None else last_sun - pd.Timedelta(days=6)
        start = start - pd.Timedelta(days=start.weekday())
        out = []
        while start + pd.Timedelta(days=6) <= last_sun:
            out.append((start, start + pd.Timedelta(days=6)))
            start += pd.Timedelta(days=7)
        return out

    def run(self, projects: list[dict], window_days, out, unbounded: bool = False):
        spec = _spec()
        name = spec["project"]
        if not any(p["name"] == name for p in projects):
            return
        import os
        from .near_bigquery import SKIP_ENV
        p2p, bq = spec["p2p"], self._bq_spec()
        self._run_bytes = self._backfill_bytes = 0
        st = self._load()
        st.setdefault("ledger", {})
        e = st.setdefault("eth_p2p", {"days": {}, "prices": {}})
        self._import_eth_csv(e, out, name, p2p)
        now = today()
        last = e.get("turn_on")
        if last and (now - pd.Timestamp(last)).days < int(p2p["cadence_days"]):
            out.mark_current(SOURCE, name, spec["legs"]["p2p"], f"{spec['legs']['p2p']}: weekly — last turn {last}",
                             TIER)
        elif os.environ.get(SKIP_ENV) == "1":
            out.skipped(SOURCE, name, "Ethereum P2P (BigQuery) skipped — the daily run's BigQuery check failed", TIER)
        else:
            try:
                client, why = self._client(bq)
            except Exception as ex:  # noqa: BLE001
                if not is_reauth(ex):
                    raise
                client, why = None, f"Application Default Credentials need re-authentication ({str(ex)[:80]})"
            if client is None:
                out.skipped(SOURCE, name, f"{spec['legs']['p2p']}: NOT READ — {why}", TIER)
            else:
                try:
                    self._turn(client, bq, p2p, st, e, out, name, now)
                    e["turn_on"] = str(now.date())
                except ReauthNeeded as ex:
                    out.fail(SOURCE, name, f"{spec['legs']['p2p']}: ADC re-authentication needed — {str(ex)[:120]}",
                             TIER)
                out.skipped(SOURCE, name, self._quota_line(bq, st), TIER)
        self._save(st)
        self._value_eth(p2p, st, e, out, name, spec["legs"]["p2p"])

    def _turn(self, client, bq, p2p, st, e, out, name, now) -> None:
        sql = (SQL / p2p["sql"]).read_text()
        toks = [t[1] for t in p2p["tokens"]]
        wk = self.weeks(None, now)[-1]
        val = (pd.Timestamp(p2p["validation_from"]), pd.Timestamp(_spec()["validation"]["to"]))
        est = {}
        for what, (a, b) in (("one week", wk), ("validation window", val)):
            try:
                est[what] = self._dry(client, sql, {"d0": a.date(), "d1": b.date(), "tokens": toks}, bq)
            except Exception as ex:  # noqa: BLE001
                if is_reauth(ex):
                    raise ReauthNeeded(str(ex)) from ex
                out.fail(SOURCE, name, f"p2p dry run ({what}): {type(ex).__name__}: {str(ex)[:160]}", TIER)
                return
        e["dry"] = {k: int(v) for k, v in est.items()} | {"on": str(now.date())}
        out.skipped(SOURCE, name, "Ethereum P2P DRY RUNS (bytes, no quota): " + "; ".join(
            f"{k} {v / 1e9:,.1f} GB" for k, v in est.items()) + f" — weekly ≈ {est['one week'] * 52 / 12 / 1e9:,.0f} GB "
            f"a month on the shared {int(bq['monthly_budget_bytes']) / 1e9:,.0f} GB budget", TIER)
        if not p2p.get("approved"):
            out.skipped(SOURCE, name, "p2p_transfer_volume_usd NOT APPROVED — dry runs only (config SETTLEMENT_REBUILD."
                                      "ethereum_rebuild.p2p.approved, Jake's decision on the logged bytes)", TIER)
            return
        todo = []
        if not e.get("validation_done"):
            todo.append(("validation window", val, True))
        through = pd.Timestamp(e["through"]) if e.get("through") else None
        todo += [(f"week {a.date()}..{b.date()}", (a, b), False) for a, b in self.weeks(through, now)]
        for what, (a, b), is_val in todo:
            rows = self._run(client, f"p2p {what}", sql, {"d0": a.date(), "d1": b.date(), "tokens": toks}, bq, st, out,
                             name, backfill=is_val)          # topup_bytes stays NEAR's (its reserve)
            if rows is None:
                break
            for r in rows:
                e["days"].setdefault(str(pd.Timestamp(r["day"]).date()), {})[str(r["token"])] = str(r["amount"])
            if is_val:
                e["validation_done"] = True
            else:
                e["through"] = str(b.date())

    def _import_eth_csv(self, e, out, name, p2p) -> None:
        import csv
        root = self.eth_csv_dir or ROOT / p2p["csv_dir"]
        added = 0
        for f in sorted(Path(root).glob("*.csv")) if Path(root).exists() else ():
            with open(f, newline="", encoding="utf-8-sig") as fh:
                for r in csv.DictReader(fh):
                    try:
                        d = str(pd.Timestamp(r["day"]).date())
                    except (KeyError, ValueError):
                        continue
                    if d not in e["days"] or r["token"] not in e["days"][d]:
                        e["days"].setdefault(d, {})[str(r["token"]).lower() if r["token"] != "ETH" else "ETH"] = \
                            str(r["amount"])
                        added += 1
        if added:
            out.skipped(SOURCE, name, f"P2P CSV (hand-run query): {added} token-day(s) read from {root}", TIER)

    def _value_eth(self, p2p, st, e, out, name, metric) -> None:
        eth_px = self._stored(name, "price_usd")
        dec = {t[1]: int(t[2]) for t in p2p["tokens"]}
        weth = next(t[1] for t in p2p["tokens"] if t[0] == "WETH")
        stored, no_eth, refused, notes = [], 0, 0, []
        for key in sorted(e["days"]):
            day, toks = pd.Timestamp(key), e["days"][key]
            if day >= today():
                continue
            p_eth = eth_px.get(day) if eth_px is not None else None
            if p_eth is None or pd.isna(p_eth):
                no_eth += 1
                continue
            stables = [t for t in toks if t not in ("ETH", weth)]
            px = e["prices"].get(key)
            if px is None and stables:
                try:
                    px = {c: list(v) for c, v in self._price(p2p, day, [f"ethereum:{t}" for t in stables]).items()}
                except Exception as ex:  # noqa: BLE001
                    notes.append(f"prices {key}: {str(ex).split('?')[0][:80]}")
                    break
                e["prices"][key] = px
            px = px or {}
            try:
                usd = float(Decimal(toks.get("ETH", "0")) / Decimal(10) ** 18) * p_eth
                usd += float(Decimal(toks.get(weth, "0")) / Decimal(10) ** 18) * p_eth
                ok = True
                for t in stables:
                    p = px.get(f"ethereum:{t}") or (None, None)
                    if p[0] is None or p[1] is None or int(p[1]) != dec.get(t):
                        ok = False                       # unpriced, or decimals not as declared: the day is refused
                        break
                    usd += float(Decimal(toks[t]) / Decimal(10) ** dec[t]) * p[0]
            except (InvalidOperation, ValueError) as ex:
                notes.append(f"{key}: {ex}")
                continue
            if not ok:
                refused += 1
                continue
            stored.append((day, usd))
        self._save(st)
        if not stored:
            if e["days"]:
                out.skipped(SOURCE, name, f"{metric}: {len(e['days'])} day(s) held, none valued ({no_eth} without "
                                          f"a stored Ethereum price_usd, {refused} refused)", TIER)
            return
        out.add(tidy(stored, name, metric, f"{SOURCE}:bigquery:p2p[Artemis method, rebuilt]", TIER), SOURCE, name,
                f"{metric} = P2P transfers between non-contract accounts, native ETH + {len(dec)} ERC-20s; "
                f"{len(stored)} day(s) {stored[0][0].date()}..{stored[-1][0].date()}; {refused} day(s) refused "
                f"(unpriced or decimals not as declared); {no_eth} waiting on a price"
                + (f"; {'; '.join(notes[:3])}" if notes else ""), TIER)
