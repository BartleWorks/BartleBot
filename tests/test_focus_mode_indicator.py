"""Tests for the focus-mode indicator — making it obvious that the canvas is in focus mode.

The issue reports that entering focus mode gives no explanation for everything
disappearing. A "Viewing: <region>" pill already existed when it was filed, nearly
three months earlier, so the interesting part is *why* that failed to register:

* it was styled exactly like every other piece of chrome, so it read as furniture
* it never named a mode, and never said how to leave
* the camera did not move, so a small or off-centre region stayed small and
  off-centre while the rest of the graph vanished — the canvas simply looked empty

So the indicator is three reinforcing signals: the camera frames the region, a border
in the region's own colour surrounds the viewport, and the pill names the mode.

Real widget tests (QApplication required). Nothing reads pixels; these check the
wiring, the text, and the camera.
"""

import pytest

from bartlebot.bot.schema import BotConfig, RegionModel, StateModel, TransitionModel
from bartlebot.gui.canvas.node_item import GRID_H, GRID_W


def _config():
    """Two clusters far apart, so focusing one is a real camera move."""
    states = [
        StateModel(id="a", node_type="change_to_next_realm", params={}, position={"x": 0.0, "y": 0.0}),
        StateModel(id="b", node_type="change_to_next_realm", params={}, position={"x": 0.0, "y": float(GRID_H)}),
        StateModel(
            id="far1", node_type="change_to_next_realm", params={}, position={"x": float(GRID_W * 12), "y": 0.0}
        ),
        StateModel(
            id="far2",
            node_type="change_to_next_realm",
            params={},
            position={"x": float(GRID_W * 12), "y": float(GRID_H)},
        ),
    ]
    return BotConfig(
        id="test-bot",
        name="t",
        initial="a",
        recovery_state="a",
        states=states,
        transitions=[TransitionModel(trigger="advance", source="a", dest="b", when="SUCCESS")],
        regions=[
            RegionModel(id="r1", label="Approach", members=["a", "b"], color=3),
            RegionModel(id="r2", label="Boss", members=["far1", "far2"], color=5),
        ],
    )


@pytest.fixture
def canvas(qapp):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas

    built = []

    def _build():
        c = BotCanvas()
        c.setup(_config())
        c.enter_edit_mode()
        c.resize(800, 600)
        # setup() fits the scene, which holds GraphView._fitting until a 0 ms timer
        # fires. Without an event loop that never happens and every later fit is
        # silently skipped, so drain the queue before the test drives the camera.
        qapp.processEvents()
        built.append(c)
        return c

    yield _build
    for c in built:
        c.prepare_for_shutdown()


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def test_entering_focus_mode_records_the_region(canvas):
    c = canvas()

    c.enter_focus_mode("r1")

    assert c.focus_region_id == "r1"


def test_leaving_focus_mode_clears_it(canvas):
    c = canvas()
    c.enter_focus_mode("r1")

    c.exit_focus_mode()

    assert c.focus_region_id is None


def test_focusing_an_unknown_region_does_nothing(canvas):
    c = canvas()

    c.enter_focus_mode("nope")

    assert c.focus_region_id is None


def test_only_the_focused_region_members_stay_visible(canvas):
    c = canvas()

    c.enter_focus_mode("r1")

    assert c.scene.node_items["a"].isVisible()
    assert not c.scene.node_items["far1"].isVisible()


# ---------------------------------------------------------------------------
# The pill
# ---------------------------------------------------------------------------


def test_the_pill_names_the_mode_and_the_region(canvas):
    """'Viewing: X' never said a mode was entered, which is most of why it was missed."""
    c = canvas()

    c.enter_focus_mode("r1")

    text = c.focus_crumb_text
    assert "Focus Mode" in text
    assert "Approach" in text


def test_the_pill_says_how_to_leave(canvas):
    """Escape already worked and was undiscoverable."""
    c = canvas()

    c.enter_focus_mode("r1")

    assert "Esc" in c.focus_hint_text


