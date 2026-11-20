"""Round 15 (Jake 2026-11-20): the daily run never hangs, never prompts, never blocks the next day.

Jake's laptop logs: the scheduled runs of 2026-11-11 and 11-18 were KILLED during token_metrics; the 11-19 run started
08:41 and stayed open at "BigQuery check" (daily_run_state.json: "open": "BigQuery check"); no 11-20 log at all.
SPEC "### Round 15 — the daily run" has one row per change; RUNBOOK 11fd has Jake's laptop steps.

These tests check behaviour: adc_check.py returns its timeout code inside its limit when the BigQuery call blocks, an
expired login is exit 2 without any prompt, an absent login is exit 3 without google.auth.default(); the PowerShell is
read for the paths it can take (no PowerShell runs here).
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PS = (ROOT / "windows" / "daily_run.ps1").read_text(encoding="utf-8")
REG = (ROOT / "windows" / "register_task.ps1").read_text(encoding="utf-8")


# ============================================================================================ (a) adc_check never hangs
FAKE_GOOGLE = {
    "google/__init__.py": "",
    "google/auth/__init__.py": """
import json, os
from google.auth.exceptions import DefaultCredentialsError
class _Creds:
    quota_project_id = os.environ.get("FAKE_QUOTA", "near-data-510309")
def load_credentials_from_file(path, scopes=None, **kw):
    json.load(open(path))
    return _Creds(), None
def default(*a, **k):
    # google.auth.default() runs `gcloud config get project` on a user login: adc_check must never call it
    open(os.environ["FAKE_DEFAULT_CALLED"], "w").write("called")
    import time; time.sleep(600)
""",
    "google/auth/exceptions.py": """
class DefaultCredentialsError(Exception): pass
class RefreshError(Exception): pass
class ReauthFailError(RefreshError): pass
""",
    "google/oauth2/__init__.py": "",
    "google/oauth2/reauth.py": """
import sys
def is_interactive():
    return True          # as on Windows, where sys.stdin.isatty() is True even for NUL
""",
    "google/oauth2/challenges.py": """
import getpass
def get_user_password(text):
    return getpass.getpass(text)
""",
    "google/cloud/__init__.py": "",
    "google/cloud/bigquery.py": """
import os, time
class _Retry:
    def with_deadline(self, s):
        return self
DEFAULT_RETRY = _Retry()
class QueryJobConfig:
    def __init__(self, **kw): pass
class _Job:
    total_bytes_billed = 10485760
    def result(self, timeout=None, retry=None):
        return [1]
class Client:
    def __init__(self, project=None, credentials=None): pass
    def query(self, q, job_config=None, retry=None, timeout=None):
        mode = os.environ.get("FAKE_BQ", "ok")
        if mode == "block":
            time.sleep(600)                 # the client call never returns
        if mode == "reauth":
            from google.oauth2 import reauth, challenges
            from google.auth.exceptions import ReauthFailError
            if reauth.is_interactive():
                challenges.get_user_password("Please enter your password:")   # would wait on the console
            raise ReauthFailError("Reauthentication challenge could not be answered because you are not in an "
                                  "interactive session.")
        return _Job()
