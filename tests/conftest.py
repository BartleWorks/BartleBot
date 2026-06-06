"""Shared test doubles for node unit tests."""

import logging
from unittest.mock import AsyncMock, MagicMock

from wizwalker import XYZ
from wizwalker.extensions.wizsprinter import SprintyClient

# ---------------------------------------------------------------------------
# API drift guard
#
# Validates that every method mocked in FakeClient still exists on the real
# SprintyClient (which inherits from WizWalker's Client).  When the WizSprinter
# pin in pyproject.toml is bumped, any renamed or removed method will cause an
# immediate ImportError here rather than silently passing stale tests.
#
# The four MagicMock attributes — body, root_window, stats, mouse_handler —
# are instance attributes set in WizWalker's Client.__init__, not class-level
# attributes, so hasattr(SprintyClient, name) cannot see them.  They are
# excluded from this check and must be verified manually when the pin changes.
# ---------------------------------------------------------------------------
_FAKED_METHODS = frozenset(
    {
        "zone_name",
        "teleport",
        "is_loading",
        "is_in_npc_range",
        "is_in_dialog",
        "send_key",
        "find_closest_mob",
        "find_closest_by_vague_name",
        "tp_to",
        "in_battle",
        "calc_health_ratio",
        "calc_mana_ratio",
        "has_potion",
        "use_potion_if_needed",
        "use_potion",
        "tp_to_closest_health_wisp",
        "tp_to_closest_mana_wisp",
        "backpack_space",
        "get_base_entities_with_vague_name",
    }
)
for _method in _FAKED_METHODS:
    if not hasattr(SprintyClient, _method):
        raise AttributeError(
            f"FakeClient mocks {_method!r} but SprintyClient has no such attribute. "
            "Update FakeClient to match the current WizSprinter API."
        )
del _method


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
        self.bot_config = bot_config
        self._options = options or {}
        self.logs: list[tuple] = []
        self.drops: list[tuple] = []

    def log(self, msg: str, level: int = logging.INFO) -> None:
        self.logs.append((msg, level))

    def on_drop(self, item: str, qty: int, client_index: int = 0) -> None:
        self.drops.append((item, qty, client_index))

    def has_drop(self, item: str, client_index: int) -> bool:
        key = item.strip().lower()
        return any(client_index == ci and name.strip().lower() == key for name, _qty, ci in self.drops)

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


class FakeParamForm:
    """Minimal stub for _DynamicParamForm used by recording coroutines.

    Recording coroutines call only two methods:
      - set_str(key, value)    — stores a string field
      - set_xyz(key, x, y, z) — stores an XYZ coordinate triple

    Tests inspect .str_fields and .xyz_fields directly.
    """

    def __init__(self):
        self.str_fields: dict[str, str] = {}
        self.xyz_fields: dict[str, tuple[float, float, float]] = {}

    def set_str(self, key: str, value: str) -> None:
        self.str_fields[key] = value

    def set_xyz(self, key: str, x: float, y: float, z: float) -> None:
        self.xyz_fields[key] = (x, y, z)
