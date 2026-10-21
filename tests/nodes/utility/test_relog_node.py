"""Tests for the relog node.

The node drives a short UI sequence — spellbook, Preferences, Quit, character select, Play
— and then waits for the world to come back. A fake game stands in for all of it, so these
run with no Wizard101 process and no real timing.

The one behavior worth stating up front: health monitoring is suspended for the players
being relogged and put back afterwards, *including* when the relog fails. A client at
character select fails ``zone_name()`` and shows loading screens, which is exactly what the
crash watchers look for.

The other: past Quit there is no going back. A player that leaves the world and cannot get
back in is handed to crash recovery (``machine.abandon_client``) and the node raises, rather
than returning a FAILURE the run would count and possibly stop on.
"""

import asyncio
import logging
from unittest.mock import patch

import pytest
from wizwalker.errors import ClientClosedError

from bartlebot.nodes.paths import (
    BACKPACK_CLOSE_BUTTON_PATH,
    MESSAGE_BOX_CAPTION_PATH,
    MESSAGE_BOX_CENTER,
    MESSAGE_BOX_RIGHT,
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

    ``quit_dialog`` puts a modal between Quit and character select, as the game does when
    logging out of a dungeon: its caption, and which buttons it shows. The center button
    goes on to character select; the right one backs out to the settings page.

    The ways out of the world can go wrong: ``logout_hangs`` sticks on the loading screen
    out to character select, ``hangs_loading`` on the one back in, and ``crashes_loading``
    goes unreadable instead. ``ignores_play_for`` is a character select that takes that
    many seconds to accept Play.
    """

    def __init__(
        self,
        *,
        opens_on_realms: bool = False,
        loads_in: bool = True,
        reaches_char_select: bool = True,
        quit_dialog: tuple[str, tuple] | None = None,
        logout_hangs: bool = False,
        hangs_loading: bool = False,
        crashes_loading: bool = False,
        ignores_play_for: float = 0.0,
        load_seconds: float | None = None,
        escape_opens: bool = True,
    ):
        self.escape_opens = escape_opens
        self.escapes = 0
        self.load_seconds = load_seconds
        self._loading_from = 0.0
        self.screen = "world"
        self.logout_hangs = logout_hangs
        self.hangs_loading = hangs_loading
        self.crashes_loading = crashes_loading
        self.ignores_play_for = ignores_play_for
        self._first_play_at: float | None = None
        self.opens_on_realms = opens_on_realms
        self.quit_dialog = quit_dialog
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
        if path in (MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT):
            return self.screen == "dialog" and path in self.quit_dialog[1]
        return False

    def text(self, path: list[str]) -> str:
        if path == MESSAGE_BOX_CAPTION_PATH and self.screen == "dialog":
            return self.quit_dialog[0]
        return ""

    def at_character_select(self) -> bool:
        return self.screen == "char_select"

    async def is_loading(self) -> bool:
        if self.screen == "dead":
            raise RuntimeError("unreadable")
        return self.screen in ("loading", "loading_out")

    async def zone_name(self) -> str:
        # The world is readable with a menu or a dialog open over it, as in the game.
        if self.screen not in ("world", "spellbook", "settings", "dialog"):
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
            if self.quit_dialog:
                self.screen = "dialog"
            elif self.logout_hangs:
                self.screen = "loading_out"
            else:
                self.screen = "char_select" if self.reaches_char_select else "settings"
        elif path == MESSAGE_BOX_CENTER and self.screen == "dialog":
            self.screen = "char_select"
        elif path == MESSAGE_BOX_RIGHT and self.screen == "dialog":
            self.screen = "settings"

    def press_escape(self) -> None:
        """Esc opens the settings page from the world, in battle or not, and closes it again."""
        self.escapes += 1
        if self.screen == "world" and self.escape_opens:
            self.screen = "settings"
        elif self.screen == "settings":
            self.screen = "world"

    def press_play(self) -> None:
        self.plays += 1
        if self.screen != "char_select":
            return
        now = asyncio.get_running_loop().time()
        if self._first_play_at is None:
            self._first_play_at = now
        if now - self._first_play_at < self.ignores_play_for or not self.loads_in:
            return
        self.screen = "dead" if self.crashes_loading else "loading"
        self._loading_from = now

    def tick(self) -> None:
        """Advance a loading screen, so loading in takes more than one poll.

        Two polls by default; ``load_seconds`` makes it a slow load measured in time instead.
        """
        if self.screen == "loading" and not self.hangs_loading:
            self._loading_ticks += 1
            if self.load_seconds is not None:
                done = asyncio.get_running_loop().time() - self._loading_from >= self.load_seconds
            else:
                done = self._loading_ticks >= 2
            if done:
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

    async def _press_escape(hwnd):
        registry[hwnd].press_escape()

    async def _text(client, path):
        return registry[client.window_handle].text(list(path))

    import bartlebot.nodes.node_helpers as nh
    from bartlebot.launcher import auto_select

    monkeypatch.setattr(nh, "is_visible_by_path", _is_visible)
    monkeypatch.setattr(nh, "click_by_path", _click)
    monkeypatch.setattr(nh, "get_text_by_path", _text)
    monkeypatch.setattr(auto_select, "at_character_select", _at_char_select)
    monkeypatch.setattr(auto_select, "press_character_select_play", _press_play)
    monkeypatch.setattr(auto_select, "press_escape", _press_escape)
    # Cadences, never ceilings — the fakes count polls, not seconds.
    monkeypatch.setattr(relog_node, "POLL_INTERVAL", 0.001)
    monkeypatch.setattr(relog_node, "TRIGGER_TIMEOUT", 0.2)
    monkeypatch.setattr(relog_node, "RELOG_CHAR_SELECT_TIMEOUT", 0.2)
    monkeypatch.setattr(relog_node, "RELOG_IN_GAME_TIMEOUT", 0.5)
    # A third of the overall deadline, as the real minute is of three, so a stuck loading
    # screen is caught by its own clock with room to spare.
    monkeypatch.setattr(relog_node, "LOADING_SCREEN_TIMEOUT", 0.15)

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


# The game's own en-US text for the two logout warnings, as the snapshot holds it.
DUNGEON_LOGOUT_CAPTION = (
    "If you log out in a Dungeon you will lose all your progress in the Dungeon and will have "
    "to start over at the beginning.<br>Are you sure you want to log out now?"
)
DUNGEON_RECALL_LOGOUT_CAPTION = "If you log out, Dungeon Recall will close.<br>Are you sure you want to log out now?"


class TestLogoutWarnings:
    """#108: Quit from inside a dungeon asks first, and the relog stalled on the question."""

    @pytest.mark.parametrize("caption", [DUNGEON_LOGOUT_CAPTION, DUNGEON_RECALL_LOGOUT_CAPTION])
    async def test_the_warning_is_confirmed_and_the_relog_carries_on(self, machine, games, caption):
        clients, (game,) = games({"quit_dialog": (caption, (MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT))})
        assert await relog_node.run(machine, clients, {}) == "SUCCESS"
        assert game.clicks[-1] == MESSAGE_BOX_CENTER
        assert game.screen == "world"

    async def test_a_dismiss_only_dialog_fails_fast_as_a_refusal(self, machine, games):
        """A dialog offering no way on is the game saying no; waiting out the timeout says nothing more."""
        clients, (game,) = games({"quit_dialog": ("Wording nobody has cataloged", (MESSAGE_BOX_RIGHT,))})
        assert await relog_node.run(machine, clients, {}) == "FAILURE"
        assert MESSAGE_BOX_CENTER not in game.clicks
        assert any("refused the logout" in msg for msg, _ in machine.logs)
        assert not any("never appeared" in msg for msg, _ in machine.logs)


class TestFailurePaths:
    """Failures with the player still in the world: an ordinary FAILURE, no recovery."""

    async def test_quit_that_does_not_take_effect(self, machine, games, caplog):
        clients, (game,) = games({"reaches_char_select": False})
        with caplog.at_level(logging.WARNING):
            assert await relog_node.run(machine, clients, {}) == "FAILURE"
        assert any("character select never appeared" in msg and "still in the world" in msg for msg, _ in machine.logs)
        assert machine.abandoned == []

    async def test_one_failure_fails_the_node_for_the_group(self, machine, games):
        clients, (good, bad) = games({}, {"reaches_char_select": False})
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


class TestInBattle:
    """Crash recovery logs a team out of a fight with Esc: the spellbook will not open in battle."""

    async def test_esc_opens_the_settings_page_and_the_player_comes_back(self, machine, games):
        (client,), (game,) = games({})
        assert await relog_node.log_out_and_in(machine, client, in_battle=True) == relog_node.BACK
        assert game.escapes == 1
        assert game.clicks == [SETTINGS_QUIT_BUTTON_PATH]  # never the spellbook
        assert game.screen == "world"

    async def test_the_dungeon_warning_is_confirmed_on_this_route_too(self, machine, games):
        (client,), (game,) = games({"quit_dialog": (DUNGEON_LOGOUT_CAPTION, (MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT))})
        assert await relog_node.log_out_and_in(machine, client, in_battle=True) == relog_node.BACK
        assert MESSAGE_BOX_CENTER in game.clicks

    async def test_esc_that_opens_nothing_fails_with_the_player_still_in_the_world(self, machine, games):
        (client,), (game,) = games({"escape_opens": False})
        assert await relog_node.log_out_and_in(machine, client, in_battle=True) == relog_node.FAILED
        assert game.escapes == 2  # pressed again only once the first showed nothing
        assert game.clicks == []
        assert machine.abandoned == []

    async def test_past_quit_a_stuck_load_is_still_handed_to_recovery(self, machine, games):
        machine.recovers = True
        (client,), (_game,) = games({"hangs_loading": True})
        assert await relog_node.log_out_and_in(machine, client, in_battle=True) == relog_node.ABANDONED
        assert machine.abandoned


class TestPastQuit:
    """Past Quit, a player that cannot get back in is a crash: handed to recovery, and the node raises.

    The bug this came from: a player left character select and sat on its loading screen for
    good. The node waited out its whole deadline with health checks off, returned FAILURE,
    and the retry turned them off again, so nothing ever relaunched the client.
    """

    @pytest.mark.parametrize(
        ("game", "said"),
        [
            ({"hangs_loading": True}, "has been loading"),
            ({"crashes_loading": True}, "has been loading"),  # unreadable is the same hang
            ({"logout_hangs": True}, "character select never appeared"),
            ({"loads_in": False}, "did not load back in"),  # character select keeps coming back
        ],
        ids=["stuck-loading", "unreadable", "stuck-logging-out", "play-never-takes"],
    )
    async def test_the_player_is_handed_to_recovery(self, machine, games, game, said):
        machine.recovers = True
        clients, _ = games(game)
        with pytest.raises(ClientClosedError):
            await relog_node.run(machine, clients, {})
        ((client, reason),) = machine.abandoned
        assert client is clients[0]
        assert said in reason

    async def test_a_stuck_loading_screen_is_caught_well_before_the_overall_deadline(self, machine, games):
        machine.recovers = True
        clients, _ = games({"hangs_loading": True})
        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises(ClientClosedError):
            await relog_node.run(machine, clients, {})
        assert loop.time() - started < relog_node.RELOG_IN_GAME_TIMEOUT

    async def test_a_slow_load_under_the_limit_succeeds(self, machine, games):
        machine.recovers = True
        clients, (game,) = games({"load_seconds": relog_node.LOADING_SCREEN_TIMEOUT / 3})
        assert await relog_node.run(machine, clients, {}) == "SUCCESS"
        assert machine.abandoned == []

    async def test_time_at_character_select_is_not_loading(self, machine, games, monkeypatch):
        """A character select slow to take Play restarts nothing: only time off it counts."""
        monkeypatch.setattr(relog_node, "RELOG_IN_GAME_TIMEOUT", 2.0)
        machine.recovers = True
        clients, (game,) = games({"ignores_play_for": relog_node.LOADING_SCREEN_TIMEOUT * 2})
        assert await relog_node.run(machine, clients, {}) == "SUCCESS"
        assert game.plays > 1
        assert machine.abandoned == []

    async def test_health_checks_stay_off_for_the_abandoned_player_only(self, machine, games):
        machine.recovers = True
        clients, (good, _bad) = games({}, {"hangs_loading": True})
        with pytest.raises(ClientClosedError):
            await relog_node.run(machine, clients, {})
        assert good.screen == "world"
        assert machine.health_checks_resumed == {clients[0].window_handle}
        assert machine.health_checks_suspended == {clients[1].window_handle}  # recovery's resume() clears it

    async def test_with_no_recovery_behind_the_run_it_is_a_failure(self, machine, games):
        """The node tester's case: nothing to hand to, so say why and fail as before."""
        clients, _ = games({"hangs_loading": True})
        assert await relog_node.run(machine, clients, {}) == "FAILURE"
        assert any("relog: player has been loading" in msg for msg, _ in machine.logs)
        assert machine.health_checks_suspended == set()

    async def test_a_player_recovery_cannot_relaunch_leaves_the_stop_to_explain_itself(self, games):
        """abandon_client stopped the run with the reason; the node adds no second account of it."""

        class StoppingMachine(FakeMachine):
            def abandon_client(self, client, reason):
                super().abandon_client(client, reason)
                self.is_stopped = True
                return False

        machine = StoppingMachine()
        clients, _ = games({"hangs_loading": True})
        assert await relog_node.run(machine, clients, {}) == "FAILURE"
        assert len(machine.abandoned) == 1
        assert not any("has been loading" in msg for msg, _ in machine.logs)


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

    async def test_the_disconnect_dialog_watcher_stays_on(self, machine, games):
        """A disconnect modal is no part of a relog, and only a relaunch gets past it."""
        clients, _ = games({})
        await relog_node.run(machine, clients, {})
        assert machine.watching_disconnects is True


class TestHandedToRecoveryMidRelog:
    """The disconnect dialog watcher can close a client while the node is still working on it."""

    async def test_the_wait_for_character_select_ends_and_the_player_is_left_to_recovery(self, machine, games):
        (client,), _ = games({"reaches_char_select": False})  # Quit does nothing: the session is gone

        async def _watcher_recovers():
            await asyncio.sleep(0.02)
            machine.lost = {client.window_handle}

        asyncio.create_task(_watcher_recovers())
        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises(ClientClosedError):
            await relog_node.run(machine, [client], {})

        assert loop.time() - started < relog_node.RELOG_CHAR_SELECT_TIMEOUT
        assert machine.abandoned == []  # the watcher did it, so the node hands nothing over itself
        assert client.window_handle not in machine.health_checks_resumed

    async def test_a_read_that_raises_on_the_closed_client_is_an_abandon_not_an_error(self, machine, games):
        (client,), _ = games({})
        machine.lost = {client.window_handle}

        async def _closed(*_args):
            raise RuntimeError("process gone")

        import bartlebot.nodes.node_helpers as nh

        with patch.object(nh, "click_by_path", _closed):
            assert await relog_node.log_out_and_in(machine, client) == relog_node.ABANDONED


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


class TestFights:
    """Nobody is logged out mid-fight, and the group is not re-formed during one (#203).

    The node's route to Quit, the spellbook, will not open in battle, and the group survey opens
    the social window over the cards. The ambient combat watcher fights meanwhile; here a fight is just ``in_battle``.
    """

    async def test_a_fight_is_waited_out_before_anyone_logs_out(self, machine, games):
        clients, (game, other) = games({}, {})
        reads = []

        async def _in_battle():
            reads.append(list(game.clicks))
            return len(reads) <= 3

        clients[0].in_battle = _in_battle

        assert await relog_node.run(machine, clients, {"relog_clients": [2]}) == "SUCCESS"
        assert reads[:3] == [[], [], []]
        assert other.clicks[0] == SPELLBOOK_BUTTON_PATH
        assert any("before logging anyone out" in msg for msg, _ in machine.logs)

    async def test_a_fight_that_never_ends_fails_with_nobody_logged_out(self, machine, games, monkeypatch):
        monkeypatch.setattr(relog_node, "RELOG_FIGHT_TIMEOUT", 0.05)
        clients, (game,) = games({})
        clients[0].in_battle.return_value = True

        assert await relog_node.run(machine, clients, {}) == "FAILURE"
        assert game.clicks == []
        assert machine.health_checks_suspended == set()

    async def test_a_fight_found_on_logging_back_in_is_over_before_the_group_is_re_formed(
        self, machine, games, monkeypatch
    ):
        clients, (game, _other) = games({}, {})
        settings = type("S", (), {"auto_group_players": True})()
        monkeypatch.setattr(relog_node.SettingsManager, "instance", staticmethod(lambda: settings))
        fight = {"reads": 0}

        async def _in_battle():
            # Pulled in beside a mob on arrival: in battle for a few reads once back in the world.
            if game.plays and game.screen == "world":
                fight["reads"] += 1
                return fight["reads"] <= 3
            return False

        clients[0].in_battle = _in_battle
        seen = {}

        async def _resolve(cs):
            return ["A", "B"]

        async def _form(cs, names, **kwargs):
            seen["reads"] = fight["reads"]
            return type("R", (), {"success": True, "summary": lambda self: "2 grouped"})()

        monkeypatch.setattr(relog_node.wizard_names, "resolve_names", _resolve)
        monkeypatch.setattr(relog_node.group_manager, "form_group", _form)

        assert await relog_node.run(machine, clients, {}) == "SUCCESS"
        assert seen["reads"] > 3
        assert any("before re-forming the group" in msg for msg, _ in machine.logs)

    async def test_a_fight_after_logging_in_that_never_ends_leaves_the_group_unformed(
        self, machine, games, monkeypatch
    ):
        monkeypatch.setattr(relog_node, "RELOG_FIGHT_TIMEOUT", 0.05)
        clients, (game, _other) = games({}, {})
        settings = type("S", (), {"auto_group_players": True})()
        monkeypatch.setattr(relog_node.SettingsManager, "instance", staticmethod(lambda: settings))

        async def _in_battle():
            return bool(game.plays) and game.screen == "world"

        async def _boom(*_args, **_kwargs):
            raise AssertionError("the group survey must not run mid-fight")

        clients[0].in_battle = _in_battle
        monkeypatch.setattr(relog_node.wizard_names, "resolve_names", _boom)

        assert await relog_node.run(machine, clients, {}) == "SUCCESS"
        assert any("continues ungrouped" in msg for msg, _ in machine.logs)


class TestFreshLogins:
    """A player just logged in, by recovery or a relog, has nothing left for a relog to do."""

    async def test_a_freshly_logged_in_player_is_skipped_and_the_rest_relogged(self, machine, games):
        clients, (fresh, stale) = games({}, {})
        machine.note_logged_in([clients[0].window_handle])

        assert await relog_node.run(machine, clients, {}) == "SUCCESS"

        assert fresh.clicks == []
        assert stale.clicks[-1] == SETTINGS_QUIT_BUTTON_PATH
        assert any("not relogging them" in msg for msg, _ in machine.logs)

    async def test_a_team_recovery_just_logged_in_is_not_logged_out_again(self, machine, games):
        clients, made = games({}, {}, {}, {})
        machine.note_logged_in([c.window_handle for c in clients])

        assert await relog_node.run(machine, clients, {}) == "SUCCESS"

        assert all(game.clicks == [] and game.plays == 0 for game in made)
        assert machine.health_checks_suspended == set()

    async def test_a_player_back_from_a_relog_is_fresh(self, machine, games):
        (client,), _ = games({})
        assert await relog_node.log_out_and_in(machine, client) == relog_node.BACK
        assert machine.is_freshly_logged_in(client)

    async def test_a_player_still_in_the_world_after_a_failed_relog_is_not_fresh(self, machine, games):
        (client,), _ = games({"reaches_char_select": False})
        assert await relog_node.log_out_and_in(machine, client) == relog_node.FAILED
        assert not machine.is_freshly_logged_in(client)

    async def test_nobody_relogged_leaves_the_group_recovery_formed_alone(self, machine, games, monkeypatch):
        """Recovery regrouped the team it logged in; with nobody logged out, regrouping again only costs time."""
        clients, _ = games({}, {})
        settings = type("S", (), {"auto_group_players": True})()
        monkeypatch.setattr(relog_node.SettingsManager, "instance", staticmethod(lambda: settings))
        formed: list = []

        async def _form(cs, names, **kwargs):
            formed.append(cs)

        monkeypatch.setattr(relog_node.group_manager, "form_group", _form)
        machine.note_logged_in([c.window_handle for c in clients])

        assert await relog_node.run(machine, clients, {}) == "SUCCESS"
        assert formed == []
