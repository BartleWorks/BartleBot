"""Node: log players out to character select and straight back in.

Why a bot would want this: a game client gets slower the longer it stays logged in. Over a
measured 801-fight overnight run the same fight went from 23 s to 32 s, and the cost is a
page-fault storm inside the game process that nothing outside it can reach. Logging
out clears it. That is also what a scheduled relog does — this node is for a bot that
wants to choose the moment instead of waiting for the timer, and for wiring a relog onto a
FAILURE transition as a last resort.

**The process is never closed here.** The client stays hooked all the way through: the
player-struct hook is a patch in the game's own code that re-populates itself the moment a
character loads in, so nothing has to be re-attached, re-embedded, or logged back in with
stored credentials. That is what makes this cost seconds where the scheduled relog costs
minutes — and also what it cannot do: a client wedged badly enough to be unable to reach its
own menu needs the scheduled relog's force-close, and GDI exhaustion needs the
process gone entirely.

**Past Quit there is no going back.** Once Quit has taken the client out of the world, a
relog that cannot finish leaves a client nothing else in the bot can use, and one this node
is holding health checks off for, so no watcher will notice either. Any failure from there on
is therefore treated as a crash: the client is handed to crash recovery to be closed and
relaunched, and the run resumes at the bot's recovery state once it is back. That includes
the case this was written for, a client that left character select and then sat on its
loading screen forever. Only a failure while the client is still in the world is a FAILURE.

**Where the character comes back.** Not where it logged out. Quitting from inside a dungeon
or a house puts the character back where it stood before entering. Put this node somewhere
the bot can carry on from — typically just before it travels somewhere — rather than in the
middle of a dungeon run.
"""

import asyncio
import logging

from wizwalker.errors import ClientClosedError

from bartlebot import dialog_captions, group_manager, wizard_names
from bartlebot.launcher import auto_select
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import (
    BACKPACK_CLOSE_BUTTON_PATH,
    REALM_CURRENT_NAME_PATH,
    SETTINGS_QUIT_BUTTON_PATH,
    SPELLBOOK_BUTTON_PATH,
    SPELLBOOK_PREFERENCES_TAB_PATH,
)
from bartlebot.nodes.timing import (
    LOADING_SCREEN_TIMEOUT,
    POLL_INTERVAL,
    RELOG_CHAR_SELECT_TIMEOUT,
    RELOG_IN_GAME_TIMEOUT,
    TRIGGER_TIMEOUT,
)
from bartlebot.settings_manager import SettingsManager

logger = logging.getLogger(__name__)

# How many times to reopen the spellbook when it comes up on the Realms tab, which is the
# one tab that hides the Quit button. Two is already paranoid: a fresh open shows it.
_MENU_ATTEMPTS = 2

# How one player's relog ended. ABANDONED is a player past Quit that could not finish and
# is now in crash recovery's hands.
_BACK = "back"
_FAILED = "failed"
_ABANDONED = "abandoned"

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="relog_clients",
        type="player_list",
        label="Players to Relog",
        help=(
            "Which players log out and back in. All of them by default.\n\n"
            "Relogging the whole group at once is usually what you want: the players are "
            "logged out for the same few seconds, and the group is re-formed once at the end "
            "rather than after each one."
        ),
        default=[],
        required=False,
        default_hint="Empty means every player.",
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Logs players out to the character select screen and immediately plays the same "
        "character back in, without closing the game. Clears the slowdown that builds up over "
        "a long session in seconds rather than the minutes a full relaunch costs."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Every selected player logged out and is back in the world."),
        ReturnSpec(
            "FAILURE",
            "The spellbook or its settings page did not open, the game refused the logout, or "
            "Quit did not take effect. In every case the player is still in the world.",
        ),
    ],
    max_consecutive_retries=2,
    usage_notes=(
        "Players come back where they were BEFORE entering their current dungeon or house — "
        "logging out leaves an instance. Place this node where the bot can carry on from "
        "whatever the character's position turns out to be, normally just before it travels "
        "somewhere.\n\n"
        "The group is lost on logout and re-formed afterwards when 'Automatically group "
        "players' is enabled in Settings.\n\n"
        "A player that logs out but cannot get back in (character select never appears, or "
        "it is still loading a minute after leaving character select) is closed and "
        "relaunched as though its game had crashed, and the bot carries on from its recovery "
        "state. If that player cannot be relaunched, because no account is linked or no "
        "password is saved, the bot stops and says so.\n\n"
        "You may not need this node at all: Settings → Bot → 'Relog interval' does "
        "the same job on a timer, and can also bring back a player too broken to use its own "
        "menu. Use the node when the moment matters — after a fixed number of passes, or on a "
        "FAILURE transition as a last resort before giving up."
    ),
)


