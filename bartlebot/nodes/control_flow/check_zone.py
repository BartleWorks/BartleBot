"""Node: check whether all specified clients are in a given zone."""

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="zone",
        label="Zone Name",
        help="Zone name to check (e.g. 'WizardCity/WC_Ravenwood').",
        default="",
        required=True,
    ),
    ParamField(
        key="clients_to_check",
        type="player_list",
        label="Players to Check",
        help="Which players to check zone for.",
        default=[],
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description="Checks whether all specified players are currently in a given zone.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("TRUE", "All checked players are in the specified zone."),
        ReturnSpec("FALSE", "One or more checked players are not in the specified zone."),
    ],
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name() (via node_helpers.all_in_zone())

    Flow:
      1. Validate the zone param; return FALSE immediately if empty.
      2. Resolve which 0-based client indices to check (defaults to all clients).
      3. Check zone_name() on all specified clients simultaneously.
      4. Return TRUE if every client is in the specified zone; FALSE otherwise.

    FAILURE: never — always returns TRUE or FALSE.
    """
    zone: str = params.get("zone", "")
    clients_to_check: list[int] = params.get("clients_to_check") or []

    if not zone:
        machine.log("check_zone: zone parameter is empty — returning FALSE")
        return "FALSE"

    # Determine which 0-based client indices to check.
    check_indices = [n - 1 for n in clients_to_check if 0 < n <= len(clients)]

    check_clients = [clients[i] for i in check_indices]

    if await node_helpers.all_in_zone(check_clients, zone):
        machine.log(f"check_zone: TRUE — all checked clients in {zone!r}")
        return "TRUE"

    machine.log(f"check_zone: FALSE — not all checked clients in {zone!r}")
    return "FALSE"
