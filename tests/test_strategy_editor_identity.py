"""Tests for save-identity in the strategy editors and the Strategy Library window.

The bug these cover: both editors auto-save on a 500 ms debounce, and the library
window used to resolve *which* strategy to persist from ``list.currentItem()`` at
save time.  Selecting a different strategy within the debounce window therefore
wrote the outgoing strategy's edits into the incoming strategy's file — the
outgoing edits were lost and the incoming file was silently corrupted.

These are real widget tests rather than pure-logic tests because the defect lives
entirely in the signal/slot wiring.  ``monkeypatch.chdir`` isolates the strategy
store (which resolves paths from the cwd) into ``tmp_path``.
"""

import sys

import pytest
from PyQt6.QtWidgets import QApplication

from bartlebot.bot.strategy_models import (
    DuelCombatStrategy,
    PlayerAssignment,
    PlayerCombatStrategy,
)
from bartlebot.gui.strategy_library.duel_strategy_editor import DuelStrategyEditor
from bartlebot.gui.strategy_library.player_strategy_editor import PlayerStrategyEditor
from bartlebot.gui.strategy_library.strategy_library_window import StrategyLibraryWindow
from bartlebot.strategy_store import (
    load_duel_strategy,
    load_player_strategy,
    save_duel_strategy,
    save_player_strategy,
)

_ID_ROLE = 0x100


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication required by the QTimer inside each editor."""
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the strategy store at an empty tmp_path for the duration of a test."""
    (tmp_path / "strategies" / "player").mkdir(parents=True)
    (tmp_path / "strategies" / "duel").mkdir(parents=True)
    (tmp_path / "strategies" / "decks").mkdir(parents=True)
    (tmp_path / "bot_strategies").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _item_text(list_widget, uuid):
    for i in range(list_widget.count()):
        item = list_widget.item(i)
        if item.data(_ID_ROLE) == uuid:
            return item.text()
    return None


# ---------------------------------------------------------------------------
# PlayerStrategyEditor — signal carries the edited strategy's id
# ---------------------------------------------------------------------------


def test_pcs_editor_emits_the_loaded_strategy_id(qapp):
    editor = PlayerStrategyEditor()
    pcs = PlayerCombatStrategy(name="A", dsl="")
    editor.load(pcs)

    seen = []
    editor.changed.connect(seen.append)
    editor._dsl_edit.setPlainText("any<damage>")
    editor.flush()

    assert seen == [pcs.id]


def test_pcs_editor_emits_the_id_it_was_loaded_with_not_a_later_one(qapp):
    """The id is captured from the strategy being edited, so a later load is irrelevant."""
    editor = PlayerStrategyEditor()
    first = PlayerCombatStrategy(name="A", dsl="")
    editor.load(first)
    editor._dsl_edit.setPlainText("any<damage>")

    seen = []
    editor.changed.connect(seen.append)
    editor.flush()

    assert seen == [first.id]


def test_pcs_editor_flush_is_a_noop_with_nothing_pending(qapp):
    editor = PlayerStrategyEditor()
    editor.load(PlayerCombatStrategy(name="A", dsl=""))
    seen = []
    editor.changed.connect(seen.append)
    editor.flush()
    assert seen == []


def test_pcs_editor_clear_drops_the_pending_save(qapp):
    editor = PlayerStrategyEditor()
    editor.load(PlayerCombatStrategy(name="A", dsl=""))
    editor._dsl_edit.setPlainText("any<damage>")
    assert editor._save_timer.isActive()

    seen = []
    editor.changed.connect(seen.append)
    editor.clear()
    editor.flush()

    assert not editor._save_timer.isActive()
    assert seen == []


def test_pcs_editor_flush_after_clear_does_not_emit(qapp):
    editor = PlayerStrategyEditor()
    editor.load(PlayerCombatStrategy(name="A", dsl=""))
    editor.clear()
    seen = []
    editor.changed.connect(seen.append)
    editor._flush()
    assert seen == []


