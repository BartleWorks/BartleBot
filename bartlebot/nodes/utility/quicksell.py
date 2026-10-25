"""Node: quicksell backpack items when full, keeping items that match a keyword list."""

import asyncio
import enum
import logging
from dataclasses import dataclass
from typing import NamedTuple

from wizwalker import Keycode

from bartlebot.game import dialog_captions
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import SAFETY_DELAY_OPTION, BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import (
    BACKPACK_CLOSE_BUTTON_PATH,
    JEWEL_INVENTORY_SPACE_PATH,
    MESSAGE_BOX_CENTER,
    MESSAGE_BOX_LEFT,
    MESSAGE_BOX_RIGHT,
    QUICKSELL_ALL_BUTTON_PATH,
    QUICKSELL_ALL_TAB_PATH,
    QUICKSELL_CLOSE_BUTTON_PATH,
    QUICKSELL_CONFIRM_SELL_PATH,
    QUICKSELL_JEWEL_OPEN_PATH,
    QUICKSELL_NEXT_PAGE_PATH,
    QUICKSELL_PET_TAB_PATH,
    QUICKSELL_SELL_BUTTON_PATH,
    QUICKSELL_SHOP_PATH,
    quicksell_item_checkbox_path,
    quicksell_item_title_path,
)
from bartlebot.options.option_groups import GROUP_QUICKSELL

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
# The user *also* has a keep list of their own in Settings -> Bot (#34), for what they
# never want sold whatever they farm. It is not a fifth level of the keep list option:
# a resolution picks one winner, and nearly every bot declares a list, which would
# outrank it in every bot. It is applied beside the option instead, by every bot,
# whatever the option resolved to -- so a bot's keep list can only ever add to it.
#
# There was one bot in the library with two quicksell nodes, and it existed only to
# offer a choice between two keep lists.  Under bot options that choice is a value
# the user edits directly, so the reason for a second node is gone, and
# ``hall_of_heroes`` is now one node declaring ``["Merciless"]`` -- never sell what
# the author flagged as valuable, since the user can clear the list in one click
# whereas the reverse loses gear.

#: The option whose answer decides whether this node does anything at all.
OPTION_ENABLED = "Quicksell"

#: The authorial one: keywords naming this dungeon's drops worth keeping.
OPTION_KEEP_LIST = "Quicksell Keep List"

#: The ``general`` settings key holding the user's own keep list, which every bot applies.
USER_KEEP_LIST_KEY = "quicksell_keep_list"

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

#: The one part of the safety warning that is still true when nothing is sold.
#:
#: Kept apart so the two readers cannot drift: the full warning below ends with it,
#: and the safety dialog shows it *alone* when a bot the author disabled quicksell
#: in is run -- the sale the rest of the warning is about is not going to happen,
#: but a backpack quietly filling over a long unattended farm still is.
SAFETY_WARNING_DISABLED = (
    "With quicksell off, nothing is sold. A full backpack overflows into the character's bank, and once "
    "the bank is full too, only Crowns items are kept."
)

