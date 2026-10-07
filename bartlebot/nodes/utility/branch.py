"""Node: do nothing and return SUCCESS, so a multi-way switch has somewhere to hang."""

from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = []

NODE_SPEC = NodeSpec(
    description="Does nothing and returns SUCCESS. Its edges choose where to go next.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Always returned, at once."),
    ],
    usage_notes=(
        "Give its SUCCESS port several edges, each with a condition, and one without for "
        "when none of them is true: a switch. Several nodes can lead into one branch and "
        "share its switch, instead of each carrying a copy of the edges."
    ),
    see_also=["set_variables"],
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: none.

    Flow:
      1. Return SUCCESS. The conditions on its edges are the whole of what it does.

    FAILURE: never.
    """
    del machine, clients, params
    return "SUCCESS"
