"""The Add card at the foot of each Quick Launch column.

A fresh install opened on two columns reading "(No accounts)" and "(No profiles)". That
is a dead end: nothing on either column said what to do about it, and the only way
forward was a small underlined link between them, a dialog, the right tab, and a button
under a list. The Add card replaces that hint outright and is the whole first-run path.

Three things it has to be, and each of them is one of the sections below: last in its
column and never a member of it, immune to the fit pass that greys the cards above it,
and a route to the same editor the Game tab's Add button opens.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QEnterEvent, QMouseEvent
from PyQt6.QtWidgets import QDialog

import bartlebot.gui.theme as theme_mod
from bartlebot.gui import screen_fit
from bartlebot.gui.game_embed.card_columns import EMPTY_ACCOUNTS_HINT, EMPTY_PROFILES_HINT
from bartlebot.gui.game_embed.game_view import GameView, _GamePlaceholder
from bartlebot.gui.game_embed.launch_cards import ADD_CARD_BORDER_WIDTH, AccountCard, AddCard, ProfileCard
from bartlebot.gui.settings import accounts_tab
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.settings_manager import SettingsManager
from tests.conftest import main_window_stub

# A 4K screen at the default layout: everything fits, so a card is greyed only when a
# test asks for it to be.
_4K = ((3840, 2160), 1.0)
# Small enough that nothing this app launches fits inside it.
_TINY = ((800, 600), 1.0)


@pytest.fixture
def screen(monkeypatch):
    """Pin the screen the fit is judged against, so the result is not the runner's."""

    def _use(metrics):
        monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: metrics)

    return _use


@pytest.fixture
def theme():
    before = theme_mod.active_theme()
    yield theme_mod.set_theme
    theme_mod.set_theme_instance(before)


def _populate(accounts: list[str], profiles: list[str]) -> None:
    """Store *accounts* and *profiles*, every profile launching every account.

    A profile with no accounts launches nothing, so it fits any screen -- which would
    quietly defeat the test that needs the cards above the Add card to be greyed out.
    """
    sm = SettingsManager.instance()
    usernames = [f"{name}@example.com" for name in accounts]
    sm.accounts = [{"username": u, "alias": name} for u, name in zip(usernames, accounts)]
    sm.launch_profiles = [{"name": name, "accounts": list(usernames)} for name in profiles]


def _rows(area) -> list:
    """Every widget in a column, in the order it is drawn."""
    layout = area.widget().layout()
    items = (layout.itemAt(i) for i in range(layout.count()))
    return [w for w in (item.widget() for item in items) if w is not None]


def _columns(view: GameView) -> tuple[list, list]:
    """The Accounts column's rows and the Launch Profiles column's, in that order.

    *view* must be held by the caller for as long as the rows are read: a GameView built
    inline is freed at the end of the expression that built it, and every widget under it
    goes with it -- ``isinstance`` still answers on the corpse, but ``text()`` raises.
    """
    placeholder = view.findChild(_GamePlaceholder)
    return (
        _rows(placeholder.accounts_column.scroll_area),
        _rows(placeholder.profiles_column.scroll_area),
    )


def _click(widget) -> None:
    point = QPoint(5, 5).toPointF()
    widget.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            point,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


# ---------------------------------------------------------------------------
# Where the card sits
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("column", [0, 1], ids=["accounts", "profiles"])
def test_a_full_column_ends_with_its_add_card(qapp, screen, column):
    """Always last. It is the column's terminator, not one of its entries."""
    screen(_4K)
    _populate(["one", "two"], ["Team A", "Team B"])

    view = GameView()
    rows = _columns(view)[column]

    assert [type(row) for row in rows[:-1]] == [(AccountCard, ProfileCard)[column]] * 2
    assert isinstance(rows[-1], AddCard)


