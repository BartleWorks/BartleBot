"""Subgraph categories (#172): the vocabulary, where a definition stores it, and the two filters.

A definition's categories are a stored group with a fixed vocabulary, normalized on
load, beside a Source worked out from where the file is. The library's Subgraphs folder
and the node browser's Subgraphs tab filter by both, OR within a group and AND across
groups, as the strategy dialog does. Real libraries on ``tmp_path``; the definition is
the synthetic "sync players" one.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QLabel, QWidget

from bartlebot import share_export
from bartlebot.bot.schema import SubgraphDefinition
from bartlebot.gui.canvas.bot_canvas import _BotCard, _BotLibraryPanel, _BotMeta, filter_bot_metas
from bartlebot.gui.canvas.bot_header import BotHeader
from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog, subgraph_key
from bartlebot.gui.widgets.tag_picker import BOT_VOCABULARY, SUBGRAPH_VOCABULARY
from bartlebot.gui.widgets.tag_pill import TagPill
from bartlebot.paths import project_root
from bartlebot.store_roots import Origin
from bartlebot.subgraph_store import SubgraphLibrary
from bartlebot.subgraph_tags import (
    SUBGRAPH_CATEGORIES,
    normalize_subgraph_tags,
    subgraph_matches,
    subgraph_tag_group,
)
from bartlebot.subgraphs import config_to_definition, definition_change, definition_to_config
from tests.test_subgraphs import SYNC_ID, sync_players_definition


def _defn(sid: str = SYNC_ID, name: str = "Sync players", tags: list[str] | None = None) -> SubgraphDefinition:
    return sync_players_definition().model_copy(update={"id": sid, "name": name, "tags": tags or []})


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


def test_the_vocabulary_is_the_node_browsers_words_and_team():
    from bartlebot.nodes.registry import NODE_CATEGORIES  # noqa: PLC0415

    node_words = {name for name, _ in NODE_CATEGORIES}
    assert set(SUBGRAPH_CATEGORIES) - node_words == {"Team"}


def test_no_category_is_also_a_bot_tag_or_a_source_word():
    """A shared word would put a bot tag's medallion on a subgraph, or match a Source chip."""
    from bartlebot.bot_tags import BOT_TAGS  # noqa: PLC0415
    from bartlebot.store_roots import SOURCE_LABELS  # noqa: PLC0415

    assert not set(SUBGRAPH_CATEGORIES) & set(BOT_TAGS)
    assert not set(SUBGRAPH_CATEGORIES) & set(SOURCE_LABELS.values())


def test_normalizing_orders_dedupes_and_drops_unknown_words(caplog):
    with caplog.at_level(logging.WARNING):
        assert normalize_subgraph_tags(["Team", "Bogus", "Zone Entry", "Team", 3, "Official"]) == [
            "Zone Entry",
            "Team",
        ]
    assert "Bogus" in caplog.text and "Official" in caplog.text, "a Source word is never stored"
    assert normalize_subgraph_tags("Team") == []
    assert normalize_subgraph_tags(None) == []


def test_the_groups_are_category_and_source():
    assert subgraph_tag_group("Combat and Healing") == "Category"
    assert subgraph_tag_group("Custom") == "Source"
    assert subgraph_tag_group("Boss") == ""


def test_matching_ors_within_a_group_and_ands_across_groups():
    assert subgraph_matches(["Team"], "Official", [])
    assert subgraph_matches(["Team"], "Official", ["Team", "Combat and Healing"])
    assert not subgraph_matches(["Team"], "Official", ["Combat and Healing"])
    assert subgraph_matches(["Team"], "Official", ["Team", "Official", "Custom"])
    assert not subgraph_matches(["Team"], "Official", ["Team", "Custom"])
    assert not subgraph_matches([], "Custom", ["Team"]), "an uncategorized subgraph matches no category"
    assert subgraph_matches([], "Custom", ["Custom"])


# ---------------------------------------------------------------------------
# Where a definition stores them
# ---------------------------------------------------------------------------


def test_a_definition_stores_its_categories_and_leaves_an_empty_list_out(tmp_path):
    path = tmp_path / "d.json"
    _defn().save(path)
    assert "tags" not in json.loads(path.read_text(encoding="utf-8")), "a definition never given one is unchanged"

    _defn(tags=["Team", "Combat and Healing"]).save(path)
    assert json.loads(path.read_text(encoding="utf-8"))["tags"] == ["Team", "Combat and Healing"]
    assert SubgraphDefinition.load(path).tags == ["Team", "Combat and Healing"]


def test_a_hand_edited_file_loads_with_its_unknown_categories_dropped(tmp_path):
    data = _defn().model_dump(mode="json") | {"tags": ["Travel", "Team"]}
    path = tmp_path / "d.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert SubgraphDefinition.load(path).tags == ["Team"]


