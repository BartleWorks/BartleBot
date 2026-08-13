"""Reading and rebuilding the in-game group across clients.

Wizard101 groups grant wisps after combat and, critically, guarantee the group goes first
in battle. A player is dropped from their group whenever they log off or crash, so any
strategy that depends on going first degrades silently after a crash — and BartleBot's
recovery relaunches the client without re-grouping it.

This module rebuilds the group through the friends list: the leader opens the missing
player's profile popup and clicks "Add to Group", the invited client accepts the modal.
The friends-list route is used in preference to clicking a player's model in the world
because it works **cross-zone** — clients do not have to be gathered anywhere first, so a
regroup can run while the fleet is still scattered mid-recovery.

Group membership is read from the UI, not from memory. WizWalker exposes
SocialSystemsManager.group_gid(), which looks like the obvious source, but on the current
game build the library resolves that object to a dead instance: player_gid — which is
never zero for a logged-in wizard — reads 0, and no field moves when a group is created or
left. tools/probe_group_offsets.py demonstrates this and is kept for re-checking after a
game patch. Until that is fixed upstream, membership is read from the Leave Group button
in the social window, which is *visible* only while grouped — the game hides it when
ungrouped rather than graying it, so enabled-ness says nothing.

That button answers "am I in a group?" but not "which group?", and two clients in two
different groups both read True. The Group tab's roster closes that gap: it lists the
member names, and since a wizard belongs to at most one group, two clients showing the
same member set are necessarily in the same group. The roster is read on the same window
visit as the button, so it costs a handful of memory reads rather than a second survey.

Knowing the shape of a split is also what makes it repairable. The game refuses an invite
to a player who is already in a group, so form_group dissolves every group except the
largest one (see leave_group and _dissolve_other_groups) and then invites the freed
clients into it — the same invite path any ungrouped client takes.

Deliberately free of Qt and FSM dependencies so that the form_group node, AppController
and ClientManager can all call it, and so it can be tested without either.

Requires every account to be mutually friended in-game — a one-time manual setup.
"""

import asyncio
import logging
import weakref
from dataclasses import dataclass, field

from bartlebot import wizard_names
from bartlebot.nodes import node_helpers
from bartlebot.nodes.paths import (
    ALREADY_IN_GROUP_DISMISS_PATH,
    CHARACTER_PROFILE_CLOSE_PATH,
    CHARACTER_PROFILE_PATH,
    FRIENDS_LIST_WINDOW_NAMES,
    GROUP_INVITE_ACCEPT_PATH,
    GROUP_ROSTER_LINES,
    GROUP_WINDOW_CLOSE_PATH,
    INVITE_TO_GROUP_BUTTON_PATH,
    LEAVE_GROUP_BUTTON_PATH,
    SOCIAL_BUTTON_PATH,
    SOCIAL_GROUP_TAB_PATH,
    SOCIAL_WINDOW_PATH,
    group_member_name_path,
)
from bartlebot.nodes.timing import (
    GROUP_ADD_RETRIES,
    GROUP_INVITE_TIMEOUT,
    GROUP_UI_SETTLE,
    GROUP_VERIFY_TIMEOUT,
    POLL_INTERVAL,
    TRIGGER_TIMEOUT,
    jitter,
)

logger = logging.getLogger(__name__)

# Serialises form_group across its three callers: the form_group node, pre-run setup, and
# post-recovery regrouping.
#
# Without this, two calls can overlap and each elect a different leader, producing two
# separate groups instead of one — the exact split-group failure the UI cannot detect,
# since membership is only readable per client as a yes/no.
#
# Two overlaps are reachable today:
#   * machine.pause() suspends the FSM only AFTER the current node finishes, so a
#     form_group node keeps running while recovery starts regrouping;
#   * ClientManager.set_machine() is called before pre-run setup, so a client that dies
#     during deck apply or pre-run grouping queues recovery alongside it.
#
# Holding the lock for the whole call also fixes the check-then-act race: the second
# caller re-reads membership only after the first has finished, so it sees the group that
# was just built and no-ops instead of building a second one.
#
# Keyed by event loop rather than created once at import: an asyncio.Lock binds to the
# first loop that awaits it and raises on any other, so a single module-level lock would
# be a latent failure for anything that runs on a second loop.
_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()


