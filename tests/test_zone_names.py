"""Unit tests for the zone display-name snapshot."""

import pytest

from bartlebot import zone_names, zone_nav


def test_every_locale_is_offered_with_english_first():
    names = zone_names.display_names("WizardCity/WC_Streets/WC_Colossus")

    assert names[0] == "Colossus Boulevard"
    assert len(names) == len(set(names)) > 1


def test_an_unknown_zone_has_no_names():
    assert zone_names.display_names("Nowhere/Nothing") == []


def test_the_maps_label_wins_where_the_game_name_is_ambiguous():
    assert zone_names.label("Empyrea/Interiors/EM_Z02_Sewers") == "Zanadu Sewers"


def test_a_zone_without_a_name_falls_back_to_its_path():
    assert zone_names.label("Nowhere/Interiors/Boss_Room") == "Boss_Room"


def test_every_zone_on_the_map_can_be_named():
    nav = zone_nav.load()
    nameless = [
        zone
        for world in nav.worlds.values()
        for zone in world.zones
        if not world.label(zone) and not zone_names.display_names(zone)
    ]

    assert nameless == [], "regenerate with tools/dump_zone_names.py, or give the zone a label"


def test_an_unreadable_snapshot_names_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(zone_names, "ZONE_NAMES_PATH", tmp_path / "missing.json")
    zone_names._snapshot.cache_clear()
    try:
        assert zone_names.display_names("WizardCity/WC_Streets/WC_Colossus") == []
    finally:
        zone_names._snapshot.cache_clear()


# ---------------------------------------------------------------------------
# Challenge Mode copies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("zone", "base"),
    [
        ("Marleybone/G14_Gauntlet/MB_G14_ThroneRoomB_Challenge", "Marleybone/G14_Gauntlet/MB_G14_ThroneRoomB"),
        ("Aquila/AQ_Z03_Tartarus_Hard", "Aquila/AQ_Z03_Tartarus"),
        ("Karamelle/Interiors/KM_Z00_TheCavity_Adv", "Karamelle/Interiors/KM_Z00_TheCavity"),
        ("Darkmoor/Interiors/DM_Z04I08_BogOfVengeanceAdv", "Darkmoor/Interiors/DM_Z04I08_BogOfVengeance"),
    ],
)
def test_a_challenge_mode_copy_names_the_zone_it_is_a_copy_of(zone, base):
    """One case per suffix the game uses; the four are not interchangeable between dungeons."""
    assert zone_names.challenge_variant_of(zone) == base


@pytest.mark.parametrize(
    "zone",
    [
        "Aquila/AQ_Z03_Tartarus",
        "Wallaru/Interiors/WL_Z02_I10_OutbackDungeon_Easy",
        "Nowhere/Made_Up_Hard",
        "",
        None,
    ],
    ids=["ordinary", "easy copy", "no such sibling", "blank", "unread"],
)
def test_anything_else_is_not_a_challenge_mode_copy(zone):
    assert zone_names.challenge_variant_of(zone) is None
