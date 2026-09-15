"""Reading and changing the equipped castle on the housing page (U key, Castles tab).

handle_potions uses this to temporarily unequip a non-dorm primary residence. A client whose
home is a castle lands in that castle when GotoDormButton is pressed, not the dorm, and the
node then has to wait out the ~30 s teleport cooldown before it can press HOME again.
Unequipping first makes the first press land in the dorm.

Two facts from tools/probe_housing_equip.py shape everything here (see that file for the
evidence, and bartlebot/nodes/paths.py for the paths):

  * A row's own text is the castle's name, and an equipped row shows a visible `fist` icon.
    Nothing else distinguishes equipped from unequipped — Equip_Item is a bare toggle. So a
    client with castles but none equipped, and a client with one equipped, are the same
    window tree apart from that icon; getting it wrong EQUIPS a castle for somebody who
    deliberately has none.

  * Row order is only stable while the page stays open. Closing and reopening it resorts the
    list alphabetically, so a castle unequipped before a teleport is not in its old row when
    the page is reopened afterwards.

Everything below therefore works by searching — by fist to find what is equipped, by name to
find it again — and never by remembering a row number.
"""

import asyncio
import logging

from wizwalker import Keycode

from bartlebot.nodes import node_helpers
from bartlebot.nodes.paths import (
    BACKPACK_CLOSE_BUTTON_PATH,
    HOUSING_CASTLES_TAB_PATH,
    HOUSING_EQUIP_BUTTON_PATH,
    HOUSING_SCROLL_RIGHT_PATH,
    housing_castle_equipped_icon_path,
    housing_castle_row_path,
)
from bartlebot.nodes.timing import (
    HOUSING_EQUIP_TIMEOUT,
    HOUSING_OPEN_RETRIES,
    HOUSING_PAGE_MAX,
    POLL_INTERVAL,
    TRIGGER_TIMEOUT,
)

logger = logging.getLogger(__name__)

# Rows shown on one page of the castle list.
ROWS_PER_PAGE = 8


async def open_page(client) -> bool:
    """Press U until the housing page is showing. False if it never appears.

    Retries because U is swallowed when another spellbook page already owns the window;
    ESC backs out of whatever that was before trying again.
    """
    for attempt in range(1, HOUSING_OPEN_RETRIES + 1):
        if await node_helpers.is_visible_by_path(client, HOUSING_CASTLES_TAB_PATH):
            return True
        await client.send_key(Keycode.U, 0.1)
        if await node_helpers.poll_until(
            lambda: node_helpers.is_visible_by_path(client, HOUSING_CASTLES_TAB_PATH),
            TRIGGER_TIMEOUT,
            POLL_INTERVAL,
        ):
            return True
        logger.debug("housing page did not open on attempt %d; pressing ESC", attempt)
        await client.send_key(Keycode.ESC, 0.1)
        await asyncio.sleep(POLL_INTERVAL)
    return False


async def close_page(client) -> None:
    """Close the housing page. Best effort — nothing downstream depends on it being shut."""
    if await node_helpers.is_enabled_by_path(client, BACKPACK_CLOSE_BUTTON_PATH):
        await node_helpers.click_by_path(client, BACKPACK_CLOSE_BUTTON_PATH)
    else:
        await client.send_key(Keycode.ESC, 0.1)


async def select_castles_tab(client) -> bool:
    """Show the Castles tab. False if Equip_Item never appears.

    Equip_Item exists only on this tab, so its presence is the marker. It is *visible* but
    grayed until a row is selected, hence is_visible rather than is_enabled.
    """
    if await node_helpers.is_visible_by_path(client, HOUSING_EQUIP_BUTTON_PATH):
        return True
    await node_helpers.click_by_path(client, HOUSING_CASTLES_TAB_PATH)
    return await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, HOUSING_EQUIP_BUTTON_PATH),
        TRIGGER_TIMEOUT,
        POLL_INTERVAL,
    )


async def read_page(client) -> list[tuple[int, str, bool]]:
    """Return (row, name, equipped) for every occupied row on the current page.

    Empty slots read as an empty name and are dropped.
    """
    rows: list[tuple[int, str, bool]] = []
    for row in range(1, ROWS_PER_PAGE + 1):
        name = (await node_helpers.get_text_by_path(client, housing_castle_row_path(row)) or "").strip()
        if not name:
            continue
        equipped = await node_helpers.is_visible_by_path(client, housing_castle_equipped_icon_path(row))
        rows.append((row, name, equipped))
    return rows


async def _next_page(client) -> bool:
    """Advance to the next page of castles. False when there is no next page."""
    if not await node_helpers.is_visible_by_path(client, HOUSING_SCROLL_RIGHT_PATH):
        return False
    if not await node_helpers.is_enabled_by_path(client, HOUSING_SCROLL_RIGHT_PATH):
        return False
    return await node_helpers.click_by_path(client, HOUSING_SCROLL_RIGHT_PATH)


