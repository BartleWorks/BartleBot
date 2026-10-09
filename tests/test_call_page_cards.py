"""The subgraph node's page as cards (#142): one per input group, the ungrouped inputs and the roles.

Drives a real ``BotCanvas`` over the synthetic "sync players" fixture from
``test_subgraphs``, with one member promoted whole so its card carries Record.
"""

from __future__ import annotations

import pytest

from bartlebot.bot.schema import SubgraphPanel
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary
from bartlebot.content.subgraphs.subgraphs import compose_bot
from bartlebot.gui.widgets.record_button import RecordButton
from bartlebot.options import param_cards
from bartlebot.options.option_groups import summary_text
from bartlebot.ui_state import UiState
from tests.test_subgraphs import REGION, SYNC_ID, linked_bot, sync_players_definition


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    from PyQt6.QtWidgets import QMessageBox  # noqa: PLC0415

    for name in ("question", "warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.StandardButton.No)


def _definition():
    """Sync players with its first hop recordable and promoted as "First hop"."""
    defn = sync_players_definition()
    tp_a = next(s for s in defn.states if s.id == "tp_a")
    tp_a.node_type = "teleport_to_coordinates"
    tp_a.label = "First hop"
    tp_a.params = {}
    defn.panels = [SubgraphPanel(id="node_tp_a", kind="node", label="First hop", member="tp_a")]
    return defn


@pytest.fixture
def canvas(qapp, tmp_path):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    library = SubgraphLibrary(tmp_path / "library")
    library.save(_definition())
    c = BotCanvas()
    c.set_subgraph_library(library)
    bot, report = compose_bot(linked_bot(library.get(SYNC_ID), library), library)
    c.setup(bot)
    c.set_compose_report(report)
    c.enter_edit_mode()
    yield c
    c.prepare_for_shutdown()


def _page(canvas):
    canvas.scene.clearSelection()
    canvas.scene.node_items[REGION].setSelected(True)
    return canvas.param_panel.call_page


def test_the_page_is_a_card_per_group_and_one_for_the_roles(canvas):
    page = _page(canvas)

    assert page.card(param_cards.INPUTS).title == "Inputs"
    assert page.card("node_tp_a").title == "First hop"
    assert page.card(param_cards.ROLE).title == "Role"


def test_a_cards_band_counts_what_it_still_needs(canvas):
    page = _page(canvas)

    form = page.form("node_tp_a")
    assert page.to_fill("node_tp_a") == form.to_fill() > 0
    assert page.to_fill_label("node_tp_a").text() == f"{form.to_fill()} to fill"
    assert not page.to_fill_label("node_tp_a").red, "gray until a field has been left empty"


def test_an_incomplete_card_starts_open_and_a_complete_one_folded(canvas):
    page = _page(canvas)

    for group in (param_cards.INPUTS, "node_tp_a"):
        assert page.card(group).is_collapsed == (page.to_fill(group) == 0)


def test_the_role_card_opens_on_a_node_that_holds_a_role(canvas):
    page = _page(canvas)  # the fixture's initial and recovery states are inside the instance

    role = page.card(param_cards.ROLE)
    assert not role.is_collapsed
    assert summary_text(role.summary_parts) == "Initial · Recovery"


def test_a_fold_the_reader_makes_is_kept_per_definition(canvas):
    page = _page(canvas)
    card = page.card("node_tp_a")
    was = card.is_collapsed

    page.folds.set_group_collapsed("node_tp_a", not was)

    stored = UiState().folds(param_cards.call_page_surface(SYNC_ID), {"node_tp_a": was})
    assert stored == {"node_tp_a": not was}
    assert _page(canvas).card("node_tp_a").is_collapsed is not was, "and the next page reads it back"


def test_a_recordable_nodes_card_carries_record_on_its_band(canvas):
    page = _page(canvas)
    card = page.card("node_tp_a")

    record = card.header.findChildren(RecordButton)
    assert len(record) == 1
    assert page.record_status("node_tp_a").text().startswith("Not recorded. Record fills ")
    assert summary_text(card.summary_parts) == "Not recorded"


def test_recording_opens_a_folded_card_without_remembering_it(canvas, monkeypatch):
    page = _page(canvas)
    card = page.card("node_tp_a")
    page.folds.set_group_collapsed("node_tp_a", True)
    started = []
    monkeypatch.setattr("bartlebot.gui.canvas.call_page.recording.run_recording", lambda *a, **k: started.append(a))
    monkeypatch.setattr("bartlebot.gui.canvas.call_page.asyncio.ensure_future", lambda coro: coro)

    page._get_clients = lambda: [object()]
    page._record_from("node_tp_a", "teleport_to_coordinates", card.header.findChildren(RecordButton)[0], 0)

    assert not card.is_collapsed
    stored = UiState().folds(param_cards.call_page_surface(SYNC_ID), {"node_tp_a": False})
    assert stored == {"node_tp_a": True}, "the reader's fold is still the folded one"


def test_the_name_appears_once(canvas):
    page = _page(canvas)

    assert page.label_edit.text() == "Sync players"
    assert page.meta_text.startswith("Subgraph · ") and "Sync players" not in page.meta_text


def test_a_relabelled_node_names_its_definition_on_the_line_under_the_title(canvas):
    page = _page(canvas)
    page.label_edit.setText("Regroup")

    page = _page(canvas)

    assert page.meta_text.startswith("Subgraph · Sync players")


def test_showing_the_page_never_shows_a_parentless_widget(canvas, parentless_shows):
    _page(canvas)
    canvas.scene.clearSelection()
    _page(canvas)

    assert parentless_shows == []


def test_an_emptied_title_shows_the_definitions_name_and_takes_it_back_when_left(canvas):
    page = _page(canvas)
    page.label_edit.setText("Regroup")
    page.label_edit.setText("")

    assert page.label_edit.placeholderText() == "Sync players"
    page.label_edit.editingFinished.emit()
    assert page.label_edit.text() == "Sync players"
    assert next(r for r in canvas.config.regions if r.id == REGION).label == "Sync players"
