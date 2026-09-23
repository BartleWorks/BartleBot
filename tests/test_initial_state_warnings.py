"""Regression tests: initial-state warnings must track the change.

Two halves, fixed at different times:

* ``BotCanvas.refresh_initial`` has to revalidate, not just move the badge —
  moving the start node changes which nodes are reachable.
* The sidebars and the canvas have to still be editing the *same* ``BotConfig``.
  Undo/redo restores from a ``model_dump`` snapshot, so the canvas swaps in a new
  object; a sidebar left bound to the old one writes the new initial state
  somewhere nothing reads, and the warning never moves.  The right sidebar's
  Possible Drops list had the same bug after the left one was fixed: drops added
  there showed in the list and nowhere else, and were not saved.

Real widget tests (QApplication required), in the style of
test_canvas_property_edits.  Only the wiring is exercised — nothing reads pixels.
"""

import pytest

from bartlebot.bot.schema import BotConfig, DropModel, StateModel, TransitionModel
from bartlebot.gui.canvas.drop_keep_list import _COL_ITEM
from bartlebot.nodes.utility import quicksell as qs


def _config(node_type="change_realm"):
    return BotConfig(
        id="test-bot",
        name="t",
        initial="a",
        recovery_state="a",
        states=[StateModel(id=sid, node_type=node_type, params={}) for sid in ("a", "b")],
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
    """Canvas + both sidebars wired the way MainWindow and AppController._on_config_changed wire them."""

    def __init__(self, config):
        from bartlebot.gui.canvas.bot_canvas import BotCanvas
        from bartlebot.gui.sidebar.left_sidebar import LeftSidebar
        from bartlebot.gui.sidebar.right_sidebar import RightSidebar

        self.canvas = BotCanvas()
        _BUILT.append(self.canvas)
        self.canvas.setup(config)
        self.canvas.enter_edit_mode()
        self.sidebar = LeftSidebar(self.canvas.param_panel)
        self.sidebar.set_config(config)
        self.sidebar.set_editable(True)
        self.right = RightSidebar()
        self.right.set_config(config)
        self.right.set_editable(True)
        self.loaded = config
        self.right.possible_drops.drops_changed.connect(self.sidebar.refresh_drops)
        self.sidebar.config_changed.connect(self._on_config_changed)
        self.right.config_changed.connect(self._on_config_changed)
        self.canvas.config_changed.connect(self._on_config_changed)

    def _on_config_changed(self):
        from bartlebot.gui.canvas.bot_canvas import BotMode

        canvas_config = self.canvas.config
        if canvas_config is not None:
            self.loaded = canvas_config
            self.sidebar.sync_config(canvas_config, self.canvas.mode == BotMode.EDIT)
            self.right.sync_config(canvas_config)
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

    def undo_an_edit(self):
        """Make an undoable graph edit and undo it, so the canvas swaps in a new config."""
        self.canvas.scene.about_to_change.emit("add a state")
        self.canvas.config.states.append(StateModel(id="c", node_type="change_realm", params={}))
        self.canvas.scene.graph_changed.emit()
        self.canvas.undo()

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
    h.undo_an_edit()

    assert h.canvas.config is h.sidebar.config

    h.choose_initial("b")

    assert h.canvas.config.initial == "b"
    assert "a: Unreachable from initial state" in h.warnings()


def test_drops_added_after_an_undo_reach_the_saved_config_and_the_keep_list(qapp):
    """Possible Drops kept writing into the object undo discarded: the drop showed in its
    own list, but not in the Setup keep-list picker, and the save wrote ``drops: []``."""
    h = _Harness(_config(node_type="quicksell"))
    h.undo_an_edit()

    h.right.possible_drops.add_drops([DropModel(name="Onyx", category="Jewels")])

    assert h.right.possible_drops.config is h.canvas.config
    assert [d.name for d in h.canvas.config.drops] == ["Onyx"]
    picker = h.sidebar.setup.option_widgets[qs.OPTION_KEEP_LIST]
    assert [r.text(_COL_ITEM) for r in picker._rows] == ["Onyx"]
