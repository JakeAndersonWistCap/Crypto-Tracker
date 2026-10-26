# windows/daily_run.ps1 - the daily run, for Windows Task Scheduler (register_task.ps1 sets it up).
#
#   1. adc_check.py      - NEAR BigQuery, before any step: a real one-row query on Jake's user ADC; run by hand it
#                          logs in itself on failure, scheduled it skips the BigQuery reads and says so first
#   2. token_metrics.py  - the run; RERUN ONCE, after a pause, if its summary shows NETWORK-WIDE TROUBLE
#   3. credibility_report.py --roots, then the full report; credibility_history.py --record (verdicts kept per run)
#   4. daily_summary.py  - the PLAIN SUMMARY the log ends with: counts vs the previous day, NETWORK-WIDE TROUBLE,
#                          ACTION NEEDED, new CHECKs (overnight 2026-10-06, E2)
#
# Windows PowerShell 5.1 compatible (no pipeline-chain operators, no ?? or ternaries). Reviewed overnight 2026-10-06 (E1): Python's
# output is decoded as UTF-8 (5.1 otherwise uses the OEM code page and mangles non-ASCII text); the interpreter
# is .venv, then venv, then the py launcher, then python on PATH; a missing interpreter is logged, not thrown.
#
# Everything goes to logs\run_YYYY-MM-DD.log (appended if the task runs twice in a day).
# Run by hand to test:  powershell -ExecutionPolicy Bypass -File windows\daily_run.ps1

param(
    [string]$Python = "",                 # default: .venv\Scripts\python.exe if present, else python on PATH
    [int]$RerunPauseMinutes = 15,
    [int]$LoginWaitMinutes = 5,           # the most an interactive Google login may hold the run (Round G 1.7)
    [switch]$Scheduled,                   # set by register_task.ps1: never stop for a login prompt
    [switch]$Reconcile                    # only close out a previous run that was killed (D2-8a), then exit
)

$ErrorActionPreference = "Continue"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"               # every printed line reaches the log as it is printed (D2-8a)
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }   # no console: nothing to set

$PyArgs = @()                             # extra leading arguments (the py launcher's -3)
if (-not $Python) {
    $Python = "python"
    foreach ($cand in @((Join-Path $Repo ".venv\Scripts\python.exe"), (Join-Path $Repo "venv\Scripts\python.exe"))) {
        if (Test-Path $cand) { $Python = $cand; break }
    }
    if ($Python -eq "python" -and -not (Get-Command python -ErrorAction SilentlyContinue) -and
        (Get-Command py -ErrorAction SilentlyContinue)) {
        $Python = "py"
        $PyArgs = @("-3")
    }
}
$LogDir = Join-Path $Repo "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir ("run_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

function Write-Log([string]$text) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $text
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

