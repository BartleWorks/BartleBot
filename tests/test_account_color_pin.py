"""Pinning an account's color instead of deriving it from the username.

The mechanism was already there and nothing wrote it: ``pinned_color_key`` has always
read a ``color`` slot off the account record, and ``account_color_keys`` has always
claimed pins first. So what is new is a picker, and what these tests are about is the two
things that make a picker safe rather than the swatches themselves:

* **Automatic is a choice, not the absence of one.** It removes the key. A user who picks
  once has to be able to get a derived color back, or the de-duplication that keeps a
  fleet distinct stops applying to that account forever.
* **The picker says which slots are taken.** Independent choices colliding is the bug
  ``account_accent`` exists to fix; a picker that hid the current assignment would invite
  the user to reproduce it by hand.
"""

from __future__ import annotations

import pytest
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QDialog

from bartlebot.clients.credentials import CredentialStore
from bartlebot.gui.account_accent import ACCOUNT_PALETTE_SIZE, account_color_keys
from bartlebot.gui.settings import accounts_tab
from bartlebot.gui.settings.accounts_tab import _AccountColorPicker, _slots_in_use
from bartlebot.gui.theme import THEME
from bartlebot.settings_manager import SettingsManager

# Synthetic throughout, as CLAUDE.md requires.
ONE = "one@example.com"
TWO = "two@example.com"


@pytest.fixture
def store(monkeypatch):
    """Two accounts, with the keyring faked out and nothing reaching the disk."""
    monkeypatch.setattr(CredentialStore, "store", staticmethod(lambda u, p: None))
    monkeypatch.setattr(SettingsManager, "save", lambda self: None)
    sm = SettingsManager.instance()
    sm.accounts = [{"username": ONE, "alias": "One"}, {"username": TWO, "alias": "Two"}]
    return sm


class _FakeAccountDialog:
    """Stands in for _AccountDialog: exec() is the one thing a test cannot run.

    The constructor signature matches the keyword call in the card actions, which is what
    keeps the stub honest about what the editor is opened on — the color it was handed
    included.
    """

    accepted = True
    result_username = "new@example.com"
    result_alias = ""
    result_password = "hunter2"
    result_color: int | None = None
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
    class Dialog(_FakeAccountDialog):
        pass

    monkeypatch.setattr(accounts_tab, "_AccountDialog", Dialog)
    return Dialog


def _record(username: str) -> dict:
    return next(a for a in SettingsManager.instance().accounts if a["username"] == username)


# ---------------------------------------------------------------------------
# What gets stored
# ---------------------------------------------------------------------------


def test_a_picked_color_is_written_to_the_account(store, account_editor):
    account_editor.result_color = 5

    assert accounts_tab.edit_account(ONE) is True
    assert _record(ONE)["color"] == 5


def test_automatic_removes_the_key_rather_than_storing_a_sentinel(store, account_editor):
    """A ``color`` left behind holding None reads as "pins nothing usable" today, but it
    is a stored value that then has to keep meaning that. Removing it puts the account
    back under the de-duplication with nothing to interpret."""
    _record(ONE)["color"] = 3
    account_editor.result_color = None

    assert accounts_tab.edit_account(ONE) is True
    assert "color" not in _record(ONE)


def test_the_editor_opens_on_the_color_the_account_already_pins(store, account_editor):
    _record(ONE)["color"] = 4

    accounts_tab.edit_account(ONE)
    assert account_editor.seen["color"] == 4


def test_an_account_that_pins_nothing_opens_on_automatic(store, account_editor):
    accounts_tab.edit_account(ONE)
    assert account_editor.seen["color"] is None


def test_a_stored_pin_outside_the_palette_opens_on_automatic(store, account_editor):
    """``pinned_color_key`` ignores it rather than clamping, and the picker has to agree —
    otherwise the dialog shows a selection the reader would never honor."""
    _record(ONE)["color"] = 12

    accounts_tab.edit_account(ONE)
    assert account_editor.seen["color"] is None


def test_a_new_account_stores_a_color_only_when_one_was_picked(store, account_editor):
    account_editor.result_color = None
    assert accounts_tab.add_account() is True
    assert "color" not in _record("new@example.com")

    account_editor.result_username = "other@example.com"
    account_editor.result_color = 2
    assert accounts_tab.add_account() is True
    assert _record("other@example.com")["color"] == 2


# ---------------------------------------------------------------------------
# Which slots the picker says are taken
# ---------------------------------------------------------------------------


