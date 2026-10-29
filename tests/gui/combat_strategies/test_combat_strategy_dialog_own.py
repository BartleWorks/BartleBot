"""Tests for CombatStrategyDialog's ways in, and the Bot Combat Strategy inside it (#118, #123).

What each way in shows, that Pick selects and edits nothing, and everything that makes the
difference between a library strategy and one of the bot's own visible: the section, the pill, the
About line, Add to library, and the confirmation at Save.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from bartleby.qt.widgets.buttons.action_button import ActionButton
from bartleby.shared.strategy_models import DeckPreset, DuelCombatStrategy, PlayerAssignment, PlayerCombatStrategy
from PyQt6.QtWidgets import QInputDialog, QMessageBox, QSplitter

from bartlebot.combat.bot_strategy_models import BotCombatStrategy
from bartlebot.combat.strategy_store import (
    StrategyKind,
    list_duel_strategies,
    load_bot_strategy,
    save_bot_strategy,
    save_deck_preset,
    save_duel_strategy,
    save_player_strategy,
)
from bartlebot.combat.strategy_tags import THIS_BOT_SOURCE
from bartlebot.combat.strategy_workset import StrategyWorkingSet
from bartlebot.content import store_roots
from bartlebot.gui.combat_strategies.assignment_card import strategy_menu_entries
from bartlebot.gui.combat_strategies.dialog import (
    BOT_STRATEGY_EMPTY_NOTE,
    CREATE_BOT_STRATEGY_TEXT,
    FAVORITES_HEADING,
    LIBRARY_HEADING,
    NO_BOT_STRATEGY_NOTE,
    THIS_BOT_SECTION,
    CombatStrategyDialog,
    DialogMode,
    move_confirmation,
    tabs_for,
)
from bartlebot.gui.widgets.pills.tag_pill import ThisBotPill
from bartlebot.settings_manager import SettingsManager

DUEL, PLAYER, DECK = StrategyKind.DUEL, StrategyKind.PLAYER, StrategyKind.DECK


@pytest.fixture
def roots(tmp_path, monkeypatch):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    for root in (shipped, user):
        for sub in ("strategies/player", "strategies/duel", "strategies/decks", "bot_strategies"):
            (root / sub).mkdir(parents=True)
    monkeypatch.setattr(store_roots, "project_root", lambda: shipped)
    monkeypatch.setattr(store_roots, "product_dir", lambda: user)
    return shipped, user


def _ws(roots, bcs: BotCombatStrategy | None = None, bot_id: str | None = "bot-1") -> StrategyWorkingSet:
    shipped, user = roots
    if bcs is not None:
        save_bot_strategy(bcs, shipped, user)
    return StrategyWorkingSet.from_store(
        root=shipped, user_root=user, bot_strategy_id=bcs.id if bcs else None, bot_id=bot_id
    )


def _bcs(**kw) -> BotCombatStrategy:
    return BotCombatStrategy(name=kw.pop("name", "Solo"), bot_id="bot-1", **kw)


def _dialog(ws, **kw) -> CombatStrategyDialog:
    return CombatStrategyDialog(ws, **kw)


# ---------------------------------------------------------------------------
# Each way in shows exactly its tabs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "kind", "tabs"),
    [
        (DialogMode.LIBRARY, DUEL, (DUEL, PLAYER, DECK)),
        (DialogMode.BOT_STRATEGY, DUEL, (DUEL, PLAYER)),
        (DialogMode.PICK, DUEL, (DUEL,)),
        (DialogMode.PICK, DECK, (DECK,)),
    ],
)
def test_each_way_in_lists_exactly_its_tabs(mode, kind, tabs):
    assert tabs_for(mode, kind) == tabs


@pytest.mark.parametrize(
    ("mode", "kind", "tabs"),
    [
        (DialogMode.LIBRARY, DUEL, [DUEL, PLAYER, DECK]),
        (DialogMode.BOT_STRATEGY, DUEL, [DUEL, PLAYER]),
        (DialogMode.PICK, DUEL, [DUEL]),
        (DialogMode.PICK, DECK, [DECK]),
    ],
)
def test_the_dialog_builds_exactly_those_tabs(qapp, roots, mode, kind, tabs):
    dlg = _dialog(_ws(roots, _bcs()), mode=mode, kind=kind)

    assert dlg.tab_kinds() == tabs
    assert dlg._tabs.count() == len(tabs)


# ---------------------------------------------------------------------------
# Pick is select-only (#123)
# ---------------------------------------------------------------------------


def test_pick_has_no_new_duplicate_or_delete(qapp, roots):
    save_duel_strategy(DuelCombatStrategy(name="Boss"), *roots)
    dlg = _dialog(_ws(roots), mode=DialogMode.PICK)
    page = dlg.page(DUEL)

    assert page.button_row.isHidden()
    assert not page.editable


def test_pick_shows_a_duel_read_only(qapp, roots):
    duel = DuelCombatStrategy(name="Boss", player_assignments=[PlayerAssignment(players=[1], dsl="pass")])
    save_duel_strategy(duel, *roots)
    dlg = _dialog(_ws(roots), mode=DialogMode.PICK, current_id=duel.id)
    page = dlg.page(DUEL)

    assert page.about.name_edit.isReadOnly()
    assert page.editor.add_card.isHidden()
    assert all(not card.pills.isEnabled() or not card._editable for card in page.editor.cards)


def test_pick_shows_a_deck_read_only(qapp, roots):
    preset = DeckPreset(name="Deck")
    save_deck_preset(preset, *roots)
    dlg = _dialog(_ws(roots, bot_id=None), mode=DialogMode.PICK, kind=DECK, current_id=preset.id)
    page = dlg.page(DECK)

    assert page.button_row.isHidden()
    assert not page.editor.archmastery.isEnabled()


def test_the_player_editor_is_read_only_when_it_is_not_editable(qapp):
    from bartlebot.gui.combat_strategies.player_editor import PlayerStrategyEditor  # noqa: PLC0415

    editor = PlayerStrategyEditor()
    editor.set_editable(False)

    assert editor.dsl_edit.isReadOnly()
    assert not editor.school_picker.isEnabled()


def test_the_library_view_and_the_bot_strategy_editor_can_edit(qapp, roots):
    for mode in (DialogMode.LIBRARY, DialogMode.BOT_STRATEGY):
        dlg = _dialog(_ws(roots, _bcs()), mode=mode)
        page = dlg.page(DUEL)
        assert not page.button_row.isHidden()
        assert not page.about.name_edit.isReadOnly()


def test_pick_does_not_ask_about_shared_edits(qapp, roots):
    duel = DuelCombatStrategy(name="Boss")
    save_duel_strategy(duel, *roots)
    dlg = _dialog(_ws(roots), mode=DialogMode.PICK, current_id=duel.id)

    with patch.object(CombatStrategyDialog, "_confirm_shared_edits") as asked:
        dlg._on_commit()

    asked.assert_not_called()
    assert dlg.selected_id() == duel.id


# ---------------------------------------------------------------------------
# The This bot section
# ---------------------------------------------------------------------------


def _with_both(roots):
    """A Bot Combat Strategy with one duel of its own, and one library duel."""
    own = DuelCombatStrategy(name="Just This Fight")
    library = DuelCombatStrategy(name="Shared Boss")
    save_duel_strategy(library, *roots)
    return _ws(roots, _bcs(duel_strategies=[own])), own, library


def test_the_this_bot_section_comes_first_and_is_headed_with_the_bot_combat_strategys_name(qapp, roots):
    ws, own, library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.BOT_STRATEGY)
    page = dlg.page(DUEL)

    assert page.section_headings() == ["Solo", LIBRARY_HEADING]
    assert page.ids_in_section(THIS_BOT_SECTION) == [own.id]
    assert page.ids_in_section("library") == [library.id]


def test_the_this_bot_section_goes_before_favorites(qapp, roots):
    ws, _own, library = _with_both(roots)
    SettingsManager.instance().toggle_favorite_strategy("duel", library.id)
    dlg = _dialog(ws, mode=DialogMode.BOT_STRATEGY)
    page = dlg.page(DUEL)

    assert page.section_headings()[:2] == ["Solo", FAVORITES_HEADING]


def test_an_own_entry_wears_a_this_bot_pill_and_no_star(qapp, roots):
    """The pill says "This bot"; the heading above it says which one, and the tooltip does too."""
    ws, own, library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.BOT_STRATEGY)
    page = dlg.page(DUEL)

    card = page.card_for(own.id)
    assert card.source_text == THIS_BOT_SOURCE == "This bot"
    assert isinstance(card._source_pill, ThisBotPill)
    assert card._source_pill.toolTip() == "Belongs to the Bot Combat Strategy 'Solo' only"
    assert card.star is None
    assert page.card_for(library.id).star is not None


def test_pick_shows_the_this_bot_section_too(qapp, roots):
    ws, own, _library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.PICK)
    page = dlg.page(DUEL)

    assert page.ids_in_section(THIS_BOT_SECTION) == [own.id]


def test_the_library_view_never_shows_a_bot_strategy(qapp, roots):
    ws, own, library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.LIBRARY)
    page = dlg.page(DUEL)

    assert page.visible_ids() == [library.id]
    assert page.section_headings() == []
    assert THIS_BOT_SOURCE not in page.filter_bar.tag_pickers["Source"].vocabulary.tags


def test_an_empty_this_bot_section_says_what_it_is_for(qapp, roots):
    dlg = _dialog(_ws(roots, _bcs()), mode=DialogMode.BOT_STRATEGY)
    page = dlg.page(DUEL)

    assert page.note_text() == BOT_STRATEGY_EMPTY_NOTE
    assert page.section_headings()[0] == "Solo"


def test_the_empty_bot_strategy_note_wraps_when_the_column_narrows(qapp, roots):
    """Its row is refitted like a card's, or the sentence is cut off past the first line."""
    dlg = _dialog(_ws(roots, _bcs()), mode=DialogMode.BOT_STRATEGY)
    page = dlg.page(DUEL)
    splitter = page.findChild(QSplitter)
    dlg.resize(1400, 720)
    dlg.show()
    qapp.processEvents()

    item, row = page._note_item, page._note_widget
    wide = item.sizeHint().height()
    assert wide == row.heightForWidth(row.width())

    splitter.setSizes([splitter.widget(0).minimumSizeHint().width(), 2000])
    qapp.processEvents()

    assert item.sizeHint().height() == row.heightForWidth(row.width())
    assert item.sizeHint().height() > wide
    dlg.close()


