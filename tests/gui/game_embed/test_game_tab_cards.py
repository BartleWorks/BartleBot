"""The Game settings tab is the launch screen's two card columns.

It used to draw the identical two lists as ``QListWidget`` rows of formatted text: an
account was a display string, a profile was ``f"{name} ({n} accounts, WxH)"``. Nothing
about the accent color, the character roster or whether the thing would fit survived the
trip — so the surface where accounts are *edited* said strictly less about them than the
surface where they are merely launched, and neither taught you to read the other.

Same cards now. What these tests hold is the three ways the tab's copy has to differ,
each of which is a deliberate decision rather than an accident of reuse:

* a body click **edits**, this being the editing surface;
* a card that will not fit is **noted, not grayed out** — graying out the row the user
  came here to fix is hostile;
* the verbs are **on the card face**, trash included, which is only safe because of the
  first point.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QListWidget, QPushButton

from bartlebot.gui import screen_fit
from bartlebot.gui.game_embed.launch_cards import AccountCard, AddCard, ProfileCard
from bartlebot.gui.settings.accounts_tab import AccountsTab
from bartlebot.settings_manager import SettingsManager

ACCOUNTS = [
    {"username": "one@example.com", "alias": "One"},
    {"username": "two@example.com", "alias": "Two"},
]
PROFILES = [{"name": "Farm Team", "accounts": ["one@example.com", "two@example.com"], "resolution": [1280, 720]}]

# A 4K screen at the default layout: everything fits unless a test asks otherwise.
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
def populated():
    sm = SettingsManager.instance()
    sm.accounts = [dict(a) for a in ACCOUNTS]
    sm.launch_profiles = [dict(p) for p in PROFILES]
    return sm


def _click(widget) -> None:
    """Down and up in the same place — a card activates on release."""
    for kind, buttons in (
        (QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
    ):
        handler = widget.mousePressEvent if kind == QEvent.Type.MouseButtonPress else widget.mouseReleaseEvent
        handler(
            QMouseEvent(
                kind,
                QPoint(5, 5).toPointF(),
                Qt.MouseButton.LeftButton,
                buttons,
                Qt.KeyboardModifier.NoModifier,
            )
        )


# ---------------------------------------------------------------------------
# What it draws
# ---------------------------------------------------------------------------


def test_the_tab_draws_accounts_and_profiles_as_the_launch_screens_cards(qapp, screen, populated):
    screen(_4K)
    tab = AccountsTab()

    assert [type(c) for c in tab.accounts_column.cards] == [AccountCard, AccountCard]
    assert [type(c) for c in tab.profiles_column.cards] == [ProfileCard]


def test_the_tab_keeps_no_list_widget_for_either_column(qapp, screen, populated):
    """The rows are gone, and with them the selection that fed the button rows.

    One ``QListWidget`` survives, inside the profile editor's own account list — that
    dialog is a different problem and explicitly out of the Game tab card rework's scope. Any second one means
    a column came back.
    """
    screen(_4K)
    tab = AccountsTab()

    assert tab.findChildren(QListWidget) == []


@pytest.mark.parametrize("column", ["accounts_column", "profiles_column"])
def test_each_column_ends_with_its_add_card(qapp, screen, populated, column):
    screen(_4K)
    tab = AccountsTab()
    rows = getattr(tab, column).scroll_area.widget().layout()
    widgets = [rows.itemAt(i).widget() for i in range(rows.count())]

    assert isinstance([w for w in widgets if w is not None][-1], AddCard)


def test_neither_banner_offers_a_link_to_the_settings_it_is_already_in(qapp, screen, populated):
    """ "Change resolution" pointing at a combo box forty pixels above it teaches nothing."""
    screen(_TINY)
    SettingsManager.instance().game_resolution = (1280, 720)
    tab = AccountsTab()

    assert tab.accounts_column.banner_text, "test needs a screen the cards do not fit"
    links = [b for b in tab.findChildren(QPushButton) if b.text() == "Change resolution"]
    assert [b for b in links if b.isVisibleTo(tab)] == []


# ---------------------------------------------------------------------------
# What a click means here
# ---------------------------------------------------------------------------


def test_clicking_an_account_card_edits_it_rather_than_launching_it(qapp, screen, populated, monkeypatch):
    """The tab's primary verb. The two surfaces are never on screen together."""
    screen(_4K)
    tab = AccountsTab()
    edited: list[str] = []
    monkeypatch.setattr(
        "bartlebot.gui.settings.accounts_tab.edit_account", lambda u, *args, **kwargs: edited.append(u) or False
    )

    _click(tab.accounts_column.cards[0])

    assert edited == ["one@example.com"]


