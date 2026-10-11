"""Central environment-variable configuration.

Precedence (highest first):
  1. real environment variables
  2. .env file (CWD, then data dir; AIIS_ENV_FILE for an explicit path)
     — loaded once at startup, before anything reads the environment
  3. QSettings / built-in defaults

All variables are optional; unset behavior is identical to before.
Secrets are masked in reports and never logged (see diagnostics.redact).
"""
from __future__ import annotations

import os
from pathlib import Path

# name -> (kind, default, secret, help)
SPEC = {
    # paths
    "AIIS_DATA_DIR":    ("path", "", False, "Data directory (settings, logs, lock file)."),
    "AIIS_LOG_DIR":     ("path", "", False, "Log directory override."),
    "AIIS_OUTPUT_DIR":  ("path", "", False, "Default image output folder."),
    # logging / diagnostics
    "AIIS_LOG_LEVEL":   ("enum", "", False, "Log level override (debug/info/warning/error)."),
    "AIIS_CONSOLE_LOG": ("bool", "", False, "Mirror log records to the console."),
    # behavior
    "AIIS_THEME":       ("enum", "", False, "Initial theme: dark or light."),
    "AIIS_BACKEND":     ("enum", "", False, "Force one backend: demo, local or api."),
    "AIIS_DEVICE":      ("enum", "auto", False, "Compute device for local models: auto/cpu/cuda."),
    "AIIS_NO_UPDATES":  ("bool", "", False, "Disable update checks."),
    "AIIS_ALLOW_MULTI": ("bool", "", False, "Allow multiple app instances at once."),
    "AIIS_MAX_IMAGE_PIXELS": ("int", "", False,
                              "Override Pillow's decompression-bomb pixel limit."),
    # models
    "AIIS_SD_MODEL":         ("str", "", False, "Generation model repo-id or checkpoint path override."),
    "AIIS_SD_INPAINT_MODEL": ("str", "", False, "Inpainting model repo-id override."),
    "HF_TOKEN":              ("str", "", True, "HuggingFace token (needed for gated models)."),
    "HUGGING_FACE_HUB_TOKEN": ("str", "", True, "Alias of HF_TOKEN."),
    # api
    "OPENAI_API_KEY":  ("str", "", True, "OpenAI-compatible API key."),
    "OPENAI_BASE_URL": ("str", "", False, "OpenAI-compatible base URL."),
    # meta
    "AIIS_ENV_FILE":   ("path", "", False, "Explicit path to a .env file (real environment only)."),
}

_ENUMS = {
    "AIIS_LOG_LEVEL": ("debug", "info", "warning", "error"),
    "AIIS_THEME": ("dark", "light"),
    "AIIS_BACKEND": ("demo", "local", "api"),
    "AIIS_DEVICE": ("auto", "cpu", "cuda"),
}


# ----------------------------------------------------------------- getters
def get_str(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return v if v is not None else default


def get_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def get_int(name: str, default: int = 0) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _enum(name: str) -> str:
    v = get_str(name).strip().lower()
    return v if v in _ENUMS.get(name, ()) else ""


# ------------------------------------------------------------- .env loader
def load_dotenv(paths) -> int:
    """Minimal .env parser (no dependency). Real environment variables win.

    Only keys listed in SPEC are accepted; unknown keys are ignored so a
    shared .env can't inject arbitrary settings. Returns the count loaded."""
    loaded = 0
    for p in paths:
        p = Path(p)
        if not p.is_file():
            continue
        try:
            for raw in p.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key not in SPEC or key in os.environ:
                    continue
                os.environ[key] = val
                loaded += 1
        except OSError:
            continue
    return loaded


# -------------------------------------------------------- semantic helpers
def log_level_pref() -> str:
    return _enum("AIIS_LOG_LEVEL")


def console_log_enabled() -> bool:
    return get_bool("AIIS_CONSOLE_LOG", False)


def theme_default() -> str:
    return _enum("AIIS_THEME")


def force_backend() -> str:
    return _enum("AIIS_BACKEND")


def device_pref() -> str:
    v = _enum("AIIS_DEVICE")
    return v or "auto"


def updates_disabled() -> bool:
    return get_bool("AIIS_NO_UPDATES", False)


def allow_multi_instance() -> bool:
    return get_bool("AIIS_ALLOW_MULTI", False)


def hf_token() -> str:
    return get_str("HF_TOKEN").strip() or get_str("HUGGING_FACE_HUB_TOKEN").strip()


def sd_model_override() -> str:
    return get_str("AIIS_SD_MODEL").strip()


def sd_inpaint_override() -> str:
    return get_str("AIIS_SD_INPAINT_MODEL").strip()


def openai_base_env() -> str:
    return get_str("OPENAI_BASE_URL").strip()


# ------------------------------------------------------------- side effects
def apply_runtime_side_effects():
    """Apply env vars that change library behavior at startup."""
    px = get_int("AIIS_MAX_IMAGE_PIXELS", 0)
    if px > 0:
        try:
            from PIL import Image
            Image.MAX_IMAGE_PIXELS = px
        except Exception:
            pass


# ------------------------------------------------------------------ report
def report_rows() -> list:
    """[(name, value)] for vars actually set; secrets masked."""
    rows = []
    for name, (_kind, _def, secret, _help) in SPEC.items():
        raw = os.environ.get(name)
        if not raw:
            continue
        rows.append((name, "***" if secret else raw))
    return rows
