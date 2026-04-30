import asyncio
import logging
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from transitions.extensions.asyncio import AsyncMachine
from wizwalker.errors import ClientClosedError, MemoryReadError

from bartlebot.nodes.timing import LOADING_SCREEN_TIMEOUT, POLL_INTERVAL

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_CLIENT_STATUS_CHECK_INTERVAL: float = 5.0
_QTY_PREFIX = re.compile(r"^(\d+)\s+(.+)$")
# Seconds between client responsiveness polls in _client_status_watcher.

_CLIENT_STATUS_CHECK_FAIL_THRESHOLD: int = 3
# Consecutive failed zone_name() calls before a client is treated as unresponsive.


class BotMachine:
    """
    FSM engine for BartleBot. Wraps transitions.AsyncMachine.

    Each state in the config maps to a node coroutine in the registry.
    The run() loop executes the current state's node, stores the result,
    then fires the 'advance' trigger so transitions evaluates conditions
    and moves to the next state.
    """

    def __init__(
        self,
        clients: list,
        config: dict,
        node_registry: dict,
        bot_config: "BotConfig | None" = None,
        on_state_change: Callable[[str], None] | None = None,
        on_transition: Callable[[str, str, str], None] | None = None,
        on_drop: Callable[[str, int, int], None] | None = None,
        max_consecutive_same_state: int | None = None,
        on_client_disconnected: Callable[[int], None] | None = None,
        stop_grace_period: float = 1.0,
    ) -> None:
        self.clients = clients
        self._config = config
        self._registry = node_registry
        self._bot_config = bot_config
        self._on_state_change = on_state_change
        self._on_transition = on_transition
        self._on_drop = on_drop
        self._on_client_disconnected = on_client_disconnected
        self._stop_flag = asyncio.Event()
        self._stop_grace_period = stop_grace_period
        self._current_node_task: asyncio.Task | None = None
        self.drops: list[tuple[str, int, int]] = []  # (item, quantity, client_index)
        self._resume_event = asyncio.Event()
        self._resume_event.set()  # not paused by default
        self._ambient_combat_lock = asyncio.Lock()
        self._combat_auto_paused: bool = False  # True while watcher has auto-paused the FSM
        self.last_result: str | None = None
        self._no_match_count: int = 0
        self._max_consecutive_same_state = max_consecutive_same_state
        self._consecutive_same_state_count: int = 0
        self._status_check_flagged: set[int] = set()  # HWNDs already flagged as unresponsive

        self._state_map = {s["id"]: s for s in config["states"]}
        # Compiled compound flag expressions from to_machine_config().
        # Keys are _flag_compound_<idx> strings; values are {"flags": [...], "op": "AND"|"OR"}.
        self._conditions_registry: dict[str, dict] = config.get("conditions_registry", {})

        states = [s["id"] for s in config["states"]]
        transitions = config["transitions"]

        self.machine = AsyncMachine(
            model=self,
            states=states,
            transitions=transitions,
            initial=config["initial"],
            auto_transitions=False,
            ignore_invalid_triggers=False,
        )

    # --- Transition conditions ---

    def __getattr__(self, name: str):
        """Dynamically provide condition methods for transitions.

        _result_is_<VALUE>    — BotConfig.to_machine_config() converts when="SUCCESS"
                                into this condition name.
        _flag_<NAME>          — fires when bot option <NAME> is truthy.
        _flag_compound_<IDX>  — evaluates a compiled multi-flag AND/OR/NOT expression
                                stored in self._conditions_registry.
        """
        if name.startswith("_result_is_"):
            expected = name[len("_result_is_") :]
            return lambda: self.last_result == expected
        if name.startswith("_flag_compound_"):
            cond_info = self._conditions_registry.get(name)
            if cond_info:

                def _eval_compound(ci=cond_info):
                    results = [
                        (not bool(self.get_option(c["option"], default=False)))
                        if c["negate"]
                        else bool(self.get_option(c["option"], default=False))
                        for c in ci["flags"]
                    ]
                    return any(results) if ci["op"] == "OR" else all(results)

                return _eval_compound
        if name.startswith("_flag_"):
            option_name = name[len("_flag_") :]
            return lambda: bool(self.get_option(option_name, default=False))
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    # --- Options ---

    def get_option(self, name: str, default=None):
        """Return the current value of a bot option, or default if not found.

        Checks the current state's per-node option_overrides first, then falls
        back to the bot-wide option value. Reads live from BotConfig so GUI
        changes take effect without restarting the machine.
        """
        # Per-node override takes priority
        try:
            state_def = self._state_map.get(self.state, {})
            overrides = state_def.get("option_overrides") or {}
            if name in overrides:
                return overrides[name]
        except AttributeError:
            pass  # self.state not yet initialised
        if self._bot_config is None:
            return default
        for opt in self._bot_config.options:
            if opt.name == name:
                return opt.default
        return default

    # --- Logging ---

    def log(self, msg: str, level: int = logging.INFO) -> None:
        """Emit a log message from a node."""
        logger.log(level, msg)

    # --- Drop reporting ---

    def on_drop(self, item: str, quantity: int, client_index: int = 0) -> None:
        """Record a detected drop and notify the callback if one was provided."""
        m = _QTY_PREFIX.match(item)
        if m:
            item, quantity = m.group(2), quantity * int(m.group(1))
        self.drops.append((item, quantity, client_index))
        if self._on_drop:
            self._on_drop(item, quantity, client_index)

    # --- Public API ---

    @property
    def is_paused(self) -> bool:
        return not self._resume_event.is_set()

    @property
    def is_stopped(self) -> bool:
        return self._stop_flag.is_set()

    @property
    def stop_event(self) -> asyncio.Event:
        """The asyncio Event that is set when stop() is called.

        Nodes may await or check this to exit early within the grace period,
        avoiding the hard task cancellation that fires after stop_grace_period seconds.
        Example: ``if machine.is_stopped: return "FAILURE"``
        """
        return self._stop_flag

    def _fmt_state(self, state_id: str) -> str:
        """Format a state ID with its label for log messages, e.g. 'state_1' (Go to Zone)."""
        label = self._state_map.get(state_id, {}).get("label")
        return f"{state_id!r} ({label})" if label else f"{state_id!r}"

    async def run(self) -> None:
        """Drive the FSM until stop() is called."""
        watcher = asyncio.create_task(self._ambient_dialog_watcher())
        combat_watcher = asyncio.create_task(self._ambient_combat_watcher())
        status_watcher = asyncio.create_task(self._client_status_watcher())
        loading_watcher = asyncio.create_task(self._loading_screen_watcher())
        try:
            while not self._stop_flag.is_set():
                await self._resume_event.wait()  # block here while paused
                if self._stop_flag.is_set():
                    break
                state_id = self.state
                state_def = self._state_map[state_id]

                if self._on_state_change:
                    try:
                        self._on_state_change(state_id)
                    except Exception:
                        logger.exception("on_state_change callback raised in state %s", self._fmt_state(state_id))

                node_type = state_def["node_type"]
                self.log(f"Executing state {self._fmt_state(state_id)} (node_type={node_type})", logging.DEBUG)
                _tick_start = time.monotonic()
                node_fn = self._registry[node_type]
                self._current_node_task = asyncio.create_task(node_fn(self, self.clients, state_def.get("params", {})))
                try:
                    self.last_result = await self._current_node_task
                except asyncio.CancelledError:
                    if self._stop_flag.is_set():
                        # Our deferred_cancel fired after the grace period elapsed.
                        self.log("Node forcibly cancelled after grace period.", logging.WARNING)
                        break
                    # run() was cancelled from outside — propagate.
                    raise
                except (ClientClosedError, MemoryReadError) as exc:
                    # A client process has died. Pause immediately rather than treating
                    # this as a normal FAILURE — prevents the no-match / idle counters
                    # from hitting their limits before _client_loop detects the removal
                    # and triggers the recovery procedure.
                    # MemoryReadError (e.g. error 299 = ERROR_PARTIAL_COPY) occurs when the
                    # process dies before WizWalker's ClientClosedError fires; both indicate
                    # a dead game process and need the same recovery path.
                    logger.warning(
                        "%s in state %s — client may have died, pausing for recovery",
                        type(exc).__name__,
                        self._fmt_state(state_id),
                    )
                    self._no_match_count = 0
                    self._consecutive_same_state_count = 0
                    self.pause()
                    continue
                except Exception:
                    logger.exception("Node execution failed in state %s", self._fmt_state(state_id))
                    self.last_result = "FAILURE"
                finally:
                    self._current_node_task = None
                _duration = time.monotonic() - _tick_start
                self.log(
                    f"State {self._fmt_state(state_id)} returned {self.last_result!r} in {_duration:.1f}s",
                    logging.DEBUG,
                )

                if not self._stop_flag.is_set():
                    prev_state = state_id
                    # A state with no outgoing transitions is a terminal state — stop cleanly.
                    has_exit = any(t["source"] == state_id for t in self._config["transitions"])
                    if not has_exit:
                        self.log(f"Reached terminal state {self._fmt_state(state_id)}; stopping.", logging.INFO)
                        self.stop()
                    else:
                        fired = await self.advance()
                        if not fired:
                            self._no_match_count += 1
                            self.log(
                                f"No transition matched for result {self.last_result!r} in state"
                                f" {self._fmt_state(state_id)} (consecutive: {self._no_match_count})",
                                logging.WARNING,
                            )
                            if self._no_match_count >= 10:
                                self.log(
                                    f"State {self._fmt_state(state_id)} stuck after {self._no_match_count}"
                                    " consecutive no-match advances; stopping.",
                                    logging.ERROR,
                                )
                                self.stop()
                        else:
                            self._no_match_count = 0
                            self.log(
                                f"Transition: {self._fmt_state(prev_state)} --{self.last_result}--> {self._fmt_state(self.state)}",
                                logging.INFO,
                            )
                        if self._on_transition:
                            try:
                                self._on_transition(prev_state, self.last_result or "", self.state)
                            except Exception:
                                logger.exception(
                                    "on_transition callback raised on transition %r → %r",
                                    prev_state,
                                    self.state,
                                )

                    # Per-state idle detection (independent of no-match counter).
                    # Counts how many consecutive ticks the FSM has stayed on the same
                    # state (whether via a self-loop transition or a no-match).
                    per_state_limit = state_def.get("max_consecutive_retries")
                    effective_limit = (
                        per_state_limit if per_state_limit is not None else self._max_consecutive_same_state
                    )
                    if self.state == state_id:
                        self._consecutive_same_state_count += 1
                        if effective_limit is not None and self._consecutive_same_state_count >= effective_limit:
                            self.log(
                                f"State {self._fmt_state(state_id)} active for"
                                f" {self._consecutive_same_state_count} consecutive ticks"
                                " without advancing; stopping.",
                                logging.WARNING,
                            )
                            self.stop()
                    else:
                        self._consecutive_same_state_count = 0
        finally:
            watcher.cancel()
            combat_watcher.cancel()
            status_watcher.cancel()
            loading_watcher.cancel()
            for t in (watcher, combat_watcher, status_watcher, loading_watcher):
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    async def _client_status_watcher(self) -> None:
        """Background task: detect unresponsive clients via zone_name() polling.

        Checks every _CLIENT_STATUS_CHECK_INTERVAL seconds. If a client fails
        _CLIENT_STATUS_CHECK_FAIL_THRESHOLD consecutive checks, it is considered dead
        (crashed or stuck on a disconnect dialog). The machine is paused and
        on_client_disconnected is fired with the client's HWND so AppController can
        force-close the game window. The resulting process exit is detected by
        _client_loop / remove_dead_clients(), which triggers the reconnect procedure.

        This approach avoids trying to match a specific disconnect dialog UI path, which
        shares the same generic MessageBoxModalWindow structure as many other dialogs.
        """
        fail_counts: dict[int, int] = {}  # hwnd -> consecutive failure count

        while not self._stop_flag.is_set():
            await asyncio.sleep(_CLIENT_STATUS_CHECK_INTERVAL)
            for client in list(self.clients):
                hwnd = client.window_handle
                if hwnd in self._status_check_flagged:
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
                        self._status_check_flagged.add(hwnd)
                        self.pause()
                        if self._on_client_disconnected:
                            try:
                                self._on_client_disconnected(hwnd)
                            except Exception:
                                logger.exception("on_client_disconnected callback raised for hwnd=%d", hwnd)

    async def _loading_screen_watcher(self) -> None:
        """Background task: detect clients stuck in an infinite loading screen.

        Tracks when each client first enters a loading state. If any client remains
        loading for longer than LOADING_SCREEN_TIMEOUT seconds, it is treated as a
        crash: the machine is paused and on_client_disconnected is called so
        AppController can force-close and relaunch the game via the standard
        crash-recovery path.
        """
        loading_since: dict[int, float] = {}  # hwnd -> monotonic time loading started

        while not self._stop_flag.is_set():
            await asyncio.sleep(POLL_INTERVAL)
            for client in list(self.clients):
                hwnd = client.window_handle
                if hwnd in self._status_check_flagged:
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
                        self._status_check_flagged.add(hwnd)
                        self.pause()
                        if self._on_client_disconnected:
                            try:
                                self._on_client_disconnected(hwnd)
                            except Exception:
                                logger.exception(
                                    "on_client_disconnected callback raised for hwnd=%d (loading timeout)",
                                    hwnd,
                                )
                else:
                    loading_since.pop(hwnd, None)

    async def _ambient_dialog_watcher(self) -> None:
        """Background task: dismiss any open dialog on all clients via SPACEBAR."""
        from bartlebot.nodes import node_helpers

        while not self._stop_flag.is_set():
            await asyncio.sleep(node_helpers.POLL_INTERVAL)
            if not self._resume_event.is_set():
                continue
            if not self.clients:
                continue
            results = await asyncio.gather(
                *[node_helpers.dismiss_ambient_dialog_once(c) for c in self.clients],
                return_exceptions=True,
            )
            for i, r in enumerate(results):
                if r is True:
                    self.log(f"Ambient dialog dismissed on client {i}", logging.DEBUG)
                elif isinstance(r, Exception):
                    self.log(f"Ambient dialog watcher error on client {i}: {r}", logging.DEBUG)

    async def _ambient_combat_watcher(self) -> None:
        """Background task: detect and handle unexpected combat on any client.

        Runs SprintyCombat using the bot's default combat config whenever a client
        enters combat outside of an explicit handle_combat node. Guarded against
        re-entry and against conflicting with intentional combat nodes.
        """
        from bartlebot.nodes import node_helpers
        from bartlebot.nodes.combat_runner import _resolve_combat_dsl, _run_combat, _split_combat_config
        from bartlebot.utils.drop_detection import extract_new_drops, snapshot_chat

        while not self._stop_flag.is_set():
            await asyncio.sleep(node_helpers.POLL_INTERVAL)
            if not self._resume_event.is_set():
                continue
            clients = list(self.clients)
            if not clients:
                continue

            # Skip while the FSM itself is running a handle_combat node.
            try:
                state_def = self._state_map.get(self.state, {})
            except AttributeError:
                continue  # self.state not yet initialised
            if state_def.get("node_type") == "handle_combat":
                continue

            # Skip if a prior ambient combat session is still running.
            if self._ambient_combat_lock.locked():
                continue

            in_battle_flags = await asyncio.gather(
                *[c.in_battle() for c in clients],
                return_exceptions=True,
            )
            battling = [c for c, f in zip(clients, in_battle_flags) if f is True]
            if not battling:
                continue

            was_running = self._resume_event.is_set()
            if was_running:
                self._combat_auto_paused = True
                self.pause()
                self.log("Ambient combat: FSM paused — waiting for combat to finish", logging.INFO)

            try:
                async with self._ambient_combat_lock:
                    # Re-check state after acquiring lock — FSM may have entered handle_combat
                    # between the check above and here.
                    try:
                        if self._state_map.get(self.state, {}).get("node_type") == "handle_combat":
                            continue
                    except AttributeError:
                        continue

                    self.log(
                        f"Ambient combat detected on {len(battling)} client(s) — handling",
                        logging.WARNING,
                    )

                    combat_dsl, dsl_source = _resolve_combat_dsl(self, {}, use_bot_default=True)
                    if not combat_dsl:
                        self.log("Ambient combat: no combat DSL available, skipping", logging.WARNING)
                        continue

                    self.log(f"Ambient combat: DSL source={dsl_source!r}", logging.DEBUG)

                    chat_before_raw = await asyncio.gather(
                        *[snapshot_chat(c) for c in battling],
                        return_exceptions=True,
                    )
                    chat_before = [s if isinstance(s, str) else "" for s in chat_before_raw]

                    per_client_dsl = _split_combat_config(combat_dsl, len(battling))
                    results = await asyncio.gather(
                        *[_run_combat(c, dsl) for c, dsl in zip(battling, per_client_dsl)],
                        return_exceptions=True,
                    )
                    for i, r in enumerate(results):
                        if isinstance(r, Exception):
                            self.log(f"Ambient combat: error on client {i}: {r}", logging.DEBUG)

                    self.log("Ambient combat: SprintyCombat finished", logging.DEBUG)

                    for i, (client, before) in enumerate(zip(battling, chat_before)):
                        try:
                            after = await snapshot_chat(client)
                            for item_name in extract_new_drops(before, after):
                                self.on_drop(item_name, 1, self.clients.index(client))
                                self.log(f"Ambient combat drop: {item_name}")
                        except Exception as exc:
                            self.log(
                                f"Ambient combat: drop detection error on client {i}: {exc}",
                                logging.DEBUG,
                            )
            finally:
                if self._combat_auto_paused:
                    self._combat_auto_paused = False
                    self.resume()
                    self.log("Ambient combat: FSM resumed", logging.INFO)

    def stop(self) -> None:
        """Signal the FSM to stop.

        Sets the stop flag so cooperative nodes can exit at their next polling checkpoint.
        After stop_grace_period seconds, any still-running node task is forcibly cancelled
        so the bot always halts promptly regardless of what the node is doing.
        """
        if not self._stop_flag.is_set():
            self.log("Bot stopped.", logging.INFO)
        self._stop_flag.set()
        self._resume_event.set()  # unblock any paused wait so stop is seen immediately
        # Schedule hard cancellation as a fallback if the node doesn't exit cooperatively.
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._deferred_cancel(self._current_node_task, self._stop_grace_period))
        except RuntimeError:
            pass  # no running event loop; node will complete naturally

    async def _deferred_cancel(self, task: "asyncio.Task | None", grace: float) -> None:
        """Cancel the node task after a grace period if it hasn't already finished.

        Called by stop() to enforce a maximum stop latency. Nodes that check
        machine.is_stopped (or machine.stop_event) will exit within their polling
        interval and the task will be done before this fires.
        """
        if task is None or task.done():
            return
        await asyncio.sleep(grace)
        if task is not None and not task.done():
            logger.warning("Grace period elapsed; forcibly cancelling running node task")
            task.cancel()

    def pause(self) -> None:
        """Suspend the FSM after the current node finishes."""
        self._resume_event.clear()

    def resume(self) -> None:
        """Resume a paused FSM."""
        self._status_check_flagged.clear()  # allow re-detection after client recovery
        self._resume_event.set()

    def force_state(self, state_id: str) -> None:
        """Force the FSM into a specific state for recovery. Bypasses normal entry callbacks."""
        self.machine.set_state(state_id)  # sets self.state via the transitions machine
        self._consecutive_same_state_count = 0
        self._no_match_count = 0
        logger.info("FSM state forced to %s (recovery)", self._fmt_state(state_id))
