"""Editor for a DeckPreset: its schools and archmastery override, over a read-only card list.

A deck preset's *contents* are captured from the game, not typed — there is no
sensible way to author "4 × Storm Blade" by hand that is better than pressing
Capture with the deck already set up. So the card list is shown, never edited.

What is editable is the one thing about a saved deck that is a decision rather than a
capture: whether applying it should also set the wizard's archmastery school. That was
reachable only from the sidebar deck card's menu, which meant a preset in the library
could not be configured in the place it is configured — it had to be loaded into a
player slot first. :class:`~bartlebot.gui.widgets.archmastery_editor.ArchmasteryEditor`
is that control, and the school itself stays read-only for the same reason the cards do.

The schools the deck is built for (#174) are the other decision, and are authored here the
way a player strategy's are, with the same picker: empty means any school. They are not the
archmastery school, which is the wizard's at capture time and is shown below them.

The identity fields are not here: name and description moved up into the detail pane's
shared *About* section — see
:class:`~bartlebot.gui.combat_strategies.dialog.AboutSection`.

Like its siblings in this package, it writes through to the working copy it was
handed and never touches disk; the 500ms rename debounce it inherited from the
Strategy Library window is gone with the rest of the autosaves. See
:mod:`bartlebot.strategy_workset`.

No scroll area of its own, either. The page around this one scrolls, and a nested
scroll area inside it is the thing ``dialog.py`` says it never wants: two bars for one
column of rows, the inner one appearing at whatever height the outer one happens to
give this widget.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from bartlebot.bot.strategy_models import DeckPreset
from bartlebot.gui.widgets.archmastery_editor import ArchmasteryEditor
from bartlebot.gui.widgets.deck_card_list import DeckCardList
from bartlebot.gui.widgets.rule_heading import RuleHeading
from bartlebot.gui.widgets.school_picker import SchoolPickerButton

#: Said in place of the card list for a preset holding nothing. A preset *can* be empty:
#: ``+ New`` makes one before anything has been captured into it.
EMPTY_DECK = "(empty deck — capture one from a client to fill it)"


class DeckPresetEditor(QWidget):
    """Edit a DeckPreset's schools and archmastery answer; show what it holds."""

    #: An edit landed on the loaded preset — for whoever draws the card list.
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._preset: DeckPreset | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        school_row = QHBoxLayout()
        school_row.addWidget(QLabel("Schools:"))
        self._school_picker = SchoolPickerButton()
        self._school_picker.setToolTip("The schools this deck is built for, if any — descriptive only")
        self._school_picker.selection_changed.connect(self._on_schools_changed)
        school_row.addWidget(self._school_picker)
        school_row.addStretch()
        layout.addLayout(school_row)

        layout.addWidget(RuleHeading("Archmastery", self))
        self._archmastery = ArchmasteryEditor(parent=self)
        self._archmastery.changed.connect(self.changed)
        layout.addWidget(self._archmastery)

        layout.addWidget(RuleHeading("Cards", self))
        # The same rows the sidebar's deck card unfolds into, art and all — see
        # ``widgets/deck_card_list.py``. Treasure cards are omitted there, and
        # deliberately: never captured and never applied, so listing them would
        # misrepresent what the preset restores.
        self._cards = DeckCardList(empty_text=EMPTY_DECK, parent=self)
        layout.addWidget(self._cards)

        # Keeps both regions at the top of a pane the outer scroll area stretches.
        layout.addStretch()

    def preset(self) -> DeckPreset | None:
        """The working copy being edited, or None."""
        return self._preset

    @property
    def school_picker(self) -> SchoolPickerButton:
        """The schools control. Public so a test can pick a school as a user would."""
        return self._school_picker

    @property
    def archmastery(self) -> ArchmasteryEditor:
        """The archmastery control. Public because it is what the page's tests read."""
        return self._archmastery

    @property
    def cards(self) -> DeckCardList:
        """The card list. Public for the same reason."""
        return self._cards

    def set_editable(self, editable: bool) -> None:
        """Read-only in the dialog's Pick mode, which selects and edits nothing (#123)."""
        self._school_picker.setEnabled(editable)
        self._archmastery.setEnabled(editable)

    def load(self, preset: DeckPreset) -> None:
        self._preset = None  # so setting the picker below is not taken for an edit
        self._school_picker.set_value(preset.schools)
        self._preset = preset
        self._archmastery.load(preset.deck)
        self._cards.set_deck(preset.deck)

    def clear(self) -> None:
        self._preset = None
        self._school_picker.set_value([])
        self._archmastery.clear()
        self._cards.set_deck(None)

    def _on_schools_changed(self) -> None:
        if self._preset is None:
            return
        self._preset.schools = self._school_picker.value()
        self.changed.emit()
