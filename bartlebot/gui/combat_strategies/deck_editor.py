"""Editor for a DeckPreset: an editable name and description over a read-only card list.

A deck preset's *contents* are captured from the game, not typed — there is no
sensible way to author "4 × Storm Blade" by hand that is better than pressing
Capture with the deck already set up. So the card list is shown, never edited,
and the only writable fields are the two that identify the preset to a human.

Like its siblings in this package, it writes through to the working copy it was
handed and never touches disk; the 500ms rename debounce it inherited from the
Strategy Library window is gone with the rest of the autosaves. See
:mod:`bartlebot.strategy_workset`.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.strategy_models import DeckPreset
from bartlebot.gui.widgets.elided_label import apply_muted_color


class DeckPresetEditor(QWidget):
    """Edit a DeckPreset's identity; show what it holds."""

    #: An edit landed on the loaded preset — for whoever draws the card list.
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._preset: DeckPreset | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Name and description moved up into the detail pane's shared *About* section —
        # see :class:`~bartlebot.gui.combat_strategies.dialog.AboutSection`. What is left
        # here is the one thing this editor was always really showing: what the preset
        # holds.
        layout.addWidget(QLabel("Cards:"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._cards_widget = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(4)
        scroll.setWidget(self._cards_widget)
        layout.addWidget(scroll, 1)

    def preset(self) -> DeckPreset | None:
        """The working copy being edited, or None."""
        return self._preset

    def load(self, preset: DeckPreset) -> None:
        self._preset = preset
        self._rebuild_cards()

    def clear(self) -> None:
        self._preset = None
        self._rebuild_cards()

    def _rebuild_cards(self) -> None:
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        if self._preset is None:
            return

        # Treasure cards are omitted — never captured or applied, so showing
        # them would misrepresent what the preset restores.
        deck = self._preset.deck
        any_section = False
        for section_name, cards in (("Normal Cards", deck.normal), ("Item Cards", deck.item)):
            if not cards:
                continue
            any_section = True
            self._cards_layout.addWidget(QLabel(f"<b>{section_name}</b>"))
            for name, count in sorted(cards.items()):
                self._cards_layout.addWidget(QLabel(f"  {name} × {count}"))

        if not any_section:
            empty = QLabel("(empty deck — capture one from a client to fill it)")
            apply_muted_color(empty)
            self._cards_layout.addWidget(empty)

        self._cards_layout.addStretch()
