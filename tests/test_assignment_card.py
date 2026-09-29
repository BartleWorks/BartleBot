"""Tests for ``AssignmentCard`` — the card that replaced the assignment form row.

The row's controls did not go away; they went behind the chevron. So what these tests are
mostly about is that the fold is honest: everything is still reachable, everything still
writes through, and the one thing the fold must *not* hide — a DSL that does not parse —
is on the face whether or not the card is open.

The rest is the card's face: four possible titles, one per answer the assignment can
hold, including the one for a strategy that has since been deleted.
"""

import pytest

from bartlebot.bot.strategy_models import PlayerAssignment, PlayerCombatStrategy
from bartlebot.gui.combat_strategies.assignment_card import (
    CHANGE_VERB,
    CUSTOM_CHOICE,
    CUSTOM_DATA,
    DEFAULT_CHOICE,
    DEFAULT_DATA,
    EDIT_VERB,
    HIDE_DETAILS,
    MISSING_TITLE,
    REMOVE_VERB,
    SHOW_DETAILS,
    AssignmentCard,
    dsl_preview,
    strategy_menu_entries,
)
from tests.picker_helpers import pick

_BROKEN_DSL = "any<"


def _card(assignment: PlayerAssignment, pcs_list=()) -> AssignmentCard:
    return AssignmentCard(assignment, list(pcs_list))


# ---------------------------------------------------------------------------
# What the menu offers — pure, so no QApplication is needed
# ---------------------------------------------------------------------------


def test_the_menu_offers_the_default_first_and_custom_last():
    """The order is the point: naming a strategy is the common answer and authoring DSL
    is the rare one, so the rare one is at the far end."""
    entries = strategy_menu_entries([])

    assert entries == [(DEFAULT_CHOICE, DEFAULT_DATA), (CUSTOM_CHOICE, CUSTOM_DATA)]


def test_every_player_strategy_sits_between_them():
    pcs = PlayerCombatStrategy(name="Blade")

    labels = [label for label, _data in strategy_menu_entries([pcs])]

    assert labels == [DEFAULT_CHOICE, "Blade", CUSTOM_CHOICE]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ""),
        ("any<aoe>", "any<aoe>"),
        ("any<aoe>\n  | pass", "any<aoe> | pass"),  # collapsed onto one line
    ],
)
def test_dsl_preview_collapses_whitespace(text, expected):
    assert dsl_preview(text) == expected


def test_dsl_preview_truncates_rather_than_widening_the_card():
    assert dsl_preview("x" * 400).endswith("…")
    assert len(dsl_preview("x" * 400)) == 120


# ---------------------------------------------------------------------------
# The face: one title per answer
# ---------------------------------------------------------------------------


def test_a_linked_card_is_titled_with_the_strategy_it_names(qapp):
    pcs = PlayerCombatStrategy(name="Ice Blade", description="Blades then traps", dsl="any<blade>")
    card = _card(PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id), [pcs])

    assert card.title_text() == "Ice Blade"
    assert card._desc_lbl.text() == "Blades then traps"
    assert "any<blade>" in card._dsl_lbl.text()


def test_a_long_dsl_token_does_not_widen_the_card(qapp):
    """A DSL token has no spaces to wrap at; one wider than the line was the card's minimum width (#118)."""
    # Both fail to parse, so both carry the error marker and only the token's length differs.
    short = _card(PlayerAssignment(players=[1], dsl="any<zz>"))
    long = _card(PlayerAssignment(players=[1], dsl="any<" + "z" * 100 + ">"))

    assert long.minimumSizeHint().width() == short.minimumSizeHint().width()
    assert "z" * 100 in long._dsl_lbl.text()  # still whole when read back


def test_a_default_card_says_so_rather_than_being_blank(qapp):
    card = _card(PlayerAssignment(players=[1]))

    assert card.title_text() == DEFAULT_CHOICE
    assert card._desc_lbl.text()  # names what the built-in default will do


def test_a_custom_card_is_titled_custom_and_previews_its_own_dsl(qapp):
    card = _card(PlayerAssignment(players=[1], dsl="any<aoe>"))

    assert card.title_text() == CUSTOM_CHOICE
    assert "any<aoe>" in card._dsl_lbl.text()


def test_a_link_to_a_deleted_strategy_is_named_rather_than_silently_reset(qapp):
    """Resetting it would look like it was always configured that way, and Save would
    then make that true."""
    assignment = PlayerAssignment(players=[1], player_combat_strategy_id="gone")

    card = _card(assignment)

    assert card.title_text() == MISSING_TITLE
    assert assignment.player_combat_strategy_id == "gone"


