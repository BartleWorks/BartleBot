"""Shared async helpers for node implementations."""

import asyncio
import fnmatch
import logging
import re
from typing import NamedTuple

from wizwalker import Keycode, MemoryReadError
from wizwalker.extensions.scripting import teleport_to_friend_from_list

logger = logging.getLogger(__name__)

from bartlebot import dialog_captions
from bartlebot.nodes.paths import (  # noqa: F401 — re-exported for callers
    ADVANCE_DIALOG_PATH,
    DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH,
    FRIENDS_LIST_WINDOW_NAME,
    FRIENDS_LIST_WINDOW_NAMES,
    GAUNTLET_ENTRANCE_PATH,
    HUB_BUTTON_PATH,
    LARGE_FRIENDS_LIST_WINDOW_NAME,
    MESSAGE_BOX_CAPTION_PATH,
    MESSAGE_BOX_CENTER,
    MESSAGE_BOX_RIGHT,
    TELEPORT_TIMER_PATH,
)
from bartlebot.nodes.timing import (  # noqa: F401 — re-exported for callers
    DIALOG_FAST_POLL_INTERVAL,
    DIALOG_TIMEOUT,
    MAX_RETRIES,
    OBJECT_USE_DELAY,
    POLL_INTERVAL,
    TRIGGER_TIMEOUT,
    WINDOW_CLOSE_CONFIRM_INTERVAL,
    WINDOW_CLOSE_RETRY_INTERVAL,
    WINDOW_CLOSE_TIMEOUT,
    ZONE_CHANGE_TIMEOUT,
    ZONE_SETTLE_GRACE,
)


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


async def dismiss_ambient_dialog_once(client) -> bool:
    """If client has an advanceable dialog open, press SPACEBAR to dismiss it. Returns True if dismissed."""
    if await is_visible_by_path(client, ADVANCE_DIALOG_PATH):
        await client.send_key(Keycode.SPACEBAR, 0.1)
        return True
    return False


async def advance_dialogue_until_clear(
    clients: list,
    safety_delay: float,
    timeout: float | None,
) -> bool:
    """Advance dialogue on all clients until all are dialogue-free for safety_delay seconds.

    Presses SPACEBAR on any client that has an open dialogue banner, then resets the quiet
    timer. Polls at DIALOG_FAST_POLL_INTERVAL after a dismissal and POLL_INTERVAL during the
    quiet phase (no dialogue detected). Returns True when all clients have been dialogue-free
    for safety_delay seconds. Returns False if timeout expires while clients are still in
    dialogue.
    """
    loop = asyncio.get_event_loop()
    overall_start = loop.time()
    quiet_since = overall_start

    while True:
        now = loop.time()
        dialogs = await asyncio.gather(*[is_visible_by_path(c, ADVANCE_DIALOG_PATH) for c in clients])

        if not any(dialogs) and (now - quiet_since) >= safety_delay:
            return True

        if timeout is not None and (now - overall_start) >= timeout:
            return False

        if any(dialogs):
            await asyncio.gather(*[c.send_key(Keycode.SPACEBAR, 0.1) for c, d in zip(clients, dialogs) if d])
            quiet_since = loop.time()
            await asyncio.sleep(DIALOG_FAST_POLL_INTERVAL)
        else:
            await asyncio.sleep(POLL_INTERVAL)


async def identify_modal(client) -> str | None:
    """Return the lang key of the modal currently on screen, or None.

    Every in-game modal is the same MessageBoxModalWindow tree, so "a modal is up" says
    nothing about which one it is. Its caption does: see bartlebot.dialog_captions for the
    key constants to compare against (INSTANCE_EXIT, FRIEND_BUSY, …) and for how the match
    survives markup, substitution slots, and all seven locales.

    None means *no recognised dialog* — either nothing is showing, or something the
    snapshot has not seen. The two are deliberately not distinguished: callers should have
    a button-based fallback for both, so a caption we do not know degrades to the previous
    behaviour rather than hanging. Use modal_showing() to tell them apart when it matters.
    """
    return dialog_captions.identify_caption(await get_text_by_path(client, MESSAGE_BOX_CAPTION_PATH))


