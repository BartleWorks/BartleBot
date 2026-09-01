"""Tests for the relog node (#89).

The node drives a short UI sequence — spellbook, Preferences, Quit, character select, Play
— and then waits for the world to come back. A fake game stands in for all of it, so these
run with no Wizard101 process and no real timing.

The one behaviour worth stating up front: health monitoring is suspended for the players
being relogged and put back afterwards, *including* when the relog fails. A client at
character select fails ``zone_name()`` and shows loading screens, which is exactly what the
crash watchers look for.
"""

import logging

import pytest

from bartlebot.nodes.paths import (
    BACKPACK_CLOSE_BUTTON_PATH,
    REALM_CURRENT_NAME_PATH,
    SETTINGS_QUIT_BUTTON_PATH,
    SPELLBOOK_BUTTON_PATH,
    SPELLBOOK_PREFERENCES_TAB_PATH,
)
from bartlebot.nodes.utility import relog as relog_node
from tests.conftest import FakeClient, FakeMachine


class FakeGame:
    """One client's UI, as a state machine over the paths the node actually touches.

    Screens: ``world`` -> ``spellbook`` -> ``settings`` -> ``char_select`` -> ``loading``
    -> ``world``. ``opens_on_realms`` reproduces the one tab that hides the Quit button.
    """

    def __init__(self, *, opens_on_realms: bool = False, loads_in: bool = True, reaches_char_select: bool = True):
        self.screen = "world"
        self.opens_on_realms = opens_on_realms
        self.loads_in = loads_in
        self.reaches_char_select = reaches_char_select
        self.clicks: list[list[str]] = []
        self.plays = 0
        self.opens = 0
        self._loading_ticks = 0

    # --- what the node reads -------------------------------------------------
    def is_visible(self, path: list[str]) -> bool:
        if path == SPELLBOOK_PREFERENCES_TAB_PATH:
            return self.screen in ("spellbook", "settings")
        if path == SETTINGS_QUIT_BUTTON_PATH:
            return self.screen == "settings" and not self.opens_on_realms
        if path == REALM_CURRENT_NAME_PATH:
            return self.screen == "settings" and self.opens_on_realms
        return False

    def at_character_select(self) -> bool:
        return self.screen == "char_select"

    async def is_loading(self) -> bool:
        return self.screen == "loading"

    async def zone_name(self) -> str:
        if self.screen != "world":
            raise RuntimeError("not in a zone")
        return "TestZone"

    # --- what the node does --------------------------------------------------
    def click(self, path: list[str]) -> None:
        self.clicks.append(list(path))
        if path == SPELLBOOK_BUTTON_PATH and self.screen == "world":
            self.opens += 1
            self.screen = "spellbook"
        elif path == SPELLBOOK_PREFERENCES_TAB_PATH and self.screen == "spellbook":
            self.screen = "settings"
        elif path == BACKPACK_CLOSE_BUTTON_PATH:
            # Reopening is what clears the Realms tab, exactly as it does in the game.
            self.opens_on_realms = False
            self.screen = "world"
        elif path == SETTINGS_QUIT_BUTTON_PATH and self.screen == "settings":
            self.screen = "char_select" if self.reaches_char_select else "settings"

    def press_play(self) -> None:
        self.plays += 1
        if self.screen == "char_select":
            self.screen = "loading" if self.loads_in else "char_select"

    def tick(self) -> None:
        """Advance a loading screen, so loading in takes more than one poll."""
        if self.screen == "loading":
            self._loading_ticks += 1
            if self._loading_ticks >= 2:
                self.screen = "world"


