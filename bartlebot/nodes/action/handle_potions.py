"""handle_potions node — use potions and refill from Hilda Brewer in the Commons if needed."""

import asyncio

from wizwalker import Keycode, XYZ

import logging
from bartlebot.nodes import node_helpers
from bartlebot.nodes.action import go_to_xyz, go_to_zone, mark_location, recall_to_marked_location
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import (
    GOTO_DORM_BUTTON_PATH,
    POTION_BUY_PATH,
    POTION_EXIT_PATH,
    POTION_FILL_ALL_PATH,
    POTION_SHOP_BASE_PATH,
)

# ── Zone names ─────────────────────────────────────────────────────────────────
DORM_ZONE      = "WizardCity/Interiors/WC_Housing_Dorm_Interior"
RAVENWOOD_ZONE = "WizardCity/WC_Ravenwood"
COMMONS_ZONE   = "WizardCity/WC_Hub"

# ── Navigation coordinates ─────────────────────────────────────────────────────
# Dorm exit teleport bounding box — triggers Dorm → Ravenwood transition.
_DORM_EXIT_XYZ      = {"x": -197.23, "y": 1.41, "z": 0.0}

# Ravenwood exit bounding box — triggers Ravenwood → Commons transition.
_RAVENWOOD_EXIT_XYZ = {"x": -0.7323388457298279, "y": -2200.223388671875, "z": -155.97055053710938}

# Hilda Brewer's location in the Commons (potion vendor).
_HILDA_XYZ = XYZ(-4398.70654296875, 1016.1954345703125, 229.00079345703125)

# Nearby Commons point where Hilda's interact prompt does NOT appear.
_HILDA_RESET_XYZ = XYZ(-3860.94, 1601.97, 229.0)

# ── Timing constants ───────────────────────────────────────────────────────────
from bartlebot.nodes.timing import COMPASS_TELEPORT_TIMEOUT, INTERACT_TIMEOUT, MAX_RETRIES, POLL_INTERVAL
_HOME_LOADING_TIMEOUT = 15.0   # max seconds to wait for a loading screen after HOME.
_SHOP_OPEN_TIMEOUT    = 5.0    # max seconds to wait for the potion shop window.


PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="if_health_below",
        type="float",
        label="Use Potion If Health Below",
        help=(
            "Health ratio (0.0–1.0) below which a client should use a potion. "
            "Set to 0.0 to skip health-based potion use."
        ),
        default=0.5,
        required=True,
    ),
    ParamField(
        key="if_mana_below",
        type="float",
        label="Use Potion If Mana Below",
        help=(
            "Mana ratio (0.0–1.0) below which a client should use a potion. "
            "Set to 0.0 to skip mana-based potion use."
        ),
        default=0.15,
        required=True,
    ),
    ParamField(
        key="refill_potions",
        type="bool",
        label="Refill Potions",
        help=(
            "If enabled, navigate to Hilda Brewer in the Commons to buy potions "
            "when any client has no potion charges remaining."
        ),
        default=True,
        required=True,
    ),
    ParamField(
        key="mark_zone",
        type="str",
        label="Mark Zone",
        help=(
            "Optional. If provided, all clients that need to buy potions must already "
            "be in this zone when handle_potions executes. Returns FAILURE if they are "
            "not. Use this to ensure the mark is placed at a predictable location "
            "(e.g., a dungeon entrance) rather than wherever clients happen to be."
        ),
        default="",
        required=False,
        default_hint="any zone",
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Uses potions on clients below health or mana thresholds. "
        "If refill_potions is enabled and any client has no potion charges remaining, "
        "marks the current location, navigates to Hilda Brewer in the Commons to buy "
        "potions, then recalls back to the mark."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Potions used and/or refilled as needed for all clients."),
        ReturnSpec(
            "FAILURE",
            "Buying clients were not in mark_zone; mark failed; a navigation step failed; "
            "or the potion shop could not be reached.",
        ),
    ],
)


