"""Node: check whether all specified clients are in a given zone."""

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="str",
        label="Zone Name",
        help="Zone name to check (e.g. 'WizardCity/WC_Ravenwood').",
        default="",
        required=True,
    ),
    ParamField(
        key="clients_to_check",
        type="int_list",
        label="Clients to Check",
        help="1-based client numbers to check (e.g. '1, 2'). Leave empty to check all clients.",
        default=[],
        default_hint="Default: checks all clients",
    ),
]

NODE_SPEC = NodeSpec(
    description="Checks whether all specified clients are currently in a given zone.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("TRUE", "All checked clients are in the specified zone."),
        ReturnSpec("FALSE", "One or more checked clients are not in the specified zone."),
    ],
)


async def run(machine, clients: list, params: dict) -> str:
    """Return 'TRUE' if all specified clients are in zone, 'FALSE' otherwise."""
    zone: str = params.get("zone", "")
    clients_to_check: list[int] = params.get("clients_to_check") or []

    if not zone:
        machine.log("check_zone: zone parameter is empty — returning FALSE")
        return "FALSE"

    # Determine which 0-based client indices to check.
    if clients_to_check:
        check_indices = [n - 1 for n in clients_to_check]
    else:
        check_indices = list(range(len(clients)))

    check_clients = [clients[i] for i in check_indices]

    if await node_helpers.all_in_zone(check_clients, zone):
        machine.log(f"check_zone: TRUE — all checked clients in {zone!r}")
        return "TRUE"

    machine.log(f"check_zone: FALSE — not all checked clients in {zone!r}")
    return "FALSE"
