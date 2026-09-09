"""Combat tab: the Run-mode surface for a bot's combat configuration.

WHAT THIS TAB IS FOR
--------------------

Picking, and reading what was picked. It holds the run-time truth -- which strategy is
active, which fights it covers, per-client deck state -- and it no longer holds an
editor. There is no disclosure arrow into a DSL box on any card here: combat editing is
a user-owned edit that wants an explicit Save/Cancel boundary, and it lives in
``CombatStrategyDialog``. ``Change…`` on a card opens that dialog in pick mode; ``Edit…``
opens the same window on the same strategy in manage mode.

TWO SECTIONS, NOT THREE AND A CAPTION
-------------------------------------

**Bot Combat Strategy** is which of the user's setups for this bot is live -- someone
who usually runs four wizards and someone who solos want different ones, and a
``BotCombatStrategy`` is the thing that differs. **Duel Combat Strategies** is what that
setup fights with: one card per strategy, each naming the fights it covers.

That second section used to be two sections and a bare caption -- "Default Duel Combat
Strategy" over a single card, "Specialized Duel Combat Strategies" over the cards for
fights that opted out, and "Using the bot default" over one muted row per fight that had
not. Three headings for a list whose every entry is the same sentence: *this strategy,
these fights*. The default is now the first card in the one list, wearing a locked
``Default`` badge on its pill row, and what told the levels apart in headings is told by
a badge on the card the heading was describing.

The badge is also what keeps that card from ever looking empty. Of the 68 shipped bots
that fight at all, 49 expose no combat state -- ``use_default_strategy: false`` is an
author's opt-in -- so on those the section is exactly one card with a badge and no fight
pills, which is the honest shape of their combat configuration. The add-card is what is
hidden there instead: with no fight to assign, a second strategy could never be given
one.

EVERYTHING IS A CARD
--------------------

Every row on this tab is a ``CardFrame`` -- the same frame, hover, verb list and
right-click menu as the launch profile cards and the player cards, which is what the
tab was conspicuously not built from. What that replaces, per section: a ``QPushButton``
styled to look like a search field, two 1pt-smaller muted lines, and a ``⋮`` button
opening a hand-built ``QMenu``, repeated once per level and once per fight. Verbs are
data now (``CardVerb``), so which buttons a card has is answered by reading its
``_verbs`` rather than its layout, and everything is reachable by right-click whether or
not it earned a place on the face.

Including the fights that opted out of nothing: they are pills on the default card,
which is a ``StrategyPanel`` in ``PanelRole.DEFAULT`` rather than a widget of its own.
On a freshly made Bot Combat Strategy those fights were once the whole section -- nothing
configured yet, so every exposed fight drew a row saying so, under the tab's last bare
caption. Condensing them is the move the section already makes for fights that share a
strategy, applied to the one they share by default.

The section ends with the same dashed ``AddCard`` the launch columns end with, and it is
the second way in. Its flow is two chained dialogs -- pick a strategy, then pick its
fights -- because a card holding a strategy and no fights forms no group, draws nothing
on the next rebuild and is pruned by ``_prune_orphans`` on the next save; it cannot be
left on screen to be filled in later. A single fight is still quicker through its own
pill on the default card. The add-card is what pays when several are to share one
strategy.

One consequence to keep in mind: there is no ``⋮`` button any more, here or on any other
card in the app. A verb that is not on the face is in the right-click menu, which is the
convention ``card_frame.py`` sets out and the reason the destructive ones can stay off
the face at all.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import TYPE_CHECKING
from uuid import uuid4

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DeckData,
    DuelCombatStrategy,
    DuelConfig,
    PlayerCombatStrategy,
    StateAssignment,
)
from bartlebot.combat_grouping import (
    apply_membership,
    build_bindings,
    group_bindings,
    state_label,
)
from bartlebot.combat_warnings import exposed_combat_states
from bartlebot.gui.combat_strategies.bot_strategy_picker import BotStrategyPicker
from bartlebot.gui.combat_strategies.dialog import CombatStrategyDialog, DialogMode
from bartlebot.gui.deck_content import deck_content_lines
from bartlebot.gui.sidebar.sidebar_section import (
    SidebarSection,
)
from bartlebot.gui.sidebar.strategy_panel import (
    BUILT_IN_LABEL,
    CUSTOM_LABEL,
    LOCKED,
    MISSING_LABEL,
    NOT_CONFIGURED_LABEL,
    STATE_INHERIT_LABEL,
    STATE_INHERIT_META,
    PanelRole,
    StrategyPanel,
)
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.add_card import AddCard
from bartlebot.gui.widgets.card_frame import CardFrame, CardMode, CardVerb
from bartlebot.gui.widgets.elided_label import ElidedLabel
from bartlebot.gui.widgets.picker_popup import PickerRow, Section
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.settings_manager import SettingsManager
from bartlebot.strategy_store import (
    StrategyKind,
    delete_bot_strategy,
    list_bot_strategies,
    list_duel_strategies,
    list_player_strategies,
    save_bot_strategy,
    save_duel_strategy,
)
from bartlebot.strategy_summary import summarize_bot
from bartlebot.strategy_workset import StrategyWorkingSet

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig, StateModel

logger = logging.getLogger(__name__)

_NUM_PLAYER_SLOTS = 4
_NO_STRATEGY_LABEL = "(no strategy set)"
_ICON_BTN_SIZE = 16

#: Section headings. Two, where there were three plus a bare caption: the levels below
#: the Bot Combat Strategy are all Duel Combat Strategies, and which one is the default
#: is a fact about a *card* — its ``Default`` badge — rather than about a region of the
#: sidebar. See the module docstring.
_BCS_SECTION = "Bot Combat Strategy"
_STRATEGIES_SECTION = "Duel Combat Strategies"

#: The section's terminator, in the AddCard the launch columns end with.
_ADD_STRATEGY = "Add a strategy for specific fights"

#: Verb labels for the Bot Combat Strategy card, shared with its menu and the tests.
_CHANGE_BCS = "Change…"
_RENAME_BCS = "Rename…"
_NEW_BCS = "New…"
_DUPLICATE_BCS = "Duplicate…"
_DELETE_BCS = "Delete…"


# ---------------------------------------------------------------------------
# Player Decks section
# ---------------------------------------------------------------------------


#: Label, and the stored value it means, for a deck's three-state archmastery override.
#: Module-level because the card offers them as verbs and the card body names the one in
#: force, and those two have to agree on the words.
_ARCHMASTERY_MODES = (
    ("Use the app setting", None),
    ("Always set it", True),
    ("Never set it", False),
)

#: Verb labels for a deck card, shared with its menu and the tests.
_IMPORT_DECK = "Import from client"
_APPLY_DECK = "Apply to client"
_SAVE_DECK = "Save to Library…"
_LOAD_DECK = "Load from Library…"
_CLEAR_DECK = "Clear deck"
_NO_DECK = "No deck saved"


class _DeckCard(CardFrame):
    """One player slot's saved deck: what is in it, and the six things you can do to it.

    Was a row of two ``QPushButton``\\s and a ``⋮`` -- eight text buttons across four
    slots, and by some distance the loudest thing on the tab. The verbs are the same
    verbs; they are icons on the face and labels in the right-click menu now, which is
    the language every other card in the app speaks and is what the section costs to
    read rather than what it can do.

    A body click unfolds the card list, which is the only non-destructive thing there is
    to do to a deck that is not one of the verbs -- and it is refused outright on a slot
    with no deck, there being nothing to unfold.

    The archmastery override is three menu verbs rather than a checkable submenu, and the
    setting in force is a line *in the card* instead. A ``CardVerb`` carries no check
    mark, but that is not why: which of the three is active is a fact about the deck, and
    a fact about the deck belongs where the rest of them are, not behind two hovers.
    """

    FRAME_NAME = "DeckCard"

    import_requested = pyqtSignal()
    apply_requested = pyqtSignal()
    save_to_library_requested = pyqtSignal()
    load_from_library_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    archmastery_mode_changed = pyqtSignal(object)  # True / False / None

    def __init__(self, slot: int, parent: QWidget | None = None) -> None:
        super().__init__(parent, mode=CardMode.MANAGE)
        self._slot = slot
        self._deck: DeckData | None = None
        self._editable = True
        self._has_client = False
        self._expanded = False
        self._build()
        self.activated.connect(self._toggle_expanded)
        self.refresh_theme_styles()

    def _build(self) -> None:
        layout = self._build_frame("decks", draggable=False)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self._name = QLabel(f"P{self._slot}")
        font = self._name.font()
        font.setBold(True)
        self._name.setFont(font)
        header.addWidget(self._name, 0)
        # Elided rather than wrapped, for the reason StrategyPanel's captions are.
        self._summary = ElidedLabel(_NO_DECK)
        header.addWidget(self._summary, 1)
        self._add_face_verbs(header)
        layout.addLayout(header)

        # "Archmastery: Fire · always set". Hidden unless the deck names a school, a
        # deck without one having nothing for the override to act on.
        self._archmastery = ElidedLabel()
        self._archmastery.setVisible(False)
        layout.addWidget(self._archmastery)

        # The card list. Built once and refilled, never created on first expand, so
        # toggling is never the first time this widget is shown -- see gui/CLAUDE.md on
        # showing a widget before it has a parent.
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 4, 0, 0)
        self._content_layout.setSpacing(2)
        self._content.setVisible(False)
        layout.addWidget(self._content)

    # -- data -----------------------------------------------------------

    @property
    def slot(self) -> int:
        return self._slot

    @property
    def is_expanded(self) -> bool:
        return self._expanded

    @property
    def has_deck(self) -> bool:
        return self._deck is not None and not self._deck.is_empty()

    def set_deck(self, deck: DeckData | None) -> None:
        """Redraw from *deck*, keeping the fold state unless there is nothing left to fold.

        Called after every import, apply, save, load and clear -- far more often than
        anyone folds a card -- so an unfolded card stays unfolded, or the disclosure would
        read as broken rather than persistent.
        """
        self._deck = deck
        if self.has_deck:
            # Treasure cards are never captured or applied, so the summary reports only
            # what a deck apply will actually restore.
            parts = []
            if deck.normal:
                parts.append(f"{sum(deck.normal.values())} normal")
            if deck.item:
                parts.append(f"{sum(deck.item.values())} item")
            self._summary.setText(" · ".join(parts) if parts else "Empty deck")
        else:
            self._summary.setText(_NO_DECK)
            self.set_expanded(False)

        school = deck.archmastery_school if deck is not None else None
        if school:
            mode = next(label for label, value in _ARCHMASTERY_MODES if value is deck.apply_archmastery)
            self._archmastery.setText(f"Archmastery: {school} · {mode.lower()}")
        self._archmastery.setVisible(bool(school))

        if self._expanded:
            self._rebuild_content()
        self._reapply_verb_states()
        self.refresh_theme_styles()

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._reapply_verb_states()

    def set_has_client(self, has_client: bool) -> None:
        """Whether a game client is hooked for this slot -- what Import and Apply need."""
        self._has_client = has_client
        self._reapply_verb_states()

    # -- folding --------------------------------------------------------

    def set_expanded(self, expanded: bool) -> None:
        expanded = expanded and self.has_deck
        self._expanded = expanded
        if expanded:
            self._rebuild_content()
        self._content.setVisible(expanded)

    def _toggle_expanded(self) -> None:
        self.set_expanded(not self._expanded)

    def _rebuild_content(self) -> None:
        while (item := self._content_layout.takeAt(0)) is not None:
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if self._deck is None:
            return
        for line in deck_content_lines(self._deck):
            css = "font-weight: bold;" if line.bold else ""
            row_lbl = ThemedTextLabel(line.text, color="text", css=css)
            row_lbl.setWordWrap(True)
            self._content_layout.addWidget(row_lbl)
            row_lbl.show()

    # -- verbs ----------------------------------------------------------

    def _verbs(self) -> list[CardVerb]:
        verbs = [
            CardVerb(_IMPORT_DECK, "download", self._import_tooltip(), self.import_requested.emit, on_face=True),
            CardVerb(_APPLY_DECK, "upload", self._apply_tooltip(), self.apply_requested.emit, on_face=True),
            CardVerb(
                _SAVE_DECK, "save_done", "Save this deck to the Deck Library", self.save_to_library_requested.emit
            ),
            CardVerb(
                _LOAD_DECK, "copy", "Replace this deck from the Deck Library", self.load_from_library_requested.emit
            ),
        ]
        if self._deck is not None and self._deck.archmastery_school:
            for label, value in _ARCHMASTERY_MODES:
                verbs.append(
                    CardVerb(
                        f"Archmastery: {label.lower()}",
                        "",
                        f"When applying this deck, {label.lower()} for {self._deck.archmastery_school}",
                        partial(self.archmastery_mode_changed.emit, value),
                    )
                )
        if self.has_deck:
            # Off the face: the body click one icon away unfolds a card list, and a trash
            # beside it is the mis-click card_frame.py keeps destructive verbs out of.
            verbs.append(
                CardVerb(_CLEAR_DECK, "trash", f"Forget the saved deck for P{self._slot}", self.clear_requested.emit)
            )
        return verbs

    def _import_tooltip(self) -> str:
        """What Import would do, or the one reason it currently would not.

        A disabled control that keeps its enabled tooltip tells the user what the button
        would do but never why it won't -- which is the actual question.
        """
        if not self._editable:
            return LOCKED
        if not self._has_client:
            return f"No game client connected for P{self._slot}"
        return "Snapshot the deck from this game client"

    def _apply_tooltip(self) -> str:
        if not self._editable:
            return LOCKED
        if not self._has_client:
            return f"No game client connected for P{self._slot}"
        if not self.has_deck:
            return f"No deck saved for P{self._slot} — use Import first"
        return "Push the saved deck to this game client now"

    def _reapply_verb_states(self) -> None:
        for label, enabled, tooltip in (
            (_IMPORT_DECK, self._editable and self._has_client, self._import_tooltip()),
            (_APPLY_DECK, self._editable and self._has_client and self.has_deck, self._apply_tooltip()),
        ):
            button = self.action_button(label)
            if button is not None:
                button.setEnabled(enabled)
                button.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.has_deck else Qt.CursorShape.ArrowCursor)
        self.setToolTip("" if self._editable else LOCKED)

    def _refuses_activation(self) -> bool:
        """Nothing to unfold on a slot with no deck, so the body has no verb there."""
        return not self.has_deck

    def _restyle_content(self) -> None:
        for label, color in ((self._name, THEME.text), (self._summary, THEME.text_muted)):
            palette = label.palette()
            palette.setColor(label.foregroundRole(), QColor(color))
            label.setPalette(palette)
        palette = self._archmastery.palette()
        palette.setColor(self._archmastery.foregroundRole(), QColor(THEME.text_muted))
        self._archmastery.setPalette(palette)


class _PlayerDecksSection(SidebarSection):
    """Collapsible section holding one ``_DeckCard`` per player slot.

    Signals carry the slot, so the tab never has to ask a card which one it is: each
    card's own slot-less signal is re-emitted here with the number bound.
    """

    import_requested = pyqtSignal(int)  # 1-based slot number
    apply_requested = pyqtSignal(int)  # 1-based slot number
    save_to_library_requested = pyqtSignal(int)  # 1-based slot number
    load_from_library_requested = pyqtSignal(int)  # 1-based slot number
    clear_requested = pyqtSignal(int)  # 1-based slot number
    # (slot, True/False/None) — the deck's three-state archmastery override.
    archmastery_mode_changed = pyqtSignal(int, object)

    def __init__(self, parent=None):
        super().__init__("Player Decks", parent)

        layout = QVBoxLayout()
        layout.setSpacing(4)
        self._cards: list[_DeckCard] = []
        self._client_count = 0

        for slot in range(1, _NUM_PLAYER_SLOTS + 1):
            card = _DeckCard(slot)
            card.import_requested.connect(partial(self.import_requested.emit, slot))
            card.apply_requested.connect(partial(self.apply_requested.emit, slot))
            card.save_to_library_requested.connect(partial(self.save_to_library_requested.emit, slot))
            card.load_from_library_requested.connect(partial(self.load_from_library_requested.emit, slot))
            card.clear_requested.connect(partial(self.clear_requested.emit, slot))
            card.archmastery_mode_changed.connect(partial(self._on_archmastery, slot))
            layout.addWidget(card)
            self._cards.append(card)

        self.set_content_layout(layout)

    def _on_archmastery(self, slot: int, mode: object) -> None:
        self.archmastery_mode_changed.emit(slot, mode)

    def card(self, slot: int) -> _DeckCard:
        """The card for a 1-based slot. Public so the tab and the tests can read one."""
        return self._cards[slot - 1]

    def set_row_expanded(self, slot: int, expanded: bool) -> None:
        """Fold or unfold one slot's card list, exactly as a body click would."""
        self.card(slot).set_expanded(expanded)

    def set_editable(self, editable: bool) -> None:
        for card in self._cards:
            card.set_editable(editable)

    def set_client_count(self, count: int) -> None:
        self._client_count = count
        for card in self._cards:
            card.set_has_client(card.slot <= count)

    def refresh(self, player_decks: dict[int, DeckData]) -> None:
        for card in self._cards:
            card.set_deck(player_decks.get(card.slot))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _DefaultBinding:
    """Adapts BotCombatStrategy's ``default_strategy_id`` / ``default_duel_config``
    fields to the same shape as ``StateAssignment`` — ``duel_config``,
    ``duel_combat_strategy_id`` (settable), ``is_linked`` — so one ``StrategyPanel``
    class draws both the bot default and every per-fight override.
    """

    def __init__(self, bcs: BotCombatStrategy):
        self._bcs = bcs

    @property
    def duel_config(self) -> DuelConfig:
        return self._bcs.default_duel_config

    @duel_config.setter
    def duel_config(self, value: DuelConfig) -> None:
        self._bcs.default_duel_config = value

    @property
    def duel_combat_strategy_id(self) -> str | None:
        return self._bcs.default_strategy_id

    @duel_combat_strategy_id.setter
    def duel_combat_strategy_id(self, value: str | None) -> None:
        self._bcs.default_strategy_id = value

    @property
    def is_linked(self) -> bool:
        return self._bcs.default_strategy_id is not None


