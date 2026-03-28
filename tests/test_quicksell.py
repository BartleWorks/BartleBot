"""Unit tests for the quicksell node."""

from unittest.mock import AsyncMock, patch

import bartlebot.nodes.action.quicksell as qs
from bartlebot.nodes import node_helpers
from bartlebot.nodes.paths import (
    QUICKSELL_INVENTORY_OPEN_PATH,
    QUICKSELL_JEWEL_OPEN_PATH,
    QUICKSELL_NEXT_PAGE_PATH,
    quicksell_item_checkbox_path,
    quicksell_item_title_path,
)
from tests.conftest import FakeClient, FakeMachine

_NO_KEEPS: dict = {"keep_list": []}
_HAT_KEEPS: dict = {"keep_list": ["hat"]}


def _make_patchers(
    *,
    poll_until_returns=True,
    is_enabled_by_path_returns=False,
    get_window_text_returns="",
    is_jewel_full_returns=False,
):
    """Return a dict of patch context managers for the common quicksell dependencies."""
    return {
        "poll_until": patch.object(node_helpers, "poll_until", new_callable=AsyncMock, return_value=poll_until_returns),
        "click_by_path": patch.object(node_helpers, "click_by_path", new_callable=AsyncMock, return_value=True),
        "is_enabled": patch.object(
            node_helpers, "is_enabled_by_path", new_callable=AsyncMock, return_value=is_enabled_by_path_returns
        ),
        "get_text": patch.object(qs, "_get_window_text", new_callable=AsyncMock, return_value=get_window_text_returns),
        "jewel_full": patch.object(
            qs, "_is_jewel_backpack_full", new_callable=AsyncMock, return_value=is_jewel_full_returns
        ),
        "poll_interval": patch.object(qs, "POLL_INTERVAL", 0),
    }


async def test_returns_success_when_neither_backpack_full():
    """Both backpacks have space → no quicksell UI opened, returns SUCCESS."""
    client = FakeClient()
    client.backpack_space.return_value = (50, 100)

    p = _make_patchers(is_jewel_full_returns=False)
    with (
        p["poll_until"],
        p["click_by_path"] as mock_click,
        p["is_enabled"],
        p["get_text"],
        p["jewel_full"],
        p["poll_interval"],
    ):
        result = await qs.run(FakeMachine(), [client], _NO_KEEPS)

    assert result == "SUCCESS"
    # The only click_by_path calls should be the close-button calls (one per backpack type).
    # The quicksell open buttons must NOT have been clicked.
    clicked_paths = [c.args[1] for c in mock_click.call_args_list]
    assert QUICKSELL_INVENTORY_OPEN_PATH not in clicked_paths
    assert QUICKSELL_JEWEL_OPEN_PATH not in clicked_paths


async def test_quicksells_when_main_backpack_full():
    """Main backpack full, jewel not full → only inventory quicksell UI opened."""
    client = FakeClient()
    client.backpack_space.return_value = (100, 100)

    p = _make_patchers(is_jewel_full_returns=False)
    with (
        p["poll_until"],
        p["click_by_path"] as mock_click,
        p["is_enabled"],
        p["get_text"],
        p["jewel_full"],
        p["poll_interval"],
    ):
        result = await qs.run(FakeMachine(), [client], _NO_KEEPS)

    assert result == "SUCCESS"
    clicked_paths = [c.args[1] for c in mock_click.call_args_list]
    assert QUICKSELL_INVENTORY_OPEN_PATH in clicked_paths
    assert QUICKSELL_JEWEL_OPEN_PATH not in clicked_paths


