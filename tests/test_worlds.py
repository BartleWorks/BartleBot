"""Unit tests for the spiral door world table."""

import json

from bartlebot import worlds


def test_the_world_gate_title_is_in_the_snapshot_english_first():
    assert worlds.world_gate_titles()[0] == "Spiral Map"
    assert len(worlds.world_gate_titles()) > 1


def test_a_world_gate_title_matches_through_markup_and_case():
    assert worlds.is_world_gate_title("Spiral Map")
    assert worlds.is_world_gate_title("<center>SPIRAL  MAP</center>")
    assert worlds.is_world_gate_title("<center>Spirala</center>")


def test_other_list_titles_are_not_a_world_gate():
    assert not worlds.is_world_gate_title("Map")
    assert not worlds.is_world_gate_title("Spiral Map of Empyrea")
    assert not worlds.is_world_gate_title("")
    assert not worlds.is_world_gate_title(None)


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
    """A World Gate step checks for this and fails loudly; it must not explode here first."""
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


def test_the_dungeon_worlds_are_dungeons_and_not_on_the_door():
    """These are nav-map "worlds" the game ships no world emblem for, so they take the
    generic dungeon badge. None of them is a spiral door destination."""
    assert set(worlds.DUNGEON_WORLDS) == {"G14_DM", "G14_SB", "G14_HS"}
    assert not set(worlds.DUNGEON_WORLDS) & set(worlds.SPIRAL_DOOR_WORLDS)


def test_zigazag_is_labeled_though_the_game_never_names_it():
    """G14_HS has no WorldNames entry, so without a fallback its picker section read
    "G14_HS"."""
    assert worlds.label("G14_HS") == "Zigazag"


def test_a_fallback_label_never_reaches_the_door_matching_list():
    """display_names is matched against the World Gate's own text; the game never shows
    a fallback name there."""
    assert worlds.display_names("G14_HS") == []


def test_no_fallback_shadows_a_name_the_game_ships():
    """If the game ever names one of these, the fallback is stale and should go."""
    snapshot = json.loads(worlds.WORLD_NAMES_PATH.read_text(encoding="utf-8"))["worlds"]

    assert not set(worlds.LABEL_FALLBACKS) & set(snapshot)


def test_aquila_is_a_world_with_an_emblem_not_a_dungeon():
    """It is off the door because it is reached by the Cyclops Lane chariot, which is not
    the same claim as being a dungeon -- it has a Button_World_ texture of its own."""
    assert "Aquila" not in worlds.DUNGEON_WORLDS


def test_worlds_reached_by_other_means_are_not_on_the_door():
    """Aquila is a chariot ride and Wintertusk is through Grizzleheim, so neither is offered."""
    assert "Aquila" not in worlds.SPIRAL_DOOR_WORLDS
    assert "Wintertusk" not in worlds.SPIRAL_DOOR_WORLDS
    assert "AllWorlds" not in worlds.SPIRAL_DOOR_WORLDS
