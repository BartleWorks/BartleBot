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
from bartlebot.nodes.timing import LAUNCH_STAGGER_DELAY, jitter
from bartlebot.settings_manager import SettingsManager

# Spawn anchor position. Distinct grid offsets per instance aren't needed here:
# every window gets reparented and re-centered by ClientTile (see
# gui/game_embed/client_tile.py) as soon as it's embedded, so the position it
# spawns at is only ever visible for a brief moment.
_LAUNCH_ANCHOR_X = 50
_LAUNCH_ANCHOR_Y = 50


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


async def _wait_for_n_new_handles(known_handles: set[int], n: int) -> list[int]:
    """Poll until *n* handles beyond *known_handles* exist, then return the new ones.

    Used to claim a whole batch of instances started back-to-back, instead of
    waiting for each one individually — the account each handle ends up with
    doesn't matter, since login credentials are just typed into whichever
    window, so there's no need to correlate a given handle back to the
    start_instance() call that spawned it.
    """
    while True:
        new_handles = set(get_all_wizard_handles()).difference(known_handles)
        if len(new_handles) >= n:
            return list(new_handles)[:n]
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

    Starts all instances back-to-back rather than waiting for each one to
    reach the login screen before starting the next, then logs in all
    accounts after a single shared wait for the login screen.

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

    # Patch config.xml once for the whole batch (all instances read the same
    # file at their own startup, so one shared resolution/position is enough —
    # see the _LAUNCH_ANCHOR_X comment above for why per-instance positions
    # aren't needed). A per-profile resolution overrides the global setting.
    profile_res = profile.get("resolution")
    w, h = tuple(profile_res) if profile_res else SettingsManager.instance().game_resolution
    _patch_window_config(w, h, _LAUNCH_ANCHOR_X, _LAUNCH_ANCHOR_Y)

    # Fire off every instance with a short stagger between spawns, then claim
    # the whole batch of new handles at once — much faster than waiting for
    # each instance to fully reach the login screen before starting the next,
    # while the stagger still avoids hitting the process spawn / login server
    # in one tight burst.
    known = set(get_all_wizard_handles())
    for i, _ in enumerate(accounts):
        start_instance()
        if i < len(accounts) - 1:
            await asyncio.sleep(jitter(LAUNCH_STAGGER_DELAY))
    handles = await _wait_for_n_new_handles(known, len(accounts))

    # Single shared wait for all instances to reach the login screen
    await asyncio.gather(*[_wait_for_login_screen(h) for h in handles])

    # Stagger login attempts too — the login server intermittently rejects
    # logins that arrive in too-tight succession.
    for i, (handle, (username, password, display_name)) in enumerate(zip(handles, accounts)):
        instance_login(handle, username, password)
        set_window_title(handle, f"[{display_name}] Wizard101")
        if i < len(accounts) - 1:
            await asyncio.sleep(jitter(LAUNCH_STAGGER_DELAY))
