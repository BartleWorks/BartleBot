"""The control for the ``player_realms`` option type: Together or Separate, as a radio pair.

Two answers, both always visible, because neither is the absence of the other: Separate is
not "Together, unticked". Speaks the get/set/changed protocol every node-only option control
shares, so ``option_widgets`` dispatches to it without naming the class.

A bot holding a node that merges players cannot be Separate (:mod:`bartlebot.player_realms`),
and :meth:`PlayerRealmsChoice.block_separate` is how a row says so: the Separate radio greys
out and its tooltip names the nodes responsible. A value that already says Separate -- an
override from before the node was added -- stays shown rather than being rewritten behind
the user's back; the run refuses to start on it, and picking Together clears it.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QRadioButton, QWidget

from bartlebot import player_realms


class PlayerRealmsChoice(QWidget):
    """Together / Separate."""

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._together = QRadioButton(player_realms.TOGETHER)
        self._separate = QRadioButton(player_realms.SEPARATE)
        self._group = QButtonGroup(self)
        self._group.addButton(self._together)
        self._group.addButton(self._separate)
        self._group.buttonClicked.connect(lambda _btn: self.changed.emit())

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._together)
        layout.addWidget(self._separate)
        layout.addStretch()

    # ------------------------------------------------------------------
    # Value protocol
    # ------------------------------------------------------------------

    def get_value(self) -> str:
        return player_realms.SEPARATE if self._separate.isChecked() else player_realms.TOGETHER

    def set_value(self, value) -> None:
        # Anything unrecognised reads as Together, as it does at run time.
        (self._separate if player_realms.is_separate(value) else self._together).setChecked(True)

    # ------------------------------------------------------------------
    # Blocking
    # ------------------------------------------------------------------

    def block_separate(self, reason: str) -> None:
        """Grey out Separate, saying why. An empty *reason* offers it again."""
        self._separate.setEnabled(not reason)
        self._separate.setToolTip(reason)

    def separate_blocked(self) -> bool:
        return not self._separate.isEnabled()

    def blocked_reason(self) -> str:
        """Why Separate is greyed out, or "" while it is offered."""
        return self._separate.toolTip() if self.separate_blocked() else ""
