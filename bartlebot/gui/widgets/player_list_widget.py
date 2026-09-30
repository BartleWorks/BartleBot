"""Shared P1–P4 player pickers used in param forms and strategy editors."""

from PyQt6.QtCore import QEvent, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QRadioButton, QWidget

from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.action_button import ActionButton


class PlayerListWidget(QWidget):
    """P1–P4 checkboxes with Select All / None buttons.

    Returns list[int] of checked 1-based player numbers via get_value().

    Two independent limits, because they mean different things:

    - ``set_max_players(n)`` is a *hard* limit — a duel strategy configured for two
      players has no third player, so the box is hidden and its value cleared.
    - ``set_available_players(n)`` is *advisory* — it grays the label for players who
      are not currently connected, and nothing else. It never clears a value and
      never blocks a click, because bots are authored offline: hiding or unchecking
      P3/P4 because only two clients happen to be running would silently rewrite a
      four-client bot the moment its node was selected.
    """

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._max_players = 4  # track separately — isVisible() is unreliable before the widget is shown
        self._available = 4  # players currently connected; advisory only, see set_available_players
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._checks: list[QCheckBox] = []
        for i in range(1, 5):
            chk = QCheckBox(f"P{i}")
            chk.checkStateChanged.connect(self.changed)
            layout.addWidget(chk)
            self._checks.append(chk)
        layout.addStretch()
        # ``fill=False``: these trail a row of check boxes rather than forming a row of
        # their own, and an expanding pair would take the width the boxes are using.
        all_btn = ActionButton("", "All", fill=False)
        all_btn.clicked.connect(self._select_all)
        none_btn = ActionButton("", "None", fill=False)
        none_btn.clicked.connect(self._deselect_all)
        layout.addWidget(all_btn)
        layout.addWidget(none_btn)

    def get_value(self) -> list[int]:
        return [i + 1 for i, chk in enumerate(self._checks) if i < self._max_players and chk.isChecked()]

    def set_value(self, value: list[int]) -> None:
        for i, chk in enumerate(self._checks):
            chk.setChecked(i < self._max_players and (i + 1) in value)

    def set_available_players(self, n: int) -> None:
        """Gray the label of every player past *n* as not currently connected.

        Advisory: the box stays clickable and its value untouched, so a four-client
        bot can still be edited with two clients running.
        """
        self._available = n
        self._apply_availability()

    def _apply_availability(self) -> None:
        for i, chk in enumerate(self._checks):
            if i < self._available:
                chk.setStyleSheet("")
                chk.setToolTip("")
            else:
                chk.setStyleSheet(f"QCheckBox {{ color: {THEME.text_muted}; }}")
                chk.setToolTip(f"Player {i + 1} is not connected. It can still be selected.")

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_availability()

    def set_max_players(self, n: int) -> None:
        """Show only the first n checkboxes; uncheck and hide any beyond n."""
        self._max_players = n
        for i, chk in enumerate(self._checks):
            chk.blockSignals(True)
            if i >= n:
                chk.setChecked(False)
            chk.setVisible(i < n)
            chk.blockSignals(False)

    def _select_all(self) -> None:
        for i, chk in enumerate(self._checks):
            if i < self._max_players:
                chk.setChecked(True)

    def _deselect_all(self) -> None:
        for i, chk in enumerate(self._checks):
            if i < self._max_players:
                chk.setChecked(False)


class PlayerSlotWidget(QWidget):
    """P1–P4 radio buttons — the single-select sibling of PlayerListWidget.

    Returns one 1-based player number via get_value(), or 0 when nothing is selected.
    Availability is advisory here for the same reason it is there: a bot authored for four
    clients must stay editable with two running.
    """

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._available = 4
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        # Buttons are exclusive within their parent widget by default, which is what we
        # want, but it also means there is no way back to "nothing selected" by clicking.
        # That is fine: a target is required once this mode is on, and 0 only survives
        # until the user picks one.
        self._radios: list[QRadioButton] = []
        for i in range(1, 5):
            btn = QRadioButton(f"P{i}")
            btn.toggled.connect(self._on_toggled)
            layout.addWidget(btn)
            self._radios.append(btn)
        layout.addStretch()

    def _on_toggled(self, checked: bool) -> None:
        # toggled fires twice per change (off, then on); only report the on edge.
        if checked:
            self.changed.emit()

    def get_value(self) -> int:
        for i, btn in enumerate(self._radios):
            if btn.isChecked():
                return i + 1
        return 0

    def set_value(self, value: int) -> None:
        try:
            slot = int(value)
        except (TypeError, ValueError):
            slot = 0
        for i, btn in enumerate(self._radios):
            btn.blockSignals(True)
            btn.setChecked(i + 1 == slot)
            btn.blockSignals(False)

    def set_available_players(self, n: int) -> None:
        """Gray the label of every player past *n*. Advisory only; see PlayerListWidget."""
        self._available = n
        self._apply_availability()

    def _apply_availability(self) -> None:
        for i, btn in enumerate(self._radios):
            if i < self._available:
                btn.setStyleSheet("")
                btn.setToolTip("")
            else:
                btn.setStyleSheet(f"QRadioButton {{ color: {THEME.text_muted}; }}")
                btn.setToolTip(f"Player {i + 1} is not connected. It can still be selected.")

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_availability()
