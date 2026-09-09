param(
    [string]$TaskName = "SOSOVE Shopline Dashboard"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"

if (-not (Test-Path -LiteralPath $Pythonw)) {
    throw "Virtual environment Python was not found: $Pythonw"
}

$Action = New-ScheduledTaskAction `
    -Execute $Pythonw `
    -Argument "-m shopline_monitor.task_runner" `
    -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 99 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$Task = New-ScheduledTask `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Runs the local SOSOVE Shopline dashboard independently of Codex."

Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Output "Registered and started scheduled task: $TaskName"
