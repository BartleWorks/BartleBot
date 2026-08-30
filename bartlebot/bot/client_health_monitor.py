"""Background health monitoring for WizSprinter clients.

Three polling loops run concurrently:
- _disconnect_dialog_watcher: polls for the in-game disconnect modal every 0.25 s;
  force-kills the process on detection so the recovery flow takes over immediately.
- _client_status_watcher: calls zone_name() every few seconds to detect
  clients that have crashed or are unresponsive.
- _loading_screen_watcher: calls is_loading() every poll interval to detect
  clients stuck in an infinite loading screen.
- _restricted_zone_watcher: polls zone_name() -- and the duel's own PvP flags
  once a client is in combat -- to stop the bot outright in PvP zones.

All loops write to a shared _flagged set so a client is never double-reported.
"""

import asyncio
import logging
import time
from collections.abc import Callable

from bartlebot import dialog_captions, restricted_zones
from bartlebot.nodes.paths import MESSAGE_BOX_CAPTION_PATH
from bartlebot.nodes.timing import LOADING_SCREEN_TIMEOUT, POLL_INTERVAL
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
    so a partly-readable duel fails open, matching the watcher's own behaviour.
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
    ) -> None:
        self._clients_ref = clients_ref
        self._stop_flag = stop_flag
        self._on_pause = on_pause
        self._on_client_disconnected = on_client_disconnected
        self._on_restricted_zone = on_restricted_zone
        self._flagged: set[int] = set()  # HWNDs already flagged as unresponsive

    def clear_flagged(self) -> None:
        """Clear flagged HWNDs so recovered clients can be monitored again."""
        self._flagged.clear()

    async def run(self) -> None:
        """Start all health-check loops and wait until the stop flag is set."""
        dialog_task = asyncio.create_task(self._disconnect_dialog_watcher())
        status_task = asyncio.create_task(self._client_status_watcher())
        loading_task = asyncio.create_task(self._loading_screen_watcher())
        restricted_task = asyncio.create_task(self._restricted_zone_watcher())
        try:
            await self._stop_flag.wait()
        finally:
            for t in (dialog_task, status_task, loading_task, restricted_task):
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

    @staticmethod
    def _force_kill_process(pid: int) -> None:
        """Hard-terminate a process by PID using Win32 TerminateProcess.

        A hard kill rather than the graceful sequence in window_kill.force_close_client:
        a client sitting on a disconnect modal has nothing left to shut down cleanly, and
        terminating takes any error popup it might be showing with it.
        """
        terminate_process(pid)

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
                logger.warning(
                    "Client hwnd=%d shows a disconnect modal — force-killing and triggering recovery",
                    hwnd,
                )
                self._flagged.add(hwnd)
                try:
                    self._force_kill_process(client.process_id)
                except Exception:
                    logger.exception("Failed to force-kill process for hwnd=%d", hwnd)
                self._fire_disconnected(hwnd)

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
        # connection. A recognised non-disconnect dialog is a definite no, so it returns
        # here rather than falling through to the keyword guess.
        key = dialog_captions.identify_caption(text)
        if key is not None:
            return key in dialog_captions.DISCONNECTS

        return any(kw in text.lower() for kw in _DISCONNECT_KEYWORDS)

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
                if hwnd in self._flagged:
                    loading_since.pop(hwnd, None)
                    continue
                try:
                    is_loading = await client.is_loading()
                except Exception:
                    loading_since.pop(hwnd, None)
                    continue  # let _client_status_watcher handle dead clients

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