async def find_equipped_row(client) -> tuple[int, str] | None:
    """Return (row, name) of the equipped castle, or None if nothing is equipped.

    None is a normal answer, not an error: a wizard whose home is already the dorm has no
    castle equipped, and that client needs no unequipping at all.
    """
    for _ in range(HOUSING_PAGE_MAX):
        for row, name, equipped in await read_page(client):
            if equipped:
                return row, name
        if not await _next_page(client):
            return None
    logger.warning("gave up looking for an equipped castle after %d pages", HOUSING_PAGE_MAX)
    return None


async def find_row_by_name(client, name: str) -> int | None:
    """Return the row holding the named castle, paging through the list to find it.

    A wizard cannot own two castles with the same name (confirmed in game, 2026-08-13), so
    the first match is always the right one and a name is a complete identity for a castle.
    That is what makes caching the name across a close-and-reopen sound: there is no
    ambiguous case left for the resorted list to introduce.

    Starts from whatever page is showing. Callers reopen the page before searching, which
    resets it to the first one.
    """
    wanted = name.strip().casefold()
    for _ in range(HOUSING_PAGE_MAX):
        for row, row_name, _ in await read_page(client):
            if row_name.strip().casefold() == wanted:
                return row
        if not await _next_page(client):
            return None
    logger.warning("gave up looking for castle %r after %d pages", name, HOUSING_PAGE_MAX)
    return None


async def _set_equipped(client, row: int, equipped: bool) -> bool:
    """Click Equip_Item for `row` and confirm the fist icon reaches `equipped`.

    Verified rather than assumed: Equip_Item is a toggle, so a click that did not land
    leaves the opposite state, and a caller that trusted it would either fail to unequip
    (and then wait out the cooldown anyway) or fail to restore somebody's home.
    """
    icon = housing_castle_equipped_icon_path(row)
    if await node_helpers.is_visible_by_path(client, icon) == equipped:
        # Logged at INFO because this is the one read nothing downstream re-checks. A fist
        # that reads stale here reports success without clicking, which is a castle left
        # off (or on) with the log saying it was put back -- one candidate for #15.
        logger.info(
            "hwnd %d: castle row %d already reads %s; Equip_Item not clicked",
            client.window_handle,
            row,
            "equipped" if equipped else "unequipped",
        )
        return True

    # Selecting the row is what enables Equip_Item. The probe read the button as already
    # ungrayed with no row selected, which contradicts the observed UI behaviour and is
    # probably the same unreliable is_control_grayed seen elsewhere on this page — so this
    # poll may pass trivially. It is kept as a cheap guard, but the icon check below is the
    # real confirmation, and nothing here concludes success without it.
    await node_helpers.click_by_path(client, housing_castle_row_path(row))
    if not await node_helpers.poll_until(
        lambda: node_helpers.is_enabled_by_path(client, HOUSING_EQUIP_BUTTON_PATH),
        TRIGGER_TIMEOUT,
        POLL_INTERVAL,
    ):
        logger.warning("Equip_Item never became enabled after selecting row %d", row)
        return False

    await node_helpers.click_by_path(client, HOUSING_EQUIP_BUTTON_PATH)
    return await node_helpers.poll_until(
        lambda: _icon_matches(client, icon, equipped),
        HOUSING_EQUIP_TIMEOUT,
        POLL_INTERVAL,
    )


async def _icon_matches(client, icon: list[str], equipped: bool) -> bool:
    return await node_helpers.is_visible_by_path(client, icon) == equipped


async def unequip_castle(client) -> str | None:
    """Unequip this client's castle, returning its name so it can be restored.

    Returns None when there is nothing to do (no castle equipped) and also when the attempt
    failed — in both cases the caller simply proceeds without the optimisation, since the
    HOME path still works, just more slowly. The distinction is logged rather than returned
    because no caller can act on it differently.

    Leaves the housing page closed.
    """
    if not await open_page(client):
        logger.warning("could not open the housing page; skipping the unequip shortcut")
        return None
    try:
        if not await select_castles_tab(client):
            logger.warning("could not reach the Castles tab; skipping the unequip shortcut")
            return None

        found = await find_equipped_row(client)
        if found is None:
            logger.debug("no castle equipped; the dorm is already this client's home")
            return None

        row, name = found
        if not await _set_equipped(client, row, False):
            logger.warning("could not unequip castle %r; falling back to the slow HOME path", name)
            return None
        logger.debug("unequipped castle %r", name)
        return name
    finally:
        await close_page(client)


async def equip_castle(client, name: str) -> bool:
    """Re-equip a castle by name, undoing unequip_castle().

    Searches by name because the list resorts alphabetically once the page has been closed
    and reopened, so the row it came from means nothing now.

    Leaves the housing page closed.
    """
    if not await open_page(client):
        logger.warning("could not open the housing page to restore castle %r", name)
        return False
    try:
        if not await select_castles_tab(client):
            logger.warning("could not reach the Castles tab to restore castle %r", name)
            return False

        row = await find_row_by_name(client, name)
        if row is None:
            logger.warning("castle %r is no longer in the list; cannot restore it", name)
            return False

        if not await _set_equipped(client, row, True):
            logger.warning("could not re-equip castle %r", name)
            return False
        logger.debug("re-equipped castle %r", name)
        return True
    finally:
        await close_page(client)