async def modal_showing(client) -> bool:
    """Return True when any modal message box is on screen, recognised or not.

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


async def resolve_modal(client) -> ModalOutcome:
    """Answer whatever modal is on screen.

    The one call a node needs while waiting for something the game may be holding behind a
    dialog. Three outcomes:

      * a question (dialog_captions.CONFIRM_TO_PROCEED) — confirmed, so the node's wait
        carries on and will now succeed;
      * a refusal (dialog_captions.REFUSALS) — dismissed, and returned, so the node can
        fail immediately: the game has said no, and waiting out a timeout only delays the
        same answer while reporting it as the wrong cause;
      * nothing recognised — falls back to the button layout, which still separates the
        two kinds: a centre button offers a way to proceed, so it is confirmed; a right
        button with no centre offers only a way out, so it is dismissed and reported as
        dialog_captions.UNRECOGNISED_REFUSAL. Without that second half a dialog reworded
        by a game patch would stop failing fast and start timing out instead.

    Refusals are dismissed with the right button where it exists and the centre otherwise:
    a one-button refusal puts OK in the centre, and a two-button one puts cancel on the
    right, which is the answer that leaves the game alone.
    """
    key = await identify_modal(client)

    if key is None:
        if await is_visible_by_path(client, MESSAGE_BOX_CENTER):
            await click_by_path(client, MESSAGE_BOX_CENTER)
            return ModalOutcome(answered=True)
        if await is_visible_by_path(client, MESSAGE_BOX_RIGHT):
            await click_by_path(client, MESSAGE_BOX_RIGHT)
            return ModalOutcome(answered=True, refusal=dialog_captions.UNRECOGNISED_REFUSAL)
        return _NOTHING

    if key in dialog_captions.CONFIRM_TO_PROCEED:
        await click_by_path(client, MESSAGE_BOX_CENTER)
        return ModalOutcome(answered=True)

    if key in dialog_captions.REFUSALS:
        if await is_visible_by_path(client, MESSAGE_BOX_RIGHT):
            await click_by_path(client, MESSAGE_BOX_RIGHT)
        else:
            await click_by_path(client, MESSAGE_BOX_CENTER)
        return ModalOutcome(answered=True, refusal=key)

    # Recognised, but somebody else's dialog — leave it alone rather than click blind.
    logger.debug("resolve_modal: leaving %s to its own handler", key)
    return _NOTHING


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

    def __init__(self, action_fn):
        self._action_fn = action_fn
        self.refusal: str | None = None

    async def __call__(self, client) -> None:
        outcome = await resolve_modal(client)
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
    either serialises nothing useful or holds the object long after it is free.

    `park_after_turn` sends each client back to `reset_xyz` once it is done, so the object
    is genuinely free for the next one. Leave it on for interactions the client walks away
    from — standing in range is enough to swallow the next client's keypress, so a client
    left at the object undoes the serialisation for everyone after it. Turn it off only
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

    # Clear everyone off the object first. Unconditional, unlike the parallel path's
    # stale-prompt check: sequential mode is only worth anything if exactly one client is
    # in range at a time, and a client that starts in range never leaves it on its own.
    await asyncio.gather(*[c.teleport(reset_xyz) for c in clients])
    await poll_until(lambda: none_in_npc_range(clients), safety_delay, poll_interval)

    for position, client in enumerate(clients):
        if position:
            await asyncio.sleep(gap)

        number = labels[position]
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

      * No metacharacters — case-insensitive *substring* match, which is what
        WizSprinter's get_base_entities_with_vague_name() does. "Cyclops" finds
        "Cyclops/CyclopsMinion". Every existing bot file relies on this.
      * With metacharacters — case-insensitive glob against the *whole* name, so
        "*Bandit*" matches anywhere but "Bandit*" only matches names that start
        with it. Anchoring is the point: substring matching alone cannot express
        "starts with", and a pattern that silently meant the same thing either
        way would make the wildcard decorative.

    A malformed glob does not raise: fnmatch treats an unbalanced bracket as a
    literal character, so "[unclosed" simply matches names containing that text
    (anchored, like any other pattern). No fallback is needed.
    """
    if actual is None:
        return False
    if has_glob(pattern):
        return fnmatch.fnmatch(actual.lower(), pattern.lower())
    return pattern.lower() in actual.lower()


def entity_name_predicate(pattern: str):
    """Build an async predicate matching entities against pattern by template name.

    Suitable for WizSprinter's find_closest_by_predicate() and
    get_base_entities_with_predicate().
    """

    async def _matches(entity) -> bool:
        # An entity can despawn mid-scan; WizSprinter's own vague-name predicate
        # would propagate that and abandon the whole search, so skip it instead.
        try:
            template = await entity.object_template()
            if template is None:
                return False
            return entity_name_matches(await template.object_name(), pattern)
        except (ValueError, MemoryReadError):
            return False

    return _matches


async def find_closest_entity_by_name(client, pattern: str):
    """Return the closest entity whose template name matches pattern, or None.

    Plain names delegate to WizSprinter's find_closest_by_vague_name() so the
    long-standing path is untouched; only a pattern carrying metacharacters takes
    the predicate route, which is the same search with our matcher in place of
    WizSprinter's hardcoded substring test.
    """
    if not has_glob(pattern):
        return await client.find_closest_by_vague_name(pattern)
    return await client.find_closest_by_predicate(entity_name_predicate(pattern))


