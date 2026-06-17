from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from shopline_monitor.backend import ShoplineClient, build_dashboard_payload, now_iso
from shopline_monitor.server import parse_date_param


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
    allow_headers=["Content-Type"],
)


def no_store_headers() -> dict[str, str]:
    return {"Cache-Control": "no-store"}


def error_json(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": message},
        status_code=status_code,
        headers=no_store_headers(),
    )


@app.get("/")
@app.get("/index.html")
def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        media_type="text/html; charset=utf-8",
        headers=no_store_headers(),
    )


@app.get("/static/{relative_path:path}")
def static_asset(relative_path: str) -> FileResponse | JSONResponse:
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


@app.get("/api/connector")
def connector() -> dict[str, Any]:
    return ShoplineClient().connector_status()


@app.get("/api/metrics")
def metrics(range: str = "7d", date: str = "") -> dict[str, Any]:
    return build_dashboard_payload(range, today=parse_date_param(date))


@app.post("/api/sync")
async def sync(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    range_key = str(payload.get("range", "7d"))
    selected_date = parse_date_param(str(payload.get("date", "")))
    return build_dashboard_payload(range_key, today=selected_date)


@app.post("/api/connector/test")
def test_connector() -> dict[str, Any]:
    return ShoplineClient().test_connection()
