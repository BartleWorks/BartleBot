"""Delete a bot: the file, and what the user stored against its id (#268)."""

from __future__ import annotations

import pytest

from bartlebot.bot.schema import BotConfig, ForkedFrom, StateModel
from bartlebot.combat.bot_strategy_models import BotCombatStrategy
from bartlebot.combat.strategy_store import list_bot_strategies, save_bot_strategy
from bartlebot.content import bot_delete
from bartlebot.settings_manager import SettingsManager


def _bot(bot_id: str = "mine", **overrides) -> BotConfig:
    fields = {
        "id": bot_id,
        "name": "Jade Oni",
        "initial": "fight",
        "recovery_state": "fight",
        "states": [StateModel(id="fight", node_type="handle_combat")],
        "transitions": [],
    }
    fields.update(overrides)
    return BotConfig(**fields)


@pytest.fixture
def roots(tmp_path):
    return {"root": tmp_path / "shipped", "user_root": tmp_path / "user"}


def _strategy(roots, bot_id: str, name: str = "Solo") -> BotCombatStrategy:
    bcs = BotCombatStrategy(name=name, bot_id=bot_id)
    save_bot_strategy(bcs, **roots)
    return bcs


def test_the_plan_names_the_bot_and_its_own_strategies(tmp_path, roots):
    path = tmp_path / "mine.json"
    _bot().save(path)
    first, second = _strategy(roots, "mine", "Solo"), _strategy(roots, "mine", "Duo")
    _strategy(roots, "someone-else")

    plan = bot_delete.plan_deletion(path, **roots)

    assert (plan.bot_id, plan.name) == ("mine", "Jade Oni")
    assert set(plan.strategy_ids) == {first.id, second.id}


def test_an_official_strategy_is_not_the_users_to_delete(tmp_path, roots):
    path = tmp_path / "mine.json"
    _bot().save(path)
    official = BotCombatStrategy(name="Shipped", bot_id="mine")
    official.save(roots["root"] / "bot_strategies" / f"{official.id}.json")

    plan = bot_delete.plan_deletion(path, **roots)

    assert plan.strategy_ids == ()


def test_deleting_removes_the_file_its_strategies_and_everything_keyed_on_its_id(tmp_path, roots):
    path = tmp_path / "mine.json"
    _bot().save(path)
    kept_file = tmp_path / "other.json"
    _bot("other").save(kept_file)
    bcs, other = _strategy(roots, "mine"), _strategy(roots, "other")
    settings = SettingsManager.instance()
    settings.set_option_override("mine", "safety_delay", 5.0)
    settings.set_option_override("other", "safety_delay", 2.0)
    settings.accept_quicksell_sales("mine", ["sell_gear"])
    settings.set_active_strategy("mine", bcs.id)

    bot_delete.delete_bot(bot_delete.plan_deletion(path, **roots), settings, **roots)

    assert not path.exists() and kept_file.exists()
    assert [b.id for b in list_bot_strategies("mine", **roots)] == []
    assert [b.id for b in list_bot_strategies("other", **roots)] == [other.id]
    assert settings.get_active_strategy("mine") is None
    assert settings.accepted_quicksell_sales("mine") == []
    assert settings.get_option_override("mine", "safety_delay") != 5.0
    assert settings.get_option_override("other", "safety_delay") == 2.0


def test_deleting_a_fork_leaves_the_bot_it_came_from_alone(tmp_path, roots):
    official = tmp_path / "official.json"
    _bot("official").save(official)
    fork = tmp_path / "fork.json"
    _bot("fork", forked_from=ForkedFrom(id="official", sha256="0" * 64)).save(fork)

    bot_delete.delete_bot(bot_delete.plan_deletion(fork, **roots), SettingsManager.instance(), **roots)

    assert official.exists() and not fork.exists()


def test_a_file_that_cannot_be_removed_leaves_the_rest_alone(tmp_path, roots):
    path = tmp_path / "mine.json"
    _bot().save(path)
    bcs = _strategy(roots, "mine")
    settings = SettingsManager.instance()
    settings.set_option_override("mine", "safety_delay", 5.0)
    plan = bot_delete.plan_deletion(path, **roots)
    path.unlink()
    path.mkdir()  # unlink() on a directory raises OSError

    with pytest.raises(OSError):
        bot_delete.delete_bot(plan, settings, **roots)

    assert [b.id for b in list_bot_strategies("mine", **roots)] == [bcs.id]
    assert settings.get_option_override("mine", "safety_delay") == 5.0
