"""Interactive, secret-safe configuration for the Docker installer."""
from __future__ import annotations

import argparse
import ast
import getpass
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit


def parse_values(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            try:
                value = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                value = value[1:-1]
        values[key.strip()] = str(value)
    return values


def store_domain(value: str) -> str:
    parsed = urlsplit(value if "://" in value else "https://" + value)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopline\.com", host)):
        raise ValueError("请输入正确的店铺域名，例如 jp-sosove.myshopline.com")
    return host


def credential(value: str, minimum: int = 1) -> str:
    if len(value) < minimum or re.search(r"[\s'\"\\\x00-\x1f\x7f]", value):
        raise ValueError(f"至少 {minimum} 位，不能包含空白、引号或反斜杠")
    return value


def port_value(value: str) -> str:
    if not value.isdigit() or not 1024 <= int(value) <= 65535:
        raise ValueError("端口应为 1024-65535 的数字，默认 8000")
    return str(int(value))


def quote(value: str) -> str:
    if any(char in value for char in "\r\n\x00'\\"):
        raise ValueError("配置值包含不支持的字符")
    return "'" + value + "'"


def write_values(path: Path, template: str, updates: dict[str, str]) -> None:
    if path.is_symlink():
        raise ValueError("拒绝覆盖符号链接 .env")
    existing = path.exists()
    text = path.read_text(encoding="utf-8-sig") if existing else template
    remaining = dict(updates)
    lines = []
    seen = set()
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if key in updates:
            if key not in seen:
                lines.append(key + "=" + quote(updates[key]))
            seen.add(key)
            remaining.pop(key, None)
        else:
            lines.append(line)
    lines.extend(key + "=" + quote(value) for key, value in remaining.items())
    if existing:
        backup = path.with_name(".env.backup-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        shutil.copy2(path, backup)
        backup.chmod(0o600)
    fd, temporary_name = tempfile.mkstemp(prefix=".env.install-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(lines) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def configure(directory: Path, noninteractive: bool = False) -> dict[str, str]:
    path = directory / ".env"
    if path.is_symlink():
        raise ValueError("拒绝读取符号链接 .env")
    existing = parse_values(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    updates = {}

    def ask(key, prompt, validate, default="", hidden=False):
        if existing.get(key):
            return existing[key]
        if key in os.environ:
            value = os.environ[key]
            updates[key] = validate(value)
            return updates[key]
        if noninteractive:
            if default:
                updates[key] = validate(default)
                return updates[key]
            raise ValueError(f"缺少 {key}，需要在交互终端运行安装命令")
        while True:
            value = (getpass.getpass(prompt) if hidden else input(prompt)).strip() or default
            try:
                updates[key] = validate(value)
                return updates[key]
            except ValueError as exc:
                print(str(exc), file=sys.stderr)

    base = ask("SHOPLINE_API_BASE_URL", "Shopline 店铺域名（例如 jp-sosove.myshopline.com）：",
               lambda value: "https://" + store_domain(value))
    if "SHOPLINE_STORE_DOMAIN" not in existing:
        updates["SHOPLINE_STORE_DOMAIN"] = store_domain(base)
    if not path.exists():
        updates["SHOPLINE_STOREFRONT_DOMAINS"] = store_domain(base)
    ask("SHOPLINE_ACCESS_TOKEN", "Shopline API Token（输入隐藏）：", credential, hidden=True)
    ask("DASHBOARD_ACCESS_TOKEN", "设置面板登录密码（至少16位，输入隐藏）：",
        lambda value: credential(value, 16), hidden=True)
    ask("DASHBOARD_PORT", "面板端口 [8000]：", port_value, default="8000")

    def bind(value):
        if value not in {"127.0.0.1", "0.0.0.0"}:
            raise ValueError("请输入 127.0.0.1 或 0.0.0.0")
        return value

    if not existing.get("DASHBOARD_BIND_IP") and not noninteractive and "DASHBOARD_BIND_IP" not in os.environ:
        print("公网 HTTP 不加密密码/订单；长期使用请配 HTTPS。公网访问还需在云安全组限制来源并放行端口。")
    ask("DASHBOARD_BIND_IP", "访问地址 [127.0.0.1，仅本机/Nginx]；公网IP访问填 0.0.0.0：", bind, default="127.0.0.1")

    if not path.exists():
        for key in ("GA4_PROPERTY_ID", "GA4_SERVICE_ACCOUNT_FILE", "GA4_SERVICE_ACCOUNT_JSON"):
            updates[key] = ""
    if updates:
        write_values(path, (directory / ".env.example").read_text(encoding="utf-8-sig"), updates)
    else:
        path.chmod(0o600)
    values = parse_values(path.read_text(encoding="utf-8-sig"))
    port_value(values.get("DASHBOARD_PORT", "8000"))
    bind(values.get("DASHBOARD_BIND_IP", "127.0.0.1"))
    print("配置已保存，现有密钥和 GA4 设置保留。新安装的 GA4 可按教程稍后补充。")
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--connection", action="store_true")
    args = parser.parse_args()
    if args.connection:
        values = parse_values((args.directory / ".env").read_text(encoding="utf-8-sig"))
        host = values.get("DASHBOARD_BIND_IP") or "127.0.0.1"
        if host not in {"127.0.0.1", "0.0.0.0"}:
            raise ValueError("Invalid bind address")
        print(host + " " + port_value(values.get("DASHBOARD_PORT") or "8000"))
        return
    configure(args.directory, noninteractive=args.non_interactive)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, EOFError, KeyboardInterrupt) as exc:
        print(f"配置未完成：{exc}", file=sys.stderr)
        sys.exit(1)
