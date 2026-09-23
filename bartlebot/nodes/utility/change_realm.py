"""Node: change every player's realm, to the favourite or to the next one.

Merged from ``change_to_favorite_realm`` and ``change_to_next_realm`` (both removed once
``scripts/migrate_change_realm.py`` had moved every bot off them): two node types for
one act, and a node type is the one thing neither a subgraph input nor an override can
bridge. One type is what lets every template share a single Player Sync. The mode is the
``use_favorite_realm`` param; the UI plumbing both modes share is
:mod:`bartlebot.nodes.utility.realm_list`.

Favourite mode reads the ``Favorite Realm`` bot option -- the user's, never the bot's: it
is declared null here, answered by the Favorite Realm setting on the Bot settings tab,
and a per-bot override in Setup sits above that. A bot file naming a realm would send
everyone who runs it to the same place, which is the crowding a favourite realm exists to
escape, so ``Favorite Realm`` is in ``bot_options.USER_OWNED_OPTIONS`` and the Options
surface has no row for it. The stored value is the realm's *key* (its English name); the
in-game list shows the client's own locale, so a row is matched through
:func:`bartlebot.realms.matches`, against every locale's text for that key.

Next-realm mode cycles through pages 3-5 of the list. The range used to be two params,
``start_realm_page`` and ``end_realm_page``; they held one value across every bot that
ever used them and were not something an author should be changing, so they are
constants now. Page 1-2 hold the busiest realms, and page 6 attracts players who
spam-click to reach the emptiest ones.
"""

import asyncio
import logging

from bartlebot import realms
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import TRIGGER_TIMEOUT
from bartlebot.nodes.utility import realm_list
from bartlebot.nodes.utility.realm_list import REALMS_PER_PAGE

_TAG = "change_realm"

OPTION_FAVORITE_REALM = "Favorite Realm"

PARAM_USE_FAVORITE_REALM = "use_favorite_realm"

#: The page range next-realm mode cycles through. See the module docstring.
NEXT_REALM_FIRST_PAGE = 3
NEXT_REALM_LAST_PAGE = 5