""",
}


@pytest.fixture
def fake_google(tmp_path):
    lib = tmp_path / "lib"
    for rel, text in FAKE_GOOGLE.items():
        f = lib / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    adc = tmp_path / "adc.json"
    adc.write_text(json.dumps({"type": "authorized_user"}))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(lib), str(ROOT)]), GOOGLE_APPLICATION_CREDENTIALS=str(adc),
               FAKE_DEFAULT_CALLED=str(tmp_path / "default_called"))
    env.pop("ADC_CHECK_CHILD", None)
    return env, tmp_path


def _run_check(env, timeout_s, **extra):
    env = dict(env, TOKEN_METRICS_ADC_TIMEOUT=str(timeout_s), **extra)
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, str(ROOT / "adc_check.py")], cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=timeout_s + 60)
    return r, time.monotonic() - t0


def test_a_the_check_returns_its_timeout_code_inside_its_limit_when_the_client_call_blocks(fake_google):
    env, _ = fake_google
    r, took = _run_check(env, 14, FAKE_BQ="block")           # the child's own limit: 14 - 10 = 4 s
    assert r.returncode == 9, r.stdout + r.stderr
    assert took < 14, f"took {took:.1f}s: the child must end itself before the parent's limit"
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1 and lines[0].startswith("BIGQUERY CHECK: FAILED (exit 9)") and "FIX: " in lines[0], lines


def test_a_an_expired_login_is_exit_2_at_once_and_never_prompts(fake_google):
    env, _ = fake_google
    r, took = _run_check(env, 60, FAKE_BQ="reauth")
    assert r.returncode == 2, r.stdout + r.stderr
    assert took < 30
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1 and "FAILED (exit 2)" in lines[0] and "login expired" in lines[0]
    assert "password" not in (r.stdout + r.stderr).lower()


def test_a_an_absent_login_is_exit_3_and_never_calls_google_auth_default(fake_google):
    env, tmp = fake_google
    r, _ = _run_check(env, 60, GOOGLE_APPLICATION_CREDENTIALS=str(tmp / "missing.json"))
    assert r.returncode == 3 and "FAILED (exit 3)" in r.stdout and "no Application Default Credentials" in r.stdout
    assert not (tmp / "default_called").exists()
    r, _ = _run_check(env, 60, FAKE_BQ="ok")                 # a good login: exit 0, google.auth.default() untouched
    assert r.returncode == 0 and "BIGQUERY CHECK: OK" in r.stdout
    assert not (tmp / "default_called").exists()


def test_a_bounded_ends_a_blocked_worker_with_the_timeout_code(capsys):
    import adc_check
    gate = threading.Event()
    exits = []
    t0 = time.monotonic()
    rc = adc_check.bounded(lambda: gate.wait(60) or 0, 1, _exit=exits.append)
    gate.set()
    assert rc == adc_check.EXIT_TIMEOUT == 9 and exits == [9] and time.monotonic() - t0 < 5
    out = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(out) == 1 and out[0].startswith("BIGQUERY CHECK: FAILED (exit 9)")
    assert adc_check.bounded(lambda: 0, 5, _exit=exits.append) == 0 and exits == [9]


def test_a_the_parent_never_waits_on_a_grandchild_holding_the_output(tmp_path):
    """subprocess.run's timeout on Windows waits, unbounded, for the pipe to close after the kill; the parent now writes
    the child's output to a file and kills by PID."""
    import adc_check
    pidfile = tmp_path / "grandchild.pid"
    script = tmp_path / "holds.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(40)'])\n"   # inherits stdout
        f"open({str(pidfile)!r}, 'w').write(str(g.pid))\n"
        "print('started', flush=True)\n"
        "time.sleep(40)\n")
    t0 = time.monotonic()
    try:
        assert adc_check.guarded(timeout_s=3, argv0=str(script)) == 9
        assert time.monotonic() - t0 < 20
    finally:
        if pidfile.exists():                                    # clean up by PID only
            try:
                os.kill(int(pidfile.read_text()), 9)
            except OSError:
                pass


# ============================================================================== (a) a wall-clock limit on every step
def _fn(name: str) -> str:
    i = PS.index(f"function {name}")
    return PS[i:PS.index("\n}\n", i)]


def test_a_every_step_is_started_waited_for_with_a_timeout_and_killed_by_pid_only():
    body = _fn("Invoke-Step(")
    assert "Start-Process -FilePath $Python" in body and "-RedirectStandardInput $inFile" in body
    assert "& $Python" not in body, "no pipeline the run waits on for ever"
    loop = body[body.index("while (-not $p.WaitForExit(2000))"):]
    assert loop.index("(Get-Date) -ge $deadline") < loop.index("Stop-StepProcess $p.Id") < loop.index(
        "$code = $StepTimedOut")
    assert "TIMED OUT after $limit min - stopped PID $($p.Id)" in body
    stop = _fn("Stop-StepProcess(")
    assert "taskkill.exe /PID $id /T /F" in stop and "Get-Process -Id $id" in stop
    for by_name in ("/IM", "Stop-Process -Name", "Get-Process -Name", "pkill", "killall"):
        assert by_name not in PS, by_name


