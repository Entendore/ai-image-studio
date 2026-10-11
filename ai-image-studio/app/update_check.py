"""Optional update check against GitHub releases. Off by default, and
disabled entirely by AIIS_NO_UPDATES=1. Point `update/repo_api` in QSettings
(or DEFAULT_REPO_API) at your releases/latest API URL before shipping."""
from __future__ import annotations

import re

from PySide6.QtCore import QObject, Signal

from .diagnostics import LOG
from .envconfig import updates_disabled
from .version import APP_VERSION
from .workers import Worker

DEFAULT_REPO_API = ("https://api.github.com/repos/EXAMPLE/ai-image-studio/"
                    "releases/latest")


def parse_version(s: str):
    m = re.search(r"(\d+(?:\.\d+)+)", s or "")
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def fetch_latest(api_url: str, current: str) -> str:
    import requests
    r = requests.get(api_url, timeout=6,
                     headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    tag = r.json().get("tag_name", "")
    latest, cur = parse_version(tag), parse_version(current)
    return tag if (latest and cur and latest > cur) else ""


class UpdateChecker(QObject):
    checked = Signal(str)          # empty = up to date / unreachable

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._worker = None

    def check(self):
        if updates_disabled():
            LOG.app.info("update check skipped (AIIS_NO_UPDATES)")
            self.checked.emit("")
            return
        if self._worker is not None and self._worker.isRunning():
            return
        url = self.settings.value("update/repo_api", DEFAULT_REPO_API) \
            or DEFAULT_REPO_API
        self._worker = Worker(fetch_latest, parent=self, label="update check",
                              kwargs=dict(api_url=url, current=APP_VERSION))
        self._worker.done.connect(lambda tag: self.checked.emit(tag or ""))
        self._worker.failed.connect(lambda _m: self.checked.emit(""))
        self._worker.start()
        LOG.app.info("update check started")
