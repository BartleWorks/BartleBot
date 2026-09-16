"""Card art tiles, and the wrapping strip of them a deck card shows.

The art itself is extracted by the user (``tools/extract_card_art.py``) and found by
:mod:`bartlebot.card_art`; this is only the drawing. Three pieces:

* :class:`CardArtTile` -- one card's art at a fixed size, with a count badge when the
  deck holds more than one copy.
* :class:`GroupDivider` -- the rotated label that separates Normal from Item cards in
  the strip, where a text heading would cost a whole row.
* :class:`CardArtStrip` -- the two of them in a :class:`FlowLayout`.

THE ART IS NOT CROPPED
    Each texture is a circular portrait inside its own ring, on transparent corners.
    Cropping to the inscribed square (70.7% of the width) would remove the ring
    entirely and match how the game's own deck window draws these -- but it cuts the
    ring off partway at any larger fraction, which reads as damage rather than as a
    crop. So the tile draws the whole image, and the size is what makes it legible:
    ``COLLAPSED_TILE`` is 40px because the ring and its margin take roughly 40% of the
    diameter, leaving a ~24px subject, and below that cards start telling apart only
    by colour.

THE BADGE SITS ON THE RING
    Top right, centred on the ring's 45-degree point, so it overlaps the frame rather
    than the portrait inside it. It is drawn in ``THEME.text`` with ``THEME.bg`` text
    -- inverted against the theme rather than coloured -- because it lands on artwork
    of every hue and an accent-coloured badge is legible against some of it.

Nothing here caches a ``THEME`` value: both classes read the theme inside
``paintEvent``, which is the one case ``gui/CLAUDE.md`` exempts from ``ThemedMixin``,
since a repaint is what a theme switch causes anyway.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QSizePolicy, QWidget

from bartlebot import card_art
from bartlebot.gui.deck_content import DeckCardEntry, DeckGroup
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.flow_layout import FlowLayout

#: Tile size in the collapsed strip. See "the art is not cropped" above.
COLLAPSED_TILE = 40

#: Tile size beside a row of text in the expanded list, where the text carries the
#: identity and the art only has to be recognizable.
ROW_TILE = 32

#: The rotated group label's column width, and the gap that follows a group.
DIVIDER_WIDTH = 12
GROUP_GAP = 8


@lru_cache(maxsize=256)
def _art_icon(path: str, stamp: float) -> QIcon:
    """The art at *path* as an icon holding the full-size image, cached.

    *stamp* is the file's mtime: it is not read, only keyed on, so that re-running the
    extractor while the app is open replaces what is cached instead of being ignored.
    """
    pixmap = QPixmap(path)
    return QIcon() if pixmap.isNull() else QIcon(pixmap)


def art_icon(path: Path) -> QIcon | None:
    """An extracted PNG as an icon, or None if it cannot be read.

    The icon holds the full-size image, never one scaled in advance, and every surface
    draws it through ``QIcon.paint`` (or hands it to a view that does). That asks for
    the image at the painted size *times the device pixel ratio of the screen being
    painted*, which Qt scales down from the original and caches per ratio. A pixmap
    pre-scaled to the logical size is stretched back up on any display scaled above
    100%, and drew soft with a jagged ring; one pre-scaled for a single ratio is wrong
    on every other, including the second monitor of the same machine.

    The images are the extractor's 128px textures (a few 200-256px), so holding the
    originals costs about 64KB each.
    """
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return None
    icon = _art_icon(str(path), stamp)
    return None if icon.isNull() else icon


def card_icon(template: str) -> QIcon | None:
    """The extracted art for *template*, or None if there is none.

    None is the ordinary answer on an install whose user has never run the extractor,
    and for the handful of cards whose texture the game's own files do not contain.
    """
    path = card_art.art_path(template)
    return None if path is None else art_icon(path)


class ArtIconView(QWidget):
    """One extracted icon, drawn at a fixed logical size for whichever screen it is on.

    Stands in for a ``QLabel`` with a pixmap, which would fix the image's resolution
    when it is set rather than when it is painted.
    """

    def __init__(self, size: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon: QIcon | None = None
        self.setFixedSize(size, size)

    def icon(self) -> QIcon | None:
        return self._icon

    def set_icon(self, icon: QIcon | None) -> None:
        self._icon = icon
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802  (Qt override)
        if self._icon is None:
            return
        painter = QPainter(self)
        self._icon.paint(painter, self.rect())
        painter.end()


def tooltip_for(entry: DeckCardEntry) -> str:
    """What a tile says on hover: the display name, the count, and the template.

    The template name is the line that disambiguates two cards drawn with the same
    art -- a tiered variant uses its base spell's texture, so "Storm Lord" and
    "Storm Lord - T05 - A" are pixel-identical. It is dropped when it would only
    repeat the display name.
    """
    head = f"{entry.display} × {entry.count}" if entry.count > 1 else entry.display
    return f"{head}\n{entry.template}" if entry.shows_template else head


class CardArtTile(QWidget):
    """One card's art, badged with its count when the deck holds more than one."""

    def __init__(self, entry: DeckCardEntry, icon: QIcon, size: int = COLLAPSED_TILE, parent=None) -> None:
        super().__init__(parent)
        self._entry = entry
        self._icon = icon
        self._size = size
        self.setFixedSize(size, size)
        self.setToolTip(tooltip_for(entry))
        # The tile sits inside a card whose body click unfolds the list; it is a
        # picture, not a button, so it declines the click and lets the card have it.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

    @property
    def entry(self) -> DeckCardEntry:
        return self._entry

    def paintEvent(self, event) -> None:  # noqa: N802  (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Centred, aspect kept: QIcon.paint does both, for a non-square source too.
        self._icon.paint(painter, QRect(0, 0, self._size, self._size))
        if self._entry.count > 1:
            self._paint_badge(painter)
        painter.end()

    def _paint_badge(self, painter: QPainter) -> None:
        rect = badge_rect(self._size, len(str(self._entry.count)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(THEME.text))
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)

        font = QFont(self.font())
        font.setBold(True)
        font.setPixelSize(max(9, int(rect.height() * 0.72)))
        painter.setFont(font)
        painter.setPen(QColor(THEME.bg))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(self._entry.count))


