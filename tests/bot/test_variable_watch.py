"""LiveVariables: what the Run-mode Variables section shows, and how long a change stays marked."""

from __future__ import annotations

from bartlebot.bot.schema import VariableModel
from bartlebot.bot.variable_watch import LiveVariables

DECLS = [
    VariableModel(name="swords", type="int"),
    VariableModel(name="found", type="bool", initial=True),
    VariableModel(name="stage", type="enum", values=["Sun", "Moon"]),
]


def _live() -> LiveVariables:
    live = LiveVariables()
    live.reset(DECLS)
    return live


def test_before_a_run_it_holds_the_initial_values_and_marks_nothing():
    live = _live()
    assert live.values == {"swords": 0, "found": True, "stage": "Sun"}
    assert not any(live.is_marked(n) for n in live.values)


def test_a_write_changes_the_value_and_marks_it_with_what_it_was():
    live = _live()
    live.change("swords", 0, 1)
    assert live.value("swords") == 1
    assert live.is_marked("swords") and live.before("swords") == 0
    assert not live.is_marked("found") and live.before("found") is None


def test_the_mark_lasts_through_the_first_game_node_after_the_write():
    live = _live()
    live.change("swords", 0, 1)
    assert live.node_started("navigate_to_zone") == set()
    assert live.is_marked("swords"), "the node the write led to shows it"
    assert live.node_started("interact") == {"swords"}
    assert not live.is_marked("swords")
    assert live.value("swords") == 1, "clearing the mark keeps the value"


def test_variable_only_nodes_never_count_toward_clearing_it():
    live = _live()
    live.change("swords", 0, 1)
    for node_type in ("branch", "set_variables", "branch"):
        assert live.node_started(node_type) == set()
    live.node_started("interact")
    assert live.is_marked("swords")


def test_two_writes_before_any_game_node_are_one_change():
    live = _live()
    live.change("swords", 0, 1)
    live.node_started("branch")
    live.change("swords", 1, 5)
    assert live.before("swords") == 0


def test_a_write_after_the_change_was_shown_starts_a_new_mark():
    live = _live()
    live.change("swords", 0, 1)
    live.node_started("interact")
    live.change("swords", 1, 2)
    assert live.before("swords") == 1
    live.node_started("interact")
    assert live.is_marked("swords"), "the count starts again from the new write"


def test_a_reset_puts_back_the_initial_values_and_clears_every_mark():
    live = _live()
    live.change("swords", 0, 3)
    live.change("stage", "Sun", "Moon")
    live.reset(DECLS)
    assert live.values == {"swords": 0, "found": True, "stage": "Sun"}
    assert not live.is_marked("swords") and not live.is_marked("stage")


def test_a_name_declared_twice_starts_where_the_machine_starts_it():
    """Validation refuses the duplicate before a run; even so, the section never disagrees with the machine."""
    from bartlebot.bot.machine import BotMachine

    decls = [VariableModel(name="x", type="int", initial=2), VariableModel(name="x", type="bool")]
    config = {"initial": "a", "states": [{"id": "a", "node_type": "branch"}], "transitions": []}
    machine = BotMachine(clients=[], config={**config, "variables": [d.model_dump() for d in decls]}, node_registry={})
    live = LiveVariables()
    live.reset(decls)
    assert live.values == dict(machine.variables)
