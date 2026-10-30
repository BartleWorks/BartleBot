"""BartleBot's game panel: core's GamePanel, with the authoring utilities as its Bar A.

Core's :mod:`bartleby.qt.game_embed.game_view_panel` draws the bar, its captions and the
game view; this module is BartleBot's list of buttons, what each says on hover, and
when each can be pressed.
"""

from __future__ import annotations

from bartleby.qt.game_embed.game_view import GameView
from bartleby.qt.game_embed.game_view_panel import ActionBar, BarAction, GamePanel

from bartlebot.clients.authoring_tools import ACTION_LABELS, AuthoringAction
from bartlebot.gui.logo import logo_disc_image
from bartlebot.settings_manager import SettingsManager


def new_game_view(parent=None) -> GameView:
    """Core's game view as BartleBot draws it: from BartleBot's settings, with its logo disc."""
    return GameView(SettingsManager.instance(), logo_disc_image, parent)


#: The toggles: each switches on and off, and reports its state in the accent.
_TOGGLES = frozenset(
    {
        AuthoringAction.TOGGLE_SPEED,
        AuthoringAction.TOGGLE_COMBAT,
        AuthoringAction.TOGGLE_DIALOGUE,
        AuthoringAction.TOGGLE_SPEED_AUTHORING,
    }
)


def _action(action: AuthoringAction, icon: str) -> BarAction:
    return BarAction(action.value, icon, ACTION_LABELS[action], checkable=action in _TOGGLES)


#: Bar A's buttons by what they do, with a divider between groups: the two moves, the
#: toggles that act on every player, and speed-authoring, which acts on the canvas.
#: The pin is the node params' TP glyph, since XYZ sync is a teleport to a spot; porting to
#: a friend and fighting take the game's own teleport and damage icons.
AUTHORING_GROUPS: tuple[tuple[BarAction, ...], ...] = (
    (_action(AuthoringAction.XYZ_SYNC, "teleport"), _action(AuthoringAction.TP_TO_FOCUSED, "friend_teleport")),
    (
        _action(AuthoringAction.TOGGLE_SPEED, "speed"),
        _action(AuthoringAction.TOGGLE_COMBAT, "fist"),
        _action(AuthoringAction.TOGGLE_DIALOGUE, "dialogue"),
    ),
    (_action(AuthoringAction.TOGGLE_SPEED_AUTHORING, "keyboard"),),
)


def authoring_tooltip(action: AuthoringAction, binding: str, *, on: bool = False) -> str:
    """What an authoring button says on hover: its name, what it does, and its key.

    The name leads because the button has none of its own; a toggle says when it is on.
    """
    what = {
        AuthoringAction.XYZ_SYNC: "Move every other player in the same zone onto the focused player's spot",
        AuthoringAction.TP_TO_FOCUSED: "Teleport every other player to the focused player through the friends list",
        AuthoringAction.TOGGLE_SPEED: "Make every player walk faster, until switched off or a bot starts",
        AuthoringAction.TOGGLE_COMBAT: "Fight any battle a player enters, with the open bot's default Duel Strategy",
        AuthoringAction.TOGGLE_DIALOGUE: "Click through every player's NPC dialogue, declining any side quest",
        AuthoringAction.TOGGLE_SPEED_AUTHORING: (
            "Keys 1–0 in a game window add that hotbar node after the selected one and record it"
        ),
    }[action]
    name = f"{ACTION_LABELS[action]} (on)" if on else ACTION_LABELS[action]
    key = f" [{binding}]" if binding else ""
    return f"{name}{key} — {what}"


def authoring_enabled(action: AuthoringAction, *, clients: int, run_active: bool, busy: bool) -> bool:
    """Whether *action*'s button can be pressed. Pure, so it is testable without a QApplication.

    Nothing during a run, since a run switches both toggles off and refuses the rest. A
    toggle that is on stays pressable with its clients gone, so it can still be switched off.
    """
    if run_active:
        return False
    if action in (AuthoringAction.XYZ_SYNC, AuthoringAction.TP_TO_FOCUSED):
        return clients >= 2 and not busy
    return clients >= 1


def authoring_disabled_reason(action: AuthoringAction, *, run_active: bool, busy: bool) -> str:
    """Why *action*'s button is disabled, for its tooltip. Only asked of a disabled button."""
    if run_active:
        return "a bot is running"
    if action in (AuthoringAction.XYZ_SYNC, AuthoringAction.TP_TO_FOCUSED):
        return "another move is in progress" if busy else "needs at least two connected clients"
    return "needs a connected client"


class AuthoringBar(ActionBar):
    """The authoring utilities as core's action bar, in the middle of Bar A (#81, #85).

    Bar A, because the utilities are about the clients, not the bot. A toggle that is on
    draws its glyph in the accent, the same way the node browser's favorite star reports
    its state. The buttons sit in AUTHORING_GROUPS, with a divider between groups.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(AUTHORING_GROUPS, parent)
        self.show_state(
            speed_on=False,
            combat_on=False,
            busy=False,
            run_active=False,
            clients=0,
            hotkeys={},
            speed_authoring_on=False,
            dialogue_on=False,
        )

    def show_state(
        self,
        *,
        speed_on: bool,
        combat_on: bool,
        busy: bool,
        run_active: bool,
        clients: int,
        hotkeys: dict[str, str],
        speed_authoring_on: bool = False,
        dialogue_on: bool = False,
    ) -> None:
        on = {
            AuthoringAction.TOGGLE_SPEED: speed_on,
            AuthoringAction.TOGGLE_COMBAT: combat_on,
            AuthoringAction.TOGGLE_SPEED_AUTHORING: speed_authoring_on,
            AuthoringAction.TOGGLE_DIALOGUE: dialogue_on,
        }
        for action in map(AuthoringAction, self.action_ids):
            is_on = on.get(action, False)
            enabled = is_on or authoring_enabled(action, clients=clients, run_active=run_active, busy=busy)
            tip = authoring_tooltip(action, hotkeys.get(action.value, ""), on=is_on)
            if not enabled:
                tip = f"{tip}\n(Unavailable: {authoring_disabled_reason(action, run_active=run_active, busy=busy)})"
            self.set_state(action, enabled=enabled, checked=is_on if action in _TOGGLES else None, tooltip=tip)


class GameViewPanel(GamePanel):
    """Middle panel: Bar A above the embedded game view, with the authoring utilities in its middle.

    Title bar layout (left → right):
        [N clients connected · Profile]   [authoring utilities]   [active node title]
    """

    def __init__(self, parent=None) -> None:
        super().__init__(new_game_view(), AuthoringBar(), parent)
        self.authoring_bar: AuthoringBar = self.action_bar
