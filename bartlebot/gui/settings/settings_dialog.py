"""SettingsDialog: BartleBot's General, Bot, Combat, Canvas, Authoring, Theme, and Game tabs.

The dialog itself moved to core's ``bartleby/qt/settings/settings_dialog.py`` in Stage 11
of #167; this is BartleBot's list of tabs for it.
"""

from __future__ import annotations

from bartleby.qt.settings.rows import ScrollableTab
from bartleby.qt.settings.settings_dialog import SettingsDialog as CoreSettingsDialog
from bartleby.qt.settings.settings_dialog import SettingsTab
from bartleby.qt.settings.theme_tab import ThemeTab

from bartlebot.gui.settings.accounts_tab import AccountsTab
from bartlebot.gui.settings.authoring_tab import AuthoringTab
from bartlebot.gui.settings.bot_tab import BotTab
from bartlebot.gui.settings.canvas_tab import CanvasTab
from bartlebot.gui.settings.combat_defaults_tab import CombatDefaultsTab
from bartlebot.gui.settings.general_tab import GeneralTab


class SettingsDialog(CoreSettingsDialog):
    """Modal settings dialog opened via Settings menu or Ctrl+,."""

    def __init__(self, parent=None, live_game_area: tuple[int, int] | None = None) -> None:
        """*live_game_area* is the game area the window behind this dialog currently has, for the Game tab."""
        super().__init__(
            [
                SettingsTab("general", "General", lambda: ScrollableTab(GeneralTab())),
                # How a run behaves, and the app-setting level of every bot option.
                SettingsTab("bot", "Bot", lambda: ScrollableTab(BotTab())),
                # The Solo and Team Default Duel Strategies, shared with BartleBuddy.
                SettingsTab("combat", "Combat", lambda: ScrollableTab(CombatDefaultsTab())),
                SettingsTab("canvas", "Canvas", lambda: ScrollableTab(CanvasTab())),
                # The speed multiplier and the authoring utilities' hotkeys.
                SettingsTab("authoring", "Authoring", lambda: ScrollableTab(AuthoringTab())),
                SettingsTab("theme", "Theme", ThemeTab),
                # Accounts, launch profiles and game embed resolution.
                SettingsTab("game", "Game", lambda: AccountsTab(live_game_area=live_game_area)),
            ],
            parent,
        )
