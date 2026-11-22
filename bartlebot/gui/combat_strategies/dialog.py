"""BartleBot's Combat Strategies window: core's, with the Bot Combat Strategy in it.

The window is core's (:mod:`bartleby.qt.combat.dialog`, BartleBuddy #31, Stage 6), whose
docstring says why it is a dialog, what its modes do, and where a product plugs in. What is
here is what only BartleBot has:

``BOT_STRATEGY``  ``[Cancel] [Save]`` — the ✏ on a Combat tab card. The Duel and Player
             tabs, with the active Bot Combat Strategy's own strategies in a section at the
             top. New and Duplicate create *in it*; library entries stay editable too, and
             the Save confirmation says where else an edit reaches.

The other two are core's: ``LIBRARY`` is **File → Combat Strategies**, with no This bot
section even with a bot loaded, and ``PICK`` is 🔍 on a card, a fight's pill, the add-card's
"Select existing duel strategy…", a deck slot's "Load from library", and Settings › Combat's
Change….

TWO PLACES A STRATEGY CAN LIVE (#118)
-------------------------------------

A strategy belongs to the library, which every bot sees, or to one Bot Combat
Strategy. Users who don't realize there are two places struggle, so the difference is
drawn everywhere it can be: the bot's own section is headed with the Bot Combat
Strategy's name, each of its cards wears a **This bot** pill in its own color (not the
error red), an empty section says what it is for, and the About pane says "In 'Solo'
only" beside **Add to library**. Creating locally and publishing
separately is the shape design tools settled on — Figma's components, Illustrator's
swatches — and it keeps the one-off from ever cluttering the shared list. Favorites are
library-only, so an entry of the bot's own has no star.

EXPORT (#170)
-------------

A card's right-click offers Export…, in every mode: it changes nothing. A share file is
BartleBot's, so core's card has no menu of its own.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from bartleby.qt.combat import dialog as core_dialog
from bartleby.qt.combat.deck_link import DeckClientLink
from bartleby.qt.combat.dialog import (
    LIBRARY_HEADING,
    NONE_LABEL,
    NONE_TOOLTIP,
    AboutSection,
    FlowStep,
    KindPage,
)
from bartleby.qt.combat.dialog import CombatStrategyDialog as CoreCombatStrategyDialog
from bartleby.qt.menu_utils import popup_menu
from bartleby.qt.widgets.buttons.action_button import ActionButton, ActionStyle
from bartleby.qt.widgets.cards.strategy_card import StrategyCard
from bartleby.qt.widgets.labels.elided_label import apply_muted_color
from bartleby.runtime.combat.strategy_summary import StrategySummary, name_list
from bartleby.runtime.combat.strategy_workset import OWN_KINDS, StrategyOwner, StrategyWorkingSet
from bartleby.shared.strategy_library import StrategyKind
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox, QWidget

from bartlebot.combat.bot_strategy_owner import THIS_BOT_SOURCE, BotStrategyOwner, bot_strategy_of
from bartlebot.combat.deck_io import app_default_apply_archmastery
from bartlebot.content.share_export import ExportPlan, Sources, export_deck, export_duel, export_player
from bartlebot.gui.context_help import set_help_topic
from bartlebot.gui.dialogs.export_dialog import run_export
from bartlebot.gui.widgets.pills.tag_pill import this_bot_pill
from bartlebot.settings_manager import SettingsManager


class DialogMode(Enum):
    """Which way in the dialog was opened by: core's two, and the Bot Combat Strategy's editor."""

    LIBRARY = core_dialog.DialogMode.LIBRARY
    BOT_STRATEGY = "bot_strategy"
    PICK = core_dialog.DialogMode.PICK

    @property
    def core(self) -> core_dialog.DialogMode:
        """The core mode this way in is: the Bot Combat Strategy's editor edits, as Library does."""
        return self.value if isinstance(self.value, core_dialog.DialogMode) else core_dialog.DialogMode.LIBRARY


def tabs_for(mode: DialogMode, kind: StrategyKind) -> tuple[StrategyKind, ...]:
    """The tabs a way in shows, in order: core's, and for a Bot Combat Strategy the two kinds one can hold."""
    if mode is DialogMode.BOT_STRATEGY:
        return (StrategyKind.DUEL, StrategyKind.PLAYER)
    return core_dialog.tabs_for(mode.core, kind)


