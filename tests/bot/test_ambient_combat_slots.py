"""Tests for combat_runner.slots_in_battle and the slot-indexing contract it enforces.

The ambient combat watcher used to filter clients down to the battling ones and then
zip that shorter list against the DSLs returned by resolve_combat().  Those DSLs are
indexed by *player slot*, so any gap in the battling set handed every subsequent
client the wrong player's strategy.  These tests pin the indexing contract.
"""

from __future__ import annotations

from bartlebot.nodes.combat_runner import slots_in_battle


def test_all_clients_battling():
    assert slots_in_battle([True, True, True]) == [0, 1, 2]


def test_no_clients_battling():
    assert slots_in_battle([False, False]) == []


def test_empty_input():
    assert slots_in_battle([]) == []


def test_returns_indices_not_positions():
    """Only client index 2 is in battle — the caller needs 2, not 0."""
    assert slots_in_battle([False, False, True]) == [2]


def test_gap_in_the_middle_preserves_original_indices():
    assert slots_in_battle([True, False, True, False]) == [0, 2]


def test_exceptions_are_not_treated_as_battling():
    """in_battle() is gathered with return_exceptions=True, so a dead client
    surfaces as an Exception rather than a bool."""
    flags = [ConnectionError("client died"), True]

    assert slots_in_battle(flags) == [1]


def test_truthy_non_true_values_are_not_battling():
    """Identity check, not truthiness — a stray non-bool must not start combat."""
    assert slots_in_battle(["yes", 1, None, True]) == [3]


# ---------------------------------------------------------------------------
# The indexing contract this helper exists to protect
# ---------------------------------------------------------------------------


def test_slot_indexing_selects_the_right_dsl():
    """Indexing per_client_dsl by slot gives each battling client its own strategy."""
    per_client_dsl = ["p1-dsl", "p2-dsl", "p3-dsl", "p4-dsl"]
    flags = [False, False, True, False]

    slots = slots_in_battle(flags)
    paired = [(slot, per_client_dsl[slot]) for slot in slots]

    assert paired == [(2, "p3-dsl")]


def test_old_filtered_zip_would_have_picked_the_wrong_dsl():
    """Regression guard: documents the bug this helper replaced.

    The previous code built `battling = [c for c, f in zip(clients, flags) if f]`
    and zipped it against DSLs resolved for len(battling) clients, so the lone
    battling client at index 2 received player 1's DSL.
    """
    clients = ["c0", "c1", "c2", "c3"]
    per_client_dsl = ["p1-dsl", "p2-dsl", "p3-dsl", "p4-dsl"]
    flags = [False, False, True, False]

    old_battling = [c for c, f in zip(clients, flags) if f is True]
    old_pairing = list(zip(old_battling, per_client_dsl))
    assert old_pairing == [("c2", "p1-dsl")]  # the bug: c2 got player 1's DSL

    new_pairing = [(clients[i], per_client_dsl[i]) for i in slots_in_battle(flags)]
    assert new_pairing == [("c2", "p3-dsl")]


def test_multiple_battling_clients_keep_their_own_slots():
    clients = ["c0", "c1", "c2", "c3"]
    per_client_dsl = ["p1-dsl", "p2-dsl", "p3-dsl", "p4-dsl"]
    flags = [False, True, False, True]

    pairing = [(clients[i], per_client_dsl[i]) for i in slots_in_battle(flags)]

    assert pairing == [("c1", "p2-dsl"), ("c3", "p4-dsl")]
