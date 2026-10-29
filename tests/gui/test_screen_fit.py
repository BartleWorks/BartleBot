"""Tests for the launch-time fit check and the disabled cards it drives.

``bartleby/runtime/game/game_sizing.py`` owns the arithmetic and is tested on its own; what is
checked here is the layer that supplies it with the screen and the settings, and that an
unfittable launch is *refused* — a card that cannot be clicked — rather than warned about.
"""

from __future__ import annotations

import pytest
from bartleby.runtime.game import game_sizing
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QMouseEvent, QResizeEvent
from PyQt6.QtWidgets import QApplication, QPushButton

from bartlebot.gui import screen_fit
from bartlebot.gui.game_embed.launch_cards import AccountCard, ProfileCard, character_summary, profile_summary
from bartlebot.settings_manager import SettingsManager

# A 1080p screen at the default layout, which is where two 800x600 clients stop fitting.
_1080P = ((1920, 1080), 1.0)
_4K = ((3840, 2160), 1.0)


@pytest.fixture
def screen(monkeypatch):
    """Pin the screen the fit is judged against, so the result is not the runner's."""

    def _use(metrics):
        monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: metrics)

    return _use


def _sidebar_excess() -> int:
    """How much wider than the reference this machine's two sidebar floors are, together.

    ``budget_for`` subtracts the floors ``screen_fit`` measures here, and those are style
    and font metrics: the same layout leaves 1200px of game width under ``windowsvista``,
    the style a dev box resolves, and 33 fewer under ``windows11``, the one CI resolves.
    A literal budget therefore asserts which style the runner happens to have rather than
    the behavior this test names — see "One QApplication per worker" in tests/CLAUDE.md.
    """
    return (screen_fit.left_sidebar_floor() - game_sizing.LEFT_SIDEBAR_MIN_WIDTH) + (
        screen_fit.right_sidebar_floor() - game_sizing.RIGHT_SIDEBAR_MIN_WIDTH
    )


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
    """Two clients at the stock 800x600 floor, on 1080p: the case the window-scaling work opens with."""
    screen(_1080P)
    assert screen_fit.profile_fit_problem(_profile(["a", "b"], (800, 600))) is not None


def test_the_same_pair_at_a_smaller_tier_is_allowed(screen):
    screen(_1080P)
    assert screen_fit.profile_fit_problem(_profile(["a", "b"], (560, 420))) is None


def test_the_same_pair_fits_a_4k_screen_untouched(screen):
    """The floor is relaxed for the screens that need it, not imposed on the ones that do not."""
    screen(_4K)
    assert screen_fit.profile_fit_problem(_profile(["a", "b"], (800, 600))) is None


def test_a_third_client_can_make_a_fitting_resolution_unfittable(screen):
    """A third client forces a 2x2 grid, which is a whole extra row rather than a column."""
    screen(_1080P)
    assert screen_fit.fit_problem((560, 420), 2) is None
    assert screen_fit.fit_problem((560, 420), 3) is not None


def test_an_empty_profile_is_not_reported_as_a_sizing_failure(screen):
    """It cannot launch, but for its own reason — the card and the launcher both say so."""
    screen(_1080P)
    assert screen_fit.profile_fit_problem(_profile([])) is None


def test_a_wider_sidebar_can_take_a_resolution_away(screen):
    """The budget is the user's configured layout, not the panel minimums."""
    screen(_1080P)
    assert screen_fit.fit_problem((560, 420), 2) is None
    SettingsManager.instance().set_general("left_sidebar_width", 700)
    assert screen_fit.fit_problem((560, 420), 2) is not None


def test_the_largest_fitting_resolution_is_offered_as_a_hint(screen):
    """Micro's 16:9 form: the smallest offered resolution, and it still fits a 2x2 grid."""
    screen(_1080P)
    assert screen_fit.largest_fitting_resolution(4) == (534, 300)


