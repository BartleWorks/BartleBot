"""Settings tab: Authoring (the speed multiplier, and the authoring utilities' hotkeys).

Its own tab, not a Canvas section: the utilities drive the clients rather than the graph,
and not a Game tab row either, because nothing here happens to a client as it launches.
See ``bartlebot.authoring_tools`` for what the four utilities do and
``bartlebot.gui.global_hotkeys`` for why the keys only work inside a game window.
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QKeySequenceEdit,
    QVBoxLayout,
    QWidget,
)

from bartlebot.authoring_tools import ACTION_LABELS, AuthoringAction
from bartlebot.game_keybindings import GameBinding, read_game_keybindings
from bartlebot.gui.global_hotkeys import parse_binding
from bartlebot.gui.settings.rows import hint
from bartlebot.gui.widgets.rule_heading import RuleHeading
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.settings_manager import SPEED_MULTIPLIER_RANGE, SettingsManager

_HOTKEY_HINTS: dict[AuthoringAction, str] = {
    AuthoringAction.XYZ_SYNC: "Moves every other player in the same zone onto the focused player's spot.",
    AuthoringAction.TP_TO_FOCUSED: (
        "Ports every other player to the focused player through the friends list, across zones and realms."
    ),
    AuthoringAction.TOGGLE_SPEED: "Switches the speed multiplier above on and off for every player.",
    AuthoringAction.TOGGLE_COMBAT: (
        "Switches auto-combat on and off. Fights use the open bot's default Duel Strategy, or the "
        "built-in one; PvP is never fought."
    ),
}


def binding_problems(bindings: dict[str, str], game_bindings: Sequence[GameBinding] = ()) -> list[str]:
    """What is wrong with a set of bindings, as sentences; empty when nothing is.

    Pure, so the rules are testable without a QApplication. A key nothing can register is
    one problem; two actions on one key is another, since only the first would ever fire;
    a key the game itself binds (*game_bindings*, from ``config.xml``) is a third, since
    while the hotkey is armed the game never sees that key.
    """
    problems: list[str] = []
    game: dict[tuple[int, int], GameBinding] = {}
    for gb in game_bindings:
        combo = parse_binding(gb.binding)
        if combo is not None:
            game.setdefault(combo, gb)
    seen: dict[tuple[int, int], str] = {}
    for action in AuthoringAction:
        text = bindings.get(action.value, "")
        if not text:
            continue
        combo = parse_binding(text)
        label = ACTION_LABELS[action]
        if combo is None:
            problems.append(f"{label}: {text} cannot be bound — use F1–F24, a letter or a digit.")
        elif combo in seen:
            problems.append(f"{label} and {seen[combo]} are both on {text}.")
        else:
            seen[combo] = label
            if clash := game.get(combo):
                problems.append(
                    f"{label}: {text} is the game's own key for {clash.description}, which will not "
                    "work inside the game while this is bound."
                )
    return problems


class AuthoringTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(0)

        sm = SettingsManager.instance()
        # Read once per dialog: the game rewrites config.xml only when its own options change.
        self._game_bindings = read_game_keybindings()

        layout.addWidget(RuleHeading("Speed"))
        speed_form = QFormLayout()
        speed_form.setSpacing(4)
        self._speed_sb = QDoubleSpinBox()
        self._speed_sb.setRange(*SPEED_MULTIPLIER_RANGE)
        self._speed_sb.setSingleStep(0.5)
        self._speed_sb.setDecimals(1)
        self._speed_sb.setSuffix("×")
        self._speed_sb.setValue(sm.speed_multiplier)
        speed_form.addRow("Speed multiplier:", self._speed_sb)
        speed_form.addRow(
            hint(
                "How much faster every player walks while Speed is on. It switches off when a bot "
                "starts, since a run walks at the game's own speed. A change applies the next time "
                "Speed is switched on."
            )
        )
        layout.addLayout(speed_form)

        layout.addWidget(RuleHeading("Hotkeys"))
        keys_form = QFormLayout()
        keys_form.setSpacing(4)
        self._key_edits: dict[AuthoringAction, QKeySequenceEdit] = {}
        bindings = sm.authoring_hotkeys
        for action in AuthoringAction:
            edit = QKeySequenceEdit(QKeySequence(bindings[action.value]))
            edit.setMaximumSequenceLength(1)
            edit.setClearButtonEnabled(True)
            edit.keySequenceChanged.connect(self._refresh_problems)
            self._key_edits[action] = edit
            keys_form.addRow(f"{ACTION_LABELS[action]}:", edit)
            keys_form.addRow(hint(_HOTKEY_HINTS[action]))
        layout.addLayout(keys_form)
        layout.addWidget(
            hint(
                "The keys work only while a game window has the keyboard, so they never take a key "
                "from another application or from BartleBot's own fields — but inside the game they "
                "replace whatever that key did there, and a key the game binds itself is flagged "
                "below. The focused player is the game window you are "
                "in; a button in the bar above the game windows uses the one you were last in."
            )
        )
        self._problems = ThemedTextLabel("", css="padding-top: 6px;", color="error")
        self._problems.setWordWrap(True)
        layout.addWidget(self._problems)
        layout.addStretch()
        self._refresh_problems()

    def bindings(self) -> dict[str, str]:
        """The bindings as the tab shows them, in the form settings stores."""
        return {
            action.value: edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
            for action, edit in self._key_edits.items()
        }

    def _refresh_problems(self) -> None:
        problems = binding_problems(self.bindings(), self._game_bindings)
        self._problems.setText("\n".join(problems))
        self._problems.setVisible(bool(problems))

    def apply(self) -> None:
        sm = SettingsManager.instance()
        sm.speed_multiplier = round(self._speed_sb.value(), 1)
        for action, binding in self.bindings().items():
            sm.set_authoring_hotkey(action, binding)
