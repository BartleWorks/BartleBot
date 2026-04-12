"""Node: quicksell backpack items when full, keeping items that match a keyword list."""

import asyncio
import logging

from wizwalker import Keycode

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import (
    BACKPACK_CLOSE_BUTTON_PATH,
    JEWEL_INVENTORY_SPACE_PATH,
    MESSAGE_BOX_CAPTION_PATH,
    MESSAGE_BOX_CENTER,
    MESSAGE_BOX_RIGHT,
    QUICKSELL_ALL_BUTTON_PATH,
    QUICKSELL_ALL_TAB_PATH,
    QUICKSELL_CLOSE_BUTTON_PATH,
    QUICKSELL_CONFIRM_SELL_PATH,
    QUICKSELL_JEWEL_OPEN_PATH,
    QUICKSELL_NEXT_PAGE_PATH,
    QUICKSELL_PET_TAB_PATH,
    QUICKSELL_SELL_BUTTON_PATH,
    quicksell_item_checkbox_path,
    quicksell_item_title_path,
)

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="keep_list",
        type="str_list",
        label="Keep List",
        help=(
            "Keywords to match against item names when quickselling. "
            "Any item whose name contains one of these strings (case-insensitive) will NOT be sold. "
            "Partial matches count — 'hat' will keep any item with 'hat' in the name."
        ),
        default=[],
        required=False,
    ),
    ParamField(
        key="sell_crowns",
        type="bool",
        label="Sell Crowns Items",
        help=(
            "If enabled, Crowns items are included in the quicksell. "
            "If disabled, Crowns items are excluded when Select All is clicked."
        ),
        default=True,
        required=False,
    ),
    ParamField(
        key="sell_pets",
        type="bool",
        label="Sell Pets",
        help="If enabled, pets will be sold when the post-sell confirmation dialog appears.",
        default=True,
        required=False,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Checks if the main backpack or jewel pouch is full. "
        "If either is full, opens the quicksell UI and sells all items except those "
        "whose names match a keyword in keep_list. "
        "sell_crowns and sell_pets control whether those item types are included."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Neither backpack was full, or items were sold successfully."),
        ReturnSpec("FAILURE", "The quicksell UI did not appear or the sell could not be completed."),
    ],
    max_consecutive_retries=5,
)

from bartlebot.nodes.timing import INTERACT_TIMEOUT, POLL_INTERVAL  # noqa: E402

_ROWS_PER_PAGE = 10


async def run(machine, clients: list, params: dict) -> str:
    keep_list = [k.lower() for k in (params.get("keep_list") or [])]
    sell_crowns: bool = params.get("sell_crowns", True)
    sell_pets: bool = params.get("sell_pets", True)
    machine.log(f"quicksell: keep_list={keep_list!r} sell_crowns={sell_crowns} sell_pets={sell_pets}")

    results = await asyncio.gather(
        *[_process_client(machine, c, keep_list, sell_crowns, sell_pets) for c in clients],
        return_exceptions=True,
    )
    if any(isinstance(r, Exception) or r == "FAILURE" for r in results):
        return "FAILURE"
    return "SUCCESS"


async def _process_client(machine, client, keep_list: list[str], sell_crowns: bool, sell_pets: bool) -> str:
    """Check both backpack types and quicksell once if either is full."""
    # Open inventory page and check fullness.
    await client.send_key(Keycode.B, 0.1)
    await asyncio.sleep(POLL_INTERVAL)
    used, total = await client.backpack_space()
    inventory_full = used >= total
    machine.log(f"quicksell: inventory {used}/{total} — {'full' if inventory_full else 'not full'}", logging.DEBUG)

    # Switch to jewel page and check fullness.
    await client.send_key(Keycode.Y, 0.1)
    await asyncio.sleep(POLL_INTERVAL)
    jewel_full = await _is_jewel_backpack_full(client)
    machine.log(f"quicksell: jewel pouch — {'full' if jewel_full else 'not full'}", logging.DEBUG)

    # If nothing is full, return early
    if not inventory_full and not jewel_full:
        await _close_backpack(client)
        return "SUCCESS"

    full_desc = " and ".join(t for t, f in [("inventory", inventory_full), ("jewel", jewel_full)] if f)
    machine.log(f"quicksell: {full_desc} backpack full — opening quicksell UI")
    return await _run_quicksell(machine, client, keep_list, sell_crowns, sell_pets)


