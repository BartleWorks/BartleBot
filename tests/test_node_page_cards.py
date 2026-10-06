"""The node page as cards (#142): Parameters, Connections, Notes and Role, the page scrolling as one.

Connections is Returns merged in: each return with where it goes, including an
unrouted FAILURE's route to the recovery state, which the canvas never draws.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QScrollArea

from bartlebot import param_cards
from bartlebot.bot.schema import StateModel, TransitionModel
from bartlebot.gui.canvas.param_panel import _ParamPanel
from bartlebot.gui.theme import THEME
from bartlebot.option_groups import summary_text
from bartlebot.ui_state import UiState


def _t(src, dest, when=None):
    return TransitionModel(trigger=f"{src}_{dest}_{when}", source=src, dest=dest, when=when)


def _panel(qapp, state=None, *, initial="hub", recovery="hub", states=None, transitions=None) -> _ParamPanel:
    state = state or StateModel(id="enter", node_type="enter_dungeon", label="Enter Mount Olympus")
    others = [
        StateModel(id="sync", node_type="sync_in_zone", label="Sync players"),
        StateModel(id="hub", node_type="teleport_to_world_hub", label="Teleport to world hub"),
    ]
    panel = _ParamPanel()
    panel.show_state(
        state,
        states or [state, *others],
        transitions if transitions is not None else [_t("enter", "sync", "SUCCESS"), _t("hub", "enter", "SUCCESS")],
        initial,
        recovery,
    )
    return panel


def test_the_page_is_four_cards_in_order_and_scrolls_as_one(qapp):
    panel = _panel(qapp)

    assert list(panel.node_cards) == [param_cards.PARAMS, param_cards.CONNECTIONS, param_cards.NOTES, param_cards.ROLE]
    assert isinstance(panel._stack.currentWidget(), QScrollArea)
    assert [c.title for c in panel.node_cards.values()] == ["Parameters", "Connections", "Notes", "Role"]


def test_parameters_and_connections_start_open_and_the_rest_folded(qapp):
    cards = _panel(qapp).node_cards

    assert [cards[g].is_collapsed for g in param_cards.NODE_PAGE_GROUPS] == [False, False, True, True]


def test_role_opens_on_a_node_that_holds_a_role(qapp):
    cards = _panel(qapp, initial="enter").node_cards

    assert not cards[param_cards.ROLE].is_collapsed
    assert summary_text(cards[param_cards.ROLE].summary_parts) == "Initial · not recovery"


def test_a_fold_is_the_readers_and_the_same_on_every_node(qapp):
    panel = _panel(qapp)
    panel.node_folds.set_group_collapsed(param_cards.CONNECTIONS, True)

    assert UiState().folds(param_cards.NODE_PAGE_SURFACE, {param_cards.CONNECTIONS: False}) == {
        param_cards.CONNECTIONS: True
    }
    other = StateModel(id="sync", node_type="sync_in_zone")
    panel.show_state(other, [other], [], None, None)
    assert panel.node_cards[param_cards.CONNECTIONS].is_collapsed


def test_record_is_on_the_parameters_band(qapp):
    panel = _panel(qapp)
    card = panel.node_cards[param_cards.PARAMS]

    assert panel._record_btn.parentWidget() is card.header
    assert panel._record_btn.isVisibleTo(panel)
    assert panel._record_status_lbl.text().startswith("Not recorded. Record fills ")
    assert summary_text(card.summary_parts) == "Not recorded"


def test_a_node_record_cannot_fill_has_no_record(qapp):
    panel = _panel(qapp, StateModel(id="sync", node_type="sync_in_zone"))

    assert not panel._record_btn.isVisibleTo(panel)
    assert not panel._record_status_lbl.isVisibleTo(panel)


def test_the_parameters_band_counts_what_is_left_and_turns_red_after_a_refused_save(qapp):
    panel = _panel(qapp)
    count = panel.params_to_fill

    assert count.text() == f"{panel.param_form.to_fill()} to fill"
    assert not count.red

    panel.show_required_errors()

    assert count.red


def test_an_unrouted_failure_is_listed_with_its_route_to_recovery(qapp):
    panel = _panel(qapp)
    card = panel.node_cards[param_cards.CONNECTIONS]

    assert summary_text(card.summary_parts) == "SUCCESS → Sync players · FAILURE → recovery"
    texts = [lbl.text() for lbl in panel._conn_body.findChildren(type(panel._idle_lbl))]
    assert "recovery · not drawn" in texts
    assert "Teleport to world hub" in texts


def test_each_returns_description_is_behind_its_rows_question_mark(qapp):
    panel = _panel(qapp)

    descriptions = [lbl.text() for lbl in panel._conn_body.findChildren(type(panel._idle_lbl)) if lbl.wordWrap()]
    assert "All players are now in dungeon_zone." in descriptions


def test_notes_fold_to_their_first_line(qapp):
    state = StateModel(id="enter", node_type="enter_dungeon", notes="Team-Up stays off here.\nThe queue stalls.")
    panel = _panel(qapp, state)

    assert summary_text(panel.node_cards[param_cards.NOTES].summary_parts) == "Team-Up stays off here."


def test_the_wiki_import_sits_at_the_end_of_a_fights_wiki_page_row(qapp, monkeypatch):
    opened = []
    monkeypatch.setattr(_ParamPanel, "_on_wiki_import", lambda self: opened.append(1))
    fight = StateModel(id="fight", node_type="handle_combat")
    panel = _panel(qapp, fight, states=[fight], transitions=[])

    panel.wiki_import_button.click()

    assert opened == [1]
    assert _panel(qapp).wiki_import_button is None


def test_showing_a_node_never_shows_a_parentless_widget(qapp, parentless_shows):
    panel = _panel(qapp)
    fight = StateModel(id="fight", node_type="handle_combat", notes="x")
    panel.show_state(fight, [fight], [], "fight", None)

    assert parentless_shows == []


def test_an_unlabeled_nodes_title_field_is_outlined_without_hover_or_focus(qapp):
    """#156: the outline was hover- and focus-only, so an empty title drew nothing to click."""
    panel = _panel(qapp, StateModel(id="enter", node_type="enter_dungeon"))
    edit = panel.node_label_edit
    assert edit.text() == ""
    base_rule = edit.styleSheet().split("}")[0]
    assert f"border: 1px solid {THEME.border}" in base_rule
    assert "transparent" not in edit.styleSheet()
