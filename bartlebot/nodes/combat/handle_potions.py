"""handle_potions node — use potions and refill from Hilda Brewer in the Commons if needed."""

import asyncio
import logging

from wizwalker import XYZ, Keycode

from bartlebot import dialog_captions
from bartlebot.nodes import housing, node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import (
    GOTO_DORM_BUTTON_PATH,
    POTION_BUY_PATH,
    POTION_EXIT_PATH,
    POTION_FILL_ALL_PATH,
    POTION_SHOP_BASE_PATH,
)
from bartlebot.nodes.teleportation import mark_location, teleport_to_marked_location
from bartlebot.nodes.zone_entry import enter_zone

# ── Zone names ─────────────────────────────────────────────────────────────────
DORM_ZONE = "WizardCity/Interiors/WC_Housing_Dorm_Interior"
RAVENWOOD_ZONE = "WizardCity/WC_Ravenwood"
COMMONS_ZONE = "WizardCity/WC_Hub"

# ── Navigation coordinates ─────────────────────────────────────────────────────
# Dorm exit teleport bounding box — triggers Dorm → Ravenwood transition.
_DORM_EXIT_XYZ = {"x": -197.23, "y": 1.41, "z": 0.0}

# Ravenwood exit bounding box — triggers Ravenwood → Commons transition.
_RAVENWOOD_EXIT_XYZ = {"x": -0.7323388457298279, "y": -2200.223388671875, "z": -155.97055053710938}

# Hilda Brewer's location in the Commons (potion vendor).
_HILDA_XYZ = XYZ(-4398.70654296875, 1016.1954345703125, 229.00079345703125)

# Nearby Commons point where Hilda's interact prompt does NOT appear.
_HILDA_RESET_XYZ = XYZ(-3860.94, 1601.97, 229.0)

# ── Timing constants ───────────────────────────────────────────────────────────
from bartlebot.nodes.timing import COMPASS_TELEPORT_TIMEOUT, MAX_RETRIES, POLL_INTERVAL

