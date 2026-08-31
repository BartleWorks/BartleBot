"""Tests for the bot library panel's filtering.

The panel itself is a QWidget, so the filtering is a pure module-level function and
only that is tested here — same convention as tests/test_bot_canvas_paths.py.
"""

import sys
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QLabel

from bartlebot.gui.canvas.bot_canvas import _BotCard, _BotLibraryPanel, _BotMeta, filter_bot_metas
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.tag_picker import group_accent


def _meta(name: str, *, tags: list[str] | None = None, drops: list[str] | None = None, **kw) -> _BotMeta:
    return _BotMeta(
        name=name,
        description=kw.get("description", ""),
        authors=kw.get("authors", ""),
        drops=drops or [],
        tags=tags or [],
        start_location="",
        version="1.0",
        path=Path(f"/bots/{name}.json"),
    )


JADE = _meta("Jade Oni", tags=["Gear", "Spellements", "Boss"], drops=["Amber", "Jade Robe"])
FLAX = _meta("Nightside Flax", tags=["Reagents", "Gathering"], drops=["Flax"])
WATER = _meta("Waterworks", tags=["Gear", "Dungeon"], description="Crab Alley", authors="Someone")
OLYMPUS = _meta("Mt Olympus", tags=["Gear", "Spellements", "Dungeon"])
ALL = [JADE, FLAX, WATER, OLYMPUS]


def _names(metas: list[_BotMeta]) -> list[str]:
    return [m.name for m in metas]


def test_no_query_and_no_tags_returns_everything():
    results, _ = filter_bot_metas(ALL, "", set())

    assert _names(results) == _names(ALL)


def test_query_matches_name_case_insensitively():
    results, _ = filter_bot_metas(ALL, "jade", set())

    assert _names(results) == ["Jade Oni"]


def test_query_matches_description_and_author():
    assert _names(filter_bot_metas(ALL, "crab", set())[0]) == ["Waterworks"]
    assert _names(filter_bot_metas(ALL, "someone", set())[0]) == ["Waterworks"]


def test_query_matches_a_tag():
    """Typing a tag name works even for someone who never notices the chips."""
    results, _ = filter_bot_metas(ALL, "gathering", set())

    assert _names(results) == ["Nightside Flax"]


def test_query_matching_a_drop_reports_which_drops_matched():
    results, matched = filter_bot_metas(ALL, "amber", set())

    assert _names(results) == ["Jade Oni"]
    assert matched[JADE.path] == ["Amber"]


def test_drops_are_not_reported_when_the_query_is_empty():
    """Every drop would 'match' the empty string, turning each card into a wall of text."""
    _, matched = filter_bot_metas(ALL, "", {"Gear"})

    assert matched == {}


def test_a_single_tag_filters_with_no_query():
    """An empty query used to short-circuit back to the category listing."""
    results, _ = filter_bot_metas(ALL, "", {"Spellements"})

    assert _names(results) == ["Jade Oni", "Mt Olympus"]


def test_tags_from_different_groups_and_together():
    """Rewards and Activity answer different questions, so combining them narrows."""
    results, _ = filter_bot_metas(ALL, "", {"Spellements", "Dungeon"})

    assert _names(results) == ["Mt Olympus"]


def test_tags_within_one_group_or_together():
    """Two buttons make this a faceted filter, and a facet ORs inside itself.

    ANDing here would ask for a bot dropping both Gear and Reagents, which is a
    question almost nothing answers — the old behaviour, and why one combined
    picker could produce an empty list from two reasonable-looking clicks.
    """
    results, _ = filter_bot_metas(ALL, "", {"Gear", "Reagents"})

    assert _names(results) == ["Jade Oni", "Nightside Flax", "Waterworks", "Mt Olympus"]


def test_or_within_a_group_still_intersects_the_other_group():
    """ "Gear or Reagents" AND "Gathering" — Jade Oni has Gear but is a Boss."""
    results, _ = filter_bot_metas(ALL, "", {"Gear", "Reagents", "Gathering"})

    assert _names(results) == ["Nightside Flax"]


