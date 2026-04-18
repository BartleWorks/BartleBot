"""ClientTile: a QWidget that embeds one Wizard101 game window via Win32 SetParent."""

from __future__ import annotations

import ctypes
import ctypes.wintypes

from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QPainter
from PyQt6.QtWidgets import QWidget

from bartlebot.gui.icon_utils import svg_pixmap
from bartlebot.gui.theme import THEME

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------
_GWL_STYLE = -16
_WS_CHILD = 0x40000000
_WS_VISIBLE = 0x10000000
_WS_BORDER_MASK = 0x00CF0000  # WS_CAPTION | WS_BORDER | WS_DLGFRAME

FOCUS_BORDER = 6  # logical pixels of red fill around the game window when focused
_LABEL_GAP = 3  # logical pixels between the bottom of the pill and the top of the focus border
_LABEL_PILL_H = 26  # conservative upper bound on pill height (max(text_h, ICON_SIZE) + 6)
LABEL_RESERVE = (
    FOCUS_BORDER + _LABEL_GAP + _LABEL_PILL_H + 4
)  # = 39 px; keeps label clear of border at minimum tile size

_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_FRAMECHANGED = 0x0020  # Required after SetWindowLongW to flush style change

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_user32.IsChild.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.HWND]
_user32.IsChild.restype = ctypes.wintypes.BOOL

