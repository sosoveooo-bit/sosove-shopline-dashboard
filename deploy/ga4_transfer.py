"""Authenticated GA4 key transfer. Only ciphertext belongs in the repository."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from deploy.configure_docker import parse_values, port_value, write_values


FORMAT = "sosove-ga4-transfer-v1"
DEFAULT_BUNDLE = Path(__file__).resolve().parent / "encrypted" / "ga4-transfer.fernet"
MAX_BUNDLE_BYTES = 128 * 1024
COMPOSE = ["docker", "compose", "-f", "docker-compose.yml", "-f", "compose.build.yml"]
DECRYPT_HELPER = """
import json, sys
from cryptography.fernet import Fernet
message = json.load(sys.stdin)
plain = Fernet(message['code'].encode('ascii')).decrypt(message['token'].encode('ascii'))
sys.stdout.buffer.write(plain)
"""


def validate_payload(payload: object) -> dict:
    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        raise ValueError("不是受支持的 GA4 加密配置文件")
    if not re.fullmatch(r"[0-9]{1,20}", str(payload.get("property_id", ""))):
        raise ValueError("GA4 Property ID 无效")
    credentials = payload.get("credentials")
    if not isinstance(credentials, dict) or credentials.get("type") != "service_account":
        raise ValueError("缺少 Google 服务账号配置")
    if not all(isinstance(credentials.get(key), str) and credentials[key] for key in ("private_key", "client_email")):
        raise ValueError("Google 服务账号配置不完整")
    if credentials.get("token_uri") not in {"https://oauth2.googleapis.com/token", "https://accounts.google.com/o/oauth2/token"}:
        raise ValueError("Google 授权地址无效")
    if payload.get("conversion_metric") not in {"userKeyEventRate", "sessionKeyEventRate"}:
        raise ValueError("GA4 转化率指标无效")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", str(payload.get("key_event_name", ""))):
        raise ValueError("GA4 关键事件名称无效")
    return payload


def seal(payload: dict) -> tuple[bytes, str]:
    from cryptography.fernet import Fernet
    validate_payload(payload)
    code = Fernet.generate_key()
    token = Fernet(code).encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return token, code.decode("ascii")


def open_bundle(token: bytes, code: str) -> dict:
    from cryptography.fernet import Fernet, InvalidToken
    if len(token) > MAX_BUNDLE_BYTES:
        raise ValueError("加密文件过大")
    try:
        return validate_payload(json.loads(Fernet(code.strip().encode("ascii")).decrypt(token)))
    except (InvalidToken, UnicodeError, ValueError, TypeError):
        raise ValueError("解密失败：口令不正确或文件被修改") from None


def decrypt_with_docker(token: bytes, code: str, image: str) -> dict:
    if len(token) > MAX_BUNDLE_BYTES:
        raise ValueError("加密文件过大")
    if not re.fullmatch(r"(?:sha256:)?[a-f0-9]{12,64}", image):
        raise ValueError("未找到本地面板镜像 ID")
    command = ["docker", "run", "--rm", "-i", "--pull", "never", "--network", "none",
               "--read-only", "--log-driver", "none", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges:true", "--entrypoint", "python",
               image, "-c", DECRYPT_HELPER]
    # Code/ciphertext go through stdin, not argv or environment. Plaintext is
    # captured in memory; the helper has no network, mounts, or Docker log driver.
    request = json.dumps({"token": token.decode("ascii"), "code": code.strip()}).encode("utf-8")
    result = subprocess.run(command, input=request, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
    if result.returncode:
        raise ValueError("解密失败：请核对口令完整性和本地面板镜像，现有配置未改动")
    try:
        return validate_payload(json.loads(result.stdout))
    except (UnicodeError, ValueError, TypeError):
        raise ValueError("解密后的配置无效，现有配置未改动") from None


def atomic_private_write(path: Path, data: bytes, mode: int) -> None:
    if path.is_symlink():
        raise ValueError("拒绝写入符号链接")
    fd, name = tempfile.mkstemp(prefix=".ga4-import-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        if os.name == "posix":
            os.chown(temporary, 0, 10001)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install_payload(project: Path, payload: dict) -> None:
    validate_payload(payload)
    project = project.resolve()
    env_path = project / ".env"
    secret_dir = project / "secrets"
    secret_path = secret_dir / "ga.json"
    if not env_path.is_file() or env_path.is_symlink():
        raise ValueError("请先完成面板安装，必须存在普通 .env 文件")
    if secret_dir.is_symlink() or secret_path.is_symlink():
        raise ValueError("secrets 或 ga.json 不能是符号链接")
    before_env = env_path.read_bytes()
    before_secret = secret_path.read_bytes() if secret_path.exists() else None
    secret_dir.mkdir(mode=0o750, exist_ok=True)
    secret_dir.chmod(0o750)
    if os.name == "posix":
        os.chown(secret_dir, 0, 10001)
    if before_secret is not None:
        backup = secret_dir / ("ga.json.backup-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        atomic_private_write(backup, before_secret, 0o600)
    try:
        atomic_private_write(secret_path, json.dumps(payload["credentials"], ensure_ascii=False, indent=2).encode("utf-8"), 0o640)
        write_values(env_path, "", {
            "GA4_PROPERTY_ID": str(payload["property_id"]),
            "GA4_KEY_EVENT_NAME": payload["key_event_name"],
            "GA4_CONVERSION_METRIC": payload["conversion_metric"],
            "GA4_CONVERSION_MODE": "key_event_rate",
            "GA4_SERVICE_ACCOUNT_FILE": "/app/secrets/ga.json",
            "GA4_SERVICE_ACCOUNT_JSON": "",
        })
    except Exception:
        atomic_private_write(env_path, before_env, 0o600)
        if before_secret is None:
            secret_path.unlink(missing_ok=True)
        else:
            atomic_private_write(secret_path, before_secret, 0o640)
        raise


def current_image(project: Path) -> str:
    result = subprocess.run(COMPOSE + ["images", "-q", "dashboard"], cwd=project,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
    image = result.stdout.strip().splitlines()
    if result.returncode or len(image) != 1 or not re.fullmatch(r"(?:sha256:)?[a-f0-9]{12,64}", image[0]):
        raise ValueError("面板镜像不可用，请先启动现有 Docker 面板")
    return image[0]


def recreate_and_check(project: Path) -> None:
    result = subprocess.run(COMPOSE + ["up", "-d", "--force-recreate", "--pull", "never", "dashboard"], cwd=project, timeout=90)
    if result.returncode:
        raise ValueError("GA4 文件已保存，但容器重建未完成，请检查 Docker 状态")
    values = parse_values((project / ".env").read_text(encoding="utf-8-sig"))
    port = port_value(values.get("DASHBOARD_PORT") or "8000")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    healthy = False
    for attempt in range(20):
        try:
            with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
                if json.load(response).get("ok") is True:
                    healthy = True
                    break
        except (OSError, ValueError):
            pass
        time.sleep(2)
    if not healthy:
        raise ValueError("GA4 已保存，但面板健康检查未通过，请查看容器日志")
    readable = subprocess.run(COMPOSE + ["exec", "-T", "dashboard", "python", "-c",
        "import json; from pathlib import Path; assert json.loads(Path('/app/secrets/ga.json').read_text())['type'] == 'service_account'"],
        cwd=project, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    if readable.returncode:
        raise ValueError("容器无法读取 GA4 文件，请检查 secrets 目录权限")
    print("GA4 文件和参数已配置，面板已重新启动。请在网页点击接口测试确认 Google 授权。")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import an encrypted GA4 configuration without publishing its decryption code.")
    commands = parser.add_subparsers(dest="action", required=True)
    exporter = commands.add_parser("seal")
    exporter.add_argument("--credentials", type=Path, required=True)
    exporter.add_argument("--property-id", required=True)
    exporter.add_argument("--key-event", default="purchase")
    exporter.add_argument("--metric", default="userKeyEventRate")
    exporter.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    exporter.add_argument("--code-file", type=Path, required=True)
    importer = commands.add_parser("import")
    importer.add_argument("--project-dir", type=Path, default=Path.cwd())
    importer.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    importer.add_argument("--code-stdin", action="store_true", help="For automated synthetic tests; normal use has a hidden prompt.")
    args = parser.parse_args()
    if args.action == "seal":
        root = Path(__file__).resolve().parents[1]
        if args.code_file.resolve().is_relative_to(root):
            raise ValueError("解密口令文件必须保存在 Git 仓库之外")
        if args.code_file.exists() or args.bundle.exists():
            raise ValueError("输出文件已存在，拒绝覆盖已有口令或密文")
        payload = {"format": FORMAT, "property_id": args.property_id, "key_event_name": args.key_event,
                   "conversion_metric": args.metric, "credentials": json.loads(args.credentials.read_text(encoding="utf-8-sig"))}
        token, code = seal(payload)
        if open_bundle(token, code) != payload:
            raise ValueError("加密校验失败")
        # Generated private output goes beside the user's existing credentials,
        # not in the repository, terminal log, or a GitHub Actions secret.
        with args.code_file.open("x", encoding="ascii") as stream:
            args.code_file.chmod(0o600)
            stream.write(code + "\n")
        args.bundle.parent.mkdir(parents=True, exist_ok=True)
        with args.bundle.open("xb") as stream:
            stream.write(token + b"\n")
        print("加密和回读校验完成。解密口令已单独保存，未打印到日志。")
        return
    if os.name != "posix" or os.geteuid() != 0:
        raise ValueError("请在 NAS 的 root SSH 终端执行导入")
    project = args.project_dir.resolve()
    image = current_image(project)
    token = args.bundle.read_bytes().strip()
    if len(token) > MAX_BUNDLE_BYTES:
        raise ValueError("加密文件过大")
    if args.code_stdin:
        code = sys.stdin.readline(256).strip()
    else:
        if not sys.stdin.isatty():
            raise ValueError("请在交互 SSH 终端输入解密口令")
        code = getpass.getpass("GA4 解密口令（隐藏输入）：").strip()
    payload = decrypt_with_docker(token, code, image)
    install_payload(project, payload)
    recreate_and_check(project)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        sys.exit(1)
    except (ValueError, OSError, subprocess.SubprocessError):
        # Do not echo subprocess stdin/stdout or exception objects containing it.
        print("GA4 导入未完成，请核对口令、部署目录和 Docker 状态；不要把私钥或口令发到公开日志。", file=sys.stderr)
        sys.exit(1)
