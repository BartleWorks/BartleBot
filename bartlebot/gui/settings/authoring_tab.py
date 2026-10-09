"""Settings tab: Authoring (the speed multiplier, and the authoring utilities' hotkeys).

Its own tab, not a Canvas section: the utilities drive the clients rather than the graph,
and not a Game tab row either, because nothing here happens to a client as it launches.
See ``bartlebot.clients.authoring_tools`` for what the utilities do and
``bartlebot.gui.global_hotkeys`` for why the keys only work inside a game window.
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QDoubleSpinBox, QKeySequenceEdit

from bartlebot.clients.authoring_tools import ACTION_LABELS, SLOT_KEYS, AuthoringAction
from bartlebot.game.game_keybindings import GameBinding, read_game_keybindings
from bartlebot.gui.global_hotkeys import parse_binding
from bartlebot.gui.settings.rows import SettingRow, SettingsForm
from bartlebot.gui.widgets.labels.themed_label import ThemedTextLabel
from bartlebot.settings_manager import DEFAULT_AUTHORING_HOTKEYS, SPEED_MULTIPLIER_RANGE, SettingsManager, default_for

#: Each hotkey row's one line, and the longer prose behind its ``?`` (empty: none).
_HOTKEY_TEXT: dict[AuthoringAction, tuple[str, str]] = {
    AuthoringAction.XYZ_SYNC: ("Move every other player in the zone onto the focused player.", ""),
    AuthoringAction.TP_TO_FOCUSED: (
        "Port every other player to the focused player.",
        "Through the friends list, across zones and realms.",
    ),
    AuthoringAction.TOGGLE_SPEED: ("Switch the speed multiplier above on and off for every player.", ""),
    AuthoringAction.TOGGLE_COMBAT: (
        "Switch auto-combat on and off.",
        "Fights use the open bot's default Duel Strategy, or the built-in one; PvP is never fought.",
    ),
    AuthoringAction.TOGGLE_DIALOGUE: (
        "Switch auto-dialogue on and off.",
        "NPC dialogue is clicked through on every player as it opens, and a side quest offered on the "
        "way is declined, never accepted.",
    ),
    AuthoringAction.TOGGLE_SPEED_AUTHORING: (
        "Keys 1–0 add a favorites-bar node while in Edit mode.",
        "Switches speed-authoring on and off. While it is on and the canvas is in Edit mode, keys 1–0 in "
        "a game window add that slot's favorites-bar node after the selected node, or at the first gap "
        "from the start node, and start recording it from the player whose window you are in.",
    ),
}

_HOTKEYS_NOTE = (
    "The keys work only while a game window has the keyboard, so they never take a key from another "
    "application or from BartleBot's own fields — but inside the game they replace whatever that key "
    "did there, and a key the game binds itself is flagged below. The focused player is the game window "
    "you are in; a button in the bar above the game windows uses the one you were last in."
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
    return f"While speed-authoring is on, the game does not see these keys it binds: {uses}."


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


class AuthoringTab(SettingsForm):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        sm = SettingsManager.instance()
        self._game_bindings = read_game_keybindings()

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
                details=(
                    "It switches off when a bot starts, since a run walks at the game's own speed. A change "
                    "applies the next time Speed is switched on."
                ),
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
