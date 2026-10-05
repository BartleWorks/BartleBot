"""Node: level scale the chosen players at one Scales of Knowledge, from wherever they are.

One node for the whole job (#141): it checks who needs the elixir, marks where the players
are, takes everyone to the chosen Scales, applies the elixir and recalls. It replaces
check_level_scaling, apply_level_scaling and the detour a bot built by hand around them.
``docs/LEVEL_SCALING.md`` has the decisions behind it.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from wizwalker import XYZ, Keycode

from bartlebot import player_realms, scales, zone_names
from bartlebot.nodes import node_helpers, zone_navigation
from bartlebot.nodes.node_helpers import POSITION_MARGIN
from bartlebot.nodes.param_schema import SAFETY_DELAY_OPTION, BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import LEVEL_SCALING_ELIXIR_PATH, LEVEL_SCALING_OPTION_BUTTON_PATH
from bartlebot.nodes.teleportation import mark_location, teleport_to_marked_location
from bartlebot.nodes.zone_entry import enter_dungeon, enter_zone
from bartlebot.option_groups import GROUP_TEAM

PARAM_SCALES = "scales"

#: Every node type that reads the elixir indicator. A bot holding one gets the pre-run
#: warning when a level scaling player starts the run with an elixir already active.
LEVEL_SCALING_NODE_TYPES = frozenset({"handle_level_scaling", "check_level_scaling", "apply_level_scaling"})

#: Where this node keeps its marks in ``machine.node_scratch``.
SCRATCH_KEY = "handle_level_scaling"


LEVEL_SCALING_PLAYERS_OPTION = BotOptionSpec(
    name="Level Scaling Players",
    group=GROUP_TEAM,
    type="player_list",
    default=[],
    description="Which players to check/apply level scaling. Empty = no players. Shared by every level scaling node.",
)

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key=PARAM_SCALES,
        type="scales",
        label="Scales of Knowledge",
        help=(
            "Which Scales of Knowledge the players take the elixir from. Normally the Scales of the "
            "world this bot farms, since the elixir scales a player to that dungeon's level.\n\n"
            "The list is grouped by world; type part of a dungeon's name, its world or its level to "
            "narrow it."
        ),
        default="",
        default_hint=(
            "The Scales of the world this bot farms, when that world has one. Choose one by hand for a "
            "world that has several, or to use another world's."
        ),
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Makes sure every Level Scaling Player has the level scaling elixir. When one does not, every "
        "player marks where they stand, travels to the chosen Scales of Knowledge, the players who lack "
        "the elixir take it, and everyone recalls. Players already inside the Scales' dungeon take the "
        "elixir in place and stay there."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec(
            "SUCCESS",
            "Every Level Scaling Player has the elixir, and the players are where the bot expects them: "
            "back where they were, or still inside the Scales' dungeon if that is where they started.",
        ),
        ReturnSpec(
            "FAILURE",
            "The Scales is not one this build knows; a player could not mark (no mana, or a zone that "
            "refuses marks, such as a house); the trip to the Scales failed; a player still lacks the "
            "elixir after every retry; or a player did not land back on their mark.",
        ),
    ],
    required_bot_options=[player_realms.SPEC, SAFETY_DELAY_OPTION, LEVEL_SCALING_PLAYERS_OPTION],
    # One tick is a whole trip, and the apply step already retries every player.
    max_consecutive_retries=3,
    usage_notes=(
        "When every Level Scaling Player already shows an elixir, nothing happens and the node returns "
        "SUCCESS at once, so it is cheap at the top of a loop.\n\n"
        "Left unset, the Scales is the one for the world this bot farms: the world most of its zones are "
        "in. A world with several Scales, or none, has no default, and the node says so.\n\n"
        "Whether the players mark and recall is decided by where they are, not by a setting. Away from "
        "the Scales' dungeon, they mark, travel, scale and recall. Inside it, they scale in place and "
        "stay. So in a bot that farms the Scales' own dungeon, place this node after the bot's dungeon "
        "entry; placed before it, the players make an extra trip in and back out.\n\n"
        "Every player makes the trip, not only the ones being scaled. The dungeon is entered through "
        "its sigil in normal mode, never through Team-Up or Challenge Mode.\n\n"
        "In a Challenge Mode dungeon the node does nothing and returns SUCCESS: the game level scales "
        "everyone inside it, and it has no Scales of Knowledge.\n\n"
        "A player who already has an elixir keeps it: the node never cancels one. The game shows every "
        "elixir the same way, so a player with any other elixir active reads as level scaled. Starting "
        "a run in that state raises a warning."
    ),
    see_also=["sync_in_zone", "enter_dungeon"],
)

from bartlebot.nodes.timing import (  # noqa: E402
    LEVEL_SCALING_STAGGER_DELAY,
    LOADING_SCREEN_TIMEOUT,
    MAX_RETRIES,
    POLL_INTERVAL,
)

# ---------------------------------------------------------------------------
# Decisions, kept free of clients so they can be tested without one
# ---------------------------------------------------------------------------


#: Where one client stood when it marked: what its recall is checked against. mark_client
#: records the same thing for the rest of the run.
Mark = mark_location.Mark


@dataclass(frozen=True)
class Trip:
    """What is left of the way to a Scales, from where the clients are."""

    inside: bool
    """The clients are already in the Scales' dungeon: they scale in place, with no mark or recall."""
    navigate: bool
    """Zone navigation takes the clients to the entrance zone first."""
    steps: tuple[scales.Step, ...]
    """The entry's steps still to make, in order."""


