"""Async recording workflows for the node editor's Record button.

A workflow follows the player through one kind of step and fills the param form
with what it captured. There are three, and every recordable node is one of them
with its own param keys and prompt text:

  * ``_WalkIn`` — walk into a zone change (``enter_zone``, ``enter_dungeon``).
  * ``_Interact`` — walk up to something and use it, optionally through a loading
    screen (``interact``, ``interact_menu``, ``apply_level_scaling``,
    ``enter_zone_via_object``).
  * ``_Point`` — the zone and the player's position, at once.
  * ``_Enemy`` — the mob closest to the player, by name (``handle_combat``).

Adding a new recordable node type is one ``_REGISTRY`` entry: a workflow and the
keys it fills. ``RECORDABLE_NODES`` and the dispatch in ``run_recording`` follow.
"""

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bartlebot import entity_names
from bartlebot.nodes import node_helpers
from bartlebot.nodes.paths import NPC_RANGE_TITLE_PATH
from bartlebot.nodes.timing import RECORDING_POLL_INTERVAL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Session guard — enforces at most one active recording across the entire app
# ---------------------------------------------------------------------------

# The currently running recording task, or None.  This is module-level so
# the invariant holds regardless of how many _ParamPanel instances exist.
_active_task: asyncio.Task | None = None


def is_recording() -> bool:
    """Return True if a recording session is currently active."""
    return _active_task is not None and not _active_task.done()


def cancel_active_recording() -> None:
    """Cancel the currently active recording session, if any.

    Clears the module-level guard immediately so a new session can start
    without waiting for the cancelled coroutine to process CancelledError.
    The cancelled coroutine's own cleanup callbacks (on_btn/on_status) will
    still fire but are harmless at that point.
    """
    global _active_task
    task = _active_task
    _active_task = None
    if task is not None and not task.done():
        task.cancel()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_recording(
    node_type: str,
    client: Any,
    param_form: Any,
    on_status: Callable[[str], None],
    on_btn: Callable[[str], None],
    try_set_label: Callable[[str], None],
    *,
    player: int | None = None,
) -> None:
    """Dispatch to the recording workflow for *node_type*.

    *player* is the 1-based number of the player *client* is, for a workflow that
    records who performed the step; None when the caller cannot say.

    At most one session runs at a time.  Returns immediately if another
    session is already active.
    """
    global _active_task
    fn = _REGISTRY.get(node_type)
    if fn is None:
        return
    if is_recording():
        return
    my_task = asyncio.current_task()
    _active_task = my_task
    try:
        await fn.record(client, param_form, on_status, on_btn, try_set_label, player)
    finally:
        # Only clear if we're still the registered task; a cancel followed by
        # an immediate new session could have already replaced _active_task.
        if _active_task is my_task:
            _active_task = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _zone_display(zone: str | None) -> str:
    """Return a human-friendly display name for a zone path like 'WizardCity/WC_Hub'."""
    if zone is None:
        return "(unknown zone)"
    try:
        from wizsprinter import toZone, toZoneDisplayName  # noqa: PLC0415

        return toZoneDisplayName(toZone(zone))
    except Exception:
        return zone.split("/")[-1] if "/" in zone else zone


#: The labels Record writes, other than a bare zone: ``Interact with …``, ``Defeat …``,
#: and a zone change's ``A → B``.
_RECORDED_LABEL = re.compile(r"^(?:Interact with |Defeat ).+|.+ → .+$")
#: What ``_zone_display`` falls back to, and so what an older recording left as the label:
#: a zone path's last segment, like ``WC_Hub`` — one word joined by underscores, which a
#: label typed by hand almost never is.
_ZONE_SEGMENT_LABEL = re.compile(r"^\w+_\w+$")


def is_recorded_label(label: str) -> bool:
    """Whether *label* has the shape of one Record wrote, so recording again may replace it.

    Recognised by shape rather than remembered, so a label recorded in an earlier session,
    or before this rule existed, is still replaced; one the author typed is kept.
    """
    label = label.strip()
    return label == _zone_display(None) or bool(_RECORDED_LABEL.match(label) or _ZONE_SEGMENT_LABEL.match(label))


async def _npc_range_title(client) -> str:
    """The interact prompt's title — the name of what the player is in range of — or ''."""
    try:
        return node_helpers.strip_html(await node_helpers.get_text_by_path(client, NPC_RANGE_TITLE_PATH))
    except Exception:
        logger.debug("Could not read the interact prompt's title", exc_info=True)
        return ""