@pytest.mark.parametrize(
    ("column", "hint"),
    [(0, EMPTY_ACCOUNTS_HINT), (1, EMPTY_PROFILES_HINT)],
    ids=["accounts", "profiles"],
)
def test_an_empty_column_is_the_add_card_and_one_line_saying_what_it_adds(qapp, screen, column, hint):
    """The lone Add card *is* the empty state, and says more than "(No accounts)" did.

    The line beneath it survives because a first-run user does not yet know what a launch
    profile is -- and only while the column is empty, since after that the cards are the
    explanation.
    """
    screen(_4K)
    _populate([], [])

    view = GameView()
    rows = _columns(view)[column]

    assert isinstance(rows[0], AddCard)
    assert [row.text() for row in rows if isinstance(row, ThemedTextLabel)] == [hint]


@pytest.mark.parametrize("column", [0, 1], ids=["accounts", "profiles"])
def test_the_old_empty_hint_is_gone_once_the_column_has_something_in_it(qapp, screen, column):
    screen(_4K)
    _populate(["one"], ["Team A"])

    view = GameView()
    rows = _columns(view)[column]

    assert not [row for row in rows if isinstance(row, ThemedTextLabel)]


@pytest.mark.parametrize("column", [0, 1], ids=["accounts", "profiles"])
def test_no_column_still_says_none_of_these(qapp, screen, column):
    """The dead end itself: a hint that names the problem and no way out of it."""
    screen(_4K)
    _populate([], [])

    view = GameView()
    texts = [row.text() for row in _columns(view)[column] if isinstance(row, ThemedTextLabel)]

    assert not any(text.startswith("(No ") for text in texts)


# ---------------------------------------------------------------------------
# What the fit pass may do to it
# ---------------------------------------------------------------------------


def test_a_screen_too_small_for_the_game_greys_the_cards_but_not_the_add_card(qapp, screen):
    """It launches nothing and has no resolution to judge.

    Greying it out would tell a user whose screen is too small for Wizard101 that it is
    also too small to type a username into -- on the one screen whose whole job is to get
    them out of that state. ``refresh_fit`` walks the launch cards, and the Add card is
    kept out of that list rather than skipped inside it, so nothing can put it back.
    """
    screen(_TINY)
    SettingsManager.instance().game_resolution = (1280, 720)
    _populate(["one"], ["Team A"])

    view = GameView()
    accounts, profiles = _columns(view)

    assert accounts[0].unavailable_reason is not None, "test needs a screen the cards refuse"
    assert profiles[0].unavailable_reason is not None
    for add in (accounts[-1], profiles[-1]):
        assert isinstance(add, AddCard)
        assert not hasattr(add, "set_unavailable")
        assert add.cursor().shape() == Qt.CursorShape.PointingHandCursor


def test_the_add_card_is_not_one_of_the_cards_the_fit_pass_walks(qapp, screen):
    screen(_4K)
    _populate(["one"], ["Team A"])
    view = GameView()

    placeholder = view.findChild(_GamePlaceholder)
    # Read off the columns deliberately: ``CardColumn.cards`` is the list the fit pass
    # iterates, and the assertion is about its membership.
    for column in (placeholder.accounts_column, placeholder.profiles_column):
        assert not any(isinstance(card, AddCard) for card in column.cards)


# ---------------------------------------------------------------------------
# Clicking it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("column", [0, 1], ids=["accounts", "profiles"])
def test_clicking_the_add_card_asks_for_the_editor(qapp, screen, column):
    screen(_4K)
    _populate(["one"], ["Team A"])
    view = GameView()
    fired: list[str] = []
    view.add_account_requested.connect(lambda: fired.append("account"))
    view.add_profile_requested.connect(lambda: fired.append("profile"))

    _click(_columns(view)[column][-1])

    assert fired == [("account", "profile")[column]]


def test_a_right_click_does_not_open_the_editor(qapp):
    """The card is a hit target for the button that means "do it", as the launch cards are."""
    card = AddCard("Add account")
    fired = []
    card.add_requested.connect(lambda: fired.append(True))

    point = QPoint(5, 5).toPointF()
    card.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            point,
            Qt.MouseButton.RightButton,
            Qt.MouseButton.RightButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )

    assert fired == []


