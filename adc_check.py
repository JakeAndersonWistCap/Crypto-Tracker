#!/usr/bin/env python3
"""
adc_check.py — is Jake's Google login (Application Default Credentials) still good for the NEAR BigQuery reads?

    python adc_check.py        exit 0 = good; 2 = re-login needed; 3 = no credentials / library; 4 = wrong quota project

Refreshes the ADC access token and nothing else: NO BigQuery query, no quota spent. The daily task
(windows/daily_run.ps1) runs it first and raises a desktop notification on any non-zero exit, so an
expired login (Jake's organisation forces re-auth about daily) is fixed before the run, not found after it.
Prints the commands to paste; never prints a token.
"""
from __future__ import annotations

import sys

import config
from fetch.near_bigquery import REAUTH_ACTION, is_reauth


def main() -> int:
    project = (config.PROJECT_BY_NAME.get("Near") or {}).get("near_bigquery", {}).get("project", "near-data-510309")
    fix = (f"    gcloud auth application-default login\n"
           f"    gcloud auth application-default set-quota-project {project}")
    try:
        import google.auth
        from google.auth.exceptions import DefaultCredentialsError
        from google.auth.transport.requests import Request
    except ImportError:
        print("ADC CHECK: google-auth is not installed (pip install google-cloud-bigquery) - NEAR BigQuery reads "
              "will be skipped")
        return 3
    try:
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/bigquery"])
    except DefaultCredentialsError:
        print(f"ADC CHECK: NO Application Default Credentials - paste:\n{fix}")
        return 3
    try:
        creds.refresh(Request())
    except Exception as e:  # noqa: BLE001 — RefreshError and transport errors alike
        if is_reauth(e):
            print(f"ADC CHECK: LOGIN EXPIRED ({REAUTH_ACTION}) - paste:\n{fix}")
            return 2
        print(f"ADC CHECK: token refresh failed ({type(e).__name__}) - network? NEAR BigQuery reads may fail")
        return 3
    quota = getattr(creds, "quota_project_id", None)
    if quota != project:
        print(f"ADC CHECK: quota project is {quota or 'NOT SET'}, not {project} - paste:\n"
              f"    gcloud auth application-default set-quota-project {project}")
        return 4
    print(f"ADC CHECK: OK (quota project {project})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