def test_a_screen_that_cannot_be_measured_refuses_nothing(monkeypatch, qapp):
    """Better to allow a launch than to make an app that cannot see a screen launch nothing."""
    monkeypatch.setattr(QApplication, "primaryScreen", staticmethod(lambda: None))
    assert screen_fit.fit_problem((1920, 1080), 4) is None


# ---------------------------------------------------------------------------
# The cards
# ---------------------------------------------------------------------------


def _click(widget):
    """Down and up in the same place. Both halves: a card activates on release."""
    widget.mousePressEvent(
        QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPoint(5, 5).toPointF(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    widget.mouseReleaseEvent(
        QMouseEvent(
            QMouseEvent.Type.MouseButtonRelease,
            QPoint(5, 5).toPointF(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def test_an_unavailable_profile_card_will_not_launch(qapp):
    """The whole card is the target, so it has to refuse its own activation -- while still
    taking the click that selects it, so the launch screen can say why it is refused."""
    card = ProfileCard(_profile(["a", "b"]), [])
    fired: list[str] = []
    picked: list[int] = []
    card.launch_profile_requested.connect(fired.append)
    card.picked.connect(lambda: picked.append(1))

    card.set_unavailable("needs more room")
    _click(card)
    assert card.activate() is False
    assert fired == []
    assert picked == [1]

    card.set_unavailable(None)
    assert card.activate() is True
    assert fired == ["Team"]


def test_an_unavailable_account_card_will_not_launch(qapp):
    card = AccountCard({"username": "u@example.test", "alias": "Alice"}, [])
    fired: list[tuple] = []
    card.launch_account_requested.connect(lambda u, d: fired.append((u, d)))

    card.set_unavailable("needs more room")
    assert card.activate() is False
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
    ``QSplitter`` — which honors a child's minimum — refuses to drag past it. Nothing
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


# ---------------------------------------------------------------------------
# The live game area
# ---------------------------------------------------------------------------
#
# Sidebar widths and the canvas height are startup seeds written only by the settings
# dialog — nothing persists a splitter drag — so the configured layout can describe
# something the user moved away from. These are the guards on that gap.


def test_the_budget_takes_the_larger_of_the_layout_and_the_live_area(screen):
    screen(_1080P)
    configured, _dpr = screen_fit.budget_for()
    assert configured == (1200 - _sidebar_excess(), 800)

    wider, _dpr = screen_fit.budget_for(live_area=(1700, 700))
    assert wider == (1700, 800)  # width from the drag, height from the layout


def test_dragging_a_sidebar_narrower_unlocks_the_tier_it_made_room_for(screen):
    """The reported bug: fixing the layout by hand left the launch still refused."""
    screen(_1080P)
    profile = _profile(["a", "b"], (800, 600))
    assert screen_fit.profile_fit_problem(profile) is not None

    # Two 800x600 tiles need 1624 wide; a drag that gets the game area there is enough.
    assert screen_fit.profile_fit_problem(profile, None, (1624, 651)) is None


def test_a_merely_small_window_refuses_nothing_the_screen_could_carry(screen):
    """Why the budget is a max and not the live area alone.

    A launch bigger than the current window corrects itself — embedding raises the game
    view's minimum and Qt grows the window — so a small window is not a reason to refuse.
    It is also the state every card is built in, before the window has ever been shown.
    """
    screen(_1080P)
    # Whatever tier the screen carries, rather than a literal one: which tier that is
    # comes off the sidebar floors, and those are style and font metrics — see
    # _sidebar_excess. The subject here is the tiny live area, not the tier.
    fits_on_screen = screen_fit.largest_fitting_resolution(2)
    assert fits_on_screen is not None
    assert screen_fit.fit_problem(fits_on_screen, 2, None, (200, 100)) is None


def test_the_hint_grows_with_the_live_area_too(screen):
    screen(_1080P)
    # Both tiers derived for the reason above: the hint's *growth* is the subject, and a
    # literal pair would assert this machine's sidebar widths instead.
    on_screen = screen_fit.largest_fitting_resolution(2)
    roomier = screen_fit.largest_fitting_resolution(2, None, (1700, 900))
    assert on_screen is not None and roomier is not None
    assert roomier > on_screen


def test_the_launch_screen_re_judges_its_cards_on_a_resize_without_rebuilding_them(qapp, monkeypatch):
    """A splitter drag has to move the answer, and must not destroy every card doing it.

    Rebuilding on resize would deleteLater and recreate every widget several times a
    second for the length of a drag; re-judging is a few comparisons that usually change
    nothing.

    The resize is delivered by hand because Qt defers a resize event to a hidden widget
    until it is shown — which is also why ``_recompute_geometry`` drives the re-judge
    rather than leaving it to the placeholder's own handler.
    """
    from bartlebot.gui.game_embed.game_view import GameView

    # A screen with room for one 800x600 tile and not two, so only the live game area can
    # decide this pair.
    monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: ((1300, 800), 1.0))
    SettingsManager.instance().launch_profiles = [_profile(["a", "b"], (800, 600))]

    view = GameView()
    card = view.findChild(ProfileCard)
    container = card.parent()

    _resize(view, 900, 800)
    assert card.unavailable_reason is not None

    _resize(view, 1700, 800)
    assert card.unavailable_reason is None
    # Same widget throughout: the resize re-judged it rather than replacing it.
    assert view.findChild(ProfileCard) is card
    assert card.parent() is container


def _resize(widget, width, height):
    """Resize *widget* and deliver the event Qt would if it were on screen."""
    old = widget.size()
    widget.resize(width, height)
    widget.resizeEvent(QResizeEvent(widget.size(), old))


# ---------------------------------------------------------------------------
# Fitting a window to the screen
# ---------------------------------------------------------------------------


def test_a_window_that_already_fits_is_left_at_its_preferred_size():
    assert screen_fit.size_within_screen((620, 720), (480, 400), (1920, 1080)) == (620, 720)


def test_a_window_taller_than_the_screen_is_cut_down_to_it():
    width, height = screen_fit.size_within_screen((620, 1400), (480, 400), (1920, 1080))
    assert height == 1080 - screen_fit.WINDOW_SCREEN_MARGIN
    assert width == 620


def test_the_minimum_wins_over_a_screen_too_small_for_it():
    """Clamping past the minimum is a second way to lose the buttons, not a fix."""
    assert screen_fit.size_within_screen((620, 720), (480, 400), (500, 300)) == (480, 400)


# ---------------------------------------------------------------------------
# Where each half of a refusal is said
# ---------------------------------------------------------------------------


def _accounts(*usernames):
    return [{"username": name} for name in usernames]


def test_a_profile_card_carries_the_numbers_and_not_the_advice(qapp, screen):
    """The half that differs between profiles goes on the card; the half that does not, does not."""
    screen(_1080P)
    problem = screen_fit.fit_details((800, 600), 2)
    card = ProfileCard(_profile(["a", "b"]), [])
    card.set_unavailable(problem)

    assert problem.numbers in card.quiet_text
    assert "lower the resolution" not in card.quiet_text.lower()
    # The tooltip is read one at a time, so it can afford the whole thing.
    assert card.toolTip() == problem.sentence


def test_an_account_card_keeps_its_characters_and_carries_no_numbers(qapp, screen):
    """Its column's banner is the whole refusal, numbers included -- every card shares it."""
    screen(((900, 600), 1.0))
    account = {"username": "one"}
    roster = [{"name": "Someone Spellthorn"}]
    card = AccountCard(account, roster)

    card.set_unavailable(screen_fit.fit_details((800, 600), 1))

    assert card.unavailable_reason is not None
    # Nothing is added: not the numbers, not the advice. The card goes gray and goes on
    # showing the one thing it is for.
    assert card.quiet_text == ""
    assert card.detail_text == character_summary(roster)
    # Still answerable about itself if asked directly.
    assert "800" in card.toolTip()


def test_the_accounts_banner_names_the_default_resolution_and_two_ways_out(qapp, monkeypatch):
    """The bug: an identical paragraph on every card, because they ask an identical question.

    "Launch fewer clients" is left out on purpose -- an account card launches exactly one,
    so it is a remedy that cannot be taken.
    """
    from bartlebot.gui.game_embed.game_view import GameView

    monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: ((900, 600), 1.0))
    SettingsManager.instance().game_resolution = (800, 600)
    SettingsManager.instance().accounts = _accounts("one", "two", "three")
    SettingsManager.instance().launch_profiles = []

    view = GameView()
    cards = view.findChildren(AccountCard)
    assert len(cards) == 3
    assert all(card.unavailable_reason is not None for card in cards)

    banners = _banner_texts(view)
    assert len(banners) == 1
    text = banners[0]
    assert text.startswith("Your default game resolution (800×600) needs ")
    assert "is available" in text
    assert "increase the size of the game view panel" in text
    assert "fewer clients" not in text
    # Said once above the column, and not again on any of the three cards below it.
    for card in cards:
        assert "resolution" not in card.quiet_text.lower()
        assert "available" not in card.quiet_text.lower()


def test_the_profiles_banner_is_sized_against_the_panel_not_a_resolution(qapp, monkeypatch):
    """Profiles may each pin their own, so naming one would point at the wrong setting."""
    from bartlebot.gui.game_embed.game_view import GameView

    monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: ((900, 600), 1.0))
    SettingsManager.instance().game_resolution = (640, 480)
    SettingsManager.instance().accounts = []
    SettingsManager.instance().launch_profiles = [
        _profile(["a", "b"], (800, 600)),
        _profile(["a", "b"], (1024, 768)),
    ]

    text = _banner_texts(GameView())[0]
    assert text.startswith("No launch profiles fit in the game view panel at its current size (")
    assert "800" not in text and "1024" not in text
    assert "launch fewer clients" in text


def test_a_lone_unfittable_profile_is_not_described_as_a_list(qapp, monkeypatch):
    from bartlebot.gui.game_embed.game_view import GameView

    monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: ((900, 600), 1.0))
    SettingsManager.instance().game_resolution = (640, 480)
    SettingsManager.instance().accounts = []
    SettingsManager.instance().launch_profiles = [_profile(["a", "b"], (800, 600))]

    assert _banner_texts(GameView())[0].startswith("This launch profile does not fit")


def test_the_banner_goes_away_once_everything_fits(qapp, monkeypatch):
    from bartlebot.gui.game_embed.game_view import GameView

    monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: ((3840, 2160), 1.0))
    SettingsManager.instance().game_resolution = (640, 480)
    SettingsManager.instance().accounts = _accounts("one", "two")
    SettingsManager.instance().launch_profiles = []

    assert _banner_texts(GameView()) == []


