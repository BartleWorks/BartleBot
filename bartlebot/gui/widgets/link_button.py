"""A button that reads as a link: no frame, accent text, pointer cursor.

Third use of the ``setFlat(True)`` + accent-stylesheet pattern (the keep list's
bulk row, the node browser's star, this), which is what earns the extraction.
The other half of the reason is theming: the accent is baked into a stylesheet
the widget sets on *itself*, so it has to be re-derived on a theme switch, and a
factory returning a plain ``QPushButton`` would put that obligation on every
caller instead.

Prefer this over a check box for a control whose *effect* can be named. "Show
descriptions" / "Hide descriptions" states what a click will do; a tick box
captioned "Show descriptions" has to be read together with its own state, and
sitting at the top of a list of options it reads as the first option.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton, QSizePolicy, QWidget

from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin


class LinkButton(ThemedMixin, QPushButton):
    """A flat, accent-coloured button sized to its own text."""

    def __init__(self, text: str = "", *, font_size: int | None = None, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._font_size = THEME.font_subtext_px if font_size is None else font_size
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        self.setStyleSheet(
            f"QPushButton {{ color: {THEME.accent}; font-size: {self._font_size}px;"
            " background: transparent; border: none; padding: 0; text-align: left; }"
            f"QPushButton:hover {{ color: {THEME.text}; }}"
        )
