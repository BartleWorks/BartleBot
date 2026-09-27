"""Tests for bartlebot.bot.strategy_models."""

import json
from pathlib import Path

import pytest

from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DeckData,
    DeckPreset,
    DuelCombatStrategy,
    DuelConfig,
    PlayerAssignment,
    PlayerCombatStrategy,
    StateAssignment,
)

# ---------------------------------------------------------------------------
# PlayerCombatStrategy
# ---------------------------------------------------------------------------


def test_pcs_auto_uuid():
    pcs = PlayerCombatStrategy(name="Test", dsl="some dsl")
    assert len(pcs.id) == 36  # UUID4 format
    assert pcs.dsl == "some dsl"


def test_pcs_save_load_roundtrip(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Blade", dsl="trap @ self")
    path = tmp_path / f"{pcs.id}.json"
    pcs.save(path)
    assert path.exists()
    loaded = PlayerCombatStrategy.load(path)
    assert loaded.id == pcs.id
    assert loaded.name == pcs.name
    assert loaded.dsl == pcs.dsl


def test_pcs_save_atomic(tmp_path: Path):
    """save() creates parent directories and writes valid JSON."""
    pcs = PlayerCombatStrategy(name="X", dsl="y")
    path = tmp_path / "sub" / "dir" / f"{pcs.id}.json"
    pcs.save(path)
    with open(path) as f:
        data = json.load(f)
    assert data["name"] == "X"


# ---------------------------------------------------------------------------
# DeckData
# ---------------------------------------------------------------------------


def test_deck_data_is_empty():
    assert DeckData().is_empty()
    assert not DeckData(normal={"Fireball": 1}).is_empty()


def test_deck_data_total_cards():
    """Normal and item cards count; tc is legacy and never applied."""
    d = DeckData(normal={"Fireball": 4}, tc={"Feint": 2}, item={"Reshuffle": 1})
    assert d.total_cards() == 5


def test_deck_data_item_only_is_not_empty():
    assert not DeckData(item={"Reshuffle": 1}).is_empty()


def test_deck_data_treasure_only_is_empty():
    """A deck holding only unrestorable treasure cards counts as empty."""
    assert DeckData(tc={"Feint": 3}).is_empty()


# ---------------------------------------------------------------------------
# DuelCombatStrategy
# ---------------------------------------------------------------------------


def test_dcs_resolve_dsl_for_player():
    pcs_id_a = "aaa"
    pcs_id_b = "bbb"
    pcs_map = {"aaa": "dsl_a", "bbb": "dsl_b"}
    dcs = DuelCombatStrategy(
        name="Test",
        player_assignments=[
            PlayerAssignment(players=[1, 2], player_combat_strategy_id=pcs_id_a),
            PlayerAssignment(players=[3, 4], player_combat_strategy_id=pcs_id_b),
        ],
    )
    assert dcs.resolve_dsl_for_player(1, pcs_map) == "dsl_a"
    assert dcs.resolve_dsl_for_player(2, pcs_map) == "dsl_a"
    assert dcs.resolve_dsl_for_player(3, pcs_map) == "dsl_b"
    assert dcs.resolve_dsl_for_player(5, pcs_map) is None  # unassigned


def test_dcs_resolve_inline_dsl():
    """Inline assignments resolve from their own dsl, with no library lookup."""
    dcs = DuelCombatStrategy(
        name="Inline",
        player_assignments=[
            PlayerAssignment(players=[1, 2], dsl="inline_a"),
            PlayerAssignment(players=[3], dsl="inline_b"),
        ],
    )
    assert dcs.resolve_dsl_for_player(1, {}) == "inline_a"
    assert dcs.resolve_dsl_for_player(2, {}) == "inline_a"
    assert dcs.resolve_dsl_for_player(3, {}) == "inline_b"
    assert dcs.resolve_dsl_for_player(4, {}) is None  # unassigned


def test_dcs_resolve_empty_inline_dsl_is_none():
    """An empty inline DSL falls through to the caller's built-in default."""
    dcs = DuelCombatStrategy(name="Empty", player_assignments=[PlayerAssignment(players=[1], dsl="")])
    assert dcs.resolve_dsl_for_player(1, {}) is None


def test_dcs_resolve_link_wins_over_inline_dsl():
    """When a preset is linked, the stale inline dsl is ignored."""
    dcs = DuelCombatStrategy(
        name="Mixed",
        player_assignments=[
            PlayerAssignment(players=[1], dsl="stale", player_combat_strategy_id="pcs-1"),
            PlayerAssignment(players=[2], dsl="inline"),
        ],
    )
    assert dcs.resolve_dsl_for_player(1, {"pcs-1": "from_preset"}) == "from_preset"
    assert dcs.resolve_dsl_for_player(2, {"pcs-1": "from_preset"}) == "inline"


def test_assignment_is_linked():
    assert PlayerAssignment(players=[1], dsl="x").is_linked is False
    assert PlayerAssignment(players=[1], player_combat_strategy_id="pcs-1").is_linked is True


def test_dcs_linked_pcs_ids_skips_inline():
    dcs = DuelCombatStrategy(
        name="Mixed",
        player_assignments=[
            PlayerAssignment(players=[1], dsl="inline"),
            PlayerAssignment(players=[2], player_combat_strategy_id="pcs-1"),
            PlayerAssignment(players=[3], player_combat_strategy_id="pcs-2"),
        ],
    )
    assert dcs.linked_pcs_ids() == ["pcs-1", "pcs-2"]


def test_dcs_loads_legacy_assignment_without_dsl(tmp_path: Path):
    """Pre-migration files have no 'dsl' key and a required PCS id — still parse."""
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps(
            {
                "id": "dcs-legacy",
                "name": "Legacy",
                "player_assignments": [{"players": [1], "player_combat_strategy_id": "pcs-1"}],
            }
        ),
        encoding="utf-8",
    )
    loaded = DuelCombatStrategy.load(path)
    assignment = loaded.player_assignments[0]
    assert assignment.is_linked is True
    assert assignment.dsl == ""
    assert loaded.resolve_dsl_for_player(1, {"pcs-1": "legacy_dsl"}) == "legacy_dsl"