@pytest.fixture
def games(monkeypatch):
    """Wire N fake games to N fake clients and patch every UI call the node makes."""
    registry: dict[int, FakeGame] = {}

    async def _is_visible(client, path):
        return registry[client.window_handle].is_visible(list(path))

    async def _click(client, path):
        registry[client.window_handle].click(list(path))
        return True

    async def _at_char_select(client):
        game = registry[client.window_handle]
        game.tick()
        return game.at_character_select()

    async def _press_play(hwnd):
        registry[hwnd].press_play()

    import bartlebot.nodes.node_helpers as nh
    from bartlebot.launcher import auto_select

    monkeypatch.setattr(nh, "is_visible_by_path", _is_visible)
    monkeypatch.setattr(nh, "click_by_path", _click)
    monkeypatch.setattr(auto_select, "at_character_select", _at_char_select)
    monkeypatch.setattr(auto_select, "press_character_select_play", _press_play)
    # Cadences, never ceilings — the fakes count polls, not seconds.
    monkeypatch.setattr(relog_node, "POLL_INTERVAL", 0.001)
    monkeypatch.setattr(relog_node, "TRIGGER_TIMEOUT", 0.2)
    monkeypatch.setattr(relog_node, "RELOG_CHAR_SELECT_TIMEOUT", 0.2)
    monkeypatch.setattr(relog_node, "RELOG_IN_GAME_TIMEOUT", 0.5)

    def _make(*game_kwargs) -> tuple[list, list[FakeGame]]:
        clients, made = [], []
        for kwargs in game_kwargs or ({},):
            client = FakeClient()
            game = FakeGame(**kwargs)
            registry[client.window_handle] = game
            client.is_loading = game.is_loading
            client.zone_name = game.zone_name
            clients.append(client)
            made.append(game)
        return clients, made

    return _make


@pytest.fixture
def machine() -> FakeMachine:
    return FakeMachine()


@pytest.fixture
def no_grouping(monkeypatch):
    """Grouping off, so the node's own sequence is what is under test."""
    settings = type("S", (), {"auto_group_players": False})()
    monkeypatch.setattr(relog_node.SettingsManager, "instance", staticmethod(lambda: settings))


pytestmark = pytest.mark.usefixtures("no_grouping")


class TestSuccessPath:
    async def test_one_player_walks_the_whole_sequence(self, machine, games):
        clients, (game,) = games({})
        assert await relog_node.run(machine, clients, {}) == "SUCCESS"
        assert game.clicks == [
            SPELLBOOK_BUTTON_PATH,
            SPELLBOOK_PREFERENCES_TAB_PATH,
            SETTINGS_QUIT_BUTTON_PATH,
        ]
        assert game.plays >= 1
        assert game.screen == "world"

    async def test_every_player_is_relogged_by_default(self, machine, games):
        clients, made = games({}, {}, {})
        assert await relog_node.run(machine, clients, {}) == "SUCCESS"
        assert all(g.screen == "world" for g in made)

    async def test_the_player_list_selects_a_subset(self, machine, games):
        clients, (first, second) = games({}, {})
        assert await relog_node.run(machine, clients, {"relog_clients": [2]}) == "SUCCESS"
        assert first.clicks == []
        assert second.screen == "world"

    async def test_an_out_of_range_selection_relogs_nobody(self, machine, games):
        clients, (game,) = games({})
        assert await relog_node.run(machine, clients, {"relog_clients": [7]}) == "SUCCESS"
        assert game.clicks == []


class TestRealmsTabGuard:
    async def test_the_spellbook_is_reopened_when_it_lands_on_the_realms_tab(self, machine, games):
        clients, (game,) = games({"opens_on_realms": True})
        assert await relog_node.run(machine, clients, {}) == "SUCCESS"
        assert game.opens == 2  # closed and reopened, rather than clicking around the tabs
        assert BACKPACK_CLOSE_BUTTON_PATH in game.clicks
        assert game.clicks[-1] == SETTINGS_QUIT_BUTTON_PATH


