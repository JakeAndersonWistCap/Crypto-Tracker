# windows/near_lockups_run.ps1 - the weekly NEAR lockup read, for its own scheduled task (Round D3-2, Jake 2026-10-26).
# register_lockup_task.ps1 sets it up: "Crypto-Tracker NEAR lockups", Sunday 02:15, stopped after 4 hours.
#
#   python near_lockups_weekly.py  - reads every live lockup at one pinned block and stores the totals only when all
#                                    are read (never a partial total); resumable at that block for 48 hours
#
# Its own log: logs\near_lockups_YYYY-MM-DD.log, written LINE BY LINE as the output arrives (so a stopped read keeps
# its progress lines), closing with "near lockups FINAL: exit <code>". If the task is stopped (its 4-hour limit,
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
}
Write-Log ("near lockups FINAL: exit $code ({0:N0} min)" -f ((Get-Date) - $t0).TotalMinutes)
exit $code
