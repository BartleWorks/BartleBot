"""GameView: tiled display of embedded Wizard101 game windows."""

from __future__ import annotations

import ctypes

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QLabel, QWidget

from bartlebot.gui.game_embed.client_tile import ClientTile

_user32 = ctypes.windll.user32

# Grid layouts: number of clients → (rows, cols)
_GRID: dict[int, tuple[int, int]] = {
    1: (1, 1),
    2: (1, 2),
    3: (2, 2),
    4: (2, 2),
}


class GameView(QWidget):
    """Container that shows N ClientTile widgets in a grid layout.

    Tiles are positioned manually (not via a QLayout) so that we can recompute
    geometry precisely on resize without Qt interfering with the Win32-embedded
    child windows.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tiles: dict[int, ClientTile] = {}

        # Placeholder shown when no clients are embedded
        self._placeholder = QLabel("No game window", self)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: #888; font-size: 14px;")
        self._placeholder.show()

        # Poll focus every 250 ms to update tile borders
        self._focus_timer = QTimer(self)
        self._focus_timer.setInterval(250)
        self._focus_timer.timeout.connect(self._poll_focus)
        self._focus_timer.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed_client(self, hwnd: int, client_idx: int) -> None:
        """Embed the game window identified by *hwnd* as client *client_idx*."""
        if client_idx in self._tiles:
            return  # already embedded
        label = f"Client {client_idx + 1}"
        tile = ClientTile(hwnd, label, parent=self)
        self._tiles[client_idx] = tile
        tile.show()
        self._placeholder.setVisible(False)
        self._recompute_geometry()

    def unembed_client(self, client_idx: int) -> None:
        """Unembed and remove the tile for *client_idx*."""
        tile = self._tiles.pop(client_idx, None)
        if tile is None:
            return
        tile.unembed()
        tile.hide()
        tile.setParent(None)  # type: ignore[arg-type]
        tile.deleteLater()
        if not self._tiles:
            self._placeholder.setVisible(True)
        self._recompute_geometry()

    def unembed_all(self) -> None:
        """Unembed every client."""
        for idx in list(self._tiles.keys()):
            self.unembed_client(idx)

    def toggle(self) -> None:
        """Show or hide the GameView (used by the toggle_embed keybinding)."""
        visible = not self.isVisible()
        self.setVisible(visible)
        # Notify parent MainWindow to adjust splitter sizes
        parent = self.parent()
        while parent is not None:
            if hasattr(parent, "show_game_view"):
                parent.show_game_view(visible)
                return
            parent = parent.parent()

    def cycle_focus(self) -> None:
        """Move OS keyboard focus to the next embedded client tile."""
        if not self._tiles:
            return
        indices = sorted(self._tiles)
        fg = _user32.GetForegroundWindow()
        current_idx: int | None = None
        for idx, tile in self._tiles.items():
            if tile._hwnd == fg or _user32.GetParent(fg) == tile._hwnd:
                current_idx = idx
                break
        if current_idx is None:
            next_idx = indices[0]
        else:
            pos = indices.index(current_idx)
            next_idx = indices[(pos + 1) % len(indices)]
        _user32.SetForegroundWindow(self._tiles[next_idx]._hwnd)

    # ------------------------------------------------------------------
    # Qt event overrides
    # ------------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._recompute_geometry()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._recompute_geometry()

    # ------------------------------------------------------------------
    # Internal geometry management
    # ------------------------------------------------------------------

    def _recompute_geometry(self) -> None:
        """Lay out tiles in the grid and update placeholder position."""
        self._placeholder.setGeometry(0, 0, self.width(), self.height())

        n = len(self._tiles)
        if n == 0:
            return

        rows, cols = _GRID.get(n, (2, 2))
        tile_w = self.width()  // cols
        tile_h = self.height() // rows

        for pos, (idx, tile) in enumerate(sorted(self._tiles.items())):
            row = pos // cols
            col = pos  % cols
            tile.setGeometry(col * tile_w, row * tile_h, tile_w, tile_h)

    # ------------------------------------------------------------------
    # Focus polling
    # ------------------------------------------------------------------

    def _poll_focus(self) -> None:
        fg = _user32.GetForegroundWindow()
        for tile in self._tiles.values():
            hwnd = tile._hwnd
            focused = (hwnd == fg) or bool(_user32.IsChild(hwnd, fg))
            tile.set_focused(focused)