#: More pages than the realm list has ever had. A guard, not a limit: paging stops at
#: the disabled right arrow, and this only keeps a list that never disables it from
#: being walked forever.
_MAX_PAGES = 10

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key=PARAM_USE_FAVORITE_REALM,
        type="bool",
        label="Use Favorite Realm",
        help=(
            "On: switch every player to the Favorite Realm — set in Settings → Bot, or overridden "
            "for this bot in Setup.\n\n"
            "Off: switch to the next realm after the current one, cycling through the quieter "
            "pages of the realm list. Useful where a fresh realm means fresh spawns, as with "
            "reagent farming."
        ),
        default=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Switches every player's realm: to the Favorite Realm, or to the next realm in the list. "
        "Opens the spellbook to the Realms tab, finds the target row, and hops to it, waiting out "
        "the realm-hop cooldown if needed. In favorite mode a player already on that realm is left "
        "alone."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Every player is on the target realm, whether it hopped or was already there."),
        ReturnSpec(
            "FAILURE",
            "Favorite mode with no favorite realm set; the spellbook or realms panel did not open; the "
            "realm was not found on any page; the realm-hop timer did not expire within the timeout; the "
            "game refused the switch (a dungeon, gauntlet or house); or a loading screen was not detected.",
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
        BotOptionSpec(
            name=OPTION_FAVORITE_REALM,
            type="realm",
            # Null, always: the author has no opinion about which realm you farm in, and
            # cannot have one. The level below is your app setting.
            default=None,
            description=(
                "The realm every player is switched to. This is your value, not the bot's — it "
                "is stored on this machine (Settings → Bot) and never written to a bot file."
            ),
            required_when=PARAM_USE_FAVORITE_REALM,
        ),
    ],
    max_consecutive_retries=5,
    usage_notes=(
        "Realms can only be switched from an open-world zone: a dungeon, gauntlet or house refuses with "
        '"You are not allowed to switch Realms from this location", and the node fails. Place it before '
        "entering the dungeon, or after leaving it. With a favorite realm set, every launched player also "
        "has the game's Remember Realm option turned on, so a relogged player comes back to the realm it "
        "left."
    ),
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: realm_list.open_realm_list(), realm_list.wait_for_current_realm_name(), realm_list.navigate_to_page_1(),
          realm_list.navigate_pages(), realm_list.row_name(), realm_list.is_current_realm(),
          realm_list.has_next_page(), realm_list.hop_to_row(), realms.matches()

    Flow:
      1. Read use_favorite_realm (default on).
      2. Favorite mode: resolve the Favorite Realm option, and FAILURE at once if it is
         empty or names a realm the snapshot does not know, so the user is told what to set.
      3. Run the chosen mode's per-client flow for every client concurrently; FAILURE if
         any client fails.
    """
    safety_delay: float = machine.get_option("Safety Delay", 3.0)

    if params.get(PARAM_USE_FAVORITE_REALM, True):
        favorite = str(machine.get_option(OPTION_FAVORITE_REALM, "") or "").strip()
        if not favorite:
            machine.log("FAILURE: no favorite realm is set — choose one in Settings → Bot, or in this bot's Setup")
            return "FAILURE"
        if not realms.is_known(favorite):
            machine.log(
                f"FAILURE: favorite realm {favorite!r} is not a realm this build knows — pick one from the list "
                "in Settings → Bot (or regenerate the realm table with tools/dump_realm_names.py)"
            )
            return "FAILURE"
        machine.log(f"{_TAG}: switching every player to {realms.label(favorite)!r}")
        coros = [_to_favorite_realm(machine, c, favorite, safety_delay) for c in clients]
    else:
        machine.log(f"{_TAG}: cycling realms on pages {NEXT_REALM_FIRST_PAGE}–{NEXT_REALM_LAST_PAGE}")
        coros = [_to_next_realm(machine, c, NEXT_REALM_FIRST_PAGE, NEXT_REALM_LAST_PAGE, safety_delay) for c in clients]

    results = await asyncio.gather(*coros, return_exceptions=True)
    if any(isinstance(r, Exception) or r == "FAILURE" for r in results):
        return "FAILURE"
    return "SUCCESS"


async def _to_favorite_realm(machine, client, favorite: str, safety_delay: float) -> str:
    """Find *favorite* in the realm list and hop to it, for one client.

    Flow:
      1. Open the spellbook to the Realms tab and wait for the list to render.
      2. Read the current realm directly from txtYourRealmName. If it matches the
         favourite already, close the spellbook and report SUCCESS without paging.
      3. Otherwise anchor to page 1.
      4. On each page, read every row's name and match it against the favourite in
         every locale. A blank row ends the page.
      5. If the matching row's button is disabled the player is already there: close the
         spellbook and report SUCCESS. Otherwise select it, wait out the cooldown, press
         Go To Realm, and wait for the loading screen to start and clear.
      6. No match and the right arrow enabled: page right and repeat. No match and the
         arrow disabled: FAILURE, the realm is not in the list.
    """
    if not await realm_list.open_realm_list(machine, client, safety_delay, _TAG):
        return "FAILURE"

    current_name = await realm_list.wait_for_current_realm_name(client, safety_delay)
    if current_name and realms.matches(favorite, current_name):
        machine.log(f"SUCCESS: already on {current_name!r}")
        await realm_list.close_spellbook(client)
        return "SUCCESS"

    machine.log(f"{_TAG}: anchoring to page 1", logging.DEBUG)
    if not await realm_list.navigate_to_page_1(machine, client, TRIGGER_TIMEOUT, _TAG):
        machine.log("FAILURE: could not anchor to page 1", logging.WARNING)
        await realm_list.close_spellbook(client)
        return "FAILURE"

    for page in range(1, _MAX_PAGES + 1):
        machine.log(f"{_TAG}: scanning page {page} for {realms.label(favorite)!r}", logging.DEBUG)
        for row in range(REALMS_PER_PAGE):
            name = await realm_list.row_name(client, row)
            if not name:
                break  # a short last page
            if not realms.matches(favorite, name):
                continue
            if await realm_list.is_current_realm(client, row):
                machine.log(f"SUCCESS: already on {name!r}")
                await realm_list.close_spellbook(client)
                return "SUCCESS"
            return await realm_list.hop_to_row(machine, client, row, _TAG)

        if not await realm_list.has_next_page(client):
            break
        if not await realm_list.navigate_pages(machine, client, 1, TRIGGER_TIMEOUT, _TAG):
            machine.log(f"FAILURE: could not advance past page {page}", logging.WARNING)
            await realm_list.close_spellbook(client)
            return "FAILURE"

    machine.log(
        f"FAILURE: realm {realms.label(favorite)!r} was not found on any page of the realm list",
        logging.WARNING,
    )
    await realm_list.close_spellbook(client)
    return "FAILURE"


async def _to_next_realm(machine, client, start_page: int, end_page: int, safety_delay: float) -> str:
    """Hop to the realm after the current one within pages *start_page*..*end_page*, for one client.

    Flow:
      1. Open the spellbook to the Realms tab and wait for the list to render.
      2. Anchor to page 1 by clicking the left arrow until it is disabled, then page
         right to start_page, verifying each flip.
      3. Scan pages start_page..end_page for the current realm, identified by its
         grayed-out (disabled) button.
      4. The target is the next entry after the current one, wrapping back to start_page
         row 0 if the current realm is last in the range. If the current realm was not
         found, default to start_page row 0.
      5. Navigate to the target page, select the row, wait out the realm-hop cooldown,
         press Go To Realm, and wait for the loading screen to start and clear.
    """
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
