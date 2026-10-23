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

Deliberately free of Qt and FSM dependencies so that the relog node, AppController
and ClientManager can all call it, and so it can be tested without either.

Requires every account to be mutually friended in-game — a one-time manual setup.
"""

import asyncio
import logging
import weakref
from dataclasses import dataclass, field

from bartlebot.bot import run_trace
from bartlebot.game import wizard_names
from bartlebot.nodes import node_helpers
from bartlebot.nodes.paths import (
    ALREADY_IN_GROUP_DISMISS_PATH,
    CHARACTER_PROFILE_CLOSE_PATH,
    CHARACTER_PROFILE_PATH,
    CROWNS_SHOP_WINDOW_PATH,
    FRIENDLY_PLAYER_GO_TO_BUTTON_PATH,
    FRIENDLY_PLAYER_MENU_PATH,
    FRIENDS_LIST_PATHS,
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
    FRIEND_PROFILE_SETTLE_TIMEOUT,
    FRIEND_PROFILE_TIMEOUT,
    GROUP_ADD_RETRIES,
    GROUP_INVITE_TIMEOUT,
    GROUP_JOIN_HINT_TIMEOUT,
    GROUP_TAB_LOAD_TIMEOUT,
    GROUP_UI_SETTLE,
    GROUP_VERIFY_TIMEOUT,
    POLL_INTERVAL,
    TRIGGER_TIMEOUT,
    jitter,
)
from bartlebot.settings_manager import SettingsManager

logger = logging.getLogger(__name__)

# Serializes form_group across its three callers: the relog node, pre-run setup, and
# post-recovery regrouping.
#
# Without this, two calls can overlap and each elect a different leader, producing two
# separate groups instead of one — the exact split-group failure the UI cannot detect,
# since membership is only readable per client as a yes/no.
#
# Two overlaps are reachable today:
#   * machine.pause() suspends the FSM only AFTER the current node finishes, so a
#     relog node keeps regrouping while recovery starts regrouping too;
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


def slot_label(idx: int) -> str:
    """Name a client by its player slot ("P2"), for anything that reaches a log line.

    Never a wizard name: logs get pasted into bug reports, and a real name links the
    account to whoever posted it. Callers pass their clients in slot order, so the index
    into `clients` is the slot.
    """
    return f"P{idx + 1}"


@dataclass
class GroupResult:
    """Outcome of a form_group call.

    The lists hold slot labels (see slot_label), not wizard names, because summary() is
    logged by every caller.
    """

    success: bool
    added: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    # Clients taken out of a different group so they could be invited into this one. Worth
    # reporting separately from `added`: it is the only part of a regroup that removes
    # someone from a group they were happily in, including players who are not ours.
    left: list[str] = field(default_factory=list)
    reason: str = ""
    # Every client's group state as form_group left it, positionally aligned with its
    # clients, so a caller that needs membership next (the pre-run port survey) does not
    # open every social window again to learn what this call just did. None when it read
    # nothing. See _final_states for what is read and what is inferred.
    states: list[GroupState] | None = None

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


async def _open_social_window(client, ready=None, ready_timeout: float = 0.0, for_input: bool = False) -> bool:
    """Open the social window on the Group tab. Returns True if it is showing.

    The HUD button toggles the window, so it is only clicked when the window is not
    already visible — clicking unconditionally would close one that was already open.

    The button is used in preference to the Ctrl+A shortcut because WizWalker's
    send_hotkey does not reliably deliver the modifier: the game receives a bare "A" and
    the character strafes instead.

    The tab is clicked unconditionally, because which tab is showing cannot be read:
    GroupPageNumber reads visible=True on every tab, and tools/probe_group_tab.py then swept
    all of them without finding any property that separates Group from the rest (see
    paths.py). Do not skip the click on the strength of some indicator without a fresh
    probe run.

    What CAN be read is whether the Group page has loaded, and that is what the click is
    followed by (_group_tab_loaded). The page is blank each time the window opens and fills
    in only once the Group tab has been selected; the game clears it again when the window
    closes. So on a window this function opened, "loaded" is proof the tab click landed,
    grouped or not — which is what replaced the fixed GROUP_UI_SETTLE that used to follow
    every click.

    The one case that proof does not cover is a window that was already open with the page
    loaded from earlier: the page then reads loaded before the click is processed, and stays
    loaded if the window is switched to another tab. For a read that does not matter — the
    loaded page is readable from any tab. For a click on something on the page it does,
    because a control on a hidden tab still reports visible and the click lands on whatever
    the showing tab has there. `for_input` callers therefore still pay the fixed settle in
    that case, and only in that case.

    `ready` replaces the load poll for the caller with something more specific to wait
    for. Confirming a join waits for "grouped" only, and its arrival is proof the page has
    populated, so that caller passes the Leave Group button and returns as soon as it
    appears. Absence for the whole `ready_timeout` is then the answer too.
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

    loaded_before_click = await _group_tab_loaded(client)
    await node_helpers.click_by_path(client, SOCIAL_GROUP_TAB_PATH)
    if ready is not None:
        await node_helpers.poll_until(ready, timeout=ready_timeout, poll_interval=POLL_INTERVAL)
    elif for_input and loaded_before_click:
        await asyncio.sleep(jitter(GROUP_UI_SETTLE))
    elif not await node_helpers.poll_until(
        lambda: _group_tab_loaded(client),
        timeout=GROUP_TAB_LOAD_TIMEOUT,
        poll_interval=POLL_INTERVAL,
    ):
        # Carry on and read anyway, as the fixed settle did: the Leave Group button may still
        # answer, and an empty roster already reads as "cannot tell". But say so, because a
        # page that never loads is most likely a game update having moved the roster path.
        logger.warning(
            "Group tab did not load within %.0fs — reading it anyway (see tools/probe_group_roster.py)",
            GROUP_TAB_LOAD_TIMEOUT,
        )
    return True