def test_a_bot_with_no_bot_strategy_is_offered_one(qapp, roots):
    dlg = _dialog(_ws(roots, None), mode=DialogMode.BOT_STRATEGY)
    page = dlg.page(DUEL)

    assert page.note_text() == NO_BOT_STRATEGY_NOTE
    buttons = [b.text() for b in page._note_widget.findChildren(ActionButton)]
    assert buttons == [CREATE_BOT_STRATEGY_TEXT]
    assert not page.new_button.isEnabled()


def test_pick_mentions_a_missing_bot_strategy_but_offers_nothing(qapp, roots):
    dlg = _dialog(_ws(roots, None), mode=DialogMode.PICK)
    page = dlg.page(DUEL)

    assert page.note_text() == NO_BOT_STRATEGY_NOTE
    assert page._note_widget.findChildren(ActionButton) == []


def test_create_one_makes_a_bot_strategy_that_cancel_discards(qapp, roots):
    ws = _ws(roots, None)
    dlg = _dialog(ws, mode=DialogMode.BOT_STRATEGY)

    with patch.object(QInputDialog, "getText", return_value=("Solo", True)):
        dlg._on_create_bot_strategy()

    page = dlg.page(DUEL)
    assert ws.bot_strategy.name == "Solo"
    assert page.section_headings()[0] == "Solo"
    assert page.note_text() == BOT_STRATEGY_EMPTY_NOTE
    assert page.new_button.isEnabled()


