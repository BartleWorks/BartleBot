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

from bartlebot.gui.game_embed.game_view import GameView
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin


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


class GameViewPanel(ThemedMixin, QWidget):
    """Middle panel: a title bar above the embedded game view.

    Title bar layout (left → right):
        [N clients connected · Profile]        [active node title]

    The bar sits above the *game windows*, so it says what the clients are doing.
    The bot's name and run status used to live here and moved to Bar B above the
    graph (#80) — they belong to the bot, and here they vanished with the game view
    whenever it was hidden.
    """

    launch_account_requested = pyqtSignal(str, str)  # forwarded from GameView
    launch_profile_requested = pyqtSignal(str)  # forwarded from GameView
    edit_profile_requested = pyqtSignal(str)  # forwarded from GameView
    manage_accounts_requested = pyqtSignal()  # forwarded from GameView
    dismiss_launch_requested = pyqtSignal(str)  # forwarded from GameView

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.game_view = GameView()
        self._connected = 0
        self._profile: str | None = None
        self._build_ui()

        # Forward placeholder signals
        self.game_view.launch_account_requested.connect(self.launch_account_requested)
        self.game_view.launch_profile_requested.connect(self.launch_profile_requested)
        self.game_view.edit_profile_requested.connect(self.edit_profile_requested)
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
        self._clients_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        # ── Right: node title (active state label) ───────────────────
        self._state_lbl = QLabel()
        self._state_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._state_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._state_lbl.setVisible(False)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)
        layout.addWidget(self._clients_lbl, stretch=1)
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
