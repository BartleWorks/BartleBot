"""Tests for the launch-time fit check and the disabled cards it drives (#101).

``bartlebot/game_sizing.py`` owns the arithmetic and is tested on its own; what is
checked here is the layer that supplies it with the screen and the settings, and that an
unfittable launch is *refused* — a card that cannot be clicked — rather than warned about.
"""

from __future__ import annotations

import sys

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from bartlebot.gui import screen_fit
from bartlebot.gui.game_embed.launch_cards import AccountCard, ProfileCard, profile_summary
from bartlebot.settings_manager import SettingsManager

# A 1080p screen at the default layout, which is where two 800x600 clients stop fitting.
_1080P = ((1920, 1080), 1.0)
_4K = ((3840, 2160), 1.0)


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def screen(monkeypatch):
    """Pin the screen the fit is judged against, so the result is not the runner's."""

    def _use(metrics):
        monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: metrics)

    return _use


def _profile(accounts, resolution=None):
    profile = {"name": "Team", "accounts": list(accounts)}
    if resolution is not None:
        profile["resolution"] = list(resolution)
    return profile


# ---------------------------------------------------------------------------
# Which resolution a profile is judged at
# ---------------------------------------------------------------------------


def test_a_profile_that_pins_a_resolution_is_judged_at_that_one():
    assert screen_fit.profile_resolution(_profile(["a"], (640, 480))) == (640, 480)


def test_a_profile_that_pins_nothing_follows_the_app_setting():
    SettingsManager.instance().game_resolution = (560, 420)
    assert screen_fit.profile_resolution(_profile(["a"])) == (560, 420)


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------


def test_the_bug_this_issue_is_about_is_refused(screen):
    """Two clients at the stock 800x600 floor, on 1080p: the case #101 opens with."""
    screen(_1080P)
    assert screen_fit.profile_fit_problem(_profile(["a", "b"], (800, 600))) is not None


def test_the_same_pair_at_a_smaller_tier_is_allowed(screen):
    screen(_1080P)
    assert screen_fit.profile_fit_problem(_profile(["a", "b"], (640, 480))) is None


def test_the_same_pair_fits_a_4k_screen_untouched(screen):
    """The floor is relaxed for the screens that need it, not imposed on the ones that do not."""
    screen(_4K)
    assert screen_fit.profile_fit_problem(_profile(["a", "b"], (800, 600))) is None


def test_a_third_client_can_make_a_fitting_resolution_unfittable(screen):
    """A third client forces a 2x2 grid, which is a whole extra row rather than a column."""
    screen(_1080P)
    assert screen_fit.fit_problem((640, 480), 2) is None
    assert screen_fit.fit_problem((640, 480), 3) is not None


def test_an_empty_profile_is_not_reported_as_a_sizing_failure(screen):
    """It cannot launch, but for its own reason — the card and the launcher both say so."""
    screen(_1080P)
    assert screen_fit.profile_fit_problem(_profile([])) is None


def test_a_wider_sidebar_can_take_a_resolution_away(screen):
    """The budget is the user's configured layout, not the panel minimums."""
    screen(_1080P)
    assert screen_fit.fit_problem((640, 480), 2) is None
    SettingsManager.instance().set_general("left_sidebar_width", 700)
    assert screen_fit.fit_problem((640, 480), 2) is not None


def test_the_largest_fitting_resolution_is_offered_as_a_hint(screen):
    screen(_1080P)
    assert screen_fit.largest_fitting_resolution(4) == (400, 300)


def test_a_screen_that_cannot_be_measured_refuses_nothing(monkeypatch, qapp):
    """Better to allow a launch than to make an app that cannot see a screen launch nothing."""
    monkeypatch.setattr(QApplication, "primaryScreen", staticmethod(lambda: None))
    assert screen_fit.fit_problem((1920, 1080), 4) is None


# ---------------------------------------------------------------------------
# The cards
# ---------------------------------------------------------------------------


def _click(widget):
    widget.mousePressEvent(
        QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPoint(5, 5).toPointF(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def test_an_unavailable_profile_card_will_not_launch_when_clicked(qapp):
    """The whole card is the target, so it has to swallow its own clicks."""
    card = ProfileCard(_profile(["a", "b"]), [])
    fired: list[str] = []
    card.launch_profile_requested.connect(fired.append)

    card.set_unavailable("needs more room")
    _click(card)
    assert fired == []

    card.set_unavailable(None)
    _click(card)
    assert fired == ["Team"]


def test_an_unavailable_account_card_will_not_launch_when_clicked(qapp):
    card = AccountCard({"username": "u@example.test", "alias": "Alice"}, [])
    fired: list[tuple] = []
    card.launch_account_requested.connect(lambda u, d: fired.append((u, d)))

    card.set_unavailable("needs more room")
    _click(card)
    assert fired == []


def test_the_card_says_why_rather_than_disappearing(qapp):
    """Disabled and explained, never hidden — the rule the launch surfaces share."""
    profile = _profile(["a", "b"])
    card = ProfileCard(profile, [])
    assert card.quiet_text == profile_summary(profile)

    card.set_unavailable("2 clients at 800×600 needs 1624×651")
    assert "1624×651" in card.quiet_text
    assert card.toolTip() == "2 clients at 800×600 needs 1624×651"
    assert card.unavailable_reason is not None


def test_making_the_card_available_again_restores_its_own_line(qapp):
    profile = _profile(["a", "b"])
    card = ProfileCard(profile, [])
    card.set_unavailable("needs more room")
    card.set_unavailable(None)
    assert card.quiet_text == profile_summary(profile)
    assert card.unavailable_reason is None


# ---------------------------------------------------------------------------
# The floor the splitters and the window resize stop at
# ---------------------------------------------------------------------------


def test_the_game_view_s_minimum_reaches_the_panel_that_sits_in_the_splitter(qapp):
    """What stops a splitter drag clipping the game windows.

    ``GameView`` sets a minimum from the grid it is holding; the clamp only works because
    ``GameViewPanel`` wraps it in a layout, so that minimum becomes the panel's own and
    ``QSplitter`` — which honours a child's minimum — refuses to drag past it. Nothing
    else enforces this, so a panel that stopped propagating would silently reopen the
    path into a clipped layout.
    """
    from bartlebot.gui.game_embed.game_view_panel import GameViewPanel

    panel = GameViewPanel()
    before = panel.minimumSizeHint()
    panel.game_view.setMinimumSize(1200, 900)
    after = panel.minimumSizeHint()

    assert after.width() >= 1200
    assert after.height() >= 900
    assert (after.width(), after.height()) > (before.width(), before.height())
