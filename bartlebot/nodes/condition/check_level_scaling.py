"""Node: check whether specified clients have an active level-scaling elixir."""

import asyncio

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import LEVEL_SCALING_PATH

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="clients_to_check",
        type="int_list",
        label="Clients to Check",
        help=(
            "1-based client numbers to check for level scaling (e.g. '2, 3'). "
            "Leave empty to check all clients."
        ),
        default=[],
        default_hint="Default: checks all clients",
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Checks whether the level-scaling elixir UI indicator is visible for the "
        "specified clients. Returns TRUE only if every checked client is level scaled."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("TRUE", "All specified clients have an active level-scaling elixir."),
        ReturnSpec("FALSE", "One or more specified clients are not level scaled."),
    ],
)


async def run(machine, clients: list, params: dict) -> str:
    """Return 'TRUE' if all specified clients are level scaled, 'FALSE' otherwise."""
    clients_to_check: list[int] = params.get("clients_to_check") or []

    check_indices = [n - 1 for n in clients_to_check] if clients_to_check else list(range(len(clients)))
    check_clients = [clients[i] for i in check_indices]

    results = await asyncio.gather(
        *[node_helpers.is_visible_by_path(c, LEVEL_SCALING_PATH) for c in check_clients]
    )

    for idx, scaled in zip(check_indices, results):
        if not scaled:
            machine.log(f"check_level_scaling: FALSE — client {idx + 1} is not level scaled")
            return "FALSE"

    machine.log("check_level_scaling: TRUE — all checked clients are level scaled")
    return "TRUE"
