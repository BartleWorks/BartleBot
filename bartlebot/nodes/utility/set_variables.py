"""Node: write the bot's variables, in order, and return SUCCESS."""

from bartlebot.bot import variables
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key=variables.ASSIGNMENTS_PARAM,
        type="assignments",
        label="Assignments",
        help=(
            "Each row writes one variable: set it to a value, add a whole number to an int, or "
            "toggle a bool. Rows apply in order, so a later row sees an earlier one's write."
        ),
        default=[],
    ),
]

NODE_SPEC = NodeSpec(
    description="Writes the bot's variables, one row at a time, then returns SUCCESS.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Always returned, once every row is written."),
    ],
    usage_notes=(
        "Never touches the game. Use it to remember something the run found, for an edge "
        "condition further on to read: which rack is next, how many swords are placed.\n\n"
        "Every row is written at once or none is, so an ambient fight can never apply a row "
        "twice. More than a few dozen variable-only nodes in a row stop the run."
    ),
    see_also=["branch"],
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: none -- machine.set_variable() only.

    Flow:
      1. Work out every row's new value from the variables as they are now.
      2. Write them, with no await between the first and the last.

    FAILURE: never. A row naming an undeclared variable or a value its type refuses is
    caught by validation before the run; reaching one here raises, which the run loop
    turns into FAILURE.
    """
    del clients  # never touches the game
    rows = variables.parse_assignments(params.get(variables.ASSIGNMENTS_PARAM))
    for name, value in variables.apply_assignments(machine.variables, machine.variable_declarations, rows).items():
        machine.set_variable(name, value)
    return "SUCCESS"
