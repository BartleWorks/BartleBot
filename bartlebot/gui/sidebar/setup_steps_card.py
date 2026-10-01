"""The setup card: what heads Setup, in Run mode, for a bot whose template steps are not all done.

Setup writes ``settings.json`` and never the bot file, and a fill-in is a value *in* the
bot file, so the steps cannot be done here. The card only says how many are left and
offers the way to where they are done: Edit mode, with nothing selected, where
Properties shows them (``canvas/template_page.py``). It is gone once nothing is left.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin, set_style_sheet
from bartlebot.gui.widgets.action_button import ActionButton, ActionStyle


def steps_left_text(left: int) -> str:
    """The card's line, and the library row's: how many steps are still to do."""
    return f"Not set up yet: {left} {'step' if left == 1 else 'steps'} left"


class SetupStepsCard(ThemedMixin, QFrame):
    """How many setup steps are left, and a **Set up** button. Hidden while nothing is left."""

    setup_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._left = 0
        self.setObjectName("SetupStepsCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)
        self._label = QLabel("", self)
        self._label.setWordWrap(True)
        self._button = ActionButton("pencil", "Set up", style=ActionStyle.PRIMARY, fill=False, parent=self)
        self._button.setToolTip("Go to Edit mode, where Properties lists the steps and takes you to each one")
        self._button.clicked.connect(self.setup_requested)
        row.addWidget(self._label, 1)
        row.addWidget(self._button, 0)
        self.setVisible(False)
        self.refresh_theme_styles()

    @property
    def steps_left(self) -> int:
        return self._left

    @property
    def text(self) -> str:
        return self._label.text()

    @property
    def button(self) -> ActionButton:
        return self._button

    def set_steps_left(self, left: int) -> None:
        self._left = max(0, left)
        self._label.setText(steps_left_text(self._left) if self._left else "")
        self.setVisible(self._left > 0)

    def apply_theme_styles(self) -> None:
        set_style_sheet(self._label, f"color: {THEME.text}; font-weight: 600;")
        set_style_sheet(
            self,
            f"#SetupStepsCard {{ border: 1px solid {THEME.warning}; border-radius: 6px;"
            f" background: {THEME.surface}; }}",
        )
