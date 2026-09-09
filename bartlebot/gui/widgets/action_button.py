"""``ActionButton`` — a flat, icon-plus-text push button that fills the space it is given.

The Fusion style's default ``QPushButton`` chrome (a beveled 3D frame sized to its
own label) is what every icon-and-text row in this app has been fighting: SVG
icons drawn against that frame read as flattened and off-centre, and a row of
such buttons naturally sizes each one to its own text rather than sharing the
row evenly. ``ActionButton`` is the reusable replacement — flat, theme-tinted,
and set to expand — so a row of them (``buttons.addWidget(btn, 1)`` each) splits
the available width evenly instead of leaving buttons to size themselves and
leaving a stretch to eat the remainder.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QPushButton, QSizePolicy, QWidget

from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin


class ActionButton(ThemedMixin, QPushButton):
    """A flat button with an icon and a label, sized to expand and fill its row.

    Meant for a row of equal-weight actions (``+ New`` / ``Duplicate`` / ``Delete``
    and similar) rather than a single call-to-action — there is no accent variant
    because nothing here should look more important than something dangerous like
    delete sitting beside it.
    """

    def __init__(self, icon_name: str, text: str, *, icon_size: int = 16, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._icon_name = icon_name
        self._icon_px = icon_size
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(28)
        self.refresh_theme_styles()

    def set_icon_name(self, icon_name: str) -> None:
        """Swap the glyph — e.g. delete becoming revert once an origin is known."""
        if icon_name == self._icon_name:
            return
        self._icon_name = icon_name
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        self.setIcon(svg_icon(self._icon_name, self._icon_px))
        self.setStyleSheet(
            "QPushButton {"
            f"  background: {THEME.surface};"
            f"  color: {THEME.text};"
            f"  border: 1px solid {THEME.border};"
            "  border-radius: 4px;"
            "  padding: 4px 10px;"
            "}"
            f"QPushButton:hover:enabled {{ background: {THEME.surface_disabled}; }}"
            f"QPushButton:pressed:enabled {{ background: {THEME.border}; }}"
            f"QPushButton:disabled {{ color: {THEME.text_disabled}; background: {THEME.surface_disabled}; }}"
        )
