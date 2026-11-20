# windows/near_lockups_run.ps1 - the weekly NEAR lockup read, for its own scheduled task (Round D3-2, Jake 2026-10-26).
# register_lockup_task.ps1 sets it up: "Crypto-Tracker NEAR lockups", Sunday 02:15, stopped after 8 hours.
#
#   python near_lockups_weekly.py  - reads every live lockup at one pinned block and stores the totals only when all
#                                    are read (never a partial total); resumable at that block for 48 hours
#
# Its own log: logs\near_lockups_YYYY-MM-DD.log, written LINE BY LINE as the output arrives (so a stopped read keeps
# its progress lines), closing with "near lockups FINAL: exit <code>". If the task is stopped (its 8-hour limit,
# battery, sleep, shutdown) no FINAL line is written: the log ends at the last progress line, and daily_run.ps1's
# fallback (step 2b, when the last stored read is 8+ days old) continues the read at the same block.
# Windows PowerShell 5.1 compatible. Run by hand:  powershell -ExecutionPolicy Bypass -File windows\near_lockups_run.ps1

param(
    [string]$Python = "",
    [switch]$Force                        # read now, whatever the age of the last stored read
)

$ErrorActionPreference = "Continue"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
# NOTHING MAY PROMPT (R15 (b), as daily_run.ps1): gcloud, git and pip never ask a scheduled task anything.
$env:CLOUDSDK_CORE_DISABLE_PROMPTS = "1"
$env:GIT_TERMINAL_PROMPT = "0"
$env:GCM_INTERACTIVE = "never"
$env:PIP_NO_INPUT = "1"

$PyArgs = @()
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
$Log = Join-Path $LogDir ("near_lockups_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

function Write-Log([string]$text) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $text
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

$arguments = @("near_lockups_weekly.py")
if ($Force) { $arguments += "--force" }
Write-Log "near lockups start (repo $Repo, python $Python $($arguments -join ' '))"
$t0 = Get-Date
# ONE WRITER OF THE STORE (R15.11, as daily_run.ps1): logs\store.lock for this PowerShell's PID. A live holder (the
# daily run, or a run by hand): this read does not start - "SKIPPED: ..." and exit 76 (the daily run's step 2b reads
# when the last stored read is 8+ days old). A stale lock is taken over. Released in the finally block below.
# storelock.py reads no network and waits for nothing here, so it returns at once.
$lockOut = (& $Python @PyArgs storelock.py acquire --pid "$PID" --who "scheduled NEAR lockups" --command "near_lockups_run.ps1" --max-hours 8 2>&1 | ForEach-Object { "$_" }) -join " "
$lockCode = $LASTEXITCODE
if ($lockOut) { Write-Log $lockOut }
if ($lockCode -eq 76) {
    Write-Log "near lockups FINAL: SKIPPED - the store was held (exit 76)"
    exit 76
}
$lockTaken = ($lockCode -eq 0)
if ($lockTaken) { $env:TOKEN_METRICS_STORE_LOCK_HELD = "$PID" }
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
    }
    $code = $LASTEXITCODE
} catch {
    $s = "could not start ${Python}: $($_.Exception.Message)"
    if ($w) { $w.WriteLine($s) } else { Add-Content -Path $Log -Value $s -Encoding UTF8 }
    $code = 9009
} finally {
    if ($w) { $w.Dispose() }
    if ($lockTaken) {
        $rel = (& $Python @PyArgs storelock.py release --pid "$PID" 2>&1 | ForEach-Object { "$_" }) -join " "
        if ($rel) { Write-Log $rel }
    }
}
Write-Log ("near lockups FINAL: exit $code ({0:N0} min)" -f ((Get-Date) - $t0).TotalMinutes)
exit $code
