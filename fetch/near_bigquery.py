"""
fetch/near_bigquery.py — NEAR from Google's public BigQuery dataset. Jake, 2026-10-01.

bigquery-public-data.crypto_near_mainnet_us is LIVE (Jake ran sql/near/bigquery_freshness.sql in his
sandbox project near-data-510309: MAX(block_date) = 2026-10-01 on blocks, execution_outcomes and
receipt_actions) — the NEAR Lake deprecation did not freeze it. Queries run in near-data-510309, which
has no billing: it cannot be charged, and BigQuery refuses a query once the free 1 TB/month is spent.

THREE READS, EACH GATED BY config Near.near_bigquery.approved (Jake decides after the dry runs):

  circulating   sql/near/bigquery_circulating_supply.sql — NEAR's own daily circulating supply
                (total − lockups − lockup.near − contributors.near), full history once, then new
                days. 10 MB a run (BigQuery's minimum; the table is ~45 KB). Stored as
                circulating_supply_first_party (PRIMARY for NEAR; CoinGecko the cross-check).
  p2p           sql/near/bigquery_token_census.sql picks the NEP-141 tokens covering >95% of value
                (priced by DefiLlama, decimals from the same answer); sql/near/bigquery_p2p_daily.sql
                then aggregates P2P transfers per day, month-sized chunks newest first, plus each
                day's top-up. Stored as p2p_transfer_volume_usd, "Artemis method (adapted to NEAR),
                UNVALIDATED": native NEAR and wrap.near at the SAME-DAY NEAR price in this store
                (price_usd), every other token at DefiLlama's same-day price; an unpriced token is
                left out and counted. DEX volume is added at read time (build_workbook
                _rebuilt_settlement_view); NFT ~0.
  dry runs      every heavy query is DRY-RUN first (free, no quota): the bytes it would scan are
                logged, and the year-backfill and one-day figures are logged once a day even when
                nothing is approved — the numbers Jake decides on.

THE MONTH'S TOP-UPS ARE RESERVED BEFORE BACKFILL SPENDS (Jake, 2026-10-01). The daily top-up comes
out of the same budget, so backfill may spend only budget − used − reserve, where reserve = the days
left in the month x the latest measured one-day top-up dry run x 1.2 (+ the token census if its refresh
falls due this month). No measured top-up yet: backfill is held. Every run logs the QUOTA line: bytes
this run, backfill spend, month used, remaining, the reserve and what it leaves for backfill.

THE QUOTA IS LEDGERED. Each query's bytes billed is added to <logcache>/near-bq.json under its UTC
month; a query whose dry run would take the month past `monthly_budget_bytes`, or is larger than
`max_bytes_per_query`, is not run, and says so. maximum_bytes_billed is set on every job to its dry
run, so BigQuery itself refuses anything larger.

AUTH: Application Default Credentials from Jake's own login (`gcloud auth application-default login`,
quota project near-data-510309) — the service-account key route is blocked by his organisation's
iam.disableServiceAccountKeyCreation (RUNBOOK 11n). Those credentials can reach his other projects, so
the client is pinned to near-data-510309, a different quota project is refused, and every query is
checked to name only bigquery-public-data.crypto_near_mainnet_us before it is sent.
FALLBACK: CSVs of the P2P query's output (day, token, amount, n) saved by hand into data/near/ are
read the same way, for days not already held.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from .base import Http, Progress, tidy, today

log = logging.getLogger("token_metrics.fetch.near_bigquery")

SOURCE = "near_bigquery"
TIER = 1
REAUTH_ACTION = "run `gcloud auth application-default login`"
QUOTA_REMINDER = "ADC has no quota project — run `gcloud auth application-default set-quota-project {project}`"


class ReauthNeeded(Exception):
    """Jake's Application Default Credentials expired (google.auth RefreshError, "Reauthentication is
    needed"). Every BigQuery read stops for the run, gracefully, with the action in the run summary."""


