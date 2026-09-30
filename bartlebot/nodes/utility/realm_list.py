"""The in-game realm list: opening it, paging it, and hopping to a row.

Shared by ``change_realm``'s two modes. Next-realm mode walks a page range and picks
the row after the current one; favorite mode walks every page looking for one name.
Everything between -- the spellbook, the Preferences tab, the Realms tab, the render
wait, the page arrows, the cooldown on Go To Realm, the two modals that can interrupt
it -- is the same in both and lives here once.

Nothing here decides *which* realm. A function takes a row on the page currently
showing, and the caller is responsible for having navigated to it.
"""

from __future__ import annotations

import asyncio
import logging

from bartlebot import dialog_captions, realms
from bartlebot.nodes import node_helpers
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

#: Rows the realm panel shows per page (btnRealm0 .. btnRealm6).
REALMS_PER_PAGE = 7

_strip_html = node_helpers.strip_html


async def row_name(client, row: int) -> str:
    """The realm name shown on *row* of the current page, HTML stripped; '' if blank."""
    return _strip_html(await node_helpers.get_text_by_path(client, realm_name_path(row)))


async def is_current_realm(client, row: int) -> bool:
    """Whether *row* is the realm the client is already on.

    The current realm's button is grayed out, because you cannot switch to the realm
    you are already on. Used while scanning rows on a page; see current_realm_name()
    for reading the realm directly, without a row to check.
    """
    return not await node_helpers.is_enabled_by_path(client, realm_button_path(row))


async def current_realm_name(client) -> str:
    """The client's current realm, read directly from txtYourRealmName, HTML stripped."""
    return _strip_html(await node_helpers.get_text_by_path(client, REALM_CURRENT_NAME_PATH))


async def wait_for_current_realm_name(client, timeout: float) -> str:
    """Poll until txtYourRealmName has text, returning it ('' if it never renders)."""
    name = ""

    async def _ready() -> bool:
        nonlocal name
        name = await current_realm_name(client)
        return bool(name)

    await node_helpers.poll_until(_ready, timeout, POLL_INTERVAL)
    return name


async def read_current_realm(machine, client, safety_delay: float, tag: str) -> str | None:
    """The key of the realm *client* is on, read from its spellbook, or None if it cannot be told.

    Read from txtYourRealmName, so no page is turned. Opens and closes the spellbook.
    """
    if not await open_realm_list(machine, client, safety_delay, tag):
        return None
    try:
        shown = await wait_for_current_realm_name(client, safety_delay)
    finally:
        await close_spellbook(client)
    return realms.key_for(shown) if shown else None


async def close_spellbook(client) -> None:
    await node_helpers.click_by_path(client, BACKPACK_CLOSE_BUTTON_PATH)


async def _content_changed(client, before: str) -> bool:
    """True once the row-0 realm name is non-empty and differs from *before*."""
    current = await row_name(client, 0)
    return bool(current) and current != before


async def _content_ready(client) -> bool:
    """True once the realm list has rendered at least one readable realm name."""
    return bool(await row_name(client, 0))


async def navigate_pages(machine, client, steps: int, timeout: float, tag: str) -> bool:
    """Page the realm list by *steps* (positive = right, negative = left).

    After each click, polls until the row-0 realm name changes to confirm the page
    actually flipped before proceeding to the next step. Returns False if any step
    does not produce a content change within *timeout*.
    """
    direction = REALM_NEXT_PAGE_PATH if steps > 0 else REALM_PREV_PAGE_PATH
    for step in range(abs(steps)):
        before = await row_name(client, 0)
        await node_helpers.click_by_path(client, direction)
        if not await node_helpers.poll_until(
            lambda b=before: _content_changed(client, b),
            timeout,
            POLL_INTERVAL,
        ):
            machine.log(
                f"{tag}: page did not change on step {step + 1}/{abs(steps)} "
                f"(direction={'right' if steps > 0 else 'left'})",
                logging.WARNING,
            )
            return False
    return True


async def navigate_to_page_1(machine, client, timeout: float, tag: str) -> bool:
    """Click left until btnRealmLeft is disabled, which is page 1.

    The panel may open on whatever page was last visited, so a starting position cannot
    be assumed. Each left-click is verified by waiting for the row-0 name to change.
    """
    while await node_helpers.is_enabled_by_path(client, REALM_PREV_PAGE_PATH):
        if not await navigate_pages(machine, client, -1, timeout, tag):
            machine.log(f"{tag}: could not reach page 1 via left navigation", logging.WARNING)
            return False
    return True


async def has_next_page(client) -> bool:
    return await node_helpers.is_enabled_by_path(client, REALM_NEXT_PAGE_PATH)


