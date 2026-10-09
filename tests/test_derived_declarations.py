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
``enter_dungeon`` also requires Challenge Mode (bool, False), but only while its
``offers_challenge_mode`` param is on.
"""

from __future__ import annotations

import json

from bartlebot.bot.schema import BotConfig, DropModel, StateModel
from bartlebot.options.bot_options import (
    compose_options,
    options_signature,
    orphaned_flag_options,
    storable_options,
)


def _config(**overrides) -> BotConfig:
    data = {
        "id": "test-bot",
        "name": "Test Bot",
        "initial": "s0",
        "recovery_state": "s0",
        "states": [{"id": "s0", "node_type": "enter_dungeon", "params": {"offers_challenge_mode": True}}],
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


def test_an_option_the_author_has_not_offered_is_not_composed():
    """Challenge Mode exists in a handful of dungeons and nowhere else, and only the
    author knows which theirs is. Composing it unconditionally put a switch the user
    could not act on into all sixty-odd bots with a sigil in them."""
    config = _config(states=[{"id": "s0", "node_type": "enter_dungeon", "params": {}}])

    composed = _named(compose_options(config))

    assert "Challenge Mode" not in composed
    assert "Safety Delay" in composed, "the node's unconditional options are unaffected"


def test_one_state_offering_it_is_enough():
    """A bot can enter two dungeons; the option is bot-wide, so any state that can use
    it makes it worth offering."""
    config = _config(
        states=[
            {"id": "s0", "node_type": "enter_dungeon", "params": {}},
            {"id": "s1", "node_type": "enter_dungeon", "params": {"offers_challenge_mode": True}},
        ]
    )

    assert "Challenge Mode" in _named(compose_options(config))


def test_an_offered_option_still_defaults_to_off():
    """The author says it is available, not that it is wanted -- that stays the user's,
    and the author may still pin a default by declaring one."""
    assert _named(compose_options(_config()))["Challenge Mode"].default is False


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
# Order
# ---------------------------------------------------------------------------


def _order(config) -> list[str]:
    return [o.name for o in compose_options(config)]


def test_a_derived_option_keeps_its_place_when_its_declaration_is_materialised():
    """Editing a derived option's default writes a declaration into the file, and the
    file's own options come first -- so an order that followed the file moved the row
    the user had just touched to the top of the list, under the pointer."""
    config = _config(states=[{"id": "s0", "node_type": "quicksell"}])
    before = _order(config)

    from bartlebot.bot.schema import BotOptionModel  # noqa: PLC0415

    config.options.append(BotOptionModel(name="Sell Pets", type="bool", default=False))

    assert _order(config) == before


def test_the_options_of_one_node_stay_together():
    """quicksell requires five; a declaration of one of them used to pull it out of the
    group and leave enter_dungeon's Challenge Mode threaded through the rest."""
    from bartlebot.bot.schema import BotOptionModel  # noqa: PLC0415

    config = _config(
        states=[
            {"id": "s0", "node_type": "enter_dungeon", "params": {"offers_challenge_mode": True}},
            {"id": "s1", "node_type": "quicksell"},
        ],
        options=[{"name": "Skip to boss", "type": "bool", "default": False}],
    )
    config.options.append(BotOptionModel(name="Sell Pets", type="bool", default=False))

    order = _order(config)
    quicksell = [i for i, name in enumerate(order) if name.startswith(("Sell ", "Enable Quicksell", "Quicksell "))]

    assert order[0] == "Skip to boss", "a hand-added option is the author's own and comes first"
    assert quicksell == list(range(min(quicksell), max(quicksell) + 1))
    assert order.index("Challenge Mode") < min(quicksell)


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
    """The Challenge Mode opt-in, as a check: 61 bots carried this declaration and 2 could use it."""
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


# ---------------------------------------------------------------------------
# Parentage
# ---------------------------------------------------------------------------


def _quicksell_config(**overrides) -> BotConfig:
    return _config(states=[{"id": "s0", "node_type": "quicksell", "params": {}}], **overrides)


def test_a_child_option_is_composed_with_its_parent():
    """``depends_on`` rides the composed declaration exactly as type and bounds do:
    derived from the NodeSpec, never written to the file."""
    from bartlebot.nodes.utility import quicksell as qs

    composed = _named(compose_options(_quicksell_config()))

    assert composed[qs.SELL_FLAG_OPTIONS["sell_pets"]].depends_on == qs.OPTION_ENABLED
    assert composed[qs.OPTION_ENABLED].depends_on is None