def test_a_partly_fitting_profile_column_says_some_rather_than_none(qapp, monkeypatch):
    """Counting grayed rows is not how the user should learn that one of them still works."""
    from bartlebot.gui.game_embed.game_view import GameView

    monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: _1080P)
    SettingsManager.instance().game_resolution = (640, 480)
    SettingsManager.instance().accounts = []
    # One 800x600 client fits a 1080p screen and two do not -- the pair this whole
    # module is named for.
    SettingsManager.instance().launch_profiles = [
        _profile(["a"], (800, 600)),
        _profile(["a", "b"], (800, 600)),
    ]

    text = _banner_texts(GameView())[0]
    assert text.startswith("Some launch profiles do not fit")
    assert text.endswith("have been disabled.")
    # The ways out are left to the cards that still work and the ones that do not: this
    # column is not broken, two of its entries are.
    assert "launch fewer clients" not in text


def test_the_banner_link_opens_the_settings_the_advice_points_at(qapp, monkeypatch):
    """ "Lower the resolution" the user then has to go hunting for is half an answer."""
    from bartlebot.gui.game_embed.card_columns import _FitBanner
    from bartlebot.gui.game_embed.game_view import GameView

    monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: ((900, 600), 1.0))
    SettingsManager.instance().game_resolution = (800, 600)
    SettingsManager.instance().accounts = _accounts("one")
    SettingsManager.instance().launch_profiles = []

    view = GameView()
    fired = []
    view.manage_accounts_requested.connect(lambda: fired.append(True))
    banner = next(b for b in view.findChildren(_FitBanner) if b.text)
    banner.findChild(QPushButton).click()
    assert fired == [True]


