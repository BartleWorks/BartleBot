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
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
        self._suppress = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.textChanged.connect(self._on_changed)
        name_row.addWidget(self._name_edit, 1)
        layout.addLayout(name_row)

        layout.addWidget(QLabel("Description:"))
        self._description_edit = QLineEdit()
        self._description_edit.setPlaceholderText("What this deck is for (optional)")
        self._description_edit.textChanged.connect(self._on_changed)
        layout.addWidget(self._description_edit)

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
        self._suppress = True
        self._name_edit.setText(preset.name)
        self._description_edit.setText(preset.description)
        self._suppress = False
        self._rebuild_cards()

    def clear(self) -> None:
        self._preset = None
        self._suppress = True
        self._name_edit.clear()
        self._description_edit.clear()
        self._suppress = False
        self._rebuild_cards()

    def _on_changed(self) -> None:
        if self._suppress or self._preset is None:
            return
        # As in PlayerStrategyEditor: an empty name would leave a nameless card
        # in the list with no way back to it.
        self._preset.name = self._name_edit.text().strip() or self._preset.name
        self._preset.description = self._description_edit.text()
        self.changed.emit()

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
