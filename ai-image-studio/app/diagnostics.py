"""Diagnostics: rotating log files, categorized logging with secret redaction,
memory monitoring, health checks, reports and crash detection.

Log files live in  <data>/logs/  (AIIS_LOG_DIR overrides the directory)
  studio.log  — configured level and up, rotated ~2 MB, 5 backups
  errors.log  — ERROR and above only, rotated ~1 MB, 3 backups
"""
from __future__ import annotations

import atexit
import logging
import logging.handlers
import os
import platform
import re
import shutil
import sys
import time
import traceback
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .envconfig import console_log_enabled, log_level_pref, report_rows
from .paths import app_dir, logs_dir
from .version import APP_VERSION

APP_START = time.time()
APP_DIR = app_dir()
LOG_DIR = logs_dir()          # honors AIIS_LOG_DIR
MAIN_LOG = LOG_DIR / "studio.log"
ERROR_LOG = LOG_DIR / "errors.log"

LEVEL_NAMES = ["debug", "info", "warning", "error"]
_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO,
           "warning": logging.WARNING, "error": logging.ERROR}
CATEGORIES = ["APP", "UI", "QT", "GEN", "EDIT", "MODEL", "MEMORY",
              "BACKEND", "API", "JOB", "DOWNLOAD"]

_SECRET_RES = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}"), "sk-***"),
    (re.compile(r"\bhf_[A-Za-z0-9]{6,}"), "hf_***"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{6,}"), "Bearer ***"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*([=:])\s*\S+"),
     r"\1\2***"),
]


def redact(msg: str) -> str:
    """Mask API keys / tokens so they never reach log files."""
    try:
        for rx, repl in _SECRET_RES:
            msg = rx.sub(repl, msg)
    except Exception:
        pass
    return msg


def fmt_bytes(v) -> str:
    if v is None:
        return "?"
    v = float(v)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{int(v)} B" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} TB"


# ============================================================================
# Logging core
# ============================================================================
class LogBus(QObject):
    """In-memory ring buffer of structured entries + Qt signals for live UI."""
    entryAdded = Signal(str)
    errorLogged = Signal(str)

    def __init__(self, maxlen: int = 3000):
        super().__init__()
        self._buf = deque(maxlen=maxlen)
        self.error_count = 0

    def append(self, levelno: int, category: str, line: str):
        self._buf.append((time.time(), levelno, category, line))
        self.entryAdded.emit(line)
        if levelno >= logging.ERROR:
            self.error_count += 1
            self.errorLogged.emit(line)

    def entries(self, level=None, category=None, contains=None):
        needle = contains.lower() if contains else None
        out = []
        for ts, lvl, cat, line in self._buf:
            if level is not None and lvl < level:
                continue
            if category and cat != category:
                continue
            if needle and needle not in line.lower():
                continue
            out.append((ts, lvl, cat, line))
        return out

    def recent_errors(self, n: int = 20):
        return [e for e in self._buf if e[1] >= logging.ERROR][-n:]

    def text(self) -> str:
        return "\n".join(e[3] for e in self._buf)

    def clear(self):
        self._buf.clear()
        self.error_count = 0


class _RingHandler(logging.Handler):
    """Feeds the LogBus (thread-safe via queued signal connections)."""

    def __init__(self, bus: LogBus):
        super().__init__()
        self.bus = bus

    def emit(self, record: logging.LogRecord):
        try:
            cat = getattr(record, "cat", "APP")
            ts = datetime.fromtimestamp(record.created)
            line = (f"{ts:%H:%M:%S}.{int(record.msecs):03d} "
                    f"[{record.levelname}] [{cat}] {record.getMessage()}")
            self.bus.append(record.levelno, cat, line)
        except Exception:
            pass


class CategoryLogger:
    """Logger tagged with a category (shows as [MODEL], [API], …)."""

    def __init__(self, category: str):
        self.category = category
        self._a = logging.LoggerAdapter(
            logging.getLogger(f"studio.{category}"), {"cat": category})

    def debug(self, msg): self._a.debug(redact(str(msg)))
    def info(self, msg): self._a.info(redact(str(msg)))
    def warn(self, msg): self._a.warning(redact(str(msg)))
    def warning(self, msg): self._a.warning(redact(str(msg)))
    def error(self, msg): self._a.error(redact(str(msg)))

    def exception(self, msg: str):
        self._a.error(redact(f"{msg}\n{traceback.format_exc(limit=6)}"))


