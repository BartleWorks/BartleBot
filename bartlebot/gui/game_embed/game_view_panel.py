"""GameViewPanel: GameView wrapped with a status title bar."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from bartlebot.clients.authoring_tools import ACTION_LABELS, AuthoringAction
from bartlebot.gui.game_embed.game_view import GameView
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.theming.themed import ThemedMixin
from bartlebot.gui.widgets.buttons.icon_button import IconButton, IconTint


def client_summary(connected: int, profile: str | None) -> str:
    """The caption for the left of Bar A: how many clients, launched from what.

    Pure, so the wording is testable without a ``QApplication`` — the same reasoning
    as ``client_refresh_interval()``.  The profile is named only when there is one
    *and* something is connected: "0 clients · Four Wizards" reads as a claim that
    the profile is running.
    """
    noun = "client" if connected == 1 else "clients"
    text = f"{connected} {noun} connected" if connected else "No clients connected"
    if connected and profile:
        text = f"{text} · {profile}"
    return text


#: Each utility's glyph. Icons, not labels: Bar A's height is a term in the window-sizing
#: arithmetic (``game_sizing.GAME_PANEL_CHROME_PX``), and a labeled button is taller than
#: the caption row it would sit in.
#: The pin is the node params' TP glyph, since XYZ sync is a teleport to a spot; porting to
#: a friend and fighting take the game's own teleport and damage icons.
AUTHORING_ICONS: dict[AuthoringAction, str] = {
    AuthoringAction.XYZ_SYNC: "teleport",
    AuthoringAction.TP_TO_FOCUSED: "friend_teleport",
    AuthoringAction.TOGGLE_SPEED: "speed",
    AuthoringAction.TOGGLE_COMBAT: "fist",
    AuthoringAction.TOGGLE_DIALOGUE: "dialogue",
    AuthoringAction.TOGGLE_SPEED_AUTHORING: "keyboard",
}

#: Bar A's buttons by what they do, with a divider between groups: the two moves, the
#: toggles that act on every player, and speed-authoring, which acts on the canvas.
AUTHORING_GROUPS: tuple[tuple[AuthoringAction, ...], ...] = (
    (AuthoringAction.XYZ_SYNC, AuthoringAction.TP_TO_FOCUSED),
    (AuthoringAction.TOGGLE_SPEED, AuthoringAction.TOGGLE_COMBAT, AuthoringAction.TOGGLE_DIALOGUE),
    (AuthoringAction.TOGGLE_SPEED_AUTHORING,),
)

# The divider's box: the rule is drawn centered in it, so the width beyond its 1px is the
# padding either side, added to the row's spacing. Shorter than the 24px buttons, so it
# reads as a break in the row rather than a wall.
_DIVIDER_SIZE = (9, 16)


def authoring_tooltip(action: AuthoringAction, binding: str, *, on: bool = False) -> str:
    """What an authoring button says on hover: its name, what it does, and its key.

    The name leads because the button has none of its own; a toggle says when it is on.
    """
    what = {
        AuthoringAction.XYZ_SYNC: "Move every other player in the same zone onto the focused player's spot",
        AuthoringAction.TP_TO_FOCUSED: "Port every other player to the focused player through the friends list",
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


class AuthoringBar(ThemedMixin, QWidget):
    """The authoring utilities as icon buttons, in the middle of Bar A (#81, #85).

    Bar A, because the utilities are about the clients, not the bot. A toggle that is on
    draws its glyph in the accent, the same way the node browser's favorite star reports
    its state. The buttons sit in AUTHORING_GROUPS, with a divider between groups.
    """

    action_requested = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._buttons: dict[AuthoringAction, IconButton] = {}
        self._dividers: list[QFrame] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for group in AUTHORING_GROUPS:
            if self._buttons:
                divider = QFrame(self)
                divider.setFrameShape(QFrame.Shape.VLine)
                divider.setFixedSize(*_DIVIDER_SIZE)
                layout.addWidget(divider, alignment=Qt.AlignmentFlag.AlignVCenter)
                self._dividers.append(divider)
            for action in group:
                btn = IconButton(
                    AUTHORING_ICONS[action], tooltip=ACTION_LABELS[action], size=24, icon_size=18, parent=self
                )
                btn.clicked.connect(lambda _checked=False, a=action: self.action_requested.emit(a.value))
                layout.addWidget(btn)
                self._buttons[action] = btn
        self.refresh_theme_styles()
        self.set_state(
            speed_on=False,
            combat_on=False,
            busy=False,
            run_active=False,
            clients=0,
            hotkeys={},
            speed_authoring_on=False,
            dialogue_on=False,
        )

    def button(self, action: AuthoringAction) -> IconButton:
        return self._buttons[action]

    @property
    def dividers(self) -> list[QFrame]:
        return list(self._dividers)

    def apply_theme_styles(self) -> None:
        for divider in self._dividers:
            divider.setStyleSheet(f"color: {THEME.border};")

    def set_state(
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
        for action, btn in self._buttons.items():
            is_on = on.get(action, False)
            btn.set_tint(IconTint.ACCENT if is_on else IconTint.DEFAULT)
            btn.setEnabled(is_on or authoring_enabled(action, clients=clients, run_active=run_active, busy=busy))
            btn.setToolTip(authoring_tooltip(action, hotkeys.get(action.value, ""), on=is_on))


def _side_policy() -> QSizePolicy:
    """The size policy of Bar A's two captions, which is what keeps the buttons centered.

    Equal shares of the row regardless of text: ``Ignored`` so a long node title clips at
    its half rather than pushing the buttons left, and space kept while hidden so the node
    title appearing when a run starts does not move them either -- a hidden widget
    otherwise gives its share to the other caption.
    """
    policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    policy.setRetainSizeWhenHidden(True)
    return policy


class GameViewPanel(ThemedMixin, QWidget):
    """Middle panel: a title bar above the embedded game view.

    Title bar layout (left → right):
        [N clients connected · Profile]   [authoring utilities]   [active node title]

    The bar sits above the *game windows*, so it says what the clients are doing.
    The bot's name and run status used to live here and moved to Bar B above the
    graph — they belong to the bot, and here they vanished with the game view
    whenever it was hidden.
    """

    launch_account_requested = pyqtSignal(str, str)  # forwarded from GameView
    launch_profile_requested = pyqtSignal(str)  # forwarded from GameView
    edit_account_requested = pyqtSignal(str)  # forwarded from GameView
    delete_account_requested = pyqtSignal(str)  # forwarded from GameView
    edit_profile_requested = pyqtSignal(str)  # forwarded from GameView
    duplicate_profile_requested = pyqtSignal(str)  # forwarded from GameView
    delete_profile_requested = pyqtSignal(str)  # forwarded from GameView
    add_account_requested = pyqtSignal()  # forwarded from GameView
    add_profile_requested = pyqtSignal()  # forwarded from GameView
    reorder_accounts_requested = pyqtSignal(list)  # forwarded from GameView
    reorder_profiles_requested = pyqtSignal(list)  # forwarded from GameView
    manage_accounts_requested = pyqtSignal()  # forwarded from GameView
    dismiss_launch_requested = pyqtSignal(str)  # forwarded from GameView
    authoring_action_requested = pyqtSignal(str)  # an AuthoringAction value, from AuthoringBar

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.game_view = GameView()
        self._connected = 0
        self._profile: str | None = None
        self._build_ui()

        # Forward placeholder signals
        self.game_view.launch_account_requested.connect(self.launch_account_requested)
        self.game_view.launch_profile_requested.connect(self.launch_profile_requested)
        self.game_view.edit_account_requested.connect(self.edit_account_requested)
        self.game_view.delete_account_requested.connect(self.delete_account_requested)
        self.game_view.edit_profile_requested.connect(self.edit_profile_requested)
        self.game_view.duplicate_profile_requested.connect(self.duplicate_profile_requested)
        self.game_view.delete_profile_requested.connect(self.delete_profile_requested)
        self.game_view.add_account_requested.connect(self.add_account_requested)
        self.game_view.add_profile_requested.connect(self.add_profile_requested)
        self.game_view.reorder_accounts_requested.connect(self.reorder_accounts_requested)
        self.game_view.reorder_profiles_requested.connect(self.reorder_profiles_requested)
        self.game_view.manage_accounts_requested.connect(self.manage_accounts_requested)
        self.game_view.dismiss_launch_requested.connect(self.dismiss_launch_requested)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def refresh_launch_lists(self) -> None:
        """Rebuild the Quick Launch lists — see GameView.refresh_launch_lists."""
        self.game_view.refresh_launch_lists()

    def set_roster_source(self, source) -> None:
        """Supply what the account cards ask for their characters."""
        self.game_view.set_roster_source(source)

    def repaint_tile_labels(self) -> None:
        """Repaint the tile captions — see GameView.repaint_tile_labels."""
        self.game_view.repaint_tile_labels()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._make_header())

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(sep)

        outer.addWidget(self.game_view, stretch=1)

        self.refresh_theme_styles()

    def _make_header(self) -> QWidget:
        bar = QWidget()

        # ── Left: what is connected, and what launched it ────────────
        self._clients_lbl = QLabel(client_summary(0, None))
        self._clients_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._clients_lbl.setSizePolicy(_side_policy())

        # ── Right: node title (active state label) ───────────────────
        self._state_lbl = QLabel()
        self._state_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._state_lbl.setSizePolicy(_side_policy())
        self._state_lbl.setVisible(False)

        layout = QHBoxLayout(bar)
        # 2px, not 4, above and below: the authoring buttons set the row's height, and the
        # whole strip has to stay inside GAME_PANEL_CHROME_PX.
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(6)
        # ── Middle: the authoring utilities (#81) ────────────────────
        self.authoring_bar = AuthoringBar()
        self.authoring_bar.action_requested.connect(self.authoring_action_requested)

        # The buttons stay dead center, whatever the two captions say: each side gets the
        # same stretch and neither one's text width counts (see _side_policy).
        layout.addWidget(self._clients_lbl, stretch=1)
        layout.addWidget(self.authoring_bar)
        layout.addWidget(self._state_lbl, stretch=1)

        return bar

    # ------------------------------------------------------------------
    # Public API — called by MainWindow
    # ------------------------------------------------------------------

    def set_client_summary(self, connected: int, profile: str | None = None) -> None:
        """How many clients are hooked, and the launch profile that produced them.

        *profile* is remembered when omitted, because the client count changes on
        every detection tick while the profile changes only on a launch.
        """
        self._connected = connected
        if profile is not None:
            self._profile = profile
        self._clients_lbl.setText(client_summary(self._connected, self._profile))

    def set_launch_profile(self, profile: str | None) -> None:
        """Name the launch profile most recently used, or clear it."""
        self._profile = profile
        self._clients_lbl.setText(client_summary(self._connected, self._profile))

    def set_active_node(self, state_label: str) -> None:
        """Show the node the bot is currently in, or nothing when it is idle."""
        self._state_lbl.setText(state_label)
        # Its half of the bar can be narrower than a long title, which then clips.
        self._state_lbl.setToolTip(state_label)
        self._state_lbl.setVisible(bool(state_label))

    @property
    def client_text(self) -> str:
        """What the left of the bar currently reads."""
        return self._clients_lbl.text()

    @property
    def node_text(self) -> str:
        """What the right of the bar currently reads."""
        return self._state_lbl.text()

    def apply_theme_styles(self) -> None:
        self._clients_lbl.setStyleSheet(f"color: {THEME.text};")
        self._state_lbl.setStyleSheet(f"color: {THEME.text_muted};")
