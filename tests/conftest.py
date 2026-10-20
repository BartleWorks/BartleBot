"""Shared test doubles for node unit tests."""

import asyncio
import ctypes
import itertools
import logging
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from wizwalker import XYZ
from wizwalker.extensions.wizsprinter import SprintyClient

from bartlebot.clients import sightings
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
        "get_health_wisps",
        "get_mana_wisps",
        "get_mobs",
        "backpack_space",
        "get_base_entities_with_vague_name",
        "get_base_entities_with_predicate",
        "get_base_entity_list",
    }
)
for _method in _FAKED_METHODS:
    if not hasattr(SprintyClient, _method):
        raise AttributeError(
            f"FakeClient mocks {_method!r} but SprintyClient has no such attribute. "
            "Update FakeClient to match the current WizSprinter API."
        )
del _method


#: Every FakeClient the running test has made. See FakeClient.get_base_entity_list.
_clients_in_this_test: list = []


async def _everyone_in_this_test() -> list:
    return [FakeGidEntity(c.gid) for c in _clients_in_this_test]


@pytest.fixture(autouse=True)
def _forget_fake_clients():
    """Start each test with no FakeClients sharing its realm. A list clear: costs nothing."""
    _clients_in_this_test.clear()
    yield
    _clients_in_this_test.clear()


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
        # The center of the fight this client is in, away from FakeEntity's location so a
        # teleport to one is never mistaken for a teleport to the other.
        self.duel = MagicMock()
        self.duel.position = AsyncMock(return_value=XYZ(500.0, 600.0, 0.0))
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
        # A level 180 Fire wizard, which is what Challenge Mode's stat caps read (scaled_ratios).
        self.stats.reference_level = AsyncMock(return_value=180)
        self.stats.school_id = AsyncMock(return_value=2343174)
        self.get_health_wisps = AsyncMock(return_value=[])
        self.get_mana_wisps = AsyncMock(return_value=[])
        self.get_mobs = AsyncMock(return_value=[])
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
        # this a behavior — see tests/game/test_wizard_names.py.
        self.client_object.inactive_behaviors = AsyncMock(return_value=[])
        # This character's global id, and what it has loaded. By default every FakeClient
        # a test makes has every other loaded -- one zone, realm and instance, which is
        # what node tests written before realm checks assume. see_each_other() splits them.
        self.gid = FakeClient._next_handle * 1000 + 1
        self.client_object.global_id_full = AsyncMock(return_value=self.gid)
        self.client_object.object_name = AsyncMock(return_value=FAKE_PLAYER_TEMPLATE)
        self.get_base_entity_list = AsyncMock(side_effect=_everyone_in_this_test)
        _clients_in_this_test.append(self)


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


#: The template every player is made from, a FakeClient's own character included. The real
#: name is not needed: the code compares an entity's template with the client's own.
FAKE_PLAYER_TEMPLATE = "Player Object"


class FakeGidEntity:
    """An entity-list entry that is only a global id: another player, as a client sees it.

    *template* other than the player's makes it an NPC or a mob instead.
    """

    def __init__(self, gid: int, template: str = FAKE_PLAYER_TEMPLATE):
        self.global_id_full = AsyncMock(return_value=gid)
        self.object_name = AsyncMock(return_value=template)


def prompt_at(client: "FakeClient", *points, initially: bool = False) -> None:
    """Make *client*'s interact prompt show only while its last teleport was to one of *points*.

    That is how the game behaves, so a test using this does not depend on how many times a
    node reads the prompt, only on where it sends the client. *initially* is the prompt
    before the first teleport: True for a client that starts beside some other object.
    Points are XYZs or the ``{"x", "y", "z"}`` dicts node params hold, compared by
    coordinates, since XYZ has no equality of its own.
    """
    wanted = [(p["x"], p["y"], p["z"]) if isinstance(p, dict) else tuple(p) for p in points]

    async def _showing() -> bool:
        if not client.teleport.call_args_list:
            return initially
        return tuple(client.teleport.call_args_list[-1].args[0]) in wanted

    client.is_in_npc_range.side_effect = _showing