async def _open_settings_page(machine, client) -> bool:
    """Open the spellbook's settings page with the Quit button showing.

    The Realms tab is the only tab that hides Quit, and a settings page opened fresh always
    shows it — so a page that comes up on Realms is closed and reopened rather than clicked
    around, which needs no knowledge of what the other tabs are called.
    """
    for attempt in range(_MENU_ATTEMPTS):
        await node_helpers.click_by_path(client, SPELLBOOK_BUTTON_PATH)
        if not await node_helpers.poll_until(
            lambda: node_helpers.is_visible_by_path(client, SPELLBOOK_PREFERENCES_TAB_PATH),
            TRIGGER_TIMEOUT,
            POLL_INTERVAL,
        ):
            machine.log("relog: spellbook did not open", logging.WARNING)
            return False

        await node_helpers.click_by_path(client, SPELLBOOK_PREFERENCES_TAB_PATH)
        if not await node_helpers.poll_until(
            lambda: node_helpers.is_visible_by_path(client, SETTINGS_QUIT_BUTTON_PATH),
            TRIGGER_TIMEOUT,
            POLL_INTERVAL,
        ):
            if await node_helpers.is_visible_by_path(client, REALM_CURRENT_NAME_PATH) and attempt + 1 < _MENU_ATTEMPTS:
                machine.log("relog: settings page opened on the Realms tab — reopening it", logging.DEBUG)
                await node_helpers.click_by_path(client, BACKPACK_CLOSE_BUTTON_PATH)
                await asyncio.sleep(POLL_INTERVAL)
                continue
            machine.log("relog: the Quit button never appeared on the settings page", logging.WARNING)
            return False
        return True
    return False


async def _back_in_world(client) -> bool:
    """Whether this client has finished loading in and is readable again.

    Every read here fails while the client sits at character select or on a loading screen,
    which is the normal case for most of the wait — so the exception is the answer, not an
    error worth logging.
    """
    try:
        if await client.is_loading():
            return False
        return bool(await client.zone_name())
    except Exception:
        return False


def _abandon(machine, client, reason: str) -> str:
    """Give up on a player past Quit, handing it to crash recovery when there is one.

    *reason* reads after a subject. A handover and a stop each log for themselves, so the
    node speaks only when neither happened: a run with no recovery behind it, such as the
    node tester's.
    """
    if machine.abandon_client(client, reason):
        return _ABANDONED
    if not machine.is_stopped:
        machine.log(f"relog: player {reason}", logging.WARNING)
    return _FAILED


async def _relog_one(machine, client) -> str:
    """Take one client out to character select and back into the world."""
    if not await _open_settings_page(machine, client):
        return _FAILED

    await node_helpers.click_by_path(client, SETTINGS_QUIT_BUTTON_PATH)

    # Quit usually goes straight to character select, but inside a dungeon, or with Dungeon
    # Recall open, the game first asks whether to log out anyway (#108). The wait answers
    # that each tick rather than spending a Safety Delay up front on a dialog that is
    # normally absent.
    refusal: str | None = None

    async def _reached_char_select() -> bool:
        nonlocal refusal
        if await auto_select.at_character_select(client):
            return True
        outcome = await node_helpers.resolve_modal(client)
        refusal = outcome.refusal
        return refusal is not None

    if not await node_helpers.poll_until(_reached_char_select, RELOG_CHAR_SELECT_TIMEOUT, POLL_INTERVAL):
        if await _back_in_world(client):
            machine.log(
                "relog: character select never appeared after Quit — the player is still in the world",
                logging.WARNING,
            )
            return _FAILED
        return _abandon(
            machine,
            client,
            f"left the world but character select never appeared within {RELOG_CHAR_SELECT_TIMEOUT:.0f}s",
        )
    if refusal:
        machine.log(f"relog: the game refused the logout — {dialog_captions.describe(refusal)}", logging.WARNING)
        return _FAILED

    machine.log("relog: at character select — playing the same character back in", logging.DEBUG)

    # Press until the screen is gone. One Enter is normally enough, but the screen ignores
    # input for a moment while it settles, and pressing again costs nothing: Enter only ever
    # lands on a screen already confirmed to be character select.
    #
    # Two clocks. Off character select and not yet in the world is a loading screen, or a
    # client too far gone to read (an infinite load can look like either), and it gets the
    # same minute the health monitor allows any other loading screen. The overall deadline
    # is for a character select that keeps coming back.
    loop = asyncio.get_event_loop()
    deadline = loop.time() + RELOG_IN_GAME_TIMEOUT
    loading_since: float | None = None
    while (now := loop.time()) < deadline:
        if await _back_in_world(client):
            return _BACK
        if await auto_select.at_character_select(client):
            loading_since = None
            await auto_select.press_character_select_play(client.window_handle)
        elif loading_since is None:
            loading_since = now
        elif now - loading_since >= LOADING_SCREEN_TIMEOUT:
            return _abandon(
                machine, client, f"has been loading for {LOADING_SCREEN_TIMEOUT:.0f}s since leaving character select"
            )
        await asyncio.sleep(POLL_INTERVAL)

    return _abandon(machine, client, f"did not load back in within {RELOG_IN_GAME_TIMEOUT:.0f}s")