def _banner_texts(view):
    """What every visible fit banner in *view* says."""
    from bartlebot.gui.game_embed.card_columns import _FitBanner

    return [banner.text for banner in view.findChildren(_FitBanner) if banner.text]


# ---------------------------------------------------------------------------
# The sidebar floors are measured once per font, style and stylesheet
# ---------------------------------------------------------------------------


@pytest.fixture
def counted_measures(qapp, monkeypatch):
    """How many probe rows each floor builds, from a cold cache."""
    from bartleby.qt.sidebar import log_panel  # noqa: PLC0415

    monkeypatch.setattr(screen_fit, "_FLOORS", {})
    built = {"left": 0, "right": 0}
    measure_left, header_row_floor = screen_fit._measure_left_sidebar_floor, log_panel.header_row_floor

    def left(app):
        built["left"] += 1
        return measure_left(app)

    def right(font):
        built["right"] += 1
        return header_row_floor(font)

    monkeypatch.setattr(screen_fit, "_measure_left_sidebar_floor", left)
    monkeypatch.setattr(log_panel, "header_row_floor", right)
    return built


def test_a_floor_asked_for_again_is_not_measured_again(counted_measures):
    """A resize asks once per launch card: 14 probe rows made a drag from maximized freeze."""
    first = (screen_fit.left_sidebar_floor(), screen_fit.right_sidebar_floor())
    for _ in range(14):
        assert (screen_fit.left_sidebar_floor(), screen_fit.right_sidebar_floor()) == first

    assert counted_measures == {"left": 1, "right": 1}


