import os

from PIL import Image, ImageDraw, ImageFilter
from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter, QVBoxLayout,
    QWidget,
)

from .backends import DemoBackend, LocalSDBackend, OpenAIImageBackend
from .canvas import ImageCanvas
from .errors import ErrorDialog, UserError, humanize
from .fast_adjust import prepare_adjust_cache, render_adjust
from .image_ops import (FILTERS, LANCZOS, apply_adjustments, apply_filter,
                        auto_enhance, flip_h, flip_v, resize_to, rotate_ccw,
                        rotate_cw)
from .persistence import (atomic_save_image, open_image, restore_splitter,
                          save_splitter)
from .theme import CANVAS_TEXT, theme_colors
from .utils import default_output_dir
from .widgets import CollapsibleSection, PromptForm, Segmented

UNDO_LIMIT = 25
UNDO_BYTES = 512 * 1024 * 1024      # hard memory cap for the undo stack
PREVIEW_DRAG_PX = 1000              # quality while a slider is being dragged
PREVIEW_MAX_PX = 1600               # quality once the user settles

EDIT_FILL, EDIT_EXPAND = 0, 1

ADJUSTMENTS = (
    ("brightness", "Brightness", 0, 200, 100),
    ("contrast", "Contrast", 0, 200, 100),
    ("saturation", "Saturation", 0, 200, 100),
    ("hue", "Hue", -180, 180, 0),
    ("temperature", "Temperature", -100, 100, 0),
    ("sharpness", "Sharpness", 0, 200, 100),
)

FILL_FIELDS = (
    ("object", "Add / change", "e.g. a red vintage bicycle"),
    ("details", "Details", "e.g. leather seat, wicker basket"),
)
FILL_CHIPS = ["intricate details", "matching perspective", "soft shadows"]

EXPAND_FIELDS = (
    ("surroundings", "Surroundings", "e.g. pine forest, morning fog"),
    ("details", "Details", "e.g. distant mountains, same weather"),
)
EXPAND_CHIPS = ["seamless", "same lighting", "extended scenery",
                "natural transition"]


def _approx_bytes(img):
    if img is None:
        return 0
    return img.width * img.height * len(img.getbands())


