"""The logo on its disc, as the launch screen shows it (#26)."""

from __future__ import annotations

import math

from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QWidget

from bartlebot.gui.logo import logo_disc_image


class LogoDisc(QWidget):
    """The tree on a disc *diameter* logical pixels across.

    Drawn at the screen's physical pixel size rather than scaled from a logical
    one, because the cells only stay sharp when each is a whole number of the
    pixels actually on screen; at 125% a 160 px disc is 200 of them.
    """

    def __init__(self, diameter: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(diameter, diameter)
        self._image: QImage | None = None

    def paintEvent(self, event) -> None:
        dpr = self.devicePixelRatioF()
        physical = math.floor(self.width() * dpr)
        if self._image is None or self._image.width() != physical:
            self._image = logo_disc_image(physical)
            self._image.setDevicePixelRatio(dpr)
        painter = QPainter(self)
        painter.drawImage(0, 0, self._image)
        painter.end()