def test_the_note_rides_on_the_face_beside_the_dsl_preview(qapp):
    """It is the assignment's own words about this fight — exactly what someone scanning
    the list is looking for, and burying it in the fold hid it from all but its author."""
    card = _card(PlayerAssignment(players=[1], dsl="any<aoe>", note="Kites the adds"))

    assert "Kites the adds" in card._dsl_lbl.text()


# ---------------------------------------------------------------------------
# The fold
# ---------------------------------------------------------------------------


def test_a_card_starts_folded(qapp):
    card = _card(PlayerAssignment(players=[1]))

    assert not card.is_expanded()
    assert card._details.isHidden()


def test_the_chevron_unfolds_the_details(qapp):
    card = _card(PlayerAssignment(players=[1]))

    card.chevron.click()

    assert card.is_expanded()
    assert not card._details.isHidden()


def test_the_chevron_folds_it_back(qapp):
    card = _card(PlayerAssignment(players=[1]))
    card.toggle_expanded()

    card.chevron.click()

    assert not card.is_expanded()


def test_the_chevron_points_the_way_the_fold_will_go(qapp):
    card = _card(PlayerAssignment(players=[1]))

    assert card.chevron._icon_name == "chevron_down"
    assert card.chevron.toolTip() == SHOW_DETAILS

    card.chevron.click()

    assert card.chevron._icon_name == "chevron_up"
    assert card.chevron.toolTip() == HIDE_DETAILS


def test_the_chevron_sits_at_the_right_edge_of_the_header(qapp):
    """The standard disclosure placement, and the one spot that does not move as the card grows."""
    card = _card(PlayerAssignment(players=[1]))
    header = card.frame.layout().itemAt(0).layout()

    assert header.itemAt(header.count() - 1).widget() is card.chevron


def _menu_labels(card: AssignmentCard) -> list[str]:
    return [action.text() for action in card._build_verbs_menu().actions()]


def test_the_fold_is_still_in_the_right_click_menu(qapp):
    card = _card(PlayerAssignment(players=[1]))

    assert SHOW_DETAILS in _menu_labels(card)
    card.toggle_expanded()
    assert HIDE_DETAILS in _menu_labels(card)


# ---------------------------------------------------------------------------
# The pencil: edit the linked Player Strategy
# ---------------------------------------------------------------------------


def test_the_pencil_asks_to_open_the_linked_strategy(qapp):
    pcs = PlayerCombatStrategy(name="Blade", dsl="any<blade>")
    card = _card(PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id), [pcs])
    seen = []
    card.open_pcs_requested.connect(seen.append)

    card.action_button(EDIT_VERB).click()

    assert seen == [pcs.id]
    assert not card.is_expanded()  # it navigates; it no longer folds


@pytest.mark.parametrize(
    "assignment",
    [
        PlayerAssignment(players=[1]),
        PlayerAssignment(players=[1], dsl="any<aoe>"),
        PlayerAssignment(players=[1], player_combat_strategy_id="gone"),
    ],
    ids=["default", "custom", "missing"],
)
def test_the_pencil_is_withdrawn_when_there_is_no_strategy_to_open(qapp, assignment):
    card = _card(assignment)

    assert card.action_button(EDIT_VERB).isHidden()
    assert EDIT_VERB not in _menu_labels(card)


def test_the_pencil_follows_the_strategy_the_card_is_given(qapp):
    pcs = PlayerCombatStrategy(name="Blade")
    card = _card(PlayerAssignment(players=[1]), [pcs])

    card.choose_strategy(pcs.id)
    assert not card.action_button(EDIT_VERB).isHidden()
    assert EDIT_VERB in _menu_labels(card)

    card.choose_strategy(DEFAULT_DATA)
    assert card.action_button(EDIT_VERB).isHidden()


def test_choosing_custom_dsl_unfolds_the_card_on_its_own(qapp):
    """Custom DSL is the one answer with nowhere to be typed on the face, so choosing it
    and then having to find the chevron would be two gestures for one intent."""
    card = _card(PlayerAssignment(players=[1]))

    card.choose_strategy(CUSTOM_DATA)

    assert card.is_expanded()
    assert not card._dsl_edit.isHidden()


def test_choosing_a_named_strategy_does_not_unfold_anything(qapp):
    pcs = PlayerCombatStrategy(name="Blade", dsl="any<blade>")
    card = _card(PlayerAssignment(players=[1]), [pcs])

    card.choose_strategy(pcs.id)

    assert not card.is_expanded()
    assert card._dsl_edit.isHidden()


