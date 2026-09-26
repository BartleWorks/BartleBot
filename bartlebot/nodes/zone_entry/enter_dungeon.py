"""Node: enter a dungeon via its sigil, alone or through the Team-Up queue."""

import asyncio
import logging

from wizwalker import XYZ, Keycode

from bartlebot import dialog_captions
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import (
    DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH,
    ENTER_TEAMUP_BUTTON_PATH,
    SIGIL_ENTRANCE_TITLE_PATH,
    SIGIL_MODE_TOGGLE_BUTTON_PATH,
    TEAMUP_2_PLAYER_CHECKBOX,
    TEAMUP_3_PLAYER_CHECKBOX,
    TEAMUP_4_PLAYER_CHECKBOX,
)

#: Option names, so the safety dialog and the tests name them from one place.
OPTION_TEAM_UP = "Team-Up"
OPTION_MINIMUM_TEAM_SIZE = "Minimum Team Size"

#: The param that says this sigil offers Team-Up at all.
PARAM_OFFERS_TEAM_UP = "offers_team_up"

#: The param holding the entrance prompt's title. Editor-only: see bartlebot/bot_summary.py.
PARAM_DUNGEON_NAME = "dungeon_name"

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="from_zone",
        type="zone",
        label="From Zone",
        help="Zone name players must currently be in when this node executes.",
        default="",
        required=True,
    ),
    ParamField(
        key="dungeon_zone",
        type="zone",
        label="Dungeon Zone",
        help="Zone name all players must be in after successfully entering the dungeon.",
        default="",
        required=True,
    ),
    ParamField(
        key=PARAM_DUNGEON_NAME,
        type="str",
        label="Dungeon Name",
        help=(
            "The dungeon's name as its entrance prompt shows it. Record fills it in. "
            "Nothing reads it while the bot runs: it names the dungeon in the bot's generated "
            "description, which the zone cannot always do, because many dungeons share their "
            "street's zone name."
        ),
        default="",
        default_hint="Empty — the generated description names the dungeon by its zone, if at all.",
    ),
    ParamField(
        key="sigil_xyz",
        type="xyz",
        label="Sigil XYZ",
        help=("Coordinates of the dungeon sigil. Standing here should trigger the dungeon entrance prompt."),
        default=None,
        required=True,
    ),
    ParamField(
        key="reset_xyz",
        type="xyz",
        label="Reset XYZ",
        help=(
            "Coordinates used to dismiss the entrance prompt and reset state. "
            "The prompt must NOT appear when standing here."
        ),
        default=None,
        required=True,
    ),
    ParamField(
        key="offers_challenge_mode",
        type="bool",
        label="Offers Challenge Mode",
        help=(
            "Tick this only if this dungeon's sigil offers Challenge Mode. It adds the "
            "Challenge Mode bot option, so the user can choose whether to use it; leave it "
            "off and the option does not appear at all, because there is nothing to choose."
        ),
        default=False,
        default_hint="Off — the dungeon is entered in normal mode.",
    ),
    ParamField(
        key=PARAM_OFFERS_TEAM_UP,
        type="bool",
        label="Offers Team-Up",
        help=(
            "Tick this only if this sigil offers Team-Up *and* this is a run you would be "
            "happy for a stranger to be matched into — the queue brings in players who are "
            "not yours and cannot see what the bot is doing. It adds the Team-Up bot option, "
            "so the user chooses whether to use it; leave it off and neither option appears."
        ),
        default=False,
        default_hint="Off — the dungeon is entered alone.",
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Enters a dungeon via its sigil. Handles prompt detection, "
        "dungeon warning confirmation, and player zone desynchronization. "
        "Tick Offers Challenge Mode if this dungeon has it: that gives the user a Challenge Mode "
        "bot option, and when they turn it on the sigil is toggled before entering. "
        "Tick Offers Team-Up to give the user a Team-Up bot option, which enters through the "
        "matchmaking queue instead — the same sigil, a different tail."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players are now in dungeon_zone."),
        ReturnSpec(
            "FAILURE",
            "Players were not in from_zone at the start, the entrance prompt "
            "never appeared after retries, Challenge Mode was requested but the sigil "
            "could not be toggled into it, the Team-Up confirmation window never opened, "
            "timed out waiting for the zone change or for the queue to match, "
            "or not all players reached dungeon_zone.",
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
            name="Challenge Mode",
            type="bool",
            default=False,
            description=(
                "Enter this bot's dungeon in Challenge Mode. Each player toggles the sigil's mode "
                "on the entrance prompt before pressing X."
            ),
            # Only the author knows whether their dungeon has a Challenge Mode at all, and
            # most do not. Without this the option appeared in every bot with a sigil in it.
            required_when="offers_challenge_mode",
        ),
        BotOptionSpec(
            name=OPTION_TEAM_UP,
            type="bool",
            default=False,
            description=(
                "Join this bot's dungeon through the Team-Up queue instead of entering it alone. "
                "Matching can take up to an hour, and brings in players who are not yours."
            ),
            # The same shape Challenge Mode established: the author states the fact about
            # their dungeon in a param, and whether to use it stays the user's choice.
            required_when=PARAM_OFFERS_TEAM_UP,
        ),
        BotOptionSpec(
            name=OPTION_MINIMUM_TEAM_SIZE,
            type="int",
            default=2,
            minimum=2,
            maximum=4,
            description=(
                "How many players the Team-Up queue must gather before the dungeon starts. "
                "Larger teams take longer to match."
            ),
            required_when=PARAM_OFFERS_TEAM_UP,
            # Nothing this holds can affect a run that is not queueing.
            depends_on=OPTION_TEAM_UP,
        ),
    ],
    safety_warning=(
        "Team-Up is on with more than one player hooked. Each player queues on their own, and the "
        "node can only check that they all reached the dungeon zone — not that they were matched "
        "into the same instance of it. A split party would still be reported as a successful entry."
    ),
    max_consecutive_retries=10,
    usage_notes=(
        "If some but not all players reach dungeon_zone, the node first tries teleporting the "
        "stragglers to whichever player did arrive — the same recovery Teleport To Player "
        "If In Zone offers as a standalone node — before reporting FAILURE. That needs "
        "every player to already be mutual in-game friends."
    ),
    implies_tags_when={"Team-Up": PARAM_OFFERS_TEAM_UP, "Challenge Mode": "offers_challenge_mode"},
)

