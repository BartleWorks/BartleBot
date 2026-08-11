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

Deliberately free of Qt and FSM dependencies so that the form_group node, AppController
and ClientManager can all call it, and so it can be tested without either.

Requires every account to be mutually friended in-game — a one-time manual setup.
"""

import asyncio
import logging
import weakref
from dataclasses import dataclass, field

from bartlebot.nodes import node_helpers
from bartlebot.nodes.paths import (
    ALREADY_IN_GROUP_DISMISS_PATH,
    CHARACTER_PROFILE_CLOSE_PATH,
    CHARACTER_PROFILE_PATH,
    FRIENDS_LIST_WINDOW_NAMES,
    GROUP_INVITE_ACCEPT_PATH,
    GROUP_WINDOW_CLOSE_PATH,
    INVITE_TO_GROUP_BUTTON_PATH,
    LEAVE_GROUP_BUTTON_PATH,
    SOCIAL_BUTTON_PATH,
    SOCIAL_GROUP_TAB_PATH,
    SOCIAL_WINDOW_PATH,
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


@dataclass
class GroupResult:
    """Outcome of a form_group call."""

    success: bool
    added: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    reason: str = ""

    def summary(self) -> str:
        if self.success and not self.added:
            return "all clients were already grouped"
        parts = []
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
    """
    if not await node_helpers.is_visible_by_path(client, SOCIAL_WINDOW_PATH):
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


async def is_in_group(client) -> bool:
    """Return True when this client is currently in a group.

    Read from the *visibility* of the Leave Group button on the social window's Group tab.
    The game hides that button when you are not in a group rather than graying it, so
    visibility is the discriminator — confirmed with tools/probe_group_ui.py.

    Visibility specifically, not is_enabled_by_path: that helper returns True for any
    window that merely exists and is not grayed, and the whole SocialSystemsWindow subtree
    exists in the UI tree whether or not the window is open. Using it here reported every
    client as grouped, always.

    This opens and closes the window, so it costs a few clicks — call it to make a
    decision, not in a polling loop.

    Returns False on any failure: the caller's next move is the same either way (try to
    add this client), and one unreadable client must not abort a regroup for the rest.
    """
    try:
        if not await _open_social_window(client):
            logger.debug("Social window did not open; treating client as ungrouped")
            return False
        return await node_helpers.is_visible_by_path(client, LEAVE_GROUP_BUTTON_PATH)
    except Exception:
        logger.debug("Could not read group membership", exc_info=True)
        return False
    finally:
        await close_social_window(client)


async def read_group_flags(clients: list) -> list[bool]:
    """Return whether each client is in a group.

    Checked sequentially rather than gathered: each check drives the UI with a hotkey and
    clicks, and WizWalker's reads are synchronous on the event loop anyway, so running them
    concurrently would interleave keypresses across clients for no speed gain.
    """
    return [await is_in_group(c) for c in clients]


async def is_grouped(clients: list) -> bool:
    """Return True when every client reports being in a group.

    A single client is never considered grouped — a group of one confers no benefit, and
    treating it as grouped would suppress the regroup that adds the others back.

    Note this cannot prove they are in the SAME group; the UI exposes only a per-client
    yes/no. In practice a client in someone else's group is caught at invite time, when
    the game reports it is already grouped and the add is recorded as a failure.
    """
    if len(clients) < 2:
        return False
    return all(await read_group_flags(clients))


def _choose_leader(flags: list[bool], leader_index: int) -> int:
    """Return the index of the client that should send invitations.

    Prefers a client that is already in a group, so a group that merely lost one member is
    rebuilt by inviting that member back rather than by tearing the existing group down.
    Falls back to `leader_index` when nobody is grouped (e.g. at bot start, or when the
    leader itself crashed and the group dissolved).
    """
    if flags[leader_index]:
        return leader_index
    for idx, grouped in enumerate(flags):
        if grouped:
            return idx
    return leader_index


async def form_group(
    clients: list,
    names: list[str | None],
    leader_index: int = 0,
    log=None,
    flags: list[bool] | None = None,
) -> GroupResult:
    """Ensure every client is in one shared group, inviting whoever is missing.

    `names` holds each client's wizard name, positionally aligned with `clients`; a None
    entry means the name is unknown and that client cannot be located in the leader's
    friends list, so it is reported as failed rather than silently skipped.

    `flags` optionally supplies membership already read by the caller, saving a second
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
    # flags the caller read beforehand are stale and must be re-read inside the lock.
    contended = lock.locked()
    if contended:
        _log("Another group formation is already running — waiting for it to finish", logging.DEBUG)
    async with lock:
        return await _form_group_locked(clients, names, leader_index, _log, None if contended else flags)


async def _form_group_locked(
    clients: list,
    names: list[str | None],
    leader_index: int,
    _log,
    flags: list[bool] | None,
) -> GroupResult:
    """Body of form_group. Must only be called while _form_group_lock is held."""
    if flags is None:
        flags = await read_group_flags(clients)
    if all(flags):
        _log("All clients are already in a group — nothing to do", logging.DEBUG)
        return GroupResult(success=True)

    leader_idx = _choose_leader(flags, leader_index)
    leader = clients[leader_idx]
    _log(f"Rebuilding group: leader is client {leader_idx} ({names[leader_idx] or 'unknown'})")

    result = GroupResult(success=True)

    for idx, client in enumerate(clients):
        if idx == leader_idx or flags[idx]:
            # Already grouped. If it is in a *different* group the invite would be
            # rejected anyway, and the game tells us so at invite time.
            continue

        name = names[idx]
        if not name:
            _log(f"Client {idx} has no known wizard name — cannot invite it", logging.WARNING)
            result.success = False
            result.failed.append(f"client {idx}")
            continue

        if await _add_one(leader, client, name, _log):
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


async def _add_one(leader, target, name: str, log) -> bool:
    """Invite one client into the leader's group. Returns True once the UI confirms it."""
    for attempt in range(1, GROUP_ADD_RETRIES + 1):
        try:
            if await _invite_and_accept(leader, target, name, log):
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


async def _invite_and_accept(leader, target, name: str, log) -> bool:
    """Run one full invite exchange and report whether the target ended up grouped."""
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
    return await is_in_group(target)


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
