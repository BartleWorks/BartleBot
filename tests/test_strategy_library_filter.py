"""Filtering a Strategy Library list must not disturb the editor beside it (#223).

``QListWidget.clear()`` emits ``currentItemChanged(None, previous)``, and
repopulating then emits it again for the reselect.  Both reached the selection
slot, so a single keystroke in a filter box hid and reloaded the editor, flushed
it to disk twice, and put the DSL caret back to position 0.

These are widget tests because the defect is entirely in the signal/slot wiring.
Like ``test_strategy_editor_identity.py``, they isolate the store by patching
``strategy_store.project_root``.
"""

import pytest

from bartlebot import strategy_store as store_mod
from bartlebot.bot.strategy_models import DeckPreset, DuelCombatStrategy, PlayerCombatStrategy
from bartlebot.gui.strategy_library import strategy_library_window as win_mod
from bartlebot.gui.strategy_library.strategy_library_window import StrategyLibraryWindow
from bartlebot.strategy_store import (
    save_deck_preset,
    save_duel_strategy,
    save_player_strategy,
)

_ID_ROLE = 0x100


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the strategy store's default root at an empty tmp_path for the test."""
    (tmp_path / "strategies" / "player").mkdir(parents=True)
    (tmp_path / "strategies" / "duel").mkdir(parents=True)
    (tmp_path / "strategies" / "decks").mkdir(parents=True)
    (tmp_path / "bot_strategies").mkdir(parents=True)
    monkeypatch.setattr(store_mod, "project_root", lambda: tmp_path)
    return tmp_path