def test_the_school_and_note_in_the_fold_still_write_through(qapp):
    assignment = PlayerAssignment(players=[1], dsl="any<aoe>")
    card = _card(assignment)
    card.toggle_expanded()

    pick(card.school_picker, "Fire")
    card._note_edit.setText("Passes to bank pips")

    assert assignment.schools
    assert assignment.note == "Passes to bank pips"


def test_a_folded_card_still_loads_its_school_and_note(qapp):
    """Nothing is deferred to first expand: the fold hides built widgets, it does not
    postpone building them."""
    card = _card(PlayerAssignment(players=[1], dsl="x", schools=["Storm"], note="Kites adds"))

    assert card.school_picker.value() == ["Storm"]
    assert card._note_edit.text() == "Kites adds"


# ---------------------------------------------------------------------------
# A broken DSL is never folded away
# ---------------------------------------------------------------------------


def test_a_folded_card_with_broken_dsl_wears_the_warning_mark(qapp):
    card = _card(PlayerAssignment(players=[1], dsl=_BROKEN_DSL))

    assert not card.is_expanded()
    assert "⚠" in card.title_text()
    assert card.title_text().startswith(CUSTOM_CHOICE)


def test_the_warning_names_the_problem_in_the_title_s_tooltip(qapp):
    card = _card(PlayerAssignment(players=[1], dsl=_BROKEN_DSL))

    assert card._title_lbl.toolTip().startswith("Does not parse:")


def test_fixing_the_dsl_takes_the_warning_mark_back_off(qapp):
    card = _card(PlayerAssignment(players=[1], dsl=_BROKEN_DSL))

    card._dsl_edit.setPlainText("any<aoe>")
    card._validate_timer.stop()
    card._update_dsl_validation()

    assert "⚠" not in card.title_text()
    assert not card._title_lbl.toolTip()


def test_a_card_naming_a_broken_strategy_does_not_mirror_its_error(qapp):
    """A problem with a *shared* strategy belongs on that strategy's own card."""
    pcs = PlayerCombatStrategy(name="Broken", dsl=_BROKEN_DSL)
    card = _card(PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id), [pcs])

    assert card.title_text() == "Broken"
    assert card._dsl_error_lbl.isHidden()


# ---------------------------------------------------------------------------
# Verbs and locking
# ---------------------------------------------------------------------------


def test_the_cross_asks_to_be_removed_rather_than_removing_itself(qapp):
    """Membership of the list is the editor's, not the card's."""
    card = _card(PlayerAssignment(players=[1]))
    seen = []
    card.remove_requested.connect(seen.append)

    card.action_button(REMOVE_VERB).click()

    assert seen == [card]


def test_locking_disables_the_change_and_remove_verbs(qapp):
    card = _card(PlayerAssignment(players=[1]))

    card.set_editable(False)

    assert not card.action_button(CHANGE_VERB).isEnabled()
    assert not card.action_button(REMOVE_VERB).isEnabled()


def test_locking_leaves_the_chevron_live_so_the_details_stay_readable(qapp):
    """Everything the fold reveals is read-only by then, and watching a bot fight is
    exactly when someone wants to read the note and the DSL."""
    card = _card(PlayerAssignment(players=[1], dsl="any<aoe>"))

    card.set_editable(False)

    assert card.chevron.isEnabled()
    assert card._dsl_edit.isReadOnly()


def test_locking_leaves_the_pencil_live_since_it_only_navigates(qapp):
    pcs = PlayerCombatStrategy(name="Blade")
    card = _card(PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id), [pcs])

    card.set_editable(False)

    assert card.action_button(EDIT_VERB).isEnabled()


def test_a_card_with_nowhere_to_open_its_strategy_greys_the_pencil(qapp):
    """Pick has no Player tab, so the pencil there would be a live button that does nothing."""
    pcs = PlayerCombatStrategy(name="Blade")
    card = _card(PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id), [pcs])

    card.set_can_open_pcs(False)

    assert not card.action_button(EDIT_VERB).isEnabled()
    assert EDIT_VERB not in _menu_labels(card)


def test_a_locked_card_offers_nothing_editing_by_right_click(qapp):
    """The menu does not consult the buttons' enabled state, so it has to leave them out."""
    card = _card(PlayerAssignment(players=[1]))

    card.set_editable(False)

    assert CHANGE_VERB not in _menu_labels(card)
    assert REMOVE_VERB not in _menu_labels(card)


def test_a_locked_card_refuses_to_change_its_strategy_by_menu(qapp):
    assignment = PlayerAssignment(players=[1])
    card = _card(assignment)
    card.set_editable(False)

    card._open_strategy_menu()  # the click the disabled button would have made

    assert assignment.player_combat_strategy_id is None
    assert not card.is_custom()
