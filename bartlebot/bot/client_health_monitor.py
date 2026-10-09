"""Background health monitoring for WizSprinter clients.

Five polling loops run concurrently:
- _disconnect_dialog_watcher: polls for the in-game disconnect modal every 0.25 s;
  force-kills the process on detection so the recovery flow takes over immediately.
- _network_link_watcher: polls the HUD's network status icons, for a connection that
  failed without the modal; recovers a client stuck on "bad" or "broken".
- _client_status_watcher: calls zone_name() every few seconds to detect
  clients that have crashed or are unresponsive.
- _loading_screen_watcher: calls is_loading() every poll interval to detect
  clients stuck in an infinite loading screen, and reports every client it sees
  loading through on_loading, flagged or not.
- _restricted_zone_watcher: polls zone_name() -- and the duel's own PvP flags
  once a client is in combat -- to stop the bot outright in PvP zones.

All loops write to a shared _flagged set so a client is never double-reported.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from bartlebot.game import dialog_captions, restricted_zones
from bartlebot.nodes.paths import (
    MESSAGE_BOX_CAPTION_PATH,
    NETWORK_LINK_BAD_PATH,
    NETWORK_LINK_BROKEN_PATH,
    NETWORK_LINK_SLOW_PATH,
)
from bartlebot.nodes.timing import (
    LOADING_SCREEN_TIMEOUT,
    NETWORK_LINK_BAD_TIMEOUT,
    NETWORK_LINK_BROKEN_POLLS,
    NETWORK_LINK_POLL_INTERVAL,
    POLL_INTERVAL,
)
from bartlebot.utils.window_kill import terminate_process

logger = logging.getLogger(__name__)


async def _players_on_both_teams(client) -> bool:
    """True when at least two teams in the current duel each hold a real player.

    This is what separates a PvP match from a PvE fight running the PvP ruleset --
    Challenge Mode sets both ``m_bPVP`` and ``m_bBattleground`` on ordinary dungeon
    bosses, so either flag alone stopped a Marleybone run mid-fight.

    ``is_player`` is read per participant, and ``team_id`` rather than
    ``original_team`` on purpose: a mind-controlled *enemy* is an NPC either way,
    and reading the team it is fighting for now is the honest answer to "who is on
    each side". A participant that cannot be read is skipped rather than counted,
    so a partly-readable duel fails open, matching the watcher's own behavior.
    """
    teams: set[int] = set()
    for participant in await client.duel.participant_list():
        try:
            if not await participant.is_player():
                continue
            teams.add(await participant.team_id())
        except Exception:
            continue
        if len(teams) >= 2:
            return True
    return len(teams) >= 2


async def _traverse_path(parent, path: list[str]):
    """Walk a UI window tree by child name segments; return the final node or None."""
    for segment in path:
        match = None
        for child in await parent.children():
            if await child.name() == segment:
                match = child
                break
        if match is None:
            return None
        parent = match
    return parent


_CLIENT_STATUS_CHECK_INTERVAL: float = 5.0
# Seconds between zone_name() responsiveness polls.

_CLIENT_STATUS_CHECK_FAIL_THRESHOLD: int = 3
# Consecutive failed zone_name() calls before a client is treated as unresponsive.

_RESTRICTED_ZONE_CHECK_INTERVAL: float = 0.25
# Seconds between restricted-zone checks. Deliberately far faster than the 5 s status
# poll: the window between arriving in a PvP zone and the bot acting there is the whole
# thing being closed, so this is the one loop where latency is the point.

_DISCONNECT_KEYWORDS: frozenset[str] = frozenset({"disconnect", "connection", "network", "inactivity", "kicked"})
# Fallback only. These are English substrings, so on a client running in any of the game's
# other six locales they match nothing — and "nothing" reads as "no disconnect", which
# leaves a dead client sitting there instead of being recovered. That is why the caption is
# matched against dialog_captions.DISCONNECTS first, which knows all seven locales. The
# keywords stay for a disconnect dialog the snapshot has not seen: broad and English-only
# beats silent on the machines most likely to be running this.
#
# Note "network" would also match the "Network Connection Status" label, which is not a
# disconnect at all. Another reason it is the fallback rather than the test.

LINK_SLOW = "slow"
LINK_BAD = "bad"
LINK_BROKEN = "broken"

_NETWORK_LINK_HUD_PATH = NETWORK_LINK_BROKEN_PATH[:-1]
# The three icons are siblings, so the HUD is walked to once and its children read once.

_NETWORK_LINK_SPRITES: tuple[tuple[str, str], ...] = (
    (LINK_BROKEN, NETWORK_LINK_BROKEN_PATH[-1]),
    (LINK_BAD, NETWORK_LINK_BAD_PATH[-1]),
    (LINK_SLOW, NETWORK_LINK_SLOW_PATH[-1]),
)
# Most severe first, so a client whose icons are mid-transition reports the worse state.


async def _network_link_level(client) -> str | None:
    """Return the most severe network status icon the client's HUD is showing, or None.

    Reads each sprite's *own* visible flag. The spriteNetworkLinkImage inside it reads
    visible whether or not anything is wrong, so a path to the image says nothing. None
    covers both a healthy link and a HUD that is not there to read, such as at character
    select; neither is evidence of a failure.
    """
    hud = await _traverse_path(client.root_window, _NETWORK_LINK_HUD_PATH)
    if hud is None:
        return None
    wanted = {sprite for _, sprite in _NETWORK_LINK_SPRITES}
    found: dict[str, object] = {}
    for child in await hud.children():
        name = await child.name()
        if name in wanted:
            found.setdefault(name, child)
    for level, sprite in _NETWORK_LINK_SPRITES:
        window = found.get(sprite)
        if window is not None and await window.is_visible():
            return level
    return None


async def restriction_for(client) -> restricted_zones.Restriction | None:
    """Return the restriction in force for *client*, or None.

    Checks the zone first because it is one read and always available. The duel
    flags are only consulted while the client is actually in battle: outside
    combat the duel struct still holds whatever the last fight left in it, and a
    stale PvP flag would stop a bot standing in a questing zone.

    The participant list is read only once a flag is already set, because it is
    one memory read per combatant and this runs every 250 ms. In an ordinary PvE
    fight neither flag is set and the extra reads never happen; in a Challenge
    Mode fight both are set and the list is what proves the fight is still PvE.
    """
    hit = restricted_zones.check_zone(await client.zone_name())
    if hit is not None:
        return hit
    if not await client.in_battle():
        return None
    pvp = await client.duel.pvp()
    battleground = await client.duel.battleground()
    if not (pvp or battleground):
        return None
    return restricted_zones.check_duel(
        pvp=pvp,
        battleground=battleground,
        players_on_both_teams=await _players_on_both_teams(client),
    )


@dataclass
class _LinkStreak:
    """One client's unbroken run of a single network status icon."""

    level: str
    started: float
    polls: int = 0
    paused_at: float | None = None


