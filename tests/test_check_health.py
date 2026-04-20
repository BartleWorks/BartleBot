import bartlebot.nodes.combat.check_health as ch
from tests.conftest import FakeClient, FakeMachine


async def test_all_above_threshold_returns_true():
    clients = [FakeClient(), FakeClient()]
    clients[0].calc_health_ratio.return_value = 0.9
    clients[1].calc_health_ratio.return_value = 0.85
    result = await ch.run(FakeMachine(), clients, {"threshold": 0.8})
    assert result == "TRUE"


async def test_one_below_threshold_returns_false():
    clients = [FakeClient(), FakeClient()]
    clients[0].calc_health_ratio.return_value = 1.0
    clients[1].calc_health_ratio.return_value = 0.5
    result = await ch.run(FakeMachine(), clients, {"threshold": 0.8})
    assert result == "FALSE"


async def test_exactly_at_threshold_returns_true():
    client = FakeClient()
    client.calc_health_ratio.return_value = 0.5
    result = await ch.run(FakeMachine(), [client], {"threshold": 0.5})
    assert result == "TRUE"


async def test_default_threshold_full_health_true():
    client = FakeClient()
    client.calc_health_ratio.return_value = 1.0
    result = await ch.run(FakeMachine(), [client], {})
    assert result == "TRUE"


async def test_default_threshold_not_full_health_false():
    client = FakeClient()
    client.calc_health_ratio.return_value = 0.99
    result = await ch.run(FakeMachine(), [client], {})
    assert result == "FALSE"


async def test_single_client_below_returns_false():
    client = FakeClient()
    client.calc_health_ratio.return_value = 0.3
    result = await ch.run(FakeMachine(), [client], {"threshold": 0.5})
    assert result == "FALSE"