async def _group_tab_loaded(client) -> bool:
    """Whether the Group page has loaded since the social window opened. Never raises.

    Read off the first roster line, which is blank until the page loads and never blank
    after: it holds a member when grouped, and the invitation placeholder
    (_ROSTER_PLACEHOLDERS) when not. That makes it a load signal that works in both states,
    which the Leave Group button — hidden when ungrouped — is not.
    """
    try:
        return bool(wizard_names.strip_markup(await node_helpers.get_text_by_path(client, group_member_name_path(1))))
    except Exception:
        logger.debug("Could not read group roster line 1", exc_info=True)
        return False


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
    """
    return tuple(name for _, name in await _roster_lines(client))


async def _roster_lines(client) -> list[tuple[int, str]]:
    """Each member on the Group tab with the roster line it is on. The social window must be open.

    The line is what teleport_to_group_member clicks. Never raises.

    Lines are read to GROUP_ROSTER_LINES rather than stopping at the first blank one,
    because an empty slot in the middle of the list would otherwise truncate the members
    after it. A line beyond the ones the game drew simply resolves to no window.
    """
    lines: list[tuple[int, str]] = []
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
            # The text itself is not logged: this check is all that stands between a
            # member's name and the log, and a new placeholder is found with the probe.
            logger.debug("Ignoring placeholder text on group roster line %d", line)
            continue
        lines.append((line, name))
    return lines


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
# in the member counts the log reports, and in the roster-disagreement warning for an
# ungrouped client. tools/probe_group_roster.py prints the raw text of every
# line, which is how to identify it; add it here.
_ROSTER_PLACEHOLDERS = frozenset({"left"})


async def read_group_state(client, confirm_grouped_timeout: float | None = None) -> GroupState:
    """Return this client's membership flag and group roster in one social-window visit.

    Membership is read from the *visibility* of the Leave Group button on the social
    window's Group tab. The game hides that button when you are not in a group rather than
    graying it, so visibility is the discriminator — confirmed with tools/probe_group_ui.py.

    Visibility specifically, not is_enabled_by_path: that helper returns True for any
    window that merely exists and is not grayed, and the whole SocialSystemsWindow subtree
    exists in the UI tree whether or not the window is open. Using it here reported every
    client as grouped, always.

    The roster is read on the same visit because the expensive part is getting the window
    open and the Group page loaded, and the roster adds only a handful of memory reads on
    top of a window that is already showing. Reading it separately would double the cost of
    the slowest and most failure-prone step for no benefit.

    `confirm_grouped_timeout` says "I am expecting this client to be grouped, wait up to
    this long for it". It replaces the wait for the page to load with a poll for the Leave
    Group button — see _open_social_window. Leave it None for a survey, where the answer is
    genuinely unknown and the page loading is what to wait for. Zero is distinct from None
    and still means poll the button: it is what a caller whose deadline has run out passes.

    Returns a blank state on any failure: the caller's next move is the same either way
    (try to add this client), and one unreadable client must not abort a regroup for the
    rest.
    """
    try:
        ready = None
        if confirm_grouped_timeout is not None:

            async def ready():
                return await node_helpers.is_visible_by_path(client, LEAVE_GROUP_BUTTON_PATH)

        if not await _open_social_window(client, ready=ready, ready_timeout=confirm_grouped_timeout or 0.0):
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
    memory reads: the window opening, the Group page loading, and the retry pauses inside
    close_social_window. Those overlap perfectly; only the ReadProcessMemory calls
    serialize on the loop thread.

    No return_exceptions is needed — read_group_state already absorbs every failure and
    answers a blank state, so one unreadable client cannot cancel the survey for the rest.
    """
    return list(
        await asyncio.gather(*(run_trace.traced(read_group_state(c), "group.read_state", client=c) for c in clients))
    )


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
        if not await _open_social_window(client, for_input=True):
            logger.debug("Social window did not open; cannot leave the group")
            return False
        if not await node_helpers.is_visible_by_path(client, LEAVE_GROUP_BUTTON_PATH):
            return True

        clicked = await node_helpers.click_by_path(client, LEAVE_GROUP_BUTTON_PATH)
        # Leaving is a server round-trip, so the button does not vanish on the click.
        left = await node_helpers.poll_until(
            lambda: _left_the_group(client),
            timeout=GROUP_VERIFY_TIMEOUT,
            poll_interval=POLL_INTERVAL,
        )
        if not left:
            logger.debug(
                "Leave Group button still showing %.0fs after the click (click sent: %s)", GROUP_VERIFY_TIMEOUT, clicked
            )
        return left
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
    safety_delay: float | None = None,
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

    `safety_delay` is how long to wait for the social window the game opens on the leader
    when the first invitation forms the group. It defaults to the app's Safety Delay setting.

    Returns a GroupResult instead of raising. Callers decide what a partial failure means:
    the node fails the run, the automatic hooks log and carry on.

    Serialized against other form_group calls — see _form_group_lock. Callers may await
    this concurrently; the second simply waits and then finds the group already built.
    """

    def _log(msg: str, level: int = logging.INFO) -> None:
        # One or the other, never both: a node's machine.log files the line under the node,
        # and writing it here as well put every grouping line in the session log twice.
        if log is not None:
            log(msg, level)
        else:
            logger.log(level, msg)

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
        if safety_delay is None:
            safety_delay = SettingsManager.instance().default_safety_delay
        return await _form_group_locked(clients, names, leader_index, _log, None if contended else states, safety_delay)


def _in_leader_group(state: GroupState, leader_state: GroupState) -> bool:
    """Whether this client is demonstrably in the leader's group, not merely in *a* group.

    Falls back to the plain membership flag when either roster is unreadable, which is the
    behavior this had before rosters existed: assume a grouped client is in our group.
    Guessing the other way would tear down a working group whenever a roster read failed.
    """
    if not state.grouped:
        return False
    if not state.roster or not leader_state.roster:
        return True
    return _roster_key(state) == _roster_key(leader_state)


def _final_states(
    states: list[GroupState],
    leader_idx: int,
    joined: set[int],
    names: list[str | None],
) -> list[GroupState]:
    """Every client's group state after a regroup, without opening a social window to read it.

    `states` is the survey as _dissolve_other_groups left it. The leader's group is the
    leader, the clients already in it, and the ones that joined — each join was confirmed
    by the Leave Group button and a roster listing the leader, so nothing here is a guess
    that the regroup did not check. They share one roster: the leader's as surveyed, plus
    our own clients' names. That can miss a stranger who joined in the meantime, but the
    port map, the one reader, keeps only pairs of our own clients.

    Everyone else keeps the state already read for them: a client freed by a dissolve reads
    ungrouped, and one whose invitation failed reads whatever it did before.
    """
    leader_state = states[leader_idx]
    members = [
        idx
        for idx in range(len(states))
        if idx == leader_idx or idx in joined or _in_leader_group(states[idx], leader_state)
    ]
    if len(members) < 2:
        return list(states)

    roster: list[str] = []
    for name in (*leader_state.roster, *(names[idx] for idx in members)):
        if name and not _lists(tuple(roster), name):
            roster.append(name)
    group = GroupState(grouped=True, roster=tuple(roster))
    return [group if idx in members else state for idx, state in enumerate(states)]


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
        size = len(states[members[0]].roster)
        ours = ", ".join(slot_label(idx) for idx in members)
        # Worth saying out loud: a group can contain players who are not ours, and they
        # lose their group here too. Counted rather than listed, so no name reaches the log.
        _log(
            f"Dissolving the group holding {ours} ({size} members) so its members can join the leader's",
            logging.WARNING,
        )
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
    safety_delay: float,
) -> GroupResult:
    """Body of form_group. Must only be called while _form_group_lock is held."""
    if states is None:
        states = await read_group_states(clients)
    if all(state.grouped for state in states) and same_group(states) is not False:
        _log("All clients are already in a group — nothing to do", logging.DEBUG)
        return GroupResult(success=True, states=list(states))

    leader_idx = _choose_leader(states, leader_index)
    leader = clients[leader_idx]
    _log(f"Rebuilding group: leader is {slot_label(leader_idx)}")

    result = GroupResult(success=True)

    # An invitation to a player who is already in a group is refused by the game, so any
    # client sitting in a different group has to be freed before it can be invited across.
    before = states
    states = await _dissolve_other_groups(clients, states, leader_idx, _log)
    result.left = [slot_label(i) for i in range(len(clients)) if before[i].grouped and not states[i].grouped]
    leader_state = states[leader_idx]

    to_invite: list[int] = []
    for idx in range(len(clients)):
        if idx == leader_idx or _in_leader_group(states[idx], leader_state):
            continue
        if not names[idx]:
            _log(f"{slot_label(idx)} has no known wizard name — cannot invite it", logging.WARNING)
            result.success = False
            result.failed.append(slot_label(idx))
            continue
        to_invite.append(idx)

    joined = await _invite_all(
        leader, clients, names, to_invite, names[leader_idx], _log, safety_delay if not leader_state.grouped else None
    )
    result.added = [slot_label(idx) for idx in to_invite if idx in joined]
    result.failed += [slot_label(idx) for idx in to_invite if idx not in joined]
    if len(joined) < len(to_invite):
        result.success = False
    result.states = _final_states(states, leader_idx, joined, names)

    # Close the friends list once, after every invite — it is reused between them, so
    # closing per-invite would mean reopening and re-paginating it for each client.
    #
    # The profile popup and the social window both sit on top of the friends list, and a
    # close click aimed at a covered button lands on whatever is above it. Clear them
    # first: the social window opened on the leader when the group formed, and nothing
    # since has closed it.
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


async def _invite_all(
    leader,
    clients: list,
    names: list[str | None],
    to_invite: list[int],
    leader_name: str | None,
    log,
    social_window_wait: float | None = None,
) -> set[int]:
    """Invite every client in `to_invite` into the leader's group. Returns the ones that joined.

    Each round runs in two phases (#128):

      1. The leader sends every invitation, one after another, waiting only for each to
         reach its target. It has one friends list and one mouse, so this part is serial.
      2. Every invited client accepts and confirms, all at once. Accepts are independent
         of each other, and each client's input goes to its own window.

    Waiting for one invitee to join before inviting the next made every invite pay the
    whole server round trip and join confirmation in turn; now a regroup pays it once.

    Accepts are held back until every invitation is out, rather than each accepted as soon
    as it lands, because the first accept forms the group, and the game opens the social
    window on the leader when it does. Mid-send, that window would cover the friends list
    and profile popup the leader is clicking through. The leader's windows are also cleared
    before every send (see _send_invite), which covers the window opening on the send
    itself, and a retry round starting after the group formed.

    A client that did not join is retried in the next round, up to GROUP_ADD_RETRIES. One
    the game refused, or that is not in the friends list, drops out at once: retrying cannot
    fix either.

    `social_window_wait` is set when the leader is not yet in a group. The first invitation
    then forms one, and the game opens the social window on the leader a moment later; the
    first send waits that long for it (see _send_invite). Once an invitation has gone out,
    the leader is grouped and no later send waits.
    """
    joined: set[int] = set()
    pending = list(to_invite)
    for attempt in range(1, GROUP_ADD_RETRIES + 1):
        sent: list[int] = []
        for idx in list(pending):
            # The span is the invitee's; the inputs in it are the leader's, and carry its slot.
            with run_trace.span("group.send_invite", client=clients[idx], attempt=attempt):
                outcome = await _try_send(leader, clients[idx], names[idx], slot_label(idx), log, social_window_wait)
            if outcome is _SENT:
                sent.append(idx)
                social_window_wait = None
            elif outcome is _GIVE_UP:
                pending.remove(idx)

        accepted = await asyncio.gather(
            *(
                run_trace.traced(
                    _accept_invite(clients[i], slot_label(i), log, leader_name), "group.accept", client=clients[i]
                )
                for i in sent
            )
        )
        for idx, ok in zip(sent, accepted):
            if ok:
                log(f"Added {slot_label(idx)} to the group")
                joined.add(idx)
                pending.remove(idx)

        if not pending:
            break
        log(
            f"Attempt {attempt}/{GROUP_ADD_RETRIES} to add {', '.join(slot_label(i) for i in pending)} "
            "did not take effect",
            logging.DEBUG,
        )

    for idx in pending:
        log(f"Gave up adding {slot_label(idx)} to the group after {GROUP_ADD_RETRIES} attempts", logging.WARNING)
    return joined


# _try_send outcomes.
_SENT = "sent"
_RETRY = "retry"
_GIVE_UP = "give up"


async def _try_send(leader, target, name: str, label: str, log, social_window_wait: float | None = None) -> str:
    """Send one invitation, turning each way it can fail into what the round should do.

    `name` is what the friends list is searched for; `label` is what the log calls the
    target. They are kept apart so a wizard name never reaches a log line.
    """
    try:
        return _SENT if await _send_invite(leader, target, name, label, log, social_window_wait) else _RETRY
    except AlreadyInAnotherGroup:
        log(f"{label} is already in another group and must leave it before joining this one", logging.WARNING)
        return _GIVE_UP
    except ValueError:
        # Raised by the friends-list helpers: name not present, or nobody online. Their
        # message quotes the name searched for, so it is not passed on.
        log(f"Could not find {label} in the leader's online friends list", logging.WARNING)
        return _GIVE_UP
    except Exception:
        logger.warning("Unexpected error inviting %s to the group", label, exc_info=True)
        return _RETRY


async def _send_invite(leader, target, name: str, label: str, log, social_window_wait: float | None = None) -> bool:
    """Invite one client, and return True once the invitation is showing on it. Does not accept it.

    The leader's social window and friend profile are cleared before the send as well as
    after. The social window opens on the leader by itself when the group forms, and on top
    of the friends list: left there, it takes the click meant for the next friend's row.

    `social_window_wait` is given when this invitation forms the group. The window opens a
    moment after the invitation is out, so a close that runs at once can read it before it
    exists and leave it open; the send waits up to that long for it to show, and the close
    in the `finally` then finds it.
    """
    await _close_overlapping_windows(leader)
    try:
        async with leader.mouse_handler:
            await node_helpers.open_friend_profile(leader, name)
            # The profile popup is drawn locally, so it is normally up within a frame or
            # two — polled rather than slept through. A timeout here is not fatal on its
            # own: the click below then fails and reports what it actually found, which is a
            # better diagnosis than "the popup was late".
            settled = None
            if await node_helpers.poll_until(
                lambda: node_helpers.is_visible_by_path(leader, CHARACTER_PROFILE_PATH),
                timeout=FRIEND_PROFILE_TIMEOUT,
                poll_interval=POLL_INTERVAL,
            ):
                # Visible is not laid out: aimed at the first positions read, the click can
                # land on the wishlist button below Invite to Group (#280).
                settled, reads = await node_helpers.await_settled_friend_profile(leader)
                if settled is None:
                    log(
                        f"Profile buttons for {label} were still moving after "
                        f"{FRIEND_PROFILE_SETTLE_TIMEOUT:.0f}s; clicking anyway",
                        logging.DEBUG,
                    )
                elif reads > 2:
                    log(
                        f"Profile buttons for {label} moved after the popup showed; settled after {reads} reads",
                        logging.DEBUG,
                    )
            else:
                log(f"Friend profile for {label} did not appear within {FRIEND_PROFILE_TIMEOUT:.0f}s", logging.DEBUG)

            # Read straight before the click and again straight after, so a click that lands on
            # the wrong button (#280) shows which one was where, and whether the layout moved.
            before, before_text = (
                (settled, node_helpers.profile_layout_text(settled))
                if settled
                else await node_helpers.read_friend_profile(leader)
            )
            log(f"Profile buttons before the invite click for {label}: {before_text}", logging.DEBUG)
            if not await node_helpers.click_by_path(leader, INVITE_TO_GROUP_BUTTON_PATH):
                diagnosis = await node_helpers.diagnose_path(leader, INVITE_TO_GROUP_BUTTON_PATH)
                log(f"Group invite button unavailable for {label}: {diagnosis}", logging.DEBUG)
                return False
            after, after_text = await node_helpers.read_friend_profile(leader)
            log(f"Profile buttons after the invite click for {label}: {after_text}", logging.DEBUG)
            aimed = node_helpers.profile_button_rect(before, _INVITE_BUTTON)
            now = node_helpers.profile_button_rect(after, _INVITE_BUTTON)
            if aimed and now and aimed != now:
                # Not treated as a miss: the click may still have landed first. The outcome
                # wait below decides; this says why it failed if it does.
                log(
                    f"Invite to Group moved under the click for {label}, from {node_helpers.rect_text(aimed)} to "
                    f"{node_helpers.rect_text(now)}, so the click may have hit another button",
                    logging.WARNING,
                )

        invited, refused = await _await_invite_outcome(leader, target)
        if refused:
            # The game rejects the invite outright if that player is already in a group —
            # someone else's, since form_group skips clients known to be in one. Dismiss the
            # popup so it does not block the next invite, and stop: retrying cannot help.
            await node_helpers.click_by_path(leader, ALREADY_IN_GROUP_DISMISS_PATH)
            raise AlreadyInAnotherGroup(f"{label} is already in another group")
        if not invited:
            log(f"No group invitation appeared for {label} within {GROUP_INVITE_TIMEOUT:.0f}s", logging.DEBUG)
            log(f"Leader's windows then: {await _visible_windows(leader)}", logging.DEBUG)
            log(f"Profile buttons then: {await node_helpers.friend_profile_buttons(leader)}", logging.DEBUG)
            return False
        if social_window_wait is not None and not await node_helpers.poll_until(
            lambda: node_helpers.is_visible_by_path(leader, SOCIAL_WINDOW_PATH),
            timeout=social_window_wait,
            poll_interval=POLL_INTERVAL,
        ):
            log(f"The social window did not open on the leader within {social_window_wait:.0f}s", logging.DEBUG)
        return True
    finally:
        # The friends list is deliberately left open — it is reused for the next invite and
        # closed once at the end of form_group.
        await _close_overlapping_windows(leader)


async def _accept_invite(target, label: str, log, leader_name: str | None) -> bool:
    """Accept the invitation showing on `target` and report whether it joined the leader's group.

    Never raises: this runs alongside the other invitees' accepts, and one failing must not
    cancel the rest.
    """
    try:
        # The prompt was showing when the invite was sent, but the other sends have run since.
        # Checked so a prompt that has gone reads as a failed attempt, not a blind click.
        if not await node_helpers.is_visible_by_path(target, GROUP_INVITE_ACCEPT_PATH):
            log(f"The group invitation to {label} was gone before it could be accepted", logging.DEBUG)
            return False
        await node_helpers.click_by_path(target, GROUP_INVITE_ACCEPT_PATH)

        # Authoritative check: the Leave Group button is only visible when actually grouped.
        state = await _confirm_joined(target)
        if not state.grouped:
            return False

        # Grouped is not the same as grouped WITH US. The target could have accepted
        # somebody else's invitation in the moment between our click and this read, which
        # the flag alone cannot see. The roster can: the leader has to be listed on it.
        if leader_name and state.roster and not _lists(state.roster, leader_name):
            log(
                f"{label} joined a group that does not contain the leader ({len(state.roster)} members)",
                logging.WARNING,
            )
            return False
        return True
    except Exception:
        logger.warning("Unexpected error accepting the group invitation on %s", label, exc_info=True)
        return False
    finally:
        # The game opens the social window on a client that joins, and it does not close
        # itself. _confirm_joined's read closes it on the success path; this covers the rest.
        await close_social_window(target)


async def _await_invite_outcome(leader, target) -> tuple[bool, bool]:
    """Wait for the invite to land, returning (prompt_showing, refused).

    Two things can follow an "Add to Group" click, and they are mutually exclusive: the
    invitation prompt appears on the TARGET, or the game tells the LEADER that player is
    already in a group. Both are polled together rather than one after the other.

    The refusal used to be checked after a fixed sleep — absence being the expected outcome,
    there is nothing to poll for on its own — which put a flat second under every successful
    invite for the sake of a case that almost never happens. Racing it against the
    prompt removes that floor without giving up the check: whichever window appears first
    ends the wait, and a refusal is still noticed as soon as it is drawn.

    PartyInviteWindow is specific to group invitations, so unlike the shared
    MessageBoxModalWindow there is no risk of confirming some unrelated dialog that
    happened to be open. The prompt is server-relayed, so it lags the click.

    The target is checked first each cycle: it is the common outcome, and ruling it out
    costs one memory read.
    """
    refused = False

    async def _settled() -> bool:
        nonlocal refused
        if await node_helpers.is_visible_by_path(target, GROUP_INVITE_ACCEPT_PATH):
            return True
        refused = await node_helpers.is_visible_by_path(leader, ALREADY_IN_GROUP_DISMISS_PATH)
        return refused

    settled = await node_helpers.poll_until(
        _settled,
        timeout=GROUP_INVITE_TIMEOUT,
        poll_interval=POLL_INTERVAL,
    )
    return settled and not refused, refused


async def _confirm_joined(target) -> GroupState:
    """Wait for an invited client to actually be in a group, and read its roster.

    Two signals, both polled:

      * the game opens the social window by itself on joining, which is the first sign the
        accept was processed. Only a hint — but waiting for it also keeps our own HUD-button
        click from racing the game's open and toggling the window straight back shut.
      * the Leave Group button, which is authoritative.

    They share one GROUP_VERIFY_TIMEOUT budget rather than taking one each, so an accept
    that never went through still costs a single timeout. The hint takes a short slice of it
    (GROUP_JOIN_HINT_TIMEOUT) and hands the remainder to the check that decides.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + GROUP_VERIFY_TIMEOUT
    await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(target, SOCIAL_WINDOW_PATH),
        timeout=min(GROUP_JOIN_HINT_TIMEOUT, GROUP_VERIFY_TIMEOUT),
        poll_interval=POLL_INTERVAL,
    )
    return await read_group_state(target, confirm_grouped_timeout=max(deadline - loop.time(), 0.0))