class ClientHealthMonitor:
    """Monitors WizSprinter clients for crashes and loading-screen hangs.

    Intended to be run as a single asyncio task from BotMachine.run().
    Internally manages two sub-tasks and tears them down cleanly when
    cancelled or when the stop flag is set.

    Args:
        clients_ref: Callable that returns the current client list.
                     Called on each poll so it always reflects live clients.
        stop_flag:   The machine's stop event — when set, both loops exit.
        on_pause:    Called when a problem is detected; should pause the FSM.
        on_client_disconnected: Optional callback receiving the dead client's
                     HWND; used by AppController to trigger game relaunch.
        on_restricted_zone: Optional callback receiving the Restriction that fired;
                     used by BotMachine to stop the run. Unlike the disconnect
                     callback this is not a recovery hook -- there is no recovering
                     from it, the run is over.
    """

    def __init__(
        self,
        clients_ref: Callable[[], list],
        stop_flag: asyncio.Event,
        on_pause: Callable[[], None],
        on_client_disconnected: Callable[[int], None] | None = None,
        on_restricted_zone: Callable[[restricted_zones.Restriction], None] | None = None,
        on_loading: Callable[[object], None] | None = None,
    ) -> None:
        self._clients_ref = clients_ref
        self._stop_flag = stop_flag
        self._on_pause = on_pause
        self._on_client_disconnected = on_client_disconnected
        self._on_restricted_zone = on_restricted_zone
        # Told of every client seen loading, or unreadable, on every poll. See _note_loading.
        self._on_loading = on_loading
        self._flagged: set[int] = set()  # HWNDs already flagged as unresponsive

    def clear_flagged(self) -> None:
        """Clear flagged HWNDs so recovered clients can be monitored again."""
        self._flagged.clear()

    def is_flagged(self, hwnd: int) -> bool:
        """Whether this client is currently being ignored by every watcher."""
        return hwnd in self._flagged

    def unflag(self, hwnd: int) -> None:
        """Start watching this client again, without touching anyone else's flag.

        The counterpart to ``flag`` for a suspension that ends inside a run rather than at
        a resume: ``clear_flagged`` would also un-flag a client that is genuinely dead and
        part-way through recovery.
        """
        self._flagged.discard(hwnd)

    def flag(self, hwnd: int) -> None:
        """Stop every watcher reacting to this client until the flags are cleared.

        Flagging is how a watcher records "already dealt with", and a deliberate relog
        needs exactly that from the outside: a client being closed on purpose, or sitting
        at character select, fails ``zone_name()`` and shows loading screens, which is
        indistinguishable here from the crash those watchers exist to catch.
        """
        self._flagged.add(hwnd)

    async def run(self) -> None:
        """Start all health-check loops and wait until the stop flag is set."""
        dialog_task = asyncio.create_task(self._disconnect_dialog_watcher())
        network_task = asyncio.create_task(self._network_link_watcher())
        status_task = asyncio.create_task(self._client_status_watcher())
        loading_task = asyncio.create_task(self._loading_screen_watcher())
        restricted_task = asyncio.create_task(self._restricted_zone_watcher())
        try:
            await self._stop_flag.wait()
        finally:
            for t in (dialog_task, network_task, status_task, loading_task, restricted_task):
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    def _fire_disconnected(self, hwnd: int) -> None:
        """Pause the FSM and invoke the disconnection callback for hwnd."""
        self._on_pause()
        if self._on_client_disconnected:
            try:
                self._on_client_disconnected(hwnd)
            except Exception:
                logger.exception("on_client_disconnected callback raised for hwnd=%d", hwnd)

    def _note_loading(self, client) -> None:
        """Tell the machine *client* is loading, or could not be read.

        Ahead of the flagged check on purpose: a relog flags its clients, and a relog is
        exactly when a realm can change. The machine's record of who shares a realm only
        stays true while nobody it names loads (``bartlebot.sightings.TogetherRecord``).
        """
        if self._on_loading:
            try:
                self._on_loading(client)
            except Exception:
                logger.exception("on_loading callback raised for hwnd=%s", getattr(client, "window_handle", "?"))

    @staticmethod
    def _force_kill_process(pid: int) -> None:
        """Hard-terminate a process by PID using Win32 TerminateProcess.

        A hard kill rather than the graceful sequence in window_kill.force_close_client:
        a client sitting on a disconnect modal has nothing left to shut down cleanly, and
        terminating takes any error popup it might be showing with it.
        """
        terminate_process(pid)

    def recover(self, client, reason: str) -> None:
        """Kill a client that has lost its connection and hand it to recovery.

        Shared by every watcher whose signal is "the game is still running but its
        connection is gone": nothing is left to shut down cleanly, and killing the process
        is what gets remove_dead_clients() to reap it and start the relaunch.

        Public for ``BotMachine.abandon_client``: a node holding health checks off for a
        client it has wedged is the one party that can tell, and it recovers the client
        exactly as a watcher would. *reason* reads after "Client hwnd=N".
        """
        hwnd = client.window_handle
        logger.warning("Client hwnd=%d %s — force-killing and triggering recovery", hwnd, reason)
        self._flagged.add(hwnd)
        try:
            self._force_kill_process(client.process_id)
        except Exception:
            logger.exception("Failed to force-kill process for hwnd=%d", hwnd)
        self._fire_disconnected(hwnd)

    async def _disconnect_dialog_watcher(self) -> None:
        """Detect in-game disconnect modals and force-kill the client process.

        Polls every POLL_INTERVAL seconds. When a client shows a visible
        MessageBoxModalWindow whose caption text matches a disconnect keyword,
        the game process is hard-killed via TerminateProcess so WizWalker's
        remove_dead_clients() picks it up on the next _client_loop tick and
        the existing recovery flow (relaunch + re-hook) runs automatically.

        This fires well before _client_status_watcher would notice, because
        zone_name() keeps returning the last cached zone even after the
        disconnect dialog appears.
        """
        while not self._stop_flag.is_set():
            await asyncio.sleep(POLL_INTERVAL)
            for client in list(self._clients_ref()):
                hwnd = client.window_handle
                if hwnd in self._flagged:
                    continue
                try:
                    modal_visible = await self._is_disconnect_modal_visible(client)
                except Exception:
                    continue
                if not modal_visible:
                    continue
                self.recover(client, "shows a disconnect modal")

    @staticmethod
    async def _is_disconnect_modal_visible(client) -> bool:
        """Return True if the client shows a MessageBoxModalWindow with a disconnect caption."""
        # Quick check: is the modal root present and visible?
        modal = await _traverse_path(client.root_window, ["MessageBoxModalWindow"])
        if modal is None or not await modal.is_visible():
            return False
        # Read the caption to confirm it's a disconnect dialog, not another modal.
        caption_window = await _traverse_path(modal, MESSAGE_BOX_CAPTION_PATH[1:])
        if caption_window is None:
            return False
        text = await caption_window.maybe_text()
        if not text:
            return False

        # Identify it properly first: this works in every locale the game ships, and it
        # distinguishes an actual disconnect from the other modals that mention a
        # connection. A recognized non-disconnect dialog is a definite no, so it returns
        # here rather than falling through to the keyword guess.
        key = dialog_captions.identify_caption(text)
        if key is not None:
            return key in dialog_captions.DISCONNECTS

        return any(kw in text.lower() for kw in _DISCONNECT_KEYWORDS)

    async def _network_link_watcher(self) -> None:
        """Recover clients whose connection failed without a disconnect modal.

        The game does not always put up the modal _disconnect_dialog_watcher looks for.
        Sometimes the client drops off the server and carries on, still walkable, and
        the only sign is the network status icon in the HUD. zone_name() keeps answering
        and no loading screen appears, so none of the other watchers will notice.

        Slow only warns. Bad triggers recovery after NETWORK_LINK_BAD_TIMEOUT: the game
        can recover from it, but rarely does. Broken never recovers, so it only has to
        be read NETWORK_LINK_BROKEN_POLLS times in a row, to rule out a misread.
        """
        streaks: dict[int, _LinkStreak] = {}
        while not self._stop_flag.is_set():
            await asyncio.sleep(NETWORK_LINK_POLL_INTERVAL)
            clients = list(self._clients_ref())
            for gone in streaks.keys() - {c.window_handle for c in clients}:
                del streaks[gone]
            for client in clients:
                await self._check_network_link(client, streaks, time.monotonic())

    async def _check_network_link(self, client, streaks: dict[int, _LinkStreak], now: float) -> None:
        """Advance one client's network-icon streak by one poll, recovering it if due.

        A loading screen pauses the streak rather than ending it. A client showing
        "broken" as a zone change begins is still broken when it lands, and the icon is
        not worth trusting while the screen is up. A read that fails leaves the streak
        untouched: it is no evidence either way, and an unreadable client belongs to
        _client_status_watcher.
        """
        hwnd = client.window_handle
        if hwnd in self._flagged:
            streaks.pop(hwnd, None)
            return
        try:
            loading = await client.is_loading()
            level = None if loading else await _network_link_level(client)
        except Exception:
            return

        streak = streaks.get(hwnd)
        if loading:
            if streak is not None and streak.paused_at is None:
                streak.paused_at = now
            return
        if streak is not None and streak.paused_at is not None:
            streak.started += now - streak.paused_at
            streak.paused_at = None

        if level is None:
            if streak is not None:
                logger.info("Client hwnd=%d network status cleared (was %s)", hwnd, streak.level)
                del streaks[hwnd]
            return
        if streak is None or streak.level != level:
            streak = streaks[hwnd] = _LinkStreak(level=level, started=now)
            logger.warning("Client hwnd=%d network status shows %s", hwnd, level)
        streak.polls += 1

        if level == LINK_BROKEN and streak.polls >= NETWORK_LINK_BROKEN_POLLS:
            reason = f"has shown a broken network link for {streak.polls} polls"
        elif level == LINK_BAD and now - streak.started >= NETWORK_LINK_BAD_TIMEOUT:
            reason = f"has shown a bad network link for {now - streak.started:.0f}s"
        else:
            return
        del streaks[hwnd]
        self.recover(client, reason)

    async def _client_status_watcher(self) -> None:
        """Detect unresponsive clients via zone_name() polling.

        Checks every _CLIENT_STATUS_CHECK_INTERVAL seconds. A client that
        fails _CLIENT_STATUS_CHECK_FAIL_THRESHOLD consecutive checks is treated
        as dead: the FSM is paused and on_client_disconnected is fired with
        the HWND so AppController can force-close the game window.
        """
        fail_counts: dict[int, int] = {}

        while not self._stop_flag.is_set():
            await asyncio.sleep(_CLIENT_STATUS_CHECK_INTERVAL)
            for client in list(self._clients_ref()):
                hwnd = client.window_handle
                if hwnd in self._flagged:
                    continue
                try:
                    await client.zone_name()
                    fail_counts[hwnd] = 0
                except Exception:
                    fail_counts[hwnd] = fail_counts.get(hwnd, 0) + 1
                    logger.debug(
                        "Client status check failed for hwnd=%d (%d/%d)",
                        hwnd,
                        fail_counts[hwnd],
                        _CLIENT_STATUS_CHECK_FAIL_THRESHOLD,
                    )
                    if fail_counts[hwnd] >= _CLIENT_STATUS_CHECK_FAIL_THRESHOLD:
                        logger.warning(
                            "Client hwnd=%d unresponsive after %d checks — pausing and notifying",
                            hwnd,
                            fail_counts[hwnd],
                        )
                        self._flagged.add(hwnd)
                        self._fire_disconnected(hwnd)

    async def _restriction_for(self, client) -> restricted_zones.Restriction | None:
        """Return the restriction in force for *client*, or None. See ``restriction_for``."""
        return await restriction_for(client)

    async def _restricted_zone_watcher(self) -> None:
        """Stop the bot outright when any client is somewhere it must not be automated.

        Unlike the other loops here this one does not pause for recovery -- it fires
        once and returns. There is nothing to recover: the correct response to a
        client sitting in a PvP arena is for the run to be over.

        Read failures are ignored rather than counted. A client mid-zone-transition
        raises from all three of these reads, which the status watcher already treats
        as its own signal; failing open here costs at most one 250 ms tick, and the
        alternative is stopping every run on a transient memory read.
        """
        while not self._stop_flag.is_set():
            await asyncio.sleep(_RESTRICTED_ZONE_CHECK_INTERVAL)
            for client in list(self._clients_ref()):
                try:
                    hit = await self._restriction_for(client)
                except Exception:
                    logger.debug("Restricted-zone check failed for hwnd=%s", getattr(client, "window_handle", "?"))
                    continue
                if hit is None:
                    continue
                logger.critical("Restricted zone detected: %s — stopping the bot", hit.describe())
                self._fire_restricted_zone(hit)
                return

    def _fire_restricted_zone(self, hit: restricted_zones.Restriction) -> None:
        """Invoke the restricted-zone callback, guarded like every other callback here."""
        if self._on_restricted_zone:
            try:
                self._on_restricted_zone(hit)
            except Exception:
                logger.exception("on_restricted_zone callback raised for %s", hit.describe())

    async def _loading_screen_watcher(self) -> None:
        """Detect clients stuck in an infinite loading screen.

        Tracks when each client first enters a loading state. A client that
        remains loading for LOADING_SCREEN_TIMEOUT seconds is treated as crashed:
        the FSM is paused and on_client_disconnected fires so AppController can
        force-close and relaunch.
        """
        loading_since: dict[int, float] = {}

        while not self._stop_flag.is_set():
            await asyncio.sleep(POLL_INTERVAL)
            for client in list(self._clients_ref()):
                hwnd = client.window_handle
                try:
                    is_loading = await client.is_loading()
                except Exception:
                    self._note_loading(client)
                    loading_since.pop(hwnd, None)
                    continue  # let _client_status_watcher handle dead clients
                if is_loading:
                    self._note_loading(client)
                if hwnd in self._flagged:
                    loading_since.pop(hwnd, None)
                    continue

                if is_loading:
                    now = time.monotonic()
                    start = loading_since.setdefault(hwnd, now)
                    elapsed = now - start
                    if elapsed >= LOADING_SCREEN_TIMEOUT:
                        logger.warning(
                            "Client hwnd=%d stuck in loading screen for %.0fs — "
                            "treating as crash and triggering recovery",
                            hwnd,
                            elapsed,
                        )
                        loading_since.pop(hwnd)
                        self._flagged.add(hwnd)
                        self._fire_disconnected(hwnd)
                else:
                    loading_since.pop(hwnd, None)
