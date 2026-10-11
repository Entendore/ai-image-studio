"""Prevent multiple app instances (QLockFile breaks stale locks by PID).
AIIS_ALLOW_MULTI=1 disables the guard (development)."""
from PySide6.QtCore import QLockFile

from .envconfig import allow_multi_instance
from .paths import app_dir


def acquire_lock():
    """Return a truthy lock when acquired, None if another instance runs."""
    if allow_multi_instance():
        return True
    lock = QLockFile(str(app_dir() / "app.lock"))
    if lock.tryLock(150):
        return lock
    return None
