"""Left sidebar: assembles bot info, options, and combat config sections."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from bartlebot.gui.bot_info_section import BotInfoSection
from bartlebot.gui.combat_config_section import CombatConfigSection
from bartlebot.gui.options_section import OptionsSection
from bartlebot.gui.sidebar_section import Sidebar

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig


class LeftSidebar(Sidebar):
    """Left sidebar containing bot metadata, options, and combat config sections."""

    def __init__(self, parent=None):
        super().__init__(min_width=300, parent=parent)

        self.bot_info = BotInfoSection()
        self.options = OptionsSection()
        self.combat_config = CombatConfigSection()

        self._add_section(self.bot_info)
        self._add_section(self.options)
        self._add_section(self.combat_config)

    # ------------------------------------------------------------------
    # Convenience forwarders (dispatch to all sections)
    # ------------------------------------------------------------------

    def set_config(self, config: BotConfig | None) -> None:
        self.bot_info.set_config(config)
        self.options.set_config(config)
        self.combat_config.set_config(config)

    def set_editable(self, editable: bool) -> None:
        self.bot_info.set_editable(editable)
        self.options.set_editable(editable)
        self.combat_config.set_editable(editable)

    @property
    def on_config_changed(self) -> Callable[[], None]:
        return self._on_config_changed

    @on_config_changed.setter
    def on_config_changed(self, callback: Callable[[], None]) -> None:
        self._on_config_changed = callback
        self.bot_info.on_config_changed = callback
        self.options.on_config_changed = callback
        self.combat_config.on_config_changed = callback

    # Initialise so the property getter never raises AttributeError
    _on_config_changed: Callable[[], None] = lambda: None
