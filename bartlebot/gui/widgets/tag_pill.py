"""The two shapes a bot tag takes on screen, and the one palette behind both.

A tag is coloured by its group everywhere it appears — the dot in the picker, the
pill on a library card, the chip in a filter bar, the chip in Bot Info. Someone who
filters by Rewards and scans the results should be matching a colour rather than
re-reading every pill, and that only holds if one module owns the mapping.

Two shapes, because they do different jobs:

* :func:`tag_pill` — descriptive. Small, inert, several per row. It says what a bot
  is; there is nothing to click.
* :class:`TagChip` — interactive. Larger, with a ✕ and a real hit target, because
  removing the filter or the tag it stands for is the whole point of it being there.

Both take their colour from the same call, so the sizes differ and the hues never do.

Both are also led by the tag's game medallion where the user has extracted one (#29),
at ``TAG_ICON_PX``: the pill is exactly as tall as the medallion, so the ring forms its
rounded left end and the coloured body runs on from it, name included. Without extracted
art, and always for the Type tags, which have none, both are exactly the text forms above.

:class:`NeutralPill` is the odd one out and lives here for the shape rather than the
palette: a caption that wants to look like a pill without claiming a tag colour.
:class:`ActionPill` is that one with a click behind it -- same metrics, so a pill that
leads somewhere and a pill that only names something are not two different shapes.

:class:`AccentPill` is a third: the same metrics again, outlined in the accent, for a
badge that has to be told apart from the pills it shares a row with.

:class:`AccountPill` is the other odd one: it claims a colour, but from the account
palette rather than the tag one, and it wears it as an outline instead of a fill.
See its docstring for why the two colour systems can share a screen without reading
as one.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath
from PyQt6.QtWidgets import QLabel, QPushButton, QSizePolicy, QWidget

from bartlebot.bot_tags import tag_group
from bartlebot.gui.account_accent import account_accent, account_tint
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.tag_picker import TAG_ICON_PX, group_accent, tag_icon

#: The inert pill's own metrics, shared with :class:`ActionPill` so a clickable pill and
#: a descriptive one are the same shape -- which is the whole reason the clickable one
#: exists rather than a chip.
_PILL_HEIGHT = 14
_PILL_PAD_X = 6


def pill_style(bg: str, fg: str, *, font_size: int | None = None, radius: int = 7, pad: str = "1px 6px") -> str:
    """The shared pill look, so a caller varies only what it means to vary."""
    font_size = THEME.font_micro_px if font_size is None else font_size
    return (
        f"background: {bg}; color: {fg}; font-size: {font_size}px;"
        f" font-weight: bold; border-radius: {radius}px; padding: {pad};"
    )


#: The space either side of the medallion pill's text. The pill is as tall as the
#: medallion, so its text is a size up from the plain pill's -- see ``medallion_font``.
_MEDALLION_GAP = 4  # medallion to text
_MEDALLION_PAD_R = 8  # text to the round right end


def medallion_font(base: QFont) -> QFont:
    """*base* at the medallion pill's size, bold."""
    font = QFont(base)
    font.setPixelSize(THEME.font_subtext_px)
    font.setBold(True)
    return font


def medallion_pill_width(font: QFont, text: str) -> int:
    """How wide a medallion pill carrying *text* in *font* is."""
    return TAG_ICON_PX + _MEDALLION_GAP + QFontMetrics(font).horizontalAdvance(text) + _MEDALLION_PAD_R


def paint_medallion_pill(painter: QPainter, width: int, icon: QIcon, text: str, fill: str, fg: str) -> None:
    """A pill whose left end *is* the medallion.

    The pill is exactly as tall as the medallion and every medallion fills its square, so
    the ring and the pill's round end are the same circle. The fill starts at the
    medallion's centre rather than at its edge: a rounded rectangle starting at the edge
    would curve away under the ring and leave a notch where the top and bottom edges meet
    it. The right end is a half circle of the same radius.
    """
    h = TAG_ICON_PX
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    body = QPainterPath()
    body.setFillRule(Qt.FillRule.WindingFill)
    body.addRect(QRectF(h / 2, 0, max(width - h, 0), h))
    body.addEllipse(QRectF(width - h, 0, h, h))
    painter.fillPath(body, QColor(fill))
    icon.paint(painter, QRect(0, 0, h, h))
    painter.setPen(QColor(fg))
    text_x = h + _MEDALLION_GAP
    painter.drawText(
        QRect(text_x, 0, max(width - text_x, 0), h),
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        text,
    )


