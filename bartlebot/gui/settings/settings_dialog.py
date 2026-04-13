"""SettingsDialog: modal dialog with General, Canvas, Theme, and Game tabs."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTabWidget,
    QVBoxLayout,
)

from bartlebot.gui.settings.accounts_tab import AccountsTab
from bartlebot.gui.settings.canvas_tab import CanvasTab
from bartlebot.gui.settings.general_tab import GeneralTab
from bartlebot.gui.settings.theme_tab import ThemeTab
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

        # Canvas tab
        self._canvas_tab = CanvasTab()
        self._tabs.addTab(self._canvas_tab, "Canvas")

        # Theme tab
        self._theme_tab = ThemeTab()
        self._tabs.addTab(self._theme_tab, "Theme")

        # Game tab (accounts, launch profiles, game embed resolution)
        self._accounts_tab = AccountsTab()
        self._tabs.addTab(self._accounts_tab, "Game")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs, 1)
        layout.addWidget(buttons)

    def open_tab(self, tab_name: str) -> None:
        """Switch to the named tab. Valid names: general, canvas, theme, game."""
        widgets = {
            "general": self._general_tab,
            "canvas": self._canvas_tab,
            "theme": self._theme_tab,
            "game": self._accounts_tab,
        }
        if w := widgets.get(tab_name):
            self._tabs.setCurrentWidget(w)

    def _on_accept(self) -> None:
        self._general_tab.apply()
        self._canvas_tab.apply()
        self._theme_tab.apply()
        SettingsManager.instance().save()
        self.accept()
