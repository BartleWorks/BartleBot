"""ClientTile: a QWidget that embeds one Wizard101 game window via Win32 SetParent."""

from __future__ import annotations

from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QPainter
from PyQt6.QtWidgets import QWidget

from bartlebot import streamer_mode
from bartlebot.gui.game_embed.win32_window import EmbeddedWindow
from bartlebot.gui.icon_utils import svg_pixmap
from bartlebot.gui.theme import THEME

FOCUS_BORDER = 6  # logical pixels of red fill around the game window when focused
_LABEL_GAP = 3  # logical pixels between the bottom of the pill and the top of the focus border
_LABEL_PILL_H = 26  # conservative upper bound on pill height (max(text_h, ICON_SIZE) + 6)
LABEL_RESERVE = (
    FOCUS_BORDER + _LABEL_GAP + _LABEL_PILL_H + 4
)  # = 39 px; keeps label clear of border at minimum tile size

# Pill icon constants
_ICON_SIZE = 18
_ICON_GAP = 6  # pixels between label text and detach icon


def pill_text(label: str, wizard_name: str | None) -> str:
    """Compose the pill caption: the slot label, plus the wizard name once it is known.

    Kept separate from the label itself because the two change independently — reordering
    clients renumbers "Player N" while the wizard stays put, and a character switch
    replaces the wizard while the slot stays put. Composing at paint time means neither
    update can clobber the other.

    Streamer Mode drops the name and keeps the label, rather than substituting the
    positional placeholder streamer_mode.display_name would give: the slot label *is*
    a position already, so appending a second one would be redundant at best and
    contradictory at worst — a client in slot 2 can be the account's fourth character.
    """
    if wizard_name and not streamer_mode.enabled():
        return f"{label} · {wizard_name}"
    return label


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
        self._wizard_name: str | None = None
        self._focused = False

        # Pill hit-testing rects (updated each paintEvent)
        self._pill_rect: QRect = QRect()
        self._icon_rect: QRect = QRect()

        # Constructed before embedding: it captures the state needed to restore the
        # window later (parent, styles, screen rect, native resolution).
        self._window = EmbeddedWindow(hwnd)
        self._game_w, self._game_h = self._window.game_size

        self._window.embed(int(self.winId()))
        self._reposition()

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    @property
    def hwnd(self) -> int:
        """The embedded game window's HWND."""
        return self._hwnd

    @property
    def label(self) -> str:
        """The slot label shown on the pill (e.g. "Player 2")."""
        return self._label

    @property
    def game_size(self) -> tuple[int, int]:
        """Native game resolution in physical pixels, measured before embedding."""
        return self._game_w, self._game_h

    # ------------------------------------------------------------------
    # Embedding / unembedding
    # ------------------------------------------------------------------

    def set_label(self, label: str) -> None:
        """Set the slot label (e.g. "Player 2"), repainting if it changed."""
        if label != self._label:
            self._label = label
            self.update()

    def set_wizard_name(self, name: str | None) -> None:
        """Set the wizard name shown beside the slot label, repainting if it changed."""
        if name != self._wizard_name:
            self._wizard_name = name
            self.update()

    @property
    def wizard_name(self) -> str | None:
        """The wizard name currently shown on this tile, if any."""
        return self._wizard_name

    def unembed(self) -> None:
        """Restore the game window to a normal, movable top-level window."""
        self._window.restore()

    def focus_game(self) -> None:
        """Give the embedded game window keyboard focus."""
        self._window.focus()

    def is_game_focused(self) -> bool:
        """Return True if the game window (or a child of it) has keyboard focus."""
        return self._window.is_focused()

    def _reposition(self) -> None:
        """Center the game window at its native resolution within the tile.

        self.width()/height() are Qt logical pixels; MoveWindow uses Win32
        physical pixels.  Multiplying by devicePixelRatio() converts them so
        the centering offset is computed in the same coordinate space.

        LABEL_RESERVE logical pixels are kept clear at the top of the tile so
        the client label pill is never obscured by the Win32 child window.

        No-op once the window has been restored: a resizeEvent can still arrive
        between unembed() and the tile being deleted, and moving the window then
        would drag the popped-out game window back to tile coordinates.
        """
        if not self._window.is_embedded:
            return
        dpr = self.devicePixelRatio()
        tile_w = int(self.width() * dpr)
        tile_h = int(self.height() * dpr) - int(LABEL_RESERVE * dpr)
        x = max(0, (tile_w - self._game_w) // 2)
        y = int(LABEL_RESERVE * dpr) + max(0, (tile_h - self._game_h) // 2)
        self._window.move(x, y, self._game_w, self._game_h)

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
            if event.button() == Qt.MouseButton.LeftButton:
                self.focus_game()
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
        text = pill_text(self._label, self._wizard_name)
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
        self._wizard_name: str | None = None
        self._pill_rect: QRect = QRect()
        self._icon_rect: QRect = QRect()

    def set_label(self, label: str) -> None:
        """Set the slot label, repainting if it changed."""
        if label != self._label:
            self._label = label
            self.update()

    def set_wizard_name(self, name: str | None) -> None:
        """Set the wizard name shown beside the slot label, repainting if it changed."""
        if name != self._wizard_name:
            self._wizard_name = name
            self.update()

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
            "Player game window not currently embedded",
        )

        # Pill label (same layout as ClientTile, but with attach icon)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pill_font = QFont()
        pill_font.setPointSize(9)
        pill_font.setBold(True)
        painter.setFont(pill_font)
        metrics = painter.fontMetrics()
        text = pill_text(self._label, self._wizard_name)
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
