"""Unit tests for the enter_zone_via_list node."""

from unittest.mock import AsyncMock, patch

import bartlebot.nodes.interaction.enter_zone_via_list as evl
from tests.conftest import FakeClient, FakeMachine

_PARAMS = {
    "zone": "TestZone",
    "to_zone": "DestZone",
    "interact_xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
    "reset_xyz": {"x": 1.0, "y": 0.0, "z": 0.0},
    "zone_list_entry": "Krokotopia",
}

# Patch targets — helpers are called via node_helpers in both the module and _select_zone
_NH = "bartlebot.nodes.interaction.enter_zone_via_list.node_helpers"


def _ready_client(zone: str = "TestZone") -> FakeClient:
    """Client with no stale prompt and interact prompt appearing at interact_xyz."""
    c = FakeClient()
    c.zone_name.return_value = zone
    # First call: stale-prompt check → no stale prompt.
    # Second call onward: prompt is present at interact_xyz.
    c.is_in_npc_range.side_effect = [False, True]
    return c


def _ui_patches(
    *,
    teleport_visible: bool = True,
    prev_enabled: bool = False,
    next_enabled: bool = False,
    opt_texts: list[str] | None = None,
    click_returns: bool = True,
):
    """Return a dict of patch kwargs for all node_helpers UI calls.

    Defaults simulate: list opens immediately on page 1 with 'Krokotopia' as opt0.
    """
    if opt_texts is None:
        opt_texts = ["Krokotopia", "", "", ""]

    return {
        f"{_NH}.is_visible_by_path": AsyncMock(return_value=teleport_visible),
        f"{_NH}.is_enabled_by_path": AsyncMock(return_value=False),  # prev disabled = page 1
        f"{_NH}.get_text_by_path": AsyncMock(side_effect=opt_texts * 10),  # repeat for safety
        f"{_NH}.click_by_path": AsyncMock(return_value=click_returns),
    }


async def test_wrong_zone_fails():
    """Zone check: client not in expected zone → FAILURE before any teleport."""
    client = FakeClient()
    client.zone_name.return_value = "WrongZone"

    with patch.object(evl, "POLL_INTERVAL", 0):
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_success_entry_on_first_page():
    """Happy path: zone list opens, entry is on page 1, teleport → zone change → SUCCESS."""
    client = _ready_client()
    client.zone_name.side_effect = ["TestZone", "DestZone"]  # zone check, then wait_for_zone

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch.object(evl, "ZONE_CHANGE_TIMEOUT", 0.1),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.is_enabled_by_path", AsyncMock(return_value=False)),
        patch(f"{_NH}.get_text_by_path", AsyncMock(side_effect=["Krokotopia", "", "", ""] * 10)),
        patch(f"{_NH}.click_by_path", AsyncMock(return_value=True)),
    ):
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"


async def test_success_entry_on_second_page():
    """Entry not on page 1; next enabled; entry found on page 2 → SUCCESS."""
    client = _ready_client()
    client.zone_name.side_effect = ["TestZone", "DestZone"]

    # is_enabled_by_path: prev always False (page 1), next True on first check then False (last page)
    enabled_calls = [True, False]
    enabled_iter = iter(enabled_calls)

    async def _enabled(c, path):
        if path is evl.UNIVERSE_PREV_PAGE:
            return False
        # UNIVERSE_NEXT_PAGE: enabled on first check, disabled after page turn
        try:
            return next(enabled_iter)
        except StopIteration:
            return False

    # opt texts: page 1 (forward scan), then page 2 twice — once for page_changed
    # detection inside poll_until and once for the next forward scan read.
    opt_texts = [
        "Wizard City",
        "Marleybone",
        "Mooshu",
        "Dragonspyre",  # page 1 scan
        "Krokotopia",
        "",
        "",
        "",  # page 2 page_changed check
        "Krokotopia",
        "",
        "",
        "",
    ]  # page 2 scan
    opt_iter = iter(opt_texts)

    async def _get_text(c, path):
        try:
            return next(opt_iter)
        except StopIteration:
            return ""

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch.object(evl, "ZONE_CHANGE_TIMEOUT", 0.1),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.is_enabled_by_path", _enabled),
        patch(f"{_NH}.get_text_by_path", _get_text),
        patch(f"{_NH}.click_by_path", AsyncMock(return_value=True)),
    ):
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"


