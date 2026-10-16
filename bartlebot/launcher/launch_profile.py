"""Launch profile logic: start Wizard101 instances and auto-login accounts.

Reuses the same WizWalker utilities as launch_wizard101.py, but driven from
the GUI via SettingsManager and CredentialStore.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable

from wizwalker.utils import (
    get_all_wizard_handles,
    get_window_title,
    get_wiz_install,
    instance_login,
    patch_open_browser,
    set_window_title,
    start_instance,
)

from bartlebot.clients.credentials import CredentialStore, account_label
from bartlebot.clients.launch_tracker import LaunchStage
from bartlebot.game.game_sizing import ui_size_for
from bartlebot.nodes.timing import (
    LAUNCH_LOGIN_SCREEN_TIMEOUT,
    LAUNCH_SPAWN_TIMEOUT,
    LAUNCH_STAGGER_DELAY,
    jitter,
)
from bartlebot.settings_manager import SettingsManager

logger = logging.getLogger(__name__)

# Advances one launched window from auto-login to in-game, keyed by window title.
# Injected by the caller rather than imported so the launcher stays independent of
# ClientManager, which owns both the "is this client hooked yet" signal the drive waits
# on and the auto-select setting that decides whether it does anything at all.
DriveFn = Callable[[str], Awaitable[bool]]

# Called with (username, stage) each time one account's launch moves on, so a caller
# drawing the wait can say which part of it is running. Injected rather than imported for
# the same reason as DriveFn -- and optional, because nothing about the launch itself
# depends on anyone listening.
ProgressFn = Callable[[str, LaunchStage], None]


def _report(progress: ProgressFn | None, username: str, stage: LaunchStage) -> None:
    """Send one stage report, if anyone is listening, without letting it break the launch.

    A progress callback lands in GUI code, and a launch must not die because a caption
    failed to draw -- the client is already spawned by the time most of these are sent, so
    an exception here would strand a real window with no one logging into it.
    """
    if progress is None:
        return
    try:
        progress(username, stage)
    except Exception:
        logger.exception("Launch progress callback raised for '%s'", account_label(username))


def _report_drive_outcome(progress: ProgressFn | None, username: str, handle: int) -> None:
    """Report a drive that did not get its client in-game, if that is worth reporting.

    A drive returns False for two different things, and only one of them is a failure.
    Either it ran out of time or gave up on a window that is still sitting there -- which
    is FAILED, and the tile says so until the user dismisses it -- or the user closed the
    window while it was still coming up, which is not a failure and must not leave a tile
    behind: a terminal attempt keeps the account unlaunchable until it is dismissed, so
    filing a deliberate close as one is the "cannot relaunch" bug wearing the other hat
    (issue #65).

    Asked of the handle rather than passed down through the drive: the two callers both
    have it, and the question -- is the window still there -- is the same one however the
    drive happened to end.
    """
    if handle in get_all_wizard_handles():
        _report(progress, username, LaunchStage.FAILED)
        return
    logger.info("Launch: the window for '%s' was closed before it finished coming up", account_label(username))


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

    A tag the file does not contain is logged and skipped: these files are written by
    the game, so a missing tag means the game stopped writing it, and inventing one at
    an arbitrary place in the tree is likelier to confuse its parser than to be read.
    """
    text = path.read_text(encoding="utf-8")
    for tag, value in updates.items():
        text, count = re.subn(rf"(<{tag}\b[^>]*>)[^<]*(</\s*{tag}\s*>)", rf"\g<1>{value}\2", text)
        if count == 0:
            logger.warning("No <%s> element in %s — leaving it unset", tag, path.name)
    path.write_text(text, encoding="utf-8")


def _launch_config_updates() -> dict[str, str]:
    """The config.xml settings to force before launch, beyond window placement.

    Quick friend teleport turns a double-click on a friends list row into a teleport.
    Nodes that drive that list double-click rows to open a friend's profile, so with the
    option on the click teleports instead and the node quietly does the wrong thing.
    The game only reads this at startup, which is why it is patched here rather
    than toggled through the in-game options window.
    """
    if not SettingsManager.instance().disable_quick_friend_teleport:
        return {}
    return {"QuickFriendTeleport": "0"}


# The tag holding the game's UI scale. It lives in config.xml *only* -- preferences.xml
# has no such element -- while Resolution goes the other way: it appears in both files
# but only the preferences.xml copy is read, the config.xml one being a mirror the game
# rewrites at launch. Neither split is guessable, which is why both are spelled out here
# and in bartlebot/game/game_sizing.py.
_UI_SIZE_TAG = "UISize"

# The two preferences.xml tags a tiled launch overwrites. Resolution is the size the
# window opens at, and IsFullscreen decides whether the game reads it at all -- a
# fullscreen client ignores the resolution we wrote, comes up the wrong size, and no
# longer fits its tile. Both are the user's own settings, so both are captured and put
# back exactly as <UISize> is.
_RESOLUTION_TAG = "Resolution"
_FULLSCREEN_TAG = "IsFullscreen"


def _prefs_path():
    return get_wiz_install() / "Bin" / "preferences.xml"


def _read_prefs_field(tag: str) -> str | None:
    """The text in *tag* in preferences.xml, or None if it is missing or unreadable.

    None rather than a default, for the same reason as :func:`read_ui_size`: "the game
    did not write one" means there is nothing to restore, and inventing a value would
    hand the user back a setting they never chose.
    """
    try:
        text = _prefs_path().read_text(encoding="utf-8")
    except Exception:
        # Broad: get_wiz_install() raises its own when the install cannot be found, and a
        # preference nobody can read must not be what stops the app starting or quitting.
        logger.warning("Could not read preferences.xml to find <%s>", tag, exc_info=True)
        return None
    match = re.search(rf"<{tag}\b[^>]*>([^<]*)</\s*{tag}\s*>", text)
    return None if match is None else match.group(1).strip()


def _prefs_update(width: int, height: int) -> dict[str, str]:
    """The preferences.xml patch for a launch at *width* x *height*.

    Forces windowed mode alongside the resolution: the game only honors <Resolution>
    when it is not fullscreen, so patching one without the other produces a window at
    the wrong size and a tile that cannot hold it, with nothing saying why.

    Captures the user's own values here, once, before either is overwritten -- and from
    *settings* when a capture already exists rather than from the file, because one
    patched session leaves our values sitting in preferences.xml and re-reading would
    adopt them as the user's preference, losing theirs for good. Same trap as
    :func:`_ui_size_update`.
    """
    settings = SettingsManager.instance()
    if settings.original_resolution is None:
        captured = _read_prefs_field(_RESOLUTION_TAG)
        if captured:
            settings.original_resolution = captured
            settings.save()
    if settings.original_fullscreen is None:
        raw = _read_prefs_field(_FULLSCREEN_TAG)
        try:
            captured_fullscreen = None if raw in (None, "") else int(raw)
        except ValueError:
            captured_fullscreen = None
        if captured_fullscreen is not None:
            settings.original_fullscreen = captured_fullscreen
            settings.save()
    return {_RESOLUTION_TAG: f"{width}x{height}", _FULLSCREEN_TAG: "0"}


def restore_game_prefs() -> None:
    """Put the user's own resolution and fullscreen mode back, and forget the captures.

    The other half of :func:`_prefs_update`. Called from the same place as
    :func:`restore_ui_size` -- on quit, and again on the next start in case that quit
    never came -- and a no-op with nothing captured, so running it twice costs nothing.

    Both captures are kept if the write fails, so the next start tries again rather than
    leaving the user's game windowed at a tile-sized resolution with nothing left that
    remembers what it was.
    """
    settings = SettingsManager.instance()
    updates: dict[str, str] = {}
    if settings.original_resolution is not None:
        updates[_RESOLUTION_TAG] = settings.original_resolution
    if settings.original_fullscreen is not None:
        updates[_FULLSCREEN_TAG] = str(settings.original_fullscreen)
    if not updates:
        return
    try:
        _patch_xml_fields(_prefs_path(), updates)
    except Exception:
        logger.warning(
            "Could not restore the game's resolution and window mode — will retry on the next start", exc_info=True
        )
        return
    settings.original_resolution = None
    settings.original_fullscreen = None
    settings.save()


def read_ui_size() -> int | None:
    """The value in ``<UISize>``, or None if it is missing or unreadable.

    None rather than a default: "the game did not write one" is the case where there is
    no preference to clamp and none to restore, and guessing one would mean handing the
    user back a scale they never chose.
    """
    try:
        text = _config_path().read_text(encoding="utf-8")
    except Exception:
        # Broad: get_wiz_install() raises its own when the install cannot be found, and a
        # UI scale nobody can read must not be what stops the app starting or quitting.
        logger.warning("Could not read config.xml to find the game's UI scale", exc_info=True)
        return None
    match = re.search(rf"<{_UI_SIZE_TAG}\b[^>]*>([^<]*)</\s*{_UI_SIZE_TAG}\s*>", text)
    if match is None:
        return None
    try:
        return int(match.group(1).strip())
    except ValueError:
        return None


def _ui_size_update(width: int, height: int) -> dict[str, str]:
    """The ``<UISize>`` patch for a launch at *width* x *height*, if there is one.

    The HUD is fixed-pixel, so a resolution below the game's own 800x600 floor needs the
    UI scaled down to match or the interface overflows the client area -- the two are one
    coupled setting (see bartlebot/game/game_sizing.py). The clamp only ever goes down, and
    the user's own value is captured here, once, before anything overwrites it.

    Captured from *settings* when a capture already exists rather than from the file: one
    clamped session leaves our value sitting in config.xml, so re-reading it would adopt
    our own clamp as the user's preference and lose theirs for good.
    """
    settings = SettingsManager.instance()
    preferred = settings.original_ui_size
    if preferred is None:
        preferred = read_ui_size()
        if preferred is not None:
            settings.original_ui_size = preferred
            settings.save()
    target = ui_size_for(preferred, (width, height))
    return {} if target is None else {_UI_SIZE_TAG: str(target)}


def restore_ui_size() -> None:
    """Put the user's own UI scale back into config.xml, and forget the capture.

    They play Wizard101 outside BartleBot, so a scale this app clamped for its own tiled
    layout must not be left behind. Called on quit, and again on the next start in case
    that quit never happened -- it is a no-op with nothing captured, so running it twice
    costs nothing.

    The capture is kept if the write fails, so the next start tries again rather than
    leaving the user's game altered with nothing left that remembers what it was.
    """
    settings = SettingsManager.instance()
    original = settings.original_ui_size
    if original is None:
        return
    try:
        _patch_xml_fields(_config_path(), {_UI_SIZE_TAG: str(original)})
    except Exception:
        logger.warning("Could not restore the game's UI scale — will retry on the next start", exc_info=True)
        return
    settings.original_ui_size = None
    settings.save()


def _patch_window_config(width: int, height: int, x: int, y: int) -> None:
    """Set the game window resolution, UI scale and position before launch.

    Wizard101 reads Resolution and IsFullscreen from preferences.xml and
    WindowedX/WindowedY/UISize from config.xml on startup.  Uses the same
    get_wiz_install() path as patch_open_browser().
    """
    bin_dir = get_wiz_install() / "Bin"
    _patch_xml_fields(bin_dir / "preferences.xml", _prefs_update(width, height))
    _patch_xml_fields(
        bin_dir / "config.xml",
        {
            "WindowedX": str(x),
            "WindowedY": str(y),
            **_ui_size_update(width, height),
            **_launch_config_updates(),
        },
    )


async def _wait_for_login_screen(handle: int, timeout: float = LAUNCH_LOGIN_SCREEN_TIMEOUT) -> bool:
    """Poll until the window title equals 'Wizard101' (login screen is ready).

    Returns whether it got there. The wait was unbounded until the launching-client indicator work, which meant a window
    that never reached the login screen -- a crash on startup, a patcher that opened
    instead -- left its launch running forever, and so its tile spinning, for the rest of
    the session. Reported rather than raised because a batch is several of these at once
    and one window failing to come up is not a reason to abandon the ones that did.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while get_window_title(handle) != "Wizard101":
        if loop.time() >= deadline:
            logger.error("Launch: window %d never reached the login screen after %.0fs", handle, timeout)
            return False
        await asyncio.sleep(0.1)
    return True


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


async def _wait_for_n_new_handles(known_handles: set[int], n: int, timeout: float = LAUNCH_SPAWN_TIMEOUT) -> list[int]:
    """Poll until *n* handles beyond *known_handles* exist, then return the new ones.

    Used to claim a whole batch of instances started back-to-back, instead of
    waiting for each one individually — the account each handle ends up with
    doesn't matter, since login credentials are just typed into whichever
    window, so there's no need to correlate a given handle back to the
    start_instance() call that spawned it.

    Raises ``TimeoutError`` if they never appear. Unlike a login screen that does not
    arrive, this one is not attributable to an account — the handles have not been paired
    with anybody yet — so the whole batch fails together, which is also what it means: the
    windows were never started.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        new_handles = set(get_all_wizard_handles()).difference(known_handles)
        if len(new_handles) >= n:
            return list(new_handles)[:n]
        if loop.time() >= deadline:
            raise TimeoutError(f"Only {len(new_handles)} of {n} game windows started within {timeout:.0f}s")
        await asyncio.sleep(0.1)


async def launch_account(
    username: str,
    password: str,
    display_name: str,
    resolution: tuple[int, int] | None = None,
    drive: DriveFn | None = None,
    progress: ProgressFn | None = None,
) -> str:
    """Start one Wizard101 instance and auto-login with *username*/*password*.

    Sets the window title to *display_name* once the login screen is ready, and returns
    that title — it is how every later stage identifies the window.

    If *resolution* is provided it is used instead of the global setting — pass the
    crashed client's measured size here so reconnects preserve the original resolution.

    If *drive* is given, the instance is then advanced through the loading screens and
    character select (see launcher/auto_select.py), and this call does not return until
    the character is in-game or the drive gives up. Whether the drive actually presses
    anything is the drive's own call — it is where the auto-select setting is read.
    Callers that never want a window advanced pass drive=None.

    If *progress* is given it is told which stage this launch is in, as it enters each one.
    """
    patch_open_browser()
    w, h = resolution if resolution is not None else SettingsManager.instance().game_resolution
    _patch_window_config(w, h, _LAUNCH_ANCHOR_X, _LAUNCH_ANCHOR_Y)
    _report(progress, username, LaunchStage.STARTING)
    handle = await _start_and_claim_handle(set(get_all_wizard_handles()))
    if not await _wait_for_login_screen(handle):
        _report(progress, username, LaunchStage.FAILED)
        raise RuntimeError(f"'{display_name}' never reached the login screen.")
    _report(progress, username, LaunchStage.LOGGING_IN)
    instance_login(handle, username, password)
    title = f"[{display_name}] Wizard101"
    set_window_title(handle, title)
    _report(progress, username, LaunchStage.LOADING)
    if drive is not None and not await drive(title):
        # The quiet failure, and the common one: the drive times out rather than raising,
        # so until this was read the window sat wherever it stopped and the launch looked
        # like it had simply finished.
        _report_drive_outcome(progress, username, handle)
    return title


async def launch_accounts(
    accounts: list[tuple[str, str, str]],
    *,
    resolution: tuple[int, int] | None = None,
    drive: DriveFn | None = None,
    progress: ProgressFn | None = None,
) -> list[str]:
    """Start one instance per account, log each in, and advance them all together.

    *accounts* is ``(username, password, display_name)`` in the order the windows should
    be logged in. Returns each account's window title, in the same order — that title is
    how every later stage identifies the window.

    The batch shape is the whole point: instances are spawned back-to-back with a short
    stagger, the login screens are waited for once for all of them, and each drive starts
    as soon as its own login is typed. Doing this one client at a time costs the full
    launch — spawn, two loading screens, character select, load-in — per client, which is
    minutes rather than seconds once more than one window is involved.

    *resolution* is patched into config.xml once for the whole batch, so every account
    here must want the same one — the game reads that file at its own startup and there
    is only one of it. Callers with mixed resolutions launch a batch per resolution.

    *progress* is told which stage each account is in. The batch shape is visible in what
    it can honestly say: every window's login screen is waited for in one shared gather, so
    the whole batch sits at STARTING until the slowest of them is up, and the accounts only
    diverge from ``instance_login`` onward. That is not a reporting gap to paper over — it
    is what is actually happening, and a per-client stage invented for the shared wait
    would claim progress that has not been made.
    """
    if not accounts:
        return []

    patch_open_browser()
    w, h = resolution if resolution is not None else SettingsManager.instance().game_resolution
    _patch_window_config(w, h, _LAUNCH_ANCHOR_X, _LAUNCH_ANCHOR_Y)

    # Fire off every instance with a short stagger between spawns, then claim the whole
    # batch of new handles at once — much faster than waiting for each instance to fully
    # reach the login screen before starting the next, while the stagger still avoids
    # hitting the process spawn / login server in one tight burst.
    known = set(get_all_wizard_handles())
    for username, _password, _display in accounts:
        _report(progress, username, LaunchStage.STARTING)
    for i, _ in enumerate(accounts):
        start_instance()
        if i < len(accounts) - 1:
            await asyncio.sleep(jitter(LAUNCH_STAGGER_DELAY))
    handles = await _wait_for_n_new_handles(known, len(accounts))

    # Single shared wait for all instances to reach the login screen
    reached = await asyncio.gather(*[_wait_for_login_screen(h) for h in handles])

    # Stagger login attempts too — the login server intermittently rejects logins that
    # arrive in too-tight succession.
    #
    # Each instance's drive starts as soon as its own login is typed, rather than after
    # the whole batch: the drives are independent, cost nothing while they wait, and
    # overlapping them with the remaining stagger delays is most of the reason a team
    # launch is faster than doing this by hand.
    titles: list[str] = []
    # The handle rides along with each drive so the outcome can be read against the window
    # it was advancing -- see _report_drive_outcome.
    drives: list[tuple[str, int, asyncio.Task]] = []
    for i, (handle, (username, password, display_name)) in enumerate(zip(handles, accounts)):
        # A window that never reached the login screen is skipped rather than typed into.
        # Its siblings are unaffected — that is the whole reason the wait reports instead
        # of raising — and typing credentials at whatever screen it is actually showing is
        # the one outcome worse than not launching it.
        if not reached[i]:
            _report(progress, username, LaunchStage.FAILED)
            continue
        _report(progress, username, LaunchStage.LOGGING_IN)
        instance_login(handle, username, password)
        title = f"[{display_name}] Wizard101"
        set_window_title(handle, title)
        titles.append(title)
        _report(progress, username, LaunchStage.LOADING)
        if drive is not None:
            drives.append((username, handle, asyncio.create_task(drive(title))))
        if i < len(accounts) - 1:
            await asyncio.sleep(jitter(LAUNCH_STAGGER_DELAY))

    if drives:
        # return_exceptions so one drive blowing up does not cancel the report for the
        # others: every account in this batch has a window by now and each is owed an
        # answer about its own.
        outcomes = await asyncio.gather(*(task for _u, _h, task in drives), return_exceptions=True)
        for (username, handle, _task), outcome in zip(drives, outcomes):
            if isinstance(outcome, BaseException):
                logger.error("Auto-select drive for '%s' raised", account_label(username), exc_info=outcome)
                _report_drive_outcome(progress, username, handle)
            elif not outcome:
                _report_drive_outcome(progress, username, handle)
    return titles


async def launch_profile(
    profile_name: str,
    drive: DriveFn | None = None,
    usernames: list[str] | None = None,
    progress: ProgressFn | None = None,
) -> None:
    """Launch the accounts in the named profile.

    Looks up the profile in SettingsManager and retrieves passwords from
    CredentialStore (Windows Credential Manager).

    The accounts go out as one batch — see ``launch_accounts`` for the shape and why it
    is faster than launching them one at a time.

    *usernames* narrows the launch to those accounts, still in profile order; None launches
    the whole profile. It is how a caller that has already claimed the slots passes on
    which of them it got — an account whose launch is already running is dropped by
    ``ClientManager.begin_launch`` and must not be spawned a second time here.

    Slot reservation is the caller's, not this function's. It was taken through here by a
    ``reserve`` callback until the guard needed a single place both launch routes meet,
    which is one level up: reserving from inside the launch cannot reject a launch
    that has not started yet.

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
    if usernames is not None:
        wanted = set(usernames)
        account_usernames = [u for u in account_usernames if u in wanted]
        if not account_usernames:
            return

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

    # A per-profile resolution overrides the global setting; the whole profile shares it,
    # which is what lets the accounts go out as one batch.
    profile_res = profile.get("resolution")
    resolution = tuple(profile_res) if profile_res else None

    await launch_accounts(accounts, resolution=resolution, drive=drive, progress=progress)


# ---------------------------------------------------------------------------
# config.xml
# ---------------------------------------------------------------------------


def _config_path():
    return get_wiz_install() / "Bin" / "config.xml"
