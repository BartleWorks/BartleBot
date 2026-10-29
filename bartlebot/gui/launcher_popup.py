"""LauncherPopup: small non-modal window listing launch profile buttons."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

from bartleby.qt.widgets.buttons.action_button import ActionButton
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QLabel,
    QMessageBox,
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
            # One profile is not more expected than another, so none of them takes a
            # weight. A glyph would repeat on every row and say nothing the row does not.
            btn = ActionButton("", name)
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

    async def _run_launch(self, profile_name: str) -> None:
        """Launch through the main window, rather than calling the launcher directly.

        The popup used to assemble its own launch out of borrowed parts — the parent's
        driver and its slot reserver — which made it a second route to the same launcher
        and therefore a route the in-flight guard could not see. A click here while
        that profile was already coming up spawned a second batch, and the fix is one door
        rather than a second copy of the guard: everything the parent's own launch does,
        including the fit check and the recent list, is now shared instead of skipped.
        """
        window = self.parent()
        launch = getattr(window, "launch_profile_named", None)
        if launch is None:
            # No main window to launch through. Nothing is missing that this could
            # substitute for — the slot ordering and the guard both live over there — so
            # say so rather than falling back to a launch that ignores both.
            logger.error("Launcher popup has no main window to launch '%s' through", profile_name)
            QTimer.singleShot(
                0,
                lambda: QMessageBox.critical(
                    self, "Launch failed", "The launcher is not connected to the main window."
                ),
            )
            return
        try:
            await launch(profile_name)
        except Exception as e:
            # Deferred: this is a task, where a modal wedges every other task (#154).
            message = str(e)
            QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Launch failed", message))
            logger.exception("Profile launch failed")
