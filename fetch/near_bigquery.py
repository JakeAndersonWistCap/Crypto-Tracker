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
import math
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
SKIP_ENV = "TOKEN_METRICS_SKIP_NEAR_BQ"      # set to 1 by windows/daily_run.ps1 when its BigQuery check failed
LOGIN_COMMANDS = ("gcloud auth application-default login",
                  "gcloud auth application-default set-quota-project near-data-510309")


def login_state() -> dict:
    """{'needed_on', 'ok_on', 'dates'} from the BigQuery state file — whether the LATEST attempt needed a Google login
    (Round G 1.7(a): the Run Log and the Gap Report say so first). Empty when there is no state."""
    import json
    from .logcache import LogCache
    try:
        st = json.loads((LogCache().root / "near-bq.json").read_text())
    except (OSError, ValueError):
        return {}
    return {"needed_on": st.get("login_needed_on"), "ok_on": st.get("login_ok_on"),
            "dates": list((st.get("reauth") or {}).get("dates") or [])}


def login_needed(state: dict | None = None) -> bool:
    s = login_state() if state is None else state
    need, ok = s.get("needed_on"), s.get("ok_on")
    return bool(need) and (not ok or str(need) > str(ok))
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


def hole_days(held, gap_from) -> list:
    """R23: the days inside [gap_from, the newest held day] that `held` (the state's days) does not hold, oldest first.
    [] when nothing is held or gap_from is not a date. Pure: no network, no state write."""
    try:
        lo = pd.Timestamp(str(gap_from)[:10])
    except (ValueError, TypeError):
        return []
    have = {str(d)[:10] for d in held}
    if not have:
        return []
    hi = pd.Timestamp(max(have))
    if lo > hi:
        return []
    return [d for d in pd.date_range(lo, hi, freq="D") if str(d.date()) not in have]


