"""Detection of a client's own wizard (character) name.

Wizard101 exposes no character-name field in memory: ClientObject.display_name() and
object_name() resolve through the object *template* ("Player"), CharacterRegistry holds
only quest ids, and the on-disk config files (preferences.xml / config.xml) contain
graphics settings only. The registry value HKCU\\SOFTWARE\\KingsIsle\\Wizard101\\UserName
is the *account* login, not the wizard, so it cannot say which character is logged in.

That leaves two UI-derived routes, both implemented here:

  detect_wizard_name()   opens the character page and reads its title. Works any time,
                         but costs two keypresses and a UI read.
  capture_from_combat()  reads the local player's combat nameplate. Free — no input at
                         all — but only valid during a duel.

Names are needed so one client can find another in its friends list; see
bartlebot.group_manager. Callers are expected to cache the result (ClientManager keys it
by account username, which survives a crash), not to call these on a hot path.
"""

import asyncio
import logging
import re

from wizwalker import Keycode

from bartlebot.nodes.paths import CHARACTER_PAGE_TITLE_WINDOW
from bartlebot.nodes.timing import TRIGGER_TIMEOUT, jitter

logger = logging.getLogger(__name__)

# Window text arrives wrapped in KI colour markup, e.g.
#   "<Color;255,255,255>Wolf Deathblade</Color>"
# Deimos strips this with a fixed [8:-9] slice, which breaks the moment the tag carries
# different attributes. Strip every <...> tag instead and keep what is left.
_MARKUP_TAG = re.compile(r"<[^>]*>")

# Seconds to wait for the character page to render after pressing C. The page is a local
# UI panel with no server round-trip, so this only needs to cover a frame or two.
_CHARACTER_PAGE_SETTLE = 0.5


def _strip_markup(raw: str | None) -> str:
    """Return window text with KI colour markup removed and whitespace collapsed."""
    if not raw:
        return ""
    return " ".join(_MARKUP_TAG.sub("", raw).split())


async def detect_wizard_name(client) -> str | None:
    """Return this client's wizard name by reading the character page, or None.

    Opens the character page (C), reads the TitleScroll window's first child, then closes
    the page again. The page is located by name search rather than a fixed window path:
    its parent chain is not recorded in any reference repo, and a guessed path would fail
    silently on every client.

    Never raises — every failure mode (page did not open, window missing, text empty)
    returns None, because callers treat a missing name as "skip this client" rather than
    as an error worth aborting a bot run for.
    """
    try:
        await client.send_key(Keycode.C, 0.1)
        await asyncio.sleep(jitter(_CHARACTER_PAGE_SETTLE))

        name = await _read_title_scroll(client)
        if name:
            logger.debug("Detected wizard name %r", name)
        else:
            logger.warning("Character page gave no readable wizard name")
        return name
    except Exception:
        logger.warning("Failed to detect wizard name from the character page", exc_info=True)
        return None
    finally:
        # Always attempt to close the page, including on failure — leaving it open would
        # obscure the HUD for every node that runs afterwards.
        try:
            await client.send_key(Keycode.C, 0.1)
        except Exception:
            logger.debug("Could not close the character page", exc_info=True)


async def _read_title_scroll(client) -> str | None:
    """Read and clean the character page title, retrying until the page renders."""
    deadline = asyncio.get_event_loop().time() + TRIGGER_TIMEOUT
    while True:
        windows = await client.root_window.get_windows_with_name(CHARACTER_PAGE_TITLE_WINDOW)
        if windows:
            children = await windows[0].children()
            if children:
                name = _strip_markup(await children[0].maybe_text())
                if name:
                    return name
        if asyncio.get_event_loop().time() >= deadline:
            return None
        await asyncio.sleep(0.25)


# ---------------------------------------------------------------------------
# Cache
#
# Keyed by window handle rather than by account, deliberately. A relaunched client gets a
# new handle, so a recovered client is re-detected automatically — which is what we want:
# it confirms the expected character actually came back, rather than trusting a name
# cached before the crash. The cache exists only to avoid re-opening the character page on
# every call, so losing an entry costs one page open, never correctness.
# ---------------------------------------------------------------------------

_cache: dict[int, str] = {}


def cached_name(client) -> str | None:
    """Return this client's cached wizard name without performing any detection."""
    return _cache.get(_handle(client))


def remember(client, name: str) -> None:
    """Store a wizard name discovered by some other route (e.g. capture_from_combat)."""
    if name:
        _cache[_handle(client)] = name


def clear_cache() -> None:
    """Drop every cached name. Intended for tests and for a full client reset."""
    _cache.clear()


def _handle(client) -> int:
    """Return a stable per-client cache key, falling back to object identity."""
    return getattr(client, "window_handle", None) or id(client)


async def resolve_names(clients: list) -> list[str | None]:
    """Return each client's wizard name, detecting and caching any that are unknown.

    The returned list is positionally aligned with `clients`; an entry is None when that
    client's name could not be determined. Detection runs sequentially rather than
    gathered: it drives the UI with keypresses, and WizWalker's memory reads are
    synchronous on the event loop anyway, so concurrency would buy nothing while making
    several clients fight over the character page at once.
    """
    names: list[str | None] = []
    for client in clients:
        name = cached_name(client)
        if name is None:
            name = await detect_wizard_name(client)
            if name:
                remember(client, name)
        names.append(name)
    return names


async def capture_from_combat(client) -> str | None:
    """Return this client's wizard name from its combat nameplate, or None.

    Free relative to detect_wizard_name — it reads windows the game already has on screen
    and sends no input — but only returns a name while the client is in a duel. Intended
    as an opportunistic cache filler, not a primary route.
    """
    try:
        for member in await client.combat_handler.get_members():
            if await member.is_client():
                name = _strip_markup(await member.name())
                return name or None
    except Exception:
        logger.debug("Could not read the wizard name from combat", exc_info=True)
    return None