def _count_calls(monkeypatch, obj, name):
    """Replace ``obj.name`` with a counting wrapper; return the call list."""
    calls = []
    original = getattr(obj, name)

    def wrapper(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(obj, name, wrapper)
    return calls


# ---------------------------------------------------------------------------
# Player Strategies
# ---------------------------------------------------------------------------


def test_typing_in_the_pcs_filter_leaves_the_dsl_caret_alone(qapp, store):
    """The repro from #223: one keystroke used to send the caret back to 0."""
    a = PlayerCombatStrategy(name="Alpha", dsl="any<damage> @ enemy")
    save_player_strategy(a)

    win = StrategyLibraryWindow()
    win.focus_player_strategy(a.id)

    cursor = win._pcs_editor._dsl_edit.textCursor()
    cursor.setPosition(5)
    win._pcs_editor._dsl_edit.setTextCursor(cursor)

    win._pcs_filter.setText("A")

    assert win._pcs_editor._dsl_edit.textCursor().position() == 5


def test_typing_in_the_pcs_filter_does_not_reload_or_hide_the_editor(qapp, store, monkeypatch):
    a = PlayerCombatStrategy(name="Alpha", dsl="original")
    save_player_strategy(a)

    win = StrategyLibraryWindow()
    win.focus_player_strategy(a.id)

    loads = _count_calls(monkeypatch, win._pcs_editor, "load")
    hides = _count_calls(monkeypatch, win._pcs_editor, "hide")

    for text in ("A", "Al", "Alp"):
        win._pcs_filter.setText(text)

    assert loads == []
    assert hides == []
    assert win._pcs_editor.isVisibleTo(win)


def test_typing_in_the_pcs_filter_does_not_commit_a_pending_edit(qapp, store, monkeypatch):
    """Filtering is read-only — it used to flush the editor twice per keystroke."""
    a = PlayerCombatStrategy(name="Alpha", dsl="original")
    save_player_strategy(a)

    win = StrategyLibraryWindow()
    win.focus_player_strategy(a.id)
    win._pcs_editor._dsl_edit.setPlainText("edited")

    saves = _count_calls(monkeypatch, win_mod, "save_player_strategy")
    win._pcs_filter.setText("A")

    assert saves == []
    assert win._pcs_editor._save_timer.isActive()  # still pending, not written early
    assert store_mod.load_player_strategy(a.id).dsl == "original"


def test_filtering_the_selected_pcs_out_of_view_keeps_it_in_the_editor(qapp, store):
    a = PlayerCombatStrategy(name="Alpha", dsl="original")
    save_player_strategy(a)

    win = StrategyLibraryWindow()
    win.focus_player_strategy(a.id)

    win._pcs_filter.setText("zzz-no-match")

    assert win._find_item(win._pcs_list, a.id) is None
    assert win._pcs_editor.isVisibleTo(win)
    assert win._pcs_editor._dsl_edit.toPlainText() == "original"


def test_clearing_the_pcs_filter_reselects_the_edited_row(qapp, store):
    a = PlayerCombatStrategy(name="Alpha", dsl="original")
    save_player_strategy(a)

    win = StrategyLibraryWindow()
    win.focus_player_strategy(a.id)
    win._pcs_filter.setText("zzz-no-match")
    win._pcs_filter.setText("")

    assert win._pcs_list.currentItem().data(_ID_ROLE) == a.id


def test_edits_made_while_the_pcs_row_is_filtered_out_still_save(qapp, store):
    a = PlayerCombatStrategy(name="Alpha", dsl="original")
    save_player_strategy(a)

    win = StrategyLibraryWindow()
    win.focus_player_strategy(a.id)
    win._pcs_filter.setText("zzz-no-match")
    win._pcs_editor._dsl_edit.setPlainText("edited")
    win._pcs_editor.flush()

    assert store_mod.load_player_strategy(a.id).dsl == "edited"


def test_selecting_another_pcs_still_loads_it(qapp, store):
    """The rebuild guard must not swallow a genuine selection change."""
    a = PlayerCombatStrategy(name="Alpha", dsl="a-dsl")
    b = PlayerCombatStrategy(name="Beta", dsl="b-dsl")
    save_player_strategy(a)
    save_player_strategy(b)

    win = StrategyLibraryWindow()
    win.focus_player_strategy(a.id)
    win.focus_player_strategy(b.id)

    assert win._pcs_editor._dsl_edit.toPlainText() == "b-dsl"


def test_deleting_the_selected_pcs_shows_the_empty_label(qapp, store, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    a = PlayerCombatStrategy(name="Alpha", dsl="")
    save_player_strategy(a)

    win = StrategyLibraryWindow()
    win.focus_player_strategy(a.id)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win._on_delete_pcs()

    assert not win._pcs_editor.isVisibleTo(win)
    assert win._pcs_empty_lbl.isVisibleTo(win)


def test_refresh_repoints_the_editor_at_the_reloaded_strategy(qapp, store):
    """``refresh()`` re-reads from disk, so the editor must follow the new object."""
    a = PlayerCombatStrategy(name="Alpha", dsl="original")
    save_player_strategy(a)

    win = StrategyLibraryWindow()
    win.focus_player_strategy(a.id)

    a.dsl = "changed-elsewhere"
    save_player_strategy(a)
    win.refresh()

    assert win._pcs_editor._dsl_edit.toPlainText() == "changed-elsewhere"
    assert win._pcs_editor._strategy is win._all_pcs[0]


# ---------------------------------------------------------------------------
# Duel Strategies
# ---------------------------------------------------------------------------


def test_typing_in_the_dcs_filter_does_not_reload_or_flush_the_editor(qapp, store, monkeypatch):
    d = DuelCombatStrategy(name="Duel Alpha", min_players=1)
    save_duel_strategy(d)

    win = StrategyLibraryWindow()
    win.focus_duel_strategy(d.id)

    loads = _count_calls(monkeypatch, win._dcs_editor, "load")
    saves = _count_calls(monkeypatch, win_mod, "save_duel_strategy")

    win._dcs_filter.setText("D")

    assert loads == []
    assert saves == []
    assert win._dcs_editor.isVisibleTo(win._dcs_tab)


def test_filtering_the_selected_dcs_out_of_view_keeps_the_use_button_enabled(qapp, store):
    """Pick mode: hiding the row is not a deselection, so "Use selected" stays live."""
    d = DuelCombatStrategy(name="Duel Alpha")
    save_duel_strategy(d)

    win = StrategyLibraryWindow(mode="pick", current_id=d.id)
    assert win._use_btn.isEnabled()

    win._dcs_filter.setText("zzz-no-match")

    assert win._use_btn.isEnabled()
    win._on_use()
    assert win.selected_id() == d.id


def test_pick_mode_use_is_a_noop_with_nothing_selected(qapp, store):
    save_duel_strategy(DuelCombatStrategy(name="Duel Alpha"))
    win = StrategyLibraryWindow(mode="pick")

    assert not win._use_btn.isEnabled()
    win._on_use()  # must not raise or accept

    assert win.result() != win.DialogCode.Accepted.value


# ---------------------------------------------------------------------------
# Deck Presets
# ---------------------------------------------------------------------------


def test_typing_in_the_decks_filter_does_not_reload_the_detail_panel(qapp, store, monkeypatch):
    preset = DeckPreset(name="Alpha Deck")
    save_deck_preset(preset)

    win = StrategyLibraryWindow()
    win.focus_deck_preset(preset.id)

    loads = _count_calls(monkeypatch, win._deck_detail, "load")
    win._decks_filter.setText("A")

    assert loads == []
    assert win._deck_detail.isVisibleTo(win)


def test_renaming_a_deck_relabels_the_row_without_reloading_the_panel(qapp, store, monkeypatch):
    """The rename comes from the panel's own name box — reloading it would move the caret."""
    preset = DeckPreset(name="Alpha Deck")
    save_deck_preset(preset)

    win = StrategyLibraryWindow()
    win.focus_deck_preset(preset.id)

    loads = _count_calls(monkeypatch, win._deck_detail, "load")
    win._on_deck_renamed("Renamed Deck")

    assert loads == []
    assert win._find_item(win._decks_list, preset.id).text().startswith("Renamed Deck")
    assert store_mod.load_deck_preset(preset.id).name == "Renamed Deck"


def test_renaming_a_deck_with_nothing_selected_is_a_noop(qapp, store):
    save_deck_preset(DeckPreset(name="Alpha Deck"))
    win = StrategyLibraryWindow()

    win._on_deck_renamed("Renamed Deck")  # must not raise
