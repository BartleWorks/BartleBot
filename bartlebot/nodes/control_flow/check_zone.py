"""Node: check whether all specified clients are in a given zone."""

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="zone",
        label="Zone",
        help="The zone to check, such as WizardCity/WC_Ravenwood.",
        default="",
        required=True,
    ),
    ParamField(
        key="clients_to_check",
        type="player_list",
        label="Players to Check",
        help="Which players to check.",
        default=[],
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description="Checks whether the chosen players are all in a zone.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("TRUE", "Every checked player is in Zone."),
        ReturnSpec("FALSE", "A checked player isn't in Zone."),
    ],
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name() (via node_helpers.read_zones())

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

    # Read once each is readable: a None would take the FALSE edge for a player in the zone.
    zones = await node_helpers.read_zones(check_clients)
    if all(node_helpers.zone_matches(z, zone) for z in zones):
        machine.log(f"check_zone: TRUE — all checked clients in {zone!r}")
        return "TRUE"

    machine.log(f"check_zone: FALSE — not all checked clients in {zone!r}")
    return "FALSE"