def selected_numbers(option: object, client_count: int) -> list[int]:
    """The 1-based player numbers a Level Scaling Players value names, among `client_count` clients."""
    if not isinstance(option, list | tuple):
        return []
    return [n for n in option if isinstance(n, int) and 0 < n <= client_count]


def plan_trip(entry: scales.Scales, zones: Sequence[str | None]) -> Trip:
    """How clients standing in `zones` get to `entry`'s Scales.

    Clients that share one zone on the entry's own path pick the path up from there. Anything
    else (somewhere unrelated, or split across zones) starts from zone navigation.
    """
    done = _steps_done(entry, zones)
    return Trip(inside=done > entry.dungeon_from, navigate=done == 0, steps=entry.steps[done:])


def _steps_done(entry: scales.Scales, zones: Sequence[str | None]) -> int:
    """How many of `entry`'s steps clients in `zones` have already made."""
    if not zones or not zones[0] or any(zone != zones[0] for zone in zones):
        return 0
    zone = zones[0]
    if node_helpers.zone_matches(zone, entry.zone):
        return len(entry.steps)
    for index in reversed(range(len(entry.steps))):
        if node_helpers.zone_matches(zone, entry.steps[index].to_zone):
            return index + 1
    return 0


def recall_mismatches(
    marks: Sequence[Mark], zones: Sequence[str | None], positions: Sequence[XYZ], margin: float = POSITION_MARGIN
) -> list[int]:
    """The 1-based numbers of the clients that did not land on their own mark."""
    return [
        number
        for number, (mark, zone, position) in enumerate(zip(marks, zones, positions), start=1)
        if zone != mark.zone or node_helpers.distance(position, mark.position) > margin
    ]


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def players_with_elixir(clients: list, numbers: Sequence[int]) -> list[int]:
    """Which of the players in `numbers` (1-based) show an elixir.

    The indicator is the same for every elixir, so this cannot say it is the level scaling one.
    """
    shown = await asyncio.gather(
        *[node_helpers.is_visible_by_path(clients[n - 1], LEVEL_SCALING_ELIXIR_PATH) for n in numbers]
    )
    return [n for n, active in zip(numbers, shown) if active]


async def _lacking(clients: list, numbers: Sequence[int]) -> list[int]:
    have = await players_with_elixir(clients, numbers)
    return [n for n in numbers if n not in have]


async def _zones(clients: list) -> list[str] | None:
    """Every client's zone, once none is between zones. None if one stays unreadable."""
    zones: list[str] = []

    async def readable() -> bool:
        zones[:] = await asyncio.gather(*[c.zone_name() for c in clients])
        return all(zones)

    if not await node_helpers.poll_until(readable, LOADING_SCREEN_TIMEOUT, POLL_INTERVAL):
        return None
    return zones