async def test_quicksells_when_jewel_full():
    """Main not full, jewel full → only jewel quicksell UI opened."""
    client = FakeClient()
    client.backpack_space.return_value = (50, 100)

    p = _make_patchers(is_jewel_full_returns=True)
    with (
        p["poll_until"],
        p["click_by_path"] as mock_click,
        p["is_enabled"],
        p["get_text"],
        p["jewel_full"],
        p["poll_interval"],
    ):
        result = await qs.run(FakeMachine(), [client], _NO_KEEPS)

    assert result == "SUCCESS"
    clicked_paths = [c.args[1] for c in mock_click.call_args_list]
    assert QUICKSELL_JEWEL_OPEN_PATH in clicked_paths
    assert QUICKSELL_INVENTORY_OPEN_PATH not in clicked_paths


async def test_skips_jewel_when_not_full():
    """Main not full and jewel not full → jewel quicksell UI never opened."""
    client = FakeClient()
    client.backpack_space.return_value = (0, 100)

    p = _make_patchers(is_jewel_full_returns=False)
    with (
        p["poll_until"],
        p["click_by_path"] as mock_click,
        p["is_enabled"],
        p["get_text"],
        p["jewel_full"],
        p["poll_interval"],
    ):
        result = await qs.run(FakeMachine(), [client], _NO_KEEPS)

    assert result == "SUCCESS"
    clicked_paths = [c.args[1] for c in mock_click.call_args_list]
    assert QUICKSELL_JEWEL_OPEN_PATH not in clicked_paths
    assert QUICKSELL_INVENTORY_OPEN_PATH not in clicked_paths


async def test_quicksells_both_when_both_full():
    """Both backpacks full → both inventory and jewel quicksell UIs are opened."""
    client = FakeClient()
    client.backpack_space.return_value = (100, 100)

    p = _make_patchers(is_jewel_full_returns=True)
    with (
        p["poll_until"],
        p["click_by_path"] as mock_click,
        p["is_enabled"],
        p["get_text"],
        p["jewel_full"],
        p["poll_interval"],
    ):
        result = await qs.run(FakeMachine(), [client], _NO_KEEPS)

    assert result == "SUCCESS"
    clicked_paths = [c.args[1] for c in mock_click.call_args_list]
    assert QUICKSELL_INVENTORY_OPEN_PATH in clicked_paths
    assert QUICKSELL_JEWEL_OPEN_PATH in clicked_paths


async def test_keeps_items_matching_keyword():
    """Row 0 contains 'Rare Hat' and keep_list=['hat'] → row 0 checkbox is clicked to uncheck."""
    client = FakeClient()
    client.backpack_space.return_value = (100, 100)

    def _text_for_path(client_arg, path):
        if path == quicksell_item_title_path(0):
            return "Rare Hat"
        return ""

    p = _make_patchers(is_jewel_full_returns=False)
    with p["poll_until"], p["click_by_path"] as mock_click, p["is_enabled"], p["jewel_full"], p["poll_interval"]:
        with patch.object(qs, "_get_window_text", side_effect=_text_for_path, new_callable=AsyncMock):
            result = await qs.run(FakeMachine(), [client], _HAT_KEEPS)

    assert result == "SUCCESS"
    clicked_paths = [c.args[1] for c in mock_click.call_args_list]
    assert quicksell_item_checkbox_path(0) in clicked_paths


async def test_case_insensitive_keep_match():
    """keep_list=['STAFF'] matches 'Firebrand Staff' (case-insensitive) → checkbox unchecked."""
    client = FakeClient()
    client.backpack_space.return_value = (100, 100)

    def _text_for_path(client_arg, path):
        if path == quicksell_item_title_path(3):
            return "Firebrand Staff"
        return ""

    p = _make_patchers(is_jewel_full_returns=False)
    with p["poll_until"], p["click_by_path"] as mock_click, p["is_enabled"], p["jewel_full"], p["poll_interval"]:
        with patch.object(qs, "_get_window_text", side_effect=_text_for_path, new_callable=AsyncMock):
            result = await qs.run(FakeMachine(), [client], {"keep_list": ["STAFF"]})

    assert result == "SUCCESS"
    clicked_paths = [c.args[1] for c in mock_click.call_args_list]
    assert quicksell_item_checkbox_path(3) in clicked_paths


