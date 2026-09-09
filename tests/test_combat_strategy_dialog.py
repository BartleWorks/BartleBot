"""Tests for CombatStrategyDialog — the Save/Cancel boundary, as a widget.

This file replaces ``test_strategy_editor_identity.py``, which existed for a
defect that no longer has a shape. Every editor used to autosave on a 500ms
debounce, so a save could land *after* the user had selected a different
strategy, and its ``changed`` signal had to carry an id to keep the late write
from being applied to the wrong entry. #4 retired the debounce: editors write
through to a working copy, and nothing reaches disk until Save. There is no
window across which the selection can move, so there is no id to carry.

What is worth testing now is the boundary itself in its Qt form — that the
editors mutate the working set and only the working set, that Cancel writes
nothing, and that Save writes everything — plus the two things the dialog knows
that the working set does not: that removing a shipped override is a *revert*,
and that promoting an assignment's DSL makes a Player Strategy the sibling tab
can see.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from PyQt6.QtWidgets import QMessageBox

from bartlebot import strategy_store as store_mod
from bartlebot.bot.strategy_models import (
    DeckPreset,
    DuelCombatStrategy,
    PlayerAssignment,
    PlayerCombatStrategy,
)
from bartlebot.gui.combat_strategies.dialog import (
    NEW_NAMES,
    CombatStrategyDialog,
    DialogMode,
    _KindPage,
)
from bartlebot.gui.combat_strategies.duel_editor import _CUSTOM_DATA
from bartlebot.strategy_store import (
    StrategyKind,
    list_duel_strategies,
    list_player_strategies,
    save_duel_strategy,
    save_player_strategy,
)
from bartlebot.strategy_workset import StrategyWorkingSet


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """A separate shipped and user root, so overrides and reverts are reachable."""
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    for root in (shipped, user):
        (root / "strategies" / "player").mkdir(parents=True)
        (root / "strategies" / "duel").mkdir(parents=True)
        (root / "strategies" / "decks").mkdir(parents=True)
        (root / "bot_strategies").mkdir(parents=True)
    monkeypatch.setattr(store_mod, "project_root", lambda: shipped)
    monkeypatch.setattr(store_mod, "product_dir", lambda: user)
    return shipped, user


def _dialog(roots, **kw) -> CombatStrategyDialog:
    shipped, user = roots
    ws = StrategyWorkingSet.from_store(root=shipped, user_root=user)
    return CombatStrategyDialog(ws, **kw)


# ---------------------------------------------------------------------------
# The boundary
# ---------------------------------------------------------------------------


def test_typing_in_an_editor_reaches_the_working_set_and_nothing_else(qapp, roots):
    shipped, user = roots
    pcs = PlayerCombatStrategy(name="Blade", dsl="blade")
    save_player_strategy(pcs, shipped)
    dlg = _dialog(roots, kind=StrategyKind.PLAYER, current_id=pcs.id)

    dlg._pages[StrategyKind.PLAYER]._editor._dsl_edit.setPlainText("pass")

    assert dlg._ws.get(StrategyKind.PLAYER, pcs.id).dsl == "pass"
    assert list_player_strategies(shipped, user)[0].dsl == "blade"


def test_save_writes_every_change_at_once(qapp, roots):
    shipped, user = roots
    pcs = PlayerCombatStrategy(name="Blade", dsl="blade")
    save_player_strategy(pcs, shipped)
    dlg = _dialog(roots, kind=StrategyKind.PLAYER, current_id=pcs.id)
    dlg._pages[StrategyKind.PLAYER]._editor._dsl_edit.setPlainText("pass")

    dlg._on_commit()

    assert list_player_strategies(shipped, user)[0].dsl == "pass"
    assert not dlg._ws.is_dirty


def test_cancel_writes_nothing(qapp, roots):
    shipped, user = roots
    pcs = PlayerCombatStrategy(name="Blade", dsl="blade")
    save_player_strategy(pcs, shipped)
    dlg = _dialog(roots, kind=StrategyKind.PLAYER, current_id=pcs.id)
    dlg._pages[StrategyKind.PLAYER]._editor._dsl_edit.setPlainText("pass")

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
        dlg.reject()

    assert list_player_strategies(shipped, user)[0].dsl == "blade"


def test_a_clean_cancel_asks_nothing(qapp, roots):
    save_player_strategy(PlayerCombatStrategy(name="Blade"), roots[0])
    dlg = _dialog(roots, kind=StrategyKind.PLAYER)

    with patch.object(QMessageBox, "question") as asked:
        dlg.reject()

    assert not asked.called


def test_declining_the_discard_prompt_keeps_the_dialog_open(qapp, roots):
    pcs = PlayerCombatStrategy(name="Blade", dsl="blade")
    save_player_strategy(pcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.PLAYER, current_id=pcs.id)
    dlg._pages[StrategyKind.PLAYER]._editor._dsl_edit.setPlainText("pass")

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
        dlg.reject()

    assert dlg._ws.is_dirty
    assert dlg.result() == 0


def test_a_save_that_could_not_write_keeps_the_dialog_open(qapp, roots, monkeypatch):
    """commit() logs and skips a failed write; closing anyway would lose it silently."""
    import bartlebot.strategy_workset as ws_mod

    pcs = PlayerCombatStrategy(name="Blade", dsl="blade")
    save_player_strategy(pcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.PLAYER, current_id=pcs.id)
    dlg._pages[StrategyKind.PLAYER]._editor._dsl_edit.setPlainText("pass")

    def boom(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setitem(ws_mod._SAVERS, StrategyKind.PLAYER, boom)
    with patch.object(QMessageBox, "warning") as warned:
        dlg._on_commit()

    assert warned.called
    assert dlg.result() == 0
    assert dlg._ws.is_dirty


# ---------------------------------------------------------------------------
# Pick mode
# ---------------------------------------------------------------------------


def test_use_returns_the_selection_and_commits_the_edits(qapp, roots):
    shipped, user = roots
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    save_duel_strategy(dcs, shipped)
    dlg = _dialog(roots, kind=StrategyKind.DUEL, mode=DialogMode.PICK, current_id=dcs.id)
    dlg._pages[StrategyKind.DUEL]._editor._name_edit.setText("Renamed")

    dlg._on_commit()

    assert dlg.selected_id() == dcs.id
    assert list_duel_strategies(shipped, user)[0].name == "Renamed"


def test_use_built_in_default_returns_none_and_says_so(qapp, roots):
    save_duel_strategy(DuelCombatStrategy(name="Frenzy AoE"), roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, mode=DialogMode.PICK)

    dlg._on_use_none()

    assert dlg.selected_id() is None
    assert dlg.used_built_in_default()


def test_a_pick_returns_the_kind_that_was_asked_for(qapp, roots):
    """A Player Strategy made while picking a duel is not an answer to the question."""
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    save_duel_strategy(dcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, mode=DialogMode.PICK, current_id=dcs.id)
    dlg._tabs.setCurrentWidget(dlg._pages[StrategyKind.PLAYER])
    dlg._pages[StrategyKind.PLAYER]._on_new()

    dlg._on_commit()

    assert dlg.selected_id() == dcs.id


def test_a_pick_that_may_not_be_empty_refuses_to_close_with_nothing(qapp, roots):
    save_duel_strategy(DuelCombatStrategy(name="Frenzy AoE"), roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, mode=DialogMode.PICK, allow_none=False)
    dlg._pages[StrategyKind.DUEL].select(None)

    with patch.object(QMessageBox, "information") as told:
        dlg._on_commit()

    assert told.called
    assert dlg.result() == 0


def test_manage_mode_offers_no_built_in_default_button(qapp, roots):
    dlg = _dialog(roots, kind=StrategyKind.DUEL, mode=DialogMode.MANAGE)

    assert not hasattr(dlg, "_none_btn")


# ---------------------------------------------------------------------------
# New / duplicate / delete-or-revert
# ---------------------------------------------------------------------------


def test_new_creates_selects_and_names_uniquely(qapp, roots):
    save_player_strategy(PlayerCombatStrategy(name=NEW_NAMES[StrategyKind.PLAYER]), roots[0])
    dlg = _dialog(roots, kind=StrategyKind.PLAYER)
    page = dlg._pages[StrategyKind.PLAYER]

    page._on_new()

    made = dlg._ws.get(StrategyKind.PLAYER, page.selected_id())
    assert made.name == f"{NEW_NAMES[StrategyKind.PLAYER]} (2)"
    assert page._editor.strategy() is made


def test_duplicate_selects_the_copy_not_the_original(qapp, roots):
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    save_duel_strategy(dcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)
    page = dlg._pages[StrategyKind.DUEL]

    page._on_duplicate()

    assert page.selected_id() != dcs.id
    assert dlg._ws.get(StrategyKind.DUEL, page.selected_id()).name == "Frenzy AoE (2)"


def test_a_shipped_override_offers_revert_rather_than_delete(qapp, roots):
    """Deleting only removes the user copy, so on one of these the act is revert."""
    shipped, user = roots
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    save_duel_strategy(dcs, shipped)
    save_duel_strategy(dcs, user)  # the override
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)

    assert dlg._pages[StrategyKind.DUEL]._del_btn.text() == "Revert to shipped"


def test_a_user_only_strategy_offers_delete(qapp, roots):
    dcs = DuelCombatStrategy(name="Mine")
    save_duel_strategy(dcs, roots[1])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)

    assert dlg._pages[StrategyKind.DUEL]._del_btn.text() == "Delete"


def test_reverting_an_override_restores_the_shipped_version_on_save(qapp, roots):
    shipped, user = roots
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    save_duel_strategy(dcs, shipped)
    save_duel_strategy(dcs.model_copy(update={"name": "Mine"}), user)
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        dlg._pages[StrategyKind.DUEL]._on_delete()
    dlg._on_commit()

    assert list_duel_strategies(shipped, user)[0].name == "Frenzy AoE"


def test_a_declined_delete_removes_nothing(qapp, roots):
    dcs = DuelCombatStrategy(name="Mine")
    save_duel_strategy(dcs, roots[1])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
        dlg._pages[StrategyKind.DUEL]._on_delete()

    assert not dlg._ws.is_dirty


# ---------------------------------------------------------------------------
# The detail pane's two extra lines
# ---------------------------------------------------------------------------


def test_the_detail_pane_says_what_uses_this_strategy(qapp, roots):
    shipped, _user = roots
    pcs = PlayerCombatStrategy(name="Blade")
    dcs = DuelCombatStrategy(
        name="Boss", player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)]
    )
    save_player_strategy(pcs, shipped)
    save_duel_strategy(dcs, shipped)
    dlg = _dialog(roots, kind=StrategyKind.PLAYER, current_id=pcs.id)

    assert dlg._pages[StrategyKind.PLAYER]._usage_lbl.text() == "Used by 1 duel strategy"


def test_the_usage_line_counts_a_link_made_a_minute_ago(qapp, roots):
    """The reason it is counted against the working set rather than disk."""
    shipped, _user = roots
    pcs = PlayerCombatStrategy(name="Blade")
    dcs = DuelCombatStrategy(name="Boss", player_assignments=[PlayerAssignment(players=[1])])
    save_player_strategy(pcs, shipped)
    save_duel_strategy(dcs, shipped)
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)
    row = dlg._pages[StrategyKind.DUEL]._editor._config_editor._rows[0]
    row._strategy_combo.setCurrentIndex(row._strategy_combo.findData(pcs.id))

    dlg._pages[StrategyKind.PLAYER].select(pcs.id)

    assert dlg._pages[StrategyKind.PLAYER]._usage_lbl.text() == "Used by 1 duel strategy"


# ---------------------------------------------------------------------------
# Promotion: the row asks, the dialog names and creates
# ---------------------------------------------------------------------------


def test_promoting_a_rows_dsl_makes_a_player_strategy_and_links_it(qapp, roots):
    shipped, _user = roots
    dcs = DuelCombatStrategy(name="Boss", player_assignments=[PlayerAssignment(players=[1], dsl="blade", school="Ice")])
    save_duel_strategy(dcs, shipped)
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)
    row = dlg._pages[StrategyKind.DUEL]._editor._config_editor._rows[0]

    with patch("bartlebot.gui.combat_strategies.dialog.QInputDialog.getText", return_value=("Ice Blade", True)):
        row._promote_btn.click()

    made = next(p for p in dlg._ws.players if p.name == "Ice Blade")
    assert made.dsl == "blade"
    assert made.school == "Ice"
    assert row.assignment().player_combat_strategy_id == made.id
    assert not row.is_custom()


def test_a_cancelled_promotion_changes_nothing(qapp, roots):
    dcs = DuelCombatStrategy(name="Boss", player_assignments=[PlayerAssignment(players=[1], dsl="blade")])
    save_duel_strategy(dcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)
    row = dlg._pages[StrategyKind.DUEL]._editor._config_editor._rows[0]

    with patch("bartlebot.gui.combat_strategies.dialog.QInputDialog.getText", return_value=("", False)):
        row._promote_btn.click()

    assert dlg._ws.players == []
    assert row.assignment().player_combat_strategy_id is None


def test_a_player_strategy_made_on_the_sibling_tab_appears_in_the_selector(qapp, roots):
    """What retired the modal picker-on-a-picker."""
    dcs = DuelCombatStrategy(name="Boss", player_assignments=[PlayerAssignment(players=[1])])
    save_duel_strategy(dcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)
    dlg._pages[StrategyKind.PLAYER]._on_new()

    dlg._tabs.setCurrentWidget(dlg._pages[StrategyKind.DUEL])

    row = dlg._pages[StrategyKind.DUEL]._editor._config_editor._rows[0]
    assert NEW_NAMES[StrategyKind.PLAYER] in [
        row._strategy_combo.itemText(i) for i in range(row._strategy_combo.count())
    ]


# ---------------------------------------------------------------------------
# Custom DSL, one level deeper
# ---------------------------------------------------------------------------


def test_a_fresh_assignment_shows_no_dsl_box(qapp, roots):
    """The Assemble tier: no monospace box in front of a user who will never type in one."""
    dcs = DuelCombatStrategy(name="Boss", player_assignments=[PlayerAssignment(players=[1])])
    save_duel_strategy(dcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)

    row = dlg._pages[StrategyKind.DUEL]._editor._config_editor._rows[0]
    assert not row.is_custom()
    assert row._dsl_edit.isHidden()
    assert row._promote_btn.isHidden()


def test_choosing_custom_dsl_opens_the_box_and_offers_to_name_it(qapp, roots):
    dcs = DuelCombatStrategy(name="Boss", player_assignments=[PlayerAssignment(players=[1])])
    save_duel_strategy(dcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)
    row = dlg._pages[StrategyKind.DUEL]._editor._config_editor._rows[0]

    row._strategy_combo.setCurrentIndex(row._strategy_combo.findData(_CUSTOM_DATA))
    assert row.is_custom()
    assert row._promote_btn.isHidden()  # nothing to name yet

    row._dsl_edit.setPlainText("blade")
    assert not row._promote_btn.isHidden()


# ---------------------------------------------------------------------------
# Deck presets
# ---------------------------------------------------------------------------


def test_the_deck_tab_edits_identity_only(qapp, roots):
    from bartlebot.strategy_store import save_deck_preset  # noqa: PLC0415

    preset = DeckPreset(name="Storm 60")
    save_deck_preset(preset, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DECK, current_id=preset.id)
    page = dlg._pages[StrategyKind.DECK]

    page._editor._description_edit.setText("for the Storm wizard")

    assert dlg._ws.get(StrategyKind.DECK, preset.id).description == "for the Storm wizard"


def test_every_kind_gets_a_page(qapp, roots):
    dlg = _dialog(roots)

    assert dlg._tabs.count() == 3
    assert all(isinstance(dlg._tabs.widget(i), _KindPage) for i in range(3))