class TagPill(QLabel):
    """A small, inert pill naming one tag, led by its medallion where one was extracted.

    The medallion form is drawn rather than styled (see :func:`paint_medallion_pill`): a
    stylesheet cannot put an icon flush with a rounded end. The text form, group-coloured,
    is every other case: an install with no extracted art, and the Type tags, which have
    none.

    The name stays beside the medallion. The art alone was tried and was not enough to
    read a card by.
    """

    def __init__(self, tag: str, parent: QWidget | None = None) -> None:
        super().__init__(tag, parent)
        self._tag = tag
        self._applying_style = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._icon = tag_icon(tag)
        if self._icon is None:
            self.setFixedHeight(_PILL_HEIGHT)
        else:
            self.setFont(medallion_font(self.font()))
            self.setFixedSize(medallion_pill_width(self.font(), tag), TAG_ICON_PX)
        self._restyle()

    @property
    def tag(self) -> str:
        return self._tag

    @property
    def has_icon(self) -> bool:
        """Whether this pill is led by the tag's medallion."""
        return self._icon is not None

    def paintEvent(self, event) -> None:
        if self._icon is None:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setFont(self.font())
        paint_medallion_pill(
            painter, self.width(), self._icon, self._tag, group_accent(tag_group(self._tag)), THEME.pill_text
        )
        painter.end()

    def _restyle(self) -> None:
        # Guard is load-bearing: setStyleSheet delivers StyleChange straight back to
        # changeEvent, and the app dies in window construction with no traceback.
        # Same shape as PickerButton._apply_style.
        if self._applying_style:
            return
        self._applying_style = True
        try:
            if self._icon is not None:
                # paintEvent draws it all, reading THEME at paint time; a stylesheet font
                # would override the one the width was measured with.
                self.setStyleSheet("background: transparent;")
            else:
                self.setStyleSheet(pill_style(group_accent(tag_group(self._tag)), THEME.pill_text))
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._restyle()


class TagChip(QPushButton):
    """A group-coloured chip that removes its tag when clicked.

    Led by the tag's medallion where one was extracted, in the same shape as
    :class:`TagPill`'s, so a tag reads the same in the filter bar as on the cards below it.
    """

    removed = pyqtSignal(str)

    def __init__(self, tag: str, *, tooltip: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(f"{tag}  ✕", parent)
        self._tag = tag
        self._applying_style = False
        self._icon = tag_icon(tag)
        if self._icon is not None:
            # Hover repaints need WA_Hover once the stylesheet no longer draws the chip.
            self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
            self.setFont(medallion_font(self.font()))
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self.setFixedSize(medallion_pill_width(self.font(), self.text()), TAG_ICON_PX)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip or f"Remove the {tag} tag")
        self._restyle()
        self.clicked.connect(lambda: self.removed.emit(self._tag))

    @property
    def tag(self) -> str:
        return self._tag

    @property
    def has_icon(self) -> bool:
        """Whether this chip is led by the tag's medallion."""
        return self._icon is not None

    def paintEvent(self, event) -> None:
        if self._icon is None:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setFont(self.font())
        fill = THEME.edge_selected if self.underMouse() else group_accent(tag_group(self._tag))
        paint_medallion_pill(painter, self.width(), self._icon, self.text(), fill, THEME.pill_text)
        painter.end()

    def _restyle(self) -> None:
        if self._applying_style:  # see TagPill._restyle
            return
        self._applying_style = True
        try:
            if self._icon is not None:
                self.setStyleSheet("QPushButton { border: none; background: transparent; }")
                return
            base = pill_style(
                group_accent(tag_group(self._tag)),
                THEME.pill_text,
                font_size=THEME.font_subtext_px,
                radius=9,
                pad="2px 9px",
            )
            self.setStyleSheet(
                f"QPushButton {{ border: none; {base} }}QPushButton:hover {{ background: {THEME.edge_selected}; }}"
            )
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._restyle()


