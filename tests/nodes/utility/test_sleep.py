"""Unit tests for the sleep node."""

from unittest.mock import patch

import bartlebot.nodes.utility.sleep as sleep_node
from tests.conftest import FakeMachine


async def test_returns_success():
    result = await sleep_node.run(FakeMachine(), [], {"duration": 0})
    assert result == "SUCCESS"


async def test_sleep_called_with_correct_duration():
    with patch("asyncio.sleep") as mock_sleep:
        result = await sleep_node.run(FakeMachine(), [], {"duration": 1.5})

    assert result == "SUCCESS"
    mock_sleep.assert_awaited_once_with(1.5)


async def test_use_safety_delay_sleeps_the_option_not_the_duration():
    machine = FakeMachine(options={"Safety Delay": 4.5})

    with patch("asyncio.sleep") as mock_sleep:
        result = await sleep_node.run(machine, [], {"duration": 1.5, "use_safety_delay": True})

    assert result == "SUCCESS"
    mock_sleep.assert_awaited_once_with(4.5)


async def test_safety_delay_off_or_absent_sleeps_the_duration():
    machine = FakeMachine(options={"Safety Delay": 4.5})

    with patch("asyncio.sleep") as mock_sleep:
        await sleep_node.run(machine, [], {"duration": 1.5, "use_safety_delay": False})
        await sleep_node.run(machine, [], {"duration": 2.0})

    assert [c.args for c in mock_sleep.await_args_list] == [(1.5,), (2.0,)]


def test_safety_delay_is_an_option_of_the_bot_only_while_a_sleep_reads_it():
    from bartlebot.bot.schema import BotConfig, StateModel  # noqa: PLC0415
    from bartlebot.options.bot_options import required_option_specs  # noqa: PLC0415

    def bot(use: bool) -> BotConfig:
        state = StateModel(id="a", node_type="sleep", params={"duration": 1.0, "use_safety_delay": use})
        return BotConfig(id="b", name="B", initial="a", recovery_state="a", states=[state], transitions=[])

    assert "Safety Delay" not in required_option_specs(bot(False))
    assert "Safety Delay" in required_option_specs(bot(True))
