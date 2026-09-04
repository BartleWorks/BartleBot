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

logger = logging.getLogger(__name__)

# Quicksell's settings are **bot options**, not node params, and every one of them
# is now user-owned except the keep list.
#
# What a user is willing to lose is a fact about that user -- their tolerance for a
# pet or a socket wrench going with the rest -- so it belongs beside their Safety
# Delay rather than in a file they share.  Which drops *this dungeon* yields that are
# worth keeping is the opposite: the author knows it and the user has no way to.  So
# the keep list is the one authorial declaration here, and the user's own list
# replaces it, seeded from the author's.
#
# There was one bot in the library with two quicksell nodes, and it existed only to
# offer a choice between two keep lists.  Under bot options that choice is a value
# the user edits directly, so the reason for a second node is gone, and
# ``hall_of_heroes`` is now one node declaring ``["Merciless"]`` -- never sell what
# the author flagged as valuable, since the user can clear the list in one click
# whereas the reverse loses gear.

#: The option whose answer decides whether this node does anything at all.
OPTION_ENABLED = "Enable Quicksell"

#: The authorial one: keywords naming this dungeon's drops worth keeping.
OPTION_KEEP_LIST = "Quicksell Keep List"

#: Internal key -> option name, for the three bools. The internal keys are what every
#: pure helper below is written against, and what the editor's picker speaks.
SELL_FLAG_OPTIONS: dict[str, str] = {
    "sell_pets": "Sell Pets",
    "sell_socket_wrenches": "Sell Socket Wrenches",
    "sell_skeleton_keys": "Sell Skeleton Keys",
}

#: The floor under each bool -- what to assume when nothing above has an answer.
#: Not the user's setting: that is a level above this, resolved by ``get_option``.
SELL_FLAG_DEFAULTS: dict[str, bool] = {
    "sell_pets": True,
    "sell_socket_wrenches": False,
    "sell_skeleton_keys": False,
}

#: Nothing left. Quicksell used to carry five params; all five are bot options now,
#: which is what lets one answer cover every quicksell node in a bot.
PARAM_SCHEMA: list[ParamField] = []

NODE_SPEC = NodeSpec(
    description=(
        "Checks if the main backpack or jewel pouch is full. "
        "If either is full, opens the quicksell UI and sells all items except those "
        "whose names match a keyword in keep_list. "
        "sell_pets controls whether pets are included — it is all-or-nothing, because the "
        "quicksell window shows a pet's nickname rather than its body type and no keep-list "
        "keyword can match it. Crowns items are always included."
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
            minimum=0.0,
            suffix=" s",
            description=(
                "Seconds to allow for game-server responses when waiting for prompts or menus. "
                "Increase on high-latency connections."
            ),
        ),
        BotOptionSpec(
            name=OPTION_ENABLED,
            type="bool",
            # Null: the author has no opinion, so the app setting answers. Quicksell
            # is on by default, which is what every bot did before it could be off.
            default=None,
            description=(
                "Whether this bot sells anything at all. Turn it off to farm without selling: the "
                "backpack fills, and after that new drops go to your shared bank instead. Once the "
                "bank is full too, only Crowns items are still kept and everything else is lost."
            ),
        ),
        BotOptionSpec(
            name=SELL_FLAG_OPTIONS["sell_pets"],
            type="bool",
            default=None,
            depends_on=OPTION_ENABLED,
            description=(
                "Whether pets are sold along with everything else. The keep list cannot spare an "
                "individual pet: the quicksell window lists a pet by the nickname its owner gave it, "
                "never by the body type a drop table names it with, so no keyword ever matches one. "
                "Turning this off is the only thing that keeps a pet."
            ),
        ),
        BotOptionSpec(
            name=SELL_FLAG_OPTIONS["sell_socket_wrenches"],
            type="bool",
            default=None,
            depends_on=OPTION_ENABLED,
            description=(
                "Whether items whose name contains 'Socket Wrench' are sold. Off adds that keyword to the keep list."
            ),
        ),
        BotOptionSpec(
            name=SELL_FLAG_OPTIONS["sell_skeleton_keys"],
            type="bool",
            default=None,
            depends_on=OPTION_ENABLED,
            description=(
                "Whether items whose name contains 'Skeleton Key' are sold. Off adds that keyword to the keep list."
            ),
        ),
        BotOptionSpec(
            name=OPTION_KEEP_LIST,
            # Node-declared type: a str_list underneath, shown in Setup as this bot's
            # own drop table with a tick box per item.
            type="drop_keep_list",
            default=[],
            description=(
                "Keywords to match against item names when quickselling. Any item whose name "
                "contains one of these (case-insensitive, partial matches count) is not sold. "
                "The bot's author recommends a list; yours replaces it."
            ),
        ),
    ],
    max_consecutive_retries=5,
    safety_warning=(
        "This bot quicksells items when your backpack is full. "
        "Ensure that you have locked any valuable items or have added them to the keep list in "
        "Setup. "
        "Pets are the exception: the quicksell window lists them by nickname, so the keep list "
        "cannot spare one — turn Sell Pets off to keep your pets. "
        "With Enable Quicksell off nothing is sold: the backpack fills, further drops overflow "
        "into your shared bank, and once that is full too only Crowns items are kept."
    ),
)

