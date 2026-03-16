"""KeybindingManager: installs QShortcut instances from SettingsManager."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from PyQt6.QtGui import QKeySequence
from PyQt6.QtGui import QShortcut

from bartlebot.settings_manager import SettingsManager

if TYPE_CHECKING:
    from bartlebot.gui.main_window import MainWindow
    from bartlebot.gui.game_embed.game_view import GameView
    from bartlebot.gui.canvas.bot_canvas import BotCanvas


class KeybindingManager:
    """Reads keybinding settings and installs QShortcut objects on MainWindow."""

    def __init__(self) -> None:
        self._shortcuts: list[QShortcut] = []

    def install(
        self,
        window: "MainWindow",
        game_view: "GameView",
        bot_canvas: "BotCanvas",
        launch_fn,  # Callable[[str], Coroutine] — launch_profile(name)
    ) -> None:
        """Create all shortcuts. Removes any previously installed shortcuts first."""
        self.uninstall()
        bindings = SettingsManager.instance().keybindings
        profiles = SettingsManager.instance().launch_profiles

        def _bind(key_str: str | None, callback) -> None:
            if not key_str:
                return
            try:
                seq = QKeySequence(key_str)
                sc = QShortcut(seq, window)
                sc.activated.connect(callback)
                self._shortcuts.append(sc)
            except Exception:
                pass

        _bind(bindings.get("toggle_embed"),  game_view.toggle)
        _bind(bindings.get("next_client"),   game_view.cycle_focus)
        _bind(bindings.get("run_bot"),       bot_canvas._run_pause_btn.click)
        _bind(bindings.get("pause_bot"),     bot_canvas._run_pause_btn.click)
        _bind(bindings.get("stop_bot"),      bot_canvas.stop_button.click)

        for i, profile in enumerate(profiles):
            key_action = f"launch_profile_{i}"
            profile_name = profile.get("name", "")
            _bind(
                bindings.get(key_action),
                lambda _pn=profile_name: asyncio.ensure_future(_launch(launch_fn, _pn)),
            )

    def uninstall(self) -> None:
        """Remove all installed shortcuts."""
        for sc in self._shortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self._shortcuts.clear()

    def reload(
        self,
        window: "MainWindow",
        game_view: "GameView",
        bot_canvas: "BotCanvas",
        launch_fn,
    ) -> None:
        """Re-install shortcuts after a settings change."""
        self.install(window, game_view, bot_canvas, launch_fn)


async def _launch(launch_fn, profile_name: str) -> None:
    try:
        await launch_fn(profile_name)
    except Exception as e:
        print(f"[KeybindingManager] Launch failed for '{profile_name}': {e}")
