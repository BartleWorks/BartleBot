"""Tests for bartlebot.game.stat_caps: the shipped caps, the level bands, the factors, and dungeon levels."""

import pytest

from bartlebot.game import stat_caps

THRONE_ROOM = "Marleybone/G14_Gauntlet/MB_G14_ThroneRoomB"
SISYPHUS = "Aquila/Interiors/AQ_Z01_TempleOfSisyphus"
MOUNT_OLYMPUS = "Aquila/AQ_Z01_MountOlympus"


def test_the_shipped_caps_hold_every_school_at_every_ten_levels():
    table = stat_caps.load()
    assert sorted(table) == ["Balance", "Death", "Fire", "Ice", "Life", "Myth", "Storm"]
    for levels in table.values():
        assert sorted(levels) == list(range(10, 181, 10))


def test_the_caps_are_the_games_own():
    assert stat_caps.load()["Fire"][90] == (5709, 759)


@pytest.mark.parametrize(
    ("level", "held_to"),
    [(1, 10), (10, 10), (89, 90), (90, 90), (91, 100), (100, 100), (175, 180), (180, 180), (0, None)],
)
def test_a_player_is_held_to_the_lowest_step_it_has_not_passed(level, held_to):
    assert stat_caps.band(level) == held_to


def test_the_factor_is_the_cap_at_the_target_over_the_cap_at_the_players_own_band():
    health, mana = stat_caps.factors("Fire", 180, 40)
    assert health == pytest.approx(2690 / 13413)
    assert mana == pytest.approx(450 / 936)


def test_a_player_between_steps_scales_from_the_step_above():
    assert stat_caps.factors("Fire", 175, 90) == stat_caps.factors("Fire", 180, 90)


def test_nobody_is_scaled_up():
    assert stat_caps.factors("Fire", 85, 90) == (1.0, 1.0)
    assert stat_caps.factors("Fire", 180, 180) == (1.0, 1.0)


def test_an_unknown_school_takes_the_mean_over_every_school():
    health, _ = stat_caps.factors(None, 180, 90)
    each = [caps[90][0] / caps[180][0] for caps in stat_caps.load().values()]
    assert health == pytest.approx(sum(each) / len(each))


def test_an_unread_level_scales_nothing():
    assert stat_caps.factors("Fire", 0, 90) == (1.0, 1.0)


@pytest.mark.parametrize(
    ("zone", "level"),
    [
        (THRONE_ROOM, 40),
        (THRONE_ROOM + "_Challenge", 40),
        (SISYPHUS + "_Hard", 90),
        # Pit of the Noxii's Scales are one gateway in: Challenge Mode fights it at Mount Olympus's level.
        (MOUNT_OLYMPUS, 30),
    ],
)
def test_a_dungeon_with_its_own_scales_takes_their_level(zone, level):
    assert stat_caps.dungeon_level(zone) == level


def test_a_dungeon_without_scales_takes_its_worlds_default():
    assert stat_caps.dungeon_level("Marleybone/Interiors/SomeDungeon_Hard") == 30


def test_a_world_with_no_default_but_one_level_takes_that_level():
    assert stat_caps.dungeon_level("Azteca/Interiors/SomeDungeon_Hard") == 90


@pytest.mark.parametrize(
    ("zone", "level"),
    [
        ("Wallaru/Interiors/WL_Z05_BanditCootCave_Hard", 170),
        ("Darkmoor/Interiors/DM_Z05_HereticalAcademy_Adv", 180),
    ],
)
def test_the_worlds_without_scales_have_their_level_listed(zone, level):
    assert stat_caps.dungeon_level(zone) == level


def test_a_world_nothing_names_has_no_level():
    assert stat_caps.dungeon_level("Nowhere/Somewhere") is None