class TestFailurePaths:
    async def test_character_select_never_appears(self, machine, games, caplog):
        clients, (game,) = games({"reaches_char_select": False})
        with caplog.at_level(logging.WARNING):
            assert await relog_node.run(machine, clients, {}) == "FAILURE"
        assert any("character select never appeared" in msg for msg, _ in machine.logs)

    async def test_a_player_that_never_loads_back_in(self, machine, games):
        clients, (game,) = games({"loads_in": False})
        assert await relog_node.run(machine, clients, {}) == "FAILURE"
        assert any("did not load back in" in msg for msg, _ in machine.logs)

    async def test_one_failure_fails_the_node_for_the_group(self, machine, games):
        clients, (good, bad) = games({}, {"loads_in": False})
        assert await relog_node.run(machine, clients, {}) == "FAILURE"
        assert good.screen == "world"  # the healthy one still relogged

    async def test_a_spellbook_that_never_opens(self, machine, games, monkeypatch):
        clients, (game,) = games({})
        import bartlebot.nodes.node_helpers as nh

        async def _never_visible(_client, _path):
            return False

        monkeypatch.setattr(nh, "is_visible_by_path", _never_visible)
        assert await relog_node.run(machine, clients, {}) == "FAILURE"
        assert any("spellbook did not open" in msg for msg, _ in machine.logs)


class TestHealthChecks:
    async def test_suspended_for_the_targets_and_restored_afterwards(self, machine, games):
        clients, _ = games({}, {})
        await relog_node.run(machine, clients, {})
        hwnds = {c.window_handle for c in clients}
        assert machine.health_checks_resumed == hwnds
        assert machine.health_checks_suspended == set()

    async def test_restored_even_when_the_relog_fails(self, machine, games):
        clients, _ = games({"loads_in": False})
        assert await relog_node.run(machine, clients, {}) == "FAILURE"
        assert machine.health_checks_suspended == set()
        assert machine.health_checks_resumed == {clients[0].window_handle}

    async def test_only_the_selected_players_are_suspended(self, machine, games):
        clients, _ = games({}, {})
        await relog_node.run(machine, clients, {"relog_clients": [1]})
        assert machine.health_checks_resumed == {clients[0].window_handle}


class TestRegrouping:
    async def test_the_group_is_re_formed_when_the_setting_is_on(self, machine, games, monkeypatch):
        clients, _ = games({}, {})
        settings = type("S", (), {"auto_group_players": True})()
        monkeypatch.setattr(relog_node.SettingsManager, "instance", staticmethod(lambda: settings))

        called = {}

        async def _resolve(cs):
            return ["A", "B"]

        async def _form(cs, names, **kwargs):
            called["clients"] = cs
            return type("R", (), {"success": True, "summary": lambda self: "2 grouped"})()

        monkeypatch.setattr(relog_node.wizard_names, "resolve_names", _resolve)
        monkeypatch.setattr(relog_node.group_manager, "form_group", _form)

        assert await relog_node.run(machine, clients, {}) == "SUCCESS"
        assert called["clients"] == clients

    async def test_a_failed_regroup_does_not_fail_the_node(self, machine, games, monkeypatch):
        clients, _ = games({}, {})
        settings = type("S", (), {"auto_group_players": True})()
        monkeypatch.setattr(relog_node.SettingsManager, "instance", staticmethod(lambda: settings))

        async def _boom(*_args, **_kwargs):
            raise RuntimeError("friends list would not open")

        monkeypatch.setattr(relog_node.wizard_names, "resolve_names", _boom)

        assert await relog_node.run(machine, clients, {}) == "SUCCESS"
        assert any("continues ungrouped" in msg for msg, _ in machine.logs)

    async def test_a_single_player_is_never_grouped(self, machine, games, monkeypatch):
        clients, _ = games({})
        settings = type("S", (), {"auto_group_players": True})()
        monkeypatch.setattr(relog_node.SettingsManager, "instance", staticmethod(lambda: settings))

        async def _boom(*_args, **_kwargs):
            raise AssertionError("a group of one confers nothing")

        monkeypatch.setattr(relog_node.wizard_names, "resolve_names", _boom)
        assert await relog_node.run(machine, clients, {}) == "SUCCESS"
