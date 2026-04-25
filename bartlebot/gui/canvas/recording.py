"""Async recording coroutines for the node editor's Record button.

Each function drives a specific node type through its recording workflow,
then populates the param form fields with the captured values.

Adding a new recordable node type:
  1. Write an ``async def _record_<name>(client, param_form, on_status, on_btn,
     try_set_label) -> None`` function below.
  2. Add an entry to ``_REGISTRY``.
  That's it — ``RECORDABLE_NODES`` and the dispatch in ``run_recording`` update
  automatically.
"""

import asyncio
from collections.abc import Callable
from typing import Any

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
    """Dispatch to the appropriate recording coroutine for *node_type*."""
    fn = _REGISTRY.get(node_type)
    if fn:
        await fn(client, param_form, on_status, on_btn, try_set_label)


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
# Recording coroutines
# ---------------------------------------------------------------------------


async def _record_enter_zone_gateway(client, param_form, on_status, on_btn, try_set_label) -> None:
    POLL = 0.15
    on_btn("Stop")
    on_status("Starting…")
    try:
        from_zone = await client.zone_name()
        param_form.set_str("from_zone", from_zone)
        start_pos = await client.body.position()
        param_form.set_xyz("reset_xyz", start_pos.x, start_pos.y, start_pos.z)
        on_status("Walk to transition…")

        last_pos = start_pos

        while True:
            await asyncio.sleep(POLL)
            if await client.is_loading():
                param_form.set_xyz("xyz", last_pos.x, last_pos.y, last_pos.z)
                on_status("Loading…")
                break
            last_pos = await client.body.position()
            param_form.set_xyz("xyz", last_pos.x, last_pos.y, last_pos.z)

        while await client.is_loading():
            await asyncio.sleep(POLL)

        to_zone = await _wait_for_zone_name(client, POLL)
        param_form.set_str("to_zone", to_zone)
        try_set_label(f"{_zone_display(from_zone)} → {_zone_display(to_zone)}")
        on_status(f"Done — {_zone_display(to_zone)}")
        on_btn("Record")

    except asyncio.CancelledError:
        on_status("")
        on_btn("Record")
    except Exception as e:
        on_status(f"Error: {e}")
        on_btn("Record")


async def _record_go_to_xyz(client, param_form, on_status, on_btn, try_set_label) -> None:
    try:
        zone = await client.zone_name()
        pos = await client.body.position()
        param_form.set_str("zone", zone)
        param_form.set_xyz("xyz", pos.x, pos.y, pos.z)
        on_status(f"Done — {zone.split('/')[-1]}")
        on_btn("Record")
    except asyncio.CancelledError:
        on_status("")
        on_btn("Record")
    except Exception as e:
        on_status(f"Error: {e}")
        on_btn("Record")


async def _record_enter_dungeon(client, param_form, on_status, on_btn, try_set_label) -> None:
    POLL = 0.15
    on_btn("Stop")
    on_status("Starting…")
    try:
        from_zone = await client.zone_name()
        param_form.set_str("from_zone", from_zone)
        start_pos = await client.body.position()
        param_form.set_xyz("reset_xyz", start_pos.x, start_pos.y, start_pos.z)
        on_status("Walk to sigil…")

        last_pos = start_pos

        while True:
            await asyncio.sleep(POLL)
            if await client.is_loading():
                param_form.set_xyz("sigil_xyz", last_pos.x, last_pos.y, last_pos.z)
                on_status("Loading…")
                break
            last_pos = await client.body.position()
            param_form.set_xyz("sigil_xyz", last_pos.x, last_pos.y, last_pos.z)

        while await client.is_loading():
            await asyncio.sleep(POLL)

        dungeon_zone = await _wait_for_zone_name(client, POLL)
        param_form.set_str("dungeon_zone", dungeon_zone)
        try_set_label(f"{_zone_display(from_zone)} → {_zone_display(dungeon_zone)}")
        on_status(f"Done — {_zone_display(dungeon_zone)}")
        on_btn("Record")

    except asyncio.CancelledError:
        on_status("")
        on_btn("Record")
    except Exception as e:
        on_status(f"Error: {e}")
        on_btn("Record")


