"""Tests for bartlebot.combat.flee_marks: the session's fights fled on a default (Stage 7 of epic #201).

``fled_on_default`` runs against the same two real roots as the resolution's tests: a
shipped one holding a copy of the repo's ``strategies/``, and an empty user root.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.combat import strategy_store
from bartlebot.combat.bot_strategy_models import BotCombatStrategy, FightRef, StateAssignment
from bartlebot.combat.combat_resolution import Level
from bartlebot.combat.flee_marks import (
    FledFight,
    FleeMarks,
    fled_fight_tooltip,
    fled_on_default,
    fled_pill_text,
    fled_text,
    fled_tooltip,
)
from bartlebot.paths import project_root
from bartlebot.shared.preferences import PreferenceStore
from bartlebot.shared.strategy_models import DuelCombatStrategy, PlayerAssignment


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    shutil.copytree(project_root() / "strategies", shipped / "strategies")
    user.mkdir()
    return shipped, user


@pytest.fixture
def prefs(tmp_path: Path) -> PreferenceStore:
    return PreferenceStore(tmp_path / "vendor")


def _bot() -> BotConfig:
    """``boss`` asked for a strategy of its own; ``mob`` runs the bot's default."""
    return BotConfig(
        id="bot-1",
        name="Bot",
        initial="mob",
        recovery_state="mob",
        states=[
            StateModel(id="mob", node_type="handle_combat", label="Mob"),
            StateModel(id="boss", node_type="handle_combat", label="Lost Soul", params={"use_default_strategy": False}),
        ],
        transitions=[],
    )


def _fled(marks, roots, prefs, pick=None, clients=2, bot=None):
    shipped, user = roots
    return fled_on_default(marks, bot or _bot(), clients, pick, prefs=prefs, root=shipped, user_root=user)


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", [Level.YOUR_DEFAULT, Level.SHIPPED_DEFAULT])
def test_a_flee_on_a_default_counts(level):
    marks = FleeMarks()
    assert marks.record("bot-1", "boss", level) is True
    assert marks.counts("bot-1") == {"boss": 1}


@pytest.mark.parametrize("level", [Level.PICK, Level.OFFICIAL, Level.LAST_RESORT])
def test_a_flee_on_anything_tailored_or_on_the_last_resort_counts_for_nothing(level):
    marks = FleeMarks()
    assert marks.record("bot-1", "boss", level) is False
    assert marks.counts("bot-1") == {}


def test_a_flee_with_no_state_counts_for_nothing():
    marks = FleeMarks()
    assert marks.record("bot-1", None, Level.SHIPPED_DEFAULT) is False
    assert marks.counts("bot-1") == {}


def test_the_count_matches_the_flees_this_session_per_fight_and_per_bot():
    marks = FleeMarks()
    for _ in range(3):
        marks.record("bot-1", "boss", Level.SHIPPED_DEFAULT)
    marks.record("bot-1", "mob", Level.YOUR_DEFAULT)
    marks.record("bot-1", "boss", Level.PICK)
    marks.record("bot-2", "boss", Level.SHIPPED_DEFAULT)

    assert marks.counts("bot-1") == {"boss": 3, "mob": 1}
    assert marks.counts("bot-2") == {"boss": 1}
    assert marks.counts("bot-3") == {}
    assert marks.counts(None) == {}


def test_counts_hands_back_a_copy():
    marks = FleeMarks()
    marks.record("bot-1", "boss", Level.SHIPPED_DEFAULT)
    marks.counts("bot-1")["boss"] = 99
    assert marks.counts("bot-1") == {"boss": 1}


# ---------------------------------------------------------------------------
# Which marks still apply: resolved as a run would, writing nothing
# ---------------------------------------------------------------------------


def test_with_no_pick_every_fled_fight_still_runs_a_default(roots, prefs):
    marks = FleeMarks()
    marks.record("bot-1", "boss", Level.SHIPPED_DEFAULT)
    marks.record("bot-1", "boss", Level.SHIPPED_DEFAULT)
    marks.record("bot-1", "mob", Level.SHIPPED_DEFAULT)

    # In the bot's state order, by the author's label.
    assert _fled(marks, roots, prefs) == [FledFight("mob", "Mob", 1), FledFight("boss", "Lost Soul", 2)]


def test_nothing_recorded_is_nothing_marked(roots, prefs):
    assert _fled(FleeMarks(), roots, prefs) == []
    assert fled_on_default(FleeMarks(), None, 2, None) == []


def test_a_fight_given_a_strategy_of_its_own_loses_its_mark(roots, prefs):
    shipped, user = roots
    duel = DuelCombatStrategy(name="Mine", player_assignments=[PlayerAssignment(players=[1], dsl="pass")])
    bcs = BotCombatStrategy(
        name="Four wizards",
        bot_id="bot-1",
        duel_strategies=[duel],
        state_assignments=[StateAssignment(fights=[FightRef(state="boss")], duel_combat_strategy_id=duel.id)],
    )
    strategy_store.save_bot_strategy(bcs, shipped, user)
    marks = FleeMarks()
    marks.record("bot-1", "boss", Level.SHIPPED_DEFAULT)
    marks.record("bot-1", "mob", Level.SHIPPED_DEFAULT)

    # The pick has no default of its own, so the mob fight still falls to the defaults.
    assert _fled(marks, roots, prefs, pick=bcs.id) == [FledFight("mob", "Mob", 1)]


def test_a_bot_combat_strategys_default_covers_the_fights_it_runs(roots, prefs):
    shipped, user = roots
    duel = DuelCombatStrategy(name="Mine", player_assignments=[PlayerAssignment(players=[1], dsl="pass")])
    bcs = BotCombatStrategy(name="Solo", bot_id="bot-1", duel_strategies=[duel], default_strategy_id=duel.id)
    strategy_store.save_bot_strategy(bcs, shipped, user)
    marks = FleeMarks()
    marks.record("bot-1", "boss", Level.SHIPPED_DEFAULT)
    marks.record("bot-1", "mob", Level.SHIPPED_DEFAULT)

    assert _fled(marks, roots, prefs, pick=bcs.id) == []
    # The count is kept: with the pick gone, both marks are back.
    assert _fled(marks, roots, prefs) == [FledFight("mob", "Mob", 1), FledFight("boss", "Lost Soul", 1)]


def test_a_state_since_deleted_is_left_out(roots, prefs):
    marks = FleeMarks()
    marks.record("bot-1", "gone", Level.SHIPPED_DEFAULT)
    assert _fled(marks, roots, prefs) == []


def test_nobody_connected_still_marks(roots, prefs):
    marks = FleeMarks()
    marks.record("bot-1", "boss", Level.SHIPPED_DEFAULT)
    assert _fled(marks, roots, prefs, clients=0) == [FledFight("boss", "Lost Soul", 1)]


# ---------------------------------------------------------------------------
# The words
# ---------------------------------------------------------------------------


def test_the_words():
    assert fled_text(2) == "Fled 2× on the default"
    assert fled_pill_text("Lost Soul", 3) == "Lost Soul · fled 3×"
    assert fled_fight_tooltip("Lost Soul", 1) == (
        "Lost Soul was fled 1× this session on the default duel strategy. Click to give it a strategy of its own."
    )
    fights = [FledFight("mob", "Mob", 1), FledFight("boss", "Lost Soul", 2)]
    assert fled_tooltip(fights, "Do this.") == (
        "Fled this session on the default duel strategy: Mob 1×, Lost Soul 2×.\nDo this."
    )
