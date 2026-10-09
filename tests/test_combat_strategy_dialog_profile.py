"""Tests for CombatStrategyDialog's ways in, and the combat profile inside it (#118, #123).

What each way in shows, that Pick selects and edits nothing, and everything that makes the
difference between a library strategy and a profile one visible: the section, the pill, the
About line, Add to library, and the confirmation at Save.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from PyQt6.QtWidgets import QInputDialog, QMessageBox, QSplitter

from bartlebot.combat.profile_models import BotCombatStrategy
from bartlebot.combat.strategy_store import (
    StrategyKind,
    list_duel_strategies,
    load_bot_strategy,
    save_bot_strategy,
    save_deck_preset,
    save_duel_strategy,
    save_player_strategy,
)
from bartlebot.combat.strategy_tags import PROFILE_SOURCE
from bartlebot.combat.strategy_workset import StrategyWorkingSet
from bartlebot.content import store_roots
from bartlebot.gui.combat_strategies.assignment_card import strategy_menu_entries
from bartlebot.gui.combat_strategies.dialog import (
    CREATE_PROFILE_TEXT,
    FAVORITES_HEADING,
    LIBRARY_HEADING,
    NO_PROFILE_NOTE,
    PROFILE_EMPTY_NOTE,
    CombatStrategyDialog,
    DialogMode,
    move_confirmation,
    tabs_for,
)
from bartlebot.gui.widgets.action_button import ActionButton
from bartlebot.gui.widgets.tag_pill import ProfilePill
from bartlebot.settings_manager import SettingsManager
from bartlebot.shared.strategy_models import DeckPreset, DuelCombatStrategy, PlayerAssignment, PlayerCombatStrategy

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


def _ws(roots, profile: BotCombatStrategy | None = None, bot_id: str | None = "bot-1") -> StrategyWorkingSet:
    shipped, user = roots
    if profile is not None:
        save_bot_strategy(profile, shipped, user)
    return StrategyWorkingSet.from_store(
        root=shipped, user_root=user, profile_id=profile.id if profile else None, bot_id=bot_id
    )


def _profile(**kw) -> BotCombatStrategy:
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
        (DialogMode.PROFILE, DUEL, (DUEL, PLAYER)),
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
        (DialogMode.PROFILE, DUEL, [DUEL, PLAYER]),
        (DialogMode.PICK, DUEL, [DUEL]),
        (DialogMode.PICK, DECK, [DECK]),
    ],
)
def test_the_dialog_builds_exactly_those_tabs(qapp, roots, mode, kind, tabs):
    dlg = _dialog(_ws(roots, _profile()), mode=mode, kind=kind)

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


def test_the_library_view_and_the_profile_editor_can_edit(qapp, roots):
    for mode in (DialogMode.LIBRARY, DialogMode.PROFILE):
        dlg = _dialog(_ws(roots, _profile()), mode=mode)
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
# The profile section
# ---------------------------------------------------------------------------


def _with_both(roots):
    """A profile with one duel of its own, and one library duel."""
    own = DuelCombatStrategy(name="Just This Fight")
    library = DuelCombatStrategy(name="Shared Boss")
    save_duel_strategy(library, *roots)
    return _ws(roots, _profile(duel_strategies=[own])), own, library


def test_the_profile_section_comes_first_and_is_headed_with_the_profiles_name(qapp, roots):
    ws, own, library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.PROFILE)
    page = dlg.page(DUEL)

    assert page.section_headings() == ["Solo", LIBRARY_HEADING]
    assert page.ids_in_section("profile") == [own.id]
    assert page.ids_in_section("library") == [library.id]


def test_the_profile_section_goes_before_favorites(qapp, roots):
    ws, _own, library = _with_both(roots)
    SettingsManager.instance().toggle_favorite_strategy("duel", library.id)
    dlg = _dialog(ws, mode=DialogMode.PROFILE)
    page = dlg.page(DUEL)

    assert page.section_headings()[:2] == ["Solo", FAVORITES_HEADING]


def test_a_profile_entry_wears_a_profile_pill_and_no_star(qapp, roots):
    """The pill says "Profile"; the heading above it says which one, and the tooltip does too."""
    ws, own, library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.PROFILE)
    page = dlg.page(DUEL)

    card = page.card_for(own.id)
    assert card.source_text == "Profile"
    assert isinstance(card._source_pill, ProfilePill)
    assert "'Solo'" in card._source_pill.toolTip()
    assert card.star is None
    assert page.card_for(library.id).star is not None


def test_pick_shows_the_profile_section_too(qapp, roots):
    ws, own, _library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.PICK)
    page = dlg.page(DUEL)

    assert page.ids_in_section("profile") == [own.id]


def test_the_library_view_never_shows_a_profile(qapp, roots):
    ws, own, library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.LIBRARY)
    page = dlg.page(DUEL)

    assert page.visible_ids() == [library.id]
    assert page.section_headings() == []
    assert PROFILE_SOURCE not in page.filter_bar.tag_pickers["Source"].vocabulary.tags


def test_an_empty_profile_section_says_what_it_is_for(qapp, roots):
    dlg = _dialog(_ws(roots, _profile()), mode=DialogMode.PROFILE)
    page = dlg.page(DUEL)

    assert page.note_text() == PROFILE_EMPTY_NOTE
    assert page.section_headings()[0] == "Solo"


def test_the_empty_profile_note_wraps_when_the_column_narrows(qapp, roots):
    """Its row is refitted like a card's, or the sentence is cut off past the first line."""
    dlg = _dialog(_ws(roots, _profile()), mode=DialogMode.PROFILE)
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


