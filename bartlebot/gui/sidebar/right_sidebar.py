"""Right sidebar: tab-based panel with Players, Drops and Log tabs, and Setup while a template's steps apply."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bartleby.runtime.game.game_sizing import SIDEBAR_MIN_HEIGHT
from PyQt6.QtCore import QEvent, pyqtSignal
from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from bartlebot.gui import screen_fit
from bartlebot.gui.sidebar.log_panel import LogPanel
from bartlebot.gui.sidebar.sections.client_section import ClientSection
from bartlebot.gui.sidebar.sections.drop_section import DropSection
from bartlebot.gui.sidebar.sections.possible_drops_section import PossibleDropsSection
from bartlebot.gui.sidebar.setup_panel import SetupPanel

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_PLAYERS_TAB = 0
_DROPS_TAB = 1
_LOG_TAB = 2
#: The Setup tab's label. Last, so the three fixed tabs keep their indices with or without it.
_SETUP_LABEL = "Setup"


class RightSidebar(QTabWidget):
    """Right sidebar with three tabs: Players, Drops and Log.

    These were previously stacked collapsible sections, but four player cards
    leave no usable height for the drop table, so the stack already forced an
    either/or choice — one that had to be re-made by hand every time.  Tabs make
    it explicit and give each side the full panel height.

    **Log** moved here from the left sidebar.  The left one is now the bot's
    configuration and nothing else, and a log you have to close Option Values to read is a
    log nobody watches while a run is going.

    **Possible Drops** — the author's list of what this bot can drop — sits above
    the live per-client drop tables in the Drops tab, because the two answer the
    same question at different times: what this run *could* yield, and what it
    has.  It is the one piece of the old Bot tab that is not metadata about the
    file, so it did not follow the rest into the header.

    **Setup** is there only while it has something to say: a template draft's steps to
    edit, or the steps a bot made from a template still has to do. It is over here, not
    a page of Properties, so the list stays on screen while Properties shows the node a
    step points at (``setup_panel.py``). A ``QTabWidget`` cannot hide a tab, so it is
    added and removed, and parked on a hidden child in between, as the left sidebar
    parks the tabs its mode does not offer.
    """

    # Emitted when the *bot file* is edited from this panel -- which today means the
    # possible-drops list, the only authorial thing over here.
    config_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # The arithmetic's term, set explicitly so it is the floor Qt enforces -- see the
        # same call in LeftSidebar, and "The panel floors" in game_sizing.py. Measured
        # rather than fixed because how wide it needs to be is font and style metrics.
        #
        # Measured from the Log tab's header row, which is the widest row here that
        # neither elides nor wraps. It was the player card's tool row until that card was
        # reworked into icon verbs and elided rows; the floor went on being a button's
        # width scaled over the whole panel long after the row was gone, which charged
        # every larger font against content that had not moved. See
        # ``screen_fit.right_sidebar_floor``.
        self._apply_width_floor()

        self.clients = ClientSection()
        self.drops = DropSection()
        self.possible_drops = PossibleDropsSection()
        self.log_panel = LogPanel()
        self.setup_panel = SetupPanel()
        self._parked = QWidget(self)
        self._parked.setVisible(False)
        self.setup_panel.setParent(self._parked)

        # ── Players tab ─────────────────────────────────────────
        # ClientSection goes in unwrapped, exactly as DropSection does below: its card
        # column scrolls internally, and a second scroll area around it would produce two
        # competing scrollbars -- one for the column and one for the page holding it, both
        # live, neither covering the whole content.
        players_page = QWidget()
        players_vbox = QVBoxLayout(players_page)
        players_vbox.setContentsMargins(8, 8, 8, 8)
        players_vbox.setSpacing(4)
        players_vbox.addWidget(self.clients, 1)

        # ── Drops tab ───────────────────────────────────────────
        # DropSection goes in unwrapped: its tables scroll internally, and a second
        # scroll area around them would produce two competing scrollbars.  The
        # possible-drops list is collapsible and sits above it.
        #
        # The page owns the gutter for both, as the Players tab does -- Possible Drops
        # is a SidebarSection and carries no side padding of its own, so leaving it to
        # the children put its table flush against the sidebar's edges.
        drops_page = QWidget()
        drops_vbox = QVBoxLayout(drops_page)
        drops_vbox.setContentsMargins(8, 8, 8, 8)
        drops_vbox.setSpacing(4)
        drops_vbox.addWidget(self.possible_drops)
        drops_vbox.addWidget(self.drops, 1)

        self.addTab(players_page, "Players")  # _PLAYERS_TAB = 0
        self.addTab(drops_page, "Drops")  # _DROPS_TAB   = 1
        self.addTab(self.log_panel, "Log")  # _LOG_TAB     = 2

        self.possible_drops.drops_changed.connect(self.config_changed)
        self.currentChanged.connect(self._on_tab_changed)

    def _apply_width_floor(self) -> None:
        self.setMinimumSize(screen_fit.right_sidebar_floor(), SIDEBAR_MIN_HEIGHT)

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Re-measure the floor when the text size under it changes.

        The theme is applied to the whole application after this widget is built, and a
        theme carrying a different font would otherwise leave the panel holding a floor
        measured against the old one -- narrower than its own buttons, which is the state
        this floor exists to prevent.
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self._apply_width_floor()

    # ------------------------------------------------------------------
    # Tab switching
    # ------------------------------------------------------------------

    def switch_to_players(self) -> None:
        self.setCurrentIndex(_PLAYERS_TAB)

    def switch_to_drops(self) -> None:
        self.setCurrentIndex(_DROPS_TAB)

    def switch_to_log(self) -> None:
        self.setCurrentIndex(_LOG_TAB)

    @property
    def setup_tab_shown(self) -> bool:
        return self.indexOf(self.setup_panel) >= 0

    def set_setup_tab_shown(self, shown: bool) -> None:
        """Add the Setup tab after the others, or take it away. Leaves the current tab alone where it can."""
        if shown == self.setup_tab_shown:
            return
        if shown:
            self.addTab(self.setup_panel, _SETUP_LABEL)
        else:
            was_current = self.currentWidget() is self.setup_panel
            self.removeTab(self.indexOf(self.setup_panel))
            # removeTab leaves the page parentless, which is a top-level window.
            self.setup_panel.setParent(self._parked)
            if was_current:
                self.setCurrentIndex(_PLAYERS_TAB)

    def switch_to_setup(self) -> None:
        """Bring the Setup tab forward, if it is there."""
        if self.setup_tab_shown:
            self.setCurrentWidget(self.setup_panel)

    # ------------------------------------------------------------------
    # Bot binding
    # ------------------------------------------------------------------

    def set_config(self, config: BotConfig | None) -> None:
        self.possible_drops.set_config(config)
        # The live tables need it too, for the keep-list filter over this bot's drops.
        self.drops.set_config(config)

    def sync_config(self, config: BotConfig | None) -> None:
        """Rebind Possible Drops if *config* is a different object than it holds.

        The right-hand half of ``LeftSidebar.sync_config``, and the same bug: undo/redo
        hands the canvas a brand-new ``BotConfig``, and a list left bound to the old one
        shows every drop added to it while the Option Values keep-list picker and the saved file
        never see them.  The wiki import makes it look worse than it is, because the
        page it records goes onto the node -- the canvas's object -- and does save.
        """
        if config is self.possible_drops.config:
            return
        self.set_config(config)

    def set_editable(self, editable: bool) -> None:
        self.possible_drops.set_editable(editable)

    def _on_tab_changed(self, index: int) -> None:
        # Let the client section back its polling off while it is off screen.
        self.clients.set_players_visible(index == _PLAYERS_TAB)
        if index == _DROPS_TAB:
            # The keep list is a bot option edited over in Option Values, which does not report
            # here. Coming forward is the moment the filter has to be right, so it is
            # the moment to re-resolve it.
            self.drops.refresh_keep_list()
