"""The game view: its minimum size while a launch is coming up, and its Quick Launch screen.

A profile's windows open at the profile's resolution, and its pending tiles have no window
to measure. Sized at the global resolution instead, which setup fits to one client, a
four-account profile held room for four single-client windows, more than the screen has,
and the pending tiles ran off the edge of the game view.

The Quick Launch screen selects a card on a click and launches the selection from its
Launch button (#266). Its tests are the second half of this file.
"""

from __future__ import annotations

import pytest

from bartlebot.clients.tile_slots import TileSlot
from bartlebot.game.game_sizing import required_game_area
from bartlebot.gui import screen_fit
from bartlebot.gui.game_embed import game_view as game_view_module
from bartlebot.gui.game_embed.game_view import GameView, _GamePlaceholder
from bartlebot.gui.widgets.labels.logo_disc import LogoDisc
from bartlebot.gui.widgets.labels.themed_label import ThemedTextLabel
from bartlebot.settings_manager import SettingsManager


@pytest.fixture
def game_view(qapp):
    """A GameView with its two app-wide hooks disarmed, as ``test_game_view_theme`` has it."""
    view = GameView()
    view._focus_timer.stop()
    qapp.removeEventFilter(view)
    yield view
    view.sync_tiles([])
    view.deleteLater()


def _pending(slot: int, username: str, resolution: tuple[int, int] | None) -> TileSlot:
    return TileSlot(slot=slot, hwnd=None, label=username, player=None, username=username, resolution=resolution)


def _area(view: GameView, resolution: tuple[int, int], count: int) -> tuple[int, int]:
    return required_game_area(resolution, count, view.devicePixelRatio() or 1.0)


def test_pending_tiles_hold_room_for_the_resolution_their_launch_opens_at(game_view):
    SettingsManager.instance().game_resolution = (960, 540)

    game_view.sync_tiles([_pending(slot, name, (534, 300)) for slot, name in enumerate("abcd")])

    minimum = game_view.minimumSize()
    assert (minimum.width(), minimum.height()) == _area(game_view, (534, 300), 4)


def test_a_launch_that_did_not_say_holds_room_for_the_global_resolution(game_view):
    SettingsManager.instance().game_resolution = (800, 600)

    game_view.sync_tiles([_pending(0, "a", None)])

    minimum = game_view.minimumSize()
    assert (minimum.width(), minimum.height()) == _area(game_view, (800, 600), 1)


# ---------------------------------------------------------------------------
# Quick Launch: select, then launch
# ---------------------------------------------------------------------------

ACCOUNTS = [
    {"username": "one@example.com", "alias": "One"},
    {"username": "two@example.com", "alias": "Two"},
]
PROFILES = [
    {"name": "Farm Team", "accounts": ["one@example.com", "two@example.com"]},
    {"name": "Solo", "accounts": ["one@example.com"]},
]

# A 4K screen: everything fits unless a test shrinks the launch screen.
_4K = ((3840, 2160), 1.0)

LONG_NAME = "A Profile Name Far Too Long To Sit On One Button"


@pytest.fixture
def quick_launch(qapp, monkeypatch):
    """Build the launch screen over two accounts and two profiles, sized so all of them fit.

    Sized by hand rather than shown: the fit is measured against the widget's own size, and
    an unshown widget keeps whatever it is given.
    """
    monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: _4K)
    sm = SettingsManager.instance()
    sm.game_resolution = (800, 600)
    sm.accounts = [dict(a) for a in ACCOUNTS]
    sm.launch_profiles = [dict(p) for p in PROFILES]

    def _build() -> _GamePlaceholder:
        screen = _GamePlaceholder()
        screen.resize(3840, 2160)
        screen.refresh()
        return screen

    return _build


def _selected(screen: _GamePlaceholder) -> list[tuple[str, str]]:
    """Every card drawn as selected -- which has to be at most one, across both columns."""
    cards = [*screen.accounts_column.cards, *screen.profiles_column.cards]
    return [card.launch_key for card in cards if card.is_selected]


def test_with_no_launch_yet_nothing_is_selected_and_launch_is_off(quick_launch):
    screen = quick_launch()

    assert screen.selected_card is None
    assert _selected(screen) == []
    assert not screen.launch_button.isEnabled()
    assert screen.launch_button.text() == "Launch"
    assert "Select an account or a launch profile" in screen.launch_note


def test_the_last_launch_is_preselected(quick_launch):
    """So a returning user still launches in one click: the button."""
    SettingsManager.instance().add_recent_launch("profile", "Solo")

    screen = quick_launch()

    assert _selected(screen) == [("profile", "Solo")]
    assert screen.launch_button.isEnabled()
    assert screen.launch_button.text() == "Launch Solo"
    assert screen.launch_note == ""


def test_a_last_launch_that_is_gone_falls_back_to_the_one_before(quick_launch):
    sm = SettingsManager.instance()
    sm.add_recent_launch("account", "two@example.com")
    sm.add_recent_launch("profile", "Deleted Profile")

    screen = quick_launch()

    assert _selected(screen) == [("account", "two@example.com")]
    assert screen.launch_button.text() == "Launch Two"


def test_one_selection_spans_both_columns(quick_launch):
    """Two selections would give "what does Launch do?" two answers."""
    screen = quick_launch()

    screen.accounts_column.cards[0].picked.emit()
    assert _selected(screen) == [("account", "one@example.com")]

    screen.profiles_column.cards[0].picked.emit()
    assert _selected(screen) == [("profile", "Farm Team")]