from bartlebot.nodes.timing import POLL_INTERVAL, QUICKSELL_COMPLETE_TIMEOUT  # noqa: E402

_ROWS_PER_PAGE = 10

# Bool params that add a keyword to the keep list when switched *off*.
IMPLIED_KEEP_KEYWORDS: dict[str, str] = {
    "sell_socket_wrenches": "socket wrench",
    "sell_skeleton_keys": "skeleton key",
}

# Bool params that spare a whole wiki category when switched *off*, rather than
# adding a keyword. sell_pets is not a name match at all: the node deselects the
# Pet tab wholesale, so no keep-list entry describes what it does.
#
# Editor-only, like NEVER_QUICKSOLD_CATEGORIES and for the same reason — the node
# never sees a category. This exists so the picker does not show a pet as "will be
# sold" while the config plainly says otherwise.
CATEGORY_SELL_FLAGS: dict[str, str] = {"sell_pets": "Pets"}

# Categories the quicksell window labels with something other than the item's own
# name, so no keep-list keyword can ever match a row in them.
#
# Pets are the only one, and the mismatch is total rather than partial: the drop
# table names a pet by its body type ("Storm Potbelly"), while the quicksell row
# shows the nickname its owner gave it ("Princess Sparkles"). The two strings
# share nothing, so a keep-list entry for a pet silently fails and the pet is
# sold. Turning Sell Pets off is the only thing that keeps one, because it
# deselects the Pet tab wholesale instead of matching text.
#
# Editor-only, like the two sets below: the node sees names, never categories.
UNMATCHABLE_CATEGORIES: dict[str, str] = {
    "Pets": (
        "The quicksell window lists a pet by its nickname, not the body type shown here, "
        "so no keep-list keyword can spare it. Turn Sell Pets off to keep every pet."
    ),
}

# Wiki drop categories this node will never sell, and why. Editor-only: the node
# reads item names off the quicksell window and never sees a category, so this
# cannot be consulted at run time — it exists so the keep-list picker can leave
# out rows where there is no decision to make.
#
# Keys must match the category strings the wiki import produces. Run
# ``uv run python tools/drop_categories.py`` after importing new bots to
# catch a category nobody has classified yet.
#
# Provenance: established by hand from how the game behaves, not from any file we
# can read or any check that runs. Nothing here is verified automatically, and a
# game update that made one of these quicksellable would go unnoticed. That is
# tolerable because of which way the error falls — a wrong entry hides a row from
# the picker, it never sells something the user asked to keep. Re-checking one is
# cheap: open the quicksell window with the item in the backpack and see whether
# it is listed.
NEVER_QUICKSOLD_CATEGORIES: dict[str, str] = {
    "Reagents": "Reagents cannot be quicksold.",
    "Snacks": "Snacks cannot be quicksold.",
    "Spellements": "Spellements cannot be quicksold.",
    "Mounts": "Mounts cannot be quicksold.",
    "Treasure Cards": "Treasure cards sell through a separate UI this node does not use.",
}

# Categories confirmed to go through the quicksell window. Tracked alongside the
# never-sold set so tools/drop_categories.py can tell "known to sell" apart from
# "not yet looked at" — Pins are listed because quicksell treats them as Jewels.
QUICKSELLABLE_CATEGORIES: frozenset[str] = frozenset(
    {
        "Amulets",
        "Athames",
        "Boots",
        "Decks",
        "Hats",
        "Housing Items",
        "Jewels",
        "Pets",
        "Pins",
        "Rings",
        "Robes",
        "Seeds",
        "Wands",
    }
)


def resolve_options(get_option) -> dict:
    """Collapse the five quicksell options into the plain dict everything else reads.

    This is the one place the option layer touches quicksell.  Below it nothing
    knows an option exists: the keep-list logic, the picker and ``run()`` all see
    ``{"enabled": bool, "keep_list": [...], "sell_pets": bool, ...}``, which is what
    keeps them pure and testable without a settings file.

    *get_option* is ``BotMachine.get_option`` at run time and a settings-backed
    lookup in the editor.  Its answer is checked rather than trusted: an override
    written under an older type, or a stub that answers everything with a number,
    must not turn a bool into "sell everything".  A value of the wrong shape falls
    back to the floor in ``SELL_FLAG_DEFAULTS``, never to something more destructive.
    """
    keep_list = get_option(OPTION_KEEP_LIST, [])
    enabled = get_option(OPTION_ENABLED, True)
    values: dict = {
        "enabled": enabled if isinstance(enabled, bool) else True,
        "keep_list": [str(k) for k in keep_list] if isinstance(keep_list, list) else [],
    }
    for key, name in SELL_FLAG_OPTIONS.items():
        value = get_option(name, SELL_FLAG_DEFAULTS[key])
        values[key] = value if isinstance(value, bool) else SELL_FLAG_DEFAULTS[key]
    return values