# ---------------------------------------------------------------------------
# The frame
# ---------------------------------------------------------------------------


def test_the_frame_is_painted_in_the_focused_region_colour(canvas):
    from bartlebot.gui.theme import THEME

    c = canvas()

    c.enter_focus_mode("r1")

    assert c.focus_color == THEME.region_color(3)


def test_focusing_a_different_region_repaints_the_frame(canvas):
    from bartlebot.gui.theme import THEME

    c = canvas()
    c.enter_focus_mode("r1")

    c.enter_focus_mode("r2")

    assert c.focus_color == THEME.region_color(5)


def test_the_frame_never_swallows_a_click(canvas):
    """It covers the entire viewport, so it has to be transparent to the mouse."""
    from PyQt6.QtCore import Qt

    c = canvas()
    c.enter_focus_mode("r1")

    assert c.focus_frame.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def test_the_frame_covers_the_viewport(canvas):
    c = canvas()

    c.enter_focus_mode("r1")

    assert c.focus_frame.width() > 0
    assert c.focus_frame.size() == c.view.viewport().size()


# ---------------------------------------------------------------------------
# The camera
# ---------------------------------------------------------------------------


def _assert_camera_back(c, before) -> None:
    """The camera came back to within one device pixel of *before*.

    Not a slack tolerance dressed up as one: ``camera_state`` is a transform plus two
    *integer* scrollbar values, and when the scene fits the viewport QGraphicsView adds
    a centering indent that is integer too. One device pixel is the finest thing the
    mechanism can express, so asserting anything finer asserts an accident of rounding
    -- at a zoomed-out fit that was over nine scene units, which is how this pair of
    tests came to fail only in workers where an earlier test had left an app stylesheet
    behind (see ``_restore_app_stylesheet`` in conftest).
    """
    after = c.view.mapToScene(c.view.viewport().rect().center())
    scale = c.view.transform().m11()  # scene units -> device pixels
    assert abs(after.x() - before.x()) * scale <= 1.0 + 1e-6
    assert abs(after.y() - before.y()) * scale <= 1.0 + 1e-6


def test_entering_focus_mode_moves_the_camera_to_the_region(canvas):
    """The root of the complaint: the view used to stay put while the graph vanished."""
    c = canvas()
    before = c.view.mapToScene(c.view.viewport().rect().center())

    c.enter_focus_mode("r2")
    after = c.view.mapToScene(c.view.viewport().rect().center())

    assert after != before


def test_the_camera_lands_on_the_focused_region(canvas):
    c = canvas()

    c.enter_focus_mode("r2")

    centre = c.view.mapToScene(c.view.viewport().rect().center())
    assert c.scene.region_items["r2"].scene_bounds.contains(centre)


def test_leaving_focus_mode_puts_the_camera_back(canvas):
    c = canvas()
    before = c.view.mapToScene(c.view.viewport().rect().center())

    c.enter_focus_mode("r2")
    c.exit_focus_mode()

    _assert_camera_back(c, before)


def test_the_zoom_is_restored_too(canvas):
    c = canvas()
    before = c.view.transform().m11()

    c.enter_focus_mode("r2")
    c.exit_focus_mode()

    assert c.view.transform().m11() == pytest.approx(before)


def test_switching_regions_still_restores_the_original_camera(canvas):
    """Entering a second region exits the first, so the saved camera must not be lost."""
    c = canvas()
    before = c.view.mapToScene(c.view.viewport().rect().center())

    c.enter_focus_mode("r1")
    c.enter_focus_mode("r2")
    c.exit_focus_mode()

    _assert_camera_back(c, before)


def test_exiting_when_not_focused_is_harmless(canvas):
    c = canvas()
    before = c.view.mapToScene(c.view.viewport().rect().center())

    c.exit_focus_mode()

    assert c.view.mapToScene(c.view.viewport().rect().center()) == before
