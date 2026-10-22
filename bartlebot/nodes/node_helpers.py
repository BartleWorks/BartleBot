"""Shared async helpers for node implementations."""

import asyncio
import enum
import fnmatch
import logging
import math
import re
import weakref
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from typing import NamedTuple

from wizwalker import ExceptionalTimeout, Keycode, MemoryReadError
from wizwalker.extensions.scripting import teleport_to_friend_from_list

logger = logging.getLogger(__name__)

from bartlebot.clients import sightings
from bartlebot.game import dialog_captions, wizard_names, zone_names
from bartlebot.nodes.paths import (  # noqa: F401 — re-exported for callers
    ADVANCE_DIALOG_PATH,
    DECLINE_QUEST_PATH,
    DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH,
    FRIENDS_LIST_WINDOW_NAME,
    FRIENDS_LIST_WINDOW_NAMES,
    GAUNTLET_ENTRANCE_PATH,
    HUB_BUTTON_PATH,
    LARGE_FRIENDS_LIST_WINDOW_NAME,
    MESSAGE_BOX_CAPTION_PATH,
    MESSAGE_BOX_CENTER,
    MESSAGE_BOX_LEFT,
    MESSAGE_BOX_RIGHT,
    TELEPORT_TIMER_PATH,
)
from bartlebot.nodes.timing import (  # noqa: F401 — re-exported for callers
    DIALOG_FAST_POLL_INTERVAL,
    DIALOG_PAGE_SETTLE,
    DIALOG_TIMEOUT,
    MAX_RETRIES,
    OBJECT_USE_DELAY,
    POLL_INTERVAL,
    PORT_QUIET_GRACE,
    PROMPT_CLEAR_SETTLE,
    TRIGGER_TIMEOUT,
    WINDOW_CLOSE_CONFIRM_INTERVAL,
    WINDOW_CLOSE_RETRY_INTERVAL,
    WINDOW_CLOSE_TIMEOUT,
    ZONE_CHANGE_TIMEOUT,
    ZONE_SETTLE_GRACE,
)
from bartlebot.options import player_realms


async def all_in_npc_range(clients: list) -> bool:
    """Return True when every client has the interact prompt showing."""
    results = await asyncio.gather(*[c.is_in_npc_range() for c in clients])
    return all(results)


async def none_in_npc_range(clients: list) -> bool:
    """Return True when no client has an interact prompt showing."""
    results = await asyncio.gather(*[c.is_in_npc_range() for c in clients])
    return not any(results)


async def all_at_dungeon_entrance(clients: list, path: list[str] = DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH) -> bool:
    """Return True when every client has the dungeon entrance prompt showing.

    Pass GAUNTLET_ENTRANCE_PATH for gauntlets, which lack a TeamUpButton.
    """
    results = await asyncio.gather(*[is_visible_by_path(c, path) for c in clients])
    return all(results)


async def none_at_dungeon_entrance(clients: list, path: list[str] = DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH) -> bool:
    """Return True when no client has the dungeon entrance prompt showing.

    Pass GAUNTLET_ENTRANCE_PATH for gauntlets, which lack a TeamUpButton.
    """
    results = await asyncio.gather(*[is_visible_by_path(c, path) for c in clients])
    return not any(results)


class PromptKind(NamedTuple):
    """A prompt a node stands a client in front of, and how to tell it is showing.

    ``showing`` checks one client; ``all_showing`` and ``none_showing`` check a group.
    The group checks are kept rather than derived from ``showing`` so a test patching
    ``all_at_dungeon_entrance`` still reaches ``reach_prompt`` — the constants below
    look every check up at call time for the same reason.
    """

    name: str
    showing: Callable[[object], Awaitable[bool]]
    all_showing: Callable[[list], Awaitable[bool]]
    none_showing: Callable[[list], Awaitable[bool]]


#: The X-to-interact prompt raised by an NPC, sign, door or other interactable.
INTERACT_PROMPT = PromptKind(
    "interact prompt",
    lambda c: c.is_in_npc_range(),
    lambda cs: all_in_npc_range(cs),
    lambda cs: none_in_npc_range(cs),
)

#: The entrance prompt raised by standing on a dungeon sigil.
DUNGEON_ENTRANCE_PROMPT = PromptKind(
    "dungeon entrance prompt",
    lambda c: is_visible_by_path(c, DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH),
    lambda cs: all_at_dungeon_entrance(cs),
    lambda cs: none_at_dungeon_entrance(cs),
)


# One lock per client around a dialogue page's read and its press. See DIALOG_PAGE_SETTLE.
_dialog_locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _dialog_lock(client) -> asyncio.Lock:
    lock = _dialog_locks.get(client)
    if lock is None:
        lock = _dialog_locks[client] = asyncio.Lock()
    return lock


async def advance_dialog_once(client) -> bool:
    """Advance *client*'s open dialogue by one page, declining a side quest rather than taking it.

    The one place dialogue is pressed. A side quest's offer page puts ACCEPT on the right
    button, the same one SPACEBAR presses to advance every other page, so pressing through
    blind takes every side quest an NPC offers. The offer page is the only one with a left
    button (DECLINE), so while that shows it is clicked instead, and nothing else is pressed.

    DECLINE is only read once the right button shows: a child's visibility is its own flag
    (see is_visible_by_path), and the right button is the one already trusted to say a
    dialogue is open.

    Returns True while dialogue is open -- pressed, or a DECLINE that could not be clicked,
    which the next call tries again rather than falling back to SPACEBAR.
    """
    async with _dialog_lock(client):
        if not await is_visible_by_path(client, ADVANCE_DIALOG_PATH):
            return False
        if await is_visible_by_path(client, DECLINE_QUEST_PATH):
            if await click_by_path(client, DECLINE_QUEST_PATH):
                logger.info("Declined a side quest offer")
            else:
                logger.debug("A side quest offer's DECLINE could not be clicked yet")
        else:
            await client.send_key(Keycode.SPACEBAR, 0.1)
        await asyncio.sleep(DIALOG_PAGE_SETTLE)
        return True


async def advance_dialogue_until_clear(
    clients: list,
    safety_delay: float,
    timeout: float | None,
    first_wait: float | None = None,
) -> bool:
    """Advance dialogue on all clients until all are dialogue-free for safety_delay seconds.

    Advances any client that has an open dialogue banner (advance_dialog_once, which declines
    a side quest offer), then resets the quiet timer. Polls at DIALOG_FAST_POLL_INTERVAL
    after a dismissal and POLL_INTERVAL during the quiet phase (no dialogue detected). Returns True when all clients have been dialogue-free
    for safety_delay seconds. Returns False if timeout expires while clients are still in
    dialogue.

    `first_wait`, when given, is how long each client may take to open its first dialogue,
    counted from the call — the time an animation may take to open it. It is per client: one
    player's window opening early says nothing about another's animation, which is still
    playing. A client that has shown dialogue needs only the safety_delay quiet period after
    the last page.
    """
    loop = asyncio.get_event_loop()
    overall_start = loop.time()
    quiet_since = overall_start
    # The clients whose first dialogue has not been seen yet, while first_wait still covers them.
    unseen = set(range(len(clients))) if first_wait is not None else set()

    while True:
        now = loop.time()
        dialogs = await asyncio.gather(*[advance_dialog_once(c) for c in clients])
        unseen -= {i for i, shown in enumerate(dialogs) if shown}
        animating = bool(unseen) and (now - overall_start) < (first_wait or 0.0)

        if not any(dialogs) and (now - quiet_since) >= safety_delay and not animating:
            return True

        if timeout is not None and (now - overall_start) >= timeout:
            return False

        if any(dialogs):
            quiet_since = loop.time()
            await asyncio.sleep(DIALOG_FAST_POLL_INTERVAL)
        else:
            await asyncio.sleep(POLL_INTERVAL)


async def identify_modal(client) -> str | None:
    """Return the lang key of the modal currently on screen, or None.

    Every in-game modal is the same MessageBoxModalWindow tree, so "a modal is up" says
    nothing about which one it is. Its caption does: see bartlebot.game.dialog_captions for the
    key constants to compare against (INSTANCE_EXIT, FRIEND_BUSY, …) and for how the match
    survives markup, substitution slots, and all seven locales.

    None means *no recognized dialog* — either nothing is showing, or something the
    snapshot has not seen. The two are deliberately not distinguished: callers should have
    a button-based fallback for both, so a caption we do not know degrades to the previous
    behavior rather than hanging. Use modal_showing() to tell them apart when it matters.
    """
    return dialog_captions.identify_caption(await get_text_by_path(client, MESSAGE_BOX_CAPTION_PATH))


async def modal_showing(client) -> bool:
    """Return True when any modal message box is on screen, recognized or not.

    Reads the caption rather than the modal window itself: a window's is_visible() flag is
    its own and ignores its parents (see is_visible_by_path), whereas CaptionText only
    holds text while a dialog is actually up.
    """
    return bool(await get_text_by_path(client, MESSAGE_BOX_CAPTION_PATH))


class ModalOutcome(NamedTuple):
    """What resolve_modal() did about the dialog on screen.

    `answered` says a dialog was there and has been clicked — a caller in a retry loop
    should skip its retry this tick, because the click it would send lands on a modal that
    is still closing rather than on the button it means to press.

    `refusal` is set when that dialog was one the game will not be talked out of. Pair it
    with dialog_captions.describe() to log what the player would have read.
    """

    answered: bool
    refusal: str | None = None


_NOTHING = ModalOutcome(answered=False)


async def resolve_modal(client, *, recognized_only: bool = False) -> ModalOutcome:
    """Answer whatever modal is on screen.

    The one call a node needs while waiting for something the game may be holding behind a
    dialog. Three outcomes:

      * a question (dialog_captions.CONFIRM_TO_PROCEED) — confirmed, so the node's wait
        carries on and will now succeed;
      * a refusal (dialog_captions.REFUSALS) — dismissed, and returned, so the node can
        fail immediately: the game has said no, and waiting out a timeout only delays the
        same answer while reporting it as the wrong cause;
      * nothing recognized — falls back to the button layout, which still separates the
        two kinds: a center button offers a way to proceed, so it is confirmed; a right
        button with no center offers only a way out, so it is dismissed and reported as
        dialog_captions.UNRECOGNIZED_REFUSAL. Without that second half a dialog reworded
        by a game patch would stop failing fast and start timing out instead.

    Refusals are dismissed with the right button where it exists and the center otherwise:
    a one-button refusal puts OK in the center, and a two-button one puts cancel on the
    right, which is the answer that leaves the game alone.

    `recognized_only` answers only a dialog the snapshot knows, and leaves any other alone.
    It is for a wait that goes on answering for as long as it lasts, which would otherwise
    confirm or decline whatever the game raises meanwhile: an invitation, a request.
    """
    key = await identify_modal(client)

    if key is None:
        if recognized_only:
            return _NOTHING
        if await is_visible_by_path(client, MESSAGE_BOX_CENTER):
            await click_by_path(client, MESSAGE_BOX_CENTER)
            return ModalOutcome(answered=True)
        if await is_visible_by_path(client, MESSAGE_BOX_RIGHT):
            await click_by_path(client, MESSAGE_BOX_RIGHT)
            return ModalOutcome(answered=True, refusal=dialog_captions.UNRECOGNIZED_REFUSAL)
        return _NOTHING

    if key in dialog_captions.CONFIRM_TO_PROCEED:
        logger.debug("resolve_modal: confirming %s", key)
        await click_by_path(client, MESSAGE_BOX_CENTER)
        return ModalOutcome(answered=True)

    if key in dialog_captions.REFUSALS:
        logger.debug("resolve_modal: dismissing %s", key)
        if await is_visible_by_path(client, MESSAGE_BOX_RIGHT):
            await click_by_path(client, MESSAGE_BOX_RIGHT)
        else:
            await click_by_path(client, MESSAGE_BOX_CENTER)
        return ModalOutcome(answered=True, refusal=key)

    # Recognized, but somebody else's dialog — leave it alone rather than click blind.
    logger.debug("resolve_modal: leaving %s to its own handler", key)
    return _NOTHING