NODE_SPEC = NodeSpec(
    description=(
        "Sells drops when the backpack or jewel pouch is full, except items a keep list keeps. "
        "Crowns items are sold like any other."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Nothing was full, or the drops were sold."),
        ReturnSpec("FAILURE", "The quicksell window didn't open, or the sale didn't finish."),
    ],
    required_bot_options=[
        SAFETY_DELAY_OPTION,
        BotOptionSpec(
            name=OPTION_ENABLED,
            group=GROUP_QUICKSELL,
            heads_group=True,
            type="bool",
            # Null: the author has no opinion, so the app setting answers. Quicksell
            # is off by default (#262): a player who never locks their items would
            # otherwise lose them on a first run.
            default=None,
            description=(
                "Sell drops so backpacks never fill. When off, a full backpack overflows into the "
                "character's bank, and once the bank is full too, only Crowns items are kept."
            ),
        ),
        BotOptionSpec(
            name=SELL_FLAG_OPTIONS["sell_pets"],
            group=GROUP_QUICKSELL,
            type="bool",
            default=None,
            depends_on=OPTION_ENABLED,
            description="Turn off to keep pets. The keep list can't protect a pet.",
        ),
        BotOptionSpec(
            name=SELL_FLAG_OPTIONS["sell_socket_wrenches"],
            group=GROUP_QUICKSELL,
            type="bool",
            default=None,
            depends_on=OPTION_ENABLED,
            description=('Turn off to keep any item with "Socket Wrench" in its name.'),
        ),
        BotOptionSpec(
            name=SELL_FLAG_OPTIONS["sell_skeleton_keys"],
            group=GROUP_QUICKSELL,
            type="bool",
            default=None,
            depends_on=OPTION_ENABLED,
            description=('Turn off to keep any item with "Skeleton Key" in its name.'),
        ),
        BotOptionSpec(
            name=OPTION_KEEP_LIST,
            group=GROUP_QUICKSELL,
            # Node-declared type: a str_list underneath, shown in Options as this bot's
            # own drop table with a tick box per item.
            type="drop_keep_list",
            default=[],
            description=(
                'Which items quicksell keeps. A keyword keeps every item containing it, so "giver" keeps '
                "every Giver jewel, and a checked item keeps only itself. Your list replaces the "
                "author's, and your Always keep list in Settings › Bot applies too."
            ),
        ),
    ],
    max_consecutive_retries=5,
    safety_warning=(
        "This bot sells drops when your backpack is full. Lock valuable items in the game, or add "
        "them to a keep list in this bot's Options or in Settings › Bot. To lock items, first "
        "turn on Backpack Item Lock in the game's Options › Gameplay Options 2. Turn off Sell "
        "Pets to keep pets. "
    )
    + SAFETY_WARNING_DISABLED,
)

from bartlebot.nodes.timing import POLL_INTERVAL, QUICKSELL_COMPLETE_TIMEOUT  # noqa: E402

_ROWS_PER_PAGE = 10

#: Marks a keep-list entry as one item rather than a keyword: ``"=Couch Potatoes"``.
#:
#: The two are added differently and mean different things. A keyword is typed, to keep
#: a family -- "giver" keeps every Giver jewel -- so it matches anywhere in a name. An
#: item is ticked in the picker, to keep that item, so it matches the whole name and
#: nothing else; matched as a fragment, ticking Couch Potatoes also kept Red and Golden
#: Couch Potatoes, and nothing could keep the one without the others.
#:
#: A prefix inside the one list rather than a second list, so the option stays the
#: ``str_list`` every level of the resolution, the template diff and the share files
#: already carry. An entry without it is a keyword, which is what every entry was before.
ITEM_PREFIX = "="

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

# Every category the wiki import produces, as one ordered vocabulary. Derived from
# the two sets above rather than written out again: those are what the extractor's
# icon table and ``tools/drop_categories.py`` already treat as the whole list, and a
# third hand-maintained copy would be the one that drifts.
#
# This is what the Possible Drops editor offers when a drop's category is set by
# hand. Hand-editing is the only route for a drop no wiki page lists -- a reagent a
# user added themselves -- and the list is closed on purpose: a typed category is a
# category no icon, no sellability classification and no picker filter knows, which
# reads as a bug rather than as a new vocabulary word. A genuinely new category is
# added here by classifying it above, which is the same step
# ``tools/drop_categories.py`` already demands.
DROP_CATEGORIES: tuple[str, ...] = tuple(sorted(QUICKSELLABLE_CATEGORIES | set(NEVER_QUICKSOLD_CATEGORIES)))


