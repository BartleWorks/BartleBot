"""The account editor's field order and its alias placeholder."""

from __future__ import annotations

from PyQt6.QtWidgets import QLineEdit

from bartlebot.clients.credentials import CredentialStore
from bartlebot.gui.settings.accounts_tab import _AccountDialog

# Synthetic, as CLAUDE.md requires.
USERNAME = "one@example.com"


def _line_edits_in_tab_order(dlg: _AccountDialog) -> list[QLineEdit]:
    edits = set(dlg.findChildren(QLineEdit))
    order: list[QLineEdit] = []
    widget = dlg.nextInFocusChain()
    while widget is not dlg:
        if widget in edits and widget not in order:
            order.append(widget)
        widget = widget.nextInFocusChain()
    return order


def test_tab_goes_from_username_to_password_then_alias(qapp):
    """A user tabs from username to password by habit, so nothing sits between them."""
    dlg = _AccountDialog()
    username, password, alias = _line_edits_in_tab_order(dlg)
    assert password.echoMode() == QLineEdit.EchoMode.Password
    username.setText(USERNAME)
    alias.setText("Main")
    assert (dlg.username, dlg.alias) == (USERNAME, "Main")


def test_the_alias_placeholder_names_the_censored_username(qapp):
    dlg = _AccountDialog()
    assert dlg.alias_placeholder == "Optional — shown instead of username"
    _line_edits_in_tab_order(dlg)[0].setText(USERNAME)
    assert dlg.alias_placeholder == f"Optional — shown as {CredentialStore.display_name(USERNAME, None)}"
    assert USERNAME not in dlg.alias_placeholder


def test_editing_an_account_names_its_censored_username_at_once(qapp):
    dlg = _AccountDialog(username=USERNAME)
    assert dlg.alias_placeholder == f"Optional — shown as {CredentialStore.display_name(USERNAME, None)}"
