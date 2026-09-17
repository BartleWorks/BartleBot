"""A wizard-name field with autocomplete and a live friends-list picker.

Lifted out of the properties panel's ``_AppSettingStrWidget``, which owned both the
control *and* the storage: it wrote the app setting directly, because a friend name
had nowhere else to live.  A friend name is now an ordinary bot option, so where the
value goes is the option layer's business and this is only the control -- it reports
``changed`` and answers ``get_value()`` like every other option widget, and Setup
decides whether that becomes an override.

The two suggestion tiers are the same arrangement zones use: live first (the online
friends list, read from a running client on demand), remembered after
(``recent_friend_names``).  The history is autocomplete and nothing more -- no
run-time code consults it, so a suggestion can never decide where a bot teleports.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from PyQt6.QtCore import QStringListModel, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCompleter,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.menu_utils import popup_menu
from bartlebot.gui.widgets.action_button import ActionButton

logger = logging.getLogger(__name__)


class FriendNameEdit(QWidget):
    """Line edit plus a "Pick from Friends List" button, for a ``friend_name`` option."""

    changed = pyqtSignal()

    def __init__(self, get_clients: Callable[[], list] | None = None, parent=None):
        super().__init__(parent)
        self._get_clients: Callable[[], list] = get_clients or (lambda: [])

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        self._edit = QLineEdit()
        self._model = QStringListModel(self)
        completer = QCompleter(self._model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._edit.setCompleter(completer)
        self._edit.setPlaceholderText("Wizard name")
        self._edit.editingFinished.connect(self._on_edited)
        outer.addWidget(self._edit)

        # ``download`` for the same reason the fill buttons carry it: this reads the
        # list off a connected player rather than out of the bot file.
        self._pick = ActionButton("download", "Pick from Friends List")
        self._pick.setToolTip("Read the online friends list from a connected player")
        self._pick.clicked.connect(lambda _: self._show_pick_menu())
        outer.addWidget(self._pick)

        self._refresh_suggestions()

    # ------------------------------------------------------------------
    # Value protocol
    # ------------------------------------------------------------------

    def get_value(self) -> str:
        return self._edit.text().strip()

    def set_value(self, value) -> None:
        self._edit.setText("" if value is None else str(value))

    def set_clients_getter(self, fn: Callable[[], list]) -> None:
        self._get_clients = fn

    def _on_edited(self) -> None:
        """Remember the name for autocomplete, then report the edit.

        Remembering is separate from storing: this list is only ever offered back as
        a suggestion, so it survives whether or not the value becomes an override.
        """
        name = self.get_value()
        if name:
            try:
                from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415 - avoids an import cycle

                settings = SettingsManager.instance()
                settings.add_recent_friend_name(name)
                settings.save()
            except Exception:
                logger.exception("Could not remember the friend name for autocomplete")
            self._refresh_suggestions()
        self.changed.emit()

    def _refresh_suggestions(self) -> None:
        try:
            from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415 - avoids an import cycle

            self._model.setStringList(SettingsManager.instance().recent_friend_names)
        except Exception:
            self._model.setStringList([])

    # ------------------------------------------------------------------
    # Live friends list
    # ------------------------------------------------------------------

    def _show_pick_menu(self) -> None:
        clients = self._get_clients()
        if not clients:
            QMessageBox.information(self, "No players", "Connect a player to read its friends list.")
            return
        if len(clients) == 1:
            asyncio.ensure_future(self._pick_from(0))
            return
        menu = popup_menu(self._pick)
        for i in range(len(clients)):
            action = menu.addAction(f"Read Player {i + 1}'s friends list")
            action.triggered.connect(lambda _checked, idx=i: asyncio.ensure_future(self._pick_from(idx)))
        menu.exec(self._pick.mapToGlobal(self._pick.rect().bottomLeft()))

    async def _pick_from(self, idx: int) -> None:
        """Read one client's online friends and offer them as a menu.

        Opening the list moves the game's own UI, so this only ever runs from an
        explicit button press, never when the panel is merely shown.
        """
        from bartlebot.nodes import node_helpers  # noqa: PLC0415 - heavy import, only needed on demand

        clients = self._get_clients()
        if idx >= len(clients):
            return
        client = clients[idx]
        try:
            async with client.mouse_handler:
                names = await node_helpers.list_online_friends(client)
            await node_helpers.close_friends_list(client)
        except Exception as exc:
            logger.debug("Could not read the friends list", exc_info=True)
            QMessageBox.information(self, "Friends list", f"Could not read the friends list: {exc}")
            return
        if not names:
            QMessageBox.information(self, "Friends list", "No friends are online.")
            return
        menu = popup_menu(self._pick)
        for name in names:
            menu.addAction(name).triggered.connect(lambda _checked, n=name: self._choose(n))
        menu.exec(self._pick.mapToGlobal(self._pick.rect().bottomLeft()))

    def _choose(self, name: str) -> None:
        self.set_value(name)
        self._on_edited()
