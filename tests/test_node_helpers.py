"""Tests for node_helpers zone matching utilities."""

from unittest.mock import AsyncMock

import pytest

from bartlebot.nodes.node_helpers import all_in_zone, all_left_zone, zone_matches

# ---------------------------------------------------------------------------
# zone_matches — pure function, no async
# ---------------------------------------------------------------------------


def test_zone_matches_exact_hit():
    assert zone_matches("WizardCity/WC_Hub", "WizardCity/WC_Hub") is True


def test_zone_matches_exact_miss():
    assert zone_matches("WizardCity/WC_Ravenwood", "WizardCity/WC_Hub") is False


def test_zone_matches_star_wildcard():
    assert zone_matches("WizardCity/WC_Hub", "WizardCity/*") is True


def test_zone_matches_star_wildcard_different_world():
    assert zone_matches("Krokotopia/KT_Hub", "WizardCity/*") is False


def test_zone_matches_star_any_world():
    assert zone_matches("Krokotopia/KT_Hub", "*/KT_Hub") is True


def test_zone_matches_question_mark():
    assert zone_matches("WizardCity/WC_Hub", "WizardCity/WC_Hu?") is True
    assert zone_matches("WizardCity/WC_Hub", "WizardCity/WC_Hu??") is False


def test_zone_matches_star_all_zones():
    assert zone_matches("WizardCity/WC_Hub", "*") is True
    assert zone_matches("Krokotopia/KT_Market", "*") is True


def test_zone_matches_case_sensitive():
    assert zone_matches("wizardcity/wc_hub", "WizardCity/*") is False


def test_zone_matches_malformed_pattern_falls_back_to_exact():
    # "[unclosed" is an invalid glob — should not raise, falls back to equality
    assert zone_matches("WizardCity/WC_Hub", "[unclosed") is False
    assert zone_matches("[unclosed", "[unclosed") is True


# ---------------------------------------------------------------------------
# all_in_zone — async, uses FakeClient-style mocks
# ---------------------------------------------------------------------------


def _make_client(zone: str):
    c = AsyncMock()
    c.zone_name = AsyncMock(return_value=zone)
    return c


@pytest.mark.asyncio
async def test_all_in_zone_exact_all_match():
    clients = [_make_client("WizardCity/WC_Hub"), _make_client("WizardCity/WC_Hub")]
    assert await all_in_zone(clients, "WizardCity/WC_Hub") is True


@pytest.mark.asyncio
async def test_all_in_zone_exact_partial_match():
    clients = [_make_client("WizardCity/WC_Hub"), _make_client("WizardCity/WC_Ravenwood")]
    assert await all_in_zone(clients, "WizardCity/WC_Hub") is False


@pytest.mark.asyncio
async def test_all_in_zone_wildcard_all_match():
    clients = [_make_client("WizardCity/WC_Hub"), _make_client("WizardCity/WC_Ravenwood")]
    assert await all_in_zone(clients, "WizardCity/*") is True


@pytest.mark.asyncio
async def test_all_in_zone_wildcard_partial_match():
    clients = [_make_client("WizardCity/WC_Hub"), _make_client("Krokotopia/KT_Hub")]
    assert await all_in_zone(clients, "WizardCity/*") is False


# ---------------------------------------------------------------------------
# all_left_zone — async, uses FakeClient-style mocks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_left_zone_exact_all_left():
    clients = [_make_client("WizardCity/WC_Ravenwood"), _make_client("Krokotopia/KT_Hub")]
    assert await all_left_zone(clients, "WizardCity/WC_Hub") is True


@pytest.mark.asyncio
async def test_all_left_zone_exact_still_present():
    clients = [_make_client("WizardCity/WC_Hub"), _make_client("WizardCity/WC_Ravenwood")]
    assert await all_left_zone(clients, "WizardCity/WC_Hub") is False


@pytest.mark.asyncio
async def test_all_left_zone_wildcard_all_left():
    clients = [_make_client("Krokotopia/KT_Hub"), _make_client("Krokotopia/KT_Market")]
    assert await all_left_zone(clients, "WizardCity/*") is True


@pytest.mark.asyncio
async def test_all_left_zone_wildcard_one_still_matches():
    # One client is still in a WizardCity zone — should NOT be considered "left"
    clients = [_make_client("WizardCity/WC_Ravenwood"), _make_client("Krokotopia/KT_Hub")]
    assert await all_left_zone(clients, "WizardCity/*") is False


@pytest.mark.asyncio
async def test_all_left_zone_wildcard_same_prefix_as_destination():
    # Documents the known hazard: if from_zone wildcard matches the destination
    # zone too, all_left_zone stays False even after the zone transition.
    clients = [_make_client("WizardCity/WC_GreatSpyre_DF3"), _make_client("WizardCity/WC_GreatSpyre_DF3")]
    assert await all_left_zone(clients, "WizardCity/*") is False
