# SOSOVE Shopline Dashboard

A small full-stack dashboard for monitoring Shopline store data.

## Docker 部署入口

### 一条命令安装

在 Ubuntu 22.04/24.04/26.04 的 **root SSH 终端**执行（需要能访问 GitHub 和已安装 curl）：

```bash
(sosove_installer=$(mktemp) && curl -fsSL https://raw.githubusercontent.com/sosoveooo-bit/sosove-shopline-dashboard/main/deploy/install_docker.sh -o "$sosove_installer" && bash "$sosove_installer")
```

命令会以 root 权限运行本仓库的[安装脚本](deploy/install_docker.sh)，自动安装缺少的 Docker 组件、下载源码、询问配置并构建启动。Shopline Token 和面板密码由你输入；公网 IP 访问时按提示填写 `0.0.0.0` 并在云安全组放行所选端口，默认 `8000`。默认安装目录为 `/opt/sosove-dashboard-docker-source`。已有配置会保留，旧 Nginx/systemd 面板不会被停止；GA4 私钥可以稍后按教程配置。

完整中文教程：[Ubuntu VPS Docker 部署](docs/docker-deploy.md)

最新版本包含 SmartPush 点击查看订单、Yahoo 合并订单分页、GA4 同口径周期转化率，以及后台快照更新。容器与本地面板使用同一套后端逻辑。

Docker 镜像不包含密钥。使用服务器上的 `.env` 配置 Shopline，并把 GA4 私钥挂载为 `/app/secrets/ga.json`。默认端口为 `127.0.0.1:8000`，避免直接暴露订单数据；公网访问及现有 Nginx 的切换步骤见教程。

## Run locally

```powershell
python -m shopline_monitor.server --port 8787
```

Then open `http://127.0.0.1:8787/`.

## Shopline env vars

The app automatically loads `.env` from the project root. Copy `.env.example` to `.env`, then fill in your real Shopline token.

```bash
SHOPLINE_API_BASE_URL=https://jp-sosove.myshopline.com
SHOPLINE_ACCESS_TOKEN=your-shopline-api-token
SHOPLINE_ORDERS_ENDPOINT=/orders
SHOPLINE_PRODUCTS_ENDPOINT=/products
SHOPLINE_ORDER_ATTRIBUTION_ENDPOINT=/orders/order_attribution_info.json

SHOPLINE_API_VERSION=v20260301
SHOPLINE_STORE_DOMAIN=jp-sosove.myshopline.com
SHOPLINE_STOREFRONT_DOMAINS=sosove.com
SHOPLINE_TOKEN_HEADER=Authorization
SHOPLINE_AUTH_PREFIX=Bearer
SHOPLINE_DEFAULT_CURRENCY=JPY
SHOPLINE_TIMEZONE=Asia/Shanghai
SHOPLINE_MAX_ORDER_PAGES=10
SHOPLINE_MAX_PRODUCT_PAGES=25
SHOPLINE_ORDER_CHUNK_DAYS=3
SHOPLINE_TIMEOUT_SECONDS=30
SHOPLINE_RETRY_ATTEMPTS=3
DASHBOARD_CACHE_SECONDS=120

SHOPLINE_CONVERSION_TRAFFIC_FIELD=visitors
SHOPLINE_TRAFFIC_JSON={}

GA4_PROPERTY_ID=
GA4_KEY_EVENT_NAME=purchase
GA4_CONVERSION_METRIC=userKeyEventRate
GA4_CONVERSION_MODE=key_event_rate
GA4_SERVICE_ACCOUNT_FILE=
GA4_SERVICE_ACCOUNT_JSON=
GA4_TIMEOUT_SECONDS=30

SHOPLINE_PRODUCT_COST_RATE=0.35
SHOPLINE_PAYMENT_FEE_RATE=0.036
SHOPLINE_SHIPPING_COST_PER_ORDER=500
SHOPLINE_SKU_COST_JSON={"SKU-001":1200}
SHOPLINE_SHIPPING_COST_BY_MARKET_JSON={"JP":500}
DASHBOARD_ACCESS_TOKEN=
DASHBOARD_ROLE=admin

SHOPLINE_AD_SPEND_JSON={"Facebook":0,"Instagram":0,"Google":0,"TikTok":0,"Email":0,"Direct":0,"Organic":0,"Ad":0}
```

