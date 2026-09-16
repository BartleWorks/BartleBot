"""Drop category icons (#29): the pin kinds, the icon keys, the extractor's table, and both drop tables.

The extractor needs a game install to run, so its side is checked for what can be wrong
without one: that the table covers the category vocabulary and crops only what is a state
sheet. The tables are checked with an icon extracted and without one, the ordinary install.
"""

from __future__ import annotations

import json

import pytest
from PIL import Image
from PyQt6.QtWidgets import QTreeWidget

from bartlebot import card_art
from bartlebot.bot.schema import DropModel
from bartlebot.card_art import drop_icon_name, pin_icon_name
from bartlebot.nodes.utility import quicksell
from bartlebot.wiki_drops import PIN_KINDS, pin_kind
from tools.extract_card_art import (
    DROP_ICON_SOURCES,
    DROP_TEXTURES,
    ICON_SOURCES,
    PIN_ICON_SOURCES,
    PIN_TEXTURES,
    STATE_SHEET_ICONS,
)

# -- pin kinds ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("Balance Accurate Pin (100)", "power"),
        ("Storm Conversion Pin (180)", "power"),
        ("Fire Blocking Pin (150, Life)", "shield"),
        ("Ice Resist Pin (140, Myth)", "shield"),
        ("Life Mending Pin (170)", "shield"),
        ("Balance Punishing Pin (60)", "sword"),
        ("Myth Disabling Pin (90)", "sword"),
        ("Balance Crushing Pin (60, Death)", "sword"),
    ],
)
def test_a_pin_is_filed_under_its_type_word(name, kind):
    assert pin_kind(name) == kind


def test_a_pin_of_no_known_type_has_no_kind():
    assert pin_kind("Fire Gleaming Pin (100)") is None


def test_the_type_word_only_counts_directly_before_pin():
    """A type word elsewhere in a name says nothing about what the item is."""
    assert pin_kind("Resist Amulet") is None
    assert pin_kind("Crushing Blow Pin Cushion") is None


# -- icon keys ---------------------------------------------------------------------------


def test_a_category_key_is_a_tidy_filename():
    assert drop_icon_name("Treasure Cards") == "drop_treasure_cards"
    assert drop_icon_name("Hats") == "drop_hats"


def test_a_pin_takes_its_kinds_icon():
    assert drop_icon_name("Pins", "Fire Blocking Pin (150, Life)") == "pin_shield"
    assert drop_icon_name("Pins", "Storm Accurate Pin (80)") == "pin_power"


def test_a_pin_of_no_known_kind_keeps_the_category_icon():
    assert drop_icon_name("Pins", "Fire Gleaming Pin (100)") == "drop_pins"
    assert drop_icon_name("Pins") == "drop_pins"


def test_only_the_pins_category_is_split_by_name():
    """A jewel is not a pin, whatever its name says."""
    assert drop_icon_name("Jewels", "Fire Blocking Pin (150)") == "drop_jewels"


# -- the extractor's table ---------------------------------------------------------------


def test_every_category_the_import_produces_has_an_icon():
    """A category added to quicksell's vocabulary with no icon keeps plain text silently;
    this is where that gap shows."""
    vocabulary = set(quicksell.QUICKSELLABLE_CATEGORIES) | set(quicksell.NEVER_QUICKSOLD_CATEGORIES)
    assert set(DROP_TEXTURES) == vocabulary


def test_every_pin_kind_has_an_icon():
    assert set(PIN_TEXTURES) == set(PIN_KINDS.values())


def test_an_unknown_pin_draws_the_jewels_medallion():
    assert DROP_TEXTURES["Pins"] == DROP_TEXTURES["Jewels"]


def test_drop_sources_are_full_paths_and_join_the_icons_map():
    assert all(t.startswith("GUI/") and t.endswith(".dds") for t in DROP_ICON_SOURCES.values())
    assert DROP_ICON_SOURCES.items() <= ICON_SOURCES.items()


def test_only_the_pin_buttons_are_cropped():
    """The pin buttons are 2x2 state sheets; the medallions are single images."""
    assert set(PIN_ICON_SOURCES) <= STATE_SHEET_ICONS
    assert not (set(DROP_ICON_SOURCES) - set(PIN_ICON_SOURCES)) & STATE_SHEET_ICONS


