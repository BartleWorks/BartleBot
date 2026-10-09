"""Tests for the bot library panel's filtering.

The panel itself is a QWidget, so the filtering is a pure module-level function and
only that is tested here — same convention as tests/test_bot_canvas_paths.py.
"""

import json
from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QEnterEvent
from PyQt6.QtWidgets import QLabel

import bartlebot.gui.theming.theme as theme_mod
from bartlebot.gui.canvas.bot_canvas import _BotCard, _BotLibraryPanel, _BotMeta, _read_bot_meta, filter_bot_metas
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.widgets.pickers.tag_picker import group_accent


def _meta(name: str, *, tags: list[str] | None = None, drops: list[str] | None = None, **kw) -> _BotMeta:
    return _BotMeta(
        name=name,
        description=kw.get("description", ""),
        authors=kw.get("authors", ""),
        drops=drops or [],
        tags=tags or [],
        version="1.0",
        path=Path(f"/bots/{name}.json"),
        generated=kw.get("generated", ""),
    )


JADE = _meta("Jade Oni", tags=["Gear", "Spellements", "Boss"], drops=["Amber", "Jade Robe"])
FLAX = _meta("Nightside Flax", tags=["Reagents", "Collecting"], drops=["Flax"])
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


# ---------------------------------------------------------------------------
# A bot with no description: the one its graph implies
# ---------------------------------------------------------------------------

_BOSS_BOT = {
    "id": "b",
    "name": "Nightshade",
    "initial": "a",
    "recovery_state": "a",
    "states": [
        {
            "id": "a",
            "node_type": "enter_dungeon",
            "params": {
                "from_zone": "WizardCity/WC_Streets/WC_HauntedCave",
                "dungeon_zone": "WizardCity/WC_Streets/Interiors/WC_HauntedCave_T1",
            },
        },
        {"id": "b", "node_type": "handle_combat", "params": {"enemy": "Wraith-WCBoss-L05"}},
    ],
    "transitions": [{"trigger": "advance", "source": "a", "dest": "b", "when": "SUCCESS"}],
}


def test_a_bot_with_no_description_gets_the_generated_one(tmp_path):
    path = tmp_path / "nightshade.json"
    path.write_text(json.dumps(_BOSS_BOT), encoding="utf-8")

    meta = _read_bot_meta(path)

    assert meta.description == ""
    assert meta.generated == "Defeats Lord Nightshade in Nightshade's Tower, Haunted Cave, Wizard City."


def test_a_bot_with_a_description_is_not_given_another(tmp_path):
    path = tmp_path / "nightshade.json"
    path.write_text(json.dumps({**_BOSS_BOT, "description": "Farms Lord Nightshade."}), encoding="utf-8")

    assert _read_bot_meta(path).generated == ""


def test_query_matches_the_generated_description():
    bot = _meta("Nightshade", generated="Defeats Lord Nightshade in Nightshade's Tower.")

    assert _names(filter_bot_metas([*ALL, bot], "tower", set())[0]) == ["Nightshade"]


def test_a_card_shows_the_generated_description_in_italics(qapp):
    card = _BotCard(_meta("Nightshade", generated="Defeats Lord Nightshade."))

    assert card._desc_lbl.text() == "Defeats Lord Nightshade."  # noqa: SLF001
    assert "italic" in card._desc_lbl.styleSheet()  # noqa: SLF001


def test_a_long_word_in_a_description_does_not_widen_the_card(qapp):
    """A description is user text, and word wrap cannot break a word (#118)."""
    short = _BotCard(_meta("Nightshade", description="Farms it."))
    long = _BotCard(_meta("Nightshade", description="Farms " + "Nightshade" * 30 + "."))

    assert long.minimumSizeHint().width() == short.minimumSizeHint().width()


def test_a_generated_description_keeps_its_tooltip(qapp):
    card = _BotCard(_meta("Nightshade", generated="Defeats Lord Nightshade."))

    assert card._desc_lbl.toolTip() == "Generated from the graph: this bot has no description."  # noqa: SLF001