def test_the_shipped_sync_players_is_team():
    defn = SubgraphDefinition.load(Path(project_root()) / "subgraphs" / "sync-players.json")
    assert defn.tags == ["Team"]


def test_the_document_carries_them_both_ways():
    config = definition_to_config(_defn(tags=["Team"]))
    assert config.definition.tags == ["Team"]
    assert config.tags == [], "the document's own tags are bot tags"
    config.definition.tags = ["Team", "Utility"]
    assert config_to_definition(config).tags == ["Team", "Utility"]


def test_a_category_change_is_meta_and_keeps_the_version():
    """Like a rename (#160): nothing a bot runs changes."""
    assert definition_change(_defn(), _defn(tags=["Team"])) == "meta"


def test_a_share_file_carries_them(tmp_path):
    library = SubgraphLibrary(tmp_path / "lib")
    library.save(_defn(tags=["Team"]))
    plan = share_export.export_subgraph(library.get(SYNC_ID), share_export.Sources(subgraph_library=library))
    assert plan.share.items[0].content["tags"] == ["Team"]


# ---------------------------------------------------------------------------
# Bar B edits them on a definition
# ---------------------------------------------------------------------------


@pytest.fixture
def header(qapp) -> BotHeader:
    return BotHeader(QWidget(), QWidget())


def test_a_definition_shows_its_categories_and_a_bot_its_tags(header):
    from tests.test_bot_header import _config  # noqa: PLC0415

    header.set_config(definition_to_config(_defn(tags=["Team"])))
    assert header.tags_field.vocabulary == SUBGRAPH_VOCABULARY
    assert header.tags_field.tags() == ["Team"]
    assert not header.tags_field.isHidden()
    assert list(header.tags_field._pickers) == ["Category"]

    header.set_config(_config(tags=["Gear", "Boss"]))
    assert header.tags_field.vocabulary == BOT_VOCABULARY
    assert header.tags_field.tags() == ["Gear", "Boss"]
    assert list(header.tags_field._pickers) == ["Rewards", "Activities"]


def test_picking_a_category_writes_the_definition_and_reports_the_edit(header):
    config = definition_to_config(_defn())
    header.set_config(config)
    header.set_editable(True)
    edits = []
    header.metadata_changed.connect(lambda: edits.append(1))

    header.tags_field._pickers["Category"].set_selection(["Combat and Healing", "Team"])
    header.tags_field._on_picker_changed([])

    assert config.definition.tags == ["Team", "Combat and Healing"]
    assert config.tags == []
    assert edits == [1]


# ---------------------------------------------------------------------------
# The library's Subgraphs folder
# ---------------------------------------------------------------------------


def _meta(name: str, source: Origin, tags: list[str]) -> _BotMeta:
    return _BotMeta(
        name=name,
        description="",
        authors="",
        drops=[],
        tags=tags,
        version="1",
        path=Path(f"/{name}"),
        subgraph_id=name,
        source=source,
    )


def test_filtering_metas_by_category_uses_the_category_groups():
    sync = _meta("Sync", Origin.SHIPPED, ["Team"])
    fight = _meta("Fight", Origin.USER, ["Combat and Healing"])
    both = _meta("Both", Origin.USER, ["Team", "Combat and Healing"])
    metas = [sync, fight, both]

    def run(tags, sources=frozenset()):
        return filter_bot_metas(metas, "", set(tags), sources, subgraph_tag_group)[0]

    assert run(["Team"]) == [sync, both]
    assert run(["Team", "Combat and Healing"]) == metas, "OR within Category"
    assert run(["Team"], {"Custom"}) == [both], "AND with Source"
    assert filter_bot_metas(metas, "comb", set(), group_of=subgraph_tag_group)[0] == [fight, both], "search finds one"


@pytest.fixture
def panel(qapp, tmp_path) -> _BotLibraryPanel:
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    SubgraphLibrary(shipped).save(_defn(tags=["Team"]))
    SubgraphLibrary(user).save(_defn("fight", "Fight loop", tags=["Combat and Healing"]))
    SubgraphLibrary(user).save(_defn("plain", "Plain"))
    p = _BotLibraryPanel()
    p.set_subgraph_library(SubgraphLibrary(shipped, user))
    p.refresh()
    return p


def _select(panel: _BotLibraryPanel, caption: str) -> None:
    panel._cat_list.setCurrentRow(
        next(i for i in range(panel._cat_list.count()) if panel._cat_list.item(i).text() == caption)
    )


def _names(panel: _BotLibraryPanel) -> list[str]:
    layout = panel._cards_layout
    cards = [w for i in range(layout.count()) if isinstance(w := layout.itemAt(i).widget(), _BotCard)]
    return [c.findChild(QLabel).text() for c in cards]


def test_the_subgraphs_folder_offers_category_in_place_of_the_bot_tags(panel):
    _select(panel, "All")
    assert panel.bot_tag_filters_visible and not panel.category_filter_visible
    _select(panel, "Subgraphs")
    assert panel.category_filter_visible and not panel.bot_tag_filters_visible
    assert "subgraphs" in panel._search.placeholderText()