# ---------------------------------------------------------------------------
# The node
# ---------------------------------------------------------------------------


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: node_helpers.is_visible_by_path(), mark_location.mark_client(), zone_navigation.navigate(),
          enter_dungeon.run(), enter_zone.run(), apply_elixir(),
          teleport_to_marked_location.recall()

    Flow:
      1. Resolve the Scales from its key; an empty key is the default Scales of the bot's world.
         FAILURE if the table has no such entry.
      2. Check. SUCCESS at once if every Level Scaling Player shows an elixir (or none is chosen),
         unless a cancelled run of this node left the players away from their marks.
      2b. SUCCESS at once if every client is in a Challenge Mode dungeon: the game scales its
          players itself there, and that dungeon has no Scales.
      3. Mark, if away from the Scales' dungeon: every client marks where it stands and the node
         remembers each one's zone and position. FAILURE if any mark fails.
      4. Travel: zone navigation to the entrance zone, then the entry's sigil and gateway steps
         not yet made. FAILURE if any step fails.
      5. Apply the elixir to the players that lack it, one at a time, with retries.
      6. Recall, if step 3 marked, and compare where each client lands against its mark.

    A rerun after the ambient combat watcher cancelled this node finds the marks of the first run
    in machine.node_scratch, so it does not mark again partway to the Scales. The record is
    dropped whenever the node returns.

    FAILURE: unknown Scales; a mark failed; the trip failed; a player still lacks the elixir after
    the retries; a recall failed or landed somewhere else.
    """
    key = params.get(PARAM_SCALES) or ""
    entry = scales.resolve(key, getattr(machine, "bot_config", None))
    if entry is None:
        reason = (
            f"{key!r} is not a Scales of Knowledge this build knows"
            if key
            else "no Scales of Knowledge is chosen, and this bot's world has no default one"
        )
        machine.log(f"FAILURE: {reason}", logging.WARNING)
        return "FAILURE"

    numbers = selected_numbers(machine.get_option(LEVEL_SCALING_PLAYERS_OPTION.name, []), len(clients))
    lacking = await _lacking(clients, numbers)
    marks = _kept_marks(machine, clients)
    if not lacking and marks is None:
        machine.log("SUCCESS: every level scaling player already has an elixir")
        return "SUCCESS"

    # Challenge Mode scales its players itself, shows no elixir for it, and has no Scales inside.
    # Leaving to fetch an elixir would give the instance up for one nobody needs.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    if marks is None and zones and all(zone_names.challenge_variant_of(zone) for zone in zones):
        machine.log("SUCCESS: the players are in a Challenge Mode dungeon, which level scales them itself")
        return "SUCCESS"

    machine.log(f"handle_level_scaling: {scales.describe(entry)}, for player(s) {lacking}")
    result = await _scale(machine, clients, entry, numbers, marks)
    # Reached only when the node finished; a cancel leaves the marks for the rerun.
    machine.node_scratch.pop(SCRATCH_KEY, None)
    return result


def _kept_marks(machine, clients: list) -> list[Mark] | None:
    """The marks a cancelled run of this node left, if they are for these same clients."""
    kept = machine.node_scratch.get(SCRATCH_KEY)
    if kept is None:
        return None
    kept_clients, marks = kept
    if len(kept_clients) != len(clients) or any(a is not b for a, b in zip(kept_clients, clients)):
        machine.node_scratch.pop(SCRATCH_KEY, None)
        return None
    return marks


async def _scale(machine, clients: list, entry: scales.Scales, numbers: list[int], marks: list[Mark] | None) -> str:
    zones = await _zones(clients)
    if zones is None:
        machine.log(f"FAILURE: a client's zone could not be read for {LOADING_SCREEN_TIMEOUT:.0f}s", logging.WARNING)
        return "FAILURE"
    trip = plan_trip(entry, zones)

    # 3. Mark, unless the clients are already in the Scales' dungeon or an earlier run marked.
    if marks is None and not trip.inside:
        marks = await _mark_all(machine, clients, zones)
        if marks is None:
            return "FAILURE"
        machine.node_scratch[SCRATCH_KEY] = (list(clients), marks)

    # 4 and 5. A rerun that was cancelled after the elixirs landed has only the recall left.
    lacking = await _lacking(clients, numbers)
    if lacking:
        if not await _travel(machine, clients, entry, trip):
            return "FAILURE"
        # Read again at the Scales: a trip is long enough for an elixir to run out on the way.
        lacking = await _lacking(clients, numbers)
        safety_delay: float = machine.get_option("Safety Delay", 3.0)
        if lacking and not await apply_elixir(
            machine, [(n, clients[n - 1]) for n in lacking], entry.interact_xyz, entry.reset_xyz, safety_delay
        ):
            return "FAILURE"

    # 6. Recall, if the clients marked.
    if marks is None:
        machine.log("SUCCESS: level scaling applied; the players stay in the Scales' dungeon")
        return "SUCCESS"
    return await _return_to_marks(machine, clients, marks)


async def _mark_all(machine, clients: list, zones: list[str]) -> list[Mark] | None:
    """Mark every client where it stands. None, having logged why, if any client could not."""
    mana_ratios = await asyncio.gather(*[c.calc_mana_ratio() for c in clients])
    if any(ratio <= 0.0 for ratio in mana_ratios):
        machine.log("FAILURE: one or more clients have no mana to mark location", logging.WARNING)
        return None
    positions = await asyncio.gather(*[c.body.position() for c in clients])
    machine.log("Marking every client's location before the trip to the Scales…", logging.DEBUG)
    marked = await asyncio.gather(*[mark_location.mark_client(machine, c) for c in clients])
    if not all(marked):
        failed = [i + 1 for i, ok in enumerate(marked) if not ok]
        machine.log(
            f"FAILURE: client(s) {failed} could not mark their location (some zones, such as a house, refuse marks)",
            logging.WARNING,
        )
        return None
    return [Mark(zone, position) for zone, position in zip(zones, positions)]


def _point(xyz: XYZ) -> dict:
    return {"x": xyz.x, "y": xyz.y, "z": xyz.z}


async def _travel(machine, clients: list, entry: scales.Scales, trip: Trip) -> bool:
    """Take every client to the Scales' zone. False, the failing node having logged why."""
    if trip.navigate:
        outcome = await zone_navigation.navigate(machine, clients, entry.entrance_zone)
        if not outcome.success:
            return False
    for step in trip.steps:
        points = {"reset_xyz": _point(step.reset_xyz)}
        if step.kind == scales.SIGIL:
            # No offers_* params: the sigil is entered in normal mode, never Team-Up or Challenge Mode.
            result = await enter_dungeon.run(
                machine,
                clients,
                {"from_zone": step.from_zone, "dungeon_zone": step.to_zone, "sigil_xyz": _point(step.xyz), **points},
            )
        else:
            result = await enter_zone.run(
                machine,
                clients,
                {"from_zone": step.from_zone, "to_zone": step.to_zone, "xyz": _point(step.xyz), **points},
            )
        if result != "SUCCESS":
            return False
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    if not all(zone and node_helpers.zone_matches(zone, entry.zone) for zone in zones):
        machine.log(
            f"FAILURE: not every client is in the Scales' zone (expected={entry.zone!r}, got={list(zones)})",
            logging.WARNING,
        )
        return False
    return True


