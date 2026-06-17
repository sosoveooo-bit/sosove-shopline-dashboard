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

## Files

- `shopline_monitor/` - backend, static UI, and tests
- `docs/plans/` - design note for the dashboard
- `shopline-monitor-*.png` - UI previews

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
