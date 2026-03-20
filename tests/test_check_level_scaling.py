"""Unit tests for the check_level_scaling condition node."""

from unittest.mock import AsyncMock, patch

import bartlebot.nodes.condition.check_level_scaling as cls
import bartlebot.nodes.node_helpers as nh

from tests.conftest import FakeClient, FakeMachine


# Patch is_visible_by_path so the level-scaling indicator is visible (scaled) by default.
_ALL_SCALED = patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=True))
_NONE_SCALED = patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=False))


# ---------------------------------------------------------------------------
# Single client
# ---------------------------------------------------------------------------

async def test_single_client_scaled_returns_true():
    """TRUE when the only client is level scaled."""
    with _ALL_SCALED:
        result = await cls.run(FakeMachine(), [FakeClient()], {})
    assert result == "TRUE"


async def test_single_client_not_scaled_returns_false():
    """FALSE when the only client is not level scaled."""
    with _NONE_SCALED:
        result = await cls.run(FakeMachine(), [FakeClient()], {})
    assert result == "FALSE"


# ---------------------------------------------------------------------------
# Multiple clients — all checked by default
# ---------------------------------------------------------------------------

async def test_all_clients_scaled_returns_true():
    """TRUE when all clients are level scaled."""
    with _ALL_SCALED:
        result = await cls.run(FakeMachine(), [FakeClient(), FakeClient()], {})
    assert result == "TRUE"


async def test_one_client_not_scaled_returns_false():
    """FALSE when at least one client is not scaled."""
    # Client 1 scaled, client 2 not scaled.
    scaled_states = patch.object(
        nh, "is_visible_by_path", new=AsyncMock(side_effect=[True, False])
    )
    with scaled_states:
        result = await cls.run(FakeMachine(), [FakeClient(), FakeClient()], {})
    assert result == "FALSE"


# ---------------------------------------------------------------------------
# Level Scaling Clients option filtering
# ---------------------------------------------------------------------------

async def test_clients_to_check_subset_all_scaled_returns_true():
    """TRUE when only specified clients are checked and all are scaled."""
    # Two clients; we only check client 2 (index 1).
    # is_visible_by_path should be called once (for client 2 only).
    machine = FakeMachine(options={"Level Scaling Clients": [2]})
    with _ALL_SCALED:
        result = await cls.run(machine, [FakeClient(), FakeClient()], {})
    assert result == "TRUE"


async def test_clients_to_check_unchecked_client_ignored():
    """Client 1 is not scaled but Level Scaling Clients=[2] — only client 2 is checked → TRUE."""
    machine = FakeMachine(options={"Level Scaling Clients": [2]})
    scaled_states = patch.object(
        nh, "is_visible_by_path", new=AsyncMock(return_value=True)
    )
    with scaled_states:
        result = await cls.run(machine, [FakeClient(), FakeClient()], {})
    assert result == "TRUE"


async def test_clients_to_check_specified_client_not_scaled_returns_false():
    """FALSE when a specified client is not scaled."""
    machine = FakeMachine(options={"Level Scaling Clients": [1, 2]})
    with _NONE_SCALED:
        result = await cls.run(machine, [FakeClient(), FakeClient()], {})
    assert result == "FALSE"


async def test_empty_clients_to_check_defaults_to_all():
    """Level Scaling Clients=[] (default) → all clients are checked."""
    with _NONE_SCALED:
        result = await cls.run(FakeMachine(), [FakeClient(), FakeClient()], {})
    assert result == "FALSE"