def test_a_bot_with_no_profile_is_offered_one(qapp, roots):
    dlg = _dialog(_ws(roots, None), mode=DialogMode.PROFILE)
    page = dlg.page(DUEL)

    assert page.note_text() == NO_PROFILE_NOTE
    buttons = [b.text() for b in page._note_widget.findChildren(ActionButton)]
    assert buttons == [CREATE_PROFILE_TEXT]
    assert not page.new_button.isEnabled()


def test_pick_mentions_a_missing_profile_but_offers_nothing(qapp, roots):
    dlg = _dialog(_ws(roots, None), mode=DialogMode.PICK)
    page = dlg.page(DUEL)

    assert page.note_text() == NO_PROFILE_NOTE
    assert page._note_widget.findChildren(ActionButton) == []


def test_create_one_makes_a_profile_that_cancel_discards(qapp, roots):
    ws = _ws(roots, None)
    dlg = _dialog(ws, mode=DialogMode.PROFILE)

    with patch.object(QInputDialog, "getText", return_value=("Solo", True)):
        dlg._on_create_profile()

    page = dlg.page(DUEL)
    assert ws.profile.name == "Solo"
    assert page.section_headings()[0] == "Solo"
    assert page.note_text() == PROFILE_EMPTY_NOTE
    assert page.new_button.isEnabled()


def test_the_source_filter_finds_profile_entries(qapp, roots):
    ws, own, _library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.PROFILE)
    page = dlg.page(DUEL)

    page.filter_bar.tag_pickers["Source"].set_selection([PROFILE_SOURCE])
    page.filter_bar._on_selection_changed()

    assert page.visible_ids() == [own.id]


# ---------------------------------------------------------------------------
# Creating in the profile editor
# ---------------------------------------------------------------------------


def test_new_in_the_profile_editor_creates_in_the_profile(qapp, roots):
    ws = _ws(roots, _profile())
    dlg = _dialog(ws, mode=DialogMode.PROFILE)
    page = dlg.page(DUEL)

    page._on_new()

    assert ws.is_profile_entry(DUEL, page.selected_id())
    assert page.ids_in_section("profile") == [page.selected_id()]


def test_duplicating_a_library_entry_in_the_profile_editor_lands_in_the_profile(qapp, roots):
    ws, _own, library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.PROFILE, current_id=library.id)
    page = dlg.page(DUEL)

    page._on_duplicate()

    assert ws.is_profile_entry(DUEL, page.selected_id())
    assert page.selected_id() != library.id


def test_new_in_the_library_view_creates_in_the_library(qapp, roots):
    ws = _ws(roots, _profile())
    dlg = _dialog(ws, mode=DialogMode.LIBRARY)
    page = dlg.page(DUEL)

    page._on_new()

    assert not ws.is_profile_entry(DUEL, page.selected_id())


def test_save_as_player_strategy_follows_the_duel_it_came_from(qapp, roots):
    own = DuelCombatStrategy(name="Own", player_assignments=[PlayerAssignment(players=[1], dsl="pass")])
    ws = _ws(roots, _profile(duel_strategies=[own]))
    dlg = _dialog(ws, mode=DialogMode.PROFILE, current_id=own.id)
    card = dlg.page(DUEL).editor.cards[0]

    with patch.object(QInputDialog, "getText", return_value=("Kept", True)):
        dlg._on_promote_requested(card)

    pcs_id = card.assignment().player_combat_strategy_id
    assert ws.is_profile_entry(PLAYER, pcs_id)


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
    ws = _ws(roots, _profile(player_strategies=[own_pcs]))
    dlg = _dialog(ws, mode=DialogMode.PROFILE, current_id=library.id)
    page = dlg.page(DUEL)

    assert "Own P" not in _menu_names(page)
    assert "Library P" in _menu_names(page)


def test_a_profile_duels_menu_offers_the_profiles_first(qapp, roots):
    own_pcs = PlayerCombatStrategy(name="Own P")
    library_pcs = PlayerCombatStrategy(name="Library P")
    save_player_strategy(library_pcs, *roots)
    own = DuelCombatStrategy(name="Own", player_assignments=[PlayerAssignment(players=[1])])
    ws = _ws(roots, _profile(player_strategies=[own_pcs], duel_strategies=[own]))
    dlg = _dialog(ws, mode=DialogMode.PROFILE, current_id=own.id)
    page = dlg.page(DUEL)

    names = _menu_names(page)
    assert names.index("Own P") < names.index("Library P")


