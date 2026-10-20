"""Behavioral tests for the Quick Launch cards as widgets.

The text they build is covered in test_launch_cards.py without a QApplication. What
needs one is the part that is genuinely widget behavior: that clicking a card selects it
and double-clicking launches the thing it stands for, that the edit button does *not*
launch, and that a theme switch reaches a card's labels. The screen that holds the
selection and the Launch button is test_game_view.py.
"""

import pytest
from PyQt6.QtCore import QEvent, QObject, QPoint, Qt
from PyQt6.QtGui import QEnterEvent, QMouseEvent
from PyQt6.QtWidgets import QWidget

import bartlebot.gui.theming.theme as theme_mod
from bartlebot.gui.game_embed.card_columns import CardColumn
from bartlebot.gui.game_embed.game_view import _GamePlaceholder
from bartlebot.gui.game_embed.launch_cards import MAX_CHARACTER_ROWS, AccountCard, CardMode, ProfileCard
from bartlebot.gui.settings.accounts_tab import AccountsTab
from bartlebot.gui.theming.account_accent import account_accent
from bartlebot.gui.widgets.pills.tag_pill import AccountPill

ACCOUNT = {"username": "someone@example.com", "alias": "Main"}
PROFILE = {"name": "Farm Team", "accounts": ["someone@example.com"], "resolution": [1280, 720]}
ROSTER = [{"gid": 1001, "name": "Luke SkullHunter"}]


@pytest.fixture
def theme():
    before = theme_mod.active_theme()
    yield theme_mod.set_theme
    theme_mod.set_theme_instance(before)


def _press(widget, button=Qt.MouseButton.LeftButton, at=QPoint(5, 5)) -> None:
    widget.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            at.toPointF(),
            button,
            button,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def _release(widget, button=Qt.MouseButton.LeftButton, at=QPoint(5, 5)) -> None:
    widget.mouseReleaseEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            at.toPointF(),
            button,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def _click(widget, button=Qt.MouseButton.LeftButton) -> None:
    """A full click: down and up in the same place.

    Both halves, because a card acts on *release* and only for a pointer that never
    moved -- the column it sits in turns a press-and-drag into a reorder, and a card that
    fired on press launched what it was being dragged past.
    """
    _press(widget, button)
    _release(widget, button)