def resolve_options(get_option, user_keep_list=()) -> dict:
    """Collapse the five quicksell options into the plain dict everything else reads.

    This is the one place the option layer touches quicksell.  Below it nothing
    knows an option exists: the keep-list logic, the picker and ``run()`` all see
    ``{"enabled": bool, "keep_list": [...], "sell_pets": bool, ...}``, which is what
    keeps them pure and testable without a settings file.

    *get_option* is ``BotMachine.get_option`` at run time and a settings-backed
    lookup in the editor.  Its answer is checked rather than trusted: an override
    written under an older type, or a stub that answers everything with a number,
    must not turn a bool into "sell everything".  A value of the wrong shape falls
    back to the floor -- off for Quicksell, ``SELL_FLAG_DEFAULTS`` for the
    flags -- never to something more destructive.

    *user_keep_list* is the user's own list from Settings -> Bot, read by the caller
    with :func:`user_keep_list`.  It is kept apart as ``"user_keep_list"`` rather than
    merged into ``"keep_list"``: the picker edits ``"keep_list"`` and writes it back as
    this bot's value, and a merged list would copy the user's entries into every bot
    whose picker they touched.
    """
    keep_list = get_option(OPTION_KEEP_LIST, [])
    enabled = get_option(OPTION_ENABLED, False)
    values: dict = {
        "enabled": enabled if isinstance(enabled, bool) else False,
        "keep_list": [str(k) for k in keep_list] if isinstance(keep_list, list) else [],
        "user_keep_list": [str(k) for k in user_keep_list],
    }
    for key, name in SELL_FLAG_OPTIONS.items():
        value = get_option(name, SELL_FLAG_DEFAULTS[key])
        values[key] = value if isinstance(value, bool) else SELL_FLAG_DEFAULTS[key]
    return values


def user_keep_list(settings=None) -> list[str]:
    """The user's own keep list from Settings -> Bot, or ``[]`` when it cannot be read.

    Never raises: a settings file that cannot be read keeps nothing extra, which is
    the state every install starts in.
    """
    try:
        if settings is None:
            from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415 - avoids an import cycle

            settings = SettingsManager.instance()
        value = settings.get("general", USER_KEEP_LIST_KEY, default=[])
    except Exception:
        logger.exception("Failed to read the user's quicksell keep list")
        return []
    return [str(k) for k in value] if isinstance(value, list) else []


def resolve_options_for(config, settings=None) -> dict:
    """The same resolved dict ``run()`` builds, for a *config* the editor is holding.

    The editor has no machine, so it resolves through ``option_values`` instead --
    the same four levels, the same order.  Kept here rather than in each caller so
    the picker, the safety dialog and the run cannot drift apart about what this
    bot will sell.
    """
    from bartlebot.options import option_values  # noqa: PLC0415 - avoids a settings import at node load

    return resolve_options(
        lambda name, default=None: option_values.option_value(config, name, default, settings=settings),
        user_keep_list(settings),
    )


def declared_sales(config, settings=None) -> list[str]:
    """The sell flags this bot's own declaration turns on that the user's settings keep.

    What the safety check still warns about once the user has read its explanation of
    quicksell (#145). The user's settings are the baseline: a sale they set up
    themselves, in Settings -> Bot or as this bot's value in Options, needs no warning.
    A bot file is the one level that can sell more than the user asked for without the
    user having touched anything -- Tree of Life declares Sell Skeleton Keys on, while
    the app setting keeps them.

    Returns option names in ``SELL_FLAG_OPTIONS`` order; empty when nothing is
    declared, or when the declaration agrees with the user or is safer.
    """
    from bartlebot.options import bot_options, option_values  # noqa: PLC0415 - avoids a settings import at node load

    sales = []
    for key, name in SELL_FLAG_OPTIONS.items():
        resolution = option_values.option_resolution(config, name, settings=settings)
        if resolution is None or resolution.source != bot_options.SOURCE_BOT_FILE or resolution.value is not True:
            continue
        users = option_values.app_value(name, settings=settings)
        if not isinstance(users, bool):
            users = SELL_FLAG_DEFAULTS[key]
        if not users:
            sales.append(name)
    return sales


