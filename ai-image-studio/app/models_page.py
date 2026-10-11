import os

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                               QProgressBar, QPushButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .backends import LocalSDBackend
from .errors import ErrorDialog, JobCancelled, UserError, humanize
from .model_manager import human_size


class ModelsPage(QWidget):
    statusMessage = Signal(str)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._build_ui()
        self.ctx.models.changed.connect(self.refresh)
        self.ctx.models.activeChanged.connect(self.refresh)
        self.ctx.models.downloadProgress.connect(self._on_progress)
        self.ctx.models.downloadFinished.connect(self._on_finished)
        self.refresh()

    # ------------------------------------------------------------------- UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        title = QLabel("🧠 Model manager")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        root.addWidget(title)

        self.avail_label = QLabel()
        self.avail_label.setWordWrap(True)
        self.avail_label.setStyleSheet("color: palette(mid);")
        root.addWidget(self.avail_label)

        self.state_label = QLabel()
        self.state_label.setWordWrap(True)
        self.state_label.setStyleSheet("color: palette(mid);")
        root.addWidget(self.state_label)

        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setHeaderLabels(["Model", "Purpose", "Size", "Status"])
        self.tree.setColumnWidth(0, 400)
        self.tree.setColumnWidth(1, 100)
        self.tree.setColumnWidth(2, 100)
        self.tree.itemSelectionChanged.connect(self._update_details)
        root.addWidget(self.tree, 1)

        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.RichText)
        self.details.setStyleSheet("color: palette(mid);")
        root.addWidget(self.details)

        btns = QHBoxLayout()
        self.dl_btn = QPushButton("⬇  Download")
        self.gen_btn = QPushButton("✦  Use for Generate")
        self.inp_btn = QPushButton("✦  Use for Fill/Expand")
        self.unload_btn = QPushButton("⏏  Unload from memory")
        self.del_btn = QPushButton("🗑  Delete")
        for b in (self.dl_btn, self.gen_btn, self.inp_btn,
                  self.unload_btn, self.del_btn):
            btns.addWidget(b)
        btns.addStretch(1)
        self.local_btn = QPushButton("＋ Add local checkpoint…")
        self.cache_btn = QPushButton("Open cache folder")
        btns.addWidget(self.local_btn)
        btns.addWidget(self.cache_btn)
        root.addLayout(btns)

        prow = QHBoxLayout()
        self.prog_label = QLabel()
        self.prog_label.hide()
        self.progress = QProgressBar()
        self.progress.hide()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self._cancel)
        prow.addWidget(self.prog_label, 0)
        prow.addWidget(self.progress, 1)
        prow.addWidget(self.cancel_btn, 0)
        root.addLayout(prow)

        self.dl_btn.clicked.connect(self._download)
        self.gen_btn.clicked.connect(lambda: self._set_active("gen"))
        self.inp_btn.clicked.connect(lambda: self._set_active("inpaint"))
        self.unload_btn.clicked.connect(self._unload)
        self.del_btn.clicked.connect(self._delete)
        self.local_btn.clicked.connect(self._add_local)
        self.cache_btn.clicked.connect(self._open_cache)

    # ------------------------------------------------------------- selection
    def _selected(self):
        items = self.tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.UserRole)   # ("repo"|"local", id)

    # -------------------------------------------------------------- refresh
    def refresh(self):
        mgr = self.ctx.models
        if mgr.diffusers_available:
            self.avail_label.setText(
                "Download models once — they're cached on disk and reused "
                "offline. Pick which model the Generate tab and Fill/Expand use.")
        else:
            self.avail_label.setText(
                "Running local models needs torch + diffusers (see "
                "requirements.txt). Downloads and cache can still be managed.")

        be = self.ctx.backends.get("local")
        st = be.status() if isinstance(be, LocalSDBackend) else {}
        loaded_model = st.get("model") if st.get("loaded") else None
        device = st.get("device")

        if isinstance(be, LocalSDBackend):
            if st.get("loaded"):
                dtype = f" · {st.get('dtype')}" if st.get("dtype") else ""
                self.state_label.setText(
                    f"In memory: {loaded_model} · {device}{dtype} · "
                    f"pipelines: {', '.join(st.get('pipelines') or [])}")
            else:
                self.state_label.setText(
                    "No model loaded in memory (loads automatically on first "
                    "generation, or pick one and use Unload to free VRAM).")
        else:
            self.state_label.setText(
                "Local Stable Diffusion backend not available — install torch "
                "+ diffusers to enable it.")

        gen_active = mgr.active_gen_model()
        inp_active = mgr.active_inpaint_model()

        self.tree.clear()
        for m in mgr.catalog():
            rid = m["repo_id"]
            cached = mgr.is_cached(rid)
            status = []
            if rid in (gen_active, inp_active):
                status.append("★ active")
            if loaded_model == rid:
                status.append(f"loaded ({device})")
            status.append("on disk" if cached else
                          ("not downloaded" if mgr.hub_available
                           else "hub unavailable"))
            it = QTreeWidgetItem([rid, m["purpose"], m["size"],
                                  " · ".join(status)])
            it.setData(0, Qt.UserRole, ("repo", rid))
            it.setToolTip(0, m["desc"])
            self.tree.addTopLevelItem(it)

        for path in mgr.local_models():
            try:
                size = human_size(os.path.getsize(path))
            except OSError:
                size = "—"
            status = ["local file"]
            if path == gen_active:
                status.append("★ active")
            if loaded_model == path:
                status.append(f"loaded ({device})")
            it = QTreeWidgetItem([os.path.basename(path), "generate", size,
                                  " · ".join(status)])
            it.setData(0, Qt.UserRole, ("local", path))
            it.setToolTip(0, path)
            self.tree.addTopLevelItem(it)

        self._update_details()

    def _update_details(self):
        mgr = self.ctx.models
        data = self._selected()
        if not data:
            self.details.setText("Select a model to see details.")
            for b in (self.dl_btn, self.gen_btn, self.inp_btn,
                      self.unload_btn, self.del_btn):
                b.setEnabled(False)
            return
        kind, mid = data
        busy = self.ctx.jobs.busy
        if kind == "repo":
            m = next((c for c in mgr.catalog() if c["repo_id"] == mid), None)
            cached = mgr.is_cached(mid)
            txt = (f"<b>{mid}</b><br>{m['desc'] if m else ''}<br>"
                   f"Download size {m['size'] if m else '?'} · "
                   f"{m['vram'] if m else '?'} VRAM recommended")
            if cached:
                txt += (f"<br>On disk: {human_size(mgr.disk_usage(mid))} · "
                        f"{mgr.repo_dir(mid)}")
            self.dl_btn.setEnabled(mgr.hub_available and not cached and not busy)
        else:
            txt = (f"<b>{os.path.basename(mid)}</b><br>Local checkpoint, "
                   f"loaded via from_single_file (Generate / Image → Image)."
                   f"<br>{mid}")
            self.dl_btn.setEnabled(False)
        self.details.setText(txt)
        self.gen_btn.setEnabled(not busy)
        self.inp_btn.setEnabled(kind == "repo" and not busy)
        self.unload_btn.setEnabled(True)
        self.del_btn.setEnabled(not busy)

    # -------------------------------------------------------------- actions
    def _download(self):
        data = self._selected()
        if not data or data[0] != "repo":
            return
        mgr = self.ctx.models
        if not mgr.hub_available:
            QMessageBox.information(
                self, "huggingface_hub missing",
                "Downloads need huggingface_hub:\n"
                "  pip install huggingface_hub")
            return
        repo = data[1]
        try:
            self.ctx.jobs.submit(
                f"download {repo}", mgr.download,
                kwargs={"repo_id": repo},
                on_done=lambda _r: None,
                on_fail=self._on_failed_exc,
                on_finish=self._on_dl_finish)
        except UserError as exc:
            self.statusMessage.emit(str(exc))
            return
        self.progress.show()
        self.cancel_btn.show()
        self.prog_label.show()
        self.prog_label.setText("Starting…")
        self.progress.setRange(0, 0)
        self._update_details()

    def _cancel(self):
        if self.ctx.jobs.current_label.startswith("download"):
            self.ctx.jobs.cancel_current()
            self.prog_label.setText("Cancelling…")

    def _on_progress(self, _repo, pct, label):
        self.prog_label.setText(label)
        if pct < 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(pct)

    def _on_finished(self, repo, ok, msg):
        self.statusMessage.emit(f"{repo}: {msg}")

    def _on_dl_finish(self, cancelled=False):
        self.progress.hide()
        self.cancel_btn.hide()
        self.prog_label.hide()
        self.refresh()
        if cancelled:
            self.statusMessage.emit("Download cancelled.")

    def _on_failed_exc(self, exc):
        self.progress.hide()
        self.cancel_btn.hide()
        self.prog_label.hide()
        self.refresh()
        if isinstance(exc, JobCancelled):
            self.statusMessage.emit("Download cancelled.")
            return
        title, msg, details = humanize(exc)
        ErrorDialog(title, msg, details, self).exec()

    def _set_active(self, which):
        data = self._selected()
        if not data:
            return
        kind, mid = data
        mgr = self.ctx.models
        try:
            if which == "gen":
                mgr.set_active_gen_model(mid)
                self.statusMessage.emit(f"Generate tab now uses {mid}")
            else:
                if kind != "repo":
                    QMessageBox.information(
                        self, "Not inpaint-capable",
                        "Local checkpoint files can't be used for inpainting. "
                        "Pick a dedicated inpaint model from the catalog.")
                    return
                mgr.set_active_inpaint_model(mid)
                self.statusMessage.emit(f"Fill/Expand now uses {mid}")
        except Exception as e:
            QMessageBox.warning(self, "Could not set model", str(e))
        self.refresh()

    def _unload(self):
        be = self.ctx.backends.get("local")
        if isinstance(be, LocalSDBackend):
            be.unload()
            self.refresh()
            self.statusMessage.emit("Model unloaded from memory")

    def _delete(self):
        data = self._selected()
        if not data:
            return
        kind, mid = data
        mgr = self.ctx.models
        be = self.ctx.backends.get("local")
        if kind == "repo":
            used = mgr.disk_usage(mid)
            ret = QMessageBox.question(
                self, "Delete model",
                f"Delete downloaded files of\n{mid}\n"
                f"({human_size(used)}) from disk?\n\n"
                "You can re-download it later.",
                QMessageBox.Yes | QMessageBox.No)
            if ret != QMessageBox.Yes:
                return
        else:
            ret = QMessageBox.question(
                self, "Remove local model",
                f"Remove {os.path.basename(mid)} from the model list?\n\n"
                "(The file itself is not deleted.)",
                QMessageBox.Yes | QMessageBox.No)
            if ret != QMessageBox.Yes:
                return
        if isinstance(be, LocalSDBackend):
            st = be.status()
            if st.get("loaded") and st.get("model") == mid:
                be.unload()
        mgr.delete(mid)
        self.refresh()
        self.statusMessage.emit(f"Deleted {mid}")

    def _add_local(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Add local checkpoint", "",
            "Model files (*.safetensors *.ckpt *.pt *.pth)")
        if not path:
            return
        self.ctx.models.add_local(path)
        self.statusMessage.emit(f"Added {os.path.basename(path)}")

    def _open_cache(self):
        QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self.ctx.models.hub_cache_path())))
