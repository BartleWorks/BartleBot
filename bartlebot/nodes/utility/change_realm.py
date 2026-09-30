"""Node: change every player's realm, to the favourite or to the next one.

Merged from ``change_to_favorite_realm`` and ``change_to_next_realm`` (both since
removed, once every bot had been migrated off them): two node types for one act, and a
node type is the one thing neither a subgraph input nor an override can bridge. One type
is what lets every template share a single Player Sync. The mode is the
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

**How the players are arranged is the Player Realms option's, not a param's (#131).** A
param says where the players go; the option says whether they go there together:

======================  ==================================  =====================================
``use_favorite_realm``  Together                            Separate
======================  ==================================  =====================================
on                      every player to the favourite       player 1 to the favourite, the rest
                                                            to the rows after it
off (next realm)        the leader to the next realm, and   player N to the realm N rows after
                        the others to the leader's          its own, within pages 3-5
======================  ==================================  =====================================

A split param here would let a bot say Together on the bot and split on the node, which
can never be right, so there is none. Afterwards a Together bot is checked by sight and
anyone not with the leader ports to it; a Separate bot is checked for two players who
landed on one realm, which is logged rather than failed.
"""

import asyncio
import logging

from bartlebot import player_realms, realms, sightings
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import POLL_INTERVAL, SIGHTING_SETTLE_TIMEOUT, TRIGGER_TIMEOUT
from bartlebot.nodes.utility import realm_list
from bartlebot.nodes.utility.realm_list import REALMS_PER_PAGE

_TAG = "change_realm"

