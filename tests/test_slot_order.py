"""Tests for player slot ordering across a launch.

The property under test: players come up in the order the launch profile lists them,
regardless of which character finishes loading in first. Nothing about a game window
carries that order — every window in a batch spawns at the same anchor, and the account a
window ends up with is decided by a set difference — so it has to be claimed up front, by
reserve_slots, before any window exists.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bartlebot.client_manager import ClientManager
from bartlebot.launcher.launch_profile import launch_profile


class OrderHarness:
    """Binds the real slot-ordering methods to a minimal fake, as tests/test_recovery.py does."""

    reserve_slots = ClientManager.reserve_slots
    _assign_slot = ClientManager._assign_slot
    _build_ordered_clients = ClientManager._build_ordered_clients

    def __init__(self) -> None:
        self._client_order: list[str | None] = []
        self._vacated_slots: dict[str, int] = {}
        self._pending_recovery: dict[int, str] = {}
        self._hwnd_account_map: dict[int, str] = {}


@pytest.fixture
def harness() -> OrderHarness:
    return OrderHarness()


def _client(hwnd: int):
    return MagicMock(window_handle=hwnd)


# ---------------------------------------------------------------------------
# reserve_slots
# ---------------------------------------------------------------------------


def test_reserves_in_the_order_given(harness):
    harness.reserve_slots(["alpha", "beta", "gamma"])
    assert harness._client_order == ["alpha", "beta", "gamma"]


def test_load_in_order_does_not_change_the_result(harness):
    """The regression: slots were decided by whichever character loaded in first."""
    harness.reserve_slots(["alpha", "beta", "gamma"])
    # Clients arrive in reverse order, one per detection tick.
    for username in ("gamma", "beta", "alpha"):
        harness._assign_slot(username)
    assert harness._client_order == ["alpha", "beta", "gamma"]


def test_published_list_follows_the_reserved_order(harness):
    """End to end: the ordering _client_loop publishes is the profile's."""
    harness.reserve_slots(["alpha", "beta", "gamma"])
    harness._hwnd_account_map = {1: "gamma", 2: "alpha", 3: "beta"}
    live = [_client(1), _client(2), _client(3)]  # detection order, unrelated to the profile

    ordered = harness._build_ordered_clients(live)
    assert [c.window_handle for c in ordered] == [2, 3, 1]


def test_a_client_still_loading_leaves_no_gap(harness):
    """Reserved-but-not-yet-live accounts are skipped, and the rest keep their order."""
    harness.reserve_slots(["alpha", "beta", "gamma"])
    harness._hwnd_account_map = {1: "alpha", 3: "gamma"}

    ordered = harness._build_ordered_clients([_client(3), _client(1)])
    assert [c.window_handle for c in ordered] == [1, 3]


def test_running_clients_keep_their_slots(harness):
    """Launching a second profile must not renumber the players already up."""
    harness.reserve_slots(["alpha", "beta"])
    harness.reserve_slots(["beta", "gamma"])
    assert harness._client_order == ["alpha", "beta", "gamma"]


def test_reserving_fills_a_hole_left_by_a_closed_client(harness):
    harness.reserve_slots(["alpha", "beta", "gamma"])
    harness._client_order[1] = None  # beta was closed
    harness.reserve_slots(["delta"])
    assert harness._client_order == ["alpha", "delta", "gamma"]


def test_a_slot_held_for_recovery_is_not_taken(harness):
    """A crashed client's slot is being kept for it — a new launch must not claim it."""
    harness._client_order = ["alpha", None, "gamma"]
    harness._pending_recovery = {1: "beta"}

    harness.reserve_slots(["delta"])
    assert harness._client_order == ["alpha", None, "gamma", "delta"]


def test_an_explicit_launch_overrides_a_remembered_slot(harness):
    """The profile is a statement of intent; the vacated slot is only a memory."""
    harness._vacated_slots = {"beta": 0}
    harness.reserve_slots(["alpha", "beta"])
    assert harness._client_order == ["alpha", "beta"]
    assert "beta" not in harness._vacated_slots


def test_reserving_is_idempotent(harness):
    """A relaunch of the same profile must not append duplicates."""
    harness.reserve_slots(["alpha", "beta"])
    harness.reserve_slots(["alpha", "beta"])
    assert harness._client_order == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# launch_profile launches the subset it is given
#
# Reservation itself moved out of launch_profile with #186: it is the caller's, because
# rejecting a launch that is already running cannot be done from inside the launch. What
# is left here is the other half of that handover -- launch_profile spawning exactly the
# accounts the caller says it claimed, and no others.
# ---------------------------------------------------------------------------


