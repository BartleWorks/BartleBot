"""The control for the ``player_realms`` option type: Together or Separate, as a radio pair.

Two answers, both always visible, because neither is the absence of the other: Separate is
not "Together, unticked". Speaks the get/set/changed protocol every node-only option control
shares, so ``option_widgets`` dispatches to it without naming the class.

A bot holding a node that merges players cannot be Separate (:mod:`bartlebot.options.player_realms`),
and :meth:`PlayerRealmsChoice.block_separate` is how a row says so: the Separate radio grays
out and its tooltip names the nodes responsible. A value that already says Separate -- an
override from before the node was added -- stays shown rather than being rewritten behind
the user's back; the run refuses to start on it, and picking Together clears it.

A user with one player has nothing to arrange (#264), and :meth:`PlayerRealmsChoice.set_no_team`
grays both radios, each saying why. The value shown is left as it is.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QRadioButton, QWidget

from bartlebot.options import player_realms


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
        #: Why Separate is not on offer in this bot, or "".
        self._separate_reason = ""
        #: Why neither is on offer to this user, or "".
        self._no_team_reason = ""

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
        # Anything unrecognized reads as Together, as it does at run time.
        (self._separate if player_realms.is_separate(value) else self._together).setChecked(True)

    # ------------------------------------------------------------------
    # Blocking
    # ------------------------------------------------------------------

    def block_separate(self, reason: str) -> None:
        """Gray out Separate, saying why. An empty *reason* offers it again."""
        self._separate_reason = reason
        self._apply()

    def separate_blocked(self) -> bool:
        """Whether this bot cannot be Separate: a merging node, not the user's lack of a team."""
        return bool(self._separate_reason)

    def blocked_reason(self) -> str:
        """Why Separate is grayed out for this bot, or "" while it is offered."""
        return self._separate_reason

    def set_no_team(self, reason: str) -> None:
        """Gray out both choices, saying why. An empty *reason* offers them again."""
        self._no_team_reason = reason
        self._apply()

    def no_team(self) -> bool:
        return bool(self._no_team_reason)

    def _apply(self) -> None:
        # No team says more than a merging node: with one player, neither choice does anything.
        self._together.setEnabled(not self._no_team_reason)
        self._together.setToolTip(self._no_team_reason)
        self._separate.setEnabled(not (self._no_team_reason or self._separate_reason))
        self._separate.setToolTip(self._no_team_reason or self._separate_reason)
