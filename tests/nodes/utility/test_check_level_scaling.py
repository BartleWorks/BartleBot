"""Unit tests for the check_level_scaling condition node."""

from unittest.mock import AsyncMock, patch

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.utility.check_level_scaling as cls
from tests.conftest import FakeClient, FakeMachine

# Patch is_visible_by_path so the level-scaling indicator is visible (scaled) by default.
_ALL_SCALED = patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=True))
_NONE_SCALED = patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=False))


# ---------------------------------------------------------------------------
# Single client
# ---------------------------------------------------------------------------


async def test_single_client_scaled_returns_true():
    """TRUE when the only client is level scaled."""
    machine = FakeMachine(options={"Level Scaling Players": [1]})
    with _ALL_SCALED:
        result = await cls.run(machine, [FakeClient()], {})
    assert result == "TRUE"


async def test_single_client_not_scaled_returns_false():
    """FALSE when the only client is not level scaled."""
    machine = FakeMachine(options={"Level Scaling Players": [1]})
    with _NONE_SCALED:
        result = await cls.run(machine, [FakeClient()], {})
    assert result == "FALSE"


# ---------------------------------------------------------------------------
# Multiple clients — explicit client list required
# ---------------------------------------------------------------------------


async def test_all_clients_scaled_returns_true():
    """TRUE when all specified clients are level scaled."""
    machine = FakeMachine(options={"Level Scaling Players": [1, 2]})
    with _ALL_SCALED:
        result = await cls.run(machine, [FakeClient(), FakeClient()], {})
    assert result == "TRUE"


async def test_one_client_not_scaled_returns_false():
    """FALSE when at least one specified client is not scaled."""
    machine = FakeMachine(options={"Level Scaling Players": [1, 2]})
    # Client 1 scaled, client 2 not scaled.
    scaled_states = patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=[True, False]))
    with scaled_states:
        result = await cls.run(machine, [FakeClient(), FakeClient()], {})
    assert result == "FALSE"


# ---------------------------------------------------------------------------
# Level Scaling Players option filtering
# ---------------------------------------------------------------------------


async def test_clients_to_check_subset_all_scaled_returns_true():
    """TRUE when only specified clients are checked and all are scaled."""
    # Two clients; we only check client 2 (index 1).
    # is_visible_by_path should be called once (for client 2 only).
    machine = FakeMachine(options={"Level Scaling Players": [2]})
    with _ALL_SCALED:
        result = await cls.run(machine, [FakeClient(), FakeClient()], {})
    assert result == "TRUE"


async def test_clients_to_check_unchecked_client_ignored():
    """Client 1 is not scaled but Level Scaling Players=[2] — only client 2 is checked → TRUE."""
    machine = FakeMachine(options={"Level Scaling Players": [2]})
    scaled_states = patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=True))
    with scaled_states:
        result = await cls.run(machine, [FakeClient(), FakeClient()], {})
    assert result == "TRUE"


async def test_clients_to_check_specified_client_not_scaled_returns_false():
    """FALSE when a specified client is not scaled."""
    machine = FakeMachine(options={"Level Scaling Players": [1, 2]})
    with _NONE_SCALED:
        result = await cls.run(machine, [FakeClient(), FakeClient()], {})
    assert result == "FALSE"


async def test_empty_clients_to_check_returns_true():
    """Level Scaling Players=[] (default) → no clients checked → TRUE (vacuous pass)."""
    with _NONE_SCALED:
        result = await cls.run(FakeMachine(), [FakeClient(), FakeClient()], {})
    assert result == "TRUE"