logger = logging.getLogger(__name__)

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
            "On: switch to the Favorite Realm — set in Settings → Bot, or overridden for this bot "
            "in Setup.\n\n"
            "Off: switch to the next realm after the current one, cycling through the quieter "
            "pages of the realm list. Useful where a fresh realm means fresh spawns, as with "
            "reagent farming.\n\n"
            "Whether the players share the new realm is the bot's Player Realms option: Together, "
            "they all end on one; Separate, each gets a realm of its own."
        ),
        default=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Switches the players' realm: to the Favorite Realm, or to the next realm in the list. With "
        "Player Realms set to Together they all end on one realm and are checked to be together; "
        "with Separate each gets a realm of its own. Opens the spellbook to the Realms tab, finds the "
        "target row, and hops to it, waiting out the realm-hop cooldown if needed. A player already "
        "on its target realm is left alone."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec(
            "SUCCESS",
            "Every player is on its target realm, whether it hopped or was already there -- and, "
            "Together, seen with the others.",
        ),
        ReturnSpec(
            "FAILURE",
            "Favorite mode with no favorite realm set; the spellbook or realms panel did not open; the "
            "realm was not found on any page; the realm-hop timer did not expire within the timeout; the "
            "game refused the switch (a dungeon, gauntlet or house); a loading screen was not detected; "
            "or, Together, a player could not be brought to the others afterwards.",
        ),
    ],
    required_bot_options=[
        player_realms.SPEC,
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
    separate = player_realms.machine_is_separate(machine) and len(clients) > 1

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
        if separate:
            machine.log(f"{_TAG}: spreading the players from {realms.label(favorite)!r}, one realm each")
            coros = [
                to_realm(machine, c, favorite, safety_delay)
                if i == 0
                else _to_realm_after(machine, c, favorite, i, safety_delay)
                for i, c in enumerate(clients)
            ]
        else:
            machine.log(f"{_TAG}: switching every player to {realms.label(favorite)!r}")
            coros = [to_realm(machine, c, favorite, safety_delay) for c in clients]
    elif separate:
        machine.log(
            f"{_TAG}: spreading the players across pages {NEXT_REALM_FIRST_PAGE}–{NEXT_REALM_LAST_PAGE}, one realm each"
        )
        coros = [
            _to_next_realm(machine, c, NEXT_REALM_FIRST_PAGE, NEXT_REALM_LAST_PAGE, safety_delay, offset=i + 1)
            for i, c in enumerate(clients)
        ]
    else:
        machine.log(
            f"{_TAG}: moving the players to the next realm on pages {NEXT_REALM_FIRST_PAGE}–{NEXT_REALM_LAST_PAGE}"
        )
        if await _next_realm_together(machine, clients, safety_delay) == "FAILURE":
            return "FAILURE"
        coros = []

    results = await asyncio.gather(*coros, return_exceptions=True)
    if any(isinstance(r, Exception) or r == "FAILURE" for r in results):
        return "FAILURE"
    _forget(machine, clients)
    if separate:
        await _warn_if_any_share_a_realm(machine, clients)
        return "SUCCESS"
    if await node_helpers.ensure_together(machine, clients, where="after the realm change") is (
        node_helpers.TogetherOutcome.FAILED
    ):
        return "FAILURE"
    return "SUCCESS"


def _forget(machine, clients: list) -> None:
    """Drop every player from the record of who is together: a realm change is a load.

    The loading watcher says so in a run; this says it at once, before the check that follows.
    """
    forget = getattr(machine, "forget_sightings", None)
    if forget is not None:
        for client in clients:
            forget(client)


async def _next_realm_together(machine, clients: list, safety_delay: float) -> str:
    """Next-realm mode, Together: the leader moves to the next realm, and the others to its.

    Each player moving to the realm after its own keeps a group together only by coincidence --
    while it started together and every hop succeeds. Following the leader keeps it together.
    """
    check = await node_helpers.read_sightings(clients, machine)
    leader = clients[sightings.choose_leader(check)]
    try:
        hopped = await _to_next_realm(machine, leader, NEXT_REALM_FIRST_PAGE, NEXT_REALM_LAST_PAGE, safety_delay)
    except Exception:
        # The same as a client raising in the gather the other modes use: the node fails, not the machine.
        logger.exception("The leader's realm change raised")
        return "FAILURE"
    if hopped == "FAILURE":
        return "FAILURE"
    others = [c for c in clients if c is not leader]
    if not others:
        return "SUCCESS"
    realm = await realm_list.read_current_realm(machine, leader, safety_delay, _TAG)
    if realm is None:
        machine.log("FAILURE: the leader's new realm could not be read, so nobody could follow it", logging.WARNING)
        return "FAILURE"
    results = await asyncio.gather(*(to_realm(machine, c, realm, safety_delay) for c in others), return_exceptions=True)
    return "FAILURE" if any(isinstance(r, Exception) or r == "FAILURE" for r in results) else "SUCCESS"


async def _warn_if_any_share_a_realm(machine, clients: list) -> None:
    """Separate: log any players seen together after spreading. A collision costs spawns, not the run."""
    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients), SIGHTING_SETTLE_TIMEOUT, POLL_INTERVAL)
    check = await node_helpers.read_sightings(clients, machine)
    shared = [[i + 1 for i in group] for group in check.groups() if len(group) > 1]
    if shared:
        machine.log(f"Player(s) {shared} landed on one realm despite the spread", logging.WARNING)