def badge_rect(size: int, digits: int = 1) -> QRectF:
    """Where the count badge goes on a *size* tile holding a *digits*-long number.

    Centred on the ring's top-right 45-degree point -- ``r/sqrt(2)`` along each axis
    from the middle -- then pulled back inside the tile's edges, so a two-digit badge
    grows leftwards instead of off the widget.
    """
    diameter = max(13.0, size * 0.42)
    width = diameter + (diameter * 0.45 * (digits - 1))
    radius = size / 2
    offset = radius * 0.7071
    center_x = radius + offset
    center_y = radius - offset
    left = min(max(center_x - width / 2, 0.0), size - width)
    top = min(max(center_y - diameter / 2, 0.0), size - diameter)
    return QRectF(left, top, width, diameter)


class GroupDivider(QWidget):
    """A group's name, rotated to read bottom-to-top, as a divider in the strip.

    A horizontal heading would take a row of its own in a strip whose whole point is
    to be two or three rows tall; rotated, the label is the gap between groups rather
    than something above them.
    """

    def __init__(self, text: str, height: int = COLLAPSED_TILE, parent=None) -> None:
        super().__init__(parent)
        self._text = text
        self.setFixedSize(DIVIDER_WIDTH, height)
        self.setToolTip(f"{text} cards")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802  (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPixelSize(9)
        font.setCapitalization(QFont.Capitalization.AllUppercase)
        painter.setFont(font)
        painter.setPen(QColor(THEME.text_muted))
        # Rotate about the bottom-left corner so the text runs up the widget.
        painter.translate(0, self.height())
        painter.rotate(-90)
        painter.drawText(QRectF(0, 0, self.height(), self.width()), Qt.AlignmentFlag.AlignCenter, self._text)
        painter.end()


class CardArtStrip(QWidget):
    """The wrapping strip of tiles a collapsed deck card shows.

    Built from :func:`~bartlebot.gui.deck_content.deck_groups`, and empty -- which is
    to say, never built at all -- when no art has been extracted. The caller checks
    :meth:`has_art` and falls back to its text form.
    """

    def __init__(self, size: int = COLLAPSED_TILE, parent=None) -> None:
        super().__init__(parent)
        self._size = size
        self._layout = FlowLayout(self, spacing=3)
        self._tiles: list[CardArtTile] = []
        # Without this the strip is one row tall forever: a ``QBoxLayout`` asks the
        # *size policy* whether an item's height depends on its width, and answers no
        # for a widget that has not said so -- however many rows the FlowLayout inside
        # it goes on to lay out. A 14-tile deck then drew seven tiles and clipped the
        # rest, in a card exactly as tall as a five-tile one.
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    @property
    def tiles(self) -> list[CardArtTile]:
        """The tiles currently shown, for the tests and for anything counting them."""
        return list(self._tiles)

    def has_art(self) -> bool:
        return bool(self._tiles)

    def set_groups(self, groups: list[DeckGroup]) -> None:
        """Rebuild the strip from *groups*, dropping cards that have no art.

        A card whose art is missing is simply not tiled: the expanded list still names
        every card, and a placeholder per missing card would make an install with no
        art at all into a strip of grey squares.
        """
        self._clear()
        for index, group in enumerate(groups):
            tiles = [
                CardArtTile(entry, icon, self._size, self)
                for entry in group.entries
                if (icon := card_icon(entry.template)) is not None
            ]
            if not tiles:
                continue
            if index:
                # Only between groups: a divider before the first reads as a label for
                # the whole strip rather than for the group after it.
                self._layout.addWidget(GroupDivider(group.label, self._size, self))
            for tile in tiles:
                self._layout.addWidget(tile)
                self._tiles.append(tile)
        for i in range(self._layout.count()):
            item = self._layout.itemAt(i)
            widget = item.widget() if item else None
            if widget is not None:
                widget.show()

    def hasHeightForWidth(self) -> bool:  # noqa: N802  (Qt override)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802  (Qt override)
        return self._layout.heightForWidth(width)

    def sizeHint(self) -> QSize:  # noqa: N802  (Qt override)
        """The wrapped height at the width the strip actually has.

        ``FlowLayout.sizeHint`` is one row of everything -- what it would *like* -- so
        asking the layout alone understates the height of every strip that wraps.
        Before the widget has a width there is nothing better to answer with.
        """
        width = self.width()
        if width <= 0:
            return self._layout.sizeHint()
        return QSize(width, self._layout.heightForWidth(width))

    def _clear(self) -> None:
        while (item := self._layout.takeAt(0)) is not None:
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._tiles.clear()
