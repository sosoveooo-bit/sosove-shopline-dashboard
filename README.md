# SOSOVE Shopline Dashboard

A small full-stack dashboard for monitoring Shopline store data.

## Run locally

```powershell
python -m shopline_monitor.server --port 8787
```

Then open `http://127.0.0.1:8787/`.

## Shopline env vars

```powershell
$env:SHOPLINE_API_BASE_URL = "https://jp-sosove.myshopline.com"
$env:SHOPLINE_ACCESS_TOKEN = "your-access-token"
$env:SHOPLINE_ORDERS_ENDPOINT = "/orders"
$env:SHOPLINE_PRODUCTS_ENDPOINT = "/products"
$env:SHOPLINE_DEFAULT_CURRENCY = "JPY"
$env:SHOPLINE_MAX_ORDER_PAGES = "5"
```

Optional cost settings:

```powershell
$env:SHOPLINE_PRODUCT_COST_RATE = "0.35"
$env:SHOPLINE_PAYMENT_FEE_RATE = "0.036"
$env:SHOPLINE_SHIPPING_COST_PER_ORDER = "500"
$env:SHOPLINE_AD_SPEND_JSON = '{"Facebook":12000,"Instagram":8000,"Google":5000,"TikTok":3000}'
```

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
SHOPLINE_ACCESS_TOKEN=your-access-token
SHOPLINE_ORDERS_ENDPOINT=/orders
SHOPLINE_PRODUCTS_ENDPOINT=/products
SHOPLINE_DEFAULT_CURRENCY=JPY
SHOPLINE_MAX_ORDER_PAGES=5
SHOPLINE_PRODUCT_COST_RATE=0.35
SHOPLINE_PAYMENT_FEE_RATE=0.036
SHOPLINE_SHIPPING_COST_PER_ORDER=500
SHOPLINE_AD_SPEND_JSON={"Facebook":12000,"Instagram":8000,"Google":5000,"TikTok":3000}
```

The container listens on port `8000`, so open `http://your-server-ip:8000/` or put Nginx in front of it.

Build locally if needed:

```bash
docker build -t sosove-shopline-dashboard .
docker run --rm -p 8000:8000 --env-file .env sosove-shopline-dashboard
```
