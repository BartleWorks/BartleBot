"""LauncherPopup: small non-modal window listing launch profile buttons."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

from PyQt6.QtWidgets import (
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot.settings_manager import SettingsManager


class LauncherPopup(QWidget):
    """Non-modal floating panel showing one button per launch profile."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Launch")
        self.setWindowFlags(self.windowFlags() | __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.WindowType.Tool)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        profiles = SettingsManager.instance().launch_profiles
        if not profiles:
            layout.addWidget(QLabel("No launch profiles configured.\nGo to Settings → Accounts."))
            return

        for profile in profiles:
            name = profile.get("name", "Unnamed")
            btn = QPushButton(name)
            btn.clicked.connect(lambda _checked, n=name: self._launch(n))
            layout.addWidget(btn)

        self.adjustSize()

    def _launch(self, profile_name: str) -> None:
        asyncio.ensure_future(self._run_launch(profile_name))

    @property
    def _auto_select_driver(self):
        """The main window's driver, so a popup launch behaves like a menu launch."""
        parent = self.parent()
        return getattr(parent, "auto_select_driver", None)

    async def _run_launch(self, profile_name: str) -> None:
        try:
            from bartlebot.launcher.launch_profile import launch_profile

            await launch_profile(profile_name, drive=self._auto_select_driver)
        except Exception as e:
            QMessageBox.critical(self, "Launch failed", str(e))
            logger.exception("Profile launch failed")
