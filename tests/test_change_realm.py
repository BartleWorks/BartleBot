"""Unit tests for the change_realm node."""

from unittest.mock import AsyncMock, patch

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.utility.change_realm as cr
from bartlebot.nodes.paths import REALM_GO_TO_BUTTON_PATH, realm_button_path
from tests.conftest import FakeClient, FakeMachine

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
