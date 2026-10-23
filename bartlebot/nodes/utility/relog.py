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

**A player just logged in is skipped** (``machine.is_freshly_logged_in``): a relaunch, crash
recovery's Esc → Quit or this node itself left it outside any instance on a fresh session, and
nothing has moved it since. Nearly every shipped bot's recovery state is this node, and without
the skip it logged out a team recovery had just logged in.

**A dialog over the screen is closed first (#281).** Every modal swallows clicks on whatever is behind it,
and the spellbook is behind them all. A player that failed to leave a dungeon is still looking at its exit
prompt when recovery sends the bot here, so ``node_helpers.clear_modals`` closes whatever is up before the
spellbook is clicked, and again if one opens over it. It *cancels*: the right button, else the center. Confirming
would leave the dungeon or enter another, and Quit raises its own question about a dungeon, which is
confirmed after the click. A dialog that will not close fails the node, naming it. A disconnect modal is left
alone and the player is handed to crash recovery. The crowns shop is closed with ESC before any dialog: it is no
dialog, but it takes every click the same way (#280). It is closed again on logging back in, since some logins
open it, but there one that stays open is only logged.

**Where the character comes back.** Not where it logged out. Quitting from inside a dungeon
or a house puts the character back where it stood before entering. Put this node somewhere
the bot can carry on from — typically just before it travels somewhere — rather than in the
middle of a dungeon run.

**Never mid-fight (#203).** This node reaches Quit through the spellbook, which will not open in
battle. Esc opens the same settings page there, and ``log_out_and_in(..., in_battle=True)`` takes
that route for crash recovery, which logs a team out of a fight a relaunched player could not get
back into (not while "You Are Idle" is up, which blocks Esc). Logging out of a fight is not wanted
from this node, though: a player that logs back in beside a mob is pulled straight
into one, and the group survey that follows opens the social window on every player while the
fight goes on unanswered, until the game asks "You Are Idle". So the node waits for every player
to be out of battle before it logs anyone out, and again before it re-forms the group. The
ambient combat watcher fights meanwhile: it never cancels this node, so it moves nobody.
"""

import asyncio
import logging

from wizwalker.errors import ClientClosedError

from bartlebot.clients import group_manager
from bartlebot.game import dialog_captions, wizard_names
from bartlebot.launcher import auto_select
from bartlebot.nodes import node_helpers
from bartlebot.nodes.combat_runner import slots_in_battle
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
    RELOG_FIGHT_TIMEOUT,
    RELOG_IN_GAME_TIMEOUT,
    TRIGGER_TIMEOUT,
)
from bartlebot.settings_manager import SettingsManager

logger = logging.getLogger(__name__)

# How many times to try the menu: reopening the spellbook when it comes up on the Realms tab,
# which is the one tab that hides the Quit button (a fresh open shows it), or when a dialog
# opened over it. Two is already paranoid.
_MENU_ATTEMPTS = 2

# How one player's relog ended, as log_out_and_in returns it. BACK is in the world again;
# FAILED is still in the world where it started, having never got past Quit; ABANDONED is a
# player past Quit that could not finish and is now in crash recovery's hands.
BACK = "back"
FAILED = "failed"
ABANDONED = "abandoned"

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="relog_clients",
        type="player_list",
        label="Players to Relog",
        help=(
            "Which players log out and back in. Relogging everyone at once is usually best, since the "
            "group re-forms once at the end."
        ),
        default=[],
        required=False,
        default_hint="every player",
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Logs players out to character select and straight back in, which clears the slowdown of "
        "a long session in seconds."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Every selected player logged out and is back in the world."),
        ReturnSpec(
            "FAILURE",
            (
                "A fight didn't end in time, a dialog or the Crowns Shop wouldn't close, the spellbook or its settings page "
                "didn't open, or the game refused to log out. The player is still in the world in every case."
            ),
        ),
    ],
    max_consecutive_retries=2,
    usage_notes=(
        (
            "Place this node just before the bot travels. Players come back where they were before "
            "entering their current dungeon or house."
        )
        + "\n\n"
        + (
            "Settings › Bot › Relog interval does the same on a timer. Use this node when the moment "
            "matters, such as after a set number of loops, or on a FAILURE edge as a last resort."
        )
        + "\n\n"
        + (
            "The group is lost on logout, and re-formed afterward when Automatically group players is "
            "on in Settings › Bot."
        )
        + "\n\n"
        + ("Nobody logs out during a fight, and the group re-forms only after any fight the players log back into.")
        + "\n\n"
        + (
            "A dialog left open on a player, such as the prompt for leaving a dungeon, is closed first "
            "with Cancel or OK, so nothing it asks for happens. So is the Crowns Shop, before logging out "
            "and again after logging back in."
        )
        + "\n\n"
        + (
            "A player that can't log back in is closed and relaunched as after a crash, and the bot "
            "continues from its recovery state. If that player has no account or saved password, the "
            "bot stops."
        )
        + "\n\n"
        + ("A player that just logged in, after crash recovery or a scheduled relog, is skipped until it moves.")
    ),
)


async def _clear_modals(machine, client) -> bool:
    """Close any dialog over the screen, which would swallow every click on the menu behind it.

    A player that failed to leave a dungeon is left looking at its exit prompt, and recovery
    sends the bot here from exactly that state (#281). Cancelled rather than confirmed, so
    nothing the dialog asks for happens: Quit asks about a dungeon itself, and that one is
    confirmed after the click. False when a dialog would not close, or the client is
    disconnected, which the caller sorts out.

    The crowns shop goes first. It is not a dialog, but it takes every click the same way,
    and one a misclick opened while the group formed kept a run's spellbook shut until the
    bot stopped (#280).
    """
    if not await _close_crowns_shop(machine, client, "over the menu"):
        return False
    result = await node_helpers.clear_modals(client, TRIGGER_TIMEOUT, POLL_INTERVAL)
    if result.dismissed:
        machine.log(f"relog: closed {len(result.dismissed)} dialog(s) over the menu — {_named(result.dismissed)}")
    if result.cleared or result.disconnected:
        return result.cleared
    machine.log(f"relog: a dialog is blocking the menu and would not close — {_named((result.key,))}", logging.WARNING)
    return False


async def _close_crowns_shop(machine, client, where: str) -> bool:
    """Close the crowns shop if it is open, saying so. False only when it was open and stayed open.

    *where* finishes the log lines, after "the crowns shop".
    """
    shop = await node_helpers.close_crowns_shop(client)
    if shop.was_open and shop.closed:
        machine.log(f"relog: closed the crowns shop {where}")
    elif shop.was_open:
        machine.log(f"relog: the crowns shop {where} would not close", logging.WARNING)
    return shop.closed


def _named(keys) -> str:
    """The dialogs a log line speaks of, by what a player would have read, never by their text."""
    return ", ".join(dialog_captions.describe(key) if key else "one this build does not recognize" for key in keys)


async def _open_settings_page(machine, client) -> bool:
    """Open the spellbook's settings page with the Quit button showing.

    The Realms tab is the only tab that hides Quit, and a settings page opened fresh always
    shows it — so a page that comes up on Realms is closed and reopened rather than clicked
    around, which needs no knowledge of what the other tabs are called.
    """
    for attempt in range(_MENU_ATTEMPTS):
        if not await _clear_modals(machine, client):
            return False
        await node_helpers.click_by_path(client, SPELLBOOK_BUTTON_PATH)
        if not await node_helpers.poll_until(
            lambda: node_helpers.is_visible_by_path(client, SPELLBOOK_PREFERENCES_TAB_PATH),
            TRIGGER_TIMEOUT,
            POLL_INTERVAL,
        ):
            if attempt + 1 < _MENU_ATTEMPTS and await node_helpers.modal_showing(client):
                machine.log("relog: a dialog opened over the spellbook — closing it", logging.DEBUG)
                continue
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


async def _open_settings_page_in_battle(machine, client) -> bool:
    """Open the settings page with Esc, the one way to it in battle: the spellbook will not open there.

    Esc opens the same page the spellbook's Preferences tab does, Quit button included, at any
    point in a fight. Pressed again only if the first press showed nothing, since a second press
    on an open page closes it. Not while "You Are Idle" is up, which blocks it.
    """
    for _attempt in range(_MENU_ATTEMPTS):
        if not await _clear_modals(machine, client):
            return False
        await auto_select.press_escape(client.window_handle)
        if await node_helpers.poll_until(
            lambda: node_helpers.is_visible_by_path(client, SETTINGS_QUIT_BUTTON_PATH),
            TRIGGER_TIMEOUT,
            POLL_INTERVAL,
        ):
            return True
    machine.log("relog: Esc did not open the settings page", logging.WARNING)
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


async def _disconnected(client) -> bool:
    """Whether this client is showing a disconnect modal ("Connection Lost!" and the like).

    A disconnected client goes on answering ``zone_name()`` with the zone it was last in, so
    ``_back_in_world`` takes it for a player still in the world, and nothing on its screen
    responds: Quit does nothing, and the modal blocks the spellbook. Only a relaunch brings
    it back, so it is abandoned to crash recovery rather than reported as a FAILURE the run
    counts, and can stop on.
    """
    try:
        return await node_helpers.identify_modal(client) in dialog_captions.DISCONNECTS
    except Exception:
        return False


_DISCONNECTED = "lost its connection to the game"
# Reads after a subject, as _abandon's reasons do.


def _abandon(machine, client, reason: str) -> str:
    """Give up on a player past Quit, handing it to crash recovery when there is one.

    *reason* reads after a subject. A handover and a stop each log for themselves, so the
    node speaks only when neither happened: a run with no recovery behind it, such as the
    node tester's.
    """
    if machine.abandon_client(client, reason):
        return ABANDONED
    if not machine.is_stopped:
        machine.log(f"relog: player {reason}", logging.WARNING)
    return FAILED


async def log_out_and_in(machine, client, *, in_battle: bool = False) -> str:
    """Take one client out to character select and back into the world: BACK, FAILED or ABANDONED.

    *in_battle* reaches Quit with Esc rather than the spellbook, which will not open in battle.
    Crash recovery uses it to log players out of a fight a relaunched player could not get back
    into. The caller holds health checks off for *client* throughout, as :func:`run` does, all
    but the disconnect dialog watcher.

    That watcher can hand the client to crash recovery part-way through, closing it. Whatever
    the steps here made of that -- a menu that never opened, a read that raised -- the answer
    is then ABANDONED, so the caller leaves the client to recovery as it would one this
    function gave up on itself.
    """
    try:
        result = await _log_out_and_in(machine, client, in_battle=in_battle)
    except Exception:
        if machine.is_handed_to_recovery(client):
            return ABANDONED
        raise
    if result != BACK and machine.is_handed_to_recovery(client):
        return ABANDONED
    return result


async def _log_out_and_in(machine, client, *, in_battle: bool) -> str:
    """The steps of :func:`log_out_and_in`, which stop early once *client* is handed to recovery."""
    if await _disconnected(client):
        return _abandon(machine, client, _DISCONNECTED)

    opened = (
        await _open_settings_page_in_battle(machine, client)
        if in_battle
        else await _open_settings_page(machine, client)
    )
    if not opened:
        # A connection lost while the menu was being opened is recovery's, not a failure in the world.
        return _abandon(machine, client, _DISCONNECTED) if await _disconnected(client) else FAILED

    await node_helpers.click_by_path(client, SETTINGS_QUIT_BUTTON_PATH)

    # Quit usually goes straight to character select, but inside a dungeon, or with Dungeon
    # Recall open, the game first asks whether to log out anyway (#108). The wait answers
    # that each tick rather than spending a Safety Delay up front on a dialog that is
    # normally absent.
    refusal: str | None = None
    disconnected = False

    async def _reached_char_select() -> bool:
        nonlocal refusal, disconnected
        if machine.is_handed_to_recovery(client):
            return True
        if await auto_select.at_character_select(client):
            return True
        if await _disconnected(client):
            disconnected = True
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
            return FAILED
        return _abandon(
            machine,
            client,
            f"left the world but character select never appeared within {RELOG_CHAR_SELECT_TIMEOUT:.0f}s",
        )
    if machine.is_handed_to_recovery(client):
        return ABANDONED
    if disconnected:
        return _abandon(machine, client, _DISCONNECTED)
    if refusal:
        machine.log(f"relog: the game refused the logout — {dialog_captions.describe(refusal)}", logging.WARNING)
        return FAILED

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
        if machine.is_handed_to_recovery(client):
            return ABANDONED
        if await _back_in_world(client):
            machine.note_logged_in([client.window_handle])
            # The shop opens by itself on some logins. Back in the world is back, open or
            # not, so a shop that stays up is only logged; the next node's clicks are what it
            # would cost.
            await _close_crowns_shop(machine, client, "the login opened")
            return BACK
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


async def _fights_over(machine, clients: list, before: str) -> bool:
    """Wait until no player is in battle. True once none is; False after RELOG_FIGHT_TIMEOUT.

    *before* finishes the log line saying what is waiting on the fight. Nothing here fights:
    the ambient combat watcher does that alongside the node.
    """

    async def _fighting() -> list[int]:
        flags = await asyncio.gather(*(c.in_battle() for c in clients), return_exceptions=True)
        return slots_in_battle(flags)

    fighting = await _fighting()
    if not fighting:
        return True
    machine.log(f"relog: player(s) {[i + 1 for i in fighting]} in a fight — waiting for it to end before {before}")

    async def _none_fighting() -> bool:
        return not await _fighting()

    return await node_helpers.poll_until(_none_fighting, RELOG_FIGHT_TIMEOUT, POLL_INTERVAL)


async def _regroup(machine, clients: list) -> None:
    """Re-form the in-game group the logout dissolved, if the user has that enabled.

    Never raises and never fails the node: running ungrouped costs the guaranteed first turn
    in combat, which is a degraded run rather than a broken one. Waits out a fight first, since
    a player can log back in beside a mob, and the survey opens the social window over the cards.
    """
    if not SettingsManager.instance().auto_group_players:
        return
    if len(clients) < 2:
        return
    if not await _fights_over(machine, clients, "re-forming the group"):
        machine.log(
            f"relog: the fight was still going after {RELOG_FIGHT_TIMEOUT:.0f}s — the bot continues ungrouped",
            logging.WARNING,
        )
        return
    try:
        names = await wizard_names.resolve_names(clients)
        result = await group_manager.form_group(
            clients, names, log=machine.log, safety_delay=machine.get_option("Safety Delay", 3.0)
        )
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
          node_helpers.clear_modals(),
          node_helpers.is_visible_by_path(), auto_select.at_character_select(),
          auto_select.press_character_select_play(), client.is_loading(), client.zone_name(),
          machine.suspend_health_checks_for() / resume_health_checks_for(),
          machine.abandon_client()

    Flow (all selected players in parallel):
      0. Wait until no player, selected or not, is in a fight.
      1. Suspend health monitoring for the players involved. A client at character select
         fails zone_name() and shows loading screens, which is indistinguishable from the
         crash those watchers exist to catch — without this the monitor force-closes a
         client that is relogging perfectly well.
      2. Close the crowns shop and any dialog over the screen, open the spellbook, open
         Preferences, and make sure the Quit button is showing.
      3. Click Quit and wait for the character select screen, confirming the warning the
         game raises when logging out of a dungeon or with Dungeon Recall open.
      4. Press Play, and keep pressing while the screen is still up.
      5. Wait for the world to be readable again, and close the crowns shop if the login opened it.
      6. Put health monitoring back, then re-form the group once no player is in a fight.

    FAILURE: a fight was still going RELOG_FIGHT_TIMEOUT after the node started; a dialog or the
    crowns shop over the screen would not close; the spellbook or its settings page did not open; the game refused the logout; or Quit did not take
    effect. The player is still in the world each time.

    Past Quit, a player that does not make it back (character select never appears, a
    loading screen outlasts LOADING_SCREEN_TIMEOUT, or the overall deadline passes) is
    handed to crash recovery through machine.abandon_client(), and the node raises
    ClientClosedError instead of returning. A FAILURE is routed and counted before the run
    parks, and can stop the run before recovery resumes it; the dead-client branch parks
    without counting. Health checks stay off for that player until recovery's resume().
    A player showing a disconnect modal is handed over the same way, before Quit or after.
    """
    raw: list[int] = params.get("relog_clients") or []
    indices = [n - 1 for n in raw if 0 < n <= len(clients)] if raw else list(range(len(clients)))
    targets = [clients[i] for i in indices]
    if not targets:
        machine.log("relog: no players selected — nothing to do")
        return "SUCCESS"

    fresh = [i for i in indices if machine.is_freshly_logged_in(clients[i])]
    if fresh:
        # Recovery has just logged them in, or a relog has: they are outside any instance on a
        # fresh session already, which is all a relog would do for them.
        machine.log(f"relog: player(s) {[i + 1 for i in fresh]} logged in since they last moved — not relogging them")
        indices = [i for i in indices if i not in fresh]
        targets = [clients[i] for i in indices]

    if not targets:
        # Nobody logs out, so the group is as recovery left it: re-forming it would only open the
        # social window on every player for nothing.
        machine.log("SUCCESS: every player had just logged in; nobody relogged")
        return "SUCCESS"

    if not await _fights_over(machine, clients, "logging anyone out"):
        machine.log(f"FAILURE: the fight was still going after {RELOG_FIGHT_TIMEOUT:.0f}s", logging.WARNING)
        return "FAILURE"

    machine.log(f"relog: logging player(s) {[i + 1 for i in indices]} out and back in")

    hwnds = [c.window_handle for c in targets]
    machine.suspend_health_checks_for(hwnds, watch_disconnects=True)
    results: list = []
    try:
        results = await asyncio.gather(
            *[log_out_and_in(machine, c) for c in targets],
            return_exceptions=True,
        )
    finally:
        # Always, including on a failure: leaving the watchers off for the rest of the run
        # would mean a real crash on one of these clients goes unnoticed. Except for a
        # player handed to recovery, where the flag is what stops a watcher recovering it
        # a second time.
        abandoned = [i for i, r in zip(indices, results) if r == ABANDONED]
        machine.resume_health_checks_for([clients[i].window_handle for i in indices if i not in abandoned])

    for position, result in zip(indices, results):
        if isinstance(result, Exception):
            machine.log(f"relog: player {position + 1} raised during the relog: {result}", logging.WARNING)

    if abandoned:
        machine.log(f"relog: player(s) {[i + 1 for i in abandoned]} being relaunched — the bot resumes after recovery")
        raise ClientClosedError()

    if not all(r == BACK for r in results):
        machine.log("FAILURE: not every player logged back in", logging.WARNING)
        return "FAILURE"

    await _regroup(machine, clients)
    machine.log(f"SUCCESS: {len(targets)} player(s) relogged")
    return "SUCCESS"
