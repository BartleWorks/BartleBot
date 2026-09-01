"""Behavioural tests for the Quick Launch cards as widgets.

The text they build is covered in test_launch_cards.py without a QApplication. What
needs one is the part that is genuinely widget behaviour: that clicking a card launches
the thing it stands for, that the edit button does *not* launch, and that a theme switch
reaches a card's labels.
"""

import sys

import pytest
from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QEnterEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication

import bartlebot.gui.theme as theme_mod
from bartlebot.gui.game_embed.launch_cards import AccountCard, ProfileCard

ACCOUNT = {"username": "someone@example.com", "alias": "Main"}
PROFILE = {"name": "Farm Team", "accounts": ["someone@example.com"], "resolution": [1280, 720]}
ROSTER = [{"gid": 1001, "name": "Luke SkullHunter"}]


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def theme():
    before = theme_mod.active_theme()
    yield theme_mod.set_theme
    theme_mod.set_theme_instance(before)


def _click(widget) -> None:
    widget.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPoint(5, 5).toPointF(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def test_clicking_an_account_card_launches_that_account(qapp):
    card = AccountCard(ACCOUNT, ROSTER)
    seen = []
    card.launch_account_requested.connect(lambda u, d: seen.append((u, d)))

    _click(card)

    assert seen == [("someone@example.com", "Main")]


def test_clicking_a_profile_card_launches_that_profile(qapp):
    card = ProfileCard(PROFILE, [ACCOUNT])
    seen = []
    card.launch_profile_requested.connect(seen.append)

    _click(card)

    assert seen == ["Farm Team"]


def test_a_right_click_does_not_launch(qapp):
    """The card is the launch target, but only for the button that means "do it"."""
    card = ProfileCard(PROFILE, [ACCOUNT])
    seen = []
    card.launch_profile_requested.connect(seen.append)

    card.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPoint(5, 5).toPointF(),
            Qt.MouseButton.RightButton,
            Qt.MouseButton.RightButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )

    assert seen == []


def test_the_edit_button_edits_without_launching(qapp):
    """The whole card launches, so the one control on it must consume its own click —
    otherwise opening the editor also starts four game clients."""
    card = ProfileCard(PROFILE, [ACCOUNT])
    launched, edited = [], []
    card.launch_profile_requested.connect(launched.append)
    card.edit_profile_requested.connect(edited.append)

    card._edit_btn.click()

    assert edited == ["Farm Team"]
    assert launched == []


def test_the_edit_button_is_revealed_on_hover(qapp):
    """Secondary to launching: a button visible on every card competes with the name."""
    card = ProfileCard(PROFILE, [ACCOUNT])
    # isVisibleTo, not isVisible: the card is never shown in the test, and isVisible is
    # False for every child of an unshown parent — which would pass whatever we did.
    assert not card._edit_btn.isVisibleTo(card)

    point = QPoint(5, 5).toPointF()
    card.enterEvent(QEnterEvent(point, point, point))
    assert card._edit_btn.isVisibleTo(card)

    card.leaveEvent(QEvent(QEvent.Type.Leave))
    assert not card._edit_btn.isVisibleTo(card)


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
