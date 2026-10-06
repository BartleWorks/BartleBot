"""The node browser's Subgraphs tab (Stage 4 of #37).

A library handed to the dialog adds one tab listing its definitions under
``subgraph:<id>`` keys, which ``selected_node_type`` returns exactly as it would a
node type; a port insert (``allowed_node_types``) never gets the tab. Real
dialog tests on a ``tmp_path`` library; the definition is the synthetic "sync
players" one.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QLabel

from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog, _SubgraphListItem, subgraph_key
from bartlebot.subgraph_store import SubgraphLibrary
from tests.test_subgraphs import SYNC_ID, sync_players_definition


@pytest.fixture
def library(tmp_path) -> SubgraphLibrary:
    lib = SubgraphLibrary(tmp_path / "library")
    lib.save(sync_players_definition())
    return lib


def _tab_index(dlg: NodeBrowserDialog, label: str) -> int:
    return next(i for i, (lbl, _) in enumerate(dlg._tabs_data) if lbl == label)


def test_the_subgraphs_tab_lists_the_library(qapp, library):
    dlg = NodeBrowserDialog(mode="add", subgraph_library=library)
    dlg._tab_bar.setCurrentIndex(_tab_index(dlg, "Subgraphs"))
    assert dlg.offered_node_types == [subgraph_key(SYNC_ID)]
    item = dlg._list.item(0)
    assert isinstance(item, _SubgraphListItem)
    assert item.text() == "Sync players"
    dlg._list.setCurrentRow(0)
    assert dlg.selected_node_type == subgraph_key(SYNC_ID)
    assert dlg._add_btn.isEnabled()


def test_no_library_means_no_tab(qapp):
    dlg = NodeBrowserDialog(mode="add")
    assert "Subgraphs" not in [lbl for lbl, _ in dlg._tabs_data]


def test_a_port_insert_never_offers_subgraphs(qapp, library):
    dlg = NodeBrowserDialog(mode="add", allowed_node_types={"sleep"}, subgraph_library=library)
    assert "Subgraphs" not in [lbl for lbl, _ in dlg._tabs_data]
    dlg._search.setText("Sync")
    assert subgraph_key(SYNC_ID) not in dlg.offered_node_types


def test_search_finds_a_subgraph_by_name(qapp, library):
    dlg = NodeBrowserDialog(mode="add", subgraph_library=library)
    dlg._search.setText("regroup")  # in the description
    assert subgraph_key(SYNC_ID) in dlg.offered_node_types


def test_the_detail_panel_describes_the_subgraph(qapp, library, tmp_path, monkeypatch):
    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415

    bots = tmp_path / "bots"
    bots.mkdir()
    from tests.test_subgraphs import linked_bot  # noqa: PLC0415

    linked_bot(library.get(SYNC_ID), library).save(bots / "one.json")
    SettingsManager.instance().set("canvas", "bot_library_path", value=str(bots))

    dlg = NodeBrowserDialog(mode="add", subgraph_library=library)
    dlg._tab_bar.setCurrentIndex(_tab_index(dlg, "Subgraphs"))
    dlg._list.setCurrentRow(0)
    panel = dlg._detail
    assert panel._shown[0] == "subgraph"
    joined = " ".join(w.text() for w in panel.widget().findChildren(QLabel))
    assert "Used by 1 bot" in joined
    assert "Custom" in joined, "the Source pill"
    assert "7 states" in joined


def test_adding_a_subgraph_does_not_touch_recent(qapp, library):
    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415

    before = list(SettingsManager.instance().recently_used_nodes)
    dlg = NodeBrowserDialog(mode="add", subgraph_library=library)
    dlg._tab_bar.setCurrentIndex(_tab_index(dlg, "Subgraphs"))
    dlg._list.setCurrentRow(0)
    dlg._on_add()
    assert list(SettingsManager.instance().recently_used_nodes) == before


def test_the_empty_tab_says_how_to_fill_it(qapp, tmp_path):
    dlg = NodeBrowserDialog(mode="add", subgraph_library=SubgraphLibrary(tmp_path / "empty"))
    dlg._tab_bar.setCurrentIndex(_tab_index(dlg, "Subgraphs"))
    assert "Save Region as Reusable Subgraph" in dlg._empty_label.text()


def test_the_source_filter_is_offered_on_the_subgraphs_tab_and_narrows_it(qapp, tmp_path):
    """Official or the user's own, in the words every list uses (#149)."""
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    SubgraphLibrary(shipped).save(sync_players_definition())
    mine = sync_players_definition().model_copy(update={"id": "mine", "name": "My sync"})
    SubgraphLibrary(user).save(mine)
    dlg = NodeBrowserDialog(mode="add", subgraph_library=SubgraphLibrary(shipped, user))

    assert not dlg.source_filter_visible, "a node type has no source to filter by"
    dlg._tab_bar.setCurrentIndex(_tab_index(dlg, "Subgraphs"))
    assert dlg.source_filter_visible
    assert set(dlg.offered_node_types) == {subgraph_key(SYNC_ID), subgraph_key("mine")}

    dlg.set_source_filter(["Official"])
    assert dlg.offered_node_types == [subgraph_key(SYNC_ID)]
    dlg.set_source_filter(["Custom"])
    assert dlg.offered_node_types == [subgraph_key("mine")]
    dlg._search.setText("sync")
    offered = [k for k in dlg.offered_node_types if k.startswith("subgraph:")]
    assert offered == [subgraph_key("mine")], "a search's subgraphs are filtered too"
