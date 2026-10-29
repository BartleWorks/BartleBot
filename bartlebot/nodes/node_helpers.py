"""Shared async helpers for node implementations.

The ones that need no bot, the game's own windows, dialogue, the friends list and porting,
are bartleby-core's (``bartleby.runtime.actions.node_helpers``), and this module imports
them, so the nodes and the rest of BartleBot reach every helper here as before. What stays
is BartleBot's: zones, the run's prompts, recovery and gathering the group.
"""

import asyncio
import enum
import fnmatch
import logging
import math
import re
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from typing import NamedTuple

from bartleby.runtime.clients import game_keys
from bartleby.runtime.game.key_bindings import GameAction
from wizwalker import ExceptionalTimeout, MemoryReadError

logger = logging.getLogger(__name__)

from bartleby.runtime.actions.node_helpers import (  # noqa: F401 — re-exported for callers
    _NOTHING,
    TRIGGER_MARGIN,
    ModalOutcome,
    PortToFriendResult,
    ProfileLayout,
    ShopClosure,
    _click_go_to_friend,
    _entity_gids,
    _get_window_from_path,
    _or_none,
    _position_or_none,
    advance_dialog_once,
    advance_dialogue_until_clear,
    await_settled_friend_profile,
    click_by_path,
    click_until_dialog_gone,
    click_until_gone,
    click_window,
    close_crowns_shop,
    close_friend_profile,
    close_friends_list,
    diagnose_path,
    distance,
    find_windows_by_path,
    get_text_by_path,
    identify_modal,
    is_enabled_by_path,
    is_friends_list_open,
    is_named_window_visible,
    is_visible_by_path,
    iter_windows_by_path,
    list_online_friends,
    modal_showing,
    none_loading,
    open_friend_profile,
    open_friends_list,
    poll_until,
    port_clients,
    port_to_friend,
    press_key_until_gone,
    profile_button_rect,
    profile_layout_text,
    read_friend_profile,
    read_sightings,
    rect_text,
    resolve_friend_name,
    resolve_modal,
    resolve_path_match,
    strip_html,
    teleport_via_friends_list,
    window_text_matches,
)
from bartleby.runtime.clients import sightings
from bartleby.runtime.game import dialog_captions, wizard_names
from bartleby.runtime.paths import (  # noqa: F401 — re-exported for callers
    ADVANCE_DIALOG_PATH,
    BACKPACK_CLOSE_BUTTON_PATH,
    CHARACTER_PROFILE_CLOSE_PATH,
    CHARACTER_PROFILE_PATH,
    CROWNS_SHOP_WINDOW_PATH,
    DECK_CONFIGURATION_PATH,
    DECLINE_QUEST_PATH,
    DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH,
    FRIENDS_LIST_BUTTON_PATH,
    FRIENDS_LIST_PATH,
    FRIENDS_LIST_PATHS,
    GAUNTLET_ENTRANCE_PATH,
    GO_TO_FRIEND_BUTTON_PATH,
    HUB_BUTTON_PATH,
    LARGE_FRIENDS_LIST_PATH,
    MESSAGE_BOX_CAPTION_PATH,
    MESSAGE_BOX_CENTER,
    MESSAGE_BOX_LEFT,
    MESSAGE_BOX_RIGHT,
    TELEPORT_TIMER_PATH,
)
from bartleby.runtime.timing import (  # noqa: F401 — re-exported for callers
    DIALOG_FAST_POLL_INTERVAL,
    DIALOG_PAGE_SETTLE,
    DIALOG_TIMEOUT,
    FRIEND_PROFILE_SETTLE_TIMEOUT,
    FRIEND_PROFILE_TIMEOUT,
    FRIEND_TELEPORT_ATTEMPTS,
    FRIEND_TELEPORT_CONFIRM_TIMEOUT,
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
    ZONE_READABLE_TIMEOUT,
    ZONE_SETTLE_GRACE,
)

from bartlebot.game import zone_names
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


class Pickup(enum.Enum):
    """How one attempt at picking up an entity in the world ended."""

    #: The prompt showed, X was pressed, and the prompt went away.
    COLLECTED = "collected"
    #: Standing on the entity brought up no interact prompt.
    NO_PROMPT = "no prompt"
    #: X was pressed, and the prompt stayed.
    NOT_DISMISSED = "not dismissed"


