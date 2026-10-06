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
#   - starts only on AC power (-AllowBattery to change), and runs as soon as possible after a missed time
#     (laptop off or asleep without a wake timer);
#   - is stopped after 4 hours, and never starts a second copy while one is running.
# Remove it with:  Unregister-ScheduledTask -TaskName "Crypto-Tracker daily" -Confirm:$false
# Run it now:      Start-ScheduledTask -TaskName "Crypto-Tracker daily"

param(
    [string]$At = "06:30",
    [string]$TaskName = "Crypto-Tracker daily",
    [switch]$AllowBattery
)

$script = Join-Path $PSScriptRoot "daily_run.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4) -MultipleInstances IgnoreNew
if ($AllowBattery) {
    $settings.DisallowStartIfOnBatteries = $false
    $settings.StopIfGoingOnBatteries = $false
}
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "token_metrics.py + credibility_report.py daily; logs in logs\run_YYYY-MM-DD.log" -Force
Write-Host "Registered '$TaskName' daily at $At. Wake timers: powercfg /waketimers lists them; check Power Options."
