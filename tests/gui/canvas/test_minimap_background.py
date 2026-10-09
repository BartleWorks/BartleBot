"""The minimap paints the scene's background but never the editor's grid.

The minimap shows the whole scene and repaints on every scroll step of the main view,
so drawing the grid there cost some 2,500 dots a frame -- two thirds of a pan.
"""

import pytest
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter

from bartlebot.gui.canvas.edit_scene import EditScene
from bartlebot.gui.canvas.graph_view import GraphView, _MinimapView

_SIZE = 20  # image px; the painter is centered on a grid dot at the scene origin


@pytest.fixture
def views(qapp):
    scene = EditScene()
    scene.set_snap_enabled(True)
    scene.set_grid_style("dots")
    main = GraphView(scene, interactive=True)
    return scene, main, _MinimapView(scene, main)


def _center_after_background(view) -> QColor:
    """The pixel on the grid dot at (0, 0) after *view* paints its background there."""
    image = QImage(_SIZE, _SIZE, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.translate(_SIZE / 2, _SIZE / 2)
    view.drawBackground(painter, QRectF(-_SIZE / 2, -_SIZE / 2, _SIZE, _SIZE))
    painter.end()
    return image.pixelColor(_SIZE // 2, _SIZE // 2)


def test_the_main_view_draws_the_grid_dot(views):
    """The control: without it the minimap test below would pass with no grid at all."""
    _scene, main, _mini = views
    assert _center_after_background(main).alpha() > 0


def test_the_minimap_leaves_the_grid_out(views):
    _scene, _main, mini = views
    assert _center_after_background(mini).alpha() == 0


def test_the_minimap_still_paints_the_scenes_background_brush(views):
    scene, _main, mini = views
    scene.setBackgroundBrush(QColor(10, 200, 30))
    assert _center_after_background(mini) == QColor(10, 200, 30)
