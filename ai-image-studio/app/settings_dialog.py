import os

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSpinBox)

from .diagnostics import LEVEL_NAMES, LOG
from .memory import STRATEGY_LABELS
from .secrets import keyring_available, resolve_key
from .secrets import store as secrets_store
from .utils import default_output_dir


class SettingsDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.ctx = ctx
        s = ctx.settings
        form = QFormLayout(self)
        form.setContentsMargins(16, 16, 16, 16)

        # -- memory ------------------------------------------------------
        self.strategy_combo = QComboBox()
        for label, value in STRATEGY_LABELS:
            self.strategy_combo.addItem(label, value)
        cur = self.ctx.memory_policy.strategy
        self.strategy_combo.setCurrentIndex(
            max(0, self.strategy_combo.findData(cur)))
        self._old_strategy = cur

        self.auto_unload_chk = QCheckBox("Auto-unload model when idle")
        self.auto_unload_chk.setChecked(self.ctx.memory_policy.auto_unload)
        self.auto_unload = QSpinBox()
        self.auto_unload.setRange(1, 240)
        self.auto_unload.setValue(self.ctx.memory_policy.auto_unload_minutes)
        self.auto_unload.setSuffix(" min")
        self.single_pipe_chk = QCheckBox(
            "Keep only one pipeline in memory (frees others on load)")
        self.single_pipe_chk.setChecked(self.ctx.memory_policy.single_pipeline)

        form.addRow(QLabel("— Memory management —"))
        form.addRow("Strategy", self.strategy_combo)
        form.addRow(self.auto_unload_chk)
        form.addRow("Idle timeout", self.auto_unload)
        form.addRow(self.single_pipe_chk)
        mem_hint = QLabel("CPU-offload strategies cut VRAM use sharply but run "
                          "slower. Changes take effect the next time a model "
                          "loads.")
        mem_hint.setWordWrap(True)
        mem_hint.setStyleSheet("color: palette(mid);")
        form.addRow(mem_hint)

        # -- logging -----------------------------------------------------
        self.log_level = QComboBox()
        for name in LEVEL_NAMES:
            self.log_level.addItem(name.capitalize(), name)
        cur_lvl = str(s.value("logging/level", "info") or "info")
        self.log_level.setCurrentIndex(max(0, self.log_level.findData(cur_lvl)))
        open_logs_btn = QPushButton("Open log folder")
        open_logs_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(LOG.log_dir()))))
        log_hint = QLabel("studio.log records everything from this level up; "
                          "errors.log always records errors. Files rotate "
                          "automatically. Secrets are redacted. "
                          "AIIS_LOG_LEVEL overrides this setting.")
        log_hint.setWordWrap(True)
        log_hint.setStyleSheet("color: palette(mid);")
        form.addRow(QLabel("— Logging —"))
        form.addRow("Log level", self.log_level)
        form.addRow(open_logs_btn)
        form.addRow(log_hint)

        # -- updates -------------------------------------------------------
        self.upd_auto = QCheckBox(
            "Check for updates on startup (contacts GitHub)")
        self.upd_auto.setChecked(
            str(s.value("update/auto", "")).lower() in ("true", "1"))
        form.addRow(QLabel("— Updates —"))
        form.addRow(self.upd_auto)

        # -- api / paths ---------------------------------------------------
        self.oai_base = QLineEdit(s.value("api/openai_base",
                                          "https://api.openai.com/v1"))
        self._keyring_ok = keyring_available()
        self.oai_key = QLineEdit(resolve_key(s))
        self.oai_key.setEchoMode(QLineEdit.Password)
        self.oai_key.setPlaceholderText("sk-…")
        self.keyring_chk = QCheckBox(
            "Store API key in the system keyring (recommended)")
        self.keyring_chk.setChecked(self._keyring_ok)
        self.keyring_chk.setEnabled(self._keyring_ok)
        self.hf_token = QLineEdit(s.value("api/hf_token", ""))
        self.hf_token.setEchoMode(QLineEdit.Password)
        self.hf_token.setPlaceholderText("hf_… (needed for gated models)")
        self.oai_model = QLineEdit(s.value("api/openai_model", "dall-e-3"))
        self.out_dir = QLineEdit(s.value("paths/output", default_output_dir()))
        browse = QPushButton("…")
        browse.setFixedWidth(32)
        browse.clicked.connect(self._browse)

        form.addRow(QLabel("— OpenAI-compatible API —"))
        form.addRow("Base URL", self.oai_base)
        if os.environ.get("OPENAI_API_KEY"):
            env_note = QLabel("OPENAI_API_KEY is set in the environment — it "
                              "will be used if no key is stored here.")
            env_note.setWordWrap(True)
            env_note.setStyleSheet("color: palette(mid);")
            form.addRow(env_note)
        form.addRow("API key", self.oai_key)
        form.addRow(self.keyring_chk)
        if not self._keyring_ok:
            warn = QLabel("No system keyring detected — the API key would be "
                          "stored in plaintext settings.")
            warn.setWordWrap(True)
            warn.setStyleSheet("color: palette(mid);")
            form.addRow(warn)
        form.addRow("HF token", self.hf_token)
        form.addRow("Model", self.oai_model)
        h = QHBoxLayout()
        h.addWidget(self.out_dir)
        h.addWidget(browse)
        form.addRow("Output folder", h)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Output folder",
                                             self.out_dir.text())
        if d:
            self.out_dir.setText(d)

    def accept(self):
        s = self.ctx.settings
        s.setValue("memory/strategy", self.strategy_combo.currentData())
        s.setValue("memory/auto_unload", self.auto_unload_chk.isChecked())
        s.setValue("memory/auto_unload_min", self.auto_unload.value())
        s.setValue("memory/single_pipeline", self.single_pipe_chk.isChecked())
        s.setValue("logging/level", self.log_level.currentData())
        s.setValue("update/auto", self.upd_auto.isChecked())

        key = self.oai_key.text().strip()
        if self.keyring_chk.isEnabled() and self.keyring_chk.isChecked():
            if key:
                try:
                    secrets_store(key)
                    s.setValue("api/openai_key", "")     # scrub plaintext copy
                except Exception as exc:
                    LOG.app.warn(f"keyring store failed ({exc}); keeping "
                                 "key in settings")
                    s.setValue("api/openai_key", key)
            else:
                s.setValue("api/openai_key", "")
        else:
            s.setValue("api/openai_key", key)
        s.setValue("api/hf_token", self.hf_token.text().strip())
        s.setValue("api/openai_base", self.oai_base.text().strip())
        s.setValue("api/openai_model", self.oai_model.text().strip())
        s.setValue("paths/output", self.out_dir.text().strip())
        s.sync()

        LOG.set_level(self.log_level.currentData())   # applies immediately
        new_strategy = self.ctx.memory_policy.strategy
        if new_strategy != self._old_strategy:
            be = self.ctx.backends.get("local")
            if be is not None and getattr(be, "loaded", False):
                be.unload()
                LOG.memory.info(f"Memory strategy changed "
                                f"({self._old_strategy} → {new_strategy}); "
                                "model unloaded, policy applies on next load")
        self.ctx.rebuild()
        super().accept()
