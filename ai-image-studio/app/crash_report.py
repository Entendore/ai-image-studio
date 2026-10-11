"""Friendly notice when the previous session did not close cleanly."""
from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout)

from .diagnostics import ERROR_LOG, LOG, LOG_DIR, build_report, \
    previous_session_clean


def startup_crash_check(settings, parent=None):
    if str(settings.value("logging/crash_prompt_off", "")).lower() in ("true", "1"):
        return
    try:
        if previous_session_clean():
            return
        had_errors = ERROR_LOG.exists() and ERROR_LOG.stat().st_size > 0
    except Exception:
        return

    dlg = QDialog(parent)
    dlg.setWindowTitle("Unexpected shutdown detected")
    dlg.setMinimumWidth(460)
    v = QVBoxLayout(dlg)
    v.setContentsMargins(16, 16, 16, 16)
    msg = QLabel(
        "The last session did not close cleanly"
        + (" and errors were recorded." if had_errors else ".")
        + "\n\nIf this keeps happening, use “Copy report” and include the "
          "content in a bug report.")
    msg.setWordWrap(True)
    v.addWidget(msg)

    row = QHBoxLayout()
    b_open = QPushButton("Open log folder")
    b_open.clicked.connect(lambda: QDesktopServices.openUrl(
        QUrl.fromLocalFile(str(LOG_DIR))))

    def _copy():
        tail = ""
        try:
            if had_errors:
                tail = ERROR_LOG.read_text(encoding="utf-8",
                                           errors="ignore")[-4000:]
        except Exception:
            pass
        QGuiApplication.clipboard().setText(
            build_report(None) + "\n\n== errors.log (tail) ==\n" + tail)

    b_copy = QPushButton("Copy report")
    b_copy.clicked.connect(_copy)
    dont = QCheckBox("Don't ask again")
    row.addWidget(b_open)
    row.addWidget(b_copy)
    row.addStretch(1)
    row.addWidget(dont)
    ok = QPushButton("Continue")
    ok.setDefault(True)
    ok.clicked.connect(dlg.accept)
    row.addWidget(ok)
    v.addLayout(row)

    dlg.exec()
    if dont.isChecked():
        settings.setValue("logging/crash_prompt_off", True)
        settings.sync()
    LOG.app.info("Crash-report dialog shown and dismissed")
