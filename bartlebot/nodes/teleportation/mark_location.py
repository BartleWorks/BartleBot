"""mark_location node — mark the current in-game location with failsafe handling."""

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum, auto

from bartleby.runtime.paths import MARK_BUTTON_PATH, RECALL_BUTTON_PATH, RECALL_TIMER_PATH
from bartleby.runtime.timing import COMPASS_TELEPORT_TIMEOUT, POLL_INTERVAL
from wizwalker import XYZ

from bartlebot.nodes import node_helpers
from bartlebot.nodes.help_text import ZONE_AT_START
from bartlebot.nodes.node_helpers import POSITION_MARGIN
from bartlebot.nodes.param_schema import SAFETY_DELAY_OPTION, NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="zone",
        label="Zone",
        help=ZONE_AT_START,
        default="",
        required=True,
    ),
    ParamField(
        key="xyz",
        type="xyz",
        label="Mark XYZ",
        help="Where to mark. Players elsewhere in Zone teleport here first.",
        default=None,
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Marks every player's location. A player who already marked this spot in this run is left "
        "alone, so a loop can pass through the node every time."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Every player has a mark here, set now or earlier in this run."),
        ReturnSpec(
            "FAILURE",
            (
                "A player wasn't in Zone, had no mana, was in a fight or a zone that refuses marks, or "
                "the game didn't answer within Safety Delay."
            ),
        ),
    ],
    required_bot_options=[SAFETY_DELAY_OPTION],
    max_consecutive_retries=5,
    usage_notes=(
        (
            "Marking costs mana, so a player with no mana can't mark. If recall is on cooldown, the "
            "node waits for it first."
        )
        + "\n\n"
        + (
            "Each player waits Safety Delay before marking, so the game knows where the player now "
            "stands. A mark set sooner can land where the player stood before the last teleport. Raise "
            "Safety Delay on a slow connection if marks fail or a recall lands somewhere else."
        )
    ),
)

#: Where each client last marked in this run, in ``machine.node_scratch``. Every mark goes
#: through :func:`mark_client`, handle_level_scaling's included, so the record never names a
#: mark that has since been replaced. A recall that lands elsewhere drops it (:func:`forget`):
#: the record is the client's own position, which cannot show where the game put the mark.
MARKS_KEY = "marks"


@dataclass(frozen=True)
class Mark:
    """Where one client stood when it marked: what its recall is checked against."""

    zone: str
    position: XYZ


def marked_at(machine, client) -> Mark | None:
    """Where `client` last marked in this run, or None if it has not."""
    for marked, mark in machine.node_scratch.get(MARKS_KEY, ()):
        if marked is client:
            return mark
    return None


def forget(machine, client) -> None:
    """Drop `client` from the run's record of marks, so the next Mark Location marks it again.

    For a recall that did not bring `client` back to its mark. The game set that mark where the
    server last had the client, and the record holds where the client itself stood.
    """
    if marks := machine.node_scratch.get(MARKS_KEY):
        machine.node_scratch[MARKS_KEY] = [(c, m) for c, m in marks if c is not client]


