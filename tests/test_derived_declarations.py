"""An option a node requires is composed at load, not stored in the bot file.

The bug this closes is visible in the library: 61 bots carry a ``Challenge Mode``
declaration because they contain ``enter_dungeon``, and only two can act on it;
26 carry a description no current NodeSpec produces, because injection adds a
missing name and never refreshes what it added.

So the file keeps only what is authorial -- a hand-added option, or a required
one whose default the author deliberately changed -- and everything else is
derived from ``NODE_SPECS``. These tests pin down the three halves of that: what
the composed list contains, what survives a save, and what happens to a
transition guarding on an option nothing declares any more.

Written against the real NodeSpecs rather than fakes, because the predicate is
about agreeing with them: ``interact`` requires Safety Delay (float, 3.0) and
``enter_dungeon`` also requires Challenge Mode (bool, False).
"""

from __future__ import annotations

import json

from bartlebot.bot.schema import BotConfig
from bartlebot.bot_options import (
    compose_options,
    orphaned_flag_options,
    storable_options,
)


def _config(**overrides) -> BotConfig:
    data = {
        "id": "test-bot",
        "name": "Test Bot",
        "initial": "s0",
        "recovery_state": "s0",
        "states": [{"id": "s0", "node_type": "enter_dungeon"}],
        "transitions": [],
    }
    data.update(overrides)
    return BotConfig.model_validate(data)


def _named(options) -> dict:
    return {o.name: o for o in options}


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def test_a_required_option_is_composed_from_its_node_spec():
    composed = _named(compose_options(_config()))

    assert composed["Safety Delay"].type == "float"
    assert composed["Safety Delay"].default == 3.0
    assert composed["Challenge Mode"].default is False
    assert composed["Safety Delay"].description  # the NodeSpec's, not a stored copy


def test_composing_writes_nothing_back_to_the_file():
    config = _config()

    compose_options(config)

    assert config.options == []


def test_an_option_no_node_in_the_graph_requires_is_not_composed():
    config = _config(states=[{"id": "s0", "node_type": "interact"}])

    assert "Challenge Mode" not in _named(compose_options(config))


def test_a_hand_added_option_is_composed_as_the_author_wrote_it():
    config = _config(options=[{"name": "Skip to boss", "type": "bool", "default": True}])

    composed = _named(compose_options(config))

    assert composed["Skip to boss"].default is True
    assert "Safety Delay" in composed


def test_a_stored_pin_wins_over_the_node_default_in_the_composed_list():
    config = _config(options=[{"name": "Safety Delay", "type": "float", "default": 5.0}])

    assert _named(compose_options(config))["Safety Delay"].default == 5.0


def test_a_stored_description_never_survives_composition():
    """26 bots ship a description no NodeSpec produces. A NodeSpec fix has to
    reach them, which it cannot do while the file's stale copy is what shows."""
    config = _config(
        options=[{"name": "Safety Delay", "type": "float", "default": 3.0, "description": "Seconds to wait. (stale)"}]
    )

    assert "stale" not in (_named(compose_options(config))["Safety Delay"].description or "")


def test_a_system_option_no_node_in_the_graph_reads_is_dropped_from_the_composed_list():
    config = _config(
        states=[{"id": "s0", "node_type": "interact"}],
        options=[{"name": "Challenge Mode", "type": "bool", "default": True}],
    )

    assert "Challenge Mode" not in _named(compose_options(config))


# ---------------------------------------------------------------------------
# The save-time prune
# ---------------------------------------------------------------------------


def test_a_declaration_matching_its_node_spec_is_not_stored():
    config = _config(options=[{"name": "Challenge Mode", "type": "bool", "default": False}])

    assert storable_options(config) == []


def test_a_declaration_the_author_changed_is_stored():
    config = _config(options=[{"name": "Challenge Mode", "type": "bool", "default": True}])

    kept = storable_options(config)

    assert [(o.name, o.default) for o in kept] == [("Challenge Mode", True)]


def test_a_stored_pin_keeps_the_node_spec_type_and_no_description():
    """Type and description are never authorial for a derived option: only 66 of
    97 stored declarations in the library matched a NodeSpec byte for byte, and
    the other 31 differ only by carrying an injection nobody refreshed."""
    config = _config(
        options=[{"name": "Safety Delay", "type": "int", "default": 5.0, "description": "something obsolete"}]
    )

    kept = storable_options(config)[0]

    assert kept.type == "float"
    assert kept.description is None