async def _record_interact(client, param_form, on_status, on_btn, try_set_label) -> None:
    POLL = 0.15
    on_btn("Stop")
    on_status("Starting…")
    try:
        zone = await client.zone_name()
        param_form.set_str("zone", zone)
        start_pos = await client.body.position()
        param_form.set_xyz("reset_xyz", start_pos.x, start_pos.y, start_pos.z)
        on_status("Walk to interactable…")

        while not await client.is_in_npc_range():
            await asyncio.sleep(POLL)

        on_status("Interact to finish…")
        while True:
            pos = await client.body.position()
            param_form.set_xyz("interact_xyz", pos.x, pos.y, pos.z)
            await asyncio.sleep(POLL)
            if not await client.is_in_npc_range():
                break

        try_set_label(_zone_display(zone))
        on_status(f"Done — {zone.split('/')[-1]}")
        on_btn("Record")

    except asyncio.CancelledError:
        on_status("")
        on_btn("Record")
    except Exception as e:
        on_status(f"Error: {e}")
        on_btn("Record")


async def _record_apply_level_scaling(client, param_form, on_status, on_btn, try_set_label) -> None:
    POLL = 0.15
    on_btn("Stop")
    on_status("Starting…")
    try:
        zone = await client.zone_name()
        param_form.set_str("zone", zone)
        start_pos = await client.body.position()
        param_form.set_xyz("reset_xyz", start_pos.x, start_pos.y, start_pos.z)
        on_status("Walk to level-scaling NPC…")

        while not await client.is_in_npc_range():
            await asyncio.sleep(POLL)

        on_status("Leave NPC range to finish…")
        while True:
            pos = await client.body.position()
            param_form.set_xyz("interact_xyz", pos.x, pos.y, pos.z)
            await asyncio.sleep(POLL)
            if not await client.is_in_npc_range():
                break

        try_set_label(_zone_display(zone))
        on_status(f"Done — {zone.split('/')[-1]}")
        on_btn("Record")

    except asyncio.CancelledError:
        on_status("")
        on_btn("Record")
    except Exception as e:
        on_status(f"Error: {e}")
        on_btn("Record")


async def _record_enter_zone_interact(client, param_form, on_status, on_btn, try_set_label) -> None:
    POLL = 0.15
    on_btn("Stop")
    on_status("Starting…")
    try:
        from_zone = await client.zone_name()
        param_form.set_str("from_zone", from_zone)
        start_pos = await client.body.position()
        param_form.set_xyz("reset_xyz", start_pos.x, start_pos.y, start_pos.z)
        on_status("Walk to interactable…")

        while not await client.is_in_npc_range():
            await asyncio.sleep(POLL)

        on_status("Interact to trigger zone change…")
        while True:
            pos = await client.body.position()
            param_form.set_xyz("interact_xyz", pos.x, pos.y, pos.z)
            await asyncio.sleep(POLL)
            if not await client.is_in_npc_range():
                break

        on_status("Loading…")
        while not await client.is_loading():
            await asyncio.sleep(POLL)
        while await client.is_loading():
            await asyncio.sleep(POLL)

        to_zone = await _wait_for_zone_name(client, POLL)
        param_form.set_str("to_zone", to_zone)
        try_set_label(f"{_zone_display(from_zone)} → {_zone_display(to_zone)}")
        on_status(f"Done — {_zone_display(to_zone)}")
        on_btn("Record")

    except asyncio.CancelledError:
        on_status("")
        on_btn("Record")
    except Exception as e:
        on_status(f"Error: {e}")
        on_btn("Record")


async def _record_enter_dungeon_teamup(client, param_form, on_status, on_btn, try_set_label) -> None:
    POLL = 0.15
    on_btn("Stop")
    on_status("Starting…")
    try:
        from_zone = await client.zone_name()
        param_form.set_str("from_zone", from_zone)
        start_pos = await client.body.position()
        param_form.set_xyz("reset_xyz", start_pos.x, start_pos.y, start_pos.z)
        on_status("Walk to sigil…")

        last_pos = start_pos

        while True:
            await asyncio.sleep(POLL)
            if await client.is_loading():
                param_form.set_xyz("sigil_xyz", last_pos.x, last_pos.y, last_pos.z)
                on_status("Loading…")
                break
            last_pos = await client.body.position()
            param_form.set_xyz("sigil_xyz", last_pos.x, last_pos.y, last_pos.z)

        while await client.is_loading():
            await asyncio.sleep(POLL)

        dungeon_zone = await _wait_for_zone_name(client, POLL)
        param_form.set_str("dungeon_zone", dungeon_zone)
        try_set_label(f"{_zone_display(from_zone)} → {_zone_display(dungeon_zone)}")
        on_status(f"Done — {_zone_display(dungeon_zone)}")
        on_btn("Record")

    except asyncio.CancelledError:
        on_status("")
        on_btn("Record")
    except Exception as e:
        on_status(f"Error: {e}")
        on_btn("Record")


