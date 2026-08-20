"""Node: quicksell backpack items when full, keeping items that match a keyword list."""

import asyncio
import logging
from dataclasses import dataclass

from wizwalker import Keycode

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import (
    BACKPACK_CLOSE_BUTTON_PATH,
    JEWEL_INVENTORY_SPACE_PATH,
    MESSAGE_BOX_CAPTION_PATH,
    MESSAGE_BOX_CENTER,
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
        type="drop_keep_list",
        label="Keep List",
        help=(
            "Keywords to match against item names when quickselling. "
            "Any item whose name contains one of these strings (case-insensitive) will NOT be sold. "
            "Partial matches count — 'hat' will keep any item with 'hat' in the name. "
            "Tick an item below to add its exact name to the list."
        ),
        default=[],
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
    ParamField(
        key="sell_socket_wrenches",
        type="bool",
        label="Sell Socket Wrenches",
        help="If disabled, any item whose name contains 'Socket Wrench' will be kept and not sold.",
        default=False,
        required=False,
    ),
    ParamField(
        key="sell_skeleton_keys",
        type="bool",
        label="Sell Skeleton Keys",
        help="If disabled, any item whose name contains 'Skeleton Key' will be kept and not sold.",
        default=False,
        required=False,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Checks if the main backpack or jewel pouch is full. "
        "If either is full, opens the quicksell UI and sells all items except those "
        "whose names match a keyword in keep_list. "
        "sell_pets controls whether pets are included. Crowns items are always included."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Neither backpack was full, or items were sold successfully."),
        ReturnSpec("FAILURE", "The quicksell UI did not appear or the sell could not be completed."),
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
    max_consecutive_retries=5,
    safety_warning=(
        "This bot quicksells items when your backpack is full. "
        "Ensure that you have locked any valuable items or have added them "
        "to the keep list in the quicksell node parameters."
    ),
)

from bartlebot.nodes.timing import POLL_INTERVAL, QUICKSELL_COMPLETE_TIMEOUT  # noqa: E402

_ROWS_PER_PAGE = 10

# Bool params that add a keyword to the keep list when switched *off*.
IMPLIED_KEEP_KEYWORDS: dict[str, str] = {
    "sell_socket_wrenches": "socket wrench",
    "sell_skeleton_keys": "skeleton key",
}


def _typed_keywords(params: dict) -> list[str]:
    """The keep_list param, lowercased. Blank entries are dropped: an empty keyword
    is a substring of every name and would silently keep the entire backpack."""
    return [k for k in (kw.lower().strip() for kw in (params.get("keep_list") or [])) if k]


def _implied_keywords(params: dict) -> list[str]:
    """The keywords contributed by the sell_* bools rather than by the keep_list."""
    return [keyword for param_key, keyword in IMPLIED_KEEP_KEYWORDS.items() if not params.get(param_key, False)]


def effective_keep_list(params: dict) -> list[str]:
    """Return the lowercased keywords this config keeps items by."""
    return [*_typed_keywords(params), *_implied_keywords(params)]


def matching_keyword(item_name: str, keywords: list[str]) -> str | None:
    """Return the first keyword in *keywords* that keeps *item_name*, or None.

    Matching is the case-insensitive substring test the quicksell page loop uses,
    so the answer is exactly what the node will do to that item.
    """
    lowered = item_name.lower()
    for keyword in keywords:
        if keyword and keyword in lowered:
            return keyword
    return None


@dataclass(frozen=True)
class DropKeepState:
    """How one item from a bot's drop table fares under a quicksell config.

    Attributes:
        name:    The item name as it appears in the drop table.
        keyword: The keep-list keyword that spares it, or None when it will be sold.
        exact:   True when *keyword* is a keep_list entry equal to the item's own
                 name, so ticking or unticking the item is enough to change its
                 fate. False for a partial match, and false for an implied keyword
                 even when it happens to equal the name ("Socket Wrench") — removing
                 that one means flipping the sell_* bool, not editing the keep list.
    """

    name: str
    keyword: str | None
    exact: bool

    @property
    def kept(self) -> bool:
        return self.keyword is not None


def drop_keep_states(drops: list[str], params: dict) -> list[DropKeepState]:
    """Report what a quicksell config does to each item in a bot's drop table."""
    typed = _typed_keywords(params)
    implied = _implied_keywords(params)
    states = []
    for name in drops:
        keyword = matching_keyword(name, typed)
        if keyword is not None:
            states.append(DropKeepState(name, keyword, exact=keyword == name.lower().strip()))
        else:
            states.append(DropKeepState(name, matching_keyword(name, implied), exact=False))
    return states


def set_drops_in_keep_list(keep_list: list[str], names: list[str], keep: bool) -> list[str]:
    """Return *keep_list* with every name in *names* added or removed, in one pass.

    The bulk form of toggle_drop_in_keep_list, for the picker's check/uncheck-all
    buttons: doing it one name at a time would rescan the list per name, and a
    drop table can hold hundreds of entries.
    """
    targets = {name.lower().strip() for name in names}
    remaining = [k for k in keep_list if k.lower().strip() not in targets]
    return [*remaining, *names] if keep else remaining


def toggle_drop_in_keep_list(keep_list: list[str], name: str, keep: bool) -> list[str]:
    """Return *keep_list* with the exact item *name* added or removed.

    Only entries equal to *name* are touched, so a broader keyword the user typed
    themselves survives an untick — it simply cannot be removed from here.
    """
    target = name.lower().strip()
    remaining = [k for k in keep_list if k.lower().strip() != target]
    return [*remaining, name] if keep else remaining


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.backpack_space(), client.send_key(Keycode.B), client.send_key(Keycode.Y),
          node_helpers.is_visible_by_path(), node_helpers.is_enabled_by_path(),
          node_helpers.click_by_path(), node_helpers.get_text_by_path()

    Flow (per client, run concurrently via asyncio.gather):
      1. Build the effective keep_list via effective_keep_list(): the keep_list param
         plus the keywords implied by the sell_* bools. The properties panel calls the
         same function to show which of the bot's drops this config will keep.
      2. Open the inventory page (B key); read backpack_space() to check if full.
      3. Switch to the jewel page (Y key); read the jewel capacity text to check if full.
      4. If neither is full, close the backpack and return SUCCESS.
      5. Click the quicksell open button on the jewel page.
      6. Wait for the shop UI to appear (QUICKSELL_ALL_BUTTON).
      7. Click the "All" tab to see all item types.
      8. Click "Select All" to check every item.
      9. Handle the crowns dialog if it appears: always confirm (YES) to include all crowns items.
      10. If sell_pets is False, switch to the Pet tab, click All to deselect pets,
          then return to the All tab.
      11. Page through the quicksell list and uncheck any item whose name matches a
          keyword in keep_list (case-insensitive partial match).
      12. If the Sell button is grayed out (all items unchecked), close the shop and
          backpack and return SUCCESS.
      13. Click the Sell button; wait for the sell confirmation dialog; confirm it.
      14. Dismiss up to 3 post-sell dialogs (crowns confirm, pets confirm, gold limit
          warning) by clicking the center/YES button each time.
      15. Wait for the backpack close button to reappear, then close the backpack.

    FAILURE: quicksell open button did not appear; shop UI did not appear;
    sell confirmation dialog did not appear.
    """
    keep_list = effective_keep_list(params)
    sell_pets: bool = params.get("sell_pets", True)
    sell_socket_wrenches: bool = params.get("sell_socket_wrenches", False)
    sell_skeleton_keys: bool = params.get("sell_skeleton_keys", False)
    machine.log(
        f"quicksell: keep_list={keep_list!r} sell_pets={sell_pets} "
        f"sell_socket_wrenches={sell_socket_wrenches} sell_skeleton_keys={sell_skeleton_keys}"
    )

    safety_delay: float = machine.get_option("Safety Delay", 3.0)
    results = await asyncio.gather(
        *[_process_client(machine, c, keep_list, sell_pets, safety_delay) for c in clients],
        return_exceptions=True,
    )
    if any(isinstance(r, Exception) or r == "FAILURE" for r in results):
        return "FAILURE"
    return "SUCCESS"


async def _process_client(machine, client, keep_list: list[str], sell_pets: bool, safety_delay: float) -> str:
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
        await _close_backpack(client, safety_delay)
        return "SUCCESS"

    full_desc = " and ".join(t for t, f in [("inventory", inventory_full), ("jewel", jewel_full)] if f)
    machine.log(f"quicksell: {full_desc} backpack full — opening quicksell UI")
    return await _run_quicksell(machine, client, keep_list, sell_pets, safety_delay)


async def _run_quicksell(machine, client, keep_list: list[str], sell_pets: bool, safety_delay: float) -> str:
    """Open the quicksell UI from the jewel page and sell all unkeep-listed items."""
    # Wait for the quicksell open button to be visible, then click it.
    machine.log("quicksell: waiting for quicksell open button", logging.DEBUG)
    qs_button_visible = await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, QUICKSELL_JEWEL_OPEN_PATH),
        safety_delay,
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
        safety_delay,
        POLL_INTERVAL,
    )
    if not appeared:
        machine.log("FAILURE: quicksell UI did not appear", logging.WARNING)
        return "FAILURE"

    # Wait for the "All" tab and switch to it.
    machine.log("quicksell: waiting for All tab (QUICKSELL_ALL_TAB)", logging.DEBUG)
    all_tab_visible = await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, QUICKSELL_ALL_TAB_PATH),
        safety_delay,
        POLL_INTERVAL,
    )
    machine.log(f"quicksell: All tab {'appeared' if all_tab_visible else 'timed out'}", logging.DEBUG)
    await node_helpers.click_by_path(client, QUICKSELL_ALL_TAB_PATH)

    # Wait for the "Select All" button and click it.
    machine.log("quicksell: waiting for Select All button (QUICKSELL_ALL_BUTTON)", logging.DEBUG)
    select_all_visible = await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, QUICKSELL_ALL_BUTTON_PATH),
        safety_delay,
        POLL_INTERVAL,
    )
    machine.log(f"quicksell: Select All button {'appeared' if select_all_visible else 'timed out'}", logging.DEBUG)
    await node_helpers.click_by_path(client, QUICKSELL_ALL_BUTTON_PATH)
    await asyncio.sleep(POLL_INTERVAL)

    # Dismiss the "sell Crowns items?" dialog if it appears after selecting all items.
    # Always confirm (YES) so all items are guaranteed checked before the keep_list page loop.
    machine.log("quicksell: waiting for crowns select dialog (optional)", logging.DEBUG)
    crowns_dialog = await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, MESSAGE_BOX_CENTER),
        safety_delay,
        POLL_INTERVAL,
    )
    if crowns_dialog:
        machine.log("quicksell: crowns select dialog appeared — including crowns items", logging.DEBUG)
        await node_helpers.click_by_path(client, MESSAGE_BOX_CENTER)
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
        safety_delay,
        POLL_INTERVAL,
    )
    machine.log(f"quicksell: sell button {'appeared' if sell_button_visible else 'timed out'}", logging.DEBUG)

    # If the sell button is grayed out, all items matched the keep list — nothing to sell.
    if not await node_helpers.is_enabled_by_path(client, QUICKSELL_SELL_BUTTON_PATH):
        machine.log("quicksell: all items matched keep list — closing shop and backpack")
        await node_helpers.poll_until(
            lambda: node_helpers.is_visible_by_path(client, QUICKSELL_CLOSE_BUTTON_PATH),
            safety_delay,
            POLL_INTERVAL,
        )
        await node_helpers.click_by_path(client, QUICKSELL_CLOSE_BUTTON_PATH)
        await _close_backpack(client, safety_delay)
        return "SUCCESS"

    await node_helpers.click_by_path(client, QUICKSELL_SELL_BUTTON_PATH)

    # Wait for and confirm the sell dialog.
    machine.log("quicksell: waiting for sell confirmation dialog", logging.DEBUG)
    confirmed = await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, QUICKSELL_CONFIRM_SELL_PATH),
        safety_delay,
        POLL_INTERVAL,
    )
    if not confirmed:
        machine.log("FAILURE: sell confirmation dialog did not appear", logging.WARNING)
        return "FAILURE"
    await node_helpers.click_by_path(client, QUICKSELL_CONFIRM_SELL_PATH)

    # Dismiss any post-sell message boxes (crowns confirm, gold limit warning).
    await _dismiss_post_sell_dialogs(machine, client, safety_delay)

    # Wait to return to the main backpack view and close it.
    # Use QUICKSELL_COMPLETE_TIMEOUT rather than safety_delay here: selling a full
    # backpack triggers a server-side transaction that can cause noticeable lag,
    # and the close button may take longer than safety_delay to reappear even on
    # a successful sale.
    machine.log("quicksell: waiting for backpack close button", logging.DEBUG)
    backpack_visible = await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, BACKPACK_CLOSE_BUTTON_PATH),
        QUICKSELL_COMPLETE_TIMEOUT,
        POLL_INTERVAL,
    )
    machine.log(f"quicksell: backpack close button {'appeared' if backpack_visible else 'timed out'}", logging.DEBUG)
    await _close_backpack(client, safety_delay)
    machine.log("quicksell: sell complete")
    return "SUCCESS"


async def _dismiss_post_sell_dialogs(machine, client, safety_delay: float) -> None:
    """Dismiss message boxes that appear after confirming the sale.

    Handles up to 3 dialogs in sequence (Crowns items confirmation, Pets confirmation,
    gold limit warning). All are answered YES / OK — clicking No on any post-sell dialog
    cancels the entire sale. Unwanted item types are excluded before selling (e.g. pets
    are deselected via the Pet tab), so those dialogs will not appear in practice.
    """
    for _ in range(3):
        visible = await node_helpers.poll_until(
            lambda: node_helpers.is_visible_by_path(client, MESSAGE_BOX_CENTER),
            safety_delay,
            POLL_INTERVAL,
        )
        if not visible:
            break
        text = await node_helpers.get_text_by_path(client, MESSAGE_BOX_CAPTION_PATH)
        machine.log(f"quicksell: post-sell dialog {text!r} — dismissing", logging.DEBUG)
        await node_helpers.click_by_path(client, MESSAGE_BOX_CENTER)
        await asyncio.sleep(POLL_INTERVAL)


async def _process_quicksell_pages(client, keep_list: list[str]) -> None:
    """Page through the quicksell UI and uncheck items that match any keep_list keyword."""
    while True:
        for row in range(_ROWS_PER_PAGE):
            text = await node_helpers.get_text_by_path(client, quicksell_item_title_path(row))
            if not text:
                continue
            if matching_keyword(text, keep_list) is not None:
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

    text = await node_helpers.get_text_by_path(client, JEWEL_INVENTORY_SPACE_PATH)
    # The game wraps the value in HTML (e.g. "<center>100/100"), so strip tags first.
    text = re.sub(r"<[^>]+>", "", text).strip()
    parts = text.split("/")
    if len(parts) != 2:
        return False
    try:
        return int(parts[0].strip()) >= int(parts[1].strip())
    except ValueError:
        return False


async def _close_backpack(client, safety_delay: float) -> None:
    """Click the backpack close button and wait for it to disappear."""
    await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, BACKPACK_CLOSE_BUTTON_PATH),
        safety_delay,
        POLL_INTERVAL,
    )
    await node_helpers.click_by_path(client, BACKPACK_CLOSE_BUTTON_PATH)

    async def _backpack_closed():
        return not await node_helpers.is_visible_by_path(client, BACKPACK_CLOSE_BUTTON_PATH)

    await node_helpers.poll_until(_backpack_closed, safety_delay, POLL_INTERVAL)
