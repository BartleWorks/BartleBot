"""Tests for the bot tag vocabulary and its normalizer.

The normalizer is deliberately tolerant: it runs on every bot load, so a bad value
has to cost one tag rather than the whole file.
"""

import pytest

from bartlebot.bot.schema import BotConfig
from bartlebot.bot_tags import BOT_TAGS, TAG_GROUPS, normalize_tags


def test_bot_tags_is_the_flattened_groups():
    assert BOT_TAGS == tuple(t for group in TAG_GROUPS.values() for t in group)


def test_no_tag_appears_in_two_groups():
    """A tag in two groups would render two chips that filter identically."""
    assert len(BOT_TAGS) == len(set(BOT_TAGS))


def test_known_tags_survive():
    assert normalize_tags(["Boss", "Gear"]) == ["Gear", "Boss"]


def test_output_is_in_vocabulary_order_not_input_order():
    """Storage order is fixed so the editor cannot produce diff noise by reordering."""
    assert normalize_tags(["Gathering", "Boss", "Gear"]) == ["Gear", "Boss", "Gathering"]


def test_duplicates_collapse():
    assert normalize_tags(["Gear", "Gear", "Boss"]) == ["Gear", "Boss"]


def test_unknown_tags_are_dropped_not_raised():
    """A typo costs one tag. Raising here would make the bot fail to open at all."""
    assert normalize_tags(["Gear", "gear", "Sparkles"]) == ["Gear"]


@pytest.mark.parametrize("raw", [None, "Gear", 42, {"Gear": True}])
def test_non_list_input_is_tolerated(raw):
    assert normalize_tags(raw) == []


def test_non_string_entries_are_skipped():
    assert normalize_tags(["Gear", 7, None]) == ["Gear"]


def _minimal_bot(**extra) -> dict:
    return {
        "name": "T",
        "initial": "s1",
        "recovery_state": "s1",
        "states": [{"id": "s1", "node_type": "sleep"}],
        "transitions": [],
        **extra,
    }


def test_a_bot_with_a_bad_tag_still_loads():
    config = BotConfig.model_validate(_minimal_bot(tags=["Boss", "NotATag"]))

    assert config.tags == ["Boss"]


def test_tags_default_to_empty():
    assert BotConfig.model_validate(_minimal_bot()).tags == []


def test_tags_survive_a_round_trip(tmp_path):
    """UndoManager round-trips through model_dump/model_validate on every edit."""
    config = BotConfig.model_validate(_minimal_bot(tags=["Gathering", "Reagents"]))
    path = tmp_path / "b.json"
    config.save(path)

    assert BotConfig.load(path).tags == ["Reagents", "Gathering"]


def test_every_tag_has_a_description():
    """The picker shows one per row; a blank line there looks like a rendering bug."""
    from bartlebot.bot_tags import TAG_DESCRIPTIONS  # noqa: PLC0415

    missing = [t for t in BOT_TAGS if not TAG_DESCRIPTIONS.get(t)]

    assert not missing, f"tags with no description: {missing}"


def test_no_description_is_orphaned():
    """A description for a tag that no longer exists is a rename someone half-did."""
    from bartlebot.bot_tags import TAG_DESCRIPTIONS  # noqa: PLC0415

    orphans = sorted(set(TAG_DESCRIPTIONS) - set(BOT_TAGS))

    assert not orphans, f"descriptions with no tag: {orphans}"
