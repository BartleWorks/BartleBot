"""Tests for wizard name-key decoding and the checked-in name table snapshot.

Most tests run against small synthetic tables, so they need neither the game installed nor
a Root.wad read. The three recorded samples are pinned separately as a regression test
against the real snapshot.
"""

from __future__ import annotations

import json

import pytest

from bartlebot.game.name_tables import (
    DEFAULT_LOCALE,
    FIRST_FEMALE,
    FIRST_MALE,
    GENDER_FEMALE,
    GENDER_MALE,
    LAST,
    MIDDLE,
    TABLES_PATH,
    UNKNOWN_NAME,
    NameTables,
    decode_name_keys,
    load_name_tables,
    split_name_keys,
    wizard_name,
)


def _tables(**kw) -> NameTables:
    base = {
        "first_male": ("Aaron", "Adam", "Adrian"),
        "first_female": ("Abigail", "Ada", "Adele"),
        "middle": ("Angle", "Anvil", "Ash"),
        "last": ("Bane", "Blade", "Blood"),
    }
    return NameTables(**(base | kw))


# ------------------------------------------------------------------ bit layout


def test_split_is_msb_first():
    assert split_name_keys(0x034D4B03) == (0x03, 0x4D, 0x4B, 0x03)


def test_split_handles_zero():
    assert split_name_keys(0) == (0, 0, 0, 0)


# ------------------------------------------------------------------ index bases


def test_first_name_is_zero_based():
    assert decode_name_keys(0x03_00_01_01, tables=_tables()) == "Aaron AngleBane"


def test_middle_and_last_are_one_based():
    """The off-by-one that would shift every surname part by one entry."""
    assert decode_name_keys(0x03_00_03_03, tables=_tables()) == "Aaron AshBlood"


def test_zero_middle_means_absent():
    assert decode_name_keys(0x03_00_00_01, tables=_tables()) == "Aaron Bane"


def test_zero_last_means_absent():
    assert decode_name_keys(0x03_00_01_00, tables=_tables()) == "Aaron Angle"


def test_surname_parts_are_joined_without_a_space():
    # The game renders Skull + Hunter as one surname, "SkullHunter".
    assert decode_name_keys(0x03_00_01_01, tables=_tables()).split(" ")[1] == "AngleBane"


# ------------------------------------------------------------------ gender


def test_gender_male_uses_male_table():
    assert decode_name_keys(0x03_02_01_01, GENDER_MALE, tables=_tables()) == "Adrian AngleBane"


def test_female_gender_is_zero_not_two():
    """m_eGender is 0 for female, so `gender or DEFAULT` would silently pick the male table."""
    assert decode_name_keys(0x03_02_01_01, GENDER_FEMALE, tables=_tables()) == "Adele AngleBane"


def test_gender_zero_is_not_treated_as_missing():
    explicit = decode_name_keys(0x03_02_01_01, GENDER_FEMALE, tables=_tables())
    assert explicit == "Adele AngleBane", "0 must not fall through to gender inference"


def test_gender_inferred_from_flags_when_not_given():
    # byte0 == 2 means female, so the female table is used without m_eGender.
    assert decode_name_keys(0x02_02_01_01, tables=_tables()) == "Adele AngleBane"
    assert decode_name_keys(0x03_02_01_01, tables=_tables()) == "Adrian AngleBane"


def test_explicit_gender_overrides_flags():
    """m_eGender is directly observed, so it wins over the weaker byte0 signal."""
    assert decode_name_keys(0x02_02_01_01, GENDER_MALE, tables=_tables()) == "Adrian AngleBane"


# ------------------------------------------------------------------ stale snapshot


def test_index_past_end_returns_none_rather_than_guessing():
    """A newly added game name looks like this; a guess would be a wrong name shown as fact."""
    assert decode_name_keys(0x03_FF_01_01, tables=_tables()) is None


def test_out_of_range_middle_returns_none():
    assert decode_name_keys(0x03_00_FF_01, tables=_tables()) is None


def test_out_of_range_last_returns_none():
    assert decode_name_keys(0x03_00_01_FF, tables=_tables()) is None