def _form_group_lock() -> asyncio.Lock:
    """Return the form_group lock for the running event loop, creating it on first use."""
    loop = asyncio.get_running_loop()
    lock = _locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _locks[loop] = lock
    return lock


class AlreadyInAnotherGroup(Exception):
    """The invited player is in a group we are not part of.

    Distinguished from an ordinary failed invite because retrying cannot fix it — the
    player has to leave their other group first.
    """


@dataclass(frozen=True)
class GroupState:
    """One client's view of its own group, as read from the social window's Group tab.

    `roster` holds the wizard names the Group tab lists, in line order, and is empty when
    the roster could not be read — which is not the same as an empty group, so it must not
    be interpreted as one. See same_group.
    """

    grouped: bool = False
    roster: tuple[str, ...] = ()


@dataclass
class GroupResult:
    """Outcome of a form_group call."""

    success: bool
    added: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    # Clients taken out of a different group so they could be invited into this one. Worth
    # reporting separately from `added`: it is the only part of a regroup that removes
    # someone from a group they were happily in, including players who are not ours.
    left: list[str] = field(default_factory=list)
    reason: str = ""

    def summary(self) -> str:
        if self.success and not self.added and not self.left:
            return "all clients were already grouped"
        parts = []
        if self.left:
            parts.append(f"moved {', '.join(self.left)} out of another group")
        if self.added:
            parts.append(f"added {', '.join(self.added)}")
        if self.failed:
            parts.append(f"could not add {', '.join(self.failed)}")
        if self.reason:
            parts.append(self.reason)
        return "; ".join(parts) or "nothing to do"


async def _open_social_window(client) -> bool:
    """Open the social window on the Group tab. Returns True if it is showing.

    The HUD button toggles the window, so it is only clicked when the window is not
    already visible — clicking unconditionally would close one that was already open.

    The button is used in preference to the Ctrl+A shortcut because WizWalker's
    send_hotkey does not reliably deliver the modifier: the game receives a bare "A" and
    the character strafes instead.

    The tab click is followed by a fixed sleep rather than a poll for something on the
    Group page, and neither is it skipped when the window may already be on that tab.
    Both would need a way to tell which tab is showing, and there is none: GroupPageNumber
    reads visible=True on every tab, and tools/probe_group_tab.py then swept all of them
    without finding any property that separates Group from the rest (see paths.py). Do not
    reintroduce either optimisation without a fresh probe run — an indicator that is
    always true makes this function return immediately, so the window never reaches the
    Group tab and membership is read off whichever page happened to be showing.
    """
    if not await node_helpers.is_visible_by_path(client, SOCIAL_WINDOW_PATH):
        # An open friends list covers the social HUD button, so the click lands on the list
        # instead and the social window never appears — which failed the whole pre-run
        # grouping when a run was started with the large list left up. Checked rather than
        # closed unconditionally: when nothing is open this costs two name lookups and no
        # input, and pressing F on a closed list would open one.
        if await node_helpers.is_friends_list_open(client):
            logger.debug("Friends list is covering the social button; closing it first")
            await node_helpers.close_friends_list(client)

        await node_helpers.click_by_path(client, SOCIAL_BUTTON_PATH)
        opened = await node_helpers.poll_until(
            lambda: node_helpers.is_visible_by_path(client, SOCIAL_WINDOW_PATH),
            timeout=TRIGGER_TIMEOUT,
            poll_interval=POLL_INTERVAL,
        )
        if not opened:
            return False

    await node_helpers.click_by_path(client, SOCIAL_GROUP_TAB_PATH)
    await asyncio.sleep(jitter(GROUP_UI_SETTLE))
    return True


async def close_social_window(client) -> None:
    """Close the social window if it is open, retrying until it is gone. Never raises.

    Openness is judged from the window itself, not its close button: a control inside a
    hidden window still reports visible=True, so polling the button never terminates.
    """
    try:
        await node_helpers.click_until_gone(
            client,
            GROUP_WINDOW_CLOSE_PATH,
            is_open=lambda: node_helpers.is_visible_by_path(client, SOCIAL_WINDOW_PATH),
        )
    except Exception:
        logger.debug("Could not close the social window", exc_info=True)


