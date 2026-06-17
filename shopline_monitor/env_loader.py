from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Iterable


def load_environment(paths: Iterable[Path | str] | None = None) -> list[str]:
    candidate_paths = _normalize_paths(paths) if paths is not None else _default_paths()
    loaded_keys: list[str] = []
    seen_paths: set[Path] = set()

    for path in candidate_paths:
        resolved = path.resolve()
        if resolved in seen_paths or not path.is_file():
            continue
        seen_paths.add(resolved)
        loaded_keys.extend(_load_env_file(path))

    return loaded_keys


def _default_paths() -> list[Path]:
    package_root = Path(__file__).resolve().parent.parent
    cwd = Path.cwd()
    paths = [cwd / ".env", package_root / ".env"]
    if cwd != package_root:
        paths.extend([cwd / ".env.local", package_root / ".env.local"])
    return paths


def _normalize_paths(paths: Iterable[Path | str]) -> list[Path]:
    return [Path(path) for path in paths]


def _load_env_file(path: Path) -> list[str]:
    loaded: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _parse_env_value(value.strip())
        loaded.append(key)
    return loaded


def _parse_env_value(value: str) -> str:
    if not value:
        return ""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, str):
                return parsed
        except (SyntaxError, ValueError):
            return value[1:-1]
    return value
