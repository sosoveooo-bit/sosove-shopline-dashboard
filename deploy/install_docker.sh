#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL='https://github.com/sosoveooo-bit/sosove-shopline-dashboard.git'
INSTALL_DIR="${SOSOVE_INSTALL_DIR:-/opt/sosove-dashboard-docker-source}"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

validate_directory() {
    [[ "$INSTALL_DIR" == /* && "$INSTALL_DIR" != *$'\n'* ]] || fail 'Installation path must be absolute.'
    [[ ! -L "$INSTALL_DIR" ]] || fail 'Installation directory cannot be a symbolic link.'
    INSTALL_DIR="$(realpath -m -- "$INSTALL_DIR")"
    case "$INSTALL_DIR" in /|/opt|/srv|/root|/tmp|/home|/usr|/var|/etc) fail 'Refusing a broad system directory.' ;; esac
}

install_prerequisites() {
    if ! command -v curl >/dev/null || ! command -v git >/dev/null || ! command -v python3 >/dev/null; then
        apt-get update
        apt-get install -y ca-certificates curl git python3
    fi
}

ensure_docker() {
    if ! command -v docker >/dev/null; then
        local package
        for package in docker.io docker-compose docker-compose-v2 podman-docker containerd runc; do
            if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
                fail "Existing $package installation found; install compatible Docker Compose without removing other services."
            fi
        done
        apt-get update
        apt-get install -y ca-certificates curl
        install -d -m 0755 /etc/apt/keyrings
        if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
            curl -fsSL --retry 3 https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
            chmod a+r /etc/apt/keyrings/docker.asc
        fi
        if [[ ! -f /etc/apt/sources.list.d/docker.sources ]]; then
            printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
                "$VERSION_CODENAME" "$(dpkg --print-architecture)" > /etc/apt/sources.list.d/docker.sources
        fi
        apt-get update
        apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
        systemctl enable --now docker
    fi
    docker compose version >/dev/null || fail 'Docker exists but Compose v2 is missing. See docs/docker-deploy.md.'
    if ! docker info >/dev/null 2>&1; then
        systemctl start docker
    fi
    docker info >/dev/null 2>&1 || fail 'Docker daemon is not available.'
}

sync_repository() {
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        [[ "$(git -C "$INSTALL_DIR" remote get-url origin)" == "$REPO_URL" ]] || fail 'Destination belongs to a different repository.'
        [[ -z "$(git -C "$INSTALL_DIR" status --porcelain)" ]] || fail 'Local code changes found; refusing to overwrite them.'
        [[ "$(git -C "$INSTALL_DIR" branch --show-current)" == main ]] || fail 'Expected main branch; keep your branch unchanged.'
        git -C "$INSTALL_DIR" pull --ff-only origin main
    else
        if [[ -e "$INSTALL_DIR" ]]; then
            [[ -d "$INSTALL_DIR" ]] || fail 'Destination is not a directory.'
            [[ -z "$(find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]] || fail 'Destination is not empty; nothing was removed.'
        fi
        mkdir -p "$(dirname "$INSTALL_DIR")"
        GIT_TERMINAL_PROMPT=0 git clone --branch main --single-branch "$REPO_URL" "$INSTALL_DIR"
    fi
}

main() {
    [[ "${EUID}" -eq 0 ]] || fail 'Run this installer as root (sudo bash install_docker.sh).'
    [[ -r /etc/os-release ]] || fail 'Ubuntu 22.04/24.04/26.04 is required.'
    # Only source the trusted OS release file, never the dashboard .env.
    . /etc/os-release
    [[ "$ID" == ubuntu && "$VERSION_ID" =~ ^(22\.04|24\.04|26\.04)$ ]] || fail 'This installer supports Ubuntu 22.04/24.04/26.04.'
    validate_directory
    printf 'Installing SOSOVE Docker dashboard in %s. Existing websites will not be stopped.\n' "$INSTALL_DIR"
    install_prerequisites
    ensure_docker
    sync_repository
    cd "$INSTALL_DIR"
    [[ ! -L secrets ]] || fail 'Refusing a symbolic-link secrets directory.'
    if [[ ! -d secrets ]]; then
        install -d -m 0750 -o root -g 10001 secrets
    fi
    if [[ "${SOSOVE_INSTALL_NONINTERACTIVE:-0}" == 1 ]]; then
        python3 deploy/configure_docker.py "$INSTALL_DIR" --non-interactive
    else
        [[ -r /dev/tty ]] || fail 'An interactive SSH terminal is required to enter credentials.'
        python3 deploy/configure_docker.py "$INSTALL_DIR" </dev/tty
    fi

    local connection bind_ip port
    connection="$(python3 deploy/configure_docker.py "$INSTALL_DIR" --connection)"
    read -r bind_ip port <<< "$connection"
    local -a compose=(docker compose -f docker-compose.yml -f compose.build.yml)
    "${compose[@]}" config --quiet
    if [[ -z "$("${compose[@]}" ps -q dashboard)" ]]; then
        python3 - "$bind_ip" "$port" <<'PY'
import socket, sys
with socket.socket() as listener:
    try:
        listener.bind((sys.argv[1], int(sys.argv[2])))
    except OSError:
        sys.exit('Port is busy. Change DASHBOARD_PORT in .env and rerun; no process was stopped.')
PY
    fi

    # Build first; if a build fails, an existing container keeps running.
    "${compose[@]}" build --pull
    "${compose[@]}" up -d --pull never
    local healthy=0
    for attempt in $(seq 1 30); do
        if curl -fsS --max-time 3 "http://127.0.0.1:$port/api/health" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("ok") is True and d.get("service") == "shopline-monitor" else 1)' 2>/dev/null; then
            healthy=1
            break
        fi
        sleep 2
    done
    [[ "$healthy" == 1 ]] || fail "Health check failed. Inspect: cd $INSTALL_DIR && docker compose logs --tail=80 dashboard"
    "${compose[@]}" ps
    printf '\nInstallation completed. Configuration: %s/.env\n' "$INSTALL_DIR"
    if [[ "$bind_ip" == 0.0.0.0 ]]; then
        printf 'Open http://YOUR_VPS_IP:%s/ and use the dashboard password you entered.\n' "$port"
        printf 'Restrict TCP %s to your IP in the cloud security group; use HTTPS for long-term access.\n' "$port"
    else
        printf 'Local endpoint: http://127.0.0.1:%s/\n' "$port"
        printf 'On your computer: ssh -N -L 18000:127.0.0.1:%s root@YOUR_VPS_IP\n' "$port"
        printf 'Then open http://127.0.0.1:18000/ or configure your existing HTTPS reverse proxy.\n'
    fi
    printf 'GA4 is optional. Its container key path is /app/secrets/ga.json; see docs/docker-deploy.md.\n'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    trap 'printf "Installation stopped at line %s. Existing code/configuration were not deleted.\n" "$LINENO" >&2' ERR
    main "$@"
fi
