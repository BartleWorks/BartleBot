"""Right sidebar: tab-based panel with Players and Drops tabs."""

from PyQt6.QtWidgets import QScrollArea, QTabWidget, QVBoxLayout, QWidget

from bartlebot.gui.sidebar.sections.client_section import ClientSection
from bartlebot.gui.sidebar.sections.drop_section import DropSection

_PLAYERS_TAB = 0
_DROPS_TAB = 1


class RightSidebar(QTabWidget):
    """Right sidebar with two tabs: Players and Drops.

    These were previously stacked collapsible sections, but four player cards
    leave no usable height for the drop table, so the stack already forced an
    either/or choice — one that had to be re-made by hand every time.  Tabs make
    it explicit and give each side the full panel height.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(300)

        self.clients = ClientSection()
        self.drops = DropSection()

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

        self.addTab(players_scroll, "Players")  # _PLAYERS_TAB = 0
        # Drops goes in unwrapped: its tables scroll internally, and a second
        # scroll area around them would produce two competing scrollbars.
        self.addTab(self.drops, "Drops")  # _DROPS_TAB   = 1

        self.currentChanged.connect(self._on_tab_changed)

    # ------------------------------------------------------------------
    # Tab switching
    # ------------------------------------------------------------------

    def switch_to_players(self) -> None:
        self.setCurrentIndex(_PLAYERS_TAB)

    def switch_to_drops(self) -> None:
        self.setCurrentIndex(_DROPS_TAB)

    def _on_tab_changed(self, index: int) -> None:
        # Let the client section back its polling off while it is off screen.
        self.clients.set_players_visible(index == _PLAYERS_TAB)
