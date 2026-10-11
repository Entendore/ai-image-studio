"""Theming: full stylesheets for dark / light mode + canvas color tokens."""
from string import Template

THEME_SETTINGS = {
    "dark": dict(
        window="#1e1f23", panel="#232428", sidebar="#191a1d", border="#33343a",
        input="#2a2b30", hover="#3d3e45", pressed="#2f3036", text="#dadbdd",
        subtext="#9aa0a6", faint="#7c7f86", accent="#4a6cf7",
        accent_hover="#5b7cff", accent_disabled="#3a4570", accent_text="#ffffff",
        checked_bg="#3f4c85", checked_text="#ffffff", canvas="#141518",
        preview="#232428", disabled_bg="#2c2d32", disabled_text="#6f7076",
        handle="rgba(74,108,247,110)",
    ),
    "light": dict(
        window="#f4f5f7", panel="#ffffff", sidebar="#e9ebef", border="#d7dade",
        input="#ffffff", hover="#e2e5ea", pressed="#d5d9df", text="#23252b",
        subtext="#5f646d", faint="#8a8f98", accent="#4a6cf7",
        accent_hover="#3d5ce0", accent_disabled="#b9c4f5", accent_text="#ffffff",
        checked_bg="#dfe6ff", checked_text="#23366e", canvas="#e7e9ee",
        preview="#eceef2", disabled_bg="#eceef1", disabled_text="#b0b4bb",
        handle="rgba(74,108,247,140)",
    ),
}

CANVAS_TEXT = {"dark": "#7c7f86", "light": "#9aa0a6"}


def theme_colors(mode: str) -> dict:
    return THEME_SETTINGS.get(mode, THEME_SETTINGS["dark"])


_TEMPLATE = Template("""
* { outline: none; }
QWidget { background-color: $window; color: $text; font-size: 13px; }
QLabel { background: transparent; }

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: $input; border: 1px solid $border; border-radius: 6px;
    padding: 5px 8px; selection-background-color: $accent;
    selection-color: $accent_text;
}
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background-color: $input; border: 1px solid $border;
    border-radius: 6px; selection-background-color: $accent;
    selection-color: $accent_text; outline: 0; }

QPushButton { background-color: $input; border: 1px solid $border;
    border-radius: 6px; padding: 7px 14px; }
QPushButton:hover { background-color: $hover; }
QPushButton:pressed { background-color: $pressed; }
QPushButton:disabled { color: $disabled_text; background-color: $disabled_bg; }
QPushButton:checked { background-color: $checked_bg; color: $checked_text;
    border-color: $accent; font-weight: 600; }

QPushButton#accent { background-color: $accent; border: none; color: $accent_text;
    font-weight: 600; }
QPushButton#accent:hover { background-color: $accent_hover; }
QPushButton#accent:disabled { background-color: $accent_disabled; color: $window; }

QPushButton#nav { text-align: left; padding: 10px 14px; border: none;
    border-radius: 8px; background: transparent; font-size: 14px; }
QPushButton#nav:hover { background: $hover; }
QPushButton#nav:checked { background: $checked_bg; color: $checked_text;
    font-weight: 600; }

QPushButton#collapseHeader { border: none; background: transparent;
    color: $subtext; font-weight: 600; text-align: left; padding: 6px 4px;
    font-size: 13px; }
QPushButton#collapseHeader:hover { color: $text; }
QPushButton#collapseHeader:checked { color: $text; background: transparent;
    border: none; }

QWidget#sectionBody { background: $panel; border: 1px solid $border;
    border-radius: 8px; }
QWidget#sectionBody QLabel { background: transparent; }

QFrame#toolRail { background: $sidebar; border: 1px solid $border;
    border-radius: 10px; }
QFrame#toolRail QPushButton { background: transparent; border: none;
    border-radius: 8px; padding: 9px 0; font-size: 16px; }
QFrame#toolRail QPushButton:hover { background: $hover; }
QFrame#toolRail QPushButton:checked { background: $checked_bg;
    color: $checked_text; }
QFrame#toolRail QLabel { background: transparent; color: $subtext;
    font-size: 11px; }

QFrame#sidebar { background: $sidebar; border-right: 1px solid $border; }
QLabel#appTitle { font-size: 17px; font-weight: 700; color: $text; }

QLabel#previewArea { background: $preview; border: 1px solid $border;
    border-radius: 8px; color: $faint; }

QGroupBox { border: 1px solid $border; border-radius: 8px; margin-top: 12px;
    padding: 8px; background: $panel; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px;
    color: $subtext; }

QSlider::groove:horizontal { height: 5px; background: $border; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; background: $accent;
    border-radius: 7px; }
QSlider::groove:vertical { width: 5px; background: $border; border-radius: 2px; }
QSlider::handle:vertical { height: 14px; margin: 0 -5px; background: $accent;
    border-radius: 7px; }

QProgressBar { background: $input; border: 1px solid $border; border-radius: 6px;
    text-align: center; color: $text; }
QProgressBar::chunk { background: $accent; border-radius: 5px; }

QListWidget { background: $panel; border: 1px solid $border; border-radius: 6px; }
QListWidget::item { border-radius: 4px; margin: 2px; }
QListWidget::item:selected { background: $accent; color: $accent_text; }

QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid $border;
    border-radius: 4px; background: $input; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }

QMenuBar { background: $sidebar; border-bottom: 1px solid $border; }
QMenuBar::item { padding: 5px 10px; background: transparent; border-radius: 4px; }
QMenuBar::item:selected { background: $hover; }
QMenu { background: $panel; border: 1px solid $border; border-radius: 6px;
    padding: 4px; }
QMenu::item { padding: 6px 24px 6px 12px; border-radius: 4px; }
QMenu::item:selected { background: $accent; color: $accent_text; }
QMenu::separator { height: 1px; background: $border; margin: 4px 8px; }

QStatusBar { background: $sidebar; color: $subtext; }
QToolTip { background: $input; color: $text; border: 1px solid $border;
    padding: 4px; border-radius: 4px; }

QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }

QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 5px; }
QSplitter::handle:vertical { height: 5px; }
QSplitter::handle:hover { background: $handle; border-radius: 2px; }

QScrollBar:vertical, QScrollBar:horizontal { background: transparent;
    width: 10px; height: 10px; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: $border;
    border-radius: 5px; min-height: 24px; min-width: 24px; }
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background: $subtext; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QTabWidget::pane { border: 1px solid $border; border-radius: 6px; }
QTabBar::tab { background: transparent; padding: 7px 14px; color: $subtext; }
QTabBar::tab:selected { color: $text; border-bottom: 2px solid $accent; }

QTreeWidget { background: $panel; border: 1px solid $border; border-radius: 6px; }
QTreeWidget::item:selected { background: $accent; color: $accent_text; }
""")


def build_theme(mode: str = "dark") -> str:
    return _TEMPLATE.substitute(**theme_colors(mode))
