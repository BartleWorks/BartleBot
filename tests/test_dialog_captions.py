"""Tests for identifying in-game modals by their caption text.

The snapshot under test is the real one shipped in bartlebot/data — these are not
fixtures. That is the point: the checked-in captions are the thing that can rot when a
game patch reworks a dialog, so the round-trip test below is what catches it.
"""

import json

import pytest

from bartlebot import dialog_captions as dc

SNAPSHOT = json.loads(dc.CAPTIONS_PATH.read_text(encoding="utf-8"))
CAPTIONS: dict[str, dict[str, str]] = SNAPSHOT["captions"]

# ---------------------------------------------------------------------------
# normalize / literal_runs — pure text handling
# ---------------------------------------------------------------------------


def test_normalize_strips_markup_and_collapses_whitespace():
    assert dc.normalize("<center>Hello   there</center>\r\n friend") == "hello there friend"


def test_normalize_turns_substitution_slots_into_boundaries():
    """Slots are filled in before the text reaches memory, so they can never be matched."""
    assert "\x00" in dc.normalize("Go to $NAME$ now")


def test_literal_runs_are_longest_first():
    runs = dc.literal_runs("short bit $X$ a considerably longer stretch of words")
    assert runs[0] == "a considerably longer stretch of words"


def test_literal_runs_drops_empty_fragments():
    assert dc.literal_runs("$A$$B$") == []


# ---------------------------------------------------------------------------
# identify_caption
# ---------------------------------------------------------------------------


def test_identifies_the_dungeon_exit_warning():
    text = (
        "If you leave a Dungeon you will lose all your progress in the Dungeon and will "
        "have to start over at the beginning.<br>Are you sure you want to leave now?"
    )
    assert dc.identify_caption(text) == dc.INSTANCE_EXIT


def test_identifies_the_friend_busy_dialog():
    """Issue #84: this and the dungeon exit warning share a button layout."""
    assert dc.identify_caption("Your friend is busy right now.") == dc.FRIEND_BUSY


def test_identifies_a_templated_caption_after_substitution():
    """GoToFriendZoneClearBossMark carries $ZONE$ and #1:{their[!n]}, filled in by the game.

    Equality against the template could never match; the literal run either side of a slot
    is what survives.
    """
    live = (
        "Your friend is in a different Realm.  Changing Realms will reset your current "
        "Dungeon.  Are you sure you want to go to your friend in their Krokotopia?"
    )
    assert dc.identify_caption(live) == dc.FRIEND_CROSS_REALM


@pytest.mark.parametrize("text", ["", None, "   ", "A dialog the snapshot has never seen"])
def test_unknown_and_empty_captions_return_none(text):
    """None means 'nothing recognised', so callers fall back rather than guess."""
    assert dc.identify_caption(text) is None


def test_matching_ignores_surrounding_markup_the_game_adds():
    assert dc.identify_caption("<center>Your friend is busy right now.</center>") == dc.FRIEND_BUSY


# ---------------------------------------------------------------------------
# The shipped snapshot
# ---------------------------------------------------------------------------


def test_every_known_key_is_present():
    missing = [key for key in dc.KNOWN_KEYS if key not in CAPTIONS]
    assert not missing, f"regenerate with tools/dump_dialog_captions.py; missing {missing}"


@pytest.mark.parametrize("locale", ["de", "el", "en-US", "es", "fr", "it", "pl"])
def test_every_locale_is_covered(locale):
    """A locale that silently drops out is how the English-only bug comes back."""
    absent = [key for key, by_locale in CAPTIONS.items() if locale not in by_locale]
    assert not absent, f"{locale} missing for {absent}"


def test_every_caption_identifies_as_its_own_key():
    """The round trip. A game patch that reworks a dialog's wording breaks this first."""
    wrong = [
        (key, locale, dc.identify_caption(text))
        for key, by_locale in CAPTIONS.items()
        for locale, text in by_locale.items()
        if dc.identify_caption(text) != key
    ]
    assert not wrong, f"captions matched the wrong key: {wrong}"


def test_no_two_keys_share_a_fingerprint():
    """A shared fingerprint makes one of the two dialogs unreachable."""
    owners: dict[str, set[str]] = {}
    for key, by_locale in CAPTIONS.items():
        for text in by_locale.values():
            if fingerprint := dc._fingerprint(text):
                owners.setdefault(fingerprint, set()).add(key)
    shared = {f: sorted(keys) for f, keys in owners.items() if len(keys) > 1}
    assert not shared, f"ambiguous fingerprints: {shared}"


def test_a_missing_snapshot_does_not_crash_the_caller(tmp_path, monkeypatch):
    """A bad install should degrade to the button fallback, not take the bot down."""
    monkeypatch.setattr(dc, "CAPTIONS_PATH", tmp_path / "nope.json")
    dc._fingerprints.cache_clear()
    try:
        assert dc.identify_caption("Your friend is busy right now.") is None
    finally:
        dc._fingerprints.cache_clear()


# ---------------------------------------------------------------------------
# The .lang parser
#
# The middle line of a record is a translator description that is usually, but not
# always, blank. A parser that required it blank silently dropped every documented
# key — which is how GoToFriendFail came to be written off as unusable, its
# description mistaken for its value.
# ---------------------------------------------------------------------------


def _lang_bytes(*lines: str) -> bytes:
    return ("1:GUI\r\n" + "\r\n".join(lines)).encode("utf-16-le")


def test_parse_lang_reads_a_plain_record():
    from tools.dump_dialog_captions import parse_lang

    raw = _lang_bytes("ConnectionLost", "", "Connection Lost!", "")
    assert parse_lang(raw)["ConnectionLost"] == "Connection Lost!"


def test_parse_lang_reads_a_record_whose_description_is_filled_in():
    from tools.dump_dialog_captions import parse_lang

    raw = _lang_bytes(
        "GoToFriendFail",
        "The message that appears when you can't go to a friend.",
        "You failed to teleport to this friend.",
        "",
    )
    table = parse_lang(raw)

    assert table["GoToFriendFail"] == "You failed to teleport to this friend."
    # The description must never be mistaken for the value.
    assert "The message that appears" not in table["GoToFriendFail"]


def test_parse_lang_keeps_records_aligned_across_mixed_descriptions():
    """A described record must not shift every record after it."""
    from tools.dump_dialog_captions import parse_lang

    raw = _lang_bytes(
        "First",
        "",
        "first value",
        "Second",
        "a description",
        "second value",
        "Third",
        "",
        "third value",
        "",
    )
    table = parse_lang(raw)

    assert table["First"] == "first value"
    assert table["Second"] == "second value"
    assert table["Third"] == "third value"
