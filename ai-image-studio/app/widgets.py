"""Small reusable custom widgets."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)


class CollapsibleSection(QWidget):
    """A titled section whose body can be folded away with one click."""

    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(parent)
        self._title = title
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self.toggle_btn = QPushButton(("▾  " if expanded else "▸  ") + title)
        self.toggle_btn.setObjectName("collapseHeader")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(expanded)
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.clicked.connect(self._on_toggle)
        lay.addWidget(self.toggle_btn)
        self.body = QWidget()
        self.body.setObjectName("sectionBody")
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(8, 8, 8, 8)
        self.body_lay.setSpacing(8)
        self.body.setVisible(expanded)
        lay.addWidget(self.body)

    def _on_toggle(self, checked: bool):
        self.body.setVisible(checked)
        self.toggle_btn.setText(("▾  " if checked else "▸  ") + self._title)

    def addWidget(self, w):
        self.body_lay.addWidget(w)

    def addLayout(self, l):
        self.body_lay.addLayout(l)


class Segmented(QWidget):
    """Exclusive checkable-button row — used as a mode switcher."""
    changed = Signal(int)

    def __init__(self, labels, checked=0, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons = []
        for i, txt in enumerate(labels):
            b = QPushButton(txt)
            b.setCheckable(True)
            b.setChecked(i == checked)
            b.clicked.connect(lambda _=False, idx=i: self.changed.emit(idx))
            self._group.addButton(b, i)
            lay.addWidget(b, 1)
            self.buttons.append(b)

    def setCurrentIndex(self, i):
        if 0 <= i < len(self.buttons):
            self.buttons[i].setChecked(True)

    def currentIndex(self):
        for i, b in enumerate(self.buttons):
            if b.isChecked():
                return i
        return 0


class PromptForm(QWidget):
    """Structured prompt builder: labeled fields + tag chips + live preview."""
    changed = Signal()

    def __init__(self, fields, chips=None, show_preview=True, parent=None):
        super().__init__(parent)
        self._edits = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        for key, label, placeholder in fields:
            e = QLineEdit()
            e.setPlaceholderText(placeholder)
            e.textChanged.connect(self._update)
            self._edits[key] = e
            form.addRow(label, e)
        lay.addLayout(form)

        if chips:
            crow = QHBoxLayout()
            crow.setSpacing(4)
            cap = QLabel("Add:")
            cap.setStyleSheet("color: palette(mid);")
            crow.addWidget(cap)
            for tag in chips:
                b = QPushButton(tag)
                b.setCursor(Qt.PointingHandCursor)
                b.setStyleSheet("padding: 2px 8px; font-size: 11px; "
                                "border-radius: 10px;")
                b.clicked.connect(lambda _=False, t=tag: self._add_tag(t))
                crow.addWidget(b)
            crow.addStretch(1)
            lay.addLayout(crow)

        self.preview = None
        if show_preview:
            pl = QLabel("Combined prompt")
            pl.setStyleSheet("color: palette(mid);")
            self.preview = QPlainTextEdit()
            self.preview.setReadOnly(True)
            self.preview.setFixedHeight(54)
            lay.addWidget(pl)
            lay.addWidget(self.preview)
        self._update()

    def _add_tag(self, tag):
        e = self._edits.get("details") or next(iter(self._edits.values()))
        cur = e.text().strip()
        parts = [p.strip() for p in cur.split(",")] if cur else []
        if tag not in parts:
            parts.append(tag)
        e.setText(", ".join(parts))

    def _update(self, *_):
        if self.preview is not None:
            self.preview.setPlainText(self.combined())
        self.changed.emit()

    def combined(self, extra=()):
        parts = [v for v in (e.text().strip() for e in self._edits.values()) if v]
        parts += [e for e in extra if e]
        return ", ".join(parts)

    def is_empty(self):
        return not self.combined()

    def clear(self):
        for e in self._edits.values():
            e.clear()