async def _read_roster(client) -> tuple[str, ...]:
    """Read the wizard names listed on the Group tab. The social window must be open.

    Returns them in line order with blanks dropped. Never raises: an unreadable roster
    yields (), which every caller treats as "cannot tell" rather than as "empty group".

    Lines are read to GROUP_ROSTER_LINES rather than stopping at the first blank one,
    because an empty slot in the middle of the list would otherwise truncate the members
    after it. A line beyond the ones the game drew simply resolves to no window.
    """
    names: list[str] = []
    for line in range(1, GROUP_ROSTER_LINES + 1):
        try:
            raw = await node_helpers.get_text_by_path(client, group_member_name_path(line))
        except Exception:
            logger.debug("Could not read group roster line %d", line, exc_info=True)
            continue
        name = wizard_names.strip_markup(raw)
        if not name:
            continue
        if name.casefold() in _ROSTER_PLACEHOLDERS:
            logger.debug("Ignoring placeholder text %r on group roster line %d", name, line)
            continue
        names.append(name)
    return tuple(names)


# Text that appears in a roster NameText but is not a player.
#
# The first empty slot is not blank: it is an invitation prompt ("Invite someone by
# clicking here!"), and its NameText carries "Left" — apparently leftover placeholder text
# from the game's own layout. It shows up on every client, in every group, so a two-wizard
# group reads as three members.
#
# Matched exactly rather than by any property of the string. An earlier version filtered on
# names containing a space, on the theory that every wizard has a given name and a surname;
# that is wrong, a wizard can have no surname at all, and the rule would have dropped real
# players from the roster.
#
# Leaving the junk in is not an option either, and not merely untidy: a constant entry pads
# every roster to at least one member, which defeats two of the fail-safes here. same_group
# treats a non-empty roster as one it managed to read, so two clients whose real member
# lines failed would compare equal on the junk alone and report a confident "same group";
# and _warn_on_disagreement counts roster entries to notice that one of the two membership
# reads has broken.
#
# If another placeholder turns up after a game update it will show up as a phantom member
# in the split-group log line. tools/probe_group_roster.py prints the raw text of every
# line, which is how to identify it; add it here.
_ROSTER_PLACEHOLDERS = frozenset({"left"})


async def read_group_state(client) -> GroupState:
    """Return this client's membership flag and group roster in one social-window visit.

    Membership is read from the *visibility* of the Leave Group button on the social
    window's Group tab. The game hides that button when you are not in a group rather than
    graying it, so visibility is the discriminator — confirmed with tools/probe_group_ui.py.

    Visibility specifically, not is_enabled_by_path: that helper returns True for any
    window that merely exists and is not grayed, and the whole SocialSystemsWindow subtree
    exists in the UI tree whether or not the window is open. Using it here reported every
    client as grouped, always.

    The roster is read on the same visit because the expensive part is getting the window
    open and settled — GROUP_UI_SETTLE dominates, and the roster adds only a handful of
    memory reads on top of a window that is already showing. Reading it separately would
    double the cost of the slowest and most failure-prone step for no benefit.

    Returns a blank state on any failure: the caller's next move is the same either way
    (try to add this client), and one unreadable client must not abort a regroup for the
    rest.
    """
    try:
        if not await _open_social_window(client):
            logger.debug("Social window did not open; treating client as ungrouped")
            return GroupState()
        grouped = await node_helpers.is_visible_by_path(client, LEAVE_GROUP_BUTTON_PATH)
        state = GroupState(grouped=grouped, roster=await _read_roster(client))
        _warn_on_disagreement(state)
        return state
    except Exception:
        logger.debug("Could not read group membership", exc_info=True)
        return GroupState()
    finally:
        await close_social_window(client)


