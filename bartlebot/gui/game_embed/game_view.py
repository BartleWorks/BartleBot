"""GameView: tiled display of embedded Wizard101 game windows."""

from __future__ import annotations

import ctypes
import ctypes.wintypes

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QLabel, QWidget

from bartlebot.gui.game_embed.client_tile import ClientTile, LABEL_RESERVE

_user32     = ctypes.windll.user32
_VK_LBUTTON = 0x01

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
        self._update_minimum_size()

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
        self._update_minimum_size()

    def unembed_all(self) -> None:
        """Unembed every client."""
        for idx in list(self._tiles.keys()):
            self.unembed_client(idx)

    def renumber_tiles(self, clients: list) -> None:
        """Re-key _tiles and update labels to match current list positions.

        Called after any change to the client list (removal or reorder) so that
        the game-view tile labels stay in sync with the sidebar and bot logic.
        """
        hwnd_to_old_idx = {tile._hwnd: old_idx for old_idx, tile in self._tiles.items()}
        new_tiles: dict[int, ClientTile] = {}
        for new_idx, client in enumerate(clients):
            old_idx = hwnd_to_old_idx.get(client.window_handle)
            if old_idx is not None and old_idx in self._tiles:
                tile = self._tiles[old_idx]
                tile._label = f"Client {new_idx + 1}"
                tile.update()
                new_tiles[new_idx] = tile
        self._tiles = new_tiles
        self._recompute_geometry()
        self._update_minimum_size()

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

    def _update_minimum_size(self) -> None:
        """Set GameView minimum size to fit the current client grid plus padding."""
        n = len(self._tiles)
        if n == 0:
            self.setMinimumSize(0, 0)
            return
        rows, cols = _GRID.get(n, (2, 2))
        tile = next(iter(self._tiles.values()))
        dpr = self.devicePixelRatio() or 1.0
        pad = 4
        self.setMinimumSize(
            int(cols * tile._game_w / dpr) + 2 * pad,
            int(rows * tile._game_h / dpr) + rows * LABEL_RESERVE + 2 * pad,
        )

    # ------------------------------------------------------------------
    # Focus polling
    # ------------------------------------------------------------------

    def _poll_focus(self) -> None:
        # If LMB was clicked since the last poll, check whether it landed over a
        # game window and give that window keyboard focus.  GetAsyncKeyState bit 0
        # is set if the key was pressed since the last call, so we reliably catch
        # short clicks even at a 250 ms polling interval.
        if _user32.GetAsyncKeyState(_VK_LBUTTON) & 0x0001:
            pt = ctypes.wintypes.POINT()
            _user32.GetCursorPos(ctypes.byref(pt))
            for tile in self._tiles.values():
                rect = ctypes.wintypes.RECT()
                _user32.GetWindowRect(tile._hwnd, ctypes.byref(rect))
                if rect.left <= pt.x < rect.right and rect.top <= pt.y < rect.bottom:
                    _user32.SetFocus(tile._hwnd)
                    break

        for tile in self._tiles.values():
            tile.set_focused(tile.is_game_focused())
