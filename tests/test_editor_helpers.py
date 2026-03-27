"""Tests for non-GUI helper functions in the editor module."""

from bartlebot.gui.canvas.edit_scene import _unique_id


def test_unique_id_empty_list():
    assert _unique_id([]) == "state_1"


def test_unique_id_avoids_existing():
    assert _unique_id(["state_1"]) == "state_2"


def test_unique_id_fills_gap():
    # state_1 and state_3 exist — should fill state_2
    assert _unique_id(["state_1", "state_3"]) == "state_2"


def test_unique_id_sequential_existing():
    assert _unique_id(["state_1", "state_2", "state_3"]) == "state_4"


def test_unique_id_non_sequential_names_are_ignored():
    # Only "state_N" names are considered; other names don't block slot 1
    assert _unique_id(["go_to_dungeon", "my_state"]) == "state_1"
