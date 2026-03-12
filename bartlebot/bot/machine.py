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
    ) -> None:
        self.clients = clients
        self._config = config
        self._registry = node_registry
        self._on_state_change = on_state_change
        self._stop_flag = asyncio.Event()
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

    # --- Transition conditions (referenced by name in config) ---

    def last_result_is_success(self) -> bool:
        return self.last_result == "SUCCESS"

    # --- Public API ---

    async def run(self) -> None:
        """Drive the FSM until stop() is called."""
        self._stop_flag.clear()

        while not self._stop_flag.is_set():
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
                await self.advance()

    def stop(self) -> None:
        """Signal the FSM to halt after the current node finishes."""
        self._stop_flag.set()
