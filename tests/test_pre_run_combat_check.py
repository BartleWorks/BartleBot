"""The run request's combat check: a dialog only for faults, with Go back as its default (#207).

Each of the four faults opens the dialog; each notice opens none and is logged once at
INFO, between the check and the run-start line. The check itself is real, over a shipped root holding the repo's strategies and an
empty user root; only the dialog's answer is faked.
"""

from __future__ import annotations

import functools
import logging
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from bartlebot import app_controller
from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.combat import strategy_store
from bartlebot.combat.bot_strategy_models import BotCombatStrategy
from bartlebot.combat.combat_resolution import CombatResolver
from bartlebot.combat.combat_warnings import check_combat
from bartlebot.gui.dialogs import combat_faults_dialog
from bartlebot.paths import project_root
from bartlebot.shared.combat_defaults import PREFERENCE_KEYS, TeamSize
from bartlebot.shared.preferences import PreferenceStore
from bartlebot.shared.strategy_models import DuelCombatStrategy, PlayerAssignment, PlayerCombatStrategy

_VALID = "any<damage>[any<mod_damage>] @ enemy"


def _bot() -> BotConfig:
    return BotConfig(
        id="bot-1",
        name="Bot",
        initial="s0",
        recovery_state="s0",
        states=[
            StateModel(id="s0", node_type="handle_combat"),
            StateModel(id="s1", node_type="handle_combat", label="Boss", params={"use_default_strategy": False}),
        ],
        transitions=[],
    )


class _Controller:
    """Hosts AppController's combat check, with *pick* as the bot's Bot Combat Strategy."""

    def __init__(self, clients: int) -> None:
        from bartlebot.app_controller import AppController  # noqa: PLC0415

        for name in ("_confirm_combat", "_check_combat", "_log_combat_sources"):
            setattr(self, name, getattr(AppController, name).__get__(self))
        self._client_manager = SimpleNamespace(clients=[object()] * clients)
        self._loaded_config = _bot()
        self._window = None


@pytest.fixture
def world(tmp_path: Path, monkeypatch, qapp):
    """Roots, preferences and a pick, wired into the controller's check; and every dialog shown."""
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    shutil.copytree(project_root() / "strategies", shipped / "strategies")
    prefs = PreferenceStore(tmp_path / "vendor")
    state = SimpleNamespace(roots=(shipped, user), prefs=prefs, pick=None, shown=[], answer=False)

    monkeypatch.setattr(
        app_controller, "check_combat", functools.partial(check_combat, root=shipped, user_root=user, prefs=prefs)
    )
    monkeypatch.setattr(
        app_controller, "CombatResolver", functools.partial(CombatResolver, root=shipped, user_root=user, prefs=prefs)
    )
    settings = SimpleNamespace(get_active_strategy=lambda _bot_id: state.pick)
    monkeypatch.setattr(app_controller, "SettingsManager", SimpleNamespace(instance=lambda: settings))

    def confirm_run(_parent, faults):
        state.shown.append(list(faults))
        return state.answer

    monkeypatch.setattr(combat_faults_dialog, "confirm_run", confirm_run)
    return state


def _save_duel(world, name: str = "Duel", **kwargs) -> DuelCombatStrategy:
    kwargs.setdefault("player_assignments", [PlayerAssignment(players=[1, 2, 3, 4], dsl=_VALID)])
    duel = DuelCombatStrategy(name=name, **kwargs)
    strategy_store.save_duel_strategy(duel, world.roots[1])
    return duel


def _pick(world, **kwargs) -> BotCombatStrategy:
    bcs = BotCombatStrategy(name="Mine", bot_id="bot-1", **kwargs)
    strategy_store.save_bot_strategy(bcs, world.roots[1])
    world.pick = bcs.id
    return bcs


# -- the four faults ----------------------------------------------------------


def _broken_dsl(world):
    _pick(
        world, default_strategy_id=_save_duel(world, player_assignments=[PlayerAssignment(players=[1], dsl="@@@")]).id
    )
    return "does not parse"


def _missing_file(world):
    world.pick = "deadbeef-0000-0000-0000-000000000000"
    return "missing its file"


def _missing_link(world):
    _pick(world, default_strategy_id="abcdef00-0000-0000-0000-000000000000")
    return "Duel Strategy file is missing"


def _too_few_players(world):
    _pick(world, default_strategy_id=_save_duel(world, "Four Box", min_players=4).id)
    return "requires ≥4 players but only 2 client(s) are connected"


def _library_links_own(world):
    own = PlayerCombatStrategy(name="Frost Heal", dsl=_VALID)
    library = _save_duel(
        world, "Boss Burn", player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=own.id)]
    )
    _pick(world, default_strategy_id=library.id, player_strategies=[own])
    return "belongs to this bot only"


@pytest.mark.parametrize("make", [_broken_dsl, _missing_file, _missing_link, _too_few_players, _library_links_own])
def test_a_fault_opens_the_dialog_and_going_back_stops_the_run(world, make):
    expected = make(world)

    assert _Controller(2)._confirm_combat() is False
    assert len(world.shown) == 1
    assert any(expected in fault for fault in world.shown[0])


def test_run_anyway_proceeds(world):
    _too_few_players(world)
    world.answer = True

    assert _Controller(2)._confirm_combat() is True


# -- notices ------------------------------------------------------------------


def _no_pick(world):
    return "duel strategy 'Default Team Duel' (the shipped Team default; this bot has no Bot Combat Strategy)"


def _only_a_default(world):
    _pick(world, default_strategy_id=_save_duel(world, "General").id)
    return "No duel strategy is assigned to 'Boss', so it uses 'General'"


def _your_default_does_not_fit(world):
    world.prefs.set(PREFERENCE_KEYS[TeamSize.TEAM], _save_duel(world, "Four Box", min_players=4).id)
    return "your Team default 'Four Box' takes 4 players, and 2 are connected"


@pytest.mark.parametrize("make", [_no_pick, _only_a_default, _your_default_does_not_fit])
def test_a_notice_opens_no_dialog_and_is_logged_once(world, make, caplog):
    """Once across the check and the run-start line, which say the same of a fallback (epic #201 review, R4)."""
    expected = make(world)

    with caplog.at_level(logging.INFO, logger=app_controller.__name__):
        controller = _Controller(2)
        assert controller._confirm_combat() is True
        controller._log_combat_sources()

    assert world.shown == []
    logged = [r for r in caplog.records if r.levelno == logging.INFO and expected in r.getMessage()]
    assert len(logged) == 1
