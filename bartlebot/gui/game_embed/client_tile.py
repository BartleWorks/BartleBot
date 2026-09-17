"""ClientTile: a QWidget that embeds one Wizard101 game window via Win32 SetParent."""

from __future__ import annotations

import time

from PyQt6.QtCore import QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from bartlebot import streamer_mode
from bartlebot.game_sizing import FOCUS_BORDER, LABEL_GAP, LABEL_RESERVE
from bartlebot.gui.account_accent import TINT_ALPHA_PERCENT
from bartlebot.gui.game_embed.win32_window import EmbeddedWindow
from bartlebot.gui.icon_utils import svg_pixmap
from bartlebot.gui.theme import THEME
from bartlebot.nodes.timing import LAUNCH_STAGE_ELAPSED_HINT

# FOCUS_BORDER and LABEL_RESERVE are imported rather than defined here: how much chrome
# a tile wraps around its game window is a term in the screen-fit arithmetic as well as
# a drawing constant, and bartlebot/game_sizing.py cannot import a QWidget to find out.
__all__ = [
    "FOCUS_BORDER",
    "LABEL_RESERVE",
    "ClientTile",
    "DetachedTile",
    "PendingTile",
    "launch_status_text",
    "pill_text",
    "tile_focus_color",
    "tile_pill_colors",
]

# Pill icon constants
_ICON_SIZE = 18
_ICON_GAP = 6  # pixels between label text and detach icon


def tile_pill_colors(color_key: int | None) -> tuple[QColor, QColor, QColor]:
    """``(fill, border, text)`` for a tile's caption pill, read from the live theme.

    The same claim the ``P{n}`` pills make on the player cards and the Combat tab (#40):
    outlined and tinted in the account's colour when the account is known, and the neutral
    grey pill when it is not -- never a default hue, because a colour here says which
    account is in the window. The text and icon take the same colour as each other.

    Read at paint time, like everything else a tile draws, so a theme switch needs nothing
    re-applied.
    """
    if color_key is None:
        grey = THEME.qcolor("pill_neutral_bg")
        return grey, grey, THEME.qcolor("pill_text")
    accent = THEME.account_color(color_key)
    fill = QColor(accent)
    fill.setAlpha(round(255 * TINT_ALPHA_PERCENT / 100))
    return fill, accent, THEME.qcolor("text")


def tile_focus_color(color_key: int | None) -> QColor:
    """The highlight round a focused game window: its account's colour, else the accent."""
    return THEME.account_color(color_key) if color_key is not None else THEME.qcolor("accent")


def _draw_pill(painter: QPainter, rect: QRect, color_key: int | None) -> QColor:
    """Paint the capsule behind a tile caption, and hand back the colour to write in.

    *rect* is the pill's size exactly as it always was; only the shape and the colours
    changed. The 1px outline is stroked half a pixel inside the rect, so the outer edge is
    still *rect* and nothing next to the pill moves.
    """
    fill, border, text = tile_pill_colors(color_key)
    painter.setBrush(fill)
    painter.setPen(QPen(border, 1))
    radius = (rect.height() - 1) / 2
    painter.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    return text


