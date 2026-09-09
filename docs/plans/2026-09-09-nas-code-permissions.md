# NAS Image Code Permission Fix

The supplied container log shows exit code 1, repeated restarts, no OOM, and `PermissionError: [Errno 13] Permission denied: '/app/app.py'` during Uvicorn module import. This confirms a runtime code-read permission failure. A restrictive NAS checkout is a compatible explanation; the host's exact umask was not measured.

Docker COPY preserved host file modes while the runtime uses UID 10001. Normalize only image application code to directories 0755 and files 0644 after COPY, before switching to the non-root user. Keep code root-owned and not writable by the runtime. Do not loosen `.env`, mounted GA4 credentials, runtime data or NAS storage permissions.

Regression coverage builds an isolated public source copy with `app.py` and package files 0600 and package directories 0700. On both native ARM64 and AMD64, verify actual default-user access, exact modes, no code write permission, app import, HTTP/auth/static responses, persisted background snapshots and restart. No commands are executed on the user's NAS during this fix; publish an update/rebuild command instead.