async def run(machine, clients: list, params: dict) -> str:
    """
    Use potions on clients below thresholds and optionally navigate to refill.

    Params:
        if_health_below (float): Use potion if health ratio is below this value.
        if_mana_below   (float): Use potion if mana ratio is below this value.
        refill_potions  (bool):  Navigate to Hilda to buy potions if any client is out.
        mark_zone       (str):   Optional zone precondition for clients that need to buy.
    """
    health_threshold: float = params["if_health_below"]
    mana_threshold:   float = params["if_mana_below"]
    refill:           bool  = params["refill_potions"]
    mark_zone:        str   = params.get("mark_zone", "") or ""

    machine.log(
        f"handle_potions: health_threshold={health_threshold:.2f} "
        f"mana_threshold={mana_threshold:.2f} "
        f"refill={refill} mark_zone={mark_zone!r}"
    )

    # 1. Check health/mana ratios and use potions on low clients that have them.
    health_ratios, mana_ratios = await asyncio.gather(
        asyncio.gather(*[c.calc_health_ratio() for c in clients]),
        asyncio.gather(*[c.calc_mana_ratio() for c in clients]),
    )
    machine.log(
        f"Health: {[round(r, 2) for r in health_ratios]}  "
        f"Mana: {[round(r, 2) for r in mana_ratios]}",
        logging.DEBUG,
    )

    low_clients = [
        c for c, h, m in zip(clients, health_ratios, mana_ratios)
        if (health_threshold > 0.0 and h < health_threshold)
        or (mana_threshold > 0.0 and m < mana_threshold)
    ]
    if low_clients:
        machine.log(
            f"{len(low_clients)} client(s) below threshold — using potions if available",
            logging.DEBUG,
        )
        # Use use_potion() directly — we already decided these clients need a potion.
        # use_potion_if_needed() re-checks with its own hardcoded defaults (20% health,
        # 10% mana) and would skip clients that are below our threshold but above its own.
        # Wrap each call in `async with c.mouse_handler` to activate the mouseless cursor
        # hook — the installed WizSprinter use_potion() omits this activation itself.
        results = await asyncio.gather(*[_use_potion(c) for c in low_clients])
        for c, used in zip(low_clients, results):
            machine.log(
                f"use_potion() → {used!r} for client {getattr(c, 'title', repr(c))}",
                logging.DEBUG,
            )

    # 2. Identify buying_clients: those with no potion charges remaining after use.
    has_potion_flags = await asyncio.gather(*[c.has_potion() for c in clients])
    buying_clients = [c for c, has in zip(clients, has_potion_flags) if not has]
    machine.log(
        f"{len(buying_clients)} client(s) have no potions left",
        logging.DEBUG,
    )

    # 3. Early exit if refill is disabled or everyone still has potions.
    if not refill or not buying_clients:
        machine.log("SUCCESS: no refill needed")
        return "SUCCESS"

    # 4. Precondition: buying_clients must be in mark_zone if one was specified.
    #    Without mark_zone, guard against clients already being in a transit zone
    #    (DORM → RAVENWOOD → COMMONS). If they are, the mark would be placed there,
    #    the node would navigate the full HOME loop back to Commons, and the recall
    #    would return clients to Commons rather than their actual origin.
    _TRANSIT_ZONES = {DORM_ZONE, RAVENWOOD_ZONE, COMMONS_ZONE}
    zones = await asyncio.gather(*[c.zone_name() for c in buying_clients])
    machine.log(f"Buying client zones: {list(zones)}", logging.DEBUG)
    if mark_zone:
        if not all(z == mark_zone for z in zones):
            wrong = [z for z in zones if z != mark_zone]
            machine.log(
                f"FAILURE: buying clients not in mark_zone "
                f"(expected={mark_zone!r}, found={wrong})",
                logging.WARNING,
            )
            return "FAILURE"
    else:
        stuck = [z for z in zones if z in _TRANSIT_ZONES]
        if stuck:
            machine.log(
                f"FAILURE: buying client(s) are already in a transit zone {stuck!r}. "
                f"They may be stuck from a previous failed run. "
                f"Specify mark_zone to enforce a valid origin zone.",
                logging.WARNING,
            )
            return "FAILURE"

    # 5. Snapshot the current zone and position (will be the mark and recall target).
    ref = buying_clients[0]
    current_zone = await ref.zone_name()
    current_pos  = await ref.body.position()
    recall_params = {
        "zone": current_zone,
        "xyz":  {"x": current_pos.x, "y": current_pos.y, "z": current_pos.z},
    }
    machine.log(
        f"Mark/recall point: {current_zone!r} "
        f"XYZ({current_pos.x:.1f}, {current_pos.y:.1f}, {current_pos.z:.1f})",
        logging.DEBUG,
    )

    # 6. Mark location for all buying clients.
    machine.log(f"Marking location for {len(buying_clients)} buying client(s)…")
    if await mark_location.run(machine, buying_clients, recall_params) != "SUCCESS":
        machine.log("FAILURE: mark_location failed", logging.WARNING)
        return "FAILURE"

    # 7. Teleport home and verify dorm zone.
    machine.log("Teleporting buying clients home…")
    if not await _teleport_home_and_verify_dorm(machine, buying_clients):
        machine.log("FAILURE: could not reach dorm after teleporting home", logging.WARNING)
        return "FAILURE"

    # 8. Navigate Dorm → Ravenwood.
    machine.log("Navigating Dorm → Ravenwood…")
    if await go_to_zone.run(
        machine,
        buying_clients,
        {
            "from_zone": DORM_ZONE,
            "to_zone":   RAVENWOOD_ZONE,
            "xyz":       _DORM_EXIT_XYZ,
        },
    ) != "SUCCESS":
        machine.log("FAILURE: Dorm → Ravenwood failed", logging.WARNING)
        return "FAILURE"

    # 9. Navigate Ravenwood → Commons.
    machine.log("Navigating Ravenwood → Commons…")
    if await go_to_zone.run(
        machine,
        buying_clients,
        {
            "from_zone": RAVENWOOD_ZONE,
            "to_zone":   COMMONS_ZONE,
            "xyz":       _RAVENWOOD_EXIT_XYZ,
        },
    ) != "SUCCESS":
        machine.log("FAILURE: Ravenwood → Commons failed", logging.WARNING)
        return "FAILURE"

    # 10. Approach Hilda and open the potion shop.
    machine.log("Opening potion shop at Hilda Brewer…")
    if not await _open_potion_shop(machine, buying_clients):
        machine.log("FAILURE: could not open potion shop", logging.WARNING)
        return "FAILURE"

    # 11. Buy potions for each buying client (parallel; each client manages their own shop window).
    machine.log("Buying potions…")
    await asyncio.gather(*[_buy_potions_for_client(machine, c) for c in buying_clients])

    # 12. Recall all buying clients to the marked location.
    machine.log("Recalling buying clients to marked location…")
    if await recall_to_marked_location.run(machine, buying_clients, recall_params) != "SUCCESS":
        machine.log("FAILURE: recall_to_marked_location failed", logging.WARNING)
        return "FAILURE"

    machine.log(
        f"SUCCESS: potions refilled, {len(buying_clients)} client(s) recalled to {current_zone!r}"
    )
    return "SUCCESS"


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _teleport_home_and_verify_dorm(machine, clients: list) -> bool:
    """
    Press HOME and verify all clients end up in DORM_ZONE.

    Retries once if the first HOME press lands elsewhere (e.g., a non-dorm house).
    Waits out the home-button cooldown between attempts.

    NOTE: Clients with a non-dorm primary residence are expected to end up in the dorm
    on the second HOME press (most houses have a spiral door that returns to the street,
    from which HOME reaches the dorm). The Arcanum Apartment edge case is not handled
    and will result in FAILURE.
    """
    for attempt in range(1, 3):
        machine.log(f"Pressing HOME (attempt {attempt}/2)…", logging.DEBUG)
        await asyncio.gather(*[c.send_key(Keycode.HOME, 0.1) for c in clients])

        # "Ever seen loading" pattern: re-press HOME for slow clients that don't start loading.
        seen_loading: set[int] = set()
        loop_start = asyncio.get_event_loop().time()
        while len(seen_loading) < len(clients):
            is_loading = await asyncio.gather(*[c.is_loading() for c in clients])
            for i, loading in enumerate(is_loading):
                if loading:
                    seen_loading.add(i)
            if len(seen_loading) == len(clients):
                break
            if asyncio.get_event_loop().time() - loop_start > _HOME_LOADING_TIMEOUT:
                still_waiting = [i for i in range(len(clients)) if i not in seen_loading]
                machine.log(f"Clients not loading at timeout: {still_waiting}", logging.DEBUG)
                break
            not_loading = [c for i, c in enumerate(clients) if i not in seen_loading]
            await asyncio.gather(*[c.send_key(Keycode.HOME, 0.1) for c in not_loading])
            await asyncio.sleep(POLL_INTERVAL)

        await node_helpers.wait_until(
            lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL
        )

        zones = await asyncio.gather(*[c.zone_name() for c in clients])
        machine.log(f"Zones after HOME press {attempt}: {list(zones)}", logging.DEBUG)
        if all(z == DORM_ZONE for z in zones):
            return True

        if attempt == 1:
            machine.log(
                f"Not in dorm zone after HOME (got {list(set(zones))}); "
                f"waiting for GotoDormButton to become enabled…",
                logging.DEBUG,
            )
            if not await node_helpers.wait_until(
                lambda: _all_dorm_button_enabled(clients),
                COMPASS_TELEPORT_TIMEOUT,
                POLL_INTERVAL,
            ):
                machine.log(
                    f"FAILURE: GotoDormButton not enabled within {COMPASS_TELEPORT_TIMEOUT}s",
                    logging.WARNING,
                )
                return False

    return False


