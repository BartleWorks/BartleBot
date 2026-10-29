"""The launch profile editor: one membership, two controls, and one way to change it.

The dialog has two ways to take an account out of a profile — the cross on its card and
unticking it in the account picker — and the whole risk of that arrangement is the two
disagreeing. So most of what is here is the same assertion from both directions: after
either gesture, the column and the picker say the same thing.

The rest is the ordering rule. A profile's account order decides which window becomes
Player 1, so it is not a display preference: a tick appends, an untick leaves the others
where they were, and neither may be re-derived from the picker, which reports its
selection in section order rather than in the order the user built.
"""

from __future__ import annotations

import pytest

from bartlebot.gui import screen_fit
from bartlebot.gui.game_embed.launch_cards import AccountCard, CardMode
from bartlebot.gui.settings.profile_editor import (
    ProfileDialog,
    account_sections,
    reconcile_membership,
    roster_label,
)
from bartlebot.settings_manager import SettingsManager

ONE = "one@example.com"
TWO = "two@example.com"
THREE = "three@example.com"

ACCOUNTS = [
    {"username": ONE, "alias": "One"},
    {"username": TWO, "alias": "Two"},
    {"username": THREE, "alias": "Three"},
]

# A 4K screen at the default layout, so nothing is refused for want of room.
_4K = ((3840, 2160), 1.0)


@pytest.fixture
def screen(monkeypatch):
    """Pin the screen the fit is judged against, so the result is not the runner's."""
    monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: _4K)


@pytest.fixture
def stored():
    sm = SettingsManager.instance()
    sm.accounts = [dict(a) for a in ACCOUNTS]
    return sm


def _tick(dialog, keys: list[str]) -> None:
    """Tick *keys* in the real popup, opened the way the Add button opens it.

    Driven through an actual ``PickerPopup`` rather than by calling the dialog's slot,
    because half of what these tests are about is that the popup is built from the
    membership rather than keeping its own copy of it.
    """
    dialog.open_account_picker()
    popup = dialog.account_picker
    assert popup is not None, "the Add button should have opened a picker"
    popup.selection_changed.emit(list(keys))


def _usernames(dialog) -> list[str]:
    return [card.action_button("Remove").toolTip() for card in dialog.accounts_column.cards]


# ---------------------------------------------------------------------------
# The rule both removal paths obey. Pure — no QApplication.
# ---------------------------------------------------------------------------


def test_a_new_account_lands_at_the_end():
    """It is the next player. Slotting it anywhere else renumbers the ones already there."""
    assert reconcile_membership([ONE, TWO], [ONE, TWO, THREE]) == [ONE, TWO, THREE]


def test_the_order_survives_a_selection_reported_in_another_order():
    """The picker reports in section order, which is not the order the user built."""
    assert reconcile_membership([THREE, ONE], [ONE, TWO, THREE]) == [THREE, ONE, TWO]


def test_a_removal_leaves_everything_else_where_it_was():
    assert reconcile_membership([ONE, TWO, THREE], [ONE, THREE]) == [ONE, THREE]


def test_an_empty_selection_empties_the_profile():
    assert reconcile_membership([ONE, TWO], []) == []


def test_a_repeated_name_is_taken_once():
    """A caller may hand over a list it has not de-duplicated; two cards for one account
    would be two player slots pointed at one login."""
    assert reconcile_membership([], [ONE, TWO, ONE]) == [ONE, TWO]


def test_the_roster_meta_carries_its_unit():
    assert roster_label(0) == "no characters yet"
    assert roster_label(1) == "1 character"
    assert roster_label(4) == "4 characters"


