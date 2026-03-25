"""Unit tests for the handle_potions node."""

from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode

import bartlebot.nodes.action.handle_potions as hp
import bartlebot.nodes.node_helpers as nh
from bartlebot.nodes.action import go_to_zone, mark_location, recall_to_marked_location
from tests.conftest import FakeClient, FakeMachine

# ── Zone aliases ─────────────────────────────────────────────────────────────
DORM = hp.DORM_ZONE
RAVEN = hp.RAVENWOOD_ZONE
COMMONS = hp.COMMONS_ZONE

# ── Default params ────────────────────────────────────────────────────────────
_PARAMS = {
    "if_health_below": 0.5,
    "if_mana_below": 0.15,
    "refill_potions": True,
    "mark_zone": "",
}

# ── Re-usable sub-node patches ────────────────────────────────────────────────
_MARK_OK = patch.object(mark_location, "run", new=AsyncMock(return_value="SUCCESS"))
_RECALL_OK = patch.object(recall_to_marked_location, "run", new=AsyncMock(return_value="SUCCESS"))
_ZONES_OK = patch.object(go_to_zone, "run", new=AsyncMock(return_value="SUCCESS"))

# Patch the private helpers so run() tests don't need to drive their internals.
_HOME_OK = patch(
    "bartlebot.nodes.action.handle_potions._teleport_home_and_verify_dorm",
    new=AsyncMock(return_value=True),
)
_SHOP_FN_OK = patch(
    "bartlebot.nodes.action.handle_potions._open_potion_shop",
    new=AsyncMock(return_value=True),
)
_BUY_OK = patch(
    "bartlebot.nodes.action.handle_potions._buy_potions_for_client",
    new=AsyncMock(),
)


# ── Client factories ──────────────────────────────────────────────────────────


def _well_stocked_client() -> FakeClient:
    """Client above thresholds with potions available — no action needed."""
    c = FakeClient()
    c.calc_health_ratio.return_value = 1.0
    c.calc_mana_ratio.return_value = 1.0
    c.has_potion.return_value = True
    c.zone_name.return_value = COMMONS
    return c


def _depleted_client(zone: str = "WizardCity/WC_Olde_Town") -> FakeClient:
    """Client below thresholds with no potions — should enter the buy flow."""
    c = FakeClient()
    c.calc_health_ratio.return_value = 0.1
    c.calc_mana_ratio.return_value = 0.05
    c.has_potion.return_value = False
    c.zone_name.return_value = zone
    c.body.position.return_value = XYZ(0.0, 0.0, 0.0)
    return c


# ══════════════════════════════════════════════════════════════════════════════
# run() — potion use logic
# ══════════════════════════════════════════════════════════════════════════════


async def test_all_above_threshold_no_potions_used():
    """Clients above both thresholds → use_potion never called."""
    client = _well_stocked_client()

    result = await hp.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.use_potion.assert_not_called()


async def test_health_below_threshold_uses_potion():
    """Client below health threshold → use_potion called."""
    client = _well_stocked_client()
    client.calc_health_ratio.return_value = 0.3  # below 0.5

    result = await hp.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.use_potion.assert_called_once()


async def test_mana_below_threshold_uses_potion():
    """Client below mana threshold → use_potion called."""
    client = _well_stocked_client()
    client.calc_mana_ratio.return_value = 0.05  # below 0.15

    result = await hp.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.use_potion.assert_called_once()


async def test_zero_thresholds_skip_potion_use():
    """if_health_below=0 and if_mana_below=0 → no potion use regardless of ratios."""
    client = _well_stocked_client()
    client.calc_health_ratio.return_value = 0.0
    client.calc_mana_ratio.return_value = 0.0
    params = {**_PARAMS, "if_health_below": 0.0, "if_mana_below": 0.0}

    result = await hp.run(FakeMachine(), [client], params)

    assert result == "SUCCESS"
    client.use_potion.assert_not_called()


async def test_only_low_clients_use_potions():
    """Only clients below threshold receive a potion use call."""
    c_ok = _well_stocked_client()
    c_low = _well_stocked_client()
    c_low.calc_health_ratio.return_value = 0.1

    result = await hp.run(FakeMachine(), [c_ok, c_low], _PARAMS)

    assert result == "SUCCESS"
    c_ok.use_potion.assert_not_called()
    c_low.use_potion.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# run() — refill early exits