def test_clicking_a_profile_card_edits_it_rather_than_launching_it(qapp, screen, populated, monkeypatch):
    screen(_4K)
    tab = AccountsTab()
    edited: list[str] = []
    monkeypatch.setattr(
        "bartlebot.gui.settings.accounts_tab.edit_launch_profile",
        lambda n, *args, **kwargs: edited.append(n) or False,
    )

    _click(tab.profiles_column.cards[0])

    assert edited == ["Farm Team"]


# ---------------------------------------------------------------------------
# Fit is a note here, not a state
# ---------------------------------------------------------------------------


def test_a_card_that_would_not_fit_is_noted_and_still_editable(qapp, screen, populated, monkeypatch):
    """Graying out the row the user came here to fix is hostile.

    On the launch screen a card goes gray because clicking it would launch something that
    cannot fit. Here, clicking it is how the user fixes that — so the refusal reaches the
    column banner and the card's tooltip, and stops there.
    """
    screen(_TINY)
    SettingsManager.instance().game_resolution = (1280, 720)
    tab = AccountsTab()
    card = tab.profiles_column.cards[0]
    edited: list[str] = []
    monkeypatch.setattr(
        "bartlebot.gui.settings.accounts_tab.edit_launch_profile",
        lambda n, *args, **kwargs: edited.append(n) or False,
    )

    assert card.unavailable_reason is not None, "test needs a screen the card does not fit"
    assert card.refuses_when_unfit is False
    assert card.cursor().shape() == Qt.CursorShape.PointingHandCursor

    _click(card)

    assert edited == ["Farm Team"]


def test_the_same_card_on_the_launch_screen_still_refuses(qapp, screen, populated):
    """The other half: nothing here may loosen the launch screen's own refusal.

    The click still lands, and selects: the launch screen says why its Launch button is
    off rather than leaving the card to ignore the pointer, so the cursor still promises
    that clicking does something."""
    screen(_TINY)
    SettingsManager.instance().game_resolution = (1280, 720)
    card = ProfileCard(PROFILES[0], ACCOUNTS)
    card.set_unavailable("needs more room")
    launched: list[str] = []
    card.launch_profile_requested.connect(launched.append)

    _click(card)

    assert card.refuses_when_unfit is True
    assert card.activate() is False
    assert launched == []
    assert card.cursor().shape() == Qt.CursorShape.PointingHandCursor


# ---------------------------------------------------------------------------
# The verbs on the card face
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("column", ["accounts_column", "profiles_column"])
def test_the_cards_carry_their_verbs_openly_here(qapp, screen, populated, column):
    """Pinned rather than revealed on hover, and a trash beside the pencil.

    Hiding the verbs behind hover on the one surface that exists to run them is backwards,
    and the trash is safe here for exactly the reason it is not on the launch screen: a
    body click edits, so a mis-aim 22px away costs a dialog and not a game client.
    """
    screen(_4K)
    tab = AccountsTab()
    card = getattr(tab, column).cards[0]

    tooltips = [button.toolTip() for button in card.action_buttons]
    assert len(tooltips) == 2
    assert any(t.startswith("Edit") for t in tooltips)
    assert any(t.startswith("Delete") for t in tooltips)
    assert all(not button.isHidden() for button in card.action_buttons)


def test_the_launch_screens_cards_keep_the_trash_off_their_faces(qapp):
    """A trash icon 22px from a body whose double-click starts a game client is a
    mis-click that costs a stored credential and a character roster. It stays on the
    right-click menu there."""
    card = ProfileCard(PROFILES[0], ACCOUNTS)

    assert card.action_button("Delete") is None
    assert card.action_button("Edit").isHidden()
