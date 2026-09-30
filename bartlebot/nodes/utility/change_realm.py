"""Node: change every player's realm, to the favorite or to the next one.

Merged from ``change_to_favorite_realm`` and ``change_to_next_realm`` (both since
removed, once every bot had been migrated off them): two node types for one act, and a
node type is the one thing neither a subgraph input nor an override can bridge. One type
is what lets every template share a single Player Sync. The mode is the
``use_favorite_realm`` param; the UI plumbing both modes share is
:mod:`bartlebot.nodes.utility.realm_list`.

Favorite mode reads the ``Favorite Realm`` bot option -- the user's, never the bot's: it
is declared null here, answered by the Favorite Realm setting on the Bot settings tab,
and a per-bot override in Setup sits above that. A bot file naming a realm would send
everyone who runs it to the same place, which is the crowding a favorite realm exists to
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
on                      every player to the favorite       player 1 to the favorite, the rest
                                                            to the rows after it
off (next realm)        the leader to the next realm, and   one block of fresh rows, one each:
                        the others to the leader's          see :func:`next_block`
======================  ==================================  =====================================

A split param here would let a bot say Together on the bot and split on the node, which
can never be right, so there is none. Afterwards a Together bot is checked by sight and
anyone not with the leader ports to it; a Separate bot is checked for two players who
landed on one realm, which is logged rather than failed.
"""

import asyncio
import logging
from dataclasses import dataclass

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

#: Realms in that range. Pages 3-5 are always full, so the ring is fixed.
NEXT_REALM_RING = (NEXT_REALM_LAST_PAGE - NEXT_REALM_FIRST_PAGE + 1) * REALMS_PER_PAGE

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
        if await _spread_to_next_realms(machine, clients, safety_delay) == "FAILURE":
            return "FAILURE"
        coros = []
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
         favorite already, close the spellbook and report SUCCESS without paging.
      3. Otherwise anchor to page 1.
      4. On each page, read every row's name and match it against the favorite in
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


@dataclass
class _Place:
    """Where one client stands in the next-realm range, with its realm list left open."""

    #: Its row in the range, counted across pages 3-5 from 0; None when it is on no row there.
    position: int | None
    #: The page the list is showing.
    page: int
    #: The current realm's row text, for hop_to_row's log; empty when not in the range.
    name: str


def next_block(positions: list[int | None], ring: int = NEXT_REALM_RING) -> list[int]:
    """Separate, next realm: the row each player hops to, in player order.

    *positions* are the players' rows in the range, None for one outside it. The players
    take consecutive rows from the start of the longest run of rows nobody is on, so a block
    moves on to the block after it (0-3 to 4-7), a group on one realm fans out from it, and a
    block that wrapped, or a player whose last hop failed, still gets rows nobody holds. While
    the players stay a block, the ring order makes the next one the block left longest ago.
    Each player decides nothing alone: from a spread start, "the row N after my own" doubled
    the spacing onto realms the others had just left.
    """
    occupied = sorted({p for p in positions if p is not None})
    start, longest = 0, -1
    for i, row in enumerate(occupied):
        gap = (occupied[(i + 1) % len(occupied)] - row - 1) % ring
        if gap > longest:
            start, longest = (row + 1) % ring, gap
    return [(start + i) % ring for i in range(len(positions))]


async def _spread_to_next_realms(machine, clients: list, safety_delay: float) -> str:
    """Next-realm mode, Separate: find where every player is, then hop each to its row of one block.

    Every list stays open between the two, so the look costs nothing extra.
    """
    first, last = NEXT_REALM_FIRST_PAGE, NEXT_REALM_LAST_PAGE
    places = await asyncio.gather(
        *(_find_place(machine, c, first, last, safety_delay) for c in clients), return_exceptions=True
    )
    if not all(isinstance(p, _Place) for p in places):
        for client, place in zip(clients, places):
            if isinstance(place, _Place):
                await realm_list.close_spellbook(client)
        machine.log("FAILURE: not every player's current realm could be found, so none was moved", logging.WARNING)
        return "FAILURE"
    targets = next_block([p.position for p in places])
    machine.log(
        f"{_TAG}: rows {[p.position for p in places]} -> {targets} of the {NEXT_REALM_RING} on pages {first}–{last}",
        logging.DEBUG,
    )
    results = await asyncio.gather(
        *(_hop_in_range(machine, c, p, t, first) for c, p, t in zip(clients, places, targets)),
        return_exceptions=True,
    )
    return "FAILURE" if any(isinstance(r, Exception) or r == "FAILURE" for r in results) else "SUCCESS"


