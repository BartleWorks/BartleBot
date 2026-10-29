"""AppController's part of the update check (#27): the title bar, Help › Check for Updates…, and a running bot.

``_wire_updates`` and ``_bot_busy``, bound to a stand-in as the other controller tests do.
"""

from __future__ import annotations

import asyncio

from bartleby.qt.title_bar.title_bar import TitleBar
from PyQt6.QtCore import QObject
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenuBar, QWidget

from bartlebot.app_controller import AppController


class _Window(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.title_bar = TitleBar(QMenuBar(), self)
        self.action_check_updates = QAction("Check for Updates…", self)
        self.action_check_updates.setEnabled(False)


class _Controller(QObject):
    """The controller as far as the update wiring reaches: a QObject to parent the prompt, and the run's state."""

    _wire_updates = AppController._wire_updates
    _bot_busy = AppController._bot_busy

    def __init__(self) -> None:
        super().__init__()
        self._window = _Window()
        self._current_machine = None
        self._bot_task = None
        self._run_probe = None


def _controller() -> _Controller:
    ns = _Controller()
    ns._wire_updates()
    return ns


def test_in_a_checkout_help_cannot_check_and_says_why(qapp):
    ns = _controller()

    assert not ns._window.action_check_updates.isEnabled()
    assert "installed" in ns._window.action_check_updates.toolTip()


def test_the_prompts_button_text_reaches_the_title_bar(qapp):
    ns = _controller()

    ns._updates.button_changed.emit("Update 1.2.0")
    assert not ns._window.title_bar.update_button.isHidden()
    ns._updates.button_changed.emit("")
    assert ns._window.title_bar.update_button.isHidden()


async def test_a_bot_starting_or_running_holds_the_popup_back(qapp):
    ns = _controller()
    assert not ns._bot_busy()

    ns._bot_task = asyncio.get_running_loop().create_future()
    assert ns._bot_busy(), "the run is starting"
    ns._bot_task.set_result(None)
    assert not ns._bot_busy()

    ns._current_machine = object()
    assert ns._bot_busy()
    assert ns._updates.busy()
