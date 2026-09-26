"""The suggested-tag pill's outline, which has to be one weight all the way round.

A stylesheet border is one logical pixel, and at 125% display scaling that is 1.25 device
pixels: each edge rasterised to one pixel or two depending on where it fell, and the
bottom of the ``+ Tag`` pill came out visibly thinner than the top.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QPainter

from bartlebot.gui.widgets.tag_pill import paint_pill_outline


def _edge_rows(scale: float) -> tuple[list[int], list[int]]:
    """Paint a pill from an off-grid origin at *scale*, and read one column down its middle.

    Returns the alpha of each covered pixel in the top edge and in the bottom edge.
    """
    image = QImage(300, 100, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    image.setDevicePixelRatio(scale)
    painter = QPainter(image)
    # Where a widget's origin sits between device pixels: 109 * 1.25 is 136.25.
    painter.translate(3, 13)
    paint_pill_outline(painter, QRectF(0, 0, 90, 21), "#3a7bd5")
    painter.end()

    column = int((3 + 45) * scale)
    alphas = [image.pixelColor(column, y).alpha() for y in range(image.height())]
    covered = [y for y, a in enumerate(alphas) if a]
    split = next(i for i in range(1, len(covered)) if covered[i] != covered[i - 1] + 1)
    return [alphas[y] for y in covered[:split]], [alphas[y] for y in covered[split:]]


@pytest.mark.parametrize(("scale", "width"), [(1.0, 1), (1.25, 1), (1.5, 2), (2.0, 2)])
def test_top_and_bottom_edges_are_the_same_whole_pixels(qapp, scale, width):
    top, bottom = _edge_rows(scale)

    assert top == bottom == [255] * width
