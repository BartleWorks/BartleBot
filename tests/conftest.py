"""Shared test doubles for node unit tests."""

import logging
from unittest.mock import AsyncMock, MagicMock

from wizwalker import XYZ


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
        self.body = MagicMock()
        self.body.position = AsyncMock(return_value=XYZ(0.0, 0.0, 0.0))
        self.root_window = MagicMock()
        self.root_window.children = AsyncMock(return_value=[])
        self.calc_health_ratio = AsyncMock(return_value=1.0)
        self.calc_mana_ratio = AsyncMock(return_value=1.0)
        self.has_potion = AsyncMock(return_value=True)
        self.use_potion_if_needed = AsyncMock()
        self.use_potion = AsyncMock(return_value=True)
        self.stats = MagicMock()
        self.stats.potion_max = AsyncMock(return_value=10)
        self.stats.potion_charge = AsyncMock(return_value=10)
        self.tp_to_closest_health_wisp = AsyncMock(return_value=False)
        self.tp_to_closest_mana_wisp = AsyncMock(return_value=False)
        self.backpack_space = AsyncMock(return_value=(0, 100))  # (used, total)
        self.mouse_handler = MagicMock()
        self.mouse_handler.__aenter__ = AsyncMock(return_value=None)
        self.mouse_handler.__aexit__ = AsyncMock(return_value=False)
        self.mouse_handler.click_window = AsyncMock()
        self.get_base_entities_with_vague_name = AsyncMock(return_value=[])


class FakeEntity:
    """Minimal stub for a WizWalker entity (mob / NPC / reagent)."""

    def __init__(self, name: str = "TestEnemy/TestEnemy"):
        self.object_name = AsyncMock(return_value=name)
        self.location = AsyncMock(return_value=XYZ(100.0, 200.0, 0.0))


class FakeMachine:
    """Minimal stub for BotMachine that records logs and drops."""

    def __init__(self, bot_config=None, options: dict | None = None):
        self._bot_config = bot_config
        self._options = options or {}
        self.logs: list[tuple] = []
        self.drops: list[tuple] = []

    def log(self, msg: str, level: int = logging.INFO) -> None:
        self.logs.append((msg, level))

    def on_drop(self, item: str, qty: int, client_index: int = 0) -> None:
        self.drops.append((item, qty, client_index))

    def get_option(self, name: str, default=None):
        """Return configured option value, or a sensible test default.

        Safety Delay uses 0.01s (10ms): small enough to be negligible but reliable.
        timeout=0 and timeout=0.001 can race against asyncio.wait_for's own scheduling
        overhead in Python 3.12+ and fire before the condition coroutine runs even once.
        10ms gives the event loop plenty of room while still being 10× faster than the
        original 0.1s default.
        Tests that need a longer window (e.g. late-appearing dialog tests) should pass
        options={"Safety Delay": <value>} to the constructor.
        """
        return self._options.get(name, 0.01 if name == "Safety Delay" else 0)
