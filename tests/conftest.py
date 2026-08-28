"""Shared test doubles for node unit tests."""

import ctypes
import logging
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from wizwalker import XYZ
from wizwalker.extensions.wizsprinter import SprintyClient

from bartlebot.paths import project_root

# ---------------------------------------------------------------------------
# API drift guard
#
# Validates that every method mocked in FakeClient still exists on the real
# SprintyClient (which inherits from WizWalker's Client).  When the WizSprinter
# pin in pyproject.toml is bumped, any renamed or removed method will cause an
# immediate ImportError here rather than silently passing stale tests.
#
# The MagicMock attributes — body, root_window, stats, mouse_handler,
# combat_handler — are instance attributes set in WizWalker's Client.__init__,
# not class-level attributes, so hasattr(SprintyClient, name) cannot see them.
# They are excluded from this check and must be verified manually when the pin
# changes.
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
        "find_closest_by_predicate",
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
        "get_base_entities_with_predicate",
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

    _next_handle = 1000

    def __init__(self):
        # Distinct per instance so wizard_names' handle-keyed cache treats each FakeClient
        # as a separate client, the way real windows behave.
        FakeClient._next_handle += 1
        self.window_handle = FakeClient._next_handle
        self.combat_handler = MagicMock()
        self.combat_handler.get_members = AsyncMock(return_value=[])
        self.zone_name = AsyncMock(return_value="TestZone")
        self.teleport = AsyncMock()
        self.is_loading = AsyncMock(return_value=False)
        self.is_in_npc_range = AsyncMock(return_value=False)
        self.is_in_dialog = AsyncMock(return_value=False)
        self.send_key = AsyncMock()
        self.find_closest_mob = AsyncMock(return_value=None)
        self.find_closest_by_vague_name = AsyncMock(return_value=None)
        self.find_closest_by_predicate = AsyncMock(return_value=None)
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
        self.get_base_entities_with_predicate = AsyncMock(return_value=[])
        # Distinct per instance, like window_handle, so each FakeClient looks like a
        # different character to wizard_names' switch detection. Tests that care set
        # read_value_from_offset.return_value directly to simulate a character switch.
        self.client_object = MagicMock()
        self.client_object.read_value_from_offset = AsyncMock(return_value=FakeClient._next_handle * 1000)
        # No name behavior by default, so the memory name route answers None and callers
        # fall through to the character-page route. Tests that want the memory route give
        # this a behavior — see tests/test_wizard_names.py.
        self.client_object.inactive_behaviors = AsyncMock(return_value=[])


class FakeEntity:
    """Minimal stub for a WizWalker entity (mob / NPC / reagent)."""

    def __init__(self, name: str = "TestEnemy/TestEnemy"):
        self.object_name = AsyncMock(return_value=name)
        self.location = AsyncMock(return_value=XYZ(100.0, 200.0, 0.0))
        # Real entities expose their name through the template, not directly; predicate
        # searches read it that way, so the two must report the same name.
        template = MagicMock()
        template.object_name = self.object_name
        self.object_template = AsyncMock(return_value=template)


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


def message_box_patches(visible_buttons=()):
    """Patch node_helpers' UI reads to stand in for an on-screen modal message box.

    `visible_buttons` is the set of MESSAGE_BOX_* paths the modal shows; clicks land only
    on those. Which buttons are showing is the ONLY way to tell the game's modals apart —
    they all share the MessageBoxModalWindow tree — so tests state a dialog by naming its
    buttons, the way the nodes identify it:

        (MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT)  the dungeon exit warning (confirm + cancel)
        (MESSAGE_BOX_RIGHT,)                     teleport_to_friend's friend-busy dialog
        ()                                       no modal

    Returns (patches, clicked_paths): apply the patches with an ExitStack or a `with`
    tuple, and assert against clicked_paths afterwards.
    """
    import bartlebot.nodes.node_helpers as nh

    visible = [list(p) for p in visible_buttons]
    clicked: list[list[str]] = []

    async def _is_visible(_client, path):
        return list(path) in visible

    async def _click(_client, path):
        clicked.append(path)
        return list(path) in visible

    return (
        patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=_is_visible)),
        patch.object(nh, "click_by_path", new=AsyncMock(side_effect=_click)),
    ), clicked


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


