from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
from logging.handlers import RotatingFileHandler
from pathlib import Path


def probe_health(port: int) -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
            data = json.loads(response.read(4096))
            return response.status == 200 and data.get("ok") is True and data.get("service") == "shopline-monitor"
    except (OSError, ValueError, AttributeError):
        return False


def stop_child(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    if os.name == "nt":
        # The venv executable is a Windows launcher with a child interpreter.
        # Target only this owned process tree, never all Python processes.
        subprocess.run(
            ["taskkill.exe", "/PID", str(child.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW, timeout=15, check=False,
        )
    else:
        child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def monitor_child(child, health_check, stop, logger, sleep=time.sleep, interval=10, failure_limit=3) -> str:
    failures = 0
    while child.poll() is None:
        sleep(interval)
        if child.poll() is not None:
            break
        if health_check():
            failures = 0
            continue
        failures += 1
        logger.warning("Health check failed (%s/%s), child pid=%s", failures, failure_limit, child.pid)
        if failures >= failure_limit:
            stop(child)
            return "unhealthy"
    return "exited"


def pump_output(stream, logger) -> None:
    try:
        for line in stream:
            logger.info("server: %s", line.rstrip())
    finally:
        stream.close()


def run(port: int, log_dir: Path | None = None) -> None:
    project_root = Path(__file__).resolve().parent.parent
    log_dir = log_dir or Path(__file__).resolve().parent / "runtime_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "local-supervisor.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("shopline.local-supervisor")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    child = None
    lock = None
    try:
        if os.name == "nt":
            import msvcrt
            lock = (log_dir / f"supervisor-{port}.lock").open("a+b")
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                logger.info("Another supervisor already owns port %s", port)
                return
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        logger.info("Supervisor started pid=%s port=%s", os.getpid(), port)
        while True:
            child = subprocess.Popen(
                [sys.executable, "-u", "-m", "shopline_monitor.server", "--host", "127.0.0.1", "--port", str(port)],
                cwd=project_root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            logger.info("Started child pid=%s", child.pid)
            reader = threading.Thread(target=pump_output, args=(child.stdout, logger), daemon=True)
            reader.start()
            reason = monitor_child(child, lambda: probe_health(port), stop_child, logger)
            reader.join(timeout=2)
            logger.warning("Child stopped reason=%s exit=%s; retrying in 10 seconds", reason, child.poll())
            child = None
            time.sleep(10)
    except KeyboardInterrupt:
        logger.info("Supervisor stopped by user")
    finally:
        if child is not None:
            stop_child(child)
        if lock is not None:
            lock.close()
        handler.close()
        logger.removeHandler(handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Supervise the local Shopline panel.")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--log-dir", type=Path)
    args = parser.parse_args()
    run(args.port, args.log_dir)


if __name__ == "__main__":
    main()