async def pick_up(client, xyz, safety_delay: float, poll_interval: float = POLL_INTERVAL) -> Pickup:
    """Teleport *client* onto *xyz*, press Interact at the prompt there, and wait for the prompt to clear.

    The step collect_reagent and collect_item share. Each waits *safety_delay* for the prompt
    and again for it to clear. The caller reads *xyz* from the entity, since a stale entity
    is its own case to log.
    """
    await client.teleport(xyz)
    if not await poll_until(lambda: all_in_npc_range([client]), safety_delay, poll_interval):
        return Pickup.NO_PROMPT
    await game_keys.press(client, GameAction.INTERACT, 0.1)
    if not await poll_until(lambda: none_in_npc_range([client]), safety_delay, poll_interval):
        return Pickup.NOT_DISMISSED
    return Pickup.COLLECTED


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

    An unreadable (None) zone counts as left, on purpose. Every caller asks this after the
    trigger has fired, and a zone that goes unreadable then is the old zone being torn down.
    Waiting for the new one instead would outlast the Safety Delay these checks run under,
    and the client would be reset as stuck while it was on its way (#70). A client that is
    logged out reads None too, and is the health monitor's to catch.
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


async def read_zones(
    clients: list, timeout: float | None = None, poll_interval: float = POLL_INTERVAL
) -> list[str | None]:
    """Every client's zone_name(), waiting up to *timeout* for each to be readable.

    A client between zones, or not logged in, has no zone: zone_name() is None. That says
    nothing about where it is going, so a comparison against it (or against another None)
    proves nothing and is waited out here instead. Returns the last read; a client that is
    still None after *timeout* is still unreadable, and the caller decides what that means.

    *timeout* defaults to ZONE_READABLE_TIMEOUT, read here rather than in the signature so it
    stays tunable.
    """
    if timeout is None:
        timeout = ZONE_READABLE_TIMEOUT
    zones: list[str | None] = []

    async def readable() -> bool:
        zones[:] = await asyncio.gather(*[c.zone_name() for c in clients])
        return all(zones)

    await poll_until(readable, timeout, poll_interval)
    return zones


async def in_expected_zone(machine, clients: list, zone: str, poll_interval: float = POLL_INTERVAL) -> bool:
    """Whether every client is in *zone*, read once each zone is readable. False having logged why.

    The check a node makes before it acts. It reads through read_zones, since a client just off
    a loading screen reads None for a moment, and a None never matches: read at once, it would
    fail a node whose players were where it expected.
    """
    zones = await read_zones(clients, poll_interval=poll_interval)
    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    if all(zone_matches(z, zone) for z in zones):
        return True
    wrong = [z for z in zones if not zone_matches(z, zone)]
    machine.log(
        f"FAILURE: clients not in expected zone (expected={zone!r}, unexpected={wrong})",
        logging.WARNING,
    )
    return False


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
    loading screen is ClientHealthMonitor's to call, not this loop's. Nobody is ported while
    a client's zone reads None either: one just off its loading screen reads None for a
    moment before it reads the zone it reached.

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
            # A None zone is a client just off its loading screen, or one that is not logged in:
            # the first may be about to read the destination, and a port would undo that (#70).
            and None not in zones
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


async def is_spellbook_open(client) -> bool:
    """Whether the spellbook is open, on any of its pages: backpack, deck, settings, realms, housing.

    Read from DeckConfiguration, the spellbook window itself. Its close button is no use for
    this, since it keeps reading visible once the window is closed (see is_visible_by_path).
    """
    return await is_visible_by_path(client, DECK_CONFIGURATION_PATH)


async def close_spellbook(client) -> bool:
    """Click the spellbook's close button until the spellbook is gone. True once it is.

    One click is not reliable: straight after a quicksell sale the game dropped it and the
    backpack stayed open, with the node reporting success. An open spellbook covers the
    screen and takes every click aimed behind it, so a caller that leaves one open breaks
    whatever runs next. A spellbook already closed costs one read and no click.
    """
    return await click_until_gone(client, BACKPACK_CLOSE_BUTTON_PATH, is_open=lambda: is_spellbook_open(client))


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


"""Units a client must move, with no loading screen, for a port or recall to count as having happened."""

POSITION_MARGIN = 10.0
"""Units a client may land from where it was sent and still count as there. A port puts you exactly
on the player you port to, and a teleport exactly on its XYZ; this is only room for the odd wobble."""


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
    * Arrival is every mover in the target's zone and seen with it (#130): only the sighting
      proves the realm and the instance. Not where the target stands: the editor's TP to
      player leaves the target to the user, who walks it off once the others appear, and a
      check against its live position then never passed. A sighting holds to ~3800 units
      (`sightings.SIGHT_RANGE`). A mover presumed already there has to be seen too
      (port_clients' `target`).

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
        if _same_zone(zone, target_zone) and pos is not None and distance(pos, target_pos) <= POSITION_MARGIN
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
    machine.log(
        f"Ports started: loading {[numbers[i] for i in sorted(result.seen_loading)]}, "
        f"moved {[numbers[i] for i in sorted(result.jumped)]}; waiting for every loading screen to clear",
        logging.DEBUG,
    )

    if not await poll_until(lambda: none_loading(movers), ZONE_CHANGE_TIMEOUT, poll_interval):
        machine.log(
            f"FAILURE: a player was still on a loading screen {ZONE_CHANGE_TIMEOUT:.0f}s after porting", logging.WARNING
        )
        return False
    machine.log(f"Loading screens cleared; waiting to see every player with P{target_no}", logging.DEBUG)
    landed_at = await _position_or_none(target)

    shortfall: list[str] = []

    async def all_with() -> bool:
        missing = await _not_with(machine, target, movers)
        reasons = [f"P{numbers[i]} {why}" for i, why in missing]
        if reasons != shortfall:
            # Logged when the reasons change, not every poll: a wait that never ends says why once.
            shortfall[:] = reasons
            if reasons:
                machine.log(f"Not yet with P{target_no}: {'; '.join(reasons)}", logging.DEBUG)
        return not missing

    if await poll_until(all_with, ZONE_CHANGE_TIMEOUT, poll_interval):
        return True
    target_zone, *zones = await read_zones([target, *movers], poll_interval=poll_interval)
    if all(_same_zone(z, target_zone) for z in zones):
        now_at = await _position_or_none(target)
        moved = (
            f"; P{target_no} moved {distance(now_at, landed_at):.0f} units while it waited, and a player "
            "too far from it cannot be seen"
            if now_at is not None and landed_at is not None and distance(now_at, landed_at) > POSITION_MARGIN
            else ""
        )
        machine.log(
            f"FAILURE: not every player ended up with P{target_no}: all are in its zone, but not all "
            f"seen with it{moved}",
            logging.WARNING,
        )
        return False
    machine.log(
        f"FAILURE: not every player ended up with P{target_no} (its zone {target_zone!r}, theirs {sorted(set(zones))})",
        logging.WARNING,
    )
    return False


async def _not_with(machine, target, movers: list) -> list[tuple[int, str]]:
    """Each mover not yet in the target's zone and seen with it, and why.

    Indices into *movers*. Zones are named by their last part, so the reasons can go in a
    log. Seen is asked only once every mover is in the target's zone.
    """
    target_zone = await target.zone_name()
    zones = await asyncio.gather(*[c.zone_name() for c in movers])
    missing = [
        (i, f"is in {_zone_tail(zone)}, not {_zone_tail(target_zone)}")
        for i, zone in enumerate(zones)
        if not _same_zone(zone, target_zone)
    ]
    if missing:
        return missing
    # The target's group, not its own sightings: a mover seen with one who is seen with the
    # target is with it, as all_together() counts it.
    with_target = set((await read_sightings([target, *movers], machine)).group_of(0))
    return [(i, "is not seen with it") for i in range(len(movers)) if i + 1 not in with_target]


def _zone_tail(zone: str | None) -> str:
    """A zone's last part for a log line (``DM_Z06_Bridge``), or ``an unreadable zone``."""
    return zone.rsplit("/", 1)[-1] if zone else "an unreadable zone"


def _same_zone(a: str | None, b: str | None) -> bool:
    """Whether two zone reads name one zone. Two unreadable (None) zones name nothing."""
    return a is not None and a == b


async def friend_profile_buttons(client) -> str:
    """Describe the friend profile popup's buttons for a log line. Never raises."""
    return (await read_friend_profile(client))[1]


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
    any_client: bool = False,
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

    With *any_client* the wait ends as soon as the prompt shows on one client rather than
    all of them, for an object only one client needs to use and that raises the prompt
    only for those who can (a skeleton key door: a player with no key never sees it). The
    caller reads which clients got it. Without it, a client that can never get the prompt
    costs every attempt its full ``safety_delay``.

    Logs under the running node, since it is called from one — see ``machine.log`` in
    the node conventions.
    """

    async def _reached() -> bool:
        if not any_client:
            return await prompt.all_showing(clients)
        return any(await asyncio.gather(*[prompt.showing(c) for c in clients]))

    if clear_first and not await clear_stale_prompt(
        machine, clients, reset, prompt, safety_delay, poll_interval, numbers
    ):
        return False

    for attempt in range(1, MAX_RETRIES + 1):
        machine.log(f"Attempt {attempt}/{MAX_RETRIES}: teleporting to the {prompt.name}", logging.DEBUG)
        await asyncio.gather(*[c.teleport(point) for c in clients])

        machine.log(f"Waiting for {prompt.name}…", logging.DEBUG)
        if await poll_until(_reached, safety_delay, poll_interval):
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
    if not await in_expected_zone(machine, clients, from_zone, poll_interval):
        return False

    return await reach_prompt(machine, clients, sigil, reset, DUNGEON_ENTRANCE_PROMPT, safety_delay, poll_interval)
