"""A line edit that looks like what it is: free-text search.

The leading magnifier is the near-universal convention for "type here to narrow
what is below", and it does that job before the placeholder has been read — which
matters most for the fields whose placeholder is just "Filter…".

It exists as a widget rather than a two-line helper because the icon has to be
re-tinted when the theme changes. ``svg_icon`` bakes the tint in at render time, so
an icon added once at construction keeps a dark glyph on a dark field after a switch
to the dark theme. ``changeEvent`` is the hook for that (CLAUDE.md, GUI theming);
putting it in one widget means no call site has to remember.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QLineEdit, QWidget

from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.theme import THEME

SEARCH_ICON_SIZE = 14


class SearchLineEdit(QLineEdit):
    """``QLineEdit`` with a leading search icon that tracks the theme.

    Everything else — the clear button, signal wiring, width — is left to the caller,
    so this can replace an existing field without changing its behavior.
    """

    def __init__(self, placeholder: str = "Search…", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self._icon_action = self.addAction(self._icon(), QLineEdit.ActionPosition.LeadingPosition)

    @staticmethod
    def _icon():
        return svg_icon("search", SEARCH_ICON_SIZE, tint=THEME.text_muted)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._icon_action.setIcon(self._icon())