#: The one muted line an empty This bot section shows (#118).
BOT_STRATEGY_EMPTY_NOTE = "Strategies made here belong to this Bot Combat Strategy. Use Add to library to share one."
#: What the This bot section says for a loaded bot that has no Bot Combat Strategy yet.
NO_BOT_STRATEGY_NOTE = "This bot has no Bot Combat Strategy yet."
CREATE_BOT_STRATEGY_TEXT = "Create one"
#: The section key and heading fallback of the bot's own entries. The section's heading is
#: the Bot Combat Strategy's name.
THIS_BOT_SECTION = "this_bot"
_THIS_BOT_HEADING = "Bot Combat Strategy"
#: The About pane's verb for an entry of the bot's own.
ADD_TO_LIBRARY_TEXT = "Add to library"

#: A card's right-click (#170), and why it is off on an entry with unsaved edits.
EXPORT_TEXT = "Export…"
EXPORT_UNSAVED_TIP = "Save your changes first: Export sends what is saved."
#: Why New and Duplicate are grayed out in the Bot Combat Strategy editor before one exists.
_NO_BOT_STRATEGY_TIP = "Create a Bot Combat Strategy for this bot first"


def move_confirmation(entry_name: str, moving: list[str]) -> str:
    """What Add to library says before moving a duel that takes player strategies with it.

    Numerals throughout, never "2" beside "three" (#118): *moving* are the names of the
    bot's own player strategies the duel uses.
    """
    count = len(moving)
    noun = "player strategy" if count == 1 else "player strategies"
    names = name_list([f"'{n}'" for n in moving], limit=len(moving))
    return f"'{entry_name}' uses {count} {noun} from this bot: {names}. All {count + 1} will be moved to the library."


class BotStrategyCard(StrategyCard):
    """Core's card, with the This bot pill on an entry of the bot's own."""

    def owner_pill(self, owner_name: str) -> QLabel:
        return this_bot_pill(owner_name)


class BotAboutSection(AboutSection):
    """Core's About section, with **Add to library** beside the line that says the entry is the bot's own (#118)."""

    add_to_library_requested = pyqtSignal()

    def _build_origin_extras(self, row: QHBoxLayout) -> None:
        # Beside the line that says the entry belongs to one Bot Combat Strategy, because
        # that is the sentence it answers.
        self._add_btn = ActionButton("upload", ADD_TO_LIBRARY_TEXT, style=ActionStyle.SECONDARY, fill=False)
        self._add_btn.setToolTip("Move this into the library, where every bot can use it")
        self._add_btn.clicked.connect(self.add_to_library_requested)
        self._add_btn.setVisible(False)
        row.addWidget(self._add_btn, 0, Qt.AlignmentFlag.AlignTop)

    def clear(self) -> None:
        super().clear()
        self._add_btn.setVisible(False)

    def set_notes(self, usage: str, origin: str, *, can_add_to_library: bool = False) -> None:
        """Core's two lines, and whether **Add to library** sits beside the second."""
        super().set_notes(usage, origin)
        self._add_btn.setVisible(can_add_to_library)

    @property
    def add_to_library_button(self) -> ActionButton:
        """Add to library. Public so a test can press it and see whether it is offered."""
        return self._add_btn