async def test_launch_profile_spawns_only_the_accounts_it_was_given():
    """An account already launching is dropped by the caller and must not be spawned again."""
    logins: list[str] = []

    with (
        patch("bartlebot.launcher.launch_profile.patch_open_browser"),
        patch("bartlebot.launcher.launch_profile._patch_window_config"),
        patch("bartlebot.launcher.launch_profile.get_all_wizard_handles", return_value=[]),
        patch("bartlebot.launcher.launch_profile._wait_for_n_new_handles", AsyncMock(return_value=[1, 2])),
        patch("bartlebot.launcher.launch_profile._wait_for_login_screen", AsyncMock()),
        patch("bartlebot.launcher.launch_profile.start_instance") as start,
        patch(
            "bartlebot.launcher.launch_profile.instance_login",
            lambda _h, username, _pw: logins.append(username),
        ),
        patch("bartlebot.launcher.launch_profile.set_window_title"),
        patch("bartlebot.launcher.launch_profile.jitter", return_value=0.0),
        patch("bartlebot.launcher.launch_profile.CredentialStore") as creds,
        patch("bartlebot.launcher.launch_profile.SettingsManager") as sm,
    ):
        creds.retrieve.return_value = "pw"
        creds.display_name.side_effect = lambda u, _a: u
        sm.instance.return_value = MagicMock(
            game_resolution=(800, 600),
            launch_profiles=[{"name": "Team", "accounts": ["alpha", "beta", "gamma"]}],
            accounts=[{"username": u} for u in ("alpha", "beta", "gamma")],
        )

        await launch_profile("Team", usernames=["alpha", "gamma"])

    assert logins == ["alpha", "gamma"]
    assert start.call_count == 2, "spawned a window for an account it was told to skip"


async def test_launch_profile_spawns_nothing_when_every_account_is_filtered_out():
    """A profile whose accounts are all already launching is a no-op, not an empty batch."""
    with (
        patch("bartlebot.launcher.launch_profile.patch_open_browser") as browser,
        patch("bartlebot.launcher.launch_profile._patch_window_config"),
        patch("bartlebot.launcher.launch_profile.start_instance") as start,
        patch("bartlebot.launcher.launch_profile.CredentialStore") as creds,
        patch("bartlebot.launcher.launch_profile.SettingsManager") as sm,
    ):
        creds.retrieve.return_value = "pw"
        creds.display_name.side_effect = lambda u, _a: u
        sm.instance.return_value = MagicMock(
            game_resolution=(800, 600),
            launch_profiles=[{"name": "Team", "accounts": ["alpha", "beta"]}],
            accounts=[{"username": u} for u in ("alpha", "beta")],
        )

        await launch_profile("Team", usernames=[])

    assert start.call_count == 0
    # Returning before config.xml is touched matters as much as not spawning: the patch is
    # global to the install, so writing it for a launch that never happens would change the
    # resolution the *next* manual launch comes up at.
    assert browser.call_count == 0


async def test_launch_profile_launches_the_whole_profile_by_default():
    """usernames is a filter, not a requirement — None launches everything."""
    with (
        patch("bartlebot.launcher.launch_profile.patch_open_browser"),
        patch("bartlebot.launcher.launch_profile._patch_window_config"),
        patch("bartlebot.launcher.launch_profile.get_all_wizard_handles", return_value=[]),
        patch("bartlebot.launcher.launch_profile._wait_for_n_new_handles", AsyncMock(return_value=[1])),
        patch("bartlebot.launcher.launch_profile._wait_for_login_screen", AsyncMock()),
        patch("bartlebot.launcher.launch_profile.start_instance"),
        patch("bartlebot.launcher.launch_profile.instance_login"),
        patch("bartlebot.launcher.launch_profile.set_window_title"),
        patch("bartlebot.launcher.launch_profile.jitter", return_value=0.0),
        patch("bartlebot.launcher.launch_profile.CredentialStore") as creds,
        patch("bartlebot.launcher.launch_profile.SettingsManager") as sm,
    ):
        creds.retrieve.return_value = "pw"
        creds.display_name.side_effect = lambda u, _a: u
        sm.instance.return_value = MagicMock(
            game_resolution=(800, 600),
            launch_profiles=[{"name": "Solo", "accounts": ["alpha"]}],
            accounts=[{"username": "alpha"}],
        )
        await launch_profile("Solo")
