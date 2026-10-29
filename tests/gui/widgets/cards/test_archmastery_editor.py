"""Tests for the archmastery control on the Deck Presets page.

Three rules carry this widget: the radios write the three-state override straight onto
the deck (the working copy the dialog's Save commits), the caption names what the app
setting will do and *only* while the deck defers to it, and a deck with no captured
school gets no controls at all.

The icons come out of a tmp art cache in distinguishable colors, the way
``test_archmastery_badge.py`` does, which is how "drew the school" is told from "drew
the universal mark".
"""

from __future__ import annotations

import json

import pytest
from bartleby.shared.strategy_models import DeckData
from PyQt6.QtGui import QColor, QIcon, QPixmap

from bartlebot.combat import deck_io
from bartlebot.game import card_art
from bartlebot.gui.widgets.cards.archmastery_editor import (
    APP_SETTING_OFF,
    APP_SETTING_ON,
    NO_SCHOOL,
    ArchmasteryEditor,
)

COLORS = {
    card_art.ARCHMASTERY_ICON: QColor("red"),
    card_art.UNIVERSAL_ICON: QColor("blue"),
    "school_fire": QColor("green"),
}


@pytest.fixture
def art_home(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    card_art.forget_cache()
    yield tmp_path
    card_art.forget_cache()


@pytest.fixture
def app_setting(monkeypatch):
    """Pin the app setting the None mode resolves through, without touching settings.json."""

    def _set(value: bool) -> None:
        monkeypatch.setattr(deck_io, "_app_default_apply_archmastery", lambda: value)

    _set(False)
    return _set


def _extract_icons(keys=tuple(COLORS)) -> None:
    card_art.icons_dir().mkdir(parents=True, exist_ok=True)
    icons = {key: f"{key}.png" for key in keys}
    card_art.index_path().write_text(json.dumps({"images": {}, "icons": icons}), encoding="utf-8")
    for key in keys:
        pixmap = QPixmap(32, 32)
        pixmap.fill(COLORS[key])
        assert pixmap.save(str(card_art.icons_dir() / f"{key}.png"), "PNG")


def _color_of(icon: QIcon) -> QColor:
    image = icon.pixmap(16).toImage()
    return image.pixelColor(image.width() // 2, image.height() // 2)


def _button(editor: ArchmasteryEditor, label: str):
    return next(button for button, _value in editor._buttons if button.text() == label)


def _click(editor: ArchmasteryEditor, label: str) -> None:
    """The radio's own click, so the widget's slot runs exactly as it does in the app."""
    _button(editor, label).click()


# -- what the radios show ----------------------------------------------------


def test_the_stored_mode_is_the_one_checked(qapp, app_setting):
    editor = ArchmasteryEditor()

    editor.load(DeckData(normal={"Test Card": 1}, archmastery_school="Fire", apply_archmastery=False))

    assert editor.mode() is False
    assert _button(editor, "Never set").isChecked()


def test_a_deck_with_no_opinion_shows_the_app_setting_mode(qapp, app_setting):
    editor = ArchmasteryEditor()

    editor.load(DeckData(normal={"Test Card": 1}, archmastery_school="Fire"))

    assert editor.mode() is None
    assert _button(editor, "Use app setting").isChecked()


# -- what a click does -------------------------------------------------------


def test_clicking_a_mode_writes_it_onto_the_deck(qapp, app_setting):
    deck = DeckData(normal={"Test Card": 1}, archmastery_school="Fire")
    editor = ArchmasteryEditor()
    editor.load(deck)
    seen = []
    editor.changed.connect(lambda: seen.append(deck.apply_archmastery))

    _click(editor, "Always set")

    assert deck.apply_archmastery is True
    assert seen == [True]


def test_re_picking_the_stored_mode_reports_no_change(qapp, app_setting):
    """Dirtiness is derived from the fields, and a change the user is warned about
    losing has to be a change they made."""
    deck = DeckData(normal={"Test Card": 1}, archmastery_school="Fire", apply_archmastery=True)
    editor = ArchmasteryEditor()
    editor.load(deck)
    seen = []
    editor.changed.connect(lambda: seen.append(1))

    _click(editor, "Always set")

    assert seen == []
    assert deck.apply_archmastery is True


def test_loading_a_deck_never_writes_to_it(qapp, app_setting):
    deck = DeckData(normal={"Test Card": 1}, archmastery_school="Fire")
    editor = ArchmasteryEditor()
    seen = []
    editor.changed.connect(lambda: seen.append(1))

    editor.load(deck)

    assert deck.apply_archmastery is None
    assert seen == []


# -- the caption -------------------------------------------------------------


def test_the_caption_names_what_the_app_setting_will_do(qapp, app_setting):
    app_setting(True)
    editor = ArchmasteryEditor()

    editor.load(DeckData(normal={"Test Card": 1}, archmastery_school="Fire"))

    assert editor.caption_text == APP_SETTING_ON


def test_the_caption_follows_the_app_setting_when_it_is_off(qapp, app_setting):
    app_setting(False)
    editor = ArchmasteryEditor()

    editor.load(DeckData(normal={"Test Card": 1}, archmastery_school="Fire"))

    assert editor.caption_text == APP_SETTING_OFF


def test_a_pinned_mode_has_no_caption(qapp, app_setting):
    """The radio already says what will happen; a sentence under it is the same fact
    twice."""
    editor = ArchmasteryEditor()

    editor.load(DeckData(normal={"Test Card": 1}, archmastery_school="Fire", apply_archmastery=True))

    assert editor.caption_text == ""


def test_pinning_a_mode_drops_the_caption_on_the_spot(qapp, app_setting):
    app_setting(True)
    editor = ArchmasteryEditor()
    editor.load(DeckData(normal={"Test Card": 1}, archmastery_school="Fire"))

    _click(editor, "Never set")

    assert editor.caption_text == ""


# -- no school ---------------------------------------------------------------


def test_a_deck_with_no_school_gets_no_controls(qapp, app_setting):
    editor = ArchmasteryEditor()

    editor.load(DeckData(normal={"Test Card": 1}))

    assert editor._modes.isHidden()
    assert editor._school.text() == NO_SCHOOL


def test_no_deck_at_all_says_nothing(qapp, app_setting):
    editor = ArchmasteryEditor()
    editor.load(DeckData(normal={"Test Card": 1}, archmastery_school="Fire"))

    editor.clear()

    assert editor.deck is None
    assert editor._school.isHidden()
    assert editor._modes.isHidden()


# -- the icons ---------------------------------------------------------------


def test_the_school_medallion_is_drawn_when_the_school_will_be_set(art_home, qapp, app_setting):
    _extract_icons()
    editor = ArchmasteryEditor()

    editor.load(DeckData(normal={"Test Card": 1}, archmastery_school="Fire", apply_archmastery=True))

    assert not editor._icons.isHidden()
    assert _color_of(editor._icons._school.icon()) == COLORS["school_fire"]


def test_the_medallion_follows_the_mode_the_user_just_picked(art_home, qapp, app_setting):
    """A Fire medallion on a deck whose apply leaves archmastery alone is a claim about
    the wizard the deck does not make."""
    _extract_icons()
    editor = ArchmasteryEditor()
    editor.load(DeckData(normal={"Test Card": 1}, archmastery_school="Fire", apply_archmastery=True))

    _click(editor, "Never set")

    assert _color_of(editor._icons._school.icon()) == COLORS[card_art.UNIVERSAL_ICON]


def test_the_school_is_named_in_words_whether_or_not_there_are_icons(art_home, qapp, app_setting):
    """The medallion is the universal mark for a deck that will not set its school, so
    without the word beside it the deck would not say anywhere which school it holds."""
    _extract_icons()
    editor = ArchmasteryEditor()

    editor.load(DeckData(normal={"Test Card": 1}, archmastery_school="Fire", apply_archmastery=False))

    assert editor._school.text() == "Fire"


def test_the_icons_are_absent_on_an_install_that_never_extracted_them(art_home, qapp, app_setting):
    editor = ArchmasteryEditor()

    editor.load(DeckData(normal={"Test Card": 1}, archmastery_school="Fire", apply_archmastery=True))

    assert editor._icons.isHidden()
    assert editor._school.text() == "Fire", "the school still has to be legible"
    assert not editor._modes.isHidden(), "and still editable"


def test_new_art_shows_the_icons_in_place_of_nothing(qapp, art_home, app_setting):
    """Art extracted while the editor is up (#239): the badge appears beside the school."""
    from bartleby.qt.art_signal import announce_art_changed  # noqa: PLC0415

    editor = ArchmasteryEditor()
    editor.load(DeckData(normal={"Test Card": 1}, archmastery_school="Fire"))
    assert editor._icons.isHidden()

    _extract_icons()
    card_art.forget_cache()
    announce_art_changed()

    assert not editor._icons.isHidden()
    assert editor._icons.has_icons()