def describe_config(params: dict) -> str:
    """One line naming everything this quicksell will do, for the log."""
    return f"enabled={params.get('enabled', True)} keep_list={effective_keep_list(params)!r} " + " ".join(
        f"{key}={sell_flag(params, key)}" for key in SELL_FLAG_OPTIONS
    )


def is_item_entry(entry: str) -> bool:
    """Whether keep-list *entry* names one item (ticked) rather than a keyword (typed)."""
    return entry.strip().startswith(ITEM_PREFIX)


def item_entry(name: str) -> str:
    """The keep-list entry that keeps the item called *name* and nothing else."""
    return ITEM_PREFIX + name


def entry_text(entry: str) -> str:
    """*entry* as the user reads it: the item's name, or the keyword."""
    entry = entry.strip()
    return entry[len(ITEM_PREFIX) :].strip() if entry.startswith(ITEM_PREFIX) else entry


def _normalize_name(name: str) -> str:
    """An item name as both kinds of entry compare it: no markup, single spaces, lowercase.

    The markup matters to an item entry only. A keyword matched inside ``<center>Amber``
    just as well, but a whole-name comparison would fail on the wrapper and sell the item.
    """
    return " ".join(node_helpers.strip_html(name).split()).lower()


def _typed_keywords(params: dict) -> list[str]:
    """The keep_list param, normalized. Blank entries are dropped: an empty keyword
    is a substring of every name and would silently keep the entire backpack."""
    return _keywords(params.get("keep_list"))


def _user_keywords(params: dict) -> list[str]:
    """The user's own keep list from Settings -> Bot, normalized, blanks dropped the same way."""
    return _keywords(params.get("user_keep_list"))


def _keywords(entries) -> list[str]:
    """Normalize keep-list entries for :func:`matching_keyword`: keywords lowercased, items
    by :func:`_normalize_name` with their prefix kept, blanks of either kind dropped."""
    normalized = []
    for raw in entries or []:
        entry = str(raw).strip()
        if is_item_entry(entry):
            name = _normalize_name(entry_text(entry))
            if name:
                normalized.append(item_entry(name))
        elif keyword := entry.lower():
            normalized.append(keyword)
    return normalized


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
    """Return the normalized entries this config keeps items by, each once.

    The bot's own list, then the user's from Settings -> Bot, then the ones the sell
    flags imply.
    """
    return list(dict.fromkeys([*_typed_keywords(params), *_user_keywords(params), *_implied_keywords(params)]))


def matching_keyword(item_name: str, keywords: list[str]) -> str | None:
    """Return the first entry in *keywords* that keeps *item_name*, or None.

    *keywords* are normalized entries (:func:`effective_keep_list`). An item entry keeps
    the item whose whole name it is; a keyword keeps any name containing it, ignoring
    case. This is the test the quicksell page loop uses, so the answer is exactly what
    the node will do to that item.
    """
    name = _normalize_name(item_name)
    for keyword in keywords:
        if keyword.startswith(ITEM_PREFIX):
            if keyword[len(ITEM_PREFIX) :] == name:
                return keyword
        elif keyword and keyword in name:
            return keyword
    return None


@dataclass(frozen=True)
class DropKeepState:
    """How one item from a bot's drop table fares under a quicksell config.

    Attributes:
        name:    The item name as it appears in the drop table.
        keyword: The normalized keep-list entry that spares it, or None when it will
                 be sold.
        exact:   True when *keyword* is this bot's item entry for the drop -- it was
                 ticked -- so unticking it is enough to change its fate. False for a
                 keyword, even one equal to the item's name: a keyword is typed and
                 removed as a chip. False for an implied keyword too ("Socket
                 Wrench"): removing that one means flipping the sell_* bool.
        users:   True when *keyword* comes from the user's own keep list in
                 Settings -> Bot. Never exact either: this bot's list cannot
                 remove it, so the row is not this bot's to untick.
    """

    name: str
    keyword: str | None
    exact: bool
    users: bool = False

    @property
    def kept(self) -> bool:
        return self.keyword is not None


