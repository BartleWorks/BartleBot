"""The tools that run nodes with no FSM hand them a stand-in machine, which has to carry a walk.

The walk here is the real one, on a simulated client: the suite's walking double never touches
the machine, so a stand-in missing what a walk needs would pass on it.
"""

import pytest
from bartleby.runtime.movement.walk import WalkEnd

from bartlebot.nodes import walking
from tests.nodes.test_walking import _client, real_walks  # noqa: F401 - real_walks is a fixture
from tools import dry_run_potion_route
from tools.zone_nav_tester import app


@pytest.mark.parametrize(
    "machine",
    [app.ConsoleMachine, lambda: dry_run_potion_route.ConsoleMachine(0.01)],
    ids=["zone_nav_tester", "dry_run_potion_route"],
)
async def test_a_stand_in_machine_can_walk_a_client(real_walks, machine):  # noqa: F811
    walked = await walking.walk_clients(machine(), [_client()], (1400.0, 1000.0, 0.0))

    assert walked[0].result.end is WalkEnd.ARRIVED