_HOME_LOADING_TIMEOUT = 15.0  # max seconds to wait for a loading screen after HOME.
_SHOP_OPEN_TIMEOUT = 5.0  # max seconds to wait for the potion shop window.
_SHOP_CLICK_DELAY = 0.25  # seconds to wait after clicking Fill All / Buy to let the UI settle.
_EXIT_CLICK_INTERVAL = 0.125  # seconds between repeated Exit clicks while the shop window is still open.


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
            "Mana ratio (0.0–1.0) below which a client should use a potion. Set to 0.0 to skip mana-based potion use."
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
        ReturnSpec("SUCCESS", "Potions used and/or refilled as needed for all players."),
        ReturnSpec(
            "FAILURE",
            "Buying players were not in mark_zone; mark failed; a navigation step failed; "
            "or the potion shop could not be reached.",
        ),
    ],
    required_bot_options=[
        BotOptionSpec(
            name="Safety Delay",
            type="float",
            default=3.0,
            description=(
                "Seconds to allow for game-server responses when waiting for prompts or menus. "
                "Increase on high-latency connections."
            ),
        ),
    ],
    max_consecutive_retries=5,
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: SprintyClient.calc_health_ratio(), SprintyClient.calc_mana_ratio(),
          SprintyClient.use_potion(), SprintyClient.has_potion(),
          mark_location.run(), enter_zone.run(), teleport_to_marked_location.run()

    Flow:
      1. Check health and mana ratios; use potions on clients below threshold that have them.
      2. Identify buying_clients: clients with no potion charges remaining after use.
      3. If refill is disabled or no clients need to buy, return SUCCESS.
      4. Check buying_clients zones:
         a. If all are already in the Commons, open the shop and buy immediately (no mark or
            recall needed). Return SUCCESS or FAILURE from the shop step.
         b. If mark_zone is set, verify all buying_clients are in that zone. Return FAILURE if not.
         c. Otherwise, guard against clients stuck in a transit zone (Dorm, Ravenwood).
            Return FAILURE if any are found there.
      5. Snapshot current zone and position as the mark/recall target.
      6. Call mark_location.run() for buying_clients. Return FAILURE if it fails.
      7. Temporarily unequip any equipped castle, then click GotoDormButton to teleport home;
         retry once if the first press lands in a non-dorm zone. Each attempt only re-presses
         HOME for clients that have not yet reached the dorm. Castles are re-equipped before
         this step returns, whether it succeeded or not. Return FAILURE if any client fails
         to reach the dorm after both attempts.
      8. Call enter_zone.run() Dorm → Ravenwood. Return FAILURE if it fails.
      9. Call enter_zone.run() Ravenwood → Commons. Return FAILURE if it fails.
      10. Teleport to Hilda Brewer, wait for the interact prompt, press X to open the potion
          shop. Return FAILURE if the shop does not open after MAX_RETRIES.
      11. For each buying_client: buy potions until fully stocked or gold runs out.
      12. Call teleport_to_marked_location.run() for buying_clients. Return FAILURE if it fails.

    FAILURE: buying clients not in mark_zone or stuck in a transit zone (Dorm/Ravenwood);
    mark_location failed; could not reach dorm; navigation step failed; potion shop did not
    open after retries; teleport_to_marked_location failed.
    """
    health_threshold: float = params.get("if_health_below", 0.5)
    mana_threshold: float = params.get("if_mana_below", 0.15)
    refill: bool = params.get("refill_potions", True)
    mark_zone: str = params.get("mark_zone", "") or ""

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
        f"Health: {[round(r, 2) for r in health_ratios]}  Mana: {[round(r, 2) for r in mana_ratios]}",
        logging.DEBUG,
    )

    low_clients = [
        c
        for c, h, m in zip(clients, health_ratios, mana_ratios)
        if (health_threshold > 0.0 and h < health_threshold) or (mana_threshold > 0.0 and m < mana_threshold)
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

    # 4. Check buying_clients zones.
    zones = await asyncio.gather(*[c.zone_name() for c in buying_clients])
    machine.log(f"Buying client zones: {list(zones)}", logging.DEBUG)

    # 4a. Commons shortcut: all buying clients are already at Hilda's location —
    #     skip mark, HOME, and navigation; just open the shop and buy in place.
    if all(z == COMMONS_ZONE for z in zones):
        machine.log("Buying clients already in Commons; skipping mark and recall…")
        if not await _open_potion_shop(machine, buying_clients):
            machine.log("FAILURE: could not open potion shop", logging.WARNING)
            return "FAILURE"
        machine.log("Buying potions…")
        await asyncio.gather(*[_buy_potions_for_client(machine, c) for c in buying_clients])
        machine.log(f"SUCCESS: potions refilled in Commons for {len(buying_clients)} client(s)")
        return "SUCCESS"

    # 4b/c. Normal path: verify origin zone before placing the mark.
    #   Without mark_zone, guard against clients already being in a transit zone
    #   (Dorm → Ravenwood). If they are, the mark would be placed there and the
    #   recall would return clients to a transit zone rather than their actual origin.
    _TRANSIT_ZONES = {DORM_ZONE, RAVENWOOD_ZONE}
    if mark_zone:
        if not all(z == mark_zone for z in zones):
            wrong = [z for z in zones if z != mark_zone]
            machine.log(
                f"FAILURE: buying clients not in mark_zone (expected={mark_zone!r}, found={wrong})",
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

    # 5. Snapshot each buying client's current position for independent mark/recall.
    #    Using each client's own position ensures they recall to where they actually were,
    #    not to the first client's position (clients may not be co-located).
    positions = await asyncio.gather(*[c.body.position() for c in buying_clients])
    per_client_recall_params = [
        {"zone": zone, "xyz": {"x": pos.x, "y": pos.y, "z": pos.z}} for zone, pos in zip(zones, positions)
    ]
    for i, p in enumerate(per_client_recall_params):
        machine.log(
            f"Client {i} mark/recall point: {p['zone']!r} "
            f"XYZ({p['xyz']['x']:.1f}, {p['xyz']['y']:.1f}, {p['xyz']['z']:.1f})",
            logging.DEBUG,
        )

    # 6. Mark each buying client at their own current position.
    machine.log(f"Marking location for {len(buying_clients)} buying client(s)…")
    mark_results = await asyncio.gather(
        *[mark_location.run(machine, [c], rp) for c, rp in zip(buying_clients, per_client_recall_params)]
    )
    if not all(r == "SUCCESS" for r in mark_results):
        machine.log("FAILURE: mark_location failed for one or more clients", logging.WARNING)
        return "FAILURE"

    # 7-10. Travel to Hilda Brewer and open her shop.
    if not await navigate_to_potion_vendor(machine, buying_clients):
        return "FAILURE"

    # 11. Buy potions for each buying client (parallel; each client manages their own shop window).
    machine.log("Buying potions…")
    await asyncio.gather(*[_buy_potions_for_client(machine, c) for c in buying_clients])

    # 12. Recall each buying client to their own marked location.
    machine.log("Recalling buying clients to marked location…")
    recall_results = await asyncio.gather(
        *[teleport_to_marked_location.run(machine, [c], rp) for c, rp in zip(buying_clients, per_client_recall_params)]
    )
    if not all(r == "SUCCESS" for r in recall_results):
        machine.log("FAILURE: teleport_to_marked_location failed for one or more clients", logging.WARNING)
        return "FAILURE"

    origin_zones = [rp["zone"] for rp in per_client_recall_params]
    machine.log(f"SUCCESS: potions refilled, {len(buying_clients)} client(s) recalled to {origin_zones}")
    return "SUCCESS"


# ── Navigation ─────────────────────────────────────────────────────────────────


async def navigate_to_potion_vendor(machine, clients: list) -> bool:
    """Take clients from wherever they are to Hilda Brewer, with her shop window open.

    Steps 7-10 of run(): unequip castles and HOME to the dorm, Dorm → Ravenwood, Ravenwood →
    Commons, then approach Hilda and press X. Returns True only when the shop is open on
    every client; logs the reason and returns False otherwise.

    Public because this is the whole interesting part of handle_potions and the part that
    breaks — buying is a few clicks in a window that is already open. tools/dry_run_potion_route.py
    drives exactly this, so the route can be exercised without a client that actually needs
    potions and without spending gold.
    """
    machine.log("Teleporting clients home…")
    if not await teleport_home_and_verify_dorm(machine, clients):
        machine.log("FAILURE: could not reach dorm after teleporting home", logging.WARNING)
        return False

    machine.log("Navigating Dorm → Ravenwood…")
    if (
        await enter_zone.run(
            machine,
            clients,
            {"from_zone": DORM_ZONE, "to_zone": RAVENWOOD_ZONE, "xyz": _DORM_EXIT_XYZ},
        )
        != "SUCCESS"
    ):
        machine.log("FAILURE: Dorm → Ravenwood failed", logging.WARNING)
        return False

    machine.log("Navigating Ravenwood → Commons…")
    if (
        await enter_zone.run(
            machine,
            clients,
            {"from_zone": RAVENWOOD_ZONE, "to_zone": COMMONS_ZONE, "xyz": _RAVENWOOD_EXIT_XYZ},
        )
        != "SUCCESS"
    ):
        machine.log("FAILURE: Ravenwood → Commons failed", logging.WARNING)
        return False

    machine.log("Opening potion shop at Hilda Brewer…")
    if not await _open_potion_shop(machine, clients):
        machine.log("FAILURE: could not open potion shop", logging.WARNING)
        return False

    return True


async def close_potion_shop(client) -> None:
    """Click Exit until the potion shop window is gone.

    One click is not enough: the button can be swallowed while the shop is mid-animation.
    """
    while await node_helpers.is_visible_by_path(client, POTION_SHOP_BASE_PATH):
        await node_helpers.click_by_path(client, POTION_EXIT_PATH)
        await asyncio.sleep(_EXIT_CLICK_INTERVAL)


# ── Helpers ────────────────────────────────────────────────────────────────────


async def teleport_home_and_verify_dorm(machine, clients: list) -> bool:
    """
    Press HOME and verify all clients end up in DORM_ZONE.

    Any client with a castle equipped is unequipped first, so that GotoDormButton lands in
    the dorm on the first press. Without that, such a client lands in its castle instead and
    has to sit out the ~30 s teleport cooldown before a second press — and the Arcanum
    Apartment case never recovers at all, because it has no spiral door back to a street
    from which HOME reaches the dorm. Each castle is restored as soon as its client is in
    the dorm, in a finally block so that a failure anywhere in here cannot leave somebody's
    home unequipped.

    The retry is kept for clients where the unequip did not happen — it is skipped when no
    castle is equipped, and it can fail for its own reasons — so the old slow path is still
    there as a fallback. Each attempt only re-presses HOME for clients not yet confirmed in
    the dorm, so a client that reached the dorm on attempt 1 is never pressed again during
    its HOME cooldown.
    """
    machine.log(f"Unequipping castles for {len(clients)} client(s) so HOME reaches the dorm…", logging.DEBUG)
    castles = await asyncio.gather(*[housing.unequip_castle(c) for c in clients])
    for c, castle in zip(clients, castles):
        if castle:
            machine.log(f"Unequipped {castle!r} for client {getattr(c, 'title', repr(c))}", logging.DEBUG)

    try:
        return await _press_home_until_dorm(machine, clients)
    finally:
        restorable = [(c, castle) for c, castle in zip(clients, castles) if castle]
        if restorable:
            machine.log(f"Re-equipping {len(restorable)} castle(s)…", logging.DEBUG)
            restored = await asyncio.gather(*[housing.equip_castle(c, castle) for c, castle in restorable])
            for (c, castle), ok in zip(restorable, restored):
                if not ok:
                    machine.log(
                        f"Could not re-equip castle {castle!r} for client "
                        f"{getattr(c, 'title', repr(c))} — re-equip it by hand from the housing page (U).",
                        logging.WARNING,
                    )


async def _press_home_until_dorm(machine, clients: list) -> bool:
    """Press HOME, up to twice, until every client is in DORM_ZONE."""
    needs_home = list(clients)
    # Going home from inside a dungeon raises the exit warning, which blocks the teleport
    # until it is answered — clicking the button again behind the modal achieves nothing,
    # so a stuck client gets the answer rather than another press.
    retry_home = node_helpers.ModalAwareRetry(lambda c: node_helpers.click_by_path(c, GOTO_DORM_BUTTON_PATH))

    for attempt in range(1, 3):
        machine.log(f"Clicking GotoDormButton for {len(needs_home)} client(s) (attempt {attempt}/2)…", logging.DEBUG)
        await asyncio.gather(*[node_helpers.click_by_path(c, GOTO_DORM_BUTTON_PATH) for c in needs_home])

        # "Ever seen loading" pattern: re-click for needs_home clients that don't start loading.
        seen_loading: set[int] = set()
        loop_start = asyncio.get_event_loop().time()
        while len(seen_loading) < len(needs_home):
            is_loading = await asyncio.gather(*[c.is_loading() for c in needs_home])
            for i, loading in enumerate(is_loading):
                if loading:
                    seen_loading.add(i)
            if len(seen_loading) == len(needs_home):
                break
            if asyncio.get_event_loop().time() - loop_start > _HOME_LOADING_TIMEOUT:
                still_waiting = [i for i in range(len(needs_home)) if i not in seen_loading]
                machine.log(f"Clients not loading at timeout: {still_waiting}", logging.DEBUG)
                break
            not_loading = [c for i, c in enumerate(needs_home) if i not in seen_loading]
            await asyncio.gather(*[retry_home(c) for c in not_loading])
            if retry_home.refusal:
                machine.log(
                    f"FAILURE: could not go home — {dialog_captions.describe(retry_home.refusal)}",
                    logging.WARNING,
                )
                return False
            await asyncio.sleep(POLL_INTERVAL)

        # Wait for ALL clients to settle (includes clients already in dorm from a prior attempt).
        await node_helpers.poll_until(lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL)

        zones = await asyncio.gather(*[c.zone_name() for c in needs_home])
        machine.log(f"Zones after HOME press {attempt}: {list(zones)}", logging.DEBUG)
        needs_home = [c for c, z in zip(needs_home, zones) if z != DORM_ZONE]

        if not needs_home:
            return True

        if attempt == 1:
            machine.log(
                f"{len(needs_home)} client(s) not in dorm after HOME; waiting for GotoDormButton to become enabled…",
                logging.DEBUG,
            )
            if not await node_helpers.poll_until(
                lambda _c=needs_home: _all_dorm_button_enabled(_c),
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
    safety_delay: float = machine.get_option("Safety Delay", 3.0)
    for attempt in range(1, MAX_RETRIES + 1):
        machine.log(f"Attempt {attempt}/{MAX_RETRIES}: teleporting to Hilda…", logging.DEBUG)
        await asyncio.gather(*[c.teleport(_HILDA_XYZ) for c in clients])

        if not await node_helpers.poll_until(
            lambda: node_helpers.all_in_npc_range(clients), safety_delay, POLL_INTERVAL
        ):
            machine.log(f"Interact prompt did not appear (attempt {attempt}); resetting…", logging.DEBUG)
            await asyncio.gather(*[c.teleport(_HILDA_RESET_XYZ) for c in clients])
            continue

        machine.log("Pressing X to open potion shop…", logging.DEBUG)
        await asyncio.gather(*[c.send_key(Keycode.X, 0.1) for c in clients])

        if await node_helpers.poll_until(lambda: _all_shop_open(clients), _SHOP_OPEN_TIMEOUT, POLL_INTERVAL):
            return True

        machine.log(f"Potion shop did not open (attempt {attempt}); resetting…", logging.DEBUG)
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
            await node_helpers.poll_until(
                lambda: node_helpers.is_visible_by_path(client, POTION_SHOP_BASE_PATH),
                _SHOP_OPEN_TIMEOUT,
                POLL_INTERVAL,
            )

        await node_helpers.click_by_path(client, POTION_FILL_ALL_PATH)
        await asyncio.sleep(_SHOP_CLICK_DELAY)
        await node_helpers.click_by_path(client, POTION_BUY_PATH)
        await asyncio.sleep(_SHOP_CLICK_DELAY)

        await close_potion_shop(client)

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
            await node_helpers.poll_until(
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
    results = await asyncio.gather(*[node_helpers.is_enabled_by_path(c, GOTO_DORM_BUTTON_PATH) for c in clients])
    return all(results)


async def _all_shop_open(clients: list) -> bool:
    """Return True when the potion shop window is visible for all clients."""
    results = await asyncio.gather(*[node_helpers.is_visible_by_path(c, POTION_SHOP_BASE_PATH) for c in clients])
    return all(results)
