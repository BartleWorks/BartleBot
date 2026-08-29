"""Unit tests for the Wizard City travel route shared by handle_potions and go_to_world."""

from unittest.mock import AsyncMock, patch

import pytest

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.wizard_city as wc
from bartlebot.nodes.paths import GOTO_DORM_BUTTON_PATH, MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT
from bartlebot.nodes.zone_entry import enter_zone
from tests.conftest import FakeClient, FakeMachine, message_box_patches

DORM = wc.DORM_ZONE


# ══════════════════════════════════════════════════════════════════════════════
# _press_home_until_dorm
# ══════════════════════════════════════════════════════════════════════════════


def _dorm_client() -> FakeClient:
    """Client that immediately enters loading and lands in dorm."""
    c = FakeClient()
    c.zone_name.return_value = DORM
    c.is_loading.side_effect = [True, False]
    return c


async def test_home_lands_in_dorm_first_try():
    """Single HOME press → dorm zone → True."""
    client = _dorm_client()
    none_load = patch.object(nh, "none_loading", new=AsyncMock(return_value=True))

    with none_load, patch.object(wc, "POLL_INTERVAL", 0):
        result = await wc._press_home_until_dorm(FakeMachine(), [client])

    assert result is True


async def test_home_wrong_zone_dorm_button_wait_second_press_succeeds():
    """First HOME → non-dorm; waits for dorm button; second HOME → dorm → True."""
    client = FakeClient()
    client.zone_name.side_effect = ["SomeHouse", DORM]
    client.is_loading.side_effect = [True, False, True, False]
    none_load = patch.object(nh, "none_loading", new=AsyncMock(return_value=True))
    btn_enabled = patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=True))

    with none_load, btn_enabled, patch.object(wc, "POLL_INTERVAL", 0):
        result = await wc._press_home_until_dorm(FakeMachine(), [client])

    assert result is True


async def test_home_dorm_button_timeout_returns_false():
    """Dorm button never becomes enabled → False after timeout."""
    client = FakeClient()
    client.zone_name.return_value = "SomeHouse"
    client.is_loading.side_effect = [True, False]
    none_load = patch.object(nh, "none_loading", new=AsyncMock(return_value=True))
    btn_disabled = patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=False))

    with (
        none_load,
        btn_disabled,
        patch.object(wc, "COMPASS_TELEPORT_TIMEOUT", 0.01),
        patch.object(wc, "POLL_INTERVAL", 0),
    ):
        result = await wc._press_home_until_dorm(FakeMachine(), [client])

    assert result is False


async def test_home_both_attempts_wrong_zone_returns_false():
    """Both HOME presses land in a non-dorm zone → False."""
    client = FakeClient()
    client.zone_name.side_effect = ["SomeHouse", "SomeHouse"]
    client.is_loading.side_effect = [True, False, True, False]
    none_load = patch.object(nh, "none_loading", new=AsyncMock(return_value=True))
    btn_enabled = patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=True))

    with none_load, btn_enabled, patch.object(wc, "POLL_INTERVAL", 0):
        result = await wc._press_home_until_dorm(FakeMachine(), [client])

    assert result is False


async def test_home_loading_timeout_still_checks_zone():
    """Client never enters loading screen within timeout; zone check still runs."""
    client = FakeClient()
    client.zone_name.return_value = DORM
    client.is_loading.return_value = False  # never loads
    none_load = patch.object(nh, "none_loading", new=AsyncMock(return_value=True))

    with (
        none_load,
        patch.object(wc, "_HOME_LOADING_TIMEOUT", 0.01),
        patch.object(wc, "POLL_INTERVAL", 0),
    ):
        result = await wc._press_home_until_dorm(FakeMachine(), [client])

    # Zone is DORM, so should still succeed.
    assert result is True


async def test_home_only_retries_clients_not_yet_in_dorm():
    """One client reaches dorm on attempt 1; the other lands in a house and needs a retry.
    Only the house client should receive the second HOME press.

    Both clients load immediately on every attempt (no loading re-press), so
    click_by_path is called exactly once per HOME attempt per client.
    """
    c_dorm = FakeClient()
    c_dorm.zone_name.return_value = DORM
    c_dorm.is_loading.side_effect = [True]  # loads on first check in attempt 1

    c_house = FakeClient()
    c_house.zone_name.side_effect = ["SomeHouse", DORM]
    c_house.is_loading.side_effect = [True, True]  # loads on first check in each attempt

    pressed_for: list = []

    async def fake_click(client, path):
        pressed_for.append(client)

    none_load = patch.object(nh, "none_loading", new=AsyncMock(return_value=True))
    btn_enabled = patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=True))
    click_patch = patch.object(nh, "click_by_path", side_effect=fake_click)

    with none_load, btn_enabled, click_patch, patch.object(wc, "POLL_INTERVAL", 0):
        result = await wc._press_home_until_dorm(FakeMachine(), [c_dorm, c_house])

    assert result is True
    assert pressed_for.count(c_dorm) == 1  # reached dorm on first press; no retry
    assert pressed_for.count(c_house) == 2  # initial press + second-attempt press


