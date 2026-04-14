"""Node: change to the next realm in a configured page range."""

import asyncio
import logging
import re

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import (
    BACKPACK_CLOSE_BUTTON_PATH,
    REALM_CURRENT_NAME_PATH,
    REALM_GO_TO_BUTTON_PATH,
    REALM_NEXT_PAGE_PATH,
    REALM_PREV_PAGE_PATH,
    SPELLBOOK_BUTTON_PATH,
    SPELLBOOK_PREFERENCES_TAB_PATH,
    SPELLBOOK_REALMS_BUTTON_PATH,
    realm_button_path,
    realm_name_path,
)
from bartlebot.nodes.timing import POLL_INTERVAL, REALM_HOP_TIMEOUT, TRIGGER_TIMEOUT

_REALMS_PER_PAGE = 7
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
        ReturnSpec("SUCCESS", "All clients successfully entered a loading screen and the realm change completed."),
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
            description=(
                "Seconds to allow for game-server responses when waiting for UI panels to appear. "
                "Increase on high-latency connections."
            ),
        ),
    ],
    max_consecutive_retries=5,
)


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string (e.g. '<center>Unicorn' → 'Unicorn')."""
    return re.sub(r"<[^>]+>", "", text).strip()


async def run(machine, clients: list, params: dict) -> str:
    """
    Change realms for all clients by cycling to the next realm in the configured range.

    Params:
        start_realm_page (int): First page of the realm list to cycle through (default 3).
        end_realm_page (int):   Last page of the realm list to cycle through (default 5).
    """
    start_page: int = params.get("start_realm_page", 3)
    end_page: int = params.get("end_realm_page", 5)

    # Clamp to valid range in case of stale params from older bot files.
    start_page = max(2, min(6, start_page))
    end_page = max(start_page, min(6, end_page))

    machine.log(f"change_realm: cycling realms on pages {start_page}–{end_page}")

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

    # 1. Open spellbook.
    machine.log("change_realm: opening spellbook", logging.DEBUG)
    await node_helpers.click_by_path(client, SPELLBOOK_BUTTON_PATH)
    if not await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, SPELLBOOK_PREFERENCES_TAB_PATH),
        safety_delay,
        POLL_INTERVAL,
    ):
        machine.log("FAILURE: spellbook did not open", logging.WARNING)
        return "FAILURE"

    # 2. Open Preferences tab.
    machine.log("change_realm: opening Preferences tab", logging.DEBUG)
    await node_helpers.click_by_path(client, SPELLBOOK_PREFERENCES_TAB_PATH)
    await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, SPELLBOOK_REALMS_BUTTON_PATH),
        safety_delay,
        POLL_INTERVAL,
    )

    # 3. Open Realms tab.
    # REALM_CURRENT_NAME_PATH is used as a presence check — the window exists but its
    # text is not readable via WizWalker (maybe_text() and offset reads both return '').
    # Current-realm detection is done via the grayed button state instead (see step 5).
    machine.log("change_realm: opening Realms tab", logging.DEBUG)
    await node_helpers.click_by_path(client, SPELLBOOK_REALMS_BUTTON_PATH)
    if not await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, REALM_CURRENT_NAME_PATH),
        safety_delay,
        POLL_INTERVAL,
    ):
        machine.log("FAILURE: realms panel did not open", logging.WARNING)
        await node_helpers.click_by_path(client, BACKPACK_CLOSE_BUTTON_PATH)
        return "FAILURE"

    # 4. Navigate from page 1 (the panel's initial page) to start_page.
    current_page = 1
    for _ in range(start_page - 1):
        await node_helpers.click_by_path(client, REALM_NEXT_PAGE_PATH)
        await asyncio.sleep(POLL_INTERVAL)
        current_page += 1

    # 5. Search for the current realm across pages [start_page .. end_page].
    #    The current realm's button is grayed out (disabled) because you cannot switch
    #    to the realm you are already on. txtYourRealmName exists but its text is not
    #    accessible via WizWalker, so button state is the reliable detection method.
    found_page: int | None = None
    found_row: int | None = None
    for page in range(start_page, end_page + 1):
        machine.log(f"change_realm: scanning page {page} for current realm", logging.DEBUG)
        for row in range(_REALMS_PER_PAGE):
            if not await node_helpers.is_enabled_by_path(client, realm_button_path(row)):
                found_page, found_row = page, row
                break
        if found_page is not None:
            break
        if page < end_page:
            await node_helpers.click_by_path(client, REALM_NEXT_PAGE_PATH)
            await asyncio.sleep(POLL_INTERVAL)
            current_page = page + 1

    if found_page is not None:
        current_realm_name = _strip_html(await node_helpers.get_text_by_path(client, realm_name_path(found_row)))
        machine.log(
            f"change_realm: current realm is {current_realm_name!r} (page {found_page}, row {found_row})",
            logging.DEBUG,
        )
    else:
        machine.log(
            f"change_realm: current realm not found in pages {start_page}–{end_page}; "
            f"defaulting to first realm on page {start_page}",
            logging.DEBUG,
        )
        current_realm_name = ""

    # 6. Determine the target realm (next after current, with wraparound).
    if found_page is not None:
        assert found_row is not None
        next_row = found_row + 1
        next_name = (
            _strip_html(await node_helpers.get_text_by_path(client, realm_name_path(next_row)))
            if next_row < _REALMS_PER_PAGE
            else ""
        )
        if next_row < _REALMS_PER_PAGE and next_name:
            target_page, target_row = found_page, next_row
        elif found_page < end_page:
            target_page, target_row = found_page + 1, 0
        else:
            # Current realm is the last on end_page — wrap back to start.
            target_page, target_row = start_page, 0
    else:
        # Not found in range — start at start_page, row 0.
        target_page, target_row = start_page, 0

    machine.log(f"change_realm: target page {target_page}, row {target_row}", logging.DEBUG)

    # 7. Navigate to target_page from current_page.
    if target_page > current_page:
        for _ in range(target_page - current_page):
            await node_helpers.click_by_path(client, REALM_NEXT_PAGE_PATH)
            await asyncio.sleep(POLL_INTERVAL)
    elif target_page < current_page:
        for _ in range(current_page - target_page):
            await node_helpers.click_by_path(client, REALM_PREV_PAGE_PATH)
            await asyncio.sleep(POLL_INTERVAL)

    # 8. Select the target realm.
    await node_helpers.click_by_path(client, realm_button_path(target_row))
    await asyncio.sleep(POLL_INTERVAL)

    selected_name = _strip_html(await node_helpers.get_text_by_path(client, realm_name_path(target_row)))
    machine.log(f"change_realm: switching {current_realm_name!r} → {selected_name!r}")

    # 9. Wait for the Go To Realm button to become enabled (realm-hop cooldown).
    #    The game deselects the chosen row when the timer expires, which keeps the button
    #    disabled even after the cooldown clears. Re-click the target row on every poll
    #    cycle so the selection is restored as soon as the timer allows it.
    loop_start = asyncio.get_event_loop().time()
    while not await node_helpers.is_enabled_by_path(client, REALM_GO_TO_BUTTON_PATH):
        elapsed = asyncio.get_event_loop().time() - loop_start
        if elapsed > REALM_HOP_TIMEOUT:
            machine.log(
                f"FAILURE: realm-hop timer still active after {REALM_HOP_TIMEOUT:.0f}s",
                logging.WARNING,
            )
            await node_helpers.click_by_path(client, BACKPACK_CLOSE_BUTTON_PATH)
            return "FAILURE"
        if int(elapsed) % 10 == 0 and elapsed >= 5.0:
            machine.log(f"change_realm: waiting for realm-hop cooldown ({elapsed:.0f}s elapsed)…", logging.DEBUG)
        await node_helpers.click_by_path(client, realm_button_path(target_row))
        await asyncio.sleep(POLL_INTERVAL)

    await node_helpers.click_by_path(client, REALM_GO_TO_BUTTON_PATH)

    # 10. Wait for the loading screen to start.
    seen = await node_helpers.poll_each_until_seen(
        [client],
        check_fn=lambda c: c.is_loading(),
        action_fn=lambda c: asyncio.sleep(0),
        timeout=TRIGGER_TIMEOUT,
    )
    if not seen:
        machine.log(
            f"FAILURE: loading screen did not start within {TRIGGER_TIMEOUT}s after clicking Go To Realm",
            logging.WARNING,
        )
        return "FAILURE"

    # 11. Wait for the loading screen to clear.
    machine.log("change_realm: loading screen active, waiting for it to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading([client]), None, POLL_INTERVAL)

    machine.log(f"SUCCESS: realm changed to {selected_name!r}")
    return "SUCCESS"