def see_each_other(*clients: "FakeClient") -> None:
    """Make *clients* have each other loaded, and nobody else: one zone, realm and instance.

    By default a test's FakeClients all see each other; calling this once per group is how
    a test splits them, e.g. ``see_each_other(a, b); see_each_other(c)``.
    """
    for viewer in clients:
        viewer.get_base_entity_list.side_effect = None
        viewer.get_base_entity_list.return_value = [FakeGidEntity(c.gid) for c in clients]


class FakeMachine:
    """Minimal stub for BotMachine that records logs and drops."""

    def __init__(self, bot_config=None, options: dict | None = None):
        self.bot_config = bot_config
        self._options = options or {}
        self.logs: list[tuple] = []
        self.drops: list[tuple] = []
        self.chat_baselines: dict[object, str] = {}
        self.drop_poll: asyncio.Task | None = None
        # Which client hwnds are currently exempt from ClientHealthMonitor, and which have
        # been put back. A node that suspends and forgets to restore leaves a real crash
        # unnoticed for the rest of the run, so both sides are recorded.
        self.health_checks_suspended: set[int] = set()
        self.health_checks_resumed: set[int] = set()
        # Every abandon_client call, as (client, reason). ``recovers`` is what it answers:
        # False by default, which is the node tester's run with no recovery behind it.
        self.abandoned: list[tuple[object, str]] = []
        self.recovers = False
        self.is_stopped = False
        # Mirrors BotMachine.port_map: None until something surveys who can port to whom.
        self.port_map = None
        # Mirrors BotMachine.node_scratch: what a node keeps for its own rerun.
        self.node_scratch: dict[str, object] = {}
        # The real record, retaining as it does mid-run, so node tests exercise its rules.
        self.together = sightings.TogetherRecord()

    def note_sightings(self, result) -> None:
        self.together.note(result)

    def are_together(self, clients) -> bool:
        return self.together.are_together(clients)

    def together_with(self, client, clients) -> list:
        return self.together.together_with(client, clients)

    def forget_sightings(self, client) -> None:
        self.together.forget(client)

    def log(self, msg: str, level: int = logging.INFO) -> None:
        self.logs.append((msg, level))

    def note_flee(self) -> bool:
        self.flees = getattr(self, "flees", 0) + 1
        return False

    def note_fight_fled(self, state_id, level) -> None:
        """Mirror BotMachine: a node's fight was fled, on *level*. Recorded as (state_id, level)."""
        self.fights_fled = [*getattr(self, "fights_fled", []), (state_id, level)]

    def on_drop(self, item: str, qty: int, client_index: int = 0) -> None:
        self.drops.append((item, qty, client_index))

    def chat_baseline(self, client) -> str | None:
        """Mirror BotMachine: None means never seeded, "" is a real empty baseline."""
        return self.chat_baselines.get(client)

    def set_chat_baseline(self, client, text: str) -> None:
        self.chat_baselines[client] = text

    def start_drop_poll(self, task: asyncio.Task) -> None:
        """Mirror BotMachine: a new fight's poll cancels the previous one."""
        if self.drop_poll is not None:
            self.drop_poll.cancel()
        self.drop_poll = task

    async def wait_for_drop_poll(self) -> None:
        if self.drop_poll is not None and not self.drop_poll.done():
            await asyncio.wait({self.drop_poll})

    def suspend_health_checks_for(self, hwnds) -> None:
        """Record a health-check suspension. Real BotMachine flags them on its monitor."""
        self.health_checks_suspended.update(hwnds)

    def resume_health_checks_for(self, hwnds) -> None:
        self.health_checks_suspended.difference_update(hwnds)
        self.health_checks_resumed.update(hwnds)

    def note_logged_in(self, hwnds) -> None:
        """Mirror BotMachine: these players are back from a login and nothing has moved them."""
        self.fresh_logins = {*getattr(self, "fresh_logins", set()), *hwnds}

    def is_freshly_logged_in(self, client) -> bool:
        return client.window_handle in getattr(self, "fresh_logins", set())

    def abandon_client(self, client, reason: str) -> bool:
        """Record a handover to crash recovery. Real BotMachine kills the client, or stops the run."""
        self.abandoned.append((client, reason))
        return self.recovers

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
    """Minimal stub for _DynamicParamForm used by recording workflows.

    Recording workflows write through three methods, and snapshot/restore around them:
      - set_str(key, value)      — stores a string field
      - set_xyz(key, x, y, z)   — stores an XYZ coordinate triple
      - set_players(key, value) — stores a player list, read back by players(key)

    Tests inspect .str_fields, .xyz_fields and .player_fields directly.
    """

    def __init__(self):
        self.str_fields: dict[str, str] = {}
        self.xyz_fields: dict[str, tuple[float, float, float]] = {}
        self.player_fields: dict[str, list[int]] = {}

    def players(self, key: str) -> list[int]:
        return self.player_fields.get(key, [])

    def set_players(self, key: str, value: list[int]) -> None:
        self.player_fields[key] = list(value)

    def set_str(self, key: str, value: str) -> None:
        self.str_fields[key] = value

    def set_xyz(self, key: str, x: float, y: float, z: float) -> None:
        self.xyz_fields[key] = (x, y, z)

    def snapshot(self, keys) -> dict:
        return {key: (self.str_fields.get(key), self.xyz_fields.get(key), self.player_fields.get(key)) for key in keys}

    def restore(self, saved: dict) -> None:
        for key, (text, xyz, players) in saved.items():
            for fields, value in ((self.str_fields, text), (self.xyz_fields, xyz), (self.player_fields, players)):
                if value is None:
                    fields.pop(key, None)
                else:
                    fields[key] = value