def resolve_options_for(config, settings=None) -> dict:
    """The same resolved dict ``run()`` builds, for a *config* the editor is holding.

    The editor has no machine, so it resolves through ``option_values`` instead --
    the same four levels, the same order.  Kept here rather than in each caller so
    the picker, the safety dialog and the run cannot drift apart about what this
    bot will sell.
    """
    from bartlebot import option_values  # noqa: PLC0415 - avoids a settings import at node load

    return resolve_options(
        lambda name, default=None: option_values.option_value(config, name, default, settings=settings)
    )


def describe_config(params: dict) -> str:
    """One line naming everything this quicksell will do, for the log."""
    return f"enabled={params.get('enabled', True)} keep_list={effective_keep_list(params)!r} " + " ".join(
        f"{key}={sell_flag(params, key)}" for key in SELL_FLAG_OPTIONS
    )


def _typed_keywords(params: dict) -> list[str]:
    """The keep_list param, lowercased. Blank entries are dropped: an empty keyword
    is a substring of every name and would silently keep the entire backpack."""
    return [k for k in (kw.lower().strip() for kw in (params.get("keep_list") or [])) if k]


def sell_flag(params: dict, param_key: str) -> bool:
    """One resolved sell_* value.

    Callers holding the options should run them through ``resolve_options`` first,
    which is where the user's answer comes from. Reaching this with a missing key is
    still well defined — it falls back to the floor in SELL_FLAG_DEFAULTS — but that
    is a floor, not a substitute: nothing in a pure function may go and read the
    settings itself.
    """
    value = params.get(param_key)
    return value if isinstance(value, bool) else SELL_FLAG_DEFAULTS[param_key]


def _implied_keywords(params: dict) -> list[str]:
    """The keywords contributed by the sell_* bools rather than by the keep_list."""
    return [keyword for param_key, keyword in IMPLIED_KEEP_KEYWORDS.items() if not sell_flag(params, param_key)]


def category_keep_reasons(params: dict) -> dict[str, str]:
    """Categories this config spares wholesale, mapped to why.

    Distinct from NEVER_QUICKSOLD_CATEGORIES, which is a fact about the game: these
    depend on the node's params, and flipping the bool sells the category again.
    The picker keeps such rows in the sellable count for that reason — they *are*
    sellable, this config just is not selling them.
    """
    reasons = {}
    for param_key, category in CATEGORY_SELL_FLAGS.items():
        if not sell_flag(params, param_key):
            label = SELL_FLAG_OPTIONS.get(param_key, param_key)
            reasons[category] = f"Kept because {label} is off, not by the keep list."
    return reasons


def category_unmatchable_reasons(params: dict) -> dict[str, str]:
    """Categories the keep list cannot act on under this config, mapped to why.

    A category already spared by a sell_* bool is left out: category_keep_reasons
    has the say there, and a row that is safely kept needs no warning. So this is
    empty for Pets when Sell Pets is off, and names Pets when it is on — exactly
    when a ticked pet would be sold anyway.
    """
    spared = category_keep_reasons(params)
    return {category: reason for category, reason in UNMATCHABLE_CATEGORIES.items() if category not in spared}


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
      0. Resolve the five bot options via resolve_options(). Enable Quicksell off is
         SUCCESS without touching a client -- a node that does nothing has not failed.
      1. Build the effective keep_list via effective_keep_list(): the Quicksell Keep
         List option plus the keywords implied by the sell_* bools that are off. The
         Setup picker resolves the same way, so what it shows is what this will do.
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
    # The one place the option layer is read. Everything below sees plain values;
    # which level answered each one is already in the log, written once per run by
    # get_option itself, so this line says only what the node will do with them.
    params = resolve_options(machine.get_option)
    keep_list = effective_keep_list(params)
    sell_pets: bool = params["sell_pets"]
    machine.log(f"quicksell: {describe_config(params)}")

    if not params["enabled"]:
        # Not a failure: the user turned selling off, and the run carries on without
        # it. What that costs them is in the option's own description and in the
        # safety dialog -- the backpack fills, then the bank, and after that only
        # Crowns items are kept.
        machine.log("quicksell: Enable Quicksell is off — nothing will be sold.")
        return "SUCCESS"

    # Only when the user typed keywords of their own: the implied ones can never
    # name a pet, so keying off the effective list would fire this on every bot
    # that leaves the defaults alone.
    if sell_pets and _typed_keywords(params):
        machine.log(
            "quicksell: Sell Pets is on — the quicksell window lists pets by their nickname, not the "
            "body type a keep list names them by, so no keyword can spare a pet. Turn Sell Pets off "
            "to keep pets.",
            logging.INFO,
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
    # The game wraps the value in HTML (e.g. "<center>100/100"), so strip tags first.
    text = node_helpers.strip_html(await node_helpers.get_text_by_path(client, JEWEL_INVENTORY_SPACE_PATH))
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
