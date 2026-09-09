# Local Dashboard Operations

URL: http://127.0.0.1:8787/

The Windows task `SOSOVE Shopline Dashboard` starts `run_local_panel.ps1`, which runs the local Python supervisor independently of Codex. The existing task runs at login and retries every minute if no instance is running. It does not stop on battery power.

The supervisor checks `/api/health` every 10 seconds with a 3-second timeout. Three consecutive failures terminate only its owned server process tree; another server is started after 10 seconds. A slow GA4 or Shopline report alone does not trigger a health restart. The computer must remain awake and the configured Windows user logged in.

Logs are UTF-8 in `shopline_monitor/runtime_logs/local-supervisor.log`, with 2 MB rotation and three backups. Older scheduled-task logs are preserved.

Dashboard snapshots are private, query-specific files under `.cache/shopline-monitor/`. They are excluded from Git. Dates, filters and configuration fingerprints are part of the key. Refresh failures preserve the last complete disk copy; fresh Shopline orders can still be shown when only GA4 fails. Initial queries without a snapshot must wait for upstream data. Subsequent visits show the saved timestamp while refreshing.

Useful PowerShell commands:

```powershell
Get-ScheduledTask -TaskName 'SOSOVE Shopline Dashboard'
Start-ScheduledTask -TaskName 'SOSOVE Shopline Dashboard'
Invoke-RestMethod 'http://127.0.0.1:8787/api/health'
Get-Content 'shopline_monitor/runtime_logs/local-supervisor.log' -Tail 50 -Encoding utf8
```

To intentionally keep the panel stopped, disable the scheduled task before stopping it; otherwise the next one-minute trigger starts it again:

```powershell
Disable-ScheduledTask -TaskName 'SOSOVE Shopline Dashboard'
Stop-ScheduledTask -TaskName 'SOSOVE Shopline Dashboard'
```

To restore automatic startup, enable the task and start it. Do not launch another foreground copy on port 8787 while the task is running.
