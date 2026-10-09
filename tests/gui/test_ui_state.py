"""``ui_state.json``: folds stored only where they depart from a default, and safe to lose."""

from __future__ import annotations

import json
import logging

from bartlebot.gui.ui_state import UI_STATE_FILE, UiState
from bartlebot.shared.vendor import product_dir

_DEFAULTS = {"quicksell": True, "autosave": True}


def test_a_missing_file_gives_every_default(tmp_path):
    state = UiState(tmp_path / UI_STATE_FILE)

    assert state.folds("settings_dialog", _DEFAULTS) == _DEFAULTS
    assert not (tmp_path / UI_STATE_FILE).exists()


def test_it_lives_in_the_products_own_directory():
    assert UiState().path == product_dir() / UI_STATE_FILE


def test_a_fold_against_its_default_is_remembered(tmp_path):
    path = tmp_path / UI_STATE_FILE
    UiState(path).set_collapsed("settings_dialog", "quicksell", False, default=True)

    assert UiState(path).folds("settings_dialog", _DEFAULTS) == {"quicksell": False, "autosave": True}


def test_only_departures_from_the_default_are_written(tmp_path):
    path = tmp_path / UI_STATE_FILE
    state = UiState(path)
    state.set_collapsed("settings_dialog", "quicksell", False, default=True)
    state.set_collapsed("settings_dialog", "autosave", True, default=True)

    assert json.loads(path.read_text(encoding="utf-8")) == {"folds": {"settings_dialog": {"quicksell": False}}}


def test_folding_back_to_the_default_removes_the_entry(tmp_path):
    path = tmp_path / UI_STATE_FILE
    state = UiState(path)
    state.set_collapsed("settings_dialog", "quicksell", False, default=True)
    state.set_collapsed("settings_dialog", "quicksell", True, default=True)

    assert json.loads(path.read_text(encoding="utf-8")) == {}


def test_a_corrupt_file_gives_defaults_rather_than_failing(tmp_path, caplog):
    path = tmp_path / UI_STATE_FILE
    path.write_text("{not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="bartlebot.gui.ui_state"):
        state = UiState(path)

    assert state.folds("settings_dialog", _DEFAULTS) == _DEFAULTS
    assert "not valid JSON" in caplog.text


def test_a_file_holding_the_wrong_shape_gives_defaults(tmp_path):
    path = tmp_path / UI_STATE_FILE
    path.write_text(json.dumps({"folds": {"settings_dialog": ["quicksell"]}}), encoding="utf-8")

    assert UiState(path).folds("settings_dialog", _DEFAULTS) == _DEFAULTS


def test_a_group_the_surface_no_longer_has_is_pruned(tmp_path):
    path = tmp_path / UI_STATE_FILE
    path.write_text(
        json.dumps({"folds": {"settings_dialog": {"quicksell": False, "retired": False}, "other": {"x": True}}}),
        encoding="utf-8",
    )

    folds = UiState(path).folds("settings_dialog", _DEFAULTS)

    assert folds == {"quicksell": False, "autosave": True}
    # Another surface's entries are not this one's to prune.
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "folds": {"settings_dialog": {"quicksell": False}, "other": {"x": True}}
    }


def test_a_failed_write_is_logged_and_not_raised(tmp_path, caplog):
    # A directory where the file should be: the replace cannot land.
    path = tmp_path / UI_STATE_FILE
    path.mkdir()

    with caplog.at_level(logging.WARNING, logger="bartlebot.gui.ui_state"):
        UiState(path).set_collapsed("settings_dialog", "quicksell", False, default=True)

    assert "will not be remembered" in caplog.text


def test_two_holders_do_not_write_over_each_others_entries(tmp_path):
    """Each Settings tab holds its own; a stale copy must not erase the other tab's fold."""
    path = tmp_path / UI_STATE_FILE
    general, bot = UiState(path), UiState(path)
    general.set_collapsed("settings_dialog", "autosave", False, default=True)
    bot.set_collapsed("settings_dialog", "quicksell", False, default=True)

    assert UiState(path).folds("settings_dialog", _DEFAULTS) == {"quicksell": False, "autosave": False}