def test_a_every_step_has_a_limit_and_the_worst_case_run_ends_before_the_task_limit():
    caps = {k: int(v) for k, v in re.findall(r'"([^"]+)" = (\d+)', PS[PS.index("$StepMinutes = @{"):
                                                                     PS.index("$TailSteps = @(")])}
    steps = re.findall(r'Invoke-Step "([^"]+)"', PS)
    assert set(steps) <= set(caps), set(steps) - set(caps)
    tail = re.findall(r'"([^"]+)"', PS[PS.index("$TailSteps = @("):PS.index(")", PS.index("$TailSteps = @("))])
    budget = int(re.search(r"\[int\]\$RunBudgetMinutes = (\d+)", PS).group(1))
    reserve = int(re.search(r"\[int\]\$TailReserveMinutes = (\d+)", PS).group(1))
    login = int(re.search(r"\[int\]\$LoginWaitMinutes = (\d+)", PS).group(1))
    pause = int(re.search(r"\[int\]\$RerunPauseMinutes = (\d+)", PS).group(1))
    limit_h = int(re.search(r"\[int\]\$LimitHours = (\d+)", REG).group(1))
    # simulate Get-StepLimit with every step running to its limit, the login wait and the rerun pause included
    clock = 0.0
    order = ["BigQuery check", "LOGIN", "BigQuery check (after login)", "token_metrics", "PAUSE", "token_metrics (rerun)",
             "NEAR lockups (weekly)", "NEAR stage 1 (November)", *tail]
    for s in order:
        if s == "LOGIN":
            clock += login + 1
            continue
        if s == "PAUSE":
            left = min(caps["token_metrics (rerun)"], budget - clock - reserve)
            if left - pause >= 30:
                clock += pause
            continue
        left = budget - clock - (0 if s in tail else reserve)
        lim = int(min(caps[s], left)) if left == left else 0
        if lim >= 1:
            clock += lim + 0.5                                  # + the stop and the 15 s wait after a timeout
    assert clock < limit_h * 60, f"worst case {clock:.0f} min against the task's {limit_h} h"
    assert budget < limit_h * 60 and reserve >= sum(caps[s] for s in tail)


# ======================================================================================== (b) never waits on a prompt
def test_b_a_scheduled_run_has_no_path_to_the_login():
    ti = _fn("Test-Interactive")
    assert ti.splitlines()[1].strip() == "if ($Scheduled) { return $false }"     # first thing it decides
    assert "[Environment]::UserInteractive" in ti and "IsInputRedirected" in ti
    assert PS.index("$Interactive = Test-Interactive") < PS.index('$adc = Invoke-Step "BigQuery check"')
    assert PS.count('"auth", "application-default", "login"') == 1
    i = PS.index("if (-not $Interactive) {")
    j = PS.index("} elseif ($gcloud) {", i)
    assert "Start-Process" not in PS[i:j] and "login" not in PS[i:j].replace("no login attempted", "")
    assert j < PS.index('"auth", "application-default", "login"'), "the login sits only in the interactive branch"
    assert "-Scheduled" in REG and "-NonInteractive" in REG


def test_b_nothing_the_steps_start_can_prompt():
    first = PS.index('$adc = Invoke-Step "BigQuery check"')
    for env in ('$env:CLOUDSDK_CORE_DISABLE_PROMPTS = "1"', '$env:GIT_TERMINAL_PROMPT = "0"',
                '$env:GCM_INTERACTIVE = "never"', '$env:PIP_NO_INPUT = "1"'):
        assert -1 < PS.index(env) < first, env
    # the steps' stdin is an empty file: input() reads end-of-file
    assert '[System.IO.File]::WriteAllText($inFile, "")' in _fn("Invoke-Step(")
    # and no script a step runs asks for input or a password itself
    scripts = {a for a in re.findall(r'@\("([a-z_]+\.py)"', PS)}
    assert {"adc_check.py", "token_metrics.py", "export_snapshot.py"} <= scripts
    files = [ROOT / s for s in scripts] + sorted((ROOT / "fetch").glob("*.py"))
    for f in files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "input":
                raise AssertionError(f"{f.name}:{node.lineno} calls input()")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "getpass" \
                    and f.name != "adc_check.py":
                raise AssertionError(f"{f.name}:{node.lineno} calls getpass")
    lock = (ROOT / "windows" / "near_lockups_run.ps1").read_text(encoding="utf-8")
    assert '$env:CLOUDSDK_CORE_DISABLE_PROMPTS = "1"' in lock and '$env:GIT_TERMINAL_PROMPT = "0"' in lock


