"""Unit tests for the handle_potions node."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode

import bartlebot.nodes.combat.handle_potions as hp
import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.wizard_city as wc
from bartlebot.nodes.teleportation import mark_location, teleport_to_marked_location
from bartlebot.nodes.zone_entry import enter_zone
from tests.conftest import FakeClient, FakeMachine

# ── Zone aliases ─────────────────────────────────────────────────────────────
DORM = hp.DORM_ZONE
RAVEN = hp.RAVENWOOD_ZONE
COMMONS = hp.COMMONS_ZONE

# ── Default params ────────────────────────────────────────────────────────────
_PARAMS = {
    "if_health_below": 0.5,
    "if_mana_below": 0.15,
    "mark_zone": "",
}


def _machine(refill: bool = True) -> FakeMachine:
    """A machine whose Refill Potions resolves to ``refill``, on as the app setting is by default."""
    return FakeMachine(options={hp.OPTION_REFILL: refill})


# ── Re-usable sub-node patches ────────────────────────────────────────────────
_MARK_OK = patch.object(mark_location, "run", new=AsyncMock(return_value="SUCCESS"))
_RECALL_OK = patch.object(teleport_to_marked_location, "run", new=AsyncMock(return_value="SUCCESS"))
_ZONES_OK = patch.object(enter_zone, "run", new=AsyncMock(return_value="SUCCESS"))

# Patch the travel helpers so run() tests don't need to drive their internals. These are
# reached through navigate_to_potion_vendor(), which run() calls; patching the module
# attributes still intercepts them because it looks them up by global name.
#
# HOME lives in wizard_city now, one level further down (go_to_ravenwood calls it), which
# is why this one is patched there rather than on handle_potions. Its own tests are in
# test_wizard_city.py.
_HOME_OK = patch(
    "bartlebot.nodes.wizard_city.teleport_home_and_verify_dorm",
    new=AsyncMock(return_value=True),
)
_SHOP_FN_OK = patch(
    "bartlebot.nodes.combat.handle_potions._open_potion_shop",
    new=AsyncMock(return_value=True),
)
_BUY_OK = patch(
    "bartlebot.nodes.combat.handle_potions._buy_potions_for_client",
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

    result = await hp.run(_machine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.use_potion.assert_not_called()


async def test_health_below_threshold_uses_potion():
    """Client below health threshold → use_potion called."""
    client = _well_stocked_client()
    client.calc_health_ratio.return_value = 0.3  # below 0.5

    result = await hp.run(_machine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.use_potion.assert_called_once()


async def test_mana_below_threshold_uses_potion():
    """Client below mana threshold → use_potion called."""
    client = _well_stocked_client()
    client.calc_mana_ratio.return_value = 0.05  # below 0.15

    result = await hp.run(_machine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.use_potion.assert_called_once()


async def test_zero_thresholds_skip_potion_use():
    """if_health_below=0 and if_mana_below=0 → no potion use regardless of ratios."""
    client = _well_stocked_client()
    client.calc_health_ratio.return_value = 0.0
    client.calc_mana_ratio.return_value = 0.0
    params = {**_PARAMS, "if_health_below": 0.0, "if_mana_below": 0.0}

    result = await hp.run(_machine(), [client], params)

    assert result == "SUCCESS"
    client.use_potion.assert_not_called()


async def test_only_low_clients_use_potions():
    """Only clients below threshold receive a potion use call."""
    c_ok = _well_stocked_client()
    c_low = _well_stocked_client()
    c_low.calc_health_ratio.return_value = 0.1

    result = await hp.run(_machine(), [c_ok, c_low], _PARAMS)

    assert result == "SUCCESS"
    c_ok.use_potion.assert_not_called()
    c_low.use_potion.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# run() — refill early exits
# ══════════════════════════════════════════════════════════════════════════════


async def test_refill_disabled_no_potions_still_success():
    """Refill Potions off and client has no potions → SUCCESS, no navigation."""
    client = _depleted_client()

    result = await hp.run(_machine(refill=False), [client], _PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_not_called()


async def test_everyone_has_potions_no_refill():
    """All clients have potions → SUCCESS without entering refill flow."""
    clients = [_well_stocked_client(), _well_stocked_client()]

    result = await hp.run(_machine(), clients, _PARAMS)

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
        result = await hp.run(_machine(), [client], params)

    assert result == "SUCCESS"


async def test_mark_zone_wrong_returns_failure():
    """Buying client not in mark_zone → FAILURE before any navigation."""
    client = _depleted_client(zone="OtherZone")
    params = {**_PARAMS, "mark_zone": "MyDungeon"}

    result = await hp.run(_machine(), [client], params)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_mark_zone_empty_skips_zone_check():
    """mark_zone='' → no zone precondition checked → proceeds to refill."""
    client = _depleted_client(zone="AnyZone")

    with _MARK_OK, _HOME_OK, _ZONES_OK, _SHOP_FN_OK, _BUY_OK, _RECALL_OK:
        result = await hp.run(_machine(), [client], _PARAMS)

    assert result == "SUCCESS"


async def test_mixed_clients_only_some_in_wrong_zone_fails():
    """Two buying clients, one in wrong zone → FAILURE."""
    c_ok = _depleted_client(zone="MyDungeon")
    c_wrong = _depleted_client(zone="OtherZone")
    params = {**_PARAMS, "mark_zone": "MyDungeon"}

    result = await hp.run(_machine(), [c_ok, c_wrong], params)

    assert result == "FAILURE"


# ══════════════════════════════════════════════════════════════════════════════
# run() — sub-node failure propagation
# ══════════════════════════════════════════════════════════════════════════════


async def test_buying_client_in_transit_zone_returns_failure():
    """Buying client stuck in Dorm or Ravenwood (mid-navigation) → FAILURE."""
    for transit_zone in [hp.DORM_ZONE, hp.RAVENWOOD_ZONE]:
        client = _depleted_client(zone=transit_zone)
        result = await hp.run(_machine(), [client], _PARAMS)
        assert result == "FAILURE", f"Expected FAILURE for transit zone {transit_zone!r}"
        client.teleport.assert_not_called()


async def test_buying_client_in_commons_opens_shop_no_navigate():
    """Buying clients already in Commons → skip mark/recall, open shop, buy → SUCCESS."""
    client = _depleted_client(zone=COMMONS)
    mark_mock = AsyncMock(return_value="SUCCESS")

    with patch.object(mark_location, "run", new=mark_mock), _SHOP_FN_OK, _BUY_OK:
        result = await hp.run(_machine(), [client], _PARAMS)

    assert result == "SUCCESS"
    mark_mock.assert_not_called()


async def test_buying_client_in_commons_shop_failure_returns_failure():
    """Buying clients in Commons but potion shop fails to open → FAILURE."""
    client = _depleted_client(zone=COMMONS)
    shop_fail = patch(
        "bartlebot.nodes.combat.handle_potions._open_potion_shop",
        new=AsyncMock(return_value=False),
    )

    with shop_fail:
        result = await hp.run(_machine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_mark_location_failure_returns_failure():
    client = _depleted_client()
    mark_fail = patch.object(mark_location, "run", new=AsyncMock(return_value="FAILURE"))

    with mark_fail:
        result = await hp.run(_machine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_home_teleport_failure_returns_failure():
    client = _depleted_client()
    home_fail = patch(
        "bartlebot.nodes.wizard_city.teleport_home_and_verify_dorm",
        new=AsyncMock(return_value=False),
    )

    with _MARK_OK, home_fail:
        result = await hp.run(_machine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_dorm_to_ravenwood_failure_returns_failure():
    client = _depleted_client()
    zone_fail = patch.object(enter_zone, "run", new=AsyncMock(return_value="FAILURE"))

    with _MARK_OK, _HOME_OK, zone_fail:
        result = await hp.run(_machine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_ravenwood_to_commons_failure_returns_failure():
    """First enter_zone (dorm→ravenwood) succeeds; second (ravenwood→commons) fails."""
    client = _depleted_client()
    zone_mixed = patch.object(enter_zone, "run", new=AsyncMock(side_effect=["SUCCESS", "FAILURE"]))

    with _MARK_OK, _HOME_OK, zone_mixed:
        result = await hp.run(_machine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_open_potion_shop_failure_returns_failure():
    client = _depleted_client()
    shop_fail = patch(
        "bartlebot.nodes.combat.handle_potions._open_potion_shop",
        new=AsyncMock(return_value=False),
    )

    with _MARK_OK, _HOME_OK, _ZONES_OK, shop_fail:
        result = await hp.run(_machine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_recall_failure_returns_failure():
    client = _depleted_client()
    recall_fail = patch.object(teleport_to_marked_location, "run", new=AsyncMock(return_value="FAILURE"))

    with _MARK_OK, _HOME_OK, _ZONES_OK, _SHOP_FN_OK, _BUY_OK, recall_fail:
        result = await hp.run(_machine(), [client], _PARAMS)

    assert result == "FAILURE"


# ══════════════════════════════════════════════════════════════════════════════
# run() — happy paths
# ══════════════════════════════════════════════════════════════════════════════


async def test_full_refill_happy_path():
    """Single depleted client: complete navigation flow → SUCCESS."""
    client = _depleted_client()

    with _MARK_OK, _HOME_OK, _ZONES_OK, _SHOP_FN_OK, _BUY_OK, _RECALL_OK:
        result = await hp.run(_machine(), [client], _PARAMS)

    assert result == "SUCCESS"


async def test_only_depleted_clients_enter_refill():
    """Well-stocked client is excluded from the buying group."""
    c_ok = _well_stocked_client()
    c_depleted = _depleted_client()
    buy_mock = AsyncMock()
    buy_patch = patch(
        "bartlebot.nodes.combat.handle_potions._buy_potions_for_client",
        new=buy_mock,
    )

    with _MARK_OK, _HOME_OK, _ZONES_OK, _SHOP_FN_OK, buy_patch, _RECALL_OK:
        result = await hp.run(_machine(), [c_ok, c_depleted], _PARAMS)

    assert result == "SUCCESS"
    assert buy_mock.call_count == 1
    assert buy_mock.call_args.args[1] is c_depleted


async def test_multi_client_refill_uses_per_client_positions():
    """Two buying clients at different positions: mark and recall use each client's own XYZ."""
    c0 = _depleted_client(zone="MyDungeon")
    c0.body.position.return_value = XYZ(100.0, 200.0, 0.0)
    c1 = _depleted_client(zone="MyDungeon")
    c1.body.position.return_value = XYZ(500.0, 600.0, 0.0)
    params = {**_PARAMS, "mark_zone": "MyDungeon"}

    mark_calls: list[tuple] = []

    async def capture_mark(_machine, clients, rp):
        mark_calls.append((clients[0], dict(rp)))
        return "SUCCESS"

    recall_calls: list[tuple] = []

    async def capture_recall(_machine, clients, rp):
        recall_calls.append((clients[0], dict(rp)))
        return "SUCCESS"

    with (
        patch.object(mark_location, "run", new=capture_mark),
        patch.object(teleport_to_marked_location, "run", new=capture_recall),
        _HOME_OK,
        _ZONES_OK,
        _SHOP_FN_OK,
        _BUY_OK,
    ):
        result = await hp.run(_machine(), [c0, c1], params)

    assert result == "SUCCESS"
    assert len(mark_calls) == 2
    assert mark_calls[0][0] is c0
    assert mark_calls[0][1]["xyz"] == {"x": 100.0, "y": 200.0, "z": 0.0}
    assert mark_calls[1][0] is c1
    assert mark_calls[1][1]["xyz"] == {"x": 500.0, "y": 600.0, "z": 0.0}
    assert len(recall_calls) == 2
    assert recall_calls[0][1]["xyz"] == {"x": 100.0, "y": 200.0, "z": 0.0}
    assert recall_calls[1][1]["xyz"] == {"x": 500.0, "y": 600.0, "z": 0.0}