def test_a_new_stylesheet_measures_the_floors_again(qapp, counted_measures):
    """A theme switch is a new app stylesheet, and a stylesheet can widen the probe rows."""
    sheet = qapp.styleSheet()
    screen_fit.left_sidebar_floor()
    screen_fit.right_sidebar_floor()
    try:
        qapp.setStyleSheet(sheet + "\nQComboBox { padding: 0 30px; }")
        screen_fit.left_sidebar_floor()
        screen_fit.right_sidebar_floor()
    finally:
        qapp.setStyleSheet(sheet)

    assert counted_measures == {"left": 2, "right": 2}


# -- what setup fits: a new profile's resolution, and the canvas -----------------------

_1080P_TASKBAR = ((1920, 1032), 1.0)


def test_setup_judges_a_new_profile_at_the_default_canvas(screen):
    """Not the canvas as configured: one fitted to a single account would leave four nothing."""
    screen(_1080P_TASKBAR)
    SettingsManager.instance().set_general("bottom_section_height", 500)

    assert screen_fit.setup_area()[1] == 1032 - game_sizing.BOTCANVAS_DEFAULT_HEIGHT - 60
    assert screen_fit.setup_resolution(4) == (534, 300)


def test_one_client_gets_the_largest_resolution_that_fits(screen):
    """800x600 is a quarter of a 4K screen: setup gives a single account the room there is."""
    screen(_1080P_TASKBAR)

    assert screen_fit.fitted_layout().resolution == (960, 540)


def test_a_resolution_chosen_in_settings_is_kept(screen):
    screen(_1080P_TASKBAR)
    SettingsManager.instance().game_resolution = (854, 480)

    assert screen_fit.fitted_layout().resolution == (854, 480)


