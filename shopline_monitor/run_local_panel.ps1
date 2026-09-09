$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project Python was not found: $python"
}

Set-Location -LiteralPath $projectRoot

# The supervisor owns UTF-8 rotating logs and checks HTTP health independently
# of Shopline/GA4 report latency.
& $python -u -m shopline_monitor.local_supervisor --port 8787
exit $LASTEXITCODE
