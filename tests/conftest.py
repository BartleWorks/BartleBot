"""Shared test doubles for node unit tests."""

from unittest.mock import AsyncMock, MagicMock

from bartlebot.log_level import LogLevel


class FakeClient:
    """AsyncMock-backed stub for a WizSprinter SprintyClient.

    Each method is an AsyncMock so tests can configure return values and
    side_effect sequences, or assert call counts without any Wizard101 process.
    """

    def __init__(self):
        self.zone_name = AsyncMock(return_value="TestZone")
        self.teleport = AsyncMock()
        self.is_loading = AsyncMock(return_value=False)
        self.is_in_npc_range = AsyncMock(return_value=False)
        self.is_in_dialog = AsyncMock(return_value=False)
        self.send_key = AsyncMock()
        self.find_closest_mob = AsyncMock(return_value=None)
        self.find_closest_by_vague_name = AsyncMock(return_value=None)
        self.tp_to = AsyncMock()
        self.in_battle = AsyncMock(return_value=False)
        self.root_window = MagicMock()
        self.root_window.children = AsyncMock(return_value=[])


class FakeEntity:
    """Minimal stub for a WizWalker entity (mob / NPC)."""

    def __init__(self, name: str = "TestEnemy/TestEnemy"):
        self.object_name = AsyncMock(return_value=name)


class FakeMachine:
    """Minimal stub for BotMachine that records logs and drops."""

    def __init__(self, bot_config=None):
        self._bot_config = bot_config
        self.logs: list[tuple] = []
        self.drops: list[tuple] = []

    def log(self, msg: str, level: LogLevel = LogLevel.INFO) -> None:
        self.logs.append((msg, level))

    def on_drop(self, item: str, qty: int, client_index: int = 0) -> None:
        self.drops.append((item, qty, client_index))

    def get_option(self, name: str, default=None):
        """Return 0 for numeric options so nodes incur no real delay in tests."""
        return 0
