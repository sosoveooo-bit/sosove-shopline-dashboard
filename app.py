from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from shopline_monitor.backend import (
    ShoplineClient,
    build_dashboard_payload,
    deliver_alert_webhook,
    now_iso,
    probe_integrations,
)
from shopline_monitor.server import (
    auth_status,
    dashboard_auth_enabled,
    dashboard_role,
    normalize_filter_payload,
    parse_date_param,
    verify_dashboard_token,
)


ROOT_DIR = Path(__file__).resolve().parent
STATIC_DIR = ROOT_DIR / "shopline_monitor" / "static"

app = FastAPI(
    title="SOSOVE Shopline Dashboard",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Dashboard-Token"],
)


@app.middleware("http")
async def add_no_store_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


def no_store_headers() -> dict[str, str]:
    return {"Cache-Control": "no-store"}


def error_json(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": message},
        status_code=status_code,
        headers=no_store_headers(),
    )


async def read_json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def require_api_access(request: Request, write: bool = False) -> JSONResponse | None:
    if not dashboard_auth_enabled():
        return None
    supplied = request.headers.get("X-Dashboard-Token", "")
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not verify_dashboard_token(supplied):
        return error_json(401, "dashboard authentication required")
    if write and dashboard_role() == "viewer":
        return error_json(403, "viewer role is read-only")
    return None


@app.get("/")
@app.get("/index.html")
def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        media_type="text/html; charset=utf-8",
        headers=no_store_headers(),
    )


@app.get("/static/{relative_path:path}")
def static_asset(relative_path: str) -> Any:
    target = (STATIC_DIR / relative_path).resolve()
    try:
        target.relative_to(STATIC_DIR.resolve())
    except ValueError:
        return error_json(400, "invalid static path")
    if not target.exists() or not target.is_file():
        return error_json(404, "file not found")

    media_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type, headers=no_store_headers())


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "shopline-monitor", "time": now_iso()}


@app.get("/api/auth/status")
def get_auth_status() -> dict[str, Any]:
    return auth_status()


@app.post("/api/auth/login")
async def login(request: Request) -> Any:
    payload = await read_json_body(request)
    supplied = str(payload.get("token") or "")
    if verify_dashboard_token(supplied):
        return {"ok": True, **auth_status()}
    return error_json(401, "access token is invalid")


@app.get("/api/connector")
def connector(request: Request) -> Any:
    denied = require_api_access(request)
    if denied:
        return denied
    return ShoplineClient().connector_status()


@app.get("/api/metrics")
def metrics(
    request: Request,
    range: str = "7d",
    date: str = "",
    channel: str = "",
    status: str = "",
    market: str = "",
    product: str = "",
) -> Any:
    denied = require_api_access(request)
    if denied:
        return denied
    filters = normalize_filter_payload(
        {"channel": channel, "status": status, "market": market, "product": product}
    )
    return build_dashboard_payload(range, today=parse_date_param(date), filters=filters)


@app.post("/api/sync")
async def sync(request: Request) -> Any:
    denied = require_api_access(request, write=True)
    if denied:
        return denied
    payload = await read_json_body(request)

    range_key = str(payload.get("range", "7d"))
    selected_date = parse_date_param(str(payload.get("date", "")))
    filters = normalize_filter_payload(payload.get("filters"))
    return build_dashboard_payload(
        range_key,
        today=selected_date,
        filters=filters,
        force_refresh=True,
    )


@app.post("/api/connector/test")
def test_connector(request: Request) -> Any:
    denied = require_api_access(request, write=True)
    if denied:
        return denied
    return probe_integrations()


@app.post("/api/alerts/notify")
async def notify_alert(request: Request) -> Any:
    denied = require_api_access(request, write=True)
    if denied:
        return denied
    payload = await read_json_body(request)
    raw_alert = payload.get("alert")
    if not isinstance(raw_alert, dict):
        return error_json(400, "alert payload is required")
    alert = {
        "id": str(raw_alert.get("id") or "")[:64],
        "level": str(raw_alert.get("level") or "info")[:24],
        "title": str(raw_alert.get("title") or "SOSOVE 数据预警")[:120],
        "message": str(raw_alert.get("message") or "")[:1200],
    }
    delivery = deliver_alert_webhook([alert], force_refresh=True, manual=True)
    return {"ok": bool(delivery.get("delivered")), **delivery}