class _LogFacade:
    """LOG.info(...) → [APP]; LOG.backend.info(...) → [BACKEND];
    LOG.get("X") → [X] created on demand."""

    def __init__(self):
        self.bus = LogBus()
        self.session_id = uuid.uuid4().hex[:8]
        self._shutdown_logged = False
        for cat in CATEGORIES:
            setattr(self, cat.lower(), CategoryLogger(cat))

    def debug(self, m): self.app.debug(m)
    def info(self, m): self.app.info(m)
    def warn(self, m): self.app.warn(m)
    def warning(self, m): self.app.warn(m)
    def error(self, m): self.app.error(m)
    def exception(self, m): self.app.exception(m)

    def get(self, category: str) -> CategoryLogger:
        cat = category.upper()
        if not hasattr(self, cat.lower()):
            setattr(self, cat.lower(), CategoryLogger(cat))
        return getattr(self, cat.lower())

    def shutdown(self, reason: str = "normal exit"):
        if self._shutdown_logged:
            return
        self._shutdown_logged = True
        self.app.info(f"SESSION {self.session_id} END — {reason} · "
                      f"uptime {int(time.time() - APP_START)}s · "
                      f"{self.bus.error_count} error(s) this session")

    def log_session_end_marker(self):
        self.app.info(f"--- session {self.session_id} ended normally ---")

    @staticmethod
    def log_dir() -> Path:
        return LOG_DIR

    @staticmethod
    def files() -> dict:
        return {"main": MAIN_LOG, "errors": ERROR_LOG}

    @staticmethod
    def file_sizes() -> dict:
        def sz(p: Path):
            try:
                return p.stat().st_size if p.exists() else 0
            except OSError:
                return 0
        return {"main": sz(MAIN_LOG), "errors": sz(ERROR_LOG)}

    def set_level(self, name: str):
        level = _LEVELS.get(str(name).lower(), logging.INFO)
        root = logging.getLogger("studio")
        root.setLevel(level)
        for h in root.handlers:
            if getattr(h, "tag", "") == "errors-only":
                continue
            h.setLevel(level)
        self.app.info(f"Log level set to '{name}'")


LOG = _LogFacade()


def _setup_logging(settings=None):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    level_name = log_level_pref()               # env wins over settings
    if not level_name and settings is not None:
        level_name = str(settings.value("logging/level", "info") or "info")
    level = _LEVELS.get(level_name or "info", logging.INFO)

    root = logging.getLogger("studio")
    root.setLevel(level)
    root.propagate = False
    if root.handlers:
        return

    file_fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)7s] [%(cat)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")

    main_h = logging.handlers.RotatingFileHandler(
        MAIN_LOG, maxBytes=2_000_000, backupCount=5, encoding="utf-8",
        delay=True)
    main_h.setLevel(level)
    main_h.setFormatter(file_fmt)
    root.addHandler(main_h)

    err_h = logging.handlers.RotatingFileHandler(
        ERROR_LOG, maxBytes=1_000_000, backupCount=3, encoding="utf-8",
        delay=True)
    err_h.setLevel(logging.ERROR)
    err_h.tag = "errors-only"
    err_h.setFormatter(file_fmt)
    root.addHandler(err_h)

    ring = _RingHandler(LOG.bus)
    ring.setLevel(level)
    root.addHandler(ring)

    if console_log_enabled():
        con = logging.StreamHandler()
        con.setLevel(level)
        con.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(cat)s] %(message)s",
            datefmt="%H:%M:%S"))
        root.addHandler(con)


def previous_session_clean() -> bool:
    """Crash detection: the previous session must contain a clean-end marker."""
    try:
        if not MAIN_LOG.exists():
            return True
        size = MAIN_LOG.stat().st_size
        with open(MAIN_LOG, "rb") as f:
            f.seek(max(0, size - 4096))
            tail = f.read().decode("utf-8", "ignore")
        return "ended normally" in tail
    except Exception:
        return True


def export_bundle(dest_path: str) -> str:
    """Zip all log rotations + a fresh system report (for bug reports)."""
    import zipfile
    paths = (sorted(LOG_DIR.glob("studio.log*"))
             + sorted(LOG_DIR.glob("errors.log*")))
    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.txt", build_report(None))
        for p in paths:
            try:
                z.write(p, arcname=p.name)
            except OSError:
                pass
    LOG.app.info(f"diagnostics bundle exported: {dest_path}")
    return dest_path