def _lists(roster: tuple[str, ...], name: str) -> bool:
    """Whether `name` appears on this roster, compared case-insensitively."""
    return name.casefold() in {member.casefold() for member in roster}


_INVITE_BUTTON = INVITE_TO_GROUP_BUTTON_PATH[-1]


async def _visible_windows(client) -> str:
    """Report which of the windows involved here are currently visible.

    Diagnostic only. A close click that lands on a covering window fails silently, so when
    a close does time out the useful question is "what else was on screen?". The game's
    shared message box is included because it is the one window grouping never opens on
    purpose that can still sit on top of the others.
    """
    checks = {
        "wndCharacter": CHARACTER_PROFILE_PATH,
        "SocialSystemsWindow": SOCIAL_WINDOW_PATH,
        "PermanentShopModalWindow": CROWNS_SHOP_WINDOW_PATH,
        **{path[-1]: path for path in FRIENDS_LIST_PATHS},
    }
    parts = []
    for label, path in checks.items():
        try:
            parts.append(f"{label}={await node_helpers.is_visible_by_path(client, path)}")
        except Exception:
            parts.append(f"{label}=<error>")
    # Several boxes share this name, in different places; any of them counts here
    try:
        parts.append(
            f"MessageBoxModalWindow={await node_helpers.is_named_window_visible(client, 'MessageBoxModalWindow')}"
        )
    except Exception:
        parts.append("MessageBoxModalWindow=<error>")
    return ", ".join(parts)


