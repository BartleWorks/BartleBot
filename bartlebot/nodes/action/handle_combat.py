"""Node: enter and complete a combat encounter using WizSprinter."""

import asyncio
import re

from bartlebot.defaults.combat_config import BARTLEBOT_DEFAULT_COMBAT_CONFIG
import logging
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="enemy",
        type="str",
        label="Enemy Name",
        help=(
            "Entity name of the enemy to initiate combat with. "
            "Leave empty to use the closest available mob."
        ),
        default="",
        default_hint="Default: targets the closest available mob",
    ),
    ParamField(
        key="use_bot_default",
        type="bool",
        label="Use bot-level default combat config",
        help=(
            "When checked, the bot-level default combat config is used "
            "(or the BartleBot built-in default if none is set). "
            "Uncheck to supply a per-node combat config below."
        ),
        default=True,
    ),
    ParamField(
        key="combat_config",
        type="text",
        label="Combat Config",
        help=(
            "Combat config to use for this encounter. "
            "Only active when 'Use bot-level default' is unchecked."
        ),
        default="",
        default_hint="Default: uses the bot-level or BartleBot built-in combat config",
        disabled_by="use_bot_default",
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Initiates and completes a combat encounter. Teleports all clients to the "
        "target enemy, waits for them to enter battle, and then runs SprintyCombat "
        "until the duel ends. Detects item drops via chat during the encounter."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All clients have exited combat successfully."),
        ReturnSpec(
            "FAILURE",
            "No valid target found, no combat config available, or clients "
            "failed to enter combat within the timeout.",
        ),
    ],
    required_bot_options=[
        BotOptionSpec(
            name="Default Combat Order",
            type="int_list",
            default=[],
            description=(
                "Default order clients enter combat (e.g. '2, 1' sends client 2 first). "
                "Empty = all clients enter simultaneously. Can be overridden per node "
                "via Option Overrides."
            ),
        ),
    ],
)

_ENTER_COMBAT_TIMEOUT = 30.0
_POLL_INTERVAL = 0.25
_TP_RETRY_INTERVAL = 5.0  # re-teleport this often if still not in battle


