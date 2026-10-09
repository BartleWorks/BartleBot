"""The bot's own description, under a collapsible ``Description`` header.

The description heads both option surfaces — read-only above Option Values, editable above
Options — because the four bots carrying multi-line ``REQUIREMENTS:`` blocks say
things like "change the client resolution to 800x600 before running", and that is
exactly the text eliding it into the header bar would have hidden.

Two things fall out of that:

- **Options owns the only editor.**  Option Values is a values surface, and the description
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
  split as ever — the wrapping label above Option Values is where a long block is *read*.

**An empty description stands in for itself with the one the graph implies**
(``bartlebot/content/bot_summary.py``).  Above Option Values it is shown in italics, so it never reads
as something the author wrote.  Above Options it sits under the empty editor with a
*Use this* link rather than in the placeholder, which Qt draws as one unwrapped line and
clips — and the generated sentence is usually longer than the prompt.  It is never
written to the file unless the author takes it.

Writing back is guarded by ``_suppress``: ``setPlainText`` emits
``textChanged`` like any other edit, so refreshing from a freshly loaded config
would otherwise mark the bot dirty the moment it opened.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from bartlebot.content import bot_summary
from bartlebot.gui.sidebar.sidebar_section import SidebarSection
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.growing_text_edit import GrowingTextEdit
from bartlebot.gui.widgets.link_button import LinkButton
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


class DescriptionSection(ThemedMixin, SidebarSection):
    """Collapsible ``Description`` section: a label, or an editor in Edit mode."""

    description_changed = pyqtSignal()

    def __init__(self, editable: bool = False, parent=None):
        super().__init__("Description", parent)
        self._config: BotConfig | None = None
        self._suppress = False
        self._generated: str | None = None

        vbox = QVBoxLayout()
        vbox.setSpacing(0)

        self._label: ThemedTextLabel | None = None
        self._edit: GrowingTextEdit | None = None

        if editable:
            self._edit = GrowingTextEdit(min_lines=_MIN_LINES, max_lines=_MAX_LINES)
            # On the widget, not in the stylesheet: a stylesheet ``font-size`` never
            # reaches ``QWidget.font()``, and the editor measures its own line height
            # from that font.
            font = QFont(self._edit.font())
            font.setPixelSize(THEME.font_small_px)
            self._edit.setFont(font)
            self._edit.setPlaceholderText(_PROMPT)
            self._edit.setToolTip(_TOOLTIP)
            self._edit.textChanged.connect(self._on_text_changed)
            vbox.addWidget(self._edit)

            # The generated sentence, offered while the editor is empty.
            self._suggestion = QWidget()
            row = QHBoxLayout(self._suggestion)
            row.setContentsMargins(0, 4, 0, 0)
            row.setSpacing(8)
            self._suggestion_lbl = ThemedTextLabel(
                "", color="text_muted", css=f"font-size: {THEME.font_small_px}px; font-style: italic;"
            )
            self._suggestion_lbl.setWordWrap(True)
            self._suggestion_lbl.setTextFormat(Qt.TextFormat.PlainText)
            self._suggestion_lbl.setToolTip("Generated from the graph. Shown while the description is empty.")
            self._use_btn = LinkButton("Use this")
            self._use_btn.setToolTip("Put this sentence in the description, to edit or keep")
            self._use_btn.clicked.connect(self._use_generated)
            row.addWidget(self._suggestion_lbl, 1)
            row.addWidget(self._use_btn, 0, Qt.AlignmentFlag.AlignTop)
            vbox.addWidget(self._suggestion)
        else:
            self._label = ThemedTextLabel("", color="text_muted", css=f"font-size: {THEME.font_small_px}px;")
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
        config = self._config
        self._generated = bot_summary.describe(config) if config is not None and not config.is_definition else None
        if self._label is not None:
            if text or not self._generated:
                self._label.setTextFormat(Qt.TextFormat.PlainText)
                self._label.setText(text or NO_DESCRIPTION)
            else:
                self._label.setTextFormat(Qt.TextFormat.RichText)
                self._label.setText(f"<i>{html.escape(self._generated)}</i>")
        if self._edit is not None and self._edit.toPlainText() != text:
            # Guarded, not because the write is wrong but because it is not the
            # user's: an unguarded setPlainText on load marks the bot dirty.  The
            # equality check keeps the caret where it is when nothing changed.
            self._suppress = True
            try:
                self._edit.setPlainText(text)
            finally:
                self._suppress = False
        self._refresh_suggestion()

    @property
    def generated(self) -> str | None:
        """The sentence the graph implies, shown while the description is empty; None if none."""
        return self._generated

    @property
    def suggestion_shown(self) -> bool:
        """Whether the editor is offering the generated sentence — never on the read-only copy."""
        return self._edit is not None and not self._suggestion.isHidden()

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
        if self._label.textFormat() == Qt.TextFormat.RichText:
            return self._generated or ""
        return self._label.text()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _refresh_suggestion(self) -> None:
        if self._edit is None:
            return
        self._suggestion_lbl.setText(self._generated or "")
        self._suggestion.setVisible(bool(self._generated) and not self._edit.toPlainText().strip())

    def _use_generated(self) -> None:
        if self._edit is not None and self._generated:
            self._edit.setPlainText(self._generated)

    def _on_text_changed(self) -> None:
        self._refresh_suggestion()
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
