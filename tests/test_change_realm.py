"""Unit tests for the change_realm node."""

from unittest.mock import AsyncMock, patch

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.utility.change_realm as cr
from bartlebot import dialog_captions
from bartlebot.nodes.paths import (
    MESSAGE_BOX_CENTER,
    MESSAGE_BOX_RIGHT,
    REALM_GO_TO_BUTTON_PATH,
    realm_button_path,
)
from tests.conftest import FakeClient, FakeMachine, message_box_patches

# ---------------------------------------------------------------------------
# run() wrapper — tested by patching _change_realm_for_client
# ---------------------------------------------------------------------------


async def test_run_returns_success():
    with patch.object(cr, "_change_realm_for_client", new=AsyncMock(return_value="SUCCESS")):
        result = await cr.run(FakeMachine(), [FakeClient()], {})
    assert result == "SUCCESS"


async def test_run_returns_failure():
    with patch.object(cr, "_change_realm_for_client", new=AsyncMock(return_value="FAILURE")):
        result = await cr.run(FakeMachine(), [FakeClient()], {})
    assert result == "FAILURE"


async def test_run_one_of_two_clients_fails():
    """run() must return FAILURE if any single client fails."""
    with patch.object(cr, "_change_realm_for_client", new=AsyncMock(side_effect=["SUCCESS", "FAILURE"])):
        result = await cr.run(FakeMachine(), [FakeClient(), FakeClient()], {})
    assert result == "FAILURE"


# ---------------------------------------------------------------------------
# _change_realm_for_client — targeted failure paths
# ---------------------------------------------------------------------------


async def test_spellbook_never_opens():
    """FAILURE when the spellbook does not open (Preferences tab never appears)."""
    client = FakeClient()

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch.object(nh, "click_by_path", new=AsyncMock()),
        patch.object(nh, "poll_until", new=AsyncMock(return_value=False)),
    ):
        result = await cr._change_realm_for_client(FakeMachine(), client, 3, 5, 0.0)

    assert result == "FAILURE"


async def test_realm_panel_never_opens():
    """FAILURE when the realm panel does not open after navigating to it."""
    client = FakeClient()

    # poll_until calls in order:
    # 1. SPELLBOOK_PREFERENCES_TAB visible → True (spellbook opened)
    # 2. SPELLBOOK_REALMS_BUTTON visible   → True (not a failure gate in the code)
    # 3. REALM_CURRENT_NAME visible        → False (realm panel didn't open) → FAILURE
    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch.object(nh, "click_by_path", new=AsyncMock()),
        patch.object(nh, "poll_until", new=AsyncMock(side_effect=[True, True, False])),
    ):
        result = await cr._change_realm_for_client(FakeMachine(), client, 3, 5, 0.0)

    assert result == "FAILURE"


async def test_realm_hop_timeout():
    """FAILURE when the realm-hop cooldown timer never expires."""
    client = FakeClient()

    async def fake_is_enabled(c, path):
        # GoTo button stays disabled; current realm detected at row 0; prev-page disabled.
        if path == REALM_GO_TO_BUTTON_PATH:
            return False
        if path == realm_button_path(0):
            return False
        return True

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch.object(cr, "REALM_HOP_TIMEOUT", -1),  # -1 ensures elapsed > timeout immediately
        patch.object(cr, "_navigate_to_page_1", new=AsyncMock(return_value=True)),
        patch.object(cr, "_navigate_pages", new=AsyncMock(return_value=True)),
        patch.object(nh, "click_by_path", new=AsyncMock()),
        patch.object(nh, "poll_until", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_enabled_by_path", side_effect=fake_is_enabled),
        patch.object(nh, "get_text_by_path", new=AsyncMock(return_value="SomeRealm")),
    ):
        result = await cr._change_realm_for_client(FakeMachine(), client, 3, 5, 0.0)

    assert result == "FAILURE"


# ---------------------------------------------------------------------------
# Dungeon exit warning (issue #84)
# ---------------------------------------------------------------------------