async def find_entities_by_name(client, pattern: str) -> list:
    """Return every entity whose template name matches pattern.

    The list counterpart of find_closest_entity_by_name(), with the same split:
    plain names go through WizSprinter untouched, wildcards through our matcher.
    """
    if not has_glob(pattern):
        return await client.get_base_entities_with_vague_name(pattern)
    return await client.get_base_entities_with_predicate(entity_name_predicate(pattern))


def zone_matches(actual: str, pattern: str) -> bool:
    """Return True when actual matches pattern.

    If pattern contains glob metacharacters (* ? [), fnmatch is used so callers
    can write patterns like "WizardCity/*" to match any WizardCity sub-zone.
    Plain strings (no metacharacters) fall through to exact equality, preserving
    the previous behaviour for all existing bot files.

    A malformed glob does not raise: fnmatch treats an unbalanced bracket as a
    literal character, so "[unclosed" matches the zone of that exact name and
    nothing else. No fallback is needed.
    """
    if actual is None:
        return False
    if has_glob(pattern):
        matched = fnmatch.fnmatchcase(actual, pattern)
        if matched:
            logger.debug("zone_matches: pattern %r matched actual zone %r (wildcard)", pattern, actual)
        return matched
    return actual == pattern


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
    stragglers are ported to it via the same mechanics teleport_to_player_if_in_zone offers
    as a standalone node — cheaper than retrying the whole transition from a world hub. All
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

    Returns None on success (whether or not recovery was needed), or the list of actual
    zone names if the deadline passed with some clients still not in `zone`.
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
        arriving = any(await asyncio.gather(*[c.is_loading() for c in behind]))
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


async def _attempt_zone_recovery(
    machine, clients: list, zone: str, zones: list[str], poll_interval: float = POLL_INTERVAL
) -> None:
    """Best-effort teleport-to-player recovery for wait_for_zone_with_recovery.

    Ports whichever clients are not yet in `zone` (per the already-read `zones` snapshot) to
    whichever hooked client already is. Never raises and does not itself report success —
    the caller's own polling loop re-reads zones right after this returns and decides that.
    A recovery that cannot even get started (no client's name cached yet, no friends online,
    the friend-busy dialog, …) is logged and left for the caller's normal timeout to report.
    """
    from bartlebot.nodes.teleportation import teleport_to_player_if_in_zone  # noqa: PLC0415 — avoid a circular import

    target = await teleport_to_player_if_in_zone.find_target_in_zone(clients, zone, zones=zones)
    if target.error:
        machine.log(f"Recovery not possible: {target.error}", logging.DEBUG)
        return

    stragglers = [c for i, c in enumerate(clients) if i != target.index and not zone_matches(zones[i], zone)]
    if not stragglers:
        return

    machine.log(
        f"Some clients did not reach {zone!r} but at least one did; attempting a "
        f"teleport-to-player recovery for {len(stragglers)} client(s)…",
        logging.INFO,
    )
    try:
        result = await port_to_friend(stragglers, target.name.lower(), poll_interval=poll_interval)
    except ValueError as exc:
        machine.log(f"Recovery teleport failed: {exc}", logging.WARNING)
        return

    if result.refusal:
        machine.log(f"Recovery teleport refused — {dialog_captions.describe(result.refusal)}", logging.WARNING)
        return
    if len(result.seen_loading) < len(stragglers):
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

    Returns False if the panel is still open when the timeout expires.
    """
    if is_open is None:

        async def is_open():
            return await is_visible_by_path(client, path)

    async def _gone():
        return not await is_open()

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        if await _gone():
            return True
        await click_by_path(client, path)
        if loop.time() >= deadline:
            break
        if await poll_until(_gone, timeout=poll_interval, poll_interval=WINDOW_CLOSE_CONFIRM_INTERVAL):
            return True

    still_open = await is_open()
    if still_open:
        logger.warning("Window closed via %s is still open after %.0fs of retries", "/".join(path), timeout)
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

    raise ValueError(f"Could not find friend matching {friend_name_lower!r} in online friends list")


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


class PortToFriendResult(NamedTuple):
    """Outcome of port_to_friend()'s loading-detection loop.

    `seen_loading` holds the indices (into the clients_to_move list a caller passed in) that
    entered a loading screen before the ceiling. `refusal` is set when the game refused the
    port outright for one client — pair it with dialog_captions.describe(); `refused_index`
    says which client (again by position in clients_to_move) hit it.
    """

    seen_loading: set[int]
    refusal: str | None = None
    refused_index: int | None = None


async def port_to_friend(
    clients_to_move: list,
    friend_name_lower: str,
    *,
    trigger_timeout: float = TRIGGER_TIMEOUT,
    ceiling: float = ZONE_CHANGE_TIMEOUT,
    poll_interval: float = POLL_INTERVAL,
) -> PortToFriendResult:
    """Teleport clients_to_move to friend_name_lower via the friends list, and wait for each
    to enter a loading screen.

    The mechanics shared by every node that ports players to a friend — teleport_to_friend,
    teleport_to_player_if_in_zone, and node_helpers' own zone-arrival recovery: initiate,
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
    )