class ModalClearance(NamedTuple):
    """What clear_modals() found, and did.

    `cleared` says nothing is blocking the screen now. Otherwise `disconnected` says the
    client is out of the game, which is crash recovery's to deal with and was left alone, and
    `key` names the dialog still up when the wait ran out (None for one the snapshot does not
    know). `dismissed` is the key of every dialog it closed along the way, None for an unknown
    one: keys only, never the caption, which can carry a wizard's name.
    """

    cleared: bool
    disconnected: bool = False
    key: str | None = None
    dismissed: tuple[str | None, ...] = ()


async def clear_modals(
    client, timeout: float = TRIGGER_TIMEOUT, poll_interval: float = POLL_INTERVAL
) -> ModalClearance:
    """Close every modal on screen the way a player backs out of one, so a menu can be reached.

    A modal swallows every click aimed at the screen behind it, so a node about to click
    through a menu (the spellbook) fails on whichever dialog the game left up: the dungeon
    exit prompt of a player that never left, a stale invitation, a refusal nobody answered.
    The caption does not matter to this, which is the difference from resolve_modal(): that
    one *answers* a dialog the node is waiting on, confirming a question so the action
    proceeds. Here the node wants none of the actions, and confirming one would leave a
    dungeon, enter another or port across realms. Cancel is always safe, so the right button
    is pressed where the dialog has one, else the center (a one-button OK), else the left.

    Rereads the caption after each click, since a box takes a moment to close and another may
    be queued behind it. Gives up after `timeout` and says which dialog was left, so the node
    can fail with the reason instead of a menu that "did not open". A disconnect modal is not
    clicked: nothing on it helps, and it is the health monitor's to recover.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    dismissed: list[str | None] = []
    while True:
        text = await get_text_by_path(client, MESSAGE_BOX_CAPTION_PATH)
        if not text:
            return ModalClearance(cleared=True, dismissed=tuple(dismissed))
        key = dialog_captions.identify_caption(text)
        if key in dialog_captions.DISCONNECTS:
            return ModalClearance(cleared=False, disconnected=True, key=key, dismissed=tuple(dismissed))
        if loop.time() >= deadline:
            return ModalClearance(cleared=False, key=key, dismissed=tuple(dismissed))
        for button in (MESSAGE_BOX_RIGHT, MESSAGE_BOX_CENTER, MESSAGE_BOX_LEFT):
            if await is_visible_by_path(client, button):
                await click_by_path(client, button)
                if not dismissed or dismissed[-1] != key:
                    dismissed.append(key)
                break
        await asyncio.sleep(poll_interval)


class ModalAwareRetry:
    """Straggler-retry action for poll_each_until_seen() that answers modals first.

    A client held behind a modal is not lagging: the button the retry would press is
    unreachable while the dialog is up, so a plain retry loop burns its whole timeout
    pressing something the game cannot see. This answers the dialog instead, and only
    falls through to `action_fn` when there is nothing on screen.

    After the wait, read `.refusal`. It is set when the game refused outright, which
    the retry loop itself cannot express — poll_each_until_seen only reports which
    clients were seen, so without this a refusal looks identical to a timeout and gets
    logged as the wrong cause.

    Why not answer the dialog once up front, the way await_instance_prompt() does: that
    blocks for the full Safety Delay whenever no dialog appears, which is the common
    case. It is affordable ahead of a wait on a sticky condition (left the zone, arrived
    somewhere) but not ahead of a wait on is_loading(), which is transient — three
    seconds spent before starting to watch for a loading screen can miss it entirely and
    turn a successful teleport into a timeout.
    """

    def __init__(self, action_fn, *, recognized_only: bool = False):
        self._action_fn = action_fn
        self._recognized_only = recognized_only
        self.refusal: str | None = None

    async def __call__(self, client) -> None:
        outcome = await resolve_modal(client, recognized_only=self._recognized_only)
        if outcome.refusal:
            self.refusal = outcome.refusal
            return
        if outcome.answered:
            # The retry would land on a modal that is still closing, not on the button.
            return
        await self._action_fn(client)


async def await_instance_prompt(
    client,
    delay: float = 3.0,
    poll_interval: float = POLL_INTERVAL,
) -> ModalOutcome:
    """Wait up to `delay` for the game to ask about an instance, then answer it.

    Crossing an instance boundary raises a modal, and which one depends on the direction:
    entering a dungeon asks "you are about to enter a Dungeon", leaving one asks "you will
    lose all your progress", and a gauntlet refuses the move outright. All are answered
    here, which is why this is named for the boundary rather than for one of the dialogs —
    the previous name, confirm_dungeon_warning_dialog, described the exit warning while
    two of its five callers were actually answering the entry prompt.

    Returns as soon as a dialog is answered; otherwise polls out the full `delay` before
    concluding none is coming. Check the returned refusal — a gauntlet, or a full dungeon,
    will not be talked round, and the node should stop rather than wait for a zone change
    that cannot happen.

    Only safe ahead of a wait on a *sticky* condition (left the zone, arrived in the
    destination). Ahead of a wait on is_loading(), use ModalAwareRetry.
    """
    loop_start = asyncio.get_event_loop().time()
    while True:
        outcome = await resolve_modal(client)
        if outcome.answered:
            return outcome
        if asyncio.get_event_loop().time() - loop_start >= delay:
            return _NOTHING
        await asyncio.sleep(poll_interval)


async def answer_instance_prompts(
    clients: list,
    delay: float = 3.0,
    poll_interval: float = POLL_INTERVAL,
) -> str | None:
    """Answer the instance prompt on every client at once. Returns the first refusal, or None.

    All clients cross the boundary together, so one refusal condemns the move for the whole
    group — a gauntlet that will not let anyone change areas is not something the other
    clients can proceed past.
    """
    outcomes = await asyncio.gather(*[await_instance_prompt(c, delay, poll_interval) for c in clients])
    return next((o.refusal for o in outcomes if o.refusal), None)


async def diagnose_path(client, path: list[str]) -> str:
    """Walk path step-by-step and return a diagnostic string.

    If a segment is missing, reports the name that was expected, the names of the
    children that actually exist at that level, and how far traversal got.
    If the full path exists, reports whether is_visible() is True or False.
    Intended for DEBUG logging when is_visible_by_path unexpectedly returns False.
    """
    node = client.root_window
    for i, segment in enumerate(path):
        children = await node.children()
        names = [await c.name() for c in children]
        match = next((c for c, n in zip(children, names) if n == segment), None)
        if match is None:
            prefix = "→".join(path[:i]) if i > 0 else "(root)"
            return f"missing {segment!r} under {prefix!r}; actual children: {names}"
        node = match
    visible = await node.is_visible()
    return f"path found, is_visible()={visible}"


async def is_visible_by_path(client, path: list[str]) -> bool:
    """Return True if the UI window at path exists and is currently visible.

    IMPORTANT: a window's is_visible() flag is its OWN flag — it does not account for its
    parents. A button inside a hidden window commonly still reports visible=True, so
    reading a child to infer whether its container is open gives a constant answer.
    Observed directly: wndFriendsList reads visible=False while its btnFriendListClose
    reads visible=True, before and after the friends list is ever opened
    (tools/probe_friends_list.py).

    So: to ask "is this panel open?", read the panel's own path, not a control inside it.
    """
    window = await _get_window_from_path(client.root_window, path)
    if window is None:
        return False
    return await window.is_visible()


async def is_named_window_visible(client, name: str) -> bool:
    """Return True if any window with this name is visible.

    For windows that are located by name rather than by a fixed path — the friends list
    is opened and read as "NewFriendsListWindow", whose parent chain is not recorded.
    """
    try:
        windows = await client.root_window.get_windows_with_name(name)
    except Exception:
        logger.debug("Could not search for window %r", name, exc_info=True)
        return False
    for window in windows:
        if await window.is_visible():
            return True
    return False


async def is_enabled_by_path(client, path: list[str]) -> bool:
    """Return True if the UI window at path exists and is not grayed out (i.e., enabled)."""
    window = await _get_window_from_path(client.root_window, path)
    if window is None:
        return False
    return not await window.is_control_grayed()


async def iter_windows_by_path(parent, path: list[str]):
    """Yield every window reachable from parent by this sequence of child names, in tree order.

    Lazy on purpose: the single-result callers below take the first yielded window and stop,
    so an unambiguous path costs exactly what a first-match walk always cost. Only a caller
    that asks for every match pays to explore the remaining branches.

    An empty path yields the parent itself.
    """
    if not path:
        yield parent
        return
    for child in await parent.children():
        if await child.name() == path[0]:
            async for window in iter_windows_by_path(child, path[1:]):
                yield window


async def _get_window_from_path(parent, path: list[str]):
    """Traverse the UI window tree by a sequence of child names.

    Returns the matching Window object, or None if the path cannot be followed.

    Backtracks across same-named siblings: if two children share a name and only the
    second one leads to the rest of the path, the second is returned. This is what makes
    ambiguous names like Wizard101's duplicate `ButtonLayout` windows safe to path
    through, as long as one branch alone completes the path.

    When SEVERAL branches complete the path — a list menu whose rows are structurally
    identical — the first in tree order wins silently. Use find_windows_by_path() or
    resolve_path_match() where that choice matters.
    """
    async for window in iter_windows_by_path(parent, path):
        return window
    return None


async def find_windows_by_path(parent, path: list[str]) -> list:
    """Return every window reachable by this name path, in tree order.

    Unlike _get_window_from_path(), this does not stop at the first branch that completes
    the path, so the caller can see that a path is ambiguous and pick among the matches.
    """
    return [window async for window in iter_windows_by_path(parent, path)]


def strip_html(text: str) -> str:
    """Remove HTML tags from a string (e.g. '<center>Unicorn' → 'Unicorn')."""
    return re.sub(r"<[^>]+>", "", text).strip()


async def _window_text(window) -> str:
    """Read one window's displayed text, stripped of the game's HTML wrapping.

    Returns '' for any window that has no text or whose text cannot be decoded — a routine
    outcome, since maybe_text() reads a different offset per class and fails on many of them.
    """
    try:
        return strip_html(await window.maybe_text() or "")
    except Exception:
        logger.debug("Could not read window text", exc_info=True)
        return ""


async def window_text_matches(window, needle: str, *, exact: bool = False, deep: bool = True) -> bool:
    """Whether this window's text — or, when deep, any descendant's — matches needle.

    Case-insensitive either way; `exact` switches equality for substring. Deep by default
    because a menu row is usually a container whose label lives on a child text window.
    """
    target = needle.strip().casefold()
    text = (await _window_text(window)).casefold()
    if (text == target) if exact else (target in text):
        return True
    if not deep:
        return False
    try:
        children = await window.children()
    except Exception:
        logger.debug("Could not read window children while text matching", exc_info=True)
        return False
    for child in children:
        if await window_text_matches(child, needle, exact=exact, deep=True):
            return True
    return False


async def resolve_path_match(
    client,
    path: list[str],
    *,
    match_text: str = "",
    match_text_exact: bool = False,
    match_index: int = 0,
) -> tuple[object | None, int]:
    """Pick one window among everything `path` matches.

    Returns ``(window, candidate_count)``, where the count is how many windows the path
    matched before `match_text` filtered them — so a caller can report "matched 6 rows" even
    when its text filter then rejected all of them. The window is None when nothing matched,
    when no match carried the text, or when match_index is out of range.

    With the defaults this is `_get_window_from_path` plus a count: first match, no filter.
    """
    candidates = await find_windows_by_path(client.root_window, path)
    count = len(candidates)
    if match_text:
        matched = []
        for window in candidates:
            if await window_text_matches(window, match_text, exact=match_text_exact):
                matched.append(window)
        candidates = matched
    if not candidates or not (-len(candidates) <= match_index < len(candidates)):
        return None, count
    return candidates[match_index], count


async def poll_until(condition_coro, timeout: float | None, poll_interval: float = POLL_INTERVAL) -> bool:
    """Poll condition_coro every poll_interval until it returns True or timeout expires.

    Always evaluates condition_coro at least once before the timeout clock starts, so a
    condition that is already True never races against a tight deadline.
    """
    if await condition_coro():
        return True

    async def _poll():
        while not await condition_coro():
            await asyncio.sleep(poll_interval)

    try:
        await asyncio.wait_for(_poll(), timeout=timeout)
        return True
    except TimeoutError:
        return False


def skip_while_loading(action_fn):
    """Wrap a straggler-retry action so it never fires at a client that is loading.

    A loading client is not a straggler that needs prodding: its trigger has already
    fired, and every state a retry loop watches — zone name, position, the UI — is frozen
    at its pre-transition value until the screen clears. Retrying it there is at best
    wasted (a click the game cannot receive) and at worst destructive: a teleport issued
    mid-load is applied when the client lands, pulling it straight back out of the zone it
    had just reached on its own. That is issue #70, and on screen it looks like a
    successful transition undone the moment it completes.

    Wrap any action_fn whose check_fn is something *other* than the loading screen — a zone
    name, a position, a window. A loop whose check_fn is is_loading() already counts a
    loading client as seen and never retries it, so it needs no wrapper and should not pay
    for a second read of the same flag.
    """

    async def _guarded(client) -> None:
        if await client.is_loading():
            return
        await action_fn(client)

    return _guarded


async def poll_each_until_seen(
    clients: list,
    check_fn,
    action_fn,
    timeout: float,
    poll_interval: float = POLL_INTERVAL,
    stop_fn=None,
) -> set[int]:
    """Poll until check_fn has returned True for every client at least once.

    Re-calls action_fn for each client not yet seen on every poll cycle.
    Returns the set of client indices that triggered. If all clients triggered,
    len(result) == len(clients); on timeout, len(result) < len(clients).

    `stop_fn`, if given, is awaited once per poll cycle — after a new check_fn read but
    before the timeout check — as `stop_fn([clients[i] for i in seen])`, and only once at
    least one client has been seen. Returning True ends the poll immediately, before
    `timeout` elapses, with whatever has been seen so far. This is for a caller whose
    action_fn is not the only way a straggler might resolve — a client already-seen
    reaching some further state elsewhere makes continuing to chase the stragglers here a
    waste of the very budget meant to bound this poll.

    If check_fn is anything other than the loading screen itself, wrap action_fn in
    skip_while_loading() — a client mid-loading-screen still reads its old zone, position
    and UI, so an unguarded retry fires at a client that is already on its way and lands
    once it arrives.
    """
    seen: set[int] = set()
    loop_start = asyncio.get_event_loop().time()
    while True:
        results = await asyncio.gather(*[check_fn(c) for c in clients])
        for i, triggered in enumerate(results):
            if triggered:
                seen.add(i)
        if len(seen) == len(clients):
            return seen
        if stop_fn is not None and seen and await stop_fn([clients[i] for i in seen]):
            return seen
        if asyncio.get_event_loop().time() - loop_start > timeout:
            return seen
        stragglers = [c for i, c in enumerate(clients) if i not in seen]
        await asyncio.gather(*[action_fn(c) for c in stragglers])
        await asyncio.sleep(poll_interval)


class TurnResult(NamedTuple):
    """What happened on one client's turn at a shared object, from take_turns().

    The two failures are kept apart because they are different problems and a node should
    not report them with the same message: `prompted=False` means the client never got the
    interact prompt at all — it is in the wrong place, or the object is gone — while
    `prompted=True, completed=False` means the interaction started and then did not finish,
    which is what contention on the object actually looks like.
    """

    prompted: bool
    completed: bool


async def take_turns(
    clients: list,
    interact_xyz,
    reset_xyz,
    safety_delay: float,
    turn_fn,
    numbers: list[int] | None = None,
    poll_interval: float = POLL_INTERVAL,
    object_use_delay: float | None = None,
    park_after_turn: bool = True,
    log=None,
) -> list[TurnResult]:
    """Let each client use a shared world object alone, one after another.

    For objects that only serve one player at a time. Every client waits at `reset_xyz`,
    out of the object's range; one at a time each is teleported to `interact_xyz`, given
    up to MAX_RETRIES attempts to raise the interact prompt, and then handed to `turn_fn`,
    which owns the object until it returns.

    `turn_fn(client)` is the whole of one client's interaction — the keypress and whatever
    that client must finish before another may start, which differs per node: a menu that
    opens and is read, a zone list that is paged through and dismissed, a loading screen
    that has begun. It returns truthy when that got as far as it needed to. Making it a
    callback rather than a fixed sequence is the point: the release moment is exactly what
    varies between the nodes that need this, and getting it wrong in either direction
    either serializes nothing useful or holds the object long after it is free.

    `park_after_turn` sends each client back to `reset_xyz` once it is done, so the object
    is genuinely free for the next one. Leave it on for interactions the client walks away
    from — standing in range is enough to swallow the next client's keypress, so a client
    left at the object undoes the serialization for everyone after it. Turn it off only
    when the interaction removes the client itself, as a zone transition does: there the
    teleport lands on a client that is mid-loading-screen or already elsewhere.

    Results are positional, one per client in `clients`, so a caller that passed a subset
    can map them back. `numbers` supplies the 1-based client numbers used in log messages;
    without it clients are logged by their position in `clients`, which is misleading for
    a subset.

    Never raises on a client's failure — a client that never got the prompt is recorded and
    the next one takes its turn, because one player standing in the wrong place is not a
    reason to deny the object to everybody else.
    """
    labels = numbers if numbers is not None else list(range(1, len(clients) + 1))
    # Read at call time rather than bound as a default, so the constant stays patchable.
    gap = OBJECT_USE_DELAY if object_use_delay is None else object_use_delay

    def _log(message: str, level: int = logging.DEBUG) -> None:
        if log:
            log(message, level)

    results: list[TurnResult] = []

    # Clear everyone off the object first, and let the prompt settle there, as
    # clear_stale_prompt does for the parallel path (#152): sequential mode is only worth
    # anything if exactly one client is in range at a time, and a client that starts in
    # range never leaves it on its own.
    await asyncio.gather(*[c.teleport(reset_xyz) for c in clients])
    stuck = await settle_prompt_clear(clients, INTERACT_PROMPT, safety_delay, poll_interval)
    if any(stuck):
        _log(
            f"Client(s) {[n for n, s in zip(labels, stuck) if s]}: interact prompt would not clear at reset_xyz; "
            "skipping their turns, since approaching from there could interact with the wrong object",
            logging.WARNING,
        )

    for position, client in enumerate(clients):
        number = labels[position]
        if stuck[position]:
            results.append(TurnResult(prompted=False, completed=False))
            continue
        if position:
            await asyncio.sleep(gap)

        prompted = False
        for attempt in range(1, MAX_RETRIES + 1):
            await client.teleport(interact_xyz)
            if await poll_until(lambda c=client: all_in_npc_range([c]), safety_delay, poll_interval):
                prompted = True
                break
            _log(f"Client {number}: interact prompt did not appear (attempt {attempt}/{MAX_RETRIES}); resetting")
            await client.teleport(reset_xyz)

        if not prompted:
            _log(f"Client {number}: interact prompt never appeared after {MAX_RETRIES} attempts")
            results.append(TurnResult(prompted=False, completed=False))
            continue

        _log(f"Client {number}: taking its turn at the object")
        completed = bool(await turn_fn(client))
        if not completed:
            _log(f"Client {number}: did not finish its turn at the object")
        results.append(TurnResult(prompted=True, completed=completed))

        # Park whatever the outcome: a client whose turn failed is the one most likely to
        # still be standing at the object, and it is the standing there — not the keypress
        # — that costs the next client its turn.
        if park_after_turn:
            await client.teleport(reset_xyz)
            await poll_until(lambda c=client: none_in_npc_range([c]), safety_delay, poll_interval)

    return results


def summarize_turns(results: list[TurnResult], numbers: list[int]) -> tuple[list[int], list[int]]:
    """Split take_turns() results into (never prompted, prompted but did not finish).

    Both lists hold 1-based client numbers. A node reports them separately because they
    point at different fixes — wrong coordinates versus an object that would not serve the
    client — and lumping them together is what makes a contention bug look like a bad XYZ.
    """
    unprompted = [n for n, r in zip(numbers, results) if not r.prompted]
    unfinished = [n for n, r in zip(numbers, results) if r.prompted and not r.completed]
    return unprompted, unfinished


def has_glob(pattern: str) -> bool:
    """Return True when pattern contains glob metacharacters (* ? [)."""
    return "*" in pattern or "?" in pattern or "[" in pattern


def entity_name_matches(actual: str, pattern: str) -> bool:
    """Return True when an entity's template name matches pattern.

    Two modes, chosen by the pattern itself, mirroring zone_matches():

      * No metacharacters — the whole name, ignoring case. A plain name is an ID, as
        the Entity Browser hands it over, so "Stone" is the Stone reagent node and
        nothing else. It used to be a substring match, WizSprinter's
        get_base_entities_with_vague_name(), which sent collect_reagent to
        ZF-STONE-NPC10 and to the Sandstone and DreamStone nodes (#144). A fight
        does not use this mode -- see fight_target_rank.
      * With metacharacters — case-insensitive glob against the *whole* name, so
        "*Bandit*" matches anywhere but "Bandit*" only matches names that start
        with it. This is the one way to ask for a partial match.

    A malformed glob does not raise: fnmatch treats an unbalanced bracket as a
    literal character, so "[unclosed" simply matches names containing that text
    (anchored, like any other pattern). No fallback is needed.
    """
    if actual is None:
        return False
    if has_glob(pattern):
        return fnmatch.fnmatch(actual.lower(), pattern.lower())
    return actual.lower() == pattern.lower()


def entity_name_predicate(pattern: str):
    """Build an async predicate matching entities against pattern by template name.

    Suitable for WizSprinter's find_closest_by_predicate() and
    get_base_entities_with_predicate().
    """
    return _template_name_predicate(lambda name: entity_name_matches(name, pattern))


def _template_name_predicate(matches: Callable[[str], bool]):
    """An async entity predicate that applies *matches* to the entity's template name."""

    async def _matches(entity) -> bool:
        # An entity can despawn mid-scan; WizSprinter's own vague-name predicate
        # would propagate that and abandon the whole search, so skip it instead.
        try:
            template = await entity.object_template()
            if template is None:
                return False
            return matches(await template.object_name())
        except (ValueError, MemoryReadError):
            return False

    return _matches


async def find_closest_entity_by_name(client, pattern: str):
    """Return the closest entity whose template name matches pattern, or None.

    See entity_name_matches for what a pattern means. WizSprinter's
    find_closest_by_vague_name() is never used: its hardcoded substring test is the
    loose match a plain name no longer means.
    """
    return await client.find_closest_by_predicate(entity_name_predicate(pattern))


#: Template names that share a mob's name without being its fight: the StandIn is the actor
#: that speaks a mob's pre-battle dialogue (it has no display name of its own), and -mini is
#: the pet copy of a boss. Teleporting onto a StandIn plays the speech and never starts the
#: fight, which is how Atlantea's Voracious Legionnaires stalled.
_NOT_A_FIGHT_MARKERS = ("standin", "-mini")


def fight_target_rank(actual: str | None, pattern: str) -> int | None:
    """How well an entity's template name answers a fight's plain enemy name, for handle_combat.

    0 is the name itself. 1 is a name merely containing it -- the advanced-mode ``Adv_``
    copy, a ``_Hard`` copy -- other than a StandIn or pet copy, unless *pattern* names one
    itself. None is no match. Exact wins outright rather than by distance: a substring can
    also be a different boss (``Golem-Steel-MBBoss-L50-1`` is Mr. Incinerator, not Bellows).
    """
    if not actual:
        return None
    name, wanted = actual.lower(), pattern.lower()
    if name == wanted:
        return 0
    if wanted not in name:
        return None
    if any(marker in name and marker not in wanted for marker in _NOT_A_FIGHT_MARKERS):
        return None
    return 1


async def find_closest_fight_target(client, pattern: str):
    """The closest entity to fight for *pattern*, or None: an exact name first, then a fallback.

    See fight_target_rank for the ranking. A pattern with wildcards names its own anchoring,
    so it matches as find_closest_entity_by_name does. The fallback costs a second scan of
    the entity list, paid only when nothing carries the name exactly -- in advanced mode,
    or while the mob has not loaded.
    """
    if has_glob(pattern):
        return await find_closest_entity_by_name(client, pattern)
    exact = await client.find_closest_by_predicate(
        _template_name_predicate(lambda n: fight_target_rank(n, pattern) == 0)
    )
    if exact is not None:
        return exact
    return await client.find_closest_by_predicate(
        _template_name_predicate(lambda n: fight_target_rank(n, pattern) == 1)
    )


async def find_entities_by_name(client, pattern: str) -> list:
    """Return every entity whose template name matches pattern.

    The list counterpart of find_closest_entity_by_name(), and the same matcher.
    """
    return await client.get_base_entities_with_predicate(entity_name_predicate(pattern))


def zone_matches(actual: str, pattern: str) -> bool:
    """Return True when actual matches pattern.

    If pattern contains glob metacharacters (* ? [), fnmatch is used so callers
    can write patterns like "WizardCity/*" to match any WizardCity sub-zone.
    Plain strings (no metacharacters) are compared for equality, with one
    exception: duplicates of one place are the same zone here, in both directions.
    That is a dungeon and its Challenge Mode copy (#143), and a gauntlet room in
    each of its level bands (zone_names.TIERED_GAUNTLETS), whether or not the bot
    asked for Challenge Mode and whatever band the players' levels chose. Both
    sides are compared as zone_names.canonical_zone.

    A glob is tried against the zone and against its canonical zone, so "*_Hard"
    still names only the copy, and a glob over a gauntlet's lowest band
    ("PA_Gauntlet_01/*") takes in every band.

    A malformed glob does not raise: fnmatch treats an unbalanced bracket as a
    literal character, so "[unclosed" matches the zone of that exact name and
    nothing else. No fallback is needed.
    """
    if actual is None:
        return False
    canonical = zone_names.canonical_zone(actual)
    if has_glob(pattern):
        matched = fnmatch.fnmatchcase(actual, pattern) or fnmatch.fnmatchcase(canonical, pattern)
        if matched:
            logger.debug("zone_matches: pattern %r matched actual zone %r (wildcard)", pattern, actual)
        return matched
    return actual == pattern or canonical == zone_names.canonical_zone(pattern)


async def all_left_zone(clients: list, zone: str) -> bool:
    """Return True when every client's zone_name does not match zone.

    zone may be a glob pattern (e.g. "WizardCity/*"); see zone_matches().
    Note: if the pattern is broad enough to encompass the destination zone,
    this helper will never return True until clients leave that zone family too.
    """
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    return all(not zone_matches(z, zone) for z in zones)


async def all_in_zone(clients: list, zone: str) -> bool:
    """Return True when every client's zone_name matches zone.

    zone may be a glob pattern (e.g. "WizardCity/*"); see zone_matches().
    """
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    return all(zone_matches(z, zone) for z in zones)


async def wait_for_zone(
    clients: list, zone: str, timeout: float, poll_interval: float = POLL_INTERVAL
) -> list[str] | None:
    """Poll until all clients are in zone.

    Returns None on success, or the list of actual zone names on timeout.
    """
    if await poll_until(lambda: all_in_zone(clients, zone), timeout, poll_interval):
        return None
    return list(await asyncio.gather(*[c.zone_name() for c in clients]))


import contextlib  # noqa: E402 -- beside the one switch that uses them
import contextvars  # noqa: E402

_straggler_recovery: contextvars.ContextVar[bool] = contextvars.ContextVar("straggler_recovery", default=True)


def straggler_recovery() -> bool:
    """Whether wait_for_zone_with_recovery ports stragglers when its caller does not say. On by default."""
    return _straggler_recovery.get()


@contextlib.contextmanager
def without_straggler_recovery():
    """Turn straggler recovery off for everything awaited inside the block, in this task and its children."""
    token = _straggler_recovery.set(False)
    try:
        yield
    finally:
        _straggler_recovery.reset(token)


#: Under Player Realms: Separate, who shares whose realm as a zone change begins. See note_realm_peers.
_realm_peers: contextvars.ContextVar[dict | None] = contextvars.ContextVar("realm_peers", default=None)


async def note_realm_peers(machine, clients: list) -> None:
    """Remember who shares whose realm, just before a zone change that keeps realms.

    Only a Separate bot (#132) needs this, and for anything else it costs nothing. Straggler
    recovery ports a player who missed a transition to one who made it, and a friends-list
    port moves the porter onto the target's realm -- so in a Separate bot the target has to
    be someone already on the straggler's realm. The machine's record cannot say who that
    is once the transition is under way: everyone who went through the door loaded, and a
    load is when the record forgets. A door keeps each player's own realm, though, so who
    shared a realm *before* the transition is who shares one after it.

    Call it as close to the transition as the node allows, once the players are standing at
    the door or sigil, where a check is definite. Kept in a context variable, like
    straggler_recovery, so it reaches wait_for_zone_with_recovery through whatever the node
    calls in between; each node runs in its own task, so it never outlives the node.
    """
    if not player_realms.machine_is_separate(machine):
        return
    await read_sightings(clients, machine)
    _realm_peers.set({c: [p for p in machine.together_with(c, clients) if p is not c] for c in clients})


async def wait_for_zone_with_recovery(
    machine,
    clients: list,
    zone: str,
    timeout: float,
    poll_interval: float = POLL_INTERVAL,
    settle_grace: float | None = None,
    recover: bool | None = None,
) -> list[str] | None:
    """Poll until all clients are in zone, trying a teleport-to-player recovery the moment
    it becomes viable rather than only after the whole wait is exhausted.

    `recover=False` only waits and reports. Left as None it follows straggler_recovery(),
    which zone navigation turns off around every step it runs: it sorts out its own stragglers
    once the step is over, rather than porting them into a zone the rest are about to leave.
    A context variable rather than a param, so it reaches the step nodes -- and whatever they
    call -- without every one of them growing an argument no bot author should ever set.

    Some clients failing a zone transition while others land fine is usually not a crash —
    it means the stragglers have not unlocked the destination, or missed the transition
    trigger. As soon as at least one client is seen in `zone` and at least one is not, the
    stragglers are ported to whichever client arrived first (find_target_in_zone) — cheaper
    than retrying the whole transition from a world hub. All
    clients must already be mutual in-game friends for this to have anything to work with.

    Waiting out the *whole* of `timeout` before even attempting recovery (the original
    approach) only delayed the fallback that was going to be needed anyway, so recovery is
    tried as soon as it can help, with `timeout` still bounding the overall wait for
    stragglers that recovery could not fix.

    What that speed has to respect is that a straggler's zone name is stale, not wrong,
    while its transition is in flight: a client on a loading screen still reports the zone
    it left, and one whose trigger has just fired has not started its loading screen yet.
    Recovering either of those ports a client that was already arriving, and the teleport
    lands *after* it gets there — a correct transition undone (#70). So no client is
    treated as a straggler while it is loading, and `settle_grace` is the quiet window a
    non-loading straggler gets to start loading before it counts as one. The grace re-arms
    every time a straggler is seen loading, which also keeps the timeout verdict honest:
    a client mid-transition is never reported as one that failed to arrive. An endless
    loading screen is ClientHealthMonitor's to call, not this loop's.

    A straggler held at an instance prompt is not stuck either, and nothing else would press
    it: leaving a dungeon asks first, and until the question is answered the zone change never
    fires (#281). Every poll before the deadline answers the prompt of a client that is not
    loading, as many times as it shows, and re-arms the settle grace for the screen it opens.
    Only dialogs the snapshot knows are answered. A refusal ends the wait at once, with the
    zones as they are, since the game has said no.

    Returns None on success (whether or not recovery was needed), or the list of actual
    zone names if the deadline passed with some clients still not in `zone`, or the game refused.
    """
    if settle_grace is None:
        settle_grace = ZONE_SETTLE_GRACE  # read here, not in the signature, so it stays tunable
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    settled_after = loop.time() + settle_grace
    recovery_tried = False

    while True:
        zones = list(await asyncio.gather(*[c.zone_name() for c in clients]))
        if all(zone_matches(z, zone) for z in zones):
            return None

        behind = [c for c, z in zip(clients, zones) if not zone_matches(z, zone)]
        now = loop.time()
        # Mid-transition, not stuck: let the screen clear and re-read before judging it.
        loading = await asyncio.gather(*[c.is_loading() for c in behind])
        arriving = any(loading)
        prompts = await _answer_waiting_prompts(
            machine, [c for c, busy in zip(behind, loading) if not busy], now, deadline
        )
        if prompts is None:
            return zones
        if prompts:
            settled_after = loop.time() + settle_grace
            await asyncio.sleep(poll_interval)
            continue
        if arriving:
            settled_after = now + settle_grace
        elif (
            (straggler_recovery() if recover is None else recover)
            and not recovery_tried
            and now >= settled_after
            and any(zone_matches(z, zone) for z in zones)
        ):
            recovery_tried = True
            await _attempt_zone_recovery(machine, clients, zone, zones, poll_interval)
            continue  # re-read zones immediately; the recovery attempt already spent real time

        if not arriving and now >= deadline and now >= settled_after:
            return zones
        await asyncio.sleep(poll_interval)


async def _answer_waiting_prompts(machine, clients: list, now: float, deadline: float) -> bool | None:
    """Answer the instance prompt on each of `clients` that has one, while a zone wait has time left.

    True when a prompt was answered, False when there was nothing to answer, None when the game
    refused (logged here): the wait cannot succeed and should end. A read that fails is the
    client's own business and means no prompt was answered, as a client loading in raises.
    """
    if not clients or now >= deadline:
        return False
    outcomes = await asyncio.gather(*[resolve_modal(c, recognized_only=True) for c in clients], return_exceptions=True)
    answered = [o for o in outcomes if isinstance(o, ModalOutcome) and o.answered]
    if refusal := next((o.refusal for o in answered if o.refusal), None):
        machine.log(f"FAILURE: the game refused the zone change — {dialog_captions.describe(refusal)}", logging.WARNING)
        return None
    return bool(answered)


@dataclass(frozen=True)
class ZoneTarget:
    """Which hooked client to port everyone else to, or why there isn't one.

    Mirrors teleport_to_friend's TargetResolution -- same shape, different resolution rule:
    the first client already in the zone, by player order, rather than a configured name or
    slot. It outlived the teleport_to_player_if_in_zone node it was written for, removed
    once sync_in_zone superseded it; zone-arrival recovery is its one caller now.
    """

    index: int | None = None
    name: str = ""
    error: str = ""


async def find_target_in_zone(clients: list, target_zone: str, zones: list[str] | None = None) -> ZoneTarget:
    """Pick the first hooked client (in player order) already in target_zone as the target.

    `zones` lets a caller that already read every client's zone_name() pass it in rather than
    have this read it again -- the recovery path always has a fresh one on hand.

    .error is set when no client is in target_zone, or the one found there has no cached
    wizard name yet (ClientManager fills this; a client just connected may not have it yet).
    """
    if zones is None:
        zones = list(await asyncio.gather(*[c.zone_name() for c in clients]))

    in_zone_indices = [i for i, z in enumerate(zones) if zone_matches(z, target_zone)]
    if not in_zone_indices:
        return ZoneTarget(error=f"no hooked client is currently in {target_zone!r}")

    target_index = in_zone_indices[0]
    target_name = wizard_names.cached_name(clients[target_index])
    if not target_name:
        return ZoneTarget(error=f"P{target_index + 1}'s wizard name has not been detected yet")

    return ZoneTarget(index=target_index, name=target_name)


def _recovery_batches(machine, clients: list, zone: str, zones: list[str], first: int) -> list[tuple[int, list]]:
    """``(target index, stragglers)`` for each port straggler recovery should make.

    Together, every straggler ports to *first*, the first client to arrive. Separate, each
    ports only to an arrived client that shared its realm as the transition began (see
    note_realm_peers), and one with nobody like that is left where it is.
    """
    behind = [c for c, z in zip(clients, zones) if not zone_matches(z, zone)]
    if not player_realms.machine_is_separate(machine):
        return [(first, behind)]
    peers = _realm_peers.get()
    if peers is None:
        machine.log(
            f"{player_realms.OPTION} is {player_realms.SEPARATE} and nobody was seen sharing a realm "
            "before the transition, so no straggler is ported",
            logging.DEBUG,
        )
        return []
    arrived = {i for i, z in enumerate(zones) if zone_matches(z, zone)}
    batches: dict[int, list] = {}
    for straggler in behind:
        target = next(
            (i for i, c in enumerate(clients) if i in arrived and any(c is p for p in peers.get(straggler, []))),
            None,
        )
        if target is None:
            machine.log(
                f"P{clients.index(straggler) + 1} shares a realm with nobody who arrived; not porting it across realms",
                logging.INFO,
            )
            continue
        batches.setdefault(target, []).append(straggler)
    return sorted(batches.items())


async def _attempt_zone_recovery(
    machine, clients: list, zone: str, zones: list[str], poll_interval: float = POLL_INTERVAL
) -> None:
    """Best-effort teleport-to-player recovery for wait_for_zone_with_recovery.

    Ports whichever clients are not yet in `zone` (per the already-read `zones` snapshot) to
    a hooked client that already is -- under Player Realms: Separate, only one on the
    straggler's own realm (_recovery_batches). Never raises and does not itself report
    success — the caller's own polling loop re-reads zones right after this returns and
    decides that. A recovery that cannot even get started (no client's name cached yet, no
    friends online, the friend-busy dialog, …) is logged and left for the caller's normal
    timeout to report.
    """
    target = await find_target_in_zone(clients, zone, zones=zones)
    if target.error:
        machine.log(f"Recovery not possible: {target.error}", logging.DEBUG)
        return

    for index, stragglers in _recovery_batches(machine, clients, zone, zones, target.index):
        if not stragglers:
            continue
        name = target.name if index == target.index else wizard_names.cached_name(clients[index])
        await _port_stragglers(machine, index, name, stragglers, zone, poll_interval)


async def _port_stragglers(machine, index: int, name: str, stragglers: list, zone: str, poll_interval) -> None:
    """Port *stragglers* to P(*index* + 1), whose wizard name is *name*, and wait out their loading."""
    if not name:
        machine.log(f"Recovery not possible: P{index + 1}'s wizard name has not been detected yet", logging.DEBUG)
        return
    machine.log(
        f"Some clients did not reach {zone!r} but at least one did; attempting a "
        f"teleport-to-player recovery for {len(stragglers)} client(s) to P{index + 1}…",
        logging.INFO,
    )
    try:
        result = await port_to_friend(stragglers, name.lower(), poll_interval=poll_interval)
    except ValueError:
        # The message names the player it could not find, so it stays out of the log.
        machine.log(f"Recovery teleport to P{index + 1} failed: it is not on every straggler's friends list")
        return
    except ExceptionalTimeout:
        # A straggler that cannot open its friends list is not a dead client: a dialog over the
        # screen swallows the click on the Friends button (#281). Raised out of here it parked
        # the run for DEAD_CLIENT_CONFIRM_TIMEOUT. A client that did die fails the caller's next
        # read with its own error, and is handled as one.
        machine.log(
            f"Recovery teleport to P{index + 1} timed out: a window may be blocking a straggler's friends list",
            logging.WARNING,
        )
        return

    if result.refusal:
        machine.log(f"Recovery teleport refused — {dialog_captions.describe(result.refusal)}", logging.WARNING)
        return
    if result.missing(len(stragglers)):
        machine.log("Recovery teleport: not every straggler entered a loading screen", logging.WARNING)
        return

    await poll_until(lambda: none_loading(stragglers), None, poll_interval)


async def none_loading(clients: list) -> bool:
    """Return True when no client is on a loading screen."""
    loading = await asyncio.gather(*[c.is_loading() for c in clients])
    return not any(loading)


async def get_text_by_path(client, path: list[str]) -> str:
    """Return the displayed text of the UI window at path, or '' if not found."""
    window = await _get_window_from_path(client.root_window, path)
    if window is None:
        return ""
    return await window.maybe_text() or ""


_COUNTDOWN = re.compile(r"(?:(\d+):)?(\d+(?:\.\d+)?)")


def parse_countdown(text: str | None) -> float | None:
    """The seconds a countdown shows, written "25" or "0:25", markup allowed; None if it shows no number."""
    match = _COUNTDOWN.search(re.sub(r"<[^>]*>", " ", text or ""))
    if match is None:
        return None
    minutes, seconds = match.groups()
    return int(minutes or 0) * 60 + float(seconds)


async def teleport_cooldown(client) -> float | None:
    """Seconds before the hub and HOME buttons can be used again. One timer covers both.

    0.0 while the hub button is enabled. The timer only shows while the buttons are disabled,
    so a disabled button with no readable timer returns None: disabled for a reason, and for a
    time, this cannot read.
    """
    if await is_enabled_by_path(client, HUB_BUTTON_PATH):
        return 0.0
    return parse_countdown(await get_text_by_path(client, TELEPORT_TIMER_PATH))


async def click_until_gone(
    client,
    path: list[str],
    is_open=None,
    timeout: float = WINDOW_CLOSE_TIMEOUT,
    poll_interval: float = WINDOW_CLOSE_RETRY_INTERVAL,
) -> bool:
    """Click the button at `path` until the thing it closes is gone. Returns True once it is.

    A single click is not reliable: another window can sit on top of the button and swallow
    the click, and the game can drop clicks sent during an animation. Retrying until the
    target is actually gone turns "we clicked it" into "it actually closed".

    `is_open` is an async predicate for "the thing is still open". It MUST test the panel
    itself, not the close button, because a control inside a hidden window still reports
    visible=True — polling the button gives a constant answer and guarantees a timeout.
    See is_visible_by_path() for the observed evidence.

    It defaults to the button's own visibility, which is only correct for controls that
    genuinely disappear with their window; pass an explicit predicate otherwise.

    The predicate is evaluated before each click, so an already-closed panel costs one read
    and no clicks — important because these buttons often sit on toggles, where a stray
    click would reopen what was just closed.

    `poll_interval` is the floor on how fast we may click AGAIN, not on how fast we may
    notice the click worked: the gap is spent polling the predicate, so a close that took
    effect immediately returns immediately instead of after a full interval.

    Returns False if the panel is still open when the timeout expires. The warning then says
    how many of the clicks were never sent, because the button was missing or grayed. That
    separates the two ways a close can fail: none sent means a disabled button, all sent
    means something on top took them.
    """
    if is_open is None:

        async def is_open():
            return await is_visible_by_path(client, path)

    async def _gone():
        return not await is_open()

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    attempts = unsent = 0
    while True:
        if await _gone():
            return True
        attempts += 1
        if not await click_by_path(client, path):
            unsent += 1
        if loop.time() >= deadline:
            break
        if await poll_until(_gone, timeout=poll_interval, poll_interval=WINDOW_CLOSE_CONFIRM_INTERVAL):
            return True

    still_open = await is_open()
    if still_open:
        logger.warning(
            "Window closed via %s is still open after %.0fs of retries (%d of %d clicks not sent: button missing or grayed)",
            "/".join(path),
            timeout,
            unsent,
            attempts,
        )
    return not still_open


async def click_window(client, window) -> bool:
    """Click an already-resolved UI window if it is enabled (not grayed out).

    Separate from click_by_path so a caller that had to disambiguate among several
    same-named windows can click the exact one it chose, rather than re-walking the path
    and landing on whichever match comes first.
    """
    if window is None:
        return False
    if await window.is_control_grayed():
        return False
    async with client.mouse_handler:
        await client.mouse_handler.click_window(window)
    return True


async def click_by_path(client, path: list[str]) -> bool:
    """Click the UI window at path if it exists and is enabled (not grayed out).
    Returns True if found, enabled, and clicked. Returns False if not found or grayed."""
    window = await _get_window_from_path(client.root_window, path)
    return await click_window(client, window)


# ---------------------------------------------------------------------------
# Friends list
#
# The friends list has no per-row window — rows are one block of markup text in
# `listFriends`, clicked by computing a pixel offset. WizWalker already solves this, so
# these helpers wrap its (private) routines rather than re-deriving the row geometry.
# Depending on underscore-prefixed names across a library boundary is normally a bad
# trade, but both libraries are pinned by exact commit in pyproject.toml, so they cannot
# shift without a deliberate pin bump.
#
# Callers must hold `async with client.mouse_handler` around every function here.
# ---------------------------------------------------------------------------


async def open_friends_list(client):
    """Open the compact friends list if it is not already open, and return its window.

    Opens NewFriendsListWindow specifically. The game's other friends list, wndFriendsList,
    is a separate window reached from this one — see paths.FRIENDS_LIST_WINDOW_NAMES. The
    name/profile code reads the compact list, so this deliberately opens only that one.

    Raises ValueError if the window cannot be found even after clicking the button.
    """
    from wizwalker.extensions.scripting.utils import _maybe_get_named_window  # noqa: PLC0415

    # The large list has to be cleared, not clicked past. Visibility is judged below from
    # NewFriendsListWindow alone, so from the large list this reads "closed" and clicks
    # btnFriends anyway — and what the game does with that click was never established.
    # Closing first replaces an unknown with the known-good closed state, at
    # the cost of one extra toggle in a case that only arises when a list was left open by
    # hand.
    if await is_named_window_visible(client, LARGE_FRIENDS_LIST_WINDOW_NAME):
        logger.debug("Large friends list is open; closing it before opening the compact one")
        await close_friends_list(client)

    try:
        friends_window = await _maybe_get_named_window(client.root_window, FRIENDS_LIST_WINDOW_NAME)
        if not await friends_window.is_visible():
            btn = await _maybe_get_named_window(client.root_window, "btnFriends")
            await client.mouse_handler.click_window(btn)
        return friends_window
    except ValueError:
        # The window does not exist at all until the list has been opened once.
        btn = await _maybe_get_named_window(client.root_window, "btnFriends")
        await client.mouse_handler.click_window(btn)
        return await _maybe_get_named_window(client.root_window, FRIENDS_LIST_WINDOW_NAME)


async def press_key_until_gone(
    client,
    key: Keycode,
    is_open,
    timeout: float = WINDOW_CLOSE_TIMEOUT,
    poll_interval: float = WINDOW_CLOSE_RETRY_INTERVAL,
) -> bool:
    """Press `key` until `is_open()` reports the panel has closed. Returns True once gone.

    The keyboard counterpart to click_until_gone, and preferable for panels that have a
    keyboard toggle: a keypress goes to the client as a whole, so unlike a click it cannot
    land on the wrong window — which is the failure mode every close bug here has had.

    `is_open` must test the panel itself; see click_until_gone for why a close button's own
    visibility is useless for this.

    The predicate is checked before each press, because these keys are toggles: pressing
    one on an already-closed panel reopens it.

    As in click_until_gone, `poll_interval` bounds how soon the next press may go out, and
    the gap is spent polling rather than slept through.
    """

    async def _gone():
        return not await is_open()

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        if await _gone():
            return True
        await client.send_key(key, 0.1)
        if loop.time() >= deadline:
            break
        if await poll_until(_gone, timeout=poll_interval, poll_interval=WINDOW_CLOSE_CONFIRM_INTERVAL):
            return True

    still_open = await is_open()
    if still_open:
        logger.warning("Panel toggled by %s is still open after %.0fs of retries", key, timeout)
    return not still_open


async def is_friends_list_open(client) -> bool:
    """Return True if EITHER friends-list window is showing.

    There are two independent friends-list windows (see paths.FRIENDS_LIST_WINDOW_NAMES):
    the compact NewFriendsListWindow and the larger wndFriendsList. Only one is on screen
    at a time, and F cycles between them, so a check against a single name will report the
    list closed while the other one is still up.
    """
    for name in FRIENDS_LIST_WINDOW_NAMES:
        if await is_named_window_visible(client, name):
            return True
    return False


async def close_friends_list(client) -> bool:
    """Close the friends list, retrying until it is gone. Never raises.

    Closed with the F toggle rather than a close button. F cycles
    closed -> NewFriendsListWindow -> wndFriendsList -> closed, so pressing it until
    neither window is visible handles whichever list the client happens to be showing.

    A close button cannot do this job: btnFriendListClose belongs to wndFriendsList and
    does not close NewFriendsListWindow. It appeared to work earlier only because the two
    windows put their close buttons in the same place, so the click sometimes landed on
    whichever list was actually up.
    """
    try:
        return await press_key_until_gone(
            client,
            Keycode.F,
            is_open=lambda: is_friends_list_open(client),
        )
    except Exception:
        logger.debug("Could not close the friends list", exc_info=True)
        return False


async def list_online_friends(client) -> list[str]:
    """Return the exact-case name of every friend currently online, in list order.

    Opens the online friends list (if not already open) and reads the whole list window
    in one text read. That text contains every online friend regardless of the current
    scroll position, so pagination is not a concern here.

    Raises ValueError if no friends are online, which is what callers have to act on —
    an empty list and a failed read are not distinguishable from the window text alone.
    """
    from wizwalker.extensions.scripting.utils import (  # noqa: PLC0415
        _cycle_to_online_friends,
        _friend_list_entry,
        _maybe_get_named_window,
    )

    friends_window = await open_friends_list(client)
    await _cycle_to_online_friends(client, friends_window)

    friends_list = await _maybe_get_named_window(friends_window, "listFriends")
    list_text = await friends_list.maybe_text()

    if not list_text:
        raise ValueError("No friends online")

    return [entry.group("name") for entry in _friend_list_entry.finditer(list_text)]


async def resolve_friend_name(client, friend_name_lower: str) -> str:
    """Return the exact-case friend name matching the lowercased search term.

    WizWalker's friend lookups compare names case-sensitively, so callers must resolve the
    exact casing through this function before handing a name to them.

    Raises ValueError if no friends are online or no entry matches the search term.
    """
    for name in await list_online_friends(client):
        if name.lower() == friend_name_lower:
            return name

    # The name is not quoted: callers log this message, and a wizard name must not reach a log.
    raise ValueError("Could not find friend in the online friends list")


async def teleport_via_friends_list(client, friend_name_lower: str) -> None:
    """Resolve the exact-case friend name, then teleport via the in-game friends list.

    teleport_to_friend_from_list uses a case-sensitive string comparison internally, so the
    exact-case name is looked up first. The mouse handler context is activated here because
    both the resolution step and WizWalker's click chain require it.

    Raises ValueError if no friends are online or the name is not found.
    """
    async with client.mouse_handler:
        exact_name = await resolve_friend_name(client, friend_name_lower)
        await teleport_to_friend_from_list(client, name=exact_name)


TRIGGER_MARGIN = 5.0
"""Units a client must move, with no loading screen, for a port or recall to count as having happened."""

POSITION_MARGIN = 10.0
"""Units a client may land from where it was sent and still count as there. A port puts you exactly
on the player you port to, and a teleport exactly on its XYZ; this is only room for the odd wobble."""


def distance(a, b) -> float:
    """Straight-line distance between two XYZ-shaped points."""
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


SAFE_RADIUS = 1500.0
"""How far a point must be from every mob to count as safe to stand on.

A battle triggers at a fixed radius on the server, but the client shows a mob walking toward
the wizard trailing where it really is, so a fast mob pulls the wizard in from further off than
it looks: up to 886 units measured (#117, tools/probe_aggro_radius.py). WizSprinter's default of
700 did not cover that; this also leaves room for a mob to close in while a player waits there.
"""


async def mob_clearances(client, points: list) -> list[float]:
    """The distance from each of *points* to the nearest mob *client* has loaded.

    Infinite where no mob is loaded. Only loaded mobs count -- roughly 3800 units around the
    client -- so a point far from *client* can read clear when it is not. One read of the
    entity list serves every point.
    """
    mobs = [await m.location() for m in await client.get_mobs()]
    return [min((distance(point, mob) for mob in mobs), default=math.inf) for point in points]


async def mob_clearance(client, point) -> float:
    """The distance from *point* to the nearest mob *client* has loaded. See :func:`mob_clearances`."""
    return (await mob_clearances(client, [point]))[0]


async def _position_or_none(client):
    """The client's position, or None when it cannot be read -- a body mid-load, say."""
    try:
        return await client.body.position()
    except Exception:
        logger.debug("Could not read a client's position", exc_info=True)
        return None


async def _or_none(awaitable):
    """*awaitable*'s result, or None if it raised. For reads a check can do without."""
    try:
        return await awaitable
    except Exception:
        return None


async def _entity_gids(client) -> frozenset[int] | None:
    """The global ids *client* has loaded, or None if its entity list could not be read."""
    try:
        entities = await client.get_base_entity_list()
    except Exception:
        logger.debug("Could not read a client's entity list", exc_info=True)
        return None
    gids = await asyncio.gather(*(_or_none(e.global_id_full()) for e in entities))
    return frozenset(g for g in gids if g is not None)


async def _reading(client) -> sightings.Reading:
    gid, zone, position, loading, seen = await asyncio.gather(
        _or_none(client.client_object.global_id_full()),
        _or_none(client.zone_name()),
        _position_or_none(client),
        _or_none(client.is_loading()),
        _entity_gids(client),
    )
    # An unreadable loading flag is treated as loading: nothing else it reports can be trusted.
    return sightings.Reading(gid=gid, zone=zone, position=position, loading=loading is not False, seen=seen)


async def read_sightings(clients: list, machine=None) -> sightings.Sightings:
    """Check which of *clients* share a zone, realm and instance. See :mod:`bartlebot.clients.sightings`.

    One id read, one entity-list read and one set intersection per client, about 5 ms each.
    Nothing here raises: a read that fails makes its pairs Unknown.

    With *machine*, the check is also folded into its record of who has been seen together
    (``BotMachine.note_sightings``), which is what lets a later check answer for players
    who have since walked out of each other's sight.
    """
    readings = await asyncio.gather(*(_reading(c) for c in clients))
    result = sightings.Sightings(clients, readings)
    note = getattr(machine, "note_sightings", None)
    if note is not None:
        note(result)
    return result


async def _player_gids(client) -> frozenset[int] | None:
    """The global ids of the players *client* has loaded, itself included, or None if unreadable.

    A player is an entity made from the same template as the client's own character. NPCs,
    mobs, pets and wisps each have templates of their own, so no template name is spelled
    out here, and none has to be right.
    """
    player_template = await _or_none(client.client_object.object_name())
    if not player_template:
        return None
    try:
        entities = await client.get_base_entity_list()
    except Exception:
        logger.debug("Could not read a client's entity list", exc_info=True)
        return None
    names = await asyncio.gather(*(_or_none(e.object_name()) for e in entities))
    players = [e for e, name in zip(entities, names) if name == player_template]
    gids = await asyncio.gather(*(_or_none(e.global_id_full()) for e in players))
    return frozenset(g for g in gids if g is not None)


async def count_strangers(clients: list) -> int:
    """How many players outside *clients* any of them has loaded. See :func:`sightings.count_strangers`.

    For a place only the team should be, such as a dungeon instance it has just entered (#279).
    Every client is read, since a Separate bot's players need not share an instance. Never
    raises, and a read that fails counts nobody.
    """
    team, seen = await asyncio.gather(
        asyncio.gather(*(_or_none(c.client_object.global_id_full()) for c in clients)),
        asyncio.gather(*(_player_gids(c) for c in clients)),
    )
    return sightings.count_strangers(team, seen)


class PortToFriendResult(NamedTuple):
    """Outcome of port_clients()'s wait for every port to start.

    Indices are into the clients_to_move list the caller passed in. A port shows itself one of
    three ways, and each has its own set:

    * `seen_loading` -- the client entered a loading screen: another zone, or the same zone on
      another realm.
    * `jumped` -- its position moved with no loading screen: the same zone and realm.
    * `presumed` -- neither, through a re-issue, for a client the caller said may already be
      where it is going. A port onto a player you are standing beside does nothing visible.

    `refusal` is set when the game refused the port outright for one client — pair it with
    dialog_captions.describe(); `refused_index` says which client hit it. `missing()` is what
    is left over: clients that showed nothing at all by the ceiling.
    """

    seen_loading: set[int]
    refusal: str | None = None
    refused_index: int | None = None
    jumped: frozenset[int] = frozenset()
    presumed: frozenset[int] = frozenset()

    def missing(self, count: int) -> list[int]:
        """Indices below `count` that neither loaded, jumped, nor were presumed already there."""
        started = self.seen_loading | self.jumped | self.presumed
        return [i for i in range(count) if i not in started]


async def port_to_friend(
    clients_to_move: list,
    friend_name_lower: str,
    *,
    trigger_timeout: float = TRIGGER_TIMEOUT,
    ceiling: float = ZONE_CHANGE_TIMEOUT,
    poll_interval: float = POLL_INTERVAL,
    maybe_there: Collection[int] = (),
    target=None,
) -> PortToFriendResult:
    """Teleport clients_to_move to friend_name_lower via the friends list, and wait for each
    to enter a loading screen.

    The mechanics shared by everything that ports players to a friend — teleport_to_friend
    and node_helpers' own zone-arrival recovery: initiate,
    watch for whatever modal a not-yet-loading client is showing (a dungeon exit or
    cross-realm warning is confirmed so the port proceeds; a friend-busy dialog is a refusal),
    retry a straggler once trigger_timeout's grace period has passed, and cap the whole wait
    at `ceiling`. Only how the target and the surrounding zone check are resolved differs
    between callers, and that stays with them.

    Raises ValueError if the initial teleport, or a straggler's retry, cannot resolve the
    friend (not found, or not online) — the same error teleport_via_friends_list raises.

    None of the waiting here is bounded by Safety Delay: a caller using this as a fast
    fallback rather than as the whole point of the node should size `ceiling` accordingly
    rather than assume this returns quickly.
    """
    return await port_clients(
        clients_to_move,
        lambda client: teleport_via_friends_list(client, friend_name_lower),
        trigger_timeout=trigger_timeout,
        ceiling=ceiling,
        poll_interval=poll_interval,
        maybe_there=maybe_there,
        target=target,
    )


async def port_clients(
    clients_to_move: list,
    initiate,
    *,
    trigger_timeout: float = TRIGGER_TIMEOUT,
    ceiling: float = ZONE_CHANGE_TIMEOUT,
    poll_interval: float = POLL_INTERVAL,
    maybe_there: Collection[int] = (),
    quiet_grace: float | None = None,
    target=None,
) -> PortToFriendResult:
    """Start a port on every client with `initiate`, and wait for each port to show itself.

    `initiate(client)` is how one client's port is started: the friends list for port_to_friend,
    the group roster for group_manager.port_to_group_member. Everything after the start is the
    same whichever way it began -- the dungeon exit and cross-realm warnings are confirmed, a
    friend-busy dialog is a refusal -- so it lives here once. See port_to_friend for the rest.

    What a successful port looks like depends on where the client starts, and the realm it is on
    cannot be read without opening the spellbook, so every sign is accepted from every client:

    * another zone, or the same zone on another realm -- a loading screen;
    * the same zone and realm, standing elsewhere -- no loading screen, but the client's position
      jumps onto the target (more than TRIGGER_MARGIN);
    * the same zone and realm, already beside the target -- nothing at all.

    Only the last is ambiguous, so it is only ever concluded for the indices in `maybe_there`:
    clients the caller has reason to think are already there. Such a client that shows nothing
    for `quiet_grace` has its port re-issued once, in case the first never registered, and
    after another `quiet_grace` is presumed there. Every other quiet client is re-issued once
    per `trigger_timeout` -- once per window, not every poll -- until `ceiling`.

    With `target`, the hooked client being ported to, a presumption has to be seen: the
    client must have the target loaded (#130), which proves the same zone, realm and
    instance where standing beside it proves only the zone. One that does not is on another
    realm, where the port would have loaded, so it goes back to being re-issued like any
    other quiet client.

    Raises whatever `initiate` raises; both starters raise ValueError when they cannot begin.
    """
    if quiet_grace is None:
        quiet_grace = PORT_QUIET_GRACE  # read here, not in the signature, so it stays tunable
    maybe_there = set(maybe_there)
    count = len(clients_to_move)
    loop = asyncio.get_event_loop()

    # Positions are read before the port, or a jump that happens during initiate goes unseen.
    start_positions = await asyncio.gather(*[_position_or_none(c) for c in clients_to_move])
    await asyncio.gather(*[initiate(c) for c in clients_to_move])

    seen_loading: set[int] = set()
    jumped: set[int] = set()
    presumed: set[int] = set()
    loop_start = loop.time()
    last_attempt = [loop_start] * count
    reissued = [False] * count

    def result(refusal: str | None = None, refused_index: int | None = None) -> PortToFriendResult:
        return PortToFriendResult(seen_loading, refusal, refused_index, frozenset(jumped), frozenset(presumed))

    while True:
        for i in range(count):
            if i in seen_loading or i in jumped or i in presumed:
                continue
            outcome = await resolve_modal(clients_to_move[i])
            if outcome.refusal:
                return result(outcome.refusal, i)
            if outcome.answered:
                # The port only starts once the dialog is gone, so its quiet window starts then too.
                last_attempt[i] = loop.time()

        loading_flags = await asyncio.gather(*[c.is_loading() for c in clients_to_move])
        for i, is_now_loading in enumerate(loading_flags):
            if is_now_loading:
                seen_loading.add(i)

        # A loading client still reports the position it left, so only the rest are read.
        unsettled = [i for i in range(count) if i not in seen_loading and i not in jumped and i not in presumed]
        positions = await asyncio.gather(*[_position_or_none(clients_to_move[i]) for i in unsettled])
        quiet: list[int] = []
        for i, pos in zip(unsettled, positions):
            start = start_positions[i]
            if pos is not None and start is not None and distance(pos, start) > TRIGGER_MARGIN:
                jumped.add(i)
            else:
                quiet.append(i)

        now = loop.time()
        due: list[int] = []
        for i in quiet:
            grace = quiet_grace if i in maybe_there else trigger_timeout
            if now - last_attempt[i] < grace:
                continue
            if i in maybe_there and reissued[i]:
                if target is None or await _sees_each_other(target, clients_to_move[i]):
                    presumed.add(i)
                    continue
                maybe_there.discard(i)
            due.append(i)

        if len(seen_loading | jumped | presumed) == count or now - loop_start > ceiling:
            return result()

        if due:
            await asyncio.gather(*[initiate(clients_to_move[i]) for i in due])
            after = loop.time()
            for i in due:
                last_attempt[i] = after
                reissued[i] = True

        await asyncio.sleep(poll_interval)


class TogetherOutcome(enum.Enum):
    """What ensure_together found, and did."""

    #: Every player already shared one zone, realm and instance; nobody moved.
    ALREADY = "already"
    #: Some did not, and were ported to the leader, and are now seen with it.
    GATHERED = "gathered"
    #: Some did not, and could not be brought to the leader.
    FAILED = "failed"


async def ensure_together(machine, clients: list, *, where: str) -> TogetherOutcome:
    """Make sure every player shares one zone, realm and instance, porting stragglers to the leader.

    For a bot whose ``Player Realms`` is Together (#132); a Separate bot always answers
    ALREADY, since its players are meant to be apart. The machine's record answers first, so
    a group seen together since anyone last loaded costs nothing. Otherwise one check decides:
    everyone the leader (sightings.choose_leader) does not see -- Apart, and Unknown too, since
    a player too far away to tell may be on another realm -- ports to it through
    gather_at_player, which confirms by sight that they arrived.

    *where* names the moment in the log ("on the sigil", "before the fight"). Logs its own
    reasons, players by number.
    """
    if len(clients) < 2 or player_realms.machine_is_separate(machine) or machine.are_together(clients):
        return TogetherOutcome.ALREADY
    check = await read_sightings(clients, machine)
    if check.all_together():
        return TogetherOutcome.ALREADY

    leader = sightings.choose_leader(check)
    group = check.group_of(leader)
    movers = [i for i in range(len(clients)) if i not in group]
    machine.log(
        f"Player(s) {[i + 1 for i in movers]} are not with P{leader + 1} {where} "
        f"({player_realms.OPTION} is {player_realms.TOGETHER}); porting them to it",
        logging.WARNING,
    )
    name = wizard_names.cached_name(clients[leader])
    if not name:
        machine.log(f"FAILURE: P{leader + 1}'s wizard name has not been detected yet", logging.WARNING)
        return TogetherOutcome.FAILED
    if not await gather_at_player(machine, clients, leader, name, only=movers):
        return TogetherOutcome.FAILED
    return TogetherOutcome.GATHERED


async def _sees_each_other(a, b) -> bool:
    """Whether a check proves *a* and *b* share a zone, realm and instance."""
    return (await read_sightings([a, b])).pair(0, 1) is sightings.Sight.TOGETHER


async def gather_at_player(
    machine,
    clients: list,
    target_index: int,
    target_name: str,
    *,
    poll_interval: float = POLL_INTERVAL,
    only: Collection[int] | None = None,
) -> bool:
    """Port every client but `clients[target_index]` onto that player, and confirm they are with it.

    `only` narrows the movers to those indices, for a caller that knows the rest are already
    with the target; players keep their numbers in the log either way.

    The hooked-player half of teleport_to_friend.
    Because the target is one of ours, its zone and position are readable, which makes this
    exact where porting to a friend by name is not:

    * Nobody is skipped for being in the target's zone, or even beside it. Neither can see
      realm -- two players at the same login spawn on different realms read identically -- and
      a port within the same zone and realm costs no loading screen, so it is cheaper to send
      it than to be wrong about skipping it.
    * A mover already in the target's zone within POSITION_MARGIN of it is the one case whose
      port shows nothing, so it is the only one passed to port_clients as `maybe_there`.
    * Arrival is every mover in the target's zone, within POSITION_MARGIN of it, and seen with
      it (#130): standing beside the target proves the zone, and only the sighting proves the
      realm. A mover presumed already there has to be seen too (port_clients' `target`).

    Logs its own reasons, naming players by number and never by name, and returns False on any
    failure: a player still loading, a friend not found, a refusal, a port that never showed
    itself, or a mover that did not end up with the target.
    """
    target = clients[target_index]
    target_no = target_index + 1
    moving = [i for i in range(len(clients)) if i != target_index and (only is None or i in only)]
    numbers = [i + 1 for i in moving]
    movers = [clients[i] for i in moving]
    if not movers:
        machine.log(f"No other players to port to P{target_no}")
        return True

    # Everything below reads zones and positions, and a loading client reports the ones it left.
    if not await poll_until(lambda: none_loading(clients), ZONE_CHANGE_TIMEOUT, poll_interval):
        machine.log("FAILURE: a player is still on a loading screen, so no position can be trusted", logging.WARNING)
        return False

    target_zone, target_pos = await asyncio.gather(target.zone_name(), _position_or_none(target))
    if target_pos is None:
        machine.log(f"FAILURE: P{target_no}'s position could not be read", logging.WARNING)
        return False
    zones = await asyncio.gather(*[c.zone_name() for c in movers])
    positions = await asyncio.gather(*[_position_or_none(c) for c in movers])
    beside = {
        i
        for i, (zone, pos) in enumerate(zip(zones, positions))
        if zone == target_zone and pos is not None and distance(pos, target_pos) <= POSITION_MARGIN
    }
    machine.log(
        f"Porting player(s) {numbers} to P{target_no}; already beside it: {[numbers[i] for i in sorted(beside)]}",
        logging.DEBUG,
    )

    try:
        result = await port_to_friend(
            movers,
            target_name.lower(),
            trigger_timeout=TRIGGER_TIMEOUT,
            ceiling=ZONE_CHANGE_TIMEOUT,
            poll_interval=poll_interval,
            maybe_there=beside,
            target=target,
        )
    except ValueError:
        # The message names the player it could not find, so it stays out of the log.
        machine.log(f"FAILURE: P{target_no} is not on the online friends list of every player", logging.WARNING)
        return False

    if result.refusal:
        machine.log(
            f"FAILURE: P{numbers[result.refused_index]} cannot reach P{target_no} — "
            f"{dialog_captions.describe(result.refusal)}",
            logging.WARNING,
        )
        return False
    if missing := result.missing(len(movers)):
        machine.log(
            f"FAILURE: player(s) {[numbers[i] for i in missing]} showed no sign of porting within "
            f"{ZONE_CHANGE_TIMEOUT:.0f}s",
            logging.WARNING,
        )
        return False
    for i in sorted(result.presumed):
        machine.log(f"P{numbers[i]} neither loaded nor moved; it was already beside P{target_no}")

    await poll_until(lambda: none_loading(movers), None, poll_interval)

    if await poll_until(lambda: _all_beside(machine, target, movers), ZONE_CHANGE_TIMEOUT, poll_interval):
        return True
    target_zone = await target.zone_name()
    zones = await asyncio.gather(*[c.zone_name() for c in movers])
    if all(z == target_zone for z in zones):
        machine.log(
            f"FAILURE: not every player ended up with P{target_no}: all are in its zone, but not all "
            "beside it and on its realm",
            logging.WARNING,
        )
        return False
    machine.log(
        f"FAILURE: not every player ended up with P{target_no} (its zone {target_zone!r}, theirs {sorted(set(zones))})",
        logging.WARNING,
    )
    return False


async def _all_beside(machine, target, movers: list) -> bool:
    """Whether every mover is in the target's zone, within POSITION_MARGIN of it, and seen with it."""
    target_zone, target_pos = await asyncio.gather(target.zone_name(), _position_or_none(target))
    if target_pos is None:
        return False
    zones = await asyncio.gather(*[c.zone_name() for c in movers])
    positions = await asyncio.gather(*[_position_or_none(c) for c in movers])
    if not all(
        zone == target_zone and pos is not None and distance(pos, target_pos) <= POSITION_MARGIN
        for zone, pos in zip(zones, positions)
    ):
        return False
    return (await read_sightings([target, *movers], machine)).all_together()


async def open_friend_profile(client, name: str):
    """Open a friend's profile popup from the friends list and return the wndCharacter window.

    This is the first half of WizWalker's teleport_to_friend_from_list — it locates the
    friend, paginates to their row and clicks it, but stops at the profile popup instead of
    going on to teleport. That popup carries the per-player action buttons (go to friend,
    add/remove friend, add to group).

    `name` is matched case-insensitively. WizWalker's own lookup compares names with ==,
    so the exact casing is resolved here first, from the list text this function already
    reads — no extra UI work. Without that, a name whose casing differs even slightly from
    the friends list (e.g. one detected from the character page) silently fails to match.

    Raises ValueError if no friends are online or the name is not in the list.
    """
    from wizwalker.extensions.scripting.utils import (  # noqa: PLC0415
        _click_on_friend,
        _cycle_friends_list,
        _cycle_to_online_friends,
        _friend_list_entry,
        _maybe_get_named_window,
    )

    friends_window = await open_friends_list(client)
    await _cycle_to_online_friends(client, friends_window)

    friends_list = await _maybe_get_named_window(friends_window, "listFriends")
    list_text = await friends_list.maybe_text()
    if not list_text:
        raise ValueError("No friends online")

    wanted = name.lower()
    exact_name = next(
        (e.group("name") for e in _friend_list_entry.finditer(list_text) if e.group("name").lower() == wanted),
        name,
    )
    if exact_name != name:
        logger.debug("Resolved the friend's name to its spelling in the friends list")

    right_button = await _maybe_get_named_window(friends_window, "btnArrowDown")
    page_number = await _maybe_get_named_window(friends_window, "PageNumber")
    page_text = await page_number.maybe_text()
    current_page, _ = map(
        int,
        page_text.replace("<center>", "").replace("</center>", "").replace(" ", "").split("/"),
    )

    friend, friend_index = await _cycle_friends_list(
        client, right_button, friends_list, None, None, exact_name, current_page
    )
    if friend is None:
        # The name is not quoted: callers log this message, and a wizard name must not reach a log.
        raise ValueError("Could not find friend in the online friends list")

    await _click_on_friend(client, friends_list, friend_index)
    return await _maybe_get_named_window(client.root_window, "wndCharacter")


# ---------------------------------------------------------------------------
# Reaching a prompt
# ---------------------------------------------------------------------------


def _client_numbers(clients: list, numbers: list[int] | None) -> list[int]:
    return list(numbers) if numbers is not None else list(range(1, len(clients) + 1))


async def clear_stale_prompt(
    machine,
    clients: list,
    reset,
    prompt: PromptKind,
    safety_delay: float,
    poll_interval: float = POLL_INTERVAL,
    numbers: list[int] | None = None,
) -> bool:
    """Stand every client at *reset* until no *prompt* is showing, before this run raises one.

    A prompt that is not this run's has the client press X on whatever raised it. It may
    be one left over from a previous interaction, or one still on its way: a client just
    off a loading screen beside another object raises that object's prompt a moment
    later, so a prompt reading absent proves nothing on its own (#152). So every client
    teleports to *reset* whether or not a prompt shows, and the prompt must stay absent
    there for PROMPT_CLEAR_SETTLE — see ``settle_prompt_clear``.

    Returns False, having logged the node's failure, if a prompt still shows after
    ``safety_delay``: *reset* is then in range of something, and approaching from it
    would carry that prompt along. *numbers* labels the clients in the log, 1-based, for
    a caller working on a subset of the party.
    """
    labels = _client_numbers(clients, numbers)
    showing = await asyncio.gather(*[prompt.showing(c) for c in clients])
    if any(showing):
        stale = [n for n, s in zip(labels, showing) if s]
        machine.log(
            f"Pre-existing {prompt.name} on client(s) {stale}; teleporting to reset_xyz to clear it",
            logging.DEBUG,
        )
    await asyncio.gather(*[c.teleport(reset) for c in clients])
    still = await settle_prompt_clear(clients, prompt, safety_delay, poll_interval)
    if not any(still):
        return True
    machine.log(
        f"FAILURE: {prompt.name} would not clear at reset_xyz on client(s) "
        f"{[n for n, s in zip(labels, still) if s]} within {safety_delay:.1f}s — reset_xyz is in range of "
        "something, so approaching from it could interact with the wrong object",
        logging.WARNING,
    )
    return False


async def settle_prompt_clear(
    clients: list, prompt: PromptKind, timeout: float, poll_interval: float = POLL_INTERVAL
) -> list[bool]:
    """Wait for *prompt* to read absent on every client for PROMPT_CLEAR_SETTLE in a row.

    The clients must already be standing out of range. Returns, per client, whether the
    prompt still showed when *timeout* ran out — all False once it has settled. The
    settle is counted in consecutive reads, at least two, so a prompt appearing between
    reads restarts it.
    """
    needed = max(2, 1 + math.ceil(PROMPT_CLEAR_SETTLE / poll_interval)) if poll_interval > 0 else 2
    streak = 0

    async def _settled() -> bool:
        nonlocal streak
        streak = streak + 1 if await prompt.none_showing(clients) else 0
        return streak >= needed

    if await poll_until(_settled, timeout + PROMPT_CLEAR_SETTLE, poll_interval):
        return [False] * len(clients)
    return list(await asyncio.gather(*[prompt.showing(c) for c in clients]))


async def reach_prompt(
    machine,
    clients: list,
    point,
    reset,
    prompt: PromptKind,
    safety_delay: float,
    poll_interval: float = POLL_INTERVAL,
    *,
    numbers: list[int] | None = None,
    fail_on_miss: bool = True,
    clear_first: bool = True,
) -> bool:
    """Get every client standing at *point* with *prompt* showing.

    The routine every node that resets and retries shares, up to the point the prompt
    is on screen:

      1. Stand at *reset* until no prompt shows — see ``clear_stale_prompt``. A prompt
         that will not clear there is always the node's failure, logged as one.
      2. Up to MAX_RETRIES: teleport to *point* and wait ``safety_delay`` for the
         prompt on every client; on a miss, teleport back to *reset* and try again.

    Returns False once the retries run out. With *fail_on_miss*, the default, that is
    logged as the node's failure, so a caller's failure path is a bare ``return
    "FAILURE"``; a caller that carries on with whichever clients did get the prompt
    passes False and logs its own outcome, and finds every client still at *point*
    rather than reset after the last attempt. Such a caller must tell a miss from step 1
    failing, so it runs ``clear_stale_prompt`` itself and passes *clear_first* False.
    Logs under the running node, since it is called from one — see ``machine.log`` in
    the node conventions.
    """
    if clear_first and not await clear_stale_prompt(
        machine, clients, reset, prompt, safety_delay, poll_interval, numbers
    ):
        return False

    for attempt in range(1, MAX_RETRIES + 1):
        machine.log(f"Attempt {attempt}/{MAX_RETRIES}: teleporting to the {prompt.name}", logging.DEBUG)
        await asyncio.gather(*[c.teleport(point) for c in clients])

        machine.log(f"Waiting for {prompt.name}…", logging.DEBUG)
        if await poll_until(lambda: prompt.all_showing(clients), safety_delay, poll_interval):
            return True

        if attempt == MAX_RETRIES and not fail_on_miss:
            # The caller presses X for whichever clients got the prompt, so they stay at
            # *point*: back at *reset* they would read no prompt, or press X on another's.
            machine.log(f"{prompt.name.capitalize()} did not appear on every client (attempt {attempt})", logging.DEBUG)
            break
        machine.log(f"{prompt.name.capitalize()} did not appear (attempt {attempt}); resetting", logging.DEBUG)
        await asyncio.gather(*[c.teleport(reset) for c in clients])

    if fail_on_miss:
        machine.log(f"FAILURE: {prompt.name} never appeared after {MAX_RETRIES} attempts", logging.WARNING)
    return False


# ---------------------------------------------------------------------------
# Dungeon sigils
# ---------------------------------------------------------------------------


async def reach_sigil_prompt(
    machine,
    clients: list,
    from_zone: str,
    sigil,
    reset,
    safety_delay: float,
    poll_interval: float = POLL_INTERVAL,
) -> bool:
    """Get every client standing on a dungeon sigil with its entrance prompt showing.

    The half `enter_dungeon` shares whichever way it is entered — normal or Team-Up —
    which is the whole of the node up to the point the prompt is on screen: verify
    every client is in *from_zone*, then ``reach_prompt`` for the entrance prompt.

    Returns False having already logged the reason, so a caller's failure path is a
    bare ``return "FAILURE"``.
    """
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    if not all(zone_matches(z, from_zone) for z in zones):
        wrong = [z for z in zones if not zone_matches(z, from_zone)]
        machine.log(
            f"FAILURE: clients not in expected zone (expected={from_zone!r}, unexpected={wrong})",
            logging.WARNING,
        )
        return False

    return await reach_prompt(machine, clients, sigil, reset, DUNGEON_ENTRANCE_PROMPT, safety_delay, poll_interval)