async def _close_overlapping_windows(client) -> None:
    """Close the windows that can sit on top of the friends list. Never raises.

    The friend profile popup, the social window the game opens on its own when a group
    forms, and the crowns shop, which an invite click that lands on the profile's wishlist
    button opens (#280). Left open they cover the HUD for every node that runs afterwards,
    and the friends list for the next invite.

    ORDER MATTERS. The crowns shop is a modal over everything, so it goes first: left up,
    it took every later click of a run, the spellbook's included, until the bot stopped.
    The social window opens on top of the friend profile, and it covers the profile's
    close button — a click aimed at that button lands on the social window instead,
    leaving the profile open. So the social window is closed next.

    Each close retries until the window is actually gone rather than trusting one click,
    because a click swallowed by an overlapping window leaves the next one aimed at the
    wrong place too. Each also checks first, so a window that is not open costs a read and
    no input.

    A profile that will not close is followed by a list of what else was showing, since a
    click that something on top took is the usual way this fails.
    """
    await _close_crowns_shop(client)
    await close_social_window(client)
    try:
        if not await node_helpers.click_until_gone(
            client,
            CHARACTER_PROFILE_CLOSE_PATH,
            is_open=lambda: node_helpers.is_visible_by_path(client, CHARACTER_PROFILE_PATH),
        ):
            logger.debug("Friend profile popup did not close. Visible windows: %s", await _visible_windows(client))
    except Exception:
        logger.debug("Could not close the friend profile popup", exc_info=True)


