"""Node: unlock a skeleton key door with one player's key, and take every player through it."""

import asyncio
import logging
from dataclasses import dataclass

from bartleby.runtime.clients import game_keys
from bartleby.runtime.game.key_bindings import GameAction
from bartleby.runtime.timing import (
    DIALOG_TIMEOUT,
    KEY_DOOR_ENTRY_TIMEOUT,
    KEY_DOOR_PROMPT_SETTLE,
    KEY_DOOR_RETRY_INTERVAL,
    KEY_DOOR_TELEPORT_CUTOFF,
    MAX_RETRIES,
    POLL_INTERVAL,
    TRIGGER_TIMEOUT,
)
from wizwalker import XYZ

from bartlebot.nodes import node_helpers, walking
from bartlebot.nodes.help_text import (
    ARRIVAL_NOTE,
    INTERACT_XYZ,
    RESET_XYZ,
    TRANSITION_XYZ,
    ZONE_AT_END,
    ZONE_AT_START,
)
from bartlebot.nodes.param_schema import SAFETY_DELAY_OPTION, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.zone_entry import enter_zone
from bartlebot.options import player_realms

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="from_zone",
        type="zone",
        label="From Zone",
        help=ZONE_AT_START,
        default="",
        required=True,
    ),
    ParamField(
        key="to_zone",
        type="zone",
        label="To Zone",
        help=ZONE_AT_END,
        default="",
        required=True,
    ),
    ParamField(
        key="interact_xyz",
        type="xyz",
        label="Interact XYZ",
        help=INTERACT_XYZ,
        default=None,
        required=True,
    ),
    ParamField(
        key="xyz",
        type="xyz",
        label="Transition XYZ",
        help=TRANSITION_XYZ,
        default=None,
        required=True,
    ),
    ParamField(
        key="reset_xyz",
        type="xyz",
        label="Reset XYZ",
        help=RESET_XYZ,
        default=None,
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description="Unlocks a skeleton key door with one player's key and takes every player through it.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Every player is in To Zone."),
        ReturnSpec("NO_KEY", "No player got the interact prompt, and the door isn't open."),
        ReturnSpec(
            "FAILURE",
            "A player was in neither zone, the prompt stayed after X, or not every player reached To Zone.",
        ),
    ],
    required_bot_options=[
        player_realms.SPEC,
        SAFETY_DELAY_OPTION,
        walking.AVOID_MOBS_OPTION,
    ],
    merges_players=True,
    max_consecutive_retries=5,
    usage_notes=(
        (
            "Use it for a skeleton key door, in a skeleton key boss dungeon or a side room of an ordinary "
            "one. No Enter Zone follows it: it takes every player through.\n\n"
            "The lowest-numbered player with a key presses X, so one player's keys run out before the "
            "next player's are used. A player without a key gets no prompt and is skipped. One key opens "
            "the door for everyone in the dungeon, so the players must be on one realm.\n\n"
            "With four players, the one who used the key goes through first and the rest follow once it "
            "has loaded, so all four end up on one realm. With fewer, they all go together.\n\n"
            "Give NO_KEY a route or a stop: it's the one failure that trying again won't fix, and it "
            "doesn't go to the recovery state by itself. If the door is already open, the node spends no "
            "key and takes everyone through. Keep Reset XYZ clear of the door's trigger too."
        )
        + "\n\n"
        + ARRIVAL_NOTE
    ),
    see_also=["interact", "enter_zone"],
)


@dataclass(frozen=True)
class _Door:
    """What every step of the node reads."""

    machine: object
    clients: list
    from_zone: str
    to_zone: str
    trigger: XYZ
    reset: XYZ
    safety_delay: float

    def numbers(self, group: list) -> list[int]:
        return [i + 1 for i, c in enumerate(self.clients) if any(c is member for member in group)]

    async def on_its_way(self, client) -> bool:
        """Whether *client* has started loading, or has landed in To Zone."""
        return await client.is_loading() or node_helpers.zone_matches(await client.zone_name(), self.to_zone)


