# SOSOVE Shopline Dashboard

A small full-stack dashboard for monitoring Shopline store data.

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

SHOPLINE_API_VERSION=v20260301
SHOPLINE_STORE_DOMAIN=jp-sosove.myshopline.com
SHOPLINE_TOKEN_HEADER=Authorization
SHOPLINE_AUTH_PREFIX=Bearer
SHOPLINE_DEFAULT_CURRENCY=JPY
SHOPLINE_TIMEZONE=Asia/Tokyo
SHOPLINE_MAX_ORDER_PAGES=10
SHOPLINE_TIMEOUT_SECONDS=12

SHOPLINE_CONVERSION_TRAFFIC_FIELD=visitors
SHOPLINE_TRAFFIC_JSON={}

GA4_PROPERTY_ID=
GA4_KEY_EVENT_NAME=purchase
GA4_CONVERSION_METRIC=userKeyEventRate
GA4_CONVERSION_MODE=key_event_rate
GA4_SERVICE_ACCOUNT_FILE=
GA4_SERVICE_ACCOUNT_JSON=
GA4_TIMEOUT_SECONDS=12

SHOPLINE_PRODUCT_COST_RATE=0.35
SHOPLINE_PAYMENT_FEE_RATE=0.036
SHOPLINE_SHIPPING_COST_PER_ORDER=500

SHOPLINE_AD_SPEND_JSON={"Facebook":0,"Instagram":0,"Google":0,"TikTok":0,"Email":0,"Direct":0,"Organic":0,"Ad":0}
```

`SHOPLINE_TIMEZONE` controls the "today" boundary for order queries. Increase `SHOPLINE_MAX_ORDER_PAGES` if a selected period has more than 1,000 orders.

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
SHOPLINE_TIMEZONE=Asia/Tokyo

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

This repo includes a `Dockerfile`, `docker-compose.yml`, and a GitHub Actions workflow that publishes an image to GitHub Container Registry:

```text
ghcr.io/sosoveooo-bit/sosove-shopline-dashboard:latest
```

After GitHub Actions finishes, deploy on a VPS:

```bash
mkdir -p /opt/sosove-dashboard
cd /opt/sosove-dashboard
curl -O https://raw.githubusercontent.com/sosoveooo-bit/sosove-shopline-dashboard/main/docker-compose.yml
nano .env
docker compose pull
docker compose up -d
```

The commands above work without `docker login` after the GHCR package is public.
Open the package page, go to **Package settings -> Danger Zone -> Change visibility**, and set it to **Public**:

```text
https://github.com/users/sosoveooo-bit/packages/container/package/sosove-shopline-dashboard
```

If the package stays private, use `docker login ghcr.io -u sosoveooo-bit` and enter a GitHub personal access token with `read:packages` as the password.

Example `.env`:

```bash
SHOPLINE_API_BASE_URL=https://jp-sosove.myshopline.com
SHOPLINE_ACCESS_TOKEN=your-shopline-api-token
SHOPLINE_ORDERS_ENDPOINT=/orders
SHOPLINE_PRODUCTS_ENDPOINT=/products
SHOPLINE_API_VERSION=v20260301
SHOPLINE_STORE_DOMAIN=jp-sosove.myshopline.com
SHOPLINE_DEFAULT_CURRENCY=JPY
SHOPLINE_TIMEZONE=Asia/Tokyo
SHOPLINE_MAX_ORDER_PAGES=10
SHOPLINE_CONVERSION_TRAFFIC_FIELD=visitors
SHOPLINE_TRAFFIC_JSON={}
GA4_PROPERTY_ID=123456789
GA4_KEY_EVENT_NAME=purchase
GA4_CONVERSION_METRIC=userKeyEventRate
GA4_CONVERSION_MODE=key_event_rate
GA4_SERVICE_ACCOUNT_FILE=C:\path\to\ga4-service-account.json
SHOPLINE_PRODUCT_COST_RATE=0.35
SHOPLINE_PAYMENT_FEE_RATE=0.036
SHOPLINE_SHIPPING_COST_PER_ORDER=500
SHOPLINE_AD_SPEND_JSON={"Facebook":0,"Instagram":0,"Google":0,"TikTok":0,"Email":0,"Direct":0,"Organic":0,"Ad":0}
```

The container listens on port `8000`, so open `http://your-server-ip:8000/` or put Nginx in front of it.

Build locally if needed:

```bash
docker build -t sosove-shopline-dashboard .
docker run --rm -p 8000:8000 --env-file .env sosove-shopline-dashboard
```
