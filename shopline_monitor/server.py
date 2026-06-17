from __future__ import annotations

import argparse
import json
import mimetypes
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from shopline_monitor.backend import ShoplineClient, build_dashboard_payload, now_iso


PROJECT_DIR = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_DIR / "static"


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
        if path == "/api/connector":
            self.send_json(ShoplineClient().connector_status())
            return
        if path == "/api/metrics":
            query = parse_qs(parsed.query)
            range_key = query.get("range", ["7d"])[0]
            selected_date = parse_date_param(query.get("date", [""])[0])
            self.send_json(build_dashboard_payload(range_key, today=selected_date))
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "route not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/sync":
            payload = self.read_json_body()
            range_key = str(payload.get("range", "7d")) if isinstance(payload, dict) else "7d"
            selected_date = parse_date_param(str(payload.get("date", ""))) if isinstance(payload, dict) else None
            self.send_json(build_dashboard_payload(range_key, today=selected_date))
            return
        if parsed.path == "/api/connector/test":
            self.send_json(ShoplineClient().test_connection())
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
        self.wfile.write(content)

    def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_common_headers(content_type="application/json; charset=utf-8", cache=False)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

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
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
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