def test_an_int_pin_matching_a_float_node_default_is_not_stored():
    """``enter_dungeon`` shipped Safety Delay as ``3`` for a while, so the
    library holds one file with a bare ``3``. JSON cannot tell it from ``3.0``."""
    config = _config(options=[{"name": "Safety Delay", "type": "float", "default": 3}])

    assert storable_options(config) == []


def test_a_false_pin_against_a_numeric_node_default_is_stored():
    """``False == 0`` in Python, and collapsing the two would drop a real answer."""
    config = _config(options=[{"name": "Safety Delay", "type": "float", "default": False}])

    assert [o.default for o in storable_options(config)] == [False]


def test_a_hand_added_option_survives_the_prune_untouched():
    config = _config(options=[{"name": "Skip to boss", "type": "bool", "default": False, "description": "mine"}])

    kept = storable_options(config)

    assert [(o.name, o.default, o.description) for o in kept] == [("Skip to boss", False, "mine")]


def test_a_system_option_no_node_reads_is_pruned():
    config = _config(
        states=[{"id": "s0", "node_type": "interact"}],
        options=[{"name": "Challenge Mode", "type": "bool", "default": True}],
    )

    assert storable_options(config) == []


def test_saving_drops_the_derived_declarations(tmp_path):
    config = _config(
        options=[
            {"name": "Safety Delay", "type": "float", "default": 3.0},
            {"name": "Challenge Mode", "type": "bool", "default": False},
            {"name": "Skip to boss", "type": "bool", "default": True},
        ]
    )
    path = tmp_path / "bot.json"

    config.save(path)

    with open(path, encoding="utf-8") as f:
        assert [o["name"] for o in json.load(f)["options"]] == ["Skip to boss"]


def test_saving_does_not_disturb_the_config_in_memory(tmp_path):
    """A save is not an edit -- the editor is still showing what it was."""
    config = _config(options=[{"name": "Safety Delay", "type": "float", "default": 3.0}])

    config.save(tmp_path / "bot.json")

    assert [o.name for o in config.options] == ["Safety Delay"]


def test_a_bot_with_enter_dungeon_does_not_gain_challenge_mode_on_a_round_trip(tmp_path):
    """#159, as a check: 61 bots carried this declaration and 2 could use it."""
    path = tmp_path / "bot.json"
    _config().save(path)

    reloaded = BotConfig.load(path)
    reloaded.save(path)

    with open(path, encoding="utf-8") as f:
        assert json.load(f)["options"] == []


# ---------------------------------------------------------------------------
# Orphaned flag references
# ---------------------------------------------------------------------------


def test_a_flag_reading_a_declared_option_is_not_an_orphan():
    config = _config(
        transitions=[
            {
                "trigger": "advance",
                "source": "s0",
                "dest": "s0",
                "flags": [{"option": "Challenge Mode"}],
            }
        ]
    )

    assert orphaned_flag_options(config) == []


def test_a_flag_reading_an_undeclared_option_is_reported():
    """``_flag_<name>`` resolves to False rather than erroring, so without this
    the bot simply stops taking the edge, hours into a run."""
    config = _config(
        transitions=[
            {"trigger": "advance", "source": "s0", "dest": "s0", "flags": [{"option": "Skip to boss"}]},
        ]
    )

    assert [name for _t, name in orphaned_flag_options(config)] == ["Skip to boss"]


def test_a_grouped_flag_is_walked_too():
    """Grouped flags take precedence at run time; walking only the flat list is
    the pre-existing bug in the rename handler, and is not repeated here."""
    config = _config(
        transitions=[
            {
                "trigger": "advance",
                "source": "s0",
                "dest": "s0",
                "flag_groups": [{"flags": [{"option": "Ghost option"}], "op": "AND"}],
            }
        ]
    )

    assert [name for _t, name in orphaned_flag_options(config)] == ["Ghost option"]


def test_a_hand_added_option_declares_its_own_flag():
    config = _config(
        options=[{"name": "Skip to boss", "type": "bool", "default": False}],
        transitions=[
            {"trigger": "advance", "source": "s0", "dest": "s0", "flags": [{"option": "Skip to boss"}]},
        ],
    )

    assert orphaned_flag_options(config) == []


def test_cutting_the_node_that_declared_an_option_orphans_its_flag():
    config = _config(
        states=[{"id": "s0", "node_type": "interact"}],
        transitions=[
            {"trigger": "advance", "source": "s0", "dest": "s0", "flags": [{"option": "Challenge Mode"}]},
        ],
    )

    assert [name for _t, name in orphaned_flag_options(config)] == ["Challenge Mode"]
