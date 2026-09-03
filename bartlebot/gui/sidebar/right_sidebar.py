"""Right sidebar: tab-based panel with Players, Drops and Log tabs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QScrollArea, QTabWidget, QVBoxLayout, QWidget

from bartlebot.gui.sidebar.log_panel import LogPanel
from bartlebot.gui.sidebar.sections.client_section import ClientSection
from bartlebot.gui.sidebar.sections.drop_section import DropSection
from bartlebot.gui.sidebar.sections.possible_drops_section import PossibleDropsSection

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_PLAYERS_TAB = 0
_DROPS_TAB = 1
_LOG_TAB = 2


class RightSidebar(QTabWidget):
    """Right sidebar with three tabs: Players, Drops and Log.

    These were previously stacked collapsible sections, but four player cards
    leave no usable height for the drop table, so the stack already forced an
    either/or choice — one that had to be re-made by hand every time.  Tabs make
    it explicit and give each side the full panel height.

    **Log** moved here from the left sidebar (#80).  The left one is now the bot's
    configuration and nothing else, and a log you have to close Setup to read is a
    log nobody watches while a run is going.

    **Possible Drops** — the author's list of what this bot can drop — sits above
    the live per-client drop tables in the Drops tab, because the two answer the
    same question at different times: what this run *could* yield, and what it
    has.  It is the one piece of the old Bot tab that is not metadata about the
    file, so it did not follow the rest into the header.
    """

    # Emitted when the *bot file* is edited from this panel -- which today means the
    # possible-drops list, the only authorial thing over here.
    config_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(300)

        self.clients = ClientSection()
        self.drops = DropSection()
        self.possible_drops = PossibleDropsSection()
        self.log_panel = LogPanel()

        # ── Players tab ─────────────────────────────────────────
        players_content = QWidget()
        players_vbox = QVBoxLayout(players_content)
        players_vbox.setContentsMargins(8, 8, 8, 8)
        players_vbox.setSpacing(4)
        players_vbox.addWidget(self.clients)
        players_vbox.addStretch()  # keeps the cards top-aligned

        players_scroll = QScrollArea()
        players_scroll.setWidget(players_content)
        players_scroll.setWidgetResizable(True)

        # ── Drops tab ───────────────────────────────────────────
        # DropSection goes in unwrapped: its tables scroll internally, and a second
        # scroll area around them would produce two competing scrollbars.  The
        # possible-drops list is collapsible and sits above it.
        drops_page = QWidget()
        drops_vbox = QVBoxLayout(drops_page)
        drops_vbox.setContentsMargins(0, 0, 0, 0)
        drops_vbox.setSpacing(0)
        drops_vbox.addWidget(self.possible_drops)
        drops_vbox.addWidget(self.drops, 1)

        self.addTab(players_scroll, "Players")  # _PLAYERS_TAB = 0
        self.addTab(drops_page, "Drops")  # _DROPS_TAB   = 1
        self.addTab(self.log_panel, "Log")  # _LOG_TAB     = 2

        self.possible_drops.drops_changed.connect(self.config_changed)
        self.currentChanged.connect(self._on_tab_changed)

    # ------------------------------------------------------------------
    # Tab switching
    # ------------------------------------------------------------------

    def switch_to_players(self) -> None:
        self.setCurrentIndex(_PLAYERS_TAB)

    def switch_to_drops(self) -> None:
        self.setCurrentIndex(_DROPS_TAB)

    def switch_to_log(self) -> None:
        self.setCurrentIndex(_LOG_TAB)

    # ------------------------------------------------------------------
    # Bot binding
    # ------------------------------------------------------------------

    def set_config(self, config: BotConfig | None) -> None:
        self.possible_drops.set_config(config)

    def set_editable(self, editable: bool) -> None:
        self.possible_drops.set_editable(editable)

    def _on_tab_changed(self, index: int) -> None:
        # Let the client section back its polling off while it is off screen.
        self.clients.set_players_visible(index == _PLAYERS_TAB)
