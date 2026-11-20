#!/usr/bin/env python3
"""
adc_check.py — can the NEAR BigQuery reads run? Jake's user Application Default Credentials, tested with a REAL
one-row query (Jake 2026-10-12 evening, item 16: his ADC keeps expiring and service-account keys are blocked by his
organisation's policy, so the daily run checks — and, run by hand, repairs — the login before any step).

    python adc_check.py        exit 0 = good
                               2 = login expired            3 = no credentials / library
                               4 = wrong ADC quota project  7 = permission denied      8 = the query failed otherwise
                               9 = timed out (never waits for a login: see guarded() and bounded())

The query reads one row of yesterday's partition of the public NEAR blocks table, billed bytes capped at 100 MB
(BigQuery bills a partition-pruned read at its 10 MB minimum). On failure it prints ONE line,
"BIGQUERY CHECK: FAILED (exit N) - <why>. FIX: <exact command>" (R15: one line; it was two, the second "FIX: ...");
windows/daily_run.ps1 runs that fix itself only when it runs interactively by hand, and otherwise skips the BigQuery
reads with the fix at the top of the summary. Never prints a token.

R15 (Jake 2026-11-20: the 11-19 scheduled run stayed open at "BigQuery check"): EVERY WAIT IS BOUNDED, IN THREE LAYERS.
  1. the API calls carry their own deadline (QUERY_TIMEOUT_S: the query, the job wait, the retry deadline), and the
     credentials are loaded from the ADC FILE (google.auth.load_credentials_from_file) - not google.auth.default(),
     which on a user login with no project runs `gcloud config get project` as a subprocess with no timeout, and with no
     file at all probes the GCE metadata server;
  2. google-auth's reauthentication never prompts: reauth.is_interactive() answers False and getpass refuses, so an
     expired login is exit 2 at once (on Windows sys.stdin.isatty() is True even for NUL, so "no stdin" alone did not
     stop the password prompt, which reads the console directly);
  3. the whole check runs in a worker thread with a wall-clock limit (bounded()): past it the child prints one line and
     exits 9 by os._exit, whatever the thread is waiting on; and the parent (guarded()) kills the child BY PID (its tree
     on Windows) at its own limit, never waiting on a pipe (the output goes to a temp file).
"""
from __future__ import annotations

import os
import sys
import threading

import config
from fetch.near_bigquery import REAUTH_ACTION, is_reauth

QUERY = ("SELECT block_height FROM `bigquery-public-data.crypto_near_mainnet_us.blocks` "
         "WHERE block_date = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY) LIMIT 1")

EXIT_EXPIRED = 2
EXIT_NO_CREDS = 3
EXIT_TIMEOUT = 9
ADC_TIMEOUT_S = 90            # the parent's wall-clock limit on the child (TOKEN_METRICS_ADC_TIMEOUT overrides)
QUERY_TIMEOUT_S = 30          # each BigQuery API call and its retry deadline
INNER_MARGIN_S = 10           # the child's own limit is the parent's minus this, so it exits itself first

# Set before google-auth / gcloud can ask anything (R15 (b)): no prompt, no browser.
NO_PROMPT_ENV = {"CLOUDSDK_CORE_DISABLE_PROMPTS": "1", "NO_GCE_CHECK": "True", "PIP_NO_INPUT": "1",
                 "GIT_TERMINAL_PROMPT": "0"}


def _project() -> str:
    return (config.PROJECT_BY_NAME.get("Near") or {}).get("near_bigquery", {}).get("project", "near-data-510309")


def _login() -> str:
    return (f"gcloud auth application-default login; "
            f"gcloud auth application-default set-quota-project {_project()}")


def fail(code: int, why: str, fix: str) -> int:
    # ONE line (R15): the code, why, and the exact fix
    print(f"BIGQUERY CHECK: FAILED (exit {code}) - {why}. FIX: {fix}", flush=True)
    return code


def no_prompts() -> None:
    """google-auth's reauthentication and every getpass() refuse at once instead of waiting on the console."""
    for k, v in NO_PROMPT_ENV.items():
        os.environ.setdefault(k, v)

    def _refuse(*_a, **_k):
        raise EOFError("adc_check: no interactive prompt (scheduled / non-interactive check)")
    import getpass
    getpass.getpass = _refuse
    try:
        from google.oauth2 import challenges, reauth
        reauth.is_interactive = lambda: False
        challenges.get_user_password = _refuse
    except ImportError:
        pass


def adc_path() -> str:
    """The ADC file google-auth would read (the env override, else gcloud's well-known path) - computed, no subprocess."""
    env = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if env:
        return env
    cfg = os.environ.get("CLOUDSDK_CONFIG")
    if not cfg:
        if os.name == "nt":
            cfg = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "gcloud")
        else:
            cfg = os.path.join(os.path.expanduser("~"), ".config", "gcloud")
    return os.path.join(cfg, "application_default_credentials.json")


