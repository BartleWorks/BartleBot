"""The combat-order chips take the account color of the slot they name (issue #40).

The point of #40 is that a player slot reads the *same* color everywhere it is named —
the player card, the assignment pill, and the combat-order chip. So these tests are about
agreement and about the fallback, not about which hue any slot gets.
"""

from bartlebot.gui.sidebar.sections.combat_order_widget import CombatOrderWidget
from bartlebot.gui.theming.account_accent import account_accent, account_tint
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.widgets.pills.player_pills import PlayerPillBar


def _chip_for(widget: CombatOrderWidget, player: int):
    return next(c for c in widget._chips if c._player_num == player)


def test_an_uncolored_chip_looks_exactly_as_it_always_did(qapp):
    """Nothing hooked is the normal authoring case and must not pick a default color."""
    widget = CombatOrderWidget()
    widget.set_value([1, 2, 3, 4])

    style = _chip_for(widget, 1).styleSheet()

    assert THEME.border in style
    assert account_accent(1) not in style


def test_a_colored_chip_is_tinted_and_outlined_rather_than_filled(qapp):
    """Four saturated fills would make the combat order the loudest thing on the panel,
    when all it is saying is an order the positions already give. See ``AccountPill``."""
    widget = CombatOrderWidget()
    widget.set_value([1, 2, 3, 4])

    widget.set_player_color_keys([5, None, None, None])

    style = _chip_for(widget, 1).styleSheet()
    assert account_tint(5) in style
    assert f"1px solid {account_accent(5)}" in style


def test_a_chip_and_its_pill_agree_about_the_slot_s_color(qapp):
    """The whole of issue #40 in one assertion."""
    widget = CombatOrderWidget()
    widget.set_value([1, 2, 3, 4])
    widget.set_player_color_keys([2, 5, 7, 1])
    bar = PlayerPillBar()
    bar.set_value([3])
    bar.set_color_keys([2, 5, 7, 1])

    assert account_accent(7) in _chip_for(widget, 3).styleSheet()
    assert account_accent(7) in bar.pills[2].styleSheet()


def test_colors_survive_a_reorder(qapp):
    """The chips are rebuilt on every swap, so each rebuild has to arrive colored."""
    widget = CombatOrderWidget()
    widget.set_value([1, 2, 3, 4])
    widget.set_player_color_keys([2, 5, 7, 1])

    widget._swap_players(1, 4)

    assert account_accent(2) in _chip_for(widget, 1).styleSheet()
    assert account_accent(1) in _chip_for(widget, 4).styleSheet()


def test_colors_survive_a_max_players_change(qapp):
    widget = CombatOrderWidget()
    widget.set_value([1, 2, 3, 4])
    widget.set_player_color_keys([2, 5, 7, 1])

    widget.set_max_players(2)

    assert account_accent(2) in _chip_for(widget, 1).styleSheet()
    assert len(widget._chips) == 2


def test_a_short_color_list_leaves_the_rest_of_the_chips_plain(qapp):
    widget = CombatOrderWidget()
    widget.set_value([1, 2, 3, 4])

    widget.set_player_color_keys([2, 5])

    assert account_accent(5) in _chip_for(widget, 2).styleSheet()
    assert THEME.border in _chip_for(widget, 4).styleSheet()


def test_a_disabled_chip_drops_its_color_for_the_app_wide_off_look(qapp):
    """A chip that cannot be dragged is off, and "off" is one look everywhere."""
    widget = CombatOrderWidget()
    widget.set_value([1, 2, 3, 4])
    widget.set_player_color_keys([2, 5, 7, 1])

    widget.set_editable(False)

    style = _chip_for(widget, 1).styleSheet()
    assert THEME.surface_disabled in style
    assert account_accent(2) not in style


# ---------------------------------------------------------------------------
# add_header_widget — how the duel editor gets Min/Max onto the checkbox row
# ---------------------------------------------------------------------------


def test_a_header_widget_lands_on_the_checkbox_row(qapp):
    from PyQt6.QtWidgets import QLabel  # noqa: PLC0415

    widget = CombatOrderWidget()
    label = QLabel("Min players:")

    widget.add_header_widget(label)

    assert widget._header_layout.indexOf(label) >= 0


def test_header_widgets_land_after_the_stretch_so_they_sit_at_the_right(qapp):
    from PyQt6.QtWidgets import QLabel  # noqa: PLC0415

    widget = CombatOrderWidget()
    label = QLabel("Min players:")

    widget.add_header_widget(label)

    assert widget._header_layout.indexOf(label) > widget._header_layout.indexOf(widget._checkbox) + 1
