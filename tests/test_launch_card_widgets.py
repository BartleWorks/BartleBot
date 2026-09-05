"""Behavioural tests for the Quick Launch cards as widgets.

The text they build is covered in test_launch_cards.py without a QApplication. What
needs one is the part that is genuinely widget behaviour: that clicking a card launches
the thing it stands for, that the edit button does *not* launch, and that a theme switch
reaches a card's labels.
"""

import pytest
from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QEnterEvent, QMouseEvent

import bartlebot.gui.theme as theme_mod
from bartlebot.gui.account_accent import account_accent
from bartlebot.gui.game_embed.launch_cards import MAX_CHARACTER_ROWS, AccountCard, ProfileCard
from bartlebot.gui.widgets.tag_pill import AccountPill

ACCOUNT = {"username": "someone@example.com", "alias": "Main"}
PROFILE = {"name": "Farm Team", "accounts": ["someone@example.com"], "resolution": [1280, 720]}
ROSTER = [{"gid": 1001, "name": "Luke SkullHunter"}]


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


def test_the_edit_button_keeps_its_space_while_hidden(qapp):
    """Revealing it on hover must not resize the card: a card that grows under the
    pointer can move out from under it, hiding the button and shrinking back — the
    jitter that made the cards unusable to mouse over."""
    card = ProfileCard(PROFILE, [ACCOUNT])
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
# Account colour
# ---------------------------------------------------------------------------


def _pills(card) -> list[AccountPill]:
    return card.findChildren(AccountPill)


def test_an_account_card_is_striped_with_its_colour(qapp):
    card = AccountCard(ACCOUNT, ROSTER)
    assert f"border-left: 3px solid {account_accent(card.color_key)}" in card.styleSheet()


def test_the_stripe_survives_the_frame_shorthand(qapp):
    """The shorthand sets all four sides, so a stripe written before it would be
    overwritten — and the stylesheet would still look right to anyone reading it."""
    card = AccountCard(ACCOUNT, ROSTER)
    sheet = card.styleSheet()
    assert sheet.index("border:") < sheet.index("border-left:")


def test_a_profile_pill_matches_that_account_card(qapp):
    """The pair is the whole feature: a colour on the pills alone would be decoration,
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
    """The pill holds its palette slot, not a colour, so this is a re-resolve rather
    than a repaint of a stale hex — and a card's stylesheet does not reach its pills."""
    theme("light")
    pill = AccountPill("Main", 1)
    light = pill.styleSheet()

    theme("dark")
    qapp.setStyleSheet(theme_mod.THEME.global_stylesheet())

    assert pill.styleSheet() != light
