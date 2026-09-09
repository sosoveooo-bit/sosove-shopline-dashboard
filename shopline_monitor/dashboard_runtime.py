from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any, Callable


LOGGER = logging.getLogger(__name__)
SNAPSHOT_VERSION = 1


def configuration_fingerprint() -> str:
    values = {key: value for key, value in os.environ.items()
              if key.startswith(("SHOPLINE_", "GA4_", "DASHBOARD_")) or key == "GOOGLE_APPLICATION_CREDENTIALS"}
    for key in ("GA4_SERVICE_ACCOUNT_FILE", "GOOGLE_APPLICATION_CREDENTIALS"):
        try:
            credential = Path(values[key]).stat()
            values[key + "_revision"] = f"{credential.st_size}:{credential.st_mtime_ns}"
        except (KeyError, OSError):
            pass
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


class DashboardRuntime:
    """Serve exact-query snapshots while at most two refreshes run in the background."""

    def __init__(
        self, builder: Callable[..., dict[str, Any]], directory: Path,
        fingerprint: str, ttl: float = 120, max_jobs: int = 2,
    ) -> None:
        self.builder = builder
        self.directory = directory
        self.fingerprint = fingerprint
        self.ttl = max(1, ttl)
        self.max_jobs = max_jobs
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=max_jobs, thread_name_prefix="snapshot-refresh")
        self.snapshots: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.jobs: set[str] = set()
        self.failures: dict[str, tuple[float, str]] = {}
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def query_key(self, range_key: str, day: date, filters: dict[str, str]) -> str:
        query = [SNAPSHOT_VERSION, self.fingerprint, range_key, day.isoformat(), filters]
        return hashlib.sha256(json.dumps(query, sort_keys=True).encode()).hexdigest()

    def request(
        self, range_key: str, day: date, filters: dict[str, str], force: bool = False,
    ) -> dict[str, Any]:
        key = self.query_key(range_key, day, filters)
        with self.lock:
            snapshot = self._read(key)
            now = time.time()
            expired = not snapshot or now - snapshot["savedAt"] >= self.ttl
            failure = self.failures.get(key)
            retry_allowed = not failure or now - failure[0] >= 60
            queued = False
            if key not in self.jobs and (force or expired) and (force or retry_allowed):
                if len(self.jobs) < self.max_jobs:
                    self.jobs.add(key)
                    self.pool.submit(self._refresh, key, range_key, day, dict(filters), force)
                else:
                    queued = True
            running = key in self.jobs or queued
            refresh = {
                "state": "queued" if queued else "refreshing" if running else "error" if failure else "ready",
                "running": running,
                "retryAfterMs": 2000,
                "error": failure[1] if failure and not running else None,
            }
            if snapshot:
                result = copy.deepcopy(snapshot["payload"])
                result["source"]["cached"] = True
            else:
                result = {"pending": True, "range": {"key": range_key, "end": day.isoformat()}}
            result["refresh"] = refresh
            return result

    def _read(self, key: str) -> dict[str, Any] | None:
        snapshot = self.snapshots.get(key)
        if snapshot:
            self.snapshots.move_to_end(key)
            return snapshot
        try:
            path = self.directory / f"{key}.json"
            if path.stat().st_size > 25_000_000:
                return None
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            if (snapshot.get("key") != key or not isinstance(snapshot.get("savedAt"), (int, float))
                    or not isinstance(snapshot.get("payload", {}).get("source"), dict)):
                return None
        except (OSError, ValueError, AttributeError, TypeError):
            return None
        self._remember(key, snapshot)
        return snapshot

    def _remember(self, key: str, snapshot: dict[str, Any]) -> None:
        self.snapshots[key] = snapshot
        self.snapshots.move_to_end(key)
        while len(self.snapshots) > 32:
            self.snapshots.popitem(last=False)

    def _refresh(self, key: str, range_key: str, day: date, filters: dict[str, str], force: bool) -> None:
        try:
            result = self.builder(range_key, today=day, filters=filters, force_refresh=force)
            source = result.get("source", {})
            errors = source.get("errors") or []
            success = not errors and source.get("mode") in {"live", "sample"}
            with self.lock:
                if success:
                    snapshot = {"key": key, "savedAt": time.time(), "payload": result}
                    self._remember(key, snapshot)
                    self.failures.pop(key, None)
                    self._save(key, snapshot)
                else:
                    self.failures[key] = (time.time(), str(errors[0] if errors else "数据源未完整更新"))
                    if source.get("mode") == "live" or not self._read(key):
                        # Keep fresh Shopline orders available when only GA4
                        # failed, but do not replace the last complete disk copy.
                        self._remember(key, {"key": key, "savedAt": 0, "payload": result})
        except Exception as exc:
            with self.lock:
                self.failures[key] = (time.time(), f"{type(exc).__name__}: 数据更新失败，请查看本地日志。")
            LOGGER.exception("Dashboard refresh failed for query %s", key)
        finally:
            with self.lock:
                self.jobs.discard(key)
                while len(self.failures) > 64:
                    self.failures.pop(next(iter(self.failures)))

    def _save(self, key: str, snapshot: dict[str, Any]) -> None:
        path = self.directory / f"{key}.json"
        temporary = path.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600)
                json.dump(snapshot, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            paths = sorted(self.directory.glob("[0-9a-f]" * 64 + ".json"), key=lambda item: item.stat().st_mtime)
            for old in paths[:-64]:
                old.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("Could not persist dashboard snapshot", exc_info=True)
        finally:
            temporary.unlink(missing_ok=True)

    def close(self) -> None:
        self.pool.shutdown(wait=True)
