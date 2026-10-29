import asyncio
import contextvars
import enum
import functools
import logging
import re
import time
from collections import Counter, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from bartleby.runtime import run_trace
from bartleby.runtime.clients import sightings
from transitions.extensions.asyncio import AsyncMachine
from wizwalker.errors import ClientClosedError, ExceptionalTimeout, MemoryReadError

from bartlebot.bot import variables
from bartlebot.bot.client_health_monitor import ClientHealthMonitor
from bartlebot.bot.schema import FALLTHROUGH_RETURNS, AssignmentModel, VariableModel
from bartlebot.game import restricted_zones
from bartlebot.options import bot_options, option_values

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
    from bartlebot.clients.port_map import PortMap

_QTY_PREFIX = re.compile(r"^(\d+)\s+(.+)$")

# Node types the ambient combat watcher never cancels. handle_combat is already fighting
# (the watcher leaves its fights alone entirely); relog is itself the recovery, and waits out a
# fight before logging anyone out (#203).
NEVER_PREEMPTED = frozenset({"handle_combat", "relog"})

# The node type a log() call is filed under: set in the running node's own task, and so in
# every task it starts, and nowhere else. A field on the machine said only that some node was
# running, so a watcher logging beside it was filed under that node. See log().
_NODE_TYPE: contextvars.ContextVar[str | None] = contextvars.ContextVar("bartlebot_node_type", default=None)


class VariableWriteRefused(RuntimeError):
    """A variable write from a task that may not make one: anything but the running node's and the run loop's."""


