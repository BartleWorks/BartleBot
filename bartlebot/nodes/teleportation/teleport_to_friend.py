"""teleport_to_friend node — teleport follower clients to a hooked player or a named friend."""

import asyncio
import logging
from dataclasses import dataclass

from bartlebot import dialog_captions, wizard_names
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import POLL_INTERVAL, TRIGGER_TIMEOUT, ZONE_CHANGE_TIMEOUT

logger = logging.getLogger(__name__)

APP_SETTING_KEY = "default_friend_name"

#: The bot option holding the friend to port to. A **bot option**, not a param, and
#: one no bot file may carry a value for: a character name in a shared file is both a
#: privacy leak and a bot only its author can run.  The declaration is null
#: everywhere, so the app setting answers by default and a per-bot override sits above
#: it -- one idler for most bots, a different one for the odd bot that needs it.
OPTION_FRIEND_NAME = "Friend Name"

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="target_hooked_player",
        type="bool",
        label="Teleport to a Hooked Player",
        help=(
            "On: port to one of this bot's own players, whose wizard name is read from the game at run "
            "time. Off: port to the Friend Name bot option, which you set in Setup."
        ),
        default=True,
        required=False,
    ),
    ParamField(
        key="target_player",
        type="player_slot",
        label="Target Player",
        help=(
            "Which of this bot's players the others port to. That player must already be at the "
            "destination, and is never asked to port to itself. Both accounts still have to be in-game "
            "friends — this reads the friends list, it does not port through the party."
        ),
        default=0,
        enabled_by="target_hooked_player",
        required=False,
    ),
    ParamField(
        key="to_zone",
        type="str",
        label="Destination Zone",
        help="Expected zone name after teleport. Players already in this zone are skipped. Used to verify arrival.",
        default="",
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Teleports one or more players to a target using the in-game friends list. The target is "
        "either one of this bot's own players — its wizard name is read from the game, so nothing "
        "is stored — or the Friend Name option, valued on this machine. Players already in the "
        "destination zone are skipped. Handles the friend-busy error dialog and verifies arrival zone."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players are now in to_zone."),
        ReturnSpec(
            "FAILURE",
            "No teleport target could be resolved (no friend name configured, or the target "
            "player's wizard name has not been detected yet); friend not found or not online; "
            "the friend-busy dialog appeared (friend has teleports off, or is somewhere you "
            "cannot follow them into); players did not enter a loading screen within the trigger "
            "timeout; or not all players arrived in to_zone after loading.",
        ),
    ],
    required_bot_options=[
        BotOptionSpec(
            name=OPTION_FRIEND_NAME,
            type="friend_name",
            # Null, always: the author has no opinion about whose wizard you port to,
            # and cannot have one. The level below is your app setting.
            default=None,
            description=(
                "Wizard name to search for in the friends list (case-insensitive). Must be online "
                "and on each player's friends list. This is your value, not the bot's \u2014 it is "
                "stored on this machine and never written to a bot file. Only used when the node "
                "is not targeting one of this bot's own players."
            ),
        ),
    ],
    max_consecutive_retries=5,
    usage_notes=(
        "Both accounts must be in-game friends even in hooked-player mode: this drives the friends "
        "list, it does not port through the party. The target player is expected to already be at "
        "the destination and is never asked to port to itself. "
        "In friend-name mode the name is a bot option valued on this machine, not in the bot file, so a bot can be "
        "shared without carrying anyone's character name and each user points it at their own idler."
    ),
    safety_warning=(
        "This bot teleports you to another wizard. In friend-name mode that wizard must be on "
        "every player's friends list and online — set the name here, in Setup, or in Settings "
        "if it is wrong."
    ),
)


@dataclass(frozen=True)
class TargetResolution:
    """Where this run's teleport target came from, or why there isn't one.

    Resolution happens in one place and says which level answered, the way
    resolve_combat's source_label does. The name itself is
    deliberately absent from *source*: it is logged at DEBUG only, because an INFO line ends
    up in log excerpts pasted into issues and a character name must not (CLAUDE.md, "No real
    player data").
    """

    name: str = ""
    source: str = ""
    target_index: int | None = None
    error: str = ""


