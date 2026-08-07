"""Pick which fights a Duel Strategy covers.

The only place group membership is edited directly.  Everything else in the
combat tab changes *what* a strategy does; this changes *which* fights it does
it to.

Checking a fight that already belongs to another group takes it from that group,
so each row says what it is currently bound to.  Without that, a click silently
destroys a config the user spent time on and nothing on screen admits it.

Shown from a menu action — a synchronous Qt slot — so ``exec()`` here is safe.
Calling it from inside an asyncio task would wedge the event loop under Python
3.13's task re-entrancy rule; see the note in CLAUDE.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.elided_label import ElidedLabel, apply_muted_color

if TYPE_CHECKING:
    from collections.abc import Callable

    from bartlebot.bot.schema import StateModel

_MIN_WIDTH = 380
_MAX_HEIGHT = 420


class AppliesToDialog(QDialog):
    """Checkbox list of every exposed fight, with its current binding shown."""

    def __init__(
        self,
        strategy_name: str,
        exposed: list[StateModel],
        chosen_ids: list[str],
        describe: Callable[[str], str],
        parent=None,
    ):
        """``describe`` maps a state id to what it is currently bound to, and is
        only rendered for fights this group does not already own — a fight that
        is already checked has nothing to be taken from."""
        super().__init__(parent)
        self.setWindowTitle("Applies to")
        self.setMinimumWidth(_MIN_WIDTH)

        outer = QVBoxLayout(self)

        self._prompt = QLabel(f"Which fights use {strategy_name}?")
        self._prompt.setWordWrap(True)
        outer.addWidget(self._prompt)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMaximumHeight(_MAX_HEIGHT)
        content = QWidget()
        rows = QVBoxLayout(content)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(2)

        chosen = set(chosen_ids)
        self._boxes: dict[str, QCheckBox] = {}
        self._current_lbls: list[ElidedLabel] = []

        for state in exposed:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)

            box = QCheckBox(state.label or state.id)
            box.setChecked(state.id in chosen)
            box.toggled.connect(self._update_ok)
            self._boxes[state.id] = box
            row.addWidget(box, 1)

            if state.id not in chosen:
                current = ElidedLabel(f"currently: {describe(state.id)}")
                apply_muted_color(current)
                current.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._current_lbls.append(current)
                row.addWidget(current)

            rows.addLayout(row)

        rows.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        outer.addWidget(self._buttons)

        self._update_ok()

    def selected_ids(self) -> list[str]:
        """Chosen fights, in the exposed order they were listed in."""
        return [sid for sid, box in self._boxes.items() if box.isChecked()]

    def _update_ok(self) -> None:
        """A group covering no fights is a deletion, not a membership edit.

        There are already paths for getting rid of one — unlink it, or clear its
        chip — and both say what they do.  Letting OK mean "delete this group"
        would make an empty list a second, unlabelled way to do it.
        """
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        empty = not self.selected_ids()
        ok.setEnabled(not empty)
        ok.setToolTip("Pick at least one fight" if empty else "")

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            for lbl in self._current_lbls:
                apply_muted_color(lbl)
            self._prompt.setStyleSheet(f"color: {THEME.text};")
