"""teleport_to_friend node — teleport follower clients to a hooked player or a named friend."""

import asyncio
import logging
from dataclasses import dataclass

from bartleby.runtime.game import dialog_captions, wizard_names
from bartleby.runtime.timing import POLL_INTERVAL, TRIGGER_TIMEOUT, ZONE_CHANGE_TIMEOUT

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.options.option_groups import GROUP_TEAM

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
            "When on, players teleport to one of the bot's own players. When off, they teleport to "
            "the wizard in the Friend Name option."
        ),
        default=True,
        required=False,
    ),
    ParamField(
        key="target_player",
        type="player_slot",
        label="Target Player",
        help="Which of the bot's players the others teleport to. That player must already be at the destination.",
        default=0,
        enabled_by="target_hooked_player",
        required=False,
    ),
    ParamField(
        key="to_zone",
        type="zone",
        label="Destination Zone",
        help=(
            "Where the target should be. With a hooked player, the node fails before anyone teleports "
            "if the target is elsewhere. Leave it empty when you can't know, such as a friend's "
            "house."
        ),
        default="",
        required=False,
        default_hint="anywhere, as long as everyone ends up together",
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Teleports players through the friends list to one of the bot's own players, or to the "
        "wizard in the Friend Name option. Checks that everyone arrived."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec(
            "SUCCESS",
            "Every player is with the target, and in Destination Zone if it's set.",
        ),
        ReturnSpec(
            "FAILURE",
            (
                "The friend isn't online or has teleports off, no target is set or detected yet, a hooked "
                "target isn't in Destination Zone, a player didn't teleport, or not every player reached "
                "the target."
            ),
        ),
    ],
    merges_players=True,
    required_bot_options=[
        BotOptionSpec(
            name=OPTION_FRIEND_NAME,
            group=GROUP_TEAM,
            type="friend_name",
            # Null, always: the author has no opinion about whose wizard you port to,
            # and cannot have one. The level below is your app setting.
            default=None,
            # A hooked-player port never reads it, so a bot whose every port is to a
            # hooked player gets no Options row for a name it will not use.
            required_unless="target_hooked_player",
            description=(
                "The wizard to teleport to, when the node doesn't target one of the bot's own players. "
                "The wizard must be online and on every player's friends list, and the name is stored on "
                "this computer, never in the bot file."
            ),
        ),
    ],
    max_consecutive_retries=5,
    usage_notes=(
        (
            "Every account must be on the target's friends list, even when the target is one of the "
            "bot's own players. The node teleports through the friends list."
        )
        + "\n\n"
        + (
            "A teleport within the same zone and realm shows no loading screen, and one onto a player "
            "already beside you shows nothing. Both count as arrived."
        )
    ),
    safety_warning=(
        "This bot teleports your players to another wizard, who must be online and on every "
        "player's friends list. If the name is wrong, change it here, in this bot's Options, or "
        "in Settings › Bot."
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
            error="no friend name configured — set Friend Name in Options, in the pre-run warning, or in Settings → Bot"
        )
    return TargetResolution(name=configured, source="the Friend Name option")


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.is_loading(), client.body.position(),
          node_helpers.gather_at_player(), node_helpers.port_to_friend()

    Flow:
      0. Resolve the teleport target: a hooked player's wizard name, or the Friend Name
         bot option. No target -> FAILURE.
      1. Hooked player: its zone and position are readable, so the port is exact.
         a. If to_zone is set and the target is not in it, FAILURE before anyone ports.
         b. node_helpers.gather_at_player() ports everyone else onto it and confirms each
            is standing with it.
      2. Friend by name: nothing about the friend is readable, so every client ports.
         a. node_helpers.port_to_friend() accepts a loading screen or a jump in position,
            and — because any client might already be at the friend — presumes a client
            that shows nothing through one re-issue is already there, and logs it.
         b. Once loading clears, every client must be in one zone, and in to_zone if set.

    FAILURE: no target could be resolved; a hooked target is not in to_zone; friend not found
    or not online; friend-busy dialog appeared (friend has teleports off, or is somewhere
    unreachable); a client showed no sign of porting within ZONE_CHANGE_TIMEOUT; clients did
    not end up together (or in to_zone, when set).
    """
    to_zone: str = str(params.get("to_zone") or "").strip()

    # 0. Resolve the target once, and say which level answered — never the name itself, at
    #    any level: the session file keeps DEBUG too, and it is what a user pastes into an issue.
    target = resolve_target(clients, params, machine.get_option(OPTION_FRIEND_NAME, ""))
    if target.error:
        machine.log(f"FAILURE: {target.error}", logging.WARNING)
        return "FAILURE"
    machine.log(f"teleport_to_friend: target from {target.source}, to_zone={to_zone or '(not set)'!r}")

    if target.target_index is not None:
        return await _gather_at_hooked_player(machine, clients, target, to_zone)
    return await _port_to_named_friend(machine, clients, target, to_zone)


async def _gather_at_hooked_player(machine, clients: list, target: TargetResolution, to_zone: str) -> str:
    """Step 1 of run(): port everyone onto one of this bot's own players."""
    target_client = clients[target.target_index]

    # a. to_zone is a statement about where the target is, so check it before moving anyone:
    #    otherwise a target that is somewhere unexpected takes the whole group with it.
    if to_zone:
        await node_helpers.poll_until(
            lambda: node_helpers.none_loading([target_client]), ZONE_CHANGE_TIMEOUT, POLL_INTERVAL
        )
        (target_zone,) = await node_helpers.read_zones([target_client], poll_interval=POLL_INTERVAL)
        if not node_helpers.zone_matches(target_zone, to_zone):
            machine.log(
                f"FAILURE: the target ({target.source}) is in {target_zone!r}, not {to_zone!r}", logging.WARNING
            )
            return "FAILURE"

    # b. Port everyone else onto it and confirm they are standing with it.
    if not await node_helpers.gather_at_player(
        machine, clients, target.target_index, target.name, poll_interval=POLL_INTERVAL
    ):
        return "FAILURE"

    machine.log(f"SUCCESS: every player is with {target.source}")
    return "SUCCESS"


