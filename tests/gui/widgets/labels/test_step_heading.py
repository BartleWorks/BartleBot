"""StepHeading: "Step N of M", what the step decides, and what follows (#272)."""

from __future__ import annotations

from bartlebot.gui.widgets.labels.step_heading import Step, StepHeading
from bartlebot.gui.widgets.labels.themed_label import ThemedTextLabel


def test_it_says_where_the_step_is_what_it_decides_and_what_follows(qapp):
    heading = StepHeading(Step(1, 2, "Choose the duel strategy", "Next, the fights."))

    assert heading.counter_text() == "Step 1 of 2"
    assert heading.heading_text() == "Choose the duel strategy"
    assert heading.note_text() == "Next, the fights."


def test_a_step_with_no_note_hides_the_line(qapp):
    heading = StepHeading(Step(2, 2, "Which fights?"))

    counter, title, note = heading.findChildren(ThemedTextLabel)
    assert not title.isHidden()
    assert note.isHidden()


def test_building_it_never_shows_a_parentless_widget(qapp, parentless_shows):
    StepHeading(Step(1, 2, "Heading", "Note"))

    assert parentless_shows == []
