"""handle_potions node — use potions and refill from Hilda Brewer in the Commons if needed."""

import asyncio
import logging
from enum import Enum, auto

from bartleby.runtime.clients import game_keys
from bartleby.runtime.game.key_bindings import GameAction
from bartleby.runtime.paths import (
    MARK_BUTTON_PATH,
    POTION_BUY_PATH,
    POTION_EXIT_PATH,
    POTION_FILL_ALL_PATH,
    POTION_SHOP_BASE_PATH,
)
from wizwalker import XYZ

from bartlebot.clients import port_map
from bartlebot.nodes import node_helpers
from bartlebot.nodes.combat_and_healing import scaled_ratios
from bartlebot.nodes.param_schema import SAFETY_DELAY_OPTION, BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.teleportation import mark_location, teleport_to_marked_location
from bartlebot.nodes.wizard_city import (
    COMMONS_ZONE,
    DORM_ZONE,
    RAVENWOOD_EXIT_XYZ,
    RAVENWOOD_ZONE,
    go_to_ravenwood,
)
from bartlebot.nodes.zone_entry import enter_zone
from bartlebot.options import player_realms
from bartlebot.options.option_groups import GROUP_GENERAL

# ── Navigation coordinates ─────────────────────────────────────────────────────
# The route as far as Ravenwood lives in bartlebot.nodes.wizard_city. Only the Commons leg
# and Hilda herself are potion-specific.

# Hilda Brewer's location in the Commons (potion vendor).
_HILDA_XYZ = XYZ(-4398.70654296875, 1016.1954345703125, 229.00079345703125)

# Nearby Commons point where Hilda's interact prompt does NOT appear.
_HILDA_RESET_XYZ = XYZ(-3860.94, 1601.97, 229.0)

#: Whether players with no potion charges left go to Hilda Brewer to buy more. A bot option
#: rather than a param: spending gold describes the user, not this node's place in the graph,
#: so it is declared ``None`` and the app setting answers (docs/BOT_OPTIONS.md).
OPTION_REFILL = "Refill Potions"

