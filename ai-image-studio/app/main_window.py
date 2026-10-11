from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QMessageBox, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from .context import AppContext
from .crash_report import startup_crash_check
from .diagnostics import LOG, fmt_bytes
from .diagnostics_dialog import DiagnosticsDialog
from .edit_page import EditPage
from .envconfig import log_level_pref, updates_disabled
from .generate_page import GeneratePage
from .models_page import ModelsPage
from .persistence import restore_window, save_window
from .settings_dialog import SettingsDialog
from .theme import build_theme
from .update_check import UpdateChecker
from .version import APP_NAME, APP_VERSION, ORG_NAME

PAGE_GENERATE, PAGE_MODELS, PAGE_EDIT = 0, 1, 2

SHORTCUTS_TEXT = """
<b>Global</b><br>
Ctrl+T — toggle dark / light theme<br>
Ctrl+D — diagnostics (memory, health, log)<br><br>
<b>Editor</b><br>
B / E / P / C — brush · eraser · pan · crop tool<br>
F — fit image to view<br>
Ctrl+O / Ctrl+S — open / save image<br>
Ctrl+Z / Ctrl+Y — undo / redo<br>
Enter — apply crop · Esc — cancel crop<br>
Mouse wheel — zoom · right/middle drag — pan
"""


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings = None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.ctx = AppContext(settings or QSettings(ORG_NAME, APP_NAME))
        LOG.set_level(log_level_pref()
                      or self.ctx.settings.value("logging/level", "info")
                      or "info")

        self._errors = 0
        QApplication.instance().aboutToQuit.connect(
            lambda: LOG.shutdown("window closed"))

        self._build_menu()

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        # ------------------------------------------------------------- sidebar
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(196)
        sv = QVBoxLayout(sidebar)
        sv.setContentsMargins(12, 16, 12, 14)
        sv.setSpacing(6)
        title = QLabel("🎨 AI Image\nStudio")
        title.setObjectName("appTitle")
        sv.addWidget(title)
        subtitle = QLabel("Generate · Models · Edit")
        subtitle.setStyleSheet("color: palette(mid); font-size: 11px;")
        sv.addWidget(subtitle)
        sv.addSpacing(16)

        self.nav_generate = QPushButton("✨  Generate")
        self.nav_generate.setObjectName("nav")
        self.nav_generate.setCheckable(True)
        self.nav_generate.setChecked(True)
        self.nav_models = QPushButton("🧠  Models")
        self.nav_models.setObjectName("nav")
        self.nav_models.setCheckable(True)
        self.nav_edit = QPushButton("🖌  Edit")
        self.nav_edit.setObjectName("nav")
        self.nav_edit.setCheckable(True)
        for b in (self.nav_generate, self.nav_models, self.nav_edit):
            sv.addWidget(b)
        sv.addStretch(1)

        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("nav")
        self.theme_btn.clicked.connect(self.ctx.toggle_theme)
        sv.addWidget(self.theme_btn)

        settings_btn = QPushButton("⚙  Settings")
        settings_btn.setObjectName("nav")
        settings_btn.clicked.connect(self._open_settings)
        sv.addWidget(settings_btn)

        # ---------------------------------------------------------------- pages
        self.stack = QStackedWidget()
        self.generate_page = GeneratePage(self.ctx)
        self.models_page = ModelsPage(self.ctx)
        self.edit_page = EditPage(self.ctx)
        self.stack.addWidget(self.generate_page)
        self.stack.addWidget(self.models_page)
        self.stack.addWidget(self.edit_page)

        root.addWidget(sidebar)
        root.addWidget(self.stack, 1)

        self.nav_generate.clicked.connect(lambda: self._switch(PAGE_GENERATE))
        self.nav_models.clicked.connect(lambda: self._switch(PAGE_MODELS))
        self.nav_edit.clicked.connect(lambda: self._switch(PAGE_EDIT))

        self.generate_page.sendToEditor.connect(self._send_to_editor)
        for page in (self.generate_page, self.models_page, self.edit_page):
            page.statusMessage.connect(self.statusBar().showMessage)

        # ------------------------------------------------ status bar indicators
        self.err_btn = QPushButton()
        self.err_btn.setFlat(True)
        self.err_btn.setCursor(Qt.PointingHandCursor)
        self.err_btn.hide()
        self.err_btn.clicked.connect(self._open_diagnostics)
        self.statusBar().addPermanentWidget(self.err_btn)

        self.mem_btn = QPushButton()
        self.mem_btn.setFlat(True)
        self.mem_btn.setCursor(Qt.PointingHandCursor)
        self.mem_btn.setToolTip(
            "Memory usage — click to open Diagnostics (Ctrl+D)")
        self.mem_btn.clicked.connect(self._open_diagnostics)
        self.statusBar().addPermanentWidget(self.mem_btn)

        self.ctx.monitor.updated.connect(self._on_mem_update)
        self.ctx.monitor.start(5000)
        self.ctx.idle.unloaded.connect(self.statusBar().showMessage)
        LOG.errorLogged.connect(self._on_error_logged)
        self.ctx.jobs.jobStuck.connect(self._on_job_stuck)

        self.ctx.themeChanged.connect(self._on_theme_changed)
        self._on_theme_changed(self.ctx.theme_mode)

        theme_sc = QAction(self)
        theme_sc.setShortcut(QKeySequence("Ctrl+T"))
        theme_sc.triggered.connect(self.ctx.toggle_theme)
        self.addAction(theme_sc)

        diag_sc = QAction(self)
        diag_sc.setShortcut(QKeySequence("Ctrl+D"))
        diag_sc.triggered.connect(self._open_diagnostics)
        self.addAction(diag_sc)

        # ---------------------------------------------------- deferred startup
        self._update_manual = False
        self.updater = UpdateChecker(self.ctx.settings, self)
        self.updater.checked.connect(self._on_update_checked)
        if str(self.ctx.settings.value("update/auto", "")).lower() \
                in ("true", "1") and not updates_disabled():
            QTimer.singleShot(2500, self.updater.check)   # after first paint
        QTimer.singleShot(0, lambda: startup_crash_check(
            self.ctx.settings, parent=self))

        self.statusBar().showMessage("Ready")
        self.resize(1440, 900)
        restore_window(self, self.ctx.settings)

    # ------------------------------------------------------------------- menu
    def _build_menu(self):
        m = self.menuBar()

        file_m = m.addMenu("&File")
        act_open = QAction("Open Image…", self)
        act_open.setShortcut(QKeySequence("Ctrl+O"))
        act_open.triggered.connect(self._menu_open)
        act_save = QAction("Save Image…", self)
        act_save.setShortcut(QKeySequence("Ctrl+S"))
        act_save.triggered.connect(lambda: self.edit_page._save())
        act_quit = QAction("Exit", self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.close)
        for a in (act_open, act_save):
            file_m.addAction(a)
        file_m.addSeparator()
        file_m.addAction(act_quit)

        view_m = m.addMenu("&View")
        act_theme = QAction("Toggle Dark / Light", self)
        act_theme.setShortcut(QKeySequence("Ctrl+T"))
        act_theme.triggered.connect(self.ctx.toggle_theme)
        view_m.addAction(act_theme)
        act_diag = QAction("Diagnostics…", self)
        act_diag.setShortcut(QKeySequence("Ctrl+D"))
        act_diag.triggered.connect(self._open_diagnostics)
        view_m.addAction(act_diag)
        view_m.addSeparator()
        act_gen = QAction("Go to Generate", self)
        act_gen.triggered.connect(lambda: self._switch(PAGE_GENERATE))
        act_mod = QAction("Go to Models", self)
        act_mod.triggered.connect(lambda: self._switch(PAGE_MODELS))
        act_edit = QAction("Go to Edit", self)
        act_edit.triggered.connect(lambda: self._switch(PAGE_EDIT))
        view_m.addActions([act_gen, act_mod, act_edit])

        help_m = m.addMenu("&Help")
        act_keys = QAction("Keyboard Shortcuts", self)
        act_keys.triggered.connect(lambda: QMessageBox.information(
            self, "Keyboard Shortcuts", SHORTCUTS_TEXT.strip()))
        act_upd = QAction("Check for updates…", self)
        act_upd.triggered.connect(self._check_updates_manual)
        act_about = QAction("About", self)
        act_about.triggered.connect(lambda: QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> v{APP_VERSION}<br>A desktop AI image generator "
            "&amp; photo editor.<br><br>Local Stable Diffusion via diffusers, "
            "OpenAI-compatible APIs, offline demo mode.<br>PySide6 + Pillow."))
        help_m.addActions([act_keys, act_upd, act_about])

    # ------------------------------------------------------------------ theme
    def _on_theme_changed(self, mode: str):
        QApplication.instance().setStyleSheet(build_theme(mode))
        self.theme_btn.setText("☀  Light mode" if mode == "dark"
                               else "🌙  Dark mode")

    # ------------------------------------------------------------ status bar
    def _on_mem_update(self, snap):
        ram, gpu = snap.get("ram", {}), snap.get("gpu", {})
        txt = (f"RAM {ram.get('percent'):.0f}%"
               if ram.get("percent") is not None else "RAM ?")
        if gpu.get("available"):
            txt += (f"  ·  VRAM {gpu['used'] / 1e9:.1f}"
                    f"/{gpu['total'] / 1e9:.1f} GB")
        self.mem_btn.setText(txt)
        tip = f"RAM: {fmt_bytes(ram.get('used'))} / {fmt_bytes(ram.get('total'))}"
        if gpu.get("available"):
            tip += (f"\nGPU: {gpu['name']}"
                    f"\nVRAM: {fmt_bytes(gpu['used'])} / "
                    f"{fmt_bytes(gpu['total'])}"
                    f" (free {fmt_bytes(gpu['free'])})")
        tip += "\nClick for full diagnostics"
        self.mem_btn.setToolTip(tip)

    def _on_error_logged(self, line: str):
        self._errors += 1
        self.err_btn.setText(f"⚠ {self._errors} error"
                             f"{'s' if self._errors != 1 else ''}")
        self.err_btn.setToolTip(f"Click to view the log.\nLast error:\n"
                                f"{line[:300]}")
        self.err_btn.show()

    def _on_job_stuck(self, label: str, seconds: int):
        self.statusBar().showMessage(
            f"'{label}' has shown no progress for {seconds // 60} min — "
            "you can cancel it.", 8000)

    # ------------------------------------------------------------------ misc
    def _check_updates_manual(self):
        if updates_disabled():
            self.statusBar().showMessage(
                "Updates are disabled (AIIS_NO_UPDATES).", 5000)
            return
        self._update_manual = True
        self.statusBar().showMessage("Checking for updates…")
        self.updater.check()

    def _on_update_checked(self, tag: str):
        manual, self._update_manual = self._update_manual, False
        if tag:
            LOG.app.info(f"update available: {tag}")
            QMessageBox.information(
                self, "Update available",
                f"A new version is available: {tag}\n"
                "Please download it from the project releases page.")
        elif manual:
            self.statusBar().showMessage("You are up to date.", 5000)

    def _open_diagnostics(self):
        self._errors = 0
        self.err_btn.hide()
        dlg = DiagnosticsDialog(self.ctx, self)
        dlg.exec()

    def _switch(self, page):
        self.stack.setCurrentIndex(page)
        self.nav_generate.setChecked(page == PAGE_GENERATE)
        self.nav_models.setChecked(page == PAGE_MODELS)
        self.nav_edit.setChecked(page == PAGE_EDIT)

    def _menu_open(self):
        self._switch(PAGE_EDIT)
        self.edit_page._open()

    def _send_to_editor(self, img):
        self.edit_page.loadImage(img, "Sent from Generate tab")
        self._switch(PAGE_EDIT)

    def _open_settings(self):
        dlg = SettingsDialog(self.ctx, self)
        if dlg.exec():
            self.statusBar().showMessage("Settings updated")

    def closeEvent(self, e):
        jobs = self.ctx.jobs
        if jobs.busy:
            ret = QMessageBox.question(
                self, "Operation in progress",
                "A generation/edit job is still running.\nCancel it and quit?",
                QMessageBox.Yes | QMessageBox.No)
            if ret != QMessageBox.Yes:
                e.ignore()
                return
            if not jobs.wait_for_current(8000):
                jobs.terminate_current()     # last resort at shutdown
        save_window(self, self.ctx.settings)
        self.generate_page.save_state()
        self.edit_page.save_state()
        self.ctx.monitor.stop()
        be = self.ctx.backends.get("local")
        if be is not None and getattr(be, "loaded", False):
            try:
                be.unload()                  # release VRAM promptly
            except Exception:
                pass
        LOG.app.info("Window closed")
        super().closeEvent(e)