def _warn_on_disagreement(state: GroupState) -> None:
    """Log when the Leave Group button and the roster tell different stories.

    They are independent reads of the same fact, so a disagreement means one of them has
    stopped working — most likely a game update moved a path. Neither is trusted over the
    other here; the point is to surface the breakage rather than let membership quietly
    become wrong.
    """
    if state.grouped and len(state.roster) < 2:
        logger.warning(
            "Leave Group button says grouped but the roster lists %d member(s) — "
            "one of those reads is broken (see tools/probe_group_roster.py)",
            len(state.roster),
        )
    elif not state.grouped and len(state.roster) > 1:
        logger.warning(
            "Roster lists %d members but the Leave Group button says ungrouped — "
            "one of those reads is broken (see tools/probe_group_roster.py)",
            len(state.roster),
        )


async def is_in_group(client) -> bool:
    """Return True when this client is currently in a group.

    Says nothing about *which* group — see same_group for that.
    """
    return (await read_group_state(client)).grouped


async def read_group_states(clients: list) -> list[GroupState]:
    """Return each client's group state, positionally aligned with `clients`.

    Checked concurrently. Every input this sends is addressed to one window handle —
    _open_social_window clicks the HUD button rather than pressing Ctrl+A, and
    click_by_path goes through client.mouse_handler, whose mouseless hook writes into that
    client's own memory — so the clients cannot interfere with each other. This is the same
    isolation that lets the pre-run deck applies run concurrently.

    The win is large because almost all of the elapsed time is asyncio.sleep, not blocking
    memory reads: one GROUP_UI_SETTLE per client plus the retry pauses inside
    close_social_window. Those overlap perfectly; only the ReadProcessMemory calls
    serialise on the loop thread.

    No return_exceptions is needed — read_group_state already absorbs every failure and
    answers a blank state, so one unreadable client cannot cancel the survey for the rest.
    """
    return list(await asyncio.gather(*(read_group_state(c) for c in clients)))


async def read_group_flags(clients: list) -> list[bool]:
    """Return whether each client is in a group, positionally aligned with `clients`."""
    return [state.grouped for state in await read_group_states(clients)]


def same_group(states: list[GroupState]) -> bool | None:
    """Return whether every client is in ONE group. None when it cannot be determined.

    Two clients are in the same group exactly when their rosters list the same members: a
    wizard belongs to at most one group, so two distinct groups have disjoint — and
    therefore unequal — member sets. That makes set equality a complete test, and one that
    needs no wizard names of our own and does not care which line a member appears on.
    Both of those matter: names cost a character-page open each, and the meaning of any
    particular line is a game-layout detail that a patch can reorder.

    Returns None rather than a guess when any client's roster is unreadable, since empty
    sets compare equal and would otherwise report a confident "same group" precisely when
    nothing could be read at all. Callers should treat None as "carry on as before" — it
    is the state the code was in before rosters were readable.
    """
    if len(states) < 2:
        return None
    if any(not state.roster for state in states):
        return None
    first = _roster_key(states[0])
    return all(_roster_key(state) == first for state in states[1:])


def _roster_key(state: GroupState) -> frozenset[str]:
    """Return a roster as a case-insensitive set, for comparison between clients."""
    return frozenset(name.casefold() for name in state.roster)


def describe_groups(states: list[GroupState]) -> str:
    """Render the distinct groups these clients are in, for a log line or an error.

    Deduplicated by member set: every member of a group reads the same roster, so listing
    one per client showed four rosters for two groups and buried the actual shape of the
    problem. Ordering within a group is left as read (each client sees itself first).
    """
    distinct: list[tuple[str, ...]] = []
    seen: set[frozenset[str]] = set()
    for state in states:
        key = _roster_key(state)
        if key in seen:
            continue
        seen.add(key)
        distinct.append(state.roster)
    return " vs ".join("[" + ", ".join(roster) + "]" if roster else "[unreadable]" for roster in distinct)


async def is_grouped(clients: list) -> bool:
    """Return True when every client is in a group, and in the same one where readable.

    A single client is never considered grouped — a group of one confers no benefit, and
    treating it as grouped would suppress the regroup that adds the others back.

    A split group (everyone grouped, but not with each other) reads as False, so callers
    that use this to decide whether to regroup will act on it. When the rosters cannot be
    read this falls back to the per-client yes/no, which cannot see a split.
    """
    if len(clients) < 2:
        return False
    states = await read_group_states(clients)
    if not all(state.grouped for state in states):
        return False
    return same_group(states) is not False


