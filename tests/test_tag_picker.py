"""Tests for the bot-tag picker shared by Bot Info and the library panel."""

import sys

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication

from bartlebot.bot.schema import BotConfig
from bartlebot.bot_tags import BOT_TAGS, TAG_GROUPS
from bartlebot.gui.sidebar.sections.bot_info_section import _PLACEHOLDER, BotInfoSection, tags_summary
from bartlebot.gui.widgets.picker_popup import _RowWidget
from bartlebot.gui.widgets.tag_picker import (
    TagPickerButton,
    count_label,
    group_accent,
    tag_group,
    tag_sections,
)


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


def test_rows_carry_no_description():
    """Names say enough; a second line doubled the row height and read as an underline."""
    assert all(not r.description for _, rows in tag_sections() for r in rows)


def test_counts_are_omitted_when_not_supplied():
    """The editor has no library to count, and '0' there would be a lie."""
    assert all(r.meta == "" for _, rows in tag_sections() for r in rows)


def test_counts_carry_their_unit():
    """A bare "49" beside a name reads as ambiguous until you work out what it counts."""
    rows = {r.key: r.meta for _, rows in tag_sections({"Gear": 49}) for r in rows}

    assert rows["Gear"] == "49 bots"


def test_a_count_of_one_is_singular():
    assert count_label(1) == "1 bot"
    assert count_label(0) == "0 bots"


def test_a_tag_with_no_bots_is_listed_but_dimmed():
    """A missing row reads as a bug; a dimmed "0 bots" is an answer."""
    rows = {r.key: r for _, rows in tag_sections({"Gear": 49}) for r in rows}

    assert rows["Mounts"].meta == "0 bots"
    assert rows["Mounts"].dim
    assert not rows["Gear"].dim


def test_nothing_is_dimmed_when_counts_are_unknown():
    """The editor has no counts, so nothing there is "empty"."""
    assert not any(r.dim for _, rows in tag_sections() for r in rows)


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
    btn = TagPickerButton(placeholder="Filter by tag")

    assert btn.text() == "Filter by tag"


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