class NeutralChip(QPushButton):
    """A grey chip that removes the word it names when clicked.

    :class:`TagChip`'s shape without a tag's colour, for a list of plain strings the
    user assembled: the quicksell keep list's keywords are the case. Colour on a chip
    means "tag group" everywhere else in the app, so a keyword that borrowed one
    would read as a tag.

    The keep list is why this is a chip at all rather than the comma-separated line
    edit it replaced. At a 400px sidebar that field showed the *middle* of a scrolled
    string -- "electable Hooves, Skyscream, Frostbit, ..." -- which is neither
    readable nor safely editable, and removing one keyword from it meant selecting
    text between two commas.
    """

    removed = pyqtSignal(str)

    def __init__(self, text: str, *, tooltip: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(f"{text}  ✕", parent)
        self._text = text
        self._applying_style = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip or f"Remove {text}")
        self._restyle()
        self.clicked.connect(lambda: self.removed.emit(self._text))

    @property
    def value(self) -> str:
        return self._text

    def _restyle(self) -> None:
        if self._applying_style:  # see TagPill._restyle
            return
        self._applying_style = True
        try:
            base = pill_style(
                THEME.pill_neutral_bg, THEME.pill_text, font_size=THEME.font_subtext_px, radius=9, pad="2px 9px"
            )
            self.setStyleSheet(
                f"QPushButton {{ border: none; {base} }}QPushButton:hover {{ background: {THEME.edge_selected}; }}"
            )
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._restyle()


