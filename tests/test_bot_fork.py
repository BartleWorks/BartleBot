"""Make my own copy: the fork of an Official bot, and what it takes with it (#149)."""

from __future__ import annotations

import json

from bartlebot import bot_fork, option_values
from bartlebot.bot.schema import BotConfig, BotOptionModel, ForkedFrom, StateModel, TemplateRef
from bartlebot.bot.strategy_models import BotCombatStrategy, FightRef, StateAssignment
from bartlebot.settings_manager import SettingsManager
from bartlebot.shared.strategy_models import DuelCombatStrategy, PlayerCombatStrategy
from bartlebot.strategy_store import list_bot_strategies, load_bot_strategy, save_bot_strategy


def _official(**overrides) -> BotConfig:
    fields = {
        "id": "official",
        "name": "Jade Oni",
        "initial": "fight",
        "recovery_state": "fight",
        "states": [StateModel(id="fight", node_type="handle_combat")],
        "transitions": [],
    }
    fields.update(overrides)
    return BotConfig(**fields)


# ---------------------------------------------------------------------------
# The content hash
# ---------------------------------------------------------------------------


def test_the_hash_is_of_content_so_a_reformatted_file_hashes_the_same(tmp_path):
    bot = _official()
    bot.save(tmp_path / "a.json")
    data = json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))
    (tmp_path / "b.json").write_text(json.dumps(data, indent=8, sort_keys=True), encoding="utf-8")

    assert bot_fork.content_sha256(BotConfig.load(tmp_path / "a.json")) == bot_fork.content_sha256(
        BotConfig.load(tmp_path / "b.json")
    )


def test_the_hash_changes_with_the_content():
    assert bot_fork.content_sha256(_official()) != bot_fork.content_sha256(_official(name="Jade Oni II"))


# ---------------------------------------------------------------------------
# The copy
# ---------------------------------------------------------------------------


def test_a_fork_gets_a_new_id_the_name_given_and_records_where_it_came_from():
    official = _official(template=TemplateRef(id="t_boss", version=3))

    fork = bot_fork.make_fork(official, "  My Jade Oni ")

    assert fork.id != official.id
    assert fork.name == "My Jade Oni"
    assert fork.forked_from == ForkedFrom(id="official", sha256=bot_fork.content_sha256(official))
    assert fork.template == official.template, "a copy of a bot has the same base"
    assert [s.id for s in fork.states] == ["fight"], "state ids are kept, so combat assignments still match"
    assert official.forked_from is None and official.id == "official", "the Official bot is not touched"


def test_a_blank_name_keeps_the_official_one():
    assert bot_fork.make_fork(_official(), "   ").name == "Jade Oni"


def test_forked_from_round_trips_and_is_absent_from_every_other_bot(tmp_path):
    fork = bot_fork.make_fork(_official(), "Mine")
    fork.save(tmp_path / "fork.json")
    _official().save(tmp_path / "official.json")

    assert BotConfig.load(tmp_path / "fork.json").forked_from == fork.forked_from
    assert "forked_from" not in json.loads((tmp_path / "official.json").read_text(encoding="utf-8"))


def test_the_copy_goes_in_the_users_folder_under_the_official_files_name(tmp_path):
    user = tmp_path / "user"

    assert bot_fork.fork_path("install/bots/04_mooshu/jade-oni.json", user, "04_mooshu") == (
        user / "04_mooshu" / "jade-oni.json"
    )
    assert bot_fork.fork_path("install/bots/loose.json", user, None) == user / "loose.json"


def test_a_second_copy_does_not_overwrite_the_first(tmp_path):
    user = tmp_path / "user"
    (user / "04_mooshu").mkdir(parents=True)
    (user / "04_mooshu" / "jade-oni.json").write_text("{}", encoding="utf-8")

    assert bot_fork.fork_path("jade-oni.json", user, "04_mooshu").name == "jade-oni-2.json"