async def test_rewinds_to_first_page():
    """List opens on page 2 (prev enabled once); node rewinds then finds entry on page 1 → SUCCESS."""
    client = _ready_client()
    client.zone_name.side_effect = ["TestZone", "DestZone"]

    # prev_enabled: True on first check (need to rewind), False after clicking prev
    prev_calls = iter([True, False])

    async def _enabled(c, path):
        if path is evl.UNIVERSE_PREV_PAGE:
            try:
                return next(prev_calls)
            except StopIteration:
                return False
        return False  # next page disabled — only one page

    # opt texts: page 2 snapshot (for rewind), then page 1 twice — once for
    # page_changed detection inside poll_until and once for the forward scan read.
    opt_texts = [
        "Marleybone",
        "",
        "",
        "",  # page 2 snapshot before prev click
        "Krokotopia",
        "",
        "",
        "",  # page 1 page_changed check
        "Krokotopia",
        "",
        "",
        "",
    ]  # page 1 forward scan
    opt_iter = iter(opt_texts)

    async def _get_text(c, path):
        try:
            return next(opt_iter)
        except StopIteration:
            return ""

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch.object(evl, "ZONE_CHANGE_TIMEOUT", 0.1),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.is_enabled_by_path", _enabled),
        patch(f"{_NH}.get_text_by_path", _get_text),
        patch(f"{_NH}.click_by_path", AsyncMock(return_value=True)),
    ):
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"


async def test_entry_not_found_cancels_and_fails():
    """Entry not on any page; last page reached; cancel clicked → FAILURE."""
    client = _ready_client()
    click_mock = AsyncMock(return_value=True)

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.is_enabled_by_path", AsyncMock(return_value=False)),  # already page 1, no next
        patch(f"{_NH}.get_text_by_path", AsyncMock(return_value="Wizard City")),  # never matches
        patch(f"{_NH}.click_by_path", click_mock),
    ):
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    # Cancel button must have been clicked
    paths_clicked = [call.args[1] for call in click_mock.call_args_list]
    assert evl.UNIVERSE_CANCEL_BUTTON in paths_clicked


async def test_prompt_never_appears_fails():
    """Interact prompt never shows after MAX_RETRIES → FAILURE."""
    client = FakeClient()
    client.is_in_npc_range.return_value = False

    with patch.object(evl, "POLL_INTERVAL", 0):
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    assert client.teleport.call_count == evl.MAX_RETRIES * 2


async def test_zone_list_never_opens_fails():
    """NPC prompt appears but zone list UI never becomes visible → FAILURE."""
    client = _ready_client()

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=False)),
    ):
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_zone_change_timeout_fails():
    """Entry found, teleport clicked, but clients never reach to_zone → FAILURE."""
    client = _ready_client()
    # zone_name always returns starting zone (never reaches DestZone)
    client.zone_name.return_value = "TestZone"

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch.object(evl, "ZONE_CHANGE_TIMEOUT", 0.01),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.is_enabled_by_path", AsyncMock(return_value=False)),
        patch(f"{_NH}.get_text_by_path", AsyncMock(side_effect=["Krokotopia", "", "", ""] * 20)),
        patch(f"{_NH}.click_by_path", AsyncMock(return_value=True)),
    ):
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_stale_prompt_cleared():
    """Pre-existing prompt is cleared via reset_xyz before the retry loop starts."""
    client = FakeClient()
    call_count = 0

    async def _npc_range():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return True  # stale prompt on node entry
        if call_count == 2:
            return False  # cleared after teleport to reset_xyz
        return True  # prompt at interact_xyz on first attempt

    client.is_in_npc_range.side_effect = _npc_range
    client.zone_name.side_effect = ["TestZone", "DestZone"]

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch.object(evl, "ZONE_CHANGE_TIMEOUT", 0.1),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.is_enabled_by_path", AsyncMock(return_value=False)),
        patch(f"{_NH}.get_text_by_path", AsyncMock(side_effect=["Krokotopia", "", "", ""] * 10)),
        patch(f"{_NH}.click_by_path", AsyncMock(return_value=True)),
    ):
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    # First teleport must be to reset_xyz (x=1.0)
    first_tp = client.teleport.call_args_list[0]
    assert first_tp.args[0].x == 1.0