async def _record_enter_zone_via_list(client, param_form, on_status, on_btn, try_set_label) -> None:
    POLL = 0.15
    on_btn("Stop")
    on_status("Starting…")
    try:
        zone = await client.zone_name()
        param_form.set_str("zone", zone)
        start_pos = await client.body.position()
        param_form.set_xyz("reset_xyz", start_pos.x, start_pos.y, start_pos.z)
        on_status("Walk to zone-list object…")

        while not await client.is_in_npc_range():
            await asyncio.sleep(POLL)

        on_status("Press X, select zone, click Teleport…")
        # Keep updating interact_xyz until the teleport triggers a loading screen.
        # The player's position at that moment is the correct interact coordinate.
        while not await client.is_loading():
            pos = await client.body.position()
            param_form.set_xyz("interact_xyz", pos.x, pos.y, pos.z)
            await asyncio.sleep(POLL)

        on_status("Loading…")
        while await client.is_loading():
            await asyncio.sleep(POLL)

        to_zone = await _wait_for_zone_name(client, POLL)
        param_form.set_str("to_zone", to_zone)
        # WizWalker exposes no "selected item" state on list controls, so we derive
        # the list entry text from the destination zone's display name. The user
        # should verify this matches the in-game text exactly.
        param_form.set_str("zone_list_entry", _zone_display(to_zone))
        try_set_label(f"{_zone_display(zone)} → {_zone_display(to_zone)}")
        on_status("Done — verify 'Zone List Entry' matches in-game text")
        on_btn("Record")

    except asyncio.CancelledError:
        on_status("")
        on_btn("Record")
    except Exception as e:
        on_status(f"Error: {e}")
        on_btn("Record")


async def _record_mark_location(client, param_form, on_status, on_btn, try_set_label) -> None:
    try:
        zone = await client.zone_name()
        pos = await client.body.position()
        param_form.set_str("zone", zone)
        param_form.set_xyz("xyz", pos.x, pos.y, pos.z)
        on_status(f"Done — {zone.split('/')[-1]}")
        on_btn("Record")
    except asyncio.CancelledError:
        on_status("")
        on_btn("Record")
    except Exception as e:
        on_status(f"Error: {e}")
        on_btn("Record")


async def _record_teleport_to_marked_location(client, param_form, on_status, on_btn, try_set_label) -> None:
    on_btn("Stop")
    on_status("Click the Recall button to recall…")
    try:
        zone = await client.zone_name()
        pos = await client.body.position()
        param_form.set_str("zone", zone)
        param_form.set_xyz("xyz", pos.x, pos.y, pos.z)
        try_set_label(_zone_display(zone))
        on_status(f"Done — {zone.split('/')[-1]}")
        on_btn("Record")

    except asyncio.CancelledError:
        on_status("")
        on_btn("Record")
    except Exception as e:
        on_status(f"Error: {e}")
        on_btn("Record")


# ---------------------------------------------------------------------------
# Registry  (must come after the coroutine definitions)
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, Any] = {
    "enter_zone": _record_enter_zone_gateway,
    "teleport_to_coordinates": _record_go_to_xyz,
    "enter_dungeon": _record_enter_dungeon,
    "enter_dungeon_teamup": _record_enter_dungeon_teamup,
    "interact": _record_interact,
    "apply_level_scaling": _record_apply_level_scaling,
    "enter_zone_via_object": _record_enter_zone_interact,
    "enter_zone_via_list": _record_enter_zone_via_list,
    "mark_location": _record_mark_location,
    "teleport_to_marked_location": _record_teleport_to_marked_location,
}

#: Frozenset of node_type strings that have a recording workflow.
#: Derived automatically from ``_REGISTRY`` — do not edit by hand.
RECORDABLE_NODES: frozenset[str] = frozenset(_REGISTRY)
