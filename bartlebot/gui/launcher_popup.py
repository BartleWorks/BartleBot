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

from bartlebot.gui import screen_fit
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
            # Disabled with the reason on it, never dropped from the list. This popup and
            # the Quick Launch cards show the same profiles and have to agree about which
            # of them can launch — see bartlebot/gui/screen_fit.py.
            if (reason := screen_fit.profile_fit_problem(profile, self, self._live_game_area)) is not None:
                btn.setEnabled(False)
                btn.setToolTip(reason)
            layout.addWidget(btn)

        self.adjustSize()

    def _launch(self, profile_name: str) -> None:
        asyncio.ensure_future(self._run_launch(profile_name))

    @property
    def _live_game_area(self):
        """The main window's live game area, or None when this popup has no main window.

        Read from the parent for the same reason the driver and the reserver below are:
        the popup is a floating list of buttons and owns nothing the answer depends on.
        None simply leaves the judgement to the configured layout.
        """
        return getattr(self.parent(), "live_game_area", None)

    @property
    def _auto_select_driver(self):
        """The main window's driver, so a popup launch behaves like a menu launch."""
        return getattr(self.parent(), "auto_select_driver", None)

    @property
    def _slot_reserver(self):
        """The main window's reserver, so a popup launch respects the profile order too."""
        return getattr(self.parent(), "slot_reserver", None)

    async def _run_launch(self, profile_name: str) -> None:
        try:
            from bartlebot.launcher.launch_profile import launch_profile

            await launch_profile(profile_name, drive=self._auto_select_driver, reserve=self._slot_reserver)
        except Exception as e:
            QMessageBox.critical(self, "Launch failed", str(e))
            logger.exception("Profile launch failed")