def test_dcs_save_load_roundtrip(tmp_path: Path):
    dcs = DuelCombatStrategy(
        name="Boss Fight",
        min_players=2,
        max_players=4,
        combat_order=[4, 3, 2, 1],
        player_assignments=[
            PlayerAssignment(players=[1], player_combat_strategy_id="pcs-1"),
        ],
    )
    path = tmp_path / f"{dcs.id}.json"
    dcs.save(path)
    loaded = DuelCombatStrategy.load(path)
    assert loaded.id == dcs.id
    assert loaded.combat_order == [4, 3, 2, 1]
    assert len(loaded.player_assignments) == 1
    assert loaded.player_assignments[0].players == [1]


def test_dcs_simultaneous_combat_order_none(tmp_path: Path):
    """combat_order=None (simultaneous) survives a round-trip."""
    dcs = DuelCombatStrategy(name="X", combat_order=None)
    path = tmp_path / f"{dcs.id}.json"
    dcs.save(path)
    loaded = DuelCombatStrategy.load(path)
    assert loaded.combat_order is None


# ---------------------------------------------------------------------------
# BotCombatStrategy
# ---------------------------------------------------------------------------


def test_bcs_find_dcs_id_for_state():
    bcs = BotCombatStrategy(
        name="Farm",
        bot_id="bot-123",
        state_assignments=[
            StateAssignment(duel_combat_strategy_id="dcs-1", state_ids=["foulgaze", "jade_oni"]),
            StateAssignment(duel_combat_strategy_id="dcs-2", state_ids=["darkmoor_boss"]),
        ],
    )
    assert bcs.find_dcs_id_for_state("foulgaze") == "dcs-1"
    assert bcs.find_dcs_id_for_state("jade_oni") == "dcs-1"
    assert bcs.find_dcs_id_for_state("darkmoor_boss") == "dcs-2"
    assert bcs.find_dcs_id_for_state("unknown_state") is None


def test_bcs_save_load_roundtrip(tmp_path: Path):
    bcs = BotCombatStrategy(
        name="Storm Team",
        bot_id="bot-456",
        player_decks={1: DeckData(normal={"Tempest": 7}), 2: DeckData()},
        default_strategy_id="dcs-default",
        state_assignments=[
            StateAssignment(duel_combat_strategy_id="dcs-1", state_ids=["boss1"]),
        ],
    )
    path = tmp_path / f"{bcs.id}.json"
    bcs.save(path)
    loaded = BotCombatStrategy.load(path)
    assert loaded.id == bcs.id
    assert loaded.bot_id == "bot-456"
    assert loaded.default_strategy_id == "dcs-default"
    assert loaded.player_decks[1].normal == {"Tempest": 7}
    assert len(loaded.state_assignments) == 1


