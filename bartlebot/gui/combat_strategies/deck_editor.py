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

from bartlebot.bot.strategy_models import DeckData, DeckPreset
from bartlebot.gui.combat_strategies.deck_link import DeckClientLink
from bartlebot.gui.deck_content import APPLY_DECK, IMPORT_DECK
from bartlebot.gui.widgets.action_button import ActionButton
from bartlebot.gui.widgets.archmastery_editor import ArchmasteryEditor
from bartlebot.gui.widgets.deck_card_list import DeckCardList
from bartlebot.gui.widgets.player_picker import pick_player, picker_label
from bartlebot.gui.widgets.rule_heading import RuleHeading
from bartlebot.gui.widgets.school_picker import SchoolPickerButton

#: Said in place of the card list for a preset holding nothing. A preset *can* be empty:
#: ``+ New`` makes one before anything has been captured into it.
EMPTY_DECK = f"(empty deck — use {IMPORT_DECK} to fill it)"


class DeckPresetEditor(QWidget):
    """Edit a DeckPreset's schools and archmastery answer; show what it holds."""

    #: An edit landed on the loaded preset — for whoever draws the card list.
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._preset: DeckPreset | None = None
        self._link: DeckClientLink | None = None
        self._editable = True
        self._busy = False

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
        # The Combat tab's deck verbs on the library's copy: Import replaces the cards with
        # the ones a hooked client holds now, Apply puts them on a client.
        client_row = QHBoxLayout()
        self._import_btn = ActionButton("download", IMPORT_DECK, fill=False)
        self._import_btn.clicked.connect(self._on_import)
        client_row.addWidget(self._import_btn)
        self._apply_btn = ActionButton("upload", APPLY_DECK, fill=False)
        self._apply_btn.clicked.connect(self._on_apply)
        client_row.addWidget(self._apply_btn)
        client_row.addStretch()
        layout.addLayout(client_row)
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

    @property
    def import_button(self) -> ActionButton:
        """Import from client. Public for the tests."""
        return self._import_btn

    @property
    def apply_button(self) -> ActionButton:
        """Apply to client. Public for the tests."""
        return self._apply_btn

    def set_link(self, link: DeckClientLink | None) -> None:
        """Hand in the way to the game clients; without one the buttons stay grayed."""
        self._link = link
        self.refresh_clients()

    def refresh_clients(self) -> None:
        """Re-read how many clients are hooked: the buttons' state, and the ▾ that says a click asks which."""
        self._refresh_buttons()

    def showEvent(self, event) -> None:  # type: ignore[override]
        # A client can hook while the dialog is open, and nothing announces it.
        self._refresh_buttons()
        super().showEvent(event)

    def _clients(self) -> int:
        return self._link.client_count() if self._link is not None else 0

    def _refresh_buttons(self) -> None:
        clients = self._clients()
        has_client = clients > 0
        deck = self._preset.deck if self._preset is not None else None
        has_cards = deck is not None and not deck.is_empty()
        self._import_btn.setText(picker_label(IMPORT_DECK, clients))
        self._apply_btn.setText(picker_label(APPLY_DECK, clients))
        self._import_btn.setEnabled(has_client and self._editable and self._preset is not None and not self._busy)
        self._apply_btn.setEnabled(has_client and has_cards and not self._busy)
        if not has_client:
            why = "No game client connected"
        elif self._busy:
            why = "A deck job is running"
        else:
            why = ""
        self._import_btn.setToolTip(why or "Replace this deck's cards with the deck a client holds now")
        self._apply_btn.setToolTip(why or ("Push this deck to a client now" if has_cards else "This deck has no cards"))

    def _on_import(self) -> None:
        if self._link is None or self._preset is None:
            return
        pick_player(self._import_btn, self._clients(), "Import", self._import_from)

    def _import_from(self, index: int) -> None:
        if self._link is None or self._preset is None:
            return
        preset = self._preset
        if self._link.import_deck(index + 1, lambda result: self._import_done(preset, result)):
            self._busy = True
            self._refresh_buttons()

    def _import_done(self, preset: DeckPreset, result: DeckData | str) -> None:
        self._busy = False
        if isinstance(result, DeckData) and preset is self._preset:
            # Whether applying the deck sets archmastery is a decision, not a capture, so
            # it outlives the capture it sits beside.
            result.apply_archmastery = preset.deck.apply_archmastery
            preset.deck = result
            self.load(preset)
            self.changed.emit()
        else:
            self._refresh_buttons()

    def _on_apply(self) -> None:
        if self._link is None or self._preset is None:
            return
        pick_player(self._apply_btn, self._clients(), "Apply", self._apply_to, preposition="to")

    def _apply_to(self, index: int) -> None:
        if self._link is None or self._preset is None:
            return
        if self._link.apply_deck(index + 1, self._preset.deck.model_copy(deep=True), self._apply_done):
            self._busy = True
            self._refresh_buttons()

    def _apply_done(self, _failure: str | None) -> None:
        # The controller reports a failure, or a deck that went on changed; this wakes the buttons.
        self._busy = False
        self._refresh_buttons()

    def set_editable(self, editable: bool) -> None:
        """Read-only in the dialog's Pick mode, which selects and edits nothing (#123)."""
        self._editable = editable
        self._school_picker.setEnabled(editable)
        self._archmastery.setEnabled(editable)
        self._refresh_buttons()

    def load(self, preset: DeckPreset) -> None:
        self._preset = None  # so setting the picker below is not taken for an edit
        self._school_picker.set_value(preset.schools)
        self._preset = preset
        self._archmastery.load(preset.deck)
        self._cards.set_deck(preset.deck)
        self.refresh_clients()

    def clear(self) -> None:
        self._preset = None
        self._school_picker.set_value([])
        self._archmastery.clear()
        self._cards.set_deck(None)
        self._refresh_buttons()

    def _on_schools_changed(self) -> None:
        if self._preset is None:
            return
        self._preset.schools = self._school_picker.value()
        self.changed.emit()