# ---------------------------------------------------------------------------
# Settings isolation
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _appdata_root(tmp_path_factory):
    """The parent of every test's private ``%APPDATA%``, made once per worker."""
    return tmp_path_factory.getbasetemp() / "appdata"


_APPDATA_SERIAL = itertools.count()

#: Where the install key and the file types go in a test that does not choose a key of its
#: own: one throwaway key per worker, never the vendor's or ``Software\Classes``.
UNGUARDED_REGISTRY_KEY = rf"Software\Bartleby-test-unguarded-{os.getpid()}"


@pytest.fixture(scope="session", autouse=True)
def _remove_unguarded_registry_key():
    """Remove whatever a test wrote under :data:`UNGUARDED_REGISTRY_KEY`, at the end of the session."""
    yield
    if sys.platform == "win32":
        import winreg  # noqa: PLC0415

        from bartlebot.shared.install_key import delete_tree  # noqa: PLC0415

        delete_tree(winreg.HKEY_CURRENT_USER, UNGUARDED_REGISTRY_KEY)


@pytest.fixture(autouse=True)
def isolated_settings(_appdata_root, monkeypatch):
    """Point the SettingsManager singleton at a per-test directory.

    Code that resolves a value against the app settings — quicksell's three-state
    sell_* params, most of all — must not read whatever the developer running the
    suite happens to have configured, or the result differs between a laptop and
    CI. APPDATA is redirected rather than the internals patched, because that is
    the same door the real app goes through.

    Tests that build their own SettingsManager against tmp_path are unaffected:
    they get a second, private instance as before.

    The directory is *named*, not created. This fixture is autouse, so it runs for all
    5,000-odd tests, and ``tmp_path`` — which is what it used to ask for — pays a real
    ``mkdir`` under every one of them: ~4.8 ms each on Windows, around 25 s of the
    suite's CPU, for a directory that only the few dozen tests reaching ``save()`` ever
    want. Everything downstream already copes with the path being absent: ``load()``
    checks ``exists()`` and falls back to the defaults, ``save()`` creates its own
    parents, and ``_adopt_legacy_appdata`` looks for a sibling that is equally not
    there. The counter is what keeps a test that *does* write from being read by the
    next one — it only has to be unique within a worker, since each xdist worker gets
    its own basetemp.
    """
    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415 — keeps node tests import-light

    serial = next(_APPDATA_SERIAL)
    monkeypatch.setenv("APPDATA", str(_appdata_root / f"t{serial}"))
    # And %LocalAppData%, where the machine's caches go: wizwalker's, and update staging.
    monkeypatch.setenv("LOCALAPPDATA", str(_appdata_root / f"t{serial}-local"))
    # A developer's shell with BARTLEBOT_DEV set would otherwise send every store write
    # that takes the default roots into the working tree — see store_roots.
    monkeypatch.delenv("BARTLEBOT_DEV", raising=False)
    monkeypatch.setattr(SettingsManager, "_instance", None)
    # Nor may another product installed on the developer's machine decide which shared
    # Official set the suite reads (see shared.official_set).
    from bartlebot.shared import official_set  # noqa: PLC0415

    monkeypatch.setattr(official_set, "_registry", official_set.NoRegistry())
    official_set.reset_shipped_root()
    # The art index is cached, and a cache read under the last test's APPDATA describes
    # an art folder this test does not have (#239's tests write one each).
    from bartlebot.game import card_art  # noqa: PLC0415

    card_art.forget_cache()
    # Nor may a test that reaches an install hook write the machine's install key or file
    # types: both registry roots point at a throwaway key, removed at the end of the
    # session, and Explorer is never told (see packaging.install_hooks). Before this, a
    # hook test registered all six share types to the venv's python.
    from bartlebot.shared import file_types, install_key  # noqa: PLC0415

    monkeypatch.setattr(install_key, "REGISTRY_KEY", UNGUARDED_REGISTRY_KEY)
    monkeypatch.setattr(file_types, "CLASSES_KEY", rf"{UNGUARDED_REGISTRY_KEY}\Classes")
    monkeypatch.setattr(file_types, "notify", lambda: None)
    # Nor the game's compatibility settings, which the scaled-window tile can write.
    from bartlebot.shared import game_dpi  # noqa: PLC0415

    monkeypatch.setattr(game_dpi, "LAYERS_KEY", rf"{UNGUARDED_REGISTRY_KEY}\Layers")
    # Nor the game the developer has installed, which would decide whether setup asks
    # for a game folder (see shared.game_install).
    from bartlebot.shared import game_install  # noqa: PLC0415

    monkeypatch.setattr(game_install, "_places", game_install.NoPlaces())


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
# Zone-settle grace
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fast_zone_settle_grace(monkeypatch):
    """Compress wait_for_zone_with_recovery's straggler settle window.

    ZONE_SETTLE_GRACE is five seconds of real time, and it is paid by every test whose
    zone check does not succeed on the first read — a handful of node failure paths, plus
    every recovery test. node_helpers reads it at call time rather than binding it as a
    default argument, so patching the module attribute reaches it.

    A test whose subject *is* the grace (the #70 regression tests) passes ``settle_grace``
    explicitly instead of relying on this.
    """
    from bartlebot.nodes import node_helpers

    monkeypatch.setattr(node_helpers, "ZONE_SETTLE_GRACE", 0.05)


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
    test that calls one without redirecting the store writes a file straight into a
    tracked store directory.  Its name comes from the strategy's name, just as a real
    one's does, so ``.gitignore`` cannot separate the junk from the genuine article,
    and one has already been committed by accident once (0c12612).

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


