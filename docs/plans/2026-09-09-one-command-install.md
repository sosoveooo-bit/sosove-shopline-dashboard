# One-Command Docker Installer

## Scope

Provide one downloadable Ubuntu installation command for the existing dashboard. The installer is published to the authorized repository; it is not run on the user's VPS during this task.

## Design

- Source build avoids the existing GHCR anonymous-authentication problem. A longer shell one-liner alone would be hard to validate and preserve on reruns; a checked-in installer makes the steps testable.
- Interactive prompts collect store domain, hidden API token and hidden dashboard password. Port and network binding are explicit; loopback remains the default. New GA4 configuration is optional and documented separately.
- Install missing prerequisites from the official Docker Ubuntu repository. Do not uninstall or stop other Docker services, Nginx or the existing panel.
- Refuse broad destinations, symlink destinations, unrelated/nonempty directories, dirty repositories and non-main branches. Pull fast-forward only and build before replacing an existing container.
- Never source or evaluate `.env`. Preserve existing values, use atomic writes and private backups when completing a partial configuration, and do not print secrets.

## Verification

- Configuration tests: input validation, literal quoting, hidden input, preservation and backup, no missing-secret partial writes.
- Bash tests: syntax, path guards, existing-directory preservation.
- GitHub Ubuntu CI: run the installer twice using synthetic configuration and an isolated Compose project, verify health and unchanged `.env`, then remove the CI container (not the user's services).
