"""Node: change to the next realm in a configured page range.

Renamed from ``change_realm`` when ``change_to_favorite_realm`` arrived: the two are
siblings, and a name that says *which* realm is the only way to tell them apart in a
node picker. The UI plumbing both share is :mod:`bartlebot.nodes.utility.realm_list`.
"""

import asyncio
import logging

from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import TRIGGER_TIMEOUT
from bartlebot.nodes.utility import realm_list
from bartlebot.nodes.utility.realm_list import REALMS_PER_PAGE

_TAG = "change_to_next_realm"
_PAGE_CHOICES = [2, 3, 4, 5, 6]

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="start_realm_page",
        type="int_choice",
        label="Start Realm Page",
        help=(
            "First page of realms to cycle through (pages 2–6). "
            "Page 1 is excluded as it is the most populated. "
            "Default of 3 avoids the busiest realms."
        ),
        default=3,
        choices=_PAGE_CHOICES,
        required=False,
    ),
    ParamField(
        key="end_realm_page",
        type="int_choice",
        label="End Realm Page",
        help=(
            "Last page of realms to cycle through (pages 2–6). "
            "Page 6 is excluded from the default range because it attracts "
            "users who spam-click to reach low-population realms. "
            "Default of 5 provides the best low-traffic experience."
        ),
        default=5,
        choices=_PAGE_CHOICES,
        required=False,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Opens the spellbook, navigates to the Realms tab, finds the current realm "
        "(identified by its disabled/grayed button in the list), and switches to the "
        "next realm in the configured page range. "
        "If the current realm is not found in the range (e.g. it is on page 1), "
        "the first realm on start_realm_page is selected instead. "
        "Waits out the realm-hop cooldown timer if needed before confirming the switch."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players successfully entered a loading screen and the realm change completed."),
        ReturnSpec(
            "FAILURE",
            "The spellbook or realms panel did not open, the realm-hop timer did not "
            "expire within the timeout, or a loading screen was not detected.",
        ),
    ],
    required_bot_options=[
        BotOptionSpec(
            name="Safety Delay",
            type="float",
            default=3.0,
            minimum=0.0,
            suffix=" s",
            description=(
                "Seconds to allow for game-server responses when waiting for prompts or menus. "
                "Increase on high-latency connections."
            ),
        ),
    ],
    max_consecutive_retries=5,
    usage_notes=(
        "Realms can only be switched from an open-world zone: a dungeon, gauntlet or house refuses. "
        "Use change_to_favorite_realm instead when every player should end up in one particular realm."
    ),
    see_also=["change_to_favorite_realm"],
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: realm_list.open_realm_list(), realm_list.navigate_to_page_1(), realm_list.navigate_pages(),
          realm_list.is_current_realm(), realm_list.row_name(), realm_list.hop_to_row()

    Flow (per client, run concurrently via asyncio.gather):
      1. Open the spellbook to the Realms tab and wait for the list to render.
      2. Anchor to page 1 by clicking the left arrow until it is disabled.
      3. Navigate right from page 1 to start_realm_page, verifying each page flip.
      4. Scan pages start_realm_page–end_realm_page for the current realm, identified by
         its grayed-out (disabled) button.
      5. Determine the target realm: the next entry after the current one, wrapping back
         to start_realm_page row 0 if the current realm is last in the range. If the
         current realm was not found, default to start_realm_page row 0.
      6. Navigate from the end of the scan to the target page.
      7. Select the target row, wait out the realm-hop cooldown, press Go To Realm, and
         wait for the loading screen to start and clear.

    FAILURE: spellbook or realm panel did not open; page navigation timed out; realm-hop
    cooldown did not expire within REALM_HOP_TIMEOUT; loading screen did not start after
    clicking Go To Realm.
    """
    start_page: int = params.get("start_realm_page", 3)
    end_page: int = params.get("end_realm_page", 5)

    # Clamp to valid range in case of stale params from older bot files.
    start_page = max(2, min(6, start_page))
    end_page = max(start_page, min(6, end_page))

    machine.log(f"{_TAG}: cycling realms on pages {start_page}–{end_page}")

    safety_delay: float = machine.get_option("Safety Delay", 3.0)

    results = await asyncio.gather(
        *[_change_realm_for_client(machine, c, start_page, end_page, safety_delay) for c in clients],
        return_exceptions=True,
    )
    if any(isinstance(r, Exception) or r == "FAILURE" for r in results):
        return "FAILURE"
    return "SUCCESS"


async def _change_realm_for_client(machine, client, start_page: int, end_page: int, safety_delay: float) -> str:
    """Execute the full realm-change UI flow for a single client."""
    if not await realm_list.open_realm_list(machine, client, safety_delay, _TAG):
        return "FAILURE"

    machine.log(f"{_TAG}: anchoring to page 1", logging.DEBUG)
    if not await realm_list.navigate_to_page_1(machine, client, TRIGGER_TIMEOUT, _TAG):
        machine.log("FAILURE: could not anchor to page 1", logging.WARNING)
        await realm_list.close_spellbook(client)
        return "FAILURE"

    if start_page > 1:
        machine.log(f"{_TAG}: navigating to page {start_page}", logging.DEBUG)
        if not await realm_list.navigate_pages(machine, client, start_page - 1, TRIGGER_TIMEOUT, _TAG):
            machine.log(f"FAILURE: could not navigate to start page {start_page}", logging.WARNING)
            await realm_list.close_spellbook(client)
            return "FAILURE"

    # Search for the current realm across pages [start_page .. end_page].
    current_scan_page = start_page
    found_page: int | None = None
    found_row: int | None = None
    for page in range(start_page, end_page + 1):
        machine.log(f"{_TAG}: scanning page {page} for current realm", logging.DEBUG)
        for row in range(REALMS_PER_PAGE):
            if await realm_list.is_current_realm(client, row):
                found_page, found_row = page, row
                break
        if found_page is not None:
            break
        if page < end_page:
            if not await realm_list.navigate_pages(machine, client, 1, TRIGGER_TIMEOUT, _TAG):
                machine.log(f"FAILURE: could not advance past page {page}", logging.WARNING)
                await realm_list.close_spellbook(client)
                return "FAILURE"
            current_scan_page = page + 1

    if found_page is not None:
        assert found_row is not None
        current_realm_name = await realm_list.row_name(client, found_row)
        machine.log(
            f"{_TAG}: current realm is {current_realm_name!r} (page {found_page}, row {found_row})",
            logging.DEBUG,
        )
        # The next entry after the current one, wrapping back to the start of the range.
        next_row = found_row + 1
        next_name = await realm_list.row_name(client, next_row) if next_row < REALMS_PER_PAGE else ""
        if next_row < REALMS_PER_PAGE and next_name:
            target_page, target_row = found_page, next_row
        elif found_page < end_page:
            target_page, target_row = found_page + 1, 0
        else:
            target_page, target_row = start_page, 0
    else:
        machine.log(
            f"{_TAG}: current realm not found in pages {start_page}–{end_page}; "
            f"defaulting to first realm on page {start_page}",
            logging.DEBUG,
        )
        current_realm_name = ""
        target_page, target_row = start_page, 0

    machine.log(f"{_TAG}: target page {target_page}, row {target_row}", logging.DEBUG)

    steps = target_page - current_scan_page
    if steps != 0 and not await realm_list.navigate_pages(machine, client, steps, TRIGGER_TIMEOUT, _TAG):
        machine.log(f"FAILURE: could not navigate to target page {target_page}", logging.WARNING)
        await realm_list.close_spellbook(client)
        return "FAILURE"

    return await realm_list.hop_to_row(machine, client, target_row, _TAG, current_name=current_realm_name)
