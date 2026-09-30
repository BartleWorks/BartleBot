"""Unit tests for the sync_in_zone node.

Route planning is zone_navigation's and is tested there. This node only has to refuse an
empty zone and report navigate()'s outcome.
"""

from unittest.mock import AsyncMock, patch

import pytest

import bartlebot.nodes.teleportation.sync_in_zone as gtz
from bartlebot.nodes.zone_navigation import Outcome
from tests.conftest import FakeClient, FakeMachine

_ZONE = "WizardCity/WC_Hub"


def _navigate(success: bool):
    return patch.object(gtz.zone_navigation, "navigate", new=AsyncMock(return_value=Outcome(success=success)))


async def test_success_when_every_player_arrives():
    clients = [FakeClient(), FakeClient()]
    machine = FakeMachine()

    with _navigate(True) as navigate:
        result = await gtz.run(machine, clients, {"zone": _ZONE})

    assert result == "SUCCESS"
    navigate.assert_awaited_once_with(machine, clients, _ZONE)


async def test_failure_when_navigation_fails():
    with _navigate(False):
        result = await gtz.run(FakeMachine(), [FakeClient()], {"zone": _ZONE})

    assert result == "FAILURE"


@pytest.mark.parametrize("params", [{}, {"zone": ""}, {"zone": None}], ids=["missing", "empty", "none"])
async def test_no_zone_fails_without_moving_anyone(params):
    machine = FakeMachine()

    with _navigate(True) as navigate:
        result = await gtz.run(machine, [FakeClient()], params)

    assert result == "FAILURE"
    navigate.assert_not_awaited()
    assert any("no zone" in msg for msg, _ in machine.logs)