async def _wait_for_zone_name(client, poll: float, timeout: float = 10.0) -> str | None:
    """Poll until zone_name() returns a non-None value after a loading screen."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        zone = await client.zone_name()
        if zone is not None:
            return zone
        if asyncio.get_event_loop().time() >= deadline:
            return None
        await asyncio.sleep(poll)


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------


def _set_position(param_form, key: str, pos) -> None:
    param_form.set_xyz(key, pos.x, pos.y, pos.z)


async def _wait_out_loading(client) -> None:
    while await client.is_loading():
        await asyncio.sleep(RECORDING_POLL_INTERVAL)


@dataclass(frozen=True, kw_only=True)
class _Workflow:
    """The session every workflow shares: the Stop button while it runs, and the
    Record button back however it ends — done, cancelled, or failed.

    A workflow writes each value as it captures it, so the author can watch the
    point follow the player. A session that does not finish puts every field it
    fills back as it was: half a recording is values that were never recorded
    together, and nothing in the form would say which half is which.
    """

    #: Whether the session shows Stop while it runs. An instant capture has nothing
    #: to stop.
    shows_stop: bool = True

    async def record(self, client, param_form, on_status, on_btn, try_set_label, player=None) -> None:
        saved = param_form.snapshot(self.keys())
        if self.shows_stop:
            on_btn("Stop")
            on_status("Starting…")
        try:
            on_status(await self.capture(client, param_form, on_status, try_set_label, player))
            on_btn("Record")
        except asyncio.CancelledError:
            param_form.restore(saved)
            on_status("")
            on_btn("Record")
        except Exception as e:
            param_form.restore(saved)
            on_status(f"Error: {e}")
            on_btn("Record")

    def keys(self) -> list[str]:
        """Every param key ``capture`` may write."""
        raise NotImplementedError

    async def capture(self, client, param_form, on_status, try_set_label, player) -> str:
        """Fill *param_form* and return the closing status. *player* is as ``run_recording``'s."""
        raise NotImplementedError


@dataclass(frozen=True, kw_only=True)
class _WalkIn(_Workflow):
    """Walk into a zone change.

    The zone and the starting position become the reset; the player is followed
    until the loading screen, and the last position before it is the trigger point.
    The zone read after loading is the destination.
    """

    zone_key: str
    point_key: str
    destination_key: str
    approach: str

    def keys(self) -> list[str]:
        return [self.zone_key, "reset_xyz", self.point_key, self.destination_key]

    async def capture(self, client, param_form, on_status, try_set_label, player) -> str:
        from_zone = await client.zone_name()
        param_form.set_str(self.zone_key, from_zone)
        last_pos = await client.body.position()
        _set_position(param_form, "reset_xyz", last_pos)
        on_status(self.approach)

        while True:
            await asyncio.sleep(RECORDING_POLL_INTERVAL)
            if await client.is_loading():
                _set_position(param_form, self.point_key, last_pos)
                on_status("Loading…")
                break
            last_pos = await client.body.position()
            _set_position(param_form, self.point_key, last_pos)

        await _wait_out_loading(client)
        to_zone = await _wait_for_zone_name(client, RECORDING_POLL_INTERVAL)
        param_form.set_str(self.destination_key, to_zone)
        try_set_label(f"{_zone_display(from_zone)} → {_zone_display(to_zone)}")
        return f"Done — {_zone_display(to_zone)}"


