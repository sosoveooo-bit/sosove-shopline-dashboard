#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/sosove-dashboard}"
REPO_URL="${REPO_URL:-https://github.com/sosoveooo-bit/sosove-shopline-dashboard.git}"
SERVICE_NAME="${SERVICE_NAME:-sosove-dashboard}"
APP_PORT="${APP_PORT:-8787}"
SERVER_NAME="${1:-${SERVER_NAME:-_}}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root: sudo bash deploy/install_ubuntu.sh [domain-or-ip]"
  exit 1
fi

if [[ -z "${APP_DIR}" || "${APP_DIR}" != /* || "${APP_DIR}" == "/" || "${APP_DIR}" == "/opt" ]]; then
  echo "Invalid APP_DIR: ${APP_DIR}"
  exit 1
fi

echo "[1/8] Installing system packages"
apt-get update
apt-get install -y ca-certificates curl git nginx python3 python3-venv python3-pip

echo "[2/8] Syncing repository"
mkdir -p "$(dirname "${APP_DIR}")"
if [[ -d "${APP_DIR}/.git" ]]; then
  git -C "${APP_DIR}" pull --ff-only
else
  TMP_ENV=""
  if [[ -f "${APP_DIR}/.env" ]]; then
    TMP_ENV="$(mktemp)"
    cp "${APP_DIR}/.env" "${TMP_ENV}"
  fi
  rm -rf "${APP_DIR:?}"
  git clone "${REPO_URL}" "${APP_DIR}"
  if [[ -n "${TMP_ENV}" ]]; then
    cp "${TMP_ENV}" "${APP_DIR}/.env"
    rm -f "${TMP_ENV}"
  fi
fi

echo "[3/8] Installing Python dependencies"
python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

echo "[4/8] Preparing .env and secrets directory"
if [[ ! -f "${APP_DIR}/.env" ]]; then
  cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
  chmod 600 "${APP_DIR}/.env"
  echo "Created ${APP_DIR}/.env from .env.example. Fill real Shopline / GA4 values before expecting live data."
else
  chmod 600 "${APP_DIR}/.env"
  echo "Keeping existing ${APP_DIR}/.env"
fi
mkdir -p "${APP_DIR}/secrets"
chmod 700 "${APP_DIR}/secrets"

echo "[5/8] Installing systemd service"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=SOSOVE Shopline Dashboard
After=network.target

[Service]
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python -m shopline_monitor.server --host 127.0.0.1 --port ${APP_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo "[6/8] Configuring Nginx"
cat > "/etc/nginx/sites-available/${SERVICE_NAME}" <<EOF
server {
    listen 80;
    server_name ${SERVER_NAME};

    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

ln -sfn "/etc/nginx/sites-available/${SERVICE_NAME}" "/etc/nginx/sites-enabled/${SERVICE_NAME}"
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 'Nginx Full'
fi

echo "[7/8] Checking local service"
for attempt in 1 2 3 4 5; do
  if curl -fsS "http://127.0.0.1:${APP_PORT}/api/health" >/dev/null; then
    echo "Service health check passed"
    break
  fi
  if [[ "${attempt}" -eq 5 ]]; then
    echo "Service health check failed. Run: journalctl -u ${SERVICE_NAME} -n 80 --no-pager"
    exit 1
  fi
  sleep 2
done

echo "[8/8] Done"
systemctl --no-pager --full status "${SERVICE_NAME}" || true
echo
echo "Open: http://${SERVER_NAME}"
echo "Edit env: sudo nano ${APP_DIR}/.env"
echo "Restart: sudo systemctl restart ${SERVICE_NAME}"