def main() -> int:
    project = _project()
    login = _login()
    no_prompts()
    try:
        import google.auth
        from google.auth.exceptions import DefaultCredentialsError
        from google.cloud import bigquery
    except ImportError:
        return fail(EXIT_NO_CREDS, "google-cloud-bigquery is not installed", "pip install google-cloud-bigquery")
    path = adc_path()
    if not os.path.isfile(path):
        return fail(EXIT_NO_CREDS, "no Application Default Credentials (no login on this machine)", login)
    try:
        creds, _ = google.auth.load_credentials_from_file(path, scopes=["https://www.googleapis.com/auth/bigquery"])
    except (DefaultCredentialsError, ValueError, OSError) as e:
        return fail(EXIT_NO_CREDS, f"the ADC file is unreadable ({type(e).__name__})", login)
    quota = getattr(creds, "quota_project_id", None)
    if quota != project:
        return fail(4, f"the ADC quota project is {quota or 'NOT SET'}, not {project}",
                    f"gcloud auth application-default set-quota-project {project}")
    try:
        client = bigquery.Client(project=project, credentials=creds)
        retry = bigquery.DEFAULT_RETRY.with_deadline(QUERY_TIMEOUT_S)
        job = client.query(QUERY, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=100 * 1024 ** 2,
                                                                    use_query_cache=False),
                           retry=retry, timeout=QUERY_TIMEOUT_S)
        rows = list(job.result(timeout=QUERY_TIMEOUT_S, retry=retry))
    except Exception as e:  # noqa: BLE001 — classify; never echo a credential
        text = str(e)
        if is_reauth(e) or isinstance(e, EOFError):
            return fail(EXIT_EXPIRED, f"the login expired (\"Reauthentication is needed\": {REAUTH_ACTION})", login)
        if "403" in text or "Access Denied" in text or "PERMISSION_DENIED" in text:
            return fail(7, f"permission denied ({type(e).__name__}: {text[:160]})",
                        f"ask for roles/bigquery.jobUser on {project} for the logged-in user")
        if isinstance(e, TimeoutError) or "Timeout" in type(e).__name__ or "timed out" in text.lower():
            return fail(EXIT_TIMEOUT, f"the one-row query did not answer in {QUERY_TIMEOUT_S}s "
                                      f"- NEAR BigQuery is skipped this run, everything else carries on", login)
        return fail(8, f"the one-row query failed ({type(e).__name__}: {text[:160]})",
                    "check the network, then: python adc_check.py")
    billed = getattr(job, "total_bytes_billed", None)
    print(f"BIGQUERY CHECK: OK - user ADC, quota project {project}: one-row query returned {len(rows)} row(s)"
          + (f", {int(billed) / 1e6:,.0f} MB billed" if billed is not None else ""), flush=True)
    return 0


def bounded(fn, limit_s: float, _exit=os._exit) -> int:
    """Run fn() in a daemon thread; past limit_s print ONE line and exit EXIT_TIMEOUT at once (os._exit: a thread blocked
    in a socket, a subprocess or a prompt cannot hold the process). Returns fn()'s code when it finishes in time."""
    box: list = []

    def _run():
        try:
            box.append(fn())
        except BaseException as e:  # noqa: BLE001 — any crash is a failed check, never a hang
            box.append(fail(8, f"the check crashed ({type(e).__name__})", "python adc_check.py"))
    t = threading.Thread(target=_run, name="adc_check", daemon=True)
    t.start()
    t.join(limit_s)
    if t.is_alive() or not box:
        fail(EXIT_TIMEOUT, f"the check did not finish in {limit_s:.0f}s (a login prompt or the network) - NEAR "
                           f"BigQuery is skipped this run, everything else carries on", _login())
        try:
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass
        _exit(EXIT_TIMEOUT)
        return EXIT_TIMEOUT                       # only reached when _exit is replaced (tests)
    return box[0]


# NEVER WAITS FOR A LOGIN (Round G 1.7(b), Jake's run_2026-10-18.log: the daily run stopped at "=== BigQuery check" and
# went no further). The check runs in a child process with NO stdin and a hard timeout: past ADC_TIMEOUT_S it is killed
# BY PID and reported as exit 9 — NEAR BigQuery is skipped for this run and everything else carries on.
# R15: the child's output goes to a temp FILE, not a pipe (subprocess.run's timeout on Windows kills the child and then
# waits, with no limit, for the pipe to close - a grandchild holding it held the check for ever); the kill takes the
# child's tree on Windows (taskkill /PID <pid> /T /F) and every wait after it is bounded.
def _kill_pid(pid: int) -> None:
    import signal
    import subprocess
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        else:
            os.kill(pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001 — already gone
        pass


def guarded(timeout_s: float | None = None, argv0: str | None = None) -> int:
    import subprocess
    import tempfile
    timeout_s = float(os.environ.get("TOKEN_METRICS_ADC_TIMEOUT", ADC_TIMEOUT_S) if timeout_s is None else timeout_s)
    env = dict(os.environ, ADC_CHECK_CHILD="1", ADC_CHECK_LIMIT=str(max(1.0, timeout_s - INNER_MARGIN_S)))
    env.update({k: v for k, v in NO_PROMPT_ENV.items() if k not in os.environ})
    with tempfile.TemporaryFile() as out:
        p = subprocess.Popen([sys.executable, argv0 or __file__], stdin=subprocess.DEVNULL, stdout=out,
                             stderr=subprocess.STDOUT, env=env)
        try:
            code = p.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_pid(p.pid)
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            code = None
        out.seek(0)
        text = out.read().decode("utf-8", errors="replace")
    if text:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    if code is None:
        return fail(EXIT_TIMEOUT, f"the check did not finish in {timeout_s:.0f}s (a login prompt or the network) - "
                                  f"killed PID {p.pid}; NEAR BigQuery is skipped this run, everything else carries on",
                    _login())
    return code


if __name__ == "__main__":
    import progress                                    # stdout line-buffered when piped (2026-10-11)
    progress.line_buffered()
    if os.environ.get("ADC_CHECK_CHILD") == "1":
        limit = float(os.environ.get("ADC_CHECK_LIMIT", ADC_TIMEOUT_S - INNER_MARGIN_S))
        rc = bounded(main, limit)
        sys.stdout.flush()
        os._exit(rc)                                   # os._exit: a stuck daemon thread cannot delay the exit
    sys.exit(guarded())
