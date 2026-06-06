"""Left sidebar: tab-based panel with Bot, Config, Combat, Properties, and Log tabs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QScrollArea, QTabWidget, QVBoxLayout, QWidget

from bartlebot.gui.sidebar.combat_tab import CombatTab
from bartlebot.gui.sidebar.log_panel import LogPanel
from bartlebot.gui.sidebar.sections.bot_info_section import BotInfoSection
from bartlebot.gui.sidebar.sections.options_section import OptionsSection
from bartlebot.gui.sidebar.sections.possible_drops_section import PossibleDropsSection

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_BOT_TAB = 0
_CONFIG_TAB = 1
_COMBAT_TAB = 2
_PROPERTIES_TAB = 3
_LOG_TAB = 4


class LeftSidebar(QTabWidget):
    """Left sidebar with five tabs: Bot, Config, Combat, Properties, Log.

    - Bot: bot metadata (name, version, description, authors, start, initial/recovery state, possible drops)
    - Config: user-facing run settings (options)
    - Combat: Bot Combat Profiles, Duel Profiles, player deck config
    - Properties: node parameter editor (created externally in BotCanvas and passed in)
    - Log: log viewer
    """

    config_changed = pyqtSignal()

    def __init__(
        self,
        param_panel: QWidget,
        parent=None,
    ):
        super().__init__(parent)
        self.setMinimumWidth(300)
        self._config: BotConfig | None = None

        # ── Bot tab ─────────────────────────────────────────────
        self.bot_info = BotInfoSection()
        self.possible_drops = PossibleDropsSection()

        bot_content = QWidget()
        bot_vbox = QVBoxLayout(bot_content)
        bot_vbox.setContentsMargins(8, 8, 8, 8)
        bot_vbox.setSpacing(4)
        bot_vbox.addWidget(self.bot_info)
        bot_vbox.addWidget(self.possible_drops, 1)  # stretch=1: fills space when expanded
        bot_vbox.addStretch()  # absorbs leftover when possible_drops is collapsed

        # ── Config tab ──────────────────────────────────────────
        self.options = OptionsSection()

        config_content = QWidget()
        config_vbox = QVBoxLayout(config_content)
        config_vbox.setContentsMargins(8, 8, 8, 8)
        config_vbox.setSpacing(4)
        config_vbox.addWidget(self.options)
        config_vbox.addStretch()

        config_scroll = QScrollArea()
        config_scroll.setWidget(config_content)
        config_scroll.setWidgetResizable(True)

        # ── Combat tab ──────────────────────────────────────────
        self.combat_tab = CombatTab()

        self.log_panel = LogPanel()

        self.addTab(bot_content, "Bot")  # _BOT_TAB        = 0
        self.addTab(config_scroll, "Config")  # _CONFIG_TAB     = 1
        self.addTab(self.combat_tab, "Combat")  # _COMBAT_TAB     = 2
        self.addTab(param_panel, "Properties")  # _PROPERTIES_TAB = 3
        self.addTab(self.log_panel, "Log")  # _LOG_TAB        = 4

        # Propagate section-level signals up as the unified config_changed signal.
        self.bot_info.metadata_changed.connect(self.config_changed)
        self.possible_drops.drops_changed.connect(self.config_changed)
        self.options.options_changed.connect(self.config_changed)
        self.combat_tab.combat_config_changed.connect(self.config_changed)

        # Refresh the Combat tab's fight profile list whenever it becomes visible,
        # so that handle_combat nodes edited in the Properties tab are reflected.
        self.currentChanged.connect(self._on_tab_changed)

    # ------------------------------------------------------------------
    # Tab switching (called by BotCanvas.on_tab_switch)
    # ------------------------------------------------------------------

    def switch_to_bot(self) -> None:
        self.setCurrentIndex(_BOT_TAB)

    def switch_to_config(self) -> None:
        self.setCurrentIndex(_CONFIG_TAB)

    def switch_to_combat(self) -> None:
        self.setCurrentIndex(_COMBAT_TAB)

    def switch_to_properties(self) -> None:
        self.setCurrentIndex(_PROPERTIES_TAB)

    def switch_to_log(self) -> None:
        self.setCurrentIndex(_LOG_TAB)

    # ------------------------------------------------------------------
    # Convenience forwarders (dispatch to bot sections)
    # ------------------------------------------------------------------

    def _on_tab_changed(self, index: int) -> None:
        if index == _COMBAT_TAB:
            self.combat_tab.refresh_fight_profiles()

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self.bot_info.set_config(config)
        self.possible_drops.set_config(config)
        self.options.set_config(config)
        self.combat_tab.set_config(config)

    def set_editable(self, editable: bool) -> None:
        self.bot_info.set_editable(editable)
        self.possible_drops.set_editable(editable)
        self.options.set_editable(editable)
        # Combat tab is intentionally not gated by edit mode — profiles are user config, not bot structure.

    def set_bot_running(self, running: bool) -> None:
        """Lock the combat tab while the bot is actively running."""
        self.combat_tab.set_editable(not running)