async def run(machine, clients: list, params: dict) -> str:
    """Enter and complete a combat encounter for all clients."""
    from bartlebot.utils.drop_detection import extract_new_drops, snapshot_chat  # noqa: PLC0415

    enemy_name: str = params.get("enemy", "")
    use_bot_default: bool = params.get("use_bot_default", True)

    combat_dsl, dsl_source = _resolve_combat_dsl(machine, params, use_bot_default)
    if not combat_dsl:
        machine.log(
            "FAILURE: no combat DSL available "
            "(use_bot_default is off and per-node combat_config is empty)",
            logging.WARNING,
        )
        return "FAILURE"

    enemy_display = repr(enemy_name) if enemy_name else "(closest)"
    machine.log(f"handle_combat: enemy={enemy_display}, DSL source={dsl_source!r}")
    machine.log(
        f"Combat DSL: {combat_dsl[:80]}{'…' if len(combat_dsl) > 80 else ''}",
        logging.DEBUG,
    )

    # 1. Resolve target entity for each client
    targets = await asyncio.gather(*[_find_target(c, enemy_name) for c in clients])
    if not all(targets):
        none_idx = [i for i, t in enumerate(targets) if t is None]
        machine.log(
            f"FAILURE: target not found for client(s) {none_idx} "
            f"(enemy={enemy_display})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"Targets resolved for {len(clients)} client(s)", logging.DEBUG)

    # 2. Snapshot chat before combat so we can diff for drops afterward
    chat_before = await asyncio.gather(*[snapshot_chat(c) for c in clients])

    # 3. Teleport clients to their target (sequential or simultaneous).
    combat_order: list[int] = machine.get_option("Default Combat Order", []) or []
    if combat_order:
        machine.log(f"Entering combat sequentially: order={combat_order}", logging.DEBUG)
        for client_num in combat_order:
            idx = client_num - 1  # user-facing 1-based → 0-based list index
            if idx < 0 or idx >= len(clients):
                machine.log(
                    f"combat_order: skipping client {client_num} — not connected "
                    f"(have {len(clients)} client(s))",
                    logging.DEBUG,
                )
                continue
            if not await _tp_and_wait_for_combat(clients[idx], enemy_name, _ENTER_COMBAT_TIMEOUT):
                machine.log(
                    f"FAILURE: client {client_num} did not enter combat within "
                    f"{_ENTER_COMBAT_TIMEOUT}s",
                    logging.WARNING,
                )
                return "FAILURE"
    else:
        # 4. Teleport all clients to their targets, retrying until in combat
        machine.log(
            "Teleporting all clients to target (retrying until in combat)…", logging.DEBUG
        )
        results = await asyncio.gather(
            *[_tp_and_wait_for_combat(c, enemy_name, _ENTER_COMBAT_TIMEOUT) for c in clients]
        )
        if not all(results):
            not_in_battle = [i for i, ok in enumerate(results) if not ok]
            machine.log(f"Clients not in battle at timeout: {not_in_battle}", logging.DEBUG)
            machine.log(
                f"FAILURE: timed out after {_ENTER_COMBAT_TIMEOUT}s waiting for "
                "clients to enter combat",
                logging.WARNING,
            )
            return "FAILURE"

    machine.log("All clients in combat — running SprintyCombat")

    # 5. Split multi-client DSL by ### pN section headers, then run SprintyCombat in parallel.
    per_client_dsl = _split_combat_config(combat_dsl, len(clients))
    await asyncio.gather(*[_run_combat(c, dsl) for c, dsl in zip(clients, per_client_dsl)])

    machine.log("SprintyCombat finished for all clients", logging.DEBUG)

    # 6. Detect drops by comparing chat before and after combat
    drops_found: list[str] = []
    for i, (client, before) in enumerate(zip(clients, chat_before)):
        after = await snapshot_chat(client)
        for item_name in extract_new_drops(before, after):
            machine.on_drop(item_name, 1, i)
            drops_found.append(item_name)

    if drops_found:
        machine.log(f"Detected {len(drops_found)} drop(s): {', '.join(drops_found)}")
    else:
        machine.log("No drops detected", logging.DEBUG)

    machine.log("SUCCESS: combat complete")
    return "SUCCESS"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_combat_dsl(machine, params: dict, use_bot_default: bool) -> tuple[str, str]:
    """Return (dsl_string, source_label). dsl_string is empty if none can be resolved."""
    if use_bot_default:
        bot_cfg = machine._bot_config
        if bot_cfg is not None and not bot_cfg.use_bartlebot_default_combat_config:
            return bot_cfg.default_combat_config or "", "bot-level custom config"
        return BARTLEBOT_DEFAULT_COMBAT_CONFIG, "BartleBot built-in default"
    return params.get("combat_config", "").strip(), "per-node config"


async def _find_target(client, enemy_name: str):
    """Return the closest matching entity, or None if not found."""
    if enemy_name:
        return await client.find_closest_by_vague_name(enemy_name)
    return await client.find_closest_mob()


async def _run_combat(client, combat_dsl: str) -> None:
    """Run SprintyCombat for a single client using an inline DSL string."""
    from wizwalker.extensions.wizsprinter import SprintyCombat  # noqa: PLC0415
    from wizwalker.extensions.wizsprinter.combat_backends.config_backend import (  # noqa: PLC0415
        CombatConfigProvider,
    )
    from wizwalker.extensions.wizsprinter.combat_backends.backend_base import (  # noqa: PLC0415
        BaseCombatBackend,
    )

    class _StrCombatConfigProvider(CombatConfigProvider):
        """CombatConfigProvider variant that accepts a DSL string directly."""

        def __init__(self, content: str, cast_time: float = 0.2):
            # Bypass the parent __init__ (which opens a file path).
            BaseCombatBackend.__init__(self, cast_time=cast_time)
            self.filename = "<inline>"
            self.config = self.parse_config(content)

    provider = _StrCombatConfigProvider(combat_dsl)
    await SprintyCombat(client, provider).handle_combat()


def _split_combat_config(dsl: str, num_clients: int) -> list[str]:
    """Split a Deimos-style multi-client DSL into per-client config strings.

    Section headers of the form '### p1', '### p2', etc. (1-based, matching
    Deimos convention) delimit per-client configs. Content before the first
    header and clients without a matching section fall back to client 1's
    section, or the full string if no headers exist.
    """
    pattern = re.compile(r"###\sp\s*(\d+)")

    lines = dsl.splitlines()
    sections: dict[int, list[str]] = {}  # 0-indexed client → lines
    current_idx = -1
    current_lines: list[str] = []

    for line in lines:
        match = re.search(pattern, line)
        if match:
            if current_idx != -1:
                sections[current_idx] = current_lines[:]
            current_idx = int(match.group(1)) - 1  # 1-based → 0-based
            current_lines = []
        else:
            current_lines.append(line)

    if current_idx == -1:
        # No section headers — all clients share the full config.
        return [dsl] * num_clients

    sections[current_idx] = current_lines
    fallback = "\n".join(sections.get(0, lines))
    return ["\n".join(sections.get(i, sections.get(0, lines))) for i in range(num_clients)]


async def _tp_and_wait_for_combat(client, enemy_name: str, timeout: float) -> bool:
    """Teleport to target, retrying every _TP_RETRY_INTERVAL seconds until in combat."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        target = await _find_target(client, enemy_name)
        if target is None:
            return False
        await client.tp_to(target)

        # Poll in_battle() for up to _TP_RETRY_INTERVAL (or remaining time)
        retry_at = min(asyncio.get_event_loop().time() + _TP_RETRY_INTERVAL, deadline)
        while asyncio.get_event_loop().time() < retry_at:
            if await client.in_battle():
                return True
            await asyncio.sleep(_POLL_INTERVAL)

        if asyncio.get_event_loop().time() >= deadline:
            break

    return False
