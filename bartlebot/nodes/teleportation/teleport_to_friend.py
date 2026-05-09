"""teleport_to_friend node — teleport follower clients to a named friend."""

import asyncio
import logging

from wizwalker.extensions.scripting import teleport_to_friend_from_list

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import MESSAGE_BOX_RIGHT
from bartlebot.nodes.timing import POLL_INTERVAL, TRIGGER_TIMEOUT, ZONE_CHANGE_TIMEOUT

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="friend_name",
        type="str",
        label="Friend Name",
        help="Wizard name to search for in the friends list (case-insensitive). Must be online and on each player's friends list.",
        default="",
        required=True,
    ),
    ParamField(
        key="to_zone",
        type="str",
        label="Destination Zone",
        help="Expected zone name after teleport. Players already in this zone are skipped. Used to verify arrival.",
        default="",
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Teleports one or more players to a named friend using the in-game friends list. "
        "Players already in the destination zone are skipped. "
        "Handles the friend-busy error dialog and verifies arrival zone."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players are now in to_zone."),
        ReturnSpec(
            "FAILURE",
            "Friend not found or not online; the friend-busy dialog appeared "
            "(friend has teleports off, is in combat, or the instance is unavailable); "
            "players did not enter a loading screen within the trigger timeout; "
            "or not all players arrived in to_zone after loading.",
        ),
    ],
    max_consecutive_retries=5,
    safety_warning=(
        "This bot teleports you to a friend. You must have the following player on your "
        "friends list or change the friend name in the teleport_to_friend node settings."
    ),
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.is_loading(),
          wizwalker.extensions.scripting.teleport_to_friend_from_list()

    Flow:
      1. Check if all clients are already in to_zone. Return SUCCESS immediately if so.
      2. Filter to only clients not yet in to_zone.
      3. Call teleport_to_friend_from_list (wrapped in mouse_handler context) for all
         clients_to_move simultaneously. ValueError (friend not found/offline) → FAILURE.
      4. "Ever seen" loading loop: on each tick, check each not-yet-loading client for the
         friend-busy dialog — if visible, dismiss it and return FAILURE. Record newly-loading
         clients. Re-attempt teleport for stragglers after TRIGGER_TIMEOUT has elapsed.
         Return FAILURE if ZONE_CHANGE_TIMEOUT expires.
      5. Wait for all loading screens to finish.
      6. Wait for all clients (including those already in to_zone) to arrive in to_zone.
         Return FAILURE if any are not.

    FAILURE: friend not found or not online; friend-busy dialog appeared (friend has teleports
    off, is in combat, or the instance is unavailable); loading not detected within
    ZONE_CHANGE_TIMEOUT; not all clients arrived in to_zone.
    """
    friend_name: str = params["friend_name"].lower()
    to_zone: str = params["to_zone"]

    machine.log(f"teleport_to_friend: friend={friend_name!r} to_zone={to_zone!r}")

    # 1. Early exit if all clients already in to_zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current zones: {list(zones)}", logging.DEBUG)
    if all(z == to_zone for z in zones):
        machine.log(f"SUCCESS: all clients already in {to_zone!r}")
        return "SUCCESS"

    clients_to_move = [c for c, z in zip(clients, zones) if z != to_zone]
    machine.log(f"{len(clients_to_move)} of {len(clients)} client(s) will teleport", logging.DEBUG)

    # 2. Initiate teleport on all clients_to_move simultaneously.
    #    teleport_to_friend_from_list uses mouse_handler.click_window() internally,
    #    so the mouse handler context must be active for each client.
    machine.log("Calling teleport_to_friend_from_list…", logging.DEBUG)
    try:
        await asyncio.gather(*[_teleport_with_mouse(c, friend_name) for c in clients_to_move])
    except ValueError as exc:
        machine.log(f"FAILURE: {exc}", logging.WARNING)
        return "FAILURE"

    # 3. "Ever seen" loading loop.
    #    Cannot use poll_each_until_seen here because we must check for the
    #    friend-busy dialog on every tick before re-attempting the teleport.
    seen_loading: set[int] = set()
    loop_start = asyncio.get_event_loop().time()
    # Wait a full TRIGGER_TIMEOUT before re-attempting clients that haven't started
    # loading yet — they may simply be slow to respond to the initial teleport click.
    # ZONE_CHANGE_TIMEOUT is the hard ceiling for the entire loading-detection loop.
    straggler_after = loop_start + TRIGGER_TIMEOUT

    while True:
        # Check each not-yet-loading client for the friend-busy dialog first.
        # If the dialog is visible, the friend is unreachable — dismiss and fail.
        for i, c in enumerate(clients_to_move):
            if i in seen_loading:
                continue
            if await node_helpers.is_visible_by_path(c, MESSAGE_BOX_RIGHT):
                machine.log(
                    f"FAILURE: friend-busy dialog on client {i} — friend has teleports off, "
                    "is in combat, or the instance is no longer accessible",
                    logging.WARNING,
                )
                await node_helpers.click_by_path(c, MESSAGE_BOX_RIGHT)
                return "FAILURE"

        # Record newly-loading clients.
        loading_flags = await asyncio.gather(*[c.is_loading() for c in clients_to_move])
        for i, is_now_loading in enumerate(loading_flags):
            if is_now_loading:
                seen_loading.add(i)

        if len(seen_loading) == len(clients_to_move):
            break

        now = asyncio.get_event_loop().time()
        if now - loop_start > ZONE_CHANGE_TIMEOUT:
            not_triggered = [i for i in range(len(clients_to_move)) if i not in seen_loading]
            machine.log(f"Clients not loading at timeout: {not_triggered}", logging.DEBUG)
            machine.log(
                f"FAILURE: {len(not_triggered)} client(s) did not enter a loading screen "
                f"within {ZONE_CHANGE_TIMEOUT:.0f}s",
                logging.WARNING,
            )
            return "FAILURE"

        # Re-attempt for stragglers only after the initial grace period has elapsed.
        if now >= straggler_after:
            stragglers = [c for i, c in enumerate(clients_to_move) if i not in seen_loading]
            try:
                await asyncio.gather(*[_teleport_with_mouse(c, friend_name) for c in stragglers])
            except ValueError as exc:
                machine.log(f"FAILURE: {exc} (during straggler re-attempt)", logging.WARNING)
                return "FAILURE"

        await asyncio.sleep(POLL_INTERVAL)

    # 4. Wait for all loading screens to finish.
    machine.log("Loading detected; waiting for screens to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients_to_move), None, POLL_INTERVAL)

    # 5. Verify all clients (including those already in to_zone) arrived.
    actual_zones = await node_helpers.wait_for_zone(clients, to_zone, ZONE_CHANGE_TIMEOUT, POLL_INTERVAL)
    if actual_zones is not None:
        machine.log(f"Zones after teleport: {actual_zones}", logging.DEBUG)
        machine.log(
            f"FAILURE: not all clients in expected zone (expected={to_zone!r}, got={list(set(actual_zones))})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients in {to_zone!r}")
    return "SUCCESS"


async def _teleport_with_mouse(client, friend_name_lower: str) -> None:
    """Resolve the exact-case friend name, then teleport via the in-game friends list.

    teleport_to_friend_from_list uses a case-sensitive string comparison internally,
    so we must look up the exact-case name ourselves before delegating to it.
    The mouse handler context is activated here because both the resolution step and
    WizWalker's click chain require it.
    Raises ValueError if no friends are online or the name is not found.
    """
    async with client.mouse_handler:
        exact_name = await _resolve_exact_friend_name(client, friend_name_lower)
        await teleport_to_friend_from_list(client, name=exact_name)


async def _resolve_exact_friend_name(client, friend_name_lower: str) -> str:
    """Return the exact-case friend name matching the lowercased search term.

    Opens the online friends list (if not already open) and scans the full list text
    with a case-insensitive comparison.  The list window's text contains all friends
    regardless of the current scroll position, so pagination is not a concern here.

    Raises ValueError if no friends are online or no entry matches the search term.
    """
    # Import WizWalker internals needed to navigate the friends UI.
    from wizwalker.extensions.scripting.utils import (  # noqa: PLC0415
        _cycle_to_online_friends,
        _friend_list_entry,
        _maybe_get_named_window,
    )

    # Open the friends list window if it isn't already visible.
    try:
        friends_window = await _maybe_get_named_window(client.root_window, "NewFriendsListWindow")
        if not await friends_window.is_visible():
            btn = await _maybe_get_named_window(client.root_window, "btnFriends")
            await client.mouse_handler.click_window(btn)
    except ValueError:
        btn = await _maybe_get_named_window(client.root_window, "btnFriends")
        await client.mouse_handler.click_window(btn)
        friends_window = await _maybe_get_named_window(client.root_window, "NewFriendsListWindow")

    await _cycle_to_online_friends(client, friends_window)

    friends_list = await _maybe_get_named_window(friends_window, "listFriends")
    list_text = await friends_list.maybe_text()

    if not list_text:
        raise ValueError("No friends online")

    for entry in _friend_list_entry.finditer(list_text):
        if entry.group("name").lower() == friend_name_lower:
            return entry.group("name")

    raise ValueError(f"Could not find friend matching {friend_name_lower!r} in online friends list")