async def leave_group(client) -> bool:
    """Take this client out of its group. True once it is out, including if it never was.

    Used to repair a split: the game refuses an invitation to a player who is already in a
    group, so one of the two groups has to be dissolved before anyone can be moved.

    Finding the button already gone is success, not failure. Wizard101 dissolves a
    two-player group when *either* player leaves, so in the common case of two pairs only
    one client actually clicks anything and its partner is already out by the time this
    reaches it. That is also why membership is re-read here rather than assumed from the
    survey: the click that frees this client may have been made on a different one.
    """
    try:
        if not await _open_social_window(client):
            logger.debug("Social window did not open; cannot leave the group")
            return False
        if not await node_helpers.is_visible_by_path(client, LEAVE_GROUP_BUTTON_PATH):
            return True

        await node_helpers.click_by_path(client, LEAVE_GROUP_BUTTON_PATH)
        # Leaving is a server round-trip, so the button does not vanish on the click.
        return await node_helpers.poll_until(
            lambda: _left_the_group(client),
            timeout=GROUP_VERIFY_TIMEOUT,
            poll_interval=POLL_INTERVAL,
        )
    except Exception:
        logger.warning("Failed to leave the group", exc_info=True)
        return False
    finally:
        await close_social_window(client)


async def _left_the_group(client) -> bool:
    """Whether the Leave Group button has gone, i.e. this client is no longer grouped."""
    return not await node_helpers.is_visible_by_path(client, LEAVE_GROUP_BUTTON_PATH)


def _group_members(states: list[GroupState]) -> dict[frozenset[str], list[int]]:
    """Map each distinct group to the indices of our clients in it.

    Clients whose roster could not be read are left out entirely — they cannot be assigned
    to a group, and guessing would risk dissolving the wrong one.
    """
    groups: dict[frozenset[str], list[int]] = {}
    for idx, state in enumerate(states):
        if state.grouped and state.roster:
            groups.setdefault(_roster_key(state), []).append(idx)
    return groups


def _choose_leader(states: list[GroupState], leader_index: int) -> int:
    """Return the index of the client that should send invitations.

    Prefers the group holding the MOST of our clients, because every other group has to be
    dissolved before its members can be invited across — picking the smaller one in a 3+1
    split would make three wizards leave and rejoin instead of one. `leader_index` wins
    ties, so the caller's preference still decides when the choice is free.

    Falls back to the first client in any group when rosters are unreadable, and to
    `leader_index` when nobody is grouped (at bot start, or when the leader crashed and the
    group dissolved with it).
    """
    groups = _group_members(states)
    if groups:
        largest = max(len(members) for members in groups.values())
        contenders = [members for members in groups.values() if len(members) == largest]
        for members in contenders:
            if leader_index in members:
                return leader_index
        return contenders[0][0]

    if states[leader_index].grouped:
        return leader_index
    for idx, state in enumerate(states):
        if state.grouped:
            return idx
    return leader_index