def is_reauth(e: BaseException) -> bool:
    try:
        from google.auth.exceptions import RefreshError
        if isinstance(e, RefreshError):
            return True
    except ImportError:
        pass
    t = str(e)
    return "Reauthentication is needed" in t or "invalid_grant" in t or "reauth" in t.lower()
LABEL = "Artemis method (adapted to NEAR), UNVALIDATED"
SQL_DIR = Path(__file__).resolve().parent.parent / "sql" / "near"
NATIVE = "NEAR"
WRAPPED = "wrap.near"


def _sql(name: str) -> str:
    return (SQL_DIR / name).read_text()


class NearBigQuery:
    SOURCE = SOURCE
    TIER = TIER

    def __init__(self, client=None, bq=None, stored_long=None, http: Http | None = None, prices=None,
                 cache_file: Path | None = None, csv_dir: Path | None = None, **_ignored):
        self._client_obj, self._bq = client, bq
        self.stored_long = stored_long
        self.http = http or Http(min_interval=0.25, retries=2)
        self._prices = prices                        # tests inject {(day, coin): (price, decimals)}
        self.cache_file = cache_file
        self.csv_dir = csv_dir

    # --- state ---------------------------------------------------------------------------------
    def _path(self) -> Path:
        if self.cache_file:
            return Path(self.cache_file)
        from .logcache import LogCache
        return LogCache().root / "near-bq.json"

    def _load(self) -> dict:
        try:
            return json.loads(self._path().read_text())
        except (OSError, ValueError):
            return {"ledger": {}, "tokens": None, "days": {}, "prices": {}, "dry_on": None}

    def _save(self, st: dict) -> None:
        p = self._path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st))

    # --- BigQuery ------------------------------------------------------------------------------
    def _client(self, spec: dict):
        """(client, None) or (None, why). APPLICATION DEFAULT CREDENTIALS from Jake's own login
        (`gcloud auth application-default login`) — the service-account key route is blocked by his
        organisation's iam.disableServiceAccountKeyCreation. Those credentials carry Jake's USER
        access, which reaches other projects (cbc-risk-regime-api), so the client is pinned to the
        declared project and anything else is refused (and _guard_sql refuses any table outside the
        public dataset)."""
        if self._client_obj is not None:
            return self._client_obj, None
        project = os.environ.get(spec["project_env"]) or spec["project"]
        if project != spec["project"]:
            return None, (f"{spec['project_env']} names a different project than the declared "
                          f"{spec['project']} — refused, so no other project is ever billed or touched")
        try:
            import google.auth
            from google.auth.exceptions import DefaultCredentialsError
            from google.cloud import bigquery
        except ImportError:
            return None, "google-cloud-bigquery is not installed (pip install google-cloud-bigquery)"
        try:
            creds, _adc_project = google.auth.default(scopes=["https://www.googleapis.com/auth/bigquery"])
        except DefaultCredentialsError:
            return None, ("no Application Default Credentials — run `gcloud auth application-default login` "
                          f"then `gcloud auth application-default set-quota-project {spec['project']}` (RUNBOOK 11n)")
        quota = getattr(creds, "quota_project_id", None)
        if not quota:
            # Jake, 2026-10-02: a re-login drops the ADC quota project — say so every time it is missing
            self.quota_reminder = QUOTA_REMINDER.format(project=spec["project"])
            log.warning("NEAR BigQuery: %s", self.quota_reminder)
        if quota and quota != spec["project"]:
            return None, (f"the ADC quota project is {quota}, not {spec['project']} — refused; run "
                          f"`gcloud auth application-default set-quota-project {spec['project']}`")
        self._bq = bigquery
        self._client_obj = bigquery.Client(project=spec["project"], credentials=creds)
        if getattr(self._client_obj, "project", spec["project"]) != spec["project"]:
            self._client_obj = None
            return None, f"the BigQuery client resolved a project other than {spec['project']} — refused"
        return self._client_obj, None

    def _params(self, params: dict):
        bq = self._bq
        out = []
        for k, v in params.items():
            if isinstance(v, (list, tuple)):
                out.append(bq.ArrayQueryParameter(k, "STRING", list(v)))
            else:
                out.append(bq.ScalarQueryParameter(k, "DATE", str(v)))
        return out

    @staticmethod
    def _guard_sql(sql: str, spec: dict) -> None:
        """Every table a query names must be in the public NEAR dataset: Jake's own credentials can
        read other projects' data, and a query naming one is refused before it is sent."""
        allowed = spec.get("allowed_dataset", "bigquery-public-data.crypto_near_mainnet_us")
        code = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())   # comments are prose
        for ref in re.findall(r"`([^`]+\.[^`]+)`", code):
            if not ref.startswith(allowed):
                raise PermissionError(f"query names `{ref}`, outside {allowed} — refused")

    @staticmethod
    def billing_cap(est: int) -> int:
        """maximum_bytes_billed for a job whose dry run said `est`. NOT the dry run itself: billing
        rounds up (Jake's run 2026-10-01: the census dry-ran 1,592,724,227 bytes and BigQuery refused it
        at that cap — "1592786944 or higher required"). Dry run x 1.05 + 10 MB, and never below
        BigQuery's 10 MB minimum. The monthly budget and the backfill reserve are still judged on the
        dry run; the ledger records what was actually billed."""
        mb10 = 10 * 1024 ** 2
        return max(int(est * 1.05) + mb10, mb10)

    def _dry(self, client, sql: str, params: dict, spec: dict | None = None) -> int:
        self._guard_sql(sql, spec or {})
        cfg = self._bq.QueryJobConfig(dry_run=True, use_query_cache=False, query_parameters=self._params(params))
        return int(client.query(sql, job_config=cfg).total_bytes_processed or 0)

    def _run(self, client, what: str, sql: str, params: dict, spec: dict, st: dict, out, name: str,
             backfill: bool = False, topup_days: int = 0):
        """Rows, or None with the reason logged. Dry run, budget, then the job capped at its dry run.
        A BACKFILL query may only spend what the month's top-up reserve leaves (_reserve)."""
        try:
            est = self._dry(client, sql, params, spec)
        except Exception as e:  # noqa: BLE001
            if is_reauth(e):
                raise ReauthNeeded(str(e)) from e
            out.fail(SOURCE, name, f"{what}: dry run failed — {type(e).__name__}: {str(e)[:200]}", TIER)
            return None
        if topup_days:                                    # a measured top-up size, per day
            st["topup_bytes"] = {"bytes": est / topup_days, "on": str(today().date()),
                                 "source": f"top-up dry run ({topup_days} day(s))"}
        month = str(today().date())[:7]
        used = int(st["ledger"].get(month, 0))
        if est > int(spec["max_bytes_per_query"]):
            out.skipped(SOURCE, name, f"{what}: dry run {est / 1e9:,.1f} GB is over the per-query cap "
                                      f"{int(spec['max_bytes_per_query']) / 1e9:,.0f} GB — NOT RUN", TIER)
            return None
        if backfill:
            reserve, why = self._reserve(spec, st)
            if reserve is None:
                out.skipped(SOURCE, name, f"{what}: BACKFILL HELD — {why}", TIER)
                return None
            room = int(spec["monthly_budget_bytes"]) - used - reserve
            if est > room:
                out.skipped(SOURCE, name, f"{what}: BACKFILL HELD — dry run {est / 1e9:,.1f} GB is more than the "
                                          f"{max(room, 0) / 1e9:,.1f} GB left for backfill after the top-up reserve "
                                          f"({reserve / 1e9:,.1f} GB: {why}); resumes next month", TIER)
                return None
        if used + est > int(spec["monthly_budget_bytes"]):
            out.skipped(SOURCE, name, f"{what}: dry run {est / 1e9:,.1f} GB would take {month} to "
                                      f"{(used + est) / 1e9:,.1f} GB, past the budget "
                                      f"{int(spec['monthly_budget_bytes']) / 1e9:,.0f} GB — NOT RUN; resumes "
                                      f"next month", TIER)
            return None
        cfg = self._bq.QueryJobConfig(use_query_cache=True, query_parameters=self._params(params),
                                      maximum_bytes_billed=self.billing_cap(est))
        try:
            job = client.query(sql, job_config=cfg)
            rows = [dict(r.items()) if hasattr(r, "items") else dict(r) for r in job.result()]
        except Exception as e:  # noqa: BLE001
            if is_reauth(e):
                raise ReauthNeeded(str(e)) from e
            out.fail(SOURCE, name, f"{what}: {type(e).__name__}: {str(e)[:200]}", TIER)
            return None
        if getattr(job, "project", spec["project"]) != spec["project"]:
            out.fail(SOURCE, name, f"{what}: the job ran in a project other than {spec['project']} — stopped", TIER)
            return None
        billed = int(getattr(job, "total_bytes_billed", None) or est)
        self._run_bytes += billed
        if backfill:
            self._backfill_bytes += billed
        st["ledger"][month] = used + billed
        self._save(st)
        out.skipped(SOURCE, name, f"{what}: {len(rows)} row(s); {billed / 1e9:,.2f} GB billed (dry run "
                                  f"{est / 1e9:,.2f} GB); {month} so far {(used + billed) / 1e9:,.1f} GB of "
                                  f"{int(spec['monthly_budget_bytes']) / 1e9:,.0f} GB", TIER)
        return rows

    def _reserve(self, spec: dict, st: dict):
        """(bytes, how) the rest of the month's top-ups need — or (None, why) when no top-up has been
        measured yet, which HOLDS the backfill rather than guess. Jake, 2026-10-01: a seed spending the
        whole 900 GB on day 1 would block every top-up for the rest of the month, so backfill spends only
        budget − used − reserve. reserve = days left in the month after today x the latest measured
        one-day top-up dry run x the margin (1.2), plus the token census's last dry run (x the margin)
        when its refresh falls due before the month ends."""
        tb = st.get("topup_bytes") or {}
        if not tb.get("bytes"):
            return None, ("no top-up has been dry-run yet, so the month's top-up reserve is unknown — "
                          "run `check_offline_items.py near_settlement_routes` or let a routine run dry-run one")
        now = today().normalize()
        month_end = (now + pd.offsets.MonthEnd(0)).normalize()
        days_left = (month_end - now).days
        margin = float(spec["topup_reserve_margin"])
        reserve = days_left * float(tb["bytes"]) * margin
        how = (f"{days_left} day(s) left x {float(tb['bytes']) / 1e9:,.2f} GB/day ({tb.get('source')}, "
               f"{tb.get('on')}) x {margin:g}")
        on = (st.get("tokens") or {}).get("on")
        cb = (st.get("census_bytes") or {}).get("bytes")
        if on and cb and (month_end - pd.Timestamp(on)).days > int(spec["census_every_days"]):
            reserve += float(cb) * margin
            how += f" + the token census due this month {float(cb) / 1e9:,.1f} GB x {margin:g}"
        return int(reserve), how

    def _quota_line(self, spec: dict, st: dict) -> str:
        month = str(today().date())[:7]
        budget, used = int(spec["monthly_budget_bytes"]), int(st["ledger"].get(month, 0))
        reserve, how = self._reserve(spec, st)
        line = (f"QUOTA {month}: {self._run_bytes / 1e9:,.2f} GB billed this run (backfill "
                f"{self._backfill_bytes / 1e9:,.2f} GB); month used {used / 1e9:,.1f} GB; remaining "
                f"{(budget - used) / 1e9:,.1f} GB of the {budget / 1e9:,.0f} GB budget (free tier 1 TB); ")
        if reserve is None:
            return line + f"top-up reserve UNKNOWN — backfill held ({how})"
        return line + (f"top-up reserve {reserve / 1e9:,.1f} GB ({how}); left for backfill "
                       f"{max(budget - used - reserve, 0) / 1e9:,.1f} GB")

    # --- the read ------------------------------------------------------------------------------
    def run(self, projects: list[dict], window_days, out, unbounded: bool = False):
        for p in projects:
            spec = p.get("near_bigquery")
            if spec:
                self._project(p["name"], spec, out, unbounded)

    def _project(self, name: str, spec: dict, out, unbounded: bool) -> None:
        self._run_bytes = self._backfill_bytes = 0
        st = self._load()
        st.setdefault("ledger", {})
        st.setdefault("days", {})
        st.setdefault("prices", {})
        self._import_csv(st, out, name)
        try:
            client, why = self._client(spec)
        except Exception as e:  # noqa: BLE001 — credentials that fail on first use
            if not is_reauth(e):
                raise
            client, why = None, None
            self._reauth(spec, st, out, name, e)
        if client is None and why:
            out.skipped(SOURCE, name, f"NOT READ — {why}. "
                                      + ("CSV fallback days are valued below." if st["days"] else ""), TIER)
        elif client is not None:
            try:
                ok = spec.get("approved") or {}
                if ok.get("circulating"):
                    self._circulating(client, spec, st, out, name)
                self._dry_estimates(client, spec, st, out, name)
                if ok.get("p2p"):
                    self._p2p(client, spec, st, out, name, unbounded)
                else:
                    out.skipped(SOURCE, name, "p2p NOT APPROVED — dry runs only (config Near.near_bigquery."
                                              "approved.p2p, Jake's decision on the logged estimates)", TIER)
            except ReauthNeeded as e:
                self._reauth(spec, st, out, name, e)
            out.skipped(SOURCE, name, self._quota_line(spec, st), TIER)
        if getattr(self, "quota_reminder", None):
            out.skipped(SOURCE, name, f"REMINDER: {self.quota_reminder}", TIER)
        self._save(st)
        self._value(spec, st, out, name)

    def _reauth(self, spec, st, out, name, e) -> None:
        """Expired ADC (probes6 4, Jake 2026-10-02): every BigQuery read stops for this run, the action goes
        to the run summary, and how often it has happened is kept (state `reauth`)."""
        day = str(today().date())
        r = st.setdefault("reauth", {"count": 0, "dates": []})
        if day not in r["dates"]:
            r["count"] = int(r.get("count", 0)) + 1
            r["dates"] = (r["dates"] + [day])[-30:]
        prev = [d for d in r["dates"] if d != day]
        out.fail(SOURCE, name, f"NOT READ — Application Default Credentials need re-authentication "
                               f"(\"Reauthentication is needed\"): {REAUTH_ACTION} (then `gcloud auth "
                               f"application-default set-quota-project {spec['project']}` if the reminder says so). "
                               f"Seen on {r['count']} day(s)" + (f"; before today: {', '.join(prev[-5:])}" if prev
                                                                 else " (first time)")
                               + ". Every NEAR BigQuery read stopped for this run; nothing stored from it.", TIER)

    def _circulating(self, client, spec, st, out, name) -> None:
        if st.get("circulating_on") == str(today().date()):       # once a day: the table is daily
            out.mark_current(SOURCE, name, spec["circulating_metric"], "circulating_supply already read today", TIER)
            return
        since = st.get("circulating_through") or "2000-01-01"
        rows = self._run(client, "circulating_supply", _sql("bigquery_circulating_supply.sql"),
                         {"since": since}, spec, st, out, name)
        if rows is None:
            return
        st["circulating_on"] = str(today().date())
        pts = []
        for r in rows:
            try:
                pts.append((pd.Timestamp(r["block_date"]), float(r["circulating_tokens_supply"]) / 1e24))
            except (TypeError, ValueError, KeyError):
                continue
        if not pts:
            return
        st["circulating_through"] = str(pts[-1][0].date())
        frame = tidy(pts, name, spec["circulating_metric"], f"{SOURCE}:circulating_supply", TIER)
        msg = (f"{spec['circulating_metric']} = NEAR's own circulating_supply table (total − lockups − "
               f"lockup.near − contributors.near): {len(frame)} day(s) {pts[0][0].date()}..{pts[-1][0].date()}, "
               f"latest {pts[-1][1]:,.0f} NEAR")
        cg = self._stored(name, "circulating_supply")
        if cg is not None and not cg.empty:
            v = float(cg.iloc[-1])
            msg += f"; CoinGecko's latest {v:,.0f} ({pts[-1][1] / v - 1:+.2%} ours vs CoinGecko)"
        out.add(frame, SOURCE, name, msg, TIER)

    def _dry_estimates(self, client, spec, st, out, name) -> None:
        """Once a day: what a year's backfill and one day's top-up would scan (free)."""
        day = str(today().date())
        if st.get("dry_on") == day:
            return
        yday = (today() - pd.Timedelta(days=1)).normalize()
        toks = (st.get("tokens") or {}).get("list") or list(spec["seed_tokens"])
        parts = []
        try:
            for label, d0 in (("year", yday - pd.Timedelta(days=int(spec["days"]) - 1)), ("one day", yday)):
                b = self._dry(client, _sql("bigquery_p2p_daily.sql"),
                              {"d0": d0.date(), "d1": yday.date(), "tokens": toks}, spec)
                parts.append(f"{label} {b / 1e9:,.1f} GB")
            st["topup_bytes"] = {"bytes": b, "on": day, "source": "daily one-day dry run"}
            b = self._dry(client, _sql("bigquery_token_census.sql"),
                          {"d0": (yday - pd.Timedelta(days=29)).date(), "d1": yday.date()}, spec)
            parts.append(f"token census (30 days) {b / 1e9:,.1f} GB")
            st["census_bytes"] = {"bytes": b, "on": day}
        except Exception as e:  # noqa: BLE001
            if is_reauth(e):
                raise ReauthNeeded(str(e)) from e
            out.fail(SOURCE, name, f"dry runs failed — {type(e).__name__}: {str(e)[:200]}", TIER)
            return
        st["dry_on"] = day
        out.skipped(SOURCE, name, "DRY RUNS (free): p2p " + "; ".join(parts) + f" — {len(toks)} token(s) in "
                                  f"the FT leg; dry runs ignore clustering, so the FT leg bills less", TIER)

    def _census(self, client, spec, st, out, name) -> bool:
        yday = (today() - pd.Timedelta(days=1)).normalize()
        rows = self._run(client, "token census", _sql("bigquery_token_census.sql"),
                         {"d0": (yday - pd.Timedelta(days=29)).date(), "d1": yday.date()}, spec, st, out, name)
        if not rows:
            return False
        px = self._price(spec, yday, [f"near:{r['token']}" for r in rows])
        valued = []
        for r in rows:
            p = px.get(f"near:{r['token']}")
            if p and p[0] is not None and p[1] is not None:
                valued.append((r["token"], float(Decimal(r["amount"]) / Decimal(10) ** int(p[1])) * p[0]))
        valued.sort(key=lambda t: -t[1])
        total = sum(v for _, v in valued) or 1.0
        keep, run = [], 0.0
        for t, v in valued:
            keep.append(t)
            run += v
            if run / total > float(spec["value_cover"]):
                break
        if WRAPPED not in keep:
            keep.append(WRAPPED)
        st["tokens"] = {"list": keep, "on": str(today().date()), "cover": run / total,
                        "shares": {t: v / total for t, v in valued[:len(keep)]}}
        out.skipped(SOURCE, name, f"token census: {len(keep)} token(s) carry {run / total:.1%} of 30 days' priced "
                                  f"ft_transfer value ({len(valued)} of {len(rows)} priced): "
                                  + ", ".join(f"{t} {v / total:.1%}" for t, v in valued[:len(keep)]), TIER)
        return True

    def _p2p(self, client, spec, st, out, name, unbounded: bool) -> None:
        tok = st.get("tokens") or {}
        stale = not tok.get("on") or (today() - pd.Timestamp(tok["on"])).days > int(spec["census_every_days"])
        if stale and not self._census(client, spec, st, out, name):
            return
        toks = st["tokens"]["list"]
        yday = (today() - pd.Timedelta(days=1)).normalize()
        floor = yday - pd.Timedelta(days=int(spec["days"]) - 1)
        if spec.get("backfill_floor"):                       # Jake, 2026-10-01: not across the break
            floor = max(floor, pd.Timestamp(spec["backfill_floor"]))
        held = sorted(st["days"])
        newest = pd.Timestamp(held[-1]) if held else None
        # 1. THE DAILY TOP-UP FIRST: every missing day newer than the newest held (just yesterday on a
        #    first run), so the series stays current whatever the backfill has left to do.
        top = [d for d in pd.date_range(yday, floor, freq="-1D")
               if str(d.date()) not in st["days"] and (newest is None or d > newest)]
        top = top[:1] if newest is None else top[:int(spec["chunk_days"])]
        # 2. THEN THE BACKFILL: the remaining missing days in chunks of <= chunk_days, newest first.
        missing = [d for d in pd.date_range(yday, floor, freq="-1D")
                   if str(d.date()) not in st["days"] and d not in top]
        chunks = [("top-up", top)] if top else []
        for d in missing:                                     # newest first, contiguous, <= chunk_days
            if chunks and chunks[-1][0] == "backfill" and (min(chunks[-1][1]) - d).days == 1 \
                    and len(chunks[-1][1]) < int(spec["chunk_days"]):
                chunks[-1][1].append(d)
            else:
                chunks.append(("backfill", [d]))
        limit = None if unbounded else int(spec["max_chunks_per_run"])
        done_backfill = 0
        n_run = len(chunks) if limit is None else min(len(chunks), 1 + limit)
        prog = Progress(f"near_bigquery p2p {name}", total=n_run or None, unit="chunk(s)", every_units=1,
                        checkpoint=lambda: self._save(st),
                        status=lambda: (f"{len(st['days'])} day(s) held (floor {floor.date()}); "
                                        f"{self._run_bytes / 1e9:,.1f} GB billed this run"))
        for kind, days in chunks:
            if kind == "backfill":
                if limit is not None and done_backfill >= limit:
                    break
                done_backfill += 1
            d0, d1 = min(days), max(days)
            rows = self._run(client, f"p2p {kind} {d0.date()}..{d1.date()}", _sql("bigquery_p2p_daily.sql"),
                             {"d0": d0.date(), "d1": d1.date(), "tokens": toks}, spec, st, out, name,
                             backfill=(kind == "backfill"), topup_days=len(days) if kind == "top-up" else 0)
            if rows is None:
                break
            for d in days:
                st["days"].setdefault(str(d.date()), {})
            for r in rows:
                st["days"].setdefault(str(pd.Timestamp(r["day"]).date()), {})[r["token"]] = str(r["amount"])
            prog.tick()                                      # saves st and prints the line
        if prog.done:
            prog.flush(final=True)

    def _import_csv(self, st, out, name) -> None:
        """FALLBACK: data/near/*.csv saved from the console (day, token, amount, n)."""
        root = self.csv_dir or Path(__file__).resolve().parent.parent / "data" / "near"
        added = 0
        for f in sorted(Path(root).glob("*.csv")) if Path(root).exists() else ():
            got: dict = {}
            with open(f, newline="", encoding="utf-8-sig") as fh:
                for r in csv.DictReader(fh):
                    try:
                        got.setdefault(str(pd.Timestamp(r["day"]).date()), {})[r["token"]] = str(r["amount"])
                    except (KeyError, ValueError):
                        continue
            for d, toks in got.items():
                if d not in st["days"]:
                    st["days"][d] = toks
                    added += 1
        if added:
            out.skipped(SOURCE, name, f"CSV fallback: {added} day(s) read from {root}", TIER)

    # --- valuation -----------------------------------------------------------------------------
    def _stored(self, name: str, metric: str):
        df = self.stored_long
        if df is None or getattr(df, "empty", True):
            return None
        g = df[(df["project"] == name) & (df["metric"] == metric)]
        if g.empty:
            return None
        return g.assign(date=pd.to_datetime(g["date"]).dt.normalize()).sort_values("date") \
            .drop_duplicates("date", keep="last").set_index("date")["value"].astype(float)

    def _price(self, spec, day: pd.Timestamp, coins: list[str]) -> dict:
        """{coin: (price, decimals)} at DefiLlama's price nearest midday UTC of `day`."""
        if self._prices is not None:
            return {c: self._prices.get((str(day.date()), c), (None, None)) for c in coins}
        got = {}
        ts = int((day + pd.Timedelta(hours=12)).timestamp())
        for i in range(0, len(coins), 50):
            chunk = coins[i:i + 50]
            j = self.http.get(f"{spec['price_api'].rstrip('/')}/prices/historical/{ts}/{','.join(chunk)}")
            for c, v in ((j or {}).get("coins") or {}).items():
                got[c] = (float(v["price"]) if v.get("price") is not None else None, v.get("decimals"))
        return got

    def _value(self, spec, st, out, name) -> None:
        near_px = self._stored(name, "price_usd")
        stored, unpriced, no_near, notes = [], 0, 0, []
        for key in sorted(st["days"]):
            toks = st["days"][key]
            day = pd.Timestamp(key)
            p_near = near_px.get(day) if near_px is not None else None
            if p_near is None or pd.isna(p_near):
                no_near += 1
                continue
            others = [t for t in toks if t not in (NATIVE, WRAPPED)]
            px = st["prices"].get(key)
            if px is None and others:
                try:
                    px = {c: list(v) for c, v in self._price(spec, day, [f"near:{t}" for t in others]).items()}
                except Exception as e:  # noqa: BLE001
                    notes.append(f"prices {key}: {str(e).split('?')[0]}")
                    break
                st["prices"][key] = px
            px = px or {}
            usd = 0.0
            try:
                usd += float(Decimal(toks.get(NATIVE, "0"))) * p_near
                usd += float(Decimal(toks.get(WRAPPED, "0")) / Decimal(10) ** 24) * p_near
                for t in others:
                    p = px.get(f"near:{t}") or (None, None)
                    if p[0] is None or p[1] is None:
                        unpriced += 1
                        continue
                    usd += float(Decimal(toks[t]) / Decimal(10) ** int(p[1])) * p[0]
            except (InvalidOperation, ValueError) as e:
                notes.append(f"{key}: {e}")
                continue
            stored.append((day, usd))
        self._save(st)
        if not stored:
            if st["days"]:
                out.skipped(SOURCE, name, f"{spec['p2p_metric']}: {len(st['days'])} day(s) held, none valued "
                                          f"({no_near} without a stored NEAR price_usd)", TIER)
            return
        frame = tidy(stored, name, spec["p2p_metric"], f"{SOURCE}:p2p[{LABEL}]", TIER)
        out.add(frame, SOURCE, name,
                f"{spec['p2p_metric']} = P2P transfers between non-contract accounts, native NEAR + NEP-141 "
                f"({LABEL}); {len(frame)} day(s) {stored[0][0].date()}..{stored[-1][0].date()}; NEAR at the "
                f"store's same-day price_usd, other tokens at DefiLlama's; {unpriced} token-day(s) unpriced "
                f"and left out; {no_near} day(s) waiting on a NEAR price" + (f"; {'; '.join(notes[:3])}" if notes else ""),
                TIER)
