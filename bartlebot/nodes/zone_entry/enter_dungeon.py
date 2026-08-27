"""Node: enter a dungeon via its sigil."""

import asyncio
import logging

from wizwalker import XYZ, Keycode

from bartlebot import dialog_captions
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import SIGIL_ENTRANCE_TITLE_PATH, SIGIL_MODE_TOGGLE_BUTTON_PATH

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="from_zone",
        type="str",
        label="From Zone",
        help="Zone name players must currently be in when this node executes.",
        default="",
        required=True,
    ),
    ParamField(
        key="dungeon_zone",
        type="str",
        label="Dungeon Zone",
        help="Zone name all players must be in after successfully entering the dungeon.",
        default="",
        required=True,
    ),
    ParamField(
        key="sigil_xyz",
        type="xyz",
        label="Sigil XYZ",
        help=("Coordinates of the dungeon sigil. Standing here should trigger the dungeon entrance prompt."),
        default={"x": 0.0, "y": 0.0, "z": 0.0},
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
        default={"x": 0.0, "y": 0.0, "z": 0.0},
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Enters a dungeon via its sigil. Handles prompt detection, "
        "dungeon warning confirmation, and player zone desynchronization. "
        "Enable the Challenge Mode bot option to toggle the sigil into Challenge Mode before entering."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players are now in dungeon_zone."),
        ReturnSpec(
            "FAILURE",
            "Players were not in from_zone at the start, the entrance prompt "
            "never appeared after retries, Challenge Mode was requested but the sigil "
            "could not be toggled into it, timed out waiting for zone change, "
            "or not all players reached dungeon_zone.",
        ),
    ],
    required_bot_options=[
        BotOptionSpec(
            name="Safety Delay",
            type="float",
            default=3,
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
                "Enter dungeons in Challenge Mode. Each player toggles the sigil's mode on the "
                "entrance prompt before pressing X. Fails if the dungeon does not offer Challenge Mode."
            ),
        ),
    ],
    max_consecutive_retries=10,
)

from bartlebot.nodes.timing import MAX_RETRIES, POLL_INTERVAL, ZONE_CHANGE_TIMEOUT

# Word the entrance prompt's title gains once the sigil is set to Challenge Mode. The
# toggle button itself carries no readable state, so the title is the only confirmation.
CHALLENGE_TITLE_KEYWORD = "challenge"

