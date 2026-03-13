import asyncio
import traceback
from typing import Callable

from transitions.extensions.asyncio import AsyncMachine


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
        on_state_change: Callable[[str], None] | None = None,
        on_transition: Callable[[str, str, str], None] | None = None,
    ) -> None:
        self.clients = clients
        self._config = config
        self._registry = node_registry
        self._on_state_change = on_state_change
        self._on_transition = on_transition
        self._stop_flag = asyncio.Event()
        self._resume_event = asyncio.Event()
        self._resume_event.set()  # not paused by default
        self.last_result: str | None = None

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
        """Dynamically provide _result_is_<VALUE> condition methods.

        BotConfig.to_machine_config() converts when="SUCCESS" into the condition
        name "_result_is_SUCCESS". This __getattr__ supplies the callable on
        demand without requiring upfront registration for every return value.
        """
        if name.startswith("_result_is_"):
            expected = name[len("_result_is_"):]
            return lambda: self.last_result == expected
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    # --- Public API ---

    @property
    def is_paused(self) -> bool:
        return not self._resume_event.is_set()

    async def run(self) -> None:
        """Drive the FSM until stop() is called."""
        while not self._stop_flag.is_set():
            await self._resume_event.wait()  # block here while paused
            if self._stop_flag.is_set():
                break
            state_id = self.state
            state_def = self._state_map[state_id]

            if self._on_state_change:
                self._on_state_change(state_id)

            node_fn = self._registry[state_def["node_type"]]
            try:
                self.last_result = await node_fn(self, self.clients, state_def.get("params", {}))
            except Exception:
                print(traceback.format_exc())
                self.last_result = "FAILURE"

            if not self._stop_flag.is_set():
                prev_state = state_id
                await self.advance()
                if self._on_transition:
                    self._on_transition(prev_state, self.last_result or "", self.state)

    def stop(self) -> None:
        """Signal the FSM to halt after the current node finishes."""
        self._stop_flag.set()
        self._resume_event.set()  # unblock any paused wait so stop is seen immediately

    def pause(self) -> None:
        """Suspend the FSM after the current node finishes."""
        self._resume_event.clear()

    def resume(self) -> None:
        """Resume a paused FSM."""
        self._resume_event.set()