def test_pcs_editor_blank_name_keeps_the_existing_one(qapp):
    editor = PlayerStrategyEditor()
    pcs = PlayerCombatStrategy(name="Original", dsl="")
    editor.load(pcs)
    editor._name_edit.setText("   ")
    editor.flush()
    assert pcs.name == "Original"


# ---------------------------------------------------------------------------
# DuelStrategyEditor — same contract
# ---------------------------------------------------------------------------


def test_dcs_editor_emits_the_loaded_strategy_id(qapp):
    editor = DuelStrategyEditor()
    dcs = DuelCombatStrategy(name="Duel A")
    editor.load(dcs, [])

    seen = []
    editor.changed.connect(seen.append)
    editor._min_spin.setValue(3)
    editor.flush()

    assert seen == [dcs.id]
    assert dcs.min_players == 3


def test_dcs_editor_clear_drops_the_pending_save(qapp):
    editor = DuelStrategyEditor()
    editor.load(DuelCombatStrategy(name="Duel A"), [])
    editor._min_spin.setValue(2)
    assert editor._save_timer.isActive()

    seen = []
    editor.changed.connect(seen.append)
    editor.clear()
    editor.flush()

    assert not editor._save_timer.isActive()
    assert seen == []


def test_dcs_editor_clear_removes_the_assignment_rows(qapp):
    editor = DuelStrategyEditor()
    dcs = DuelCombatStrategy(name="Duel A", player_assignments=[PlayerAssignment(players=[1], dsl="x")])
    editor.load(dcs, [])
    assert editor._rows

    editor.clear()

    assert editor._rows == []


# ---------------------------------------------------------------------------
# StrategyLibraryWindow — the actual data-loss scenario
# ---------------------------------------------------------------------------


def test_switching_pcs_mid_edit_saves_the_edited_one(qapp, store):
    """Type into A, click B before the debounce fires: A must be what gets saved."""
    a = PlayerCombatStrategy(name="A", dsl="original-a")
    b = PlayerCombatStrategy(name="B", dsl="original-b")
    save_player_strategy(a)
    save_player_strategy(b)

    win = StrategyLibraryWindow()
    win.focus_player_strategy(a.id)
    win._pcs_editor._dsl_edit.setPlainText("edited-a")

    # Selecting B flushes the editor while A's edits are still pending.
    win.focus_player_strategy(b.id)

    assert load_player_strategy(a.id).dsl == "edited-a"
    assert load_player_strategy(b.id).dsl == "original-b"


def test_switching_dcs_mid_edit_saves_the_edited_one(qapp, store):
    a = DuelCombatStrategy(name="Duel A", min_players=1)
    b = DuelCombatStrategy(name="Duel B", min_players=1)
    save_duel_strategy(a)
    save_duel_strategy(b)

    win = StrategyLibraryWindow()
    win.focus_duel_strategy(a.id)
    win._dcs_editor._min_spin.setValue(4)

    win.focus_duel_strategy(b.id)

    assert load_duel_strategy(a.id).min_players == 4
    assert load_duel_strategy(b.id).min_players == 1


def test_renaming_pcs_mid_edit_renames_the_edited_row(qapp, store):
    a = PlayerCombatStrategy(name="A", dsl="")
    b = PlayerCombatStrategy(name="B", dsl="")
    save_player_strategy(a)
    save_player_strategy(b)

    win = StrategyLibraryWindow()
    win.focus_player_strategy(a.id)
    win._pcs_editor._name_edit.setText("A renamed")
    win.focus_player_strategy(b.id)

    assert _item_text(win._pcs_list, a.id) == "A renamed"
    assert _item_text(win._pcs_list, b.id) == "B"
    assert load_player_strategy(a.id).name == "A renamed"
    assert load_player_strategy(b.id).name == "B"


def test_pcs_save_for_an_unknown_id_is_ignored(qapp, store):
    """A save signal for a strategy no longer in the list must not raise or write."""
    a = PlayerCombatStrategy(name="A", dsl="")
    save_player_strategy(a)
    win = StrategyLibraryWindow()

    win._on_pcs_saved("not-a-real-id")  # must not raise

    assert load_player_strategy(a.id).name == "A"


