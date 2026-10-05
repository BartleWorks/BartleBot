"""Tests for the pre-run recoverability check.

Two halves: the wording (pure, no Qt) and ClientManager.recovery_gaps, which decides
which clients have a gap. The dialog itself is a QMessageBox call in a Qt slot and is
not covered here — the decision it presents is.
"""

from unittest.mock import MagicMock, patch

import pytest

from bartlebot import recovery_readiness
from bartlebot.client_manager import ClientManager
from bartlebot.recovery_readiness import NO_ACCOUNT, client_label, warning_text

# ---------------------------------------------------------------------------
# Wording
# ---------------------------------------------------------------------------


def test_label_pairs_the_player_number_with_the_wizard():
    assert client_label(2, "Luke SkullHunter") == "Player 2 — Luke SkullHunter"


def test_label_falls_back_to_the_number_alone():
    """The client loop may not have read the name yet; the number still finds the window."""
    assert client_label(3, None) == "Player 3"
    assert client_label(3, "") == "Player 3"


def test_warning_names_every_affected_client():
    text = warning_text(["Player 1 — A Wizard: " + NO_ACCOUNT, "Player 2 — B Wizard: " + NO_ACCOUNT], total=4)
    assert "2 of 4" in text
    assert "Player 1 — A Wizard" in text and "Player 2 — B Wizard" in text


def test_warning_is_singular_for_one_client():
    text = warning_text(["Player 1: " + NO_ACCOUNT], total=4)
    assert "This client cannot" in text
    assert "These clients" not in text


def test_warning_says_what_the_consequence_is():
    """The point is the outcome, not the misconfiguration."""
    text = warning_text(["Player 1: " + NO_ACCOUNT], total=1)
    assert "paused" in text
    assert text.rstrip().endswith("?"), "it has to ask, since the run still starts on Yes"


# ---------------------------------------------------------------------------
# Which clients have gaps
# ---------------------------------------------------------------------------


class GapsHarness:
    """The attributes recovery_gaps and _alias_for use on self."""

    recovery_gaps = ClientManager.recovery_gaps
    recovery_gap_for = ClientManager.recovery_gap_for
    _recovery_gap = ClientManager._recovery_gap
    _alias_for = ClientManager._alias_for

    def __init__(self, clients: list, account_map: dict[int, str]) -> None:
        self._clients = clients
        self._hwnd_account_map = account_map


def _client(hwnd: int):
    return MagicMock(window_handle=hwnd)


@pytest.fixture
def passwords():
    """Patch CredentialStore so no test touches Credential Manager."""
    with patch("bartlebot.client_manager.CredentialStore") as store:
        store.display_name.side_effect = lambda username, alias: alias or username
        store.retrieve.return_value = "hunter2"
        yield store


@pytest.fixture(autouse=True)
def _accounts():
    with patch("bartlebot.client_manager.SettingsManager") as sm:
        sm.instance.return_value = MagicMock(accounts=[{"username": "user1", "alias": "Jeff"}])
        yield


def test_no_gaps_when_every_client_is_mapped_and_has_a_password(passwords):
    harness = GapsHarness([_client(100), _client(200)], {100: "user1", 200: "user2"})
    assert harness.recovery_gaps() == []


def test_unmapped_client_is_a_gap(passwords):
    harness = GapsHarness([_client(100), _client(200)], {100: "user1"})
    assert harness.recovery_gaps() == [(2, NO_ACCOUNT)]


def test_positions_are_one_based_to_match_the_player_numbering(passwords):
    harness = GapsHarness([_client(100), _client(200), _client(300)], {})
    assert [position for position, _ in harness.recovery_gaps()] == [1, 2, 3]


def test_mapped_client_with_no_stored_password_is_a_gap(passwords):
    """Identified, but there is nothing to log it back in with."""
    passwords.retrieve.return_value = None
    harness = GapsHarness([_client(100)], {100: "user1"})

    gaps = harness.recovery_gaps()

    assert len(gaps) == 1
    assert "no password saved" in gaps[0][1]


def test_the_password_gap_shows_the_alias_not_the_username(passwords):
    """Real usernames stay out of the GUI; display_name is the established path."""
    passwords.retrieve.return_value = None
    harness = GapsHarness([_client(100)], {100: "user1"})

    assert "Jeff" in harness.recovery_gaps()[0][1]


def test_a_credential_store_failure_does_not_invent_a_gap(passwords):
    """Claiming unrecoverable on a lookup error would train the warning to be ignored."""
    passwords.retrieve.side_effect = OSError("credential manager unavailable")
    harness = GapsHarness([_client(100)], {100: "user1"})

    assert harness.recovery_gaps() == []


def test_no_clients_means_no_gaps(passwords):
    assert GapsHarness([], {}).recovery_gaps() == []


def test_reason_constants_are_human_readable():
    """These land verbatim in a dialog, so they have to read as prose."""
    assert recovery_readiness.NO_ACCOUNT.islower() or " " in recovery_readiness.NO_ACCOUNT
    assert "{account}" in recovery_readiness.NO_PASSWORD


def test_one_client_can_be_asked_about_by_hwnd(passwords):
    """What the relog node's handover asks, mid-run, before closing a client."""
    harness = GapsHarness([_client(100), _client(200)], {100: "user1"})
    assert harness.recovery_gap_for(100) is None
    assert harness.recovery_gap_for(200) == NO_ACCOUNT


def test_one_client_without_a_password_is_a_gap(passwords):
    passwords.retrieve.return_value = None
    harness = GapsHarness([_client(100)], {100: "user1"})
    assert "no password saved" in harness.recovery_gap_for(100)