def test_the_subgraphs_folder_filters_by_category_and_source(panel):
    _select(panel, "Subgraphs")
    assert _names(panel) == ["Fight loop", "Plain", "Sync players"]

    panel.set_category_filter(["Team"])
    assert _names(panel) == ["Sync players"]
    panel.set_category_filter(["Team", "Combat and Healing"])
    assert _names(panel) == ["Fight loop", "Sync players"]
    panel.set_source_filter(["Custom"])
    assert _names(panel) == ["Fight loop"]
    assert panel.chip_texts() == ["Team", "Combat and Healing", "Custom"], "in vocabulary order"

    panel._clear_filters()
    assert _names(panel) == ["Fight loop", "Plain", "Sync players"]


def test_a_bot_tag_left_selected_does_not_empty_the_subgraphs_folder(panel):
    _select(panel, "All")
    panel._set_tag_selection(["Gear"])
    _select(panel, "Subgraphs")
    assert _names(panel) == ["Fight loop", "Plain", "Sync players"]
    assert panel.chip_texts() == [], "its chip comes back with the bots"
    _select(panel, "All")
    assert panel.chip_texts() == ["Gear"]


def test_a_subgraph_card_wears_its_categories(panel):
    _select(panel, "Subgraphs")
    pills = [p for p in panel._cards_container.findChildren(TagPill) if p.isVisibleTo(panel._cards_container)]
    assert [p.tag for p in pills] == ["Combat and Healing", "Team"]
    assert all(p._vocabulary == SUBGRAPH_VOCABULARY for p in pills)


def test_the_category_picker_counts_the_subgraphs(panel):
    rows = {r.key: r.meta for _title, section in panel._category_picker._sections for r in section}
    assert rows["Team"] == "1 subgraph"
    assert rows["Utility"] == "0 subgraphs"


# ---------------------------------------------------------------------------
# The node browser's Subgraphs tab
# ---------------------------------------------------------------------------


@pytest.fixture
def browser(qapp, tmp_path) -> NodeBrowserDialog:
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    SubgraphLibrary(shipped).save(_defn(tags=["Team"]))
    SubgraphLibrary(user).save(_defn("fight", "Fight loop", tags=["Combat and Healing"]))
    SubgraphLibrary(user).save(_defn("mine", "My sync", tags=["Team"]))
    return NodeBrowserDialog(mode="add", subgraph_library=SubgraphLibrary(shipped, user))


def _subgraph_tab(dlg: NodeBrowserDialog) -> None:
    dlg._tab_bar.setCurrentIndex(next(i for i, (lbl, _) in enumerate(dlg._tabs_data) if lbl == "Subgraphs"))


def test_the_category_filter_is_offered_on_the_subgraphs_tab_only(browser):
    assert not browser.category_filter_visible
    _subgraph_tab(browser)
    assert browser.category_filter_visible and browser.source_filter_visible


def test_the_subgraphs_tab_filters_by_category_and_source(browser):
    _subgraph_tab(browser)
    browser.set_category_filter(["Team"])
    assert set(browser.offered_node_types) == {subgraph_key(SYNC_ID), subgraph_key("mine")}
    browser.set_source_filter(["Custom"])
    assert browser.offered_node_types == [subgraph_key("mine")]
    browser.set_category_filter(["Team", "Combat and Healing"])
    assert set(browser.offered_node_types) == {subgraph_key("mine"), subgraph_key("fight")}


def test_search_finds_a_subgraph_by_category_and_keeps_the_filter(browser):
    browser._search.setText("combat")
    assert subgraph_key("fight") in browser.offered_node_types
    browser.set_category_filter(["Team"])
    assert subgraph_key("fight") not in browser.offered_node_types


def test_the_detail_panel_shows_the_categories(browser):
    _subgraph_tab(browser)
    browser.set_category_filter(["Combat and Healing"])
    browser._list.setCurrentRow(0)
    assert [p.tag for p in browser._detail.widget().findChildren(TagPill)] == ["Combat and Healing"]


def test_a_definitions_categories_stay_on_one_line_in_edit_mode(header):
    """Its provenance shares the row, where a bot has none, so the row is measured with both.

    Off-screen, for the reason ``test_a_typical_bot_keeps_its_tags_on_one_line_in_edit_mode`` gives.
    """
    from PyQt6.QtCore import Qt  # noqa: PLC0415

    header.set_config(definition_to_config(_defn(tags=["Team", "Combat and Healing", "Utility"])))
    header.set_definition_info("Official (modified)", 12)
    header.set_editable(True)
    header.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    header.resize(1920, header.sizeHint().height())
    header.show()
    header.layout().activate()

    field = header.tags_field
    assert field.height() == field._floor(), "the category row wrapped at 1920px"