class PauseHold(enum.StrEnum):
    """Who is holding the FSM paused. It runs only while nobody is.

    Each party that pauses releases only its own hold, so the end of an ambient fight cannot
    resume a bot the user paused during it, or one crash recovery is relaunching a client for.
    The user's Resume is the exception: it lifts every hold, as it always has, which is how a
    bot left paused by a recovery that cannot finish is set going again.
    """

    USER = "user"
    """The Pause button."""
    RECOVERY = "recovery"
    """A client died, or is being relaunched. Crash recovery releases it once every client is back."""
    RELOG = "relog"
    """A relog waiting for a node boundary. Handed to RECOVERY once its clients are closed."""
    AMBIENT = "ambient"
    """The ambient combat watcher, for the length of a fight."""
    IDLE = "idle"
    """The idle-popup watcher, while it cancels the running node and sends the bot to recovery."""


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
        on_explicit_stop: Callable[[str, str, str], None] | None = None,
        on_stopped: Callable[[str], None] | None = None,
        recovery_gap: Callable[[int], str | None] | None = None,
        on_variable_change: Callable[[str, Any, Any], None] | None = None,
        on_fight_fled: Callable[[str | None, Any], None] | None = None,
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
        self._on_explicit_stop = on_explicit_stop
        self._on_stopped = on_stopped
        self._on_variable_change = on_variable_change
        self._on_fight_fled = on_fight_fled
        # Why a client could not be relaunched, or None when it could. See abandon_client.
        self._recovery_gap = recovery_gap
        # When each recent flee happened (monotonic), for the flee limit. See note_flee.
        self._flees: list[float] = []
        # True while run()'s loop is live, so the ambient watcher knows whether a park can come.
        self._running: bool = False
        self._stop_flag = asyncio.Event()
        self._stop_grace_period = stop_grace_period
        self._current_node_task: asyncio.Task | None = None
        # run()'s own task while it runs. With the node's, the only task that may write a
        # variable -- see set_variable.
        self._run_task: asyncio.Task | None = None
        self.drops: deque[tuple[str, int, int]] = deque(maxlen=10_000)  # (item, quantity, client_index)
        # Per-client chat-log baseline that drop detection diffs against. Keyed by the
        # client object rather than its slot: ClientManager.swap_clients reorders slots
        # mid-run, and a baseline describes what a particular game window has already
        # said. Shared by handle_combat and the ambient combat watcher so the two do not
        # absorb each other's announcements.
        self._chat_baselines: dict[object, str] = {}
        # The last fight's drop poll, left running so the FSM can move on. See start_drop_poll.
        self._drop_poll: asyncio.Task | None = None
        self._resume_event = asyncio.Event()
        self._resume_event.set()  # not paused by default
        # Who is holding the FSM paused; _resume_event is set exactly when this is empty. See PauseHold.
        self._holds: set[PauseHold] = set()
        # Windows handed to crash recovery since the run last resumed. See _await_dead_client.
        self._lost_hwnds: set[int] = set()
        # Windows back in the world from a login that nothing has moved since. See note_logged_in.
        self._fresh_logins: set[int] = set()
        # Set by the run loop once it has actually reached its paused wait. pause() only
        # clears _resume_event, which says a pause was *asked for*, not that the node that
        # was running has finished -- see wait_until_parked.
        self._parked = asyncio.Event()
        self._ambient_combat_lock = asyncio.Lock()
        # The ambient combat session in progress, so the idle watcher can tell it of a flee.
        self._ambient_session = None
        # Set when the ambient combat watcher cancels the running node to take over a fight.
        # The run loop then runs that state again rather than counting a result. See
        # _preempt_node.
        self._preempted: bool = False
        # True while the loop is running a state again after a preemption, so the rerun is
        # not logged as a new pass.
        self._rerunning: bool = False
        self._ambient_dialog_last_seen: float = 0.0  # monotonic timestamp of last dismissed banner
        self.last_result: str | None = None
        self._no_match_count: int = 0
        self._max_consecutive_same_state = max_consecutive_same_state
        self._consecutive_same_state_count: int = 0
        self._health_monitor: ClientHealthMonitor | None = None
        # Who can port to whom, surveyed before the run. See the port_map property.
        self._port_map: PortMap | None = None
        # What a node keeps for its own rerun, by node type. A node cancelled by the ambient
        # combat watcher is run again from the start (see bartlebot/nodes/CLAUDE.md), and this
        # is the only thing that outlives the cancel: handle_level_scaling keeps where each
        # client marked, so the rerun does not mark again somewhere on the way to the Scales.
        # One entry is run-wide rather than a node's own: mark_location.MARKS_KEY, where each
        # client last marked in this run, written by every mark.
        self.node_scratch: dict[str, object] = {}
        # Which clients have been seen sharing a zone, realm and instance. Keeps only the
        # latest check until run() starts the loading watcher that says when one goes stale.
        self._together = sightings.TogetherRecord(retain=False)
        # Run bookkeeping, for the summary logged when run() returns.
        self._pass_count: int = 0
        self._states_executed: int = 0
        self._results_seen: Counter[str] = Counter()
        # Options whose resolution has already been logged this run. See get_option().
        self._logged_option_sources: set[str] = set()
        # name -> BotOptionSpec, composed lazily. See _option_specs_for_bot().
        self._option_specs: dict | None = None

        self._state_map = {s["id"]: s for s in config["states"]}
        # Where an unrouted FALLTHROUGH_RETURNS port goes. Absent only for a config
        # hand-built in a test; the fall-through is then simply off.
        self._recovery_state: str | None = config.get("recovery_state")
        # Compiled compound flag expressions from to_machine_config().
        # Keys are _flag_compound_<idx> strings; values are {"flags": [...], "op": "AND"|"OR"}.
        self._conditions_registry: dict[str, dict] = config.get("conditions_registry", {})
        # The bot's variables (docs/VARIABLES.md): declarations by name, and what each holds
        # now. Every value starts at its declaration's initial value when run() begins, and
        # after that only set_variable changes one -- no recovery jump, rerun or watcher.
        self._variable_decls: dict[str, VariableModel] = {
            d.name: d for d in (VariableModel.model_validate(v) for v in config.get("variables", []))
        }
        self._variables: dict[str, Any] = {}
        self._reset_variables()
        # The variable-only nodes run in a row, most recent last. See MAX_VARIABLE_ONLY_STEPS.
        self._variable_only_streak: list[str] = []

        # Each conditioned transition's condition as text, by the before-callback that notes
        # it was taken, and the one taken by the advance in flight. See _machine_transitions.
        self._guards: dict[str, str] = {}
        self._taken_guard: str | None = None
        # Each assigning transition's rows, by the after-callback that notes it was taken, and
        # the rows of the one taken by the advance in flight. See _apply_taken_assignments.
        self._assigns: dict[str, list[AssignmentModel]] = {}
        self._taken_assign: list[AssignmentModel] | None = None

        states = [s["id"] for s in config["states"]]
        transitions = self._machine_transitions(config["transitions"])

        self.machine = AsyncMachine(
            model=self,
            states=states,
            transitions=transitions,
            initial=config["initial"],
            auto_transitions=False,
            ignore_invalid_triggers=False,
        )

    # --- Transition conditions ---

    def _machine_transitions(self, transitions: list[dict]) -> list[dict]:
        """The config's transitions as ``transitions`` takes them.

        Three keys are the machine's own and are taken off: ``guard``, the condition as
        text; ``assign``, the edge's writes; and ``label``, an author's name for the edge.
        ``transitions`` refuses any argument it does not know. A guarded transition gets a
        before-callback in its place, which runs only when that transition is the one taken,
        so the log can name the condition that chose it -- two edges from one port can go
        to the same node on different conditions. An assigning one gets an after-callback
        the same way, so its writes are applied for that edge and no other.
        """
        out = []
        for idx, raw in enumerate(transitions):
            t = {k: v for k, v in raw.items() if k not in ("guard", "assign", "label")}
            if guard := raw.get("guard"):
                callback = f"_took_guard_{idx}"
                self._guards[callback] = guard
                before = t.get("before") or []
                t["before"] = [*([before] if isinstance(before, str) else before), callback]
            if rows := raw.get("assign"):
                callback = f"_assign_{idx}"
                self._assigns[callback] = [AssignmentModel.model_validate(r) for r in rows]
                after = t.get("after") or []
                t["after"] = [*([after] if isinstance(after, str) else after), callback]
            out.append(t)
        return out

    def __getattr__(self, name: str):
        """Dynamically provide condition methods for transitions.

        _result_is_<VALUE>    — BotConfig.to_machine_config() converts when="SUCCESS"
                                into this condition name.
        _flag_<NAME>          — fires when bot option <NAME> is truthy.
        _flag_compound_<IDX>  — evaluates a compiled multi-flag AND/OR/NOT expression
                                stored in self._conditions_registry, whose rows read
                                options and variables alike (_condition_row_holds).
        _took_guard_<IDX>     — a before-callback, not a condition: notes that a guarded
                                transition was taken (_machine_transitions).
        _assign_<IDX>         — an after-callback: hands the run loop the writes of the
                                transition just taken (_apply_taken_assignments).
        """
        if name.startswith("_took_guard_"):
            guard = self.__dict__.get("_guards", {}).get(name)
            if guard is not None:

                def _note_taken(g=guard):
                    self._taken_guard = g

                return _note_taken
        if name.startswith("_assign_"):
            rows = self.__dict__.get("_assigns", {}).get(name)
            if rows is not None:

                def _note_assign(r=rows):
                    self._taken_assign = r

                return _note_assign
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
                            vals = [self._condition_row_holds(c) for c in grp["flags"]]
                            group_results.append(any(vals) if grp["op"] == "OR" else all(vals))
                        return any(group_results) if ci["groups_op"] == "OR" else all(group_results)
                    # Legacy flat format.
                    results = [self._condition_row_holds(c) for c in ci["flags"]]
                    return any(results) if ci["op"] == "OR" else all(results)

                return _eval_compound
        if name.startswith("_flag_"):
            option_name = name[len("_flag_") :]
            return lambda: bool(self.get_option(option_name, default=False))
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def _condition_row_holds(self, row: dict) -> bool:
        """Whether one compiled condition row is true: an option's truthiness, or a variable's comparison."""
        if "variable" in row:
            return variables.compare(self._variables.get(row["variable"]), row["compare"], row["value"])
        value = bool(self.get_option(row["option"], default=False))
        return not value if row["negate"] else value

    # --- Variables ---

    @property
    def variables(self) -> Mapping[str, Any]:
        """What each of the bot's variables holds now, by name. Read-only: set_variable is the one write."""
        return MappingProxyType(self._variables)

    @property
    def variable_declarations(self) -> Mapping[str, VariableModel]:
        """The bot's variable declarations, by name."""
        return MappingProxyType(self._variable_decls)

    def _reset_variables(self) -> None:
        """Every variable back to its initial value. Only ever as a run starts."""
        self._variables = {name: decl.initial_value() for name, decl in self._variable_decls.items()}

    def set_variable(self, name: str, value: Any) -> None:
        """Write one variable, and log the change once.

        The only write there is, and only from the task running the current node or from
        the run loop itself. The ambient watchers run beside a node and a node's game code
        runs once per client in parallel, so either writing would let two of them change one
        value -- a write from any other task raises :class:`VariableWriteRefused`. A name
        the bot does not declare, or a value its type cannot hold, raises
        :class:`bartlebot.bot.variables.VariableError`.

        Contains no ``await``, so a cancel can never land halfway through a write.
        """
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        if task is None or task not in (self._current_node_task, self._run_task):
            raise VariableWriteRefused(
                f"variable {name!r} can only be written by the running node or the run loop, not from another task"
            )
        decl = self._variable_decls.get(name)
        if decl is None:
            raise variables.VariableError(f"there is no variable named {name!r}")
        if not variables.value_fits(decl, value):
            raise variables.VariableError(f"{name!r} cannot hold {variables.format_value(value)}")
        old = self._variables.get(name)
        if type(old) is type(value) and old == value:
            self._log_machine(f"Variable {name} stays {variables.format_value(value)}", logging.DEBUG)
            return
        self._variables[name] = value
        self._log_machine(f"Variable {name}: {variables.format_value(old)} → {variables.format_value(value)}")
        if self._on_variable_change:
            try:
                self._on_variable_change(name, old, value)
            except Exception:
                logger.exception("on_variable_change callback raised for %r", name)

    def _apply_taken_assignments(self) -> None:
        """Apply the writes of the edge the last advance took, if it had any (#189).

        Called by the run loop right after it logs the transition, so the log reads in the
        order things happened: the edge, then each write it made, once. Only the edge taken
        noted its rows, so another edge from the same port writes nothing, and a jump to
        recovery takes no edge at all. Every value is worked out before any is written, and
        nothing here awaits, so the writes land whole or not at all. A write validation
        should have refused stops the run instead of half-applying.
        """
        rows, self._taken_assign = self._taken_assign, None
        if not rows:
            return
        try:
            changed = variables.apply_assignments(self._variables, self._variable_decls, rows)
        except variables.VariableError as exc:
            self._stop_with_reason(f"An edge into {self._fmt_state(self.state)} could not set its variables: {exc}")
            return
        for name, value in changed.items():
            self.set_variable(name, value)

    def _note_variable_only_step(self, state_id: str, node_type: str) -> bool:
        """Count a step toward the variable-only limit. True when this one stopped the run.

        A wrong condition between two set_variables or branch nodes loops without touching
        the game. Neither the no-match count nor the same-state count sees it, because every
        step matches and the state keeps changing.
        """
        if node_type not in variables.VARIABLE_ONLY_NODE_TYPES:
            self._variable_only_streak.clear()
            return False
        self._variable_only_streak.append(state_id)
        if len(self._variable_only_streak) <= variables.MAX_VARIABLE_ONLY_STEPS:
            return False
        looping = ", ".join(self._fmt_state(s) for s in dict.fromkeys(self._variable_only_streak))
        self._stop_with_reason(
            f"{len(self._variable_only_streak)} variable-only nodes ran in a row without touching the game,"
            f" going round {looping}. Check the conditions on their edges."
        )
        return True

    # --- Options ---

    def get_option(self, name: str, default=None):
        """Return the effective value of a bot option.

        Four levels, most local first -- the user's own value, the author's
        declaration, the app setting, then the node's default. The policy is in
        :mod:`bartlebot.options.bot_options`; this only gathers the inputs, so the
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
        to name, and the machine's logger is the honest answer. So is it for the ambient
        watchers, and for the session and helpers they share with handle_combat: they run
        beside whatever node is executing, and are filed under it only when the call is made
        from the node's own task, or one it started (_NODE_TYPE).
        """
        logging.getLogger(self._node_logger_name()).log(level, msg)

    def _node_logger_name(self) -> str:
        node_type = _NODE_TYPE.get()
        return f"bartlebot.node.{node_type}" if node_type else logger.name

    def _log_machine(self, msg: str, level: int = logging.INFO) -> None:
        """Emit a message about the machine itself, never attributed to a node.

        Same signature as log() so the two read alike at the call site, but this one
        always lands on bartlebot.bot.machine. The run loop's own bookkeeping belongs
        there, and for the ambient watchers it is the only correct answer: they run
        concurrently with whatever node is executing, so attributing them to it would
        file a watcher's message under a node that had nothing to do with it.
        """
        logger.log(level, msg)

    # --- Drop reporting ---

    def on_drop(self, item: str, quantity: int, client_index: int = 0) -> None:
        """Record a detected drop and notify the callback if one was provided."""
        m = _QTY_PREFIX.match(item)
        if m:
            item, quantity = m.group(2), quantity * int(m.group(1))
        self.drops.append((item, quantity, client_index))
        if self._on_drop:
            try:
                self._on_drop(item, quantity, client_index)
            except Exception:
                logger.exception("on_drop callback raised for item=%r qty=%d", item, quantity)

    def note_fight_fled(self, state_id: str | None, level) -> None:
        """A node's fight was fled: tell the callback which state, and the ``combat_resolution.Level`` it ran.

        handle_combat calls it, beside ``note_flee``. The Combat tab marks a fight fled on
        a default (Stage 7 of epic #201); the level is passed whatever it is, and the
        callback decides. Never an ambient or an idle-popup flee: neither is a fight of
        the bot's, so the tab would have nowhere to put the mark.
        """
        if self._on_fight_fled:
            try:
                self._on_fight_fled(state_id, level)
            except Exception:
                logger.exception("on_fight_fled callback raised for state %s", state_id)

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

    def start_drop_poll(self, task: asyncio.Task) -> None:
        """Adopt *task* as the fight's drop poll, cancelling the previous fight's.

        A fight hands its poll here instead of awaiting it: an announcement can take many
        seconds to arrive, and on a fight that dropped nothing the poll waits out its whole
        window. Nothing downstream needs the answer at once; anything that does waits with
        ``wait_for_drop_poll``.

        Cancelling the old poll loses nothing. It only moves a baseline forward on a hit,
        and records that hit with no await in between, so a cancel lands either before
        it saw the line — leaving the line ahead of the baseline for the new poll — or
        after it has been fully recorded. Letting both run would diff the same baseline
        twice and report the same drop twice.
        """
        if self._drop_poll is not None:
            self._drop_poll.cancel()
        self._drop_poll = task

    async def wait_for_drop_poll(self) -> None:
        """Wait until the last fight's drop poll has finished. Returns at once if none is running.

        Waiting never cancels the poll, even if the waiter is itself cancelled.
        """
        if self._drop_poll is not None and not self._drop_poll.done():
            await asyncio.wait({self._drop_poll})

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

        False on timeout. A machine with no run loop active never parks, so for one never
        started, or already past its loop, this waits out the whole timeout and returns False:
        a caller must not read that as a node that would not stop. A run just created as a
        task counts as started, which is why this does not return early on one. A stop while
        waiting returns False at once, since a loop that stops breaks out without parking.
        """
        if self._stop_flag.is_set():
            return False
        parked = asyncio.ensure_future(self._parked.wait())
        stopped = asyncio.ensure_future(self._stop_flag.wait())
        try:
            await asyncio.wait({parked, stopped}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            parked.cancel()
            stopped.cancel()
        return self._parked.is_set() and not self._stop_flag.is_set()

    def suspend_health_checks_for(self, hwnds: Iterable[int], *, watch_disconnects: bool = False) -> None:
        """Stop ClientHealthMonitor reacting to these clients until the run resumes.

        Closing clients on purpose, or logging one out to character select, looks exactly
        like a disconnect to the watchers: ``zone_name()`` starts failing, loading screens
        appear, and the status watcher force-queues a recovery that is already under way.
        Flagging the hwnds is the monitor's own mechanism for "already dealt with".

        *watch_disconnects* is for a logout, which keeps the clients running: the disconnect
        dialog watcher stays on for them (``ClientHealthMonitor.suspend``), since a client
        that loses its connection mid-relog can only come back by being relaunched. Closing
        clients leaves it False, as the clients are going away regardless.

        ``resume()`` clears the flags, so the suspension lifts on its own and nothing has
        to remember to undo it. A no-op when no run is in flight.
        """
        if self._health_monitor is None:
            return
        for hwnd in hwnds:
            if watch_disconnects:
                self._health_monitor.suspend(hwnd)
            else:
                self._health_monitor.flag(hwnd)

    # --- Who shares a realm ---

    def note_sightings(self, result: "sightings.Sightings") -> None:
        """Fold a check from ``node_helpers.read_sightings`` into the record of who is together."""
        self._together.note(result)

    def are_together(self, clients: list) -> bool:
        """Whether every one of *clients* has been seen sharing one zone, realm and instance,
        with none of them loading since. True for a single client.

        False means "not known to be", not "known not to be": ask ``read_sightings`` for that.
        """
        return self._together.are_together(clients)

    def together_with(self, client, clients: list) -> list:
        """Those of *clients* known to share *client*'s zone, realm and instance, itself included."""
        return self._together.together_with(client, clients)

    def forget_sightings(self, client) -> None:
        """Drop *client* from the record of who is together: it has loaded, so its realm may have changed."""
        self._together.forget(client)

    def set_clients(self, clients: list) -> None:
        """Replace the active client list."""
        self.clients = clients
        self._together.drop_clients(clients)
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

    def node_type_of(self, state_id: str) -> str | None:
        """The node type *state_id* runs, or None for a state this machine does not have."""
        return self._state_map.get(state_id, {}).get("node_type")

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
        if self._variables:
            final = ", ".join(f"{name} = {variables.format_value(value)}" for name, value in self._variables.items())
            logger.info("Variables: %s", final)

    async def run(self) -> None:
        """Drive the FSM until stop() is called."""
        self._health_monitor = ClientHealthMonitor(
            clients_ref=lambda: self.clients,
            stop_flag=self._stop_flag,
            on_pause=functools.partial(self.pause, PauseHold.RECOVERY),
            on_client_disconnected=self._client_lost,
            on_restricted_zone=self._handle_restricted_zone,
            on_loading=self.forget_sightings,
        )
        # The watcher now says when a sighting goes stale, so older ones can be kept.
        self._together.retain = True
        _run_start = time.monotonic()
        self._running = True
        self._run_task = asyncio.current_task()
        self._lost_hwnds.clear()
        self._fresh_logins.clear()
        self._reset_variables()
        self._variable_only_streak.clear()
        watcher = asyncio.create_task(self._ambient_dialog_watcher())
        combat_watcher = asyncio.create_task(self._ambient_combat_watcher())
        idle_watcher = asyncio.create_task(self._combat_idle_watcher())
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
                if node_type != "relog":
                    # Any other node may take a player into an instance, so a login no longer
                    # says where anyone is. See note_logged_in.
                    self._fresh_logins.clear()
                if self._note_variable_only_step(state_id, node_type):
                    break
                if not self._rerunning:
                    self._note_pass(state_id)
                self._rerunning = False
                self._states_executed += 1
                self._log_machine(f"Executing state {self._fmt_state(state_id)} (node_type={node_type})", logging.DEBUG)
                _tick_start = time.monotonic()
                node_fn = self._registry[node_type]
                node_clients = list(self.clients)
                # The node's own context, which every task it starts copies: log() files under it.
                context = contextvars.copy_context()
                context.run(_NODE_TYPE.set, node_type)
                node_coro = node_fn(self, self.clients, state_def.get("params", {}))
                if run_trace.active():
                    node_coro = run_trace.traced(node_coro, f"node.{node_type}", state=state_id)
                self._current_node_task = asyncio.create_task(node_coro, context=context)
                try:
                    self.last_result = await self._current_node_task
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise  # run() itself was cancelled from outside
                    if self._stop_flag.is_set():
                        # Our deferred_cancel fired after the grace period elapsed.
                        self._log_machine("Node forcibly cancelled after grace period.", logging.WARNING)
                        break
                    if self._preempted:
                        # The ambient combat watcher took the node's clients for a fight.
                        # No result and no transition: park, and on resume run this state
                        # again from the start. The counters are left alone.
                        self._preempted = False
                        self._rerunning = True
                        self._log_machine(
                            f"State {self._fmt_state(state_id)} interrupted by a fight; it runs again afterwards",
                            logging.INFO,
                        )
                        continue
                    # Nobody cancelled the node: a CancelledError escaped from inside it, such as
                    # an await on a task something else cancelled. Taken for run()'s own
                    # cancellation, it ended the run with no reason given.
                    logger.error(
                        "Node in state %s ended with a cancellation nothing asked for; counting it as a FAILURE",
                        self._fmt_state(state_id),
                        exc_info=True,
                    )
                    self.last_result = "FAILURE"
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
                    # Both can also come from a live client, so the death is confirmed before
                    # the run waits on a recovery: see _await_dead_client.
                    logger.warning(
                        "%s in state %s — client may have died, pausing for recovery",
                        type(exc).__name__,
                        self._fmt_state(state_id),
                    )
                    if await self._await_dead_client(node_clients):
                        self._no_match_count = 0
                        self._consecutive_same_state_count = 0
                        continue
                    self.last_result = "FAILURE"
                except Exception:
                    logger.exception("Node execution failed in state %s", self._fmt_state(state_id))
                    self.last_result = "FAILURE"
                finally:
                    self._current_node_task = None
                self._results_seen[self.last_result or "NONE"] += 1
                _duration = time.monotonic() - _tick_start
                self._log_machine(
                    f"State {self._fmt_state(state_id)} returned {self.last_result!r} in {_duration:.1f}s",
                    logging.DEBUG,
                )

                if not self._stop_flag.is_set():
                    prev_state = state_id
                    # Before anything else: an author-marked stop is deliberate, and
                    # outranks both the terminal-state check and any edge that would
                    # otherwise carry this result onward.
                    stop_reason = self._explicit_stop_reason(state_id, self.last_result)
                    if stop_reason is not None:
                        self._handle_explicit_stop(state_id, self.last_result or "", stop_reason)
                        continue
                    # A state with no outgoing transitions is a terminal state — stop cleanly.
                    has_exit = any(t["source"] == state_id for t in self._config["transitions"])
                    if not has_exit:
                        self._log_machine(
                            f"Reached terminal state {self._fmt_state(state_id)}; stopping.", logging.INFO
                        )
                        self.stop()
                    else:
                        self._taken_guard = None
                        self._taken_assign = None
                        fired = await self.advance()
                        recovery_dest = None if fired else self._fallthrough_dest(state_id, self.last_result)
                        if fired:
                            self._no_match_count = 0
                            guard = f" [{self._taken_guard}]" if self._taken_guard else ""
                            if self._taken_assign:
                                # A statechart's ``event [guard] / action``: the arrows are the log's own.
                                guard += f" / {variables.describe_assignments(self._taken_assign)}"
                            self._log_machine(
                                f"Transition: {self._fmt_state(prev_state)} --{self.last_result}{guard}-->"
                                f" {self._fmt_state(self.state)}",
                                logging.INFO,
                            )
                            self._apply_taken_assignments()
                        elif recovery_dest is not None:
                            # force_state, not a synthesised transition: there is no
                            # TransitionModel behind this edge, and inventing one would
                            # put it in the file the next time the graph is saved.
                            # It clears the no-match counter for us.
                            self.force_state(
                                recovery_dest,
                                reason=(
                                    f"Unrouted {self.last_result} in state {self._fmt_state(state_id)}"
                                    f" — recovering to {self._fmt_state(recovery_dest)}"
                                ),
                            )
                        else:
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
            self._running = False
            self._run_task = None
            self._log_run_summary(time.monotonic() - _run_start)
            watcher.cancel()
            combat_watcher.cancel()
            idle_watcher.cancel()
            health_task.cancel()
            # With the watcher gone nothing can say when a sighting goes stale.
            self._together = sightings.TogetherRecord(retain=False)
            if self._drop_poll is not None:
                self._drop_poll.cancel()
            for t in (watcher, combat_watcher, idle_watcher, health_task):
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    def _client_lost(self, hwnd: int) -> None:
        """The health monitor's word that *hwnd* has been handed to crash recovery. Passed on to the callback."""
        self._lost_hwnds.add(hwnd)
        if self._on_client_disconnected:
            try:
                self._on_client_disconnected(hwnd)
            except Exception:
                logger.exception("on_client_disconnected callback raised for hwnd=%d", hwnd)

    def note_logged_in(self, hwnds: Iterable[int]) -> None:
        """Record that these players have just come back into the world from a login.

        A relaunch, recovery's Esc → Quit and the relog node all say so. A login leaves a player
        outside any instance, on a fresh session, which is everything a relog is for, so until
        the player is moved the relog node skips it (``is_freshly_logged_in``). That is what
        stops a recovery state of Relog, which nearly every shipped bot has, logging out a team
        recovery has just logged in.

        Moved means the run loop has started any node but relog, or an ambient session has had
        the player fight or help. Both forget the record.
        """
        self._fresh_logins.update(hwnds)

    def is_freshly_logged_in(self, client) -> bool:
        """Whether *client* is back from a login and nothing has moved it since. See note_logged_in."""
        return client.window_handle in self._fresh_logins

    def _a_client_is_lost(self, clients: list) -> bool:
        """Whether any of *clients* has left the client list, or been handed to crash recovery."""
        return any(c not in self.clients or c.window_handle in self._lost_hwnds for c in clients)

    async def _await_dead_client(self, clients: list) -> bool:
        """Hold the run for recovery after a dead-client error, once a client is shown to be gone.

        True when one of *clients*, the node's, is gone, or the run stopped: the loop then parks
        without counting the tick, and recovery resumes it. False when every one of them is still
        there after DEAD_CLIENT_CONFIRM_TIMEOUT: the error came from a live client, a window
        torn down mid-read in a zone change or a lagging one, and nothing is coming to resume the
        run, so the hold is released and the caller counts the node's FAILURE. Parked on such an
        error, a run used to sit paused for good.

        Holds the run paused while it waits, so neither combat watcher acts on a dying client.
        """
        from bartleby.runtime.timing import DEAD_CLIENT_CONFIRM_TIMEOUT, POLL_INTERVAL

        self.pause(PauseHold.RECOVERY)
        deadline = time.monotonic() + DEAD_CLIENT_CONFIRM_TIMEOUT
        while not self._stop_flag.is_set() and not self._a_client_is_lost(clients):
            if time.monotonic() >= deadline:
                self.resume(PauseHold.RECOVERY)
                self._log_machine(
                    f"Every client is still running {DEAD_CLIENT_CONFIRM_TIMEOUT:.0f}s after the error;"
                    " counting it as the node's FAILURE",
                    logging.WARNING,
                )
                return False
            await asyncio.sleep(POLL_INTERVAL)
        return True

    async def _ambient_dialog_watcher(self) -> None:
        """Background task: advance any open dialog on all clients, declining side quests.

        Polls at POLL_INTERVAL normally. Once a banner is dismissed, switches to
        DIALOG_FAST_POLL_INTERVAL until a full Safety Delay window has elapsed with
        no further dialogue, so multi-banner conversations are advanced quickly.
        """
        from bartleby.runtime.timing import DIALOG_FAST_POLL_INTERVAL

        from bartlebot.nodes import node_helpers

        while not self._stop_flag.is_set():
            try:
                safety_delay: float = self.get_option("Safety Delay", 3.0)
                in_sequence = (time.monotonic() - self._ambient_dialog_last_seen) < safety_delay
                await asyncio.sleep(DIALOG_FAST_POLL_INTERVAL if in_sequence else node_helpers.POLL_INTERVAL)
                if not self._resume_event.is_set():
                    continue
                if not self.clients:
                    continue
                results = await asyncio.gather(
                    *[node_helpers.advance_dialog_once(c) for c in self.clients],
                    return_exceptions=True,
                )
                for i, r in enumerate(results):
                    if r is True:
                        self._ambient_dialog_last_seen = time.monotonic()
                        self._log_machine(f"Ambient dialog dismissed on client {i}", logging.DEBUG)
                    elif isinstance(r, Exception):
                        self._log_machine(f"Ambient dialog watcher error on client {i}: {r}", logging.DEBUG)
            except Exception:
                # One bad pass must not end the watcher for the rest of the run (#154).
                logger.exception("Ambient dialog watcher failed; it carries on")
                await asyncio.sleep(node_helpers.POLL_INTERVAL)

    async def _ambient_combat_watcher(self) -> None:
        """Background task: detect and handle unexpected combat on any client.

        Whenever a client is in battle outside a handle_combat node, pauses the FSM, cancels
        the running node, and hands the fight to an AmbientSession: helpers join, every
        fighter's combat runs with its own slot's strategy, and everyone ends somewhere safe
        (bartlebot/bot/ambient_combat.py). Then the interrupted state runs again, or the bot
        goes to its recovery state if anyone was left out of place. Guarded against
        re-entry and against conflicting with intentional combat nodes.
        """
        from bartlebot.nodes import node_helpers
        from bartlebot.nodes.combat_runner import slots_in_battle

        while not self._stop_flag.is_set():
            try:
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
                    continue  # self.state not yet initialized
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
                        may_move = False
                        if self._resume_event.is_set():
                            self.pause(PauseHold.AMBIENT)
                            self._log_machine("Ambient combat: FSM paused — waiting for combat to finish", logging.INFO)
                            may_move = await self._preempt_node()

                        end = await self._run_ambient_session(clients, battling_slots, may_move)
                        if end is None:
                            if may_move:
                                self._recover_after_ambient()
                            continue
                        if may_move and end.recover and not self._stop_flag.is_set():
                            self._recover_after_ambient()
                finally:
                    if PauseHold.AMBIENT in self._holds:
                        # Only the watcher's own hold: a pause the user or crash recovery asked
                        # for during the fight outlasts it.
                        self.resume(PauseHold.AMBIENT)
                        if self.is_paused:
                            self._log_machine(
                                f"Ambient combat: over; the FSM stays paused ({self._describe_holds()})",
                                logging.INFO,
                            )
                        else:
                            self._log_machine("Ambient combat: FSM resumed", logging.INFO)
            except Exception:
                # One bad pass must not end the watcher for the rest of the run (#154).
                logger.exception("Ambient combat watcher failed; it carries on")
                await asyncio.sleep(node_helpers.POLL_INTERVAL)

    async def _run_ambient_session(
        self,
        clients: list,
        battling_slots: list[int],
        may_move: bool,
        *,
        rejoining: Sequence = (),
        node_strategy: bool | None = None,
    ):
        """Fight out the fights *battling_slots* are in, and count their flees. Hold the lock.

        *rejoining* and *node_strategy* are crash recovery's: see :meth:`settle_fights`.
        Returns the session's SessionEnd, or None when the session raised.
        """
        from bartlebot.bot import ambient_combat

        self._log_machine(f"Ambient combat detected on client(s) {battling_slots} — handling", logging.WARNING)
        session = ambient_combat.AmbientSession(
            self,
            clients,
            [clients[i] for i in battling_slots],
            may_move=may_move,
            rejoining=list(rejoining),
            node_strategy=node_strategy,
        )
        self._ambient_session = session
        end = None
        try:
            end = await session.run()
        except Exception:
            logger.exception("Ambient combat session raised")
            return None
        finally:
            self._ambient_session = None
            if end is None or not end.log_out:
                # They fought, or went to help: no longer where their login left them. A session
                # that hands players back for a logout stopped before anyone moved, and the
                # relaunched player it waited for is still fresh.
                self._fresh_logins.difference_update(c.window_handle for c in [*session.participants, *session.helpers])
        for _ in range(end.fled):
            if self.note_flee():
                break
        return end

    async def settle_fights(self, rejoining: Sequence = ()) -> list:
        """End every fight our players are in, for a caller holding the FSM paused (#203).

        Crash recovery is the caller. It keeps the FSM paused while it relaunches a client,
        which keeps both combat watchers off, so a fight under way when the client crashed, or
        one the relaunched client logs back into, goes unanswered for the whole recovery. Its
        players sit out round after round, and the group survey that follows opens the social
        window over their cards. So before regrouping it calls this, which answers "You Are
        Idle" by fleeing, as the idle watcher would, and then fights whatever is left the way
        the ambient combat watcher does.

        A pause does not stop a node, so a handle_combat node whose fight a client crashed out
        of is still fighting it with everyone else. It is cancelled here, relog alone being
        spared, so the session is the only thing casting for those players, and the session
        fights on with that node's strategy rather than the ambient default.

        *rejoining* are the players recovery relaunched. Each tries to get back into the fight,
        as any helper does. If one cannot (a dungeon that refuses the port, typically), the
        session stops fighting and the players still in it are returned, for recovery to log
        out so the whole team starts again together. Otherwise the result is empty.

        Where anyone ends up does not matter: the caller sends the bot to its recovery state
        either way. Never raises.
        """
        from bartlebot.nodes.combat_runner import slots_in_battle

        if self._stop_flag.is_set():
            return []
        async with self._ambient_combat_lock:
            clients = list(self.clients)
            if not clients:
                return []
            try:
                fled = await self._flee_idle_popups(clients)
                if fled and self.note_flee():
                    return []
                flags = await asyncio.gather(*[c.in_battle() for c in clients], return_exceptions=True)
                battling_slots = slots_in_battle(flags)
                if not battling_slots or self._stop_flag.is_set():
                    return []
                node_strategy = self._running_fight_strategy()
                may_move = await self._preempt_node(spare=frozenset({"relog"}))
                end = await self._run_ambient_session(
                    clients,
                    battling_slots,
                    may_move,
                    rejoining=[c for c in rejoining if c in clients],
                    node_strategy=node_strategy,
                )
                return end.log_out if end is not None else []
            except Exception:
                logger.exception("Settling fights after recovery raised")
                return []

    def _running_fight_strategy(self) -> bool | None:
        """The ``use_default_strategy`` of the handle_combat node still running, or None if none is."""
        task = self._current_node_task
        if task is None or task.done():
            return None
        state_def = self._state_map.get(self.state, {})
        if state_def.get("node_type") != "handle_combat":
            return None
        return state_def.get("params", {}).get("use_default_strategy", True)

    def _recover_after_ambient(self) -> None:
        """Send the FSM to its recovery state: the players are not where the interrupted state left them."""
        self._recover("Ambient combat: the players are not where the interrupted state left them")

    def _recover(self, why: str) -> None:
        """Send the parked FSM to its recovery state, *why* opening the log line.

        A bot with no recovery state runs the interrupted state again instead, with a WARNING.
        """
        dest = self._recovery_state
        if dest is None or dest not in self._state_map:
            self._log_machine(
                f"{why}, and this bot has no recovery state; running the interrupted state again",
                logging.WARNING,
            )
            return
        self.force_state(dest, reason=f"{why} — recovering to {self._fmt_state(dest)}")

    async def _combat_idle_watcher(self) -> None:
        """Background task: answer the game's "You Are Idle" popup by fleeing, then recover (#203).

        The game raises the popup once a player has let two planning phases in a row run out
        without choosing a card or passing, and passes that player's turn for as long as it is
        up. So it means nothing of ours is fighting for that player: typically a fight that
        started while the bot was busy with something it would not put down. What the players
        were doing is unknown by then, so they flee, which leaves them in their world's hub, and
        the bot starts again from its recovery state.

        Acts while the bot runs, and while the ambient combat watcher holds it paused. A pause
        the user asked for, or one held for client recovery, is left alone.
        """
        from bartleby.runtime.timing import COMBAT_AFK_POLL_INTERVAL

        while not self._stop_flag.is_set():
            try:
                await asyncio.sleep(COMBAT_AFK_POLL_INTERVAL)
                if self._stop_flag.is_set():
                    return  # stop() sets the resume event too, to free a parked loop
                if self._holds and PauseHold.AMBIENT not in self._holds:
                    continue
                clients = list(self.clients)
                if not clients:
                    continue
                fled = await self._flee_idle_popups(clients)
                if not fled:
                    continue

                session = self._ambient_session
                if session is not None:
                    # The session fighting this one ends once its players are out of battle, and
                    # counts and recovers from a fled fight itself.
                    for client in fled:
                        session.note_fled(client)
                    continue
                if self.note_flee():
                    return
                await self._recover_from_idle(", ".join(f"P{clients.index(c) + 1}" for c in fled))
            except Exception:
                # One bad pass must not end the watcher for the rest of the run (#154).
                logger.exception("Idle popup watcher failed; it carries on")
                await asyncio.sleep(COMBAT_AFK_POLL_INTERVAL)

    async def _flee_idle_popups(self, clients: list) -> list:
        """Click Flee on every one of *clients* showing "You Are Idle". Returns those it fled."""
        from bartleby.runtime.paths import COMBAT_AFK_FLEE_PATH, COMBAT_AFK_WINDOW_PATH

        from bartlebot.nodes import node_helpers

        shown = await asyncio.gather(
            *[node_helpers.is_visible_by_path(c, COMBAT_AFK_WINDOW_PATH) for c in clients],
            return_exceptions=True,
        )
        fled = []
        for slot, (client, up) in enumerate(zip(clients, shown)):
            if up is not True:
                continue
            try:
                clicked = await node_helpers.click_by_path(client, COMBAT_AFK_FLEE_PATH)
            except Exception as exc:
                self._log_machine(f"Idle popup: Flee could not be clicked on P{slot + 1} ({exc!r})", logging.DEBUG)
                continue
            if clicked:
                fled.append(client)
                self._log_machine(
                    f"Idle popup: P{slot + 1} sat out two rounds of a fight and the game asked whether"
                    " it would return; fleeing",
                    logging.WARNING,
                )
        return fled

    async def _recover_from_idle(self, players: str) -> None:
        """Stop the running node and send the bot to its recovery state after an idle-popup flee.

        Holds the ambient combat lock, so no session starts while the node is being cancelled.
        A relog in progress is left to finish: it is the recovery already. handle_combat is
        cancelled, unlike under the ambient watcher: the fight it was running has just been
        fled, and left alone it would report that fight as won.
        """
        async with self._ambient_combat_lock:
            if self._stop_flag.is_set():
                return
            if self._state_map.get(self.state, {}).get("node_type") == "relog":
                self._log_machine(f"Idle popup: {players} fled; the relog under way is the recovery", logging.INFO)
                return
            self.pause(PauseHold.IDLE)
            try:
                if await self._preempt_node(spare=frozenset({"relog"})):
                    self._recover(f"Idle popup: {players} fled a fight nothing was fighting")
            finally:
                self.resume(PauseHold.IDLE)

    def note_flee(self) -> bool:
        """Count one fled fight toward the flee limit. True when this one stopped the bot.

        handle_combat and the ambient watcher both call it. Fight, flee, recovery, fight never
        trips the same-state or no-match limits, because the states keep changing; this does.
        """
        from bartleby.runtime.timing import AMBIENT_FLEE_LIMIT, AMBIENT_FLEE_WINDOW

        now = time.monotonic()
        self._flees = [t for t in self._flees if now - t < AMBIENT_FLEE_WINDOW] + [now]
        if len(self._flees) < AMBIENT_FLEE_LIMIT:
            return False
        minutes = AMBIENT_FLEE_WINDOW / 60
        self._stop_with_reason(
            f"The players fled {len(self._flees)} fights within {minutes:.0f} minutes. Fleeing means none"
            " of them could still deal damage: check their decks, their mana and the combat strategy"
            " before running the bot again."
        )
        return True

    def _stop_with_reason(self, reason: str) -> None:
        """Stop the run for a reason the user should see, and tell them why."""
        self._log_machine(f"Bot stopped: {reason}", logging.WARNING)
        self.stop()
        if self._on_stopped:
            try:
                self._on_stopped(reason)
            except Exception:
                logger.exception("on_stopped callback raised")

    async def _preempt_node(self, spare: frozenset[str] = NEVER_PREEMPTED) -> bool:
        """Cancel the running node so the ambient watcher has the clients to itself.

        True once no node is running, so the watcher may move players; False when the node
        is one that is never cancelled, or would not stop in time.

        Pausing alone only stops the loop at the next node boundary, leaving the node free to
        move a client the watcher is fighting with. The cancelled state runs again from the
        start once the watcher resumes (see the loop's CancelledError branch). This waits for
        the loop to park, so nothing the node started is still running and a node helper the
        watcher reuses logs as the machine rather than as the cancelled node.

        Never cancels a node whose type is in *spare*, by default NEVER_PREEMPTED: relog is
        itself the recovery, and handle_combat is already fighting. The idle watcher spares only
        relog. Cancellation is not new to nodes: stop() already does it after its grace period.
        """
        from bartleby.runtime.timing import PREEMPT_PARK_TIMEOUT

        if not self._running:
            return True  # no loop, so no node to get out of the way
        task = self._current_node_task
        if task is not None and not task.done():
            if self._state_map.get(self.state, {}).get("node_type") in spare:
                return False
            self._preempted = True
            task.cancel()
        if not await self.wait_until_parked(PREEMPT_PARK_TIMEOUT):
            self._log_machine(
                f"Ambient combat: the interrupted state did not stop within {PREEMPT_PARK_TIMEOUT:.0f}s;"
                " fighting without moving anyone",
                logging.WARNING,
            )
            return False
        return True

    # --- Unrouted return values ---

    def _explicit_stop_reason(self, state_id: str, result: str | None) -> str | None:
        """The author's reason for stopping the run on *result*, or None if it does not stop.

        An empty string means "stop, no reason given", so this is tested against
        None -- see :meth:`StateModel.stop_reason`, whose contract this mirrors on
        the flat dict ``to_machine_config`` produces.
        """
        if result is None:
            return None
        stops = self._state_map.get(state_id, {}).get("stops") or {}
        return stops.get(result)

    def _handle_explicit_stop(self, state_id: str, result: str, reason: str) -> None:
        """End the run because the author marked this return value as fatal.

        Stopping happens here rather than in the GUI for the same reason the
        restricted-zone stop does: the FSM has to halt with no GUI attached, and
        before the callback, which is free to put a dialog in front of the user.
        """
        detail = reason.strip()
        self._log_machine(
            f"Bot stopped at {self._fmt_state(state_id)}: {result} is marked as a deliberate stop."
            + (f" {detail}" if detail else ""),
            logging.WARNING,
        )
        self.stop()
        if self._on_explicit_stop:
            try:
                self._on_explicit_stop(detail, self._fmt_state(state_id), result)
            except Exception:
                logger.exception("on_explicit_stop callback raised")

    def _fallthrough_dest(self, state_id: str, result: str | None) -> str | None:
        """The recovery state an unrouted *result* falls through to, or None.

        None for anything but FALLTHROUGH_RETURNS, and for the recovery state's own
        failure: defaulting that one loops it onto itself, which the same-state idle
        limit would only ever turn into a stop several minutes later.
        """
        if result not in FALLTHROUGH_RETURNS:
            return None
        dest = self._recovery_state
        if dest is None or dest == state_id or dest not in self._state_map:
            return None
        return dest

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
        self._holds.clear()
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

    def pause(self, hold: PauseHold = PauseHold.USER) -> None:
        """Suspend the FSM after the current node finishes, on *hold*'s account.

        Returns immediately. The loop has not necessarily parked yet -- await
        wait_until_parked for that. It stays paused until *hold*, and every other hold
        taken meanwhile, has been released. See PauseHold.
        """
        self._holds.add(hold)
        self._resume_event.clear()

    def resume(self, hold: PauseHold = PauseHold.USER) -> None:
        """Release *hold*, and resume the FSM if no other hold remains.

        The user's resume (the default) releases every hold. Any other releases only its own,
        so a party resuming the pause it took never runs a bot someone else paused meanwhile.
        """
        if hold is PauseHold.USER:
            self._holds.clear()
        else:
            self._holds.discard(hold)
        if self._holds:
            return
        self._lost_hwnds.clear()  # recovered, or given up on: either way not waited for again
        if self._health_monitor is not None:
            self._health_monitor.clear_flagged()  # allow re-detection after client recovery
        # A preemption whose node finished before the cancel landed is never consumed by the
        # loop. The FSM has already moved on then, and the next state is the right one to run.
        self._preempted = False
        self._parked.clear()
        self._resume_event.set()

    def _describe_holds(self) -> str:
        """Who is holding the FSM paused, for a log line: "held for recovery, user"."""
        return "held for " + ", ".join(sorted(self._holds))

    def resume_health_checks_for(self, hwnds: Iterable[int]) -> None:
        """Undo suspend_health_checks_for, one client at a time.

        A relog that happens *inside* a node has to put the watchers back itself: nothing
        calls resume() on the way out, so without this the clients it touched would go
        unwatched for the rest of the run.

        Skips a client a watcher has handed to crash recovery in the meantime, which stays
        flagged until recovery's ``resume()`` so no watcher recovers it a second time.
        """
        if self._health_monitor is None:
            return
        for hwnd in hwnds:
            if hwnd not in self._lost_hwnds:
                self._health_monitor.unflag(hwnd)

    def is_handed_to_recovery(self, client) -> bool:
        """Whether *client* has been handed to crash recovery since the run last resumed.

        By a watcher or by ``abandon_client``. A node holding health checks off for a client
        asks, since the disconnect dialog watcher can close it under the node mid-step.
        """
        return client.window_handle in self._lost_hwnds

    def abandon_client(self, client, reason: str) -> bool:
        """Hand a client a node can no longer use to crash recovery, to be closed and relaunched.

        For a node that has taken a client somewhere it cannot bring it back from -- the
        relog node after Quit, with the client stuck on a loading screen -- while holding
        health checks off for it, so no watcher is going to notice on its own.

        True when the client was handed over. The node must then raise ``ClientClosedError``
        rather than return a result: the run loop's dead-client branch parks without counting
        the tick, where a FAILURE is routed and counted first and can stop the run before
        recovery gets to resume it. The client stays flagged until recovery's ``resume()``
        clears it, so the caller must not resume health checks for it either.

        False when it was not. With no run in flight there is no recovery to hand to. A
        client recovery could not relaunch stops the run with a message saying why: closing
        it would only leave the bot paused on a player that never comes back.

        True as well, without a second recovery, when a watcher has handed the client over
        already: the disconnect dialog watcher keeps running through a relog
        (``suspend_health_checks_for(watch_disconnects=True)``) and can get there first.

        *reason* reads after a subject: "has been loading for 60s".
        """
        monitor = self._health_monitor
        if monitor is None:
            return False
        hwnd = client.window_handle
        if hwnd in self._lost_hwnds:
            return True
        gap = None
        if self._recovery_gap:
            try:
                gap = self._recovery_gap(hwnd)
            except Exception:
                logger.exception("recovery_gap raised")
        if gap:
            position = next((i for i, c in enumerate(self.clients, start=1) if c is client), None)
            who = f"Player {position}" if position else f"The client at hwnd={hwnd}"
            self._stop_with_reason(
                f"{who} {reason}, and cannot be closed and relaunched automatically: {gap}."
                " Fix that before running the bot again."
            )
            return False
        monitor.recover(client, reason)
        self._lost_hwnds.add(hwnd)
        return True

    def force_state(self, state_id: str, reason: str | None = None) -> None:
        """Force the FSM into a specific state for recovery. Bypasses normal entry callbacks.

        *reason* replaces the default log line, so a caller that has a better account
        of why the jump happened does not have to file a second record beside this
        one for the same event.
        """
        self.machine.set_state(state_id)  # sets self.state via the transitions machine
        self._rerunning = False
        self._consecutive_same_state_count = 0
        self._no_match_count = 0
        if reason is None:
            logger.info("FSM state forced to %s (recovery)", self._fmt_state(state_id))
        else:
            logger.info("%s", reason)
