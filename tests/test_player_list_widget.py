"""Tests for PlayerListWidget's two limits.

The widget carries two independent notions of "fewer than four players", and the
whole point is that they behave differently:

- ``set_max_players`` is a hard limit from a duel strategy's own configuration.
  There is no third player, so the box is hidden and its value cleared.
- ``set_available_players`` is advisory — how many clients happen to be hooked
  right now.  Bots are authored offline, so a four-client bot opened with two
  clients running must keep P3/P4 checked and still report them.  Graying is the
  only thing that changes.

Real widget tests (QApplication required), in the style of test_menu_anchoring.
"""

import pytest
from PyQt6.QtCore import QEvent

from bartlebot.gui.widgets.pickers.player_list_widget import PlayerListWidget


@pytest.fixture
def widget(qapp):
    return PlayerListWidget()


class TestAvailability:
    def test_unavailable_players_keep_their_value(self, widget) -> None:
        """The client-checkbox hazard: editing a four-client bot with two clients connected."""
        widget.set_value([1, 3, 4])
        widget.set_available_players(2)
        assert widget.get_value() == [1, 3, 4]

    def test_a_stored_value_still_loads_past_the_available_count(self, widget) -> None:
        """Order matters too — set_value after the gray-out must not be filtered."""
        widget.set_available_players(2)
        widget.set_value([1, 3, 4])
        assert widget.get_value() == [1, 3, 4]

    def test_unavailable_players_stay_clickable(self, widget) -> None:
        widget.set_available_players(1)
        assert all(chk.isEnabled() for chk in widget._checks)

    def test_unavailable_players_are_grayed_and_explained(self, widget) -> None:
        widget.set_available_players(2)
        grayed = [bool(chk.styleSheet()) for chk in widget._checks]
        assert grayed == [False, False, True, True]
        assert "not connected" in widget._checks[2].toolTip()
        assert widget._checks[1].toolTip() == ""

    def test_reconnecting_clears_the_graying(self, widget) -> None:
        widget.set_available_players(1)
        widget.set_available_players(4)
        assert [chk.styleSheet() for chk in widget._checks] == ["", "", "", ""]
        assert [chk.toolTip() for chk in widget._checks] == ["", "", "", ""]

    def test_all_button_selects_grayed_players_too(self, widget) -> None:
        """Advisory means advisory: a 4-client bot is still authorable on 2 clients."""
        widget.set_available_players(2)
        widget._select_all()
        assert widget.get_value() == [1, 2, 3, 4]

    def test_style_change_reapplies_the_graying(self, widget) -> None:
        """Theme switches restyle every widget; the gray must survive one."""
        widget.set_available_players(2)
        widget._checks[3].setStyleSheet("")  # simulate the stylesheet being blown away
        widget.changeEvent(QEvent(QEvent.Type.StyleChange))
        assert widget._checks[3].styleSheet() != ""


class TestMaxPlayers:
    def test_max_players_clears_beyond_the_limit(self, widget) -> None:
        """The hard limit still clears — a duel with two players has no third."""
        widget.set_value([1, 3])
        widget.set_max_players(2)
        assert widget.get_value() == [1]
        assert not widget._checks[2].isVisible()

    def test_the_two_limits_are_independent(self, widget) -> None:
        widget.set_max_players(3)
        widget.set_available_players(1)
        widget.set_value([1, 2, 3])
        assert widget.get_value() == [1, 2, 3]
