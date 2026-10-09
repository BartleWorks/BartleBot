"""Why every graph view delivers itself one synthetic mouse move when it is shown.

Leaving Edit mode calls ``unsetCursor()`` on every node, and Qt answers that by asking
the view what is under the pointer -- through ``lastMouseEvent``, which its private
class never initializes and which only a real mouse move fills in.  The one reader that
consults it, ``_q_unsetViewportCursor()``, is also the only one missing the
``useLastMouseEvent`` guard, so on a view that has never seen a mouse event it reads
uninitialized storage and the process dies inside Qt6Gui (#95).

The workaround is to fill that storage once, on show.  Two things about it are
load-bearing and are what these tests pin: it happens on *every* view attached to the
scene, because ``unsetCursor()`` walks all of them, and it lands on the real pointer
rather than the viewport center, because the move is dispatched into the scene as hover
and a center point would light up whichever node happens to sit there.
"""

import pytest
from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QGraphicsScene

from bartlebot.bot.schema import BotConfig, StateModel


class _MoveRecorder(QObject):
    """Collects the mouse moves delivered to whatever it is installed on."""

    def __init__(self):
        super().__init__()
        self.moves = []

    def eventFilter(self, obj, event):  # noqa: ANN001, N802
        if event.type() == QEvent.Type.MouseMove:
            self.moves.append((event.position().toPoint(), event.buttons()))
        return False


@pytest.fixture
def scene(qapp):
    return QGraphicsScene()


@pytest.fixture
def view(qapp, scene):
    from bartlebot.gui.canvas.graph_view import GraphView

    v = GraphView(scene, interactive=True)
    v.resize(400, 300)
    yield v
    v.close()


@pytest.fixture
def pinned_pointer(monkeypatch):
    """Freeze the pointer the priming reads, for the one test that asserts on it.

    ``prime_last_mouse_event`` samples ``QCursor.pos()``, and an assertion that reads
    it again is comparing two samples taken at different instants -- so moving the
    physical mouse in between, which is what using the machine while the suite runs
    looks like, fails the test.  Patching the name the module under test calls pins
    both ends to one value and leaves the user's pointer where it is, which
    ``QCursor.setPos`` would not.  The value is the live pointer, so the point being
    mapped is still a real one on a real screen.
    """
    from bartlebot.gui.canvas import graph_view

    where = QCursor.pos()

    class _PinnedCursor:
        @staticmethod
        def pos():
            return where

    monkeypatch.setattr(graph_view, "QCursor", _PinnedCursor)
    return where


def _recorded(widget):
    rec = _MoveRecorder()
    widget.viewport().installEventFilter(rec)
    return rec


def test_showing_a_view_primes_exactly_one_synthetic_move(view):
    rec = _recorded(view)
    view.show()

    assert len(rec.moves) == 1


def test_the_primed_move_carries_no_buttons(view):
    """A held button would read as a pan or a rubber band instead of a bare move."""
    rec = _recorded(view)
    view.show()

    _pos, buttons = rec.moves[0]
    from PyQt6.QtCore import Qt

    assert buttons == Qt.MouseButton.NoButton


def test_the_primed_move_lands_on_the_real_pointer(view, pinned_pointer):
    """The failure path this guards: a viewport-center point hovers whatever is there.

    ``GraphView.mouseMoveEvent`` falls through to ``QGraphicsView``'s, which dispatches
    hover into the scene and applies the hovered item's cursor -- so priming at the
    center leaves a node showing its hover highlight and grab cursor with the pointer
    somewhere else entirely.
    """
    rec = _recorded(view)
    view.show()

    pos, _buttons = rec.moves[0]
    assert pos == view.viewport().mapFromGlobal(pinned_pointer)


def test_a_view_with_no_mouse_tracking_is_still_primed(view):
    """The second failure path: Qt drops button-less moves to an untracked viewport.

    A canvas shown before any bot is loaded is exactly that -- nothing in the scene has
    a cursor yet, so Qt has not turned tracking on -- and the priming would be dropped
    and, because it only runs once, never retried.
    """
    assert not view.viewport().hasMouseTracking()  # or the test asserts nothing
    rec = _recorded(view)
    view.show()

    assert len(rec.moves) == 1


def test_priming_leaves_mouse_tracking_as_it_found_it(view):
    """Tracking is Qt's to manage; priming borrows it rather than turning it on."""
    before = view.viewport().hasMouseTracking()
    view.show()

    assert view.viewport().hasMouseTracking() == before


def test_a_second_show_does_not_prime_again(view):
    """Once filled the storage stays filled, so re-showing has nothing to do."""
    rec = _recorded(view)
    view.show()
    view.hide()
    view.show()

    assert len(rec.moves) == 1


def test_the_minimap_is_primed_too(qapp, scene, view):
    """A second view on the same scene is a second unprimed trigger, not a spectator."""
    from bartlebot.gui.canvas.graph_view import _MinimapView

    mini = _MinimapView(scene, view)
    rec = _recorded(mini)
    mini.show()

    assert len(rec.moves) == 1
    mini.close()


def test_priming_does_not_hover_a_node_sitting_at_the_viewport_center(qapp):
    """The property the real-pointer position buys, measured on a real canvas.

    Shown while already in Edit mode, which is the one ordering where the nodes are
    interactive and accepting hover at the moment the synthetic move lands.
    """
    from bartlebot.gui.canvas.bot_canvas import BotCanvas

    c = BotCanvas()
    c.resize(1400, 700)
    c.setup(
        BotConfig(
            id="test-bot",
            name="Example",
            initial="a",
            recovery_state="a",
            states=[StateModel(id="a", node_type="change_realm", params={})],
            transitions=[],
        )
    )
    c.enter_edit_mode()
    c.show()

    view = c.view
    viewport = view.viewport()
    if viewport.rect().contains(viewport.mapFromGlobal(QCursor.pos())):
        pytest.skip("pointer is genuinely over the canvas, where a hover is correct")

    node = next(iter(view.scene().node_items.values()))
    assert node.acceptHoverEvents()  # or the test asserts nothing
    assert view.itemAt(viewport.rect().center()) is node
    assert not node.is_hovered

    c.close()
