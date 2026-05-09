"""Background health monitoring for WizSprinter clients.

Two polling loops run concurrently:
- _client_status_watcher: calls zone_name() every few seconds to detect
  clients that have crashed or are stuck on a disconnect dialog.
- _loading_screen_watcher: calls is_loading() every poll interval to detect
  clients stuck in an infinite loading screen.

Both loops write to a shared _flagged set so a client is never double-reported.
"""

import asyncio
import logging
import time
from collections.abc import Callable

from bartlebot.nodes.timing import LOADING_SCREEN_TIMEOUT, POLL_INTERVAL

logger = logging.getLogger(__name__)

_CLIENT_STATUS_CHECK_INTERVAL: float = 5.0
# Seconds between zone_name() responsiveness polls.

_CLIENT_STATUS_CHECK_FAIL_THRESHOLD: int = 3
# Consecutive failed zone_name() calls before a client is treated as unresponsive.


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
    """

    def __init__(
        self,
        clients_ref: Callable[[], list],
        stop_flag: asyncio.Event,
        on_pause: Callable[[], None],
        on_client_disconnected: Callable[[int], None] | None = None,
    ) -> None:
        self._clients_ref = clients_ref
        self._stop_flag = stop_flag
        self._on_pause = on_pause
        self._on_client_disconnected = on_client_disconnected
        self._flagged: set[int] = set()  # HWNDs already flagged as unresponsive

    def clear_flagged(self) -> None:
        """Clear flagged HWNDs so recovered clients can be monitored again."""
        self._flagged.clear()

    async def run(self) -> None:
        """Start both health-check loops and wait until the stop flag is set."""
        status_task = asyncio.create_task(self._client_status_watcher())
        loading_task = asyncio.create_task(self._loading_screen_watcher())
        try:
            await self._stop_flag.wait()
        finally:
            for t in (status_task, loading_task):
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