async def test_enter_zone_called_twice_for_route():
    """enter_zone must be called exactly twice (dorm→raven, raven→commons)."""
    client = _depleted_client()
    zone_mock = AsyncMock(return_value="SUCCESS")
    zone_patch = patch.object(enter_zone, "run", new=zone_mock)

    with _MARK_OK, _HOME_OK, zone_patch, _SHOP_FN_OK, _BUY_OK, _RECALL_OK:
        result = await hp.run(_machine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert zone_mock.call_count == 2
    # First call navigates FROM dorm, second FROM ravenwood.
    first_call_params = zone_mock.call_args_list[0].args[2]
    second_call_params = zone_mock.call_args_list[1].args[2]
    assert first_call_params["from_zone"] == DORM
    assert second_call_params["from_zone"] == RAVEN


# ══════════════════════════════════════════════════════════════════════════════
# navigate_to_potion_vendor / close_potion_shop — the public route API, which
# tools/dry_run_potion_route.py drives so the route can be tested without buying
# ══════════════════════════════════════════════════════════════════════════════


async def test_navigate_runs_the_whole_route_in_order():
    client = FakeClient()
    order: list[str] = []

    async def record_home(machine, clients):
        order.append("home")
        return True

    async def record_zone(machine, clients, params):
        order.append(params["to_zone"])
        return "SUCCESS"

    async def record_shop(machine, clients):
        order.append("shop")
        return True

    home = patch.object(wc, "teleport_home_and_verify_dorm", new=AsyncMock(side_effect=record_home))
    zones = patch.object(enter_zone, "run", new=AsyncMock(side_effect=record_zone))
    shop = patch.object(hp, "_open_potion_shop", new=AsyncMock(side_effect=record_shop))

    with home, zones, shop:
        assert await hp.navigate_to_potion_vendor(_machine(), [client]) is True

    assert order == ["home", RAVEN, COMMONS, "shop"]


async def test_navigate_stops_at_the_first_failed_step():
    """A failed dorm hop must not go on to walk the Ravenwood route from the wrong zone."""
    client = FakeClient()
    home = patch.object(wc, "teleport_home_and_verify_dorm", new=AsyncMock(return_value=False))
    zones = patch.object(enter_zone, "run", new=AsyncMock(return_value="SUCCESS"))

    with home, zones as z:
        assert await hp.navigate_to_potion_vendor(_machine(), [client]) is False

    z.assert_not_awaited()


async def test_navigate_reports_failure_when_the_shop_will_not_open():
    client = FakeClient()
    home = patch.object(wc, "teleport_home_and_verify_dorm", new=AsyncMock(return_value=True))
    zones = patch.object(enter_zone, "run", new=AsyncMock(return_value="SUCCESS"))
    shop = patch.object(hp, "_open_potion_shop", new=AsyncMock(return_value=False))

    with home, zones, shop:
        assert await hp.navigate_to_potion_vendor(_machine(), [client]) is False


async def test_close_potion_shop_clicks_until_the_window_is_gone():
    """One click can be swallowed mid-animation, so it repeats until the shop reports closed."""
    client = FakeClient()
    visible = patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=[True, True, False]))
    click = patch.object(nh, "click_by_path", new=AsyncMock(return_value=True))

    with visible, click as c, patch.object(hp, "SHOP_EXIT_CLICK_INTERVAL", 0):
        await hp.close_potion_shop(client)

    assert c.await_count == 2