async def _run_quicksell(machine, client, keep_list: list[str], sell_crowns: bool, sell_pets: bool) -> str:
    """Open the quicksell UI from the jewel page and sell all unkeep-listed items."""
    # Wait for the quicksell open button to be visible, then click it.
    machine.log("quicksell: waiting for quicksell open button", logging.DEBUG)
    qs_button_visible = await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, QUICKSELL_JEWEL_OPEN_PATH),
        INTERACT_TIMEOUT,
        POLL_INTERVAL,
    )
    if not qs_button_visible:
        machine.log("FAILURE: quicksell open button did not appear", logging.WARNING)
        return "FAILURE"
    await node_helpers.click_by_path(client, QUICKSELL_JEWEL_OPEN_PATH)

    # Wait for the shop UI to appear.
    machine.log("quicksell: waiting for shop UI (QUICKSELL_ALL_BUTTON)", logging.DEBUG)
    appeared = await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, QUICKSELL_ALL_BUTTON_PATH),
        INTERACT_TIMEOUT,
        POLL_INTERVAL,
    )
    if not appeared:
        machine.log("FAILURE: quicksell UI did not appear", logging.WARNING)
        return "FAILURE"

    # Wait for the "All" tab and switch to it.
    machine.log("quicksell: waiting for All tab (QUICKSELL_ALL_TAB)", logging.DEBUG)
    all_tab_visible = await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, QUICKSELL_ALL_TAB_PATH),
        INTERACT_TIMEOUT,
        POLL_INTERVAL,
    )
    machine.log(f"quicksell: All tab {'appeared' if all_tab_visible else 'timed out'}", logging.DEBUG)
    await node_helpers.click_by_path(client, QUICKSELL_ALL_TAB_PATH)

    # Wait for the "Select All" button and click it.
    machine.log("quicksell: waiting for Select All button (QUICKSELL_ALL_BUTTON)", logging.DEBUG)
    select_all_visible = await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, QUICKSELL_ALL_BUTTON_PATH),
        INTERACT_TIMEOUT,
        POLL_INTERVAL,
    )
    machine.log(f"quicksell: Select All button {'appeared' if select_all_visible else 'timed out'}", logging.DEBUG)
    await node_helpers.click_by_path(client, QUICKSELL_ALL_BUTTON_PATH)
    await asyncio.sleep(POLL_INTERVAL)

    # Dismiss the "sell Crowns items?" dialog if it appears after selecting all items.
    # If sell_crowns is True, confirm (center/YES); if False, decline (right/NO) to exclude them.
    machine.log("quicksell: waiting for crowns select dialog (optional)", logging.DEBUG)
    crowns_dialog = await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, MESSAGE_BOX_CENTER),
        INTERACT_TIMEOUT,
        POLL_INTERVAL,
    )
    if crowns_dialog:
        btn = MESSAGE_BOX_CENTER if sell_crowns else MESSAGE_BOX_RIGHT
        machine.log(
            f"quicksell: crowns select dialog appeared — {'including' if sell_crowns else 'excluding'} crowns items",
            logging.DEBUG,
        )
        await node_helpers.click_by_path(client, btn)
    else:
        machine.log("quicksell: crowns select dialog not present, skipping", logging.DEBUG)

    # If pets should not be sold, switch to the Pet tab and click "All" to deselect them all.
    # This avoids the post-sell pets confirmation dialog, which would cancel the entire sale
    # if answered with No. After deselecting, switch back to All so the page loop sees everything.
    if not sell_pets:
        machine.log("quicksell: deselecting pets via Pet tab", logging.DEBUG)
        await node_helpers.click_by_path(client, QUICKSELL_PET_TAB_PATH)
        await asyncio.sleep(POLL_INTERVAL)
        await node_helpers.click_by_path(client, QUICKSELL_ALL_BUTTON_PATH)
        await asyncio.sleep(POLL_INTERVAL)
        await node_helpers.click_by_path(client, QUICKSELL_ALL_TAB_PATH)
        await asyncio.sleep(POLL_INTERVAL)

    # Page through items and uncheck any that match the keep_list.
    await _process_quicksell_pages(client, keep_list)

    # Wait for the sell button to be ready and click it.
    machine.log("quicksell: waiting for sell button (QUICKSELL_SELL_BUTTON)", logging.DEBUG)
    sell_button_visible = await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, QUICKSELL_SELL_BUTTON_PATH),
        INTERACT_TIMEOUT,
        POLL_INTERVAL,
    )
    machine.log(f"quicksell: sell button {'appeared' if sell_button_visible else 'timed out'}", logging.DEBUG)

    # If the sell button is grayed out, all items matched the keep list — nothing to sell.
    if not await node_helpers.is_enabled_by_path(client, QUICKSELL_SELL_BUTTON_PATH):
        machine.log("quicksell: all items matched keep list — closing shop and backpack")
        await node_helpers.poll_until(
            lambda: node_helpers.is_visible_by_path(client, QUICKSELL_CLOSE_BUTTON_PATH),
            INTERACT_TIMEOUT,
            POLL_INTERVAL,
        )
        await node_helpers.click_by_path(client, QUICKSELL_CLOSE_BUTTON_PATH)
        await _close_backpack(client)
        return "SUCCESS"

    await node_helpers.click_by_path(client, QUICKSELL_SELL_BUTTON_PATH)

    # Wait for and confirm the sell dialog.
    machine.log("quicksell: waiting for sell confirmation dialog", logging.DEBUG)
    confirmed = await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, QUICKSELL_CONFIRM_SELL_PATH),
        INTERACT_TIMEOUT,
        POLL_INTERVAL,
    )
    if not confirmed:
        machine.log("FAILURE: sell confirmation dialog did not appear", logging.WARNING)
        return "FAILURE"
    await node_helpers.click_by_path(client, QUICKSELL_CONFIRM_SELL_PATH)

    # Dismiss any post-sell message boxes (crowns confirm, gold limit warning).
    await _dismiss_post_sell_dialogs(machine, client)

    # Wait to return to the main backpack view and close it.
    machine.log("quicksell: waiting for backpack close button", logging.DEBUG)
    backpack_visible = await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, BACKPACK_CLOSE_BUTTON_PATH),
        INTERACT_TIMEOUT,
        POLL_INTERVAL,
    )
    machine.log(f"quicksell: backpack close button {'appeared' if backpack_visible else 'timed out'}", logging.DEBUG)
    await _close_backpack(client)
    machine.log("quicksell: sell complete")
    return "SUCCESS"


