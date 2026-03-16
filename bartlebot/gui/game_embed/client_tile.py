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
_GWL_STYLE      = -16
_WS_CHILD       = 0x40000000
_WS_VISIBLE     = 0x10000000
_WS_BORDER_MASK = 0x00CF0000  # WS_CAPTION | WS_BORDER | WS_DLGFRAME

_SWP_NOMOVE       = 0x0002
_SWP_NOSIZE       = 0x0001
_SWP_NOZORDER     = 0x0004
_SWP_FRAMECHANGED = 0x0020  # Required after SetWindowLongW to flush style change

_user32   = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize",        ctypes.wintypes.DWORD),
        ("flags",         ctypes.wintypes.DWORD),
        ("hwndActive",    ctypes.wintypes.HWND),
        ("hwndFocus",     ctypes.wintypes.HWND),
        ("hwndCapture",   ctypes.wintypes.HWND),
        ("hwndMenuOwner", ctypes.wintypes.HWND),
        ("hwndMoveSize",  ctypes.wintypes.HWND),
        ("hwndCaret",     ctypes.wintypes.HWND),
        ("rcCaret",       ctypes.wintypes.RECT),
    ]


def _get_client_size(hwnd: int) -> tuple[int, int]:
    """Return (width, height) of the window client area."""
    rect = ctypes.wintypes.RECT()
    _user32.GetClientRect(hwnd, ctypes.byref(rect))
    return rect.right - rect.left, rect.bottom - rect.top


class ClientTile(QWidget):
    """Hosts one embedded game window centered within the tile.

    The game window is reparented via Win32 SetParent (WS_CHILD) and displayed
    at its native resolution, centered with the system default background
    showing as letterbox bars if the tile is larger than the game.

    AttachThreadInput is used so that the Qt thread can call SetFocus on the
    game window (cross-thread SetFocus requires shared input queues).
    """

    def __init__(self, hwnd: int, label: str, parent=None) -> None:
        super().__init__(parent)
        self._hwnd  = hwnd
        self._label = label
        self._focused = False

        # Save original state for unembed/restoration
        self._orig_parent: int = _user32.GetParent(hwnd)
        self._orig_style:  int = _user32.GetWindowLongW(hwnd, _GWL_STYLE)

        # Detect native resolution before embedding
        self._game_w, self._game_h = _get_client_size(hwnd)

        # Thread IDs for AttachThreadInput and GetGUIThreadInfo
        _pid = ctypes.wintypes.DWORD(0)
        self._game_thread_id: int = _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(_pid))
        self._my_thread_id:   int = _kernel32.GetCurrentThreadId()

        self._embed()

    # ------------------------------------------------------------------
    # Embedding / unembedding
    # ------------------------------------------------------------------

    def _embed(self) -> None:
        tile_hwnd = int(self.winId())

        # 1. Reparent
        _user32.SetParent(self._hwnd, tile_hwnd)

        # 2. Strip decorations and set as child
        new_style = (self._orig_style & ~_WS_BORDER_MASK) | _WS_CHILD | _WS_VISIBLE
        _user32.SetWindowLongW(self._hwnd, _GWL_STYLE, new_style)

        # 3. Flush style change — required for decoration removal to take effect
        _user32.SetWindowPos(
            self._hwnd, 0, 0, 0, 0, 0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_FRAMECHANGED,
        )

        # 4. Attach input queues so the Qt thread can call SetFocus on the game
        #    window. Without this, cross-thread SetFocus is silently blocked.
        if self._game_thread_id and self._game_thread_id != self._my_thread_id:
            _user32.AttachThreadInput(self._my_thread_id, self._game_thread_id, True)

        self._reposition()

    def unembed(self) -> None:
        """Restore the game window to its original parent and style."""
        if self._game_thread_id and self._game_thread_id != self._my_thread_id:
            _user32.AttachThreadInput(self._my_thread_id, self._game_thread_id, False)

        _user32.SetParent(self._hwnd, self._orig_parent)
        _user32.SetWindowLongW(self._hwnd, _GWL_STYLE, self._orig_style)
        _user32.SetWindowPos(
            self._hwnd, 0, 0, 0, 0, 0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_FRAMECHANGED,
        )
        _user32.ShowWindow(self._hwnd, 5)  # SW_SHOW

    def is_game_focused(self) -> bool:
        """Return True if the game window (or a child of it) has keyboard focus.

        Uses GetGUIThreadInfo to query the game thread's focus state directly,
        which works across process boundaries.
        """
        if not self._game_thread_id:
            return False
        gti = _GUITHREADINFO(cbSize=ctypes.sizeof(_GUITHREADINFO))
        if not _user32.GetGUIThreadInfo(self._game_thread_id, ctypes.byref(gti)):
            return False
        focus = gti.hwndFocus
        return bool(focus) and (
            focus == self._hwnd or bool(_user32.IsChild(self._hwnd, focus))
        )

    def _reposition(self) -> None:
        """Center the game window at its native resolution within the tile.

        self.width()/height() are Qt logical pixels; MoveWindow uses Win32
        physical pixels.  Multiplying by devicePixelRatio() converts them so
        the centering offset is computed in the same coordinate space.
        """
        dpr = self.devicePixelRatio()
        tile_w = int(self.width()  * dpr)
        tile_h = int(self.height() * dpr)
        x = max(0, (tile_w - self._game_w) // 2)
        y = max(0, (tile_h - self._game_h) // 2)
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

        # Focus border — drawn around the game window area, not the whole tile.
        # Convert game dimensions to logical pixels (QPainter coordinate space).
        if self._focused:
            dpr = self.devicePixelRatio()
            gw = round(self._game_w / dpr)
            gh = round(self._game_h / dpr)
            gx = max(0, (self.width()  - gw) // 2)
            gy = max(0, (self.height() - gh) // 2)
            pen = painter.pen()
            pen.setColor(QColor("#4a90d9"))
            pen.setWidth(3)
            painter.setPen(pen)
            painter.drawRect(gx + 1, gy + 1, gw - 2, gh - 2)

        # Client label pill — painted on the Qt widget surface.
        # Note: this may be obscured by the embedded game window (which renders
        # on top as a Win32 child). A floating overlay widget can be added later
        # if the label needs to be always-on-top.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text = self._label
        text_w  = metrics.horizontalAdvance(text)
        text_h  = metrics.height()
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
