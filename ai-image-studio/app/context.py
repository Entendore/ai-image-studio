from PySide6.QtCore import QObject, QSettings, Signal

from .backends import create_backends
from .diagnostics import MemoryMonitor
from .envconfig import theme_default
from .job_manager import JobManager
from .memory import IdleUnloader, MemoryPolicy
from .model_manager import ModelManager


class AppContext(QObject):
    """Shared app state: settings, backends, models, jobs, memory, theme."""
    backendsChanged = Signal()
    activeBackendChanged = Signal(str)
    themeChanged = Signal(str)

    def __init__(self, settings: QSettings):
        super().__init__()
        self.settings = settings
        self.models = ModelManager(settings)
        self.memory_policy = MemoryPolicy(settings)
        self.monitor = MemoryMonitor(
            cache_path_fn=lambda: self.models.hub_cache_path())
        self.idle = IdleUnloader(self)
        self.jobs = JobManager(parent=self)
        self.backends = {}
        self._active_key = None

        mode = (settings.value("ui/theme", "")
                or theme_default() or "dark")
        self.theme_mode = mode if mode in ("dark", "light") else "dark"
        self.rebuild()

    def set_theme_mode(self, mode: str):
        if mode in ("dark", "light") and mode != self.theme_mode:
            self.theme_mode = mode
            self.settings.setValue("ui/theme", mode)
            self.themeChanged.emit(mode)

    def toggle_theme(self):
        self.set_theme_mode("light" if self.theme_mode == "dark" else "dark")

    def rebuild(self):
        self.backends = create_backends(self.settings, model_manager=self.models,
                                        memory_policy=self.memory_policy)
        changed = False
        if self._active_key not in self.backends:
            last = self.settings.value("backend/last", "") or ""
            fallback = "local" if "local" in self.backends else "demo"
            self._active_key = last if last in self.backends else fallback
            changed = True
        self.backendsChanged.emit()
        if changed:
            self.activeBackendChanged.emit(self._active_key)

    @property
    def active_key(self):
        return self._active_key

    @active_key.setter
    def active_key(self, key):
        if key in self.backends and key != self._active_key:
            self._active_key = key
            self.settings.setValue("backend/last", key)
            self.activeBackendChanged.emit(key)

    def current_backend(self):
        return self.backends.get(self._active_key) or \
            next(iter(self.backends.values()))