def test_the_slots_in_use_are_the_ones_actually_on_screen(store):
    """Resolved through ``account_color_keys``, not read off the records: a slot an
    account merely derived is just as taken on screen as one it pinned."""
    assert _slots_in_use() == {
        slot: [CredentialStore.display_name(u, _record(u)["alias"])]
        for u, slot in account_color_keys(SettingsManager.instance().accounts).items()
    }


def test_the_account_being_edited_is_not_counted_against_itself(store):
    named = [n for names in _slots_in_use(exclude=ONE).values() for n in names]
    assert named == ["Two"]


def test_two_accounts_in_one_slot_are_both_named(store):
    SettingsManager.instance().accounts = [
        {"username": ONE, "alias": "One", "color": 6},
        {"username": TWO, "alias": "Two", "color": 6},
    ]
    assert _slots_in_use()[6] == ["One", "Two"]


def test_the_editor_is_told_what_the_other_accounts_hold(store, account_editor):
    accounts_tab.edit_account(ONE)
    assert account_editor.seen["taken"] == _slots_in_use(exclude=ONE)


# ---------------------------------------------------------------------------
# The picker widget
# ---------------------------------------------------------------------------


def test_the_picker_offers_every_slot(qapp):
    picker = _AccountColorPicker()
    assert set(picker.swatches) == set(range(1, ACCOUNT_PALETTE_SIZE + 1))


def test_the_picker_starts_on_automatic_when_nothing_is_pinned(qapp):
    assert _AccountColorPicker().selected is None


def test_the_picker_starts_on_the_slot_it_was_given(qapp):
    assert _AccountColorPicker(selected=3).selected == 3


def test_a_slot_outside_the_palette_falls_back_to_automatic(qapp):
    assert _AccountColorPicker(selected=ACCOUNT_PALETTE_SIZE + 1).selected is None
    assert _AccountColorPicker(selected=0).selected is None


def test_automatic_is_reachable_again_after_a_color_is_picked(qapp):
    """The whole reason Automatic is a swatch of its own: without it a user who pins once
    can never get a derived color back."""
    picker = _AccountColorPicker(selected=2)
    picker.set_selected(None)
    assert picker.selected is None
    assert picker.automatic_button.isChecked()


def test_clicking_a_swatch_selects_it(qapp):
    picker = _AccountColorPicker()
    picker.swatches[7].click()
    assert picker.selected == 7


def test_one_swatch_is_checked_at_a_time(qapp):
    picker = _AccountColorPicker()
    picker.swatches[7].click()
    picker.swatches[4].click()
    assert [k for k, b in picker.swatches.items() if b.isChecked()] == [4]
    assert not picker.automatic_button.isChecked()


def test_taken_slots_are_marked_and_named(qapp):
    """A marker rather than a disabled swatch: two accounts sharing a color is the
    user's call, but reproducing a collision unknowingly is the bug this module exists
    to prevent."""
    picker = _AccountColorPicker(taken={3: ["One", "Two"]})
    assert picker.swatches[3].taken
    assert "One, Two" in picker.swatches[3].toolTip()
    assert not picker.swatches[4].taken


def test_the_dialog_carries_the_pick_back_out(qapp):
    """The one test that builds the real editor: the properties the card actions read are
    what the picker was set to, not what was passed in."""
    dlg = accounts_tab._AccountDialog(username=ONE, alias="One", color=4, taken={2: ["Two"]})
    assert dlg.color == 4
    dlg.color_picker.set_selected(None)
    assert dlg.color is None


def test_the_marker_is_painted_at_the_center_of_the_swatch(qapp):
    """It used to be a "•" set as the button's text, which draws on the text baseline and
    lands a pixel or two above the middle of a round swatch. Rendered and measured rather
    than asserted on a stylesheet, because being centered is the whole claim."""
    picker = _AccountColorPicker(taken={3: ["One"]})
    image = picker.swatches[3].grab().toImage()
    dot = QColor(THEME.pill_text).rgb()
    lit = [(x, y) for y in range(image.height()) for x in range(image.width()) if image.pixel(x, y) == dot]

    assert lit, "no marker was painted"
    # Measured against the grabbed image rather than SWATCH_SIZE: grab() renders at the
    # screen's device pixel ratio, so on a scaled display the two differ. One device
    # pixel of slack, because an odd ratio cannot land an even-diameter dot dead center.
    for extent, axis in ((image.width(), 0), (image.height(), 1)):
        low = min(p[axis] for p in lit)
        high = max(p[axis] for p in lit)
        assert abs((low + high) - (extent - 1)) <= 1, f"marker off center on axis {axis}"


def test_a_taken_slot_can_still_be_chosen(qapp):
    picker = _AccountColorPicker(taken={3: ["One"]})
    assert picker.swatches[3].isEnabled()
    picker.swatches[3].click()
    assert picker.selected == 3
