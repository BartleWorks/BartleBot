"""Tests for UndoManager — snapshot-based undo/redo for the canvas editor."""

import sys

import pytest
from PyQt6.QtWidgets import QApplication

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.gui.canvas.undo_manager import UndoManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication required by QTimer inside UndoManager."""
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def mgr(qapp):
    return UndoManager()


def _config(name: str = "test") -> BotConfig:
    """Minimal BotConfig whose identity can be checked via .name."""
    return BotConfig(
        name=name,
        initial="s1",
        recovery_state="s1",
        states=[StateModel(id="s1", node_type="sleep")],
        transitions=[],
    )


# ---------------------------------------------------------------------------
# push / pop_undo
# ---------------------------------------------------------------------------


def test_undo_restores_config(mgr):
    """push() before a mutation; pop_undo() returns the pre-mutation config."""
    before = _config("before")
    after = _config("after")

    mgr.push(before, "add node")
    result = mgr.pop_undo(after)

    assert result is not None
    assert result.name == "before"


def test_undo_empty_stack_returns_none(mgr):
    assert mgr.pop_undo(_config()) is None


def test_undo_moves_current_to_redo(mgr):
    """pop_undo() pushes current state onto the redo stack."""
    before = _config("before")
    current = _config("current")

    mgr.push(before, "edit")
    mgr.pop_undo(current)

    result = mgr.pop_redo(_config("after_redo"))
    assert result is not None
    assert result.name == "current"


# ---------------------------------------------------------------------------
# pop_redo
# ---------------------------------------------------------------------------


def test_redo_reapplies_config(mgr):
    """Undo then redo returns to the state that was undone from."""
    before = _config("before")
    current = _config("current")

    mgr.push(before, "edit")
    mgr.pop_undo(current)  # current → redo, returns before
    result = mgr.pop_redo(before)  # before → undo, returns current

    assert result is not None
    assert result.name == "current"


def test_redo_empty_stack_returns_none(mgr):
    assert mgr.pop_redo(_config()) is None


def test_push_clears_redo_stack(mgr):
    """After undo, doing a new structural edit must clear the redo stack."""
    before = _config("before")
    current = _config("current")

    mgr.push(before, "first edit")
    mgr.pop_undo(current)  # populates redo

    assert mgr.can_redo()
    mgr.push(current, "second edit")
    assert not mgr.can_redo()


# ---------------------------------------------------------------------------
# Max depth
# ---------------------------------------------------------------------------


def test_max_depth_drops_oldest():
    """The 50-step limit drops the oldest snapshot when exceeded."""
    QApplication.instance() or QApplication(sys.argv)
    mgr = UndoManager()

    for i in range(51):
        mgr.push(_config(f"step_{i}"), f"edit {i}")

    assert len(mgr._undo) == 50
    # Oldest (step_0) was dropped; step_1 is now at the bottom.
    oldest = mgr._undo[0]
    assert oldest.data["name"] == "step_1"


# ---------------------------------------------------------------------------
# Debounce
# ---------------------------------------------------------------------------


def test_debounce_multiple_calls_produce_one_snapshot(mgr):
    """Rapid begin_debounce() calls capture only the first 'before' state."""
    config_a = _config("a")
    config_b = _config("b")  # intermediate edit, should NOT be captured
    config_c = _config("c")  # final edit state (current when committing)

    mgr.begin_debounce(config_a, "param edit")
    mgr.begin_debounce(config_b, "param edit")  # timer restarts; pending unchanged
    mgr.begin_debounce(config_c, "param edit")  # timer restarts; pending unchanged

    mgr._commit_debounce()  # simulate timer fire

    assert len(mgr._undo) == 1
    assert mgr._undo[0].data["name"] == "a"


def test_debounce_commit_clears_pending(mgr):
    mgr.begin_debounce(_config("x"), "edit")
    mgr._commit_debounce()
    assert mgr._pending is None


def test_debounce_cancel_discards_snapshot(mgr):
    """cancel_debounce() throws away the pending snapshot without committing."""
    mgr.begin_debounce(_config("x"), "edit")
    mgr.cancel_debounce()

    assert mgr._pending is None
    assert not mgr.can_undo()


def test_debounce_clears_redo(mgr):
    """Committing a debounced edit must clear the redo stack, same as push()."""
    before = _config("before")
    current = _config("current")

    mgr.push(before, "structural")
    mgr.pop_undo(current)  # populate redo

    assert mgr.can_redo()
    mgr.begin_debounce(current, "param edit")
    mgr._commit_debounce()
    assert not mgr.can_redo()


# ---------------------------------------------------------------------------
# Interleave: structural push followed by debounced param edit
# ---------------------------------------------------------------------------


def test_interleave_push_then_debounce(mgr):
    """Structural edit and param edit produce independent undo steps."""
    config_a = _config("a")
    config_b = _config("b")  # after structural edit
    config_c = _config("c")  # after param edit

    mgr.push(config_a, "add node")  # before structural edit
    mgr.begin_debounce(config_b, "edit param")  # before param edit
    mgr._commit_debounce()

    assert len(mgr._undo) == 2

    # First undo: c → b
    result = mgr.pop_undo(config_c)
    assert result.name == "b"

    # Second undo: b → a
    result = mgr.pop_undo(config_b)
    assert result.name == "a"


# ---------------------------------------------------------------------------
# State queries
# ---------------------------------------------------------------------------


def test_can_undo_can_redo(mgr):
    assert not mgr.can_undo()
    assert not mgr.can_redo()

    mgr.push(_config("before"), "edit")
    assert mgr.can_undo()
    assert not mgr.can_redo()

    mgr.pop_undo(_config("after"))
    assert not mgr.can_undo()
    assert mgr.can_redo()


def test_undo_desc_and_redo_desc(mgr):
    assert mgr.undo_desc() is None
    assert mgr.redo_desc() is None

    mgr.push(_config(), "my edit")
    assert mgr.undo_desc() == "my edit"

    mgr.pop_undo(_config("current"))
    assert mgr.redo_desc() == "my edit"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_clear_empties_both_stacks(mgr):
    mgr.push(_config("a"), "edit")
    mgr.pop_undo(_config("b"))  # populate redo
    mgr.begin_debounce(_config("c"), "param")

    mgr.clear()

    assert not mgr.can_undo()
    assert not mgr.can_redo()
    assert mgr._pending is None


def test_set_enabled_false_disables_push(mgr):
    mgr.set_enabled(False)
    mgr.push(_config(), "edit")
    assert not mgr.can_undo()
    mgr.set_enabled(True)


def test_set_enabled_false_disables_debounce(mgr):
    mgr.set_enabled(False)
    mgr.begin_debounce(_config(), "param edit")
    mgr._commit_debounce()
    assert not mgr.can_undo()
    mgr.set_enabled(True)