async def _regroup(machine, clients: list) -> None:
    """Re-form the in-game group the logout dissolved, if the user has that enabled.

    Never raises and never fails the node: running ungrouped costs the guaranteed first turn
    in combat, which is a degraded run rather than a broken one.
    """
    if not SettingsManager.instance().auto_group_players:
        return
    if len(clients) < 2:
        return
    try:
        names = await wizard_names.resolve_names(clients)
        result = await group_manager.form_group(clients, names, log=machine.log)
        if result.success:
            machine.log(f"relog: group re-formed — {result.summary()}")
        else:
            machine.log(f"relog: group not fully re-formed ({result.summary()})", logging.WARNING)
    except Exception:
        machine.log("relog: could not re-form the group — the bot continues ungrouped", logging.WARNING)
        logger.debug("Post-relog grouping failed", exc_info=True)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: node_helpers.click_by_path(), node_helpers.poll_until(), node_helpers.resolve_modal(),
          node_helpers.is_visible_by_path(), auto_select.at_character_select(),
          auto_select.press_character_select_play(), client.is_loading(), client.zone_name(),
          machine.suspend_health_checks_for() / resume_health_checks_for(),
          machine.abandon_client()

    Flow (all selected players in parallel):
      1. Suspend health monitoring for the players involved. A client at character select
         fails zone_name() and shows loading screens, which is indistinguishable from the
         crash those watchers exist to catch — without this the monitor force-closes a
         client that is relogging perfectly well.
      2. Open the spellbook, open Preferences, and make sure the Quit button is showing.
      3. Click Quit and wait for the character select screen, confirming the warning the
         game raises when logging out of a dungeon or with Dungeon Recall open.
      4. Press Play, and keep pressing while the screen is still up.
      5. Wait for the world to be readable again.
      6. Put health monitoring back, then re-form the group.

    FAILURE: the spellbook or its settings page did not open; the game refused the logout;
    or Quit did not take effect. The player is still in the world each time.

    Past Quit, a player that does not make it back (character select never appears, a
    loading screen outlasts LOADING_SCREEN_TIMEOUT, or the overall deadline passes) is
    handed to crash recovery through machine.abandon_client(), and the node raises
    ClientClosedError instead of returning. A FAILURE is routed and counted before the run
    parks, and can stop the run before recovery resumes it; the dead-client branch parks
    without counting. Health checks stay off for that player until recovery's resume().
    """
    raw: list[int] = params.get("relog_clients") or []
    indices = [n - 1 for n in raw if 0 < n <= len(clients)] if raw else list(range(len(clients)))
    targets = [clients[i] for i in indices]
    if not targets:
        machine.log("relog: no players selected — nothing to do")
        return "SUCCESS"

    machine.log(f"relog: logging player(s) {[i + 1 for i in indices]} out and back in")

    hwnds = [c.window_handle for c in targets]
    machine.suspend_health_checks_for(hwnds)
    results: list = []
    try:
        results = await asyncio.gather(
            *[_relog_one(machine, c) for c in targets],
            return_exceptions=True,
        )
    finally:
        # Always, including on a failure: leaving the watchers off for the rest of the run
        # would mean a real crash on one of these clients goes unnoticed. Except for a
        # player handed to recovery, where the flag is what stops a watcher recovering it
        # a second time.
        abandoned = [i for i, r in zip(indices, results) if r == _ABANDONED]
        machine.resume_health_checks_for([clients[i].window_handle for i in indices if i not in abandoned])

    for position, result in zip(indices, results):
        if isinstance(result, Exception):
            machine.log(f"relog: player {position + 1} raised during the relog: {result}", logging.WARNING)

    if abandoned:
        machine.log(f"relog: player(s) {[i + 1 for i in abandoned]} being relaunched — the bot resumes after recovery")
        raise ClientClosedError()

    if not all(r == _BACK for r in results):
        machine.log("FAILURE: not every player logged back in", logging.WARNING)
        return "FAILURE"

    await _regroup(machine, clients)
    machine.log(f"SUCCESS: {len(targets)} player(s) relogged")
    return "SUCCESS"