`SHOPLINE_TIMEZONE` controls the "today" boundary for order queries. Production defaults use three-day order windows and up to 10 pages per window, preventing high-volume 30-day comparisons from being truncated.

Channel orders use SHOPLINE's official last-touch attribution first. GA4 aliases such as `smartpush/email` and `wangao/wangao` are normalized to SmartPush. The channel table exposes both SHOPLINE orders/GA4 sessions and GA4 Purchase/sessions, plus an order-level audit dialog for UTM and Campaign verification.

Transient SHOPLINE requests retry automatically. A failed refresh keeps the last successful live snapshot instead of replacing real data with demo orders. Historical order windows are cached so subsequent live refreshes only request the current window.

Conversion rate is calculated as `orders / visitors * 100` by default. Shopline order/product APIs do not include store visitor counts, so live dashboards show `--` until traffic data is configured. Add daily traffic from Shopline analytics, GA4, or another traffic source:

```bash
SHOPLINE_TRAFFIC_JSON={"2026-06-17":{"visitors":1200,"sessions":1350}}
SHOPLINE_CONVERSION_TRAFFIC_FIELD=visitors
```

Set `SHOPLINE_CONVERSION_TRAFFIC_FIELD=sessions` if you want to match a sessions-based analytics report.

## GA4 conversion rate

To show conversion rate from GA4, enable the Google Analytics Data API and give a service account Viewer access to the GA4 property.

1. In Google Cloud, create or choose a project, enable **Google Analytics Data API**, then create a service account key as JSON.
2. In GA4 Admin, open **Property access management**, add the service account email, and grant **Viewer** access.
3. In GA4 Admin, copy the numeric **Property ID**.
4. Make sure your purchase event is marked as a key event. The default key event name used by this app is `purchase`.

Then add one of these credential styles to `.env`:

```bash
GA4_PROPERTY_ID=123456789
GA4_KEY_EVENT_NAME=purchase
GA4_CONVERSION_METRIC=userKeyEventRate
GA4_CONVERSION_MODE=key_event_rate
GA4_SERVICE_ACCOUNT_FILE=C:\path\to\ga4-service-account.json
```

Or paste the JSON into one line:

```bash
GA4_PROPERTY_ID=123456789
GA4_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"..."}
GA4_CONVERSION_MODE=key_event_rate
```

Restart the app after editing `.env`. The connector card will show GA4 as configured. By default the Conversion KPI uses pure GA4 user key event rate. Set `GA4_CONVERSION_METRIC=sessionKeyEventRate` if you want the session-based GA4 rate, or set `GA4_CONVERSION_MODE=shopline_orders_over_sessions` only if you want Shopline realtime orders divided by GA4 sessions.

## Files

- `shopline_monitor/` - backend, static UI, and tests
- `docs/plans/` - design note for the dashboard
- `shopline-monitor-*.png` - UI previews

## 最简单：Ubuntu VPS 一键部署

推荐用这个方式部署到 VPS。它会自动安装 Python、Nginx，拉取 GitHub 代码，创建 systemd 服务，并把网站代理到 80 端口。

在 VPS 里执行：

```bash
curl -fsSL https://raw.githubusercontent.com/sosoveooo-bit/sosove-shopline-dashboard/main/deploy/install_ubuntu.sh -o /tmp/sosove-install.sh
sudo bash /tmp/sosove-install.sh 你的域名或服务器IP
sudo nano /opt/sosove-dashboard/.env
sudo systemctl restart sosove-dashboard
```

如果你没有域名，第二行直接填服务器 IP。部署完成后打开：

```text
http://你的域名或服务器IP/
```

`.env` 里最少要填这些值，才能抓真实数据：

