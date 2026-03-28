"""Node: quicksell backpack items when full, keeping items that match a keyword list."""

import asyncio
import logging

from wizwalker import Keycode

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import (
    BACKPACK_CLOSE_BUTTON_PATH,
    GOLD_LIMIT_OK_PATH,
    JEWEL_INVENTORY_SPACE_PATH,
    QUICKSELL_ALL_BUTTON_PATH,
    QUICKSELL_CONFIRM_SELL_PATH,
    QUICKSELL_INVENTORY_OPEN_PATH,
    QUICKSELL_JEWEL_OPEN_PATH,
    QUICKSELL_NEXT_PAGE_PATH,
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
]

NODE_SPEC = NodeSpec(
    description=(
        "Checks if the main backpack or jewel pouch is full. "
        "If either is full, opens the quicksell UI and sells all items except those "
        "whose names match a keyword in keep_list."
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
_GOLD_WARNING_TIMEOUT = 2.0


async def run(machine, clients: list, params: dict) -> str:
    keep_list = [k.lower() for k in (params.get("keep_list") or [])]
    machine.log(f"quicksell: keep_list={keep_list!r}")

    results = await asyncio.gather(
        *[_process_client(machine, c, keep_list) for c in clients],
        return_exceptions=True,
    )
    if any(isinstance(r, Exception) or r == "FAILURE" for r in results):
        return "FAILURE"
    return "SUCCESS"


async def _process_client(machine, client, keep_list: list[str]) -> str:
    """Check and quicksell both backpack types (main inventory, then jewel pouch) for one client."""
    for backpack_type in ("inventory", "jewel"):
        result = await _check_and_quicksell(machine, client, keep_list, backpack_type)
        if result == "FAILURE":
            return "FAILURE"
    return "SUCCESS"


async def _check_and_quicksell(machine, client, keep_list: list[str], backpack_type: str) -> str:
    """Open the specified backpack page, check if it is full, and quicksell if so."""
    # Open the correct page.
    if backpack_type == "inventory":
        await client.send_key(Keycode.B, 0.1)
        open_path = QUICKSELL_INVENTORY_OPEN_PATH
    else:
        await client.send_key(Keycode.Y, 0.1)
        open_path = QUICKSELL_JEWEL_OPEN_PATH

    await asyncio.sleep(POLL_INTERVAL)

    # Check fullness.
    if backpack_type == "inventory":
        used, total = await client.backpack_space()
        is_full = used >= total
        machine.log(f"quicksell: inventory {used}/{total} — {'full' if is_full else 'not full'}", logging.DEBUG)
    else:
        is_full = await _is_jewel_backpack_full(client)
        machine.log(f"quicksell: jewel pouch — {'full' if is_full else 'not full'}", logging.DEBUG)

    if not is_full:
        await _close_backpack(client)
        return "SUCCESS"

    machine.log(f"quicksell: {backpack_type} backpack full — opening quicksell UI")

    # Click the quicksell open button and wait for the shop UI to appear.
    await node_helpers.click_by_path(client, open_path)
    appeared = await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, QUICKSELL_ALL_BUTTON_PATH),
        INTERACT_TIMEOUT,
        POLL_INTERVAL,
    )
    if not appeared:
        machine.log(f"FAILURE: quicksell UI did not appear for {backpack_type} backpack", logging.WARNING)
        return "FAILURE"

    # Select all items to sell.
    await node_helpers.click_by_path(client, QUICKSELL_ALL_BUTTON_PATH)
    await asyncio.sleep(POLL_INTERVAL)

    # Page through items and uncheck any that match the keep_list.
    await _process_quicksell_pages(client, keep_list)

    # Sell.
    await node_helpers.click_by_path(client, QUICKSELL_SELL_BUTTON_PATH)

    # Confirm the sell dialog.
    confirmed = await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, QUICKSELL_CONFIRM_SELL_PATH),
        INTERACT_TIMEOUT,
        POLL_INTERVAL,
    )
    if not confirmed:
        machine.log(f"FAILURE: sell confirmation dialog did not appear for {backpack_type} backpack", logging.WARNING)
        return "FAILURE"
    await node_helpers.click_by_path(client, QUICKSELL_CONFIRM_SELL_PATH)

    # Dismiss the gold limit warning if it appears (it's optional — don't wait long).
    await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, GOLD_LIMIT_OK_PATH),
        _GOLD_WARNING_TIMEOUT,
        POLL_INTERVAL,
    )
    await node_helpers.click_by_path(client, GOLD_LIMIT_OK_PATH)

    # Wait to return to the main backpack view.
    await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, BACKPACK_CLOSE_BUTTON_PATH),
        INTERACT_TIMEOUT,
        POLL_INTERVAL,
    )
    await _close_backpack(client)
    machine.log(f"quicksell: {backpack_type} backpack sell complete")
    return "SUCCESS"


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
    text = await _get_window_text(client, JEWEL_INVENTORY_SPACE_PATH)
    parts = text.split("/")
    if len(parts) != 2:
        return False
    try:
        return int(parts[0].strip()) >= int(parts[1].strip())
    except ValueError:
        return False


async def _get_window_text(client, path: list[str]) -> str:
    """Return the displayed text of the UI window at path, or '' if not found."""
    window = await node_helpers._get_window_from_path(client.root_window, path)
    if window is None:
        return ""
    return await window.maybe_text() or ""


async def _close_backpack(client) -> None:
    """Click the backpack close button and wait for it to disappear."""
    await node_helpers.click_by_path(client, BACKPACK_CLOSE_BUTTON_PATH)

    async def _backpack_closed():
        return not await node_helpers.is_visible_by_path(client, BACKPACK_CLOSE_BUTTON_PATH)

    await node_helpers.poll_until(_backpack_closed, INTERACT_TIMEOUT, POLL_INTERVAL)
