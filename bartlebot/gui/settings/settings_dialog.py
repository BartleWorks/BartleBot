"""SettingsDialog: modal dialog with General, Bot, Canvas, Theme, and Game tabs."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTabWidget,
    QVBoxLayout,
)

from bartlebot.gui.settings.accounts_tab import AccountsTab
from bartlebot.gui.settings.bot_tab import BotTab
from bartlebot.gui.settings.canvas_tab import CanvasTab
from bartlebot.gui.settings.general_tab import GeneralTab
from bartlebot.gui.settings.theme_tab import ThemeTab
from bartlebot.settings_manager import SettingsManager


class SettingsDialog(QDialog):
    """Modal settings dialog opened via Settings menu or Ctrl+,."""

    def __init__(self, parent=None, live_game_area: tuple[int, int] | None = None) -> None:
        """*live_game_area* is the game area the window behind this dialog currently has.

        Passed in rather than looked up: the Game tab's resolution pickers judge a launch
        exactly as the launch surfaces do, and a splitter drag moves that answer without
        moving any setting. Reaching back up the parent chain from a settings tab to find
        the main window would couple the tab to the window that happens to open it.
        """
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(480, 400)

        self._tabs = QTabWidget()

        # General tab
        self._general_tab = GeneralTab()
        self._tabs.addTab(self._general_tab, "General")

        # Bot tab (how a run behaves, and the app-setting level of every bot option)
        self._bot_tab = BotTab()
        self._tabs.addTab(self._bot_tab, "Bot")

        # Canvas tab
        self._canvas_tab = CanvasTab()
        self._tabs.addTab(self._canvas_tab, "Canvas")

        # Theme tab
        self._theme_tab = ThemeTab()
        self._tabs.addTab(self._theme_tab, "Theme")

        # Game tab (accounts, launch profiles, game embed resolution)
        self._accounts_tab = AccountsTab(live_game_area=live_game_area)
        self._tabs.addTab(self._accounts_tab, "Game")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs, 1)
        layout.addWidget(buttons)

    @property
    def tabs(self) -> QTabWidget:
        """The tab bar itself, for anything that needs to ask what tabs there are."""
        return self._tabs

    def apply_all(self) -> None:
        """Push every tab's form state into the settings singleton, without saving.

        Driven off the tab bar rather than a hand-written list of four: a list is
        one line to forget when a tab is added, and forgetting it discards
        everything the user typed into the new tab with no error anywhere.
        ``AccountsTab`` persists eagerly and its ``apply`` is a documented no-op,
        which is what makes iterating safe.
        """
        for i in range(self._tabs.count()):
            self._tabs.widget(i).apply()

    def open_tab(self, tab_name: str) -> None:
        """Switch to the named tab. Valid names: general, bot, canvas, theme, game."""
        widgets = {
            "general": self._general_tab,
            "bot": self._bot_tab,
            "canvas": self._canvas_tab,
            "theme": self._theme_tab,
            "game": self._accounts_tab,
        }
        if w := widgets.get(tab_name):
            self._tabs.setCurrentWidget(w)

    def edit_profile(self, profile_name: str) -> None:
        """Show the Game tab and open the editor for *profile_name*.

        The route the launch screen's profile cards take: they name a profile, and the
        dialog owns where its editor lives.
        """
        self.open_tab("game")
        self._accounts_tab.edit_profile(profile_name)

    def _on_accept(self) -> None:
        self.apply_all()
        SettingsManager.instance().save()
        self.accept()