def test_the_source_filter_finds_bot_strategy_entries(qapp, roots):
    ws, own, _library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.BOT_STRATEGY)
    page = dlg.page(DUEL)

    # The facet offers the very word the pill wears: both come from THIS_BOT_SOURCE.
    assert THIS_BOT_SOURCE in page.filter_bar.tag_pickers["Source"].vocabulary.tags
    assert page.card_for(own.id).source_text == THIS_BOT_SOURCE

    page.filter_bar.tag_pickers["Source"].set_selection([THIS_BOT_SOURCE])
    page.filter_bar._on_selection_changed()

    assert page.visible_ids() == [own.id]


# ---------------------------------------------------------------------------
# Creating in the Bot Combat Strategy editor
# ---------------------------------------------------------------------------


def test_new_in_the_bot_strategy_editor_creates_in_the_bot_strategy(qapp, roots):
    ws = _ws(roots, _bcs())
    dlg = _dialog(ws, mode=DialogMode.BOT_STRATEGY)
    page = dlg.page(DUEL)

    page._on_new()

    assert ws.is_own_entry(DUEL, page.selected_id())
    assert page.ids_in_section(THIS_BOT_SECTION) == [page.selected_id()]


def test_duplicating_a_library_entry_in_the_bot_strategy_editor_lands_in_the_bot_strategy(qapp, roots):
    ws, _own, library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.BOT_STRATEGY, current_id=library.id)
    page = dlg.page(DUEL)

    page._on_duplicate()

    assert ws.is_own_entry(DUEL, page.selected_id())
    assert page.selected_id() != library.id


def test_new_in_the_library_view_creates_in_the_library(qapp, roots):
    ws = _ws(roots, _bcs())
    dlg = _dialog(ws, mode=DialogMode.LIBRARY)
    page = dlg.page(DUEL)

    page._on_new()

    assert not ws.is_own_entry(DUEL, page.selected_id())


