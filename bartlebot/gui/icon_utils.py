"""Utility for loading SVG files from bartlebot/gui/icons/ into QIcon objects."""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

_ICONS_DIR = Path(__file__).parent / "icons"


def svg_icon(name: str, size: int = 20) -> QIcon:
    """Render bartlebot/gui/icons/<name>.svg at *size*×*size* and return a QIcon."""
    renderer = QSvgRenderer(str(_ICONS_DIR / f"{name}.svg"))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    renderer.render(p)
    p.end()
    return QIcon(pm)
