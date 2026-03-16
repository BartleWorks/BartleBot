"""SettingsDialog: modal dialog with General, Game Embed, Accounts, Keybindings tabs."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QTabWidget, QVBoxLayout, QWidget,
)

from bartlebot.gui.settings.accounts_tab import AccountsTab
from bartlebot.gui.settings.game_embed_tab import GameEmbedTab
from bartlebot.gui.settings.keybindings_tab import KeybindingsTab
from bartlebot.settings_manager import SettingsManager


class SettingsDialog(QDialog):
    """Modal settings dialog opened via Settings menu or Ctrl+,."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(480, 400)

        self._tabs = QTabWidget()

        # General tab (reserved for future use)
        general = QWidget()
        self._tabs.addTab(general, "General")

        # Game Embed tab
        self._game_embed_tab = GameEmbedTab()
        self._tabs.addTab(self._game_embed_tab, "Game Embed")

        # Accounts tab
        self._accounts_tab = AccountsTab()
        self._tabs.addTab(self._accounts_tab, "Accounts")

        # Keybindings tab
        self._keybindings_tab = KeybindingsTab()
        self._tabs.addTab(self._keybindings_tab, "Keybindings")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs, 1)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        self._game_embed_tab.apply()
        self._keybindings_tab.apply()
        SettingsManager.instance().save()
        self.accept()
