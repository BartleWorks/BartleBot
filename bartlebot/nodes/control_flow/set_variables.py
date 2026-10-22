"""Node: write the bot's variables, in order, and return SUCCESS."""

from bartlebot.bot import variables
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key=variables.ASSIGNMENTS_PARAM,
        type="assignments",
        label="Assignments",
        help=(
            "Each row sets one variable: to a value, by adding a whole number to an int, or by "
            "toggling a bool. Rows apply in order, so a later row sees an earlier one's change."
        ),
        default=[],
    ),
]

NODE_SPEC = NodeSpec(
    description="Sets the bot's variables, one row at a time.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Always, once every row is written."),
    ],
    usage_notes=(
        (
            "Use this node to remember something for a later edge condition, such as which rack is "
            "next. It doesn't touch the game."
        )
        + "\n\n"
        + ("All rows are written together. More than a few dozen variable nodes in a row stop the run.")
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
