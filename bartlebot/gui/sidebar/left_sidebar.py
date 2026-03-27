"""Left sidebar: tab-based panel with Bot, Properties, and Log tabs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QScrollArea, QTabWidget, QVBoxLayout, QWidget

from bartlebot.gui.sidebar.log_panel import LogPanel
from bartlebot.gui.sidebar.sections.bot_info_section import BotInfoSection
from bartlebot.gui.sidebar.sections.combat_config_section import CombatConfigSection
from bartlebot.gui.sidebar.sections.options_section import OptionsSection

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_BOT_TAB = 0
_PROPERTIES_TAB = 1
_LOG_TAB = 2


class LeftSidebar(QTabWidget):
    """Left sidebar with three tabs: Bot, Properties, Log.

    The Properties widget is created externally (in BotCanvas) and passed in.
    The Log tab is owned here as a LogPanel instance.
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
        self.options = OptionsSection()
        self.combat_config = CombatConfigSection()

        bot_content = QWidget()
        bot_vbox = QVBoxLayout(bot_content)
        bot_vbox.setContentsMargins(8, 8, 8, 8)
        bot_vbox.setSpacing(4)
        bot_vbox.addWidget(self.bot_info)
        bot_vbox.addWidget(self.options)
        bot_vbox.addWidget(self.combat_config)
        bot_vbox.addStretch()

        bot_scroll = QScrollArea()
        bot_scroll.setWidget(bot_content)
        bot_scroll.setWidgetResizable(True)

        self.log_panel = LogPanel()

        self.addTab(bot_scroll, "Bot")  # _BOT_TAB        = 0
        self.addTab(param_panel, "Properties")  # _PROPERTIES_TAB = 1
        self.addTab(self.log_panel, "Log")  # _LOG_TAB        = 2

        # Propagate config_changed from any sub-section upward
        self.bot_info.config_changed.connect(self.config_changed)
        self.options.config_changed.connect(self.config_changed)
        self.combat_config.config_changed.connect(self.config_changed)

    # ------------------------------------------------------------------
    # Tab switching (called by BotCanvas.on_tab_switch)
    # ------------------------------------------------------------------

    def switch_to_bot(self) -> None:
        self.setCurrentIndex(_BOT_TAB)

    def switch_to_properties(self) -> None:
        self.setCurrentIndex(_PROPERTIES_TAB)

    def switch_to_log(self) -> None:
        self.setCurrentIndex(_LOG_TAB)

    # ------------------------------------------------------------------
    # Convenience forwarders (dispatch to bot sections)
    # ------------------------------------------------------------------

    def set_config(self, config: BotConfig | None) -> None:
        self.bot_info.set_config(config)
        self.options.set_config(config)
        self.combat_config.set_config(config)

    def set_editable(self, editable: bool) -> None:
        self.bot_info.set_editable(editable)
        self.options.set_editable(editable)
        self.combat_config.set_editable(editable)
