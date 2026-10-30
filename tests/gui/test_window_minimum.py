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
from bartleby.qt import screen_fit
from bartleby.qt.sidebar.log_panel import LogPanel
from bartleby.qt.sidebar.option_gutter import BAR_WIDTH_PX, GUTTER_BTN_PX, GUTTER_GAP_PX, GUTTER_TO_BODY_PX
from bartleby.runtime.game import game_sizing as gs
from bartleby.runtime.game.game_sizing import VALID_RESOLUTIONS
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QBoxLayout, QComboBox, QScrollArea, QSizePolicy, QStyleFactory, QWidget

from bartlebot.bot.schema import DropModel
from bartlebot.game import pane_sizing
from bartlebot.gui import window_panes
from bartlebot.gui.canvas.bot_canvas import BotCanvas
from bartlebot.gui.canvas.drop_keep_list import DropKeepListWidget
from bartlebot.gui.main_window import MainWindow
from bartlebot.gui.sidebar.left_sidebar import LeftSidebar
from bartlebot.gui.sidebar.option_card import BODY_MARGIN_PX
from bartlebot.gui.sidebar.right_sidebar import RightSidebar
from bartlebot.settings_manager import SettingsManager

# The Players tab, and the two things between its viewport and the sidebar's own edge
# that no widget reports on its own: the tab widget's frame, and the room a machine with
# larger system text needs for the same layout.
_PLAYERS_TAB = 0
_TAB_CHROME_PX = 8
_PLAYERS_CONTENT_MARGINS_PX = 16  # RightSidebar's players_vbox, 8 a side

# The left sidebar's equivalent: everything between an option row's body and the panel
# edge. The gutter is the override bar, the two 20px chrome buttons and the gap to the
# body; the margins are ``LeftSidebar._wrap``'s content vbox, 8 a side. The scroll area's
# own frame and scrollbar are measured from the widget rather than named here.
_GUTTER_PX = BAR_WIDTH_PX + GUTTER_GAP_PX + 2 * GUTTER_BTN_PX + GUTTER_TO_BODY_PX
_SETUP_CONTENT_MARGINS_PX = 16
# The option card the keep list sits in: its 1px border and its body's padding, each side.
_CARD_INSET_PX = 2 * (1 + BODY_MARGIN_PX)


class _FakeClient:
    """Enough of a client for a ``PlayerCard`` to be built against it.

    The card reads the rest of what it shows asynchronously, on a refresh the suite never
    spins an event loop for, so its width is settled by the layout alone. ``window_handle``
    is what ``ClientSection`` keys its cards by across a rebuild, so two of these must not
    share one — a colliding pair would be re-used as a single card and this would measure
    a column of one.
    """

    process_id = 1234
    title = "Wizard101"

    _next_handle = 0

    def __init__(self) -> None:
        type(self)._next_handle += 1
        self.window_handle = type(self)._next_handle


# Enough of the ladder to cover both ends and the size that broke: the widest offered
# rung, a stock one, and the smallest thing on the list.
_RESOLUTIONS = [(2560, 1440), (1280, 720), min(VALID_RESOLUTIONS, key=lambda r: r[0] * r[1])]


def _player_card_needs(sidebar: RightSidebar) -> int:
    """How wide the sidebar has to be for the first player card to draw uncropped.

    Measured from the **card**, not from ``ClientSection``. The section's own size hint
    stops at the ``CardColumn``'s scroll area, which does not propagate its content's
    width — 166px for a card wanting 232 — so a floor asserted against the section is a
    floor asserted against nothing at all. That non-propagation is the whole reason this
    test exists: nothing upstream pushes back, so the card runs off the panel rather than
    being squeezed or scrolled to.

    The terms beyond the card are the ones no widget reports: the players page's own
    margins, the column's vertical scrollbar (in the viewport once there is a second
    card), and the tab widget's frame.
    """
    card = sidebar.clients.cards[0]
    scrollbar = sidebar.clients.column.scroll_area.verticalScrollBar()
    return card.sizeHint().width() + _PLAYERS_CONTENT_MARGINS_PX + scrollbar.sizeHint().width() + _TAB_CHROME_PX