async def test_non_matching_items_not_unchecked():
    """Item name does not match any keyword → checkbox is NOT clicked."""
    client = FakeClient()
    client.backpack_space.return_value = (100, 100)

    def _text_for_path(client_arg, path):
        if path == quicksell_item_title_path(0):
            return "Common Boots"
        return ""

    p = _make_patchers(is_jewel_full_returns=False)
    with p["poll_until"], p["click_by_path"] as mock_click, p["is_enabled"], p["jewel_full"], p["poll_interval"]:
        with patch.object(qs, "_get_window_text", side_effect=_text_for_path, new_callable=AsyncMock):
            result = await qs.run(FakeMachine(), [client], _HAT_KEEPS)

    assert result == "SUCCESS"
    clicked_paths = [c.args[1] for c in mock_click.call_args_list]
    assert quicksell_item_checkbox_path(0) not in clicked_paths


async def test_iterates_multiple_pages():
    """Next-page button enabled first time, disabled second → advances exactly one page."""
    client = FakeClient()
    client.backpack_space.return_value = (100, 100)

    p = _make_patchers(is_jewel_full_returns=False)
    # is_enabled_by_path: first call True (advance), second call False (stop).
    with p["poll_until"], p["click_by_path"] as mock_click, p["get_text"], p["jewel_full"], p["poll_interval"]:
        with patch.object(node_helpers, "is_enabled_by_path", new_callable=AsyncMock, side_effect=[True, False]):
            result = await qs.run(FakeMachine(), [client], _NO_KEEPS)

    assert result == "SUCCESS"
    # Next-page button should have been clicked exactly once.
    next_page_clicks = [c for c in mock_click.call_args_list if c.args[1] == QUICKSELL_NEXT_PAGE_PATH]
    assert len(next_page_clicks) == 1


async def test_returns_failure_when_quicksell_ui_never_opens():
    """poll_until returns False (shop UI never appeared) → FAILURE."""
    client = FakeClient()
    client.backpack_space.return_value = (100, 100)

    p = _make_patchers(poll_until_returns=False, is_jewel_full_returns=False)
    with p["poll_until"], p["click_by_path"], p["is_enabled"], p["get_text"], p["jewel_full"], p["poll_interval"]:
        result = await qs.run(FakeMachine(), [client], _NO_KEEPS)

    assert result == "FAILURE"


async def test_handles_multiple_clients():
    """Two clients both have full main backpacks → both are quicksold, SUCCESS."""
    client1 = FakeClient()
    client1.backpack_space.return_value = (100, 100)
    client2 = FakeClient()
    client2.backpack_space.return_value = (100, 100)

    p = _make_patchers(is_jewel_full_returns=False)
    with p["poll_until"], p["click_by_path"], p["is_enabled"], p["get_text"], p["jewel_full"], p["poll_interval"]:
        result = await qs.run(FakeMachine(), [client1, client2], _NO_KEEPS)

    assert result == "SUCCESS"
    # Both clients should have sent B to open the inventory.
    assert client1.send_key.called
    assert client2.send_key.called


async def test_is_jewel_backpack_full_parses_text():
    """_is_jewel_backpack_full returns True when text is 'X/Y' with X >= Y."""
    with patch.object(qs, "_get_window_text", new_callable=AsyncMock) as mock_text:
        mock_text.return_value = "100/100"
        assert await qs._is_jewel_backpack_full(FakeClient()) is True

        mock_text.return_value = "50/100"
        assert await qs._is_jewel_backpack_full(FakeClient()) is False

        mock_text.return_value = "bad text"
        assert await qs._is_jewel_backpack_full(FakeClient()) is False

        mock_text.return_value = ""
        assert await qs._is_jewel_backpack_full(FakeClient()) is False
