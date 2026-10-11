import threading
import time
import traceback

from PySide6.QtCore import QThread, Signal

from .diagnostics import LOG
from .errors import JobCancelled

job = LOG.job


class Worker(QThread):
    """Runs a callable off the UI thread with cooperative cancellation,
    optional thread priority, duration logging and structured failures."""
    progress = Signal(int)
    done = Signal(object)
    failed = Signal(str)          # legacy formatted string
    failedExc = Signal(object)    # preferred: exception instance

    def __init__(self, fn, *args, parent=None, label=None,
                 cancel_event: threading.Event = None, priority=None, **kwargs):
        super().__init__(parent)
        self._fn, self._args, self._kwargs = fn, args, kwargs
        self.label = label or getattr(fn, "__name__", "job")
        self.cancel_event = cancel_event or threading.Event()
        self._priority = priority

    def cancel(self):
        self.cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def run(self):
        if self._priority is not None:
            try:
                self.setPriority(self._priority)
            except Exception:
                pass
        t0 = time.perf_counter()
        try:
            result = self._fn(*self._args, **self._kwargs)
            if self.cancel_event.is_set():
                raise JobCancelled(self.label)
            job.info(f"{self.label} finished in {time.perf_counter() - t0:.1f}s")
            self.done.emit(result)
        except JobCancelled:
            job.info(f"{self.label} cancelled after "
                     f"{time.perf_counter() - t0:.1f}s")
            self.failedExc.emit(JobCancelled(self.label))
        except Exception as exc:
            job.exception(f"{self.label} failed after "
                          f"{time.perf_counter() - t0:.1f}s: {exc}")
            self.failedExc.emit(exc)
            self.failed.emit(f"{type(exc).__name__}: {exc}\n\n"
                             f"{traceback.format_exc(limit=4)}")