def test_bcs_auto_uuid_distinct():
    b1 = BotCombatStrategy(name="A", bot_id="x")
    b2 = BotCombatStrategy(name="B", bot_id="x")
    assert b1.id != b2.id


# ---------------------------------------------------------------------------
# DuelConfig player range
# ---------------------------------------------------------------------------


def test_duel_config_repairs_an_inverted_player_range():
    """A hand-written inline config is the door the editor's spin-box coupling misses.

    An inverted range is unsatisfiable rather than merely odd: combat_warnings wants
    min_players clients connected, while only max_players of them can be assigned a
    strategy at all.
    """
    config = DuelConfig(min_players=4, max_players=1)
    assert (config.min_players, config.max_players) == (4, 4)


def test_duel_config_leaves_a_valid_range_alone():
    config = DuelConfig(min_players=2, max_players=3)
    assert (config.min_players, config.max_players) == (2, 3)


def test_duel_config_defaults_span_every_player():
    config = DuelConfig()
    assert (config.min_players, config.max_players) == (1, 4)


def test_an_inverted_range_is_repaired_on_load_not_rejected():
    """Repair, not ValidationError — a strategy file must still open."""
    dcs = DuelCombatStrategy.model_validate(
        {"name": "Legacy", "min_players": 3, "max_players": 1},
    )
    assert (dcs.min_players, dcs.max_players) == (3, 3)


# ---------------------------------------------------------------------------
# Authored metadata: description, school, note (phase 1)
# ---------------------------------------------------------------------------


def test_new_fields_round_trip_under_exclude_defaults(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Blade", dsl="trap @ self", schools=["Storm"], description="Storm blade support")
    path = tmp_path / f"{pcs.id}.json"
    pcs.save(path)
    data = json.loads(path.read_text())
    assert data == {
        "id": pcs.id,
        "name": "Blade",
        "dsl": "trap @ self",
        "schools": ["Storm"],
        "description": "Storm blade support",
    }
    loaded = PlayerCombatStrategy.load(path)
    assert loaded.schools == ["Storm"]
    assert loaded.description == "Storm blade support"


def test_default_valued_new_fields_are_not_written(tmp_path: Path):
    """A strategy that never sets the new fields writes no trace of them."""
    pcs = PlayerCombatStrategy(name="Plain", dsl="x")
    path = tmp_path / f"{pcs.id}.json"
    pcs.save(path)
    data = json.loads(path.read_text())
    assert "school" not in data
    assert "schools" not in data
    assert "tags" not in data
    assert "description" not in data


def test_id_survives_exclude_defaults(tmp_path: Path):
    """A uuid4 default_factory value never equals a freshly generated default, so id is always written."""
    dcs = DuelCombatStrategy(name="X")
    path = tmp_path / f"{dcs.id}.json"
    dcs.save(path)
    loaded = DuelCombatStrategy.load(path)
    assert loaded.id == dcs.id


def test_description_only_config_is_still_empty():
    """A description alone is not a real config — is_empty() must not change."""
    config = DuelConfig(description="Boss enters at 50% health")
    assert config.is_empty()


def test_player_assignment_school_and_note_round_trip(tmp_path: Path):
    dcs = DuelCombatStrategy(
        name="Boss",
        player_assignments=[
            PlayerAssignment(players=[1], dsl="blade @ self", schools=["Storm"], note="Sets up the kill"),
        ],
    )
    path = tmp_path / f"{dcs.id}.json"
    dcs.save(path)
    loaded = DuelCombatStrategy.load(path)
    assignment = loaded.player_assignments[0]
    assert assignment.schools == ["Storm"]
    assert assignment.note == "Sets up the kill"


# ---------------------------------------------------------------------------
# DuelConfig.school_requirements
# ---------------------------------------------------------------------------


def test_school_requirements_counts_players_per_assignment():
    config = DuelConfig(
        player_assignments=[
            PlayerAssignment(players=[1, 2], dsl="x", schools=["Storm"]),
            PlayerAssignment(players=[3], dsl="y", schools=["Life"]),
        ],
    )
    assert config.school_requirements({}) == {"Storm": 2, "Life": 1}


def test_school_requirements_ignores_assignments_with_no_school():
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="x")])
    assert config.school_requirements({}) == {}


