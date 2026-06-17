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
