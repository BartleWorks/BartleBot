"""Unit tests for the realm table and its snapshot."""

import json

from bartlebot.game import realms


def test_the_snapshot_lists_the_realms_with_an_english_name_each():
    keys = realms.realm_keys()

    assert len(keys) >= 30  # 35 as of this snapshot; the game adds and retires a few
    assert keys == sorted(keys)
    assert all(realms.display_names(key)[0] for key in keys)


def test_every_realm_has_every_locale():
    """Unlike worlds, every realm is translated everywhere; a gap would be a dump bug."""
    snapshot = json.loads(realms.REALM_NAMES_PATH.read_text(encoding="utf-8"))["realms"]

    assert all(set(by_locale) == set(realms.LOCALES) for by_locale in snapshot.values())


def test_a_retired_realm_is_not_offered():
    """Wu has a key in every locale but no display text in en-US: the list does not show it."""
    assert not realms.is_known("Wu")


def test_display_names_lead_with_english_and_carry_other_locales():
    names = realms.display_names("Balestrom")

    assert names[0] == "Balestrom"
    assert "Hagelkorn" in names  # de
    assert realms.label("Balestrom") == "Balestrom"


def test_matches_any_locale_case_insensitively():
    assert realms.matches("Balestrom", "Balestrom")
    assert realms.matches("Balestrom", "  hagelkorn ")
    assert not realms.matches("Balestrom", "Wraith")
    assert not realms.matches("Balestrom", "")


def test_key_for_reverses_a_row_in_any_locale():
    assert realms.key_for("Hagelkorn") == "Balestrom"
    assert realms.key_for("wraith") == "Wraith"
    assert realms.key_for("Not A Realm") is None
    assert realms.key_for("") is None


def test_unknown_realm_yields_nothing_rather_than_raising():
    assert realms.display_names("NotARealm") == []
    assert realms.label("NotARealm") == "NotARealm"
    assert not realms.is_known("NotARealm")


def test_yes_no_reads_every_locale_and_strips_markup():
    """The Greek Yes carries a <scale> tag in the lang file; memory may show it too."""
    assert realms.yes_no("Yes") is True
    assert realms.yes_no("Nein") is False
    assert realms.yes_no("<scale;0.90>Ναι!") is True
    assert realms.yes_no("Όχι") is False
    assert realms.yes_no("Tak") is True


def test_yes_no_refuses_to_guess():
    """A blind toggle from an unreadable value would turn the option off as often as on."""
    assert realms.yes_no("") is None
    assert realms.yes_no("Maybe") is None
