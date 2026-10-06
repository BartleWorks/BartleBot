"""#126: a native ViewFrame took every click meant for the canvas.

Embedding a game client made the tile native, Qt promoted siblings to native windows, and
the reparenting in ``anchor_menu_to_window`` then made every child of the canvas's view
native, ViewFrame included. A native widget gets its clicks from Windows, which never
consults ``WA_TransparentForMouseEvents``. ``bartlebot/gui/native_widgets.py`` has the
whole chain.

The suite's QApplication is built with ``keep_siblings_alien`` already applied, as the
app's is (``qapp_cls`` in conftest). A tile here is a plain widget that asks for a native
window, which is all ``ClientTile`` does to Qt.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication, QWidget

from bartlebot.gui.canvas.subgraph_widgets import ViewFrame
from bartlebot.gui.menu_utils import popup_menu


def test_the_app_keeps_siblings_of_a_native_widget_alien(qapp):
    assert QApplication.testAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings)


@pytest.fixture
def window_with_a_tile(qapp):
    from bartlebot.gui.main_window import MainWindow  # noqa: PLC0415

    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    QApplication.processEvents()
    tile = QWidget(win.game_view)
    tile.show()
    tile.winId()
    QApplication.processEvents()
    yield win
    win.bot_canvas.prepare_for_shutdown()
    win.close()


def _native(widget: QWidget) -> bool:
    return widget.windowHandle() is not None


def test_an_embedded_tile_leaves_the_canvas_alien(window_with_a_tile):
    canvas = window_with_a_tile.bot_canvas
    assert not _native(canvas)
    assert not _native(canvas.view)


def test_a_popup_menu_on_the_view_leaves_the_frames_alien(window_with_a_tile):
    """The exact route of #126: a right-click on the canvas, with a client embedded."""
    canvas = window_with_a_tile.bot_canvas
    view = canvas.view
    menu = popup_menu(view)
    menu.addAction("Save Region as Reusable Subgraph…")
    menu.popup(view.mapToGlobal(view.rect().center()))
    QApplication.processEvents()
    menu.hide()
    QApplication.processEvents()
    assert not _native(view)
    assert not _native(view.viewport())
    assert not _native(canvas.shared_frame)
    assert not _native(canvas.focus_frame)


def test_the_overlay_button_is_native_so_it_stays_over_the_tiles(window_with_a_tile):
    button = window_with_a_tile.game_view.overlay_button
    assert button.testAttribute(Qt.WidgetAttribute.WA_NativeWindow)


# ---------------------------------------------------------------------------
# The frame's mask: the second guard, for a frame that goes native anyway
# ---------------------------------------------------------------------------


@pytest.fixture
def frame(qtbot):
    """Shown, since a hidden widget's resize event waits until it is."""
    f = ViewFrame()
    qtbot.addWidget(f)
    f.show()
    return f


@pytest.mark.parametrize("shared", [True, False])
def test_the_frame_is_masked_to_its_border(frame, shared):
    if shared:
        frame.set_shared()
    else:
        frame.set_region(2)
    frame.resize(400, 300)
    w = frame.border_px
    assert w == (6 if shared else 3)
    mask = frame.mask()
    assert mask.contains(QPoint(0, 0))
    assert mask.contains(QPoint(w - 1, 150))
    assert mask.contains(QPoint(399, 299))
    assert not mask.contains(QPoint(w, 150)), "just inside the border belongs to the canvas"
    assert not mask.contains(QPoint(200, 150))


def test_the_mask_follows_a_resize_and_a_change_of_line(frame):
    frame.resize(200, 100)
    frame.resize(800, 600)
    assert frame.mask().contains(QPoint(799, 599))
    assert not frame.mask().contains(QPoint(400, 300))
    frame.set_shared()
    assert frame.mask().contains(QPoint(5, 300))
