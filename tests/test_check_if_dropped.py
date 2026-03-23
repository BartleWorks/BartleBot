"""Unit tests for the check_if_dropped condition node."""

import bartlebot.nodes.condition.check_if_dropped as cid
from tests.conftest import FakeClient, FakeMachine


def _machine_with_drops(*drops: tuple) -> FakeMachine:
    """Return a FakeMachine pre-loaded with the given (item, qty, client_idx) tuples."""
    m = FakeMachine()
    m.drops = list(drops)
    return m


# ---------------------------------------------------------------------------
# No-drop / no-item edge cases
# ---------------------------------------------------------------------------


async def test_empty_item_names_returns_false():
    """item_names is empty → FALSE regardless of drop history."""
    machine = _machine_with_drops(("IronBloom", 1, 0))
    result = await cid.run(machine, [FakeClient()], {"item_names": [], "clients_to_check": []})
    assert result == "FALSE"


async def test_no_drops_returns_false():
    """No drops recorded → FALSE."""
    result = await cid.run(FakeMachine(), [FakeClient()], {"item_names": ["IronBloom"]})
    assert result == "FALSE"


# ---------------------------------------------------------------------------
# Basic any-match (require_all=False, default)
# ---------------------------------------------------------------------------


async def test_matching_drop_any_client_returns_true():
    """Drop present for the only client → TRUE."""
    machine = _machine_with_drops(("IronBloom", 1, 0))
    result = await cid.run(machine, [FakeClient()], {"item_names": ["IronBloom"]})
    assert result == "TRUE"


async def test_case_insensitive_match():
    """Item names are matched case-insensitively."""
    machine = _machine_with_drops(("ironbloom", 1, 0))
    result = await cid.run(machine, [FakeClient()], {"item_names": ["IronBloom"]})
    assert result == "TRUE"


async def test_irrelevant_drop_returns_false():
    """Drop present but for a different item → FALSE."""
    machine = _machine_with_drops(("GoldKey", 1, 0))
    result = await cid.run(machine, [FakeClient()], {"item_names": ["IronBloom"]})
    assert result == "FALSE"


async def test_any_item_in_list_triggers_true():
    """Second item in item_names matches → TRUE (any-match mode)."""
    machine = _machine_with_drops(("GoldKey", 1, 0))
    result = await cid.run(machine, [FakeClient()], {"item_names": ["IronBloom", "GoldKey"]})
    assert result == "TRUE"


# ---------------------------------------------------------------------------
# clients_to_check filtering
# ---------------------------------------------------------------------------


async def test_drop_on_wrong_client_returns_false():
    """Drop found for client 2 but only client 1 is in clients_to_check → FALSE."""
    clients = [FakeClient(), FakeClient()]
    machine = _machine_with_drops(("IronBloom", 1, 1))  # client index 1 = client 2
    result = await cid.run(machine, clients, {"item_names": ["IronBloom"], "clients_to_check": [1]})
    assert result == "FALSE"


async def test_drop_on_correct_client_returns_true():
    """Drop found for client 2, and clients_to_check=[2] → TRUE."""
    clients = [FakeClient(), FakeClient()]
    machine = _machine_with_drops(("IronBloom", 1, 1))  # client index 1 = client 2
    result = await cid.run(machine, clients, {"item_names": ["IronBloom"], "clients_to_check": [2]})
    assert result == "TRUE"


async def test_empty_clients_to_check_defaults_to_all_clients():
    """clients_to_check=[] → all clients are checked."""
    clients = [FakeClient(), FakeClient()]
    machine = _machine_with_drops(("IronBloom", 1, 1))  # only client 2 has the drop
    result = await cid.run(machine, clients, {"item_names": ["IronBloom"], "clients_to_check": []})
    assert result == "TRUE"


# ---------------------------------------------------------------------------
# require_all=True
# ---------------------------------------------------------------------------


async def test_require_all_all_present_returns_true():
    """require_all=True, all items present for all checked clients → TRUE."""
    clients = [FakeClient(), FakeClient()]
    machine = _machine_with_drops(
        ("IronBloom", 1, 0),
        ("GoldKey", 1, 0),
        ("IronBloom", 1, 1),
        ("GoldKey", 1, 1),
    )
    result = await cid.run(
        machine,
        clients,
        {"item_names": ["IronBloom", "GoldKey"], "clients_to_check": [1, 2], "require_all": True},
    )
    assert result == "TRUE"


async def test_require_all_one_item_missing_returns_false():
    """require_all=True, one item missing for one client → FALSE."""
    clients = [FakeClient(), FakeClient()]
    machine = _machine_with_drops(
        ("IronBloom", 1, 0),
        ("IronBloom", 1, 1),
        # GoldKey never dropped
    )
    result = await cid.run(
        machine,
        clients,
        {"item_names": ["IronBloom", "GoldKey"], "clients_to_check": [1, 2], "require_all": True},
    )
    assert result == "FALSE"


async def test_require_all_item_dropped_for_one_client_only_returns_false():
    """require_all=True, item present for client 1 but not client 2 → FALSE."""
    clients = [FakeClient(), FakeClient()]
    machine = _machine_with_drops(("IronBloom", 1, 0))  # only client 1
    result = await cid.run(
        machine,
        clients,
        {"item_names": ["IronBloom"], "clients_to_check": [1, 2], "require_all": True},
    )
    assert result == "FALSE"


async def test_require_all_single_client_single_item_true():
    """require_all=True, one client, one item, drop present → TRUE."""
    machine = _machine_with_drops(("IronBloom", 1, 0))
    result = await cid.run(
        machine,
        [FakeClient()],
        {"item_names": ["IronBloom"], "require_all": True},
    )
    assert result == "TRUE"