def _row_floor(row: QBoxLayout) -> int:
    """The narrowest *row* can be laid out at without a widget clipping its neighbor.

    A widget whose horizontal policy carries ``ShrinkFlag`` is taken at its minimum hint
    and everything else at its natural one, which is what a ``QBoxLayout`` itself does —
    and is the distinction the whole floor turns on. ``minimumSize()`` on the row would
    answer with the combobox's own minimum hint, which is an elided label: that is the
    clipped state the sidebar width bump reports, not a width anything fits at.
    """
    total = row.spacing() * (row.count() - 1)
    for index in range(row.count()):
        widget = row.itemAt(index).widget()
        shrinks = bool(widget.sizePolicy().horizontalPolicy().value & QSizePolicy.PolicyFlag.ShrinkFlag.value)
        total += (widget.minimumSizeHint() if shrinks else widget.sizeHint()).width()
    return total


def _filter_row_of(picker: DropKeepListWidget) -> QBoxLayout:
    """The keep-list picker's filter row, found by what is in it rather than by index.

    Its position in the outer column is an implementation detail that has already moved
    once; being the one row holding the category dropdown is not.
    """
    outer = picker.layout()
    for index in range(outer.count()):
        row = outer.itemAt(index).layout()
        if row is None:
            continue
        if any(isinstance(row.itemAt(i).widget(), QComboBox) for i in range(row.count())):
            return row
    raise AssertionError("the keep-list picker has no filter row")


def _keep_list_needs() -> int:
    """How wide the left sidebar has to be for the keep-list picker's filter row.

    The picker is built against a synthetic drop table rather than a bot's, with the
    longest category name the shipped library uses and a three-digit count, because the
    combobox sizes itself to the widest *item in its model* — a picker holding one short
    label would agree with any floor at all.
    """
    drops = [
        DropModel(name=f"Drop {n}", category=category)
        for category in ("Treasure Cards", "Reagents", "Snacks")
        for n in range(120)
    ]
    picker = DropKeepListWidget(lambda: drops)
    picker.refresh()
    picker.ensurePolished()

    # A scroll area of the sidebar's own kind, for the two numbers that are style metrics
    # rather than constants: how thick its frame is, and how wide its scrollbar is.
    scroll = QScrollArea()
    return (
        _row_floor(_filter_row_of(picker))
        + _GUTTER_PX
        + _CARD_INSET_PX
        + _SETUP_CONTENT_MARGINS_PX
        + 2 * scroll.frameWidth()
        + scroll.verticalScrollBar().sizeHint().width()
    )


def _declared_floor(resolution: tuple[int, int], client_count: int = 1) -> tuple[int, int]:
    """The arithmetic's own answer, measured the way the window measures it.

    ``MainWindow._apply_minimum_size`` reaches ``required_window_size`` through
    ``SettingsManager.minimum_window_size`` and hands it the two floors ``screen_fit``
    measures on *this* machine, so the arithmetic side of the invariant has to be given
    the same two numbers. Left at the reference constants it quietly asserts that the
    runner resolves the reference *style* as well — which a dev box on ``windowsvista``
    does and CI on ``windows11`` does not, by 33px of sidebar between them. Both sides
    are style and font metrics, so they have to be allowed to grow together, exactly as
    the two sidebar tests below already say of themselves.
    """
    return gs.required_window_size(resolution, client_count, window_panes.panes())


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

    assert _window_floor(window) <= _declared_floor(resolution, client_count)