# ══════════════════════════════════════════════════════════════════════════════


async def test_refill_disabled_no_potions_still_success():
    """refill_potions=False and client has no potions → SUCCESS, no navigation."""
    client = _depleted_client()
    params = {**_PARAMS, "refill_potions": False}

    result = await hp.run(FakeMachine(), [client], params)

    assert result == "SUCCESS"
    client.teleport.assert_not_called()


async def test_everyone_has_potions_no_refill():
    """All clients have potions → SUCCESS without entering refill flow."""
    clients = [_well_stocked_client(), _well_stocked_client()]

    result = await hp.run(FakeMachine(), clients, _PARAMS)

    assert result == "SUCCESS"
    for c in clients:
        c.teleport.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# run() — mark_zone precondition
# ══════════════════════════════════════════════════════════════════════════════


async def test_mark_zone_correct_proceeds():
    """Buying client in the specified mark_zone → precondition passes → SUCCESS."""
    client = _depleted_client(zone="MyDungeon")
    params = {**_PARAMS, "mark_zone": "MyDungeon"}

    with _MARK_OK, _HOME_OK, _ZONES_OK, _SHOP_FN_OK, _BUY_OK, _RECALL_OK:
        result = await hp.run(FakeMachine(), [client], params)

    assert result == "SUCCESS"


async def test_mark_zone_wrong_returns_failure():
    """Buying client not in mark_zone → FAILURE before any navigation."""
    client = _depleted_client(zone="OtherZone")
    params = {**_PARAMS, "mark_zone": "MyDungeon"}

    result = await hp.run(FakeMachine(), [client], params)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_mark_zone_empty_skips_zone_check():
    """mark_zone='' → no zone precondition checked → proceeds to refill."""
    client = _depleted_client(zone="AnyZone")

    with _MARK_OK, _HOME_OK, _ZONES_OK, _SHOP_FN_OK, _BUY_OK, _RECALL_OK:
        result = await hp.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"


async def test_mixed_clients_only_some_in_wrong_zone_fails():
    """Two buying clients, one in wrong zone → FAILURE."""
    c_ok = _depleted_client(zone="MyDungeon")
    c_wrong = _depleted_client(zone="OtherZone")
    params = {**_PARAMS, "mark_zone": "MyDungeon"}

    result = await hp.run(FakeMachine(), [c_ok, c_wrong], params)

    assert result == "FAILURE"


# ══════════════════════════════════════════════════════════════════════════════
# run() — sub-node failure propagation
# ══════════════════════════════════════════════════════════════════════════════


async def test_buying_client_in_transit_zone_returns_failure():
    """Buying client already in a transit zone (e.g. stuck at Hilda) → FAILURE."""
    for transit_zone in [hp.COMMONS_ZONE, hp.DORM_ZONE, hp.RAVENWOOD_ZONE]:
        client = _depleted_client(zone=transit_zone)
        result = await hp.run(FakeMachine(), [client], _PARAMS)
        assert result == "FAILURE", f"Expected FAILURE for transit zone {transit_zone!r}"
        client.teleport.assert_not_called()


async def test_mark_location_failure_returns_failure():
    client = _depleted_client()
    mark_fail = patch.object(mark_location, "run", new=AsyncMock(return_value="FAILURE"))

    with mark_fail:
        result = await hp.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_home_teleport_failure_returns_failure():
    client = _depleted_client()
    home_fail = patch(
        "bartlebot.nodes.action.handle_potions._teleport_home_and_verify_dorm",
        new=AsyncMock(return_value=False),
    )

    with _MARK_OK, home_fail:
        result = await hp.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_dorm_to_ravenwood_failure_returns_failure():
    client = _depleted_client()
    zone_fail = patch.object(go_to_zone, "run", new=AsyncMock(return_value="FAILURE"))

    with _MARK_OK, _HOME_OK, zone_fail:
        result = await hp.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_ravenwood_to_commons_failure_returns_failure():
    """First go_to_zone (dorm→ravenwood) succeeds; second (ravenwood→commons) fails."""
    client = _depleted_client()
    zone_mixed = patch.object(go_to_zone, "run", new=AsyncMock(side_effect=["SUCCESS", "FAILURE"]))

    with _MARK_OK, _HOME_OK, zone_mixed:
        result = await hp.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_open_potion_shop_failure_returns_failure():
    client = _depleted_client()
    shop_fail = patch(
        "bartlebot.nodes.action.handle_potions._open_potion_shop",
        new=AsyncMock(return_value=False),
    )

    with _MARK_OK, _HOME_OK, _ZONES_OK, shop_fail:
        result = await hp.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_recall_failure_returns_failure():
    client = _depleted_client()
    recall_fail = patch.object(recall_to_marked_location, "run", new=AsyncMock(return_value="FAILURE"))

    with _MARK_OK, _HOME_OK, _ZONES_OK, _SHOP_FN_OK, _BUY_OK, recall_fail:
        result = await hp.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


