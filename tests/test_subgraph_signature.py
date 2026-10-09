"""The signature warning on saving a subgraph definition, and the Subgraphs folder's "Official updated" (#169).

Built on the synthetic "sync players" fixture from ``test_subgraphs`` (inputs World and
Hub; Entries Start and Hub; Exits Done and Early) and the controller harness from
``test_subgraph_definition_document``. Nothing here is a real character.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMessageBox

from bartlebot.bot.schema import SubgraphDefinition, SubgraphInput, SubgraphInputBinding, SubgraphPanel
from bartlebot.content.subgraphs.subgraph_signature import signature_changes, warning_text
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary, bots_linking
from tests.test_subgraph_definition_document import _canvas, _show_subgraphs, _subgraph_cards
from tests.test_subgraph_integration import ControllerHarness
from tests.test_subgraphs import REGION, SYNC_ID, linked_bot, sync_players_definition


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """Answer any message box a test did not stub, so a missed prompt fails fast instead of blocking the run."""
    for name in ("question", "warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.StandardButton.No)


@pytest.fixture
def library(tmp_path) -> SubgraphLibrary:
    lib = SubgraphLibrary(tmp_path / "library")
    lib.save(sync_players_definition())
    return lib


@pytest.fixture
def harness(qapp, library, tmp_path):
    """A bot linking the fixture definition, open in Edit mode, as in ``test_subgraph_definition_document``."""
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415
    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415

    (tmp_path / "bots").mkdir()
    SettingsManager.instance().set("canvas", "bot_library_path", value=str(tmp_path / "bots"))
    canvas = BotCanvas()
    canvas.set_subgraph_library(library)
    h = ControllerHarness(canvas, library)
    canvas.open_definition_requested.connect(h._open_definition)
    path = tmp_path / "bots" / "linked.json"
    linked_bot(library.get(SYNC_ID), library).save(path)
    h._open_bot(str(path))
    canvas.enter_edit_mode()
    yield h
    canvas.prepare_for_shutdown()


def _changed(**update) -> SubgraphDefinition:
    return sync_players_definition().model_copy(update=update, deep=True)


def _without_port(port: str) -> SubgraphDefinition:
    defn = sync_players_definition()
    defn.states = [s for s in defn.states if s.id != port]
    defn.transitions = [t for t in defn.transitions if port not in (t.source, t.dest)]
    return SubgraphDefinition.model_validate(defn.model_dump())


def _relabelled(state_id: str, label: str) -> SubgraphDefinition:
    defn = sync_players_definition()
    next(s for s in defn.states if s.id == state_id).label = label
    return defn


def _kinds(new: SubgraphDefinition, old: SubgraphDefinition | None = None) -> list[str]:
    return [c.kind for c in signature_changes(old or sync_players_definition(), new)]


# ---------------------------------------------------------------------------
# What warns
# ---------------------------------------------------------------------------


def test_removing_an_input_warns():
    old = sync_players_definition()
    changes = signature_changes(old, _changed(inputs=old.inputs[1:]))
    assert [c.text for c in changes] == ["removes the input 'World'"]


def test_renaming_an_input_warns_as_a_rename():
    old = sync_players_definition()
    renamed = old.inputs[0].model_copy(update={"name": "realm", "label": "Realm"})
    changes = signature_changes(old, _changed(inputs=[renamed, old.inputs[1]]))
    assert [c.text for c in changes] == ["renames the input 'World' to 'Realm'"]


def test_adding_an_input_with_no_default_warns():
    old = sync_players_definition()
    extra = SubgraphInput(
        name="wait", label="Wait", type="float", bindings=[SubgraphInputBinding(state="wisps", param="timeout")]
    )
    changes = signature_changes(old, _changed(inputs=[*old.inputs, extra]))
    assert [c.text for c in changes] == ["adds the input 'Wait' with no default"]


def test_no_longer_exposing_a_node_warns():
    exposed = _changed(panels=[SubgraphPanel(id="p", kind="node", member="wisps")])
    assert _kinds(sync_players_definition(), old=exposed) == ["input_removed"]


def test_removing_a_port_warns():
    assert [c.text for c in signature_changes(sync_players_definition(), _without_port("exit_early"))] == [
        "removes the Exit 'Early'"
    ]
    assert _kinds(_without_port("entry_hub")) == ["port_removed"]


def test_renaming_a_port_warns():
    changes = signature_changes(sync_players_definition(), _relabelled("exit_done", "Finished"))
    assert [c.text for c in changes] == ["renames the Exit 'Done' to 'Finished'"]


# ---------------------------------------------------------------------------
# What does not
# ---------------------------------------------------------------------------


def _with_default_input() -> SubgraphDefinition:
    old = sync_players_definition()
    extra = SubgraphInput(
        name="wait", type="float", default=30, bindings=[SubgraphInputBinding(state="wisps", param="timeout")]
    )
    return _changed(inputs=[*old.inputs, extra])


def _relabelled_input() -> SubgraphDefinition:
    old = sync_players_definition()
    return _changed(inputs=[old.inputs[0].model_copy(update={"label": "Realm"}), old.inputs[1]])


def _moved_node() -> SubgraphDefinition:
    defn = sync_players_definition()
    next(s for s in defn.states if s.id == "wisps").position = {"x": 1.0, "y": 2.0}
    return defn


def _member_param() -> SubgraphDefinition:
    defn = sync_players_definition()
    next(s for s in defn.states if s.id == "wisps").params["timeout"] = 99
    return defn


def _member_relabelled() -> SubgraphDefinition:
    return _relabelled("wisps", "Collect")


@pytest.mark.parametrize(
    "new",
    [
        _with_default_input,
        _relabelled_input,
        _moved_node,
        _member_param,
        _member_relabelled,
        lambda: _changed(name="Regroup", description="Everyone in one place."),
        lambda: _changed(version=7),
        lambda: _changed(panels=[SubgraphPanel(id="p", kind="node", member="wisps")]),
    ],
    ids=[
        "an input with a default",
        "an input's label",
        "a moved node",
        "a member's param",
        "a member's label",
        "the name and description",
        "the version",
        "exposing a node",
    ],
)
def test_no_other_edit_warns(new):
    assert signature_changes(sync_players_definition(), new()) == []


def test_the_warning_lists_every_change_and_names_who_uses_it():
    changes = signature_changes(sync_players_definition(), _relabelled("exit_done", "Finished"))
    users = [f"Bot {i}" for i in range(10)]
    text = warning_text("Sync players", changes, users)
    assert "Saving renames the Exit 'Done' to 'Finished'." in text
    assert "Bot 0, Bot 1" in text and "Bot 7 and 2 more" in text
    assert "Bot 8" not in text


# ---------------------------------------------------------------------------
# Saving a definition
# ---------------------------------------------------------------------------


def test_bots_linking_names_each_bot_once(tmp_path, library):
    linked_bot(library.get(SYNC_ID), library).save(tmp_path / "a.json")
    linked_bot(library.get(SYNC_ID), library).model_copy(update={"id": "b", "name": "Another"}).save(
        tmp_path / "w" / "b.json"
    )
    (tmp_path / "w").mkdir(exist_ok=True)
    (tmp_path / "broken.json").write_text("{nope", encoding="utf-8")
    assert bots_linking(SYNC_ID, tmp_path) == ["Another", "Linked"]
    assert bots_linking("other", tmp_path) == []


def _rename_exit_in_the_open_document(harness) -> None:
    canvas = _canvas(harness)
    canvas.open_definition_of(REGION)
    next(s for s in canvas.config.states if s.id == "exit_done").label = "Finished"
    canvas.config_changed.emit()


def test_a_signature_change_warns_naming_the_bots_and_saves_on_yes(harness, library, monkeypatch):
    asked = []

    def answer(*a, **k):
        asked.append(a[2])
        return QMessageBox.StandardButton.Save

    monkeypatch.setattr(QMessageBox, "warning", answer)
    _rename_exit_in_the_open_document(harness)
    assert harness._save_definition()
    assert len(asked) == 1
    assert "renames the Exit 'Done' to 'Finished'" in asked[0] and "used by Linked" in asked[0]
    assert library.get(SYNC_ID).version == 2


def test_cancelling_the_warning_writes_nothing(harness, library, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    _rename_exit_in_the_open_document(harness)
    assert not harness._save_definition()
    assert library.get(SYNC_ID).version == 1
    assert harness._has_unsaved_changes


def test_an_edit_that_keeps_the_signature_saves_without_a_warning(harness, library, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))
    canvas = _canvas(harness)
    canvas.open_definition_of(REGION)
    next(s for s in canvas.config.states if s.id == "wisps").params["timeout"] = 45
    canvas.config_changed.emit()
    assert harness._save_definition()
    assert not warned


def test_nothing_linking_it_means_nothing_to_warn_about(harness, library, monkeypatch, tmp_path):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))
    _rename_exit_in_the_open_document(harness)
    (tmp_path / "bots" / "linked.json").unlink()
    assert harness._save_definition()
    assert not warned


# ---------------------------------------------------------------------------
# The Subgraphs folder: "Official updated", with Revert beside it
# ---------------------------------------------------------------------------


def _panel_over(shipped, user):
    from bartlebot.gui.canvas.bot_canvas import _BotLibraryPanel  # noqa: PLC0415

    lib = SubgraphLibrary(shipped, user)
    panel = _BotLibraryPanel()
    panel.set_subgraph_library(lib)
    panel.refresh()
    _show_subgraphs(panel)
    return panel, lib


def test_a_stale_override_card_says_so_and_offers_revert(qapp, tmp_path):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    sync_players_definition().save(shipped / "subgraphs" / "sync-players.json")
    SubgraphLibrary(shipped, user).save(sync_players_definition(version=2))
    sync_players_definition(version=3, world="Krokotopia").save(shipped / "subgraphs" / "sync-players.json")
    panel, _lib = _panel_over(shipped, user)
    [card] = _subgraph_cards(panel)
    assert card.source_text == "Official (modified) · Official updated"
    reverts = []
    panel.delete_subgraph_requested.connect(reverts.append)
    card.revert_button.click()
    assert reverts == [SYNC_ID], "the same ask-then-revert as the card's menu"
    panel.deleteLater()


def test_an_override_whose_official_one_is_unchanged_offers_no_revert_beside_it(qapp, tmp_path):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    sync_players_definition().save(shipped / "subgraphs" / "sync-players.json")
    SubgraphLibrary(shipped, user).save(sync_players_definition(version=2))
    panel, _lib = _panel_over(shipped, user)
    [card] = _subgraph_cards(panel)
    assert card.source_text == "Official (modified)"
    assert card.revert_button is None
    panel.deleteLater()


def test_the_node_browsers_subgraph_page_says_official_updated(qapp, tmp_path):
    from PyQt6.QtWidgets import QLabel  # noqa: PLC0415

    from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog  # noqa: PLC0415
    from tests.test_node_browser_subgraphs import _tab_index  # noqa: PLC0415

    shipped, user = tmp_path / "shipped", tmp_path / "user"
    sync_players_definition().save(shipped / "subgraphs" / "sync-players.json")
    SubgraphLibrary(shipped, user).save(sync_players_definition(version=2))
    sync_players_definition(version=3, world="Krokotopia").save(shipped / "subgraphs" / "sync-players.json")
    dlg = NodeBrowserDialog(mode="add", subgraph_library=SubgraphLibrary(shipped, user))
    dlg._tab_bar.setCurrentIndex(_tab_index(dlg, "Subgraphs"))
    dlg._list.setCurrentRow(0)
    texts = [w.text() for w in dlg._detail.widget().findChildren(QLabel)]
    assert "Official (modified) · Official updated" in texts