def _remember(machine, client, mark: Mark) -> None:
    forget(machine, client)
    machine.node_scratch[MARKS_KEY] = [*machine.node_scratch.get(MARKS_KEY, ()), (client, mark)]


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.stats.current_mana(),
          client.body.position(), node_helpers.click_by_path()

    Flow:
      1. Leave out every client that already marked this spot in this run and still has a
         mark (its RecallButton is enabled, or grayed only by recall's cooldown). SUCCESS at
         once if that is every client.
      2. Verify the rest can mark: MarkButton enabled. Return FAILURE if any cannot.
      3. Verify all clients are in zone. Return FAILURE if not.
      4. Teleport any of the rest not already at xyz to the mark position.
      5. Mark each of the rest (:func:`mark_client`), which gives the server Safety Delay to
         hear where the client now stands, reads whether it had a mark before clicking, and
         re-marks only once it has seen the click clear one.

    FAILURE: any client left to mark cannot; clients not in zone; a mark was not confirmed.
    """
    zone: str = params["zone"]
    xyz_data: dict = params["xyz"]
    target = XYZ(xyz_data["x"], xyz_data["y"], xyz_data["z"])

    machine.log(f"mark_location: zone={zone!r} XYZ({target.x:.1f}, {target.y:.1f}, {target.z:.1f})")

    # 1. A mark set here earlier in the run stands: a recall does not use it up.
    kept = await asyncio.gather(*[_still_marked_here(machine, c, zone, target) for c in clients])
    to_mark = [c for c, ok in zip(clients, kept) if not ok]
    if not to_mark:
        machine.log("SUCCESS: every client already marked this spot in this run")
        return "SUCCESS"

    # 2. Verify the clients that will mark can: the game grays MarkButton when they cannot.
    can_mark = await asyncio.gather(*[node_helpers.is_enabled_by_path(c, MARK_BUTTON_PATH) for c in to_mark])
    if not all(can_mark):
        machine.log(f"FAILURE: {_CANNOT_MARK}", logging.WARNING)
        return "FAILURE"

    # 3. Verify zone; teleport to xyz if needed.
    if not await node_helpers.in_expected_zone(machine, clients, zone, POLL_INTERVAL):
        return "FAILURE"

    positions = await asyncio.gather(*[c.body.position() for c in to_mark])
    away = [c for c, p in zip(to_mark, positions) if _distance(p, target) > 1.0]
    if away:
        machine.log(
            f"Teleporting clients to mark XYZ({target.x:.1f}, {target.y:.1f}, {target.z:.1f})",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(target) for c in away])

    # 5. Mark each client, handling the same-spot edge case. mark_client waits for the server
    #    to catch up with the teleport before it clicks.
    results = await asyncio.gather(*[mark_client(machine, c) for c in to_mark])
    if not all(results):
        machine.log("FAILURE: one or more clients failed to set a mark", logging.WARNING)
        return "FAILURE"

    machine.log("SUCCESS: all clients have marked their location")
    return "SUCCESS"


class _Click(Enum):
    """What one click of MarkButton did, read from mana and the RecallButton."""

    SET = auto()  # no mark before, one now
    MOVED = auto()  # a mark elsewhere before, here now
    CLEARED = auto()  # stood on the mark: the click removed it
    UNSET = auto()  # mana spent, but the RecallButton never lit
    NO_RESPONSE = auto()  # mana unchanged: the click did not land


_CANNOT_MARK = "MarkButton grayed: no mana, in combat, or a zone that refuses marks (a house)"


async def mark_client(machine, client) -> bool:
    """Mark location for a single client, where it stands. Returns True if mark was confirmed set.

    Public: handle_level_scaling marks each client wherever it is, with no recorded spot. A mark
    that is set is remembered for the rest of the run (:func:`marked_at`).

    The game sets a mark where the *server* last had the client, and a teleport reaches the
    server a moment after the client has moved, so the first click waits until Safety Delay has
    passed since this began. Nothing readable says when the server has caught up, and the
    client may have been teleported by the node before, so the wait is never skipped. A click
    too soon sets the mark where the client stood before the teleport, and nothing here can see
    that: only a recall shows it.

    Every mark and every clear costs mana, so a drop in mana is the sign a click landed. Whether
    the client had a mark before the click says which change to look for: a client standing on
    its mark clears it, and only a clear that has been seen earns a second click. A click is
    never repeated on a guess, because a late one would toggle the mark again.
    """
    if not await node_helpers.is_enabled_by_path(client, MARK_BUTTON_PATH):
        machine.log(_CANNOT_MARK, logging.WARNING)
        return False
    safety_delay: float = machine.get_option("Safety Delay", 3.0)
    loop = asyncio.get_running_loop()
    caught_up_at = loop.time() + safety_delay

    # Recall's cooldown, when there is one, counts toward the wait.
    had_mark = await _had_mark(machine, client, safety_delay)
    if had_mark is None:
        return False
    if (remaining := caught_up_at - loop.time()) > 0:
        machine.log(f"Giving the server {remaining:.1f}s to catch up with this player before marking…", logging.DEBUG)
        await asyncio.sleep(remaining)
    click = await _click_mark(client, had_mark, safety_delay)
    if click is _Click.CLEARED:
        machine.log("Marking cleared the mark this player stood on; marking again…", logging.DEBUG)
        click = await _click_mark(client, False, safety_delay)

    if click is _Click.NO_RESPONSE:
        machine.log(f"Mana did not drop within {safety_delay}s of marking: the click did not land", logging.WARNING)
        return False
    if click is _Click.UNSET:
        machine.log(f"RecallButton stayed grayed for {safety_delay}s after marking: no mark was set", logging.WARNING)
        return False

    # A mark kept with no zone could never be matched by a recall, so the zone is waited for.
    (zone,), position = await asyncio.gather(
        node_helpers.read_zones([client], poll_interval=POLL_INTERVAL), client.body.position()
    )
    if not zone:
        machine.log("The mark was set, but this player's zone could not be read to remember it by", logging.WARNING)
        return False
    _remember(machine, client, Mark(zone, position))
    return True


async def _had_mark(machine, client, safety_delay: float) -> bool | None:
    """Whether `client` has a mark anywhere, waiting out recall's cooldown to see. None if it never ends.

    The RecallButton is grayed while its timer runs, mark or no mark, so it says nothing until
    the timer is gone. Grayed with no timer is no mark, or a state recall is barred in; both
    are read as no mark, and the click after shows which.
    """
    if await node_helpers.is_visible_by_path(client, RECALL_TIMER_PATH):
        machine.log("Recall is on cooldown; waiting for it to end before marking…", logging.DEBUG)
        if not await node_helpers.poll_until(
            lambda: _recall_timer_hidden(client), COMPASS_TELEPORT_TIMEOUT, POLL_INTERVAL
        ):
            machine.log(f"Recall's cooldown did not end within {COMPASS_TELEPORT_TIMEOUT}s", logging.WARNING)
            return None
        # The button can light a moment after its timer goes.
        return await node_helpers.poll_until(lambda: _recall_enabled(client), safety_delay, POLL_INTERVAL)
    return await _recall_enabled(client)


async def _click_mark(client, had_mark: bool, safety_delay: float) -> _Click:
    """Click MarkButton once and wait, up to `safety_delay` per step, for the game to answer."""
    mana = await client.stats.current_mana()
    await node_helpers.click_by_path(client, MARK_BUTTON_PATH)
    if not await node_helpers.poll_until(lambda: _mana_below(client, mana), safety_delay, POLL_INTERVAL):
        return _Click.NO_RESPONSE
    if had_mark:
        # A clear and a move both spend mana and differ only in the RecallButton graying, which
        # can trail the mana by a moment, so a move is known only once the whole window passes.
        if await node_helpers.poll_until(lambda: _recall_grayed(client), safety_delay, POLL_INTERVAL):
            return _Click.CLEARED
        return _Click.MOVED
    if await node_helpers.poll_until(lambda: _recall_enabled(client), safety_delay, POLL_INTERVAL):
        return _Click.SET
    return _Click.UNSET


async def _mana_below(client, mana: int) -> bool:
    return await client.stats.current_mana() < mana


async def _recall_enabled(client) -> bool:
    return await node_helpers.is_enabled_by_path(client, RECALL_BUTTON_PATH)


async def _recall_grayed(client) -> bool:
    return not await _recall_enabled(client)


async def _recall_timer_hidden(client) -> bool:
    return not await node_helpers.is_visible_by_path(client, RECALL_TIMER_PATH)


async def _still_marked_here(machine, client, zone: str, target: XYZ) -> bool:
    """Whether `client` marked this spot earlier in the run and the mark is still there."""
    mark = marked_at(machine, client)
    if mark is None or not node_helpers.zone_matches(mark.zone, zone):
        return False
    if _distance(mark.position, target) > POSITION_MARGIN:
        return False
    # Cleared by hand, or by a same-spot click outside this run's record. A running recall
    # timer grays the button too, but only because a recall just used the mark, which keeps it.
    return await _recall_enabled(client) or await node_helpers.is_visible_by_path(client, RECALL_TIMER_PATH)


def _distance(a: XYZ, b: XYZ) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2) ** 0.5