async def _port_to_named_friend(machine, clients: list, target: TargetResolution, to_zone: str) -> str:
    """Step 2 of run(): port everyone to a friend who is not one of this bot's players."""
    # Quiet clients are judged by their position, and a loading client reports the one it left.
    if not await node_helpers.poll_until(
        lambda: node_helpers.none_loading(clients), ZONE_CHANGE_TIMEOUT, POLL_INTERVAL
    ):
        machine.log("FAILURE: a player is still on a loading screen", logging.WARNING)
        return "FAILURE"

    # a. Any client might already be standing at the friend, and nothing says which.
    try:
        port_result = await node_helpers.port_to_friend(
            clients,
            target.name.lower(),
            trigger_timeout=TRIGGER_TIMEOUT,
            ceiling=ZONE_CHANGE_TIMEOUT,
            poll_interval=POLL_INTERVAL,
            maybe_there=range(len(clients)),
        )
    except ValueError:
        # The message names the friend it could not find, so it stays out of the log.
        machine.log(
            f"FAILURE: the target ({target.source}) is not on every player's online friends list", logging.WARNING
        )
        return "FAILURE"

    if port_result.refusal:
        machine.log(
            f"FAILURE: P{port_result.refused_index + 1} cannot reach the target ({target.source}) — "
            f"{dialog_captions.describe(port_result.refusal)}",
            logging.WARNING,
        )
        return "FAILURE"
    if missing := port_result.missing(len(clients)):
        machine.log(
            f"FAILURE: player(s) {[i + 1 for i in missing]} showed no sign of porting within "
            f"{ZONE_CHANGE_TIMEOUT:.0f}s",
            logging.WARNING,
        )
        return "FAILURE"
    for i in sorted(port_result.presumed):
        # The one case a failed port could hide in, so it is always visible after the fact.
        machine.log(f"P{i + 1} neither loaded nor moved; presumed already with the target ({target.source})")

    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL)

    # b. Nobody knows the friend's zone, but everyone who ported to them shares it.
    async def _together() -> bool:
        zones = await asyncio.gather(*[c.zone_name() for c in clients])
        # All None is one set too, and says nothing: a zone just off a loading screen is unread.
        if None in zones or len(set(zones)) != 1 or (to_zone and not node_helpers.zone_matches(zones[0], to_zone)):
            return False
        # One zone is not one realm. Everyone ported onto the friend, so everyone is standing
        # together, where seeing each other is the proof (#130).
        return (await node_helpers.read_sightings(clients, machine)).all_together()

    if not await node_helpers.poll_until(_together, ZONE_CHANGE_TIMEOUT, POLL_INTERVAL):
        zones = await asyncio.gather(*[c.zone_name() for c in clients])
        expected = f"all in {to_zone!r}, on one realm" if to_zone else "all in one zone, on one realm"
        machine.log(f"FAILURE: players did not end up together (expected {expected}, got {zones})", logging.WARNING)
        return "FAILURE"

    machine.log(f"SUCCESS: every player is with the target ({target.source})")
    return "SUCCESS"
