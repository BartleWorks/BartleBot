"""A node paints nothing outside its own boundingRect.

Qt repaints an item only inside its bounds, so whatever it draws past them is never
erased when the item moves or the thing drawn goes away, and is painted over, not
redrawn, when a neighbour repaints across it -- the stale-pixel kind #73 is about.
"""

import pytest
from PyQt6.QtGui import QPainter, QPicture
from PyQt6.QtWidgets import QStyleOptionGraphicsItem

from bartlebot.gui.canvas.node_item import NodeItem


def painted_rect(item):
    pic = QPicture()
    painter = QPainter(pic)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    item.paint(painter, QStyleOptionGraphicsItem())
    painter.end()
    return pic.boundingRect().toRectF()


@pytest.mark.parametrize("interactive", [True, False], ids=["edit", "run"])
@pytest.mark.parametrize("roles", [(True, False, False), (True, True, True), (False, False, True)])
def test_role_badges_paint_inside_the_bounding_rect(qapp, interactive, roles):
    initial, terminal, recovery = roles
    node = NodeItem("s", "state", interactive=interactive, returns=["SUCCESS", "FAILURE"])
    node.set_initial(initial)
    node.set_terminal(terminal)
    node.set_recovery(recovery)
    assert node.boundingRect().contains(painted_rect(node))


@pytest.mark.parametrize("interactive", [True, False], ids=["edit", "run"])
def test_the_run_highlights_outline_paints_inside_the_bounding_rect(qapp, interactive):
    """The widest outline is the running node's; in Run mode it had no margin at all."""
    node = NodeItem("s", "state", interactive=interactive, returns=["SUCCESS"])
    node.set_active(True)
    try:
        assert node.boundingRect().contains(painted_rect(node))
    finally:
        node.set_active(False)
