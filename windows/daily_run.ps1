# windows/daily_run.ps1 - the daily run, for Windows Task Scheduler (register_task.ps1 sets it up).
#
#   1. adc_check.py      - NEAR's Google login; a desktop notification if Jake must re-login
#   2. token_metrics.py  - the run; RERUN ONCE, after a pause, if its summary shows NETWORK-WIDE TROUBLE
#   3. credibility_report.py --roots, then the full report
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

if (-not $Python) {
    $venv = Join-Path $Repo ".venv\Scripts\python.exe"
    $Python = if (Test-Path $venv) { $venv } else { "python" }
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
    $out = & $Python @arguments 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
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

Write-Log "daily run end"
