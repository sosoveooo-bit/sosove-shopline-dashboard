#!/usr/bin/env bash
# CI-only: build an isolated copy with private NAS-style file permissions.
set -Eeuo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
test_context="$(mktemp -d "${RUNNER_TEMP:-/tmp}/sosove-permissions.XXXXXX")"
container_id=''
cleanup() {
    if [[ "$container_id" =~ ^[0-9a-f]{64}$ ]]; then
        docker rm -f "$container_id" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

# The context contains committed public files only. It is inside runner temp,
# which GitHub removes with the job; no user workspace permissions are changed.
git -C "$project_root" archive HEAD | tar -x -C "$test_context"
chmod 0600 "$test_context/app.py"
find "$test_context/shopline_monitor" -type f -exec chmod 0600 {} +
find "$test_context/shopline_monitor" -type d -exec chmod 0700 {} +
test "$(stat -c '%a' "$test_context/app.py")" = 600
test "$(stat -c '%a' "$test_context/shopline_monitor/static")" = 700
docker build -t sosove-dashboard:restricted "$test_context"

docker run --rm -i --read-only --entrypoint python sosove-dashboard:restricted - < "$project_root/deploy/check_image_permissions.py"
container_id="$(docker run -d --read-only --tmpfs /tmp \
    -p 127.0.0.1:18003:8000 \
    --mount type=volume,source=sosove-ci-restricted-cache,target=/app/runtime \
    -e DASHBOARD_ACCESS_TOKEN=ci-smoke-token sosove-dashboard:restricted)"
python "$project_root/deploy/smoke_container.py" --url http://127.0.0.1:18003
docker restart "$container_id" >/dev/null
python "$project_root/deploy/smoke_container.py" --url http://127.0.0.1:18003
