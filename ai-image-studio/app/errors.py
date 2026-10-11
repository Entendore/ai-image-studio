"""User-friendly error handling: UserError, JobCancelled, humanize, ErrorDialog."""
from __future__ import annotations

import traceback

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QVBoxLayout)


class UserError(Exception):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


class JobCancelled(Exception):
    pass


def humanize(exc: BaseException) -> tuple[str, str, str]:
    """Map any exception to (title, user_message, technical_details)."""
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    name = type(exc).__name__
    msg = str(exc).strip() or name
    low = msg.lower()

    if isinstance(exc, UserError):
        hint = f"\n\n{exc.hint}" if exc.hint else ""
        return ("Something went wrong", msg + hint, tb)
    if name == "GatedRepoError" or "gated" in low or \
            "must have access" in low or "requires you to accept" in low or \
            (name == "HTTPError" and ("401" in msg or "403" in msg)):
        return ("Model access denied",
                "This model repository is gated: accept its license on "
                "huggingface.co and/or add a HuggingFace token in Settings.", tb)
    if "out of memory" in low:
        return ("Not enough GPU memory",
                msg + "\n\nTry: a smaller resolution, fewer steps, or the "
                      "'Low / Ultra-low VRAM' strategy in Settings.", tb)
    if name == "UnidentifiedImageError":
        return ("Unrecognized image",
                "This file could not be opened as an image.", tb)
    if "decompressionbomb" in name.lower() or "decompression" in low:
        return ("Image too large", "This image is too large to open safely.", tb)
    if isinstance(exc, FileNotFoundError):
        return ("File not found", msg, tb)
    if isinstance(exc, PermissionError):
        return ("Permission denied", msg + "\n\nCheck the folder permissions.", tb)
    if name in ("ConnectionError", "ConnectTimeout", "ReadTimeout",
                "ChunkedEncodingError", "ProtocolError") or \
            isinstance(exc, (ConnectionError, TimeoutError)):
        return ("Network problem",
                "Could not reach the server.\n\nCheck your internet connection, "
                "proxy settings, or the configured URL.", tb)
    if "certificate" in low or "ssl" in low:
        return ("Secure connection failed", msg, tb)
    if "429" in msg or "rate limit" in low:
        return ("Rate limited",
                "The service is limiting requests (HTTP 429). "
                "Wait a moment and try again.", tb)
    if isinstance(exc, MemoryError):
        return ("Out of memory",
                "The system ran out of RAM.\n\nTry a smaller image, lower "
                "resolution, or the 'Low / Ultra-low VRAM' strategy in Settings.",
                tb)
    if isinstance(exc, NotImplementedError):
        return ("Not supported", msg, tb)
    return ("Unexpected error", f"{name}: {msg}", tb)


class ErrorDialog(QDialog):
    """Friendly message, opt-in technical details, copy button."""

    def __init__(self, title: str, message: str, details: str = "",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        head = QLabel(title)
        head.setStyleSheet("font-size: 15px; font-weight: 700;")
        v.addWidget(head)
        body = QLabel(message)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(body)

        self._details = QPlainTextEdit()
        self._details.setReadOnly(True)
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(8)
        self._details.setFont(f)
        self._details.setPlainText(details or "—")
        self._details.hide()
        self._details_btn = QPushButton("Show technical details")
        self._details_btn.setCheckable(True)
        self._details_btn.toggled.connect(self._toggle_details)
        v.addWidget(self._details_btn)
        v.addWidget(self._details, 1)

        row = QHBoxLayout()
        copy_btn = QPushButton("Copy details")
        copy_btn.clicked.connect(self._copy)
        row.addWidget(copy_btn)
        row.addStretch(1)
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.reject)
        row.addWidget(close)
        v.addLayout(row)

    def _toggle_details(self, on: bool):
        self._details.setVisible(on)
        self._details_btn.setText("Hide technical details" if on
                                  else "Show technical details")

    def _copy(self):
        QGuiApplication.clipboard().setText(
            f"{self.windowTitle()}\n\n{self._details.toPlainText()}")