def drop_keep_states(drops: list[str], params: dict) -> list[DropKeepState]:
    """Report what a quicksell config does to each item in a bot's drop table.

    A drop's own item entry answers first, even when a keyword keeps it too: the row
    stays tickable, so the entry can still be unticked away. Answering with the
    keyword would lock the row and leave the entry stuck in the list, unseen.
    """
    typed = _typed_keywords(params)
    items = [k for k in typed if is_item_entry(k)]
    keywords = [k for k in typed if not is_item_entry(k)]
    users = _user_keywords(params)
    implied = _implied_keywords(params)
    states = []
    for name in drops:
        if (keyword := matching_keyword(name, items)) is not None:
            states.append(DropKeepState(name, keyword, exact=True))
        elif (keyword := matching_keyword(name, keywords)) is not None:
            states.append(DropKeepState(name, keyword, exact=False))
        elif (keyword := matching_keyword(name, users)) is not None:
            states.append(DropKeepState(name, keyword, exact=False, users=True))
        else:
            states.append(DropKeepState(name, matching_keyword(name, implied), exact=False))
    return states


def set_drops_in_keep_list(keep_list: list[str], names: list[str], keep: bool) -> list[str]:
    """Return *keep_list* with every name in *names* added or removed, in one pass.

    The bulk form of toggle_drop_in_keep_list, for the picker's check/uncheck-all
    buttons: doing it one name at a time would rescan the list per name, and a
    drop table can hold hundreds of entries.
    """
    targets = {_normalize_name(name) for name in names}
    remaining = [k for k in keep_list if not _is_item_entry_for(k, targets)]
    return [*remaining, *(item_entry(name) for name in names)] if keep else remaining


def toggle_drop_in_keep_list(keep_list: list[str], name: str, keep: bool) -> list[str]:
    """Return *keep_list* with the item entry for *name* added or removed.

    Only item entries for *name* are touched, so a keyword the user typed survives an
    untick even when it equals the name -- it is removed as a chip, not from here.
    """
    remaining = [k for k in keep_list if not _is_item_entry_for(k, {_normalize_name(name)})]
    return [*remaining, item_entry(name)] if keep else remaining


