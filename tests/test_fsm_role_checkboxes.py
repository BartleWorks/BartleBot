"""Tests for the state-role checkboxes — initial/recovery state set from the state's own properties panel.

Both roles are bot-wide, exactly-one pointers into the states list, so the checkboxes
read as checkboxes but behave as radios: checking one moves the role, and the box on
the state that already holds it is disabled, because ``BotConfig`` has no legal "no
initial state" to fall back to.

Real widget tests (QApplication required), in the style of test_initial_state_warnings.
"""

import pytest

from bartlebot.bot.schema import BotConfig, StateModel, TransitionModel


def _config():
    return BotConfig(
        id="test-bot",
        name="t",
        initial="a",
        recovery_state="a",
        states=[StateModel(id=sid, node_type="change_realm", params={}) for sid in ("a", "b", "c")],
        transitions=[
            TransitionModel(trigger="advance", source="a", dest="b", when="SUCCESS"),
            TransitionModel(trigger="advance", source="b", dest="c", when="SUCCESS"),
        ],
    )


@pytest.fixture
def canvas(qapp):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas

    c = BotCanvas()
    c.setup(_config())
    c.enter_edit_mode()
    yield c
    c.prepare_for_shutdown()


def _show(canvas, state_id):
    """Select a node on the canvas and return the properties panel showing it.

    The real path: selecting is what loads a state into the panel.
    """
    canvas.select_state(state_id)
    return canvas.param_panel


# ---------------------------------------------------------------------------
# Moving a role
# ---------------------------------------------------------------------------


def test_checking_initial_moves_the_role(canvas):
    _show(canvas, "b").request_initial_role()

    assert canvas.config.initial == "b"


def test_checking_recovery_moves_the_role(canvas):
    _show(canvas, "b").request_recovery_role()

    assert canvas.config.recovery_state == "b"


def test_the_two_roles_are_independent(canvas):
    """Moving one must not drag the other along, even when they start on one state."""
    _show(canvas, "b").request_initial_role()

    assert canvas.config.initial == "b"
    assert canvas.config.recovery_state == "a"


def test_a_role_can_be_moved_twice(canvas):
    _show(canvas, "b").request_initial_role()
    _show(canvas, "c").request_initial_role()

    assert canvas.config.initial == "c"


# ---------------------------------------------------------------------------
# Checkbox state
# ---------------------------------------------------------------------------


def test_the_holder_shows_checked_and_disabled(canvas):
    """No click on the holder can mean anything — the role cannot be vacated."""
    panel = _show(canvas, "a")

    assert panel.initial_check.isChecked()
    assert not panel.initial_check.isEnabled()
    assert panel.recovery_check.isChecked()
    assert not panel.recovery_check.isEnabled()


def test_a_non_holder_shows_unchecked_and_enabled(canvas):
    panel = _show(canvas, "b")

    assert not panel.initial_check.isChecked()
    assert panel.initial_check.isEnabled()


def test_the_shown_node_loses_its_tick_when_the_role_moves_away(canvas):
    """The losing node is the one already on screen — its tick has to come off.

    Only reachable because ``request_initial_role`` acts on the shown state: click
    the box on b while a is shown is impossible, but the panel is refreshed for
    whichever node is displayed after the move, and here that is the loser.
    """
    panel = _show(canvas, "b")
    panel.request_initial_role()
    assert panel.initial_check.isChecked()

    # Move it on, without changing what the panel is showing.
    canvas.param_panel.show_state(
        next(s for s in canvas.config.states if s.id == "b"),
        initial_id="c",
        recovery_id=canvas.config.recovery_state,
    )

    assert not panel.initial_check.isChecked()
    assert panel.initial_check.isEnabled()


def test_unchecking_is_refused(canvas):
    """Defensive: the box is disabled, but a click that got through must not clear the role."""
    panel = _show(canvas, "a")
    panel.initial_check.setEnabled(True)

    panel.initial_check.click()

    assert canvas.config.initial == "a"
    assert panel.initial_check.isChecked()


def test_clicking_an_enabled_box_moves_the_role(canvas):
    """The click path, not just the public request method."""
    panel = _show(canvas, "b")

    panel.initial_check.click()

    assert canvas.config.initial == "b"


# ---------------------------------------------------------------------------
# Integration with the rest of the editor
# ---------------------------------------------------------------------------


