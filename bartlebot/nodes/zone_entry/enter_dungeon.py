"""Node: enter a dungeon via its sigil, alone or through the Team-Up queue."""

import asyncio
import logging

from wizwalker import XYZ

from bartlebot.clients import game_keys
from bartlebot.game import dialog_captions
from bartlebot.game.key_bindings import GameAction
from bartlebot.nodes import node_helpers
from bartlebot.nodes.help_text import ARRIVAL_NOTE, RESET_XYZ, ZONE_AT_END, ZONE_AT_START
from bartlebot.nodes.param_schema import SAFETY_DELAY_OPTION, BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import (
    DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH,
    ENTER_TEAMUP_BUTTON_PATH,
    SIGIL_ENTRANCE_TITLE_PATH,
    SIGIL_MODE_TOGGLE_BUTTON_PATH,
    TEAMUP_2_PLAYER_CHECKBOX,
    TEAMUP_3_PLAYER_CHECKBOX,
    TEAMUP_4_PLAYER_CHECKBOX,
    TEAMUP_CONFIRMATION_WINDOW_PATH,
    TEAMUP_QUEUED_PATH,
)
from bartlebot.options import player_realms
from bartlebot.options.option_groups import GROUP_DUNGEON, GROUP_TEAM

#: Option names, so the safety dialog and the tests name them from one place.
OPTION_TEAM_UP = "Team-Up"
OPTION_CHALLENGE_MODE = "Challenge Mode"
OPTION_MINIMUM_TEAM_SIZE = "Minimum Team Size"

#: The param that says this sigil offers Team-Up at all.
PARAM_OFFERS_TEAM_UP = "offers_team_up"

#: The param that says this sigil offers Challenge Mode at all.
PARAM_OFFERS_CHALLENGE_MODE = "offers_challenge_mode"

#: The param holding the entrance prompt's title. Editor-only: see bartlebot/content/bot_summary.py.
PARAM_DUNGEON_NAME = "dungeon_name"

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
        key="dungeon_zone",
        type="zone",
        label="Dungeon Zone",
        help=ZONE_AT_END,
        default="",
        required=True,
    ),
    ParamField(
        key=PARAM_DUNGEON_NAME,
        type="str",
        label="Dungeon Name",
        help=(
            "The dungeon's name as its entrance prompt shows it. Record fills it in, and it's used "
            "only to name the dungeon in the bot's description."
        ),
        default="",
        default_hint="named by its zone, if at all",
    ),
    ParamField(
        key="sigil_xyz",
        type="xyz",
        label="Sigil XYZ",
        help="Where players stand to get the dungeon's entrance prompt.",
        default=None,
        required=True,
    ),
    ParamField(
        key="reset_xyz",
        type="xyz",
        label="Reset XYZ",
        help=(RESET_XYZ),
        default=None,
        required=True,
    ),
    ParamField(
        key=PARAM_OFFERS_CHALLENGE_MODE,
        type="bool",
        label="Offers Challenge Mode",
        help="Turn on if this sigil offers Challenge Mode. The user then gets a Challenge Mode bot option.",
        default=False,
        default_hint="normal mode only",
    ),
    ParamField(
        key=PARAM_OFFERS_TEAM_UP,
        type="bool",
        label="Offers Team-Up",
        help=(
            "Turn on if this sigil offers Team-Up and a stranger joining the run is acceptable. The "
            "user then gets the Team-Up and Minimum Team Size bot options."
        ),
        default=False,
        default_hint="no Team-Up",
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Enters a dungeon through its sigil and checks that every player arrived. Fails if a "
        "player outside the team entered with them."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Every player is in Dungeon Zone."),
        ReturnSpec(
            "FAILURE",
            (
                "A player wasn't in From Zone, the entrance prompt never appeared, not every player "
                "reached Dungeon Zone, a player outside the team entered with them, the sigil couldn't "
                "switch to Challenge Mode, or Team-Up didn't open, a player couldn't join its queue, or it "
                "didn't match in time. Team-Up runs skip the check for outsiders."
            ),
        ),
    ],
    required_bot_options=[
        player_realms.SPEC,
        SAFETY_DELAY_OPTION,
        BotOptionSpec(
            name=OPTION_CHALLENGE_MODE,
            group=GROUP_DUNGEON,
            type="bool",
            default=False,
            description="Enter the dungeon in Challenge Mode.",
            # Only the author knows whether their dungeon has a Challenge Mode at all, and
            # most do not. Without this the option appeared in every bot with a sigil in it.
            required_when=PARAM_OFFERS_CHALLENGE_MODE,
        ),
        BotOptionSpec(
            name=OPTION_TEAM_UP,
            group=GROUP_TEAM,
            type="bool",
            default=False,
            description=(
                "Enter the dungeon through the Team-Up queue. Matching can take up to an hour and brings "
                "in players who aren't yours."
            ),
            # The same shape Challenge Mode established: the author states the fact about
            # their dungeon in a param, and whether to use it stays the user's choice.
            required_when=PARAM_OFFERS_TEAM_UP,
        ),
        BotOptionSpec(
            name=OPTION_MINIMUM_TEAM_SIZE,
            group=GROUP_TEAM,
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
        "Team-Up is on with more than one player. Each player queues alone and may land in a "
        "different copy of the dungeon, so the bot brings them together on arrival and stops if "
        "the game refuses."
    ),
    max_consecutive_retries=10,
    usage_notes=(ARRIVAL_NOTE),
    implies_tags_when={"Team-Up": PARAM_OFFERS_TEAM_UP, "Challenge Mode": PARAM_OFFERS_CHALLENGE_MODE},
)