async def form_group(
    clients: list,
    names: list[str | None],
    leader_index: int = 0,
    log=None,
    states: list[GroupState] | None = None,
) -> GroupResult:
    """Ensure every client is in one shared group, inviting whoever is missing.

    Clients found in a *different* group are taken out of it first — the game refuses an
    invitation to a player who is already grouped. That is a real change to the game state
    and can remove players who are not ours from a group they were in, so it is logged at
    WARNING and reported in GroupResult.left.

    `names` holds each client's wizard name, positionally aligned with `clients`; a None
    entry means the name is unknown and that client cannot be located in the leader's
    friends list, so it is reported as failed rather than silently skipped.

    `states` optionally supplies group state already read by the caller, saving a second
    survey — each one opens and closes the social window on every client, so re-reading
    doubles both the time and the number of chances for a flaky UI read. They are ignored
    (and re-read) if another form_group call was in progress, since it may have changed
    membership while we waited.

    Returns a GroupResult instead of raising. Callers decide what a partial failure means:
    the node fails the run, the automatic hooks log and carry on.

    Serialised against other form_group calls — see _form_group_lock. Callers may await
    this concurrently; the second simply waits and then finds the group already built.
    """

    def _log(msg: str, level: int = logging.INFO) -> None:
        logger.log(level, msg)
        if log is not None:
            log(msg, level)

    if len(clients) < 2:
        return GroupResult(success=True, reason="fewer than two clients connected")

    if len(names) != len(clients):
        return GroupResult(
            success=False,
            reason=f"expected {len(clients)} wizard names but got {len(names)}",
        )

    lock = _form_group_lock()
    # If we have to wait, another call is changing group membership right now, so any
    # states the caller read beforehand are stale and must be re-read inside the lock.
    contended = lock.locked()
    if contended:
        _log("Another group formation is already running — waiting for it to finish", logging.DEBUG)
    async with lock:
        return await _form_group_locked(clients, names, leader_index, _log, None if contended else states)


def _in_leader_group(state: GroupState, leader_state: GroupState) -> bool:
    """Whether this client is demonstrably in the leader's group, not merely in *a* group.

    Falls back to the plain membership flag when either roster is unreadable, which is the
    behaviour this had before rosters existed: assume a grouped client is in our group.
    Guessing the other way would tear down a working group whenever a roster read failed.
    """
    if not state.grouped:
        return False
    if not state.roster or not leader_state.roster:
        return True
    return _roster_key(state) == _roster_key(leader_state)


async def _dissolve_other_groups(
    clients: list,
    states: list[GroupState],
    leader_idx: int,
    _log,
) -> list[GroupState]:
    """Make every client in a group other than the leader's leave it.

    Returns updated states with the freed clients marked ungrouped, so the invite loop that
    follows picks them up. Nothing is re-surveyed: another full pass would open and close
    the social window on every client, and each leave is already confirmed individually.

    Members of a doomed group are handled one at a time, not concurrently. Wizard101
    dissolves a two-player group when either player leaves, so the second client's Leave
    button is usually gone by the time its turn comes — leave_group treats that as success
    and clicks nothing. Doing them in parallel would race that dissolve instead of riding
    it, which is where the "only one wizard needs to leave" saving comes from.

    Does nothing when the leader's own roster is unreadable: without it there is no way to
    tell which group is the one being kept, and dissolving the wrong one is worse than
    leaving the split in place for the invite loop to report.
    """
    leader_state = states[leader_idx]
    if not leader_state.roster:
        return states

    leader_key = _roster_key(leader_state)
    doomed: dict[frozenset[str], list[int]] = {}
    for key, members in _group_members(states).items():
        if key != leader_key:
            doomed[key] = members
    if not doomed:
        return states

    updated = list(states)
    for members in doomed.values():
        roster = states[members[0]].roster
        # Worth saying out loud: a group can contain players who are not ours, and they
        # lose their group here too.
        _log(f"Dissolving group [{', '.join(roster)}] so its members can join the leader's", logging.WARNING)
        for idx in members:
            if await leave_group(clients[idx]):
                updated[idx] = GroupState()
            # A client that could not leave keeps its old state, so the invite loop still
            # tries it and the game's refusal is what reports the failure. Recording it
            # here as well would name it twice in the result.
    return updated