def test_every_account_is_offered_and_none_is_dimmed():
    """The roster is learned as clients connect, so on a fresh install dimming the ones
    with no characters would dim every row on the surface used to set them up."""
    ((title, rows),) = account_sections(ACCOUNTS, {ONE: [{"gid": 1, "name": "Luke SkullHunter"}]})

    assert title == "Accounts"
    assert [row.key for row in rows] == [ONE, TWO, THREE]
    assert [row.label for row in rows] == ["One", "Two", "Three"]
    assert [row.meta for row in rows] == ["1 character", "no characters yet", "no characters yet"]
    assert not any(row.dim for row in rows)
    # A color each, and each one distinct: the dot is the same hue as the account's
    # stripe on the card below it and its pill on every profile card elsewhere.
    assert len({row.accent for row in rows}) == 3


# ---------------------------------------------------------------------------
# The dialog
# ---------------------------------------------------------------------------


def test_the_column_draws_the_profile_in_player_order(qapp, screen, stored):
    dlg = ProfileDialog(name="Farm Team", selected_usernames=[TWO, ONE])

    cards = dlg.accounts_column.cards
    assert [type(card) for card in cards] == [AccountCard, AccountCard]
    assert dlg.selected_usernames == [TWO, ONE]
    # The pills say which player each will be, so the order can be read rather than
    # counted -- which is the whole reason the column's heading claims one.
    assert [card.action_button("Remove").toolTip() for card in cards] == [
        "Remove 'Two' from this profile",
        "Remove 'One' from this profile",
    ]


def test_a_member_card_carries_a_cross_and_nothing_else(qapp, screen, stored):
    """Not the pencil, and above all not the trash: this card's one verb removes an
    account from a profile, and the card beside it in the Game tab deletes the account."""
    dlg = ProfileDialog(selected_usernames=[ONE])
    (card,) = dlg.accounts_column.cards

    assert card.mode is CardMode.MEMBER
    assert [button.toolTip() for button in card.action_buttons] == ["Remove 'One' from this profile"]
    assert card.action_button("Edit") is None
    assert card.action_button("Delete") is None


def test_a_member_cards_body_does_nothing(qapp, screen, stored):
    """A row in a list being assembled is not a thing to open. Nothing is connected to
    ``activated``, so the cursor and the hover highlight must not promise otherwise."""
    from PyQt6.QtCore import Qt

    dlg = ProfileDialog(selected_usernames=[ONE])
    (card,) = dlg.accounts_column.cards

    assert card.has_body_verb is False
    assert card.cursor().shape() == Qt.CursorShape.ArrowCursor
    assert card.pins_chrome is True
    assert card.refuses_when_unfit is False


def test_the_cross_removes_the_account_from_both_controls(qapp, screen, stored):
    """The half of the sync that is easy to get wrong: a card removed from the column
    while the picker still shows it ticked comes back on the user's next tick."""
    dlg = ProfileDialog(selected_usernames=[ONE, TWO, THREE])

    dlg.accounts_column.cards[1].action_button("Remove").click()

    assert dlg.selected_usernames == [ONE, THREE]
    assert len(dlg.accounts_column.cards) == 2
    # And the picker agrees, because it has no opinion of its own to disagree with: it
    # is built from the membership each time the Add button opens it.
    dlg.open_account_picker()
    assert dlg.account_picker.selection() == [ONE, THREE]


def test_unticking_in_the_picker_removes_the_card(qapp, screen, stored):
    """The other direction, and it must leave the survivors in the order they were in."""
    dlg = ProfileDialog(selected_usernames=[THREE, ONE, TWO])

    _tick(dlg, [ONE, THREE])

    assert dlg.selected_usernames == [THREE, ONE]
    assert _usernames(dlg) == ["Remove 'Three' from this profile", "Remove 'One' from this profile"]


def test_ticking_in_the_picker_appends(qapp, screen, stored):
    dlg = ProfileDialog(selected_usernames=[TWO])

    _tick(dlg, [ONE, TWO])

    assert dlg.selected_usernames == [TWO, ONE]


