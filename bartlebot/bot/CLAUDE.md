# The machine

Conventions for `bartlebot/bot/`: the FSM that runs a bot, and what it calls.

## `machine.log()` and the session file

`machine.log()` is the node-facing API and files the record under the running
node when the node's own task, or one it started, makes the call, and under the
machine anywhere else, so the ambient watchers and what they share with
`handle_combat` can use it. The machine's own bookkeeping uses `self._log_machine()`,
which never names a node. Timing constants are in `bartlebot/nodes/timing.py` until
Stage 3 of #167 moves BartleBot onto core's `bartleby/runtime/timing.py`.

## Callback guards in BotMachine

Every callback stored on `BotMachine` and invoked from the `run()` loop must be wrapped in try/except. A bare callback call will crash the FSM on any GUI exception. Follow the existing pattern for `on_state_change` and `on_transition`:

```python
if self._on_my_callback:
    try:
        self._on_my_callback(arg1, arg2)
    except Exception:
        logger.exception("on_my_callback raised")
```

When adding a new callback to `BotMachine`, check that its invocation site has this guard.
