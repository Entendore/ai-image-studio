"""Application entry point: env/.env, argparse, single-instance, theme."""
import argparse
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from . import diagnostics
from .diagnostics import LOG
from .envconfig import (allow_multi_instance, apply_runtime_side_effects,
                        load_dotenv, theme_default)
from .paths import app_dir
from .secrets import migrate_from_settings
from .single_instance import acquire_lock
from .theme import build_theme
from .version import APP_NAME, APP_VERSION, ORG_NAME

_UI_KEYS = ("ui/geometry", "ui/gen_split", "ui/gen_vsplit", "ui/edit_split")


def _env_locations():
    locs = []
    custom = os.environ.get("AIIS_ENV_FILE", "").strip()
    if custom:
        locs.append(Path(custom))          # explicit file first
    locs.append(Path.cwd() / ".env")       # then CWD, then data dir
    locs.append(app_dir() / ".env")
    return locs


def main() -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--version", action="version",
                        version=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument("--reset-ui", action="store_true",
                        help="forget saved window geometry and splitter sizes")
    args, _qt_args = parser.parse_known_args()

    # Environment first, so logging/paths honors AIIS_* from the start.
    n_env = load_dotenv(_env_locations())
    apply_runtime_side_effects()

    diagnostics.install()
    app = QApplication(sys.argv)
    app.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(ORG_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(build_theme(theme_default() or "dark"))

    if not allow_multi_instance() and acquire_lock() is None:
        QMessageBox.information(None, APP_NAME,
                                f"{APP_NAME} is already running.")
        return 0

    settings = QSettings(ORG_NAME, APP_NAME)
    if args.reset_ui:
        for key in _UI_KEYS:
            settings.remove(key)
        settings.sync()
        LOG.app.info("UI state reset via --reset-ui")
    migrate_from_settings(settings)

    LOG.app.info(f"{APP_NAME} v{APP_VERSION} starting (data dir: {app_dir()})")
    if n_env:
        LOG.app.info(f"loaded {n_env} variable(s) from .env file(s)")

    from .main_window import MainWindow
    win = MainWindow(settings)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