# ---------------------------------------------------------------------------
# Application font
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp_cls():
    """pytest-qt's hook for *how* the one QApplication per worker is built.

    The font is set in its constructor because the autouse fixture below cannot reach
    the first GUI test in a worker: that test is what makes pytest-qt build the app, so
    when the fixture runs there is no app yet to set a font on. It measured at the
    platform's 9pt and every later test measured at the token, which surfaced as three
    modules failing under ``-n auto`` and passing alone — a worker-order flake of the
    kind "One QApplication per worker" in tests/CLAUDE.md is about.

    The app's Qt attributes are set here too, before the app exists, as ``bartlebot.py``
    sets them: which widgets turn native depends on them (#126). So is the guard that
    keeps the first-run dialog from opening, for the same reason: it must hold from the
    first GUI test.
    """
    from PyQt6.QtWidgets import QApplication  # noqa: PLC0415 - keeps Qt out of non-GUI runs

    from bartlebot.gui.native_widgets import keep_siblings_alien  # noqa: PLC0415
    from bartlebot.gui.theming.theme import THEME  # noqa: PLC0415

    keep_siblings_alien()
    _refuse_first_run_dialog()

    class _AppFontApplication(QApplication):
        def __init__(self, argv):
            super().__init__(argv)
            self.setFont(THEME.ui_font())

    return _AppFontApplication