async def test_close_potion_shop_does_nothing_when_already_closed():
    client = FakeClient()
    visible = patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=False))
    click = patch.object(nh, "click_by_path", new=AsyncMock(return_value=True))

    with visible, click as c:
        await hp.close_potion_shop(client)

    c.assert_not_awaited()


# ══════════════════════════════════════════════════════════════════════════════
# _open_potion_shop
# ══════════════════════════════════════════════════════════════════════════════


async def test_shop_opens_first_attempt():
    """Teleport → NPC in range → X pressed → shop visible → True."""
    client = FakeClient()
    npc_patch = patch.object(nh, "all_in_npc_range", new=AsyncMock(return_value=True))
    shop_patch = patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=True))

    with npc_patch, shop_patch, patch.object(hp, "POLL_INTERVAL", 0):
        result = await hp._open_potion_shop(_machine(), [client])

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
        result = await hp._open_potion_shop(_machine(), [client])

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
        patch.object(hp, "SHOP_OPEN_TIMEOUT", 0.01),
        patch.object(hp, "POLL_INTERVAL", 0),
    ):
        result = await hp._open_potion_shop(_machine(), [client])

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
        patch.object(hp, "SHOP_OPEN_TIMEOUT", 0.01),
        patch.object(hp, "POLL_INTERVAL", 0),
    ):
        result = await hp._open_potion_shop(_machine(), [client])

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
        await hp._buy_potions_for_client(_machine(), client)

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
        await hp._buy_potions_for_client(_machine(), client)

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
        await hp._buy_potions_for_client(_machine(), client)

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
        patch.object(hp, "SHOP_OPEN_TIMEOUT", 0.01),
        patch.object(hp, "POLL_INTERVAL", 0),
    ):
        await hp._buy_potions_for_client(_machine(), client)

    x_presses = [c for c in client.send_key.call_args_list if c.args[0] == Keycode.X]
    assert len(x_presses) >= 1


