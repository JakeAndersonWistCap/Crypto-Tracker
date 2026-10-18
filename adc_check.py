#!/usr/bin/env python3
"""
adc_check.py — can the NEAR BigQuery reads run? Jake's user Application Default Credentials, tested with a REAL
one-row query (Jake 2026-10-12 evening, item 16: his ADC keeps expiring and service-account keys are blocked by his
organisation's policy, so the daily run checks — and, run by hand, repairs — the login before any step).

    python adc_check.py        exit 0 = good
                               2 = login expired            3 = no credentials / library
                               4 = wrong ADC quota project  7 = permission denied      8 = the query failed otherwise
                               9 = timed out (never waits for a login: see guarded())

The query reads one row of yesterday's partition of the public NEAR blocks table, billed bytes capped at 100 MB
(BigQuery bills a partition-pruned read at its 10 MB minimum). On failure the last line printed is
"FIX: <exact command>"; windows/daily_run.ps1 runs that fix itself when it runs interactively, and otherwise skips the
BigQuery reads with the fix at the top of the summary. Never prints a token.
"""
from __future__ import annotations

import sys

import config
from fetch.near_bigquery import REAUTH_ACTION, is_reauth

QUERY = ("SELECT block_height FROM `bigquery-public-data.crypto_near_mainnet_us.blocks` "
         "WHERE block_date = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY) LIMIT 1")


def fail(code: int, why: str, fix: str) -> int:
    print(f"BIGQUERY CHECK: FAILED - {why}")
    print(f"FIX: {fix}")
    return code


def main() -> int:
    project = (config.PROJECT_BY_NAME.get("Near") or {}).get("near_bigquery", {}).get("project", "near-data-510309")
    login = f"gcloud auth application-default login; gcloud auth application-default set-quota-project {project}"
    try:
        import google.auth
        from google.auth.exceptions import DefaultCredentialsError
        from google.cloud import bigquery
    except ImportError:
        return fail(3, "google-cloud-bigquery is not installed", "pip install google-cloud-bigquery")
    try:
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/bigquery"])
    except DefaultCredentialsError:
        return fail(3, "no Application Default Credentials", login)
    quota = getattr(creds, "quota_project_id", None)
    if quota != project:
        return fail(4, f"the ADC quota project is {quota or 'NOT SET'}, not {project}",
                    f"gcloud auth application-default set-quota-project {project}")
    try:
        client = bigquery.Client(project=project, credentials=creds)
        job = client.query(QUERY, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=100 * 1024 ** 2,
                                                                    use_query_cache=False))
        rows = list(job.result())
    except Exception as e:  # noqa: BLE001 — classify; never echo a credential
        text = str(e)
        if is_reauth(e):
            return fail(2, f"the login expired (\"Reauthentication is needed\": {REAUTH_ACTION})", login)
        if "403" in text or "Access Denied" in text or "PERMISSION_DENIED" in text:
            return fail(7, f"permission denied ({type(e).__name__}: {text[:160]})",
                        f"ask for roles/bigquery.jobUser on {project} for the logged-in user")
        return fail(8, f"the one-row query failed ({type(e).__name__}: {text[:160]})",
                    "check the network, then: python adc_check.py")
    billed = getattr(job, "total_bytes_billed", None)
    print(f"BIGQUERY CHECK: OK - user ADC, quota project {project}: one-row query returned {len(rows)} row(s)"
          + (f", {int(billed) / 1e6:,.0f} MB billed" if billed is not None else ""))
    return 0


# NEVER WAITS FOR A LOGIN (Round G 1.7(b), Jake's run_2026-10-18.log: the daily run stopped at "=== BigQuery check" and
# went no further). With an expired login google-auth's reauthentication asks for a password ON THE TERMINAL when stdin
# is one — and under the daily runner the prompt is captured into the log, so the run waits for ever. The check now runs
# in a child process with NO stdin (google-auth then refuses the interactive challenge at once and the login reads as
# expired, exit 2) and a hard timeout: past ADC_TIMEOUT_S it is killed and reported as exit 9 — NEAR BigQuery is
# skipped for this run and everything else carries on.
ADC_TIMEOUT_S = 90


def guarded(timeout_s: float | None = None, argv0: str | None = None) -> int:
    import os
    import subprocess
    timeout_s = float(os.environ.get("TOKEN_METRICS_ADC_TIMEOUT", ADC_TIMEOUT_S) if timeout_s is None else timeout_s)
    env = dict(os.environ, ADC_CHECK_CHILD="1")
    try:
        r = subprocess.run([sys.executable, argv0 or __file__], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, env=env, timeout=timeout_s, text=True, encoding="utf-8",
                           errors="replace")
    except subprocess.TimeoutExpired:
        project = (config.PROJECT_BY_NAME.get("Near") or {}).get("near_bigquery", {}).get("project", "near-data-510309")
        return fail(9, f"the check did not finish in {timeout_s:.0f}s (a login prompt or the network) — NEAR BigQuery "
                       f"is skipped this run, everything else carries on",
                    f"gcloud auth application-default login; gcloud auth application-default set-quota-project {project}")
    sys.stdout.write(r.stdout or "")
    return r.returncode


if __name__ == "__main__":
    import progress                                    # stdout line-buffered when piped (2026-10-11)
    progress.line_buffered()
    import os
    sys.exit(main() if os.environ.get("ADC_CHECK_CHILD") == "1" else guarded())
