import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from transitions.extensions.asyncio import AsyncMachine

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig


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
        on_log: Callable[[str, int], None] | None = None,
        max_consecutive_same_state: int | None = None,
    ) -> None:
        self.clients = clients
        self._config = config
        self._registry = node_registry
        self._bot_config = bot_config
        self._on_state_change = on_state_change
        self._on_transition = on_transition
        self._on_drop = on_drop
        self._on_log = on_log
        self._stop_flag = asyncio.Event()
        self.drops: list[tuple[str, int, int]] = []  # (item, quantity, client_index)
        self._resume_event = asyncio.Event()
        self._resume_event.set()  # not paused by default
        self._ambient_combat_lock = asyncio.Lock()
        self.last_result: str | None = None
        self._no_match_count: int = 0
        self._max_consecutive_same_state = max_consecutive_same_state
        self._consecutive_same_state_count: int = 0

        self._state_map = {s["id"]: s for s in config["states"]}

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

        _result_is_<VALUE>  — BotConfig.to_machine_config() converts when="SUCCESS"
                              into this condition name.
        _flag_<NAME>        — fires when bot option <NAME> is truthy. Used for
                              bot-level behavioural flags (e.g. "repeat").
        """
        if name.startswith("_result_is_"):
            expected = name[len("_result_is_") :]
            return lambda: self.last_result == expected
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
        self.drops.append((item, quantity, client_index))
        if self._on_drop:
            self._on_drop(item, quantity, client_index)

    # --- Public API ---

    @property
    def is_paused(self) -> bool:
        return not self._resume_event.is_set()

    async def run(self) -> None:
        """Drive the FSM until stop() is called."""
        watcher = asyncio.create_task(self._ambient_dialog_watcher())
        combat_watcher = asyncio.create_task(self._ambient_combat_watcher())
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
                        logger.exception("on_state_change callback raised in state %r", state_id)

                node_type = state_def["node_type"]
                self.log(f"Executing state {state_id!r} (node_type={node_type})", logging.DEBUG)
                _tick_start = time.monotonic()
                node_fn = self._registry[node_type]
                try:
                    self.last_result = await node_fn(self, self.clients, state_def.get("params", {}))
                except Exception:
                    logger.exception("Node execution failed in state %r", state_id)
                    self.last_result = "FAILURE"
                _duration = time.monotonic() - _tick_start
                self.log(
                    f"State {state_id!r} returned {self.last_result!r} in {_duration:.1f}s",
                    logging.DEBUG,
                )

                if not self._stop_flag.is_set():
                    prev_state = state_id
                    # A state with no outgoing transitions is a terminal state — stop cleanly.
                    has_exit = any(t["source"] == state_id for t in self._config["transitions"])
                    if not has_exit:
                        self.log(f"Reached terminal state {state_id!r}; stopping.", logging.INFO)
                        self.stop()
                    else:
                        fired = await self.advance()
                        if not fired:
                            self._no_match_count += 1
                            self.log(
                                f"No transition matched for result {self.last_result!r} in state"
                                f" {state_id!r} (consecutive: {self._no_match_count})",
                                logging.WARNING,
                            )
                            if self._no_match_count >= 10:
                                self.log(
                                    f"State {state_id!r} stuck after {self._no_match_count}"
                                    " consecutive no-match advances; stopping.",
                                    logging.ERROR,
                                )
                                self.stop()
                        else:
                            self._no_match_count = 0
                            self.log(
                                f"Transition: {prev_state!r} --{self.last_result}--> {self.state!r}",
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
                                f"State {state_id!r} active for"
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
            for t in (watcher, combat_watcher):
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    async def _ambient_dialog_watcher(self) -> None:
        """Background task: dismiss any open dialog on all clients via SPACEBAR."""
        from bartlebot.nodes import node_helpers

        while not self._stop_flag.is_set():
            await asyncio.sleep(node_helpers.POLL_INTERVAL)
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
        from bartlebot.nodes.action.handle_combat import (
            _resolve_combat_dsl,
            _run_combat,
            _split_combat_config,
        )
        from bartlebot.utils.drop_detection import extract_new_drops, snapshot_chat

        while not self._stop_flag.is_set():
            await asyncio.sleep(node_helpers.POLL_INTERVAL)
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

    def stop(self) -> None:
        """Signal the FSM to halt after the current node finishes."""
        self.log("Bot stopped.", logging.INFO)
        self._stop_flag.set()
        self._resume_event.set()  # unblock any paused wait so stop is seen immediately

    def pause(self) -> None:
        """Suspend the FSM after the current node finishes."""
        self._resume_event.clear()

    def resume(self) -> None:
        """Resume a paused FSM."""
        self._resume_event.set()