def test_the_launch_button_launches_the_selection(quick_launch):
    screen = quick_launch()
    profiles: list[str] = []
    accounts: list[tuple] = []
    screen.launch_profile_requested.connect(profiles.append)
    screen.launch_account_requested.connect(lambda u, d: accounts.append((u, d)))

    screen.profiles_column.cards[0].picked.emit()
    screen.launch_button.click()
    screen.accounts_column.cards[1].picked.emit()
    screen.launch_button.click()

    assert profiles == ["Farm Team"]
    assert accounts == [("two@example.com", "Two")]


def test_a_click_alone_launches_nothing(quick_launch):
    screen = quick_launch()
    launched: list[str] = []
    screen.launch_profile_requested.connect(launched.append)

    screen.profiles_column.cards[0].picked.emit()

    assert launched == []


def test_the_selection_survives_a_rebuild(quick_launch):
    """Every refresh replaces the cards, and a reorder or an edit refreshes."""
    screen = quick_launch()
    screen.profiles_column.cards[1].picked.emit()

    screen.refresh()

    assert _selected(screen) == [("profile", "Solo")]


def test_a_selection_that_will_not_fit_turns_launch_off_and_says_why(quick_launch, monkeypatch):
    """Selectable, so the screen can say why -- not a card that ignores the click."""
    SettingsManager.instance().add_recent_launch("profile", "Farm Team")
    screen = quick_launch()
    launched: list[str] = []
    screen.launch_profile_requested.connect(launched.append)

    # Both terms of the fit shrink: the screen's is a floor under the widget's own size.
    monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: ((800, 600), 1.0))
    screen.resize(400, 300)
    screen.refresh_fit()
    screen.launch_selected()

    assert _selected(screen) == [("profile", "Farm Team")]
    assert not screen.launch_button.isEnabled()
    assert screen.launch_note.startswith("Doesn't fit")
    assert screen.launch_button.toolTip()
    assert launched == []

    monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: _4K)
    screen.resize(3840, 2160)
    screen.refresh_fit()
    assert screen.launch_button.isEnabled()
    assert screen.launch_note == ""


def test_the_note_under_the_button_is_given_every_line_it_wraps_to(qapp, quick_launch):
    """Left to the nested box layouts, it got one line's height and drew the prompt cut
    off mid-sentence -- "Select an account or a launch profile to"."""
    screen = quick_launch()
    screen.show()
    qapp.processEvents()
    (note,) = [label for label in screen.findChildren(ThemedTextLabel) if label.text() == screen.launch_note]

    assert note.height() >= note.heightForWidth(note.width())
    assert note.heightForWidth(note.width()) > note.fontMetrics().height(), "the prompt wraps"
    screen.close()


def test_a_long_name_is_elided_on_the_button(quick_launch):
    sm = SettingsManager.instance()
    sm.launch_profiles = [{"name": LONG_NAME, "accounts": ["one@example.com"]}]
    sm.add_recent_launch("profile", LONG_NAME)

    screen = quick_launch()

    assert screen.launch_button.text().endswith("…")
    assert LONG_NAME in screen.launch_button.toolTip()


# The keyboard. The shortcuts are only live in an active window, so these drive the two
# moves the arrow keys are bound to, and the slot Enter is bound to.


def test_down_with_nothing_selected_takes_the_first_card(quick_launch):
    screen = quick_launch()

    screen.move_selection(1)

    assert _selected(screen) == [("account", "one@example.com")]


def test_up_and_down_move_within_a_column_and_stop_at_its_ends(quick_launch):
    screen = quick_launch()
    screen.accounts_column.cards[0].picked.emit()

    screen.move_selection(1)
    assert _selected(screen) == [("account", "two@example.com")]
    screen.move_selection(1)
    assert _selected(screen) == [("account", "two@example.com")]
    screen.move_selection(-1)
    screen.move_selection(-1)
    assert _selected(screen) == [("account", "one@example.com")]


def test_left_and_right_cross_columns_at_the_same_row(quick_launch):
    screen = quick_launch()
    screen.accounts_column.cards[1].picked.emit()

    screen.select_in_column(1)
    assert _selected(screen) == [("profile", "Solo")]

    screen.select_in_column(0)
    assert _selected(screen) == [("account", "two@example.com")]


def test_crossing_into_a_shorter_column_takes_its_last_card(quick_launch):
    SettingsManager.instance().launch_profiles = [dict(PROFILES[0])]
    screen = quick_launch()
    screen.accounts_column.cards[1].picked.emit()

    screen.select_in_column(1)

    assert _selected(screen) == [("profile", "Farm Team")]


def test_enter_launches_the_selection(quick_launch):
    screen = quick_launch()
    launched: list[str] = []
    screen.launch_profile_requested.connect(launched.append)
    screen.profiles_column.cards[1].picked.emit()

    screen.launch_selected()

    assert launched == ["Solo"]


# ---------------------------------------------------------------------------
# The name and version over the logo
# ---------------------------------------------------------------------------


def test_the_launch_screen_names_the_app_and_this_builds_version_over_the_logo(quick_launch, monkeypatch):
    monkeypatch.setattr(game_view_module, "current_version", lambda: "1.2.3")
    screen = quick_launch()
    screen.show()

    labels = {label.text(): label for label in screen.findChildren(ThemedTextLabel)}
    name, version = labels["BartleBot"], labels["v1.2.3"]
    logo = screen.findChild(LogoDisc)

    def top(widget) -> int:
        return widget.mapTo(screen, widget.rect().topLeft()).y()

    assert top(name) < top(version) < top(logo)
    screen.close()
