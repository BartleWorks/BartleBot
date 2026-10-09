"""Tests for bartlebot.game.badge_categories, against the checked-in snapshot."""

from __future__ import annotations

from bartlebot.game import badge_categories


def test_the_game_order_is_kept():
    keys = badge_categories.keys()

    assert keys.index("WizardCity") < keys.index("Khrysalis") < keys.index("Wysteria") < keys.index("Aquila")
    assert keys.index("Empyrea") < keys.index("Arcanum")
    assert keys.index("Wallaru") < keys.index("Selenopolis") < keys.index("Darkmoor")


def test_a_caption_drops_the_trailing_badges():
    assert badge_categories.label("WizardCity") == "Wizard City"
    assert badge_categories.label("4Dungeons") == "Four Dungeons"


def test_no_category_captions_as_an_empty_string():
    assert all(badge_categories.label(key) for key in badge_categories.keys())


def test_an_unknown_key_captions_as_itself():
    assert badge_categories.label("NotACategory") == "NotACategory"
    assert badge_categories.icon_texture("NotACategory") is None


def test_every_category_has_an_icon_path():
    assert badge_categories.icon_texture("Gardening") == "GUI/BadgeCategory/Badge_Category_Gardening.dds"
    assert all(badge_categories.icon_texture(key) for key in badge_categories.keys())
