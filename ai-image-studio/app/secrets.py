"""API-key storage: system keyring → OPENAI_API_KEY env → settings fallback."""
from __future__ import annotations

import os

from .diagnostics import LOG

SERVICE = "AI-Image-Studio"
_KEY = "openai_key"

try:
    import keyring
    _mod = type(keyring.get_keyring()).__module__.lower()
    _KEYRING_OK = "fail" not in _mod and "null" not in _mod
except Exception:
    _KEYRING_OK = False


def keyring_available() -> bool:
    return _KEYRING_OK


def store(key: str):
    if not _KEYRING_OK:
        raise RuntimeError("No system keyring available.")
    keyring.set_password(SERVICE, _KEY, key)


def load() -> str | None:
    if not _KEYRING_OK:
        return None
    try:
        return keyring.get_password(SERVICE, _KEY)
    except Exception:
        return None


def delete():
    if not _KEYRING_OK:
        return
    try:
        keyring.delete_password(SERVICE, _KEY)
    except Exception:
        pass


def migrate_from_settings(settings) -> bool:
    legacy = (settings.value("api/openai_key", "") or "").strip()
    if not legacy or not _KEYRING_OK or load():
        return False
    try:
        store(legacy)
        settings.setValue("api/openai_key", "")
        settings.sync()
        LOG.app.info("API key migrated from settings to system keyring")
        return True
    except Exception as exc:
        LOG.app.warn(f"keyring migration failed: {exc}")
        return False


def resolve_key(settings) -> str:
    """Keyring → OPENAI_API_KEY env → legacy settings value."""
    k = (load() or "").strip()
    if k:
        return k
    env = os.environ.get("OPENAI_API_KEY", "").strip()
    if env:
        return env
    return (settings.value("api/openai_key", "") or "").strip()