def test_dcs_save_for_an_unknown_id_is_ignored(qapp, store):
    save_duel_strategy(DuelCombatStrategy(name="Duel A"))
    win = StrategyLibraryWindow()
    win._on_dcs_saved("not-a-real-id")  # must not raise


def test_pcs_save_still_persists_when_the_row_is_filtered_out(qapp, store):
    """The list row may be hidden by the filter box; the file must still be written."""
    a = PlayerCombatStrategy(name="Alpha", dsl="original")
    save_player_strategy(a)

    win = StrategyLibraryWindow()
    win.focus_player_strategy(a.id)
    win._pcs_editor._dsl_edit.setPlainText("edited")
    win._pcs_filter.setText("zzz-no-match")
    assert win._find_item(win._pcs_list, a.id) is None

    win._pcs_editor.flush()

    assert load_player_strategy(a.id).dsl == "edited"


# ---------------------------------------------------------------------------
# Delete does not get undone by a pending save
# ---------------------------------------------------------------------------


def test_deleting_a_pcs_mid_edit_does_not_resurrect_the_file(qapp, store, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    a = PlayerCombatStrategy(name="A", dsl="original")
    save_player_strategy(a)
    path = store / "strategies" / "player" / f"{a.id}.json"

    win = StrategyLibraryWindow()
    win.focus_player_strategy(a.id)
    win._pcs_editor._dsl_edit.setPlainText("edited")
    assert win._pcs_editor._save_timer.isActive()

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win._on_delete_pcs()
    win._pcs_editor.flush()

    assert not path.exists()


def test_deleting_a_dcs_mid_edit_does_not_resurrect_the_file(qapp, store, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    d = DuelCombatStrategy(name="Duel A")
    save_duel_strategy(d)
    path = store / "strategies" / "duel" / f"{d.id}.json"

    win = StrategyLibraryWindow()
    win.focus_duel_strategy(d.id)
    win._dcs_editor._min_spin.setValue(4)
    assert win._dcs_editor._save_timer.isActive()

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win._on_delete_dcs()
    win._dcs_editor.flush()

    assert not path.exists()


def test_deleting_one_pcs_leaves_the_others_alone(qapp, store, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    a = PlayerCombatStrategy(name="A", dsl="a")
    b = PlayerCombatStrategy(name="B", dsl="b")
    save_player_strategy(a)
    save_player_strategy(b)

    win = StrategyLibraryWindow()
    win.focus_player_strategy(a.id)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win._on_delete_pcs()

    assert load_player_strategy(b.id).dsl == "b"


# ---------------------------------------------------------------------------
# flush_editors
# ---------------------------------------------------------------------------


def test_flush_editors_commits_both_tabs(qapp, store):
    pcs = PlayerCombatStrategy(name="A", dsl="original")
    dcs = DuelCombatStrategy(name="Duel A", min_players=1)
    save_player_strategy(pcs)
    save_duel_strategy(dcs)

    win = StrategyLibraryWindow()
    win.focus_player_strategy(pcs.id)
    win._pcs_editor._dsl_edit.setPlainText("edited")
    win.focus_duel_strategy(dcs.id)
    win._dcs_editor._min_spin.setValue(3)

    win.flush_editors()

    assert load_player_strategy(pcs.id).dsl == "edited"
    assert load_duel_strategy(dcs.id).min_players == 3


def test_flush_editors_is_safe_with_nothing_loaded(qapp, store):
    StrategyLibraryWindow().flush_editors()  # must not raise


# ---------------------------------------------------------------------------
# _find_item
# ---------------------------------------------------------------------------


def test_find_item_returns_the_matching_row(qapp, store):
    a = PlayerCombatStrategy(name="A", dsl="")
    save_player_strategy(a)
    win = StrategyLibraryWindow()
    assert win._find_item(win._pcs_list, a.id).data(_ID_ROLE) == a.id


def test_find_item_returns_none_for_a_missing_id(qapp, store):
    win = StrategyLibraryWindow()
    assert win._find_item(win._pcs_list, "nope") is None
