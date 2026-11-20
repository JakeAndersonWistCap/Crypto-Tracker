# windows/register_task.ps1 - registers the daily run with Windows Task Scheduler. Run ONCE, in PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File windows\register_task.ps1                 # 06:30 every day
#   powershell -ExecutionPolicy Bypass -File windows\register_task.ps1 -At 07:15
#
# The task:
#   - runs daily_run.ps1 -Scheduled as YOU, only while you are logged on (it needs your Google login for NEAR, and
#     a logged-on session to show the notification); -Scheduled means the run never stops for a login prompt;
#   - WAKES THE LAPTOP to run (needs "Allow wake timers" = Enable in Power Options > Advanced; many
#     Modern Standby laptops honour wake timers only on AC power);
#   - runs, and keeps running, on battery (-StopOnBattery for the old start-on-AC-only, stop-on-battery behaviour), and
#     runs as soon as possible after a missed time (laptop off or asleep without a wake timer);
#   - is stopped after 6 hours (-LimitHours; 0 = no limit, not the default). daily_run.ps1 keeps its own run inside
#     5 h 30 min (-RunBudgetMinutes 330, a wall-clock limit on every step), so the 6 h stop is only the backstop.
#   Round D2-8a / D3-3 (2026-10-26): Jake removed the stop-on-battery and set his task to PT6H after the runs of 10-22,
#   10-23 and 10-25 died silently; these defaults match, so re-registering never puts the old settings back. A cap stays
#   so a run that HANGS cannot keep the next morning's run from starting. daily_run.ps1 closes out a killed
#   run's log on its next start (or: daily_run.ps1 -Reconcile). The weekly NEAR lockup read has its own task
#   (register_lockup_task.ps1).
#   R15 (Jake 2026-11-20: the 11-19 run stayed open at "BigQuery check" and no 11-20 run started): MultipleInstances is
#   StopExisting (was IgnoreNew). With IgnoreNew a run still open at the next trigger made Task Scheduler DROP that
#   day's run - and StartWhenAvailable does not bring back a dropped run, only a missed one. With StopExisting the
#   next trigger stops the old instance and the new run starts; daily_run.ps1 records the old one KILLED in its log.
#   The ScheduledTasks module's enum has no StopExisting on Windows 10, so it is set on the CIM property (value 3,
#   TASK_INSTANCES_STOP_EXISTING); if that is refused, Parallel (daily_run.ps1 then stops a run open past -StaleHours
#   itself). The settings are printed after registering, so you can see what Task Scheduler holds.
# Remove it with:  Unregister-ScheduledTask -TaskName "Crypto-Tracker daily" -Confirm:$false
# Run it now:      Start-ScheduledTask -TaskName "Crypto-Tracker daily"

param(
    [string]$At = "06:30",
    [string]$TaskName = "Crypto-Tracker daily",
    [switch]$StopOnBattery,
    [switch]$AllowBattery,                # kept so older command lines still work; running on battery is the default
    [int]$LimitHours = 6                  # 0 = no limit (not the default: Jake 2026-10-26, D3-3)
)

$script = Join-Path $PSScriptRoot "daily_run.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Scheduled" `
    -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger = New-ScheduledTaskTrigger -Daily -At $At
# R15: Parallel here, then StopExisting on the CIM property below (was: -MultipleInstances IgnoreNew)
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours $LimitHours) -MultipleInstances Parallel
$instances = "Parallel"
try {
    $settings.CimInstanceProperties.Item("MultipleInstances").Value = 3      # TASK_INSTANCES_STOP_EXISTING
    $instances = "StopExisting"
} catch { }
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
Write-Host "Registered '$TaskName' daily at $At ($limitText, $batteryText, a run still open at the next trigger: $instances). Wake timers: powercfg /waketimers lists them; check Power Options."
# What Task Scheduler now holds (R15): expect ExecutionTimeLimit PT6H, StartWhenAvailable True, WakeToRun True,
# DisallowStartIfOnBatteries False, StopIfGoingOnBatteries False, MultipleInstances StopExisting (or Parallel).
try {
    $held = (Get-ScheduledTask -TaskName $TaskName).Settings
    Write-Host ("Settings: ExecutionTimeLimit {0}; StartWhenAvailable {1}; WakeToRun {2}; DisallowStartIfOnBatteries {3}; StopIfGoingOnBatteries {4}; MultipleInstances {5}" -f `
        $held.ExecutionTimeLimit, $held.StartWhenAvailable, $held.WakeToRun, $held.DisallowStartIfOnBatteries,
        $held.StopIfGoingOnBatteries, $held.MultipleInstances)
} catch { }