async def test_home_repressed_for_lagging_client():
    """Client that doesn't start loading gets a re-press of HOME."""
    c1 = FakeClient()
    c1.zone_name.return_value = DORM
    c1.is_loading.side_effect = [True, True, False]  # starts loading immediately

    c2 = FakeClient()
    c2.zone_name.return_value = DORM
    c2.is_loading.side_effect = [False, True, False]  # needs re-press

    none_load = patch.object(nh, "none_loading", new=AsyncMock(return_value=True))

    with none_load, patch.object(wc, "POLL_INTERVAL", 0):
        result = await wc._press_home_until_dorm(FakeMachine(), [c1, c2])

    assert result is True


# ══════════════════════════════════════════════════════════════════════════════
# teleport_home_and_verify_dorm — the castle unequip wrapper around the above
# ══════════════════════════════════════════════════════════════════════════════


def _housing_patches(unequip_returns=None, equip_returns=True):
    """Patch the housing module as handle_potions sees it."""
    return (
        patch.object(wc.housing, "unequip_castle", new=AsyncMock(return_value=unequip_returns)),
        patch.object(wc.housing, "equip_castle", new=AsyncMock(return_value=equip_returns)),
    )


async def test_castle_is_unequipped_before_home_and_restored_after():
    client = FakeClient()
    unequip, equip = _housing_patches(unequip_returns="Red Barn Farm")
    press = patch.object(wc, "_press_home_until_dorm", new=AsyncMock(return_value=True))

    with unequip as u, equip as e, press:
        result = await wc.teleport_home_and_verify_dorm(FakeMachine(), [client])

    assert result is True
    u.assert_awaited_once_with(client)
    e.assert_awaited_once_with(client, "Red Barn Farm")


async def test_castle_is_restored_even_when_home_fails():
    """The restore is in a finally block — a failed teleport must not cost somebody their home."""
    client = FakeClient()
    unequip, equip = _housing_patches(unequip_returns="Red Barn Farm")
    press = patch.object(wc, "_press_home_until_dorm", new=AsyncMock(return_value=False))

    with unequip, equip as e, press:
        result = await wc.teleport_home_and_verify_dorm(FakeMachine(), [client])

    assert result is False
    e.assert_awaited_once_with(client, "Red Barn Farm")


async def test_castle_is_restored_even_when_home_raises():
    client = FakeClient()
    unequip, equip = _housing_patches(unequip_returns="Red Barn Farm")
    press = patch.object(wc, "_press_home_until_dorm", new=AsyncMock(side_effect=RuntimeError("boom")))

    with unequip, equip as e, press, pytest.raises(RuntimeError):
        await wc.teleport_home_and_verify_dorm(FakeMachine(), [client])

    e.assert_awaited_once_with(client, "Red Barn Farm")


async def test_no_castle_equipped_skips_the_restore():
    """A wizard whose home is already the dorm has nothing to unequip and nothing to put back."""
    client = FakeClient()
    unequip, equip = _housing_patches(unequip_returns=None)
    press = patch.object(wc, "_press_home_until_dorm", new=AsyncMock(return_value=True))

    with unequip, equip as e, press:
        result = await wc.teleport_home_and_verify_dorm(FakeMachine(), [client])

    assert result is True
    e.assert_not_awaited()


async def test_failed_restore_warns_but_does_not_fail_the_step():
    """The trip home worked; the user is told to fix the home by hand rather than losing the run."""
    client = FakeClient()
    machine = FakeMachine()
    unequip, equip = _housing_patches(unequip_returns="Red Barn Farm", equip_returns=False)
    press = patch.object(wc, "_press_home_until_dorm", new=AsyncMock(return_value=True))

    with unequip, equip, press:
        result = await wc.teleport_home_and_verify_dorm(machine, [client])

    assert result is True
    assert any("Red Barn Farm" in str(entry) for entry in machine.logs)


