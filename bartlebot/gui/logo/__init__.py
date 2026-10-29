"""The application logo: one SVG, and the raster sizes generated from it (#26).

``bartlebot.svg`` beside this file is the source of truth. It is pixel art, a tree
drawn on a 12-cell grid over a solid background, and pixel art only stays sharp at
a size where every cell is a whole number of pixels. Plain SVG scaling does not
give you that: 12 cells into 16 px is 1.33 px per cell, and the result is smeared.

So a raster is never the SVG scaled down. The art's cells are read from the SVG and
redrawn at a whole number of pixels per cell, in one of three framings:

* :func:`logo_image`, the square with its background, for avatars (GitHub, Discord).
  The background grows or shrinks around the tree, keeping at least the SVG's
  one-cell margin.
* :func:`tree_image`, the tree alone on transparency, for the window icon. A title
  bar icon is 16 to 24 px, where a margin of background leaves a tree too small to
  make out, so here the tree fills the square instead.
* :func:`logo_disc_image`, the tree on a disc of the background color, for the
  launch screen.

Editing the logo means editing the SVG. Anything that stays on the grid, meaning
whole-cell rects over a one-color background, comes through unchanged.
"""

from __future__ import annotations

import ctypes
import logging
import math
import sys
from functools import cache
from pathlib import Path

from bartleby.shared.vendor import PRODUCT, VENDOR
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication, QWidget

logger = logging.getLogger(__name__)

#: The source of truth for every raster of the logo.
LOGO_SVG = Path(__file__).parent / "bartlebot.svg"

#: The grid the SVG is drawn on, in cells along each side.
GRID = 12

#: The sizes Windows asks a window icon for: title bar, taskbar, Alt+Tab and the
#: large icon views, at 100% to 200% scaling.
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)

# Rendered at two pixels per cell, so that art offset by half a cell, as the SVG's
# vertically centered tree is, still lands on whole pixels.
_SAMPLE_SCALE = 2

_Cells = tuple[tuple[QColor | None, ...], ...]