# ══════════════════════════════════════════════════════════════════════════════
# run() — happy paths
# ══════════════════════════════════════════════════════════════════════════════


async def test_full_refill_happy_path():
    """Single depleted client: complete navigation flow → SUCCESS."""
    client = _depleted_client()

    with _MARK_OK, _HOME_OK, _ZONES_OK, _SHOP_FN_OK, _BUY_OK, _RECALL_OK:
        result = await hp.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"


async def test_only_depleted_clients_enter_refill():
    """Well-stocked client is excluded from the buying group."""
    c_ok = _well_stocked_client()
    c_depleted = _depleted_client()
    buy_mock = AsyncMock()
    buy_patch = patch(
        "bartlebot.nodes.action.handle_potions._buy_potions_for_client",
        new=buy_mock,
    )

    with _MARK_OK, _HOME_OK, _ZONES_OK, _SHOP_FN_OK, buy_patch, _RECALL_OK:
        result = await hp.run(FakeMachine(), [c_ok, c_depleted], _PARAMS)

    assert result == "SUCCESS"
    assert buy_mock.call_count == 1
    assert buy_mock.call_args.args[1] is c_depleted


async def test_go_to_zone_called_twice_for_route():
    """go_to_zone must be called exactly twice (dorm→raven, raven→commons)."""
    client = _depleted_client()
    zone_mock = AsyncMock(return_value="SUCCESS")
    zone_patch = patch.object(go_to_zone, "run", new=zone_mock)

    with _MARK_OK, _HOME_OK, zone_patch, _SHOP_FN_OK, _BUY_OK, _RECALL_OK:
        result = await hp.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert zone_mock.call_count == 2
    # First call navigates FROM dorm, second FROM ravenwood.
    first_call_params = zone_mock.call_args_list[0].args[2]
    second_call_params = zone_mock.call_args_list[1].args[2]
    assert first_call_params["from_zone"] == DORM
    assert second_call_params["from_zone"] == RAVEN


# ══════════════════════════════════════════════════════════════════════════════
# _teleport_home_and_verify_dorm
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

    with none_load, patch.object(hp, "POLL_INTERVAL", 0):
        result = await hp._teleport_home_and_verify_dorm(FakeMachine(), [client])

    assert result is True


async def test_home_wrong_zone_dorm_button_wait_second_press_succeeds():
    """First HOME → non-dorm; waits for dorm button; second HOME → dorm → True."""
    client = FakeClient()
    client.zone_name.side_effect = ["SomeHouse", DORM]
    client.is_loading.side_effect = [True, False, True, False]
    none_load = patch.object(nh, "none_loading", new=AsyncMock(return_value=True))
    btn_enabled = patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=True))

    with none_load, btn_enabled, patch.object(hp, "POLL_INTERVAL", 0):
        result = await hp._teleport_home_and_verify_dorm(FakeMachine(), [client])

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
        patch.object(hp, "COMPASS_TELEPORT_TIMEOUT", 0.01),
        patch.object(hp, "POLL_INTERVAL", 0),
    ):
        result = await hp._teleport_home_and_verify_dorm(FakeMachine(), [client])

    assert result is False


async def test_home_both_attempts_wrong_zone_returns_false():
    """Both HOME presses land in a non-dorm zone → False."""
    client = FakeClient()
    client.zone_name.side_effect = ["SomeHouse", "SomeHouse"]
    client.is_loading.side_effect = [True, False, True, False]
    none_load = patch.object(nh, "none_loading", new=AsyncMock(return_value=True))
    btn_enabled = patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=True))

    with none_load, btn_enabled, patch.object(hp, "POLL_INTERVAL", 0):
        result = await hp._teleport_home_and_verify_dorm(FakeMachine(), [client])

    assert result is False


