<#
  Register a Windows Scheduled Task that runs `oura-export run` every few hours.

  Each run is short: it signs in if needed (reading the code from Gmail),
  collects a finished export, and requests a new one about once a day. Running
  every few hours means a slow export is picked up soon after Oura finishes it.

  Usage (normal PowerShell, from the repo root, after setting up the venv):

      powershell -ExecutionPolicy Bypass -File scripts\windows\Register-OuraExportTask.ps1

  Options:  -EveryHours 3   -TaskName OuraExport   -Python <path to pythonw.exe>
  Remove:   Unregister-ScheduledTask -TaskName OuraExport -Confirm:$false

  The task runs while you are signed in to Windows (it needs your Credential
  Manager entry for the Gmail app password). Missed runs, e.g. while the PC
  was off, start as soon as it is back.
#>

param(
    [int]    $EveryHours = 3,
    [string] $TaskName = "OuraExport",
    [string] $Python = ""
)
$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $Python) { $Python = Join-Path $repo "venv\Scripts\pythonw.exe" }
if (-not (Test-Path $Python)) {
    throw "Python not found at $Python. Create the venv first (README, 'Install')."
}

# pythonw.exe runs without flashing a console window every few hours.
$action = New-ScheduledTaskAction -Execute $Python -Argument "-m oura_export run" -WorkingDirectory $repo

# A daily trigger that repeats every N hours for a day = every N hours, forever.
$trigger = New-ScheduledTaskTrigger -Daily -At "00:05"
$repeat  = New-ScheduledTaskTrigger -Once -At "00:05" `
    -RepetitionInterval (New-TimeSpan -Hours $EveryHours) -RepetitionDuration (New-TimeSpan -Days 1)
$trigger.Repetition = $repeat.Repetition

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Downloads my Oura data export (oura-export)." -Force | Out-Null

Write-Host "Registered '$TaskName': runs every $EveryHours h while you're signed in to Windows."
Write-Host "Run it now:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Check it:    oura-export status   (or open STATUS.txt in your data folder)"