async def _form_group_locked(
    clients: list,
    names: list[str | None],
    leader_index: int,
    _log,
    states: list[GroupState] | None,
) -> GroupResult:
    """Body of form_group. Must only be called while _form_group_lock is held."""
    if states is None:
        states = await read_group_states(clients)
    if all(state.grouped for state in states) and same_group(states) is not False:
        _log("All clients are already in a group — nothing to do", logging.DEBUG)
        return GroupResult(success=True)

    leader_idx = _choose_leader(states, leader_index)
    leader = clients[leader_idx]
    _log(f"Rebuilding group: leader is client {leader_idx} ({names[leader_idx] or 'unknown'})")

    result = GroupResult(success=True)

    # An invitation to a player who is already in a group is refused by the game, so any
    # client sitting in a different group has to be freed before it can be invited across.
    before = states
    states = await _dissolve_other_groups(clients, states, leader_idx, _log)
    result.left = [
        names[i] or f"client {i}" for i in range(len(clients)) if before[i].grouped and not states[i].grouped
    ]
    leader_state = states[leader_idx]

    for idx, client in enumerate(clients):
        if idx == leader_idx or _in_leader_group(states[idx], leader_state):
            continue

        name = names[idx]
        if not name:
            _log(f"Client {idx} has no known wizard name — cannot invite it", logging.WARNING)
            result.success = False
            result.failed.append(f"client {idx}")
            continue

        if await _add_one(leader, client, name, _log, names[leader_idx]):
            result.added.append(name)
        else:
            result.success = False
            result.failed.append(name)

    # Close the friends list once, after every invite — it is reused between them, so
    # closing per-invite would mean reopening and re-paginating it for each client.
    #
    # The profile popup and the social window both sit on top of the friends list, and a
    # close click aimed at a covered button lands on whatever is above it. Clear them
    # first: _close_invite_windows already tried, but it gives up after its own timeout
    # and its failure must not silently strand the list underneath.
    await _close_overlapping_windows(leader)
    if not await node_helpers.close_friends_list(leader):
        _log(
            f"Friends list did not close on the leader. Visible windows: {await _visible_windows(leader)}",
            logging.WARNING,
        )

    if not result.success:
        result.reason = "check that every account is online and mutually friended"

    _log(f"Group formation finished: {result.summary()}")
    return result


async def _add_one(leader, target, name: str, log, leader_name: str | None = None) -> bool:
    """Invite one client into the leader's group. Returns True once the UI confirms it."""
    for attempt in range(1, GROUP_ADD_RETRIES + 1):
        try:
            if await _invite_and_accept(leader, target, name, log, leader_name):
                log(f"Added {name} to the group")
                return True
            log(f"Attempt {attempt}/{GROUP_ADD_RETRIES} to add {name} did not take effect", logging.DEBUG)
        except AlreadyInAnotherGroup:
            log(
                f"{name} is already in another group and must leave it before joining this one",
                logging.WARNING,
            )
            return False
        except ValueError as exc:
            # Raised by the friends-list helpers: name not present, or nobody online.
            log(f"Could not find {name} in the leader's friends list: {exc}", logging.WARNING)
            return False
        except Exception:
            logger.warning("Unexpected error adding %s to the group", name, exc_info=True)
        finally:
            await _close_invite_windows(leader, target)

    log(f"Gave up adding {name} to the group after {GROUP_ADD_RETRIES} attempts", logging.WARNING)
    return False


