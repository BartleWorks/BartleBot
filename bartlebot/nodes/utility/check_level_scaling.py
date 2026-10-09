"""Node: check whether specified clients have an active level-scaling elixir."""

import asyncio

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import LEVEL_SCALING_ELIXIR_PATH
from bartlebot.options.option_groups import GROUP_TEAM

PARAM_SCHEMA: list[ParamField] = []

_LEVEL_SCALING_CLIENTS_OPTION = BotOptionSpec(
    name="Level Scaling Players",
    group=GROUP_TEAM,
    type="player_list",
    default=[],
    description="Which players to check/apply level scaling. Empty = no players. Shared by every level scaling node.",
)

NODE_SPEC = NodeSpec(
    description=(
        "Checks whether the level-scaling elixir UI indicator is visible for the "
        "specified players. Returns TRUE only if every checked player is level scaled."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("TRUE", "All specified players have an active level-scaling elixir."),
        ReturnSpec("FALSE", "One or more specified players are not level scaled."),
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
