"""Slot drift through a run: a client lost mid-run, its recovery abandoned, and its return.

Core's ``ClientManager`` empties a lost client's slot and remembers it (bartleby-core's
``tests/runtime/clients/test_client_slots.py``, where the rest of the slot bookkeeping is
tested). What is BartleBot's is the recovery that may or may not hold the slot meanwhile:
``_on_clients_removed`` always nulled a client's slot, but recovery did not always run or
succeed, so returning clients were appended past the end of the ordering -- four clients
ending up on slots 0-4.
"""

from unittest.mock import patch

import pytest

from tests.clients.test_recovery import RecoveryHarness, _make_machine, _mock_create_task


@pytest.fixture
def harness() -> RecoveryHarness:
    return RecoveryHarness()


# ---------------------------------------------------------------------------
# Slot drift — _on_clients_removed followed by _assign_slot
# ---------------------------------------------------------------------------


class TestSlotDrift:
    def test_crash_while_stopped_then_relaunch_reuses_the_slot(self, harness: RecoveryHarness) -> None:
        harness._hwnd_account_map = {100: "user1", 200: "user2"}
        harness._client_order = ["user1", "user2"]
        harness._machine = _make_machine(stopped=True)

        harness._on_clients_removed({100})
        assert harness._client_order == [None, "user2"]
        assert harness._pending_recovery == {}

        harness._assign_slot("user1")
        assert harness._client_order == ["user1", "user2"]

    def test_abandoned_recovery_then_late_arrival_reuses_the_slot(self, harness: RecoveryHarness) -> None:
        """Recovery times out and drops the client from the queue, but it logs in anyway.

        This is the path that produced slot 4 on a four-client run: the returning
        client was no longer pending, so it was appended past the end.
        """
        harness._hwnd_account_map = {100: "user1", 200: "user2", 300: "user3"}
        harness._client_order = ["user1", "user2", "user3"]
        harness._machine = _make_machine()

        with patch("asyncio.create_task", side_effect=_mock_create_task):
            harness._on_clients_removed({200})
        assert harness._client_order == ["user1", None, "user3"]

        # Recovery gives up and clears the queue (launch failure / login timeout).
        harness._pending_recovery.clear()

        # The client finishes logging in later and is picked up as a plain addition.
        harness._assign_slot("user2")
        assert harness._client_order == ["user1", "user2", "user3"]
        assert len(harness._client_order) == 3

    def test_repeated_crash_cycles_do_not_grow_the_ordering(self, harness: RecoveryHarness) -> None:
        harness._hwnd_account_map = {100: "user1", 200: "user2", 300: "user3", 400: "user4"}
        harness._client_order = ["user1", "user2", "user3", "user4"]
        harness._machine = _make_machine(stopped=True)

        for _ in range(5):
            harness._on_clients_removed({200, 300, 400})
            for username in ("user3", "user4", "user2"):  # return out of slot order
                harness._assign_slot(username)
            assert len(harness._client_order) == 4, harness._client_order

        assert harness._client_order == ["user1", "user2", "user3", "user4"]