# ---------------------------------------------------------------------------
# The About pane, and Add to library
# ---------------------------------------------------------------------------


def test_a_profile_entry_says_where_it_lives_with_add_to_library_beside_it(qapp, roots):
    ws, own, _library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.PROFILE, current_id=own.id)
    page = dlg.page(DUEL)

    assert page.about.origin_text == "In profile 'Solo' only"
    assert not page.about.add_to_library_button.isHidden()
    assert page.delete_button.text() == "Delete"


def test_pick_says_where_it_lives_but_offers_no_move(qapp, roots):
    ws, own, _library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.PICK, current_id=own.id)
    page = dlg.page(DUEL)

    assert page.about.origin_text == "In profile 'Solo' only"
    assert page.about.add_to_library_button.isHidden()


def test_a_library_entry_offers_no_move(qapp, roots):
    ws, _own, library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.PROFILE, current_id=library.id)
    page = dlg.page(DUEL)

    assert page.about.add_to_library_button.isHidden()


def test_the_move_confirmation_uses_numerals_throughout():
    assert move_confirmation("Boss Burn", ["Frost Heal", "Blade Stack"]) == (
        "'Boss Burn' uses 2 player strategies from this profile: 'Frost Heal' and 'Blade Stack'. "
        "All 3 will be moved to the library."
    )


def _move_fixture(roots):
    frost = PlayerCombatStrategy(name="Frost Heal")
    boss = DuelCombatStrategy(
        name="Boss Burn", player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=frost.id)]
    )
    ws = _ws(roots, _profile(duel_strategies=[boss], player_strategies=[frost]))
    dlg = _dialog(ws, mode=DialogMode.PROFILE, current_id=boss.id)
    return ws, dlg, boss, frost


def test_add_to_library_names_what_moves_along_and_moves_it(qapp, roots):
    ws, dlg, boss, frost = _move_fixture(roots)
    page = dlg.page(DUEL)

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as asked:
        page.about.add_to_library_button.click()

    assert "'Frost Heal'" in asked.call_args.args[2]
    assert not ws.is_profile_entry(DUEL, boss.id)
    assert page.ids_in_section("library") == [boss.id]
    assert page.selected_id() == boss.id
    # The Player tab follows, since the player strategy moved too.
    assert dlg.page(PLAYER).ids_in_section("library") == [frost.id]


def test_declining_the_move_moves_nothing(qapp, roots):
    ws, dlg, boss, _frost = _move_fixture(roots)

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
        dlg.page(DUEL).about.add_to_library_button.click()

    assert ws.is_profile_entry(DUEL, boss.id)


def test_a_move_reaches_disk_at_save_and_not_before(qapp, roots):
    ws, dlg, boss, _frost = _move_fixture(roots)
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        dlg.page(DUEL).about.add_to_library_button.click()
    assert list_duel_strategies(*roots) == []

    dlg._on_commit()

    assert [d.id for d in list_duel_strategies(*roots)] == [boss.id]
    assert load_bot_strategy(ws.profile.id, *roots).duel_strategies == []


# ---------------------------------------------------------------------------
# The Save confirmation
# ---------------------------------------------------------------------------


def _shared_fixture(roots):
    duel = DuelCombatStrategy(name="Boss Burn")
    save_duel_strategy(duel, *roots)
    save_bot_strategy(BotCombatStrategy(name="Team", bot_id="bot-2", default_strategy_id=duel.id), *roots)
    ws = _ws(roots, _profile(default_strategy_id=duel.id))
    dlg = _dialog(ws, mode=DialogMode.PROFILE, current_id=duel.id, bot_name={"bot-2": "Darkmoor"}.get)
    dlg.page(DUEL).about.name_edit.setText("Boss Burn v2")
    return ws, dlg, duel


def test_saving_an_edit_to_a_shared_entry_asks_first_and_names_the_uses(qapp, roots):
    _ws_, dlg, _duel = _shared_fixture(roots)

    with patch.object(CombatStrategyDialog, "_confirm_shared_edits", return_value=True) as asked:
        dlg._on_commit()

    [uses] = asked.call_args.args
    assert [(u.name, u.profiles) for u in uses] == [("Boss Burn v2", ("'Team' (Darkmoor)",))]


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


def test_an_edit_only_the_open_profile_uses_asks_nothing(qapp, roots):
    duel = DuelCombatStrategy(name="Boss Burn")
    save_duel_strategy(duel, *roots)
    ws = _ws(roots, _profile(default_strategy_id=duel.id))
    dlg = _dialog(ws, mode=DialogMode.PROFILE, current_id=duel.id)
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


def test_the_profile_editor_returns_the_duel_selected_whatever_tab_is_showing(qapp, roots):
    ws, own, _library = _with_both(roots)
    dlg = _dialog(ws, mode=DialogMode.PROFILE, current_id=own.id)
    dlg._tabs.setCurrentWidget(dlg.page(PLAYER))

    dlg._on_commit()

    assert dlg.selected_id() == own.id