class ActionPill(QPushButton):
    """A grey pill that runs a verb when clicked, and wears no ✕.

    :class:`NeutralPill`'s exact shape and colour with a hit target behind it, so a row
    of these and a row of inert pills read as the same kind of thing. The Combat tab's
    Bot Default Strategy card is the case: it condenses every fight that has no override
    of its own into one card, one pill each, and a click on a pill opens the strategy
    picker for that fight — the click that used to land on the fight's own condensed row.

    No ✕, unlike :class:`NeutralChip`. A cross means "remove the thing this names", which
    for a fight would be an offer to delete a node out of the bot; the pill *leads* to
    the fight instead of destroying it.

    Carries a *key* beside its text — a state id, where the label is the fight's display
    name — because a card reuses its pills rather than rebuilding them, and an index into
    a list of labels is not a stable way to say which one was clicked.
    """

    activated = pyqtSignal(str)  # this pill's key

    def __init__(self, text: str = "", key: str = "", *, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._key = key
        self._applying_style = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(_PILL_HEIGHT)
        # Sized from the text rather than from the style. A ``QPushButton``'s own size
        # hint carries the platform's minimum button width -- 80px on Windows -- which
        # would make four pills wider than the sidebar they wrap inside.
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        # On the widget as well as in the stylesheet: a stylesheet ``font-size`` never
        # reaches ``QWidget.font()``, so :meth:`sizeHint` would measure at the base size
        # and leave every pill several pixels too wide.
        font = self.font()
        font.setPixelSize(THEME.font_micro_px)
        font.setBold(True)
        self.setFont(font)
        self._restyle()
        self.clicked.connect(lambda: self.activated.emit(self._key))

    @property
    def key(self) -> str:
        return self._key

    def set_pill(self, text: str, key: str) -> None:
        """Re-point this pill at another thing, keeping the widget."""
        self._key = key
        self.setText(text)
        self.updateGeometry()

    def sizeHint(self) -> QSize:
        return QSize(QFontMetrics(self.font()).horizontalAdvance(self.text()) + 2 * _PILL_PAD_X, _PILL_HEIGHT)

    def _restyle(self) -> None:
        if self._applying_style:  # see TagPill._restyle
            return
        self._applying_style = True
        try:
            base = pill_style(
                THEME.pill_neutral_bg, THEME.pill_text, font_size=THEME.font_micro_px, pad=f"0px {_PILL_PAD_X}px"
            )
            self.setStyleSheet(
                f"QPushButton {{ border: none; {base} }}"
                f"QPushButton:hover:enabled {{ background: {THEME.edge_selected}; }}"
                f"QPushButton:disabled {{ color: {THEME.text_subtle}; }}"
            )
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._restyle()


class NeutralPill(QLabel):
    """A small, inert, grey pill for a caption that is not a tag.

    A bot card's version, a launch profile's resolution — descriptive text that wants
    the pill *shape* without claiming a tag colour. Colour on these cards means "tag
    group", and spending an accent on a version number made the loudest pill the one
    carrying the least information.

    ``pill_neutral_bg`` is a true grey on purpose: the blue-grey ``chip_neutral_bg``
    read as a third tag colour beside the blue Rewards pill.

    Owns its own colour, like :class:`TagPill`. The predecessor was a factory returning
    a bare ``QLabel`` for the caller to restyle from ``apply_theme_styles``, which put
    the staleness on every caller — see the themed-helper note in CLAUDE.md.
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._applying_style = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(_PILL_HEIGHT)
        self._restyle()

    def _restyle(self) -> None:
        if self._applying_style:  # see TagPill._restyle
            return
        self._applying_style = True
        try:
            self.setStyleSheet(pill_style(THEME.pill_neutral_bg, THEME.pill_text))
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._restyle()


class AccentPill(QLabel):
    """An inert, accent-outlined pill marking what the row it leads is.

    :class:`NeutralPill`'s metrics with the accent's outline instead of a grey fill, for
    a badge that shares a row with pills naming *things*. The Combat tab's default
    strategy card is the case: its pill row is ``Default`` followed by a pill per fight,
    and the two have to be told apart at a glance because they are different kinds of
    word -- one qualifies the card, the rest are its contents.

    Outlined rather than filled, for :class:`AccountPill`'s reason turned around. There
    the outline keeps four saturated fills off one row; here it is that a *filled* accent
    pill beside grey ones reads as the loudest item in the list rather than as a label on
    it. It is also the affordance boundary on that card, where the fight pills are
    :class:`ActionPill`\\ s: no fill, no hover, no pointing hand, nothing to click.
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._applying_style = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(_PILL_HEIGHT)
        self._restyle()

    def _restyle(self) -> None:
        if self._applying_style:  # see TagPill._restyle
            return
        self._applying_style = True
        try:
            # One pixel of the padding goes to the border, so the text sits at the same
            # x inside this pill as inside the filled ones beside it.
            self.setStyleSheet(
                f"background: transparent; color: {THEME.accent};"
                f" font-size: {THEME.font_micro_px}px; font-weight: bold; border-radius: 7px;"
                f" border: 1px solid {THEME.accent}; padding: 0px {_PILL_PAD_X - 1}px;"
            )
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._restyle()


class AccountPill(QLabel):
    """A pill naming one account, outlined in that account's colour.

    Outlined rather than filled, and with ordinary text rather than coloured text.
    Both follow from where it is used: a launch profile shows one of these per
    account, so four of them sit in a row, and four saturated fills make the row
    the loudest thing on a screen whose point is the profile's *name*. The colour
    is a match target — you find the account card carrying the same hue — and an
    outline plus a low-alpha tint is enough to match on while leaving the name at
    full contrast, which coloured text at 9px is not.

    That the shape differs from :class:`TagPill` is the other half of it: tags mean
    something different by colour, and the two palettes never have to be told apart
    because a filled pill and an outlined one are not the same object.

    Holds the palette *slot*, not a colour — like :class:`TagPill` holding its tag —
    so a theme switch re-resolves rather than repainting a stale hex.
    """

    def __init__(self, text: str, color_key: int, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._color_key = color_key
        self._applying_style = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(16)
        self._restyle()

    @property
    def color_key(self) -> int:
        return self._color_key

    def _restyle(self) -> None:
        if self._applying_style:  # see TagPill._restyle
            return
        self._applying_style = True
        try:
            self.setStyleSheet(
                f"background: {account_tint(self._color_key)}; color: {THEME.text};"
                f" font-size: {THEME.font_micro_px}px; font-weight: bold; border-radius: 8px;"
                f" border: 1px solid {account_accent(self._color_key)}; padding: 0px 6px;"
            )
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._restyle()
