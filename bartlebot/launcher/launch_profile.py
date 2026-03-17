"""Launch profile logic: start Wizard101 instances and auto-login accounts.

Reuses the same WizWalker utilities as launch_wizard101.py, but driven from
the GUI via SettingsManager and CredentialStore.
"""

from __future__ import annotations

import asyncio

from wizwalker.utils import (
    get_all_wizard_handles,
    instance_login,
    set_window_title,
    start_instance,
)

from bartlebot.credentials import CredentialStore
from bartlebot.settings_manager import SettingsManager


async def _start_and_claim_handle(known_handles: set[int]) -> int:
    """Start one Wizard101 instance and return its window handle.

    *known_handles* is the set of handles that already exist (including any
    previously claimed by the current launch batch) so the poll loop can
    unambiguously identify the newly created window.
    """
    start_instance()
    while True:
        new_handles = set(get_all_wizard_handles()).difference(known_handles)
        if new_handles:
            return new_handles.pop()
        await asyncio.sleep(0.1)


async def launch_account(
    username: str,
    password: str,
    display_name: str,
) -> None:
    """Start one Wizard101 instance and auto-login with *username*/*password*.

    Sets the window title to *display_name* once the login screen is ready.
    """
    handle = await _start_and_claim_handle(set(get_all_wizard_handles()))
    await asyncio.sleep(5)
    instance_login(handle, username, password)
    set_window_title(handle, f"[{display_name}] Wizard101")


async def launch_profile(profile_name: str) -> None:
    """Launch all accounts in the named profile.

    Looks up the profile in SettingsManager and retrieves passwords from
    CredentialStore (Windows Credential Manager).

    Starts instances sequentially so each handle can be unambiguously claimed,
    then logs in all accounts after a single shared wait for the login screen.

    Raises ``ValueError`` if the profile is not found or has no accounts.
    Raises ``RuntimeError`` if a password is not stored for an account.
    """
    settings = SettingsManager.instance()
    profiles = settings.launch_profiles
    profile = next((p for p in profiles if p.get("name") == profile_name), None)
    if profile is None:
        raise ValueError(f"Launch profile '{profile_name}' not found")

    account_usernames: list[str] = profile.get("accounts", [])
    if not account_usernames:
        raise ValueError(f"Launch profile '{profile_name}' has no accounts")

    # Build (username, password, display_name) tuples
    accounts_meta = {a["username"]: a for a in settings.accounts}
    accounts = []
    for username in account_usernames:
        password = CredentialStore.retrieve(username)
        if password is None:
            raise RuntimeError(
                f"No stored password for '{username}'. "
                "Please add the account in Settings → Accounts."
            )
        meta = accounts_meta.get(username, {})
        alias = meta.get("alias") or None
        display_name = CredentialStore.display_name(username, alias)
        accounts.append((username, password, display_name))

    # Start instances one at a time so each handle is unambiguously claimed
    # before the next instance is spawned.
    handles: list[int] = []
    known = set(get_all_wizard_handles())
    for _ in accounts:
        handles.append(await _start_and_claim_handle(known | set(handles)))

    # Single shared wait for all instances to reach the login screen
    await asyncio.sleep(5)

    for handle, (username, password, display_name) in zip(handles, accounts):
        instance_login(handle, username, password)
        set_window_title(handle, f"[{display_name}] Wizard101")
