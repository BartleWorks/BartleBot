"""CredentialStore: secure credential management via Windows Credential Manager.

Passwords are stored using the ``keyring`` library (Windows Credential Manager
backend on Win32). Usernames and optional aliases are stored in settings.json
via SettingsManager — passwords are never written to disk.
"""

from __future__ import annotations

import keyring

_SERVICE = "BartleBot"


def _censor(username: str) -> str:
    """Return a censored version of a username for stream-safe display."""
    if len(username) <= 1:
        return username + "***"
    return username[0] + "***" + username[-1]


class CredentialStore:
    """Thin wrapper around keyring for BartleBot account credentials."""

    @staticmethod
    def store(username: str, password: str) -> None:
        """Save *password* for *username* to Windows Credential Manager."""
        keyring.set_password(_SERVICE, username, password)

    @staticmethod
    def retrieve(username: str) -> str | None:
        """Fetch the stored password for *username*, or None if not found."""
        return keyring.get_password(_SERVICE, username)

    @staticmethod
    def delete(username: str) -> None:
        """Remove the stored credential for *username* (no-op if absent)."""
        try:
            keyring.delete_password(_SERVICE, username)
        except keyring.errors.PasswordDeleteError:
            pass

    @staticmethod
    def display_name(username: str, alias: str | None) -> str:
        """Return the name to show in the GUI for this account.

        If *alias* is set, return the alias. Otherwise return a censored
        version of the username (first char + *** + last char).
        """
        if alias:
            return alias
        return _censor(username)