# ============================================================================
# Memory monitoring
# ============================================================================
def ram_info() -> dict:
    try:
        import psutil
        vm = psutil.virtual_memory()
        return {"total": vm.total, "used": vm.total - vm.available,
                "available": vm.available, "percent": vm.percent}
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = _MS()
            st.dwLength = ctypes.sizeof(_MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            total, avail = st.ullTotalPhys, st.ullAvailPhys
            return {"total": total, "used": total - avail,
                    "available": avail, "percent": st.dwMemoryLoad}
        except Exception:
            pass
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        return {"total": total, "used": None, "available": None, "percent": None}
    except Exception:
        return {"total": None, "used": None, "available": None, "percent": None}


def gpu_info() -> dict:
    info = {"available": False, "name": None, "cuda": None, "total": 0,
            "used": 0, "free": 0, "reserved": 0, "torch": None}
    try:
        import torch
        info["torch"] = torch.__version__
        if torch.cuda.is_available():
            info["available"] = True
            info["name"] = torch.cuda.get_device_name(0)
            info["cuda"] = torch.version.cuda
            free, total = torch.cuda.mem_get_info()
            info.update(total=total, free=free, used=total - free,
                        reserved=torch.cuda.memory_reserved())
    except Exception:
        pass
    return info


def rss_bytes():
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss
    except Exception:
        return None


class MemoryMonitor(QObject):
    updated = Signal(dict)

    def __init__(self, cache_path_fn=None, parent=None):
        super().__init__(parent)
        self.cache_path_fn = cache_path_fn
        from PySide6.QtCore import QTimer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

    def start(self, ms: int = 5000):
        self.refresh()
        self._timer.start(ms)

    def stop(self):
        self._timer.stop()

    def snapshot(self) -> dict:
        disk = None
        if self.cache_path_fn:
            try:
                du = shutil.disk_usage(self.cache_path_fn())
                disk = {"total": du.total, "free": du.free}
            except Exception:
                pass
        return {"ram": ram_info(), "gpu": gpu_info(), "rss": rss_bytes(),
                "disk": disk, "time": time.time()}

    def refresh(self) -> dict:
        snap = self.snapshot()
        self.updated.emit(snap)
        return snap


# ============================================================================
# Health checks & report
# ============================================================================
def _mod_check(mod):
    def _f():
        m = __import__(mod)
        return True, str(getattr(m, "__version__", "?"))
    return _f


def health_checks():
    def torch_check():
        import torch
        return True, (f"{torch.__version__} "
                      f"(CUDA: {'yes' if torch.cuda.is_available() else 'no'})")

    return [
        ("Python ≥ 3.9", lambda: (sys.version_info >= (3, 9),
                                  sys.version.split()[0]), "use a newer Python"),
        ("PySide6", _mod_check("PySide6"), "pip install PySide6"),
        ("Pillow", _mod_check("PIL"), "pip install Pillow"),
        ("requests", _mod_check("requests"), "pip install requests"),
        ("torch", torch_check, "pip install torch (CUDA wheel from pytorch.org)"),
        ("diffusers", _mod_check("diffusers"), "pip install diffusers"),
        ("transformers", _mod_check("transformers"), "pip install transformers"),
        ("accelerate", _mod_check("accelerate"),
         "pip install accelerate (CPU offload)"),
        ("huggingface_hub", _mod_check("huggingface_hub"),
         "pip install huggingface_hub (model downloads)"),
        ("rembg (optional)", _mod_check("rembg"), "pip install rembg"),
        ("psutil (optional)", _mod_check("psutil"), "pip install psutil"),
    ]


def run_health_checks():
    rows = []
    for name, fn, fix in health_checks():
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}"
        rows.append((name, bool(ok), detail, fix))
    return rows


