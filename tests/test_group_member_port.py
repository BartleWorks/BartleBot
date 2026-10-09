"""Tests for porting to a group member through the Group tab (group_manager.teleport_to_group_member).

The group route to a player, used by bartlebot.clients.port_map for two of our clients who are grouped
but not friends. Runs against the group manager tests' FakeUI, which models the social window.
"""

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

import bartlebot.clients.group_manager as gm
import bartlebot.nodes.node_helpers as nh
from bartlebot.nodes.paths import FRIENDLY_PLAYER_GO_TO_BUTTON_PATH, FRIENDLY_PLAYER_MENU_PATH, group_member_name_path
from tests.test_group_manager import FakeUI


@pytest.fixture
def ui():
    return FakeUI()


def _patches(ui: FakeUI, clicks: list, *, menu_opens: bool = True):
    """FakeUI's window layer, plus a roster line that opens the player menu when clicked."""
    menu_open: dict = {}

    async def find_windows_by_path(parent, path):
        # The "window" is its own path, so the click below can record which line it was.
        return [tuple(path)]

    async def click_window(client, window):
        clicks.append((client, list(window)))
        menu_open[client] = menu_opens
        return True

    async def is_visible_by_path(client, path):
        if path == FRIENDLY_PLAYER_MENU_PATH:
            return menu_open.get(client, False)
        return await ui.is_visible_by_path(client, path)

    async def click_by_path(client, path):
        if path == FRIENDLY_PLAYER_GO_TO_BUTTON_PATH:
            clicks.append((client, "go to player"))
            return menu_open.get(client, False)
        return await ui.click_by_path(client, path)

    return (
        *ui.patches(),
        patch.object(nh, "find_windows_by_path", new=AsyncMock(side_effect=find_windows_by_path)),
        patch.object(nh, "click_window", new=AsyncMock(side_effect=click_window)),
        patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=is_visible_by_path)),
        patch.object(nh, "click_by_path", new=AsyncMock(side_effect=click_by_path)),
    )


async def _with(patches, coro_factory):
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return await coro_factory()


async def test_it_clicks_the_members_roster_line_then_go_to_player(ui):
    mover, target = ui.make_client(), ui.make_client()
    ui.put_in_group(mover, target)
    clicks: list = []

    await _with(_patches(ui, clicks), lambda: gm.teleport_to_group_member(mover, ui.name_of[target].lower()))

    # Every client sees itself on Line1, so the target is on Line2.
    assert clicks == [(mover, group_member_name_path(2)), (mover, "go to player")]


async def test_a_player_not_on_the_roster_raises_and_leaves_the_window_closed(ui):
    mover, stranger = ui.make_client(), ui.make_client()
    clicks: list = []

    with pytest.raises(ValueError):
        await _with(_patches(ui, clicks), lambda: gm.teleport_to_group_member(mover, ui.name_of[stranger]))

    assert clicks == []
    assert ui.social_open[mover] is False


async def test_a_player_menu_that_never_opens_raises_and_leaves_the_window_closed(ui):
    mover, target = ui.make_client(), ui.make_client()
    ui.put_in_group(mover, target)
    clicks: list = []

    with pytest.raises(ValueError):
        await _with(
            _patches(ui, clicks, menu_opens=False),
            lambda: gm.teleport_to_group_member(mover, ui.name_of[target]),
        )

    assert clicks == [(mover, group_member_name_path(2))]
    assert ui.social_open[mover] is False


async def test_port_to_group_member_closes_the_social_window_after_the_port(ui):
    mover, target = ui.make_client(), ui.make_client()
    ui.put_in_group(mover, target)
    port = AsyncMock(return_value=nh.PortToFriendResult(seen_loading={0}))

    async def go():
        ui.social_open[mover] = True  # the port leaves it up
        return await gm.port_to_group_member([mover], ui.name_of[target].lower())

    result = await _with((*_patches(ui, []), patch.object(nh, "port_clients", port)), go)

    assert result.seen_loading == {0}
    port.assert_awaited_once()
    assert ui.social_open[mover] is False