class BotKindPage(KindPage):
    """Core's tab, with the active Bot Combat Strategy's own entries in a section at the top.

    *show_own* draws that section, and makes New and Duplicate create there (#118).
    """

    #: The This bot section's "Create one", on a bot with no Bot Combat Strategy yet.
    create_bot_strategy_requested = pyqtSignal()

    own_source = THIS_BOT_SOURCE

    def __init__(self, kind: StrategyKind, ws: StrategyWorkingSet, *args, show_own: bool = False, **kwargs):
        # Before core's, which draws the page.
        self._show_own = show_own and kind in OWN_KINDS
        super().__init__(kind, ws, *args, **kwargs)

    # -- hooks ----------------------------------------------------------

    def _filter_source(self) -> str | None:
        return self.own_source if self._show_own else None

    def _make_about(self) -> AboutSection:
        about = BotAboutSection(tag_groups=self._about_groups())
        about.add_to_library_requested.connect(self._on_add_to_library)
        return about

    def _new_card(self, summary: StrategySummary, favorite: bool | None) -> StrategyCard:
        return BotStrategyCard(summary, favorite=favorite)

    def _favorable(self, section: str) -> bool:
        # No star on an entry of the bot's own: favorites are a preference about the library.
        return section != THIS_BOT_SECTION

    @property
    def creates_own(self) -> bool:
        """Whether New and Duplicate put their entry in the Bot Combat Strategy rather than the library."""
        return self._show_own

    def _create_block(self) -> str:
        return _NO_BOT_STRATEGY_TIP if self._show_own and self._ws.owner is None else ""

    def _sections(self) -> list[tuple[str, str, list]]:
        """This bot first, then core's ★ Favorites and the library (#118).

        Favorites are library-only, and a library entry the bot's own shadows is not drawn
        twice: its own copy is the one every lookup finds.
        """
        if not self._show_own:
            return self._library_sections()
        own = self._ws.own_items(self._kind)
        heading = self._ws.owner.name if self._ws.owner is not None else _THIS_BOT_HEADING
        return [
            (THIS_BOT_SECTION, heading, own),
            *self._library_sections({e.id for e in own}, LIBRARY_HEADING),
        ]

    def _on_empty_section(self, key: str) -> None:
        """The This bot section's one row when it holds nothing: what it is for, or an offer.

        With a Bot Combat Strategy, one muted line saying what the section is for. With
        none yet, the same place says so, and — in its editor — offers to make one. Pick shows
        the sentence without the button: it edits nothing.
        """
        if key != THIS_BOT_SECTION:
            return
        row = QWidget()
        box = QHBoxLayout(row)
        box.setContentsMargins(4, 2, 4, 2)
        box.setSpacing(6)
        no_bot_strategy = self._ws.owner is None
        label = QLabel(NO_BOT_STRATEGY_NOTE if no_bot_strategy else BOT_STRATEGY_EMPTY_NOTE, row)
        label.setWordWrap(True)
        apply_muted_color(label)
        box.addWidget(label, 1)
        if no_bot_strategy and self._editable:
            button = ActionButton("plus", CREATE_BOT_STRATEGY_TEXT, style=ActionStyle.SECONDARY, fill=False)
            button.clicked.connect(self.create_bot_strategy_requested)
            box.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._add_section_note(key, row)

    def _set_own_notes(self, usage: str) -> None:
        # The section heading names the Bot Combat Strategy, so the name alone is enough.
        self._about.set_notes(usage, f"In '{self._ws.owner.name}' only", can_add_to_library=self._editable)

    # -- reading --------------------------------------------------------

    def note_text(self) -> str:
        """What the This bot section's note row says, or empty when it has none."""
        return self.section_note_text(THIS_BOT_SECTION)

    # -- the bot's own --------------------------------------------------

    def _on_add_to_library(self) -> None:
        """Move the selected entry of the bot's own into the library, keeping its id (#118).

        A duel takes the bot's own player strategies it uses with it — a library duel
        may not link a bot's own content — and says so first, naming them. It is an edit like
        any other: it reaches disk at Save, and Cancel undoes it.
        """
        entry = self._ws.get(self._kind, self._selected_id)
        if entry is None or not self._ws.is_own_entry(self._kind, entry.id):
            return
        moving = self._ws.own_players_of(entry) if self._kind is StrategyKind.DUEL else []
        if moving:
            answer = QMessageBox.question(
                self,
                "Add to library?",
                move_confirmation(entry.name, [p.name for p in moving]),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer is not QMessageBox.StandardButton.Yes:
                return
        self._ws.move_to_library(self._kind, entry.id)
        self.rebuild_list()
        self.select(entry.id)
        self.structure_changed.emit()

    # -- export ---------------------------------------------------------

    def card_menu(self, uuid: str) -> QMenu:
        """The menu a card shows on a right-click. Public so a test can read it.

        Export sends what is saved, so an entry with edits the dialog has not saved yet
        says so instead.
        """
        menu = popup_menu(self)
        menu.setToolTipsVisible(True)
        act = menu.addAction(EXPORT_TEXT)
        if self._ws.has_unsaved(self._kind, uuid):
            act.setEnabled(False)
            act.setToolTip(EXPORT_UNSAVED_TIP)
        act.triggered.connect(lambda: self.export(uuid))
        return menu

    def export_plan(self, uuid: str) -> ExportPlan | None:
        """What exporting *uuid* would write: the entry as saved, and what it links. None if it is gone."""
        entry = self._ws.get(self._kind, uuid)
        if entry is None:
            return None
        root, user_root = self._ws.roots
        sources = Sources(strategy_root=root, strategy_user_root=user_root)
        if self._kind is StrategyKind.DUEL:
            # One of the bot's own finds its links there first, as a run does.
            owner = bot_strategy_of(self._ws) if self._ws.is_own_entry(self._kind, uuid) else None
            return export_duel(entry, sources, bot_strategy=owner)
        if self._kind is StrategyKind.PLAYER:
            return export_player(entry, sources)
        return export_deck(entry, sources)

    def export(self, uuid: str):
        """Export… on a card: the summary, then the file. The path written, or None."""
        if self._ws.get(self._kind, uuid) is None:
            return None
        return run_export(self, lambda _choices: self.export_plan(uuid))


class CombatStrategyDialog(CoreCombatStrategyDialog):
    """Core's window, with BartleBot's third way in. See the module docstring.

    *bot_name* turns a bot id into its name for the Save confirmation, so another Bot
    Combat Strategy reads as ``'Solo' (Atlantea)``; without it its name stands alone. The
    rest is core's.
    """

    def __init__(
        self,
        ws: StrategyWorkingSet,
        kind: StrategyKind = StrategyKind.DUEL,
        mode: DialogMode = DialogMode.LIBRARY,
        current_id: str | None = None,
        allow_none: bool = True,
        none_label: str = NONE_LABEL,
        player_color_keys: list[int | None] | None = None,
        player_account_labels: list[str] | None = None,
        bot_name: Callable[[str], str | None] | None = None,
        deck_link: DeckClientLink | None = None,
        parent=None,
        *,
        none_tooltip: str = NONE_TOOLTIP,
        team_size: int | None = None,
        flow: FlowStep | None = None,
        start_new: bool = False,
        start_from: str | None = None,
    ):
        # Before core's, which builds the tabs.
        self._way_in = mode
        self._bot_name = bot_name
        super().__init__(
            ws,
            SettingsManager.instance(),
            kind,
            mode.core,
            current_id,
            allow_none,
            none_label,
            player_color_keys,
            player_account_labels,
            deck_link,
            parent,
            none_tooltip=none_tooltip,
            team_size=team_size,
            flow=flow,
            start_new=start_new,
            start_from=start_from,
            archmastery_default=app_default_apply_archmastery,
        )

    @property
    def mode(self) -> DialogMode:
        """The way in it was opened by."""
        return self._way_in

    # -- hooks ----------------------------------------------------------

    def _prepare_window(self) -> None:
        # Before the tabs are built: the window flag it sets lays out what is already there.
        set_help_topic(self, "combat_strategies")

    def _tabs_shown(self) -> tuple[StrategyKind, ...]:
        return tabs_for(self._way_in, self._pick_kind)

    def _make_page(self, kind: StrategyKind) -> KindPage:
        page = BotKindPage(
            kind,
            self._ws,
            self._settings,
            editable=not self.picks,
            archmastery_default=self._archmastery_default,
            # The This bot section is for the ways in from the Combat tab, and only when a bot
            # is loaded. The library view never shows it (#118).
            show_own=self._way_in is not DialogMode.LIBRARY and self._ws.takes_owner,
        )
        page.create_bot_strategy_requested.connect(self._on_create_bot_strategy)
        return page

    def _result_page(self) -> KindPage | None:
        if self._way_in is DialogMode.BOT_STRATEGY and self._pick_kind in self._pages:
            # The kind the caller asked about, whichever tab is showing: the Combat tab
            # links the card that opened this to the duel selected here, if it is new.
            return self._pages[self._pick_kind]
        return super()._result_page()

    def _owner_open(self) -> bool:
        return self._way_in is not DialogMode.LIBRARY and self._ws.owner is not None

    def _owner_label(self, owner: StrategyOwner) -> str | None:
        """The name of the bot *owner*'s Bot Combat Strategy is for, as the Save confirmation names it."""
        if self._bot_name is None or not isinstance(owner, BotStrategyOwner):
            return None
        return self._bot_name(owner.strategy.bot_id)

    # -- the Bot Combat Strategy ----------------------------------------

    def _on_create_bot_strategy(self) -> None:
        """Make a Bot Combat Strategy for a bot that has none — inside the working set, so
        Cancel discards it like any other edit. It gives every tab a section to fill."""
        if not self._ws.takes_owner or self._ws.owner is not None:
            return
        name, ok = QInputDialog.getText(self, "New Bot Combat Strategy", "Name:", text="Default")
        if not ok or not name.strip():
            return
        self._ws.create_owner(name.strip())
        for page in self._pages.values():
            page.rebuild_list()
