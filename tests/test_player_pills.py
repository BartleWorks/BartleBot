"""Tests for the slot pills that replaced the assignment row's P1–P4 checkboxes.

Two things matter here and neither is which hue a pill gets. The first is that the pill
keeps saying ``P1`` whatever is hooked — a DuelConfig stores slots, not accounts, and a
pill that renamed itself would be a claim the stored data does not make. The second is
that the colour degrades to *nothing* rather than to a default when no account is known:
a colour on this pill is a claim about which account is in that slot, and a wrong match
target is worse than no match target.
"""

from bartlebot.gui.account_accent import account_accent, account_tint
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.player_pills import PlayerPillBar


def test_a_bar_offers_four_slots(qapp):
    bar = PlayerPillBar()

    assert [p.player for p in bar.pills] == [1, 2, 3, 4]


def test_every_pill_reads_its_slot_number(qapp):
    bar = PlayerPillBar()

    assert [p.text() for p in bar.pills] == ["P1", "P2", "P3", "P4"]


def test_a_pill_still_reads_its_slot_number_once_an_account_is_behind_it(qapp):
    """The colour is the account's; the text is not. See the module docstring."""
    bar = PlayerPillBar()

    bar.set_color_keys([3, 4, 1, 2])
    bar.set_account_labels(["Main", "Alt", "Third", "Fourth"])

    assert [p.text() for p in bar.pills] == ["P1", "P2", "P3", "P4"]


def test_the_account_name_is_in_the_tooltip_where_it_can_go_stale_safely(qapp):
    bar = PlayerPillBar()

    bar.set_account_labels(["Main"])

    assert "Main" in bar.pills[0].toolTip()
    assert "Main" not in bar.pills[1].toolTip()


# ---------------------------------------------------------------------------
# Value round-trip — the same API PlayerListWidget has, so a surface can swap one
# for the other without its own code changing.
# ---------------------------------------------------------------------------


def test_set_value_then_get_value_round_trips(qapp):
    bar = PlayerPillBar()

    bar.set_value([1, 3])

    assert bar.get_value() == [1, 3]


def test_clicking_a_pill_toggles_it_into_the_value(qapp):
    bar = PlayerPillBar()
    bar.set_value([])
    seen = []
    bar.changed.connect(lambda: seen.append(True))

    bar.pills[1].mousePressEvent(_left_press())

    assert bar.get_value() == [2]
    assert seen == [True]


def test_clicking_a_pill_that_is_on_turns_it_off(qapp):
    bar = PlayerPillBar()
    bar.set_value([2])

    bar.pills[1].mousePressEvent(_left_press())

    assert bar.get_value() == []


def test_a_locked_pill_does_not_toggle(qapp):
    """Reordering and reassigning are edits, and edits are locked mid-run."""
    bar = PlayerPillBar()
    bar.set_value([])
    bar.set_editable(False)

    bar.pills[0].mousePressEvent(_left_press())

    assert bar.get_value() == []
    assert not bar.pills[0].is_editable


# ---------------------------------------------------------------------------
# max_players is a *hard* limit, unlike PlayerListWidget's advisory availability
# ---------------------------------------------------------------------------


def test_lowering_max_players_hides_and_clears_the_slots_past_it(qapp):
    bar = PlayerPillBar()
    bar.set_value([1, 2, 3, 4])

    bar.set_max_players(2)

    assert bar.get_value() == [1, 2]
    assert bar.pills[2].isHidden()
    assert bar.pills[3].isHidden()


def test_raising_max_players_brings_the_slots_back_switched_off(qapp):
    """A hidden slot's value was cleared, so it must not come back on."""
    bar = PlayerPillBar()
    bar.set_value([1, 2, 3])
    bar.set_max_players(2)

    bar.set_max_players(4)

    assert not bar.pills[2].isHidden()
    assert bar.get_value() == [1, 2]


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------


def test_an_uncoloured_pill_wears_no_account_colour_at_all(qapp):
    """Nothing hooked is the normal authoring case, and it must not pick a default."""
    bar = PlayerPillBar()
    bar.set_value([1])

    style = bar.pills[0].styleSheet()

    assert THEME.accent in style
    assert account_accent(1) not in style


def test_a_coloured_pill_wears_that_account_s_outline_and_tint(qapp):
    bar = PlayerPillBar()
    bar.set_value([1])
    bar.set_color_keys([3])

    style = bar.pills[0].styleSheet()

    assert account_accent(3) in style
    assert account_tint(3) in style


def test_a_short_colour_list_leaves_the_remaining_slots_plain(qapp):
    """Two clients hooked colours P1 and P2 and says nothing about P3 and P4."""
    bar = PlayerPillBar()
    bar.set_value([1, 2, 3, 4])

    bar.set_color_keys([1, 2])

    assert account_accent(1) in bar.pills[0].styleSheet()
    assert account_accent(2) in bar.pills[1].styleSheet()
    assert THEME.accent in bar.pills[2].styleSheet()


def test_a_pill_that_is_off_is_drawn_as_a_slot_rather_than_as_an_account(qapp):
    """Colour on this pill means "this assignment covers that player". An off pill
    covers nobody, so it claims no colour even when the slot has an account."""
    bar = PlayerPillBar()
    bar.set_color_keys([3])
    bar.set_value([])

    style = bar.pills[0].styleSheet()

    assert account_accent(3) not in style
    assert THEME.border in style


def test_a_pill_holds_its_slot_not_a_hex_so_a_theme_switch_re_resolves(qapp):
    from bartlebot.gui.theme import set_theme  # noqa: PLC0415

    bar = PlayerPillBar()
    bar.set_value([1])
    bar.set_color_keys([3])
    set_theme("light")
    try:
        light = account_accent(3)
        bar.pills[0].set_color_key(None)  # force a restyle through the public path
        bar.pills[0].set_color_key(3)

        assert light in bar.pills[0].styleSheet()
    finally:
        set_theme("dark")


def _left_press():
    """A synthetic left-button press at the pill's origin."""
    from PyQt6.QtCore import QEvent, QPointF, Qt  # noqa: PLC0415
    from PyQt6.QtGui import QMouseEvent  # noqa: PLC0415

    return QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(1, 1),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
