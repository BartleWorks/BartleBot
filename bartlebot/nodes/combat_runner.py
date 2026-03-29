"""Shared combat execution helpers used by handle_combat and the ambient combat watcher.

Extracted so that machine.py does not need to import from a specific node module.
"""

import re

from bartlebot.defaults.combat_config import BARTLEBOT_DEFAULT_COMBAT_CONFIG


def _resolve_combat_dsl(machine, params: dict, use_bot_default: bool) -> tuple[str, str]:
    """Return (dsl_string, source_label). dsl_string is empty if none can be resolved."""
    if use_bot_default:
        bot_cfg = machine._bot_config
        if bot_cfg is not None and not bot_cfg.use_bartlebot_default_combat_config:
            return bot_cfg.default_combat_config or "", "bot-level custom config"
        return BARTLEBOT_DEFAULT_COMBAT_CONFIG, "BartleBot built-in default"
    return params.get("combat_config", "").strip(), "per-node config"


async def _run_combat(client, combat_dsl: str) -> None:
    """Run SprintyCombat for a single client using an inline DSL string."""
    from wizwalker.extensions.wizsprinter import SprintyCombat  # noqa: PLC0415
    from wizwalker.extensions.wizsprinter.combat_backends.backend_base import (  # noqa: PLC0415
        BaseCombatBackend,
    )
    from wizwalker.extensions.wizsprinter.combat_backends.config_backend import (  # noqa: PLC0415
        CombatConfigProvider,
    )

    class _StrCombatConfigProvider(CombatConfigProvider):
        """CombatConfigProvider variant that accepts a DSL string directly."""

        def __init__(self, content: str, cast_time: float = 0.2):
            # Bypass the parent __init__ (which opens a file path).
            BaseCombatBackend.__init__(self, cast_time=cast_time)
            self.filename = "<inline>"
            self.config = self.parse_config(content)

    provider = _StrCombatConfigProvider(combat_dsl)
    await SprintyCombat(client, provider).wait_for_combat()


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
    return ["\n".join(sections.get(i, sections.get(0, lines))) for i in range(num_clients)]
