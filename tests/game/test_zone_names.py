"""Unit tests for the zone display-name snapshot."""

import pytest

from bartlebot.game import zone_names, zone_nav


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


def test_the_ordinary_zone_of_a_copy_is_the_zone_it_copies():
    assert zone_names.canonical_zone("Aquila/AQ_Z02_Atlantea_Hard") == "Aquila/AQ_Z02_Atlantea"


@pytest.mark.parametrize(
    "zone",
    ["Aquila/AQ_Z02_Atlantea", "Wallaru/Interiors/WL_Z02_I10_OutbackDungeon_Easy", "Nowhere/Made_Up_Hard"],
    ids=["ordinary", "easy copy", "no such sibling"],
)
def test_any_other_zone_is_its_own_ordinary_zone(zone):
    assert zone_names.canonical_zone(zone) == zone


# ---------------------------------------------------------------------------
# Gauntlets copied once per level band
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("zone", "canonical"),
    [
        (
            "Housing_AV_Gauntlet/AV_Gauntlet_60/AV_Gauntlet_60_03",
            "Housing_AV_Gauntlet/AV_Gauntlet_01/AV_Gauntlet_01_03",
        ),
        ("Housing_Gauntlet/KH_Gauntlet80/KH_Gauntlet80_07", "Housing_Gauntlet/KH_Gauntlet01/KH_Gauntlet01_07"),
        (
            "Housing_PA_Gauntlet/PA_Gauntlet_40/PA_Gauntlet_40_06",
            "Housing_PA_Gauntlet/PA_Gauntlet_01/PA_Gauntlet_01_06",
        ),
        (
            "Housing_VA_Gauntlet/VA_Clock_Gauntlet_120/VA_Level120_Exterior",
            "Housing_VA_Gauntlet/VA_Clock_Gauntlet_020/VA_Level020_Exterior",
        ),
        (
            "Housing_GHGauntlet/GH_WinterbaneGauntlet05/GH_WinterbaneGauntlet05_R02",
            "Housing_GHGauntlet/GH_WinterbaneGauntlet01/GH_WinterbaneGauntlet01_R02",
        ),
        (
            "Housing_Gauntlet_Theater/Gauntlet_06/TheaterGauntlet_G06_Int04",
            "Housing_Gauntlet_Theater/Gauntlet_01/TheaterGauntlet_G01_Int04",
        ),
        ("Housing_Gauntlet_Voyage/Gauntlet_03/Z02_KalypsoDeck", "Housing_Gauntlet_Voyage/Gauntlet_01/Z02_KalypsoDeck"),
    ],
    ids=["AV", "KH", "PA", "VA", "Winterbane", "Theater", "Voyage"],
)
def test_a_gauntlet_room_in_any_band_is_stored_as_its_lowest_band(zone, canonical):
    """One case per family: the game picks the band from the players' levels."""
    assert zone_names.canonical_zone(zone) == canonical
    assert zone_names.canonical_zone(canonical) == canonical


@pytest.mark.parametrize(
    "zone",
    [
        "WizardCity/WC_Streets/Interiors/WC_Sunken_City_T2",
        "WizardCity/TreasureTower/WC_TT01_Fire_L12",
        "Holiday/Halloween/Gauntlet_Hard/Level1",
        "Housing_Gauntlet_SIT/HG_Gauntlet_SIT_Int02",
    ],
    ids=["numbered room", "tower floor", "halloween difficulty", "untiered gauntlet"],
)
def test_numbered_rooms_and_floors_are_not_level_bands(zone):
    assert zone_names.canonical_zone(zone) == zone


def test_a_band_newer_than_the_snapshot_is_still_stored_as_the_lowest_band():
    """A game patch can add a band before the snapshot is regenerated."""
    zone = "Housing_PA_Gauntlet/PA_Gauntlet_100/PA_Gauntlet_100_02"

    assert zone_names.canonical_zone(zone) == "Housing_PA_Gauntlet/PA_Gauntlet_01/PA_Gauntlet_01_02"


@pytest.mark.parametrize("prefix", zone_names.TIERED_GAUNTLETS)
def test_every_declared_gauntlet_has_the_same_rooms_in_several_bands(prefix):
    """Fails if a regenerated snapshot no longer bears a family out."""
    zones, _ = zone_names._snapshot()
    family = zone_names.TIERED_GAUNTLETS.index(prefix)
    rooms_by_band: dict[int, set[str]] = {}
    for zone in zones:
        if (tier := zone_names.tier_of(zone)) is not None and tier[0] == family:
            rooms_by_band.setdefault(tier[2], set()).add(tier[1])

    assert len(rooms_by_band) > 1
    assert len({frozenset(rooms) for rooms in rooms_by_band.values()}) == 1