def _double_click(widget, at=QPoint(5, 5)) -> None:
    """What Qt delivers for one: press, release, double-click, release."""
    _click(widget)
    widget.mouseDoubleClickEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonDblClick,
            at.toPointF(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    _release(widget)


@pytest.mark.parametrize("factory", [lambda: AccountCard(ACCOUNT, ROSTER), lambda: ProfileCard(PROFILE, [ACCOUNT])])
def test_a_click_selects_and_does_not_launch(qapp, factory):
    """The fix for #266: a click on a launch card only asks to be selected."""
    card = factory()
    picked, activated = [], []
    card.picked.connect(lambda: picked.append(1))
    card.activated.connect(lambda: activated.append(1))

    _click(card)

    assert picked == [1]
    assert activated == []


def test_double_clicking_an_account_card_launches_that_account(qapp):
    card = AccountCard(ACCOUNT, ROSTER)
    seen = []
    card.launch_account_requested.connect(lambda u, d: seen.append((u, d)))

    _double_click(card)

    assert seen == [("someone@example.com", "Main")]


def test_double_clicking_a_profile_card_launches_that_profile(qapp):
    card = ProfileCard(PROFILE, [ACCOUNT])
    seen = []
    card.launch_profile_requested.connect(seen.append)

    _double_click(card)

    assert seen == ["Farm Team"]


def test_the_double_click_launches_once_and_selects_first(qapp):
    """Its first click is an ordinary one, so the shortcut also leaves the card selected."""
    card = ProfileCard(PROFILE, [ACCOUNT])
    picked, seen = [], []
    card.picked.connect(lambda: picked.append(1))
    card.launch_profile_requested.connect(seen.append)

    _double_click(card)

    assert picked == [1]
    assert seen == ["Farm Team"]


def test_a_double_click_that_moved_does_not_launch(qapp):
    """The same stayed-put test as a click: a second press that wanders is a sweep."""
    card = ProfileCard(PROFILE, [ACCOUNT])
    seen = []
    card.launch_profile_requested.connect(seen.append)

    _click(card)
    card.mouseDoubleClickEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonDblClick,
            QPoint(5, 5).toPointF(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    _release(card, at=QPoint(5, 60))

    assert seen == []


def test_a_click_in_the_game_tab_still_edits_at_once(qapp):
    """Selection is the launch screen's alone: a click that edits costs nothing to undo."""
    card = ProfileCard(PROFILE, [ACCOUNT], mode=CardMode.MANAGE)
    edited, picked = [], []
    card.edit_profile_requested.connect(edited.append)
    card.picked.connect(lambda: picked.append(1))

    _click(card)

    assert edited == ["Farm Team"]
    assert picked == []


def test_a_right_click_neither_selects_nor_launches(qapp):
    """The card is the target, but only for the button that means "do it"."""
    card = ProfileCard(PROFILE, [ACCOUNT])
    seen, picked = [], []
    card.launch_profile_requested.connect(seen.append)
    card.picked.connect(lambda: picked.append(1))

    _click(card, Qt.MouseButton.RightButton)

    assert seen == []
    assert picked == []


def test_a_selected_card_is_filled_and_outlined(qapp):
    card = ProfileCard(PROFILE, [ACCOUNT])
    resting = card.styleSheet()

    card.set_selected(True)

    assert card.is_selected
    assert theme_mod.THEME.node_fill_selected in card.styleSheet()
    assert card.styleSheet() != resting


def test_a_selected_card_that_will_not_fit_stays_gray_but_outlined(qapp):
    """The refusal palette replaces the fill, so the outline is what still says "this one"."""
    card = ProfileCard(PROFILE, [ACCOUNT])
    card.set_unavailable("needs more room")
    unselected = card.styleSheet()

    card.set_selected(True)

    assert theme_mod.THEME.surface_disabled in card.styleSheet()
    assert f"border: 1px solid {theme_mod.THEME.accent}" in card.styleSheet()
    assert card.styleSheet() != unselected


def test_the_edit_button_edits_without_launching(qapp):
    """The whole card is a target, so the one control on it must consume its own click —
    otherwise opening the editor also selects, or starts, four game clients."""
    card = ProfileCard(PROFILE, [ACCOUNT])
    launched, edited = [], []
    card.launch_profile_requested.connect(launched.append)
    card.edit_profile_requested.connect(edited.append)

    card.action_button("Edit").click()

    assert edited == ["Farm Team"]
    assert launched == []


def test_an_account_cards_pencil_edits_without_launching(qapp):
    """Same rule on the other card type, which grew a pencil of its own with the card action verbs."""
    card = AccountCard(ACCOUNT, ROSTER)
    launched, edited = [], []
    card.launch_account_requested.connect(lambda *a: launched.append(a))
    card.edit_account_requested.connect(edited.append)

    card.action_button("Edit").click()

    assert edited == [ACCOUNT["username"]]
    assert launched == []


@pytest.mark.parametrize("factory", [lambda: AccountCard(ACCOUNT, ROSTER), lambda: ProfileCard(PROFILE, [ACCOUNT])])
def test_the_edit_button_is_drawn_without_hovering(qapp, factory):
    """#266: revealed on hover, the pencil appeared under the pointer as it arrived, and
    testers aiming at it launched instead. The grip is still revealed on hover."""
    card = factory()
    edit_btn = card.action_button("Edit")
    # isVisibleTo, not isVisible: the card is never shown in the test, and isVisible is
    # False for every child of an unshown parent — which would fail whatever we did.
    assert edit_btn.isVisibleTo(card)
    assert not card.grip.isVisibleTo(card)

    point = QPoint(5, 5).toPointF()
    card.enterEvent(QEnterEvent(point, point, point))
    assert edit_btn.isVisibleTo(card)
    assert card.grip.isVisibleTo(card)

    card.leaveEvent(QEvent(QEvent.Type.Leave))
    assert edit_btn.isVisibleTo(card)
    assert not card.grip.isVisibleTo(card)


@pytest.mark.parametrize("factory", [lambda: AccountCard(ACCOUNT, ROSTER), lambda: ProfileCard(PROFILE, [ACCOUNT])])
def test_a_theme_switch_restyles_the_card(qapp, theme, factory):
    card = factory()
    theme("light")
    card.refresh_theme_styles()
    light = card.styleSheet()

    theme("dark")
    card.refresh_theme_styles()

    assert card.styleSheet() != light


def test_an_account_with_no_known_characters_still_builds(qapp):
    """The state every account is in until one of its characters has been played."""
    card = AccountCard(ACCOUNT, [])
    assert card is not None


@pytest.mark.parametrize("factory", [lambda: AccountCard(ACCOUNT, ROSTER), lambda: ProfileCard(PROFILE, [ACCOUNT])])
def test_hovering_does_not_resize_the_card(qapp, factory):
    """Revealing the grip on hover must not resize the card: a card that grows under the
    pointer can move out from under it, hiding the grip and shrinking back — the jitter
    that made the cards unusable to mouse over."""
    card = factory()
    card.resize(300, card.sizeHint().height())
    hidden = card.sizeHint()

    point = QPoint(5, 5).toPointF()
    card.enterEvent(QEnterEvent(point, point, point))

    assert card.sizeHint() == hidden


def test_a_narrow_account_card_drops_names_a_wide_one_shows(qapp):
    """The count of names shown is a property of the card's width, not a constant."""
    roster = [{"gid": 1000 + i, "name": f"Wizard Nameyname{i}"} for i in range(6)]
    card = AccountCard(ACCOUNT, roster)
    # Shown, because Qt does not deliver a resize event to a hidden widget — and the
    # fit hangs off that event, which is the thing being tested.
    card.show()

    card.resize(2000, 60)
    qapp.processEvents()
    wide = card._detail_lbl.text()
    card.resize(160, 60)
    qapp.processEvents()
    narrow = card._detail_lbl.text()
    card.close()

    assert "more" not in wide
    assert narrow.endswith("more")
    assert len(narrow) < len(wide)


def test_an_account_card_uses_more_rows_before_dropping_a_name(qapp):
    """Five accounts do not fill the column, so a card that has run out of width grows
    downwards rather than hiding characters."""
    roster = [{"gid": 1000 + i, "name": f"Wizard Nameyname{i}"} for i in range(6)]
    card = AccountCard(ACCOUNT, roster)
    card.show()

    card.resize(420, 120)
    qapp.processEvents()
    text = card._detail_lbl.text()
    card.close()

    assert "more" not in text
    assert 1 < len(text.split("\n")) <= MAX_CHARACTER_ROWS


# ---------------------------------------------------------------------------
# Account color
# ---------------------------------------------------------------------------


def _pills(card) -> list[AccountPill]:
    return card.findChildren(AccountPill)


def test_an_account_card_is_striped_with_its_color(qapp):
    card = AccountCard(ACCOUNT, ROSTER)
    assert f"border-left: 3px solid {account_accent(card.color_key)}" in card.styleSheet()


def test_the_stripe_survives_the_frame_shorthand(qapp):
    """The shorthand sets all four sides, so a stripe written before it would be
    overwritten — and the stylesheet would still look right to anyone reading it."""
    card = AccountCard(ACCOUNT, ROSTER)
    sheet = card.styleSheet()
    assert sheet.index("border:") < sheet.index("border-left:")


def test_a_profile_pill_matches_that_account_card(qapp):
    """The pair is the whole feature: a color on the pills alone would be decoration,
    because nothing else on the screen would say which account it stands for."""
    account_card = AccountCard(ACCOUNT, ROSTER)
    profile_card = ProfileCard(PROFILE, [ACCOUNT])

    (pill,) = _pills(profile_card)

    assert pill.color_key == account_card.color_key


def test_the_resolution_pill_stays_neutral(qapp):
    """It names a setting, not an account. A hue on it would put the one pill that says
    nothing about membership into the same language as the ones that do."""
    card = ProfileCard(PROFILE, [ACCOUNT])
    assert len(_pills(card)) == 1


def test_a_theme_switch_restyles_an_account_pill(qapp, theme):
    """The pill holds its palette slot, not a color, so this is a re-resolve rather
    than a repaint of a stale hex — and a card's stylesheet does not reach its pills."""
    theme("light")
    pill = AccountPill("Main", 1)
    light = pill.styleSheet()

    theme("dark")
    qapp.setStyleSheet(theme_mod.THEME.global_stylesheet())

    assert pill.styleSheet() != light


# ---------------------------------------------------------------------------
# Nothing a card builds is a window
# ---------------------------------------------------------------------------


class _WindowShowWatcher(QObject):
    """Records every widget that becomes a *top-level window* while installed."""

    def __init__(self) -> None:
        super().__init__()
        self.shown: list[QWidget] = []

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
            self.shown.append(obj)
        return False


def _windows_shown_by(qapp, build):
    watcher = _WindowShowWatcher()
    qapp.installEventFilter(watcher)
    try:
        build()
    finally:
        qapp.removeEventFilter(watcher)
    return watcher.shown


def _column_with_cards():
    column = CardColumn("Accounts", "hint", "Add account")
    column.set_cards([AccountCard(ACCOUNT, ROSTER)])
    return column


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: AccountCard(ACCOUNT, ROSTER), id="account"),
        pytest.param(lambda: ProfileCard(PROFILE, [ACCOUNT]), id="profile"),
        pytest.param(lambda: CardColumn("Accounts", "hint", "Add account"), id="column"),
        pytest.param(_column_with_cards, id="column-with-cards"),
        pytest.param(lambda: _GamePlaceholder(), id="launch-screen"),
        pytest.param(lambda: AccountsTab(), id="game-tab"),
    ],
)
def test_building_a_card_puts_no_window_on_the_screen(qapp, build):
    """A widget's children must be its children from birth.

    A parentless QWidget *is* a top-level window, so ``setVisible(True)`` on one — which
    the quiet line does whenever it has text — shows a label-sized window for the frame
    between there and the layout reparenting it. The user sees a flash in the corner of
    the display every time the cards are rebuilt, and the profile editor rebuilds them on
    the way out.

    Extended past the cards to the columns and both surfaces that host them, because the
    same mistake landed one level up in the Game tab card rework: a ``_FitBanner`` hiding its "Change
    resolution" link before the layout had adopted the button, flashing twice on every
    app start. Anything that shows or hides a child in its constructor belongs on this
    list.
    """
    assert _windows_shown_by(qapp, build) == []