def test_a_card_shows_an_authored_description_upright(qapp):
    card = _BotCard(WATER)

    assert card._desc_lbl.text() == "Crab Alley"  # noqa: SLF001
    assert "italic" not in card._desc_lbl.styleSheet()  # noqa: SLF001


def test_query_matches_a_tag():
    """Typing a tag name works even for someone who never notices the chips."""
    results, _ = filter_bot_metas(ALL, "Collecting", set())

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
    """Rewards and Activities answer different questions, so combining them narrows."""
    results, _ = filter_bot_metas(ALL, "", {"Spellements", "Dungeon"})

    assert _names(results) == ["Mt Olympus"]


def test_tags_within_one_group_or_together():
    """Two buttons make this a faceted filter, and a facet ORs inside itself.

    ANDing here would ask for a bot dropping both Gear and Reagents, which is a
    question almost nothing answers — the old behavior, and why one combined
    picker could produce an empty list from two reasonable-looking clicks.
    """
    results, _ = filter_bot_metas(ALL, "", {"Gear", "Reagents"})

    assert _names(results) == ["Jade Oni", "Nightside Flax", "Waterworks", "Mt Olympus"]


def test_or_within_a_group_still_intersects_the_other_group():
    """ "Gear or Reagents" AND "Collecting" — Jade Oni has Gear but is a Boss."""
    results, _ = filter_bot_metas(ALL, "", {"Gear", "Reagents", "Collecting"})

    assert _names(results) == ["Nightside Flax"]


def test_tags_and_query_intersect():
    results, _ = filter_bot_metas(ALL, "olympus", {"Gear"})

    assert _names(results) == ["Mt Olympus"]

    # The same query with a tag the bot lacks yields nothing, not the query's hit.
    assert filter_bot_metas(ALL, "olympus", {"Reagents"})[0] == []


def test_whitespace_only_query_is_treated_as_no_query():
    results, _ = filter_bot_metas(ALL, "   ", {"Collecting"})

    assert _names(results) == ["Nightside Flax"]


def test_a_tag_no_bot_carries_yields_nothing():
    assert filter_bot_metas(ALL, "", {"Mounts"})[0] == []


# ---------------------------------------------------------------------------
# The panel, where category and filters compose
#
# A QWidget, so only the composition is checked here — the same latitude
# tests/gui/widgets/pickers/test_tag_picker.py takes for the picker button.
# ---------------------------------------------------------------------------


def _cards(panel: _BotLibraryPanel) -> list:
    """The card widgets currently in the panel, ignoring the trailing stretch."""
    layout = panel._cards_layout
    return [w for i in range(layout.count()) if (w := layout.itemAt(i).widget()) is not None]


def test_the_panel_lists_nothing_until_it_is_shown(qapp):
    """It is BotCanvas's placeholder, and a canvas that opens a bot hides it before it
    is ever painted — listing in the constructor read every file in bots/ and built a
    card widget for each one, for a panel nobody was going to see."""
    panel = _BotLibraryPanel()

    assert panel._all_metas == []
    assert _cards(panel) == []


def test_showing_the_panel_lists_the_library(qapp):
    panel = _BotLibraryPanel()

    panel.show()

    assert panel._all_metas != []
    assert panel._cat_list.count() > 0


def test_a_second_show_does_not_relist(qapp):
    """Only the first show pays for the scan; re-showing keeps what is there."""
    panel = _BotLibraryPanel()
    panel.show()
    metas = panel._all_metas

    panel.hide()
    panel.show()

    assert panel._all_metas is metas


def test_a_listing_builds_its_cards_once(qapp):
    """refresh() used to select row 0 *and* call the row handler by hand. Selecting a
    row already emits currentRowChanged — the list was cleared, so row 0 is always a
    change — so every card was built, torn down and built again, and on a profile with
    no recent files that is the whole library twice."""
    panel = _BotLibraryPanel()
    real = panel._show_cards
    calls = []

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    panel._show_cards = counting

    panel.refresh()

    assert len(calls) == 1