async def test_only_clients_with_castles_are_restored():
    """Two clients, one with a castle — the other must not be handed a castle it never had."""
    c_castle = FakeClient()
    c_dorm = FakeClient()
    unequip = patch.object(wc.housing, "unequip_castle", new=AsyncMock(side_effect=["Red Barn Farm", None]))
    equip = patch.object(wc.housing, "equip_castle", new=AsyncMock(return_value=True))
    press = patch.object(wc, "_press_home_until_dorm", new=AsyncMock(return_value=True))

    with unequip, equip as e, press:
        await wc.teleport_home_and_verify_dorm(FakeMachine(), [c_castle, c_dorm])

    e.assert_awaited_once_with(c_castle, "Red Barn Farm")


# ══════════════════════════════════════════════════════════════════════════════
# _all_dorm_button_enabled
# ══════════════════════════════════════════════════════════════════════════════


async def test_all_dorm_button_enabled_all_true():
    clients = [FakeClient(), FakeClient()]
    with patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=True)):
        assert await wc._all_dorm_button_enabled(clients) is True


async def test_all_dorm_button_enabled_one_false():
    clients = [FakeClient(), FakeClient()]
    with patch.object(nh, "is_enabled_by_path", new=AsyncMock(side_effect=[True, False])):
        assert await wc._all_dorm_button_enabled(clients) is False


# ══════════════════════════════════════════════════════════════════════════════
# Dungeon exit warning (issue #84)
# ══════════════════════════════════════════════════════════════════════════════


async def _press_home_with_modal(visible_buttons) -> tuple[bool, list[list[str]]]:
    """Run _press_home_until_dorm past one stuck tick; return (result, clicked paths)."""
    client = FakeClient()
    client.zone_name.return_value = DORM
    # Tick 1: nothing has happened yet. Tick 2: loading. Then done.
    client.is_loading.side_effect = [False, True, False]
    patches, clicked = message_box_patches(visible_buttons)

    with (
        patch.object(nh, "none_loading", new=AsyncMock(return_value=True)),
        patch.object(wc, "POLL_INTERVAL", 0),
        patches[0],
        patches[1],
    ):
        result = await wc._press_home_until_dorm(FakeMachine(), [client])

    return result, clicked


async def test_home_confirms_dungeon_exit_warning_rather_than_re_pressing():
    """Going home from inside a dungeon raises the warning, which blocks the teleport."""
    result, clicked = await _press_home_with_modal((MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT))

    assert result is True
    assert clicked == [GOTO_DORM_BUTTON_PATH, MESSAGE_BOX_CENTER]


async def test_home_without_a_warning_still_re_presses():
    """Outside a dungeon the straggler retry must behave exactly as it did before."""
    result, clicked = await _press_home_with_modal(())

    assert result is True
    assert clicked == [GOTO_DORM_BUTTON_PATH, GOTO_DORM_BUTTON_PATH]


# ══════════════════════════════════════════════════════════════════════════════
# go_to_ravenwood — the route both handle_potions and go_to_world start with
# ══════════════════════════════════════════════════════════════════════════════


async def test_go_to_ravenwood_homes_then_walks_out():
    client = FakeClient()
    home = patch.object(wc, "teleport_home_and_verify_dorm", new=AsyncMock(return_value=True))
    zones = patch.object(enter_zone, "run", new=AsyncMock(return_value="SUCCESS"))

    with home as h, zones as z:
        assert await wc.go_to_ravenwood(FakeMachine(), [client]) is True

    h.assert_awaited_once()
    params = z.await_args.args[2]
    assert params["from_zone"] == wc.DORM_ZONE
    assert params["to_zone"] == wc.RAVENWOOD_ZONE


async def test_go_to_ravenwood_stops_when_home_fails():
    """Walking the dorm exit from the wrong zone would teleport into open Ravenwood air."""
    client = FakeClient()
    home = patch.object(wc, "teleport_home_and_verify_dorm", new=AsyncMock(return_value=False))
    zones = patch.object(enter_zone, "run", new=AsyncMock(return_value="SUCCESS"))

    with home, zones as z:
        assert await wc.go_to_ravenwood(FakeMachine(), [client]) is False

    z.assert_not_awaited()


async def test_go_to_ravenwood_reports_a_failed_dorm_exit():
    client = FakeClient()
    home = patch.object(wc, "teleport_home_and_verify_dorm", new=AsyncMock(return_value=True))
    zones = patch.object(enter_zone, "run", new=AsyncMock(return_value="FAILURE"))

    with home, zones:
        assert await wc.go_to_ravenwood(FakeMachine(), [client]) is False
