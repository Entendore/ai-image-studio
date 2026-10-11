import gc

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QTabWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .backends import LocalSDBackend
from .diagnostics import (CATEGORIES, LOG, build_report, export_bundle,
                          fmt_bytes, gpu_info, ram_info, rss_bytes,
                          run_health_checks)
from .workers import Worker

_LEVEL_FILTERS = [("All levels", None), ("DEBUG", 10), ("INFO", 20),
                  ("WARNING", 30), ("ERROR", 40)]


class DiagnosticsDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Diagnostics")
        self.resize(760, 580)
        self._worker = None

        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs, 1)
        root.addWidget(self._button_row(), 0)

        self.sys_txt = QPlainTextEdit()
        self.sys_txt.setReadOnly(True)
        self.sys_txt.setFont(self._mono())
        tabs.addTab(self._wrap(self.sys_txt, self._sys_buttons), "System")

        mem_page = QWidget()
        mg = QGridLayout(mem_page)
        mg.setContentsMargins(14, 14, 14, 14)
        self.mem_labels = {}
        rows = [("ram", "RAM"), ("gpu", "GPU"), ("vram", "VRAM"),
                ("reserved", "VRAM reserved"), ("rss", "This process"),
                ("cache", "Model cache"), ("pipes", "Loaded pipelines"),
                ("logs", "Log files"), ("idle", "Auto-unload")]
        for i, (key, label) in enumerate(rows):
            mg.addWidget(QLabel(label), i, 0)
            val = QLabel("—")
            val.setFont(self._mono())
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.mem_labels[key] = val
            mg.addWidget(val, i, 1)
        mg.setColumnStretch(1, 1)
        brow = QHBoxLayout()
        for txt, slot in (("Refresh", self._refresh_mem),
                          ("Run garbage collection", self._run_gc),
                          ("⏏ Unload model now", self._unload_now)):
            b = QPushButton(txt)
            b.clicked.connect(slot)
            brow.addWidget(b)
        brow.addStretch(1)
        mg.addLayout(brow, len(rows), 0, 1, 2)
        tabs.addTab(mem_page, "Memory")

        self.health_tree = QTreeWidget()
        self.health_tree.setRootIsDecorated(False)
        self.health_tree.setHeaderLabels(["Component", "Status", "Detail"])
        self.health_tree.setColumnWidth(0, 200)
        self.health_tree.setColumnWidth(1, 90)
        tabs.addTab(self._wrap(self.health_tree, self._health_buttons),
                    "Health check")

        tabs.addTab(self._build_log_tab(), "Log")

        self._refresh_mem()
        self._refresh_sys()
        self._run_health()

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _mono():
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(9)
        return f

    def _wrap(self, widget, buttons_fn):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)
        if buttons_fn is not None:
            row = QHBoxLayout()
            buttons_fn(row)
            row.addStretch(1)
            v.addLayout(row)
        v.addWidget(widget, 1)
        return page

    def _sys_buttons(self, row):
        b = QPushButton("Refresh")
        b.clicked.connect(self._refresh_sys)
        row.addWidget(b)
        b2 = QPushButton("Copy full report")
        b2.clicked.connect(self._copy_report)
        row.addWidget(b2)
        b3 = QPushButton("Export bundle…")
        b3.clicked.connect(self._export_bundle)
        row.addWidget(b3)

    def _health_buttons(self, row):
        b = QPushButton("Run checks")
        b.clicked.connect(self._run_health)
        row.addWidget(b)

    def _button_row(self):
        row = QHBoxLayout()
        hint = QLabel("Use “Copy full report” / “Export bundle” when reporting "
                      "problems. Secrets are redacted automatically.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        row.addWidget(hint)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.clicked.connect(lambda *_: self.accept())
        row.addWidget(bb)
        return row

    # ----------------------------------------------------------------- system
    def _refresh_sys(self):
        self.sys_txt.setPlainText(build_report(self.ctx))

    def _export_bundle(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export diagnostics bundle",
            "ai-image-studio-diagnostics.zip", "Zip (*.zip)")
        if not path:
            return
        try:
            export_bundle(path)
            QMessageBox.information(self, "Bundle exported",
                                    f"Saved to:\n{path}")
        except Exception as e:
            LOG.app.error(f"bundle export failed: {e}")
            QMessageBox.warning(self, "Export failed", str(e))

    # ---------------------------------------------------------------- memory
    def _refresh_mem(self):
        ram, gpu = ram_info(), gpu_info()
        if ram.get("total"):
            pct = (f" ({ram['percent']:.0f}%)"
                   if ram.get("percent") is not None else "")
            self.mem_labels["ram"].setText(
                f"{fmt_bytes(ram['used'])} / {fmt_bytes(ram['total'])}{pct}")
        else:
            self.mem_labels["ram"].setText("unknown (install psutil)")
        self.mem_labels["gpu"].setText(
            f"{gpu['name']} (CUDA {gpu['cuda']}, torch {gpu['torch']})"
            if gpu["available"] else "none detected")
        if gpu["available"]:
            self.mem_labels["vram"].setText(
                f"{fmt_bytes(gpu['used'])} / {fmt_bytes(gpu['total'])} "
                f"(free {fmt_bytes(gpu['free'])})")
            self.mem_labels["reserved"].setText(fmt_bytes(gpu["reserved"]))
        else:
            self.mem_labels["vram"].setText("—")
            self.mem_labels["reserved"].setText("—")
        self.mem_labels["rss"].setText(fmt_bytes(rss_bytes()))
        try:
            self.mem_labels["cache"].setText(
                fmt_bytes(self.ctx.models.total_cache_size()))
        except Exception:
            self.mem_labels["cache"].setText("—")
        be = self.ctx.backends.get("local")
        if isinstance(be, LocalSDBackend):
            st = be.status()
            pipes = ", ".join(st.get("pipelines") or []) or "none"
            self.mem_labels["pipes"].setText(
                f"{pipes}  ·  device {st.get('device')}")
        else:
            self.mem_labels["pipes"].setText("local SD backend unavailable")
        sizes = LOG.file_sizes()
        self.mem_labels["logs"].setText(
            f"studio.log {fmt_bytes(sizes['main'])} · errors.log "
            f"{fmt_bytes(sizes['errors'])} · {LOG.log_dir()}")
        pol = self.ctx.memory_policy
        self.mem_labels["idle"].setText(
            f"after {pol.auto_unload_minutes} min idle" if pol.auto_unload
            else "off")

    def _run_gc(self):
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        LOG.app.info("Manual garbage collection from Diagnostics")
        self._refresh_mem()
        self.ctx.monitor.refresh()

    def _unload_now(self):
        be = self.ctx.backends.get("local")
        if isinstance(be, LocalSDBackend):
            be.unload()
            LOG.app.info("Model unloaded from Diagnostics")
        self._refresh_mem()
        self.ctx.monitor.refresh()

    # ---------------------------------------------------------------- health
    def _run_health(self):
        if self._worker is not None and self._worker.isRunning():
            return
        self.health_tree.clear()
        QTreeWidgetItem(self.health_tree, ["Running checks…", "", ""])
        self._worker = Worker(run_health_checks, parent=self,
                              label="health check")
        self._worker.done.connect(self._health_done)
        self._worker.failed.connect(lambda _m: self._health_done([]))
        self._worker.start()

    def _health_done(self, rows):
        self.health_tree.clear()
        for name, ok, detail, fix in rows:
            status = "OK" if ok else "MISSING"
            item = QTreeWidgetItem([name, status, detail or fix])
            item.setToolTip(2, fix if not ok else "")
            self.health_tree.addTopLevelItem(item)

    # ------------------------------------------------------------------- log
    def _build_log_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        frow = QHBoxLayout()
        frow.addWidget(QLabel("Level"))
        self.level_combo = QComboBox()
        for label, lvl in _LEVEL_FILTERS:
            self.level_combo.addItem(label, lvl)
        self.level_combo.currentIndexChanged.connect(self._render)
        frow.addWidget(self.level_combo)
        frow.addWidget(QLabel("Category"))
        self.cat_combo = QComboBox()
        self.cat_combo.addItem("All", None)
        for cat in CATEGORIES:
            self.cat_combo.addItem(cat, cat)
        self.cat_combo.currentIndexChanged.connect(self._render)
        frow.addWidget(self.cat_combo)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter text…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._render)
        frow.addWidget(self.search_edit, 1)
        self.follow_chk = QCheckBox("Follow")
        self.follow_chk.setChecked(True)
        frow.addWidget(self.follow_chk)
        v.addLayout(frow)

        self.log_txt = QPlainTextEdit()
        self.log_txt.setReadOnly(True)
        self.log_txt.setFont(self._mono())
        self.log_txt.setMaximumBlockCount(20000)
        v.addWidget(self.log_txt, 1)

        brow = QHBoxLayout()
        for txt, slot in (("Copy filtered", self._copy_filtered),
                          ("Export filtered…", self._export),
                          ("Open log folder", self._open_folder),
                          ("Clear buffer", self._clear_buffer)):
            b = QPushButton(txt)
            b.clicked.connect(slot)
            brow.addWidget(b)
        brow.addStretch(1)
        v.addLayout(brow)

        self._info = QLabel()
        self._info.setStyleSheet("color: palette(mid);")
        v.addWidget(self._info)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(250)
        self._render_timer.timeout.connect(self._render)
        LOG.bus.entryAdded.connect(lambda _line: self._render_timer.start())

        self._render()
        return page

    def _filtered(self):
        return LOG.bus.entries(
            level=self.level_combo.currentData(),
            category=self.cat_combo.currentData(),
            contains=self.search_edit.text().strip() or None)

    def _render(self):
        rows = self._filtered()
        self.log_txt.setPlainText("\n".join(r[3] for r in rows))
        sizes = LOG.file_sizes()
        self._info.setText(
            f"{len(rows)} / {len(LOG.bus.entries())} entries in buffer · "
            f"errors this session: {LOG.bus.error_count} · "
            f"studio.log {fmt_bytes(sizes['main'])} · "
            f"errors.log {fmt_bytes(sizes['errors'])}")
        if self.follow_chk.isChecked():
            sb = self.log_txt.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _copy_filtered(self):
        text = "\n".join(r[3] for r in self._filtered())
        QGuiApplication.clipboard().setText(text or "")
        LOG.app.info(f"Copied {len(self._filtered())} filtered log lines")

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export filtered log", "ai-image-studio-log.txt",
            "Text (*.txt *.log)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(r[3] for r in self._filtered()) + "\n")
            LOG.app.info(f"Exported filtered log to {path}")
        except Exception as e:
            LOG.app.error(f"Log export failed: {e}")

    def _open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG.log_dir())))

    def _clear_buffer(self):
        LOG.bus.clear()
        self._render()

    # ------------------------------------------------------------- clipboard
    def _copy_report(self):
        report = build_report(self.ctx)
        checks = "\n".join(
            f"{it.text(0):<20} {it.text(1):<8} {it.text(2)}"
            for i in range(self.health_tree.topLevelItemCount())
            if (it := self.health_tree.topLevelItem(i)).text(0) !=
            "Running checks…")
        recent = "\n".join(e[3] for e in LOG.bus.recent_errors(20))
        QGuiApplication.clipboard().setText(
            f"{report}\n\n== Health ==\n{checks}\n\n"
            f"== Last errors (this session) ==\n{recent or '-'}\n\n"
            f"== Log (tail) ==\n{LOG.bus.text()[-4000:]}")
        LOG.app.info("Full diagnostics report copied to clipboard")