# ---------------------------------------------------------------------------
# Settings isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Point the SettingsManager singleton at a per-test directory.

    Code that resolves a value against the app settings — quicksell's three-state
    sell_* params, most of all — must not read whatever the developer running the
    suite happens to have configured, or the result differs between a laptop and
    CI. APPDATA is redirected rather than the internals patched, because that is
    the same door the real app goes through.

    Tests that build their own SettingsManager against tmp_path are unaffected:
    they get a second, private instance as before.
    """
    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415 — keeps node tests import-light

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(SettingsManager, "_instance", None)


# ---------------------------------------------------------------------------
# Timer resolution
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _fine_grained_timers():
    """Raise the Windows timer resolution for the duration of the test session.

    Windows schedules waits on a ~15.6 ms tick by default, so every ``asyncio.sleep``
    shorter than that still costs a full tick. The suite is mostly nodes polling on
    short intervals, which means thousands of 1-10 ms sleeps each rounding up to 15.6 ms
    — well over half the wall time, and none of it doing anything.

    ``timeBeginPeriod(1)`` drops the tick to ~1 ms. It cannot make a sleep return early,
    so nothing that asserts a lower bound on elapsed time can be affected; it only stops
    the short ones overshooting. The app itself does not do this — its own cadences are
    tenths of seconds, where the tick does not matter.

    Session-scoped and paired with ``timeEndPeriod`` because the setting is process-wide
    and refcounted by the OS.
    """
    if sys.platform != "win32":
        yield
        return
    winmm = ctypes.WinDLL("winmm")
    winmm.timeBeginPeriod(1)
    try:
        yield
    finally:
        winmm.timeEndPeriod(1)


# ---------------------------------------------------------------------------
# Strategy-store write guard
# ---------------------------------------------------------------------------

_STRATEGY_DIRS = (
    "bot_strategies",
    "strategies/player",
    "strategies/duel",
    "strategies/decks",
)


def _strategy_mtimes() -> tuple:
    """Modification time of each strategy directory.

    Adding a file bumps its directory's mtime, so this is enough to answer "did
    anything appear?" in four stat calls -- cheap enough to run either side of all
    2000-odd tests.  The more expensive listing only happens when this says yes.
    """
    root = project_root()
    out = []
    for sub in _STRATEGY_DIRS:
        try:
            out.append(os.stat(root / sub).st_mtime_ns)
        except OSError:
            out.append(None)
    return tuple(out)


def _strategy_snapshot() -> set:
    """Every strategy file currently in the repo, as (dir, name) pairs."""
    root = project_root()
    found = set()
    for sub in _STRATEGY_DIRS:
        d = root / sub
        if not d.is_dir():
            continue
        with os.scandir(d) as entries:
            found.update((sub, e.name) for e in entries if e.name.endswith(".json"))
    return found


# Listed once at import, before any test runs: the baseline of what legitimately
# lives in the repo.  A per-test listing would cost more than the guard is worth.
_SESSION_STRATEGY_FILES: set = set()


@pytest.fixture(scope="session", autouse=True)
def _record_baseline_strategy_files():
    global _SESSION_STRATEGY_FILES
    _SESSION_STRATEGY_FILES = _strategy_snapshot()
    yield


@pytest.fixture(autouse=True)
def _no_strategy_writes_into_the_repo(_record_baseline_strategy_files):
    """Fail any test that saves a strategy into the real repo instead of tmp_path.

    ``save_bot_strategy`` and friends default their root to ``project_root()``, so a
    test that calls one without redirecting the store writes a ``{uuid}.json`` file
    straight into the tracked ``bot_strategies/`` directory.  There are 51 real files
    in there under exactly that naming scheme, so the junk is indistinguishable from
    the genuine article by name -- ``.gitignore`` cannot separate them, and one has
    already been committed by accident once (0c12612).

    Catching it here instead: the offending file is removed and the test that wrote it
    is named, rather than the mess being left for `git status` to find later.  The fix
    in the test is to request the ``store`` fixture, or pass ``root=tmp_path``.
    """
    before_mtimes = _strategy_mtimes()
    yield
    if _strategy_mtimes() == before_mtimes:
        return
    # A directory changed, so now it is worth listing.  The comparison is against the
    # session baseline rather than a per-test listing, which is the whole point: the
    # cost per test stays four stat calls no matter how large the library grows.
    new = _strategy_snapshot() - _SESSION_STRATEGY_FILES
    if not new:
        return
    root = project_root()
    for sub, name in new:
        (root / sub / name).unlink(missing_ok=True)
    listed = ", ".join(f"{sub}/{name}" for sub, name in sorted(new))
    pytest.fail(
        f"This test wrote {len(new)} strategy file(s) into the repo: {listed}\n"
        "Redirect the store first: request the `store` fixture, or pass root=tmp_path."
    )
