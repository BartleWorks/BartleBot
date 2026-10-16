"""Tests for the slot pills that replaced the assignment row's P1–P4 checkboxes.

Two things matter here and neither is which hue a pill gets. The first is that the pill
keeps saying ``P1`` whatever is hooked — a DuelConfig stores slots, not accounts, and a
pill that renamed itself would be a claim the stored data does not make. The second is
that the color degrades to *nothing* rather than to a default when no account is known:
a color on this pill is a claim about which account is in that slot, and a wrong match
target is worse than no match target.
"""

from bartlebot.gui.theming.account_accent import account_accent, account_tint
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.widgets.pills.player_pills import PlayerPillBar


def test_a_bar_offers_four_slots(qapp):
    bar = PlayerPillBar()

    assert [p.player for p in bar.pills] == [1, 2, 3, 4]


def test_every_pill_reads_its_slot_number(qapp):
    bar = PlayerPillBar()

    assert [p.text() for p in bar.pills] == ["P1", "P2", "P3", "P4"]


def test_a_pill_still_reads_its_slot_number_once_an_account_is_behind_it(qapp):
    """The color is the account's; the text is not. See the module docstring."""
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
# Color
# ---------------------------------------------------------------------------


def test_an_uncolored_pill_wears_no_account_color_at_all(qapp):
    """Nothing hooked is the normal authoring case, and it must not pick a default."""
    bar = PlayerPillBar()
    bar.set_value([1])

    style = bar.pills[0].styleSheet()

    assert THEME.accent in style
    assert account_accent(1) not in style


def test_a_colored_pill_wears_that_account_s_outline_and_tint(qapp):
    bar = PlayerPillBar()
    bar.set_value([1])
    bar.set_color_keys([3])

    style = bar.pills[0].styleSheet()

    assert account_accent(3) in style
    assert account_tint(3) in style


def test_a_short_color_list_leaves_the_remaining_slots_plain(qapp):
    """Two clients hooked colors P1 and P2 and says nothing about P3 and P4."""
    bar = PlayerPillBar()
    bar.set_value([1, 2, 3, 4])

    bar.set_color_keys([1, 2])

    assert account_accent(1) in bar.pills[0].styleSheet()
    assert account_accent(2) in bar.pills[1].styleSheet()
    assert THEME.accent in bar.pills[2].styleSheet()


def test_a_pill_that_is_off_is_drawn_as_a_slot_rather_than_as_an_account(qapp):
    """Color on this pill means "this assignment covers that player". An off pill
    covers nobody, so it claims no color even when the slot has an account."""
    bar = PlayerPillBar()
    bar.set_color_keys([3])
    bar.set_value([])

    style = bar.pills[0].styleSheet()

    assert account_accent(3) not in style
    assert THEME.border in style


def test_a_pill_holds_its_slot_not_a_hex_so_a_theme_switch_re_resolves(qapp):
    from bartlebot.gui.theming.theme import set_theme  # noqa: PLC0415

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


# ---------------------------------------------------------------------------
# PlayerBadge / PlayerBadgeRow — the read-only pills the Combat tab draws
# ---------------------------------------------------------------------------


def test_an_uncolored_badge_is_the_neutral_gray_pill(qapp):
    from bartlebot.gui.widgets.pills.player_pills import PlayerBadge  # noqa: PLC0415

    badge = PlayerBadge(2)

    assert badge.text() == "P2"
    assert THEME.pill_neutral_bg in badge.styleSheet()
    assert account_accent(1) not in badge.styleSheet()


def test_a_colored_badge_is_outlined_and_tinted_and_names_the_account_in_its_tooltip(qapp):
    from bartlebot.gui.widgets.pills.player_pills import PlayerBadge  # noqa: PLC0415

    badge = PlayerBadge(1)

    badge.set_color_key(3)
    badge.set_account_label("Main")

    assert account_accent(3) in badge.styleSheet()
    assert account_tint(3) in badge.styleSheet()
    assert badge.text() == "P1"
    assert "Main" in badge.toolTip()


def test_a_badge_row_hides_surplus_badges_and_separators_rather_than_destroying_them(qapp):
    from bartlebot.gui.widgets.pills.player_pills import PlayerBadgeRow  # noqa: PLC0415

    row = PlayerBadgeRow("→")
    first = row._badges[0]

    row.set_players([3, 1])

    assert row.players == [3, 1]
    assert [b.text() for b in row.badges] == ["P3", "P1"]
    assert row._badges[2].isHidden() and row._badges[3].isHidden()
    assert [s.isHidden() for s in row._separators] == [False, True, True]

    row.set_players([])

    assert row._badges[0] is first
    assert all(b.isHidden() for b in row._badges)


def test_a_badge_row_colors_by_slot_and_leaves_unknown_slots_plain(qapp):
    from bartlebot.gui.widgets.pills.player_pills import PlayerBadgeRow  # noqa: PLC0415

    row = PlayerBadgeRow()
    row.set_player_colors([2, 5])

    row.set_players([4, 2])

    assert [b.color_key for b in row.badges] == [None, 5]


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


def test_new_art_shows_the_gems_of_the_players_listed_and_no_others(qapp):
    """Art extracted while a combat order is summarized (#239)."""
    from bartlebot.gui.art_signal import announce_art_changed  # noqa: PLC0415
    from bartlebot.gui.widgets.pills.player_pills import PlayerBadgeRow  # noqa: PLC0415
    from tests.extracted_art import write_extracted_art  # noqa: PLC0415

    row = PlayerBadgeRow(duel_order=True)
    row.set_players([2, 1])
    assert all(gem.isHidden() for gem in row._gems)

    write_extracted_art(icons=[f"duel_slot_{slot}" for slot in range(1, 5)])
    announce_art_changed()

    shown = [gem.slot for gem in row._gems if not gem.isHidden()]
    assert shown == [2, 1]