def _is_item_entry_for(entry: str, names: set[str]) -> bool:
    return is_item_entry(entry) and _normalize_name(entry_text(entry)) in names


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.backpack_space(), client.send_key(Keycode.B), client.send_key(Keycode.Y),
          node_helpers.is_visible_by_path(), node_helpers.is_enabled_by_path(),
          node_helpers.click_by_path(), node_helpers.get_text_by_path()

    Flow (per client, run concurrently via asyncio.gather):
      0. Resolve the five bot options via resolve_options(). Quicksell off is
         SUCCESS without touching a client -- a node that does nothing has not failed.
      1. Build the effective keep_list via effective_keep_list(): the Quicksell Keep
         List option, the user's own list from Settings -> Bot, and the keywords
         implied by the sell_* bools that are off. The Options picker resolves the
         same way, so what it shows is what this will do.
      2. Open the inventory page (B key); read backpack_space() to check if full.
      3. Switch to the jewel page (Y key); read the jewel capacity text to check if full.
      4. If neither is full, close the backpack and return SUCCESS.
      5. Click the quicksell open button on the jewel page.
      6. Wait for the shop UI to appear (QUICKSELL_ALL_BUTTON).
      7. Click the "All" tab to see all item types.
      8. Click "Select All" to check every item.
      9. Answer "include Crowns Items?" if it comes up: always yes, to include them.
      10. If sell_pets is False, switch to the Pet tab, click All to deselect pets,
          then return to the All tab.
      11. Page through the quicksell list and uncheck any item the keep list keeps
          (``matching_keyword``: a keyword anywhere in the name, an item by its whole name).
      12. If the Sell button is grayed out (all items unchecked), close the shop and
          backpack and return SUCCESS.
      13. Click the Sell button; wait for the sell confirmation dialog; confirm it.
      14. Answer each dialog until the backpack is back (QUICKSELL_COMPLETE_TIMEOUT): the
          sale's own questions (Crowns items, pets, the gold limit) are confirmed, since No
          cancels the sale, and anything else is cancelled. Each is clicked until it goes.
      15. Close the backpack, clicking again until it is gone.

    FAILURE: quicksell open button did not appear; shop UI did not appear;
    sell confirmation dialog did not appear; the backpack stayed open after the close
    button had been clicked for ``WINDOW_CLOSE_TIMEOUT`` (a sale already made stands).
    """
    # The one place the option layer is read. Everything below sees plain values;
    # which level answered each one is already in the log, written once per run by
    # get_option itself, so this line says only what the node will do with them.
    params = resolve_options(machine.get_option, user_keep_list())
    keep_list = effective_keep_list(params)
    sell_pets: bool = params["sell_pets"]
    machine.log(f"quicksell: {describe_config(params)}")

    if not params["enabled"]:
        # Not a failure: the user turned selling off, and the run carries on without
        # it. What that costs them is in the option's own description and in the
        # safety dialog -- the backpack fills, then the bank, and after that only
        # Crowns items are kept.
        machine.log("quicksell: Quicksell is off — nothing will be sold.")
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
        return await _close_backpack(machine, client)

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

    # Answer "include Crowns Items?" if it comes up after selecting all items. Always yes, so
    # every item is checked before the keep_list page loop.
    machine.log("quicksell: waiting for the Crowns items question (optional)", logging.DEBUG)
    outcome = await _answer_dialogs(
        machine, client, done=_never, timeout=safety_delay, stop_after=dialog_captions.QUICKSELL_INCLUDE_CROWNS
    )
    if outcome is _Dialogs.STUCK:
        return "FAILURE"
    if outcome is _Dialogs.TIMED_OUT:
        machine.log("quicksell: no Crowns items question, skipping", logging.DEBUG)

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
        # Until the shop is gone the backpack's close button is behind it, so its clicks
        # would land on the shop.
        if not await node_helpers.click_until_gone(
            client,
            QUICKSELL_CLOSE_BUTTON_PATH,
            is_open=lambda: node_helpers.is_visible_by_path(client, QUICKSELL_SHOP_PATH),
        ):
            machine.log("FAILURE: the quicksell window did not close", logging.WARNING)
            return "FAILURE"
        return await _close_backpack(machine, client)

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

    # Answer the questions the sale raises (Crowns items, pets, the gold limit) until the
    # backpack is back. QUICKSELL_COMPLETE_TIMEOUT rather than safety_delay: selling a full
    # backpack is a server-side transaction that can lag well past it on a successful sale.
    machine.log("quicksell: waiting for the sale to finish", logging.DEBUG)
    outcome = await _answer_dialogs(
        machine, client, done=lambda: _backpack_is_back(client), timeout=QUICKSELL_COMPLETE_TIMEOUT
    )
    if outcome is _Dialogs.STUCK:
        return "FAILURE"
    if outcome is _Dialogs.TIMED_OUT:
        machine.log(
            f"FAILURE: the backpack did not come back within {QUICKSELL_COMPLETE_TIMEOUT:.0f}s of the sale",
            logging.WARNING,
        )
        return "FAILURE"
    if await _close_backpack(machine, client) == "FAILURE":
        return "FAILURE"
    machine.log("quicksell: sell complete")
    return "SUCCESS"


class _Dialogs(enum.Enum):
    """How _answer_dialogs ended."""

    DONE = enum.auto()  # done() held with no dialog up, or stop_after was answered
    TIMED_OUT = enum.auto()  # the timeout passed first
    STUCK = enum.auto()  # a dialog would not go, or more came than any sale raises; logged


# More than a sale raises (Crowns items, pets and the gold limit, plus the odd stray box),
# so reaching it means something keeps putting dialogs up.
_MAX_DIALOGS = 10


async def _never() -> bool:
    return False


async def _backpack_is_back(client) -> bool:
    """The sale has finished: the shop is gone and the backpack shows again.

    Read from the backpack's close button, which is hidden while the shop is up and comes
    back with the backpack. Whether the backpack itself is still open afterwards is the
    spellbook's own read, which _close_backpack makes.
    """
    return await node_helpers.is_visible_by_path(client, BACKPACK_CLOSE_BUTTON_PATH)


async def _answer_dialogs(machine, client, *, done, timeout: float, stop_after: str | None = None) -> _Dialogs:
    """Answer each dialog as it comes up, until done() holds with none showing.

    A fixed count of dialogs is what this replaced: a sale raised exactly as many as it
    allowed for, so one more, or one dropped click, left a dialog up. ``done`` is read only
    while no dialog shows, since a dialog covers whatever it would read. ``stop_after`` ends
    the wait as soon as that dialog has been answered.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    answered = 0
    while True:
        if await node_helpers.modal_showing(client):
            if answered >= _MAX_DIALOGS:
                machine.log(f"FAILURE: more than {_MAX_DIALOGS} dialogs came up during the quicksell", logging.WARNING)
                return _Dialogs.STUCK
            answer = await _answer_dialog(machine, client)
            if not answer.closed:
                return _Dialogs.STUCK
            answered += 1
            if stop_after is not None and answer.key == stop_after:
                return _Dialogs.DONE
            continue
        if await done():
            return _Dialogs.DONE
        if loop.time() >= deadline:
            return _Dialogs.TIMED_OUT
        await asyncio.sleep(POLL_INTERVAL)