def test_a_fat_tab_does_not_raise_the_window_s_floor(open_window):
    """Edit mode swaps the parameter panel in, which hints 562px wide before a bot loads.

    The sidebar tab sets are mode-swapped, so the widest thing either sidebar can hold is
    not in it at construction — which is exactly why measuring at construction missed this.
    """
    window = open_window((2560, 1440))
    window.left_sidebar.set_editable(True)
    window.right_sidebar.switch_to_log()
    window.ensurePolished()

    assert _window_floor(window) <= _declared_floor((2560, 1440))


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
    happened to need — 562px for the parameter panel, against the width budgeted here.
    """
    assert LeftSidebar(QWidget()).minimumSize() == QSize(window_panes.left_sidebar_floor(), gs.SIDEBAR_MIN_HEIGHT)
    assert RightSidebar().minimumSize() == QSize(screen_fit.right_sidebar_floor(), gs.SIDEBAR_MIN_HEIGHT)


def test_the_keep_list_filter_row_fits_the_left_sidebar_at_its_floor(qapp):
    """The left sidebar's counterpart to the player card, and the subject of the sidebar width bump.

    The panel's floor was a bare 220px, justified by a comment saying its content elides
    or scrolls. Most of it does. The quicksell keep-list picker's filter row does not: a
    ``QComboBox`` and a ``QCheckBox`` both carry a ``Minimum`` policy, which has no shrink
    flag, so at 220px the "Kept only" box was drawn over the dropdown rather than being
    squeezed or scrolled to.

    Measured against the floor *this machine* reports rather than the constant, for the
    same reason as the right sidebar: both sides are font and style metrics.
    """
    assert _keep_list_needs() <= window_panes.left_sidebar_floor()


def test_the_keep_list_filter_row_fits_the_left_sidebar_under_every_available_style(qapp):
    """The same assertion, asked of the styles this machine can build.

    Not hypothetical here either: the first probe stood a bare ``QLineEdit`` in for the
    search field, which reported 27px less than the real one — enough that under
    ``windows11``, the style CI resolves, the measured scale stayed at 1.0 and the floor
    never moved off its reference while the row it had to hold grew by 10px.

    The style is process-global, like the font — see "One QApplication per worker" in
    tests/CLAUDE.md — so it goes back.
    """
    original = qapp.style().objectName()
    try:
        for name in QStyleFactory.keys():
            style = QStyleFactory.create(name)
            if style is None:  # pragma: no cover - a key that will not build
                continue
            qapp.setStyle(style)

            assert _keep_list_needs() <= window_panes.left_sidebar_floor(), f"{name} style"
    finally:
        qapp.setStyle(QStyleFactory.create(original))


def test_the_log_header_row_fits_the_right_sidebar_at_its_floor(qapp):
    """The row that *is* the right sidebar's floor now, so this is the tight one.

    The player card below only has to stay under the floor; this row sets it. A caption,
    a fixed-width combo and two fixed-width buttons, none of which elide or wrap -- so if
    the chrome allowance were short, the Clear button would run off the panel exactly as
    ``Unhook`` did before any of this was measured.
    """
    assert LogPanel(SettingsManager.instance()).minimumSizeHint().width() <= screen_fit.right_sidebar_floor()


def test_the_log_header_row_fits_the_right_sidebar_under_every_available_style(qapp):
    """The same assertion, asked of the styles this machine can build.

    The row is mostly pinned widths, so the font barely moves it -- but a combo box's
    frame and a push button's padding are the *style's*, and a style that spends more on
    them is exactly the machine the reference metrics do not describe. Same sweep, same
    reason, as the two rows either side of this one.

    The style is process-global, like the font — see "One QApplication per worker" in
    tests/CLAUDE.md — so it goes back.
    """
    original = qapp.style().objectName()
    try:
        for name in QStyleFactory.keys():
            style = QStyleFactory.create(name)
            if style is None:  # pragma: no cover - a key that will not build
                continue
            qapp.setStyle(style)

            assert LogPanel(SettingsManager.instance()).minimumSizeHint().width() <= screen_fit.right_sidebar_floor(), (
                f"{name} style"
            )
    finally:
        qapp.setStyle(QStyleFactory.create(original))


def test_the_player_card_fits_the_right_sidebar_at_its_floor(qapp):
    """A floor is only worth having at a width the panel is still usable at.

    The card's text rows all elide, so what sets its width is the pair of freecam buttons
    and the three icon verbs beside them. A ``QPushButton`` carries a ``Minimum`` size
    policy, which has no shrink flag, so those never render below their own size hints;
    and a ``QScrollArea`` does not propagate its content's minimum, so nothing upstream
    pushes back. The row therefore runs off the panel rather than being squeezed or
    scrolled to, which is what a 300px-wide sidebar was doing to ``Unhook`` in the report
    this came from.

    Measured against the card's natural width for that reason, with two of them so the
    vertical scrollbar is in the viewport and counted, and against the floor *this machine*
    reports rather than the constant: both sides are style and font metrics, so a runner
    with larger system text or a wider-buttoned style has to see them grow together or
    this asserts nothing. It once did not — the floor scaled on the text height only, and
    CI, which resolves the ``windows11`` style where a button's minimum is 81px rather
    than 75, needed 303px inside the 300 it was promised.

    The floor itself did not move when the Players tab card rework replaced the labeled tool row with icon
    verbs. The card only ever needs less than it did, so the margin here got wider rather
    than the number getting smaller — see ``RightSidebar._apply_width_floor``.
    """
    sidebar = RightSidebar()
    sidebar.clients.set_all_clients([_FakeClient(), _FakeClient()], [])

    assert _player_card_needs(sidebar) <= screen_fit.right_sidebar_floor()


def test_the_player_card_fits_the_right_sidebar_under_every_available_style(qapp):
    """The same assertion as above, asked of the styles this machine can build.

    The one above only ever sees the style this machine happens to resolve, which is how a
    3px overflow reached CI: a developer box on ``windowsvista`` and a runner on
    ``windows11`` disagree about a push button's minimum width while agreeing about every
    font metric. Sweeping the styles asks the question locally that only CI was asking.

    The style is process-global, like the font — see "One QApplication per worker" in
    tests/CLAUDE.md — so it goes back, and each sidebar is built after the swap because
    the floor is declared once, in ``__init__``.
    """
    original = qapp.style().objectName()
    try:
        for name in QStyleFactory.keys():
            style = QStyleFactory.create(name)
            if style is None:  # pragma: no cover - a key that will not build
                continue
            qapp.setStyle(style)
            sidebar = RightSidebar()
            sidebar.clients.set_all_clients([_FakeClient(), _FakeClient()], [])

            assert _player_card_needs(sidebar) <= screen_fit.right_sidebar_floor(), f"{name} style"
    finally:
        qapp.setStyle(QStyleFactory.create(original))


def test_the_bot_canvas_declares_the_floor_the_arithmetic_budgets_for_it(qapp):
    assert BotCanvas().minimumSize() == QSize(pane_sizing.BOTCANVAS_MIN_WIDTH, pane_sizing.BOTCANVAS_MIN_HEIGHT)


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
    ("log_row", "expected"),
    [
        (None, gs.RIGHT_SIDEBAR_MIN_WIDTH),  # not measured: the reference
        (gs.REFERENCE_LOG_ROW_PX, gs.RIGHT_SIDEBAR_MIN_WIDTH),  # the reference row itself
        (289, 308),  # the row at 14px body text
        (298, 317),  # at 16px
        (240, gs.RIGHT_SIDEBAR_MIN_WIDTH),  # a narrower row does not narrow the scroll bar
    ],
)
def test_the_right_floor_follows_its_forcing_row(log_row, expected):
    """Which is the point of measuring it: a pixel count would have to choose.

    Pinned at 300 it clips the machine at 125% system text; pinned at 375 it charges a
    1080p screen a rung of the ladder for a text size it is not using. The row carries
    the style as well as the font -- a ``QPushButton`` is never narrower than its style's
    own minimum, and that is not a font metric -- so one measurement answers both.
    """
    assert gs.right_sidebar_floor(log_row) == expected


@pytest.mark.parametrize(
    ("filter_row", "expected"),
    [
        (None, pane_sizing.LEFT_SIDEBAR_MIN_WIDTH),  # not measured: the reference
        (pane_sizing.REFERENCE_FILTER_ROW_PX, pane_sizing.LEFT_SIDEBAR_MIN_WIDTH),  # windowsvista, the reference style
        (301, 412),  # windows11 — wider comboboxes and checkboxes at the same text height
        (324, 435),  # the row at 14px body text
        (274, pane_sizing.LEFT_SIDEBAR_MIN_WIDTH),  # Fusion, narrower: it does not narrow the panel
    ],
)
def test_the_left_floor_follows_its_forcing_row(filter_row, expected):
    """The same shape on the left, from a combobox and a checkbox instead of a button."""
    assert pane_sizing.left_sidebar_floor(filter_row) == expected


@pytest.mark.parametrize(
    ("floor", "reference_row", "reference_floor"),
    [
        (pane_sizing.left_sidebar_floor, pane_sizing.REFERENCE_FILTER_ROW_PX, pane_sizing.LEFT_SIDEBAR_MIN_WIDTH),
        (gs.right_sidebar_floor, gs.REFERENCE_LOG_ROW_PX, gs.RIGHT_SIDEBAR_MIN_WIDTH),
    ],
    ids=["left", "right"],
)
def test_a_wider_row_costs_its_own_width_and_no_more(floor, reference_row, reference_floor):
    """The chrome is added, never scaled -- the bug this model replaced.

    Both floors used to be the whole reference width times the larger of two ratios, so a
    row that grew 11% grew the floor 19%: the font's growth was charged against the scroll
    bar, the frame, the margins and the pinned control widths too, none of which a font
    moves. Between the two panels that was 129px of sidebar at 14px body text, which on a
    1080p screen took three- and four-client launches away entirely.

    A row 30px wider therefore costs exactly 30px of panel.
    """
    for grew_by in (1, 30, 80):
        assert floor(reference_row + grew_by) == reference_floor + grew_by


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
        right, left = RightSidebar(), LeftSidebar(QWidget())
        right_floor, left_floor = screen_fit.right_sidebar_floor(), window_panes.left_sidebar_floor()

        # The font change actually moved them, so the rest asserts something.
        assert right_floor > gs.RIGHT_SIDEBAR_MIN_WIDTH
        assert left_floor > pane_sizing.LEFT_SIDEBAR_MIN_WIDTH
        assert right.minimumSize().width() == right_floor
        assert left.minimumSize().width() == left_floor
        panes = window_panes.panes()
        assert [pane.floor for pane in panes[:2]] == [left_floor, right_floor]
        sizes = SettingsManager.instance().pane_sizes()
        assert gs.layout_budget((1920, 1080), panes, sizes) == (
            1920 - max(left_floor, sizes["left_sidebar_width"]) - max(right_floor, sizes["right_sidebar_width"]) - 16,
            1080 - 220 - gs.WINDOW_CHROME_ESTIMATE_PX,
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

    budget = gs.layout_budget(screen, window_panes.panes(), settings.pane_sizes())

    assert budget == (
        1920 - window_panes.left_sidebar_floor() - screen_fit.right_sidebar_floor() - 2 * gs.SPLITTER_HANDLE_PX,
        1080 - pane_sizing.BOTCANVAS_MIN_HEIGHT - gs.WINDOW_CHROME_ESTIMATE_PX,
    )


def test_the_bot_canvas_never_becomes_the_window_s_width_floor():
    """Same argument on the other axis: the canvas spans the window, so it has no term."""
    narrowest_window = min(gs.required_window_size(r, 1, pane_sizing.panes())[0] for r in VALID_RESOLUTIONS)

    assert pane_sizing.BOTCANVAS_MIN_WIDTH <= narrowest_window


def test_the_settings_floor_and_the_arithmetic_agree(qapp):
    sm = SettingsManager.instance()

    assert sm.minimum_window_size() == gs.required_window_size(sm.game_resolution, 1, pane_sizing.panes())