@dataclass(frozen=True, kw_only=True)
class _Interact(_Workflow):
    """Walk up to something and use it.

    The zone and the starting position become the reset. Once the interact prompt
    shows, the player's position is taken as the interaction point on every poll
    until the prompt goes — the last in-range position wins. With a
    *destination_key*, the workflow then waits out the loading screen that follows
    and reads the zone it lands in.

    With *names_target*, the prompt's title — what the player is in range of — names the
    node ``Interact with …``, in the author's display language; the zone names it when the
    title cannot be read. With *players_key*, an empty player list becomes the player the
    step was recorded from; one the author already filled is left alone.
    """

    zone_key: str = "zone"
    approach: str = "Walk to interactable…"
    finish: str = "Interact to finish…"
    destination_key: str | None = None
    #: What is left for the author to do by hand, said in the closing status.
    todo: str | None = None
    names_target: bool = False
    players_key: str | None = None

    def keys(self) -> list[str]:
        keys = [self.zone_key, "reset_xyz", "interact_xyz"]
        if self.players_key is not None:
            keys.append(self.players_key)
        return keys if self.destination_key is None else [*keys, self.destination_key]

    async def capture(self, client, param_form, on_status, try_set_label, player) -> str:
        zone = await client.zone_name()
        param_form.set_str(self.zone_key, zone)
        _set_position(param_form, "reset_xyz", await client.body.position())
        on_status(self.approach)

        while not await client.is_in_npc_range():
            await asyncio.sleep(RECORDING_POLL_INTERVAL)

        on_status(self.finish)
        target = ""
        while True:
            _set_position(param_form, "interact_xyz", await client.body.position())
            if self.names_target:
                # Read on every poll: the prompt can show before its title is filled in.
                target = await _npc_range_title(client) or target
            await asyncio.sleep(RECORDING_POLL_INTERVAL)
            if not await client.is_in_npc_range():
                break

        if self.players_key is not None and player is not None and not param_form.players(self.players_key):
            param_form.set_players(self.players_key, [player])

        if self.destination_key is None:
            try_set_label(f"Interact with {target}" if target else _zone_display(zone))
            where = zone.split("/")[-1]
            return f"Done — {self.todo} ({where})" if self.todo else f"Done — {where}"

        on_status("Loading…")
        while not await client.is_loading():
            await asyncio.sleep(RECORDING_POLL_INTERVAL)
        await _wait_out_loading(client)
        to_zone = await _wait_for_zone_name(client, RECORDING_POLL_INTERVAL)
        param_form.set_str(self.destination_key, to_zone)
        try_set_label(f"{_zone_display(zone)} → {_zone_display(to_zone)}")
        return f"Done — {_zone_display(to_zone)}"


@dataclass(frozen=True, kw_only=True)
class _Point(_Workflow):
    """The zone and the player's position, captured at once. No reset."""

    sets_label: bool = False

    def keys(self) -> list[str]:
        return ["zone", "xyz"]

    async def capture(self, client, param_form, on_status, try_set_label, player) -> str:
        zone = await client.zone_name()
        pos = await client.body.position()
        param_form.set_str("zone", zone)
        _set_position(param_form, "xyz", pos)
        if self.sets_label:
            try_set_label(_zone_display(zone))
        return f"Done — {zone.split('/')[-1]}"


@dataclass(frozen=True, kw_only=True)
class _Enemy(_Workflow):
    """The mob closest to the player — the one their card shows — captured at once.

    Its object name fills *key*, the identity a fight node matches on; the name a player
    reads, where the game has one, names the node ``Defeat …``.
    """

    key: str = "enemy"
    shows_stop: bool = False

    def keys(self) -> list[str]:
        return [self.key]

    async def capture(self, client, param_form, on_status, try_set_label, player) -> str:
        mob = await client.find_closest_mob()
        name = await mob.object_name() if mob is not None else None
        if not name:
            return "No enemy nearby."
        param_form.set_str(self.key, name)
        shown = entity_names.label(name)
        try_set_label(f"Defeat {shown}")
        return f"Done — {shown}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, _Workflow] = {
    "enter_zone": _WalkIn(
        zone_key="from_zone", point_key="xyz", destination_key="to_zone", approach="Walk to transition…"
    ),
    "enter_dungeon": _WalkIn(
        zone_key="from_zone", point_key="sigil_xyz", destination_key="dungeon_zone", approach="Walk to sigil…"
    ),
    "interact": _Interact(names_target=True, players_key="interact_clients"),
    "interact_menu": _Interact(todo="set Menu Path manually", names_target=True, players_key="interact_clients"),
    "apply_level_scaling": _Interact(approach="Walk to level-scaling NPC…", finish="Leave NPC range to finish…"),
    "enter_zone_via_object": _Interact(
        zone_key="from_zone", finish="Interact to trigger zone change…", destination_key="to_zone"
    ),
    "teleport_to_coordinates": _Point(shows_stop=False),
    "mark_location": _Point(shows_stop=False),
    "teleport_to_marked_location": _Point(sets_label=True),
    "handle_combat": _Enemy(),
}

#: Frozenset of node_type strings that have a recording workflow.
#: Derived automatically from ``_REGISTRY`` — do not edit by hand.
RECORDABLE_NODES: frozenset[str] = frozenset(_REGISTRY)