async def _lowest_prompted(door: _Door) -> tuple[object | None, list]:
    """The client who should use a key, and everyone showing the prompt.

    The first prompt up is whoever's rendered first, so the choice waits a moment for the
    lowest-numbered player's. That player having it ends the wait: nobody can outrank them.
    """

    async def _first_has_it() -> bool:
        return await door.clients[0].is_in_npc_range()

    await node_helpers.poll_until(_first_has_it, KEY_DOOR_PROMPT_SETTLE, POLL_INTERVAL)
    showing = await asyncio.gather(*[c.is_in_npc_range() for c in door.clients])
    prompted = [c for c, shows in zip(door.clients, showing) if shows]
    return (prompted[0] if prompted else None), prompted


async def _press_until_open(unlocker, prompted: list) -> float | None:
    """Press X on *unlocker* until a prompt goes, and return when that press was sent.

    One press has nothing behind it if the game drops it, so it is repeated while every
    prompt stays. The door opening takes the prompt within a moment, so a repeat is never a
    second key. The carry-in counts from the press that worked, which is the last one. None
    if a prompt never went within TRIGGER_TIMEOUT.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + TRIGGER_TIMEOUT

    async def _a_prompt_went() -> bool:
        return not await node_helpers.all_in_npc_range(prompted)

    while True:
        pressed_at = loop.time()
        await game_keys.press(unlocker, GameAction.INTERACT, 0.1)
        wait = min(KEY_DOOR_RETRY_INTERVAL, max(0.0, deadline - loop.time()))
        if await node_helpers.poll_until(_a_prompt_went, wait, POLL_INTERVAL):
            return pressed_at
        if loop.time() >= deadline:
            return None


async def _enter_unlocker(door: _Door, unlocker, pressed_at: float) -> bool:
    """Get the player who used the key into To Zone, beside the game's own carry-in.

    Left alone they are carried in about 7 s after the press. So this waits Safety Delay, up
    to KEY_DOOR_TELEPORT_CUTOFF, for the door to take them, and then walks them into its
    trigger once, which is quicker than the carry-in. The walk ends when the zone changes,
    whichever of the two moved them. It used to be a teleport, kept clear of the carry-in
    since one that crossed it was applied once they landed and put them back out (#284). A
    walk is keys, not a position write the game holds, so it cannot land late; the cutoff
    stays only as the wait's bound. A walk that fails is logged and the carry-in waited for,
    since that needs no walk. Past KEY_DOOR_ENTRY_TIMEOUT no carry-in is coming, and a normal
    attempt at the trigger is made.
    """
    loop = asyncio.get_running_loop()
    machine = door.machine
    number = door.numbers([unlocker])[0]

    def left_until(seconds: float) -> float:
        return max(0.0, pressed_at + seconds - loop.time())

    async def _on_its_way() -> bool:
        return await door.on_its_way(unlocker)

    going = await node_helpers.poll_until(
        _on_its_way, left_until(min(door.safety_delay, KEY_DOOR_TELEPORT_CUTOFF)), POLL_INTERVAL
    )
    if not going:
        machine.log(f"Walking P{number} into the door's trigger", logging.DEBUG)
        (walked,) = await walking.walk_clients(machine, [unlocker], door.trigger, numbers=[number])
        if not walked.ok:
            machine.log(
                f"P{number} could not walk into the door's trigger; waiting for the game to carry them", logging.INFO
            )
        going = await _on_its_way()

    if not going:
        machine.log(f"Leaving P{number} for the game to carry through the door", logging.DEBUG)
        going = await node_helpers.poll_until(_on_its_way, left_until(KEY_DOOR_ENTRY_TIMEOUT), POLL_INTERVAL)

    if not going:
        machine.log(f"P{number} was not carried through the door; trying the trigger", logging.INFO)
        if not await _fire_trigger(door, [unlocker]):
            return False
    return await enter_zone.await_arrival(machine, [unlocker], [number], door.to_zone)


async def _fire_trigger(door: _Door, group: list) -> bool:
    if not group:
        return True
    return await enter_zone.fire_trigger(
        door.machine,
        group,
        door.numbers(group),
        door.from_zone,
        door.to_zone,
        door.trigger,
        [door.reset] * len(group),
    )


async def _clear_dialogue(door: _Door, group: list) -> bool:
    """Click through anything the door said to players who are staying put for now.

    Not the unlocker, who is about to load: a key press at a client on a loading screen does
    nothing useful, and the run's ambient dialog watcher has them covered.
    """
    if not group:
        return True
    machine = door.machine
    machine.log("Advancing dialogue…", logging.DEBUG)
    if await node_helpers.advance_dialogue_until_clear(group, door.safety_delay, DIALOG_TIMEOUT):
        return True
    machine.log(f"FAILURE: dialogue did not clear within {DIALOG_TIMEOUT}s", logging.WARNING)
    return False


async def _enter_after_unlock(door: _Door, unlocker, pressed_at: float) -> bool:
    """The unlocker, then the rest: one after the other with four players, together with fewer."""
    rest = [c for c in door.clients if c is not unlocker]

    if len(door.clients) == 4:
        entered, cleared = await asyncio.gather(
            _enter_unlocker(door, unlocker, pressed_at),
            _clear_dialogue(door, rest),
        )
        return entered and cleared and await _fire_trigger(door, rest)

    async def _the_rest_go() -> bool:
        return await _clear_dialogue(door, rest) and await _fire_trigger(door, rest)

    entered, fired = await asyncio.gather(_enter_unlocker(door, unlocker, pressed_at), _the_rest_go())
    return entered and fired


async def _unlock_and_enter(door: _Door, interact: XYZ) -> str:
    """Everyone is at the door's side of it. Open it, or find it open, and start them through."""
    machine = door.machine
    clients = door.clients

    if not await node_helpers.clear_stale_prompt(
        machine, clients, door.reset, node_helpers.INTERACT_PROMPT, door.safety_delay, POLL_INTERVAL
    ):
        return "FAILURE"
    # A miss is the node's own outcome to word, so the helper is told not to log one.
    has_prompt = await node_helpers.reach_prompt(
        machine,
        clients,
        interact,
        door.reset,
        node_helpers.INTERACT_PROMPT,
        door.safety_delay,
        POLL_INTERVAL,
        fail_on_miss=False,
        clear_first=False,
        any_client=True,
    )

    # As every zone-entry node does. A bot holding this node cannot be Separate, so a run has
    # nothing to note; it stays for a caller that reaches the node some other way.
    await node_helpers.note_realm_peers(machine, clients)

    if not has_prompt:
        # No key and an open door both show no prompt. Only the trigger tells them apart: this
        # is a rerun, after a fight, of a node that already got the door open.
        first = clients[0]
        machine.log(f"No player got the interact prompt after {MAX_RETRIES} attempts; trying the trigger", logging.INFO)
        (walked,) = await walking.walk_clients(machine, [first], door.trigger, numbers=[1])
        if not walked.ok and not await door.on_its_way(first):
            # Logged with where it ended. Whether the door is open is still unknown, so not NO_KEY.
            machine.log("FAILURE: could not walk to the door's trigger to see whether it is open", logging.WARNING)
            return "FAILURE"

        async def _first_is_going() -> bool:
            return await door.on_its_way(first)

        wait = max(door.safety_delay, KEY_DOOR_RETRY_INTERVAL)
        if not await node_helpers.poll_until(_first_is_going, wait, POLL_INTERVAL):
            machine.log(
                f"NO_KEY: no player got the interact prompt after {MAX_RETRIES} attempts, "
                "and the door's trigger did nothing",
                logging.WARNING,
            )
            return "NO_KEY"
        machine.log("The door is already open; no key needed", logging.INFO)
        if not await enter_zone.await_arrival(machine, [first], [1], door.to_zone):
            return "FAILURE"
        rest = clients[1:]
        if not (await _clear_dialogue(door, rest) and await _fire_trigger(door, rest)):
            return "FAILURE"
        return "SUCCESS"

    unlocker, prompted = await _lowest_prompted(door)
    if unlocker is None:
        # The prompt showed and went again before it was read. Nothing has pressed X, so the
        # door is not known to be open; fail and let the route decide.
        machine.log("FAILURE: the interact prompt went away before X was pressed", logging.WARNING)
        return "FAILURE"

    machine.log(f"P{door.numbers([unlocker])[0]} uses a key on the door", logging.INFO)
    pressed_at = await _press_until_open(unlocker, prompted)
    if pressed_at is None:
        machine.log(f"FAILURE: the interact prompt stayed {TRIGGER_TIMEOUT:.0f}s after X", logging.WARNING)
        return "FAILURE"
    return "SUCCESS" if await _enter_after_unlock(door, unlocker, pressed_at) else "FAILURE"


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), walking.walk_clients(), client.is_in_npc_range(), client.is_loading(),
          game_keys.press(client, GameAction.INTERACT)

    Flow:
      1. See where everyone is. All in to_zone is done. Some in to_zone means the door is open
         and the node is running again, so the rest go. Anyone in neither zone fails.
      2. Clear any stale prompt at reset_xyz, then walk everyone to interact_xyz until the
         prompt shows on someone. A player with no key never gets it, so waiting for every
         client would cost each attempt its full Safety Delay.
      3. No prompt: try the trigger with the first player. The door is open if it fires,
         otherwise nobody has a key. Return NO_KEY.
      4. Press X with the lowest-numbered player who has the prompt, again if it stays.
      5. Wait Safety Delay, then enter: the unlocker first with four players and the rest once
         it has landed, everyone together with fewer. Only the unlocker's walk is on the
         clock from the press; see _enter_unlocker.
      6. Wait for every player to be in to_zone, with straggler recovery to anyone inside.

    FAILURE: a client in neither zone; a stale prompt would not clear; a walk failed; the prompt
    went before X, or stayed after it; dialogue did not clear within DIALOG_TIMEOUT; not every
    client reached to_zone. NO_KEY: step 3.
    """
    from_zone: str = params["from_zone"]
    to_zone: str = params["to_zone"]
    interact = XYZ(**params["interact_xyz"])
    door = _Door(
        machine=machine,
        clients=clients,
        from_zone=from_zone,
        to_zone=to_zone,
        trigger=XYZ(**params["xyz"]),
        reset=XYZ(**params["reset_xyz"]),
        safety_delay=machine.get_option("Safety Delay", 3.0),
    )

    machine.log(
        f"open_skeleton_key_door: {from_zone!r} → {to_zone!r} XYZ({interact.x:.1f}, {interact.y:.1f}, {interact.z:.1f})"
    )

    # 1. A rerun after a fight can find the door open and someone already through it.
    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL)
    zones = await node_helpers.read_zones(clients, poll_interval=POLL_INTERVAL)
    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    inside = [node_helpers.zone_matches(z, to_zone) for z in zones]
    wrong = [z for z, here in zip(zones, inside) if not here and not node_helpers.zone_matches(z, from_zone)]
    if wrong:
        machine.log(
            f"FAILURE: clients not in expected zone (expected={from_zone!r} or {to_zone!r}, unexpected={wrong})",
            logging.WARNING,
        )
        return "FAILURE"
    if all(inside):
        machine.log(f"SUCCESS: all clients already in {to_zone!r}")
        return "SUCCESS"

    numbers = list(range(1, len(clients) + 1))
    watcher = asyncio.create_task(enter_zone.watch_group(machine, clients, numbers))
    try:
        if any(inside):
            machine.log("Someone is already through the door; sending the rest", logging.INFO)
            rest = [c for c, here in zip(clients, inside) if not here]
            result = "SUCCESS" if await _fire_trigger(door, rest) else "FAILURE"
        else:
            result = await _unlock_and_enter(door, interact)
        if result == "SUCCESS" and not await enter_zone.await_arrival(machine, clients, numbers, to_zone):
            result = "FAILURE"
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)

    if result == "SUCCESS":
        machine.log(f"SUCCESS: all clients in {to_zone!r}")
    return result