def test_hovering_restyles_the_card(qapp):
    """Dashed and quiet at rest, filled on hover -- a hit target confirming itself."""
    card = AddCard("Add account")
    at_rest = card.styleSheet()

    point = QPoint(5, 5).toPointF()
    card.enterEvent(QEnterEvent(point, point, point))
    hovered = card.styleSheet()
    card.leaveEvent(QEvent(QEvent.Type.Leave))

    assert "dashed" in at_rest and "dashed" in hovered
    assert hovered != at_rest
    assert card.styleSheet() == at_rest


def test_the_dashes_are_wide_enough_to_be_dashes(qapp):
    """Qt scales a dash pattern by the pen width, so 1px dashed draws as 1px dots.

    At the subtle border colour that is a line you have to look for, and on a dark card's
    top and left edges it was not visible at all -- which loses the outline that is the
    whole reason this reads as an empty slot rather than a card.
    """
    card = AddCard("Add account")

    assert ADD_CARD_BORDER_WIDTH > 1
    assert f"{ADD_CARD_BORDER_WIDTH}px dashed" in card.styleSheet()


def test_a_theme_switch_restyles_the_add_card(qapp, theme):
    card = AddCard("Add account")
    theme("light")
    card.refresh_theme_styles()
    light = card.styleSheet()

    theme("dark")
    card.refresh_theme_styles()

    assert card.styleSheet() != light


# ---------------------------------------------------------------------------
# add_account / add_launch_profile
# ---------------------------------------------------------------------------
# The Add card and the Game tab's Add button run the same module function, which is what
# keeps the card from having to build a Settings dialog to reach one editor -- the same
# seam the profile-editor dialog work cut for the profile editor.


class _FakeAccountDialog:
    """Stands in for _AccountDialog: exec() is the one thing a test cannot run."""

    accepted = True
    result_username = "new@example.com"
    result_alias = "New"
    result_password = "hunter2"
    result_color = None
    seen: dict = {}

    def __init__(self, username="", alias="", color=None, taken=None, parent=None):
        type(self).seen = {"username": username, "alias": alias, "color": color, "taken": taken, "parent": parent}

    def exec(self):
        return QDialog.DialogCode.Accepted if type(self).accepted else QDialog.DialogCode.Rejected

    @property
    def username(self):
        return type(self).result_username

    @property
    def alias(self):
        return type(self).result_alias

    @property
    def password(self):
        return type(self).result_password

    @property
    def color(self):
        return type(self).result_color


@pytest.fixture
def account_editor(monkeypatch):
    """The account editor, stubbed, with the keyring write recorded rather than made."""

    class Dialog(_FakeAccountDialog):
        pass

    stored: list[tuple[str, str]] = []
    monkeypatch.setattr(accounts_tab, "_AccountDialog", Dialog)
    monkeypatch.setattr(accounts_tab.CredentialStore, "store", staticmethod(lambda u, p: stored.append((u, p))))
    # A refusal puts up a modal warning, which would block the suite. Its return is never
    # read; what the refusal does is decline to save, which is what is asserted.
    monkeypatch.setattr(accounts_tab.QMessageBox, "warning", lambda *a, **k: None)
    SettingsManager.instance().accounts = [{"username": "one@example.com", "alias": "One"}]
    Dialog.stored = stored
    return Dialog


def _accounts():
    return SettingsManager.instance().accounts


def test_an_accepted_account_is_stored_with_its_password(account_editor):
    assert accounts_tab.add_account() is True
    assert _accounts()[-1] == {"username": "new@example.com", "alias": "New"}
    assert account_editor.stored == [("new@example.com", "hunter2")]


def test_a_cancelled_account_changes_nothing(account_editor):
    account_editor.accepted = False

    assert accounts_tab.add_account() is False
    assert len(_accounts()) == 1
    assert account_editor.stored == []


@pytest.mark.parametrize("field", ["result_username", "result_password"])
def test_an_account_missing_a_username_or_a_password_is_refused(account_editor, field):
    """The password specifically: an account with no credential cannot launch anything."""
    setattr(account_editor, field, "")

    assert accounts_tab.add_account() is False
    assert len(_accounts()) == 1
    assert account_editor.stored == []


def test_an_account_that_already_exists_is_refused_without_overwriting_its_password(account_editor):
    account_editor.result_username = "one@example.com"

    assert accounts_tab.add_account() is False
    assert _accounts() == [{"username": "one@example.com", "alias": "One"}]
    assert account_editor.stored == []