# The toggle cycles modes rather than flipping a flag, so a dungeon offering more than two
# could need several clicks to come back around. Bounded so a button that does nothing
# fails rather than clicking forever.
MAX_MODE_TOGGLES = 4


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


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.is_loading(),
          client.send_key(Keycode.X)

    Flow:
      1. Verify all clients are in from_zone. Return FAILURE if not.
      2. Clear any pre-existing dungeon entrance prompt by teleporting to reset_xyz.
      3. Retry loop (up to MAX_RETRIES): teleport to sigil_xyz, wait for the entrance
         prompt. If it does not appear, teleport to reset_xyz and retry.
      3b. If the Challenge Mode bot option is on, click each client's sigil mode toggle
         until the prompt title reads "Challenge". Return FAILURE if any client cannot
         be toggled into it.
      4. Press X on all clients to interact with the sigil.
      5. Poll for a dungeon warning dialog for Safety Delay seconds; confirm it if present.
      6. Wait for all clients to leave from_zone. Return FAILURE if timeout expires.
      7. Wait for all loading screens to finish.
      8. Wait for all clients to arrive in dungeon_zone. Return FAILURE if any are not.

    FAILURE: clients not in from_zone; entrance prompt never appeared after MAX_RETRIES;
    Challenge Mode requested but the sigil could not be toggled into it; zone change timed
    out; not all clients reached dungeon_zone.
    """
    from_zone: str = params["from_zone"]
    dungeon_zone: str = params["dungeon_zone"]
    sigil = XYZ(**params["sigil_xyz"])
    reset = XYZ(**params["reset_xyz"])
    safety_delay: float = machine.get_option("Safety Delay", 3.0)
    challenge_mode: bool = bool(machine.get_option("Challenge Mode", False))
    mode_label = "Challenge Mode" if challenge_mode else "normal mode"
    machine.log(f"enter_dungeon: {from_zone!r} → {dungeon_zone!r} ({mode_label})")

    # 1. Verify all clients are in from_zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    if not all(node_helpers.zone_matches(z, from_zone) for z in zones):
        wrong = [z for z in zones if not node_helpers.zone_matches(z, from_zone)]
        machine.log(
            f"FAILURE: clients not in expected zone (expected={from_zone!r}, unexpected={wrong})",
            logging.WARNING,
        )
        return "FAILURE"

    # 2. Clear any pre-existing dungeon entrance prompt before starting.
    prompts = await asyncio.gather(
        *[node_helpers.is_visible_by_path(c, node_helpers.DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH) for c in clients]
    )
    if any(prompts):
        stale = [i for i, p in enumerate(prompts) if p]
        machine.log(
            f"Pre-existing dungeon entrance prompt on client(s) {stale}; teleporting to reset_xyz to clear it",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(reset) for c in clients])
        await node_helpers.poll_until(
            lambda: node_helpers.none_at_dungeon_entrance(clients), safety_delay, POLL_INTERVAL
        )

    # 3. Retry loop: teleport to sigil, wait for dungeon entrance prompt.
    prompt_appeared = False
    for attempt in range(1, MAX_RETRIES + 1):
        machine.log(f"Attempt {attempt}/{MAX_RETRIES}: teleporting clients to sigil", logging.DEBUG)
        await asyncio.gather(*[c.teleport(sigil) for c in clients])

        machine.log("Waiting for dungeon entrance prompt…", logging.DEBUG)
        if await node_helpers.poll_until(
            lambda: node_helpers.all_at_dungeon_entrance(clients), safety_delay, POLL_INTERVAL
        ):
            prompt_appeared = True
            break

        machine.log(
            f"Entrance prompt did not appear (attempt {attempt}); resetting to reset_xyz",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(reset) for c in clients])

    if not prompt_appeared:
        machine.log(
            f"FAILURE: entrance prompt never appeared after {MAX_RETRIES} attempts",
            logging.WARNING,
        )
        return "FAILURE"

    # 3b. Set the sigil's entrance mode. Every client toggles its own prompt, and the mode
    #     is confirmed from the prompt title rather than from the toggle button, which has
    #     no readable state. Only done when Challenge Mode is on: a sigil without the
    #     toggle is already in normal mode, and clicking a missing button is not a failure
    #     there.
    if challenge_mode:
        machine.log("Toggling sigils into Challenge Mode…", logging.DEBUG)
        toggled = await asyncio.gather(*[_set_sigil_mode(machine, c, True, safety_delay) for c in clients])
        if not all(toggled):
            failed = [i for i, ok in enumerate(toggled) if not ok]
            machine.log(
                f"FAILURE: could not set Challenge Mode on client(s) {failed}",
                logging.WARNING,
            )
            return "FAILURE"

    # 4. Press X to interact with the sigil (entrance prompt is showing).
    machine.log("Pressing X to enter dungeon sigil", logging.DEBUG)
    await asyncio.gather(*[c.send_key(Keycode.X, 0.1) for c in clients])

    # 5. Answer the dungeon entry prompt — "you are about to enter a Dungeon" — which the
    #    sigil raises before it will take anyone inside.
    machine.log("Checking for the dungeon entry prompt…", logging.DEBUG)
    if refusal := await node_helpers.answer_instance_prompts(clients, safety_delay, POLL_INTERVAL):
        machine.log(
            f"FAILURE: could not enter the dungeon — {dialog_captions.describe(refusal)}",
            logging.WARNING,
        )
        return "FAILURE"

    # 6. Wait for all clients to leave from_zone.
    machine.log(f"Waiting for clients to leave {from_zone!r}…", logging.DEBUG)
    if not await node_helpers.poll_until(
        lambda: node_helpers.all_left_zone(clients, from_zone), ZONE_CHANGE_TIMEOUT, POLL_INTERVAL
    ):
        zones_at_timeout = await asyncio.gather(*[c.zone_name() for c in clients])
        machine.log(f"Client zones at timeout: {list(zones_at_timeout)}", logging.DEBUG)
        machine.log(
            f"FAILURE: timed out after {ZONE_CHANGE_TIMEOUT}s waiting for clients to leave {from_zone!r}",
            logging.WARNING,
        )
        return "FAILURE"

    # 7. Wait for loading screens to finish.
    machine.log("Waiting for loading screen to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL)

    # 8. Wait for all clients to arrive in dungeon_zone.
    if (
        actual := await node_helpers.wait_for_zone(clients, dungeon_zone, ZONE_CHANGE_TIMEOUT, POLL_INTERVAL)
    ) is not None:
        machine.log(f"Zones after dungeon entry: {actual}", logging.DEBUG)
        machine.log(
            f"FAILURE: not all clients reached dungeon zone (expected={dungeon_zone!r}, got={list(set(actual))})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients in {dungeon_zone!r}")
    return "SUCCESS"
