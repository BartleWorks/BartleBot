import bartlebot.nodes.combat.check_health_and_mana as chm
from tests.conftest import FakeClient, FakeMachine


def _client(health: float = 1.0, mana: float = 1.0) -> FakeClient:
    client = FakeClient()
    client.calc_health_ratio.return_value = health
    client.calc_mana_ratio.return_value = mana
    return client


async def test_everyone_above_both_thresholds_returns_true():
    clients = [_client(0.9, 0.6), _client(0.85, 0.9)]
    result = await chm.run(FakeMachine(), clients, {"health_threshold": 0.8, "mana_threshold": 0.5})
    assert result == "TRUE"


async def test_low_health_returns_false():
    clients = [_client(1.0, 1.0), _client(0.5, 1.0)]
    result = await chm.run(FakeMachine(), clients, {"health_threshold": 0.8, "mana_threshold": 0.5})
    assert result == "FALSE"


async def test_low_mana_returns_false():
    clients = [_client(1.0, 1.0), _client(1.0, 0.2)]
    result = await chm.run(FakeMachine(), clients, {"health_threshold": 0.8, "mana_threshold": 0.5})
    assert result == "FALSE"


async def test_exactly_at_threshold_returns_true():
    result = await chm.run(FakeMachine(), [_client(0.5, 0.5)], {"health_threshold": 0.5, "mana_threshold": 0.5})
    assert result == "TRUE"


async def test_defaults_require_everyone_full_on_both():
    assert await chm.run(FakeMachine(), [_client(1.0, 1.0)], {}) == "TRUE"
    assert await chm.run(FakeMachine(), [_client(1.0, 0.99)], {}) == "FALSE"
    assert await chm.run(FakeMachine(), [_client(0.99, 1.0)], {}) == "FALSE"


async def test_a_zero_threshold_skips_that_stat_without_reading_it():
    client = _client(health=0.1, mana=1.0)
    result = await chm.run(FakeMachine(), [client], {"health_threshold": 0.0, "mana_threshold": 0.5})

    assert result == "TRUE"
    client.calc_health_ratio.assert_not_called()


async def test_both_thresholds_zero_is_always_true():
    client = _client(0.0, 0.0)
    result = await chm.run(FakeMachine(), [client], {"health_threshold": 0.0, "mana_threshold": 0.0})

    assert result == "TRUE"
    client.calc_health_ratio.assert_not_called()
    client.calc_mana_ratio.assert_not_called()


async def test_a_tiny_mana_threshold_catches_only_an_empty_player():
    """The fled-from-battle case: 0.01 flags a player at zero mana and nobody else."""
    params = {"health_threshold": 0.0, "mana_threshold": 0.01}
    assert await chm.run(FakeMachine(), [_client(mana=0.05), _client(mana=0.5)], params) == "TRUE"
    assert await chm.run(FakeMachine(), [_client(mana=0.5), _client(mana=0.0)], params) == "FALSE"


async def test_false_names_each_player_and_stat_that_fell_short():
    machine = FakeMachine()
    clients = [_client(0.3, 1.0), _client(1.0, 1.0), _client(1.0, 0.1)]
    await chm.run(machine, clients, {"health_threshold": 0.5, "mana_threshold": 0.5})

    msg = next(m for m, _lvl in machine.logs if "FALSE" in m)
    assert "player 1 health" in msg
    assert "player 3 mana" in msg
    assert "player 2" not in msg
