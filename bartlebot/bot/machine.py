import asyncio
import logging
import re
import time
from collections import Counter, deque
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from transitions.extensions.asyncio import AsyncMachine
from wizwalker.errors import ClientClosedError, ExceptionalTimeout, MemoryReadError

from bartlebot import bot_options, option_values, restricted_zones
from bartlebot.bot.client_health_monitor import ClientHealthMonitor

logger = logging.getLogger(__name__)


def _format_duration(seconds: float) -> str:
    """Seconds as "4h 23m 06s" — a five-hour run is unreadable as a float."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig
    from bartlebot.port_map import PortMap

_QTY_PREFIX = re.compile(r"^(\d+)\s+(.+)$")


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
        on_restricted_zone: Callable[[str], None] | None = None,
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
        self._on_restricted_zone = on_restricted_zone
        self._stop_flag = asyncio.Event()
        self._stop_grace_period = stop_grace_period
        self._current_node_task: asyncio.Task | None = None
        self.drops: deque[tuple[str, int, int]] = deque(maxlen=10_000)  # (item, quantity, client_index)
        self._drops_seen: set[tuple[int, str]] = set()  # (client_index, item_lower) — never evicted, used by has_drop()
        # Per-client chat-log baseline that drop detection diffs against. Keyed by the
        # client object rather than its slot: ClientManager.swap_clients reorders slots
        # mid-run, and a baseline describes what a particular game window has already
        # said. Shared by handle_combat and the ambient combat watcher so the two do not
        # absorb each other's announcements.
        self._chat_baselines: dict[object, str] = {}
        self._resume_event = asyncio.Event()
        self._resume_event.set()  # not paused by default
        # Set by the run loop once it has actually reached its paused wait. pause() only
        # clears _resume_event, which says a pause was *asked for*, not that the node that
        # was running has finished -- see wait_until_parked.
        self._parked = asyncio.Event()
        self._ambient_combat_lock = asyncio.Lock()
        self._combat_auto_paused: bool = False  # True while watcher has auto-paused the FSM
        self._ambient_dialog_last_seen: float = 0.0  # monotonic timestamp of last dismissed banner
        self.last_result: str | None = None
        self._no_match_count: int = 0
        self._max_consecutive_same_state = max_consecutive_same_state
        self._consecutive_same_state_count: int = 0
        self._health_monitor: ClientHealthMonitor | None = None
        # Who can port to whom, surveyed before the run. See the port_map property.
        self._port_map: PortMap | None = None
        # Which node's log() calls are currently being attributed. See log().
        self._current_node_type: str | None = None
        # Run bookkeeping, for the summary logged when run() returns.
        self._pass_count: int = 0
        self._states_executed: int = 0
        self._results_seen: Counter[str] = Counter()
        # Options whose resolution has already been logged this run. See get_option().
        self._logged_option_sources: set[str] = set()
        # name -> BotOptionSpec, composed lazily. See _option_specs_for_bot().
        self._option_specs: dict | None = None

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
                    if "groups" in ci:
                        # Grouped format: evaluate each group then combine with groups_op.
                        group_results = []
                        for grp in ci["groups"]:
                            vals = [
                                (not bool(self.get_option(c["option"], default=False)))
                                if c["negate"]
                                else bool(self.get_option(c["option"], default=False))
                                for c in grp["flags"]
                            ]
                            group_results.append(any(vals) if grp["op"] == "OR" else all(vals))
                        return any(group_results) if ci["groups_op"] == "OR" else all(group_results)
                    # Legacy flat format.
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
        """Return the effective value of a bot option.

        Four levels, most local first -- the user's own value, the author's
        declaration, the app setting, then the node's default. The policy is in
        :mod:`bartlebot.bot_options`; this only gathers the inputs, so the
        precedence rules stay testable without a settings file or a bot.

        *default* is the caller's floor, used when nothing else has an answer --
        chiefly ``_flag_<name>`` asking about an option that no longer exists.

        Reads live from BotConfig, so a GUI edit takes effect without restarting
        the machine.
        """
        if self._bot_config is None:
            return default
        resolution = option_values.option_resolution(
            self._bot_config,
            name,
            option_specs=self._option_specs_for_bot(),
            default=default,
        )
        if resolution is None:
            return default
        # Once per option per run: get_option("Safety Delay") fires on nearly every
        # node, and a line each would be most of the session log.
        if name not in self._logged_option_sources:
            self._logged_option_sources.add(name)
            self._log_machine(f"Option {name!r} = {resolution.value!r} ({resolution.source})")
        return resolution.value

    def _option_specs_for_bot(self):
        """``{name: BotOptionSpec}`` for this bot, built once and kept for the run."""
        if self._option_specs is None:
            self._option_specs = bot_options.option_specs_for(self._bot_config)
        return self._option_specs

    # --- Logging ---

    def log(self, msg: str, level: int = logging.INFO) -> None:
        """Emit a log message from a node, attributed to that node.

        Every node logs through here, so before the log-readability work every one of these records was
        named "bartlebot.bot.machine" — four fifths of a session log under a single
        logger, with no way to ask what one node did except by matching message text.
        The record is named for the node that is running instead, which is what makes
        "everything quicksell did" a grep and lets the log widget filter by node.

        Outside a node's execution — the run loop's own bookkeeping — there is no node
        to name, and the machine's logger is the honest answer.
        """
        logging.getLogger(self._node_logger_name()).log(level, msg)

    def _node_logger_name(self) -> str:
        node_type = self._current_node_type
        return f"bartlebot.node.{node_type}" if node_type else logger.name

    def _log_machine(self, msg: str, level: int = logging.INFO) -> None:
        """Emit a message about the machine itself, never attributed to a node.

        Same signature as log() so the two read alike at the call site, but this one
        always lands on bartlebot.bot.machine. The run loop's own bookkeeping belongs
        there, and for the ambient watchers it is the only correct answer: they run
        concurrently with whatever node is executing, so attributing them to
        _current_node_type would file a watcher's message under a node that had
        nothing to do with it.
        """
        logger.log(level, msg)

    # --- Drop reporting ---

    def on_drop(self, item: str, quantity: int, client_index: int = 0) -> None:
        """Record a detected drop and notify the callback if one was provided."""
        m = _QTY_PREFIX.match(item)
        if m:
            item, quantity = m.group(2), quantity * int(m.group(1))
        self.drops.append((item, quantity, client_index))
        self._drops_seen.add((client_index, item.strip().lower()))
        if self._on_drop:
            try:
                self._on_drop(item, quantity, client_index)
            except Exception:
                logger.exception("on_drop callback raised for item=%r qty=%d", item, quantity)

    def has_drop(self, item: str, client_index: int) -> bool:
        """Return True if *item* was ever dropped by *client_index* in this run.

        Uses a dedicated set so the answer is correct even when self.drops has
        rotated old entries out of its bounded deque window.
        """
        return (client_index, item.strip().lower()) in self._drops_seen

    def chat_baseline(self, client) -> str | None:
        """The chat-log text *client*'s drops were last read from, or None if unseeded.

        None and "" mean different things. None is "never looked", and the caller must
        seed a baseline before diffing — treating it as "" would report every drop line
        already in the log. "" is a real baseline, from a client whose chat log was
        empty or unreadable at seeding time.
        """
        return self._chat_baselines.get(client)

    def set_chat_baseline(self, client, text: str) -> None:
        """Record the chat-log text *client*'s drops have been read up to.

        Callers pass back whatever ``poll_new_drops`` returned: the snapshot the drops
        came from on a hit, or their own baseline unchanged on a miss. Storing the
        unchanged one is what turns a missed announcement into a late record instead of
        a lost one — the next fight's poll still has the old baseline to diff against.
        """
        self._chat_baselines[client] = text

    # --- Public API ---

    @property
    def bot_config(self) -> "BotConfig | None":
        return self._bot_config

    @property
    def is_paused(self) -> bool:
        return not self._resume_event.is_set()

    @property
    def is_stopped(self) -> bool:
        return self._stop_flag.is_set()

    @property
    def is_ambient_combat_active(self) -> bool:
        """Whether the ambient combat watcher is currently running a fight.

        The watcher runs *concurrently* with the run loop, so a parked loop does not mean
        the clients are idle -- the watcher can be mid-fight on all of them. Anything that
        acts on the clients while the FSM is paused has to check both.
        """
        return self._ambient_combat_lock.locked()

    @property
    def health_monitor(self) -> "ClientHealthMonitor | None":
        """The monitor for the current run, or None when no run is in flight."""
        return self._health_monitor

    @property
    def port_map(self) -> "PortMap | None":
        """Who among this run's clients can port to whom, or None until surveyed.

        Set by pre-run setup. A node that needs it and finds None surveys for itself, which
        is what a run started outside the app (the node tester) gets.
        """
        return self._port_map

    @port_map.setter
    def port_map(self, value: "PortMap | None") -> None:
        self._port_map = value

    async def wait_until_parked(self, timeout: float) -> bool:
        """Wait for the run loop to reach its paused wait. True once it has.

        ``pause()`` clears an event and returns immediately, so ``is_paused`` goes true
        while the current node is still running -- for the whole of a fight, in the worst
        case. Closing a client on the strength of that kills it mid-node.

        False on timeout, and False when no run loop is active at all: a machine that was
        never started, or one already past its loop, never parks and callers must not wait
        out the timeout expecting otherwise.
        """
        if self._stop_flag.is_set():
            return False
        try:
            await asyncio.wait_for(self._parked.wait(), timeout)
        except TimeoutError:
            return False
        return True

    def suspend_health_checks_for(self, hwnds: Iterable[int]) -> None:
        """Stop ClientHealthMonitor reacting to these clients until the run resumes.

        Closing clients on purpose, or logging one out to character select, looks exactly
        like a disconnect to the watchers: ``zone_name()`` starts failing, loading screens
        appear, and the status watcher force-queues a recovery that is already under way.
        Flagging the hwnds is the monitor's own mechanism for "already dealt with".

        ``resume()`` clears the flags, so the suspension lifts on its own and nothing has
        to remember to undo it. A no-op when no run is in flight.
        """
        if self._health_monitor is None:
            return
        for hwnd in hwnds:
            self._health_monitor.flag(hwnd)

    def set_clients(self, clients: list) -> None:
        """Replace the active client list."""
        self.clients = clients
        # Drop baselines for clients that are gone would otherwise accumulate for the
        # life of the run. Surviving clients keep theirs, which is the point — a
        # reconnect elsewhere in the group must not cost the others their history.
        self._chat_baselines = {c: b for c, b in self._chat_baselines.items() if c in clients}

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

    # --- Run landmarks ---

    def _note_pass(self, state_id: str) -> None:
        """Log a landmark each time the FSM comes back round to its first state.

        A farming bot runs the same handful of states for hours, and without a
        landmark a log of it is thousands of interchangeable lines: "when did this
        start failing?" can only be answered by binary-searching timestamps. The
        pass number gives every question about a long run something to anchor to.

        This reads the bot's own shape rather than imposing one. A bot whose loop
        returns to its first state — which is most of them — gets numbered passes;
        one that loops back to somewhere in the middle gets a single pass 1, which
        is honest, since nothing in the graph says where its cycle begins.
        """
        if state_id != self._config["initial"]:
            return
        self._pass_count += 1
        logger.info("=== pass %d ===", self._pass_count)

    def _log_run_summary(self, elapsed: float) -> None:
        """One block at the end saying what the whole run did.

        Answering "how did that go?" otherwise means reading the entire file, since
        every fact about a run is spread across the ticks that produced it.
        """
        results = ", ".join(f"{result}={count}" for result, count in sorted(self._results_seen.items()))
        logger.info("=== run finished in %s ===", _format_duration(elapsed))
        logger.info("Passes: %d   States executed: %d", self._pass_count, self._states_executed)
        logger.info("Results: %s", results or "none")
        if self.drops:
            total = sum(quantity for _item, quantity, _client in self.drops)
            logger.info("Drops: %d item(s) over %d entries", total, len(self.drops))

    async def run(self) -> None:
        """Drive the FSM until stop() is called."""
        self._health_monitor = ClientHealthMonitor(
            clients_ref=lambda: self.clients,
            stop_flag=self._stop_flag,
            on_pause=self.pause,
            on_client_disconnected=self._on_client_disconnected,
            on_restricted_zone=self._handle_restricted_zone,
        )
        _run_start = time.monotonic()
        watcher = asyncio.create_task(self._ambient_dialog_watcher())
        combat_watcher = asyncio.create_task(self._ambient_combat_watcher())
        health_task = asyncio.create_task(self._health_monitor.run())
        try:
            while not self._stop_flag.is_set():
                if not self._resume_event.is_set():
                    # Announce the park only from here: this is the one point in the loop
                    # where no node is running, which is what a caller waiting to close
                    # clients out from under the FSM actually needs to know.
                    self._parked.set()
                    await self._resume_event.wait()  # block here while paused
                    self._parked.clear()
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
                self._note_pass(state_id)
                self._current_node_type = node_type
                self._states_executed += 1
                self._log_machine(f"Executing state {self._fmt_state(state_id)} (node_type={node_type})", logging.DEBUG)
                _tick_start = time.monotonic()
                node_fn = self._registry[node_type]
                self._current_node_task = asyncio.create_task(node_fn(self, self.clients, state_def.get("params", {})))
                try:
                    self.last_result = await self._current_node_task
                except asyncio.CancelledError:
                    if self._stop_flag.is_set():
                        # Our deferred_cancel fired after the grace period elapsed.
                        self._log_machine("Node forcibly cancelled after grace period.", logging.WARNING)
                        break
                    # run() was cancelled from outside — propagate.
                    raise
                except (ClientClosedError, ExceptionalTimeout, MemoryReadError) as exc:
                    # A client process has died. Pause immediately rather than treating
                    # this as a normal FAILURE — prevents the no-match / idle counters
                    # from hitting their limits before _client_loop detects the removal
                    # and triggers the recovery procedure.
                    # MemoryReadError (e.g. error 299 = ERROR_PARTIAL_COPY) occurs when the
                    # process dies before WizWalker's ClientClosedError fires; both indicate
                    # a dead game process and need the same recovery path.
                    # ExceptionalTimeout wraps a memory-read poll that timed out — same root
                    # cause when WizWalker's internal timeout fires before ClientClosedError.
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
                    self._current_node_type = None
                self._results_seen[self.last_result or "NONE"] += 1
                _duration = time.monotonic() - _tick_start
                self._log_machine(
                    f"State {self._fmt_state(state_id)} returned {self.last_result!r} in {_duration:.1f}s",
                    logging.DEBUG,
                )

                if not self._stop_flag.is_set():
                    prev_state = state_id
                    # A state with no outgoing transitions is a terminal state — stop cleanly.
                    has_exit = any(t["source"] == state_id for t in self._config["transitions"])
                    if not has_exit:
                        self._log_machine(
                            f"Reached terminal state {self._fmt_state(state_id)}; stopping.", logging.INFO
                        )
                        self.stop()
                    else:
                        fired = await self.advance()
                        if not fired:
                            self._no_match_count += 1
                            self._log_machine(
                                f"No transition matched for result {self.last_result!r} in state"
                                f" {self._fmt_state(state_id)} (consecutive: {self._no_match_count})",
                                logging.WARNING,
                            )
                            if self._no_match_count >= 10:
                                self._log_machine(
                                    f"State {self._fmt_state(state_id)} stuck after {self._no_match_count}"
                                    " consecutive no-match advances; stopping.",
                                    logging.ERROR,
                                )
                                self.stop()
                        else:
                            self._no_match_count = 0
                            self._log_machine(
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
                            self._log_machine(
                                f"State {self._fmt_state(state_id)} active for"
                                f" {self._consecutive_same_state_count} consecutive ticks"
                                " without advancing; stopping.",
                                logging.WARNING,
                            )
                            self.stop()
                    else:
                        self._consecutive_same_state_count = 0
        finally:
            self._current_node_type = None
            self._log_run_summary(time.monotonic() - _run_start)
            watcher.cancel()
            combat_watcher.cancel()
            health_task.cancel()
            for t in (watcher, combat_watcher, health_task):
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    async def _ambient_dialog_watcher(self) -> None:
        """Background task: dismiss any open dialog on all clients.

        Polls at POLL_INTERVAL normally. Once a banner is dismissed, switches to
        DIALOG_FAST_POLL_INTERVAL until a full Safety Delay window has elapsed with
        no further dialogue, so multi-banner conversations are advanced quickly.
        """
        from bartlebot.nodes import node_helpers
        from bartlebot.nodes.timing import DIALOG_FAST_POLL_INTERVAL

        while not self._stop_flag.is_set():
            safety_delay: float = self.get_option("Safety Delay", 3.0)
            in_sequence = (time.monotonic() - self._ambient_dialog_last_seen) < safety_delay
            await asyncio.sleep(DIALOG_FAST_POLL_INTERVAL if in_sequence else node_helpers.POLL_INTERVAL)
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
                    self._ambient_dialog_last_seen = time.monotonic()
                    self._log_machine(f"Ambient dialog dismissed on client {i}", logging.DEBUG)
                elif isinstance(r, Exception):
                    self._log_machine(f"Ambient dialog watcher error on client {i}: {r}", logging.DEBUG)

    async def _ambient_combat_watcher(self) -> None:
        """Background task: detect and handle unexpected combat on any client.

        Runs SprintyCombat using the bot's default combat config whenever a client
        enters combat outside of an explicit handle_combat node. Guarded against
        re-entry and against conflicting with intentional combat nodes.
        """
        from bartlebot.nodes import node_helpers
        from bartlebot.nodes.combat_runner import (
            _run_combat,
            poll_combat_drops,
            resolve_combat,
            seed_chat_baselines,
            slots_in_battle,
        )

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
            # Track slots (indices into `clients`), not just the clients themselves —
            # a client's index is its player slot, and combat DSL is resolved per slot.
            battling_slots = slots_in_battle(in_battle_flags)
            if not battling_slots:
                continue

            try:
                async with self._ambient_combat_lock:
                    # Re-check state after acquiring lock — FSM may have entered handle_combat
                    # between the pre-lock check above and here (in_battle gather is an await point).
                    try:
                        if self._state_map.get(self.state, {}).get("node_type") == "handle_combat":
                            continue
                    except AttributeError:
                        continue

                    # Pause only after confirming inside the lock that we need to handle combat,
                    # so a spurious pause never happens if the FSM slipped into handle_combat
                    # while we were awaiting in_battle_flags.
                    if self._resume_event.is_set():
                        self._combat_auto_paused = True
                        self.pause()
                        self._log_machine("Ambient combat: FSM paused — waiting for combat to finish", logging.INFO)

                    self._log_machine(
                        f"Ambient combat detected on client(s) {battling_slots} — handling",
                        logging.WARNING,
                    )

                    # Resolve for every client so per_client_dsl is indexed by slot;
                    # resolving for only the battling ones would give client 3 the
                    # DSL configured for player 1.
                    per_client_dsl, _combat_order, dsl_source = resolve_combat(
                        self, use_default_strategy=True, num_clients=len(clients)
                    )

                    self._log_machine(f"Ambient combat: DSL source={dsl_source!r}", logging.DEBUG)

                    battling = [clients[i] for i in battling_slots]
                    # Seed only clients with no baseline yet; one carried from an earlier
                    # fight is what recovers an announcement that arrived too late for it.
                    await seed_chat_baselines(self, battling)
                    results = await asyncio.gather(
                        *[_run_combat(clients[i], per_client_dsl[i]) for i in battling_slots],
                        return_exceptions=True,
                    )
                    for pos, r in enumerate(results):
                        if isinstance(r, Exception):
                            self._log_machine(
                                f"Ambient combat: error on client {battling_slots[pos]}: {r}",
                                logging.DEBUG,
                            )

                    self._log_machine("Ambient combat: SprintyCombat finished", logging.DEBUG)

                    drops_found = await poll_combat_drops(self, battling, battling_slots)
                    for item_name in drops_found:
                        self._log_machine(f"Ambient combat drop: {item_name}")
            finally:
                if self._combat_auto_paused:
                    self._combat_auto_paused = False
                    self.resume()
                    self._log_machine("Ambient combat: FSM resumed", logging.INFO)

    def _handle_restricted_zone(self, hit: "restricted_zones.Restriction") -> None:
        """Stop the run because a client is somewhere it must not be automated.

        Stopping is done here rather than left to the GUI so the FSM halts even when
        no GUI is attached, and so it happens before the notification: the callback
        below tears the hooks out, and the bot must not still be issuing node actions
        while that runs.
        """
        self._log_machine(f"Bot stopped: {hit.describe()} is a restricted zone.", logging.CRITICAL)
        self.stop()
        if self._on_restricted_zone:
            try:
                self._on_restricted_zone(hit.describe())
            except Exception:
                logger.exception("on_restricted_zone callback raised")

    def stop(self) -> None:
        """Signal the FSM to stop.

        Sets the stop flag so cooperative nodes can exit at their next polling checkpoint.
        After stop_grace_period seconds, any still-running node task is forcibly cancelled
        so the bot always halts promptly regardless of what the node is doing.
        """
        if not self._stop_flag.is_set():
            self._log_machine("Bot stopped.", logging.INFO)
        self._stop_flag.set()
        self._parked.clear()
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
        """Suspend the FSM after the current node finishes.

        Returns immediately. The loop has not necessarily parked yet -- await
        wait_until_parked for that.
        """
        self._resume_event.clear()

    def resume(self) -> None:
        """Resume a paused FSM."""
        if self._health_monitor is not None:
            self._health_monitor.clear_flagged()  # allow re-detection after client recovery
        self._parked.clear()
        self._resume_event.set()

    def resume_health_checks_for(self, hwnds: Iterable[int]) -> None:
        """Undo suspend_health_checks_for, one client at a time.

        A relog that happens *inside* a node has to put the watchers back itself: nothing
        calls resume() on the way out, so without this the clients it touched would go
        unwatched for the rest of the run.
        """
        if self._health_monitor is None:
            return
        for hwnd in hwnds:
            self._health_monitor.unflag(hwnd)

    def force_state(self, state_id: str) -> None:
        """Force the FSM into a specific state for recovery. Bypasses normal entry callbacks."""
        self.machine.set_state(state_id)  # sets self.state via the transitions machine
        self._consecutive_same_state_count = 0
        self._no_match_count = 0
        logger.info("FSM state forced to %s (recovery)", self._fmt_state(state_id))