# ---------------------------------------------------------------------------
# Bot Combat Strategy card
# ---------------------------------------------------------------------------


class _BotStrategyCard(CardFrame):
    """Which of the user's setups for this bot is live, and the five things you can
    do to that list.

    A ``BotCombatStrategy`` is not a duel config, so this is not a ``StrategyPanel``:
    it names a container -- a default plus a set of per-fight overrides plus four
    decks -- rather than describing what anyone will cast. What it shares is the frame,
    which is the point: three levels drawn as three different kinds of widget was most
    of why this tab did not read as one surface.

    Three verbs reach the face and two do not. Duplicate is rare, and Delete is
    destructive next to a body that changes the live configuration -- the rule
    ``card_frame.py`` states for a trash icon 22px from a body click. Both are in the
    right-click menu, which is also the keyboard route.
    """

    FRAME_NAME = "BotStrategyCard"

    change_requested = pyqtSignal()
    new_requested = pyqtSignal()
    rename_requested = pyqtSignal()
    duplicate_requested = pyqtSignal()
    delete_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, mode=CardMode.MANAGE)
        self._bcs: BotCombatStrategy | None = None
        self._editable = True
        self._build()
        self.activated.connect(self.change_requested)
        self.refresh_theme_styles()

    def _build(self) -> None:
        layout = self._build_frame("strategies", draggable=False)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self._name = ElidedLabel(_NO_STRATEGY_LABEL)
        font = self._name.font()
        font.setBold(True)
        self._name.setFont(font)
        header.addWidget(self._name, 1)
        self._add_face_verbs(header)
        layout.addLayout(header)

        self._meta = ElidedLabel()
        layout.addWidget(self._meta)
        self._meta.setVisible(False)

    def set_strategy(self, bcs: BotCombatStrategy | None) -> None:
        self._bcs = bcs
        if bcs is None:
            self._name.setText(_NO_STRATEGY_LABEL)
            self._name.setToolTip("No Bot Combat Strategy for this bot yet — right-click to make one")
            self._meta.setVisible(False)
        else:
            self._name.setText(bcs.name)
            summary = summarize_bot(bcs)
            self._name.setToolTip(summary.description or bcs.name)
            self._meta.setText(summary.subtitle_line())
            self._meta.setVisible(True)
        self._reapply_verb_states()
        self.refresh_theme_styles()

    @property
    def strategy(self) -> BotCombatStrategy | None:
        """The strategy this card names, or None. Public so the tab need not re-derive it."""
        return self._bcs

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._reapply_verb_states()
        self.setCursor(Qt.CursorShape.PointingHandCursor if editable else Qt.CursorShape.ArrowCursor)
        self.setToolTip("" if editable else LOCKED)

    def _reapply_verb_states(self) -> None:
        """Rename needs something to rename; New does not.

        New stays live with no strategy loaded, because that is exactly the state a user
        gets out of by making one -- a card whose every affordance is dead is a dead end.
        """
        for label in (_RENAME_BCS,):
            button = self.action_button(label)
            if button is not None:
                button.setEnabled(self._editable and self._bcs is not None)
        button = self.action_button(_NEW_BCS)
        if button is not None:
            button.setEnabled(self._editable)
        button = self.action_button(_CHANGE_BCS)
        if button is not None:
            button.setEnabled(self._editable and self._bcs is not None)

    def _verbs(self) -> list[CardVerb]:
        verbs = [
            CardVerb(_CHANGE_BCS, "search", "Pick a different Bot Combat Strategy", self.change_requested.emit, True),
            CardVerb(_RENAME_BCS, "pencil", "Rename this Bot Combat Strategy", self.rename_requested.emit, True),
            CardVerb(_NEW_BCS, "plus", "Create a new Bot Combat Strategy for this bot", self.new_requested.emit, True),
        ]
        if self._bcs is not None:
            verbs.append(
                CardVerb(_DUPLICATE_BCS, "copy", "Copy this strategy under a new name", self.duplicate_requested.emit)
            )
            verbs.append(CardVerb(_DELETE_BCS, "trash", "Delete this Bot Combat Strategy", self.delete_requested.emit))
        return verbs

    def _refuses_activation(self) -> bool:
        """A body click picks, and there is nothing to pick between until one exists."""
        return not self._editable or self._bcs is None

    def _restyle_content(self) -> None:
        for label, color in ((self._name, THEME.text), (self._meta, THEME.text_muted)):
            palette = label.palette()
            palette.setColor(label.foregroundRole(), QColor(color))
            label.setPalette(palette)


