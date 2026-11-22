"""The pills that are about a bot or a keep list, beside core's.

The two shapes a tag takes, ``TagPill`` and ``TagChip``, are core's
(``bartleby/qt/widgets/pills/tag_pill.py``, BartleBuddy #31, Stage 6), and so are the pills
that claim no tag color, or another palette's (``NeutralPill``, ``ActionPill``,
``NeutralChip``, ``AccentPill``, ``AccountPill``, ``WarningPill``), and the metrics every
pill shares (``pill.py``, Stage 6 of #167). :class:`SuggestedTagPill`, :class:`KeywordChip`
and :class:`ThisBotPill` stay here: a tag a bot could have, a drop keep list's keyword, and
a strategy that belongs to one Bot Combat Strategy.
"""

from __future__ import annotations

from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import set_style_sheet
from bartleby.qt.widgets.pills.pill import NeutralChip, NeutralPill, paint_pill_outline, pill_style
from PyQt6.QtCore import QEvent, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QLabel, QPushButton, QWidget

from bartlebot.combat.bot_strategy_owner import THIS_BOT_SOURCE
from bartlebot.gui.widgets.pickers.tag_vocabularies import BOT_VOCABULARY


class SuggestedTagPill(QPushButton):
    """A tag the bot does not carry yet, outlined in its group's color: ``+ Reagents``.

    :class:`TagChip`'s metrics, one pixel of the padding given to the border so the two
    sit level in a row. Outlined rather than filled because it is not a tag yet — a
    filled one would read as already on the bot — and with no medallion for the same
    reason, which also keeps it one shape on every install.

    Checkable, for the one surface that picks several at once: checked, it fills like
    the tag it would become and drops the ``+``. The editor header leaves it unchecked
    and treats a click as accepting the suggestion.

    The outline is painted by :func:`paint_pill_outline`, not by the stylesheet, whose
    border is a transparent placeholder keeping the metrics. A stylesheet border is one
    *logical* pixel, which at 125% display scaling is 1.25 device pixels, and each edge
    rasterises to one pixel or two depending on where it falls: the bottom edge came out
    visibly thinner than the rest.
    """

    def __init__(self, tag: str, *, tooltip: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tag = tag
        self._applying_style = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip or f"Add the {tag} tag")
        self.toggled.connect(self._on_toggled)
        self._on_toggled(False)

    @property
    def tag(self) -> str:
        return self._tag

    def _on_toggled(self, checked: bool) -> None:
        self.setText(self._tag if checked else f"+ {self._tag}")
        self._restyle()

    def _restyle(self) -> None:
        if self._applying_style:  # see TagPill._restyle
            return
        self._applying_style = True
        try:
            accent = BOT_VOCABULARY.accent(self._tag)
            outline = "border: 1px solid transparent; border-radius: 9px; padding: 1px 8px;"
            filled = pill_style(accent, THEME.pill_text, font_size=THEME.font_subtext_px, radius=9, pad="1px 8px")
            set_style_sheet(
                self,
                f"QPushButton {{ background: transparent; color: {THEME.text}; font-size: {THEME.font_subtext_px}px;"
                f" font-weight: bold; {outline} }}"
                f"QPushButton:hover, QPushButton:checked {{ {filled} border: 1px solid transparent; }}",
            )
        finally:
            self._applying_style = False

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        paint_pill_outline(painter, QRectF(self.rect()), BOT_VOCABULARY.accent(self._tag))
        painter.end()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._restyle()


class KeywordChip(NeutralChip):
    """A keep-list keyword: :class:`NeutralChip` with how many drops it catches.

    A keyword keeps every drop whose name contains it, a ticked row keeps only itself,
    and the count is what says which of the two a chip is: "giver · 7" is plainly a
    family. Only keywords are chips; a ticked item is shown by its row.

    Unlike :class:`NeutralChip`, only the ✕ removes. The rest of the chip *shows* what
    the keyword keeps: hovering emits :attr:`hovered` so the picker can light those
    rows, and a click emits :attr:`activated` so it can filter to them. From the
    keyboard, Space or Enter activates and Delete or Backspace removes.
    """

    activated = pyqtSignal(str)
    #: ``(keyword, True)`` as the pointer enters, ``(keyword, False)`` as it leaves.
    hovered = pyqtSignal(str, bool)

    #: Slack either side of the ✕ glyph that still counts as pressing it.
    _CROSS_SLACK = 6

    def __init__(self, keyword: str, count: int = 0, *, parent: QWidget | None = None) -> None:
        super().__init__(keyword, parent=parent)
        self.clicked.disconnect()
        self.clicked.connect(self._on_clicked)
        self._press_x: int | None = None
        self.set_count(count)

    def set_count(self, count: int) -> None:
        """Show *count* as the drops in this bot's table that the keyword keeps."""
        self.setText(f"{self.value} · {count}  ✕")
        drops = "drop" if count == 1 else "drops"
        self.setToolTip(
            f"Keeps every drop whose name contains “{self.value}”: {count} {drops} in this bot's table.\n"
            "Click to show them; click ✕ to stop keeping them."
        )

    def on_cross(self, x: int) -> bool:
        """Whether a click at *x* lands on the ✕ at the chip's right end."""
        cross = self.fontMetrics().horizontalAdvance("✕")
        # pad="2px 9px" in NeutralChip's style: the glyph ends 9px from the right edge.
        return x >= self.width() - 9 - cross - self._CROSS_SLACK

    def mousePressEvent(self, event) -> None:
        self._press_x = int(event.position().x())
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.removed.emit(self.value)
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.activated.emit(self.value)
            return
        self._press_x = None  # Space: a click with no position, so it activates
        super().keyPressEvent(event)

    def _on_clicked(self) -> None:
        x, self._press_x = self._press_x, None
        if x is not None and self.on_cross(x):
            self.removed.emit(self.value)
        else:
            self.activated.emit(self.value)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.hovered.emit(self.value, True)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.hovered.emit(self.value, False)


class ThisBotPill(NeutralPill):
    """:class:`NeutralPill` in ``pill_this_bot_bg``: this strategy belongs to one Bot Combat Strategy.

    Says "This bot" -- the section heading above the card names which one (#118). Its own
    hue, not the error red — see ``pill_this_bot_bg``.
    """

    def _restyle(self) -> None:
        if self._applying_style:  # see TagPill._restyle
            return
        self._applying_style = True
        try:
            set_style_sheet(self, pill_style(THEME.pill_this_bot_bg, THEME.pill_text))
        finally:
            self._applying_style = False


def this_bot_pill(bot_strategy_name: str) -> QLabel:
    """The pill on an entry that belongs to one Bot Combat Strategy: "This bot" (#118).

    Separate from core's ``source_pill``, whose ``Origin`` describes the two storage roots; an
    entry of the bot's own lives in neither, but inside its Bot Combat Strategy's file.

    The word, not the Bot Combat Strategy's name. The name is already the heading of the
    section the card sits in, and a pill carrying it both repeated that and was the one
    pill whose width came from user text -- a long name squeezed the card's own name to a
    word a line and widened the whole list past its column. "This bot" is also what the
    Source filter calls these entries, from the same constant, so the pill reads as the
    chip it matches. The name is still in the tooltip.
    """
    pill = ThisBotPill(THIS_BOT_SOURCE)
    pill.setToolTip(f"Belongs to the Bot Combat Strategy '{bot_strategy_name}' only")
    return pill
