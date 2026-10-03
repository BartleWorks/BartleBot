"""A realm picker, for the ``realm`` option type and the Favorite Realm setting.

One control for both surfaces, so the Bot settings tab and an Option Values override list the
same realms in the same order and neither can offer a realm the other cannot. The
list is the checked-in snapshot (``bartlebot.realms``), never typed: the value has
to match a key the run-time matcher knows, and a free-text field is how a realm
spelled slightly wrong would fail at run time with "not found in the list".

Speaks the get/set/changed protocol every node-only option control shares, so
``option_widgets`` dispatches to it without naming the class.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox

from bartlebot import realms

#: What the empty choice reads as. Empty is a real value -- "no favorite" -- and the
#: node fails plainly on it rather than guessing a realm.
NOT_SET_TEXT = "Not set"


class RealmCombo(QComboBox):
    """Drop-down of every realm the game offers, with an explicit empty choice first."""

    changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.addItem(NOT_SET_TEXT, "")
        for key in realms.realm_keys():
            self.addItem(realms.label(key), key)
        self.currentIndexChanged.connect(lambda _i: self.changed.emit())

    # ------------------------------------------------------------------
    # Value protocol
    # ------------------------------------------------------------------

    def get_value(self) -> str:
        return str(self.currentData() or "")

    def set_value(self, value) -> None:
        key = "" if value is None else str(value).strip()
        index = self.findData(key)
        if index < 0:
            # A key the snapshot no longer lists -- a retired realm, or a hand-edited
            # settings.json. Show it rather than silently replacing the user's value
            # with "Not set"; the node reports it as unknown at run time.
            self.addItem(f"{key} (unknown realm)", key)
            index = self.count() - 1
        self.setCurrentIndex(index)
