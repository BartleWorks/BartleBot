"""Node: click a UI element at a window-tree path with optional pre/post-click checks."""

import asyncio
import logging

from bartlebot.nodes.node_helpers import (
    click_window,
    is_enabled_by_path,
    is_visible_by_path,
    poll_until,
    resolve_path_match,
)
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
        key="click_clients",
        type="player_list",
        label="Click Players",
        help=(
            "Which players click the element. Leave all unchecked to click on every player, "
            "which is what this node has always done. Select a subset when the UI is only open "
            "on some players — a menu opened by interact_menu with a narrower Interact Players "
            "is not present on the others, and the path matches nothing there."
        ),
        default=[],
        default_hint="all players",
    ),
    ParamField(
        key="match_text",
        type="str",
        label="Match Text",
        help=(
            "When several elements share the same path — a menu whose rows are identical by name — "
            "keep only those whose text, or the text of any element inside them, matches this. "
            "Read the text off the Text column in the UI Tree Browser. Leave empty to skip the text filter."
        ),
        default="",
        default_hint="no text filter",
    ),
    ParamField(
        key="match_text_exact",
        type="bool",
        label="Match Text: Exact (vs Contains)",
        help=(
            "When Match Text is set: if True, the text must match exactly; if False, it need only "
            "contain the string. Case-insensitive either way. Ignored when Match Text is empty."
        ),
        default=False,
    ),
    ParamField(
        key="match_index",
        type="int",
        label="Match Index",
        help=(
            "Which of the matching elements to click, 0-based, in tree order (negative counts from "
            "the end). Applied after Match Text. Leave at 0 to click the first match, which is what "
            "this node has always done."
        ),
        default=0,
        default_hint="first match",
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
        "before clicking. When the path is ambiguous, Match Text and Match Index pick which of the "
        "matching elements to click. Optionally polls a second path after clicking to confirm the "
        "action triggered."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Element clicked (and confirm condition met, if configured)."),
        ReturnSpec(
            "FAILURE",
            "No element matched the path (or Match Text / Match Index selected none of them) and was "
            "visible and enabled within the timeout, the click returned False, "
            "or the confirm condition was not met within the confirm timeout.",
        ),
    ],
    max_consecutive_retries=5,
    usage_notes=(
        "Use paths from bartlebot/nodes/paths.py when possible. "
        "Paths match on window NAME only, and several elements can share one path — a list menu "
        "whose rows are structurally identical is the usual case. Without Match Text or Match Index "
        "the first match in tree order is clicked, and the log warns when there was more than one. "
        "Every player clicks unless Click Players narrows it, so a menu that only some players "
        "opened needs the same selection here that opened it. "
        "The confirm path is useful when a click opens a dialog or changes a button state "
        "that the next node depends on; it always uses its first match and takes no match params."
    ),
)


def _describe_miss(count: int, match_text: str, match_index: int) -> str:
    """Explain why the match params selected nothing, given how many the path itself matched."""
    if count == 0:
        return "path matched no elements"
    if match_text:
        return f"none of the {count} matched elements had text {match_text!r}"
    return f"match index {match_index} out of range ({count} matched)"


async def run(machine, clients: list, params: dict) -> str:
    path: list[str] = params.get("path", [])
    click_clients: list[int] = params.get("click_clients") or []
    match_text: str = params.get("match_text", "")
    match_text_exact: bool = params.get("match_text_exact", False)
    match_index: int = params.get("match_index", 0)
    availability_timeout: float = params.get("availability_timeout", TRIGGER_TIMEOUT)
    confirm_path: list[str] = params.get("confirm_path", [])
    confirm_visible: bool = params.get("confirm_visible", True)
    confirm_timeout: float = params.get("confirm_timeout", TRIGGER_TIMEOUT)

    if not path:
        machine.log("click_path: FAILURE — path parameter is empty", logging.WARNING)
        return "FAILURE"

    # Empty means every client, not none: this node predates the param, and existing bot
    # files carry no value for it.
    if click_clients:
        indices = [n - 1 for n in click_clients if 0 < n <= len(clients)]
        clients = [clients[i] for i in indices]
        if not clients:
            machine.log("click_path: no selected client is connected — SUCCESS")
            return "SUCCESS"
        machine.log(f"click_path: clicking on client(s) {[i + 1 for i in indices]}", logging.DEBUG)

    selector = ""
    if match_text:
        selector = f" matching text {match_text!r}" + (" (exact)" if match_text_exact else "")
    if match_index:
        selector += f" at index {match_index}"
    machine.log(f"click_path: clicking {'→'.join(path)}{selector}")

    # The element the checks passed on is the element that gets clicked. Resolving the path
    # again for the click would re-run the match from scratch, and with several matching rows
    # that is not guaranteed to land on the same one.
    resolved: dict[int, object] = {}
    # Why the path failed, kept per client so the failure message can say which case it was
    # rather than the generic "not available".
    reasons: dict[int, str] = {}
    # The ambiguity warning is about the node's configuration, not about this tick, so it is
    # said once per run rather than on every poll.
    warned: list[bool] = []

    async def _is_available(client) -> bool:
        window, count = await resolve_path_match(
            client,
            path,
            match_text=match_text,
            match_text_exact=match_text_exact,
            match_index=match_index,
        )
        if window is None:
            reasons[id(client)] = _describe_miss(count, match_text, match_index)
            return False
        if count > 1 and not match_text and not match_index and not warned:
            warned.append(True)
            machine.log(
                f"click_path: path matched {count} elements; clicking the first. "
                "Set Match Text or Match Index to choose deliberately.",
                logging.WARNING,
            )
        if not await window.is_visible() or await window.is_control_grayed():
            reasons[id(client)] = "matched but not visible/enabled"
            return False
        resolved[id(client)] = window
        return True

    if availability_timeout == 0.0:
        checks = await asyncio.gather(*[_is_available(c) for c in clients])
    else:
        checks = await asyncio.gather(
            *[poll_until(lambda c=c: _is_available(c), availability_timeout, POLL_INTERVAL) for c in clients]
        )

    if not all(checks):
        detail = "; ".join(sorted({reasons[id(c)] for c in clients if id(c) not in resolved and id(c) in reasons}))
        machine.log(
            f"click_path: FAILURE — path not available within {availability_timeout}s"
            + (f" ({detail})" if detail else ""),
            logging.WARNING,
        )
        return "FAILURE"

    clicked = await asyncio.gather(*[click_window(c, resolved[id(c)]) for c in clients])
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