#: What a test that opens the first-run dialog fails with.
FIRST_RUN_REFUSED = "A test opened the first-run dialog modally"


def _refuse_first_run_dialog() -> None:
    """Fail any test that opens the first-run dialog with ``exec()`` (epic #201, Stage 6).

    Every test has a ``%APPDATA%`` of its own with no ``preferences.json``, so to the app
    every test is a first run. No test builds an ``AppController`` today, which is what
    opens it; this stops one that does from blocking the suite on a modal nobody answers.
    The dialog's own tests drive it without ``exec()``.
    """
    from bartlebot.gui.dialogs.first_run_dialog import FirstRunDialog  # noqa: PLC0415

    def refuse(self) -> int:
        raise AssertionError(f"{FIRST_RUN_REFUSED}; stub AppController._offer_first_run or FirstRunDialog instead")

    FirstRunDialog.exec = refuse


@pytest.fixture(autouse=True)
def _production_app_font():
    """Measure at the font the app actually runs with.

    ``AppController._apply_theme`` sets the app font to ``THEME.ui_font()``; pytest-qt's
    ``QApplication`` keeps the platform's own 9pt. The two agreed while the body size was
    the 12px equivalent of 9pt, and any change to it splits them — every test that
    measures a font box then measures a size the app never shows, and a baseline or slot
    that is wrong in the app passes here. ``test_the_filename_and_author_sit_on_the_bot_name_s_baseline``
    failed that way, against a bot name sized from the token beside a filename at 9pt.

    Set rather than restored: it is the same value for every test, so there is nothing
    to leak. Applied only once — assigning a font, like assigning a stylesheet, is a
    change event delivered to every live widget, and the guard keeps that off the 3,000
    tests that never build one. Qt is reached through ``sys.modules`` so a non-GUI run
    does not import it.
    """
    qtwidgets = sys.modules.get("PyQt6.QtWidgets")
    if qtwidgets is not None:
        app = qtwidgets.QApplication.instance()
        if app is not None:
            from bartlebot.gui.theming.theme import THEME  # noqa: PLC0415 - keeps Qt out of non-GUI runs

            if app.font().pixelSize() != THEME.font_body_px:
                app.setFont(THEME.ui_font())
    yield


# ---------------------------------------------------------------------------
# Application stylesheet
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_app_stylesheet():
    """Undo any ``QApplication.setStyleSheet`` a test leaves behind.

    The theme tests broadcast a switch the way ``AppController._apply_theme`` does —
    ``qapp.setStyleSheet(THEME.global_stylesheet())`` — because that is the only thing
    that reaches a themed child of a plain parent. There is one QApplication per xdist
    worker, though, so the stylesheet outlives the test that set it and every widget
    built afterwards in that worker is styled by it.

    That is order-dependent state, and ``-n auto`` hands tests to workers in an order
    that changes run to run, so it surfaces as a flake rather than as a failure. The one
    that sent us looking: an app stylesheet raises the canvas viewport 461px -> 470px,
    which flips the parity of the centering indent QGraphicsView applies when the scene
    fits, and ``test_leaving_focus_mode_puts_the_camera_back`` then read a camera one
    device pixel from where it started -- 6 failures in 13 runs, and none in isolation.

    Restores only when the stylesheet actually changed — assigning one is a StyleChange
    broadcast to every live widget, which is not worth paying on 3,000 tests that never
    touch it.
    """
    from PyQt6.QtWidgets import QApplication  # noqa: PLC0415 - keeps Qt out of non-GUI runs

    app = QApplication.instance()
    before = app.styleSheet() if app is not None else None
    yield
    app = QApplication.instance()
    if app is not None and before is not None and app.styleSheet() != before:
        app.setStyleSheet(before)


