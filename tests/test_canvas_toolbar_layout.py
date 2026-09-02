"""How Bar B's button clusters are arranged, and what must not move.

Two rules the arrangement encodes.  A transport is a landmark -- people aim at it
without looking -- so it stays in the centre in both modes and is *disabled* when it
cannot be used, rather than being swapped for a pair of graph-editing tools that
happened to fit the same pixels.  And the clusters at the right-hand end are
fixed-width slots, so switching mode does not slide them around under the pointer.
"""

import sys

import pytest
from PyQt6.QtWidgets import QApplication

from bartlebot.bot.schema import BotConfig, StateModel


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def canvas(qapp):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas

    c = BotCanvas()
    c.resize(1500, 600)
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
    # Shown, because an unshown widget never applies the geometry its layout computed
    # and every child reports the same default rectangle.
    c.show()
    yield c
    c.close()
    c.deleteLater()


def _right_edge(canvas, widget) -> int:
    return widget.mapTo(canvas, widget.rect().topRight()).x()


def _left_edge(canvas, widget) -> int:
    return widget.mapTo(canvas, widget.rect().topLeft()).x()


def test_the_transport_is_in_the_same_place_in_both_modes(canvas):
    """Add State and Auto-Layout used to occupy these exact pixels in Edit mode, so
    the same target did two different things with no cue at the target saying which."""
    run_x = _left_edge(canvas, canvas._run_pause_btn)

    canvas.enter_edit_mode()

    assert canvas._run_pause_btn.isVisible()
    assert _left_edge(canvas, canvas._run_pause_btn) == run_x


def test_the_transport_is_disabled_while_editing_and_says_why(canvas):
    canvas.enter_edit_mode()

    assert not canvas._run_pause_btn.isEnabled()
    assert not canvas.stop_button.isEnabled()
    assert canvas._run_pause_btn.toolTip() == "Finish editing to run"


def test_leaving_edit_mode_puts_the_run_button_back(canvas):
    """_apply_run_btn_state is a no-op while the mode is EDIT, so if _apply_mode did
    not call it on the way back the button would stay disabled for good."""
    canvas.set_run_enabled(True)
    canvas.enter_edit_mode()
    canvas._done_btn.click()

    assert canvas._run_pause_btn.isEnabled()


def test_the_view_tools_do_not_move_when_the_mode_changes(canvas):
    """The edit tools take a reserved slot rather than appearing out of nothing: a
    cluster that shifts 78px sideways on a mode switch is one the user has to re-find."""
    before = _left_edge(canvas, canvas._fit_btn)

    canvas.enter_edit_mode()

    assert _left_edge(canvas, canvas._fit_btn) == before


def test_the_mode_toggle_stays_anchored_to_the_right_margin(canvas):
    """One button in Run mode, two in Edit -- right-aligned inside a fixed slot, so
    the way out of the mode is always in the same corner."""
    before = _right_edge(canvas, canvas._edit_btn)

    canvas.enter_edit_mode()

    assert _right_edge(canvas, canvas._save_done_btn) == before


@pytest.mark.parametrize("edit_mode", [False, True])
def test_no_button_is_clipped_by_the_slot_it_sits_in(canvas, edit_mode):
    """Every toolbar row sets its spacing explicitly.  Left to the style's default of
    6px, a row that also called addSpacing(4) came to 80px inside a 68px slot and the
    Save button lost its right-hand end."""
    if edit_mode:
        canvas.enter_edit_mode()

    for stack in (canvas._edit_tools_stack, canvas._right_stack):
        page = stack.currentWidget()
        for child in page.findChildren(type(canvas._fit_btn)):
            assert child.geometry().right() <= page.rect().right()