async def _return_to_marks(machine, clients: list, marks: list[Mark]) -> str:
    if not await teleport_to_marked_location.recall(machine, clients):
        return "FAILURE"
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    positions = await asyncio.gather(*[c.body.position() for c in clients])
    if missed := recall_mismatches(marks, zones, positions):
        machine.log(
            f"FAILURE: client(s) {missed} did not land where they marked (margin={POSITION_MARGIN})",
            logging.WARNING,
        )
        return "FAILURE"
    machine.log("SUCCESS: level scaling applied and every client is back on its mark")
    return "SUCCESS"


# ---------------------------------------------------------------------------
# The elixir
# ---------------------------------------------------------------------------


async def apply_elixir(
    machine, numbered: list[tuple[int, object]], interact: XYZ, reset: XYZ, safety_delay: float
) -> bool:
    """Give the elixir to each ``(player number, client)`` at the Scales, with the retry rounds.

    Every client must already be in the Scales' zone. Returns False having logged who still
    lacks the elixir. run() calls this with only the players that lack one; the old
    apply_level_scaling node calls it with every Level Scaling Player.
    """
    # Clear any pre-existing interact prompt on the scaled clients.
    await node_helpers.clear_stale_prompt(
        machine,
        [c for _, c in numbered],
        reset,
        node_helpers.INTERACT_PROMPT,
        safety_delay,
        POLL_INTERVAL,
        numbers=[n for n, _ in numbered],
    )

    # Retry rounds over the clients that still need the elixir. One client uses the
    #    station from start to finish before the next one opens it: an open menu closes
    #    on every client that has not clicked yet as soon as one of them does, so the
    #    whole interaction has to be sequential, not just the clicks.
    pending = list(numbered)
    for attempt in range(1, MAX_RETRIES + 1):
        machine.log(
            f"Attempt {attempt}/{MAX_RETRIES}: applying elixir to client(s) {[n for n, _ in pending]}",
            logging.DEBUG,
        )

        for position, (number, client) in enumerate(pending):
            if position:
                await asyncio.sleep(LEVEL_SCALING_STAGGER_DELAY)
            await _use_station(machine, client, number, interact, safety_delay)

        # Give the elixirs time to apply server-side before reading the indicator.
        machine.log(f"Waiting {safety_delay}s for elixir to apply…", logging.DEBUG)
        await asyncio.sleep(safety_delay)

        machine.log("Verifying level-scaling elixir is active…", logging.DEBUG)
        elixir_results = await asyncio.gather(
            *[node_helpers.is_visible_by_path(c, LEVEL_SCALING_ELIXIR_PATH) for _, c in pending]
        )
        pending = [entry for entry, active in zip(pending, elixir_results) if not active]
        if not pending:
            machine.log("SUCCESS: level scaling applied")
            return True

        # Step out of NPC range so the next round's teleport re-triggers the prompt.
        machine.log(
            f"Elixir not active on client(s) {[n for n, _ in pending]}; resetting to retry",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(reset) for _, c in pending])

    machine.log(
        f"FAILURE: level-scaling elixir not active on client(s) {[n for n, _ in pending]} after {MAX_RETRIES} attempts",
        logging.WARNING,
    )
    return False