# ---------------------------------------------------------------------------
# Widget lifetime
# ---------------------------------------------------------------------------


_WIDGETS_BEFORE = pytest.StashKey[set[int]]()
_WIDGETS_PINNED = pytest.StashKey[list]()


@pytest.hookimpl(wrapper=True, trylast=True)
def pytest_runtest_call(item):
    """Hold the test's top-level widgets from the moment it returns until they are reaped.

    pytest-qt's own ``pytest_runtest_call`` wrapper is ``tryfirst``, so it is outermost and
    calls ``processEvents()`` after the test function returns but before any fixture
    teardown. By then a widget the test kept only in a local is garbage — a ``QDialog``
    with its signals connected to its own bound methods is a reference cycle — and the
    cyclic collector can run inside a paint event that pump is delivering. Collecting the
    wrapper deletes the C++ dialog, its popup, and the very row whose ``paintEvent`` is on
    the stack: an access violation in ``QPainter``, and only when the allocation count
    happens to trip a collection there (issue #80).

    This wrapper is innermost, so it runs first once the test returns, and a strong
    reference is all it takes. ``_free_widgets_this_test_created`` releases the pins
    after it has freed the widgets deterministically.
    """
    try:
        return (yield)
    finally:
        before_ids = item.stash.get(_WIDGETS_BEFORE, None)
        if before_ids is not None:
            from PyQt6.QtWidgets import QApplication  # noqa: PLC0415

            if QApplication.instance() is not None:
                item.stash[_WIDGETS_PINNED] = [w for w in QApplication.topLevelWidgets() if id(w) not in before_ids]


@pytest.fixture(autouse=True)
def _free_widgets_this_test_created(request):
    """Destroy the top-level widgets a test built, before the next test starts.

    ``deleteLater()`` on its own frees nothing here: it posts a ``DeferredDelete``
    event that only arrives on an event-loop spin, and no Qt event loop runs during
    the suite. So every widget every test constructed used to stay alive for the
    whole session — 27,918 of them after 20 GUI files in one process.

    That is quadratic, not merely wasteful, because ``QApplication.setStyleSheet``
    delivers a ``StyleChange`` to every live widget: one app-wide theme broadcast
    cost 26 s at that population and 0.001 s at zero. Two tests in
    ``test_game_view_theme.py`` were spending 130 s between them on it.

    What is reaped, and why that is safe:

    * Only *top-level* widgets, since deleting one takes its children with it.
      41 parentless widgets were holding the other 20,000 alive, kept off the
      garbage collector by signal connections into the long-lived singletons.
    * Only widgets that did not exist when this fixture was set up. Being autouse
      and function-scoped, it is set up after every module- and session-scoped
      fixture and torn down after every other function-scoped one, so a widget
      owned by a wider-scoped fixture is already in ``before`` and survives, and a
      function-scoped fixture's widget is only freed once that fixture's own
      teardown has run.

    The scoping is the whole point. A blanket sweep over ``topLevelWidgets()``
    frees widgets a still-live fixture is holding, and the next
    ``processEvents()`` touches the corpse: exit 127, no traceback, reproducibly
    inside ``test_launch_card_widgets.py``. Reap per test, never globally.

    ``sendPostedEvents(None, DeferredDelete)`` is what actually performs the
    deletion, standing in for the event-loop spin. It flushes the whole queue,
    which is fine precisely because nothing accumulates in it any more: any
    ``deleteLater()`` a test posted names a widget this same teardown is freeing.
    """
    from PyQt6.QtWidgets import QApplication  # noqa: PLC0415 - keeps Qt out of non-GUI runs

    before = QApplication.topLevelWidgets() if QApplication.instance() is not None else []
    before_ids = {id(w) for w in before}
    request.node.stash[_WIDGETS_BEFORE] = before_ids
    yield
    if QApplication.instance() is None:
        return
    from PyQt6 import sip  # noqa: PLC0415
    from PyQt6.QtCore import QEvent, Qt  # noqa: PLC0415

    new = [w for w in QApplication.topLevelWidgets() if id(w) not in before_ids and not sip.isdeleted(w)]
    # Popups last. A QCompleter's popup is a parentless window the completer holds by raw
    # pointer and deletes itself, so freeing the popup first leaves its owner to free it
    # again: exit 127, no traceback, and only when topLevelWidgets() happens to list the
    # popup first. Freeing owners first lets each take its popup with it.
    popups = [w for w in new if w.windowType() == Qt.WindowType.Popup]
    for batch in ([w for w in new if w not in popups], popups):
        for widget in batch:
            if sip.isdeleted(widget):
                continue
            widget.close()
            widget.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    # Freed deterministically above, so the collector no longer has anything to race.
    if _WIDGETS_PINNED in request.node.stash:
        del request.node.stash[_WIDGETS_PINNED]
    # Held until here only so an id() cannot be recycled onto a new widget.
    del before


