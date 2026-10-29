"""Node: check whether specified clients have an active level-scaling elixir."""

import asyncio

from bartleby.runtime.paths import LEVEL_SCALING_ELIXIR_PATH

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.options.option_groups import GROUP_TEAM

PARAM_SCHEMA: list[ParamField] = []

_LEVEL_SCALING_CLIENTS_OPTION = BotOptionSpec(
    name="Level Scaling Players",
    group=GROUP_TEAM,
    type="player_list",
    default=[],
    description=(
        "Which players need the level scaling elixir. Every level scaling node uses this list, "
        "and an empty list means no players."
    ),
)

NODE_SPEC = NodeSpec(
    description="Checks whether every Level Scaling Player has the level scaling elixir.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("TRUE", "Every Level Scaling Player has the elixir."),
        ReturnSpec("FALSE", "A Level Scaling Player doesn't have the elixir."),
    ],
    required_bot_options=[_LEVEL_SCALING_CLIENTS_OPTION],
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: node_helpers.is_visible_by_path()

    Flow:
      1. Read Level Scaling Players bot option; resolve to 0-based client indices.
      2. Check LEVEL_SCALING_ELIXIR_PATH visibility on all specified clients simultaneously.
      3. Return TRUE if every client shows the elixir indicator; FALSE otherwise.

    FAILURE: never — always returns TRUE or FALSE.
    """
    clients_to_check: list[int] = machine.get_option("Level Scaling Players", []) or []

    check_indices = [n - 1 for n in clients_to_check if 0 < n <= len(clients)]
    check_clients = [clients[i] for i in check_indices]

    results = await asyncio.gather(
        *[node_helpers.is_visible_by_path(c, LEVEL_SCALING_ELIXIR_PATH) for c in check_clients]
    )

    for idx, scaled in zip(check_indices, results):
        if not scaled:
            machine.log(f"check_level_scaling: FALSE — client {idx + 1} is not level scaled")
            return "FALSE"

    machine.log("check_level_scaling: TRUE — all checked clients are level scaled")
    return "TRUE"