# ══════════════════════════════════════════════════════════════════════════════
# _all_dorm_button_enabled / _all_shop_open
# ══════════════════════════════════════════════════════════════════════════════


async def test_all_shop_open_all_true():
    clients = [FakeClient(), FakeClient()]
    with patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=True)):
        assert await hp._all_shop_open(clients) is True


async def test_all_shop_open_one_false():
    clients = [FakeClient(), FakeClient()]
    with patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=[True, False])):
        assert await hp._all_shop_open(clients) is False


# ══════════════════════════════════════════════════════════════════════════════
# Refill Potions — the user's call, resolved as a bot option
# ══════════════════════════════════════════════════════════════════════════════


def test_refill_potions_is_an_option_the_app_setting_answers():
    """Declared null, so the author has no opinion and the user's app setting decides."""
    from bartlebot import bot_options
    from bartlebot.settings_manager import _DEFAULTS

    spec = next(o for o in hp.NODE_SPEC.required_bot_options if o.name == hp.OPTION_REFILL)

    assert spec.default is None
    assert spec.type == "bool"
    assert bot_options.APP_SETTING_KEYS[hp.OPTION_REFILL] == "default_refill_potions"
    assert _DEFAULTS["general"]["default_refill_potions"] is True
    assert "refill_potions" not in {f.key for f in hp.NODE_SPEC.params}


