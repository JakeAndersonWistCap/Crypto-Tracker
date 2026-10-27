# windows/register_lockup_task.ps1 - registers the weekly NEAR lockup read as its OWN scheduled task (Round D3-2,
# Jake 2026-10-26). Run ONCE, in PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File windows\register_lockup_task.ps1               # Sunday 02:15
#
# The task "Crypto-Tracker NEAR lockups":
#   - runs windows\near_lockups_run.ps1 (its own log: logs\near_lockups_YYYY-MM-DD.log) as YOU, while you are logged
#     on (Interactive, like the daily task);
#   - weekly, Sunday at 02:15 local; WAKES THE LAPTOP to run (WakeToRun) and runs as soon as possible after a missed
#     time (StartWhenAvailable);
#   - is stopped after 8 hours (-LimitHours; 4 until D4-1a - Jake's read of 2026-10-26 took 4h35m, so 4 h killed every
#     read), never starts a second copy while one is running, and runs on battery as
#     the daily task does (-StopOnBattery for AC only).
#   The daily task's step 2b stays as the fallback: it reads only when the last stored read is 8+ days old.
# Remove it with:  Unregister-ScheduledTask -TaskName "Crypto-Tracker NEAR lockups" -Confirm:$false
# Run it now:      Start-ScheduledTask -TaskName "Crypto-Tracker NEAR lockups"

param(
    [string]$At = "02:15",
    [string]$Day = "Sunday",
    [string]$TaskName = "Crypto-Tracker NEAR lockups",
    [switch]$StopOnBattery,
    [int]$LimitHours = 8
)

$script = Join-Path $PSScriptRoot "near_lockups_run.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Day -At $At
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours $LimitHours) -MultipleInstances IgnoreNew
if (-not $StopOnBattery) {
    $settings.DisallowStartIfOnBatteries = $false
    $settings.StopIfGoingOnBatteries = $false
}
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "near_lockups_weekly.py weekly; log in logs\near_lockups_YYYY-MM-DD.log" -Force
Write-Host "Registered '$TaskName' weekly, $Day at $At (stopped after $LimitHours h)."
