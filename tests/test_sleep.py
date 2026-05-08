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