def test_refill_potions_resolves_to_the_app_setting_when_nothing_outranks_it():
    from bartlebot import bot_options

    assert bot_options.resolve_option(hp.OPTION_REFILL, declaration=None, app_value=False).value is False
    assert bot_options.resolve_option(hp.OPTION_REFILL, override=True, app_value=False).value is True


# ── Challenge Mode ────────────────────────────────────────────────────────────

_THRONE_ROOM_BOT = SimpleNamespace(
    states=[
        SimpleNamespace(
            node_type="enter_dungeon",
            params={"dungeon_zone": "Marleybone/G14_Gauntlet/MB_G14_ThroneRoomB", "offers_challenge_mode": True},
        )
    ]
)


async def test_challenge_mode_reads_health_against_the_scaled_max():
    """Back from a level 40 Challenge Mode dungeon at 60% of the scaled max: no potion, though it reads 12%."""
    client = _well_stocked_client()
    client.calc_health_ratio.return_value = 0.6 * 2690 / 13413

    machine = FakeMachine(bot_config=_THRONE_ROOM_BOT, options={hp.OPTION_REFILL: True, "Challenge Mode": True})
    result = await hp.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    client.use_potion.assert_not_called()


async def test_the_same_health_without_challenge_mode_uses_a_potion():
    client = _well_stocked_client()
    client.calc_health_ratio.return_value = 0.6 * 2690 / 13413

    machine = FakeMachine(bot_config=_THRONE_ROOM_BOT, options={hp.OPTION_REFILL: True, "Challenge Mode": False})
    await hp.run(machine, [client], _PARAMS)

    client.use_potion.assert_called_once()
