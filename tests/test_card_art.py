"""Tests for bartlebot.card_art — finding extracted card art, and not finding it.

Every test points ``APPDATA`` at a tmp directory, because that is what
``shared/vendor.product_dir`` reads and so what decides where the cache is. The
module caches the index, so the fixture clears it either side.
"""

from __future__ import annotations

import json

import pytest

from bartlebot import card_art


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


def test_forgetting_the_cache_picks_up_a_later_extraction(art_home):
    assert not card_art.is_available()

    _write_index({"Tempest": "Spell_Storm_Tempest.png"}, files=("Spell_Storm_Tempest.png",))
    assert not card_art.is_available(), "the empty answer should still be cached"

    card_art.forget_cache()
    assert card_art.is_available()
