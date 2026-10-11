"""Central data-directory resolution (env-overridable for portable installs)."""
import os
from pathlib import Path


def _env_path(name: str) -> Path | None:
    v = os.environ.get(name, "").strip()
    return Path(v) if v else None


def app_dir() -> Path:
    base = _env_path("AIIS_DATA_DIR") or (Path.home() / ".ai-image-studio")
    base.mkdir(parents=True, exist_ok=True)
    return base


def logs_dir() -> Path:
    d = _env_path("AIIS_LOG_DIR") or (app_dir() / "logs")
    d.mkdir(parents=True, exist_ok=True)
    return d