async def _use_station(machine, client, client_number: int, interact: XYZ, safety_delay: float) -> None:
    """Walk one client through a single use of the level-scaling station.

    Teleports to the NPC, presses X, clicks the elixir option, and waits for the menu to
    close again before returning, so no other client is sitting in the menu meanwhile.

    Reports problems and returns instead of raising — whether the elixir actually landed
    is decided by the caller's indicator check, which also drives the retry, so there is
    nothing useful to distinguish here between "the menu never opened" and "the click did
    not take".
    """
    await client.teleport(interact)

    machine.log(f"Client {client_number}: waiting for interact prompt…", logging.DEBUG)
    if not await node_helpers.poll_until(lambda: node_helpers.all_in_npc_range([client]), safety_delay, POLL_INTERVAL):
        machine.log(f"Client {client_number}: interact prompt did not appear", logging.DEBUG)
        return

    machine.log(f"Client {client_number}: pressing X to interact with level-scaling NPC", logging.DEBUG)
    await client.send_key(Keycode.X, 0.1)

    machine.log(f"Client {client_number}: waiting for NPCServicesWin menu…", logging.DEBUG)
    if not await node_helpers.poll_until(lambda: _all_menu_visible([client]), safety_delay, POLL_INTERVAL):
        machine.log(
            f"Client {client_number}: NPCServicesWin option button never appeared within {safety_delay}s",
            logging.DEBUG,
        )
        return

    machine.log(f"Client {client_number}: clicking level-scaling option button", logging.DEBUG)
    await node_helpers.click_by_path(client, LEVEL_SCALING_OPTION_BUTTON_PATH)

    machine.log(f"Client {client_number}: waiting for NPCServicesWin menu to dismiss…", logging.DEBUG)
    await node_helpers.poll_until(lambda: _none_menu_visible([client]), safety_delay, POLL_INTERVAL)


async def _all_menu_visible(clients: list) -> bool:
    """Return True when LEVEL_SCALING_OPTION_BUTTON_PATH is visible on all clients."""
    results = await asyncio.gather(
        *[node_helpers.is_visible_by_path(c, LEVEL_SCALING_OPTION_BUTTON_PATH) for c in clients]
    )
    return all(results)


async def _none_menu_visible(clients: list) -> bool:
    """Return True when LEVEL_SCALING_OPTION_BUTTON_PATH is not visible on any client."""
    results = await asyncio.gather(
        *[node_helpers.is_visible_by_path(c, LEVEL_SCALING_OPTION_BUTTON_PATH) for c in clients]
    )
    return not any(results)
