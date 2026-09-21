"""Tests for the bot-tag picker shared by the bot header and the library panel."""

from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QWidget

from bartlebot.bot.schema import BotConfig
from bartlebot.bot_tags import BOT_TAGS, TAG_GROUPS
from bartlebot.gui.canvas.bot_header import BotHeader
from bartlebot.gui.widgets.picker_popup import _RowWidget
from bartlebot.gui.widgets.tag_picker import (
    TagPickerButton,
    count_label,
    group_accent,
    tag_group,
    tag_sections,
)
from bartlebot.gui.widgets.tag_pill import TagChip, TagPill

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
# Bot header wiring
# ---------------------------------------------------------------------------


def _header() -> BotHeader:
    """A header with three throwaway button clusters standing in for the toolbar."""
    return BotHeader(QWidget(), QWidget())


def _bot(**extra) -> BotConfig:
    return BotConfig.model_validate(
        {
            "id": "test-bot",
            "name": "T",
            "initial": "s1",
            "recovery_state": "s1",
            "states": [{"id": "s1", "node_type": "sleep"}],
            "transitions": [],
            **extra,
        }
    )


def test_loading_a_bot_shows_its_tags_without_marking_it_dirty(qapp):
    section = _header()
    dirty = []
    section.metadata_changed.connect(lambda: dirty.append(1))

    section.set_config(_bot(tags=["Boss", "Gear"]))

    assert section._tags_field.tags() == ["Gear", "Boss"]
    assert not dirty


def test_toggling_a_tag_writes_it_back_in_vocabulary_order(qapp):
    section = _header()
    config = _bot(tags=["Boss"])
    section.set_config(config)
    section.set_editable(True)

    picker = section._tags_field._pickers["Rewards"]
    picker._open()
    picker._popup._on_row_toggled("Gear", True)

    assert config.tags == ["Gear", "Boss"]
    picker._popup.close()


def test_the_editor_offers_no_counts(qapp):
    """Counts describe the library, which the editor knows nothing about."""
    section = _header()
    section.set_config(_bot())
    picker = section._tags_field._pickers["Activity"]
    picker._open()

    assert all(w._row.meta == "" for w in picker._popup.findChildren(_RowWidget))
    picker._popup.close()


# ---------------------------------------------------------------------------
# One button per group
# ---------------------------------------------------------------------------


def test_a_grouped_button_offers_only_its_own_group(qapp):
    btn = TagPickerButton(group="Activity")
    btn._open()

    assert len(btn._popup.findChildren(_RowWidget)) == len(TAG_GROUPS["Activity"])
    btn._popup.close()


def test_a_grouped_button_keeps_its_group_name_when_nothing_is_selected(qapp):
    """The name is the only thing advertising the vocabulary to someone who has
    not opened the popup, so it must not be replaced by a placeholder dash."""
    assert TagPickerButton(group="Rewards", placeholder="Rewards").text() == "Rewards"


def test_a_grouped_button_counts_its_selection_rather_than_listing_it(qapp):
    btn = TagPickerButton(group="Rewards", placeholder="Rewards")
    btn._open()

    btn._popup._on_row_toggled("Gear", True)
    btn._popup._on_row_toggled("Jewels", True)

    assert btn.text() == "Rewards (2)"
    btn._popup.close()


def test_an_ungrouped_button_still_lists_its_selection(qapp):
    """The library's ungrouped picker names what is selected rather than counting."""
    btn = TagPickerButton()
    btn._open()

    btn._popup._on_row_toggled("Gear", True)

    assert btn.text() == "Gear"
    btn._popup.close()


def test_tag_sections_for_a_group_drops_the_others():
    sections = tag_sections(group="Activity")

    assert [name for name, _ in sections] == ["Activity"]


# ---------------------------------------------------------------------------
# The Tags field
# ---------------------------------------------------------------------------


def _field(tags: list[str], *, editable: bool):
    """The Tags field, and the section that owns it.

    Both are returned because the test has to hold the section: dropping it collects
    the C++ side and every child widget with it, mid-assertion.
    """
    section = _header()
    section.set_config(_bot(tags=tags))
    section.set_editable(editable)
    return section._tags_field, section


