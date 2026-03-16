"""ClientTile: a QWidget that embeds one Wizard101 game window via Win32 SetParent."""

from __future__ import annotations

import ctypes
import ctypes.wintypes

from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QColor, QPainter, QFont
from PyQt6.QtWidgets import QWidget

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------
_GWL_STYLE     = -16
_WS_CHILD      = 0x40000000
_WS_VISIBLE    = 0x10000000
_WS_BORDER_MASK = 0x00CF0000  # WS_CAPTION | WS_BORDER | WS_DLGFRAME

_user32 = ctypes.windll.user32


def _get_window_rect(hwnd: int) -> tuple[int, int]:
    """Return (width, height) of the window client area."""
    rect = ctypes.wintypes.RECT()
    _user32.GetClientRect(hwnd, ctypes.byref(rect))
    return rect.right - rect.left, rect.bottom - rect.top


class ClientTile(QWidget):
    """Hosts one embedded game window centered within the tile.

    The game window is reparented via Win32 SetParent and displayed at its
    native resolution, centered with the system default background showing
    as letterbox bars if the tile is larger than the game.
    """

    def __init__(self, hwnd: int, label: str, parent=None) -> None:
        super().__init__(parent)
        self._hwnd = hwnd
        self._label = label
        self._focused = False

        # Save original state for unembed/restoration
        self._orig_parent: int = _user32.GetParent(hwnd)
        self._orig_style: int = _user32.GetWindowLongW(hwnd, _GWL_STYLE)

        # Detect native resolution before embedding
        self._game_w, self._game_h = _get_window_rect(hwnd)

        self._embed()

    # ------------------------------------------------------------------
    # Embedding / unembedding
    # ------------------------------------------------------------------

    def _embed(self) -> None:
        tile_hwnd = int(self.winId())
        _user32.SetParent(self._hwnd, tile_hwnd)
        new_style = (self._orig_style & ~_WS_BORDER_MASK) | _WS_CHILD | _WS_VISIBLE
        _user32.SetWindowLongW(self._hwnd, _GWL_STYLE, new_style)
        self._reposition()

    def unembed(self) -> None:
        """Restore the game window to its original parent and style."""
        _user32.SetParent(self._hwnd, self._orig_parent)
        _user32.SetWindowLongW(self._hwnd, _GWL_STYLE, self._orig_style)
        _user32.ShowWindow(self._hwnd, 5)  # SW_SHOW

    def _reposition(self) -> None:
        """Center the game window at its native resolution within the tile."""
        x = max(0, (self.width()  - self._game_w) // 2)
        y = max(0, (self.height() - self._game_h) // 2)
        _user32.MoveWindow(self._hwnd, x, y, self._game_w, self._game_h, True)

    # ------------------------------------------------------------------
    # Qt event overrides
    # ------------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition()

    def set_focused(self, focused: bool) -> None:
        if focused != self._focused:
            self._focused = focused
            self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)

        # Focus border
        if self._focused:
            border_color = QColor("#4a90d9")
            pen = painter.pen()
            pen.setColor(border_color)
            pen.setWidth(3)
            painter.setPen(pen)
            painter.drawRect(1, 1, self.width() - 2, self.height() - 2)

        # Client label pill (top-left)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text = self._label
        text_w = metrics.horizontalAdvance(text)
        text_h = metrics.height()
        pill_x, pill_y = 8, 8
        pill_w = text_w + 16
        pill_h = text_h + 6
        painter.setBrush(QColor(0, 0, 0, 160))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(pill_x, pill_y, pill_w, pill_h, 4, 4)
        painter.setPen(QColor(255, 255, 255, 230))
        painter.drawText(
            QRect(pill_x + 8, pill_y + 3, text_w, text_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text,
        )
