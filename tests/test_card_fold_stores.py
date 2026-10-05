"""Where a surface's card folds are kept: ``settings.json`` for the option surfaces, ``ui_state.json`` for the node pages (#142)."""

from PyQt6.QtCore import Qt

from bartlebot.gui.sidebar.option_card import CardFolds, CardHeadingActions, OptionCard, UiStateFoldStore
from bartlebot.ui_state import UiState


def _store(tmp_path, defaults):
    return UiStateFoldStore("node_page", defaults, UiState(tmp_path / "ui_state.json"))


def test_a_fold_matching_its_default_stores_nothing(tmp_path):
    store = _store(tmp_path, {"params": False, "notes": True})
    store.write({"params": False, "notes": True}, {"notes": True})
    assert not (tmp_path / "ui_state.json").exists()


def test_a_departure_is_stored_and_read_back(tmp_path):
    store = _store(tmp_path, {"params": False, "notes": True})
    store.write({"params": True, "notes": True}, {"params": True})
    assert _store(tmp_path, {"params": False, "notes": True}).read() == {"params": True, "notes": True}


def test_a_default_that_moves_reaches_a_group_the_reader_never_touched(tmp_path):
    # The subgraph node page's case: an incomplete card starts open, a complete one folded.
    _store(tmp_path, {"enter": False, "sync": False}).write({"enter": True, "sync": False}, {"enter": True})
    assert _store(tmp_path, {"enter": True, "sync": True}).read() == {"enter": True, "sync": True}


def test_card_folds_writes_a_click_through_its_store(qapp, tmp_path):
    actions = CardHeadingActions()
    folds = CardFolds(actions, store=_store(tmp_path, {"params": False, "notes": True}))
    cards = {"params": OptionCard("params", "Parameters"), "notes": OptionCard("notes", "Notes")}
    folds.set_cards(cards)
    assert not cards["params"].is_collapsed
    assert cards["notes"].is_collapsed
    cards["params"].request_fold(Qt.KeyboardModifier.NoModifier)
    assert cards["params"].is_collapsed
    assert UiState(tmp_path / "ui_state.json").folds("node_page", {"params": False}) == {"params": True}


def test_the_heading_row_draws_its_actions_without_a_heading_to_hover(qapp, tmp_path):
    actions = CardHeadingActions()
    folds = CardFolds(actions, store=_store(tmp_path, {"params": False}))
    assert folds.collapse_all_button.revealed
    assert folds.expand_all_button.revealed


def test_building_the_heading_row_never_shows_a_parentless_widget(qapp, tmp_path, parentless_shows):
    actions = CardHeadingActions()
    CardFolds(actions, store=_store(tmp_path, {"params": False}))
    assert parentless_shows == []
