"""Tests for SchoolPickerButton — authoring a school set, and filtering by one (#125)."""

from bartlebot.combat.deck_io import PIP_SCHOOLS
from bartlebot.gui.widgets.pickers.school_picker import ANY_SCHOOL, SchoolPickerButton, school_sections
from tests.picker_helpers import pick


def test_nothing_selected_reads_any_school(qapp):
    picker = SchoolPickerButton()
    assert picker.value() == []
    assert picker.text() == ANY_SCHOOL


def test_offers_every_pip_school():
    [(_title, rows)] = school_sections()
    assert [r.key for r in rows] == list(PIP_SCHOOLS)


def test_value_comes_back_in_school_order_not_click_order(qapp):
    picker = SchoolPickerButton()
    pick(picker, "Balance", "Fire")
    assert picker.value() == ["Fire", "Balance"]


def test_picking_emits_so_an_editor_can_write_through(qapp):
    picker = SchoolPickerButton()
    seen = []
    picker.selection_changed.connect(seen.append)
    pick(picker, "Storm")
    assert seen == [["Storm"]]


def test_set_value_shows_the_set_without_emitting(qapp):
    picker = SchoolPickerButton()
    seen = []
    picker.selection_changed.connect(seen.append)
    picker.set_value(["Life", "Ice"])
    assert picker.value() == ["Ice", "Life"]
    assert picker.text() == "Ice, Life"
    assert seen == []


def test_set_value_drops_a_name_that_is_not_a_pip_school(qapp):
    """A stale or foreign value in a hand-edited file must not crash the picker."""
    picker = SchoolPickerButton()
    picker.set_value(["NotASchool", "Myth"])
    assert picker.value() == ["Myth"]


def test_a_filter_caption_stays_put_and_counts_the_selection(qapp):
    picker = SchoolPickerButton(caption="School")
    assert picker.text() == "School"
    picker.set_value(["Fire", "Ice"])
    assert picker.text() == "School (2)"


def test_counts_carry_the_callers_noun():
    [(_title, rows)] = school_sections({"Storm": 1, "Fire": 3}, ("duel", "duels"))
    meta = {r.key: (r.meta, r.dim) for r in rows}
    assert meta["Storm"] == ("1 duel", False)
    assert meta["Fire"] == ("3 duels", False)
    assert meta["Life"] == ("0 duels", True)


def test_new_art_rebuilds_the_rows_keeping_their_counts(qapp):
    """The rows hold their medallions, so art extracted after the button was made needs new rows (#239)."""
    from bartleby.qt.art_signal import announce_art_changed  # noqa: PLC0415

    from tests.extracted_art import write_extracted_art  # noqa: PLC0415

    button = SchoolPickerButton(caption="School")
    button.set_counts({"Fire": 2}, ("strategy", "strategies"))

    def fire_row():
        return next(r for _title, rows in button._sections for r in rows if r.key == "Fire")

    assert fire_row().icon is None

    write_extracted_art(icons=["school_fire"])
    announce_art_changed()

    assert fire_row().icon is not None
    assert fire_row().meta == "2 strategies"
