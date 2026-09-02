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


class GameViewPanel(QWidget):
    """Middle panel: a title bar above the embedded game view.

    Title bar layout (left → right):
        [● Status  elapsed]    [Bot display name]    [Node title]
    """

    launch_account_requested = pyqtSignal(str, str)  # forwarded from GameView
    launch_profile_requested = pyqtSignal(str)  # forwarded from GameView
    edit_profile_requested = pyqtSignal(str)  # forwarded from GameView
    manage_accounts_requested = pyqtSignal()  # forwarded from GameView

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.game_view = GameView()
        self._build_ui()

        # Forward placeholder signals
        self.game_view.launch_account_requested.connect(self.launch_account_requested)
        self.game_view.launch_profile_requested.connect(self.launch_profile_requested)
        self.game_view.edit_profile_requested.connect(self.edit_profile_requested)
        self.game_view.manage_accounts_requested.connect(self.manage_accounts_requested)

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

    def _make_header(self) -> QWidget:
        bar = QWidget()

        mono = THEME.font_mono()

        # ── Left: status dot + text + elapsed ────────────────────────
        self._dot_lbl = QLabel("●")
        self._dot_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._status_lbl = QLabel("Idle")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._elapsed_lbl = QLabel()
        self._elapsed_lbl.setFont(mono)
        self._elapsed_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._elapsed_lbl.setVisible(False)

        left = QWidget()
        left.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        left_row = QHBoxLayout(left)
        left_row.setContentsMargins(6, 0, 0, 0)
        left_row.setSpacing(4)
        left_row.addWidget(self._dot_lbl)
        left_row.addWidget(self._status_lbl)
        left_row.addWidget(self._elapsed_lbl)
        left_row.addStretch()

        # ── Center: bot display name (main title) ────────────────────
        self._name_lbl = QLabel("No bot loaded")
        title_font = self._name_lbl.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 1)
        self._name_lbl.setFont(title_font)
        self._name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        self._name_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        # ── Right: node title (active state label) ───────────────────
        self._state_lbl = QLabel()
        self._state_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._state_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._state_lbl.setVisible(False)

        right = QWidget()
        right.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        right_row = QHBoxLayout(right)
        right_row.setContentsMargins(0, 0, 6, 0)
        right_row.setSpacing(0)
        right_row.addStretch()
        right_row.addWidget(self._state_lbl)

        # ── Assemble ─────────────────────────────────────────────────
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(0)
        layout.addWidget(left, stretch=1)
        layout.addWidget(self._name_lbl, stretch=1)
        layout.addWidget(right, stretch=1)

        return bar

    # ------------------------------------------------------------------
    # Public API — called by MainWindow
    # ------------------------------------------------------------------

    def set_bot_name(self, name: str | None) -> None:
        self._name_lbl.setText(name or "No bot loaded")

    def set_bot_status(self, color: str, status: str, state_label: str, elapsed: str) -> None:
        self._dot_lbl.setStyleSheet(f"color: {color};")
        self._status_lbl.setText(status)

        self._elapsed_lbl.setText(elapsed)
        self._elapsed_lbl.setVisible(bool(elapsed))

        self._state_lbl.setText(state_label)
        self._state_lbl.setVisible(bool(state_label))