def test_a_drag_reorders_the_profile_and_renumbers_it(qapp, screen, stored):
    """The column reports previous indices in their new order; the cards are rebuilt so
    the player pills follow, which is the point of numbering them at all."""
    dlg = ProfileDialog(selected_usernames=[ONE, TWO, THREE])

    dlg.accounts_column.reordered.emit([2, 0, 1])

    assert dlg.selected_usernames == [THREE, ONE, TWO]
    assert _usernames(dlg)[0] == "Remove 'Three' from this profile"


def test_a_stale_permutation_is_refused(qapp, screen, stored):
    """Applying one silently reorders a list that is not the one the user dragged."""
    dlg = ProfileDialog(selected_usernames=[ONE, TWO])

    dlg.accounts_column.reordered.emit([0, 1, 2])

    assert dlg.selected_usernames == [ONE, TWO]


def test_an_account_that_no_longer_exists_keeps_its_place(qapp, screen, stored):
    """Dropping it would rewrite the user's profile on open, and they would never see
    what went. It stays on screen with a cross, which is how they take it out on purpose.
    """
    dlg = ProfileDialog(selected_usernames=[ONE, "gone@example.com"])

    assert dlg.selected_usernames == [ONE, "gone@example.com"]
    assert len(dlg.accounts_column.cards) == 2


def test_the_resolution_hint_is_judged_against_this_profiles_account_count(qapp, screen, stored):
    """Adding the fourth account is exactly what costs a profile a resolution tier, so
    the sentence has to move when the membership does."""
    dlg = ProfileDialog(selected_usernames=[ONE])
    one_client = dlg._res_hint.text()

    _tick(dlg, [ONE, TWO, THREE])

    assert "1 client" in one_client
    assert "3 clients" in dlg._res_hint.text()


def _pill_texts(card) -> list[str]:
    from bartlebot.gui.widgets.pills.tag_pill import NeutralPill

    return [pill.text() for pill in card.findChildren(NeutralPill)]


def test_the_position_pills_number_the_players(qapp, screen, stored):
    """The column's heading claims an order; the pills are what let it be read rather
    than counted, and they have to follow a drag or they are worse than nothing."""
    dlg = ProfileDialog(selected_usernames=[ONE, TWO, THREE])

    assert [_pill_texts(card)[0] for card in dlg.accounts_column.cards] == ["P1", "P2", "P3"]

    dlg.accounts_column.reordered.emit([2, 0, 1])

    cards = dlg.accounts_column.cards
    assert [_pill_texts(card)[0] for card in cards] == ["P1", "P2", "P3"]
    assert cards[0].action_button("Remove").toolTip() == "Remove 'Three' from this profile"


def test_an_empty_profile_says_what_it_will_do(qapp, screen, stored):
    """Said rather than refused: a half-built profile is a legitimate thing to close the
    dialog on, and there is no Add card in the column to carry the explanation instead --
    the Add button now lives beside it."""
    from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel

    from bartlebot.gui.settings.profile_editor import EMPTY_PROFILE_HINT

    dlg = ProfileDialog()

    assert dlg.accounts_column.cards == []
    hints = [w.text() for w in dlg.accounts_column.findChildren(ThemedTextLabel)]
    assert EMPTY_PROFILE_HINT in hints


def test_the_add_button_opens_the_picker(qapp, screen, stored):
    """Beside the column rather than at its foot, so it holds still while the list under
    it grows and shrinks."""
    from bartlebot.gui.settings.profile_editor import ADD_BUTTON_TEXT

    dlg = ProfileDialog(selected_usernames=[ONE])

    assert dlg.add_button.text() == ADD_BUTTON_TEXT
    assert dlg.accounts_column.add_card is None
    assert dlg.account_picker is None

    dlg.add_button.click()

    assert dlg.account_picker is not None
    assert dlg.account_picker.selection() == [ONE]


def test_the_dialog_says_whether_it_adds_or_edits(qapp, screen, stored):
    assert ProfileDialog().windowTitle() == "Add Launch Profile"
    assert ProfileDialog(name="Farm Team", selected_usernames=[ONE]).windowTitle() == "Edit Launch Profile"