def test_the_category_list_offers_all_after_recent(qapp):
    """Recent is the default because opening the library is usually the first step
    of going back to a bot you ran yesterday. All sits under it as the fallback —
    without it there is no way back to the whole library once a world is clicked,
    and nothing for a cross-world tag filter to be scoped to."""
    panel = _BotLibraryPanel()
    panel.refresh()
    rows = [panel._cat_list.item(i).text() for i in range(panel._cat_list.count())]

    assert panel._ALL in rows
    if panel._RECENT in rows:
        assert rows[:2] == [panel._RECENT, panel._ALL]
    else:
        assert rows[0] == panel._ALL


def test_the_selected_category_is_the_first_row(qapp):
    """The ordering above *is* the default, so the two cannot drift apart."""
    panel = _BotLibraryPanel()
    panel.refresh()

    assert panel._cat_list.currentRow() == 0


def test_switching_category_keeps_the_tag_filter(qapp):
    """It used to clear it, because a filter replaced the listing rather than
    narrowing it. Now that they compose, clearing is just discarding the user's work."""
    panel = _BotLibraryPanel()
    panel.refresh()
    panel._set_tag_selection(["Gear"])

    panel._cat_list.setCurrentRow(panel._cat_list.count() - 1)

    assert panel._selected_tag_list() == ["Gear"]


def test_a_category_narrows_what_the_tags_search(qapp):
    """The world is one more facet, so it intersects rather than being overridden."""
    panel = _BotLibraryPanel()
    panel._all_metas = list(ALL)
    panel._category_map = {"13_aquila": [OLYMPUS]}
    panel._cat_list.clear()
    panel.add_category_row(panel._ALL_KEY, panel._ALL)
    panel.add_category_row("13_aquila", "Aquila")

    panel._cat_list.setCurrentRow(0)
    assert _names(filter_bot_metas(panel._category_metas(), "", {"Gear"})[0]) == [
        "Jade Oni",
        "Waterworks",
        "Mt Olympus",
    ]

    panel._cat_list.setCurrentRow(1)
    assert _names(filter_bot_metas(panel._category_metas(), "", {"Gear"})[0]) == ["Mt Olympus"]


# ---------------------------------------------------------------------------
# Folder rows: keyed by folder, captioned by the game, icons optional
# ---------------------------------------------------------------------------


