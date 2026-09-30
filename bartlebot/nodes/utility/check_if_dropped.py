"""Node: check whether specific items have been dropped in the current run."""

from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="item_names",
        type="str_list",
        label="Item Names",
        help=(
            "Comma-separated names of items to check for (e.g. 'IronBloom, GoldKey'). "
            "Names are matched case-insensitively."
        ),
        default=[],
        required=True,
    ),
    ParamField(
        key="clients_to_check",
        type="player_list",
        label="Players to Check",
        help="Which players to check for drops.",
        default=[],
    ),
    ParamField(
        key="require_all",
        type="bool",
        label="Require All",
        help=(
            "When checked, returns TRUE only if every item in the list has been "
            "dropped for every checked player. When unchecked, returns TRUE as "
            "soon as any matching item is found for any checked player."
        ),
        default=False,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Checks the current run's drop history for one or more item names. "
        "Useful for routing the bot graph: loop back until the desired item drops."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("TRUE", "The drop condition is satisfied."),
        ReturnSpec("FALSE", "The drop condition is not yet satisfied."),
    ],
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: machine.wait_for_drop_poll(), machine.has_drop() (runtime state — no client API calls)

    Flow:
      0. Wait for the last fight's drop poll, which runs in the background after the
         fight ends. Without this, a node placed straight after handle_combat would
         answer before that fight's drops were recorded.
      1. Normalize item_names to lowercase; return FALSE immediately if the list is empty.
      2. Resolve which 0-based client indices to check (defaults to all clients).
      3. If require_all: return TRUE only if every item appears for every checked client.
         Otherwise: return TRUE as soon as any item matches any checked client.

    FAILURE: never — always returns TRUE or FALSE.
    """
    item_names: list[str] = params.get("item_names") or []
    clients_to_check: list[int] = params.get("clients_to_check") or []
    require_all: bool = params.get("require_all", False)

    await machine.wait_for_drop_poll()

    # Normalize item names for case-insensitive comparison.
    items = [n.strip().lower() for n in item_names if n.strip()]
    if not items:
        machine.log("check_if_dropped: item_names is empty — returning FALSE")
        return "FALSE"

    # Determine which 0-based client indices to check.
    check_indices = [n - 1 for n in clients_to_check if 0 < n <= len(clients)]

    if require_all:
        # Every item must be present for every checked client.
        for idx in check_indices:
            for item in items:
                if not machine.has_drop(item, idx):
                    machine.log(f"check_if_dropped: FALSE — {item!r} not found for client {idx + 1}")
                    return "FALSE"
        machine.log("check_if_dropped: TRUE — all items present for all checked clients")
        return "TRUE"
    else:
        # Any item present for any checked client is sufficient.
        for idx in check_indices:
            for item in items:
                if machine.has_drop(item, idx):
                    machine.log(f"check_if_dropped: TRUE — {item!r} found for client {idx + 1}")
                    return "TRUE"
        machine.log("check_if_dropped: FALSE — no matching items found")
        return "FALSE"
