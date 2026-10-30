"""Settings tab: Authoring (the speed multiplier, and the authoring utilities' hotkeys).

Its own tab, not a Canvas section: the utilities drive the clients rather than the graph,
and not a Game tab row either, because nothing here happens to a client as it launches.
See ``bartlebot.clients.authoring_tools`` for what the utilities do and
``bartleby.qt.global_hotkeys`` for why the keys only work inside a game window.
"""

from __future__ import annotations

from collections.abc import Sequence

from bartleby.qt.global_hotkeys import binding_problems as hotkey_problems
from bartleby.qt.settings.rows import SettingRow, SettingsForm
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from bartleby.runtime.clients import game_keys
from bartleby.runtime.game.game_keybindings import GameBinding, read_game_keybindings
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QDoubleSpinBox, QKeySequenceEdit

from bartlebot.clients.authoring_tools import ACTION_LABELS, SLOT_KEYS, AuthoringAction
from bartlebot.settings_manager import DEFAULT_AUTHORING_HOTKEYS, SPEED_MULTIPLIER_RANGE, SettingsManager, default_for

#: Each hotkey row's one line, and the longer prose behind its ``?`` (empty: none).
_HOTKEY_TEXT: dict[AuthoringAction, tuple[str, str]] = {
    AuthoringAction.XYZ_SYNC: ("Move every other player in the zone onto the focused player.", ""),
    AuthoringAction.TP_TO_FOCUSED: (
        "Teleport every other player to the focused player.",
        "Players teleport across zones and realms, through the friends list.",
    ),
    AuthoringAction.TOGGLE_SPEED: ("Turn Speed on or off for every player.", ""),
    AuthoringAction.TOGGLE_COMBAT: (
        "Turn auto-combat on or off.",
        "Fights use the open bot's default Duel Strategy, or the built-in one. PvP is never fought.",
    ),
    AuthoringAction.TOGGLE_DIALOGUE: (
        "Turn auto-dialogue on or off.",
        "Auto-dialogue clicks through NPC dialogue on every player and declines side quests.",
    ),
    AuthoringAction.TOGGLE_SPEED_AUTHORING: (
        "Turn speed-authoring on or off.",
        "While speed-authoring is on and the canvas is in Edit mode, keys 1–0 in a game window add that "
        "favorites-bar slot's node and start recording it.",
    ),
}

_HOTKEYS_NOTE = (
    "These keys work only while a game window has focus, and there they replace the game's own use of "
    "the key. The focused player is the one whose window you're in."
)


def slot_key_notes(game_bindings: Sequence[GameBinding]) -> str:
    """What the game loses while speed-authoring holds keys 1-0; empty when nothing.

    A note rather than a problem: the keys are fixed, and taking them is the feature. The
    stock game binds 1-5 only to Pet Derby talents, which nothing recorded in a dungeon
    needs, but a user may have put something of their own there.
    """
    taken = [gb for gb in game_bindings if gb.binding in SLOT_KEYS]
    if not taken:
        return ""
    uses = ", ".join(f"{gb.binding} ({gb.description})" for gb in taken)
    return f"While speed-authoring is on, these keys don't reach the game: {uses}."


def binding_problems(bindings: dict[str, str], game_bindings: Sequence[GameBinding] = ()) -> list[str]:
    """What is wrong with the authoring hotkeys, as sentences; empty when nothing is.

    Core's :func:`~bartleby.qt.global_hotkeys.binding_problems` over BartleBot's actions,
    in the order the tab shows them.
    """
    labels = {action.value: ACTION_LABELS[action] for action in AuthoringAction}
    return hotkey_problems(bindings, labels, game_bindings)


class AuthoringTab(SettingsForm):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        sm = SettingsManager.instance()
        self._game_bindings = read_game_keybindings(game_keys.known_maps())

        self.add_heading("Speed")
        self._speed_sb = QDoubleSpinBox()
        self._speed_sb.setRange(*SPEED_MULTIPLIER_RANGE)
        self._speed_sb.setSingleStep(0.5)
        self._speed_sb.setDecimals(1)
        self._speed_sb.setSuffix("×")
        self._speed_sb.setValue(sm.speed_multiplier)
        self.add_row(
            SettingRow(
                "Speed multiplier",
                "How much faster players walk while Speed is on.",
                self._speed_sb,
                default=default_for("authoring", "speed_multiplier"),
                details="Speed turns off when a bot starts. A change applies the next time Speed is turned on.",
            )
        )

        self.add_heading("Hotkeys")
        self.add_note(_HOTKEYS_NOTE)
        notes = slot_key_notes(self._game_bindings)
        if notes:
            self.add_note(notes)
        self._key_edits: dict[AuthoringAction, QKeySequenceEdit] = {}
        bindings = sm.authoring_hotkeys
        for action in AuthoringAction:
            edit = QKeySequenceEdit(QKeySequence(bindings[action.value]))
            edit.setMaximumSequenceLength(1)
            edit.setClearButtonEnabled(True)
            edit.keySequenceChanged.connect(self._refresh_problems)
            self._key_edits[action] = edit
            summary, details = _HOTKEY_TEXT[action]
            self.add_row(
                SettingRow(
                    ACTION_LABELS[action],
                    summary,
                    edit,
                    default=DEFAULT_AUTHORING_HOTKEYS[action.value],
                    details=details,
                )
            )
        self._problems = ThemedTextLabel("", css="padding-top: 6px;", color="error")
        self._problems.setWordWrap(True)
        self.add_widget(self._problems)
        self.finish()
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