def main_window_stub():
    """A stand-in for MainWindow, for testing what a card action refreshes.

    The eight card-action slots are one line each on top of
    ``MainWindow._after_card_action``, and what is worth testing about them is which
    refreshes run -- the second-order bug in the profile-editor dialog work, where cancelling a dialog dropped the
    refresh for a profile already written to disk. Building a real MainWindow to ask that
    costs a game view, a canvas and a client manager, so the slots are called unbound
    against this instead.

    ``_after_card_action`` is the *real* method, bound onto the stub: it is the thing
    under test, and a reimplementation here would pass while the app did something else.
    Everything it reaches for is a recorder.

    Returns (calls, stub). The calls list is in order, so it also answers "did the cards
    get redrawn before or after the menu".
    """
    from types import SimpleNamespace  # noqa: PLC0415

    from bartlebot.gui.main_window import MainWindow  # noqa: PLC0415

    calls: list[str] = []
    stub = SimpleNamespace(
        live_game_area=(1200, 700),
        _rebuild_launch_menu=lambda: calls.append("menu"),
        refresh_minimum_size=lambda: calls.append("minimum"),
        _game_view_panel=SimpleNamespace(refresh_launch_lists=lambda: calls.append("cards")),
        left_sidebar=SimpleNamespace(refresh_player_availability=lambda: calls.append("players")),
    )
    stub._after_card_action = MainWindow._after_card_action.__get__(stub)
    return calls, stub


@pytest.fixture
def parentless_shows(qapp):
    """The class name of every widget shown while it had no parent, as the test goes on.

    Qt gives such a widget a window of its own, maps it, and unmaps it again the moment
    the next ``addWidget`` reparents it, so the user sees a window flash where the
    surface is about to appear. Nothing in the finished widget tree records it, which
    is why this watches the events instead: a filter on the ``QApplication`` sees the
    ``Show`` delivered to every object in the process.

    Ask for it in the test that builds a new dialog, card or row, build the surface, and
    assert the list is empty. Opt-in rather than autouse: a filter on every event of
    every test is paid 7,000 times, and some tests show a top-level widget on purpose.
    ``bartlebot/gui/CLAUDE.md``, "Never show a widget before it has a parent", has the rule.
    """
    from PyQt6.QtCore import QEvent, QObject  # noqa: PLC0415
    from PyQt6.QtWidgets import QWidget  # noqa: PLC0415

    shown: list[str] = []

    class _Spy(QObject):
        def eventFilter(self, obj, event):  # noqa: N802 - Qt's name
            if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.parent() is None:
                shown.append(type(obj).__name__)
            return False

    spy = _Spy()
    qapp.installEventFilter(spy)
    try:
        yield shown
    finally:
        qapp.removeEventFilter(spy)