@cache
def _art() -> tuple[QColor, _Cells]:
    """The background color and the art's cells, cropped to the art, None where empty."""
    side = GRID * _SAMPLE_SCALE
    image = QImage(side, side, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    QSvgRenderer(str(LOGO_SVG)).render(painter, QRectF(0, 0, side, side))
    painter.end()

    background = image.pixel(0, 0)
    drawn = [(x, y) for y in range(side) for x in range(side) if image.pixel(x, y) != background]
    left = min(x for x, _ in drawn)
    top = min(y for _, y in drawn)
    width = (max(x for x, _ in drawn) - left + 1) // _SAMPLE_SCALE
    height = (max(y for _, y in drawn) - top + 1) // _SAMPLE_SCALE

    def cell(column: int, row: int) -> QColor | None:
        rgb = image.pixel(left + column * _SAMPLE_SCALE, top + row * _SAMPLE_SCALE)
        return None if rgb == background else QColor.fromRgba(rgb)

    rows = tuple(tuple(cell(c, r) for c in range(width)) for r in range(height))
    return QColor.fromRgba(background), rows


def art_size() -> tuple[int, int]:
    """The tree's width and height, in cells."""
    _, rows = _art()
    return len(rows[0]), len(rows)


def _paint_art(painter: QPainter, left: int, top: int, scale: float) -> None:
    """Paint the tree with its top-left corner at (*left*, *top*), *scale* pixels per cell.

    A cell's edges are its grid lines times *scale*, rounded down, so a fractional
    scale gives cells of unequal but whole widths rather than blended edges.
    """
    _, rows = _art()
    for r, row in enumerate(rows):
        y0, y1 = top + math.floor(r * scale), top + math.floor((r + 1) * scale)
        for c, color in enumerate(row):
            if color is not None:
                x0, x1 = left + math.floor(c * scale), left + math.floor((c + 1) * scale)
                painter.fillRect(x0, y0, x1 - x0, y1 - y0, color)


def _framed(size: int, scale: float, fill: QColor | None) -> QImage:
    """A *size* square, filled or transparent, with the tree centered at *scale*."""
    width, height = art_size()
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(fill if fill is not None else Qt.GlobalColor.transparent)
    painter = QPainter(image)
    _paint_art(
        painter,
        (size - math.floor(width * scale)) // 2,
        (size - math.floor(height * scale)) // 2,
        scale,
    )
    painter.end()
    return image


def cell_size(size: int) -> int:
    """Pixels per cell on the square: whole, and never less margin than the SVG's one cell."""
    return max(1, size // GRID)


def logo_image(size: int) -> QImage:
    """The logo on its background, *size* square, every cell a whole number of pixels."""
    background, _ = _art()
    return _framed(size, cell_size(size), background)


def tree_scale(size: int) -> float:
    """Pixels per cell for the tree alone: as large as fits in *size*.

    Whole wherever that is two or more. Below that, at 16 px, one pixel per cell
    leaves a 10 px tree in a 16 px square, so it goes to one and a half instead: the
    cells alternate one and two pixels wide, which stays sharp where a true 1.5
    would blur.
    """
    whole = size // max(art_size())
    return whole if whole >= 2 else max(1.0, math.floor(size * 2 / max(art_size())) / 2)


def tree_image(size: int) -> QImage:
    """The tree alone on transparency, filling a *size* square."""
    return _framed(size, tree_scale(size), None)


def disc_cell_size(diameter: int) -> int:
    """The largest whole cell that keeps a cell of disc around every corner of the tree."""
    width, height = art_size()
    _, rows = _art()
    radius = diameter / 2
    for scale in range(max(1, diameter // max(width, height)), 0, -1):
        left = (diameter - width * scale) // 2
        top = (diameter - height * scale) // 2
        farthest = max(
            math.hypot(left + (c + dx) * scale - radius, top + (r + dy) * scale - radius)
            for r, row in enumerate(rows)
            for c, color in enumerate(row)
            if color is not None
            for dx in (0, 1)
            for dy in (0, 1)
        )
        if farthest + scale <= radius:
            return scale
    return 1


def logo_disc_image(diameter: int) -> QImage:
    """The tree on a disc of the background color, *diameter* square, transparent outside."""
    background, _ = _art()
    image = _framed(diameter, disc_cell_size(diameter), None)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOver)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(background)
    painter.drawEllipse(QRectF(0, 0, diameter, diameter))
    painter.end()
    return image


def logo_icon() -> QIcon:
    """The window icon: the tree alone, snapped by hand at each size Windows asks for."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QPixmap.fromImage(tree_image(size)))
    return icon


def app_user_model_id() -> str:
    """The id Windows groups our windows under on the taskbar."""
    return f"{VENDOR}.{PRODUCT}"


def install_app_icon(app: QApplication) -> None:
    """Make the logo every window's icon, and give the app a taskbar button of its own.

    Run from source, the process is ``python.exe``, and Windows groups its windows
    under Python's own id and shows Python's icon. An explicit AppUserModelID gives
    us a button of our own. It has to be set before the first window is shown, and
    it is not enough alone: see :func:`refresh_taskbar_icon`.
    """
    app.setWindowIcon(logo_icon())
    if sys.platform != "win32":
        return
    try:
        shell32 = ctypes.WinDLL("shell32")
        shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_user_model_id()))
    except OSError:
        logger.warning("Could not set the taskbar id; the taskbar may show Python's icon", exc_info=True)


def refresh_taskbar_icon(window: QWidget) -> None:
    """Hand *window* its icon again, so the taskbar picks it up. Call once the event loop runs.

    The main window is shown partway through start-up, and the GUI thread then stays
    busy for seconds. The taskbar asks the new window for its icon in that time, gets
    no answer, falls back to the window class's icon (Windows' generic application
    icon), and does not ask again until the window's icon changes. Setting it on
    the class does not help, because the taskbar is not told. A fresh ``QIcon`` makes
    Qt send the window a new icon handle, and the taskbar updates to it.
    """
    window.setWindowIcon(logo_icon())
