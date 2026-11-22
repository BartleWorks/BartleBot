"""Favorite strategies in BartleBot's settings (#125), and the Combat Strategies window keeping them there.

What a star does in the window is core's (its ``tests/qt/combat/test_strategy_filters.py``);
these are the settings it is handed, and that the click reaches them.
"""

from __future__ import annotations

import pytest
from bartleby.shared.strategy_library import StrategyKind
from bartleby.shared.strategy_models import PlayerCombatStrategy

from bartlebot.combat.bot_strategy_owner import load_working_set
from bartlebot.combat.strategy_store import save_player_strategy
from bartlebot.gui.combat_strategies.dialog import CombatStrategyDialog
from bartlebot.settings_manager import SettingsManager


@pytest.fixture
def roots(tmp_path):
    return tmp_path / "shipped", tmp_path / "user"


def test_starring_in_the_window_is_in_the_settings_at_once(qapp, roots):
    shipped, user = roots
    pcs = PlayerCombatStrategy(name="Support", dsl="x")
    save_player_strategy(pcs, root=shipped, user_root=shipped)
    dlg = CombatStrategyDialog(load_working_set(root=shipped, user_root=user), kind=StrategyKind.PLAYER)

    dlg.page(StrategyKind.PLAYER).card_for(pcs.id).star.click()

    # Saved on the click: a fresh manager reads it back from disk, with no Save pressed.
    SettingsManager._instance = None
    assert SettingsManager.instance().is_favorite_strategy(StrategyKind.PLAYER.value, pcs.id)


def test_a_favorite_in_the_settings_is_listed_first(qapp, roots):
    shipped, user = roots
    pcs = PlayerCombatStrategy(name="Zeta", dsl="x")
    save_player_strategy(pcs, root=shipped, user_root=shipped)
    save_player_strategy(PlayerCombatStrategy(name="Alpha", dsl="x"), root=shipped, user_root=shipped)
    SettingsManager.instance().toggle_favorite_strategy(StrategyKind.PLAYER.value, pcs.id)

    dlg = CombatStrategyDialog(load_working_set(root=shipped, user_root=user), kind=StrategyKind.PLAYER)

    assert dlg.page(StrategyKind.PLAYER).visible_ids()[0] == pcs.id


def test_favorites_are_kept_per_kind(qapp):
    settings = SettingsManager.instance()
    settings.toggle_favorite_strategy(StrategyKind.PLAYER.value, "abc")
    assert settings.favorite_strategies(StrategyKind.PLAYER.value) == ["abc"]
    assert settings.favorite_strategies(StrategyKind.DUEL.value) == []


def test_a_malformed_favorites_value_reads_as_none(qapp):
    settings = SettingsManager.instance()
    settings.set("favorite_strategies", StrategyKind.DUEL.value, value="not a list")
    assert settings.favorite_strategies(StrategyKind.DUEL.value) == []
