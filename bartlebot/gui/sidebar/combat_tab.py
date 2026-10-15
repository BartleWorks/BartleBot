"""Combat tab: the Run-mode surface for a bot's combat configuration.

WHAT THIS TAB IS FOR
--------------------

Picking, and reading what was picked. It holds the run-time truth -- which strategy is
active, which fights it covers, per-client deck state -- and it no longer holds an
editor. There is no disclosure arrow into a DSL box on any card here: combat editing is
a user-owned edit that wants an explicit Save/Cancel boundary, and it lives in
``CombatStrategyDialog``. ``Change…`` (🔍) on a card opens that dialog in Pick mode,
which selects and edits nothing (#123); ``Edit…`` (✏) opens it as the active Bot
Combat Strategy's editor, on every card, one running a default included. A strategy made there
on that visit is linked to the card that opened it on Save (#118).

A BANNER, THEN THE SECTIONS
---------------------------

**Bot Combat Strategy** is which of the user's setups for this bot is live -- someone
who usually runs four wizards and someone who solos want different ones, and a
``BotCombatStrategy`` is the thing that differs. It is a banner pinned across the top of
the tab rather than a card in a folding section of its own (#119): a section is for a
region holding several distinct groups, and this one only ever holds the one pick. It is
also the thing every section below it belongs *to*, so it sits above them, outside the
scroll area, and never scrolls away from what it qualifies.

**Duel Combat Strategies** is what that setup fights with: one card per strategy, each
naming the fights it covers.

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

Every row on this tab is a ``CardFrame`` -- the banner included, which is a card drawn
full-bleed rather than a different kind of widget -- the same frame, hover, verb list and
right-click menu as the launch profile cards and the player cards, which is what the
tab was conspicuously not built from. What that replaces, per section: a ``QPushButton``
styled to look like a search field, two 1pt-smaller muted lines, and a ``⋮`` button
opening a hand-built ``QMenu``, repeated once per level and once per fight. Verbs are
data now (``CardVerb``), so which buttons a card has is answered by reading its
``_verbs`` rather than its layout, and everything is reachable by right-click whether or
not it earned a place on the face -- and, on the deck cards, by a kebab button that opens
the same menu without the mouse having to know right-click is a thing to try (see
``CardFrame._make_kebab_button``).

Including the fights that opted out of nothing: they are pills on the default card,
which is a ``StrategyPanel`` in ``PanelRole.DEFAULT`` rather than a widget of its own.
On a freshly made Bot Combat Strategy those fights were once the whole section -- nothing
configured yet, so every exposed fight drew a row saying so, under the tab's last bare
caption. Condensing them is the move the section already makes for fights that share a
strategy, applied to the one they share by default.

The section ends with the same dashed ``AddCard`` the launch columns end with, and it is
the second way in. It opens a menu of two -- choose an existing strategy, or create one
for this bot -- and either way the flow is two chained dialogs, the strategy and
then its fights, because a card holding a strategy and no fights forms no group, draws nothing
on the next rebuild and is pruned by ``_prune_orphans`` on the next save; it cannot be
left on screen to be filled in later. A single fight is still quicker through its own
pill on the default card. The add-card is what pays when several are to share one
strategy.

One consequence to keep in mind: there is no per-card hand-built ``QMenu`` any more,
here or anywhere else in the app. A verb that is not on the face is in the right-click
menu ``card_frame.py`` builds from the same ``_verbs`` list every time, which is the
reason the destructive ones can stay off the face at all -- a ``⋮`` button, where one is
built at all, opens that same menu rather than one of its own.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bartlebot.clients.credentials import CredentialStore
from bartlebot.combat.bot_combat_strategy import StrategyLookup, duplicate_bot_strategy, read_bot_names
from bartlebot.combat.bot_strategy_models import BotCombatStrategy, FightRef, StateAssignment
from bartlebot.combat.combat_grouping import (
    apply_membership,
    build_bindings,
    group_bindings,
    state_label,
)
from bartlebot.combat.combat_resolution import (
    BOT_STRATEGY_CAPTION,
    OFFICIAL_PILL,
    SourceText,
    defaults_in_use,
    describe_source,
)
from bartlebot.combat.deck_content import APPLY_DECK, ARCHMASTERY_MODES, IMPORT_DECK, archmastery_note, deck_groups
from bartlebot.combat.deck_io import resolve_apply_archmastery
from bartlebot.combat.fight_keys import exposed_combat_states, heal, place
from bartlebot.combat.flee_marks import FledFight, FleeMarks, fled_on_default, fled_text, fled_tooltip
from bartlebot.combat.strategy_store import (
    Origin,
    StrategyKind,
    delete_bot_strategy,
    list_bot_strategies,
    list_duel_strategies,
    list_player_strategies,
    load_bot_strategy,
    origin_of,
    save_bot_strategy,
)
from bartlebot.combat.strategy_summary import summarize_bot
from bartlebot.combat.strategy_workset import StrategyWorkingSet
from bartlebot.content import bot_roots
from bartlebot.gui.combat_strategies.bot_strategy_picker import BotStrategyPicker
from bartlebot.gui.combat_strategies.deck_link import DeckClientLink
from bartlebot.gui.combat_strategies.dialog import NONE_LABEL, NONE_TOOLTIP, CombatStrategyDialog, DialogMode
from bartlebot.gui.sidebar.player_card import STRIPE_WIDTH, account_stripe_keys
from bartlebot.gui.sidebar.sidebar_section import (
    SidebarSection,
)
from bartlebot.gui.sidebar.strategy_panel import (
    DEFAULTS_LABEL,
    DEFAULTS_META,
    DEFAULTS_NONE_TIP,
    LOCKED,
    MISSING_LABEL,
    STATE_INHERIT_LABEL,
    STATE_INHERIT_META,
    STATE_INHERIT_TIP,
    PanelRole,
    StrategyPanel,
)
from bartlebot.gui.theming.account_accent import account_accent
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.theming.themed import set_style_sheet
from bartlebot.gui.widgets.buttons.action_button import ActionButton
from bartlebot.gui.widgets.cards.add_card import AddCard
from bartlebot.gui.widgets.cards.archmastery_badge import ArchmasteryBadge
from bartlebot.gui.widgets.cards.card_art_tile import CardArtStrip
from bartlebot.gui.widgets.cards.card_frame import GRIP_GUTTER, CardFrame, CardMode, CardVerb
from bartlebot.gui.widgets.cards.deck_card_list import DeckCardList
from bartlebot.gui.widgets.labels.elided_label import ElidedLabel
from bartlebot.gui.widgets.labels.themed_label import ThemedTextLabel
from bartlebot.gui.widgets.layout.drag_reorder import DragReorder
from bartlebot.gui.widgets.layout.scroll_gutter import reserve_scrollbar_gutter
from bartlebot.gui.widgets.pickers.picker_popup import PickerRow, Section
from bartlebot.gui.widgets.pills.player_pills import PlayerBadge
from bartlebot.gui.widgets.pills.tag_pill import AccentPill, WarningPill
from bartlebot.nodes.timing import PLAYER_COLOR_REFRESH_INTERVAL
from bartlebot.settings_manager import SettingsManager
from bartlebot.shared.strategy_models import DeckData, DuelCombatStrategy, PlayerCombatStrategy

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig, StateModel

logger = logging.getLogger(__name__)

_NUM_PLAYER_SLOTS = 4
#: ``_binding_key`` for the bot default, which has no fights of its own to be named by.
_DEFAULT_BINDING = "\x00default"
_NO_STRATEGY_LABEL = "(no strategy set)"
_ICON_BTN_SIZE = 16

#: The banner's caption. Nothing heads the banner, so it names itself.
_BCS_CAPTION = BOT_STRATEGY_CAPTION

#: Section heading. One, where there were three plus a bare caption: the levels below
#: the Bot Combat Strategy are all Duel Combat Strategies, and which one is the default
#: is a fact about a *card* — its ``Default`` badge — rather than about a region of the
#: sidebar. See the module docstring.
_STRATEGIES_SECTION = "Duel Combat Strategies"

#: The section's terminator, in the AddCard the launch columns end with.
_ADD_STRATEGY = "Add another duel strategy"
#: The add-card menu's two items. The second does not name the Bot Combat Strategy it saves into:
#: the Bot Combat Strategy banner already does, two cards up.
_ADD_EXISTING = "Select existing duel strategy…"
_ADD_CREATED = "Create new duel strategy…"

#: Verb labels for the Bot Combat Strategy banner, shared with its menu and the tests.
_CHANGE_BCS = "Change…"
_RENAME_BCS = "Rename…"
_NEW_BCS = "New…"
_DUPLICATE_BCS = "Duplicate…"
_DELETE_BCS = "Delete…"
_EXPORT_BCS = "Export…"
#: The banner's two verbs while the bot has no Bot Combat Strategy (epic #201, Stage 5): make
#: one, or change the defaults its fights are using instead, in Settings › Combat.
_CREATE_BCS = "Create Bot Combat Strategy"
_CHANGE_DEFAULTS = "Change defaults…"
#: Forget a pick whose file is gone, the fault that otherwise stops every run (epic #201 review, R3).
_CLEAR_PICK = "Clear pick"
#: What the banner's flee mark says to do about the fights it names (Stage 7 of epic #201).
_FLED_BANNER_FIX = "Create Bot Combat Strategy to give these fights a strategy of their own."


# ---------------------------------------------------------------------------
# Player Decks section
# ---------------------------------------------------------------------------


#: Verb labels for a deck card, shared with its menu and the tests.
_IMPORT_DECK = IMPORT_DECK
_APPLY_DECK = APPLY_DECK
_SAVE_DECK = "Save to Library…"
_LOAD_DECK = "Load from Library…"
_CLEAR_DECK = "Clear deck"
_NO_DECK = "No deck saved"

#: The section's two whole-group verbs (#109), and what a verb says while a deck job runs.
_IMPORT_ALL = "Import all"
_APPLY_ALL = "Apply all"
_DECK_BUSY = "Working on a deck — wait for it to finish"


class _DeckCard(CardFrame):
    """One player slot's saved deck: what is in it, and the six things you can do to it.

    Was a row of two ``QPushButton``\\s and a ``⋮`` -- eight text buttons across four
    slots, and by some distance the loudest thing on the tab. The verbs are the same
    verbs; Import and Apply are icons on the face, and the rest -- Save, Load, the
    archmastery override, Clear -- are in a menu reached by right-click or by the kebab
    button beside them (see ``CardFrame._make_kebab_button``), which is the language
    every other card in the app speaks and is what the section costs to read rather than
    what it can do.

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
        # A deck import or apply is running somewhere in the section. Every deck verb waits
        # for it, because two jobs on one client drive the same deck window at once.
        self._busy = False
        self._expanded = False
        # The account in this slot right now, as a palette slot, or None for no stripe --
        # the same stripe a player card wears, so deck and player are matched by hue.
        self._color_key: int | None = None
        self._build()
        self.activated.connect(self._toggle_expanded)
        self.refresh_theme_styles()

    def _build(self) -> None:
        # A drag takes the deck to another player: the card is the deck, and its slot is
        # wherever it stands. See ``_PlayerDecksSection``.
        layout = self._build_frame("decks between players")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        # A pill, colored by the account in this slot, where a bold "P1" used to be (#40).
        self._name = PlayerBadge(self._slot)
        header.addWidget(self._name, 0, Qt.AlignmentFlag.AlignVCenter)
        # The deck's archmastery as the game's own two icons, beside the pill: the facts
        # about the deck run left to right from here, and the row's right-hand end stays
        # entirely things that can be clicked.
        self._archmastery_icons = ArchmasteryBadge(parent=self)
        self._archmastery_icons.setVisible(False)
        header.addWidget(self._archmastery_icons, 0, Qt.AlignmentFlag.AlignVCenter)
        # Elided rather than wrapped, for the reason StrategyPanel's captions are.
        self._summary = ElidedLabel(_NO_DECK)
        header.addWidget(self._summary, 1)
        self._add_face_verbs(header)
        header.addWidget(self._make_kebab_button("More deck actions"), 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        # The other half of the same fact, and exactly one of the two is ever up: the
        # icons on the header row where they have been extracted, this sentence where
        # they have not. A row of its own because it is a sentence; both hidden unless
        # the deck names a school, a deck without one having nothing for the override
        # to act on.
        self._archmastery = ElidedLabel()
        self._archmastery.setVisible(False)
        layout.addWidget(self._archmastery)

        # The collapsed card's card art: one tile per distinct card, wrapping, with a
        # rotated divider between Normal and Item. Hidden while the card is unfolded,
        # and on any install with no card art extracted -- the
        # summary line above says what is in the deck either way.
        self._art = CardArtStrip(parent=self)
        self._art.setVisible(False)
        layout.addWidget(self._art)

        # The card list -- the same rows, art and all, that the Deck Presets page in the
        # combat strategy dialog shows at rest. Built once and refilled, never created on
        # first expand, so toggling is never the first time this widget is shown -- see
        # gui/CLAUDE.md on showing a widget before it has a parent.
        #
        # No empty text: the body refuses to unfold a slot with no deck, so this list is
        # never reached empty.
        self._content = DeckCardList(margins=(0, 4, 0, 0), parent=self)
        self._content.setVisible(False)
        layout.addWidget(self._content)

    # -- data -----------------------------------------------------------

    @property
    def slot(self) -> int:
        return self._slot

    def set_slot(self, slot: int) -> None:
        """Stand in *slot*: the pill and every sentence naming the player follow it.

        A card is the deck, not the player -- a drag moves the deck to another player --
        so the slot is wherever the card stands, and the section says so as it moves.
        """
        if slot == self._slot:
            return
        self._slot = slot
        self._name.set_player(slot)
        self._reapply_verb_states()

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
        will_set = False
        note = ""
        if school:
            # The wording is ``deck_content``'s, shared with the Deck Presets page, so the
            # card and the surface that edits this cannot drift apart on what it says.
            note = archmastery_note(deck)
            self._archmastery.setText(note)
            # Resolved rather than stored, because the medallion only names the school
            # when the apply will actually set it -- see archmastery_badge.py.
            will_set, _source = resolve_apply_archmastery(deck)
        self._archmastery_icons.set_archmastery(school, will_set, note)
        # The icons where they exist, the sentence where they do not, never both.
        show_icons = bool(school) and self._archmastery_icons.has_icons()
        self._archmastery_icons.setVisible(show_icons)
        self._archmastery.setVisible(bool(school) and not show_icons)

        self._rebuild_art()
        if self._expanded:
            self._content.set_deck(self._deck)
        self._reapply_verb_states()
        self.refresh_theme_styles()

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._reapply_verb_states()

    def set_has_client(self, has_client: bool) -> None:
        """Whether a game client is hooked for this slot -- what Import and Apply need."""
        self._has_client = has_client
        self._reapply_verb_states()

    def set_busy(self, busy: bool) -> None:
        """Whether a deck job is running -- Import and Apply wait while one is."""
        self._busy = busy
        self._reapply_verb_states()

    @property
    def color_key(self) -> int | None:
        """The account palette slot this card is striped with, or None for no stripe."""
        return self._color_key

    def set_player_color(self, key: int | None, account_label: str = "") -> None:
        """Stripe the card and color its pill by the account in this slot, or None for neither."""
        self._name.set_color_key(key)
        self._name.set_account_label(account_label)
        if key == self._color_key:
            return
        self._color_key = key
        self.refresh_theme_styles()

    def _frame_extra_qss(self) -> str:
        """The account stripe down the left edge, exactly as ``PlayerCard`` draws it."""
        if self._color_key is None:
            return ""
        return f" border-left: {STRIPE_WIDTH}px solid {account_accent(self._color_key)};"

    # -- folding --------------------------------------------------------

    def set_expanded(self, expanded: bool) -> None:
        expanded = expanded and self.has_deck
        self._expanded = expanded
        if expanded:
            # No archmastery note in the list: the card's own header line already carries
            # it, and unfolding a card put the same fact on screen twice in two different
            # wordings.
            self._content.set_deck(self._deck)
        self._content.setVisible(expanded)
        # The strip and the list are the same cards, so only one of them is up at a
        # time: art while folded, names while not.
        self._art.setVisible(self._art.has_art() and not expanded)

    def _toggle_expanded(self) -> None:
        self.set_expanded(not self._expanded)

    def _rebuild_art(self) -> None:
        """Refill the collapsed strip, and decide whether it appears at all.

        Hidden while the card is unfolded, because the list below names the same cards,
        and on an install with no extracted art, where ``set_groups`` finds none and
        leaves the strip empty. Neither case changes the rest of the card: the summary
        line carries the counts whether or not there is art to show.
        """
        self._art.set_groups(deck_groups(self._deck) if self.has_deck else [])
        self._art.setVisible(self._art.has_art() and not self._expanded)

    @property
    def content(self) -> DeckCardList:
        """The unfolded card list. Public so the tab and the tests can read its rows."""
        return self._content

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
            for label, value in ARCHMASTERY_MODES:
                verbs.append(
                    CardVerb(
                        f"Archmastery: {label}",
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
        if self._busy:
            return _DECK_BUSY
        if not self._has_client:
            return f"No game client connected for P{self._slot}"
        return "Snapshot the deck from this game client"

    def _apply_tooltip(self) -> str:
        if not self._editable:
            return LOCKED
        if self._busy:
            return _DECK_BUSY
        if not self._has_client:
            return f"No game client connected for P{self._slot}"
        if not self.has_deck:
            return f"No deck saved for P{self._slot} — use Import first"
        return "Push the saved deck to this game client now"

    def _reapply_verb_states(self) -> None:
        ready = self._editable and self._has_client and not self._busy
        for label, enabled, tooltip in (
            (_IMPORT_DECK, ready, self._import_tooltip()),
            (_APPLY_DECK, ready and self.has_deck, self._apply_tooltip()),
        ):
            button = self.action_button(label)
            if button is not None:
                button.setEnabled(enabled)
                button.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.has_deck else Qt.CursorShape.ArrowCursor)
        self.setToolTip("" if self._editable else LOCKED)
        if self.grip is not None:
            self.grip.set_locked(not self._editable or self._busy, LOCKED if not self._editable else _DECK_BUSY)

    def _refuses_activation(self) -> bool:
        """Nothing to unfold on a slot with no deck, so the body has no verb there."""
        return not self.has_deck

    def _restyle_content(self) -> None:
        self._summary.set_text_color(THEME.text_muted)
        self._archmastery.set_text_color(THEME.text_muted)


class _PlayerDecksSection(SidebarSection):
    """Collapsible section holding one ``_DeckCard`` per player slot.

    Signals carry the slot, so the tab never has to ask a card which one it is: each
    card's own slot-less signal is re-emitted here with the number bound.

    Above the cards sit Import all and Apply all (#109), the same two verbs for every
    hooked client at once -- doing four wizards one card at a time was the complaint. They
    are a row in the body rather than buttons in the heading because ``SidebarSection``'s
    heading is a disclosure, and a verb sitting on it would share its click.

    A card is a *deck*, and its slot is wherever it stands: dragging one to the third
    place gives that deck to P3, and the decks between shift one place, as a drag does
    everywhere else. So the slot-shaped things on a card -- the pill, the account stripe,
    whether a client is hooked -- are the position's, and are re-said on every step of the
    drag rather than only once it lands. Every signal reads the card's slot at the moment
    it fires for the same reason.
    """

    import_requested = pyqtSignal(int)  # 1-based slot number
    apply_requested = pyqtSignal(int)  # 1-based slot number
    import_all_requested = pyqtSignal()
    apply_all_requested = pyqtSignal()
    save_to_library_requested = pyqtSignal(int)  # 1-based slot number
    load_from_library_requested = pyqtSignal(int)  # 1-based slot number
    clear_requested = pyqtSignal(int)  # 1-based slot number
    #: The slots in their new order: new slot N takes the deck that was in ``order[N-1]``.
    decks_reordered = pyqtSignal(list)
    # (slot, True/False/None) — the deck's three-state archmastery override.
    archmastery_mode_changed = pyqtSignal(int, object)

    def __init__(self, parent=None):
        super().__init__("Player Decks", parent)

        layout = QVBoxLayout()
        layout.setSpacing(4)
        self._cards: list[_DeckCard] = []
        self._client_count = 0
        self._editable = True
        self._busy = False

        all_row = QHBoxLayout()
        # Indented by the gutter every card below reserves for its grip, so the row spans
        # exactly the width of the card frames it acts on.
        all_row.setContentsMargins(GRIP_GUTTER, 0, 0, 0)
        all_row.setSpacing(4)
        self._import_all_btn = ActionButton("download", _IMPORT_ALL, parent=self)
        self._import_all_btn.clicked.connect(self.import_all_requested)
        self._apply_all_btn = ActionButton("upload", _APPLY_ALL, parent=self)
        self._apply_all_btn.clicked.connect(self.apply_all_requested)
        all_row.addWidget(self._import_all_btn, 1)
        all_row.addWidget(self._apply_all_btn, 1)
        layout.addLayout(all_row)

        # The account color per slot, held so a card that moves into a slot mid-drag can
        # be striped for it. See ``set_player_colors``.
        self._color_keys: list[int | None] = []
        self._color_labels: list[str] = []

        for slot in range(1, _NUM_PLAYER_SLOTS + 1):
            card = _DeckCard(slot)
            for source, target in (
                (card.import_requested, self.import_requested),
                (card.apply_requested, self.apply_requested),
                (card.save_to_library_requested, self.save_to_library_requested),
                (card.load_from_library_requested, self.load_from_library_requested),
                (card.clear_requested, self.clear_requested),
            ):
                source.connect(partial(self._emit_for, target, card))
            card.archmastery_mode_changed.connect(partial(self._on_archmastery, card))
            layout.addWidget(card)
            self._cards.append(card)

        self.set_content_layout(layout)
        self._drag = DragReorder(layout, self)
        self._drag.set_cards(self._cards)
        self._drag.previewed.connect(self._on_drag_previewed)
        self._drag.reordered.connect(self._on_drag_reordered)
        self._drag.watch_for_show(self)
        self._reapply_all_states()

    @staticmethod
    def _emit_for(signal, card: _DeckCard) -> None:
        """Re-emit a card's slot-less *signal* with the slot it stands in now."""
        signal.emit(card.slot)

    def _on_archmastery(self, card: _DeckCard, mode: object) -> None:
        self.archmastery_mode_changed.emit(card.slot, mode)

    def _on_drag_previewed(self, cards: list) -> None:
        """Re-say every card's slot from where it now stands, as the drag moves it."""
        self._cards = list(cards)
        for index, card in enumerate(self._cards):
            card.set_slot(index + 1)
            card.set_has_client(card.slot <= self._client_count)
        self._apply_player_colors()
        self._reapply_all_states()

    def _on_drag_reordered(self, order: list[int]) -> None:
        """A drag has settled: new slot N holds the deck that was in slot ``order[N-1] + 1``.

        The cards were handed to the engine in slot order, so a baseline index is the old
        slot less one. The cards already stand, and already read, where they belong; this
        makes it the baseline for the next drag and tells the tab to move the decks.
        """
        self._drag.set_cards(self._cards)
        self.decks_reordered.emit([index + 1 for index in order])

    def card(self, slot: int) -> _DeckCard:
        """The card standing in a 1-based slot. Public so the tab and the tests can read one."""
        return self._cards[slot - 1]

    @property
    def drag(self) -> DragReorder:
        """The drag that reorders the cards. Public for the tests that drive it."""
        return self._drag

    @property
    def import_all_button(self) -> ActionButton:
        return self._import_all_btn

    @property
    def apply_all_button(self) -> ActionButton:
        return self._apply_all_btn

    def set_row_expanded(self, slot: int, expanded: bool) -> None:
        """Fold or unfold one slot's card list, exactly as a body click would."""
        self.card(slot).set_expanded(expanded)

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        for card in self._cards:
            card.set_editable(editable)
        self._reapply_all_states()

    def set_client_count(self, count: int) -> None:
        self._client_count = count
        for card in self._cards:
            card.set_has_client(card.slot <= count)
        self._reapply_all_states()

    def set_busy(self, busy: bool) -> None:
        """Hold every deck verb, per card and for all, while a deck job runs."""
        self._busy = busy
        for card in self._cards:
            card.set_busy(busy)
        self._reapply_all_states()

    def _reapply_all_states(self) -> None:
        """Enable the two whole-group verbs, each with the one reason it would not run.

        Apply all needs a saved deck in at least one *hooked* slot: a deck saved for P4
        with two clients hooked has nowhere to go, and the controller skips it.
        """
        ready = self._editable and not self._busy and self._client_count > 0
        any_deck = any(card.has_deck for card in self._cards if card.slot <= self._client_count)
        if not self._editable:
            import_tip = apply_tip = LOCKED
        elif self._busy:
            import_tip = apply_tip = _DECK_BUSY
        elif self._client_count == 0:
            import_tip = apply_tip = "No game clients connected"
        else:
            import_tip = "Read the current deck from every hooked client into this strategy"
            apply_tip = (
                "Apply each saved deck to its client"
                if any_deck
                else "No deck saved for any connected client — use Import first"
            )
        self._import_all_btn.setEnabled(ready)
        self._import_all_btn.setToolTip(import_tip)
        self._apply_all_btn.setEnabled(ready and any_deck)
        self._apply_all_btn.setToolTip(apply_tip)

    def set_player_colors(self, keys: list[int | None], labels: list[str]) -> None:
        """Per slot, index 0 being P1; shorter lists leave the remaining cards unstriped."""
        self._color_keys = list(keys)
        self._color_labels = list(labels)
        self._apply_player_colors()

    def _apply_player_colors(self) -> None:
        keys, labels = self._color_keys, self._color_labels
        for card in self._cards:
            index = card.slot - 1
            card.set_player_color(
                keys[index] if index < len(keys) else None,
                labels[index] if index < len(labels) else "",
            )

    def refresh(self, player_decks: dict[int, DeckData]) -> None:
        for card in self._cards:
            card.set_deck(player_decks.get(card.slot))
        self._reapply_all_states()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _DefaultBinding:
    """Adapts BotCombatStrategy's ``default_strategy_id`` to the same shape as
    ``StateAssignment`` — ``duel_combat_strategy_id`` (settable), ``is_linked`` — so one
    ``StrategyPanel`` class draws both the bot default and every per-fight override.
    """

    def __init__(self, bcs: BotCombatStrategy):
        self._bcs = bcs

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
# Bot Combat Strategy banner
# ---------------------------------------------------------------------------


class _BotStrategyBanner(CardFrame):
    """Which of the user's setups for this bot is live, and the five things you can
    do to that list.

    A ``BotCombatStrategy`` is not a duel config, so this is not a ``StrategyPanel``:
    it names a container -- a default plus a set of per-fight overrides plus four
    decks -- rather than describing what anyone will cast. What it shares is the frame,
    which is the point: three levels drawn as three different kinds of widget was most
    of why this tab did not read as one surface.

    It is still a card in everything but its outline -- the same name and summary lines,
    face verbs, right-click menu and body click -- drawn as a banner (#119): no gutter,
    no rounded border, edge to edge across the top of the tab in the cards' own fill,
    and ruled off from the sections below. Its own caption stands in for the folding
    section heading it used to sit under, which promised a fold over a region that only
    ever held this one row.

    Three verbs reach the face and the rest do not. Duplicate and Export are rare, and
    Delete is destructive next to a body that changes the live configuration -- the
    rule ``card_frame.py`` states for a trash icon 22px from a body click. All of them
    are in the menu, which a kebab button beside the face verbs opens as a right-click
    does (#176): with nothing on the banner saying a menu was there, Export went
    unfound, the finding #46 made for the deck cards.
    """

    FRAME_NAME = "BotStrategyBanner"

    change_requested = pyqtSignal()
    new_requested = pyqtSignal()
    rename_requested = pyqtSignal()
    duplicate_requested = pyqtSignal()
    delete_requested = pyqtSignal()
    export_requested = pyqtSignal()
    #: Change defaults…: open Settings › Combat, where the Solo and Team defaults are chosen.
    defaults_requested = pyqtSignal()
    #: Clear pick: forget a pick whose file is gone.
    clear_pick_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, mode=CardMode.MANAGE)
        self._bcs: BotCombatStrategy | None = None
        self._editable = True
        # Whether there is a Bot Combat Strategy to pick with none picked: an Official one.
        self._has_choices = False
        self._source: SourceText | None = None
        self._build()
        self.activated.connect(self.change_requested)
        self.refresh_theme_styles()

    def _build(self) -> None:
        layout = self._build_frame("strategies", draggable=False, gutter=False)
        # Lines the text up with the text inside the cards below: the tab's 8px margin,
        # their grip gutter, their 1px border and their own 10px padding.
        layout.setContentsMargins(8 + GRIP_GUTTER + 1 + 10, 6, 8 + 1 + 10, 8)

        self._caption = ThemedTextLabel(
            _BCS_CAPTION, css=f"font-size: {THEME.font_micro_px}px; font-weight: bold;", parent=self.frame
        )
        layout.addWidget(self._caption)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self._name = ElidedLabel(_NO_STRATEGY_LABEL)
        font = self._name.font()
        font.setBold(True)
        self._name.setFont(font)
        header.addWidget(self._name, 1)
        # Beside the name, as the strategy cards' source pill is: filled with the accent,
        # because "Official" is the same word with the same weight wherever it appears.
        self._official_pill = AccentPill(OFFICIAL_PILL, parent=self.frame, filled=True)
        self._official_pill.setToolTip("Shipped with this bot. Create a Bot Combat Strategy of your own to change it.")
        header.addWidget(self._official_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        self._official_pill.setVisible(False)
        # Every face button the banner can show, built once; which are up is the state's
        # (``_reapply_verb_states``). They give up their space when hidden, unlike a hover
        # button's: two sets trade places here, and a gap where the other set was is a
        # gap that never fills.
        self._add_face_verbs(header, self._face_verbs())
        for button in self.action_buttons:
            policy = button.sizePolicy()
            policy.setRetainSizeWhenHidden(False)
            button.setSizePolicy(policy)
        header.addWidget(self._make_kebab_button("More Bot Combat Strategy actions"), 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        self._meta = ElidedLabel()
        layout.addWidget(self._meta)
        self._meta.setVisible(False)

        # Fights fled on the default this session, with no Bot Combat Strategy to put them
        # on (Stage 7 of epic #201). Its own row under the line, rather than beside the name
        # with the Official pill: the name elides first at a sidebar's width, and this is
        # the line that should not.
        self._fled_pill = WarningPill("", parent=self.frame)
        fled_row = QHBoxLayout()
        fled_row.setContentsMargins(0, 2, 0, 0)
        fled_row.addWidget(self._fled_pill)
        fled_row.addStretch(1)
        layout.addLayout(fled_row)
        self._fled_pill.setVisible(False)

    def set_fled(self, fights: list[FledFight]) -> None:
        """Mark fights fled on the default this session, while the bot has no Bot Combat Strategy.

        Create Bot Combat Strategy, first on the face and in the menu already, is the fix.
        Empty hides the mark; with a Bot Combat Strategy the default card carries it instead.
        """
        self._fled_pill.setVisible(bool(fights))
        if fights:
            self._fled_pill.setText(fled_text(sum(fight.count for fight in fights)))
            self._fled_pill.setToolTip(fled_tooltip(fights, _FLED_BANNER_FIX))

    @property
    def fled_text(self) -> str:
        """The banner's flee mark, or empty while it is hidden. Public for the tests."""
        return "" if self._fled_pill.isHidden() else self._fled_pill.text()

    def set_strategy(self, bcs: BotCombatStrategy | None) -> None:
        self._bcs = bcs
        self._redraw()

    def set_source(self, source: SourceText | None) -> None:
        """Say where the fights' strategies come from (``combat_resolution.describe_source``).

        With a Bot Combat Strategy picked, it adds which default its unassigned fights use,
        if any do. With none, it is the whole banner: the Official one by name, or the
        Default Duel Strategies with the duel in use.
        """
        self._source = source
        self._redraw()

    def set_has_choices(self, has_choices: bool) -> None:
        """Whether Change… has something to offer while none is picked: an Official one."""
        self._has_choices = has_choices
        self._reapply_verb_states()

    def _redraw(self) -> None:
        bcs, source = self._bcs, self._source
        reasons = "\n".join(source.reasons) if source is not None and source.reasons else ""
        if bcs is not None:
            self._caption.setText(_BCS_CAPTION)
            self._name.setText(bcs.name)
            summary = summarize_bot(bcs)
            self._name.setToolTip(summary.description or bcs.name)
            self._meta.setText(summary.subtitle_line())
            # Which default its unassigned fights fall to is the tooltip's, not the line's:
            # beside the summary it elides at a sidebar's width, and the default card just
            # below already shows that default, with a pill saying whose it is.
            detail = source.detail if source is not None else ""
            self._meta.setToolTip("\n".join(part for part in (detail, reasons) if part))
            self._meta.setVisible(True)
            self._official_pill.setVisible(False)
        elif source is not None:
            self._caption.setText(source.caption)
            self._name.setText(source.name)
            self._name.setToolTip(source.name)
            self._meta.setText(source.detail)
            self._meta.setToolTip(reasons)
            self._meta.setVisible(bool(source.detail))
            self._official_pill.setVisible(source.pill == OFFICIAL_PILL)
        else:
            self._caption.setText(_BCS_CAPTION)
            self._name.setText(_NO_STRATEGY_LABEL)
            self._name.setToolTip("No Bot Combat Strategy for this bot yet")
            self._meta.setVisible(False)
            self._official_pill.setVisible(False)
        self._reapply_verb_states()
        self.refresh_theme_styles()

    @property
    def strategy(self) -> BotCombatStrategy | None:
        """The strategy this card names, or None. Public so the tab need not re-derive it."""
        return self._bcs

    @property
    def caption_text(self) -> str:
        return self._caption.text()

    @property
    def name_text(self) -> str:
        return self._name.full_text()

    @property
    def meta_text(self) -> str:
        """The line under the name, or empty when it is hidden."""
        return self._meta.full_text() if not self._meta.isHidden() else ""

    @property
    def meta_tooltip(self) -> str:
        return self._meta.toolTip()

    @property
    def shows_official_pill(self) -> bool:
        return not self._official_pill.isHidden()

    @property
    def missing_pick(self) -> bool:
        """Whether the banner shows the fault of a pick whose file is gone."""
        return self._bcs is None and self._source is not None and self._source.missing_pick

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._reapply_verb_states()
        self.setCursor(Qt.CursorShape.PointingHandCursor if editable else Qt.CursorShape.ArrowCursor)
        self.setToolTip("" if editable else LOCKED)

    def _reapply_verb_states(self) -> None:
        """Show the face buttons for the state, and enable them while the bot is stopped.

        With a Bot Combat Strategy: Change…, Rename… and New…. With none: Create Bot
        Combat Strategy and Change defaults…, the two ways out of running on the defaults,
        and Change… too when there is an Official one to pick. With a pick whose file is
        gone, Clear pick takes Change defaults…' place, since until it is cleared the
        defaults are not what a run asks about. The Create verb stays live, because that is
        exactly the state a user gets out of by making one -- a card whose every
        affordance is dead is a dead end.
        """
        has = self._bcs is not None
        missing = self.missing_pick
        shown = {
            _CLEAR_PICK: missing,
            _CHANGE_BCS: has or self._has_choices,
            _RENAME_BCS: has,
            _NEW_BCS: has,
            _CREATE_BCS: not has,
            _CHANGE_DEFAULTS: not has and not missing,
        }
        for label, visible in shown.items():
            button = self.action_button(label)
            if button is not None:
                button.setVisible(visible)
                button.setEnabled(self._editable)

    def _face_verbs(self) -> list[CardVerb]:
        """Every verb that can reach the face, in header order: every state's set."""
        return [
            CardVerb(
                _CLEAR_PICK,
                "dismiss",
                "Forget the missing Bot Combat Strategy, so runs stop asking about it",
                self.clear_pick_requested.emit,
                True,
            ),
            CardVerb(_CHANGE_BCS, "search", "Pick a different Bot Combat Strategy", self.change_requested.emit, True),
            CardVerb(_RENAME_BCS, "pencil", "Rename this Bot Combat Strategy", self.rename_requested.emit, True),
            CardVerb(_NEW_BCS, "plus", "Create a new Bot Combat Strategy for this bot", self.new_requested.emit, True),
            CardVerb(_CREATE_BCS, "plus", "Create a Bot Combat Strategy for this bot", self.new_requested.emit, True),
            CardVerb(
                _CHANGE_DEFAULTS,
                "gear",
                "Choose the Solo and Team defaults, in Settings › Combat",
                self.defaults_requested.emit,
                True,
            ),
        ]

    def _verbs(self) -> list[CardVerb]:
        """What the right-click menu offers now: the face verbs this state shows, then the rest."""
        face = {verb.label: verb for verb in self._face_verbs()}
        if self._bcs is None:
            labels = [_CREATE_BCS, _CHANGE_DEFAULTS] + ([_CHANGE_BCS] if self._has_choices else [])
            if self.missing_pick:
                labels.insert(0, _CLEAR_PICK)
            return [face[label] for label in labels]
        return [
            face[_CHANGE_BCS],
            face[_RENAME_BCS],
            face[_NEW_BCS],
            CardVerb(_DUPLICATE_BCS, "copy", "Copy this strategy under a new name", self.duplicate_requested.emit),
            CardVerb(_DELETE_BCS, "trash", "Delete this Bot Combat Strategy", self.delete_requested.emit),
            CardVerb(_EXPORT_BCS, "upload", "Save this strategy as a file to share", self.export_requested.emit),
            CardVerb(
                _CHANGE_DEFAULTS,
                "gear",
                "Choose the Solo and Team defaults its unassigned fights use",
                self.defaults_requested.emit,
            ),
        ]

    def _refuses_activation(self) -> bool:
        """A body click picks, and there is nothing to pick between until one exists."""
        return not self._editable or (self._bcs is None and not self._has_choices)

    def _apply_state_palette(self) -> bool:
        """The banner's palette, in place of the card's: the card fill and a rule, not a box.

        Hover lights it the way it lights a card -- ``node_fill`` and an accent line --
        because the body click is the same promise here as there. There is no dragging
        branch to honor: with no grip, nothing can pick the banner up.
        """
        lit = self._hovered and self.has_body_verb
        set_style_sheet(
            self,
            f"#{self.FRAME_NAME} {{ background: {THEME.node_fill if lit else THEME.surface}; "
            f"border: none; border-bottom: 1px solid {THEME.accent if lit else THEME.border}; }}",
        )
        self._restyle_content()
        return True

    def _restyle_content(self) -> None:
        # ``error``, as for anything else that does not work: the pick is a fault.
        self._name.set_text_color(THEME.error if self.missing_pick else THEME.text)
        self._meta.set_text_color(THEME.text_muted)


# ---------------------------------------------------------------------------
# Combat Tab
# ---------------------------------------------------------------------------


class CombatTab(QWidget):
    """Left-sidebar tab for combat configuration using the three-level strategy system."""

    combat_config_changed = pyqtSignal()
    import_deck_requested = pyqtSignal(int)  # 1-based slot number
    apply_deck_requested = pyqtSignal(int)  # 1-based slot number
    import_all_decks_requested = pyqtSignal()
    apply_all_decks_requested = pyqtSignal()
    #: The banner's Export… (#170), with the Bot Combat Strategy it names. The controller writes the file.
    export_bot_strategy_requested = pyqtSignal(object)
    #: The banner's Change defaults…: open Settings › Combat. The main window opens it,
    #: from a synchronous slot, as every Settings route does.
    combat_defaults_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: BotConfig | None = None
        self._bcs: BotCombatStrategy | None = None
        self._all_bcs: list[BotCombatStrategy] = []
        self._all_dcs: list[DuelCombatStrategy] = []
        self._all_pcs: list[PlayerCombatStrategy] = []
        # state_id → its StateAssignment (owned in-memory; flushed to BCS on save,
        # dropped if it never ends up linked)
        self._state_bindings: dict[str, StateAssignment] = {}
        self._state_panels: dict[str, StrategyPanel] = {}
        # A card made by the add-card and not yet given any fights. It exists only so the
        # membership popup has something to anchor to, and it is dropped unsaved if that
        # popup closes with nothing chosen — see ``_on_add_strategy``.
        self._pending_panel: StrategyPanel | None = None
        # The specialized cards' order, top to bottom: the user's, set by dragging, and
        # kept on disk as the order of ``state_assignments``. Identity, not equality --
        # two unlinked placeholders compare equal. See ``_set_bindings``.
        self._card_order: list[StateAssignment] = []
        self._suppress = False
        self._editable = True
        # How many game clients are connected: which default, Solo or Team, a fight with
        # nothing tailored would run. Pushed by the controller (``set_client_count``).
        self._clients = 0
        # The session's fights fled on a default, every bot's. Installed by the controller,
        # which records into it; None draws no marks (``set_flee_marks``).
        self._flee_marks: FleeMarks | None = None
        # Answers "which account is behind each player slot"; installed by AppController,
        # which is what holds the ClientManager. See ``set_player_account_source``.
        self._player_account_source: Callable[[], list[str | None]] | None = None
        self._deck_link: DeckClientLink | None = None
        # The last answer pushed to the cards, so a tick that learns nothing new repaints
        # nothing. See ``refresh_player_colors``.
        self._player_colors: tuple[list[int | None], list[str]] = ([], [])
        # Runs only while the tab is on screen -- see showEvent/hideEvent. Parented, so it
        # dies with the tab rather than firing into it.
        self._color_timer = QTimer(self)
        self._color_timer.setInterval(int(PLAYER_COLOR_REFRESH_INTERVAL * 1000))
        self._color_timer.timeout.connect(self.refresh_player_colors)

        # No debounce timer. There was one, at 500ms, because the inline DSL editor
        # emitted ``changed`` on every keystroke and every keystroke would otherwise have
        # written a strategy file. With the editor gone, every remaining way this tab can
        # change the configuration is a single discrete act — a pick accepted from a
        # dialog, a membership committed, a deck loaded — so each one saves immediately
        # and there is no pending state for a bot switch, a Save As or a quit to race.

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        # Sections fold and decks come and go, each of which can tip the tab into scrolling.
        reserve_scrollbar_gutter(scroll_area)

        content = QWidget()
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(8, 8, 8, 8)
        self._content_layout.setSpacing(4)

        # ── Bot Combat Strategy ──────────────────────────────────────
        # A banner above the scroll area rather than a section inside it -- see the
        # module docstring. The outer layout at the foot of ``__init__`` places it.
        self._bcs_banner = _BotStrategyBanner(self)
        self._bcs_banner.change_requested.connect(self._on_pick_bcs)
        self._bcs_banner.new_requested.connect(self._on_new_bcs)
        self._bcs_banner.rename_requested.connect(self._on_rename_bcs)
        self._bcs_banner.duplicate_requested.connect(self._on_duplicate_bcs)
        self._bcs_banner.delete_requested.connect(self._on_delete_bcs)
        self._bcs_banner.export_requested.connect(
            lambda: self._bcs is not None and self.export_bot_strategy_requested.emit(self._bcs)
        )
        self._bcs_banner.defaults_requested.connect(self.combat_defaults_requested)
        self._bcs_banner.clear_pick_requested.connect(self._on_clear_pick)

        # ── Duel Combat Strategies ───────────────────────────────────
        # One section for both levels. The default card is first and always present; the
        # cards for fights that opted out follow it; the AddCard ends the list. All three
        # of those are persistent widgets, so ``_rebuild_state_assignments`` re-places
        # them rather than recreating them — a live click target must not be destroyed
        # mid-gesture, and the add-card's popup is a gesture that spans a rebuild.
        self._strategies_section = SidebarSection(_STRATEGIES_SECTION, self)
        self._strategies_layout = QVBoxLayout()
        self._strategies_layout.setSpacing(6)

        # The bot default is the last stop before the Solo and Team defaults, so with nothing
        # picked it shows the one that answers, with a pill saying whose it is. The
        # specialized cards below say something different — see STATE_INHERIT_LABEL.
        self._default_panel = StrategyPanel(
            DEFAULTS_LABEL, DEFAULTS_META, parent=self._strategies_section, role=PanelRole.DEFAULT
        )
        self._default_panel.pick_requested.connect(partial(self._on_pick_duel, self._default_panel))
        self._default_panel.edit_requested.connect(partial(self._on_edit_strategy, self._default_panel))
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
        # Handed the specialized cards alone, so the default card above them and the
        # add-card below are never part of the order -- see ``_on_strategies_reordered``.
        self._strategy_drag = DragReorder(self._strategies_layout, self)
        self._strategy_drag.reordered.connect(self._on_strategies_reordered)
        self._strategy_drag.watch_for_show(self._strategies_section)
        self._content_layout.addWidget(self._strategies_section)
        self._strategies_section.setVisible(False)

        # ── Player Decks ─────────────────────────────────────────────
        self._decks_section = _PlayerDecksSection(self)
        self._decks_section.import_requested.connect(self.import_deck_requested)
        self._decks_section.apply_requested.connect(self.apply_deck_requested)
        self._decks_section.import_all_requested.connect(self.import_all_decks_requested)
        self._decks_section.apply_all_requested.connect(self.apply_all_decks_requested)
        self._decks_section.save_to_library_requested.connect(self._on_save_deck_to_library)
        self._decks_section.load_from_library_requested.connect(self._on_load_deck_from_library)
        self._decks_section.clear_requested.connect(self._on_clear_deck)
        self._decks_section.archmastery_mode_changed.connect(self._on_archmastery_mode_changed)
        self._decks_section.decks_reordered.connect(self._on_decks_reordered)
        self._content_layout.addWidget(self._decks_section)

        self._content_layout.addStretch()

        scroll_area.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._bcs_banner)
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
        self._bcs_banner.set_strategy(self._bcs)
        self._refresh_default_panel()
        self._suppress = False

    def refresh_source(self) -> None:
        """Re-say where the fights' strategies come from: the banner, and the default card's fallback.

        Public because more than this tab moves the answer: the Solo and Team defaults
        change in Settings › Combat, which the main window reports, and the team size
        changes with the clients connected. Read through ``describe_source``, which
        resolves as a run would and writes nothing.
        """
        pick = self._bcs.id if self._bcs is not None else None
        if pick is None and self._config is not None:
            # A pick whose file is gone still names the reason the banner gives.
            pick = SettingsManager.instance().get_active_strategy(self._config.id)
        try:
            source = describe_source(self._config, self._clients, pick)
            fallback = (
                defaults_in_use(self._clients) if self._bcs is not None and not self._bcs.default_strategy_id else []
            )
        except Exception:
            logger.exception("Could not work out where this bot's fights get their duel strategies")
            source, fallback = None, []
        self._bcs_banner.set_has_choices(self._bcs is None and bool(self._all_bcs))
        self._bcs_banner.set_source(source)
        self._default_panel.set_fallback(fallback)
        self._refresh_fled()

    def set_flee_marks(self, marks: FleeMarks | None) -> None:
        """Install the session's record of fights fled on a default, and draw this bot's marks.

        Installed once by ``AppController``, which records each flee into it and then calls
        ``refresh_source``. The record outlives every bot switch, so a bot's marks are
        there again when it is loaded again, until the app closes.
        """
        self._flee_marks = marks
        self._refresh_fled()

    def _refresh_fled(self) -> None:
        """Mark this bot's fights fled on a default where each is drawn (Stage 7 of epic #201).

        Only fights that would still run a default are marked, so a fight given a strategy
        loses its mark. With a Bot Combat Strategy picked, the default card carries them,
        on a fight's pill where it has one; with none, the banner does, being all there is.
        """
        fled: list[FledFight] = []
        if self._flee_marks is not None and self._config is not None:
            pick = self._bcs.id if self._bcs is not None else None
            try:
                fled = fled_on_default(self._flee_marks, self._config, self._clients, pick)
            except Exception:
                logger.exception("Could not work out which fled fights still run a default")
        self._default_panel.set_fled(fled if self._bcs is not None else [])
        self._bcs_banner.set_fled(fled if self._bcs is None else [])

    @property
    def banner(self) -> _BotStrategyBanner:
        """The Bot Combat Strategy banner. Public for the tests that read what it says."""
        return self._bcs_banner

    @property
    def default_panel(self) -> StrategyPanel:
        """The default card. Public for the tests that read its fallback."""
        return self._default_panel

    def refresh_deck_summaries(self) -> None:
        self._refresh_deck_summaries()

    def update_deck_slots(self, decks: dict[int, DeckData]) -> None:
        """Update the in-memory BCS decks by slot, then redraw the cards once.

        Once for all of them: a redraw per slot rebuilt every card's art once per slot
        in the same event-loop turn, which is what flashed windows during Import all (#120).
        """
        if self._bcs is not None:
            self._bcs.player_decks.update(decks)
        self._refresh_deck_summaries()

    def refresh_duel_strategies(self) -> None:
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
        self._bcs_banner.set_strategy(self._bcs)
        lookup = self._lookup()
        self._default_panel.set_libraries(lookup.duels, lookup.players)
        for panel in self._group_panels():
            panel.set_libraries(lookup.duels, lookup.players)
        self.refresh_source()

    def _lookup(self) -> StrategyLookup:
        """Every strategy in reach: the active Bot Combat Strategy's own first, then the library's.

        The one rule for resolving an id, shared with ``resolve_combat`` -- see
        ``bartlebot.combat.bot_combat_strategy``. The cards and the fights popup both read from it,
        so they cannot name a different strategy than the one the fight will run.
        """
        return StrategyLookup(self._bcs, self._all_dcs, self._all_pcs)

    def _group_panels(self) -> list[StrategyPanel]:
        """Each panel once — ``_state_panels`` maps every fight in a group to the
        same widget, so its values repeat."""
        return list(dict.fromkeys(self._state_panels.values()))

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._bcs_banner.set_editable(editable)
        self._decks_section.set_editable(editable)
        self._default_panel.set_editable(editable)
        self._add_card.setEnabled(editable)
        for panel in self._group_panels():
            panel.set_editable(editable)

    def set_client_count(self, count: int) -> None:
        self._decks_section.set_client_count(count)
        self.refresh_player_colors()
        if count != self._clients:
            self._clients = count
            self.refresh_source()

    def set_deck_client_link(self, link: DeckClientLink | None) -> None:
        """Install the way the combat dialog's Deck tab reaches the game clients.

        Pushed by ``AppController``, which owns the clients and the one-at-a-time deck job.
        """
        self._deck_link = link

    def set_deck_busy(self, busy: bool) -> None:
        """Whether a deck import or apply is running. Pushed by ``AppController``, which
        owns the job; every deck verb waits for it."""
        self._decks_section.set_busy(busy)

    def set_player_account_source(self, source: Callable[[], list[str | None]] | None) -> None:
        """Install what answers *which account is behind each player slot, right now*.

        A callable rather than a value, and asked at the moment the combat dialog opens.
        An account identity is learned asynchronously after a client is hooked and nothing
        announces it arriving — ``ClientManager.account_for`` says so in as many words —
        so a value pushed in when the client *set* last changed would be stale exactly
        when someone hooks four clients and immediately opens this dialog. Asked at open
        time it is a dict lookup per client and it is never wrong.

        ``None`` (the default) means nobody has installed one, which is the normal state
        in tests and in a session with nothing running: the dialog's player pills simply
        go uncolored. See gui/widgets/pills/player_pills.py for why that degrades cleanly
        rather than falling back to a color.

        The tab's own player pills and deck stripes ask the same source, on a timer while
        the tab is visible -- see ``refresh_player_colors``.
        """
        self._player_account_source = source
        self.refresh_player_colors()

    def refresh_player_colors(self) -> None:
        """Re-ask which account is behind each slot, and recolor the tab if that changed.

        Public because it is what the timer, a client-count change and the tests call.
        Polled rather than signalled for the reason ``set_player_account_source`` gives:
        an identity arrives after its client does and nothing announces it. The ask is a
        dict lookup per client, and nothing repaints unless the answer moved.
        """
        colors = self._player_color_keys()
        if colors == self._player_colors:
            return
        self._player_colors = colors
        keys, labels = colors
        self._decks_section.set_player_colors(keys, labels)
        self._default_panel.set_player_colors(keys, labels)
        for panel in self._group_panels():
            panel.set_player_colors(keys, labels)
        if self._pending_panel is not None:
            self._pending_panel.set_player_colors(keys, labels)

    def _player_color_keys(self) -> tuple[list[int | None], list[str]]:
        """A palette slot and a display name per player slot, both possibly empty.

        Resolved through ``account_stripe_keys`` over the *whole* account list rather than
        per username, because a color is only distinct with respect to the others — see
        gui/theming/account_accent.py. An unidentified client gets no slot and no name, which is
        the truth about it.
        """
        if self._player_account_source is None:
            return [], []
        usernames = self._player_account_source()
        accounts = SettingsManager.instance().accounts
        aliases = {a.get("username", ""): a for a in accounts}
        labels = [
            "" if u is None else CredentialStore.display_name(u, (aliases.get(u) or {}).get("alias") or None)
            for u in usernames
        ]
        return account_stripe_keys(usernames, accounts), labels

    # ------------------------------------------------------------------
    # BCS loading / saving
    # ------------------------------------------------------------------

    def _load_bcs(self) -> None:
        """Load BCS from disk based on active strategy setting."""
        self._bcs = None
        self._set_bindings([])
        self._all_bcs = []
        self._all_dcs = list_duel_strategies()
        self._all_pcs = list_player_strategies()
        if self._config is None:
            return
        self._all_bcs = list_bot_strategies(self._config.id)
        active_id = SettingsManager.instance().get_active_strategy(self._config.id)
        if active_id:
            self._bcs = next((b for b in self._all_bcs if b.id == active_id), None)
        own = self._own_bcs()
        if self._bcs is None and own:
            self._bcs = own[0]
            if active_id != self._bcs.id:
                SettingsManager.instance().set_active_strategy(self._config.id, self._bcs.id)
        if self._bcs is not None:
            self._set_bindings(self._bcs.state_assignments)

    def _own_bcs(self) -> list[BotCombatStrategy]:
        """The user's own Bot Combat Strategies for this bot, the only ones the tab picks unasked.

        Never an Official one: that is level 2 of the resolution and answers without being
        picked, and picking it on the user's behalf would make it level 1, the user's,
        without the user doing anything.
        """
        return [b for b in self._all_bcs if origin_of(StrategyKind.BOT, b.id) is not Origin.SHIPPED]

    def _set_bindings(self, assignments: list[StateAssignment]) -> None:
        """Bind every fight from a strategy file's *assignments*, whose order is the cards'.

        Each stored fight is placed on one of the bot's current fights, by enemy and
        then by state id (``bartlebot.combat.fight_keys``), so from here on an assignment's
        ``state_ids`` are the fights it is bound to.

        ``build_bindings`` inserts in file order, so each assignment's first appearance
        among the values is its place in the file.
        """
        if self._config is not None:
            orphans = place(assignments, self._config).orphans
            if orphans:
                # Still in the file: nothing is written on load. The next save drops them.
                logger.info(
                    "Stored fight(s) on state(s) %s of combat strategy %s match no fight in the bot",
                    ", ".join(fight.state for _, fight in orphans),
                    self._bcs.id if self._bcs else "?",
                )
        self._state_bindings = build_bindings(assignments, self._config)
        self._card_order = list({id(a): a for a in self._state_bindings.values()}.values())

    def _card_rank(self, assignment: StateAssignment) -> int:
        """Where *assignment*'s card sits; one the order has not seen yet goes last."""
        for index, known in enumerate(self._card_order):
            if known is assignment:
                return index
        return len(self._card_order)

    def _ensure_state_binding(self, state_id: str) -> StateAssignment:
        """Return this state's binding, creating an empty in-memory one if absent.

        Never persisted until it actually links a strategy — see ``_save_bcs``.
        """
        existing = self._state_bindings.get(state_id)
        if existing is not None:
            return existing
        fresh = StateAssignment(fights=[FightRef(state=state_id)])
        self._state_bindings[state_id] = fresh
        return fresh

    def _save_bcs(self) -> None:
        if self._bcs is None:
            return
        # Only persist bindings that actually say something — an unlinked
        # placeholder is indistinguishable from no binding at all, so writing it
        # out would just be silent clutter in the file.
        # dict keyed by id() in case a loaded file grouped several fights
        # under one shared StateAssignment object.
        kept = {id(a): a for a in self._state_bindings.values() if a.is_linked}
        # In card order, which is how the order survives a reload.
        ordered = sorted(kept.values(), key=self._card_rank)
        self._bcs.state_assignments = self._prune_orphans(ordered)
        try:
            save_bot_strategy(self._bcs)
        except Exception:
            logger.exception("Failed to save BotCombatStrategy %s", self._bcs.id)
        # After the save, since the marks are resolved from the file as a run would: a fight
        # that just left the default card for one of its own no longer runs a default.
        self._refresh_fled()
        self.combat_config_changed.emit()

    def _prune_orphans(self, assignments: list[StateAssignment]) -> list[StateAssignment]:
        """Write each fight as it stands now, dropping the orphaned ones and any assignment left empty.

        Per fight: one whose state is no longer an exposed fight of the bot goes, and
        the rest of its assignment stays. Each kept fight is written as its current
        state, plus its enemy when that enemy names it alone in the bot -- which is how
        the keys heal, here and nowhere else (``bartlebot.combat.fight_keys``).

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
        dropped = heal(assignments, self._config)
        if dropped:
            logger.info(
                "Dropping combat assignment fight(s) for state(s) %s from strategy %s",
                ", ".join(fight.state for fight in dropped),
                self._bcs.id if self._bcs else "?",
            )
        return [assignment for assignment in assignments if assignment.fights]

    def open_combat_dialog(
        self,
        kind: StrategyKind = StrategyKind.DUEL,
        mode: DialogMode = DialogMode.LIBRARY,
        current_id: str | None = None,
        allow_none: bool = True,
        none_label: str = NONE_LABEL,
        none_tooltip: str = NONE_TOOLTIP,
    ) -> CombatStrategyDialog | None:
        """Run the combat strategy dialog. Returns it if accepted, else None.

        Modal, and given a working set loaded fresh each time. Modal because the
        dialog owns a Save boundary over the same files this tab reads, and a
        non-modal window editing them alongside the tab is how the two used to
        disagree about what was on disk.

        The working set holds this tab's active Bot Combat Strategy, editable, so the dialog
        can write its file (#118). That makes this tab's own ``self._bcs`` stale
        the moment an accepted dialog closes, and every caller goes on to change a
        binding and save — which would write the stale copy over the dialog's work,
        dropping a strategy made there and leaving the fight linked to nothing. So an
        accepted dialog is followed by a reload of the Bot Combat Strategy from disk and a re-bind
        of every fight, *before* the caller acts on the result: a caller holding a
        binding from before the dialog must look it up again. Cancel writes nothing,
        so it only re-reads the library.

        The library view — **File → Combat Strategies**, the default *mode* — gets no Bot
        Combat Strategy at all, even with a bot loaded: it shows and edits the library alone.
        """
        library_only = mode is DialogMode.LIBRARY or self._config is None
        ws = StrategyWorkingSet.from_store(
            bot_strategy_id=None if library_only or self._bcs is None else self._bcs.id,
            bot_id=None if library_only else self._config.id,
        )
        color_keys, account_labels = self._player_color_keys()
        dlg = CombatStrategyDialog(
            ws,
            kind=kind,
            mode=mode,
            current_id=current_id,
            allow_none=allow_none,
            none_label=none_label,
            none_tooltip=none_tooltip,
            player_color_keys=color_keys,
            player_account_labels=account_labels,
            bot_name=self._bot_name_lookup(),
            deck_link=self._deck_link,
            parent=self,
        )
        accepted = bool(dlg.exec())
        if not accepted:
            self.refresh_strategy_data()
            return None
        made = ws.bot_strategy
        if self._config is not None and made is not None and (self._bcs is None or made.id != self._bcs.id):
            # A bot with no Bot Combat Strategy had one made in the dialog: it becomes the active one.
            SettingsManager.instance().set_active_strategy(self._config.id, made.id)
            self._rebind()
        elif self._bot_strategy_changed_on_disk():
            self._rebind()
        else:
            # Nothing the tab holds is stale, so the cards stay: a rebuild would destroy
            # every one of them for a pick that changes one line.
            self.refresh_strategy_data()
        self.combat_config_changed.emit()
        return dlg

    def _bot_name_lookup(self) -> Callable[[str], str | None]:
        """Bot id to bot name, for the Save confirmation's "'Solo' (Atlantea)".

        Lazy, and read once per dialog: the bot library is only scanned if a Save
        actually has another Bot Combat Strategy to name.
        """
        names: dict[str, str] = {}
        config = self._config

        def lookup(bot_id: str) -> str | None:
            if config is not None and bot_id == config.id:
                return config.name
            if not names:
                names.update(read_bot_names(*bot_roots.library_dirs()))
            return names.get(bot_id)

        return lookup

    def _bot_strategy_changed_on_disk(self) -> bool:
        """Whether the active Bot Combat Strategy's file no longer matches ``self._bcs``."""
        if self._bcs is None:
            return False
        try:
            on_disk = load_bot_strategy(self._bcs.id)
        except Exception:
            return True
        return on_disk.model_dump() != self._bcs.model_dump()

    # ------------------------------------------------------------------
    # UI refresh
    # ------------------------------------------------------------------

    def _refresh_default_panel(self) -> None:
        """Redraw the default card, and with it what the banner says: both follow the pick."""
        if self._bcs is None:
            self._default_panel.setVisible(False)
        else:
            lookup = self._lookup()
            self._default_panel.load(_DefaultBinding(self._bcs), lookup.duels, lookup.players)
            self._default_panel.set_editable(self._editable)
        self.refresh_source()

    def _refresh_deck_summaries(self) -> None:
        decks = self._bcs.player_decks if self._bcs else {}
        self._decks_section.refresh(decks)

    def _on_save_deck_to_library(self, slot: int) -> None:
        from bartlebot.combat.strategy_store import save_deck_preset  # noqa: PLC0415
        from bartlebot.shared.strategy_models import DeckPreset  # noqa: PLC0415

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
        from bartlebot.combat.strategy_store import list_deck_presets  # noqa: PLC0415

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

    def _on_decks_reordered(self, order: list[int]) -> None:
        """Give each player the deck the drag put in their slot, and save.

        *order* is the old slots in their new order. An empty slot moves like any other,
        so dragging P3's deck onto an empty P1 leaves P3 empty rather than copying.
        """
        if self._bcs is None:
            return
        old = self._bcs.player_decks
        # A slot past the four cards is not in *order*, and keeps its deck.
        decks = {slot: deck for slot, deck in old.items() if slot not in order}
        decks.update({new: old[prev] for new, prev in enumerate(order, start=1) if prev in old})
        self._bcs.player_decks = dict(sorted(decks.items()))
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
        # Before the cards go: a drag in flight would otherwise be left holding one.
        self._strategy_drag.cancel()
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
        # The user's order, not ``group_bindings``' FSM order; a card new since the last
        # rebuild goes last (sorted is stable, so newcomers keep FSM order among
        # themselves). Recorded again so the order forgets cards that are gone.
        configured = sorted((g for g in groups if g.is_configured), key=lambda g: self._card_rank(g.assignment))
        self._card_order = [g.assignment for g in configured]

        for group in configured:
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
        self._strategy_drag.set_cards(self._group_panels())

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
        lookup = self._lookup()
        panel.load(assignment, lookup.duels, lookup.players)
        panel.set_fight_options(self._fight_sections(exposed), list(assignment.state_ids))
        panel.set_editable(self._editable)
        # Arrives colored: the timer only repaints when the answer changes, so a card
        # built between two identical answers would otherwise stay plain.
        panel.set_player_colors(*self._player_colors)
        panel.pick_requested.connect(partial(self._on_pick_duel, panel))
        panel.edit_requested.connect(partial(self._on_edit_strategy, panel))
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
        key = self._binding_key(binding)
        accepted, chosen = self._pick(binding.duel_combat_strategy_id, panel.empty_label)
        if accepted:
            self._link(key, chosen)

    def _on_pick_for_fight(self, state_id: str) -> None:
        """Give one fight a strategy of its own — a click on its pill in ``BotDefaultPanel``.

        The same act ``_on_pick_duel`` performs for a card, for a fight that has no card
        yet: it is the fight that leaves the bot-default pills and arrives as a card of
        its own, so this always regroups and rebuilds rather than redrawing one panel.
        """
        if self._config is None:
            return
        current = self._ensure_state_binding(state_id).duel_combat_strategy_id
        accepted, chosen = self._pick(current, STATE_INHERIT_LABEL)
        if accepted:
            self._link(state_id, chosen)

    def _on_add_strategy(self) -> None:
        """Give some fights a strategy of their own — the add-card at the foot of the list.

        A menu of two, because the strategy can already exist or be made for these fights
        (#118): **Select existing duel strategy…** opens the picker, and **Create new duel
        strategy…** opens the Bot Combat Strategy editor with nothing selected, so New and
        Duplicate are both there. GitHub's sub-issues are the shape, "Create sub-issue"
        beside "Add existing issue"; so is the assignment card's own menu, which mixes
        picking a Player Strategy with "Custom DSL…".

        Either way it is two dialogs, chained, and the chaining is what keeps the
        invariant that a card on screen is a card in the file. A ``StateAssignment`` with
        no ``fights`` forms no group, so it would draw no card on the next rebuild, and
        ``_prune_orphans`` drops it outright on the next save: an empty card cannot be left
        lying about for the user to fill in later, because nothing would bring it back. So
        the strategy comes first, the card is made, its own membership popup opens on it
        immediately, and the card is dropped again if that popup closes with nothing
        chosen. A strategy made for it is kept regardless: it is in the Bot Combat Strategy, and Save
        put it there.

        The pill route on the default card is the other way in, and stays the shorter one
        for a single fight. This is the one that pays when several fights are to share.
        """
        if self._config is None or self._bcs is None or not self._editable:
            return
        self._add_card.open_menu(self._add_card_verbs())

    def _add_card_verbs(self) -> list[tuple[str, str, Callable[[], None]]]:
        """The add-card menu's two items as (icon, label, run), in order. Public-shaped so a test can read them."""
        return [("search", _ADD_EXISTING, self._on_add_existing), ("plus", _ADD_CREATED, self._on_add_created)]

    def _on_add_existing(self) -> None:
        """The add-card's first item: pick a strategy that already exists."""
        if self._config is None or self._bcs is None or not self._editable:
            return
        # No "none" option: picking nothing here would make a card that says nothing,
        # which is what the default card already is.
        dlg = self.open_combat_dialog(kind=StrategyKind.DUEL, mode=DialogMode.PICK, allow_none=False)
        if dlg is not None and dlg.selected_id():
            self._start_pending_card(dlg.selected_id())

    def _on_add_created(self) -> None:
        """The add-card's second item: make a strategy for these fights, in the Bot Combat Strategy.

        Its editor opens with nothing selected. On Save, the new card takes
        whatever is selected — made there, or not — and goes on to its fights.
        """
        if self._config is None or self._bcs is None or not self._editable:
            return
        dlg = self.open_combat_dialog(kind=StrategyKind.DUEL, mode=DialogMode.BOT_STRATEGY)
        if dlg is not None and dlg.selected_id():
            self._start_pending_card(dlg.selected_id())

    def _start_pending_card(self, chosen: str) -> None:
        """Make the add flow's card for *chosen*, and ask for its fights."""
        if self._config is None:
            return
        exposed = exposed_combat_states(self._config)
        assignment = StateAssignment(duel_combat_strategy_id=chosen)
        panel = self._make_group_panel(assignment, exposed, [])
        panel.membership_changed.connect(partial(self._on_pending_membership, assignment, panel))
        # Where the rebuild would have put it: above the add-card, below the rest.
        self._strategies_layout.insertWidget(self._strategies_layout.count() - 1, panel)
        self._pending_panel = panel
        # Not a card in the order until it has fights: see ``_on_pending_membership``.
        panel.grip.set_locked(True, "Choose this strategy's fights first")
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

    def _on_strategies_reordered(self, order: list[int]) -> None:
        """A drag of the specialized cards has settled: keep the order, and save it.

        Cosmetic: which fights a card covers decides what they fight with, never where it
        sits. The cards already stand where they belong, so nothing is rebuilt -- the
        order is recorded, the per-fight index follows it, and the file is written.
        """
        panels = self._group_panels()
        reordered = [panels[index] for index in order]
        self._card_order = [panel.binding for panel in reordered]
        old = self._state_panels
        self._state_panels = {sid: panel for panel in reordered for sid, p in old.items() if p is panel}
        self._strategy_drag.set_cards(reordered)
        self._save_bcs()

    def _pick(self, current_id: str | None, none_label: str) -> tuple[bool, str | None]:
        """Run the picker, and return whether it was accepted and what it chose.

        Shared by the card path and the pill path so both offer the same dialog with the
        same "none" row — what a binding falls through to is a property of the binding's
        level, not of which widget the click landed on.

        Returns the choice rather than applying it: an accepted dialog reloads the
        Bot Combat Strategy (see ``open_combat_dialog``), so the binding the click came from is gone,
        and the caller finds its successor with ``_link``.
        """
        dlg = self.open_combat_dialog(
            kind=StrategyKind.DUEL,
            mode=DialogMode.PICK,
            current_id=current_id,
            allow_none=True,
            none_label=none_label,
            none_tooltip=STATE_INHERIT_TIP if none_label == STATE_INHERIT_LABEL else DEFAULTS_NONE_TIP,
        )
        if dlg is None:
            return False, None
        return True, dlg.selected_id()

    def _binding_key(self, binding) -> str:
        """What names *binding* across a reload: the default, or its group's first fight.

        Bindings are rebuilt from disk after the dialog closes, so an object held from
        before is a stale copy; this is how the caller finds the one that replaced it.
        """
        if isinstance(binding, _DefaultBinding) or not binding.state_ids:
            return _DEFAULT_BINDING
        return binding.state_ids[0]

    def _link(self, key: str, dcs_id: str | None) -> None:
        """Point the binding *key* names at *dcs_id*: redraw, regroup, save.

        The bot default has no group of its own, so it only redraws. A fight may have
        just joined or left a group, which is a rebuild of the whole section.
        """
        if self._bcs is None:
            return
        if key == _DEFAULT_BINDING:
            self._bcs.default_strategy_id = dcs_id
            self._default_panel.refresh()
        else:
            self._ensure_state_binding(key).duel_combat_strategy_id = dcs_id
            self._regroup_bindings()
            self._rebuild_state_assignments()
        self._save_bcs()
        if key == _DEFAULT_BINDING:
            # After the save: the banner is read the way a run reads it, from the file.
            self.refresh_source()

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
            dcs = self._lookup().duel(binding.duel_combat_strategy_id)
            return dcs.name if dcs is not None else MISSING_LABEL
        return STATE_INHERIT_LABEL

    def _on_edit_strategy(self, panel: StrategyPanel) -> None:
        """The pencil: open the Bot Combat Strategy editor on this card's strategy (#118).

        On every card, one running a default included. If the strategy selected at Save
        was made on this visit — New or Duplicate — the card now points at it; otherwise
        the card's link is left as it was. So pencil, New, Save is the whole flow for
        giving a card a strategy of its own.
        """
        binding = panel.binding
        if binding is None or self._bcs is None:
            return
        key = self._binding_key(binding)
        dlg = self.open_combat_dialog(
            kind=StrategyKind.DUEL, mode=DialogMode.BOT_STRATEGY, current_id=binding.duel_combat_strategy_id
        )
        if dlg is not None and dlg.selected_id() and dlg.selected_was_created():
            self._link(key, dlg.selected_id())

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
                self._set_bindings(self._bcs.state_assignments if self._bcs else [])
                SettingsManager.instance().set_active_strategy(self._config.id, chosen)
                self._rebuild_state_assignments()
                self._bcs_banner.set_strategy(self._bcs)
                self._refresh_default_panel()
                self._refresh_deck_summaries()

    def _on_clear_pick(self) -> None:
        """Forget a pick whose file is gone, so the run falls to the Official one or the defaults.

        Only the user's press clears it, never loading: clearing on load would write
        ``settings.json`` for a file that might be back on the next start.
        """
        if self._config is None or self._bcs is not None:
            return
        SettingsManager.instance().set_active_strategy(self._config.id, None)
        self.refresh_source()

    def _on_new_bcs(self) -> None:
        if self._config is None:
            return
        name, ok = QInputDialog.getText(self, "New Bot Combat Strategy", "Name:")
        if not ok or not name.strip():
            return
        bcs = BotCombatStrategy(name=name.strip(), bot_id=self._config.id)
        save_bot_strategy(bcs)
        self._all_bcs.append(bcs)
        self._bcs = bcs
        self._set_bindings([])
        SettingsManager.instance().set_active_strategy(self._config.id, bcs.id)
        self._rebuild_state_assignments()
        self._bcs_banner.set_strategy(self._bcs)
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
        self._bcs_banner.set_strategy(self._bcs)

    def _on_duplicate_bcs(self) -> None:
        if self._bcs is None or self._config is None:
            return
        name, ok = QInputDialog.getText(self, "Duplicate Strategy", "New name:", text=f"{self._bcs.name} copy")
        if not ok or not name.strip():
            return
        # Fresh ids for the copy's own strategies, and every link inside it rewritten to
        # follow: sharing them, one Bot Combat Strategy's copy would hide the library's the
        # day the other moved its copy there, lookup checking the bot's own first (#118).
        new_bcs = duplicate_bot_strategy(self._bcs, name.strip())
        save_bot_strategy(new_bcs)
        self._all_bcs.append(new_bcs)
        self._bcs = new_bcs
        self._set_bindings(new_bcs.state_assignments)
        SettingsManager.instance().set_active_strategy(self._config.id, new_bcs.id)
        self._rebuild_state_assignments()
        self._bcs_banner.set_strategy(self._bcs)
        self._refresh_default_panel()

    def _on_delete_bcs(self) -> None:
        if self._bcs is None or self._config is None:
            return
        ans = QMessageBox.question(
            self,
            "Delete Bot Combat Strategy",
            f"Delete '{self._bcs.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        to_delete = self._bcs
        self._all_bcs = [b for b in self._all_bcs if b.id != to_delete.id]
        delete_bot_strategy(to_delete.id)
        own = self._own_bcs()
        self._bcs = own[0] if own else None
        self._set_bindings(self._bcs.state_assignments if self._bcs else [])
        new_active = self._bcs.id if self._bcs else None
        SettingsManager.instance().set_active_strategy(self._config.id, new_active)
        self._rebuild_state_assignments()
        self._bcs_banner.set_strategy(self._bcs)
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

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Start asking about account colors, and ask once now rather than a second late."""
        super().showEvent(event)
        self.refresh_player_colors()
        self._color_timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Stop asking while nobody can see the answer -- Edit mode, or another tab."""
        self._color_timer.stop()
        super().hideEvent(event)

    @property
    def is_polling_player_colors(self) -> bool:
        """Whether the color timer is running. Public for the tests."""
        return self._color_timer.isActive()