async def to_realm(machine, client, favorite: str, safety_delay: float) -> str:
    """Find the realm keyed *favorite* in the realm list and hop to it, for one client.

    Public because sync_in_zone (#133) hops its leader, and a player who cannot port to the
    leader, the same way. *favorite* is any realm key, not only the Favorite Realm option's.

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


async def _to_next_realm(
    machine, client, start_page: int, end_page: int, safety_delay: float, *, offset: int = 1
) -> str:
    """Hop to the realm *offset* rows after the current one within pages *start_page*..*end_page*.

    *offset* is 1 for the next realm; a Separate bot spreading its players passes each its own.

    Flow:
      1. Open the spellbook to the Realms tab and wait for the list to render.
      2. Anchor to page 1 by clicking the left arrow until it is disabled, then page
         right to start_page, verifying each flip.
      3. Scan pages start_page..end_page for the current realm, identified by its
         grayed-out (disabled) button.
      4. The target is the entry *offset* rows after the current one, counted across the
         range's pages and wrapping back to start_page. A blank target row (a short page)
         wraps to start_page row 0. If the current realm was not found, count from just
         before start_page row 0.
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
        position = (found_page - start_page) * REALMS_PER_PAGE + found_row
    else:
        machine.log(
            f"{_TAG}: current realm not found in pages {start_page}–{end_page}; "
            f"counting from the first realm on page {start_page}",
            logging.DEBUG,
        )
        current_realm_name = ""
        position = -1
    target = (position + offset) % ((end_page - start_page + 1) * REALMS_PER_PAGE)
    target_page, target_row = start_page + target // REALMS_PER_PAGE, target % REALMS_PER_PAGE

    machine.log(f"{_TAG}: target page {target_page}, row {target_row}", logging.DEBUG)

    steps = target_page - current_scan_page
    if steps != 0 and not await realm_list.navigate_pages(machine, client, steps, TRIGGER_TIMEOUT, _TAG):
        machine.log(f"FAILURE: could not navigate to target page {target_page}", logging.WARNING)
        await realm_list.close_spellbook(client)
        return "FAILURE"
    if not await realm_list.row_name(client, target_row):
        # A short page: wrap to the start of the range.
        if target_page != start_page and not await realm_list.navigate_pages(
            machine, client, start_page - target_page, TRIGGER_TIMEOUT, _TAG
        ):
            machine.log(f"FAILURE: could not navigate back to page {start_page}", logging.WARNING)
            await realm_list.close_spellbook(client)
            return "FAILURE"
        target_row = 0

    return await realm_list.hop_to_row(machine, client, target_row, _TAG, current_name=current_realm_name)


async def _to_realm_after(machine, client, favorite: str, offset: int, safety_delay: float) -> str:
    """Hop to the realm *offset* rows after *favorite* in the list, for one client.

    Separate and favourite mode: player 1 takes the favourite, and each other player the row
    its number past it. Past the end of the list it wraps to page 1.
    """
    if not await realm_list.open_realm_list(machine, client, safety_delay, _TAG):
        return "FAILURE"
    if not await realm_list.navigate_to_page_1(machine, client, TRIGGER_TIMEOUT, _TAG):
        machine.log("FAILURE: could not anchor to page 1", logging.WARNING)
        await realm_list.close_spellbook(client)
        return "FAILURE"

    found = None
    for page in range(1, _MAX_PAGES + 1):
        for row in range(REALMS_PER_PAGE):
            name = await realm_list.row_name(client, row)
            if not name:
                break
            if realms.matches(favorite, name):
                found = (page, row)
                break
        if found or not await realm_list.has_next_page(client):
            break
        if not await realm_list.navigate_pages(machine, client, 1, TRIGGER_TIMEOUT, _TAG):
            machine.log(f"FAILURE: could not advance past page {page}", logging.WARNING)
            await realm_list.close_spellbook(client)
            return "FAILURE"
    if found is None:
        machine.log(
            f"FAILURE: realm {realms.label(favorite)!r} was not found on any page of the realm list",
            logging.WARNING,
        )
        await realm_list.close_spellbook(client)
        return "FAILURE"

    page, row = found
    target = row + offset
    while target >= REALMS_PER_PAGE or not await realm_list.row_name(client, target):
        target -= REALMS_PER_PAGE if target >= REALMS_PER_PAGE else target
        if await realm_list.has_next_page(client):
            if not await realm_list.navigate_pages(machine, client, 1, TRIGGER_TIMEOUT, _TAG):
                await realm_list.close_spellbook(client)
                return "FAILURE"
        elif not await realm_list.navigate_to_page_1(machine, client, TRIGGER_TIMEOUT, _TAG):
            await realm_list.close_spellbook(client)
            return "FAILURE"
    if await realm_list.is_current_realm(client, target):
        machine.log(f"SUCCESS: already on {await realm_list.row_name(client, target)!r}")
        await realm_list.close_spellbook(client)
        return "SUCCESS"
    return await realm_list.hop_to_row(machine, client, target, _TAG)
