"""Detection of a client's own wizard (character) name.

No *string* field in memory holds the name: ClientObject.display_name() and object_name()
resolve through the object *template* ("Player"), CharacterRegistry holds only quest ids,
and the on-disk config files contain graphics settings. The registry value
HKCU\\SOFTWARE\\KingsIsle\\Wizard101\\UserName is the *account* login, not the wizard.

The name is nonetheless in memory, as three indices into the game's own name tables packed
into WizPlayerNameBehavior.m_nameKeys — see bartlebot.name_tables, which decodes it. Wiring
that in as the primary route is still to come; today the two UI routes below do the work:

  detect_wizard_name()   opens the character page and reads its title. Works any time,
                         but costs two keypresses and a UI read.
  capture_from_combat()  reads the local player's combat nameplate. Free — no input at
                         all — but only valid during a duel.

Names are needed so one client can find another in its friends list; see
bartlebot.group_manager. Callers use the cache here rather than calling detection on a hot
path.
"""

import asyncio
import logging
import re

from wizwalker import Keycode
from wizwalker.memory.memory_object import Primitive

from bartlebot.name_tables import decode_name_keys
from bartlebot.nodes.paths import CHARACTER_PAGE_TITLE_WINDOW
from bartlebot.nodes.timing import CHARACTER_PAGE_SETTLE, TRIGGER_TIMEOUT, jitter

logger = logging.getLogger(__name__)

# Window text arrives wrapped in KI colour markup, e.g.
#   "<Color;255,255,255>Wolf Deathblade</Color>"
# Deimos strips this with a fixed [8:-9] slice, which breaks the moment the tag carries
# different attributes. Strip every <...> tag instead and keep what is left.
_MARKUP_TAG = re.compile(r"<[^>]*>")


def strip_markup(raw: str | None) -> str:
    """Return window text with KI colour markup removed and whitespace collapsed.

    Public because every wizard name read out of the UI needs the same treatment wherever
    it comes from — the character page here, the combat nameplate below, and the group
    roster in bartlebot.group_manager.
    """
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
        await asyncio.sleep(jitter(CHARACTER_PAGE_SETTLE))

        name = await _read_title_scroll(client)
        if name:
            # Not the name itself — see the note in read_name_from_memory (#156).
            logger.debug("Detected a wizard name from the character page")
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
                name = strip_markup(await children[0].maybe_text())
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
#
# Each entry also carries the character id it was read from, because a window handle alone
# cannot tell that the user switched characters in place — the handle survives a switch and
# the cached name would go on naming the previous wizard indefinitely. refresh_identity()
# drops an entry whose character id has moved.
# ---------------------------------------------------------------------------

# ClientObject.m_characterId. Reliable identity: it holds steady through zone changes,
# reads 0 at character select, and takes the new value on switch.
CHARACTER_ID_OFFSET = 0x1C0

# Character id meaning "no character right now" — character select, or a client that is not
# far enough through login to answer. Never treat it as a switch: a loading screen would
# then throw away a perfectly good name once a second.
NO_CHARACTER = 0

_cache: dict[int, tuple[int, str]] = {}


def cached_name(client) -> str | None:
    """Return this client's cached wizard name without performing any detection."""
    entry = _cache.get(_handle(client))
    return entry[1] if entry else None


def cached_character_id(client) -> int:
    """Return the character id the cached name was read from, or NO_CHARACTER."""
    entry = _cache.get(_handle(client))
    return entry[0] if entry else NO_CHARACTER


def remember(client, name: str, character_id: int = NO_CHARACTER) -> None:
    """Store a wizard name discovered by some other route (e.g. capture_from_combat).

    ``character_id`` ties the name to the character it belongs to. Omitting it still caches
    the name — callers that have no id should not be forced to invent one — and the next
    refresh_identity() adopts whatever id is live rather than treating the gap as a switch.
    """
    if name:
        _cache[_handle(client)] = (character_id, name)


def forget(client) -> None:
    """Drop this client's cached name."""
    _cache.pop(_handle(client), None)


def clear_cache() -> None:
    """Drop every cached name. Intended for tests and for a full client reset."""
    _cache.clear()
    _memory_misses.clear()


def _handle(client) -> int:
    """Return a stable per-client cache key, falling back to object identity."""
    return getattr(client, "window_handle", None) or id(client)