class _ResizeDialog(QDialog):
    def __init__(self, size, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Resize image")
        w0, h0 = size
        self._ratio = w0 / max(1, h0)
        self._lock = False
        form = QFormLayout(self)
        form.setContentsMargins(14, 14, 14, 14)
        self.w = QSpinBox(); self.w.setRange(1, 20000); self.w.setValue(w0)
        self.h = QSpinBox(); self.h.setRange(1, 20000); self.h.setValue(h0)
        self.keep = QCheckBox("Keep aspect ratio"); self.keep.setChecked(True)
        self.w.valueChanged.connect(self._w_changed)
        self.h.valueChanged.connect(self._h_changed)
        form.addRow("Width", self.w)
        form.addRow("Height", self.h)
        form.addRow(self.keep)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _w_changed(self, v):
        if self.keep.isChecked() and not self._lock:
            self._lock = True
            self.h.setValue(max(1, round(v / self._ratio)))
            self._lock = False

    def _h_changed(self, v):
        if self.keep.isChecked() and not self._lock:
            self._lock = True
            self.w.setValue(max(1, round(v * self._ratio)))
            self._lock = False

    def value(self):
        return self.w.value(), self.h.value()


class EditPage(QWidget):
    statusMessage = Signal(str)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._undo = []
        self._redo = []
        self._adjust_base = None
        self._adjust_cache = None
        self._cache_px = None
        self._adjust_timer = QTimer(self)
        self._adjust_timer.setSingleShot(True)
        self._adjust_timer.setInterval(120)
        self._adjust_timer.timeout.connect(
            lambda: self._render_adjust_preview(PREVIEW_DRAG_PX))
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(500)
        self._settle_timer.timeout.connect(
            lambda: self._render_adjust_preview(PREVIEW_MAX_PX))
        self._build_ui()
        self._add_shortcuts()
        self.ctx.backendsChanged.connect(self.refresh_backends)
        self.ctx.activeBackendChanged.connect(self._sync_edit_combo)
        self.ctx.models.activeChanged.connect(self._update_edit_note)
        self.ctx.themeChanged.connect(self._apply_canvas_theme)
        self._apply_canvas_theme(self.ctx.theme_mode)
        self.refresh_backends()

    # ------------------------------------------------------------------- UI
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        splitter = QSplitter(Qt.Horizontal)
        self._split = splitter
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter)

        # ---------------------------------------------- left: vertical tool rail
        rail = QFrame()
        rail.setObjectName("toolRail")
        rail.setFixedWidth(62)
        rv = QVBoxLayout(rail)
        rv.setContentsMargins(6, 8, 6, 8)
        rv.setSpacing(6)
        self.tool_buttons = {}
        for name, tip, glyph in ((ImageCanvas.TOOL_BRUSH, "Brush (B)", "🖌"),
                                 (ImageCanvas.TOOL_ERASER, "Eraser (E)", "⌫"),
                                 (ImageCanvas.TOOL_PAN, "Pan (P)", "✋"),
                                 (ImageCanvas.TOOL_CROP, "Crop (C)", "⛶")):
            b = QPushButton(glyph)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, t=name: self._set_tool(t))
            self.tool_buttons[name] = b
            rv.addWidget(b)
        self.tool_buttons[ImageCanvas.TOOL_BRUSH].setChecked(True)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: palette(mid);")
        rv.addWidget(line)
        size_lbl = QLabel("Size")
        size_lbl.setAlignment(Qt.AlignCenter)
        rv.addWidget(size_lbl)
        self.brush_slider = QSlider(Qt.Vertical)
        self.brush_slider.setRange(2, 300)
        self.brush_slider.setValue(48)
        self.brush_slider.setMinimumHeight(110)
        self.brush_slider.valueChanged.connect(self._on_brush_changed)
        rv.addWidget(self.brush_slider, 1)
        self.brush_val = QLabel("48")
        self.brush_val.setAlignment(Qt.AlignCenter)
        rv.addWidget(self.brush_val)
        splitter.addWidget(rail)

        # ------------------------------------------- center: top bar + canvas
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(6)

        def tbtn(text, slot, tip=None):
            b = QPushButton(text)
            b.clicked.connect(slot)
            if tip:
                b.setToolTip(tip)
            return b

        self.open_btn = tbtn("Open…", self._open, "Ctrl+O")
        self.save_btn = tbtn("Save as…", self._save, "Ctrl+S")
        self.undo_btn = tbtn("↶", self._do_undo, "Undo (Ctrl+Z)")
        self.redo_btn = tbtn("↷", self._do_redo, "Redo (Ctrl+Y)")
        top.addWidget(self.open_btn)
        top.addWidget(self.save_btn)
        top.addSpacing(6)
        top.addWidget(self.undo_btn)
        top.addWidget(self.redo_btn)
        top.addSpacing(10)
        zout = tbtn("−", lambda: self.canvas.zoomBy(0.8))
        zout.setFixedWidth(32)
        zin = tbtn("+", lambda: self.canvas.zoomBy(1.25))
        zin.setFixedWidth(32)
        self.zoom_lbl = QLabel("100%")
        self.zoom_lbl.setFixedWidth(46)
        self.zoom_lbl.setAlignment(Qt.AlignCenter)
        top.addWidget(zout)
        top.addWidget(self.zoom_lbl)
        top.addWidget(zin)
        top.addWidget(tbtn("Fit", self.canvas.fit, "Fit to view (F)"))
        top.addStretch(1)
        self.crop_apply = tbtn("✓ Apply crop", self._apply_crop)
        self.crop_apply.setEnabled(False)
        self.crop_cancel = tbtn("✕", self.canvas.cancelCrop)
        top.addWidget(self.crop_apply)
        top.addWidget(self.crop_cancel)
        cv.addLayout(top)

        self.canvas = ImageCanvas()
        self.canvas.strokeStarted.connect(self._push_undo)
        self.canvas.fileDropped.connect(self._open_path)
        self.canvas.zoomChanged.connect(
            lambda z: self.zoom_lbl.setText(f"{z * 100:.0f}%"))
        self.canvas.cropChanged.connect(self._on_crop_changed)
        self.canvas.maskChanged.connect(self._update_info)
        cv.addWidget(self.canvas, 1)
        splitter.addWidget(center)

        # ------------------------------- right: collapsible tool panels
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(330)
        inner = QWidget()
        pv = QVBoxLayout(inner)
        pv.setContentsMargins(4, 0, 4, 0)
        pv.setSpacing(10)
        scroll.setWidget(inner)
        splitter.addWidget(scroll)

        sec_model = CollapsibleSection("AI model")
        self.edit_backend_combo = QComboBox()
        self.edit_backend_combo.currentIndexChanged.connect(
            self._on_edit_backend_changed)
        sec_model.addWidget(self.edit_backend_combo)
        self.edit_backend_note = QLabel()
        self.edit_backend_note.setWordWrap(True)
        self.edit_backend_note.setStyleSheet("color: palette(mid);")
        sec_model.addWidget(self.edit_backend_note)
        pv.addWidget(sec_model)

        sec_edit = CollapsibleSection("AI edit", expanded=True)
        self.edit_mode_seg = Segmented(
            ["🪄 Fill (mask)", "⬚ Expand (outpaint)"])
        self.edit_mode_seg.changed.connect(self._on_edit_mode)
        sec_edit.addWidget(self.edit_mode_seg)

        self.edit_stack = QStackedWidget()

        fill_page = QWidget()
        fp = QVBoxLayout(fill_page)
        fp.setContentsMargins(0, 0, 0, 0)
        fp.setSpacing(6)
        fill_hint = QLabel("Paint a mask with the Brush, then describe what "
                           "should appear inside it.")
        fill_hint.setWordWrap(True)
        fill_hint.setStyleSheet("color: palette(mid);")
        fp.addWidget(fill_hint)
        self.pform_fill = PromptForm(FILL_FIELDS, chips=FILL_CHIPS,
                                     show_preview=True)
        fp.addWidget(self.pform_fill)
        mrow = QHBoxLayout()
        self.clear_mask_btn = QPushButton("Clear mask")
        self.invert_mask_btn = QPushButton("Invert")
        self.clear_mask_btn.clicked.connect(self.canvas.clearMask)
        self.invert_mask_btn.clicked.connect(self.canvas.invertMask)
        mrow.addWidget(self.clear_mask_btn)
        mrow.addWidget(self.invert_mask_btn)
        self.show_mask_chk = QCheckBox("Show mask")
        self.show_mask_chk.setChecked(True)
        self.show_mask_chk.toggled.connect(self._toggle_mask_vis)
        mrow.addWidget(self.show_mask_chk)
        fp.addLayout(mrow)
        srow = QHBoxLayout()
        srow.addWidget(QLabel("Steps"))
        self.inpaint_steps = QSpinBox()
        self.inpaint_steps.setRange(4, 60)
        self.inpaint_steps.setValue(30)
        srow.addWidget(self.inpaint_steps)
        srow.addStretch(1)
        fp.addLayout(srow)
        self.edit_stack.addWidget(fill_page)

        expand_page = QWidget()
        ep = QVBoxLayout(expand_page)
        ep.setContentsMargins(0, 0, 0, 0)
        ep.setSpacing(6)
        expand_hint = QLabel("Grows the canvas on the selected sides and "
                             "generates the new area from your description.")
        expand_hint.setWordWrap(True)
        expand_hint.setStyleSheet("color: palette(mid);")
        ep.addWidget(expand_hint)
        sides = QHBoxLayout()
        sides.addWidget(QLabel("Sides"))
        self.side_checks = {}
        for key, lab in (("l", "L"), ("r", "R"), ("t", "T"), ("b", "B")):
            c = QCheckBox(lab)
            c.setChecked(True)
            self.side_checks[key] = c
            sides.addWidget(c)
        sides.addStretch(1)
        ep.addLayout(sides)
        am = QHBoxLayout()
        am.addWidget(QLabel("Amount"))
        self.expand_amount = QSlider(Qt.Horizontal)
        self.expand_amount.setRange(10, 100)
        self.expand_amount.setValue(40)
        self.expand_val = QLabel("40%")
        self.expand_amount.valueChanged.connect(
            lambda v: self.expand_val.setText(f"{v}%"))
        am.addWidget(self.expand_amount, 1)
        am.addWidget(self.expand_val)
        ep.addLayout(am)
        self.pform_expand = PromptForm(EXPAND_FIELDS, chips=EXPAND_CHIPS,
                                       show_preview=True)
        ep.addWidget(self.pform_expand)
        self.edit_stack.addWidget(expand_page)

        sec_edit.addWidget(self.edit_stack)
        self.neg_edit = QPlainTextEdit()
        self.neg_edit.setPlaceholderText("Negative prompt (optional)…")
        self.neg_edit.setFixedHeight(40)
        sec_edit.addWidget(self.neg_edit)
        self.ai_run_btn = QPushButton("🪄  Run fill")
        self.ai_run_btn.setObjectName("accent")
        self.ai_run_btn.clicked.connect(self._run_ai_edit)
        sec_edit.addWidget(self.ai_run_btn)
        pv.addWidget(sec_edit)

        sec_ai = CollapsibleSection("AI tools")
        agrid = QGridLayout()
        self.up_btn = QPushButton("⬆  Upscale")
        self.up_factor = QComboBox()
        self.up_factor.addItems(["×2", "×3", "×4"])
        self.bg_btn = QPushButton("✂  Remove background")
        self.en_btn = QPushButton("✨  Auto enhance")
        self.up_btn.clicked.connect(self._run_upscale)
        self.bg_btn.clicked.connect(self._run_remove_bg)
        self.en_btn.clicked.connect(self._run_enhance)
        agrid.addWidget(self.up_btn, 0, 0)
        agrid.addWidget(self.up_factor, 0, 1)
        agrid.addWidget(self.bg_btn, 1, 0, 1, 2)
        agrid.addWidget(self.en_btn, 2, 0, 1, 2)
        sec_ai.addLayout(agrid)
        pv.addWidget(sec_ai)

        sec_f = CollapsibleSection("Filters")
        frow = QHBoxLayout()
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(FILTERS)
        self.filter_btn = QPushButton("Apply")
        self.filter_btn.clicked.connect(self._apply_filter)
        frow.addWidget(self.filter_combo, 1)
        frow.addWidget(self.filter_btn)
        sec_f.addLayout(frow)
        pv.addWidget(sec_f)

        sec_adj = CollapsibleSection("Adjustments")
        aj = QGridLayout(sec_adj.body)
        self.adj_sliders = {}
        self.adj_labels = {}
        for row, (name, label, mn, mx, defv) in enumerate(ADJUSTMENTS):
            aj.addWidget(QLabel(label), row, 0)
            s = QSlider(Qt.Horizontal)
            s.setRange(mn, mx)
            s.setValue(defv)
            fmt = self._adj_fmt(name)
            val = QLabel(fmt(defv))
            s.valueChanged.connect(
                lambda v, n=name, l=val, f=fmt: (l.setText(f(v)),
                                                 self._on_adjust(n)))
            self.adj_sliders[name] = s
            self.adj_labels[name] = val
            aj.addWidget(s, row, 1)
            aj.addWidget(val, row, 2)
        hbox = QHBoxLayout()
        self.adj_apply_btn = QPushButton("Apply")
        self.adj_reset_btn = QPushButton("Reset")
        self.adj_apply_btn.clicked.connect(self._apply_adjust)
        self.adj_reset_btn.clicked.connect(self._reset_adjust)
        hbox.addWidget(self.adj_apply_btn)
        hbox.addWidget(self.adj_reset_btn)
        aj.addLayout(hbox, len(ADJUSTMENTS), 0, 1, 3)
        pv.addWidget(sec_adj)

        sec_tr = CollapsibleSection("Transform")
        tv = QGridLayout()
        self.rot_ccw_btn = QPushButton("⟲ 90°")
        self.rot_cw_btn = QPushButton("⟳ 90°")
        self.flip_h_btn = QPushButton("Flip H")
        self.flip_v_btn = QPushButton("Flip V")
        self.resize_btn = QPushButton("Resize…")
        self.rot_ccw_btn.clicked.connect(
            lambda: self._transform(rotate_ccw, "Rotated"))
        self.rot_cw_btn.clicked.connect(
            lambda: self._transform(rotate_cw, "Rotated"))
        self.flip_h_btn.clicked.connect(
            lambda: self._transform(flip_h, "Flipped"))
        self.flip_v_btn.clicked.connect(
            lambda: self._transform(flip_v, "Flipped"))
        self.resize_btn.clicked.connect(self._resize)
        tv.addWidget(self.rot_ccw_btn, 0, 0)
        tv.addWidget(self.rot_cw_btn, 0, 1)
        tv.addWidget(self.flip_h_btn, 1, 0)
        tv.addWidget(self.flip_v_btn, 1, 1)
        tv.addWidget(self.resize_btn, 2, 0, 1, 2)
        sec_tr.addLayout(tv)
        pv.addWidget(sec_tr)

        self.info = QLabel("No image")
        self.info.setStyleSheet("color: palette(mid);")
        pv.addWidget(self.info)
        self.progress = QProgressBar()
        self.progress.hide()
        pv.addWidget(self.progress)
        self.cancel_btn = QPushButton("✖ Cancel")
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self._cancel_job)
        pv.addWidget(self.cancel_btn)
        pv.addStretch(1)

        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([62, 900, 350])
        restore_splitter(self._split, self.ctx.settings, "edit_split")

        self._busy_buttons = [self.ai_run_btn, self.up_btn, self.bg_btn,
                              self.en_btn, self.filter_btn, self.rot_ccw_btn,
                              self.rot_cw_btn, self.flip_h_btn, self.flip_v_btn,
                              self.resize_btn, self.open_btn, self.save_btn,
                              self.undo_btn, self.redo_btn, self.crop_apply,
                              self.clear_mask_btn, self.invert_mask_btn]

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _adj_fmt(name):
        if name == "hue":
            return lambda v: f"{v}°"
        if name == "temperature":
            return lambda v: f"{v:+d}"
        return lambda v: f"{v}%"

    def _apply_canvas_theme(self, mode):
        colors = theme_colors(mode)
        self.canvas.setThemeColors(colors["canvas"], CANVAS_TEXT.get(mode))

    def _on_edit_mode(self, idx):
        self.edit_stack.setCurrentIndex(idx)
        self.ai_run_btn.setText("🪄  Run fill" if idx == EDIT_FILL
                                else "🖼  Run expand")

    def _set_tool(self, tool):
        self.canvas.setTool(tool)
        if self.canvas.tool() == tool:
            for t, b in self.tool_buttons.items():
                b.setChecked(t == tool)

    def _on_brush_changed(self, v):
        self.brush_val.setText(str(v))
        self.canvas.setBrushSize(v)

    def _toggle_mask_vis(self, on):
        self.canvas.show_mask = on
        self.canvas.update()

    def _shortcut_return(self):
        if self.canvas.hasCrop():
            self._apply_crop()

    def _shortcut_escape(self):
        if self.canvas.hasCrop():
            self.canvas.cancelCrop()

    def _on_crop_changed(self, has):
        self.crop_apply.setEnabled(has)

    def _update_info(self):
        img = self.canvas.image()
        if img is None:
            self.info.setText("No image")
        else:
            m = " · mask painted" if self.canvas.hasMaskPaint() else ""
            self.info.setText(f"{img.width} × {img.height} · {img.mode}{m}")

    # -------------------------------------------------------------- loading
    def loadImage(self, img, status="Image loaded"):
        self.canvas.setImage(img, reset_mask=True)
        self._undo.clear()
        self._redo.clear()
        self._adjust_base = None
        self._adjust_cache = None
        self.canvas.paint_enabled = True
        self._reset_adjust_sliders()
        self._sync_undo_buttons()
        self._update_info()
        self.statusMessage.emit(status)

    def _open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open image", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            self._open_path(path)

    def _open_path(self, path):
        try:
            img = open_image(path)
        except UserError as exc:
            ErrorDialog("Open failed", str(exc), "", self).exec()
            return
        self.loadImage(img, f"Opened {os.path.basename(path)}")

    def _save(self):
        self._reset_adjust()
        img = self.canvas.image()
        if img is None:
            return
        out_dir = self.ctx.settings.value("paths/output", default_output_dir())
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            pass
        path, _ = QFileDialog.getSaveFileName(
            self, "Save image", os.path.join(out_dir, "edit.png"),
            "PNG (*.png);;JPEG (*.jpg);;WebP (*.webp)")
        if not path:
            return
        try:
            atomic_save_image(img, path)
            self.statusMessage.emit(f"Saved → {path}")
        except UserError as exc:
            self._show_error(*humanize(exc))

    # ----------------------------------------------------------------- undo
    def _push_undo(self):
        if self.canvas.image() is None:
            return
        img, mask = self.canvas.current()
        self._push_undo_state(img, mask)

    def _push_undo_state(self, img, mask):
        self._undo.append((img.copy(), mask.copy() if mask is not None else None))
        total = sum(_approx_bytes(i) + _approx_bytes(m) for i, m in self._undo)
        while len(self._undo) > 1 and (len(self._undo) > UNDO_LIMIT
                                       or total > UNDO_BYTES):
            old_img, old_mask = self._undo.pop(0)
            total -= _approx_bytes(old_img) + _approx_bytes(old_mask)
        self._redo.clear()
        self._sync_undo_buttons()

    def _restore(self, img, mask):
        self.canvas.setImage(img, reset_mask=False)
        if mask is not None:
            self.canvas.setMask(mask)
        else:
            self.canvas.clearMask()
        self._update_info()

    def _do_undo(self):
        self._reset_adjust()
        if not self._undo:
            return
        img, mask = self._undo.pop()
        if self.canvas.image() is not None:
            self._redo.append(self.canvas.current())
        self._restore(img, mask)
        self._sync_undo_buttons()

    def _do_redo(self):
        self._reset_adjust()
        if not self._redo:
            return
        img, mask = self._redo.pop()
        if self.canvas.image() is not None:
            self._undo.append(self.canvas.current())
        self._restore(img, mask)
        self._sync_undo_buttons()

    def _sync_undo_buttons(self):
        self.undo_btn.setEnabled(bool(self._undo))
        self.redo_btn.setEnabled(bool(self._redo))

    # -------------------------------------------------------------- backends
    def refresh_backends(self):
        combo = self.edit_backend_combo
        combo.blockSignals(True)
        combo.clear()
        for key, be in self.ctx.backends.items():
            combo.addItem(be.name, key)
        idx = combo.findData(self.ctx.active_key)
        combo.setCurrentIndex(max(0, idx))
        combo.blockSignals(False)
        self._update_edit_note()

    def _sync_edit_combo(self, key):
        idx = self.edit_backend_combo.findData(key)
        if idx >= 0 and idx != self.edit_backend_combo.currentIndex():
            self.edit_backend_combo.blockSignals(True)
            self.edit_backend_combo.setCurrentIndex(idx)
            self.edit_backend_combo.blockSignals(False)
            self._update_edit_note()

    def _on_edit_backend_changed(self, *_):
        key = self.edit_backend_combo.currentData()
        if key:
            self.ctx.active_key = key
            self._update_edit_note()

    def _update_edit_note(self):
        be = self.ctx.current_backend()
        if isinstance(be, OpenAIImageBackend):
            note = (f"API model '{be.model}': Fill/Expand are not supported "
                    "here; use Image → Image on the Generate tab for AI edits "
                    "(gpt-image-1 / dall-e-2). Upscale/remove-bg/enhance run "
                    "locally.")
        elif isinstance(be, LocalSDBackend):
            note = (f"Fill & Expand model: {be.inpaint_model_id()} — change it "
                    "in the 🧠 Models tab.")
        elif isinstance(be, DemoBackend):
            note = ("Demo: fill blurs the masked area, expand pads + blurs. "
                    "All other tools are fully functional.")
        else:
            note = ""
        self.edit_backend_note.setText(note)

    # ------------------------------------------------------------- AI actions
    def _start_job(self, backend, fn, kwargs, on_done, label):
        try:
            self.ctx.jobs.submit(
                label, fn, kwargs=kwargs, backend=backend,
                priority=(QThread.Priority.LowPriority
                          if backend is None else None),
                on_done=on_done,
                on_fail=self._show_error,
                on_progress=self.progress.setValue,
                on_finish=self._on_job_finished)
        except UserError as exc:
            self.statusMessage.emit(str(exc))
            return
        self.progress.setRange(0, 0 if backend is None else 100)
        self.progress.setValue(0)
        self.progress.show()
        self.cancel_btn.show()
        self.info.setText(label)
        self._set_busy(True)

    def _show_error(self, title, msg, details):
        ErrorDialog(title, msg, details, self).exec()

    def _cancel_job(self):
        self.ctx.jobs.cancel_current()
        self.info.setText("Cancelling…")

    def _set_busy(self, busy):
        for b in self._busy_buttons:
            b.setEnabled(not busy)

    def _on_job_finished(self, cancelled=False):
        self.ctx.idle.notify_activity()
        self.progress.hide()
        self.cancel_btn.hide()
        self._set_busy(False)
        self.canvas.paint_enabled = True
        self._sync_undo_buttons()
        self._update_info()
        if cancelled:
            self.statusMessage.emit("Operation cancelled")

    def _after_edit(self):
        self._adjust_base = None
        self._adjust_cache = None
        self._reset_adjust_sliders()
        self._update_info()

    def _on_edit_result(self, img):
        mask = self.canvas.mask()
        self.canvas.setImage(img, reset_mask=False)
        if mask is not None and mask.size == img.size:
            self.canvas.setMask(mask)
        else:
            self.canvas.clearMask()
        self._after_edit()
        self.statusMessage.emit("Done")

    def _on_inpaint_result(self, img):
        self.canvas.setImage(img, reset_mask=True)
        self._after_edit()
        self.statusMessage.emit("AI edit done")

    # ------------------------------------------------------- fill / expand
    def _run_ai_edit(self):
        if self.edit_mode_seg.currentIndex() == EDIT_FILL:
            self._run_fill()
        else:
            self._run_expand()

    def _run_fill(self):
        self._reset_adjust()
        img = self.canvas.image()
        if img is None:
            return
        if not self.canvas.hasMaskPaint():
            QMessageBox.information(self, "No mask",
                                    "Paint over the area you want to regenerate "
                                    "first (Brush tool, or press B).")
            return
        prompt = self.pform_fill.combined()
        if not prompt:
            QMessageBox.information(self, "Prompt needed",
                                    "Describe what should appear in the "
                                    "masked area.")
            return
        self._push_undo()
        be = self.ctx.current_backend()
        self._start_job(be, be.inpaint,
                        dict(image=img.convert("RGB"),
                             mask=self.canvas.mask().copy(),
                             prompt=prompt,
                             negative=self.neg_edit.toPlainText().strip(),
                             steps=self.inpaint_steps.value(),
                             guidance=7.0, seed=-1),
                        self._on_inpaint_result, "Filling masked area…")

    def _compose_expand(self, img, pct):
        w, h = img.size
        sides = {k: c.isChecked() for k, c in self.side_checks.items()}
        l = int(w * pct / 100) if sides["l"] else 0
        r = int(w * pct / 100) if sides["r"] else 0
        t = int(h * pct / 100) if sides["t"] else 0
        b = int(h * pct / 100) if sides["b"] else 0
        if (l, r, t, b) == (0, 0, 0, 0):
            return None
        W, H = w + l + r, h + t + b
        bg = img.convert("RGB").resize((W, H), LANCZOS).filter(
            ImageFilter.GaussianBlur(24))
        canvas = bg.copy()
        canvas.paste(img.convert("RGB"), (l, t))
        mask = Image.new("L", (W, H), 0)
        d = ImageDraw.Draw(mask)
        if l: d.rectangle([0, 0, l - 1, H - 1], fill=255)
        if r: d.rectangle([W - r, 0, W - 1, H - 1], fill=255)
        if t: d.rectangle([0, 0, W - 1, t - 1], fill=255)
        if b: d.rectangle([0, H - b, W - 1, H - 1], fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(6))
        return canvas, mask

    def _run_expand(self):
        self._reset_adjust()
        img = self.canvas.image()
        if img is None:
            return
        prompt = self.pform_expand.combined(
            extra=("seamless continuation of the scene",))
        if not prompt:
            QMessageBox.information(self, "Prompt needed",
                                    "Describe what should surround the image.")
            return
        composed = self._compose_expand(img, self.expand_amount.value())
        if composed is None:
            QMessageBox.information(self, "No sides selected",
                                    "Tick at least one side to expand.")
            return
        padded, mask = composed
        self._push_undo()
        be = self.ctx.current_backend()
        self._start_job(be, be.inpaint,
                        dict(image=padded, mask=mask, prompt=prompt,
                             negative=self.neg_edit.toPlainText().strip(),
                             steps=self.inpaint_steps.value(),
                             guidance=7.0, seed=-1),
                        self._on_inpaint_result, "Expanding scene…")

    # ------------------------------------------------------- other AI tools
    def _run_upscale(self):
        self._reset_adjust()
        img = self.canvas.image()
        if img is None:
            return
        factor = int(self.up_factor.currentText().lstrip("×"))
        self._push_undo()
        be = self.ctx.current_backend()
        self._start_job(be, be.upscale, dict(image=img, factor=factor),
                        self._on_edit_result, f"Upscaling ×{factor}…")

    def _run_remove_bg(self):
        self._reset_adjust()
        img = self.canvas.image()
        if img is None:
            return
        self._push_undo()
        be = self.ctx.current_backend()
        self._start_job(be, be.remove_background, dict(image=img),
                        self._on_edit_result, "Removing background…")

    def _run_enhance(self):
        self._reset_adjust()
        img = self.canvas.image()
        if img is None:
            return
        self._push_undo()
        self._start_job(None, auto_enhance, dict(image=img),
                        self._on_edit_result, "Enhancing…")

    # ---------------------------------------------------------------- filters
    def _apply_filter(self):
        self._reset_adjust()
        name = self.filter_combo.currentText()
        if name == FILTERS[0]:
            return
        img = self.canvas.image()
        if img is None:
            return
        self._push_undo()
        self._start_job(None, apply_filter, dict(image=img, name=name),
                        self._on_edit_result, f"Applying {name}…")

    # ------------------------------------------------------------- transforms
    def _transform(self, fn, msg):
        self._reset_adjust()
        img = self.canvas.image()
        if img is None:
            return
        self._push_undo()
        mask = self.canvas.mask()
        nimg = fn(img)
        nmask = fn(mask) if mask is not None else None
        self.canvas.setImage(nimg, reset_mask=True)
        if nmask is not None:
            self.canvas.setMask(nmask)
        self._update_info()
        self.statusMessage.emit(msg)

    def _resize(self):
        self._reset_adjust()
        img = self.canvas.image()
        if img is None:
            return
        dlg = _ResizeDialog(img.size, self)
        if not dlg.exec():
            return
        w, h = dlg.value()
        if (w, h) == img.size:
            return
        self._push_undo()
        mask = self.canvas.mask()
        nimg = resize_to(img, w, h)
        nmask = resize_to(mask, w, h) if mask is not None else None
        self.canvas.setImage(nimg, reset_mask=True)
        if nmask is not None:
            self.canvas.setMask(nmask)
        self._update_info()
        self.statusMessage.emit(f"Resized to {w} × {h}")

    # ----------------------------------------------------------------- crop
    def _apply_crop(self):
        if not self.canvas.hasCrop():
            return
        self._reset_adjust()
        res = self.canvas.applyCrop()
        if not res:
            return
        new_img, new_mask = res
        self._push_undo()
        self.canvas.setImage(new_img, reset_mask=True)
        if new_mask is not None:
            self.canvas.setMask(new_mask)
        self._update_info()
        self.statusMessage.emit("Cropped")

    # ------------------------------------------------------------ adjustments
    def _adjust_values(self):
        return {name: self.adj_sliders[name].value()
                for name, _, _, _, _ in ADJUSTMENTS}

    def _on_adjust(self, _name):
        if self.canvas.image() is None:
            return
        if self._adjust_base is None:
            self._adjust_base = self.canvas.image().copy()
            self.canvas.paint_enabled = False
            self._adjust_cache = None
        self._adjust_timer.start()      # quick low-res feedback
        self._settle_timer.start()      # hi-res render after the drag settles

    def _scaled_base(self, max_px):
        base = self._adjust_base
        if max(base.size) > max_px:
            s = max_px / max(base.size)
            return base.resize((max(1, int(base.width * s)),
                                max(1, int(base.height * s))), LANCZOS)
        return base

    def _render_adjust_preview(self, max_px=PREVIEW_MAX_PX):
        if self._adjust_base is None:
            return
        if self._adjust_cache is None or self._cache_px != max_px:
            self._adjust_cache = prepare_adjust_cache(self._scaled_base(max_px))
            self._cache_px = max_px
        img = render_adjust(self._adjust_cache, **self._adjust_values())
        self.canvas.setImage(img, reset_mask=False)

    def _apply_adjust(self):
        if self._adjust_base is None:
            return
        mask = self.canvas.mask()
        full = apply_adjustments(self._adjust_base, **self._adjust_values())
        self._push_undo_state(self._adjust_base, mask)
        self._adjust_base = None
        self._adjust_cache = None
        self._reset_adjust_sliders()
        self.canvas.paint_enabled = True
        self.canvas.setImage(full, reset_mask=False)
        if mask is not None:
            self.canvas.setMask(mask)
        self._update_info()
        self.statusMessage.emit("Adjustments applied")

    def _reset_adjust(self):
        self._reset_adjust_sliders()
        if self._adjust_base is not None:
            base, self._adjust_base = self._adjust_base, None
            self._adjust_cache = None
            mask = self.canvas.mask()
            self.canvas.paint_enabled = True
            self.canvas.setImage(base, reset_mask=False)
            if mask is not None:
                self.canvas.setMask(mask)

    def _reset_adjust_sliders(self):
        for name, _, _, _, defv in ADJUSTMENTS:
            s = self.adj_sliders[name]
            s.blockSignals(True)
            s.setValue(defv)
            s.blockSignals(False)
            self.adj_labels[name].setText(self._adj_fmt(name)(defv))

    def save_state(self):
        save_splitter(self._split, self.ctx.settings, "edit_split")