async def port_clients(
    clients_to_move: list,
    initiate,
    *,
    trigger_timeout: float = TRIGGER_TIMEOUT,
    ceiling: float = ZONE_CHANGE_TIMEOUT,
    poll_interval: float = POLL_INTERVAL,
) -> PortToFriendResult:
    """Start a port on every client with `initiate`, and wait for each to enter a loading screen.

    `initiate(client)` is how one client's port is started, called once for everyone and again
    for each straggler after `trigger_timeout`: the friends list for port_to_friend, the group
    roster for group_manager.port_to_group_member. Everything after the start is the same
    whichever way it began -- the dungeon exit and cross-realm warnings are confirmed, a
    friend-busy dialog is a refusal -- so it lives here once. See port_to_friend for the rest.

    Raises whatever `initiate` raises; both starters raise ValueError when they cannot begin.
    """
    await asyncio.gather(*[initiate(c) for c in clients_to_move])

    seen_loading: set[int] = set()
    loop_start = asyncio.get_event_loop().time()
    straggler_after = loop_start + trigger_timeout

    while True:
        for i, c in enumerate(clients_to_move):
            if i in seen_loading:
                continue
            outcome = await resolve_modal(c)
            if outcome.refusal:
                return PortToFriendResult(seen_loading, outcome.refusal, i)

        loading_flags = await asyncio.gather(*[c.is_loading() for c in clients_to_move])
        for i, is_now_loading in enumerate(loading_flags):
            if is_now_loading:
                seen_loading.add(i)

        if len(seen_loading) == len(clients_to_move):
            return PortToFriendResult(seen_loading)

        now = asyncio.get_event_loop().time()
        if now - loop_start > ceiling:
            return PortToFriendResult(seen_loading)

        if now >= straggler_after:
            stragglers = [c for i, c in enumerate(clients_to_move) if i not in seen_loading]
            await asyncio.gather(*[initiate(c) for c in stragglers])

        await asyncio.sleep(poll_interval)


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
        logger.debug("Resolved friend name %r to %r from the friends list", name, exact_name)

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
        raise ValueError(f"Could not find friend {exact_name!r} in online friends list")

    await _click_on_friend(client, friends_list, friend_index)
    return await _maybe_get_named_window(client.root_window, "wndCharacter")


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
    which is the whole of the node up to the point the prompt is on screen:

      1. Verify every client is in *from_zone*.
      2. Clear a pre-existing prompt by teleporting to *reset*, so a stale one is not
         mistaken for the one this run raised.
      3. Up to MAX_RETRIES: teleport to *sigil* and wait ``safety_delay`` for the
         prompt; on a miss, teleport back to *reset* and try again.

    Returns False having already logged the reason, so a caller's failure path is a
    bare ``return "FAILURE"``. Logs under the running node, since it is called from
    one — see ``machine.log`` in the node conventions.
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

    prompts = await asyncio.gather(*[is_visible_by_path(c, DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH) for c in clients])
    if any(prompts):
        stale = [i for i, p in enumerate(prompts) if p]
        machine.log(
            f"Pre-existing dungeon entrance prompt on client(s) {stale}; teleporting to reset_xyz to clear it",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(reset) for c in clients])
        await poll_until(lambda: none_at_dungeon_entrance(clients), safety_delay, poll_interval)

    for attempt in range(1, MAX_RETRIES + 1):
        machine.log(f"Attempt {attempt}/{MAX_RETRIES}: teleporting clients to sigil", logging.DEBUG)
        await asyncio.gather(*[c.teleport(sigil) for c in clients])

        machine.log("Waiting for dungeon entrance prompt…", logging.DEBUG)
        if await poll_until(lambda: all_at_dungeon_entrance(clients), safety_delay, poll_interval):
            return True

        machine.log(
            f"Entrance prompt did not appear (attempt {attempt}); resetting to reset_xyz",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(reset) for c in clients])

    machine.log(
        f"FAILURE: entrance prompt never appeared after {MAX_RETRIES} attempts",
        logging.WARNING,
    )
    return False
