"""Drive a freshly launched client from auto-login through to in-game.

After ``instance_login`` types the credentials, a Wizard101 instance still has three gates
between it and a usable client: a loading screen that ignores input for ~3 s and then
skips on any key, a second loading screen that skips immediately, and the character-select
screen, where Enter plays the most recently played character. Until those are cleared
hooks cannot activate, so the client never reaches ``ClientManager.clients`` and no bot can
route to it. It *is* on screen throughout — a window is embedded as soon as its title
identifies it, which is why keystrokes here cannot be aimed by ``FindWindowW`` alone; see
``_press_target``.

WHY THIS DOES NOT HAVE TO BE BLIND

It looks like it should be. ``Client.activate_hooks()`` does not return until the *player
struct* is written, which only happens once a character is in a game zone, so the obvious
reading is that nothing is knowable beforehand — and crash recovery used to spam Enter on
a timer on exactly that assumption.

But ``activate_all_hooks`` installs every hook *before* it starts waiting, and only the
player struct needs a character. ``tools/probe_char_select.py`` measured the rest against a
never-logged-in client: the root window becomes readable **1.8 s** after attach, well
before the first loading screen ends, and ``WorldView/mainWindow`` — with its ``btnPlay``
child — appears exactly when the character-select screen does and disappears when it is
dismissed. So the screen can be identified rather than guessed at.

That is what this module does:

    loading screens   press a harmless key on a timer (nothing to read yet)
    character select  positively identified, then Enter
    load-in           press nothing at all, just wait for the hooks

Enter therefore only ever lands on a screen we have confirmed is character select.

That positive identification is also what makes the two halves separable. With the
``auto_select_character`` setting off the loading screens are *still* skipped — they carry
no decision, and making the user watch them and press a key twice is nobody's preference —
and the drive simply stops when ``at_character_select`` goes true, having pressed nothing
that could pick a character. ``drive_to_character_select`` is that half; it has no blind
fallback, because blind means Enter. That
matters beyond tidiness: blind Enter opened the chat box on arrival (recovery carried a
whole ESC dance to close it again) and would confirm an unexpected modal sight unseen — a
login queue, a ToS update, an expired subscription. Neither is possible now.

CRITICAL: THE CLIENT IS BORROWED, NEVER CONSTRUCTED

The driver reads the tree through the ``Client`` that ``ClientManager`` already built for
this window, and must never make its own. Two ``Client`` objects over one game process
mean two ``_prepare_autobot`` calls, and the second zeroes the scratch region the first
one's hooks jump into — corrupting live hooks in the game process. ``ClientManager``
registers a ``Client`` and installs its hooks the moment the window appears (see
``_start_activation``), so by the time the tree is readable there is always one to borrow.

If the tree never becomes readable, the driver falls back to the old blind behavior
rather than failing the launch.

Keystrokes go out via ``PostMessageW``, not ``SendMessageW``: it is fire-and-forget, so it
neither blocks the event loop on the target's message pump (see the note in
``AppController._close_client``) nor requires the window to be focused. That is what lets a
whole launch profile advance in parallel without stealing focus from whatever the user is
doing.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
from collections.abc import Callable

from bartlebot.launcher.remember_realm import turn_on_remember_realm
from bartlebot.nodes.node_helpers import click_by_path, is_enabled_by_path, is_visible_by_path, poll_until
from bartlebot.nodes.paths import BACKPACK_CLOSE_BUTTON_PATH, CHAR_SELECT_PATH, CROWNS_SHOP_PATH
from bartlebot.nodes.timing import (
    AUTO_SELECT_CHAR_SELECT_TIMEOUT,
    AUTO_SELECT_CLEANUP_INTERVAL,
    AUTO_SELECT_FIRST_SCREEN_DELAY,
    AUTO_SELECT_KEY_INTERVAL,
    AUTO_SELECT_MAX_ENTER,
    AUTO_SELECT_TIMEOUT,
    AUTO_SELECT_TREE_TIMEOUT,
    TRIGGER_TIMEOUT,
)

logger = logging.getLogger(__name__)

_VK_RETURN = 0x0D
_VK_ESCAPE = 0x1B
_VK_SPACE = 0x20
_WM_KEYDOWN = 0x0100
_WM_KEYUP = 0x0101

# Gap between a key going down and coming up. Long enough for the game to register a
# discrete press, short enough not to read as a held key.
_KEY_HOLD = 0.05

# How many ESC presses to spend on each stage of the post-login cleanup before giving up.
# The cleanup is best-effort — a leftover window is cosmetic, not a failure.
_CLEANUP_ATTEMPTS = 5


async def _press(hwnd: int, vk: int) -> None:
    """Send one discrete keypress to a window, focused or not."""
    ctypes.windll.user32.PostMessageW(hwnd, _WM_KEYDOWN, vk, 0)
    await asyncio.sleep(_KEY_HOLD)
    ctypes.windll.user32.PostMessageW(hwnd, _WM_KEYUP, vk, 0)


def _window_exists(hwnd: int) -> bool:
    """Whether a handle still names a live window.

    The drive's other exit conditions are all "not yet": hooked, at character select, or
    out of time. A window the user closed mid-launch matches none of them, so without this
    the drive kept pressing keys into a dead handle until its full timeout -- and the
    launch it belongs to stayed in flight for that whole time, which is exactly how long
    the account stayed unlaunchable (issue #65).

    ``IsWindow`` rather than a title lookup: an embedded window is a child and
    ``FindWindowW`` cannot see it, so a title miss means nothing on its own.
    """
    return bool(ctypes.windll.user32.IsWindow(hwnd))


def _find_window(title: str) -> int:
    """The window handle for an exact title, or 0. Re-resolved every press.

    The handle is looked up each time rather than captured once because the launcher
    renames the window after login, and a client that crashes and relaunches mid-drive
    comes back under a different handle with the same title.
    """
    return ctypes.windll.user32.FindWindowW(None, title)


def _press_target(title: str, get_client: Callable[[], object | None]) -> int:
    """The window to send keystrokes to, or 0 if it cannot be found.

    ``FindWindowW`` searches **top-level** windows only, and a window embedded into the
    game view is a child of a Qt widget — so from the moment it gets a tile, which is now
    as soon as its title identifies it, the title lookup returns nothing. The borrowed
    client's handle is that same window and is unaffected by embedding.

    The title lookup stays first because it is what re-resolves across a crash and
    relaunch mid-drive; the client is the fallback, not the other way round.
    """
    hwnd = _find_window(title)
    if hwnd:
        return hwnd
    client = get_client()
    return getattr(client, "window_handle", 0) or 0


async def _try_press(title: str, vk: int, get_client: Callable[[], object | None] = lambda: None) -> None:
    """Press a key on the named window, tolerating a window that is not there yet."""
    try:
        hwnd = _press_target(title, get_client)
        if hwnd:
            await _press(hwnd, vk)
    except Exception:
        logger.warning("Auto-select: keypress to '%s' failed, will retry", title, exc_info=True)


async def _tree_is_readable(client) -> bool:
    """Whether the client's root window has been written yet.

    ClientManager installs the root-window hook as soon as it sees the window, but the
    game does not write the address until it builds its first UI root, so this is False
    for roughly the first two seconds of a launch.
    """
    try:
        return bool(await client.hook_handler.read_current_root_window_base())
    except Exception:
        return False


async def press_character_select_play(hwnd: int) -> None:
    """Take the highlighted character on a screen already confirmed to be character select.

    Public so the relog node presses Play exactly the way a launch does. Enter rather than
    clicking ``btnPlay``: the button's own visibility flickers while the screen settles,
    which is the same reason ``at_character_select`` reads the panel instead.
    """
    await _press(hwnd, _VK_RETURN)


async def at_character_select(client) -> bool:
    """Whether the character-select screen is up.

    Reads ``WorldView/mainWindow`` — the panel's own path, not a control inside it, per the
    warning on ``is_visible_by_path``. The probe timeline shows that window absent through
    both loading screens, present and visible for the whole of character select, and gone
    again once Play is taken, which makes it a clean binary signal. Its ``btnPlay`` child is
    not usable for this: the button's own visibility flickers while the screen settles.
    """
    try:
        return await is_visible_by_path(client, CHAR_SELECT_PATH)
    except Exception:
        return False


async def _wait_for_readable_tree(
    get_client: Callable[[], object | None],
    is_hooked: Callable[[], bool],
    deadline: float,
):
    """Poll for a borrowable client whose UI tree reads. None if it never arrives."""
    loop = asyncio.get_event_loop()
    while loop.time() < deadline:
        if is_hooked():
            return None
        client = get_client()
        if client is not None and await _tree_is_readable(client):
            return client
        await asyncio.sleep(AUTO_SELECT_KEY_INTERVAL)
    return None


async def auto_select_character(
    expected_title: str,
    is_hooked: Callable[[], bool],
    get_client: Callable[[], object | None] = lambda: None,
    *,
    timeout: float = AUTO_SELECT_TIMEOUT,
) -> bool:
    """Advance the window titled *expected_title* until its character is in-game.

    *is_hooked* is polled for completion — pass ``ClientManager.has_client_titled`` bound
    to the same title. A client only lands in that list once ``activate_hooks()`` succeeds,
    which requires a character to be in a game zone, so it is a true in-game signal rather
    than a proxy for one.

    *get_client* returns the ``Client`` ClientManager built for this window, hooked or not,
    so the driver can read its UI tree. Never build one here — see the module docstring.
    Omitting it drives the client blind.

    Returns True once the client is in-game, False on timeout. Never raises: callers
    decide what a failure means (recovery stops the bot; a normal launch just leaves the
    window for the user).
    """
    return await _drive(expected_title, is_hooked, get_client, timeout=timeout, select_character=True)


async def drive_to_character_select(
    expected_title: str,
    is_hooked: Callable[[], bool],
    get_client: Callable[[], object | None] = lambda: None,
    *,
    timeout: float = AUTO_SELECT_CHAR_SELECT_TIMEOUT,
) -> bool:
    """Clear the post-login loading screens and stop, leaving the user at character select.

    The auto-select-is-off path. Nobody wants to sit through two loading screens whose only
    input is "press a key", so those are still skipped — but the character is the user's
    choice, so Enter is never pressed and the drive ends the moment
    ``at_character_select`` says the screen is up.

    Returns True if the screen was reached (or the client got in-game by itself, which is
    what a window that was already past character select looks like), False on timeout.
    There is no blind fallback here: driving blind means pressing Enter, which is precisely
    what the user opted out of.
    """
    return await _drive(expected_title, is_hooked, get_client, timeout=timeout, select_character=False)


async def _drive(
    expected_title: str,
    is_hooked: Callable[[], bool],
    get_client: Callable[[], object | None],
    *,
    timeout: float,
    select_character: bool,
) -> bool:
    """Shared body of the two drives: wait out the first screen, then read or guess."""
    if is_hooked():
        return True

    logger.info("Auto-select: driving '%s' to character select", expected_title)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout

    # The first loading screen ignores input until roughly this point, so there is nothing
    # to gain from pressing earlier — but poll while waiting, in case the client was
    # already past it (a recovery relaunch that beat us to it, say).
    waited = 0.0
    while waited < AUTO_SELECT_FIRST_SCREEN_DELAY:
        if is_hooked():
            return True
        await asyncio.sleep(AUTO_SELECT_KEY_INTERVAL)
        waited += AUTO_SELECT_KEY_INTERVAL

    tree_deadline = min(deadline, loop.time() + AUTO_SELECT_TREE_TIMEOUT)
    client = await _wait_for_readable_tree(get_client, is_hooked, tree_deadline)
    if is_hooked():
        return True
    if client is None:
        if not select_character:
            # Blind means Enter, and Enter picks a character. Leave the window alone.
            logger.warning(
                "Auto-select: could not read the UI tree for '%s' — leaving it for the user",
                expected_title,
            )
            return False
        logger.warning(
            "Auto-select: could not read the UI tree for '%s' — falling back to blind input",
            expected_title,
        )
        return await _drive_blind(expected_title, is_hooked, deadline, get_client)
    return await _drive_by_sight(expected_title, is_hooked, client, deadline, select_character=select_character)


async def _drive_by_sight(
    expected_title: str,
    is_hooked: Callable[[], bool],
    client,
    deadline: float,
    *,
    select_character: bool = True,
) -> bool:
    """Skip the loading screens, then press Enter on a confirmed character-select screen.

    With *select_character* False the loading screens are still skipped, but the drive
    stops at character select and presses nothing — the character is the user's to pick.
    """
    loop = asyncio.get_event_loop()
    enters = 0
    seen_character_select = False

    while loop.time() < deadline:
        if is_hooked():
            logger.info("Auto-select: '%s' is in-game", expected_title)
            return True
        if not _window_exists(client.window_handle):
            logger.info("Auto-select: the window for '%s' was closed — abandoning the drive", expected_title)
            return False

        if await at_character_select(client):
            if not select_character:
                logger.info("Auto-select: '%s' is at character select — leaving it for the user", expected_title)
                return True
            seen_character_select = True
            if enters < AUTO_SELECT_MAX_ENTER:
                await _try_press(expected_title, _VK_RETURN, lambda: client)
                enters += 1
            elif enters == AUTO_SELECT_MAX_ENTER:
                logger.warning(
                    "Auto-select: '%s' is still at character select after %d attempts — something may be blocking Play",
                    expected_title,
                    enters,
                )
                enters += 1  # log once, then keep waiting
        elif not seen_character_select:
            # A loading screen, or something we do not recognize. Space rather than Enter:
            # both dismiss a loading screen, but Space is far less likely to confirm a
            # dialog we have not identified.
            await _try_press(expected_title, _VK_SPACE, lambda: client)
        # Once character select has been taken, press nothing — the client is loading in
        # and every further keystroke would land in the game world.

        await asyncio.sleep(AUTO_SELECT_KEY_INTERVAL)

    if is_hooked():
        return True
    if not select_character:
        logger.error("Auto-select: timed out waiting for '%s' to reach character select", expected_title)
        return False
    logger.error("Auto-select: timed out waiting for '%s' to load in", expected_title)
    return False


async def _drive_blind(
    expected_title: str,
    is_hooked: Callable[[], bool],
    deadline: float,
    get_client: Callable[[], object | None] = lambda: None,
) -> bool:
    """Fallback for when the UI tree never reads: press Enter on a timer and hope.

    This is what the whole drive used to be. It can open the chat box on arrival and can
    confirm an unexpected modal, so it runs only when reading the screen has failed.
    """
    loop = asyncio.get_event_loop()
    # The handle is not known up front here -- the tree never read, so there may be no
    # client to borrow one from -- so it is learned from the first press that lands and
    # only then watched. Until one has been seen, a zero press target means "not found
    # yet", which is the ordinary state at the top of a blind drive.
    seen_hwnd = 0
    while loop.time() < deadline:
        if is_hooked():
            logger.info("Auto-select: '%s' is in-game", expected_title)
            return True
        if seen_hwnd and not _window_exists(seen_hwnd):
            logger.info("Auto-select: the window for '%s' was closed — abandoning the drive", expected_title)
            return False
        seen_hwnd = _press_target(expected_title, get_client) or seen_hwnd
        await _try_press(expected_title, _VK_RETURN, get_client)
        await asyncio.sleep(AUTO_SELECT_KEY_INTERVAL)

    if is_hooked():
        return True
    logger.error("Auto-select: timed out waiting for '%s' to load in", expected_title)
    return False


async def wait_for_client_in(
    expected_title: str,
    is_hooked: Callable[[], bool],
    *,
    timeout: float = AUTO_SELECT_TIMEOUT,
) -> bool:
    """Wait for a client to reach in-game without touching it.

    The auto-select-is-off counterpart to auto_select_character: recovery still has to
    know when the user has finished logging the relaunched client back in, it just must
    not press anything to get there.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if is_hooked():
            return True
        await asyncio.sleep(AUTO_SELECT_KEY_INTERVAL)
    return is_hooked()


async def clean_up_after_auto_select(client, *, remember_realm: bool = False) -> None:
    """Dismiss anything that opened by itself during login.

    The crowns shop opens on its own for some accounts. The chat box is also handled, for
    the blind fallback path: there, Enter lands in the game world on arrival and opens it,
    and ESC on an open chat box closes it *and* opens the settings window — so the only
    reliable sequence is ESC until settings appears, then ESC until it goes away again.
    Both loops no-op when nothing is open, which is the normal case now that Enter only
    ever lands on the character-select screen.

    *remember_realm* turns the game's Remember Realm option on while that settings window
    is up, between the two ESC loops — the one moment per launch it is already open, so
    the player is not seen closing settings and reopening it a moment later. The caller
    passes it when a favorite realm is set, since that is the only time the option
    changes anything a bot relies on.

    Best-effort throughout: a leftover window is cosmetic, and nothing here should be able
    to fail a launch that otherwise succeeded.
    """
    hwnd = client.window_handle

    async def _esc() -> None:
        await _press(hwnd, _VK_ESCAPE)

    async def _settings_open() -> bool:
        return await is_enabled_by_path(client, BACKPACK_CLOSE_BUTTON_PATH)

    async def _settings_closed() -> bool:
        return not await _settings_open()

    # Each ESC is followed by a wait for the window to actually appear or go, capped at
    # AUTO_SELECT_CLEANUP_INTERVAL. A fixed sleep of that length used to sit here, which
    # left the player parked in front of an open settings window for two seconds before
    # anything touched it; the cap is still what stops a second ESC landing mid-animation.
    try:
        for _ in range(_CLEANUP_ATTEMPTS):
            if await _settings_open():
                break
            await _esc()
            await poll_until(_settings_open, timeout=AUTO_SELECT_CLEANUP_INTERVAL)
        if remember_realm and await _settings_open():
            await turn_on_remember_realm(client)
        for _ in range(_CLEANUP_ATTEMPTS):
            if await _settings_closed():
                break
            await _esc()
            await poll_until(_settings_closed, timeout=AUTO_SELECT_CLEANUP_INTERVAL)

        for _ in range(_CLEANUP_ATTEMPTS):
            if not await is_enabled_by_path(client, CROWNS_SHOP_PATH):
                break
            await _esc()
            if not await poll_until(
                lambda: is_enabled_by_path(client, BACKPACK_CLOSE_BUTTON_PATH),
                timeout=TRIGGER_TIMEOUT,
            ):
                continue
            await click_by_path(client, BACKPACK_CLOSE_BUTTON_PATH)
            await poll_until(
                lambda: not is_enabled_by_path(client, BACKPACK_CLOSE_BUTTON_PATH),
                timeout=TRIGGER_TIMEOUT,
            )
    except Exception:
        logger.warning("Auto-select: post-login cleanup failed", exc_info=True)


async def drive_client_in(
    expected_title: str,
    is_hooked: Callable[[], bool],
    get_client: Callable[[], object | None],
    *,
    timeout: float = AUTO_SELECT_TIMEOUT,
    remember_realm: bool = False,
) -> bool:
    """auto_select_character followed by the cleanup pass, for the common case.

    *get_client* returns the client for *expected_title* — used both to read the screen
    while driving and to tidy up afterwards. Returns whatever ``auto_select_character``
    returned; cleanup failures do not change it.

    *remember_realm* is handed to the cleanup, which turns the game's Remember Realm
    option on while it has the settings window open — passed when a favorite realm is
    set, since that is the only time the option changes anything a bot relies on.
    """
    reached = await auto_select_character(expected_title, is_hooked, get_client, timeout=timeout)
    if not reached:
        return False
    client = get_client()
    if client is not None:
        await clean_up_after_auto_select(client, remember_realm=remember_realm)
    return True