def resolve_target(clients: list, params: dict, configured: str = "") -> TargetResolution:
    """Resolve the teleport target from the bot's params and the resolved Friend Name option.

    Hooked-player mode reads the target's wizard name from the cache ClientManager fills, so
    nothing name-shaped is stored anywhere. Friend-name mode uses *configured* -- the ``Friend
    Name`` option already resolved through override, app setting and node default -- and falls
    back to a literal in the bot file only for files written before that was disallowed.

    *configured* is passed in rather than read here so this stays pure: the caller holds the
    machine at run time and the settings in the editor, and this holds the precedence.

    ``target_hooked_player`` defaults to *False* here while the editor's ParamField defaults to
    True: a file with neither key predates this node's two modes, and its friend_name is the
    only target it has.
    """
    if params.get("target_hooked_player", False):
        slot = int(params.get("target_player") or 0)
        if not 1 <= slot <= len(clients):
            return TargetResolution(
                error=f"target player P{slot} is not connected ({len(clients)} client(s) running)"
                if slot
                else "no target player selected"
            )
        name = wizard_names.cached_name(clients[slot - 1])
        if not name:
            return TargetResolution(error=f"P{slot}'s wizard name has not been detected yet")
        return TargetResolution(name=name, source=f"hooked player P{slot}", target_index=slot - 1)

    legacy = str(params.get("friend_name") or "").strip()
    if legacy:
        logger.warning(
            "teleport_to_friend: this bot stores a friend name in its params, which is deprecated. "
            "Clear it and set Friend Name in app settings instead — bot files must not carry "
            "character names."
        )
        return TargetResolution(name=legacy, source="the bot file (deprecated)")

    configured = str(configured or "").strip()
    if not configured:
        return TargetResolution(
            error="no friend name configured — set Friend Name in Setup, in the pre-run warning, or in Settings → Bot"
        )
    return TargetResolution(name=configured, source="the Friend Name option")


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.is_loading(), node_helpers.port_to_friend()

    Flow:
      0. Resolve the teleport target: a hooked player's wizard name, or the Friend Name
         bot option. No target -> FAILURE.
      1. Check if all clients are already in to_zone. Return SUCCESS immediately if so.
      2. Filter to only clients not yet in to_zone, and drop the target itself — it is
         already where everyone is going, and cannot port to itself.
      3. node_helpers.port_to_friend(): initiate the teleport for all clients_to_move,
         answer modals, retry stragglers, and wait for each to enter a loading screen.
         ValueError (friend not found/offline), a refusal, or not every client loading
         within the timeout -> FAILURE.
      4. Wait for all loading screens to finish.
      5. Wait for all clients (including those already in to_zone) to arrive in to_zone.
         Return FAILURE if any are not.

    FAILURE: no target could be resolved; only the target itself is outside to_zone; friend
    not found or not online; friend-busy dialog appeared (friend has teleports off, or is
    somewhere unreachable); loading not detected within ZONE_CHANGE_TIMEOUT; not all clients
    arrived in to_zone.
    """
    to_zone: str = params["to_zone"]

    # 0. Resolve the target once, and say which level answered — never the name itself at
    #    INFO, which is what a user pastes into an issue.
    target = resolve_target(clients, params, machine.get_option(OPTION_FRIEND_NAME, ""))
    if target.error:
        machine.log(f"FAILURE: {target.error}", logging.WARNING)
        return "FAILURE"
    friend_name: str = target.name.lower()
    machine.log(f"teleport_to_friend: target from {target.source}, to_zone={to_zone!r}")
    machine.log(f"Resolved target name: {target.name!r}", logging.DEBUG)

    # 1. Early exit if all clients already in to_zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current zones: {list(zones)}", logging.DEBUG)
    if all(node_helpers.zone_matches(z, to_zone) for z in zones):
        machine.log(f"SUCCESS: all clients already in {to_zone!r}")
        return "SUCCESS"

    clients_to_move = [
        c
        for i, (c, z) in enumerate(zip(clients, zones))
        if not node_helpers.zone_matches(z, to_zone) and i != target.target_index
    ]
    if not clients_to_move:
        machine.log(f"FAILURE: only the target ({target.source}) is outside {to_zone!r}", logging.WARNING)
        return "FAILURE"
    machine.log(f"{len(clients_to_move)} of {len(clients)} client(s) will teleport", logging.DEBUG)

    # 2. Initiate the teleport and wait for it to trigger a loading screen.
    machine.log("Calling teleport_to_friend_from_list…", logging.DEBUG)
    try:
        port_result = await node_helpers.port_to_friend(
            clients_to_move,
            friend_name,
            trigger_timeout=TRIGGER_TIMEOUT,
            ceiling=ZONE_CHANGE_TIMEOUT,
            poll_interval=POLL_INTERVAL,
        )
    except ValueError as exc:
        machine.log(f"FAILURE: {exc}", logging.WARNING)
        return "FAILURE"

    if port_result.refusal:
        machine.log(
            f"FAILURE: client {port_result.refused_index} cannot reach the target ({target.source}) — "
            f"{dialog_captions.describe(port_result.refusal)}",
            logging.WARNING,
        )
        return "FAILURE"
    if len(port_result.seen_loading) < len(clients_to_move):
        not_triggered = [i for i in range(len(clients_to_move)) if i not in port_result.seen_loading]
        machine.log(f"Clients not loading at timeout: {not_triggered}", logging.DEBUG)
        machine.log(
            f"FAILURE: {len(not_triggered)} client(s) did not enter a loading screen within {ZONE_CHANGE_TIMEOUT:.0f}s",
            logging.WARNING,
        )
        return "FAILURE"

    # 3. Wait for all loading screens to finish.
    machine.log("Loading detected; waiting for screens to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients_to_move), None, POLL_INTERVAL)

    # 4. Verify all clients (including those already in to_zone) arrived.
    actual_zones = await node_helpers.wait_for_zone(clients, to_zone, ZONE_CHANGE_TIMEOUT, POLL_INTERVAL)
    if actual_zones is not None:
        machine.log(f"Zones after teleport: {actual_zones}", logging.DEBUG)
        machine.log(
            f"FAILURE: not all clients in expected zone (expected={to_zone!r}, got={list(set(actual_zones))})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients in {to_zone!r}")
    return "SUCCESS"
