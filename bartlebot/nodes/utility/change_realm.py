"""Node: change to the next realm in a configured page range."""

import asyncio
import logging
import re

from bartlebot import dialog_captions
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
            description=(
                "Seconds to allow for game-server responses when waiting for UI panels "
                "to appear, and for the realm list to initially render. "
                "Increase on high-latency connections."
            ),
        ),
    ],
    max_consecutive_retries=5,
)


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string (e.g. '<center>Unicorn' → 'Unicorn')."""
    return re.sub(r"<[^>]+>", "", text).strip()


async def _get_row0_name(client) -> str:
    """Read the name of the first realm on the currently displayed page."""
    return _strip_html(await node_helpers.get_text_by_path(client, realm_name_path(0)))


async def _content_changed(client, before: str) -> bool:
    """Return True once the row-0 realm name is non-empty and differs from before."""
    current = await _get_row0_name(client)
    return bool(current) and current != before


async def _navigate_pages(machine, client, steps: int, timeout: float) -> bool:
    """Navigate the realm list by steps pages (positive = right, negative = left).

    After each click, polls until the row-0 realm name changes to confirm the page
    actually flipped before proceeding to the next step. Returns False if any step
    does not produce a content change within timeout.
    """
    direction = REALM_NEXT_PAGE_PATH if steps > 0 else REALM_PREV_PAGE_PATH
    for step in range(abs(steps)):
        before = await _get_row0_name(client)
        await node_helpers.click_by_path(client, direction)
        if not await node_helpers.poll_until(
            lambda b=before: _content_changed(client, b),
            timeout,
            POLL_INTERVAL,
        ):
            machine.log(
                f"change_realm: page did not change on step {step + 1}/{abs(steps)} "
                f"(direction={'right' if steps > 0 else 'left'})",
                logging.WARNING,
            )
            return False
    return True


async def _navigate_to_page_1(machine, client, timeout: float) -> bool:
    """Navigate left until btnRealmLeft is disabled, confirming we are on page 1.

    Each left-click is verified by waiting for the row-0 realm name to change.
    Returns False if any step times out.
    """
    while await node_helpers.is_enabled_by_path(client, REALM_PREV_PAGE_PATH):
        if not await _navigate_pages(machine, client, -1, timeout):
            machine.log("change_realm: could not reach page 1 via left navigation", logging.WARNING)
            return False
    return True


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: node_helpers.click_by_path(), node_helpers.poll_until(), node_helpers.is_visible_by_path(),
          node_helpers.is_enabled_by_path(), node_helpers.get_text_by_path(), client.is_loading()

    Flow (per client, run concurrently via asyncio.gather):
      1. Click the spellbook button; poll until the Preferences tab is visible.
      2. Click the Preferences tab; poll until the Realms button is visible.
      3. Click the Realms button; poll until the realm panel appears and the first realm
         name is readable (the list can be slow to render on first open).
      4. Anchor to page 1 by clicking the left arrow until the left button is disabled.
      5. Navigate right from page 1 to start_realm_page, verifying each page flip.
      6. Scan pages start_realm_page–end_realm_page for the current realm, identified by
         its grayed-out (disabled) button.
      7. Determine the target realm: the next entry after the current one, wrapping back
         to start_realm_page row 0 if the current realm is last in the range. If the
         current realm was not found, default to start_realm_page row 0.
      8. Navigate from the end of the scan to the target page.
      9. Click the target realm button.
      10. Poll until the Go To Realm button is enabled (realm-hop cooldown). Re-click the
          target row each tick to restore selection if the game deselects it.
      11. Click Go To Realm; poll until a loading screen starts.
      12. Wait for the loading screen to clear.

    FAILURE: spellbook or realm panel did not open; page navigation timed out; realm-hop
    cooldown did not expire within REALM_HOP_TIMEOUT; loading screen did not start after
    clicking Go To Realm.
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

    # 3. Open Realms tab and wait for the list to fully render.
    #    The realm list can be slow to appear on the first open. We wait up to
    #    safety_delay for the panel itself to appear, then up to safety_delay again
    #    for the first realm name to become readable. This prevents falsely detecting
    #    a page change by comparing against an empty initial state.
    #    Note: REALM_CURRENT_NAME_PATH is used only as a presence check — its text
    #    is not readable via WizWalker; current-realm detection uses button state instead.
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

    machine.log("change_realm: waiting for realm list to render…", logging.DEBUG)
    if not await node_helpers.poll_until(
        lambda: _content_ready(client),
        safety_delay,
        POLL_INTERVAL,
    ):
        machine.log("FAILURE: realm list did not render within safety delay", logging.WARNING)
        await node_helpers.click_by_path(client, BACKPACK_CLOSE_BUTTON_PATH)
        return "FAILURE"

    # 4. Anchor to page 1 by clicking left until btnRealmLeft is disabled.
    #    The panel may open on a different page than page 1 (e.g. the last page
    #    visited), so we cannot assume a starting position. Each left-click is
    #    verified via row-0 content change before proceeding.
    machine.log("change_realm: anchoring to page 1", logging.DEBUG)
    if not await _navigate_to_page_1(machine, client, TRIGGER_TIMEOUT):
        machine.log("FAILURE: could not anchor to page 1", logging.WARNING)
        await node_helpers.click_by_path(client, BACKPACK_CLOSE_BUTTON_PATH)
        return "FAILURE"

    # 5. Navigate from page 1 to start_page.
    if start_page > 1:
        machine.log(f"change_realm: navigating to page {start_page}", logging.DEBUG)
        if not await _navigate_pages(machine, client, start_page - 1, TRIGGER_TIMEOUT):
            machine.log(f"FAILURE: could not navigate to start page {start_page}", logging.WARNING)
            await node_helpers.click_by_path(client, BACKPACK_CLOSE_BUTTON_PATH)
            return "FAILURE"

    # 6. Search for the current realm across pages [start_page .. end_page].
    #    The current realm's button is grayed out (disabled) because you cannot switch
    #    to the realm you are already on. txtYourRealmName exists but its text is not
    #    accessible via WizWalker, so button state is the reliable detection method.
    current_scan_page = start_page
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
            if not await _navigate_pages(machine, client, 1, TRIGGER_TIMEOUT):
                machine.log(f"FAILURE: could not advance past page {page}", logging.WARNING)
                await node_helpers.click_by_path(client, BACKPACK_CLOSE_BUTTON_PATH)
                return "FAILURE"
            current_scan_page = page + 1

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

    # 7. Determine the target realm (next after current, with wraparound).
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

    # 8. Navigate from the end of the scan to target_page.
    steps = target_page - current_scan_page
    if steps != 0:
        if not await _navigate_pages(machine, client, steps, TRIGGER_TIMEOUT):
            machine.log(f"FAILURE: could not navigate to target page {target_page}", logging.WARNING)
            await node_helpers.click_by_path(client, BACKPACK_CLOSE_BUTTON_PATH)
            return "FAILURE"

    # 9. Select the target realm.
    await node_helpers.click_by_path(client, realm_button_path(target_row))
    await asyncio.sleep(POLL_INTERVAL)

    selected_name = _strip_html(await node_helpers.get_text_by_path(client, realm_name_path(target_row)))
    machine.log(f"change_realm: switching {current_realm_name!r} → {selected_name!r}")

    # 10. Wait for the Go To Realm button to become enabled (realm-hop cooldown).
    #     The game deselects the chosen row when the timer expires, which keeps the
    #     button disabled even after the cooldown clears. Re-click the target row on
    #     every poll cycle so the selection is restored as soon as the timer allows it.
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

    # 11. Wait for the loading screen to start.
    #     Re-click Go To Realm each tick while still enabled — the button can be clicked
    #     too quickly after it enables, causing the game to ignore the first press.
    #
    #     Two realm modals can intervene, and they want opposite answers (verified live,
    #     issue #84). Inside a dungeon the game simply refuses: "You are not allowed to
    #     switch Realms from this location" — no amount of re-clicking gets past it, so
    #     the node fails immediately rather than burning TRIGGER_TIMEOUT on a button the
    #     game has already declined. Outside a dungeon but still holding a dungeon recall,
    #     it asks instead: "Changing to a different Realm will reset your current Dungeon"
    #     — a question, confirmed so the hop proceeds.
    retry = node_helpers.ModalAwareRetry(lambda c: node_helpers.click_by_path(c, REALM_GO_TO_BUTTON_PATH))

    async def _loading_or_refused(c) -> bool:
        # A refusal ends the wait as surely as a loading screen does; the caller
        # distinguishes them straight after.
        return retry.refusal is not None or await c.is_loading()

    seen = await node_helpers.poll_each_until_seen(
        [client],
        check_fn=_loading_or_refused,
        action_fn=retry,
        timeout=TRIGGER_TIMEOUT,
        poll_interval=POLL_INTERVAL,
    )
    if retry.refusal:
        machine.log(
            f"FAILURE: realm change refused — {dialog_captions.describe(retry.refusal)}",
            logging.WARNING,
        )
        return "FAILURE"
    if not seen:
        machine.log(
            f"FAILURE: loading screen did not start within {TRIGGER_TIMEOUT}s after clicking Go To Realm",
            logging.WARNING,
        )
        return "FAILURE"

    # 12. Wait for the loading screen to clear.
    machine.log("change_realm: loading screen active, waiting for it to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading([client]), None, POLL_INTERVAL)

    machine.log(f"SUCCESS: realm changed to {selected_name!r}")
    return "SUCCESS"


async def _content_ready(client) -> bool:
    """Return True once the realm list has rendered at least one readable realm name."""
    return bool(await _get_row0_name(client))