def launch_status_text(
    status: str | None,
    since: float | None,
    now: float | None = None,
    failed: bool = False,
) -> str:
    """The caption under a pending tile's spinner: the stage, and how long it has run.

    The elapsed part only appears once a stage has run longer than a stage normally does.
    Below that threshold the number is noise — every launch spends some seconds in every
    stage, and a counter running on all of them reads as a stopwatch rather than as a
    warning. Above it, it draws the distinction the stage name cannot: a launch stalled at
    the login screen and a launch progressing normally say the same thing otherwise, which
    was the whole complaint.

    A stage with no name yet — the launcher has not reported one — still says something,
    because a tile that says nothing is the state this replaced.

    A failed launch gets neither the ellipsis nor the clock: it is not still doing the
    thing, and how long it has been finished is not information.
    """
    label = status or "Launching"
    if failed:
        return label
    if since is None:
        return f"{label}…"
    elapsed = (time.monotonic() if now is None else now) - since
    if elapsed < LAUNCH_STAGE_ELAPSED_HINT:
        return f"{label}…"
    return f"{label}… {int(elapsed)}s"


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
        # The account in this window, as a palette slot, or None -- see tile_pill_colors.
        self._color_key: int | None = None

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

    @property
    def color_key(self) -> int | None:
        """The account palette slot the pill and focus border wear, or None."""
        return self._color_key

    def set_color_key(self, key: int | None) -> None:
        """Colour the pill and the focus border by an account, repainting if it changed."""
        if key != self._color_key:
            self._color_key = key
            self.update()

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

        # Focus highlight — filled rectangle behind the game window, in the account's
        # colour (the accent when none is known). The Win32 child covers the game area,
        # leaving only the surrounding _FOCUS_BORDER-wide strip visible as the highlight.
        if self._focused:
            painter.setBrush(tile_focus_color(self._color_key))
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
        font = THEME.ui_font(bold=True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text = pill_text(self._label, self._wizard_name)
        text_w = metrics.horizontalAdvance(text)
        text_h = metrics.height()

        pill_w = text_w + _ICON_GAP + _ICON_SIZE + 16  # 8px left + 8px right padding
        pill_h = max(text_h, _ICON_SIZE) + 6
        pill_x = (self.width() - pill_w) // 2
        pill_y = gy - FOCUS_BORDER - LABEL_GAP - pill_h

        # Pill background
        text_color = _draw_pill(painter, QRect(pill_x, pill_y, pill_w, pill_h), self._color_key)

        # Label text
        painter.setPen(text_color)
        painter.drawText(
            QRect(pill_x + 8, pill_y + 3, text_w, text_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text,
        )

        # Detach icon (right side of pill, in the text's colour)
        icon_pm = svg_pixmap("detach", _ICON_SIZE, tint=text_color.name())
        ix = pill_x + 8 + text_w + _ICON_GAP
        iy = pill_y + (pill_h - _ICON_SIZE) // 2
        painter.drawPixmap(ix, iy, icon_pm)

        # Store rects for hit-testing in mousePressEvent
        self._pill_rect = QRect(pill_x, pill_y, pill_w, pill_h)
        self._icon_rect = QRect(ix - 2, pill_y, _ICON_SIZE + 4, pill_h)  # 2px buffer


class _WindowlessTile(QWidget):
    """Shared drawing for the tile kinds that hold a slot without a game window in it.

    Two of them now -- a detached window, and a launch that has not produced a window
    yet -- and they differ only in the icon on the pill and the line of body text under
    it. The pill layout is the part worth having in one place: it has to agree with
    ``ClientTile``'s to the pixel, because a tile changing kind must not move its caption,
    and it was already written twice before there was a third kind to write it a third
    time.

    ``ClientTile`` is deliberately not folded in here. It paints its pill *around* a Win32
    child surface, at coordinates derived from the embedded window's own geometry, and the
    order of that against the focus highlight is most of why its paintEvent looks the way
    it does. Sharing the layout constants is the honest amount of sharing there.
    """

    #: Icon drawn at the right of the pill, and the click target for ``_on_icon_clicked``.
    _pill_icon = ""

    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        self._label = label
        self._wizard_name: str | None = None
        self._color_key: int | None = None
        self._pill_rect: QRect = QRect()
        self._icon_rect: QRect = QRect()

    @property
    def label(self) -> str:
        """The slot label shown on the pill."""
        return self._label

    @property
    def color_key(self) -> int | None:
        """The account palette slot the pill wears, or None -- see tile_pill_colors."""
        return self._color_key

    def set_color_key(self, key: int | None) -> None:
        """Colour the pill by an account, repainting if it changed."""
        if key != self._color_key:
            self._color_key = key
            self.update()

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

    @property
    def wizard_name(self) -> str | None:
        """The wizard name currently shown on this tile, if any."""
        return self._wizard_name

    def _body_text(self) -> str:
        """The line drawn under the pill, saying why there is no game window here."""
        return ""

    def _on_icon_clicked(self) -> None:
        """Called when the pill icon is clicked. Subclasses emit their own signal."""

    # ------------------------------------------------------------------
    # Qt event overrides
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._icon_rect.contains(event.pos()):
            self._on_icon_clicked()
        else:
            super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        self._paint_body(painter)
        # PendingTile repaints just its spinner, several times a second; the pill is
        # nowhere near it and is the expensive half of this widget (font metrics, and an
        # SVG rasterised for the icon). Its rect is left as the last full paint set it,
        # which is still where it is -- the layout only moves on a resize, and a resize
        # repaints the whole tile.
        if self._pill_rect.isEmpty() or event.rect().intersects(self._pill_rect):
            self._paint_pill(painter)

    def _paint_body(self, painter: QPainter) -> None:
        """Draw the explanatory line in the space a game window would occupy."""
        body_top = LABEL_RESERVE
        painter.setPen(THEME.qcolor("text_muted"))
        body_font = THEME.ui_font()
        painter.setFont(body_font)
        painter.drawText(
            QRect(0, body_top, self.width(), self.height() - body_top),
            Qt.AlignmentFlag.AlignCenter,
            self._body_text(),
        )

    def _paint_pill(self, painter: QPainter) -> None:
        """Draw the caption pill, where ClientTile draws its own."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pill_font = THEME.ui_font(bold=True)
        painter.setFont(pill_font)
        metrics = painter.fontMetrics()
        text = pill_text(self._label, self._wizard_name)
        text_w = metrics.horizontalAdvance(text)
        text_h = metrics.height()

        # A tile with nothing to click has no icon, and then the space and the gap for one
        # are not reserved either -- a pill padded out around an absent icon reads as a
        # button that has lost its glyph rather than as a caption.
        icon_w = _ICON_GAP + _ICON_SIZE if self._pill_icon else 0
        pill_w = text_w + icon_w + 16  # 8px left + 8px right padding
        pill_h = max(text_h, _ICON_SIZE) + 6
        pill_x = (self.width() - pill_w) // 2
        pill_y = FOCUS_BORDER + LABEL_GAP

        text_color = _draw_pill(painter, QRect(pill_x, pill_y, pill_w, pill_h), self._color_key)

        painter.setPen(text_color)
        painter.drawText(
            QRect(pill_x + 8, pill_y + 3, text_w, text_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text,
        )

        self._pill_rect = QRect(pill_x, pill_y, pill_w, pill_h)
        if not self._pill_icon:
            self._icon_rect = QRect()
            return

        icon_pm = svg_pixmap(self._pill_icon, _ICON_SIZE, tint=text_color.name())
        ix = pill_x + 8 + text_w + _ICON_GAP
        iy = pill_y + (pill_h - _ICON_SIZE) // 2
        painter.drawPixmap(ix, iy, icon_pm)
        self._icon_rect = QRect(ix - 2, pill_y, _ICON_SIZE + 4, pill_h)  # 2px buffer


class DetachedTile(_WindowlessTile):
    """Placeholder shown in a grid slot when the game window is detached (floating free).

    Renders the same pill label as ClientTile but with an attach icon.  Clicking
    the icon emits ``reattach_requested`` so GameView can re-embed the window.
    """

    reattach_requested = pyqtSignal()

    _pill_icon = "attach"

    def _body_text(self) -> str:
        return "Player game window not currently embedded"

    def _on_icon_clicked(self) -> None:
        self.reattach_requested.emit()


def _body_font() -> QFont:
    """The caption font under the spinner.

    A function rather than a module constant because ``_spinner_rect`` has to measure the
    same font ``_paint_body`` lays the caption out in: the two disagreeing by a pixel puts
    the partial-repaint rect off the ring it is meant to cover, and the arc then leaves a
    sliver of the previous frame behind as it turns.
    """
    return THEME.ui_font()


# Busy indicator geometry and motion. One revolution every _SPINNER_PERIOD_MS, repainted
# every _SPINNER_TICK_MS.
#
# The tick is a frame rate, not the motion itself: the angle is read from the clock at
# paint time, so a tick that arrives late draws the frame it should have drawn rather than
# the next one along. That distinction is the whole reason the spinner looks smooth during
# a launch -- a launch spends much of its time in synchronous memory reads and Win32 calls
# that hold the event loop for tens of milliseconds, so ticks *do* arrive late, and an
# angle accumulated per tick turns every one of those into a visible hitch (a jump, then a
# slow patch) instead of a frame that is merely skipped.
_SPINNER_RADIUS = 14
_SPINNER_THICKNESS = 3
_SPINNER_ARC_DEGREES = 100
_SPINNER_PERIOD_MS = 1200
_SPINNER_TICK_MS = 16
# Gap between the spinner and the caption under it.
_SPINNER_CAPTION_GAP = 14


class PendingTile(_WindowlessTile):
    """Placeholder shown in a reserved slot while that account's launch is still running.

    Drawn from the moment the launch is asked for, which is the point of it: a launch takes
    tens of seconds to minutes before a window exists, and until this the app looked like
    nothing had happened for that whole window -- so the natural response to a slow launch
    was to click again, and a second click really did start a second batch.

    Its life is short relative to the launch. The window is titled seconds after login and
    takes over the slot as a real ``ClientTile`` then, long before the character is in game,
    so this covers spawn-to-login rather than the whole wait. That is the honest boundary:
    once there is a window on screen the user can see for themselves that something is
    happening.
    """

    #: Emitted when the user clicks the dismiss icon on a failed launch.
    dismiss_requested = pyqtSignal()

    def __init__(self, label: str, parent=None) -> None:
        super().__init__(label, parent)
        self._status: str | None = None
        self._status_since: float | None = None
        self._failed = False
        self._painted_text: str | None = None
        self._spin_start = time.monotonic()
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(_SPINNER_TICK_MS)
        # Coarse timers -- Qt's default above 20ms -- are allowed to drift their interval
        # by up to 5% to coalesce wakeups with other timers, which at a 16ms frame is
        # exactly the jitter this is trying not to show.
        self._spin_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._spin_timer.timeout.connect(self._advance)
        self._spin_timer.start()

    @property
    def _pill_icon(self) -> str:
        """No icon while a launch is running — there is nothing to do about it yet.

        A failed one gets a dismiss cross, which is the only thing that clears it: the
        attempt is deliberately left open so the tile can keep saying what happened, and
        the user acknowledging it is what gives the slot back.
        """
        return "dismiss" if self._failed else ""

    @property
    def _angle(self) -> float:
        """Where the arc has got to, in degrees, read from the clock rather than counted.

        See the note on _SPINNER_TICK_MS: at 60fps a frame the event loop misses has to
        cost a frame, not a step, or the launch's own stalls come back out as stutter.
        """
        return (time.monotonic() - self._spin_start) * 1000 / _SPINNER_PERIOD_MS * 360 % 360

    def _spinner_rect(self) -> QRect:
        """The square the ring is drawn in, padded by the pen width it is stroked with."""
        body_top = LABEL_RESERVE
        body_h = self.height() - body_top
        if body_h <= 0:
            return QRect()
        block_h = 2 * _SPINNER_RADIUS + _SPINNER_CAPTION_GAP + QFontMetrics(_body_font()).height()
        top = body_top + max(0, (body_h - block_h) // 2)
        cx = self.width() // 2
        pad = _SPINNER_THICKNESS
        return QRect(
            cx - _SPINNER_RADIUS - pad,
            top - pad,
            2 * (_SPINNER_RADIUS + pad),
            2 * (_SPINNER_RADIUS + pad),
        )

    def _advance(self) -> None:
        """Repaint the ring, and only the ring.

        The caption under it is unchanged for a second at a time and the pill above it for
        the whole life of the tile, so repainting the tile 60 times a second to move an
        arc would redraw both of those 60 times for nothing -- and with several tiles
        pending at once, that is the difference between a spinner that costs nothing and
        one that competes with the launch it is reporting on. ``_paint_caption`` asks for
        the rest when the text actually changes.

        Parented to the tile, so Qt stops the timer when the tile is deleted -- a pending
        tile is destroyed the moment its window appears, and a timer outliving it would
        repaint a widget that is on its way out.
        """
        # The caption ages on the clock, not on a report (see set_status), so the tick is
        # also what notices it has changed -- roughly once a second, against sixty frames.
        if self.status_text != self._painted_text:
            self.update()
        else:
            self.update(self._spinner_rect())

    def set_status(self, status: str | None, since: float | None, failed: bool = False) -> None:
        """Say which stage this launch is in, and when it entered that stage.

        *since* is a ``time.monotonic`` reading rather than a duration, so the caption ages
        on its own between reports. It has to: the stages are coarse and the slow one is
        the last, so a launch can sit in ``Loading`` for minutes with nothing new to report
        — and a caption that only changed when a report arrived would freeze for exactly
        the stretch the user most wants to know about.
        """
        if (status, since, failed) == (self._status, self._status_since, self._failed):
            return
        self._status, self._status_since, self._failed = status, since, failed
        # A stopped launch does not spin. Leaving the timer running would also keep
        # repainting a tile that has nothing left to say, several times a second, for as
        # long as the user leaves it up.
        if failed:
            self._spin_timer.stop()
        elif not self._spin_timer.isActive():
            self._spin_timer.start()
        self.update()

    @property
    def status_text(self) -> str:
        """The caption as it currently reads, elapsed hint and all."""
        return launch_status_text(self._status, self._status_since, failed=self._failed)

    @property
    def failed(self) -> bool:
        """Whether this tile is showing a launch that gave up."""
        return self._failed

    def _on_icon_clicked(self) -> None:
        self.dismiss_requested.emit()

    def _body_text(self) -> str:
        return self.status_text

    def _paint_body(self, painter: QPainter) -> None:
        """Draw the spinner with the caption beneath it, together in the tile's body.

        Overridden rather than added to: the base centres its one line in the whole body,
        and a spinner drawn independently would either sit on top of that line or push it
        off centre. Laying both out here keeps the pair centred as a unit.
        """
        body_top = LABEL_RESERVE
        body_h = self.height() - body_top
        if body_h <= 0:
            return

        painter.setFont(_body_font())
        text_h = painter.fontMetrics().height()

        block_h = 2 * _SPINNER_RADIUS + _SPINNER_CAPTION_GAP + text_h
        top = body_top + max(0, (body_h - block_h) // 2)

        if not self._failed:
            self._paint_spinner(painter, self.width() // 2, top + _SPINNER_RADIUS)

        painter.setPen(THEME.qcolor("text_muted"))
        self._painted_text = self._body_text()
        painter.drawText(
            QRect(0, top + 2 * _SPINNER_RADIUS + _SPINNER_CAPTION_GAP, self.width(), text_h),
            Qt.AlignmentFlag.AlignCenter,
            self._painted_text,
        )
        if self._failed:
            # The caption alone says what happened; this says what to do about it, and is
            # the reason a failed tile is not simply removed on the user's behalf.
            hint_font = THEME.ui_font(THEME.font_small_px)
            painter.setFont(hint_font)
            painter.drawText(
                QRect(0, top + 2 * _SPINNER_RADIUS + _SPINNER_CAPTION_GAP + text_h + 4, self.width(), text_h),
                Qt.AlignmentFlag.AlignCenter,
                "Dismiss to free the slot, then try again",
            )

    def _paint_spinner(self, painter: QPainter, cx: int, cy: int) -> None:
        """Draw the busy ring centred on (cx, cy): a faint full circle under a bright arc."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        box = QRect(cx - _SPINNER_RADIUS, cy - _SPINNER_RADIUS, 2 * _SPINNER_RADIUS, 2 * _SPINNER_RADIUS)

        track = QColor(THEME.qcolor("text_muted"))
        track.setAlpha(60)
        pen = painter.pen()
        pen.setWidth(_SPINNER_THICKNESS)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setColor(track)
        painter.setPen(pen)
        painter.drawEllipse(box)

        pen.setColor(THEME.qcolor("accent"))
        painter.setPen(pen)
        # Qt angles are in 1/16 degree units and run anticlockwise, so the negated angle is
        # what makes the arc sweep the way every other spinner on the platform does.
        painter.drawArc(box, -round(self._angle * 16), -_SPINNER_ARC_DEGREES * 16)
        painter.restore()