# ===================================================================================== (c) never blocks the next day
def test_c_the_task_settings():
    assert "-WakeToRun -StartWhenAvailable" in REG
    assert "[int]$LimitHours = 6" in REG and "-ExecutionTimeLimit (New-TimeSpan -Hours $LimitHours)" in REG
    code = "\n".join(ln for ln in REG.splitlines() if not ln.lstrip().startswith("#"))
    assert "IgnoreNew" not in code and "Queue" not in code, "a hung run must not drop or hold the next day's run"
    assert 'CimInstanceProperties.Item("MultipleInstances").Value = 3' in code      # StopExisting
    assert "$settings.DisallowStartIfOnBatteries = $false" in code and "$settings.StopIfGoingOnBatteries = $false" in code
    assert "Get-ScheduledTask -TaskName $TaskName" in code, "what Task Scheduler holds is printed"
    stale = int(re.search(r"\[int\]\$StaleHours = (\d+)", PS).group(1))
    assert 0 < stale <= int(re.search(r"\[int\]\$LimitHours = (\d+)", REG).group(1))


def test_c_the_run_holds_the_laptop_awake_before_its_first_step():
    i = PS.index("SetThreadExecutionState([uint32]2147483649)")
    assert 2147483649 == 0x80000000 | 0x00000001                       # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    assert i < PS.index("$PrevKilled = Resolve-PreviousRun") < PS.index('$adc = Invoke-Step "BigQuery check"')


def test_c_a_previous_run_left_open_too_long_is_recorded_killed_and_the_new_run_proceeds():
    fn = _fn("Resolve-PreviousRun")
    keep = fn.index("if ($ageH -lt $StaleHours) {")
    assert fn.index("$ageH = ((Get-Date) - [datetime]$prev.started).TotalHours") < keep
    assert keep < fn.index("Stop-StepProcess ([int]$prev.pid)") < fn.index("=== $($prev.open) : KILLED")
    assert "Stop-StepProcess ([int]$prev.step_pid)" in fn and '$sp.ProcessName -like "py*"' in fn
    assert "exit" not in re.sub(r"\$\(.*?\)|\"[^\"]*\"", "", fn), "the new run is never stopped by an old one"
    assert "step_pid = $stepPid" in _fn("Save-State(") and "Save-State $label $p.Id" in _fn("Invoke-Step(")
    assert PS.index("$PrevKilled = Resolve-PreviousRun") < PS.index('$adc = Invoke-Step "BigQuery check"')


# ================================================================================================= ASCII and the docs
def test_every_ps1_is_ascii():
    for f in (ROOT / "windows").glob("*.ps1"):
        assert f.read_bytes().isascii(), f


def test_the_runbook_and_spec_sections_are_in_place():
    rb = (ROOT / "RUNBOOK.md").read_text(encoding="utf-8")
    i = rb.index("## 11fd. Round 15 — the daily run never hangs, never prompts, never blocks the next day (2026-11-20)")
    assert i < rb.index("## 11fc.") and rb.index("## 11fc.") == rb.index("\n## ", i + 1) + 1
    sec = rb[i:rb.index("## 11fc.")]
    for want in ("register_task.ps1", "powercfg /waketimers", "Get-ScheduledTask", "-Reconcile", "python -c"):
        assert want in sec, want
    for ln in sec.splitlines():
        if "python -c " in ln:
            arg = ln[ln.index("python -c ") + len("python -c "):].split("  #")[0].strip()
            assert arg.startswith('"') and arg.rstrip().endswith('"') and '"' not in arg.strip()[1:-1], ln
    spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    j = spec.index("### Round 15 — the daily run (Jake 2026-11-20), old → new")
    assert j < spec.index("### Round 14") and spec.index("\n### ", j + 1) == spec.index("\n### Round 14")