def test_tags_and_query_intersect():
    results, _ = filter_bot_metas(ALL, "olympus", {"Gear"})

    assert _names(results) == ["Mt Olympus"]

    # The same query with a tag the bot lacks yields nothing, not the query's hit.
    assert filter_bot_metas(ALL, "olympus", {"Reagents"})[0] == []


def test_whitespace_only_query_is_treated_as_no_query():
    results, _ = filter_bot_metas(ALL, "   ", {"Gathering"})

    assert _names(results) == ["Nightside Flax"]


def test_a_tag_no_bot_carries_yields_nothing():
    assert filter_bot_metas(ALL, "", {"Mounts"})[0] == []


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


# ---------------------------------------------------------------------------
# The panel, where category and filters compose
#
# A QWidget, so only the composition is checked here — the same latitude
# tests/test_tag_picker.py takes for the picker button.
# ---------------------------------------------------------------------------


def test_the_category_list_offers_all_after_recent(qapp):
    """Recent is the default because opening the library is usually the first step
    of going back to a bot you ran yesterday. All sits under it as the fallback —
    without it there is no way back to the whole library once a world is clicked,
    and nothing for a cross-world tag filter to be scoped to."""
    panel = _BotLibraryPanel()
    rows = [panel._cat_list.item(i).text() for i in range(panel._cat_list.count())]

    assert panel._ALL in rows
    if panel._RECENT in rows:
        assert rows[:2] == [panel._RECENT, panel._ALL]
    else:
        assert rows[0] == panel._ALL


def test_the_selected_category_is_the_first_row(qapp):
    """The ordering above *is* the default, so the two cannot drift apart."""
    panel = _BotLibraryPanel()

    assert panel._cat_list.currentRow() == 0


def test_switching_category_keeps_the_tag_filter(qapp):
    """It used to clear it, because a filter replaced the listing rather than
    narrowing it. Now that they compose, clearing is just discarding the user's work."""
    panel = _BotLibraryPanel()
    panel._set_tag_selection(["Gear"])

    panel._cat_list.setCurrentRow(panel._cat_list.count() - 1)

    assert panel._selected_tag_list() == ["Gear"]


def test_a_category_narrows_what_the_tags_search(qapp):
    """The world is one more facet, so it intersects rather than being overridden."""
    panel = _BotLibraryPanel()
    panel._all_metas = list(ALL)
    panel._category_map = {"Aquila": [OLYMPUS]}
    panel._cat_list.clear()
    panel._cat_list.addItem(panel._ALL)
    panel._cat_list.addItem("Aquila")

    panel._cat_list.setCurrentRow(0)
    assert _names(filter_bot_metas(panel._category_metas(), "", {"Gear"})[0]) == [
        "Jade Oni",
        "Waterworks",
        "Mt Olympus",
    ]

    panel._cat_list.setCurrentRow(1)
    assert _names(filter_bot_metas(panel._category_metas(), "", {"Gear"})[0]) == ["Mt Olympus"]


# ---------------------------------------------------------------------------
# Card pills
# ---------------------------------------------------------------------------


def _pill_styles(card: _BotCard) -> str:
    return " ".join(lbl.styleSheet() for lbl in card.findChildren(QLabel))


def test_a_tag_pill_is_coloured_by_its_group(qapp):
    """One palette for a tag everywhere: picker dot, filter chip, card pill.

    JADE carries Gear (Rewards) and Boss (Activity), so both hues appear.
    """
    styles = _pill_styles(_BotCard(JADE))

    assert group_accent("Rewards") in styles
    assert group_accent("Activity") in styles


def test_the_version_pill_is_neutral_rather_than_the_accent(qapp):
    """Colour on the card means "tag group" now, and a version is not one — the
    accent was being spent on the pill carrying the least information.

    Tagless, because the accent doubles as the Rewards group's colour: on a bot
    carrying one, finding it in the stylesheets proves nothing about the version.
    """
    styles = _pill_styles(_BotCard(_meta("Untagged")))

    assert THEME.pill_neutral_bg in styles
    assert f"background: {THEME.accent}" not in styles