async def read_character_id(client) -> int:
    """Return this client's character id, or NO_CHARACTER if it cannot be read.

    One uint64 read, cheap enough to poll every client-loop cycle. Failures are common and
    uninteresting — a client mid-teardown or not yet hooked — so they answer NO_CHARACTER
    rather than raising.
    """
    try:
        return await client.client_object.read_value_from_offset(CHARACTER_ID_OFFSET, Primitive.uint64)
    except Exception:
        logger.debug("Could not read the character id", exc_info=True)
        return NO_CHARACTER


async def refresh_identity(client) -> int:
    """Drop this client's cached name if it is now on a different character.

    Returns the live character id (NO_CHARACTER when unreadable). Cheap by design: the id
    is one read, and the expensive name detection only happens afterwards, for clients that
    no longer have a valid cached name.
    """
    character_id = await read_character_id(client)
    if character_id == NO_CHARACTER:
        # At character select or mid-login. Keep the cached name: the user is most likely
        # coming back to the same character, and if not, the next read catches it.
        return NO_CHARACTER

    entry = _cache.get(_handle(client))
    if entry is None:
        return character_id

    known_id, name = entry
    if known_id == NO_CHARACTER:
        # Cached by a route that had no id to give. Adopt the live one so the *next*
        # switch is detectable, rather than treating this as a switch itself.
        _cache[_handle(client)] = (character_id, name)
    elif known_id != character_id:
        logger.debug("Character switch detected (%s -> %s); dropping cached name", known_id, character_id)
        forget(client)
    return character_id


async def refresh_identities(clients: list) -> list[int]:
    """refresh_identity across clients, concurrently. Positionally aligned with `clients`."""
    return list(await asyncio.gather(*(refresh_identity(c) for c in clients)))


# ---------------------------------------------------------------------------
# Memory route
#
# The name lives on WizPlayerNameBehavior as packed table indices; bartlebot.name_tables
# decodes them. Two keypresses and a UI settle become two memory reads, and nothing on
# screen moves.
# ---------------------------------------------------------------------------

# Offsets on ClientWizPlayerNameBehavior, from the r800294 type dump.
#   poetry run python tools/typedump.py show ClientWizPlayerNameBehavior
NAME_KEYS_OFFSET = 0x160  # m_nameKeys, unsigned int
GENDER_OFFSET = 0x1B0  # m_eGender, enum eGender

# Behaviors are matched on this suffix rather than by an exact template name. The dump's
# class name is not the runtime template name and the difference is not derivable
# (ClientWizPlayerNameBehavior -> "WizPlayerNameBehavior", ClientInventoryBehavior ->
# "WizardInventoryBehavior"), so a hardcoded name is a rename away from silently failing.
_NAME_BEHAVIOR_SUFFIX = "namebehavior"

# ClientPetNameBehavior inherits from ClientWizPlayerNameBehavior and carries m_nameKeys at
# the same offset, so a loose match could return the pet's name. A pet is its own
# ClientObject and should not appear here at all, but the guard costs one comparison.
_PET_MARKER = "pet"


async def _find_name_behavior(client):
    """Return this client's wizard-name behavior, or None."""
    for behavior in await client.client_object.inactive_behaviors():
        name = await behavior.behavior_name()
        if not name:
            continue
        lowered = name.lower()
        if lowered.endswith(_NAME_BEHAVIOR_SUFFIX) and _PET_MARKER not in lowered:
            return behavior
    return None


async def read_name_from_memory(client) -> str | None:
    """Return this client's wizard name by decoding m_nameKeys, or None.

    None means "ask another route", and covers three genuinely different cases that the
    caller treats identically: the behavior was not found, the reads failed, or the name
    tables are older than the client and cannot resolve the indices. The last of those is
    the one that matters operationally — see bartlebot.name_tables — and it is exactly why
    the character-page route stays.

    Costs a walk of the behavior list (~38 entries, each a pointer read plus a string
    read), so it belongs behind the cache, not on a hot path.
    """
    try:
        behavior = await _find_name_behavior(client)
        if behavior is None:
            logger.debug("No wizard-name behavior on this client object")
            return None
        name_keys = await behavior.read_value_from_offset(NAME_KEYS_OFFSET, Primitive.uint32)
        gender = await behavior.read_value_from_offset(GENDER_OFFSET, Primitive.uint32)
    except Exception:
        logger.debug("Could not read the wizard name from memory", exc_info=True)
        return None

    name = decode_name_keys(name_keys, gender)
    if name:
        # Neither the name nor its name_keys is logged. Logs get pasted into bug
        # reports, and a wizard name in one links the account to whoever filed it —
        # see "No real player data" in CLAUDE.md. name_keys is not a safer stand-in:
        # `tools/name_keys.py --decode` turns it straight back into the name. That it
        # resolved at all is the part worth recording (#156).
        logger.debug("Resolved a wizard name from memory")
    return name


