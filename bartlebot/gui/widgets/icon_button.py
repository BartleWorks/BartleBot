"""A small, flat, icon-only button that re-tints itself when the theme changes.

``svg_icon``'s tint defaults to ``THEME.icon_tint``, so an icon rendered once and
handed to ``setIcon`` is a snapshot of whichever theme was active then -- stale
after a switch, with no stylesheet anywhere for a text search to find.  That is
the whole reason this is a class rather than three lines at each call site.

The second reason is the *enabled* tint.  In the Setup gutter the revert button
is disabled on most rows and its being enabled is one of the two marks saying
"this value is yours"; the other is the override bar it sits against.  So the
accent is spent on the enabled state and the icon falls back to the ordinary
tint when there is nothing to revert -- which means the icon has to be re-rendered
on ``setEnabled`` as well as on a theme switch.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QPushButton, QWidget

from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin


class ThemedIconButton(ThemedMixin, QPushButton):
    """An icon-only button whose glyph follows the theme and its own enabled state.

    *accent_when_enabled* spends ``THEME.accent`` on the enabled icon; leave it off
    for a button whose being enabled carries no meaning of its own.
    """

    def __init__(
        self,
        icon_name: str,
        *,
        size: int = 20,
        icon_size: int = 16,
        accent_when_enabled: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self._icon_px = icon_size
        self._accent_when_enabled = accent_when_enabled
        self.setFixedSize(size, size)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setFlat(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_theme_styles()

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 - Qt override
        was = self.isEnabled()
        super().setEnabled(enabled)
        if enabled != was:
            self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        tint = THEME.accent if (self._accent_when_enabled and self.isEnabled()) else THEME.icon_tint
        self.setIcon(svg_icon(self._icon_name, self._icon_px, tint=tint))
        self.setStyleSheet(
            "QPushButton { background: transparent; border: none; padding: 0; }"
            f"QPushButton:hover:enabled {{ background: {THEME.surface_disabled}; border-radius: 3px; }}"
        )
