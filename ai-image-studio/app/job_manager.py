"""Central, serialized job runner shared by all pages.

Guarantees:
  - only one job runs at a time, across all pages;
  - cooperative cancellation (backend.cancel_event, or pass cancel_event= in
    kwargs to receive the live event inside the callable);
  - progress watchdog: warns when a job stops reporting progress;
  - one consistent error path: exceptions -> humanize() -> ErrorDialog;
  - callbacks always run in the UI thread.
"""
from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QWidget

from .diagnostics import LOG
from .errors import ErrorDialog, JobCancelled, UserError, humanize
from .workers import Worker


class _Job:
    __slots__ = ("label", "worker", "backend", "on_done", "on_fail", "on_finish")

    def __init__(self, label, worker, backend, on_done, on_fail, on_finish):
        self.label = label
        self.worker = worker
        self.backend = backend
        self.on_done = on_done
        self.on_fail = on_fail
        self.on_finish = on_finish


class JobManager(QObject):
    busyChanged = Signal(bool)
    jobStarted = Signal(str)
    jobStuck = Signal(str, int)          # label, seconds without progress

    def __init__(self, error_parent_fn: Callable[[], QWidget] = None,
                 stuck_after_s: int = 600, check_interval_s: int = 30,
                 parent=None):
        super().__init__(parent)
        self._worker: Worker | None = None
        self._job: _Job | None = None
        self._error_parent_fn = error_parent_fn or (lambda: None)
        self._stuck_after_s = stuck_after_s
        self._last_progress = 0.0
        self._stuck_warned = False
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(check_interval_s * 1000)
        self._watchdog.timeout.connect(self._check_stuck)

    @property
    def busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    @property
    def current_label(self) -> str:
        return self._job.label if self._job else ""

    def submit(self, label, fn, *, args=(), kwargs=None, backend=None,
               on_done=None, on_fail=None, on_progress=None, on_finish=None,
               priority=None):
        """Run fn(*args, **kwargs) in the single worker slot.
        Raises UserError if another job is already running."""
        if self.busy:
            raise UserError("Another operation is already running.",
                            hint="Wait for it to finish, or cancel it first.")
        kw = dict(kwargs or {})
        kw.pop("cancel_event", None)     # always replaced by the live event
        worker = Worker(fn, *args, parent=self, label=label, priority=priority,
                        **kw)
        worker._kwargs["cancel_event"] = worker.cancel_event

        jb = _Job(label, worker, backend, on_done, on_fail, on_finish)
        if backend is not None:
            backend.progress_cb = worker.progress.emit
            backend.cancel_event = worker.cancel_event
        worker.done.connect(lambda res: self._emit_done(jb, res))
        worker.failedExc.connect(lambda exc: self._emit_fail(jb, exc))
        worker.finished.connect(lambda: self._finish(jb))
        worker.progress.connect(self._touch_progress)
        if on_progress is not None:
            worker.progress.connect(on_progress)

        self._worker, self._job = worker, jb
        self._last_progress = time.monotonic()
        self._stuck_warned = False
        self._watchdog.start()
        self.jobStarted.emit(label)
        self.busyChanged.emit(True)
        worker.start()
        return jb

    def cancel_current(self):
        if self._worker is not None:
            self._worker.cancel()

    def wait_for_current(self, ms: int = 8000) -> bool:
        if self._worker is None:
            return True
        self._worker.cancel()
        return self._worker.wait(ms)

    def terminate_current(self):
        if self._worker is not None:
            LOG.app.warn("Force-terminating the active worker thread")
            self._worker.terminate()
            self._worker.wait(2000)

    # ---------------------------------------------------------------- watchdog
    def _touch_progress(self, *_):
        self._last_progress = time.monotonic()

    def _check_stuck(self):
        if not self.busy:
            return
        idle = time.monotonic() - self._last_progress
        if idle >= self._stuck_after_s and not self._stuck_warned:
            self._stuck_warned = True
            LOG.job.warn(f"job '{self._job.label}' has reported no progress "
                         f"for {int(idle)}s")
            self.jobStuck.emit(self._job.label, int(idle))

    # --------------------------------------------------------------- callbacks
    def _emit_done(self, jb: _Job, result):
        if not jb.worker.cancelled and jb.on_done:
            try:
                jb.on_done(result)
            except Exception:
                LOG.app.exception("on_done callback failed")

    def _emit_fail(self, jb: _Job, exc: BaseException):
        if isinstance(exc, JobCancelled) or jb.worker.cancelled:
            return
        title, msg, details = humanize(exc)
        try:
            if jb.on_fail:
                jb.on_fail(title, msg, details)
            else:
                ErrorDialog(title, msg, details, self._error_parent_fn()).exec()
        except Exception:
            LOG.app.exception("on_fail callback failed")

    def _finish(self, jb: _Job):
        if jb.backend is not None:
            jb.backend.progress_cb = None
            jb.backend.cancel_event = None
        cancelled = jb.worker.cancelled
        if jb.on_finish:
            try:
                jb.on_finish(cancelled)
            except Exception:
                LOG.app.exception("on_finish callback failed")
        self._worker = None
        self._job = None
        self._watchdog.stop()
        self.busyChanged.emit(False)
