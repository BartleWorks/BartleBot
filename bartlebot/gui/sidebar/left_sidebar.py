"""Left sidebar: tab-based panel with Bot, Config, Properties, and Log tabs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QScrollArea, QTabWidget, QVBoxLayout, QWidget

from bartlebot.gui.sidebar.log_panel import LogPanel
from bartlebot.gui.sidebar.sections.bot_info_section import BotInfoSection
from bartlebot.gui.sidebar.sections.combat_config_section import CombatConfigSection
from bartlebot.gui.sidebar.sections.options_section import OptionsSection
from bartlebot.gui.sidebar.sections.possible_drops_section import PossibleDropsSection

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_BOT_TAB = 0
_CONFIG_TAB = 1
_PROPERTIES_TAB = 2
_LOG_TAB = 3


class LeftSidebar(QTabWidget):
    """Left sidebar with four tabs: Bot, Config, Properties, Log.

    - Bot: bot metadata (name, version, description, authors, start, initial/recovery state, possible drops)
    - Config: user-facing run settings (options, combat config)
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

        # ── Bot tab ─────────────────────────────────────────────
        self.bot_info = BotInfoSection()
        self.possible_drops = PossibleDropsSection()

        bot_content = QWidget()
        bot_vbox = QVBoxLayout(bot_content)
        bot_vbox.setContentsMargins(8, 8, 8, 8)
        bot_vbox.setSpacing(4)
        bot_vbox.addWidget(self.bot_info)
        bot_vbox.addWidget(self.possible_drops)
        bot_vbox.addStretch()

        bot_scroll = QScrollArea()
        bot_scroll.setWidget(bot_content)
        bot_scroll.setWidgetResizable(True)

        # ── Config tab ──────────────────────────────────────────
        self.options = OptionsSection()
        self.combat_config = CombatConfigSection()

        config_content = QWidget()
        config_vbox = QVBoxLayout(config_content)
        config_vbox.setContentsMargins(8, 8, 8, 8)
        config_vbox.setSpacing(4)
        config_vbox.addWidget(self.options)
        config_vbox.addWidget(self.combat_config)
        config_vbox.addStretch()

        config_scroll = QScrollArea()
        config_scroll.setWidget(config_content)
        config_scroll.setWidgetResizable(True)

        self.log_panel = LogPanel()

        self.addTab(bot_scroll, "Bot")  # _BOT_TAB        = 0
        self.addTab(config_scroll, "Config")  # _CONFIG_TAB     = 1
        self.addTab(param_panel, "Properties")  # _PROPERTIES_TAB = 2
        self.addTab(self.log_panel, "Log")  # _LOG_TAB        = 3

        # Propagate section-level signals up as the unified config_changed signal.
        self.bot_info.metadata_changed.connect(self.config_changed)
        self.possible_drops.drops_changed.connect(self.config_changed)
        self.options.options_changed.connect(self.config_changed)
        self.combat_config.combat_config_changed.connect(self.config_changed)

    # ------------------------------------------------------------------
    # Tab switching (called by BotCanvas.on_tab_switch)
    # ------------------------------------------------------------------

    def switch_to_bot(self) -> None:
        self.setCurrentIndex(_BOT_TAB)

    def switch_to_config(self) -> None:
        self.setCurrentIndex(_CONFIG_TAB)

    def switch_to_properties(self) -> None:
        self.setCurrentIndex(_PROPERTIES_TAB)

    def switch_to_log(self) -> None:
        self.setCurrentIndex(_LOG_TAB)

    # ------------------------------------------------------------------
    # Convenience forwarders (dispatch to bot sections)
    # ------------------------------------------------------------------

    def set_config(self, config: BotConfig | None) -> None:
        self.bot_info.set_config(config)
        self.possible_drops.set_config(config)
        self.options.set_config(config)
        self.combat_config.set_config(config)

    def set_editable(self, editable: bool) -> None:
        self.bot_info.set_editable(editable)
        self.possible_drops.set_editable(editable)
        self.options.set_editable(editable)
        self.combat_config.set_editable(editable)