from bartlebot.nodes.timing import (  # noqa: E402
    AFK_INTERVAL,
    POLL_INTERVAL,
    TEAMUP_QUEUE_TIMEOUT,
    ZONE_CHANGE_TIMEOUT,
)

# Word the entrance prompt's title gains once the sigil is set to Challenge Mode. The
# toggle button itself carries no readable state, so the title is the only confirmation.
CHALLENGE_TITLE_KEYWORD = "challenge"

# The toggle cycles modes rather than flipping a flag, so a dungeon offering more than two
# could need several clicks to come back around. Bounded so a button that does nothing
# fails rather than clicking forever.
MAX_MODE_TOGGLES = 4

_TEAM_SIZE_PATHS = {
    2: TEAMUP_2_PLAYER_CHECKBOX,
    3: TEAMUP_3_PLAYER_CHECKBOX,
    4: TEAMUP_4_PLAYER_CHECKBOX,
}


async def _mode_matches(client, challenge: bool) -> bool:
    """Whether the entrance prompt's title says this client's sigil is in the wanted mode."""
    title = node_helpers.strip_html(await node_helpers.get_text_by_path(client, SIGIL_ENTRANCE_TITLE_PATH))
    return (CHALLENGE_TITLE_KEYWORD in title.casefold()) is challenge


async def _set_sigil_mode(machine, client, challenge: bool, safety_delay: float) -> bool:
    """Click the sigil mode toggle until the prompt title reports the wanted mode.

    Returns False only when the mode could not be reached — which, for challenge=False,
    includes an unreadable title never reading as Challenge, so a dungeon without the
    toggle costs one read and no clicks.
    """
    for attempt in range(MAX_MODE_TOGGLES):
        if await _mode_matches(client, challenge):
            return True
        if not await node_helpers.click_by_path(client, SIGIL_MODE_TOGGLE_BUTTON_PATH):
            machine.log(
                "Sigil mode toggle button is missing or disabled — this dungeon may not offer Challenge Mode",
                logging.WARNING,
            )
            return False
        machine.log(f"Toggled sigil mode (click {attempt + 1}/{MAX_MODE_TOGGLES})", logging.DEBUG)
        await node_helpers.poll_until(lambda: _mode_matches(client, challenge), safety_delay, POLL_INTERVAL)
    return await _mode_matches(client, challenge)