# ── Timing constants ───────────────────────────────────────────────────────────
from bartleby.runtime.timing import (
    MAX_RETRIES,
    POLL_INTERVAL,
    SHOP_CLICK_DELAY,
    SHOP_EXIT_CLICK_INTERVAL,
    SHOP_OPEN_TIMEOUT,
)

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="if_health_below",
        type="ratio",
        label="Use Potion If Health Below",
        help="Players below this share of their health use a potion. At 0%, health is never checked.",
        default=0.5,
        required=True,
    ),
    ParamField(
        key="if_mana_below",
        type="ratio",
        label="Use Potion If Mana Below",
        help="Players below this share of their mana use a potion. At 0%, mana is never checked.",
        default=0.15,
        required=True,
    ),
    ParamField(
        key="mark_zone",
        type="zone",
        label="Mark Zone",
        help=(
            "The zone players must be in before leaving to buy potions, so the mark lands somewhere "
            "predictable, such as a dungeon entrance. The node fails if they're elsewhere."
        ),
        default="",
        required=False,
        default_hint="any zone",
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Has each player below a health or mana threshold use a potion. With Refill Potions on, "
        "players who run out mark their spot, buy more from Hilda Brewer in the Commons, use one "
        "if they still need it, and recall."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec(
            "SUCCESS", "Every player who needed a potion used one, and players who ran out refilled and are back."
        ),
        ReturnSpec(
            "FAILURE",
            (
                "A player who needed to buy wasn't in Mark Zone, couldn't mark for a reason other than "
                "mana, couldn't reach the potion shop, or couldn't mark or recall and then couldn't "
                "teleport back to another player. Players can be left in the Commons."
            ),
        ),
    ],
    required_bot_options=[
        BotOptionSpec(
            name=OPTION_REFILL,
            group=GROUP_GENERAL,
            type="bool",
            # Null: the author has no opinion, so the app setting answers. On by default,
            # which is what every bot did while this was a param.
            default=None,
            description=(
                "Buy potions from Hilda Brewer when a player runs out. Each refill costs gold and a trip "
                "to the Commons, and players come back to where they were."
            ),
        ),
        SAFETY_DELAY_OPTION,
    ],
    max_consecutive_retries=5,
    usage_notes=(
        (
            "With Challenge Mode on, thresholds are measured against the lower maximums the next "
            "entry scales players to, so the same thresholds work in both modes."
        )
        + "\n\n"
        + (
            "A player with too little mana to mark goes to buy anyway. After buying, it teleports "
            "to a player who stayed behind or recalled, through the friends list or the group. "
            "So does a player whose recall fails. A teleported player lands beside that player, "
            "away from its own starting spot. With Player Realms set to Separate, a player only "
            "teleports to one that shared its realm before the trip."
        )
        + "\n\n"
        + ("A purchase that buys nothing, usually for lack of gold, is logged as a warning, and the node goes on.")
    ),
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: scaled_ratios.ratios() (SprintyClient.calc_health_ratio(), calc_mana_ratio()),
          SprintyClient.use_potion(), SprintyClient.has_potion(), client.stats.potion_charge(),
          mark_location.run(), mark_location.short_of_mana(), enter_zone.run(),
          teleport_to_marked_location.run(), port_map.port_waves()

    Flow:
      1. Check health and mana ratios; use potions on clients below threshold that have them,
         waiting up to Safety Delay for each charge to drop (_drink), since use_potion()
         returns once the click is sent. With Challenge Mode on, the ratios are against the
         scaled max (scaled_ratios).
      2. Identify buying_clients: clients with no potion charges remaining after use.
      3. If refill is disabled or no clients need to buy, return SUCCESS.
      4. Check buying_clients zones:
         a. If all are already in the Commons, open the shop, buy, and have any buyer still
            below a threshold use a potion (no mark or recall needed). Return SUCCESS, or
            FAILURE if the shop does not open.
         b. If mark_zone is set, verify all buying_clients are in that zone. Return FAILURE if not.
         c. Otherwise, guard against clients stuck in a transit zone (Dorm, Ravenwood).
            Return FAILURE if any are found there.
      5. Snapshot each buyer's zone and position as its mark/recall target, and, under Player
         Realms: Separate, who shares whose realm (_realm_peers).
      6. Sort the buyers. One whose MarkButton is grayed and whose mana is below a mark's cost
         (mark_location.short_of_mana) skips the mark. The rest call mark_location.run(); one
         whose mark fails with its mana unchanged and below the cost (a click that got no
         answer) is counted as having no mana. Any other failed mark returns FAILURE, before
         anyone leaves.
      7-10. navigate_to_potion_vendor(): HOME, Dorm → Ravenwood → Commons, open Hilda's shop.
         Return FAILURE if any step fails.
      11. For each buying_client: buy potions until fully stocked or gold runs out, with a
          WARNING for a purchase that bought nothing.
      12. Have each buyer below a threshold use a potion, and each buyer that marked and is
          now below a recall's cost, whatever the thresholds say.
      13. Call teleport_to_marked_location.run() for each buyer that marked. A failed recall
          joins the movers.
      14. Port every mover (no mana to mark, or a failed recall) to a client that is back:
          one that never bought, or a buyer that recalled (_port_back). Return SUCCESS when
          nobody is left waiting.

    FAILURE: buying clients not in mark_zone or stuck in a transit zone (Dorm/Ravenwood);
    a mark failed for a reason other than mana; could not reach the dorm; navigation step
    failed; potion shop did not open after retries; a mover could not port back (no routes
    surveyed, nobody back to port to, no realm peer back under Separate, or the port failed).
    Movers left waiting stay in the Commons for recovery's Sync in zone to fetch.
    """
    health_threshold: float = params.get("if_health_below", 0.5)
    mana_threshold: float = params.get("if_mana_below", 0.15)
    refill = bool(machine.get_option(OPTION_REFILL, True))
    mark_zone: str = params.get("mark_zone", "") or ""
    scaled_to = scaled_ratios.challenge_level(machine)

    machine.log(
        f"handle_potions: health_threshold={health_threshold:.2f} "
        f"mana_threshold={mana_threshold:.2f} "
        f"refill={refill} mark_zone={mark_zone!r} scaled_to={scaled_to}"
    )

    # 1. Check health/mana ratios and use potions on low clients that have them.
    health_ratios, mana_ratios = zip(*await asyncio.gather(*[scaled_ratios.ratios(c, scaled_to) for c in clients]))
    machine.log(
        f"Health: {[round(r, 2) for r in health_ratios]}  Mana: {[round(r, 2) for r in mana_ratios]}",
        logging.DEBUG,
    )

    low_clients = [
        c for c, h, m in zip(clients, health_ratios, mana_ratios) if _is_low(h, m, health_threshold, mana_threshold)
    ]
    if low_clients:
        machine.log(
            f"{len(low_clients)} client(s) below threshold — using potions if available",
            logging.DEBUG,
        )
        await _drink_all(machine, clients, low_clients)

    # 2. Identify buying_clients: those with no potion charges remaining after use. _drink
    #    waited for each charge to drop, so a player that just drank its last potion reads
    #    empty here instead of starting the next run with none.
    has_potion_flags = await asyncio.gather(*[c.has_potion() for c in clients])
    buying_clients = [c for c, has in zip(clients, has_potion_flags) if not has]
    machine.log(
        f"{len(buying_clients)} client(s) have no potions left",
        logging.DEBUG,
    )

    # 3. Early exit if refill is disabled or everyone still has potions.
    if not refill or not buying_clients:
        machine.log("SUCCESS: no refill needed")
        return "SUCCESS"

    # 4. Check buying_clients zones.
    zones = await node_helpers.read_zones(buying_clients, poll_interval=POLL_INTERVAL)
    machine.log(f"Buying client zones: {list(zones)}", logging.DEBUG)

    # 4a. Commons shortcut: all buying clients are already at Hilda's location —
    #     skip mark, HOME, and navigation; just open the shop and buy in place.
    if all(z == COMMONS_ZONE for z in zones):
        machine.log("Buying clients already in Commons; skipping mark and recall…")
        if not await _open_potion_shop(machine, buying_clients):
            machine.log("FAILURE: could not open potion shop", logging.WARNING)
            return "FAILURE"
        await _buy_all(machine, clients, buying_clients)
        await _drink_after_buying(machine, clients, buying_clients, (health_threshold, mana_threshold), scaled_to)
        machine.log(f"SUCCESS: potions refilled in Commons for {len(buying_clients)} client(s)")
        return "SUCCESS"

    # 4b/c. Normal path: verify origin zone before placing the mark.
    #   Without mark_zone, guard against clients already being in a transit zone
    #   (Dorm → Ravenwood). If they are, the mark would be placed there and the
    #   recall would return clients to a transit zone rather than their actual origin.
    _TRANSIT_ZONES = {DORM_ZONE, RAVENWOOD_ZONE}
    if mark_zone:
        if not all(z == mark_zone for z in zones):
            wrong = [z for z in zones if z != mark_zone]
            machine.log(
                f"FAILURE: buying clients not in mark_zone (expected={mark_zone!r}, found={wrong})",
                logging.WARNING,
            )
            return "FAILURE"
    else:
        stuck = [z for z in zones if z in _TRANSIT_ZONES]
        if stuck:
            machine.log(
                f"FAILURE: buying client(s) are already in a transit zone {stuck!r}. "
                f"They may be stuck from a previous failed run. "
                f"Specify mark_zone to enforce a valid origin zone.",
                logging.WARNING,
            )
            return "FAILURE"

    # 5. Snapshot each buying client's current position for independent mark/recall.
    #    Using each client's own position ensures they recall to where they actually were,
    #    not to the first client's position (clients may not be co-located).
    positions = await asyncio.gather(*[c.body.position() for c in buying_clients])
    per_client_recall_params = [
        {"zone": zone, "xyz": {"x": pos.x, "y": pos.y, "z": pos.z}} for zone, pos in zip(zones, positions)
    ]
    for i, p in enumerate(per_client_recall_params):
        machine.log(
            f"Client {i} mark/recall point: {p['zone']!r} "
            f"XYZ({p['xyz']['x']:.1f}, {p['xyz']['y']:.1f}, {p['xyz']['z']:.1f})",
            logging.DEBUG,
        )

    #    Who shares whose realm, read before anyone leaves: a port moves the porter onto the
    #    target's realm, so under Separate a mover may only port to a pre-trip peer.
    peers = await _realm_peers(machine, clients)

    # 6. Mark each buying client at its own position. One with no mana to mark goes anyway,
    #    and ports back in step 14. The others still mark: making everyone skip the mark for
    #    one player's sake would turn its problem into a port for all.
    recall_params = dict(zip(map(id, buying_clients), per_client_recall_params))
    outcomes = await asyncio.gather(*[_mark(machine, c, recall_params[id(c)]) for c in buying_clients])
    if _MarkOutcome.FAILED in outcomes:
        machine.log("FAILURE: mark_location failed for one or more clients", logging.WARNING)
        return "FAILURE"
    markers = [c for c, o in zip(buying_clients, outcomes) if o is _MarkOutcome.MARKED]
    no_mana = [c for c, o in zip(buying_clients, outcomes) if o is _MarkOutcome.NO_MANA]
    if no_mana:
        machine.log(
            f"Player(s) {_numbers(clients, no_mana)} have too little mana to mark; "
            "they buy anyway and port back afterwards"
        )

    # 7-10. Travel to Hilda Brewer and open her shop.
    if not await navigate_to_potion_vendor(machine, buying_clients):
        return "FAILURE"

    # 11. Buy potions for each buying client (parallel; each client manages their own shop window).
    await _buy_all(machine, clients, buying_clients)

    # 12. Anyone still low drinks, and a marker short of a recall's cost drinks regardless:
    #     with the mana threshold at 0 it would otherwise skip the potion and fail the recall.
    await _drink_after_buying(
        machine, clients, buying_clients, (health_threshold, mana_threshold), scaled_to, markers=markers
    )

    # 13. Recall each marker to its own marked location. A failed recall ports instead.
    if markers:
        machine.log("Recalling buying clients to marked location…")
    recall_results = await asyncio.gather(
        *[teleport_to_marked_location.run(machine, [c], recall_params[id(c)]) for c in markers]
    )
    recalled = [c for c, r in zip(markers, recall_results) if r == "SUCCESS"]
    unrecalled = [c for c, r in zip(markers, recall_results) if r != "SUCCESS"]
    if unrecalled:
        machine.log(f"Player(s) {_numbers(clients, unrecalled)} could not recall; porting them back instead")

    # 14. Port everyone not back to someone who is: a client that never left, or one that recalled.
    movers = no_mana + unrecalled
    if movers:
        arrived = [c for c in clients if not _contains(buying_clients, c)] + recalled
        left = await _port_back(machine, clients, movers, arrived, peers)
        if left:
            machine.log(
                f"FAILURE: player(s) {_numbers(clients, left)} could not get back from the Commons",
                logging.WARNING,
            )
            return "FAILURE"

    origin_zones = [rp["zone"] for rp in per_client_recall_params]
    machine.log(
        f"SUCCESS: potions refilled for {len(buying_clients)} client(s), back in {origin_zones} "
        f"({len(recalled)} recalled, {len(movers)} ported)"
    )
    return "SUCCESS"


# ── Navigation ─────────────────────────────────────────────────────────────────


async def navigate_to_potion_vendor(machine, clients: list) -> bool:
    """Take clients from wherever they are to Hilda Brewer, with her shop window open.

    Steps 7-10 of run(): wizard_city.go_to_ravenwood() covers 7-8 (unequip castles, HOME to
    the dorm, Dorm → Ravenwood), then Ravenwood → Commons, then approach Hilda and press X.
    Returns True only when the shop is open on every client; logs the reason and returns
    False otherwise.

    Public because this is the whole interesting part of handle_potions and the part that
    breaks — buying is a few clicks in a window that is already open. tools/dry_run_potion_route.py
    drives exactly this, so the route can be exercised without a client that actually needs
    potions and without spending gold.
    """
    if not await go_to_ravenwood(machine, clients):
        return False

    machine.log("Navigating Ravenwood → Commons…")
    if (
        await enter_zone.run(
            machine,
            clients,
            {"from_zone": RAVENWOOD_ZONE, "to_zone": COMMONS_ZONE, "xyz": RAVENWOOD_EXIT_XYZ},
        )
        != "SUCCESS"
    ):
        machine.log("FAILURE: Ravenwood → Commons failed", logging.WARNING)
        return False

    machine.log("Opening potion shop at Hilda Brewer…")
    if not await _open_potion_shop(machine, clients):
        machine.log("FAILURE: could not open potion shop", logging.WARNING)
        return False

    return True


async def close_potion_shop(client) -> bool:
    """Click Exit until the potion shop window is gone. True once it is.

    One click is not enough: the button can be swallowed while the shop is mid-animation.
    Gives up after WINDOW_CLOSE_TIMEOUT with a warning, where it used to click for as long
    as the shop read open, which a window that never reads closed would make forever.
    """
    return await node_helpers.click_until_gone(
        client,
        POTION_EXIT_PATH,
        is_open=lambda: node_helpers.is_visible_by_path(client, POTION_SHOP_BASE_PATH),
        poll_interval=SHOP_EXIT_CLICK_INTERVAL,
    )


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _open_potion_shop(machine, clients: list) -> bool:
    """
    Teleport clients to Hilda Brewer, wait for the interact prompt, press X,
    and confirm the potion shop window opens on all clients.

    Returns True if the shop opened successfully, False after all retries are exhausted.
    """
    safety_delay: float = machine.get_option("Safety Delay", 3.0)
    for attempt in range(1, MAX_RETRIES + 1):
        machine.log(f"Attempt {attempt}/{MAX_RETRIES}: teleporting to Hilda…", logging.DEBUG)
        await asyncio.gather(*[c.teleport(_HILDA_XYZ) for c in clients])

        if not await node_helpers.poll_until(
            lambda: node_helpers.all_in_npc_range(clients), safety_delay, POLL_INTERVAL
        ):
            machine.log(f"Interact prompt did not appear (attempt {attempt}); resetting…", logging.DEBUG)
            await asyncio.gather(*[c.teleport(_HILDA_RESET_XYZ) for c in clients])
            continue

        machine.log("Pressing X to open potion shop…", logging.DEBUG)
        await asyncio.gather(*[game_keys.press(c, GameAction.INTERACT, 0.1) for c in clients])

        if await node_helpers.poll_until(lambda: _all_shop_open(clients), SHOP_OPEN_TIMEOUT, POLL_INTERVAL):
            return True

        machine.log(f"Potion shop did not open (attempt {attempt}); resetting…", logging.DEBUG)
        await asyncio.gather(*[c.teleport(_HILDA_RESET_XYZ) for c in clients])

    machine.log(
        f"FAILURE: potion shop never opened after {MAX_RETRIES} attempts",
        logging.WARNING,
    )
    return False


async def _buy_all(machine, clients: list, buyers: list) -> None:
    """Buy potions for every buyer at once; each manages its own shop window."""
    machine.log("Buying potions…")
    await asyncio.gather(*[_buy_potions_for_client(machine, c, _number(clients, c)) for c in buyers])


async def _buy_potions_for_client(machine, client, number: int) -> None:
    """
    Buy potions for a single client until fully stocked or gold runs out.
    Assumes the potion shop window is already open when called. `number` is the client's
    1-based player number, for the warning a purchase that buys nothing logs.
    """
    max_potions = await client.stats.potion_max()
    machine.log(f"Buying potions (max={max_potions})…", logging.DEBUG)

    while True:
        charge_before = await client.stats.potion_charge()
        if charge_before >= max_potions:
            machine.log(f"Potions full ({charge_before}/{max_potions})", logging.DEBUG)
            break

        # Re-open the shop window if it closed between iterations.
        if not await node_helpers.is_visible_by_path(client, POTION_SHOP_BASE_PATH):
            machine.log("Shop closed unexpectedly; re-opening…", logging.DEBUG)
            await game_keys.press(client, GameAction.INTERACT, 0.1)
            await node_helpers.poll_until(
                lambda: node_helpers.is_visible_by_path(client, POTION_SHOP_BASE_PATH),
                SHOP_OPEN_TIMEOUT,
                POLL_INTERVAL,
            )

        await node_helpers.click_by_path(client, POTION_FILL_ALL_PATH)
        await asyncio.sleep(SHOP_CLICK_DELAY)
        await node_helpers.click_by_path(client, POTION_BUY_PATH)
        await asyncio.sleep(SHOP_CLICK_DELAY)

        await close_potion_shop(client)

        charge_after = await client.stats.potion_charge()
        machine.log(f"Charge after buy: {charge_after}/{max_potions}", logging.DEBUG)

        if charge_after == charge_before:
            # A player with no gold and no mana can't drink, so it ports back still empty.
            machine.log(
                f"Player {number}'s potion purchase bought nothing ({charge_after}/{max_potions}); "
                "gold may have run out",
                logging.WARNING,
            )
            break

        # Re-open the shop for the next purchase iteration.
        if charge_after < max_potions:
            await game_keys.press(client, GameAction.INTERACT, 0.1)
            await node_helpers.poll_until(
                lambda: node_helpers.is_visible_by_path(client, POTION_SHOP_BASE_PATH),
                SHOP_OPEN_TIMEOUT,
                POLL_INTERVAL,
            )


def _is_low(health: float, mana: float, health_threshold: float, mana_threshold: float) -> bool:
    """Whether either ratio is below its threshold. A threshold of 0 never checks its ratio."""
    return (health_threshold > 0.0 and health < health_threshold) or (mana_threshold > 0.0 and mana < mana_threshold)


async def _drink_all(machine, clients: list, drinkers: list) -> None:
    """Have each of `drinkers` use a potion, and wait until each use shows in its charge."""
    results = await asyncio.gather(*[_drink(machine, c) for c in drinkers])
    for c, drank in zip(drinkers, results):
        machine.log(f"Player {_number(clients, c)} used a potion: {drank}", logging.DEBUG)


async def _drink(machine, client) -> bool:
    """Use a potion, and return True once the client's charge has dropped.

    use_potion() returns as soon as the click is sent. Read straight after, the charge can
    still show the potion just used, which once let a player drink its last potion, count as
    stocked, and start the next run with none. So the charge is polled, up to Safety Delay.
    """
    safety_delay: float = machine.get_option("Safety Delay", 3.0)
    before = await client.stats.potion_charge()
    if not await _use_potion(client):
        return False
    if await node_helpers.poll_until(lambda: _charge_below(client, before), safety_delay, POLL_INTERVAL):
        return True
    machine.log(f"Potion charge still read {before} {safety_delay}s after using a potion", logging.WARNING)
    return False


async def _charge_below(client, charge: int) -> bool:
    return await client.stats.potion_charge() < charge


async def _drink_after_buying(
    machine, clients: list, buyers: list, thresholds: tuple[float, float], scaled_to, markers: list | tuple = ()
) -> None:
    """Have each buyer still below a threshold use one of the potions it just bought.

    A buyer in `markers` also drinks if it is short of a recall's cost, whatever the
    thresholds say: one with just enough mana to mark may have too little left to recall.
    """
    ratios = await asyncio.gather(*[scaled_ratios.ratios(c, scaled_to) for c in buyers])

    async def needs_potion(client, health: float, mana: float) -> bool:
        if _is_low(health, mana, *thresholds):
            return True
        return _contains(markers, client) and await mark_location.short_of_mana(client)

    needs = await asyncio.gather(*[needs_potion(c, h, m) for c, (h, m) in zip(buyers, ratios)])
    drinkers = [c for c, need in zip(buyers, needs) if need]
    if drinkers:
        await _drink_all(machine, clients, drinkers)


async def _use_potion(client) -> bool:
    """Activate the mouseless cursor hook and use a potion for one client.

    use_potion() rather than use_potion_if_needed(): the caller has already decided this
    client needs one, and use_potion_if_needed() re-checks against its own fixed defaults
    (20% health, 10% mana). The installed WizSprinter use_potion() doesn't activate the
    hook itself.
    """
    async with client.mouse_handler:
        return await client.use_potion()


class _MarkOutcome(Enum):
    """What step 6 did for one buyer."""

    MARKED = auto()
    NO_MANA = auto()  # too little mana to mark: it goes anyway and ports back
    FAILED = auto()  # grayed for another reason (a house, a fight), or the mark wasn't confirmed


async def _mark(machine, client, recall_params: dict) -> _MarkOutcome:
    """Mark `client` where it stands, unless it has too little mana to.

    mark_location.run() fails without saying why, so the mana is checked here: a grayed
    MarkButton with mana below the cost before the call, or a failed mark with mana unchanged
    and below the cost after it, in case the game leaves the button lit below the cost.
    """
    if not await node_helpers.is_enabled_by_path(client, MARK_BUTTON_PATH) and await mark_location.short_of_mana(
        client
    ):
        return _MarkOutcome.NO_MANA
    mana = await client.stats.current_mana()
    if await mark_location.run(machine, [client], recall_params) == "SUCCESS":
        return _MarkOutcome.MARKED
    if await client.stats.current_mana() == mana and await mark_location.short_of_mana(client):
        return _MarkOutcome.NO_MANA
    return _MarkOutcome.FAILED


async def _realm_peers(machine, clients: list) -> dict:
    """Each client's realm peers among `clients`, itself left out, when Player Realms is Separate.

    Empty when the players are together, where anyone may port to anyone.
    """
    if not player_realms.machine_is_separate(machine):
        return {}
    await node_helpers.read_sightings(clients, machine)
    return {c: [p for p in machine.together_with(c, clients) if p is not c] for c in clients}


async def _port_back(machine, clients: list, movers: list, arrived: list, peers: dict) -> list:
    """Port each of `movers` to one of `arrived`, and return those still waiting.

    Under Player Realms: Separate, a mover ports only to an arrived client that shared its
    realm before the trip, as node_helpers._recovery_batches does for zone stragglers.
    """
    who = _numbers(clients, movers)
    if machine.port_map is None:
        machine.log(f"No port routes were surveyed for this run, so player(s) {who} can't port back", logging.WARNING)
        return list(movers)
    if not arrived:
        machine.log(f"Nobody is back at the starting point for player(s) {who} to port to", logging.WARNING)
        return list(movers)
    if not player_realms.machine_is_separate(machine):
        return await port_map.port_waves(machine, clients, movers, arrived, machine.port_map)

    stranded: list = []
    groups: dict[tuple[int, ...], tuple[list, list]] = {}
    for mover in movers:
        targets = [a for a in arrived if _contains(peers.get(mover, ()), a)]
        if not targets:
            machine.log(
                f"Player {_number(clients, mover)} shared a realm with nobody who is back; "
                "not porting it across realms",
                logging.WARNING,
            )
            stranded.append(mover)
            continue
        groups.setdefault(tuple(map(id, targets)), (targets, []))[1].append(mover)
    left = await asyncio.gather(
        *(port_map.port_waves(machine, clients, group, targets, machine.port_map) for targets, group in groups.values())
    )
    return stranded + [c for batch in left for c in batch]


def _contains(clients, client) -> bool:
    """Whether `client` is one of `clients`, by identity."""
    return any(c is client for c in clients)


def _number(clients: list, client) -> int:
    """`client`'s 1-based player number, which the log uses in place of wizard names."""
    return next((i + 1 for i, c in enumerate(clients) if c is client), 0)


def _numbers(clients: list, subset: list) -> list[int]:
    return [_number(clients, c) for c in subset]


async def _all_shop_open(clients: list) -> bool:
    """Return True when the potion shop window is visible for all clients."""
    results = await asyncio.gather(*[node_helpers.is_visible_by_path(c, POTION_SHOP_BASE_PATH) for c in clients])
    return all(results)