function Show-Toast([string]$title, [string]$body) {
    # Windows PowerShell 5.1's WinRT toast; falls back to msg.exe. Never fails the run.
    try {
        [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
        [void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
        $esc = [System.Security.SecurityElement]
        $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
        $xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$($esc::Escape($title))</text>" +
                     "<text>$($esc::Escape($body))</text></binding></visual></toast>")
        $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show(
            [Windows.UI.Notifications.ToastNotification]::new($xml))
    } catch {
        try { & msg.exe $env:USERNAME "$title - $body" } catch { }
    }
    Write-Log "NOTIFIED: $title - $body"
}

# A FINAL LINE FOR EVERY STEP, EVEN WHEN KILLED (Round D2-8a, Jake 2026-10-26): the scheduled runs died silently on
# 10-22 (log ends at adc_check.py), 10-23 (after token_metrics started) and 10-25 (1 minute into the lockup step). A
# process stopped from outside (Task Scheduler's stop or time limit, battery, sleep, shutdown) runs no finally block, and
# each step's output used to reach the log only after the step exited. Now:
#   * a step's output is written to the log LINE BY LINE as it arrives, so a killed step keeps what it printed;
#   * every step's start and end is recorded in logs\daily_run_state.json (this run's log, PID, the open step);
#   * the NEXT run (or -Reconcile by hand) finds a run that never finished, whose PowerShell is gone, and appends its
#     FINAL lines to THAT run's log: the step it was killed in, the steps that ran with their exit codes, the steps
#     that never ran. It never guesses the cause.
$StateFile = Join-Path $LogDir "daily_run_state.json"
$RunStarted = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$Plan = @("BigQuery check", "token_metrics", "NEAR lockups (weekly)", "NEAR stage 1 (November)", "credibility roots",
          "credibility report", "credibility history", "daily summary", "history gaps", "export snapshot")

function Save-State([string]$open) {
    $since = ""
    if ($open) { $since = Get-Date -Format "yyyy-MM-dd HH:mm:ss" }
    $done = @($script:Steps | ForEach-Object { "$($_[0])=$($_[1])" })
    $st = [ordered]@{ log = $Log; pid = $PID; started = $RunStarted; open = $open; open_since = $since;
                      final = [bool]$script:FinalWritten; steps = $done }
    try { ($st | ConvertTo-Json -Depth 3) | Set-Content -Path $StateFile -Encoding UTF8 } catch { }
}

function Resolve-PreviousRun {
    # Returns a one-line notice when a previous run was killed (and closes out its log), else "".
    if (-not (Test-Path $StateFile)) { return "" }
    try { $prev = Get-Content -Path $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return "" }
    if ($prev.final) { return "" }
    $alive = $null
    if ($prev.pid) { $alive = Get-Process -Id ([int]$prev.pid) -ErrorAction SilentlyContinue }
    if ($alive -and $alive.ProcessName -like "powershell*" -and $alive.StartTime -le [datetime]$prev.started) {
        return "a daily run started $($prev.started) (PID $($prev.pid)) is still running - left alone"
    }
    $ran = (@($prev.steps) | Where-Object { $_ }) -join ", "
    if (-not $ran) { $ran = "none" }
    $doneLabels = @(@($prev.steps) | Where-Object { $_ } | ForEach-Object { $_.Substring(0, $_.LastIndexOf("=")) })
    $notRun = @($Plan | Where-Object { ($doneLabels -notcontains $_) -and ($_ -ne $prev.open) })
    $nr = "none"
    if ($notRun.Count -gt 0) { $nr = $notRun -join ", " }
    $stamp = "[reconciled {0}]" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    $why = "stopped from outside - Task Scheduler stop or time limit, battery, sleep or shutdown; the log does not say which"
    $lines = @()
    $where = "between steps"
    if ($prev.open) {
        $where = "during $($prev.open)"
        $lines += "$stamp === $($prev.open) : KILLED - no exit code (started $($prev.open_since); $why)"
    }
    $lines += "$stamp daily run FINAL: KILLED $where - steps that ran: $ran; not run: $nr"
    try { Add-Content -Path $prev.log -Value $lines -Encoding UTF8 } catch { }
    $prev.final = $true
    try { ($prev | ConvertTo-Json -Depth 3) | Set-Content -Path $StateFile -Encoding UTF8 } catch { }
    return "the previous run (started $($prev.started)) was KILLED $where; FINAL lines appended to $($prev.log)"
}

function Invoke-Step([string]$label, [string[]]$arguments) {
    # Runs one Python step, writing its output to the log LINE BY LINE as it arrives (a killed step keeps what it
    # printed); the step stays open in the run state until it exits. Returns (exit code, output text).
    Write-Log "=== $label : $Python $($arguments -join ' ')"
    Save-State $label
    $t0 = Get-Date
    $out = New-Object System.Collections.Generic.List[string]
    $w = $null
    try {
        $fs = New-Object System.IO.FileStream($Log, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write,
                                              [System.IO.FileShare]::ReadWrite)
        $w = New-Object System.IO.StreamWriter($fs, (New-Object System.Text.UTF8Encoding($false)))
        $w.AutoFlush = $true
    } catch { $w = $null }
    try {
        & $Python @PyArgs @arguments 2>&1 | ForEach-Object {
            $s = "$_"
            if ($w) { $w.WriteLine($s) } else { Add-Content -Path $Log -Value $s -Encoding UTF8 }
            $out.Add($s)
        }
        $code = $LASTEXITCODE
    } catch {
        $s = "could not start ${Python}: $($_.Exception.Message)"
        if ($w) { $w.WriteLine($s) } else { Add-Content -Path $Log -Value $s -Encoding UTF8 }
        $out.Add($s)
        $code = 9009
    } finally {
        if ($w) { $w.Dispose() }
    }
    Write-Log ("=== $label exited $code ({0:N0}s)" -f ((Get-Date) - $t0).TotalSeconds)
    $script:Steps += ,@($label, $code)
    Save-State ""
    return @($code, ($out -join "`n"))
}

$Steps = @()                              # (label, exit code) of every step, for the closing console line
$FinalWritten = $false

$PrevKilled = Resolve-PreviousRun
if ($Reconcile) {
    if (-not $PrevKilled) { $PrevKilled = "no killed run to close out" }
    Write-Host $PrevKilled
    exit 0
}
Write-Log "daily run start (repo $Repo, python $Python)"
if ($PrevKilled) { Write-Log "PREVIOUS RUN: $PrevKilled" }
Save-State ""
# A FINAL STATUS LINE WHATEVER HAPPENS (Round G 1.8): run_2026-10-18.log ended at "=== BigQuery check" with nothing
# after it. The body runs inside try/finally; if it ends any way other than the closing summary, the finally block
# writes "daily run FINAL: stopped early" with the steps that ran.
try {

# 1. NEAR BigQuery, FIRST, before any other step (Jake 2026-10-12 evening, item 16): his user ADC keeps expiring and
#    service-account keys are blocked by org policy. adc_check.py runs a REAL one-row query. On failure:
#      * run by hand (interactive): log in here — gcloud auth application-default login, then set the quota project —
#        re-test, and continue;
#      * scheduled (-Scheduled, or started by the Task Scheduler service): never wait for a browser; the BigQuery reads
#        are skipped (TOKEN_METRICS_SKIP_NEAR_BQ) and the summary's FIRST line says how to log in.
#    Either way every BigQuery series resumes from its last stored day on the next good run (RUNBOOK 11bs).
function Test-Interactive {
    if ($Scheduled) { return $false }
    if (-not [Environment]::UserInteractive) { return $false }
    try {
        $me = Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($me.ParentProcessId)" -ErrorAction Stop
        if ($parent.Name -in @("svchost.exe", "taskeng.exe", "taskhostw.exe")) { return $false }
    } catch { }
    try { if ([Console]::IsInputRedirected) { return $false } } catch { }
    return $true
}
$BqSkipped = ""
$adc = Invoke-Step "BigQuery check" @("adc_check.py")
if ($adc[0] -ne 0) {
    $gcloud = Get-Command gcloud -ErrorAction SilentlyContinue
    if ((Test-Interactive) -and $gcloud) {
        Write-Log "BigQuery check failed - interactive: logging in (a browser window opens)"
        Write-Host "NEAR BigQuery: the Google login has expired - a browser window opens to log in."
        # BOUNDED (Round G 1.7(b)): the login waits at most $LoginWaitMinutes for the browser, then the run carries on
        $lp = Start-Process -FilePath $gcloud.Source -ArgumentList @("auth", "application-default", "login") -PassThru -NoNewWindow
        if ($lp.WaitForExit($LoginWaitMinutes * 60000)) {
            & gcloud auth application-default set-quota-project near-data-510309 2>&1 |
                ForEach-Object { Add-Content -Path $Log -Value "$_" -Encoding UTF8 }
            $adc = Invoke-Step "BigQuery check (after login)" @("adc_check.py")
        } else {
            try { $lp.Kill() } catch { }
            Write-Log "BigQuery login not completed in $LoginWaitMinutes minute(s) - skipped; the run carries on"
        }
    }
    if ($adc[0] -ne 0) {
        # the em dash from its code point: Windows PowerShell 5.1 reads this BOM-less file as ANSI
        $BqSkipped = "NEAR BigQuery skipped " + [char]0x2014 + " log in: gcloud auth application-default login"
        $env:TOKEN_METRICS_SKIP_NEAR_BQ = "1"
        Write-Log $BqSkipped
        Show-Toast "Crypto-Tracker: NEAR BigQuery skipped" ("Log in: gcloud auth application-default login, then " +
            "gcloud auth application-default set-quota-project near-data-510309. Each BigQuery series resumes from its " +
            "last stored day on the next good run. Log: $Log")
    }
}

# 2. The run, rerun ONCE if the summary shows network-wide trouble.
$run = Invoke-Step "token_metrics" @("token_metrics.py")
if ($run[1] -match "NETWORK-WIDE TROUBLE") {
    Write-Log "NETWORK-WIDE TROUBLE in the summary - rerunning once in $RerunPauseMinutes minute(s)"
    Start-Sleep -Seconds ($RerunPauseMinutes * 60)
    $run = Invoke-Step "token_metrics (rerun)" @("token_metrics.py")
    if ($run[1] -match "NETWORK-WIDE TROUBLE") {
        Show-Toast "Crypto-Tracker: network trouble twice" "Both runs hit NETWORK-WIDE TROUBLE. Stored values stand. Log: $Log"
    }
}
if ($run[0] -eq 75) {
    # RUN LOCK (Round G 1.8): another run held metrics.db for the whole wait; nothing was fetched by this one
    Write-Log "token_metrics did NOT run: another run holds metrics.db (RUN LOCKED - see the line above)"
    Show-Toast "Crypto-Tracker: run skipped" "Another run was using metrics.db; this one did not run. Log: $Log"
} elseif ($run[0] -ne 0) {
    Show-Toast "Crypto-Tracker: run exited $($run[0])" "token_metrics.py did not finish cleanly. Log: $Log"
}

# 2b. NEAR's still-unvested lockups (Jake 2026-10-11, C), WEEKLY: the script reads only when the last stored read is
#     7+ days old, and stores nothing when any lockup is unreadable. Resumable at its pinned block for 48 h (D2-8), so
#     a read cut short is continued by the next day's run; ~80 min when neither RPC endpoint throttles (D2-8a).
[void](Invoke-Step "NEAR lockups (weekly)" @("near_lockups_weekly.py"))

# 2c. NEAR stage 1 (Round G3 item 6): wallet_inflow_days runs ONCE, inside 2026-11-01..2026-11-07, under that month's
#     BigQuery budget and the top-up reserve rule; on any other day it says so and exits 0. Never wallet_inflows_alltime.
[void](Invoke-Step "NEAR stage 1 (November)" @("check_offline_items.py", "near_wallet_inflow_days"))

# 3. Credibility: the root-cause map, then the full report.
[void](Invoke-Step "credibility roots" @("credibility_report.py", "--roots"))
[void](Invoke-Step "credibility report" @("credibility_report.py"))

# 3b. Credibility history (2026-10-11): record this run's verdicts, then per row the flips in 7 / 14 / 28 days,
#     mean and max |gap|, and the rows whose verdict changed since the previous run.
[void](Invoke-Step "credibility history" @("credibility_history.py", "--record"))

# 4. The plain summary, so the log ends with it.
Write-Log "daily run end"
[void](Invoke-Step "daily summary" @("daily_summary.py", "--log", $Log))

# 4b. History gaps (theory layer, Phase 1, 2026-10-12): logs\history_gaps.csv — which series the trajectory engine is
# missing and the cheapest fix. Reads nothing from the network; `python history_gaps.py --run-fixes` runs the free ones.
[void](Invoke-Step "history gaps" @("history_gaps.py"))

# 5. The theory snapshot (Jake 2026-10-11, D2), the LAST step: one row per project, every headline metric with its
#    credibility verdict, NEAR LIMIT flag and coverage days -> snapshots\snapshot_<date>.csv (METRICS.md defines it).
[void](Invoke-Step "export snapshot" @("export_snapshot.py"))

# 6. ONE LINE ON THE CONSOLE (Jake's Phase 1 review 2026-10-12, item 9: the script printed nothing): what ran, the run's
#    exit code, any step that failed, and where the log is. The task's exit code is the run's.
# A FAILED BIGQUERY CHECK NAMES ITS FIX (Jake's review of the ef6dc86 run, item 15): adc_check.py's last line on
# failure is "FIX: <exact command>"; this table is the fallback. A check that failed and then passed after the login
# above is not a failed step.
$AdcFix = @{
    2 = "login expired - run: gcloud auth application-default login; gcloud auth application-default set-quota-project near-data-510309";
    3 = "no credentials or library - run: gcloud auth application-default login (pip install google-cloud-bigquery if missing)";
    4 = "wrong quota project - run: gcloud auth application-default set-quota-project near-data-510309";
    7 = "permission denied - the logged-in user needs roles/bigquery.jobUser on near-data-510309";
    8 = "the one-row query failed - check the network, then: python adc_check.py";
    9 = "the check timed out (a login prompt or the network) - run: gcloud auth application-default login; gcloud auth application-default set-quota-project near-data-510309"
}
$AdcFixLine = (($adc[1] -split "`n") | Where-Object { $_ -like "FIX: *" } | Select-Object -Last 1)
$bqOk = ($adc[0] -eq 0)
$failed = @($Steps | Where-Object { $_[1] -ne 0 -and -not ($bqOk -and $_[0] -eq "BigQuery check") } | ForEach-Object {
    $t = "$($_[0])=$($_[1])"
    if ($_[0] -like "BigQuery check*") {
        if ($AdcFixLine) { $t += " ($($AdcFixLine.Substring(5).Trim()))" }
        elseif ($AdcFix.ContainsKey([int]$_[1])) { $t += " ($($AdcFix[[int]$_[1]]))" }
    }
    $t
})
$failText = "no step failed"
if ($failed.Count -gt 0) { $failText = "failed: " + ($failed -join ", ") }
$summary = "daily_run: ran $($Steps.Count) step(s); token_metrics exit $($run[0]); $failText; log $Log"
if ($BqSkipped) { $summary = "$BqSkipped | $summary" }      # the skip notice FIRST (item 16)
if ($PrevKilled -like "*KILLED*") { $summary = "PREVIOUS RUN KILLED - see its log | $summary" }
# NEARBLOCKS v1 SUNSET (2026-10-31; Jake 2026-10-13, Part E4): fetch/nearblocks.py logs "NEARBLOCKS API SUNSET" when a
# call answers with a Deprecation / Sunset header, HTTP 410 or a sunset message — the console line names NearBlocks
$NbSunset = (($run[1] -split "`n") | Where-Object { $_ -like "*NEARBLOCKS API SUNSET*" } | Select-Object -First 1)
if ($NbSunset) { $summary = "NearBlocks: deprecation/sunset answer - see the log ($($NbSunset.Trim().Substring(0, [Math]::Min(160, $NbSunset.Trim().Length)))) | $summary" }
Write-Log $summary
Write-Host $summary
Write-Log "daily run FINAL: complete - token_metrics exit $($run[0])"
$FinalWritten = $true
Save-State ""
exit $run[0]
} finally {
    if (-not $FinalWritten) {
        $ran = ($Steps | ForEach-Object { "$($_[0])=$($_[1])" }) -join ", "
        if (-not $ran) { $ran = "none" }
        Write-Log "daily run FINAL: stopped early - steps that ran: $ran"
        $FinalWritten = $true
        Save-State ""
    }
}
