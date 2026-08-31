"""Tests for the bot-tag picker shared by Bot Info and the library panel."""

import sys

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication

from bartlebot.bot.schema import BotConfig
from bartlebot.bot_tags import BOT_TAGS, TAG_GROUPS
from bartlebot.gui.sidebar.sections.bot_info_section import _PLACEHOLDER, BotInfoSection, tags_summary
from bartlebot.gui.widgets.picker_popup import _RowWidget
from bartlebot.gui.widgets.tag_picker import TagPickerButton, group_accent, tag_group, tag_sections


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


# ---------------------------------------------------------------------------
# Section building (pure)
# ---------------------------------------------------------------------------


def test_sections_mirror_the_vocabulary():
    sections = tag_sections()

    assert [title for title, _ in sections] == list(TAG_GROUPS)
    assert [r.key for _, rows in sections for r in rows] == list(BOT_TAGS)


def test_every_row_carries_a_description():
    """The description is the tagging rule, shown where someone tagging will read it."""
    blank = [r.key for _, rows in tag_sections() for r in rows if not r.description]

    assert not blank, f"tags with no description: {blank}"


def test_counts_are_omitted_when_not_supplied():
    """The editor has no library to count, and '0' there would be a lie."""
    assert all(r.meta == "" for _, rows in tag_sections() for r in rows)


def test_counts_appear_when_supplied():
    rows = {r.key: r.meta for _, rows in tag_sections({"Gear": 49}) for r in rows}

    assert rows["Gear"] == "49"


def test_a_tag_with_no_bots_still_shows_a_zero():
    """A missing row reads as a bug; an explicit 0 is an answer."""
    rows = {r.key: r.meta for _, rows in tag_sections({"Gear": 49}) for r in rows}

    assert rows["Mounts"] == "0"


def test_the_two_groups_get_different_dots():
    assert group_accent("Rewards") != group_accent("Activity")


def test_tag_group_finds_the_owning_group():
    assert tag_group("Gear") == "Rewards"
    assert tag_group("Dungeon") == "Activity"
    assert tag_group("Nonsense") == ""


# ---------------------------------------------------------------------------
# The button
# ---------------------------------------------------------------------------


def test_tags_come_back_in_vocabulary_order_not_click_order(qapp):
    btn = TagPickerButton()
    btn._open()

    btn._popup._on_row_toggled("Dungeon", True)
    btn._popup._on_row_toggled("Gear", True)

    assert btn.tags() == ["Gear", "Dungeon"]
    btn._popup.close()


def test_the_popup_offers_every_tag(qapp):
    btn = TagPickerButton()
    btn._open()

    assert len(btn._popup.findChildren(_RowWidget)) == len(BOT_TAGS)
    btn._popup.close()


def test_constructing_the_button_does_not_recurse(qapp):
    """This widget styles itself, and setStyleSheet feeds StyleChange back in.
    Without the re-entrancy guard the app died at startup with no traceback at
    all — a 0xC0000409 stack overflow inside window construction."""
    btn = TagPickerButton(placeholder="Tags")

    assert btn.text() == "Tags"


def test_a_theme_change_reapplies_the_stylesheet_without_recursing(qapp):
    btn = TagPickerButton()
    btn.setStyleSheet("")

    qapp.sendEvent(btn, QEvent(QEvent.Type.StyleChange))

    assert "QToolButton" in btn.styleSheet()


# ---------------------------------------------------------------------------
# Bot Info wiring
# ---------------------------------------------------------------------------


def test_summary_of_no_tags_is_the_placeholder():
    assert tags_summary([]) == _PLACEHOLDER


def test_summary_joins_tags():
    assert tags_summary(["Gear", "Boss"]) == "Gear, Boss"


def _bot(**extra) -> BotConfig:
    return BotConfig.model_validate(
        {
            "name": "T",
            "initial": "s1",
            "recovery_state": "s1",
            "states": [{"id": "s1", "node_type": "sleep"}],
            "transitions": [],
            **extra,
        }
    )


def test_loading_a_bot_shows_its_tags_without_marking_it_dirty(qapp):
    section = BotInfoSection()
    dirty = []
    section.metadata_changed.connect(lambda: dirty.append(1))

    section.set_config(_bot(tags=["Boss", "Gear"]))

    assert section._tags_lbl.text() == "Gear, Boss"
    assert not dirty


def test_toggling_a_tag_writes_it_back_in_vocabulary_order(qapp):
    section = BotInfoSection()
    config = _bot(tags=["Boss"])
    section.set_config(config)
    section.set_editable(True)

    section._tags_btn._open()
    section._tags_btn._popup._on_row_toggled("Gear", True)

    assert config.tags == ["Gear", "Boss"]
    section._tags_btn._popup.close()


def test_the_editor_offers_no_counts(qapp):
    """Counts describe the library, which the editor knows nothing about."""
    section = BotInfoSection()
    section.set_config(_bot())
    section._tags_btn._open()

    assert all(w._row.meta == "" for w in section._tags_btn._popup.findChildren(_RowWidget))
    section._tags_btn._popup.close()
