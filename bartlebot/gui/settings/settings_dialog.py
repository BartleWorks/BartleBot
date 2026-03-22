"""SettingsDialog: modal dialog with General, Game Embed, Accounts, Keybindings tabs."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QTabWidget, QVBoxLayout,
)

from bartlebot.gui.settings.accounts_tab import AccountsTab
from bartlebot.gui.settings.canvas_tab import CanvasTab
from bartlebot.gui.settings.game_embed_tab import GameEmbedTab
from bartlebot.gui.settings.general_tab import GeneralTab
from bartlebot.settings_manager import SettingsManager


class SettingsDialog(QDialog):
    """Modal settings dialog opened via Settings menu or Ctrl+,."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(480, 400)

        self._tabs = QTabWidget()

        # General tab
        self._general_tab = GeneralTab()
        self._tabs.addTab(self._general_tab, "General")

        # Game Embed tab
        self._game_embed_tab = GameEmbedTab()
        self._tabs.addTab(self._game_embed_tab, "Game Embed")

        # Canvas tab
        self._canvas_tab = CanvasTab()
        self._tabs.addTab(self._canvas_tab, "Canvas")

        # Accounts tab
        self._accounts_tab = AccountsTab()
        self._tabs.addTab(self._accounts_tab, "Accounts")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs, 1)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        self._general_tab.apply()
        self._game_embed_tab.apply()
        self._canvas_tab.apply()
        SettingsManager.instance().save()
        self.accept()