async def _enter_normal(machine, clients: list, from_zone: str, challenge_mode: bool, safety_delay: float) -> bool:
    """Enter through the sigil itself: set the mode, press X, answer the prompt, leave.

    The entrance prompt is already showing on every client. Returns False only on a real
    refusal — the game saying no. A client that presses X and simply never leaves from_zone
    is not failed here: the caller's loading-wait and recovery-aware arrival check run
    regardless, and a groupmate who did transition gives the straggler a working fallback,
    the same way enter_zone's trigger retry does.
    """
    # Every client toggles its own prompt, and the mode is confirmed from the prompt
    # title rather than from the toggle button, which has no readable state. Only done
    # when Challenge Mode is on: a sigil without the toggle is already in normal mode,
    # and clicking a missing button is not a failure there.
    if challenge_mode:
        machine.log("Toggling sigils into Challenge Mode…", logging.DEBUG)
        toggled = await asyncio.gather(*[_set_sigil_mode(machine, c, True, safety_delay) for c in clients])
        if not all(toggled):
            failed = [i for i, ok in enumerate(toggled) if not ok]
            machine.log(f"FAILURE: could not set Challenge Mode on client(s) {failed}", logging.WARNING)
            return False

    machine.log("Pressing X to enter dungeon sigil", logging.DEBUG)
    await asyncio.gather(*[c.send_key(Keycode.X, 0.1) for c in clients])

    # Answer the dungeon entry prompt — "you are about to enter a Dungeon" — which the
    # sigil raises before it will take anyone inside.
    machine.log("Checking for the dungeon entry prompt…", logging.DEBUG)
    if refusal := await node_helpers.answer_instance_prompts(clients, safety_delay, POLL_INTERVAL):
        machine.log(
            f"FAILURE: could not enter the dungeon — {dialog_captions.describe(refusal)}",
            logging.WARNING,
        )
        return False

    # Bounded by Safety Delay rather than the full zone-change budget: this is only checking
    # whether the trigger fired, not waiting out a loading screen — that budget belongs to
    # the arrival check the caller runs afterward.
    machine.log(f"Waiting for clients to leave {from_zone!r}…", logging.DEBUG)
    if not await node_helpers.poll_until(
        lambda: node_helpers.all_left_zone(clients, from_zone), safety_delay, POLL_INTERVAL
    ):
        zones_at_timeout = await asyncio.gather(*[c.zone_name() for c in clients])
        machine.log(f"Client zones at timeout: {list(zones_at_timeout)}", logging.DEBUG)
        stuck = [z for z in zones_at_timeout if node_helpers.zone_matches(z, from_zone)]
        machine.log(
            f"{len(stuck)} client(s) never left {from_zone!r} after {safety_delay:.1f}s; "
            "continuing to the recovery-aware arrival check",
            logging.DEBUG,
        )
    return True


