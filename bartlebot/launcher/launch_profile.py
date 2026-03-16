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
from wizwalker.client import Client

from bartlebot.credentials import CredentialStore
from bartlebot.settings_manager import SettingsManager


async def launch_account(
    username: str,
    password: str,
    display_name: str,
) -> None:
    """Start one Wizard101 instance and auto-login with *username*/*password*.

    Sets the window title to *display_name* once the login window appears.
    """
    start_handles = set(get_all_wizard_handles())
    start_instance()

    # Wait for the new window to appear
    while True:
        new_handles = set(get_all_wizard_handles()).difference(start_handles)
        if new_handles:
            break
        await asyncio.sleep(0.1)

    handle = new_handles.pop()
    client = Client(handle)

    await client.hook_handler.activate_root_window_hook()

    # Wait for the login window
    while await client.root_window.get_windows_with_name("LoginWindow") == []:
        await asyncio.sleep(0.1)

    instance_login(handle, username, password)
    set_window_title(handle, f"[{display_name}] Wizard101")

    await client.hook_handler.close()


async def launch_profile(profile_name: str) -> None:
    """Launch all accounts in the named profile concurrently.

    Looks up the profile in SettingsManager and retrieves passwords from
    CredentialStore (Windows Credential Manager).

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
    tasks = []
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
        tasks.append(launch_account(username, password, display_name))

    await asyncio.gather(*tasks)
