"""``IconButton`` — a fixed-square, icon-only button that re-tints itself.

The counterpart to ``ActionButton``, and the reason both exist: an ``ActionButton``
is *a labeled verb that expands to share a row*, so it sets ``Expanding``, a minimum
height and a text label. Roughly a seventh of this app's buttons are none of those —
a remove ``✕``, a close ``×``, a help ``?``, a favorite star. Forcing them into the
action vocabulary would stretch a 22px affordance across a toolbar; leaving them bare
leaves every one of them a bespoke ``setFixedSize`` plus an inline stylesheet.

``svg_icon``'s tint defaults to ``THEME.icon_tint``, so an icon rendered once and
handed to ``setIcon`` is a snapshot of whichever theme was active then -- stale
after a switch, with no stylesheet anywhere for a text search to find.  That is
the whole reason this is a class rather than three lines at each call site.

The second reason is the *enabled* tint.  In the Option Values gutter the revert button
is disabled on most rows and its being enabled is one of the two marks saying
"this value is yours"; the other is the override bar it sits against.  So the
accent is spent on the enabled state and the icon falls back to the ordinary
tint when there is nothing to revert -- which means the icon has to be re-rendered
on ``setEnabled`` as well as on a theme switch.

A badge is a state the button reports without changing its glyph: an accent dot in
the glyph's top-right corner, as an editor's tab marks unsaved changes (Edit mode's
Save, #267). It is painted over the icon rather than drawn into the SVG, which is
tinted whole, and it is a shape as well as a color, so it reads in grayscale.

A tooltip is required rather than optional. An icon-only control carries no label,
so without one it is unusable to anyone who does not already recognize the glyph;
making it a keyword argument with no default is what stops that being an oversight.
"""

from __future__ import annotations

from enum import Enum

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QPushButton, QWidget

from bartlebot.gui.theming.icon_utils import svg_icon
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.theming.themed import ThemedMixin


class IconTint(Enum):
    """What color the glyph is drawn in — the only thing an ``IconButton`` varies.

    The same rule ``ActionStyle`` follows: a variant changes color and nothing else,
    because the size is the caller's to choose and everything else is shared.

    ``DEFAULT`` is ordinary chrome. ``ACCENT`` is a button whose glyph is the state it
    reports (the node browser's favorite star). ``ACCENT_WHEN_ENABLED`` spends the
    accent only while the button can act, for a row where *being* enabled is the
    signal. ``DANGER`` is a remove — the red is what separates it from the controls it
    sits beside in a flag row. ``ON_ACCENT`` is a glyph drawn *over* an accent fill (the
    crumb bar's close), in the color text on that fill already uses.
    """

    DEFAULT = "default"
    ACCENT = "accent"
    ACCENT_WHEN_ENABLED = "accent_when_enabled"
    DANGER = "danger"
    ON_ACCENT = "on_accent"


class IconButton(ThemedMixin, QPushButton):
    """A fixed-size, icon-only button whose glyph follows the theme.

    Pass *checked_icon_name* for a toggle: the button becomes checkable and swaps
    glyphs on state change, which is what the favorite star needs. For a swap that
    is not a toggle — one driven by something other than this button's own checked
    state — use :meth:`set_icon_name`, as ``ActionButton`` does.
    """

    def __init__(
        self,
        icon_name: str,
        *,
        tooltip: str,
        size: int = 20,
        icon_size: int = 16,
        tint: IconTint = IconTint.DEFAULT,
        checked_icon_name: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self._checked_icon_name = checked_icon_name
        self._icon_px = icon_size
        self._tint = tint
        self._badge = False
        self.setToolTip(tooltip)
        self.setFixedSize(size, size)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setFlat(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Whether the pointer is set, held here because unsetCursor() leaves WA_SetCursor
        # alone on a widget with no parent yet.
        self._pointer = True
        if checked_icon_name is not None:
            self.setCheckable(True)
            self.toggled.connect(self._on_toggled)
        self.refresh_theme_styles()

    @property
    def icon_name(self) -> str:
        """The glyph drawn when unchecked -- the one :meth:`set_icon_name` last set."""
        return self._icon_name

    def set_icon_name(self, icon_name: str) -> None:
        """Swap the glyph — e.g. a play button that has become a pause button."""
        if icon_name == self._icon_name:
            return
        self._icon_name = icon_name
        self.refresh_theme_styles()

    def set_tint(self, tint: IconTint) -> None:
        """Change the glyph's color — a toggle whose state lives elsewhere, spending the
        accent while it is on (the authoring bar's toggles)."""
        if tint is self._tint:
            return
        self._tint = tint
        self.refresh_theme_styles()

    @property
    def has_badge(self) -> bool:
        """Whether the accent dot is on show."""
        return self._badge

    def set_badge(self, shown: bool) -> None:
        """Show or hide the accent dot in the glyph's top-right corner."""
        if shown == self._badge:
            return
        self._badge = shown
        self.update()

    def _badge_rect(self) -> QRectF:
        """Where the dot is drawn: three eighths of the glyph across, in its top-right corner."""
        d = max(5, round(self._icon_px * 3 / 8))
        left = (self.width() - self._icon_px) / 2 + self._icon_px - d
        top = (self.height() - self._icon_px) / 2
        return QRectF(left, top, d, d)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        if not self._badge:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(THEME.accent))
        painter.drawEllipse(self._badge_rect())
        painter.end()

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 - Qt override
        was = self.isEnabled()
        super().setEnabled(enabled)
        # A pointer while enabled, and no cursor of its own while disabled, so the parent's
        # shows: Qt clears a native window's cursor over a disabled widget that has one, and
        # the window then borrows its parent window's, which can be stale (#258). Only on a
        # change, since every setCursor call writes to the window, pointer over it or not.
        if enabled != self._pointer:
            self._pointer = enabled
            if enabled:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.unsetCursor()
        if enabled != was:
            self.refresh_theme_styles()

    def _on_toggled(self, _checked: bool) -> None:
        self.refresh_theme_styles()

    def _glyph(self) -> str:
        if self._checked_icon_name is not None and self.isChecked():
            return self._checked_icon_name
        return self._icon_name

    def _tint_color(self) -> str:
        if self._tint is IconTint.DANGER:
            return THEME.error
        if self._tint is IconTint.ACCENT:
            return THEME.accent
        if self._tint is IconTint.ACCENT_WHEN_ENABLED and self.isEnabled():
            return THEME.accent
        if self._tint is IconTint.ON_ACCENT:
            return THEME.pill_text
        return THEME.icon_tint

    def apply_theme_styles(self) -> None:
        self.setIcon(svg_icon(self._glyph(), self._icon_px, tint=self._tint_color()))
        self.setStyleSheet(
            "QPushButton { background: transparent; border: none; padding: 0; }"
            f"QPushButton:hover:enabled {{ background: {THEME.surface_disabled}; border-radius: 3px; }}"
        )
