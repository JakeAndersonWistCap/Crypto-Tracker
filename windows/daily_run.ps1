# windows/daily_run.ps1 - the daily run, for Windows Task Scheduler (register_task.ps1 sets it up).
#
#   1. adc_check.py      - NEAR's Google login; a desktop notification if Jake must re-login
#   2. token_metrics.py  - the run; RERUN ONCE, after a pause, if its summary shows NETWORK-WIDE TROUBLE
#   3. credibility_report.py --roots, then the full report
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
    [int]$RerunPauseMinutes = 15
)

$ErrorActionPreference = "Continue"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
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

function Invoke-Step([string]$label, [string[]]$arguments) {
    # Runs one Python step, appends its output to the log, returns (exit code, output text).
    Write-Log "=== $label : $Python $($arguments -join ' ')"
    try {
        $out = & $Python @PyArgs @arguments 2>&1 | ForEach-Object { "$_" }
        $code = $LASTEXITCODE
    } catch {
        $out = @("could not start ${Python}: $($_.Exception.Message)")
        $code = 9009
    }
    Add-Content -Path $Log -Value $out -Encoding UTF8
    Write-Log "=== $label exited $code"
    return @($code, ($out -join "`n"))
}

Write-Log "daily run start (repo $Repo, python $Python)"

# 1. NEAR's Google login, first: fixed before the run, not found after it.
$adc = Invoke-Step "ADC check" @("adc_check.py")
if ($adc[0] -ne 0) {
    Show-Toast "Crypto-Tracker: NEAR login needs you" ("Run: gcloud auth application-default login, then " +
        "gcloud auth application-default set-quota-project near-data-510309. The run continues; NEAR BigQuery " +
        "reads are skipped until then. Log: $Log")
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
if ($run[0] -ne 0) {
    Show-Toast "Crypto-Tracker: run exited $($run[0])" "token_metrics.py did not finish cleanly. Log: $Log"
}

# 3. Credibility: the root-cause map, then the full report.
[void](Invoke-Step "credibility roots" @("credibility_report.py", "--roots"))
[void](Invoke-Step "credibility report" @("credibility_report.py"))

# 4. The plain summary, LAST, so the log ends with it.
Write-Log "daily run end"
[void](Invoke-Step "daily summary" @("daily_summary.py", "--log", $Log))