async def _open_potion_shop(machine, clients: list) -> bool:
    """
    Teleport clients to Hilda Brewer, wait for the interact prompt, press X,
    and confirm the potion shop window opens on all clients.

    Returns True if the shop opened successfully, False after all retries are exhausted.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        machine.log(
            f"Attempt {attempt}/{MAX_RETRIES}: teleporting to Hilda…", logging.DEBUG
        )
        await asyncio.gather(*[c.teleport(_HILDA_XYZ) for c in clients])

        if not await node_helpers.wait_until(
            lambda: node_helpers.all_in_npc_range(clients), INTERACT_TIMEOUT, POLL_INTERVAL
        ):
            machine.log(
                f"Interact prompt did not appear (attempt {attempt}); resetting…", logging.DEBUG
            )
            await asyncio.gather(*[c.teleport(_HILDA_RESET_XYZ) for c in clients])
            continue

        machine.log("Pressing X to open potion shop…", logging.DEBUG)
        await asyncio.gather(*[c.send_key(Keycode.X, 0.1) for c in clients])

        if await node_helpers.wait_until(
            lambda: _all_shop_open(clients), _SHOP_OPEN_TIMEOUT, POLL_INTERVAL
        ):
            return True

        machine.log(
            f"Potion shop did not open (attempt {attempt}); resetting…", logging.DEBUG
        )
        await asyncio.gather(*[c.teleport(_HILDA_RESET_XYZ) for c in clients])

    machine.log(
        f"FAILURE: potion shop never opened after {MAX_RETRIES} attempts",
        logging.WARNING,
    )
    return False


async def _buy_potions_for_client(machine, client) -> None:
    """
    Buy potions for a single client until fully stocked or gold runs out.
    Assumes the potion shop window is already open when called.
    """
    max_potions = await client.stats.potion_max()
    machine.log(f"Buying potions (max={max_potions})…", logging.DEBUG)

    while True:
        charge_before = await client.stats.potion_charge()
        if charge_before >= max_potions:
            machine.log(f"Potions full ({charge_before}/{max_potions})", logging.DEBUG)
            break

        # Re-open the shop window if it closed between iterations.
        if not await node_helpers.is_visible_by_path(client, POTION_SHOP_BASE_PATH):
            machine.log("Shop closed unexpectedly; re-opening…", logging.DEBUG)
            await client.send_key(Keycode.X, 0.1)
            await node_helpers.wait_until(
                lambda: node_helpers.is_visible_by_path(client, POTION_SHOP_BASE_PATH),
                _SHOP_OPEN_TIMEOUT,
                POLL_INTERVAL,
            )

        await node_helpers.click_by_path(client, POTION_FILL_ALL_PATH)
        await asyncio.sleep(0.25)
        await node_helpers.click_by_path(client, POTION_BUY_PATH)
        await asyncio.sleep(0.25)

        while await node_helpers.is_visible_by_path(client, POTION_SHOP_BASE_PATH):
            await node_helpers.click_by_path(client, POTION_EXIT_PATH)
            await asyncio.sleep(0.125)

        charge_after = await client.stats.potion_charge()
        machine.log(f"Charge after buy: {charge_after}/{max_potions}", logging.DEBUG)

        if charge_after == charge_before:
            # Charge unchanged — probably out of gold.
            machine.log(
                "Potion charge unchanged after purchase; stopping (out of gold?)",
                logging.DEBUG,
            )
            break

        # Re-open the shop for the next purchase iteration.
        if charge_after < max_potions:
            await client.send_key(Keycode.X, 0.1)
            await node_helpers.wait_until(
                lambda: node_helpers.is_visible_by_path(client, POTION_SHOP_BASE_PATH),
                _SHOP_OPEN_TIMEOUT,
                POLL_INTERVAL,
            )


async def _use_potion(client) -> bool:
    """Activate the mouseless cursor hook and use a potion for one client."""
    async with client.mouse_handler:
        return await client.use_potion()


async def _all_dorm_button_enabled(clients: list) -> bool:
    """Return True when the GotoDormButton is enabled (not grayed) on all clients."""
    results = await asyncio.gather(
        *[node_helpers.is_enabled_by_path(c, GOTO_DORM_BUTTON_PATH) for c in clients]
    )
    return all(results)


async def _all_shop_open(clients: list) -> bool:
    """Return True when the potion shop window is visible for all clients."""
    results = await asyncio.gather(
        *[node_helpers.is_visible_by_path(c, POTION_SHOP_BASE_PATH) for c in clients]
    )
    return all(results)