def test_the_tags_field_shows_pills_in_run_mode_and_chips_in_edit_mode(qapp):
    """The pills are the value in both modes; only the ✕ and the buttons change.

    A second, differently shaped read-only view of the same list is how the two
    drift apart.
    """
    run, _run_section = _field(["Gear", "Boss"], editable=False)
    assert len(run.findChildren(TagPill)) == 2
    assert not run.findChildren(TagChip)

    edit, _edit_section = _field(["Gear", "Boss"], editable=True)
    assert len(edit.findChildren(TagChip)) == 2
    assert not edit.findChildren(TagPill)


def test_the_picker_buttons_are_hidden_until_the_bot_is_editable(qapp):
    field, _section = _field(["Gear"], editable=False)
    assert not field._buttons.isVisibleTo(field)

    field.set_editable(True)
    assert field._buttons.isVisibleTo(field)


def test_the_picker_buttons_name_their_group_and_nothing_else(qapp):
    """Bare group names, not "Add Rewards…": the popup toggles, so the same button
    takes a tag off again.  No count either — the chips below already report the
    selection, which is the library's reason for carrying one."""
    field, _section = _field([], editable=True)

    assert [b.text() for b in field._pickers.values()] == ["Rewards", "Activity"]


def test_the_picker_buttons_lead_the_flow_and_do_not_move_with_the_chips(qapp):
    """Trailing the chips, both buttons slid sideways by the width of every tag added
    or removed -- and removing one is a click on that chip's own ✕, which leaves the
    pointer where the buttons are about to arrive.  Leading, the chips grow away from
    a target that stays put."""
    field, _section = _field(["Gear"], editable=True)
    field.resize(600, field.sizeHint().height())
    field.show()
    field.layout().activate()
    before = field._buttons.mapTo(field, field._buttons.rect().topLeft())

    field.set_tags(["Gear", "Jewels", "Boss"])
    field.layout().activate()

    assert field._pill_layout.itemAt(0).widget() is field._buttons
    assert field._buttons.mapTo(field, field._buttons.rect().topLeft()) == before


def test_a_picker_button_keeps_its_caption_once_something_is_selected(qapp):
    field, _section = _field(["Gear", "Jewels"], editable=True)

    assert field._pickers["Rewards"].text() == "Rewards"


def test_removing_a_chip_writes_the_shorter_list_back(qapp):
    section = _header()
    config = _bot(tags=["Gear", "Jewels", "Boss"])
    section.set_config(config)
    section.set_editable(True)

    section._tags_field._remove("Jewels")

    assert config.tags == ["Gear", "Boss"]


def test_the_placeholder_stands_in_for_the_pills_only_while_read_only(qapp):
    """With the Add buttons showing, an untagged bot already reads as untagged."""
    read_only, _ro_section = _field([], editable=False)
    assert read_only._empty_lbl.isVisibleTo(read_only)

    field, _section = _field([], editable=True)
    assert not field._empty_lbl.isVisibleTo(field)


def test_tags_from_both_pickers_come_back_in_vocabulary_order(qapp):
    """Two buttons feed one list, and BOT_TAGS order is what gets saved."""
    section = _header()
    config = _bot()
    section.set_config(config)
    section.set_editable(True)

    for group, tag in (("Activity", "Boss"), ("Rewards", "Jewels"), ("Rewards", "Gear")):
        picker = section._tags_field._pickers[group]
        picker._open()
        picker._popup._on_row_toggled(tag, True)
        picker._popup.close()

    assert config.tags == ["Gear", "Jewels", "Boss"]


def test_a_chip_is_coloured_by_its_group(qapp):
    """Same palette as the picker dot and the library card — one module owns it."""
    field, _section = _field(["Gear", "Boss"], editable=True)
    styles = " ".join(c.styleSheet() for c in field.findChildren(TagChip))

    assert group_accent("Rewards") in styles
    assert group_accent("Activity") in styles
