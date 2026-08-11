"""form_group node — put every player into one in-game group."""

import logging

from bartlebot import group_manager, wizard_names
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="leader_index",
        type="int",
        label="Leader Player",
        help=(
            "Zero-based index of the player that sends the invitations. Only used when nobody "
            "is grouped yet — if a group already exists, whichever player is already in it "
            "leads, so the existing group is added to rather than rebuilt."
        ),
        default=0,
        default_hint="Player 1 leads.",
    ),
    ParamField(
        key="require_all",
        type="bool",
        label="Require All Players",
        help=(
            "When enabled, the node fails if any player could not be added. When disabled, it "
            "succeeds as long as the group was formed, and only logs a warning about whoever "
            "was left out."
        ),
        default=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Puts every player into a single in-game group using the friends list. "
        "Group members receive health and mana wisps after combat and are guaranteed the "
        "first turn in battle. Players already grouped together are left alone."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players share one group (or were already grouped)."),
        ReturnSpec(
            "FAILURE",
            "One or more players could not be added — not online, not on the leader's "
            "friends list, wizard name could not be detected, or the invitation was never "
            "accepted. With Require All Players disabled, only a total failure to form any "
            "group returns FAILURE.",
        ),
    ],
    max_consecutive_retries=3,
    usage_notes=(
        "Every account must be on every other account's friends list in-game — the invitation "
        "is sent by opening the target player's profile from the leader's friends list.\n\n"
        "Grouping works across zones, so players do not need to be in the same place.\n\n"
        "You normally do not need this node: enable 'Automatically group players' in Settings "
        "to group at the start of every run and after any crash. Use the node when you want "
        "grouping at a specific point in the bot instead."
    ),
    safety_warning=(
        "This bot forms an in-game group between your players. Each account must appear on the "
        "other accounts' friends lists, or the players cannot be found to invite."
    ),
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: friends list and social window UI, via node_helpers and group_manager

    Flow:
      1. Return SUCCESS immediately when fewer than two clients are connected — a group
         of one confers no benefit and there is nobody to invite.
      2. Resolve every client's wizard name (cached; detected via the character page when
         unknown).
      3. Delegate to group_manager.form_group, which skips clients already in a group,
         sends an invitation to each remaining client, and confirms the join by checking
         that the Leave Group button became enabled on that client.
      4. Map the result onto SUCCESS / FAILURE according to require_all.

    FAILURE: a player could not be added and require_all is set; or no group was formed
    at all.
    """
    leader_index: int = params.get("leader_index", 0)
    require_all: bool = params.get("require_all", True)

    if len(clients) < 2:
        machine.log("form_group: fewer than two clients connected — nothing to group")
        return "SUCCESS"

    if not 0 <= leader_index < len(clients):
        machine.log(
            f"FAILURE: leader_index {leader_index} is out of range for {len(clients)} client(s)",
            logging.WARNING,
        )
        return "FAILURE"

    names = await wizard_names.resolve_names(clients)
    machine.log(f"form_group: wizard names = {names}", logging.DEBUG)

    result = await group_manager.form_group(
        clients,
        names,
        leader_index=leader_index,
        log=machine.log,
    )

    if result.success:
        machine.log(f"SUCCESS: {result.summary()}")
        return "SUCCESS"

    if not require_all and result.added:
        # A partial group still delivers the first-turn benefit to everyone in it, so with
        # require_all off this is a warning rather than a failed run.
        machine.log(f"SUCCESS (partial): {result.summary()}", logging.WARNING)
        return "SUCCESS"

    machine.log(f"FAILURE: {result.summary()}", logging.WARNING)
    return "FAILURE"
