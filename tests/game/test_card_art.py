"""Tests for bartlebot.game.card_art — finding extracted game art, and not finding it.

Every test points ``APPDATA`` at a tmp directory, because that is what
``shared/vendor.product_dir`` reads and so what decides where the cache is. The
module caches the index, so the fixture clears it either side.
"""

from __future__ import annotations

import json

import pytest

from bartlebot.game import card_art


@pytest.fixture
def art_home(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    card_art.forget_cache()
    yield tmp_path
    card_art.forget_cache()


def _write_index(images: dict[str, str], *, files: tuple[str, ...] = ()) -> None:
    card_art.spells_dir().mkdir(parents=True, exist_ok=True)
    card_art.index_path().write_text(json.dumps({"images": images}), encoding="utf-8")
    for name in files:
        (card_art.spells_dir() / name).write_bytes(b"not really a png, only its presence is read")


def test_a_badge_icon_is_found_under_its_own_namespace(art_home):
    """``badge_`` and ``world_`` are separate: the badge page spells Dragonspyre the way
    the nav map does not."""
    card_art.icons_dir().mkdir(parents=True, exist_ok=True)
    (card_art.icons_dir() / "badge_4dungeons.png").write_bytes(b"x")
    card_art.index_path().write_text(
        json.dumps({"icons": {"badge_4dungeons": "badge_4dungeons.png"}}), encoding="utf-8"
    )

    assert card_art.badge_icon_name("4Dungeons") == "badge_4dungeons"
    assert card_art.badge_icon_path("4Dungeons") == card_art.icons_dir() / "badge_4dungeons.png"
    assert card_art.badge_icon_path("Crafting") is None


def test_nothing_extracted_reads_as_no_art(art_home):
    assert not card_art.is_available()
    assert card_art.art_path("Tempest") is None


def test_a_template_in_the_index_finds_its_file(art_home):
    _write_index({"Tempest": "Spell_Storm_Tempest.png"}, files=("Spell_Storm_Tempest.png",))

    path = card_art.art_path("Tempest")

    assert path is not None
    assert path.name == "Spell_Storm_Tempest.png"
    assert card_art.is_available()


def test_templates_sharing_one_image_both_resolve(art_home):
    # 18,000 templates share ~1,300 images: a tiered variant uses its base spell's
    # texture, so the index maps many names onto one file rather than copying it.
    _write_index(
        {"Storm Lord": "Spell_Storm_StormLord.png", "Storm Lord - T05 - A": "Spell_Storm_StormLord.png"},
        files=("Spell_Storm_StormLord.png",),
    )

    assert card_art.art_path("Storm Lord") == card_art.art_path("Storm Lord - T05 - A")


def test_a_template_the_index_does_not_name_reads_as_missing(art_home):
    _write_index({"Tempest": "Spell_Storm_Tempest.png"}, files=("Spell_Storm_Tempest.png",))

    assert card_art.art_path("Some Card With No Art") is None


def test_an_index_entry_whose_file_is_gone_reads_as_missing(art_home):
    # The index is written once; the directory beside it is an ordinary cache the user
    # can delete part of. A name in the index is not a promise the file is still there.
    _write_index({"Tempest": "Spell_Storm_Tempest.png"})

    assert card_art.art_path("Tempest") is None


def test_an_unreadable_index_reads_as_no_art(art_home):
    card_art.art_dir().mkdir(parents=True, exist_ok=True)
    card_art.index_path().write_text("{not json at all", encoding="utf-8")

    assert not card_art.is_available()
    assert card_art.art_path("Tempest") is None


def test_an_index_with_no_images_map_reads_as_no_art(art_home):
    card_art.art_dir().mkdir(parents=True, exist_ok=True)
    card_art.index_path().write_text(json.dumps({"generated": "2026-09-15"}), encoding="utf-8")

    assert not card_art.is_available()


def test_an_icon_in_the_index_finds_its_file(art_home):
    card_art.icons_dir().mkdir(parents=True, exist_ok=True)
    card_art.index_path().write_text(json.dumps({"icons": {"archmastery": "archmastery.png"}}), encoding="utf-8")
    (card_art.icons_dir() / "archmastery.png").write_bytes(b"only its presence is read")

    path = card_art.icon_path("archmastery")

    assert path is not None
    assert path.name == "archmastery.png"


def test_an_art_cache_written_before_icons_existed_reads_as_no_icons(art_home):
    """The spell art still resolves: a missing ``icons`` map is an older cache, not a
    broken one, and the surfaces using icons keep the text they replaced."""
    _write_index({"Tempest": "Spell_Storm_Tempest.png"}, files=("Spell_Storm_Tempest.png",))

    assert card_art.art_path("Tempest") is not None
    assert card_art.icon_path("archmastery") is None


def test_an_icon_whose_file_is_gone_reads_as_missing(art_home):
    card_art.art_dir().mkdir(parents=True, exist_ok=True)
    card_art.index_path().write_text(json.dumps({"icons": {"archmastery": "archmastery.png"}}), encoding="utf-8")

    assert card_art.icon_path("archmastery") is None


# -- world emblems -----------------------------------------------------------


def test_a_world_icon_key_is_case_folded():
    """The game spells this world three ways across its own files, so the key cannot be
    the caller's casing: ``DragonSpire`` is the nav key, ``Dragonspire`` the texture."""
    assert card_art.world_icon_name("DragonSpire") == card_art.world_icon_name("Dragonspire")
    assert card_art.world_icon_name("MooShu") == "world_mooshu"


def test_a_world_in_the_index_finds_its_emblem(art_home):
    card_art.icons_dir().mkdir(parents=True, exist_ok=True)
    card_art.index_path().write_text(
        json.dumps({"icons": {"world_wizardcity": "world_wizardcity.png"}}), encoding="utf-8"
    )
    (card_art.icons_dir() / "world_wizardcity.png").write_bytes(b"only its presence is read")

    path = card_art.world_icon_path("WizardCity")

    assert path is not None
    assert path.name == "world_wizardcity.png"


def test_a_world_with_no_extracted_emblem_reads_as_missing(art_home):
    """The ordinary case: most installs never run the extractor."""
    assert card_art.world_icon_path("WizardCity") is None


def test_the_read_side_does_not_substitute_a_dungeon_badge(art_home):
    """Which "worlds" are really dungeons is the extractor's table to state -- it points
    those keys at the dungeon texture. A fallback here would be a second place deciding
    the same thing, and the two would drift the first time a dungeon group was added."""
    card_art.icons_dir().mkdir(parents=True, exist_ok=True)
    card_art.index_path().write_text(json.dumps({"icons": {"dungeon": "dungeon.png"}}), encoding="utf-8")
    (card_art.icons_dir() / "dungeon.png").write_bytes(b"only its presence is read")

    assert card_art.world_icon_path("G14_DM") is None


def test_forgetting_the_cache_picks_up_a_later_extraction(art_home):
    assert not card_art.is_available()

    _write_index({"Tempest": "Spell_Storm_Tempest.png"}, files=("Spell_Storm_Tempest.png",))
    assert not card_art.is_available(), "the empty answer should still be cached"

    card_art.forget_cache()
    assert card_art.is_available()
