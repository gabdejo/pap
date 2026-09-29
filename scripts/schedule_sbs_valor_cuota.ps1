# scripts/schedule_sbs_valor_cuota.ps1
# ---------------------------------------------------------------
# Registers, inspects or removes the Windows task that runs the SPP
# valor cuota scrape (scripts/run_sbs_valor_cuota.py --scheduled).
#
# This scrape cannot be a job of the unified scheduler
# (scripts/scheduler.py): the SBS site sits behind the Imperva WAF and
# only a REAL, VISIBLE Chrome with a persistent profile gets through,
# so the process needs an interactive Windows session. A user-level
# task with -LogonType Interactive is exactly that; no admin rights.
#
#   powershell -ExecutionPolicy Bypass -File scripts\schedule_sbs_valor_cuota.ps1
#       registers it daily at 16:00 with retries at 16:30 and 17:00
#   ... -Time 19:30      another time
#   ... -Status          what is registered and how the last run went
#   ... -Test            run it now, in the foreground (Chrome opens)
#   ... -Remove          unregister it
#
# The task name is the one the standalone SPP monitor used, on
# purpose: registering here REPLACES the monitor's task (-Force), so
# two scrapers never compete for the same SBS page. The run's trail is
# data\spp\scrape.log and scrape.json.
# ---------------------------------------------------------------

[CmdletBinding()]
param(
  [string]$Time = "16:00",
  [switch]$Status,
  [switch]$Test,
  [switch]$Remove
)

$ErrorActionPreference = "Stop"
$Root     = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
$Python   = Join-Path $Root ".venv\Scripts\python.exe"
$Script   = Join-Path $Root "scripts\run_sbs_valor_cuota.py"
$TaskName = "Profuturo - Valor cuota SPP"

function Show-Status {
  $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if (-not $t) { Write-Output "No task named '$TaskName' is registered."; return }
  $i = $t | Get-ScheduledTaskInfo
  $trigger = ($t.Triggers | ForEach-Object { $_.StartBoundary }) -join ", "
  $rep = $t.Triggers[0].Repetition
  Write-Output "Task       : $($t.TaskName)"
  Write-Output "Action     : $($t.Actions[0].Execute) $($t.Actions[0].Arguments)"
  Write-Output "State      : $($t.State)"
  Write-Output "Scheduled  : $trigger (local time), repeating every $($rep.Interval) for $($rep.Duration)"
  Write-Output "Next run   : $($i.NextRunTime)"
  Write-Output "Last run   : $($i.LastRunTime)  result $($i.LastTaskResult)"
  Write-Output "Missed runs: $($i.NumberOfMissedRuns)"
}

if ($Status) { Show-Status; exit 0 }

if ($Remove) {
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Task '$TaskName' removed. The scrape no longer runs on its own."
  } else {
    Write-Output "Nothing to remove."
  }
  exit 0
}

if ($Test) {
  Write-Output "Running the scrape now (Chrome will open)..."
  & $Python $Script --scheduled
  Write-Output "Exit code: $LASTEXITCODE"
  exit $LASTEXITCODE
}

if (-not (Test-Path $Python)) { throw "Environment not found at $Python" }
if (-not (Test-Path $Script)) { throw "Script not found: $Script" }
if ($Time -notmatch '^([01]?\d|2[0-3]):[0-5]\d$') { throw "Invalid time: $Time. Use HH:mm." }

# The SBS WAF requires REAL Chrome (Playwright's Chromium is blocked).
# Warning here beats discovering it at 16:00 in an unattended run.
$chrome = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) {
  Write-Warning ("Google Chrome does not appear to be installed. The scrape needs it " +
                 "(the SBS WAF rejects headless browsers and Chromium). Install it " +
                 "before relying on the daily run.")
}

$action = New-ScheduledTaskAction -Execute $Python `
            -Argument "`"$Script`" --scheduled" -WorkingDirectory $Root

$trigger = New-ScheduledTaskTrigger -Daily -At $Time

# Three attempts: on time, half an hour later, an hour later. The SBS
# publishes t-2 business days "in the afternoon" with no fixed hour; if
# it is not there at 16:00 the retries pick it up. Every attempt first
# asks whether the book already holds that day (run_scheduled) and, if
# so, does not open Chrome: the retries cost a second when the first one
# landed. If none brings anything, the day is left alone until tomorrow.
# New-ScheduledTaskTrigger only accepts -RepetitionInterval with -Once,
# so the repetition is taken from a one-shot trigger and copied over.
$repetition = (New-ScheduledTaskTrigger -Once -At $Time `
                 -RepetitionInterval (New-TimeSpan -Minutes 30) `
                 -RepetitionDuration (New-TimeSpan -Hours 1)).Repetition
$trigger.Repetition = $repetition

# StartWhenAvailable recovers the run if the machine was off at that
# time. The 30-minute limit only exists so a Chrome stuck on the WAF
# does not run forever.
$settings = New-ScheduledTaskSettingsSet `
              -StartWhenAvailable `
              -DontStopIfGoingOnBatteries `
              -AllowStartIfOnBatteries `
              -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
              -MultipleInstances IgnoreNew

# The principal is identified by SID, not "DOMAIN\user": on a laptop
# joined to Entra/Azure AD $env:USERDOMAIN is "AzureAD" and
# Register-ScheduledTask aborts with "No mapping between account names
# and security IDs was done", leaving the task unregistered. The SID
# always resolves, and the Task Scheduler shows the name anyway.
$who = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$principal = New-ScheduledTaskPrincipal -UserId $who `
               -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
  -Settings $settings -Principal $principal -Force `
  -Description ("Daily SPP valor cuota scrape from the SBS (pgetl pipeline). " +
                "Inserts what is missing; never overwrites. " +
                "Trail in data\spp\scrape.log.") | Out-Null

Write-Output "Registered: '$TaskName', daily at $Time with retries at +30 and +60 min, pointing at this repo."
Write-Output ""
Show-Status
Write-Output ""
Write-Output "The SBS requires a visible Chrome, so the Windows session must be"
Write-Output "signed in at that time. If the machine is off, the run is postponed"
Write-Output "and recovered when it comes back (StartWhenAvailable)."
Write-Output ""
Write-Output "FIRST time on this machine: run"
Write-Output "  powershell -ExecutionPolicy Bypass -File scripts\schedule_sbs_valor_cuota.ps1 -Test"
Write-Output "with an operator watching. The Chrome profile starts empty and the WAF"
Write-Output "challenge has to be solved once by hand; only then can the $Time run"
Write-Output "work unattended."
