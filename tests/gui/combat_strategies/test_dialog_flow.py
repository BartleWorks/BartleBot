"""CombatStrategyDialog as step 1 of the Combat tab's add flow (#272).

What a ``FlowStep`` changes (title, heading, commit), and ``start_new``: the dialog opens on
a new strategy rather than an empty pane, a copy of the one it starts from, and forgets it
at Save when another was chosen and it was never touched.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from PyQt6.QtWidgets import QMessageBox

from bartlebot.combat.bot_strategy_models import BotCombatStrategy
from bartlebot.combat.strategy_store import StrategyKind, load_bot_strategy, save_bot_strategy, save_duel_strategy
from bartlebot.combat.strategy_workset import StrategyWorkingSet
from bartlebot.content import store_roots
from bartlebot.gui.combat_strategies.dialog import NEW_NAMES, CombatStrategyDialog, DialogMode, FlowStep
from bartlebot.gui.widgets.labels.step_heading import Step
from bartlebot.shared.strategy_models import DuelCombatStrategy, PlayerAssignment

DUEL = StrategyKind.DUEL
FLOW = FlowStep("Add a duel strategy", Step(1, 2, "Make the duel strategy", "Next, the fights."), "Next: choose fights")


@pytest.fixture
def roots(tmp_path, monkeypatch):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    for root in (shipped, user):
        for sub in ("strategies/player", "strategies/duel", "strategies/decks", "bot_strategies"):
            (root / sub).mkdir(parents=True)
    monkeypatch.setattr(store_roots, "project_root", lambda: shipped)
    monkeypatch.setattr(store_roots, "product_dir", lambda: user)
    return shipped, user


def _setup(roots):
    """A Bot Combat Strategy with nothing of its own, and one library duel to start from."""
    shipped, user = roots
    library = DuelCombatStrategy(
        name="Default Team Duel",
        description="One hitter, the others support.",
        player_assignments=[PlayerAssignment(players=[1], dsl="pass")],
    )
    save_duel_strategy(library, shipped, user)
    bcs = BotCombatStrategy(name="Solo", bot_id="bot-1")
    save_bot_strategy(bcs, shipped, user)
    ws = StrategyWorkingSet.from_store(root=shipped, user_root=user, bot_strategy_id=bcs.id, bot_id="bot-1")
    return ws, bcs, library


# ---------------------------------------------------------------------------
# The flow step
# ---------------------------------------------------------------------------


def test_a_flow_step_names_the_dialog_heads_it_and_says_where_save_goes(qapp, roots):
    ws, _bcs, _library = _setup(roots)
    dlg = CombatStrategyDialog(ws, kind=DUEL, mode=DialogMode.PICK, allow_none=False, flow=FLOW)

    assert dlg.windowTitle() == "Add a duel strategy"
    assert dlg.step_heading.counter_text() == "Step 1 of 2"
    assert dlg.step_heading.heading_text() == "Make the duel strategy"
    assert dlg.commit_button.text() == "Next: choose fights"


def test_without_a_flow_the_dialog_is_as_it_was(qapp, roots):
    ws, _bcs, _library = _setup(roots)
    dlg = CombatStrategyDialog(ws, kind=DUEL, mode=DialogMode.BOT_STRATEGY)

    assert dlg.windowTitle() == "Combat Strategies"
    assert dlg.step_heading is None
    assert dlg.commit_button.text() == "Save"


# ---------------------------------------------------------------------------
# start_new: opening on a strategy rather than an empty pane
# ---------------------------------------------------------------------------


def test_the_copy_takes_the_setup_but_not_the_name_or_description(qapp, roots):
    ws, _bcs, library = _setup(roots)
    dlg = CombatStrategyDialog(
        ws, kind=DUEL, mode=DialogMode.BOT_STRATEGY, flow=FLOW, start_new=True, start_from=library.id
    )
    made = ws.get(DUEL, dlg.page(DUEL).selected_id())

    assert made.id != library.id
    assert ws.is_own_entry(DUEL, made.id)
    assert made.player_assignments == library.player_assignments
    assert made.name == NEW_NAMES[DUEL]
    assert made.description == ""


def test_with_nothing_to_start_from_it_opens_on_news_own_entry(qapp, roots):
    ws, _bcs, _library = _setup(roots)
    dlg = CombatStrategyDialog(ws, kind=DUEL, mode=DialogMode.BOT_STRATEGY, start_new=True)
    made = ws.get(DUEL, dlg.page(DUEL).selected_id())

    assert ws.is_own_entry(DUEL, made.id)
    assert made.name == NEW_NAMES[DUEL]
    assert made.player_assignments == []


def test_the_new_name_is_ready_to_type_over_once_shown(qapp, roots):
    ws, _bcs, library = _setup(roots)
    dlg = CombatStrategyDialog(ws, kind=DUEL, mode=DialogMode.BOT_STRATEGY, start_new=True, start_from=library.id)
    name = dlg.page(DUEL).about.name_edit

    dlg.show()
    try:
        # The window's focus widget, not hasFocus(): that waits on the WM activating it.
        assert dlg.focusWidget() is name
        assert name.selectedText() == NEW_NAMES[DUEL]
    finally:
        dlg.reject()


def test_pick_makes_nothing_however_it_is_asked(qapp, roots):
    ws, _bcs, library = _setup(roots)
    CombatStrategyDialog(ws, kind=DUEL, mode=DialogMode.PICK, start_new=True, start_from=library.id)

    assert ws.own_items(DUEL) == []


# ---------------------------------------------------------------------------
# The starter is forgotten if it was passed over untouched
# ---------------------------------------------------------------------------


def test_saving_with_the_starter_selected_keeps_it_and_says_it_was_created(qapp, roots):
    ws, bcs, library = _setup(roots)
    dlg = CombatStrategyDialog(ws, kind=DUEL, mode=DialogMode.BOT_STRATEGY, start_new=True, start_from=library.id)
    starter = dlg.page(DUEL).selected_id()

    dlg._on_commit()

    assert dlg.selected_id() == starter
    assert dlg.selected_was_created()
    assert [d.id for d in load_bot_strategy(bcs.id).duel_strategies] == [starter]


def test_choosing_another_drops_an_untouched_starter(qapp, roots):
    ws, bcs, library = _setup(roots)
    dlg = CombatStrategyDialog(ws, kind=DUEL, mode=DialogMode.BOT_STRATEGY, start_new=True, start_from=library.id)
    dlg.page(DUEL).select(library.id)

    dlg._on_commit()

    assert dlg.selected_id() == library.id
    assert load_bot_strategy(bcs.id).duel_strategies == []


def test_choosing_another_keeps_a_starter_the_user_edited(qapp, roots):
    """Edited is work, and work is never thrown away behind the user's back."""
    ws, bcs, library = _setup(roots)
    dlg = CombatStrategyDialog(ws, kind=DUEL, mode=DialogMode.BOT_STRATEGY, start_new=True, start_from=library.id)
    starter = dlg.page(DUEL).selected_id()
    ws.get(DUEL, starter).name = "Boss Burn"
    dlg.page(DUEL).select(library.id)

    dlg._on_commit()

    assert [d.name for d in load_bot_strategy(bcs.id).duel_strategies] == ["Boss Burn"]


def test_cancel_on_an_untouched_starter_asks_nothing(qapp, roots):
    """The dialog made it, not the user: "1 change will be lost" would be a change they never made."""
    ws, bcs, library = _setup(roots)
    dlg = CombatStrategyDialog(ws, kind=DUEL, mode=DialogMode.BOT_STRATEGY, start_new=True, start_from=library.id)

    with patch.object(QMessageBox, "question") as asked:
        dlg.reject()

    asked.assert_not_called()
    assert load_bot_strategy(bcs.id).duel_strategies == []


def test_cancel_on_an_edited_starter_still_asks(qapp, roots):
    ws, _bcs, library = _setup(roots)
    dlg = CombatStrategyDialog(ws, kind=DUEL, mode=DialogMode.BOT_STRATEGY, start_new=True, start_from=library.id)
    ws.get(DUEL, dlg.page(DUEL).selected_id()).name = "Boss Burn"

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel) as asked:
        dlg.reject()

    asked.assert_called_once()
