"""Shared sidebar infrastructure: SidebarSection, Sidebar base class, and widget helpers."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

_PLACEHOLDER = "—"


# ---------------------------------------------------------------------------
# Section header
# ---------------------------------------------------------------------------

class _SectionHeader(QWidget):

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, parent=None):
        super().__init__(parent)

        self._expanded = True

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.arrow = QLabel("▾")
        self.arrow.setFixedWidth(12)

        self.label = QLabel(title)
        font = self.label.font()
        font.setBold(True)
        self.label.setFont(font)

        layout.addWidget(self.arrow)
        layout.addWidget(self.label)
        layout.addStretch()

        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):  # noqa: ARG002
        self._expanded = not self._expanded
        self.arrow.setText("▾" if self._expanded else "▸")
        self.toggled.emit(self._expanded)


# ---------------------------------------------------------------------------
# Collapsible section
# ---------------------------------------------------------------------------

class SidebarSection(QWidget):
    """A collapsible section with a bold header and a padded content body.

    All sections created with this class share the same content margins,
    ensuring visual consistency across both sidebars without per-section
    margin overrides.

    Usage::

        section = SidebarSection("Options")
        layout = QVBoxLayout()
        layout.addWidget(some_widget)
        section.set_content_layout(layout)
        parent_layout.addWidget(section)
    """

    # Standard padding applied around every section's content area.
    _CONTENT_MARGINS = (0, 2, 0, 6)

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(2)

        self._header = _SectionHeader(title)
        self._body = QWidget()
        self._body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        vbox.addWidget(self._header)
        vbox.addWidget(self._body)

        self._header.toggled.connect(self._body.setVisible)

    def set_content_layout(self, layout) -> None:
        """Attach *layout* as the section body and apply standard content margins."""
        layout.setContentsMargins(*self._CONTENT_MARGINS)
        self._body.setLayout(layout)

    @property
    def body(self) -> QWidget:
        return self._body


# ---------------------------------------------------------------------------
# Sidebar base class
# ---------------------------------------------------------------------------

class Sidebar(QScrollArea):
    """Base class for left and right sidebars.

    Sets up a scrollable content area with standard margins and a trailing
    stretch. Subclasses populate sections by calling ``_add_section()``.
    """

    def __init__(self, min_width: int = 220, parent=None):
        super().__init__(parent)

        self._content = QWidget()
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(4)
        self._layout.addStretch()  # trailing stretch — kept last

        self.setWidget(self._content)
        self.setWidgetResizable(True)
        self.setMinimumWidth(min_width)

    def _add_section(self, section: SidebarSection) -> None:
        """Insert *section* before the trailing stretch."""
        self._layout.insertWidget(self._layout.count() - 1, section)


# ---------------------------------------------------------------------------
# Shared widget helpers (used by multiple section classes)
# ---------------------------------------------------------------------------

MUTED_LABEL_STYLE = "color: #999; font-size: 11px; font-weight: 600;"


def field_label(text: str) -> QLabel:
    """Semi-bold field label in muted colour."""
    lbl = QLabel(text + ":")
    lbl.setStyleSheet(MUTED_LABEL_STYLE)
    lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
    return lbl


class SidebarField(QWidget):
    """Label stacked above an input widget — the standard sidebar field layout."""

    def __init__(self, label_text: str, widget: QWidget, parent=None):
        super().__init__(parent)
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 8)
        vbox.setSpacing(3)
        vbox.addWidget(field_label(label_text))
        vbox.addWidget(widget)


def value_label(wrap: bool = False) -> QLabel:
    """Read-only selectable value label, defaulting to the placeholder."""
    lbl = QLabel(_PLACEHOLDER)
    lbl.setWordWrap(wrap)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


def value_edit() -> QLineEdit:
    """Read-only single-line value field."""
    w = QLineEdit()
    w.setReadOnly(True)
    w.setPlaceholderText(_PLACEHOLDER)
    return w


def value_edit_multi(lines: int = 3) -> QPlainTextEdit:
    """Read-only multi-line value field with a fixed pixel height."""
    w = QPlainTextEdit()
    w.setReadOnly(True)
    w.setPlaceholderText(_PLACEHOLDER)
    w.setFixedHeight(18 * lines + 10)
    return w
