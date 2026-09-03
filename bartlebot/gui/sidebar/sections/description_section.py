"""The bot's own description, under a collapsible ``Description`` header.

The description heads both option surfaces — read-only above Setup, editable above
Options — because the four bots carrying multi-line ``REQUIREMENTS:`` blocks say
things like "change the client resolution to 800x600 before running", and that is
exactly the text eliding it into the header bar would have hidden.

Two things fall out of that:

- **Options owns the only editor.**  Setup is a values surface, and the description
  is authorial; the same split that puts declarations in Options puts the writable
  copy there (#180).  The read-only copy is not a lesser variant of the editor — it
  is a wrapping label, so a long block is read in full rather than through a
  three-line viewport.
- **It collapses.**  A header the user can fold is what makes a long block
  affordable above a surface they came to the tab to use.

Writing back is guarded by ``_suppress``: ``QPlainTextEdit.setPlainText`` emits
``textChanged`` like any other edit, so refreshing from a freshly loaded config
would otherwise mark the bot dirty the moment it opened.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QPlainTextEdit, QVBoxLayout

from bartlebot.gui.sidebar.sidebar_section import SidebarSection
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.themed_label import ThemedTextLabel

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

NO_DESCRIPTION = "This bot has no description."

_PROMPT = "What this bot does, and anything the user has to set up before running it."

# Three lines plus the frame — the height the description editor had in the old Bot
# Info section.  Longer text scrolls; the read-only copy above Setup is where a
# REQUIREMENTS block is read in full.
_EDIT_LINES = 3


class DescriptionSection(ThemedMixin, SidebarSection):
    """Collapsible ``Description`` section: a label, or an editor in Edit mode."""

    description_changed = pyqtSignal()

    def __init__(self, editable: bool = False, parent=None):
        super().__init__("Description", parent)
        self._config: BotConfig | None = None
        self._suppress = False

        vbox = QVBoxLayout()
        vbox.setSpacing(0)

        self._label: ThemedTextLabel | None = None
        self._edit: QPlainTextEdit | None = None

        if editable:
            self._edit = QPlainTextEdit()
            self._edit.setPlaceholderText(_PROMPT)
            self._edit.setFixedHeight(18 * _EDIT_LINES + 10)
            self._edit.textChanged.connect(self._on_text_changed)
            vbox.addWidget(self._edit)
        else:
            self._label = ThemedTextLabel("", color="text_muted", css="font-size: 11px;")
            self._label.setWordWrap(True)
            vbox.addWidget(self._label)

        self.set_content_layout(vbox)
        self.refresh_theme_styles()
        self.set_config(None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self.refresh()

    def refresh(self) -> None:
        """Re-read the description from the bound config.

        Called by the read-only copy when the editable one writes, so the two never
        disagree across a mode switch, which does not re-bind either.
        """
        self.setVisible(self._config is not None)
        text = (self._config.description or "").strip() if self._config else ""
        if self._label is not None:
            self._label.setText(text or NO_DESCRIPTION)
        if self._edit is not None and self._edit.toPlainText() != text:
            # Guarded, not because the write is wrong but because it is not the
            # user's: an unguarded setPlainText on load marks the bot dirty.  The
            # equality check keeps the caret where it is when nothing changed.
            self._suppress = True
            try:
                self._edit.setPlainText(text)
            finally:
                self._suppress = False

    @property
    def is_editable(self) -> bool:
        """Whether this copy writes back — true only for the one above Options."""
        return self._edit is not None

    @property
    def editor(self) -> QPlainTextEdit | None:
        """The text edit, or ``None`` on the read-only copy."""
        return self._edit

    def set_text(self, text: str) -> None:
        """Type *text* into the editor, as the user would.

        Read-only copies ignore it: they follow the bot, and the bot is written
        through the copy above Options.
        """
        if self._edit is not None:
            self._edit.setPlainText(text)

    def text(self) -> str:
        """What the section is currently showing, whichever variant it is."""
        if self._edit is not None:
            return self._edit.toPlainText()
        assert self._label is not None
        return self._label.text()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _on_text_changed(self) -> None:
        if self._suppress or self._config is None:
            return
        text = self._edit.toPlainText() if self._edit is not None else ""
        self._config.description = text or None
        self.description_changed.emit()

    def apply_theme_styles(self) -> None:
        if self._edit is not None:
            self._edit.setStyleSheet(
                f"QPlainTextEdit {{ background: {THEME.surface}; border: 1px solid {THEME.border}; "
                f"border-radius: 4px; color: {THEME.text}; font-size: 11px; padding: 3px; }}"
            )
