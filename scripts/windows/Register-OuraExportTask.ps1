<#
  Register a Windows Scheduled Task that runs `oura-export fetch` once a day.

  Your Windows desktop is "almost always on", which makes it the more reliable
  host of the two. This task is set to run whether or not you are logged in and
  to run as soon as possible after a missed start (e.g. the PC was off).

  Usage (in an elevated PowerShell, from the repo root):

      .\scripts\windows\Register-OuraExportTask.ps1 `
          -OuraExportExe "C:\path\to\venv\Scripts\oura-export.exe" `
          -At "11:00"

  Remove it later with:
      Unregister-ScheduledTask -TaskName "OuraExport" -Confirm:$false
#>

param(
    [Parameter(Mandatory = $true)] [string] $OuraExportExe,
    [string] $At = "11:00",
    [string] $TaskName = "OuraExport"
)

if (-not (Test-Path $OuraExportExe)) {
    throw "oura-export executable not found at: $OuraExportExe"
}

$action   = New-ScheduledTaskAction -Execute $OuraExportExe -Argument "fetch"
$trigger  = New-ScheduledTaskTrigger -Daily -At $At

# Run even on battery, and catch up a missed run after the PC wakes/powers on.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -WakeToRun

Register-ScheduledTask `
    -TaskName   $TaskName `
    -Action     $action `
    -Trigger    $trigger `
    -Settings   $settings `
    -Description "Daily download of my Oura data export" `
    -Force

Write-Host "Registered scheduled task '$TaskName' to run '$OuraExportExe fetch' daily at $At."
Write-Host "Test it now with:  Start-ScheduledTask -TaskName $TaskName"
