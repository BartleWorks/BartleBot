# BartleBot FSM Architecture

## Library

BartleBot uses the [`transitions`](https://github.com/pytransitions/transitions) library with its
async extension: `transitions.extensions.asyncio.AsyncMachine`.

This integrates cleanly with the existing `asyncio`/`qasync` architecture — all state callbacks
and transition conditions can be async coroutines.

## Core Concepts

### States
Each state represents one FSM node. A node corresponds to a single bot action (e.g.,
`go_to_zone`, `handle_combat`, `enter_dungeon`).

### Transitions
Edges between states. Each transition has:
- A trigger name (used to advance the machine)
- A source state (or `*` for any state)
- A destination state
- Optional conditions (predicates that must be true for the transition to fire)

### Node Behavior
When a state is entered, `AsyncMachine` calls the `on_enter_<state_id>` callback. BartleBot maps
this callback to the node's behavior coroutine — an async Python function from the standard library
or a user-defined custom node.

## Behavior Script Contract

All node behavior functions share this signature:

```python
async def node_func(machine: BotMachine, clients: list[Client], params: dict) -> str:
    ...
    return "SUCCESS"  # or "FAILURE"
```

- `machine` — the running `BotMachine` instance (allows storing result for use in conditions)
- `clients` — the list of WizWalker `Client` objects the bot is operating on
- `params` — the node's parameter dict from the bot JSON
- Return value must be `"SUCCESS"` or `"FAILURE"`

After the coroutine returns, the machine stores the result on `machine.last_result`, then
automatically triggers the `advance` trigger to evaluate outgoing transitions.

## Machine Class

```python
from transitions.extensions.asyncio import AsyncMachine

class BotMachine:
    def __init__(self, clients, bot_config):
        self.clients = clients
        self.last_result = None
        self.bot_config = bot_config
        self._node_registry = {}  # maps node_type -> coroutine

        states = [s["id"] for s in bot_config["states"]]
        states += ["__error__", "__done__"]

        transitions = bot_config["transitions"]
        # Always add: any state -> __error__ on unhandled exception (handled in callback wrapper)

        self.machine = AsyncMachine(
            model=self,
            states=states,
            transitions=transitions,
            initial="__start__",
            auto_transitions=False,
        )

        # Register on_enter callbacks for each state
        for state_def in bot_config["states"]:
            state_id = state_def["id"]
            callback = self._make_callback(state_def)
            self.machine.get_state(state_id).on_enter.append(callback)

    def _make_callback(self, state_def):
        async def callback():
            node_fn = self._node_registry[state_def["node_type"]]
            params = state_def.get("params", {})
            try:
                self.last_result = await node_fn(self, self.clients, params)
            except Exception:
                self.last_result = "FAILURE"
                await self.to___error__()
                return
            await self.advance()  # evaluate outgoing transitions
        return callback
```

## Transition Conditions

Conditions are predicates on the machine model. They are evaluated synchronously (or async if
needed) when the `advance` trigger fires.

Built-in conditions:
- `last_result_is_success` → `return self.last_result == "SUCCESS"`
- `last_result_is_failure` → `return self.last_result == "FAILURE"`
- `all_clients_in_zone(zone)` → check `client.zone_name()` for all clients
- `any_client_below_health(pct)` → check `sprinter.calc_health_ratio()` for any client

Custom conditions can be registered by name in the node registry.

## Machine Instantiation

One `BotMachine` instance is created per bot run (not per client). The state list and transition
list are deserialized from the bot JSON file using pydantic models, then passed to `BotMachine`.

```python
bot_config = load_bot_json("my_bot.json")   # pydantic validation
machine = BotMachine(clients=handler.clients, bot_config=bot_config)
await machine.to_<initial_state>()           # kick off the FSM
```

## Multi-Client Strategy

A single `BotMachine` coordinates all selected clients. Per-client logic (teleport, combat, zone
checks) is parallelized inside node coroutines using `asyncio.gather()`:

```python
# Inside a node coroutine:
await asyncio.gather(*[client.teleport(xyz) for client in clients])
zones = await asyncio.gather(*[c.zone_name() for c in clients])
```

Multi-node, multi-client coordination (where different clients are in different FSM states
simultaneously) is deferred as future work.

## FSM Lifecycle

```
load bot JSON
  └─> create BotMachine
        └─> enter initial state
              └─> on_enter callback fires node coroutine
                    └─> node returns SUCCESS/FAILURE
                          └─> machine.advance() fires
                                └─> evaluate transition conditions
                                      └─> enter next state
                                            └─> repeat ...
                                      └─> (no condition met) → stay / wait / error
```

Bot stops when:
- A terminal state (`__done__`) is entered
- An unhandled exception transitions to `__error__`
- User presses the stop button (sets a cancellation flag checked at the start of each node)

## Error Handling

Each node callback is wrapped in try/except. On exception:
1. `machine.last_result = "FAILURE"`
2. Machine transitions to `__error__` state
3. Error details logged to the GUI logging tab
4. Bot halts (user must restart)

Recoverable errors (e.g., zone desync) are handled *within* the node coroutine itself — returning
`"FAILURE"` rather than raising an exception. The FSM then routes via the failure transition to a
recovery state defined in the bot.