class _FakeProfileDialog:
    accepted = True
    result_name = "New Team"
    result_accounts: list[str] = ["one@example.com"]
    result_resolution: tuple[int, int] | None = None
    seen: dict = {}

    def __init__(self, name="", selected_usernames=None, resolution=None, parent=None, live_game_area=None):
        type(self).seen = {"parent": parent, "live_game_area": live_game_area}

    def exec(self):
        return QDialog.DialogCode.Accepted if type(self).accepted else QDialog.DialogCode.Rejected

    @property
    def name(self):
        return type(self).result_name

    @property
    def selected_usernames(self):
        return type(self).result_accounts

    @property
    def resolution(self):
        return type(self).result_resolution


@pytest.fixture
def profile_editor(monkeypatch):
    class Dialog(_FakeProfileDialog):
        pass

    monkeypatch.setattr(accounts_tab, "ProfileDialog", Dialog)
    monkeypatch.setattr(accounts_tab.QMessageBox, "warning", lambda *a, **k: None)
    SettingsManager.instance().launch_profiles = [{"name": "Farm Team", "accounts": []}]
    return Dialog


def _profiles():
    return SettingsManager.instance().launch_profiles


def test_an_accepted_profile_is_appended(profile_editor):
    profile_editor.result_resolution = (800, 600)

    assert accounts_tab.add_launch_profile() is True
    assert _profiles()[-1] == {
        "name": "New Team",
        "accounts": ["one@example.com"],
        "resolution": [800, 600],
    }


def test_a_profile_that_pins_no_resolution_stores_no_key(profile_editor):
    profile_editor.result_resolution = None

    assert accounts_tab.add_launch_profile() is True
    assert "resolution" not in _profiles()[-1]


def test_the_new_profile_editor_is_handed_the_live_game_area(profile_editor):
    """Passed through rather than dropped: the picker has to judge fit the same way the
    surface behind it does."""
    accounts_tab.add_launch_profile(None, (1200, 700))

    assert profile_editor.seen["live_game_area"] == (1200, 700)


@pytest.mark.parametrize("name", ["", "Farm Team"], ids=["empty", "duplicate"])
def test_a_profile_with_no_name_or_a_taken_one_is_refused(profile_editor, name):
    profile_editor.result_name = name

    assert accounts_tab.add_launch_profile() is False
    assert [p["name"] for p in _profiles()] == ["Farm Team"]


def test_a_cancelled_profile_changes_nothing(profile_editor):
    profile_editor.accepted = False

    assert accounts_tab.add_launch_profile() is False
    assert [p["name"] for p in _profiles()] == ["Farm Team"]


# ---------------------------------------------------------------------------
# The slots behind the two cards
# ---------------------------------------------------------------------------
# Called unbound against a stand-in: MainWindow is far too heavy to build for this, and
# what is under test is which refreshes run -- the same second-order bug as the profile-editor dialog work.


def _main_window_stub(monkeypatch, saved: bool):
    monkeypatch.setattr(accounts_tab, "add_account", lambda *a, **k: saved)
    monkeypatch.setattr(accounts_tab, "add_launch_profile", lambda *a, **k: saved)
    return main_window_stub()


@pytest.mark.parametrize("slot", ["_add_account", "_add_launch_profile"])
def test_something_added_refreshes_the_menu_and_the_cards(monkeypatch, slot):
    from bartlebot.gui.main_window import MainWindow

    calls, stub = _main_window_stub(monkeypatch, saved=True)

    getattr(MainWindow, slot)(stub)

    assert calls == ["menu", "minimum", "cards"]


@pytest.mark.parametrize("slot", ["_add_account", "_add_launch_profile"])
def test_a_cancelled_add_still_re_judges_the_cards(monkeypatch, slot):
    """The screen behind a modal editor has been sitting on cards nothing re-judged."""
    from bartlebot.gui.main_window import MainWindow

    calls, stub = _main_window_stub(monkeypatch, saved=False)

    getattr(MainWindow, slot)(stub)

    assert calls == ["cards"]