def _write_bot(folder: Path, name: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(f'{{"name": "{name}"}}', encoding="utf-8")


@pytest.fixture
def library(tmp_path, monkeypatch):
    from bartlebot.content import bot_roots  # noqa: PLC0415
    from bartlebot.game import card_art  # noqa: PLC0415
    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415

    # The user's folder alone: the shipped bots would add every world the repo has.
    monkeypatch.setattr(bot_roots, "shipped_bots_dir", lambda: tmp_path / "no-shipped-bots")
    bots = tmp_path / "bots"
    _write_bot(bots / "04_mooshu", "Jade Oni")
    _write_bot(bots / "31_four_dungeons", "Graveyard")
    _write_bot(bots / "40_my_farms", "Mine")
    (bots / "26_gardening").mkdir()
    SettingsManager.instance().set("canvas", "bot_library_path", value=str(bots))
    card_art.forget_cache()
    yield bots
    card_art.forget_cache()


def _rows(panel: _BotLibraryPanel) -> list[tuple[str, str]]:
    items = [panel._cat_list.item(i) for i in range(panel._cat_list.count())]
    return [(item.data(Qt.ItemDataRole.UserRole), item.text()) for item in items]


def test_a_folder_row_is_captioned_by_its_category_and_keyed_by_its_name(qapp, library):
    """MooShu, not the "Mooshu" a title-cased folder name gave, and the lookup survives
    the caption differing from the folder."""
    panel = _BotLibraryPanel()
    panel.refresh()

    assert _rows(panel)[1:] == [
        ("04_mooshu", "MooShu"),
        ("31_four_dungeons", "Four Dungeons"),
        ("40_my_farms", "My Farms"),
    ]

    panel._cat_list.setCurrentRow(2)
    assert _names(panel._category_metas()) == ["Graveyard"]


def test_an_empty_folder_is_not_listed(qapp, library):
    panel = _BotLibraryPanel()
    panel.refresh()

    assert "26_gardening" not in [key for key, _ in _rows(panel)]


def test_with_no_art_extracted_no_row_has_an_icon(qapp, library):
    """The ordinary case: rows keep their compact height, and nothing is logged."""
    panel = _BotLibraryPanel()
    panel.refresh()

    assert all(panel._cat_list.item(i).icon().isNull() for i in range(panel._cat_list.count()))


def test_extracted_badges_decorate_all_and_the_known_folders(qapp, library, tmp_path):
    from PyQt6.QtGui import QColor, QPixmap  # noqa: PLC0415

    from bartlebot.game import card_art  # noqa: PLC0415

    icons = {"badge_all": "badge_all.png", "badge_4dungeons": "badge_4dungeons.png"}
    card_art.icons_dir().mkdir(parents=True, exist_ok=True)
    for filename in icons.values():
        pixmap = QPixmap(64, 64)
        pixmap.fill(QColor("red"))
        pixmap.save(str(card_art.icons_dir() / filename))
    card_art.index_path().write_text(json.dumps({"icons": icons}), encoding="utf-8")
    card_art.forget_cache()

    panel = _BotLibraryPanel()
    panel.refresh()
    with_icon = {
        panel._cat_list.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(panel._cat_list.count())
        if not panel._cat_list.item(i).icon().isNull()
    }

    # MooShu's badge was not extracted and my_farms has none to have.
    assert with_icon == {panel._ALL_KEY, "31_four_dungeons"}


def test_loose_files_are_listed_as_other(qapp, library):
    (library / "loose.json").write_text('{"name": "Loose"}', encoding="utf-8")
    panel = _BotLibraryPanel()
    panel.refresh()

    assert _rows(panel)[-1] == (panel._OTHER_KEY, panel._OTHER)


# ---------------------------------------------------------------------------
# Cards are kept across filters
#
# Rebuilding every card per keystroke made the search box lag once the tag pills
# carried medallions; the panel now builds a card once per refresh.
# ---------------------------------------------------------------------------


def _listed_panel(metas: list[_BotMeta]) -> _BotLibraryPanel:
    panel = _BotLibraryPanel()
    panel._all_metas = list(metas)
    panel._cat_list.clear()
    panel.add_category_row(panel._ALL_KEY, panel._ALL)
    panel._cat_list.setCurrentRow(0)
    return panel


def test_a_search_reuses_the_cards_it_already_built(qapp):
    panel = _listed_panel(ALL)
    before = {id(card) for card in _cards(panel)}

    panel._search.setText("jade")
    assert [card._meta for card in _cards(panel)] == [JADE]

    panel._search.setText("")
    assert {id(card) for card in _cards(panel)} == before


def test_a_kept_card_shows_and_drops_the_matched_drops_line(qapp):
    panel = _listed_panel(ALL)

    panel._search.setText("amber")
    (card,) = _cards(panel)
    assert card.matched_drops_text == "Drops: Amber"

    panel._search.setText("jade oni")
    assert _cards(panel) == [card]
    assert card.matched_drops_text is None


def test_the_empty_hint_does_not_linger_among_kept_cards(qapp):
    panel = _listed_panel(ALL)

    panel._search.setText("no bot is called this")
    assert panel._empty_hint is not None

    panel._search.setText("")
    assert panel._empty_hint is None
    assert all(isinstance(w, _BotCard) for w in _cards(panel))


def test_a_refresh_builds_new_cards(qapp, library):
    """The files were re-read, so a kept card may describe a version that is gone."""
    panel = _BotLibraryPanel()
    panel.refresh()
    # Hold the cards themselves, not their ids: a freed card's id is free for the
    # next allocation, so a new card can come back with an old one's id.
    before = list(panel._cards.values())
    assert before

    panel.refresh()

    assert not any(card is old for card in panel._cards.values() for old in before)


def test_a_card_built_with_matched_drops_shows_them(qapp):
    card = _BotCard(JADE, matched_drops=["Amber", "Jade Robe", "Jade Hat", "Jade Boots"])

    assert card.matched_drops_text == "Drops: Amber, Jade Robe, Jade Hat +1 more"


# ---------------------------------------------------------------------------
# Card pills
# ---------------------------------------------------------------------------


def _pill_styles(card: _BotCard) -> str:
    return " ".join(lbl.styleSheet() for lbl in card.findChildren(QLabel))


def test_a_tag_pill_is_colored_by_its_group(qapp):
    """One palette for a tag everywhere: picker dot, filter chip, card pill.

    JADE carries Gear (Rewards) and Boss (Activities), so both hues appear.
    """
    styles = _pill_styles(_BotCard(JADE))

    assert group_accent("Rewards") in styles
    assert group_accent("Activities") in styles


def test_the_version_pill_is_neutral_rather_than_the_accent(qapp):
    """Color on the card means "tag group" now, and a version is not one — the
    accent was being spent on the pill carrying the least information.

    Tagless, because the accent doubles as the Rewards group's color: on a bot
    carrying one, finding it in the stylesheets proves nothing about the version.
    """
    styles = _pill_styles(_BotCard(_meta("Untagged")))

    assert THEME.pill_neutral_bg in styles
    assert f"background: {THEME.accent}" not in styles


# ---------------------------------------------------------------------------
# Following a theme switch
#
# The reported bug: card text kept the old palette, and the search bar and
# category list did not recolor at all until the app was restarted. Only the
# card *frame* appeared to work, because hover was the one thing re-reading
# THEME — so a card recolored when the mouse crossed it and not otherwise.
# ---------------------------------------------------------------------------


@pytest.fixture
def theme():
    before = theme_mod.active_theme()
    yield theme_mod.set_theme
    theme_mod.set_theme_instance(before)


def _switch(qapp, widget, theme, mode: str) -> None:
    """Change the theme the way AppController._apply_theme does, for one widget."""
    theme(mode)
    qapp.sendEvent(widget, QEvent(QEvent.Type.StyleChange))


def test_a_card_recolors_its_text_on_a_theme_switch(qapp, theme):
    theme("light")
    card = _BotCard(_meta("Nightside Flax", description="Flax farm", authors="BartleBot"))
    before = [lbl.styleSheet() for lbl in card.findChildren(QLabel)]

    _switch(qapp, card, theme, "dark")

    after = [lbl.styleSheet() for lbl in card.findChildren(QLabel)]
    assert before != after
    assert THEME.text_muted in card._desc_lbl.styleSheet()


def test_a_card_not_hovered_still_recolors(qapp, theme):
    """The frame used to follow the theme only via enterEvent/leaveEvent, so an
    untouched card stayed stale until the mouse crossed it."""
    theme("light")
    card = _BotCard(_meta("Nightside Flax"))
    before = card.styleSheet()

    _switch(qapp, card, theme, "dark")

    assert card.styleSheet() != before
    assert THEME.surface in card.styleSheet()


def test_hover_survives_a_theme_switch(qapp, theme):
    """Hover is state now, not a separate stylesheet, so a switch while hovered
    must keep the hover colors rather than reverting to the resting ones."""
    theme("light")
    card = _BotCard(_meta("Nightside Flax"))
    card.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))

    _switch(qapp, card, theme, "dark")

    assert THEME.node_fill in card.styleSheet()
    assert THEME.accent in card.styleSheet()


def test_the_panel_chrome_recolors_on_a_theme_switch(qapp, theme):
    """The search bar, category list and card area — none of which rebuild
    themselves, and all of which needed a restart before."""
    theme("light")
    panel = _BotLibraryPanel()
    before = (panel._top_bar.styleSheet(), panel._cat_list.styleSheet(), panel._cards_area.styleSheet())

    _switch(qapp, panel, theme, "dark")

    after = (panel._top_bar.styleSheet(), panel._cat_list.styleSheet(), panel._cards_area.styleSheet())
    assert all(a != b for a, b in zip(before, after))
    assert THEME.surface in panel._top_bar.styleSheet()


def test_the_clear_button_recolors_while_it_exists(qapp, theme):
    """It is rebuilt on every selection change, so it is absent most of the time
    and stale in between."""
    theme("light")
    panel = _BotLibraryPanel()
    panel._set_tag_selection(["Gear"])
    assert panel._clear_btn is not None
    before = panel._clear_btn.styleSheet()

    _switch(qapp, panel, theme, "dark")

    assert panel._clear_btn.styleSheet() != before
