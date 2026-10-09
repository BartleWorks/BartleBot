"""Tests for non-GUI helper functions in the editor module."""

from bartlebot.gui.canvas.edit_scene import _unique_id


def test_unique_id_empty_list():
    assert _unique_id([]) == "state_1"


def test_unique_id_avoids_existing():
    assert _unique_id(["state_1"]) == "state_2"


def test_unique_id_does_not_fill_gaps():
    # state_2 is free, but recycling it would hand the deleted state_2's combat
    # assignment to this brand-new node.  Number above the highest id instead.
    assert _unique_id(["state_1", "state_3"]) == "state_4"


def test_unique_id_never_reissues_a_deleted_id():
    """Delete-then-add must not produce the id that was just freed.

    BotCombatStrategy.state_assignments stores raw state ids and resolve_combat
    looks the running state up by id, so a reissued id silently transfers a duel
    strategy to an unrelated node.
    """
    states = ["state_1", "state_2", "state_3"]
    states.remove("state_2")  # user deletes the middle node

    fresh = _unique_id(states)

    assert fresh == "state_4"
    assert fresh != "state_2"


def test_unique_id_sequential_existing():
    assert _unique_id(["state_1", "state_2", "state_3"]) == "state_4"


def test_unique_id_non_sequential_names_are_ignored():
    # Only "state_N" names are considered; other names don't block slot 1
    assert _unique_id(["go_to_dungeon", "my_state"]) == "state_1"


# ---------------------------------------------------------------------------
# Template drafts: the t_ prefix and the ids earlier releases reserved
# ---------------------------------------------------------------------------


def test_a_prefix_numbers_only_ids_carrying_it():
    assert _unique_id(["t_1", "t_4", "state_9"], prefix="t_") == "t_5"


def test_a_bot_ignores_template_owned_ids_when_it_numbers():
    """A bot made from a template keeps its t_ nodes and still counts state_N from its own."""
    assert _unique_id(["t_1", "t_12", "state_3"]) == "state_4"
    assert _unique_id(["t_1", "t_2"]) == "state_1"


def test_a_reserved_id_is_numbered_past_even_when_nothing_uses_it_now():
    """A node a released version had, and the draft has since deleted, still names a node in old bots."""
    assert _unique_id(["t_1"], prefix="t_", reserved=["t_1", "t_7"]) == "t_8"


def test_a_template_owned_instance_member_does_not_count_as_a_draft_node():
    assert _unique_id(["t_sync_players_1_state_9", "t_2"], prefix="t_") == "t_3"
