from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BASE_DIR / ".env"
DATA_DIR_ENV = "DUBBING_DATA_DIR"


class ConfigError(RuntimeError):
    pass


def resolve_data_dir() -> Path:
    """Resolve the local persistence root.

    DUBBING_DATA_DIR exists so tests (and anyone running a second checkout) can point
    SQLite and the project/output folders at a scratch directory instead of the real
    <repo>/data. It has to be set before the modules that freeze it at import time.
    """
    configured = os.getenv(DATA_DIR_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return BASE_DIR / "data"


def load_env(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigError(f"Missing required config: {name}. Set it in .env.")
    return value
