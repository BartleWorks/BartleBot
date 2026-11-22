"""Tests for bartlebot.game.bot_art — the world and badge icons, and where BartleBot's art is.

Core's tests/runtime/game/test_card_art.py has the read side itself. The drop and pin icons
are in test_drop_icons.py.
"""

from __future__ import annotations

import json

import pytest
from bartleby.runtime.game import card_art
from bartleby.shared.vendor import product_dir

from bartlebot.game import bot_art
from bartlebot.packaging.product import NAME as PRODUCT


@pytest.fixture
def art_home(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    card_art.forget_cache()
    yield tmp_path
    card_art.forget_cache()


def test_bartlebots_art_is_where_it_always_was(art_home):
    """The extractor's folder: the read side moved to core, and what is written did not."""
    assert card_art.own_art_dir() == product_dir(PRODUCT) / "art"


def test_a_badge_icon_is_found_under_its_own_namespace(art_home):
    """``badge_`` and ``world_`` are separate: the badge page spells Dragonspyre the way
    the nav map does not."""
    card_art.icons_dir().mkdir(parents=True, exist_ok=True)
    (card_art.icons_dir() / "badge_4dungeons.png").write_bytes(b"x")
    card_art.index_path().write_text(
        json.dumps({"icons": {"badge_4dungeons": "badge_4dungeons.png"}}), encoding="utf-8"
    )

    assert bot_art.badge_icon_name("4Dungeons") == "badge_4dungeons"
    assert bot_art.badge_icon_path("4Dungeons") == card_art.icons_dir() / "badge_4dungeons.png"
    assert bot_art.badge_icon_path("Crafting") is None


# -- world emblems -----------------------------------------------------------


def test_a_world_icon_key_is_case_folded():
    """The game spells this world three ways across its own files, so the key cannot be
    the caller's casing: ``DragonSpire`` is the nav key, ``Dragonspire`` the texture."""
    assert bot_art.world_icon_name("DragonSpire") == bot_art.world_icon_name("Dragonspire")
    assert bot_art.world_icon_name("MooShu") == "world_mooshu"


def test_a_world_in_the_index_finds_its_emblem(art_home):
    card_art.icons_dir().mkdir(parents=True, exist_ok=True)
    card_art.index_path().write_text(
        json.dumps({"icons": {"world_wizardcity": "world_wizardcity.png"}}), encoding="utf-8"
    )
    (card_art.icons_dir() / "world_wizardcity.png").write_bytes(b"only its presence is read")

    path = bot_art.world_icon_path("WizardCity")

    assert path is not None
    assert path.name == "world_wizardcity.png"


def test_a_world_with_no_extracted_emblem_reads_as_missing(art_home):
    """The ordinary case: most installs never run the extractor."""
    assert bot_art.world_icon_path("WizardCity") is None


def test_the_read_side_does_not_substitute_a_dungeon_badge(art_home):
    """Which "worlds" are really dungeons is the extractor's table to state -- it points
    those keys at the dungeon texture. A fallback here would be a second place deciding
    the same thing, and the two would drift the first time a dungeon group was added."""
    card_art.icons_dir().mkdir(parents=True, exist_ok=True)
    card_art.index_path().write_text(json.dumps({"icons": {"dungeon": "dungeon.png"}}), encoding="utf-8")
    (card_art.icons_dir() / "dungeon.png").write_bytes(b"only its presence is read")

    assert bot_art.world_icon_path("G14_DM") is None
