import os
import random

from PIL import Image
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QSplitter, QVBoxLayout, QWidget,
)

from .backends import DemoBackend, LocalSDBackend, OpenAIImageBackend
from .errors import ErrorDialog, UserError, humanize
from .persistence import (atomic_save_image, open_image, restore_splitter,
                          save_splitter)
from .utils import default_output_dir, pil_to_pixmap, pil_thumbnail_pixmap
from .widgets import PromptForm, Segmented

HISTORY_LIMIT = 24

MODE_TXT, MODE_IMG = 0, 1
PROMPT_FORM, PROMPT_FREE = 0, 1

STYLES = {
    "None": "",
    "Photorealistic": "photorealistic, ultra detailed, 8k, sharp focus, natural lighting",
    "Cinematic": "cinematic shot, dramatic lighting, shallow depth of field, film grain",
    "Digital Art": "digital painting, concept art, highly detailed, artstation",
    "Anime": "anime style, clean line art, vibrant colors, studio quality",
    "3D Render": "3d render, octane render, soft studio lighting, high detail",
    "Watercolor": "watercolor painting, soft edges, paper texture",
    "Product Shot": "product photography, studio lighting, clean background, commercial",
}

ASPECTS = (("1:1", 512, 512), ("3:2", 768, 512), ("2:3", 512, 768),
           ("16:9", 1024, 576))

GEN_FIELDS = (
    ("subject", "Subject", "e.g. a weathered lighthouse on a cliff"),
    ("action", "Action / scene", "e.g. waves crashing below, gulls circling"),
    ("environment", "Environment", "e.g. rocky coastline at dusk"),
    ("lighting", "Lighting", "e.g. golden hour, soft rim light"),
    ("colors", "Colors / mood", "e.g. muted teal and amber palette"),
    ("camera", "Camera / composition", "e.g. wide-angle, low angle, 35mm"),
    ("details", "Details / quality", "e.g. highly detailed, sharp focus"),
    ("extra", "Extra tags", "anything else, comma separated"),
)

CHIPS = ["8k", "ultra detailed", "sharp focus", "volumetric light",
         "film grain", "bokeh", "matte painting", "trending on artstation"]


def run_batch(method, backend, base_params, count):
    """Run `count` generations in one worker; map per-image progress to overall.
    Raises JobCancelled between images when the user cancels."""
    outer = backend.progress_cb
    images = []
    base_seed = base_params.get("seed", -1)
    try:
        for i in range(count):
            backend._check_cancel()
            params = dict(base_params)
            if base_seed >= 0:
                params["seed"] = base_seed + i
            if outer:
                def cb(p, i=i):
                    outer(int((i * 100 + max(0, min(100, p))) / count))
                backend.progress_cb = cb
            images.append(method(**params))
    finally:
        backend.progress_cb = outer
    return images


