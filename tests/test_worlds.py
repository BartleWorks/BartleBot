"""Unit tests for the spiral door world table."""

import json

from bartlebot import worlds


def test_every_spiral_door_world_is_in_the_snapshot():
    """A typo in SPIRAL_DOOR_WORLDS must fail here, not silently empty the editor dropdown."""
    missing = [world for world in worlds.SPIRAL_DOOR_WORLDS if not worlds.display_names(world)]

    assert missing == []


def test_every_spiral_door_world_has_an_english_name():
    """en-US is what label() shows and what untranslated locales fall back to."""
    snapshot = json.loads(worlds.WORLD_NAMES_PATH.read_text(encoding="utf-8"))["worlds"]

    assert all("en-US" in snapshot[world] for world in worlds.SPIRAL_DOOR_WORLDS)


def test_world_keys_are_unique():
    assert len(set(worlds.SPIRAL_DOOR_WORLDS)) == len(worlds.SPIRAL_DOOR_WORLDS)


def test_display_names_leads_with_english():
    assert worlds.display_names("DragonSpire")[0] == "Dragonspyre"
    assert worlds.label("DragonSpire") == "Dragonspyre"


def test_display_names_carries_other_locales():
    """The whole point of the table: a French client lists Sorcelleville, not Wizard City."""
    names = worlds.display_names("WizardCity")

    assert "Wizard City" in names
    assert "Sorcelleville" in names


def test_display_names_are_deduped():
    """Most worlds share a spelling across several locales; each is only worth matching once."""
    names = worlds.display_names("Krokotopia")

    assert len(names) == len(set(names))


def test_an_untranslated_world_still_resolves_to_english():
    """Darkmoor has no el/it/pl entry — those clients show the English name."""
    names = worlds.display_names("Darkmoor")

    assert names[0] == "Darkmoor"


def test_unknown_world_yields_nothing_rather_than_raising():
    """go_to_world checks for this and fails loudly; it must not explode here first."""
    assert worlds.display_names("NotAWorld") == []
    assert worlds.label("NotAWorld") == "NotAWorld"


def test_zone_pattern_matches_the_world_segment():
    from bartlebot.nodes.node_helpers import zone_matches

    pattern = worlds.zone_pattern("Darkmoor")

    assert zone_matches("Darkmoor/DM_Z01_Graveholm", pattern)
    assert zone_matches("Darkmoor/Interiors/DM_Z01I17_SandersonHouse", pattern)


def test_castle_darkmoor_is_not_the_darkmoor_world():
    """G14_DM is a dungeon reached from Nightside; a client in it does need to travel."""
    from bartlebot.nodes.node_helpers import zone_matches

    assert not zone_matches("G14_DM/DM_Z01_CastleDarkmoor", worlds.zone_pattern("Darkmoor"))
    assert "G14_DM" not in worlds.SPIRAL_DOOR_WORLDS
    assert "Darkmoor" in worlds.SPIRAL_DOOR_WORLDS


def test_worlds_reached_by_other_means_are_not_on_the_door():
    """Aquila is a chariot ride and Wintertusk is through Grizzleheim, so neither is offered."""
    assert "Aquila" not in worlds.SPIRAL_DOOR_WORLDS
    assert "Wintertusk" not in worlds.SPIRAL_DOOR_WORLDS
    assert "AllWorlds" not in worlds.SPIRAL_DOOR_WORLDS