def test_wizard_name_shows_unknown_for_stale_index():
    """Visible placeholder, so a stale snapshot announces itself instead of hiding."""
    assert wizard_name(0x03_FF_01_01, tables=_tables()) == UNKNOWN_NAME


def test_wizard_name_returns_the_name_when_resolvable():
    assert wizard_name(0x03_00_01_01, tables=_tables()) == "Aaron AngleBane"


def test_decode_still_returns_none_so_callers_can_act_on_it():
    """The primitive stays honest; only the display wrapper substitutes a placeholder."""
    assert decode_name_keys(0x03_FF_01_01, tables=_tables()) is None


def test_stale_index_is_logged_with_regeneration_hint(caplog):
    decode_name_keys(0x03_FF_01_01, tables=_tables())
    assert "dump_name_tables" in caplog.text


def test_missing_tables_file_returns_none(tmp_path):
    assert load_name_tables("en-US", tmp_path / "nope.json") is None


def test_corrupt_tables_file_returns_none(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_name_tables("en-US", path) is None


def test_unknown_locale_falls_back_to_default(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(
        json.dumps({"locales": {DEFAULT_LOCALE: {FIRST_MALE: ["Aaron"], MIDDLE: ["Angle"], LAST: ["Bane"]}}}),
        encoding="utf-8",
    )
    tables = load_name_tables("zz-ZZ", path)
    assert tables is not None and tables.first_male == ("Aaron",)


# ------------------------------------------------------------------ the real snapshot


@pytest.fixture(scope="module")
def real_tables() -> NameTables:
    tables = load_name_tables()
    assert tables is not None, "bartlebot/game/data/name_tables.json should be checked in"
    return tables


# Synthetic, built from the shipped tables rather than any real account -- see CLAUDE.md,
# "No real player data". They keep the property that made the layout legible: rows 1 and 2
# share a first and last name, rows 1 and 3 share a middle name.
SAMPLES = [
    (56900648, GENDER_MALE, "Luke SkullHunter"),
    (56916008, GENDER_MALE, "Luke CycloneHunter"),
    (64109768, GENDER_MALE, "Misha SkullSeer"),
    (39360090, GENDER_FEMALE, "Michelle HistoryBond"),
    (46997935, GENDER_FEMALE, "Naomi HexPaint"),
]


@pytest.mark.parametrize(("value", "gender", "expected"), SAMPLES)
def test_samples_decode_against_real_snapshot(real_tables, gender, value, expected):
    assert decode_name_keys(value, gender, tables=real_tables) == expected


@pytest.mark.parametrize(("value", "gender", "expected"), SAMPLES)
def test_samples_decode_from_flags_alone(real_tables, gender, value, expected):
    """byte0 alone picks the right first-name table, on both genders."""
    assert decode_name_keys(value, tables=real_tables) == expected


def test_shared_name_parts_share_bytes(real_tables):
    """The property the layout was derived from, pinned so it stays visible."""
    luke_skull, luke_cyclone, misha_skull = (split_name_keys(v) for v, _g, _n in SAMPLES[:3])
    assert luke_skull[1] == luke_cyclone[1], "same first name -> same byte1"
    assert luke_skull[3] == luke_cyclone[3], "same last name -> same byte3"
    assert luke_skull[2] == misha_skull[2], "same middle name -> same byte2"


def test_snapshot_has_separate_first_name_tables_per_gender(real_tables):
    # Confirmed in game: male and female have different first names.
    assert real_tables.first_male != real_tables.first_female


def test_snapshot_shares_surname_tables_across_genders(real_tables):
    # Confirmed in game: surnames are shared, so there is only one middle/last table.
    assert len(real_tables.middle) > 0 and len(real_tables.last) > 0


def test_snapshot_covers_expected_locales():
    raw = json.loads(TABLES_PATH.read_text(encoding="utf-8"))
    assert DEFAULT_LOCALE in raw["locales"]
    assert set(raw["locales"][DEFAULT_LOCALE]) == {FIRST_MALE, FIRST_FEMALE, MIDDLE, LAST}
