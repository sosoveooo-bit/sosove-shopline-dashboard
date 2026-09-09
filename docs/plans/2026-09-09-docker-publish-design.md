# Dashboard Docker Publication

## Scope

Publish the latest local Shopline panel to its existing repository. Keep unrelated workspace projects, local API configuration, GA4 keys and order snapshots out of Git and container layers. Do not modify the live VPS.

## Decisions

- Preserve the existing FastAPI/Uvicorn Docker entrypoint and connect it to the local server's shared background-snapshot API. Keep Vercel responses blocking because serverless background execution is not guaranteed.
- Build and test an amd64 image in the existing GitHub Actions pipeline, then publish the same Dockerfile to GHCR with latest and full commit tags. Source-build Compose override is the fallback for private-registry/ARM cases.
- Require a separately configured dashboard token in Compose, bind loopback by default, mount GA4 credentials read-only, and persist snapshots in a named volume. Do not bake `.env` into the image.
- Provide Chinese Ubuntu instructions covering configuration, file permissions, direct IP or SSH tunnel access, existing Nginx, updates, rollback and troubleshooting.

## Verification

- Existing Python and executable JavaScript regressions.
- FastAPI contract tests for auth, background queries, serverless fallback and async thread offloading.
- Real Linux container smoke tests under Compose, including non-root access, writable snapshots, read-only secret mount and restart.
- Staged-file review and secret scan before Git push; compare the final remote commit and published image revision.