async def _enter_teamup(machine, clients: list, from_zone: str, team_size: int, safety_delay: float) -> bool:
    """Enter through the Team-Up queue: open the window, pick a size, queue, and wait.

    The entrance prompt is already showing on every client. Returns False having logged
    the reason, or True once every client has left *from_zone*.

    Every client queues on its own, and nothing here makes them one party — see the
    node's safety warning for what that costs.
    """
    checkbox_path = _TEAM_SIZE_PATHS.get(team_size)
    if checkbox_path is None:
        machine.log(
            f"FAILURE: invalid {OPTION_MINIMUM_TEAM_SIZE} {team_size!r}, must be 2, 3, or 4",
            logging.WARNING,
        )
        return False

    machine.log("Clicking team-up button", logging.DEBUG)
    await asyncio.gather(*[node_helpers.click_by_path(c, DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH) for c in clients])

    machine.log("Waiting for team-up confirmation window…", logging.DEBUG)
    appeared = await asyncio.gather(
        *[
            node_helpers.poll_until(
                lambda c=c: node_helpers.is_visible_by_path(c, ENTER_TEAMUP_BUTTON_PATH),
                safety_delay,
                POLL_INTERVAL,
            )
            for c in clients
        ]
    )
    if not all(appeared):
        machine.log("FAILURE: team-up confirmation window never appeared", logging.WARNING)
        return False

    machine.log(f"Selecting {team_size}-player team size checkbox", logging.DEBUG)
    await asyncio.gather(*[node_helpers.click_by_path(c, checkbox_path) for c in clients])

    machine.log("Clicking enter team-up button", logging.DEBUG)
    await asyncio.gather(*[node_helpers.click_by_path(c, ENTER_TEAMUP_BUTTON_PATH) for c in clients])

    # Matchmaking can take a long time; a brief A keypress every AFK_INTERVAL seconds
    # keeps the character from going idle and being disconnected out of the queue.
    machine.log(f"Waiting for team-up zone change (timeout: {TEAMUP_QUEUE_TIMEOUT / 60:.0f}m)…", logging.DEBUG)
    loop_start = asyncio.get_event_loop().time()
    last_afk = loop_start
    while True:
        now = asyncio.get_event_loop().time()
        if await node_helpers.all_left_zone(clients, from_zone):
            return True
        if now - loop_start > TEAMUP_QUEUE_TIMEOUT:
            machine.log(
                f"FAILURE: timed out after {TEAMUP_QUEUE_TIMEOUT / 60:.0f}m waiting for team-up zone change",
                logging.WARNING,
            )
            return False
        if now - last_afk >= AFK_INTERVAL:
            machine.log("Sending anti-AFK input during team-up wait", logging.DEBUG)
            await asyncio.gather(*[c.send_key(Keycode.A, 0.1) for c in clients])
            last_afk = now
        await asyncio.sleep(POLL_INTERVAL)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.is_loading(),
          client.send_key(Keycode.X), node_helpers.click_by_path()

    Flow:
      1. node_helpers.reach_sigil_prompt(): verify from_zone, clear a stale prompt, and
         teleport to the sigil until the entrance prompt shows. FAILURE if it never does.
      2. One tail or the other, chosen by the Team-Up option:
         - normal: toggle Challenge Mode if it was asked for, press X, answer the
           instance prompt, wait to leave from_zone.
         - team-up: click Team-Up, tick the team size, join the queue, and wait up to an
           hour with anti-AFK keypresses.
      3. Wait for all loading screens to finish.
      4. Wait for all clients to arrive in dungeon_zone. Return FAILURE if any are not.

    FAILURE: clients not in from_zone; entrance prompt never appeared; Challenge Mode
    requested but the sigil could not be toggled into it; the Team-Up window never opened;
    the zone change or the queue timed out; not all clients reached dungeon_zone.
    """
    from_zone: str = params["from_zone"]
    dungeon_zone: str = params["dungeon_zone"]
    sigil = XYZ(**params["sigil_xyz"])
    reset = XYZ(**params["reset_xyz"])
    safety_delay: float = machine.get_option("Safety Delay", 3.0)
    # The param is the gate and the option is the choice: a user's leftover override
    # cannot ask for a way in that the author has said this dungeon does not offer.
    challenge_mode: bool = bool(params.get("offers_challenge_mode")) and bool(
        machine.get_option("Challenge Mode", False)
    )
    team_up: bool = bool(params.get(PARAM_OFFERS_TEAM_UP)) and bool(machine.get_option(OPTION_TEAM_UP, False))
    team_size = int(machine.get_option(OPTION_MINIMUM_TEAM_SIZE, 2) or 2)

    if team_up:
        mode_label = f"Team-Up, minimum team size {team_size}"
    else:
        mode_label = "Challenge Mode" if challenge_mode else "normal mode"
    machine.log(f"enter_dungeon: {from_zone!r} → {dungeon_zone!r} ({mode_label})")

    # 1. Get every client onto the sigil with its entrance prompt showing.
    if not await node_helpers.reach_sigil_prompt(
        machine, clients, from_zone, sigil, reset, safety_delay, POLL_INTERVAL
    ):
        return "FAILURE"

    # 2. The two tails share nothing past this point.
    if team_up:
        entered = await _enter_teamup(machine, clients, from_zone, team_size, safety_delay)
    else:
        entered = await _enter_normal(machine, clients, from_zone, challenge_mode, safety_delay)
    if not entered:
        return "FAILURE"

    # 3. Wait for loading screens to finish.
    machine.log("Waiting for loading screen to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL)

    # 4. Wait for all clients to arrive in dungeon_zone.
    if (
        actual := await node_helpers.wait_for_zone_with_recovery(
            machine, clients, dungeon_zone, ZONE_CHANGE_TIMEOUT, POLL_INTERVAL
        )
    ) is not None:
        machine.log(f"Zones after dungeon entry: {actual}", logging.DEBUG)
        machine.log(
            f"FAILURE: not all clients reached dungeon zone (expected={dungeon_zone!r}, got={list(set(actual))})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients in {dungeon_zone!r}")
    return "SUCCESS"
