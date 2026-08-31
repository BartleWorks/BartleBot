"""Tests for the bot library panel's filtering.

The panel itself is a QWidget, so the filtering is a pure module-level function and
only that is tested here — same convention as tests/test_bot_canvas_paths.py.
"""

from pathlib import Path

from bartlebot.gui.canvas.bot_canvas import _BotMeta, filter_bot_metas


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


def test_two_tags_and_together_rather_than_or():
    results, _ = filter_bot_metas(ALL, "", {"Spellements", "Dungeon"})

    assert _names(results) == ["Mt Olympus"]


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
