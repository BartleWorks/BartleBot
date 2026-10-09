"""Unit tests for the check_zone condition node."""

import bartlebot.nodes.utility.check_zone as cz
from tests.conftest import FakeClient, FakeMachine

# ---------------------------------------------------------------------------
# Basic zone matching
# ---------------------------------------------------------------------------


async def test_all_clients_in_zone_returns_true():
    """All clients in the target zone → TRUE."""
    clients = [FakeClient(), FakeClient()]
    clients[0].zone_name.return_value = "WizardCity/WC_Ravenwood"
    clients[1].zone_name.return_value = "WizardCity/WC_Ravenwood"
    result = await cz.run(FakeMachine(), clients, {"zone": "WizardCity/WC_Ravenwood"})
    assert result == "TRUE"


async def test_one_client_wrong_zone_returns_false():
    """One client in the wrong zone → FALSE."""
    clients = [FakeClient(), FakeClient()]
    clients[0].zone_name.return_value = "WizardCity/WC_Ravenwood"
    clients[1].zone_name.return_value = "WizardCity/WC_Hub"
    result = await cz.run(FakeMachine(), clients, {"zone": "WizardCity/WC_Ravenwood", "clients_to_check": [1, 2]})
    assert result == "FALSE"


async def test_single_client_in_zone_returns_true():
    """Single client in the target zone → TRUE."""
    client = FakeClient()
    client.zone_name.return_value = "WizardCity/WC_Ravenwood"
    result = await cz.run(FakeMachine(), [client], {"zone": "WizardCity/WC_Ravenwood"})
    assert result == "TRUE"


async def test_single_client_wrong_zone_returns_false():
    """Single client not in the target zone → FALSE."""
    client = FakeClient()
    client.zone_name.return_value = "WizardCity/WC_Hub"
    result = await cz.run(FakeMachine(), [client], {"zone": "WizardCity/WC_Ravenwood", "clients_to_check": [1]})
    assert result == "FALSE"


# ---------------------------------------------------------------------------
# Empty / missing zone param
# ---------------------------------------------------------------------------


async def test_empty_zone_param_returns_false():
    """zone="" → FALSE without checking clients."""
    result = await cz.run(FakeMachine(), [FakeClient()], {"zone": ""})
    assert result == "FALSE"


async def test_missing_zone_param_returns_false():
    """zone key absent from params → FALSE."""
    result = await cz.run(FakeMachine(), [FakeClient()], {})
    assert result == "FALSE"


# ---------------------------------------------------------------------------
# clients_to_check filtering
# ---------------------------------------------------------------------------


async def test_clients_to_check_in_zone_returns_true():
    """Only the checked client is in the zone; the other isn't — but it's ignored → TRUE."""
    clients = [FakeClient(), FakeClient()]
    clients[0].zone_name.return_value = "WizardCity/WC_Ravenwood"
    clients[1].zone_name.return_value = "WizardCity/WC_Hub"
    # Check only client 1 (1-based), which is in the target zone.
    result = await cz.run(FakeMachine(), clients, {"zone": "WizardCity/WC_Ravenwood", "clients_to_check": [1]})
    assert result == "TRUE"


async def test_clients_to_check_not_in_zone_returns_false():
    """The checked client is not in the zone → FALSE, even though others are."""
    clients = [FakeClient(), FakeClient()]
    clients[0].zone_name.return_value = "WizardCity/WC_Ravenwood"
    clients[1].zone_name.return_value = "WizardCity/WC_Hub"
    # Check only client 2 (1-based), which is NOT in the target zone.
    result = await cz.run(FakeMachine(), clients, {"zone": "WizardCity/WC_Ravenwood", "clients_to_check": [2]})
    assert result == "FALSE"


async def test_empty_clients_to_check_returns_true():
    """clients_to_check=[] means no players to check — vacuously TRUE."""
    clients = [FakeClient(), FakeClient()]
    clients[0].zone_name.return_value = "WizardCity/WC_Ravenwood"
    clients[1].zone_name.return_value = "WizardCity/WC_Hub"
    result = await cz.run(FakeMachine(), clients, {"zone": "WizardCity/WC_Ravenwood", "clients_to_check": []})
    assert result == "TRUE"
