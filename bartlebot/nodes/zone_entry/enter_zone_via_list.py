"""Node: interact with a zone-selection list object and teleport to the chosen zone."""

import asyncio
import difflib
import logging

from wizwalker import XYZ, Keycode

from bartlebot import dialog_captions
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import (
    UNIVERSE_CANCEL_BUTTON,
    UNIVERSE_NEXT_PAGE,
    UNIVERSE_TELEPORT_BUTTON,
    universe_zone_option_path,
)
from bartlebot.nodes.timing import MAX_RETRIES, POLL_INTERVAL, ZONE_CHANGE_TIMEOUT

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="str",
        label="Zone",
        help="Zone name all players must currently be in when this node executes.",
        default="",
        required=True,
    ),
    ParamField(
        key="to_zone",
        type="str",
        label="Destination Zone",
        help="Zone name players should be in after the teleport completes. Used to verify the zone change succeeded.",
        default="",
        required=True,
    ),
    ParamField(
        key="interact_xyz",
        type="xyz",
        label="Interact XYZ",
        help="Coordinates where the interact prompt appears for the zone-list object.",
        default={"x": 0.0, "y": 0.0, "z": 0.0},
        required=True,
    ),
    ParamField(
        key="reset_xyz",
        type="xyz",
        label="Reset XYZ",
        help=(
            "Coordinates where the interact prompt does NOT appear. "
            "Used to clear stale prompts and reset between retry attempts."
        ),
        default={"x": 0.0, "y": 0.0, "z": 0.0},
        required=True,
    ),
    ParamField(
        key="zone_list_entry",
        type="str",
        label="Zone List Entry",
        help=(
            "The display text of the zone to select from the list (case-insensitive). "
            "The node pages through all available entries until a match is found."
        ),
        default="",
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Interacts with a zone-selection list object, navigates its pages to find the specified "
        "zone entry, selects it, and teleports all clients to that zone."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec(
            "SUCCESS",
            "Zone entry selected, teleport button clicked, and all clients confirmed in the destination zone.",
        ),
        ReturnSpec(
            "FAILURE",
            "Clients not in the expected zone; interact prompt never appeared; zone list UI never opened; "
            "zone entry not found on any page; or zone change timed out.",
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
    max_consecutive_retries=10,
)

_MAX_PAGES = 20  # Safety cap — must exceed the total page count of any zone list
_FUZZY_THRESHOLD = 0.75  # Minimum SequenceMatcher ratio for a fuzzy match


def _fuzzy_match(text: str, query_lower: str) -> bool:
    """Return True if text is a close enough match for query_lower (already lowercased).

    Checks substring containment first (fast path), then falls back to
    SequenceMatcher ratio. This handles display names that may differ slightly
    from the underlying zone name used to derive the query.
    """
    t = text.strip().lower()
    if not t or not query_lower:
        return False
    if query_lower in t:
        return True
    return difflib.SequenceMatcher(None, t, query_lower).ratio() >= _FUZZY_THRESHOLD


async def _select_zone(client, entry: str, safety_delay: float) -> bool:
    """Navigate the zone list UI on a single client to find and click the matching entry.

    Returns True if the entry was found and clicked, False if all pages were exhausted.
    Assumes the zone list UI is already open.

    Page navigation uses a visited-signature set to detect when the list has wrapped
    around (all pages seen), since the prev/next buttons never become disabled — they
    always cycle through the list circularly.
    """
    entry_lower = entry.strip().lower()
    visited: set[tuple[str, ...]] = set()

    async def read_texts() -> list[str]:
        return [await node_helpers.get_text_by_path(client, universe_zone_option_path(i)) for i in range(4)]

    async def page_changed(s: list[str]) -> bool:
        return await read_texts() != s

    for _ in range(_MAX_PAGES):
        texts = await read_texts()
        sig = tuple(texts)

        # If we've seen this page before the list has wrapped — all pages exhausted.
        if sig in visited:
            return False
        visited.add(sig)

        for i, text in enumerate(texts):
            logging.debug(f"Searching for {entry_lower!r} in {text!r} (client={client}, option={i})")
            if _fuzzy_match(text, entry_lower):
                await node_helpers.click_by_path(client, universe_zone_option_path(i))
                return True

        snapshot = texts
        await node_helpers.click_by_path(client, UNIVERSE_NEXT_PAGE)
        await node_helpers.poll_until(lambda s=snapshot: page_changed(s), safety_delay, POLL_INTERVAL)

    return False


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.is_in_npc_range(),
          client.send_key(Keycode.X), node_helpers.is_visible_by_path(),
          node_helpers.get_text_by_path(), node_helpers.click_by_path()

    Flow:
      1. Verify all clients are in zone. Return FAILURE if not.
      2. Clear any pre-existing interact prompt by teleporting to reset_xyz.
      3. Retry loop (MAX_RETRIES): teleport to interact_xyz, wait for the interact
         prompt. If it does not appear, teleport to reset_xyz and retry.
         Return FAILURE if the prompt never appeared.
      4. Press X on all clients to open the zone list.
      5. Wait for the zone list UI to open (UNIVERSE_TELEPORT_BUTTON visible on all
         clients). Return FAILURE if it never appears within Safety Delay.
      6. For each client in parallel: navigate pages searching for zone_list_entry using
         fuzzy matching. After each failed page, click Next and wait for the page to
         change. A visited-signature set detects when all pages have been cycled through
         (the list wraps circularly — Next/Prev are never disabled). If not found on
         any page, click cancel on all clients and return FAILURE.
      7. Click the teleport button on all clients, then confirm the dungeon exit warning
         if the list was used from inside a dungeon.
      8. Wait for all clients to reach to_zone within ZONE_CHANGE_TIMEOUT.
         Return FAILURE if timed out.
      9. Return SUCCESS.

    FAILURE: clients not in zone; interact prompt never appeared after MAX_RETRIES;
    zone list UI never opened; zone_list_entry not found on any page; zone change timed out.
    """
    zone: str = params["zone"]
    to_zone: str = params["to_zone"]
    interact = XYZ(**params["interact_xyz"])
    reset = XYZ(**params["reset_xyz"])
    zone_list_entry: str = params["zone_list_entry"]
    safety_delay: float = machine.get_option("Safety Delay", 3.0)

    machine.log(f"enter_zone_via_list: {zone!r} → {to_zone!r}, entry={zone_list_entry!r}")

    # 1. Zone check.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    if not all(node_helpers.zone_matches(z, zone) for z in zones):
        wrong = [z for z in zones if not node_helpers.zone_matches(z, zone)]
        machine.log(
            f"FAILURE: clients not in expected zone (expected={zone!r}, unexpected={wrong})",
            logging.WARNING,
        )
        return "FAILURE"

    # 2. Clear any pre-existing interact prompt.
    prompts = await asyncio.gather(*[c.is_in_npc_range() for c in clients])
    if any(prompts):
        machine.log("Pre-existing interact prompt detected; teleporting to reset_xyz to clear it", logging.DEBUG)
        await asyncio.gather(*[c.teleport(reset) for c in clients])
        await node_helpers.poll_until(lambda: node_helpers.none_in_npc_range(clients), safety_delay, POLL_INTERVAL)

    # 3. Retry loop: teleport to interact_xyz, wait for prompt.
    prompt_appeared = False
    for attempt in range(1, MAX_RETRIES + 1):
        machine.log(f"Attempt {attempt}/{MAX_RETRIES}: teleporting to interact_xyz", logging.DEBUG)
        await asyncio.gather(*[c.teleport(interact) for c in clients])
        if await node_helpers.poll_until(lambda: node_helpers.all_in_npc_range(clients), safety_delay, POLL_INTERVAL):
            prompt_appeared = True
            break
        machine.log(f"Interact prompt did not appear (attempt {attempt}); resetting", logging.DEBUG)
        await asyncio.gather(*[c.teleport(reset) for c in clients])

    if not prompt_appeared:
        machine.log(
            f"FAILURE: interact prompt never appeared after {MAX_RETRIES} attempts",
            logging.WARNING,
        )
        return "FAILURE"

    # 4. Press X to open the zone list.
    machine.log("Pressing X to open zone list", logging.DEBUG)
    await asyncio.gather(*[c.send_key(Keycode.X, 0.1) for c in clients])

    # 5. Wait for zone list UI to open.
    async def _list_open() -> bool:
        results = await asyncio.gather(*[node_helpers.is_visible_by_path(c, UNIVERSE_TELEPORT_BUTTON) for c in clients])
        return all(results)

    if not await node_helpers.poll_until(_list_open, safety_delay, POLL_INTERVAL):
        machine.log("FAILURE: zone list UI never opened after pressing X", logging.WARNING)
        return "FAILURE"

    machine.log(f"Zone list open; searching for {zone_list_entry!r}", logging.DEBUG)

    # 6. Each client navigates to and selects the matching entry.
    found = await asyncio.gather(*[_select_zone(c, zone_list_entry, safety_delay) for c in clients])
    if not all(found):
        machine.log(
            f"FAILURE: zone list entry {zone_list_entry!r} not found on any page",
            logging.WARNING,
        )
        await asyncio.gather(*[node_helpers.click_by_path(c, UNIVERSE_CANCEL_BUTTON) for c in clients])
        return "FAILURE"

    machine.log(f"Entry {zone_list_entry!r} selected; clicking teleport button", logging.DEBUG)

    # 7. Click the teleport button.
    await asyncio.gather(*[node_helpers.click_by_path(c, UNIVERSE_TELEPORT_BUTTON) for c in clients])

    # 7b. Confirm the dungeon exit warning if the list was used from inside a dungeon —
    #     until it is answered the teleport never fires and step 8 times out. Blocking
    #     here is safe because what follows waits on the arrival zone, which is sticky.
    machine.log("Checking for an instance prompt…", logging.DEBUG)
    if refusal := await node_helpers.answer_instance_prompts(clients, safety_delay, POLL_INTERVAL):
        machine.log(
            f"FAILURE: the game refused the teleport — {dialog_captions.describe(refusal)}",
            logging.WARNING,
        )
        return "FAILURE"

    # 8. Wait for zone change.
    actual = await node_helpers.wait_for_zone(clients, to_zone, ZONE_CHANGE_TIMEOUT)
    if actual is not None:
        machine.log(
            f"FAILURE: zone change timed out (expected={to_zone!r}, actual={actual})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"enter_zone_via_list: all clients in {to_zone!r} — SUCCESS")
    return "SUCCESS"
