"""The bot's own description, under a collapsible ``Description`` header.

The description heads both option surfaces — read-only above Setup, editable above
Options — because the four bots carrying multi-line ``REQUIREMENTS:`` blocks say
things like "change the client resolution to 800x600 before running", and that is
exactly the text eliding it into the header bar would have hidden.

Two things fall out of that:

- **Options owns the only editor.**  Setup is a values surface, and the description
  is authorial; the same split that puts declarations in Options puts the writable
  copy there.  The read-only copy is not a lesser variant of the editor — it
  is a wrapping label, so a long block is read in full rather than through a
  capped viewport.
- **It collapses.**  A header the user can fold is what makes a long block
  affordable above a surface they came to the tab to use.
- **The editor is as tall as its text**, between two and eight lines
  (``GrowingTextEdit``).  A fixed three-line box was both halves of one bug: at the
  300px default sidebar three lines is the *median* of the shipped library, so 47%
  of bots scrolled a viewport while the other half held blank space above the
  options they had come to edit.  Eight lines fits 94% of them; the three gauntlets
  whose ``REQUIREMENTS`` blocks run to 20 lines still scroll here, which is the same
  split as ever — the wrapping label above Setup is where a long block is *read*.

Writing back is guarded by ``_suppress``: ``setPlainText`` emits
``textChanged`` like any other edit, so refreshing from a freshly loaded config
would otherwise mark the bot dirty the moment it opened.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QVBoxLayout

from bartlebot.gui.sidebar.sidebar_section import SidebarSection
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.growing_text_edit import GrowingTextEdit
from bartlebot.gui.widgets.themed_label import ThemedTextLabel

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

NO_DESCRIPTION = "This bot has no description."

# One line's worth at the 300px default sidebar, because Qt draws placeholder text
# as a single unwrapped line and clips the rest: a prompt that says more than this
# says less.  The full version of it is the section's tooltip.
_PROMPT = "What this bot does, and any setup it needs."

_TOOLTIP = "What this bot does, and anything the user has to set up before running it."

# Measured over the 80 shipped bots at the 300px default sidebar: 8% are two lines
# or fewer, 90% are six or fewer, 94% are eight or fewer, and the tail is three
# 20-line gauntlets.  Two is a floor that barely ever binds — and the one that a
# taller floor would cost is the *empty* editor, which is where a new bot's author
# starts; eight is where the distribution stops and the outliers begin.
_MIN_LINES = 2
_MAX_LINES = 8

# On the widget, not in the stylesheet: a stylesheet ``font-size`` never reaches
# ``QWidget.font()``, and the editor measures its own line height from that font.
_FONT_PX = 11


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
        self._edit: GrowingTextEdit | None = None

        if editable:
            self._edit = GrowingTextEdit(min_lines=_MIN_LINES, max_lines=_MAX_LINES)
            font = QFont(self._edit.font())
            font.setPixelSize(_FONT_PX)
            self._edit.setFont(font)
            self._edit.setPlaceholderText(_PROMPT)
            self._edit.setToolTip(_TOOLTIP)
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
    def editor(self) -> GrowingTextEdit | None:
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
            # No ``font-size`` here — it is set on the widget, which is the only
            # place the editor's own line-height measurement can read it from.
            self._edit.setStyleSheet(
                f"QTextEdit {{ background: {THEME.surface}; border: 1px solid {THEME.border}; "
                f"border-radius: 4px; color: {THEME.text}; padding: 3px; }}"
            )