# ---------------------------------------------------------------------------
# What the copy takes with it
# ---------------------------------------------------------------------------


def _profile(name: str = "Solo") -> BotCombatStrategy:
    player = PlayerCombatStrategy(name="Hitter")
    duel = DuelCombatStrategy(name="Boss fight")
    return BotCombatStrategy(
        name=name,
        bot_id="official",
        duel_strategies=[duel],
        player_strategies=[player],
        state_assignments=[StateAssignment(fights=[FightRef(state="fight")], duel_combat_strategy_id=duel.id)],
        default_strategy_id=duel.id,
    )


def test_the_copy_takes_every_option_value_and_quicksell_consent():
    settings = SettingsManager.instance()
    settings.set_option_override("official", "safety_delay", 5.0)
    settings.set_option_override("official", "keep_list", ["Amber"])
    settings.accept_quicksell_sales("official", ["sell_gear"])

    result = bot_fork.carry_over("official", "fork", settings)

    assert result.options == 2
    assert settings.get_option_override("fork", "safety_delay") == 5.0
    assert settings.get_option_override("fork", "keep_list") == ["Amber"]
    assert settings.accepted_quicksell_sales("fork") == ["sell_gear"]
    assert settings.get_option_override("official", "safety_delay") == 5.0, "the Official bot keeps its own"


def test_the_copy_takes_every_bot_combat_strategy_and_the_active_pick():
    settings = SettingsManager.instance()
    active, other = _profile("Solo"), _profile("Duo")
    for bcs in (active, other):
        save_bot_strategy(bcs)
    settings.set_active_strategy("official", active.id)

    result = bot_fork.carry_over("official", "fork", settings)

    copies = {b.name: b for b in list_bot_strategies("fork")}
    assert set(copies) == {"Solo", "Duo"}
    assert result.strategies == {active.id: copies["Solo"].id, other.id: copies["Duo"].id}
    assert settings.get_active_strategy("fork") == copies["Solo"].id
    solo = copies["Solo"]
    assert solo.id != active.id and solo.duel_strategies[0].id != active.duel_strategies[0].id, (
        "fresh ids for the copy's own strategies, as duplicate_profile gives them"
    )
    assert solo.state_assignments[0].duel_combat_strategy_id == solo.duel_strategies[0].id
    assert solo.default_strategy_id == solo.duel_strategies[0].id
    assert {b.id for b in list_bot_strategies("official")} == {active.id, other.id}, "the Official bot keeps its own"
    assert settings.get_active_strategy("official") == active.id


def test_a_bot_with_nothing_set_carries_nothing():
    settings = SettingsManager.instance()

    result = bot_fork.carry_over("official", "fork", settings)

    assert result == bot_fork.CarriedOver()
    assert settings.get_active_strategy("fork") is None
    assert list_bot_strategies("fork") == []


def test_a_fork_runs_with_the_same_option_values_and_combat_as_the_bot_it_came_from():
    """The done-when: what the run reads for the copy is what it read for the Official bot."""
    settings = SettingsManager.instance()
    official = _official(options=[BotOptionModel(name="laps", type="int", default=1)])
    settings.set_option_override(official.id, "laps", 7)
    bcs = _profile()
    save_bot_strategy(bcs)
    settings.set_active_strategy(official.id, bcs.id)

    fork = bot_fork.make_fork(official, "Mine")
    bot_fork.carry_over(official.id, fork.id, settings)

    assert option_values.option_value(official, "laps") == 7
    assert option_values.option_value(fork, "laps") == 7
    mine = load_bot_strategy(settings.get_active_strategy(fork.id))
    theirs = load_bot_strategy(settings.get_active_strategy(official.id))
    assert mine.bot_id == fork.id
    assert [a.state_ids for a in mine.state_assignments] == [a.state_ids for a in theirs.state_assignments]
    assert [d.name for d in mine.duel_strategies] == [d.name for d in theirs.duel_strategies]
