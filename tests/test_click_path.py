"""Unit tests for the click_path node."""

from unittest.mock import AsyncMock, patch

import bartlebot.nodes.interaction.click_path as cp
from tests.conftest import FakeClient, FakeMachine

_PATH = ["WorldView", "windowHUD", "btnOk"]
_CONFIRM_PATH = ["WorldView", "windowHUD", "dlgResult"]


async def test_success_no_confirm():
    """Path visible+enabled → click fires → SUCCESS."""
    client = FakeClient()
    with (
        patch.object(cp, "is_visible_by_path", AsyncMock(return_value=True)),
        patch.object(cp, "is_enabled_by_path", AsyncMock(return_value=True)),
        patch.object(cp, "click_by_path", AsyncMock(return_value=True)),
    ):
        result = await cp.run(FakeMachine(), [client], {"path": _PATH, "availability_timeout": 0})
    assert result == "SUCCESS"


async def test_success_with_confirm_visible():
    """Click fires; confirm path becomes visible → SUCCESS."""
    client = FakeClient()
    with (
        patch.object(cp, "is_visible_by_path", AsyncMock(return_value=True)),
        patch.object(cp, "is_enabled_by_path", AsyncMock(return_value=True)),
        patch.object(cp, "click_by_path", AsyncMock(return_value=True)),
    ):
        result = await cp.run(
            FakeMachine(),
            [client],
            {
                "path": _PATH,
                "availability_timeout": 0,
                "confirm_path": _CONFIRM_PATH,
                "confirm_visible": True,
                "confirm_timeout": 0.01,
            },
        )
    assert result == "SUCCESS"


async def test_success_with_confirm_enabled():
    """Click fires; confirm path becomes enabled (not grayed) → SUCCESS."""
    client = FakeClient()
    with (
        patch.object(cp, "is_visible_by_path", AsyncMock(return_value=True)),
        patch.object(cp, "is_enabled_by_path", AsyncMock(return_value=True)),
        patch.object(cp, "click_by_path", AsyncMock(return_value=True)),
    ):
        result = await cp.run(
            FakeMachine(),
            [client],
            {
                "path": _PATH,
                "availability_timeout": 0,
                "confirm_path": _CONFIRM_PATH,
                "confirm_visible": False,
                "confirm_timeout": 0.01,
            },
        )
    assert result == "SUCCESS"


async def test_failure_path_not_visible():
    """Path never visible → FAILURE without clicking."""
    client = FakeClient()
    mock_click = AsyncMock(return_value=True)
    with (
        patch.object(cp, "is_visible_by_path", AsyncMock(return_value=False)),
        patch.object(cp, "is_enabled_by_path", AsyncMock(return_value=True)),
        patch.object(cp, "click_by_path", mock_click),
    ):
        result = await cp.run(FakeMachine(), [client], {"path": _PATH, "availability_timeout": 0})
    assert result == "FAILURE"
    mock_click.assert_not_called()


async def test_failure_path_grayed():
    """Path visible but grayed out → FAILURE without clicking."""
    client = FakeClient()
    mock_click = AsyncMock(return_value=True)
    with (
        patch.object(cp, "is_visible_by_path", AsyncMock(return_value=True)),
        patch.object(cp, "is_enabled_by_path", AsyncMock(return_value=False)),
        patch.object(cp, "click_by_path", mock_click),
    ):
        result = await cp.run(FakeMachine(), [client], {"path": _PATH, "availability_timeout": 0})
    assert result == "FAILURE"
    mock_click.assert_not_called()


async def test_failure_click_returns_false():
    """click_by_path returns False (race: element grayed between check and click) → FAILURE."""
    client = FakeClient()
    with (
        patch.object(cp, "is_visible_by_path", AsyncMock(return_value=True)),
        patch.object(cp, "is_enabled_by_path", AsyncMock(return_value=True)),
        patch.object(cp, "click_by_path", AsyncMock(return_value=False)),
    ):
        result = await cp.run(FakeMachine(), [client], {"path": _PATH, "availability_timeout": 0})
    assert result == "FAILURE"


async def test_failure_confirm_timeout():
    """Click fires but confirm path never becomes visible → FAILURE."""
    client = FakeClient()

    async def _vis_by_path(client, path):
        if path == _PATH:
            return True  # availability: target is visible
        return False  # confirm: never becomes visible

    with (
        patch.object(cp, "is_visible_by_path", _vis_by_path),
        patch.object(cp, "is_enabled_by_path", AsyncMock(return_value=True)),
        patch.object(cp, "click_by_path", AsyncMock(return_value=True)),
        patch.object(cp, "POLL_INTERVAL", 0),
    ):
        result = await cp.run(
            FakeMachine(),
            [client],
            {
                "path": _PATH,
                "availability_timeout": 0,
                "confirm_path": _CONFIRM_PATH,
                "confirm_visible": True,
                "confirm_timeout": 0.01,
            },
        )
    assert result == "FAILURE"


async def test_no_wait_when_availability_timeout_zero():
    """availability_timeout=0 does a single check — poll_until is not invoked."""
    client = FakeClient()
    with (
        patch.object(cp, "is_visible_by_path", AsyncMock(return_value=True)),
        patch.object(cp, "is_enabled_by_path", AsyncMock(return_value=True)),
        patch.object(cp, "click_by_path", AsyncMock(return_value=True)),
        patch.object(cp, "poll_until") as mock_poll,
    ):
        result = await cp.run(FakeMachine(), [client], {"path": _PATH, "availability_timeout": 0})
    assert result == "SUCCESS"
    mock_poll.assert_not_called()


async def test_multiple_clients_all_must_succeed():
    """Two clients; second client's path is grayed → FAILURE."""
    client1 = FakeClient()
    client2 = FakeClient()

    async def _enabled(client, path):
        return client is client1  # client2 is grayed

    with (
        patch.object(cp, "is_visible_by_path", AsyncMock(return_value=True)),
        patch.object(cp, "is_enabled_by_path", _enabled),
        patch.object(cp, "click_by_path", AsyncMock(return_value=True)),
    ):
        result = await cp.run(FakeMachine(), [client1, client2], {"path": _PATH, "availability_timeout": 0})
    assert result == "FAILURE"


async def test_empty_path_returns_failure():
    """Empty path parameter → immediate FAILURE without any window checks."""
    result = await cp.run(FakeMachine(), [FakeClient()], {"path": []})
    assert result == "FAILURE"
