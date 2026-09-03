"""Shared sidebar infrastructure: SidebarSection and widget helpers."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.icon_utils import svg_pixmap
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.themed_label import ThemedTextLabel

_PLACEHOLDER = "—"
_QWIDGETSIZE_MAX = 16777215  # Qt's QWIDGETSIZE_MAX

# ---------------------------------------------------------------------------
# Disclosure (collapse/expand) affordance
# ---------------------------------------------------------------------------
#
# Every collapsible header in the app draws its triangle at this one size, as a
# plain QLabel pixmap on a click-through row.  Nesting depth is expressed by
# indentation and label weight, never by a different glyph size or a different
# widget class — a 16 px triangle inside an auto-raise QToolButton next to a
# 10 px triangle on a QLabel reads as two unrelated controls rather than as two
# levels of the same one.

DISCLOSURE_PX = 10
#: Left inset applied per level of nesting inside a SidebarSection body.
NEST_INDENT_PX = 12


def disclosure_pixmap(expanded: bool, tint: str | None = None) -> QPixmap:
    """Triangle for a collapsible header: down when *expanded*, right when collapsed.

    *tint* defaults to ``THEME.icon_tint`` (top-level headers); nested headers
    pass ``THEME.text_muted`` so depth is legible at a glance.
    """
    return svg_pixmap("arrow_down" if expanded else "arrow_right", DISCLOSURE_PX, tint=tint or THEME.icon_tint)


# ---------------------------------------------------------------------------
# Section header
# ---------------------------------------------------------------------------


class _SectionHeader(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, title: str, parent=None):
        super().__init__(parent)

        self._expanded = True

        self._pixmap_down = disclosure_pixmap(True)
        self._pixmap_right = disclosure_pixmap(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.arrow = QLabel()
        self.arrow.setFixedSize(DISCLOSURE_PX, DISCLOSURE_PX)
        self.arrow.setPixmap(self._pixmap_down)

        self.label = QLabel(title)
        font = self.label.font()
        font.setBold(True)
        self.label.setFont(font)

        layout.addWidget(self.arrow)
        layout.addWidget(self.label)
        layout.addStretch()

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    @property
    def expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        """Fold or unfold, exactly as a click on the header would.

        A no-op when already in that state, so a caller restoring a remembered
        position does not emit a toggle nothing acted on.
        """
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self.arrow.setPixmap(self._pixmap_down if expanded else self._pixmap_right)
        self.toggled.emit(expanded)

    def mousePressEvent(self, event):  # noqa: ARG002
        self.set_expanded(not self._expanded)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._pixmap_down = disclosure_pixmap(True)
            self._pixmap_right = disclosure_pixmap(False)
            self.arrow.setPixmap(self._pixmap_down if self._expanded else self._pixmap_right)


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
    # Vertical size policy used when the section is expanded.  Subclasses that
    # need to grow (e.g. a list that should fill remaining space) override this.
    _expanded_v_policy: QSizePolicy.Policy = QSizePolicy.Policy.Maximum

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
        self._header.toggled.connect(self._on_section_toggled)

    def _on_section_toggled(self, expanded: bool) -> None:
        if expanded:
            self.setMaximumHeight(_QWIDGETSIZE_MAX)
            self.setSizePolicy(QSizePolicy.Policy.Preferred, self._expanded_v_policy)
        else:
            # Switch to Maximum so the parent layout stops allocating extra space
            # to this widget; without this the section holds its large allocated
            # cell and the header appears centred within it.
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            h = self._header.height() or self._header.sizeHint().height() or 24
            self.setMaximumHeight(h)

    def set_content_layout(self, layout) -> None:
        """Attach *layout* as the section body and apply standard content margins."""
        layout.setContentsMargins(*self._CONTENT_MARGINS)
        self._body.setLayout(layout)

    @property
    def body(self) -> QWidget:
        return self._body

    @property
    def expanded(self) -> bool:
        """Whether the body is currently unfolded."""
        return self._header.expanded

    def set_expanded(self, expanded: bool) -> None:
        """Fold or unfold the section, exactly as a click on its header would."""
        self._header.set_expanded(expanded)


# ---------------------------------------------------------------------------
# Shared widget helpers (used by multiple section classes)
# ---------------------------------------------------------------------------


def field_label(text: str) -> QLabel:
    """The small muted caption above a sidebar field.

    Returns a ``ThemedTextLabel`` rather than styling a bare ``QLabel``: a factory
    cannot restyle what it has handed back, so the colour would otherwise go stale on
    every caller — which is exactly what ``ClientWidget`` and ``SidebarField`` were
    doing.

    The trailing colon and the fixed horizontal policy are part of the caption: the
    policy is what holds the label column at a consistent width in ``ClientWidget``'s
    rows, whose value labels are added with stretch.
    """
    lbl = ThemedTextLabel(text + ":", css="font-size: 11px; font-weight: 600;")
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