from bartlebot.nodes.timing import (  # noqa: E402
    AFK_INTERVAL,
    POLL_INTERVAL,
    SIGHTING_SETTLE_TIMEOUT,
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
    await asyncio.gather(*[game_keys.press(c, GameAction.INTERACT, 0.1) for c in clients])

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
    joined = await asyncio.gather(*[_join_queue(c, from_zone, safety_delay) for c in clients])
    if not all(joined):
        missing = [number for number, ok in enumerate(joined, start=1) if not ok]
        machine.log(f"FAILURE: player(s) {missing} did not join the Team-Up queue", logging.WARNING)
        return False

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
            await asyncio.gather(*[game_keys.press(c, GameAction.MOVE_LEFT, 0.1) for c in clients])
            last_afk = now
        await asyncio.sleep(POLL_INTERVAL)


async def _join_queue(client, from_zone: str, safety_delay: float) -> bool:
    """Click Enter Team-Up until *client* is in the queue, or already on its way in. True once it is.

    A dropped click used to cost the whole TEAMUP_QUEUE_TIMEOUT, an hour of waiting in a
    queue nobody had joined. Joined is read three ways: the HUD's team-up label shows while
    queued, and the last player to make a team skips the wait, loading straight in or
    having already left the zone. The click is repeated only while the confirmation
    window is still up, so a retry never lands on the world behind a window already gone.
    """

    async def joined() -> bool:
        if await node_helpers.is_visible_by_path(client, TEAMUP_QUEUED_PATH) or await client.is_loading():
            return True
        return await node_helpers.all_left_zone([client], from_zone)

    async def waiting_to_join() -> bool:
        return await node_helpers.is_visible_by_path(client, TEAMUP_CONFIRMATION_WINDOW_PATH) and not await joined()

    await node_helpers.click_until_gone(client, ENTER_TEAMUP_BUTTON_PATH, is_open=waiting_to_join)
    # The window can close before the server's answer reaches the HUD.
    return await node_helpers.poll_until(joined, safety_delay, POLL_INTERVAL)


async def _all_seen(machine, clients: list) -> bool:
    """Whether every player is seen with every other: one realm and one instance. True when Separate."""
    if len(clients) < 2 or player_realms.machine_is_separate(machine):
        return True
    return (await node_helpers.read_sightings(clients, machine)).all_together()


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.is_loading(),
          game_keys.press(client, GameAction.INTERACT), node_helpers.click_by_path()

    Flow:
      1. node_helpers.reach_sigil_prompt(): verify from_zone, clear a stale prompt, and
         teleport to the sigil until the entrance prompt shows. FAILURE if it never does.
      2. One tail or the other, chosen by the Team-Up option:
         - normal: toggle Challenge Mode if it was asked for, press X, answer the
           instance prompt, wait to leave from_zone.
         - team-up: click Team-Up, tick the team size, join the queue, and wait up to an
           hour with anti-AFK keypresses.
      1b. Player Realms: Together -- ensure_together() on the sigil: anyone not seen with the
          others ports to the leader, and everyone reaches the prompt again. FAILURE if that
          port fails.
      3. Wait for all loading screens to finish.
      4. Wait for all clients to arrive in dungeon_zone. Return FAILURE if any are not.
      5. Player Realms: Together -- confirm by sight that everyone is in one instance, and port
         anyone who is not to the leader. FAILURE if that port fails (Team-Up's instances).
      6. Unless through Team-Up: FAILURE if any client has a player loaded who is not ours.

    FAILURE: clients not in from_zone; entrance prompt never appeared; Challenge Mode
    requested but the sigil could not be toggled into it; the Team-Up window never opened;
    the zone change or the queue timed out; not all clients reached dungeon_zone; a
    stranger entered with them.
    """
    from_zone: str = params["from_zone"]
    dungeon_zone: str = params["dungeon_zone"]
    sigil = XYZ(**params["sigil_xyz"])
    reset = XYZ(**params["reset_xyz"])
    safety_delay: float = machine.get_option("Safety Delay", 3.0)
    # The param is the gate and the option is the choice: a user's leftover override
    # cannot ask for a way in that the author has said this dungeon does not offer.
    challenge_mode: bool = bool(params.get(PARAM_OFFERS_CHALLENGE_MODE)) and bool(
        machine.get_option(OPTION_CHALLENGE_MODE, False)
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

    # Everyone is on the sigil, so this is definite. For straggler recovery in a Separate bot.
    await node_helpers.note_realm_peers(machine, clients)

    # 1b. Everyone is on the sigil, where seeing each other is definite. A Together bot enters
    #     as one party or not at all: players on different realms enter separate dungeons.
    gathered = await node_helpers.ensure_together(machine, clients, where="on the sigil")
    if gathered is node_helpers.TogetherOutcome.FAILED:
        return "FAILURE"
    if gathered is node_helpers.TogetherOutcome.GATHERED and not await node_helpers.reach_sigil_prompt(
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

    # 5. Everyone starts at the dungeon's entrance, so a check there is definite. The check on
    #    the sigil makes a split here rare, except through Team-Up, which matches each player on
    #    its own and can put them in different instances.
    if not await node_helpers.poll_until(lambda: _all_seen(machine, clients), SIGHTING_SETTLE_TIMEOUT, POLL_INTERVAL):
        outcome = await node_helpers.ensure_together(machine, clients, where="in the dungeon")
        if outcome is node_helpers.TogetherOutcome.FAILED:
            machine.log("FAILURE: the players entered separate instances of the dungeon", logging.WARNING)
            return "FAILURE"

    # 6. Anyone who pressed X on the sigil in our countdown came in with us, and an instance
    #    holds nobody else, so any player here who is not ours is a stranger (#279). Team-Up
    #    brings strangers in on purpose. Unrouted, FAILURE falls through to recovery, a relog in
    #    every shipped bot, and a relog from inside puts everyone back where they stood before.
    if not team_up and (strangers := await node_helpers.count_strangers(clients)):
        machine.log(
            f"FAILURE: {strangers} player(s) outside the team entered the dungeon with it",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients in {dungeon_zone!r}")
    return "SUCCESS"