def test_a_child_option_is_not_persisted_as_one():
    """Same reason the other three derived fields are not: a stored copy is one more
    thing to go stale, and the composed one is refreshed on every load."""
    from bartlebot.nodes.utility import quicksell as qs

    config = _quicksell_config()
    config.options = [o for o in compose_options(config) if o.name == qs.SELL_FLAG_OPTIONS["sell_pets"]]
    config.options[0].default = False  # authorial, so the declaration survives the prune

    kept = _named(storable_options(config))

    assert kept[qs.SELL_FLAG_OPTIONS["sell_pets"]].depends_on is None


def test_a_child_whose_parent_is_not_in_the_graph_is_not_marked_as_one():
    """A row indented under a row that is not there reads as a child of whatever
    precedes it, so the link is dropped rather than left dangling."""
    from bartlebot.nodes.utility import quicksell as qs

    specs = {"quicksell": _spec_without(qs.OPTION_ENABLED)}
    config = _quicksell_config()

    composed = _named(compose_options(config, specs=specs))

    assert composed[qs.SELL_FLAG_OPTIONS["sell_pets"]].depends_on is None


def _spec_without(option_name: str):
    """The quicksell NodeSpec with one of its options removed."""
    import copy

    from bartlebot.nodes.registry import NODE_SPECS

    spec = copy.deepcopy(NODE_SPECS["quicksell"])
    spec.required_bot_options = [o for o in spec.required_bot_options if o.name != option_name]
    return spec


# ---------------------------------------------------------------------------
# The signature the option surfaces skip a rebuild on
# ---------------------------------------------------------------------------


def test_an_edit_that_touches_no_option_leaves_the_signature_alone():
    """The common graph edit: a node that declares nothing, moved or added.  Building
    the rows costs ~55ms on a large bot and composing them costs 0.05ms, so this is
    what makes the ordinary edit free."""
    config = _config()
    before = options_signature(config)

    config.states.append(StateModel(id="s1", node_type="sleep", params={}))

    assert options_signature(config) == before


def test_adding_a_node_that_requires_an_option_moves_the_signature():
    config = _config(states=[{"id": "s0", "node_type": "sleep"}])
    before = options_signature(config)

    config.states.append(StateModel(id="s1", node_type="interact", params={}))

    assert options_signature(config) != before


def test_turning_a_conditional_option_off_moves_the_signature():
    """Challenge Mode is required only while ``offers_challenge_mode`` is on, so the
    param edit that drops it is a graph edit whose row list genuinely changed."""
    config = _config()
    before = options_signature(config)

    config.states[0].params["offers_challenge_mode"] = False

    assert options_signature(config) != before


def _teleport_states(*modes) -> list[dict]:
    """One teleport_to_friend state per mode: True/False for the param, None for absent."""
    return [
        {
            "id": f"s{i}",
            "node_type": "teleport_to_friend",
            "params": {} if mode is None else {"target_hooked_player": mode},
        }
        for i, mode in enumerate(modes)
    ]


def test_friend_name_is_not_composed_when_every_port_is_to_a_hooked_player():
    """A hooked-player port never reads Friend Name, so Option Values has no row for it."""
    config = _config(states=_teleport_states(True, True))

    assert "Friend Name" not in _named(compose_options(config))


def test_one_friend_name_port_keeps_friend_name_for_the_whole_bot():
    """Hiding the row because *some* node ports to a hooked player would leave the one
    that ports by name with nothing to read."""
    config = _config(states=_teleport_states(True, False))

    assert "Friend Name" in _named(compose_options(config))


def test_a_file_without_the_mode_param_still_composes_friend_name():
    """``resolve_target`` treats an absent param as friend-name mode, for files written
    before the hooked-player mode existed -- so their option has to stay."""
    config = _config(states=_teleport_states(None))

    assert "Friend Name" in _named(compose_options(config))


def test_switching_a_port_to_a_hooked_player_moves_the_signature():
    config = _config(states=_teleport_states(False))
    before = options_signature(config)

    config.states[0].params["target_hooked_player"] = True

    assert options_signature(config) != before


def test_a_new_id_moves_the_signature():
    """Save As mints one under a loaded bot, and Option Values resolves the user's stored
    values through it -- so those rows have to be re-resolved."""
    config = _config()
    before = options_signature(config)

    config.id = "forked-bot"

    assert options_signature(config) != before


def test_a_changed_drop_table_moves_the_signature():
    """The quicksell keep list is picked from it, so a row is built from it."""
    config = _config()
    before = options_signature(config)

    config.drops.append(DropModel(name="Sun Serpent"))

    assert options_signature(config) != before


def test_a_declaration_losing_its_node_moves_the_signature():
    """The composed list can hold the same names either way -- what changes is that
    the option is no longer *derived*, which is what gives its row an Edit button."""
    config = _config()
    config.options = list(compose_options(config))
    before = options_signature(config)

    config.states.clear()

    assert options_signature(config) != before
