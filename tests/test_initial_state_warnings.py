"""Regression tests: initial-state warnings must track the change.

Two halves, fixed at different times:

* ``BotCanvas.refresh_initial`` has to revalidate, not just move the badge —
  moving the start node changes which nodes are reachable.
* The sidebar and the canvas have to still be editing the *same* ``BotConfig``.
  Undo/redo restores from a ``model_dump`` snapshot, so the canvas swaps in a new
  object; a sidebar left bound to the old one writes the new initial state
  somewhere nothing reads, and the warning never moves.

Real widget tests (QApplication required), in the style of
test_canvas_property_edits.  Only the wiring is exercised — nothing reads pixels.
"""

import pytest

from bartlebot.bot.schema import BotConfig, StateModel, TransitionModel


def _config():
    return BotConfig(
        id="test-bot",
        name="t",
        initial="a",
        recovery_state="a",
        states=[StateModel(id=sid, node_type="change_realm", params={}) for sid in ("a", "b")],
        transitions=[TransitionModel(trigger="advance", source="a", dest="b", when="SUCCESS")],
    )


# Canvases built by _Harness, drained after each test.  A QGraphicsScene destroyed
# with items still selected corrupts the heap on the way out, and these tests select
# nodes; prepare_for_shutdown is what makes the teardown safe.
_BUILT: list = []


@pytest.fixture(autouse=True)
def _shut_down_harness_canvases():
    yield
    while _BUILT:
        _BUILT.pop().prepare_for_shutdown()


class _Harness:
    """Canvas + sidebar wired the way AppController._on_config_changed wires them."""

    def __init__(self, config):
        from bartlebot.gui.canvas.bot_canvas import BotCanvas
        from bartlebot.gui.sidebar.left_sidebar import LeftSidebar

        self.canvas = BotCanvas()
        _BUILT.append(self.canvas)
        self.canvas.setup(config)
        self.canvas.enter_edit_mode()
        self.sidebar = LeftSidebar(self.canvas.param_panel)
        self.sidebar.set_config(config)
        self.sidebar.set_editable(True)
        self.loaded = config
        self.sidebar.config_changed.connect(self._on_config_changed)
        self.canvas.config_changed.connect(self._on_config_changed)

    def _on_config_changed(self):
        from bartlebot.gui.canvas.bot_canvas import BotMode

        canvas_config = self.canvas.config
        if canvas_config is not None:
            self.loaded = canvas_config
            self.sidebar.sync_config(canvas_config, self.canvas.mode == BotMode.EDIT)
        if self.loaded is not None:
            self.canvas.refresh_initial(self.loaded.initial)
            self.canvas.refresh_recovery(self.loaded.recovery_state)

    def _show(self, state_id):
        """Select a node, which is what loads its state into the properties panel."""
        self.canvas.select_state(state_id)

    def choose_initial(self, state_id):
        """Set the initial state the way a user now does — from the state's own panel.

        Selecting the node is what loads it into the properties panel, so this is the
        whole path: selection → panel → request → canvas mutation.
        """
        self._show(state_id)
        self.canvas.param_panel.request_initial_role()

    def choose_recovery(self, state_id):
        self._show(state_id)
        self.canvas.param_panel.request_recovery_role()

    def warnings(self):
        return self.canvas.validation_warnings()


def test_moving_the_initial_state_reports_the_orphan(qapp):
    h = _Harness(_config())
    assert not any("Unreachable" in w for w in h.warnings())

    h.choose_initial("b")

    assert h.canvas.config.initial == "b"
    assert "a: Unreachable from initial state" in h.warnings()


def test_moving_the_initial_state_back_clears_the_orphan(qapp):
    h = _Harness(_config())
    h.choose_initial("b")
    assert any("Unreachable" in w for w in h.warnings())

    h.choose_initial("a")

    assert not any("Unreachable" in w for w in h.warnings())


def test_the_sidebar_follows_the_config_undo_swapped_in(qapp):
    """The initial-state warning work's surviving half: after an undo the sidebar edited a discarded object."""
    h = _Harness(_config())
    h.canvas.scene.about_to_change.emit("add a state")
    h.canvas.config.states.append(StateModel(id="c", node_type="change_realm", params={}))
    h.canvas.scene.graph_changed.emit()
    h.canvas.undo()

    assert h.canvas.config is h.sidebar.config

    h.choose_initial("b")

    assert h.canvas.config.initial == "b"
    assert "a: Unreachable from initial state" in h.warnings()