async def _dismiss_post_sell_dialogs(machine, client) -> None:
    """Dismiss message boxes that appear after confirming the sale.

    Handles up to 3 dialogs in sequence (Crowns items confirmation, Pets confirmation,
    gold limit warning). All are answered YES / OK — clicking No on any post-sell dialog
    cancels the entire sale. Unwanted item types are excluded before selling (e.g. pets
    are deselected via the Pet tab), so those dialogs will not appear in practice.
    """
    for _ in range(3):
        visible = await node_helpers.poll_until(
            lambda: node_helpers.is_visible_by_path(client, MESSAGE_BOX_CENTER),
            INTERACT_TIMEOUT,
            POLL_INTERVAL,
        )
        if not visible:
            break
        text = await _get_window_text(client, MESSAGE_BOX_CAPTION_PATH)
        machine.log(f"quicksell: post-sell dialog {text!r} — dismissing", logging.DEBUG)
        await node_helpers.click_by_path(client, MESSAGE_BOX_CENTER)
        await asyncio.sleep(POLL_INTERVAL)


async def _process_quicksell_pages(client, keep_list: list[str]) -> None:
    """Page through the quicksell UI and uncheck items that match any keep_list keyword."""
    while True:
        for row in range(_ROWS_PER_PAGE):
            text = await _get_window_text(client, quicksell_item_title_path(row))
            if not text:
                continue
            if any(kw in text.lower() for kw in keep_list):
                # Uncheck this item so it is not sold.
                await node_helpers.click_by_path(client, quicksell_item_checkbox_path(row))

        # Advance to the next page if the button is enabled, else stop.
        if await node_helpers.is_enabled_by_path(client, QUICKSELL_NEXT_PAGE_PATH):
            await node_helpers.click_by_path(client, QUICKSELL_NEXT_PAGE_PATH)
            await asyncio.sleep(POLL_INTERVAL)
        else:
            break


async def _is_jewel_backpack_full(client) -> bool:
    """Check jewel pouch fullness by reading the 'X/Y' capacity text in the jewel page UI."""
    import re

    text = await _get_window_text(client, JEWEL_INVENTORY_SPACE_PATH)
    # The game wraps the value in HTML (e.g. "<center>100/100"), so strip tags first.
    text = re.sub(r"<[^>]+>", "", text).strip()
    parts = text.split("/")
    if len(parts) != 2:
        return False
    try:
        return int(parts[0].strip()) >= int(parts[1].strip())
    except ValueError:
        return False


async def _get_window_text(client, path: list[str]) -> str:
    """Return the displayed text of the UI window at path, or '' if not found.

    Tries maybe_text() first; falls back to reading the wide string at offset 616,
    which is where control elements (like item name labels) store their text.
    """
    from wizwalker.errors import MemoryReadError

    window = await node_helpers._get_window_from_path(client.root_window, path)
    if window is None:
        return ""
    try:
        text = await window.maybe_text()
        if text:
            return text
    except (ValueError, MemoryReadError):
        pass
    try:
        text = await window.read_wide_string_from_offset(616)
        if text:
            return text
    except (ValueError, MemoryReadError):
        pass
    return ""


async def _close_backpack(client) -> None:
    """Click the backpack close button and wait for it to disappear."""
    await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, BACKPACK_CLOSE_BUTTON_PATH),
        INTERACT_TIMEOUT,
        POLL_INTERVAL,
    )
    await node_helpers.click_by_path(client, BACKPACK_CLOSE_BUTTON_PATH)

    async def _backpack_closed():
        return not await node_helpers.is_visible_by_path(client, BACKPACK_CLOSE_BUTTON_PATH)

    await node_helpers.poll_until(_backpack_closed, INTERACT_TIMEOUT, POLL_INTERVAL)
