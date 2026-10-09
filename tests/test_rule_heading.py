"""Tests for ``RuleHeading`` — the detail pane's section heading.

The rule is drawn from ``THEME.border`` rather than as a sunken ``QFrame``, and that is
the whole reason this has tests: Qt draws a sunken HLine from the palette's Mid/Light
pair, which the dark theme leaves all but invisible against ``surface``. A heading whose
rule disappears in one theme is a heading that stops being a section boundary there.
"""

from bartlebot.gui.theming.theme import THEME, set_theme
from bartlebot.gui.widgets.labels.rule_heading import RuleHeading


def test_the_heading_reads_what_it_was_given(qapp):
    assert RuleHeading("About").text == "About"


def test_the_label_is_bold_so_it_reads_as_a_heading(qapp):
    heading = RuleHeading("About")

    assert heading._label.font().bold()


def test_the_rule_takes_the_theme_border_color(qapp):
    heading = RuleHeading("About")

    assert THEME.border in heading._rule.styleSheet()


def test_the_rule_is_repainted_on_a_theme_switch(qapp):
    """A color resolved once in __init__ would go stale with nothing to grep for."""
    set_theme("light")
    try:
        heading = RuleHeading("About")
        light_border = THEME.border
        set_theme("dark")
        heading.refresh_theme_styles()

        assert THEME.border in heading._rule.styleSheet()
        assert light_border != THEME.border  # the switch actually changed something
    finally:
        set_theme("dark")


def test_the_rule_expands_so_it_reaches_the_right_edge(qapp):
    from PyQt6.QtWidgets import QSizePolicy  # noqa: PLC0415

    heading = RuleHeading("About")

    assert heading._rule.sizePolicy().horizontalPolicy() is QSizePolicy.Policy.Expanding


def test_the_heading_does_not_grow_vertically(qapp):
    """It sits in a column of form fields; a heading that ate the slack would push them
    apart."""
    from PyQt6.QtWidgets import QSizePolicy  # noqa: PLC0415

    heading = RuleHeading("About")

    assert heading.sizePolicy().verticalPolicy() is QSizePolicy.Policy.Fixed


def test_set_text_renames_it(qapp):
    heading = RuleHeading("About")

    heading.set_text("Combat Order")

    assert heading.text == "Combat Order"


# ---------------------------------------------------------------------------
# The gap around it. A heading opens a region, so it carries its own leading
# space rather than leaving every surface to set a spacer of its own.
# ---------------------------------------------------------------------------


def test_the_heading_keeps_space_above_itself(qapp):
    heading = RuleHeading("About")

    assert heading.layout().contentsMargins().top() > 0


def test_it_sits_closer_to_what_it_introduces_than_to_what_it_follows(qapp):
    """That asymmetry is what makes the rule read as a heading rather than a divider."""
    heading = RuleHeading("About")

    margins = heading.layout().contentsMargins()
    assert margins.bottom() < margins.top()