def test_school_requirements_resolves_through_a_linked_preset():
    """An unset assignment school falls back to the linked preset's own school."""
    pcs = PlayerCombatStrategy(name="Storm Blade", dsl="x", schools=["Storm"])
    config = DuelConfig(
        player_assignments=[PlayerAssignment(players=[1, 2], player_combat_strategy_id=pcs.id)],
    )
    assert config.school_requirements({pcs.id: pcs}) == {"Storm": 2}


def test_school_requirements_assignment_school_wins_over_linked_preset():
    pcs = PlayerCombatStrategy(name="Storm Blade", dsl="x", schools=["Storm"])
    config = DuelConfig(
        player_assignments=[
            PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id, schools=["Life"]),
        ],
    )
    assert config.school_requirements({pcs.id: pcs}) == {"Life": 1}


def test_school_requirements_missing_preset_reference_is_ignored():
    config = DuelConfig(
        player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id="missing-id")],
    )
    assert config.school_requirements({}) == {}


# ---------------------------------------------------------------------------
# #125: a school is a set, and entries carry stored tags
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "schools"),
    [({"school": "Fire"}, ["Fire"]), ({"school": None}, []), ({"school": ""}, []), ({}, [])],
)
def test_a_file_with_a_single_school_still_loads(raw, schools):
    assert PlayerCombatStrategy.model_validate({"name": "Old", **raw}).schools == schools
    assert PlayerAssignment.model_validate({"players": [1], **raw}).schools == schools


def test_a_file_carrying_both_keeps_schools_and_drops_school():
    pcs = PlayerCombatStrategy.model_validate({"name": "Both", "school": "Fire", "schools": ["Ice"]})
    assert pcs.schools == ["Ice"]
    assert "school" not in pcs.model_dump()


def test_an_old_file_is_rewritten_with_schools_on_save(tmp_path: Path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"id": "x", "name": "Old", "school": "Storm"}), encoding="utf-8")
    PlayerCombatStrategy.load(path).save(path)
    data = json.loads(path.read_text())
    assert data["schools"] == ["Storm"]
    assert "school" not in data


def test_schools_drop_repeats_and_blanks_but_keep_the_authors_order():
    assert PlayerCombatStrategy(name="x", schools=["Storm", "", "Fire", "Storm"]).schools == ["Storm", "Fire"]


def test_tags_are_normalized_on_load():
    pcs = PlayerCombatStrategy.model_validate({"name": "x", "tags": ["Healer", "Custom", "Hitter", "Healer"]})
    assert pcs.tags == ["Hitter", "Healer"]


def test_a_deck_preset_carries_tags_but_no_schools(tmp_path: Path):
    preset = DeckPreset(name="Deck", tags=["Support"])
    path = tmp_path / "deck.json"
    preset.save(path)
    assert json.loads(path.read_text())["tags"] == ["Support"]
    assert DeckPreset.load(path).tags == ["Support"]
    assert "schools" not in DeckPreset.model_fields


def test_slot_schools_has_one_set_per_slot_and_uncovered_slots_accept_any():
    config = DuelConfig(
        max_players=3,
        player_assignments=[PlayerAssignment(players=[1, 3], dsl="x", schools=["Storm", "Fire"])],
    )
    assert config.slot_schools({}) == [frozenset({"Storm", "Fire"}), frozenset(), frozenset({"Storm", "Fire"})]


def test_slot_schools_resolves_a_linked_preset_and_ignores_slots_past_max():
    pcs = PlayerCombatStrategy(name="Heals", dsl="x", schools=["Life"])
    config = DuelConfig(
        max_players=2,
        player_assignments=[PlayerAssignment(players=[2, 4], player_combat_strategy_id=pcs.id)],
    )
    assert config.slot_schools({pcs.id: pcs}) == [frozenset(), frozenset({"Life"})]


def test_school_requirements_keys_a_set_by_its_schools():
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1, 2], dsl="x", schools=["Storm", "Fire"])])
    assert config.school_requirements({}) == {"Storm/Fire": 2}


def test_an_empty_assignment_set_falls_through_to_the_linked_preset():
    pcs = PlayerCombatStrategy(name="Heals", dsl="x", schools=["Life", "Myth"])
    assignment = PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)
    assert DuelConfig.assignment_schools(assignment, {pcs.id: pcs}) == ["Life", "Myth"]