async def _invite_and_accept(leader, target, name: str, log, leader_name: str | None = None) -> bool:
    """Run one full invite exchange and report whether the target joined the leader's group."""
    async with leader.mouse_handler:
        await node_helpers.open_friend_profile(leader, name)
        await asyncio.sleep(jitter(GROUP_UI_SETTLE))

        if not await node_helpers.click_by_path(leader, INVITE_TO_GROUP_BUTTON_PATH):
            diagnosis = await node_helpers.diagnose_path(leader, INVITE_TO_GROUP_BUTTON_PATH)
            log(f"Group invite button unavailable for {name}: {diagnosis}", logging.DEBUG)
            return False

        # The game rejects the invite outright if that player is already in a group —
        # someone else's, since form_group skips clients known to be in one. Dismiss the
        # popup so it does not block the next invite, and stop: retrying cannot help.
        await asyncio.sleep(jitter(GROUP_UI_SETTLE))
        if await node_helpers.is_visible_by_path(leader, ALREADY_IN_GROUP_DISMISS_PATH):
            await node_helpers.click_by_path(leader, ALREADY_IN_GROUP_DISMISS_PATH)
            raise AlreadyInAnotherGroup(f"{name} is already in another group")

    # Accept on the invited client. PartyInviteWindow is specific to group invitations, so
    # unlike the shared MessageBoxModalWindow there is no risk of confirming some unrelated
    # dialog that happened to be open. The prompt is server-relayed, so it lags the click.
    invited = await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(target, GROUP_INVITE_ACCEPT_PATH),
        timeout=GROUP_INVITE_TIMEOUT,
        poll_interval=POLL_INTERVAL,
    )
    if not invited:
        log(f"No group invitation appeared for {name} within {GROUP_INVITE_TIMEOUT:.0f}s", logging.DEBUG)
        return False

    await node_helpers.click_by_path(target, GROUP_INVITE_ACCEPT_PATH)

    # The game opens the social window by itself on joining, so its appearance is the
    # first sign the accept was processed. Waiting on it is cheap — no clicks — but it is
    # only a hint, so membership is confirmed properly below.
    await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(target, SOCIAL_WINDOW_PATH),
        timeout=GROUP_VERIFY_TIMEOUT,
        poll_interval=POLL_INTERVAL,
    )

    # Authoritative check: the Leave Group button is only visible when actually grouped.
    # The social window it needs is already open here, so this costs one click, not two.
    # Cleanup is left to _add_one's `finally`, which runs on the failure paths too —
    # closing here as well would double up and mask ordering bugs in the cleanup.
    state = await read_group_state(target)
    if not state.grouped:
        return False

    # Grouped is not the same as grouped WITH US. The target could have accepted somebody
    # else's invitation in the moment between our click and this read, which the flag alone
    # cannot see. The roster can: the leader has to be listed on it.
    if leader_name and state.roster and not _lists(state.roster, leader_name):
        log(
            f"{name} joined a group that does not contain {leader_name} (roster: {', '.join(state.roster)})",
            logging.WARNING,
        )
        return False
    return True


def _lists(roster: tuple[str, ...], name: str) -> bool:
    """Whether `name` appears on this roster, compared case-insensitively."""
    return name.casefold() in {member.casefold() for member in roster}


async def _visible_windows(client) -> str:
    """Report which of the windows involved here are currently visible.

    Diagnostic only. A close click that lands on a covering window fails silently, so when
    a close does time out the useful question is "what else was on screen?".
    """
    checks = {
        "wndCharacter": CHARACTER_PROFILE_PATH,
        "SocialSystemsWindow": SOCIAL_WINDOW_PATH,
    }
    parts = []
    for label, path in checks.items():
        try:
            parts.append(f"{label}={await node_helpers.is_visible_by_path(client, path)}")
        except Exception:
            parts.append(f"{label}=<error>")
    for name in FRIENDS_LIST_WINDOW_NAMES:
        try:
            parts.append(f"{name}={await node_helpers.is_named_window_visible(client, name)}")
        except Exception:
            parts.append(f"{name}=<error>")
    return ", ".join(parts)


async def _close_overlapping_windows(client) -> None:
    """Close the windows that can sit on top of the friends list. Never raises."""
    await close_social_window(client)
    try:
        await node_helpers.click_until_gone(
            client,
            CHARACTER_PROFILE_CLOSE_PATH,
            is_open=lambda: node_helpers.is_visible_by_path(client, CHARACTER_PROFILE_PATH),
        )
    except Exception:
        logger.debug("Could not close the friend profile popup", exc_info=True)


async def _close_invite_windows(leader, target) -> None:
    """Close the windows one invite leaves behind. Never raises.

    Two windows are opened by an invite exchange and neither closes itself:
      - the friend profile popup, on the leader;
      - the social window, which the game opens on *both* players once the group is
        created or joined.
    Left open they cover the HUD for every node that runs afterwards, and they stack up
    across a multi-client regroup.

    ORDER MATTERS. The social window opens on top of the friend profile on the leader, and
    it covers the profile's close button — a click aimed at that button lands on the social
    window instead, leaving the profile open. So the social window is closed first on both
    players, and only then the profile.

    Each close retries until the window is actually gone rather than trusting one click,
    because a click swallowed by an overlapping window leaves the next one aimed at the
    wrong place too.

    The friends list is deliberately not closed here — it is reused for the next invite
    and closed once at the end of form_group.
    """
    await close_social_window(target)
    await _close_overlapping_windows(leader)