def build_report(ctx=None) -> str:
    w = []
    add = w.append
    add("AI Image Studio — system report")
    add(f"Generated : {datetime.now():%Y-%m-%d %H:%M:%S}  "
        f"(uptime {int(time.time() - APP_START)}s)")
    add(f"Version   : {APP_VERSION}")
    add("")
    add("== System ==")
    add(f"Platform : {platform.platform()}")
    add(f"Machine  : {platform.machine()} · CPUs: {os.cpu_count()}")
    add(f"Python   : {sys.version.split()[0]} ({sys.executable})")
    for mod in ("PySide6", "PIL", "requests"):
        try:
            m = __import__(mod)
            add(f"{mod:<9}: {getattr(m, '__version__', '?')}")
        except Exception as exc:
            add(f"{mod:<9}: not importable ({type(exc).__name__})")

    ram, gpu, rss = ram_info(), gpu_info(), rss_bytes()
    add("")
    add("== Memory ==")
    if ram.get("total"):
        pct = f" ({ram['percent']:.0f}%)" if ram.get("percent") is not None else ""
        add(f"RAM      : {fmt_bytes(ram['used'])} / {fmt_bytes(ram['total'])}{pct}")
    else:
        add("RAM      : unknown (install psutil for details)")
    if rss:
        add(f"Process  : {fmt_bytes(rss)} RSS")
    if gpu["available"]:
        add(f"GPU      : {gpu['name']} · CUDA {gpu['cuda']} · torch {gpu['torch']}")
        add(f"VRAM     : {fmt_bytes(gpu['used'])} used / {fmt_bytes(gpu['total'])}"
            f" (free {fmt_bytes(gpu['free'])}, reserved {fmt_bytes(gpu['reserved'])})")
    else:
        add(f"GPU      : none detected (torch {gpu['torch'] or 'not installed'})")

    env_rows = report_rows()
    if env_rows:
        add("")
        add("== Environment overrides ==")
        for k, v in env_rows:
            add(f"{k} = {v}")

    if ctx is not None:
        add("")
        add("== App state ==")
        be = ctx.backends.get("local")
        if be is not None and hasattr(be, "status"):
            st = be.status()
            pipes = ", ".join(st.get("pipelines") or []) or "-"
            add(f"Local SD : loaded={st.get('loaded')} · pipelines=[{pipes}] · "
                f"device={st.get('device')}")
        add(f"Model (generate): {ctx.models.active_gen_model()}")
        add(f"Model (inpaint) : {ctx.models.active_inpaint_model()}")
        try:
            add(f"Model cache     : {fmt_bytes(ctx.models.total_cache_size())} at "
                f"{ctx.models.hub_cache_path()}")
        except Exception:
            pass
        add(f"Backend in use  : {ctx.current_backend().name}")
        pol = ctx.memory_policy
        add(f"Memory policy   : strategy={pol.strategy} · auto-unload="
            f"{pol.auto_unload} ({pol.auto_unload_minutes} min) · "
            f"single-pipeline={pol.single_pipeline}")

    sizes = LOG.file_sizes()
    add("")
    add("== Logging ==")
    add(f"Session   : {LOG.session_id} · errors this session: "
        f"{LOG.bus.error_count}")
    add(f"Log dir   : {LOG_DIR}")
    add(f"studio.log: {fmt_bytes(sizes['main'])}")
    add(f"errors.log: {fmt_bytes(sizes['errors'])}")
    return "\n".join(w)


# ============================================================================
# Install: excepthook + Qt messages + session bookkeeping
# ============================================================================
def install(settings=None):
    _setup_logging(settings)

    _orig_hook = sys.excepthook

    def _hook(t, val, tb):
        full = "".join(traceback.format_exception(t, val, tb))
        LOG.app.error(f"Uncaught exception: {t.__name__}: {val}\n{full}")
        if _orig_hook:
            _orig_hook(t, val, tb)

    sys.excepthook = _hook

    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler

        def qt_handler(mode, _ctx, message):
            fn = {QtMsgType.QtDebugMsg: LOG.qt.debug,
                  QtMsgType.QtInfoMsg: LOG.qt.info,
                  QtMsgType.QtWarningMsg: LOG.qt.warn,
                  QtMsgType.QtCriticalMsg: LOG.qt.error,
                  QtMsgType.QtFatalMsg: LOG.qt.error}.get(mode, LOG.qt.info)
            fn(f"Qt: {message}")

        qInstallMessageHandler(qt_handler)
    except Exception:
        pass

    if not previous_session_clean():
        LOG.app.warn("Previous session did not end cleanly (possible crash) — "
                     "see errors.log and previous studio.log rotation")
    LOG.app.info(f"SESSION {LOG.session_id} START — AI Image Studio "
                 f"v{APP_VERSION}")
    LOG.app.info(f"Python {sys.version.split()[0]} · {platform.system()} "
                 f"{platform.release()} ({platform.machine()}) · "
                 f"CPUs {os.cpu_count()}")
    env_rows = report_rows()
    if env_rows:
        LOG.app.info("environment overrides: "
                     + ", ".join(f"{k}={v}" for k, v in env_rows))
    sizes = LOG.file_sizes()
    LOG.app.info(f"Logging to {LOG_DIR} "
                 f"(studio.log {fmt_bytes(sizes['main'])}, "
                 f"errors.log {fmt_bytes(sizes['errors'])})")

    atexit.register(LOG.log_session_end_marker)
    atexit.register(LOG.shutdown)
    return LOG