```bash
SHOPLINE_API_BASE_URL=https://jp-sosove.myshopline.com
SHOPLINE_ACCESS_TOKEN=你的Shopline API token
SHOPLINE_ORDERS_ENDPOINT=/orders
SHOPLINE_PRODUCTS_ENDPOINT=/products
SHOPLINE_API_VERSION=v20260301
SHOPLINE_STORE_DOMAIN=jp-sosove.myshopline.com
SHOPLINE_TIMEZONE=Asia/Shanghai

GA4_PROPERTY_ID=你的GA4 Property ID
GA4_KEY_EVENT_NAME=purchase
GA4_CONVERSION_METRIC=userKeyEventRate
GA4_CONVERSION_MODE=key_event_rate
GA4_SERVICE_ACCOUNT_FILE=/opt/sosove-dashboard/secrets/ga4-service-account.json

SHOPLINE_PRODUCT_COST_RATE=0.35
SHOPLINE_PAYMENT_FEE_RATE=0.036
SHOPLINE_SHIPPING_COST_PER_ORDER=500
SHOPLINE_AD_SPEND_JSON={"Facebook":0,"Instagram":0,"Google":0,"TikTok":0,"Email":0,"Direct":0,"Organic":0,"Ad":0}
```

GA4 JSON 密钥不要放进 GitHub。先在 Windows PowerShell 上传到 VPS：

```powershell
scp "E:\ga4\你的GA4-service-account.json" root@你的服务器IP:/opt/sosove-dashboard/secrets/ga4-service-account.json
```

然后在 VPS 上执行：

```bash
sudo chmod 600 /opt/sosove-dashboard/secrets/ga4-service-account.json
sudo systemctl restart sosove-dashboard
```

更新代码时，重新跑安装脚本即可，它会自动 `git pull` 并重启服务：

```bash
sudo bash /opt/sosove-dashboard/deploy/install_ubuntu.sh 你的域名或服务器IP
```

常用排查命令：

```bash
sudo systemctl status sosove-dashboard
sudo journalctl -u sosove-dashboard -n 80 --no-pager
curl http://127.0.0.1:8787/api/health
```

如果你绑定了域名并需要 HTTPS：

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d 你的域名
```

## Deploy to Vercel

1. Push this repository to GitHub.
2. In Vercel, choose **New Project** and import this GitHub repo.
3. Add the Shopline environment variables in **Project Settings -> Environment Variables**.
4. Deploy.

Vercel uses `api/index.py` as the Python Function entrypoint and rewrites all routes to the FastAPI app in `app.py`. Every push to the connected branch triggers a new deployment.

## Deploy with Docker

Follow the [complete Chinese Docker guide](docs/docker-deploy.md). GitHub Actions tests the code, starts a synthetic-data container, and publishes only after the smoke checks pass:

```text
ghcr.io/sosoveooo-bit/sosove-shopline-dashboard:latest
```

For a new deployment directory, download the Compose file and configuration template:

```bash
mkdir -p /opt/sosove-dashboard-docker
cd /opt/sosove-dashboard-docker
curl -fsSL https://raw.githubusercontent.com/sosoveooo-bit/sosove-shopline-dashboard/main/docker-compose.yml -o docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/sosoveooo-bit/sosove-shopline-dashboard/main/.env.example -o .env.example
test -f .env || cp .env.example .env
install -d -m 0750 -o root -g 10001 secrets
chmod 600 .env
nano .env
```

Fill `SHOPLINE_ACCESS_TOKEN` and a random `DASHBOARD_ACCESS_TOKEN`. GA4 is optional: when enabled, upload `secrets/ga.json`, set `GA4_SERVICE_ACCOUNT_FILE=/app/secrets/ga.json`, and follow the group/permissions instructions in the guide. Then:

```bash
docker compose config --quiet
docker compose pull
docker compose up -d
curl -fsS http://127.0.0.1:8000/api/health
```

The container runs as UID/GID `10001`, with read-only code and credentials and a writable named volume for snapshots. `docker compose down` preserves that volume; `down -v` deletes it. Docker restarts exited containers, but an `unhealthy` status alone does not trigger a restart.

Public GHCR images support anonymous pulls. For `unauthorized`, see the guide's source-build fallback using `compose.build.yml`. The default published image supports `linux/amd64`; ARM hosts can build from source. Use a full commit SHA as `DASHBOARD_IMAGE_TAG` to pin or roll back to a successfully published revision.