def test_the_canvas_takes_the_height_one_client_leaves(screen):
    screen(_1080P_TASKBAR)

    assert screen_fit.fitted_layout().panes["bottom_section_height"] == 1032 - 60 - (540 + 51)


def test_the_canvas_leaves_room_for_the_tallest_launch_profile(screen):
    screen(_1080P_TASKBAR)
    SettingsManager.instance().accounts = [{"username": u, "alias": ""} for u in "abcd"]
    SettingsManager.instance().launch_profiles = [_profile("abcd", (534, 300))]

    assert screen_fit.fitted_layout().panes["bottom_section_height"] == 1032 - 60 - 2 * (300 + 51)


def test_a_launch_profile_that_cannot_fit_shapes_no_pane(screen):
    """Two at 800x600 can't launch on 1080p whatever the panes do."""
    screen(_1080P_TASKBAR)
    alone = screen_fit.fitted_layout().panes
    SettingsManager.instance().accounts = [{"username": u, "alias": ""} for u in "ab"]
    SettingsManager.instance().launch_profiles = [_profile("ab", (800, 600))]

    assert screen_fit.fitted_layout().panes == alone


def test_the_sidebars_share_the_width_the_widest_grid_leaves_evenly(screen):
    screen(_1080P_TASKBAR)
    panes = screen_fit.fitted_layout().panes
    left, right = panes["left_sidebar_width"], panes["right_sidebar_width"]

    assert abs(left - right) <= 1
    assert left + right == 1920 - 2 * game_sizing.SPLITTER_HANDLE_PX - (960 + 12)


def test_a_sidebar_sized_in_settings_keeps_its_size(screen):
    screen(_1080P_TASKBAR)
    SettingsManager.instance().set_general("left_sidebar_width", 450)
    panes = screen_fit.fitted_layout().panes

    assert "left_sidebar_width" not in panes
    assert panes["right_sidebar_width"] == 1920 - 2 * game_sizing.SPLITTER_HANDLE_PX - 972 - 450


def test_an_unmeasurable_screen_fits_nothing(screen):
    screen((screen_fit._UNMEASURABLE, 1.0))

    assert screen_fit.fitted_layout() is None


def test_a_launch_profile_decides_the_layout_and_one_client_fits_what_it_leaves(screen):
    """Two at 560x420 on 1080p keep a 471px-tall game area, and a single client fits in it."""
    screen(_1080P_TASKBAR)
    SettingsManager.instance().accounts = [{"username": u, "alias": ""} for u in "ab"]
    SettingsManager.instance().launch_profiles = [_profile("ab", (560, 420))]

    fitted = screen_fit.fitted_layout()

    assert fitted.panes["bottom_section_height"] == 1032 - 60 - (420 + 51)
    assert fitted.resolution == (746, 420)


def test_one_client_is_fitted_to_a_profile_that_follows_it(screen):
    """A profile with no resolution of its own launches at the global one."""
    screen(_1080P_TASKBAR)
    SettingsManager.instance().accounts = [{"username": u, "alias": ""} for u in "ab"]
    SettingsManager.instance().launch_profiles = [_profile("ab")]

    assert screen_fit.fitted_layout().resolution == (560, 420)


def test_one_client_on_1440p_leaves_the_canvas_its_share(screen):
    """1680x1050 is the largest that fits, but leaves the canvas 231px; 1600x900 leaves 381."""
    screen(((2560, 1392), 1.0))
    fitted = screen_fit.fitted_layout()

    assert fitted.resolution == (1600, 900)
    assert fitted.panes["bottom_section_height"] == 1392 - 60 - (900 + 51)


def test_one_client_on_4k_at_150_percent_keeps_the_largest(screen):
    """2560x1440 leaves 329 of 1400, over the share: the bar is set so this stays."""
    screen(((2560, 1400), 1.5))

    assert screen_fit.fitted_layout().resolution == (2560, 1440)