async def _to_next_realm(machine, client, start_page: int, end_page: int, safety_delay: float) -> str:
    """Hop to the realm after the current one within pages *start_page*..*end_page*, wrapping.

    A player on no row in the range takes the range's first realm.
    """
    place = await _find_place(machine, client, start_page, end_page, safety_delay)
    if place is None:
        return "FAILURE"
    ring = (end_page - start_page + 1) * REALMS_PER_PAGE
    target = 0 if place.position is None else (place.position + 1) % ring
    return await _hop_in_range(machine, client, place, target, start_page)


async def _find_place(machine, client, start_page: int, end_page: int, safety_delay: float) -> _Place | None:
    """Open the realm list and find the current realm's row within pages *start_page*..*end_page*.

    None, with the spellbook closed, when the list could not be opened or paged. Otherwise the
    list is left open on the page where the scan stopped.

    Flow:
      1. Open the spellbook to the Realms tab and wait for the list to render.
      2. Anchor to page 1 by clicking the left arrow until it is disabled, then page
         right to start_page, verifying each flip.
      3. Scan pages start_page..end_page for the current realm, identified by its
         grayed-out (disabled) button.
    """
    if not await realm_list.open_realm_list(machine, client, safety_delay, _TAG):
        return None

    machine.log(f"{_TAG}: anchoring to page 1", logging.DEBUG)
    if not await realm_list.navigate_to_page_1(machine, client, TRIGGER_TIMEOUT, _TAG):
        machine.log("FAILURE: could not anchor to page 1", logging.WARNING)
        await realm_list.close_spellbook(client)
        return None

    if start_page > 1:
        machine.log(f"{_TAG}: navigating to page {start_page}", logging.DEBUG)
        if not await realm_list.navigate_pages(machine, client, start_page - 1, TRIGGER_TIMEOUT, _TAG):
            machine.log(f"FAILURE: could not navigate to start page {start_page}", logging.WARNING)
            await realm_list.close_spellbook(client)
            return None

    for page in range(start_page, end_page + 1):
        machine.log(f"{_TAG}: scanning page {page} for current realm", logging.DEBUG)
        for row in range(REALMS_PER_PAGE):
            if await realm_list.is_current_realm(client, row):
                name = await realm_list.row_name(client, row)
                machine.log(f"{_TAG}: current realm is {name!r} (page {page}, row {row})", logging.DEBUG)
                return _Place((page - start_page) * REALMS_PER_PAGE + row, page, name)
        if page < end_page and not await realm_list.navigate_pages(machine, client, 1, TRIGGER_TIMEOUT, _TAG):
            machine.log(f"FAILURE: could not advance past page {page}", logging.WARNING)
            await realm_list.close_spellbook(client)
            return None

    machine.log(f"{_TAG}: current realm not found in pages {start_page}–{end_page}", logging.DEBUG)
    return _Place(None, end_page, "")


async def _hop_in_range(machine, client, place: _Place, target: int, start_page: int) -> str:
    """From *place*, with the list still open, hop to row *target* of the range starting at *start_page*.

    Flow:
      1. Navigate to the target's page. A blank target row (a short page) wraps to
         start_page row 0 -- a guard only: pages 3-5 are always full.
      2. Select the row, wait out the realm-hop cooldown, press Go To Realm, and wait for
         the loading screen to start and clear.
    """
    target_page, target_row = start_page + target // REALMS_PER_PAGE, target % REALMS_PER_PAGE
    machine.log(f"{_TAG}: target page {target_page}, row {target_row}", logging.DEBUG)

    steps = target_page - place.page
    if steps != 0 and not await realm_list.navigate_pages(machine, client, steps, TRIGGER_TIMEOUT, _TAG):
        machine.log(f"FAILURE: could not navigate to target page {target_page}", logging.WARNING)
        await realm_list.close_spellbook(client)
        return "FAILURE"
    if not await realm_list.row_name(client, target_row):
        if target_page != start_page and not await realm_list.navigate_pages(
            machine, client, start_page - target_page, TRIGGER_TIMEOUT, _TAG
        ):
            machine.log(f"FAILURE: could not navigate back to page {start_page}", logging.WARNING)
            await realm_list.close_spellbook(client)
            return "FAILURE"
        target_row = 0

    return await realm_list.hop_to_row(machine, client, target_row, _TAG, current_name=place.name)


async def _to_realm_after(machine, client, favorite: str, offset: int, safety_delay: float) -> str:
    """Hop to the realm *offset* rows after *favorite* in the list, for one client.

    Separate and favorite mode: player 1 takes the favorite, and each other player the row
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