async def test_home_loading_timeout_still_checks_zone():
    """Client never enters loading screen within timeout; zone check still runs."""
    client = FakeClient()
    client.zone_name.return_value = DORM
    client.is_loading.return_value = False  # never loads
    none_load = patch.object(nh, "none_loading", new=AsyncMock(return_value=True))

    with (
        none_load,
        patch.object(hp, "_HOME_LOADING_TIMEOUT", 0.01),
        patch.object(hp, "POLL_INTERVAL", 0),
    ):
        result = await hp._teleport_home_and_verify_dorm(FakeMachine(), [client])

    # Zone is DORM, so should still succeed.
    assert result is True


async def test_home_repressed_for_lagging_client():
    """Client that doesn't start loading gets a re-press of HOME."""
    c1 = FakeClient()
    c1.zone_name.return_value = DORM
    c1.is_loading.side_effect = [True, True, False]  # starts loading immediately

    c2 = FakeClient()
    c2.zone_name.return_value = DORM
    c2.is_loading.side_effect = [False, True, False]  # needs re-press

    none_load = patch.object(nh, "none_loading", new=AsyncMock(return_value=True))

    with none_load, patch.object(hp, "POLL_INTERVAL", 0):
        result = await hp._teleport_home_and_verify_dorm(FakeMachine(), [c1, c2])

    assert result is True


# ══════════════════════════════════════════════════════════════════════════════
# _open_potion_shop
# ══════════════════════════════════════════════════════════════════════════════


async def test_shop_opens_first_attempt():
    """Teleport → NPC in range → X pressed → shop visible → True."""
    client = FakeClient()
    npc_patch = patch.object(nh, "all_in_npc_range", new=AsyncMock(return_value=True))
    shop_patch = patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=True))

    with npc_patch, shop_patch, patch.object(hp, "POLL_INTERVAL", 0):
        result = await hp._open_potion_shop(FakeMachine(), [client])

    assert result is True
    client.teleport.assert_called()
    x_presses = [c for c in client.send_key.call_args_list if c.args[0] == Keycode.X]
    assert len(x_presses) >= 1


async def test_shop_npc_timeout_resets_and_retries():
    """NPC prompt times out once → reset teleport → next attempt succeeds → True."""
    client = FakeClient()
    # Patch poll_until directly so we control exactly which calls succeed:
    #   call 1: npc range on attempt 1 → False (timeout)
    #   call 2: npc range on attempt 2 → True
    #   call 3: shop visible on attempt 2 → True
    wait_mock = AsyncMock(side_effect=[False, True, True])

    with patch.object(nh, "poll_until", new=wait_mock):
        result = await hp._open_potion_shop(FakeMachine(), [client])

    assert result is True
    # Hilda (attempt 1) + reset + Hilda (attempt 2) = 3 teleports.
    assert client.teleport.call_count == 3


async def test_shop_window_never_opens_returns_false():
    """Shop window never becomes visible after all retries → False."""
    client = FakeClient()
    npc_patch = patch.object(nh, "all_in_npc_range", new=AsyncMock(return_value=True))
    shop_patch = patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=False))

    with (
        npc_patch,
        shop_patch,
        patch.object(hp, "_SHOP_OPEN_TIMEOUT", 0.01),
        patch.object(hp, "POLL_INTERVAL", 0),
    ):
        result = await hp._open_potion_shop(FakeMachine(), [client])

    assert result is False


async def test_shop_opens_on_second_attempt():
    """Shop fails to open first try → resets → opens second try → True."""
    client = FakeClient()
    npc_patch = patch.object(nh, "all_in_npc_range", new=AsyncMock(return_value=True))
    # First wait_until(shop_open): False; second: True.
    shop_patch = patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=[False, True]))

    with (
        npc_patch,
        shop_patch,
        patch.object(hp, "_SHOP_OPEN_TIMEOUT", 0.01),
        patch.object(hp, "POLL_INTERVAL", 0),
    ):
        result = await hp._open_potion_shop(FakeMachine(), [client])

    assert result is True


# ══════════════════════════════════════════════════════════════════════════════
# _buy_potions_for_client
# ══════════════════════════════════════════════════════════════════════════════

_SLEEP = patch("asyncio.sleep", new=AsyncMock())


