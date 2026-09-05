"""The window's *enforced* floor never exceeds the one the fit arithmetic declared.

``SettingsManager.minimum_window_size`` is not only what the window is dragged down to;
it is the number ``screen_budget`` and ``fits`` reason about when they decide a launch
will fit on this screen. Qt does not take that number on trust — it holds the window to
the larger of it and what the layout inside needs — so a pane whose content asks for more
raises the real floor above the declared one, and the fit is computed against a window
that cannot exist.

That is not hypothetical: 2560x1440 cleared the fit on a 3840x2160 screen, and the window
Qt then built had a 3770x2100 floor against a 2081px-tall work area. Windows refused the
geometry and clipped the window, which clips the game the whole ladder exists to protect.

So each pane declares the arithmetic's own term as an explicit minimum — the one thing
that wins over a layout's minimum in ``qSmartMinSize`` — and these tests hold the two
sides together. They are worth having as widget tests rather than pure ones precisely
because the failure was the gap between the arithmetic and the widgets.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QWidget

from bartlebot import game_sizing as gs
from bartlebot.gui import screen_fit
from bartlebot.gui.canvas.bot_canvas import BotCanvas
from bartlebot.gui.main_window import MainWindow
from bartlebot.gui.sidebar.left_sidebar import LeftSidebar
from bartlebot.gui.sidebar.right_sidebar import RightSidebar
from bartlebot.settings_manager import VALID_RESOLUTIONS, SettingsManager

# The Players tab, and the two things between its viewport and the sidebar's own edge
# that no widget reports on its own: the tab widget's frame, and the room a machine with
# larger system text needs for the same layout.
_PLAYERS_TAB = 0
_TAB_CHROME_PX = 8
_PLAYERS_CONTENT_MARGINS_PX = 16  # RightSidebar's players_vbox, 8 a side


class _FakeClient:
    """Enough of a client for a ``ClientWidget`` to be built against it.

    The card reads the rest of what it shows asynchronously, on a refresh the suite never
    spins an event loop for, so its width is settled by the layout alone.
    """

    process_id = 1234
    title = "Wizard101"


# Enough of the ladder to cover both ends and the size that broke: the widest offered
# rung, a stock one, and the smallest thing on the list.
_RESOLUTIONS = [(2560, 1440), (1280, 720), min(VALID_RESOLUTIONS, key=lambda r: r[0] * r[1])]


def _window_floor(window: MainWindow) -> tuple[int, int]:
    """The size Windows will not let *window* go below, on each axis.

    The larger of what the window declares and what its layout needs — which is the whole
    subject here. ``minimumSize()`` does not absorb the layout's minimum: the report this
    came from printed a 3016x1680 ``minimum size`` and a ``mintrack`` of 3770x2100 in the
    same line, and it was the second number the window was actually held to.
    """
    explicit, layout_min = window.minimumSize(), window.minimumSizeHint()
    return max(explicit.width(), layout_min.width()), max(explicit.height(), layout_min.height())


@pytest.fixture
def open_window(qapp):
    """Open a window whose declared floor is the one *resolution* implies.

    The resolution is set before construction because the floor is declared once, in
    ``__init__`` — setting it afterwards would leave the window declaring one size and
    being measured against another.
    """

    def _open(resolution: tuple[int, int] = (2560, 1440), client_count: int = 1) -> MainWindow:
        SettingsManager.instance().game_resolution = resolution
        window = MainWindow()
        # Stand in for an embedded fleet by raising the game view's minimum as one would.
        # ``GameView._update_minimum_size`` needs live windows to measure; the minimum it
        # arrives at is public arithmetic, so the tiles can be left out of it without
        # weakening what is asserted.
        window.game_view.setMinimumSize(*gs.required_game_area(resolution, client_count))
        window.ensurePolished()
        return window

    return _open


# ---------------------------------------------------------------------------
# The invariant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("resolution", _RESOLUTIONS)
@pytest.mark.parametrize("client_count", [1, 2, 4])
def test_the_declared_floor_is_the_one_qt_enforces(open_window, resolution, client_count):
    window = open_window(resolution, client_count)

    assert _window_floor(window) <= gs.required_window_size(resolution, client_count)


def test_a_fat_tab_does_not_raise_the_window_s_floor(open_window):
    """Edit mode swaps the parameter panel in, which hints 562px wide before a bot loads.

    The sidebar tab sets are mode-swapped, so the widest thing either sidebar can hold is
    not in it at construction — which is exactly why measuring at construction missed this.
    """
    window = open_window((2560, 1440))
    window.left_sidebar.set_editable(True)
    window.right_sidebar.switch_to_log()
    window.ensurePolished()

    assert _window_floor(window) <= gs.required_window_size((2560, 1440))


def test_the_window_that_was_refused_fits_the_screen_that_refused_it(open_window):
    """The reported failure, as an assertion: 1440p on a 4K screen, work area and all."""
    window = open_window((2560, 1440))
    window.left_sidebar.set_editable(True)
    window.ensurePolished()
    work_area = (3840, 2081)  # 3840x2160 less the taskbar and the window frame

    assert _window_floor(window) <= work_area


# ---------------------------------------------------------------------------
# The terms it is built from
# ---------------------------------------------------------------------------


def test_each_sidebar_declares_the_floor_the_arithmetic_budgets_for_it(qapp):
    """Explicitly, because only an explicit minimum wins over a layout's in qSmartMinSize.

    A sidebar whose floor came from its content instead would be whatever the widest tab
    happened to need — 562px for the parameter panel, against the 220px budgeted here.
    """
    assert LeftSidebar(QWidget()).minimumSize() == QSize(gs.LEFT_SIDEBAR_MIN_WIDTH, gs.SIDEBAR_MIN_HEIGHT)
    assert RightSidebar().minimumSize() == QSize(screen_fit.right_sidebar_floor(), gs.SIDEBAR_MIN_HEIGHT)


def test_the_player_card_fits_the_right_sidebar_at_its_floor(qapp):
    """A floor is only worth having at a width the panel is still usable at.

    The card's text lines already elide, so what sets its width is the tool row —
    ``Entities``, ``UI Tree`` and ``Unhook`` side by side. A ``QPushButton`` carries a
    ``Minimum`` size policy, which has no shrink flag, so those three never render below
    their own size hints; and a ``QScrollArea`` does not propagate its content's minimum,
    so nothing upstream pushes back. The row therefore runs off the panel rather than
    being squeezed or scrolled to, which is what a 300px-wide sidebar was doing to
    ``Unhook`` in the report this came from.

    Measured against the card's natural width for that reason, with two of them so the
    vertical scrollbar is in the viewport and counted, and against the floor *this machine*
    reports rather than the constant: both sides are font metrics, so a runner with larger
    system text has to see them grow together or this asserts nothing.
    """
    sidebar = RightSidebar()
    sidebar.clients.set_all_clients([_FakeClient(), _FakeClient()], [])
    players_tab = sidebar.widget(_PLAYERS_TAB)

    needed = (
        sidebar.clients.sizeHint().width()
        + _PLAYERS_CONTENT_MARGINS_PX
        + 2 * players_tab.frameWidth()
        + players_tab.verticalScrollBar().sizeHint().width()
        + _TAB_CHROME_PX
    )

    assert needed <= screen_fit.right_sidebar_floor()


def test_the_bot_canvas_declares_the_floor_the_arithmetic_budgets_for_it(qapp):
    assert BotCanvas().minimumSize() == QSize(gs.BOTCANVAS_MIN_WIDTH, gs.BOTCANVAS_MIN_HEIGHT)


def test_the_game_panel_s_own_chrome_fits_its_allowance(open_window):
    """The panel's header strip is a term in the sum, so its allowance has to cover it.

    Measured rather than asserted equal: it is a font-dependent strip, and the constant is
    an upper bound. Only exceeding it is a bug — that is a floor the window has and the
    arithmetic does not.
    """
    window = open_window()
    window.game_view.setMinimumSize(1000, 1000)
    window.ensurePolished()

    chrome = window.game_view_panel.minimumSizeHint().height() - 1000
    assert 0 <= chrome <= gs.GAME_PANEL_CHROME_PX


def test_a_sidebar_never_becomes_the_top_row_s_floor():
    """The window minimum has no sidebar *height* term, so the game view must dominate.

    Pure arithmetic: if a sidebar could be taller than the shortest tile on the ladder,
    ``required_window_size`` would understate the floor for that rung and the invariant
    above would only hold by luck of which resolutions are offered.
    """
    shortest_tile = min(gs.tile_size(r)[1] for r in VALID_RESOLUTIONS)

    assert gs.SIDEBAR_MIN_HEIGHT <= shortest_tile


@pytest.mark.parametrize(
    ("text_height", "expected"),
    [
        (None, gs.RIGHT_SIDEBAR_MIN_WIDTH),  # not measured: the reference
        (16, gs.RIGHT_SIDEBAR_MIN_WIDTH),  # the reference height itself
        (20, 375),  # 125% system text
        (24, 450),  # 150%
        (8, gs.RIGHT_SIDEBAR_MIN_WIDTH),  # smaller text does not narrow the scrollbar
    ],
)
def test_the_floor_grows_with_the_system_text_size(text_height, expected):
    """Which is the point of measuring it: a pixel count would have to choose.

    Pinned at 300 it clips the machine at 125%; pinned at 375 it charges a 1080p screen a
    rung of the ladder for a text size it is not using.
    """
    assert gs.right_sidebar_floor(text_height) == expected


def test_the_floor_the_widget_holds_is_the_floor_the_budget_subtracts(qapp):
    """The two must be the same number, or the failure this module is about is back.

    Raising the application font is the cheapest way to move both at once. It is
    process-global state, so it is put back — see "One QApplication per worker" in
    tests/CLAUDE.md.
    """
    original = qapp.font()
    bigger = QFont(original)
    bigger.setPointSizeF(original.pointSizeF() * 1.5)
    try:
        qapp.setFont(bigger)
        sidebar = RightSidebar()
        floor = screen_fit.right_sidebar_floor()

        assert floor > gs.RIGHT_SIDEBAR_MIN_WIDTH  # the font change actually moved it
        assert sidebar.minimumSize().width() == floor
        assert SettingsManager.instance().layout_budget((1920, 1080), right_sidebar_floor=floor) == gs.screen_budget(
            (1920, 1080),
            left_sidebar=300,
            right_sidebar=floor,
            bottom_section=220,
            splitter_handle=gs.SPLITTER_HANDLE_PX,
        )
    finally:
        qapp.setFont(original)


def test_the_budget_never_assumes_a_panel_narrower_than_its_floor(qapp):
    """A stored width under the floor describes a layout the splitter will not produce.

    Budgeting against it would hand the game area space it cannot have — the same gap
    between the arithmetic and the widgets as above, arriving from the settings file
    rather than from a fat tab.
    """
    settings = SettingsManager.instance()
    settings.set_general("left_sidebar_width", 10)
    settings.set_general("right_sidebar_width", 10)
    settings.set_general("bottom_section_height", 10)
    screen = (1920, 1080)

    budget = settings.layout_budget(screen, right_sidebar_floor=screen_fit.right_sidebar_floor())

    assert budget == gs.screen_budget(
        screen,
        left_sidebar=gs.LEFT_SIDEBAR_MIN_WIDTH,
        right_sidebar=screen_fit.right_sidebar_floor(),
        bottom_section=gs.BOTCANVAS_MIN_HEIGHT,
        splitter_handle=gs.SPLITTER_HANDLE_PX,
    )


def test_the_bot_canvas_never_becomes_the_window_s_width_floor():
    """Same argument on the other axis: the canvas spans the window, so it has no term."""
    narrowest_window = min(gs.required_window_size(r)[0] for r in VALID_RESOLUTIONS)

    assert gs.BOTCANVAS_MIN_WIDTH <= narrowest_window


def test_the_settings_floor_and_the_arithmetic_agree(qapp):
    sm = SettingsManager.instance()

    assert sm.minimum_window_size() == gs.required_window_size(sm.game_resolution)
