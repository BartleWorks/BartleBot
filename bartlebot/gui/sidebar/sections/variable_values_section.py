"""Sidebar section: the bot's variables as the run has them, on the Run-mode Options tab (#188).

Edit mode's Variables section is the declarations; this is the values, under Option Values
the way that one sits under Options, so a mode switch keeps the variables where they were.
Before a run each row shows where the variable starts. During one it shows what the
variable holds now, from the machine's ``on_variable_change``, and a value that just
changed is drawn in the accent, with what it was in its tooltip, until the first node
after the write that touches the game has run (``variable_watch``). After the run the
values stay, so the run can be read; a new run, or another bot, starts them again.

The section never restates a write in the log. The machine logs each one once, and that
line already reaches the in-app log.

A bot with no variables has nothing to show here, so the section hides.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from bartleby.qt.sidebar.sidebar_section import SidebarSection
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from bartleby.qt.widgets.layout.discard import discard_widget
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from bartlebot.bot import variables
from bartlebot.bot.variable_watch import LiveVariables
from bartlebot.gui.sidebar.option_gutter import (
    HelpToggle,
    OverrideBar,
    RevertButton,
    build_gutter_row,
    keep_slot_when_hidden,
)

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig, VariableModel

logger = logging.getLogger(__name__)


class _ValueRow:
    """One variable's row: its name, and its value right-aligned on the same line."""

    def __init__(self, decl: VariableModel) -> None:
        self.decl = decl
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(2)
        head = QHBoxLayout()
        body_layout.addLayout(head)
        name_lbl = QLabel(decl.name)
        name_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        name_font = QFont(name_lbl.font())
        name_font.setBold(True)
        name_lbl.setFont(name_font)
        self.value_lbl = ThemedTextLabel(color="text")
        self.value_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        head.addWidget(name_lbl, 1)
        head.addWidget(self.value_lbl)

        self.description: QWidget | None = None
        if decl.description:
            self.description = ThemedTextLabel(decl.description)
            self.description.setWordWrap(True)
            body_layout.addWidget(self.description)

        # Option Values' revert column, kept so the ``?`` lines up with the rows above;
        # a variable is the bot's, so the user has nothing here to revert.
        slot = RevertButton()
        help_btn = HelpToggle()
        help_btn.setToolTip("Show what this variable remembers")
        self.widget = build_gutter_row(OverrideBar(), slot, help_btn, body)

        # Visibility only now that the row is parented: see gui/CLAUDE.md.
        keep_slot_when_hidden(slot)
        if self.description is None:
            keep_slot_when_hidden(help_btn)
        else:
            self.description.setVisible(False)
            help_btn.toggled.connect(self.description.setVisible)

    def show_value(self, value: Any, *, marked: bool, before: Any) -> None:
        self.value_lbl.setText(variables.format_value(value))
        font = QFont(self.value_lbl.font())
        font.setBold(marked)
        self.value_lbl.setFont(font)
        self.value_lbl.set_color("accent" if marked else "text")
        if marked:
            self.value_lbl.setToolTip(f"Just changed: it was {variables.format_value(before)}")
        else:
            start = self.decl.initial_value()
            self.value_lbl.setToolTip(f"Starts each run at {variables.format_value(start)}")


class VariableValuesSection(SidebarSection):
    """The variables' values, one row each: where they start before a run, and what they hold during it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Variables", parent)
        self._config: BotConfig | None = None
        self._declared: tuple[str, ...] | None = None
        self._running = False
        self._live = LiveVariables()
        self._rows: dict[str, _ValueRow] = {}

        outer = QVBoxLayout()
        outer.setSpacing(4)
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(6)
        outer.addLayout(self._rows_layout)
        self.set_content_layout(outer)
        self.setVisible(False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_config(self, config: BotConfig | None) -> None:
        """Show ``config``'s variables, each where it starts.

        During a run the config can be swapped for an equal one (the pre-run recompose,
        an undo snapshot), and the values the run has written are kept unless the
        declarations themselves changed.
        """
        declared = tuple(v.model_dump_json() for v in config.variables) if config is not None else ()
        same_bot = self._running or config is self._config
        self._config = config
        if same_bot and declared == self._declared:
            return
        self._declared = declared
        self._live.reset(self._declarations())
        self._rebuild()

    def run_started(self) -> None:
        """A run is starting, so every value is back where it starts, as the machine's are."""
        self._running = True
        self._live.reset(self._declarations())
        self._refresh_all()

    def run_finished(self) -> None:
        """The run is over. The values it left stay on screen until the next run or another bot."""
        self._running = False

    def on_variable_change(self, name: str, old: Any, new: Any) -> None:
        """The machine's ``on_variable_change``: one write, which marks its row."""
        self._live.change(name, old, new)
        self._refresh(name)

    def on_node_started(self, node_type: str | None) -> None:
        """A node is starting; a mark the run has shown long enough goes."""
        for name in self._live.node_started(node_type):
            self._refresh(name)

    @property
    def row_names(self) -> list[str]:
        """The variables shown, in the file's order."""
        return list(self._rows)

    def value_text(self, name: str) -> str | None:
        """The value ``name``'s row shows, as text."""
        row = self._rows.get(name)
        return row.value_lbl.text() if row else None

    def is_marked(self, name: str) -> bool:
        """Whether ``name``'s row shows it as just changed."""
        return self._live.is_marked(name)

    def value_label(self, name: str) -> QLabel | None:
        """``name``'s value label, for a test to look at."""
        row = self._rows.get(name)
        return row.value_lbl if row else None

    # ------------------------------------------------------------------

    def _declarations(self) -> list[VariableModel]:
        return list(self._config.variables) if self._config is not None else []

    def _rebuild(self) -> None:
        for row in self._rows.values():
            discard_widget(row.widget, self._rows_layout)
        self._rows.clear()
        for decl in self._declarations():
            if decl.name in self._rows:
                continue  # a second declaration of one name: validation refuses the run
            row = _ValueRow(decl)
            self._rows_layout.addWidget(row.widget)
            self._rows[decl.name] = row
        self._refresh_all()
        # Shown only once parented, or it would open as a window of its own (gui/CLAUDE.md).
        if not self._rows or self.parent() is not None:
            self.setVisible(bool(self._rows))

    def _refresh_all(self) -> None:
        for name in self._rows:
            self._refresh(name)

    def _refresh(self, name: str) -> None:
        row = self._rows.get(name)
        if row is None:
            return
        row.show_value(self._live.value(name), marked=self._live.is_marked(name), before=self._live.before(name))