async def open_realm_list(machine, client, safety_delay: float, tag: str) -> bool:
    """Open the spellbook to the Realms tab and wait for the list to render.

    Spellbook -> Preferences tab -> Realms tab. The list can be slow to appear on the
    first open: the panel itself gets *safety_delay*, then the first realm name gets
    *safety_delay* again, so a page-change check never compares against an empty
    initial state. Closes the spellbook again on a failure past the first step.
    """
    machine.log(f"{tag}: opening spellbook", logging.DEBUG)
    await node_helpers.click_by_path(client, SPELLBOOK_BUTTON_PATH)
    if not await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, SPELLBOOK_PREFERENCES_TAB_PATH),
        safety_delay,
        POLL_INTERVAL,
    ):
        machine.log("FAILURE: spellbook did not open", logging.WARNING)
        return False

    machine.log(f"{tag}: opening Preferences tab", logging.DEBUG)
    await node_helpers.click_by_path(client, SPELLBOOK_PREFERENCES_TAB_PATH)
    await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, SPELLBOOK_REALMS_BUTTON_PATH),
        safety_delay,
        POLL_INTERVAL,
    )

    machine.log(f"{tag}: opening Realms tab", logging.DEBUG)
    await node_helpers.click_by_path(client, SPELLBOOK_REALMS_BUTTON_PATH)
    if not await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, REALM_CURRENT_NAME_PATH),
        safety_delay,
        POLL_INTERVAL,
    ):
        machine.log("FAILURE: realms panel did not open", logging.WARNING)
        await close_spellbook(client)
        return False

    machine.log(f"{tag}: waiting for realm list to render…", logging.DEBUG)
    if not await node_helpers.poll_until(lambda: _content_ready(client), safety_delay, POLL_INTERVAL):
        machine.log("FAILURE: realm list did not render within safety delay", logging.WARNING)
        await close_spellbook(client)
        return False
    return True


async def hop_to_row(machine, client, row: int, tag: str, *, current_name: str = "") -> str:
    """Select *row* on the page showing, press Go To Realm, and ride out the load.

    Returns "SUCCESS" once the loading screen has cleared, "FAILURE" if the realm-hop
    cooldown never expires, the game refuses, or no loading screen starts. Leaves the
    spellbook open on the refusal and no-load paths -- the game's own modal is what the
    user should see there.
    """
    await node_helpers.click_by_path(client, realm_button_path(row))
    await asyncio.sleep(POLL_INTERVAL)

    selected_name = await row_name(client, row)
    machine.log(f"{tag}: switching {current_name!r} → {selected_name!r}")

    # Wait for Go To Realm to enable (realm-hop cooldown). The game deselects the chosen
    # row when the timer expires, which keeps the button disabled even after the cooldown
    # clears, so the row is re-clicked every tick to restore the selection.
    loop_start = asyncio.get_event_loop().time()
    while not await node_helpers.is_enabled_by_path(client, REALM_GO_TO_BUTTON_PATH):
        elapsed = asyncio.get_event_loop().time() - loop_start
        if elapsed > REALM_HOP_TIMEOUT:
            machine.log(f"FAILURE: realm-hop timer still active after {REALM_HOP_TIMEOUT:.0f}s", logging.WARNING)
            await close_spellbook(client)
            return "FAILURE"
        if int(elapsed) % 10 == 0 and elapsed >= 5.0:
            machine.log(f"{tag}: waiting for realm-hop cooldown ({elapsed:.0f}s elapsed)…", logging.DEBUG)
        await node_helpers.click_by_path(client, realm_button_path(row))
        await asyncio.sleep(POLL_INTERVAL)

    await node_helpers.click_by_path(client, REALM_GO_TO_BUTTON_PATH)

    # Re-click Go To Realm each tick while still enabled -- pressed too soon after it
    # enables, the game ignores the first press.
    #
    # Two realm modals can intervene, and they want opposite answers (verified live).
    # Inside a dungeon the game refuses: "You are not allowed to switch Realms from this
    # location" -- no amount of re-clicking gets past it, so fail at once rather than
    # burn TRIGGER_TIMEOUT on a button the game has declined. Outside a dungeon but still
    # holding a dungeon recall, it asks instead: "Changing to a different Realm will reset
    # your current Dungeon" -- a question, confirmed so the hop proceeds.
    retry = node_helpers.ModalAwareRetry(lambda c: node_helpers.click_by_path(c, REALM_GO_TO_BUTTON_PATH))

    async def _loading_or_refused(c) -> bool:
        return retry.refusal is not None or await c.is_loading()

    seen = await node_helpers.poll_each_until_seen(
        [client],
        check_fn=_loading_or_refused,
        action_fn=retry,
        timeout=TRIGGER_TIMEOUT,
        poll_interval=POLL_INTERVAL,
    )
    if retry.refusal:
        machine.log(f"FAILURE: realm change refused — {dialog_captions.describe(retry.refusal)}", logging.WARNING)
        return "FAILURE"
    if not seen:
        machine.log(
            f"FAILURE: loading screen did not start within {TRIGGER_TIMEOUT}s after clicking Go To Realm",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"{tag}: loading screen active, waiting for it to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading([client]), None, POLL_INTERVAL)

    machine.log(f"SUCCESS: realm changed to {selected_name!r}")
    return "SUCCESS"
