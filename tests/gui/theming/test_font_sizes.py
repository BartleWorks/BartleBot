"""BartleBot's widgets fit the text a ``THEME`` font token sets in them.

Every size is a token (the literal-size guard is core's ``literal_font_sizes``, run by
``tests/test_family_guards.py``), so raising the body size moves every widget at once. These
hold the widgets sized by a number rather than by their own metrics to that.
"""

from __future__ import annotations

import pytest


def test_a_widget_pinned_to_a_height_still_fits_its_text(qapp) -> None:
    """Every fixed height that holds text is tall enough for the text it holds.

    These are the widgets sized by a number rather than by their own metrics, and each
    one here was a constant that happened to match the text at some earlier body size: a
    pill at 14px, a badge at 14, a chip at 20. Raising the body text cut into all of
    them at once, and nothing failed -- a three-pixel vertical clip on a short badge is
    not something a test suite notices on its own, and it is barely something a person
    notices either.

    ``sizeHint()`` is the right question because ``setFixedHeight`` does not change it:
    a QLabel goes on reporting the height its text wants while being held at the height
    it was given, so the gap between the two is exactly the clipping.
    """
    from bartlebot.gui.canvas.node_browser_dialog import _Chip
    from bartlebot.gui.widgets.pills.player_pills import PlayerBadge, PlayerSlotPill
    from bartlebot.gui.widgets.pills.tag_pill import ActionPill, NeutralPill, TagPill

    widgets = {
        "PlayerBadge": PlayerBadge(1),
        "PlayerSlotPill": PlayerSlotPill(1),
        "NeutralPill": NeutralPill("v1.0"),
        "ActionPill": ActionPill("Defeat the second Lost Soul", "state_1"),
        "TagPill": TagPill("Boss"),
        "_Chip": _Chip("FAILURE", "#333333", "#ffffff"),
    }
    short = {
        name: (w.minimumHeight(), w.sizeHint().height())
        for name, w in widgets.items()
        if w.sizeHint().height() > w.minimumHeight() > 0
    }
    assert not short, "pinned shorter than their own text (fixed, wants): " + repr(short)


@pytest.mark.parametrize("lines", [2, 3, 4])
def test_a_multi_line_value_field_shows_every_line_it_promises(qapp, lines: int) -> None:
    """``value_edit_multi(n)`` is n lines tall, at whatever size the body text is.

    It charged 18px a line, which was the body font's line spacing when it was written,
    so the last line went under the fold as soon as that moved. Asserted by asking the
    field whether it can scroll: if it can, there is text it is not showing.
    """
    from bartlebot.gui.sidebar.sidebar_section import value_edit_multi

    field = value_edit_multi(lines)
    field.setPlainText("\n".join(f"line {n}" for n in range(lines)))
    field.resize(200, field.height())
    field.show()
    qapp.processEvents()
    bar = field.verticalScrollBar()

    assert bar.maximum() <= bar.minimum(), f"{lines} lines do not fit {field.height()}px"
