"""Node: change every player to the user's favourite realm.

The realm is the ``Favorite Realm`` bot option -- the user's, never the bot's: it is
declared null here, answered by the Favorite Realm setting on the Bot settings tab, and
a per-bot override in Setup sits above that. A bot file naming a realm would send
everyone who runs it to the same place, which is the crowding a favourite realm exists
to escape, so ``Favorite Realm`` is in ``bot_options.USER_OWNED_OPTIONS`` and the
Options surface has no row for it.

The stored value is the realm's *key* (its English name). The in-game list shows the
client's own locale, so a row is matched through :func:`bartlebot.realms.matches`,
against every locale's text for that key.
"""

import asyncio
import logging

from bartlebot import realms
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ReturnSpec
from bartlebot.nodes.timing import TRIGGER_TIMEOUT
from bartlebot.nodes.utility import realm_list
from bartlebot.nodes.utility.realm_list import REALMS_PER_PAGE

_TAG = "change_to_favorite_realm"

OPTION_FAVORITE_REALM = "Favorite Realm"

#: More pages than the realm list has ever had. A guard, not a limit: paging stops at
#: the disabled right arrow, and this only keeps a list that never disables it from
#: being walked forever.
_MAX_PAGES = 10

NODE_SPEC = NodeSpec(
    description=(
        "Switches every player to the realm chosen as Favorite Realm — in Settings → Bot, or "
        "overridden for this bot in Setup. Opens the spellbook to the Realms tab, pages "
        "through the list until that realm's row is found, and hops to it, waiting out the "
        "realm-hop cooldown if needed. A player already on that realm is left alone."
    ),
    returns=[
        ReturnSpec("SUCCESS", "Every player is on the favorite realm, whether it hopped or was already there."),
        ReturnSpec(
            "FAILURE",
            "No favorite realm is set; the spellbook or realms panel did not open; the realm was not "
            "found on any page; the realm-hop timer did not expire within the timeout; the game refused "
            "the switch (a dungeon, gauntlet or house); or a loading screen was not detected.",
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
        ),
    ],
    max_consecutive_retries=5,
    usage_notes=(
        "Realms can only be switched from an open-world zone: a dungeon, gauntlet or house refuses "
        'with "You are not allowed to switch Realms from this location", and the node fails. Place '
        "it before entering the dungeon, or after leaving it. With a favorite realm set, every launched "
        "player also has the game's Remember Realm option turned on, so a relogged player comes back "
        "to the realm it left."
    ),
    see_also=["change_to_next_realm"],
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: realm_list.open_realm_list(), realm_list.navigate_to_page_1(), realm_list.navigate_pages(),
          realm_list.row_name(), realm_list.is_current_realm(), realm_list.hop_to_row(), realms.matches()

    Flow (per client, run concurrently via asyncio.gather):
      1. Resolve the Favorite Realm option; FAILURE at once if it is empty or names a
         realm the snapshot does not know, so the user is told what to set.
      2. Open the spellbook to the Realms tab and wait for the list to render.
      3. Anchor to page 1.
      4. On each page, read every row's name and match it against the favourite in
         every locale. A blank row ends the page.
      5. If the matching row's button is disabled the player is already there: close the
         spellbook and report SUCCESS. Otherwise select it, wait out the cooldown, press
         Go To Realm, and wait for the loading screen to start and clear.
      6. No match and the right arrow enabled: page right and repeat. No match and the
         arrow disabled: FAILURE, the realm is not in the list.
    """
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
    safety_delay: float = machine.get_option("Safety Delay", 3.0)

    results = await asyncio.gather(
        *[_change_realm_for_client(machine, c, favorite, safety_delay) for c in clients],
        return_exceptions=True,
    )
    if any(isinstance(r, Exception) or r == "FAILURE" for r in results):
        return "FAILURE"
    return "SUCCESS"


async def _change_realm_for_client(machine, client, favorite: str, safety_delay: float) -> str:
    """Find *favorite* in the realm list and hop to it, for one client."""
    if not await realm_list.open_realm_list(machine, client, safety_delay, _TAG):
        return "FAILURE"

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
