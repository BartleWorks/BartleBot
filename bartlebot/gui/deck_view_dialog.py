"""Read-only dialog showing the contents of a saved player deck."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.strategy_models import DeckData
from bartlebot.gui.theme import THEME


class DeckViewDialog(QDialog):
    """Shows the cards in a DeckData in three labelled sections (Normal / TC / Item)."""

    def __init__(self, slot: int, deck: DeckData, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"P{slot} Deck")
        self.setMinimumWidth(280)
        self.setMaximumWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        scroll.setMaximumHeight(480)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        sections = [
            ("Normal Cards", deck.normal),
            ("Treasure Cards", deck.tc),
            ("Item Cards", deck.item),
        ]

        any_section = False
        for section_name, cards in sections:
            if not cards:
                continue
            any_section = True
            header = QLabel(section_name)
            header.setStyleSheet(f"font-weight: bold; color: {THEME.text};")
            layout.addWidget(header)

            for name, count in sorted(cards.items()):
                row = QLabel(f"  {name} × {count}")
                row.setStyleSheet(f"color: {THEME.text};")
                row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                layout.addWidget(row)

        if not any_section:
            empty = QLabel("Deck is empty.")
            empty.setStyleSheet(f"color: {THEME.text_muted};")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(empty)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == event.Type.StyleChange:
            # Re-apply inline stylesheets by rebuilding would be heavy;
            # THEME tokens are read at construction time so a theme switch
            # while this dialog is open leaves colours slightly stale.
            # Acceptable — the dialog is transient.
            pass
