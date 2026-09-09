from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import threading
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from shopline_monitor.backend import (
    ShoplineClient,
    build_dashboard_payload,
    deliver_alert_webhook,
    now_iso,
    current_dashboard_date,
    dashboard_cache_seconds,
    normalize_dashboard_filters,
    probe_integrations,
)
from shopline_monitor.dashboard_runtime import DashboardRuntime, configuration_fingerprint


PROJECT_DIR = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_DIR / "static"
_runtime: DashboardRuntime | None = None
_runtime_lock = threading.Lock()


def dashboard_response(range_key: str, today: date | None, filters: dict[str, str],
                       force: bool = False, background: bool = False) -> dict:
    if not background:
        return build_dashboard_payload(range_key, today=today, filters=filters, force_refresh=force)
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            directory = Path(os.getenv("DASHBOARD_SNAPSHOT_DIR") or PROJECT_DIR.parent / ".cache" / "shopline-monitor")
            _runtime = DashboardRuntime(build_dashboard_payload, directory, configuration_fingerprint(), ttl=dashboard_cache_seconds())
    range_key = range_key if range_key in {"1d", "7d", "30d", "90d"} else "7d"
    return _runtime.request(range_key, today or current_dashboard_date(), normalize_dashboard_filters(filters), force=force)


class ShoplineMonitorHandler(BaseHTTPRequestHandler):
    server_version = "ShoplineMonitor/0.1"

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_common_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in {"/", "/index.html"}:
            self.serve_static("index.html")
            return
        if path.startswith("/static/"):
            self.serve_static(path.removeprefix("/static/"))
            return
        if path == "/api/health":
            self.send_json({"ok": True, "service": "shopline-monitor", "time": now_iso()})
            return
        if path == "/api/auth/status":
            self.send_json(auth_status())
            return
        if path.startswith("/api/") and not self.require_api_access(write=False):
            return
        if path == "/api/connector":
            self.send_json(ShoplineClient().connector_status())
            return
        if path == "/api/metrics":
            query = parse_qs(parsed.query)
            range_key = query.get("range", ["7d"])[0]
            selected_date = parse_date_param(query.get("date", [""])[0])
            filters = extract_dashboard_filters(query)
            self.send_json(dashboard_response(range_key, selected_date, filters,
                                             background=query.get("background", [""])[0] == "1"))
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "route not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/auth/login":
            payload = self.read_json_body()
            supplied = str(payload.get("token") or "") if isinstance(payload, dict) else ""
            if verify_dashboard_token(supplied):
                self.send_json({"ok": True, **auth_status()})
            else:
                self.send_error_json(HTTPStatus.UNAUTHORIZED, "access token is invalid")
            return
        if parsed.path.startswith("/api/") and not self.require_api_access(write=True):
            return
        if parsed.path == "/api/sync":
            payload = self.read_json_body()
            range_key = str(payload.get("range", "7d")) if isinstance(payload, dict) else "7d"
            selected_date = parse_date_param(str(payload.get("date", ""))) if isinstance(payload, dict) else None
            filters = normalize_filter_payload(payload.get("filters") if isinstance(payload, dict) else {})
            self.send_json(
                dashboard_response(
                    range_key,
                    today=selected_date,
                    filters=filters,
                    force=True,
                    background=bool(payload.get("background")) if isinstance(payload, dict) else False,
                )
            )
            return
        if parsed.path == "/api/connector/test":
            self.send_json(probe_integrations())
            return
        if parsed.path == "/api/alerts/notify":
            payload = self.read_json_body()
            raw_alert = payload.get("alert") if isinstance(payload, dict) else None
            if not isinstance(raw_alert, dict):
                self.send_error_json(HTTPStatus.BAD_REQUEST, "alert payload is required")
                return
            alert = {
                "id": str(raw_alert.get("id") or "")[:64],
                "level": str(raw_alert.get("level") or "info")[:24],
                "title": str(raw_alert.get("title") or "SOSOVE 数据预警")[:120],
                "message": str(raw_alert.get("message") or "")[:1200],
            }
            delivery = deliver_alert_webhook([alert], force_refresh=True, manual=True)
            self.send_json({"ok": bool(delivery.get("delivered")), **delivery})
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "route not found")

    def serve_static(self, relative_path: str) -> None:
        target = (STATIC_DIR / relative_path).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "invalid static path")
            return
        if not target.exists() or not target.is_file():
            self.send_error_json(HTTPStatus.NOT_FOUND, "file not found")
            return

        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        content = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_common_headers(content_type=content_type, cache=False)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Browsers cancel superseded dashboard requests during rapid filter
            # changes. The request is already complete, so this is not a server
            # failure and should not fill the error log with socket tracebacks.
            return

    def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_common_headers(content_type="application/json; charset=utf-8", cache=False)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"ok": False, "error": message}, status=status)

    def send_common_headers(
        self,
        content_type: str = "text/plain; charset=utf-8",
        cache: bool = False,
    ) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Dashboard-Token")
        if not cache:
            self.send_header("Cache-Control", "no-store")

    def read_json_body(self) -> object:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        body = self.rfile.read(length)
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def log_message(self, format: str, *args: object) -> None:
        print(f"[shopline-monitor] {self.address_string()} - {format % args}")

    def require_api_access(self, write: bool) -> bool:
        if not dashboard_auth_enabled():
            return True
        supplied = self.headers.get("X-Dashboard-Token", "")
        authorization = self.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        if not verify_dashboard_token(supplied):
            self.send_error_json(HTTPStatus.UNAUTHORIZED, "dashboard authentication required")
            return False
        if write and dashboard_role() == "viewer":
            self.send_error_json(HTTPStatus.FORBIDDEN, "viewer role is read-only")
            return False
        return True


def dashboard_auth_enabled() -> bool:
    return bool(os.getenv("DASHBOARD_ACCESS_TOKEN", "").strip())


def dashboard_role() -> str:
    role = os.getenv("DASHBOARD_ROLE", "admin").strip().lower()
    return role if role in {"admin", "operator", "viewer"} else "admin"


def verify_dashboard_token(supplied: str) -> bool:
    expected = os.getenv("DASHBOARD_ACCESS_TOKEN", "").strip()
    if not expected:
        return True
    return secrets.compare_digest(str(supplied or ""), expected)


def auth_status() -> dict[str, object]:
    return {
        "configured": dashboard_auth_enabled(),
        "role": dashboard_role(),
        "timezone": ShoplineClient().config.timezone_name,
    }


def extract_dashboard_filters(query: dict[str, list[str]]) -> dict[str, str]:
    return {
        key: str(query.get(key, [""])[0] or "").strip()
        for key in ("channel", "status", "market", "product")
    }


def normalize_filter_payload(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        key: str(value.get(key) or "").strip()
        for key in ("channel", "status", "market", "product")
    }


def run(host: str, port: int) -> None:
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((host, port), ShoplineMonitorHandler)
    print(f"Shopline Monitor running at http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def parse_date_param(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Shopline monitoring dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    run(args.host, args.port)


if __name__ == "__main__":
    main()
