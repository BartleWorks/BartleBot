"""Node: click a UI element at a window-tree path with optional pre/post-click checks."""

import asyncio
import logging

from bartlebot.nodes.help_text import UI_PATH_NOTE, seconds
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
        help="The UI path of the element to click, such as WorldView › windowHUD › btnSpellbook.",
        required=True,
    ),
    ParamField(
        key="click_clients",
        type="player_list",
        label="Click Players",
        help=(
            "Which players click. Choose some when the element is open only for some players, such as "
            "a menu that Interact Menu opened for fewer players."
        ),
        default=[],
        default_hint="all players",
    ),
    ParamField(
        key="match_text",
        type="str",
        label="Match Text",
        help=(
            "When several elements share the path, click only one whose text, or the text of "
            "something inside it, matches this. The UI Tree Browser's Text column shows each "
            "element's text."
        ),
        default="",
        default_hint="no text filter",
    ),
    ParamField(
        key="match_text_exact",
        type="bool",
        label="Match Whole Text",
        help=(
            "When on, the text must match Match Text exactly. When off, it only has to contain it. Case never matters."
        ),
        default=False,
    ),
    ParamField(
        key="match_index",
        type="int",
        label="Match Index",
        help=(
            "Which matching element to click, counting from 0 in tree order, after Match Text. A "
            "negative number counts from the end, so -1 is the last."
        ),
        default=0,
        default_hint="first match",
    ),
    ParamField(
        key="availability_timeout",
        type="float",
        label="Availability Timeout (s)",
        help="How long to wait for the element to be visible and enabled. At 0, the node checks once.",
        default=TRIGGER_TIMEOUT,
        default_hint=seconds(TRIGGER_TIMEOUT),
    ),
    ParamField(
        key="confirm_path",
        type="str_list",
        label="Confirm Path",
        help="A UI path that shows the click worked. After clicking, the node waits for its first match.",
        default=[],
        default_hint="no check",
    ),
    ParamField(
        key="confirm_visible",
        type="bool",
        label="Confirm When Visible",
        help="When on, the node waits for Confirm Path to be visible. When off, it waits for it to be enabled.",
        default=True,
    ),
    ParamField(
        key="confirm_timeout",
        type="float",
        label="Confirm Timeout (s)",
        help="How long to wait for Confirm Path after clicking.",
        default=TRIGGER_TIMEOUT,
        default_hint=seconds(TRIGGER_TIMEOUT),
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Clicks the game's UI element at a UI path, once it's visible and enabled. Can confirm "
        "the click worked by waiting for a second path."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "The element was clicked, and Confirm Path, if set, was met."),
        ReturnSpec(
            "FAILURE",
            "No element matched in time, the click didn't register, or Confirm Path wasn't met in time.",
        ),
    ],
    max_consecutive_retries=5,
    usage_notes=(
        UI_PATH_NOTE
        + " A path matches window names only, so several elements can share one, as the rows of a list menu do."
        " Match Text and Match Index pick among them. Without them, the first match is clicked, and the log warns"
        " that there were more."
        + "\n\n"
        + (
            "Every player clicks unless Click Players narrows it. For a menu only some players "
            "opened, choose the same players here."
        )
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