# -- the read side and the tables --------------------------------------------------------


@pytest.fixture
def art_home(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    card_art.forget_cache()
    yield tmp_path
    card_art.forget_cache()


def _extract(*keys: str) -> None:
    card_art.icons_dir().mkdir(parents=True, exist_ok=True)
    for key in keys:
        Image.new("RGBA", (128, 128), (10, 200, 10, 255)).save(card_art.icons_dir() / f"{key}.png")
    card_art.index_path().write_text(json.dumps({"icons": {k: f"{k}.png" for k in keys}}), encoding="utf-8")


def test_a_drop_icon_resolves_only_when_extracted(art_home):
    _extract("drop_hats", pin_icon_name("sword"))

    assert card_art.drop_icon_path("Hats") == card_art.icons_dir() / "drop_hats.png"
    assert card_art.drop_icon_path("Pins", "Balance Crushing Pin (60)") is not None
    assert card_art.drop_icon_path("Robes") is None


def test_no_icon_for_an_uncategorized_drop(qapp, art_home):
    from bartlebot.gui.widgets.drop_tree import drop_icon

    _extract("drop_hats")

    assert drop_icon(None) is None
    assert drop_icon("") is None
    assert drop_icon("Hats") is not None


def test_the_icon_keeps_the_full_size_art(qapp, art_home):
    """Pre-scaled to 20px, the icon was stretched back up on a scaled display and drew
    soft; the view has to be able to scale down from the original for each screen."""
    from PyQt6.QtCore import QSize

    from bartlebot.gui.widgets.drop_tree import drop_icon

    _extract("drop_hats")

    assert QSize(128, 128) in drop_icon("Hats").availableSizes()


def test_forgetting_the_cache_lets_new_icons_appear(qapp, art_home):
    from bartlebot.gui.widgets.drop_tree import drop_icon

    assert drop_icon("Hats") is None

    _extract("drop_hats")
    card_art.forget_cache()

    assert drop_icon("Hats") is not None


_DROPS = [
    DropModel(name="Wizard Hat", category="Hats"),
    DropModel(name="Fire Blocking Pin (150, Life)", category="Pins"),
    DropModel(name="Mystery Item"),
]


def _possible_drops(drops):
    from bartlebot.gui.sidebar.sections.possible_drops_section import _DropsListWidget

    widget = _DropsListWidget()
    widget.set_items(drops)
    return widget, _DropsListWidget.COL_NAME, _DropsListWidget.COL_CATEGORY


def _keep_list(drops):
    from bartlebot.gui.canvas.drop_keep_list import _COL_CATEGORY, _COL_ITEM, DropKeepListWidget

    widget = DropKeepListWidget(lambda: list(drops))
    widget.set_context({"sell_pets": True, "sell_socket_wrenches": True, "sell_skeleton_keys": True})
    widget.set_value([])
    return widget, _COL_ITEM, _COL_CATEGORY


def _category_icons(widget, name_col, category_col) -> dict[str, bool]:
    tree = widget.findChild(QTreeWidget)
    return {
        tree.topLevelItem(i).text(name_col): not tree.topLevelItem(i).icon(category_col).isNull()
        for i in range(tree.topLevelItemCount())
    }


@pytest.mark.parametrize("build", [_possible_drops, _keep_list])
def test_a_table_draws_the_icons_it_has(qapp, art_home, build):
    from bartlebot.gui.widgets.drop_tree import DROP_ICON_PX

    _extract("drop_hats", "pin_shield")
    widget, name_col, category_col = build(_DROPS)

    assert _category_icons(widget, name_col, category_col) == {
        "Wizard Hat": True,
        "Fire Blocking Pin (150, Life)": True,
        "Mystery Item": False,
    }
    assert widget.findChild(QTreeWidget).iconSize().height() == DROP_ICON_PX


@pytest.mark.parametrize("build", [_possible_drops, _keep_list])
def test_a_table_without_art_keeps_its_category_text(qapp, art_home, build):
    widget, name_col, category_col = build(_DROPS)

    assert not any(_category_icons(widget, name_col, category_col).values())
    tree = widget.findChild(QTreeWidget)
    texts = {tree.topLevelItem(i).text(category_col) for i in range(tree.topLevelItemCount())}
    assert {"Hats", "Pins"} <= texts
