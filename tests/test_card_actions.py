"""The verbs on an account or profile card, and the one API behind them (#194).

Adding, editing and deleting an account or a launch profile used to be possible on
exactly one surface, and only by way of a selection: select a row in the Game tab, look
down, press a button. The launch screen -- where the same two things are drawn far
better -- could edit a profile and nothing else.

So the verbs became module functions in ``settings/accounts_tab.py``, and each surface
promotes as much of the list as suits what that surface is for. The three sections below
are the three things that has to mean:

* the functions do what the buttons did, including the delete cascade and the save;
* a card emits the right name for the right verb, and Delete is *not* on its face;
* the main window catches up with settings afterwards, once per action.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QDialog, QMessageBox

from bartlebot import character_roster
from bartlebot.credentials import CredentialStore
from bartlebot.gui.game_embed.launch_cards import AccountCard, ProfileCard
from bartlebot.gui.settings import accounts_tab
from bartlebot.settings_manager import SettingsManager
from tests.conftest import main_window_stub

# Synthetic throughout, as CLAUDE.md requires: these are account usernames rather than
# wizard names, but the rule is about any identifiable player value.
ONE = "one@example.com"
TWO = "two@example.com"


def _accounts(*usernames: str) -> list[dict]:
    return [{"username": u, "alias": ""} for u in usernames]


@pytest.fixture
def store(monkeypatch):
    """Two accounts and one profile, with the keyring and the roster faked out.

    ``CredentialStore`` talks to the Windows Credential Manager and ``character_roster``
    writes to %APPDATA%. Neither belongs in a test run, and both are things ``delete_account``
    is specifically supposed to reach -- so they are recorders rather than no-ops.
    """
    stored: dict[str, str] = {ONE: "pw1", TWO: "pw2"}
    forgotten: list[str] = []
    monkeypatch.setattr(CredentialStore, "store", staticmethod(lambda u, p: stored.__setitem__(u, p)))
    monkeypatch.setattr(CredentialStore, "delete", staticmethod(lambda u: stored.pop(u, None)))
    monkeypatch.setattr(character_roster, "forget", lambda u: forgotten.append(u))
    # Nothing here should reach the disk; every one of these functions saves, which is
    # the point of test_every_write_is_saved below.
    saves: list[int] = []
    monkeypatch.setattr(SettingsManager, "save", lambda self: saves.append(1))
    sm = SettingsManager.instance()
    sm.accounts = _accounts(ONE, TWO)
    sm.launch_profiles = [
        {"name": "Solo", "accounts": [ONE]},
        {"name": "Duo", "accounts": [ONE, TWO], "resolution": [800, 600]},
    ]
    return type("Store", (), {"passwords": stored, "forgotten": forgotten, "saves": saves})


@pytest.fixture
def confirm(monkeypatch):
    """Answer the delete confirm. Call with True to accept, False to decline.

    Patched rather than driven, because ``QMessageBox.question`` is modal: an unanswered
    one blocks the whole suite until the run is killed.
    """

    def _answer(yes: bool):
        button = QMessageBox.StandardButton.Yes if yes else QMessageBox.StandardButton.No
        monkeypatch.setattr(accounts_tab.QMessageBox, "question", lambda *a, **k: button)

    return _answer


class _FakeAccountDialog:
    """Stands in for _AccountDialog: exec() is the one thing a test cannot run.

    The constructor signature matches the keyword call in ``edit_account``, which is what
    keeps the stub honest about what the editor is opened on.
    """

    accepted = True
    result_alias = ""
    result_password = ""
    result_color = None
    seen: dict = {}

    def __init__(self, username="", alias="", color=None, taken=None, parent=None):
        type(self).seen = {"username": username, "alias": alias, "color": color, "taken": taken, "parent": parent}

    def exec(self):
        return QDialog.DialogCode.Accepted if type(self).accepted else QDialog.DialogCode.Rejected

    @property
    def username(self):
        return type(self).seen["username"]

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


def _stored_accounts() -> list[dict]:
    return SettingsManager.instance().accounts


def _profile_names() -> list[str]:
    return [p["name"] for p in SettingsManager.instance().launch_profiles]


# ---------------------------------------------------------------------------
# The action API
# ---------------------------------------------------------------------------


def test_an_accepted_account_edit_writes_the_alias(store, account_editor):
    account_editor.result_alias = "Main"

    assert accounts_tab.edit_account(ONE) is True
    assert _stored_accounts()[0]["alias"] == "Main"


def test_the_account_editor_opens_on_the_account_that_was_named(store, account_editor):
    SettingsManager.instance().accounts = [{"username": ONE, "alias": "Main"}, {"username": TWO, "alias": ""}]

    accounts_tab.edit_account(ONE)

    assert account_editor.seen["username"] == ONE
    assert account_editor.seen["alias"] == "Main"


def test_a_blank_password_keeps_the_stored_one(store, account_editor):
    """The field opens empty, so "" has to mean "leave it alone" and not "clear it"."""
    account_editor.result_password = ""

    accounts_tab.edit_account(ONE)

    assert store.passwords[ONE] == "pw1"


def test_a_typed_password_replaces_the_stored_one(store, account_editor):
    account_editor.result_password = "new"

    accounts_tab.edit_account(ONE)

    assert store.passwords[ONE] == "new"


def test_a_cancelled_account_edit_changes_nothing(store, account_editor):
    account_editor.accepted = False
    account_editor.result_alias = "Main"
    account_editor.result_password = "new"

    assert accounts_tab.edit_account(ONE) is False
    assert _stored_accounts()[0]["alias"] == ""
    assert store.passwords[ONE] == "pw1"


def test_editing_an_unknown_account_opens_no_editor(store, account_editor):
    """False rather than raising: the card naming it may be a frame behind the settings."""
    account_editor.seen = {}

    assert accounts_tab.edit_account("nobody@example.com") is False
    assert account_editor.seen == {}


def test_deleting_an_account_takes_its_password_and_its_roster(store, confirm):
    confirm(True)

    assert accounts_tab.delete_account(ONE) is True
    assert [a["username"] for a in _stored_accounts()] == [TWO]
    assert ONE not in store.passwords
    assert store.forgotten == [ONE]


def test_deleting_an_account_strips_it_from_every_profile(store, confirm):
    """The cascade. A profile naming a gone account fails at the login screen instead."""
    confirm(True)

    accounts_tab.delete_account(ONE)

    assert [p["accounts"] for p in SettingsManager.instance().launch_profiles] == [[], [TWO]]


def test_a_declined_account_delete_changes_nothing(store, confirm):
    confirm(False)

    assert accounts_tab.delete_account(ONE) is False
    assert [a["username"] for a in _stored_accounts()] == [ONE, TWO]
    assert store.passwords[ONE] == "pw1"
    assert store.forgotten == []


def test_deleting_an_unknown_account_asks_nothing(store, monkeypatch):
    """No confirm at all, which is why question is left unpatched here: it would block."""
    assert accounts_tab.delete_account("nobody@example.com") is False


def test_a_duplicated_profile_lands_directly_after_the_original(store):
    assert accounts_tab.duplicate_launch_profile("Solo") is True
    assert _profile_names() == ["Solo", "Solo (copy)", "Duo"]


def test_a_duplicate_carries_the_whole_record(store):
    accounts_tab.duplicate_launch_profile("Duo")

    copy = SettingsManager.instance().launch_profiles[-1]
    assert copy["accounts"] == [ONE, TWO]
    assert copy["resolution"] == [800, 600]


def test_editing_a_duplicate_does_not_rewrite_the_original(store):
    """A shallow copy would share the accounts list, and one save would write both."""
    accounts_tab.duplicate_launch_profile("Duo")

    profiles = SettingsManager.instance().launch_profiles
    copy = next(p for p in profiles if p["name"] == "Duo (copy)")
    copy["accounts"].append("three@example.com")

    assert next(p for p in profiles if p["name"] == "Duo")["accounts"] == [ONE, TWO]


def test_duplicating_twice_numbers_the_second_copy(store):
    accounts_tab.duplicate_launch_profile("Solo")
    accounts_tab.duplicate_launch_profile("Solo")

    assert _profile_names() == ["Solo", "Solo (copy 2)", "Solo (copy)", "Duo"]


def test_a_free_name_skips_a_number_already_taken():
    assert accounts_tab._free_profile_name("Solo", set()) == "Solo (copy)"
    assert accounts_tab._free_profile_name("Solo", {"Solo (copy)"}) == "Solo (copy 2)"
    assert accounts_tab._free_profile_name("Solo", {"Solo (copy)", "Solo (copy 2)"}) == "Solo (copy 3)"


def test_duplicating_an_unknown_profile_writes_nothing(store):
    assert accounts_tab.duplicate_launch_profile("No Such Profile") is False
    assert _profile_names() == ["Solo", "Duo"]


def test_a_confirmed_profile_delete_removes_it(store, confirm):
    confirm(True)

    assert accounts_tab.delete_launch_profile("Solo") is True
    assert _profile_names() == ["Duo"]


def test_a_declined_profile_delete_changes_nothing(store, confirm):
    confirm(False)

    assert accounts_tab.delete_launch_profile("Solo") is False
    assert _profile_names() == ["Solo", "Duo"]


def test_deleting_an_unknown_profile_asks_nothing(store):
    assert accounts_tab.delete_launch_profile("No Such Profile") is False


def test_every_write_is_saved(store, confirm, account_editor):
    """The bug #195 shipped: a card has no Settings dialog behind it to call save().

    ``SettingsManager.set`` only writes memory, and the one ``save()`` these could reach
    was ``SettingsDialog._on_accept``. So an account added from the launch screen survived
    until the process exited and no further.
    """
    confirm(True)
    account_editor.result_alias = "Main"

    accounts_tab.edit_account(ONE)
    accounts_tab.duplicate_launch_profile("Solo")
    accounts_tab.delete_launch_profile("Duo")
    accounts_tab.delete_account(ONE)

    assert len(store.saves) == 4


def test_a_refused_action_saves_nothing(store, confirm, account_editor):
    confirm(False)
    account_editor.accepted = False

    accounts_tab.edit_account(ONE)
    accounts_tab.delete_account(ONE)
    accounts_tab.delete_launch_profile("Solo")
    accounts_tab.duplicate_launch_profile("No Such Profile")

    assert store.saves == []


# ---------------------------------------------------------------------------
# What each card offers
# ---------------------------------------------------------------------------


def _menu_labels(card) -> list[str]:
    return [label for label, _icon, _handler in card._context_actions()]


def _fire(card, label: str) -> None:
    """Run the context-menu entry named *label*, without putting a QMenu on screen."""
    handler = next(h for lbl, _icon, h in card._context_actions() if lbl == label)
    handler()


def test_an_account_card_offers_edit_and_delete(qapp):
    card = AccountCard({"username": ONE, "alias": ""}, [])

    assert _menu_labels(card) == ["Edit", "Delete"]


def test_a_profile_card_offers_edit_duplicate_and_delete(qapp):
    """Duplicate is the entry that exists nowhere else -- there is no button for it."""
    card = ProfileCard({"name": "Solo", "accounts": [ONE]}, _accounts(ONE))

    assert _menu_labels(card) == ["Edit", "Duplicate", "Delete"]


@pytest.mark.parametrize(
    ("label", "signal_name"),
    [("Edit", "edit_account_requested"), ("Delete", "delete_account_requested")],
)
def test_an_account_card_menu_names_the_account(qapp, label, signal_name):
    card = AccountCard({"username": ONE, "alias": "Main"}, [])
    seen: list[str] = []
    getattr(card, signal_name).connect(seen.append)

    _fire(card, label)

    # The username, not the alias: the alias is what the card reads, and the action API
    # is keyed by the username everywhere -- the credential, the roster, the membership.
    assert seen == [ONE]


@pytest.mark.parametrize(
    ("label", "signal_name"),
    [
        ("Edit", "edit_profile_requested"),
        ("Duplicate", "duplicate_profile_requested"),
        ("Delete", "delete_profile_requested"),
    ],
)
def test_a_profile_card_menu_names_the_profile(qapp, label, signal_name):
    card = ProfileCard({"name": "Solo", "accounts": [ONE]}, _accounts(ONE))
    seen: list[str] = []
    getattr(card, signal_name).connect(seen.append)

    _fire(card, label)

    assert seen == ["Solo"]


# The pencil's own hover and sizing live in tests/test_launch_card_widgets.py, which
# already owned them for ProfileCard and now runs them over both card types.


def test_a_greyed_card_still_offers_its_menu(qapp):
    """Editing the profile is exactly what the greyed card is telling the user to do."""
    card = ProfileCard({"name": "Solo", "accounts": [ONE]}, _accounts(ONE))
    card.set_unavailable("Needs a bigger screen")

    assert _menu_labels(card) == ["Edit", "Duplicate", "Delete"]


def test_delete_is_not_on_a_card_face(qapp):
    """The body starts a game client; a trash icon 22px from it is a costly mis-click.

    The pencil is the only button on either card, so the whole check is that there is one
    of them -- a second QToolButton in the header would be the regression.
    """
    from PyQt6.QtWidgets import QToolButton

    for card in (
        AccountCard({"username": ONE, "alias": ""}, []),
        ProfileCard({"name": "Solo", "accounts": [ONE]}, _accounts(ONE)),
    ):
        assert len(card.findChildren(QToolButton)) == 1


# ---------------------------------------------------------------------------
# What the main window does afterwards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("slot", "function", "args"),
    [
        ("_edit_account", "edit_account", (ONE,)),
        ("_delete_account", "delete_account", (ONE,)),
        ("_edit_launch_profile", "edit_launch_profile", ("Solo",)),
        ("_duplicate_launch_profile", "duplicate_launch_profile", ("Solo",)),
        ("_delete_launch_profile", "delete_launch_profile", ("Solo",)),
    ],
)
def test_a_written_action_refreshes_the_menu_and_the_cards(monkeypatch, slot, function, args):
    from bartlebot.gui.main_window import MainWindow

    monkeypatch.setattr(accounts_tab, function, lambda *a, **k: True)
    calls, stub = main_window_stub()

    getattr(MainWindow, slot)(stub, *args)

    assert calls == ["menu", "minimum", "cards"]


@pytest.mark.parametrize(
    ("slot", "function", "args"),
    [
        ("_edit_account", "edit_account", (ONE,)),
        ("_delete_account", "delete_account", (ONE,)),
        ("_edit_launch_profile", "edit_launch_profile", ("Solo",)),
        ("_duplicate_launch_profile", "duplicate_launch_profile", ("Solo",)),
        ("_delete_launch_profile", "delete_launch_profile", ("Solo",)),
    ],
)
def test_a_refused_action_still_re_judges_the_cards(monkeypatch, slot, function, args):
    """The action was modal, so the screen behind it has cards nothing has re-judged."""
    from bartlebot.gui.main_window import MainWindow

    monkeypatch.setattr(accounts_tab, function, lambda *a, **k: False)
    calls, stub = main_window_stub()

    getattr(MainWindow, slot)(stub, *args)

    assert calls == ["cards"]
