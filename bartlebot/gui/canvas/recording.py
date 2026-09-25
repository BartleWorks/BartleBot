"""Async recording workflows for the node editor's Record button.

A workflow follows the player through one kind of step and fills the param form
with what it captured. There are three, and every recordable node is one of them
with its own param keys and prompt text:

  * ``_WalkIn`` — walk into a zone change (``enter_zone``, ``enter_dungeon``).
  * ``_Interact`` — walk up to something and use it, optionally through a loading
    screen (``interact``, ``interact_menu``, ``apply_level_scaling``,
    ``enter_zone_via_object``).
  * ``_Point`` — the zone and the player's position, at once.

Adding a new recordable node type is one ``_REGISTRY`` entry: a workflow and the
keys it fills. ``RECORDABLE_NODES`` and the dispatch in ``run_recording`` follow.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bartlebot.nodes.timing import RECORDING_POLL_INTERVAL

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
) -> None:
    """Dispatch to the recording workflow for *node_type*.

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
        await fn.record(client, param_form, on_status, on_btn, try_set_label)
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

    async def record(self, client, param_form, on_status, on_btn, try_set_label) -> None:
        saved = param_form.snapshot(self.keys())
        if self.shows_stop:
            on_btn("Stop")
            on_status("Starting…")
        try:
            on_status(await self.capture(client, param_form, on_status, try_set_label))
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

    async def capture(self, client, param_form, on_status, try_set_label) -> str:
        """Fill *param_form* and return the closing status."""
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

    async def capture(self, client, param_form, on_status, try_set_label) -> str:
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
    """

    zone_key: str = "zone"
    approach: str = "Walk to interactable…"
    finish: str = "Interact to finish…"
    destination_key: str | None = None
    #: What is left for the author to do by hand, said in the closing status.
    todo: str | None = None

    def keys(self) -> list[str]:
        keys = [self.zone_key, "reset_xyz", "interact_xyz"]
        return keys if self.destination_key is None else [*keys, self.destination_key]

    async def capture(self, client, param_form, on_status, try_set_label) -> str:
        zone = await client.zone_name()
        param_form.set_str(self.zone_key, zone)
        _set_position(param_form, "reset_xyz", await client.body.position())
        on_status(self.approach)

        while not await client.is_in_npc_range():
            await asyncio.sleep(RECORDING_POLL_INTERVAL)

        on_status(self.finish)
        while True:
            _set_position(param_form, "interact_xyz", await client.body.position())
            await asyncio.sleep(RECORDING_POLL_INTERVAL)
            if not await client.is_in_npc_range():
                break

        if self.destination_key is None:
            try_set_label(_zone_display(zone))
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

    async def capture(self, client, param_form, on_status, try_set_label) -> str:
        zone = await client.zone_name()
        pos = await client.body.position()
        param_form.set_str("zone", zone)
        _set_position(param_form, "xyz", pos)
        if self.sets_label:
            try_set_label(_zone_display(zone))
        return f"Done — {zone.split('/')[-1]}"


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
    "interact": _Interact(),
    "interact_menu": _Interact(todo="set Menu Path manually"),
    "apply_level_scaling": _Interact(approach="Walk to level-scaling NPC…", finish="Leave NPC range to finish…"),
    "enter_zone_via_object": _Interact(
        zone_key="from_zone", finish="Interact to trigger zone change…", destination_key="to_zone"
    ),
    "teleport_to_coordinates": _Point(shows_stop=False),
    "mark_location": _Point(shows_stop=False),
    "teleport_to_marked_location": _Point(sets_label=True),
}

#: Frozenset of node_type strings that have a recording workflow.
#: Derived automatically from ``_REGISTRY`` — do not edit by hand.
RECORDABLE_NODES: frozenset[str] = frozenset(_REGISTRY)