async def test_buy_already_full_no_clicks():
    """Potion charge already at max → loop exits immediately, nothing clicked."""
    client = FakeClient()
    client.stats.potion_max.return_value = 10
    client.stats.potion_charge.return_value = 10
    click_mock = AsyncMock()
    click_patch = patch.object(nh, "click_by_path", new=click_mock)

    with click_patch, _SLEEP:
        await hp._buy_potions_for_client(FakeMachine(), client)

    click_mock.assert_not_called()


async def test_buy_once_to_full():
    """One purchase brings charge to max — exactly FILL_ALL + BUY clicked once each."""
    client = FakeClient()
    client.stats.potion_max.return_value = 2
    # charge_before(iter1)=1, charge_after(iter1)=2, charge_before(iter2)=2 → full, break.
    client.stats.potion_charge.side_effect = [1, 2, 2]

    click_mock = AsyncMock()
    click_patch = patch.object(nh, "click_by_path", new=click_mock)
    vis_patch = patch.object(
        nh,
        "is_visible_by_path",
        new=AsyncMock(
            side_effect=[
                True,  # open check: shop is open
                False,  # close loop: exits immediately (shop already closed)
            ]
        ),
    )

    with click_patch, vis_patch, _SLEEP:
        await hp._buy_potions_for_client(FakeMachine(), client)

    # click_by_path called for FILL_ALL and BUY (2 calls total).
    assert click_mock.call_count == 2


async def test_buy_stops_on_unchanged_charge():
    """If charge doesn't increase after purchase (out of gold), loop breaks."""
    client = FakeClient()
    client.stats.potion_max.return_value = 10
    # charge_before=5, charge_after=5 — no change
    client.stats.potion_charge.side_effect = [5, 5]

    click_mock = AsyncMock()
    click_patch = patch.object(nh, "click_by_path", new=click_mock)
    vis_patch = patch.object(
        nh,
        "is_visible_by_path",
        new=AsyncMock(
            side_effect=[
                True,  # open check
                False,  # close loop exits
            ]
        ),
    )

    with click_patch, vis_patch, _SLEEP:
        await hp._buy_potions_for_client(FakeMachine(), client)

    # Tried once (FILL_ALL + BUY), then stopped.
    assert click_mock.call_count == 2


async def test_buy_reopens_shop_if_closed_at_start_of_iteration():
    """If shop is closed at the top of a buy loop, sends X to re-open it."""
    client = FakeClient()
    client.stats.potion_max.return_value = 10
    # charge_before=5, charge_after=5 — stops after one attempt
    client.stats.potion_charge.side_effect = [5, 5]

    click_patch = patch.object(nh, "click_by_path", new=AsyncMock())
    vis_patch = patch.object(
        nh,
        "is_visible_by_path",
        new=AsyncMock(
            side_effect=[
                False,  # open check: shop closed → triggers send_key(X) + wait_until
                True,  # wait_until re-open: done
                False,  # close loop exits
            ]
        ),
    )

    with (
        click_patch,
        vis_patch,
        _SLEEP,
        patch.object(hp, "_SHOP_OPEN_TIMEOUT", 0.01),
        patch.object(hp, "POLL_INTERVAL", 0),
    ):
        await hp._buy_potions_for_client(FakeMachine(), client)

    x_presses = [c for c in client.send_key.call_args_list if c.args[0] == Keycode.X]
    assert len(x_presses) >= 1


# ══════════════════════════════════════════════════════════════════════════════
# _all_dorm_button_enabled / _all_shop_open
# ══════════════════════════════════════════════════════════════════════════════


async def test_all_dorm_button_enabled_all_true():
    clients = [FakeClient(), FakeClient()]
    with patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=True)):
        assert await hp._all_dorm_button_enabled(clients) is True


async def test_all_dorm_button_enabled_one_false():
    clients = [FakeClient(), FakeClient()]
    with patch.object(nh, "is_enabled_by_path", new=AsyncMock(side_effect=[True, False])):
        assert await hp._all_dorm_button_enabled(clients) is False


async def test_all_shop_open_all_true():
    clients = [FakeClient(), FakeClient()]
    with patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=True)):
        assert await hp._all_shop_open(clients) is True


async def test_all_shop_open_one_false():
    clients = [FakeClient(), FakeClient()]
    with patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=[True, False])):
        assert await hp._all_shop_open(clients) is False
