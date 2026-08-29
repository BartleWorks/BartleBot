"""The Wizard City travel route every node can fall back to: HOME, then out to Ravenwood.

Pressing HOME is the one move available from anywhere in the Spiral, which makes the dorm
the only reliable fixed point a node can navigate from. Everything that needs to reach a
specific place in Wizard City -- Hilda Brewer for potions, the World Tree for the spiral
door -- starts by getting here.

This lived inside handle_potions until go_to_world needed the same first two steps.
Nothing here is potion-specific; handle_potions continues from Ravenwood to the Commons on
its own.
"""

import asyncio
import logging

from bartlebot import dialog_captions
from bartlebot.nodes import housing, node_helpers
from bartlebot.nodes.paths import GOTO_DORM_BUTTON_PATH
from bartlebot.nodes.timing import COMPASS_TELEPORT_TIMEOUT, POLL_INTERVAL
from bartlebot.nodes.zone_entry import enter_zone

# ── Zone names ─────────────────────────────────────────────────────────────────
DORM_ZONE = "WizardCity/Interiors/WC_Housing_Dorm_Interior"
RAVENWOOD_ZONE = "WizardCity/WC_Ravenwood"
COMMONS_ZONE = "WizardCity/WC_Hub"

# ── Navigation coordinates ─────────────────────────────────────────────────────
# Dorm exit teleport bounding box — triggers Dorm → Ravenwood transition.
DORM_EXIT_XYZ = {"x": -197.23, "y": 1.41, "z": 0.0}

# Ravenwood exit bounding box — triggers Ravenwood → Commons transition.
RAVENWOOD_EXIT_XYZ = {"x": -0.7323388457298279, "y": -2200.223388671875, "z": -155.97055053710938}

_HOME_LOADING_TIMEOUT = 15.0  # max seconds to wait for a loading screen after HOME.


async def go_to_ravenwood(machine, clients: list) -> bool:
    """Take clients from wherever they are to Ravenwood. Logs the reason and returns False on failure.

    Ravenwood is the junction the rest of Wizard City hangs off: the Commons is one zone
    further out, the World Tree one zone in. Callers continue from here.
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
            {"from_zone": DORM_ZONE, "to_zone": RAVENWOOD_ZONE, "xyz": DORM_EXIT_XYZ},
        )
        != "SUCCESS"
    ):
        machine.log("FAILURE: Dorm → Ravenwood failed", logging.WARNING)
        return False

    return True


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


async def _all_dorm_button_enabled(clients: list) -> bool:
    """Return True when the GotoDormButton is enabled (not grayed) on all clients."""
    results = await asyncio.gather(*[node_helpers.is_enabled_by_path(c, GOTO_DORM_BUTTON_PATH) for c in clients])
    return all(results)