import threading as _threading
_STATE_LOCK = _threading.RLock()


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
        # P5-1.9 (Jake 2026-11-04): the byte counters exist from the start, so a dry run or a single query made outside
        # run() (settlement_extend.py dry-run / cex-sweep / backfill) can print the QUOTA line and bill a job
        self._run_bytes = self._backfill_bytes = 0

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
        # R22 (BQ one-shot): written whole or not at all - a run killed mid-write leaves the previous state, never a
        # truncated file that _load would read as an empty state (every held day lost)
        # R27: one writer at a time in this process (the NEAR and Ethereum P2P tiers share the file) and each write its
        # own temp name; the rename is retried (Windows: WinError 32 while another handle is still open) and a tier never
        # crashes over it - if every try fails the state stays as it was on disk and the next save writes it again
        import threading, time, uuid                                  # noqa: PLC0415, E401
        with _STATE_LOCK:
            tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:6]}.tmp")
            tmp.write_text(json.dumps(st))
            for i in range(8):
                try:
                    os.replace(tmp, p)
                    return
                except OSError:
                    time.sleep(0.15 * (i + 1))
            try:
                tmp.unlink()
            except OSError:
                pass

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
                # an array of dates is typed DATE so `block_date IN UNNEST(@days)` prunes partitions (G2 addendum A6)
                dated = bool(v) and all(hasattr(x, "isoformat") and not isinstance(x, str) for x in v)
                day = lambda x: str(x.date()) if hasattr(x, "hour") else str(x)  # noqa: E731 — Timestamp or date
                out.append(bq.ArrayQueryParameter(k, "DATE", [day(x) for x in v]) if dated
                           else bq.ArrayQueryParameter(k, "STRING", list(v)))
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
             backfill: bool = False, topup_days: int = 0, hard_cap: int | None = None, billed: bool = False):
        """Rows, or None with the reason logged. Dry run, budget, then the job capped at its dry run.
        A BACKFILL query may only spend what the month's top-up reserve leaves (_reserve).
        R12.5: `hard_cap` is the condition a one-off read was approved under (bytes): a dry run over it is NOT RUN,
        and the job's maximum_bytes_billed never exceeds it — BigQuery itself refuses anything larger.
        R22: `billed` is the one-shot billed history (bigquery_backfill.py, only once Jake's billing is confirmed on):
        the free tier's monthly budget and the top-up reserve do not hold it (its own 4 TB total does, through
        hard_cap), and its bytes go to st["ledger_billed"], not the free-tier ledger, so the daily reads keep their
        month."""
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
        if hard_cap is not None and est > int(hard_cap):
            out.skipped(SOURCE, name, f"{what}: dry run {est:,} bytes is over the {int(hard_cap):,}-byte condition it "
                                      f"was approved under — NOT RUN (a new decision for Jake)", TIER)
            return None
        if backfill and not billed:
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
        if not billed and used + est > int(spec["monthly_budget_bytes"]):
            out.skipped(SOURCE, name, f"{what}: dry run {est / 1e9:,.1f} GB would take {month} to "
                                      f"{(used + est) / 1e9:,.1f} GB, past the budget "
                                      f"{int(spec['monthly_budget_bytes']) / 1e9:,.0f} GB — NOT RUN; resumes "
                                      f"next month", TIER)
            return None
        cap = self.billing_cap(est) if hard_cap is None else min(self.billing_cap(est), int(hard_cap))
        cfg = self._bq.QueryJobConfig(use_query_cache=True, query_parameters=self._params(params),
                                      maximum_bytes_billed=cap)
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
        billed_mode = billed
        billed = int(getattr(job, "total_bytes_billed", None) or est)
        self._run_bytes += billed
        if backfill:
            self._backfill_bytes += billed
        if billed_mode:
            lb = st.setdefault("ledger_billed", {})
            lb[month] = int(lb.get(month, 0)) + billed
        else:
            st["ledger"][month] = used + billed
        self._save(st)
        if billed_mode:
            out.skipped(SOURCE, name, f"{what}: {len(rows)} row(s); {billed / 1e9:,.2f} GB billed (dry run "
                                      f"{est / 1e9:,.2f} GB, maximum_bytes_billed {cap:,}) — the one-shot billed "
                                      f"history, outside the free-tier ledger", TIER)
            return rows
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

    def value_only(self, projects: list[dict], out, days: set | None = None) -> None:
        """THE VALUATION STEP ALONE (P3-4 A4, the catch-up): the days already held in the state ('YYYY-MM-DD' in `days`
        only, when given), valued at the store's same-day price_usd exactly as run() ends (self._value) — no BigQuery
        client, no query. Used once the catch-up has stored the prices of missed days, so the days waiting on a price
        are valued without waiting for the next run. The state saved is always the whole state."""
        for p in projects:
            spec = p.get("near_bigquery")
            if not spec:
                continue
            st = self._load()
            st.setdefault("days", {})
            st.setdefault("prices", {})
            view = st if days is None else {**st, "days": {k: v for k, v in st["days"].items() if k in days}}
            save = self._save
            self._save = lambda _view, _st=st, _save=save: _save(_st)     # never the filtered view
            try:
                self._value(spec, view, out, p["name"])
            finally:
                self._save = save

    def _project(self, name: str, spec: dict, out, unbounded: bool) -> None:
        # SKIPPED BY THE DAILY RUN (Jake 2026-10-12 evening, item 16): its BigQuery check failed on a scheduled run, so
        # no query is attempted; every series resumes from its own last stored day on the next good run
        if os.environ.get(SKIP_ENV) == "1":
            out.skipped(SOURCE, name, "NEAR BigQuery skipped — log in: gcloud auth application-default login "
                                      "(the daily run's BigQuery check failed; each series resumes from its last "
                                      "stored day on the next good run)", TIER)
            st = self._load()
            st["login_needed_on"] = str(today().date())          # the Gap Report's P2 row reads it (Round G 1.7)
            self._save(st)
            return
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
                if ok.get("supply_flows") and spec.get("supply_flows"):
                    self._supply_flows(client, spec, st, out, name)
                    self._supply_flows_history(client, spec, st, out, name)
                self._dry_estimates(client, spec, st, out, name)
                if ok.get("activity_ex") and spec.get("activity_ex"):
                    self._activity_ex(client, spec, st, out, name)
                if ok.get("p2p"):
                    self._p2p(client, spec, st, out, name, unbounded)
                else:
                    out.skipped(SOURCE, name, "p2p NOT APPROVED — dry runs only (config Near.near_bigquery."
                                              "approved.p2p, Jake's decision on the logged estimates)", TIER)
            except ReauthNeeded as e:
                self._reauth(spec, st, out, name, e)
            else:
                # G11 A5a: a read that SUCCEEDED clears the login row — the P2 "NEAR BigQuery login" stayed open after
                # Jake logged in and the run read 2.6 GB, because a lapse and a success on the same date compared equal
                st["login_ok_on"] = str(today().date())
                st.pop("login_needed_on", None)
            out.skipped(SOURCE, name, self._quota_line(spec, st), TIER)
        if getattr(self, "quota_reminder", None):
            out.skipped(SOURCE, name, f"REMINDER: {self.quota_reminder}", TIER)
        self._save(st)
        self._value(spec, st, out, name)

    def _reauth(self, spec, st, out, name, e) -> None:
        """Expired ADC (probes6 4, Jake 2026-10-02): every BigQuery read stops for this run, the action goes
        to the run summary, and how often it has happened is kept (state `reauth`)."""
        day = str(today().date())
        st["login_needed_on"] = day
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

    def _supply_flows(self, client, spec, st, out, name) -> None:
        """NEAR's daily BURN and ISSUANCE from block-header total_supply (overnight 2026-10-06, B1;
        sql/near/bigquery_supply_flows.sql): within an epoch the supply only falls (the burn); an epoch's first block
        rises by the mint. The independent reference for the burn (ours: DefiLlama fees x the protocol's burned share)
        and the issuance (ours: declared 2.5%). The first read is a BACKFILL of `days` (top-up reserve rules); then
        each run reads only the days after the last one stored. ~3 columns x ~100k blocks/day: MBs a day."""
        fs = spec["supply_flows"]
        yday = (today() - pd.Timedelta(days=1)).normalize()
        through = st.get("supply_flows_through")
        if through and pd.Timestamp(through) >= yday:
            out.mark_current(SOURCE, name, fs["burn_metric"], "supply flows read through yesterday already", TIER)
            return
        d0 = (pd.Timestamp(through) + pd.Timedelta(days=1)) if through else yday - pd.Timedelta(days=int(fs["days"]) - 1)
        rows = self._run(client, f"supply flows {d0.date()}..{yday.date()}", _sql("bigquery_supply_flows.sql"),
                         {"d0": d0.date(), "d1": yday.date()}, spec, st, out, name, backfill=not through)
        if rows is None:
            return
        burn, iss, starts, blocks = [], [], 0, 0
        for r in rows:
            try:
                day = pd.Timestamp(r["day"])
                burn.append((day, float(r["burn_near"])))
                iss.append((day, float(r["issuance_near"])))
                starts += int(r.get("n_epoch_starts") or 0)
                blocks += int(r.get("n_blocks") or 0)
            except (TypeError, ValueError, KeyError):
                continue
        if not burn:
            out.fail(SOURCE, name, f"supply flows {d0.date()}..{yday.date()}: no rows (blocks table not up to date?)",
                     TIER)
            return
        st["supply_flows_through"] = str(burn[-1][0].date())
        src = f"{SOURCE}:blocks.total_supply"
        out.add(tidy(burn, name, fs["burn_metric"], src, TIER), SOURCE, name,
                f"{fs['burn_metric']} = within-epoch falls in block-header total_supply (+ each epoch-first block "
                f"filled with the day's mean burn per block): {len(burn)} day(s) {burn[0][0].date()}..{burn[-1][0].date()},"
                f" {sum(v for _, v in burn):,.0f} NEAR over {blocks:,} blocks", TIER)
        out.add(tidy(iss, name, fs["issuance_metric"], src, TIER), SOURCE, name,
                f"{fs['issuance_metric']} = the epoch-first blocks' rise in total_supply (+ their filled burn): "
                f"{starts} epoch start(s), {sum(v for _, v in iss):,.0f} NEAR minted over {len(iss)} day(s)", TIER)

    def _supply_flows_history(self, client, spec, st, out, name) -> None:
        """R10.B4 (Jake 2026-11-18): THE HEADER BURN AND MINT BEFORE THE FIRST STORED DAY — the same supply-flows SQL
        over [history.from, first stored day − 1], ONCE, as a backfill (the top-up reserve and the monthly budget apply;
        a held query is tried again on a later run). The dry run is logged on every run until it is done; the query
        itself runs only when config Near.near_bigquery.supply_flows.history.approved is true (Jake decides on the
        estimate). It moves the seam of near_chain_burn_tokens back (build_workbook._chain_burn_revenue_views)."""
        fs = spec["supply_flows"]
        h = fs.get("history") or {}
        if not h.get("from") or st.get("supply_flows_history_from") == h["from"]:
            return
        held = self._stored(name, fs["burn_metric"])
        if held is None or held.empty:
            return                                          # the regular backfill comes first
        d0, d1 = pd.Timestamp(h["from"]), held.index.min() - pd.Timedelta(days=1)
        if d0 > d1:
            st["supply_flows_history_from"] = h["from"]
            return
        what = f"supply flows HISTORY {d0.date()}..{d1.date()}"
        sql, params = _sql("bigquery_supply_flows.sql"), {"d0": d0.date(), "d1": d1.date()}
        if not h.get("approved"):
            try:
                est = self._dry(client, sql, params, spec)
            except Exception as e:  # noqa: BLE001 — a dry run that fails is said, never fatal
                if is_reauth(e):
                    raise ReauthNeeded(str(e)) from e
                out.skipped(SOURCE, name, f"{what}: dry run failed ({type(e).__name__}) — NOT RUN", TIER)
                return
            out.skipped(SOURCE, name, f"{what}: DRY RUN {est / 1e9:,.2f} GB — NOT APPROVED, not run (config Near."
                                      f"near_bigquery.supply_flows.history.approved; Jake's decision on this "
                                      f"estimate)", TIER)
            return
        # R12.5 (Jake 2026-11-19): approved on a dry run under a byte condition — never unbounded. An approval that
        # carries no condition is not run.
        if not h.get("max_bytes"):
            out.skipped(SOURCE, name, f"{what}: approved without a byte condition (config Near.near_bigquery."
                                      f"supply_flows.history.max_bytes) — NOT RUN", TIER)
            return
        rows = self._run(client, what, sql, params, spec, st, out, name, backfill=True, hard_cap=int(h["max_bytes"]))
        if rows is None:
            return
        burn, iss = [], []
        for r in rows:
            try:
                day = pd.Timestamp(r["day"])
                burn.append((day, float(r["burn_near"])))
                iss.append((day, float(r["issuance_near"])))
            except (TypeError, ValueError, KeyError):
                continue
        if not burn:
            out.fail(SOURCE, name, f"{what}: no rows", TIER)
            return
        src = f"{SOURCE}:blocks.total_supply"
        out.add(tidy(burn, name, fs["burn_metric"], src, TIER), SOURCE, name,
                f"{fs['burn_metric']} HISTORY: {len(burn)} day(s) {burn[0][0].date()}..{burn[-1][0].date()}, "
                f"{sum(v for _, v in burn):,.0f} NEAR — the header burn before its first stored day (R10.B4)", TIER)
        out.add(tidy(iss, name, fs["issuance_metric"], src, TIER), SOURCE, name,
                f"{fs['issuance_metric']} HISTORY: {len(iss)} day(s), {sum(v for _, v in iss):,.0f} NEAR minted", TIER)
        st["supply_flows_history_from"] = h["from"]

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
        # 1b. R23: THE HOLES - days inside [gap_from, the newest held day] the state never read - right after the
        #     top-up, newest first, in contiguous chunks of <= chunk_days. They go through the same _run as a backfill
        #     (the per-query cap, the month's budget AND the top-up reserve all hold) and are not counted against the
        #     older backfill's chunk limit.
        import config as _cfg                                   # noqa: PLC0415
        hcfg = getattr(_cfg, "NEAR_P2P_HISTORY", {}) or {}
        hole_set = {pd.Timestamp(d) for d in hole_days(held, hcfg.get("gap_from"))}
        # 2. THEN THE BACKFILL: the remaining missing days in chunks of <= chunk_days, newest first.
        missing = [d for d in pd.date_range(yday, floor, freq="-1D")
                   if str(d.date()) not in st["days"] and d not in top and d not in hole_set]
        chunks = [("top-up", top)] if top else []
        n_hole, n_seen = int(hcfg.get("hole_chunks_per_run", 3)), 0
        for d in sorted(hole_set, reverse=True):              # newest first, contiguous, <= chunk_days
            if chunks and chunks[-1][0] == "hole" and (min(chunks[-1][1]) - d).days == 1 \
                    and len(chunks[-1][1]) < int(spec["chunk_days"]):
                chunks[-1][1].append(d)
            elif n_seen < n_hole:
                n_seen += 1
                chunks.append(("hole", [d]))
        for d in missing:                                     # newest first, contiguous, <= chunk_days
            if chunks and chunks[-1][0] == "backfill" and (min(chunks[-1][1]) - d).days == 1 \
                    and len(chunks[-1][1]) < int(spec["chunk_days"]):
                chunks[-1][1].append(d)
            else:
                chunks.append(("backfill", [d]))
        limit = None if unbounded else int(spec["max_chunks_per_run"])
        done_backfill = 0
        n_run = len(chunks) if limit is None else min(len(chunks), 1 + n_seen + limit)
        prog = Progress(f"near_bigquery p2p {name}", total=n_run or None, unit="chunk(s)", every_units=1,
                        checkpoint=lambda: self._save(st),
                        status=lambda: (f"{len(st['days'])} day(s) held (floor {floor.date()}); "
                                        f"{self._run_bytes / 1e9:,.1f} GB billed this run"))
        for kind, days in chunks:
            if kind == "backfill":
                if limit is not None and done_backfill >= limit:
                    break
                done_backfill += 1
            if kind == "hole":
                # R23: a hole chunk is cut to the days the month's room holds at the estimate (newest first), so a
                # month with 60 GB left still reads 7 hole days instead of holding a 31-day chunk whole; the dry run
                # and the budget/reserve/per-query checks in _run still decide, so no cap moves
                reserve, _why = self._reserve(spec, st)
                room = int(spec["monthly_budget_bytes"]) - int(st["ledger"].get(str(today().date())[:7], 0)) \
                    - int(reserve or 0)
                fit = int(room // (float(hcfg.get("est_gb_per_day", 7.54)) * 1e9)) if reserve is not None else 0
                if fit < 1:
                    out.skipped(SOURCE, name, f"p2p hole {min(days).date()}..{max(days).date()}: HELD - the month's "
                                              f"room after the top-up reserve ({max(room, 0) / 1e9:,.1f} GB) is under "
                                              f"one day's estimate; resumes when the ledger allows", TIER)
                    continue
                days = sorted(days, reverse=True)[:min(fit, int(spec["chunk_days"]))]
            d0, d1 = min(days), max(days)
            rows = self._run(client, f"p2p {kind} {d0.date()}..{d1.date()}", _sql("bigquery_p2p_daily.sql"),
                             {"d0": d0.date(), "d1": d1.date(), "tokens": toks}, spec, st, out, name,
                             backfill=(kind in ("backfill", "hole")), topup_days=len(days) if kind == "top-up" else 0)
            if rows is None:
                break
            for d in days:
                st["days"].setdefault(str(d.date()), {})
            for r in rows:
                st["days"].setdefault(str(pd.Timestamp(r["day"]).date()), {})[r["token"]] = str(r["amount"])
            prog.tick()                                      # saves st and prints the line
        if prog.done:
            prog.flush(final=True)

    def history_chunk(self, client, spec, st, out, name, d0, d1, hard_cap: int, billed: bool = False) -> dict | None:
        """R18 (Jake 2026-11-24): ONE CHUNK OF P2P HISTORY BEFORE THE STORED RUN — the same SQL and token list as the
        daily read, over [d0, d1], as a BACKFILL (the month's budget, the per-query cap and the top-up reserve apply)
        under `hard_cap`, the bytes the month's approval allows. NEVER CALLED BY run(): only near_p2p_history.py's
        explicit command calls it, after config.NEAR_P2P_HISTORY_APPROVAL is checked.
        NO DAY TWICE: a chunk holding a day already held is refused before any query. NO PARTIAL DAY, NO PARTIAL
        CHUNK: the days are kept only if EVERY day of the chunk came back with its native-NEAR row and no row falls
        outside it; otherwise nothing is kept (the bytes are still on the ledger) and the chunk is asked again.
        Returns {"days", "billed", "d0", "d1"} or None with the reason logged. The days are valued and stored by the
        next token_metrics run (_value reads every held day), a day only where the store holds NEAR's price."""
        days = [str(d.date()) for d in pd.date_range(pd.Timestamp(d0), pd.Timestamp(d1), freq="D")]
        what = f"p2p HISTORY {days[0]}..{days[-1]}" if days else "p2p HISTORY"
        if not days:
            out.skipped(SOURCE, name, f"{what}: no day in the chunk — NOT RUN", TIER)
            return None
        twice = [d for d in days if d in st.get("days", {})]
        if twice:
            out.skipped(SOURCE, name, f"{what}: {len(twice)} day(s) are already held ({twice[0]}..{twice[-1]}) — NOT "
                                      f"RUN: a day is never read twice", TIER)
            return None
        toks = (st.get("tokens") or {}).get("list")
        if not toks:
            out.skipped(SOURCE, name, f"{what}: no token census in the state — NOT RUN (the daily run's P2P read "
                                      f"makes it; run `python token_metrics.py --project Near` first)", TIER)
            return None
        before = self._run_bytes
        rows = self._run(client, what, _sql("bigquery_p2p_daily.sql"),
                         {"d0": pd.Timestamp(d0).date(), "d1": pd.Timestamp(d1).date(), "tokens": toks}, spec, st, out,
                         name, backfill=True, hard_cap=int(hard_cap), billed=billed)
        oneshot = billed
        if rows is None:
            return None
        billed = self._run_bytes - before
        got: dict = {}
        for r in rows:
            got.setdefault(str(pd.Timestamp(r["day"]).date()), {})[r["token"]] = str(r["amount"])
        want = set(days)
        outside = sorted(k for k in got if k not in want)
        short = [d for d in days if NATIVE not in got.get(d, {})]
        if outside or short:
            out.fail(SOURCE, name, f"{what}: NOTHING KEPT — "
                     + (f"{len(short)} day(s) came back without a native-NEAR row ({', '.join(short[:5])}"
                        f"{'…' if len(short) > 5 else ''})" if short else "")
                     + ("; " if short and outside else "")
                     + (f"{len(outside)} row day(s) outside the chunk ({', '.join(outside[:5])})" if outside else "")
                     + f" — a chunk is kept whole or not at all; {billed / 1e9:,.2f} GB billed. Ask it again (a repeat "
                       f"within a day is served from BigQuery's cache).", TIER)
            return None
        for d in days:
            st["days"][d] = got[d]
        rec = {"month": str(today().date())[:7], "on": str(today().date()), "d0": days[0], "d1": days[-1],
               "days": len(days), "billed_bytes": int(billed)}
        if oneshot:
            rec["oneshot"] = True              # R22: not one of the month's approved chunks (near_p2p_history)
        st.setdefault("history", {}).setdefault("runs", []).append(rec)
        self._save(st)
        out.skipped(SOURCE, name, f"{what}: {len(days)} day(s) kept, {billed / 1e9:,.2f} GB billed; the next "
                                  f"`python token_metrics.py --project Near` values and stores them", TIER)
        return {"days": len(days), "billed": int(billed), "d0": days[0], "d1": days[-1]}

    def _activity_ex(self, client, spec, st, out, name) -> None:
        """NEAR transactions per day with Kai-Ching's signers counted apart (Jake's probes7, 2026-10-02).
        The daily top-up first (days newer than the newest held), then the rest of the year in ONE backfill
        query (held behind the top-up reserve like any backfill). Every held day is stored each run:
        tx_count_ex_kaiching = n - kaiching_n, tx_count_kaiching = kaiching_n."""
        a = spec["activity_ex"]
        held = st.setdefault("activity", {})
        yday = (today() - pd.Timedelta(days=1)).normalize()
        floor = yday - pd.Timedelta(days=int(spec["days"]) - 1)
        newest = pd.Timestamp(max(held)) if held else None
        top = [d for d in pd.date_range(floor, yday) if str(d.date()) not in held and (newest is None or d > newest)]
        if newest is None:
            top = top[-1:]                                   # first run: yesterday as the top-up
        rest = [d for d in pd.date_range(floor, yday) if str(d.date()) not in held and d not in top]
        for kind, days in (("top-up", top), ("backfill", rest)):
            if not days:
                continue
            d0, d1 = min(days), max(days)
            rows = self._run(client, f"activity ex-Kai-Ching {kind} {d0.date()}..{d1.date()}",
                             _sql("bigquery_activity_ex_kaiching.sql"), {"d0": d0.date(), "d1": d1.date()},
                             spec, st, out, name, backfill=(kind == "backfill"))
            if rows is None:
                break
            for r in rows:
                held[str(pd.Timestamp(r["day"]).date())] = [int(r["n"]), int(r["kaiching_n"])]
            self._save(st)
        if not held:
            return
        days = sorted(held)
        ex = [(pd.Timestamp(d), held[d][0] - held[d][1]) for d in days if pd.Timestamp(d) < today()]
        kc = [(pd.Timestamp(d), held[d][1]) for d in days if pd.Timestamp(d) < today()]
        src = f"{SOURCE}:transactions[signer not *{a['signer_suffix']}]"
        out.add(tidy(ex, name, a["metric"], src, TIER), SOURCE, name,
                f"{a['metric']}: {len(ex)} day(s) {days[0]}..{days[-1]}; latest {ex[-1][1]:,} "
                f"(Kai-Ching signed {kc[-1][1]:,} of {held[days[-1]][0]:,})", TIER)
        out.add(tidy(kc, name, a["excluded_metric"], f"{SOURCE}:transactions[signer *{a['signer_suffix']}]", TIER),
                SOURCE, name, f"{a['excluded_metric']}: {len(kc)} day(s)", TIER)

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

    def _decimals(self, spec, st, token):
        """A token's decimals: the declared map, else what a DefiLlama read cached for it (R26)."""
        d = (spec.get("token_decimals") or {}).get(token)
        if d is not None:
            return int(d)
        for px in (st.get("prices") or {}).values():
            v = (px or {}).get(f"near:{token}")
            if v and v[1] is not None:
                return int(v[1])
        return None

    def _fallback_price(self, spec, day, token, st, errs, near: bool = False):
        """(price, source) for NEAR (`near`) or a NEP-141 token on `day`, trying in order: DefiLlama (NEAR only here;
        tokens are asked in batch by the caller), the store's own price series, Coinbase Exchange's daily open, CoinGecko
        /coins/{id}/history. A source that failed is not asked again this run; every failure is appended to `errs`.
        (None, None) only when every source failed (R26: the day is then left unvalued with the reason)."""
        key = "near" if near else token
        chain = []
        if near:
            chain.append(("DefiLlama", lambda: self._llama_one(spec, day, "coingecko:near")))
        else:
            ref = (spec.get("token_store_price") or {}).get(token)
            if ref:
                chain.append(("store", lambda: self._stored_at(ref, day)))
        cb = ((spec.get("token_coinbase") or {}).get(token) if not near else (spec.get("near_coinbase") or "NEAR-USD"))
        if cb:
            chain.append(("Coinbase daily open", lambda: self._coinbase_open(cb, day)))
        cg = ((spec.get("token_coingecko") or {}).get(token) if not near else (spec.get("near_coingecko") or "near"))
        if cg:
            chain.append(("CoinGecko history", lambda: self._coingecko_hist(cg, day)))
        elif not near:
            # R27: a token with no hand-written map is looked up through CoinGecko by its NEAR contract address
            chain.append(("CoinGecko by contract", lambda: self._coingecko_by_contract(token, day)))
        mod = (spec.get("token_modelled") or {}).get(token)
        if mod:        # R27: MODELLED = NEAR's price x the token's published exchange rate (labelled in the source)
            chain.append(("MODELLED: NEAR price x " + mod["label"], lambda: self._modelled(spec, day, mod)))
        for label, fn in chain:
            if label in self._down:
                errs.append(f"{label}: failed earlier this run")
                continue
            try:
                v = fn()
            except Exception as e:  # noqa: BLE001
                self._down.add(label)
                if label.startswith("CoinGecko"):               # R29: one CoinGecko failure (a 429) ends all CoinGecko calls this run
                    self._down.update(("CoinGecko history", "CoinGecko by contract"))
                errs.append(f"{label}: {str(e).split('?')[0][:60]}")
                continue
            if v is not None and math.isfinite(float(v)) and float(v) > 0:
                return float(v), label
            errs.append(f"{label}: no price for {key} on {day.date()}")
        return None, None

    @staticmethod
    def _dec(raw):
        """R29: an amount string as a Decimal, tolerant of what BigQuery / a CSV can hand over (surrounding space, thousands
        separators, underscores, a trailing .0, scientific notation). Anything else (None, empty, NaN, inf, text) raises
        ValueError carrying the raw string: the day is then a NAMED hole, never valued without that token."""
        t = str(raw).strip().replace(",", "").replace("_", "").replace(" ", "")
        try:
            d = Decimal(t)
        except InvalidOperation:
            raise ValueError(f"raw string {str(raw)!r}") from None
        if not d.is_finite():
            raise ValueError(f"raw string {str(raw)!r}")
        return d

    def _coingecko_by_contract(self, token, day):
        """CoinGecko's id for a NEAR contract (/coins/near-protocol/contract/{address}), cached in the state file's
        sibling dict for the run, then its history price on `day`."""
        ids = self.__dict__.setdefault("_cg_ids", {})
        if token not in ids:
            j = self._cg_get(f"https://api.coingecko.com/api/v3/coins/near-protocol/contract/{token}")
            ids[token] = (j or {}).get("id")
        return self._coingecko_hist(ids[token], day) if ids[token] else None

    def _modelled(self, spec, day, mod):
        """NEAR's price x the published exchange rate (MODELLED): a rate series stored for the token, else the fixed
        rate declared with its date; never a guess without a declared source."""
        near = self._stored("Near", "price_usd")
        p = None if near is None else near.get(day)
        return None if p is None or mod.get("rate") is None else float(p) * float(mod["rate"])

    def _llama_one(self, spec, day, coin):
        got = self._price(spec, day, [coin])
        v = got.get(coin)
        return v[0] if v else None

    def _stored_at(self, ref, day):
        s = self._stored(ref[0], ref[1])
        return None if s is None else s.get(day)

    def _coinbase_open(self, product, day):
        a, b = day.strftime("%Y-%m-%dT00:00:00Z"), (day + pd.Timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
        j = self.http.get(f"https://api.exchange.coinbase.com/products/{product}/candles?granularity=86400&start={a}&end={b}")
        for c in (j if isinstance(j, list) else []):       # [time, low, high, open, close, volume]
            if int(c[0]) == int(day.timestamp()):
                return c[3]
        return None

    CG_BUDGET_S = 90.0

    def _cg_get(self, url):
        """R29: CoinGecko through its own client that never sleeps on a 429 (retries 0) and a 90 s total budget per run:
        over budget or any failure raises, and _fallback_price stops asking CoinGecko for the rest of the run."""
        import time as _t                                             # noqa: PLC0415
        if getattr(self, "_cg_t0", None) is None:
            self._cg_t0, self._cg_spent = _t.monotonic(), 0.0
        if self._cg_spent >= self.CG_BUDGET_S:
            raise RuntimeError("CoinGecko budget (90 s a run) spent")
        if getattr(self, "_cg_http", None) is None:
            from .base import Http                                    # noqa: PLC0415
            self._cg_http = Http(min_interval=1.2, retries=0)
        t = _t.monotonic()
        try:
            return self._cg_http.get(url)
        finally:
            self._cg_spent += _t.monotonic() - t

    def _coingecko_hist(self, cid, day):
        j = self._cg_get(f"https://api.coingecko.com/api/v3/coins/{cid}/history?date={day.strftime('%d-%m-%Y')}&localization=false")
        return (((j or {}).get("market_data") or {}).get("current_price") or {}).get("usd")

    def _value(self, spec, st, out, name) -> None:
        """R25 FIX 4: every held day is valued here with LOCAL arithmetic (native NEAR and wNEAR at the store's same-day
        price_usd; a token at its cached DefiLlama price). A day is left out only with a STATED reason, kept in the state
        (`unvalued`, read by `near_p2p_history.py gaps`): no NEAR price, no native-NEAR row (unknown, never 0), a figure
        that is not finite or beyond the metric's sanity bound. A DefiLlama price lookup that FAILS no longer stops the
        loop (it used to `break`: one failure, e.g. a rate limit after ~50 uncached days, left every LATER day unvalued
        on every run): the lookup is not repeated this run, the day is valued without the tokens it could not price
        (counted as unpriced, NOT cached, so the next run that reaches DefiLlama re-values it and the store follows)."""
        near_px = self._stored(name, "price_usd")
        stored, unpriced, no_near, notes, why = [], 0, 0, [], {}
        no_price_days = []
        self._down = set()                    # sources that failed this run: not asked again (R26)
        self._cg_t0, self._cg_spent = None, 0.0
        st.setdefault("price_src", {})
        import config                                          # noqa: PLC0415
        hi = (config.METRICS.get(spec["p2p_metric"]) or {}).get("sanity_max")
        for key in sorted(st["days"]):
            toks = st["days"][key]
            day = pd.Timestamp(key)
            if NATIVE not in toks:
                why[key] = "no native-NEAR row held for the day (unknown, not valued as 0)"
                continue
            srcs, errs = {}, []
            p_near = near_px.get(day) if near_px is not None else None
            if p_near is not None and not pd.isna(p_near):
                srcs["near"] = "store price_usd"
            else:
                cached = (st.setdefault("near_px", {}).get(key) or [None, None])
                if cached[0] is not None:
                    p_near, srcs["near"] = cached[0], cached[1]
                else:
                    p_near, srcs["near"] = self._fallback_price(spec, day, "near", None, errs, near=True)
                    if p_near is not None:
                        st["near_px"][key] = [p_near, srcs["near"]]
            if p_near is None:
                no_near += 1
                why[key] = "no NEAR price from any source (" + "; ".join(errs[:4]) + ")"
                continue
            bad = None
            for tk, raw in toks.items():
                try:
                    self._dec(raw)
                except ValueError as e:
                    bad = (tk, str(e))
                    break
            if bad:
                why[key] = (f"unreadable amount: token {bad[0]} {bad[1]} - the day is NOT valued (never stored without "
                            f"that token)")
                continue
            others = [t for t in toks if t not in (NATIVE, WRAPPED)]
            px = st["prices"].get(key) or {}
            px = {c: list(v) for c, v in px.items()}
            have = st["price_src"].get(key) or {}
            for c in px:
                srcs[c] = have.get(c, "DefiLlama")
            need = [t for t in others if (px.get(f"near:{t}") or [None, None])[0] is None]
            if need and "defillama" not in self._down:
                try:
                    got = self._price(spec, day, [f"near:{t}" for t in need])
                    for c, v in got.items():
                        if v and v[0] is not None and v[1] is not None:
                            px[c] = [v[0], v[1]]
                            srcs[c] = "DefiLlama"
                except Exception as e:  # noqa: BLE001
                    self._down.add("defillama")
                    notes.append(f"prices {key}: {str(e).split('?')[0]} (DefiLlama not asked again this run; "
                                 f"falling back to the store, Coinbase, CoinGecko)")
                    errs.append("DefiLlama: " + str(e).split("?")[0][:60])
            for t in need:
                c = f"near:{t}"
                if (px.get(c) or [None, None])[0] is not None:
                    continue
                price, srcname = self._fallback_price(spec, day, t, st, errs)
                dec = self._decimals(spec, st, t)
                if price is not None and dec is not None:
                    px[c] = [price, dec]
                    srcs[c] = srcname
            st["prices"][key] = px                  # R29: every price found is kept, a day is never priced twice
            st["price_src"][key] = srcs
            missing = [t for t in others if (px.get(f"near:{t}") or [None, None])[0] is None]
            if missing:
                why[key] = (f"no price for {len(missing)} token(s) ({missing[0]}...) from any source: DefiLlama, the "
                            f"store, Coinbase, CoinGecko (" + "; ".join(errs[:4]) + ") - NOT stored understated")
                no_price_days.append(key)
                continue
            st["prices"][key] = px
            st["price_src"][key] = srcs
            usd = 0.0
            try:
                usd += float(self._dec(toks.get(NATIVE, "0"))) * p_near
                usd += float(self._dec(toks.get(WRAPPED, "0")) / Decimal(10) ** 24) * p_near
                for t in others:
                    p = px[f"near:{t}"]
                    usd += float(self._dec(toks[t]) / Decimal(10) ** int(p[1])) * p[0]
            except (InvalidOperation, ValueError, TypeError, OverflowError) as e:
                notes.append(f"{key}: {e}")
                why[key] = f"native amount unreadable ({e})"
                continue
            if not math.isfinite(usd) or (hi is not None and usd > float(hi)):
                why[key] = f"value {usd:,.4g} is beyond the sanity bound - NOT stored"
                continue
            stored.append((day, usd))
        st["unvalued"] = why
        self._save(st)
        if no_price_days:
            notes.append(f"{len(no_price_days)} day(s) left unvalued: a token had no price from any source "
                         f"({no_price_days[0]}..{no_price_days[-1]}); retried by the next run")
        if why:
            ks = sorted(why)
            notes.append(f"{len(why)} day(s) NOT valued: " + "; ".join(f"{k} {why[k]}" for k in ks[:3])
                         + ("..." if len(ks) > 3 else ""))
        if not stored:
            if st["days"]:
                out.skipped(SOURCE, name, f"{spec['p2p_metric']}: {len(st['days'])} day(s) held, none valued "
                                          f"({no_near} without a stored NEAR price_usd)"
                                          + (f"; {'; '.join(notes[:3])}" if notes else ""), TIER)
            return
        frame = tidy(stored, name, spec["p2p_metric"], f"{SOURCE}:p2p[{LABEL}]", TIER)
        out.add(frame, SOURCE, name,
                f"{spec['p2p_metric']} = P2P transfers between non-contract accounts, native NEAR + NEP-141 "
                f"({LABEL}); {len(frame)} day(s) {stored[0][0].date()}..{stored[-1][0].date()}; NEAR at the "
                f"store's same-day price_usd, other tokens at DefiLlama's; {unpriced} token-day(s) unpriced "
                f"and left out; {no_near} day(s) waiting on a NEAR price" + (f"; {'; '.join(notes[:3])}" if notes else ""),
                TIER)
