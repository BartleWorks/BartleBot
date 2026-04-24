"""Launch profile logic: start Wizard101 instances and auto-login accounts.

Reuses the same WizWalker utilities as launch_wizard101.py, but driven from
the GUI via SettingsManager and CredentialStore.
"""

from __future__ import annotations

import asyncio
import re

from wizwalker.utils import (
    get_all_wizard_handles,
    get_window_title,
    get_wiz_install,
    instance_login,
    patch_open_browser,
    set_window_title,
    start_instance,
)

from bartlebot.credentials import CredentialStore
from bartlebot.settings_manager import SettingsManager

# Grid offsets for up to 4 instances: 2×2 layout matching the embed view.
# Client 1 at anchor, Client 2 right, Client 3 below, Client 4 bottom-right.
_LAUNCH_ANCHOR_X = 50
_LAUNCH_ANCHOR_Y = 50
_LAUNCH_GRID_OFFSETS = [(0, 0), (1, 0), (0, 1), (1, 1)]  # (col, row) multipliers


def _patch_xml_fields(path, updates: dict[str, str]) -> None:
    """Update named element text values in an XML file using regex replacement.

    Replaces only the text content of matched tags, leaving the rest of the
    file (declaration, encoding, whitespace, attribute formatting) byte-for-byte
    identical to what the game wrote — which matters because the game's parser
    is strict about the declaration format.
    """
    text = path.read_text(encoding="utf-8")
    for tag, value in updates.items():
        text = re.sub(rf"(<{tag}\b[^>]*>)[^<]*(</\s*{tag}\s*>)", rf"\g<1>{value}\2", text)
    path.write_text(text, encoding="utf-8")


def _patch_window_config(width: int, height: int, x: int, y: int) -> None:
    """Set the game window resolution and position before launch.

    Wizard101 reads Resolution from preferences.xml and WindowedX/WindowedY
    from config.xml on startup.  Uses the same get_wiz_install() path as
    patch_open_browser().
    """
    bin_dir = get_wiz_install() / "Bin"
    _patch_xml_fields(bin_dir / "preferences.xml", {"Resolution": f"{width}x{height}"})
    _patch_xml_fields(bin_dir / "config.xml", {"WindowedX": str(x), "WindowedY": str(y)})


async def _wait_for_login_screen(handle: int) -> None:
    """Poll until the window title equals 'Wizard101' (login screen is ready)."""
    while get_window_title(handle) != "Wizard101":
        await asyncio.sleep(0.1)


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
    resolution: tuple[int, int] | None = None,
) -> None:
    """Start one Wizard101 instance and auto-login with *username*/*password*.

    Sets the window title to *display_name* once the login screen is ready.
    If *resolution* is provided it is used instead of the global setting — pass the
    crashed client's measured size here so reconnects preserve the original resolution.
    """
    patch_open_browser()
    w, h = resolution if resolution is not None else SettingsManager.instance().game_resolution
    _patch_window_config(w, h, _LAUNCH_ANCHOR_X, _LAUNCH_ANCHOR_Y)
    handle = await _start_and_claim_handle(set(get_all_wizard_handles()))
    await _wait_for_login_screen(handle)
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
    patch_open_browser()
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
            raise RuntimeError(f"No stored password for '{username}'. Please add the account in Settings → Accounts.")
        meta = accounts_meta.get(username, {})
        alias = meta.get("alias") or None
        display_name = CredentialStore.display_name(username, alias)
        accounts.append((username, password, display_name))

    # Start instances one at a time so each handle is unambiguously claimed
    # before the next instance is spawned.  Patch config.xml before each spawn
    # so the window opens at the configured resolution and grid position.
    # A per-profile resolution overrides the global setting.
    handles: list[int] = []
    known = set(get_all_wizard_handles())
    profile_res = profile.get("resolution")
    w, h = tuple(profile_res) if profile_res else SettingsManager.instance().game_resolution
    for i, _ in enumerate(accounts):
        col, row = _LAUNCH_GRID_OFFSETS[i] if i < len(_LAUNCH_GRID_OFFSETS) else (0, 0)
        _patch_window_config(w, h, _LAUNCH_ANCHOR_X + col * w, _LAUNCH_ANCHOR_Y + row * h)
        handles.append(await _start_and_claim_handle(known | set(handles)))

    # Single shared wait for all instances to reach the login screen
    await asyncio.gather(*[_wait_for_login_screen(h) for h in handles])

    for handle, (username, password, display_name) in zip(handles, accounts):
        instance_login(handle, username, password)
        set_window_title(handle, f"[{display_name}] Wizard101")