def test_save_as_player_strategy_follows_the_duel_it_came_from(qapp, roots):
    own = DuelCombatStrategy(name="Own", player_assignments=[PlayerAssignment(players=[1], dsl="pass")])
    ws = _ws(roots, _bcs(duel_strategies=[own]))
    dlg = _dialog(ws, mode=DialogMode.BOT_STRATEGY, current_id=own.id)
    card = dlg.page(DUEL).editor.cards[0]

    with patch.object(QInputDialog, "getText", return_value=("Kept", True)):
        dlg._on_promote_requested(card)

    pcs_id = card.assignment().player_combat_strategy_id
    assert ws.is_own_entry(PLAYER, pcs_id)


# ---------------------------------------------------------------------------
# What an assignment may link
# ---------------------------------------------------------------------------


def _menu_names(page) -> list[str]:
    return [label for label, _data in strategy_menu_entries(page.editor.cards[0]._all_pcs)]


def test_a_library_duels_menu_offers_library_player_strategies_only(qapp, roots):
    own_pcs = PlayerCombatStrategy(name="Own P")
    library_pcs = PlayerCombatStrategy(name="Library P")
    save_player_strategy(library_pcs, *roots)
    library = DuelCombatStrategy(name="Shared", player_assignments=[PlayerAssignment(players=[1])])
    save_duel_strategy(library, *roots)
    ws = _ws(roots, _bcs(player_strategies=[own_pcs]))
    dlg = _dialog(ws, mode=DialogMode.BOT_STRATEGY, current_id=library.id)
    page = dlg.page(DUEL)

    assert "Own P" not in _menu_names(page)
    assert "Library P" in _menu_names(page)


def test_an_own_duels_menu_offers_the_bot_strategies_first(qapp, roots):
    own_pcs = PlayerCombatStrategy(name="Own P")
    library_pcs = PlayerCombatStrategy(name="Library P")
    save_player_strategy(library_pcs, *roots)
    own = DuelCombatStrategy(name="Own", player_assignments=[PlayerAssignment(players=[1])])
    ws = _ws(roots, _bcs(player_strategies=[own_pcs], duel_strategies=[own]))
    dlg = _dialog(ws, mode=DialogMode.BOT_STRATEGY, current_id=own.id)
    page = dlg.page(DUEL)

    names = _menu_names(page)
    assert names.index("Own P") < names.index("Library P")


# ---------------------------------------------------------------------------
# The About pane, and Add to library
# ---------------------------------------------------------------------------


def test_an_own_entry_says_where_it_lives_with_add_to_library_beside_it(qapp, roots):
    ws, own, _library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.BOT_STRATEGY, current_id=own.id)
    page = dlg.page(DUEL)

    assert page.about.origin_text == "In 'Solo' only"
    assert not page.about.add_to_library_button.isHidden()
    assert page.delete_button.text() == "Delete"


def test_pick_says_where_it_lives_but_offers_no_move(qapp, roots):
    ws, own, _library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.PICK, current_id=own.id)
    page = dlg.page(DUEL)

    assert page.about.origin_text == "In 'Solo' only"
    assert page.about.add_to_library_button.isHidden()


def test_a_library_entry_offers_no_move(qapp, roots):
    ws, _own, library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.BOT_STRATEGY, current_id=library.id)
    page = dlg.page(DUEL)

    assert page.about.add_to_library_button.isHidden()


def test_the_move_confirmation_uses_numerals_throughout():
    assert move_confirmation("Boss Burn", ["Frost Heal", "Blade Stack"]) == (
        "'Boss Burn' uses 2 player strategies from this bot: 'Frost Heal' and 'Blade Stack'. "
        "All 3 will be moved to the library."
    )


def _move_fixture(roots):
    frost = PlayerCombatStrategy(name="Frost Heal")
    boss = DuelCombatStrategy(
        name="Boss Burn", player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=frost.id)]
    )
    ws = _ws(roots, _bcs(duel_strategies=[boss], player_strategies=[frost]))
    dlg = _dialog(ws, mode=DialogMode.BOT_STRATEGY, current_id=boss.id)
    return ws, dlg, boss, frost


def test_add_to_library_names_what_moves_along_and_moves_it(qapp, roots):
    ws, dlg, boss, frost = _move_fixture(roots)
    page = dlg.page(DUEL)

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as asked:
        page.about.add_to_library_button.click()

    assert "'Frost Heal'" in asked.call_args.args[2]
    assert not ws.is_own_entry(DUEL, boss.id)
    assert page.ids_in_section("library") == [boss.id]
    assert page.selected_id() == boss.id
    # The Player tab follows, since the player strategy moved too.
    assert dlg.page(PLAYER).ids_in_section("library") == [frost.id]