async def _close_crowns_shop(client) -> None:
    """Close the crowns shop if it is open, saying so. Never raises."""
    shop = await node_helpers.close_crowns_shop(client)
    if shop.was_open and shop.closed:
        logger.info("Closed the crowns shop, which was covering the friends list")
    elif shop.was_open:
        logger.warning("The crowns shop would not close, and it takes every click while it is open")


async def teleport_to_group_member(client, name: str) -> None:
    """Start a port to a member of this client's group. Raises ValueError if it cannot start.

    The group route to a player, for two of ours who are grouped but not friends (see
    bartlebot.clients.port_map): open the social window on the Group tab, click the member's roster
    line, and press Go To Player on the popup it opens. The same contract as
    node_helpers.teleport_via_friends_list, so node_helpers.port_clients can drive either.

    Waits out any form_group in progress first, since that opens and closes this same window
    on every client, but does not hold the lock itself: ports must not queue behind each other.
    """
    lock = _form_group_lock()
    if lock.locked():
        async with lock:
            pass

    if not await _open_social_window(client, for_input=True):
        raise ValueError("the social window did not open")
    wanted = name.casefold()
    line = next((ln for ln, member in await _roster_lines(client) if member.casefold() == wanted), None)
    if line is None:
        await close_social_window(client)
        raise ValueError("that player is not on this client's group roster")

    # Resolved once and clicked as found: re-walking the path to click could land elsewhere.
    rows = await node_helpers.find_windows_by_path(client.root_window, group_member_name_path(line))
    if not rows or not await node_helpers.click_window(client, rows[0]):
        await close_social_window(client)
        raise ValueError(f"group roster line {line} could not be clicked")
    if not await node_helpers.poll_until(
        lambda: node_helpers.is_visible_by_path(client, FRIENDLY_PLAYER_MENU_PATH),
        timeout=TRIGGER_TIMEOUT,
        poll_interval=POLL_INTERVAL,
    ):
        await close_social_window(client)
        raise ValueError("the player menu did not open")
    if not await node_helpers.click_by_path(client, FRIENDLY_PLAYER_GO_TO_BUTTON_PATH):
        await close_social_window(client)
        raise ValueError("the Go To Player button could not be clicked")


async def port_to_group_member(clients_to_move: list, name: str, **kwargs) -> node_helpers.PortToFriendResult:
    """Port every client to a member of their group, and wait for each to start loading.

    node_helpers.port_to_friend's counterpart through the Group tab, with the same arguments
    and result. The social window can stay open across the port, so it is closed on every
    client once the loading screens have cleared.
    """
    try:
        return await node_helpers.port_clients(
            clients_to_move, lambda client: teleport_to_group_member(client, name), **kwargs
        )
    finally:
        await node_helpers.poll_until(lambda: node_helpers.none_loading(clients_to_move), None, POLL_INTERVAL)
        await asyncio.gather(*(close_social_window(c) for c in clients_to_move))