async def _run_realm_change_with_modal(visible_buttons) -> tuple[str, list[list[str]]]:
    """Drive the flow to the Go-To-Realm click and return (result, clicked paths)."""
    client = FakeClient()
    # Tick 1: nothing has happened yet. Tick 2: loading. Then done.
    client.is_loading.side_effect = [False, True, False]
    patches, clicked = message_box_patches(visible_buttons)

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch.object(cr, "_navigate_to_page_1", new=AsyncMock(return_value=True)),
        patch.object(cr, "_navigate_pages", new=AsyncMock(return_value=True)),
        patch.object(nh, "poll_until", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=True)),
        patch.object(nh, "get_text_by_path", new=AsyncMock(return_value="SomeRealm")),
        patches[0],
        patches[1],
    ):
        result = await cr._change_realm_for_client(FakeMachine(), client, 3, 5, 0.0)

    return result, clicked


async def test_dungeon_exit_warning_is_confirmed_rather_than_re_clicked():
    """Hopping realms out of a dungeon raises the warning, which blocks the realm change."""
    result, clicked = await _run_realm_change_with_modal((MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT))

    assert result == "SUCCESS"
    # The retry tick spent itself on the confirm, not on another Go-To-Realm press.
    assert clicked[-2:] == [REALM_GO_TO_BUTTON_PATH, MESSAGE_BOX_CENTER]


async def test_no_warning_still_re_clicks_go_to_realm():
    """Outside a dungeon the straggler retry must behave exactly as it did before."""
    result, clicked = await _run_realm_change_with_modal(())

    assert result == "SUCCESS"
    assert clicked[-2:] == [REALM_GO_TO_BUTTON_PATH, REALM_GO_TO_BUTTON_PATH]


async def test_refusal_from_inside_a_dungeon_fails_immediately():
    """Verified live (#84): inside a dungeon the game refuses outright rather than asking.

    Re-clicking Go To Realm cannot get past it, so the node must not spend TRIGGER_TIMEOUT
    trying — and the log must name the cause, not report a missing loading screen.
    """
    client = FakeClient()
    client.is_loading.return_value = False
    patches, clicked = message_box_patches((MESSAGE_BOX_CENTER,))
    machine = FakeMachine()

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch.object(cr, "_navigate_to_page_1", new=AsyncMock(return_value=True)),
        patch.object(cr, "_navigate_pages", new=AsyncMock(return_value=True)),
        patch.object(nh, "poll_until", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=True)),
        patch.object(nh, "identify_modal", new=AsyncMock(return_value=dialog_captions.REALM_NOT_ALLOWED_HERE)),
        patch.object(nh, "get_text_by_path", new=AsyncMock(return_value="SomeRealm")),
        patches[0],
        patches[1],
    ):
        result = await cr._change_realm_for_client(machine, client, 3, 5, 0.0)

    assert result == "FAILURE"
    assert any("cannot be switched from inside a dungeon" in msg for msg, _lvl in machine.logs)
    # A one-button refusal is dismissed with the centre button, and Go To Realm is not
    # re-clicked afterwards.
    assert clicked[-1] == MESSAGE_BOX_CENTER


async def test_dungeon_reset_warning_is_confirmed_and_the_hop_proceeds():
    """Verified live (#84): outside the dungeon, the same action asks instead of refusing."""
    client = FakeClient()
    client.is_loading.side_effect = [False, True, False]
    patches, clicked = message_box_patches((MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT))

    with (
        patch.object(cr, "POLL_INTERVAL", 0),
        patch.object(cr, "_navigate_to_page_1", new=AsyncMock(return_value=True)),
        patch.object(cr, "_navigate_pages", new=AsyncMock(return_value=True)),
        patch.object(nh, "poll_until", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=True)),
        patch.object(nh, "identify_modal", new=AsyncMock(return_value=dialog_captions.REALM_RESETS_DUNGEON)),
        patch.object(nh, "get_text_by_path", new=AsyncMock(return_value="SomeRealm")),
        patches[0],
        patches[1],
    ):
        result = await cr._change_realm_for_client(FakeMachine(), client, 3, 5, 0.0)

    assert result == "SUCCESS"
    assert clicked[-1] == MESSAGE_BOX_CENTER