# Characters whose name memory could not resolve, as (handle, character_id). Without this,
# a client whose name the shipped tables do not know would pay the behavior-list walk on
# every client-loop tick, forever. Keyed by character id so a switch retries automatically:
# swapping to a character the tables *do* know must resolve immediately.
_memory_misses: set[tuple[int, int]] = set()


async def fill_names_from_memory(clients: list) -> bool:
    """Cache any unknown wizard name that memory can supply. Returns True if any was new.

    The display path. Deliberately does *not* fall back to the character page: a sidebar
    label is not worth opening and closing the character UI on a live client, which would
    flash the game window every time a name happened to be unknown. Surfaces that need a
    name badly enough to pay for keypresses call resolve_names instead.
    """
    ids = await refresh_identities(clients)
    learned = False
    for client, character_id in zip(clients, ids):
        if character_id == NO_CHARACTER or cached_name(client):
            continue
        key = (_handle(client), character_id)
        if key in _memory_misses:
            continue
        name = await read_name_from_memory(client)
        if name:
            remember(client, name, character_id)
            learned = True
        else:
            _memory_misses.add(key)
    return learned


async def resolve_names(clients: list) -> list[str | None]:
    """Return each client's wizard name, detecting and caching any that are unknown.

    The returned list is positionally aligned with `clients`; an entry is None when that
    client's name could not be determined.

    Detection is gathered across the clients that need it, for the same reason the pre-run
    deck applies and the group-membership survey are: there is no shared character page to
    contend over. Each client is its own process with its own UI, and send_key posts to a
    single window handle, so the keypresses cannot cross. Almost all of the elapsed time is
    the CHARACTER_PAGE_SETTLE sleep plus the poll pauses in _read_title_scroll, and those
    overlap perfectly; only the ReadProcessMemory calls serialise on the loop thread.

    Three routes are tried in order, cheapest first:

      1. the cache — free
      2. memory, via read_name_from_memory — two reads behind a behavior-list walk, and no
         input at all, so nothing on screen moves
      3. the character page, via detect_wizard_name — two keypresses and a UI settle

    Route 2 handles every character whose name the shipped tables know. Route 3 exists for
    the case where they do not: Wizard101 added names and bartlebot/data/name_tables.json
    has not been regenerated yet. That is rare, but it is a real state the app has to keep
    working in, so the keypress path stays rather than being deleted.

    No return_exceptions is needed — both detection routes absorb every failure and answer
    None, so one unreadable client cannot cancel detection for the rest.
    """
    # Check for character switches first, so a client that swapped since the last call is
    # re-detected rather than answered from a cache naming its previous wizard.
    ids = await refresh_identities(clients)

    names: list[str | None] = [cached_name(c) for c in clients]
    unknown = [i for i, name in enumerate(names) if name is None]
    if not unknown:
        return names

    from_memory = await asyncio.gather(*(read_name_from_memory(clients[i]) for i in unknown))
    unresolved: list[int] = []
    for i, name in zip(unknown, from_memory):
        if name:
            remember(clients[i], name, ids[i])
            names[i] = name
        else:
            unresolved.append(i)

    if not unresolved:
        return names

    logger.debug("Falling back to the character page for %d client(s)", len(unresolved))
    detected = await asyncio.gather(*(detect_wizard_name(clients[i]) for i in unresolved))
    for i, name in zip(unresolved, detected):
        if name:
            remember(clients[i], name, ids[i])
            names[i] = name
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
                name = strip_markup(await member.name())
                return name or None
    except Exception:
        logger.debug("Could not read the wizard name from combat", exc_info=True)
    return None