# ---------------------------------------------------------------------------
# Combat Tab
# ---------------------------------------------------------------------------


class CombatTab(QWidget):
    """Left-sidebar tab for combat configuration using the three-level strategy system."""

    combat_config_changed = pyqtSignal()
    import_deck_requested = pyqtSignal(int)  # 1-based slot number
    apply_deck_requested = pyqtSignal(int)  # 1-based slot number

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: BotConfig | None = None
        self._bcs: BotCombatStrategy | None = None
        self._all_bcs: list[BotCombatStrategy] = []
        self._all_dcs: list[DuelCombatStrategy] = []
        self._all_pcs: list[PlayerCombatStrategy] = []
        # state_id → its StateAssignment (owned in-memory; flushed to BCS on save,
        # dropped if it never ends up linked or holding real inline content)
        self._state_bindings: dict[str, StateAssignment] = {}
        self._state_panels: dict[str, StrategyPanel] = {}
        # A card made by the add-card and not yet given any fights. It exists only so the
        # membership popup has something to anchor to, and it is dropped unsaved if that
        # popup closes with nothing chosen — see ``_on_add_strategy``.
        self._pending_panel: StrategyPanel | None = None
        self._suppress = False
        self._editable = True

        # No debounce timer. There was one, at 500ms, because the inline DSL editor
        # emitted ``changed`` on every keystroke and every keystroke would otherwise have
        # written a strategy file. With the editor gone, every remaining way this tab can
        # change the configuration is a single discrete act — a pick accepted from a
        # dialog, a membership committed, a deck loaded — so each one saves immediately
        # and there is no pending state for a bot switch, a Save As or a quit to race.

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(8, 8, 8, 8)
        self._content_layout.setSpacing(4)

        # ── Bot Combat Strategy ──────────────────────────────────────
        bcs_section = SidebarSection(_BCS_SECTION, self)
        bcs_layout = QVBoxLayout()
        bcs_layout.setSpacing(2)
        self._bcs_card = _BotStrategyCard()
        self._bcs_card.change_requested.connect(self._on_pick_bcs)
        self._bcs_card.new_requested.connect(self._on_new_bcs)
        self._bcs_card.rename_requested.connect(self._on_rename_bcs)
        self._bcs_card.duplicate_requested.connect(self._on_duplicate_bcs)
        self._bcs_card.delete_requested.connect(self._on_delete_bcs)
        bcs_layout.addWidget(self._bcs_card)
        bcs_section.set_content_layout(bcs_layout)
        self._content_layout.addWidget(bcs_section)

        # ── Duel Combat Strategies ───────────────────────────────────
        # One section for both levels. The default card is first and always present; the
        # cards for fights that opted out follow it; the AddCard ends the list. All three
        # of those are persistent widgets, so ``_rebuild_state_assignments`` re-places
        # them rather than recreating them — a live click target must not be destroyed
        # mid-gesture, and the add-card's popup is a gesture that spans a rebuild.
        self._strategies_section = SidebarSection(_STRATEGIES_SECTION, self)
        self._strategies_layout = QVBoxLayout()
        self._strategies_layout.setSpacing(6)

        # The bot default is the last stop before the built-in strategy, so its empty
        # label names the built-in one. The specialized cards below say something
        # different — see STATE_INHERIT_LABEL.
        self._default_panel = StrategyPanel(
            BUILT_IN_LABEL, NOT_CONFIGURED_LABEL, parent=self._strategies_section, role=PanelRole.DEFAULT
        )
        self._default_panel.pick_requested.connect(partial(self._on_pick_duel, self._default_panel))
        self._default_panel.promote_requested.connect(partial(self._on_promote, self._default_panel))
        self._default_panel.edit_in_library_requested.connect(self._on_edit_dcs_in_library)
        self._default_panel.fight_pick_requested.connect(self._on_pick_for_fight)
        self._default_panel.setVisible(False)

        # Hidden outright when the bot exposes no combat state, which is 49 of the 68
        # shipped bots that fight at all: with nothing to assign, an add-card offers a
        # strategy that could never be given a fight. ``use_default_strategy: false`` is
        # the bot author's opt-in, and nothing on this tab can set it.
        self._add_card = AddCard(_ADD_STRATEGY, parent=self._strategies_section)
        self._add_card.add_requested.connect(self._on_add_strategy)
        self._add_card.setVisible(False)

        self._strategies_section.set_content_layout(self._strategies_layout)
        self._content_layout.addWidget(self._strategies_section)
        self._strategies_section.setVisible(False)

        # ── Player Decks ─────────────────────────────────────────────
        self._decks_section = _PlayerDecksSection(self)
        self._decks_section.import_requested.connect(self.import_deck_requested)
        self._decks_section.apply_requested.connect(self.apply_deck_requested)
        self._decks_section.save_to_library_requested.connect(self._on_save_deck_to_library)
        self._decks_section.load_from_library_requested.connect(self._on_load_deck_from_library)
        self._decks_section.clear_requested.connect(self._on_clear_deck)
        self._decks_section.archmastery_mode_changed.connect(self._on_archmastery_mode_changed)
        self._content_layout.addWidget(self._decks_section)

        self._content_layout.addStretch()

        scroll_area.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll_area)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self._rebind()

    def rebind_identity(self) -> None:
        """Re-resolve the strategy display against the config's *current* id.

        Save As mints a fresh id onto the loaded config, and every surface keyed
        on the old one is stale the moment it does: the card goes on naming the
        original's strategy, and the next combat edit writes
        ``set_active_strategy`` under the *new* id — binding the fork to the
        original's strategy, which is exactly what minting an id prevents.
        A fork inherits no strategy, so this leaves the tab showing none.

        Narrower than ``set_config``, which also rebinds the whole tab: nothing
        here is ever mid-edit, so there is nothing to commit before the id
        changes.
        """
        self._rebind()

    def _rebind(self) -> None:
        """Reload the strategy for ``self._config`` and redraw everything on it."""
        self._suppress = True
        self._load_bcs()
        self._rebuild_state_assignments()
        self._refresh_deck_summaries()
        self._bcs_card.set_strategy(self._bcs)
        self._refresh_default_panel()
        self._suppress = False

    def refresh_deck_summaries(self) -> None:
        self._refresh_deck_summaries()

    def update_deck_slot(self, slot: int, deck: DeckData) -> None:
        """Update the in-memory BCS deck for one slot and refresh the display."""
        if self._bcs is not None:
            self._bcs.player_decks[slot] = deck
        self._refresh_deck_summaries()

    def refresh_duel_profiles(self) -> None:
        """Compatibility alias — refreshes state assignments when combat tab becomes visible."""
        self._rebuild_state_assignments()

    def refresh_strategy_data(self) -> None:
        """Reload DCS/BCS/PCS data from disk, after the combat strategy dialog closes.

        Pushes the refreshed lists into the existing panel widgets rather than
        rebuilding them, so a rename made in the dialog reaches the cards without
        destroying and recreating them.
        """
        self._all_dcs = list_duel_strategies()
        self._all_pcs = list_player_strategies()
        if self._config is not None:
            self._all_bcs = list_bot_strategies(self._config.id)
        self._bcs_card.set_strategy(self._bcs)
        self._default_panel.set_libraries(self._all_dcs, self._all_pcs)
        for panel in self._group_panels():
            panel.set_libraries(self._all_dcs, self._all_pcs)

    def _group_panels(self) -> list[StrategyPanel]:
        """Each panel once — ``_state_panels`` maps every fight in a group to the
        same widget, so its values repeat."""
        return list(dict.fromkeys(self._state_panels.values()))

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._bcs_card.set_editable(editable)
        self._decks_section.set_editable(editable)
        self._default_panel.set_editable(editable)
        self._add_card.setEnabled(editable)
        for panel in self._group_panels():
            panel.set_editable(editable)

    def set_client_count(self, count: int) -> None:
        self._decks_section.set_client_count(count)

    # ------------------------------------------------------------------
    # BCS loading / saving
    # ------------------------------------------------------------------

    def _load_bcs(self) -> None:
        """Load BCS from disk based on active strategy setting."""
        self._bcs = None
        self._state_bindings = {}
        self._all_bcs = []
        self._all_dcs = list_duel_strategies()
        self._all_pcs = list_player_strategies()
        if self._config is None:
            return
        self._all_bcs = list_bot_strategies(self._config.id)
        active_id = SettingsManager.instance().get_active_strategy(self._config.id)
        if active_id:
            self._bcs = next((b for b in self._all_bcs if b.id == active_id), None)
        if self._bcs is None and self._all_bcs:
            # Fall back to first available BCS
            self._bcs = self._all_bcs[0]
            if active_id != self._bcs.id:
                SettingsManager.instance().set_active_strategy(self._config.id, self._bcs.id)
        if self._bcs is not None:
            self._state_bindings = build_bindings(self._bcs.state_assignments)

    def _ensure_state_binding(self, state_id: str) -> StateAssignment:
        """Return this state's binding, creating an empty in-memory one if absent.

        Never persisted until it actually links a strategy or holds a non-empty
        inline config — see ``_save_bcs``.
        """
        existing = self._state_bindings.get(state_id)
        if existing is not None:
            return existing
        fresh = StateAssignment(state_ids=[state_id])
        self._state_bindings[state_id] = fresh
        return fresh

    def _save_bcs(self) -> None:
        if self._bcs is None:
            return
        # Only persist bindings that actually say something — an untouched
        # placeholder (unlinked, empty) is indistinguishable from no binding at
        # all, so writing it out would just be silent clutter in the file.
        # dict keyed by id() in case a loaded file grouped several state_ids
        # under one shared StateAssignment object.
        kept = {id(a): a for a in self._state_bindings.values() if a.is_linked or not a.duel_config.is_empty()}
        self._bcs.state_assignments = self._prune_orphans(list(kept.values()))
        try:
            save_bot_strategy(self._bcs)
        except Exception:
            logger.exception("Failed to save BotCombatStrategy %s", self._bcs.id)
        self.combat_config_changed.emit()

    def _prune_orphans(self, assignments: list[StateAssignment]) -> list[StateAssignment]:
        """Drop state ids that no longer exist in the bot, and any assignment left empty.

        Deleting a node used to leave its assignment behind forever, because
        bindings are seeded from the strategy file and never checked against the
        bot's actual states.  Those orphans are not merely clutter: state ids are
        recycled, so a stale assignment lies in wait for whatever node is created
        next and hands it a duel strategy nobody asked for.  ``_unique_id`` no
        longer recycles ids, which closes that door for new bots; this closes it
        for strategy files written before that change.

        Only runs when a config is loaded — with no bot to compare against,
        every assignment would look orphaned.
        """
        if self._config is None:
            return assignments
        live_ids = {s.id for s in self._config.states}
        pruned: list[StateAssignment] = []
        for assignment in assignments:
            kept_ids = [sid for sid in assignment.state_ids if sid in live_ids]
            if not kept_ids:
                logger.info(
                    "Dropping combat assignment for deleted state(s) %s from strategy %s",
                    ", ".join(assignment.state_ids),
                    self._bcs.id if self._bcs else "?",
                )
                continue
            if kept_ids != assignment.state_ids:
                logger.info(
                    "Dropping deleted state(s) %s from a combat assignment in strategy %s",
                    ", ".join(sid for sid in assignment.state_ids if sid not in live_ids),
                    self._bcs.id if self._bcs else "?",
                )
                assignment.state_ids = kept_ids
            pruned.append(assignment)
        return pruned

    def open_combat_dialog(
        self,
        kind: StrategyKind = StrategyKind.DUEL,
        mode: DialogMode = DialogMode.MANAGE,
        current_id: str | None = None,
        allow_none: bool = True,
        none_label: str = "(built-in default)",
    ) -> CombatStrategyDialog | None:
        """Run the combat strategy dialog. Returns it if accepted, else None.

        Modal, and given a working set loaded fresh each time. Modal because the
        dialog owns a Save boundary over the same files this tab reads, and a
        non-modal window editing them alongside the tab is how the two used to
        disagree about what was on disk. A reload afterwards is unconditional:
        Cancel writes nothing, so re-reading costs a few files and removes the
        question of whether it was needed.
        """
        ws = StrategyWorkingSet.from_store()
        dlg = CombatStrategyDialog(
            ws,
            kind=kind,
            mode=mode,
            current_id=current_id,
            allow_none=allow_none,
            none_label=none_label,
            parent=self,
        )
        accepted = bool(dlg.exec())
        self.refresh_strategy_data()
        return dlg if accepted else None

    # ------------------------------------------------------------------
    # UI refresh
    # ------------------------------------------------------------------

    def _refresh_default_panel(self) -> None:
        if self._bcs is None:
            self._default_panel.setVisible(False)
            return
        self._default_panel.load(_DefaultBinding(self._bcs), self._all_dcs, self._all_pcs)
        self._default_panel.set_editable(self._editable)

    def _refresh_deck_summaries(self) -> None:
        decks = self._bcs.player_decks if self._bcs else {}
        self._decks_section.refresh(decks)

    def _on_save_deck_to_library(self, slot: int) -> None:
        from bartlebot.bot.strategy_models import DeckPreset  # noqa: PLC0415
        from bartlebot.strategy_store import save_deck_preset  # noqa: PLC0415

        if self._bcs is None:
            return
        deck = self._bcs.player_decks.get(slot)
        if deck is None or deck.is_empty():
            QMessageBox.information(self, "No deck", f"No deck is saved for P{slot}.")
            return
        name, ok = QInputDialog.getText(self, "Save to Deck Library", "Preset name:")
        if not ok or not name.strip():
            return
        preset = DeckPreset(name=name.strip(), deck=deck.model_copy(deep=True))
        # Written on the spot rather than through the dialog's working set: a
        # capture is a discrete act with nothing to cancel, and the deck itself
        # came from the game, not from anything the user could still be typing.
        save_deck_preset(preset)

    def _on_load_deck_from_library(self, slot: int) -> None:
        from bartlebot.strategy_store import list_deck_presets  # noqa: PLC0415

        if not list_deck_presets():
            QMessageBox.information(
                self, "Deck Library empty", "No deck presets saved yet. Use Import and Save to Library first."
            )
            return
        dlg = self.open_combat_dialog(kind=StrategyKind.DECK, mode=DialogMode.PICK, allow_none=False)
        if dlg is None:
            return
        chosen_id = dlg.selected_id()
        if chosen_id is None:
            return
        # Re-read rather than reaching into the dialog's working set: the dialog
        # committed on the way out, so the file is the authority, and a preset
        # renamed or edited in the same visit is already reflected there.
        chosen = next((p for p in list_deck_presets() if p.id == chosen_id), None)
        if chosen is None or self._bcs is None:
            return
        self._bcs.player_decks[slot] = chosen.deck.model_copy(deep=True)
        save_bot_strategy(self._bcs)
        self._refresh_deck_summaries()

    def _on_clear_deck(self, slot: int) -> None:
        if self._bcs is None:
            return
        deck = self._bcs.player_decks.get(slot)
        if deck is None or deck.is_empty():
            return
        ans = QMessageBox.question(
            self,
            "Clear Deck",
            f"Clear the saved deck for P{slot}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self._bcs.player_decks.pop(slot, None)
        save_bot_strategy(self._bcs)
        self._refresh_deck_summaries()

    def _on_archmastery_mode_changed(self, slot: int, mode: object) -> None:
        """Pin (or unpin) this deck's answer to "set the archmastery school".

        ``mode`` is True, False, or None for "follow the app setting". Saved
        immediately rather than debounced: it is a single discrete choice made
        from a menu, not a field someone is still typing into.
        """
        if self._bcs is None:
            return
        deck = self._bcs.player_decks.get(slot)
        if deck is None or deck.apply_archmastery is mode:
            return
        deck.apply_archmastery = mode
        save_bot_strategy(self._bcs)
        self._refresh_deck_summaries()

    def _rebuild_state_assignments(self) -> None:
        """Full rebuild: destroys and recreates one panel per *group* of fights.

        A Duel Strategy is drawn once however many fights use it — several
        panels backed by one ``StateAssignment`` was the old arrangement, and it
        meant editing any of them silently edited all of them while the others
        went on showing stale summaries.

        Only call this when the set of exposed states, or the way they group, may
        have changed (config load, a strategy relinked, membership edited) — it
        destroys existing panel widgets.  Data-only refreshes go through
        ``refresh_strategy_data`` / panel.set_libraries instead.

        The whole section is hidden when there is no Bot Combat Strategy to describe.
        It is *not* hidden merely because the bot exposes no fight: the default card
        still names the strategy every one of that bot's fights will use, which is 49
        of the 68 shipped bots that fight at all. Only the add-card goes, there being
        nothing for a second strategy to be assigned to.

        Fights with no strategy of their own get no panel: they are pills on the default
        card, which is why ``_state_panels`` maps only the configured ones.
        """
        while self._strategies_layout.count():
            item = self._strategies_layout.takeAt(0)
            widget = item.widget() if item else None
            # The default card and the add-card outlive the rebuild — re-placed below.
            if widget is not None and widget not in (self._default_panel, self._add_card):
                widget.deleteLater()
        self._state_panels.clear()
        # Any half-made card was just deleted with the rest.
        self._pending_panel = None

        self._strategies_section.setVisible(self._bcs is not None)
        # First, and always: everything below it is an exception to it.
        self._strategies_layout.addWidget(self._default_panel)

        exposed = exposed_combat_states(self._config) if self._config is not None else []
        for state in exposed:
            self._ensure_state_binding(state.id)
        groups = group_bindings(self._state_bindings, exposed)

        for group in groups:
            if not group.is_configured:
                continue
            panel = self._make_group_panel(group.assignment, exposed, group.states)
            panel.membership_changed.connect(partial(self._on_membership_changed, group.assignment))
            self._strategies_layout.addWidget(panel)
            # Keyed per state, so every fight in a group resolves to its panel.
            for state in group.states:
                self._state_panels[state.id] = panel

        # An unconfigured group is always exactly one fight — ``split_unconfigured`` sees
        # to that — so this is one pill per unassigned fight, in FSM order.
        self._default_panel.set_fights(
            [(s.id, state_label(s)) for g in groups if not g.is_configured for s in g.states]
        )

        self._strategies_layout.addWidget(self._add_card)
        self._add_card.setVisible(bool(exposed))

    def _make_group_panel(
        self, assignment: StateAssignment, exposed: list[StateModel], states: list[StateModel]
    ) -> StrategyPanel:
        """One specialized card, wired to everything but its membership.

        Membership is left to the caller because the two callers want different things
        from it: a rebuilt card commits straight through ``_on_membership_changed``,
        while a card the add-card just made has to be dropped again if its popup closes
        empty.
        """
        panel = StrategyPanel(
            STATE_INHERIT_LABEL,
            STATE_INHERIT_META,
            parent=self._strategies_section,
            role=PanelRole.SPECIALIZED,
        )
        panel.set_fights([(s.id, state_label(s)) for s in states])
        panel.load(assignment, self._all_dcs, self._all_pcs)
        panel.set_fight_options(self._fight_sections(exposed), list(assignment.state_ids))
        panel.set_editable(self._editable)
        panel.pick_requested.connect(partial(self._on_pick_duel, panel))
        panel.promote_requested.connect(partial(self._on_promote, panel))
        panel.edit_in_library_requested.connect(self._on_edit_dcs_in_library)
        return panel

    # ------------------------------------------------------------------
    # Duel Combat Strategy picking
    # ------------------------------------------------------------------

    def _on_pick_duel(self, panel: StrategyPanel) -> None:
        """Choose which library strategy a panel points at — the tab's central act.

        The dialog is owned here rather than by the panel, because accepting one
        can merge two panels into one (two fights that now name the same
        strategy) and only the tab can regroup and redraw them.  The panel just
        says *pick*.
        """
        binding = panel.binding
        if binding is None:
            return
        if self._pick_into(binding, panel.empty_label):
            self._after_linkage_change(panel)

    def _on_pick_for_fight(self, state_id: str) -> None:
        """Give one fight a strategy of its own — a click on its pill in ``BotDefaultPanel``.

        The same act ``_on_pick_duel`` performs for a card, for a fight that has no card
        yet: it is the fight that leaves the bot-default pills and arrives as a card of
        its own, so this always regroups and rebuilds rather than redrawing one panel.
        """
        if self._config is None:
            return
        binding = self._ensure_state_binding(state_id)
        if not self._pick_into(binding, STATE_INHERIT_LABEL):
            return
        self._regroup_bindings()
        self._rebuild_state_assignments()
        self._save_bcs()

    def _on_add_strategy(self) -> None:
        """Give some fights a strategy of their own — the add-card at the foot of the list.

        Two dialogs, chained, and the chaining is what keeps the invariant that a card on
        screen is a card in the file. A ``StateAssignment`` with no ``state_ids`` forms no
        group, so it would draw no card on the next rebuild, and ``_prune_orphans`` drops
        it outright on the next save: an empty card cannot be left lying about for the
        user to fill in later, because nothing would bring it back. So the strategy is
        picked first, the card is made, its own membership popup opens on it immediately,
        and the card is dropped again if that popup closes with nothing chosen.

        The pill route on the default card is the other way in, and stays the shorter one
        for a single fight. This is the one that pays when several fights are to share.
        """
        if self._config is None or self._bcs is None or not self._editable:
            return
        # No "none" option: picking nothing here would make a card that says nothing,
        # which is what the default card already is.
        dlg = self.open_combat_dialog(kind=StrategyKind.DUEL, mode=DialogMode.PICK, allow_none=False)
        if dlg is None:
            return
        chosen = dlg.selected_id()
        if not chosen:
            return

        exposed = exposed_combat_states(self._config)
        assignment = StateAssignment(state_ids=[], duel_combat_strategy_id=chosen)
        panel = self._make_group_panel(assignment, exposed, [])
        panel.membership_changed.connect(partial(self._on_pending_membership, assignment, panel))
        # Where the rebuild would have put it: above the add-card, below the rest.
        self._strategies_layout.insertWidget(self._strategies_layout.count() - 1, panel)
        self._pending_panel = panel
        # Deferred a turn: the popup positions itself against the button's place on
        # screen, which the layout has not given it yet on the card inserted just above.
        QTimer.singleShot(0, panel.open_fights_popup)

    def _on_pending_membership(self, assignment: StateAssignment, panel: StrategyPanel, chosen_ids: list) -> None:
        """Commit the add flow, or undo it — see ``_on_add_strategy``."""
        if self._pending_panel is not panel:
            return
        self._pending_panel = None
        if not chosen_ids or self._config is None:
            self._strategies_layout.removeWidget(panel)
            panel.deleteLater()
            return
        # Moves each chosen fight out of whatever holds it, the default card included.
        apply_membership(self._state_bindings, assignment, list(chosen_ids))
        self._regroup_bindings()
        self._rebuild_state_assignments()
        self._save_bcs()

    def _pick_into(self, binding, none_label: str) -> bool:
        """Run the picker against *binding*, and say whether it was accepted.

        Shared by the card path and the pill path so both offer the same dialog with the
        same "none" row — what a binding falls through to is a property of the binding's
        level, not of which widget the click landed on.
        """
        dlg = self.open_combat_dialog(
            kind=StrategyKind.DUEL,
            mode=DialogMode.PICK,
            current_id=binding.duel_combat_strategy_id,
            allow_none=True,
            none_label=none_label,
        )
        if dlg is None:
            return False
        binding.duel_combat_strategy_id = dlg.selected_id()
        return True

    def _on_promote(self, panel: StrategyPanel) -> None:
        """Copy a binding's inline config into a new library entry and link to it.

        Only reachable for a binding that already holds one, which since the
        inline editor was removed means a configuration written before this tab
        stopped offering to write them, or one authored in a bot file by hand.
        Saving it as a preset is the way such a configuration becomes editable
        again — in the combat strategy dialog, which is now the only place a
        ``DuelConfig`` is edited.
        """
        binding = panel.binding
        if binding is None or binding.duel_config.is_empty():
            QMessageBox.information(self, "Nothing to save", "This entry has no inline configuration to save.")
            return
        name, ok = QInputDialog.getText(self, "Save as Preset", "Duel Combat Strategy name:")
        if not ok or not name.strip():
            return
        # Dump over the DuelConfig field set (rather than listing fields by
        # hand) so a field added later can't be silently dropped here.
        dcs = DuelCombatStrategy(name=name.strip(), **binding.duel_config.model_dump())
        save_duel_strategy(dcs)
        self._all_dcs.append(dcs)
        binding.duel_combat_strategy_id = dcs.id
        self._after_linkage_change(panel)
        self.refresh_strategy_data()

    def _after_linkage_change(self, panel: StrategyPanel) -> None:
        """A panel now names a different strategy: redraw, regroup, save.

        The bot default has no group of its own, so it only redraws.  A
        per-fight panel may have just joined or left a group, which is a rebuild
        of the whole section.
        """
        if panel is self._default_panel:
            panel.refresh()
        else:
            self._regroup_bindings()
            self._rebuild_state_assignments()
        self._save_bcs()

    def _regroup_bindings(self) -> None:
        """Re-merge bindings that now share a Duel Strategy.

        Linking a fight to a strategy another panel already uses leaves two
        objects naming one strategy; ``build_bindings`` folds them together so
        the next rebuild draws a single row, which is the whole point.
        """
        distinct = list({id(a): a for a in self._state_bindings.values()}.values())
        self._state_bindings = build_bindings(distinct)

    def _fight_sections(self, exposed: list[StateModel]) -> list[Section]:
        """Every exposed fight as a popup row, each saying what it uses today.

        The right-hand slot is the whole reason this is a popup rather than a list of
        bare names: checking a fight here *moves* it out of whatever group holds it, and
        ``meta`` is what makes that consequence visible before the click rather than
        after it. One section, unnamed -- the fights of one bot are not a vocabulary
        with groups in it.
        """
        rows = [
            PickerRow(key=state.id, label=state_label(state), meta=self._describe_state_binding(state.id))
            for state in exposed
        ]
        return [("", rows)]

    def _on_membership_changed(self, assignment: StateAssignment, chosen_ids: list) -> None:
        """Commit a group's new membership, once its popup has closed.

        Deliberately not per toggle: this rebuilds the whole section, which would
        destroy the card holding the popup the user is still clicking in. See
        ``PickerButton.committed``.
        """
        if self._config is None or list(assignment.state_ids) == list(chosen_ids):
            return
        apply_membership(self._state_bindings, assignment, list(chosen_ids))
        # Re-asserts both invariants before anything is drawn from the result.
        self._regroup_bindings()
        self._rebuild_state_assignments()
        self._save_bcs()

    def _describe_state_binding(self, state_id: str) -> str:
        binding = self._state_bindings.get(state_id)
        if binding is None:
            return STATE_INHERIT_LABEL
        return self._describe_binding_of(binding)

    def _describe_binding_of(self, binding: StateAssignment) -> str:
        """The same words the panel's own name line uses, so the dialog reads as
        the same thing — the constants come from ``strategy_panel`` for that reason."""
        if binding.is_linked:
            dcs = next((d for d in self._all_dcs if d.id == binding.duel_combat_strategy_id), None)
            return dcs.name if dcs is not None else MISSING_LABEL
        if binding.duel_config.is_empty():
            return STATE_INHERIT_LABEL
        return CUSTOM_LABEL

    def _on_edit_dcs_in_library(self, dcs_id: str) -> None:
        self.open_combat_dialog(kind=StrategyKind.DUEL, mode=DialogMode.MANAGE, current_id=dcs_id)

    # ------------------------------------------------------------------
    # BCS picker / menu actions
    # ------------------------------------------------------------------

    def _on_pick_bcs(self) -> None:
        if self._config is None:
            return
        dlg = BotStrategyPicker(
            strategies=self._all_bcs,
            current_id=self._bcs.id if self._bcs else None,
            allow_none=False,
            parent=self,
        )
        if dlg.exec():
            chosen = dlg.selected_id()
            if chosen:
                self._bcs = next((b for b in self._all_bcs if b.id == chosen), None)
                self._state_bindings = build_bindings(self._bcs.state_assignments) if self._bcs else {}
                SettingsManager.instance().set_active_strategy(self._config.id, chosen)
                self._rebuild_state_assignments()
                self._bcs_card.set_strategy(self._bcs)
                self._refresh_default_panel()
                self._refresh_deck_summaries()

    def _on_new_bcs(self) -> None:
        if self._config is None:
            return
        name, ok = QInputDialog.getText(self, "New Bot Strategy", "Name:")
        if not ok or not name.strip():
            return
        bcs = BotCombatStrategy(name=name.strip(), bot_id=self._config.id)
        save_bot_strategy(bcs)
        self._all_bcs.append(bcs)
        self._bcs = bcs
        self._state_bindings = {}
        SettingsManager.instance().set_active_strategy(self._config.id, bcs.id)
        self._rebuild_state_assignments()
        self._bcs_card.set_strategy(self._bcs)
        self._refresh_default_panel()
        self._refresh_deck_summaries()

    def _on_rename_bcs(self) -> None:
        if self._bcs is None:
            return
        name, ok = QInputDialog.getText(self, "Rename Strategy", "Name:", text=self._bcs.name)
        if not ok or not name.strip():
            return
        self._bcs.name = name.strip()
        self._save_bcs()
        self._bcs_card.set_strategy(self._bcs)

    def _on_duplicate_bcs(self) -> None:
        if self._bcs is None or self._config is None:
            return
        name, ok = QInputDialog.getText(self, "Duplicate Strategy", "New name:", text=f"{self._bcs.name} copy")
        if not ok or not name.strip():
            return
        new_bcs = self._bcs.model_copy(deep=True, update={"id": str(uuid4()), "name": name.strip()})
        save_bot_strategy(new_bcs)
        self._all_bcs.append(new_bcs)
        self._bcs = new_bcs
        self._state_bindings = build_bindings(new_bcs.state_assignments)
        SettingsManager.instance().set_active_strategy(self._config.id, new_bcs.id)
        self._rebuild_state_assignments()
        self._bcs_card.set_strategy(self._bcs)
        self._refresh_default_panel()

    def _on_delete_bcs(self) -> None:
        if self._bcs is None or self._config is None:
            return
        ans = QMessageBox.question(
            self,
            "Delete Bot Strategy",
            f"Delete '{self._bcs.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        to_delete = self._bcs
        self._all_bcs = [b for b in self._all_bcs if b.id != to_delete.id]
        delete_bot_strategy(to_delete.id)
        self._bcs = self._all_bcs[0] if self._all_bcs else None
        self._state_bindings = build_bindings(self._bcs.state_assignments) if self._bcs else {}
        new_active = self._bcs.id if self._bcs else None
        SettingsManager.instance().set_active_strategy(self._config.id, new_active)
        self._rebuild_state_assignments()
        self._bcs_card.set_strategy(self._bcs)
        self._refresh_default_panel()
        self._refresh_deck_summaries()

    # ------------------------------------------------------------------
    # Theme / lifecycle
    # ------------------------------------------------------------------

    # No closeEvent, and nothing to flush on quit any more. This tab lives inside a
    # QTabWidget and is never closed as a window, so a closeEvent would never fire;
    # and every edit it can make is a discrete act that writes on the spot, while
    # everything the combat strategy dialog holds is written by its own Save.

    # No changeEvent either. Every widget this tab owns now repaints itself on a theme
    # switch -- the cards because CardFrame does, a deck card's content rows because
    # ThemedTextLabel does -- so there is nothing left here for a StyleChange to chase.