class _Answer(NamedTuple):
    key: str | None  # the dialog's lang key, None if unrecognized
    closed: bool


async def _answer_dialog(machine, client) -> _Answer:
    """Answer the dialog showing, clicking until it has gone.

    A sale's own question is confirmed: No on any of them cancels the whole sale. Anything
    else, such as an invitation that arrived mid-sale, is cancelled the way clear_modals
    backs out of a dialog, and the sale goes on. A disconnect is left for the health
    monitor. Logged by key, never by caption, which can carry a wizard's name.
    """
    key = await node_helpers.identify_modal(client)
    if key in dialog_captions.DISCONNECTS:
        machine.log(f"FAILURE: the client was disconnected ({dialog_captions.describe(key)})", logging.WARNING)
        return _Answer(key, closed=False)
    if key in dialog_captions.QUICKSELL_QUESTIONS:
        machine.log(f"quicksell: confirming {dialog_captions.describe(key)}", logging.DEBUG)
        button = MESSAGE_BOX_CENTER
    else:
        machine.log("quicksell: cancelling a dialog the sale did not raise", logging.INFO)
        button = await _cancel_button(client)
    if button is not None and await node_helpers.click_until_dialog_gone(client, button):
        return _Answer(key, closed=True)
    what = dialog_captions.describe(key) if key else "one this build does not recognize"
    machine.log(f"FAILURE: a dialog would not close during the quicksell ({what})", logging.WARNING)
    return _Answer(key, closed=False)


async def _cancel_button(client) -> list[str] | None:
    """The button that backs out of the dialog showing: right, else center (a lone OK), else left."""
    for button in (MESSAGE_BOX_RIGHT, MESSAGE_BOX_CENTER, MESSAGE_BOX_LEFT):
        if await node_helpers.is_visible_by_path(client, button):
            return button
    return None


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


async def _close_backpack(machine, client) -> str:
    """Close the backpack (``node_helpers.close_spellbook``). "FAILURE" if it never goes.

    Straight after a sale the game is still busy and drops a click, and the node used to
    wait out the Safety Delay for a backpack it had left open, then report success.
    """
    if not await node_helpers.close_spellbook(client):
        machine.log("FAILURE: the backpack did not close", logging.WARNING)
        return "FAILURE"
    return "SUCCESS"