# Pill icon constants
_ICON_SIZE = 18
_ICON_GAP = 6  # pixels between label text and detach icon


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("hwndActive", ctypes.wintypes.HWND),
        ("hwndFocus", ctypes.wintypes.HWND),
        ("hwndCapture", ctypes.wintypes.HWND),
        ("hwndMenuOwner", ctypes.wintypes.HWND),
        ("hwndMoveSize", ctypes.wintypes.HWND),
        ("hwndCaret", ctypes.wintypes.HWND),
        ("rcCaret", ctypes.wintypes.RECT),
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

    detach_requested = pyqtSignal()

    def __init__(self, hwnd: int, label: str, parent=None) -> None:
        super().__init__(parent)
        self._hwnd = hwnd
        self._label = label
        self._focused = False

        # Pill hit-testing rects (updated each paintEvent)
        self._pill_rect: QRect = QRect()
        self._icon_rect: QRect = QRect()

        # Save original state for unembed/restoration
        self._orig_parent: int = _user32.GetParent(hwnd)
        self._orig_style: int = _user32.GetWindowLongW(hwnd, _GWL_STYLE)
        _orig_rect = ctypes.wintypes.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(_orig_rect))
        self._orig_w = _orig_rect.right - _orig_rect.left
        self._orig_h = _orig_rect.bottom - _orig_rect.top

        # Detect native resolution before embedding
        self._game_w, self._game_h = _get_client_size(hwnd)

        # Thread IDs for AttachThreadInput and GetGUIThreadInfo
        _pid = ctypes.wintypes.DWORD(0)
        self._game_thread_id: int = _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(_pid))
        self._my_thread_id: int = _kernel32.GetCurrentThreadId()

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
            self._hwnd,
            0,
            0,
            0,
            0,
            0,
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
            self._hwnd,
            0,
            0,
            self._orig_w,
            self._orig_h,
            _SWP_NOMOVE | _SWP_NOZORDER | _SWP_FRAMECHANGED,
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
        return bool(focus) and (focus == self._hwnd or bool(_user32.IsChild(self._hwnd, focus)))

    def _reposition(self) -> None:
        """Center the game window at its native resolution within the tile.

        self.width()/height() are Qt logical pixels; MoveWindow uses Win32
        physical pixels.  Multiplying by devicePixelRatio() converts them so
        the centering offset is computed in the same coordinate space.

        LABEL_RESERVE logical pixels are kept clear at the top of the tile so
        the client label pill is never obscured by the Win32 child window.
        """
        dpr = self.devicePixelRatio()
        tile_w = int(self.width() * dpr)
        tile_h = int(self.height() * dpr) - int(LABEL_RESERVE * dpr)
        x = max(0, (tile_w - self._game_w) // 2)
        y = int(LABEL_RESERVE * dpr) + max(0, (tile_h - self._game_h) // 2)
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

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._icon_rect.contains(event.pos()):
            self.detach_requested.emit()
        else:
            super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)

        # Compute game window position in logical pixels (QPainter coordinate space).
        dpr = self.devicePixelRatio()
        gw = round(self._game_w / dpr)
        gh = round(self._game_h / dpr)
        gx = max(0, (self.width() - gw) // 2)
        gy = LABEL_RESERVE + max(0, (self.height() - LABEL_RESERVE - gh) // 2)

        # Focus highlight — filled accent rectangle behind the game window.
        # The Win32 child covers the game area, leaving only the surrounding
        # _FOCUS_BORDER-wide strip visible as the accent highlight.
        if self._focused:
            painter.setBrush(THEME.qcolor("accent"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(
                gx - FOCUS_BORDER,
                gy - FOCUS_BORDER,
                gw + 2 * FOCUS_BORDER,
                gh + 2 * FOCUS_BORDER,
            )

        # Client label pill — rendered in the LABEL_RESERVE strip above the game
        # window, so it is never obscured by the Win32 child surface.
        # Layout: [8px pad] [label text] [_ICON_GAP] [detach icon] [8px pad]
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text = self._label
        text_w = metrics.horizontalAdvance(text)
        text_h = metrics.height()

        pill_w = text_w + _ICON_GAP + _ICON_SIZE + 16  # 8px left + 8px right padding
        pill_h = max(text_h, _ICON_SIZE) + 6
        pill_x = (self.width() - pill_w) // 2
        pill_y = gy - FOCUS_BORDER - _LABEL_GAP - pill_h

        # Pill background
        painter.setBrush(THEME.qcolor_client_pill_bg())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(pill_x, pill_y, pill_w, pill_h, 4, 4)

        # Label text
        painter.setPen(THEME.qcolor_client_pill_text())
        painter.drawText(
            QRect(pill_x + 8, pill_y + 3, text_w, text_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text,
        )

        # Detach icon (white-tinted, right side of pill)
        icon_pm = svg_pixmap("detach", _ICON_SIZE, tint="#ffffff")
        ix = pill_x + 8 + text_w + _ICON_GAP
        iy = pill_y + (pill_h - _ICON_SIZE) // 2
        painter.drawPixmap(ix, iy, icon_pm)

        # Store rects for hit-testing in mousePressEvent
        self._pill_rect = QRect(pill_x, pill_y, pill_w, pill_h)
        self._icon_rect = QRect(ix - 2, pill_y, _ICON_SIZE + 4, pill_h)  # 2px buffer


class DetachedTile(QWidget):
    """Placeholder shown in a grid slot when the game window is detached (floating free).

    Renders the same pill label as ClientTile but with an attach icon.  Clicking
    the icon emits ``reattach_requested`` so GameView can re-embed the window.
    """

    reattach_requested = pyqtSignal()

    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        self._label = label
        self._pill_rect: QRect = QRect()
        self._icon_rect: QRect = QRect()

    # ------------------------------------------------------------------
    # Qt event overrides
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._icon_rect.contains(event.pos()):
            self.reattach_requested.emit()
        else:
            super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)

        # Body text
        body_top = LABEL_RESERVE
        body_h = self.height() - body_top
        painter.setPen(THEME.qcolor("text_muted"))
        body_font = QFont()
        body_font.setPointSize(9)
        painter.setFont(body_font)
        painter.drawText(
            QRect(0, body_top, self.width(), body_h),
            Qt.AlignmentFlag.AlignCenter,
            "Client game window not currently embedded",
        )

        # Pill label (same layout as ClientTile, but with attach icon)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pill_font = QFont()
        pill_font.setPointSize(9)
        pill_font.setBold(True)
        painter.setFont(pill_font)
        metrics = painter.fontMetrics()
        text = self._label
        text_w = metrics.horizontalAdvance(text)
        text_h = metrics.height()

        pill_w = text_w + _ICON_GAP + _ICON_SIZE + 16
        pill_h = max(text_h, _ICON_SIZE) + 6
        pill_x = (self.width() - pill_w) // 2
        pill_y = FOCUS_BORDER + _LABEL_GAP

        painter.setBrush(THEME.qcolor_client_pill_bg())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(pill_x, pill_y, pill_w, pill_h, 4, 4)

        painter.setPen(THEME.qcolor_client_pill_text())
        painter.drawText(
            QRect(pill_x + 8, pill_y + 3, text_w, text_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text,
        )

        icon_pm = svg_pixmap("attach", _ICON_SIZE, tint="#ffffff")
        ix = pill_x + 8 + text_w + _ICON_GAP
        iy = pill_y + (pill_h - _ICON_SIZE) // 2
        painter.drawPixmap(ix, iy, icon_pm)

        self._pill_rect = QRect(pill_x, pill_y, pill_w, pill_h)
        self._icon_rect = QRect(ix - 2, pill_y, _ICON_SIZE + 4, pill_h)
