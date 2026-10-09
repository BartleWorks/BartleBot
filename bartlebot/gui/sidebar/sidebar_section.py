"""Shared sidebar infrastructure: SidebarSection and widget helpers."""

from __future__ import annotations

import math

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QFontMetricsF, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.theming.icon_utils import svg_pixmap
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.widgets.buttons.icon_button import IconButton
from bartlebot.gui.widgets.labels.themed_label import ThemedTextLabel

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


class HeadingAction(IconButton):
    """An icon button on a section heading, drawn only while the heading is reached for.

    ``reveal(False)`` blanks the glyph and leaves the button where it is: still in the
    layout, so nothing beside it moves, and still in the Tab order, so the keyboard can
    reach it -- which is what then draws it. ``IconButton`` re-renders its glyph on
    every theme pass and every enable change, so the blank is applied in that same
    pass rather than as a one-off that the next pass would undo.
    """

    # A class default, because ``IconButton.__init__`` already runs a theme pass -- and so
    # ``apply_theme_styles`` below -- before this ``__init__`` could assign anything.
    _revealed = False

    def __init__(self, icon_name: str, *, tooltip: str, parent: QWidget | None = None) -> None:
        super().__init__(icon_name, tooltip=tooltip, parent=parent)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        policy = self.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.setSizePolicy(policy)

    @property
    def revealed(self) -> bool:
        return self._revealed

    def reveal(self, shown: bool) -> None:
        if shown == self._revealed:
            return
        self._revealed = shown
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        super().apply_theme_styles()
        if not self._revealed:
            self.setIcon(QIcon())


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
        self._layout = layout
        self._actions: list[QWidget] = []
        self._hovered = False

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    # -- heading actions -------------------------------------------------
    #
    # A heading can carry a few icon buttons at its right end -- Option Values's Collapse all and
    # Expand all, for the option cards under it. They follow VS Code's view headers:
    # drawn only while the pointer is over the heading or one of them has keyboard focus,
    # so a heading nobody is reaching for stays a heading.
    #
    # *Drawn*, not shown. A hidden widget is skipped by the Tab order, so a button that
    # really hid itself until focused could never be focused at all. The buttons stay in
    # the layout and the focus chain the whole time and only stop drawing their glyph --
    # see ``reveal``. They are hidden outright only while the section is folded, when
    # there is nothing under the heading for them to act on.
    #
    # A press on one never reaches ``mousePressEvent`` below, because a button accepts
    # its own press, so clicking an action does not also fold the section.

    def add_action(self, button: QWidget) -> None:
        """Append *button* to the right end of the heading. It must have ``reveal(bool)``."""
        self._layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._actions.append(button)
        button.installEventFilter(self)
        button.setVisible(self._expanded)
        self._apply_reveal()

    @property
    def actions(self) -> list[QWidget]:
        return list(self._actions)

    def _apply_reveal(self) -> None:
        revealed = self._hovered or any(action.hasFocus() for action in self._actions)
        for action in self._actions:
            action.reveal(revealed)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._hovered = True
        self._apply_reveal()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._hovered = False
        self._apply_reveal()
        super().leaveEvent(event)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if obj in self._actions and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self._apply_reveal()
        return False

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
        for action in self._actions:
            action.setVisible(expanded)
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
    # Vertical size policy used while the section is expanded.  Subclasses that
    # need to grow (e.g. a list that should fill remaining space) override this.
    #
    # ``Preferred`` rather than ``Maximum``, because a section's content can be
    # height-for-width and ``Maximum`` caps the widget at ``sizeHint()``, which is
    # computed at no particular width.  A folded-out option description makes
    # ``heightForWidth()`` the taller of the two, so the layout reserves the tall
    # cell and then refuses to let the widget fill it -- and ``QWidgetItem``
    # centers a widget that cannot fill its cell, which reads as the section
    # sliding downwards as it grows.  The collapsed state still uses
    # ``Maximum``; see ``_on_section_toggled``.
    _expanded_v_policy: QSizePolicy.Policy = QSizePolicy.Policy.Preferred

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        # A section is born expanded, so it starts on the expanded policy.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, self._expanded_v_policy)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(2)

        self._header = _SectionHeader(title)
        self._body = QWidget()
        # Same reasoning as the section's own policy: capped at ``sizeHint()`` the
        # body cannot fill a height-for-width cell, and the leftover is split above
        # and below it -- which pushes the body away from the header it belongs to.
        self._body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

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
            # cell and the header appears centered within it.
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

    def add_header_action(self, button: QWidget) -> None:
        """Put *button* at the right end of the heading, drawn on hover or focus.

        *button* needs a ``reveal(shown: bool)`` method that draws or blanks its glyph
        without hiding the widget -- ``HeadingAction`` is the one there is.
        """
        self._header.add_action(button)

    @property
    def header_actions(self) -> list[QWidget]:
        return self._header.actions

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
    cannot restyle what it has handed back, so the color would otherwise go stale on
    every caller — which is exactly what the player card and ``SidebarField`` were
    doing.

    The trailing colon and the fixed horizontal policy are part of the caption: the
    policy is what holds the label column at a consistent width in ``PlayerCard``'s
    rows, whose value labels are added with stretch.
    """
    lbl = ThemedTextLabel(text + ":", css=f"font-size: {THEME.font_small_px}px; font-weight: 600;")
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
    """Read-only multi-line value field, exactly *lines* of its own text tall.

    Measured rather than a pixel count per line. ``18 * lines + 10`` was the body font's
    line spacing when this was written, so the field went on claiming 18px a line after
    the body text moved and cut the last line off.

    Three things the arithmetic needs, and none of them is ``QFontMetrics.lineSpacing()``
    on its own:

    * The **ceiling** of the fractional spacing. ``QFontMetrics`` rounds it down -- 21 for
      a line the document lays out at 21.28 -- and a pixel a line is the last line gone by
      the fourth.
    * The document's **margin**, which ``QPlainTextEdit`` puts inside the frame at both
      ends, and the **frame** itself, which carries a style sheet's border and padding.
    * One pixel over, which is what the document layout takes beyond its own text.
    """
    w = QPlainTextEdit()
    w.setReadOnly(True)
    w.setPlaceholderText(_PLACEHOLDER)
    line_px = math.ceil(QFontMetricsF(w.font()).lineSpacing())
    chrome = int(2 * w.document().documentMargin()) + 2 * w.frameWidth() + 1
    w.setFixedHeight(line_px * lines + chrome)
    return w