def test_declining_the_move_moves_nothing(qapp, roots):
    ws, dlg, boss, _frost = _move_fixture(roots)

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
        dlg.page(DUEL).about.add_to_library_button.click()

    assert ws.is_own_entry(DUEL, boss.id)


def test_a_move_reaches_disk_at_save_and_not_before(qapp, roots):
    ws, dlg, boss, _frost = _move_fixture(roots)
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        dlg.page(DUEL).about.add_to_library_button.click()
    assert list_duel_strategies(*roots) == []

    dlg._on_commit()

    assert [d.id for d in list_duel_strategies(*roots)] == [boss.id]
    assert load_bot_strategy(ws.bot_strategy.id, *roots).duel_strategies == []


# ---------------------------------------------------------------------------
# The Save confirmation
# ---------------------------------------------------------------------------


def _shared_fixture(roots):
    duel = DuelCombatStrategy(name="Boss Burn")
    save_duel_strategy(duel, *roots)
    save_bot_strategy(BotCombatStrategy(name="Team", bot_id="bot-2", default_strategy_id=duel.id), *roots)
    ws = _ws(roots, _bcs(default_strategy_id=duel.id))
    dlg = _dialog(ws, mode=DialogMode.BOT_STRATEGY, current_id=duel.id, bot_name={"bot-2": "Darkmoor"}.get)
    dlg.page(DUEL).about.name_edit.setText("Boss Burn v2")
    return ws, dlg, duel


def test_saving_an_edit_to_a_shared_entry_asks_first_and_names_the_uses(qapp, roots):
    _ws_, dlg, _duel = _shared_fixture(roots)

    with patch.object(CombatStrategyDialog, "_confirm_shared_edits", return_value=True) as asked:
        dlg._on_commit()

    [uses] = asked.call_args.args
    assert [(u.name, u.bot_strategies) for u in uses] == [("Boss Burn v2", ("'Team' (Darkmoor)",))]


def test_going_back_writes_nothing_and_keeps_the_dialog_open(qapp, roots):
    ws, dlg, duel = _shared_fixture(roots)

    with (
        patch.object(CombatStrategyDialog, "_confirm_shared_edits", return_value=False),
        patch.object(CombatStrategyDialog, "accept") as accepted,
    ):
        dlg._on_commit()

    accepted.assert_not_called()
    assert ws.is_dirty
    assert [d.name for d in list_duel_strategies(*roots)] == ["Boss Burn"]


def test_saving_anyway_writes_the_edit(qapp, roots):
    _ws_, dlg, _duel = _shared_fixture(roots)

    with patch.object(CombatStrategyDialog, "_confirm_shared_edits", return_value=True):
        dlg._on_commit()

    assert [d.name for d in list_duel_strategies(*roots)] == ["Boss Burn v2"]


def test_an_edit_only_the_open_bot_strategy_uses_asks_nothing(qapp, roots):
    duel = DuelCombatStrategy(name="Boss Burn")
    save_duel_strategy(duel, *roots)
    ws = _ws(roots, _bcs(default_strategy_id=duel.id))
    dlg = _dialog(ws, mode=DialogMode.BOT_STRATEGY, current_id=duel.id)
    dlg.page(DUEL).about.name_edit.setText("Edited")

    with patch.object(CombatStrategyDialog, "_confirm_shared_edits") as asked:
        dlg._on_commit()

    asked.assert_not_called()


def test_the_library_view_asks_too(qapp, roots):
    duel = DuelCombatStrategy(name="Boss Burn")
    save_duel_strategy(duel, *roots)
    save_bot_strategy(BotCombatStrategy(name="Solo", bot_id="bot-1", default_strategy_id=duel.id), *roots)
    dlg = _dialog(_ws(roots, None, bot_id=None), mode=DialogMode.LIBRARY, current_id=duel.id)
    dlg.page(DUEL).about.name_edit.setText("Edited")

    with patch.object(CombatStrategyDialog, "_confirm_shared_edits", return_value=True) as asked:
        dlg._on_commit()

    asked.assert_called_once()


def test_the_bot_strategy_editor_returns_the_duel_selected_whatever_tab_is_showing(qapp, roots):
    ws, own, _library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.BOT_STRATEGY, current_id=own.id)
    dlg._tabs.setCurrentWidget(dlg.page(PLAYER))

    dlg._on_commit()

    assert dlg.selected_id() == own.id