class _InitDrop(QLabel):
    """Drag-&-drop / click-to-browse box holding the img2img init image."""
    picked = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumHeight(120)
        self.setStyleSheet(
            "border: 2px dashed palette(mid); border-radius: 8px;"
            "color: palette(mid); background: palette(window);")
        self._set_empty()

    def _set_empty(self):
        self.setText("Drop an init image here\nor click to browse…")

    def setImage(self, img):
        self.image = img
        if img is None:
            self._set_empty()
            self.setPixmap(QPixmap())
            return
        pm = pil_to_pixmap(img)
        self.setText("")
        self.setPixmap(pm.scaled(self.width() - 16, 140,
                                 Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def mousePressEvent(self, e):
        if self.image is None:
            self._browse()
        else:
            ret = QMessageBox.question(
                self, "Init image", "Replace the init image?",
                QMessageBox.Yes | QMessageBox.No)
            if ret == QMessageBox.Yes:
                self._browse()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Init image", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            try:
                self.picked.emit(open_image(path))
            except UserError as exc:
                ErrorDialog("Open failed", str(exc), "", self).exec()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if path:
                try:
                    self.picked.emit(open_image(path))
                except UserError as exc:
                    ErrorDialog("Open failed", str(exc), "", self).exec()
                break


class GeneratePage(QWidget):
    sendToEditor = Signal(object)
    statusMessage = Signal(str)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._pending_generate = False
        self._pending = None            # (backend, method, params)
        self._history = []
        self._preview_img = None
        self._preview_pm = None
        self._last_prompt = ""
        self._build_ui()
        self.ctx.backendsChanged.connect(self.refresh_backends)
        self.ctx.activeBackendChanged.connect(self._sync_combo)
        self.ctx.models.activeChanged.connect(self._update_note)
        self.refresh_backends()

    # ------------------------------------------------------------------- UI
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        splitter = QSplitter(Qt.Horizontal)
        self._hsplit = splitter
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter)

        # ------------------------------------------------ left: settings panel
        panel = QWidget()
        panel.setMinimumWidth(348)
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        form = QVBoxLayout(inner)
        form.setSpacing(10)
        scroll.setWidget(inner)
        pv.addWidget(scroll)
        splitter.addWidget(panel)

        g_model = QGroupBox("Model")
        mv = QVBoxLayout(g_model)
        self.backend_combo = QComboBox()
        self.backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        mv.addWidget(self.backend_combo)
        srow = QHBoxLayout()
        srow.addWidget(QLabel("Sampler"))
        self.sampler_combo = QComboBox()
        self.sampler_combo.addItems(list(LocalSDBackend.SAMPLERS.keys()))
        self.sampler_combo.currentTextChanged.connect(self._on_sampler_changed)
        srow.addWidget(self.sampler_combo, 1)
        mv.addLayout(srow)
        self.backend_note = QLabel()
        self.backend_note.setWordWrap(True)
        self.backend_note.setStyleSheet("color: palette(mid);")
        mv.addWidget(self.backend_note)
        form.addWidget(g_model)

        g_mode = QGroupBox("Mode")
        mv2 = QVBoxLayout(g_mode)
        self.mode_seg = Segmented(["Text → Image", "Image → Image"])
        self.mode_seg.changed.connect(self._on_mode_changed)
        mv2.addWidget(self.mode_seg)
        self.mode_hint = QLabel()
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setStyleSheet("color: palette(mid);")
        mv2.addWidget(self.mode_hint)

        self.init_group = QWidget()
        ig = QVBoxLayout(self.init_group)
        ig.setContentsMargins(0, 4, 0, 0)
        ig.setSpacing(6)
        self.init_drop = _InitDrop()
        self.init_drop.picked.connect(self._set_init_image)
        ig.addWidget(self.init_drop)
        irow = QHBoxLayout()
        use_last = QPushButton("Use last result")
        clear_init = QPushButton("Clear")
        use_last.clicked.connect(self._use_last_result)
        clear_init.clicked.connect(lambda: self._set_init_image(None))
        irow.addWidget(use_last)
        irow.addWidget(clear_init)
        irow.addStretch(1)
        ig.addLayout(irow)
        s2 = QHBoxLayout()
        s2.addWidget(QLabel("Strength"))
        self.strength = QSlider(Qt.Horizontal)
        self.strength.setRange(5, 95)
        self.strength.setValue(65)
        self.strength_val = QLabel("0.65")
        self.strength.valueChanged.connect(
            lambda v: self.strength_val.setText(f"{v / 100:.2f}"))
        s2.addWidget(self.strength, 1)
        s2.addWidget(self.strength_val)
        ig.addLayout(s2)
        self.init_note = QLabel("Output keeps the init image size. "
                                "API backends ignore strength.")
        self.init_note.setWordWrap(True)
        self.init_note.setStyleSheet("color: palette(mid);")
        ig.addWidget(self.init_note)
        mv2.addWidget(self.init_group)
        self.init_group.setVisible(False)
        form.addWidget(g_mode)

        g_prompt = QGroupBox("Prompt")
        pv2 = QVBoxLayout(g_prompt)
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Style"))
        self.style_combo = QComboBox()
        self.style_combo.addItems(list(STYLES.keys()))
        prow.addWidget(self.style_combo, 1)
        pv2.addLayout(prow)
        self.prompt_mode = Segmented(["🧩 Structured form", "✏️ Free text"])
        self.prompt_mode.changed.connect(self._on_prompt_mode_changed)
        pv2.addWidget(self.prompt_mode)
        self.prompt_stack = QStackedWidget()
        self.pform = PromptForm(GEN_FIELDS, chips=CHIPS, show_preview=True)
        self.prompt_stack.addWidget(self.pform)
        self.free_text = QPlainTextEdit()
        self.free_text.setPlaceholderText("Describe the image in one passage…")
        self.prompt_stack.addWidget(self.free_text)
        pv2.addWidget(self.prompt_stack)
        self.negative = QPlainTextEdit()
        self.negative.setPlaceholderText("Negative prompt (what to avoid)…")
        self.negative.setFixedHeight(48)
        pv2.addWidget(self.negative)
        form.addWidget(g_prompt)

        self.size_group = QGroupBox("Output size")
        sv = QGridLayout(self.size_group)
        arow = QHBoxLayout()
        for label, w, h in ASPECTS:
            b = QPushButton(label)
            b.setFixedWidth(48)
            b.setToolTip(f"{w} × {h}")
            b.clicked.connect(lambda _=False, W=w, H=h: (
                self.width_spin.setValue(W), self.height_spin.setValue(H)))
            arow.addWidget(b)
        arow.addStretch(1)
        sv.addLayout(arow, 0, 0, 1, 2)
        sv.addWidget(QLabel("Width"), 1, 0)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(64, 2048)
        self.width_spin.setSingleStep(8)
        self.width_spin.setValue(512)
        sv.addWidget(QLabel("Height"), 2, 0)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(64, 2048)
        self.height_spin.setSingleStep(8)
        self.height_spin.setValue(512)
        sv.addWidget(QLabel("Batch"), 3, 0)
        self.batch_spin = QSpinBox()
        self.batch_spin.setRange(1, 8)
        self.batch_spin.setValue(1)
        sv.addWidget(self.width_spin, 1, 1)
        sv.addWidget(self.height_spin, 2, 1)
        sv.addWidget(self.batch_spin, 3, 1)
        form.addWidget(self.size_group)

        g_q = QGroupBox("Quality")
        qv = QGridLayout(g_q)
        qv.addWidget(QLabel("Steps"), 0, 0)
        self.steps = QSlider(Qt.Horizontal)
        self.steps.setRange(4, 60)
        self.steps.setValue(25)
        self.steps_val = QLabel("25")
        self.steps.valueChanged.connect(lambda v: self.steps_val.setText(str(v)))
        qv.addWidget(self.steps, 0, 1)
        qv.addWidget(self.steps_val, 0, 2)
        qv.addWidget(QLabel("Guidance"), 1, 0)
        self.cfg = QSlider(Qt.Horizontal)
        self.cfg.setRange(10, 150)
        self.cfg.setValue(70)
        self.cfg_val = QLabel("7.0")
        self.cfg.valueChanged.connect(lambda v: self.cfg_val.setText(f"{v / 10:.1f}"))
        qv.addWidget(self.cfg, 1, 1)
        qv.addWidget(self.cfg_val, 1, 2)
        form.addWidget(g_q)

        g_seed = QGroupBox("Seed")
        h = QHBoxLayout(g_seed)
        self.random_seed = QCheckBox("Random")
        self.random_seed.setChecked(True)
        self.seed = QSpinBox()
        self.seed.setRange(-1, 2**31 - 1)
        self.seed.setValue(-1)
        dice = QPushButton("🎲")
        dice.setFixedWidth(40)
        dice.clicked.connect(
            lambda: self.seed.setValue(random.randint(0, 2**31 - 1)))
        h.addWidget(self.random_seed)
        h.addWidget(self.seed)
        h.addWidget(dice)
        form.addWidget(g_seed)

        self.generate_btn = QPushButton("✨  Generate")
        self.generate_btn.setObjectName("accent")
        self.generate_btn.setFixedHeight(42)
        self.generate_btn.clicked.connect(self._on_generate)
        form.addWidget(self.generate_btn)
        self.progress = QProgressBar()
        self.progress.hide()
        form.addWidget(self.progress)
        self.cancel_btn = QPushButton("✖  Cancel")
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self._cancel_job)
        form.addWidget(self.cancel_btn)
        self.busy_label = QLabel()
        self.busy_label.setWordWrap(True)
        self.busy_label.setStyleSheet("color: palette(mid);")
        form.addWidget(self.busy_label)
        form.addStretch(1)

        # ---------------------------------------- right: preview + history
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)
        vsplit = QSplitter(Qt.Vertical)
        self._vsplit = vsplit
        vsplit.setChildrenCollapsible(False)
        rv.addWidget(vsplit)

        self.preview = QLabel("No image yet — build a prompt and hit Generate.")
        self.preview.setObjectName("previewArea")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(380, 280)
        self.preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        vsplit.addWidget(self.preview)

        hist_widget = QWidget()
        hv = QVBoxLayout(hist_widget)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(6)
        hrow = QHBoxLayout()
        self.hist_title = QLabel("History")
        self.hist_title.setStyleSheet(
            "font-weight: 600; color: palette(mid);")
        self.hist_clear = QPushButton("Clear")
        self.hist_clear.setFixedHeight(26)
        self.hist_clear.clicked.connect(self._clear_history)
        hrow.addWidget(self.hist_title)
        hrow.addStretch(1)
        hrow.addWidget(self.hist_clear)
        hv.addLayout(hrow)
        self.history_list = QListWidget()
        self.history_list.setViewMode(QListWidget.IconMode)
        self.history_list.setFlow(QListWidget.LeftToRight)
        self.history_list.setWrapping(False)
        self.history_list.setFixedHeight(132)
        self.history_list.setIconSize(QSize(104, 104))
        self.history_list.setUniformItemSizes(True)
        self.history_list.itemSelectionChanged.connect(self._history_selected)
        hv.addWidget(self.history_list)
        vsplit.addWidget(hist_widget)
        vsplit.setStretchFactor(0, 4)
        vsplit.setStretchFactor(1, 1)
        vsplit.setSizes([560, 170])
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([360, 1000])

        self._on_mode_changed(MODE_TXT)
        restore_splitter(self._hsplit, self.ctx.settings, "gen_split")
        restore_splitter(self._vsplit, self.ctx.settings, "gen_vsplit")

    # ---------------------------------------------------------- mode logic
    def _on_mode_changed(self, idx):
        is_img = idx == MODE_IMG
        self.init_group.setVisible(is_img)
        self.size_group.setVisible(not is_img)
        self.mode_hint.setText(
            "Re-imagine an existing image — lower strength stays closer to "
            "the original." if is_img
            else "Standard text-to-image generation from your prompt below.")

    def _on_prompt_mode_changed(self, idx):
        self.prompt_stack.setCurrentIndex(idx)
        if idx == PROMPT_FREE:
            combined = self.pform.combined()
            if combined and not self.free_text.toPlainText().strip():
                self.free_text.setPlainText(combined)

    def _set_init_image(self, img):
        self.init_drop.setImage(img)

    def _use_last_result(self):
        src = self._preview_img or (self._history[0][0] if self._history else None)
        if src is None:
            self.statusMessage.emit("No result yet to use as init image.")
            return
        self._set_init_image(src)

    # ------------------------------------------------------------- backends
    def refresh_backends(self):
        combo = self.backend_combo
        combo.blockSignals(True)
        combo.clear()
        for key, be in self.ctx.backends.items():
            combo.addItem(be.name, key)
        idx = combo.findData(self.ctx.active_key)
        combo.setCurrentIndex(max(0, idx))
        combo.blockSignals(False)
        self._update_note()

    def _sync_combo(self, key):
        idx = self.backend_combo.findData(key)
        if idx >= 0 and idx != self.backend_combo.currentIndex():
            self.backend_combo.blockSignals(True)
            self.backend_combo.setCurrentIndex(idx)
            self.backend_combo.blockSignals(False)
            self._update_note()

    def _on_backend_changed(self, *_):
        key = self.backend_combo.currentData()
        if key:
            self.ctx.active_key = key
            self._update_note()

    def _on_sampler_changed(self, name):
        be = self.ctx.current_backend()
        if isinstance(be, LocalSDBackend):
            be.set_sampler(name)

    def _update_note(self):
        be = self.ctx.current_backend()
        if isinstance(be, DemoBackend):
            note = ("Procedural placeholder art — all modes work offline. "
                    "Install torch + diffusers or add an API key in Settings "
                    "for real AI.")
        elif isinstance(be, LocalSDBackend):
            note = (f"Generate tab model: {be.gen_model_id()} — download, "
                    "switch and unload models in the 🧠 Models tab.")
        elif isinstance(be, OpenAIImageBackend):
            note = (f"Model '{be.model}'. Image → Image needs 'gpt-image-1' "
                    "or 'dall-e-2' (uses /images/edits).")
        else:
            note = ""
        self.backend_note.setText(note)

    # ------------------------------------------------------------ generation
    def _current_prompt(self):
        if self.prompt_mode.currentIndex() == PROMPT_FORM:
            return self.pform.combined()
        return self.free_text.toPlainText().strip()

    def _on_generate(self):
        if self.ctx.jobs.busy:
            self.statusMessage.emit(
                "Busy — wait for the current job or cancel it.")
            return
        base = self._current_prompt()
        if not base:
            self.statusMessage.emit(
                "Enter a prompt first (form or free text).")
            return
        be = self.ctx.current_backend()
        mode = self.mode_seg.currentIndex()

        init = None
        if mode == MODE_IMG:
            init = self.init_drop.image
            if init is None:
                self.statusMessage.emit(
                    "Provide an init image for Image → Image.")
                return

        if isinstance(be, LocalSDBackend):
            be.set_sampler(self.sampler_combo.currentText())
        if self.random_seed.isChecked():
            self.seed.setValue(random.randint(0, 2**31 - 1))

        suffix = STYLES.get(self.style_combo.currentText(), "")
        full = f"{base}, {suffix}" if suffix else base
        self._last_prompt = full
        params = dict(prompt=full,
                      negative=self.negative.toPlainText().strip(),
                      steps=self.steps.value(),
                      guidance=self.cfg.value() / 10,
                      seed=self.seed.value())

        if mode == MODE_TXT:
            params.update(width=self.width_spin.value(),
                          height=self.height_spin.value())
            method = be.generate
        else:
            params.update(image=init, strength=self.strength.value() / 100)
            method = be.img2img

        if isinstance(be, LocalSDBackend) and not be.loaded and mode == MODE_TXT:
            self._pending = (be, method, params)
            self._start_job(be, be.ensure_loaded, {}, self._after_model_loaded,
                            "Loading model (first run downloads weights)…")
            return
        self._dispatch(method, be, params)

    def _dispatch(self, method, be, params):
        n = self.batch_spin.value()
        self._start_job(be, run_batch,
                        dict(method=method, backend=be, base_params=params,
                             count=n),
                        self._on_batch_done, f"Generating with {be.name}…")

    def _after_model_loaded(self, _ok):
        self._pending_generate = True

    def _start_job(self, backend, fn, kwargs, on_done, label):
        try:
            self.ctx.jobs.submit(
                label, fn, kwargs=kwargs, backend=backend,
                on_done=on_done,
                on_fail=self._show_error,
                on_progress=self.progress.setValue,
                on_finish=self._on_job_finished)
        except UserError as exc:
            self.statusMessage.emit(str(exc))
            return
        self.busy_label.setText(label)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.show()
        self._set_busy(True)

    def _show_error(self, title, msg, details):
        ErrorDialog(title, msg, details, self).exec()

    def _cancel_job(self):
        self.ctx.jobs.cancel_current()
        self.busy_label.setText("Cancelling…")

    def _set_busy(self, busy):
        self.generate_btn.setEnabled(not busy)
        self.cancel_btn.setVisible(busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)

    def _on_job_finished(self, cancelled=False):
        self.ctx.idle.notify_activity()
        self._set_busy(False)
        self.progress.hide()
        self.busy_label.setText("")
        if cancelled:
            self.statusMessage.emit("Generation cancelled")
        if not cancelled and self._pending_generate:
            self._pending_generate = False
            be, method, params = self._pending or (None, None, None)
            self._pending = None
            if method is not None:
                self._dispatch(method, be, params)
                return
            self._on_generate()

    def _on_batch_done(self, images):
        if not images:
            return
        self._show(images[-1])
        for img in images:
            self._history.insert(0, (img, self._last_prompt))
            item = QListWidgetItem(QIcon(pil_thumbnail_pixmap(img)), "")
            item.setToolTip(self._last_prompt[:120])
            self.history_list.insertItem(0, item)
        del self._history[HISTORY_LIMIT:]
        while self.history_list.count() > HISTORY_LIMIT:
            self.history_list.takeItem(self.history_list.count() - 1)
        for i in range(self.history_list.count()):
            self.history_list.item(i).setData(Qt.UserRole, i)
        self._update_hist_title()
        self.history_list.setCurrentRow(0)
        self.statusMessage.emit(f"Generated {len(images)} image(s)")

    def _clear_history(self):
        self._history.clear()
        self.history_list.clear()
        self._update_hist_title()

    def _update_hist_title(self):
        self.hist_title.setText(f"History ({self.history_list.count()})")

    # --------------------------------------------------------------- preview
    def _show(self, img):
        self._preview_img = img
        self._preview_pm = pil_to_pixmap(img)   # convert once per image
        self._rescale_preview()

    def _rescale_preview(self):
        if self._preview_pm is None:
            return
        w = max(60, self.preview.width() - 24)
        h = max(60, self.preview.height() - 24)
        self.preview.setPixmap(self._preview_pm.scaled(
            w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._rescale_preview)

    def _history_selected(self):
        items = self.history_list.selectedItems()
        if not items:
            return
        idx = items[0].data(Qt.UserRole)
        if isinstance(idx, int) and 0 <= idx < len(self._history):
            self._show(self._history[idx][0])

    # ------------------------------------------------------------ send / save
    def _send_to_editor(self):
        if self._preview_img is None:
            self.statusMessage.emit("Nothing to send yet.")
            return
        self.sendToEditor.emit(self._preview_img)

    def _save_current(self):
        img = self._preview_img
        if img is None:
            return
        out_dir = self.ctx.settings.value("paths/output", default_output_dir())
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            pass
        path, _ = QFileDialog.getSaveFileName(
            self, "Save image", os.path.join(out_dir, "generated.png"),
            "PNG (*.png);;JPEG (*.jpg);;WebP (*.webp)")
        if not path:
            return
        try:
            atomic_save_image(img, path)
            self.statusMessage.emit(f"Saved → {path}")
        except UserError as exc:
            self._show_error(*humanize(exc))

    def save_state(self):
        save_splitter(self._hsplit, self.ctx.settings, "gen_split")
        save_splitter(self._vsplit, self.ctx.settings, "gen_vsplit")
