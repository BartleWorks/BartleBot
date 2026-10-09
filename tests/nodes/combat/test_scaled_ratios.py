"""Tests for scaled_ratios: which level a bot's Challenge Mode scales to, and the ratios read against it."""

from types import SimpleNamespace

import pytest

from bartlebot.nodes.combat import scaled_ratios
from tests.conftest import FakeClient, FakeMachine

THRONE_ROOM = "Marleybone/G14_Gauntlet/MB_G14_ThroneRoomB"
SISYPHUS = "Aquila/Interiors/AQ_Z01_TempleOfSisyphus"

#: A level 180 Fire wizard's share of its own max when it holds all of the level 40 max.
FULL_AT_40 = 2690 / 13413


def _entry(zone: str, offers: bool = True) -> SimpleNamespace:
    return SimpleNamespace(node_type="enter_dungeon", params={"dungeon_zone": zone, "offers_challenge_mode": offers})


def _machine(*states, challenge: bool = True) -> FakeMachine:
    return FakeMachine(bot_config=SimpleNamespace(states=list(states)), options={"Challenge Mode": challenge})


def test_the_level_is_the_challenge_dungeons():
    assert scaled_ratios.challenge_level(_machine(_entry(THRONE_ROOM))) == 40


def test_no_level_with_challenge_mode_off():
    assert scaled_ratios.challenge_level(_machine(_entry(THRONE_ROOM), challenge=False)) is None


def test_a_sigil_that_does_not_offer_challenge_mode_is_not_read():
    assert scaled_ratios.challenge_level(_machine(_entry(THRONE_ROOM, offers=False))) is None


def test_two_challenge_dungeons_take_the_higher_level():
    assert scaled_ratios.challenge_level(_machine(_entry(THRONE_ROOM), _entry(SISYPHUS))) == 90


def test_a_dungeon_with_no_known_level_gives_none():
    assert scaled_ratios.challenge_level(_machine(_entry("Nowhere/Somewhere"))) is None


async def test_without_a_level_the_ratios_are_plain():
    client = FakeClient()
    client.calc_health_ratio.return_value = 0.1
    assert await scaled_ratios.ratios(client, None) == (0.1, 1.0)


async def test_a_player_who_left_at_full_health_reads_full():
    client = FakeClient()
    client.calc_health_ratio.return_value = FULL_AT_40
    health, _ = await scaled_ratios.ratios(client, 40)
    assert health == pytest.approx(1.0)


async def test_half_the_scaled_max_reads_half():
    client = FakeClient()
    client.calc_health_ratio.return_value = FULL_AT_40 / 2
    health, _ = await scaled_ratios.ratios(client, 40)
    assert health == pytest.approx(0.5)


async def test_a_ratio_never_reads_above_full():
    client = FakeClient()
    client.calc_health_ratio.return_value = 0.9
    assert await scaled_ratios.ratios(client, 40) == (1.0, 1.0)


async def test_inside_a_challenge_zone_the_game_has_scaled_already():
    client = FakeClient()
    client.zone_name.return_value = THRONE_ROOM + "_Challenge"
    client.calc_health_ratio.return_value = 0.3
    assert await scaled_ratios.ratios(client, 40) == (0.3, 1.0)
