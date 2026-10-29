"""A deck's archmastery override, as a control rather than a line of text.

The sidebar's deck card *reports* this — two icons, or the sentence they replace — and
offers the three modes as menu verbs on a card whose face has no room for them. The Deck
Presets page has a form's worth of room and is the surface where a saved deck is
actually configured, so here the three modes are on screen at once, as radios, and the
icons sit above them saying what the deck captured.

WHAT IS EDITABLE, AND WHAT IS NOT
    The *school* is not. It is read off the wizard at import time, exactly like the card
    list, and there is no sensible way to type it that beats capturing it — a school the
    deck never held is a claim about a wizard nobody has. What the user owns is whether
    applying this deck should set it, which is the three-state
    :data:`~bartlebot.combat.deck_content.ARCHMASTERY_MODES` value.

THREE RADIOS, NOT A TICK BOX
    "Use app setting" is a real third answer and the shipped default, so a two-state
    control cannot express what the deck stores. This is the same reasoning
    ``BoolChoice`` states for a bool on its own in a form: under a heading, a lone box
    captioned "Set archmastery" reads as though the other answers were unavailable.

THE CAPTION ONLY APPEARS FOR THE LEVEL THAT DELEGATES
    With a mode pinned, the radio already says what will happen and a sentence
    underneath would be the same fact twice. With "Use app setting" chosen the deck has
    deliberately not answered, and what the app setting currently says is the one thing
    the user cannot see from here — the same four-level idiom the Option Values tab's source
    captions follow.

A deck with no school saved gets no radios at all: there is nothing for them to act on,
and "apply the school" cannot mean "clear it" (see ``resolve_apply_archmastery``). The
absence is explained in a line rather than left as an empty region.
"""

from __future__ import annotations

from bartleby.qt.art_signal import follow_art
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from bartleby.shared.strategy_models import DeckData
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QRadioButton, QVBoxLayout, QWidget

from bartlebot.combat.deck_content import ARCHMASTERY_MODES, archmastery_note
from bartlebot.combat.deck_io import resolve_apply_archmastery
from bartlebot.gui.widgets.cards.archmastery_badge import ArchmasteryBadge

#: What the caption says the app setting will do, for a deck that defers to it.
APP_SETTING_ON = "App setting: the school is set when this deck is applied."
APP_SETTING_OFF = "App setting: the school is left alone when this deck is applied."

#: Said in place of the controls when the deck captured no school.
NO_SCHOOL = "No archmastery school was captured with this deck, so there is nothing to set."


class ArchmasteryEditor(QWidget):
    """The deck's archmastery school, and the user's answer about applying it."""

    #: The override was changed on the loaded deck — the deck object is already updated.
    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._deck: DeckData | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # The icons where they have been extracted, and the school in words either way.
        # The medallion names the school only while the apply will set it -- it is the
        # universal mark otherwise -- so without the word beside it a deck set to "Never
        # set" would not say anywhere which school it is holding.
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self._icons = ArchmasteryBadge(parent=self)
        head.addWidget(self._icons, 0)
        self._school = ThemedTextLabel("", color="text", parent=self)
        head.addWidget(self._school, 1)
        layout.addLayout(head)

        self._modes = QWidget(self)
        modes_row = QHBoxLayout(self._modes)
        modes_row.setContentsMargins(0, 0, 0, 0)
        modes_row.setSpacing(12)
        self._group = QButtonGroup(self)
        self._buttons: list[tuple[QRadioButton, bool | None]] = []
        for label, value in ARCHMASTERY_MODES:
            button = QRadioButton(label, self._modes)
            self._group.addButton(button)
            modes_row.addWidget(button, 0)
            self._buttons.append((button, value))
        modes_row.addStretch()
        self._group.buttonClicked.connect(self._on_mode_clicked)
        layout.addWidget(self._modes)

        self._caption = ThemedTextLabel("", parent=self)
        self._caption.setWordWrap(True)
        layout.addWidget(self._caption)
        # The icons or the school alone, decided by what resolved: a reload decides again.
        follow_art(self, lambda: self.load(self._deck))

    # -- reading --------------------------------------------------------

    @property
    def deck(self) -> DeckData | None:
        """The deck being edited, or None. This is the object the radios write to."""
        return self._deck

    def mode(self) -> bool | None:
        """The override the radios currently show: True, False, or None for the setting."""
        for button, value in self._buttons:
            if button.isChecked():
                return value
        return None

    @property
    def caption_text(self) -> str:
        """What the caption reads — empty while the deck pins its own answer."""
        return self._caption.text()

    # -- filling --------------------------------------------------------

    def load(self, deck: DeckData | None) -> None:
        """Show *deck*'s archmastery, or nothing at all for no deck."""
        self._deck = deck
        has_school = deck is not None and bool(deck.archmastery_school)
        if deck is None:
            self._school.setText("")
            self._icons.set_archmastery(None, False)
        elif has_school:
            self._school.setText(deck.archmastery_school)
            will_set, _source = resolve_apply_archmastery(deck)
            self._icons.set_archmastery(deck.archmastery_school, will_set, archmastery_note(deck))
        else:
            self._school.setText(NO_SCHOOL)
            self._icons.set_archmastery(None, False)

        self._icons.setVisible(has_school and self._icons.has_icons())
        self._school.setVisible(deck is not None)
        self._modes.setVisible(has_school)
        self._sync_buttons()
        self._refresh_caption()

    def clear(self) -> None:
        self.load(None)

    def _sync_buttons(self) -> None:
        stored = self._deck.apply_archmastery if self._deck is not None else None
        for button, value in self._buttons:
            # Blocked: setChecked on a radio does not emit ``buttonClicked``, but keeping
            # the two paths symmetrical costs nothing and a later change to a signal that
            # does fire would otherwise write to the deck while loading it.
            blocked = button.blockSignals(True)
            button.setChecked(value is stored)
            button.blockSignals(blocked)

    def _refresh_caption(self) -> None:
        """Say what the app setting will do, and only while the deck defers to it."""
        if self._deck is None or not self._deck.archmastery_school or self._deck.apply_archmastery is not None:
            self._caption.setText("")
            self._caption.setVisible(False)
            return
        will_set, _source = resolve_apply_archmastery(self._deck)
        self._caption.setText(APP_SETTING_ON if will_set else APP_SETTING_OFF)
        self._caption.setVisible(True)

    # -- editing --------------------------------------------------------

    def _on_mode_clicked(self, button: QRadioButton) -> None:
        """Write the clicked mode onto the deck. Re-picking the stored one writes nothing.

        Nothing here reaches disk: the deck this holds is the working copy the combat
        strategy dialog's Save commits, and its dirtiness is derived from the fields
        rather than reported — see :mod:`bartlebot.combat.strategy_workset`. Which is also why
        a click that changes nothing must not emit: it would count as a change the user
        would then be warned about losing.
        """
        if self._deck is None:
            return
        value = next((v for b, v in self._buttons if b is button), None)
        if self._deck.apply_archmastery is value:
            return
        self._deck.apply_archmastery = value
        # The medallion follows the resolved answer, not the stored one.
        will_set, _source = resolve_apply_archmastery(self._deck)
        self._icons.set_archmastery(self._deck.archmastery_school, will_set, archmastery_note(self._deck))
        self._icons.setVisible(self._icons.has_icons())
        self._refresh_caption()
        self.changed.emit()