def test_moving_the_initial_state_is_undoable(canvas):
    _show(canvas, "b").request_initial_role()
    assert canvas.config.initial == "b"

    canvas.undo()

    assert canvas.config.initial == "a"


def test_moving_the_recovery_state_is_undoable(canvas):
    _show(canvas, "b").request_recovery_role()

    canvas.undo()

    assert canvas.config.recovery_state == "a"


def test_re_requesting_the_role_a_state_already_holds_is_a_no_op(canvas):
    """No config change, and no undo entry for an edit that did nothing."""
    before = canvas.can_undo()

    _show(canvas, "a").request_initial_role()

    assert canvas.config.initial == "a"
    assert canvas.can_undo() == before


def test_moving_the_initial_state_revalidates(canvas):
    """The orphan warnings have to move with the role, not just the badge."""
    assert not any("Unreachable" in w for w in canvas.validation_warnings())

    _show(canvas, "b").request_initial_role()

    assert "a: Unreachable from initial state" in canvas.validation_warnings()


def test_the_node_badge_follows_the_role(canvas):
    _show(canvas, "b").request_initial_role()

    assert not canvas.scene.node_items["a"].is_initial
    assert canvas.scene.node_items["b"].is_initial


# The Bot Info mirror of the two roles is gone with the Bot tab: the badge on
# the node is the only view of them now, which is what the test above covers.


# ---------------------------------------------------------------------------
# Deleting the state that holds a role
# ---------------------------------------------------------------------------


def test_deleting_the_initial_state_moves_the_role(canvas):
    """A dangling role saves fine and then refuses to load — so it must not dangle."""
    canvas.scene.delete_state("a", confirm=False)

    assert canvas.config.initial in {s.id for s in canvas.config.states}


def test_deleting_the_recovery_state_moves_the_role(canvas):
    canvas.scene.delete_state("a", confirm=False)

    assert canvas.config.recovery_state in {s.id for s in canvas.config.states}


def test_deleting_an_unrelated_state_leaves_the_roles_alone(canvas):
    canvas.scene.delete_state("c", confirm=False)

    assert canvas.config.initial == "a"
    assert canvas.config.recovery_state == "a"


def test_a_config_survives_deleting_its_role_holder(canvas):
    """The end-to-end symptom: the edited config still round-trips through the schema."""
    canvas.scene.delete_state("a", confirm=False)

    BotConfig.model_validate(canvas.config.model_dump())


# ---------------------------------------------------------------------------
# Widget lifetime
# ---------------------------------------------------------------------------


def test_closing_the_canvas_clears_the_selection(canvas):
    """A scene destroyed with items still selected corrupts the heap on the way out.

    It surfaces as a 0xC0000409 fail-fast during interpreter finalization, with no
    Python traceback -- measured at 8 crashes in 40 runs of a script that selects a
    few nodes and exits, and 0 in 40 once the selection is cleared first.
    """
    canvas.scene.node_items["b"].setSelected(True)
    assert canvas.scene.selectedItems()

    canvas.close()

    assert not canvas.scene.selectedItems()


def test_preparing_for_shutdown_twice_is_harmless(canvas):
    """It runs from closeEvent and from the app's aboutToQuit, so both can fire."""
    canvas.prepare_for_shutdown()
    canvas.prepare_for_shutdown()

    assert not canvas.scene.selectedItems()


def test_shutdown_does_not_notify_the_panel(canvas):
    """selectionChanged reaches into the properties panel; nothing should repaint a
    panel on the way to being deleted."""
    seen = []
    canvas.scene.selectionChanged.connect(lambda: seen.append(1))
    canvas.scene.node_items["b"].setSelected(True)
    seen.clear()

    canvas.prepare_for_shutdown()

    assert seen == []


def test_closing_the_canvas_stops_its_timers(canvas):
    """A validation pass must not stay queued against a canvas that is going away.

    ``_validation_timer`` is single-shot and re-armed on every graph change, so there
    is always a window where a close lands with one pending.  Asserted through Qt's
    own child list rather than the canvas's private attributes.
    """
    from PyQt6.QtCore import QTimer

    # A param edit is what defers validation behind the timer -- the expensive pass is
    # debounced so it does not run on every keystroke.
    _show(canvas, "b")
    canvas.param_panel.properties_changed.emit()
    assert any(t.isActive() for t in canvas.findChildren(QTimer))

    canvas.close()

    assert not any(t.isActive() for t in canvas.findChildren(QTimer))
