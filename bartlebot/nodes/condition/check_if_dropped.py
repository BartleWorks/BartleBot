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
        type="int_list",
        label="Clients to Check",
        help=("1-based client numbers to check (e.g. '1, 2'). Leave empty to check all clients."),
        default=[],
        default_hint="Default: checks all clients",
    ),
    ParamField(
        key="require_all",
        type="bool",
        label="Require All",
        help=(
            "When checked, returns TRUE only if every item in the list has been "
            "dropped for every checked client. When unchecked, returns TRUE as "
            "soon as any matching item is found for any checked client."
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
    """Return 'TRUE' if the requested drop condition is met, 'FALSE' otherwise."""
    item_names: list[str] = params.get("item_names") or []
    clients_to_check: list[int] = params.get("clients_to_check") or []
    require_all: bool = params.get("require_all", False)

    # Normalise item names for case-insensitive comparison.
    items = [n.strip().lower() for n in item_names if n.strip()]
    if not items:
        machine.log("check_if_dropped: item_names is empty — returning FALSE")
        return "FALSE"

    # Determine which 0-based client indices to check.
    if clients_to_check:
        check_indices = [n - 1 for n in clients_to_check]
    else:
        check_indices = list(range(len(clients)))

    # Build a set of (client_index, item_name_lower) that have been seen.
    seen: set[tuple[int, str]] = {(client_idx, name.strip().lower()) for name, _qty, client_idx in machine.drops}

    if require_all:
        # Every item must be present for every checked client.
        for idx in check_indices:
            for item in items:
                if (idx, item) not in seen:
                    machine.log(f"check_if_dropped: FALSE — {item!r} not found for client {idx + 1}")
                    return "FALSE"
        machine.log("check_if_dropped: TRUE — all items present for all checked clients")
        return "TRUE"
    else:
        # Any item present for any checked client is sufficient.
        for idx in check_indices:
            for item in items:
                if (idx, item) in seen:
                    machine.log(f"check_if_dropped: TRUE — {item!r} found for client {idx + 1}")
                    return "TRUE"
        machine.log("check_if_dropped: FALSE — no matching items found")
        return "FALSE"
