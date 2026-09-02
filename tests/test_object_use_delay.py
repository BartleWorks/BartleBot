"""The Delay Between Object Uses param, across all four sequential-capable nodes.

`take_turns` has always left a gap between one player finishing at a shared object and
the next one starting, but no node exposed it — so a bot with a slow object had no way
to reach it and pinned Safety Delay instead. Safety Delay is the user's connection
slack, not the object's cycle time, so the pin took the user's value away to buy
something unrelated. These tests hold the four nodes to passing the param through, and
to leaving `take_turns` on its own constant when nobody set one.
"""

from itertools import chain, repeat
from unittest.mock import patch

import bartlebot.nodes.interaction.interact as interact
import bartlebot.nodes.interaction.interact_menu as interact_menu
import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.zone_entry.enter_zone_via_list as via_list
import bartlebot.nodes.zone_entry.enter_zone_via_object as via_object
from tests.conftest import FakeClient, FakeMachine

_INTERACT = {"x": 0.0, "y": 0.0, "z": 0.0}
_RESET = {"x": 1.0, "y": 0.0, "z": 0.0}

# One entry per node: the module, and the params that reach its sequential path.
_NODES = {
    "interact": (
        interact,
        {
            "zone": "TestZone",
            "interact_xyz": _INTERACT,
            "reset_xyz": _RESET,
            "interact_clients": [1],
            "sequential": True,
        },
    ),
    "interact_menu": (
        interact_menu,
        {
            "zone": "TestZone",
            "interact_xyz": _INTERACT,
            "reset_xyz": _RESET,
            "menu_path": ["WorldView", "Menu"],
            "menu_timeout": 0.01,
            "interact_clients": [1],
            "sequential": True,
        },
    ),
    "enter_zone_via_object": (
        via_object,
        {
            "from_zone": "TestZone",
            "to_zone": "DestZone",
            "interact_xyz": _INTERACT,
            "reset_xyz": _RESET,
            "sequential": True,
        },
    ),
    "enter_zone_via_list": (
        via_list,
        {
            "zone": "TestZone",
            "to_zone": "DestZone",
            "interact_xyz": _INTERACT,
            "reset_xyz": _RESET,
            "zone_list_entry": "Krokotopia",
            "sequential": True,
        },
    ),
}


async def _delay_seen(module, params: dict) -> object:
    """Run one node's sequential path with take_turns stubbed; return the delay it passed.

    The stub reports every client as having taken its turn successfully, so the node runs
    to whatever it does after the shared object — which for the two zone-entry nodes is a
    collective arrival check that the FakeClient's zone answers. The return value is not
    what is under test here; the kwarg is.
    """
    captured: dict = {}

    async def _fake_take_turns(clients, interact_xyz, reset_xyz, safety_delay, turn_fn, **kwargs):
        captured.update(kwargs)
        return [nh.TurnResult(prompted=True, completed=True) for _ in clients]

    # The node checks it is in the starting zone before doing anything, and the two
    # zone-entry nodes then confirm arrival in the destination — so the client reports
    # where it started once, and where it was going from then on.
    start = params.get("from_zone") or params["zone"]
    client = FakeClient()
    client.zone_name.side_effect = chain([start], repeat(params.get("to_zone", start)))

    with patch.object(nh, "take_turns", _fake_take_turns), patch.object(nh, "POLL_INTERVAL", 0):
        await module.run(FakeMachine(), [client], params)

    assert "object_use_delay" in captured, "the node did not pass a delay through to take_turns"
    return captured["object_use_delay"]


async def test_every_sequential_node_passes_its_object_use_delay_through():
    for name, (module, params) in _NODES.items():
        seen = await _delay_seen(module, {**params, "object_use_delay": 9.0})
        assert seen == 9.0, f"{name} passed {seen!r}"


async def test_an_unset_delay_leaves_take_turns_on_its_own_constant():
    """None rather than 0.0 — take_turns reads OBJECT_USE_DELAY at call time,
    which is both the documented default and what keeps the constant patchable."""
    for name, (module, params) in _NODES.items():
        seen = await _delay_seen(module, params)
        assert seen is None, f"{name} passed {seen!r}"
