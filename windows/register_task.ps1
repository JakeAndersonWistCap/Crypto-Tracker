# windows/register_task.ps1 - registers the daily run with Windows Task Scheduler. Run ONCE, in PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File windows\register_task.ps1                 # 06:30 every day
#   powershell -ExecutionPolicy Bypass -File windows\register_task.ps1 -At 07:15
#
# The task:
#   - runs daily_run.ps1 as YOU, only while you are logged on (it needs your Google login for NEAR, and
#     a logged-on session to show the notification);
#   - WAKES THE LAPTOP to run (needs "Allow wake timers" = Enable in Power Options > Advanced; many
#     Modern Standby laptops honour wake timers only on AC power);
#   - runs, and keeps running, on battery (-StopOnBattery for the old start-on-AC-only, stop-on-battery behaviour), and
#     runs as soon as possible after a missed time (laptop off or asleep without a wake timer);
#   - has NO time limit (-LimitHours N to set one), and never starts a second copy while one is running.
#   Round D2-8a (2026-10-26): these defaults match what Jake set by hand after the runs of 10-22, 10-23 and 10-25 died
#   silently (the task stopped on battery and was capped at 4 h; he removed both), so re-registering never puts them
#   back. Open risk: with no limit, a run that HANGS keeps the next morning's run from starting (IgnoreNew); daily_run.ps1
#   closes out a killed run's log on its next start (or: daily_run.ps1 -Reconcile).
# Remove it with:  Unregister-ScheduledTask -TaskName "Crypto-Tracker daily" -Confirm:$false
# Run it now:      Start-ScheduledTask -TaskName "Crypto-Tracker daily"

param(
    [string]$At = "06:30",
    [string]$TaskName = "Crypto-Tracker daily",
    [switch]$StopOnBattery,
    [switch]$AllowBattery,                # kept so older command lines still work; running on battery is the default
    [int]$LimitHours = 0                  # 0 = no limit
)

$script = Join-Path $PSScriptRoot "daily_run.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Scheduled" `
    -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours $LimitHours) -MultipleInstances IgnoreNew
if (-not $StopOnBattery) {
    $settings.DisallowStartIfOnBatteries = $false
    $settings.StopIfGoingOnBatteries = $false
}
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "token_metrics.py + credibility_report.py daily; logs in logs\run_YYYY-MM-DD.log" -Force
$limitText = "no time limit"
if ($LimitHours -gt 0) { $limitText = "stopped after $LimitHours h" }
$batteryText = "runs on battery"
if ($StopOnBattery) { $batteryText = "AC power only" }
Write-Host "Registered '$TaskName' daily at $At ($limitText, $batteryText). Wake timers: powercfg /waketimers lists them; check Power Options."
