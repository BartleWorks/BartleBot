"""Node: click a UI element at a window-tree path with optional pre/post-click checks."""

import asyncio
import logging

from bartlebot.nodes.node_helpers import click_by_path, is_enabled_by_path, is_visible_by_path, poll_until
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import POLL_INTERVAL, TRIGGER_TIMEOUT

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="path",
        type="str_list",
        label="UI Path",
        help="Window tree path to the element to click, e.g. ['WorldView', 'windowHUD', 'btnSpellbook'].",
        required=True,
    ),
    ParamField(
        key="availability_timeout",
        type="float",
        label="Availability Timeout (s)",
        help=(
            "Seconds to wait for the path to be visible and enabled before giving up. "
            "Set to 0 to check once without waiting."
        ),
        default=TRIGGER_TIMEOUT,
        default_hint=f"{TRIGGER_TIMEOUT}s",
    ),
    ParamField(
        key="confirm_path",
        type="str_list",
        label="Confirm Path",
        help=(
            "If set, polls this path after clicking to confirm the action triggered. "
            "Leave empty to skip the post-click check."
        ),
        default=[],
        default_hint="skip confirm",
    ),
    ParamField(
        key="confirm_visible",
        type="bool",
        label="Confirm: Check Visible (vs Enabled)",
        help=(
            "When Confirm Path is set: if True, waits until the path is visible; "
            "if False, waits until it is enabled (not grayed)."
        ),
        default=True,
    ),
    ParamField(
        key="confirm_timeout",
        type="float",
        label="Confirm Timeout (s)",
        help="Seconds to wait for the confirm path condition to be met after clicking.",
        default=TRIGGER_TIMEOUT,
        default_hint=f"{TRIGGER_TIMEOUT}s",
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Clicks a UI element at a window-tree path. Waits for the element to be visible and enabled "
        "before clicking. Optionally polls a second path after clicking to confirm the action triggered."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Element clicked (and confirm condition met, if configured)."),
        ReturnSpec(
            "FAILURE",
            "Path was not visible/enabled within the timeout, the click returned False, "
            "or the confirm condition was not met within the confirm timeout.",
        ),
    ],
    max_consecutive_retries=5,
    usage_notes=(
        "Use paths from bartlebot/nodes/paths.py when possible. "
        "The confirm path is useful when a click opens a dialog or changes a button state "
        "that the next node depends on."
    ),
)


async def run(machine, clients: list, params: dict) -> str:
    path: list[str] = params.get("path", [])
    availability_timeout: float = params.get("availability_timeout", TRIGGER_TIMEOUT)
    confirm_path: list[str] = params.get("confirm_path", [])
    confirm_visible: bool = params.get("confirm_visible", True)
    confirm_timeout: float = params.get("confirm_timeout", TRIGGER_TIMEOUT)

    if not path:
        machine.log("click_path: FAILURE — path parameter is empty", logging.WARNING)
        return "FAILURE"

    machine.log(f"click_path: clicking {'→'.join(path)}")

    async def _is_available(client) -> bool:
        return await is_visible_by_path(client, path) and await is_enabled_by_path(client, path)

    if availability_timeout == 0.0:
        checks = await asyncio.gather(*[_is_available(c) for c in clients])
    else:
        checks = await asyncio.gather(
            *[poll_until(lambda c=c: _is_available(c), availability_timeout, POLL_INTERVAL) for c in clients]
        )

    if not all(checks):
        machine.log(f"click_path: FAILURE — path not available within {availability_timeout}s", logging.WARNING)
        return "FAILURE"

    clicked = await asyncio.gather(*[click_by_path(c, path) for c in clients])
    if not all(clicked):
        machine.log("click_path: FAILURE — click returned False (element grayed or missing)", logging.WARNING)
        return "FAILURE"

    if confirm_path:
        machine.log(f"click_path: checking confirm path {'→'.join(confirm_path)}")

        async def _confirm(client) -> bool:
            if confirm_visible:
                return await is_visible_by_path(client, confirm_path)
            return await is_enabled_by_path(client, confirm_path)

        confirmed = await asyncio.gather(
            *[poll_until(lambda c=c: _confirm(c), confirm_timeout, POLL_INTERVAL) for c in clients]
        )
        if not all(confirmed):
            machine.log(f"click_path: FAILURE — confirm path not met within {confirm_timeout}s", logging.WARNING)
            return "FAILURE"

    machine.log("click_path: SUCCESS")
    return "SUCCESS"
